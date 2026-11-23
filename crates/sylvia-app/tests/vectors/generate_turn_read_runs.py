"""The reads a Game Master provider round makes past the opening, and the private provider
records: what Python's owner and `sqlite_reasoning` answer.

Run with the snapshot's own interpreter, from the repository root, after the store generators
whose profiles are the bases and `generate_snapshot_runs.py` (its Riichi game is a base):

    .venv/Scripts/python.exe -B crates/sylvia-app/tests/vectors/generate_turn_read_runs.py

Five Python-made profiles: the Riichi participant profile (`cast`), the same with a Riichi game
in play (`riichi_started`), the campaign-reads profile (`campaigns`), the session-page profile
(`session_pages`), and one this generator makes (`narrative`, written as
`turn_reads/narrative.sqlite3.gz`): a DM campaign whose accepted Campaign Plan has acts, an
objective repeating an act's identity, entries without a text identity, secrets (one revealed to
the player, one to the GM only, one never) and a finite ending. For every campaign, and one that does not exist: `current_package_lock` and
`campaign_preparation_boundary`. For every session, and one that does not exist:
`campaign_narrative_options`.

Then a scripted run on the session-page profile, each write in its own transaction on one of
Python's store connections, as the owner makes them: `append_record` (thinking chunks, a replay,
a conflicting replay, native continuations, refused records, a Builder chat's record, actor
context records), `finish_attempt`, then `inspection_index`, `read_thinking`,
`provider_continuation` (found, missing, for another model, ambiguous) and `actor_context`
(assembled, missing, an invalid identity). Values are plain JSON as `generate_snapshot_runs.py`
writes them; a failure is its class, code and message. It writes
`turn_reads/turn_read_runs.json`; `tests/turn_reads.rs` replays it on the Rust store. Reference
tooling only.
"""

from __future__ import annotations

import dataclasses
import enum
import gzip
import hashlib
import io
import json
import random
import sqlite3
import sys
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPOSITORY = HERE.parents[3]
sys.path.insert(0, str(REPOSITORY / "src"))
sys.path.insert(0, str(REPOSITORY / "crates" / "sylvia-store" / "tests" / "vectors"))

_IDENTITIES = random.Random(20261123)
uuid.uuid4 = lambda: uuid.UUID(int=_IDENTITIES.getrandbits(128), version=4)  # type: ignore[assignment]

from generate_action_runs import synthetic_archive  # noqa: E402

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate  # noqa: E402
from sylvia_gm.domain.authority import CampaignContinuityCommand, WriterRole  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes  # noqa: E402
from sylvia_gm.domain.session import SessionMessageKind  # noqa: E402
from sylvia_gm.infrastructure import sqlite_reasoning  # noqa: E402

OUT = HERE / "turn_reads"
STORE = REPOSITORY / "crates" / "sylvia-store" / "tests" / "vectors"
BASES = {
    "cast": STORE / "stored_authority" / "cast.sqlite3.gz",
    "riichi_started": HERE / "snapshots" / "riichi_started.sqlite3.gz",
    "campaigns": STORE / "campaigns" / "seed.sqlite3.gz",
    "session_pages": STORE / "session_pages" / "seed.sqlite3.gz",
    "narrative": HERE / "turn_reads" / "narrative.sqlite3.gz",
}
CONTEXT = "ab" * 32
NARRATIVE = "campaign-narrative"
PROVIDER, MODEL = "synthetic.provider", "synthetic.provider/model"
PLAN = {"campaign_plan": {
    "title": "The Salt Road",
    "bindings": {"ruleset": None, "campaign_setting": None},
    "structure": [{"id": "act-1"}, {"id": "act-2"}, "an interlude", {"id": 3}, {"name": "no identity"}],
    "objectives": [{"id": "obj-1"}, {"id": "act-1"}, {"id": "obj-1"}, "loose", {"id": None}],
    "secrets": [
        {"id": "secret-told", "fact": "The miller is the thief."},
        {"id": "secret-gm", "fact": "The road is older than the town."},
        {"id": "secret-kept", "fact": "The well is dry.", "known_by": ["npc-ada"]},
        "A loose secret.",
        {"fact": "No identity."},
    ],
    "ending": {"mode": "finite", "outcomes": [
        {"id": "end-home", "open_world": False, "after_story": None, "requires_all_acts": True},
        {"id": "end-road", "open_world": True, "after_story": "The road goes on.", "requires_all_acts": False}]},
}}


def narrative_seed(folder: Path) -> bytes:
    """The `narrative` profile, made by Python's owner and store."""
    database = folder / "narrative-seed" / "authority.sqlite3"
    database.parent.mkdir()
    digest = hashlib.sha256(canonical_json_bytes(PLAN)).hexdigest()
    with AuthorityOwner.open(database, "turn-read-narrative") as owner:
        store = owner._store  # noqa: SLF001 - the continuity batch is the store's own write
        owner.prepare_new_campaign_storage()
        candidate = owner.install_package_archive(io.BytesIO(synthetic_archive()), install_id="install-narrative")
        owner.create_campaign(CampaignCreate(display_name="Narrative", writer=WriterRole.BUILDER,
                                             package_lock=candidate.package_lock, turn_id="turn-build",
                                             campaign_id=NARRATIVE))
        owner.create_agent_session(campaign_id=NARRATIVE, role=WriterRole.BUILDER, provider_id=PROVIDER,
                                   model_id=MODEL, session_id="builder-n", selected_skill="BUILD CAMPAIGN PLAN")
        owner.append_agent_message(session_id="builder-n", message_id="ev-plan", kind=SessionMessageKind.EVIDENCE,
                                   content={"kind": "plan_draft"})
        lock_id = store.current_package_lock(NARRATIVE).lock_id
        owner.admit_campaign_plan(
            plan_id="plan-n", campaign_id=NARRATIVE, builder_session_id="builder-n", expected_revision=0,
            expected_package_lock_id=lock_id, content=PLAN, content_sha256=digest,
            provenance_evidence_ids=("ev-plan",), validation_outcome="valid", acceptance_status="accepted")
        owner.complete_turn(NARRATIVE, "turn-build")
        owner.handoff_builder_to_dm(
            handoff_id="handoff-n", campaign_id=NARRATIVE, builder_session_id="builder-n", dm_session_id="dm-n",
            dm_provider_id=PROVIDER, dm_model_id=MODEL, expected_writer=WriterRole.BUILDER,
            expected_revision=1, expected_package_lock_id=lock_id,
            expected_package_candidate_id=candidate.candidate_id, plan_id="plan-n", expected_plan_revision=1,
            expected_plan_content_sha256=digest, completed_turn_id="turn-build", model_guidance_override=True)
        owner.open_turn(NARRATIVE, "turn-play")
        owner.append_agent_message(session_id="dm-n", message_id="u-1", kind=SessionMessageKind.USER,
                                   content={"text": "I ask the miller."})
        revision = store.snapshot(NARRATIVE).revision
        store.commit_campaign_continuity_batch([
            CampaignContinuityCommand(
                campaign_id=NARRATIVE, operation_id=f"op-reveal-{index}", session_id="dm-n",
                expected_revision=revision + index, kind="reveal", record_id=secret, status="active",
                payload={"secret_id": secret, "audience": audience}, basis_message_ids=("u-1",),
                turn_id="turn-play")
            for index, (secret, audience) in enumerate((("secret-told", ["player", "gm"]),
                                                        ("secret-gm", ["gm"])))
        ])
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return database.read_bytes()


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


def record(session: str, attempt: str, order: int, *, response: str = "response-1", kind: str = "thinking",
           payload: str = "Thinking.", provider: str = "openai", model: str = "gpt-test") -> dict[str, object]:
    return {"session_id": session, "attempt_id": attempt, "response_id": response, "turn_id": "turn-1",
            "basis_message_id": "message-1", "source_order": order, "provider_id": provider,
            "model_id": model, "kind": kind, "payload": payload}


def actor_payload(messages: list[object], *, context_id: str = CONTEXT) -> str:
    return json.dumps({"actor_context": {"format": 1, "context_id": context_id, "actor_id": "aki",
                                         "messages": messages}})


def script() -> list[dict[str, object]]:
    dm, chat = "dm-1", "chat-1"
    steps: list[dict[str, object]] = [
        {"op": "append", "record": record(dm, "attempt-1", 0, payload="First, ")},
        {"op": "append", "record": record(dm, "attempt-1", 1, payload="then.")},
        {"op": "append", "record": record(dm, "attempt-1", 1, payload="then.")},
        {"op": "append", "record": record(dm, "attempt-1", 1, payload="other.")},
        {"op": "append", "record": record(dm, "attempt-1", 2, kind="continuation",
                                          payload=json.dumps({"output": [{"type": "reasoning", "id": "rs_1"}]}))},
        {"op": "append", "record": record(dm, "attempt-1", 3, kind="continuation", payload="[1, 2]")},
        {"op": "append", "record": record(dm, "attempt-1", 3, kind="continuation", payload="{not json")},
        {"op": "append", "record": record(dm, "attempt-1", 3, kind="summary")},
        {"op": "append", "record": record(dm, "attempt-1", -1)},
        {"op": "append", "record": record(dm, "", 3)},
        {"op": "append", "record": record("missing-session", "attempt-1", 0)},
        {"op": "append", "record": record(chat, "attempt-chat", 0, payload="Builder thought.")},
        {"op": "append", "record": record(dm, "attempt-2", 0, response="response-2", payload="Second.")},
        {"op": "append", "record": record(dm, "attempt-3", 0, response="response-1", kind="continuation",
                                          payload=json.dumps({"again": True}))},
        {"op": "append", "record": record(dm, "attempt-4", 0, response=f"actor-context-{CONTEXT}-1",
                                          kind="continuation",
                                          payload=actor_payload([{"role": "user", "content": "Your turn."}]))},
        {"op": "append", "record": record(dm, "attempt-4", 1, response=f"actor-context-{CONTEXT}-2",
                                          kind="continuation",
                                          payload=actor_payload([{"role": "assistant", "content": "Pass."}]))},
        {"op": "finish", "session_id": dm, "attempt_id": "attempt-1", "completed": True},
        {"op": "finish", "session_id": dm, "attempt_id": "attempt-2", "completed": False},
        {"op": "finish", "session_id": dm, "attempt_id": "attempt-none", "completed": True},
        {"op": "append", "record": record(dm, "attempt-1", 9, payload="late")},
        {"op": "append", "record": record(dm, "attempt-1", 0, payload="First, ")},
    ]
    for session in (dm, chat, "missing-session"):
        steps.append({"op": "inspection_index", "session_id": session})
    for attempt, response in (("attempt-1", "response-1"), ("attempt-2", "response-2"), ("attempt-1", "response-9")):
        steps.append({"op": "read_thinking", "session_id": dm, "attempt_id": attempt, "response_id": response})
    for response, model in (("response-1", "gpt-test"), ("response-2", "gpt-test"), ("response-1", "other")):
        steps.append({"op": "provider_continuation", "session_id": dm, "response_id": response,
                      "provider_id": "openai", "model_id": model})
    steps.append({"op": "append", "record": record(dm, "attempt-5", 0, response="response-5", kind="continuation",
                                                   payload=json.dumps({"only": 1}))})
    steps.append({"op": "provider_continuation", "session_id": dm, "response_id": "response-5",
                  "provider_id": "openai", "model_id": "gpt-test"})
    for context_id, model in ((CONTEXT, "gpt-test"), (CONTEXT, "other"), ("cd" * 32, "gpt-test"),
                              ("not-hex", "gpt-test"), ("AB" * 32, "gpt-test")):
        steps.append({"op": "actor_context", "session_id": dm, "context_id": context_id,
                      "provider_id": "openai", "model_id": model})
    steps.append({"op": "append", "record": record(dm, "attempt-6", 0, response=f"actor-context-{'ef' * 32}-1",
                                                   kind="continuation",
                                                   payload=actor_payload([], context_id="ef" * 31 + "00"))})
    steps.append({"op": "actor_context", "session_id": dm, "context_id": "ef" * 32,
                  "provider_id": "openai", "model_id": "gpt-test"})
    return steps


def run_step(owner: AuthorityOwner, step: dict[str, object]) -> dict[str, object]:
    store = owner._store  # noqa: SLF001 - the owner's own store, as its operations use it
    kind = step["op"]
    if kind in ("append", "finish"):
        def write() -> None:
            connection = store._connect()  # noqa: SLF001
            try:
                with connection:
                    if kind == "append":
                        sqlite_reasoning.append_record(connection, **step["record"])  # type: ignore[arg-type]
                    else:
                        sqlite_reasoning.finish_attempt(connection, str(step["session_id"]), str(step["attempt_id"]),
                                                        completed=bool(step["completed"]))
            finally:
                connection.close()
        return outcome(write)
    if kind in ("inspection_index", "read_thinking"):
        def read() -> object:
            connection = store._connect_read_only()  # noqa: SLF001
            try:
                if kind == "inspection_index":
                    return sqlite_reasoning.inspection_index(connection, str(step["session_id"]))
                return sqlite_reasoning.read_thinking(connection, str(step["session_id"]), str(step["attempt_id"]),
                                                      str(step["response_id"]))
            finally:
                connection.close()
        return outcome(read)
    if kind == "provider_continuation":
        return outcome(lambda: owner.provider_continuation(
            str(step["session_id"]), str(step["response_id"]), provider_id=str(step["provider_id"]),
            model_id=str(step["model_id"])))
    return outcome(lambda: owner.actor_context(
        str(step["session_id"]), str(step["context_id"]), provider_id=str(step["provider_id"]),
        model_id=str(step["model_id"])))


def main() -> None:
    OUT.mkdir(exist_ok=True)
    runs = []
    records = []
    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as scratch:
        BASES["narrative"].write_bytes(gzip.compress(narrative_seed(Path(scratch)), mtime=0))
        for base, source in BASES.items():
            database = Path(scratch) / base / "authority.sqlite3"
            database.parent.mkdir()
            database.write_bytes(gzip.decompress(source.read_bytes()))
            connection = sqlite3.connect(database)
            campaigns = [row[0] for row in connection.execute("SELECT campaign_id FROM campaigns ORDER BY campaign_id")]
            sessions = [row[0] for row in connection.execute("SELECT session_id FROM agent_sessions ORDER BY session_id")]
            connection.close()
            with AuthorityOwner.open(database, "turn-read-runs") as owner:
                for campaign_id in [*campaigns, "campaign-missing"]:
                    for read in ("current_package_lock", "campaign_preparation_boundary"):
                        runs.append({"base": base, "read": read, "campaign_id": campaign_id,
                                     "outcome": outcome(lambda: getattr(owner, read)(campaign_id))})
                for session_id in [*sessions, "session-missing"]:
                    runs.append({"base": base, "read": "campaign_narrative_options", "session_id": session_id,
                                 "outcome": outcome(lambda: owner.campaign_narrative_options(session_id))})
                if base == "session_pages":
                    for step in script():
                        records.append({**step, "outcome": run_step(owner, step)})
    record_out = {"python": sys.version.split()[0], "runs": runs, "private_records": records}
    (OUT / "turn_read_runs.json").write_bytes((json.dumps(record_out, indent=1, ensure_ascii=True) + "\n").encode("utf-8"))


if __name__ == "__main__":
    main()
