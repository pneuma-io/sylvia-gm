"""Archive uses the existing durable session lifecycle and never removes Builder work."""

import threading
from pathlib import Path

import pytest

from sylvia_gm.agent._session_loop import CampaignSessionLoop
from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import LifecycleState, PackageLock, WriterRole
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionMessageKind, SessionStatus
from sylvia_gm.interfaces import owner_process


def test_archive_restore_round_trip_keeps_history_files_and_preferences(tmp_path: Path) -> None:
    database = tmp_path / "authority.sqlite3"
    workspace = tmp_path / "builder-chats" / "archived-builder"
    workspace.mkdir(parents=True)
    draft = workspace / "unfinished.md"
    draft.write_text("Synthetic unfinished work", encoding="utf-8")
    with AuthorityOwner.open(database, "archive-owner") as owner:
        owner.create_builder_chat(
            provider_id="test",
            model_id="test/model",
            session_id="archived-builder",
            display_name="Keep this draft",
        )
        owner.create_builder_chat(
            provider_id="test", model_id="test/model", session_id="other-builder"
        )
        owner.append_agent_message(
            session_id="archived-builder",
            message_id="message-one",
            kind=SessionMessageKind.USER,
            content={"text": "Synthetic request"},
        )
        owner.update_agent_session_reasoning("archived-builder", effort="high", once="low")
        before = owner.session_context("archived-builder")
        loop = CampaignSessionLoop(owner)
        result = owner_process._dispatch(  # noqa: SLF001
            None,
            "archive_builder_chat",
            {"session_id": "archived-builder"},
            application_owner=owner,
            owner_id="archive-owner",
            provider_resolver=lambda _: None,
            model_choices=lambda: (),
            session_loop=loop,
            storage_root=tmp_path,
        )
        assert result.session.status is SessionStatus.STOPPED
        assert owner.set_builder_chat_archived("archived-builder", archived=True) == result
        assert owner.session_context("other-builder").session.status is SessionStatus.ACTIVE
        with (
            pytest.raises(CommandRejected, match="not active"),
            owner.builder_session_work("archived-builder"),
        ):
            pytest.fail("Archived chats must not admit tools or inference")
    with AuthorityOwner.open(database, "reopened-archive-owner") as owner:
        assert owner.session_context("archived-builder").session.status is SessionStatus.STOPPED
        restored = owner.set_builder_chat_archived("archived-builder", archived=False)
        assert restored.display_name == "Keep this draft"
        assert owner.session_context("archived-builder") == before
        assert draft.read_text(encoding="utf-8") == "Synthetic unfinished work"
        assert owner.set_builder_chat_archived("archived-builder", archived=False) == restored


def test_archive_rejects_live_tools_and_campaign_owned_chats(tmp_path: Path) -> None:
    with AuthorityOwner.open(tmp_path / "authority.sqlite3", "busy-archive-owner") as owner:
        owner.create_builder_chat(
            provider_id="test", model_id="test/model", session_id="busy-builder"
        )
        entered, release = threading.Event(), threading.Event()

        def tools() -> None:
            with owner.builder_session_work("busy-builder"):
                entered.set()
                assert release.wait(5)

        worker = threading.Thread(target=tools)
        worker.start()
        try:
            assert entered.wait(5)
            with pytest.raises(CommandRejected) as busy:
                owner.set_builder_chat_archived("busy-builder", archived=True)
            assert busy.value.code == "builder_chat_work_in_progress"
        finally:
            release.set()
            worker.join(5)
        campaign = owner.create_guided_campaign_draft(
            operation_id="new-campaign",
            display_name="Kept campaign",
            builder_session_id="interview",
            builder_provider_id="test",
            builder_model_id="test/model",
        )
        with pytest.raises(CommandRejected, match="Archive the campaign"):
            owner.set_builder_chat_archived(campaign.builder_session_id, archived=True)
        assert owner.session_context("busy-builder").session.status is SessionStatus.ACTIVE


def test_archive_rejects_provider_admission_before_tools_start(tmp_path: Path) -> None:
    with AuthorityOwner.open(tmp_path / "authority.sqlite3", "pending-archive-owner") as owner:
        owner.create_builder_chat(
            provider_id="test", model_id="test/model", session_id="pending-builder"
        )
        loop = CampaignSessionLoop(owner)
        # The provider loop reserves this set before waiting for its session-work lane.
        loop._pending.add("pending-builder")  # noqa: SLF001
        with pytest.raises(CommandRejected) as pending:
            loop.set_builder_chat_archived("pending-builder", archived=True)
        assert pending.value.code == "builder_chat_work_in_progress"
        assert owner.session_context("pending-builder").session.status is SessionStatus.ACTIVE


def test_campaign_archive_preserves_idle_open_turn_through_restart(tmp_path: Path) -> None:
    """A saved next-player turn is resumable state, not a running response."""
    database = tmp_path / "campaign-archive.sqlite3"
    with AuthorityOwner.open(
        database, "campaign-archive-owner", disconnect_grace_seconds=0.1
    ) as owner:
        owner.create_campaign(
            CampaignCreate(
                display_name="Idle saved campaign",
                writer=WriterRole.DM,
                package_lock=PackageLock(entries=()),
                campaign_id="idle-campaign",
                turn_id="saved-turn",
            )
        )
        owner.create_agent_session(
            campaign_id="idle-campaign",
            role=WriterRole.DM,
            provider_id="test",
            model_id="test/model",
            session_id="saved-gm",
        )
        owner.append_agent_message(
            session_id="saved-gm",
            message_id="saved-message",
            kind=SessionMessageKind.USER,
            content={"text": "Stay by the platform."},
        )
        owner.update_agent_session_reasoning("saved-gm", effort="high", once="low")
        owner.start_narration_attempt(
            campaign_id="idle-campaign",
            attempt_id="live-response",
            turn_id="saved-turn",
            cancel=lambda: None,
        )
        with pytest.raises(CommandRejected, match="active work"):
            owner.archive_campaign(
                operation_id="busy-archive",
                campaign_id="idle-campaign",
                expected_revision=0,
                designated_session_id="saved-gm",
            )
        owner.interrupt_narration("idle-campaign", "live-response")
        before = owner.session_context("saved-gm")
        assert owner.current_open_turn("idle-campaign") == "saved-turn"
        owner.admit_archive(campaign_id="idle-campaign", expected_revision=0)
        archived = owner.archive_campaign(
            operation_id="idle-archive",
            campaign_id="idle-campaign",
            expected_revision=0,
            designated_session_id="saved-gm",
        )
        assert archived.lifecycle is LifecycleState.ARCHIVED
        assert owner.current_open_turn("idle-campaign") == "saved-turn"
    with AuthorityOwner.open(database, "restored-campaign-owner") as owner:
        restored = owner.restore_archived_campaign(
            operation_id="restore-idle-campaign",
            campaign_id="idle-campaign",
            expected_revision=archived.resulting_revision,
            session_id="saved-gm",
        )
        assert restored.lifecycle is LifecycleState.ACTIVE
        assert owner.current_open_turn("idle-campaign") == "saved-turn"
        assert owner.session_context("saved-gm") == before


def test_archive_reopens_after_builder_to_gm_handoff(tmp_path: Path) -> None:
    """Archiving play also stops its retained Interview, whose Plan remains valid."""
    from tests.test_alpha_f4_generic_sessions import _prepare_handoff

    database = tmp_path / "handoff-archive.sqlite3"
    with AuthorityOwner.open(database, "handoff-archive-owner") as owner:
        request = _prepare_handoff(owner, suffix="archive-reopen")
        campaign_id = request["campaign_id"]
        gm_id = request["dm_session_id"]
        builder_id = request["builder_session_id"]
        owner.complete_turn(campaign_id, request["completed_turn_id"])
        owner.handoff_builder_to_dm(**request)
        owner.open_turn(campaign_id, "next-player-turn")
        builder_before = owner.session_context(builder_id)
        gm_before = owner.session_context(gm_id)
        archived = owner.archive_campaign(
            operation_id="archive-handed-off-campaign", campaign_id=campaign_id,
            expected_revision=2, designated_session_id=gm_id,
        )
        assert owner.session_context(builder_id).session.status is SessionStatus.STOPPED
        assert owner.session_context(gm_id).session.status is SessionStatus.STOPPED
    with AuthorityOwner.open(database, "handoff-archive-reopened") as owner:
        assert owner.current_open_turn(campaign_id) == "next-player-turn"
        assert owner.session_context(builder_id).messages == builder_before.messages
        owner.restore_archived_campaign(
            operation_id="restore-handed-off-campaign", campaign_id=campaign_id,
            expected_revision=archived.resulting_revision, session_id=gm_id,
        )
        assert owner.session_context(gm_id) == gm_before
    with AuthorityOwner.open(database, "handoff-restore-reopened") as owner:
        assert owner.session_context(builder_id).messages == builder_before.messages
        assert owner.session_context(gm_id) == gm_before
