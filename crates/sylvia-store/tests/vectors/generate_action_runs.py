"""Paired runs for the turns-and-actions family: Python's starting database and its outcomes.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_action_runs.py

It writes, under `actions/`:

- `seed.sqlite3.gz`: a database the Python owner made: a fresh profile, one small synthetic
  package installed, and one active DM campaign on that package lock with its TURN open;
- `<run>.sqlite3.gz`: the database after Python's store ran each operation of the run;
- `action_runs.json`: every run's operations (exact arguments) and Python's outcome of each:
  the receipt, the boolean or the exception class and message.

`tests/actions.rs` starts from the same seed, runs the same operations on the Rust store, and
must give the same outcomes and a database that dumps the same as Python's (one dumper,
`sylvia_compare::dbdump`). Reference tooling only.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import shutil
import sys
import tempfile
import zipfile
from collections.abc import Mapping, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate  # noqa: E402
from sylvia_gm.domain.authority import (  # noqa: E402
    ActionStatus,
    MechanicOutcome,
    PackageRole,
    StateMutation,
    WriterRole,
)
from sylvia_gm.domain.package_library import (  # noqa: E402
    canonical_json_bytes,
    content_root_for_record,
)
from sylvia_gm.infrastructure import sqlite_authority as a  # noqa: E402

OUT = Path(__file__).resolve().parent / "actions"
CAMPAIGN = "campaign-actions"
TURN = "turn-actions-1"


def synthetic_archive() -> bytes:
    """One small primary package, shaped like the F2 event-protocol tests' fixture."""
    payload = canonical_json_bytes({"fixture": "lane-3-action-runs"})
    draft: dict[str, object] = {
        "id": "synthetic.actions",
        "version": "1.0.0",
        "role": "primary",
        "metadata": {"label": "Synthetic action runs"},
        "provenance": {
            "publisher": "sylvia-gm-rust-port",
            "source_sha256": hashlib.sha256(payload).hexdigest(),
        },
        "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
        "minimum_model_capability_tier": "sylvia.capability.t00",
        "dependencies": [],
        "capabilities": ["synthetic.checks"],
        "tools": [
            {
                "name": "roll_check",
                "capability": "synthetic.checks",
                "input_schema": {"type": "object"},
                "state_predicates": [],
                "turn_statuses": ["open"],
                "writers": ["dm"],
            }
        ],
        "prompt_fragments": [],
        "ui_surfaces": [],
        "files": [
            {
                "path": "payload/actions.json",
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
    }
    root = content_root_for_record(draft)
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            canonical_json_bytes({"format": 1, "packages": [{**draft, "content_sha256": root}]}),
        )
        archive.writestr("packages/synthetic.actions/payload/actions.json", payload)
    return stream.getvalue()


def seed(folder: Path) -> tuple[Path, dict[str, object]]:
    database = folder / "authority.sqlite3"
    with AuthorityOwner.open(database, "action-runs") as owner:
        owner.prepare_new_campaign_storage()
        candidate = owner.install_package_archive(
            io.BytesIO(synthetic_archive()), install_id="install-action-runs"
        )
        record = owner.create_campaign(
            CampaignCreate(
                display_name="Action runs",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id=TURN,
                campaign_id=CAMPAIGN,
            )
        )
        primary = next(
            entry for entry in candidate.package_lock.entries if entry.role is PackageRole.PRIMARY
        )
        other = next(
            (entry for entry in candidate.package_lock.entries if entry is not primary), None
        )
    facts = {
        "campaign_id": CAMPAIGN,
        "turn_id": TURN,
        "revision": record.revision,
        "package_id": primary.package_id,
        "package_version": primary.version,
        "package_sha256": primary.sha256,
        "other_package_id": None if other is None else other.package_id,
    }
    return database, facts


def action(facts: dict[str, object], **changes: object) -> dict[str, object]:
    """One `commit_action` call as JSON: Python's keyword arguments, outcome flattened."""
    call: dict[str, object] = {
        "campaign_id": facts["campaign_id"],
        "action_id": "action-1",
        "idempotency_key": "key-1",
        "payload_hash": "a" * 64,
        "expected_revision": facts["revision"],
        "writer": "dm",
        "turn_id": facts["turn_id"],
        "package_id": facts["package_id"],
        "package_version": facts["package_version"],
        "package_sha256": facts["package_sha256"],
        "tool_name": "roll_check",
        "parameters": {"ability": "dexterity", "dc": 12},
        "result": {"total": 15, "success": True},
        "mutations": [[f"{facts['package_id']}.checks", "last", {"total": 15}]],
        "consequences": [{"kind": "check_rolled", "total": 15}],
        "status": "committed",
    }
    call.update(changes)
    return {"op": "commit_action", **call}


def runs(facts: dict[str, object]) -> dict[str, list[dict[str, object]]]:
    revision = int(facts["revision"])  # type: ignore[arg-type]
    pkg = str(facts["package_id"])
    other = facts["other_package_id"]
    committed = action(facts)
    return {
        "commit_then_replay": [
            committed,
            committed,  # duplicate: same key, same payload -> the stored receipt
            action(facts, payload_hash="b" * 64),  # conflicting payload for the key
            {"op": "action_receipt", "campaign_id": CAMPAIGN, "action_id": "action-1"},
            {"op": "idempotency_hit", "campaign_id": CAMPAIGN, "key": "key-1"},
            {"op": "action_hit", "campaign_id": CAMPAIGN, "action_id": "missing"},
        ],
        "stale_and_wrong_writer": [
            committed,
            action(facts, action_id="action-2", idempotency_key="key-2"),  # stale revision
            action(
                facts, action_id="action-2", idempotency_key="key-2",
                expected_revision=revision + 1, writer="builder",
            ),
            action(
                facts, action_id="action-2", idempotency_key="key-2",
                expected_revision=revision + 1, mutations=[[f"{pkg}.checks", "last", 20]],
                result={"total": 20}, consequences=[],
            ),
        ],
        "failed_action": [
            action(facts, status="failed", mutations=[], consequences=[], result={"error": "x"}),
        ],
        "foreign_namespace": [
            action(facts, mutations=[["unowned.namespace", "k", 1]]),
            *([action(facts, mutations=[[f"{other}.state", "k", 1]])] if other else []),
        ],
        "unsafe_payload": [
            action(facts, parameters={"reasoning_private": "x"}),
            action(facts, result={"scratchpad": "x"}),
        ],
        "turn_closed_then_reopened": [
            {"op": "complete_turn", "campaign_id": CAMPAIGN, "turn_id": TURN},
            action(facts),
            {"op": "complete_turn", "campaign_id": CAMPAIGN, "turn_id": TURN},
            {"op": "open_turn", "campaign_id": CAMPAIGN, "turn_id": TURN},
            {"op": "open_turn", "campaign_id": CAMPAIGN, "turn_id": "turn-actions-2"},
            {"op": "open_turn", "campaign_id": CAMPAIGN, "turn_id": "turn-actions-3"},
            {"op": "current_open_turn", "campaign_id": CAMPAIGN},
            action(facts, turn_id="turn-actions-2"),
            {"op": "turn_is_completed", "campaign_id": CAMPAIGN, "turn_id": TURN},
            {"op": "turn_is_completed", "campaign_id": CAMPAIGN, "turn_id": "missing"},
        ],
        "interrupted_before_commit": [
            {**committed, "fault": "before_commit"},
            {"op": "action_hit", "campaign_id": CAMPAIGN, "action_id": "action-1"},
            committed,
        ],
        "unknown_campaign": [
            action(facts, campaign_id="campaign-missing"),
            {"op": "open_turn", "campaign_id": "campaign-missing", "turn_id": "t"},
        ],
    }


class _Fault(Exception):
    """Stands in for a crash between the writes and the commit."""


def ordered(value: object) -> object:
    """A frozen payload as plain JSON, keeping every mapping's key order."""
    if isinstance(value, Mapping):
        return {str(key): ordered(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [ordered(item) for item in value]
    return value


def receipt(value: object) -> object:
    if value is None or isinstance(value, (bool, str)):
        return value
    if isinstance(value, a.IdempotencyHit):
        return {"payload_hash": value.payload_hash, "receipt": receipt(value.receipt)}
    return {
        "campaign_id": value.campaign_id,  # type: ignore[attr-defined]
        "action_id": value.action_id,  # type: ignore[attr-defined]
        "status": value.status.value,  # type: ignore[attr-defined]
        # The receipt's own key order: a fresh commit keeps the outcome's, a replay the stored
        # canonical text's.
        "result": ordered(value.result),  # type: ignore[attr-defined]
        "resulting_revision": value.resulting_revision,  # type: ignore[attr-defined]
    }


def run(store: a.SQLiteAuthorityStore, op: dict[str, object]) -> dict[str, object]:
    kind = op["op"]
    args = {key: value for key, value in op.items() if key not in {"op", "fault"}}
    try:
        if kind == "commit_action":
            outcome = MechanicOutcome(
                result=args.pop("result"),  # type: ignore[arg-type]
                mutations=tuple(StateMutation(*item) for item in args.pop("mutations")),  # type: ignore[union-attr]
                consequences=tuple(args.pop("consequences")),  # type: ignore[arg-type]
            )
            args["writer"] = WriterRole(args["writer"])
            args["status"] = ActionStatus(args["status"])
            if op.get("fault") == "before_commit":
                def fail() -> None:
                    raise _Fault("fault before commit")
                store._before_commit = fail  # type: ignore[method-assign]
            try:
                value = store.commit_action(outcome=outcome, **args)  # type: ignore[arg-type]
            finally:
                store.__dict__.pop("_before_commit", None)
        else:
            value = getattr(store, str(kind))(**args)
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {
            "error": type(error).__name__,
            "code": getattr(error, "code", None),
            "message": str(error),
        }
    return {"ok": receipt(value)}


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    record: dict[str, object] = {"python": sys.version.split()[0], "runs": {}}
    with tempfile.TemporaryDirectory(dir=OUT.parent) as scratch:
        database, facts = seed(Path(scratch) / "seed")
        seed_bytes = database.read_bytes()
        (OUT / "seed.sqlite3.gz").write_bytes(gzip.compress(seed_bytes, mtime=0))
        record["facts"] = facts
        for name, operations in runs(facts).items():
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
            data = (folder / "authority.sqlite3").read_bytes()
            (OUT / f"{name}.sqlite3.gz").write_bytes(gzip.compress(data, mtime=0))
            record["runs"][name] = {"operations": operations, "outcomes": outcomes}  # type: ignore[index]
    text = json.dumps(record, indent=1, ensure_ascii=True) + "\n"
    (OUT / "action_runs.json").write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    main()
