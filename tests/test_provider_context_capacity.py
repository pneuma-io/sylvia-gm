"""Focused acceptance for honest provider context capacity and request-fit accounting."""

from __future__ import annotations

import json
import re
from collections.abc import AsyncIterator, Mapping
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

import httpx
import pytest

from sylvia_gm.agent._provider_resolver import ConfiguredProviderResolver
from sylvia_gm.config import provider_settings as settings_module
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.session import SessionRecord
from sylvia_gm.provider._base import ModelInfo, Provider, ProviderResponse, ProviderStreamChunk
from sylvia_gm.provider._context_capacity import resolve_context_capacity
from sylvia_gm.provider._context_fit import (
    GM_MODE_MINIMUM_CONTEXT_TOKENS,
    ContextFittingProvider,
    RequestTokenMeasurement,
)
from sylvia_gm.provider._exceptions import TokenLimitError


def _install_httpx_mock(monkeypatch: pytest.MonkeyPatch, handler: object) -> None:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    original_client = httpx.Client

    def client(**kwargs: object) -> httpx.Client:
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(settings_module.httpx, "Client", client)


def test_capacity_resolution_keeps_maximum_runtime_cap_and_effective_distinct() -> None:
    observed = resolve_context_capacity(
        declared_context_window=131_072,
        declared_context_source="provider_metadata",
        runtime_context_window=8_192,
        runtime_context_source="runtime_reported",
        observed_at="2026-08-20T00:00:00+00:00",
    )
    lower = resolve_context_capacity(
        declared_context_window=131_072,
        declared_context_source="provider_metadata",
        runtime_context_window=8_192,
        runtime_context_source="runtime_reported",
        configured_context_window=4_096,
    )
    oversized = resolve_context_capacity(
        declared_context_window=131_072,
        declared_context_source="provider_metadata",
        runtime_context_window=8_192,
        runtime_context_source="runtime_reported",
        configured_context_window=65_536,
    )
    maximum_only = resolve_context_capacity(
        declared_context_window=131_072,
        declared_context_source="model_catalog",
    )

    assert observed.effective_context_window == 8_192
    assert observed.effective_context_source == "runtime_reported"
    assert observed.declared_observed_at == "2026-08-20T00:00:00+00:00"
    assert observed.runtime_observed_at == "2026-08-20T00:00:00+00:00"
    assert lower.effective_context_window == 4_096
    assert lower.effective_context_source == "user_configured"
    assert oversized.effective_context_window == 8_192
    assert oversized.configured_runtime_mismatch is True
    assert maximum_only.effective_context_window is None
    assert maximum_only.effective_context_source == "unknown"


def test_lm_studio_native_probe_reports_model_maximum_and_loaded_runtime(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    visited: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        visited.append(request.url.path)
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "loaded-qwen"}]})
        if request.url.path == "/api/v1/models":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "type": "llm",
                            "key": "qwen-family",
                            "display_name": "Qwen",
                            "max_context_length": 131_072,
                            "loaded_instances": [
                                {"id": "loaded-qwen", "config": {"context_length": 8_192}}
                            ],
                            "capabilities": {"trained_for_tool_use": True},
                        }
                    ]
                },
            )
        return httpx.Response(404)

    _install_httpx_mock(monkeypatch, handle)
    result = settings_module.discover_provider_models(
        tmp_path / "config.yaml",
        provider_id="lm_studio",
        preset_id="lm_studio",
        base_url="http://127.0.0.1:1234/v1",
        credential="",
    )

    model = result["models"][0]
    assert visited == ["/v1/models", "/api/v1/models"]
    assert model["declared_context_window"] == 131_072
    assert model["declared_context_source"] == "provider_metadata"
    assert model["runtime_context_window"] == 8_192
    assert model["runtime_context_source"] == "runtime_reported"
    assert model["effective_context_window"] == 8_192
    assert model["supports_tools"] is True


def test_lm_studio_missing_context_stays_unknown_instead_of_becoming_32k(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "contextless-model"}]})
        if request.url.path == "/api/v1/models":
            return httpx.Response(200, json={"models": []})
        return httpx.Response(404)

    _install_httpx_mock(monkeypatch, handle)
    model = settings_module.discover_provider_models(
        tmp_path / "config.yaml",
        provider_id="lm_studio",
        preset_id="lm_studio",
        base_url="http://127.0.0.1:1234/v1",
        credential="",
    )["models"][0]

    assert model["declared_context_window"] is None
    assert model["runtime_context_window"] is None
    assert model["effective_context_window"] is None
    assert model["context_window"] is None
    assert 32_768 not in model.values()


def test_ollama_probe_distinguishes_running_context_from_model_maximum(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "qwen3:8b"}]})
        if request.url.path == "/api/ps":
            return httpx.Response(
                200,
                json={"models": [{"model": "qwen3:8b", "context_length": 16_384}]},
            )
        if request.url.path == "/api/show":
            assert json.loads(request.content) == {"model": "qwen3:8b"}
            return httpx.Response(
                200,
                json={"model_info": {"qwen3.context_length": 131_072}},
            )
        return httpx.Response(404)

    _install_httpx_mock(monkeypatch, handle)
    model = settings_module.discover_provider_models(
        tmp_path / "config.yaml",
        provider_id="ollama",
        preset_id="ollama",
        base_url="http://127.0.0.1:11434/v1",
        credential="",
    )["models"][0]

    assert model["declared_context_window"] == 131_072
    assert model["runtime_context_window"] == 16_384
    assert model["effective_context_window"] == 16_384


def test_reprobe_updates_changed_lm_studio_loaded_context(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    runtime = {"value": 8_192}

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "loaded-model"}]})
        if request.url.path == "/api/v1/models":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "type": "llm",
                            "key": "base-model",
                            "max_context_length": 131_072,
                            "loaded_instances": [
                                {
                                    "id": "loaded-model",
                                    "config": {"context_length": runtime["value"]},
                                }
                            ],
                        }
                    ]
                },
            )
        return httpx.Response(404)

    _install_httpx_mock(monkeypatch, handle)
    first = settings_module.discover_provider_models(
        tmp_path / "config.yaml",
        provider_id="lm_studio",
        preset_id="lm_studio",
        base_url="http://127.0.0.1:1234/v1",
        credential="",
    )["models"][0]
    runtime["value"] = 32_768
    second = settings_module.discover_provider_models(
        tmp_path / "config.yaml",
        provider_id="lm_studio",
        preset_id="lm_studio",
        base_url="http://127.0.0.1:1234/v1",
        credential="",
    )["models"][0]

    assert first["runtime_context_window"] == 8_192
    assert second["runtime_context_window"] == 32_768
    assert first["context_observed_at"] is not None
    assert second["context_observed_at"] is not None


def test_gm_resolution_replaces_a_stale_saved_local_runtime_before_admission(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    settings_module.save_provider_settings(
        config_path,
        provider_id="lm_studio",
        preset_id="lm_studio",
        display_name="LM Studio",
        base_url="http://127.0.0.1:1234/v1",
        credential="",
        model_id="loaded-e4b",
        model_name="E4B",
        context_window=262_144,
        declared_context_window=262_144,
        declared_context_source="provider_metadata",
        runtime_context_window=262_144,
        runtime_context_source="runtime_reported",
        context_observed_at="2026-08-21T00:00:00+00:00",
        max_output_tokens=8_192,
        transport="chat_completions",
        headers={},
    )

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/models":
            return httpx.Response(200, json={"data": [{"id": "loaded-e4b"}]})
        if request.url.path == "/api/v1/models":
            return httpx.Response(
                200,
                json={
                    "models": [
                        {
                            "type": "llm",
                            "key": "e4b-base",
                            "max_context_length": 262_144,
                            "loaded_instances": [
                                {
                                    "id": "loaded-e4b",
                                    "config": {"context_length": 8_192},
                                }
                            ],
                        }
                    ]
                },
            )
        return httpx.Response(404)

    _install_httpx_mock(monkeypatch, handle)
    resolver = ConfiguredProviderResolver(config_path)
    choice = resolver.model_choices()[0]
    assert choice["runtime_context_window"] == 262_144

    provider = resolver(
        SessionRecord(
            session_id="session-stale-local-runtime",
            campaign_id="campaign-stale-local-runtime",
            role=WriterRole.DM,
            provider_id=str(choice["provider_id"]),
            model_id=str(choice["model_id"]),
        )
    )

    assert provider.model_info.runtime_context_window == 8_192  # type: ignore[attr-defined]
    assert resolver.model_choices()[0]["runtime_context_window"] == 8_192
    fitted = ContextFittingProvider(
        provider,
        minimum_context_window=GM_MODE_MINIMUM_CONTEXT_TOKENS,
    )
    with pytest.raises(TokenLimitError, match="8,192"):
        fitted.admit_request([{"role": "user", "content": "Begin the campaign."}])


def test_generic_provider_works_with_explicit_cap_and_legacy_value_is_unverified(
    tmp_path: Path,
) -> None:
    explicit_path = tmp_path / "explicit.yaml"
    saved = settings_module.save_provider_settings(
        explicit_path,
        provider_id="custom",
        preset_id="custom",
        display_name="Local server",
        base_url="http://127.0.0.1:9000/v1",
        credential="",
        model_id="manual-model",
        model_name="Manual model",
        context_window=None,
        configured_context_window=65_536,
        max_output_tokens=4_096,
        transport="chat_completions",
        headers={},
    )
    projection = settings_module.provider_settings(explicit_path)["configured"][0]["models"][0]
    choice = ConfiguredProviderResolver(explicit_path).model_choices()[0]
    assert saved["provider_id"].startswith("custom_")
    assert projection["configured_context_window"] == 65_536
    assert projection["configured_context_observed_at"] is not None
    assert projection["effective_context_window"] == 65_536
    assert projection["effective_context_source"] == "user_configured"
    assert choice["effective_context_window"] == 65_536

    legacy_path = tmp_path / "legacy.yaml"
    legacy_path.write_text(
        "\n".join(
            (
                "providers:",
                "  legacy_local:",
                "    enabled: true",
                "    mode: byok",
                "    name: Legacy Local",
                "    base_url: http://127.0.0.1:9001/v1",
                "    api_key: ''",
                "    models:",
                "      - id: old-model",
                "        name: Old Model",
                "        context_window: 32768",
                "        max_output_tokens: 4096",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    legacy = settings_module.provider_settings(legacy_path)["configured"][0]["models"][0]
    assert legacy["effective_context_window"] == 32_768
    assert legacy["effective_context_source"] == "legacy_unverified"
    assert legacy["runtime_context_window"] is None
    assert legacy["configured_context_window"] is None


class _MeasuredProvider(Provider):
    def __init__(
        self,
        *,
        context_window: int = 8_192,
        runtime_context_window: int | None = None,
        provider_mode: str = "byok",
    ) -> None:
        super().__init__()
        runtime_context_window = (
            context_window if runtime_context_window is None else runtime_context_window
        )
        self.model_info = ModelInfo(  # type: ignore[assignment]
            id="test/measured",
            name="Measured",
            provider_name="Test",
            provider_key="test",
            provider_mode=provider_mode,
            context_window=context_window,
            runtime_context_window=runtime_context_window,
            runtime_context_source="runtime_reported",
            context_window_source="runtime_reported",
            max_output_tokens=1_024,
        )
        self.calls = 0

    def provider_request_representation(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        options: Mapping[str, object],
    ) -> dict[str, object]:
        return {
            "model": self.model_info.wire_model_id,
            "messages": messages,
            "tools": tools or [],
            "max_tokens": options.get("max_tokens"),
            "stream": options.get("stream"),
        }

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        self.calls += 1
        return ProviderResponse(content="ok")

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        self.calls += 1
        yield ProviderStreamChunk(content="ok", finish_reason="stop")

    async def close(self) -> None:
        return None


class _CountedProvider(_MeasuredProvider):
    def count_request_tokens(self, _request: object) -> int:
        return 123


def test_preflight_separates_output_reserve_and_never_calls_a_bound_an_exact_count() -> None:
    provider = ContextFittingProvider(_MeasuredProvider())
    measurement = provider.admit_request(
        [{"role": "user", "content": "hello"}],
        max_tokens=777,
    )

    assert measurement.precision == "conservative_bound"
    assert measurement.request_layer == "provider_client_request_representation"
    assert measurement.input_tokens is None
    assert measurement.input_token_bound == measurement.request_bytes
    assert measurement.output_token_reserve == 777
    assert measurement.required_token_bound == measurement.input_token_bound + 777
    assert measurement.admission_decision == "admitted"


def test_large_output_limit_is_reserved_without_a_hidden_smaller_cap() -> None:
    base = _MeasuredProvider(context_window=524_288)
    base.model_info.max_output_tokens = 131_072
    measurement = ContextFittingProvider(base).admit_request(
        [{"role": "user", "content": "Continue the encounter."}], max_tokens=131_072
    )

    assert measurement.output_token_reserve == 131_072
    assert measurement.required_token_bound == measurement.input_token_bound + 131_072
    assert measurement.admission_decision == "admitted"
    assert base.calls == 0


def test_authoritative_counter_remains_distinct_from_bound_estimate_and_unavailable() -> None:
    authoritative = ContextFittingProvider(_CountedProvider()).admit_request(
        [{"role": "user", "content": "hello"}],
        max_tokens=500,
    )
    assert authoritative.precision == "authoritative_count"
    assert authoritative.input_tokens == 123
    assert authoritative.input_token_bound is None
    assert authoritative.required_tokens == 623

    for precision in ("conservative_bound", "estimate", "unavailable"):
        projection = RequestTokenMeasurement(
            precision=precision,  # type: ignore[arg-type]
            source=f"test_{precision}",
            request_layer="neutral_application_request",
            request_bytes=1,
            request_sha256="0" * 64,
            input_tokens=None,
            input_token_bound=1 if precision == "conservative_bound" else None,
            output_token_reserve=1,
            required_tokens=None,
            required_token_bound=2 if precision == "conservative_bound" else None,
            effective_context_capacity=8_192,
            capacity_source="runtime_reported",
            admission_decision="admitted" if precision == "conservative_bound" else "rejected",
            provider_transformation_overhead="test",
        ).projection()
        assert projection["precision"] == precision
        assert projection["input_tokens"] is None


def test_context_rejection_occurs_before_send() -> None:
    base = _MeasuredProvider(context_window=512)
    provider = ContextFittingProvider(base)

    with pytest.raises(TokenLimitError, match="token upper bound"):
        provider.admit_request(
            [{"role": "user", "content": "x" * 1_000}],
            max_tokens=128,
        )
    assert base.calls == 0


def test_local_gm_requires_loaded_runtime_and_effective_context_of_131k() -> None:
    below_floor = _MeasuredProvider(
        context_window=130_999,
        runtime_context_window=130_999,
        provider_mode="local",
    )
    provider = ContextFittingProvider(
        below_floor,
        minimum_context_window=GM_MODE_MINIMUM_CONTEXT_TOKENS,
    )

    with pytest.raises(TokenLimitError) as error:
        provider.admit_request([{"role": "user", "content": "short request"}])

    message = str(error.value)
    assert "Measured" in message
    assert "130,999" in message
    assert "131,000" in message
    assert "Find models" in message
    assert below_floor.calls == 0


def test_local_gm_does_not_treat_configured_capacity_as_loaded_runtime() -> None:
    base = _MeasuredProvider(
        context_window=GM_MODE_MINIMUM_CONTEXT_TOKENS,
        runtime_context_window=0,
        provider_mode="local",
    )
    base.model_info.runtime_context_window = None
    base.model_info.runtime_context_source = "unknown"
    base.model_info.configured_context_window = GM_MODE_MINIMUM_CONTEXT_TOKENS
    provider = ContextFittingProvider(
        base,
        minimum_context_window=GM_MODE_MINIMUM_CONTEXT_TOKENS,
    )

    with pytest.raises(TokenLimitError, match="loaded runtime is unknown"):
        provider.admit_request([{"role": "user", "content": "short request"}])
    assert base.calls == 0


def test_local_gm_floor_admits_a_loaded_runtime_at_or_above_131k() -> None:
    provider = ContextFittingProvider(
        _MeasuredProvider(
            context_window=GM_MODE_MINIMUM_CONTEXT_TOKENS,
            runtime_context_window=131_072,
            provider_mode="local",
        ),
        minimum_context_window=GM_MODE_MINIMUM_CONTEXT_TOKENS,
    )

    measurement = provider.admit_request([{"role": "user", "content": "short request"}])

    assert measurement.admission_decision == "admitted"


def test_frontend_contains_no_32k_capacity_substitution() -> None:
    root = Path(__file__).parents[1]
    javascript = (root / "src-tauri/frontend/app.js").read_text(encoding="utf-8")
    html = (root / "src-tauri/frontend/app.html").read_text(encoding="utf-8")
    assert "32768" not in javascript
    assert 'value="32768"' not in html
    assert "unverified limit" in javascript
    assert "model.effective_context_window ?? model.context_window" not in javascript
    assert "function formatAuditTime(value)" in javascript
    for identifier in (
        "provider-declared-context",
        "provider-runtime-context",
        "provider-context-window",
        "provider-effective-context",
        "provider-output-limit",
        "provider-capacity-warning",
    ):
        assert f'id="{identifier}"' in html


def test_every_live_builder_and_gm_path_uses_one_context_fit_wrapper() -> None:
    root = Path(__file__).parents[1]
    source = (root / "src/sylvia_gm/interfaces/owner_process.py").read_text(
        encoding="utf-8"
    )
    # One constructor is centralized in the helper; guided Start preflight, ordinary Builder/GM
    # input, first-GM opening, and narration Retry all call that helper.
    assert source.count("ContextFittingProvider(") == 1
    assert source.count("_context_fitted_provider(") == 5


@pytest.mark.parametrize("runtime", [None, 130_999])
def test_local_runtime_recovery_names_the_actual_discovery_action(runtime: int | None) -> None:
    root = Path(__file__).resolve().parents[1]

    class DiscoveryControl(HTMLParser):
        def __init__(self) -> None:
            super().__init__()
            self.active = False
            self.text: list[str] = []

        def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
            if tag == "button" and dict(attrs).get("id") == "discover-provider-models-btn":
                self.active = True

        def handle_endtag(self, tag: str) -> None:
            if tag == "button":
                self.active = False

        def handle_data(self, data: str) -> None:
            if self.active:
                self.text.append(data)

    control = DiscoveryControl()
    control.feed((root / "src-tauri/frontend/app.html").read_text(encoding="utf-8"))
    label = " ".join("".join(control.text).split())
    assert label == "Find models"
    script = (root / "src-tauri/frontend/app.js").read_text(encoding="utf-8")
    assert re.search(
        r"\$\('discover-provider-models-btn'\)\.addEventListener\('click',\s*\(\)\s*=>\s*\{"
        r"\s*void discoverSelectedProviderModels\(\);", script,
    )
    assert "Add another model" in script
    base = _MeasuredProvider(
        context_window=262_144, runtime_context_window=runtime or 0, provider_mode="local",
    )
    base.model_info.runtime_context_window = runtime
    provider = ContextFittingProvider(base, minimum_context_window=GM_MODE_MINIMUM_CONTEXT_TOKENS)
    with pytest.raises(TokenLimitError) as error:
        provider.admit_request([{"role": "user", "content": "Resume the saved request"}])
    message = str(error.value)
    assert label in message
    assert "Settings > Models" in message
    assert "Connection" in message and "Add another model" in message
    assert "Add model" in message and "Manual Retry" in message
    assert "Refresh models & context" not in message
    assert "131,000" in message and "The request was not sent." in message
    assert base.calls == 0
