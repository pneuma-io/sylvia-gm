"""AR-20 persistence and portable recovery through the real session authority."""

import sqlite3
from dataclasses import replace

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import WriterRole
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.infrastructure import sqlite_effort
from tests.test_alpha_f5_smoke_backup import _campaign


def create_sessions(owner):
    """Create both saved-session kinds with ordinary history in an isolated fixture."""
    _campaign(owner, campaign_id="campaign")
    owner.complete_turn("campaign", "turn-campaign")
    owner.create_agent_session(
        campaign_id="campaign",
        role=WriterRole.BUILDER,
        provider_id="test",
        model_id="model",
        selected_skill="BUILD CAMPAIGN SETTING",
        session_id="campaign-session",
    )
    owner.create_builder_chat(provider_id="test", model_id="model", session_id="chat")
    for identity in ("campaign-session", "chat"):
        owner.append_agent_message(
            session_id=identity,
            message_id="input",
            kind=SessionMessageKind.USER,
            content={"text": "Synthetic saved input"},
        )


def test_preferences_survive_reopen_backup_model_changes_and_clearing_once(tmp_path):
    path, backup = tmp_path / "source.db", tmp_path / "backup.db"
    with AuthorityOwner.open(path, "source") as owner:
        create_sessions(owner)
        owner.rename_builder_chat("chat", "Named chat")
        contexts = {
            identity: owner.session_context(identity) for identity in ("campaign-session", "chat")
        }
        campaign = owner.snapshot("campaign")
        for identity in contexts:
            owner.update_agent_session_reasoning(identity, effort="high", once="low")
            owner.update_agent_session_model(identity, provider_id="other", model_id="other")
        owner.backup_database(backup)
        assert owner.snapshot("campaign") == campaign
    for database in (path, backup):
        with AuthorityOwner.open(database, "reopen") as owner:
            for identity, original in contexts.items():
                current = owner.session_context(identity)
                assert current.messages == original.messages
                assert current.session.reasoning_effort == "high"
                assert current.session.reasoning_effort_once == "low"
                assert current.session.model_id == "other"
                owner.update_agent_session_reasoning(identity, effort="high", once=None)
                assert owner.session_context(identity).session == replace(
                    current.session,
                    reasoning_effort_once=None,
                )


def test_campaign_export_recovers_both_preferences_without_replaying_history(tmp_path):
    path, archive, destination = (
        tmp_path / "source.db",
        tmp_path / "campaign.zip",
        tmp_path / "target.db",
    )
    with AuthorityOwner.open(path, "source") as owner:
        create_sessions(owner)
        owner.update_agent_session_reasoning(
            "campaign-session", effort="provider_default", once="xhigh"
        )
        before = owner.session_context("campaign-session")
        owner.export_campaign(operation_id="export", campaign_id="campaign", target=archive)
    with AuthorityOwner.open(destination, "target") as owner:
        owner.recover_campaign(recovery_id="recover", source=archive)
        assert owner.session_context("campaign-session") == before
    with AuthorityOwner.open(destination, "reopened") as owner:
        assert owner.session_context("campaign-session") == before


def legacy_database(path):
    """Reconstruct the exact version-11 predecessor using only synthetic records."""
    with AuthorityOwner.open(path, "source") as owner:
        create_sessions(owner)
    with sqlite3.connect(path) as connection:
        for table in ("agent_sessions", "standalone_builder_chats"):
            connection.execute(f"ALTER TABLE {table} DROP COLUMN reasoning_effort")
            connection.execute(f"ALTER TABLE {table} DROP COLUMN reasoning_effort_once")
        connection.execute("DELETE FROM schema_migrations WHERE version = 12")
        connection.execute("PRAGMA user_version = 11")


def test_legacy_upgrade_preserves_session_hashes_and_history(tmp_path):
    path = tmp_path / "legacy.db"
    legacy_database(path)
    with sqlite3.connect(path) as connection:
        hashes = [
            connection.execute(f"SELECT payload_sha256 FROM {table}").fetchone()[0]
            for table in ("agent_sessions", "standalone_builder_chats")
        ]
    with AuthorityOwner.open(path, "upgrade") as owner:
        for identity in ("campaign-session", "chat"):
            context = owner.session_context(identity)
            assert context.session.reasoning_effort is None
            assert context.session.reasoning_effort_once is None
            assert context.messages[0].content["text"] == "Synthetic saved input"
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 12
        assert hashes == [
            connection.execute(f"SELECT payload_sha256 FROM {table}").fetchone()[0]
            for table in ("agent_sessions", "standalone_builder_chats")
        ]


def test_interrupted_migration_rolls_back_and_reopens(tmp_path, monkeypatch):
    path = tmp_path / "legacy.db"
    legacy_database(path)

    def interrupted(connection):
        connection.execute("ALTER TABLE agent_sessions ADD COLUMN reasoning_effort TEXT")
        raise RuntimeError("Synthetic interruption")

    with monkeypatch.context() as patch:
        patch.setattr(sqlite_effort, "apply_migration", interrupted)
        with pytest.raises(RuntimeError, match="stored integrity could not be verified"):
            AuthorityOwner.open(path, "interrupted")
    with sqlite3.connect(path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 11
        assert "reasoning_effort" not in [
            row[1] for row in connection.execute("PRAGMA table_info(agent_sessions)")
        ]
    with AuthorityOwner.open(path, "retry") as owner:
        assert owner.session_context("campaign-session").session.reasoning_effort is None


def test_invalid_preference_is_atomic_and_stored_preferences_are_hash_bound(tmp_path):
    path = tmp_path / "source.db"
    with AuthorityOwner.open(path, "source") as owner:
        create_sessions(owner)
        owner.update_agent_session_reasoning("chat", effort="high", once=None)
        before = owner.session_context("chat")
        with pytest.raises(ValueError, match="native setting name"):
            owner.update_agent_session_reasoning("chat", effort="max", once="not a setting")
        assert owner.session_context("chat") == before
    with sqlite3.connect(path) as connection:
        connection.execute("UPDATE standalone_builder_chats SET reasoning_effort = 'low'")
    with pytest.raises(RuntimeError, match="stored integrity"):
        AuthorityOwner.open(path, "tampered")
