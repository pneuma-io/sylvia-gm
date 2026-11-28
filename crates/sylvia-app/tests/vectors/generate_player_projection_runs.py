"""The player's projection and a session's stop through Python's owner: `player_projection`,
`player_projection_page` and `stop_agent_session`.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_player_projection_runs.py

Two scenarios, each on its own base (`player_projection/<scenario>/base.sqlite3.gz`):
- `card`: `tests/test_player_actions.py`'s campaign whose move shows
  `tests/test_result_card_actions.py`'s card, with one saved move. The steps: the projection and
  pages (every `after_sequence` form, invalid ones too), a narration's THINK and TALK, a live
  attempt's provisional prose, its interruption's Retry (then with a saved Stop, then with its
  input's saved changes), and the GM session stopped twice;
- `choice`: the campaign whose move asks a route choice, with the choice pending: its projection,
  then the session's stop refused.

Each step records its answer (projections as `_json_value` writes them; a stopped session as its
identity and status) or failure; `final.sqlite3.gz` is each database after its last step.
`uuid.uuid4` is a counter here; `tests/player_projection.rs` compares interruption identities by
order of appearance. Reference tooling only.
"""

from __future__ import annotations

import dataclasses
import enum
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

from sylvia_gm.application.authority import AuthorityOwner  # noqa: E402
from sylvia_gm.domain.authority import ProseChannel  # noqa: E402
from sylvia_gm.domain.session import SessionMessageKind  # noqa: E402
from tests.test_player_actions import _create, _move  # noqa: E402
from tests.test_result_card_actions import CARD  # noqa: E402

OUT = HERE / "player_projection"
OWNER = "owner"
CAMPAIGN = "campaign"


def plain(value: object) -> object:
    """`owner_process._json_value`: a dataclass as its fields in order, an enum as its value."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {field.name: plain(getattr(value, field.name)) for field in dataclasses.fields(value)}
    if isinstance(value, enum.Enum):
        return value.value
    if isinstance(value, Mapping):
        return {str(key): plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [plain(item) for item in value]
    return value


def checkpoint(database: Path) -> None:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()


CARD_STEPS: list[dict[str, object]] = [
    {"op": "projection"},
    {"op": "page", "after": None},
    {"op": "page", "after": 0},
    {"op": "page", "after": 3},
    {"op": "page", "after": 99},
    {"op": "page", "after": -1},
    {"op": "page", "after": True},
    {"op": "page", "after": "3"},
    {"op": "page", "after": 1.5},
    {"op": "start", "attempt": "n1"},
    {"op": "emit", "attempt": "n1", "channel": "safe_think", "token": "Weighing it."},
    {"op": "emit", "attempt": "n1", "channel": "talk", "token": "The door holds."},
    {"op": "complete", "attempt": "n1"},
    {"op": "page", "after": 3},
    {"op": "start", "attempt": "n2"},
    {"op": "emit", "attempt": "n2", "channel": "talk", "token": "Half a sent"},
    {"op": "projection"},
    {"op": "interrupt", "attempt": "n2"},
    {"op": "projection"},
    {"op": "append", "session": "gm", "message": "stop-n2", "kind": "evidence",
     "content": {"kind": "session_attempt_terminal", "attempt_id": "n2", "failure_category": "user_cancelled"}},
    {"op": "page", "after": 5},
    {"op": "append", "session": "gm", "message": "input-n2", "kind": "user",
     "content": {"text": "Open it.", "attempt_id": "n2", "canonical_start_sequence": 0}},
    {"op": "page", "after": 5},
    {"op": "projection_of", "campaign": "nowhere"},
    {"op": "stop", "session": "gm"},
    {"op": "stop", "session": "gm"},
    {"op": "stop", "session": "nobody"},
    {"op": "projection"},
]

CHOICE_STEPS: list[dict[str, object]] = [
    {"op": "projection"},
    {"op": "page", "after": 1},
    {"op": "stop", "session": "gm"},
]


def run(owner: AuthorityOwner, step: dict[str, object]) -> dict[str, object]:
    op = step["op"]
    try:
        if op == "projection":
            value: object = plain(owner.player_projection(CAMPAIGN))
        elif op == "projection_of":
            value = plain(owner.player_projection(str(step["campaign"])))
        elif op == "page":
            value = plain(owner.player_projection_page(CAMPAIGN, after_sequence=step["after"]))
        elif op == "start":
            value = owner.start_narration_attempt(campaign_id=CAMPAIGN, attempt_id=str(step["attempt"]), turn_id="turn",
                                                  cancel=lambda: None)
        elif op == "emit":
            value = owner.emit_narration_token(CAMPAIGN, str(step["attempt"]), ProseChannel(step["channel"]),
                                               str(step["token"]))
        elif op == "complete":
            value = len(owner.complete_narration_attempt(CAMPAIGN, str(step["attempt"])))
        elif op == "interrupt":
            value = owner.interrupt_narration(CAMPAIGN, str(step["attempt"])).failed_attempt_id
        elif op == "append":
            value = owner.append_agent_message(session_id=str(step["session"]), message_id=str(step["message"]),
                                               kind=SessionMessageKind(step["kind"]), content=step["content"]).sequence
        elif op == "stop":
            record = owner.stop_agent_session(str(step["session"]))
            value = {"session_id": record.session_id, "status": record.status.value}
        else:
            raise AssertionError(op)
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": value}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    scenarios = []
    for name, setup, steps in (
        ("card", lambda owner: (_create(owner, card=CARD), _move(owner)), CARD_STEPS),
        ("choice", lambda owner: (_create(owner, choice=True), _move(owner)), CHOICE_STEPS),
    ):
        folder = OUT / name
        folder.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
            database = Path(scratch) / "authority.sqlite3"
            with AuthorityOwner.open(database, OWNER) as owner:
                setup(owner)
            checkpoint(database)
            (folder / "base.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
            with AuthorityOwner.open(database, OWNER, disconnect_grace_seconds=5.0) as owner:
                runs = [{"step": step, "outcome": run(owner, step)} for step in steps]
            checkpoint(database)
            (folder / "final.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
        scenarios.append({"name": name, "runs": runs})
    record = {"python": sys.version.split()[0], "owner_id": OWNER, "scenarios": scenarios}
    (OUT / "player_projection_runs.json").write_bytes(
        (json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
