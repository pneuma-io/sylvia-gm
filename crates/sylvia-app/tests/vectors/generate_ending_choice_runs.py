"""A player's ending button through Python's owner: `campaign_ending_choice_text`.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_ending_choice_runs.py

Two scenarios, each on its own base (`ending_choice/<scenario>/base.sqlite3.gz`), made as
`tests/test_campaign_endings.py` makes its campaign: the Plan format's example handed to a GM
session, every required act and the ending resolved, one narration, and the ending's progress
recorded again so the history page offers the ending. `closed` has an ending without open-world
continuation; `open` one with it. The steps: choices refused (another ending, extra keys, an
unknown choice, an unhashable one, a Builder chat, a missing session), the admitted choices, then
a saved choice (an ordinary agent message, as the session loop saves it) and the choices around it.
Each step records its answer or failure; `final.sqlite3.gz` is each database after its last step.
`uuid.uuid4` is a counter so the record is reproducible. Reference tooling only.
"""

from __future__ import annotations

import copy
import gzip
import json
import sqlite3
import sys
import tempfile
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))
sys.path.insert(0, str(REPOSITORY))

_counter = iter(range(1, 1 << 30))
uuid.uuid4 = lambda: uuid.UUID(int=next(_counter), version=4)  # noqa: E731

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.domain._content_schema_plan import EXAMPLE  # noqa: E402
from sylvia_gm.domain.authority import ProseChannel  # noqa: E402
from sylvia_gm.domain.campaign_ending import required_story_items  # noqa: E402
from sylvia_gm.domain.session import SessionMessageKind  # noqa: E402
from tests.test_alpha_f4_generic_sessions import _prepare_handoff  # noqa: E402

OUT = HERE / "ending_choice"
OWNER = "owner"


def checkpoint(database: Path) -> None:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()


def setup(owner: AuthorityOwner, open_world: bool) -> dict[str, str]:
    plan = copy.deepcopy(EXAMPLE)
    outcome = plan["ending"]["outcomes"][0]
    outcome.update(open_world=open_world, after_story="The road stays open." if open_world else None)
    plan.pop("bindings")
    owner.create_builder_chat(provider_id="test", model_id="test", session_id="init")
    request = _prepare_handoff(owner, suffix="ending", plan_content={"campaign_plan": plan})
    campaign, session = str(request["campaign_id"]), str(request["dm_session_id"])
    owner.complete_turn(campaign, str(request["completed_turn_id"]))
    owner.handoff_builder_to_dm(**request)
    turn = owner.current_open_turn(campaign)
    if turn is None:
        owner.open_turn(campaign, "ending-turn")
        turn = "ending-turn"
    owner.append_agent_message(session_id=session, message_id="ending-input", kind=SessionMessageKind.USER,
                               content={"text": "Report back."})
    progress_id = "ending:signal-settled"
    update = lambda identity, suffix="": {"operation": "update_plan_progress", "plan_item_id": identity,  # noqa: E731
                                          "progress_status": "resolved", "progress_summary": "Resolved."}
    owner.update_campaign_file(session_id=session, update_id="complete-story-and-ending",
                               updates=tuple(update(identity) for identity in [progress_id, *sorted(required_story_items(plan))]),
                               basis_message_ids=("ending-input",), turn_id=turn)
    owner.start_narration_attempt(campaign_id=campaign, attempt_id="ending-narration", turn_id=turn, cancel=lambda: None)
    owner.emit_narration_token(campaign, "ending-narration", ProseChannel.TALK, "The operator thanks you.")
    events = owner.complete_narration_attempt(campaign, "ending-narration")
    owner.append_session_canonical_references(session, events)
    owner.update_campaign_file(session_id=session, update_id="progress-refresh", updates=(update(progress_id),),
                               basis_message_ids=("ending-input",), turn_id=turn)
    assert owner.session_history_page(session, limit=1).campaign_ending is not None
    return {"session": session, "ending_id": str(outcome["id"])}


def steps(ending_id: str, session: str, open_world: bool) -> list[dict[str, object]]:
    choice = lambda selected, **extra: {"ending_id": ending_id, "choice": selected, **extra}  # noqa: E731
    out: list[dict[str, object]] = [
        {"op": "choice", "session": session, "message": "c1", "choice": choice("continue")},
        {"op": "choice", "session": session, "message": "c2", "choice": choice("finish")},
        {"op": "choice", "session": session, "message": "c3", "choice": {"ending_id": "elsewhere", "choice": "finish"}},
        {"op": "choice", "session": session, "message": "c4", "choice": choice("finish", note="extra")},
        {"op": "choice", "session": session, "message": "c5", "choice": choice("later")},
        {"op": "choice", "session": session, "message": "c6", "choice": choice(["finish"])},
        {"op": "choice", "session": session, "message": "c7", "choice": {"ending_id": "elsewhere", "choice": ["finish"]}},
        {"op": "choice", "session": session, "message": "c8", "choice": {"choice": "finish"}},
        {"op": "choice", "session": "init", "message": "c9", "choice": choice("finish")},
        {"op": "choice", "session": "nobody", "message": "c10", "choice": choice("finish")},
    ]
    saved = "continue" if open_world else "finish"
    out += [
        {"op": "append", "session": session, "message": "choice", "kind": "user",
         "content": {"text": "", "campaign_ending_choice": choice(saved),
                     "ooc_text": "Continue adventuring in this world." if open_world else "Finish the story."}},
        {"op": "choice", "session": session, "message": "second-click", "choice": choice(saved)},
        {"op": "choice", "session": session, "message": "choice", "choice": choice(saved)},
        {"op": "choice", "session": session, "message": "choice", "choice": choice("finish" if open_world else "continue")},
    ]
    return out


def run(owner: AuthorityOwner, step: dict[str, object]) -> dict[str, object]:
    try:
        if step["op"] == "choice":
            value: object = owner.campaign_ending_choice_text(str(step["session"]), str(step["message"]), step["choice"])
        else:
            message = owner.append_agent_message(session_id=str(step["session"]), message_id=str(step["message"]),
                                                 kind=SessionMessageKind(step["kind"]), content=step["content"])
            value = {"message_id": message.message_id, "sequence": message.sequence}
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": value}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    scenarios = []
    for name, open_world in (("closed", False), ("open", True)):
        folder = OUT / name
        folder.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
            database = Path(scratch) / "authority.sqlite3"
            with AuthorityOwner.open(database, OWNER, disconnect_grace_seconds=1.0) as owner:
                facts = setup(owner, open_world)
            checkpoint(database)
            (folder / "base.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
            runs = []
            with AuthorityOwner.open(database, OWNER) as owner:
                for step in steps(facts["ending_id"], facts["session"], open_world):
                    runs.append({"step": step, "outcome": run(owner, step)})
            checkpoint(database)
            (folder / "final.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
        scenarios.append({"name": name, "runs": runs})
    record = {"python": sys.version.split()[0], "owner_id": OWNER, "scenarios": scenarios}
    (OUT / "ending_choice_runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
