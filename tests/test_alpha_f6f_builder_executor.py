"""Active Alpha probes for the canonical full-tool Builder executor seam."""

from __future__ import annotations

import asyncio
import json
import threading
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from jsonschema import Draft202012Validator

from sylvia_gm.agent._builder_session import (
    _run_cancellable_stream,
    _run_cancellable_turn,
    _streamed_builder_messages,
)
from sylvia_gm.agent._mode import AgentMode
from sylvia_gm.agent._provider_session import _PROVIDER_ASYNC_RUNTIME
from sylvia_gm.agent._runner import AgentRunner
from sylvia_gm.agent._session_loop import ProviderControl, ProviderEventKind
from sylvia_gm.builder import _tools as builder_tools
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.provider._anthropic import AnthropicMessagesTransport, AnthropicProvider
from sylvia_gm.provider._base import FakeProvider, ModelInfo, ProviderResponse, ToolCall
from sylvia_gm.provider._credentials import Credential, CredentialPool
from sylvia_gm.provider._openai_compat import ChatCompletionsTransport
from sylvia_gm.tools._builder_handlers import tool_web_search


@pytest.mark.parametrize("streaming", [True, False])
def test_cached_anthropic_provider_survives_builder_to_gm_event_loop_change(
    tmp_path: Path, streaming: bool,
) -> None:
    """A cached real SDK client must not retain Builder's closed event loop."""

    responses = iter(("Builder response.", "GM response.", "Builder resumed."))
    requests: list[str] = []
    connections: set[tuple[str, int]] = set()

    class AnthropicSSEHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_POST(self) -> None:  # noqa: N802 - stdlib HTTP handler contract
            body_length = int(self.headers.get("Content-Length", "0"))
            request = json.loads(self.rfile.read(body_length))
            requests.append(self.path)
            connections.add(self.client_address)
            response_text = next(responses)
            body = (
                'event: message_start\n'
                'data: {"type":"message_start","message":{"id":"msg_synthetic",'
                '"type":"message","role":"assistant","model":"minimax-m3",'
                '"content":[],"stop_reason":null,"stop_sequence":null,'
                '"usage":{"input_tokens":1,"output_tokens":0}}}\n\n'
                'event: content_block_start\n'
                'data: {"type":"content_block_start","index":0,"content_block":'
                '{"type":"text","text":""}}\n\n'
                'event: content_block_delta\n'
                f'data: {{"type":"content_block_delta","index":0,"delta":'
                f'{{"type":"text_delta","text":{json.dumps(response_text)}}}}}\n\n'
                'event: content_block_stop\n'
                'data: {"type":"content_block_stop","index":0}\n\n'
                'event: message_delta\n'
                'data: {"type":"message_delta","delta":{"stop_reason":"end_turn",'
                '"stop_sequence":null},"usage":{"output_tokens":1}}\n\n'
                'event: message_stop\n'
                'data: {"type":"message_stop"}\n\n'
            ).encode()
            if not request.get("stream"):
                body = json.dumps({
                    "id": "msg_synthetic", "type": "message", "role": "assistant",
                    "model": "minimax-m3", "stop_reason": "end_turn", "stop_sequence": None,
                    "content": [{"type": "text", "text": response_text}],
                    "usage": {"input_tokens": 1, "output_tokens": 1},
                }).encode()
            self.send_response(200)
            self.send_header(
                "Content-Type", "text/event-stream" if request.get("stream") else "application/json"
            )
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "keep-alive")
            self.end_headers()
            self.wfile.write(body)
            self.wfile.flush()
            self.connection.settimeout(1)

        def log_message(self, _format: str, *_args: object) -> None:
            return None

    server = ThreadingHTTPServer(("127.0.0.1", 0), AnthropicSSEHandler)
    server.daemon_threads = True
    server_thread = threading.Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    provider = AnthropicProvider(
        base_url=f"http://127.0.0.1:{server.server_port}/v1",
        credential_pool=CredentialPool([Credential(type="api_key", value="synthetic")]),
        model_info=ModelInfo(
            id="synthetic/minimax-m3",
            name="Synthetic MiniMax M3",
            provider_name="Synthetic Anthropic endpoint",
            provider_key="synthetic",
            provider_mode="byok",
            context_window=8192,
            transport="anthropic_messages",
            provider_model_id="minimax-m3",
        ),
    )
    runner = AgentRunner(
        mode=AgentMode.BUILDER_FREEFORM,
        provider=provider,
        history_path=None,
        project_root=tmp_path,
        ruleset_name="none",
        system_prompt_override="Return one short synthetic response.",
        prompt_mode="replace",
    )
    gm_loop = _PROVIDER_ASYNC_RUNTIME.loop()

    async def collect_gm_response() -> list[str]:
        return [
            chunk.content
            async for chunk in provider.generate_stream(
                [{"role": "user", "content": "Synthetic campaign opening."}],
                max_tokens=32,
            )
            if chunk.content
        ]

    def builder_response(text: str) -> str:
        if not streaming:
            return _run_cancellable_turn(runner, text, ProviderControl()).dm_response
        builder_events = list(
            _run_cancellable_stream(
                runner,
                text,
                ProviderControl(),
                message_id="builder-message",
                history_cursor=[0],
            )
        )
        return "".join(
            event.text for event in builder_events if event.kind is ProviderEventKind.TALK
        )

    try:
        assert builder_response("Synthetic Builder turn.") == "Builder response."
        gm_future = asyncio.run_coroutine_threadsafe(collect_gm_response(), gm_loop)
        assert gm_future.result(timeout=3) == ["GM response."]
        assert builder_response("Return to the saved Builder.") == "Builder resumed."
        assert requests == ["/v1/messages"] * 3
        assert len(connections) == 1
    finally:
        close_future = asyncio.run_coroutine_threadsafe(provider.close(), gm_loop)
        with suppress(Exception):
            close_future.result(timeout=3)
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=3)


@pytest.mark.asyncio
@pytest.mark.parametrize("wire_format", ["anthropic_messages", "chat_completions"])
async def test_builder_executes_native_stream_tool_calls(tmp_path: Path, wire_format: str) -> None:
    """Native stream names and split arguments must reach the real Builder registry."""
    (tmp_path / "reference.txt").write_text("Public character reference", encoding="utf-8")
    transport = AnthropicMessagesTransport()
    events = [
        SimpleNamespace(
            type="content_block_start", index=1,
            content_block=SimpleNamespace(
                type="tool_use", id="read-reference", name="read_file", input={},
            ),
        ),
        SimpleNamespace(
            type="content_block_delta", index=1,
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"path":"ref'),
        ),
        SimpleNamespace(
            type="content_block_delta", index=1,
            delta=SimpleNamespace(type="input_json_delta", partial_json='erence.txt"}'),
        ),
    ]
    chunks = [chunk for event in events if (chunk := transport.parse_stream_chunk(event))]
    if wire_format == "chat_completions":
        deltas = [
            SimpleNamespace(
                index=1, id="read-reference",
                function=SimpleNamespace(name="read_file", arguments=""),
            ),
            SimpleNamespace(index=1, function=SimpleNamespace(arguments='{"path":"ref')),
            SimpleNamespace(index=1, function=SimpleNamespace(arguments='erence.txt"}')),
        ]
        chunks = [
            chunk for delta in deltas
            if (chunk := ChatCompletionsTransport().parse_stream_chunk(SimpleNamespace(
                choices=[SimpleNamespace(
                    delta=SimpleNamespace(tool_calls=[delta]), finish_reason=None,
                )],
            )))
        ]

    class OneToolResponseProvider(FakeProvider):
        async def generate_stream(self, **kwargs):
            async for chunk in super().generate_stream(**kwargs):
                yield chunk
            self._stream_chunks = []

    runner = AgentRunner(
        mode=AgentMode.BUILDER_FREEFORM,
        provider=OneToolResponseProvider(
            stream_chunks=chunks, responses=[ProviderResponse(content="Reference read.")],
        ),
        history_path=None, project_root=tmp_path, ruleset_name="none",
        system_prompt_override="Read the selected character reference.", prompt_mode="replace",
    )
    await runner.turn_stream("Read reference.txt.", asyncio.Queue())
    history = runner.history_snapshot()
    calls = [call for message in history for call in message.get("tool_calls", [])]
    assert calls == [{
        "id": "read-reference", "type": "function",
        "function": {"name": "read_file", "arguments": '{"path":"reference.txt"}'},
    }]
    results = [json.loads(message["content"]) for message in history if message["role"] == "tool"]
    assert len(results) == 1
    assert results[0]["success"] is True
    assert "Public character reference" in results[0]["content"]


@pytest.mark.asyncio
async def test_application_builder_start_has_a_provider_turn_without_player_speech(tmp_path):
    """The installed New Companion launch must form a valid Messages request."""
    provider = FakeProvider(responses=[ProviderResponse(content="What is your Companion's name?")])
    runner = AgentRunner(
        mode=AgentMode.BUILDER_FREEFORM,
        provider=provider,
        history_path=None,
        project_root=tmp_path,
        ruleset_name="none",
        system_prompt_override="Use the captured New Companion context.",
        prompt_mode="replace",
    )
    await runner.turn_stream(
        "", asyncio.Queue(),
        application_prompt="Read the selected campaign references and begin Companion creation.",
    )
    request = AnthropicMessagesTransport().format_request(
        "minimax-m3", provider.calls[0]["messages"], None, True,
    )
    assert request["messages"], "Messages transport cannot start from an empty conversation"
    assert request["messages"][-1]["role"] == "user"
    assert "application-owned start signal, not player speech" in request["messages"][-1]["content"]
    persisted = _streamed_builder_messages("start-companion", runner.history_snapshot())
    assert [message.kind for message in persisted] == [SessionMessageKind.TALK]
    assert persisted[0].content["text"] == "What is your Companion's name?"


@pytest.mark.asyncio
async def test_builder_runner_accepts_canonical_history_without_jsonl(tmp_path: Path) -> None:
    """Canonical SQLite history can seed the reused runner without a history file."""

    provider = FakeProvider(responses=[ProviderResponse(content="Builder is still here.")])
    runner = AgentRunner(
        mode=AgentMode.BUILDER_FREEFORM,
        provider=provider,
        history_path=None,
        initial_history=[
            {"role": "user", "content": "Research the setting."},
            {"role": "assistant", "content": "I started the research."},
        ],
        project_root=tmp_path,
        ruleset_name="none",
        system_prompt_override=(
            "You are Sylvia GM's full Builder agent. Use ordinary Builder tools as needed."
        ),
        prompt_mode="replace",
    )

    result = await runner.turn("Continue after the DM starts.")

    assert result.dm_response == "Builder is still here."
    assert provider.calls
    sent = provider.calls[0]["messages"]
    assert any(item.get("content") == "Research the setting." for item in sent)
    assert any(item.get("content") == "Continue after the DM starts." for item in sent)
    assert runner.history_snapshot()[-1] == {
        "role": "assistant",
        "content": "Builder is still here.",
    }
    assert list(tmp_path.glob("*.jsonl")) == []


def test_canonical_builder_excludes_legacy_campaign_creation_bypass(
    tmp_path: Path,
) -> None:
    runner = AgentRunner(
        mode=AgentMode.BUILDER_FREEFORM,
        provider=FakeProvider(responses=[]),
        history_path=None,
        project_root=tmp_path,
        ruleset_name="none",
        system_prompt_override="Canonical persistent Builder",
        prompt_mode="replace",
    )
    definitions = runner._build_tools() or []  # noqa: SLF001 - verify production registry
    names = {definition["function"]["name"] for definition in definitions}

    assert "ask_question" in names
    assert "create_campaign_folder" not in names
    for definition in definitions:
        Draft202012Validator.check_schema(definition["function"]["parameters"])


def test_builder_can_read_but_cannot_mutate_owner_managed_source_paths(tmp_path: Path) -> None:
    published = tmp_path / ".builder-sources" / "batch-test" / "source-test.txt"
    published.parent.mkdir(parents=True)
    published.write_text("PUBLISHED SOURCE", encoding="utf-8")
    pending = tmp_path / ".builder-sources-purge-pending" / "private.txt"
    pending.parent.mkdir()
    pending.write_text("PENDING SOURCE MUST STAY PRIVATE", encoding="utf-8")
    runner = AgentRunner(
        mode=AgentMode.BUILDER_FREEFORM,
        provider=FakeProvider(responses=[]),
        history_path=None,
        project_root=tmp_path,
        ruleset_name="none",
        system_prompt_override="Canonical persistent Builder",
        prompt_mode="replace",
    )

    assert runner._tool_read_file(  # noqa: SLF001 - verify the real guarded executor
        {"path": ".builder-sources/batch-test/source-test.txt"}
    )["success"]
    for tool in (  # noqa: SLF001
        runner._tool_write_file, runner._tool_edit_file, runner._tool_delete_file
    ):
        params = {"path": ".builder-sources/batch-test/source-test.txt"}
        if tool == runner._tool_write_file:  # noqa: SLF001
            params["content"] = "MUTATED"
        elif tool == runner._tool_edit_file:  # noqa: SLF001
            params.update(
                {"old_text": "PUBLISHED", "new_text": "MUTATED", "expected_version": "unused"}
            )
        result = tool(params)
        assert result["success"] is False
        assert "read-only" in result["error"]
    assert published.read_text(encoding="utf-8") == "PUBLISHED SOURCE"
    assert (
        runner._tool_read_file(  # noqa: SLF001 - owner-internal path is never readable
            {"path": ".builder-sources-purge-pending/private.txt"}
        )["success"]
        is False
    )
    search = runner._tool_search_files(  # noqa: SLF001 - root search hides purge quarantine
        {"pattern": "PENDING SOURCE", "target": "content"}
    )
    assert search == {"success": True, "matches": [], "count": 0}


def test_builder_web_search_is_separate_and_parses_bounded_results(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Builder gets ordinary web search without exposing it through DM mechanics."""

    ordinary_names = {
        definition["function"]["name"] for definition in builder_tools.BUILDER_TOOL_DEFINITIONS
    }
    assert "web_search" not in ordinary_names
    assert builder_tools.PUBLIC_WEB_TOOL_DEFINITION["function"]["name"] == "web_search"

    html = """
    <html><body>
      <div class="result">
        <a class="result__a" href="https://example.com/source">Official Source</a>
        <a class="result__snippet">Useful source description.</a>
      </div>
      <div class="result">
        <a class="result__a" href="https://example.org/second">Second Source</a>
        <a class="result__snippet">Second description.</a>
      </div>
    </body></html>
    """

    class FakeResponse:
        text = html

        def raise_for_status(self) -> None:
            return None

    def fake_get(*_args: object, **_kwargs: object) -> FakeResponse:
        return FakeResponse()

    monkeypatch.setattr(httpx, "get", fake_get)
    result = tool_web_search({"query": "official rules source", "limit": 1}, None)

    assert result == {
        "success": True,
        "query": "official rules source",
        "results": [
            {
                "title": "Official Source",
                "url": "https://example.com/source",
                "description": "Useful source description.",
            }
        ],
    }


@pytest.mark.asyncio
async def test_builder_executes_web_search_through_ordinary_tool_registry(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Builder tool calls execute locally while DM mechanics stay on MCP."""

    class FakeResponse:
        text = (
            '<a class="result__a" href="https://example.com/source">Official Source</a>'
            '<a class="result__snippet">Useful source description.</a>'
        )

        def raise_for_status(self) -> None:
            return None

    monkeypatch.setattr(httpx, "get", lambda *_args, **_kwargs: FakeResponse())
    provider = FakeProvider(
        responses=[
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="search-1",
                        name="web_search",
                        arguments='{"query":"official rules source","limit":1}',
                    )
                ]
            ),
            ProviderResponse(content="I found the official source."),
        ]
    )
    runner = AgentRunner(
        mode=AgentMode.BUILDER_FREEFORM,
        provider=provider,
        history_path=None,
        project_root=tmp_path,
        ruleset_name="none",
        system_prompt_override="You are Sylvia GM's full Builder agent.",
        prompt_mode="replace",
        allow_public_web=True,
    )

    result = await runner.turn("Find the official rules source.")

    assert result.dm_response == "I found the official source."
    assert result.tool_calls_made == ["web_search"]
    tool_messages = [message for message in result.messages if message.get("role") == "tool"]
    assert len(tool_messages) == 1
    assert "Official Source" in tool_messages[0]["content"]


def test_builder_omits_public_web_without_explicit_turn_permission(tmp_path: Path) -> None:
    denied = AgentRunner(
        mode=AgentMode.BUILDER_FREEFORM,
        provider=FakeProvider(responses=[]),
        history_path=None,
        project_root=tmp_path,
        ruleset_name="none",
        system_prompt_override="Canonical persistent Builder",
        prompt_mode="replace",
    )
    allowed = AgentRunner(
        mode=AgentMode.BUILDER_FREEFORM,
        provider=FakeProvider(responses=[]),
        history_path=None,
        project_root=tmp_path,
        ruleset_name="none",
        system_prompt_override="Canonical persistent Builder",
        prompt_mode="replace",
        allow_public_web=True,
    )

    denied_names = {
        definition["function"]["name"]
        for definition in denied._build_tools() or []  # noqa: SLF001
    }
    allowed_names = {
        definition["function"]["name"]
        for definition in allowed._build_tools() or []  # noqa: SLF001
    }
    assert "web_search" not in denied_names
    assert "web_search" in allowed_names
