"""Use the actual authority lock and pause startup across a changed updater selection."""

import io
import json

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.desktop_update.checkpoint import (
    RESTORE_CONFIRMATION,
    DataCheckpoint,
    acquire_update_data_lock,
)
from sylvia_gm.application.desktop_update.manifest import UpdateError
from sylvia_gm.application.desktop_update.recovery import RecoveryOperation
from sylvia_gm.application.desktop_update.storage import UpdateStore
from sylvia_gm.infrastructure.owner_lock import OwnerConflict
from sylvia_gm.infrastructure.sqlite_authority import SQLiteAuthorityStore
from sylvia_gm.interfaces import owner_process
from tests.test_desktop_update_preparation import fixture
from tests.test_desktop_update_replacement import assert_tree


def test_real_authority_owner_excludes_update_apply_restore_and_rollback(tmp_path):
    update, _, backup, old, _, database = fixture(tmp_path, authority_database=True)
    owner = AuthorityOwner.open(database, "live-fixture-owner")
    try:
        update.prepare(backup, lambda: True)
        before = database.read_bytes()
        for action in [
            lambda: update.apply(lambda: True),
            lambda: DataCheckpoint(database, update.stage).restore(
                RESTORE_CONFIRMATION, lambda: True,
            ),
            lambda: RecoveryOperation(update).rollback(RESTORE_CONFIRMATION, lambda: True),
        ]:
            with pytest.raises(OwnerConflict):
                action()
            assert database.read_bytes() == before
            assert_tree(update.live, old)
            assert update.store.load()["phase"] == "prepared"
    finally:
        owner.close()
    # The exact same helper lock can acquire only once the genuine owner has closed.
    lock = acquire_update_data_lock(database)
    lock.close()


def test_before_migration_validation_holds_actual_owner_lock_and_releases_on_rejection(
    tmp_path, monkeypatch,
):
    database = tmp_path / "authority.sqlite3"
    database.write_bytes(b"must not become a SQLite connection")
    calls = []

    def reject_before_sql():
        with pytest.raises(OwnerConflict):
            acquire_update_data_lock(database)
        calls.append("validated under actual ownership")
        raise UpdateError("stale_restart", "The selected restart changed.")

    monkeypatch.setattr(SQLiteAuthorityStore, "_migrate", lambda _: pytest.fail("no SQL migration"))
    with pytest.raises(UpdateError) as error:
        AuthorityOwner.open(database, "fixture-paused-startup", before_migration=reject_before_sql)
    assert error.value.code == "stale_restart"
    assert calls == ["validated under actual ownership"]
    assert database.read_bytes() == b"must not become a SQLite connection"
    lock = acquire_update_data_lock(database)
    lock.close()


@pytest.mark.parametrize("journal_existed", [True, False])
def test_owner_revalidates_selection_after_initial_check_and_before_migration(
    tmp_path, monkeypatch, journal_existed,
):
    database = tmp_path / "authority.sqlite3"
    database.write_bytes(b"preserve this exact pre-migration data")
    root = tmp_path / ".application-updates"
    if journal_existed:
        store = UpdateStore(root)
        identity, _ = store.create()
        store.save(identity, "prepared", "0.3.0-alpha.2")
    original = owner_process._launch
    calls = []

    def paused(envelope, **kwargs):
        store = UpdateStore(root)
        row = store.load()
        identity = row["operation_id"] if row is not None else store.create()[0]
        store.save(identity, "closing", "0.3.0-alpha.2")
        calls.append("state changed after initial check")
        return original(envelope, **kwargs)

    monkeypatch.setattr(owner_process, "_launch", paused)
    monkeypatch.setattr(
        SQLiteAuthorityStore, "_migrate", lambda _: pytest.fail("stale SQL migration"),
    )
    launch = {"type": "launch", "database_path": str(database), "secret_hex": "a1" * 32,
              "owner_id": "fixture-stale-startup"}
    output = io.StringIO()
    assert owner_process.run(io.StringIO(json.dumps(launch) + "\n"), output) == 2
    assert calls == ["state changed after initial check"]
    assert json.loads(output.getvalue())["type"] == "update_recovery_required"
    assert database.read_bytes() == b"preserve this exact pre-migration data"
    lock = acquire_update_data_lock(database)
    lock.close()
