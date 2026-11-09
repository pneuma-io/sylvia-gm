"""Helper orchestration uses inert payloads and fake launch callbacks; no key or app launch."""

import io
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from sylvia_gm.application.desktop_update import helper, runtime, service
from sylvia_gm.application.desktop_update.manifest import UpdateError
from sylvia_gm.application.desktop_update.restart import RestartHandshake
from tests.test_desktop_update_preparation import fixture
from tests.test_desktop_update_replacement import assert_tree


@pytest.fixture(autouse=True)
def ordinary_launch(monkeypatch):
    for name in ("SYLVIA_GM_AUTHORITY_DATABASE", "SYLVIA_GM_OWNER_EXECUTABLE"):
        monkeypatch.delenv(name, raising=False)


def identity():
    return {
        "format_version": 1,
        "product": "Sylvia GM",
        "component": "owner",
        "version": "0.3.0-alpha.2",
        "target": "windows-x86_64",
        "storage_compatibility": 1,
    }


class Exited:
    def all_exited(self):
        return True


def test_helper_waits_for_original_processes_then_launches_only_exact_copy_once(tmp_path):
    prepared, _, backup, old, new, database = fixture(tmp_path)
    prepared.prepare(backup, lambda: True)
    prepared.store.save(prepared.identity, "closing", prepared.manifest.version.text)
    before = database.read_bytes()
    ticks, launches = [], []

    class Barrier:
        def all_exited(self):
            return len(ticks) >= 3

    def pause(_seconds):
        assert_tree(prepared.live, old)
        assert prepared.store.load()["phase"] == "closing"
        ticks.append(True)

    def probe(executable):
        assert executable == prepared.live / "sylvia-gm-owner.exe"
        assert_tree(prepared.live, new)
        return identity()

    def launch(executable, arguments):
        launches.append((executable, arguments))
        assert executable == prepared.live / "Sylvia GM.exe"
        handshake = RestartHandshake(prepared.root, prepared.identity, prepared.attempt)
        handshake.confirm(
            nonce=arguments[-1],
            live=prepared.live,
            desktop_version="0.3.0-alpha.2",
            owner_version="0.3.0-alpha.2",
            target="windows-x86_64",
            distribution="portable",
            storage_compatibility=1,
        )

        class Child:
            def poll(self):
                return None

        return Child()

    helper.apply_and_restart(prepared, Barrier(), probe=probe, launch=launch, pause=pause)
    helper.apply_and_restart(prepared, Exited(), probe=probe, launch=launch, pause=pause)
    assert len(ticks) == 3 and len(launches) == 1
    assert prepared.store.load()["phase"] == "confirmed"
    assert database.read_bytes() == before


def test_failed_start_keeps_checkpoint_and_never_automatically_relaunches(tmp_path):
    prepared, _, backup, _, _, database = fixture(tmp_path)
    prepared.prepare(backup, lambda: True)
    prepared.store.save(prepared.identity, "closing", prepared.manifest.version.text)
    before = database.read_bytes()
    calls = []

    class FailedChild:
        def poll(self):
            return 1

    def launch(executable, arguments):
        calls.append((executable, arguments))
        return FailedChild()

    with pytest.raises(UpdateError) as failed:
        helper.apply_and_restart(prepared, Exited(), probe=lambda _: identity(), launch=launch)
    assert failed.value.code == "startup_failed"
    with pytest.raises(UpdateError) as reconcile:
        helper.apply_and_restart(prepared, Exited(), probe=lambda _: identity(), launch=launch)
    assert reconcile.value.code == "recovery_required" and len(calls) == 1
    assert prepared.store.load()["phase"] == "awaiting_restart"
    assert (prepared.stage / "data-checkpoint/authority.sqlite3").exists()
    assert database.read_bytes() == before


def test_owner_version_mismatch_prevents_app_launch(tmp_path):
    prepared, _, backup, _, _, _ = fixture(tmp_path)
    prepared.prepare(backup, lambda: True)
    prepared.store.save(prepared.identity, "closing", prepared.manifest.version.text)
    calls = []
    with pytest.raises(UpdateError) as wrong:
        helper.apply_and_restart(
            prepared,
            Exited(),
            probe=lambda _: {**identity(), "version": "0.3.0-alpha.1"},
            launch=lambda *args: calls.append(args),
        )
    assert wrong.value.code == "wrong_owner_identity" and calls == []
    assert prepared.store.load()["phase"] != "confirmed"


def test_unarmed_helper_cannot_start_replacement_or_launch(tmp_path):
    prepared, _, backup, old, _, database = fixture(tmp_path)
    prepared.prepare(backup, lambda: True)
    before = database.read_bytes()
    with pytest.raises(UpdateError) as unarmed:
        helper.apply_and_restart(prepared, Exited())
    assert unarmed.value.code == "recovery_required"
    assert_tree(prepared.live, old)
    assert database.read_bytes() == before
    assert prepared.store.load()["phase"] == "prepared"


def test_second_helper_cannot_overtake_the_same_operation(tmp_path):
    from sylvia_gm.application.desktop_update.checkpoint import acquire_update_data_lock
    from sylvia_gm.domain.errors import OwnerConflict

    prepared, _, backup, old, _, database = fixture(tmp_path)
    prepared.prepare(backup, lambda: True)
    prepared.store.save(prepared.identity, "closing", prepared.manifest.version.text)
    before = database.read_bytes()
    lock = acquire_update_data_lock(prepared.stage / "helper-runtime")
    try:
        with pytest.raises(OwnerConflict):
            helper.apply_and_restart(prepared, Exited())
        assert_tree(prepared.live, old)
        assert database.read_bytes() == before
        assert not (prepared.stage / "restart.json").exists()
    finally:
        lock.close()


def test_unconfigured_helper_does_not_read_input_or_touch_process_handles(monkeypatch):
    monkeypatch.setattr(service, "PUBLIC_KEYS", {})

    class Unreadable:
        def readline(self, _maximum):
            raise AssertionError("No helper input is admitted without packaged trust")

    def forbidden():
        raise AssertionError("No native handle access before trust")

    monkeypatch.setattr(helper, "WindowsHandles", forbidden)
    output = io.StringIO()
    assert helper.main(Unreadable(), output) == 2
    assert json.loads(output.getvalue()) == {"type": "error", "code": "trust_not_configured"}


def test_bounded_helper_input_and_untrusted_extra_fields_reject_without_native_access(monkeypatch):
    with pytest.raises(UpdateError):
        helper._read(io.BytesIO(b"x" * 65537 + b"\n"))
    monkeypatch.setattr(
        service,
        "PUBLIC_KEYS",
        {
            "public-vector": bytes.fromhex(
                "d75a980182b10ab7d54bfed3c964073a0ee172f3daa62325af021a68f707511a"
            )
        },
    )
    output = io.StringIO()
    assert (
        helper.main(io.BytesIO(b'{"type":"desktop_update_apply","url":"untrusted"}\n'), output) == 2
    )
    assert json.loads(output.getvalue())["code"] == "invalid_helper_request"


def test_real_source_entry_rejects_unconfigured_helper_without_owner_or_database(tmp_path):
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(root / "src")
    result = subprocess.run(
        [
            sys.executable,
            str(root / "src-tauri/scripts/sylvia_gm_owner_entry.py"),
            "--desktop-update-helper",
        ],
        input=b"",
        capture_output=True,
        cwd=tmp_path,
        env=environment,
        creationflags=subprocess.CREATE_NO_WINDOW,
        timeout=15,
    )
    assert result.returncode == 2 and result.stderr == b""
    assert json.loads(result.stdout)["code"] == "trust_not_configured"
    assert list(tmp_path.iterdir()) == []


def test_child_environment_uses_public_reset_without_persistent_change(monkeypatch):
    monkeypatch.setattr(runtime.sys, "_MEIPASS", "C:/inert-extraction", raising=False)
    monkeypatch.setenv("PATH", "C:/normal-tools;C:/inert-extraction/bin;C:/other-tools")
    before = dict(os.environ)
    result = runtime.child_environment()
    assert result["PYINSTALLER_RESET_ENVIRONMENT"] == "1"
    assert result["PATH"] == "C:/normal-tools;C:/other-tools"
    assert dict(os.environ) == before


def test_engineering_override_is_rejected_before_any_apply(tmp_path, monkeypatch):
    prepared, _, backup, old, _, _ = fixture(tmp_path)
    prepared.prepare(backup, lambda: True)
    monkeypatch.setenv("SYLVIA_GM_AUTHORITY_DATABASE", "fictional override")
    with pytest.raises(UpdateError) as refused:
        helper.apply_and_restart(prepared, Exited())
    assert refused.value.code == "unsupported_launch_override"
    assert prepared.store.load()["phase"] == "prepared"
    assert_tree(prepared.live, old)
