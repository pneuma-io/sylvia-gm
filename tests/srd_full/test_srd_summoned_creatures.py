"""A summoned creature follows its caster's saved concentration lifetime."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case


def _summon_case():
    state, data, _ = _spell_case("longstrider")
    state[f"{SRD}.entities"]["hero"]["prepared_spells"] = ["summon_dragon"]
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    return state, data


def _install(state, data, *, summon_id="dragon_one", duration_rounds=600):
    return _run(
        "install_srd_summoned_creature", state, participant_data=data,
        caster_state="hero", lasting_state="state", summon_id=summon_id,
        spell="summon_dragon", slot_level=6, base_level=5,
        duration_rounds=duration_rounds, creature_name="Dragon spirit",
        creature_type="dragon", alignment="neutral",
        base_hit_points=50, hit_points_per_level=10, armor_class_base=14,
        speed=30, flying_speed=60, swimming_speed=30, size_rank=3,
        melee_reach=10, attack_bonus=9, save_dc=17,
        strength_score=19, dexterity_score=14, constitution_score=17,
        intelligence_score=10, wisdom_score=14, charisma_score=14,
        concentration_save_modifier=3, caster_proficiency_bonus=6,
        rend_name="Rend", rend_damage_die="d6", rend_damage_type="piercing",
        rend_base_damage_modifier=4, attacks_per_level_divisor=2,
        breath_name="Breath Weapon", breath_dice_count=2,
        breath_damage_die="d6", breath_cone_feet=30,
        breath_save_ability="dexterity",
        blindsight_feet=30, darkvision_feet=60, passive_perception=12,
        languages=["draconic"], understands_caster_languages=True,
        strength_save=4, dexterity_save=2, constitution_save=3,
        intelligence_save=0, wisdom_save=2, charisma_save=2,
        damage_resistances=["acid", "cold", "fire", "lightning", "poison"],
        condition_immunities=["charmed", "frightened", "poisoned"],
        selected_damage_type="fire",
    )


def test_summon_installs_scaled_actor_and_disappears_on_concentration_end():
    state, data = _summon_case()
    _install(state, data)
    creature = state[f"{SRD}.entities"]["dragon_one"]
    caster = state[f"{SRD}.entities"]["hero"]
    assert creature["hp"] == creature["max_hp"] == 60
    assert creature["armor_class"] == 20
    assert creature["creature_type"] == "dragon"
    assert creature["abilities"]["strength"] == 19
    assert creature["size_rank"] == 3
    assert creature["proficiency_bonus"] == 6
    assert creature["flying_speed"] == 60
    assert creature["summon_caster_state_id"] == "hero"
    assert caster["concentration_target_state_id"] == "dragon_one"
    assert state[f"{SRD}.state"]["srd_summon_caster_ids"] == ["hero"]

    _run("end_srd_concentration_node", state, source_state="hero",
         target_state="dragon_one")
    creature = state[f"{SRD}.entities"]["dragon_one"]
    caster = state[f"{SRD}.entities"]["hero"]
    assert creature["summon_active"] is False
    assert creature["is_alive"] is False
    assert creature["hp"] == 0
    assert caster["concentration_spell_id"] is None
    assert state[f"{SRD}.state"]["srd_summon_caster_ids"] == []


def test_summon_expires_from_saved_time_and_dead_summon_ends_source():
    state, data = _summon_case()
    _install(state, data, duration_rounds=2)
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert state[f"{SRD}.entities"]["dragon_one"]["summon_active"] is True
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert state[f"{SRD}.entities"]["dragon_one"]["summon_active"] is False
    assert state[f"{SRD}.state"]["srd_summon_caster_ids"] == []

    _install(state, data, summon_id="dragon_two")
    state[f"{SRD}.entities"]["dragon_two"]["is_alive"] = False
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert state[f"{SRD}.entities"]["dragon_two"]["summon_active"] is False
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None


def test_summon_rejects_duplicate_identity_without_mutating_state():
    state, data = _summon_case()
    _install(state, data)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _install(state, data)
    assert state == before


def test_summon_disappears_immediately_at_zero_hit_points():
    state, data = _summon_case()
    _install(state, data)
    _run("apply_srd_damage", state, target="dragon_one", amount=100,
         adjustment=0, damage_type="radiant")
    creature = state[f"{SRD}.entities"]["dragon_one"]
    assert creature["hp"] == 0
    assert creature["summon_active"] is False
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None


def test_summon_expires_on_group_initiative_clock():
    state, data = _summon_case()
    _install(state, data, duration_rounds=2)
    state[f"{SRD}.combat"].update(
        encounter_active=True, group_order=True, round=3, turn_index=0,
        order=[{"entity_id": "hero"}, {"entity_id": "guard"}],
        lasting_epoch_round=1, lasting_epoch_tick=0,
    )
    _run("sync_srd_group_lasting_clock", state, lasting_state="state")
    assert state[f"{SRD}.entities"]["dragon_one"]["summon_active"] is False


@pytest.mark.parametrize("group_order", [False, True])
def test_summon_follows_caster_in_initiative_without_rewinding_time(group_order):
    state, data = _summon_case()
    combat = state[f"{SRD}.combat"]
    combat.update(
        encounter_active=True, turn_active=True, current_actor="hero",
        active_actor="hero", group_order=group_order, round=3,
        turn_index=1 if group_order else 0,
        order=[{"entity_id": "guard"}, {"entity_id": "hero"}] if group_order else [],
        order_first="hero", order_second="guard",
    )
    state[f"{SRD}.state"]["lasting_tick"] = 5
    _install(state, data)
    combat = state[f"{SRD}.combat"]
    assert combat["group_order"] is True
    assert [entry["entity_id"] for entry in combat["order"]] == (
        ["guard", "hero", "dragon_one"] if group_order
        else ["hero", "dragon_one", "guard"]
    )
    _run("sync_srd_group_lasting_clock", state, lasting_state="state")
    assert state[f"{SRD}.state"]["lasting_tick"] == 5


def test_summon_grants_only_chosen_resistance_until_it_ends():
    state, data = _summon_case()
    state[f"{SRD}.entities"]["hero"].update(hp=100, max_hp=100)
    _install(state, data)
    state[f"{SRD}.entities"]["hero"]["concentration_save_modifier"] = 100
    _run("apply_srd_damage", state, target="hero", amount=5,
         adjustment=0, damage_type="fire")
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 98
    _run("apply_srd_damage", state, target="hero", amount=5,
         adjustment=0, damage_type="cold")
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 93
    _run("end_srd_concentration_node", state, source_state="hero",
         target_state="dragon_one")
    _run("apply_srd_damage", state, target="hero", amount=5,
         adjustment=0, damage_type="fire")
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 88


def test_summon_condition_immunity_blocks_public_condition_application():
    state, data = _summon_case()
    _install(state, data)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="immune"):
        _run("apply_srd_condition", state, target="dragon_one", condition="poisoned")
    assert state == before
    _run("apply_srd_condition", state, target="dragon_one", condition="blinded")
    assert "blinded" in state[f"{SRD}.entities"]["dragon_one"]["conditions"]


def _begin_summon_turn(state, *, command=None):
    combat = state[f"{SRD}.combat"]
    combat.update(
        encounter_active=True, group_order=True, turn_active=False,
        current_actor="hero", active_actor=None, phase="awaiting_turn_start",
        order=[{"entity_id": "hero"}, {"entity_id": "dragon_one"},
               {"entity_id": "guard"}],
        turn_index=0, round=1, lasting_epoch_round=1, lasting_epoch_tick=0,
    )
    if command:
        _run("command_srd_summoned_creature", state, caster_state="hero",
             summon_state="dragon_one", command=command)
    state[f"{SRD}.combat"].update(current_actor="dragon_one", turn_index=1)
    _run("begin_combat_turn", state, actor="dragon_one")


def test_uncommanded_summon_dodges_and_has_no_attack_action():
    state, data = _summon_case()
    _install(state, data)
    _begin_summon_turn(state)
    creature = state[f"{SRD}.entities"]["dragon_one"]
    combat = state[f"{SRD}.combat"]
    assert creature["dodging"] is True
    assert combat["action_available"] is False
    assert combat["attack_available"] is False
    assert combat["movement_remaining"] == 30


def test_commanded_summon_uses_saved_rend_profile_and_breath():
    from tests.srd_full.test_srd_spell_wave3_information import _case as _scene_case

    state, data, parameters = _scene_case("summon_dragon")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    parameters.pop("target")
    parameters.update(
        slot_resource="spell_slot_6", summon_id="dragon_one", selected_damage_type="fire",
        created_actor_position=[10, 0, 0], created_actor_height=8,
    )
    for actor, creature_type in (("hero", "humanoid"), ("guard", "fiend")):
        state[f"{SRD}.entities"][actor]["creature_type"] = creature_type
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    _begin_summon_turn(state, command="attack")
    state[f"{SRD}.entities"]["guard"].update(hp=100, max_hp=100)
    attack = {
        "actor": "dragon_one", "target": "guard", "attack_distance": 5,
        "attack_mode": "melee", "normal_range": 10, "long_range": 10,
        "actor_can_see_target": True, "target_can_see_actor": True,
        "target_is_charmer": False, "fear_source_in_sight": False,
        "use_multiattack": True, "attack_name": "Rend",
        "attack_modifier": state[f"{SRD}.entities"]["dragon_one"]["summon_attack_bonus"],
        "damage_expression": "d6", "damage_modifier": 10, "damage_type": "piercing",
        "advantage": False, "disadvantage": False,
    }
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="saved Rend profile"):
        _run("resolve_adjudicated_creature_attack", state,
             **(attack | {"damage_modifier": 100}))
    assert state == before
    _run("resolve_adjudicated_creature_attack", state, **attack)
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 3
    _run("resolve_srd_summon_breath", state, actor_state="dragon_one",
         use_multiattack=True)
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 2
    assert state[f"{SRD}.entities"]["guard"]["hp"] < 100


def test_public_summon_cast_enforces_space_and_uses_definition_profile():
    from tests.srd_full.test_srd_spell_wave3_information import _case as _scene_case

    state, data, parameters = _scene_case("summon_dragon")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    parameters.update(
        summon_id="dragon_one", summon_distance=30,
        caster_can_see_summon_space=True, summon_space_unoccupied=True,
        selected_damage_type="lightning",
        created_actor_position=[10, 0, 0], created_actor_height=8,
    )
    parameters.pop("target")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="unoccupied space"):
        _run("cast_spell_outside_combat", state, participant_data=data,
             **(parameters | {"created_actor_position": [20, 0, 0]}))
    assert state == before
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    creature = state[f"{SRD}.entities"]["dragon_one"]
    assert creature["max_hp"] == 50
    assert creature["armor_class"] == 19
    assert creature["summon_damage_type"] == "lightning"
    assert creature["multiattack"] == [
        {"attack": "Rend", "count": 2},
        {"attack": "Breath Weapon", "count": 1},
    ]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2


def test_outside_combat_summon_shares_caster_initiative_on_encounter_start():
    state, data = _summon_case()
    _install(state, data)
    entities = state[f"{SRD}.entities"]
    for creature in entities.values():
        creature["initiative_modifier"] = 0
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="summoned creature"):
        _run("start_group_combat_encounter", state,
             combatants=["hero", "guard", "other"])
    assert state == before
    _run("start_group_combat_encounter", state,
         combatants=["hero", "dragon_one", "guard"])
    order = state[f"{SRD}.combat"]["order"]
    ids = [entry["entity_id"] for entry in order]
    caster_index = ids.index("hero")
    assert ids[caster_index + 1] == "dragon_one"
    assert order[caster_index + 1]["initiative_result"] == order[caster_index]["initiative_result"]


def test_public_combat_summon_enters_next_turn_and_defaults_to_dodge():
    from tests.srd_full.test_srd_spell_wave3_information import _case as _scene_case

    state, data, parameters = _scene_case("summon_dragon")
    combat = state[f"{SRD}.combat"]
    combat.update(
        encounter_active=True, group_order=True, turn_active=True,
        current_actor="hero", active_actor="hero", active_actor_kind="character",
        phase="turn_active", round=1, turn_index=0,
        order=[{"entity_id": "hero"}, {"entity_id": "guard"},
               {"entity_id": "other"}],
        lasting_epoch_round=1, lasting_epoch_tick=0,
    )
    parameters.pop("target")
    parameters.update(
        summon_id="dragon_one", summon_distance=30,
        caster_can_see_summon_space=True, summon_space_unoccupied=True,
        selected_damage_type="fire",
        created_actor_position=[10, 0, 0], created_actor_height=8,
    )
    _run("cast_spell", state, participant_data=data, **parameters)
    assert [entry["entity_id"] for entry in state[f"{SRD}.combat"]["order"]] == [
        "hero", "dragon_one", "guard", "other",
    ]
    assert state[f"{SRD}.combat"]["action_available"] is False
    _run("end_combat_turn", state, actor="hero")
    _run("advance_group_combat_encounter_turn", state, completed_actor="hero")
    assert state[f"{SRD}.combat"]["current_actor"] == "dragon_one"
    _run("begin_combat_turn", state, actor="dragon_one")
    assert state[f"{SRD}.entities"]["dragon_one"]["dodging"] is True
