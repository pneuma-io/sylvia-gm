"""Private stdio connection to Sylvia's isolated Codex App Server process.

The child manages ChatGPT credentials. Sylvia never reads its credential files,
inherits another Codex profile, or forwards a server request to a host tool.
"""

from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager, suppress
from pathlib import Path
from typing import Any


class CodexConnectionError(RuntimeError):
    """A privacy-safe connection failure suitable for the settings surface."""


class CodexCancelledError(CodexConnectionError):
    """This inference was intentionally stopped rather than losing its connection."""


def isolated_environment(home: Path) -> dict[str, str]:
    """Carry OS essentials into the child without provider secrets or Codex overrides."""
    allowed = {
        "systemroot",
        "windir",
        "comspec",
        "path",
        "pathext",
        "temp",
        "tmp",
        "userprofile",
        "homedrive",
        "homepath",
        "localappdata",
        "appdata",
        "programfiles",
        "programfiles(x86)",
        "programdata",
        "systemdrive",
    }
    environment = {key: value for key, value in os.environ.items() if key.lower() in allowed}
    environment["CODEX_HOME"] = str(home)
    environment["HOME"] = str(home)
    environment["USERPROFILE"] = str(home)
    environment["HOMEDRIVE"] = home.drive
    environment["HOMEPATH"] = str(home)[len(home.drive) :]
    environment["APPDATA"] = str(home / "AppData" / "Roaming")
    environment["LOCALAPPDATA"] = str(home / "AppData" / "Local")
    return environment


def find_codex_runtime() -> str | None:
    """Find a native runtime on PATH or in the Windows Codex desktop installation."""
    executable = shutil.which("codex")
    if executable and (os.name != "nt" or Path(executable).suffix.lower() == ".exe"):
        return executable
    if os.name == "nt" and os.environ.get("LOCALAPPDATA"):
        directory = Path(os.environ["LOCALAPPDATA"]) / "OpenAI" / "Codex" / "bin"
        candidates = list(directory.glob("*/codex.exe"))
        if candidates:
            return str(max(candidates, key=lambda path: path.stat().st_mtime))
    return None


class CodexConnection:
    """Serialize RPC calls while independently draining notifications and failures.

    Unexpected server requests are denied. Only explicitly supplied Sylvia tools
    are forwarded to the provider adapter, with execution retained by Sylvia.
    Raw server errors and stderr are never surfaced because they may contain secrets.
    """

    def __init__(
        self,
        home: Path,
        executable: Path | None = None,
        *,
        dynamic_tools: frozenset[str] = frozenset(),
    ) -> None:
        self.home = home.resolve()
        self.workspace = self.home / "workspace"
        self.executable = executable
        self.dynamic_tools = dynamic_tools
        self._overflow = False
        self._process: subprocess.Popen[str] | None = None
        self._write_lock = threading.Lock()
        self._call_lock = threading.RLock()
        self._responses: queue.Queue[dict[str, Any] | None] = queue.Queue()
        self._notifications: queue.Queue[dict[str, Any]] = queue.Queue(maxsize=8192)
        self._next_id = 0
        self._stop_requested = threading.Event()
        self._lifecycle_observer: Callable[[str, int, int, str], None] | None = None

    def set_lifecycle_observer(
        self, observer: Callable[[str, int, int, str], None],
    ) -> None:
        """Report local boundary metadata without exposing protocol or process content."""
        self._lifecycle_observer = observer

    def request_stop(self) -> None:
        """Fence this inference connection and wake its pending RPC without reading credentials."""
        if not self._stop_requested.is_set():
            self._stop_requested.set()
            self._responses.put(None)

    def _check_stopped(self) -> None:
        if self._stop_requested.is_set():
            raise CodexCancelledError("The ChatGPT response was cancelled.")

    @contextmanager
    def _measure(self, name: str) -> Iterator[None]:
        start, status = time.perf_counter_ns(), "completed"
        try:
            yield
        except BaseException as error:
            status = "cancelled" if isinstance(error, CodexCancelledError) else "error"
            raise
        finally:
            if self._lifecycle_observer is not None:
                with suppress(Exception):
                    self._lifecycle_observer(name, start, time.perf_counter_ns(), status)

    def start(self) -> None:
        """Start a dedicated stdio child with its own profile and no environment access."""
        with self._call_lock:
            self._check_stopped()
            if self._process is not None and self._process.poll() is None:
                return
            with self._measure("process_boot"):
                self._start_process()
            try:
                with self._measure("initialize"):
                    self.call(
                        "initialize",
                        {
                            "clientInfo": {
                                "name": "sylvia_gm",
                                "title": "Sylvia GM",
                                "version": "0.3.0",
                            },
                            "capabilities": {"experimentalApi": True},
                        },
                    )
                    self._write({"method": "initialized"})
            except BaseException:
                self.close()
                raise

    def _start_process(self) -> None:
        """Create and retain only this connection's isolated runtime instance."""
        resolved = str(self.executable) if self.executable else find_codex_runtime()
        if not resolved or (os.name == "nt" and Path(resolved).suffix.lower() != ".exe"):
            raise CodexConnectionError("Install the Codex runtime before connecting ChatGPT.")
        self.workspace.mkdir(parents=True, exist_ok=True)
        self._responses = queue.Queue()
        self._notifications = queue.Queue(maxsize=8192)
        self._overflow = False
        command = [
            resolved,
            "app-server",
            "--stdio",
            "-c",
            'cli_auth_credentials_store="file"',
            "-c",
            'forced_login_method="chatgpt"',
            "-c",
            "analytics.enabled=false",
            "-c",
            'history.persistence="none"',
            "-c",
            "features.shell_tool=false",
            "-c",
            "features.code_mode_host=true",
            "-c",
            'features.code_mode.direct_only_tool_namespaces=["sylvia"]',
            "-c",
            "features.multi_agent=false",
            "-c",
            "features.multi_agent_v2=false",
            "-c",
            "agents.enabled=false",
            "-c",
            "tools.experimental_request_user_input.enabled=false",
            "-c",
            "tools.update_plan.enabled=false",
            "-c",
            "skills.include_instructions=false",
            "-c",
            "features.apps=false",
            "-c",
            "features.goals=false",
            "-c",
            "features.hooks=false",
            "-c",
            "features.memories=false",
            "-c",
            "features.skip_host_skill_discovery=true",
            "-c",
            "features.skill_search=false",
            "-c",
            "features.tool_suggest=false",
            "-c",
            "features.sleep_tool=false",
            "-c",
            'web_search="disabled"',
        ]
        try:
            process = subprocess.Popen(
                command,
                cwd=self.workspace,
                env=isolated_environment(self.home),
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
                encoding="utf-8",
                bufsize=1,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            )
        except OSError:
            raise CodexConnectionError("The Codex runtime could not be started.") from None
        self._process = process
        threading.Thread(
            target=self._read,
            args=(process, self._responses, self._notifications),
            daemon=True,
            name="sylvia-chatgpt-rpc",
        ).start()

    def _write(self, message: dict[str, Any]) -> None:
        """Write one indivisible protocol record; never include it in an exception."""
        with self._write_lock:
            process = self._process
            if process is None or process.stdin is None or process.poll() is not None:
                raise CodexConnectionError(
                    "The ChatGPT connection has closed. Reconnect to continue."
                )
            try:
                process.stdin.write(json.dumps(message, ensure_ascii=True, allow_nan=False) + "\n")
                process.stdin.flush()
            except (OSError, ValueError):
                raise CodexConnectionError(
                    "The ChatGPT connection could not send a request."
                ) from None

    def _read(
        self,
        process: subprocess.Popen[str],
        responses: queue.Queue[dict[str, Any] | None],
        notifications: queue.Queue[dict[str, Any]],
    ) -> None:
        """Drain only this child's protocol; deny any unsolicited action request."""
        assert process.stdout is not None
        try:
            for line in process.stdout:
                if self._process is not process:
                    break
                message = json.loads(line)
                if not isinstance(message, dict):
                    break
                if "id" in message and "method" in message:
                    params = message.get("params", {})
                    if (
                        message["method"] == "item/tool/call"
                        and isinstance(params, dict)
                        and params.get("namespace") == "sylvia"
                        and isinstance(params.get("tool"), str)
                        and params.get("tool") in self.dynamic_tools
                    ):
                        notifications.put_nowait(message)
                        continue
                    self._write(
                        {
                            "id": message["id"],
                            "error": {
                                "code": -32601,
                                "message": "This capability is not available in Sylvia.",
                            },
                        }
                    )
                elif "id" in message:
                    responses.put(message)
                elif isinstance(message.get("method"), str):
                    notifications.put_nowait(message)
        except queue.Full:
            self._overflow = True
            with suppress(OSError):
                process.terminate()
        except (OSError, ValueError, CodexConnectionError):
            pass
        finally:
            responses.put(None)

    def call(self, method: str, params: dict[str, Any], *, timeout: float = 30) -> dict[str, Any]:
        """Wait for a correlated reply and close on timeout to prevent stale work reuse."""
        with self._call_lock:
            self._check_stopped()
            self._next_id += 1
            request_id = self._next_id
            self._write({"id": request_id, "method": method, "params": params})
            try:
                response = self._responses.get(timeout=timeout)
            except queue.Empty:
                self.close()
                raise CodexConnectionError(
                    "The ChatGPT connection timed out. Reconnect to retry."
                ) from None
            self._check_stopped()
            if response is None or response.get("id") != request_id:
                self.close()
                raise CodexConnectionError("The ChatGPT connection ended before replying.")
            if "error" in response:
                raise CodexConnectionError(
                    "The Codex runtime rejected this operation. "
                    "Check sign-in and runtime compatibility."
                )
            result = response.get("result")
            if not isinstance(result, dict):
                raise CodexConnectionError("The Codex runtime returned an unsupported reply.")
            return result

    def notifications(self) -> list[dict[str, Any]]:
        """Drain pending protocol notifications for owner-side interpretation only."""
        items = []
        if self._overflow:
            raise CodexConnectionError("The ChatGPT event stream exceeded its safe buffer.")
        while True:
            try:
                items.append(self._notifications.get_nowait())
            except queue.Empty:
                return items

    def next_event(self, timeout: float = 0.25) -> dict[str, Any] | None:
        """Read one event with a short bounded wait so async cancellation stays responsive."""
        self._check_stopped()
        if self._overflow:
            raise CodexConnectionError("The ChatGPT event stream exceeded its safe buffer.")
        try:
            event = self._notifications.get(timeout=timeout)
            self._check_stopped()
            return event
        except queue.Empty:
            if self._process is None or self._process.poll() is not None:
                raise CodexConnectionError(
                    "The ChatGPT runtime stopped before completing its response."
                ) from None
            return None

    def close(self) -> None:
        """Stop only the child this connection created; leave its login persisted."""
        with self._call_lock:
            process = self._process
            if process is None:
                return
            if process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=3)
            self._process = None
            for stream in (process.stdin, process.stdout):
                if stream is not None:
                    stream.close()
