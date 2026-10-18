"""Subscription setup uses its own catalog allowance and preserves explicit caps."""

import json
from datetime import UTC, datetime, timedelta

import pytest
import yaml

from sylvia_gm.agent._provider_resolver import ConfiguredProviderResolver
from sylvia_gm.config import chatgpt_settings
from sylvia_gm.config.provider_settings import provider_settings
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.session import SessionRecord
from sylvia_gm.provider._chatgpt_account import ChatGPTAccount
from sylvia_gm.provider._codex_rpc import CodexConnectionError
from tests.test_pes82_chatgpt_connection import AccountRuntime


def account_with_catalog(tmp_path, *, age=timedelta(), window=272000, percent=95):
    runtime = AccountRuntime(tmp_path / "chatgpt-runtime")
    runtime.home.mkdir()
    runtime.account = {"type": "chatgpt"}
    runtime.model_entry.update(model="gpt-6-sol", displayName="GPT-6 Sol")
    catalog = {
        "fetched_at": (datetime.now(UTC) - age).isoformat(),
        "client_version": "0.155.0",
        "identity": "private-identity-sentinel",
        "models": [
            {"slug": "gpt-6-sol", "context_window": window,
             "max_context_window": 1050000, "effective_context_window_percent": percent,
             "model_messages": "private-instruction-sentinel"},
            {"slug": "not-advertised", "context_window": 999999,
             "effective_context_window_percent": 100},
        ],
    }
    (runtime.home / "models_cache.json").write_text(json.dumps(catalog), encoding="utf-8")
    (runtime.home / "auth.json").write_text("sealed-credential-sentinel", encoding="utf-8")
    return ChatGPTAccount(runtime), runtime


def test_subscription_catalog_defaults_are_separate_from_api_ceiling_and_availability(tmp_path):
    account, runtime = account_with_catalog(tmp_path)
    models = account.models()
    assert [item["id"] for item in models] == ["gpt-6-sol"]
    model = models[0]
    assert model["runtime_context_window"] == 258400
    assert model["runtime_context_source"] == "model_catalog"
    assert model["output_reference_tokens"] == 128000
    assert model["output_reference_source"] == "https://developers.openai.com/api/docs/models/gpt-6-sol"
    assert "sentinel" not in json.dumps(models)
    assert {method for method, _ in runtime.calls} == {"account/read", "model/list"}


@pytest.mark.parametrize("changes", [
    {"age": timedelta(days=2)}, {"window": True}, {"window": 0},
    {"percent": None}, {"percent": 101}, {"percent": "95"},
])
def test_invalid_or_stale_runtime_catalog_does_not_guess_context(tmp_path, changes):
    account, _ = account_with_catalog(tmp_path, **changes)
    model = account.models()[0]
    assert model.get("runtime_context_window") is None
    assert model["output_reference_tokens"] == 128000


def test_automatic_subscription_save_preserves_caps_reopen_and_lower_observed_limit(tmp_path, monkeypatch):
    account, runtime = account_with_catalog(tmp_path)
    monkeypatch.setattr(chatgpt_settings, "_account", lambda _: account)
    automatic = {"id": "gpt-6-sol", "context_window": None, "max_output_tokens": None}
    result = chatgpt_settings.chatgpt_settings_action(tmp_path, "add_model", automatic)
    assert result["saved_model"] == "chatgpt-subscription/gpt-6-sol"

    def saved():
        settings = provider_settings(tmp_path / "config.yaml")
        assert settings["default_model"] is None
        return settings["configured"][0]["models"][0]

    first = saved()
    assert first["configured_context_window"] is None
    assert first["runtime_context_window"] == first["effective_context_window"] == 258400
    assert first["runtime_context_source"] == "model_catalog"
    assert first["configured_output_tokens"] is None
    assert first["max_output_tokens"] == first["declared_max_output_tokens"] == 128000
    assert first["declared_context_window"] is None
    assert saved() == first
    resolved = ConfiguredProviderResolver(tmp_path / "config.yaml")(SessionRecord(
        session_id="automatic-subscription", campaign_id=None, role=WriterRole.BUILDER,
        provider_id="chatgpt_subscription", model_id=str(result["saved_model"]),
    ))
    assert resolved.model_info.context_window == 258400
    assert resolved.model_info.configured_context_window is None
    assert resolved.model_info.max_output_tokens == 128000
    chatgpt_settings.chatgpt_settings_action(tmp_path, "add_model", {
        **automatic, "context_window": 200000, "max_output_tokens": 32000,
    })
    chatgpt_settings.chatgpt_settings_action(tmp_path, "models")
    assert saved()["configured_context_window"] == 200000
    assert saved()["max_output_tokens"] == 32000

    # An actual lower limit already saved by the owner must not be enlarged by a catalog default.
    path = tmp_path / "config.yaml"
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    row = document["providers"]["chatgpt_subscription"]["models"][0]
    row.update(runtime_context_window=180000, runtime_context_source="runtime_reported")
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    chatgpt_settings.chatgpt_settings_action(tmp_path, "add_model", automatic)
    restored = saved()
    assert restored["configured_context_window"] is None
    assert restored["effective_context_window"] == 180000
    assert restored["runtime_context_source"] == "runtime_reported"
    assert restored["configured_output_tokens"] is None
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    document["providers"]["chatgpt_subscription"]["models"][0]["runtime_context_source"] = "model_catalog"
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    chatgpt_settings.chatgpt_settings_action(tmp_path, "add_model", automatic)
    assert saved()["runtime_context_window"] == 258400, "a stale catalog is not an observed hard cap"
    assert {method for method, _ in runtime.calls} == {"account/read", "model/list"}


def test_missing_runtime_catalog_saves_unknown_capacity_without_manual_allowance(tmp_path, monkeypatch):
    account, runtime = account_with_catalog(tmp_path)
    (runtime.home / "models_cache.json").unlink()
    monkeypatch.setattr(chatgpt_settings, "_account", lambda _: account)
    chatgpt_settings.chatgpt_settings_action(tmp_path, "add_model", {
        "id": "gpt-6-sol", "context_window": None, "max_output_tokens": None,
    })
    row = provider_settings(tmp_path / "config.yaml")["configured"][0]["models"][0]
    assert row["runtime_context_window"] is None
    assert row["effective_context_window"] is None
    assert row["configured_context_window"] is None
    assert row["output_budget_mode"] == "runtime_managed"
