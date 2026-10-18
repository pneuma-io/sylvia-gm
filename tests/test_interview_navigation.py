"""Back saves the Interview cursor without resubmitting or discarding any answers."""

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict, StaleRevision
from sylvia_gm.provider._base import FakeProvider, ModelInfo
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness


def saved_state(owner, session_id):
    return next(
        message.content for message in reversed(owner.session_context(session_id).messages)
        if message.content.get("kind") == "campaign_interview_state"
    )


def answered_interview(owner):
    draft = owner.create_guided_campaign_draft(
        operation_id="new", display_name="Synthetic navigation",
        builder_session_id="interview", builder_provider_id="test", builder_model_id="test",
    )
    for revision, (step, answer) in enumerate((
        ("ruleset", {"candidate_id": "synthetic-ruleset"}),
        ("setting", {"setting_id": None}),
        ("plan", {"plan_id": None}),
        ("name", {"display_name": "Keep my campaign name"}),
    )):
        owner.advance_guided_campaign_draft(
            operation_id=f"answer-{step}", campaign_id=draft.campaign_id,
            builder_session_id=draft.builder_session_id,
            expected_construction_revision=revision, current_step=step,
            visible_answer=step, answer=answer,
        )
    return draft


def back_request(draft, target="plan", revision=4):
    return {
        "operation_id": f"navigate-{target}-{revision}", "campaign_id": draft.campaign_id,
        "builder_session_id": draft.builder_session_id,
        "expected_construction_revision": revision, "target_step": target,
    }


def test_interview_target_identity_survives_owner_restart(tmp_path):
    database = tmp_path / "target-identity.sqlite3"
    target = {
        "plan_id": "synthetic-plan", "plan_sha256": "a" * 64,
        "ruleset": {"id": "rules", "version": "1", "content_sha256": "b" * 64},
        "setting": {"id": "setting", "version": "1", "content_sha256": "c" * 64},
    }
    with AuthorityOwner.open(database, "target-save") as owner:
        draft = answered_interview(owner)
        result = owner.advance_guided_campaign_draft(
            operation_id="save-party-target", campaign_id=draft.campaign_id,
            builder_session_id=draft.builder_session_id, expected_construction_revision=4,
            current_step="participants", visible_answer="Synthetic party",
            answer={"archetype_id": "pc", "companion_ids": [], "target_identity": target},
        )
        original = owner.session_context(draft.builder_session_id)
    with AuthorityOwner.open(database, "target-reopen") as owner:
        reopened = owner.session_context(draft.builder_session_id)
        assert reopened.messages == original.messages
        answer = next(message for message in reopened.messages
                      if message.message_id == result.answer_message_id)
        assert answer.content["interview_value"]["target_identity"] == target


def test_back_then_builder_launch_keeps_answers_and_saved_place_after_restart(tmp_path):
    database = tmp_path / "navigation.sqlite3"
    with AuthorityOwner.open(database, "navigation") as owner:
        draft = answered_interview(owner)
        before = owner.session_context(draft.builder_session_id)
        answers = saved_state(owner, draft.builder_session_id)["answers"]
        owner.navigate_guided_campaign_draft(**back_request(draft, "name"))
        result = owner.navigate_guided_campaign_draft(**back_request(draft, "plan", 5))
        assert result.current_step == "plan"
        assert result.construction_revision == 6
        after = owner.session_context(draft.builder_session_id)
        assert after.messages[:len(before.messages)] == before.messages
        assert len(after.messages) == len(before.messages) + 2
        state = saved_state(owner, draft.builder_session_id)
        assert state["answers"] == answers
        question = next(m for m in after.messages
                        if m.message_id == state["current_question_message_id"])
        assert question.content["interview_step"] == "plan"

    with AuthorityOwner.open(database, "reopened") as owner:
        assert saved_state(owner, draft.builder_session_id) == state
        launch = owner.start_interview_builder_chat(
            operation_id="create-plan", campaign_id=draft.campaign_id,
            parent_session_id=draft.builder_session_id, expected_construction_revision=6,
            purpose="plan", visible_answer="Create New Campaign Plan", child_session_id="plan-chat",
        )
        assert launch.status == "independent"
        assert owner.session_context(draft.builder_session_id).messages == after.messages
        child = owner.session_context(launch.child_session_id)
        assert child.session.parent_session_id is None
        assert child.messages[0].content["selections"]["name"]["value"] == {
            "display_name": "Keep my campaign name",
        }
        # Continue with unchanged answers revisits saved questions through the same cursor route.
        owner.navigate_guided_campaign_draft(**back_request(draft, "name", 6))
        resumed = owner.navigate_guided_campaign_draft(**back_request(draft, "participants", 7))
        assert resumed.current_step == "participants"
        assert saved_state(owner, draft.builder_session_id)["answers"] == answers


def test_back_retry_is_exact_and_stale_or_forward_requests_do_not_write(tmp_path):
    with AuthorityOwner.open(tmp_path / "guards.sqlite3", "guards") as owner:
        draft = answered_interview(owner)
        request = back_request(draft)
        result = owner.navigate_guided_campaign_draft(**request)
        assert owner.navigate_guided_campaign_draft(**request) == result
        retained = owner.session_context(draft.builder_session_id)
        with pytest.raises(IdempotencyConflict):
            owner.navigate_guided_campaign_draft(**{**request, "target_step": "ruleset"})
        with pytest.raises(StaleRevision):
            owner.navigate_guided_campaign_draft(**back_request(draft, "setting", 4))
        for step in ("review", "ruleset_contribution", "unknown"):
            with pytest.raises(CommandRejected):
                owner.navigate_guided_campaign_draft(**back_request(draft, step, 5))
        assert owner.session_context(draft.builder_session_id) == retained
        with pytest.raises(StaleRevision):
            owner.start_interview_builder_chat(
                operation_id="stale-launch", campaign_id=draft.campaign_id,
                parent_session_id=draft.builder_session_id, expected_construction_revision=4,
                purpose="plan", visible_answer="Create New Campaign Plan", child_session_id="stale",
            )
        assert owner.builder_chat_index() == ()


def test_navigation_transport_saves_no_rules_cursor_and_launches_independent_builder(tmp_path):
    provider = FakeProvider()
    provider.model_info = ModelInfo(
        id="test.transport/model0", name="Navigation", provider_name="Test",
        provider_key="test.transport", provider_mode="test",
        context_window=65_536, max_output_tokens=4_096,
    )
    harness = _OwnerHarness(tmp_path / "transport.sqlite3", provider)
    try:
        draft = harness.request("new", "create_guided_campaign_draft", {
            "operation_id": "new", "display_name": "Navigation",
            "provider_id": "test.transport", "model_id": "test.transport/model0",
        })
        base = {key: draft[key] for key in ("campaign_id", "builder_session_id")}
        harness.request("answer", "advance_guided_campaign_draft", {
            **base, "operation_id": "answer", "expected_construction_revision": 0,
            "current_step": "ruleset", "visible_answer": "No Ruleset",
            "answer": {"candidate_id": None},
        })
        navigated = harness.request("back", "navigate_guided_campaign_draft", {
            **base, "operation_id": "back", "expected_construction_revision": 1,
            "target_step": "ruleset",
        })
        assert navigated["construction_revision"] == 2
        assert navigated["current_step"] == "ruleset"
        launched = harness.request("builder", "start_guided_creation_subflow", {
            "operation_id": "builder", "campaign_id": draft["campaign_id"],
            "parent_session_id": draft["builder_session_id"],
            "expected_construction_revision": 2, "purpose": "ruleset",
            "visible_answer": "Create New Ruleset",
        })
        assert launched["status"] == "independent"
    finally:
        harness.close()


def test_changed_answer_prevents_navigation_to_detached_later_answers(tmp_path):
    with AuthorityOwner.open(tmp_path / "changed.sqlite3", "changed") as owner:
        draft = answered_interview(owner)
        owner.navigate_guided_campaign_draft(**back_request(draft, "ruleset"))
        owner.advance_guided_campaign_draft(
            operation_id="change-ruleset", campaign_id=draft.campaign_id,
            builder_session_id=draft.builder_session_id, expected_construction_revision=5,
            current_step="ruleset", visible_answer="Different Ruleset",
            answer={"candidate_id": "different-ruleset"},
        )
        state = saved_state(owner, draft.builder_session_id)
        assert state["current_step"] == "setting"
        assert set(state["answers"]) == {"ruleset"}
        with pytest.raises(CommandRejected):
            owner.navigate_guided_campaign_draft(**back_request(draft, "name", 6))
