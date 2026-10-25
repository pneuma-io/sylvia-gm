"""Small inert portable fixtures test extraction and preservation, without executing binaries."""

import hashlib
import io
import stat
import zipfile
from dataclasses import replace

import pytest

from sylvia_gm.application.desktop_update.manifest import PayloadFile, UpdateError
from sylvia_gm.application.desktop_update.payload import extract_portable, verify_artifact
from tests.test_desktop_update_manifest import manifest_fixture, parse


def portable_fixture(tmp_path, mutate=None):
    manifest = parse(manifest_fixture())
    data = {
        row.path: b"MZ inert fixture" if row.path.endswith(".exe") else b"notice fixture"
        for row in manifest.files
    }
    data["sylvia-portable.json"] = b'{"version":1}\n'
    entries = list(data.items())
    if mutate is not None:
        entries = mutate(entries)
    raw = io.BytesIO()
    with zipfile.ZipFile(raw, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries:
            if isinstance(name, zipfile.ZipInfo):
                archive.writestr(name, value)
            else:
                archive.writestr(name, value)
    artifact = tmp_path / "artifact.verified"
    artifact.write_bytes(raw.getvalue())
    manifest = replace(
        manifest,
        artifact_size=len(raw.getvalue()),
        artifact_sha256=hashlib.sha256(raw.getvalue()).hexdigest(),
        files=tuple(
            PayloadFile(name, len(value), hashlib.sha256(value).hexdigest())
            for name, value in data.items()
        ),
    )
    return artifact, manifest, data


def test_exact_signed_inventory_prepares_only_a_separate_portable_tree(tmp_path):
    live = tmp_path / "live"
    live.mkdir()
    (live / "Sylvia GM.exe").write_bytes(b"existing playable")
    isolated = live / "isolated-data"
    isolated.mkdir()
    (isolated / "authority.sqlite3").write_bytes(b"player data")
    artifact, manifest, data = portable_fixture(tmp_path)
    destination = extract_portable(artifact, manifest, tmp_path / "prepared")
    assert {
        path.relative_to(destination).as_posix(): path.read_bytes()
        for path in destination.rglob("*")
        if path.is_file()
    } == data
    assert (live / "Sylvia GM.exe").read_bytes() == b"existing playable"
    assert (isolated / "authority.sqlite3").read_bytes() == b"player data"


def symlink_entry(entries):
    entry = zipfile.ZipInfo(entries[0][0])
    entry.create_system = 3
    entry.external_attr = (stat.S_IFLNK | 0o777) << 16
    return [(entry, entries[0][1]), *entries[1:]]


@pytest.mark.parametrize(
    "mutate",
    [
        lambda rows: [("../outside.exe", b"escape"), *rows],
        lambda rows: [("isolated-data/authority.sqlite3", b"overwrite"), *rows],
        lambda rows: [("unknown.exe", b"unlisted"), *rows],
        lambda rows: rows[1:],
        lambda rows: [(rows[0][0], b"altered"), *rows[1:]],
        symlink_entry,
    ],
)
def test_unsafe_or_changed_archive_never_touches_live_tree(tmp_path, mutate):
    artifact, manifest, _ = portable_fixture(tmp_path, mutate)
    with pytest.raises(UpdateError):
        extract_portable(artifact, manifest, tmp_path / "prepared")
    assert not (tmp_path / "outside.exe").exists()
    assert not (tmp_path / "isolated-data").exists()


def test_swapped_download_is_rejected_again_at_prepare_boundary(tmp_path):
    artifact, manifest, _ = portable_fixture(tmp_path)
    artifact.write_bytes(b"swapped")
    with pytest.raises(UpdateError) as rejected:
        verify_artifact(artifact, manifest)
    assert rejected.value.code == "artifact_mismatch"
    assert not (tmp_path / "prepared").exists()


def test_previous_prepared_tree_is_not_overwritten(tmp_path):
    artifact, manifest, _ = portable_fixture(tmp_path)
    prepared = tmp_path / "prepared"
    prepared.mkdir()
    (prepared / "keep").write_bytes(b"previous attempt")
    with pytest.raises(UpdateError) as rejected:
        extract_portable(artifact, manifest, prepared)
    assert rejected.value.code == "staging_exists"
    assert (prepared / "keep").read_bytes() == b"previous attempt"
