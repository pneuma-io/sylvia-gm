"""The GM response context's player-action reads: what Python's owner answers on saved play.

Run with the snapshot's own interpreter, from the repository root, after
`crates/sylvia-store/tests/vectors/generate_stored_authority_runs.py` (its failed-action profile
is the base):

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_player_read_runs.py

The base is the story campaign with a committed action and a failed one carrying the owner's
player-action marker. Python's store and owner then add, as play would: two more committed
player actions with markers (one from a result card, one from a saved action sequence's step), a
GM narration between them, saved `player_action_response` requests (by explicit identities, by
response window, by a single identity), a retry and turn activity answering one of them, and a
bound `action_sequence` queue whose step is the second action; then a queue whose step does not
match its saved marker. The database after each of the three is kept
(`player_reads/actions.sqlite3.gz`, `responses.sqlite3.gz`, `mismatch.sqlite3.gz`), and the reads
are made on each: `canonical_replay` at several acknowledged sequences (and a negative
one); `player_action_context`; `player_action_response_context` for several saved bases;
`action_sequence_response_results` for the queue's basis, any basis, another basis, a session
that does not exist and a Builder session. Last, Python's owner is given a live narration state
(two attempts, one terminal, and a pending tool) and `canonical_replay` projects it.

Values are written as the snapshot vectors write them. It writes
`player_reads/player_read_runs.json`; `tests/player_reads.rs` replays every read on the Rust
store and the projection on a Rust narration state. Reference tooling only.
"""

from __future__ import annotations

import dataclasses
import enum
import gzip
import json
import sqlite3
import sys
import tempfile
from collections.abc import Mapping, Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))

from sylvia_gm.application.authority import AuthorityOwner, _NarrationAttempt  # noqa: E402
from sylvia_gm.application.player_actions import PLAYER_ACTION_ORIGIN, player_control_origin  # noqa: E402
from sylvia_gm.domain.authority import (  # noqa: E402
    ActionStatus,
    MechanicOutcome,
    PlayerProjection,
    PlayerProjectionKind,
    ProseChannel,
    WriterRole,
)
from sylvia_gm.domain.session import SessionMessageKind  # noqa: E402
from sylvia_gm.infrastructure import sqlite_authority as a  # noqa: E402

OUT = HERE / "player_reads"
BASE = REPOSITORY / "crates" / "sylvia-store" / "tests" / "vectors" / "stored_authority" / "failed.sqlite3.gz"
STORY = "campaign-story"
EVIDENCE = SessionMessageKind.EVIDENCE
POLICY = {"confirmation": "none", "response": "on_request"}


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


def outcome(read) -> dict[str, object]:  # noqa: ANN001
    try:
        return {"ok": plain(read())}
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}


def commit(store: a.SQLiteAuthorityStore, action_id: str, source: dict[str, object], *,
           session_id: str | None) -> None:
    """One committed player action with the owner's marker, as the owner commits a direct one."""
    connection = store._connect()  # noqa: SLF001
    try:
        package_id, version, sha256 = connection.execute(
            "SELECT package_id, package_version, package_sha256 FROM actions WHERE action_id = 'action-1'"
        ).fetchone()
    finally:
        connection.close()
    revision = store.snapshot(STORY).revision
    parameters = {"ability": "wisdom", "dc": 10}
    origin = player_control_origin(source, expected_revision=revision, selection={}, session_id=session_id)
    marker = {"kind": PLAYER_ACTION_ORIGIN, "origin": {**origin, "policy": POLICY}}
    payload_hash = a._stored_action_hash(  # noqa: SLF001
        campaign_id=STORY, action_id=action_id, writer="dm", expected_revision=revision, turn_id="turn-2",
        package_id=package_id, package_version=version, package_sha256=sha256, tool_name="roll_check",
        parameters=parameters)
    store.commit_action(
        campaign_id=STORY, action_id=action_id, idempotency_key=f"key-{action_id}", payload_hash=payload_hash,
        expected_revision=revision, writer=WriterRole.DM, turn_id="turn-2", package_id=package_id,
        package_version=version, package_sha256=sha256, tool_name="roll_check", parameters=parameters,
        outcome=MechanicOutcome(result={"total": 12, "success": True, "spatial_evidence": {"kept": False}},
                                mutations=(), consequences=(marker,)),
        status=ActionStatus.COMMITTED)


def evidence(owner: AuthorityOwner, message_id: str, content: dict[str, object]) -> None:
    owner.append_agent_message(session_id="dm-1", message_id=message_id, kind=EVIDENCE, content=content)


def queue(sequence_id: str, step_source: dict[str, object], revision: int, *, operation_id: str) -> dict[str, object]:
    return {"kind": "action_sequence", "sequence_id": sequence_id, "campaign_id": STORY, "session_id": "dm-1",
            "narration_basis_message_id": "response-queue", "narration_attempt_id": f"att-{sequence_id}",
            "status": "completed", "version": 1,
            "steps": [{"operation_id": operation_id,
                       "execution": {"source": step_source, "expected_revision": revision, "selection": {}}}]}


CARD = {"kind": "result_card", "action_id": "action-1", "button_index": 0}


def play(owner: AuthorityOwner) -> None:
    """The two more player actions and the narration between them (the `actions` state)."""
    store = owner._store  # noqa: SLF001
    commit(store, "action-3", CARD, session_id=None)
    event = store.append_narration_segment(campaign_id=STORY, turn_id="turn-2", attempt_id="att-3",
                                           ordinal=0, channel=ProseChannel.TALK, text="The tide turns.")
    store.append_canonical_references("dm-1", [event])
    commit(store, "action-4", CARD, session_id="dm-1")


def respond(owner: AuthorityOwner) -> None:
    """The saved responses, their retry and turn activity, and the bound queue (`responses`)."""
    store = owner._store  # noqa: SLF001
    sequence_revision = next(action.expected_revision for action in store.snapshot(STORY).actions
                             if action.action_id == "action-4")
    boundary = next(event.sequence for event in store.snapshot(STORY).events
                    if event.event_type.value == "talk" and event.attempt_id == "att-3")
    evidence(owner, "response-ids", {"kind": "player_action_response", "player_action_ids": ["action-3", "missing", 5],
                                     "attempt_id": "att-r1"})
    evidence(owner, "response-window", {"kind": "player_action_response", "canonical_start_sequence": boundary})
    evidence(owner, "response-one", {"kind": "player_action_response", "player_action_id": "action-2",
                                     "attempt_id": "att-r3"})
    evidence(owner, "retry-pr", {"kind": "player_action_response_retry", "basis_message_id": "response-ids",
                                "attempt_id": "att-retry"})
    evidence(owner, "turn-activity-pr", {"kind": "gm_turn_activity", "basis_message_id": "response-one",
                                   "attempt_id": "att-activity"})
    evidence(owner, "queue-1", queue("seq-1", CARD, sequence_revision, operation_id="action-4"))


def mismatch(owner: AuthorityOwner) -> None:
    """A second bound queue whose step does not match its action's saved marker (`mismatch`)."""
    revision = owner.snapshot(STORY).revision
    evidence(owner, "queue-2", queue("seq-2", {"kind": "result_card", "action_id": "action-1", "button_index": 1},
                                     revision, operation_id="action-3"))


def reads(owner: AuthorityOwner) -> list[dict[str, object]]:
    high = owner.snapshot(STORY).events[-1].sequence
    runs: list[dict[str, object]] = []
    for acknowledged in (0, 7, high - 1, high, -1):
        runs.append({"read": "canonical_replay", "acknowledged_sequence": acknowledged,
                     "outcome": outcome(lambda: owner.canonical_replay(STORY, acknowledged_sequence=acknowledged))})
    runs.append({"read": "player_action_context", "outcome": outcome(lambda: owner.player_action_context(STORY))})
    for basis in ({"player_action_ids": ["action-3", "missing", 5]}, {"player_action_ids": "action-3"},
                  {"canonical_start_sequence": 9}, {"canonical_start_sequence": True},
                  {"canonical_start_sequence": 9.0, "player_action_id": "action-4"},
                  {"player_action_id": "action-2"}, {}):
        runs.append({"read": "player_action_response_context", "basis": basis,
                     "outcome": outcome(lambda: owner.player_action_response_context(STORY, basis))})
    for session_id, basis_id in (("dm-1", "response-queue"), ("dm-1", None), ("dm-1", "other"),
                                 ("missing-session", None), ("builder-1", None)):
        runs.append({"read": "action_sequence_response_results", "session_id": session_id,
                     "basis_message_id": basis_id,
                     "outcome": outcome(lambda: owner.action_sequence_response_results(
                         session_id=session_id, basis_message_id=basis_id))})
    return runs


def provisional(owner: AuthorityOwner) -> dict[str, object]:
    """A live narration state set on Python's owner, and the replay that projects it."""
    pending = PlayerProjection(kind=PlayerProjectionKind.PENDING_TOOL, sequence=None,
                               safe_payload={"package_id": "synthetic.actions", "tool_name": "roll_check",
                                             "parameters": {"dc": 10}},
                               collapsed_by_default=True, provisional=True, action_id="action-live")
    attempts = [
        _NarrationAttempt(campaign_id=STORY, attempt_id="att-think", turn_id="turn-2", cancel=lambda: None,
                          last_action_id=None, channel=ProseChannel.SAFE_THINK, buffer="Weighing the tide."),
        _NarrationAttempt(campaign_id=STORY, attempt_id="att-talk", turn_id="turn-2", cancel=lambda: None,
                          last_action_id="action-4", channel=ProseChannel.TALK, buffer="The gulls scatter."),
        _NarrationAttempt(campaign_id=STORY, attempt_id="att-done", turn_id="turn-2", cancel=lambda: None,
                          last_action_id=None, channel=ProseChannel.TALK, buffer="Finished.", terminal=True),
        _NarrationAttempt(campaign_id=STORY, attempt_id="att-empty", turn_id="turn-2", cancel=lambda: None,
                          last_action_id=None, channel=ProseChannel.TALK, buffer=""),
        _NarrationAttempt(campaign_id="campaign-build", attempt_id="att-other", turn_id="turn-b",
                          cancel=lambda: None, last_action_id=None, channel=ProseChannel.TALK, buffer="Elsewhere."),
    ]
    with owner._narration_lock:  # noqa: SLF001
        owner._pending_tools[(STORY, "action-live")] = pending  # noqa: SLF001
        owner._pending_tools[("campaign-build", "action-far")] = pending  # noqa: SLF001
        for index, attempt in enumerate(attempts):
            owner._attempts[(attempt.campaign_id, f"session-{index}")] = attempt  # noqa: SLF001
    try:
        high = owner.snapshot(STORY).events[-1].sequence
        replay = outcome(lambda: owner.canonical_replay(STORY, acknowledged_sequence=high))
    finally:
        with owner._narration_lock:  # noqa: SLF001
            owner._pending_tools.clear()  # noqa: SLF001
            owner._attempts.clear()  # noqa: SLF001
    return {
        "pending_tools": [[list(key), plain(item)] for key, item in
                          (((STORY, "action-live"), pending), (("campaign-build", "action-far"), pending))],
        "attempts": [[[attempt.campaign_id, f"session-{index}"], {
            "campaign_id": attempt.campaign_id, "attempt_id": attempt.attempt_id, "turn_id": attempt.turn_id,
            "last_action_id": attempt.last_action_id,
            "channel": None if attempt.channel is None else attempt.channel.value,
            "buffer": attempt.buffer, "terminal": attempt.terminal}] for index, attempt in enumerate(attempts)],
        "outcome": replay,
    }


def checkpoint(database: Path) -> bytes:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return database.read_bytes()


def main() -> None:
    OUT.mkdir(exist_ok=True)
    phases = []
    live: dict[str, object] = {}
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        database = Path(scratch) / "authority.sqlite3"
        database.write_bytes(gzip.decompress(BASE.read_bytes()))
        for name, change in (("actions", play), ("responses", respond), ("mismatch", mismatch)):
            with AuthorityOwner.open(database, "player-reads") as owner:
                change(owner)
            (OUT / f"{name}.sqlite3.gz").write_bytes(gzip.compress(checkpoint(database), mtime=0))
            with AuthorityOwner.open(database, "player-reads") as owner:
                phases.append({"database": name, "runs": reads(owner)})
                if name == "mismatch":
                    live = provisional(owner)
    record = {"python": sys.version.split()[0], "phases": phases, "provisional": live}
    (OUT / "player_read_runs.json").write_bytes((json.dumps(record, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
