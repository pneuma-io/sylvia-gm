"""Confirming a NEW CAMPAIGN and completing its construction through Python's owner:
`AuthorityOwner.confirm_new_campaign`, `complete_campaign_construction`, `campaign_confirmation`,
`reject_uncreated_ready_campaign` and `ready_campaign_start_work`, with
`begin_campaign_construction` between confirmation and completion.

Run with the snapshot's own interpreter, from the repository root, after
`generate_guided_ruleset_runs.py` (its `guided/ruleset_seed.sqlite3.gz`, a profile with three
small Rulesets installed, is the base):

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_campaign_creation_runs.py

The steps: confirmation reads (absent, invalid, a confirmation, an operation of another kind);
confirmation's identity and Builder session refusals; a No Ruleset confirmation, its replay and
a conflicting replay; one on the `plain` Ruleset's candidate with a Builder session; a reused
Builder session; initial state (owned, duplicated, unsafe); the prepared-start fence (a rejected
identity refused, a confirmed one not rejected, a start held across its confirmation, a
start rejecting its own identity while it holds the fence); then begin
and completion with completion's refusals, its replay and a conflicting replay. Each step records
its answer or failure; `campaign_creation/final.sqlite3.gz` is the database afterwards.
`uuid.uuid4` is a counter here; `tests/campaign_creation.rs` compares campaign and lock
identities by order of appearance. Reference tooling only.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[3] / "src"))

_counter = iter(range(1, 1 << 30))
uuid.uuid4 = lambda: uuid.UUID(int=next(_counter), version=4)  # type: ignore[assignment]  # noqa: E731

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.domain.authority import PackageLock, StateValue  # noqa: E402

BASE = HERE / "guided" / "ruleset_seed.sqlite3.gz"
OUT = HERE / "campaign_creation"
OWNER = "campaign-creation-runs"
PLAIN = "candidate-install-plain"
BUILDER = {"session": "session-builder-hollow", "provider": "standin", "model": "standin/standin-narrator"}


def confirm(operation_id: str, name: str = "Hollow Road", turn_id: str = "turn-hollow", candidate: str | None = None,
            session: dict[str, str | None] | None = None, skill: str | None = None,
            state: list[list[object]] | None = None) -> dict[str, object]:
    step: dict[str, object] = {"op": "confirm", "operation_id": operation_id, "display_name": name, "turn_id": turn_id,
                               "candidate": candidate}
    if session is not None:
        step["session"] = session
    if skill is not None:
        step["skill"] = skill
    if state is not None:
        step["state"] = state
    return step


def complete(operation_id: str, campaign: str, revision: object, plan: object) -> dict[str, object]:
    return {"op": "complete", "operation_id": operation_id, "campaign": campaign, "expected_revision": revision,
            "plan": plan}


PLAN = {"premise": "A road through a hollow hill.", "beats": ["arrive", "descend"]}
STEPS: list[dict[str, object]] = [
    {"op": "confirmation", "operation_id": "nothing"},
    {"op": "confirmation", "operation_id": "   "},
    confirm(""),
    confirm("blank-name", name="  "),
    confirm("blank-turn", turn_id=""),
    confirm("blank-candidate", candidate=" "),
    confirm("half-session", session={"session": "session-half", "provider": "standin", "model": None}),
    confirm("blank-session", session={"session": "", "provider": "standin", "model": "standin/x"}),
    confirm("bad-skill", session=BUILDER, skill="BUILD EVERYTHING"),
    confirm("hollow"),
    confirm("hollow"),
    confirm("hollow", name="Another Road"),
    {"op": "confirmation", "operation_id": "hollow"},
    confirm("plain", name="Plain Road", turn_id="turn-plain", candidate=PLAIN, session=BUILDER, skill="BUILD RULESET"),
    confirm("plain-again", name="Plain Road", turn_id="turn-plain", candidate=PLAIN, session=BUILDER),
    confirm("stateful", turn_id="turn-stateful", state=[["story", "weather", "rain"], ["story", "hour", 3]]),
    confirm("stateful-ruleset", turn_id="turn-stateful", candidate=PLAIN,
            state=[["synthetic.guided-plain", "pace", {"steps": [1, 2]}]]),
    confirm("duplicate-state", state=[["story", "weather", "rain"], ["story", "weather", "fog"]]),
    confirm("unsafe-state", state=[["story", "raw_reasoning", "hidden"]]),
    {"op": "reject", "operation_id": "fenced"},
    {"op": "reject", "operation_id": "fenced"},
    confirm("fenced"),
    {"op": "start", "operation_id": "fenced"},
    {"op": "reject", "operation_id": "hollow"},
    {"op": "start_and_confirm", "confirm": confirm("held", name="Held Road", turn_id="turn-held")},
    {"op": "start_and_reject", "operation_id": "held-rejected"},
    confirm("held-rejected"),
    {"op": "confirmation", "operation_id": "held"},
    complete("c-early", "plain", 0, PLAN),
    {"op": "begin", "operation_id": "b-plain", "campaign": "plain", "expected_revision": 0,
     "input": {"lane_note": "quick"}},
    {"op": "confirmation", "operation_id": "b-plain"},
    complete("", "plain", 1, PLAN),
    complete("c-plain", "plain", -1, PLAN),
    complete("c-plain", "plain", 1, "not an object"),
    complete("c-plain", "plain", 1, {"raw_reasoning": "hidden"}),
    complete("c-plain", "plain", 0, PLAN),
    complete("c-plain", "missing", 1, PLAN),
    complete("c-plain", "plain", 1, PLAN),
    complete("c-plain", "plain", 1, PLAN),
    complete("c-plain", "plain", 1, {"premise": "Changed."}),
    complete("c-plain-again", "plain", 2, PLAN),
    complete("c-hollow", "hollow", 0, PLAN),
]


def confirm_with(owner: AuthorityOwner, step: dict[str, object], campaigns: dict[str, str]) -> dict[str, object]:
    candidate = step["candidate"]
    lock = PackageLock(entries=()) if candidate is None or not str(candidate).strip() \
        else owner.package_candidate(str(candidate)).package_lock
    session = step.get("session") or {}
    keywords: dict[str, object] = {}
    if "skill" in step:
        keywords["builder_selected_skill"] = step["skill"]
    confirmation = owner.confirm_new_campaign(
        operation_id=str(step["operation_id"]), display_name=str(step["display_name"]), package_lock=lock,
        turn_id=str(step["turn_id"]), package_candidate_id=candidate,  # type: ignore[arg-type]
        builder_session_id=session.get("session"), builder_provider_id=session.get("provider"),  # type: ignore[union-attr]
        builder_model_id=session.get("model"),  # type: ignore[union-attr]
        initial_state=tuple(StateValue(str(n), str(k), v) for n, k, v in step.get("state", [])),  # type: ignore[union-attr]
        **keywords)  # type: ignore[arg-type]
    campaigns[str(step["operation_id"])] = confirmation.campaign_id
    return {"campaign_id": confirmation.campaign_id, "resulting_revision": confirmation.resulting_revision}


def run(owner: AuthorityOwner, step: dict[str, object], campaigns: dict[str, str]) -> dict[str, object]:
    op = step["op"]
    try:
        if op == "confirmation":
            found = owner.campaign_confirmation(str(step["operation_id"]))
            value: object = None if found is None else {"campaign_id": found.campaign_id,
                                                         "resulting_revision": found.resulting_revision}
        elif op == "confirm":
            value = confirm_with(owner, step, campaigns)
        elif op == "reject":
            value = owner.reject_uncreated_ready_campaign(str(step["operation_id"]))
        elif op == "start":
            with owner.ready_campaign_start_work(str(step["operation_id"])):
                value = None
        elif op == "start_and_reject":
            with owner.ready_campaign_start_work(str(step["operation_id"])):
                value = owner.reject_uncreated_ready_campaign(str(step["operation_id"]))
        elif op == "start_and_confirm":
            inner = step["confirm"]
            with owner.ready_campaign_start_work(str(inner["operation_id"])):  # type: ignore[index]
                value = confirm_with(owner, inner, campaigns)  # type: ignore[arg-type]
        elif op == "begin":
            begun = owner.begin_campaign_construction(
                operation_id=str(step["operation_id"]), campaign_id=campaigns[str(step["campaign"])],
                lane="quick_start", expected_revision=step["expected_revision"],  # type: ignore[arg-type]
                canonical_input=step["input"])  # type: ignore[arg-type]
            value = {"campaign_id": begun.campaign_id, "lane": begun.lane, "resulting_revision": begun.resulting_revision}
        elif op == "complete":
            campaign = campaigns.get(str(step["campaign"]), str(step["campaign"]))
            done = owner.complete_campaign_construction(
                operation_id=str(step["operation_id"]), campaign_id=campaign,
                expected_revision=step["expected_revision"],  # type: ignore[arg-type]
                canonical_plan=step["plan"])  # type: ignore[arg-type]
            value = {"campaign_id": done.campaign_id, "lane": done.lane,
                     "canonical_representation_sha256": done.canonical_representation_sha256,
                     "resulting_revision": done.resulting_revision}
        else:
            raise AssertionError(op)
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": value}


def checkpoint(database: Path) -> bytes:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return database.read_bytes()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        database.write_bytes(gzip.decompress(BASE.read_bytes()))
        campaigns: dict[str, str] = {}
        with AuthorityOwner.open(database, OWNER) as owner:
            runs = [{"step": step, "outcome": run(owner, step, campaigns)} for step in STEPS]
        (OUT / "final.sqlite3.gz").write_bytes(gzip.compress(checkpoint(database), mtime=0))
    record = {"python": sys.version.split()[0], "owner_id": OWNER, "runs": runs}
    (OUT / "runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
