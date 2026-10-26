"""Real task-local aliases must be rejected before any outside directory/file is created."""

import hashlib
import os
import subprocess
from dataclasses import replace

import pytest

from sylvia_gm.application.desktop_update.manifest import PayloadFile, UpdateError
from sylvia_gm.application.desktop_update.replacement import _copy
from sylvia_gm.application.desktop_update.storage import ensure_directory
from tests.test_desktop_update_replacement import upgrade


def alias(link, target):
    assert link.parent == target.parent or link.parent.parent == target.parent
    if os.name == "nt":
        result = subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(link), str(target)],
            capture_output=True, check=False,
        )
        assert result.returncode == 0, "task-local junction creation must succeed"
    else:
        link.symlink_to(target, target_is_directory=True)


def test_directory_creation_checks_closest_existing_ancestor_before_mkdir(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "sentinel").write_bytes(b"unchanged")
    link = tmp_path / "alias"
    alias(link, outside)
    with pytest.raises(UpdateError) as error:
        ensure_directory(link / "newdir" / "nested")
    assert error.value.code == "unsafe_staging"
    assert sorted(path.name for path in outside.iterdir()) == ["sentinel"]
    assert (outside / "sentinel").read_bytes() == b"unchanged"


def test_copy_never_creates_newdir_through_existing_alias(tmp_path):
    source = tmp_path / "source"
    source.write_bytes(b"inert payload")
    row = PayloadFile(
        "copied", source.stat().st_size, hashlib.sha256(source.read_bytes()).hexdigest(),
    )
    outside = tmp_path / "outside"
    outside.mkdir()
    link = tmp_path / "alias"
    alias(link, outside)
    with pytest.raises(UpdateError) as error:
        _copy(source, link / "newdir" / "copied", row)
    assert error.value.code == "unsafe_staging"
    assert list(outside.iterdir()) == []
    assert source.read_bytes() == b"inert payload"


def test_apply_rejects_added_file_parent_alias_before_outside_creation(tmp_path):
    update, _, _, data, other = upgrade(tmp_path)
    raw = b"MZ inert added fixture"
    name = "z-added/newdir/fixture.exe"
    source = update.prepared / name
    source.parent.mkdir(parents=True)
    source.write_bytes(raw)
    update.manifest = replace(
        update.manifest,
        files=(
            *update.manifest.files,
            PayloadFile(name, len(raw), hashlib.sha256(raw).hexdigest()),
        ),
    )
    update.prepare()
    outside = tmp_path / "outside"
    outside.mkdir()
    alias(update.live / "z-added", outside)
    before = (data / "authority.sqlite3").read_bytes()
    with pytest.raises(UpdateError) as error:
        update.apply(lambda: True)
    assert error.value.code == "unsafe_staging"
    assert list(outside.iterdir()) == []
    assert source.read_bytes() == raw
    assert (data / "authority.sqlite3").read_bytes() == before
    assert (other / "keep").read_bytes() == b"other playable"


def test_directory_creation_supports_nested_owned_paths_and_rejects_relative_paths(tmp_path):
    target = tmp_path / "owned" / "nested"
    ensure_directory(target)
    ensure_directory(target)
    assert target.is_dir()
    with pytest.raises(UpdateError):
        ensure_directory(type(target)("relative-unsafe"))
