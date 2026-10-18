"""F5 backup, export, recovery, and migration smoke probes."""

from __future__ import annotations

import hashlib
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import PackageLock, WriterRole
from sylvia_gm.domain.errors import CommandRejected, SchemaIdentityError
from sylvia_gm.domain.provider_records import ProviderPrivateRecord
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.infrastructure import campaign_archive
from sylvia_gm.infrastructure.sqlite_authority import SQLiteAuthorityStore


def _campaign(owner: AuthorityOwner, *, campaign_id: str) -> str:
    owner.create_campaign(
        CampaignCreate(
            display_name=f"Synthetic {campaign_id}",
            writer=WriterRole.BUILDER,
            package_lock=PackageLock(entries=()),
            turn_id=f"turn-{campaign_id}",
            campaign_id=campaign_id,
        )
    )
    return campaign_id


def _trigger_campaign_schema(owner: AuthorityOwner, campaign_id: str) -> int:
    """Run a no-op rename to migrate the schema to v6 and return the new revision."""

    snap = owner.snapshot(campaign_id)
    owner.rename_campaign(
        operation_id=f"op-rename-schema-{campaign_id}",
        campaign_id=campaign_id,
        display_name=snap.display_name,
        expected_revision=snap.revision,
    )
    return owner.snapshot(campaign_id).revision


@pytest.mark.parametrize("include_thinking", [False, True])
def test_optional_private_export_recovers_only_selected_records(
    tmp_path: Path, include_thinking: bool,
) -> None:
    archive = tmp_path / "optional.zip"
    with AuthorityOwner.open(tmp_path / "source.db", "source") as owner:
        for campaign in ("selected", "foreign"):
            _campaign(owner, campaign_id=campaign)
            owner.complete_turn(campaign, f"turn-{campaign}")
            owner.create_agent_session(
                campaign_id=campaign, role=WriterRole.BUILDER,
                provider_id="test", model_id="test", session_id=f"session-{campaign}",
                selected_skill="BUILD CAMPAIGN SETTING",
            )
            for order, (kind, payload) in enumerate((
                ("thinking", f"Synthetic private {campaign}"),
                ("continuation", '{ "signature" : "opaque-synthetic" }'),
            )):
                owner.append_private_provider_record(ProviderPrivateRecord(
                    f"session-{campaign}", "attempt", "response", f"turn-{campaign}",
                    "input", order, "test", "test", kind, payload,
                ))
        owner.export_campaign(
            operation_id="export", campaign_id="selected", target=archive,
            include_thinking=include_thinking,
        )
    with zipfile.ZipFile(archive) as zipped:
        manifest = json.loads(zipped.read("manifest.json"))
        assert manifest["format_version"] == (2 if include_thinking else 1)
        member = "provider-private.jsonl"
        assert (member in zipped.namelist()) is include_thinking
        if include_thinking:
            assert b"Synthetic private selected" in zipped.read(member)
            assert b"Synthetic private foreign" not in zipped.read(member)
    with AuthorityOwner.open(tmp_path / "recovered.db", "recovery") as owner:
        owner.recover_campaign(recovery_id="recover", source=archive)
        assert "Synthetic private" not in repr(owner.session_context("session-selected"))
        assert owner.inspect_provider_thinking("session-selected", "attempt", "response") == (
            "Synthetic private selected" if include_thinking else None
        )
        assert owner.provider_continuation(
            "session-selected", "response", provider_id="test", model_id="test",
        ) == ({"signature": "opaque-synthetic"} if include_thinking else None)
        if include_thinking:
            assert owner.private_provider_index("session-selected")[0]["status"] == "interrupted"


def test_f5_smoke_backup_database_roundtrip(tmp_path: Path) -> None:
    """Database backup preserves all selected authority records as an independent snapshot."""

    database = tmp_path / "authority.db"
    backup = tmp_path / "authority-backup.db"
    with AuthorityOwner.open(database, "owner-backup") as owner:
        for campaign_id in ("campaign-backup", "second-campaign"):
            _campaign(owner, campaign_id=campaign_id)
            owner.complete_turn(campaign_id, f"turn-{campaign_id}")
        owner.create_builder_chat(
            provider_id="synthetic", model_id="synthetic-model", session_id="independent-builder",
        )
        owner.append_agent_message(
            session_id="independent-builder", message_id="saved-draft-response",
            kind=SessionMessageKind.TALK, content={"text": "Synthetic draft is saved."},
        )
        owner.append_private_provider_record(ProviderPrivateRecord(
            "independent-builder", "attempt", "response", "turn", "input", 0,
            "synthetic", "synthetic-model", "continuation", '{"signature":"synthetic-only"}',
        ))
        owner.append_private_provider_record(ProviderPrivateRecord(
            "independent-builder", "attempt", "response", "turn", "input", 1,
            "synthetic", "synthetic-model", "thinking", "Synthetic private note",
        ))
        owner.finish_private_provider_attempt("independent-builder", "attempt", completed=True)
        snapshots = {entry.campaign_id: owner.snapshot(entry.campaign_id)
                     for entry in owner.campaign_index()}
        saved_context = owner.session_context("independent-builder")
        saved_chats = owner.builder_chat_index()
        owner.backup_database(backup)
        owner.rename_campaign(
            operation_id="rename-after-backup", campaign_id="campaign-backup",
            display_name="Changed after backup",
            expected_revision=snapshots["campaign-backup"].revision,
        )
    with AuthorityOwner.open(backup, "owner-backup-verify") as reopened:
        assert {entry.campaign_id for entry in reopened.campaign_index()} == set(snapshots)
        assert all(reopened.snapshot(identity) == saved for identity, saved in snapshots.items())
        assert reopened.session_context("independent-builder") == saved_context
        assert reopened.builder_chat_index() == saved_chats
        assert reopened.provider_continuation(
            "independent-builder", "response", provider_id="synthetic", model_id="synthetic-model",
        ) == {"signature": "synthetic-only"}
        assert reopened.inspect_provider_thinking(
            "independent-builder", "attempt", "response",
        ) == "Synthetic private note"
        assert reopened.private_provider_index("independent-builder")[0]["status"] == "completed"


@pytest.mark.parametrize("failed_stage", ["zip_member_close", "move_file_ex"])
def test_failed_export_preserves_prior_archive_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failed_stage: str,
) -> None:
    """A partial write or failed publication cannot replace the last usable export."""
    destination = tmp_path / "campaign.sylvia-gm-campaign"
    with AuthorityOwner.open(tmp_path / "source.db", "source") as owner:
        campaign_id = _campaign(owner, campaign_id="export-failure")
        owner.complete_turn(campaign_id, f"turn-{campaign_id}")
        _trigger_campaign_schema(owner, campaign_id)
        owner.export_campaign(
            operation_id="export-before-failure", campaign_id=campaign_id, target=destination,
        )
        prior_archive = destination.read_bytes()
        prior_state = owner.snapshot(campaign_id)
        prior_files = set(tmp_path.iterdir())

        def fail_selected_stage(_store: SQLiteAuthorityStore, stage: str) -> None:
            if stage == failed_stage:
                raise OSError("Synthetic destination write failure")

        with monkeypatch.context() as patch:
            patch.setattr(SQLiteAuthorityStore, "_export_failure_stage", fail_selected_stage)
            with pytest.raises(CommandRejected) as rejected:
                owner.export_campaign(
                    operation_id="export-failed", campaign_id=campaign_id, target=destination,
                )
            assert rejected.value.code == f"portable_export_{failed_stage}_failed"

        assert destination.read_bytes() == prior_archive
        assert owner.snapshot(campaign_id) == prior_state
        assert set(tmp_path.iterdir()) == prior_files
        result = owner.export_campaign(
            operation_id="export-retry", campaign_id=campaign_id, target=destination,
        )
        assert hashlib.sha256(destination.read_bytes()).hexdigest() == result.archive_sha256

    with AuthorityOwner.open(tmp_path / "recovered.db", "recovered") as recovered:
        recovered.recover_campaign(recovery_id="recover-retry", source=destination)
        assert recovered.snapshot(campaign_id) == prior_state


def _private_archive(tmp_path: Path) -> Path:
    archive = tmp_path / "private.zip"
    with AuthorityOwner.open(tmp_path / "source.db", "source") as owner:
        _campaign(owner, campaign_id="selected")
        owner.complete_turn("selected", "turn-selected")
        owner.create_agent_session(
            campaign_id="selected", role=WriterRole.BUILDER,
            provider_id="test", model_id="test", session_id="session-selected",
            selected_skill="BUILD CAMPAIGN SETTING",
        )
        owner.append_private_provider_record(ProviderPrivateRecord(
            "session-selected", "attempt", "response", "turn-selected", "input", 0,
            "test", "test", "thinking", "Synthetic private text",
        ))
        owner.export_campaign(
            operation_id="export", campaign_id="selected", target=archive, include_thinking=True,
        )
    return archive


@pytest.mark.parametrize("mutation", ["foreign", "duplicate", "hash", "format"])
def test_private_export_tamper_rejected_before_campaign_insert(
    tmp_path: Path, mutation: str,
) -> None:
    source = _private_archive(tmp_path)
    with zipfile.ZipFile(source) as archive:
        members = [(info, archive.read(info)) for info in archive.infolist()]
    member_name = "provider-private.jsonl"
    manifest = json.loads(members[-1][1])
    rewritten = []
    for info, data in members[:-1]:
        if info.filename == member_name:
            row = json.loads(data)
            if mutation == "foreign":
                row["session_id"] = "foreign-session"
                data = (json.dumps(row) + "\n").encode()
            elif mutation == "duplicate":
                data += data
            elif mutation == "hash":
                data = data.replace(b"Synthetic", b"Tampered!")
            for declaration in manifest["non_manifest_members"]:
                if declaration["name"] == member_name:
                    declaration["byte_count"] = len(data)
                    if mutation != "hash":
                        declaration["sha256"] = hashlib.sha256(data).hexdigest()
        rewritten.append((info, data))
    if mutation == "format":
        manifest["format_version"] = 1
    tampered = tmp_path / "tampered.zip"
    with zipfile.ZipFile(tampered, "w", compression=zipfile.ZIP_STORED) as archive:
        for info, data in rewritten:
            archive.writestr(info, data)
        archive.writestr(members[-1][0], campaign_archive.manifest_bytes(manifest))
    with AuthorityOwner.open(tmp_path / "target.db", "target") as owner:
        with pytest.raises(CommandRejected) as rejected:
            owner.recover_campaign(recovery_id="recover", source=tampered)
        assert "Synthetic private text" not in str(rejected.value)
        assert "Try another export" in str(rejected.value)
        if mutation != "hash":
            assert "hash conflict" not in str(rejected.value)
        assert rejected.value.code == {
            "foreign": "portable_export_invalid_private_records",
            "duplicate": "portable_export_invalid_private_records",
            "hash": "portable_export_invalid_member_hash",
            "format": "portable_export_invalid_manifest",
        }[mutation]
        with pytest.raises(CommandRejected):
            owner.snapshot("selected")


def test_private_export_recovery_rolls_back_records_and_can_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _private_archive(tmp_path)
    target = tmp_path / "target.db"
    with AuthorityOwner.open(target, "target") as owner:
        def fail_commit(self):
            raise RuntimeError("synthetic recovery failure")
        with monkeypatch.context() as patch:
            patch.setattr(SQLiteAuthorityStore, "_before_recovery_commit", fail_commit)
            with pytest.raises(RuntimeError, match="synthetic recovery failure"):
                owner.recover_campaign(recovery_id="recover", source=source)
        with pytest.raises(CommandRejected):
            owner.snapshot("selected")
        assert owner.private_provider_index("session-selected") == []
        owner.recover_campaign(recovery_id="recover", source=source)
        assert owner.inspect_provider_thinking("session-selected", "attempt", "response") == (
            "Synthetic private text"
        )


def test_f5_smoke_export_and_recover_campaign(tmp_path: Path) -> None:
    """Export produces a portable archive; recovery restores under the original ID."""

    database = tmp_path / "authority-export.db"
    export_path = tmp_path / "exported.zip"
    with AuthorityOwner.open(database, "owner-export") as owner:
        campaign_id = _campaign(owner, campaign_id="campaign-export")
        owner.complete_turn(campaign_id, f"turn-{campaign_id}")
        # Trigger v6 schema migration via a no-op rename
        _trigger_campaign_schema(owner, campaign_id)
        export_callable = owner.export_campaign
        result = export_callable(
            operation_id="op-export-1",
            campaign_id=campaign_id,
            target=export_path,
        )
        assert result.campaign_id == campaign_id
        assert result.archive_sha256
        assert result.byte_count > 0
        assert export_path.exists()
        expected_sha = result.archive_sha256
        expected_bytes = result.byte_count
        actual_sha = hashlib.sha256(export_path.read_bytes()).hexdigest()
        assert actual_sha == expected_sha
        assert len(export_path.read_bytes()) == expected_bytes

    # Trash + purge the campaign from the original database
    with AuthorityOwner.open(database, "owner-export-purge") as owner:
        snap = owner.snapshot(campaign_id)
        owner.trash_campaign(
            operation_id="op-trash-export",
            campaign_id=campaign_id,
            expected_revision=snap.revision,
        )
        trashed_rev = owner.snapshot(campaign_id).revision
        owner.purge_campaign(
            operation_id="op-purge-export",
            campaign_id=campaign_id,
            expected_revision=trashed_rev,
            confirmation=f"permanently purge {campaign_id}",
        )
        with pytest.raises(CommandRejected):
            owner.snapshot(campaign_id)

    # Recover the exported archive into the same database under original ID
    with AuthorityOwner.open(database, "owner-export-recover") as owner:
        recovery = owner.recover_campaign(
            recovery_id="recovery-export-1",
            source=export_path,
        )
        assert recovery.campaign_id == campaign_id
        snap = owner.snapshot(campaign_id)
        assert snap.campaign_id == campaign_id


def test_f5_smoke_recovery_collision_blocks(tmp_path: Path) -> None:
    """Recovery into a database that already has the campaign ID is rejected."""

    database = tmp_path / "authority-collision.db"
    export_path = tmp_path / "exported-collision.zip"
    with AuthorityOwner.open(database, "owner-collision") as owner:
        campaign_id = _campaign(owner, campaign_id="campaign-collision")
        owner.complete_turn(campaign_id, f"turn-{campaign_id}")
        _trigger_campaign_schema(owner, campaign_id)
        export_callable = owner.export_campaign
        export_callable(
            operation_id="op-export-collision",
            campaign_id=campaign_id,
            target=export_path,
        )

    # The campaign still exists; recovery must collide
    with (
        AuthorityOwner.open(database, "owner-collision-recover") as owner,
        pytest.raises((CommandRejected, SchemaIdentityError, ValueError)),
    ):
        owner.recover_campaign(
            recovery_id="recovery-collision-1",
            source=export_path,
        )


def test_f5_smoke_migration_from_f3(tmp_path: Path) -> None:
    """An F3-era database (user_version=3) migrates to F5+ when lifecycle is used."""

    from sylvia_gm.infrastructure.sqlite_authority import (
        _MIGRATION_5_NAME,
        _MIGRATION_5_SHA256,
        _MIGRATION_6_NAME,
        _MIGRATION_6_SHA256,
        _MIGRATION_7_NAME,
        _MIGRATION_7_SHA256,
        _MIGRATION_8_NAME,
        _MIGRATION_8_SHA256,
        _MIGRATION_9_NAME,
        _MIGRATION_9_SHA256,
        _MIGRATION_10_NAME,
        _MIGRATION_10_SHA256,
    )

    database = tmp_path / "authority-migrate.db"
    # Create a campaign under an owner (starts at v3)
    with AuthorityOwner.open(database, "owner-migrate") as owner:
        _campaign(owner, campaign_id="campaign-migrate")
        # v3 schema — no migrations 4/5/6 yet
        connection = sqlite3.connect(database)
        try:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        finally:
            connection.close()

    # Reopen and trigger lifecycle — this migrates v3→v6
    with AuthorityOwner.open(database, "owner-migrate-reopen") as owner:
        # complete_turn triggers v3→v4 (session schema)
        owner.complete_turn("campaign-migrate", "turn-campaign-migrate")
        # rename triggers v4→v6 (campaign schema)
        revision = _trigger_campaign_schema(owner, "campaign-migrate")
        assert revision >= 1

    connection = sqlite3.connect(database)
    try:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        migrations = tuple(
            connection.execute(
                "SELECT version, applied_name, sql_sha256 FROM schema_migrations ORDER BY version"
            )
        )
    finally:
        connection.close()
        assert version == 12
    assert (5, _MIGRATION_5_NAME, _MIGRATION_5_SHA256) in migrations
    assert (6, _MIGRATION_6_NAME, _MIGRATION_6_SHA256) in migrations
    assert (7, _MIGRATION_7_NAME, _MIGRATION_7_SHA256) in migrations
    assert (8, _MIGRATION_8_NAME, _MIGRATION_8_SHA256) in migrations
    assert (9, _MIGRATION_9_NAME, _MIGRATION_9_SHA256) in migrations
    assert (10, _MIGRATION_10_NAME, _MIGRATION_10_SHA256) in migrations

    # Reopen and verify campaign survives migration
    with AuthorityOwner.open(database, "owner-migrate-reopen2") as owner:
        snap = owner.snapshot("campaign-migrate")
        assert snap.campaign_id == "campaign-migrate"


def test_mechanics_choice_migration_remains_in_the_caller_transaction() -> None:
    """Schema 7 cannot commit independently of its authority/provenance transaction."""

    from sylvia_gm.infrastructure.sqlite_mechanics_choice import apply_migration

    connection = sqlite3.connect(":memory:", isolation_level=None)
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        apply_migration(connection)
        assert connection.in_transaction
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'pending_mechanics_actions'"
        ).fetchone() == (1,)
        connection.rollback()
        assert (
            connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'pending_mechanics_actions'"
            ).fetchone()
            is None
        )
    finally:
        connection.close()


def test_campaign_continuity_migration_materializes_legacy_session_evidence() -> None:
    """Schema 9 turns legacy DM evidence into current Campaign State atomically."""

    from sylvia_gm.infrastructure.sqlite_continuity import apply_migration, read_view

    connection = sqlite3.connect(":memory:", isolation_level=None)
    connection.row_factory = sqlite3.Row
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.executescript(
            """
            CREATE TABLE campaigns (
                campaign_id TEXT PRIMARY KEY,
                revision INTEGER NOT NULL
            );
            CREATE TABLE campaign_operations (
                operation_id TEXT PRIMARY KEY
            );
            CREATE TABLE campaign_revisions (
                campaign_id TEXT NOT NULL,
                revision INTEGER NOT NULL
            );
            CREATE TABLE campaign_plans (
                campaign_id TEXT NOT NULL,
                plan_revision INTEGER NOT NULL,
                acceptance_status TEXT NOT NULL,
                content_json TEXT NOT NULL,
                resulting_campaign_revision INTEGER NOT NULL
            );
            CREATE TABLE agent_sessions (
                session_id TEXT PRIMARY KEY,
                campaign_id TEXT,
                role TEXT NOT NULL
            );
            CREATE TABLE agent_session_messages (
                session_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                kind TEXT NOT NULL,
                content_json TEXT NOT NULL
            );
            """
        )
        connection.execute("INSERT INTO campaigns VALUES ('campaign-legacy', 7)")
        connection.execute(
            "INSERT INTO campaign_plans VALUES (?, ?, ?, ?, ?)",
            (
                "campaign-legacy",
                1,
                "accepted",
                json.dumps(
                    {
                        "campaign_plan": {
                            "starting_location": {"id": "small-town"},
                            "initial_state": {
                                "facts": [
                                    {
                                        "id": "weather",
                                        "fact": "Rain has closed the north road.",
                                        "known_by": ["player"],
                                    }
                                ]
                            },
                        }
                    }
                ),
                2,
            ),
        )
        connection.execute(
            "INSERT INTO agent_sessions VALUES ('dm-legacy', 'campaign-legacy', 'dm')"
        )
        evidence = (
            (
                "scene-1",
                {
                    "kind": "campaign_scene",
                    "act_id": "act-2",
                    "location_id": "capital-hospital",
                    "summary": "Alrya is confined to the capital hospital.",
                    "basis_message_ids": ["talk-1"],
                },
            ),
            (
                "memory-1",
                {
                    "kind": "campaign_memory",
                    "fact_id": "alrya-location",
                    "category": "location",
                    "text": "Alrya is confined to the capital hospital.",
                    "visibility": "gm_private",
                    "known_by": ["alrya"],
                    "basis_message_ids": ["talk-1"],
                },
            ),
            (
                "reveal-1",
                {
                    "kind": "campaign_secret_reveal",
                    "secret_id": "rift-is-one-way",
                    "audience": ["player", "alrya"],
                    "basis_message_ids": ["talk-2"],
                },
            ),
        )
        for message_id, content in evidence:
            connection.execute(
                "INSERT INTO agent_session_messages VALUES (?, ?, 'evidence', ?)",
                ("dm-legacy", message_id, json.dumps(content)),
            )

        connection.execute("BEGIN IMMEDIATE")
        apply_migration(connection)
        assert connection.in_transaction
        view = read_view(connection, "campaign-legacy")
        assert view.current_scene is not None
        assert view.current_scene["location_id"] == "capital-hospital"
        assert {fact["record_id"] for fact in view.facts} == {
            "alrya-location",
            "weather",
        }
        assert tuple(view.reveals["rift-is-one-way"]) == ("alrya", "player")
        assert [record.entry_id for record in view.records] == [
            record.entry_id for record in read_view(connection, "campaign-legacy").records
        ]
        connection.rollback()
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'campaign_continuity_records'"
        ).fetchone() is None
    finally:
        connection.close()


def test_material_migration_preserves_reopenable_predecessor_source(tmp_path: Path) -> None:
    """A material v3→v6 upgrade preserves one ordinary, reopenable source copy."""

    database = tmp_path / "authority.db"
    preserved = tmp_path / "migration-sources" / "authority.pre-migration-v3.sqlite3"
    with AuthorityOwner.open(database, "owner-preserve-source") as owner:
        _campaign(owner, campaign_id="campaign-preserve-source")

    with AuthorityOwner.open(database, "owner-upgrade-source") as owner:
        _trigger_campaign_schema(owner, "campaign-preserve-source")

    assert preserved.is_file()
    with sqlite3.connect(preserved) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
        assert connection.execute(
            "SELECT display_name FROM campaigns WHERE campaign_id = ?",
            ("campaign-preserve-source",),
        ).fetchone() == ("Synthetic campaign-preserve-source",)
        assert tuple(
            connection.execute("SELECT version FROM schema_migrations ORDER BY version")
        ) == ((1,), (2,), (3,))

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 12

    with AuthorityOwner.open(preserved, "owner-reopen-preserved") as owner:
        assert owner.snapshot("campaign-preserve-source").display_name == (
            "Synthetic campaign-preserve-source"
        )
    print(
        "F5 PRE-MIGRATION SOURCE",
        "live=8",
        "preserved=3",
        "campaign=campaign-preserve-source",
    )


def test_session_first_migration_preserves_reopenable_predecessor_source(tmp_path: Path) -> None:
    """The first v3→v4 session use preserves the same ordinary source guarantee."""

    database = tmp_path / "authority.db"
    preserved = tmp_path / "migration-sources" / "authority.pre-migration-v3.sqlite3"
    with AuthorityOwner.open(database, "owner-session-source") as owner:
        _campaign(owner, campaign_id="campaign-session-source")

    with AuthorityOwner.open(database, "owner-session-upgrade") as owner:
        owner.create_agent_session(
            campaign_id="campaign-session-source",
            role=WriterRole.BUILDER,
            provider_id="provider.synthetic",
            model_id="model.synthetic",
            session_id="session-source",
            selected_skill="BUILD CAMPAIGN PLAN",
        )

    assert preserved.is_file()
    with sqlite3.connect(preserved) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 4
    with AuthorityOwner.open(preserved, "owner-reopen-session-source") as owner:
        assert owner.snapshot("campaign-session-source").campaign_id == ("campaign-session-source")
    print("F5 SESSION MIGRATION SOURCE", "live=4", "preserved=3")


def test_startup_v5_to_v6_preserves_reopenable_predecessor_source(tmp_path: Path) -> None:
    """Owner startup preserves an exact v5 source before its automatic v6 upgrade."""

    from sylvia_gm.infrastructure import sqlite_campaign
    from sylvia_gm.infrastructure.sqlite_authority import (
        _CAMPAIGN_IDENTITY_DELTA_SHA256,
        _CAMPAIGN_IDENTITY_EXTENSION_NAME,
        _LIFECYCLE_EXTENSION_NAME,
        _LIFECYCLE_EXTENSION_SHA256,
        _MIGRATION_4,
        _MIGRATION_4_NAME,
        _MIGRATION_4_SHA256,
        _MIGRATION_5,
        _MIGRATION_5_NAME,
        _MIGRATION_5_SHA256,
        SQLiteAuthorityStore,
    )

    database = tmp_path / "authority.db"
    preserved = tmp_path / "migration-sources" / "authority.pre-migration-v5.sqlite3"
    with AuthorityOwner.open(database, "owner-v5-source") as owner:
        _campaign(owner, campaign_id="campaign-v5-source")

    with sqlite3.connect(database) as connection:
        connection.executescript(_MIGRATION_4)
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_name, sql_sha256) VALUES (?, ?, ?)",
            (4, _MIGRATION_4_NAME, _MIGRATION_4_SHA256),
        )
        connection.executescript(_MIGRATION_5)
        sqlite_campaign.migrate_v4_participants(connection)
        SQLiteAuthorityStore._insert_campaign_identity_extension(connection)
        sqlite_campaign.apply_lifecycle_extension(connection)
        connection.execute(
            "INSERT INTO authority_schema_extensions "
            "(extension_name, extension_sha256, predecessor_migration_name, "
            "predecessor_sql_sha256) VALUES (?, ?, ?, ?)",
            (
                _LIFECYCLE_EXTENSION_NAME,
                _LIFECYCLE_EXTENSION_SHA256,
                _CAMPAIGN_IDENTITY_EXTENSION_NAME,
                _CAMPAIGN_IDENTITY_DELTA_SHA256,
            ),
        )
        connection.execute(
            "INSERT INTO schema_migrations(version, applied_name, sql_sha256) VALUES (?, ?, ?)",
            (5, _MIGRATION_5_NAME, _MIGRATION_5_SHA256),
        )
        connection.execute("PRAGMA user_version = 5")
        connection.commit()

    with AuthorityOwner.open(database, "owner-v5-upgrade") as owner:
        assert owner.snapshot("campaign-v5-source").campaign_id == "campaign-v5-source"

    assert preserved.is_file()
    with sqlite3.connect(preserved) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 5
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 12
    print("F5 STARTUP MIGRATION SOURCE", "live=8", "preserved=5")


def test_portable_recovery_binds_path_free_migration_provenance(tmp_path: Path) -> None:
    """Exported migration lineage survives recovery without source-machine details."""

    source_database = tmp_path / "source.sqlite3"
    target_database = tmp_path / "target.sqlite3"
    archive_path = tmp_path / "campaign.sylvia-gm-campaign"
    campaign_id = "campaign-provenance"
    with AuthorityOwner.open(source_database, "owner-provenance-source") as owner:
        _campaign(owner, campaign_id=campaign_id)
        owner.complete_turn(campaign_id, f"turn-{campaign_id}")
        _trigger_campaign_schema(owner, campaign_id)
        owner.export_campaign(
            operation_id="export-provenance",
            campaign_id=campaign_id,
            target=archive_path,
        )

    with zipfile.ZipFile(archive_path) as archive:
        manifest = json.loads(archive.read("manifest.json"))
    provenance = manifest["migration_provenance"]
    assert [item["version"] for item in provenance["schema_migrations"]] == [
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        11,
        12,
    ]
    assert [item["extension_name"] for item in provenance["schema_extensions"]] == sorted(
        [
            "f5_portable_campaign_recovery_v1",
            "f5_probe_01_stable_campaign_identity",
            "f5_reversible_lifecycle_v3",
        ]
    )
    serialized = json.dumps(provenance, sort_keys=True)
    assert str(tmp_path) not in serialized
    assert "source.sqlite3" not in serialized

    with AuthorityOwner.open(target_database, "owner-provenance-target") as owner:
        owner.recover_campaign(recovery_id="recover-provenance", source=archive_path)

    with sqlite3.connect(target_database) as connection:
        payload = json.loads(
            connection.execute(
                "SELECT semantic_payload_json FROM campaign_recovery_receipts "
                "WHERE recovery_id = ?",
                ("recover-provenance",),
            ).fetchone()[0]
        )
    assert payload["migration_provenance"] == provenance
    print(
        "F5 MIGRATION PROVENANCE",
        "migrations=8",
        "extensions=3",
        "path_free=true",
        "recovery_bound=true",
    )


def test_portable_export_rejects_re_signed_migration_provenance(tmp_path: Path) -> None:
    """A canonical archive cannot substitute valid-looking migration hashes."""

    database = tmp_path / "authority.sqlite3"
    archive_path = tmp_path / "valid.sylvia-gm-campaign"
    tampered_path = tmp_path / "tampered.sylvia-gm-campaign"
    campaign_id = "campaign-provenance-tamper"
    with AuthorityOwner.open(database, "owner-provenance-tamper") as owner:
        _campaign(owner, campaign_id=campaign_id)
        owner.complete_turn(campaign_id, f"turn-{campaign_id}")
        _trigger_campaign_schema(owner, campaign_id)
        owner.export_campaign(
            operation_id="export-provenance-tamper",
            campaign_id=campaign_id,
            target=archive_path,
        )

    with zipfile.ZipFile(archive_path) as archive:
        members = [(info.filename, archive.read(info)) for info in archive.infolist()]
    manifest = json.loads(dict(members)["manifest.json"])
    manifest["migration_provenance"]["schema_migrations"][-1]["sql_sha256"] = "0" * 64
    with zipfile.ZipFile(
        tampered_path,
        "w",
        compression=zipfile.ZIP_STORED,
        allowZip64=True,
    ) as archive:
        for name, body in members:
            if name == "manifest.json":
                body = campaign_archive.manifest_bytes(manifest)
            archive.writestr(campaign_archive.fixed_zip_info(name), body)

    with pytest.raises(CommandRejected) as rejected:
        campaign_archive.validate_campaign_archive(
            tampered_path,
            expected_campaign_id=campaign_id,
        )
    assert rejected.value.code == "portable_export_invalid_migration_provenance"
    print("F5 MIGRATION PROVENANCE TAMPER", "rejected=true")


@pytest.mark.parametrize("matching_migration_history", [True, False])
@pytest.mark.parametrize("source_version", [9, 10, 11])
def test_prior_portable_archive_preserves_its_source_version_on_recovery(
    tmp_path: Path, matching_migration_history: bool, source_version: int,
) -> None:
    """Recover the prior portable format, rejecting a version with mismatched lineage."""

    source = tmp_path / "source.db"
    target = tmp_path / "target.db"
    current_archive = tmp_path / "current.zip"
    prior_archive = tmp_path / "prior.zip"
    campaign_id = "v9-portable"
    with AuthorityOwner.open(source, "source") as owner:
        _campaign(owner, campaign_id=campaign_id)
        owner.complete_turn(campaign_id, f"turn-{campaign_id}")
        owner.create_agent_session(
            campaign_id=campaign_id, role=WriterRole.BUILDER,
            provider_id="test", model_id="test", session_id="prior-session",
            selected_skill="BUILD CAMPAIGN SETTING",
        )
        _trigger_campaign_schema(owner, campaign_id)
        owner.export_campaign(
            operation_id="export-prior", campaign_id=campaign_id, target=current_archive,
        )
        original = owner.snapshot(campaign_id)

    # Reconstruct a pre-effort archive with the original session shape and manifest lineage.
    with zipfile.ZipFile(current_archive) as archive:
        members = [(info.filename, archive.read(info)) for info in archive.infolist()]
    manifest = json.loads(dict(members)["manifest.json"])
    prior_rows = []
    for line in dict(members)["authority.jsonl"].splitlines():
        record = json.loads(line)
        if record["table"] == "agent_sessions":
            del record["row"]["reasoning_effort"]
            del record["row"]["reasoning_effort_once"]
        prior_rows.append(
            campaign_archive.canonical_authority_record(record["table"], record["row"])
        )
    authority_bytes = b"".join(prior_rows)
    manifest["non_manifest_members"][0]["byte_count"] = len(authority_bytes)
    manifest["non_manifest_members"][0]["sha256"] = hashlib.sha256(authority_bytes).hexdigest()
    manifest["source_authority_schema_version"] = source_version
    manifest["source_authority_schema_identity_sha256"] = (
        campaign_archive.source_authority_schema_claim_sha256(source_version)
    )
    if matching_migration_history:
        manifest["migration_provenance"]["schema_migrations"] = [
            item for item in manifest["migration_provenance"]["schema_migrations"]
            if item["version"] <= source_version
        ]
    with zipfile.ZipFile(prior_archive, "w", compression=zipfile.ZIP_STORED) as archive:
        for name, body in members:
            if name == "authority.jsonl":
                body = authority_bytes
            if name == "manifest.json":
                body = campaign_archive.manifest_bytes(manifest)
            archive.writestr(campaign_archive.fixed_zip_info(name), body)

    with AuthorityOwner.open(target, "recovery") as owner:
        if not matching_migration_history:
            with pytest.raises(CommandRejected) as rejected:
                owner.recover_campaign(recovery_id="recover-prior", source=prior_archive)
            assert "migration_provenance" in rejected.value.code
            return
        owner.recover_campaign(recovery_id="recover-prior", source=prior_archive)
        assert owner.snapshot(campaign_id) == original
        assert owner.session_context("prior-session").session.reasoning_effort is None
        assert owner.session_context("prior-session").session.reasoning_effort_once is None
    with AuthorityOwner.open(target, "reopen") as owner:
        assert owner.snapshot(campaign_id) == original
    with sqlite3.connect(target) as connection:
        payload = json.loads(connection.execute(
            "SELECT semantic_payload_json FROM campaign_recovery_receipts "
            "WHERE recovery_id = 'recover-prior'",
        ).fetchone()[0])
    assert payload["source_authority_schema_version"] == source_version
    assert payload["migration_provenance"] == manifest["migration_provenance"]
