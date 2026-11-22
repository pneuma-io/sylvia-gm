"""Campaign purge through Python's owner handler, and launch-time purge reconciliation.

Run with the snapshot's own interpreter, from the repository root, after
`crates/sylvia-store/tests/vectors/generate_session_page_runs.py` (its seed is the base):

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_purge_runs.py

Two records, under `purge/`:

- `purge_runs.json`, with `seed.sqlite3.gz` and `purge_runs.sqlite3.gz`. The base is the
  session-page profile (a played DM campaign with a Builder and a DM session, and a Builder
  campaign). Python's `handle_purge_campaign` runs as the owner runs it, with
  `AuthorityOwner.purge_campaign` and the Builder sources' quarantine, restore and finalize. Its
  refusals (identities, confirmation, a campaign not yet trashed, a stale revision), a fault
  before the commit (the sources come back), a fault after it (the receipt resolves the outcome
  and the sources are deleted), a replay, and a purge of a purged campaign. Trashing goes through
  `trash_campaign`. After every operation the run records the campaign workspace tree.
- `reconcile_runs.json`: storage roots on the seed or the purge run's final database with purge journals
  left on disk, and what `reconcile_builder_source_purges` makes of each, called as the owner's
  launch calls it (the live campaigns from `campaign_index`, receipts from
  `campaign_purge_receipt_exists`). A committed purge's journal is deleted, a live campaign's is
  restored, an unknown one is refused (launch fails), and an abandoned temporary journal is
  removed.

The Rust owner replays the first through its dispatch (`commands/campaigns.rs`) and launches on
each storage root of the second (`tests/purge_reconcile.rs`). Reference tooling only.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import random
import shutil
import sqlite3
import sys
import tempfile
import types
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "src"))

_IDENTITIES = random.Random(20261124)
uuid.uuid4 = lambda: uuid.UUID(int=_IDENTITIES.getrandbits(128), version=4)  # type: ignore[assignment]

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.infrastructure.builder_source_ingestion import (  # noqa: E402
    PURGE_PENDING_DIRECTORY,
    SOURCE_DIRECTORY,
    reconcile_builder_source_purges,
)
from sylvia_gm.interfaces.commands import campaigns as handlers  # noqa: E402

OUT = HERE / "purge"
BASE = HERE.parents[3] / "crates" / "sylvia-store" / "tests" / "vectors" / "session_pages" / "seed.sqlite3.gz"
STORY, BUILD = "campaign-story", "campaign-build"


def purge(operation_id: str, campaign_id: str, revision: object, confirmation: str | None = None,
          fault: str | None = None) -> dict[str, object]:
    params: dict[str, object] = {"operation_id": operation_id, "campaign_id": campaign_id,
                                 "expected_revision": revision,
                                 "confirmation": f"permanently purge {campaign_id}" if confirmation is None
                                 else confirmation}
    return {"op": "purge_campaign", "params": params, **({"fault": fault} if fault else {})}


def trash(operation_id: str, campaign_id: str, revision: int, session: str) -> dict[str, object]:
    return {"op": "trash_campaign", "params": {"operation_id": operation_id, "campaign_id": campaign_id,
                                               "expected_revision": revision, "designated_session_id": session}}


def write(path: str, text: str) -> dict[str, object]:
    return {"op": "write", "path": path, "text": text}


SOURCES = f"campaigns/{STORY}/{SOURCE_DIRECTORY}"
OPERATIONS: list[dict[str, object]] = [
    # The handler's own parameter checks, then the owner's identity and confirmation checks.
    purge("purge-1", STORY, -1), purge(" purge-1", STORY, 8), purge("purge-1", STORY, 8, confirmation="purge it"),
    purge("purge-1", STORY, 8, confirmation=f"permanently purge {BUILD}"),
    # An active campaign is refused by the store's preflight, before any source moves.
    write(f"{SOURCES}/notes.md", "# Notes\n"), write(f"{SOURCES}/maps/coast.txt", "coast"),
    purge("purge-1", STORY, 8),
    trash("trash-1", STORY, 8, "dm-1"),
    purge("purge-1", STORY, 8),
    # A fault before the commit: the sources are restored and nothing is kept.
    purge("purge-1", STORY, 9, fault="before"),
    # A fault after the commit: the receipt resolves it and the sources are deleted.
    purge("purge-1", STORY, 9, fault="after"),
    # The exact replay, and a new purge of the purged campaign.
    purge("purge-1", STORY, 9), purge("purge-2", STORY, 10),
    # The Builder campaign, trashed and purged with no sources at all.
    trash("trash-2", BUILD, 0, "builder-2"), purge("purge-3", BUILD, 1),
]


def tree(root: Path) -> list[list[object]]:
    """Every entry under `campaigns/`, relative, with a file's SHA-256."""
    campaigns = root / "campaigns"
    if not campaigns.exists():
        return []
    entries: list[list[object]] = []
    for path in sorted(campaigns.rglob("*"), key=lambda item: item.relative_to(root).as_posix()):
        relative = path.relative_to(root).as_posix()
        if path.is_dir():
            entries.append([relative, "dir", None])
        else:
            entries.append([relative, "file", hashlib.sha256(path.read_bytes()).hexdigest()])
    return entries


def failure(error: BaseException) -> dict[str, object]:
    return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}


class Fault(RuntimeError):
    pass


def run(owner: AuthorityOwner, root: Path, op: dict[str, object]) -> dict[str, object]:
    kind = str(op["op"])
    if kind == "write":
        path = root / str(op["path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(str(op["text"]).encode("utf-8"))
        return {"ok": None}
    params = op["params"]
    store = owner._store  # noqa: SLF001 - the deterministic seams
    fault = op.get("fault")
    if fault is not None:
        def raise_fault() -> None:
            raise Fault(f"{fault} lifecycle commit fault")
        setattr(store, f"_{fault}_lifecycle_commit", raise_fault)
    ctx = types.SimpleNamespace(storage_root=root, application_owner=owner, bus=owner)
    try:
        if kind == "purge_campaign":
            result = handlers.handle_purge_campaign(ctx, params)  # type: ignore[arg-type]
            value = {"campaign_id": result.campaign_id, "resulting_revision": result.resulting_revision}
        else:
            result = handlers.handle_trash_campaign(ctx, params)  # type: ignore[arg-type]
            value = {"campaign_id": result.campaign_id, "lifecycle": result.lifecycle.value,
                     "writer": result.writer.value, "resulting_revision": result.resulting_revision,
                     "session_id": result.session_id}
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return failure(error)
    finally:
        if fault is not None:
            delattr(store, f"_{fault}_lifecycle_commit")
    return {"ok": value}


def checkpoint(database: Path) -> bytes:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return database.read_bytes()


def journal(root: Path, campaign_id: str, operation_id: str, *, sources: dict[str, str],
            marker_campaign: str | None = None) -> None:
    """A purge journal as `stage_builder_sources_for_purge` leaves it after moving the sources."""
    name = "purge-" + hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
    operation = root / "campaigns" / campaign_id / PURGE_PENDING_DIRECTORY / name
    operation.mkdir(parents=True)
    marker = {"format": "sylvia-gm-builder-source-purge", "version": 1,
              "campaign_id": marker_campaign or campaign_id, "operation_id": operation_id}
    (operation / "purge.json").write_bytes(json.dumps(marker, sort_keys=True, separators=(",", ":")).encode())
    for relative, text in sources.items():
        path = operation / "sources" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))


def reconcile_states(scratch: Path, bases: dict[str, bytes]) -> list[dict[str, object]]:
    """Storage roots with journals on disk, and Python's launch-time reconciliation of each."""
    # (name, database, journals to leave, other files). `seed` has both campaigns live; `purged`
    # is the purge run's final database: the story campaign purged by `purge-1`, the Builder
    # campaign by `purge-3`.
    states: list[tuple[str, str, list[dict[str, object]], list[str]]] = [
        ("committed", "purged", [{"campaign": STORY, "operation": "purge-1", "sources": {"notes.md": "# Notes\n"}}], []),
        ("live", "seed", [{"campaign": STORY, "operation": "purge-9", "sources": {"a.txt": "a", "maps/b.txt": "b"}}], []),
        ("abandoned_temporary", "seed", [],
         [f"campaigns/{STORY}/{PURGE_PENDING_DIRECTORY}/.purge-0123456789abcdef01234567-x.tmp/purge.json"]),
        ("wrong_campaign", "purged", [{"campaign": BUILD, "operation": "purge-3", "sources": {}, "marker": STORY}], []),
        ("no_receipt_not_live", "purged", [{"campaign": STORY, "operation": "purge-7", "sources": {"b.txt": "b"}}], []),
        ("nothing_pending", "seed", [], [f"campaigns/{STORY}/{SOURCE_DIRECTORY}/notes.md"]),
    ]
    records = []
    for name, base, journals, files in states:
        root = scratch / f"reconcile-{name}"
        root.mkdir()
        database = root / "authority.sqlite3"
        database.write_bytes(bases[base])
        for item in journals:
            journal(root, str(item["campaign"]), str(item["operation"]), sources=dict(item["sources"]),  # type: ignore[arg-type]
                    marker_campaign=item.get("marker"))  # type: ignore[arg-type]
        for relative in files:
            path = root / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b"{}")
        before = tree(root)
        with AuthorityOwner.open(database, "purge-reconcile") as owner:
            live = {entry.campaign_id for entry in owner.campaign_index()}
            try:
                reconcile_builder_source_purges(root, live, owner.campaign_purge_receipt_exists)
                outcome: dict[str, object] = {"ok": None}
            except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
                outcome = failure(error)
        records.append({"name": name, "base": base, "journals": journals, "files": files, "before": before, "outcome": outcome, "after": tree(root)})
    return records


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    with tempfile.TemporaryDirectory(dir=HERE, ignore_cleanup_errors=True) as scratch_name:
        scratch = Path(scratch_name)
        root = scratch / "purge"
        root.mkdir()
        database = root / "authority.sqlite3"
        database.write_bytes(gzip.decompress(BASE.read_bytes()))
        seed = checkpoint(database)
        (OUT / "seed.sqlite3.gz").write_bytes(gzip.compress(seed, mtime=0))
        runs = []
        with AuthorityOwner.open(database, "purge-runs") as owner:
            for op in OPERATIONS:
                outcome = run(owner, root, op)
                runs.append({**op, "outcome": outcome, "tree": tree(root)})
        purged = checkpoint(database)
        (OUT / "purge_runs.sqlite3.gz").write_bytes(gzip.compress(purged, mtime=0))
        reconciles = reconcile_states(scratch, {"seed": seed, "purged": purged})
    python = sys.version.split()[0]
    for name, record in (("purge_runs", {"python": python, "runs": runs}),
                         ("reconcile_runs", {"python": python, "states": reconciles})):
        (OUT / f"{name}.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
