"""Recovery composition uses inert app files and real isolated SQLite/ownership locks."""

import hashlib
import json
import sqlite3
from contextlib import closing
from types import SimpleNamespace

import pytest

from sylvia_gm.application.desktop_update.checkpoint import (
    RESTORE_CONFIRMATION,
    acquire_update_data_lock,
)
from sylvia_gm.application.desktop_update.helper import recover_and_restart
from sylvia_gm.application.desktop_update.manifest import UpdateError, Version
from sylvia_gm.application.desktop_update.recovery import KEEP_DATA, RecoveryOperation
from sylvia_gm.application.desktop_update.startup import authorize_startup
from sylvia_gm.infrastructure.owner_lock import (
    DatabaseOwnerLock,
    OwnerConflict,
    authority_lock_identity,
)
from tests.test_desktop_update_manifest import accept_stub
from tests.test_desktop_update_preparation import fixture
from tests.test_desktop_update_replacement import assert_tree
from tests.test_desktop_update_startup import prepared


def progress(database, value):
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("INSERT INTO fictional_progress(value) VALUES (?)", (value,))


def values(database):
    with closing(sqlite3.connect(database)) as connection:
        return [row[0] for row in connection.execute("SELECT value FROM fictional_progress")]


def old_identity(update):
    return {
        "format_version": 1, "product": "Sylvia GM", "component": "owner",
        "version": update.current.text, "target": update.target, "storage_compatibility": 1,
    }


def test_compatible_rollback_preserves_new_progress_and_confirms_separate_exact_restart(tmp_path):
    update, original, installation, original_request, database = prepared(tmp_path)
    original_bytes = original.ticket.read_bytes()
    progress(database, "post-update progress")
    config = update.root / "config.yaml"
    config.write_bytes(b"new fictional same-user settings")
    before = database.read_bytes(), config.read_bytes()
    recovery = RecoveryOperation(update)
    assert recovery.inspect()["can_keep_data"] is True
    calls, starts = [], []

    def launch(executable, arguments):
        calls.append((executable, arguments))
        assert executable == update.live / "Sylvia GM.exe"
        assert arguments[0] == "--sylvia-update-recovery-start"
        request = {
            "operation_id": arguments[1], "attempt": arguments[2],
            "nonce": arguments[3], "recovery": True,
        }
        start = authorize_startup(
            update.root, installation, request, running=update.current,
            desktop_version=update.current.text, storage_compatibility=1, verify=accept_stub,
        )
        starts.append(start)
        assert start.confirm() is False
        return SimpleNamespace(poll=lambda: None)

    barrier = SimpleNamespace(all_exited=lambda: True)
    recover_and_restart(
        update, barrier, KEEP_DATA, probe=lambda _: old_identity(update), launch=launch,
    )
    assert starts[0].confirm() is True
    assert update.store.load()["phase"] == "rolled_back"
    assert original.ticket.read_bytes() == original_bytes
    assert original_request["nonce"] != calls[0][1][-1]
    assert (database.read_bytes(), config.read_bytes()) == before
    assert "post-update progress" in values(database)
    assert recovery.handshake.confirmed()
    progress(database, "later player progress")
    later = database.read_bytes()
    # Duplicate recovery or helper delivery cannot launch again or restore later progress.
    recovery.rollback(RESTORE_CONFIRMATION, lambda: True)
    recover_and_restart(
        update, barrier, KEEP_DATA, launch=lambda *_: pytest.fail("duplicate launch"),
    )
    assert database.read_bytes() == later
    assert len(calls) == 1


@pytest.mark.parametrize("compatibility", [1, 2])
def test_incompatible_data_requires_explicit_restore_and_retains_post_checkpoint_bytes(
    tmp_path, compatibility,
):
    update, _, _, _, database = prepared(tmp_path, compatibility=compatibility)
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.execute("ALTER TABLE fictional_progress ADD COLUMN incompatible_semantics TEXT")
    progress(database, "new progress requiring newer schema")
    changed = database.read_bytes()
    recovery = RecoveryOperation(update)
    assert recovery.inspect()["can_keep_data"] is False
    with pytest.raises(UpdateError) as rejected:
        recovery.rollback(KEEP_DATA, lambda: True)
    assert rejected.value.code == "restore_choice_required"
    assert database.read_bytes() == changed
    assert update.store.load()["phase"] == "awaiting_restart"
    assert not recovery.choice.exists()
    recovery.rollback(RESTORE_CONFIRMATION, lambda: True)
    assert update.store.load()["phase"] == "rollback_ready"
    assert values(database) == ["keep saved progress"]
    retained = list((update.stage / "data-checkpoint/post-checkpoint").glob("*/authority.sqlite3"))
    assert len(retained) == 1 and retained[0].read_bytes() == changed
    update.replacement().verify_rollback()


def test_one_data_lock_spans_restore_and_application_rollback(tmp_path, monkeypatch):
    update, _, _, _, database = prepared(tmp_path, compatibility=2)
    progress(database, "new progress")
    replacement = update.replacement()
    original = replacement.rollback
    observations = []

    def checked(barrier, compatible):
        assert values(database) == ["keep saved progress"]
        with pytest.raises(OwnerConflict):
            DatabaseOwnerLock(authority_lock_identity(database))
        observations.append("data remains owned through binary rollback")
        return original(barrier, compatible)

    monkeypatch.setattr(replacement, "rollback", checked)
    RecoveryOperation(update).rollback(RESTORE_CONFIRMATION, lambda: True)
    assert len(observations) == 1
    lock = acquire_update_data_lock(database)
    lock.close()


def test_restore_then_binary_failure_retries_same_choice_without_discarding_retained_data(
    tmp_path, monkeypatch,
):
    update, _, _, _, database = prepared(tmp_path, compatibility=2)
    progress(database, "new progress")
    recovery = RecoveryOperation(update)
    replacement, original = update.replacement(), update.replacement().rollback

    def interrupted(*_):
        raise OSError("controlled interruption after data restore")

    monkeypatch.setattr(replacement, "rollback", interrupted)
    with pytest.raises(OSError):
        recovery.rollback(RESTORE_CONFIRMATION, lambda: True)
    assert update.store.load()["phase"] == "rolling_back"
    retained = {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (update.stage / "data-checkpoint/post-checkpoint").rglob("*") if path.is_file()
    }
    before = database.read_bytes()
    with pytest.raises(UpdateError) as changed:
        recovery.rollback(KEEP_DATA, lambda: True)
    assert changed.value.code == "changed_recovery_choice"
    assert database.read_bytes() == before
    monkeypatch.setattr(replacement, "rollback", original)
    recovery.rollback(RESTORE_CONFIRMATION, lambda: True)
    assert update.store.load()["phase"] == "rollback_ready"
    assert retained == {
        str(path): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in (update.stage / "data-checkpoint/post-checkpoint").rglob("*") if path.is_file()
    }


def test_pre_apply_recovery_and_busy_owner_never_discard_data(tmp_path):
    update, _, backup, old, _, database = fixture(tmp_path)
    update.prepare(backup, lambda: True)
    recovery = RecoveryOperation(update)
    before = database.read_bytes()
    with pytest.raises(UpdateError) as busy:
        recovery.rollback(KEEP_DATA, lambda: False)
    assert busy.value.code == "application_busy"
    lock = DatabaseOwnerLock(authority_lock_identity(database))
    try:
        with pytest.raises(OwnerConflict):
            recovery.rollback(RESTORE_CONFIRMATION, lambda: True)
        with pytest.raises(OwnerConflict):
            recovery.inspect()
        assert not recovery.choice.exists()
        assert update.store.load()["phase"] == "prepared"
    finally:
        lock.close()
    recovery.rollback(KEEP_DATA, lambda: True)
    assert database.read_bytes() == before
    assert_tree(update.live, old)


def test_ambiguous_recovery_launch_never_automatically_repeats(tmp_path):
    update, _, _, _, _ = prepared(tmp_path)
    recovery = RecoveryOperation(update)
    recovery.rollback(KEEP_DATA, lambda: True)
    handshake = recovery.create_restart(old_identity(update))
    with pytest.raises(UpdateError):
        handshake.launch_once(lambda _: (_ for _ in ()).throw(OSError("controlled failed launch")))
    for _ in range(2):
        with pytest.raises(UpdateError) as rejected:
            recover_and_restart(
                update, SimpleNamespace(all_exited=lambda: True), KEEP_DATA,
                launch=lambda *_: pytest.fail("must not repeat ambiguous launch"),
            )
        assert rejected.value.code == "recovery_required"
    assert update.store.load()["phase"] == "rollback_awaiting_restart"


@pytest.mark.parametrize("phase", ["rolling_back", "rollback_ready", "rollback_awaiting_restart"])
def test_ordinary_boot_is_fenced_before_migration_through_all_recovery_phases(tmp_path, phase):
    update, _, installation, _, database = prepared(tmp_path)
    update.store.save(update.identity, phase, update.manifest.version.text)
    before = database.read_bytes()
    with pytest.raises(UpdateError) as blocked:
        authorize_startup(
            update.root, installation, None, running=Version.parse("0.3.0-alpha.1"),
            desktop_version="0.3.0-alpha.1", storage_compatibility=1, verify=accept_stub,
        )
    assert blocked.value.code == "update_recovery_required"
    assert database.read_bytes() == before


def test_changed_restored_payload_cannot_acknowledge_or_start_owner(tmp_path):
    update, _, installation, _, database = prepared(tmp_path)
    recovery = RecoveryOperation(update)
    recovery.rollback(KEEP_DATA, lambda: True)
    handshake = recovery.create_restart(old_identity(update))
    arguments = []
    handshake.launch_once(lambda argv: arguments.extend(argv))
    (update.live / "sylvia-gm-owner.exe").write_bytes(b"changed inert app")
    before = database.read_bytes()
    with pytest.raises(UpdateError):
        authorize_startup(
            update.root, installation,
            {"operation_id": update.identity, "attempt": update.attempt,
             "nonce": arguments[-1], "recovery": True},
            running=update.current, desktop_version=update.current.text,
            storage_compatibility=1, verify=accept_stub,
        )
    assert database.read_bytes() == before
    assert not handshake.receipt.exists()
    assert json.loads(handshake.ticket.read_bytes())["phase"] == "launched"


def test_old_success_receipt_cannot_reverse_an_explicit_recovery_decision(tmp_path):
    update, handshake, installation, request, _ = prepared(tmp_path)
    start = authorize_startup(
        update.root, installation, request, running=update.manifest.version,
        desktop_version=update.manifest.version.text, storage_compatibility=1, verify=accept_stub,
    )
    assert start.confirm() is False and handshake.confirmed()
    update.confirm()
    recovery = RecoveryOperation(update)
    recovery.rollback(KEEP_DATA, lambda: True)
    assert update.store.load()["phase"] == "rollback_ready"
    for stale in [update.confirm, lambda: update.apply(lambda: True), recovery.confirm]:
        with pytest.raises(UpdateError) as rejected:
            stale()
        assert rejected.value.code == "invalid_phase"
        assert update.store.load()["phase"] == "rollback_ready"
