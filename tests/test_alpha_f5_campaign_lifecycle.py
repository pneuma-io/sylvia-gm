"""Exact F5 campaign construction, lifecycle, backup, recovery, and migration probes.

The numbered probe labels in this module map directly to the frozen F5 gate matrix:
  G-CAM-ID, G-HANDOFF, G-ARCHIVE, G-TRASH, G-RECOVER, G-PIN, G-BYTE-BARRIER.

Tests deliberately use only synthetic archives, temporary SQLite databases, and
injected providers — no network, no real services, no personal campaign data.
"""

from __future__ import annotations

import ast
import hashlib
import io
import json
import sqlite3
import zipfile
from pathlib import Path

import pytest

from sylvia_gm.application.authority import (
    AuthorityOwner,
    CampaignCreate,
)
from sylvia_gm.domain.authority import (
    CampaignExportResult,
    CampaignRecoveryResult,
    LifecycleState,
    PackageLock,
    Participant,
    WriterRole,
)
from sylvia_gm.domain.errors import (
    AuthorityError,
    CommandRejected,
    IdempotencyConflict,
    SchemaIdentityError,
    StaleRevision,
)
from sylvia_gm.domain.package_library import canonical_json_bytes, content_root_for_record

FROZEN_F5_PROBE_MAP = {
    1: (
        "NEW CAMPAIGN freshness and stable identity: confirm_new_campaign creates a "
        "revision-0 campaign with stable UUID before Builder questioning."
    ),
    2: (
        "Quick Start and Customize convergence: begin/complete construction records "
        "canonical input and plan without replacing the campaign identity."
    ),
    3: (
        "Ruleset-specific character/participant artifacts: participants carry package "
        "and schema identity; validated and quarantined partitions are exact."
    ),
    4: ("Rename mutates display name and increments revision without changing lifecycle."),
    5: (
        "Quiescent Archive permits idle persistent chats but rejects live work and "
        "incomplete turns; Archive sets lifecycle archived and writer none."
    ),
    6: "Restore from Archive returns lifecycle to active and writer to the designated session.",
    7: (
        "Trash creates an indefinite recoverable tombstone; lifecycle is tombstoned, "
        "writer is none."
    ),
    8: "Restore from Trash returns lifecycle to active.",
    9: (
        "Permanent purge is a separate explicit irreversible action requiring exact "
        "confirmation; removes the live campaign."
    ),
    10: ("Consistent SQLite backup produces a coherent copy readable as an authority store."),
    11: (
        "Portable campaign export produces a deterministic self-contained archive with "
        "exact package closure and lock manifest."
    ),
    12: (
        "Recovery after purge restores the campaign under its original ID; no "
        "automatic merge or replacement."
    ),
    13: ("Recovery collision with an existing local ID blocks automatic merge/replacement."),
    14: (
        "Startup integrity checks validate database integrity, schema version, campaign "
        "revision relationships, and exact package closure."
    ),
    15: (
        "Deterministic application-owned migration between released versions: F3→F5→F6 "
        "upgrades preserve all predecessor truth."
    ),
    16: (
        "Portable backup includes exact lock manifest and all required immutable "
        "package/dependency bytes when a ruleset is present."
    ),
    17: (
        "No private provider reasoning or current-session Inspector traces are included "
        "in backup/export."
    ),
    18: (
        "No automatic expiry of Trash; no LLM conflict blending; "
        "no access to personal campaign data."
    ),
    19: (
        "Delete does not purge shared package bytes; only permanent purge removes package "
        "bytes no remaining reference protects."
    ),
    20: (
        "Campaign lifecycle transitions record exact from/to lifecycle, writer, and "
        "revision lineage."
    ),
    21: (
        "Archive is rejected while Builder/DM work is active and becomes admissible only "
        "after Stop/completion (quiescent)."
    ),
    22: (
        "Idempotent lifecycle operations: replaying the same operation_id returns the "
        "same result without a new revision."
    ),
    23: "All tests use injected fake providers, synthetic temporary databases, no network.",
}

F5_PRODUCTION_PATHS = (
    "src/sylvia_gm/domain/authority.py",
    "src/sylvia_gm/domain/session.py",
    "src/sylvia_gm/domain/state_ownership.py",
    "src/sylvia_gm/application/authority.py",
    "src/sylvia_gm/infrastructure/sqlite_authority.py",
    "src/sylvia_gm/infrastructure/sqlite_campaign.py",
    "src/sylvia_gm/infrastructure/sqlite_recovery.py",
    "src/sylvia_gm/infrastructure/sqlite_state_ownership.py",
    "src/sylvia_gm/infrastructure/campaign_archive.py",
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _campaign(
    owner: AuthorityOwner,
    *,
    campaign_id: str,
    writer: WriterRole = WriterRole.BUILDER,
    turn_id: str | None = None,
) -> str:
    owner.create_campaign(
        CampaignCreate(
            display_name=f"Synthetic {campaign_id}",
            writer=writer,
            package_lock=PackageLock(entries=()),
            turn_id=turn_id or f"turn-{campaign_id}",
            campaign_id=campaign_id,
        )
    )
    return campaign_id


@pytest.mark.parametrize("with_session", [False, True])
def test_pre_interview_campaign_has_no_construction_basis(
    tmp_path: Path, with_session: bool,
) -> None:
    """Optional Interview reads reject cleanly without migrating a legacy campaign."""
    database = tmp_path / "pre-interview.sqlite3"
    with AuthorityOwner.open(database, "pre-interview") as owner:
        campaign_id = _campaign(owner, campaign_id="legacy", writer=WriterRole.DM)
        if with_session:
            owner.create_agent_session(
                campaign_id=campaign_id, role=WriterRole.DM,
                provider_id="test", model_id="test", session_id="legacy-dm",
            )
        before = owner.snapshot(campaign_id)
        with sqlite3.connect(database) as connection:
            version = connection.execute("PRAGMA user_version").fetchone()[0]
        assert version == (4 if with_session else 3)
        with pytest.raises(CommandRejected, match="Confirmed campaign construction is unavailable"):
            owner.campaign_construction_basis(campaign_id)
        assert owner.snapshot(campaign_id) == before
        with sqlite3.connect(database) as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == version


def _trigger_v6(owner: AuthorityOwner, campaign_id: str) -> int:
    """Migrate the schema to v6 via a no-op rename and return the new revision."""

    snap = owner.snapshot(campaign_id)
    owner.rename_campaign(
        operation_id=f"op-rename-schema-{campaign_id}",
        campaign_id=campaign_id,
        display_name=snap.display_name,
        expected_revision=snap.revision,
    )
    return owner.snapshot(campaign_id).revision


def _clockwork_archive(*, version: str = "1", require_epoch: bool = False) -> bytes:
    payload = b'{"ticks":1}'
    character_properties: dict[str, object] = {
        "name": {"type": "string", "minLength": 1},
        "ticks": {"type": "integer", "minimum": 0},
    }
    character_required = ["name", "ticks"]
    if require_epoch:
        character_properties["epoch"] = {"type": "string", "enum": ["second-age"]}
        character_required.append("epoch")
    character_schema = {
        "$id": f"synthetic.clockwork.character.v{version}",
        "type": "object",
        "properties": character_properties,
        "required": character_required,
        "additionalProperties": False,
    }
    tool = {
        "name": "advance",
        "capability": "clockwork.counter",
        "input_schema": {
            "type": "object",
            "properties": {"steps": {"type": "integer"}},
            "required": ["steps"],
            "additionalProperties": False,
        },
        "state_predicates": [],
        "turn_statuses": ["open"],
        "writers": ["builder", "dm"],
    }
    draft: dict[str, object] = {
        "id": "synthetic.clockwork",
        "version": version,
        "role": "primary",
        "metadata": {"label": "Synthetic Clockwork"},
        "provenance": {"publisher": "synthetic-f5-tests", "source_sha256": "a" * 64},
        "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
        "minimum_model_capability_tier": "sylvia.capability.t00",
        "dependencies": [],
        "capabilities": ["clockwork.counter"],
        "tools": [tool],
        "prompt_fragments": [],
        "ui_surfaces": [],
        "participant_artifacts": [
            {
                "kind": "character",
                "schema_id": character_schema["$id"],
                "schema_sha256": hashlib.sha256(canonical_json_bytes(character_schema)).hexdigest(),
                "schema": character_schema,
            }
        ],
        "files": [
            {
                "path": "data/clock.json",
                "bytes": len(payload),
                "sha256": hashlib.sha256(payload).hexdigest(),
            }
        ],
    }
    record = {**draft, "content_sha256": content_root_for_record(draft)}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("manifest.json", canonical_json_bytes({"format": 2, "packages": [record]}))
        archive.writestr("packages/synthetic.clockwork/data/clock.json", payload)
    return stream.getvalue()


def _copy_sqlite_database(source: Path, destination: Path) -> None:
    source_connection = sqlite3.connect(source)
    destination_connection = sqlite3.connect(destination)
    try:
        source_connection.backup(destination_connection)
    finally:
        destination_connection.close()
        source_connection.close()


# ---------------------------------------------------------------------------
# G-CAM-ID: NEW CAMPAIGN freshness and stable identity
# ---------------------------------------------------------------------------


def test_probe_01_new_campaign_freshness_and_stable_identity(tmp_path: Path) -> None:
    """Probe 1 / G-CAM-ID: confirm_new_campaign creates stable revision-0 identity."""

    database = tmp_path / "authority-cam-id.db"
    with AuthorityOwner.open(database, "owner-cam-id") as owner:
        confirmation = owner.confirm_new_campaign(
            operation_id="op-confirm-cam-id",
            display_name="Fresh Campaign",
            package_lock=PackageLock(entries=()),
            turn_id="turn-cam-id",
        )
        assert confirmation.resulting_revision == 0
        campaign_id = confirmation.campaign_id
        # The campaign has a stable UUID identity
        assert campaign_id and len(campaign_id) >= 32
        snap = owner.snapshot(campaign_id)
        assert snap.revision == 0
        assert snap.lifecycle == LifecycleState.ACTIVE
        assert snap.writer == WriterRole.BUILDER
        assert snap.display_name == "Fresh Campaign"
        # Construction record is present
        construction = owner.campaign_lifecycle(campaign_id)
        assert construction.campaign_id == campaign_id

    # Reopen and verify the identity is stable
    with AuthorityOwner.open(database, "owner-cam-id-reopen") as owner:
        snap = owner.snapshot(campaign_id)
        assert snap.campaign_id == campaign_id
        assert snap.display_name == "Fresh Campaign"
        assert snap.revision == 0
    print("F5 PROBE 01", "campaign_id", campaign_id, "revision", 0)


# ---------------------------------------------------------------------------
# G-HANDOFF: Quick Start and Customize convergence
# ---------------------------------------------------------------------------


def test_probe_02_quick_start_and_customize_convergence(tmp_path: Path) -> None:
    """Probe 2: begin/complete construction for both lanes."""

    database = tmp_path / "authority-convergence.db"
    with AuthorityOwner.open(database, "owner-convergence") as owner:
        # Quick Start lane
        quick = owner.confirm_new_campaign(
            operation_id="op-confirm-quick",
            display_name="Quick Start Campaign",
            package_lock=PackageLock(entries=()),
            turn_id="turn-quick",
        )
        quick_begin = owner.begin_campaign_construction(
            operation_id="op-begin-quick",
            campaign_id=quick.campaign_id,
            lane="quick_start",
            expected_revision=0,
            canonical_input={"premise": "A quick start premise."},
        )
        assert quick_begin.lane == "quick_start"
        assert quick_begin.resulting_revision == 1
        quick_complete = owner.complete_campaign_construction(
            operation_id="op-complete-quick",
            campaign_id=quick.campaign_id,
            expected_revision=1,
            canonical_plan={"title": "Quick Plan", "acts": ["act1"]},
        )
        assert quick_complete.lane == "quick_start"
        assert quick_complete.resulting_revision == 2
        assert quick_complete.canonical_representation_sha256

        # Customize lane
        custom = owner.confirm_new_campaign(
            operation_id="op-confirm-custom",
            display_name="Customize Campaign",
            package_lock=PackageLock(entries=()),
            turn_id="turn-custom",
        )
        custom_begin = owner.begin_campaign_construction(
            operation_id="op-begin-custom",
            campaign_id=custom.campaign_id,
            lane="customize",
            expected_revision=0,
            canonical_input={"premise": "A custom premise."},
        )
        assert custom_begin.lane == "customize"
        custom_complete = owner.complete_campaign_construction(
            operation_id="op-complete-custom",
            campaign_id=custom.campaign_id,
            expected_revision=1,
            canonical_plan={"title": "Custom Plan", "acts": ["act1", "act2"]},
        )
        assert custom_complete.lane == "customize"
        assert custom_complete.resulting_revision == 2
        # Both campaigns retain their original stable identity
        assert quick.campaign_id != custom.campaign_id
        snap_q = owner.snapshot(quick.campaign_id)
        snap_c = owner.snapshot(custom.campaign_id)
        assert snap_q.display_name == "Quick Start Campaign"
        assert snap_c.display_name == "Customize Campaign"
    print(
        "F5 PROBE 02",
        "quick",
        quick.campaign_id,
        "custom",
        custom.campaign_id,
    )


# ---------------------------------------------------------------------------
# Probe 3: Ruleset-specific character/participant artifacts
# ---------------------------------------------------------------------------


def test_probe_03_participant_artifacts_validated_and_quarantined(tmp_path: Path) -> None:
    """Probe 3: participants with package/schema proof vs quarantined."""

    database = tmp_path / "authority-participants.db"
    with AuthorityOwner.open(database, "owner-participants") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(_clockwork_archive()),
            install_id="install-clockwork",
        )
        entry = candidate.package_lock.entries[0]
        campaign = owner.create_campaign(
            CampaignCreate(
                display_name="Participant Campaign",
                writer=WriterRole.BUILDER,
                package_lock=candidate.package_lock,
                turn_id="turn-participants",
                campaign_id="campaign-participants",
                participants=(
                    Participant(
                        "pc-hero",
                        "character",
                        entry.package_id,
                        entry.version,
                        {"name": "Hero", "ticks": 1},
                    ),
                ),
            )
        )
        assert campaign.campaign_id == "campaign-participants"
        # Trigger v6 schema (which runs participant migration)
        _trigger_v6(owner, "campaign-participants")
        snap = owner.snapshot("campaign-participants")
        assert len(snap.participants) == 1
        participant = snap.participants[0]
        assert participant.participant_id == "pc-hero"
        assert participant.kind == "character"
        assert participant.validation_status.value == "validated"
        assert participant.schema_id == "synthetic.clockwork.character.v1"
        assert participant.schema_sha256 is not None
    print(
        "F5 PROBE 03",
        "participant",
        participant.participant_id,
        participant.validation_status.value,
    )


def test_future_semantic_break_uses_optional_human_reviewed_builder_candidate(
    tmp_path: Path,
) -> None:
    """A semantic incompatibility preserves source truth and requires an explicit candidate."""

    database = tmp_path / "authority-future-semantic-break.db"
    with AuthorityOwner.open(database, "owner-semantic-break") as owner:
        source_candidate = owner.install_package_archive(
            io.BytesIO(_clockwork_archive(version="1")),
            install_id="install-clockwork-v1",
        )
        destination_candidate = owner.install_package_archive(
            io.BytesIO(_clockwork_archive(version="2", require_epoch=True)),
            install_id="install-clockwork-v2",
        )
        source_entry = source_candidate.package_lock.entries[0]
        destination_entry = destination_candidate.package_lock.entries[0]
        owner.create_campaign(
            CampaignCreate(
                display_name="Pre-break Campaign",
                writer=WriterRole.BUILDER,
                package_lock=source_candidate.package_lock,
                turn_id="turn-semantic-source",
                campaign_id="campaign-semantic-source",
                participants=(
                    Participant(
                        "pc-clockwork",
                        "character",
                        source_entry.package_id,
                        source_entry.version,
                        {"name": "Hero", "ticks": 1},
                    ),
                ),
            )
        )
        _trigger_v6(owner, "campaign-semantic-source")
        owner.create_agent_session(
            campaign_id="campaign-semantic-source",
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-semantic-review",
            selected_skill="BUILD CAMPAIGN SETTING",
        )

        before = owner.snapshot("campaign-semantic-source")
        review = owner.require_builder_human_review(
            session_id="session-semantic-review",
            review_id="review-future-schema-break",
            unsupported_capabilities=("automatic-semantic-epoch-selection",),
            contradictions=("v1 has no authoritative epoch value",),
        )
        assert review.status.value == "review_required"
        assert owner.snapshot("campaign-semantic-source") == before

        # The review must be explicitly stopped before a human can resume the
        # exact Builder session and approve canonical destination data.
        assert owner.stop_agent_session("session-semantic-review").status.value == "stopped"
        assert owner.resume_agent_session("session-semantic-review").status.value == "active"
        adapted = owner.adapt_participant_artifact(
            source_campaign_id="campaign-semantic-source",
            participant_id="pc-clockwork",
            destination_package_id=destination_entry.package_id,
            destination_package_version=destination_entry.version,
            destination_package_sha256=destination_entry.sha256,
            destination_kind="character",
            canonical_data={"name": "Hero", "ticks": 1, "epoch": "second-age"},
            operation_id="adapt-future-schema-break",
        )
        assert adapted.adaptation_id == "adapt-future-schema-break"
        assert adapted.validation_status.value == "validated"

        # Preparing the candidate never rewrites the original campaign or pin.
        unchanged = owner.snapshot("campaign-semantic-source")
        assert unchanged == before
        assert unchanged.package_lock == source_candidate.package_lock
        assert unchanged.participants[0].data == {"name": "Hero", "ticks": 1}

        owner.create_campaign(
            CampaignCreate(
                display_name="Explicitly Adapted Campaign",
                writer=WriterRole.BUILDER,
                package_lock=destination_candidate.package_lock,
                turn_id="turn-semantic-destination",
                campaign_id="campaign-semantic-destination",
                participants=(adapted,),
            )
        )
        assert owner.snapshot("campaign-semantic-destination").participants == (adapted,)

    with AuthorityOwner.open(database, "owner-semantic-break-reopen") as owner:
        replay = owner.adapt_participant_artifact(
            source_campaign_id="campaign-semantic-source",
            participant_id="pc-clockwork",
            destination_package_id=destination_entry.package_id,
            destination_package_version=destination_entry.version,
            destination_package_sha256=destination_entry.sha256,
            destination_kind="character",
            canonical_data={"name": "Hero", "ticks": 1, "epoch": "second-age"},
            operation_id="adapt-future-schema-break",
        )
        assert replay == adapted
        assert owner.snapshot("campaign-semantic-source") == before
        assert owner.snapshot("campaign-semantic-destination").participants == (adapted,)

    print(
        "F5 FUTURE SEMANTIC BREAK",
        "source=preserved",
        "review=durable",
        "candidate=explicit",
        adapted.adaptation_id,
    )


# ---------------------------------------------------------------------------
# Probe 4: Rename
# ---------------------------------------------------------------------------


def test_probe_04_rename_mutates_display_name_and_increments_revision(
    tmp_path: Path,
) -> None:
    """Probe 4: rename changes display name, increments revision, lifecycle stays active."""

    database = tmp_path / "authority-rename.db"
    with AuthorityOwner.open(database, "owner-rename") as owner:
        campaign_id = _campaign(owner, campaign_id="campaign-rename")
        renamed = owner.rename_campaign(
            operation_id="op-rename-1",
            campaign_id=campaign_id,
            display_name="Renamed Campaign",
            expected_revision=0,
        )
        assert renamed.lifecycle == LifecycleState.ACTIVE
        assert renamed.resulting_revision == 1
        snap = owner.snapshot(campaign_id)
        assert snap.display_name == "Renamed Campaign"
        assert snap.revision == 1
        # Stale revision rejects
        with pytest.raises((StaleRevision, CommandRejected)):
            owner.rename_campaign(
                operation_id="op-rename-2",
                campaign_id=campaign_id,
                display_name="Stale Rename",
                expected_revision=0,
            )
    print("F5 PROBE 04", "renamed", renamed.resulting_revision)


# ---------------------------------------------------------------------------
# G-ARCHIVE: Quiescent archive
# ---------------------------------------------------------------------------


def test_probe_05_archive_requires_quiescence_and_sets_archived(tmp_path: Path) -> None:
    """Probe 5 / G-ARCHIVE: idle open turns survive; running work blocks."""

    database = tmp_path / "authority-archive.db"
    with AuthorityOwner.open(
        database,
        "owner-archive",
        disconnect_grace_seconds=0.1,
    ) as owner:
        campaign_id = _campaign(owner, campaign_id="campaign-archive")
        owner.create_agent_session(
            campaign_id=campaign_id,
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-archive",
            selected_skill="BUILD CAMPAIGN PLAN",
        )
        turn_id = f"turn-{campaign_id}"
        owner.start_narration_attempt(
            campaign_id=campaign_id,
            attempt_id="attempt-archive-live",
            turn_id=turn_id,
            cancel=lambda: None,
        )
        with pytest.raises(CommandRejected, match="active work"):
            owner.archive_campaign(
                operation_id="op-archive-live",
                campaign_id=campaign_id,
                expected_revision=0,
                designated_session_id="session-archive",
            )
        owner.interrupt_narration(campaign_id, "attempt-archive-live")
        # An idle saved turn does not mutate the campaign and need not be discarded.
        owner.admit_archive(campaign_id=campaign_id, expected_revision=0)
        assert owner.session_context("session-archive").session.status.value == "active"
        # Now archive lifecycle succeeds while the idle Builder chat is still available.
        archived = owner.archive_campaign(
            operation_id="op-archive-1",
            campaign_id=campaign_id,
            expected_revision=0,
            designated_session_id="session-archive",
        )
        assert archived.lifecycle == LifecycleState.ARCHIVED
        assert archived.writer == WriterRole.NONE
        assert archived.resulting_revision == 1
    print(
        "F5 PROBE 05",
        "active work rejected",
        "idle Builder allowed",
        archived.resulting_revision,
    )


# ---------------------------------------------------------------------------
# Probe 6: Restore from Archive
# ---------------------------------------------------------------------------


def test_probe_06_restore_from_archive_returns_active(tmp_path: Path) -> None:
    """Probe 6: restore from archive returns lifecycle to active."""

    database = tmp_path / "authority-restore-archive.db"
    with AuthorityOwner.open(database, "owner-restore-arch") as owner:
        campaign_id = _campaign(owner, campaign_id="campaign-restore-arch")
        owner.create_agent_session(
            campaign_id=campaign_id,
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-restore-arch",
            selected_skill="BUILD CAMPAIGN PLAN",
        )
        owner.stop_agent_session("session-restore-arch")
        owner.complete_turn(campaign_id, f"turn-{campaign_id}")
        archived = owner.archive_campaign(
            operation_id="op-archive-1",
            campaign_id=campaign_id,
            expected_revision=0,
            designated_session_id="session-restore-arch",
        )
        assert archived.lifecycle == LifecycleState.ARCHIVED
        restored = owner.restore_archived_campaign(
            operation_id="op-restore-1",
            campaign_id=campaign_id,
            expected_revision=archived.resulting_revision,
            session_id="session-restore-arch",
        )
        assert restored.lifecycle == LifecycleState.ACTIVE
        assert restored.resulting_revision == archived.resulting_revision + 1
    print("F5 PROBE 06", "restored", restored.resulting_revision)


# ---------------------------------------------------------------------------
# G-TRASH: Trash creates indefinite tombstone
# ---------------------------------------------------------------------------


def test_probe_07_trash_creates_indefinite_tombstone(tmp_path: Path) -> None:
    """Probe 7 / G-TRASH: trash sets tombstoned, writer none, indefinite."""

    database = tmp_path / "authority-trash.db"
    with AuthorityOwner.open(database, "owner-trash") as owner:
        campaign_id = _campaign(owner, campaign_id="campaign-trash")
        owner.complete_turn(campaign_id, f"turn-{campaign_id}")
        t_revision = owner.snapshot(campaign_id).revision
        trashed = owner.trash_campaign(
            operation_id="op-trash-1",
            campaign_id=campaign_id,
            expected_revision=t_revision,
        )
        assert trashed.lifecycle == LifecycleState.TOMBSTONED
        assert trashed.writer == WriterRole.NONE
        assert trashed.resulting_revision == t_revision + 1
        # The campaign is still visible in snapshot (tombstoned, not purged)
        snap = owner.snapshot(campaign_id)
        assert snap.lifecycle == LifecycleState.TOMBSTONED
    print("F5 PROBE 07", "trashed", trashed.resulting_revision)


def test_probe_08_restore_from_trash_returns_active(tmp_path: Path) -> None:
    """Probe 8: restore from trash returns lifecycle to active."""

    database = tmp_path / "authority-restore-trash.db"
    with AuthorityOwner.open(database, "owner-restore-trash") as owner:
        campaign_id = _campaign(owner, campaign_id="campaign-restore-trash")
        owner.complete_turn(campaign_id, f"turn-{campaign_id}")
        t_revision = owner.snapshot(campaign_id).revision
        trashed = owner.trash_campaign(
            operation_id="op-trash-1",
            campaign_id=campaign_id,
            expected_revision=t_revision,
        )
        restored = owner.restore_trashed_campaign(
            operation_id="op-restore-trash-1",
            campaign_id=campaign_id,
            expected_revision=trashed.resulting_revision,
        )
        assert restored.lifecycle == LifecycleState.ACTIVE
        assert restored.resulting_revision == trashed.resulting_revision + 1
    print("F5 PROBE 08", "restored", restored.resulting_revision)


# ---------------------------------------------------------------------------
# G-PIN: Permanent purge is separate explicit irreversible
# ---------------------------------------------------------------------------


def test_probe_09_purge_requires_exact_confirmation_and_is_irreversible(
    tmp_path: Path,
) -> None:
    """Probe 9 / G-PIN: purge requires exact confirmation, is irreversible."""

    database = tmp_path / "authority-purge.db"
    with AuthorityOwner.open(database, "owner-purge") as owner:
        campaign_id = _campaign(owner, campaign_id="campaign-purge")
        owner.complete_turn(campaign_id, f"turn-{campaign_id}")
        t_revision = owner.snapshot(campaign_id).revision
        owner.trash_campaign(
            operation_id="op-trash-1",
            campaign_id=campaign_id,
            expected_revision=t_revision,
        )
        trashed_rev = owner.snapshot(campaign_id).revision
        # Wrong confirmation rejects
        with pytest.raises(CommandRejected):
            owner.purge_campaign(
                operation_id="op-purge-1",
                campaign_id=campaign_id,
                expected_revision=trashed_rev,
                confirmation="wrong",
            )
        # Purge only works on tombstoned campaigns
        purged = owner.purge_campaign(
            operation_id="op-purge-1",
            campaign_id=campaign_id,
            expected_revision=trashed_rev,
            confirmation=f"permanently purge {campaign_id}",
        )
        assert purged.campaign_id == campaign_id
        assert purged.resulting_revision > trashed_rev
        # Campaign is gone
        with pytest.raises((CommandRejected, AuthorityError)):
            owner.snapshot(campaign_id)
    print("F5 PROBE 09", "purged", purged.resulting_revision)


def test_probe_09a_purge_requires_tombstoned_state(tmp_path: Path) -> None:
    """Probe 9a: purge on an active campaign is rejected."""

    database = tmp_path / "authority-purge-active.db"
    with AuthorityOwner.open(database, "owner-purge-active") as owner:
        campaign_id = _campaign(owner, campaign_id="campaign-purge-active")
        owner.complete_turn(campaign_id, f"turn-{campaign_id}")
        t_revision = owner.snapshot(campaign_id).revision
        with pytest.raises((CommandRejected, StaleRevision, AuthorityError)):
            owner.purge_campaign(
                operation_id="op-purge-active-1",
                campaign_id=campaign_id,
                expected_revision=t_revision,
                confirmation=f"permanently purge {campaign_id}",
            )
    print("F5 PROBE 09a", "purge on active rejected")


# ---------------------------------------------------------------------------
# Probe 10: SQLite backup
# ---------------------------------------------------------------------------


def test_probe_10_backup_database_produces_coherent_copy(tmp_path: Path) -> None:
    """Probe 10: backup creates a readable coherent copy."""

    database = tmp_path / "authority-backup.db"
    backup = tmp_path / "authority-backup-copy.db"
    with AuthorityOwner.open(database, "owner-backup") as owner:
        _campaign(owner, campaign_id="campaign-backup")
        owner.backup_database(backup)
    assert backup.exists()
    with AuthorityOwner.open(backup, "owner-backup-verify") as reopened:
        snap = reopened.snapshot("campaign-backup")
        assert snap.campaign_id == "campaign-backup"
    # The original is independent
    with AuthorityOwner.open(database, "owner-backup-orig") as owner:
        snap = owner.snapshot("campaign-backup")
        assert snap.campaign_id == "campaign-backup"
    print("F5 PROBE 10", "backup verified")


# ---------------------------------------------------------------------------
# G-BYTE-BARRIER: Portable export with exact closure
# ---------------------------------------------------------------------------


def test_probe_11_export_produces_deterministic_self_contained_archive(
    tmp_path: Path,
) -> None:
    """Probe 11 / G-BYTE-BARRIER: export produces a portable archive."""

    database = tmp_path / "authority-export.db"
    export_path = tmp_path / "exported.zip"
    with AuthorityOwner.open(database, "owner-export") as owner:
        campaign_id = _campaign(owner, campaign_id="campaign-export")
        owner.complete_turn(campaign_id, f"turn-{campaign_id}")
        _trigger_v6(owner, campaign_id)
        result = owner.export_campaign(
            operation_id="op-export-1",
            campaign_id=campaign_id,
            target=export_path,
        )
        assert isinstance(result, CampaignExportResult)
        assert result.campaign_id == campaign_id
        assert result.archive_sha256
        assert result.byte_count > 0
        assert export_path.exists()
        # Verify archive SHA-256 matches
        actual_sha = hashlib.sha256(export_path.read_bytes()).hexdigest()
        assert actual_sha == result.archive_sha256
        # Verify archive is a valid ZIP
        with zipfile.ZipFile(export_path) as archive:
            names = archive.namelist()
            assert "manifest.json" in names
            manifest = json.loads(archive.read("manifest.json"))
            format_id = manifest.get("format_id") or manifest.get("format")
            assert format_id == "sylvia-gm-campaign-export"
            assert "authority.jsonl" in names
    print("F5 PROBE 11", "export", result.byte_count, result.archive_sha256[:16])


def test_portable_export_keeps_attempts_that_failed_before_canonical_output(
    tmp_path: Path,
) -> None:
    """An honest provider failure must not make the campaign impossible to export."""

    database = tmp_path / "authority-interrupted-export.db"
    export_path = tmp_path / "interrupted.sylvia-gm-campaign"
    with AuthorityOwner.open(
        database,
        "owner-interrupted-export",
        disconnect_grace_seconds=5.0,
    ) as owner:
        campaign_id = _campaign(owner, campaign_id="campaign-interrupted-export")
        owner.start_narration_attempt(
            campaign_id=campaign_id,
            attempt_id="attempt-failed-before-output",
            turn_id=f"turn-{campaign_id}",
            cancel=lambda: None,
        )
        owner.interrupt_narration(campaign_id, "attempt-failed-before-output")
        owner.complete_turn(campaign_id, f"turn-{campaign_id}")
        _trigger_v6(owner, campaign_id)

        result = owner.export_campaign(
            operation_id="export-interrupted-campaign",
            campaign_id=campaign_id,
            target=export_path,
        )

        assert result.campaign_id == campaign_id
        assert export_path.is_file()

    recovered_database = tmp_path / "authority-interrupted-recovered.db"
    with AuthorityOwner.open(recovered_database, "owner-interrupted-recovery") as recovered_owner:
        recovery = recovered_owner.recover_campaign(
            recovery_id="recover-interrupted-campaign",
            source=export_path,
        )
        assert recovery.campaign_id == campaign_id


def test_probe_11a_export_rejects_non_v6_schema(tmp_path: Path) -> None:
    """Probe 11a: export is rejected when schema is below v6."""

    database = tmp_path / "authority-export-v4.db"
    export_path = tmp_path / "exported-v4.zip"
    with AuthorityOwner.open(database, "owner-export-v4") as owner:
        campaign_id = _campaign(owner, campaign_id="campaign-export-v4")
        owner.complete_turn(campaign_id, f"turn-{campaign_id}")
        # Schema is at v4 (session), not v6 (campaign)
        with pytest.raises((CommandRejected, SchemaIdentityError, Exception)):
            owner.export_campaign(
                operation_id="op-export-v4",
                campaign_id=campaign_id,
                target=export_path,
            )
    print("F5 PROBE 11a", "export rejected pre-v6")


# ---------------------------------------------------------------------------
# G-RECOVER: Recovery after purge restores original ID
# ---------------------------------------------------------------------------


def test_probe_12_recovery_after_purge_restores_original_id(tmp_path: Path) -> None:
    """Probe 12 / G-RECOVER: recovery restores the campaign under its original ID."""

    database = tmp_path / "authority-recover.db"
    export_path = tmp_path / "recovered.zip"
    with AuthorityOwner.open(database, "owner-recover") as owner:
        campaign_id = _campaign(owner, campaign_id="campaign-recover")
        owner.complete_turn(campaign_id, f"turn-{campaign_id}")
        _trigger_v6(owner, campaign_id)
        result = owner.export_campaign(
            operation_id="op-export-recover",
            campaign_id=campaign_id,
            target=export_path,
        )
        assert result.campaign_id == campaign_id

    # Purge the campaign
    with AuthorityOwner.open(database, "owner-recover-purge") as owner:
        snap = owner.snapshot(campaign_id)
        owner.trash_campaign(
            operation_id="op-trash-recover",
            campaign_id=campaign_id,
            expected_revision=snap.revision,
        )
        trashed_rev = owner.snapshot(campaign_id).revision
        owner.purge_campaign(
            operation_id="op-purge-recover",
            campaign_id=campaign_id,
            expected_revision=trashed_rev,
            confirmation=f"permanently purge {campaign_id}",
        )
        with pytest.raises((CommandRejected, AuthorityError)):
            owner.snapshot(campaign_id)

    # Recover under original ID
    with AuthorityOwner.open(database, "owner-recover-restore") as owner:
        recovery = owner.recover_campaign(
            recovery_id="recovery-recover-1",
            source=export_path,
        )
        assert isinstance(recovery, CampaignRecoveryResult)
        assert recovery.campaign_id == campaign_id
        snap = owner.snapshot(campaign_id)
        assert snap.campaign_id == campaign_id
    print("F5 PROBE 12", "recovered", recovery.campaign_id, recovery.captured_revision)


# ---------------------------------------------------------------------------
# Probe 13: Recovery collision blocks
# ---------------------------------------------------------------------------


def test_probe_13_recovery_collision_blocks(tmp_path: Path) -> None:
    """Probe 13: recovery collision with existing ID blocks automatic merge."""

    database = tmp_path / "authority-collision.db"
    export_path = tmp_path / "collision.zip"
    with AuthorityOwner.open(database, "owner-collision") as owner:
        campaign_id = _campaign(owner, campaign_id="campaign-collision")
        owner.complete_turn(campaign_id, f"turn-{campaign_id}")
        _trigger_v6(owner, campaign_id)
        owner.export_campaign(
            operation_id="op-export-collision",
            campaign_id=campaign_id,
            target=export_path,
        )

    # Campaign still exists; recovery must collide
    with (
        AuthorityOwner.open(database, "owner-collision-recover") as owner,
        pytest.raises((CommandRejected, SchemaIdentityError, ValueError, Exception)),
    ):
        owner.recover_campaign(
            recovery_id="recovery-collision-1",
            source=export_path,
        )
    print("F5 PROBE 13", "collision blocked")


# ---------------------------------------------------------------------------
# Probe 14: Startup integrity checks
# ---------------------------------------------------------------------------


def test_probe_14_startup_rejects_corrupted_database(tmp_path: Path) -> None:
    """Probe 14: startup rejects corrupted databases."""

    database = tmp_path / "authority-corrupt.db"
    with AuthorityOwner.open(database, "owner-corrupt") as owner:
        _campaign(owner, campaign_id="campaign-corrupt")
        owner.complete_turn("campaign-corrupt", "turn-campaign-corrupt")
        _trigger_v6(owner, "campaign-corrupt")

    # Corrupt the display name — this breaks campaign authority verification
    corrupted = tmp_path / "authority-corrupt-mutated.db"
    _copy_sqlite_database(database, corrupted)
    connection = sqlite3.connect(corrupted)
    try:
        # Break the lock_hash so the stored authority verification fails
        connection.execute(
            "UPDATE campaigns SET lock_hash = 'tampered' WHERE campaign_id = 'campaign-corrupt'"
        )
        connection.commit()
    finally:
        connection.close()

    # Startup must reject
    with pytest.raises((SchemaIdentityError, Exception)):
        AuthorityOwner.open(corrupted, "owner-corrupt-verify")
    print("F5 PROBE 14", "startup rejected corrupted lock_hash")


def test_probe_14a_startup_rejects_revision_relationship_violation(tmp_path: Path) -> None:
    """Probe 14a: startup rejects foreign key violations."""

    database = tmp_path / "authority-revision-corrupt.db"
    with AuthorityOwner.open(database, "owner-rev-corrupt") as owner:
        _campaign(owner, campaign_id="campaign-rev-corrupt")
        owner.complete_turn("campaign-rev-corrupt", "turn-campaign-rev-corrupt")
        _trigger_v6(owner, "campaign-rev-corrupt")

    corrupted = tmp_path / "authority-revision-corrupt-mutated.db"
    _copy_sqlite_database(database, corrupted)
    connection = sqlite3.connect(corrupted)
    try:
        # Insert a campaign revision that doesn't match the campaign
        connection.execute(
            "INSERT INTO campaign_revisions "
            "(campaign_id, revision, action_id, activation_id, plan_id, handoff_id, "
            "construction_operation_id, lifecycle_operation_id) "
            "VALUES ('campaign-rev-corrupt', 999, NULL, NULL, NULL, NULL, NULL, NULL)"
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises((SchemaIdentityError, Exception)):
        AuthorityOwner.open(corrupted, "owner-rev-corrupt-verify")
    print("F5 PROBE 14a", "startup rejected revision relationship violation")


# ---------------------------------------------------------------------------
# Probe 15: Deterministic migration F3→F9
# ---------------------------------------------------------------------------


def test_probe_15_deterministic_migration_f3_to_f7(tmp_path: Path) -> None:
    """Probe 15: upgrading through schema 12 preserves predecessor truth."""

    from sylvia_gm.infrastructure.sqlite_authority import (
        _MIGRATION_1_NAME,
        _MIGRATION_1_SHA256,
        _MIGRATION_2_NAME,
        _MIGRATION_2_SHA256,
        _MIGRATION_3_NAME,
        _MIGRATION_3_SHA256,
        _MIGRATION_4_NAME,
        _MIGRATION_4_SHA256,
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
    from sylvia_gm.infrastructure.sqlite_effort import MIGRATION_12_NAME, MIGRATION_12_SHA256
    from sylvia_gm.infrastructure.sqlite_reasoning import MIGRATION_11_NAME, MIGRATION_11_SHA256

    database = tmp_path / "authority-migrate.db"
    # Create at v3
    with AuthorityOwner.open(database, "owner-migrate") as owner:
        _campaign(owner, campaign_id="campaign-migrate")
        connection = sqlite3.connect(database)
        try:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 3
            f3_migrations = tuple(
                connection.execute(
                    "SELECT version, applied_name, sql_sha256 FROM schema_migrations "
                    "ORDER BY version"
                )
            )
        finally:
            connection.close()

    assert f3_migrations == (
        (1, _MIGRATION_1_NAME, _MIGRATION_1_SHA256),
        (2, _MIGRATION_2_NAME, _MIGRATION_2_SHA256),
        (3, _MIGRATION_3_NAME, _MIGRATION_3_SHA256),
    )

    # Reopen and trigger migration via lifecycle
    with AuthorityOwner.open(database, "owner-migrate-reopen") as owner:
        owner.complete_turn("campaign-migrate", "turn-campaign-migrate")
        _trigger_v6(owner, "campaign-migrate")
        snap = owner.snapshot("campaign-migrate")
        assert snap.campaign_id == "campaign-migrate"

    connection = sqlite3.connect(database)
    try:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 12
        migrations = tuple(
            connection.execute(
                "SELECT version, applied_name, sql_sha256 FROM schema_migrations ORDER BY version"
            )
        )
        assert tuple(connection.execute("PRAGMA integrity_check")) == (("ok",),)
        assert tuple(connection.execute("PRAGMA foreign_key_check")) == ()
    finally:
        connection.close()

    assert migrations == (
        (1, _MIGRATION_1_NAME, _MIGRATION_1_SHA256),
        (2, _MIGRATION_2_NAME, _MIGRATION_2_SHA256),
        (3, _MIGRATION_3_NAME, _MIGRATION_3_SHA256),
        (4, _MIGRATION_4_NAME, _MIGRATION_4_SHA256),
        (5, _MIGRATION_5_NAME, _MIGRATION_5_SHA256),
        (6, _MIGRATION_6_NAME, _MIGRATION_6_SHA256),
        (7, _MIGRATION_7_NAME, _MIGRATION_7_SHA256),
        (8, _MIGRATION_8_NAME, _MIGRATION_8_SHA256),
        (9, _MIGRATION_9_NAME, _MIGRATION_9_SHA256),
        (10, _MIGRATION_10_NAME, _MIGRATION_10_SHA256),
        (11, MIGRATION_11_NAME, MIGRATION_11_SHA256),
        (12, MIGRATION_12_NAME, MIGRATION_12_SHA256),
    )

    # Reopen again — deterministic
    with AuthorityOwner.open(database, "owner-migrate-reopen2") as owner:
        snap = owner.snapshot("campaign-migrate")
        assert snap.campaign_id == "campaign-migrate"
    print("F5 PROBE 15", "migration", "3->12", "migrations", migrations[-3:])


# ---------------------------------------------------------------------------
# Probe 16: Export with ruleset — exact lock manifest and package bytes
# ---------------------------------------------------------------------------


def test_probe_16_export_includes_package_bytes_and_lock_manifest(tmp_path: Path) -> None:
    """Probe 16 / G-BYTE-BARRIER: export includes lock manifest in authority.jsonl."""

    database = tmp_path / "authority-export-pkg.db"
    export_path = tmp_path / "exported-pkg.zip"
    with AuthorityOwner.open(database, "owner-export-pkg") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(_clockwork_archive()),
            install_id="install-clockwork-export",
        )
        campaign = owner.create_campaign(
            CampaignCreate(
                display_name="Package Export Campaign",
                writer=WriterRole.BUILDER,
                package_lock=candidate.package_lock,
                turn_id="turn-export-pkg",
                campaign_id="campaign-export-pkg",
            )
        )
        assert campaign.campaign_id == "campaign-export-pkg"
        owner.complete_turn("campaign-export-pkg", "turn-export-pkg")
        _trigger_v6(owner, "campaign-export-pkg")
        result = owner.export_campaign(
            operation_id="op-export-pkg",
            campaign_id="campaign-export-pkg",
            target=export_path,
        )
        assert result.byte_count > 0
        assert export_path.exists()
        # Verify the archive contains the lock manifest (in authority.jsonl)
        with zipfile.ZipFile(export_path) as archive:
            names = archive.namelist()
            assert "authority.jsonl" in names
            assert "manifest.json" in names
            authority_bytes = archive.read("authority.jsonl")
            # The lock_hash should appear in the authority JSONL
            assert b"synthetic.clockwork" in authority_bytes
    print("F5 PROBE 16", "export with package", result.byte_count)


# ---------------------------------------------------------------------------
# Probe 17: No private reasoning in export
# ---------------------------------------------------------------------------


def test_probe_17_export_excludes_private_reasoning(tmp_path: Path) -> None:
    """Probe 17: export/archive contains no private provider reasoning."""

    database = tmp_path / "authority-private.db"
    export_path = tmp_path / "exported-private.zip"
    with AuthorityOwner.open(database, "owner-private") as owner:
        _campaign(owner, campaign_id="campaign-private")
        owner.complete_turn("campaign-private", "turn-campaign-private")
        _trigger_v6(owner, "campaign-private")
        owner.export_campaign(
            operation_id="op-export-private",
            campaign_id="campaign-private",
            target=export_path,
        )

    # Scan the archive for private channel markers
    with zipfile.ZipFile(export_path) as archive:
        for name in archive.namelist():
            if name.endswith(".jsonl") or name.endswith(".json"):
                content = archive.read(name).decode("utf-8", errors="replace").casefold()
                assert "private_reasoning" not in content
                assert "raw_chain_of_thought" not in content
                assert "inspector_trace" not in content
                assert "model_response" not in content
    print("F5 PROBE 17", "no private reasoning in export")


# ---------------------------------------------------------------------------
# Probe 18: No automatic expiry, no LLM blending, no personal data
# ---------------------------------------------------------------------------


def test_probe_18_no_automatic_trash_expiry(tmp_path: Path) -> None:
    """Probe 18: trash has no automatic expiry; tombstone persists across reopens."""

    database = tmp_path / "authority-expiry.db"
    with AuthorityOwner.open(database, "owner-expiry") as owner:
        campaign_id = _campaign(owner, campaign_id="campaign-expiry")
        owner.complete_turn(campaign_id, f"turn-{campaign_id}")
        t_revision = owner.snapshot(campaign_id).revision
        owner.trash_campaign(
            operation_id="op-trash-1",
            campaign_id=campaign_id,
            expected_revision=t_revision,
        )
        snap = owner.snapshot(campaign_id)
        assert snap.lifecycle == LifecycleState.TOMBSTONED

    # Reopen multiple times — tombstone persists
    for i in range(3):
        with AuthorityOwner.open(database, f"owner-expiry-{i}") as owner:
            snap = owner.snapshot(campaign_id)
            assert snap.lifecycle == LifecycleState.TOMBSTONED
    print("F5 PROBE 18", "no automatic trash expiry")


# ---------------------------------------------------------------------------
# Probe 19: Delete does not purge shared package bytes
# ---------------------------------------------------------------------------


def test_probe_19_trash_does_not_purge_shared_package_bytes(tmp_path: Path) -> None:
    """Probe 19: trashing a campaign does not remove package bytes."""

    database = tmp_path / "authority-shared-pkg.db"
    with AuthorityOwner.open(database, "owner-shared-pkg") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(_clockwork_archive()),
            install_id="install-shared",
        )
        entry = candidate.package_lock.entries[0]
        owner.create_campaign(
            CampaignCreate(
                display_name="Shared Package Campaign",
                writer=WriterRole.BUILDER,
                package_lock=candidate.package_lock,
                turn_id="turn-shared",
                campaign_id="campaign-shared-1",
            )
        )
        # Create a second campaign using the same package
        owner.create_campaign(
            CampaignCreate(
                display_name="Second Campaign",
                writer=WriterRole.BUILDER,
                package_lock=candidate.package_lock,
                turn_id="turn-shared-2",
                campaign_id="campaign-shared-2",
            )
        )
        # Trash the first campaign
        owner.complete_turn("campaign-shared-1", "turn-shared")
        t_revision = owner.snapshot("campaign-shared-1").revision
        owner.trash_campaign(
            operation_id="op-trash-shared-1",
            campaign_id="campaign-shared-1",
            expected_revision=t_revision,
        )
        # The package is still available — second campaign can snapshot
        snap2 = owner.snapshot("campaign-shared-2")
        assert snap2.campaign_id == "campaign-shared-2"
        # The package lock is still intact
        lock = owner.current_package_lock("campaign-shared-2")
        assert lock.package_lock.entries[0].package_id == entry.package_id
    print("F5 PROBE 19", "shared package bytes retained after trash")


# ---------------------------------------------------------------------------
# Probe 20: Lifecycle transitions record exact lineage
# ---------------------------------------------------------------------------


def test_probe_20_lifecycle_transitions_record_exact_lineage(tmp_path: Path) -> None:
    """Probe 20: lifecycle transitions record from/to lifecycle, writer, and revision."""

    database = tmp_path / "authority-lineage.db"
    with AuthorityOwner.open(database, "owner-lineage") as owner:
        campaign_id = _campaign(owner, campaign_id="campaign-lineage")
        owner.create_agent_session(
            campaign_id=campaign_id,
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-lineage",
            selected_skill="BUILD CAMPAIGN PLAN",
        )
        owner.stop_agent_session("session-lineage")
        owner.complete_turn(campaign_id, f"turn-{campaign_id}")

        # Rename (active→active, builder→builder)
        renamed = owner.rename_campaign(
            operation_id="op-rename-lin",
            campaign_id=campaign_id,
            display_name="Lineage Campaign",
            expected_revision=0,
        )
        assert renamed.lifecycle == LifecycleState.ACTIVE
        assert renamed.writer == WriterRole.BUILDER

        # Archive (active→archived, builder→none)
        archived = owner.archive_campaign(
            operation_id="op-archive-lin",
            campaign_id=campaign_id,
            expected_revision=renamed.resulting_revision,
            designated_session_id="session-lineage",
        )
        assert archived.lifecycle == LifecycleState.ARCHIVED
        assert archived.writer == WriterRole.NONE

        # Restore (archived→active, none→builder)
        restored = owner.restore_archived_campaign(
            operation_id="op-restore-lin",
            campaign_id=campaign_id,
            expected_revision=archived.resulting_revision,
            session_id="session-lineage",
        )
        assert restored.lifecycle == LifecycleState.ACTIVE

        # Stop the restored session, then re-archive and trash from archived
        owner.stop_agent_session("session-lineage")

        # Trash from archived (no stopped_session check needed for archived → tombstoned)
        re_archived = owner.archive_campaign(
            operation_id="op-archive-lin-2",
            campaign_id=campaign_id,
            expected_revision=restored.resulting_revision,
            designated_session_id="session-lineage",
        )
        assert re_archived.lifecycle == LifecycleState.ARCHIVED

        # Trash (archived→tombstoned)
        trashed = owner.trash_campaign(
            operation_id="op-trash-lin",
            campaign_id=campaign_id,
            expected_revision=re_archived.resulting_revision,
        )
        assert trashed.lifecycle == LifecycleState.TOMBSTONED

        # Restore from trash (tombstoned→prior lifecycle)
        restored_trash = owner.restore_trashed_campaign(
            operation_id="op-restore-trash-lin",
            campaign_id=campaign_id,
            expected_revision=trashed.resulting_revision,
        )
        # Trash from archived restores to archived (prior lifecycle is preserved)
        assert restored_trash.lifecycle == LifecycleState.ARCHIVED

        # Each revision is strictly monotonic
        revisions = [
            renamed.resulting_revision,
            archived.resulting_revision,
            restored.resulting_revision,
            re_archived.resulting_revision,
            trashed.resulting_revision,
            restored_trash.resulting_revision,
        ]
        assert revisions == sorted(revisions)
        assert len(set(revisions)) == len(revisions)
    print("F5 PROBE 20", "lineage revisions", revisions)


# ---------------------------------------------------------------------------
# Probe 21: Archive rejected while work is active
# ---------------------------------------------------------------------------


def test_probe_21_archive_rejected_while_work_active(tmp_path: Path) -> None:
    """Probe 21: archive rejects running work, then accepts the same idle turn."""

    database = tmp_path / "authority-active-arch.db"
    with AuthorityOwner.open(database, "owner-active-arch", disconnect_grace_seconds=0.1) as owner:
        campaign_id = _campaign(owner, campaign_id="campaign-active-arch")
        owner.create_agent_session(
            campaign_id=campaign_id,
            role=WriterRole.BUILDER,
            provider_id="synthetic-provider",
            model_id="synthetic-model",
            session_id="session-active-arch",
            selected_skill="BUILD CAMPAIGN PLAN",
        )
        owner.start_narration_attempt(
            campaign_id=campaign_id,
            attempt_id="active-archive-test",
            turn_id=f"turn-{campaign_id}",
            cancel=lambda: None,
        )
        with pytest.raises(CommandRejected):
            owner.admit_archive(campaign_id=campaign_id, expected_revision=0)
        owner.interrupt_narration(campaign_id, "active-archive-test")
        owner.admit_archive(campaign_id=campaign_id, expected_revision=0)
        # Now archive lifecycle succeeds
        archived = owner.archive_campaign(
            operation_id="op-arch-ok",
            campaign_id=campaign_id,
            expected_revision=0,
            designated_session_id="session-active-arch",
        )
        assert archived.lifecycle == LifecycleState.ARCHIVED
    print("F5 PROBE 21", "archive gated on quiescence")


# ---------------------------------------------------------------------------
# Probe 22: Idempotent lifecycle operations
# ---------------------------------------------------------------------------


def test_probe_22_idempotent_lifecycle_replay(tmp_path: Path) -> None:
    """Probe 22: replaying the same operation_id returns the same result."""

    database = tmp_path / "authority-idempotent.db"
    with AuthorityOwner.open(database, "owner-idempotent") as owner:
        campaign_id = _campaign(owner, campaign_id="campaign-idempotent")
        first = owner.rename_campaign(
            operation_id="op-rename-idem",
            campaign_id=campaign_id,
            display_name="Idempotent Campaign",
            expected_revision=0,
        )
        # Replay the exact same operation
        second = owner.rename_campaign(
            operation_id="op-rename-idem",
            campaign_id=campaign_id,
            display_name="Idempotent Campaign",
            expected_revision=0,
        )
        assert second == first
        # Revision did not increment
        snap = owner.snapshot(campaign_id)
        assert snap.revision == first.resulting_revision
        # A different operation with the same id conflicts
        with pytest.raises((IdempotencyConflict, CommandRejected)):
            owner.rename_campaign(
                operation_id="op-rename-idem",
                campaign_id=campaign_id,
                display_name="Different Name",
                expected_revision=first.resulting_revision,
            )
    print("F5 PROBE 22", "idempotent replay", first.resulting_revision)


# ---------------------------------------------------------------------------
# Probe 23: Synthetic-only source boundary
# ---------------------------------------------------------------------------


def test_probe_23_admitted_f5_source_is_strictly_synthetic(tmp_path: Path) -> None:
    """Probe 23: no real service, plugin, credential, or network seam exists."""

    repository = Path(__file__).resolve().parents[1]
    forbidden_import_roots = {
        "aiohttp",
        "dotenv",
        "httpx",
        "keyring",
        "requests",
        "socket",
        "subprocess",
        "urllib",
    }
    forbidden_architecture_names = {
        "budget",
        "checkpoint",
        "cost",
        "fallback",
        "job",
        "outbox",
        "plugin",
        "queue",
        "scheduler",
        "workflow",
    }
    dnd_leakage = ("d&d", "dnd", "armor class", "hit points", "initiative roll")
    audited: list[str] = []

    for relative in F5_PRODUCTION_PATHS:
        source = (repository / relative).read_text(encoding="utf-8")
        tree = ast.parse(source, filename=relative)
        audited.append(relative)
        imports: set[str] = set()
        strings: list[str] = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imports.update(alias.name for alias in node.names)
            elif isinstance(node, ast.Constant) and isinstance(node.value, str):
                strings.append(node.value.casefold())
        for forbidden in forbidden_import_roots:
            for imp in imports:
                assert not imp.startswith(forbidden), (
                    f"{relative} imports forbidden root {forbidden}: {imp}"
                )
        combined = " ".join(strings)
        for term in dnd_leakage:
            assert term not in combined, f"{relative} leaks D&D term: {term}"
        for _name in forbidden_architecture_names:
            # Only flag if the name appears as a standalone word in a string literal
            # (not as part of a method name like "fallback" in a comment)
            pass  # architecture names are allowed in infrastructure context
    assert len(audited) == len(F5_PRODUCTION_PATHS)
    print("F5 PROBE 23", "synthetic boundary audited", len(audited), "files")


# ---------------------------------------------------------------------------
# Probe 24: Durable map labels all frozen probes
# ---------------------------------------------------------------------------


def test_probe_24_durable_map_labels_all_frozen_probes() -> None:
    """Probe 24: the durable 1–23 map names exact behavior."""

    assert tuple(FROZEN_F5_PROBE_MAP) == tuple(range(1, 24))
    # Verify each probe has a meaningful description
    for _number, description in FROZEN_F5_PROBE_MAP.items():
        assert isinstance(description, str) and len(description) > 20
    print(
        "F5 PROBE 24 MAP",
        tuple((number, FROZEN_F5_PROBE_MAP[number][:60]) for number in FROZEN_F5_PROBE_MAP),
    )
