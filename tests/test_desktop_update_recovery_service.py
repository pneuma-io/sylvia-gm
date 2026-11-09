"""Recovery pipe authenticates the native copy and never acquires the player authority."""

import io
import json

import pytest

from sylvia_gm.application.desktop_update import launch, recovery, recovery_service
from sylvia_gm.application.desktop_update.manifest import UpdateError, Version
from sylvia_gm.application.desktop_update.recovery_service import RecoveryService
from sylvia_gm.infrastructure.launch_handshake import derive_owner_response
from sylvia_gm.interfaces import owner_process
from tests.test_desktop_update_manifest import accept_stub
from tests.test_desktop_update_startup import prepared


def service_fixture(tmp_path):
    update, _, installation, _, database = prepared(tmp_path)
    boundary = RecoveryService(installation, current=update.manifest.version, verify=accept_stub)
    host = {"version": update.manifest.version.text, "target": "windows-x86_64",
            "distribution": "portable"}
    return update, boundary, host, database


def test_explicit_status_is_read_only_and_unqualified_recovery_cannot_execute(tmp_path):
    update, boundary, host, database = service_fixture(tmp_path)
    before = database.read_bytes(), (update.base.parent / "current.json").read_bytes()
    status = boundary.handle("state", {"host": host})
    assert status["can_keep_data"] is True and status["can_recover"] is False
    assert status["previous_version"] == update.current.text
    with pytest.raises(UpdateError) as rejected:
        boundary.handle("handoff", {
            "host": host, "operation_id": update.identity, "choice": "keep", "restart_nonce": None,
        })
    assert rejected.value.code == "recovery_unavailable"
    assert (database.read_bytes(), (update.base.parent / "current.json").read_bytes()) == before


@pytest.mark.parametrize("field", ["url", "path", "public_key", "command", "version"])
def test_extra_renderer_inputs_reject_before_reading_retained_files(tmp_path, monkeypatch, field):
    _, boundary, host, _ = service_fixture(tmp_path)
    monkeypatch.setattr(boundary, "_prepared", lambda: pytest.fail("must reject before file reads"))
    with pytest.raises(UpdateError) as error:
        boundary.handle("state", {"host": host, field: "untrusted"})
    assert error.value.code == "invalid_update_request"


def test_qualified_test_handoff_returns_only_exact_retained_helper_metadata(tmp_path, monkeypatch):
    update, boundary, host, database = service_fixture(tmp_path)
    monkeypatch.setattr(recovery_service, "_RECOVERY_QUALIFIED", True)
    before = database.read_bytes()
    result = boundary.handle("handoff", {
        "host": host, "operation_id": update.identity, "choice": "keep", "restart_nonce": None,
    })
    assert set(result) == {"operation_id", "attempt", "helper", "restart_nonce"}
    assert result["restart_nonce"] is None
    assert result["attempt"] == update.attempt
    assert set(result["helper"]) == {"size", "sha256"}
    assert database.read_bytes() == before
    (update.stage / "helper/sylvia-gm-owner.exe").write_bytes(b"tampered retained helper")
    with pytest.raises(UpdateError) as error:
        boundary.handle("handoff", {
            "host": host, "operation_id": update.identity, "choice": "restore",
            "restart_nonce": None,
        })
    assert error.value.code == "changed_helper"


def test_newer_recovery_reader_cannot_apply_an_old_checkpoint_without_its_old_helper(
    tmp_path, monkeypatch,
):
    update, boundary, host, _ = service_fixture(tmp_path)
    monkeypatch.setattr(recovery, "STORAGE_COMPATIBILITY", 2)
    assert boundary.handle("state", {"host": host})["can_keep_data"] is True
    with pytest.raises(UpdateError) as error:
        recovery.RecoveryOperation(update).rollback(recovery.KEEP_DATA, lambda: True)
    assert error.value.code == "wrong_recovery_helper"
    assert update.store.load()["phase"] == "awaiting_restart"


def test_wrong_running_release_does_not_gain_recovery_capability(tmp_path):
    _, boundary, host, _ = service_fixture(tmp_path)
    boundary.current = Version.parse("9.0.0")
    boundary.native_version = "9.0.0"
    host["version"] = "9.0.0"
    with pytest.raises(UpdateError) as error:
        boundary.handle("state", {"host": host})
    assert error.value.code == "wrong_recovery_app"


@pytest.mark.parametrize("pair", ["matched", "old_sidecar", "old_desktop"])
def test_real_owner_uses_recovery_only_pipe_without_authority_migration_or_provider(
    tmp_path, monkeypatch, pair,
):
    update, _, installation, _, database = prepared(tmp_path)
    before = database.read_bytes()
    native = update.current.text if pair == "old_desktop" else update.manifest.version.text
    current = update.current.text if pair == "old_sidecar" else update.manifest.version.text
    monkeypatch.setattr(launch, "__version__", current)
    monkeypatch.setattr(recovery_service, "__version__", current)
    monkeypatch.setattr(
        owner_process, "_launch", lambda *_: pytest.fail("no authority in recovery"),
    )
    envelope = {
        "type": "launch", "secret_hex": "a1" * 32, "owner_id": "inert-recovery-test",
        "database_path": str(database), "desktop_update_context": {
            "version": native, "target": "windows-x86_64", "distribution": "portable",
            "live": str(update.live), "data_root": str(installation.data),
        },
    }
    rows = [envelope]
    for identity, method, params in [
        ("reject-builder", "create_builder_chat", {}),
        ("reject-model", "send_message", {"text": "must not execute"}),
        ("trust-empty", "desktop_update_recovery_state", {"host": {
            "version": native, "target": "windows-x86_64", "distribution": "portable",
        }}),
        ("close", "shutdown", {}),
    ]:
        rows.append({"id": identity, "method": method, "params": params})
    output = io.StringIO()
    assert owner_process.run(
        io.StringIO("".join(json.dumps(row) + "\n" for row in rows)), output,
        provider_resolver=lambda *_: pytest.fail("no provider in recovery"),
    ) == 0
    replies = [json.loads(line) for line in output.getvalue().splitlines()]
    assert replies[0] == {
        "ok": True, "type": "ready", "recovery_only": True,
        "response_hex": derive_owner_response("a1" * 32),
    }
    assert [row["error"]["code"] for row in replies[1:4]] == [
        "recovery_only", "recovery_only", "trust_not_configured",
    ]
    assert replies[-1]["result"] == {"status": "closed"}
    assert database.read_bytes() == before


def test_mixed_pair_is_read_only_recovery_and_cannot_gain_ordinary_install_context(
    tmp_path, monkeypatch,
):
    update, boundary, host, database = service_fixture(tmp_path)
    boundary.native_version = update.current.text
    host["version"] = update.current.text
    before = database.read_bytes()
    assert boundary.handle("state", {"host": host})["can_recover"] is False
    context = {"version": update.current.text, "target": "windows-x86_64",
               "distribution": "portable", "live": str(update.live), "data_root": str(update.root)}
    monkeypatch.setattr(launch, "__version__", update.manifest.version.text)
    with pytest.raises(UpdateError) as rejected:
        launch.Installation.from_launch(context, update.root)
    assert rejected.value.code == "invalid_launch_context"
    assert launch.Installation.for_recovery(context, update.root) == boundary.installation
    assert database.read_bytes() == before


def test_recovery_request_size_is_bounded_before_json_decode():
    with pytest.raises(UpdateError) as error:
        recovery_service._read(io.StringIO('{"text":"' + "x" * 65536 + '"}\n'))
    assert error.value.code == "invalid_update_request"
