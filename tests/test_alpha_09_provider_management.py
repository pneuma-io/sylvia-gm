"""Focused provider-management acceptance for the shipped owner configuration seam."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
import yaml

from sylvia_gm import __version__
from sylvia_gm.agent._provider_resolver import ConfiguredProviderResolver
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.config import loader as loader_module
from sylvia_gm.config import provider_settings as settings_module
from sylvia_gm.domain.authority import PackageLock, WriterRole
from sylvia_gm.domain.session import SessionRecord
from sylvia_gm.provider._anthropic import (
    AnthropicMessagesTransport,
    AnthropicProvider,
    _AnthropicStreamContent,
)
from sylvia_gm.provider._exceptions import InvalidResponseError
from sylvia_gm.provider._openai_compat import ResponsesTransport
from sylvia_gm.provider._request_metadata import SessionBoundRequestMetadataProvider

ROOT = Path(__file__).parents[1]


def test_anthropic_m3_thinking_defaults_and_explicit_override() -> None:
    transport = AnthropicMessagesTransport()
    messages = [{"role": "user", "content": "Synthetic protocol test"}]
    assert "thinking" not in transport.format_request("minimax-m3", messages, None, True)
    assert transport.format_request(
        "minimax-m3", messages, None, True, thinking={"type": "adaptive"}
    )["thinking"] == {"type": "adaptive"}
    assert "thinking" not in transport.format_request("claude-test", messages, None, True)
    assert transport.format_request(
        "claude-test", messages, None, True, thinking={"type": "enabled", "budget_tokens": 1024}
    )["thinking"] == {"type": "enabled", "budget_tokens": 1024}


def test_anthropic_native_assistant_content_round_trips_without_reordering() -> None:
    blocks = [
        {"type": "thinking", "thinking": "Synthetic thought", "signature": "opaque-signature"},
        {"type": "text", "text": "Checking."},
        {"type": "tool_use", "id": "call-1", "name": "read_counter", "input": {}},
    ]
    request = AnthropicMessagesTransport().format_request(
        "minimax-m3",
        [
            {"role": "assistant", "content": None, "anthropic_content": blocks},
            {"role": "tool", "tool_call_id": "call-1", "content": '{"counter":35}'},
        ],
        None,
        True,
    )
    assert request["messages"][0] == {"role": "assistant", "content": blocks}
    assert request["messages"][1]["content"][0]["tool_use_id"] == "call-1"


def test_anthropic_thinking_delta_and_terminal_stop_are_not_discarded_or_overwritten() -> None:
    transport = AnthropicMessagesTransport()
    chunk = transport.parse_stream_chunk(
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="thinking_delta", thinking="Synthetic thought"),
        )
    )
    assert chunk is not None and chunk.reasoning_content == "Synthetic thought"
    assert transport.parse_stream_chunk(SimpleNamespace(type="message_stop")) is None


def test_anthropic_content_block_start_emits_initial_text_and_thinking() -> None:
    transport = AnthropicMessagesTransport()

    text_chunk = transport.parse_stream_chunk(
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block=SimpleNamespace(type="text", text="Initial text"),
        )
    )
    thinking_chunk = transport.parse_stream_chunk(
        SimpleNamespace(
            type="content_block_start",
            index=1,
            content_block=SimpleNamespace(type="thinking", thinking="Initial thought"),
        )
    )

    assert text_chunk is not None and text_chunk.content == "Initial text"
    assert thinking_chunk is not None and thinking_chunk.reasoning_content == "Initial thought"


def test_anthropic_stream_content_preserves_redacted_thinking_block() -> None:
    content = _AnthropicStreamContent()
    redacted = {"type": "redacted_thinking", "data": "opaque-redacted-data"}

    assert (
        content.accept(SimpleNamespace(type="content_block_start", index=0, content_block=redacted))
        is None
    )
    assert content.accept(SimpleNamespace(type="message_stop")) == [redacted]


def test_anthropic_stream_content_rejects_malformed_tool_arguments() -> None:
    content = _AnthropicStreamContent()
    content.accept(
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block={"type": "tool_use", "id": "tool-1", "name": "read_counter"},
        )
    )
    content.accept(
        SimpleNamespace(
            type="content_block_delta",
            index=0,
            delta=SimpleNamespace(type="input_json_delta", partial_json="{"),
        )
    )

    with pytest.raises(InvalidResponseError, match="invalid streamed tool input"):
        content.accept(SimpleNamespace(type="message_stop"))


def test_anthropic_stream_content_rejects_truncated_native_response() -> None:
    content = _AnthropicStreamContent()
    content.accept(
        SimpleNamespace(
            type="content_block_start",
            index=0,
            content_block={"type": "thinking", "thinking": "Synthetic thought"},
        )
    )

    with pytest.raises(InvalidResponseError, match="before the terminal message_stop"):
        content.require_complete()


def test_default_config_path_uses_pyinstaller_bundle_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)

    assert loader_module._default_config_path() == tmp_path / "config" / "config.yaml"


def test_owner_sidecar_bundles_general_builder_skill_documents(tmp_path: Path, monkeypatch) -> None:
    import importlib.util
    import os
    import shutil

    builder_path = ROOT / "src-tauri/scripts/build_owner_sidecar.py"
    spec = importlib.util.spec_from_file_location("provider_skill_staging_builder", builder_path)
    assert spec is not None and spec.loader is not None
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)

    # Use the shipped inventory and source bytes without writing into the checkout.
    repository = tmp_path / "repository"
    inventory_path = Path("src-tauri/sidecar-data-files.json")
    inventory = json.loads((ROOT / inventory_path).read_text(encoding="utf-8"))
    for relative in [inventory_path, *map(Path, inventory["files"])]:
        target = repository / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    owned_root = repository / ".owner-build"
    owned_root.mkdir()
    staging_root = owned_root / "skills-proof"
    arguments = builder.sidecar_data_arguments(repository, staging_root)
    assert arguments[::2] == ["--add-data"] * 4
    destinations = dict(value.rsplit(os.pathsep, 1)[::-1] for value in arguments[1::2])
    assert set(destinations) == {
        "rulesets/srd-lite",
        "rulesets/riichi-mahjong",
        "campaign-settings/severed-lands",
        "sylvia_gm/agent/skills",
    }
    staged_skills = Path(destinations["sylvia_gm/agent/skills"])
    assert staged_skills == staging_root / "sylvia_gm/agent/skills"
    expected = {
        "build_ruleset/SKILL.md",
        "build_campaign_setting/SKILL.md",
        "build_campaign_plan/SKILL.md",
        "build_campaign_plan/references/combat-plan.json",
        "build_campaign_plan/references/combat-plan.md",
        "build_campaign_plan/references/multi-act-plan.json",
        "build_campaign_plan/references/multi-act-plan.md",
        "build_campaign_plan/references/variations.md",
        "build_campaign_plan/references/worked-examples.md",
    }
    assert {
        item.relative_to(staged_skills).as_posix()
        for item in staged_skills.rglob("*")
        if item.is_file()
    } == expected
    for relative in expected:
        source = ROOT / "src/sylvia_gm/agent/skills" / relative
        assert source.is_file()
        assert (staged_skills / relative).read_bytes() == source.read_bytes()

    from sylvia_gm.agent import _skills
    from sylvia_gm.agent._builder_session import _builder_skill_loader_tool

    monkeypatch.setattr(_skills, "__file__", str(staged_skills.parent / "_skills.py"))
    tool = _builder_skill_loader_tool()
    catalog = tool.handler({"name": "BUILD CAMPAIGN PLAN"}, None)
    assert catalog["success"] is True
    for reference in catalog["references"]:
        loaded = tool.handler({"name": "BUILD CAMPAIGN PLAN", "reference": reference}, None)
        assert loaded["success"] is True
        assert loaded["instructions"] == (
            staged_skills / "build_campaign_plan" / reference
        ).read_text(encoding="utf-8")
    assert "definition.procedural_definition.executable" in tool.handler(
        {"name": "BUILD RULESET"}, None
    )["instructions"]


def _install_httpx_mock(monkeypatch: object, handler: object) -> None:
    transport = httpx.MockTransport(handler)  # type: ignore[arg-type]
    original_client = httpx.Client

    def client(**kwargs: object) -> httpx.Client:
        kwargs["transport"] = transport
        return original_client(**kwargs)

    monkeypatch.setattr(settings_module.httpx, "Client", client)  # type: ignore[attr-defined]


def test_provider_presets_cover_hosted_local_and_custom_alpha_scope(tmp_path: Path) -> None:
    projection = settings_module.provider_settings(tmp_path / "config.yaml")
    presets = {item["provider_id"]: item for item in projection["presets"]}

    assert {
        "openai",
        "opencode_go",
        "opencode_zen",
        "openrouter",
        "featherless",
        "lm_studio",
        "ollama",
        "llama_cpp",
        "vllm",
        "localai",
        "custom",
    }.issubset(presets)
    assert presets["ollama"]["base_url"] == "http://127.0.0.1:11434/v1"
    assert presets["custom"]["endpoint_editable"] is True
    assert projection["configured"] == []
    assert projection["default_model"] is None


def test_opencode_discovery_preserves_each_models_documented_transport(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.url.path.endswith("/models")
        return httpx.Response(
            200,
            json={
                "data": [
                    {"id": "gpt-5.6-luna"},
                    {"id": "qwen3.7-plus"},
                    {"id": "minimax-m3"},
                ]
            },
        )

    _install_httpx_mock(monkeypatch, handle)
    go = settings_module.discover_provider_models(
        tmp_path / "config.yaml",
        provider_id="opencode_go",
        preset_id="opencode_go",
        base_url="https://opencode.ai/zen/go/v1",
        credential="key",
    )
    zen = settings_module.discover_provider_models(
        tmp_path / "config.yaml",
        provider_id="opencode_zen",
        preset_id="opencode_zen",
        base_url="https://opencode.ai/zen/v1",
        credential="key",
    )

    assert {row["id"]: row["transport"] for row in go["models"]} == {
        "gpt-5.6-luna": "responses",
        "minimax-m3": "anthropic_messages",
        "qwen3.7-plus": "anthropic_messages",
    }
    assert {row["id"]: row["transport"] for row in zen["models"]} == {
        "gpt-5.6-luna": "responses",
        "minimax-m3": "chat_completions",
        "qwen3.7-plus": "anthropic_messages",
    }
    assert (
        next(row for row in go["models"] if row["id"] == "minimax-m3")["max_output_tokens"]
        == 131_072
    )
    assert all(row["max_output_tokens"] is None for row in zen["models"])


def test_provider_save_preserves_multiple_models_and_keeps_secrets_owner_only(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    first = settings_module.save_provider_settings(
        config_path,
        provider_id="openrouter",
        preset_id="openrouter",
        display_name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        credential="secret-one",
        model_id="vendor/model-a",
        model_name="Model A",
        context_window=32_768,
        max_output_tokens=4_096,
        transport="chat_completions",
        headers={"HTTP-Referer": "https://sylviagm.com", "X-Title": "Sylvia GM"},
    )
    second = settings_module.save_provider_settings(
        config_path,
        provider_id="openrouter",
        preset_id="openrouter",
        display_name="OpenRouter",
        base_url="https://openrouter.ai/api/v1",
        credential="",
        model_id="vendor/model-b",
        model_name="Model B",
        context_window=65_536,
        max_output_tokens=8_192,
        transport="responses",
        headers={},
    )

    assert first["provider_id"] == second["provider_id"] == "openrouter"
    projection = settings_module.provider_settings(config_path)
    configured = projection["configured"][0]
    assert [model["id"] for model in configured["models"]] == [
        "vendor/model-a",
        "vendor/model-b",
    ]
    assert configured["credential_configured"] is True
    assert configured["header_count"] == 2
    assert "HTTP-Referer" not in repr(projection)
    assert "secret-one" not in repr(projection)
    assert "https://sylviagm.com" not in repr(projection)

    raw = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert "api_key" not in raw["providers"]["openrouter"]
    assert "secret-one" not in config_path.read_text(encoding="utf-8")
    assert raw["providers"]["openrouter"]["models"][1]["transport"] == "responses"


def test_custom_provider_identity_is_owner_derived_and_multiple_endpoints_coexist(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    first = settings_module.save_provider_settings(
        config_path,
        provider_id="custom",
        preset_id="custom",
        display_name="Basement GPU",
        base_url="http://192.168.1.20:8000/v1",
        credential="",
        model_id="local-a",
        model_name="Local A",
        context_window=16_384,
        max_output_tokens=2_048,
        transport="chat_completions",
        headers={},
    )
    second = settings_module.save_provider_settings(
        config_path,
        provider_id="custom",
        preset_id="custom",
        display_name="Upstairs GPU",
        base_url="http://192.168.1.21:8000/v1",
        credential="",
        model_id="local-b",
        model_name="Local B",
        context_window=16_384,
        max_output_tokens=2_048,
        transport="chat_completions",
        headers={},
    )

    assert first["provider_id"].startswith("custom_")
    assert second["provider_id"].startswith("custom_")
    assert first["provider_id"] != second["provider_id"]
    assert len(settings_module.provider_settings(config_path)["configured"]) == 2


def test_large_catalog_discovery_is_searchable_paginated_and_not_silently_truncated(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    catalog = [
        {
            "id": f"publisher/model-{index:05d}",
            "name": f"Model {index:05d}",
            "context_length": 8_192 + index,
            "max_completion_tokens": 2_048,
        }
        for index in range(2_050)
    ]

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url == httpx.URL("https://api.featherless.ai/v1/plan"):
            return httpx.Response(200, json={"max_context_length": None})
        assert request.url == httpx.URL("https://api.featherless.ai/v1/models")
        return httpx.Response(200, json={"data": catalog})

    _install_httpx_mock(monkeypatch, handle)
    result = settings_module.discover_provider_models(
        tmp_path / "config.yaml",
        provider_id="featherless",
        preset_id="featherless",
        base_url="https://api.featherless.ai/v1",
        credential="key",
        query="model-01",
        offset=10,
        limit=25,
    )

    assert result["total"] == 1_000
    assert len(result["models"]) == 25
    assert result["offset"] == 10
    assert result["has_more"] is True
    assert result["models"][0]["context_window"] == 9_202


def test_local_detection_probes_only_declared_loopback_presets(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    visited: list[str] = []

    def handle(request: httpx.Request) -> httpx.Response:
        visited.append(str(request.url))
        if request.url.port == 11434:
            return httpx.Response(200, json={"data": [{"id": "qwen3:8b"}]})
        return httpx.Response(503, json={"error": "offline"})

    _install_httpx_mock(monkeypatch, handle)
    result = settings_module.detect_local_providers(tmp_path / "config.yaml")

    ollama = next(item for item in result if item["provider_id"] == "ollama")
    assert ollama["reachable"] is True
    assert ollama["model_count"] == 1
    assert all(url.startswith("http://127.0.0.1:") for url in visited)
    assert all("192.168." not in url for url in visited)


def test_default_model_is_persisted_only_when_exactly_configured(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    saved = settings_module.save_provider_settings(
        config_path,
        provider_id="ollama",
        preset_id="ollama",
        display_name="Ollama",
        base_url="http://127.0.0.1:11434/v1",
        credential="",
        model_id="qwen3:8b",
        model_name="Qwen 3 8B",
        context_window=32_768,
        max_output_tokens=4_096,
        transport="responses",
        headers={},
    )
    assert settings_module.provider_settings(config_path)["default_model"] is None
    default = settings_module.set_default_model_settings(
        config_path,
        provider_id=saved["provider_id"],
        model_id="ollama/qwen3:8b",
    )

    assert default == {
        "provider_id": "ollama",
        "model_id": "ollama/qwen3:8b",
    }
    assert settings_module.provider_settings(config_path)["default_model"] == default
    settings_module.save_provider_settings(
        config_path, provider_id="ollama", preset_id="ollama", display_name="Ollama",
        base_url="http://127.0.0.1:11434/v1", credential="", model_id="second-model",
        model_name="Second model", context_window=65_536, max_output_tokens=8_192,
        transport="responses", headers={},
    )
    assert settings_module.provider_settings(config_path)["default_model"] == default


def test_current_session_model_switch_is_hash_bound_and_survives_owner_restart(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authority.sqlite3"
    with AuthorityOwner.open(database, "owner-model-switch-a") as owner:
        chat = owner.create_builder_chat(
            session_id="session-model-switch",
            provider_id="provider-a",
            model_id="provider-a/model-a",
        )
        switched = owner.update_agent_session_model(
            chat.session.session_id,
            provider_id="provider-b",
            model_id="provider-b/model-b",
        )
        assert switched.provider_id == "provider-b"
        assert switched.model_id == "provider-b/model-b"
        assert owner.session_context(chat.session.session_id).messages == ()

    with AuthorityOwner.open(database, "owner-model-switch-b") as reopened:
        restored = reopened.session_context("session-model-switch")
        assert restored.session.provider_id == "provider-b"
        assert restored.session.model_id == "provider-b/model-b"
        assert restored.messages == ()


def test_campaign_builder_model_switch_preserves_confirmation_and_reopens(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authority.sqlite3"
    with AuthorityOwner.open(database, "owner-campaign-model-switch-a") as owner:
        owner.confirm_new_campaign(
            operation_id="confirm-campaign-model-switch",
            display_name="Campaign model switch",
            package_lock=PackageLock(entries=()),
            turn_id="turn-campaign-model-switch",
            builder_session_id="session-campaign-model-switch",
            builder_provider_id="provider-a",
            builder_model_id="provider-a/model-a",
        )
        switched = owner.update_agent_session_model(
            "session-campaign-model-switch",
            provider_id="provider-b",
            model_id="provider-b/model-b",
        )
        assert switched.provider_id == "provider-b"
        assert switched.model_id == "provider-b/model-b"

    with AuthorityOwner.open(database, "owner-campaign-model-switch-b") as reopened:
        restored = reopened.session_context("session-campaign-model-switch")
        assert restored.session.provider_id == "provider-b"
        assert restored.session.model_id == "provider-b/model-b"


def test_one_turn_model_selection_is_durable_private_and_consumed_exactly_once(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    for model_id in ("model-a", "model-b"):
        settings_module.save_provider_settings(
            config_path,
            provider_id="ollama",
            preset_id="ollama",
            display_name="Ollama",
            base_url="http://127.0.0.1:11434/v1",
            credential="",
            model_id=model_id,
            model_name=model_id,
            context_window=32_768,
            max_output_tokens=4_096,
            transport="chat_completions",
            headers={},
        )
    selection = settings_module.set_session_model_once_settings(
        config_path,
        session_id="session-once",
        provider_id="ollama",
        model_id="ollama/model-b",
    )

    assert selection == {"provider_id": "ollama", "model_id": "ollama/model-b"}
    assert (
        settings_module.session_model_once_settings(config_path, session_id="session-once")
        == selection
    )
    assert "session_model_once" not in settings_module.provider_settings(config_path)
    assert (
        settings_module.clear_session_model_once_settings(
            config_path,
            session_id="session-once",
            expected_provider_id="ollama",
            expected_model_id="ollama/model-a",
        )
        is False
    )
    assert (
        settings_module.session_model_once_settings(config_path, session_id="session-once")
        == selection
    )
    assert (
        settings_module.clear_session_model_once_settings(
            config_path,
            session_id="session-once",
            expected_provider_id="ollama",
            expected_model_id="ollama/model-b",
        )
        is True
    )
    assert (
        settings_module.session_model_once_settings(config_path, session_id="session-once") is None
    )


def test_configured_resolver_preserves_response_transport_and_private_headers(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    settings_module.save_provider_settings(
        config_path,
        provider_id="custom",
        preset_id="custom",
        display_name="Responses Lab",
        base_url="http://127.0.0.1:9000/v1",
        credential="private-key",
        model_id="reasoner",
        model_name="Reasoner",
        context_window=65_536,
        max_output_tokens=8_192,
        transport="responses",
        headers={"X-Lab-Secret": "private-header"},
    )
    safe = settings_module.provider_settings(config_path)
    provider_id = safe["configured"][0]["provider_id"]
    model_id = safe["configured"][0]["models"][0]["canonical_id"]
    resolver = ConfiguredProviderResolver(config_path)

    choice = resolver.model_choices()[0]
    assert choice["model_id"] == model_id
    assert choice["transport"] == "responses"
    provider = resolver(
        SessionRecord(
            session_id="session-provider-responses",
            campaign_id=None,
            role=WriterRole.BUILDER,
            provider_id=provider_id,
            model_id=model_id,
        )
    )
    assert provider.model_info.transport == "responses"
    assert "private-key" not in repr(choice)
    assert "private-header" not in repr(choice)


@pytest.mark.asyncio
async def test_configured_anthropic_transport_receives_private_advanced_headers(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.yaml"
    settings_module.save_provider_settings(
        config_path,
        provider_id="custom",
        preset_id="custom",
        display_name="Messages Gateway",
        base_url="http://127.0.0.1:9010/v1",
        credential="gateway-key",
        model_id="messages-model",
        model_name="Messages Model",
        context_window=65_536,
        max_output_tokens=8_192,
        transport="anthropic_messages",
        headers={"X-Gateway-Secret": "private-header"},
    )
    safe = settings_module.provider_settings(config_path)
    configured = safe["configured"][0]
    resolver = ConfiguredProviderResolver(config_path)
    provider = resolver(
        SessionRecord(
            session_id="session-provider-messages",
            campaign_id=None,
            role=WriterRole.BUILDER,
            provider_id=configured["provider_id"],
            model_id=configured["models"][0]["canonical_id"],
        )
    )
    try:
        assert isinstance(provider, AnthropicProvider)
        assert provider._default_headers == {"X-Gateway-Secret": "private-header"}
        assert "private-header" not in repr(safe)
    finally:
        await provider.close()


@pytest.mark.asyncio
async def test_configured_anthropic_transport_parses_raw_tool_stream_without_sdk_accumulator(
    tmp_path: Path,
) -> None:
    captured_request: dict[str, object] = {}

    class FakeStream:
        def __init__(self) -> None:
            self._events = iter(
                [
                    SimpleNamespace(
                        type="content_block_start",
                        index=0,
                        content_block=SimpleNamespace(type="thinking", thinking="", signature=""),
                    ),
                    SimpleNamespace(
                        type="content_block_delta",
                        index=0,
                        delta=SimpleNamespace(type="thinking_delta", thinking="Synthetic thought"),
                    ),
                    SimpleNamespace(
                        type="content_block_delta",
                        index=0,
                        delta=SimpleNamespace(type="signature_delta", signature="opaque-"),
                    ),
                    SimpleNamespace(
                        type="content_block_delta",
                        index=0,
                        delta=SimpleNamespace(type="signature_delta", signature="signature"),
                    ),
                    SimpleNamespace(
                        type="content_block_start",
                        index=1,
                        content_block=SimpleNamespace(
                            type="tool_use",
                            id="tool-1",
                            name="campaign_file_update",
                            input={},
                        ),
                    ),
                    SimpleNamespace(
                        type="content_block_delta",
                        index=1,
                        delta=SimpleNamespace(
                            type="input_json_delta",
                            partial_json='{"expected_revision":4}',
                        ),
                    ),
                    SimpleNamespace(
                        type="message_delta",
                        usage=SimpleNamespace(input_tokens=20, output_tokens=8),
                        delta=SimpleNamespace(stop_reason="tool_use"),
                    ),
                    SimpleNamespace(type="message_stop"),
                ]
            )

        async def __aenter__(self) -> FakeStream:
            return self

        async def __aexit__(self, *_args: object) -> None:
            return None

        def __aiter__(self) -> FakeStream:
            return self

        async def __anext__(self) -> object:
            try:
                return next(self._events)
            except StopIteration:
                raise StopAsyncIteration from None

    class FakeMessages:
        async def create(self, **kwargs: object) -> FakeStream:
            captured_request.update(kwargs)
            return FakeStream()

        def stream(self, **_kwargs: object) -> FakeStream:
            raise AssertionError("the SDK message accumulator must not wrap raw SSE events")

    class FakeClient:
        def __init__(self) -> None:
            self.api_key = ""
            self.messages = FakeMessages()

        async def close(self) -> None:
            return None

    config_path = tmp_path / "config.yaml"
    settings_module.save_provider_settings(
        config_path,
        provider_id="opencode_go",
        preset_id="opencode_go",
        display_name="OpenCode Go",
        base_url="https://opencode.ai/zen/go/v1",
        credential="test-key",
        model_id="minimax-m3",
        model_name="MiniMax M3",
        context_window=524_288,
        max_output_tokens=4_096,
        transport="anthropic_messages",
    )
    configured = settings_module.provider_settings(config_path)["configured"][0]
    provider = ConfiguredProviderResolver(config_path)(
        SessionRecord(
            session_id="session-minimax-m3",
            campaign_id=None,
            role=WriterRole.BUILDER,
            provider_id=configured["provider_id"],
            model_id=configured["models"][0]["canonical_id"],
        )
    )
    assert isinstance(provider, SessionBoundRequestMetadataProvider)
    anthropic_provider = provider._provider
    assert isinstance(anthropic_provider, AnthropicProvider)
    await anthropic_provider._client.close()
    anthropic_provider._client = FakeClient()
    try:
        chunks = [
            chunk
            async for chunk in provider.generate_stream(
                [{"role": "user", "content": "Hello"}],
                thinking={"type": "adaptive"},
            )
        ]
    finally:
        await provider.close()

    assert [chunk.tool_call_delta for chunk in chunks if chunk.tool_call_delta is not None] == [
        {
            "index": 1,
            "id": "tool-1",
            "name": "campaign_file_update",
            "arguments": "",
        },
        {"index": 1, "partial_json": '{"expected_revision":4}'},
    ]
    assert [chunk.finish_reason for chunk in chunks if chunk.finish_reason] == ["tool_use"]
    assert "".join(chunk.reasoning_content for chunk in chunks) == "Synthetic thought"
    assert chunks[-1].anthropic_content == [
        {"type": "thinking", "thinking": "Synthetic thought", "signature": "opaque-signature"},
        {
            "type": "tool_use",
            "id": "tool-1",
            "name": "campaign_file_update",
            "input": {"expected_revision": 4},
        },
    ]
    assert captured_request["thinking"] == {"type": "adaptive"}
    assert captured_request["model"] == "minimax-m3"
    assert captured_request["stream"] is True


@pytest.mark.asyncio
async def test_configured_anthropic_transport_resolves_the_messages_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import anthropic

    from tests.test_provider_transport_matrix import event_stream

    observed_requests: list[httpx.Request] = []

    async def handle_request(request: httpx.Request) -> httpx.Response:
        observed_requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=event_stream("anthropic_messages"),
        )

    original_client = anthropic.AsyncAnthropic

    def mock_client(**kwargs: object) -> anthropic.AsyncAnthropic:
        kwargs["http_client"] = httpx.AsyncClient(transport=httpx.MockTransport(handle_request))
        return original_client(**kwargs)

    monkeypatch.setattr(anthropic, "AsyncAnthropic", mock_client)
    config_path = tmp_path / "config.yaml"
    settings_module.save_provider_settings(
        config_path,
        provider_id="opencode_go",
        preset_id="opencode_go",
        display_name="OpenCode Go",
        base_url="https://opencode.ai/zen/go/v1",
        credential="test-key",
        model_id="minimax-m3",
        model_name="MiniMax M3",
        context_window=524_288,
        max_output_tokens=4_096,
        transport="anthropic_messages",
        headers={
            "X-OpenCode-Session": "wrong-static-session",
            "x-opencode-client": "cli",
            "User-Agent": "opencode/impersonator",
            "X-Gateway-Secret": "private-header",
        },
    )
    safe_settings = settings_module.provider_settings(config_path)
    configured = safe_settings["configured"][0]
    assert "wrong-static-session" not in repr(safe_settings)
    assert "private-header" not in repr(safe_settings)
    resolver = ConfiguredProviderResolver(config_path)
    provider = resolver(
        SessionRecord(
            session_id="session-minimax-m3-endpoint",
            campaign_id=None,
            role=WriterRole.BUILDER,
            provider_id=configured["provider_id"],
            model_id=configured["models"][0]["canonical_id"],
        )
    )
    repeated = resolver(
        SessionRecord(
            session_id="session-minimax-m3-endpoint",
            campaign_id=None,
            role=WriterRole.BUILDER,
            provider_id=configured["provider_id"],
            model_id=configured["models"][0]["canonical_id"],
        )
    )
    second_session = resolver(
        SessionRecord(
            session_id="session-minimax-m3-second",
            campaign_id=None,
            role=WriterRole.BUILDER,
            provider_id=configured["provider_id"],
            model_id=configured["models"][0]["canonical_id"],
        )
    )
    assert isinstance(provider, SessionBoundRequestMetadataProvider)
    assert isinstance(repeated, SessionBoundRequestMetadataProvider)
    assert isinstance(second_session, SessionBoundRequestMetadataProvider)
    assert provider._provider is repeated._provider is second_session._provider
    assert isinstance(provider._provider, AnthropicProvider)
    assert provider._provider._default_headers == {"X-Gateway-Secret": "private-header"}
    try:
        for bound_provider in (provider, repeated, second_session):
            chunks = [
                chunk
                async for chunk in bound_provider.generate_stream(
                    [{"role": "user", "content": "Hello"}]
                )
            ]
            assert "".join(chunk.content or "" for chunk in chunks) == "ready"
    finally:
        await provider.close()

    assert [str(request.url) for request in observed_requests] == [
        "https://opencode.ai/inference/go/anthropic/v1/messages",
        "https://opencode.ai/inference/go/anthropic/v1/messages",
        "https://opencode.ai/inference/go/anthropic/v1/messages",
    ]
    assert all(
        request.headers["authorization"] == "Bearer test-key"
        for request in observed_requests
    )
    assert all("x-api-key" not in request.headers for request in observed_requests)
    session_headers = [request.headers["x-opencode-session"] for request in observed_requests]
    assert session_headers[0] == session_headers[1]
    assert session_headers[2] != session_headers[0]
    assert all(value.startswith("sylvia_") for value in session_headers)
    assert all(request.headers["x-opencode-client"] == "sylvia-gm" for request in observed_requests)
    assert all(
        request.headers["user-agent"] == f"Sylvia-GM/{__version__}" for request in observed_requests
    )
    assert all(
        request.headers["x-gateway-secret"] == "private-header" for request in observed_requests
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("transport", "model_id", "expected_path"),
    [
        ("chat_completions", "hy3", "/inference/go/openai/v1/chat/completions"),
        ("responses", "gpt-5.6-luna", "/inference/go/openai/v1/responses"),
    ],
)
async def test_opencode_session_headers_reach_each_openai_compatible_api_shape(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    transport: str,
    model_id: str,
    expected_path: str,
) -> None:
    import sylvia_gm.provider._openai_compat as openai_compat
    from tests.test_provider_transport_matrix import event_stream

    observed_requests: list[httpx.Request] = []

    async def handle_request(request: httpx.Request) -> httpx.Response:
        observed_requests.append(request)
        return httpx.Response(
            200,
            headers={"content-type": "text/event-stream"},
            text=event_stream(transport),
        )

    original_client = openai_compat.AsyncOpenAI

    def mock_client(**kwargs: object) -> object:
        kwargs["http_client"] = httpx.AsyncClient(transport=httpx.MockTransport(handle_request))
        return original_client(**kwargs)

    monkeypatch.setattr(openai_compat, "AsyncOpenAI", mock_client)
    config_path = tmp_path / f"{transport}.yaml"
    settings_module.save_provider_settings(
        config_path,
        provider_id="opencode_go",
        preset_id="opencode_go",
        display_name="OpenCode Go",
        base_url="https://opencode.ai/zen/go/v1",
        credential="test-key",
        model_id=model_id,
        model_name=model_id,
        context_window=524_288,
        max_output_tokens=4_096,
        transport=transport,
    )
    configured = settings_module.provider_settings(config_path)["configured"][0]
    provider = ConfiguredProviderResolver(config_path)(
        SessionRecord(
            session_id=f"session-{transport}",
            campaign_id=None,
            role=WriterRole.BUILDER,
            provider_id=configured["provider_id"],
            model_id=configured["models"][0]["canonical_id"],
        )
    )
    assert isinstance(provider, SessionBoundRequestMetadataProvider)
    try:
        chunks = [
            chunk
            async for chunk in provider.generate_stream([{"role": "user", "content": "Hello"}])
        ]
    finally:
        await provider.close()

    assert "".join(chunk.content or "" for chunk in chunks) == "ready"
    assert [request.url.path for request in observed_requests] == [expected_path]
    assert observed_requests[0].headers["x-opencode-session"].startswith("sylvia_")
    assert observed_requests[0].headers["x-opencode-client"] == "sylvia-gm"
    assert observed_requests[0].headers["user-agent"] == f"Sylvia-GM/{__version__}"


@pytest.mark.parametrize("preset_id", ["opencode_go", "opencode_zen"])
def test_each_opencode_preset_binds_request_metadata_from_saved_identity(
    tmp_path: Path,
    preset_id: str,
) -> None:
    config_path = tmp_path / f"{preset_id}.yaml"
    config_path.write_text(
        json.dumps(
            {
                "providers": {
                    "additional_saved_profile": {
                        "enabled": True,
                        "preset_id": preset_id,
                        "mode": "byok",
                        "name": "Additional OpenCode profile",
                        "base_url": "https://example.invalid/v1",
                        "api_key": "test-key",
                        "models": [
                            {
                                "id": "test-model",
                                "name": "Test Model",
                                "context_window": 16_384,
                                "max_output_tokens": 1_024,
                                "transport": "chat_completions",
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    provider = ConfiguredProviderResolver(config_path)(
        SessionRecord(
            session_id=f"session-{preset_id}",
            campaign_id=None,
            role=WriterRole.BUILDER,
            provider_id="additional_saved_profile",
            model_id="additional-saved-profile/test-model",
        )
    )

    assert isinstance(provider, SessionBoundRequestMetadataProvider)


@pytest.mark.parametrize(
    "preset_id",
    ["openai", "anthropic", "openrouter", "lm_studio", "custom"],
)
def test_non_opencode_presets_do_not_gain_automatic_opencode_headers(
    tmp_path: Path,
    preset_id: str,
) -> None:
    config_path = tmp_path / f"{preset_id}.yaml"
    config_path.write_text(
        json.dumps(
            {
                "providers": {
                    "lookalike": {
                        "enabled": True,
                        "preset_id": preset_id,
                        "mode": "byok",
                        "name": "Non-OpenCode provider",
                        "base_url": "https://opencode.ai/zen/go/v1",
                        "api_key": "test-key",
                        "models": [
                            {
                                "id": "test-model",
                                "name": "Test Model",
                                "context_window": 16_384,
                                "max_output_tokens": 1_024,
                                "transport": "chat_completions",
                            }
                        ],
                    }
                }
            }
        ),
        encoding="utf-8",
    )
    provider = ConfiguredProviderResolver(config_path)(
        SessionRecord(
            session_id=f"session-{preset_id}",
            campaign_id=None,
            role=WriterRole.BUILDER,
            provider_id="lookalike",
            model_id="lookalike/test-model",
        )
    )

    assert not isinstance(provider, SessionBoundRequestMetadataProvider)


def test_responses_transport_converts_messages_tools_and_stream_deltas() -> None:
    transport = ResponsesTransport()
    request = transport.format_request(
        model_id="gpt-test",
        messages=[
            {"role": "system", "content": "Be useful."},
            {"role": "user", "content": "Use the tool."},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call-1",
                        "type": "function",
                        "function": {"name": "lookup", "arguments": '{"x":1}'},
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call-1", "content": "done"},
        ],
        tools=[
            {
                "type": "function",
                "function": {
                    "name": "lookup",
                    "description": "Look up a value",
                    "parameters": {"type": "object"},
                },
            }
        ],
        stream=True,
    )

    assert request["model"] == "gpt-test"
    assert request["stream"] is True
    assert request["tools"][0]["name"] == "lookup"
    assert any(item.get("type") == "function_call" for item in request["input"])
    assert any(item.get("type") == "function_call_output" for item in request["input"])

    text_delta = transport.parse_stream_chunk(
        SimpleNamespace(type="response.output_text.delta", delta="Hello")
    )
    assert text_delta is not None and text_delta.content == "Hello"
    call_start = transport.parse_stream_chunk(
        SimpleNamespace(
            type="response.output_item.added",
            output_index=2,
            item=SimpleNamespace(
                type="function_call", call_id="call-2", name="lookup", arguments=""
            ),
        )
    )
    assert call_start is not None
    assert call_start.tool_call_delta == {
        "index": 2,
        "id": "call-2",
        "function": {"name": "lookup", "arguments": ""},
    }
    call_arguments = transport.parse_stream_chunk(
        SimpleNamespace(
            type="response.function_call_arguments.delta",
            output_index=2,
            delta='{"x":1}',
        )
    )
    assert call_arguments is not None
    assert call_arguments.tool_call_delta["function"]["arguments"] == '{"x":1}'

    response = transport.parse_response(
        SimpleNamespace(
            output_text="Done",
            output=[],
            status="completed",
            usage=SimpleNamespace(
                input_tokens=80,
                output_tokens=20,
                input_tokens_details=SimpleNamespace(cached_tokens=30),
                output_tokens_details=SimpleNamespace(reasoning_tokens=7),
            ),
        ),
        "gpt-test",
    )
    assert response.usage.reasoning_tokens == 7
    assert response.cache_usage.cache_read_tokens == 30


def test_provider_validation_reports_reachability_auth_discovery_stream_and_tools_separately(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"data": [{"id": "agent-model"}]})
        assert request.url.path == "/v1/chat/completions"
        payload = json.loads(request.content)
        assert payload["max_tokens"] == 128
        assert payload["tool_choice"] == "auto"
        assert payload["tools"][0]["function"]["parameters"] == {
            "type": "object",
            "properties": {},
            "additionalProperties": False,
        }
        body = (
            'data: {"choices":[{"delta":{"tool_calls":[{"index":0,"id":"probe-1",'
            '"function":{"name":"sylvia_compatibility_probe","arguments":"{}"}}]},'
            '"finish_reason":null}]}\n\n'
            'data: {"choices":[{"delta":{},"finish_reason":"tool_calls"}]}\n\n'
            "data: [DONE]\n\n"
        )
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, text=body)

    _install_httpx_mock(monkeypatch, handle)
    report = settings_module.validate_provider_model(
        tmp_path / "config.yaml",
        provider_id="custom",
        preset_id="custom",
        base_url="http://127.0.0.1:9999/v1",
        credential="",
        model_id="agent-model",
        transport="chat_completions",
        headers={},
    )

    assert report == {
        "reachability": "passed",
        "authentication": "not_required",
        "model_discovery": "passed",
        "transport": "passed",
        "streaming": "passed",
        "tool_calling": "passed",
        "agent_ready": True,
        "message": "Provider and model passed the agent compatibility probe.",
    }


def test_provider_validation_returns_safe_offline_report_instead_of_fake_success(
    monkeypatch: object,
    tmp_path: Path,
) -> None:
    def handle(_request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("private machine detail")

    _install_httpx_mock(monkeypatch, handle)
    report = settings_module.validate_provider_model(
        tmp_path / "config.yaml",
        provider_id="ollama",
        preset_id="ollama",
        base_url="http://127.0.0.1:11434/v1",
        credential="",
        model_id="qwen3:8b",
        transport="chat_completions",
        headers={},
    )

    assert report["reachability"] == "failed"
    assert report["streaming"] == "unverified"
    assert report["tool_calling"] == "unverified"
    assert report["agent_ready"] is False
    assert "private machine detail" not in repr(report)
