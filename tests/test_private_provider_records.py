"""Synthetic privacy and replay tests for the separate provider-record storage boundary."""

import json
import sqlite3

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict
from sylvia_gm.domain.provider_records import ProviderPrivateRecord
from sylvia_gm.infrastructure import sqlite_reasoning
from sylvia_gm.infrastructure.sqlite_authority import SQLiteAuthorityStore


def open_fixture(path):
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("CREATE TABLE agent_sessions (session_id TEXT PRIMARY KEY)")
    connection.execute("CREATE TABLE standalone_builder_chats (session_id TEXT PRIMARY KEY)")
    connection.execute("INSERT INTO agent_sessions VALUES ('gm')")
    connection.execute("INSERT INTO standalone_builder_chats VALUES ('builder')")
    sqlite_reasoning.apply_migration(connection)
    connection.commit()
    return connection


def record(connection, **overrides):
    fields = {"session_id": "gm", "attempt_id": "attempt", "response_id": "response",
              "turn_id": "turn", "basis_message_id": "user", "source_order": 0,
              "provider_id": "provider", "model_id": "model", "kind": "thinking",
              "payload": "Synthetic private thought."}
    fields.update(overrides)
    sqlite_reasoning.append_record(connection, **fields)


@pytest.mark.parametrize("session_id", ["gm", "builder"])
@pytest.mark.parametrize("native", [
    {"anthropic_content": [{"type": "redacted_thinking", "data": "opaque"}]},
    {"chat_completion_metadata": {
        "reasoning_details": [{"type": "reasoning.encrypted", "data": "opaque"}],
        "tool_call_extras": {"call": {"google": {"thought_signature": "opaque"}}},
    }},
])
def test_inspection_is_explicit_ordered_and_survives_reopen(tmp_path, session_id, native):
    path = tmp_path / "private.db"
    connection = open_fixture(path)
    record(connection, session_id=session_id, payload="first ")
    record(connection, session_id=session_id, source_order=2, payload="last")
    record(connection, session_id=session_id, source_order=3, kind="continuation",
           payload=json.dumps(native))
    record(connection, session_id=session_id, source_order=1, payload="middle ")
    index = sqlite_reasoning.inspection_index(connection, session_id)
    assert "first" not in json.dumps(index) and "opaque" not in json.dumps(index)
    assert len(index) == 1 and index[0]["status"] == "partial"
    assert sqlite_reasoning.read_thinking(connection, session_id, "attempt", "response") == (
        "first middle last"
    )
    assert sqlite_reasoning.read_thinking(connection, "other", "attempt", "response") is None
    assert sqlite_reasoning.read_continuation(
        connection, session_id, "response", provider_id="other", model_id="model",
    ) is None
    assert sqlite_reasoning.read_continuation(
        connection, session_id, "response", provider_id="provider", model_id="model",
    ) == native
    sqlite_reasoning.finish_attempt(connection, session_id, "attempt", completed=False)
    connection.commit()
    connection.close()
    with sqlite3.connect(path) as reopened:
        assert sqlite_reasoning.read_continuation(
            reopened, session_id, "response", provider_id="provider", model_id="model",
        ) == native
        assert sqlite_reasoning.inspection_index(reopened, session_id)[0]["status"] == "interrupted"
        assert sqlite_reasoning.read_thinking(reopened, session_id, "attempt", "response") == (
            "first middle last"
        )


def test_replay_conflict_and_terminal_boundary_do_not_overwrite(tmp_path):
    connection = open_fixture(tmp_path / "private.db")
    record(connection)
    record(connection)
    with pytest.raises(IdempotencyConflict):
        record(connection, payload="different")
    sqlite_reasoning.finish_attempt(connection, "gm", "attempt", completed=True)
    record(connection)  # Identical acknowledgement replay remains safe after completion.
    with pytest.raises(CommandRejected):
        record(connection, source_order=1)
    assert len(sqlite_reasoning.inspection_index(connection, "gm")) == 1
    with pytest.raises(CommandRejected):
        record(connection, session_id="missing")
    connection.close()


def test_opaque_only_output_has_no_thinking_entry_and_parent_deletion_cascades(tmp_path):
    connection = open_fixture(tmp_path / "private.db")
    record(connection, kind="continuation", payload='{"opaque":"synthetic"}')
    assert sqlite_reasoning.inspection_index(connection, "gm") == []
    assert sqlite_reasoning.read_thinking(connection, "gm", "attempt", "response") is None
    connection.execute("DELETE FROM agent_sessions WHERE session_id = 'gm'")
    assert connection.execute("SELECT COUNT(*) FROM provider_private_records").fetchone()[0] == 0
    connection.close()


def test_real_owner_inspection_excludes_history_and_full_backup_retains_records(tmp_path):
    path = tmp_path / "owner.db"
    backup = tmp_path / "backup.db"
    private = ProviderPrivateRecord(
        "builder", "attempt", "response", "turn", "user", 0,
        "provider", "model", "thinking", "Synthetic sealed thought",
    )
    assert "Synthetic sealed thought" not in repr(private)
    with AuthorityOwner.open(path, "owner") as owner:
        owner.create_builder_chat(provider_id="provider", model_id="model", session_id="builder")
        owner.append_private_provider_record(private)
        owner.finish_private_provider_attempt("builder", "attempt", completed=False)
        assert "Synthetic sealed thought" not in repr(owner.session_context("builder"))
        assert "Synthetic sealed thought" not in repr(owner.session_history_page("builder"))
        assert "Synthetic sealed thought" not in repr(owner.private_provider_index("builder"))
        owner.backup_database(backup)
    for database in (path, backup):
        with AuthorityOwner.open(database, "owner") as owner:
            assert owner.inspect_provider_thinking("builder", "attempt", "response") == (
                "Synthetic sealed thought"
            )
            assert owner.private_provider_index("builder")[0]["status"] == "interrupted"


@pytest.mark.parametrize("interrupt", [False, True])
def test_exact_schema_10_upgrade_and_rollback(tmp_path, monkeypatch, interrupt):
    path = tmp_path / "predecessor.db"
    with AuthorityOwner.open(path, "owner") as owner:
        owner.create_builder_chat(provider_id="provider", model_id="model", session_id="builder")
    # This isolated fixture reconstructs the exact schema-10 predecessor, not live user data.
    with sqlite3.connect(path) as connection:
        for table in ("agent_sessions", "standalone_builder_chats"):
            connection.execute(f"ALTER TABLE {table} DROP COLUMN reasoning_effort")
            connection.execute(f"ALTER TABLE {table} DROP COLUMN reasoning_effort_once")
        connection.execute("DROP TABLE provider_private_records")
        connection.execute("DELETE FROM schema_migrations WHERE version >= 11")
        connection.execute("PRAGMA user_version = 10")
    if interrupt:
        def fail_before_commit(self):
            raise RuntimeError("Synthetic migration interruption")
        monkeypatch.setattr(
            SQLiteAuthorityStore, "_before_reasoning_migration_commit", fail_before_commit,
        )
        with pytest.raises(RuntimeError, match="stored integrity could not be verified"):
            AuthorityOwner.open(path, "owner")
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 10
            assert connection.execute(
                "SELECT 1 FROM sqlite_master WHERE name = 'provider_private_records'",
            ).fetchone() is None
    else:
        with AuthorityOwner.open(path, "owner") as owner:
            assert owner.session_context("builder").session.session_id == "builder"
            assert owner.private_provider_index("builder") == []
        with sqlite3.connect(path) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 12
