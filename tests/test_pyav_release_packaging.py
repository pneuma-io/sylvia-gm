"""Reject accidental vendor codecs and unbound DLL bytes before staging."""

import hashlib
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
