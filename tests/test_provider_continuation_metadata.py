"""Provider-native tool continuation survives the real SDK boundary."""
import json

import httpx
import pytest

from sylvia_gm.provider._base import ModelInfo
from sylvia_gm.provider._openai_compat import OpenAICompatProvider
from sylvia_gm.provider._tool_delta import parse_tool_call_delta


def test_repeated_stream_call_id_retains_one_signature_binding():
    from sylvia_gm.provider._chat_metadata import ChatCompletionMetadata

    metadata = ChatCompletionMetadata()
    metadata.add({"tool_calls": [{"index": 0, "id": "call",
                                 "function": {"arguments": "{"}}]})
    metadata.add({"tool_calls": [{"index": 0, "id": "call",
                                 "function": {"arguments": "}"},
                                 "extra_content": {"google": {"thought_signature": "opaque"}}}]})
    assert metadata.payload() == {
        "tool_call_extras": {"call": {"google": {"thought_signature": "opaque"}}},
    }



@pytest.mark.asyncio
async def test_private_chat_extensions_and_tool_bounds_reach_next_sdk_request():
    from sylvia_gm.agent._native_continuation import attach_chat_metadata

    signature = {"google": {"thought_signature": "synthetic-opaque"}}
    reasoning = [{"type": "reasoning.encrypted", "data": "synthetic-encrypted", "index": 0}]
    metadata = {"tool_call_extras": {"call-1": signature}, "reasoning_details": reasoning}
    messages = [
        {"role": "user", "content": "Continue the synthetic test"},
        attach_chat_metadata({
            "role": "assistant", "content": None, "tool_calls": [
                {"id": "call-1", "type": "function",
                 "function": {"name": "write", "arguments": '{"summary":"saved"}'}},
            ],
        }, metadata),
        {"role": "tool", "tool_call_id": "call-1", "content": '{"saved":true}'},
    ]
    tools = [{"type": "function", "function": {
        "name": "write", "description": "Synthetic write", "parameters": {
            "type": "object", "properties": {"summary": {"type": "string", "maxLength": 1000}},
            "required": ["summary"],
        },
    }}]

    def handle(request):
        payload = json.loads(request.content)
        assert request.url.path == "/v1/chat/completions"
        assert payload["messages"][1]["reasoning_details"] == reasoning
        assert payload["messages"][1]["tool_calls"][0]["extra_content"] == signature
        assert payload["messages"][2] == messages[2]
        assert payload["tools"][0]["function"]["parameters"]["properties"]["summary"] == {
            "type": "string", "maxLength": 1000,
        }
        event = {"id": "synthetic", "created": 1, "object": "chat.completion.chunk",
                 "model": "synthetic", "choices": [
                     {"index": 0, "delta": {"content": "Saved."}, "finish_reason": "stop"},
                 ]}
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n",
        )

    provider = OpenAICompatProvider(
        base_url="https://synthetic.invalid/v1", api_key="synthetic",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    try:
        chunks = [chunk async for chunk in provider.generate_stream(messages, tools=tools)]
        assert "".join(chunk.content or "" for chunk in chunks) == "Saved."
    finally:
        await provider.close()

@pytest.mark.asyncio
async def test_all_tool_calls_in_one_stream_chunk_survive_sdk_and_adapter():
    """One SSE chunk may carry more than one independent tool call."""
    calls = [
        {"index": index, "id": f"call-{index}", "type": "function",
         "function": {"name": name, "arguments": "{}"}}
        for index, name in enumerate(("first", "second"))
    ]
    event = {"id": "synthetic", "model": "test", "created": 1,
             "object": "chat.completion.chunk",
             "choices": [{"index": 0, "delta": {"tool_calls": calls},
                          "finish_reason": "tool_calls"}]}
    wire = "data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n"
    provider = OpenAICompatProvider(
        base_url="https://synthetic.invalid/v1", api_key="synthetic",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, text=wire,
            ),
        )),
        model_info=ModelInfo(id="test", name="Test", provider_name="Test",
                            provider_key="custom", provider_mode="byok"),
    )
    try:
        chunks = [chunk async for chunk in provider.generate_stream(
            [{"role": "user", "content": "Use both tools."}],
        )]
        observed = [parse_tool_call_delta(chunk.tool_call_delta)
                    for chunk in chunks if chunk.tool_call_delta is not None]
        assert observed == [(0, "call-0", "first", "{}"), (1, "call-1", "second", "{}")]
        assert [chunk.finish_reason for chunk in chunks if chunk.finish_reason] == ["tool_calls"]
    finally:
        await provider.close()



def test_native_chat_metadata_survives_response_and_stream_parsing():
    from openai.types.chat import ChatCompletion

    from sylvia_gm.provider._openai_compat import ChatCompletionsTransport

    extras = {"google": {"thought_signature": "opaque-synthetic-signature"}}
    details = [{"type": "reasoning.encrypted", "data": "opaque-synthetic-data", "index": 0}]
    response = ChatCompletion.model_validate({
        "id": "test", "object": "chat.completion", "created": 1, "model": "test",
        "choices": [{"index": 0, "finish_reason": "tool_calls", "message": {
            "role": "assistant", "content": None, "reasoning_details": details,
            "tool_calls": [{"id": "call-1", "type": "function", "extra_content": extras,
                            "function": {"name": "read", "arguments": "{}"}}],
        }}],
    })
    parsed = ChatCompletionsTransport().parse_response(response, "test")
    assert getattr(parsed, "chat_completion_metadata", None) == {
        "reasoning_details": details, "tool_call_extras": {"call-1": extras},
    }



def test_chat_metadata_rejoins_builder_and_gm_without_entering_saved_messages():
    from copy import deepcopy
    from types import SimpleNamespace

    from sylvia_gm.agent._builder_session import _builder_native_request
    from sylvia_gm.agent._provider_session import ProviderSessionAdapter
    from sylvia_gm.domain.errors import CommandRejected
    from tests.test_alpha_f4_provider_session_adapter import _provider_input, _ScriptedProvider

    source = _provider_input()
    identity = (source.session.provider_id, source.session.model_id)
    metadata = {"tool_call_extras": {"call-1": {"google": {"thought_signature": "opaque"}}},
                "reasoning_details": [{"type": "reasoning.encrypted", "data": "sealed"}]}
    requirement = {"provider_id": identity[0], "model_id": identity[1],
                   "chat_completion_required": True}
    canonical = {"role": "assistant", "content": None, "tool_calls": [
        {"id": "call-1", "type": "function", "function": {"name": "read", "arguments": "{}"}},
    ]}
    before = deepcopy(canonical)
    native = {"chat_completion_metadata": metadata}
    builder = {**canonical, "provider_response_id": "response",
               "native_continuation": requirement}
    result = _builder_native_request(
        [builder], transport="chat_completions", read=lambda response_id: native,
        provider_identity=identity,
    )
    assert result[0]["tool_calls"][0]["extra_content"] == metadata["tool_call_extras"]["call-1"]
    assert result[0]["reasoning_details"] == metadata["reasoning_details"]
    assert canonical == before and "reasoning_details" not in builder
    with pytest.raises(CommandRejected, match="continuation is unavailable"):
        _builder_native_request([builder], transport="chat_completions",
                                read=lambda response_id: None, provider_identity=identity)
    provider = _ScriptedProvider([])
    provider.model_info = ModelInfo(id="test", name="Test", provider_name="Test",
                                   provider_key="synthetic", provider_mode="byok")
    adapter = ProviderSessionAdapter(
        SimpleNamespace(provider_continuation=lambda *args, **kwargs: native),
        provider, owner_id="test",
    )
    gm = {**canonical, "_native_reference": {
        "response_id": "response", "requirement": requirement,
    }}
    assert adapter._attach_native_continuation([gm], source) == result
    assert canonical == before
    adapter._owner.provider_continuation = lambda *args, **kwargs: None
    with pytest.raises(CommandRejected, match="continuation is unavailable"):
        adapter._attach_native_continuation([gm], source)


@pytest.mark.asyncio
async def test_streamed_private_metadata_preserves_fragments_and_tool_identity():
    events = [
        {"reasoning_details": [{"index": 0, "type": "reasoning.text", "text": "first "}]},
        {"reasoning_details": [{"index": 0, "type": "reasoning.text",
                                "text": "second", "signature": "sealed"}],
         "tool_calls": [{"index": 0, "id": "call-1", "type": "function",
                         "function": {"name": "read", "arguments": "{}"},
                         "extra_content": {"google": {"thought_signature": "opaque"}}}]},
    ]
    wire = "".join("data: " + json.dumps({
        "id": "test", "model": "test", "created": 1, "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": delta, "finish_reason": None}],
    }) + "\n\n" for delta in events) + "data: " + json.dumps({
        "id": "test", "model": "test", "created": 1, "object": "chat.completion.chunk",
        "choices": [{"index": 0, "delta": {}, "finish_reason": "tool_calls"}],
    }) + "\n\ndata: [DONE]\n\n"
    provider = OpenAICompatProvider(
        base_url="https://synthetic.invalid/v1", api_key="synthetic",
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200, headers={"content-type": "text/event-stream"}, text=wire))),
        model_info=ModelInfo(id="test", name="Test", provider_name="Test",
                            provider_key="custom", provider_mode="byok"),
    )
    try:
        chunks = [c async for c in provider.generate_stream([{"role": "user", "content": "Read"}])]
        native = [c.chat_completion_metadata for c in chunks if c.chat_completion_metadata]
        assert native == [{
            "reasoning_details": [{"index": 0, "type": "reasoning.text",
                                   "text": "first second", "signature": "sealed"}],
            "tool_call_extras": {"call-1": {"google": {"thought_signature": "opaque"}}},
        }]
        assert not any(c.content for c in chunks)
        assert "".join(c.reasoning_content for c in chunks) == "first second"
    finally:
        await provider.close()

