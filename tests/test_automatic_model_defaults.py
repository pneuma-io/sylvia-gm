"""Exact provider defaults assist setup without discovering access or running inference."""

import httpx
import pytest

from sylvia_gm.agent._provider_resolver import ConfiguredProviderResolver
from sylvia_gm.config import provider_settings as settings
from sylvia_gm.config._resolver import ConfigError
from sylvia_gm.provider._base import ModelInfo
from sylvia_gm.provider._reasoning import configure_reasoning, mode_reasoning_default
from tests.test_alpha_09_provider_management import _install_httpx_mock


def test_sparse_openai_discovery_uses_exact_documented_defaults(monkeypatch, tmp_path):
    calls = []

    def handle(request):
        calls.append((request.method, str(request.url)))
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "gpt-6-sol"},
                    {"id": "gpt-6-unannounced"},
                ]
            },
        )

    _install_httpx_mock(monkeypatch, handle)
    result = settings.discover_provider_models(
        tmp_path / "config.yaml",
        provider_id="openai",
        base_url="https://api.openai.com/v1",
        credential="synthetic-key",
    )
    sol, unknown = result["models"]
    assert sol["id"] == "gpt-6-sol"
    assert sol["effective_context_window"] == 1_050_000
    assert sol["effective_context_source"] == "model_catalog"
    assert sol["max_output_tokens"] == 128_000
    assert sol["transport"] == "responses"
    assert sol["documented_source"].endswith("/gpt-6-sol")
    assert sol["documented_at"] == "2026-09-23"
    assert unknown["effective_context_window"] is None
    assert unknown["max_output_tokens"] is None
    assert calls == [("GET", "https://api.openai.com/v1/models")]


@pytest.mark.parametrize("preset", ["custom", "ollama", "openrouter", "chatgpt_subscription"])
def test_openai_defaults_do_not_cross_service_boundaries(preset):
    model = settings._discovery_model({"id": "gpt-6-sol"}, preset_id=preset)
    assert model["context_window"] is None
    assert model["max_output_tokens"] is None


def test_richer_provider_metadata_and_lower_runtime_limits_win():
    model = settings._discovery_model(
        {
            "id": "gpt-6-sol",
            "context_length": 400_000,
            "top_provider": {"max_completion_tokens": 64_000},
        },
        preset_id="openai",
    )
    assert model["runtime_context_window"] == 400_000
    assert model["runtime_context_source"] == "provider_metadata"
    assert model["max_output_tokens"] == 64_000
    router = settings._discovery_model(
        {
            "id": "synthetic/model",
            "top_provider": {
                "context_length": 96_000,
                "max_completion_tokens": 8_000,
            },
        },
        preset_id="openrouter",
    )
    assert router["effective_context_window"] == 96_000
    assert router["max_output_tokens"] == 8_000


def test_automatic_save_override_reopen_and_restore(tmp_path):
    path = tmp_path / "config.yaml"
    command = {
        "provider_id": "openai",
        "base_url": "https://api.openai.com/v1",
        "credential": "synthetic-key",
        "model_id": "gpt-6-sol",
        "model_name": "Sol",
        "context_window": None,
        "max_output_tokens": None,
    }
    settings.save_provider_settings(path, **command)

    def saved():
        projection = settings.provider_settings(path)
        assert projection["default_model"] is None
        return projection["configured"][0]["models"][0]

    automatic = saved()
    assert automatic["max_output_tokens"] == 128_000
    assert automatic["configured_output_tokens"] is None
    assert automatic["configured_context_window"] is None
    assert automatic["effective_context_window"] == 1_050_000
    assert automatic["transport"] == "responses"
    choice = ConfiguredProviderResolver(path).model_choices()[0]
    assert choice["model_id"] == "openai/gpt-6-sol"
    assert choice["supported_reasoning_efforts"] == [
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    ]
    settings.save_provider_settings(
        path,
        **{
            **command,
            "max_output_tokens": 200_000,
            "configured_context_window": 800_000,
            "runtime_context_window": 400_000,
            "runtime_context_source": "runtime_reported",
        },
    )
    capped = saved()
    assert capped["configured_output_tokens"] == 200_000
    assert capped["max_output_tokens"] == 128_000
    assert capped["configured_context_window"] == 800_000
    assert capped["effective_context_window"] == 400_000
    settings.save_provider_settings(
        path,
        **{
            **command,
            "runtime_context_window": 400_000,
            "runtime_context_source": "runtime_reported",
        },
    )
    restored = saved()
    assert restored["configured_context_window"] is None
    assert restored["configured_output_tokens"] is None
    assert restored["effective_context_window"] == 400_000


def test_gpt6_tools_use_responses_and_exact_effort_support(tmp_path):
    with pytest.raises(ConfigError, match="Choose Responses"):
        settings.save_provider_settings(
            tmp_path / "config.yaml",
            provider_id="openai",
            base_url="https://api.openai.com/v1",
            credential="synthetic-key",
            model_id="gpt-6-sol",
            model_name="Sol",
            context_window=None,
            max_output_tokens=None,
            transport="chat_completions",
        )
    model = ModelInfo(
        id="openai/gpt-6-sol",
        name="Sol",
        provider_name="OpenAI",
        provider_key="openai",
        provider_mode="byok",
        transport="responses",
    )
    configured = configure_reasoning(model, "https://api.openai.com/v1")
    assert configured.supported_reasoning_efforts == (
        "none",
        "low",
        "medium",
        "high",
        "xhigh",
        "max",
    )
    assert mode_reasoning_default(configured, "dm") == "high"
    assert mode_reasoning_default(configured, "builder") == "xhigh"
    assert (
        configure_reasoning(model, "https://proxy.example/v1").supported_reasoning_efforts is None
    )


@pytest.mark.parametrize(("preset", "endpoint", "model_id", "context", "output"), [
    ("groq", "https://api.groq.com/openai/v1", "openai/gpt-oss-120b", 131_072, 65_536),
    ("deepseek", "https://api.deepseek.com", "deepseek-flash", 1_000_000, 393_216),
    ("together", "https://api.together.ai/v1", "author/synthetic-chat", 64_000, None),
])
def test_named_service_discovery_and_save_use_the_existing_transport(
    monkeypatch, tmp_path, preset, endpoint, model_id, context, output,
):
    calls = []

    def handle(request):
        calls.append((request.method, str(request.url)))
        assert request.headers["Authorization"] == "Bearer synthetic-key"
        rows = [{"id": model_id}]
        if preset == "together":
            rows = [
                {"id": model_id, "display_name": "Synthetic chat", "type": "chat",
                 "context_length": context},
                {"id": "author/image", "type": "image"},
            ]
        return httpx.Response(200, json=rows if preset == "together" else {"data": rows})

    _install_httpx_mock(monkeypatch, handle)
    path = tmp_path / "config.yaml"
    result = settings.discover_provider_models(
        path, provider_id=preset, base_url=None, credential="synthetic-key",
    )
    assert len(result["models"]) == 1
    model = result["models"][0]
    assert model["effective_context_window"] == context
    assert model["max_output_tokens"] == output
    assert model["transport"] == "chat_completions"
    if preset == "together":
        assert model["name"] == "Synthetic chat"
    settings.save_provider_settings(
        path, provider_id=preset, base_url=endpoint, credential="synthetic-key",
        model_id=model_id, model_name=model["name"], context_window=None,
        declared_context_window=model["declared_context_window"],
        declared_context_source=model["declared_context_source"],
        runtime_context_window=model["runtime_context_window"],
        runtime_context_source=model["runtime_context_source"],
        max_output_tokens=None if output else 8192,
    )
    projection = settings.provider_settings(path)
    assert projection["default_model"] is None
    saved = projection["configured"][0]
    assert saved["base_url"] == endpoint
    assert saved["models"][0]["max_output_tokens"] == (output or 8192)
    assert saved["models"][0]["configured_output_tokens"] == (None if output else 8192)
    choice = ConfiguredProviderResolver(path).model_choices()[0]
    assert choice["model_id"] == f"{preset}/{model_id}"
    if preset == "groq":
        assert choice["supported_reasoning_efforts"] == ["low", "medium", "high"]
    elif preset == "deepseek":
        assert choice["supported_reasoning_efforts"] == ["none", "low", "high", "max"]
    assert calls == [("GET", endpoint + "/models")]


@pytest.mark.parametrize("plan_limit", [32768, None, "unavailable", True])
def test_featherless_plan_caps_discovery_without_inventing_a_limit(
    monkeypatch, tmp_path, plan_limit,
):
    calls = []

    def handle(request):
        calls.append(request.url.path)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [
                {"id": "author/allowed", "context_length": 131_072,
                 "max_completion_tokens": 8192, "available_on_current_plan": True},
                {"id": "author/unavailable", "context_length": 131_072,
                 "available_on_current_plan": False},
            ]})
        return httpx.Response(503) if plan_limit == "unavailable" else httpx.Response(
            200, json={"max_context_length": plan_limit},
        )

    _install_httpx_mock(monkeypatch, handle)
    result = settings.discover_provider_models(
        tmp_path / "config.yaml", provider_id="featherless", base_url=None,
        credential="synthetic-key",
    )
    assert len(result["models"]) == 1
    model = result["models"][0]
    assert model["declared_context_window"] == 131_072
    expected = None if plan_limit == "unavailable" or plan_limit is True else plan_limit or 131_072
    assert model["effective_context_window"] == expected
    assert bool(result["notice"]) == (expected is None)
    assert model["declared_max_output_tokens"] == 8192
    assert calls == ["/v1/models", "/v1/plan"]


def test_setup_pages_use_fixed_preset_urls_and_handle_no_browser(monkeypatch, tmp_path):
    opened = []
    monkeypatch.setattr(settings.webbrowser, "open", lambda url, new: opened.append(url) or True)
    presets = settings.provider_settings(tmp_path / "config.yaml")["presets"]
    for preset in presets:
        assert preset["setup_hint"]
        if preset["provider_id"] != "custom":
            result = settings.open_provider_setup(preset["provider_id"])
            assert result["opened"] is True
            assert opened[-1] == preset["setup_url"]
    for invalid in ("custom", "https://untrusted.example", None):
        with pytest.raises(ConfigError):
            settings.open_provider_setup(invalid)
    monkeypatch.setattr(settings.webbrowser, "open", lambda *args, **kwargs: False)
    assert settings.open_provider_setup("groq") == {
        "opened": False, "url": "https://console.groq.com/keys",
    }


def test_supported_parameter_list_does_not_imply_streaming_is_unavailable():
    model = settings._discovery_model(
        {"id": "synthetic/model", "supported_parameters": ["tools", "max_tokens"]},
        preset_id="openrouter",
    )
    assert model["supports_tools"] is True
    assert model["supports_streaming"] is None
