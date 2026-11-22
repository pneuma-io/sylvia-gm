"""Campaign rename, archive, restore and trash: Python's starting database and its outcomes.

Run with the snapshot's own interpreter, from the repository root, after
`crates/sylvia-store/tests/vectors/generate_session_page_runs.py` (its seed is the base):

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_lifecycle_runs.py

It drives `AuthorityOwner.rename_campaign`, `archive_campaign`, `restore_archived_campaign`,
`trash_campaign` and `restore_trashed_campaign` (`_lifecycle_operation`: the identity checks,
the receipt prelookups, the campaign lane and the transition reservation, then the store's
`commit_lifecycle_operation`). The base is the session-page profile (a played DM campaign with a
Builder and a DM session, and a Builder campaign) plus three guided Drafts, whose locks are
unbound: one plain, one with a second top-level Builder session, and one with a Builder child. A
`busy` operation holds campaign work, as a running turn does; a `fault` operation raises from
the store's `_before_lifecycle_commit` or `_after_lifecycle_commit` seam. It writes, under
`lifecycle/`, `seed.sqlite3.gz` (the base), `lifecycle_runs.sqlite3.gz` (the database
afterwards) and `lifecycle_runs.json` (the operations and Python's outcome of each).
`tests/lifecycle.rs` replays them through the Rust store handle and compares the database.
Identities are explicit and the owner's UUIDs are seeded, so a re-run writes byte-identical
files. Reference tooling only.
"""

from __future__ import annotations

import gzip
import json
import random
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "src"))

_IDENTITIES = random.Random(20261123)
uuid.uuid4 = lambda: uuid.UUID(int=_IDENTITIES.getrandbits(128), version=4)  # type: ignore[assignment]

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.domain.authority import WriterRole  # noqa: E402

OUT = HERE / "lifecycle"
BASE = HERE.parents[3] / "crates" / "sylvia-store" / "tests" / "vectors" / "session_pages" / "seed.sqlite3.gz"
STORY, BUILD = "campaign-story", "campaign-build"
PROVIDER, MODEL = "standin", "standin/standin-narrator"


def op(kind: str, operation_id: str, campaign_id: str, revision: int, **values: object) -> dict[str, object]:
    return {"op": kind, "operation_id": operation_id, "campaign_id": campaign_id, "expected_revision": revision,
            **values}


def rename(operation_id: str, campaign_id: str, revision: int, name: str, **extra: object) -> dict[str, object]:
    return op("rename_campaign", operation_id, campaign_id, revision, display_name=name, **extra)


def archive(operation_id: str, campaign_id: str, revision: int, session: str | None = None,
            **extra: object) -> dict[str, object]:
    return op("archive_campaign", operation_id, campaign_id, revision, designated_session_id=session, **extra)


def trash(operation_id: str, campaign_id: str, revision: int, session: str | None = None,
          **extra: object) -> dict[str, object]:
    return op("trash_campaign", operation_id, campaign_id, revision, designated_session_id=session, **extra)


def restore_archived(operation_id: str, campaign_id: str, revision: int, session: str | None = None,
                     **extra: object) -> dict[str, object]:
    return op("restore_archived_campaign", operation_id, campaign_id, revision, session_id=session, **extra)


def restore_trashed(operation_id: str, campaign_id: str, revision: int, **extra: object) -> dict[str, object]:
    return op("restore_trashed_campaign", operation_id, campaign_id, revision, **extra)


OPERATIONS: list[dict[str, object]] = [
    # Identities and the revision are checked before anything is read.
    rename("", STORY, 8, "Story"), rename("life-x", " campaign-story", 8, "Story"),
    rename("life-x", STORY, 8, "  "), rename("life-x", STORY, 8, " Story"),
    archive("life-x", STORY, 8, " dm-1"), rename("life-x", STORY, -1, "Story"),
    # The store refuses a missing campaign and a stale revision.
    rename("life-x", "campaign-missing", 0, "Gone"), rename("life-x", STORY, 7, "Story"),
    # Running campaign work blocks a transition; it is not queued.
    rename("life-x", STORY, 8, "Busy", busy=True),
    # A rename, its exact replay, and replays that differ.
    rename("life-1", STORY, 8, "Story Renamed"), rename("life-1", STORY, 8, "Story Renamed"),
    rename("life-1", STORY, 8, "Other Name"), archive("life-1", STORY, 8, "dm-1"),
    # Archive needs the writer's own session, exactly.
    archive("life-2", STORY, 9), archive("life-2", STORY, 9, "builder-1"), archive("life-2", STORY, 9, "dm-x"),
    archive("life-2", STORY, 9, "dm-1"), archive("life-2", STORY, 9, "dm-1"),
    # An archived campaign cannot be archived again or restored with another session.
    archive("life-3", STORY, 10, "dm-1"), restore_archived("life-3", STORY, 10),
    restore_archived("life-3", STORY, 10, "builder-1"),
    # It can be renamed and trashed; a trashed campaign restores to archived.
    rename("life-4", STORY, 10, "Story Archived"), trash("life-5", STORY, 11),
    trash("life-6", STORY, 12), restore_trashed("life-7", STORY, 12),
    restore_archived("life-8", STORY, 13, "dm-1"),
    # An active campaign cannot be restored; trashing it stops its sessions, and restoring it
    # restores its DM session.
    restore_trashed("life-9", STORY, 14), trash("life-10", STORY, 14, "dm-1"), restore_trashed("life-11", STORY, 15),
    # The commit seams: a fault before the commit leaves nothing; one after it is resolved from
    # the committed receipt.
    rename("life-12", STORY, 16, "Faulted", fault="before"), rename("life-12", STORY, 16, "Committed", fault="after"),
    rename("life-12", STORY, 16, "Committed"),
    # The Builder campaign, with its Builder session.
    archive("life-13", BUILD, 0, "builder-2"), restore_archived("life-14", BUILD, 1, "builder-2"),
    trash("life-15", BUILD, 2, "builder-2"), restore_trashed("life-16", BUILD, 3),
    # The guided Draft, whose lock is unbound.
    rename("life-17", "{draft}", 0, "Draft Renamed"), archive("life-18", "{draft}", 1, "session-draft"),
    restore_archived("life-19", "{draft}", 2, "session-draft"), trash("life-20", "{draft}", 3, "session-draft"),
    restore_trashed("life-21", "{draft}", 4),
    # Two top-level sessions in the writer's role, and active child work, block archive and trash.
    archive("life-22", "{crowded}", 0, "session-crowded"), trash("life-22", "{crowded}", 0, "session-crowded"),
    archive("life-23", "{helped}", 0, "session-helped"), trash("life-23", "{helped}", 0, "session-helped"),
    rename("life-24", "{helped}", 0, "Helped Renamed"),
]


class Fault(RuntimeError):
    pass


def run(owner: AuthorityOwner, op: dict[str, object]) -> dict[str, object]:
    kind = str(op["op"])
    values = {key: value for key, value in op.items() if key not in {"op", "busy", "fault"}}
    store = owner._store  # noqa: SLF001 - the deterministic seams
    fault = op.get("fault")
    if fault is not None:
        def raise_fault() -> None:
            raise Fault(f"{fault} lifecycle commit fault")
        setattr(store, f"_{fault}_lifecycle_commit", raise_fault)
    if op.get("busy"):
        owner._begin_campaign_work(str(op["campaign_id"]))  # noqa: SLF001
    try:
        result = getattr(owner, kind)(**values)
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    finally:
        if op.get("busy"):
            owner._finish_campaign_work(str(op["campaign_id"]))  # noqa: SLF001
        if fault is not None:
            delattr(store, f"_{fault}_lifecycle_commit")
    return {"ok": {"campaign_id": result.campaign_id, "lifecycle": result.lifecycle.value,
                   "writer": result.writer.value, "resulting_revision": result.resulting_revision,
                   "session_id": result.session_id}}


def checkpoint(database: Path) -> bytes:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return database.read_bytes()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(dir=HERE, ignore_cleanup_errors=True) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        database.write_bytes(gzip.decompress(BASE.read_bytes()))
        drafts: dict[str, str] = {}
        with AuthorityOwner.open(database, "lifecycle-runs") as owner:
            for name, label in (("draft", "Draft"), ("crowded", "Crowded"), ("helped", "Helped")):
                drafts[name] = owner.create_guided_campaign_draft(
                    operation_id=f"{name}-1", display_name=label,
                    builder_session_id="session-draft" if name == "draft" else f"session-{name}",
                    builder_provider_id=PROVIDER, builder_model_id=MODEL).campaign_id
            owner.create_agent_session(campaign_id=drafts["crowded"], role=WriterRole.BUILDER, provider_id=PROVIDER,
                                       model_id=MODEL, session_id="session-crowded-2",
                                       selected_skill="BUILD CAMPAIGN PLAN")
            owner.create_builder_child_session(parent_session_id="session-helped", provider_id=PROVIDER,
                                               model_id=MODEL, selected_skill="BUILD CAMPAIGN PLAN",
                                               scope=("campaign-plan.md",), session_id="child-helped")
        (OUT / "seed.sqlite3.gz").write_bytes(gzip.compress(checkpoint(database), mtime=0))
        operations = [{key: drafts.get(str(value)[1:-1], value) if str(value).startswith("{") else value
                       for key, value in item.items()} for item in OPERATIONS]
        outcomes = []
        with AuthorityOwner.open(database, "lifecycle-runs") as owner:
            for item in operations:
                outcomes.append(run(owner, item))
        (OUT / "lifecycle_runs.sqlite3.gz").write_bytes(gzip.compress(checkpoint(database), mtime=0))
    record = {"python": sys.version.split()[0], "drafts": drafts,
              "runs": [{**item, "outcome": outcome} for item, outcome in zip(operations, outcomes, strict=True)]}
    (OUT / "lifecycle_runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
