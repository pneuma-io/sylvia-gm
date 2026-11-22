"""Generate reference vectors for sylvia-provider's account-runtime process layer (lane 5).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-provider/tests/vectors/generate_runtime_vectors.py

The frozen `provider/_claude_runtime.py` and `provider/_codex_rpc.py` run unchanged. What depends
on the machine is supplied: the environment and platform through stand-ins for the modules'
`os` and `sys`, `subprocess.run`'s result, and the child's pipes as `io.TextIOWrapper` over
bytes, as `Popen` opens them in text mode. Recorded:

- `context_windows`: `documented_context_window`;
- `native_environments` and `isolated_environments`: the child's environment, both platforms;
- `auth_commands`: `native_auth_command`;
- `versions` and `auth_statuses`: `runtime_version` and `authentication_status` on given runs;
- `runtimes`: `find_runtime` on given install folders;
- `claude_frames`: what `ClaudeConnection._read` makes of the bytes a runtime writes;
- `claude_writes`: `ClaudeConnection.write` frames, as text before newline translation;
- `codex_reads`: how `CodexConnection._read` routes what the App Server writes (responses,
  notifications, the denials it writes back);
- `codex_environment_names` and `missing_runtime`: `missing_runtime_message`.

Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import io
import json
import os
import queue
import subprocess
import sys
import tempfile
import types
from pathlib import Path, PurePosixPath, PureWindowsPath

from sylvia_gm.provider import _claude_runtime, _codex_rpc
from sylvia_gm.provider._claude_runtime import ClaudeConnection
from sylvia_gm.provider._codex_rpc import CodexConnection

HERE = Path(__file__).parent
OUTPUT = HERE / "runtime_vectors.json"

WINDOWS_ENVIRON = {
    "SYSTEMROOT": "C:\\Windows", "PATH": "C:\\Windows\\system32;C:\\Tools", "PATHEXT": ".COM;.EXE",
    "TEMP": "C:\\Temp", "USERPROFILE": "C:\\Users\\dev", "LOCALAPPDATA": "C:\\Users\\dev\\AppData\\Local",
    "ANTHROPIC_API_KEY": "secret", "OPENAI_API_KEY": "secret", "CODEX_HOME": "C:\\other", "LANG": "en_AU.UTF-8",
    "PROGRAMFILES(X86)": "C:\\Program Files (x86)", "DISPLAY": ":0", "HOMEDRIVE": "C:", "HOMEPATH": "\\Users\\dev",
}
LINUX_ENVIRON = {
    "PATH": "/tmp/.mount_SylviaX/usr/bin:/usr/bin:/bin", "HOME": "/home/dev", "LANG": "C.UTF-8", "LC_ALL": "C",
    "Path": "/odd", "APPDIR": "/tmp/.mount_SylviaX", "LD_LIBRARY_PATH": "/tmp/.mount_SylviaX/usr/lib",
    "DISPLAY": ":0", "WAYLAND_DISPLAY": "wayland-0", "XDG_RUNTIME_DIR": "/run/user/1000",
    "DBUS_SESSION_BUS_ADDRESS": "unix:path=/run/user/1000/bus", "ANTHROPIC_API_KEY": "secret",
    "XDG_CONFIG_HOME": "/home/dev/.config", "BROWSER": "firefox", "TMP": "/tmp",
}


def platform_module(name: str):
    return types.SimpleNamespace(platform=name)


def native_environments() -> list[dict]:
    out = []
    cases = [
        ("windows", "win32", WINDOWS_ENVIRON, PureWindowsPath("C:\\Users\\dev\\AppData\\Local\\Sylvia GM\\claude")),
        ("linux", "linux", LINUX_ENVIRON, PurePosixPath("/home/dev/.local/share/sylvia-gm/claude")),
        ("linux-plain", "linux", {"PATH": "/usr/bin", "HOME": "/home/dev"}, PurePosixPath("/srv/claude")),
    ]
    original_os, original_sys = _claude_runtime.os, _claude_runtime.sys
    try:
        for name, platform, environ, home in cases:
            _claude_runtime.os = types.SimpleNamespace(environ=dict(environ))
            _claude_runtime.sys = platform_module(platform)
            result = _claude_runtime.native_environment(home)
            out.append({"name": name, "environ": list(environ.items()), "home": str(home),
                        "separator": "\\" if isinstance(home, PureWindowsPath) else "/",
                        "linux": platform.startswith("linux"), "windows": platform == "win32",
                        "environment": list(result.items())})
    finally:
        _claude_runtime.os, _claude_runtime.sys = original_os, original_sys
    return out


def isolated_environments() -> list[dict]:
    out = []
    cases = [
        ("windows", WINDOWS_ENVIRON, PureWindowsPath("C:\\Users\\dev\\AppData\\Local\\Sylvia GM\\chatgpt")),
        ("windows-unc", WINDOWS_ENVIRON, PureWindowsPath("\\\\server\\share\\sylvia\\chatgpt")),
        ("linux", LINUX_ENVIRON, PurePosixPath("/home/dev/.local/share/sylvia-gm/chatgpt")),
    ]
    original = _codex_rpc.os
    try:
        for name, environ, home in cases:
            _codex_rpc.os = types.SimpleNamespace(environ=dict(environ))
            result = _codex_rpc.isolated_environment(home)
            out.append({"name": name, "environ": list(environ.items()), "home": str(home), "drive": home.drive,
                        "separator": "\\" if isinstance(home, PureWindowsPath) else "/",
                        "environment": list(result.items())})
    finally:
        _codex_rpc.os = original
    return out


class Completed:
    def __init__(self, stdout, returncode):
        self.stdout, self.returncode = stdout, returncode


def with_run(outcome, call):
    """Run `call` with `subprocess.run` answering `outcome`: (stdout, returncode), or an error."""
    original = _claude_runtime.subprocess.run

    def run(*_args, **_kwargs):
        if outcome == "oserror":
            raise OSError("no such file")
        if outcome == "timeout":
            raise subprocess.TimeoutExpired("claude", 10)
        return Completed(*outcome)

    _claude_runtime.subprocess.run = run
    try:
        return {"value": call()}
    except Exception as exc:  # noqa: BLE001
        return {"error": [type(exc).__name__, str(exc)]}
    finally:
        _claude_runtime.subprocess.run = original


def versions() -> list[dict]:
    outcomes = [
        ("2.1.322 (Claude Code)\n", 0), ("2.1.400 (Claude Code)", 0), ("2.1.321 (Claude Code)\n", 0),
        ("10.0.0 (Claude Code)  \n\n", 0), ("2.1.322 (Claude Code) extra\n", 0), ("", 0),
        ("\u0662.\u0661.\u0663\u0662\u0662 (Claude Code)\n", 0), ("2.1.322 (Claude Code)\n", 1),
        ("02.01.0400 (Claude Code)\n", 0), ("2.1 (Claude Code)\n", 0), ("v2.1.322 (Claude Code)\n", 0),
        ("2.1.322 (Claude Code)\u00a0\n", 0), ("2.1.322 (Claude Code)\n", -9), "oserror", "timeout",
    ]
    home = Path(tempfile.gettempdir())
    return [{"outcome": outcome, **with_run(outcome, lambda: _claude_runtime.runtime_version(Path("claude"), home))}
            for outcome in outcomes]


def auth_statuses() -> list[dict]:
    outcomes = [
        ('{"loggedIn": true, "authMethod": "claude.ai", "email": "x@y"}', 0),
        ('{"loggedIn": true, "authMethod": "api_key"}', 0),
        ('{"loggedIn": true, "authMethod": "api_key_helper"}', 1),
        ('{"loggedIn": false, "authMethod": "none"}', 1),
        ('{"loggedIn": 1, "authMethod": "oauth_token"}', 0),
        ('{"authMethod": "something_new"}', 0),
        ('{"loggedIn": true, "authMethod": ["claude.ai"]}', 0),
        ('{"loggedIn": true}', 2),
        ('[1, 2]', 0), ("not json", 0), ("", 1), (' {"loggedIn": true} \n', 0),
        "oserror", "timeout",
    ]
    home = Path(tempfile.gettempdir())
    return [{"outcome": outcome,
             **with_run(outcome, lambda: _claude_runtime.authentication_status(Path("claude"), home))}
            for outcome in outcomes]


def runtimes() -> list[dict]:
    cases = [
        [],
        [["2.1.322", "file"]],
        [["2.1.322", "file"], ["2.1.400", "file"], ["2.10.1", "file"], ["2.9.9", "file"]],
        [["2.1.500", "dir"], ["2.1.400", "file"], ["2.1.600", "none"]],
        [["latest", "file"], ["2.1", "file"], ["2.1.322.1", "file"], ["v2.1.322", "file"]],
        [["\u0662.\u0661.\u0665", "file"], ["2.1.4", "file"]],
        [["10.0.0", "file"], ["9.99.99", "file"]],
    ]
    out = []
    for entries in cases:
        with tempfile.TemporaryDirectory() as folder:
            home = Path(folder)
            for name, kind in entries:
                (home / "install" / name).mkdir(parents=True)
                target = home / "install" / name / _claude_runtime.executable_name()
                if kind == "file":
                    target.write_bytes(b"")
                elif kind == "dir":
                    target.mkdir()
            found = _claude_runtime.find_runtime(home)
            out.append({"entries": entries, "chosen": None if found is None else found.parent.name})
    return out


def claude_frames() -> list[dict]:
    cases = {
        "events": b'{"type": "system", "subtype": "init"}\n{"type": "assistant", "message": {"content": []}}\n',
        "crlf": b'{"type": "a"}\r\n{"type": "b"}\r{"type": "c"}\n',
        "heartbeats": b'{"type": "tool_progress", "n": 1}\n{"type": "result"}\n',
        "unicode": '{"type": "text", "text": "\u00c9tienne \U0001f3b2"}\n'.encode(),
        "no-final-newline": b'{"type": "a"}\n{"type": "b"}',
        "not-an-object": b'{"type": "a"}\n[1, 2]\n{"type": "b"}\n',
        "invalid-json": b'{"type": "a"}\n{oops\n',
        "blank-line": b'{"type": "a"}\n\n{"type": "b"}\n',
        "bad-utf8": b'{"type": "a"}\n{"type": "\xff"}\n',
        "empty": b"",
        "nan": b'{"type": "a", "value": 1.5, "big": 12345678901234567890}\n',
    }
    out = []
    for name, data in cases.items():
        connection = ClaudeConnection(Path("claude"), Path("."), [], {})
        connection._process = types.SimpleNamespace(
            stdout=io.TextIOWrapper(io.BytesIO(data), encoding="utf-8", errors="strict"))
        connection._read()
        events = []
        while True:
            try:
                item = connection._events.get_nowait()
            except queue.Empty:
                break
            if item is None:
                events.append({"exit": True})
            elif isinstance(item, Exception):
                events.append({"error": [type(item).__name__, str(item)]})
            else:
                events.append({"event": item})
        out.append({"name": name, "data": data.decode("latin-1"), "events": events})
    return out


class Capture:
    def __init__(self):
        self.parts: list[str] = []

    def write(self, text):
        self.parts.append(text)

    def flush(self):
        pass


def claude_writes() -> list[dict]:
    messages = [
        {"type": "user", "message": {"role": "user", "content": "I open the door. \u00c9tienne \U0001f3b2"}},
        {"type": "control_response", "response": {"subtype": "success", "request_id": "r1", "value": [1, 2.5, True, None]}},
    ]
    out = []
    for message in messages:
        connection = ClaudeConnection(Path("claude"), Path("."), [], {})
        capture = Capture()
        connection._process = types.SimpleNamespace(stdin=capture)
        connection.write(message)
        out.append({"message": message, "text": "".join(capture.parts)})
    return out


def codex_reads() -> list[dict]:
    lines = [
        {"id": 1, "result": {"userAgent": "codex"}},
        {"method": "thread/started", "params": {"thread": {"id": "t"}}},
        {"id": 7, "method": "item/tool/call", "params": {"namespace": "sylvia", "tool": "roll", "arguments": {}}},
        {"id": 8, "method": "item/tool/call", "params": {"namespace": "sylvia", "tool": "unknown"}},
        {"id": 9, "method": "item/tool/call", "params": {"namespace": "other", "tool": "roll"}},
        {"id": "x", "method": "execCommandApproval", "params": {"command": ["rm"]}},
        {"id": 10, "method": "item/tool/call", "params": ["not", "a", "dict"]},
        {"id": 2, "error": {"code": 1, "message": "secret"}},
        {"method": 5},
        {"note": "no id or method"},
        {"method": "turn/completed"},
    ]
    stream = "".join(json.dumps(line) + "\n" for line in lines).encode()
    cases = {
        "routing": stream,
        "crlf": stream.replace(b"\n", b"\r\n"),
        "stops-at-invalid-json": b'{"method": "a"}\n{oops\n{"method": "b"}\n',
        "stops-at-non-object": b'{"method": "a"}\n[1]\n{"method": "b"}\n',
        "stops-at-blank-line": b'{"method": "a"}\n\n{"method": "b"}\n',
        "bad-utf8": b'{"method": "a"}\n{"method": "\xff"}\n',
    }
    out = []
    for name, data in cases.items():
        with tempfile.TemporaryDirectory() as folder:
            connection = CodexConnection(Path(folder), dynamic_tools=frozenset({"roll", "look"}))
            capture = Capture()
            process = types.SimpleNamespace(
                stdout=io.TextIOWrapper(io.BytesIO(data), encoding="utf-8"), stdin=capture, poll=lambda: None)
            connection._process = process
            responses: queue.Queue = queue.Queue()
            notifications: queue.Queue = queue.Queue(maxsize=8192)
            connection._read(process, responses, notifications)
            received = []
            while not responses.empty():
                received.append(responses.get_nowait())
            forwarded = []
            while not notifications.empty():
                forwarded.append(notifications.get_nowait())
            out.append({"name": name, "data": data.decode("latin-1"), "responses": received,
                        "notifications": forwarded, "written": "".join(capture.parts)})
    return out


def main() -> int:
    vectors = {
        "python": sys.version.split()[0],
        "context_windows": [{"model": model, "window": _claude_runtime.documented_context_window(model)}
                            for model in ["opus", "sonnet[1m]", "claude-opus-5-5", "claude-opus-5-5[1m]", "fable",
                                          "claude-unknown", "[1m]", "opus[1m][1m]", 5, None]],
        "native_environments": native_environments(),
        "isolated_environments": isolated_environments(),
        "auth_commands": [{"executable": "C:\\Sylvia\\claude.exe", "action": action,
                           "command": _claude_runtime.native_auth_command(PureWindowsPath("C:\\Sylvia\\claude.exe"), action)}
                          for action in ("status", "login", "logout")],
        "versions": versions(),
        "auth_statuses": auth_statuses(),
        "runtimes": runtimes(),
        "claude_frames": claude_frames(),
        "claude_writes": claude_writes(),
        "codex_reads": codex_reads(),
        "missing_runtime": {"windows": _codex_rpc.missing_runtime_message(True),
                            "other": _codex_rpc.missing_runtime_message(False)},
        "minimum_version": _claude_runtime.MINIMUM_VERSION_TEXT,
        "max_frame_bytes": _claude_runtime.MAX_FRAME_BYTES,
    }
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
