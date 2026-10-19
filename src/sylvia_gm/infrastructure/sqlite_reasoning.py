"""Private provider inspection records, separate from ordinary session messages."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Mapping

from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict

MIGRATION_11_NAME = "private_provider_records"
MIGRATION_11_SQL = """
CREATE TABLE provider_private_records (
    session_id TEXT NOT NULL,
    agent_session_id TEXT,
    builder_session_id TEXT,
    attempt_id TEXT NOT NULL,
    response_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    basis_message_id TEXT NOT NULL,
    source_order INTEGER NOT NULL CHECK (source_order >= 0),
    provider_id TEXT NOT NULL,
    model_id TEXT NOT NULL,
    kind TEXT NOT NULL CHECK (kind IN ('thinking', 'continuation')),
    payload TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('partial', 'completed', 'interrupted')),
    PRIMARY KEY (session_id, attempt_id, source_order),
    FOREIGN KEY (agent_session_id) REFERENCES agent_sessions(session_id) ON DELETE CASCADE,
    FOREIGN KEY (builder_session_id) REFERENCES standalone_builder_chats(session_id)
        ON DELETE CASCADE,
    CHECK (
        (agent_session_id = session_id AND builder_session_id IS NULL)
        OR (builder_session_id = session_id AND agent_session_id IS NULL)
    ),
    CHECK (agent_session_id IS NOT NULL OR builder_session_id IS NOT NULL)
);
""".strip()
MIGRATION_11_SHA256 = hashlib.sha256(MIGRATION_11_SQL.encode("utf-8")).hexdigest()


def apply_migration(connection: sqlite3.Connection) -> None:
    """Create the private sink inside the caller-owned schema migration transaction."""
    connection.execute(MIGRATION_11_SQL)


def append_record(
    connection: sqlite3.Connection, *, session_id: str, attempt_id: str,
    response_id: str, turn_id: str, basis_message_id: str, source_order: int,
    provider_id: str, model_id: str, kind: str, payload: str,
) -> None:
    """Retain exact ingress data once; a conflicting replay never replaces private content."""
    if (
        any(not isinstance(value, str) or not value for value in (
            session_id, attempt_id, response_id, turn_id, basis_message_id, provider_id, model_id,
        ))
        or type(source_order) is not int or source_order < 0
        or kind not in {"thinking", "continuation"}
        or not isinstance(payload, str) or not payload
    ):
        raise CommandRejected("Invalid private provider record")
    if kind == "continuation":
        try:
            native = json.loads(payload)
        except (ValueError, TypeError):
            raise CommandRejected("Invalid native continuation record") from None
        if not isinstance(native, dict):
            raise CommandRejected("Invalid native continuation record")
    agent = connection.execute(
        "SELECT 1 FROM agent_sessions WHERE session_id = ?", (session_id,),
    ).fetchone()
    builder = connection.execute(
        "SELECT 1 FROM standalone_builder_chats WHERE session_id = ?", (session_id,),
    ).fetchone()
    if bool(agent) == bool(builder):
        raise CommandRejected("Private provider record requires one known session")
    values = (
        response_id, turn_id, basis_message_id, provider_id, model_id, kind, payload,
    )
    existing = connection.execute(
        "SELECT response_id, turn_id, basis_message_id, provider_id, model_id, kind, payload "
        "FROM provider_private_records "
        "WHERE session_id = ? AND attempt_id = ? AND source_order = ?",
        (session_id, attempt_id, source_order),
    ).fetchone()
    if existing is not None:
        if tuple(existing) != values:
            raise IdempotencyConflict("Private provider record identity already has different data")
        return
    if connection.execute(
        "SELECT 1 FROM provider_private_records WHERE session_id = ? AND attempt_id = ? "
        "AND status != 'partial' LIMIT 1", (session_id, attempt_id),
    ).fetchone() is not None:
        raise CommandRejected("Private provider attempt is already terminal")
    connection.execute(
        "INSERT INTO provider_private_records "
        "(session_id, agent_session_id, builder_session_id, attempt_id, source_order, "
        "response_id, turn_id, basis_message_id, provider_id, model_id, kind, payload, status) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'partial')",
        (session_id, session_id if agent else None, session_id if builder else None,
         attempt_id, source_order, *values),
    )


def finish_attempt(
    connection: sqlite3.Connection, session_id: str, attempt_id: str, *, completed: bool,
) -> None:
    """Mark existing partial records terminal; absent reasoning never creates a display entry."""
    connection.execute(
        "UPDATE provider_private_records SET status = ? "
        "WHERE session_id = ? AND attempt_id = ? AND status = 'partial'",
        ("completed" if completed else "interrupted", session_id, attempt_id),
    )


def inspection_index(connection: sqlite3.Connection, session_id: str) -> list[dict[str, object]]:
    """Read only availability and correlation, never readable text or opaque protocol content."""
    rows = connection.execute(
        "SELECT attempt_id, response_id, turn_id, basis_message_id, "
        "MIN(source_order), status, COUNT(*) "
        "FROM provider_private_records WHERE session_id = ? AND kind = 'thinking' "
        "GROUP BY attempt_id, response_id, turn_id, basis_message_id, status "
        "ORDER BY MIN(rowid)", (session_id,),
    )
    fields = (
        "attempt_id", "response_id", "turn_id", "basis_message_id", "source_order", "status",
        "chunk_count",
    )
    return [dict(zip(fields, row, strict=True)) for row in rows]


def read_thinking(
    connection: sqlite3.Connection, session_id: str, attempt_id: str, response_id: str,
) -> str | None:
    """Explicit inspection read; never exposes native continuation bytes as display text."""
    rows = connection.execute(
        "SELECT payload FROM provider_private_records WHERE session_id = ? AND attempt_id = ? "
        "AND response_id = ? AND kind = 'thinking' ORDER BY source_order",
        (session_id, attempt_id, response_id),
    ).fetchall()
    return "".join(str(row[0]) for row in rows) if rows else None


def read_continuation(
    connection: sqlite3.Connection, session_id: str, response_id: str,
    *, provider_id: str, model_id: str,
) -> Mapping[str, object] | None:
    """Retrieve exact native data only for its originating provider, model and response."""
    rows = connection.execute(
        "SELECT payload FROM provider_private_records WHERE session_id = ? AND response_id = ? "
        "AND provider_id = ? AND model_id = ? AND kind = 'continuation' ORDER BY rowid",
        (session_id, response_id, provider_id, model_id),
    ).fetchall()
    if not rows:
        return None
    if len(rows) != 1:
        raise CommandRejected("Native continuation identity is ambiguous")
    value: dict[str, object] = json.loads(str(rows[0][0]))
    return value


def read_actor_context(
    connection: sqlite3.Connection, session_id: str, context_id: str,
    *, provider_id: str, model_id: str,
) -> Mapping[str, object] | None:
    """Assemble once-stored complete exchanges from one private participant namespace."""
    if len(context_id) != 64 or any(char not in "0123456789abcdef" for char in context_id):
        raise CommandRejected("Invalid actor context identity")
    rows = connection.execute(
        "SELECT payload FROM provider_private_records WHERE session_id = ? "
        "AND response_id LIKE ? AND provider_id = ? AND model_id = ? AND kind = 'continuation' "
        "ORDER BY rowid",
        (session_id, "actor-context-" + context_id + "-%", provider_id, model_id),
    ).fetchall()
    if not rows:
        return None
    messages = []
    for row in rows:
        payload = json.loads(str(row[0]))
        context = payload.get("actor_context")
        if (not isinstance(context, dict) or context.get("format") != 1
                or context.get("context_id") != context_id
                or not isinstance(context.get("messages"), list)):
            raise CommandRejected("Actor context ownership is invalid")
        messages.extend(context["messages"])
    return {**context, "messages": messages}
