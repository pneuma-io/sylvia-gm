"""Paired runs for campaign continuity, part 1: Python's starting database and its outcomes.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_continuity_runs.py

It writes, under `continuity/`, `seed.sqlite3.gz` (the Python owner's profile with one small
synthetic package, a DM campaign with an active DM session and its TURN open, and a
Builder-writer campaign), `<run>.sqlite3.gz` (the database after each run) and
`continuity_runs.json` (each run's operations and Python's outcome of each).
`tests/continuity.rs` replays them on the Rust store. Identities are explicit, so a re-run
writes byte-identical files. Reference tooling only.
"""

from __future__ import annotations

import gzip
import io
import json
import random
import shutil
import sys
import tempfile
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_IDENTITIES = random.Random(20261123)
uuid.uuid4 = lambda: uuid.UUID(int=_IDENTITIES.getrandbits(128), version=4)  # type: ignore[assignment]

from generate_action_runs import synthetic_archive  # noqa: E402

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate  # noqa: E402
from sylvia_gm.domain.authority import CampaignContinuityCommand, PackageLock, WriterRole  # noqa: E402
from sylvia_gm.infrastructure import sqlite_authority as a  # noqa: E402
from sylvia_gm.infrastructure import sqlite_continuity  # noqa: E402

OUT = Path(__file__).resolve().parent / "continuity"
DM = "campaign-continuity"
BUILDER = "campaign-builder"


def seed(folder: Path) -> Path:
    database = folder / "authority.sqlite3"
    with AuthorityOwner.open(database, "continuity-runs") as owner:
        owner.prepare_new_campaign_storage()
        candidate = owner.install_package_archive(
            io.BytesIO(synthetic_archive()), install_id="install-continuity-runs"
        )
        owner.create_campaign(CampaignCreate(
            display_name="Continuity runs", writer=WriterRole.DM,
            package_lock=candidate.package_lock, turn_id="turn-1", campaign_id=DM,
        ))
        owner.create_agent_session(
            campaign_id=DM, role=WriterRole.DM, provider_id="synthetic.provider",
            model_id="synthetic.provider/model", session_id="dm-1",
        )
        owner.create_campaign(CampaignCreate(
            display_name="Builder runs", writer=WriterRole.BUILDER,
            package_lock=PackageLock(entries=()), turn_id="turn-builder", campaign_id=BUILDER,
        ))
    return database


def command(**fields: object) -> dict[str, object]:
    base: dict[str, object] = {
        "campaign_id": DM, "operation_id": "op-scene-1", "session_id": "dm-1",
        "expected_revision": 0, "kind": "scene", "record_id": "current_scene",
        "status": "active", "payload": {"act_id": "act-1", "summary": "Arrival."},
        "basis_message_ids": ["m-1"], "turn_id": "turn-1",
    }
    base.update(fields)
    return base


def batch(*commands: dict[str, object], fault: bool = False) -> dict[str, object]:
    return {"op": "commit_campaign_continuity_batch", "commands": list(commands), "fault": fault}


VIEW = {"op": "campaign_continuity_view", "campaign_id": DM}

RUNS: dict[str, list[dict[str, object]]] = {
    "commit_and_replay": [
        batch(command()),
        batch(command()),
        batch(command(payload={"act_id": "act-1", "summary": "Different."})),
        batch(command(operation_id="op-scene-2", expected_revision=1, payload={"act_id": "act-2"})),
        VIEW,
    ],
    "batch_and_view": [
        batch(
            command(operation_id="op-fact-b", kind="fact", record_id="fact-b",
                    payload={"text": "The bridge is out.", "visibility": "player_known"}),
            command(operation_id="op-fact-a", expected_revision=1, kind="fact", record_id="fact-a",
                    payload={"text": "A storm is coming.", "record_id": "kept-by-merge"}),
            command(operation_id="op-entity", expected_revision=2, kind="entity",
                    record_id="npc-stern", payload={"name": "Stern"}),
            command(operation_id="op-progress", expected_revision=3, kind="plan_progress",
                    record_id="act-1", payload={"status": "resolved", "record_id": "payload-wins"}),
            command(operation_id="op-reveal-1", expected_revision=4, kind="reveal",
                    record_id="secret-1", payload={"audience": ["player", "dm", "player"]}),
            command(operation_id="op-reveal-2", expected_revision=5, kind="reveal",
                    record_id="secret-2", payload={}),
            command(operation_id="op-reveal-3", expected_revision=6, kind="reveal",
                    record_id="secret-3", payload={"audience": "player"}),
        ),
        batch(command(operation_id="op-fact-b-retired", expected_revision=7, kind="fact",
                      record_id="fact-b", status="retired", payload={})),
        VIEW,
    ],
    "refusals": [
        batch(),
        batch(*[command(operation_id=f"op-{n}", expected_revision=n) for n in range(65)]),
        batch(command(kind="memory")),
        batch(command(status="deleted")),
        batch(command(payload={"reasoning_private": "x"})),
        batch(command(expected_revision=-1)),
        batch(command(), command(operation_id="op-2", expected_revision=5)),
        batch(command(), command(operation_id="op-scene-1", expected_revision=1)),
        batch(command(record_id=" ")),
        batch(command(basis_message_ids=[""])),
        batch(command(expected_revision=3)),
        batch(command(session_id="dm-missing")),
        batch(command(turn_id="turn-missing")),
        batch(command(campaign_id="campaign-missing")),
        batch(command(campaign_id=BUILDER, turn_id="turn-builder")),
        batch(command()),
        batch(command(), command(operation_id="op-new", expected_revision=1)),
        VIEW,
    ],
    "interrupted_before_commit": [
        batch(command(), fault=True),
        VIEW,
        batch(command()),
        VIEW,
    ],
}


class _Fault(Exception):
    """Stands in for a crash between the writes and the commit."""


def plain(value: object) -> object:
    return json.loads(a._dump(value))


def receipt_json(receipt: object) -> dict[str, object]:
    return {key: getattr(receipt, key) for key in
            ("campaign_id", "operation_id", "record_id", "kind", "resulting_revision")}


def view_json(view: object) -> dict[str, object]:
    return {
        "current_scene": None if view.current_scene is None else plain(view.current_scene),  # type: ignore[attr-defined]
        "facts": [plain(fact) for fact in view.facts],  # type: ignore[attr-defined]
        "reveals": plain(view.reveals),  # type: ignore[attr-defined]
        "plan_progress": [plain(item) for item in view.plan_progress],  # type: ignore[attr-defined]
        "records": [
            {"entry_id": r.entry_id, "record_id": r.record_id, "kind": r.kind, "status": r.status,
             "payload": plain(r.payload), "basis_message_ids": list(r.basis_message_ids),
             "admitted_revision": r.admitted_revision, "operation_id": r.operation_id}
            for r in view.records  # type: ignore[attr-defined]
        ],
    }


def run(store: a.SQLiteAuthorityStore, op: dict[str, object]) -> dict[str, object]:
    try:
        if op["op"] == "campaign_continuity_view":
            connection = store._connect()
            try:
                value: object = view_json(sqlite_continuity.read_view(connection, str(op["campaign_id"])))
            finally:
                connection.close()
        else:
            commands = [
                CampaignContinuityCommand(**{**item, "basis_message_ids": tuple(item["basis_message_ids"])})  # type: ignore[arg-type, dict-item]
                for item in op["commands"]  # type: ignore[union-attr]
            ]
            if op.get("fault"):
                def fail() -> None:
                    raise _Fault("fault before commit")
                store._before_commit = fail  # type: ignore[method-assign]
            try:
                value = [receipt_json(item) for item in store.commit_campaign_continuity_batch(commands)]
            finally:
                store.__dict__.pop("_before_commit", None)
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": value}


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    record: dict[str, object] = {"python": sys.version.split()[0], "runs": {}}
    with tempfile.TemporaryDirectory(dir=OUT.parent) as scratch:
        database = seed(Path(scratch) / "seed")
        checkpoint = a.sqlite3.connect(database)
        checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        checkpoint.close()
        seed_bytes = database.read_bytes()
        (OUT / "seed.sqlite3.gz").write_bytes(gzip.compress(seed_bytes, mtime=0))
        for name, operations in RUNS.items():
            folder = Path(scratch) / name
            folder.mkdir()
            (folder / "authority.sqlite3").write_bytes(seed_bytes)
            store = a._open_for_application_owner(folder / "authority.sqlite3")
            try:
                outcomes = [run(store, op) for op in operations]
            finally:
                store.close()
            checkpoint = a.sqlite3.connect(folder / "authority.sqlite3")
            checkpoint.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            checkpoint.close()
            (OUT / f"{name}.sqlite3.gz").write_bytes(
                gzip.compress((folder / "authority.sqlite3").read_bytes(), mtime=0)
            )
            record["runs"][name] = {"operations": operations, "outcomes": outcomes}  # type: ignore[index]
    text = json.dumps(record, indent=1, ensure_ascii=True) + "\n"
    (OUT / "continuity_runs.json").write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    main()
