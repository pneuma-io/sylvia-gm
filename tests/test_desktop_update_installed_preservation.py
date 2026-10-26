"""Installed recovery composition: real SQLite/drafts, inert installer and registration."""

import hashlib
import json
import sqlite3
from contextlib import closing

import pytest

from sylvia_gm.application.desktop_update import checkpoint
from sylvia_gm.application.desktop_update.checkpoint import RESTORE_CONFIRMATION, DataCheckpoint
from sylvia_gm.application.desktop_update.drafts import DraftStore
from sylvia_gm.application.desktop_update.manifest import UpdateError
from sylvia_gm.application.desktop_update.preparation import PreparedUpdate
from sylvia_gm.application.desktop_update.recovery import KEEP_DATA, RecoveryOperation
from sylvia_gm.application.desktop_update.storage import UpdateStore
from tests.test_desktop_update_drafts import snapshot
from tests.test_desktop_update_installed import installation, release
from tests.test_desktop_update_manifest import accept_stub, envelope


def fixture(tmp_path):
    update, old, new, registration, calls, data = installation(tmp_path)
    database = data / "authority.sqlite3"
    database.write_bytes(b"")  # Only the inert fixture file created immediately above.
    with closing(sqlite3.connect(database)) as connection, connection:
        connection.executescript(
            "PRAGMA user_version=1; CREATE TABLE story(value TEXT); "
            "INSERT INTO story VALUES ('original fictional progress');"
        )
    (data / "config.yaml").write_bytes(b"fictional-provider: none\n")
    drafts = DraftStore(data)
    drafts.save(snapshot("dm-fictional"))
    saved = DataCheckpoint(database, update.operation)

    def backup(target):
        with (
            closing(sqlite3.connect(database)) as source,
            closing(sqlite3.connect(target)) as destination,
        ):
            source.backup(destination)

    saved.prepare(backup, lambda: True, update.previous.version.text)
    update.prepare()
    return update, old, new, registration, calls, data, saved, drafts


def hashes(data):
    return {name: hashlib.sha256((data / name).read_bytes()).hexdigest()
            for name in ("authority.sqlite3", "config.yaml", ".desktop-drafts.json")}


def progress(saved, drafts, *, migrate=False):
    with closing(sqlite3.connect(saved.database)) as connection, connection:
        connection.execute("INSERT INTO story VALUES ('newer fictional progress')")
        if migrate:
            connection.execute("ALTER TABLE story ADD COLUMN migrated TEXT")
            connection.execute("PRAGMA user_version=2")
    (saved.database.parent / "config.yaml").write_bytes(b"fictional-provider: still-none\n")
    value = snapshot("dm-fictional")
    value["sessions"][0].update(text="Newer unsent DM action", ooc_text="Newer private OOC note")
    value["sessions"].append(snapshot("builder-fictional")["sessions"][0])
    value["sessions"][1]["questions"][0]["text"] = "Newer active answer"
    drafts.save(value)
    return value


def fail_after_one_file(update, new, calls):
    original = update.run

    def interrupted(artifact, destination):
        calls.append(artifact)
        (destination / "sylvia-gm.exe").write_bytes(new["sylvia-gm.exe"])
        return 1

    update.run = interrupted
    with pytest.raises(UpdateError) as failed:
        update.apply(lambda: True)
    assert failed.value.code == "installer_failed"
    assert json.loads(update._journal.read_bytes())["phase"] == "applying"
    return original


def test_partial_installer_retry_and_compatible_rollback_keep_newer_data_and_drafts(tmp_path):
    update, old, new, _, calls, data, saved, drafts = fixture(tmp_path)
    before = hashes(data)
    update.run = fail_after_one_file(update, new, calls)
    assert hashes(data) == before
    update.apply(lambda: True)
    update.confirm()
    expected_drafts = progress(saved, drafts)
    latest = hashes(data)
    update.rollback(lambda: True, lambda: saved.require_compatible(1))
    update.rollback(lambda: True, lambda: saved.require_compatible(1))
    assert len(calls) == 3  # One failed apply, one explicit retry, one deliberate rollback.
    assert hashes(data) == latest
    assert drafts.load() == expected_drafts
    assert all((update.live / name).read_bytes() == raw for name, raw in old.items())
    assert not (saved.root / "restore.json").exists()


def test_incompatible_restore_failure_and_retry_preserve_latest_drafts_and_retained_data(
    tmp_path, monkeypatch,
):
    update, old, _, _, calls, data, saved, drafts = fixture(tmp_path)
    update.apply(lambda: True)
    expected_drafts = progress(saved, drafts, migrate=True)
    latest = hashes(data)
    with pytest.raises(UpdateError) as choice:
        update.rollback(lambda: True, lambda: saved.require_compatible(1))
    assert choice.value.code == "restore_choice_required"
    assert len(calls) == 1 and hashes(data) == latest
    with pytest.raises(UpdateError):
        saved.restore("yes", lambda: True)
    with pytest.raises(UpdateError):
        saved.restore(RESTORE_CONFIRMATION, lambda: False)
    assert hashes(data) == latest

    original_replace = checkpoint.os.replace
    config = data / "config.yaml"

    def fail_config(source, destination):
        if destination == config:
            raise PermissionError("Controlled locked settings publication")
        return original_replace(source, destination)

    monkeypatch.setattr(checkpoint.os, "replace", fail_config)
    with pytest.raises(PermissionError):
        saved.restore(RESTORE_CONFIRMATION, lambda: True)
    assert drafts.load() == expected_drafts
    assert (
        hashlib.sha256((data / ".desktop-drafts.json").read_bytes()).hexdigest()
        == latest[".desktop-drafts.json"]
    )
    interrupted = json.loads((saved.root / "restore.json").read_bytes())
    assert interrupted["phase"] == "restoring"
    retained = saved.root / "post-checkpoint" / interrupted["generation"]
    assert (
        hashlib.sha256((retained / "config.yaml").read_bytes()).hexdigest()
        == latest["config.yaml"]
    )
    assert len(calls) == 1, "No old installer runs before compatible data is established"
    monkeypatch.setattr(checkpoint.os, "replace", original_replace)
    saved.restore(RESTORE_CONFIRMATION, lambda: True)
    with closing(sqlite3.connect(saved.database)) as connection:
        rows = connection.execute("SELECT value FROM story").fetchall()
        assert rows == [("original fictional progress",)]
    assert config.read_bytes() == b"fictional-provider: none\n"
    receipt = json.loads((saved.root / "restore.json").read_bytes())
    retained = saved.root / "post-checkpoint" / receipt["generation"]
    assert (
        hashlib.sha256((retained / "authority.sqlite3").read_bytes()).hexdigest()
        == latest["authority.sqlite3"]
    )
    assert (
        hashlib.sha256((retained / "config.yaml").read_bytes()).hexdigest()
        == latest["config.yaml"]
    )
    update.rollback(lambda: True, lambda: saved.require_compatible(1))
    update.rollback(lambda: True, lambda: saved.require_compatible(1))
    saved.restore(RESTORE_CONFIRMATION, lambda: True)
    assert len(calls) == 2
    assert drafts.load() == expected_drafts
    assert hashes(data)[".desktop-drafts.json"] == latest[".desktop-drafts.json"]
    assert all((update.live / name).read_bytes() == raw for name, raw in old.items())


@pytest.mark.parametrize("field", ["InstallLocation", "ProductLocation", "UninstallString"])
def test_registration_change_after_partial_apply_cannot_retarget_retry_or_touch_data(
    tmp_path, field,
):
    update, _, new, registration, calls, data, _, _ = fixture(tmp_path)
    update.run = fail_after_one_file(update, new, calls)
    before = hashes(data)
    application = {name: (update.live / name).read_bytes() for name in new}
    registration.row[field] = "C:/another-fictional-copy"
    with pytest.raises(UpdateError):
        update.apply(lambda: True)
    assert len(calls) == 1 and hashes(data) == before
    assert all((update.live / name).read_bytes() == raw for name, raw in application.items())


def test_installed_recovery_orchestrator_never_runs_old_installer_after_failed_data_restore(
    tmp_path, monkeypatch,
):
    initial, old, new, registration, _, data, _, drafts = fixture(tmp_path)
    store = UpdateStore(data / ".application-updates")
    identity, base = store.create()
    new_signed, _, new_artifact, _ = release("0.3.0-alpha.2", "new")
    old_signed, _, old_artifact, _ = release("0.3.0-alpha.1", "old")
    (base / "manifest.json").write_bytes(envelope(new_signed))
    (base / "previous-manifest.json").write_bytes(envelope(old_signed))
    (base / "artifact.verified").write_bytes(new_artifact)
    (base / "previous-installer").mkdir()
    (base / "previous-installer/artifact.verified").write_bytes(old_artifact)
    store.save(identity, "verified", "0.3.0-alpha.2")
    calls = []

    def installer(artifact, destination):
        calls.append(artifact)
        files, version = (new, "0.3.0-2") if artifact == base / "artifact.verified" else (
            old, "0.3.0-1"
        )
        for name, raw in files.items():
            (destination / name).write_bytes(raw)
        registration.row["DisplayVersion"] = version
        return 0

    prepared = PreparedUpdate(
        data, initial.live, identity, "a" * 32, current=initial.previous.version,
        target="windows-x86_64", distribution="installed", verify=accept_stub,
        registration=registration, run_installer=installer,
    )

    def backup(target):
        with (
            closing(sqlite3.connect(data / "authority.sqlite3")) as source,
            closing(sqlite3.connect(target)) as destination,
        ):
            source.backup(destination)

    prepared.prepare(backup, lambda: True)
    prepared.apply(lambda: True)
    recovery = RecoveryOperation(prepared)
    expected = progress(recovery.checkpoint, drafts, migrate=True)
    assert recovery.inspect()["can_keep_data"] is False
    with pytest.raises(UpdateError) as rejected:
        recovery.rollback(KEEP_DATA, lambda: True)
    assert rejected.value.code == "restore_choice_required" and len(calls) == 1
    original_replace = checkpoint.os.replace

    def locked_config(source, destination):
        if destination == data / "config.yaml":
            raise PermissionError("Controlled installed recovery publication failure")
        return original_replace(source, destination)

    monkeypatch.setattr(checkpoint.os, "replace", locked_config)
    with pytest.raises(PermissionError):
        recovery.rollback(RESTORE_CONFIRMATION, lambda: True)
    assert store.load()["phase"] == "rolling_back"
    assert len(calls) == 1 and drafts.load() == expected
    monkeypatch.setattr(checkpoint.os, "replace", original_replace)
    recovery.rollback(RESTORE_CONFIRMATION, lambda: True)
    assert store.load()["phase"] == "rollback_ready" and len(calls) == 2
    latest = progress(recovery.checkpoint, drafts)
    before_retry = hashes(data)
    recovery.rollback(RESTORE_CONFIRMATION, lambda: True)
    assert hashes(data) == before_retry and drafts.load() == latest
    assert len(calls) == 2
