"""Action sequences through Python's real owner: `action_sequence`, `compose_action_sequence`,
`execute_action_sequence`, their agent seams and `last_attempt_commit`, for
`tests/sequence_owner.rs`, which runs lane 2's `sylvia_app::action_sequences` on the authority
(`AuthoritySequences`).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_sequence_owner_runs.py

The base (`sequence_owner/base.sqlite3.gz`) is `tests/test_player_actions.py`'s campaign whose
move shows `tests/test_result_card_actions.py`'s card, with one saved move. The steps:
- a queue of the card's button, twice: read before it exists, composed, a stale version refused,
  read, an execution at the wrong version refused, executed, and read after;
- the agent seam: composed, executed with no campaign work (refused), then with the GM session's
  own work held while a narration attempt is open for the TURN. That binds the attempt to the
  queue's input and the attempt to the action it committed, inside the held narration lock;
- `last_attempt_commit` for that attempt, the attempt's interruption (its last action), and a
  read for a session that does not exist.

A coordinate-map step (one whose selection carries `map_revision`) rebinds through
`campaign_panels`, which the authority has not ported yet; it is not a step here. Each step
records its answer or failure; `final.sqlite3.gz` is the database after the last. `uuid.uuid4`
is a counter here; the test compares interruption identities by order of appearance. Reference
tooling only.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import sys
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))
sys.path.insert(0, str(REPOSITORY))

_counter = iter(range(1, 1 << 30))
uuid.uuid4 = lambda: uuid.UUID(int=next(_counter), version=4)  # noqa: E731

from sylvia_gm.application import action_sequences  # noqa: E402
from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.domain.session import SessionMessageKind  # noqa: E402
from tests.test_player_actions import _create, _move  # noqa: E402
from tests.test_result_card_actions import CARD  # noqa: E402

OUT = HERE / "sequence_owner"
OWNER = "owner"
CAMPAIGN = "campaign"
GM = "gm"


def ordered(value: object) -> object:
    """A frozen payload as plain JSON, keeping Python's own key order (never sorted)."""
    if isinstance(value, Mapping):
        return {str(key): ordered(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [ordered(item) for item in value]
    return value


def card(identity: str) -> dict[str, object]:
    return {"step_id": identity, "source": {"kind": "result_card", "action_id": "move", "button_index": 0},
            "selection": None}


STEPS: list[dict[str, object]] = [
    {"op": "read", "session": GM, "sequence": None},
    {"op": "compose", "sequence": "q1", "version": 0, "steps": [card("c1"), card("c2")]},
    {"op": "compose", "sequence": "q1", "version": 0, "steps": [card("c1")]},
    {"op": "read", "session": GM, "sequence": "q1"},
    {"op": "execute", "sequence": "q1", "version": 9},
    {"op": "execute", "sequence": "q1", "version": "last"},
    {"op": "read", "session": GM, "sequence": "q1"},
    {"op": "compose", "sequence": "q2", "version": 0, "steps": [card("a1")], "agent": True},
    {"op": "execute", "sequence": "q2", "version": "last", "agent": True},
    {"op": "start", "attempt": "n1"},
    {"op": "append", "message": "input-n1", "content": {"text": "Move again.", "attempt_id": "n1",
                                                         "canonical_start_sequence": 0}},
    {"op": "execute", "sequence": "q2", "version": "last", "agent": True, "holding": GM},
    {"op": "last_attempt_commit", "attempt": "n1"},
    {"op": "interrupt", "attempt": "n1"},
    {"op": "read", "session": "nobody", "sequence": None},
]


def run(owner: AuthorityOwner, step: dict[str, object], last: dict[str, object]) -> dict[str, object]:
    op = step["op"]
    try:
        if op == "read":
            value: object = ordered(owner.action_sequence(session_id=str(step["session"]),
                                                          sequence_id=step["sequence"]))
        elif op == "compose":
            compose = owner._compose_action_sequence_from_agent if step.get("agent") else owner.compose_action_sequence
            queue = compose(campaign_id=CAMPAIGN, session_id=GM, sequence_id=str(step["sequence"]),
                            expected_revision=owner.snapshot(CAMPAIGN).revision,
                            expected_version=step["version"], steps=step["steps"])
            last[str(step["sequence"])] = queue
            value = ordered(queue)
        elif op == "execute":
            saved = last.get(str(step["sequence"]), {})
            version = saved.get("version") if step["version"] == "last" else step["version"]
            execute = owner._execute_action_sequence_from_agent if step.get("agent") else owner.execute_action_sequence

            def call() -> object:
                return execute(campaign_id=CAMPAIGN, session_id=GM, sequence_id=str(step["sequence"]),
                               expected_version=version, confirmation=saved.get("confirmation"))

            if step.get("holding"):
                with owner.agent_session_work(str(step["holding"])):
                    value = ordered(call())
            else:
                value = ordered(call())
        elif op == "start":
            value = owner.start_narration_attempt(campaign_id=CAMPAIGN, attempt_id=str(step["attempt"]),
                                                  turn_id="turn", cancel=lambda: None)
        elif op == "append":
            value = owner.append_agent_message(session_id=GM, message_id=str(step["message"]),
                                               kind=SessionMessageKind.USER, content=step["content"]).sequence
        elif op == "last_attempt_commit":
            value = action_sequences.last_attempt_commit(owner, CAMPAIGN, str(step["attempt"]))
        elif op == "interrupt":
            interruption = owner.interrupt_narration(CAMPAIGN, str(step["attempt"]))
            value = {"interruption_id": interruption.interruption_id, "last_action_id": interruption.last_action_id,
                     "failed_attempt_id": interruption.failed_attempt_id}
        else:
            raise AssertionError(op)
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": value}


def checkpoint(database: Path) -> None:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        with AuthorityOwner.open(database, OWNER) as owner:
            _create(owner, card=CARD)
            _move(owner)
        checkpoint(database)
        (OUT / "base.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
        last: dict[str, object] = {}
        with AuthorityOwner.open(database, OWNER, disconnect_grace_seconds=5.0) as owner:
            runs = [{"step": step, "outcome": run(owner, step, last)} for step in STEPS]
        checkpoint(database)
        (OUT / "final.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
    record = {"python": sys.version.split()[0], "owner_id": OWNER, "runs": runs}
    (OUT / "sequence_owner_runs.json").write_bytes(
        (json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
