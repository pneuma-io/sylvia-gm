"""The Linux desktop build: what the bundled authority carries and how the bundle is configured.

The Windows build is covered where it always was (the release-exclusion, dictation and
packaging tests). These cases pin what differs on Linux and that Windows inputs are untouched.
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.test_fc01_release_exclusions import catalogue, make_plan, put

ROOT = Path(__file__).resolve().parents[1]
LINUX_TRIPLE = "x86_64-unknown-linux-gnu"
SPEECH_MODULES = {"faster_whisper", "ctranslate2", "sounddevice", "onnxruntime", "av"}


def _builder():
    spec = importlib.util.spec_from_file_location(
        "linux_sidecar_builder", ROOT / "src-tauri/scripts/build_owner_sidecar.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _values(command, flag):
    return [command[i + 1] for i, argument in enumerate(command[:-1]) if argument == flag]


def test_linux_exclusions_name_the_speech_runtime_and_keep_the_command_hook(tmp_path):
    builder = _builder()
    hook = put(
        tmp_path,
        "src-tauri/scripts/hooks/hook-sylvia_gm.interfaces.commands.py",
        "# synthetic hook",
    )
    arguments = builder.dictation_exclusion_arguments(tmp_path)
    pairs = list(zip(arguments[::2], arguments[1::2], strict=True))
    excluded = {value for flag, value in pairs if flag == "--exclude-module"}
    assert excluded >= SPEECH_MODULES
    # The stock codec wheel is the one a Linux bundle must never carry.
    assert "av" in excluded
    assert ("--additional-hooks-dir", str(hook.parent)) in pairs
    assert {flag for flag, _ in pairs} == {"--exclude-module", "--additional-hooks-dir"}
    hook.unlink()
    with pytest.raises(ValueError):
        builder.dictation_exclusion_arguments(tmp_path)


def _sidecar_fixture(root, builder):
    put(root, "src-tauri/scripts/sylvia_gm_owner_entry.py", "# synthetic entry, never executed\n")
    catalogue(root / "campaign-plans")
    make_plan(root / "campaign-plans")
    legitimate = [
        "rulesets/srd-lite/content/fixture.json",
        "rulesets/riichi-mahjong/riichi-mahjong-0.1.0.sylvia-gm-package",
        "campaign-settings/severed-lands/campaigns/story.md",
        "src/sylvia_gm/agent/skills/build_fixture/SKILL.md",
        "src/sylvia_gm/campaign/the-rift-awakening.json",
    ]
    for name in legitimate:
        put(root, name)
    put(root, "src-tauri/sidecar-data-files.json", json.dumps({"format": 1, "files": legitimate}))
    put(root, "rulesets/srd-lite/nested/user-save.sqlite")
    put(root, "config/config.yaml")
    put(root, "src-tauri/scripts/hooks/hook-sylvia_gm.interfaces.commands.py", "# synthetic hook")
    put(root, "src-tauri/scripts/hooks/hook-sounddevice.py", "# synthetic hook")
    for name in builder._DICTATION_NOTICES:
        put(root, "licenses/dictation/" + name, "Synthetic speech notice")
    put(root, "src-tauri/frontend/app.html", "<title>Synthetic Sylvia fixture</title>")
    put(root, "src-tauri/webui-data-files.json", json.dumps({"format": 1, "files": ["app.html"]}))
    return legitimate


def test_linux_sidecar_command_needs_no_codec_inputs_and_leaves_speech_out(tmp_path):
    builder = _builder()
    builder.__file__ = str(tmp_path / "src-tauri/scripts/build_owner_sidecar.py")
    legitimate = _sidecar_fixture(tmp_path, builder)
    calls = []

    def fake_run(command, *, cwd=None, env=None, check):
        # The only process a Linux build starts is PyInstaller: no codec staging.
        assert command[:3] == [sys.executable, "-m", "PyInstaller"]
        assert cwd == tmp_path and check is True
        assert "PYTHONHOME" not in env and "PYTHONPATH" not in env
        calls.append(command)
        put(Path(command[command.index("--distpath") + 1]), "sylvia-gm-owner", b"SYNTHETIC")
        return subprocess.CompletedProcess(command, 0)

    def no_versions(package):
        raise AssertionError(f"a Linux build must not require the speech package {package}")

    environment = {
        "TAURI_ENV_TARGET_TRIPLE": LINUX_TRIPLE,
        "SYLVIA_KEEP_OWNER_BUILD": "1",
        "PYTHONPATH": "must-not-reach-the-build",
    }
    with (
        patch.dict(os.environ, environment),
        patch.object(builder.subprocess, "run", fake_run),
        patch.object(builder, "version", no_versions),
        contextlib.redirect_stdout(io.StringIO()),
    ):
        os.environ.pop("SYLVIA_FFMPEG_LGPL_DIR", None)
        assert builder.main() == 0
    assert len(calls) == 1
    command = calls[0]
    assert _values(command, "--paths") == [str(tmp_path / "src")]
    assert set(_values(command, "--exclude-module")) >= SPEECH_MODULES
    assert "--collect-all" not in command and "--recursive-copy-metadata" not in command
    assert _values(command, "--additional-hooks-dir") == [str(tmp_path / "src-tauri/scripts/hooks")]
    destinations = {entry.split(os.pathsep, 1)[1] for entry in _values(command, "--add-data")}
    # Speech notices describe software this build does not carry.
    assert "licenses/dictation" not in destinations
    assert {"config", "webui", "rulesets/srd-lite", "sylvia_gm/campaign"} <= destinations
    staged = [
        Path(entry.split(os.pathsep, 1)[0])
        for entry in _values(command, "--add-data")
        if Path(entry.split(os.pathsep, 1)[0]).is_dir()
    ]
    shipped = {
        path.relative_to(folder).as_posix()
        for folder in staged
        for path in folder.rglob("*")
        if path.is_file()
    }
    assert "nested/user-save.sqlite" not in shipped
    assert len(staged) == 5 and all(".owner-build" in folder.parts for folder in staged)
    built = tmp_path / f"src-tauri/binaries/sylvia-gm-owner-{LINUX_TRIPLE}"
    assert built.read_bytes() == b"SYNTHETIC"
    assert not built.with_suffix(".exe").exists()
    assert legitimate


def test_windows_sidecar_still_refuses_to_build_without_its_codec_inputs(tmp_path):
    builder = _builder()
    builder.__file__ = str(tmp_path / "src-tauri/scripts/build_owner_sidecar.py")
    _sidecar_fixture(tmp_path, builder)

    def no_process(*_arguments, **_options):
        raise AssertionError("no process may start before the codec inputs are checked")

    with (
        patch.dict(os.environ, {"TAURI_ENV_TARGET_TRIPLE": "x86_64-pc-windows-msvc"}),
        patch.object(builder.subprocess, "run", no_process),
        pytest.raises(RuntimeError, match="SYLVIA_FFMPEG_LGPL_DIR"),
    ):
        os.environ.pop("SYLVIA_FFMPEG_LGPL_DIR", None)
        builder.main()


def test_linux_bundle_settings_only_add_to_the_shared_configuration():
    shared = json.loads((ROOT / "src-tauri/tauri.conf.json").read_text(encoding="utf-8"))
    linux = json.loads((ROOT / "src-tauri/tauri.linux.conf.json").read_text(encoding="utf-8"))
    # Tauri merges the platform file over the shared one on Linux only; Windows never reads it.
    assert shared["bundle"]["targets"] == "nsis"
    assert shared["bundle"]["icon"] == ["icons/icon.ico"]
    assert set(linux) == {"bundle"}
    assert set(linux["bundle"]) == {"targets", "icon", "resources"}
    assert linux["bundle"]["targets"] == ["appimage"]
    # The Linux build carries no speech runtime, so it leaves that runtime's notices out. A null
    # removes the shared entry when Tauri merges the two files; nothing else may be touched.
    removed = linux["bundle"]["resources"]
    assert set(removed.values()) == {None}
    speech = {
        source for source, target in shared["bundle"]["resources"].items()
        if target.startswith("licenses/dictation/")
    }
    assert set(removed) == speech and len(speech) == 11
    assert all(
        target.startswith("licenses/")
        for source, target in shared["bundle"]["resources"].items()
        if source not in removed
    )
    icons = linux["bundle"]["icon"]
    assert icons and all(name.endswith(".png") for name in icons)
    for name in icons:
        payload = (ROOT / "src-tauri" / name).read_bytes()
        assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    # The bundled authority is declared once, in the shared file, for both systems.
    assert shared["bundle"]["externalBin"] == ["binaries/sylvia-gm-owner"]


def test_browser_host_names_the_system_its_owner_runs_on(monkeypatch):
    import asyncio

    from sylvia_gm.interfaces import webui

    ui = webui.LocalWebUI(object(), ROOT / "src-tauri/frontend", "http://127.0.0.1:0")
    info = asyncio.run(ui.invoke("application_info", {}))
    assert set(info) == {
        "product_name", "version", "release_channel", "signing_status", "platform",
    }
    assert info["platform"] == webui._host_platform()
    assert info["platform"] in {"windows", "linux", "macos"}
    # The same names the desktop shell reports, so the page words both hosts alike.
    for value, expected in (
        ("win32", "windows"),
        ("linux", "linux"),
        ("linux2", "linux"),
        ("darwin", "macos"),
        ("freebsd14", "freebsd14"),
    ):
        monkeypatch.setattr(webui.sys, "platform", value)
        assert webui._host_platform() == expected


def test_linux_dialogs_need_no_desktop_helpers_and_never_open_in_the_bundle_folder():
    import tomllib

    cargo = tomllib.loads((ROOT / "src-tauri/Cargo.toml").read_text(encoding="utf-8"))
    # The default backend needs a portal service for file dialogs and the zenity program for
    # message dialogs. GTK is already part of the Linux build, so use it there; elsewhere the
    # dependency is what it always was.
    assert "rfd" not in cargo["dependencies"]
    assert cargo["target"]['cfg(not(target_os = "linux"))']["dependencies"]["rfd"] == "0.15"
    assert cargo["target"]['cfg(target_os = "linux")']["dependencies"]["rfd"] == {
        "version": "0.15",
        "default-features": False,
        "features": ["gtk3"],
    }
    shell = (ROOT / "src-tauri/src/lib.rs").read_text(encoding="utf-8")
    # One helper builds every file dialog and names its starting folder on Linux. The working
    # folder itself must stay: an AppImage's web view finds its helper programs relative to it.
    assert shell.count("rfd::AsyncFileDialog::new()") == 1
    assert shell.count("file_dialog(&window)") == 9
    assert "Some(home) => dialog.set_directory(home)," in shell
    assert "set_current_dir" not in shell


def test_linux_error_boxes_cannot_wait_for_the_loop_that_draws_them():
    import tomllib

    cargo = tomllib.loads((ROOT / "src-tauri/Cargo.toml").read_text(encoding="utf-8"))
    shell = (ROOT / "src-tauri/src/lib.rs").read_text(encoding="utf-8")
    # A blocking rfd box asked for on the thread that runs the GTK loop waits for itself: the
    # start-up "selected data unavailable" box hung this way. One helper shows every blocking
    # box and, on that thread, uses GTK directly. GTK is already part of the Linux build.
    assert cargo["target"]['cfg(target_os = "linux")']["dependencies"]["gtk"] == "0.18"
    assert "gtk" not in cargo["dependencies"]
    assert shell.count("rfd::MessageDialog::new()") == 1
    helper = shell[shell.index("fn error_box(") : shell.index("pub fn run() {")]
    assert "rfd::MessageDialog::new()" in helper
    assert '#[cfg(target_os = "linux")]' in helper
    assert "if gtk::is_initialized_main_thread() {" in helper
    assert "dialog.run();" in helper
    assert shell.count("error_box(") == 8
    for title in (
        "Sylvia GM - storage unavailable",
        "Sylvia GM - update repair required",
        "Sylvia GM - selected data unavailable",
        "Sylvia GM - owner close failed",
        "Sylvia GM - close not completed",
        "Sylvia GM - exit not completed",
    ):
        assert f'"{title}"' in shell


def test_a_missing_codex_runtime_says_what_to_do_where_the_desktop_path_is_short(
    tmp_path, monkeypatch
):
    from sylvia_gm.provider import _codex_rpc

    windows = _codex_rpc.missing_runtime_message(True)
    other = _codex_rpc.missing_runtime_message(False)
    # Windows keeps the sentence it always had.
    assert windows == "Install the Codex runtime before connecting ChatGPT."
    # A Linux desktop starts applications with a shorter PATH than a terminal has.
    assert other.startswith(windows + " ")
    assert "start Sylvia from a terminal in which codex works" in other
    assert "~/.local/bin" in other
    monkeypatch.setattr(_codex_rpc, "find_codex_runtime", lambda: None)
    connection = _codex_rpc.CodexConnection(tmp_path / "codex-home")
    with pytest.raises(_codex_rpc.CodexConnectionError) as refused:
        connection._start_process()
    assert str(refused.value) == _codex_rpc.missing_runtime_message(os.name == "nt")
    assert not (tmp_path / "codex-home" / "workspace").exists()


def test_desktop_shell_names_the_authority_and_private_data_folder_per_platform():
    shell = (ROOT / "src-tauri/src/lib.rs").read_text(encoding="utf-8")
    assert 'if cfg!(windows) { "sylvia-gm-owner.exe" } else { "sylvia-gm-owner" }' in shell
    assert shell.count('"sylvia-gm-owner.exe"') == 1
    assert "desktop_storage::create_data_directory(parent)?" in shell
    assert '"platform": std::env::consts::OS,' in shell
    storage = (ROOT / "src-tauri/src/desktop_storage.rs").read_text(encoding="utf-8")
    assert ".mode(0o700)" in storage
