"""Manual Retry and redirected narration through Python's owner (N2b): `manual_retry` and
`redirect_narration`, with the narration attempt lifecycle around them.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_narration_retry_runs.py

The base (`narration_retry/base.sqlite3.gz`) is `tests/test_player_actions.py`'s campaign with
one saved panel move by its GM session `gm` and a standalone Builder chat `builder`. One owner,
with a five second disconnect grace, runs every step. A Retry's narrator is a script: each item
either emits a token (`{"emit": [channel, token, response_blocks]}`) through the Retry's stream,
or raises `RuntimeError(message)` (`{"raise": message}`); an error the owner raises for a token
passes through the narrator unchanged. Each step records its answer or failure and every
attempt's cancel count after it; `narration_retry/final.sqlite3.gz` is the database after the
last. Interruption identities come from `uuid.uuid4`, a counter here; `tests/narration_retry.rs`
compares both owners' by order of first appearance. A step argument `{"interruption": n}` is the
n-th interruption identity returned so far, and `{"interruption": "last"}` the latest. A Retry
step with `stopped_before_admission` has an `on_attempt_started` that does what the session
loop's `admit_attempt` does when Stop came first: it interrupts the Retry, then refuses. Reference tooling only.
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
from sylvia_gm.domain.errors import CommandRejected  # noqa: E402
from sylvia_gm.domain.session import SessionMessageKind  # noqa: E402
from tests.test_player_actions import _create, _move  # noqa: E402

OUT = HERE / "narration_retry"
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


def stop(attempt: str) -> dict[str, object]:
    return {"op": "append", "session": "gm", "message": f"stop-{attempt}", "kind": "evidence",
            "content": {"kind": "session_attempt_terminal", "attempt_id": attempt, "failure_category": "user_cancelled"}}


def talk(text: str) -> dict[str, object]:
    return {"emit": ["talk", text, None]}


STEPS: list[dict[str, object]] = [
    {"op": "start", "attempt": "a1"},
    {"op": "emit", "attempt": "a1", "channel": "talk", "token": "Half a sent"},
    {"op": "interrupt", "attempt": "a1"},
    {"op": "retry", "interruption": {"interruption": 0}, "retry": "r1", "script": [talk("The retry speaks.")]},
    {"op": "retry", "interruption": {"interruption": 0}, "retry": "r1", "script": [talk("Never said.")]},
    {"op": "retry", "interruption": {"interruption": 0}, "retry": "r2", "script": [talk("Never said.")]},
    {"op": "retry", "interruption": "interruption-missing", "retry": "r2", "script": []},
    {"op": "retry", "interruption": {"interruption": 0}, "retry": " r1", "script": []},
    {"op": "start", "attempt": "a2"},
    {"op": "bind", "attempt": "a2", "action": "move"},
    {"op": "interrupt", "attempt": "a2"},
    {"op": "retry", "interruption": {"interruption": 1}, "retry": "r3", "script": [{"raise": "provider went away"}]},
    {"op": "retryable", "failed": "a2"},
    {"op": "retryable", "failed": "r3"},
    {"op": "status", "attempt": "r3"},
    {"op": "retry", "interruption": {"interruption": 1}, "retry": "r3", "script": [talk("Second time lucky.")]},
    {"op": "start", "attempt": "a3"},
    {"op": "interrupt", "attempt": "a3"},
    {"op": "retry", "interruption": {"interruption": 2}, "retry": "r4",
     "script": [{"emit": ["safe_think", "x", [{"kind": "narration", "text": "x"}]]}, {"raise": "after a refused token"}]},
    {"op": "status", "attempt": "r4"},
    {"op": "retry", "interruption": {"interruption": 2}, "retry": "r5", "script": []},
    {"op": "retry", "interruption": {"interruption": 2}, "retry": "r6",
     "script": [{"emit": ["safe_think", "Thinking it over.", None]}, talk("The door holds.")]},
    {"op": "start", "attempt": "a5"},
    {"op": "emit", "attempt": "a5", "channel": "talk", "token": "Stopped mid"},
    {"op": "interrupt", "attempt": "a5"},
    {"op": "redirect", "session": "gm", "message": "redirect-early", "interruption": {"interruption": 3},
     "content": {"text": "Go left.", "turn_id": "turn", "attempt_id": "c1"}},
    stop("a5"),
    {"op": "unresolved", "session": "gm"},
    {"op": "redirect", "session": "builder", "message": "redirect-builder", "interruption": {"interruption": 3},
     "content": {"text": "Go left.", "turn_id": "turn", "attempt_id": "c1"}},
    {"op": "redirect", "session": "gm", "message": "redirect-turn", "interruption": {"interruption": 3},
     "content": {"text": "Go left.", "turn_id": "elsewhere", "attempt_id": "c1"}},
    {"op": "redirect", "session": "gm", "message": "redirect-no-attempt", "interruption": {"interruption": 3},
     "content": {"text": "Go left.", "turn_id": "turn"}},
    {"op": "redirect", "session": "gm", "message": "redirect-saved", "interruption": {"interruption": 3},
     "content": {"text": "Go left.", "turn_id": "turn", "attempt_id": "r1"}},
    {"op": "redirect", "session": "gm", "message": "redirect-blank", "interruption": {"interruption": 3},
     "content": {"text": "Go left.", "turn_id": "turn", "attempt_id": " c1"}},
    {"op": "redirect", "session": "gm", "message": "redirect-1", "interruption": {"interruption": 3},
     "content": {"text": "Go left.", "turn_id": "turn", "attempt_id": "c1",
                 "redirect_interruption_id": {"interruption": 3}}},
    {"op": "unresolved", "session": "gm"},
    {"op": "retry", "interruption": {"interruption": 3}, "retry": "c2", "script": []},
    {"op": "retry", "interruption": {"interruption": 3}, "retry": "c1", "script": [talk("Left, then.")]},
    {"op": "redirect", "session": "gm", "message": "redirect-resolved", "interruption": {"interruption": 0},
     "content": {"text": "Again.", "turn_id": "turn", "attempt_id": "c9"}},
    {"op": "start", "attempt": "a6"},
    {"op": "interrupt", "attempt": "a6"},
    {"op": "retry", "interruption": {"interruption": "last"}, "retry": "r7", "script": [talk("Never said.")],
     "stopped_before_admission": True},
    {"op": "status", "attempt": "r7"},
    {"op": "retryable", "failed": "r7"},
    {"op": "interruptions"},
]


def main() -> None:
    OUT.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        with AuthorityOwner.open(database, OWNER) as owner:
            _create(owner)
            _move(owner)
            owner.create_builder_chat(provider_id="synthetic", model_id="synthetic", session_id="builder")
        checkpoint(database)
        (OUT / "base.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
        interruptions: list[str] = []
        cancels: dict[str, list[int]] = {}

        def resolve(value: object) -> object:
            if isinstance(value, Mapping) and set(value) == {"interruption"} and isinstance(value["interruption"], int):
                return interruptions[value["interruption"]]
            if isinstance(value, Mapping) and value == {"interruption": "last"}:
                return interruptions[-1]
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

        runs = []
        with AuthorityOwner.open(database, OWNER, disconnect_grace_seconds=5.0) as owner:
            for step in STEPS:
                outcome, value = run(owner, resolve(step), cancels)  # type: ignore[arg-type]
                note(value)
                runs.append({"step": step, "outcome": outcome,
                             "cancels": {key: count[0] for key, count in sorted(cancels.items())}})
        checkpoint(database)
        (OUT / "final.sqlite3.gz").write_bytes(gzip.compress(database.read_bytes(), mtime=0))
    record = {"python": sys.version.split()[0], "owner_id": OWNER, "campaign_id": CAMPAIGN, "runs": runs}
    (OUT / "narration_retry_runs.json").write_bytes(
        (json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


def run(owner: AuthorityOwner, step: dict[str, object], cancels: dict[str, list[int]]) -> tuple[dict[str, object], object]:
    op = step["op"]

    def counter(attempt: str):  # noqa: ANN202
        count = cancels.setdefault(attempt, [0])
        return lambda: count.__setitem__(0, count[0] + 1)

    value: object = None
    try:
        if op == "start":
            value = owner.start_narration_attempt(campaign_id=CAMPAIGN, attempt_id=str(step["attempt"]), turn_id="turn",
                                                  cancel=counter(str(step["attempt"])))
        elif op == "emit":
            value = owner.emit_narration_token(CAMPAIGN, str(step["attempt"]), ProseChannel(step["channel"]),
                                               str(step["token"]))
        elif op == "bind":
            value = owner.bind_narration_action(CAMPAIGN, str(step["attempt"]), str(step["action"]))
        elif op == "interrupt":
            value = owner.interrupt_narration(CAMPAIGN, str(step["attempt"]))
        elif op == "status":
            value = owner.narration_attempt_status(CAMPAIGN, str(step["attempt"]))
        elif op == "retryable":
            value = owner.retryable_interruption(CAMPAIGN, failed_attempt_id=str(step["failed"]))
        elif op == "interruptions":
            value = tuple(owner._store.interruptions(CAMPAIGN))  # the store's own read, for the record
        elif op == "append":
            value = owner.append_agent_message(session_id=str(step["session"]), message_id=str(step["message"]),
                                               kind=SessionMessageKind(step["kind"]), content=step["content"])
        elif op == "unresolved":
            value = owner.unresolved_session_interruption(owner.session_context(str(step["session"])))
        elif op == "redirect":
            value = owner.redirect_narration(session_id=str(step["session"]), message_id=str(step["message"]),
                                             content=step["content"], interruption_id=str(step["interruption"]))
        elif op == "retry":
            started: list[int] = []

            def narrator(retry_input, stream) -> None:  # noqa: ANN001
                for item in step["script"]:  # type: ignore[union-attr]
                    if "raise" in item:
                        raise RuntimeError(item["raise"])
                    channel, token, blocks = item["emit"]
                    stream.emit(ProseChannel(channel), token, response_blocks=blocks)

            def admitted() -> None:
                started.append(1)
                if step.get("stopped_before_admission"):
                    # `_session_loop.admit_attempt` when Stop came before the Retry's admission.
                    owner.interrupt_narration(CAMPAIGN, str(step["retry"]))
                    raise CommandRejected("Agent session was stopped before Retry provider admission")

            value = owner.manual_retry(campaign_id=CAMPAIGN, interruption_id=str(step["interruption"]),
                                       retry_id=str(step["retry"]), narrator=narrator,
                                       cancel=counter(str(step["retry"])),
                                       on_attempt_started=admitted)
            return {"ok": plain(value), "started": len(started)}, value
        else:
            raise AssertionError(op)
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}, None
    return {"ok": plain(value)}, value


if __name__ == "__main__":
    main()
