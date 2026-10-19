"""SQLite rows for the shared ordinary Builder/DM session substrate."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import cast

from sylvia_gm.domain.authority import (
    LifecycleState,
    SafePayload,
    WriterRole,
    owned_json_value,
    owned_payload,
    thaw_json_value,
    validate_authority_identity,
)
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict
from sylvia_gm.domain.model_capability import model_guidance_from_payload
from sylvia_gm.domain.session import (
    BuilderChatRecord,
    BuilderDMHandoff,
    CampaignPlan,
    ChildEvidence,
    PlanAcceptanceStatus,
    PlanValidationOutcome,
    SessionActivityBasis,
    SessionContext,
    SessionHistoryPage,
    SessionMessage,
    SessionMessageKind,
    SessionRecord,
    SessionStatus,
    agent_session_payload_sha256,
    builder_chat_payload_sha256,
)
from sylvia_gm.infrastructure.sqlite_continuity import (
    read_act_markers, read_campaign_ending, read_campaign_endings,
)


def _with_mechanics_activity(
    connection: sqlite3.Connection, session: SessionRecord,
    messages: tuple[SessionMessage, ...],
) -> tuple[SessionMessage, ...]:
    """Attach saved execution links to display rows even when evidence is on an older page."""
    sequences = tuple(
        message.canonical_sequence for message in messages
        if message.kind is SessionMessageKind.CANONICAL_REFERENCE
        and message.content.get("event_type") == "tool_result"
    )
    if session.campaign_id is None or not sequences:
        return messages
    placeholders = ",".join("?" for _ in sequences)
    rows = connection.execute(
        "SELECT event.sequence, (SELECT json_extract(message.content_json, '$.activity') "
        "FROM agent_session_messages AS message WHERE message.session_id = ? "
        "AND message.kind = 'evidence' "
        "AND json_extract(message.content_json, '$.kind') = 'mechanics_activity' "
        "AND json_extract(message.content_json, '$.action_id') = event.action_id "
        "ORDER BY message.sequence LIMIT 1) AS activity "
        "FROM canonical_events AS event WHERE event.campaign_id = ? "
        f"AND event.sequence IN ({placeholders}) AND event.event_type = 'tool_result'",
        (session.session_id, session.campaign_id, *sequences),
    )
    activity = {
        row["sequence"]: _load_payload(row["activity"])
        for row in rows if row["activity"] is not None
    }
    return tuple(
        replace(message, content=owned_payload({
            **message.content, "activity": activity[message.canonical_sequence],
        })) if message.canonical_sequence in activity else message
        for message in messages
    )


MIGRATION_4_SQL = """
CREATE TABLE agent_sessions (
    session_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('builder', 'dm')),
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'active', 'stopped', 'completed', 'review_required', 'handoff_closed'
    )),
    parent_session_id TEXT,
    selected_skill TEXT CHECK (selected_skill IN (
        'BUILD RULESET', 'BUILD CAMPAIGN SETTING', 'BUILD CAMPAIGN PLAN'
    )),
    scope_json TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (parent_session_id) REFERENCES agent_sessions(session_id),
    CHECK (
        (role = 'builder' AND selected_skill IS NOT NULL)
        OR (role = 'dm' AND selected_skill IS NULL)
    )
);
CREATE TABLE agent_session_messages (
    session_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    message_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN (
        'user', 'safe_think', 'talk', 'steering', 'evidence',
        'canonical_reference', 'human_review'
    )),
    content_json TEXT NOT NULL,
    canonical_sequence INTEGER,
    PRIMARY KEY (session_id, sequence),
    UNIQUE (session_id, message_id),
    FOREIGN KEY (session_id) REFERENCES agent_sessions(session_id) ON DELETE CASCADE,
    CHECK (
        (kind = 'canonical_reference' AND canonical_sequence IS NOT NULL)
        OR (kind != 'canonical_reference' AND canonical_sequence IS NULL)
    )
);
CREATE TABLE campaign_plans (
    plan_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL,
    package_lock_id TEXT NOT NULL,
    source_session_id TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    content_json TEXT NOT NULL,
    provenance_json TEXT NOT NULL,
    validation_outcome TEXT NOT NULL CHECK (validation_outcome = 'valid'),
    acceptance_status TEXT NOT NULL CHECK (acceptance_status = 'accepted'),
    plan_revision INTEGER NOT NULL CHECK (plan_revision > 0),
    resulting_campaign_revision INTEGER NOT NULL CHECK (resulting_campaign_revision > 0),
    payload_sha256 TEXT NOT NULL,
    UNIQUE (campaign_id, plan_revision),
    UNIQUE (campaign_id, resulting_campaign_revision),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (package_lock_id) REFERENCES campaign_locks(lock_id),
    FOREIGN KEY (source_session_id) REFERENCES agent_sessions(session_id)
);
ALTER TABLE campaign_revisions ADD COLUMN plan_id TEXT;
ALTER TABLE campaign_revisions ADD COLUMN handoff_id TEXT;
CREATE TABLE builder_dm_handoffs (
    handoff_id TEXT PRIMARY KEY,
    campaign_id TEXT NOT NULL UNIQUE,
    payload_sha256 TEXT NOT NULL,
    builder_session_id TEXT NOT NULL,
    dm_session_id TEXT NOT NULL UNIQUE,
    package_lock_id TEXT NOT NULL,
    package_candidate_id TEXT,
    plan_id TEXT NOT NULL,
    plan_revision INTEGER NOT NULL CHECK (plan_revision > 0),
    plan_content_sha256 TEXT NOT NULL,
    expected_revision INTEGER NOT NULL CHECK (expected_revision >= 0),
    resulting_campaign_revision INTEGER NOT NULL CHECK (
        resulting_campaign_revision = expected_revision + 1
    ),
    chronology_ordinal INTEGER NOT NULL CHECK (chronology_ordinal = 1),
    completed_turn_id TEXT NOT NULL,
    safe_payload_json TEXT NOT NULL,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (builder_session_id) REFERENCES agent_sessions(session_id),
    FOREIGN KEY (dm_session_id) REFERENCES agent_sessions(session_id),
    FOREIGN KEY (package_lock_id) REFERENCES campaign_locks(lock_id),
    FOREIGN KEY (package_candidate_id) REFERENCES package_candidates(candidate_id),
    FOREIGN KEY (plan_id) REFERENCES campaign_plans(plan_id),
    FOREIGN KEY (campaign_id, completed_turn_id) REFERENCES turns(campaign_id, turn_id)
);
"""
MIGRATION_4_NAME = "f4_builder_dm_sessions"

MIGRATION_8_SQL = """
CREATE TABLE standalone_builder_chats (
    session_id TEXT PRIMARY KEY,
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'active', 'stopped', 'completed', 'review_required'
    )),
    display_name TEXT NOT NULL CHECK (
        length(display_name) BETWEEN 1 AND 120 AND display_name = trim(display_name)
    ),
    created_ordinal INTEGER NOT NULL UNIQUE CHECK (created_ordinal > 0),
    payload_sha256 TEXT NOT NULL
);
CREATE TABLE standalone_builder_chat_messages (
    session_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence > 0),
    message_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN (
        'user', 'safe_think', 'talk', 'steering', 'evidence', 'human_review'
    )),
    content_json TEXT NOT NULL,
    canonical_sequence INTEGER CHECK (canonical_sequence IS NULL),
    PRIMARY KEY (session_id, sequence),
    UNIQUE (session_id, message_id),
    FOREIGN KEY (session_id) REFERENCES standalone_builder_chats(session_id) ON DELETE CASCADE
);
"""
MIGRATION_8_NAME = "standalone_builder_chats"


def session_from_row(row: sqlite3.Row) -> SessionRecord:
    columns = row.keys()
    record = SessionRecord(
        session_id=str(row["session_id"]),
        campaign_id=str(row["campaign_id"]),
        role=WriterRole(str(row["role"])),
        provider_id=str(row["provider_id"]),
        model_id=str(row["model_id"]),
        status=SessionStatus(str(row["status"])),
        parent_session_id=(
            None if row["parent_session_id"] is None else str(row["parent_session_id"])
        ),
        selected_skill=(None if row["selected_skill"] is None else str(row["selected_skill"])),
        scope=tuple(str(item) for item in json.loads(str(row["scope_json"]))),
        reasoning_effort=row["reasoning_effort"] if "reasoning_effort" in columns else None,
        reasoning_effort_once=(
            row["reasoning_effort_once"] if "reasoning_effort_once" in columns else None
        ),
    )
    if str(row["payload_sha256"]) != agent_session_payload_sha256(record):
        raise ValueError("Stored agent session payload identity is invalid")
    return record


def builder_chat_from_row(row: sqlite3.Row) -> BuilderChatRecord:
    """Load one campaign-independent Builder chat and verify its exact metadata."""

    columns = row.keys()
    record = BuilderChatRecord(
        session=SessionRecord(
            session_id=str(row["session_id"]),
            campaign_id=None,
            role=WriterRole.BUILDER,
            provider_id=str(row["provider_id"]),
            model_id=str(row["model_id"]),
            status=SessionStatus(str(row["status"])),
            reasoning_effort=row["reasoning_effort"] if "reasoning_effort" in columns else None,
            reasoning_effort_once=(
                row["reasoning_effort_once"] if "reasoning_effort_once" in columns else None
            ),
        ),
        display_name=str(row["display_name"]),
        created_ordinal=int(row["created_ordinal"]),
    )
    if str(row["payload_sha256"]) != builder_chat_payload_sha256(record):
        raise ValueError("Stored standalone Builder chat payload identity is invalid")
    return record


def read_session(connection: sqlite3.Connection, session_id: str) -> SessionRecord:
    row = connection.execute(
        "SELECT * FROM agent_sessions WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is not None:
        return session_from_row(row)
    if (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'standalone_builder_chats'"
        ).fetchone()
        is None
    ):
        raise CommandRejected("Agent session is unavailable", code="session_unavailable")
    chat_row = connection.execute(
        "SELECT * FROM standalone_builder_chats WHERE session_id = ?", (session_id,)
    ).fetchone()
    if chat_row is None:
        raise CommandRejected("Agent session is unavailable", code="session_unavailable")
    return builder_chat_from_row(chat_row).session


def read_builder_chat(connection: sqlite3.Connection, session_id: str) -> BuilderChatRecord:
    row = connection.execute(
        "SELECT * FROM standalone_builder_chats WHERE session_id = ?", (session_id,)
    ).fetchone()
    if row is None:
        raise CommandRejected("Builder chat is unavailable", code="session_unavailable")
    return builder_chat_from_row(row)


def read_builder_chats(connection: sqlite3.Connection) -> tuple[BuilderChatRecord, ...]:
    return tuple(
        builder_chat_from_row(row)
        for row in connection.execute(
            "SELECT * FROM standalone_builder_chats ORDER BY created_ordinal DESC"
        )
    )


def require_active_campaign(connection: sqlite3.Connection, campaign_id: str) -> None:
    """Durably close every ordinary mutation when its campaign is inactive."""

    row = connection.execute(
        "SELECT lifecycle FROM campaigns WHERE campaign_id = ?", (campaign_id,)
    ).fetchone()
    if row is None or str(row["lifecycle"]) != LifecycleState.ACTIVE.value:
        raise CommandRejected("Campaign lifecycle blocks mutation", code="lifecycle_inactive")


def _dump(value: object) -> str:
    return json.dumps(
        thaw_json_value(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def _load_value(value: str) -> object:
    parsed = owned_json_value(json.loads(value))
    if _dump(parsed) != value:
        raise ValueError("Stored session JSON is not canonical")
    return parsed


def _load_payload(value: str) -> SafePayload:
    parsed = _load_value(value)
    if not isinstance(parsed, Mapping):
        raise ValueError("Stored session message content is not an object")
    captured = cast(SafePayload, parsed)
    return owned_payload(captured)


def message_from_row(row: sqlite3.Row) -> SessionMessage:
    return SessionMessage(
        session_id=str(row["session_id"]),
        sequence=int(row["sequence"]),
        message_id=str(row["message_id"]),
        kind=SessionMessageKind(str(row["kind"])),
        content=_load_payload(str(row["content_json"])),
        canonical_sequence=(
            None if row["canonical_sequence"] is None else int(row["canonical_sequence"])
        ),
    )


def read_messages(connection: sqlite3.Connection, session_id: str) -> tuple[SessionMessage, ...]:
    session = read_session(connection, session_id)
    table = (
        "standalone_builder_chat_messages"
        if session.campaign_id is None
        else "agent_session_messages"
    )
    return tuple(
        message_from_row(row)
        for row in connection.execute(
            f"SELECT * FROM {table} WHERE session_id = ? ORDER BY sequence",
            (session_id,),
        )
    )


def read_activity_basis(
    connection: sqlite3.Connection, session_id: str, attempt_id: str | None = None,
) -> SessionActivityBasis:
    """Decode only the selected accepted input and its terminal outcome."""
    session = read_session(connection, session_id)
    table = (
        "standalone_builder_chat_messages"
        if session.campaign_id is None else "agent_session_messages"
    )
    compaction = "json_extract(content_json, '$.kind') = 'context_compaction_requested'"
    has_compaction = connection.execute(
        f"SELECT 1 FROM {table} WHERE session_id = ? AND {compaction} LIMIT 1",
        (session_id,),
    ).fetchone() is not None
    inputs = compaction
    if session.role is WriterRole.BUILDER:
        inputs = (
            f"({compaction} OR kind = 'user' OR "
            "json_extract(content_json, '$.kind') = 'builder_start')"
        )
    match = "" if attempt_id is None else " AND json_extract(content_json, '$.attempt_id') = ?"
    parameters = (session_id,) if attempt_id is None else (session_id, attempt_id)
    row = connection.execute(
        f"SELECT * FROM {table} WHERE session_id = ? AND {inputs} "
        "AND json_type(content_json, '$.attempt_id') = 'text'"
        f"{match} ORDER BY sequence DESC LIMIT 1", parameters,
    ).fetchone()
    accepted = None if row is None else message_from_row(row)
    selected_attempt = attempt_id if attempt_id is not None else (
        str(accepted.content['attempt_id']) if accepted is not None else None
    )
    terminal_row = None if selected_attempt is None else connection.execute(
        f"SELECT * FROM {table} WHERE session_id = ? AND kind = 'evidence' "
        "AND json_extract(content_json, '$.kind') IN "
        "('builder_attempt_terminal', 'context_compaction_terminal') "
        "AND json_extract(content_json, '$.attempt_id') = ? ORDER BY sequence DESC LIMIT 1",
        (session_id, selected_attempt),
    ).fetchone()
    return SessionActivityBasis(
        session=session, accepted=accepted,
        terminal=None if terminal_row is None else message_from_row(terminal_row),
        has_compaction=has_compaction,
    )


def read_history_page(
    connection: sqlite3.Connection, session_id: str, *,
    before_sequence: int | None = None, limit: int = 50,
) -> SessionHistoryPage:
    """Read newest rows before a stable cursor without decoding the rest of history."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 200:
        raise ValueError("History page limit must be an integer from 1 to 200")
    if before_sequence is not None and (
        isinstance(before_sequence, bool)
        or not isinstance(before_sequence, int)
        or before_sequence <= 0
    ):
        raise ValueError("History cursor must be a positive integer")
    session = read_session(connection, session_id)
    table = (
        "standalone_builder_chat_messages"
        if session.campaign_id is None else "agent_session_messages"
    )
    cursor_clause = "" if before_sequence is None else " AND sequence < ?"
    parameters = (session_id,) if before_sequence is None else (session_id, before_sequence)
    rows = connection.execute(
        f"SELECT * FROM {table} WHERE session_id = ?{cursor_clause} "
        "ORDER BY sequence DESC LIMIT ?", (*parameters, limit + 1),
    ).fetchall()
    messages = _with_mechanics_activity(
        connection, session, tuple(message_from_row(row) for row in reversed(rows[:limit])),
    )
    latest = connection.execute(
        f"SELECT MAX(sequence) FROM {table} WHERE session_id = ?", (session_id,),
    ).fetchone()[0]
    latest_user = connection.execute(
        f"SELECT MAX(sequence) FROM {table} WHERE session_id = ? AND kind = ?",
        (session_id, SessionMessageKind.USER.value),
    ).fetchone()[0]
    launch_messages = tuple(
        message_from_row(row)
        for row in connection.execute(
            f"SELECT * FROM {table} WHERE session_id = ? AND kind = 'evidence' "
            "AND json_extract(content_json, '$.kind') IN "
            "('creation_subflow_link', 'builder_launch_context') ORDER BY sequence DESC LIMIT 2",
            (session_id,),
        )
    )
    latest_canonical = connection.execute(
        f"SELECT MAX(canonical_sequence) FROM {table} WHERE session_id = ?", (session_id,),
    ).fetchone()[0]
    return SessionHistoryPage(
        session=session, messages=messages,
        next_before_sequence=messages[0].sequence if len(rows) > limit else None,
        latest_sequence=latest, latest_user_sequence=latest_user, launch_messages=launch_messages,
        latest_canonical_sequence=latest_canonical,
        act_markers=read_act_markers(connection, session_id, session.campaign_id)
        if session.role is WriterRole.DM and session.campaign_id is not None else (),
        campaign_ending=read_campaign_ending(connection, session_id, session.campaign_id)
        if session.role is WriterRole.DM and session.campaign_id is not None else None,
        campaign_endings=read_campaign_endings(connection, session_id, session.campaign_id)
        if session.role is WriterRole.DM and session.campaign_id is not None else (),
    )


def read_context(connection: sqlite3.Connection, session_id: str) -> SessionContext:
    return SessionContext(
        session=read_session(connection, session_id),
        messages=read_messages(connection, session_id),
    )


def interview_name(
    connection: sqlite3.Connection, session_id: str, campaign_id: str,
) -> str | None:
    """Read only the current accepted campaign name for a default-titled Interview."""
    row = connection.execute(
        "SELECT answer.content_json FROM agent_session_messages AS answer "
        "WHERE answer.session_id = ? AND answer.kind = 'user' "
        "AND NOT EXISTS (SELECT 1 FROM campaign_lifecycle_transitions "
        "WHERE campaign_id = ? AND operation_kind = 'rename_campaign') "
        "AND answer.message_id = ("
        "SELECT json_extract(state.content_json, '$.answers.name') "
        "FROM agent_session_messages AS state WHERE state.session_id = ? "
        "AND state.kind = 'evidence' "
        "AND json_extract(state.content_json, '$.kind') = 'campaign_interview_state' "
        "ORDER BY state.sequence DESC LIMIT 1)", (session_id, campaign_id, session_id),
    ).fetchone()
    if row is None:
        return None
    answer = json.loads(row[0]).get("interview_value", {})
    name = answer.get("display_name") if isinstance(answer, dict) else None
    return name.strip() if isinstance(name, str) and 0 < len(name.strip()) <= 120 else None


def plan_from_row(row: sqlite3.Row) -> CampaignPlan:
    provenance = _load_value(str(row["provenance_json"]))
    if not isinstance(provenance, Sequence) or isinstance(provenance, (str, bytes, bytearray)):
        raise ValueError("Stored Campaign Plan provenance is not an array")
    return CampaignPlan(
        plan_id=str(row["plan_id"]),
        campaign_id=str(row["campaign_id"]),
        package_lock_id=str(row["package_lock_id"]),
        source_session_id=str(row["source_session_id"]),
        content_sha256=str(row["content_sha256"]),
        content=_load_payload(str(row["content_json"])),
        provenance_evidence_ids=tuple(str(item) for item in provenance),
        validation_outcome=PlanValidationOutcome(str(row["validation_outcome"])),
        acceptance_status=PlanAcceptanceStatus(str(row["acceptance_status"])),
        plan_revision=int(row["plan_revision"]),
        resulting_campaign_revision=int(row["resulting_campaign_revision"]),
        payload_sha256=str(row["payload_sha256"]),
    )


def read_plan(connection: sqlite3.Connection, plan_id: str) -> CampaignPlan | None:
    row = connection.execute(
        "SELECT * FROM campaign_plans WHERE plan_id = ?",
        (plan_id,),
    ).fetchone()
    return None if row is None else plan_from_row(row)


def handoff_from_row(row: sqlite3.Row) -> BuilderDMHandoff:
    safe_payload = _load_payload(str(row["safe_payload_json"]))
    expected_payload = {
        "campaign_id": str(row["campaign_id"]),
        "builder_session_id": str(row["builder_session_id"]),
        "dm_session_id": str(row["dm_session_id"]),
        "package_lock_id": str(row["package_lock_id"]),
        "package_candidate_id": (
            None if row["package_candidate_id"] is None else str(row["package_candidate_id"])
        ),
        "plan_id": str(row["plan_id"]),
        "resulting_campaign_revision": int(row["resulting_campaign_revision"]),
        "completed_turn_id": str(row["completed_turn_id"]),
        "model_guidance": safe_payload.get("model_guidance"),
    }
    if "dm_provider_id" in safe_payload:
        provider_id = safe_payload["dm_provider_id"]
        if not isinstance(provider_id, str):
            raise ValueError("Stored Builder-to-DM provider identity is invalid")
        validate_authority_identity(provider_id, "Handoff DM provider")
        expected_payload["dm_provider_id"] = provider_id
    if safe_payload != expected_payload:
        raise ValueError("Stored Builder-to-DM handoff chronology is invalid")
    return BuilderDMHandoff(
        handoff_id=str(row["handoff_id"]),
        campaign_id=str(row["campaign_id"]),
        builder_session_id=str(row["builder_session_id"]),
        dm_session_id=str(row["dm_session_id"]),
        package_lock_id=str(row["package_lock_id"]),
        package_candidate_id=(
            None if row["package_candidate_id"] is None else str(row["package_candidate_id"])
        ),
        plan_id=str(row["plan_id"]),
        plan_revision=int(row["plan_revision"]),
        plan_content_sha256=str(row["plan_content_sha256"]),
        expected_revision=int(row["expected_revision"]),
        resulting_campaign_revision=int(row["resulting_campaign_revision"]),
        chronology_ordinal=int(row["chronology_ordinal"]),
        completed_turn_id=str(row["completed_turn_id"]),
        model_guidance=model_guidance_from_payload(safe_payload["model_guidance"]),
        payload_sha256=str(row["payload_sha256"]),
    )


def read_handoff(connection: sqlite3.Connection, handoff_id: str) -> BuilderDMHandoff | None:
    row = connection.execute(
        "SELECT * FROM builder_dm_handoffs WHERE handoff_id = ?",
        (handoff_id,),
    ).fetchone()
    return None if row is None else handoff_from_row(row)


def require_human_review(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    review_id: str,
    content: SafePayload,
) -> SessionRecord:
    current = read_session(connection, session_id)
    if current.role is not WriterRole.BUILDER:
        raise CommandRejected("Only Builder sessions can require construction review")
    if current.status not in {SessionStatus.ACTIVE, SessionStatus.REVIEW_REQUIRED}:
        raise CommandRejected("Builder session is not review-admissible")
    append_message(
        connection,
        session_id=session_id,
        message_id=review_id,
        kind=SessionMessageKind.HUMAN_REVIEW,
        content=content,
    )
    return update_session_status(connection, session_id, SessionStatus.REVIEW_REQUIRED)


def append_message(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    message_id: str,
    kind: SessionMessageKind,
    content: SafePayload,
    canonical_sequence: int | None = None,
) -> SessionMessage:
    session = read_session(connection, session_id)
    if session.campaign_id is not None:
        require_active_campaign(connection, session.campaign_id)
    table = (
        "standalone_builder_chat_messages"
        if session.campaign_id is None
        else "agent_session_messages"
    )
    existing = connection.execute(
        f"SELECT * FROM {table} WHERE session_id = ? AND message_id = ?",
        (session_id, message_id),
    ).fetchone()
    if existing is not None:
        captured = message_from_row(existing)
        if (
            captured.kind is not kind
            or dict(captured.content) != dict(content)
            or captured.canonical_sequence != canonical_sequence
        ):
            raise IdempotencyConflict("Agent message identity payload does not match")
        return captured
    stopped_terminal = (
        session.status is SessionStatus.STOPPED
        and kind is SessionMessageKind.EVIDENCE
        and content.get("kind") in {
            "session_attempt_terminal", "builder_attempt_terminal", "builder_title_preference"
        }
    )
    if session.status is not SessionStatus.ACTIVE and not (
        session.status is SessionStatus.STOPPED
        and (kind is SessionMessageKind.CANONICAL_REFERENCE or stopped_terminal)
    ):
        raise CommandRejected("Agent session is not active")
    if canonical_sequence is not None:
        if session.campaign_id is None:
            raise CommandRejected("Standalone Builder chats have no campaign chronology")
        event = connection.execute(
            "SELECT campaign_id FROM canonical_events WHERE campaign_id = ? AND sequence = ?",
            (session.campaign_id, canonical_sequence),
        ).fetchone()
        if event is None:
            raise CommandRejected("Canonical session reference is unavailable")
    next_sequence = int(
        connection.execute(
            f"SELECT COALESCE(MAX(sequence), 0) + 1 FROM {table} WHERE session_id = ?",
            (session_id,),
        ).fetchone()[0]
    )
    record = SessionMessage(
        session_id=session_id,
        sequence=next_sequence,
        message_id=message_id,
        kind=kind,
        content=content,
        canonical_sequence=canonical_sequence,
    )
    connection.execute(
        f"INSERT INTO {table} "
        "(session_id, sequence, message_id, kind, content_json, canonical_sequence) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            record.session_id,
            record.sequence,
            record.message_id,
            record.kind.value,
            _dump(record.content),
            record.canonical_sequence,
        ),
    )
    return record


def update_session_status(
    connection: sqlite3.Connection,
    session_id: str,
    status: SessionStatus,
) -> SessionRecord:
    current = read_session(connection, session_id)
    if current.campaign_id is not None:
        require_active_campaign(connection, current.campaign_id)
    if current.status is status:
        return current
    if current.status is not SessionStatus.ACTIVE and not (
        current.status is SessionStatus.REVIEW_REQUIRED and status is SessionStatus.STOPPED
    ):
        raise CommandRejected("Agent session is not active")
    updated = replace(current, status=status)
    if current.campaign_id is None:
        chat = read_builder_chat(connection, session_id)
        updated_chat = replace(chat, session=updated)
        connection.execute(
            "UPDATE standalone_builder_chats SET status = ?, payload_sha256 = ? "
            "WHERE session_id = ?",
            (status.value, builder_chat_payload_sha256(updated_chat), session_id),
        )
    else:
        connection.execute(
            "UPDATE agent_sessions SET status = ?, payload_sha256 = ? WHERE session_id = ?",
            (status.value, agent_session_payload_sha256(updated), session_id),
        )
    return read_session(connection, session_id)


def restore_stopped_session(
    connection: sqlite3.Connection,
    session_id: str,
) -> SessionRecord:
    """Reactivate one exact stopped session inside its lifecycle Restore transaction."""

    current = read_session(connection, session_id)
    if current.status is not SessionStatus.STOPPED:
        raise CommandRejected("Designated campaign session is not stopped")
    updated = replace(current, status=SessionStatus.ACTIVE)
    if current.campaign_id is None:
        chat = read_builder_chat(connection, session_id)
        updated_chat = replace(chat, session=updated)
        connection.execute(
            "UPDATE standalone_builder_chats SET status = 'active', payload_sha256 = ? "
            "WHERE session_id = ? AND status = 'stopped'",
            (builder_chat_payload_sha256(updated_chat), session_id),
        )
    else:
        connection.execute(
            "UPDATE agent_sessions SET status = 'active', payload_sha256 = ? "
            "WHERE session_id = ? AND status = 'stopped'",
            (agent_session_payload_sha256(updated), session_id),
        )
    return read_session(connection, session_id)


def insert_builder_chat(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    provider_id: str,
    model_id: str,
    display_name: str,
) -> BuilderChatRecord:
    """Create one top-level general Builder chat without a Campaign Aggregate."""

    if (
        connection.execute(
            "SELECT 1 FROM agent_sessions WHERE session_id = ?", (session_id,)
        ).fetchone()
        is not None
    ):
        raise IdempotencyConflict("Builder chat identity collides with a campaign session")
    existing = connection.execute(
        "SELECT * FROM standalone_builder_chats WHERE session_id = ?", (session_id,)
    ).fetchone()
    if existing is not None:
        captured = builder_chat_from_row(existing)
        if (
            captured.session.provider_id != provider_id
            or captured.session.model_id != model_id
            or captured.display_name != display_name
        ):
            raise IdempotencyConflict("Builder chat identity payload does not match")
        return captured
    ordinal = int(
        connection.execute(
            "SELECT COALESCE(MAX(created_ordinal), 0) + 1 FROM standalone_builder_chats"
        ).fetchone()[0]
    )
    record = BuilderChatRecord(
        session=SessionRecord(
            session_id=session_id,
            campaign_id=None,
            role=WriterRole.BUILDER,
            provider_id=provider_id,
            model_id=model_id,
        ),
        display_name=display_name,
        created_ordinal=ordinal,
    )
    connection.execute(
        "INSERT INTO standalone_builder_chats "
        "(session_id, provider_id, model_id, status, display_name, created_ordinal, "
        "payload_sha256) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            record.session.session_id,
            record.session.provider_id,
            record.session.model_id,
            record.session.status.value,
            record.display_name,
            record.created_ordinal,
            builder_chat_payload_sha256(record),
        ),
    )
    return record


def rename_builder_chat(
    connection: sqlite3.Connection, session_id: str, display_name: str, *, automatic: bool = False
) -> BuilderChatRecord:
    """Save a title atomically with the preference that protects an explicit user rename."""
    current = read_builder_chat(connection, session_id)
    if automatic and any(
        message.kind is SessionMessageKind.EVIDENCE
        and message.content.get("kind") == "builder_title_preference"
        for message in read_messages(connection, session_id)
    ):
        return current
    updated = replace(current, display_name=display_name)
    if not automatic:
        append_message(
            connection,
            session_id=session_id,
            message_id="builder-title-user-" + hashlib.sha256(session_id.encode()).hexdigest()[:24],
            kind=SessionMessageKind.EVIDENCE,
            content={"kind": "builder_title_preference", "automatic": False},
        )
    connection.execute(
        "UPDATE standalone_builder_chats SET display_name = ?, payload_sha256 = ? "
        "WHERE session_id = ?",
        (display_name, builder_chat_payload_sha256(updated), session_id),
    )
    return updated


def delete_builder_chat(connection: sqlite3.Connection, session_id: str) -> BuilderChatRecord:
    """Delete exactly one standalone Builder chat and its cascade-owned messages."""

    current = read_builder_chat(connection, session_id)
    deleted = connection.execute(
        "DELETE FROM standalone_builder_chats WHERE session_id = ?", (session_id,)
    )
    if deleted.rowcount != 1:
        raise CommandRejected("Standalone Builder chat is unavailable")
    return current


def update_session_model(
    connection: sqlite3.Connection,
    session_id: str,
    *,
    provider_id: str,
    model_id: str,
) -> SessionRecord:
    """Replace one session's configured model while preserving its exact identity."""

    current = read_session(connection, session_id)
    updated = replace(current, provider_id=provider_id, model_id=model_id)
    if current.campaign_id is None:
        chat = read_builder_chat(connection, session_id)
        updated_chat = replace(chat, session=updated)
        connection.execute(
            "UPDATE standalone_builder_chats SET provider_id = ?, model_id = ?, "
            "payload_sha256 = ? WHERE session_id = ?",
            (
                updated.provider_id,
                updated.model_id,
                builder_chat_payload_sha256(updated_chat),
                session_id,
            ),
        )
    else:
        # Older openings omitted the provider from their chronology. Preserve it
        # before changing the session; the opening digest still binds its value.
        handoff_row = connection.execute(
            "SELECT handoff_id, safe_payload_json FROM builder_dm_handoffs "
            "WHERE dm_session_id = ?", (session_id,),
        ).fetchone()
        if handoff_row is not None:
            chronology = dict(_load_payload(str(handoff_row["safe_payload_json"])))
            if "dm_provider_id" not in chronology:
                chronology["dm_provider_id"] = current.provider_id
                connection.execute(
                    "UPDATE builder_dm_handoffs SET safe_payload_json = ? WHERE handoff_id = ?",
                    (_dump(chronology), handoff_row["handoff_id"]),
                )
        connection.execute(
            "UPDATE agent_sessions SET provider_id = ?, model_id = ?, payload_sha256 = ? "
            "WHERE session_id = ?",
            (
                updated.provider_id,
                updated.model_id,
                agent_session_payload_sha256(updated),
                session_id,
            ),
        )
    return read_session(connection, session_id)


def insert_child_session(
    connection: sqlite3.Connection,
    record: SessionRecord,
    *,
    maximum_children: int = 3,
) -> SessionRecord:
    existing = connection.execute(
        "SELECT * FROM agent_sessions WHERE session_id = ?", (record.session_id,)
    ).fetchone()
    if existing is not None:
        captured = session_from_row(existing)
        if captured != record:
            raise IdempotencyConflict("Agent session identity payload does not match")
        return captured
    if record.parent_session_id is None:
        raise ValueError("Child session requires a parent_session_id")
    parent = read_session(connection, record.parent_session_id)
    if parent.parent_session_id is not None:
        raise CommandRejected("Builder child sessions cannot create nested children")
    if parent.role is not WriterRole.BUILDER or parent.status is not SessionStatus.ACTIVE:
        raise CommandRejected("Builder parent session is not active")
    child_count = int(
        connection.execute(
            "SELECT COUNT(*) FROM agent_sessions WHERE parent_session_id = ?",
            (parent.session_id,),
        ).fetchone()[0]
    )
    if child_count >= maximum_children:
        raise CommandRejected("Builder child sessions are bounded at three")
    return insert_session(connection, record)


def return_child_evidence(
    connection: sqlite3.Connection,
    evidence: ChildEvidence,
) -> tuple[ChildEvidence, SessionRecord]:
    child = read_session(connection, evidence.child_session_id)
    if child.parent_session_id != evidence.parent_session_id:
        raise CommandRejected("Child evidence parent identity does not match")
    if child.role is not WriterRole.BUILDER:
        raise CommandRejected("Only Builder children return construction evidence")
    if child.status not in {SessionStatus.ACTIVE, SessionStatus.COMPLETED}:
        raise CommandRejected("Builder child session cannot return evidence")
    append_message(
        connection,
        session_id=evidence.parent_session_id,
        message_id=evidence.evidence_id,
        kind=SessionMessageKind.EVIDENCE,
        content=evidence.payload(),
    )
    completed = update_session_status(
        connection, evidence.child_session_id, SessionStatus.COMPLETED
    )
    return evidence, completed


def insert_session(connection: sqlite3.Connection, record: SessionRecord) -> SessionRecord:
    if record.campaign_id is None:
        raise ValueError("Standalone Builder chats use insert_builder_chat")
    if (
        connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'standalone_builder_chats'"
        ).fetchone()
        is not None
        and connection.execute(
            "SELECT 1 FROM standalone_builder_chats WHERE session_id = ?", (record.session_id,)
        ).fetchone()
        is not None
    ):
        raise IdempotencyConflict("Agent session identity collides with a Builder chat")
    campaign = connection.execute(
        "SELECT lifecycle, writer FROM campaigns WHERE campaign_id = ?",
        (record.campaign_id,),
    ).fetchone()
    if campaign is None:
        raise CommandRejected("Campaign is unavailable", code="campaign_unavailable")
    if str(campaign["lifecycle"]) != LifecycleState.ACTIVE.value:
        raise CommandRejected("Campaign lifecycle does not admit an active agent session")
    if str(campaign["writer"]) != record.role.value:
        raise CommandRejected("Agent session role is not the campaign writer", code="wrong_writer")
    if record.parent_session_id is not None:
        parent = read_session(connection, record.parent_session_id)
        if (
            parent.campaign_id != record.campaign_id
            or parent.role is not WriterRole.BUILDER
            or record.role is not WriterRole.BUILDER
        ):
            raise CommandRejected("Builder child session parent is incompatible")
    existing = connection.execute(
        "SELECT * FROM agent_sessions WHERE session_id = ?", (record.session_id,)
    ).fetchone()
    if existing is not None:
        captured = session_from_row(existing)
        if captured != record:
            raise IdempotencyConflict("Agent session identity payload does not match")
        return captured
    connection.execute(
        "INSERT INTO agent_sessions "
        "(session_id, campaign_id, role, provider_id, model_id, status, "
        "parent_session_id, selected_skill, scope_json, payload_sha256) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            record.session_id,
            record.campaign_id,
            record.role.value,
            record.provider_id,
            record.model_id,
            record.status.value,
            record.parent_session_id,
            record.selected_skill,
            _dump(list(record.scope)),
            agent_session_payload_sha256(record),
        ),
    )
    if record.reasoning_effort is not None or record.reasoning_effort_once is not None:
        connection.execute(
            "UPDATE agent_sessions SET reasoning_effort = ?, reasoning_effort_once = ? "
            "WHERE session_id = ?",
            (record.reasoning_effort, record.reasoning_effort_once, record.session_id),
        )
    return record
