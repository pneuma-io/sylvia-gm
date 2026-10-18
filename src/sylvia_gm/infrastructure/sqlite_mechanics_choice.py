"""Schema-7 durable reservations for rules-neutral post-result mechanic choices."""

from __future__ import annotations

import hashlib
import sqlite3

MIGRATION_7_NAME = "mechanics_choice_reservations"
MIGRATION_7_SQL = """
CREATE TABLE pending_mechanics_actions (
    campaign_id TEXT PRIMARY KEY,
    action_id TEXT NOT NULL,
    turn_id TEXT NOT NULL,
    basis_revision INTEGER NOT NULL CHECK (basis_revision >= 0),
    writer TEXT NOT NULL CHECK (writer IN ('builder', 'dm', 'none')),
    package_id TEXT NOT NULL,
    package_version TEXT NOT NULL,
    package_sha256 TEXT NOT NULL,
    lock_id TEXT NOT NULL,
    tool_name TEXT NOT NULL,
    parameters_json TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    initiating_session_id TEXT,
    choice_id TEXT NOT NULL,
    descriptor_json TEXT NOT NULL,
    descriptor_sha256 TEXT NOT NULL,
    UNIQUE (campaign_id, action_id),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (campaign_id, turn_id) REFERENCES turns(campaign_id, turn_id),
    FOREIGN KEY (lock_id) REFERENCES campaign_locks(lock_id),
    FOREIGN KEY (initiating_session_id) REFERENCES agent_sessions(session_id),
    FOREIGN KEY (campaign_id, package_id, package_version, package_sha256)
        REFERENCES campaign_package_memberships(campaign_id, package_id, version, sha256)
);
CREATE TABLE pending_mechanics_action_aliases (
    campaign_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    action_id TEXT NOT NULL,
    payload_hash TEXT NOT NULL,
    PRIMARY KEY (campaign_id, idempotency_key),
    FOREIGN KEY (campaign_id, action_id)
        REFERENCES pending_mechanics_actions(campaign_id, action_id) ON DELETE CASCADE
);
CREATE TABLE mechanics_choice_resolutions (
    campaign_id TEXT NOT NULL,
    resolution_id TEXT NOT NULL,
    action_id TEXT NOT NULL,
    choice_id TEXT NOT NULL,
    option_id TEXT NOT NULL,
    resolution_sha256 TEXT NOT NULL,
    descriptor_sha256 TEXT NOT NULL,
    session_id TEXT,
    resulting_revision INTEGER NOT NULL CHECK (resulting_revision >= 1),
    PRIMARY KEY (campaign_id, resolution_id),
    UNIQUE (campaign_id, action_id),
    FOREIGN KEY (campaign_id, action_id) REFERENCES actions(campaign_id, action_id),
    FOREIGN KEY (session_id) REFERENCES agent_sessions(session_id)
);
"""
MIGRATION_7_SHA256 = hashlib.sha256(MIGRATION_7_SQL.strip().encode("utf-8")).hexdigest()


def apply_migration(connection: sqlite3.Connection) -> None:
    # Keep this additive migration inside the caller's transaction. Python's
    # ``executescript`` issues an implicit COMMIT, which would split both
    # startup migration and portable recovery across two authority epochs.
    for statement in MIGRATION_7_SQL.split(";"):
        if statement.strip():
            connection.execute(statement)
