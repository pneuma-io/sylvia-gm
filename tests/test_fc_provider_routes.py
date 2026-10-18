"""Documented provider routes, native catalogs, and honest capability projections."""

from pathlib import Path

import httpx
import pytest

from sylvia_gm.config import provider_settings as settings
from sylvia_gm.config._resolver import ConfigError


@pytest.fixture
def mock_http(monkeypatch):
    """Intercept selected provider requests without credentials or network access."""
    original = httpx.Client

    def install(handler):
        monkeypatch.setattr(
            settings.httpx, "Client",
            lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs),
        )

    return install


def discover(tmp_path, provider):
    return settings.discover_provider_models(
        tmp_path / "config.yaml", provider_id=provider, preset_id=provider,
        base_url="", credential="synthetic-secret",
    )


def test_anthropic_catalog_uses_native_auth_paging_and_limits(tmp_path, mock_http):
    requests = []

    def handle(request):
        requests.append(request)
        assert str(request.url).startswith("https://api.anthropic.com/v1/models?")
        assert request.headers["x-api-key"] == "synthetic-secret"
        assert request.headers["anthropic-version"] == "2023-06-01"
        assert "authorization" not in request.headers
        if request.url.params.get("after_id") is None:
            return httpx.Response(200, json={
                "data": [{"id": "claude-first", "display_name": "First",
                          "max_input_tokens": 200_000, "max_tokens": 64_000}],
                "has_more": True, "last_id": "claude-first",
            })
        assert request.url.params["after_id"] == "claude-first"
        return httpx.Response(200, json={
            "data": [{"id": "claude-second", "max_input_tokens": None, "max_tokens": None}],
            "has_more": False,
        })

    mock_http(handle)
    result = discover(tmp_path, "anthropic")
    assert len(requests) == 2
    models = {row["id"]: row for row in result["models"]}
    assert models["claude-first"]["effective_context_window"] == 200_000
    assert models["claude-first"]["max_output_tokens"] == 64_000
    assert models["claude-first"]["transport"] == "anthropic_messages"
    assert models["claude-second"]["effective_context_window"] is None
    assert models["claude-second"]["max_output_tokens"] is None


def test_anthropic_repeated_catalog_cursor_fails_without_looping(tmp_path, mock_http):
    calls = []

    def handle(request):
        calls.append(request)
        return httpx.Response(200, json={
            "data": [{"id": "same"}], "has_more": True, "last_id": "same",
        })

    mock_http(handle)
    with pytest.raises(ConfigError, match="pagination"):
        discover(tmp_path, "anthropic")
    assert len(calls) == 2


def test_mistral_filters_nonchat_and_keeps_explicit_tool_capability(tmp_path, mock_http):
    mock_http(lambda _: httpx.Response(200, json={"data": [
        {"id": "chat", "max_context_length": 262_144,
         "capabilities": {"completion_chat": True, "function_calling": True}},
        {"id": "prose", "max_context_length": 32_768,
         "capabilities": {"completion_chat": True, "function_calling": False}},
        {"id": "embedding", "capabilities": {"completion_chat": False}},
    ]}))
    models = {row["id"]: row for row in discover(tmp_path, "mistral")["models"]}
    assert set(models) == {"chat", "prose"}
    assert models["chat"]["effective_context_window"] == 262_144
    assert models["chat"]["supports_tools"] is True
    assert models["prose"]["supports_tools"] is False
    assert models["chat"]["max_output_tokens"] is None


@pytest.mark.parametrize(("provider", "endpoint", "transport"), [
    ("gemini", "https://generativelanguage.googleapis.com/v1beta/openai", "chat_completions"),
    ("xai", "https://api.x.ai/v1", "responses"),
    ("minimax", "https://api.minimax.io/anthropic/v1", "anthropic_messages"),
    ("moonshot", "https://api.moonshot.ai/v1", "chat_completions"),
    ("zai", "https://api.z.ai/api/paas/v4", "chat_completions"),
    ("alibaba", "https://dashscope-intl.aliyuncs.com/compatible-mode/v1", "chat_completions"),
    ("fireworks", "https://api.fireworks.ai/inference/v1", "chat_completions"),
    ("cerebras", "https://api.cerebras.ai/v1", "chat_completions"),
])
def test_new_presets_reach_documented_catalog_route_without_inventing_limits(
    tmp_path: Path, mock_http, provider, endpoint, transport,
):
    def handle(request):
        assert str(request.url) == endpoint + "/models"
        if transport == "anthropic_messages":
            assert request.headers["x-api-key"] == "synthetic-secret"
        else:
            assert request.headers["authorization"] == "Bearer synthetic-secret"
        return httpx.Response(200, json={"data": [{"id": "synthetic-chat"}]})

    mock_http(handle)
    model, = discover(tmp_path, provider)["models"]
    assert model["transport"] == transport
    assert model["effective_context_window"] is None
    assert model["max_output_tokens"] is None


def test_missing_discovery_explains_manual_entry_without_claiming_available_models(
    tmp_path, mock_http,
):
    mock_http(lambda _: httpx.Response(404, json={"error": "private-service-detail"}))
    with pytest.raises(ConfigError, match="Enter its documented model identifier") as error:
        discover(tmp_path, "minimax")
    assert "private-service-detail" not in str(error.value)


def test_opencode_exact_route_defaults_do_not_transfer_to_zen(tmp_path, mock_http):
    mock_http(lambda _: httpx.Response(200, json={"data": [
        {"id": "minimax-m3"}, {"id": "gpt-6-luna"},
    ]}))
    models = {row["id"]: row for row in discover(tmp_path, "opencode_go")["models"]}
    assert models["minimax-m3"]["effective_context_window"] == 1_000_000
    assert models["minimax-m3"]["max_output_tokens"] == 131_072
    assert models["gpt-6-luna"]["transport"] == "responses"
    zen = {row["id"]: row for row in discover(tmp_path, "opencode_zen")["models"]}
    assert zen["minimax-m3"]["effective_context_window"] is None

@pytest.mark.parametrize("failure", ["prose", "stream_error", "redirect"])
def test_probe_does_not_claim_tool_support_from_echoed_name_or_failed_response(
    tmp_path, mock_http, failure,
):
    import json

    def handle(request):
        if request.method == "GET":
            return httpx.Response(404)
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "https://synthetic.invalid"})
        events = [
            {"choices": [{"delta": {"content": "sylvia_compatibility_probe"}}]},
        ] if failure == "prose" else [
            {"choices": [{"delta": {"tool_calls": [{"function": {
                "name": "sylvia_compatibility_probe", "arguments": "{}",
            }}]}}]},
            {"type": "error", "error": {"message": "private service detail"}},
        ]
        return httpx.Response(
            200, headers={"content-type": "text/event-stream"},
            text="".join("data: " + json.dumps(event) + "\n\n" for event in events),
        )

    mock_http(handle)
    report = settings.validate_provider_model(
        tmp_path / "config.yaml", provider_id="cerebras", preset_id="cerebras",
        base_url="", credential="synthetic-secret", model_id="synthetic",
        transport="chat_completions",
    )
    assert report["agent_ready"] is False
    assert "private service detail" not in repr(report)
    if failure == "redirect":
        assert report["authentication"] == "unverified"
