"""Unarmed attacks must affect ordinary creature entities without fake participant files."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.test_srd_action_budget import _run
from tests.test_srd_attack_budget import SRD, _combat_case


def _case():
    state, data = _combat_case()
    data["size_rank"] = 2
    state[f"{SRD}.entities"]["guard"].update(
        size_rank=2, speed=30, saving_throw_modifiers={"strength": 2, "dexterity": 1},
    )
    return state, data


def _strike(state, data, effect, **choices):
    return _run("resolve_unarmed_strike", state, participant_data=data, **{
        "actor": "hero", "actor_state": "hero", "target_state": "guard", "effect": effect,
        "target_save_ability": "strength", "target_forego_save": False, "hand_free": True,
        "distance": 5, "advantage": False, "disadvantage": False,
        "actor_can_see_target": True, "target_can_see_actor": True,
        "target_is_charmer": False, "fear_source_in_sight": False, **choices,
    })


@pytest.mark.parametrize("effect", ["damage", "grapple", "shove_prone", "shove_push"])
def test_unarmed_effect_accepts_npc_target_and_records_its_actual_roll(effect):
    state, data = _case()
    receipt = _strike(state, data, effect)
    assert receipt.result["status"] == "committed"
    context = receipt.result["context"]
    card = receipt.result["result_card"]
    assert card["subtitle"] == ("Attack roll" if effect == "damage" else "Saving throw")
    assert 1 <= card["equation"]["roll"] <= 20
    if effect != "damage":
        assert card["equation"]["modifier"] == 2
    target = state[f"{SRD}.entities"]["guard"]
    succeeded = context["_unarmed_success"]
    if effect == "damage":
        assert target["hp"] == 1000 - (4 if succeeded else 0)
        summary = {row["label"]: row["value"] for row in card["summary"]}
        assert summary["Damage"] == 1000 - target["hp"]
    elif effect == "grapple":
        assert ("grappled" in target["conditions"]) is succeeded
        if succeeded:
            assert target["grappled_by"] == "hero" and target["grapple_escape_dc"] == 14
    elif effect == "shove_prone":
        assert ("prone" in target["conditions"]) is succeeded
    elif succeeded:
        assert target["last_forced_movement_feet"] == 5
        assert target["last_forced_movement_source"] == "hero"
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 1


def test_oversized_target_rejects_grapple_without_spending_but_can_still_be_hit():
    state, data = _case()
    state[f"{SRD}.entities"]["guard"]["size_rank"] = 4
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _strike(state, data, "grapple")
    assert state == before
    assert _strike(state, data, "damage").result["status"] == "committed"


@pytest.mark.parametrize("conditions,forego", [(["stunned"], False), ([], True)])
def test_automatic_or_foregone_save_has_no_invented_die(conditions, forego):
    state, data = _case()
    state[f"{SRD}.entities"]["guard"]["conditions"] = conditions
    receipt = _strike(state, data, "shove_prone", target_forego_save=forego)
    card = receipt.result["result_card"]
    assert card.get("equation") is None
    assert receipt.result["context"]["_unarmed_success"] is True
    assert "prone" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert "No roll" in next(row["value"] for row in card["details"] if row["label"] == "Roll mode")


@pytest.mark.parametrize("effect,ability,dodging,conditions,expected_dice,mode", [
    ("damage", "strength", True, [], 2, "Disadvantage"),
    ("damage", "strength", True, ["grappled"], 1, "Normal"),
    ("grapple", "dexterity", True, [], 2, "Advantage"),
    ("grapple", "dexterity", True, ["restrained"], 2, "Disadvantage"),
])
def test_unarmed_roll_and_card_apply_target_dodge_and_conditions(
    effect, ability, dodging, conditions, expected_dice, mode,
):
    state, data = _case()
    state[f"{SRD}.entities"]["guard"].update(dodging=dodging, conditions=conditions)
    receipt = _strike(state, data, effect, target_save_ability=ability)
    assert len(receipt.result["context"]["_d20_test"]["results"]) == expected_dice
    details = {row["label"]: row["value"] for row in receipt.result["result_card"]["details"]}
    assert details["Roll mode"] == mode


def test_unarmed_attack_honors_resistance_and_reports_applied_damage():
    state, data = _case()
    state[f"{SRD}.entities"]["guard"].update(
        armor_class=1, damage_resistances=["bludgeoning"], temp_hp=1,
    )
    receipt = _strike(state, data, "damage")
    card = receipt.result["result_card"]
    summary = {row["label"]: row["value"] for row in card["summary"]}
    details = {row["label"]: row["value"] for row in card["details"]}
    assert summary["Damage"] == 2
    assert details["HP lost"] == 1 and details["Temporary HP absorbed"] == 1
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 999


def test_unknown_npc_save_is_rejected_without_assuming_zero_or_spending():
    state, data = _case()
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"] = {}
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _strike(state, data, "shove_prone")
    assert state == before
