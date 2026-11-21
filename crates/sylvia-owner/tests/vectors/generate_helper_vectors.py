"""Generate reference vectors for sylvia_owner::desktop_update's update helper.

Run with the snapshot's own Windows interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-owner/tests/vectors/generate_helper_vectors.py

It covers what the helper PR ports, on the same file trees as generate_preparation_vectors.py
(whose helpers and test-only signing key it reuses):

- `scenarios`: an installed copy (`installed.py`) through PreparedUpdate with a fake NSIS
  registration and installer, and the helper's `apply_and_restart`, `recover_and_restart` and
  `main` with fake probe, launch, pause, process handles and presentation;
- `registry`: `WindowsRegistration.read` against scratch current-user keys written with
  `reg import` (deleted afterwards), for each value shape `Reg2Py` treats differently;
- `receipts`, `barriers`, `commands`, `versions` and `failures`: the small pure parts.

Run-specific values are normalized as in the preparation vectors. This script is reference
tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PureWindowsPath

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import generate_preparation_vectors as prep  # noqa: E402

from sylvia_gm import __release_channel__, __version__  # noqa: E402
from sylvia_gm.application.desktop_update import helper, installed, preparation, service  # noqa: E402
from sylvia_gm.application.desktop_update.manifest import UpdateError, Version, canonical_bytes  # noqa: E402
from sylvia_gm.application.desktop_update.preparation import PreparedUpdate  # noqa: E402
from sylvia_gm.application.desktop_update.processes import OwnedProcesses, ProcessReceipt  # noqa: E402
from sylvia_gm.application.desktop_update.recovery import RecoveryOperation  # noqa: E402
from sylvia_gm.application.desktop_update.restart import RestartHandshake  # noqa: E402
from sylvia_gm.application.desktop_update.checkpoint import acquire_update_data_lock  # noqa: E402
from sylvia_gm.application.desktop_update.storage import UpdateStore  # noqa: E402
from sylvia_gm.domain.errors import OwnerConflict  # noqa: E402

OUTPUT = HERE / "helper_vectors.json"
OP, ATTEMPT, NEW, TARGET = prep.OP, prep.ATTEMPT, prep.NEW, prep.TARGET
LIVE_NAME = "app with spaces"
IDENTITY = prep.IDENTITY
OLD_IDENTITY = {**IDENTITY, "version": __version__}
BLOBS = prep.BLOBS


def installed_files(version: str, *, extra=()) -> dict[str, bytes]:
    notices = ["LICENSE", "COPYRIGHT", "LICENSING-STATUS.md", "SYLVIA-ADDITIONAL-PERMISSIONS.md", "SRD-5.2.1.md",
               "Severed-Lands.md", "dependency-notices.txt"]
    names = ["sylvia-gm.exe", "sylvia-gm-owner.exe", *(f"licenses/{name}" for name in notices), *extra]
    return {name: (b"MZ " if name.endswith(".exe") else b"") + f"{version} {name}\n".encode() for name in names}


def installed_envelope(version: str, files: dict[str, bytes], installer: bytes) -> bytes:
    signed = {
        "format_version": 1,
        "version": version,
        "owner_version": version,
        "minimum_app_version": "0.4.0",
        "channel": __release_channel__,
        "target": TARGET,
        "distribution": "installed",
        "notes": "Helper vectors",
        "artifact": {
            "url": f"https://github.com/pneuma-io/sylvia-gm/releases/download/v{version}/sylvia-gm-{version}-setup.exe",
            "size": len(installer),
            "sha256": hashlib.sha256(installer).hexdigest(),
        },
        "files": [{"path": name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()}
                  for name, raw in sorted(files.items())],
    }
    signature = base64.b64encode(prep.KEY.sign(canonical_bytes(signed))).decode()
    return canonical_bytes({"signed": signed, "key_id": prep.KEY_ID, "signature": signature})


OLD_FILES = installed_files(__version__, extra=("licenses/OLD-ONLY.md",))
NEW_FILES = installed_files(NEW, extra=("licenses/NEW-ONLY.md",))
OLD_INSTALLER = f"MZ inert {__version__} installer".encode()
NEW_INSTALLER = f"MZ inert {NEW} installer".encode()


def installed_tree(*, phase="verified"):
    tree = {f"{LIVE_NAME}/{name}": raw for name, raw in OLD_FILES.items()}
    tree[f"{LIVE_NAME}/manual.txt"] = b"unrelated user file\n"
    tree["data/authority.sqlite3"] = prep.DATABASE
    tree["backup-source.sqlite3"] = prep.DATABASE
    base = f"data/.application-updates/{OP}"
    tree[f"{base}/manifest.json"] = installed_envelope(NEW, NEW_FILES, NEW_INSTALLER)
    tree[f"{base}/artifact.verified"] = NEW_INSTALLER
    tree[f"{base}/previous-manifest.json"] = installed_envelope(__version__, OLD_FILES, OLD_INSTALLER)
    tree[f"{base}/previous-installer/artifact.verified"] = OLD_INSTALLER
    tree["data/.application-updates/current.json"] = canonical_bytes(
        {"format_version": 1, "operation_id": OP, "phase": phase, "version": NEW, "error_code": None})
    return tree


def registration_row(live: Path) -> dict[str, str]:
    return {
        "DisplayName": "Sylvia GM",
        "DisplayVersion": __version__,
        "Publisher": "pneuma-io",
        "MainBinaryName": "sylvia-gm.exe",
        "InstallLocation": '"' + str(live) + '"',
        "ProductLocation": str(live),
        "UninstallString": '"' + str(live / "uninstall.exe") + '"',
    }


def units(text: str) -> list[int]:
    raw = text.encode("utf-16-le", "surrogatepass")
    return [int.from_bytes(raw[i:i + 2], "little") for i in range(0, len(raw), 2)]


class Recorder:
    """The fakes of one scenario and what they saw."""

    def __init__(self, folder: Path, live: Path, data: Path, distribution: str, scenario):
        self.folder, self.live, self.data, self.distribution = folder, live, data, distribution
        self.events: list[str] = []
        self.registry = registration_row(live)
        self.installer_modes = list(scenario.get("installer", []))

    def relative(self, path) -> str:
        return Path(path).relative_to(self.folder).as_posix()

    def registration(self):
        recorder = self

        class Registration:
            def read(self):
                return dict(recorder.registry)

        return Registration()

    def run_installer(self, artifact, destination):
        self.events.append("installer:" + self.relative(artifact))
        mode = self.installer_modes.pop(0) if self.installer_modes else "ok"
        new = Path(artifact).parent.name != "previous-installer"
        files, version = (NEW_FILES, NEW) if new else (OLD_FILES, __version__)
        if mode == "partial":
            name = "sylvia-gm.exe"
            (Path(destination) / name).write_bytes(files[name])
            return 1
        if mode == "registry-only":
            self.registry["DisplayVersion"] = version
            return 0
        for name, raw in files.items():
            target = Path(destination) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(raw)
        self.registry["DisplayVersion"] = version
        return 0

    def hooks(self, op):
        barrier = list(op.get("barrier", []))
        calls = {"barrier": 0}
        recorder = self

        def exited():
            calls["barrier"] += 1
            return barrier.pop(0) if barrier else True

        def probe(executable):
            recorder.events.append("probe:" + recorder.relative(executable))
            return op.get("identity", IDENTITY)

        def launch(executable, arguments):
            recorder.events.append("launch:" + recorder.relative(executable) + ":" + arguments[0])
            mode = op.get("launch", "confirm")
            if mode == "fail":
                raise OSError("the app could not start")
            recovery = arguments[0] == "--sylvia-update-recovery-start"
            if mode == "confirm":
                version = __version__ if recovery else NEW
                RestartHandshake(recorder.data, OP, arguments[2], recovery=recovery).confirm(
                    nonce=arguments[3], live=recorder.live, desktop_version=version, owner_version=version,
                    target=TARGET, distribution=recorder.distribution, storage_compatibility=1)

            class Child:
                def poll(self):
                    return None if mode == "confirm" else 1

            return Child()

        def pause(_seconds):
            recorder.events.append("pause")

        def notify(phase):
            recorder.events.append("notify:" + phase)

        return exited, calls, {"probe": probe, "launch": launch, "pause": pause, "notify": notify}


def outcome(call):
    try:
        return {"ok": call()}
    except UpdateError as error:
        return {"error": [error.code, str(error)]}
    except Exception as error:  # noqa: BLE001 - recorded as Python's class
        return {"other": type(error).__name__}


def run_main(recorder: Recorder, op, prepared_kwargs):
    """`helper.main` with fake handles, presentation, probe, launch and pause."""
    table = {int(key): value for key, value in op.get("handles", {}).items()}
    events = recorder.events

    class Handles:
        def identity(self, handle):
            if handle not in table:
                raise UpdateError("process_identity_unavailable", "The owned process instance could not be verified.")
            return tuple(table[handle])

        def exited(self, handle):
            events.append(f"exited:{handle}")
            return True

        def close(self, handle):
            events.append(f"closed:{handle}")

    exited_unused, calls, hooks = recorder.hooks(op)
    saved = {
        "handles": helper.WindowsHandles,
        "keys": service.PUBLIC_KEYS,
        "apply": dict(helper._apply_and_restart_locked.__kwdefaults__),
        "recover": dict(helper._recover_and_restart_locked.__kwdefaults__),
        "registration": preparation.WindowsRegistration,
        "prepared": dict(PreparedUpdate.__init__.__kwdefaults__),
    }
    helper.WindowsHandles = Handles
    service.PUBLIC_KEYS = {} if op.get("trust") is False else {prep.KEY_ID: prep.KEY.public_key().public_bytes_raw()}
    for function in (helper._apply_and_restart_locked, helper._recover_and_restart_locked):
        function.__kwdefaults__.update(probe=hooks["probe"], launch=hooks["launch"], pause=hooks["pause"])
    preparation.WindowsRegistration = lambda: prepared_kwargs["registration"]
    PreparedUpdate.__init__.__kwdefaults__["run_installer"] = prepared_kwargs["run_installer"]
    output = io.StringIO()
    lines = list(op["lines"])

    class Input:
        def __init__(self):
            self.buffer = b""

        def readline(self, limit):
            if not self.buffer and lines:
                line = lines.pop(0)
                if line == "<commit>":
                    ready = json.loads(output.getvalue().splitlines()[-1])
                    line = {"action": "commit", "operation_id": OP, "attempt": ATTEMPT,
                            "helper_nonce": ready.get("helper_nonce")}
                self.buffer = (line if isinstance(line, str) else json.dumps(line) + "\n").encode()
            raw, self.buffer = self.buffer[:limit], self.buffer[limit:]
            if b"\n" in raw[:-1]:
                cut = raw.index(b"\n") + 1
                raw, self.buffer = raw[:cut], raw[cut:] + self.buffer
            return raw

    def factory():
        if op.get("presentation") == "unavailable":
            raise UpdateError("progress_unavailable", "The update status window could not open. Sylvia stays open.")

        class Presentation:
            def stage(self, phase):
                events.append("stage:" + phase)

            def fail(self, code):
                events.append("fail:" + code)

            def close(self):
                events.append("close")

        return Presentation()

    try:
        code = helper.main(Input(), output, presentation_factory=factory)
    finally:
        helper.WindowsHandles = saved["handles"]
        service.PUBLIC_KEYS = saved["keys"]
        helper._apply_and_restart_locked.__kwdefaults__.clear()
        helper._apply_and_restart_locked.__kwdefaults__.update(saved["apply"])
        helper._recover_and_restart_locked.__kwdefaults__.clear()
        helper._recover_and_restart_locked.__kwdefaults__.update(saved["recover"])
        preparation.WindowsRegistration = saved["registration"]
        PreparedUpdate.__init__.__kwdefaults__.clear()
        PreparedUpdate.__init__.__kwdefaults__.update(saved["prepared"])
    return {"code": code, "output": [json.loads(row) for row in output.getvalue().splitlines()],
            "barrier": calls["barrier"]}


def run(scenario):
    folder = Path(tempfile.mkdtemp(prefix="sylvia-helper-vectors-")).resolve()
    results = []
    distribution = scenario.get("distribution", "portable")
    try:
        for relative, digest in scenario["tree"].items():
            path = folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(BLOBS[digest]))
        if distribution == "portable":
            live = folder / "app"
            data = live / "isolated-data"
        else:
            live, data = folder / LIVE_NAME, folder / "data"
        recorder = Recorder(folder, live, data, distribution, scenario)
        prepared_kwargs = {"registration": recorder.registration(), "run_installer": recorder.run_installer}
        current = Version.parse(__version__)
        state = {"prepared": None, "lock": None, "handshake": None}

        def backup(target):
            shutil.copyfile(folder / "backup-source.sqlite3", target)

        def new_prepared(attempt=ATTEMPT):
            kwargs = prepared_kwargs if distribution == "installed" else {}
            return PreparedUpdate(data, live, OP, attempt, current=current, target=TARGET,
                                  distribution=distribution, verify=prep.RING.verify, **kwargs)

        for op in scenario["ops"]:
            name = op["op"]
            recorder.events = []
            extra = {}
            if name == "new":
                def make():
                    state["prepared"] = new_prepared()
                result = outcome(make)
            elif name == "prepare":
                result = outcome(lambda: state["prepared"].prepare(backup, lambda: True))
            elif name == "require_selected":
                result = outcome(lambda: state["prepared"].require_selected())
            elif name == "apply":
                exited = op.get("exited", True)
                result = outcome(lambda: state["prepared"].apply(lambda: exited))
            elif name == "create_restart":
                def create():
                    state["handshake"] = state["prepared"].create_restart(op["identity"])
                    return state["handshake"]._load()["nonce"]
                result = outcome(create)
            elif name == "confirm":
                result = outcome(lambda: state["prepared"].confirm())
            elif name == "save_phase":
                result = outcome(lambda: UpdateStore(data / ".application-updates").save(OP, op["phase"], NEW))
            elif name == "registry":
                recorder.registry[op["field"]] = op["value"].replace("${live}", str(live))
                result = {"ok": None}
            elif name == "write":
                path = folder / op["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(base64.b64decode(op["b64"]))
                result = {"ok": None}
            elif name == "delete":
                (folder / op["path"]).unlink()
                result = {"ok": None}
            elif name == "hold_lock":
                state["lock"] = acquire_update_data_lock(state["prepared"].stage / "helper-runtime")
                result = {"ok": None}
            elif name == "release_lock":
                state["lock"].close()
                result = {"ok": None}
            elif name == "recovery_rollback":
                exited = op.get("exited", True)
                result = outcome(lambda: RecoveryOperation(state["prepared"]).rollback(op["choice"], lambda: exited))
            elif name == "recovery_verify":
                result = outcome(lambda: state["prepared"].replacement().verify_rollback())
            elif name == "helper_apply":
                exited, calls, hooks = recorder.hooks(op)
                result = outcome(lambda: helper.apply_and_restart(state["prepared"], type("B", (), {
                    "all_exited": staticmethod(exited)})(), **hooks))
                extra = {"barrier": calls["barrier"]}
            elif name == "helper_recover":
                exited, calls, hooks = recorder.hooks(op)
                nonce = op.get("nonce")
                if nonce == "current":
                    nonce = RestartHandshake(data, OP, ATTEMPT, recovery=True)._load()["nonce"]
                result = outcome(lambda: helper.recover_and_restart(state["prepared"], type("B", (), {
                    "all_exited": staticmethod(exited)})(), op["choice"], restart_nonce=nonce, **hooks))
                extra = {"barrier": calls["barrier"]}
            elif name == "helper_main":
                request = op.get("request")
                lines = []
                for line in op["lines"]:
                    if line == "<request>":
                        line = {"type": request.get("type", "desktop_update_apply"), "operation_id": OP,
                                "attempt": ATTEMPT, "data_root": str(data), "live": str(live),
                                "distribution": distribution,
                                "processes": request.get("processes", [
                                    {"role": "desktop", "handle": 101, "pid": 10, "creation_time": 1},
                                    {"role": "owner", "handle": 102, "pid": 11, "creation_time": 2}])}
                        line.update({key: value for key, value in request.items() if key not in ("type", "processes")})
                    lines.append(line)
                result = run_main(recorder, {**op, "lines": lines}, prepared_kwargs)
            else:
                raise ValueError(name)
            results.append({"op": name, "result": result, "events": recorder.events, **extra})
        if state["lock"] is not None:
            state["lock"].close()
        files = prep.listing(folder)
    finally:
        shutil.rmtree(folder)
    return prep.record(results, files)


def encoded(tree):
    rows = {}
    for path, raw in sorted(tree.items()):
        digest = hashlib.sha256(raw).hexdigest()
        BLOBS[digest] = prep.b64(raw)
        rows[path] = digest
    return rows


HANDLES = {"101": [10, 1], "102": [11, 2]}
APPLY = {"type": "desktop_update_apply"}


def scenarios():
    armed = [{"op": "new"}, {"op": "prepare"}, {"op": "save_phase", "phase": "closing"}]
    # The installed copy.
    yield {"name": "installed: prepare, apply, restart, confirm", "distribution": "installed",
           "tree": encoded(installed_tree()),
           "ops": [{"op": "new"}, {"op": "prepare"}, {"op": "require_selected"}, {"op": "apply", "exited": False},
                   {"op": "apply"}, {"op": "apply"}, {"op": "create_restart", "identity": IDENTITY}]}
    yield {"name": "installed: helper applies and restarts once", "distribution": "installed",
           "tree": encoded(installed_tree()),
           "ops": [*armed, {"op": "helper_apply", "barrier": [False, False]}, {"op": "helper_apply"}]}
    yield {"name": "installed: explicit rollback restores the retained installer", "distribution": "installed",
           "tree": encoded(installed_tree()),
           "ops": [*armed, {"op": "helper_apply"}, {"op": "recovery_verify"},
                   {"op": "recovery_rollback", "choice": "Keep current data"},
                   {"op": "recovery_rollback", "choice": "Keep current data"}, {"op": "recovery_verify"}]}
    yield {"name": "installed: helper recovery keeps data and restarts the old app", "distribution": "installed",
           "tree": encoded(installed_tree()),
           "ops": [*armed, {"op": "helper_apply", "launch": "exit"},
                   {"op": "helper_recover", "choice": "Keep current data", "identity": OLD_IDENTITY},
                   {"op": "helper_recover", "choice": "Keep current data", "identity": OLD_IDENTITY}]}
    for field, value in [("Publisher", "another publisher"), ("InstallLocation", "C:/another-copy"),
                         ("ProductLocation", "C:/another-copy"), ("DisplayVersion", "0.4.1-99"),
                         ("UninstallString", "msiexec /x unrelated"), ("MainBinaryName", "another.exe"),
                         ("UninstallString", '"'), ("InstallLocation", '""'),
                         ("InstallLocation", '"${live}"'), ("UninstallString", '"${live}/uninstall.exe"')]:
        yield {"name": f"installed: registration {field} = {value}", "distribution": "installed",
               "tree": encoded(installed_tree()),
               "ops": [{"op": "new"}, {"op": "registry", "field": field, "value": value}, {"op": "prepare"}]}
    yield {"name": "installed: a failed partial installer can be retried", "distribution": "installed",
           "tree": encoded(installed_tree()), "installer": ["partial"],
           "ops": [{"op": "new"}, {"op": "prepare"}, {"op": "save_phase", "phase": "closing"}, {"op": "apply"},
                   {"op": "apply"}]}
    yield {"name": "installed: a zero exit with missing files is not success", "distribution": "installed",
           "tree": encoded(installed_tree()), "installer": ["registry-only"],
           "ops": [{"op": "new"}, {"op": "prepare"}, {"op": "apply"}]}
    base = f"data/.application-updates/{OP}"
    yield {"name": "installed: a tampered retained installer", "distribution": "installed",
           "tree": encoded({**installed_tree(), f"{base}/previous-installer/artifact.verified": b"tampered"}),
           "ops": [{"op": "new"}]}
    yield {"name": "installed: a manual repair is preserved", "distribution": "installed",
           "tree": encoded({**installed_tree(), f"{LIVE_NAME}/licenses/NEW-ONLY.md": b"hand made\n"}),
           "ops": [{"op": "new"}, {"op": "prepare"}]}
    yield {"name": "installed: a portable marker in the app", "distribution": "installed",
           "tree": encoded({**installed_tree(), f"{LIVE_NAME}/sylvia-portable.json": b'{"version":1}'}),
           "ops": [{"op": "new"}, {"op": "prepare"}]}
    # The helper on a portable copy.
    yield {"name": "portable: helper waits, applies, restarts once and reconciles", "tree": encoded(prep.base_tree()),
           "ops": [*armed, {"op": "helper_apply", "barrier": [False, False, False]}, {"op": "helper_apply"}]}
    yield {"name": "portable: a failed start keeps the checkpoint and never relaunches", "tree": encoded(prep.base_tree()),
           "ops": [*armed, {"op": "helper_apply", "launch": "exit"}, {"op": "helper_apply"}]}
    yield {"name": "portable: a launch that cannot start", "tree": encoded(prep.base_tree()),
           "ops": [*armed, {"op": "helper_apply", "launch": "fail"}]}
    yield {"name": "portable: a wrong owner identity prevents the launch", "tree": encoded(prep.base_tree()),
           "ops": [*armed, {"op": "helper_apply", "identity": {**IDENTITY, "version": "0.4.3"}}]}
    yield {"name": "portable: an unarmed helper changes nothing", "tree": encoded(prep.base_tree()),
           "ops": [{"op": "new"}, {"op": "prepare"}, {"op": "helper_apply"}]}
    yield {"name": "portable: a second helper cannot overtake", "tree": encoded(prep.base_tree()),
           "ops": [*armed, {"op": "hold_lock"}, {"op": "helper_apply"}, {"op": "release_lock"}]}
    yield {"name": "portable: recovery keeps data, restarts, then retries", "tree": encoded(prep.base_tree()),
           "ops": [*armed, {"op": "helper_apply", "launch": "exit"},
                   {"op": "helper_recover", "choice": "Keep current data", "nonce": "e" * 32},
                   {"op": "helper_recover", "choice": "Keep current data", "identity": OLD_IDENTITY, "launch": "exit"},
                   {"op": "helper_recover", "choice": "Keep current data", "identity": OLD_IDENTITY},
                   {"op": "helper_recover", "choice": "Restore pre-update data", "identity": OLD_IDENTITY,
                    "nonce": "current"},
                   {"op": "helper_recover", "choice": "Keep current data", "identity": OLD_IDENTITY,
                    "nonce": "current"},
                   {"op": "helper_recover", "choice": "Keep current data", "identity": OLD_IDENTITY}]}
    yield {"name": "portable: recovery restores pre-update data", "tree": encoded(prep.base_tree()),
           "ops": [*armed, {"op": "helper_apply", "launch": "exit"},
                   {"op": "write", "path": "app/isolated-data/authority.sqlite3", "b64": prep.b64(prep.NEWER_DATABASE)},
                   {"op": "helper_recover", "choice": "Keep current data", "identity": OLD_IDENTITY},
                   {"op": "helper_recover", "choice": "Restore pre-update data", "identity": OLD_IDENTITY}]}
    # The helper's own protocol.
    good = ["<request>", "<commit>"]
    yield {"name": "main: trust is not configured", "tree": encoded(prep.base_tree()),
           "ops": [*armed, {"op": "helper_main", "trust": False, "request": APPLY, "lines": good}]}
    yield {"name": "main: applies, restarts and reports nothing more", "tree": encoded(prep.base_tree()),
           "ops": [*armed, {"op": "helper_main", "handles": HANDLES, "request": APPLY, "lines": good}]}
    yield {"name": "main: installed copy", "distribution": "installed", "tree": encoded(installed_tree()),
           "ops": [*armed, {"op": "helper_main", "handles": HANDLES, "request": APPLY, "lines": good}]}
    yield {"name": "main: a failure after the commit is durable", "tree": encoded(prep.base_tree()),
           "ops": [*armed, {"op": "helper_main", "handles": HANDLES, "request": APPLY, "lines": good, "launch": "exit"}]}
    yield {"name": "main: recovery", "tree": encoded(prep.base_tree()),
           "ops": [*armed, {"op": "helper_apply", "launch": "exit"},
                   {"op": "helper_main", "handles": HANDLES, "identity": OLD_IDENTITY, "lines": good,
                    "request": {"type": "desktop_update_recover", "choice": "Keep current data", "restart_nonce": None}},
                   {"op": "helper_main", "handles": HANDLES, "identity": OLD_IDENTITY, "lines": good,
                    "request": {"type": "desktop_update_recover", "choice": "Keep current data",
                                "restart_nonce": "f" * 32}}]}
    yield {"name": "main: refusals before any native access", "tree": encoded(prep.base_tree()),
           "ops": [*armed,
                   {"op": "helper_main", "handles": HANDLES, "request": APPLY, "lines": ['{"type":"desktop_update_apply","url":"x"}\n']},
                   {"op": "helper_main", "handles": HANDLES, "request": APPLY, "lines": ["x" * 65537 + "\n"]},
                   {"op": "helper_main", "handles": HANDLES, "request": APPLY, "lines": ['{"type":"desktop_update_apply"}']},
                   {"op": "helper_main", "handles": HANDLES, "request": APPLY, "lines": []},
                   {"op": "helper_main", "handles": HANDLES, "request": {**APPLY, "live": "relative/app"}, "lines": good},
                   {"op": "helper_main", "handles": HANDLES, "request": {**APPLY, "distribution": "msi"}, "lines": good},
                   {"op": "helper_main", "handles": HANDLES, "lines": good,
                    "request": {"type": "desktop_update_recover", "choice": "keep", "restart_nonce": None}},
                   {"op": "helper_main", "handles": HANDLES, "lines": good,
                    "request": {"type": "desktop_update_recover", "choice": "Keep current data", "restart_nonce": "X"}},
                   {"op": "helper_main", "handles": HANDLES, "lines": good, "request": {**APPLY, "processes": [
                       {"role": "desktop", "handle": 101, "pid": 10, "creation_time": 1}]}},
                   {"op": "helper_main", "handles": HANDLES, "lines": good, "request": {**APPLY, "processes": [
                       {"role": "desktop", "handle": 101, "pid": 10, "creation_time": 1},
                       {"role": "owner", "handle": True, "pid": 11, "creation_time": 2}]}},
                   {"op": "helper_main", "handles": {"101": [10, 1], "102": [11, 3]}, "request": APPLY, "lines": good},
                   {"op": "helper_main", "handles": {"101": [10, 1]}, "request": APPLY, "lines": good},
                   {"op": "helper_main", "handles": HANDLES, "request": {**APPLY, "attempt": "b" * 32}, "lines": good},
                   {"op": "helper_main", "handles": HANDLES, "request": APPLY, "lines": good, "presentation": "unavailable"},
                   {"op": "helper_main", "handles": HANDLES, "request": APPLY,
                    "lines": ["<request>", {"action": "commit", "operation_id": OP, "attempt": ATTEMPT, "helper_nonce": "0" * 32}]},
                   {"op": "helper_main", "handles": HANDLES, "request": APPLY, "lines": ["<request>"]}]}
    yield {"name": "main: another helper holds the operation", "tree": encoded(prep.base_tree()),
           "ops": [*armed, {"op": "hold_lock"},
                   {"op": "helper_main", "handles": HANDLES, "request": APPLY, "lines": good}, {"op": "release_lock"}]}


# ---- the registry --------------------------------------------------------------------------

def sz(text: str, *, nul=True) -> list:
    return [1, (text + ("\0" if nul else "")).encode("utf-16-le", "surrogatepass").hex()]


def registry_cases():
    live = r"C:\Users\Example\AppData\Local\Sylvia GM"
    values = {
        "DisplayName": sz("Sylvia GM"), "DisplayVersion": sz(__version__), "Publisher": sz("pneuma-io"),
        "InstallLocation": sz('"' + live + '"'), "MainBinaryName": sz("sylvia-gm.exe"),
        "UninstallString": sz('"' + live + r'\uninstall.exe"'),
    }
    default = sz(live)

    def case(name, *, changes=None, drop=(), product=default, product_key=True):
        uninstall = {key: value for key, value in {**values, **(changes or {})}.items() if key not in drop}
        return {"name": name, "uninstall": uninstall, "product": product if product_key else None}

    yield case("ordinary")
    yield case("odd byte length", changes={"DisplayVersion": [1, sz(__version__)[1] + "41"]})
    yield case("no NUL", changes={"Publisher": sz("pneuma-io", nul=False)})
    yield case("embedded NUL", changes={"MainBinaryName": sz("sylvia-gm.exe\0junk")})
    yield case("too long", changes={"InstallLocation": sz("x" * 4097)})
    yield case("4096 characters with a surrogate pair", changes={"InstallLocation": sz("x" * 4095 + "\U0001F3B2")})
    yield case("a lone surrogate", changes={"DisplayName": sz("Sylvia \udc00GM")})
    yield case("an expandable string", changes={"Publisher": [2, sz("pneuma-io")[1]]})
    yield case("a number", changes={"Publisher": [4, "01000000"]})
    yield case("a missing value", drop=("UninstallString",))
    yield case("an empty default value", product=[1, ""])
    yield case("no product key", product_key=False)


def reg_file(key: str, case) -> str:
    def entry(name, value):
        kind, data = value
        label = "@" if name == "" else json.dumps(name)
        hex_bytes = ",".join(data[i:i + 2] for i in range(0, len(data), 2))
        return f"{label}=hex({kind:x}):{hex_bytes}"

    lines = ["Windows Registry Editor Version 5.00", "", f"[HKEY_CURRENT_USER\\{key}\\Uninstall]"]
    lines += [entry(name, value) for name, value in case["uninstall"].items()]
    if case["product"] is not None:
        lines += ["", f"[HKEY_CURRENT_USER\\{key}\\Product]", entry("", case["product"])]
    return "\r\n".join(lines) + "\r\n"


def delete_scratch_key(subkey: str) -> None:
    """Delete a scratch HKCU key in both registry views, then make sure it is gone in both."""
    for view in ("/reg:64", "/reg:32"):
        # A view where the key is already gone answers 1; the query below is the check.
        subprocess.run(["reg", "delete", "HKCU\\" + subkey, "/f", view], capture_output=True)
    for view in ("/reg:64", "/reg:32"):
        if subprocess.run(["reg", "query", "HKCU\\" + subkey, view], capture_output=True).returncode == 0:
            raise RuntimeError(f"The scratch key HKCU\\{subkey} is still there ({view}).")


def run_registry(case):
    # A key of its own directly under Software, so deleting it leaves no parent behind.
    key = rf"Software\sylvia-gm-port-test-helper-vectors-{os.getpid()}"
    folder = Path(tempfile.mkdtemp(prefix="sylvia-registry-"))
    saved = installed.UNINSTALL_KEY, installed.PRODUCT_KEY
    try:
        path = folder / "case.reg"
        path.write_bytes(b"\xff\xfe" + reg_file(key, case).encode("utf-16-le", "surrogatepass"))
        subprocess.run(["reg", "import", str(path)], check=True, capture_output=True)
        installed.UNINSTALL_KEY, installed.PRODUCT_KEY = key + r"\Uninstall", key + r"\Product"
        try:
            row = installed.WindowsRegistration().read()
            return {"ok": {name: units(value) for name, value in row.items()}}
        except UpdateError as error:
            return {"error": [error.code, str(error)]}
    finally:
        installed.UNINSTALL_KEY, installed.PRODUCT_KEY = saved
        delete_scratch_key(key)
        shutil.rmtree(folder)


# ---- the small pure parts ----------------------------------------------------------------------

def receipts():
    good = {"role": "owner", "handle": 102, "pid": 11, "creation_time": 2}
    for value in [good, {**good, "role": "dictation"}, {**good, "role": "other"}, {**good, "handle": 0},
                  {**good, "handle": 2**64 - 1}, {**good, "handle": True}, {**good, "pid": 2**32},
                  {**good, "pid": 2**32 - 1}, {**good, "creation_time": -1}, {**good, "creation_time": 1.0},
                  {**good, "extra": 1}, {k: v for k, v in good.items() if k != "pid"}, [1], "owner"]:
        yield {"value": value, "result": outcome(lambda: vars(ProcessReceipt.parse(value)))}


def barriers():
    desktop = {"role": "desktop", "handle": 101, "pid": 10, "creation_time": 1}
    owner = {"role": "owner", "handle": 102, "pid": 11, "creation_time": 2}
    dictation = {"role": "dictation", "handle": 103, "pid": 12, "creation_time": 3}
    table = {101: (10, 1), 102: (11, 2), 103: (12, 3)}
    cases = [
        ([desktop, owner], table, [True, True]), ([desktop, owner, dictation], table, [True, False]),
        ([desktop], table, []), ([desktop, desktop], table, []), ([desktop, {**owner, "handle": 101}], table, []),
        ([desktop, {**owner, "pid": 10}], table, []), ([owner, dictation], table, []),
        ([desktop, owner], {101: (10, 1), 102: (11, 9)}, []), ([desktop, owner, dictation], {101: (10, 1), 102: (11, 2)}, []),
        ([desktop, owner, dictation, {**dictation, "role": "owner"}], table, []),
    ]
    for rows, identities, exits in cases:
        events = []
        script = list(exits)

        class Api:
            def identity(self, handle):
                if handle not in identities:
                    raise UpdateError("process_identity_unavailable", "The owned process instance could not be verified.")
                return identities[handle]

            def exited(self, handle):
                events.append(f"exited:{handle}")
                return script.pop(0) if script else True

            def close(self, handle):
                events.append(f"closed:{handle}")

        def build():
            barrier = OwnedProcesses([ProcessReceipt.parse(row) for row in rows], Api())
            first = barrier.all_exited()
            barrier.close()
            barrier.close()
            return [first, outcome(barrier.all_exited)]

        result = outcome(build)
        yield {"receipts": rows, "identities": {str(k): list(v) for k, v in identities.items()}, "exits": exits,
               "result": result, "events": events}


def commands():
    for artifact, live in [(r"C:\Users\Example\verified installer.exe", r"C:\Program Files\Sylvia GM"),
                           (r"C:\x\artifact.verified", r"C:\app"), (r"C:\x\a.exe", r"\\server\share\app"),
                           (r"C:\x\a.exe", "//server/share/app"), ("relative.exe", r"C:\app"),
                           (r"C:\x\a.exe", r"C:\app\"quoted"), (r'C:\x\a"b.exe', r"C:\app"),
                           (r"C:\x\a.exe", "C:\\app\nnext"), (r"C:\x\back\\", r"C:\app")]:
        yield {"artifact": artifact, "live": live, "result": outcome(
            lambda: installed.nsis_command_line(PureWindowsPath(artifact), PureWindowsPath(live)))}
    for arguments in [["a", "b c", ""], ['a\\"b', "c\\\\", "d e\\"], ["tab\there", 'q"uote']]:
        yield {"list2cmdline": arguments, "result": subprocess.list2cmdline(arguments)}


def versions():
    for text in ["0.4.1", "0.4.0-alpha.1", "0.4.0-beta.1", "0.4.0-alpha.12", "0.4.0-alpha.x", "1.2.3-alpha.1+build",
                 "0.4.0-alpha.1.2"]:
        yield {"version": text, "registered": sorted(installed.registered_versions(Version.parse(text)))}


def failures():
    for label, error in [("sharing violation", OSError(None, "in use", None, 32)),
                         ("lock violation", OSError(None, "locked", None, 33)),
                         ("access denied", OSError(None, "denied", None, 5)),
                         ("missing file", OSError(None, "missing", None, 2)),
                         ("update error", UpdateError("disk_full", "full")),
                         ("update error with an odd code", UpdateError("Disk-Full", "full")),
                         ("owner conflict", OwnerConflict("held")), ("value error", ValueError("x"))]:
        yield {"case": label, "winerror": getattr(error, "winerror", None), "code": helper._failure_code(error)}


def main() -> None:
    vectors = {
        "python": sys.version.split()[0],
        "public_key": prep.KEY.public_key().public_bytes_raw().hex(),
        "key_id": prep.KEY_ID,
        "receipts": list(receipts()),
        "barriers": list(barriers()),
        "commands": list(commands()),
        "versions": list(versions()),
        "failures": list(failures()),
        "registry": [{**case, "result": run_registry(case)} for case in registry_cases()],
        "installer_files": {"new": encoded(NEW_FILES), "old": encoded(OLD_FILES)},
        "scenarios": [],
    }
    for scenario in scenarios():
        vectors["scenarios"].append({**scenario, "recorded": run(scenario)})
    vectors["blobs"] = dict(sorted(BLOBS.items()))
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
