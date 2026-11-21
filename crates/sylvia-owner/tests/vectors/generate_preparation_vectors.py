"""Generate reference vectors for sylvia_owner::desktop_update's update preparation.

Run with the snapshot's own Windows interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-owner/tests/vectors/generate_preparation_vectors.py

Each scenario is a real portable copy in a temporary folder: the running 0.4.1 copy with its
hash manifest, its isolated data (a small SQLite database and settings), and a retained
verified 0.4.2 download (a signed manifest and a ZIP artifact). The scenario's file tree is
written into the vectors, so the Rust test rebuilds the same bytes. Then a list of steps
drives Python's PreparedUpdate, PortableReplacement, RestartHandshake and extract_portable,
and every reply and the final tree are recorded.

Run-specific values are normalized the same way in both languages: folder identities
(`data_identity`, `app_identity`) become `<identity>`, and every run of exactly 32 hex digits
becomes `<id:N>` in order of first appearance. The signing key comes from a fixed, test-only
seed, so the output reproduces byte for byte. This script is reference tooling for the port
and is retired with the Python source.
"""

from __future__ import annotations

import base64
import hashlib
import io
import json
import re
import shutil
import sqlite3
import sys
import tempfile
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(ROOT / "src"))

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey  # noqa: E402
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat  # noqa: E402

from sylvia_gm import __release_channel__, __version__  # noqa: E402
from sylvia_gm.application.desktop_update.manifest import (  # noqa: E402
    KeyRing,
    UpdateError,
    Version,
    canonical_bytes,
    parse_manifest,
)
from sylvia_gm.application.desktop_update.payload import extract_portable  # noqa: E402
from sylvia_gm.application.desktop_update.preparation import PreparedUpdate  # noqa: E402
from sylvia_gm.application.desktop_update.restart import RestartHandshake  # noqa: E402
from sylvia_gm.application.desktop_update.storage import UpdateStore  # noqa: E402
from sylvia_gm.application.desktop_update.launch import Installation  # noqa: E402
from sylvia_gm.application.desktop_update.startup import authorize_startup  # noqa: E402
from sylvia_gm.portable_artifact import ENTRY_MANIFEST, REQUIRED_PAYLOADS  # noqa: E402

OUTPUT = HERE / "preparation_vectors.json"
KEY_ID = "prep-key"
OP = "0123456789abcdef0123456789abcdef"
ATTEMPT = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
NEW = "0.4.2"
TARGET = "windows-x86_64"
HEX_RUN = re.compile(r"[0-9a-f]+")


def test_key() -> Ed25519PrivateKey:
    """A TEST-ONLY signing key from a fixed, public seed, so the vectors reproduce exactly.

    Anyone can derive it from this file. Its public key must never be added to any production
    KeyRing or to `PUBLIC_KEYS`; a release signed with it must never be trusted.
    """
    return Ed25519PrivateKey.from_private_bytes(hashlib.sha256(b"sylvia-update-vectors test key preparation").digest())


KEY = test_key()
RING = KeyRing({KEY_ID: KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)})


def payload(version: str, *, extra=(), drop=()) -> dict[str, bytes]:
    """A portable payload: every required file, the two metadata files and the hash manifest."""
    names = sorted((REQUIRED_PAYLOADS | {"BUILD-IDENTITY.json", "SOURCE-INPUTS.json", *extra}) - set(drop))
    files = {}
    for name in names:
        if name == "sylvia-portable.json":
            files[name] = b'{"version":1}\n'
        elif name.endswith(".exe"):
            files[name] = b"MZ" + f" {version} {name}\n".encode()
        else:
            files[name] = f"{version} {name}\n".encode()
    entries = {name: {"sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)} for name, raw in files.items()}
    files[ENTRY_MANIFEST] = json.dumps(entries, indent=2, sort_keys=True).encode() + b"\n"
    return files


def zip_bytes(files: dict[str, bytes], *, mutate=None) -> bytes:
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as archive:
        for name, raw in sorted(files.items()):
            member = zipfile.ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            member.compress_type = zipfile.ZIP_DEFLATED
            member.external_attr = 0o100644 << 16
            if mutate:
                mutate(member)
            archive.writestr(member, raw)
    return stream.getvalue()


def envelope(files: dict[str, bytes], artifact: bytes, *, inventory=None) -> bytes:
    rows = inventory if inventory is not None else [
        {"path": name, "size": len(raw), "sha256": hashlib.sha256(raw).hexdigest()} for name, raw in sorted(files.items())
    ]
    signed = {
        "format_version": 1,
        "version": NEW,
        "owner_version": NEW,
        "minimum_app_version": "0.4.0",
        "channel": __release_channel__,
        "target": TARGET,
        "distribution": "portable",
        "notes": "Preparation vectors",
        "artifact": {
            "url": f"https://github.com/pneuma-io/sylvia-gm/releases/download/v{NEW}/sylvia-gm-{NEW}-{TARGET}-portable.zip",
            "size": len(artifact),
            "sha256": hashlib.sha256(artifact).hexdigest(),
        },
        "files": rows,
    }
    signature = base64.b64encode(KEY.sign(canonical_bytes(signed))).decode()
    return canonical_bytes({"signed": signed, "key_id": KEY_ID, "signature": signature})


def database_bytes() -> bytes:
    folder = Path(tempfile.mkdtemp(prefix="sylvia-prep-db-"))
    try:
        path = folder / "authority.sqlite3"
        connection = sqlite3.connect(path)
        connection.execute("CREATE TABLE campaigns (campaign_id TEXT PRIMARY KEY, name TEXT)")
        connection.execute("CREATE INDEX campaigns_name ON campaigns (name)")
        connection.execute("INSERT INTO campaigns VALUES ('c1', 'Rift')")
        connection.execute("PRAGMA user_version = 7")
        connection.commit()
        connection.close()
        return path.read_bytes()
    finally:
        shutil.rmtree(folder)


DATABASE = database_bytes()


def base_tree(*, live_extra=(), new_extra=("licenses/NEW-NOTICE.md",), new_drop=(), artifact=None, inventory=None,
              phase="verified", config=True):
    """The running copy at `app`, its data at `app/isolated-data`, and the retained download."""
    old = payload(__version__, extra=("licenses/OLD-NOTICE.md", *live_extra))
    new = payload(NEW, extra=new_extra, drop=new_drop)
    archive = artifact(new) if artifact else zip_bytes(new)
    tree = {f"app/{name}": raw for name, raw in old.items()}
    tree["app/isolated-data/authority.sqlite3"] = DATABASE
    tree["backup-source.sqlite3"] = DATABASE
    if config:
        tree["app/isolated-data/config.yaml"] = b"default_model: standin\n"
    updates = "app/isolated-data/.application-updates"
    tree[f"{updates}/{OP}/manifest.json"] = envelope(new, archive, inventory=inventory)
    tree[f"{updates}/{OP}/artifact.verified"] = archive
    tree[f"{updates}/current.json"] = canonical_bytes(
        {"format_version": 1, "operation_id": OP, "phase": phase, "version": NEW, "error_code": None})
    return tree


def outcome(call):
    try:
        return {"ok": call()}
    except UpdateError as error:
        return {"error": [error.code, str(error)]}
    except Exception as error:  # noqa: BLE001 - recorded as Python's class, which a caller reports
        return {"other": type(error).__name__}


def listing(root: Path) -> list:
    rows = []
    for path in sorted(root.rglob("*")):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            rows.append([relative, "dir"])
        elif path.suffix == ".json" and ".application-updates/" in relative:
            # Update receipts are compared as values; application files by hash.
            try:
                rows.append([relative, json.loads(path.read_text(encoding="utf-8"))])
            except (UnicodeError, ValueError):
                rows.append([relative, hashlib.sha256(path.read_bytes()).hexdigest()])
        else:
            rows.append([relative, hashlib.sha256(path.read_bytes()).hexdigest()])
    # Sorted by the relative path text, so aliases are numbered the same way in Rust.
    return sorted(rows, key=lambda row: row[0])


def normalize(value):
    if isinstance(value, dict):
        return {key: "<identity>" if key in ("data_identity", "app_identity") else normalize(item)
                for key, item in value.items()}
    if isinstance(value, list):
        return [normalize(item) for item in value]
    return value


def alias(value):
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    seen: dict[str, str] = {}

    def name(match):
        if len(match[0]) != 32:
            return match[0]
        return seen.setdefault(match[0], f"<id:{len(seen) + 1}>")

    return json.loads(HEX_RUN.sub(name, text))


def record(results, files):
    aliased_results, aliased_files = alias([normalize(results), normalize(files)])
    aliased_files.sort(key=lambda row: row[0])
    return {"results": aliased_results, "files": aliased_files}


def run(scenario):
    folder = Path(tempfile.mkdtemp(prefix="sylvia-prep-vectors-")).resolve()
    results = []
    try:
        for relative, digest in scenario["tree"].items():
            path = folder / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(base64.b64decode(BLOBS[digest]))
        live, data = folder / "app", folder / "app" / "isolated-data"
        current = Version.parse(__version__)
        state = {"prepared": None, "handshake": None, "nonce": None}
        quiesced = list(scenario.get("quiesced", []))

        def quiet():
            return quiesced.pop(0) if quiesced else True

        def backup(target):
            shutil.copyfile(folder / "backup-source.sqlite3", target)

        for op in scenario["ops"]:
            name = op["op"]
            if name == "new":
                def make():
                    state["prepared"] = PreparedUpdate(
                        Path(str(data).replace("isolated-data", op["data"])) if "data" in op else data, live, OP,
                        op.get("attempt", ATTEMPT), current=current, target=TARGET, distribution="portable",
                        verify=RING.verify)
                    return None
                result = outcome(make)
            elif name == "prepare":
                result = outcome(lambda: state["prepared"].prepare(backup, quiet))
            elif name == "require_selected":
                result = outcome(lambda: state["prepared"].require_selected())
            elif name == "apply":
                exited = op.get("exited", True)
                result = outcome(lambda: state["prepared"].apply(lambda: exited))
            elif name == "create_restart":
                def create():
                    state["handshake"] = state["prepared"].create_restart(op["identity"])
                    state["nonce"] = state["handshake"]._load()["nonce"]
                    return state["nonce"]
                result = outcome(create)
            elif name == "launch":
                def launch():
                    return list(state["handshake"].launch_once(lambda arguments: list(arguments)))
                result = outcome(launch)
            elif name in ("authorize", "confirm_handshake"):
                def context():
                    return {"nonce": op.get("nonce", state["nonce"]), "live": live,
                            "desktop_version": op.get("version", NEW), "owner_version": op.get("version", NEW),
                            "target": TARGET, "distribution": "portable", "storage_compatibility": 1}
                method = "authorize" if name == "authorize" else "confirm"
                result = outcome(lambda: getattr(state["handshake"], method)(**context()) and None)
            elif name == "confirmed":
                result = outcome(lambda: state["handshake"].confirmed())
            elif name == "confirm":
                result = outcome(lambda: state["prepared"].confirm())
            elif name == "save_phase":
                result = outcome(lambda: UpdateStore(data / ".application-updates").save(OP, op["phase"], NEW))
            elif name == "write":
                path = folder / op["path"]
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(base64.b64decode(op["b64"]))
                result = {"ok": None}
            elif name == "delete":
                (folder / op["path"]).unlink()
                result = {"ok": None}
            elif name == "authorize_startup":
                def authorize():
                    installation = Installation.from_launch(
                        {"live": str(live), "data_root": str(data), "version": __version__, "target": TARGET,
                         "distribution": "portable"}, data)
                    request = {"operation_id": OP, "attempt": ATTEMPT, "nonce": op.get("nonce", state["nonce"])}
                    state["startup"] = authorize_startup(
                        data, installation, request, running=Version.parse(op.get("running", NEW)),
                        desktop_version=op.get("running", NEW), storage_compatibility=1, verify=RING.verify)
                    return None
                result = outcome(authorize)
            elif name == "startup_confirm":
                result = outcome(lambda: state["startup"].confirm())
            elif name == "extract":
                def extract():
                    base = data / ".application-updates" / OP
                    manifest = parse_manifest((base / "manifest.json").read_bytes(), current=current,
                                              target=TARGET, distribution="portable", verify=RING.verify)
                    (base / "attempts").mkdir(exist_ok=True)
                    (base / "attempts" / ATTEMPT).mkdir(exist_ok=True)
                    extract_portable(base / "artifact.verified", manifest, base / "attempts" / ATTEMPT / "prepared")
                    return None
                result = outcome(extract)
            else:
                raise ValueError(name)
            results.append({"op": name, "result": result})
        files = listing(folder)
    finally:
        shutil.rmtree(folder)
    return record(results, files)


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


BLOBS: dict[str, str] = {}

IDENTITY = {"format_version": 1, "product": "Sylvia GM", "component": "owner", "version": NEW,
            "storage_compatibility": 1, "target": TARGET}


def scenarios():
    def encoded(tree):
        tree_rows = {}
        for path, raw in sorted(tree.items()):
            digest = hashlib.sha256(raw).hexdigest()
            BLOBS[digest] = b64(raw)
            tree_rows[path] = digest
        return tree_rows

    full = [{"op": "new"}, {"op": "prepare"}, {"op": "require_selected"}, {"op": "apply"},
            {"op": "create_restart", "identity": IDENTITY}, {"op": "confirmed"}, {"op": "launch"},
            {"op": "launch"}, {"op": "authorize", "version": "0.4.3"}, {"op": "authorize"},
            {"op": "confirm_handshake"}, {"op": "confirm_handshake"}, {"op": "confirmed"}, {"op": "confirm"}]
    yield {"name": "prepare, apply, restart and confirm", "tree": encoded(base_tree()), "ops": full}
    yield {"name": "the updated owner authorizes and confirms its start", "tree": encoded(base_tree()),
           "ops": [{"op": "new"}, {"op": "prepare"}, {"op": "apply"}, {"op": "create_restart", "identity": IDENTITY},
                   {"op": "authorize_startup"}, {"op": "launch"}, {"op": "authorize_startup", "running": "0.4.3"},
                   {"op": "authorize_startup", "nonce": "d" * 32}, {"op": "authorize_startup"},
                   {"op": "startup_confirm"}, {"op": "startup_confirm"}, {"op": "confirm"}, {"op": "startup_confirm"},
                   {"op": "startup_confirm"}]}
    yield {"name": "without settings", "tree": encoded(base_tree(config=False)),
           "ops": [{"op": "new"}, {"op": "prepare"}, {"op": "require_selected"}]}
    yield {"name": "deferred download prepares too", "tree": encoded(base_tree(phase="deferred")),
           "ops": [{"op": "new"}, {"op": "prepare"}]}
    yield {"name": "not selected for preparation", "tree": encoded(base_tree(phase="available")),
           "ops": [{"op": "new"}, {"op": "prepare"}, {"op": "require_selected"}]}
    yield {"name": "busy before preparing", "tree": encoded(base_tree()), "quiesced": [False],
           "ops": [{"op": "new"}, {"op": "prepare"}]}
    yield {"name": "work starts during the checkpoint", "tree": encoded(base_tree()), "quiesced": [True, True, False],
           "ops": [{"op": "new"}, {"op": "prepare"}, {"op": "prepare"}, {"op": "new", "attempt": "b" * 32},
                   {"op": "prepare"}]}
    yield {"name": "work starts after the checkpoint", "tree": encoded(base_tree()), "quiesced": [True, True, True, False],
           "ops": [{"op": "new"}, {"op": "prepare"}]}
    tampered = base_tree()
    tampered[f"app/isolated-data/.application-updates/{OP}/artifact.verified"] += b"!"
    yield {"name": "tampered download", "tree": encoded(tampered), "ops": [{"op": "new"}]}
    changed = base_tree()
    changed["app/README-PORTABLE.txt"] = b"changed by hand\n"
    yield {"name": "live copy changed by hand", "tree": encoded(changed), "ops": [{"op": "new"}, {"op": "prepare"}]}
    yield {"name": "an unowned file in the way", "tree": encoded(
        {**base_tree(), "app/licenses/NEW-NOTICE.md": b"someone else's file\n"}),
           "ops": [{"op": "new"}, {"op": "prepare"}]}
    elsewhere = base_tree()
    for path in [path for path in elsewhere if path.startswith("app/isolated-data/.application-updates/")]:
        elsewhere[path.replace("app/isolated-data/", "app/elsewhere/")] = elsewhere[path]
    yield {"name": "data outside the portable copy", "tree": encoded(elsewhere),
           "ops": [{"op": "new", "data": "elsewhere"}]}
    yield {"name": "apply while processes run", "tree": encoded(base_tree()),
           "ops": [{"op": "new"}, {"op": "prepare"}, {"op": "apply", "exited": False}, {"op": "apply"}]}
    yield {"name": "restart before apply finished", "tree": encoded(base_tree()),
           "ops": [{"op": "new"}, {"op": "prepare"}, {"op": "create_restart", "identity": IDENTITY}]}
    yield {"name": "wrong owner identity", "tree": encoded(base_tree()),
           "ops": [{"op": "new"}, {"op": "prepare"}, {"op": "apply"},
                   {"op": "create_restart", "identity": {**IDENTITY, "version": "0.4.3"}},
                   {"op": "create_restart", "identity": {**IDENTITY, "storage_compatibility": True}},
                   {"op": "create_restart", "identity": IDENTITY}, {"op": "create_restart", "identity": IDENTITY}]}
    base = f"app/isolated-data/.application-updates/{OP}"
    yield {"name": "selection superseded", "tree": encoded(base_tree()),
           "ops": [{"op": "new"}, {"op": "prepare"},
                   {"op": "write", "path": f"{base}/selected-attempt.json",
                    "b64": b64(canonical_bytes({"format_version": 1, "attempt": "c" * 32}))},
                   {"op": "require_selected"}]}
    yield {"name": "prepared binding changed", "tree": encoded(base_tree()),
           "ops": [{"op": "new"}, {"op": "prepare"},
                   {"op": "write", "path": f"{base}/attempts/{ATTEMPT}/prepared-update.json", "b64": b64(b"{}")},
                   {"op": "require_selected"}]}
    yield {"name": "retained helper changed", "tree": encoded(base_tree()),
           "ops": [{"op": "new"}, {"op": "prepare"},
                   {"op": "write", "path": f"{base}/attempts/{ATTEMPT}/helper/sylvia-gm-owner.exe", "b64": b64(b"MZ!")},
                   {"op": "require_selected"}]}
    yield {"name": "checkpoint database changed", "tree": encoded(base_tree()),
           "ops": [{"op": "new"}, {"op": "prepare"},
                   {"op": "write", "path": f"{base}/attempts/{ATTEMPT}/data-checkpoint/authority.sqlite3", "b64": b64(b"x")},
                   {"op": "require_selected"}]}
    yield {"name": "confirm before the restart acknowledged", "tree": encoded(base_tree()),
           "ops": [{"op": "new"}, {"op": "prepare"}, {"op": "apply"}, {"op": "create_restart", "identity": IDENTITY},
                   {"op": "launch"}, {"op": "confirm"}]}
    yield {"name": "an apply resumed after an interruption", "tree": encoded(base_tree()),
           "ops": [{"op": "new"}, {"op": "prepare"}, {"op": "save_phase", "phase": "applying"},
                   {"op": "delete", "path": "app/licenses/OLD-NOTICE.md"}, {"op": "apply"}]}

    # Archive checks, each through extract_portable on its own download.
    new = payload(NEW, extra=("licenses/NEW-NOTICE.md",))

    def archive_case(name, archive, *, inventory=None, files=None):
        tree = base_tree()
        tree[f"{base}/manifest.json"] = envelope(files or new, archive, inventory=inventory)
        tree[f"{base}/artifact.verified"] = archive
        return {"name": f"archive: {name}", "tree": encoded(tree), "ops": [{"op": "extract"}]}

    yield archive_case("complete", zip_bytes(new))
    yield archive_case("stored members", zip_bytes(new, mutate=lambda m: setattr(m, "compress_type", zipfile.ZIP_STORED)))
    yield archive_case("member outside the inventory", zip_bytes({**new, "licenses/EXTRA.md": b"x\n"}), files=new)
    yield archive_case("member missing", zip_bytes({k: v for k, v in new.items() if k != "licenses/NEW-NOTICE.md"}),
                       files=new)
    yield archive_case("unsafe member path", zip_bytes({**{k: v for k, v in new.items()}, "../x": b"x"}), files=new)
    yield archive_case("directory member", zip_bytes({**new, "licenses/": b""}), files=new)
    yield archive_case("symbolic link member", zip_bytes(new, mutate=lambda m: setattr(
        m, "external_attr", (0o120777 << 16) if m.filename == "README-PORTABLE.txt" else m.external_attr)))
    wrong_size = [{"path": k, "size": len(v) + (1 if k == "COPYRIGHT" else 0),
                   "sha256": hashlib.sha256(v).hexdigest()} for k, v in sorted(new.items())]
    yield archive_case("size differs from the inventory", zip_bytes(new), inventory=wrong_size)
    wrong_hash = [{"path": k, "size": len(v), "sha256": ("0" * 64 if k == "COPYRIGHT" else hashlib.sha256(v).hexdigest())}
                  for k, v in sorted(new.items())]
    yield archive_case("hash differs from the inventory", zip_bytes(new), inventory=wrong_hash)
    # A stored member with one changed byte: its CRC no longer matches (`BadZipFile`).
    corrupt = bytearray(zip_bytes(new, mutate=lambda m: setattr(m, "compress_type", zipfile.ZIP_STORED)))
    first = sorted(new)[0]
    corrupt[corrupt.find(b"PK\x03\x04") + 30 + len(first.encode())] ^= 0xFF
    yield archive_case("corrupt member", bytes(corrupt))
    yield archive_case("not a ZIP", b"not a zip archive at all")
    bad_marker = {**new, "sylvia-portable.json": b'{"version":2}\n'}
    yield archive_case("marker of another format", zip_bytes(bad_marker), files=bad_marker)
    bad_exe = {**new, "Sylvia GM.exe": b"ZM not an executable\n"}
    yield archive_case("desktop is not an executable", zip_bytes(bad_exe), files=bad_exe)


def main() -> None:
    vectors = {
        "python": sys.version.split()[0],
        "public_key": KEY.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw).hex(),
        "key_id": KEY_ID,
        "scenarios": [],
    }
    for scenario in scenarios():
        vectors["scenarios"].append({**scenario, "recorded": run(scenario)})
    vectors["blobs"] = dict(sorted(BLOBS.items()))
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")


if __name__ == "__main__":
    main()
