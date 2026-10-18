"""Save an independent Builder launch and its original interview context atomically."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping

from sylvia_gm.domain.authority import GuidedCreationSubflow, SafePayload, WriterRole, owned_payload
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict, StaleRevision
from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.domain.session import (
    SessionContext,
    SessionMessageKind,
    SessionRecord,
    SessionStatus,
)
from sylvia_gm.infrastructure import sqlite_session

CONTENT_PURPOSES = frozenset({"ruleset", "setting", "plan", "player_character", "companion"})


def legacy_contexts(connection: sqlite3.Connection) -> tuple[SessionContext, ...]:
    """Find only the former reusable-content children; other delegated work stays attached."""
    contexts = (
        sqlite_session.read_context(connection, str(row[0]))
        for row in connection.execute(
            "SELECT session.session_id FROM agent_sessions AS session "
            "WHERE session.role = 'builder' AND EXISTS ("
            "SELECT 1 FROM agent_session_messages AS message "
            "WHERE message.session_id = session.session_id "
            "AND message.kind = 'evidence' "
            "AND json_extract(message.content_json, '$.kind') = 'creation_subflow_link') "
            "ORDER BY session.rowid"
        ).fetchall()
    )
    return tuple(
        context
        for context in contexts
        if any(
            message.kind is SessionMessageKind.EVIDENCE
            and message.content.get("kind") == "creation_subflow_link"
            and message.content.get("purpose") in CONTENT_PURPOSES
            for message in context.messages
        )
    )


def _append_migration_evidence(
    connection: sqlite3.Connection,
    *,
    table: str,
    session_id: str,
    message_id: str,
    content: SafePayload,
) -> None:
    """Append upgrade metadata without changing historical messages or reopening a campaign."""
    if table not in {"agent_session_messages", "standalone_builder_chat_messages"}:
        raise ValueError("Migration message table is invalid")
    connection.execute(
        f"INSERT INTO {table} "
        "(session_id, sequence, message_id, kind, content_json, canonical_sequence) "
        f"SELECT ?, COALESCE(MAX(sequence), 0) + 1, ?, 'evidence', ?, NULL FROM {table} "
        "WHERE session_id = ?",
        (session_id, message_id, canonical_json_bytes(content).decode("utf-8"), session_id),
    )


def detach_legacy_chat(
    connection: sqlite3.Connection,
    *,
    session_id: str,
    expected_message_count: int,
    launch_context: SafePayload,
    display_name: str,
) -> None:
    """Move a former content subflow to independent storage in one owner transaction."""
    context = sqlite_session.read_context(connection, session_id)
    if context.session.campaign_id is None:
        return
    if (
        context.session.role is not WriterRole.BUILDER
        or len(context.messages) != expected_message_count
        or launch_context.get("purpose") not in CONTENT_PURPOSES
    ):
        raise StaleRevision("The legacy Builder chat changed before migration")
    link = next(
        (
            message
            for message in context.messages
            if message.content.get("kind") == "creation_subflow_link"
        ),
        None,
    )
    if (
        link is None
        or link.content.get("purpose") != launch_context.get("purpose")
        or link.content.get("campaign_id") != context.session.campaign_id
        or not isinstance(link.content.get("parent_session_id"), str)
    ):
        raise CommandRejected("The legacy Builder launch does not match its saved context")
    rows = connection.execute(
        "SELECT * FROM agent_session_messages WHERE session_id = ? ORDER BY sequence",
        (session_id,),
    ).fetchall()
    parent = sqlite_session.read_context(connection, str(link.content["parent_session_id"]))
    state = next(
        (
            message.content
            for message in reversed(parent.messages)
            if message.content.get("kind") == "campaign_interview_state"
        ),
        None,
    )
    connection.execute("DELETE FROM agent_sessions WHERE session_id = ?", (session_id,))
    sqlite_session.insert_builder_chat(
        connection,
        session_id=session_id,
        provider_id=context.session.provider_id,
        model_id=context.session.model_id,
        display_name=display_name,
    )
    for row in rows:
        kind, content, canonical_sequence = (
            str(row["kind"]),
            str(row["content_json"]),
            row["canonical_sequence"],
        )
        if canonical_sequence is not None:
            # A historical event becomes retained reference data; it no longer depends on a
            # live campaign foreign key and never becomes independent-chat game authority.
            kind = "evidence"
            content = canonical_json_bytes(
                {
                    "kind": "retained_campaign_reference",
                    "campaign_id": context.session.campaign_id,
                    "canonical_sequence": canonical_sequence,
                    "reference": json.loads(content),
                }
            ).decode("utf-8")
        connection.execute(
            "INSERT INTO standalone_builder_chat_messages "
            "(session_id, sequence, message_id, kind, content_json, canonical_sequence) "
            "VALUES (?, ?, ?, ?, ?, NULL)",
            (session_id, row["sequence"], row["message_id"], kind, content),
        )
    suffix = hashlib.sha256(session_id.encode("utf-8")).hexdigest()[:24]
    _append_migration_evidence(
        connection,
        table="standalone_builder_chat_messages",
        session_id=session_id,
        message_id=f"builder-migrated-{suffix}",
        content=launch_context,
    )
    # Completion used to mean the subflow was closed. An ordinary saved conversation may
    # continue after its artifact is finished. Explicit Stop/review state remains intact.
    if context.session.status in {SessionStatus.STOPPED, SessionStatus.REVIEW_REQUIRED}:
        sqlite_session.update_session_status(connection, session_id, context.session.status)
    if state is not None:
        active = state.get("active_subflow")
        retained = state.get("subflows", [])
        active_matches = (
            isinstance(active, Mapping) and active.get("child_session_id") == session_id
        )
        remaining = [item for item in retained if item.get("child_session_id") != session_id]
        if active_matches or len(remaining) != len(retained):
            _append_migration_evidence(
                connection,
                table="agent_session_messages",
                session_id=parent.session.session_id,
                message_id=f"builder-detached-{suffix}",
                content=owned_payload(
                    {
                        **state,
                        "active_subflow": None if active_matches else active,
                        "subflows": remaining,
                        "construction_revision": int(state["construction_revision"]) + 1,
                        "last_operation_id": f"builder-detached-{suffix}",
                    }
                ),
            )


def launch_from_interview(
    connection: sqlite3.Connection,
    *,
    operation_id: str,
    campaign_id: str,
    parent_session_id: str,
    expected_construction_revision: int,
    purpose: str,
    expected_step: str,
    visible_answer: str,
    child_session: SessionRecord,
    references: SafePayload | None = None,
) -> GuidedCreationSubflow:
    """Capture a launch snapshot without suspending or changing the interview's answers."""

    suffix = hashlib.sha256(operation_id.encode("utf-8")).hexdigest()[:24]
    launch_message_id = f"builder-launch-{suffix}"
    request = {
        "operation_id": operation_id,
        "campaign_id": campaign_id,
        "parent_session_id": parent_session_id,
        "session_id": child_session.session_id,
        "construction_revision": expected_construction_revision,
        "purpose": purpose,
        "button_label": visible_answer,
    }
    replay_rows = connection.execute(
        "SELECT * FROM standalone_builder_chat_messages WHERE message_id = ?",
        (launch_message_id,),
    ).fetchall()
    if replay_rows:
        if len(replay_rows) != 1:
            raise IdempotencyConflict("Builder launch identity is ambiguous")
        replay = sqlite_session.message_from_row(replay_rows[0])
        if replay.content.get("launch_request") != request:
            raise IdempotencyConflict("Builder launch identity payload does not match")
    else:
        parent_context = sqlite_session.read_context(connection, parent_session_id)
        parent = parent_context.session
        states = tuple(
            message.content
            for message in parent_context.messages
            if message.kind is SessionMessageKind.EVIDENCE
            and message.content.get("kind") == "campaign_interview_state"
        )
        current = states[-1] if states else {}
        if (
            parent.campaign_id != campaign_id
            or parent.role is not WriterRole.BUILDER
            or child_session.campaign_id is not None
            or current.get("phase") != "draft"
            or current.get("current_step") != expected_step
            or current.get("construction_revision") != expected_construction_revision
            or current.get("active_subflow") is not None
        ):
            raise StaleRevision("Campaign Interview cannot open this Builder chat")
        by_id = {message.message_id: message for message in parent_context.messages}
        answers = current.get("answers", {})
        selections = {}
        if isinstance(answers, Mapping):
            for step, message_id in answers.items():
                message = by_id.get(str(message_id))
                if message is not None and message.kind is SessionMessageKind.USER:
                    selections[str(step)] = {
                        "text": str(message.content.get("text", "")),
                        "value": message.content.get("interview_value", {}),
                    }
        context_name = next(
            (
                str(selections[step]["text"])
                for step in ("plan", "setting", "ruleset")
                if step in selections and selections[step]["text"]
            ),
            "New campaign",
        )
        task_name = {
            "ruleset": "New ruleset",
            "setting": "New setting",
            "plan": "New campaign plan",
            "player_character": "New character",
            "companion": "New companion",
        }[purpose]
        if purpose == "plan" and expected_step == "participants":
            task_name = "Complete required characters"
        if (
            connection.execute(
                "SELECT 1 FROM standalone_builder_chats WHERE session_id = ?",
                (child_session.session_id,),
            ).fetchone()
            is not None
        ):
            raise IdempotencyConflict("Builder launch cannot reuse an existing chat")
        sqlite_session.insert_builder_chat(
            connection,
            session_id=child_session.session_id,
            provider_id=child_session.provider_id,
            model_id=child_session.model_id,
            display_name=f"{task_name} · {context_name}"[:120].strip(),
        )
        sqlite_session.append_message(
            connection,
            session_id=child_session.session_id,
            message_id=launch_message_id,
            kind=SessionMessageKind.EVIDENCE,
            content=owned_payload(
                {
                    "kind": "builder_launch_context",
                    "purpose": purpose,
                    "interview_step": expected_step,
                    "launch_request": request,
                    "selections": selections,
                    "references": references,
                    "interview_transcript": [
                        {
                            "source_message_id": message.message_id,
                            "role": (
                                "user" if message.kind is SessionMessageKind.USER else "assistant"
                            ),
                            "content": str(message.content.get("text", "")),
                        }
                        for message in parent_context.messages
                        if message.kind in (SessionMessageKind.USER, SessionMessageKind.TALK)
                    ],
                }
            ),
        )
    return GuidedCreationSubflow(
        campaign_id=campaign_id,
        parent_session_id=parent_session_id,
        child_session_id=child_session.session_id,
        purpose=purpose,
        current_step=expected_step,
        status="independent",
        construction_revision=expected_construction_revision,
    )
