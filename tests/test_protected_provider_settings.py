"""Disposable credentials exercise storage, recovery and request boundaries.

Windows uses real current-user DPAPI. Linux uses the per-user key file, always in a
test-owned state folder: the top-level conftest replaces XDG_STATE_HOME for the
whole session, and the Linux-only tests below give each test its own folder.
"""

from __future__ import annotations

import base64
import json
import os
import stat
import sys
import threading

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

NATIVE = sys.platform in {"win32", "linux"}
LINUX_LABEL = "linux-user-key-file"
WINDOWS_LABEL = "windows-dpapi-user"
FOREIGN_LABEL = WINDOWS_LABEL if sys.platform == "linux" else LINUX_LABEL
requires_native = pytest.mark.skipif(
    not NATIVE, reason="Requires Windows DPAPI or the Linux user key file"
)
linux_only = pytest.mark.skipif(
    sys.platform != "linux", reason="Linux user key-file protection exists only on Linux"
)


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


@requires_native
def test_native_round_trip_and_corruption():
    envelope = protection.protect_credentials("synthetic-key", {"X-Private": "synthetic-header"})
    assert envelope["protection"] == (LINUX_LABEL if sys.platform == "linux" else WINDOWS_LABEL)
    assert protection.unprotect_credentials(envelope) == (
        "synthetic-key", {"X-Private": "synthetic-header"}
    )
    assert "synthetic" not in repr(envelope)
    envelope["data"] = "AAAA"
    with pytest.raises(ConfigError, match="Re-enter"):
        protection.unprotect_credentials(envelope)


def test_foreign_protection_kind_is_unavailable_without_unsealing(monkeypatch):
    def never_unseal(value, *, decrypt):
        raise AssertionError("A foreign envelope must be refused before unsealing")

    monkeypatch.setattr(protection, "_platform_seal", never_unseal)
    envelope = {
        "version": 1, "protection": FOREIGN_LABEL,
        "data": base64.b64encode(bytes(range(48))).decode(),
    }
    with pytest.raises(ConfigError, match="Re-enter") as refused:
        protection.unprotect_credentials(envelope)
    assert str(refused.value) == protection.UNAVAILABLE_MESSAGE


@requires_native
def test_native_envelope_relabelled_as_foreign_kind_is_unavailable():
    envelope = protection.protect_credentials("synthetic-key", {})
    envelope["protection"] = FOREIGN_LABEL
    with pytest.raises(ConfigError, match="Re-enter"):
        protection.unprotect_credentials(envelope)


def test_user_facing_messages_match_the_platform():
    if sys.platform == "linux":
        assert protection.UNAVAILABLE_MESSAGE == (
            "This computer could not unlock this connection's saved credentials. "
            "Re-enter its API key and private headers, or remove the connection."
        )
        assert "Windows" not in store._MIGRATION_RETRY
        assert "Windows" not in protection._VERIFY_FAILED
    else:
        assert protection.UNAVAILABLE_MESSAGE == (
            "Windows could not unlock this connection's saved credentials. "
            "Re-enter its API key and private headers, or remove the connection."
        )
        assert store._MIGRATION_RETRY == (
            "retry after Windows protection and settings-file access are available."
        )
        assert protection._VERIFY_FAILED == "Windows credential protection could not be verified"


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
        "synthetic-key", "X-Private-Synthetic", "synthetic-header", WINDOWS_LABEL, LINUX_LABEL
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
    native = protection._platform_seal

    def fail_second(value, *, decrypt):
        if not decrypt and b"synthetic-second" in value:
            raise ConfigError("Synthetic protection failure")
        return native(value, decrypt=decrypt)

    with monkeypatch.context() as patched:
        patched.setattr(protection, "_platform_seal", fail_second)
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


@pytest.mark.parametrize("failure", ["corrupt", "version", "foreign-kind", "foreign-context"])
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
    elif failure == "foreign-kind":
        envelope["protection"] = FOREIGN_LABEL
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


@pytest.fixture
def key_state(tmp_path, monkeypatch):
    """Give one Linux test its own missing state folder; return the key folder and file."""
    state = tmp_path / "state"
    monkeypatch.setenv("XDG_STATE_HOME", str(state))
    folder = state / "sylvia-gm"
    return folder, folder / "credential-key"


def _snapshot(path):
    status = os.lstat(path)
    if stat.S_ISLNK(status.st_mode):
        content: object = os.readlink(path)
    elif stat.S_ISREG(status.st_mode):
        content = path.read_bytes()
    else:
        content = sorted(os.listdir(path))
    return (
        status.st_mode, status.st_ino, status.st_nlink, status.st_size,
        status.st_mtime_ns, content,
    )


@linux_only
def test_linux_first_protect_creates_private_key_outside_settings(tmp_path, key_state):
    folder, key = key_state
    assert not os.path.lexists(folder.parent)
    path = tmp_path / "data" / "config.yaml"
    save(path, headers={"X-Private-Synthetic": "synthetic-header"})
    for created in (folder.parent, folder):
        assert stat.S_IMODE(os.lstat(created).st_mode) == 0o700
        assert stat.S_ISDIR(os.lstat(created).st_mode)
    status = os.lstat(key)
    assert stat.S_ISREG(status.st_mode) and stat.S_IMODE(status.st_mode) == 0o600
    assert status.st_size == 32 and status.st_nlink == 1 and status.st_uid == os.geteuid()
    assert sorted(os.listdir(folder)) == ["credential-key"]
    secret = key.read_bytes()
    stored = path.read_bytes()
    assert all(value not in stored for value in (
        secret, secret.hex().encode(), base64.b64encode(secret),
        b"synthetic-key", b"X-Private-Synthetic", b"synthetic-header",
    ))
    document = yaml.safe_load(stored)
    assert document["providers"]["openrouter"]["protected_credentials"]["protection"] == LINUX_LABEL
    assert not any(os.path.samefile(key, entry) for entry in path.parent.rglob("*"))
    assert settings._existing_connection(path, "openrouter")[1:3] == (
        "synthetic-key", {"X-Private-Synthetic": "synthetic-header"}
    )


@linux_only
def test_linux_second_protect_keeps_key_and_uses_a_fresh_nonce(key_state):
    _folder, key = key_state
    first = protection.protect_credentials("synthetic-key", {"X-Private": "synthetic-header"})
    before = _snapshot(key)
    second = protection.protect_credentials("synthetic-key", {"X-Private": "synthetic-header"})
    assert _snapshot(key) == before
    assert first["data"] != second["data"]
    nonces = [base64.b64decode(item["data"])[:12] for item in (first, second)]
    assert nonces[0] != nonces[1]
    for envelope in (first, second):
        assert protection.unprotect_credentials(envelope) == (
            "synthetic-key", {"X-Private": "synthetic-header"}
        )


@linux_only
def test_linux_opening_without_a_key_is_unavailable_and_creates_nothing(
    tmp_path, key_state, monkeypatch
):
    folder, key = key_state
    envelope = protection.protect_credentials("synthetic-key", {})
    key.unlink()
    with pytest.raises(ConfigError, match="Re-enter"):
        protection.unprotect_credentials(envelope)
    assert os.listdir(folder) == []
    empty_state = tmp_path / "empty-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(empty_state))
    with pytest.raises(ConfigError, match="Re-enter"):
        protection.unprotect_credentials(envelope)
    assert not os.path.lexists(empty_state)


def _make_group_readable(key, tmp_path):
    os.chmod(key, 0o640)


def _make_other_readable(key, tmp_path):
    os.chmod(key, 0o604)


def _rewrite(key, content):
    descriptor = os.open(key, os.O_WRONLY | os.O_TRUNC | os.O_NOFOLLOW)
    try:
        os.write(descriptor, content)
    finally:
        os.close(descriptor)


def _make_short(key, tmp_path):
    _rewrite(key, key.read_bytes()[:31])


def _make_long(key, tmp_path):
    _rewrite(key, key.read_bytes() + b"\0")


def _make_symlink(key, tmp_path):
    # The decoy holds the real key bytes, so only refusing the link prevents unlocking.
    decoy = tmp_path / "decoy-credential-key"
    decoy.write_bytes(key.read_bytes())
    os.chmod(decoy, 0o600)
    key.unlink()
    os.symlink(decoy, key)


def _make_second_hard_link(key, tmp_path):
    os.link(key, tmp_path / "second-credential-key-link")


@linux_only
@pytest.mark.parametrize("unsafe", [
    _make_group_readable, _make_other_readable, _make_short, _make_long,
    _make_symlink, _make_second_hard_link,
], ids=["group-readable", "other-readable", "short", "long", "symlink", "second-hard-link"])
def test_linux_unsafe_key_file_is_refused_and_left_unchanged(tmp_path, key_state, unsafe):
    folder, key = key_state
    envelope = protection.protect_credentials("synthetic-key", {})
    unsafe(key, tmp_path)
    before = _snapshot(key)
    listing = sorted(os.listdir(folder))
    with pytest.raises(ConfigError, match="protection failed") as refused:
        protection.protect_credentials("synthetic-key", {})
    assert "synthetic" not in str(refused.value)
    with pytest.raises(ConfigError, match="Re-enter"):
        protection.unprotect_credentials(envelope)
    assert _snapshot(key) == before
    assert sorted(os.listdir(folder)) == listing


@linux_only
def test_linux_interrupted_key_creation_is_finished_by_the_next_protect(key_state):
    folder, key = key_state
    envelope = protection.protect_credentials("synthetic-key", {})
    secret = key.read_bytes()
    # A creation killed after publishing the key leaves its temporary as a second link.
    leftover = folder / ".credential-key-0123456789abcdef.tmp"
    os.link(key, leftover)
    unrelated = folder / "credential-key.bak"
    unrelated.write_bytes(b"not a temporary")
    with pytest.raises(ConfigError, match="Re-enter"):
        protection.unprotect_credentials(envelope)
    assert os.path.lexists(leftover)
    protection.protect_credentials("synthetic-key", {})
    assert sorted(os.listdir(folder)) == ["credential-key", "credential-key.bak"]
    assert key.read_bytes() == secret and os.lstat(key).st_nlink == 1
    assert unrelated.read_bytes() == b"not a temporary"
    assert protection.unprotect_credentials(envelope)[0] == "synthetic-key"


@linux_only
@pytest.mark.parametrize("unsafe", ["group-writable", "other-writable", "symlink"])
def test_linux_unsafe_key_folder_is_refused(tmp_path, key_state, unsafe):
    folder, key = key_state
    envelope = protection.protect_credentials("synthetic-key", {})
    if unsafe == "symlink":
        moved = tmp_path / "moved-key-folder"
        os.rename(folder, moved)
        os.symlink(moved, folder)
        checked = moved
    else:
        os.chmod(folder, 0o770 if unsafe == "group-writable" else 0o707)
        checked = folder
    before = (_snapshot(folder), _snapshot(checked), _snapshot(checked / "credential-key"))
    listing = sorted(os.listdir(checked))
    with pytest.raises(ConfigError, match="protection failed"):
        protection.protect_credentials("synthetic-key", {})
    with pytest.raises(ConfigError, match="Re-enter"):
        protection.unprotect_credentials(envelope)
    after = (_snapshot(folder), _snapshot(checked), _snapshot(checked / "credential-key"))
    assert after == before
    assert sorted(os.listdir(checked)) == listing


@linux_only
@pytest.mark.parametrize("part", ["nonce", "ciphertext", "tag", "truncated"])
def test_linux_tampered_envelope_is_unavailable(key_state, part):
    envelope = protection.protect_credentials("synthetic-key", {"X-Private": "synthetic-header"})
    sealed = bytearray(base64.b64decode(envelope["data"]))
    if part == "truncated":
        sealed = sealed[:27]
    else:
        sealed[{"nonce": 0, "ciphertext": 12, "tag": len(sealed) - 1}[part]] ^= 0x01
    tampered = {**envelope, "data": base64.b64encode(bytes(sealed)).decode()}
    with pytest.raises(ConfigError, match="Re-enter"):
        protection.unprotect_credentials(tampered)
    assert protection.unprotect_credentials(envelope)[0] == "synthetic-key"


@linux_only
def test_linux_state_folder_honours_only_an_absolute_xdg_state_home(tmp_path, monkeypatch):
    absolute = tmp_path / "absolute-state"
    monkeypatch.setenv("XDG_STATE_HOME", str(absolute))
    protection.protect_credentials("synthetic-key", {})
    assert (absolute / "sylvia-gm" / "credential-key").is_file()
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("XDG_STATE_HOME", "relative-state")
    envelope = protection.protect_credentials("synthetic-key", {})
    assert not os.path.lexists(tmp_path / "relative-state")
    fallback = home / ".local" / "state" / "sylvia-gm"
    for created in (home / ".local", home / ".local" / "state", fallback):
        assert stat.S_IMODE(os.lstat(created).st_mode) == 0o700
    assert (fallback / "credential-key").is_file()
    monkeypatch.delenv("XDG_STATE_HOME")
    assert protection.unprotect_credentials(envelope)[0] == "synthetic-key"


@linux_only
def test_linux_concurrent_first_use_ends_with_one_key(key_state):
    folder, _key = key_state
    workers = 8
    barrier = threading.Barrier(workers, timeout=30)
    envelopes: dict[int, dict[str, object]] = {}
    errors: list[BaseException] = []

    def protect(index):
        try:
            barrier.wait()
            envelopes[index] = protection.protect_credentials(f"synthetic-key-{index}", {})
        except BaseException as error:  # Reported below; a thread must not swallow it.
            errors.append(error)

    threads = [threading.Thread(target=protect, args=(index,)) for index in range(workers)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=60)
    assert not any(thread.is_alive() for thread in threads)
    assert errors == []
    assert sorted(os.listdir(folder)) == ["credential-key"]
    assert sorted(envelopes) == list(range(workers))
    for index, envelope in envelopes.items():
        assert protection.unprotect_credentials(envelope) == (f"synthetic-key-{index}", {})
