"""Subscription admission reaches Codex without inventing an enforced reply budget."""

from copy import deepcopy

import pytest

from sylvia_gm.provider._context_fit import ContextFittingProvider
from sylvia_gm.provider._exceptions import TokenLimitError
from tests.test_pes82_chatgpt_failures import SyntheticRuntime, provider_for


@pytest.mark.asyncio
@pytest.mark.parametrize("size", [136909, 142595, 149210])
async def test_fresh_builder_sized_request_reaches_runtime_with_full_history(tmp_path, size):
    class CapturingRuntime(SyntheticRuntime):
        def call(self, method, params):
            if method == "thread/inject_items":
                self.items = params["items"]
            if method == "turn/start":
                self.turn = params
            return super().call(method, params)

    runtime = CapturingRuntime([
        {"method": "item/agentMessage/delta", "params": {"delta": "Ready."}},
        {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
    ])
    provider = provider_for(runtime, tmp_path)
    provider.model_info.transport = "codex_app_server"
    provider.model_info.context_window = 262144
    provider.model_info.max_output_tokens = 128000
    wrapped = ContextFittingProvider(provider)
    messages = [{"role": "user", "content": "Reference " + "x" * size}]
    before = deepcopy(messages)
    measurement = wrapped.admit_request(messages)
    assert measurement.admission_decision == "runtime_deferred"
    assert measurement.output_budget_mode == "runtime_managed"
    assert measurement.output_token_reserve == 0
    assert measurement.input_tokens is None
    response = await wrapped.generate(messages, timeout=1)
    assert response.content == "Ready."
    assert messages == before
    assert runtime.items[0]["content"][0]["text"] == messages[0]["content"]
    assert "max_tokens" not in runtime.turn


@pytest.mark.asyncio
async def test_actual_codex_overflow_is_reported_as_context_error(tmp_path):
    runtime = SyntheticRuntime([{
        "method": "turn/completed", "params": {"turn": {
            "status": "failed", "error": {"codexErrorInfo": "contextWindowExceeded"},
        }},
    }])
    with pytest.raises(TokenLimitError, match="context"):
        await provider_for(runtime, tmp_path).generate([{"role": "user", "content": "Saved"}])
    assert runtime.closed
