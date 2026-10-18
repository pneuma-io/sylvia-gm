"""Combat attack rolls must honor authoritative conditions, defenses and distances."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.test_srd_action_budget import _run
from tests.test_srd_attack_budget import SRD, _attack, _combat_case, _npc_attack


def _npc_case():
    state, data = _combat_case()
    state[f"{SRD}.combat"].update(active_actor="guard", current_actor="guard")
    return state, data


@pytest.mark.parametrize("condition", [
    "incapacitated", "paralyzed", "petrified", "stunned", "unconscious",
])
def test_incapacitated_npc_cannot_spend_an_attack(condition):
    state, _ = _npc_case()
    state[f"{SRD}.entities"]["guard"]["conditions"] = [condition]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _npc_attack(state, "Spear", use_multiattack=False)
    assert state == before


@pytest.mark.parametrize("kind", ["character", "npc"])
@pytest.mark.parametrize("target_conditions,advantage,expected_dice", [
    ([], False, 2), ([], True, 1), (["grappled"], False, 1),
])
def test_dodge_applies_disadvantage_cancels_advantage_and_ends_when_speed_is_zero(
    kind, target_conditions, advantage, expected_dice,
):
    state, data = _npc_case() if kind == "npc" else _combat_case()
    target = state[f"{SRD}.entities"]["hero" if kind == "npc" else "guard"]
    target.update(dodging=True, speed=30, conditions=target_conditions)
    receipt = (_npc_attack(state, "Spear", use_multiattack=False, advantage=advantage)
               if kind == "npc" else _attack(state, data, advantage=advantage))
    assert len(receipt.result["context"]["_attack_roll"]["results"]) == expected_dice


@pytest.mark.parametrize("kind", ["character", "npc"])
def test_exhaustion_penalty_is_in_the_actual_attack_total_and_card(kind):
    state, data = _npc_case() if kind == "npc" else _combat_case()
    actor = "guard" if kind == "npc" else "hero"
    state[f"{SRD}.entities"][actor]["exhaustion_level"] = 2
    receipt = (_npc_attack(state, "Spear", use_multiattack=False) if kind == "npc"
               else _attack(state, data))
    context = receipt.result["context"]
    # NPC's declared +3 and Fighter 5's Strength +3 / proficiency +3 each lose 4.
    expected_modifier = -1 if kind == "npc" else 2
    assert context["_attack_card_modifier"] == expected_modifier
    assert context["_attack_adjusted_total"] == context["_attack_roll"]["total"] + expected_modifier
    assert receipt.result["result_card"]["equation"]["modifier"] == expected_modifier


@pytest.mark.parametrize("attack_mode,distance,normal_range,long_range", [
    ("melee", 10, 5, 5), ("ranged", 65, 20, 60), ("ranged", 5, 60, 20),
])
def test_npc_rejects_out_of_reach_and_range_without_spending_an_attack(
    attack_mode, distance, normal_range, long_range,
):
    state, _ = _npc_case()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _npc_attack(state, "Spear", use_multiattack=False, attack_mode=attack_mode,
                    attack_distance=distance, normal_range=normal_range, long_range=long_range)
    assert state == before


def test_npc_long_range_and_unseen_target_apply_disadvantage():
    for choices in ({"attack_mode": "ranged", "attack_distance": 30,
                     "normal_range": 20, "long_range": 60}, {"actor_can_see_target": False}):
        state, _ = _npc_case()
        if "attack_distance" in choices:
            for body in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
                if body["id"] == "hero":
                    body["position"] = [-25, 0, 0]
        else:
            state[f"{SRD}.entities"]["hero"]["conditions"] = ["invisible"]
        context = _npc_attack(state, "Spear", use_multiattack=False,
                              **choices).result["context"]
        assert len(context["_attack_roll"]["results"]) == 2
        assert context["_attack_roll"]["total"] == min(context["_attack_roll"]["results"])


def test_npc_attack_uses_active_shield_of_faith_armor_class():
    state, data = _npc_case()
    _run("install_srd_concentration_armor_class_bonus", state, participant_data=data,
         casting_ability="wisdom", casting_modifier=0, casting_attack_bonus=2,
         casting_save_dc=10, casting_scaling_level=data["level"],
         casting_concentration_save_modifier=(data["abilities"]["constitution"] - 10) // 2,
         caster_state="hero", target_state="hero", lasting_state="state",
         spell="shield_of_faith", duration_rounds=100)
    assert state[f"{SRD}.entities"]["hero"]["concentration_head_by_spell"] == {
        "shield_of_faith": "hero",
    }
    context = _npc_attack(state, "Spear", use_multiattack=False).result["context"]
    assert context["_effective_target_armor_class"] == 19


def test_npc_critical_hit_against_unconscious_character_applies_two_death_failures(monkeypatch):
    # Control the random source, retaining the real roll, critical-dice and damage code.
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, _ = _npc_case()
    state[f"{SRD}.entities"]["hero"].update(
        hp=0, armor_class=1, conditions=["unconscious"], death_failures=0, stable=True,
    )
    receipt = _npc_attack(state, "Spear", use_multiattack=False, damage_expression="d6")
    context = receipt.result["context"]
    assert context["_attack_outcome"] == "critical_hit"
    assert context["_damage_roll"]["count"] == 2
    assert state[f"{SRD}.entities"]["hero"]["death_failures"] == 2
    assert state[f"{SRD}.entities"]["hero"]["stable"] is False
