"""Provider observations preserve Sylvia's request/response behavior and explicit unknowns."""

import asyncio
from types import SimpleNamespace

import pytest
from sylviabench.contracts import digest
from sylviabench.provenance import MAX_REQUESTS, ObservedResolver

from sylvia_gm.provider._base import ProviderResponse, ProviderStreamChunk, TokenUsage
from sylvia_gm.provider._hooks import HookContext, HookManager


def test_observer_preserves_requests_bounds_calls_and_keeps_stream_identity_unknown():
    response = ProviderResponse(
        content="An answer",
        model="adapter-model",
        usage_reported=True,
        usage=TokenUsage(prompt_tokens=7, completion_tokens=3, total_tokens=10),
        provider_reported_reasoning_effort="medium",
    )
    calls = []
    closed = []

    class Provider:
        async def generate(self, messages, tools=None, **kwargs):
            calls.append((messages, tools, kwargs))
            return response

        async def generate_stream(self, messages, tools=None, **kwargs):
            try:
                yield ProviderStreamChunk(content="streamed")
                yield ProviderStreamChunk(finish_reason="stop")
            finally:
                closed.append(True)

    class Resolver:
        def __call__(self, session):
            return Provider()

        def model_choices(self):
            return (
                {
                    "provider_id": "selected",
                    "model_id": "exact-model",
                    "effective_context_window": 123456,
                    "transport": "responses",
                },
            )

    observer = ObservedResolver(Resolver())
    provider = observer(SimpleNamespace(provider_id="selected", model_id="exact-model"))
    messages = [{"role": "user", "content": "A synthetic challenge"}]
    tools = [{"type": "function", "function": {"name": "test", "parameters": {}}}]
    assert asyncio.run(provider.generate(messages, tools, reasoning_effort="medium")) is response
    assert calls == [(messages, tools, {"reasoning_effort": "medium"})]
    first = observer.requests[0]
    assert first["messages_sha256"] == digest(messages)
    assert first["tools_sha256"] == digest(tools)
    assert first["adapter_model"] == "adapter-model"
    assert observer.confirmed("confirmed_model") is None
    assert first["observed_reasoning"] == "medium"
    assert first["usage"]["total_tokens"] == 10
    assert observer.selections[0]["effective_context_window"] == 123456
    assert first["stage"] == "preparation"
    alias = "tool_123456abcdef_update_campaign_file"
    tools = [{"type": "function", "function": {"name": alias}}]

    async def consume():
        return [chunk.content async for chunk in provider.generate_stream(messages, tools)]

    assert asyncio.run(consume()) == ["streamed", ""]
    assert closed == [True]
    assert "observed_model" not in observer.requests[-1]
    assert "usage" not in observer.requests[-1]
    assert observer.requests[-1]["stage"] == "gm"
    assert set(observer.requests[-1]["tool_fingerprints"]) == {alias}
    assert observer.requests[-1]["message_fingerprints"] == [
        {"role": "user", "sha256": digest(messages[0])}
    ]
    for _ in range(MAX_REQUESTS - len(observer.requests)):
        asyncio.run(provider.generate(messages, tools))
    with pytest.raises(RuntimeError, match="request limit"):
        asyncio.run(provider.generate(messages, tools))
    assert len(calls) == MAX_REQUESTS - 1  # One request above used the streaming API.


def test_stream_hook_records_usage_and_runtime_confirmation_without_copying_raw_content():
    class Provider:
        def __init__(self):
            self.hooks = HookManager()

        async def generate_stream(self, messages, tools=None, **kwargs):
            await self.hooks.fire_on_request(
                HookContext(
                    provider_key="chatgpt_subscription",
                    model_id="requested",
                    request_json={
                        "direction": "receive",
                        "method": "thread/start/result",
                        "params": {"model": "runtime-confirmed", "private": "never retain"},
                    },
                )
            )
            yield ProviderStreamChunk(content="A reply")
            yield ProviderStreamChunk(finish_reason="length")
            await self.hooks.fire_on_response(
                HookContext(
                    provider_key="chatgpt_subscription",
                    model_id="requested",
                    response=ProviderResponse(
                        model="adapter-echo",
                        finish_reason="length",
                        usage_reported=True,
                        usage=TokenUsage(13, 7, 20),
                    ),
                )
            )

    provider = Provider()

    class Resolver:
        def __call__(self, session):
            return provider

        def model_choices(self):
            return ({"provider_id": "selected", "model_id": "requested"},)

    observer = ObservedResolver(Resolver())
    wrapped = observer(SimpleNamespace(provider_id="selected", model_id="requested"))

    async def consume():
        return [
            c.content async for c in wrapped.generate_stream([{"role": "user", "content": "Hi"}])
        ]

    assert asyncio.run(consume()) == ["A reply", ""]
    assert observer.confirmed("confirmed_model") == "runtime-confirmed"
    assert observer.requests[0]["adapter_model"] == "adapter-echo"
    assert observer.requests[0]["finish_reason"] == "length"
    assert observer.usage()["tokens"]["total_tokens"] == 20
    assert observer.usage()["coverage"] == "complete"
    assert "never retain" not in str(observer.summary())
    # A failed/unreported request must prevent a complete identity or usage claim.
    observer.requests.append({"messages_sha256": "unreported"})
    assert observer.confirmed("confirmed_model") is None
    assert observer.usage()["coverage"] == "partial"
