"""The narration attempt lifecycle through Python's owner: start, emit, flush, bind, complete,
disconnect, reconnect, status, expire and interrupt, with Retry attempts resolving their
interruptions, and the interruption reads, on `tests/test_player_actions.py`'s campaign.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_narration_attempt_runs.py

One campaign (`_create`, whose `move_unit` mechanic both owners run) goes through four owner
phases on one database: without an injected disconnect grace, with a five second grace,
reopened (a new owner remembers no attempt), and reopened again for the Retries that resolve or
move their interruptions by action. That last phase comes last because Python's startup verifier
refuses an interruption resolved by an action's event (it requires the Retry's own TALK), so
the database it leaves cannot be reopened. `narration_attempt/base.sqlite3.gz` is the database
before the first phase and `final.sqlite3.gz` after the last. Each step records its outcome (the
value, or the failure's class, code and message) and every attempt's cancel count after it.

A Retry owns its interruption only once `redirect_narration` or `manual_retry` (not ported yet)
names it; an `sql` step sets `resolving_retry_id` directly in their place. `uuid.uuid4` is a
counter here so the record is reproducible; the Rust owner's interruption identities are random,
so `tests/narration_attempts.rs` compares them by order of first appearance. A step argument
`{"interruption": n}` is the n-th interruption identity returned so far. Reference tooling only.
"""

from __future__ import annotations

import dataclasses
import enum
import gzip
import json
import math
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

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner  # noqa: E402
from sylvia_gm.domain.authority import ProseChannel, WriterRole  # noqa: E402
from sylvia_gm.domain.session import SessionContext, SessionMessage, SessionMessageKind  # noqa: E402
from tests.test_player_actions import _create  # noqa: E402

OUT = HERE / "narration_attempt"
OWNER = "owner"
CAMPAIGN = "campaign"
TURN = "turn"
NUMBERS = {"inf": math.inf, "-inf": -math.inf, "nan": math.nan}


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


def number(value: object) -> float:
    return NUMBERS[value] if isinstance(value, str) else float(value)  # type: ignore[arg-type]


def move(action_id: str, revision: int, x: int) -> dict[str, object]:
    return {"op": "execute", "action_id": action_id, "revision": revision, "destination": [x, 4]}


def blocks(text: str) -> list[dict[str, object]]:
    return [{"kind": "narration", "text": text}]


# lane 2's queued-commit case: the owner markers and the saved queue, as the sequence code writes them.
MARKER5 = "{\"kind\":\"sylvia_player_action\",\"origin\":{\"control\":\"panel\",\"expected_revision\":4,\"panel_action_id\":\"move\",\"policy\":{\"confirmation\":\"none\",\"response\":\"on_request\"},\"selection\":{\"actor\":\"operator\"},\"session_id\":\"gm\",\"surface_id\":\"operations.map\"}}"
MARKER6 = "{\"kind\":\"sylvia_player_action\",\"origin\":{\"control\":\"panel\",\"expected_revision\":5,\"panel_action_id\":\"move\",\"policy\":{\"confirmation\":\"none\",\"response\":\"on_request\"},\"selection\":{\"actor\":\"operator\"},\"session_id\":\"gm\",\"surface_id\":\"operations.map\"}}"
QUEUE = "{\"campaign_id\":\"campaign\",\"kind\":\"action_sequence\",\"narration_attempt_id\":\"aq\",\"narration_basis_message_id\":\"basis-aq\",\"sequence_id\":\"queued\",\"session_id\":\"gm\",\"status\":\"completed\",\"steps\":[{\"execution\":{\"campaign_id\":\"campaign\",\"expected_revision\":4,\"operation_id\":\"act5\",\"selection\":{\"actor\":\"operator\"},\"session_id\":\"gm\",\"source\":{\"action_id\":\"move\",\"kind\":\"panel\",\"surface_id\":\"operations.map\"}},\"operation_id\":\"act5\",\"status\":\"committed\",\"step_id\":\"walk\"},{\"execution\":{\"campaign_id\":\"campaign\",\"expected_revision\":5,\"operation_id\":\"act6\",\"selection\":{\"actor\":\"operator\"},\"session_id\":\"gm\",\"source\":{\"action_id\":\"move\",\"kind\":\"panel\",\"surface_id\":\"operations.map\"}},\"operation_id\":\"act6\",\"status\":\"committed\",\"step_id\":\"walk-on\"}],\"version\":3}"

PHASES: list[dict[str, object]] = [
    {"grace": None, "steps": [
        {"op": "status", "attempt": "a0"},
        {"op": "start", "attempt": "a0"},
        {"op": "disconnect", "attempt": "a0", "now": "nan"},
        {"op": "disconnect", "attempt": "a0", "now": 1},
        {"op": "start", "attempt": "a0"},
    ]},
    {"grace": 5.0, "steps": [
        {"op": "start", "attempt": "a1", "turn": "elsewhere"},
        {"op": "start", "attempt": " a1"},
        {"op": "start", "attempt": "a1", "last_action": "missing"},
        {"op": "start", "attempt": "a1"},
        {"op": "start", "attempt": "a2"},
        {"op": "start", "attempt": "a1"},
        {"op": "start", "attempt": "a1", "retry_of": "interruption-missing"},
        {"op": "emit", "attempt": "a1", "channel": "talk", "token": "Hello "},
        {"op": "emit", "attempt": "a1", "channel": "talk", "token": "there."},
        {"op": "replay", "ack": 0},
        {"op": "emit", "attempt": "a1", "channel": "safe_think", "token": "Weighing it."},
        {"op": "flush", "attempt": "a1"},
        {"op": "flush", "attempt": "a1"},
        {"op": "emit", "attempt": "a1", "channel": "safe_think", "token": "x", "blocks": blocks("x")},
        {"op": "emit", "attempt": "a1", "channel": "talk", "token": "y", "blocks": blocks("z")},
        {"op": "emit", "attempt": "a1", "channel": "talk", "token": "y", "blocks": [{"kind": "aside", "text": "y"}]},
        {"op": "emit", "attempt": "a1", "channel": "talk", "token": "Run.", "blocks": blocks("Run.")},
        {"op": "emit", "attempt": "a1", "channel": "talk", "token": " More."},
        {"op": "emit", "attempt": "a1", "channel": "talk", "token": "Run.", "blocks": blocks("Run.")},
        move("act1", 0, 4),
        {"op": "bind", "attempt": "a1", "action": "missing"},
        {"op": "bind", "attempt": "a1", "action": "act1"},
        {"op": "status", "attempt": "a1"},
        {"op": "disconnect", "attempt": "a1", "now": 10},
        {"op": "disconnect", "attempt": "a1", "now": 12},
        {"op": "status", "attempt": "a1"},
        {"op": "emit", "attempt": "a1", "channel": "talk", "token": "x"},
        {"op": "flush", "attempt": "a1"},
        {"op": "complete", "attempt": "a1"},
        {"op": "expire", "attempt": "a1", "now": 12},
        {"op": "reconnect", "attempt": "a1", "now": "inf", "ack": 0},
        {"op": "reconnect", "attempt": "a1", "now": 12, "ack": 1},
        {"op": "status", "attempt": "a1"},
        {"op": "complete", "attempt": "a1"},
        {"op": "complete", "attempt": "a1"},
        {"op": "status", "attempt": "a1"},
        {"op": "interrupt", "attempt": "a1"},
        {"op": "expire", "attempt": "a1", "now": 99},
        {"op": "start", "attempt": "a3", "last_action": "act1"},
        {"op": "emit", "attempt": "a3", "channel": "talk", "token": "Half a sent"},
        {"op": "interrupt", "attempt": "a3"},
        {"op": "interrupt", "attempt": "a3"},
        {"op": "expire", "attempt": "a3", "now": 0},
        {"op": "status", "attempt": "a3"},
        {"op": "retryable", "failed": "a3"},
        {"op": "retryable", "failed": "a1"},
        {"op": "retryable", "failed": " "},
        {"op": "interruption", "interruption": {"interruption": 0}},
        {"op": "interruption", "interruption": "interruption-missing"},
        {"op": "unresolved", "session": "gm", "messages": []},
        {"op": "unresolved", "session": "gm", "messages": [
            {"kind": "evidence", "content": {"kind": "session_attempt_terminal", "attempt_id": "a3",
                                             "failure_category": "provider_failed"}}]},
        {"op": "unresolved", "session": "gm", "messages": [
            {"kind": "evidence", "content": {"kind": "session_attempt_terminal", "attempt_id": "a3",
                                             "failure_category": "user_cancelled"}}]},
        {"op": "start", "attempt": "a3", "retry_of": {"interruption": 0}},
        {"op": "emit", "attempt": "a3", "channel": "talk", "token": "Not mine."},
        {"op": "complete", "attempt": "a3"},
        {"op": "interrupt", "attempt": "a3"},
        {"op": "status", "attempt": "a3"},
        {"op": "sql", "query": "UPDATE narration_interruptions SET resolving_retry_id = 'r1' "
                               "WHERE failed_attempt_id = 'a3'"},
        {"op": "unresolved", "session": "gm", "messages": [
            {"kind": "user", "content": {"redirect_interruption_id": {"interruption": 0}, "attempt_id": "r1"}},
            {"kind": "evidence", "content": {"attempt_id": "a3"}}]},
        {"op": "start", "attempt": "r1", "retry_of": {"interruption": 0}},
        {"op": "emit", "attempt": "r1", "channel": "talk", "token": "The retry speaks."},
        {"op": "complete", "attempt": "r1"},
        {"op": "complete", "attempt": "r1"},
        {"op": "retryable", "failed": "a3"},
        {"op": "start", "attempt": "a6"},
        {"op": "emit", "attempt": "a6", "channel": "talk", "token": "Waiting"},
        {"op": "disconnect", "attempt": "a6", "now": 20},
        {"op": "expire", "attempt": "a6", "now": 24.5},
        {"op": "expire", "attempt": "a6", "now": "inf"},
        {"op": "replay", "ack": 20},
        {"op": "expire", "attempt": "a6", "now": 25},
        {"op": "reconnect", "attempt": "a6", "now": 21, "ack": 0},
        {"op": "status", "attempt": "a6"},
        {"op": "start", "attempt": "a7"},
        {"op": "disconnect", "attempt": "a7", "now": 30},
        {"op": "reconnect", "attempt": "a7", "now": 35, "ack": 0},
        {"op": "status", "attempt": "a7"},
        {"op": "start", "attempt": "a8"},
        {"op": "reconnect", "attempt": "a8", "now": 1, "ack": -1},
        {"op": "complete", "attempt": "a8"},
        {"op": "private_index", "session": "gm"},
        {"op": "private_index", "session": "nobody"},
        {"op": "interruptions"},
    ]},
    {"grace": 5.0, "steps": [
        {"op": "status", "attempt": "a1"},
        {"op": "start", "attempt": "a1"},
        {"op": "start", "attempt": "a9", "last_action": "act1"},
        {"op": "complete", "attempt": "a9"},
    ]},
    {"grace": 5.0, "steps": [
        {"op": "start", "attempt": "a4"},
        move("act2", 1, 5),
        {"op": "bind", "attempt": "a4", "action": "act2"},
        {"op": "interrupt", "attempt": "a4"},
        {"op": "sql", "query": "UPDATE narration_interruptions SET resolving_retry_id = 'r2' "
                               "WHERE failed_attempt_id = 'a4'"},
        {"op": "start", "attempt": "r2", "retry_of": {"interruption": 3}},
        {"op": "complete", "attempt": "r2"},
        move("act3", 2, 6),
        {"op": "bind", "attempt": "r2", "action": "act3"},
        {"op": "complete", "attempt": "r2"},
        {"op": "bind", "attempt": "r2", "action": "act2"},
        {"op": "complete", "attempt": "r2"},
        {"op": "start", "attempt": "a5"},
        {"op": "interrupt", "attempt": "a5"},
        {"op": "sql", "query": "UPDATE narration_interruptions SET resolving_retry_id = 'r3' "
                               "WHERE failed_attempt_id = 'a5'"},
        {"op": "start", "attempt": "r3", "retry_of": {"interruption": 4}},
        move("act4", 3, 7),
        {"op": "bind", "attempt": "r3", "action": "act4"},
        {"op": "interrupt", "attempt": "r3"},
        {"op": "retryable", "failed": "a5"},
        {"op": "retryable", "failed": "r3"},
        # A queue bound to the live attempt `aq` committed act5 and act6 (their saved markers and the
        # queue are written directly: no Rust owner composes queues yet). The attempt is then bound
        # to the older act4, so its interruption names act6, the latest queued commit, only if
        # `last_attempt_commit` reads the queue.
        {"op": "start", "attempt": "aq"},
        move("act5", 4, 8),
        move("act6", 5, 9),
        {"op": "sql", "query": "INSERT INTO consequences (campaign_id, action_id, ordinal, payload_json) "
                               "VALUES ('campaign', 'act5', 1000, '" + MARKER5 + "'), "
                               "('campaign', 'act6', 1000, '" + MARKER6 + "')"},
        {"op": "sql", "query": "INSERT INTO agent_session_messages (session_id, sequence, message_id, kind, "
                               "content_json, canonical_sequence) SELECT 'gm', COALESCE(MAX(sequence), 0) + 1, "
                               "'sequence-queued-3', 'evidence', '" + QUEUE + "', NULL "
                               "FROM agent_session_messages WHERE session_id = 'gm'"},
        {"op": "bind", "attempt": "aq", "action": "act4"},
        {"op": "interrupt", "attempt": "aq"},
        {"op": "interruptions"},
    ]},
]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    phases_out: list[dict[str, object]] = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        with AuthorityOwner.open(database, OWNER) as owner:
            _create(owner)
        checkpoint(database)
        (OUT / "base.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
        interruptions: list[str] = []
        cancels: dict[str, list[int]] = {}

        def resolve(value: object) -> object:
            if isinstance(value, Mapping) and set(value) == {"interruption"}:
                return interruptions[value["interruption"]]  # type: ignore[index]
            if isinstance(value, Mapping):
                return {key: resolve(item) for key, item in value.items()}
            if isinstance(value, list):
                return [resolve(item) for item in value]
            return value

        def note(value: object) -> None:
            if dataclasses.is_dataclass(value) and type(value).__name__ == "NarrationInterruption":
                if value.interruption_id not in interruptions:  # type: ignore[attr-defined]
                    interruptions.append(value.interruption_id)  # type: ignore[attr-defined]
            elif isinstance(value, tuple):
                for item in value:
                    note(item)

        for phase in PHASES:
            grace = phase["grace"]
            kwargs = {} if grace is None else {"disconnect_grace_seconds": grace}
            runs: list[dict[str, object]] = []
            with AuthorityOwner.open(database, OWNER, **kwargs) as owner:
                for step in phase["steps"]:  # type: ignore[union-attr]
                    outcome = run(owner, database, resolve(step), cancels)  # type: ignore[arg-type]
                    if "ok" in outcome:
                        note(outcome.pop("value"))
                    runs.append({"step": step, "outcome": outcome,
                                 "cancels": {key: value[0] for key, value in sorted(cancels.items())}})
            phases_out.append({"grace": grace, "runs": runs})
        checkpoint(database)
        (OUT / "final.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
    record = {"python": sys.version.split()[0], "owner_id": OWNER, "campaign_id": CAMPAIGN,
              "turn_id": TURN, "phases": phases_out}
    (OUT / "narration_attempt_runs.json").write_bytes(
        (json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


def run(owner: AuthorityOwner, database: Path, step: dict[str, object], cancels: dict[str, list[int]]) -> dict[str, object]:
    op = step["op"]
    attempt = step.get("attempt")
    try:
        if op == "start":
            count = cancels.setdefault(str(attempt), [0])
            value = owner.start_narration_attempt(
                campaign_id=CAMPAIGN, attempt_id=str(attempt), turn_id=str(step.get("turn", TURN)),
                cancel=lambda: count.__setitem__(0, count[0] + 1),
                last_action_id=step.get("last_action"), interruption_id=step.get("retry_of"),
            )
        elif op == "emit":
            value = owner.emit_narration_token(
                CAMPAIGN, str(attempt), ProseChannel(step["channel"]), str(step["token"]),
                response_blocks=step.get("blocks"),
            )
        elif op == "flush":
            value = owner.flush_narration(CAMPAIGN, str(attempt))
        elif op == "bind":
            value = owner.bind_narration_action(CAMPAIGN, str(attempt), str(step["action"]))
        elif op == "complete":
            value = owner.complete_narration_attempt(CAMPAIGN, str(attempt))
        elif op == "disconnect":
            value = owner.disconnect_narration(CAMPAIGN, str(attempt), now=number(step["now"]))
        elif op == "reconnect":
            value = owner.reconnect_narration(CAMPAIGN, str(attempt), now=number(step["now"]),
                                              acknowledged_sequence=int(step["ack"]))  # type: ignore[arg-type]
        elif op == "status":
            value = owner.narration_attempt_status(CAMPAIGN, str(attempt))
        elif op == "expire":
            value = owner.expire_narration(CAMPAIGN, str(attempt), now=number(step["now"]))
        elif op == "interrupt":
            value = owner.interrupt_narration(CAMPAIGN, str(attempt))
        elif op == "replay":
            value = owner.canonical_replay(CAMPAIGN, acknowledged_sequence=int(step["ack"]))  # type: ignore[arg-type]
        elif op == "execute":
            action_id = str(step["action_id"])
            value = owner.execute_action(ActionCommand(
                campaign_id=CAMPAIGN, action_id=action_id, idempotency_key=f"idem-{action_id}",
                owner_id=OWNER, writer=WriterRole.DM, expected_revision=int(step["revision"]),  # type: ignore[arg-type]
                turn_id=TURN, package_id="synthetic.coordinate", tool_name="move_unit",
                parameters={"actor": "operator", "destination": step["destination"], "mode": "careful"},
            ))
        elif op == "retryable":
            value = owner.retryable_interruption(CAMPAIGN, failed_attempt_id=str(step["failed"]))
        elif op == "interruption":
            value = owner.narration_interruption(CAMPAIGN, str(step["interruption"]))
        elif op == "interruptions":
            value = tuple(owner._store.interruptions(CAMPAIGN))  # the store's own read, for the record
        elif op == "unresolved":
            context = owner.session_context(str(step["session"]))
            base = len(context.messages)
            extra = tuple(
                SessionMessage(
                    session_id=context.session.session_id, sequence=base + index + 1,
                    message_id=f"extra-{index}", kind=SessionMessageKind(item["kind"]),
                    content=item["content"],
                )
                for index, item in enumerate(step["messages"])  # type: ignore[arg-type]
            )
            value = owner.unresolved_session_interruption(
                SessionContext(session=context.session, messages=(*context.messages, *extra)))
        elif op == "private_index":
            value = owner.private_provider_index(str(step["session"]))
        elif op == "sql":
            connection = sqlite3.connect(database)
            connection.execute(str(step["query"]))
            connection.commit()
            connection.close()
            value = None
        else:
            raise AssertionError(op)
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": plain(value), "value": value}


if __name__ == "__main__":
    main()
