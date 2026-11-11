"""Owned stdio access to Sylvia's own unmodified copy of Claude Code.

Sylvia keeps a separate installation, profile and sign-in under its runtime home and never
uses the Claude Code a person may already have. Only the binary accesses native
authentication. The SDK MCP control channel advertises Sylvia tools but never executes an
operation in the runtime process.
"""

from __future__ import annotations

import json
import os
import queue
import re
import subprocess
import threading
from contextlib import suppress
from pathlib import Path
from typing import Any

from sylvia_gm.provider._exceptions import (
    InvalidResponseError,
    ProviderConnectionError,
    ProviderTimeoutError,
)

MINIMUM_VERSION = (2, 1, 322)
MINIMUM_VERSION_TEXT = ".".join(map(str, MINIMUM_VERSION))
MAX_FRAME_BYTES = 8 * 1024 * 1024


def native_environment(home: Path) -> dict[str, str]:
    """Run Sylvia's copy against Sylvia's profile, without inherited inference overrides.

    Claude Code's documented directory variables keep its settings, sessions, sign-in and
    temporary files inside ``home``. Its own updater is blocked because it would write to the
    shared per-user installation; Sylvia replaces its copy itself.
    """
    allowed = {
        "systemroot",
        "windir",
        "comspec",
        "path",
        "pathext",
        "temp",
        "tmp",
        "userprofile",
        "home",
        "homedrive",
        "homepath",
        "localappdata",
        "appdata",
        "programfiles",
        "programfiles(x86)",
        "programdata",
        "systemdrive",
        "lang",
        "lc_all",
        "lc_ctype",
        "xdg_config_home",
        "xdg_data_home",
    }
    environment = {k: v for k, v in os.environ.items() if k.lower() in allowed}
    environment.update(
        {
            "CLAUDE_CONFIG_DIR": str(home / "profile"),
            "ANTHROPIC_CONFIG_DIR": str(home / "anthropic"),
            "CLAUDE_CODE_TMPDIR": str(home / "temp"),
            "DISABLE_AUTOUPDATER": "1",
            "DISABLE_UPDATES": "1",
            "DISABLE_INSTALLATION_CHECKS": "1",
            "DISABLE_TELEMETRY": "1",
            "DISABLE_ERROR_REPORTING": "1",
            "ENABLE_CLAUDEAI_MCP_SERVERS": "false",
            "ENABLE_TOOL_SEARCH": "false",
            "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
        }
    )
    return environment


def process_options() -> dict[str, Any]:
    """Hide owned console children on Windows without selecting an interactive shell."""
    return {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def native_auth_command(executable: Path, action: str) -> list[str]:
    """Use native authentication while excluding unrelated settings and customizations."""
    return [
        str(executable),
        "--safe-mode",
        "--restricted",
        "--setting-sources",
        "",
        "--settings",
        '{"disableAllHooks":true}',
        "auth",
        action,
    ]


def executable_name() -> str:
    """Name Anthropic publishes for this platform's binary."""
    return "claude.exe" if os.name == "nt" else "claude"


def find_runtime(home: Path) -> Path | None:
    """Return the newest copy Sylvia installed under ``home``; never another Claude Code."""
    installed: list[tuple[tuple[int, ...], Path]] = []
    directory = home / "install"
    for entry in directory.iterdir() if directory.is_dir() else ():
        candidate = entry / executable_name()
        if re.fullmatch(r"\d+\.\d+\.\d+", entry.name) and candidate.is_file():
            installed.append((tuple(int(part) for part in entry.name.split(".")), candidate))
    return max(installed)[1].absolute() if installed else None


def runtime_version(executable: Path, home: Path) -> str:
    """Check the native protocol prerequisite without sign-in or a model request."""
    try:
        result = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            env=native_environment(home),
            **process_options(),
        )
    except (OSError, subprocess.TimeoutExpired):
        raise ProviderConnectionError(
            "The Claude executable could not report its version."
        ) from None
    match = re.fullmatch(r"(\d+)\.(\d+)\.(\d+) \(Claude Code\)\s*", result.stdout)
    if result.returncode or match is None:
        raise ProviderConnectionError(
            "Sylvia's copy of Claude Code is damaged. Install it again in Settings > Accounts."
        )
    version = tuple(int(v) for v in match.groups())
    if version < MINIMUM_VERSION:
        raise ProviderConnectionError(
            f"Claude Code {MINIMUM_VERSION_TEXT} or later is required. "
            "Update Sylvia's copy in Settings > Accounts."
        )
    return ".".join(map(str, version))


def authentication_status(executable: Path, home: Path) -> dict[str, object]:
    """Project allowlisted native status fields; discard emails, tokens and raw diagnostics."""
    try:
        result = subprocess.run(
            native_auth_command(executable, "status"),
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=10,
            env=native_environment(home),
            **process_options(),
        )
        raw = json.loads(result.stdout)
    except (OSError, subprocess.TimeoutExpired, ValueError):
        raise ProviderConnectionError("Claude could not report its sign-in status.") from None
    if not isinstance(raw, dict) or result.returncode not in (0, 1):
        raise ProviderConnectionError("Claude could not report its sign-in status.")
    method = raw.get("authMethod")
    if method not in {
        "none",
        "claude.ai",
        "oauth_token",
        "api_key",
        "api_key_helper",
        "third_party",
    }:
        method = None
    signed_in = raw.get("loggedIn") is True and result.returncode == 0
    billing = (
        "subscription"
        if method == "claude.ai"
        else "api"
        if method in {"api_key", "api_key_helper"}
        else "unknown"
    )
    return {"signed_in": signed_in, "auth_method": method, "billing": billing}


class ClaudeConnection:
    """Own exactly one native process and bound every stdio frame and pending reader."""

    def __init__(
        self,
        executable: Path,
        workspace: Path,
        arguments: list[str],
        environment: dict[str, str],
    ) -> None:
        self.executable = executable
        self.workspace = workspace
        self.arguments = arguments
        self.environment = environment
        self._process: subprocess.Popen[str] | None = None
        self._events: queue.Queue[dict[str, Any] | Exception | None] = queue.Queue(maxsize=1024)
        self._stopped = threading.Event()
        self._reader: threading.Thread | None = None
        self._write_lock = threading.Lock()

    def start(self) -> None:
        """Start the official binary with native auth and no inherited API-key override."""
        if self._stopped.is_set():
            return
        try:
            self._process = subprocess.Popen(
                [str(self.executable), *self.arguments],
                cwd=self.workspace,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                errors="strict",
                env=self.environment,
                **process_options(),
            )
        except OSError:
            raise ProviderConnectionError("The Claude runtime could not start.") from None
        self._reader = threading.Thread(target=self._read, daemon=True)
        self._reader.start()
        if self._stopped.is_set():
            self.close()

    def _enqueue(self, event: dict[str, Any] | Exception | None) -> None:
        while not self._stopped.is_set():
            try:
                self._events.put(event, timeout=0.1)
                return
            except queue.Full:
                continue

    def _read(self) -> None:
        process = self._process
        assert process is not None and process.stdout is not None
        try:
            while not self._stopped.is_set():
                line = process.stdout.readline(MAX_FRAME_BYTES + 1)
                if not line:
                    break
                if len(line.encode("utf-8")) > MAX_FRAME_BYTES or not line.endswith("\n"):
                    raise ValueError("oversized or incomplete frame")
                event = json.loads(line)
                if not isinstance(event, dict):
                    raise ValueError("invalid event")
                self._enqueue(event)
        except (OSError, UnicodeError, ValueError):
            self._enqueue(InvalidResponseError("Claude returned an invalid runtime stream."))
        finally:
            self._enqueue(None)

    def write(self, message: dict[str, Any]) -> None:
        """Write a protocol frame without reflecting its private payload in failures."""
        with self._write_lock:
            process = self._process
            if self._stopped.is_set() or process is None or process.stdin is None:
                raise ProviderConnectionError("The Claude runtime has stopped.")
            try:
                process.stdin.write(json.dumps(message, ensure_ascii=True, allow_nan=False) + "\n")
                process.stdin.flush()
            except (OSError, ValueError):
                raise ProviderConnectionError("The Claude runtime connection closed.") from None

    def next_event(self, timeout: float) -> dict[str, Any]:
        """Fail closed on EOF, malformed framing, cancellation or runtime inactivity."""
        try:
            event = self._events.get(timeout=timeout)
        except queue.Empty:
            raise ProviderTimeoutError("Claude stopped responding. Saved work is kept.") from None
        if isinstance(event, Exception):
            raise event
        if event is None:
            raise ProviderConnectionError(
                "Claude exited before completing its response. Saved work is kept."
            )
        return event

    def close(self) -> None:
        """Collect this process only; never stop, log out or search for other Claude sessions."""
        self._stopped.set()
        process = self._process
        if process is not None:
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            for pipe in (process.stdin, process.stdout):
                if pipe is not None:
                    pipe.close()
        if self._reader is not None and self._reader is not threading.current_thread():
            self._reader.join(timeout=1)
        with suppress(queue.Full):
            self._events.put_nowait(None)
