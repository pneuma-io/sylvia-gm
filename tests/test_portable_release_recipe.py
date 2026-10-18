"""The tracked EXE ZIP recipe retains exact inputs and never overlays user data."""

import hashlib
import json
import zipfile
from pathlib import Path

import pytest

from sylvia_gm.portable_artifact import (
    DESKTOP,
    MARKER,
    OWNER,
    PORTABLE_README,
    assemble_portable_zip,
    portable_payloads,
    verify_portable_zip,
)
from sylvia_gm.release_artifact import ReleaseArtifactError


def payloads():
    result = {
        DESKTOP: b"MZsynthetic desktop",
        OWNER: b"MZsynthetic owner",
        MARKER: b'{"version":1}\n',
        "README-PORTABLE.txt": PORTABLE_README,
        "SRD-Lite.sylvia-gm-package": b"synthetic rules",
        "Severed-Lands.sylvia-gm-package": b"synthetic setting",
    }
    for name in ("LICENSE", "COPYRIGHT", "LICENSING-STATUS.md", "SYLVIA-ADDITIONAL-PERMISSIONS.md"):
        result[name] = result["licenses/" + name] = b"synthetic required notice"
    for name in (
        "licenses/SRD-5.2.1.md",
        "licenses/Severed-Lands.md",
        "licenses/dependency-notices.txt",
    ):
        result[name] = b"synthetic required attribution"
    return result


def assemble(path, files):
    return assemble_portable_zip(
        path,
        payloads=files,
        source_files={"src/example.py": "a" * 64},
        provenance={"source_commit": "b" * 40, "source_tree": "c" * 40},
        toolchain={"python": "synthetic test fixture"},
    )


def test_portable_is_deterministic_and_overlay_preserves_local_data(tmp_path):
    first, second = tmp_path / "first.zip", tmp_path / "second.zip"
    identity = assemble(first, payloads())
    assemble(second, payloads())
    assert first.read_bytes() == second.read_bytes()
    assert verify_portable_zip(first) == identity
    assert identity["desktop_sha256"] == hashlib.sha256(payloads()[DESKTOP]).hexdigest()
    destination = tmp_path / "selected"
    data = destination / "isolated-data"
    data.mkdir(parents=True)
    (data / "authority.sqlite3").write_bytes(b"selected synthetic campaign")
    (data / "config.yaml").write_bytes(b"selected non-secret settings")
    with zipfile.ZipFile(first) as archive:
        assert not any(name.startswith("isolated-data/") for name in archive.namelist())
        archive.extractall(destination)
    assert (data / "authority.sqlite3").read_bytes() == b"selected synthetic campaign"
    assert (data / "config.yaml").read_bytes() == b"selected non-secret settings"
    assert json.loads((destination / MARKER).read_bytes()) == {"version": 1}
    assert (destination / DESKTOP).read_bytes() == payloads()[DESKTOP]


@pytest.mark.parametrize(
    "bad",
    [
        "isolated-data/config.yaml",
        "config.yaml",
        "credentials.json",
        "../escape",
        "Launch Sylvia GM.cmd",
    ],
)
def test_recipe_refuses_mutable_or_undeclared_payloads(tmp_path, bad):
    files = payloads()
    files[bad] = b"must not be distributed"
    with pytest.raises(ReleaseArtifactError):
        assemble(tmp_path / "invalid.zip", files)


def test_missing_marker_or_stale_payload_manifest_cannot_pass(tmp_path):
    files = payloads()
    del files[MARKER]
    with pytest.raises(ReleaseArtifactError):
        assemble(tmp_path / "missing.zip", files)
    source = tmp_path / "valid.zip"
    assemble(source, payloads())
    target = tmp_path / "tampered.zip"
    with zipfile.ZipFile(source) as before, zipfile.ZipFile(target, "w") as after:
        for name in before.namelist():
            after.writestr(name, b"changed" if name == DESKTOP else before.read(name))
    with pytest.raises(ReleaseArtifactError, match="hash manifest"):
        verify_portable_zip(target)


def test_actual_source_notice_map_and_current_packages_are_collected(tmp_path):
    root = Path(__file__).resolve().parents[1]
    desktop, owner = tmp_path / "desktop.exe", tmp_path / "owner.exe"
    desktop.write_bytes(b"MZ synthetic compiler output")
    owner.write_bytes(b"MZ synthetic compiler output")
    files = portable_payloads(root, desktop=desktop, owner=owner)
    resources = json.loads((root / "src-tauri/tauri.conf.json").read_text())["bundle"]["resources"]
    for source, destination in resources.items():
        assert files[destination] == (root / "src-tauri" / source).read_bytes()
    assert files["SRD-Lite.sylvia-gm-package"].startswith(b"PK")
    assert files["Severed-Lands.sylvia-gm-package"].startswith(b"PK")
    assert not any(name.startswith("isolated-data/") for name in files)


def test_verifier_refuses_mutable_payload_even_with_consistent_internal_hashes(tmp_path):
    source = tmp_path / "source.zip"
    assemble(source, payloads())
    with zipfile.ZipFile(source) as archive:
        files = {name: archive.read(name) for name in archive.namelist()}
    files["isolated-data/config.yaml"] = b"must never be distributed"
    entries = json.loads(files["ENTRY-HASHES.json"])
    entries["isolated-data/config.yaml"] = {
        "bytes": len(files["isolated-data/config.yaml"]),
        "sha256": hashlib.sha256(files["isolated-data/config.yaml"]).hexdigest(),
    }
    files["ENTRY-HASHES.json"] = json.dumps(entries).encode()
    target = tmp_path / "invalid.zip"
    with zipfile.ZipFile(target, "w") as archive:
        for name, raw in files.items():
            archive.writestr(name, raw)
    with pytest.raises(ReleaseArtifactError, match="Unexpected portable payload"):
        verify_portable_zip(target)
