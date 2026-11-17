"""Sylvia's own copy of Claude Code: Anthropic's published binary, downloaded and checked.

Sylvia never bundles, modifies or redistributes Claude Code. When asked, it downloads the
binary Anthropic publishes, checks it against Anthropic's release manifest and Windows code
signature, and keeps it under Sylvia's runtime home. Claude Code's own installer and updater
are never run, so any Claude Code a person already uses is left exactly as it was.
"""

from __future__ import annotations

import hashlib
import os
import platform
import re
import shutil
import subprocess
import threading
from collections.abc import Mapping
from enum import StrEnum
from pathlib import Path
from typing import Final

import httpx

from sylvia_gm.provider._claude_runtime import (
    MINIMUM_VERSION,
    executable_name,
    process_options,
    runtime_version,
)
from sylvia_gm.provider._exceptions import ProviderConnectionError

RELEASES = "https://downloads.claude.ai/claude-code-releases"
PUBLISHER: Final = "Anthropic, PBC"
MAX_BINARY_BYTES: Final = 2 * 1024**3
_UNREACHABLE: Final = (
    "Claude Code could not be downloaded from Anthropic. Check your connection and try again."
)


class InstallState(StrEnum):
    IDLE = "idle"
    CHECKING = "checking"
    DOWNLOADING = "downloading"
    VERIFYING = "verifying"
    READY = "ready"
    FAILED = "failed"


INSTALL_MOVES: Final[Mapping[InstallState, frozenset[InstallState]]] = {
    InstallState.IDLE: frozenset({InstallState.CHECKING}),
    InstallState.CHECKING: frozenset(
        {InstallState.DOWNLOADING, InstallState.READY, InstallState.FAILED, InstallState.IDLE}
    ),
    InstallState.DOWNLOADING: frozenset(
        {InstallState.VERIFYING, InstallState.FAILED, InstallState.IDLE}
    ),
    InstallState.VERIFYING: frozenset({InstallState.READY, InstallState.FAILED}),
    InstallState.READY: frozenset({InstallState.CHECKING}),
    InstallState.FAILED: frozenset({InstallState.CHECKING}),
}
_BUSY: Final = frozenset(
    {InstallState.CHECKING, InstallState.DOWNLOADING, InstallState.VERIFYING}
)


class _CancelledError(Exception):
    """The person stopped this download; nothing was installed."""


def release_platform() -> str:
    """Name this computer's published build, or say that automatic setup is unavailable."""
    if os.name != "nt":
        raise ProviderConnectionError(
            "Sylvia can set up Claude Code on Windows only in this version."
        )
    return "win32-arm64" if platform.machine().lower() in {"arm64", "aarch64"} else "win32-x64"


def _version(text: str) -> tuple[int, int, int]:
    """Accept only a plain release number; an error page must never reach a download URL."""
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+)", text.strip())
    if match is None:
        raise ProviderConnectionError(_UNREACHABLE)
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def verify_publisher(path: Path) -> None:
    """Require a valid Windows code signature from Anthropic on the downloaded file."""
    script = (
        "$s = Get-AuthenticodeSignature -LiteralPath $env:SYLVIA_SIGNED_FILE; "
        "$n = if ($s.SignerCertificate) "
        "{ $s.SignerCertificate.GetNameInfo('SimpleName', $false) } else { '' }; "
        '[Console]::Out.Write("$($s.Status)`n$n")'
    )
    powershell = (
        Path(os.environ.get("SYSTEMROOT", r"C:\Windows"))
        / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    )
    try:
        result = subprocess.run(
            [str(powershell), "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=180,
            env={**os.environ, "SYLVIA_SIGNED_FILE": str(path)},
            **process_options(),
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ProviderConnectionError(
            "Windows could not check the Claude Code download's signature. Try again."
        ) from None
    if result.returncode or result.stdout.split("\n") != ["Valid", PUBLISHER]:
        raise ProviderConnectionError(
            "The Claude Code download is not signed by Anthropic, so Sylvia discarded it."
        )


class ClaudeInstaller:
    """Run one download at a time for a runtime home and report plain progress."""

    def __init__(self, home: Path) -> None:
        self.home = home
        self._lock = threading.Lock()
        self._state = InstallState.IDLE
        self._version: str | None = None
        self._received = 0
        self._total: int | None = None
        self._message: str | None = None
        self._cancel = threading.Event()
        self._worker: threading.Thread | None = None

    def _move(self, state: InstallState, message: str | None = None) -> None:
        with self._lock:
            if state not in INSTALL_MOVES[self._state]:
                raise RuntimeError(f"Claude Code setup cannot move from {self._state} to {state}")
            self._state, self._message = state, message

    def progress(self) -> dict[str, object]:
        """Report the stored state value and byte counts; never a path, URL or raw error."""
        with self._lock:
            return {
                "state": self._state.value,
                "version": self._version,
                "received": self._received,
                "total": self._total,
                "message": self._message,
            }

    def start(self) -> None:
        """Begin setup in the background unless one is already running."""
        with self._lock:
            if self._state in _BUSY:
                return
        self._begin()
        self._worker = threading.Thread(target=self._finish, daemon=True)
        self._worker.start()

    def run(self) -> None:
        """Set up in the calling thread, for headless use."""
        with self._lock:
            if self._state in _BUSY:
                return
        self._begin()
        self._finish()

    def cancel(self) -> None:
        """Stop a download in progress; a copy that is already installed stays in place."""
        self._cancel.set()

    def _begin(self) -> None:
        self._cancel.clear()
        with self._lock:
            self._version, self._received, self._total = None, 0, None
        self._move(InstallState.CHECKING)

    def _finish(self) -> None:
        try:
            self._install()
        except _CancelledError:
            self._move(InstallState.IDLE, "Setup was cancelled. Nothing was changed.")
        except ProviderConnectionError as error:
            self._move(InstallState.FAILED, str(error))
        except (httpx.HTTPError, OSError, ValueError, KeyError, TypeError):
            self._move(InstallState.FAILED, _UNREACHABLE)
        finally:
            for leftover in (self.home / "install").glob("*.partial"):
                shutil.rmtree(leftover, ignore_errors=True)

    def _install(self) -> None:
        """Fetch Anthropic's stable release, never older than the version Sylvia requires."""
        build = release_platform()
        directory = self.home / "install"
        directory.mkdir(parents=True, exist_ok=True)
        timeout = httpx.Timeout(30.0, read=60.0)
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            stable = client.get(f"{RELEASES}/stable")
            stable.raise_for_status()
            version = ".".join(map(str, max(_version(stable.text), MINIMUM_VERSION)))
            with self._lock:
                self._version = version
            final = directory / version
            if (final / executable_name()).is_file():
                try:
                    if runtime_version(final / executable_name(), self.home) == version:
                        self._move(InstallState.READY, "Sylvia's copy is already up to date.")
                        return
                except ProviderConnectionError:
                    pass
                shutil.rmtree(final)
            listing = client.get(f"{RELEASES}/{version}/manifest.json")
            listing.raise_for_status()
            entry = listing.json()["platforms"][build]
            checksum, size = entry["checksum"], entry["size"]
            if (
                entry.get("binary") != executable_name()
                or not isinstance(checksum, str)
                or re.fullmatch(r"[0-9a-f]{64}", checksum) is None
                or type(size) is not int
                or not 0 < size <= MAX_BINARY_BYTES
            ):
                raise ProviderConnectionError(_UNREACHABLE)
            if shutil.disk_usage(directory).free < size + 64 * 1024**2:
                raise ProviderConnectionError(
                    "There is not enough free disk space to install Claude Code."
                )
            staging = directory / f"{version}.partial"
            shutil.rmtree(staging, ignore_errors=True)
            staging.mkdir()
            download = staging / executable_name()
            with self._lock:
                self._total = size
            self._move(InstallState.DOWNLOADING)
            digest = hashlib.sha256()
            address = f"{RELEASES}/{version}/{build}/{executable_name()}"
            with client.stream("GET", address) as response, download.open("wb") as file:
                response.raise_for_status()
                received = 0
                for chunk in response.iter_bytes(1024 * 1024):
                    if self._cancel.is_set():
                        raise _CancelledError
                    received += len(chunk)
                    if received > size:
                        raise ProviderConnectionError(_UNREACHABLE)
                    digest.update(chunk)
                    file.write(chunk)
                    with self._lock:
                        self._received = received
        self._move(InstallState.VERIFYING)
        if received != size or digest.hexdigest() != checksum:
            raise ProviderConnectionError(
                "The Claude Code download did not match Anthropic's published checksum, "
                "so Sylvia discarded it. Try again."
            )
        verify_publisher(download)
        staging.rename(final)
        if runtime_version(final / executable_name(), self.home) != version:
            shutil.rmtree(final, ignore_errors=True)
            raise ProviderConnectionError(
                "The Claude Code download did not report the expected version, "
                "so Sylvia discarded it."
            )
        # A copy still running an unfinished turn stays until a later setup can remove it.
        for other in directory.iterdir():
            if other != final:
                shutil.rmtree(other, ignore_errors=True)
        self._move(InstallState.READY, f"Claude Code {version} is installed for Sylvia.")
