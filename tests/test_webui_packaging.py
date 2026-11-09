"""Exercise the packaged entry and the frozen host's real launch/asset boundaries."""

import asyncio
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from sylvia_gm.interfaces import webui

ROOT = Path(__file__).resolve().parents[1]


def test_packaged_entry_routes_webui_to_existing_cli():
    result = subprocess.run(
        [sys.executable, str(ROOT / "src-tauri/scripts/sylvia_gm_owner_entry.py"),
         "--webui", "--help"],
        input="", capture_output=True, text=True,
        env={**os.environ, "PYTHONPATH": str(ROOT / "src")},
    )
    assert result.returncode == 0, result.stderr
    assert "--data-root" in result.stdout
    assert "--webui-remote-origin" in result.stdout


@pytest.mark.parametrize("frozen", [False, True])
def test_host_starts_the_supported_owner_entry(tmp_path, monkeypatch, frozen):
    observed = []

    class LaunchObserved(Exception):
        pass

    async def launch(*args, **kwargs):
        observed.extend(args)
        raise LaunchObserved

    monkeypatch.setattr(sys, "frozen", frozen, raising=False)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", launch)
    with pytest.raises(LaunchObserved):
        asyncio.run(webui.OwnerProcess(tmp_path).start())
    expected = [sys.executable] if frozen else [
        sys.executable, "-B", "-m", "sylvia_gm.interfaces.owner_process",
    ]
    assert observed == expected


def test_frozen_host_uses_bundled_frontend(tmp_path, monkeypatch):
    bundle = tmp_path / "bundle"
    (bundle / "webui").mkdir(parents=True)
    (bundle / "webui/app.html").write_text("synthetic selected frontend")
    selected = []

    class StartupObserved(Exception):
        pass

    class Owner:
        def __init__(self, root):
            pass

        async def start(self):
            raise StartupObserved

    original = Path.is_file

    def is_file(path):
        selected.append(path)
        return original(path)

    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    monkeypatch.setattr(webui, "OwnerProcess", Owner)
    monkeypatch.setattr(Path, "is_file", is_file)
    with pytest.raises(StartupObserved):
        asyncio.run(webui.serve(tmp_path / "data"))
    assert bundle / "webui/app.html" in selected


def test_webui_assets_are_explicit_and_exclude_unlisted_siblings(tmp_path):
    spec = importlib.util.spec_from_file_location(
        "webui_sidecar_builder", ROOT / "src-tauri/scripts/build_owner_sidecar.py",
    )
    builder = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(builder)
    inventory = json.loads((ROOT / "src-tauri/webui-data-files.json").read_text())
    assert inventory["format"] == 1
    assert "app.html" in inventory["files"]
    assert "assets/sylvia-logo.png" in inventory["files"]
    frontend = tmp_path / "src-tauri/frontend"
    for name in inventory["files"]:
        target = frontend / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((ROOT / "src-tauri/frontend" / name).read_bytes())
    (tmp_path / "src-tauri/webui-data-files.json").write_text(json.dumps(inventory))
    (frontend / "unlisted-private.txt").write_text("synthetic must never be bundled")
    arguments = builder.webui_data_arguments(tmp_path)
    assert arguments[::2] == ["--add-data"] * len(inventory["files"])
    sources = [value.rsplit(os.pathsep, 1)[0] for value in arguments[1::2]]
    assert len(set(sources)) == len(inventory["files"])
    assert not any("unlisted-private" in value for value in arguments)
    (frontend / "app.html").unlink()
    with pytest.raises(ValueError):
        builder.webui_data_arguments(tmp_path)
