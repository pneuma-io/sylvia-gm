"""Focused schema-10 migration coverage for unbound Draft lifecycle history."""

from __future__ import annotations

import sqlite3

import pytest

from sylvia_gm.infrastructure import sqlite_campaign, sqlite_draft_lifecycle


def test_schema_10_preserves_v9_rows_and_relaxes_only_draft_lifecycle_basis() -> None:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE campaigns (campaign_id TEXT PRIMARY KEY);
            CREATE TABLE turns (
                campaign_id TEXT NOT NULL, turn_id TEXT NOT NULL,
                PRIMARY KEY (campaign_id, turn_id)
            );
            CREATE TABLE campaign_locks (
                lock_id TEXT PRIMARY KEY, campaign_id TEXT NOT NULL,
                created_revision INTEGER NOT NULL
            );
            """
        )
        connection.execute(sqlite_campaign.PURGE_CAMPAIGN_OPERATIONS_SQL)
        construction_sql = sqlite_campaign.CAMPAIGN_IDENTITY_DELTA_SQL.split(
            "CREATE TABLE campaign_constructions (", 1
        )[1]
        connection.execute("CREATE TABLE campaign_constructions (" + construction_sql)
        connection.execute("INSERT INTO campaigns VALUES ('campaign-1')")
        connection.execute("INSERT INTO turns VALUES ('campaign-1', 'turn-1')")
        connection.execute("INSERT INTO campaign_locks VALUES ('lock-1', 'campaign-1', 0)")
        connection.execute(
            "INSERT INTO campaign_operations VALUES "
            "('confirm-1', 'confirm_campaign', 'campaign-1', '{}', 'hash', '{}', "
            "'turn-1', 'lock-1', 0, 0)"
        )
        connection.execute(
            "INSERT INTO campaign_constructions "
            "(campaign_id, confirmation_operation_id, authority_turn_id, "
            "authority_lock_id, confirmation_revision) VALUES "
            "('campaign-1', 'confirm-1', 'turn-1', 'lock-1', 0)"
        )
        before = tuple(connection.execute("SELECT * FROM campaign_operations"))

        sqlite_draft_lifecycle.apply_migration(connection)

        assert tuple(connection.execute("SELECT * FROM campaign_operations")) == before
        connection.execute(
            "INSERT INTO campaign_operations VALUES "
            "('rename-1', 'rename_campaign', 'campaign-1', '{}', 'hash', '{}', "
            "NULL, NULL, 0, 1)"
        )
        connection.execute(
            "UPDATE campaign_constructions SET confirmation_revision = 1 "
            "WHERE campaign_id = 'campaign-1'"
        )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO campaign_operations VALUES "
                "('other-1', 'confirm_campaign', 'campaign-1', '{}', 'hash', '{}', "
                "NULL, NULL, 1, 1)"
            )
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "INSERT INTO campaign_operations VALUES "
                "('partial-1', 'rename_campaign', 'campaign-1', '{}', 'hash', '{}', "
                "'turn-1', NULL, 1, 2)"
            )
        assert tuple(connection.execute("PRAGMA foreign_key_check")) == ()
    finally:
        connection.close()
