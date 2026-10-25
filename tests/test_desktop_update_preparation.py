"""Inert portable preparation/apply composition with real SQLite backup, no executable launch."""

import hashlib
import io
import os
import sqlite3
import zipfile
from contextlib import closing

import pytest

from sylvia_gm.application.desktop_update.manifest import UpdateError, Version
from sylvia_gm.application.desktop_update.preparation import PreparedUpdate, new_attempt
from sylvia_gm.application.desktop_update.storage import UpdateStore
from tests.test_desktop_update_manifest import accept_stub, envelope, manifest_fixture
from tests.test_desktop_update_replacement import assert_tree, write_tree


def fixture(tmp_path, *, authority_database=False):
    live = tmp_path / "portable-copy"
    old = write_tree(live, "old")
    root = live / "isolated-data"
    root.mkdir()
    database = root / "authority.sqlite3"
    if authority_database:
        from sylvia_gm.application.authority import AuthorityOwner

        AuthorityOwner.open(database, "fixture-authority").close()
    else:
        with closing(sqlite3.connect(database)) as connection, connection:
            connection.execute("CREATE TABLE fictional_progress(value TEXT)")
            connection.execute("INSERT INTO fictional_progress VALUES ('keep saved progress')")
    (root / "config.yaml").write_bytes(b"fictional protected settings fixture")
    store = UpdateStore(root / ".application-updates")
    identity, stage = store.create()
    new = write_tree(tmp_path / "inert-new-payload", "new")
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, value in new.items():
            archive.writestr(name, value)
    signed = manifest_fixture()
    signed["files"] = [
        {"path": name, "size": len(value), "sha256": hashlib.sha256(value).hexdigest()}
        for name, value in new.items()
    ]
    signed["artifact"].update(
        size=len(raw.getvalue()), sha256=hashlib.sha256(raw.getvalue()).hexdigest()
    )
    (stage / "artifact.verified").write_bytes(raw.getvalue())
    (stage / "manifest.json").write_bytes(envelope(signed))
    store.save(identity, "verified", "0.3.0-alpha.2")

    def backup(target):
        with closing(sqlite3.connect(database)) as source, closing(sqlite3.connect(target)) as out:
            source.backup(out)

    def create(attempt=None):
        return PreparedUpdate(
            root,
            live,
            identity,
            attempt or new_attempt(),
            current=Version.parse("0.3.0-alpha.1"),
            target="windows-x86_64",
            distribution="portable",
            verify=accept_stub,
        )

    return create(), create, backup, old, new, database


def test_prepare_barrier_apply_restart_confirmation_compose_without_touching_saved_data(tmp_path):
    update, _, backup, old, new, database = fixture(tmp_path)
    before = database.read_bytes()
    update.prepare(backup, lambda: True)
    assert_tree(update.live, old)
    with pytest.raises(UpdateError) as busy:
        update.apply(lambda: False)
    assert busy.value.code == "application_busy"
    assert update.store.load()["phase"] == "prepared"
    update.apply(lambda: True)
    assert_tree(update.live, new)
    assert_tree(update.stage / "previous", old)
    assert database.read_bytes() == before
    with pytest.raises(UpdateError):
        update.confirm()
    handshake = update.create_restart(
        {
            "format_version": 1,
            "product": "Sylvia GM",
            "component": "owner",
            "version": "0.3.0-alpha.2",
            "target": "windows-x86_64",
            "storage_compatibility": 1,
        }
    )
    arguments = []
    handshake.launch_once(lambda argv: arguments.extend(argv) or object())
    handshake.confirm(
        nonce=arguments[-1],
        live=update.live,
        desktop_version="0.3.0-alpha.2",
        owner_version="0.3.0-alpha.2",
        target="windows-x86_64",
        distribution="portable",
        storage_compatibility=1,
    )
    update.confirm()
    update.confirm()
    assert update.store.load()["phase"] == "confirmed"
    assert database.read_bytes() == before
    with pytest.raises(UpdateError):
        update.apply(lambda: True)
    assert update.store.load()["phase"] == "confirmed"


def test_failed_attempt_retries_with_fresh_checkpoint_and_fences_old_attempt(tmp_path):
    update, create, backup, old, _, database = fixture(tmp_path)

    def failed_backup(_target):
        raise OSError("controlled backup failure")

    with pytest.raises(OSError):
        update.prepare(failed_backup, lambda: True)
    assert update.store.load()["phase"] == "verified"
    assert_tree(update.live, old)
    retried = create()
    retried.prepare(backup, lambda: True)
    assert retried.attempt != update.attempt and update.stage.exists()
    with pytest.raises(UpdateError):
        update.require_selected()
    assert retried.store.load()["phase"] == "prepared"
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT value FROM fictional_progress").fetchone() == (
            "keep saved progress",
        )


def test_busy_preparation_or_wrong_owner_identity_never_changes_live_copy(tmp_path):
    update, _, backup, old, _, _ = fixture(tmp_path)
    with pytest.raises(UpdateError):
        update.prepare(backup, lambda: False)
    assert not update.stage.exists() and update.store.load()["phase"] == "verified"
    update.prepare(backup, lambda: True)
    with pytest.raises(UpdateError):
        update.create_restart({"version": "wrong"})
    assert_tree(update.live, old)


def test_aliased_data_ownership_lock_cannot_write_another_file_or_begin_replacement(tmp_path):
    update, _, backup, old, _, _ = fixture(tmp_path)
    update.prepare(backup, lambda: True)
    foreign = tmp_path / "other-owned-empty-file"
    foreign.write_bytes(b"")
    from sylvia_gm.infrastructure.owner_lock import authority_lock_identity

    identity = authority_lock_identity(update.root / "authority.sqlite3")
    os.link(foreign, identity.with_name(identity.name + ".owner.lock"))
    with pytest.raises(UpdateError) as rejected:
        update.apply(lambda: True)
    assert rejected.value.code == "unsafe_data_lock"
    assert foreign.read_bytes() == b""
    assert_tree(update.live, old)
    assert update.store.load()["phase"] == "prepared"


def test_prepared_helper_is_retained_old_owner_and_cannot_be_swapped_before_apply(tmp_path):
    update, _, backup, old, _, _ = fixture(tmp_path)
    update.prepare(backup, lambda: True)
    helper = update.stage / "helper/sylvia-gm-owner.exe"
    assert helper.read_bytes() == old["sylvia-gm-owner.exe"]
    helper.write_bytes(b"swapped helper")
    with pytest.raises(UpdateError) as rejected:
        update.apply(lambda: True)
    assert rejected.value.code == "changed_helper"
    assert_tree(update.live, old)
    assert update.store.load()["phase"] == "prepared"
