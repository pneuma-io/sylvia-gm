"""Schema-6 relational ownership for mutable campaign-state namespaces."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable

from sylvia_gm.domain.authority import PackageLock
from sylvia_gm.domain.errors import CommandRejected, SchemaIdentityError
from sylvia_gm.domain.state_ownership import owner_matches_package_lock

MIGRATION_6_NAME = "f7_relational_state_namespace_ownership"
MIGRATION_6_SQL = """
CREATE TABLE campaign_state_namespace_owners (
    campaign_id TEXT NOT NULL,
    namespace TEXT NOT NULL,
    owner_package_id TEXT,
    PRIMARY KEY (campaign_id, namespace),
    FOREIGN KEY (campaign_id) REFERENCES campaigns(campaign_id) ON DELETE CASCADE
);
CREATE TRIGGER campaign_state_owner_matches_current_lock
BEFORE INSERT ON campaign_state_namespace_owners
BEGIN
    SELECT CASE WHEN
        (
            NEW.owner_package_id IS NULL
            AND EXISTS (
                SELECT 1 FROM campaigns AS campaign
                JOIN campaign_lock_memberships AS membership
                  ON membership.lock_id = campaign.current_lock_id
                WHERE campaign.campaign_id = NEW.campaign_id
            )
        )
        OR (
            NEW.owner_package_id IS NOT NULL
            AND NOT EXISTS (
                SELECT 1 FROM campaigns AS campaign
                JOIN campaign_lock_memberships AS membership
                  ON membership.lock_id = campaign.current_lock_id
                WHERE campaign.campaign_id = NEW.campaign_id
                  AND membership.package_id = NEW.owner_package_id
            )
        )
        OR EXISTS (
            SELECT 1 FROM campaigns AS campaign
            JOIN campaign_lock_memberships AS membership
              ON membership.lock_id = campaign.current_lock_id
            WHERE campaign.campaign_id = NEW.campaign_id
              AND NEW.namespace LIKE membership.package_id || '.%'
              AND membership.package_id <> NEW.owner_package_id
        )
        THEN RAISE(ABORT, 'campaign state namespace owner conflicts with current lock')
    END;
END;
CREATE TRIGGER campaign_state_owner_required_insert
BEFORE INSERT ON campaign_state
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM campaign_state_namespace_owners AS owner
        WHERE owner.campaign_id = NEW.campaign_id
          AND owner.namespace = NEW.namespace
    ) THEN RAISE(ABORT, 'campaign state namespace owner is required') END;
END;
CREATE TRIGGER campaign_state_owner_required_update
BEFORE UPDATE OF campaign_id, namespace ON campaign_state
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1 FROM campaign_state_namespace_owners AS owner
        WHERE owner.campaign_id = NEW.campaign_id
          AND owner.namespace = NEW.namespace
    ) THEN RAISE(ABORT, 'campaign state namespace owner is required') END;
END;
CREATE TRIGGER campaign_state_owner_immutable
BEFORE UPDATE ON campaign_state_namespace_owners
BEGIN
    SELECT RAISE(ABORT, 'campaign state namespace owner is immutable');
END;
CREATE TRIGGER campaign_state_owner_delete_requires_empty
BEFORE DELETE ON campaign_state_namespace_owners
WHEN EXISTS (
    SELECT 1 FROM campaign_state AS state
    WHERE state.campaign_id = OLD.campaign_id
      AND state.namespace = OLD.namespace
)
BEGIN
    SELECT RAISE(ABORT, 'campaign state namespace owner still has state');
END;
CREATE TRIGGER mutation_requires_matching_namespace_owner
BEFORE INSERT ON mutations
BEGIN
    SELECT CASE WHEN NOT EXISTS (
        SELECT 1
        FROM actions AS action
        JOIN campaign_state_namespace_owners AS owner
          ON owner.campaign_id = NEW.campaign_id
         AND owner.namespace = NEW.namespace
        WHERE action.campaign_id = NEW.campaign_id
          AND action.action_id = NEW.action_id
          AND owner.owner_package_id IS NOT NULL
          AND owner.owner_package_id = action.package_id
    ) THEN RAISE(ABORT, 'mutation package does not own state namespace') END;
END;
"""
MIGRATION_6_SHA256 = hashlib.sha256(MIGRATION_6_SQL.strip().encode("utf-8")).hexdigest()

_TRIGGER_NAMES = (
    "campaign_state_owner_delete_requires_empty",
    "campaign_state_owner_immutable",
    "campaign_state_owner_matches_current_lock",
    "campaign_state_owner_required_insert",
    "campaign_state_owner_required_update",
    "mutation_requires_matching_namespace_owner",
)


def trigger_identity(connection: sqlite3.Connection) -> tuple[tuple[str, str], ...]:
    """Return normalized SQL for every schema-6 ownership trigger."""

    return tuple(
        (str(row[0]), " ".join(str(row[1]).split()).casefold())
        for row in connection.execute(
            "SELECT name, sql FROM sqlite_master WHERE type = 'trigger' "
            f"AND name IN ({','.join('?' for _ in _TRIGGER_NAMES)}) ORDER BY name",
            _TRIGGER_NAMES,
        )
    )


def expected_trigger_identity() -> tuple[tuple[str, str], ...]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.executescript(
            """
            CREATE TABLE campaigns (
                campaign_id TEXT PRIMARY KEY,
                current_lock_id TEXT
            );
            CREATE TABLE campaign_lock_memberships (
                lock_id TEXT NOT NULL,
                package_id TEXT NOT NULL
            );
            CREATE TABLE campaign_state (
                campaign_id TEXT NOT NULL,
                namespace TEXT NOT NULL,
                state_key TEXT NOT NULL,
                value_json TEXT NOT NULL,
                PRIMARY KEY (campaign_id, namespace, state_key)
            );
            CREATE TABLE actions (
                campaign_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                package_id TEXT NOT NULL,
                PRIMARY KEY (campaign_id, action_id)
            );
            CREATE TABLE mutations (
                campaign_id TEXT NOT NULL,
                action_id TEXT NOT NULL,
                ordinal INTEGER NOT NULL,
                namespace TEXT NOT NULL,
                state_key TEXT NOT NULL,
                before_json TEXT NOT NULL,
                after_json TEXT NOT NULL,
                PRIMARY KEY (campaign_id, action_id, ordinal)
            );
            """
        )
        connection.executescript(MIGRATION_6_SQL)
        return trigger_identity(connection)
    finally:
        connection.close()


EXPECTED_TRIGGER_IDENTITY = expected_trigger_identity()


def _current_package_ids(connection: sqlite3.Connection, campaign_id: str) -> tuple[str, ...]:
    return tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT membership.package_id FROM campaigns AS campaign "
            "JOIN campaign_lock_memberships AS membership "
            "ON membership.lock_id = campaign.current_lock_id "
            "WHERE campaign.campaign_id = ? ORDER BY membership.closure_order",
            (campaign_id,),
        )
    )


def _current_primary_package_ids(
    connection: sqlite3.Connection, campaign_id: str
) -> tuple[str, ...]:
    return tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT membership.package_id FROM campaigns AS campaign "
            "JOIN campaign_lock_memberships AS membership "
            "ON membership.lock_id = campaign.current_lock_id "
            "WHERE campaign.campaign_id = ? AND membership.role = 'primary' "
            "ORDER BY membership.package_id",
            (campaign_id,),
        )
    )


def _prefix_matches(package_ids: Iterable[str], namespace: str) -> tuple[str, ...]:
    return tuple(
        package_id
        for package_id in package_ids
        if namespace == package_id or namespace.startswith(package_id + ".")
    )


def _historical_writers(
    connection: sqlite3.Connection, campaign_id: str, namespace: str
) -> tuple[str, ...]:
    return tuple(
        str(row[0])
        for row in connection.execute(
            "SELECT DISTINCT action.package_id FROM mutations AS mutation "
            "JOIN actions AS action ON action.campaign_id = mutation.campaign_id "
            "AND action.action_id = mutation.action_id "
            "WHERE mutation.campaign_id = ? AND mutation.namespace = ? "
            "ORDER BY action.package_id",
            (campaign_id, namespace),
        )
    )


def _backfill_owner(connection: sqlite3.Connection, campaign_id: str, namespace: str) -> str | None:
    package_ids = _current_package_ids(connection, campaign_id)
    writers = _historical_writers(connection, campaign_id, namespace)
    matches = _prefix_matches(package_ids, namespace)
    if any(writer not in package_ids for writer in writers):
        raise SchemaIdentityError(
            "Campaign state namespace ownership writer is absent from current lock"
        )
    if len(writers) > 1 or len(matches) > 1:
        raise SchemaIdentityError("Campaign state namespace ownership is ambiguous")
    if writers:
        writer = writers[0]
        if matches and matches[0] != writer:
            raise SchemaIdentityError(
                "Campaign state namespace ownership conflicts with its prefix"
            )
        return writer
    if matches:
        return matches[0]
    if len(package_ids) == 1:
        return package_ids[0]
    primary_package_ids = _current_primary_package_ids(connection, campaign_id)
    if len(primary_package_ids) == 1:
        return primary_package_ids[0]
    if not package_ids:
        return None
    raise SchemaIdentityError("Campaign state namespace ownership is ambiguous")


def _execute_migration_sql(connection: sqlite3.Connection) -> None:
    statement = ""
    for line in MIGRATION_6_SQL.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            connection.execute(statement)
            statement = ""
    if statement.strip():
        raise SchemaIdentityError("State ownership migration SQL is incomplete")


def apply_migration(connection: sqlite3.Connection) -> None:
    """Install schema 6 and deterministically backfill exact current namespaces."""

    _execute_migration_sql(connection)
    for row in connection.execute(
        "SELECT DISTINCT campaign_id, namespace FROM campaign_state ORDER BY campaign_id, namespace"
    ):
        campaign_id = str(row[0])
        namespace = str(row[1])
        connection.execute(
            "INSERT INTO campaign_state_namespace_owners "
            "(campaign_id, namespace, owner_package_id) VALUES (?, ?, ?)",
            (campaign_id, namespace, _backfill_owner(connection, campaign_id, namespace)),
        )


def initial_owner(package_lock: PackageLock, namespace: str) -> str | None:
    """Derive one initial-state owner without allowing caller-selected ownership."""

    package_ids = tuple(entry.package_id for entry in package_lock.entries)
    matches = _prefix_matches(package_ids, namespace)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise CommandRejected(
            "Campaign state namespace ownership is ambiguous",
            code="state_namespace_ownership_invalid",
        )
    if len(package_ids) == 1:
        return str(package_ids[0])
    if not package_ids:
        return None
    raise CommandRejected(
        "Campaign state namespace ownership is ambiguous",
        code="state_namespace_ownership_invalid",
    )


def activation_owner(
    package_lock: PackageLock,
    namespace: str,
    previous_owner: str | None,
) -> str | None:
    """Derive replacement ownership under a newly activated exact lock."""

    package_ids = tuple(entry.package_id for entry in package_lock.entries)
    matches = _prefix_matches(package_ids, namespace)
    if len(matches) == 1:
        return matches[0]
    if len(matches) > 1:
        raise CommandRejected(
            "Campaign state namespace ownership is ambiguous",
            code="state_namespace_ownership_invalid",
        )
    if previous_owner is not None and previous_owner in package_ids:
        return previous_owner
    if len(package_ids) == 1:
        return str(package_ids[0])
    if not package_ids:
        return None
    raise CommandRejected(
        "Campaign state namespace ownership is ambiguous",
        code="state_namespace_ownership_invalid",
    )


def package_uses_content_declarations(
    connection: sqlite3.Connection,
    package_id: str,
    package_version: str,
    package_sha256: str,
) -> bool:
    row = connection.execute(
        "SELECT canonical_json FROM package_manifests "
        "WHERE package_id = ? AND version = ? AND content_sha256 = ? AND status = 'published'",
        (package_id, package_version, package_sha256),
    ).fetchone()
    if row is None:
        raise CommandRejected("Action package is unavailable")
    try:
        manifest = json.loads(str(row[0]))
    except (TypeError, ValueError) as error:
        raise SchemaIdentityError("Package manifest identity is invalid") from error
    return isinstance(manifest, dict) and isinstance(manifest.get("content_declarations"), str)


def owner_for_mutation(
    connection: sqlite3.Connection,
    *,
    campaign_id: str,
    actor_package_id: str,
    actor_package_version: str,
    actor_package_sha256: str,
    namespace: str,
) -> tuple[str, bool]:
    """Resolve an existing owner or validate one new actor-owned claim."""

    row = connection.execute(
        "SELECT owner_package_id FROM campaign_state_namespace_owners "
        "WHERE campaign_id = ? AND namespace = ?",
        (campaign_id, namespace),
    ).fetchone()
    if row is not None:
        owner = row[0]
        recorded_owner = None if owner is None else str(owner)
        if (
            not owner_matches_package_lock(
                _current_package_ids(connection, campaign_id), namespace, recorded_owner
            )
            or recorded_owner != actor_package_id
        ):
            raise CommandRejected(
                "Tool execution failed",
                code="tool_execution_failed",
            )
        return actor_package_id, False

    package_ids = _current_package_ids(connection, campaign_id)
    matches = _prefix_matches(package_ids, namespace)
    if (len(matches) != 1 or matches[0] != actor_package_id) and (
        package_uses_content_declarations(
            connection,
            actor_package_id,
            actor_package_version,
            actor_package_sha256,
        )
        or matches
    ):
        raise CommandRejected("Tool execution failed", code="tool_execution_failed")
    return actor_package_id, True


def verify_authority(connection: sqlite3.Connection) -> None:
    """Fail closed on missing, orphaned, mutable, or cross-package ownership evidence."""

    if trigger_identity(connection) != EXPECTED_TRIGGER_IDENTITY:
        raise SchemaIdentityError("Campaign state namespace ownership triggers are invalid")
    orphan_state = connection.execute(
        "SELECT 1 FROM campaign_state AS state "
        "LEFT JOIN campaign_state_namespace_owners AS owner "
        "ON owner.campaign_id = state.campaign_id AND owner.namespace = state.namespace "
        "WHERE owner.campaign_id IS NULL LIMIT 1"
    ).fetchone()
    if orphan_state is not None:
        raise SchemaIdentityError("Campaign state namespace ownership is incomplete")
    owner_without_state = connection.execute(
        "SELECT 1 FROM campaign_state_namespace_owners AS owner "
        "LEFT JOIN campaign_state AS state ON state.campaign_id = owner.campaign_id "
        "AND state.namespace = owner.namespace WHERE state.campaign_id IS NULL LIMIT 1"
    ).fetchone()
    if owner_without_state is not None:
        raise SchemaIdentityError("Campaign state namespace ownership is not canonical")
    for row in connection.execute(
        "SELECT campaign_id, namespace, owner_package_id "
        "FROM campaign_state_namespace_owners ORDER BY campaign_id, namespace"
    ):
        campaign_id = str(row[0])
        namespace = str(row[1])
        owner = None if row[2] is None else str(row[2])
        if not owner_matches_package_lock(
            _current_package_ids(connection, campaign_id), namespace, owner
        ):
            raise SchemaIdentityError("Campaign state namespace owner conflicts with current lock")
        writers = _historical_writers(connection, campaign_id, namespace)
        if any(writer != owner for writer in writers):
            raise SchemaIdentityError(
                "Campaign state namespace owner conflicts with mutation history"
            )
