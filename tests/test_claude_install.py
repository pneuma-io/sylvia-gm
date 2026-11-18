"""Sylvia's Claude Code setup against a local stand-in for Anthropic's release channel."""

from __future__ import annotations

import hashlib
import json
import os
import stat
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
SIGNING = Path(__file__).parent / "fixtures" / "claude-release-signing"


class Channel:
    """Serve a stable pointer, one manifest and one binary, as the real channel does."""

    def __init__(
        self,
        monkeypatch,
        *,
        stable: str,
        version: str,
        payload: bytes,
        build: str = "win32-x64",
        binary: str = NAME,
    ) -> None:
        self.payload = payload
        self.signature = b"detached signature"
        self.key = b"release key"
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
                        build: {
                            "binary": binary,
                            "checksum": channel.checksum,
                            "size": channel.size,
                        }
                    },
                }
                channel.manifest = json.dumps(manifest).encode()
                bodies = {
                    "/stable": stable.encode(),
                    f"/{version}/manifest.json": channel.manifest,
                    f"/{version}/manifest.json.sig": channel.signature,
                    "/keys/claude-code.asc": channel.key,
                    f"/{version}/{build}/{binary}": channel.payload,
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
        self.manifest_checks: list[tuple[bytes, bytes, bytes]] = []
        address = f"http://127.0.0.1:{self.server.server_port}"
        monkeypatch.setattr(_claude_install, "RELEASES", address)
        monkeypatch.setattr(_claude_install, "SIGNING_KEY", f"{address}/keys/claude-code.asc")
        monkeypatch.setattr(_claude_install, "release_platform", lambda: build)
        monkeypatch.setattr(_claude_install, "executable_name", lambda: binary)
        monkeypatch.setattr(_claude_install, "verify_publisher", self.signature_checks.append)
        monkeypatch.setattr(
            _claude_install,
            "verify_manifest",
            lambda *checked: self.manifest_checks.append(checked),
        )
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
    monkeypatch.setattr(_claude_install.sys, "platform", "darwin")
    with pytest.raises(ProviderConnectionError, match="Windows and Linux only"):
        _claude_install.release_platform()


@pytest.mark.parametrize(
    ("system", "machine", "libc", "build"),
    [
        ("nt", "AMD64", None, "win32-x64"),
        ("nt", "ARM64", None, "win32-arm64"),
        ("posix", "x86_64", "glibc 2.35", "linux-x64"),
        ("posix", "aarch64", "glibc 2.39", "linux-arm64"),
        ("posix", "x86_64", None, "linux-x64-musl"),
    ],
)
def test_each_computer_gets_its_own_published_build(monkeypatch, system, machine, libc, build):
    monkeypatch.setattr(_claude_install.os, "name", system)
    monkeypatch.setattr(_claude_install.sys, "platform", "win32" if system == "nt" else "linux")
    monkeypatch.setattr(_claude_install.platform, "machine", lambda: machine)

    def confstr(name):
        assert name == "CS_GNU_LIBC_VERSION"
        if libc is None:
            raise ValueError("unrecognized configuration name")
        return libc

    monkeypatch.setattr(_claude_install.os, "confstr", confstr, raising=False)
    assert _claude_install.release_platform() == build


# Linux binaries are not code-signed; the release list carries Anthropic's signature instead,
# and each download must match the checksum that signed list gives (issue 170).
@pytest.mark.parametrize("build", ["linux-x64", "linux-arm64"])
def test_linux_installs_the_build_listed_in_the_signed_release_list(tmp_path, monkeypatch, build):
    served = Channel(
        monkeypatch,
        stable=MINIMUM,
        version=MINIMUM,
        payload=b"linux build",
        build=build,
        binary="claude",
    )
    try:
        installer = ClaudeInstaller(tmp_path)
        installer.run()
        assert installer.progress()["state"] == "ready"
        installed = tmp_path / "install" / MINIMUM / "claude"
        assert installed.read_bytes() == b"linux build"
        # The exact bytes served were checked, before the binary was asked for.
        assert served.manifest_checks == [(served.manifest, served.signature, served.key)]
        binary = f"/{MINIMUM}/{build}/claude"
        assert served.requests.index(f"/{MINIMUM}/manifest.json.sig") < served.requests.index(
            binary
        )
        assert served.signature_checks == [], "no Windows code-signature check off Windows"
        if os.name == "posix":
            assert installed.stat().st_mode & stat.S_IXUSR
    finally:
        served.close()


@pytest.mark.parametrize("fault", ["unsigned list", "no signature published"])
def test_linux_refuses_before_downloading_when_the_list_is_not_signed(tmp_path, monkeypatch, fault):
    served = Channel(
        monkeypatch,
        stable=MINIMUM,
        version=MINIMUM,
        payload=b"linux build",
        build="linux-x64",
        binary="claude",
    )
    try:
        if fault == "unsigned list":

            def unsigned(*_):
                raise ProviderConnectionError(_claude_install._UNSIGNED_MANIFEST)

            monkeypatch.setattr(_claude_install, "verify_manifest", unsigned)
        else:
            served.signature = None
        installer = ClaudeInstaller(tmp_path)
        installer.run()
        progress = installer.progress()
        assert progress["state"] == "failed"
        refusals = {_claude_install._UNSIGNED_MANIFEST, _claude_install._UNREACHABLE}
        assert progress["message"] in refusals
        assert f"/{MINIMUM}/linux-x64/claude" not in served.requests
        assert list((tmp_path / "install").iterdir()) == []
    finally:
        served.close()


def signing(name: str) -> bytes:
    return (SIGNING / name).read_bytes()


@pytest.mark.runs_on("linux", reason="gpgv checks the release list's signature on Linux")
def test_the_release_list_signature_is_checked_with_gpgv(monkeypatch):
    # The fixtures hold a throwaway test key, a list it signed, and the same list signed by
    # another key. Only a good signature by the pinned key's fingerprint is accepted.
    manifest, key = signing("manifest.json"), signing("release-key.asc")
    good, other = signing("manifest.json.sig"), signing("manifest.json.other.sig")
    with pytest.raises(ProviderConnectionError, match="not signed by Anthropic"):
        _claude_install.verify_manifest(manifest, good, key)  # Anthropic's own key is pinned
    pinned = signing("release-key.fingerprint").decode().strip()
    monkeypatch.setattr(_claude_install, "SIGNING_FINGERPRINT", pinned)
    _claude_install.verify_manifest(manifest, good, key)
    for changed in [
        (manifest.replace(b"9.9.9", b"9.9.8"), good, key),
        (manifest, other, key),
        (manifest, b"-----BEGIN PGP SIGNATURE-----\n\nAAAA\n-----END PGP SIGNATURE-----\n", key),
        (manifest, good, b"not a key"),
        (manifest, good, signing("manifest.json.sig")),
    ]:
        with pytest.raises(ProviderConnectionError, match="not signed by Anthropic"):
            _claude_install.verify_manifest(*changed)


def test_without_gpgv_the_release_list_cannot_be_checked_and_nothing_is_installed(monkeypatch):
    monkeypatch.setattr(_claude_install.shutil, "which", lambda name: None)
    with pytest.raises(ProviderConnectionError, match="needs gpgv"):
        _claude_install.verify_manifest(b"{}", b"signature", b"key")
