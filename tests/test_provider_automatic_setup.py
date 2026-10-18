"""Advertised models can be selected without inventing their unreported limits."""

import pytest

from sylvia_gm.agent._provider_resolver import ConfiguredProviderResolver
from sylvia_gm.config import chatgpt_settings
from sylvia_gm.config import provider_settings as settings
from sylvia_gm.provider._base import ModelInfo, Provider, ProviderResponse
from sylvia_gm.provider._context_fit import ContextFittingProvider


@pytest.mark.parametrize("preset", list(settings._PROVIDER_PRESETS))
@pytest.mark.parametrize("metadata", ["rich", "sparse", "absent"])
def test_every_preset_saves_reopens_new_model_without_numeric_input(
    tmp_path, monkeypatch, preset, metadata
):
    facts = {"id": "future-advertised-model", "name": "Future model"}
    if metadata == "rich":
        facts.update(context_length=262144, max_output_tokens=8192)
    elif metadata == "sparse":
        facts.update(context_length=262144)
    selected = settings._discovery_model(facts, preset_id=preset)
    definition = settings._PROVIDER_PRESETS[preset]
    path = tmp_path / "config.yaml"
    if preset == "chatgpt_subscription":

        class Account:
            def status(self):
                return {"signed_in": True}

            def models(self):
                return [
                    {
                        "id": facts["id"],
                        "name": facts["name"],
                        "runtime_context_window": facts.get("context_length"),
                        "runtime_context_source": "runtime_reported",
                        "output_reference_tokens": facts.get("max_output_tokens"),
                    }
                ]

        monkeypatch.setattr(chatgpt_settings, "_account", lambda _: Account())
        chatgpt_settings.chatgpt_settings_action(
            tmp_path,
            "add_model",
            {
                "id": facts["id"],
                "context_window": None,
                "max_output_tokens": None,
            },
        )
    else:
        settings.save_provider_settings(
            path,
            provider_id=preset,
            preset_id=preset,
            base_url=definition["base_url"] or "http://127.0.0.1:38107/v1",
            credential="synthetic-key" if definition["credential_required"] else None,
            model_id=facts["id"],
            model_name=facts["name"],
            context_window=None,
            max_output_tokens=None,
            declared_max_output_tokens=selected["max_output_tokens"],
            declared_context_window=selected["declared_context_window"],
            declared_context_source=selected["declared_context_source"],
            runtime_context_window=selected["runtime_context_window"],
            runtime_context_source=selected["runtime_context_source"],
        )
    first = settings.provider_settings(path)
    assert settings.provider_settings(path) == first
    row = first["configured"][0]["models"][0]
    assert row["configured_output_tokens"] is None
    assert row["configured_context_window"] is None
    assert row["declared_max_output_tokens"] == facts.get("max_output_tokens")
    if metadata != "rich":
        assert row["output_allowance_source"] == (
            "runtime_managed" if preset == "chatgpt_subscription" else "application_default"
        )
    assert first["default_model"] is None
    assert len(ConfiguredProviderResolver(path).model_choices()) == 1


@pytest.mark.parametrize(
    "preset", [key for key in settings._PROVIDER_PRESETS if key != "chatgpt_subscription"]
)
def test_every_http_preset_discovers_sparse_models_and_preserves_default_across_switch(
    tmp_path, monkeypatch, preset
):
    import httpx

    from tests.test_alpha_09_provider_management import _install_httpx_mock

    definition = settings._PROVIDER_PRESETS[preset]
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={"data": [{"id": "new-a"}, {"id": "new-b"}]})

    _install_httpx_mock(monkeypatch, handle)
    path = tmp_path / "config.yaml"
    command = {
        "provider_id": preset,
        "preset_id": preset,
        "base_url": definition["base_url"] or "http://127.0.0.1:38107/v1",
        "credential": "synthetic-key" if definition["credential_required"] else None,
    }
    result = settings.discover_provider_models(path, **command)
    assert [row["id"] for row in result["models"]] == ["new-a", "new-b"]
    assert all(row["declared_max_output_tokens"] is None for row in result["models"])
    assert all(request.method == "GET" or (
        preset == "ollama" and request.method == "POST" and request.url.path == "/api/show"
    ) for request in calls), "discovery must only query catalogues or Ollama model metadata"
    assert calls[0].url.path.endswith("/models")
    canonical = {}
    for row in result["models"]:
        saved = settings.save_provider_settings(
            path,
            **command,
            model_id=row["id"],
            model_name=row["id"],
            context_window=None,
            max_output_tokens=None,
        )
        canonical[row["id"]] = saved["canonical_model_id"]
    profile_id = saved["provider_id"]
    before = settings.provider_settings(path)
    for selected in ("new-a", "new-b", "new-a"):
        settings.set_default_model_settings(
            path, provider_id=profile_id, model_id=canonical[selected]
        )
        reopened = settings.provider_settings(path)
        assert reopened["configured"] == before["configured"]
        assert reopened["default_model"] == {
            "provider_id": profile_id,
            "model_id": canonical[selected],
        }
    assert len(ConfiguredProviderResolver(path).model_choices()) == 2


class RecordingProvider(Provider):
    def __init__(self, transport):
        super().__init__()
        self.model_info = ModelInfo(
            id="future-model",
            name="Future model",
            provider_name="Synthetic",
            provider_key="synthetic",
            provider_mode="byok",
            transport=transport,
            context_window=None,
            max_output_tokens=None,
        )
        self.sent = []

    async def generate(self, messages, tools=None, **options):
        self.sent.append(options)
        return ProviderResponse(content="ready")

    async def generate_stream(self, messages, tools=None, **options):
        from sylvia_gm.provider._base import ProviderStreamChunk

        self.sent.append(options)
        yield ProviderStreamChunk(content="ready")

    async def close(self):
        pass


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "transport", ["chat_completions", "responses", "anthropic_messages", "codex_app_server"]
)
async def test_unknown_capacity_is_deferred_without_fabricated_model_maximum(transport):
    raw = RecordingProvider(transport)
    provider = ContextFittingProvider(raw)
    measurement = provider.admit_request([{"role": "user", "content": "Hello"}])
    assert measurement.admission_decision == "runtime_deferred"
    assert measurement.effective_context_capacity is None
    await provider.generate([{"role": "user", "content": "Hello"}])
    assert len(raw.sent) == 1
    if transport == "codex_app_server":
        assert measurement.output_token_reserve == 0
        assert "max_tokens" not in raw.sent[0] and "max_output_tokens" not in raw.sent[0]
    else:
        assert raw.sent[0]["max_tokens"] > 0
        assert measurement.output_token_reserve == raw.sent[0]["max_tokens"]


@pytest.mark.asyncio
async def test_managed_runtime_omits_explicit_numeric_reference_from_requests():
    raw = RecordingProvider("codex_app_server")
    raw.model_info.max_output_tokens = 8192
    provider = ContextFittingProvider(raw)
    await provider.generate([{"role": "user", "content": "Test"}], max_tokens=8192)
    assert "max_tokens" not in raw.sent[0]
    assert "max_output_tokens" not in raw.sent[0]
    assert raw.model_info.max_output_tokens == 8192
