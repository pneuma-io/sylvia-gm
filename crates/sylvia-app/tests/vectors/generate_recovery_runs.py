"""Portable campaign recovery through Python's owner handlers.

Run with the snapshot's own interpreter, from the repository root, after
`crates/sylvia-store/tests/vectors/generate_archive_validation_runs.py` (its archives are the
sources) and `generate_purge_runs.py` (its final database is a base):

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_recovery_runs.py

Python's `handle_recover_campaign` and `handle_recover_campaign_file` run as the owner runs them,
with `AuthorityOwner.recover_campaign`: `stage_campaign_archive` (the source copied and hashed,
the ZIP preflight, the manifest discovered, the archive validated, the authority rows and blobs
spooled), the receipt prelookup, the campaign's owner lane, then the store's recovery
(`plan_recovery`, `insert_recovery_graph`, the receipt and every verifier, with
`_before_recovery_commit` and `_after_recovery_commit` raising where a run says so). Three
storage roots:

- `fresh` (an empty authority): the handlers' parameter checks; the four exported archives
  recovered, replayed, reused with another archive and recovered again under a new identity; the
  file command's source checks; archives whose ZIP framing or member names staging refuses; then
  the recovered workshop trashed and purged (its purge reclaims the blobs only it rooted and is
  refused for the manifest files still naming them) and the recovered campaign without blobs
  trashed, purged and recovered again.
- `damaged`: every damaged archive of the archive validation vectors, recovered from a file.
- `purged` (the purge run's final database, where the story campaign was purged): the story
  archive recovered with a fault before the commit, then with a fault after it (the receipt
  resolves the lost acknowledgement), then replayed.

Each run records the reply or the failure's class, code, message (the storage root written as
`<root>`) and supplemental codes. Paths
in parameters are written relative to the storage root as `<root>/...`. After each root the run
keeps its final database (`recovery/<root>.sqlite3.gz`). It writes `recovery/recovery_runs.json`;
the Rust owner replays every run through its dispatch (`commands/campaigns.rs`) and compares each
reply and each final database. Reference tooling only.
"""

from __future__ import annotations

import gzip
import io
import json
import random
import sqlite3
import sys
import tempfile
import types
import uuid
import zipfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))

_IDENTITIES = random.Random(20261128)
uuid.uuid4 = lambda: uuid.UUID(int=_IDENTITIES.getrandbits(128), version=4)  # type: ignore[assignment]

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.infrastructure import campaign_archive  # noqa: E402
from sylvia_gm.interfaces.commands import campaigns as handlers  # noqa: E402

OUT = HERE / "recovery"
STORE_VECTORS = REPOSITORY / "crates" / "sylvia-store" / "tests" / "vectors"
VALIDATION = STORE_VECTORS / "archive_validation"
BASES = {
    "fresh": STORE_VECTORS / "packages" / "seed_fresh.sqlite3.gz",
    "damaged": STORE_VECTORS / "packages" / "seed_fresh.sqlite3.gz",
    "purged": HERE / "purge" / "purge_runs.sqlite3.gz",
}
EXPORTED = ("story_exported", "clockwork_exported", "plain_exported", "workshop_exported")


def archives() -> dict[str, bytes]:
    record = json.loads((VALIDATION / "validation_runs.json").read_text(encoding="utf-8"))
    data = gzip.decompress((VALIDATION / "archives.bin.gz").read_bytes())
    return {case["name"]: data[case["offset"]:case["offset"] + case["length"]] for case in record["cases"]}


def recover(recovery_id: object, export_id: object = None, *, source: str | None = None,
            fault: str | None = None) -> dict[str, object]:
    params: dict[str, object] = {}
    if recovery_id is not None:
        params["recovery_id"] = recovery_id
    if source is not None:
        params["source_path"] = source
        op = "recover_campaign_file"
    else:
        op = "recover_campaign"
        if export_id is not None:
            params["export_id"] = export_id
    return {"op": op, "params": params, **({"fault": fault} if fault else {})}


def place(path: str, archive: str, *, suffix: str = "") -> dict[str, object]:
    """Write a vector archive (with `suffix` hex appended) at `path` under the root."""
    return {"op": "write", "path": path, "archive": archive, "suffix": suffix}


def trash(operation_id: str, campaign_id: str, revision: int, session: str | None = None) -> dict[str, object]:
    params: dict[str, object] = {"operation_id": operation_id, "campaign_id": campaign_id, "expected_revision": revision}
    if session is not None:
        params["designated_session_id"] = session
    return {"op": "trash_campaign", "params": params}


def purge(operation_id: str, campaign_id: str, revision: int) -> dict[str, object]:
    return {"op": "purge_campaign", "params": {
        "operation_id": operation_id, "campaign_id": campaign_id, "expected_revision": revision,
        "confirmation": f"permanently purge {campaign_id}"}}


def crafted(base: bytes, *, extra_name: str) -> bytes:
    """`base` rebuilt with one more stored member named `extra_name` before the manifest."""
    with zipfile.ZipFile(io.BytesIO(base)) as source:
        items = [(info.filename, source.read(info)) for info in source.infolist()]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_STORED) as output:
        for name, data in items[:-1] + [(extra_name, b"x")] + items[-1:]:
            output.writestr(campaign_archive.fixed_zip_info(name), data)
    return buffer.getvalue()


def operations(sources: dict[str, bytes]) -> dict[str, list[dict[str, object]]]:
    fresh: list[dict[str, object]] = [
        # The handlers' checks, then the owner's identity check and an absent source.
        recover("rec-1"), recover("rec-1", "-story"), recover(None, "story"), recover(" rec-1", "story"),
        recover("rec-1", "story"),
        *(place(f"exports/{name.removesuffix('_exported')}.sylvia-gm-campaign", name) for name in EXPORTED),
        recover("rec-1", "story"), recover("rec-1", "story"), recover("rec-1", "clockwork"),
        recover("rec-2", "story"),
        recover("rec-3", "clockwork"), recover("rec-4", "plain"), recover("rec-5", "workshop"),
        # The file command's own source checks.
        place("sources/story.zip", "story_exported"),
        recover("rec-6", source="<root>/sources/story.zip"),
        recover("rec-6", source="<root>/sources/missing.sylvia-gm-campaign"),
        {"op": "mkdir", "path": "sources/folder.sylvia-gm-campaign"},
        recover("rec-6", source="<root>/sources/folder.sylvia-gm-campaign"),
        recover("rec-1", source="<root>/exports/story.sylvia-gm-campaign"),
        # Staging's ZIP preflight and manifest discovery.
        place("sources/trailing.sylvia-gm-campaign", "story_exported", suffix="00"),
        recover("rec-7", source="<root>/sources/trailing.sylvia-gm-campaign"),
        {"op": "write", "path": "sources/empty.sylvia-gm-campaign", "hex": ""},
        recover("rec-7", source="<root>/sources/empty.sylvia-gm-campaign"),
        {"op": "write", "path": "sources/long-name.sylvia-gm-campaign",
         "hex": crafted(sources["plain_exported"], extra_name="blobs/" + "a" * 65).hex()},
        recover("rec-7", source="<root>/sources/long-name.sylvia-gm-campaign"),
        {"op": "write", "path": "sources/manifest-first.sylvia-gm-campaign",
         "hex": crafted(sources["plain_exported"], extra_name="zz.txt").hex()},
        recover("rec-7", source="<root>/sources/manifest-first.sylvia-gm-campaign"),
        # Purging recovered campaigns.
        trash("trash-w", "campaign-workshop", 0), purge("purge-w", "campaign-workshop", 1),
        trash("trash-b", "campaign-b", 0), purge("purge-b", "campaign-b", 1),
        recover("rec-8", "plain"),
    ]
    damaged: list[dict[str, object]] = []
    for index, name in enumerate(name for name in sources if name not in EXPORTED):
        damaged.append(place(f"sources/{name}.sylvia-gm-campaign", name))
        damaged.append(recover(f"damaged-{index}", source=f"<root>/sources/{name}.sylvia-gm-campaign"))
    purged: list[dict[str, object]] = [
        place("exports/story.sylvia-gm-campaign", "story_exported"),
        recover("rec-9", "story", fault="before"),
        recover("rec-9", "story", fault="after"),
        recover("rec-9", "story"),
    ]
    return {"fresh": fresh, "damaged": damaged, "purged": purged}


class Fault(RuntimeError):
    pass


def failure(error: BaseException, root: Path) -> dict[str, object]:
    """The failure, with the storage root written as `<root>` (an `OSError` names its path)."""
    message = str(error)
    spellings = {str(root.resolve()), str(root), root.as_posix()}
    # `str(OSError)` shows the file name's repr, with its backslashes doubled.
    for spelling in sorted(spellings | {repr(text)[1:-1] for text in spellings}, key=len, reverse=True):
        message = message.replace(spelling, "<root>")
    return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": message,
            "supplemental_codes": list(getattr(error, "supplemental_codes", ()))}


def value(result: object) -> dict[str, object]:
    fields = ("campaign_id", "lifecycle", "writer", "resulting_revision", "session_id",
              "captured_revision", "archive_sha256", "byte_count")
    out: dict[str, object] = {}
    for field in fields:
        if hasattr(result, field):
            item = getattr(result, field)
            out[field] = getattr(item, "value", item)
    return out


def run(owner: AuthorityOwner, root: Path, op: dict[str, object], sources: dict[str, bytes]) -> dict[str, object]:
    kind = str(op["op"])
    if kind == "write":
        path = root / str(op["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        data = bytes.fromhex(str(op["hex"])) if "hex" in op else sources[str(op["archive"])] + bytes.fromhex(str(op["suffix"]))
        path.write_bytes(data)
        return {"ok": None}
    if kind == "mkdir":
        (root / str(op["path"])).mkdir(parents=True)
        return {"ok": None}
    params = {key: (item.replace("<root>", root.as_posix()) if isinstance(item, str) else item)
              for key, item in dict(op["params"]).items()}  # type: ignore[arg-type]
    store = owner._store  # noqa: SLF001 - the deterministic seams
    fault = op.get("fault")
    if fault is not None:
        def raise_fault() -> None:
            raise Fault(f"{fault} recovery commit fault")
        setattr(store, f"_{fault}_recovery_commit", raise_fault)
    ctx = types.SimpleNamespace(storage_root=root, application_owner=owner, bus=owner, method=kind)
    try:
        result = getattr(handlers, f"handle_{kind}")(ctx, params)  # type: ignore[arg-type]
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return failure(error, root)
    finally:
        if fault is not None:
            delattr(store, f"_{fault}_recovery_commit")
    return {"ok": value(result)}


def checkpoint(database: Path) -> bytes:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return database.read_bytes()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    sources = archives()
    record: dict[str, object] = {"python": sys.version.split()[0], "roots": {}}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        for name, ops in operations(sources).items():
            root = Path(scratch) / name
            root.mkdir()
            database = root / "authority.sqlite3"
            database.write_bytes(gzip.decompress(BASES[name].read_bytes()))
            runs = []
            with AuthorityOwner.open(database, "recovery-handler") as owner:
                for op in ops:
                    runs.append({**op, "outcome": run(owner, root, op, sources)})
            (OUT / f"{name}.sqlite3.gz").write_bytes(gzip.compress(checkpoint(database), mtime=0))
            record["roots"][name] = runs  # type: ignore[index]
    (OUT / "recovery_runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
