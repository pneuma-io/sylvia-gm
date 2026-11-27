"""The owner's smaller session operations through Python's owner (N2a): `player_action_response_basis`,
`complete_turn`, `saved_action_receipt`, `append_live_steering_message`,
`append_session_canonical_references`, `append_private_provider_record`,
`finish_private_provider_attempt`, `set_builder_chat_archived`, `agent_session_work` and
`builder_session_work`.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_session_operation_runs.py

The base (`session_operation/base.sqlite3.gz`) is `tests/test_player_actions.py`'s campaign with
two saved panel moves by its GM session `gm`, a second GM session `gm-2` and a standalone Builder
chat `builder`. The steps run in order on one owner. A `narrate` step is a whole narration
attempt (start, one TALK, complete) and an `append` step an ordinary agent message, both ported
already, so later steps see what they wrote. Each step records its answer (a summary for a session
context or Builder chat record) or failure's class, code and message, and
`session_operation/final.sqlite3.gz` is the database after the last. `uuid.uuid4` is a counter
here so the record is reproducible. Reference tooling only.
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
from sylvia_gm.domain.authority import ProseChannel, WriterRole  # noqa: E402
from sylvia_gm.domain.provider_records import ProviderPrivateRecord  # noqa: E402
from sylvia_gm.domain.session import SessionMessageKind  # noqa: E402
from tests.test_player_actions import _create, _move  # noqa: E402

OUT = HERE / "session_operation"
OWNER = "owner"
CAMPAIGN = "campaign"


def plain(value: object) -> object:
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        out: dict[str, object] = {"__class__": type(value).__name__}
        for field in dataclasses.fields(value):
            out[field.name] = plain(getattr(value, field.name))
        return out
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


def chat(record) -> dict[str, object]:  # noqa: ANN001
    return {"session_id": record.session.session_id, "status": record.session.status.value,
            "display_name": record.display_name, "created_ordinal": record.created_ordinal}


def record(**changes: object) -> dict[str, object]:
    out: dict[str, object] = {
        "session_id": "gm", "attempt_id": "think-1", "response_id": "response-1", "turn_id": "turn",
        "basis_message_id": "basis", "source_order": 0, "provider_id": "synthetic", "model_id": "synthetic",
        "kind": "thinking", "payload": "Weighing the route."}
    out.update(changes)
    return out


STEPS: list[dict[str, object]] = [
    {"op": "basis", "session": "gm", "action": "move", "automatic": False},
    {"op": "basis", "session": "gm", "action": "move", "automatic": True},
    {"op": "basis", "session": "gm", "action": "missing", "automatic": False},
    {"op": "basis", "session": "gm-2", "action": "move", "automatic": False},
    {"op": "basis", "session": "builder", "action": "move", "automatic": False},
    {"op": "receipt", "action": "move"},
    {"op": "receipt", "action": "missing"},
    {"op": "agent_work", "session": "gm"},
    {"op": "agent_work", "session": "builder"},
    {"op": "agent_work", "session": "nobody"},
    {"op": "builder_work", "session": "builder"},
    {"op": "builder_work", "session": "gm"},
    {"op": "steering", "session": "gm", "message": "steer-1", "kind": "steering", "content": {"text": "Go north."}},
    {"op": "steering", "session": "gm", "message": "talk-1", "kind": "talk", "content": {"text": "North it is."}},
    {"op": "steering", "session": "builder", "message": "think-1", "kind": "safe_think", "content": {"text": "Hm."}},
    {"op": "steering", "session": "gm", "message": "user-1", "kind": "user", "content": {"text": "No."}},
    {"op": "steering", "session": "gm", "message": "steer-1", "kind": "steering", "content": {"text": "Go south."}},
    {"op": "references", "session": "gm", "sequences": [1, 2]},
    {"op": "references", "session": "gm", "sequences": [1, 2]},
    {"op": "references", "session": "builder", "sequences": [1]},
    {"op": "private", "record": record()},
    {"op": "private", "record": record()},
    {"op": "private", "record": record(payload="Another thought.")},
    {"op": "private", "record": record(response_id="native", kind="continuation", payload="[1]")},
    {"op": "private", "record": record(response_id="native", kind="continuation", payload="{\"id\": 1}")},
    {"op": "private", "record": record(kind="other")},
    {"op": "private", "record": record(source_order=-1)},
    {"op": "finish", "session": "gm", "attempt": "think-1", "completed": True},
    {"op": "finish", "session": "gm", "attempt": "nothing", "completed": False},
    {"op": "private_index", "session": "gm"},
    {"op": "append", "session": "gm", "message": "response-request", "kind": "evidence",
     "content": {"kind": "player_action_response", "player_action_id": "move"}},
    {"op": "basis", "session": "gm", "action": "move", "automatic": False},
    {"op": "basis", "session": "gm", "action": "move-2", "automatic": False},
    {"op": "narrate", "attempt": "narration-1", "text": "The operator reaches the door."},
    {"op": "basis", "session": "gm", "action": "move-2", "automatic": False},
    {"op": "archive", "session": "builder", "archived": True},
    {"op": "archive", "session": "builder", "archived": True},
    {"op": "builder_work", "session": "builder"},
    {"op": "steering", "session": "builder", "message": "think-2", "kind": "safe_think", "content": {"text": "Hm."}},
    {"op": "archive", "session": "builder", "archived": False},
    {"op": "archive", "session": "gm", "archived": True},
    {"op": "archive", "session": "nobody", "archived": True},
    {"op": "complete_turn", "campaign": CAMPAIGN, "turn": "turn"},
    {"op": "complete_turn", "campaign": CAMPAIGN, "turn": "turn"},
    {"op": "complete_turn", "campaign": CAMPAIGN, "turn": "elsewhere"},
    {"op": "complete_turn", "campaign": "nowhere", "turn": "turn"},
]


def run(owner: AuthorityOwner, step: dict[str, object]) -> dict[str, object]:
    op = step["op"]
    try:
        if op == "basis":
            value = owner.player_action_response_basis(str(step["session"]), str(step["action"]),
                                                       automatic=bool(step["automatic"]))
        elif op == "receipt":
            value = owner.saved_action_receipt(CAMPAIGN, str(step["action"]))
        elif op in {"agent_work", "builder_work"}:
            work = owner.agent_session_work if op == "agent_work" else owner.builder_session_work
            with work(str(step["session"])) as context:
                value = {"session_id": context.session.session_id, "messages": len(context.messages)}
        elif op == "steering":
            value = owner.append_live_steering_message(
                session_id=str(step["session"]), message_id=str(step["message"]),
                kind=SessionMessageKind(step["kind"]), content=step["content"])
        elif op == "references":
            events = tuple(event for event in owner.snapshot(CAMPAIGN).events
                           if event.sequence in step["sequences"])  # type: ignore[operator]
            value = owner.append_session_canonical_references(str(step["session"]), events)
        elif op == "private":
            value = owner.append_private_provider_record(ProviderPrivateRecord(**step["record"]))  # type: ignore[arg-type]
        elif op == "finish":
            value = owner.finish_private_provider_attempt(str(step["session"]), str(step["attempt"]),
                                                          completed=bool(step["completed"]))
        elif op == "private_index":
            value = owner.private_provider_index(str(step["session"]))
        elif op == "append":
            value = owner.append_agent_message(session_id=str(step["session"]), message_id=str(step["message"]),
                                               kind=SessionMessageKind(step["kind"]), content=step["content"])
        elif op == "narrate":
            owner.start_narration_attempt(campaign_id=CAMPAIGN, attempt_id=str(step["attempt"]), turn_id="turn",
                                          cancel=lambda: None)
            owner.emit_narration_token(CAMPAIGN, str(step["attempt"]), ProseChannel.TALK, str(step["text"]))
            value = owner.complete_narration_attempt(CAMPAIGN, str(step["attempt"]))
        elif op == "archive":
            value = chat(owner.set_builder_chat_archived(str(step["session"]), archived=bool(step["archived"])))
        elif op == "complete_turn":
            value = owner.complete_turn(str(step["campaign"]), str(step["turn"]))
        else:
            raise AssertionError(op)
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": plain(value)}


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        with AuthorityOwner.open(database, OWNER) as owner:
            _create(owner)
            _move(owner)
            _move(owner, operation_id="move-2", expected_revision=1,
                  selection={"actor": "operator", "destination": [5, 4], "map_revision": 7})
            owner.create_agent_session(campaign_id=CAMPAIGN, session_id="gm-2", role=WriterRole.DM,
                                       provider_id="synthetic", model_id="synthetic")
            owner.create_builder_chat(provider_id="synthetic", model_id="synthetic", session_id="builder")
        checkpoint(database)
        (OUT / "base.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
        runs = []
        with AuthorityOwner.open(database, OWNER, disconnect_grace_seconds=5.0) as owner:
            for step in STEPS:
                runs.append({"step": step, "outcome": run(owner, step)})
        checkpoint(database)
        (OUT / "final.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
    record_out = {"python": sys.version.split()[0], "owner_id": OWNER, "campaign_id": CAMPAIGN, "runs": runs}
    (OUT / "session_operation_runs.json").write_bytes(
        (json.dumps(record_out, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
