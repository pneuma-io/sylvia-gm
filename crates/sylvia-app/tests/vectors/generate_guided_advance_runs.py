"""Guided Campaign Interview answers: Python's `AuthorityOwner.advance_guided_campaign_draft`.

Run with the snapshot's own interpreter, from the repository root, after
`generate_guided_draft_runs.py` (its new profile is the base):

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_guided_advance_runs.py

It makes one Draft, then answers its Interview: a No Ruleset interview through to the review,
exact and loose replays, stale revisions, every refusal, a return to an earlier step that
drops the later answers, a missing campaign and a wrong Builder session. Steps that need an
Interview Contribution are marked `not_ported`: the Rust store refuses them until
`_guided_contribution_object` is ported. Python runs them on a copy of the profile taken at that
point, so its outcome is recorded without changing the database the other steps share. It writes
`guided/advance_runs.json` and `guided/advance_runs.sqlite3.gz` (the database afterwards);
`tests/guided.rs` replays them. Identities are explicit and the owner's UUIDs are seeded, so a
re-run writes byte-identical files. Reference tooling only.

The four guided generators share `guided/` and build on this one's seed. Run them in order with
`generate_guided_all.py` (draft, advance, start, finalize).
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

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))

_IDENTITIES = random.Random(20261125)
uuid.uuid4 = lambda: uuid.UUID(int=_IDENTITIES.getrandbits(128), version=4)  # type: ignore[assignment]

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402

OUT = Path(__file__).resolve().parent / "guided"
SEED = OUT / "seed.sqlite3.gz"
SESSION = "session-guided-1"
NO_RULESET = {"candidate_id": None}


def answer(operation_id: str, step: str, revision: int, visible: object = "An answer", value: object = None,
           session_id: str = SESSION, campaign_id: str | None = None, not_ported: bool = False) -> dict[str, object]:
    return {"op": "advance_guided_campaign_draft", "operation_id": operation_id, "campaign_id": campaign_id,
            "builder_session_id": session_id, "expected_construction_revision": revision, "current_step": step,
            "visible_answer": visible, "answer": {} if value is None else value, "not_ported": not_ported}


OPERATIONS: list[dict[str, object]] = [
    {"op": "create_guided_campaign_draft", "operation_id": "storm", "display_name": "Storm Courier",
     "builder_session_id": SESSION, "builder_provider_id": "standin", "builder_model_id": "standin/standin-narrator"},
    # Refusals of the answer itself.
    answer("a-blank", "ruleset", 0, visible="   ", value=NO_RULESET),
    answer("a-long", "ruleset", 0, visible={"repeat": "x", "count": 131_073}, value=NO_RULESET),
    answer("a-review", "review", 0), answer("a-unknown", "epilogue", 0), answer("a-negative", "ruleset", -1),
    answer(" a-padded", "ruleset", 0, value=NO_RULESET), answer("a-list", "ruleset", 0, value=["x"]),
    answer("a-session", "ruleset", 0, value=NO_RULESET, session_id="session-missing"),
    # No Ruleset: the content steps are skipped.
    answer("a-ruleset", "ruleset", 0, visible="No Ruleset", value=NO_RULESET),
    answer("a-ruleset", "ruleset", 0, visible="No Ruleset", value=NO_RULESET),
    answer("a-ruleset", "ruleset", 0, visible="A different visible answer", value={"candidate_id": "x"}),
    answer("a-stale", "name", 0, visible="Storm Courier"),
    answer("a-name", "name", 1, visible="Storm Courier", value={"display_name": "Storm Courier"}),
    # Content and Contribution steps need `_guided_contribution_object`.
    answer("a-plan", "plan", 2, value={"plan_id": None}, not_ported=True),
    answer("a-contribution", "ruleset_contribution", 2, not_ported=True),
    answer("a-boundaries", "boundaries_preferences", 2, visible="No graphic violence",
           value={"boundaries": ["graphic violence"], "preferences": []}),
    # Back to the first question: the later answers are dropped.
    answer("a-ruleset-again", "ruleset", 3, visible="No Ruleset", value=NO_RULESET),
    answer("a-name-again", "name", 4, visible="Storm Courier", value={"display_name": "Storm Courier"}),
    answer("a-boundaries-again", "boundaries_preferences", 5, visible="None", value={"boundaries": []}),
    answer("a-requests", "specific_requests", 6, visible="No additional requests", value={"requests": []}),
    answer("a-participants", "participants", 7, visible="Just me", value={"participants": []}),
    answer("a-missing-campaign", "ruleset", 0, value=NO_RULESET, campaign_id="campaign-draft-missing"),
    answer("a-bad-campaign", "ruleset", 0, value=NO_RULESET, campaign_id=" campaign"),
]


def visible(op: dict[str, object]) -> str:
    value = op["visible_answer"]
    if isinstance(value, dict):
        return str(value["repeat"]) * int(value["count"])
    return str(value)


def draft_json(result: object) -> dict[str, object]:
    return {key: getattr(result, key) for key in ("campaign_id", "builder_session_id", "current_step",
                                                  "construction_revision", "resulting_revision", "answer_message_id")}


def run(owner: AuthorityOwner, op: dict[str, object], campaign_id: str) -> dict[str, object]:
    try:
        if op["op"] == "create_guided_campaign_draft":
            result = owner.create_guided_campaign_draft(
                operation_id=str(op["operation_id"]), display_name=str(op["display_name"]),
                builder_session_id=str(op["builder_session_id"]),
                builder_provider_id=str(op["builder_provider_id"]), builder_model_id=str(op["builder_model_id"]))
        else:
            result = owner.advance_guided_campaign_draft(
                operation_id=str(op["operation_id"]), campaign_id=str(op["campaign_id"] or campaign_id),
                builder_session_id=str(op["builder_session_id"]),
                expected_construction_revision=int(op["expected_construction_revision"]),  # type: ignore[arg-type]
                current_step=str(op["current_step"]), visible_answer=visible(op), answer=op["answer"])  # type: ignore[arg-type]
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": draft_json(result)}


def aside(database: Path, folder: Path, op: dict[str, object], campaign_id: str) -> dict[str, object]:
    """Run `op` on a copy of the database as it stands, leaving the shared one unchanged."""
    folder.mkdir()
    copy = folder / "authority.sqlite3"
    source, target = sqlite3.connect(database), sqlite3.connect(copy)
    try:
        source.backup(target)
    finally:
        source.close()
        target.close()
    with AuthorityOwner.open(copy, "guided-advance-aside") as owner:
        return run(owner, op, campaign_id)


def checkpoint(database: Path) -> bytes:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return database.read_bytes()


def main() -> None:
    with tempfile.TemporaryDirectory(dir=OUT.parent) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        database.write_bytes(gzip.decompress(SEED.read_bytes()))
        outcomes = []
        campaign_id = ""
        with AuthorityOwner.open(database, "guided-advance-runs") as owner:
            for index, op in enumerate(OPERATIONS):
                if op.get("not_ported"):
                    outcomes.append(aside(database, Path(scratch) / f"aside-{index}", op, campaign_id))
                    continue
                outcome = run(owner, op, campaign_id)
                if op["op"] == "create_guided_campaign_draft":
                    campaign_id = str(outcome["ok"]["campaign_id"])  # type: ignore[index]
                outcomes.append(outcome)
        (OUT / "advance_runs.sqlite3.gz").write_bytes(gzip.compress(checkpoint(database), mtime=0))
    record = {"python": sys.version.split()[0], "operations": OPERATIONS, "outcomes": outcomes}
    text = json.dumps(record, indent=1, ensure_ascii=True) + "\n"
    (OUT / "advance_runs.json").write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    main()
