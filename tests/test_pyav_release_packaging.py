"""Reject accidental vendor codecs and unbound DLL bytes before staging."""

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "src-tauri/scripts/stage_pyav_lgpl.py"
MANIFEST = ROOT / "licenses/dictation/PYAV-LGPL-BUILD.json"


@pytest.mark.parametrize("mutation", ["vendor-codec", "altered-dll", "missing-binding"])
def test_codec_stage_rejects_unbound_inputs_before_creating_output(tmp_path, mutation):
    manifest = json.loads(MANIFEST.read_text())
    libraries = tmp_path / "libraries"
    libraries.mkdir()
    for library in manifest["libraries"]:
        content = b"Synthetic non-executable DLL input"
        (libraries / library["path"]).write_bytes(content)
        library["bytes"] = len(content)
        library["sha256"] = hashlib.sha256(content).hexdigest()
    if mutation == "vendor-codec":
        (libraries / "libx264-unexpected.dll").write_bytes(b"Synthetic extra")
    elif mutation == "altered-dll":
        (libraries / manifest["libraries"][0]["path"]).write_bytes(b"Changed")
    else:
        manifest["libraries"].pop()
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(manifest))
    output = tmp_path / "stage"
    result = subprocess.run(
        [
            sys.executable,
            str(HELPER),
            "--ffmpeg",
            str(libraries),
            "--output",
            str(output),
            "--manifest",
            str(path),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode != 0
    assert not output.exists()


@pytest.fixture(scope="module")
def provisioner():
    scripts = ROOT / "src-tauri/scripts"
    spec = importlib.util.spec_from_file_location(
        "provision_ffmpeg_lgpl", scripts / "provision_ffmpeg_lgpl.py",
    )
    module = importlib.util.module_from_spec(spec)
    sys.path.insert(0, str(scripts))
    try:
        spec.loader.exec_module(module)
    finally:
        sys.path.pop(0)
    return module


def test_public_codec_provisioning_rejects_unpinned_archives_without_dll_output(
    tmp_path, provisioner,
):
    archive = tmp_path / "unapproved.zip"
    archive.write_bytes(b"Unapproved input, not a release archive")
    work = tmp_path / "attempt"
    with pytest.raises(ValueError, match="pinned SHA256"):
        provisioner.provision(work, archive, archive)
    assert not (work / "ffmpeg-lgpl").exists()
    assert not (work / "static-owner-input.exe").exists()


@pytest.mark.parametrize(
    "mutation", ["extra", "missing", "type", "size", "hash", "licence", "notice"],
)
def test_static_codec_reader_rejects_unverified_entries(provisioner, mutation):
    manifest = json.loads(MANIFEST.read_bytes())
    first = manifest["libraries"][0]
    entry = "av.libs\\" + first["path"]
    notices = {"FFMPEG-LGPLv2.1.txt": b"Expected original notice"}

    class Archive:
        toc = {
            "av.libs\\" + item["path"]: (0, 0, item["bytes"], 1, "b")
            for item in manifest["libraries"]
        }

        def extract(self, name):
            if name.startswith("licenses\\dictation\\"):
                return b"Changed notice" if mutation == "notice" else next(iter(notices.values()))
            if mutation == "hash":
                return b"!" * first["bytes"]
            return b"Unapproved non-executable bytes"

    archive = Archive()
    if mutation == "extra":
        archive.toc["av.libs\\libx264.dll"] = (0, 0, 1, 1, "b")
    elif mutation == "missing":
        del archive.toc[entry]
    elif mutation == "type":
        archive.toc[entry] = (0, 0, first["bytes"], 1, "s")
    elif mutation == "size":
        archive.toc[entry] = (0, 0, 1, 1, "b")
    elif mutation == "licence":
        manifest["ffmpeg_licence"] = "GPL-3.0-or-later"
    with pytest.raises(ValueError):
        provisioner.read_codecs(archive, manifest, notices)


def test_windows_ci_provisions_codecs_before_building_the_owner():
    import yaml

    workflow = yaml.safe_load((ROOT / ".github/workflows/supported-journeys.yml").read_text())
    steps = workflow["jobs"]["rust-tests"]["steps"]
    provision = next(
        i for i, step in enumerate(steps) if "provision_ffmpeg_lgpl.py" in step.get("run", "")
    )
    build = next(
        i for i, step in enumerate(steps) if "build_owner_sidecar.py" in step.get("run", "")
    )
    assert provision < build
    script = steps[provision]["run"]
    assert "if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }" in script
    assert '"SYLVIA_FFMPEG_LGPL_DIR=$codecWork/ffmpeg-lgpl" >> $env:GITHUB_ENV' in script
