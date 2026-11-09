"""SQLite recovery components; real authority backup and native gates remain separate."""

import hashlib
import json
import os
import sqlite3
import stat
import subprocess
import sys
from types import SimpleNamespace

import pytest

from sylvia_gm.application.desktop_update import checkpoint
from sylvia_gm.application.desktop_update.checkpoint import RESTORE_CONFIRMATION, DataCheckpoint
from sylvia_gm.application.desktop_update.manifest import UpdateError
from sylvia_gm.domain.errors import OwnerConflict
from sylvia_gm.infrastructure.owner_lock import DatabaseOwnerLock, authority_lock_identity


def setup_checkpoint(tmp_path):
    data = tmp_path / "data"
    operation = tmp_path / "operation"
    data.mkdir()
    operation.mkdir()
    database = data / "authority.sqlite3"
    connection = sqlite3.connect(database)
    connection.executescript(
        "PRAGMA user_version=1; CREATE TABLE story (value TEXT); "
        "INSERT INTO story VALUES ('original fictional story');"
    )
    connection.close()
    config = data / "config.yaml"
    config.write_bytes(b"original protected credential fixture")
    update = DataCheckpoint(database, operation)

    def backup(target):
        source = sqlite3.connect(database)
        destination = sqlite3.connect(target)
        try:
            source.backup(destination)
        finally:
            destination.close()
            source.close()

    return update, backup, config


def story(database):
    connection = sqlite3.connect(database)
    try:
        return [row[0] for row in connection.execute("SELECT value FROM story")]
    finally:
        connection.close()


def add_progress(database, *, migrate=False):
    connection = sqlite3.connect(database)
    try:
        connection.execute("INSERT INTO story VALUES ('post-update progress')")
        if migrate:
            connection.execute("ALTER TABLE story ADD COLUMN added TEXT")
            connection.execute("PRAGMA user_version=2")
        connection.commit()
    finally:
        connection.close()


def test_active_work_rejects_before_backup_or_staging_mutation(tmp_path):
    update, _, config = setup_checkpoint(tmp_path)
    before = update.database.read_bytes(), config.read_bytes()
    with pytest.raises(UpdateError) as blocked:
        update.prepare(
            lambda _target: pytest.fail("backup must not start"), lambda: False, "0.3.0-alpha.1"
        )
    assert blocked.value.code == "application_busy"
    assert not update.root.exists()
    assert (update.database.read_bytes(), config.read_bytes()) == before


def test_checkpoint_delegates_coherent_backup_and_keeps_settings_private(tmp_path):
    update, backup, config = setup_checkpoint(tmp_path)
    update.prepare(backup, lambda: True, "0.3.0-alpha.1")
    assert story(update.root / "authority.sqlite3") == ["original fictional story"]
    assert (update.root / "config.yaml").read_bytes() == config.read_bytes()
    receipt = (update.root / "checkpoint.json").read_text()
    assert "original fictional story" not in receipt
    assert "original protected credential fixture" not in receipt
    update.require_compatible(1)


def test_compatible_schema_keeps_progress_but_new_storage_contract_requires_explicit_choice(
    tmp_path,
):
    update, backup, config = setup_checkpoint(tmp_path)
    update.prepare(backup, lambda: True, "0.3.0-alpha.1")
    add_progress(update.database)
    config.write_bytes(b"new protected credential fixture")
    update.require_compatible(1)
    before = update.database.read_bytes(), config.read_bytes()
    with pytest.raises(UpdateError) as choice:
        update.require_compatible(2)
    assert choice.value.code == "restore_choice_required"
    with pytest.raises(UpdateError):
        update.restore("yes", lambda: True)
    assert (update.database.read_bytes(), config.read_bytes()) == before


def test_incompatible_restore_retains_post_checkpoint_data_and_is_idempotent(tmp_path):
    update, backup, config = setup_checkpoint(tmp_path)
    update.prepare(backup, lambda: True, "0.3.0-alpha.1")
    add_progress(update.database, migrate=True)
    config.write_bytes(b"new protected credential fixture")
    new_db, new_config = update.database.read_bytes(), config.read_bytes()
    with pytest.raises(UpdateError):
        update.require_compatible(1)
    update.restore(RESTORE_CONFIRMATION, lambda: True)
    assert story(update.database) == ["original fictional story"]
    assert config.read_bytes() == b"original protected credential fixture"
    receipt = json.loads((update.root / "restore.json").read_bytes())
    retained = update.root / "post-checkpoint" / receipt["generation"]
    assert (retained / update.database.name).read_bytes() == new_db
    assert (retained / "config.yaml").read_bytes() == new_config
    add_progress(update.database)
    config.write_bytes(b"settings changed after successful recovery")
    latest = update.database.read_bytes(), config.read_bytes()
    update.restore(RESTORE_CONFIRMATION, lambda: True)
    assert (update.database.read_bytes(), config.read_bytes()) == latest


def test_interrupted_restore_resumes_saved_phases(tmp_path, monkeypatch):
    update, backup, config = setup_checkpoint(tmp_path)
    update.prepare(backup, lambda: True, "0.3.0-alpha.1")
    add_progress(update.database, migrate=True)
    config.write_bytes(b"post-checkpoint settings")
    original = checkpoint.os.replace

    def fail_config_publish(source, destination):
        if destination == config:
            raise OSError("controlled interruption before settings publication")
        return original(source, destination)

    monkeypatch.setattr(checkpoint.os, "replace", fail_config_publish)
    with pytest.raises(OSError):
        update.restore(RESTORE_CONFIRMATION, lambda: True)
    assert json.loads((update.root / "restore.json").read_bytes())["phase"] == "restoring"
    monkeypatch.setattr(checkpoint.os, "replace", original)
    resumed = DataCheckpoint(update.database, update.operation)
    resumed.restore(RESTORE_CONFIRMATION, lambda: True)
    assert story(update.database) == ["original fictional story"]
    assert config.read_bytes() == b"original protected credential fixture"


def test_new_manual_settings_during_interrupted_restore_are_never_overwritten(
    tmp_path, monkeypatch
):
    update, backup, config = setup_checkpoint(tmp_path)
    update.prepare(backup, lambda: True, "0.3.0-alpha.1")
    add_progress(update.database, migrate=True)
    original = checkpoint.os.replace

    def fail_config_publish(source, destination):
        if destination == config:
            raise OSError("controlled interruption")
        return original(source, destination)

    monkeypatch.setattr(checkpoint.os, "replace", fail_config_publish)
    with pytest.raises(OSError):
        update.restore(RESTORE_CONFIRMATION, lambda: True)
    monkeypatch.setattr(checkpoint.os, "replace", original)
    config.write_bytes(b"independent manual recovery settings")
    with pytest.raises(UpdateError) as rejected:
        update.restore(RESTORE_CONFIRMATION, lambda: True)
    assert rejected.value.code == "checkpoint_changed"
    assert config.read_bytes() == b"independent manual recovery settings"


def test_committed_wal_from_unclean_exit_is_retained_before_data_restore(tmp_path):
    update, backup, config = setup_checkpoint(tmp_path)
    update.prepare(backup, lambda: True, "0.3.0-alpha.1")
    subprocess.run(
        [
            sys.executable,
            "-c",
            "import os,sqlite3,sys; c=sqlite3.connect(sys.argv[1]); "
            "c.execute('PRAGMA journal_mode=WAL'); "
            "c.execute(\"INSERT INTO story VALUES ('committed WAL progress')\"); "
            "c.commit(); os._exit(0)",
            str(update.database),
        ],
        check=True,
    )
    wal = update.database.with_name(update.database.name + "-wal")
    assert wal.is_file()
    raw_wal = wal.read_bytes()
    update.restore(RESTORE_CONFIRMATION, lambda: True)
    receipt = json.loads((update.root / "restore.json").read_bytes())
    retained = update.root / "post-checkpoint" / receipt["generation"]
    assert (retained / wal.name).read_bytes() == raw_wal
    assert not wal.exists()
    assert story(update.database) == ["original fictional story"]
    assert config.read_bytes() == b"original protected credential fixture"


def test_live_owner_lock_refuses_restore_even_if_a_caller_claims_process_exit(tmp_path):
    update, backup, config = setup_checkpoint(tmp_path)
    update.prepare(backup, lambda: True, "0.3.0-alpha.1")
    before = update.database.read_bytes(), config.read_bytes()
    live_owner = DatabaseOwnerLock(authority_lock_identity(update.database))
    try:
        with pytest.raises(OwnerConflict):
            update.restore(RESTORE_CONFIRMATION, lambda: True)
    finally:
        live_owner.close()
    assert (update.database.read_bytes(), config.read_bytes()) == before


def test_tampered_checkpoint_never_becomes_a_restore_source(tmp_path):
    update, backup, config = setup_checkpoint(tmp_path)
    update.prepare(backup, lambda: True, "0.3.0-alpha.1")
    (update.root / "config.yaml").write_bytes(b"tampered")
    before = hashlib.sha256(update.database.read_bytes()).hexdigest(), config.read_bytes()
    with pytest.raises(UpdateError) as rejected:
        update.restore(RESTORE_CONFIRMATION, lambda: True)
    assert rejected.value.code == "invalid_checkpoint"
    assert (hashlib.sha256(update.database.read_bytes()).hexdigest(), config.read_bytes()) == before


def test_oversized_settings_are_rejected_before_reading_their_contents(tmp_path, monkeypatch):
    update, backup, config = setup_checkpoint(tmp_path)
    original = type(config).lstat

    def observed_size(path, *args, **kwargs):
        if path == config:
            return SimpleNamespace(st_size=9 * 1024 * 1024)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(config), "lstat", observed_size)
    with pytest.raises(UpdateError) as rejected:
        update.prepare(backup, lambda: True, "0.3.0-alpha.1")
    assert rejected.value.code == "checkpoint_limit"


def test_reparse_database_is_rejected_before_sqlite_open(tmp_path, monkeypatch):
    update, _, _ = setup_checkpoint(tmp_path)
    original = type(update.database).lstat

    def reparse(path, *args, **kwargs):
        if path == update.database:
            return SimpleNamespace(st_mode=stat.S_IFREG, st_nlink=1, st_file_attributes=0x400)
        return original(path, *args, **kwargs)

    monkeypatch.setattr(type(update.database), "lstat", reparse)
    monkeypatch.setattr(
        checkpoint.sqlite3,
        "connect",
        lambda *_args, **_kwargs: pytest.fail("must not open aliased DB"),
    )
    with pytest.raises(UpdateError) as rejected:
        checkpoint.schema_identity(update.database)
    assert rejected.value.code == "unsafe_checkpoint"


def test_aliased_restore_lock_cannot_append_to_another_owned_file(tmp_path):
    update, backup, _ = setup_checkpoint(tmp_path)
    update.prepare(backup, lambda: True, "0.3.0-alpha.1")
    foreign = tmp_path / "other-empty-fixture"
    foreign.write_bytes(b"")
    identity = authority_lock_identity(update.database)
    os.link(foreign, identity.with_name(identity.name + ".owner.lock"))
    before = update.database.read_bytes()
    with pytest.raises(UpdateError) as rejected:
        update.restore(RESTORE_CONFIRMATION, lambda: True)
    assert rejected.value.code == "unsafe_data_lock"
    assert foreign.read_bytes() == b""
    assert update.database.read_bytes() == before
    assert not (update.root / "restore.json").exists()
