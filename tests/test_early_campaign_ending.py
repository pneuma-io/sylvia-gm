"""Controlled Plan progress proves early-ending storage, never GM narrative judgment."""

import copy
import json
from pathlib import Path

import pytest

from sylvia_gm.agent._provider_session import _dm_system_prompt
from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
from sylvia_gm.application.authority import AuthorityOwner, _read_structured_campaign_plan
from sylvia_gm.domain._content_schema_plan import EXAMPLE
from sylvia_gm.domain.authority import ProseChannel
from sylvia_gm.domain.campaign_ending import (
    check_ending_progress,
    required_story_items,
    resolved_progress,
)
from sylvia_gm.domain.content_validation import validate_content_shape
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionMessageKind
from tests.test_alpha_f4_generic_sessions import _prepare_handoff
from tests.test_campaign_plan_act_completion import validate

INPUT = Path(__file__).resolve().parents[1] / "campaign-plans/rift-awakening/plan.json"


def rift_plan():
    """Load the immutable actual selected manuscript, with an explicit isolated proposal."""
    plan = json.loads(INPUT.read_text(encoding="utf-8"))
    next(item for item in plan["ending"]["outcomes"] if item["id"] == "life-ended")[
        "requires_all_acts"
    ] = False
    return plan


def test_original_rift_reproduces_the_binding_independent_rejection():
    plan = json.loads(INPUT.read_text(encoding="utf-8"))
    next(item for item in plan["ending"]["outcomes"] if item["id"] == "life-ended").pop(
        "requires_all_acts", None
    )
    with pytest.raises(ValueError, match="required acts"):
        check_ending_progress(
            plan, (), ({"plan_item_id": "ending:life-ended", "status": "resolved"},)
        )


def test_proposed_early_outcome_format_and_progress_leave_story_unplayed():
    plan = copy.deepcopy(EXAMPLE)
    plan["ending"]["outcomes"][0]["requires_all_acts"] = False
    validate(plan)
    updates = ({"plan_item_id": "ending:signal-settled", "status": "resolved"},)
    check_ending_progress(plan, (), updates)
    assert required_story_items(plan).isdisjoint(resolved_progress(updates))


@pytest.mark.parametrize("value", ["false", 0, 1, None, [], {}])
def test_early_outcome_flag_rejects_non_boolean_data(value):
    plan = copy.deepcopy(EXAMPLE)
    plan["ending"]["outcomes"][0]["requires_all_acts"] = value
    with pytest.raises(ValueError):
        validate(plan)


@pytest.mark.parametrize("explicit", [False, True])
def test_ordinary_ending_still_rejects_missing_or_retired_required_progress(explicit):
    plan = copy.deepcopy(EXAMPLE)
    if explicit:
        plan["ending"]["outcomes"][0]["requires_all_acts"] = True
    validate(plan)
    for status in ["engaged", "deferred", "retired"]:
        progress = tuple(
            {"plan_item_id": item, "status": status} for item in required_story_items(plan)
        )
        with pytest.raises(ValueError, match="required acts"):
            check_ending_progress(
                plan, progress, ({"plan_item_id": "ending:signal-settled", "status": "resolved"},)
            )


def test_rift_ordinary_outcome_cannot_use_the_early_failure_exception():
    plan = rift_plan()
    validate_content_shape("campaign_plan", plan, file="plan.json")
    check_ending_progress(plan, (), ({"plan_item_id": "ending:life-ended", "status": "resolved"},))
    with pytest.raises(ValueError, match="required acts"):
        check_ending_progress(
            plan, (), ({"plan_item_id": "ending:stay-with-a-door", "status": "resolved"},)
        )
    with pytest.raises(ValueError, match="not declared"):
        check_ending_progress(plan, (), ({"plan_item_id": "ending:made-up", "status": "resolved"},))
    progress = tuple(
        {"plan_item_id": item, "status": "resolved"} for item in required_story_items(plan)
    )
    progress += ({"plan_item_id": "ending:stay-with-a-door", "status": "resolved"},)
    with pytest.raises(ValueError, match="Only one"):
        check_ending_progress(
            plan, progress, ({"plan_item_id": "ending:life-ended", "status": "resolved"},)
        )


def test_early_outcome_reaches_the_on_demand_read_and_gm_guidance():
    plan = rift_plan()
    ending = _read_structured_campaign_plan(plan, "ending")
    assert ending == plan["ending"]
    assert (
        next(item for item in ending["outcomes"] if item["id"] == "life-ended")["requires_all_acts"]
        is False
    )
    prompt = _dm_system_prompt(campaign_has_mechanics=True, tools_available=True)
    assert "requires_all_acts false" in prompt
    assert "leave unfinished acts, objectives and unearned awards unchanged" in prompt


@pytest.mark.parametrize("completed_acts", [0, 1, 2])
def test_early_ending_choice_and_unplayed_progress_survive_reopen(tmp_path, completed_acts):
    plan = copy.deepcopy(EXAMPLE)
    later = copy.deepcopy(plan["structure"][0])
    later.update(id="act-2", objectives=["later-work"])
    plan["structure"].append(later)
    objective = copy.deepcopy(plan["objectives"][0])
    objective["id"] = "later-work"
    plan["objectives"].append(objective)
    plan["ending"]["outcomes"].append(
        copy.deepcopy(
            next(item for item in rift_plan()["ending"]["outcomes"] if item["id"] == "life-ended")
        )
    )
    # The existing no-package helper uses neutral Setting references, the actual Rift ending
    # premise and controlled two-act progress. This does not bypass the separately recorded
    # Rift/Lite Setting gap or establish portable admission of that manuscript.
    plan.pop("bindings")
    database = tmp_path / "rift-early-ending.sqlite3"
    with AuthorityOwner.open(database, "early-ending-test", disconnect_grace_seconds=1.0) as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="init")
        request = _prepare_handoff(owner, suffix="early-rift", plan_content={"campaign_plan": plan})
        campaign, session = str(request["campaign_id"]), str(request["dm_session_id"])
        owner.complete_turn(campaign, str(request["completed_turn_id"]))
        owner.handoff_builder_to_dm(**request)
        turn = owner.current_open_turn(campaign)
        if turn is None:
            owner.open_turn(campaign, "early-turn")
            turn = "early-turn"
        owner.append_agent_message(
            session_id=session,
            message_id="early-input",
            kind=SessionMessageKind.USER,
            content={"text": "Controlled fixture ending basis."},
        )
        before = owner._store.snapshot(campaign)

        def record(update_id, ids):
            return owner.update_campaign_file(
                session_id=session,
                update_id=update_id,
                updates=tuple(
                    {
                        "operation": "update_plan_progress",
                        "plan_item_id": identity,
                        "progress_status": "resolved",
                        "progress_summary": "PRIVATE controlled fixture.",
                    }
                    for identity in ids
                ),
                basis_message_ids=("early-input",),
                turn_id=turn,
            )

        with pytest.raises(CommandRejected, match="required acts"):
            record("premature-success", ["ending:signal-settled"])
        assert owner._store.snapshot(campaign) == before
        controlled_completed = {
            identity
            for act in plan["structure"][:completed_acts]
            for identity in (act["id"], *act["objectives"])
        }
        if controlled_completed:
            record("controlled-prior-progress", sorted(controlled_completed))
        # This controlled fixture declares eligibility; it is not inferred from player prose.
        owner.record_campaign_plan_progress(
            session_id=session,
            update_id="early-ending",
            plan_item_id="ending:life-ended",
            progress_status="resolved",
            summary="PRIVATE controlled early-ending fixture.",
            basis_message_ids=("early-input",),
            turn_id=turn,
        )
        assert owner.session_history_page(session).campaign_ending is None
        owner.start_narration_attempt(
            campaign_id=campaign, attempt_id="early-narration", turn_id=turn, cancel=lambda: None
        )
        owner.emit_narration_token(
            campaign,
            "early-narration",
            ProseChannel.TALK,
            "Controlled closing-scene fixture; no unplayed local victory is asserted.",
        )
        events = owner.complete_narration_attempt(campaign, "early-narration")
        anchor = owner.append_session_canonical_references(session, events)[-1].message_id
        record("ending-refresh", ["ending:life-ended"])
        ending = owner.session_history_page(session, limit=1).campaign_ending
        assert ending["ending_id"] == "life-ended"
        assert ending["after_message_id"] == anchor
        assert ending["act_number"] == (2 if completed_acts == 2 else None)
        assert ending["open_world"] is False and ending["choice"] is None
        assert "PRIVATE" not in json.dumps(dict(ending))
        snapshot = owner._store.snapshot(campaign)
        progress = resolved_progress(snapshot.continuity.plan_progress)
        assert required_story_items(plan) & progress == controlled_completed
        if completed_acts == 0:
            assert owner.session_history_page(session).act_markers == ()
        elif completed_acts == 1:
            assert [
                (item["act_number"], item["status"])
                for item in owner.session_history_page(session).act_markers
            ] == [(1, "complete")]
        assert snapshot.state == before.state
        assert snapshot.participants == before.participants
        assert snapshot.actions == before.actions
        selection = {"ending_id": "life-ended", "choice": "finish"}
        with pytest.raises(CommandRejected, match="does not support"):
            owner.campaign_ending_choice_text(
                session, "invalid-continue", {"ending_id": "life-ended", "choice": "continue"}
            )
        CampaignSessionLoop(owner).run(
            SessionRunRequest(
                session_id=session,
                turn_id=turn,
                attempt_id="early-choice-attempt",
                message_id="early-choice",
                user_text="",
                campaign_ending_choice=selection,
            ),
            lambda _: iter(()),
        )
        expected = owner.session_history_page(session).campaign_ending
        assert expected["choice"] == "finish"
        expected_progress = owner._store.snapshot(campaign).continuity.plan_progress
        expected_plan = owner.accepted_campaign_plan(campaign)
    with AuthorityOwner.open(database, "early-ending-reopen") as reopened:
        assert reopened.session_history_page(session, limit=1).campaign_ending == expected
        assert reopened._store.snapshot(campaign).continuity.plan_progress == expected_progress
        assert reopened.accepted_campaign_plan(campaign) == expected_plan
        assert reopened._store.snapshot(campaign).state == before.state
        assert reopened._store.snapshot(campaign).participants == before.participants
