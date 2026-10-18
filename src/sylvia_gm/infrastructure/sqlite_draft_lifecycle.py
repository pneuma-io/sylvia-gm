"""Schema-10 support for lifecycle receipts created before Campaign binding."""

from __future__ import annotations

import hashlib
import sqlite3

MIGRATION_10_NAME = "unbound_draft_lifecycle"

CAMPAIGN_OPERATIONS_SQL = """
CREATE TABLE campaign_operations (
    operation_id TEXT PRIMARY KEY,
    operation_kind TEXT NOT NULL,
    campaign_id TEXT NOT NULL,
    semantic_payload_json TEXT NOT NULL,
    semantic_payload_sha256 TEXT NOT NULL,
    result_json TEXT NOT NULL,
    basis_turn_id TEXT,
    basis_lock_id TEXT,
    basis_revision INTEGER NOT NULL CHECK (basis_revision >= 0),
    resulting_revision INTEGER NOT NULL CHECK (resulting_revision >= 0),
    FOREIGN KEY (campaign_id, basis_turn_id) REFERENCES turns(campaign_id, turn_id),
    FOREIGN KEY (basis_lock_id) REFERENCES campaign_locks(lock_id),
    CHECK (
        (basis_turn_id IS NULL AND basis_lock_id IS NULL
            AND operation_kind IN (
                'rename_campaign', 'archive_campaign', 'restore_campaign',
                'trash_campaign', 'purge_campaign'
            ))
        OR
        (basis_turn_id IS NOT NULL AND basis_lock_id IS NOT NULL
            AND operation_kind != 'purge_campaign')
    )
)
""".strip()

CAMPAIGN_CONSTRUCTIONS_SQL = """
CREATE TABLE campaign_constructions (
    campaign_id TEXT PRIMARY KEY,
    confirmation_operation_id TEXT NOT NULL UNIQUE,
    authority_turn_id TEXT NOT NULL,
    authority_lock_id TEXT NOT NULL,
    confirmation_revision INTEGER NOT NULL CHECK (confirmation_revision >= 0),
    lane TEXT CHECK (lane IN ('quick_start', 'customize')),
    canonical_input_json TEXT,
    begin_operation_id TEXT UNIQUE,
    begin_revision INTEGER CHECK (begin_revision > 0),
    canonical_plan_json TEXT,
    completion_operation_id TEXT UNIQUE,
    completion_revision INTEGER CHECK (completion_revision > 0),
    canonical_representation_sha256 TEXT,
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE,
    FOREIGN KEY (confirmation_operation_id) REFERENCES campaign_operations(operation_id),
    FOREIGN KEY (begin_operation_id) REFERENCES campaign_operations(operation_id),
    FOREIGN KEY (completion_operation_id) REFERENCES campaign_operations(operation_id),
    FOREIGN KEY (campaign_id, authority_turn_id) REFERENCES turns(campaign_id, turn_id),
    FOREIGN KEY (authority_lock_id) REFERENCES campaign_locks(lock_id),
    CHECK (
        (lane IS NULL AND canonical_input_json IS NULL AND begin_operation_id IS NULL
            AND begin_revision IS NULL AND canonical_plan_json IS NULL
            AND completion_operation_id IS NULL AND completion_revision IS NULL
            AND canonical_representation_sha256 IS NULL)
        OR
        (lane IS NOT NULL AND canonical_input_json IS NOT NULL AND begin_operation_id IS NOT NULL
            AND begin_revision IS NOT NULL AND canonical_plan_json IS NULL
            AND completion_operation_id IS NULL AND completion_revision IS NULL
            AND canonical_representation_sha256 IS NULL)
        OR
        (lane IS NOT NULL AND canonical_input_json IS NOT NULL AND begin_operation_id IS NOT NULL
            AND begin_revision IS NOT NULL AND canonical_plan_json IS NOT NULL
            AND completion_operation_id IS NOT NULL AND completion_revision IS NOT NULL
            AND canonical_representation_sha256 IS NOT NULL)
    )
)
""".strip()

MIGRATION_10_SQL = "\n".join(
    (
        "PRAGMA defer_foreign_keys = ON;",
        "CREATE TEMP TABLE campaign_operations_draft_shadow AS SELECT * FROM campaign_operations;",
        "CREATE TEMP TABLE campaign_constructions_draft_shadow AS "
        "SELECT * FROM campaign_constructions;",
        "DROP TABLE campaign_constructions;",
        "DROP TABLE campaign_operations;",
        CAMPAIGN_OPERATIONS_SQL + ";",
        CAMPAIGN_CONSTRUCTIONS_SQL + ";",
        "INSERT INTO campaign_operations SELECT * FROM campaign_operations_draft_shadow;",
        "INSERT INTO campaign_constructions SELECT * FROM campaign_constructions_draft_shadow;",
        "DROP TABLE campaign_operations_draft_shadow;",
        "DROP TABLE campaign_constructions_draft_shadow;",
    )
)
MIGRATION_10_SHA256 = hashlib.sha256(MIGRATION_10_SQL.strip().encode("utf-8")).hexdigest()


def _rows(
    connection: sqlite3.Connection, table: str, order_by: str
) -> tuple[tuple[object, ...], ...]:
    return tuple(
        tuple(row) for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order_by}")
    )


def apply_migration(connection: sqlite3.Connection) -> None:
    """Rebuild both referenced tables while preserving every predecessor row exactly."""

    before_operations = _rows(connection, "campaign_operations", "operation_id")
    before_constructions = _rows(connection, "campaign_constructions", "campaign_id")
    for statement in MIGRATION_10_SQL.split(";"):
        if statement.strip():
            connection.execute(statement)
    if _rows(connection, "campaign_operations", "operation_id") != before_operations:
        raise ValueError("Draft lifecycle migration changed campaign operation history")
    if _rows(connection, "campaign_constructions", "campaign_id") != before_constructions:
        raise ValueError("Draft lifecycle migration changed campaign construction history")
    if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
        raise ValueError("Draft lifecycle migration has invalid foreign keys")
