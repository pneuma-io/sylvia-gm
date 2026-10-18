"""Native GenerateContent compatibility with synthetic HTTP responses only."""

import json

import httpx
import pytest

from sylvia_gm.agent._native_continuation import attach_chat_metadata
from sylvia_gm.provider._base import ModelInfo
from sylvia_gm.provider._credentials import Credential, CredentialPool
from sylvia_gm.provider._exceptions import IncompleteProviderStreamError, RateLimitError


def make_provider(handle):
    from sylvia_gm.provider._gemini import GeminiProvider

    model = ModelInfo(
        id="synthetic/gemini",
        name="Gemini",
        provider_key="synthetic",
        provider_mode="byok",
        provider_name="Synthetic",
        provider_model_id="gemini-future",
        transport="gemini_generate_content",
    )
    return GeminiProvider(
        base_url="https://fixture.test/v1beta",
        model_info=model,
        credential_pool=CredentialPool([Credential(type="api_key", value="synthetic-key")]),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )


@pytest.mark.asyncio
async def test_native_tool_result_round_trip_retains_signed_parts_and_json_schema():
    calls = []
    parts = [
        {"thought": True, "text": "private thought"},
        {
            "functionCall": {"id": "native-1", "name": "inspect", "args": {"target": "door"}},
            "thoughtSignature": "opaque-synthetic-signature",
        },
    ]

    def handle(request):
        calls.append(json.loads(request.content))
        assert request.headers["x-goog-api-key"] == "synthetic-key"
        assert "authorization" not in request.headers
        assert request.url.path == "/v1beta/models/gemini-future:generateContent"
        output = parts if len(calls) == 1 else [{"text": "The door is closed."}]
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {"content": {"role": "model", "parts": output}, "finishReason": "STOP"}
                ]
            },
        )

    provider = make_provider(handle)
    try:
        messages = [
            {"role": "system", "content": "Test only"},
            {"role": "user", "content": "Inspect"},
        ]
        tools = [
            {
                "type": "function",
                "function": {
                    "name": "inspect",
                    "description": "Inspect",
                    "parameters": {
                        "type": "object",
                        "properties": {"target": {"type": "string"}},
                        "required": ["target"],
                        "additionalProperties": False,
                    },
                },
            }
        ]
        first = await provider.generate(messages, tools, max_tokens=4096)
        assert first.content == ""
        assert first.reasoning_content == "private thought"
        assert json.loads(first.tool_calls[0].arguments) == {"target": "door"}
        assistant = attach_chat_metadata(
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": first.tool_calls[0].id,
                        "type": "function",
                        "function": {"name": "inspect", "arguments": '{"target":"door"}'},
                    }
                ],
            },
            first.chat_completion_metadata,
        )
        second = await provider.generate(
            messages
            + [
                assistant,
                {"role": "tool", "tool_call_id": "native-1", "content": '{"closed":true}'},
            ],
            tools,
        )
        assert second.content == "The door is closed."
        assert (
            calls[0]["tools"][0]["functionDeclarations"][0]["parametersJsonSchema"]
            == tools[0]["function"]["parameters"]
        )
        assert calls[1]["contents"][1]["parts"] == parts
        assert calls[1]["contents"][2]["parts"][0]["functionResponse"]["response"] == {
            "closed": True
        }
        assert "private thought" not in first.content
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_native_cancel_closes_http_stream_without_resubmitting():
    import asyncio

    closed = []
    calls = []
    class WaitingStream(httpx.AsyncByteStream):
        async def __aiter__(self):
            yield b'data: {"candidates":[{"content":{"parts":[{"text":"partial"}]}}]}\n\n'
            await asyncio.Event().wait()

        async def aclose(self):
            closed.append(True)

    def handle(request):
        calls.append(request)
        return httpx.Response(
            200, stream=WaitingStream(), headers={"content-type": "text/event-stream"}
        )
    provider = make_provider(handle)
    try:
        stream = provider.generate_stream([{"role": "user", "content": "Test"}])
        first = await anext(stream)
        assert first.content == "partial"
        await stream.aclose()
        assert closed == [True]
        assert len(calls) == 1
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_native_stream_requires_terminal_event_and_never_retries_visible_output():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            200,
            text='data: {"candidates":[{"content":{"parts":[{"text":"partial"}]}}]}\n\n',
            headers={"content-type": "text/event-stream"},
        )

    provider = make_provider(handle)
    seen = []
    try:
        with pytest.raises(IncompleteProviderStreamError):
            async for chunk in provider.generate_stream([{"role": "user", "content": "Test"}]):
                seen.append(chunk.content)
        assert seen == ["partial"]
        assert len(calls) == 1
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_native_rate_limit_preserves_retry_after_without_resubmission():
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(
            429,
            json={"error": {"message": "secret must not escape"}},
            headers={"retry-after": "12"},
        )

    provider = make_provider(handle)
    try:
        with pytest.raises(RateLimitError) as failure:
            await provider.generate([{"role": "user", "content": "Test"}])
        assert failure.value.retry_after == 12
        assert "secret" not in str(failure.value)
        assert len(calls) == 1
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_native_stream_tool_result_and_output_cap_complete():
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        assert request.url.params["alt"] == "sse"
        assert payload["generationConfig"]["maxOutputTokens"] == 512
        assert payload["toolConfig"]["functionCallingConfig"]["allowedFunctionNames"] == ["inspect"]
        if len(requests) == 1:
            parts = [
                {"functionCall": {"name": "inspect", "args": {}}, "thoughtSignature": "opaque"}
            ]
        else:
            assert requests[1]["contents"][1]["parts"][0]["thoughtSignature"] == "opaque"
            assert "id" not in requests[1]["contents"][2]["parts"][0]["functionResponse"]
            parts = [{"text": "done"}]
        event = {
            "candidates": [{"content": {"role": "model", "parts": parts}, "finishReason": "STOP"}]
        }
        return httpx.Response(
            200,
            text="data: " + json.dumps(event) + "\n\n",
            headers={"content-type": "text/event-stream"},
        )

    provider = make_provider(handle)
    tools = [
        {"type": "function", "function": {"name": "inspect", "parameters": {"type": "object"}}}
    ]
    options = {
        "max_tokens": 512,
        "tool_choice": {"type": "function", "function": {"name": "inspect"}},
    }
    try:
        messages = [{"role": "user", "content": "test"}]
        chunks = [chunk async for chunk in provider.generate_stream(messages, tools, **options)]
        delta = next(chunk.tool_call_delta for chunk in chunks if chunk.tool_call_delta)
        assert chunks[-1].finish_reason == "tool_calls"
        assistant = attach_chat_metadata(
            {"role": "assistant", "tool_calls": [delta]}, chunks[-1].chat_completion_metadata
        )
        chunks = [
            chunk
            async for chunk in provider.generate_stream(
                messages
                + [assistant, {"role": "tool", "tool_call_id": delta["id"], "content": "{}"}],
                tools,
                **options,
            )
        ]
        assert "".join(chunk.content or "" for chunk in chunks) == "done"
        assert chunks[-1].finish_reason == "stop"
        assert len(requests) == 2
    finally:
        await provider.close()
