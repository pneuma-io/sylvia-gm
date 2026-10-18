"""Upgrade an actual prior-format campaign and its saved Builder history through owner startup."""

import sqlite3

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import PackageLock
from sylvia_gm.infrastructure import sqlite_campaign
from tests.test_interview_lifecycle import finish_interview, new_draft


def test_owner_upgrades_v9_campaign_and_independent_chat_without_changing_history(tmp_path):
    database = tmp_path / "upgrade.db"
    with AuthorityOwner.open(database, "seed") as owner:
        draft = new_draft(owner)
        chat = owner.start_interview_builder_chat(
            operation_id="build-rule", campaign_id=draft.campaign_id,
            parent_session_id=draft.builder_session_id, expected_construction_revision=0,
            purpose="ruleset", visible_answer="New Ruleset", child_session_id="rule-builder",
        )
        finish_interview(owner, draft)
        owner.bind_guided_campaign_basis(
            operation_id="bind", campaign_id=draft.campaign_id,
            display_name="The restored campaign", package_lock=PackageLock(entries=()),
            turn_id="construction", builder_session_id=draft.builder_session_id,
        )
        before_campaign = owner.snapshot(draft.campaign_id)
        before_interview = owner.session_context(draft.builder_session_id)
        before_chat = owner.session_context(chat.child_session_id)

    # Rebuild just the changed tables with their frozen v9 definitions. All records in this
    # fixture are valid before Draft lifecycle support; no user database is downgraded.
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.execute("PRAGMA defer_foreign_keys = ON")
        for table in ("campaign_operations", "campaign_constructions"):
            connection.execute(f"CREATE TEMP TABLE old_{table} AS SELECT * FROM {table}")
        connection.execute("DROP TABLE campaign_constructions")
        connection.execute("DROP TABLE campaign_operations")
        operations_sql = sqlite_campaign.CAMPAIGN_IDENTITY_DELTA_SQL.split(
            "CREATE TABLE campaign_operations (", 1,
        )[1].split("CREATE TABLE campaign_constructions (", 1)[0].rstrip().rstrip(";")
        connection.execute("CREATE TABLE campaign_operations (" + operations_sql)
        construction_sql = sqlite_campaign.CAMPAIGN_IDENTITY_DELTA_SQL.split(
            "CREATE TABLE campaign_constructions (", 1,
        )[1]
        connection.execute("CREATE TABLE campaign_constructions (" + construction_sql)
        for table in ("campaign_operations", "campaign_constructions"):
            connection.execute(f"INSERT INTO {table} SELECT * FROM old_{table}")
            connection.execute(f"DROP TABLE old_{table}")
        connection.execute("DELETE FROM schema_migrations WHERE version = 10")
        connection.execute("PRAGMA user_version = 9")

    with AuthorityOwner.open(database, "upgrade") as owner:
        assert owner.snapshot(draft.campaign_id) == before_campaign
        assert owner.session_context(draft.builder_session_id) == before_interview
        assert owner.session_context(chat.child_session_id) == before_chat
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 10
        assert connection.execute("PRAGMA foreign_key_check").fetchone() is None
    preserved = tmp_path / "migration-sources/upgrade.pre-migration-v9.sqlite3"
    assert preserved.is_file()
