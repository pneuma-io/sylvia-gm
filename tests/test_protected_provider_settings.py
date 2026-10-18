"""Disposable Windows credentials exercise storage, recovery and request boundaries."""

from __future__ import annotations

import json
import sys

import pytest
import yaml

from sylvia_gm.agent._provider_resolver import ConfiguredProviderResolver
from sylvia_gm.config import _credential_protection as protection
from sylvia_gm.config import _user_settings as store
from sylvia_gm.config import loader
from sylvia_gm.config import provider_settings as settings
from sylvia_gm.config._resolver import ConfigError
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionRecord


@pytest.fixture(autouse=True)
def isolated_defaults(tmp_path, monkeypatch):
    path = tmp_path / "defaults.yaml"
    path.write_text("providers: {}\n", encoding="utf-8")
    monkeypatch.setattr(loader, "_DEFAULT_CONFIG_PATH", path)


def save(path, *, credential="synthetic-key", headers=None, provider_id="openrouter"):
    return settings.save_provider_settings(
        path, provider_id=provider_id, preset_id="openrouter", display_name="Synthetic",
        base_url="https://openrouter.ai/api/v1", credential=credential,
        model_id="test-model", model_name="Test model", context_window=8192, max_output_tokens=1024,
        headers=headers,
    )


@pytest.mark.skipif(sys.platform != "win32", reason="Requires real current-user Windows DPAPI")
def test_native_round_trip_and_corruption():
    envelope = protection.protect_credentials("synthetic-key", {"X-Private": "synthetic-header"})
    assert protection.unprotect_credentials(envelope) == (
        "synthetic-key", {"X-Private": "synthetic-header"}
    )
    assert "synthetic" not in repr(envelope)
    envelope["data"] = "AAAA"
    with pytest.raises(ConfigError, match="Re-enter"):
        protection.unprotect_credentials(envelope)


def test_save_reopen_retain_replace_remove_and_projection(tmp_path):
    path = tmp_path / "config.yaml"
    save(path, headers={"X-Private-Synthetic": "synthetic-header"})
    stored = path.read_text(encoding="utf-8")
    assert all(value not in stored for value in (
        "synthetic-key", "X-Private-Synthetic", "synthetic-header"
    ))
    config = loader.load_config(path, load_dotenv_file=False)
    assert config.providers["openrouter"].api_key.get_secret_value() == "synthetic-key"
    assert "protected_credentials" not in config.model_dump_json()
    assert "synthetic-key" not in repr(config)
    save(path, credential="", headers={})
    assert settings._existing_connection(path, "openrouter")[1:3] == (
        "synthetic-key", {"X-Private-Synthetic": "synthetic-header"}
    )
    projection = settings.provider_settings(path)
    assert projection["configured"][0]["header_count"] == 1
    assert all(value not in json.dumps(projection) for value in (
        "synthetic-key", "X-Private-Synthetic", "synthetic-header", "windows-dpapi-user"
    ))
    save(path, credential="replacement", headers={"X-Replaced": "replacement-header"})
    assert settings._existing_connection(path, "openrouter")[1:3] == (
        "replacement", {"X-Replaced": "replacement-header"}
    )
    settings.remove_provider_settings(path, provider_id="openrouter")
    assert "protected_credentials" not in path.read_text(encoding="utf-8")


def test_migration_is_all_or_nothing_and_idempotent(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    legacy = {
        "app": {"name": "Synthetic preserved setting"},
        "providers": {
            "first": {"api_key": "synthetic-first", "models": []},
            "second": {"headers": {"X-Synthetic": "synthetic-second"}, "models": []},
        },
    }
    original = yaml.safe_dump(legacy).encode()
    path.write_bytes(original)
    native = protection._dpapi

    def fail_second(value, *, decrypt):
        if not decrypt and b"synthetic-second" in value:
            raise ConfigError("Synthetic protection failure")
        return native(value, decrypt=decrypt)

    with monkeypatch.context() as patched:
        patched.setattr(protection, "_dpapi", fail_second)
        with pytest.raises(ConfigError, match="migration did not complete"):
            loader.load_config(path, load_dotenv_file=False)
    assert path.read_bytes() == original
    assert not list(tmp_path.glob("provider-settings-*"))
    migrated = store.read_user_settings(path)
    encoded = path.read_bytes()
    assert b"synthetic-first" not in encoded and b"synthetic-second" not in encoded
    assert migrated["app"] == legacy["app"]
    assert store.read_user_settings(path) == migrated
    assert path.read_bytes() == encoded


def test_write_failure_preserves_old_settings_and_leaves_no_temporary(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    save(path)
    before = path.read_bytes()

    def fail_replace(*args):
        assert b"synthetic-new" not in args[0].read_bytes()
        raise OSError("Synthetic disk failure")

    with monkeypatch.context() as patched:
        patched.setattr(store.os, "replace", fail_replace)
        with pytest.raises(ConfigError, match="previous file is unchanged"):
            save(path, credential="synthetic-new")
    assert path.read_bytes() == before
    assert not list(tmp_path.glob("provider-settings-*"))
    save(path, credential="synthetic-new")
    assert settings._existing_connection(path, "openrouter")[1] == "synthetic-new"


@pytest.mark.parametrize("failure", ["corrupt", "version", "foreign-context"])
def test_unavailable_provider_isolated_no_request_and_can_be_replaced(
    tmp_path, monkeypatch, failure
):
    path = tmp_path / "config.yaml"
    save(path)
    save(path, provider_id="other", credential="other-synthetic-key")
    document = yaml.safe_load(path.read_text(encoding="utf-8"))
    envelope = document["providers"]["openrouter"]["protected_credentials"]
    if failure == "corrupt":
        envelope["data"] = "AAAA"
    elif failure == "version":
        envelope["version"] = 999
    else:
        original_data = envelope["data"]
        native = protection.unprotect_credentials

        def foreign_context(value, **kwargs):
            if value.get("data") == original_data:
                raise ConfigError(protection.UNAVAILABLE_MESSAGE)
            return native(value, **kwargs)

        monkeypatch.setattr(loader, "unprotect_credentials", foreign_context)
        monkeypatch.setattr(settings, "unprotect_credentials", foreign_context)
    path.write_text(yaml.safe_dump(document), encoding="utf-8")
    before = path.read_bytes()
    resolver = ConfiguredProviderResolver(path)
    # Catalog construction does not unlock or construct clients for either provider.
    assert resolver._configured_providers == set()
    with pytest.raises(CommandRejected, match="Re-enter"):
        resolver(SessionRecord(
            session_id="synthetic-protected", campaign_id=None, role=WriterRole.BUILDER,
            provider_id="openrouter", model_id="openrouter/test-model",
        ))
    assert resolver._registry._providers == {}
    with pytest.raises(ConfigError, match="Re-enter"):
        settings.discover_provider_models(
            path, provider_id="openrouter", base_url="", credential=""
        )
    projection = settings.provider_settings(path)
    affected = next(row for row in projection["configured"] if row["provider_id"] == "openrouter")
    assert affected["credential_error"] == protection.UNAVAILABLE_MESSAGE
    assert settings._existing_connection(path, "other")[1] == "other-synthetic-key"
    assert path.read_bytes() == before
    save(path, credential="recovered-synthetic-key")
    assert settings._existing_connection(path, "openrouter")[1] == "recovered-synthetic-key"


def test_explicit_environment_override_stays_in_memory(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    save(path)
    before = path.read_bytes()
    monkeypatch.setenv("SYLVIA_GM_PROVIDERS__OPENROUTER__API_KEY", "synthetic-env-key")
    assert settings._existing_connection(path, "openrouter")[1] == "synthetic-env-key"
    config = loader.load_config(path, load_dotenv_file=False)
    assert config.providers["openrouter"].api_key.get_secret_value() == "synthetic-env-key"
    assert path.read_bytes() == before


def test_blank_edit_preserves_an_environment_reference(tmp_path, monkeypatch):
    path = tmp_path / "config.yaml"
    monkeypatch.setenv("SYLVIA_SYNTHETIC_CREDENTIAL", "first-environment-value")
    save(path, credential="${SYLVIA_SYNTHETIC_CREDENTIAL}")
    save(path, credential="")
    monkeypatch.setenv("SYLVIA_SYNTHETIC_CREDENTIAL", "changed-environment-value")
    assert settings._existing_connection(path, "openrouter")[1] == "changed-environment-value"
