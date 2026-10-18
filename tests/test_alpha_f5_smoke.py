"""Smoke probe: confirm F5 lifecycle methods are wired and produce revisions."""

from __future__ import annotations

from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import LifecycleState, WriterRole
from sylvia_gm.domain.errors import CommandRejected


def _campaign(owner: AuthorityOwner, *, campaign_id: str) -> str:
    owner.create_campaign(
        __import__(
            "sylvia_gm.application.authority",
            fromlist=["CampaignCreate"],
        ).CampaignCreate(
            display_name=f"Synthetic {campaign_id}",
            writer=WriterRole.BUILDER,
            package_lock=__import__(
                "sylvia_gm.domain.authority", fromlist=["PackageLock"]
            ).PackageLock(entries=()),
            turn_id=f"turn-{campaign_id}",
            campaign_id=campaign_id,
        )
    )
    return campaign_id


def test_f5_smoke_lifecycle_rename_archive_restore_trash_purge(tmp_path: Path) -> None:
    database = tmp_path / "f5-smoke.db"
    with AuthorityOwner.open(database, "owner-f5-smoke") as owner:
        campaign_id = _campaign(owner, campaign_id="campaign-smoke")
        snapshot = owner.snapshot(campaign_id)
        assert snapshot.lifecycle == LifecycleState.ACTIVE
        assert snapshot.writer == WriterRole.BUILDER
        assert snapshot.revision == 0

        # Rename
        renamed = owner.rename_campaign(
            operation_id="op-rename-1",
            campaign_id=campaign_id,
            display_name="Renamed Smoke",
            expected_revision=0,
        )
        assert renamed.lifecycle == LifecycleState.ACTIVE
        assert renamed.resulting_revision == 1
        snapshot2 = owner.snapshot(campaign_id)
        assert snapshot2.display_name == "Renamed Smoke"
        assert snapshot2.revision == 1

        # Archive requires quiescence (no active sessions, completed turns)
        owner.complete_turn(campaign_id, f"turn-{campaign_id}")
        archived = owner.archive_campaign(
            operation_id="op-archive-1",
            campaign_id=campaign_id,
            expected_revision=1,
        )
        assert archived.lifecycle == LifecycleState.ARCHIVED
        assert archived.writer == WriterRole.NONE
        assert archived.resulting_revision == 2

        # Restore from archive
        restored = owner.restore_archived_campaign(
            operation_id="op-restore-1",
            campaign_id=campaign_id,
            expected_revision=2,
        )
        assert restored.lifecycle == LifecycleState.ACTIVE
        assert restored.resulting_revision == 3

        # Trash
        trashed = owner.trash_campaign(
            operation_id="op-trash-1",
            campaign_id=campaign_id,
            expected_revision=3,
        )
        assert trashed.lifecycle == LifecycleState.TOMBSTONED
        assert trashed.writer == WriterRole.NONE
        assert trashed.resulting_revision == 4

        # Restore from trash
        restored_trash = owner.restore_trashed_campaign(
            operation_id="op-restore-trash-1",
            campaign_id=campaign_id,
            expected_revision=4,
        )
        assert restored_trash.lifecycle == LifecycleState.ACTIVE
        assert restored_trash.resulting_revision == 5

        # Purge requires exact confirmation and tombstoned state
        owner.trash_campaign(
            operation_id="op-trash-2",
            campaign_id=campaign_id,
            expected_revision=5,
        )
        trashed_rev = owner.snapshot(campaign_id).revision
        with pytest.raises(CommandRejected):
            owner.purge_campaign(
                operation_id="op-purge-1",
                campaign_id=campaign_id,
                expected_revision=trashed_rev,
                confirmation="wrong confirmation",
            )
        purged = owner.purge_campaign(
            operation_id="op-purge-1",
            campaign_id=campaign_id,
            expected_revision=trashed_rev,
            confirmation=f"permanently purge {campaign_id}",
        )
        assert purged.campaign_id == campaign_id
        assert purged.resulting_revision > trashed_rev
        # Campaign is gone
        with pytest.raises(CommandRejected, match="unavailable"):
            owner.snapshot(campaign_id)
