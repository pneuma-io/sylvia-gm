"""Paired runs for sessions part 2: history pages, activity bases, plans, review and children.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -B crates/sylvia-store/tests/vectors/generate_session_page_runs.py

The starting database is made through the Python owner and store, with one small synthetic
package:
- `campaign-story` went from Builder to DM through an accepted Campaign Plan and the handoff,
  which seeded its continuity (scene, facts, a No Ruleset entity). The DM session `dm-1` then
  played two inputs with saved narration, a committed action with its mechanics activity, Plan
  progress that completes both acts and reaches the ending, and an ending choice. Python's
  startup verifier admits only reviewed sequels, so the multi-adventure lineage is proven by
  `generate_plan_reader_vectors.py` instead.
- `campaign-build` is a Builder campaign whose session `builder-2` holds Builder attempts, an
  `ask_question` result and a compaction.
- `chat-1` is a standalone Builder chat with launch evidence and attempts.

It writes, under `session_pages/`, `seed.sqlite3.gz`, `<run>.sqlite3.gz` (the database after
each run) and `session_page_runs.json` (the operations and Python's outcome of each). Identities
are explicit and the owner's UUIDs are seeded, so a re-run writes byte-identical files.
Reference tooling only.
"""

from __future__ import annotations

import gzip
import hashlib
import io
import json
import random
import shutil
import sqlite3
import sys
import tempfile
import uuid
from collections.abc import Mapping, Sequence
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[4] / "src"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

_IDENTITIES = random.Random(20261125)
uuid.uuid4 = lambda: uuid.UUID(int=_IDENTITIES.getrandbits(128), version=4)  # type: ignore[assignment]

from generate_action_runs import synthetic_archive  # noqa: E402

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate  # noqa: E402
from sylvia_gm.domain.authority import (  # noqa: E402
    ActionStatus,
    CampaignContinuityCommand,
    MechanicOutcome,
    PackageRole,
    ProseChannel,
    StateMutation,
    WriterRole,
)
from sylvia_gm.domain.model_capability import model_guidance_payload  # noqa: E402
from sylvia_gm.domain.package_library import canonical_json_bytes  # noqa: E402
from sylvia_gm.domain.session import (  # noqa: E402
    ChildEvidence,
    SessionMessageKind,
    SessionRecord,
    SessionStatus,
)
from sylvia_gm.infrastructure import sqlite_authority as a  # noqa: E402

OUT = Path(__file__).resolve().parent / "session_pages"
STORY, BUILD = "campaign-story", "campaign-build"
PROVIDER, MODEL = "synthetic.provider", "synthetic.provider/model"
EVIDENCE, USER = SessionMessageKind.EVIDENCE, SessionMessageKind.USER

PLAN_1 = {"campaign_plan": {
    "title": "The Drowned Bell",
    "bindings": {"ruleset": None, "campaign_setting": None},
    "structure": [{"id": "act-1", "objectives": ["obj-1"]}, {"id": "act-2"}],
    "starting_location": {"id": "harbour", "name": "The harbour"},
    "initial_state": {
        "entities": [{"id": "keeper", "kind": "npc",
                      "source_ref": {"id": "keeper", "path": "entities/keeper"},
                      "state": {"name": "The Keeper", "visibility": "player_known", "mood": "wary"}}],
        "facts": ["The bell rang at dawn.",
                  {"id": "fact-tide", "fact": "The tide is wrong.", "known_by": ["gm"]},
                  {"fact": "Gulls avoid the tower.", "known_by": "player"}, 7],
    },
    "ending": {"mode": "finite", "outcomes": [
        {"id": "end-quiet", "open_world": False, "after_story": None, "requires_all_acts": True},
        {"id": "end-loud", "open_world": True, "after_story": "The town remembers.", "requires_all_acts": False}]},
}}


def sha(content: object) -> str:
    return hashlib.sha256(canonical_json_bytes(content)).hexdigest()


def say(store: a.SQLiteAuthorityStore, owner: AuthorityOwner, *, turn: str, user_id: str,
        attempt: str, text: str, content: dict[str, object] | None = None) -> None:
    """One player input and the GM's saved narration for it."""
    owner.append_agent_message(session_id="dm-1", message_id=user_id, kind=USER,
                               content=content or {"text": text})
    event = store.append_narration_segment(campaign_id=STORY, turn_id=turn, attempt_id=attempt,
                                           ordinal=0, channel=ProseChannel.TALK, text=f"{text} The sea answers.")
    store.append_canonical_references("dm-1", [event])


def continuity(store: a.SQLiteAuthorityStore, turn: str, *items: tuple[str, str, str, dict[str, object], str]) -> None:
    revision = store.snapshot(STORY).revision
    store.commit_campaign_continuity_batch([
        CampaignContinuityCommand(
            campaign_id=STORY, operation_id=op, session_id="dm-1", expected_revision=revision + index,
            kind=kind, record_id=record_id, status="active", payload=payload,
            basis_message_ids=(basis,), turn_id=turn)
        for index, (op, kind, record_id, payload, basis) in enumerate(items)
    ])


def progress(item: str) -> dict[str, object]:
    return {"plan_item_id": item, "status": "resolved"}


def seed(folder: Path) -> Path:
    database = folder / "authority.sqlite3"
    with AuthorityOwner.open(database, "session-page-runs") as owner:
        store = owner._store
        owner.prepare_new_campaign_storage()
        candidate = owner.install_package_archive(io.BytesIO(synthetic_archive()), install_id="install-pages")
        # The story campaign: Builder, accepted Plan, handoff to the DM.
        owner.create_campaign(CampaignCreate(display_name="Story", writer=WriterRole.BUILDER,
                                             package_lock=candidate.package_lock, turn_id="turn-build",
                                             campaign_id=STORY))
        owner.create_agent_session(campaign_id=STORY, role=WriterRole.BUILDER, provider_id=PROVIDER,
                                   model_id=MODEL, session_id="builder-1", selected_skill="BUILD CAMPAIGN PLAN")
        owner.append_agent_message(session_id="builder-1", message_id="ev-plan", kind=EVIDENCE,
                                   content={"kind": "plan_draft"})
        lock_id = store.current_package_lock(STORY).lock_id
        owner.admit_campaign_plan(
            plan_id="plan-1", campaign_id=STORY, builder_session_id="builder-1", expected_revision=0,
            expected_package_lock_id=lock_id, content=PLAN_1, content_sha256=sha(PLAN_1),
            provenance_evidence_ids=("ev-plan",), validation_outcome="valid", acceptance_status="accepted")
        owner.complete_turn(STORY, "turn-build")
        owner.handoff_builder_to_dm(
            handoff_id="handoff-1", campaign_id=STORY, builder_session_id="builder-1", dm_session_id="dm-1",
            dm_provider_id=PROVIDER, dm_model_id=MODEL, expected_writer=WriterRole.BUILDER,
            expected_revision=1, expected_package_lock_id=lock_id,
            expected_package_candidate_id=candidate.candidate_id, plan_id="plan-1", expected_plan_revision=1,
            expected_plan_content_sha256=sha(PLAN_1), completed_turn_id="turn-build",
            model_guidance_override=True)
        # Play: two inputs with narration, an action with its activity, Plan progress, a choice.
        owner.open_turn(STORY, "turn-2")
        say(store, owner, turn="turn-2", user_id="u-1", attempt="att-1", text="I ring the bell.")
        continuity(store, "turn-2",
                   ("op-1", "plan_progress", "act-1", progress("act-1"), "u-1"),
                   ("op-2", "plan_progress", "obj-1", progress("obj-1"), "u-1"),
                   ("op-3", "scene", "current_scene", {"act_id": "act-2", "summary": "Below."}, "u-1"))
        say(store, owner, turn="turn-2", user_id="u-2", attempt="att-2", text="I dive.")
        primary = next(e for e in candidate.package_lock.entries if e.role is PackageRole.PRIMARY)
        revision = store.snapshot(STORY).revision
        parameters = {"ability": "dexterity", "dc": 12}
        payload_hash = a._stored_action_hash(
            campaign_id=STORY, action_id="action-1", writer="dm", expected_revision=revision,
            turn_id="turn-2", package_id=primary.package_id, package_version=primary.version,
            package_sha256=primary.sha256, tool_name="roll_check", parameters=parameters)
        store.commit_action(
            campaign_id=STORY, action_id="action-1", idempotency_key="key-1", payload_hash=payload_hash,
            expected_revision=revision, writer=WriterRole.DM, turn_id="turn-2",
            package_id=primary.package_id, package_version=primary.version, package_sha256=primary.sha256,
            tool_name="roll_check", parameters=parameters,
            outcome=MechanicOutcome(result={"total": 15, "success": True},
                                    mutations=(StateMutation(f"{primary.package_id}.checks", "last", {"total": 15}),),
                                    consequences=({"kind": "check_rolled", "total": 15},)),
            status=ActionStatus.COMMITTED)
        connection = store._connect()
        try:
            events = [store._event_from_row(row) for row in connection.execute(
                "SELECT * FROM canonical_events WHERE campaign_id = ? AND action_id = 'action-1' "
                "AND event_type = 'tool_result' ORDER BY sequence", (STORY,))]
        finally:
            connection.close()
        store.append_canonical_references("dm-1", events)
        owner.append_agent_message(session_id="dm-1", message_id="activity-1", kind=EVIDENCE, content={
            "kind": "mechanics_activity", "action_id": "action-1", "activity": {"label": "Dexterity", "total": 15}})
        continuity(store, "turn-2",
                   ("op-4", "plan_progress", "act-2", progress("act-2"), "u-2"),
                   ("op-5", "plan_progress", "ending:end-quiet", progress("ending:end-quiet"), "u-2"))
        owner.append_agent_message(session_id="dm-1", message_id="u-3", kind=USER, content={
            "text": "Let it end.", "campaign_ending_choice": {"ending_id": "end-quiet", "choice": "epilogue"}})
        # A Builder campaign with attempts, a question form and a compaction.
        owner.create_campaign(CampaignCreate(display_name="Build", writer=WriterRole.BUILDER,
                                             package_lock=candidate.package_lock, turn_id="turn-b",
                                             campaign_id=BUILD))
        owner.create_agent_session(campaign_id=BUILD, role=WriterRole.BUILDER, provider_id=PROVIDER,
                                   model_id=MODEL, session_id="builder-2", selected_skill="BUILD CAMPAIGN PLAN")
        for message_id, kind, content in (
            ("b-user-1", USER, {"text": "Make a coast.", "attempt_id": "a-1"}),
            ("b-question-old", EVIDENCE, {"kind": "builder_tool_result", "tool_name": "ask_question", "q": 1}),
            ("b-terminal-1", EVIDENCE, {"kind": "builder_attempt_terminal", "attempt_id": "a-1", "status": "completed"}),
            ("b-user-2", USER, {"text": "Now a tower.", "attempt_id": "a-2"}),
            ("b-question", EVIDENCE, {"kind": "builder_tool_result", "tool_name": "ask_question", "q": 2}),
            ("b-other-tool", EVIDENCE, {"kind": "builder_tool_result", "tool_name": "search"}),
            ("b-compact", EVIDENCE, {"kind": "context_compaction_requested", "attempt_id": "c-1"}),
            ("b-compact-end", EVIDENCE, {"kind": "context_compaction_terminal", "attempt_id": "c-1",
                                         "status": "completed"}),
        ):
            owner.append_agent_message(session_id="builder-2", message_id=message_id, kind=kind, content=content)
        # A standalone Builder chat.
        owner.create_builder_chat(provider_id=PROVIDER, model_id=MODEL, session_id="chat-1")
        for message_id, kind, content in (
            ("c-launch", EVIDENCE, {"kind": "builder_launch_context", "source": "library"}),
            ("c-start", EVIDENCE, {"kind": "builder_start", "attempt_id": "s-1"}),
            ("c-link-1", EVIDENCE, {"kind": "creation_subflow_link", "n": 1}),
            ("c-link-2", EVIDENCE, {"kind": "creation_subflow_link", "n": 2}),
            ("c-user", USER, {"text": "Hello.", "attempt_id": 7}),
        ):
            owner.append_agent_message(session_id="chat-1", message_id=message_id, kind=kind, content=content)
    return database


def page(session_id: str, before: int | None = None, limit: int = 50) -> dict[str, object]:
    return {"op": "session_history_page", "session_id": session_id, "before_sequence": before, "limit": limit}


def basis(session_id: str, attempt: str | None = None) -> dict[str, object]:
    return {"op": "session_activity_basis", "session_id": session_id, "attempt_id": attempt}


def child(session_id: str, parent: str | None = "builder-2", skill: str = "BUILD RULESET",
          model: str = MODEL, campaign: str = BUILD) -> dict[str, object]:
    return {"op": "create_builder_child_session", "record": {
        "session_id": session_id, "campaign_id": campaign, "role": "builder", "provider_id": PROVIDER,
        "model_id": model, "status": "active", "parent_session_id": parent, "selected_skill": skill,
        "scope": ["rules"], "reasoning_effort": None, "reasoning_effort_once": None}}


def evidence(evidence_id: str, child_id: str, parent: str = "builder-2") -> dict[str, object]:
    return {"op": "return_builder_child_evidence", "evidence": {
        "evidence_id": evidence_id, "child_session_id": child_id, "parent_session_id": parent,
        "content_sha256": "b" * 64, "safe_summary": "Rules drafted.", "claims": ["rules-ready"]}}


def review(session_id: str, review_id: str) -> dict[str, object]:
    return {"op": "require_agent_human_review", "session_id": session_id, "review_id": review_id,
            "content": {"outcome": "human_review_required", "unsupported_capabilities": ["maps"],
                        "contradictions": []}}


RUNS: dict[str, list[dict[str, object]]] = {
    "pages": [
        page("dm-1"), page("dm-1", limit=3), page("dm-1", before=6, limit=2), page("dm-1", before=1),
        page("dm-1", before=10_000, limit=200), page("dm-1", limit=0), page("dm-1", limit=201),
        page("dm-1", before=0), page("dm-1", before=-3), page("session-missing"),
        page("builder-1"), page("builder-2"), page("builder-2", limit=1), page("chat-1"), page("chat-1", limit=2),
    ],
    "activity": [
        basis("chat-1"), basis("chat-1", "s-1"), basis("chat-1", "missing"), basis("builder-2"),
        basis("builder-2", "a-1"), basis("builder-2", "c-1"), basis("dm-1"), basis("session-missing"),
    ],
    "plans": [
        {"op": "campaign_plan_hit", "plan_id": "plan-1"}, {"op": "campaign_plan_hit", "plan_id": "plan-missing"},
        {"op": "accepted_campaign_plan", "campaign_id": STORY}, {"op": "accepted_campaign_plan", "campaign_id": BUILD},
        {"op": "campaign_adventures", "campaign_id": STORY}, {"op": "campaign_adventures", "campaign_id": BUILD},
        {"op": "handoff_hit", "handoff_id": "handoff-1"}, {"op": "handoff_hit", "handoff_id": "handoff-missing"},
    ],
    "children_and_review": [
        child("child-1"), child("child-1"), child("child-1", model="synthetic.provider/other"),
        child("child-2"), child("child-3"), child("child-4"), child("child-nested", parent="child-1"),
        child("child-dm", parent="dm-1", campaign=STORY), child("child-none", parent=None),
        child("child-of-missing", parent="session-missing"),
        evidence("evidence-1", "child-1"), evidence("evidence-1", "child-1"),
        evidence("evidence-2", "child-2", parent="builder-1"), evidence("evidence-3", "dm-1", parent="builder-2"),
        evidence("evidence-4", "child-missing"),
        review("dm-1", "review-dm"), review("builder-1", "review-closed"), review("builder-2", "review-1"),
        review("builder-2", "review-2"), review("builder-2", "review-1"), review("chat-1", "review-chat"),
        child("child-after-review"), page("builder-2", limit=3),
    ],
}


def ordered(value: object) -> object:
    """Plain JSON in Python's own key order; `_dump` would sort every mapping."""
    if isinstance(value, Mapping):
        return {str(key): ordered(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [ordered(item) for item in value]
    return value


def plain(value: object) -> object:
    a._dump(value)  # canonical JSON still refuses what it refuses
    return ordered(value)


def session_json(record: SessionRecord) -> dict[str, object]:
    return {
        "session_id": record.session_id, "campaign_id": record.campaign_id, "role": record.role.value,
        "provider_id": record.provider_id, "model_id": record.model_id, "status": record.status.value,
        "parent_session_id": record.parent_session_id, "selected_skill": record.selected_skill,
        "scope": list(record.scope), "reasoning_effort": record.reasoning_effort,
        "reasoning_effort_once": record.reasoning_effort_once,
    }


def message_json(message: object) -> dict[str, object]:
    return {"session_id": message.session_id, "sequence": message.sequence,  # type: ignore[attr-defined]
            "message_id": message.message_id, "kind": message.kind.value,  # type: ignore[attr-defined]
            "content": plain(message.content), "canonical_sequence": message.canonical_sequence}  # type: ignore[attr-defined]


def value_json(value: object) -> object:
    if value is None:
        return None
    if isinstance(value, SessionRecord):
        return session_json(value)
    if isinstance(value, tuple):
        return [value_json(item) for item in value]
    name = type(value).__name__
    if name == "SessionHistoryPage":
        return {
            "session": session_json(value.session),  # type: ignore[attr-defined]
            "messages": [message_json(m) for m in value.messages],  # type: ignore[attr-defined]
            "next_before_sequence": value.next_before_sequence,  # type: ignore[attr-defined]
            "latest_sequence": value.latest_sequence,  # type: ignore[attr-defined]
            "latest_user_sequence": value.latest_user_sequence,  # type: ignore[attr-defined]
            "launch_messages": [message_json(m) for m in value.launch_messages],  # type: ignore[attr-defined]
            "latest_canonical_sequence": value.latest_canonical_sequence,  # type: ignore[attr-defined]
            "act_markers": [plain(m) for m in value.act_markers],  # type: ignore[attr-defined]
            "campaign_ending": None if value.campaign_ending is None else plain(value.campaign_ending),  # type: ignore[attr-defined]
            "campaign_endings": [plain(m) for m in value.campaign_endings],  # type: ignore[attr-defined]
            "builder_question_messages": [message_json(m) for m in value.builder_question_messages],  # type: ignore[attr-defined]
        }
    if name == "SessionActivityBasis":
        return {
            "session": session_json(value.session),  # type: ignore[attr-defined]
            "accepted": None if value.accepted is None else message_json(value.accepted),  # type: ignore[attr-defined]
            "terminal": None if value.terminal is None else message_json(value.terminal),  # type: ignore[attr-defined]
            "has_compaction": value.has_compaction,  # type: ignore[attr-defined]
        }
    if name == "CampaignPlan":
        return {key: getattr(value, key) for key in (
            "plan_id", "campaign_id", "package_lock_id", "source_session_id", "content_sha256",
            "plan_revision", "resulting_campaign_revision", "payload_sha256")} | {
            "content": plain(value.content),  # type: ignore[attr-defined]
            "provenance_evidence_ids": list(value.provenance_evidence_ids),  # type: ignore[attr-defined]
            "validation_outcome": value.validation_outcome.value,  # type: ignore[attr-defined]
            "acceptance_status": value.acceptance_status.value,  # type: ignore[attr-defined]
        }
    if name == "BuilderDMHandoff":
        return {key: getattr(value, key) for key in (
            "handoff_id", "campaign_id", "builder_session_id", "dm_session_id", "package_lock_id",
            "package_candidate_id", "plan_id", "plan_revision", "plan_content_sha256", "expected_revision",
            "resulting_campaign_revision", "chronology_ordinal", "completed_turn_id", "payload_sha256")} | {
            "model_guidance": plain(model_guidance_payload(value.model_guidance))}  # type: ignore[attr-defined]
    if name == "ChildEvidence":
        return plain(value.payload())  # type: ignore[attr-defined]
    raise TypeError(f"unexpected outcome {name}")


def run(store: a.SQLiteAuthorityStore, op: dict[str, object]) -> dict[str, object]:
    kind = str(op["op"])
    args = {key: value for key, value in op.items() if key != "op"}
    try:
        if kind == "create_builder_child_session":
            fields = dict(args["record"])  # type: ignore[arg-type]
            fields["role"] = WriterRole(fields["role"])
            fields["status"] = SessionStatus(fields["status"])
            fields["scope"] = tuple(fields["scope"])  # type: ignore[arg-type]
            value: object = store.create_builder_child_session(SessionRecord(**fields))  # type: ignore[arg-type]
        elif kind == "return_builder_child_evidence":
            fields = dict(args["evidence"])  # type: ignore[arg-type]
            fields["claims"] = tuple(fields["claims"])  # type: ignore[arg-type]
            value = store.return_builder_child_evidence(ChildEvidence(**fields))  # type: ignore[arg-type]
        elif kind == "session_history_page":
            value = store.session_history_page(str(args["session_id"]), before_sequence=args["before_sequence"],  # type: ignore[arg-type]
                                               limit=args["limit"])  # type: ignore[arg-type]
        elif kind == "session_activity_basis":
            value = store.session_activity_basis(str(args["session_id"]), args["attempt_id"])  # type: ignore[arg-type]
        else:
            value = getattr(store, kind)(**args)
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    return {"ok": value_json(value)}


def checkpoint(database: Path) -> bytes:
    connection = sqlite3.connect(database)
    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    connection.close()
    return database.read_bytes()


# Profiles a run leaves that Python's own startup refuses, because the run calls store functions
# in an order the owner never does. No later test may start from them.
NOT_VALID_STARTING_STATES = {
    "children_and_review": (
        "`require_agent_human_review` on `builder-1` puts the source session of the accepted Plan "
        "in review, which `_verify_session_identities` refuses (\"Stored Campaign Plan source session "
        "is invalid\"). The owner never reaches it: `AuthorityOwner.require_builder_human_review` "
        "has no caller in 0.4.1, and the app only stops or restores sessions."
    ),
}


def reopen_outcome(path: Path) -> dict[str, object]:
    """What the next startup says about a run's final profile."""
    try:
        store = a._open_for_application_owner(path)
    except Exception as error:  # noqa: BLE001 - the failure is the recorded behaviour
        return {"error": type(error).__name__, "code": getattr(error, "code", None), "message": str(error)}
    store.close()
    return {"ok": None}


def main() -> None:
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir()
    record: dict[str, object] = {"python": sys.version.split()[0], "runs": {}}
    with tempfile.TemporaryDirectory(dir=OUT.parent, ignore_cleanup_errors=True) as scratch:
        seed_bytes = checkpoint(seed(Path(scratch) / "seed"))
        (OUT / "seed.sqlite3.gz").write_bytes(gzip.compress(seed_bytes, mtime=0))
        for name, operations in RUNS.items():
            folder = Path(scratch) / name
            folder.mkdir()
            (folder / "authority.sqlite3").write_bytes(seed_bytes)
            store = a._open_for_application_owner(folder / "authority.sqlite3")
            try:
                outcomes = [run(store, op) for op in operations]
            finally:
                store.close()
            (OUT / f"{name}.sqlite3.gz").write_bytes(
                gzip.compress(checkpoint(folder / "authority.sqlite3"), mtime=0))
            reopened = reopen_outcome(folder / "authority.sqlite3")
            if (reopened.get("error") is not None) != (name in NOT_VALID_STARTING_STATES):
                raise AssertionError(f"{name}: reopening gave {reopened}; update NOT_VALID_STARTING_STATES")
            record["runs"][name] = {  # type: ignore[index]
                "operations": operations, "outcomes": outcomes, "reopen": reopened,
                **({"not_a_valid_starting_state": NOT_VALID_STARTING_STATES[name]}
                   if name in NOT_VALID_STARTING_STATES else {}),
            }
    text = json.dumps(record, indent=1, ensure_ascii=True) + "\n"
    (OUT / "session_page_runs.json").write_bytes(text.encode("utf-8"))


if __name__ == "__main__":
    main()
