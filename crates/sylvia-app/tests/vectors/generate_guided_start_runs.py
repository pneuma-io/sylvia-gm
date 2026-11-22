"""Starting a guided campaign: Python's `AuthorityOwner.bind_guided_campaign_basis`,
`begin_campaign_construction`, `campaign_construction_basis` and `append_agent_message`.

Run with the snapshot's own interpreter, from the repository root, after
`generate_guided_draft_runs.py` (its new profile is the base):

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_guided_start_runs.py

It makes a No Ruleset Draft and answers its Interview to the review, then binds and begins it as
`handle_start_guided_campaign` does: the basis read before anything exists, a binding refused
until the Ready to Begin message is saved, identity refusals, the binding, its replay and a
conflicting replay, a second binding, the basis before and after construction begins, begin's
refusals, the begin and its replay, and a Draft whose Interview is unfinished. It writes
`guided/start_runs.json` and `guided/start_runs.sqlite3.gz` (the database afterwards);
`tests/guided.rs` replays them. Identities are explicit and the owner's UUIDs are seeded, so a
re-run writes byte-identical files. Reference tooling only.

The four guided generators share `guided/` and build on this one's seed. Run them in order with
`generate_guided_all.py` (draft, advance, start, finalize, ruleset).
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

_IDENTITIES = random.Random(20261126)
uuid.uuid4 = lambda: uuid.UUID(int=_IDENTITIES.getrandbits(128), version=4)  # type: ignore[assignment]

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.domain.authority import PackageLock  # noqa: E402
from sylvia_gm.domain.session import SessionMessageKind  # noqa: E402

OUT = Path(__file__).resolve().parent / "guided"
SEED = OUT / "seed.sqlite3.gz"
STORM, HARBOUR = "session-storm", "session-harbour"
READY = {"text": "Ready to Begin", "interview_step": "ready", "interview_value": {"action": "ready_to_begin"}}


def draft(operation_id: str, name: str, session_id: str) -> dict[str, object]:
    return {"op": "create_guided_campaign_draft", "operation_id": operation_id, "display_name": name,
            "builder_session_id": session_id, "builder_provider_id": "standin",
            "builder_model_id": "standin/standin-narrator"}


def answer(operation_id: str, step: str, revision: int, value: dict[str, object]) -> dict[str, object]:
    return {"op": "advance_guided_campaign_draft", "operation_id": operation_id, "campaign": "storm",
            "builder_session_id": STORM, "expected_construction_revision": revision, "current_step": step,
            "visible_answer": "An answer", "answer": value}


def bind(operation_id: str, campaign: str = "storm", session_id: str = STORM, name: str = "Storm Courier",
         turn_id: str = "turn-construction-storm") -> dict[str, object]:
    return {"op": "bind_guided_campaign_basis", "operation_id": operation_id, "campaign": campaign,
            "display_name": name, "turn_id": turn_id, "builder_session_id": session_id}


def begin(operation_id: str, revision: int, lane: str = "quick_start", campaign: str = "storm",
          canonical_input: object = None) -> dict[str, object]:
    return {"op": "begin_campaign_construction", "operation_id": operation_id, "campaign": campaign, "lane": lane,
            "expected_revision": revision, "canonical_input": {} if canonical_input is None else canonical_input}


def basis(campaign: str = "storm") -> dict[str, object]:
    return {"op": "campaign_construction_basis", "campaign": campaign}


OPERATIONS: list[dict[str, object]] = [
    draft("storm", "Storm Courier", STORM), draft("harbour", "Harbour Lights", HARBOUR),
    answer("a-ruleset", "ruleset", 0, {"candidate_id": None}),
    answer("a-name", "name", 1, {"display_name": "Storm Courier"}),
    answer("a-boundaries", "boundaries_preferences", 2, {"boundaries": []}),
    answer("a-requests", "specific_requests", 3, {"requests": []}),
    basis(), basis(" storm"),
    # The Ready to Begin message comes first.
    bind("storm-bind"),
    {"op": "append_agent_message", "session_id": STORM, "message_id": "guided-ready-storm", "content": READY},
    {"op": "append_agent_message", "session_id": STORM, "message_id": "guided-ready-storm", "content": READY},
    bind("storm-bind", turn_id=" turn"), bind("storm-bind", name=""), bind("storm-bind", session_id=HARBOUR),
    bind("storm-bind", session_id="session-missing"),
    bind("storm-bind"), bind("storm-bind"), bind("storm-bind", name="Another Name"), bind("storm-bind-again"),
    basis(),
    begin("storm-begin", 0, lane="slow"), begin("storm-begin", -1),
    begin("storm-begin", 0, canonical_input={"private_reasoning": 1}), begin("storm-begin", 3),
    begin("storm-begin", 0, lane="customize", canonical_input={"premise": "A courier shelters from a storm."}),
    begin("storm-begin", 0, lane="customize", canonical_input={"premise": "A courier shelters from a storm."}),
    begin("storm-begin", 0), begin("storm-begin-again", 1),
    basis(),
    # An unfinished Interview cannot be bound, and has nothing to begin.
    {"op": "append_agent_message", "session_id": HARBOUR, "message_id": "guided-ready-harbour", "content": READY},
    bind("harbour-bind", campaign="harbour", session_id=HARBOUR, name="Harbour Lights"),
    begin("harbour-begin", 0, campaign="harbour"), basis("harbour"), basis("campaign-missing"),
]


def record(value: object) -> object:
    """A result's fields, as the Rust test projects them."""
    fields = getattr(value, "__dataclass_fields__", None)
    if fields is None:
        return value
    out: dict[str, object] = {}
    for name in fields:
        item = getattr(value, name)
        out[name] = dict(item) if hasattr(item, "items") else getattr(item, "value", item)
    return out


def run(owner: AuthorityOwner, op: dict[str, object], campaigns: dict[str, str]) -> dict[str, object]:
    campaign = campaigns.get(str(op.get("campaign")), str(op.get("campaign")))
    try:
        kind = op["op"]
        if kind == "create_guided_campaign_draft":
            result = owner.create_guided_campaign_draft(
                operation_id=str(op["operation_id"]), display_name=str(op["display_name"]),
                builder_session_id=str(op["builder_session_id"]),
                builder_provider_id=str(op["builder_provider_id"]), builder_model_id=str(op["builder_model_id"]))
            campaigns[str(op["operation_id"])] = result.campaign_id
            value: object = record(result)
        elif kind == "advance_guided_campaign_draft":
            value = record(owner.advance_guided_campaign_draft(
                operation_id=str(op["operation_id"]), campaign_id=campaign,
                builder_session_id=str(op["builder_session_id"]),
                expected_construction_revision=int(op["expected_construction_revision"]),  # type: ignore[arg-type]
                current_step=str(op["current_step"]), visible_answer=str(op["visible_answer"]),
                answer=op["answer"]))  # type: ignore[arg-type]
        elif kind == "append_agent_message":
            message = owner.append_agent_message(
                session_id=str(op["session_id"]), message_id=str(op["message_id"]),
                kind=SessionMessageKind.USER, content=op["content"])  # type: ignore[arg-type]
            value = {"message_id": message.message_id, "sequence": message.sequence}
        elif kind == "bind_guided_campaign_basis":
            value = record(owner.bind_guided_campaign_basis(
                operation_id=str(op["operation_id"]), campaign_id=campaign, display_name=str(op["display_name"]),
                package_lock=PackageLock(entries=()), turn_id=str(op["turn_id"]),
                builder_session_id=str(op["builder_session_id"])))
        elif kind == "begin_campaign_construction":
            value = record(owner.begin_campaign_construction(
                operation_id=str(op["operation_id"]), campaign_id=campaign, lane=str(op["lane"]),
                expected_revision=int(op["expected_revision"]),  # type: ignore[arg-type]
                canonical_input=op["canonical_input"]))  # type: ignore[arg-type]
        else:
            value = record(owner.campaign_construction_basis(campaign))
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": value}


def checkpoint(database: Path) -> bytes:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return database.read_bytes()


def main() -> None:
    with tempfile.TemporaryDirectory(dir=OUT.parent) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        database.write_bytes(gzip.decompress(SEED.read_bytes()))
        campaigns: dict[str, str] = {}
        with AuthorityOwner.open(database, "guided-start-runs") as owner:
            outcomes = [run(owner, op, campaigns) for op in OPERATIONS]
        (OUT / "start_runs.sqlite3.gz").write_bytes(gzip.compress(checkpoint(database), mtime=0))
    record_json = {"python": sys.version.split()[0], "operations": OPERATIONS, "outcomes": outcomes}
    text = json.dumps(record_json, indent=1, ensure_ascii=True) + "\n"
    (OUT / "start_runs.json").write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    main()
