"""Additive saved-session reasoning preferences, owned by the existing authority database."""

from __future__ import annotations

import hashlib
import sqlite3
from dataclasses import replace

from sylvia_gm.domain.session import (
    SessionRecord,
    agent_session_payload_sha256,
    builder_chat_payload_sha256,
)
from sylvia_gm.infrastructure import sqlite_session

MIGRATION_12_NAME = "session_reasoning_preferences"
MIGRATION_12_SQL = """
ALTER TABLE agent_sessions ADD COLUMN reasoning_effort TEXT;
ALTER TABLE agent_sessions ADD COLUMN reasoning_effort_once TEXT;
ALTER TABLE standalone_builder_chats ADD COLUMN reasoning_effort TEXT;
ALTER TABLE standalone_builder_chats ADD COLUMN reasoning_effort_once TEXT;
""".strip()
MIGRATION_12_SHA256 = hashlib.sha256(MIGRATION_12_SQL.encode("utf-8")).hexdigest()


def apply_migration(connection: sqlite3.Connection) -> None:
    """Add nullable preferences in the caller's transaction; old session identities stay valid."""
    for statement in MIGRATION_12_SQL.split(";"):
        if statement.strip():
            connection.execute(statement)


def update_preferences(
    connection: sqlite3.Connection,
    session_id: str,
    *,
    effort: str | None,
    once: str | None,
) -> SessionRecord:
    """Persist both preference scopes and their identity atomically without touching history."""
    current = sqlite_session.read_session(connection, session_id)
    updated = replace(current, reasoning_effort=effort, reasoning_effort_once=once)
    if current.campaign_id is None:
        table = "standalone_builder_chats"
        chat = sqlite_session.read_builder_chat(connection, session_id)
        digest = builder_chat_payload_sha256(replace(chat, session=updated))
    else:
        table = "agent_sessions"
        digest = agent_session_payload_sha256(updated)
    connection.execute(
        f"UPDATE {table} SET reasoning_effort = ?, reasoning_effort_once = ?, "
        "payload_sha256 = ? WHERE session_id = ?",
        (effort, once, digest, session_id),
    )
    return sqlite_session.read_session(connection, session_id)
