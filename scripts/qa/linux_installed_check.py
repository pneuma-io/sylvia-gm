#!/usr/bin/env python3
"""Installed-application check for the Sylvia GM Linux AppImage, run from one command.

    python3 scripts/qa/linux_installed_check.py --appimage PATH --output NEW_FOLDER

It runs the release AppImage on a hidden X display (Xvfb) with a window manager and its own
D-Bus session, a local stand-in for the model service (no paid model calls, no sign-ins) and
empty private home folders, then works through the installed-application steps of
docs/operations/linux-release-checklist.md. It writes report.json, report.md, screenshots and
logs to the output folder, stops every process it started and exits 0 only if every step
that ran passed (1 if a step failed, 2 if the machine or environment was refused).

How the app is driven: through WebDriver (tauri-driver in front of the system's
WebKitWebDriver), which finds page elements by id and reads their text, so the check does
not depend on screen coordinates. WebDriver starts the unpacked AppImage through its own
AppRun, unchanged. Native GTK dialogs (message boxes, save and open dialogs) are outside the
web view; the check answers them with xdotool key presses, never clicks. The API key is also
typed with xdotool, because WebKitWebDriver drops the connection when it types into a
password field. The lifecycle steps (second start, close, the data-folder error box) start
the AppImage file itself without WebDriver, so their exit codes are the app's own.

Safety rules the script enforces, because the build machine may be a WSL install that shares
the user's Windows session and PATH:
- the app never sees a Wayland display, so no window can appear on a real desktop;
- PATH loses every Windows (/mnt/...) entry, so no Windows program or sign-in is reachable;
- every process gets an allow-listed environment: no provider keys, no XDG overrides, no
  SYLVIA_GM_* settings, and a home folder made for this run;
- files are written only in the output folder and the run's own work folder, apart from what
  the system itself keeps in /tmp for the display and the AppImage mount while they run.

Machine preparation (once): docs/operations/linux-release-checklist.md, section 5.
The plain-Python parts (guards, report) are tested in tests/test_linux_installed_check.py.
"""

from __future__ import annotations

import argparse
import contextlib
import hashlib
import http.client
import json
import os
import platform
import re
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

REPORT_FORMAT = "sylvia-gm-linux-installed-check"
REPORT_VERSION = 1
DATA_FOLDER = ".local/share/com.pneuma-io.sylvia-gm"
KEY_FILE = ".local/state/sylvia-gm/credential-key"
WINDOW_TITLE = "Sylvia GM"
DATA_UNAVAILABLE_TITLE = "Sylvia GM - selected data unavailable"
MARKER_VARIABLE = "SYLVIA_INSTALLED_CHECK_RUN"
LOWEST_DISPLAY = 90
LOWEST_PORT = 8790
# Only these variables pass from the caller's environment to anything the check starts.
# Everything else (provider keys, XDG overrides, SYLVIA_GM_* settings, Wayland) stays out.
PASSED_VARIABLES = ("LANG", "LC_ALL", "LC_CTYPE", "TZ", "USER", "LOGNAME", "SHELL", "TERM")
STANDIN = Path(__file__).resolve().with_name("standin_model_endpoint.py")
# Texts the stand-in sends (see standin_model_endpoint.py); the check looks for them on screen.
NARRATION_MARK = "The stand-in narrator answers."
TURN_REPLY_MARK = "The stand-in narrator resolves your action."
# Common to both. Depending on the version, the opening is plain narration or, like a player
# turn, ends with the Campaign File tool call that carries the turn reply.
STANDIN_MARK = "The stand-in narrator"
PLAYER_MESSAGE = "I sit by the fire and ask the keeper for tea."
CHAT_MESSAGE = "Good evening. Is there a room free tonight?"
CARD_NAME = "Check Keeper"
EXPORT_NAME = "installed-check-export.sylvia-gm-campaign"
# The stand-in reports this context size; the check enters it as the model's context cap, as
# a person does for a custom service whose loaded limit Sylvia cannot detect.
CONTEXT_CAP = "4000000"
REPLY_CAP = "4096"
# Slow machines (a shared virtual machine, a first start that prepares the database) need
# generous limits; a step still ends as soon as what it waits for appears.
APP_START_SECONDS = 420
OWNER_SECONDS = 300
# The data-folder conflict is found as the app starts, so the box should come well within this.
BOX_SECONDS = 180

PASSED, FAILED, NOT_RUN = "passed", "failed", "not run"


class GuardError(RuntimeError):
    """The machine or environment is not safe for the check; nothing has been started."""


class StepError(RuntimeError):
    """One check step saw something other than what it requires."""


# ---------------------------------------------------------------------------------------------
# Environment guards


def clean_path(value: str) -> str:
    """Remove Windows folders (WSL mounts them under /mnt/) and empty entries from a PATH."""

    return ":".join(entry for entry in value.split(":") if entry and not entry.startswith("/mnt/"))


def path_problems(value: str) -> list[str]:
    """PATH entries that still lead to /mnt/, including through a symbolic link."""

    problems = []
    for entry in value.split(":"):
        if not entry:
            continue
        if entry.startswith("/mnt/") or os.path.realpath(entry).startswith("/mnt/"):
            problems.append(entry)
    return problems


def display_number(display: str) -> int:
    match = re.fullmatch(r":(\d+)", display)
    if not match:
        raise GuardError(f"the display must look like :{LOWEST_DISPLAY}, not {display!r}")
    return int(match.group(1))


def child_environment(
    base: Mapping[str, str],
    *,
    home: Path,
    runtime: Path,
    display: str,
    dbus_address: str,
    marker: str,
    extra_path: Sequence[str] = (),
) -> dict[str, str]:
    """Build the allow-listed environment for every process the check starts."""

    env = {name: base[name] for name in PASSED_VARIABLES if name in base}
    env.update({
        "PATH": clean_path(":".join([*extra_path, base.get("PATH", "/usr/bin:/bin")])),
        "HOME": str(home),
        "XDG_RUNTIME_DIR": str(runtime),
        "DISPLAY": display,
        "GDK_BACKEND": "x11",
        # A virtual display leaves parts of the web view unpainted with compositing on.
        "WEBKIT_DISABLE_COMPOSITING_MODE": "1",
        "DBUS_SESSION_BUS_ADDRESS": dbus_address,
        # Every process the check starts, and their children, carry this marker, so the final
        # clean-up can find anything that outlived its parent.
        MARKER_VARIABLE: marker,
    })
    env.setdefault("LANG", "C.UTF-8")
    return env


def guard_environment(env: Mapping[str, str], *, real_home: str) -> None:
    """Refuse an environment that could reach a real desktop, Windows programs or real data."""

    if env.get("WAYLAND_DISPLAY") or env.get("WAYLAND_SOCKET"):
        raise GuardError("WAYLAND_DISPLAY would reach a real desktop session")
    if env.get("GDK_BACKEND") != "x11":
        raise GuardError("GDK_BACKEND must be x11 so GTK uses the hidden X display")
    if display_number(env.get("DISPLAY", "")) < LOWEST_DISPLAY:
        raise GuardError(f"the hidden display must be :{LOWEST_DISPLAY} or higher")
    problems = path_problems(env.get("PATH", ""))
    if problems:
        raise GuardError(f"PATH still reaches Windows folders: {problems}")
    home = env.get("HOME", "")
    if not home or os.path.realpath(home) == os.path.realpath(real_home):
        raise GuardError("the app must not run against the real home folder")
    for name in ("XDG_DATA_HOME", "XDG_CONFIG_HOME", "XDG_CACHE_HOME", "XDG_STATE_HOME"):
        if name in env:
            raise GuardError(f"{name} must not be set; the app's folders follow HOME")
    if any(name.startswith("SYLVIA_GM_") for name in env):
        raise GuardError("SYLVIA_GM_* settings would redirect the app's data")


def display_is_free(number: int) -> bool:
    return not (Path(f"/tmp/.X{number}-lock").exists()
                or Path(f"/tmp/.X11-unix/X{number}").exists())


def port_is_free(port: int) -> bool:
    """True when nothing listens on the port.

    A run that has just ended leaves its connections waiting to close (TIME_WAIT). The
    services the check starts bind with SO_REUSEADDR and are not stopped by that, so the probe
    binds the same way: only a listening socket makes the port unavailable.
    """

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        probe.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


REQUIRED_PROGRAMS = ("Xvfb", "openbox", "dbus-daemon", "xdotool", "wmctrl", "import",
                     "tauri-driver", "WebKitWebDriver")


def missing_programs(path: str) -> list[str]:
    return [name for name in REQUIRED_PROGRAMS if not shutil.which(name, path=path)]


# ---------------------------------------------------------------------------------------------
# Report


@dataclass
class Step:
    number: int
    key: str
    title: str
    needs: tuple[int, ...] = ()
    status: str = NOT_RUN
    reason: str = ""
    facts: dict[str, object] = field(default_factory=dict)
    screenshots: list[str] = field(default_factory=list)
    seconds: float = 0.0

    def as_dict(self) -> dict[str, object]:
        return {
            "number": self.number, "key": self.key, "title": self.title, "status": self.status,
            "reason": self.reason, "facts": self.facts, "screenshots": self.screenshots,
            "seconds": round(self.seconds, 1),
        }


def summarise(steps: Sequence[Step]) -> dict[str, object]:
    counts = {status: sum(1 for step in steps if step.status == status)
              for status in (PASSED, FAILED, NOT_RUN)}
    return {"counts": counts, "passed": counts[FAILED] == 0 and counts[PASSED] > 0}


def exit_code(summary: Mapping[str, object]) -> int:
    return 0 if summary["passed"] else 1


def build_report(steps: Sequence[Step], context: Mapping[str, object]) -> dict[str, object]:
    return {
        "format": REPORT_FORMAT,
        "version": REPORT_VERSION,
        **context,
        "summary": summarise(steps),
        "steps": [step.as_dict() for step in sorted(steps, key=lambda step: step.number)],
        "not_covered": list(NOT_COVERED),
    }


def _cell(text: object) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ")


def render_markdown(report: Mapping[str, object]) -> str:
    """A readable report; report.json holds the same facts for tools."""

    summary = report["summary"]
    counts = summary["counts"]
    verdict = "PASSED" if summary["passed"] else "FAILED"
    lines = [
        "# Sylvia GM Linux installed-application check",
        "",
        f"**Result: {verdict}**: {counts[PASSED]} passed, {counts[FAILED]} failed, "
        f"{counts[NOT_RUN]} not run.",
        "",
    ]
    for label, key in (("AppImage", "appimage"), ("AppImage SHA-256", "appimage_sha256"),
                       ("Started", "started"), ("Duration", "duration_seconds"),
                       ("Driving method", "method"), ("System", "system"),
                       ("Refused", "refused")):
        if report.get(key):
            value = f"{report[key]} s" if key == "duration_seconds" else report[key]
            lines.append(f"- {label}: {value}")
    lines += ["", "| # | Step | Result | Time | Reason |", "|---|---|---|---|---|"]
    for step in report["steps"]:
        lines.append(f"| {step['number']} | {_cell(step['title'])} | {step['status']} | "
                     f"{step['seconds']} s | {_cell(step['reason'])} |")
    for step in report["steps"]:
        lines += ["", f"## {step['number']}. {step['title']}: {step['status']}", ""]
        if step["reason"]:
            lines += [step["reason"], ""]
        for name, value in step["facts"].items():
            shown = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
            # Page text spans lines; keep each fact on one list line.
            shown = " / ".join(part.strip() for part in shown.splitlines() if part.strip())
            lines.append(f"- {name}: {shown}")
        for shot in step["screenshots"]:
            lines.append(f"- screenshot: [{shot}]({shot})")
    if report.get("not_covered"):
        lines += ["", "## Not covered by this check", ""]
        lines += [f"- {note}" for note in report["not_covered"]]
    return "\n".join(lines) + "\n"


NOT_COVERED = (
    "How the app looks with the desktop's own compositing, including a Wayland session: the "
    "check uses a hidden X display with web-view compositing turned off.",
    "Open drop-down lists and file dialogs in the desktop's theme.",
    "Starting from the application menu, which decides whether separately installed programs "
    "such as the Codex runtime are found.",
    "The clipboard and detached panel windows.",
    "Real model services, sign-ins and paid calls: the model service is a local stand-in, and "
    "the Claude page is only checked, never installed or signed in.",
    "Where the save dialog opens is shown in its screenshot, and the check confirms it by "
    "saving a bare file name, which lands in the dialog's starting folder.",
    "The WebDriver steps run the unpacked AppImage through its own AppRun; only the lifecycle "
    "steps (7, 8 and 11) mount the AppImage file itself.",
)


def step_plan() -> list[Step]:
    """The steps, numbered as in the checklist; `needs` names steps that must have passed."""

    return [
        Step(1, "first-start", "First start shows Home; data folder mode 700"),
        Step(2, "provider", "API-key connection to the stand-in, saved and made default", (1,)),
        Step(3, "prepared-campaign", "Prepared campaign starts; the opening narration arrives",
             (2,)),
        Step(4, "player-turn", "A full player turn with tools offered completes", (3,)),
        Step(5, "character-chat", "Character chat with an imported card gets a reply", (2,)),
        Step(6, "export", "Export the campaign to a file in the home folder", (3,)),
        Step(7, "second-start", "A second start while running exits 0; one window remains",
             (1,)),
        Step(8, "close", "Closing the window exits 0 and leaves no process", (7,)),
        Step(9, "restart", "Restart on the same home: the campaign is still listed", (3,)),
        Step(10, "restore", "Recover the exported file into an empty profile and open it", (6,)),
        Step(11, "data-unavailable-box",
             "A copy on the same data folder (separate D-Bus) shows the error box", (7,)),
        Step(12, "linux-notes", "What Linux leaves out is stated plainly", (1,)),
    ]


# ---------------------------------------------------------------------------------------------
# Processes


class Processes:
    """Starts helper processes in their own process groups and stops all of them at the end."""

    def __init__(self, logs: Path, marker: str):
        self.logs = logs
        self.marker = marker
        self.running: list[tuple[str, subprocess.Popen]] = []

    def start(self, name: str, argv: Sequence[str], env: Mapping[str, str],
              cwd: Path | None = None) -> subprocess.Popen:
        with open(self.logs / f"{name}.log", "ab") as log:
            process = subprocess.Popen(
                list(argv), env=dict(env), cwd=cwd, stdout=log, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL, start_new_session=True,
            )
        self.running.append((name, process))
        return process

    def stop(self, process: subprocess.Popen, grace: float = 10.0) -> int | None:
        if process.poll() is None:
            _signal_group(process.pid, signal.SIGTERM)
            try:
                process.wait(grace)
            except subprocess.TimeoutExpired:
                _signal_group(process.pid, signal.SIGKILL)
                process.wait(5)
        _signal_group(process.pid, signal.SIGKILL)  # anything left in its group
        self.running = [(name, item) for name, item in self.running if item is not process]
        return process.returncode

    def stop_all(self) -> list[str]:
        for _name, process in reversed(list(self.running)):
            self.stop(process, grace=5.0)
        return sweep_marked_processes(self.marker)


def _signal_group(pid: int, number: int) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(pid, number)


def marked_processes(marker: str) -> list[tuple[int, str]]:
    """Processes whose environment carries this run's marker, with their command names."""

    found = []
    needle = f"{MARKER_VARIABLE}={marker}".encode()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            environ = (entry / "environ").read_bytes()
            name = (entry / "comm").read_text().strip()
        except OSError:
            continue
        if needle in environ.split(b"\0"):
            found.append((int(entry.name), name))
    return found


def process_tree(marker: str) -> list[str]:
    """This run's processes as "pid parent state name" lines, for evidence in the report."""

    lines = []
    for pid, name in marked_processes(marker):
        try:
            fields = Path(f"/proc/{pid}/stat").read_text().rsplit(")", 1)[1].split()
        except (OSError, IndexError):
            continue
        lines.append(f"{pid} parent {fields[1]} state {fields[0]} {name}")
    return lines


def sweep_marked_processes(marker: str) -> list[str]:
    """Kill anything of this run that outlived its parent; return what had to be killed."""

    killed = []
    for pid, name in marked_processes(marker):
        try:
            os.kill(pid, signal.SIGKILL)
            killed.append(f"{pid} {name}")
        except OSError:
            continue
    return killed


def run(argv: Sequence[str], env: Mapping[str, str], timeout: float = 30.0
        ) -> subprocess.CompletedProcess:
    return subprocess.run(list(argv), env=dict(env), capture_output=True, text=True,
                          timeout=timeout, check=False)


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as stream:
        for block in iter(lambda: stream.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def system_name() -> str:
    try:
        for line in Path("/etc/os-release").read_text().splitlines():
            if line.startswith("PRETTY_NAME="):
                return f"{line.split('=', 1)[1].strip(chr(34))}, kernel {platform.release()}"
    except OSError:
        pass
    return f"{platform.system()} {platform.release()}"


# ---------------------------------------------------------------------------------------------
# WebDriver (W3C protocol over HTTP; only what the check needs)


ELEMENT = "element-6066-11e4-a52e-4f735466cecf"  # the W3C element reference key
# WebDriver refusals that only mean "not yet": the page is still redrawing.
RETRIED_REFUSALS = ("not interactable", "click intercepted", "stale element", "no such element")


class WebDriverError(RuntimeError):
    pass


class WebDriver:
    def __init__(self, port: int):
        self.base = f"http://127.0.0.1:{port}"
        self.session: str | None = None

    def _call(self, method: str, path: str, body: object = None, timeout: float = 60.0) -> object:
        data = None if body is None else json.dumps(body).encode("utf-8")
        payload: object = None
        for attempt in range(3):
            request = urllib.request.Request(self.base + path, data=data, method=method,
                                             headers={"Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request, timeout=timeout) as response:
                    payload = json.loads(response.read() or b"{}")
                break
            except urllib.error.HTTPError as error:
                detail = error.read().decode("utf-8", "replace")[:500]
                raise WebDriverError(f"{method} {path}: HTTP {error.code} {detail}") from None
            except (http.client.RemoteDisconnected, ConnectionResetError) as error:
                # tauri-driver sometimes forwards a request over a connection to
                # WebKitWebDriver that has just closed; it then drops our connection without
                # an answer. The request never reached the web view, so sending it again is safe.
                if attempt == 2:
                    raise WebDriverError(f"{method} {path}: {error}") from None
                time.sleep(0.5)
            except (urllib.error.URLError, TimeoutError, ConnectionError) as error:
                raise WebDriverError(f"{method} {path}: {error}") from None
        return payload.get("value") if isinstance(payload, dict) else None

    def ready(self) -> bool:
        try:
            self._call("GET", "/status", timeout=3)
        except WebDriverError:
            return False
        return True

    def start(self, application: str, timeout: float) -> None:
        value = self._call("POST", "/session", {"capabilities": {"alwaysMatch": {
            "tauri:options": {"application": application}}}}, timeout=timeout)
        if not isinstance(value, dict) or "sessionId" not in value:
            raise WebDriverError(f"no session in the answer: {value!r}"[:300])
        self.session = value["sessionId"]

    def end(self) -> None:
        if self.session:
            try:
                self._call("DELETE", f"/session/{self.session}", timeout=120)
            finally:
                self.session = None

    def _s(self, path: str) -> str:
        if not self.session:
            raise WebDriverError("no WebDriver session")
        return f"/session/{self.session}{path}"

    def js(self, script: str, *arguments: object, timeout: float = 60.0) -> object:
        return self._call("POST", self._s("/execute/sync"),
                          {"script": script, "args": list(arguments)}, timeout=timeout)

    def find(self, css: str) -> str | None:
        found = self._call("POST", self._s("/elements"), {"using": "css selector", "value": css})
        return found[0][ELEMENT] if isinstance(found, list) and found else None

    def _when_ready(self, css: str, action: Callable[[str], None], wait: float) -> None:
        """Act on an element once it exists and can take input.

        The page redraws after each answer from the app, so an element can be missing, hidden
        or covered for a moment; WebDriver then refuses the action, and this tries again
        until `wait` seconds have passed.
        """

        deadline = time.time() + wait
        while True:
            element = self.find(css)
            try:
                if element is None:
                    raise StepError(f"no element matches {css}")
                # WebKitWebDriver does not scroll inside a scrolling panel (Settings is one), and
                # refuses an element outside the visible part, so scroll it into view first.
                self.js("arguments[0].scrollIntoView({block: 'center', inline: 'nearest'});"
                        " return true", {ELEMENT: element})
                action(element)
                return
            except (StepError, WebDriverError) as error:
                retry = isinstance(error, StepError) or any(
                    reason in str(error) for reason in RETRIED_REFUSALS)
                if not retry or time.time() >= deadline:
                    raise
            time.sleep(0.5)

    def click(self, css: str, wait: float = 60.0) -> None:
        self._when_ready(css, lambda element: self._call(
            "POST", self._s(f"/element/{element}/click"), {}), wait)

    def type(self, css: str, text: str, wait: float = 60.0) -> None:
        def enter(element: str) -> None:
            if self.js("return arguments[0].value", {ELEMENT: element}):
                self._call("POST", self._s(f"/element/{element}/clear"), {})
            self._call("POST", self._s(f"/element/{element}/value"), {"text": text})

        self._when_ready(css, enter, wait)


# Page helpers, written in JavaScript and run in the app's web view.

JS_VISIBLE = """
const node = document.querySelector(arguments[0]);
if (!node) return false;
const style = getComputedStyle(node);
return !node.closest('[hidden]') && style.display !== 'none' && style.visibility !== 'hidden'
  && node.getClientRects().length > 0;
"""
JS_TEXT = """
const node = document.querySelector(arguments[0]);
return node ? (node.innerText || node.textContent || '').trim() : null;
"""
# Marks the first element matching a selector whose text contains a phrase, so WebDriver can
# then click it like any element found by id. Options are not laid out, so for them the
# visibility test is left out.
JS_MARK_BY_TEXT = """
const [selector, phrase, mark, mustShow] = arguments;
for (const old of document.querySelectorAll('[data-installed-check="' + mark + '"]')) {
  old.removeAttribute('data-installed-check');
}
for (const node of document.querySelectorAll(selector)) {
  const text = (node.innerText || node.textContent || '').replace(/\\s+/g, ' ').trim();
  if (!text.includes(phrase)) continue;
  if (mustShow && (node.closest('[hidden]') || node.getClientRects().length === 0)) continue;
  node.setAttribute('data-installed-check', mark);
  return text;
}
return null;
"""


# ---------------------------------------------------------------------------------------------
# One run


class Check:
    def __init__(self, options: argparse.Namespace):
        self.options = options
        self.output: Path = options.output
        self.shots = self.output / "screenshots"
        self.logs = self.output / "logs"
        self.work = Path(tempfile.mkdtemp(prefix="sylvia-installed-check-", dir=options.work_dir))
        self.marker = secrets.token_hex(8)
        # A fresh random key for each run: it shows the saved key reached the stand-in, and
        # the check can look for it in saved files without ever printing it.
        self.key = "standin-" + secrets.token_hex(12)
        self.display = f":{options.display}"
        self.driver_port = options.port
        self.native_port = options.port + 1
        self.standin_port = options.port + 2
        self.processes = Processes(self.logs, self.marker)
        import pwd  # POSIX only; imported here so the guards can be tested anywhere

        self.real_home = pwd.getpwuid(os.getuid()).pw_dir
        self.base_env = dict(os.environ)
        self.dbus_address = ""
        self.webdriver = WebDriver(self.driver_port)
        self.driver_process: subprocess.Popen | None = None
        self.application = ""  # the unpacked AppImage's AppRun, for WebDriver sessions
        self.appimage_copy: Path | None = None
        self.started = time.time()
        self.campaign_name = ""
        self.export_path: Path | None = None
        self.first_copy: subprocess.Popen | None = None
        self.steps = {step.number: step for step in step_plan()}

    # -- folders and environments

    def profile(self, name: str) -> tuple[Path, Path]:
        home = self.work / "homes" / name
        runtime = self.work / "runtime" / name
        home.mkdir(parents=True, exist_ok=True)
        runtime.mkdir(parents=True, exist_ok=True)
        runtime.chmod(0o700)
        return home, runtime

    def home(self, profile: str) -> Path:
        return self.profile(profile)[0]

    def env_for(self, profile: str, dbus_address: str | None = None) -> dict[str, str]:
        home, runtime = self.profile(profile)
        env = child_environment(
            self.base_env, home=home, runtime=runtime, display=self.display,
            dbus_address=dbus_address or self.dbus_address or "disabled:", marker=self.marker,
            # tauri-driver is usually installed with cargo.
            extra_path=[str(Path(self.real_home) / ".cargo/bin")],
        )
        guard_environment(env, real_home=self.real_home)
        return env

    # -- the desktop

    def screenshot(self, step: Step, name: str) -> None:
        target = self.shots / f"{step.number:02d}-{name}.png"
        result = run(["import", "-display", self.display, "-window", "root", str(target)],
                     self.env_for("tools"), timeout=60)
        if result.returncode == 0:
            step.screenshots.append(f"screenshots/{target.name}")

    def windows(self) -> list[tuple[str, str]]:
        """(window id, title) for every window the window manager knows."""

        result = run(["wmctrl", "-l"], self.env_for("tools"), timeout=20)
        found = []
        for line in result.stdout.splitlines():
            parts = line.split(None, 3)
            if len(parts) >= 3:
                found.append((parts[0], parts[3] if len(parts) == 4 else ""))
        return found

    def main_windows(self) -> list[str]:
        return [window for window, title in self.windows() if title == WINDOW_TITLE]

    def wait_window(self, title: str, timeout: float, exact: bool = True) -> str:
        """Wait for a window by title and give it the keyboard focus; return its id."""

        def found() -> str | None:
            for window, name in self.windows():
                if name == title or (not exact and title in name):
                    return window
            return None

        window = wait_for(found, timeout, f"no window titled {title!r} appeared")
        self.xdotool("windowactivate", "--sync", window)
        time.sleep(0.5)
        return window

    def xdotool(self, *arguments: str) -> subprocess.CompletedProcess:
        return run(["xdotool", *arguments], self.env_for("tools"), timeout=60)

    def keys(self, *names: str) -> None:
        for name in names:
            self.xdotool("key", "--clearmodifiers", name)
            time.sleep(0.3)

    def type_keys(self, text: str) -> None:
        self.xdotool("type", "--delay", "40", "--", text)

    # -- set-up and tear-down

    def preflight(self) -> None:
        path = self.env_for("tools")["PATH"]
        missing = missing_programs(path)
        if missing:
            raise GuardError(f"missing programs: {', '.join(missing)} "
                             "(see the checklist, section 5)")
        number = self.options.display
        if number < LOWEST_DISPLAY:
            raise GuardError(f"use display :{LOWEST_DISPLAY} or higher")
        if not display_is_free(number):
            raise GuardError(f"display :{number} is in use")
        if self.options.port < LOWEST_PORT:
            raise GuardError(f"use ports {LOWEST_PORT} or higher")
        for port in (self.driver_port, self.native_port, self.standin_port):
            if not port_is_free(port):
                raise GuardError(f"port {port} is in use")
        if not os.access(self.options.appimage, os.R_OK):
            raise GuardError(f"cannot read the AppImage {self.options.appimage}")

    def start_desktop(self) -> None:
        self.processes.start("xvfb", ["Xvfb", self.display, "-screen", "0", "1600x1000x24",
                                      "-nolisten", "tcp"], self.env_for("tools"))
        wait_for(lambda: not display_is_free(self.options.display), 30,
                 "the hidden display did not start")
        time.sleep(1)
        self.dbus_address = self.start_dbus("bus")
        env = self.env_for("tools")
        self.processes.start("openbox", ["openbox"], env)
        self.processes.start("standin", [sys.executable, str(STANDIN), "--port",
                                         str(self.standin_port), "--key", self.key, "--log",
                                         str(self.output / "standin-requests.jsonl")], env)
        wait_for(lambda: not port_is_free(self.standin_port), 30, "the stand-in did not start")

    def start_dbus(self, name: str) -> str:
        socket_path = self.work / f"{name}.socket"
        address = f"unix:path={socket_path}"
        self.processes.start(f"dbus-{name}", ["dbus-daemon", "--session", "--nofork",
                                              f"--address={address}"], self.env_for("tools"))
        wait_for(socket_path.exists, 20, "the D-Bus session did not start")
        return address

    def unpack(self) -> None:
        target = self.work / "app"
        target.mkdir()
        copy = target / "sylvia-gm.AppImage"
        shutil.copyfile(self.options.appimage, copy)
        copy.chmod(0o755)
        result = subprocess.run([str(copy), "--appimage-extract"], cwd=target, capture_output=True,
                                env=self.env_for("tools"), timeout=900, check=False)
        app_run = target / "squashfs-root" / "AppRun"
        if result.returncode != 0 or not app_run.exists():
            raise GuardError("could not unpack the AppImage")
        self.application = str(app_run)
        self.appimage_copy = copy

    def tear_down(self) -> list[str]:
        with contextlib.suppress(WebDriverError):
            self.webdriver.end()
        return self.processes.stop_all()

    # -- the app under WebDriver

    def open_app(self, profile: str, step: Step) -> None:
        """Start the app for a profile under WebDriver and wait until Home is shown."""

        # The app inherits tauri-driver's environment, so each profile has its own driver.
        if self.driver_process is not None:
            self.close_app()
        self.driver_process = self.processes.start(
            f"tauri-driver-{profile}",
            ["tauri-driver", "--port", str(self.driver_port), "--native-port",
             str(self.native_port)], self.env_for(profile))
        wait_for(self.webdriver.ready, 30, "tauri-driver did not start")
        started = time.time()
        self.webdriver.start(self.application, timeout=APP_START_SECONDS)
        wait_for(lambda: self.visible("#home-view") or None, APP_START_SECONDS,
                 "Home did not appear")
        step.facts.setdefault("window_and_home_seconds", round(time.time() - started, 1))
        if self.visible("#tutorial-welcome-explore"):
            self.webdriver.click("#tutorial-welcome-explore")
            wait_for(lambda: not self.visible("#tutorial-welcome-explore"), 20,
                     "the welcome dialog did not close")

    def close_app(self) -> dict[str, object]:
        """End the WebDriver session and make sure the app it started has gone."""

        facts: dict[str, object] = {}
        try:
            self.webdriver.end()
        except WebDriverError as error:
            facts["session_end_error"] = str(error)
        try:
            wait_for(lambda: not self.app_processes(), 60, "")
        except StepError:
            facts["app_processes_killed"] = sweep_marked_processes(self.marker)
        if self.driver_process is not None:
            self.processes.stop(self.driver_process)
            self.driver_process = None
        # WebKitWebDriver can outlive tauri-driver; nothing of this session may remain.
        for pid, name in marked_processes(self.marker):
            if name.startswith(("WebKitWebDriver", "WebKit", "sylvia-gm")):
                with contextlib.suppress(OSError):
                    os.kill(pid, signal.SIGKILL)
        return facts

    def app_processes(self) -> list[tuple[int, str]]:
        return [(pid, name) for pid, name in marked_processes(self.marker)
                if name.startswith("sylvia-gm")]

    def visible(self, css: str) -> bool:
        return bool(self.webdriver.js(JS_VISIBLE, css))

    def text(self, css: str) -> str:
        value = self.webdriver.js(JS_TEXT, css)
        return value if isinstance(value, str) else ""

    def click_text(self, selector: str, phrase: str, must_show: bool = True) -> str:
        mark = "target"
        found = self.webdriver.js(JS_MARK_BY_TEXT, selector, phrase, mark, must_show)
        if not found:
            raise StepError(f"no {selector} showing {phrase!r}")
        self.webdriver.click(f'[data-installed-check="{mark}"]')
        return str(found)

    def wait_text(self, css: str, phrase: str, timeout: float,
                  failure: Callable[[], str | None] | None = None) -> str:
        """Wait until an element's text contains a phrase; stop early on a visible failure."""

        def check() -> str | None:
            if failure is not None:
                problem = failure()
                if problem:
                    raise StepError(problem)
            value = self.text(css)
            return value if phrase in value else None

        return wait_for(check, timeout, f"{css} never showed {phrase!r}")

    def turn_failure(self) -> str | None:
        status = self.text("#session-status-text")
        if "provider_error" in status or "rejected" in status.lower():
            return f"the app reported: {status}"
        return None

    def go_home(self) -> None:
        if self.visible("#close-settings-btn"):
            self.webdriver.click("#close-settings-btn")
        self.webdriver.click("#home-btn")
        wait_for(lambda: self.visible("#home-view") or None, 30, "Home did not open")

    def open_settings(self, tab: str) -> None:
        if not self.visible("#close-settings-btn"):
            self.webdriver.click("#rail-settings-btn")
            wait_for(lambda: self.visible("#close-settings-btn") or None, 30,
                     "Settings did not open")
        self.webdriver.click(f"#settings-tab-{tab}")
        wait_for(lambda: self.visible(f"#settings-panel-{tab}") or None, 30,
                 f"the {tab} settings page did not open")

    def standin_requests(self) -> list[dict[str, object]]:
        path = self.output / "standin-requests.jsonl"
        if not path.exists():
            return []
        rows = []
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return rows

    def recover_ui(self) -> None:
        """After a failed step, close native dialogs and overlays so later steps can run."""

        for window, title in self.windows():
            if title != WINDOW_TITLE:
                self.xdotool("windowactivate", "--sync", window)
                self.keys("Escape")
        try:
            if self.webdriver.session:
                self.webdriver.js("for (const d of document.querySelectorAll('dialog[open]'))"
                                  " d.close(); return true")
                if self.visible("#close-settings-btn"):
                    self.webdriver.click("#close-settings-btn")
        except (WebDriverError, StepError):
            pass

    # -- the steps

    def step_first_start(self, step: Step) -> None:
        self.open_app("a", step)
        data = self.home("a") / DATA_FOLDER
        if not data.is_dir():
            raise StepError("the data folder was not created")
        mode = oct(data.stat().st_mode & 0o777)
        step.facts["data_folder"] = f"~/{DATA_FOLDER}"
        step.facts["data_folder_mode"] = mode
        step.facts["windows"] = [title for _window, title in self.windows()]
        self.screenshot(step, "home")
        if mode != "0o700":
            raise StepError(f"the data folder has mode {mode}, not 0o700")
        if len(self.main_windows()) != 1:
            raise StepError("expected exactly one Sylvia GM window")

    def step_provider(self, step: Step) -> None:
        wd = self.webdriver
        self.open_settings("models")
        wd.click("#add-model-btn")
        wd.click("[data-model-route=api]")
        wait_for(lambda: self.webdriver.js(
            "return !document.querySelector('#provider-preset').disabled") or None, 60,
            "the service list did not load")
        wd.click("#provider-preset option[value=custom]")
        wd.type("#provider-endpoint", f"http://127.0.0.1:{self.standin_port}/v1")
        # The key goes in through real key presses (see the module notes) and is never printed.
        wd.click("#provider-credential")
        self.keys("ctrl+a", "BackSpace")
        self.type_keys(self.key)
        wait_for(lambda: wd.js("return document.querySelector('#provider-credential').value"
                               " === arguments[0]", self.key) or None, 60,
                 "the API key field did not receive the key")
        self.screenshot(step, "connection")
        wd.click("#provider-continue-btn")
        wait_for(lambda: wd.js("return document.querySelectorAll("
                               "'#provider-discovered-models option').length") or None,
                 OWNER_SECONDS, "no model was found",)
        step.facts["models_found"] = self.text("#provider-settings-status")
        wd.click("#provider-discovered-models option[value='0']")
        wd.click("#provider-model-advanced > summary")
        wd.type("#provider-context-window", CONTEXT_CAP)
        wd.type("#provider-output-limit", REPLY_CAP)
        step.facts["context"] = self.text("#provider-context-summary")
        self.screenshot(step, "model")
        wd.click("#save-provider-btn")
        wait_for(lambda: self.visible("#set-added-model-default-btn") or None, OWNER_SECONDS,
                 "the model was not saved")
        wd.click("#set-added-model-default-btn")
        wait_for(lambda: not self.visible("#set-added-model-default-btn"), OWNER_SECONDS,
                 "the model was not made the default")
        step.facts["default_model"] = self.text("#model-status")
        step.facts["saved_models"] = self.text("#configured-provider-list")
        # The storage explanation sits in a closed disclosure; open it for the screenshot.
        wd.js("const d = document.querySelector('[data-platform-copy=key-protection]')"
              ".closest('details'); if (d) d.open = true; return true")
        wd.js("document.querySelector('[data-platform-copy=key-protection]')"
              ".scrollIntoView({block: 'center'}); return true")
        storage = self.text("[data-platform-copy=key-protection]") + " " + self.text(
            "[data-platform-copy=key-protection-limits]")
        step.facts["storage_explanation"] = storage
        self.screenshot(step, "saved-default")
        problems = []
        home = self.home("a")
        config = home / DATA_FOLDER / "config.yaml"
        key_file = home / KEY_FILE
        for label, path in (("config.yaml", config), ("credential-key", key_file)):
            if not path.is_file():
                problems.append(f"{label} is missing")
                continue
            mode = oct(path.stat().st_mode & 0o777)
            step.facts[f"{label}_mode"] = mode
            if mode != "0o600":
                problems.append(f"{label} has mode {mode}, not 0o600")
        holders = files_containing(home, self.key.encode())
        step.facts["files_with_plain_key"] = [str(path.relative_to(home)) for path in holders]
        if holders:
            problems.append("a saved file contains the API key in plain text")
        requests = self.standin_requests()
        matched = [row for row in requests if row.get("path", "").endswith("/models")
                   and row.get("key_matched")]
        step.facts["standin_model_lists_with_right_key"] = len(matched)
        if not matched:
            problems.append("the stand-in never received the right key")
        if "Linux" not in storage or "Windows" in storage:
            problems.append("the storage explanation does not name Linux, or names Windows")
        if "standin-narrator" not in step.facts["default_model"]:
            problems.append("the stand-in model is not the default")
        if problems:
            raise StepError("; ".join(problems))

    def step_prepared_campaign(self, step: Step) -> None:
        wd = self.webdriver
        self.go_home()
        wd.click("#home-ready-campaign-btn")
        wait_for(lambda: (not wd.js("return document.querySelector('#ready-campaign-start')"
                                    ".disabled") and "prepared" in self.text(
            "#ready-campaign-dialog")) or None, OWNER_SECONDS,
                 "the prepared adventures did not load")
        self.campaign_name = str(wd.js("return document.querySelector('#ready-campaign-name')"
                                       ".value"))
        step.facts["campaign"] = self.campaign_name
        self.screenshot(step, "ready-campaigns")
        wd.click("#ready-campaign-start")
        wait_for(lambda: self.visible("#chat-start-confirm") or None, OWNER_SECONDS,
                 "the Start Game Master dialog did not open")
        wd.click("#chat-start-confirm")

        def opened() -> str | None:
            # An unrated model needs one explicit confirmation; answer it once.
            if (not step.facts.get("unrated_model_confirmed")
                    and self.visible("#ready-campaign-start")
                    and "Continue with this model" in self.text("#ready-campaign-start")):
                step.facts["unrated_model_confirmed"] = True
                self.screenshot(step, "unrated-model")
                wd.click("#ready-campaign-start")
                return None
            return "open" if not self.visible("#home-view") and self.text(
                "#session-title") == self.campaign_name else None

        wait_for(opened, OWNER_SECONDS, "the Play view did not open")
        text = self.wait_text("#chronology", STANDIN_MARK, OWNER_SECONDS, self.turn_failure)
        step.facts["opening_text"] = text[text.index(STANDIN_MARK):][:200]
        self.screenshot(step, "opening")
        openings = [row for row in self.standin_requests()
                    if row.get("path", "").endswith("/chat/completions") and row.get("key_matched")]
        if not openings:
            raise StepError("the stand-in received no chat request")
        step.facts["opening_request"] = {name: openings[0].get(name)
                                         for name in ("tools", "bytes", "reply")}

    def step_player_turn(self, step: Step) -> None:
        wd = self.webdriver
        # The opening may already show the turn-reply text, so wait for one more of it.
        before = self.text("#chronology").count(TURN_REPLY_MARK)
        requests_before = len(self.standin_requests())
        wd.type("#player-input", PLAYER_MESSAGE)
        wd.click("#send-btn")

        def replied() -> bool:
            problem = self.turn_failure()
            if problem:
                raise StepError(problem)
            return self.text("#chronology").count(TURN_REPLY_MARK) > before

        wait_for(replied, OWNER_SECONDS, "no turn reply appeared")
        wait_for(lambda: not wd.js("return document.querySelector('#player-input').disabled")
                 or None, OWNER_SECONDS, "the message box did not become ready again")
        self.screenshot(step, "turn-reply")
        with_tools = [row for row in self.standin_requests()[requests_before:]
                      if row.get("key_matched") and int(row.get("tools") or 0) > 0]
        if not with_tools:
            raise StepError("no request with tools reached the stand-in")
        last = with_tools[-1]
        step.facts["tools_offered"] = last.get("tools")
        step.facts["request_bytes"] = last.get("bytes")
        step.facts["standin_reply"] = last.get("reply")

    def step_character_chat(self, step: Step) -> None:
        wd = self.webdriver
        card = self.work / "installed-check-card.json"
        card.write_text(json.dumps(made_up_card()), encoding="utf-8")
        self.open_settings("packages")
        wd.click("#import-character-card-btn")
        self.wait_window("Open File", 60)
        self.keys("ctrl+l")
        self.type_keys(str(card))
        self.screenshot(step, "open-dialog")
        self.keys("Return")
        wait_for(lambda: self.visible("#confirm-character-card-btn") or None, OWNER_SECONDS,
                 "the card preview did not appear")
        step.facts["card_preview"] = self.text("#character-card-preview")
        wd.click("#confirm-character-card-btn")
        self.wait_text("#package-install-status", "Saved", OWNER_SECONDS)
        self.go_home()
        wd.click("#home-character-chat-btn")
        wait_for(lambda: wd.js("return document.querySelectorAll('#character-chat-card option')"
                               ".length") or None, OWNER_SECONDS,
                 "the character list did not load")
        self.click_text("#character-chat-card option", CARD_NAME, must_show=False)
        wait_for(lambda: not wd.js("return document.querySelector('#character-chat-start')"
                                   ".disabled") or None, 60, "Start chat stayed disabled")
        wd.click("#character-chat-start")
        wait_for(lambda: not wd.js("return document.querySelector('#character-chat-input')"
                                   ".disabled") or None, OWNER_SECONDS, "the chat did not open")
        wd.type("#character-chat-input", CHAT_MESSAGE)
        wd.click("#character-chat-send")

        def replied() -> str | None:
            status = self.text("#character-chat-status")
            if wd.js("return document.querySelector('#character-chat-status').dataset.kind"
                     " === 'error'"):
                raise StepError(f"character chat reported: {status}")
            log = self.text("#character-chat-log")
            return log if NARRATION_MARK in log else None

        log = wait_for(replied, OWNER_SECONDS, "no character reply appeared")
        step.facts["chat_log"] = log
        self.screenshot(step, "reply")
        wd.click("#character-chat-close")

    def step_export(self, step: Step) -> None:
        wd = self.webdriver
        home = self.home("a")
        target = home / EXPORT_NAME
        # Export works on the campaign open in Play, so open it from Home first.
        self.go_home()
        self.click_text("#campaign-shelves .campaign-shelf-item", self.campaign_name)
        wait_for(lambda: (not self.visible("#home-view")
                          and self.text("#session-title") == self.campaign_name) or None,
                 OWNER_SECONDS, "the campaign did not open")
        self.open_settings("campaign-data")
        wd.click("#export-campaign-btn")
        self.wait_window("Export campaign", OWNER_SECONDS)
        self.screenshot(step, "confirmation")
        # Cancel has the focus; Tab moves it to OK.
        self.keys("Tab", "Return")
        self.wait_window("Save File", 120)
        self.screenshot(step, "save-dialog")
        # A bare file name saves in the dialog's starting folder, which must be the home folder.
        self.keys("ctrl+a")
        self.type_keys(EXPORT_NAME)
        self.keys("Return")
        status = self.wait_text("#campaign-data-status", "Exported", OWNER_SECONDS * 2)
        step.facts["status"] = status
        self.screenshot(step, "exported")
        if not target.is_file():
            elsewhere = [str(path) for path in self.work.rglob(EXPORT_NAME)]
            raise StepError(f"the export is not in the home folder (found: {elsewhere})")
        size = target.stat().st_size
        step.facts["file"] = f"~/{EXPORT_NAME}"
        step.facts["bytes"] = size
        step.facts["mode"] = oct(target.stat().st_mode & 0o777)
        step.facts["sha256"] = sha256_of(target)
        if size == 0:
            raise StepError("the exported file is empty")
        self.export_path = target

    def step_linux_notes(self, step: Step) -> None:
        wd = self.webdriver
        self.open_settings("dictation")
        dictation = self.text("#settings-panel-dictation")
        step.facts["dictation_page"] = dictation
        self.screenshot(step, "dictation")
        self.open_settings("about")
        about = self.text("#settings-panel-about")
        step.facts["update_status"] = self.text("[data-update-role=status]")
        step.facts["unsigned_note"] = self.text("[data-platform-copy=unsigned-download]")
        self.screenshot(step, "about")
        self.open_settings("accounts")
        step.facts["chatgpt_page_status"] = self.text("#settings-panel-accounts .model-status")
        wd.click("#settings-panel-accounts [data-account-view=claude]")
        wait_for(lambda: self.visible("#account-view-claude") or None, 30,
                 "the Claude page did not open")
        before = self.text("#claude-connection-status")
        # Only the status check: never install Claude Code or sign in.
        wd.click("#account-view-claude [data-claude-action=status]")
        status = wait_for(lambda: (lambda value: value if value != before and not value.endswith(
            "…") else None)(self.text("#claude-connection-status")), 120,
            "Check Claude Code gave no answer")
        step.facts["claude_status"] = status
        detail = self.text("#claude-connection-detail")
        if detail:
            step.facts["claude_detail"] = detail
        step.facts["claude_visible_buttons"] = wd.js(
            "return Array.from(document.querySelectorAll('#account-view-claude button'))"
            ".filter(b => b.getClientRects().length).map(b => b.textContent.trim())")
        self.screenshot(step, "claude")
        self.go_home()
        problems = []
        if not re.search(r"not available in the Linux app|not available on Linux", dictation):
            problems.append("the Dictation page does not say dictation is not available on Linux")
        if "Windows" in dictation or "Windows" in about:
            problems.append("the Dictation or About page names Windows")
        if "not supported" not in step.facts["update_status"]:
            problems.append("the About page does not say in-app updates are not supported")
        # The Claude page wording is being changed for Linux; it is recorded, not judged.
        if problems:
            raise StepError("; ".join(problems))

    def finish_session_a(self) -> None:
        facts = self.close_app()
        if facts:
            self.steps[12].facts.setdefault("session_end", facts)

    def start_direct(self, name: str, profile: str, dbus_address: str | None = None
                     ) -> subprocess.Popen:
        assert self.appimage_copy is not None
        return self.processes.start(name, [str(self.appimage_copy)],
                                    self.env_for(profile, dbus_address), cwd=self.work)

    def step_second_start(self, step: Step) -> None:
        started = time.time()
        self.first_copy = self.start_direct("app-first", "a")
        wait_for(lambda: self.main_windows() or None, APP_START_SECONDS,
                 "the AppImage window did not appear")
        step.facts["window_seconds"] = round(time.time() - started, 1)
        time.sleep(3)
        second = self.start_direct("app-second", "a")
        try:
            code = second.wait(180)
        except subprocess.TimeoutExpired:
            self.processes.stop(second)
            raise StepError("the second copy did not exit within 180 s") from None
        time.sleep(3)
        windows = self.main_windows()
        step.facts["second_copy_exit_code"] = code
        step.facts["main_windows"] = len(windows)
        step.facts["first_copy_running"] = self.first_copy.poll() is None
        self.screenshot(step, "one-window")
        if code != 0:
            raise StepError(f"the second copy exited with code {code}")
        if len(windows) != 1 or self.first_copy.poll() is not None:
            raise StepError("expected the first copy to keep exactly one window")

    def step_data_unavailable_box(self, step: Step) -> None:
        assert self.first_copy is not None
        if self.first_copy.poll() is not None:
            raise StepError("the first copy is no longer running")
        # A separate D-Bus session hides the running copy from the single-instance check, so
        # this copy reaches the data folder the first copy holds.
        other_bus = self.start_dbus("bus-other")
        before = {pid for pid, _name in marked_processes(self.marker)}
        started = time.time()
        third = self.start_direct("app-same-data", "a", other_bus)
        # Which app processes this copy started, and when: the owner is expected to start,
        # find the data folder taken and exit, and the shell then to show the box.
        appeared: dict[int, str] = {}

        def outcome() -> str | None:
            alive = dict(marked_processes(self.marker))
            for pid, name in alive.items():
                if pid not in before and pid not in appeared and name.startswith("sylvia-gm"):
                    appeared[pid] = f"{name} after {time.time() - started:.1f} s"
            step.facts["copy_processes"] = [
                f"{label}{'' if pid in alive else ', since exited'}"
                for pid, label in appeared.items()]
            if any(title == DATA_UNAVAILABLE_TITLE for _window, title in self.windows()):
                return "box"
            if len(self.main_windows()) > 1:
                return "second window"
            if third.poll() is not None:
                return "exited"
            return None

        try:
            seen = wait_for(outcome, BOX_SECONDS, "")
        except StepError:
            seen = "nothing"
        step.facts["outcome"] = seen
        step.facts["outcome_seconds"] = round(time.time() - started, 1)
        if seen != "box":
            step.facts["windows"] = [title for _window, title in self.windows()]
            step.facts["copy_exit_code"] = third.poll()
            # Which of this run's processes were alive, to show where the copy stopped.
            step.facts["processes"] = process_tree(self.marker)
            self.screenshot(step, "no-box")
            self.processes.stop(third)
            explanation = {
                "second window": "a second window opened on the same data folder",
                "exited": f"the copy exited with code {step.facts['copy_exit_code']} "
                          "without showing the box",
                "nothing": f"the copy showed no box and no window and was still running after "
                           f"{BOX_SECONDS} s (stopped by the check)",
            }[str(seen)]
            raise StepError(explanation)
        self.wait_window(DATA_UNAVAILABLE_TITLE, 10)
        step.facts["box_title"] = DATA_UNAVAILABLE_TITLE
        self.screenshot(step, "box")
        self.keys("Return")
        try:
            code = third.wait(120)
        except subprocess.TimeoutExpired:
            self.processes.stop(third)
            raise StepError("the copy did not exit after its box was answered") from None
        step.facts["copy_exit_code"] = code
        step.facts["first_copy_running"] = self.first_copy.poll() is None
        step.facts["main_windows"] = len(self.main_windows())
        if self.first_copy.poll() is not None or len(self.main_windows()) != 1:
            raise StepError("the running copy was disturbed")

    def step_close(self, step: Step) -> None:
        assert self.first_copy is not None
        windows = self.main_windows()
        if len(windows) != 1:
            raise StepError(f"expected one window to close, found {len(windows)}")
        run(["wmctrl", "-i", "-c", windows[0]], self.env_for("tools"))
        try:
            code = self.first_copy.wait(180)
        except subprocess.TimeoutExpired:
            raise StepError("the app did not exit within 180 s of closing its window") from None
        try:
            wait_for(lambda: not self.app_processes(), 30, "")
            left: list[str] = []
        except StepError:
            left = [f"{pid} {name}" for pid, name in self.app_processes()]
        step.facts["exit_code"] = code
        step.facts["processes_left"] = left
        self.screenshot(step, "closed")
        if code != 0:
            raise StepError(f"the app exited with code {code}")
        if left:
            raise StepError(f"processes were left behind: {left}")

    def step_restart(self, step: Step) -> None:
        self.open_app("a", step)
        shelves = self.wait_text("#campaign-shelves", self.campaign_name, OWNER_SECONDS)
        step.facts["campaigns_listed"] = shelves
        self.screenshot(step, "listed")
        step.facts.update(self.close_app())

    def step_restore(self, step: Step) -> None:
        assert self.export_path is not None
        self.open_app("b", step)
        if self.campaign_name in self.text("#campaign-shelves"):
            raise StepError("the new profile is not empty")
        self.open_settings("campaign-data")
        self.webdriver.click("#recover-campaign-btn")
        self.wait_window("Open File", 120, exact=False)
        self.keys("ctrl+l")
        self.type_keys(str(self.export_path))
        self.screenshot(step, "open-dialog")
        self.keys("Return")
        # The app recovers the file, then opens the recovered campaign by itself.
        status = wait_for(lambda: (lambda value: value if value and "…" not in value else None)(
            self.text("#campaign-data-status")), OWNER_SECONDS * 2, "the recovery gave no answer")
        step.facts["status"] = status
        self.screenshot(step, "recovered")
        if not status.startswith("Recovered"):
            raise StepError(f"the recovery reported: {status}")
        self.webdriver.click("#close-settings-btn")
        text = self.wait_text("#chronology", TURN_REPLY_MARK, OWNER_SECONDS)
        step.facts["opened_campaign"] = self.text("#session-title")
        step.facts["story_restored"] = TURN_REPLY_MARK in text
        self.screenshot(step, "opened")
        self.go_home()
        step.facts["campaigns_listed"] = self.wait_text("#campaign-shelves", self.campaign_name,
                                                        OWNER_SECONDS)
        self.screenshot(step, "listed")
        step.facts.update(self.close_app())

    # -- running the steps

    def run_step(self, number: int, action: Callable[[Step], None],
                 webdriver_step: bool = False) -> None:
        step = self.steps[number]
        for need in step.needs:
            if self.steps[need].status != PASSED:
                step.reason = (f"not run: needs step {need} ({self.steps[need].title}), "
                               "which did not pass")
                return
        if webdriver_step and not self.session_alive():
            step.reason = "not run: the app session under WebDriver had already ended"
            return
        started = time.time()
        print(f"step {number}: {step.title} ...", flush=True)
        try:
            action(step)
            step.status = PASSED
        except Exception as error:  # noqa: BLE001 - every failure is recorded, then the run goes on
            step.status = FAILED
            step.reason = f"{type(error).__name__}: {error}" if not isinstance(
                error, StepError) else str(error)
            with open(self.logs / "failures.log", "a", encoding="utf-8") as log:
                log.write(f"--- step {number}\n{traceback.format_exc()}\n")
            try:
                self.screenshot(step, "failure")
                self.recover_ui()
            except Exception:  # noqa: BLE001 - recovery is best effort
                pass
        finally:
            step.seconds = time.time() - started
            print(f"step {number}: {step.status} ({step.seconds:.0f} s) {step.reason}", flush=True)

    def session_alive(self) -> bool:
        try:
            return bool(self.webdriver.session) and self.webdriver.js("return 1") == 1
        except WebDriverError:
            return False

    def run_all(self) -> None:
        self.run_step(1, self.step_first_start)
        for number, action in ((2, self.step_provider), (3, self.step_prepared_campaign),
                               (4, self.step_player_turn), (5, self.step_character_chat),
                               (6, self.step_export), (12, self.step_linux_notes)):
            self.run_step(number, action, webdriver_step=True)
        self.finish_session_a()
        self.run_step(7, self.step_second_start)
        self.run_step(11, self.step_data_unavailable_box)
        self.run_step(8, self.step_close)
        if self.first_copy is not None and self.first_copy.poll() is None:
            self.processes.stop(self.first_copy)
        self.run_step(9, self.step_restart)
        if self.driver_process is not None:
            self.close_app()
        self.run_step(10, self.step_restore)
        if self.driver_process is not None:
            self.close_app()


def wait_for(probe: Callable[[], object], timeout: float, message: str,
             interval: float = 1.0) -> object:
    """Call probe until it returns something true; raise StepError after timeout seconds."""

    deadline = time.time() + timeout
    while True:
        value = probe()
        if value:
            return value
        if time.time() >= deadline:
            raise StepError(f"{message} (waited {timeout:.0f} s)")
        time.sleep(interval)


def files_containing(root: Path, needle: bytes) -> list[Path]:
    """Files under root that contain needle; used to show no saved file holds the key."""

    found = []
    for path in root.rglob("*"):
        try:
            if path.is_file() and not path.is_symlink() and needle in path.read_bytes():
                found.append(path)
        except OSError:
            continue
    return found


def made_up_card() -> dict[str, object]:
    """A minimal character card (format v2) for the character-chat step; no real asset."""

    return {"spec": "chara_card_v2", "spec_version": "2.0", "data": {
        "name": CARD_NAME, "description": "An innkeeper made up for an automated check.",
        "personality": "Calm.", "scenario": "A quiet inn.", "first_mes": "", "mes_example": "",
        "creator_notes": "", "system_prompt": "", "post_history_instructions": "",
        "alternate_greetings": [], "tags": [], "creator": "installed check",
        "character_version": "1", "extensions": {},
    }}


def parse_arguments(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--appimage", type=Path, required=True, help="the AppImage to check")
    parser.add_argument("--output", type=Path, required=True,
                        help="a new or empty folder for the report, screenshots and logs")
    parser.add_argument("--work-dir", default=None,
                        help="where the run's temporary homes and unpacked app go "
                             "(default: the system temporary folder; must be a Linux folder)")
    parser.add_argument("--display", type=int, default=LOWEST_DISPLAY,
                        help=f"hidden X display number (default {LOWEST_DISPLAY})")
    parser.add_argument("--port", type=int, default=LOWEST_PORT,
                        help=f"first of three local ports to use (default {LOWEST_PORT})")
    parser.add_argument("--keep-work", action="store_true",
                        help="keep the temporary homes for inspection instead of removing them")
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    options = parse_arguments(argv)
    options.appimage = options.appimage.resolve()
    output: Path = options.output.resolve()
    options.output = output
    if output.exists() and any(output.iterdir()):
        print(f"refused: {output} exists and is not empty", file=sys.stderr)
        return 2
    for sub in ("", "screenshots", "logs"):
        (output / sub).mkdir(parents=True, exist_ok=True)
    check = Check(options)
    context: dict[str, object] = {
        "appimage": str(options.appimage),
        "started": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "method": "WebDriver (tauri-driver and WebKitWebDriver) for the web view; xdotool keys "
                  "for native GTK dialogs and the API key; direct AppImage starts for steps 7, "
                  "8 and 11",
        "system": system_name(),
    }
    left: list[str] = []
    code = 2
    try:
        check.preflight()
        context["appimage_sha256"] = sha256_of(options.appimage)
        check.unpack()
        check.start_desktop()
        check.run_all()
    except GuardError as error:
        context["refused"] = str(error)
        print(f"refused: {error}", file=sys.stderr)
    finally:
        left = check.tear_down()
        context["duration_seconds"] = round(time.time() - check.started)
        context["processes_killed_at_end"] = left
        context["standin_requests"] = summarise_requests(check.standin_requests())
        if options.keep_work:
            context["work_folder"] = str(check.work)
        else:
            shutil.rmtree(check.work, ignore_errors=True)
        report = build_report(list(check.steps.values()), context)
        (output / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False)
                                            + "\n", encoding="utf-8")
        (output / "report.md").write_text(render_markdown(report), encoding="utf-8")
        if "refused" not in context:
            code = exit_code(report["summary"])
        counts = report["summary"]["counts"]
        print(f"{counts[PASSED]} passed, {counts[FAILED]} failed, {counts[NOT_RUN]} not run; "
              f"report: {output / 'report.md'}", flush=True)
    return code


def summarise_requests(rows: Sequence[Mapping[str, object]]) -> dict[str, object]:
    """Counts only: the request log holds no content, and this keeps the report short."""

    return {
        "total": len(rows),
        "with_right_key": sum(1 for row in rows if row.get("key_matched")),
        "without_right_key": sum(1 for row in rows if not row.get("key_matched")),
        "chat_with_tools": sum(1 for row in rows if int(row.get("tools") or 0) > 0),
        "tool_call_replies": sum(1 for row in rows if row.get("reply") == "tool_call"),
    }


if __name__ == "__main__":
    raise SystemExit(main())
