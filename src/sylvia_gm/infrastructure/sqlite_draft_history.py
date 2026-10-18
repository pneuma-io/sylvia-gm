"""Recognize campaign revisions recorded before the Interview selects a gameplay basis."""

from __future__ import annotations

import sqlite3

DRAFT_LIFECYCLE_KINDS = frozenset({
    "rename_campaign", "archive_campaign", "restore_campaign", "trash_campaign",
})


def has_only_draft_lifecycle_history(
    connection: sqlite3.Connection, campaign_id: str, revision: int,
) -> bool:
    """Require every pre-binding revision to have its own lifecycle receipt and no game basis."""
    if revision == 0:
        return True
    rows = connection.execute(
        "SELECT revision.revision, operation.operation_kind, operation.basis_revision, "
        "operation.resulting_revision, operation.basis_turn_id, operation.basis_lock_id "
        "FROM campaign_revisions AS revision LEFT JOIN campaign_operations AS operation "
        "ON operation.operation_id = revision.lifecycle_operation_id "
        "AND operation.campaign_id = revision.campaign_id "
        "WHERE revision.campaign_id = ? AND revision.revision BETWEEN 1 AND ? "
        "ORDER BY revision.revision", (campaign_id, revision),
    ).fetchall()
    if len(rows) != revision or any(
        row[0] != number or row[1] not in DRAFT_LIFECYCLE_KINDS
        or row[2] != number - 1 or row[3] != number
        or row[4] is not None or row[5] is not None
        for number, row in enumerate(rows, start=1)
    ):
        return False
    return not any(connection.execute(query, (campaign_id, revision)).fetchone() for query in (
        "SELECT 1 FROM campaign_locks WHERE campaign_id = ? AND created_revision < ? LIMIT 1",
        "SELECT 1 FROM turns WHERE campaign_id = ? AND created_revision < ? LIMIT 1",
    ))
