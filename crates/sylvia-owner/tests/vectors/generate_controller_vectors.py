"""Generate reference vectors for sylvia_owner::desktop_update's controller and service.

Run with the snapshot's own Windows interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-owner/tests/vectors/generate_controller_vectors.py

Each scenario drives Python's `UpdateController` (or `DesktopUpdateService` with a pinned key)
over a scripted transport in a fresh temporary data folder and records every reply, then the
update folder it leaves behind. Operation identities are random in both languages, so every
32-digit hex value becomes `<id:N>` in order of first appearance. As for update_vectors.json,
the signing key comes from a fixed, test-only seed, so the output reproduces byte for byte; only
its public key and signed envelopes are written.
This script is reference tooling for the port and is retired with the Python source.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import sys
import tempfile
import threading
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import generate_update_vectors as common  # noqa: E402

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402

from sylvia_gm import __version__  # noqa: E402
from sylvia_gm.application.desktop_update import service as service_module  # noqa: E402
from sylvia_gm.application.desktop_update.controller import UpdateController  # noqa: E402
from sylvia_gm.application.desktop_update.manifest import KeyRing, UpdateError, Version  # noqa: E402
from sylvia_gm.application.desktop_update.service import DesktopUpdateService  # noqa: E402

OUTPUT = HERE / "controller_vectors.json"
STALE = "5" * 32
ATTEMPT = "a" * 32
# Runs of hex digits; only a run of exactly 32 is an identity (a 64-digit hash is kept).
HEX_RUN = re.compile(r"[0-9a-f]+")
ARTIFACT = [{"fill": "61", "count": 70000}, {"fill": "62", "count": 50000}]
PREVIOUS = [{"fill": "70", "count": 40000}]


def test_key(name: str) -> Ed25519PrivateKey:
    """A TEST-ONLY signing key from a fixed, public seed, so the vectors reproduce exactly.

    Anyone can derive it from this file. Its public key must never be added to any production
    KeyRing or to `PUBLIC_KEYS`; a release signed with it must never be trusted.
    """
    seed = hashlib.sha256(f"sylvia-update-vectors test key {name}".encode()).digest()
    return Ed25519PrivateKey.from_private_bytes(seed)


def artifact_bytes(segments) -> bytes:
    return b"".join(common.segment_bytes(segment) for segment in segments)


def signed_release(key, version, distribution, segments):
    signed = common.base_signed(distribution, version)
    payload = artifact_bytes(segments)
    signed["artifact"].update(size=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    return signed


def release_scripts(key, *, distribution="portable", version="0.4.2", tamper=False, cancel_at=None,
                    index_error=None, releases=True):
    """The official index, one release's manifest and its artifact; for an installed copy also
    the running version's recovery manifest and installer."""
    scripts = {}
    if index_error:
        scripts[common.transport.OFFICIAL_RELEASES] = {"open_error": index_error}
        return scripts
    assets = [common.asset(version, distribution)] if releases else []
    rows = [common.release_row(version, assets=assets)] if releases else []
    scripts[common.transport.OFFICIAL_RELEASES] = {"chunks": common.body(rows)}
    if not releases:
        return scripts
    signed = signed_release(key, version, distribution, ARTIFACT)
    scripts[common.asset(version, distribution)["browser_download_url"]] = {"chunks": common.body(common.envelope(key, signed))}
    chunks = list(ARTIFACT)
    if tamper:
        chunks = [chunks[0], {"fill": "63", "count": 50000}]
    artifact = {"chunks": chunks, "headers": [["Content-Length", str(len(artifact_bytes(ARTIFACT)))]]}
    if cancel_at is not None:
        artifact["cancel_at"] = cancel_at
    scripts[signed["artifact"]["url"]] = artifact
    if distribution == "installed":
        current = signed_release(key, __version__, "installed", PREVIOUS)
        name = f"sylvia-gm-{__version__}-windows-x86_64-installed-update.json"
        url = f"https://github.com/pneuma-io/sylvia-gm/releases/download/v{__version__}/{name}"
        scripts[url] = {"chunks": common.body(common.envelope(key, current))}
        scripts[current["artifact"]["url"]] = {"chunks": list(PREVIOUS)}
    return scripts


def outcome(call):
    try:
        value = call()
        return {"ok": value}
    except UpdateError as error:
        return {"error": [error.code, str(error)]}


def folder_listing(root: Path) -> list:
    rows = []
    updates = root / ".application-updates"
    if not updates.exists():
        return rows
    for path in sorted(updates.rglob("*")):
        relative = path.relative_to(updates).as_posix()
        if path.is_dir():
            rows.append([relative, "dir"])
        elif path.name == "current.json":
            rows.append([relative, json.loads(path.read_text(encoding="utf-8"))])
        else:
            rows.append([relative, hashlib.sha256(path.read_bytes()).hexdigest()])
    return sorted(rows, key=lambda row: row[0])


def alias(value):
    """Replace each distinct 32-digit hex value with <id:N> in order of first appearance."""
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    seen: dict[str, str] = {}

    def name(match):
        if len(match[0]) != 32:
            return match[0]
        return seen.setdefault(match[0], f"<id:{len(seen) + 1}>")

    return json.loads(HEX_RUN.sub(name, text))


def aliased_record(results, files, opened):
    """Alias the identities, then sort the folder listing by its aliased paths: the raw paths
    begin with random identities, so their order would change from run to run."""
    # Identities are numbered in this fixed order (the results first), never in the order of
    # the raw listing, which follows the random identities.
    aliased_results, aliased_opened, aliased_files = alias([results, opened, files])
    aliased_files.sort(key=lambda row: row[0])
    return {"results": aliased_results, "files": aliased_files, "opened": aliased_opened}


def run_controller(key, scenario):
    ring = KeyRing({common.KEY_ID: common.public_bytes(key)})
    folder = Path(tempfile.mkdtemp(prefix="sylvia-controller-vectors-"))
    results = []
    try:
        root = folder / "data"
        root.mkdir()
        transport = common.ScriptedTransport(scenario["scripts"])
        distribution = scenario.get("distribution", "portable")

        def controller_for():
            return UpdateController(root=root / ".application-updates", current=Version.parse(__version__),
                                    target="windows-x86_64", distribution=distribution, transport=transport,
                                    verify=ring.verify)

        controller = None

        def renew():
            nonlocal controller
            controller = None
            controller = controller_for()
            return controller.state()

        results.append({"op": "new", "result": outcome(renew)})
        failures = []
        for op in scenario["ops"]:
            name = op["op"]
            identity = STALE if op.get("stale") else (controller.state()["operation_id"] if controller else None)
            if name == "new":
                if controller is not None:
                    controller.close()
                    controller.wait()
                result = outcome(renew)
            elif name == "state":
                result = outcome(controller.state)
            elif name == "wait":
                controller.wait()
                result = outcome(controller.state)
            elif name in ("check",):
                result = outcome(controller.check)
            elif name in ("download", "cancel", "defer"):
                result = outcome(lambda: getattr(controller, name)(identity))
            elif name == "arm_shutdown":
                result = outcome(lambda: controller.arm_shutdown(identity))
            elif name == "prepare":
                def action():
                    if "fail" in op:
                        raise UpdateError(*op["fail"])
                    return op.get("attempt", ATTEMPT)
                result = outcome(lambda: controller.prepare(identity, action, lambda: failures.append(1)))
            elif name == "write":
                path = root / ".application-updates" / op["path"].replace("<op>", identity or "")
                path.parent.mkdir(parents=True, exist_ok=True)
                if "json" in op:
                    value = json.loads(json.dumps(op["json"]).replace("<op>", identity or ""))
                    path.write_bytes(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())
                else:
                    path.write_bytes(bytes.fromhex(op["hex"]))
                result = {"ok": None}
            else:
                raise ValueError(name)
            results.append({"op": name, "result": result})
        results.append({"op": "on_failure_calls", "result": len(failures)})
        if controller is not None:
            controller.close()
            controller.wait()
        files = folder_listing(root)
    finally:
        shutil.rmtree(folder)
    return aliased_record(results, files, transport.opened)


def run_service(key, scenario):
    folder = Path(tempfile.mkdtemp(prefix="sylvia-service-vectors-"))
    original_keys, original_transport = service_module.PUBLIC_KEYS, service_module.HttpsTransport
    results = []
    try:
        root = folder / "data"
        root.mkdir()
        transport = common.ScriptedTransport(scenario["scripts"])
        service_module.PUBLIC_KEYS = {common.KEY_ID: common.public_bytes(key)} if scenario.get("keys", True) else {}
        service_module.HttpsTransport = lambda: transport
        reserved = set(scenario.get("reserved", []))
        released = []
        service = DesktopUpdateService(root, installation=None, backup_database=None,
                                       reserved=lambda identity: identity in reserved,
                                       release=lambda identity: released.append(identity))
        for op in scenario["ops"]:
            if op["op"] == "wait":
                thread = service._restore_thread
                if thread is not None:
                    thread.join()
                controller = service._controller
                if controller is not None:
                    controller.wait()
                results.append({"op": "wait", "result": None})
                continue
            params = {"host": op.get("host", {"version": __version__, "target": "windows-x86_64",
                                              "distribution": "portable"})}
            if "operation_id" in op:
                state = service._controller.state() if service._controller else {"operation_id": None}
                params["operation_id"] = state["operation_id"] if op["operation_id"] == "<current>" else op["operation_id"]
            if op["op"] in ("download", "defer") and op.get("reserve"):
                reserved.add(params["operation_id"])
            results.append({"op": op["op"], "result": outcome(lambda: service.handle(op["op"], params))})
        results.append({"op": "released", "result": released})
        service.close()
        files = folder_listing(root)
    finally:
        service_module.PUBLIC_KEYS, service_module.HttpsTransport = original_keys, original_transport
        shutil.rmtree(folder)
    return aliased_record(results, files, transport.opened)


def controller_scenarios(key):
    portable = release_scripts(key)
    return [
        {"name": "check finds nothing", "scripts": release_scripts(key, releases=False),
         "ops": [{"op": "check"}, {"op": "wait"}, {"op": "download"}, {"op": "defer"}, {"op": "cancel"}]},
        {"name": "check, download, verify", "scripts": portable,
         "ops": [{"op": "check"}, {"op": "wait"}, {"op": "download", "stale": True}, {"op": "download"},
                 {"op": "wait"}, {"op": "download"}, {"op": "new"}, {"op": "state"}]},
        {"name": "defer then download", "scripts": portable,
         "ops": [{"op": "check"}, {"op": "wait"}, {"op": "defer"}, {"op": "download"}, {"op": "wait"},
                 {"op": "defer"}]},
        {"name": "tampered download", "scripts": release_scripts(key, tamper=True),
         "ops": [{"op": "check"}, {"op": "wait"}, {"op": "download"}, {"op": "wait"}, {"op": "download"},
                 {"op": "check"}, {"op": "wait"}]},
        {"name": "cancelled download", "scripts": release_scripts(key, cancel_at=1),
         "ops": [{"op": "check"}, {"op": "wait"}, {"op": "download"}, {"op": "wait"}, {"op": "cancel"}]},
        {"name": "index unavailable", "scripts": release_scripts(key, index_error=[
            "network_unavailable", "The update connection failed. You can retry safely."]),
         "ops": [{"op": "check"}, {"op": "wait"}, {"op": "new"}]},
        {"name": "installed keeps the current installer", "distribution": "installed",
         "scripts": release_scripts(key, distribution="installed"),
         "ops": [{"op": "check"}, {"op": "wait"}, {"op": "download"}, {"op": "wait"}, {"op": "new"},
                 {"op": "write", "path": "<op>/previous-installer/artifact.verified", "hex": "00"},
                 {"op": "new"}]},
        {"name": "verified artifact changed before restore", "scripts": portable,
         "ops": [{"op": "check"}, {"op": "wait"}, {"op": "download"}, {"op": "wait"},
                 {"op": "write", "path": "<op>/artifact.verified", "hex": "00"}, {"op": "new"},
                 {"op": "check"}, {"op": "wait"}]},
        {"name": "prepare, defer, prepare, arm shutdown", "scripts": portable,
         "ops": [{"op": "check"}, {"op": "wait"}, {"op": "arm_shutdown"}, {"op": "prepare"}, {"op": "download"},
                 {"op": "wait"}, {"op": "prepare"}, {"op": "wait"}, {"op": "defer"}, {"op": "prepare"},
                 {"op": "wait"}, {"op": "arm_shutdown"}, {"op": "check"}, {"op": "new"}, {"op": "check"}]},
        {"name": "preparation fails", "scripts": portable,
         "ops": [{"op": "check"}, {"op": "wait"}, {"op": "download"}, {"op": "wait"},
                 {"op": "prepare", "fail": ["application_busy", "Finish admitted work before preparing an update."]},
                 {"op": "wait"}, {"op": "prepare", "attempt": "not-an-attempt"}, {"op": "wait"},
                 {"op": "prepare"}, {"op": "wait"}]},
        {"name": "interrupted receipts", "scripts": portable,
         "ops": [{"op": "check"}, {"op": "wait"},
                 {"op": "write", "path": "current.json", "json": {"format_version": 1, "operation_id": "<op>",
                                                                  "phase": "downloading", "version": "0.4.2",
                                                                  "error_code": None}},
                 {"op": "new"},
                 {"op": "write", "path": "current.json", "json": {"format_version": 1, "operation_id": "<op>",
                                                                  "phase": "closing", "version": "0.4.2",
                                                                  "error_code": None}},
                 {"op": "new"}, {"op": "check"},
                 {"op": "write", "path": "current.json", "json": {"format_version": 1, "operation_id": "<op>",
                                                                  "phase": "available", "version": "0.4.3",
                                                                  "error_code": None}},
                 {"op": "new"},
                 {"op": "write", "path": "current.json", "json": {"format_version": 2}},
                 {"op": "new"}]},
    ]


def service_scenarios(key):
    portable = release_scripts(key)
    return [
        {"name": "no trusted key", "keys": False, "scripts": portable,
         "ops": [{"op": "state"}, {"op": "check"}]},
        {"name": "unsupported target with a key", "scripts": portable,
         "ops": [{"op": "state", "host": {"version": __version__, "target": "linux-x86_64", "distribution": "portable"}},
                 {"op": "check", "host": {"version": __version__, "target": "linux-x86_64", "distribution": "portable"}}]},
        {"name": "restore, check and download", "scripts": portable, "reserved": [],
         "ops": [{"op": "state"}, {"op": "wait"}, {"op": "state"}, {"op": "check"}, {"op": "wait"},
                 {"op": "download", "operation_id": "<current>"}, {"op": "wait"},
                 {"op": "defer", "operation_id": "<current>", "reserve": True}, {"op": "state"},
                 {"op": "prepare", "operation_id": "<current>"}, {"op": "handoff", "operation_id": "<current>"}]},
        {"name": "restore error is retried by check", "scripts": portable,
         "ops": [{"op": "state", "host": {"version": __version__, "target": "windows-x86_64", "distribution": "installed"}},
                 {"op": "wait"},
                 {"op": "check", "host": {"version": __version__, "target": "windows-x86_64", "distribution": "installed"}},
                 {"op": "wait"}, {"op": "state", "host": {"version": __version__, "target": "windows-x86_64", "distribution": "installed"}}]},
    ]


def launch_vectors():
    """`Installation.from_launch` and `for_recovery` on contexts under one temporary folder,
    written with `<tmp>` for that folder."""
    from sylvia_gm.application.desktop_update.launch import Installation

    folder = Path(tempfile.mkdtemp(prefix="sylvia-launch-vectors-")).resolve()
    try:
        for name in ("app", "data", "portable", "portable/isolated-data"):
            (folder / name).mkdir()

        def at(text: str) -> str:
            return text.replace("<tmp>", str(folder))

        def fill(value):
            if isinstance(value, str):
                return at(value)
            if isinstance(value, list):
                return [fill(item) for item in value]
            if isinstance(value, dict):
                return {key: fill(item) for key, item in value.items()}
            return value

        installed = {"live": "<tmp>/app", "data_root": "<tmp>/data", "version": __version__,
                     "target": "windows-x86_64", "distribution": "installed"}
        portable = {"live": "<tmp>/portable", "data_root": "<tmp>/portable/isolated-data", "version": __version__,
                    "target": "windows-x86_64", "distribution": "portable"}
        cases = [
            ("none", None, "<tmp>/data"),
            ("not an object", ["x"], "<tmp>/data"),
            ("installed", installed, "<tmp>/data"),
            ("installed, other separators", {**installed, "live": "<tmp>\\app\\"}, "<tmp>/data"),
            ("installed, other case", {**installed, "data_root": "<tmp>/DATA"}, "<tmp>/data"),
            ("portable", portable, "<tmp>/portable/isolated-data"),
            ("missing key", {k: v for k, v in installed.items() if k != "target"}, "<tmp>/data"),
            ("extra key", {**installed, "extra": 1}, "<tmp>/data"),
            ("other version", {**installed, "version": "0.4.0"}, "<tmp>/data"),
            ("other target", {**installed, "target": "linux-x86_64"}, "<tmp>/data"),
            ("other distribution", {**installed, "distribution": "msi"}, "<tmp>/data"),
            ("relative live", {**installed, "live": "app"}, "<tmp>/data"),
            ("empty live", {**installed, "live": ""}, "<tmp>/data"),
            ("live not text", {**installed, "live": 5}, "<tmp>/data"),
            ("long live", {**installed, "live": "<tmp>/" + "a" * 4096}, "<tmp>/data"),
            ("other data folder", installed, "<tmp>/portable"),
            ("portable data elsewhere", {**portable, "data_root": "<tmp>/data"}, "<tmp>/data"),
            ("missing live folder", {**installed, "live": "<tmp>/missing"}, "<tmp>/data"),
        ]
        rows = []
        for name, context, data in cases:
            value = fill(context)

            def summary(found):
                if found is None:
                    return None
                return {"live": str(found.live).replace(str(folder), "<tmp>"),
                        "data": str(found.data).replace(str(folder), "<tmp>"),
                        "distribution": found.distribution, "target": found.target}

            rows.append({"name": name, "context": context, "data": data,
                         "from_launch": outcome(lambda: summary(Installation.from_launch(value, Path(at(data))))),
                         "for_recovery": outcome(lambda: summary(Installation.for_recovery(value, Path(at(data)))))})
        for version in ("0.4.0", "bad"):
            context = {**installed, "version": version}
            value = fill(context)
            rows.append({"name": f"recovery version {version}", "context": context, "data": "<tmp>/data",
                         "from_launch": outcome(lambda: summary(Installation.from_launch(value, Path(at("<tmp>/data"))))),
                         "for_recovery": outcome(lambda: summary(Installation.for_recovery(value, Path(at("<tmp>/data")))))})
        return rows
    finally:
        shutil.rmtree(folder)


def main() -> None:
    key = test_key("controller")
    vectors = {
        "python": sys.version.split()[0],
        "public_key": common.public_bytes(key).hex(),
        "key_id": common.KEY_ID,
        "controller": [],
        "service": [],
        "launch": launch_vectors(),
    }
    for scenario in controller_scenarios(key):
        vectors["controller"].append({**scenario, "recorded": run_controller(key, scenario)})
    for scenario in service_scenarios(key):
        vectors["service"].append({**scenario, "recorded": run_service(key, scenario)})
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
