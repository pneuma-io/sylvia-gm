"""Real SRD attacks share class and NPC action budgets without manufactured attack receipts."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import SRD, _run, _state


def _combat_case(classes=None):
    classes = classes or [{"id": "fighter", "level": 5}]
    state = _state()
    state[f"{SRD}.state"] = {}
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(inventory=["spear", "dagger", "scimitar"], ammunition={}, armor_class=17,
                light_extra_attack_available=False, light_attack_source_weapons=[],
                light_extra_attack_used=False, damage_immunities=[], damage_resistances=[],
                damage_vulnerabilities=[], temp_hp=0, creature_kind="character")
    hero["fighter_level"] = sum(c["level"] for c in classes if c["id"] == "fighter")
    state[f"{SRD}.entities"]["guard"] = {
        "name": "Guard", "creature_kind": "monster", "hp": 1000, "max_hp": 1000,
        "armor_class": 13, "is_alive": True, "conditions": [], "exhaustion_level": 0,
        "temp_hp": 0, "damage_immunities": [], "damage_resistances": [],
        "damage_vulnerabilities": [], "resources": {}, "resource_maxima": {},
    }
    data = {
        "name": "Budget Hero", "level": sum(c["level"] for c in classes), "classes": classes,
        "abilities": {"strength": 16, "dexterity": 14, "constitution": 14,
                      "intelligence": 10, "wisdom": 10, "charisma": 10},
        "weapon_proficiencies": ["simple_weapons", "martial_weapons"],
        "weapon_masteries": ["dagger", "scimitar"], "speed": 30,
    }
    return state, data


def _attack(state, data, **choices):
    parameters = {
        "actor": "hero", "actor_state": "hero", "target": "guard", "attack_sequence": "primary",
        "use_graze_on_miss": False, "weapon": "spear", "attack_mode": "melee",
        "attack_ability": "strength", "wielding_two_handed": False, "mounted": False,
        "circumstantial_modifier": 0, "advantage": False, "disadvantage": False,
        "actor_can_see_target": True, "target_can_see_actor": True,
        "target_is_charmer": False, "fear_source_in_sight": False, "attack_distance": 5,
    }
    return _run("resolve_combat_attack", state, participant_data=data, **(parameters | choices))


def test_fighter_extra_attack_commits_two_actual_attacks_for_one_action():
    state, data = _combat_case()
    first = _attack(state, data)
    second = _attack(state, data)
    assert first.result["status"] == second.result["status"] == "committed"
    assert "_attack_roll" in first.result["context"] and "_attack_roll" in second.result["context"]
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert state[f"{SRD}.combat"]["attack_available"] is False


@pytest.mark.parametrize("classes,count", [
    ([{"id": "fighter", "level": 4}], 1),
    ([{"id": "fighter", "level": 11}], 3),
    ([{"id": "fighter", "level": 20}], 4),
    *[([{"id": name, "level": 5}], 2) for name in ["barbarian", "monk", "paladin", "ranger"]],
    ([{"id": "fighter", "level": 5}, {"id": "barbarian", "level": 5}], 2),
    ([{"id": "fighter", "level": 4}, {"id": "paladin", "level": 4}], 1),
    ([{"id": "wizard", "level": 20}], 1),
])
def test_attack_count_uses_class_grants_without_stacking_multiclass_levels(classes, count):
    state, data = _combat_case(classes)
    for _ in range(count):
        assert _attack(state, data).result["status"] == "committed"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _attack(state, data)
    assert state == before


@pytest.mark.parametrize("surge_first", [False, True])
def test_extra_attack_surges_for_four_attacks_with_movement_and_distinct_targets(surge_first):
    state, data = _combat_case()
    state[f"{SRD}.entities"]["other"] = deepcopy(state[f"{SRD}.entities"]["guard"])
    if surge_first:
        _run("use_srd_action_surge", state, participant_data=data, actor="hero", actor_state="hero")
    for index in range(4):
        if index == 2 and not surge_first:
            _run("use_srd_action_surge", state, participant_data=data,
                 actor="hero", actor_state="hero")
        _attack(state, data, target="other" if index % 2 else "guard")
        if index == 0:
            _run("spend_combat_movement", state, actor="hero", actor_state="hero",
                 distance=5, difficult_terrain=False)
    assert state[f"{SRD}.combat"]["movement_remaining"] == 25
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 0
    assert state[f"{SRD}.combat"]["action_available"] is False
    with pytest.raises(RulesetPreconditionRejected):
        _attack(state, data)


@pytest.mark.parametrize("surge_first", [False, True])
def test_nick_is_once_per_turn_even_with_extra_attack_and_action_surge(surge_first):
    state, data = _combat_case()
    if surge_first:
        _run("use_srd_action_surge", state, participant_data=data, actor="hero", actor_state="hero")
    _attack(state, data, weapon="dagger")
    _attack(state, data, weapon="scimitar", attack_sequence="light_nick")
    _attack(state, data, weapon="dagger")
    if not surge_first:
        _run("use_srd_action_surge", state, participant_data=data, actor="hero", actor_state="hero")
    _attack(state, data, weapon="dagger")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _attack(state, data, weapon="scimitar", attack_sequence="light_nick")
    assert state == before

def _npc_attack(state, name, **choices):
    parameters = {
        "actor": "guard", "target": "hero", "attack_name": name, "attack_modifier": 3,
        "damage_expression": "fixed-1", "damage_modifier": 0, "damage_type": "piercing",
        "advantage": False, "disadvantage": False, "use_multiattack": True,
        "attack_mode": "melee", "attack_distance": 5, "normal_range": 5, "long_range": 5,
        "actor_can_see_target": True, "target_can_see_actor": True,
        "target_is_charmer": False, "fear_source_in_sight": False,
    }
    return _run("resolve_adjudicated_creature_attack", state, **(parameters | choices))


def test_multiattack_limits_each_named_component_and_preserves_remaining_on_rejection():
    state, _ = _combat_case()
    state[f"{SRD}.combat"].update(active_actor="guard", current_actor="guard")
    state[f"{SRD}.entities"]["guard"]["multiattack"] = [
        {"attack": "Claw", "count": 2}, {"attack": "Bite", "count": 1},
    ]
    assert _npc_attack(state, "Claw").result["status"] == "committed"
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 2
    assert _npc_attack(state, "Claw").result["status"] == "committed"
    before = deepcopy(state)
    for name, choices in [("Claw", {}), ("Invented", {}), ("Bite", {"use_multiattack": False})]:
        with pytest.raises(RulesetPreconditionRejected):
            _npc_attack(state, name, **choices)
        assert state == before
    assert _npc_attack(state, "Bite").result["status"] == "committed"
    assert state[f"{SRD}.combat"]["attack_available"] is False
    with pytest.raises(RulesetPreconditionRejected):
        _npc_attack(state, "Bite")

