"""Sylvia's Claude Code setup against a local stand-in for Anthropic's release channel."""

from __future__ import annotations

import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from sylvia_gm.config import claude_settings
from sylvia_gm.provider import _claude_install, _claude_runtime
from sylvia_gm.provider._claude_install import INSTALL_MOVES, ClaudeInstaller, InstallState
from sylvia_gm.provider._exceptions import ProviderConnectionError

MINIMUM = _claude_runtime.MINIMUM_VERSION_TEXT
NAME = _claude_runtime.executable_name()


class Channel:
    """Serve a stable pointer, one manifest and one binary, as the real channel does."""

    def __init__(self, monkeypatch, *, stable: str, version: str, payload: bytes) -> None:
        self.payload = payload
        self.checksum = hashlib.sha256(payload).hexdigest()
        self.size = len(payload)
        self.requests: list[str] = []
        self.during_download = lambda: None
        channel = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                channel.requests.append(self.path)
                manifest = {
                    "version": version,
                    "platforms": {
                        "win32-x64": {
                            "binary": NAME,
                            "checksum": channel.checksum,
                            "size": channel.size,
                        }
                    },
                }
                bodies = {
                    "/stable": stable.encode(),
                    f"/{version}/manifest.json": json.dumps(manifest).encode(),
                    f"/{version}/win32-x64/{NAME}": channel.payload,
                }
                body = bodies.get(self.path)
                self.send_response(200 if body is not None else 404)
                self.send_header("Content-Length", str(len(body or b"")))
                self.end_headers()
                if body is channel.payload:
                    channel.during_download()
                self.wfile.write(body or b"")

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        self.signature_checks: list[Path] = []
        monkeypatch.setattr(
            _claude_install, "RELEASES", f"http://127.0.0.1:{self.server.server_port}"
        )
        monkeypatch.setattr(_claude_install, "release_platform", lambda: "win32-x64")
        monkeypatch.setattr(_claude_install, "verify_publisher", self.signature_checks.append)
        # The stand-in binary cannot run; the copy "reports" the release folder it sits in.
        monkeypatch.setattr(
            _claude_install, "runtime_version", lambda executable, home: executable.parent.name
        )

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def channel(monkeypatch):
    served = Channel(monkeypatch, stable="2.1.100", version=MINIMUM, payload=b"published" * 4096)
    yield served
    served.close()


def test_setup_installs_the_checked_release_into_sylvias_folder_only(tmp_path, channel):
    home = tmp_path / "claude-runtime"
    older = home / "install" / "2.1.200"
    older.mkdir(parents=True)
    (older / NAME).write_bytes(b"older copy")
    installer = ClaudeInstaller(home)
    installer.run()
    # A stable release older than Sylvia supports is raised to the supported minimum.
    assert installer.progress() == {
        "state": "ready",
        "version": MINIMUM,
        "received": channel.size,
        "total": channel.size,
        "message": f"Claude Code {MINIMUM} is installed for Sylvia.",
    }
    installed = home / "install" / MINIMUM / NAME
    assert installed.read_bytes() == channel.payload
    assert _claude_runtime.find_runtime(home) == installed
    assert [entry.name for entry in (home / "install").iterdir()] == [MINIMUM]
    assert [check.parent.name for check in channel.signature_checks] == [f"{MINIMUM}.partial"]
    assert sorted(path.name for path in tmp_path.iterdir()) == ["claude-runtime"]
    # Asking again finds the same release in place and downloads nothing.
    before = len(channel.requests)
    installer.run()
    assert installer.progress()["state"] == "ready"
    assert channel.requests[before:] == ["/stable"]


def test_a_newer_stable_release_is_selected(tmp_path, monkeypatch):
    served = Channel(monkeypatch, stable="2.9.1", version="2.9.1", payload=b"newer")
    try:
        installer = ClaudeInstaller(tmp_path)
        installer.run()
        assert installer.progress()["version"] == "2.9.1"
        assert _claude_runtime.find_runtime(tmp_path) == tmp_path / "install" / "2.9.1" / NAME
    finally:
        served.close()


@pytest.mark.parametrize("fault", ["checksum", "signature", "version", "pointer"])
def test_an_unverified_download_is_discarded(tmp_path, monkeypatch, channel, fault):
    if fault == "checksum":
        channel.checksum = "0" * 64
    elif fault == "signature":

        def unsigned(path):
            raise ProviderConnectionError("not signed by Anthropic")

        monkeypatch.setattr(_claude_install, "verify_publisher", unsigned)
    elif fault == "version":
        monkeypatch.setattr(_claude_install, "runtime_version", lambda *_: "9.9.9")
    else:
        monkeypatch.setattr(_claude_install, "RELEASES", _claude_install.RELEASES + "/missing")
    installer = ClaudeInstaller(tmp_path)
    installer.run()
    progress = installer.progress()
    assert progress["state"] == "failed" and progress["message"]
    assert "127.0.0.1" not in str(progress["message"])
    assert _claude_runtime.find_runtime(tmp_path) is None
    assert list((tmp_path / "install").iterdir()) == []


def test_status_reports_a_download_and_cancel_stops_it(tmp_path, channel):
    seen: list[dict[str, object]] = []

    def cancel_midway():
        seen.append(claude_settings.claude_settings_action(tmp_path, "status"))
        claude_settings.claude_settings_action(tmp_path, "cancel")

    channel.during_download = cancel_midway
    started = claude_settings.claude_settings_action(tmp_path, "install")
    assert started["installed"] is False
    worker = claude_settings._accounts[tmp_path.resolve() / "claude-runtime"].installer._worker
    worker.join(20)
    assert seen[0]["install"] == {
        "state": "downloading",
        "version": MINIMUM,
        "received": 0,
        "total": channel.size,
        "message": None,
    }
    status = claude_settings.claude_settings_action(tmp_path, "status")
    assert status["install"]["state"] == "idle" and status["installed"] is False
    assert list((tmp_path / "claude-runtime" / "install").iterdir()) == []


def test_setup_states_only_move_along_the_table(tmp_path):
    assert set(INSTALL_MOVES) == set(InstallState)
    installer = ClaudeInstaller(tmp_path)
    with pytest.raises(RuntimeError):
        installer._move(InstallState.READY)
    assert installer.progress()["state"] == "idle"


def test_setup_is_declined_clearly_where_no_checked_build_exists(monkeypatch):
    monkeypatch.setattr(_claude_install.os, "name", "posix")
    with pytest.raises(ProviderConnectionError, match="Windows only"):
        _claude_install.release_platform()
