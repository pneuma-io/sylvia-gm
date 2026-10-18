"""Official-spec regression fixtures, checked 2026-10-16; all wire data is synthetic.

No credentials, subscriptions, provider inference, server installation or GPU is used.
Source links and scope are recorded in docs/research/provider-spec-conformance-2026-10-16.md.
"""

import json
from types import SimpleNamespace

import httpx
import pytest

from sylvia_gm.agent._native_continuation import attach_chat_metadata
from sylvia_gm.config import provider_settings as settings
from sylvia_gm.provider._base import ModelInfo, ProviderConfig, RetryConfig
from sylvia_gm.provider._exceptions import (
    IncompleteProviderStreamError,
    InvalidResponseError,
    ProviderUnavailableError,
)
from sylvia_gm.provider._factory import ProviderFactory
from sylvia_gm.provider._openai_compat import (
    OpenAICompatProvider,
    ResponsesTransport,
    _stream_usage,
)
from tests.test_provider_transport_matrix import event_stream


@pytest.mark.asyncio
@pytest.mark.parametrize("base_style", ["current", "historical"])
@pytest.mark.parametrize("preset", ["opencode_go", "opencode_zen"])
@pytest.mark.parametrize(
    "protocol, suffix",
    [
        ("chat_completions", "/openai/v1/chat/completions"),
        ("responses", "/openai/v1/responses"),
        ("anthropic_messages", "/anthropic/v1/messages"),
        ("gemini_generate_content", "/google/v1beta/models/fixture:streamGenerateContent"),
    ],
)
async def test_opencode_factory_uses_documented_inference_paths_and_bearer(
    preset, protocol, suffix, base_style
):
    prefix = "/inference/go" if preset == "opencode_go" else "/inference"
    requests = []

    def handle(request):
        requests.append(request)
        assert request.url.path == prefix + suffix
        assert request.headers["authorization"] == "Bearer synthetic-key"
        assert "x-api-key" not in request.headers
        assert "x-goog-api-key" not in request.headers
        assert request.headers["content-type"].startswith("application/json")
        wire = (
            event_stream(protocol)
            if protocol != "gemini_generate_content"
            else (
                'data: {"candidates":[{"content":{"parts":[{"text":"ready"}]},'
                '"finishReason":"STOP"}]}\n\n'
            )
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=wire)

    definition = settings._PROVIDER_PRESETS[preset]
    model = ModelInfo(
        id="fixture",
        name="Fixture",
        provider_name=preset,
        provider_key=preset,
        provider_mode="byok",
        transport=protocol,
    )
    raw = ProviderFactory.create(
        ProviderConfig(
            provider_key=preset,
            transport=protocol,
            base_url=(
                "https://opencode.ai/zen/go/v1"
                if preset == "opencode_go"
                else "https://opencode.ai/zen/v1"
            )
            if base_style == "historical"
            else definition["base_url"],
            models=[model],
            credentials_list=[{"type": "api_key", "value": "synthetic-key"}],
            retry_config=RetryConfig(max_retries=0),
        )
    )
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
    if protocol == "gemini_generate_content":
        await raw._client.aclose()
        raw._client = client
    else:
        await raw._client.close()
        if protocol == "anthropic_messages":
            from anthropic import AsyncAnthropic

            raw._client = AsyncAnthropic(
                base_url=raw._base_url.removesuffix("/v1"),
                api_key="",
                http_client=client,
                max_retries=0,
            )
        else:
            raw._client = raw._build_client(client)
    try:
        chunks = [c async for c in raw.generate_stream([{"role": "user", "content": "Fixture"}])]
        assert "".join(c.content or "" for c in chunks) == "ready"
        assert len(requests) == 1
    finally:
        await raw.close()


@pytest.mark.parametrize("preset", ["opencode_go", "opencode_zen"])
def test_opencode_gemini_model_uses_existing_native_transport(preset):
    assert (
        settings._model_transport_for_preset(preset, "gemini-fixture") == "gemini_generate_content"
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "failure, expected",
    [
        ("failed", ProviderUnavailableError),
        ("error", ProviderUnavailableError),
        ("filtered", InvalidResponseError),
        ("truncated", IncompleteProviderStreamError),
    ],
)
async def test_responses_failed_filtered_error_or_truncated_stream_is_not_success(
    failure, expected
):
    event = {
        "failed": {
            "type": "response.failed",
            "response": {
                "id": "r",
                "object": "response",
                "status": "failed",
                "output": [],
                "error": {"code": "server_error", "message": "PRIVATE_FIXTURE"},
            },
        },
        "error": {"type": "error", "code": "server_error", "message": "PRIVATE_FIXTURE"},
        "filtered": {
            "type": "response.incomplete",
            "response": {
                "id": "r",
                "object": "response",
                "status": "incomplete",
                "output": [],
                "incomplete_details": {"reason": "content_filter"},
            },
        },
        "truncated": {
            "type": "response.output_text.delta",
            "delta": "unfinished",
            "output_index": 0,
            "content_index": 0,
            "item_id": "m",
        },
    }[failure]
    wire = "event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n"
    provider = OpenAICompatProvider(
        base_url="https://fixture.invalid/v1",
        api_key="synthetic",
        model_info=ModelInfo(
            id="fixture",
            name="Fixture",
            provider_name="Fixture",
            provider_key="custom",
            provider_mode="byok",
            transport="responses",
        ),
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, text=wire
                )
            )
        ),
    )
    try:
        with pytest.raises(expected):
            _ = [
                c async for c in provider.generate_stream([{"role": "user", "content": "Fixture"}])
            ]
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_chat_finish_reason_without_documented_done_sentinel_is_truncated():
    wire = event_stream("chat_completions").replace("data: [DONE]\n\n", "")
    provider = OpenAICompatProvider(
        base_url="https://fixture.invalid/v1",
        api_key="synthetic",
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda r: httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, text=wire
                )
            )
        ),
    )
    try:
        with pytest.raises(IncompleteProviderStreamError):
            _ = [
                c async for c in provider.generate_stream([{"role": "user", "content": "Fixture"}])
            ]
    finally:
        await provider.close()


def test_responses_native_reasoning_and_function_output_replay_exactly():
    output = [
        {"type": "reasoning", "id": "rs", "summary": [], "encrypted_content": "synthetic-opaque"},
        {
            "type": "function_call",
            "id": "fc",
            "call_id": "call",
            "name": "inspect",
            "arguments": "{}",
        },
    ]
    parsed = ResponsesTransport().parse_response(
        SimpleNamespace(
            output=[SimpleNamespace(**x) for x in output],
            output_text="",
            usage=None,
            status="completed",
        ),
        "fixture",
    )
    assert parsed.chat_completion_metadata is not None
    assistant = attach_chat_metadata(
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call",
                    "type": "function",
                    "function": {"name": "inspect", "arguments": "{}"},
                },
            ],
        },
        parsed.chat_completion_metadata,
    )
    wire = ResponsesTransport().format_request(
        "fixture",
        [assistant, {"role": "tool", "tool_call_id": "call", "content": '{"ok":true}'}],
        None,
        False,
    )
    assert wire["input"][:2] == output
    assert wire["input"][2] == {
        "type": "function_call_output",
        "call_id": "call",
        "output": '{"ok":true}',
    }


def test_responses_forced_tool_and_json_schema_use_responses_shape():
    schema = {
        "type": "object",
        "properties": {"answer": {"type": "string"}},
        "required": ["answer"],
    }
    wire = ResponsesTransport().format_request(
        "fixture",
        [],
        None,
        False,
        tool_choice={"type": "function", "function": {"name": "inspect"}},
        response_format={
            "type": "json_schema",
            "json_schema": {"name": "answer", "schema": schema, "strict": True},
        },
    )
    assert wire["tool_choice"] == {"type": "function", "name": "inspect"}
    assert "response_format" not in wire
    assert wire["text"]["format"] == {
        "type": "json_schema",
        "name": "answer",
        "schema": schema,
        "strict": True,
    }


def test_flat_reported_cache_usage_is_preserved():
    _, cache = _stream_usage(
        SimpleNamespace(prompt_tokens=12, completion_tokens=3, cached_tokens=9)
    )
    assert cache.cache_read_tokens == 9
    assert cache.cache_miss_tokens == 3


def test_server_retry_after_is_not_shortened_to_application_backoff_cap():
    from sylvia_gm.provider._failover import _build_delay

    assert _build_delay(1, RetryConfig(max_delay=30), retry_after=75) == 75


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize(
    "provider_key,field",
    [
        ("together", "reasoning"),
        ("together", "reasoning_content"),
        ("cerebras", "reasoning"),
        ("groq", "reasoning"),
    ],
)
async def test_reasoning_native_key_replay_is_provider_scoped(provider_key, field, streaming):
    definition = settings._PROVIDER_PRESETS[provider_key]
    delta = {"content": "Visible.", field: "Synthetic private reasoning"}

    def handle(request):
        if not streaming:
            return httpx.Response(
                200,
                json={
                    "id": "fixture",
                    "object": "chat.completion",
                    "created": 1,
                    "model": "fixture",
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", **delta},
                        }
                    ],
                },
            )
        event = {
            "id": "fixture",
            "object": "chat.completion.chunk",
            "created": 1,
            "model": "fixture",
            "choices": [{"index": 0, "finish_reason": "stop", "delta": delta}],
        }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n",
        )

    provider = OpenAICompatProvider(
        base_url=definition["base_url"],
        api_key="synthetic",
        model_info=ModelInfo(
            id="fixture",
            name="Fixture",
            provider_name=provider_key,
            provider_key=provider_key,
            provider_mode="byok",
        ),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    try:
        if streaming:
            chunks = [
                chunk
                async for chunk in provider.generate_stream(
                    [{"role": "user", "content": "Fixture"}]
                )
            ]
            metadata = next(
                (
                    chunk.chat_completion_metadata
                    for chunk in chunks
                    if chunk.chat_completion_metadata
                ),
                None,
            )
            reasoning = "".join(chunk.reasoning_content or "" for chunk in chunks)
        else:
            result = await provider.generate([{"role": "user", "content": "Fixture"}])
            metadata = result.chat_completion_metadata
            reasoning = result.reasoning_content
        assert reasoning == "Synthetic private reasoning"
        if provider_key == "groq":
            assert metadata is None
        else:
            assert metadata["reasoning_fields"] == {field: "Synthetic private reasoning"}
            replay = attach_chat_metadata({"role": "assistant", "content": "Visible."}, metadata)
            assert replay[field] == "Synthetic private reasoning"
            if field == "reasoning":
                assert "reasoning_content" not in replay
    finally:
        await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_mistral_thinking_blocks_remain_private_and_replay_in_full(streaming):
    blocks = [
        {"type": "thinking", "thinking": [{"type": "text", "text": "Synthetic private block"}]},
        {"type": "text", "text": "Visible."},
    ]

    def handle(request):
        common = {
            "id": "fixture",
            "model": "fixture",
            "created": 1,
            "object": "chat.completion.chunk" if streaming else "chat.completion",
        }
        if not streaming:
            return httpx.Response(
                200,
                json={
                    **common,
                    "choices": [
                        {
                            "index": 0,
                            "finish_reason": "stop",
                            "message": {"role": "assistant", "content": blocks},
                        }
                    ],
                },
            )
        event = {
            **common,
            "choices": [{"index": 0, "finish_reason": "stop", "delta": {"content": blocks}}],
        }
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n",
        )

    provider = OpenAICompatProvider(
        base_url="https://api.mistral.ai/v1",
        api_key="synthetic",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    try:
        if streaming:
            chunks = [
                chunk
                async for chunk in provider.generate_stream(
                    [{"role": "user", "content": "Fixture"}]
                )
            ]
            visible = "".join(chunk.content or "" for chunk in chunks)
            private = "".join(chunk.reasoning_content or "" for chunk in chunks)
            metadata = next(
                chunk.chat_completion_metadata for chunk in chunks if chunk.chat_completion_metadata
            )
        else:
            result = await provider.generate([{"role": "user", "content": "Fixture"}])
            visible = result.content
            private = result.reasoning_content
            metadata = result.chat_completion_metadata
        assert visible == "Visible." and private == "Synthetic private block"
        assert metadata == {"chat_content": blocks}
        assert (
            attach_chat_metadata({"role": "assistant", "content": visible}, metadata)["content"]
            == blocks
        )
    finally:
        await provider.close()


@pytest.mark.parametrize(
    "protocol,metadata",
    [
        (
            "responses",
            {
                "responses_output": [
                    {"type": "reasoning", "id": "rs", "summary": [], "encrypted_content": "sealed"},
                    {
                        "type": "function_call",
                        "call_id": "call",
                        "name": "inspect",
                        "arguments": "{}",
                    },
                ]
            },
        ),
        ("chat_completions", {"reasoning_fields": {"reasoning": "sealed"}}),
        (
            "chat_completions",
            {
                "chat_content": [
                    {"type": "thinking", "thinking": [{"type": "text", "text": "sealed"}]}
                ]
            },
        ),
    ],
)
def test_new_native_metadata_reopens_and_rejoins_both_consumers_without_history_leak(
    tmp_path, protocol, metadata
):
    import sqlite3
    from copy import deepcopy

    from sylvia_gm.agent._builder_session import _builder_native_request
    from sylvia_gm.agent._provider_session import ProviderSessionAdapter
    from sylvia_gm.domain.errors import CommandRejected
    from sylvia_gm.infrastructure import sqlite_reasoning
    from tests.test_alpha_f4_provider_session_adapter import _provider_input, _ScriptedProvider
    from tests.test_private_provider_records import open_fixture, record

    source = _provider_input()
    identity = (source.session.provider_id, source.session.model_id)
    path = tmp_path / "private.db"
    connection = open_fixture(path)
    native = {"chat_completion_metadata": metadata}
    for sid in ["gm", "builder"]:
        record(
            connection,
            session_id=sid,
            kind="continuation",
            provider_id=identity[0],
            model_id=identity[1],
            payload=json.dumps(native),
        )
    connection.commit()
    connection.close()
    canonical = {
        "role": "assistant",
        "content": None,
        "tool_calls": [
            {"id": "call", "type": "function", "function": {"name": "inspect", "arguments": "{}"}}
        ],
    }
    before = deepcopy(canonical)
    requirement = {
        "provider_id": identity[0],
        "model_id": identity[1],
        "chat_completion_required": True,
    }
    with sqlite3.connect(path) as reopened:

        def read(sid, provider=identity[0]):
            return sqlite_reasoning.read_continuation(
                reopened, sid, "response", provider_id=provider, model_id=identity[1]
            )

        assert read("gm", provider="other") is None
        provider = _ScriptedProvider([])
        provider.model_info = ModelInfo(
            id="fixture",
            name="Fixture",
            provider_name="Fixture",
            provider_key="fixture",
            provider_mode="byok",
            transport=protocol,
        )
        adapter = ProviderSessionAdapter(
            SimpleNamespace(provider_continuation=lambda *args, **kwargs: read("gm")),
            provider,
            owner_id="test",
        )
        gm = {
            **canonical,
            "_native_reference": {"response_id": "response", "requirement": requirement},
        }
        builder = {
            **canonical,
            "provider_response_id": "response",
            "native_continuation": requirement,
        }
        actual = adapter._attach_native_continuation([gm], source, has_tools=False)
        assert actual == _builder_native_request(
            [builder],
            transport=protocol,
            read=lambda rid: read("builder"),
            provider_identity=identity,
        )
        assert actual == [attach_chat_metadata(canonical, metadata)]
        assert canonical == before and "sealed" not in repr(gm) and "sealed" not in repr(builder)
        adapter._owner.provider_continuation = lambda *args, **kwargs: None
        with pytest.raises(CommandRejected, match="continuation is unavailable"):
            adapter._attach_native_continuation([gm], source, has_tools=False)
        with pytest.raises(CommandRejected, match="continuation is unavailable"):
            _builder_native_request(
                [builder], transport=protocol, read=lambda rid: None, provider_identity=identity
            )


@pytest.mark.parametrize(
    "changed", [{"call_id": "other"}, {"name": "mutate"}, {"arguments": '{"different":true}'}]
)
def test_responses_private_call_mismatch_never_reaches_request(changed):
    from sylvia_gm.domain.errors import CommandRejected

    item = {
        "type": "function_call",
        "call_id": "call",
        "name": "inspect",
        "arguments": "{}",
        **changed,
    }
    with pytest.raises(CommandRejected, match="do not match"):
        attach_chat_metadata(
            {
                "role": "assistant",
                "tool_calls": [{"id": "call", "function": {"name": "inspect", "arguments": "{}"}}],
            },
            {"responses_output": [item]},
        )


@pytest.mark.parametrize(
    "stop,expected",
    [
        ("model_context_window_exceeded", "context_length"),
        ("pause_turn", None),
        ("refusal", None),
        ("future", None),
    ],
)
def test_anthropic_special_stops_are_never_ordinary_success(stop, expected):
    from sylvia_gm.provider._anthropic import AnthropicMessagesTransport

    transport = AnthropicMessagesTransport()
    for parse in [
        lambda: transport.parse_response(
            SimpleNamespace(content=[], usage=None, stop_reason=stop), "fixture"
        ),
        lambda: transport.parse_stream_chunk(
            SimpleNamespace(type="message_delta", delta=SimpleNamespace(stop_reason=stop))
        ),
    ]:
        if expected is None:
            with pytest.raises(InvalidResponseError):
                parse()
        else:
            assert parse().finish_reason == expected


@pytest.mark.parametrize("read,write", [(100, 20), (100, 0), (0, 20), (0, 0)])
def test_anthropic_uncached_plus_cache_reads_and_writes_are_total_prompt(read, write):
    from sylvia_gm.provider._anthropic import AnthropicMessagesTransport

    result = AnthropicMessagesTransport().parse_response(
        SimpleNamespace(
            content=[],
            stop_reason="end_turn",
            usage=SimpleNamespace(
                input_tokens=10,
                cache_read_input_tokens=read,
                cache_creation_input_tokens=write,
                output_tokens=5,
            ),
        ),
        "fixture",
    )
    assert result.usage.prompt_tokens == 10 + read + write
    assert result.usage.total_tokens == 15 + read + write
    assert result.cache_usage.cache_miss_tokens == 10
    assert result.cache_usage.total_prompt_tokens == 10 + read + write


@pytest.mark.parametrize(
    "events",
    [
        [],
        [SimpleNamespace(type="message_start")],
        [SimpleNamespace(type="ping"), SimpleNamespace(type="message_start")],
    ],
)
def test_anthropic_empty_or_start_only_eof_is_incomplete(events):
    from sylvia_gm.provider._anthropic import _AnthropicStreamContent

    collector = _AnthropicStreamContent()
    for event in events:
        collector.accept(event)
    with pytest.raises(IncompleteProviderStreamError):
        collector.require_complete()
    assert collector.accept(SimpleNamespace(type="message_stop")) == []
    collector.require_complete()


@pytest.mark.parametrize("ps_state", ["empty", "unavailable", "running"])
def test_ollama_show_metadata_does_not_require_a_running_model(ps_state):
    calls = []

    def handle(request):
        calls.append(request)
        if request.url.path == "/api/ps":
            return httpx.Response(
                503 if ps_state == "unavailable" else 200,
                json={
                    "models": [{"name": "fixture", "context_length": 4096}]
                    if ps_state == "running"
                    else []
                },
            )
        assert request.url.path == "/api/show" and json.loads(request.content) == {
            "model": "fixture"
        }
        return httpx.Response(200, json={"model_info": {"fixture.context_length": 32768}})

    with httpx.Client(transport=httpx.MockTransport(handle)) as client:
        result = settings._merge_ollama_capacity(
            client,
            endpoint="http://127.0.0.1:11434/v1",
            request_headers={},
            discovered=[{"id": "fixture"}],
        )[0]
    assert len(calls) == 2 and result["declared_context_window"] == 32768
    assert result["runtime_context_window"] == (4096 if ps_state == "running" else None)
    assert result["effective_context_window"] == (4096 if ps_state == "running" else None)


def test_gemini_valid_continuation_is_typed_private_and_missing_token_is_invalid():
    from sylvia_gm.provider._exceptions import ProviderContinuationRequiredError
    from sylvia_gm.provider._gemini import GeminiProvider

    candidate = {
        "content": {"parts": [{"thought": True, "text": "sealed"}]},
        "finishReason": "CONTINUATION",
        "continuationToken": "opaque-token",
    }
    with pytest.raises(ProviderContinuationRequiredError) as captured:
        GeminiProvider._parse({"candidates": [candidate]})
    assert captured.value.continuation_token == "opaque-token"
    assert captured.value.native_parts == candidate["content"]["parts"]
    assert "opaque-token" not in str(captured.value) and "sealed" not in repr(captured.value)
    candidate.pop("continuationToken")
    with pytest.raises(InvalidResponseError, match="missing its required token"):
        GeminiProvider._parse({"candidates": [candidate]})


@pytest.mark.parametrize("strict", [True, False, None])
def test_responses_tool_strictness_is_explicit_or_absent(strict):
    function = {
        "name": "inspect",
        "parameters": {"type": "object", "properties": {"optional": {"type": "string"}}},
    }
    if strict is not None:
        function["strict"] = strict
    wire = ResponsesTransport().format_request(
        "fixture", [], [{"type": "function", "function": function}], False
    )
    assert wire["tools"][0]["parameters"] == function["parameters"]
    assert ("strict" in wire["tools"][0]) == (strict is not None)
    if strict is not None:
        assert wire["tools"][0]["strict"] is strict


@pytest.mark.parametrize(
    "base,transport,expected",
    [
        ("https://custom.invalid/inference/v1", "responses", "https://custom.invalid/inference/v1"),
        ("https://opencode.ai/private/v1", "responses", "https://opencode.ai/private/v1"),
        ("http://127.0.0.1:8000/v1", "chat_completions", "http://127.0.0.1:8000/v1"),
        (
            "https://opencode.ai/inference/google/v1beta",
            "responses",
            "https://opencode.ai/inference/openai/v1",
        ),
    ],
)
def test_endpoint_normalization_preserves_custom_routes_and_switches_saved_gateway_family(
    base, transport, expected
):
    from sylvia_gm.provider._protocol_routes import inference_base_url

    assert inference_base_url(base, transport) == expected


@pytest.mark.parametrize(
    "host,expected", [("api.openai.com", True), ("api.x.ai", True), ("custom.invalid", False)]
)
def test_only_documented_direct_responses_routes_request_encrypted_items(host, expected):
    provider = OpenAICompatProvider(
        base_url=f"https://{host}/v1",
        api_key="synthetic",
        model_info=ModelInfo(
            id="fixture",
            name="Fixture",
            provider_name="Fixture",
            provider_key="custom",
            provider_mode="byok",
            transport="responses",
        ),
    )
    wire = provider.provider_request_representation([], None, {"stream": False})
    assert ("reasoning.encrypted_content" in wire.get("include", [])) is expected
    assert (wire.get("store") is False) is expected
    # No request was sent; close the owned SDK client in the test's event loop.
    import asyncio

    asyncio.run(provider.close())


@pytest.mark.asyncio
async def test_responses_stream_preserves_complete_opaque_output_and_closes():
    output = [
        {"type": "reasoning", "id": "rs", "summary": [], "encrypted_content": "sealed"},
        {"type": "function_call", "id": "fc1", "call_id": "c1", "name": "one", "arguments": "{}"},
        {"type": "function_call", "id": "fc2", "call_id": "c2", "name": "two", "arguments": "{}"},
    ]
    events = [
        {"type": "response.output_item.added", "output_index": index, "item": item}
        for index, item in enumerate(output)
    ]
    events.append(
        {
            "type": "response.completed",
            "response": {
                "id": "r",
                "object": "response",
                "status": "completed",
                "output": output,
                "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
            },
        }
    )
    wire = "".join(
        "event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n" for event in events
    )
    closed = []

    class Bytes(httpx.AsyncByteStream):
        async def __aiter__(self):
            for index in range(0, len(wire), 7):
                yield wire[index : index + 7].encode()

        async def aclose(self):
            closed.append(True)

    provider = OpenAICompatProvider(
        base_url="https://fixture.invalid/v1",
        api_key="synthetic",
        model_info=ModelInfo(
            id="fixture",
            name="Fixture",
            provider_name="Fixture",
            provider_key="custom",
            provider_mode="byok",
            transport="responses",
        ),
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, stream=Bytes()
                )
            )
        ),
    )
    try:
        chunks = [
            chunk
            async for chunk in provider.generate_stream([{"role": "user", "content": "Fixture"}])
        ]
        assert [
            chunk.chat_completion_metadata for chunk in chunks if chunk.chat_completion_metadata
        ] == [{"responses_output": output}]
        assert len([chunk for chunk in chunks if chunk.tool_call_delta]) == 2
        assert [chunk.finish_reason for chunk in chunks if chunk.finish_reason] == ["stop"]
        assert closed == [True]
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_stream_cancellation_closes_transport_without_resubmission():
    import asyncio

    closed = []
    requests = []

    class Bytes(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield (
                b'data: {"id":"f","object":"chat.completion.chunk","created":1,"model":"f",'
                b'"choices":[{"index":0,"delta":{"content":"partial"},"finish_reason":null}]}\n\n'
            )
            await asyncio.Event().wait()

        async def aclose(self):
            closed.append(True)

    def handle(request):
        requests.append(request)
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, stream=Bytes())

    provider = OpenAICompatProvider(
        base_url="https://fixture.invalid/v1",
        api_key="synthetic",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    try:
        stream = provider.generate_stream([{"role": "user", "content": "Fixture"}])
        assert (await anext(stream)).content == "partial"
        task = asyncio.create_task(anext(stream))
        await asyncio.sleep(0)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        await stream.aclose()
        assert closed == [True] and len(requests) == 1
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_retry_after_503_is_honored_without_real_wait(monkeypatch):
    from sylvia_gm.provider import _failover

    requests = []
    delays = []

    async def sleep(delay):
        delays.append(delay)

    monkeypatch.setattr(_failover.asyncio, "sleep", sleep)

    def handle(request):
        requests.append(request)
        if len(requests) == 1:
            return httpx.Response(
                503, headers={"retry-after": "75"}, json={"error": {"message": "busy"}}
            )
        return httpx.Response(
            200,
            json={
                "id": "f",
                "object": "chat.completion",
                "created": 1,
                "model": "f",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ready"},
                    }
                ],
            },
        )

    provider = OpenAICompatProvider(
        base_url="https://fixture.invalid/v1",
        api_key="synthetic",
        retry_config=RetryConfig(max_retries=1, retryable_statuses={503}),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    try:
        assert (
            await provider.generate([{"role": "user", "content": "Fixture"}])
        ).content == "ready"
        assert delays == [75] and len(requests) == 2
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_actual_groq_tool_messages_omit_unsupported_name_without_changing_history():
    messages = [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call",
                    "type": "function",
                    "function": {"name": "inspect", "arguments": "{}"},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call", "name": "inspect", "content": '{"ok":true}'},
    ]
    from copy import deepcopy

    before = deepcopy(messages)
    requests = []

    def handle(request):
        requests.append(json.loads(request.content))
        assert all("name" not in item for item in requests[-1]["messages"])
        return httpx.Response(
            200,
            json={
                "id": "f",
                "object": "chat.completion",
                "created": 1,
                "model": "f",
                "choices": [
                    {
                        "index": 0,
                        "finish_reason": "stop",
                        "message": {"role": "assistant", "content": "ready"},
                    }
                ],
            },
        )

    provider = OpenAICompatProvider(
        base_url=settings._PROVIDER_PRESETS["groq"]["base_url"],
        api_key="synthetic",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    try:
        assert (await provider.generate(messages)).content == "ready"
        assert requests[0]["messages"][1]["tool_call_id"] == "call"
        assert messages == before
    finally:
        await provider.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "event_type,status", [("response.failed", "failed"), ("response.incomplete", "incomplete")]
)
async def test_failed_responses_terminal_usage_reaches_error_hook_once(event_type, status):
    events = []

    class Hook:
        async def on_error(self, context):
            events.append(context)

    response = {
        "id": "r",
        "object": "response",
        "status": status,
        "output": [],
        "error": {"code": "server_error", "message": "synthetic"},
        "incomplete_details": {"reason": "content_filter"},
        "usage": {"input_tokens": 12, "output_tokens": 3, "total_tokens": 15},
    }
    event = {"type": event_type, "response": response}
    wire = "event: " + event_type + "\ndata: " + json.dumps(event) + "\n\n"
    provider = OpenAICompatProvider(
        base_url="https://fixture.invalid/v1",
        api_key="synthetic",
        model_info=ModelInfo(
            id="fixture",
            name="Fixture",
            provider_name="Fixture",
            provider_key="custom",
            provider_mode="byok",
            transport="responses",
        ),
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, text=wire
                )
            )
        ),
    )
    provider.hooks.register(Hook())
    try:
        with pytest.raises((InvalidResponseError, ProviderUnavailableError)):
            _ = [
                chunk
                async for chunk in provider.generate_stream(
                    [{"role": "user", "content": "Fixture"}]
                )
            ]
        assert len(events) == 1 and events[0].response.usage_reported
        assert events[0].response.usage.prompt_tokens == 12
        assert events[0].response.usage.total_tokens == 15
        assert events[0].response.content is None
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_usage_hook_counts_only_explicitly_reported_failed_usage_once():
    from sylvia_gm.provider._base import ProviderResponse, TokenUsage
    from sylvia_gm.provider._hooks import HookContext, UsageTrackingHook

    records = []
    hook = UsageTrackingHook(
        SimpleNamespace(record=lambda response, model: records.append(response))
    )
    context = HookContext(
        provider_key="fixture",
        model_id="fixture",
        error=ProviderUnavailableError("busy"),
        response=ProviderResponse(
            usage=TokenUsage(prompt_tokens=12, completion_tokens=3, total_tokens=15),
            usage_reported=True,
        ),
    )
    await hook.on_error(context)
    await hook.on_error(
        HookContext(
            provider_key="fixture", model_id="fixture", error=ProviderUnavailableError("busy")
        )
    )
    assert records == [context.response]


def test_glm_legacy_reasoning_is_required_for_tools_and_new_llama_default_preserves_saved_endpoint(
    tmp_path,
):
    from sylvia_gm.agent._native_continuation import requires_reasoning_with_tools

    model = ModelInfo(
        id="glm-4.7", name="GLM", provider_name="ZAI", provider_key="zai", provider_mode="byok"
    )
    assert requires_reasoning_with_tools(model)
    assert settings._PROVIDER_PRESETS["llama_cpp"]["base_url"] == "http://127.0.0.1:9931/v1"
    path = tmp_path / "config.yaml"
    settings.save_provider_settings(
        path,
        provider_id="llama_cpp",
        preset_id="llama_cpp",
        base_url="http://127.0.0.1:8080/v1",
        credential=None,
        model_id="fixture",
        model_name="Fixture",
        context_window=None,
        max_output_tokens=None,
    )
    assert (
        settings.provider_settings(path)["configured"][0]["base_url"] == "http://127.0.0.1:8080/v1"
    )


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
@pytest.mark.parametrize(
    "message,is_limit",
    [
        ("Unknown parameter: max_tokens", False),
        ("Invalid JSON schema maxLength", False),
        ("maximum context length exceeded", True),
        ("prompt is too long", True),
    ],
)
def test_invalid_options_and_schemas_are_not_misreported_as_context_overflow(
    protocol, message, is_limit
):
    from sylvia_gm.provider._anthropic import AnthropicMessagesTransport
    from sylvia_gm.provider._exceptions import InvalidRequestError, TokenLimitError
    from sylvia_gm.provider._openai_compat import ChatCompletionsTransport

    error = RuntimeError(message)
    error.status_code = 400
    transport = ChatCompletionsTransport() if protocol == "openai" else AnthropicMessagesTransport()
    method = (
        transport._raise_from_openai_error
        if protocol == "openai"
        else transport._raise_from_anthropic_error
    )
    with pytest.raises(TokenLimitError if is_limit else InvalidRequestError):
        method(error, {429, 503})


@pytest.mark.parametrize(
    "name,preserved",
    [
        ("kimi-k3", True),
        ("kimi-k2.7-code-highspeed", True),
        ("kimi-k2.6", False),
        ("deepseek-v4", False),
        ("glm-4.7", False),
    ],
)
def test_legacy_reasoning_scope_distinguishes_kimi_preserved_models(name, preserved):
    from sylvia_gm.agent._native_continuation import requires_reasoning_with_tools

    model = ModelInfo(
        id=name, name=name, provider_key="fixture", provider_name="Fixture", provider_mode="byok"
    )
    assert requires_reasoning_with_tools(model, has_tools=False) is preserved
    assert requires_reasoning_with_tools(model, has_tools=True)


def test_legacy_kimi_reasoning_rejoins_both_consumers_without_tools():
    from sylvia_gm.agent._builder_session import _builder_native_request
    from sylvia_gm.agent._native_continuation import requires_reasoning_with_tools
    from sylvia_gm.agent._provider_session import ProviderSessionAdapter
    from tests.test_alpha_f4_provider_session_adapter import _provider_input, _ScriptedProvider

    source = _provider_input()
    identity = (source.session.provider_id, source.session.model_id)
    provider = _ScriptedProvider([])
    provider.model_info = ModelInfo(
        id="kimi-k3",
        name="Kimi",
        provider_key="moonshot",
        provider_name="Kimi",
        provider_mode="byok",
    )
    native = {"reasoning_content": "Synthetic retained legacy thinking"}
    requirement = {"provider_id": identity[0], "model_id": identity[1], "reasoning_required": True}
    canonical = {"role": "assistant", "content": "Old saved reply"}
    adapter = ProviderSessionAdapter(
        SimpleNamespace(provider_continuation=lambda *args, **kwargs: native),
        provider,
        owner_id="test",
    )
    gm = {**canonical, "_native_reference": {"response_id": "response", "requirement": requirement}}
    builder = {**canonical, "provider_response_id": "response", "native_continuation": requirement}
    expected = [{**canonical, "reasoning_content": native["reasoning_content"]}]
    assert adapter._attach_native_continuation([gm], source, has_tools=False) == expected
    assert (
        _builder_native_request(
            [builder],
            transport="chat_completions",
            read=lambda rid: native,
            provider_identity=identity,
            resend_reasoning=requires_reasoning_with_tools(provider.model_info, has_tools=False),
        )
        == expected
    )
    assert "reasoning_content" not in canonical


@pytest.mark.asyncio
@pytest.mark.parametrize("refusal", [False, True])
async def test_actual_anthropic_stream_cache_totals_survive_success_and_refusal(refusal):
    from anthropic import AsyncAnthropic

    from sylvia_gm.provider._anthropic import AnthropicProvider
    from sylvia_gm.provider._credentials import Credential, CredentialPool

    contexts = []

    class Hook:
        async def on_response(self, context):
            contexts.append(context)

        async def on_error(self, context):
            contexts.append(context)

    events = [
        {
            "type": "message_start",
            "message": {
                "id": "m",
                "type": "message",
                "role": "assistant",
                "model": "fixture",
                "content": [],
                "usage": {
                    "input_tokens": 10,
                    "cache_read_input_tokens": 100,
                    "cache_creation_input_tokens": 20,
                    "output_tokens": 0,
                },
            },
        },
        {
            "type": "message_delta",
            "delta": {"stop_reason": "refusal" if refusal else "end_turn"},
            "usage": {"output_tokens": 5},
        },
        {"type": "message_stop"},
    ]
    wire = "".join(
        "event: " + event["type"] + "\ndata: " + json.dumps(event) + "\n\n" for event in events
    )
    provider = AnthropicProvider(
        base_url="https://fixture.invalid",
        credential_pool=CredentialPool([Credential(type="api_key", value="synthetic")]),
        model_info=ModelInfo(
            id="fixture",
            name="Fixture",
            provider_key="fixture",
            provider_name="Fixture",
            provider_mode="byok",
            transport="anthropic_messages",
        ),
    )
    await provider._client.close()
    provider._client = AsyncAnthropic(
        base_url="https://fixture.invalid",
        api_key="synthetic",
        max_retries=0,
        http_client=httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, headers={"content-type": "text/event-stream"}, text=wire
                )
            )
        ),
    )
    provider.hooks.register(Hook())
    try:
        if refusal:
            with pytest.raises(InvalidResponseError):
                _ = [
                    chunk
                    async for chunk in provider.generate_stream(
                        [{"role": "user", "content": "Fixture"}]
                    )
                ]
        else:
            chunks = [
                chunk
                async for chunk in provider.generate_stream(
                    [{"role": "user", "content": "Fixture"}]
                )
            ]
            assert [chunk.finish_reason for chunk in chunks if chunk.finish_reason] == ["end_turn"]
        assert len(contexts) == 1
        result = contexts[0].response
        assert result.usage.prompt_tokens == 130 and result.usage.total_tokens == 135
        assert (
            result.cache_usage.cache_read_tokens == 100
            and result.cache_usage.cache_write_tokens == 20
        )
        assert result.cache_usage.cache_miss_tokens == 10
    finally:
        await provider.close()
