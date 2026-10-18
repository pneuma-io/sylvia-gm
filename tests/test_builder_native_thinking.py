from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from sylvia_gm.agent._builder_session import (
    _builder_native_request,
    _coalesce_builder_responses,
    _provider_history,
    _run_cancellable_stream,
    _streamed_builder_messages,
)
from sylvia_gm.agent._mode import AgentMode
from sylvia_gm.agent._native_continuation import requires_reasoning_with_tools
from sylvia_gm.agent._runner import AgentRunner, StreamEvent
from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.provider._base import (
    ModelInfo,
    Provider,
    ProviderResponse,
    ProviderStreamChunk,
)


class NativeM3BuilderProvider(Provider):
    def __init__(self) -> None:
        super().__init__()
        self.model_info = ModelInfo(
            id="opencode-go/minimax-m3",
            name="MiniMax M3",
            provider_name="OpenCode Go",
            provider_key="opencode_go",
            provider_mode="byok",
            transport="anthropic_messages",
            provider_model_id="minimax-m3",
        )
        self.calls: list[tuple[list[dict[str, Any]], dict[str, object]]] = []
        self.native_blocks = [
            {
                "type": "thinking",
                "thinking": "PRIVATE SYNTHETIC BUILDER REASONING",
                "signature": "opaque-signature",
            },
            {
                "type": "tool_use",
                "id": "call-write-draft",
                "name": "write_file",
                "input": {"path": "notes.md", "content": "# Notes\n"},
            },
        ]

    async def generate(self, *args: object, **kwargs: object) -> ProviderResponse:
        raise AssertionError("Builder must stream")

    async def close(self) -> None:
        return None

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        del tools, timeout
        self.calls.append((messages.copy(), dict(kwargs)))
        if len(self.calls) == 1:
            yield ProviderStreamChunk(reasoning_content="PRIVATE SYNTHETIC BUILDER REASONING")
            yield ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "call-write-draft",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps({"path": "notes.md", "content": "# Notes\n"}),
                    },
                }
            )
            yield ProviderStreamChunk(
                finish_reason="tool_calls",
                anthropic_content=self.native_blocks,
            )
            return
        yield ProviderStreamChunk(reasoning_content="PRIVATE SYNTHETIC FINAL REASONING")
        yield ProviderStreamChunk(
            content="The notes are ready.",
            finish_reason="stop",
            anthropic_content=[
                {
                    "type": "thinking",
                    "thinking": "PRIVATE SYNTHETIC FINAL REASONING",
                    "signature": "opaque-final-signature",
                },
                {"type": "text", "text": "The notes are ready."},
            ],
        )


def test_saved_builder_prose_and_tools_restore_one_native_response() -> None:
    requirement = {"provider_id": "test", "model_id": "test", "anthropic_required": True}
    history = [
        {"role": "assistant", "content": "Writing notes.", "provider_response_id": "a"},
        {"role": "assistant", "content": None, "provider_response_id": "a",
         "native_continuation": requirement, "tool_calls": [{"id": "one"}]},
        {"role": "tool", "tool_call_id": "one", "content": "saved"},
        {"role": "user", "content": "Continue"},
        {"role": "assistant", "content": "Next response.", "provider_response_id": "b"},
    ]
    original = json.dumps(history)
    grouped = _coalesce_builder_responses(history)
    assert len(grouped) == 4
    assert grouped[0]["content"] == "Writing notes."
    assert grouped[0]["tool_calls"] == [{"id": "one"}]
    assert grouped[0]["native_continuation"] == requirement
    native = [{"type": "text", "text": "Writing notes."},
              {"type": "tool_use", "id": "one", "name": "write_file", "input": {}}]
    prepared = _builder_native_request(
        grouped, transport="anthropic_messages",
        read=lambda response: {"anthropic_content": native} if response == "a" else None,
        provider_identity=("test", "test"),
    )
    assert prepared[0]["anthropic_content"] == native
    assert [message["role"] for message in prepared] == ["assistant", "tool", "user", "assistant"]
    assert json.dumps(history) == original


def test_saved_builder_parallel_calls_keep_response_boundaries() -> None:
    history = []
    for response, call in (("a", "one"), ("a", "two"), ("b", "three")):
        history.extend([
            {"role": "assistant", "content": None, "provider_response_id": response,
             "tool_calls": [{"id": call}]},
            {"role": "tool", "tool_call_id": call, "content": "saved result"},
        ])
    grouped = _coalesce_builder_responses(history)
    assert [message["role"] for message in grouped] == [
        "assistant", "tool", "tool", "assistant", "tool",
    ]
    assert grouped[0]["tool_calls"] == [{"id": "one"}, {"id": "two"}]
    assert len(history[0]["tool_calls"]) == 1


@pytest.mark.asyncio
async def test_deepseek_builder_immediate_tool_continuation_keeps_native_reasoning(
    tmp_path: Path,
) -> None:
    class DeepSeekProvider(NativeM3BuilderProvider):
        async def generate_stream(self, *args, **kwargs):
            async for chunk in super().generate_stream(*args, **kwargs):
                yield replace(chunk, anthropic_content=None)

    provider = DeepSeekProvider()
    provider.model_info = ModelInfo(
        id="synthetic/deepseek-v4", name="Synthetic DeepSeek", provider_name="synthetic",
        provider_key="synthetic", provider_mode="byok",
        provider_model_id="deepseek-v4", transport="chat_completions",
    )
    assert requires_reasoning_with_tools(provider.model_info)
    runner = AgentRunner(
        mode=AgentMode.BUILDER_FREEFORM, provider=provider,
        history_path=tmp_path / "ordinary.json", project_root=tmp_path,
    )
    await runner.turn_stream("Create notes", asyncio.Queue())
    assistant = next(message for message in provider.calls[1][0] if message.get("tool_calls"))
    assert assistant["reasoning_content"] == "PRIVATE SYNTHETIC BUILDER REASONING"
    assert "reasoning_content" not in json.dumps(runner.history_snapshot())


@pytest.mark.asyncio
async def test_native_m3_builder_preserves_signed_tool_continuation(tmp_path: Path) -> None:
    provider = NativeM3BuilderProvider()
    runner = AgentRunner(
        mode=AgentMode.BUILDER_FREEFORM,
        provider=provider,
        history_path=tmp_path / "ordinary-builder-history.json",
        project_root=tmp_path,
    )
    event_queue: asyncio.Queue[StreamEvent] = asyncio.Queue()
    await runner.turn_stream("Create a notes file.", event_queue)

    events = [event_queue.get_nowait() for _ in range(event_queue.qsize())]
    native_events = [event for event in events if event.type == "continuation"]
    assert len(native_events) == 2
    assert json.loads(native_events[0].data) == {
        "anthropic_content": provider.native_blocks,
        "reasoning_content": "PRIVATE SYNTHETIC BUILDER REASONING",
    }
    assert json.loads(native_events[1].data)["anthropic_content"][0]["signature"] == (
        "opaque-final-signature"
    )
    assert len({event.meta["response_id"] for event in native_events if event.meta}) == 2
    assert all("PRIVATE SYNTHETIC" not in repr(event) for event in events)
    reasoning_ids = {event.meta["response_id"] for event in events
                     if event.type == "reasoning" and event.meta}
    assert reasoning_ids == {event.meta["response_id"] for event in native_events if event.meta}

    assert [options for _messages, options in provider.calls] == [
        {"max_tokens": 4096, "thinking": {"type": "adaptive"}},
        {"max_tokens": 4096, "thinking": {"type": "adaptive"}},
    ]
    continuation = provider.calls[1][0]
    assistant = next(message for message in continuation if message.get("role") == "assistant")
    assert assistant["anthropic_content"] == provider.native_blocks
    tool_result = continuation[continuation.index(assistant) + 1]
    assert tool_result["role"] == "tool"
    assert tool_result["tool_call_id"] == "call-write-draft"

    durable = runner.history_snapshot()
    assert all("anthropic_content" not in message for message in durable)
    serialized_durable = json.dumps(durable)
    assert "PRIVATE SYNTHETIC BUILDER REASONING" not in serialized_durable
    assert "PRIVATE SYNTHETIC FINAL REASONING" not in serialized_durable
    assert "opaque-signature" not in serialized_durable
    assert "opaque-final-signature" not in serialized_durable

    ordinary_save = (tmp_path / "ordinary-builder-history.json").read_text(encoding="utf-8")
    assert "PRIVATE SYNTHETIC" not in ordinary_save
    assert "opaque-signature" not in ordinary_save

    canonical = _streamed_builder_messages("message-pes47", durable)
    serialized_canonical = json.dumps([message.content for message in canonical])
    saved_tool = next(message for message in canonical
                      if message.content.get("kind") == "builder_tool_result")
    assert saved_tool.content["provider_response_id"] == native_events[0].meta["response_id"]
    assert "PRIVATE SYNTHETIC" not in serialized_canonical
    assert "opaque-signature" not in serialized_canonical
    assert [
        message.content.get("text") for message in canonical if message.content.get("text")
    ] == ["The notes are ready."]


def test_builder_native_stream_bridge_persists_both_responses_privately(tmp_path: Path) -> None:
    """Exercise actual runner, event bridge and owner storage, then reopen the owner."""
    provider = NativeM3BuilderProvider()
    runner = AgentRunner(
        mode=AgentMode.BUILDER_FREEFORM, provider=provider,
        history_path=tmp_path / "ordinary.json", project_root=tmp_path,
    )
    database = tmp_path / "authority.sqlite3"
    with AuthorityOwner.open(database, "native-test") as owner:
        owner.create_builder_chat(
            provider_id="opencode_go", model_id="minimax-m3", session_id="builder",
        )
        CampaignSessionLoop(owner).run_builder(
            SessionRunRequest("builder", "turn", "attempt", "input", "Create notes"),
            lambda request: _run_cancellable_stream(
                runner, "Create notes", request.control,
                message_id="input", history_cursor=[0],
            ),
            lambda: None,
        )
    with AuthorityOwner.open(database, "native-test") as owner:
        index = owner.private_provider_index("builder")
        assert len(index) == 2
        native = [owner.provider_continuation(
            "builder", item["response_id"], provider_id="opencode_go", model_id="minimax-m3",
        ) for item in index]
        assert native[0]["anthropic_content"] == provider.native_blocks
        assert native[1]["anthropic_content"][0]["signature"] == "opaque-final-signature"
        assert all(item["status"] == "completed" for item in index)
        history = repr(owner.session_context("builder"))
        assert "PRIVATE SYNTHETIC" not in history
        assert "opaque-signature" not in history
        assert "opaque-final-signature" not in history
        reconstructed = _provider_history(
            owner.session_context("builder"), exclude_message_id="next",
        )
        final_response = next(message for message in reconstructed
                              if message.get("content") == "The notes are ready.")
        assert final_response["provider_response_id"] == index[1]["response_id"]
        assert final_response["native_continuation"]["reasoning_required"] is True
        for transport, reasoning in (("anthropic_messages", False), ("chat_completions", True)):
            with pytest.raises(CommandRejected) as missing:
                _builder_native_request(
                    reconstructed, transport=transport, read=lambda _: None,
                    resend_reasoning=reasoning, provider_identity=("opencode_go", "minimax-m3"),
                )
            assert missing.value.code == "provider_native_continuation_missing"
            # A different provider/model does not inherit the old provider's requirement.
            _builder_native_request(
                reconstructed, transport=transport, read=lambda _: None,
                resend_reasoning=reasoning, provider_identity=("different", "different"),
            )
        reads = []
        def read(response_id):
            reads.append(response_id)
            return owner.provider_continuation(
                "builder", response_id, provider_id="opencode_go", model_id="minimax-m3",
            )
        prepared = _builder_native_request(
            reconstructed, transport="anthropic_messages", read=read,
        )
        assistant = next(message for message in prepared if message.get("tool_calls"))
        assert assistant["anthropic_content"] == provider.native_blocks
        assert reads == [index[0]["response_id"]]
        assert all("provider_response_id" not in message for message in prepared)
        assert "opaque-signature" not in json.dumps(reconstructed)
        reads.clear()
        _builder_native_request(reconstructed, transport="responses", read=read)
        assert reads == []
        deepseek = _builder_native_request(
            reconstructed, transport="chat_completions", read=read, resend_reasoning=True,
        )
        assert [message["reasoning_content"] for message in deepseek
                if "reasoning_content" in message] == [
            "PRIVATE SYNTHETIC BUILDER REASONING", "PRIVATE SYNTHETIC FINAL REASONING",
        ]
        with pytest.raises(CommandRejected) as rejected:
            _builder_native_request(reconstructed, transport="anthropic_messages",
                                    read=lambda _: {"anthropic_content": []})
        assert rejected.value.code == "provider_native_continuation_mismatch"
