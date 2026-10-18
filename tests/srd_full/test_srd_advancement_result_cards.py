"""Advancement and recovery cards distinguish proposed, earned and applied changes."""

from pathlib import Path

import pytest

from sylvia_gm.application.authority import AuthorityOwner
from tests.srd_full.test_srd_action_budget import SRD, _run, _state
from tests.srd_full.test_srd_advancement import award, build, command, create, resolve


def _rows(card, section="summary"):
    return {row["label"]: row["value"] for row in card[section]}


@pytest.mark.parametrize("sides,constitution", [(4, 2), (6, 2), (8, 2), (10, 2), (12, -10)])
def test_short_rest_card_uses_selected_die_and_actual_capped_recovery(sides, constitution):
    state = _state()
    state[f"{SRD}.state"] = {}
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.entities"]["hero"].update(
        name="Budget Hero", hp=19, max_hp=20, hit_die_size=sides,
        constitution_modifier=constitution, resources={"hit_die": 2},
    )
    _run("begin_srd_rest", state, target_state="hero", kind="short", activity="light")
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    _run("complete_srd_rest", state, target_state="hero")
    result = _run("take_srd_short_rest_hit_die", state, target_state="hero").result
    card = result.get("result_card")
    assert card is not None
    roll = result["context"]["_short_rest_hit_die_roll"]
    assert card["equation"] == {
        "die_sides": sides, "roll": roll["total"], "modifier": constitution,
        "total": roll["total"] + constitution,
    }
    assert _rows(card)["HP restored"] == 1
    assert _rows(card)["Hit Dice remaining"] == 1
    assert _rows(card, "details")["HP before"] == 19
    assert _rows(card, "details")["HP after"] == 20


@pytest.mark.parametrize("option,hp_method", [("apply", "fixed"), ("apply", "roll"),
                                           ("cancel", "roll")])
def test_award_and_level_choice_cards_only_report_committed_gains(
    tmp_path: Path, option, hp_method,
):
    with AuthorityOwner.open(tmp_path / "levels.sqlite3", owner_id="test") as owner:
        create(owner)
        award_result = award(owner)
        card = award_result.result.get("result_card")
        assert card is not None
        assert _rows(card)["Eligible through level"] == 2
        assert _rows(card)["Current level"] == 1
        before = owner.snapshot("advancement")
        proposal = build(owner)
        proposal["hp_method"] = hp_method
        waiting = owner.execute_action(command(owner, "level_up_srd_character", proposal, "level"))
        resolve(owner, waiting, option)
        after = owner.snapshot("advancement")
        receipt = next(a for a in after.actions if a.tool_name == "level_up_srd_character")
        card = receipt.result.get("result_card")
        assert card is not None
        rows = _rows(card)
        assert rows["Level"] == after.participants[0].data["level"]
        if option == "cancel":
            assert card["title"] == "Level-up cancelled"
            assert card.get("equation") is None and "Maximum HP gained" not in rows
            assert after.state == before.state
        else:
            assert card["title"] == "Level gained"
            previous = before.state[f"{SRD}.entities"]["hero"]["max_hp"]
            maximum = after.state[f"{SRD}.entities"]["hero"]["max_hp"]
            assert rows["Maximum HP gained"] == maximum - previous
            if hp_method == "roll":
                assert card["equation"]["die_sides"] == 10
                assert card["equation"]["total"] == maximum - previous
            else:
                assert card.get("equation") is None
