"""Installed app and recovery storage can live on different volumes."""

import errno
import hashlib
from dataclasses import replace
from pathlib import Path

import pytest

from sylvia_gm.application.desktop_update import installed
from sylvia_gm.application.desktop_update.manifest import PayloadFile, UpdateError
from tests.test_desktop_update_installed import installation


def selected(tmp_path):
    update, old, new, _, calls, data = installation(tmp_path)
    old["licenses/old-only.txt"] = b"obsolete signed old resource"
    new["licenses/new-only.txt"] = b"new signed resource"
    for name, raw in old.items():
        path = update.live / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)

    def rows(files):
        return tuple(PayloadFile(name, len(raw), hashlib.sha256(raw).hexdigest())
                     for name, raw in files.items())

    update.previous = replace(update.previous, files=rows(old))
    update.manifest = replace(update.manifest, files=rows(new))
    return update, old, new, calls, data


def test_retirement_and_rollback_do_not_rename_from_install_to_recovery_volume(
    tmp_path, monkeypatch,
):
    update, old, new, calls, data = selected(tmp_path)
    original = installed.os.replace

    def cross_volume(source, destination):
        if (
            Path(source).is_relative_to(update.live)
            and Path(destination).is_relative_to(update.operation)
        ):
            raise OSError(errno.EXDEV, "Simulated distinct installation and recovery volumes")
        return original(source, destination)

    monkeypatch.setattr(installed.os, "replace", cross_volume)
    update.prepare()
    update.apply(lambda: True)
    update.apply(lambda: True)
    assert len(calls) == 1
    assert (update.operation / "retired/licenses/old-only.txt").read_bytes() == (
        old["licenses/old-only.txt"]
    )
    assert not (update.live / "licenses/old-only.txt").exists()
    update.rollback(lambda: True, lambda: None)
    update.rollback(lambda: True, lambda: None)
    update.verify_rollback()
    assert len(calls) == 2
    assert (update.operation / "failed-new/licenses/new-only.txt").read_bytes() == (
        new["licenses/new-only.txt"]
    )
    assert not (update.live / "licenses/new-only.txt").exists()
    assert (update.live / "manual.txt").read_bytes() == b"unrelated user file"
    assert (data / "authority.sqlite3").read_bytes() == b"fictional user data and exact pins"


def test_retained_copy_before_source_unlink_is_retryable_without_reinstall(tmp_path, monkeypatch):
    update, old, _, calls, _ = selected(tmp_path)
    target = update.live / "licenses/old-only.txt"
    original = Path.unlink
    failed = False

    def interrupted(path, *args, **kwargs):
        nonlocal failed
        if path == target and not failed:
            failed = True
            raise PermissionError("Controlled source unlink interruption")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "unlink", interrupted)
    update.prepare()
    with pytest.raises(PermissionError):
        update.apply(lambda: True)
    retained = update.operation / "retired/licenses/old-only.txt"
    assert retained.read_bytes() == target.read_bytes() == old["licenses/old-only.txt"]
    update.apply(lambda: True)
    assert len(calls) == 1 and not target.exists()
    assert retained.read_bytes() == old["licenses/old-only.txt"]


@pytest.mark.parametrize("mutation", ["manual-source", "active-process"])
def test_post_copy_changes_preserve_source_and_verified_retained_copy(
    tmp_path, monkeypatch, mutation,
):
    update, old, _, calls, _ = selected(tmp_path)
    source = update.live / "licenses/old-only.txt"
    retained = update.operation / "retired/licenses/old-only.txt"
    original = installed.os.replace
    published = False

    def publish(first, second):
        nonlocal published
        result = original(first, second)
        if Path(second) == retained:
            published = True
            if mutation == "manual-source":
                source.write_bytes(b"manual change after verified copy")
        return result

    monkeypatch.setattr(installed.os, "replace", publish)
    update.prepare()
    with pytest.raises(UpdateError) as rejected:
        update.apply(lambda: mutation != "active-process" or not published)
    assert rejected.value.code == (
        "application_busy" if mutation == "active-process" else "application_changed"
    )
    assert len(calls) == 1
    assert retained.read_bytes() == old["licenses/old-only.txt"]
    assert source.read_bytes() == (
        b"manual change after verified copy"
        if mutation == "manual-source" else old["licenses/old-only.txt"]
    )


def test_existing_retained_manual_file_is_preserved(tmp_path):
    update, old, _, _, _ = selected(tmp_path)
    retained = update.operation / "retired/licenses/old-only.txt"
    retained.parent.mkdir(parents=True)
    retained.write_bytes(b"manual retained file")
    update.prepare()
    with pytest.raises(UpdateError) as rejected:
        update.apply(lambda: True)
    assert rejected.value.code == "staging_exists"
    assert retained.read_bytes() == b"manual retained file"
    assert (update.live / "licenses/old-only.txt").read_bytes() == old["licenses/old-only.txt"]


def test_recovery_volume_space_is_checked_before_retirement_copy(tmp_path, monkeypatch):
    update, old, _, _, _ = selected(tmp_path)
    original = installed.shutil.disk_usage

    def space(path):
        value = original(path)
        return value._replace(free=0) if Path(path) == update.operation else value

    monkeypatch.setattr(installed.shutil, "disk_usage", space)
    update.prepare()
    with pytest.raises(UpdateError) as rejected:
        update.apply(lambda: True)
    assert rejected.value.code == "disk_full"
    assert (update.live / "licenses/old-only.txt").read_bytes() == old["licenses/old-only.txt"]
    assert not (update.operation / "retired/licenses/old-only.txt").exists()


def test_interrupted_retirement_copy_preserves_source_and_retries(tmp_path, monkeypatch):
    update, old, _, calls, _ = selected(tmp_path)
    original = installed._copy
    failed = False

    def interrupted(source, destination, row):
        nonlocal failed
        if row.path == "licenses/old-only.txt" and not failed:
            failed = True
            destination.write_bytes(b"partial copy")
            raise OSError("Controlled interrupted retirement copy")
        return original(source, destination, row)

    monkeypatch.setattr(installed, "_copy", interrupted)
    update.prepare()
    with pytest.raises(OSError, match="Controlled interrupted"):
        update.apply(lambda: True)
    source = update.live / "licenses/old-only.txt"
    retained = update.operation / "retired/licenses/old-only.txt"
    assert source.read_bytes() == old["licenses/old-only.txt"] and not retained.exists()
    update.apply(lambda: True)
    assert len(calls) == 1 and not source.exists()
    assert retained.read_bytes() == old["licenses/old-only.txt"]
