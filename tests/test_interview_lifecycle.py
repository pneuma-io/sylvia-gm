"""An unfinished Campaign Interview can be managed before it has any gameplay state."""

import sqlite3

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import PackageLock
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict, StaleRevision
from sylvia_gm.domain.session import SessionMessageKind


def new_draft(owner):
    return owner.create_guided_campaign_draft(
        operation_id="new-interview", display_name="Unfinished campaign",
        builder_session_id="interview", builder_provider_id="test", builder_model_id="test",
    )


def test_ready_customization_draft_retry_preserves_one_interview_and_its_model(tmp_path):
    database = tmp_path / "customization-retry.db"
    request = {
        "operation_id": "ready-custom-one-draft",
        "display_name": "New campaign interview",
        "builder_session_id": "session-guided-custom-one",
        "builder_provider_id": "original-provider",
        "builder_model_id": "original-model",
    }
    with AuthorityOwner.open(database, "customization") as owner:
        draft = owner.create_guided_campaign_draft(**request)
        before = owner.session_context(draft.builder_session_id)
        assert owner.create_guided_campaign_draft(**request).campaign_id == draft.campaign_id
        assert owner.session_context(draft.builder_session_id) == before
        assert len(owner.campaign_index()) == 1
    with AuthorityOwner.open(database, "customization-reopened") as owner:
        assert owner.create_guided_campaign_draft(**request).campaign_id == draft.campaign_id
        assert owner.session_context(draft.builder_session_id) == before
        with pytest.raises(IdempotencyConflict):
            owner.create_guided_campaign_draft(
                **{**request, "builder_provider_id": "changed-provider"}
            )
        assert owner.session_context(draft.builder_session_id) == before
        assert len(owner.campaign_index()) == 1


@pytest.mark.parametrize("manual_title", ["My campaign planning", "New campaign interview"])
def test_default_interview_title_follows_saved_name_and_respects_manual_rename(
    tmp_path, manual_title,
):
    database = tmp_path / "interview-title.db"
    with AuthorityOwner.open(database, "titles") as owner:
        draft = owner.create_guided_campaign_draft(
            operation_id="new-title", display_name="New campaign interview",
            builder_session_id="interview-title", builder_provider_id="test",
            builder_model_id="test",
        )
        finish_interview(owner, draft)
        assert owner.campaign_index()[0].display_name == "The restored campaign · Interview"
        assert owner.snapshot(draft.campaign_id).revision == 0
    with AuthorityOwner.open(database, "titles-reopen") as owner:
        assert owner.campaign_index()[0].display_name == "The restored campaign · Interview"
        owner.archive_campaign(
            operation_id="archive-title", campaign_id=draft.campaign_id,
            expected_revision=0, designated_session_id=draft.builder_session_id,
        )
        assert owner.campaign_index()[0].display_name == "The restored campaign · Interview"
        owner.restore_archived_campaign(
            operation_id="restore-title", campaign_id=draft.campaign_id,
            expected_revision=1, session_id=draft.builder_session_id,
        )
        owner.advance_guided_campaign_draft(
            operation_id="revised-name", campaign_id=draft.campaign_id,
            builder_session_id=draft.builder_session_id, expected_construction_revision=7,
            current_step="name", visible_answer="The silent relay",
            answer={"display_name": "The silent relay"},
        )
        assert owner.campaign_index()[0].display_name == "The silent relay · Interview"
        owner.rename_campaign(
            operation_id="manual-title", campaign_id=draft.campaign_id,
            display_name=manual_title, expected_revision=2,
        )
        assert owner.campaign_index()[0].display_name == manual_title
    with AuthorityOwner.open(database, "manual-title-reopen") as owner:
        assert owner.campaign_index()[0].display_name == manual_title


def finish_interview(owner, draft):
    for revision, (step, answer) in enumerate((
        ("ruleset", {"candidate_id": None}),
        ("setting", {"setting_id": None}),
        ("plan", {"plan_id": None}),
        ("name", {"display_name": "The restored campaign"}),
        ("participants", {"player_name": "Investigator"}),
        ("boundaries_preferences", {"boundaries": "", "preferences": ""}),
        ("specific_requests", {"no_additional_requests": True}),
    )):
        owner.advance_guided_campaign_draft(
            operation_id=f"choose-{step}", campaign_id=draft.campaign_id,
            builder_session_id=draft.builder_session_id,
            expected_construction_revision=revision, current_step=step,
            visible_answer=step, answer=answer,
        )
    owner.append_agent_message(
        session_id=draft.builder_session_id, message_id="ready",
        kind=SessionMessageKind.USER,
        content={"text": "Ready to Begin", "interview_step": "ready",
                 "interview_value": {"action": "ready_to_begin"}},
    )


def test_unfinished_interview_rename_archive_restore_and_bind_survive_restart(tmp_path):
    database = tmp_path / "lifecycle.db"
    with AuthorityOwner.open(database, "owner") as owner:
        draft = new_draft(owner)
        original = owner.session_context(draft.builder_session_id)
        renamed = owner.rename_campaign(
            operation_id="rename", campaign_id=draft.campaign_id,
            display_name="A later adventure", expected_revision=0,
        )
        assert renamed.resulting_revision == 1
        assert owner.session_context(draft.builder_session_id) == original
        assert owner.rename_campaign(
            operation_id="rename", campaign_id=draft.campaign_id,
            display_name="A later adventure", expected_revision=0,
        ) == renamed
        with pytest.raises(StaleRevision):
            owner.rename_campaign(
                operation_id="stale-rename", campaign_id=draft.campaign_id,
                display_name="Stale", expected_revision=0,
            )
        archived = owner.archive_campaign(
            operation_id="archive", campaign_id=draft.campaign_id,
            expected_revision=1, designated_session_id=draft.builder_session_id,
        )
        assert archived.lifecycle.value == "archived"
        assert owner.session_context(draft.builder_session_id).session.status.value == "stopped"
    with AuthorityOwner.open(database, "reopened") as owner:
        restored = owner.restore_archived_campaign(
            operation_id="restore", campaign_id=draft.campaign_id,
            expected_revision=2, session_id=draft.builder_session_id,
        )
        assert restored.resulting_revision == 3
        assert owner.session_context(draft.builder_session_id).messages == original.messages
        finish_interview(owner, draft)
        with sqlite3.connect(database) as connection:
            assert connection.execute("SELECT COUNT(*) FROM turns").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM campaign_locks").fetchone()[0] == 0
        request = {
            "operation_id": "bind", "campaign_id": draft.campaign_id,
            "display_name": "The restored campaign", "package_lock": PackageLock(entries=()),
            "turn_id": "construction", "builder_session_id": draft.builder_session_id,
        }
        bound = owner.bind_guided_campaign_basis(**request)
        assert bound.resulting_revision == 3
        assert owner.bind_guided_campaign_basis(**request) == bound
        assert owner.current_package_lock(draft.campaign_id).created_revision == 3
    with AuthorityOwner.open(database, "bound-reopened") as owner:
        assert owner.snapshot(draft.campaign_id).revision == 3
        assert owner.current_open_turn(draft.campaign_id) == "construction"


def test_unfinished_campaign_trash_restore_and_purge_retain_independent_builder(tmp_path):
    database = tmp_path / "purge.db"
    with AuthorityOwner.open(database, "owner") as owner:
        draft = new_draft(owner)
        chat = owner.start_interview_builder_chat(
            operation_id="build-rule", campaign_id=draft.campaign_id,
            parent_session_id=draft.builder_session_id, expected_construction_revision=0,
            purpose="ruleset", visible_answer="New Ruleset", child_session_id="rule-builder",
        )
        saved = owner.session_context(chat.child_session_id)
        for operation, revision in (("trash", 0), ("trash-again", 2)):
            owner.trash_campaign(
                operation_id=operation, campaign_id=draft.campaign_id,
                expected_revision=revision, designated_session_id=draft.builder_session_id,
            )
            if operation == "trash":
                owner.restore_trashed_campaign(
                    operation_id="restore", campaign_id=draft.campaign_id, expected_revision=1,
                )
        owner.purge_campaign(
            operation_id="purge", campaign_id=draft.campaign_id, expected_revision=3,
            confirmation=f"permanently purge {draft.campaign_id}",
        )
        with pytest.raises(CommandRejected):
            owner.session_context(draft.builder_session_id)
        assert owner.session_context(chat.child_session_id) == saved
    with AuthorityOwner.open(database, "reopened") as owner:
        assert owner.session_context(chat.child_session_id) == saved
