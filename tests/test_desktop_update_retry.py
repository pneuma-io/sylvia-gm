"""Explicit recovery retries retain old generations and fence stale startup before SQL."""

import io
import json
from types import SimpleNamespace

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.desktop_update import recovery_service, restart
from sylvia_gm.application.desktop_update.checkpoint import acquire_update_data_lock
from sylvia_gm.application.desktop_update.helper import recover_and_restart
from sylvia_gm.application.desktop_update.manifest import UpdateError
from sylvia_gm.application.desktop_update.recovery import KEEP_DATA, RecoveryOperation
from sylvia_gm.application.desktop_update.startup import authorize_startup
from sylvia_gm.infrastructure.owner_lock import OwnerConflict
from sylvia_gm.infrastructure.sqlite_authority import SQLiteAuthorityStore
from sylvia_gm.interfaces import owner_process
from tests.test_desktop_update_manifest import accept_stub
from tests.test_desktop_update_preparation import fixture
from tests.test_desktop_update_recovery import old_identity
from tests.test_desktop_update_recovery_service import service_fixture


def pending_recovery(tmp_path, phase="launched", *, real_authority=False):
    update, _, backup, _, _, database = fixture(tmp_path, authority_database=real_authority)
    update.prepare(backup, lambda: True)
    update.apply(lambda: True)
    recovery = RecoveryOperation(update)
    recovery.rollback(KEEP_DATA, lambda: True)
    handshake = recovery.create_restart(old_identity(update))
    nonce = handshake._load()["nonce"]
    if phase == "launch_failed":
        with pytest.raises(UpdateError):
            handshake.launch_once(lambda _: (_ for _ in ()).throw(OSError("inert launch failure")))
    elif phase == "launching":
        row = handshake._load()
        row["phase"] = "launching"
        restart.atomic_receipt(handshake.ticket, row)
    else:
        handshake.launch_once(lambda _: object())
        if phase == "confirmed":
            handshake.confirm(
                nonce=nonce, live=update.live, desktop_version=update.current.text,
                owner_version=update.current.text, target=update.target,
                distribution=update.distribution, storage_compatibility=1,
            )
    restart.atomic_receipt(handshake.error_receipt, {"code": "startup_failed"})
    return update, recovery, str(nonce), database


@pytest.mark.parametrize("phase", ["launch_failed", "launching", "launched", "confirmed"])
def test_explicit_retry_retains_previous_records_and_requires_new_ack_without_data_restore(
    tmp_path, phase,
):
    update, recovery, old_nonce, database = pending_recovery(tmp_path, phase)
    prior = recovery.handshake.ticket.parent
    retained = {path.name: path.read_bytes() for path in prior.iterdir()}
    before = database.read_bytes()
    launches, starts, stages = [], [], []

    def launch(executable, arguments):
        assert executable == update.live / "Sylvia GM.exe"
        assert arguments[-1] != old_nonce
        assert not recovery.handshake.confirmed()  # An old acknowledgement cannot pass this start.
        request = {"operation_id": update.identity, "attempt": update.attempt,
                   "nonce": arguments[-1], "recovery": True}
        from sylvia_gm.application.desktop_update.launch import Installation
        from sylvia_gm.application.desktop_update.storage import directory_identity

        installation = Installation(
            update.live, update.root, update.distribution, update.target,
            directory_identity(update.live), directory_identity(update.root),
        )
        start = authorize_startup(
            update.root, installation, request, running=update.current,
            desktop_version=update.current.text, storage_compatibility=1, verify=accept_stub,
        )
        assert start.confirm() is False
        starts.append(start)
        launches.append(arguments)
        return SimpleNamespace(poll=lambda: None)

    recover_and_restart(
        update, SimpleNamespace(all_exited=lambda: True), KEEP_DATA, restart_nonce=old_nonce,
        probe=lambda _: old_identity(update), launch=launch, notify=stages.append,
    )
    assert starts[0].confirm() is True
    assert stages == ["waiting", "verifying", "starting", "confirming"]
    assert len(launches) == 1 and database.read_bytes() == before
    assert {path.name: path.read_bytes() for path in prior.iterdir()} == retained
    assert recovery.handshake.error_receipt.parent != prior
    with pytest.raises(UpdateError):
        recover_and_restart(
            update, SimpleNamespace(all_exited=lambda: True), KEEP_DATA, restart_nonce=old_nonce,
            launch=lambda *_: pytest.fail("stale retry cannot launch again"),
        )


def test_failed_retry_preserves_new_pending_generation_for_another_explicit_choice(tmp_path):
    update, recovery, old_nonce, database = pending_recovery(tmp_path)
    before = database.read_bytes()
    with pytest.raises(UpdateError) as error:
        recover_and_restart(
            update, SimpleNamespace(all_exited=lambda: True), KEEP_DATA, restart_nonce=old_nonce,
            probe=lambda _: old_identity(update),
            launch=lambda *_: (_ for _ in ()).throw(OSError("inert second launch failure")),
        )
    assert error.value.code == "restart_failed"
    new_nonce = recovery.handshake._load()["nonce"]
    assert new_nonce != old_nonce and recovery.handshake._load()["phase"] == "launch_failed"
    assert database.read_bytes() == before
    with pytest.raises(UpdateError) as stale:
        recovery.retry_restart(old_nonce, old_identity(update), lambda: True)
    assert stale.value.code == "stale_restart"
    assert recovery.handshake._load()["nonce"] == new_nonce


def test_real_owner_refuses_retry_and_leaves_selected_generation_unchanged(tmp_path):
    update, recovery, nonce, database = pending_recovery(tmp_path, real_authority=True)
    selected = recovery.handshake.selection.read_bytes()
    before = database.read_bytes()
    owner = AuthorityOwner.open(database, "live-retry-fixture")
    try:
        with pytest.raises(OwnerConflict):
            recovery.retry_restart(nonce, old_identity(update), lambda: True)
        assert recovery.handshake.selection.read_bytes() == selected
    finally:
        owner.close()
    assert database.read_bytes() == before


def test_stale_authorized_recovery_is_rejected_under_owner_lock_before_migration(
    tmp_path, monkeypatch,
):
    update, recovery, nonce, database = pending_recovery(tmp_path)
    before = database.read_bytes()
    original = owner_process._launch

    def paused(envelope, **kwargs):
        lock = acquire_update_data_lock(update.stage / "helper-runtime")
        try:
            recovery.retry_restart(nonce, old_identity(update), lambda: True)
        finally:
            lock.close()
        return original(envelope, **kwargs)

    monkeypatch.setattr(owner_process, "_launch", paused)
    monkeypatch.setattr(
        SQLiteAuthorityStore, "_migrate", lambda _: pytest.fail("stale SQL migration"),
    )
    launch = {
        "type": "launch", "database_path": str(database), "secret_hex": "a1" * 32,
        "owner_id": "stale-recovery-fixture", "desktop_update_context": {
            "live": str(update.live), "data_root": str(update.root), "version": update.current.text,
            "target": update.target, "distribution": update.distribution,
        }, "desktop_update_start": {
            "operation_id": update.identity, "attempt": update.attempt, "nonce": nonce,
            "recovery": True,
        },
    }
    output = io.StringIO()
    assert owner_process.run(io.StringIO(json.dumps(launch) + "\n"), output) == 2
    assert json.loads(output.getvalue())["type"] == "update_recovery_required"
    assert database.read_bytes() == before


def test_partial_generation_creation_never_replaces_the_prior_selected_intent(
    tmp_path, monkeypatch,
):
    update, recovery, nonce, _ = pending_recovery(tmp_path)
    selected = recovery.handshake.selection.read_bytes()
    original = restart.atomic_receipt

    def interrupted(path, row):
        if path == recovery.handshake.selection:
            raise OSError("inert failure before selection commit")
        return original(path, row)

    monkeypatch.setattr(restart, "atomic_receipt", interrupted)
    with pytest.raises(OSError):
        recovery.retry_restart(nonce, old_identity(update), lambda: True)
    assert recovery.handshake.selection.read_bytes() == selected
    monkeypatch.setattr(restart, "atomic_receipt", original)
    recovery.retry_restart(nonce, old_identity(update), lambda: True)
    assert recovery.handshake._load()["nonce"] != nonce
    assert len(list((update.stage / "rollback-starts").iterdir())) == 3


def test_reviewed_restart_nonce_is_required_by_recovery_handoff(tmp_path, monkeypatch):
    update, boundary, host, _ = service_fixture(tmp_path)
    recovery = RecoveryOperation(update)
    recovery.rollback(KEEP_DATA, lambda: True)
    recovery.create_restart(old_identity(update)).launch_once(lambda _: object())
    monkeypatch.setattr(recovery_service, "_RECOVERY_QUALIFIED", True)
    state = boundary.handle("state", {"host": host})
    assert state["can_keep_data"] is True and state["restart_nonce"] is not None
    request = {"host": host, "operation_id": update.identity, "choice": "keep",
               "restart_nonce": state["restart_nonce"]}
    assert boundary.handle("handoff", request)["restart_nonce"] == state["restart_nonce"]
    for change in [{"restart_nonce": None}, {"restart_nonce": "f" * 32}, {"choice": "restore"}]:
        with pytest.raises(UpdateError):
            boundary.handle("handoff", {**request, **change})


def test_retention_limit_is_visible_before_handoff_and_preserves_selected_attempt(
    tmp_path, monkeypatch,
):
    update, boundary, host, database = service_fixture(tmp_path)
    recovery = RecoveryOperation(update)
    recovery.rollback(KEEP_DATA, lambda: True)
    recovery.create_restart(old_identity(update)).launch_once(lambda _: object())
    selected = recovery.handshake.selection.read_bytes()
    before = database.read_bytes()
    root = update.stage / "rollback-starts"
    for index in range(restart.MAX_RECOVERY_STARTS - 1):
        (root / f"{index:032x}").mkdir()
    monkeypatch.setattr(recovery_service, "_RECOVERY_QUALIFIED", True)
    state = boundary.handle("state", {"host": host})
    assert state["restart_capacity"] is False and state["can_recover"] is False
    assert "repair review" in state["message"]
    with pytest.raises(UpdateError):
        boundary.handle("handoff", {
            "host": host, "operation_id": update.identity, "choice": "keep",
            "restart_nonce": state["restart_nonce"],
        })
    assert recovery.handshake.selection.read_bytes() == selected
    assert database.read_bytes() == before
