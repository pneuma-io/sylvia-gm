"""Authored endings, earned completion and real saved user choices share the owner path."""

import copy
import json
from pathlib import Path

import pytest

from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
from sylvia_gm.application.authority import AuthorityOwner, _read_structured_campaign_plan
from sylvia_gm.domain._content_schema_plan import EXAMPLE
from sylvia_gm.domain.authority import ProseChannel
from sylvia_gm.domain.campaign_ending import check_ending_progress, required_story_items
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionMessageKind
from tests.test_alpha_f4_generic_sessions import _prepare_handoff
from tests.test_campaign_plan_act_completion import validate


def test_ending_format_and_ongoing_exception_preserve_finite_act_rules():
    plan = copy.deepcopy(EXAMPLE)
    validate(plan)
    final = _read_structured_campaign_plan(plan, "structure/act-1")
    assert final["campaign_ending"] == plan["ending"]
    plan["ending"]["outcomes"][0]["open_world"] = "yes"
    with pytest.raises(ValueError):
        validate(plan)
    plan["ending"] = {"mode": "ongoing", "note": "No conclusion; the relay world stays open."}
    with pytest.raises(ValueError, match="ongoing final act"):
        validate(plan)
    plan["structure"][0]["objectives"] = []
    plan["objectives"] = []
    validate(plan)
    assert _read_structured_campaign_plan(plan, "structure/act-1")["required_objectives"] == []
    with pytest.raises(ValueError, match="ongoing"):
        check_ending_progress(plan, (), ({"plan_item_id": "act-1", "status": "resolved"},))
    earlier = copy.deepcopy(plan["structure"][0])
    earlier["id"] = "earlier"
    plan["structure"].insert(0, earlier)
    with pytest.raises(ValueError, match="objectives"):
        validate(plan)


@pytest.mark.parametrize("continuable,after_story,choice", [
    (True, "The operator builds a life among the people met in play.", "continue"),
    (False, "The operator returns home, remembering the friends made here.", "finish"),
    (True, None, "finish"),
])
def test_saved_ending_choice_is_earned_private_safe_and_survives_reopen(
    tmp_path: Path, continuable: bool, after_story: str | None, choice: str,
):
    plan = copy.deepcopy(EXAMPLE)
    outcome = plan["ending"]["outcomes"][0]
    outcome.update(open_world=continuable, after_story=after_story)
    # This no-package owner fixture exercises saved narrative state, not portable Plan admission.
    # Format/real package binding are checked separately above and in the bundled Plan checks.
    plan.pop("bindings")
    database = tmp_path / "ending.sqlite3"
    with AuthorityOwner.open(database, "ending-test", disconnect_grace_seconds=1.0) as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="init")
        request = _prepare_handoff(owner, suffix="ending", plan_content={"campaign_plan": plan})
        campaign = str(request["campaign_id"])
        session = str(request["dm_session_id"])
        owner.complete_turn(campaign, str(request["completed_turn_id"]))
        owner.handoff_builder_to_dm(**request)
        turn = owner.current_open_turn(campaign)
        if turn is None:
            owner.open_turn(campaign, "ending-turn")
            turn = "ending-turn"
        owner.append_agent_message(session_id=session, message_id="ending-input",
                                   kind=SessionMessageKind.USER, content={"text": "Report back."})
        progress_id = "ending:signal-settled"

        def record(identity: str, suffix: str = ""):
            owner.update_campaign_file(
                session_id=session, update_id=f"progress-{identity}{suffix}",
                updates=({"operation": "update_plan_progress", "plan_item_id": identity,
                          "progress_status": "resolved", "progress_summary": "PRIVATE result."},),
                basis_message_ids=("ending-input",), turn_id=turn,
            )

        assert owner.session_history_page(session).campaign_ending is None
        with pytest.raises(CommandRejected, match="required acts"):
            record(progress_id)
        owner.update_campaign_file(
            session_id=session, update_id="complete-story-and-ending",
            updates=tuple({"operation": "update_plan_progress", "plan_item_id": identity,
                           "progress_status": "resolved", "progress_summary": "PRIVATE result."}
                          for identity in [progress_id, *sorted(required_story_items(plan))]),
            basis_message_ids=("ending-input",), turn_id=turn,
        )
        assert owner.session_history_page(session).campaign_ending is None
        owner.start_narration_attempt(campaign_id=campaign, attempt_id="ending-narration",
                                      turn_id=turn, cancel=lambda: None)
        owner.emit_narration_token(campaign, "ending-narration", ProseChannel.TALK,
                                   "The operator receives your report and thanks you.")
        events = owner.complete_narration_attempt(campaign, "ending-narration")
        anchor = owner.append_session_canonical_references(session, events)[-1].message_id
        record(progress_id, "-refresh")
        ending = owner.session_history_page(session, limit=1).campaign_ending
        assert ending["after_message_id"] == anchor
        assert ending["choice"] is None and ending["open_world"] is continuable
        assert "PRIVATE" not in json.dumps(dict(ending))
        assert "operator builds" not in json.dumps(dict(ending))
        selection = {"ending_id": outcome["id"], "choice": choice}
        if not continuable:
            with pytest.raises(CommandRejected, match="does not support"):
                owner.campaign_ending_choice_text(session, "bad-choice", {
                    "ending_id": outcome["id"], "choice": "continue",
                })
        observed = []

        def provider(provider_input):
            observed.append(provider_input.history)
            return iter(())

        # Real session admission saves the structured click and its OOC instruction together.
        CampaignSessionLoop(owner).run(SessionRunRequest(
            session_id=session, turn_id=turn, attempt_id="choice-attempt", message_id="choice",
            user_text="", campaign_ending_choice=selection,
        ), provider)
        saved = next(item for item in owner.session_context(session).messages
                     if item.message_id == "choice")
        assert saved.content["campaign_ending_choice"] == selection
        assert saved.content["text"] == ""
        assert saved.content["ooc_text"].startswith(
            "Continue adventuring" if choice == "continue" else "Finish the story"
        )
        assert observed and any(item.message_id == "choice" for item in observed[0].messages)
        expected = owner.session_history_page(session).campaign_ending
        assert expected["choice"] == choice
        with pytest.raises(CommandRejected, match="already saved"):
            owner.campaign_ending_choice_text(session, "second-click", selection)
        owner.append_agent_message(session_id=session, message_id="later-ooc",
                                   kind=SessionMessageKind.USER,
                                   content={"text": "OOC: I want to change that future."})
    with AuthorityOwner.open(database, "ending-reopen") as reopened:
        assert reopened.session_history_page(session, limit=1).campaign_ending == expected


def test_other_outcomes_do_not_create_multiple_completions():
    plan = copy.deepcopy(EXAMPLE)
    another = copy.deepcopy(plan["ending"]["outcomes"][0])
    another["id"] = "other"
    plan["ending"]["outcomes"].append(another)
    progress = [{"plan_item_id": item, "status": "resolved"}
                for item in required_story_items(plan)]
    check_ending_progress(plan, progress, ({"plan_item_id": "ending:other", "status": "resolved"},))
    progress.append({"plan_item_id": "ending:signal-settled", "status": "resolved"})
    with pytest.raises(ValueError, match="Only one"):
        check_ending_progress(plan, progress, (
            {"plan_item_id": "ending:other", "status": "resolved"},
        ))
