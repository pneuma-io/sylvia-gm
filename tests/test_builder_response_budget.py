"""Builder budgets are applied on the provider boundary and retained in model settings."""

import asyncio

import pytest

from sylvia_gm.agent._mode import AgentMode
from sylvia_gm.agent._runner import AgentRunner
from sylvia_gm.config.provider_settings import provider_settings, save_provider_settings
from sylvia_gm.provider._base import FakeProvider, ModelInfo


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("transport,maximum,budget,expected", [
    ("chat_completions", 128000, 32000, 32000),
    ("responses", 128000, 64000, 64000),
    ("anthropic_messages", 16000, 32000, 16000),
    ("codex_app_server", 128000, 32000, None),
])
async def test_builder_sends_only_an_enforceable_response_budget(
    tmp_path, streaming, transport, maximum, budget, expected,
):
    class RecordingProvider(FakeProvider):
        async def generate(self, messages, tools=None, **options):
            self.options = options
            return await super().generate(messages, tools)

        async def generate_stream(self, messages, tools=None, **options):
            self.options = options
            async for chunk in super().generate_stream(messages, tools):
                yield chunk

    provider = RecordingProvider()
    provider.model_info = ModelInfo(
        id="test/model", name="Model", provider_name="Test", provider_key="test",
        provider_mode="byok", transport=transport, max_output_tokens=maximum,
        builder_output_tokens=budget,
    )
    runner = AgentRunner(AgentMode.BUILDER_FREEFORM, provider, None, project_root=tmp_path)
    if streaming:
        await runner.turn_stream("Create a character.", asyncio.Queue())
    else:
        await runner.turn("Create a character.")
    assert provider.options.get("max_tokens") == expected
    assert provider.model_info.max_output_tokens == maximum


def test_builder_budget_saves_without_changing_gm_output_or_resetting_on_ordinary_edit(tmp_path):
    path = tmp_path / "config.yaml"
    arguments = {
        "provider_id": "lm_studio", "base_url": "http://127.0.0.1:1234/v1", "credential": None,
        "model_id": "test-model", "model_name": "Model", "context_window": 262144,
        "max_output_tokens": 128000,
    }
    save_provider_settings(path, **arguments, builder_output_tokens=64000)
    save_provider_settings(path, **arguments)
    model = provider_settings(path)["configured"][0]["models"][0]
    assert model["builder_output_tokens"] == 64000
    assert model["max_output_tokens"] == 128000


@pytest.mark.asyncio
async def test_builder_keeps_full_history_after_more_than_fifty_messages(tmp_path):
    provider = FakeProvider()
    history = [{"role": "user" if n % 2 == 0 else "assistant", "content": f"Decision {n}"}
               for n in range(60)]
    runner = AgentRunner(
        AgentMode.BUILDER_FREEFORM, provider, None, initial_history=history, project_root=tmp_path,
    )
    await runner.turn_stream("Continue.", asyncio.Queue(), retry_on_length=False)
    assert runner.history_snapshot()[:60] == history
    assert len(runner.history_snapshot()) == 62
