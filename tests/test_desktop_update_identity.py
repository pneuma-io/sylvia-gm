"""The actual sidecar entry's identity branch opens no DB, provider or key store."""

import json
import os
import subprocess
import sys
from pathlib import Path

from sylvia_gm import __version__


def test_owner_identity_entry_returns_only_public_metadata_without_owner_startup(tmp_path):
    root = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "PYTHONPATH": str(root / "src")}
    result = subprocess.run(
        [
            sys.executable,
            str(root / "src-tauri/scripts/sylvia_gm_owner_entry.py"),
            "--desktop-update-identity",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
        check=True,
    )
    assert result.stderr == ""
    value = json.loads(result.stdout)
    assert set(value) == {
        "format_version",
        "product",
        "component",
        "version",
        "storage_compatibility",
        "target",
    }
    assert value["version"] == __version__
    assert value["storage_compatibility"] == 1
    assert value["component"] == "owner"
    assert value["target"] == "windows-x86_64"
    assert list(tmp_path.iterdir()) == []


def test_owner_identity_entry_rejects_extra_parameters_without_starting_owner(tmp_path):
    root = Path(__file__).resolve().parents[1]
    environment = {**os.environ, "PYTHONPATH": str(root / "src")}
    result = subprocess.run(
        [
            sys.executable,
            str(root / "src-tauri/scripts/sylvia_gm_owner_entry.py"),
            "--desktop-update-identity",
            "unaccepted",
        ],
        cwd=tmp_path,
        env=environment,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 2
    assert result.stdout == result.stderr == ""
    assert list(tmp_path.iterdir()) == []
