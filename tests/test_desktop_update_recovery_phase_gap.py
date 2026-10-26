"""A selected recovery intent survives a failed global save without poisoning its retry."""

import io
import json
from types import SimpleNamespace

import pytest

from sylvia_gm.application.desktop_update import helper, recovery_service
from sylvia_gm.application.desktop_update.checkpoint import RESTORE_CONFIRMATION
from sylvia_gm.application.desktop_update.manifest import UpdateError
from sylvia_gm.application.desktop_update.recovery import KEEP_DATA, RecoveryOperation
from sylvia_gm.application.desktop_update.startup import authorize_startup
from sylvia_gm.application.desktop_update.storage import UpdateStore
from tests.test_desktop_update_manifest import accept_stub
from tests.test_desktop_update_recovery import old_identity
from tests.test_desktop_update_recovery_service import service_fixture


@pytest.mark.parametrize("choice", [KEEP_DATA, RESTORE_CONFIRMATION])
def test_failed_phase_save_requires_fresh_reviewed_intent_and_full_retry_ack(
    tmp_path, monkeypatch, choice,
):
    update, boundary, host, database = service_fixture(tmp_path)
    recovery = RecoveryOperation(update)
    saved, failures, launches, starts, presentations = UpdateStore.save, [], [], [], []

    def save(store, identity, phase, version, error_code=None):
        if phase == "rollback_awaiting_restart" and not failures:
            assert recovery.handshake._load()["phase"] == "pending"
            failures.append(recovery.handshake.ticket.parent)
            raise UpdateError("disk_full", "Injected between selection and global phase save")
        return saved(store, identity, phase, version, error_code)

    monkeypatch.setattr(UpdateStore, "save", save)
    monkeypatch.setattr(helper, "KeyRing", lambda _: SimpleNamespace(
        keys={"stub": b"public"}, verify=accept_stub,
    ))
    monkeypatch.setattr(helper, "WindowsHandles", lambda: None)
    monkeypatch.setattr(helper, "OwnedProcesses", lambda *_: SimpleNamespace(
        all_exited=lambda: True, close=lambda: None,
    ))

    def launch(executable, arguments):
        assert executable == update.live / "Sylvia GM.exe"
        assert arguments[-1] != failures[0].name
        assert update.store.load()["phase"] == "rollback_awaiting_restart"
        assert not recovery.handshake.error_receipt.exists()
        assert not recovery.handshake.confirmed()
        request = {"operation_id": update.identity, "attempt": update.attempt,
                   "nonce": arguments[-1], "recovery": True}
        start = authorize_startup(
            update.root, boundary.installation, request, running=update.current,
            desktop_version=update.current.text, storage_compatibility=1, verify=accept_stub,
        )
        assert start.confirm() is False  # Writes usable ack without the old failure poisoning it.
        starts.append(start)
        launches.append(arguments)
        return SimpleNamespace(poll=lambda: None)

    execute = helper._recover_and_restart_locked
    monkeypatch.setattr(helper, "_recover_and_restart_locked", lambda prepared, processes, choice,
                        **kwargs: execute(prepared, processes, choice,
                                          probe=lambda _: old_identity(update), launch=launch,
                                          **kwargs))

    def run(selected_choice, restart_nonce):
        output = io.StringIO()
        request = {
            "type": "desktop_update_recover", "operation_id": update.identity,
            "attempt": update.attempt, "data_root": str(update.root), "live": str(update.live),
            "distribution": update.distribution, "choice": selected_choice,
            "restart_nonce": restart_nonce, "processes": [
                {"role": "desktop", "handle": 101, "pid": 10, "creation_time": 1},
                {"role": "owner", "handle": 102, "pid": 11, "creation_time": 2},
            ],
        }

        class Input:
            first = True

            def readline(self, _limit):
                if self.first:
                    self.first = False
                    return (json.dumps(request) + "\n").encode()
                ready = json.loads(output.getvalue().splitlines()[-1])
                return (json.dumps({"action": "commit", "operation_id": update.identity,
                                   "attempt": update.attempt,
                                   "helper_nonce": ready["helper_nonce"]}) + "\n").encode()

        presentation = SimpleNamespace(stage=lambda _: None, close=lambda: None,
                                       fail=lambda code: presentations.append(code))
        result = helper.main(Input(), output, presentation_factory=lambda: presentation)
        return result, [json.loads(row) for row in output.getvalue().splitlines()]

    result, rows = run(choice, None)
    assert result == 2 and rows[-1] == {"type": "error", "code": "disk_full"}
    assert update.store.load()["phase"] == "rollback_ready"
    assert launches == [] and starts == [] and presentations == ["disk_full"]
    failed = failures[0]
    retained = {path.name: path.read_bytes() for path in failed.iterdir()}
    assert json.loads(retained["helper-error.json"]) == {"code": "disk_full"}
    before = database.read_bytes(), (update.root / "config.yaml").read_bytes()

    monkeypatch.setattr(recovery_service, "_RECOVERY_QUALIFIED", True)
    state = boundary.handle("state", {"host": host})
    assert state["phase"] == "rollback_ready" and state["restart_nonce"] == failed.name
    assert state["can_recover"] is True and state["can_keep_data"] is True
    request = {"host": host, "operation_id": update.identity, "choice": "keep",
               "restart_nonce": state["restart_nonce"]}
    assert boundary.handle("handoff", request)["restart_nonce"] == failed.name
    for changed in [{"restart_nonce": None}, {"choice": "restore"}]:
        with pytest.raises(UpdateError):
            boundary.handle("handoff", {**request, **changed})
    with pytest.raises(UpdateError) as automatic:
        recovery.create_restart(old_identity(update))
    assert automatic.value.code == "recovery_required"

    monkeypatch.setattr(RecoveryOperation, "_rollback_locked",
                        lambda *_: pytest.fail("Retry must not restore or roll back again"))
    result, rows = run(KEEP_DATA, str(state["restart_nonce"]))
    assert result == 0 and [row["type"] for row in rows] == ["ready"]
    assert len(launches) == 1 and starts[0].confirm() is True
    assert update.store.load()["phase"] == "rolled_back"
    assert recovery.handshake.confirmed() and recovery.handshake._load()["nonce"] != failed.name
    assert retained == {path.name: path.read_bytes() for path in failed.iterdir()}
    assert (database.read_bytes(), (update.root / "config.yaml").read_bytes()) == before
    assert presentations == ["disk_full"]  # No false completion error on the successful retry.
