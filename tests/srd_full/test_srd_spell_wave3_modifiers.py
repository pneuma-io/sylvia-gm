"""Fixed actor clauses must change the shared owner and survive source expiration."""

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_attack_budget import _npc_attack
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_composition import catalogue as catalogue
from tests.srd_full.test_srd_spell_result_cards import SRD
from tests.srd_full.test_srd_spell_wave_defenses import _case, _cast


def test_advanced_seven_source_headers_and_explicit_weapon_gaps(catalogue):
    from scripts.srd_full.build_srd_5_2_1_spells import _validate_spell_composition

    definitions, headers, runtime, text = catalogue
    _validate_spell_composition(definitions, headers, runtime, text)
    source_path = (
        Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1/content/spell-mechanics-source.json"
    )
    sources = json.loads(source_path.read_text())["spells"]
    for spell in (
        "foresight",
        "regenerate",
        "glibness",
        "divine_favor",
        "fire_shield",
        "magic_weapon",
        "shillelagh",
        "haste",
        "slow",
        "enlarge_reduce",
    ):
        assert definitions[spell]["family"] == "timed_actor_modifier"
        assert sources[spell]["spell_id"] == spell
    for spell in ("foresight", "regenerate", "glibness", "divine_favor", "fire_shield"):
        assert sources[spell]["not_yet_mapped"] == []
    for spell in ("magic_weapon", "shillelagh", "haste", "slow", "enlarge_reduce"):
        assert sources[spell]["not_yet_mapped"]


@pytest.mark.parametrize(
    "kind",
    [
        "sensing_emotions",
        "sensing_alignment",
        "reading_thoughts",
        "magical_location",
        "remote_observation",
        "information_gathering",
        "mind_control",
    ],
)
def test_mind_blank_guards_actual_subject_until_source_expires(kind):
    state, data, parameters = _case("mind_blank", target="guard")
    _cast(state, data, parameters)

    def check(target):
        return _run(
            "check_srd_mind_blank_information",
            state,
            target_state=target,
            lasting_state="state",
            query_kind=kind,
        ).result["context"]["_mind_blank_information_blocked"]

    assert check("guard") is True
    assert check("other") is False
    state[f"{SRD}.state"]["lasting_tick"] += 28800
    assert check("guard") is False


def test_beacon_maximum_stays_subject_specific_at_shared_hp_owner():
    state, data, parameters = _case("beacon_of_hope", targets=["guard"])
    _cast(state, data, parameters)
    for target in ("guard", "other"):
        state[f"{SRD}.entities"][target].update(hp=1, max_hp=100)
        _run(
            "restore_shared_spell_healing_target", state, target=target, amount=5, maximum_amount=44
        )
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 45
    assert state[f"{SRD}.entities"]["other"]["hp"] == 6


def test_beacon_maximizes_nonspell_second_wind(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _case("beacon_of_hope", targets=["hero"])
    _cast(state, data, parameters)
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(hp=1, max_hp=100, fighter_level=2, available_bonus_actions=["second_wind"])
    hero["resources"]["second_wind"] = 1
    state[f"{SRD}.combat"].update(
        turn_active=True, encounter_active=True, active_actor="hero", bonus_action_available=True
    )
    data["classes"] = [{"id": "fighter", "level": 2}, {"id": "cleric", "level": 15}]
    _run("use_srd_second_wind", state, participant_data=data, actor="hero", actor_state="hero")
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 13


def test_beacon_maximizes_short_rest_hit_die_at_its_direct_owner(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _case("beacon_of_hope", targets=["guard"])
    _cast(state, data, parameters)
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(
        hp=1,
        max_hp=100,
        short_rest_hit_dice_available=True,
        short_rest_hit_dice_tick=state[f"{SRD}.state"]["lasting_tick"],
        constitution_modifier=-1,
        hit_die_size=6,
        hit_dice_pools=None,
    )
    guard["resources"]["hit_die"] = 1
    result = _run("take_srd_short_rest_hit_die", state, target_state="guard").result["context"]
    assert result["_short_rest_healing"] == 5
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 6


def test_ray_delayed_roll_owner_retains_raw_trace_and_clamps_after_adjustment(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _case(
        "ray_of_enfeeblement", targets=["hero"], forego_save_targets=["hero"]
    )
    state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"] = dict.fromkeys(data["abilities"], 0)
    _cast(state, data, parameters)
    result = _run(
        "resolve_srd_delayed_spell_damage",
        state,
        target_state="guard",
        dice_count=1,
        damage_die="d4",
        damage_type="fire",
        effect_label="Delayed",
        source_state_id="hero",
    ).result["context"]
    assert result["_delayed_roll"]["results"] == [4]
    assert result["_delayed_roll"]["total"] == -3
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 19


@pytest.mark.parametrize("distributed", [False, True])
def test_ray_penalizes_each_actual_magic_missile_dart_once(monkeypatch, distributed):
    monkeypatch.setattr(dice, "_rng", Random(6))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _case(
        "ray_of_enfeeblement", targets=["hero"], forego_save_targets=["hero"]
    )
    state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"] = dict.fromkeys(data["abilities"], 0)
    _cast(state, data, parameters)
    data["spellcasting_grants"][0]["spells"].append("magic_missile")
    data["wizard_spellbook"].append("magic_missile")
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("magic_missile")
    parameters.update(spell="magic_missile", slot_resource="spell_slot_1")
    parameters.pop("targets")
    parameters.pop("forego_save_targets")
    if distributed:
        parameters["projectile_targets"] = ["guard", "other", "other"]
    context = _cast(state, data, parameters)["context"]
    assert context["_spell_damage"]["results"] == [1, 4, 3]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == (19 if distributed else 14)
    if distributed:
        assert state[f"{SRD}.entities"]["other"]["hp"] == 15


def test_freedom_separates_magic_from_mundane_conditions_and_spends_five_feet():
    state, data, parameters = _case("freedom_of_movement", targets=["guard"])
    _run(
        "apply_srd_condition", state, target="guard", condition="paralyzed", source_is_magical=True
    )
    _cast(state, data, parameters)
    guard = state[f"{SRD}.entities"]["guard"]
    assert "paralyzed" not in guard["conditions"]
    with pytest.raises(RulesetPreconditionRejected, match="immune"):
        _run(
            "apply_srd_condition",
            state,
            target="guard",
            condition="restrained",
            source_is_magical=True,
        )
    _run(
        "apply_srd_condition",
        state,
        target="guard",
        condition="restrained",
        source_is_magical=False,
    )
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]
    state[f"{SRD}.combat"].update(
        turn_active=True,
        encounter_active=True,
        active_actor="guard",
        movement_remaining=30,
        movement_spent=0,
        action_available=True,
    )
    _run("escape_srd_nonmagical_restraint", state, actor_state="guard", restraint="restrained")
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.combat"]["movement_remaining"] == 25
    assert state[f"{SRD}.combat"]["action_available"] is True
    state[f"{SRD}.state"]["lasting_tick"] += 1200
    _run("refresh_srd_timed_condition_target", state, target_state="guard", lasting_state="state")
    assert "paralyzed" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_ray_leaves_fixed_creature_damage_alone_without_reading_an_absent_roll():
    state, data, parameters = _case(
        "ray_of_enfeeblement", targets=["guard"], forego_save_targets=["guard"]
    )
    _cast(state, data, parameters)
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        current_actor="guard",
        action_available=True,
        normal_action_spent=False,
    )
    state[f"{SRD}.entities"]["guard"]["multiattack"] = [{"attack": "Claw", "count": 1}]
    result = _npc_attack(state, "Claw", attack_modifier=100)
    assert result.result["status"] == "committed"
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 19


def test_calm_indifference_changes_hostility_and_witnessed_ally_damage_ends_only_that_source():
    state, data, parameters = _case(
        "calm_emotions",
        targets=["guard"],
        all_targets_in_area=True,
        forego_save_targets=["guard"],
        calm_indifference_targets=["guard"],
        calm_indifferent_toward=["other"],
    )
    _run(
        "record_srd_creature_relationship",
        state,
        actor_state="guard",
        toward_state="other",
        attitude="hostile",
    )
    _run(
        "record_srd_creature_relationship",
        state,
        actor_state="guard",
        toward_state="hero",
        attitude="friendly",
        is_ally=True,
    )
    _cast(state, data, parameters)

    def attitude():
        return _run(
            "read_srd_creature_attitude", state, actor_state="guard", toward_state="other"
        ).result["context"]["_creature_attitude"]

    assert attitude() == "indifferent"
    with pytest.raises(RulesetPreconditionRejected, match="indifferent"):
        _run("validate_srd_charmer_harm", state, actor_state="guard", target_state="other")
    state[f"{SRD}.entities"]["hero"]["concentration_save_modifier"] = 30
    _run(
        "apply_srd_damage",
        state,
        target="hero",
        amount=1,
        adjustment=0,
        damage_type="fire",
        source_state_id="other",
        witness_state_ids=["guard"],
    )
    assert attitude() == "hostile"
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "calm_emotions"


def test_sanctuary_pending_save_is_durable_and_loss_or_retarget_is_explicit(monkeypatch):
    state, data, parameters = _case("sanctuary", targets=["guard"])
    _cast(state, data, parameters)
    state[f"{SRD}.entities"]["other"]["saving_throw_modifiers"]["wisdom"] = -30
    state[f"{SRD}.state"]["pending_srd_actions"] = {
        "event": {
            "kind": "weapon_attack",
            "actor_state_id": "other",
            "target_state_id": "guard",
            "status": "prepared",
            "stage": "targeting",
        }
    }
    first = _run(
        "check_srd_sanctuary_targeting",
        state,
        actor_state="other",
        target_state="guard",
        lasting_state="state",
        pending_action_id="event",
        operation_kind="weapon_attack",
    ).result["context"]
    assert first["_sanctuary_target_allowed"] is False
    assert first["_sanctuary_choice_required"] is True
    monkeypatch.setattr(
        dice, "roll", lambda *a, **kw: pytest.fail("A retained Sanctuary save was rerolled")
    )
    repeat = _run(
        "check_srd_sanctuary_targeting",
        state,
        actor_state="other",
        target_state="guard",
        lasting_state="state",
        pending_action_id="event",
        operation_kind="weapon_attack",
    ).result["context"]
    assert repeat["_sanctuary_save"] == first["_sanctuary_save"]
    _run(
        "decide_srd_sanctuary_target",
        state,
        pending_action_id="event",
        decision="retarget",
        new_target_state_id="hero",
    )
    event = state[f"{SRD}.state"]["pending_srd_actions"]["event"]
    assert event["stage"] == "sanctuary_retarget_validation"
    assert event["sanctuary_retarget_state_id"] == "hero"


def _cast_long_actor_spell(state, data, parameters):
    from tests.srd_full.test_srd_extended_casting import _begin

    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
        elapsed_rounds=10,
        uninterrupted=True,
    )
    return _cast(state, data, parameters)


def test_foresight_changes_all_d20_kinds_and_incoming_attacks_then_recast_releases_old_subject():
    from tests.srd_full.test_srd_spell_wave_defenses import _attack_flags

    state, data, parameters = _case("foresight", targets=["guard"])
    state[f"{SRD}.entities"]["hero"]["turns_started"] = 1
    _cast_long_actor_spell(state, data, parameters)
    for kind in ("ability_check", "saving_throw", "attack_roll"):
        result = _run(
            "d20_test",
            state,
            actor_state="guard",
            ability="wisdom",
            kind=kind,
            ability_modifier=0,
            proficiency_bonus=0,
            proficiency_factor="none",
            circumstantial_modifier=0,
            target_number=10,
            advantage=False,
            disadvantage=False,
        ).result["context"]
        assert result["_effective_advantage"] is True
        assert len(result["_d20_test"]["results"]) == 2
    assert _attack_flags(state)["_condition_disadvantage"] is True
    _cast_long_actor_spell(state, data, parameters | {"targets": ["other"]})
    assert state[f"{SRD}.entities"]["guard"]["spell_foresight"] is False
    assert state[f"{SRD}.entities"]["other"]["spell_foresight"] is True


@pytest.mark.parametrize("group", [False, True])
def test_foresight_initiative_uses_two_actual_d20_results(group):
    state, data, parameters = _case("foresight", targets=["guard"])
    state[f"{SRD}.entities"]["hero"]["turns_started"] = 1
    _cast_long_actor_spell(state, data, parameters)
    for entity in state[f"{SRD}.entities"].values():
        entity["initiative_modifier"] = 0
    if group:
        result = _run(
            "start_group_combat_encounter", state, combatants=["hero", "guard", "other"]
        ).result["context"]
        entry = next(e for e in result["_initiative_order"] if e["entity_id"] == "guard")
        assert len(entry["roll"]["results"]) == 2
    else:
        result = _run(
            "start_combat_encounter_entities",
            state,
            first_actor="guard",
            second_actor="other",
            first_surprised=False,
            second_surprised=False,
            tie_winner="first",
        ).result["context"]
        assert len(result["_initiative_first_roll"]["results"]) == 2


def test_glibness_optional_fifteen_applies_only_to_charisma_checks_and_magical_truth(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _case("glibness")
    parameters.pop("target")
    _cast(state, data, parameters)
    result = _run(
        "d20_test",
        state,
        actor_state="hero",
        ability="charisma",
        kind="ability_check",
        ability_modifier=4,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=18,
        advantage=False,
        disadvantage=False,
    ).result["context"]
    assert result["_d20_test"]["results"] == [13]
    assert result["_d20_test"]["total"] == 15
    assert result["_adjusted_total"] == 19
    assert (
        _run(
            "resolve_srd_magical_truth", state, actor_state="hero", statement_is_truthful=False
        ).result["context"]["_magically_detected_truthful"]
        is True
    )
    assert (
        _run(
            "resolve_srd_magical_truth", state, actor_state="other", statement_is_truthful=False
        ).result["context"]["_magically_detected_truthful"]
        is False
    )

    for kind, ability, use in (
        ("saving_throw", "charisma", True),
        ("ability_check", "wisdom", True),
        ("ability_check", "charisma", False),
    ):
        monkeypatch.setattr(dice, "_rng", Random(0))
        context = _run(
            "d20_test",
            state,
            actor_state="hero",
            ability=ability,
            kind=kind,
            ability_modifier=4,
            proficiency_bonus=0,
            proficiency_factor="none",
            circumstantial_modifier=0,
            target_number=18,
            advantage=False,
            disadvantage=False,
            use_glibness=use,
        ).result["context"]
        assert context["_d20_test"]["total"] == 13
    state[f"{SRD}.state"]["lasting_tick"] += 1200
    _run("refresh_srd_actor_spell_projections", state, lasting_state="state")
    assert (
        _run(
            "resolve_srd_magical_truth", state, actor_state="hero", statement_is_truthful=False
        ).result["context"]["_magically_detected_truthful"]
        is False
    )


def test_fire_shield_shared_resistance_light_and_expiration():
    state, data, parameters = _case("fire_shield", actor_spell_choice="chill")
    parameters.pop("target")
    _cast(state, data, parameters)
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(hp=100, max_hp=100, temp_hp=0)
    light = _run(
        "check_srd_actor_spell_light", state, actor_state="hero", lasting_state="state"
    ).result["context"]
    assert (light["_actor_spell_bright_light_feet"], light["_actor_spell_dim_light_feet"]) == (
        10,
        20,
    )
    _run(
        "apply_srd_damage",
        state,
        target="hero",
        amount=10,
        adjustment=0,
        damage_type="fire",
        source_state_id="other",
    )
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 95
    _run(
        "apply_srd_composite_damage",
        state,
        target="hero",
        component_1_amount=10,
        component_1_damage_type="cold",
        component_2_amount=10,
        component_2_damage_type="fire",
        source_state_id="other",
    )
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 80
    state[f"{SRD}.state"]["lasting_tick"] += 200
    _run("refresh_srd_actor_spell_projections", state, lasting_state="state")
    _run(
        "apply_srd_damage",
        state,
        target="hero",
        amount=10,
        adjustment=0,
        damage_type="fire",
        source_state_id="other",
    )
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 70
    assert (
        _run(
            "check_srd_actor_spell_light", state, actor_state="hero", lasting_state="state"
        ).result["context"]["_actor_spell_dim_light_feet"]
        == 0
    )


@pytest.mark.parametrize(
    "mode,distance,expected", [("melee", 5, True), ("ranged", 5, False), ("melee", 10, False)]
)
def test_fire_shield_actual_creature_hit_retaliates_once_without_spending_defender_reaction(
    mode, distance, expected
):
    state, data, parameters = _case("fire_shield", actor_spell_choice="warm")
    parameters.pop("target")
    _cast(state, data, parameters)
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        current_actor="guard",
        action_available=True,
        normal_action_spent=False,
    )
    state[f"{SRD}.entities"]["guard"].update(
        multiattack=[{"attack": "Claw", "count": 1}], temp_hp=0
    )
    context = _npc_attack(
        state,
        "Claw",
        attack_modifier=100,
        attack_mode=mode,
        attack_distance=distance,
        normal_range=30,
        long_range=60,
    ).result["context"]
    assert state[f"{SRD}.entities"]["hero"]["reaction_available"] is True
    if expected:
        assert len(context["_fire_shield_damage_roll"]["results"]) == 2
        assert (
            state[f"{SRD}.entities"]["guard"]["hp"] == 19 - context["_fire_shield_damage_applied"]
        )
    else:
        assert state[f"{SRD}.entities"]["guard"]["hp"] == 19


def test_regenerate_initial_healing_elapsed_pulses_regrowth_and_shutdown():
    state, data, parameters = _case("regenerate", targets=["guard"])
    state[f"{SRD}.entities"]["hero"]["turns_started"] = 1
    state[f"{SRD}.entities"]["guard"].update(hp=1, max_hp=100)
    _run(
        "record_srd_severed_body_part",
        state,
        target_state="guard",
        lasting_state="state",
        part_id="left_hand",
        part_name="left hand",
    )
    _cast_long_actor_spell(state, data, parameters)
    initial_hp = state[f"{SRD}.entities"]["guard"]["hp"]
    assert 20 <= initial_hp <= 48
    _run("advance_srd_lasting_time", state, elapsed_rounds=19)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == initial_hp + 19
    assert state[f"{SRD}.entities"]["guard"]["body_parts"]["left_hand"]["missing"] is True
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == initial_hp + 20
    assert state[f"{SRD}.entities"]["guard"]["body_parts"]["left_hand"]["missing"] is False
    _run("advance_srd_lasting_time", state, elapsed_rounds=580)
    state[f"{SRD}.entities"]["guard"]["hp"] = 10
    _run("advance_srd_lasting_time", state, elapsed_rounds=20)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 10


def test_regenerate_real_turn_boundary_does_not_grant_twice_on_retry():
    state, data, parameters = _case("regenerate", targets=["guard"])
    state[f"{SRD}.entities"]["hero"]["turns_started"] = 1
    _cast_long_actor_spell(state, data, parameters)
    state[f"{SRD}.entities"]["guard"].update(hp=1, max_hp=100)
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=True, active_actor="guard")
    for _ in range(2):
        _run(
            "process_srd_turn_effects",
            state,
            lasting_state="state",
            boundary_actor="guard",
            boundary_phase="start",
            boundary_turn=5,
        )
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 2
    _run(
        "process_srd_turn_effects",
        state,
        lasting_state="state",
        boundary_actor="guard",
        boundary_phase="start",
        boundary_turn=6,
    )
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 3


@pytest.mark.parametrize("critical", [False, True])
def test_divine_favor_adds_actual_radiant_weapon_dice_and_preserves_typed_mitigation(
    monkeypatch, critical
):
    from tests.srd_full.test_srd_attack_budget import _attack

    state, data, parameters = _case("divine_favor")
    parameters.pop("target")
    _cast(state, data, parameters)
    monkeypatch.setattr(dice, "_rng", Random(5 if critical else 0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="hero",
        current_actor="hero",
        action_available=True,
        normal_action_spent=False,
    )
    state[f"{SRD}.entities"]["hero"].update(inventory=["spear"], ammunition={}, fighter_level=0)
    state[f"{SRD}.entities"]["guard"].update(
        hp=100, max_hp=100, temp_hp=0, damage_resistances=["radiant"]
    )
    data["weapon_proficiencies"] = ["simple_weapons"]
    context = _attack(state, data).result["context"]
    assert len(context["_divine_favor_roll"]["results"]) == (2 if critical else 1)
    assert (
        context["_damage_applied"]
        == context["_styled_weapon_damage"]
        + context["_damage_modifier"]
        + context["_divine_favor_amount"] // 2
    )
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 100 - context["_damage_applied"]


def test_divine_favor_shared_critical_damage_preserves_two_failed_death_saves():
    state, data, parameters = _case("divine_favor")
    parameters.pop("target")
    _cast(state, data, parameters)
    state[f"{SRD}.entities"]["guard"].update(
        hp=0, temp_hp=0, creature_kind="character", death_failures=0, death_successes=0
    )
    _run(
        "apply_srd_divine_favor_weapon_damage",
        state,
        actor_state="hero",
        target_state="guard",
        lasting_state="state",
        amount=1,
        adjustment=0,
        damage_type="piercing",
        critical_hit=True,
    )
    assert state[f"{SRD}.entities"]["guard"]["death_failures"] == 2


@pytest.mark.parametrize("slot,bonus", [(2, 1), (3, 2), (6, 3)])
def test_magic_weapon_uses_actual_slot_and_selected_inventory_weapon(slot, bonus):
    from tests.srd_full.test_srd_attack_budget import _attack

    state, data, parameters = _case(
        "magic_weapon",
        targets=["hero"],
        actor_spell_weapon="spear",
        slot_resource=f"spell_slot_{slot}",
    )
    state[f"{SRD}.entities"]["hero"].update(
        inventory=["spear", "dagger"], ammunition={}, fighter_level=0
    )
    _cast(state, data, parameters)
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="hero",
        current_actor="hero",
        action_available=True,
        normal_action_spent=False,
    )
    context = _attack(state, data).result["context"]
    assert context["_weapon_spell_bonus"] == bonus
    assert context["_attack_modifier"] == 3 + 6 + bonus
    assert context["_damage_modifier"] == 3 + bonus
    assert state[f"{SRD}.entities"]["hero"]["last_attack_weapon_magical"] is True
    other = _run(
        "read_srd_actor_weapon_spell",
        state,
        actor_state="hero",
        lasting_state="state",
        weapon="dagger",
    ).result["context"]
    assert other["_weapon_spell_bonus"] == 0


def test_magic_weapon_rejects_duplicate_and_intrinsically_magical_inventory_identity():
    for inventory, native_magic in ((["spear", "spear"], []), (["spear"], ["spear"])):
        state, data, parameters = _case(
            "magic_weapon", targets=["hero"], actor_spell_weapon="spear"
        )
        state[f"{SRD}.entities"]["hero"].update(
            inventory=inventory, intrinsic_magic_weapon_ids=native_magic
        )
        with pytest.raises(RulesetPreconditionRejected, match="Duplicate|nonmagical"):
            _cast(state, data, parameters)


@pytest.mark.parametrize("level,expression", [(1, "1d8"), (5, "1d10"), (11, "1d12"), (17, "2d6")])
def test_shillelagh_derives_source_ability_die_and_chosen_force_damage(level, expression):
    from tests.srd_full.test_srd_attack_budget import _attack

    state, data, parameters = _case("shillelagh", actor_spell_weapon="quarterstaff")
    parameters.pop("target")
    data["level"] = level
    data["classes"][0]["level"] = level
    inventory = state[f"{SRD}.entities"]["hero"]["inventory"] + ["quarterstaff"]
    state[f"{SRD}.entities"]["hero"].update(inventory=inventory, ammunition={}, fighter_level=0)
    _run("record_srd_control_held_items", state, target_state="hero", held_items=["quarterstaff"])
    _cast(state, data, parameters)
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="hero",
        current_actor="hero",
        action_available=True,
        normal_action_spent=False,
    )
    context = _attack(state, data, weapon="quarterstaff", shillelagh_damage_type="force").result[
        "context"
    ]
    assert context["_actual_weapon_attack_ability"] == "wisdom"
    assert context["_ability_modifier"] == 4
    assert context["_damage_expression"] == expression
    assert context["_weapon_damage_type"] == "force"


def test_shillelagh_ends_permanently_on_release_then_reholding_does_not_resume():
    state, data, parameters = _case("shillelagh", actor_spell_weapon="quarterstaff")
    parameters.pop("target")
    state[f"{SRD}.entities"]["hero"]["inventory"].append("quarterstaff")
    _run("record_srd_control_held_items", state, target_state="hero", held_items=["quarterstaff"])
    _cast(state, data, parameters)
    _run("record_srd_control_held_items", state, target_state="hero", held_items=[])
    _run("record_srd_control_held_items", state, target_state="hero", held_items=["quarterstaff"])
    assert (
        _run(
            "read_srd_actor_weapon_spell",
            state,
            actor_state="hero",
            lasting_state="state",
            weapon="quarterstaff",
        ).result["context"]["_weapon_spell_die"]
        is None
    )


def test_public_glibness_retains_later_truth_effect_across_retry_reopen_and_expiry(tmp_path):
    path = tmp_path / "glibness.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "glibness")
        parameters.pop("target", None)
        command = ActionCommand(
            campaign_id="casting",
            action_id="glibness",
            idempotency_key="glibness",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        mechanics = StatelessMcpMechanics(owner)
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(command) == receipt
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)
        truth = mechanics.execute_action(
            replace(
                command,
                action_id="truth",
                idempotency_key="truth",
                expected_revision=saved.revision,
                tool_name="resolve_srd_magical_truth",
                parameters={"actor_state": "hero", "statement_is_truthful": False},
            )
        )
        assert truth.status is ActionStatus.COMMITTED
        assert truth.result["context"]["_magically_detected_truthful"] is True
        elapsed = mechanics.execute_action(
            replace(
                command,
                action_id="elapsed",
                idempotency_key="elapsed",
                expected_revision=owner.snapshot("casting").revision,
                tool_name="advance_srd_lasting_time",
                parameters={"elapsed_rounds": 1200},
            )
        )
        assert elapsed.status is ActionStatus.COMMITTED
        state = deepcopy(dict(owner.snapshot("casting").state))
        assert (
            _run(
                "resolve_srd_magical_truth", state, actor_state="hero", statement_is_truthful=False
            ).result["context"]["_magically_detected_truthful"]
            is False
        )


def test_haste_actual_turn_movement_ac_and_restricted_attack_preserve_normal_action():
    from tests.srd_full.test_srd_attack_budget import _attack

    state, data, parameters = _case("haste", targets=["hero"])
    _cast(state, data, parameters)
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor="hero")
    state[f"{SRD}.entities"]["hero"].update(exhaustion_level=0, inventory=["spear"], ammunition={})
    data["classes"] = [{"id": "fighter", "level": 11}, {"id": "wizard", "level": 6}]
    _run("begin_combat_turn", state, actor="hero")
    assert state[f"{SRD}.combat"]["movement_remaining"] == 60
    ac = _run(
        "resolve_srd_effective_armor_class",
        state,
        target_state="hero",
        lasting_state="state",
        base_armor_class=state[f"{SRD}.entities"]["hero"]["armor_class"],
    ).result["context"]
    assert ac["_effective_armor_class"] == state[f"{SRD}.entities"]["hero"]["armor_class"] + 2
    result = _run(
        "d20_test",
        state,
        actor_state="hero",
        ability="dexterity",
        kind="saving_throw",
        ability_modifier=3,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=10,
        advantage=False,
        disadvantage=False,
    ).result["context"]
    assert result["_effective_advantage"] is True
    _run("choose_srd_haste_action", state, actor_state="hero", use_haste=True)
    _attack(state, data)
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 0
    assert state[f"{SRD}.combat"]["normal_action_spent"] is False
    assert state[f"{SRD}.combat"]["haste_action_available"] is False
    _attack(state, data)
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 2
    assert state[f"{SRD}.combat"]["normal_action_spent"] is True


def test_haste_shutdown_lethargy_ends_at_end_of_next_turn():
    state, data, parameters = _case("haste", targets=["guard"])
    _cast(state, data, parameters)
    _run("end_concentration", state, caster_state="hero")
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["haste_lethargy"] is True
    assert "incapacitated" in guard["conditions"]
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor="guard")
    _run("begin_combat_turn", state, actor="guard")
    assert state[f"{SRD}.combat"]["movement_remaining"] == 0
    assert state[f"{SRD}.combat"]["active_actor_can_act"] is False
    _run("end_combat_turn", state, actor="guard")
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["haste_lethargy"] is False
    assert "incapacitated" not in guard["conditions"]


def _slow_on_hero():
    state, data, parameters = _case("slow", targets=["hero"], forego_save_targets=["hero"])
    state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"] = dict.fromkeys(data["abilities"], 0)
    _cast(state, data, parameters)
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor="hero")
    state[f"{SRD}.entities"]["hero"].update(inventory=["spear"], ammunition={})
    _run("begin_combat_turn", state, actor="hero")
    return state, data


def test_slow_actual_movement_dexterity_ac_reaction_and_one_attack_budget():
    from tests.srd_full.test_srd_attack_budget import _attack

    state, data = _slow_on_hero()
    assert state[f"{SRD}.combat"]["movement_remaining"] == 15
    base_ac = state[f"{SRD}.entities"]["hero"]["armor_class"]
    assert (
        _run(
            "resolve_srd_effective_armor_class",
            state,
            target_state="hero",
            lasting_state="state",
            base_armor_class=base_ac,
        ).result["context"]["_effective_armor_class"]
        == base_ac - 2
    )
    result = _run(
        "d20_test",
        state,
        actor_state="hero",
        ability="dexterity",
        kind="saving_throw",
        ability_modifier=3,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=10,
        advantage=False,
        disadvantage=False,
    ).result["context"]
    assert result["_adjusted_total"] == result["_d20_test"]["total"] + 3 - 2
    with pytest.raises(RulesetPreconditionRejected, match="Slow"):
        _run(
            "consume_srd_reaction",
            state,
            actor_state="hero",
            lasting_state="state",
            reaction="opportunity_attack",
        )
    data["classes"] = [{"id": "fighter", "level": 11}, {"id": "wizard", "level": 6}]
    _attack(state, data)
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 0
    with pytest.raises(RulesetPreconditionRejected, match="Slow"):
        _run("consume_srd_bonus_action", state, actor_state="hero", bonus_action="second_wind")


def test_slow_bonus_action_first_blocks_action_then_real_end_turn_save_ends_only_its_source():
    from tests.srd_full.test_srd_attack_budget import _attack

    state, data = _slow_on_hero()
    _run("consume_srd_bonus_action", state, actor_state="hero", bonus_action="second_wind")
    with pytest.raises(RulesetPreconditionRejected, match="Slow"):
        _attack(state, data)
    state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"]["wisdom"] = 100
    result = _run("end_combat_turn", state, actor="hero").result["context"]
    assert result["_slow_repeat_outcome"] == "success"
    assert state[f"{SRD}.entities"]["hero"]["spell_slow"] is False


def test_slow_actual_somatic_failure_spends_slot_and_action_without_spell_damage(monkeypatch):
    state, data, magic_parameters = _case("magic_missile")
    slow_state, _, slow_parameters = _case("slow", targets=["hero"], forego_save_targets=["hero"])
    for key, value in slow_state[f"{SRD}.entities"]["hero"].items():
        if "material" in key or key == "inventory":
            state[f"{SRD}.entities"]["hero"][key] = deepcopy(value)
    data["spellcasting_grants"][0]["spells"].append("slow")
    data["wizard_spellbook"].append("slow")
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("slow")
    state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"] = dict.fromkeys(data["abilities"], 0)
    _cast(state, data, slow_parameters)
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor="hero")
    _run("begin_combat_turn", state, actor="hero")
    monkeypatch.setattr(dice, "_rng", Random(2))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    before = state[f"{SRD}.entities"]["guard"]["hp"]
    slots = state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"]
    context = _run("cast_spell", state, participant_data=data, **magic_parameters).result["context"]
    assert context["_slow_cast_failure_roll"]["results"] == [1]
    assert context["_spell_cast_failed"] is True
    assert state[f"{SRD}.entities"]["guard"]["hp"] == before
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == slots - 1
    assert state[f"{SRD}.combat"]["normal_action_spent"] is True


def test_slow_saved_pending_cast_gesture_roll_cannot_reroll_on_resume(monkeypatch):
    state, data = _slow_on_hero()
    state[f"{SRD}.state"]["pending_srd_actions"] = {
        "attempt": {
            "kind": "spell_cast",
            "actor_state_id": "hero",
            "parameters": {"spell": "magic_missile"},
        }
    }
    monkeypatch.setattr(dice, "_rng", Random(2))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    first = _run(
        "resolve_srd_slow_cast_failure",
        state,
        caster_state="hero",
        spell="magic_missile",
        has_somatic_component=True,
        pending_action_id="attempt",
    ).result["context"]
    assert first["_slow_cast_failed"] is True
    monkeypatch.setattr(dice, "roll", lambda *a, **kw: pytest.fail("Saved gesture roll rerolled"))
    second = _run(
        "resolve_srd_slow_cast_failure",
        state,
        caster_state="hero",
        spell="magic_missile",
        has_somatic_component=True,
        pending_action_id="attempt",
    ).result["context"]
    assert second["_slow_cast_failure_roll"] == first["_slow_cast_failure_roll"]


def _resized_hero(mode):
    state, data, parameters = _case("enlarge_reduce", target="hero", actor_spell_choice=mode)
    data["size_rank"] = 2
    state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"] = dict.fromkeys(data["abilities"], 0)
    _run(
        "record_srd_actor_base_size", state, participant_data=data, actor="hero", actor_state="hero"
    )
    _cast(state, data, parameters)
    return state, data


@pytest.mark.parametrize(
    "mode,rank,advantage,disadvantage",
    [
        ("enlarge", 3, True, False),
        ("reduce", 1, False, True),
    ],
)
def test_resizing_projects_actual_size_strength_and_restores_at_source_shutdown(
    mode, rank, advantage, disadvantage
):
    state, data = _resized_hero(mode)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["size_rank"] == rank
    for kind in ("ability_check", "saving_throw"):
        context = _run(
            "d20_test",
            state,
            actor_state="hero",
            ability="strength",
            kind=kind,
            ability_modifier=3,
            proficiency_bonus=0,
            proficiency_factor="none",
            circumstantial_modifier=0,
            target_number=12,
            advantage=False,
            disadvantage=False,
        ).result["context"]
        assert context["_effective_advantage"] is advantage
        assert context["_effective_disadvantage"] is disadvantage
    assert _run("read_srd_carried_item_size", state, actor_state="hero", item="spear").result[
        "context"
    ]["_carried_item_size_adjustment"] == (1 if mode == "enlarge" else -1)
    _run("end_concentration", state, caster_state="hero")
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["size_rank"] == 2
    assert hero["actor_size_projected"] is False


@pytest.mark.parametrize("mode", ["enlarge", "reduce"])
def test_resized_weapon_and_unarmed_hit_damage_use_actual_d4(mode, monkeypatch):
    from tests.srd_full.test_srd_attack_budget import _attack
    from tests.srd_full.test_srd_unarmed_creatures import _strike

    state, data = _resized_hero(mode)
    state[f"{SRD}.entities"]["guard"].update(hp=1000, max_hp=1000, temp_hp=0, size_rank=2)
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor="hero")
    _run("begin_combat_turn", state, actor="hero")
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    result = _attack(state, data).result["context"]
    assert result["_attack_outcome"] != "miss"
    assert 1 <= result["_size_attack_roll"]["total"] <= 4
    expected = result["_styled_weapon_damage"] + result["_damage_modifier"]
    expected = (
        expected + result["_size_attack_roll"]["total"]
        if mode == "enlarge"
        else max(1, expected - result["_size_attack_roll"]["total"])
    )
    assert 1000 - state[f"{SRD}.entities"]["guard"]["hp"] == expected
    state[f"{SRD}.combat"].update(
        action_available=True, normal_action_spent=False, attack_available=True, attacks_remaining=0
    )
    result = _strike(state, data, "damage").result["context"]
    assert result["_unarmed_success"] is True
    assert result["_unarmed_damage"] == (
        4 + result["_size_attack_roll"]["total"]
        if mode == "enlarge"
        else max(1, 4 - result["_size_attack_roll"]["total"])
    )


def test_resized_thrown_weapon_returns_to_normal_after_miss_and_dropped_item_stays_normal():
    from tests.srd_full.test_srd_attack_budget import _attack

    state, data = _resized_hero("enlarge")
    state[f"{SRD}.entities"]["guard"]["armor_class"] = 100
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor="hero")
    _run("begin_combat_turn", state, actor="hero")
    _attack(state, data, attack_mode="thrown", attack_distance=15)
    assert (
        _run("read_srd_carried_item_size", state, actor_state="hero", item="spear").result[
            "context"
        ]["_carried_item_size_adjustment"]
        == 0
    )
    _run(
        "record_srd_control_held_items",
        state,
        target_state="hero",
        held_items=["dagger"],
        held_attack_names=[],
    )
    _run("drop_srd_control_held_items", state, target_state="hero")
    hero = state[f"{SRD}.entities"]["hero"]
    assert "dagger" not in hero["inventory"]
    hero["inventory"].append("dagger")
    _run("refresh_srd_timed_condition_target", state, target_state="hero", lasting_state="state")
    assert (
        _run("read_srd_carried_item_size", state, actor_state="hero", item="dagger").result[
            "context"
        ]["_carried_item_size_adjustment"]
        == 0
    )


def test_resized_actor_can_grapple_using_its_projected_size_and_rejects_unrelated_pc_size():
    from tests.srd_full.test_srd_unarmed_creatures import _strike

    state, data = _resized_hero("enlarge")
    state[f"{SRD}.entities"]["guard"]["size_rank"] = 4
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor="hero")
    _run("begin_combat_turn", state, actor="hero")
    assert (
        _strike(state, data, "grapple", target_forego_save=True).result["context"][
            "_unarmed_success"
        ]
        is True
    )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="own validated Character"):
        _run(
            "record_srd_actor_base_size",
            state,
            participant_data=data,
            actor="hero",
            actor_state="guard",
        )
    assert state == before


def test_ordinary_condition_subject_does_not_materialize_advanced_projection_fields():
    state, data, _ = _case("calm_emotions")
    _run(
        "resolve_srd_timed_actor_condition_suppression",
        state,
        target_state="guard",
        lasting_state="state",
    )
    assert "spell_haste" not in state[f"{SRD}.entities"]["guard"]
    assert "has_actor_weapon_modifiers" not in state[f"{SRD}.entities"]["guard"]


@pytest.mark.parametrize(
    "spell,slot,weapon",
    [
        ("magic_weapon", "spell_slot_6", "spear"),
        ("shillelagh", "cantrip", "quarterstaff"),
    ],
)
def test_actor_source_retains_actual_cast_level_independently_of_actor_level(spell, slot, weapon):
    state, data, parameters = _case(
        spell, targets=["hero"], actor_spell_weapon=weapon, slot_resource=slot
    )
    state[f"{SRD}.entities"]["hero"]["inventory"].append("quarterstaff")
    if spell == "shillelagh":
        parameters.pop("target")
        _run("record_srd_control_held_items", state, target_state="hero", held_items=[weapon])
    _cast(state, data, parameters)
    record = next(r for r in state[f"{SRD}.state"]["turn_effects"] if r["spell"] == spell)
    assert record["spell_level"] == (6 if spell == "magic_weapon" else 0)
    assert record["casting_level"] == 17


def test_public_haste_reopens_retries_and_spends_only_its_extra_disengage_action(tmp_path):
    path = tmp_path / "haste.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "haste")
        command = ActionCommand(
            campaign_id="casting",
            action_id="haste",
            idempotency_key="haste",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters | {"targets": ["hero"]},
        )
        mechanics = StatelessMcpMechanics(owner)
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(command) == receipt
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        mechanics = StatelessMcpMechanics(owner)

        def act(name, params):
            result = mechanics.execute_action(
                replace(
                    command,
                    action_id=name,
                    idempotency_key=name,
                    expected_revision=owner.snapshot("casting").revision,
                    tool_name=name,
                    parameters=params,
                )
            )
            assert result.status is ActionStatus.COMMITTED
            return result

        act(
            "start_combat_encounter_entities",
            {
                "first_actor": "hero",
                "second_actor": "guard",
                "first_surprised": False,
                "second_surprised": False,
                "tie_winner": "first",
            },
        )
        act("begin_combat_turn", {})
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["movement_remaining"] == 60
        act("choose_srd_haste_action", {"actor_state": "hero", "use_haste": True})
        act("take_disengage_action", {"actor": "hero", "actor_state": "hero"})
        combat = owner.snapshot("casting").state[f"{SRD}.combat"]
        assert combat["disengaged_actor"] == "hero"
        assert combat["normal_action_spent"] is False
        assert combat["haste_action_available"] is False
        act("end_concentration", {"caster_state": "hero"})
        assert (
            "incapacitated"
            in owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]["conditions"]
        )
