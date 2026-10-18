"""Every HTTP preset reaches its selected protocol through the production factory.

All network responses are synthetic. No key, account, or remote inference is used.
Subscription's installed-runtime path is separately tested against local inference.
"""

import json

import httpx
import pytest

from sylvia_gm.config import provider_settings as settings
from sylvia_gm.provider._base import ModelInfo, ProviderConfig, RetryConfig
from sylvia_gm.provider._context_fit import ContextFittingProvider
from sylvia_gm.provider._factory import ProviderFactory
from sylvia_gm.provider._tool_delta import parse_tool_call_delta

PRESETS = [preset for preset in settings._PROVIDER_PRESETS if preset != "chatgpt_subscription"]


def event_stream(protocol, *, tools=False):
    if protocol == "anthropic_messages":
        block = (
            {"type": "tool_use", "id": "call-1", "name": "inspect", "input": {}}
            if tools
            else {"type": "text", "text": ""}
        )
        events = [
            {
                "type": "message_start",
                "message": {
                    "id": "m",
                    "type": "message",
                    "role": "assistant",
                    "model": "future",
                    "content": [],
                    "usage": {"input_tokens": 1, "output_tokens": 0},
                },
            },
            {"type": "content_block_start", "index": 0, "content_block": block},
            {
                "type": "content_block_delta",
                "index": 0,
                "delta": {"type": "input_json_delta", "partial_json": '{"target":"door"}'}
                if tools
                else {"type": "text_delta", "text": "ready"},
            },
            {"type": "content_block_stop", "index": 0},
            {
                "type": "message_delta",
                "delta": {"stop_reason": "tool_use" if tools else "end_turn"},
                "usage": {"output_tokens": 2},
            },
            {"type": "message_stop"},
        ]
    elif protocol == "responses":
        item = (
            {
                "type": "function_call",
                "id": "fc-1",
                "call_id": "call-1",
                "name": "inspect",
                "arguments": '{"target":"door"}',
            }
            if tools
            else {
                "type": "message",
                "id": "msg-1",
                "role": "assistant",
                "content": [{"type": "output_text", "text": "ready"}],
            }
        )
        events = [
            {
                "type": "response.created",
                "response": {"id": "r", "status": "in_progress", "output": []},
            },
            {"type": "response.output_item.added", "output_index": 0, "item": item},
        ]
        if not tools:
            events.append(
                {
                    "type": "response.output_text.delta",
                    "item_id": "msg-1",
                    "output_index": 0,
                    "content_index": 0,
                    "delta": "ready",
                }
            )
        events.extend(
            [
                {"type": "response.output_item.done", "output_index": 0, "item": item},
                {
                    "type": "response.completed",
                    "response": {
                        "id": "r",
                        "status": "completed",
                        "output": [item],
                        "usage": {"input_tokens": 1, "output_tokens": 2, "total_tokens": 3},
                    },
                },
            ]
        )
    else:
        delta = (
            {
                "tool_calls": [
                    {
                        "index": 0,
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "inspect", "arguments": '{"target":"door"}'},
                    }
                ]
            }
            if tools
            else {"content": "ready"}
        )
        events = [
            {
                "id": "r",
                "model": "future",
                "object": "chat.completion.chunk",
                "created": 1,
                "choices": [
                    {"index": 0, "delta": delta, "finish_reason": "tool_calls" if tools else "stop"}
                ],
            }
        ]
    return "".join(
        ("event: " + event["type"] + "\n" if protocol == "anthropic_messages" else "")
        + "data: "
        + json.dumps(event)
        + "\n\n"
        for event in events
    ) + ("data: [DONE]\n\n" if protocol == "chat_completions" else "")


@pytest.mark.asyncio
@pytest.mark.parametrize("preset", PRESETS)
async def test_every_http_preset_automatic_allowance_stream_tool_result_and_continuation(preset):
    definition = settings._PROVIDER_PRESETS[preset]
    protocol = definition["default_transport"]
    requests = []

    def handle(request):
        payload = json.loads(request.content)
        requests.append(payload)
        expected = {
            "chat_completions": "/chat/completions",
            "responses": "/responses",
            "anthropic_messages": "/messages",
        }[protocol]
        assert request.url.path.endswith(expected)
        key = "max_output_tokens" if protocol == "responses" else "max_tokens"
        assert payload[key] == 4096
        if definition["credential_required"]:
            header = "x-api-key" if protocol == "anthropic_messages" else "authorization"
            expected = "synthetic-key" if header == "x-api-key" else "Bearer synthetic-key"
            assert request.headers[header] == expected
        if len(requests) == 2:
            assert "call-1" in json.dumps(payload)
            assert "closed" in json.dumps(payload)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=event_stream(protocol, tools=len(requests) == 1),
        )

    model = ModelInfo(
        id=f"{preset}/future",
        name="Future",
        provider_name=definition["name"],
        provider_key=preset,
        provider_mode=definition["mode"],
        provider_model_id="future",
        transport=protocol,
        context_window=None,
        max_output_tokens=None,
    )
    raw = ProviderFactory.create(
        ProviderConfig(
            provider_key=preset,
            transport=protocol,
            base_url=definition["base_url"] or "https://fixture.test/v1",
            models=[model],
            credentials_list=[{"type": "api_key", "value": "synthetic-key"}]
            if definition["credential_required"]
            else [],
            retry_config=RetryConfig(max_retries=0),
        )
    )
    # Replace the SDK's HTTP transport; production conversion/factory/context code stays real.
    await raw._client.close()
    client = httpx.AsyncClient(transport=httpx.MockTransport(handle))
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
    provider = ContextFittingProvider(raw)
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
                },
            },
        }
    ]
    messages = [{"role": "user", "content": "Inspect the door."}]
    try:
        chunks = [chunk async for chunk in provider.generate_stream(messages, tools)]
        fragments = [
            parse_tool_call_delta(chunk.tool_call_delta)
            for chunk in chunks
            if chunk.tool_call_delta
        ]
        delta = (
            0,
            next(item[1] for item in fragments if item[1]),
            next(item[2] for item in fragments if item[2]),
            "".join(item[3] for item in fragments),
        )
        assert delta == (0, "call-1", "inspect", '{"target":"door"}')
        assert [chunk.finish_reason for chunk in chunks if chunk.finish_reason] == [
            "stop"
            if protocol == "responses"
            else "tool_use"
            if protocol == "anthropic_messages"
            else "tool_calls"
        ]
        continuation = messages + [
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "inspect", "arguments": delta[3]},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": '{"closed":true}'},
        ]
        chunks = [chunk async for chunk in provider.generate_stream(continuation, tools)]
        assert "".join(chunk.content or "" for chunk in chunks) == "ready"
        assert len(requests) == 2
        assert model.max_output_tokens is None, "request default is not model metadata"
    finally:
        await provider.close()
