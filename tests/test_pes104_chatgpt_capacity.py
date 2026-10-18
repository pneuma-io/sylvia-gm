"""Codex usage observations refine working-context planning without changing settings."""

from copy import deepcopy

import pytest

from sylvia_gm.provider._context_fit import ContextFittingProvider
from tests.test_pes82_chatgpt_failures import SyntheticRuntime, provider_for


def usage_event(window, *, thread_id="synthetic-thread", turn_id="synthetic-turn"):
    return {"method": "thread/tokenUsage/updated", "params": {
        "threadId": thread_id, "turnId": turn_id,
        "tokenUsage": {
            "modelContextWindow": window,
            "last": {"inputTokens": 27724, "outputTokens": 172, "totalTokens": 27896},
        },
    }}


def configured_provider(tmp_path, event, *, configured=262144):
    runtime = SyntheticRuntime([
        event, {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
    ])
    provider = provider_for(runtime, tmp_path)
    model = provider.model_info
    model.context_window = configured
    model.context_window_source = "user_configured"
    model.configured_context_window = configured
    model.configured_context_observed_at = "2026-09-11T09:31:21+00:00"
    model.max_output_tokens = 128000
    return provider, runtime


@pytest.mark.asyncio
async def test_reported_capacity_reaches_existing_wrapper_and_next_admission(tmp_path):
    provider, runtime = configured_provider(tmp_path, usage_event(258400))
    wrapped = ContextFittingProvider(provider)
    messages = [{"role": "user", "content": "Synthetic request"}]
    before = deepcopy(messages)
    response = await wrapped.generate(messages, timeout=1)
    assert response.usage_reported
    assert response.usage.prompt_tokens == 27724
    assert runtime.closed
    assert messages == before
    assert wrapped.model_info is provider.model_info
    model = wrapped.model_info
    assert model.runtime_context_window == model.context_window == 258400
    assert model.runtime_context_source == model.context_window_source == "runtime_reported"
    assert model.runtime_context_observed_at
    assert model.configured_context_window == 262144
    assert model.configured_context_observed_at == "2026-09-11T09:31:21+00:00"
    assert model.configured_runtime_mismatch
    assert model.max_output_tokens == 128000
    measurement = wrapped.admit_request([{"role": "user", "content": "x" * 132000}])
    assert measurement.effective_context_capacity == 258400
    assert measurement.admission_decision == "runtime_deferred"
    assert measurement.output_token_reserve == 0


@pytest.mark.asyncio
async def test_runtime_observation_preserves_a_lower_configured_cap(tmp_path):
    provider, _ = configured_provider(tmp_path, usage_event(258400), configured=200000)
    await provider.generate([], timeout=1)
    assert provider.model_info.runtime_context_window == 258400
    assert provider.model_info.context_window == 200000
    assert provider.model_info.context_window_source == "user_configured"
    assert not provider.model_info.configured_runtime_mismatch


@pytest.mark.asyncio
@pytest.mark.parametrize("window", [None, True, 0, -1, "258400", 258400.5])
async def test_invalid_runtime_window_does_not_replace_configured_capacity(tmp_path, window):
    provider, _ = configured_provider(tmp_path, usage_event(window))
    response = await provider.generate([], timeout=1)
    assert response.usage_reported
    assert provider.model_info.context_window == 262144
    assert provider.model_info.runtime_context_window is None


@pytest.mark.asyncio
@pytest.mark.parametrize("identity", [
    {"thread_id": "another-thread"}, {"turn_id": "another-turn"},
    {"thread_id": None}, {"turn_id": None},
])
async def test_window_must_identify_the_active_runtime_turn(tmp_path, identity):
    provider, _ = configured_provider(tmp_path, usage_event(258400, **identity))
    await provider.generate([], timeout=1)
    assert provider.model_info.runtime_context_window is None


@pytest.mark.asyncio
async def test_null_usage_notification_does_not_interrupt_the_response(tmp_path):
    event = usage_event(258400)
    event["params"]["tokenUsage"] = None
    provider, runtime = configured_provider(tmp_path, event)
    response = await provider.generate([], timeout=1)
    assert not response.usage_reported
    assert provider.model_info.runtime_context_window is None
    assert runtime.closed
