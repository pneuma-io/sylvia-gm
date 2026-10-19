"""Reported cache telemetry is scoped to one real provider request, never guessed."""

import json
from types import SimpleNamespace

import httpx
import pytest

from sylvia_gm.provider._openai_compat import ChatCompletionsTransport
from tests.test_pes82_chatgpt_failures import SyntheticRuntime, provider_for


def usage_event(last, total=None, *, thread="synthetic-thread", turn="synthetic-turn"):
    return {
        "method": "thread/tokenUsage/updated",
        "params": {
            "threadId": thread,
            "turnId": turn,
            "tokenUsage": {"last": last, "total": total or last, "modelContextWindow": 128000},
        },
    }


@pytest.mark.asyncio
@pytest.mark.parametrize("read,write", [(None, None), (0, 0), (16, 8)])
async def test_chatgpt_reports_cache_field_presence_including_measured_zero(tmp_path, read, write):
    counts = {"inputTokens": 40, "outputTokens": 5, "totalTokens": 45}
    if read is not None:
        counts["cachedInputTokens"] = read
    if write is not None:
        counts["cacheWriteInputTokens"] = write
    runtime = SyntheticRuntime(
        [
            usage_event(counts),
            {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
        ]
    )
    response = await provider_for(runtime, tmp_path).generate([{"role": "user", "content": "Test"}])
    assert response.usage_reported and response.usage.prompt_tokens == 40
    assert response.cache_usage.cache_read_tokens == read
    assert response.cache_usage.cache_write_tokens == write
    assert response.cache_usage.hit_ratio == (None if read is None else read / 40)


@pytest.mark.asyncio
async def test_chatgpt_uses_latest_last_usage_without_summing_cumulative_notifications(tmp_path):
    total = {
        "inputTokens": 9000,
        "outputTokens": 800,
        "totalTokens": 9800,
        "cachedInputTokens": 6000,
        "cacheWriteInputTokens": 3000,
    }
    events = [
        usage_event(
            {
                "inputTokens": n,
                "outputTokens": 2,
                "totalTokens": n + 2,
                "cachedInputTokens": n // 2,
                "cacheWriteInputTokens": 0,
            },
            total,
        )
        for n in (20, 40)
    ]
    events.append({"method": "turn/completed", "params": {"turn": {"status": "completed"}}})
    response = await provider_for(SyntheticRuntime(events), tmp_path).generate(
        [{"role": "user", "content": "Test"}]
    )
    assert response.usage.prompt_tokens == 40 and response.usage.total_tokens == 42
    assert response.cache_usage.cache_read_tokens == 20


@pytest.mark.asyncio
async def test_chatgpt_cumulative_only_or_unowned_usage_is_unavailable(tmp_path):
    counts = {"inputTokens": 40, "outputTokens": 5, "totalTokens": 45, "cachedInputTokens": 20}
    cumulative = usage_event(None, counts)
    unowned = usage_event(counts, thread=None)
    runtime = SyntheticRuntime(
        [
            cumulative,
            unowned,
            {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
        ]
    )
    response = await provider_for(runtime, tmp_path).generate([{"role": "user", "content": "Test"}])
    assert response.usage_reported is False
    assert response.cache_usage.cache_read_tokens is None


@pytest.mark.parametrize(
    "details,read,write",
    [
        (None, None, None),
        (SimpleNamespace(cached_tokens=0, cache_write_tokens=0), 0, 0),
        (SimpleNamespace(cached_tokens=24, cache_write_tokens=8), 24, 8),
    ],
)
def test_openrouter_chat_cache_reads_and_writes_preserve_absence(details, read, write):
    raw = SimpleNamespace(
        model="synthetic-model",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content="Done", tool_calls=None), finish_reason="stop"
            )
        ],
        usage=SimpleNamespace(
            prompt_tokens=40, completion_tokens=5, total_tokens=45, prompt_tokens_details=details
        ),
    )
    response = ChatCompletionsTransport().parse_response(raw, "synthetic-model")
    assert response.cache_usage.cache_read_tokens == read
    assert response.cache_usage.cache_write_tokens == write


@pytest.mark.parametrize("read,write", [(None, None), (0, 0), (12, 8)])
def test_responses_cache_details_preserve_presence(read, write):
    from sylvia_gm.provider._openai_compat import ResponsesTransport

    raw = SimpleNamespace(
        output=[],
        status="completed",
        usage=SimpleNamespace(
            input_tokens=40,
            output_tokens=5,
            total_tokens=45,
            input_tokens_details=SimpleNamespace(cached_tokens=read, cache_write_tokens=write),
        ),
    )
    response = ResponsesTransport().parse_response(raw, "synthetic-model")
    assert response.cache_usage.cache_read_tokens == read
    assert response.cache_usage.cache_write_tokens == write


@pytest.mark.asyncio
async def test_chatgpt_drains_owned_usage_after_tool_shutdown(tmp_path):
    class Runtime(SyntheticRuntime):
        def notifications(self):
            assert self.closed
            return [
                usage_event(
                    {
                        "inputTokens": 50,
                        "outputTokens": 5,
                        "totalTokens": 55,
                        "cachedInputTokens": 20,
                        "cacheWriteInputTokens": 10,
                    }
                ),
                usage_event(
                    {"inputTokens": 900, "outputTokens": 90, "totalTokens": 990}, turn="other-turn"
                ),
            ]

    runtime = Runtime(
        [
            {
                "method": "item/tool/call",
                "params": {
                    "namespace": "sylvia",
                    "tool": "choose",
                    "callId": "one",
                    "arguments": {},
                },
            }
        ]
    )
    response = await provider_for(runtime, tmp_path).generate(
        [{"role": "user", "content": "Test"}],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "choose",
                    "description": "Choose",
                    "parameters": {"type": "object"},
                },
            }
        ],
    )
    assert response.usage.prompt_tokens == 50
    assert response.cache_usage.cache_read_tokens == 20
    assert response.cache_usage.cache_write_tokens == 10


def test_cache_audit_and_totals_distinguish_unknown_zero_and_partial_coverage():
    from sylvia_gm.provider._admin_audit import _usage_projection
    from sylvia_gm.provider._base import CacheUsage, ProviderResponse, TokenUsage
    from sylvia_gm.provider._usage_tracker import SessionUsageTracker

    tracker = SessionUsageTracker()
    for model, read, write in (("one", None, None), ("one", 0, 0), ("two", 24, 8)):
        response = ProviderResponse(
            model=model,
            usage=TokenUsage(40, 5, 45),
            cache_usage=CacheUsage(
                cache_read_tokens=read, cache_write_tokens=write, total_prompt_tokens=40
            ),
        )
        projected = _usage_projection(response)
        assert projected["cached_tokens"] == read
        assert projected["cache_write_tokens"] == write
        tracker.record(response)
        if read is None:
            assert tracker.snapshot().cache_read_tokens is None
    snapshot = tracker.snapshot()
    assert snapshot.request_count == 3 and snapshot.prompt_tokens == 120
    assert snapshot.cache_read_tokens == 24 and snapshot.cache_write_tokens == 8
    assert snapshot.cache_read_requests == snapshot.cache_write_requests == 2
    assert snapshot.by_model["one"].cache_read_tokens == 0
    assert snapshot.by_model["one"].cache_read_requests == 1
    assert snapshot.by_model["two"].cache_read_tokens == 24
    tracker.reset()
    assert tracker.snapshot().cache_read_tokens is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "host,expected",
    [
        ("openrouter.ai", True),
        ("api.openai.com", False),
        ("openrouter.ai.synthetic.invalid", False),
    ],
)
async def test_only_openrouter_serializes_opaque_session_routing_without_backend_pin(
    host, expected
):
    from sylvia_gm.provider._base import ModelInfo
    from sylvia_gm.provider._openai_compat import OpenAICompatProvider

    bodies = []

    def handle(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "id": "synthetic",
                "created": 1,
                "object": "chat.completion",
                "model": "synthetic-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Done"},
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 40,
                    "completion_tokens": 5,
                    "total_tokens": 45,
                    "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                },
            },
        )

    provider = OpenAICompatProvider(
        base_url=f"https://{host}/v1",
        api_key="synthetic",
        model_info=ModelInfo("test/model", "Test", "OpenRouter", "openrouter", "byok"),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    try:
        response = await provider.generate(
            [{"role": "user", "content": "Synthetic probe"}],
            _request_context_id="sylvia-" + "a" * 64,
            extra_body={"test_marker": "retained"},
        )
    finally:
        await provider.close()
    body = bodies[0]
    assert ("session_id" in body) is expected
    if expected:
        assert body["session_id"] == "sylvia-" + "a" * 64
    assert "_request_context_id" not in body and "provider" not in body
    assert body["test_marker"] == "retained"
    assert response.cache_usage.cache_read_tokens == 0


def test_context_identity_tracks_owner_and_compatibility_without_private_labels():
    from dataclasses import replace

    from sylvia_gm.agent._inference_identity import inference_context_id
    from sylvia_gm.provider._base import ModelInfo

    owner = {
        "kind": "participant",
        "campaign_id": "campaign",
        "session_id": "session",
        "package_sha256": "package-one",
        "actor_id": "a",
    }
    model = ModelInfo("test/model", "Test", "OpenRouter", "openrouter", "byok")
    baseline = inference_context_id(owner, model)
    assert baseline == inference_context_id(dict(owner), model)
    assert len(baseline) == 71 and "campaign" not in baseline and "package" not in baseline
    variants = [
        {**owner, key: value}
        for key, value in (
            ("kind", "gm"),
            ("campaign_id", "other"),
            ("session_id", "other"),
            ("package_sha256", "package-two"),
            ("actor_id", "b"),
        )
    ]
    assert all(inference_context_id(item, model) != baseline for item in variants)
    assert inference_context_id(owner, replace(model, id="test/other")) != baseline
    assert inference_context_id(owner, replace(model, provider_key="other")) != baseline


@pytest.mark.asyncio
async def test_cache_usage_stays_with_its_audited_owner_model_and_exchange(tmp_path):
    from sylvia_gm.provider._admin_audit import AdminAuditedProvider

    traces = []
    counts = {
        "inputTokens": 40,
        "outputTokens": 5,
        "totalTokens": 45,
        "cachedInputTokens": 24,
        "cacheWriteInputTokens": 8,
    }
    runtime = SyntheticRuntime([])
    provider = provider_for(runtime, tmp_path)
    audited = AdminAuditedProvider(
        provider,
        sink=traces.append,
        session_id="synthetic-session",
        role="dm",
        provider_id="chatgpt_subscription",
        model_id=provider.model_info.id,
    )
    for context in ("gm", "actor-a", "gm"):
        runtime.events = [
            usage_event(counts),
            {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
        ]
        audited.set_audit_context({"context_id": context})
        await audited.generate([{"role": "user", "content": "Synthetic request"}])
    requests = [record for record in traces if record["kind"] == "provider_request"]
    responses = [record for record in traces if record["kind"] == "provider_response"]
    assert len(requests) == len(responses) == 3
    assert len({record["exchange_id"] for record in responses}) == 3
    for request, response, context in zip(
        requests, responses, ("gm", "actor-a", "gm"), strict=True
    ):
        assert request["exchange_id"] == response["exchange_id"]
        assert request["correlation"]["context_id"] == context
        assert request["provider"]["model_id"] == provider.model_info.id
        assert response["response"]["model"] == provider.model_info.wire_model_id
        assert response["usage"]["input_tokens"] == 40
        assert response["usage"]["cached_tokens"] == 24
        assert response["usage"]["cache_write_tokens"] == 8


@pytest.mark.asyncio
async def test_openrouter_compaction_retains_routing_and_latest_state_after_owned_history():
    from sylvia_gm.agent._working_context import WorkingContextProvider
    from sylvia_gm.provider._base import ModelInfo
    from sylvia_gm.provider._openai_compat import OpenAICompatProvider
    from tests.test_working_context import history, pair

    bodies, saved = [], []

    def handle(request):
        body = json.loads(request.content)
        bodies.append(body)
        return httpx.Response(
            200,
            json={
                "id": "synthetic",
                "created": 1,
                "object": "chat.completion",
                "model": "synthetic",
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": "Own compacted note."},
                        "finish_reason": "stop",
                    }
                ],
            },
        )

    provider = OpenAICompatProvider(
        base_url="https://openrouter.ai/api/v1",
        api_key="synthetic",
        model_info=ModelInfo(
            "synthetic",
            "Test",
            "OpenRouter",
            "openrouter",
            "byok",
            context_window=128000,
            max_output_tokens=4096,
        ),
        http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )

    async def save(record):
        saved.append(record)

    context_id = "sylvia-" + "b" * 64
    messages = history() + [m for i in range(20) for m in pair(100 + i)]
    latest_player = next(message for message in reversed(messages) if message["role"] == "user")
    messages.append(
        {
            "role": "user",
            "content": "CURRENT_OBSERVATION revision=9",
            "_application_data": True,
            "_fresh_application_data": True,
        }
    )
    wrapper = WorkingContextProvider(
        provider,
        save=save,
        request_context_id=context_id,
        record_owner={"kind": "participant", "context_id": context_id},
    )
    try:
        await wrapper.prepare(messages, force=True)
        await wrapper.generate(messages)
    finally:
        await provider.close()
    assert saved and all(body["session_id"] == context_id + "-compact" for body in bodies[:-1])
    final = bodies[-1]
    assert final["session_id"] == context_id
    assert final["messages"][-1] == {"role": "user", "content": "CURRENT_OBSERVATION revision=9"}
    assert latest_player in final["messages"]
    assert any(
        message["role"] == "user" and str(message["content"]).startswith("SYLVIA_CONTINUATION_NOTE")
        for message in final["messages"]
    )
    assert not any("_application_data" in message for message in final["messages"])
