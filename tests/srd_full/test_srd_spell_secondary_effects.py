"""Spell consequences must affect later actions, not stop at the initial damage roll."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case


def test_fixed_healing_pool_validates_allocations_then_restores_only_healed_targets():
    state, data, parameters = _spell_case("mass_heal")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    creatures = state[f"{SRD}.entities"]
    creatures["guard"].update(hp=5, max_hp=20, temp_hp=0,
                              conditions=["blinded", "deafened", "poisoned"])
    creatures["other"].update(hp=10, max_hp=20, temp_hp=0,
                              conditions=["blinded", "poisoned"])
    choices = parameters | {"targets": ["guard", "other"],
                            "healing_allocations": [10, 0]}
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="pool maximum"):
        _run("cast_spell_outside_combat", state, participant_data=data,
             **(choices | {"healing_allocations": [500, 201]}))
    assert state == before
    with pytest.raises(RulesetPreconditionRejected, match="exactly one"):
        _run("cast_spell_outside_combat", state, participant_data=data,
             **(choices | {"healing_allocations": [10]}))
    assert state == before
    state[f"{SRD}.entities"]["other"]["is_alive"] = False
    late_invalid = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="living creature"):
        _run("cast_spell_outside_combat", state, participant_data=data,
             **(choices | {"healing_allocations": [10, 8]}))
    assert state == late_invalid
    state[f"{SRD}.entities"]["other"]["is_alive"] = True

    cast = _run("cast_spell_outside_combat", state, participant_data=data,
                **choices).result
    creatures = state[f"{SRD}.entities"]
    assert creatures["guard"]["hp"] == 15
    assert creatures["guard"]["conditions"] == []
    assert creatures["other"]["hp"] == 10
    assert creatures["other"]["conditions"] == ["blinded", "poisoned"]
    assert creatures["hero"]["resources"]["spell_slot_9"] == 2
    assert [row["label"] for row in cast["result_card"]["details"]] == ["Guard", "Other"]


def test_healing_pool_handles_64_conditioned_targets_within_operation_budget():
    state, data, parameters = _spell_case("mass_heal")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    creatures = state[f"{SRD}.entities"]
    creatures["guard"].update(hp=10, max_hp=20, temp_hp=0,
                              conditions=["blinded", "deafened", "poisoned"])
    targets = ["guard"] + [f"pool-{index}" for index in range(63)]
    for identity in targets[1:]:
        creatures[identity] = deepcopy(creatures["guard"])
        creatures[identity]["name"] = identity

    cast = _run("cast_spell_outside_combat", state, participant_data=data,
                **(parameters | {"targets": targets,
                                "healing_allocations": [1] * 64})).result
    assert len(cast["result_card"]["details"]) == 64
    assert state[f"{SRD}.entities"]["pool-62"]["hp"] == 11
    assert state[f"{SRD}.entities"]["pool-62"]["conditions"] == []


@pytest.mark.parametrize("save_modifier,failed", [(-100, True), (100, False)])
def test_harm_reduces_saved_hit_point_maximum_only_after_failed_save(
    monkeypatch, save_modifier, failed,
):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case("harm")
    target = state[f"{SRD}.entities"]["guard"]
    target.update(hp=1000, max_hp=1000, temp_hp=5)
    target["saving_throw_modifiers"]["constitution"] = save_modifier

    result = _run("cast_spell", state, participant_data=data, **parameters).result
    assert result["context"]["_spell_save_outcome"] == ("failure" if failed else "success")
    damage_taken = 1000 - target["hp"] + 5 - target["temp_hp"]
    assert target["max_hp"] == (max(1, 1000 - damage_taken) if failed else 1000)
    assert ("Hit Point maximum" in str(result["result_card"])) is failed


def test_harm_cannot_reduce_hit_point_maximum_below_one(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case("harm")
    target = state[f"{SRD}.entities"]["guard"]
    target.update(hp=10, max_hp=10, temp_hp=0)
    target["saving_throw_modifiers"]["constitution"] = -100
    _run("cast_spell", state, participant_data=data, **parameters)
    assert state[f"{SRD}.entities"]["guard"]["max_hp"] == 1


@pytest.mark.parametrize("damage,temporary_hp", [(20, 0), (200, 5)])
def test_timed_death_prevention_intercepts_zero_hit_points_once(damage, temporary_hp):
    state, data, parameters = _spell_case("death_ward")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    target = state[f"{SRD}.entities"]["guard"]
    target.update(hp=10, max_hp=20, temp_hp=temporary_hp)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert len(state[f"{SRD}.state"]["turn_effects"]) == 1
    _run("apply_srd_damage", state, target="guard", amount=damage,
         adjustment=0, damage_type="force")
    target = state[f"{SRD}.entities"]["guard"]
    assert target["hp"] == 1
    assert target["is_alive"] is True
    assert state[f"{SRD}.state"]["turn_effects"] == []
    _run("apply_srd_damage", state, target="guard", amount=5,
         adjustment=0, damage_type="force")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 0


def test_timed_death_prevention_expires_before_later_damage():
    state, data, parameters = _spell_case("death_ward")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    target = state[f"{SRD}.entities"]["guard"]
    target.update(hp=10, max_hp=20, temp_hp=0)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    _run("advance_srd_lasting_time", state, elapsed_rounds=4800)
    assert state[f"{SRD}.state"]["turn_effects"] == []
    _run("apply_srd_damage", state, target="guard", amount=20,
         adjustment=0, damage_type="force")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 0


def test_timed_armor_class_floor_tracks_base_armor_and_expires():
    state, data, parameters = _spell_case("barkskin")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    target = state[f"{SRD}.entities"]["guard"]
    target["armor_class"] = 12
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="willing"):
        _run("cast_spell_outside_combat", state, participant_data=data,
             **(parameters | {"target_willing": False}))
    assert state == before

    cast = _run("cast_spell_outside_combat", state,
                participant_data=data, **parameters).result
    assert "AC at least 17" in str(cast["result_card"])
    target = state[f"{SRD}.entities"]["guard"]
    assert target["spell_armor_class_floor"] == 17
    assert _run("resolve_srd_effective_armor_class", state,
                target_state="guard", lasting_state="state",
                base_armor_class=12).result["context"]["_effective_armor_class"] == 17
    target["armor_class"] = 19
    assert _run("resolve_srd_effective_armor_class", state,
                target_state="guard", lasting_state="state",
                base_armor_class=19).result["context"]["_effective_armor_class"] == 19
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    assert state[f"{SRD}.entities"]["guard"]["spell_armor_class_floor"] == 0
    assert _run("resolve_srd_effective_armor_class", state,
                target_state="guard", lasting_state="state",
                base_armor_class=12).result["context"]["_effective_armor_class"] == 12


def test_timed_condition_defense_cures_then_applies_typed_resistance_and_save_advantage():
    state, data, parameters = _spell_case("protection_from_poison")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(conditions=["poisoned"], hp=100, max_hp=100, temp_hp=0)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    guard = state[f"{SRD}.entities"]["guard"]
    assert "poisoned" not in guard["conditions"]
    assert guard["timed_damage_resistances"] == ["poison"]
    assert guard["timed_condition_save_advantages"] == ["poisoned"]

    save = {
        "actor_state": "guard", "ability": "constitution", "kind": "saving_throw",
        "ability_modifier": 0, "proficiency_bonus": 0, "proficiency_factor": "none",
        "circumstantial_modifier": 0, "target_number": 15, "advantage": False,
        "disadvantage": False, "forego_save": False,
    }
    assert _run("d20_test", state, **(save | {"save_avoids_condition": "poisoned"})
                ).result["context"]["_effective_advantage"] is True
    assert _run("d20_test", state, **(save | {"save_avoids_condition": "blinded"})
                ).result["context"]["_effective_advantage"] is False
    _run("apply_srd_damage", state, target="guard", amount=9,
         adjustment=0, damage_type="poison")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 96
    _run("apply_srd_composite_damage", state, target="guard", component_1_amount=9,
         component_1_damage_type="poison", component_2_amount=3,
         component_2_damage_type="fire")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 89
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["timed_damage_resistances"] == []
    assert guard["timed_condition_save_advantages"] == []
    assert _run("d20_test", state, **(save | {"save_avoids_condition": "poisoned"})
                ).result["context"]["_effective_advantage"] is False
    _run("apply_srd_damage", state, target="guard", amount=9,
         adjustment=0, damage_type="poison")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 80


def test_concentration_damage_reduction_uses_selected_type_once_per_turn_then_expires(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case("resistance")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(hp=100, max_hp=100, temp_hp=0)
    def hit_points():
        return state[f"{SRD}.entities"]["guard"]["hp"]

    cast = _run("cast_spell_outside_combat", state, participant_data=data,
                **(parameters | {"selected_damage_type": "fire"})).result
    assert "1d4 reduction" in str(cast["result_card"])
    before = hit_points()
    _run("apply_srd_damage", state, target="guard", amount=10,
         adjustment=0, damage_type="fire")
    first_loss = before - hit_points()
    assert 6 <= first_loss <= 9
    _run("apply_srd_damage", state, target="guard", amount=10,
         adjustment=0, damage_type="fire")
    assert hit_points() == before - first_loss - 10
    _run("apply_srd_damage", state, target="guard", amount=10,
         adjustment=0, damage_type="cold")
    assert hit_points() == before - first_loss - 20
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    before = hit_points()
    _run("apply_srd_composite_damage", state, target="guard",
         component_1_amount=10, component_1_damage_type="fire",
         component_2_amount=10, component_2_damage_type="cold")
    assert 16 <= before - hit_points() <= 19
    _run("advance_srd_lasting_time", state, elapsed_rounds=9)
    before = hit_points()
    _run("apply_srd_damage", state, target="guard", amount=10,
         adjustment=0, damage_type="fire")
    assert hit_points() == before - 10


def test_damage_reduction_precedes_resistance_and_immunity_does_not_spend_its_use(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case("resistance")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    target = state[f"{SRD}.entities"]["guard"]
    target.update(hp=100, max_hp=100, temp_hp=0, damage_immunities=["fire"],
                  damage_resistances=["fire"])
    _run("cast_spell_outside_combat", state, participant_data=data,
         **(parameters | {"selected_damage_type": "fire"}))
    _run("apply_srd_damage", state, target="guard", amount=10,
         adjustment=0, damage_type="fire")
    target = state[f"{SRD}.entities"]["guard"]
    assert target["hp"] == 100
    assert target.get("damage_reduction_used_tick") is None
    target["damage_immunities"] = []
    _run("apply_srd_damage", state, target="guard", amount=10,
         adjustment=0, damage_type="fire")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 97


def test_timed_hit_point_bonus_uses_strongest_same_spell_until_each_source_expires():
    state, data, parameters = _spell_case("aid")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    parameters["targets"] = ["guard", "other"]
    _run("cast_spell_outside_combat", state, participant_data=data,
         **(parameters | {"slot_resource": "spell_slot_3"}))
    assert [(state[f"{SRD}.entities"][who]["hp"],
             state[f"{SRD}.entities"][who]["max_hp"]) for who in ("guard", "other")] == [
        (29, 30), (29, 30),
    ]
    _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state[f"{SRD}.entities"]["guard"]["spell_hit_point_bonus"] == 10
    _run("advance_srd_lasting_time", state, elapsed_rounds=4700)
    guard = state[f"{SRD}.entities"]["guard"]
    assert (guard["hp"], guard["max_hp"], guard["spell_hit_point_bonus"]) == (24, 25, 5)
    _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    guard = state[f"{SRD}.entities"]["guard"]
    assert (guard["hp"], guard["max_hp"], guard["spell_hit_point_bonus"]) == (19, 20, 0)


def test_timed_hit_point_bonus_stacks_distinct_sources_and_keeps_remaining_duration():
    state, _, _ = _spell_case("aid")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    for spell, rounds, amount in (("aid", 10, 5), ("other_hp_source", 1, 7)):
        _run("install_srd_timed_hit_point_bonus", state, target_state="guard",
             lasting_state="state", caster_state_id="hero", spell=spell,
             duration_rounds=rounds, hit_point_bonus=amount)
    guard = state[f"{SRD}.entities"]["guard"]
    assert (guard["hp"], guard["max_hp"], guard["spell_hit_point_bonus"]) == (31, 32, 12)
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    guard = state[f"{SRD}.entities"]["guard"]
    assert (guard["hp"], guard["max_hp"], guard["spell_hit_point_bonus"]) == (24, 25, 5)
    _run("advance_srd_lasting_time", state, elapsed_rounds=9)
    guard = state[f"{SRD}.entities"]["guard"]
    assert (guard["hp"], guard["max_hp"], guard["spell_hit_point_bonus"]) == (19, 20, 0)


def test_expiring_hit_point_bonus_ends_concentration_when_caster_reaches_zero():
    state, data, parameters = _spell_case("guidance")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _run("cast_spell_outside_combat", state, participant_data=data,
         **(parameters | {"selected_skill": "athletics"}))
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "guidance"
    _run("install_srd_timed_hit_point_bonus", state, target_state="hero",
         lasting_state="state", caster_state_id="hero", spell="other_hp_source",
         duration_rounds=1, hit_point_bonus=5)
    state[f"{SRD}.entities"]["hero"]["hp"] = 1
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["hp"] == 0 and "unconscious" in hero["conditions"]
    assert hero["concentration_spell_id"] is None


def test_timed_hit_point_bonus_rejects_extra_or_dead_targets_atomically():
    state, data, parameters = _spell_case("aid")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.entities"]["fourth"] = deepcopy(state[f"{SRD}.entities"]["guard"])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="many creatures"):
        _run("cast_spell_outside_combat", state, participant_data=data,
             **(parameters | {"targets": ["hero", "guard", "other", "fourth"]}))
    assert state == before
    state[f"{SRD}.entities"]["other"].update(is_alive=False, hp=0)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="living creature"):
        _run("cast_spell_outside_combat", state, participant_data=data,
             **(parameters | {"targets": ["guard", "other"]}))
    assert state == before


@pytest.mark.parametrize("creature_kind", ["character", "monster"])
def test_timed_hit_point_bonus_expiry_resolves_zero_hp_without_damage(creature_kind):
    state, _, _ = _spell_case("aid")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    target = state[f"{SRD}.entities"]["guard"]
    target.update(creature_kind=creature_kind, hp=0, max_hp=20,
                  conditions=["unconscious"], is_alive=True)
    _run("install_srd_timed_hit_point_bonus", state, target_state="guard",
         lasting_state="state", caster_state_id="hero", spell="aid",
         duration_rounds=4800, hit_point_bonus=5)
    target = state[f"{SRD}.entities"]["guard"]
    assert (target["hp"], target["max_hp"]) == (5, 25)
    assert "unconscious" not in target["conditions"]
    target["hp"] = 1
    _run("advance_srd_lasting_time", state, elapsed_rounds=4800)
    target = state[f"{SRD}.entities"]["guard"]
    assert (target["hp"], target["max_hp"]) == (0, 20)
    if creature_kind == "character":
        assert target["is_alive"] is True and "unconscious" in target["conditions"]
    else:
        assert target["is_alive"] is False and "dead" in target["conditions"]


@pytest.mark.parametrize("tool,damage", [
    ("apply_srd_composite_damage", {
        "component_1_amount": 9, "component_1_damage_type": "fire",
        "component_2_amount": 9, "component_2_damage_type": "cold",
    }),
    ("apply_srd_multi_attack_damage", {
        "amount": 18, "damage_type": "force", "critical_hit": False,
    }),
])
def test_death_prevention_covers_composite_and_multi_attack_damage(tool, damage):
    state, data, parameters = _spell_case("death_ward")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.entities"]["guard"].update(hp=10, max_hp=20, temp_hp=0)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    _run(tool, state, target="guard", **damage)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1
    assert state[f"{SRD}.entities"]["guard"]["is_alive"] is True
    assert state[f"{SRD}.state"]["turn_effects"] == []


def test_timed_death_prevention_negates_instant_death_then_is_consumed():
    state, data, parameters = _spell_case("power_word_kill")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(hp=75, max_hp=100, temp_hp=0)
    _run("install_srd_timed_death_prevention", state, target_state="guard",
         lasting_state="state", spell="death_ward", duration_rounds=4800,
         replacement_hit_points=1)
    result = _run("cast_spell_outside_combat", state, participant_data=data,
                  **parameters).result
    assert state[f"{SRD}.entities"]["guard"]["is_alive"] is True
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 75
    assert state[f"{SRD}.state"]["turn_effects"] == []
    assert "Death prevented" in str(result["result_card"])
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state[f"{SRD}.entities"]["guard"]["is_alive"] is False


def test_timed_death_prevention_negates_exhaustion_death_once():
    state, data, parameters = _spell_case("death_ward")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{SRD}.entities"]["guard"]["exhaustion_level"] = 5
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    _run("gain_srd_exhaustion", state, target="guard", levels=1, cause="other")
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["is_alive"] is True
    assert guard["exhaustion_level"] == 5
    assert state[f"{SRD}.state"]["turn_effects"] == []
    _run("gain_srd_exhaustion", state, target="guard", levels=1, cause="other")
    assert state[f"{SRD}.entities"]["guard"]["is_alive"] is False


def test_vitriolic_sphere_delays_damage_only_for_failed_saves(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case("vitriolic_sphere")
    entities = state[f"{SRD}.entities"]
    for target in ("guard", "other"):
        entities[target].update(hp=1000, max_hp=1000, temp_hp=0)
    parameters["targets"] = ["guard", "other"]

    cast = _run("cast_spell", state, participant_data=data, **parameters).result
    effects = state[f"{SRD}.state"]["turn_effects"]
    assert len(effects) == 1
    assert effects[0]["target"] == "other"
    assert effects[0]["dice_count"] == 5
    assert effects[0]["damage_die"] == "d4"
    assert "5d4" in str(cast["result_card"])

    guard_before, other_before = (state[f"{SRD}.entities"][target]["hp"]
                                  for target in ("guard", "other"))
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    tick = _run("advance_srd_lasting_time", state, elapsed_rounds=1).result
    assert tick["context"]["_turn_effect_count"] == 1
    assert state[f"{SRD}.entities"]["guard"]["hp"] == guard_before
    assert state[f"{SRD}.entities"]["other"]["hp"] < other_before
    assert state[f"{SRD}.state"]["turn_effects"] == []


@pytest.mark.parametrize("hit_points", [100, 1])
def test_force_projectiles_allocate_exactly_and_keep_each_targets_damage(
    monkeypatch, hit_points,
):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case("magic_missile")
    for target in ("guard", "other"):
        state[f"{SRD}.entities"][target].update(
            hp=hit_points, max_hp=hit_points, temp_hp=0,
        )
    parameters["projectile_targets"] = ["guard", "other", "other"]

    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="exactly one target"):
        _run("cast_spell", state, participant_data=data,
             **(parameters | {"projectile_targets": ["guard", "other"]}))
    assert state == before

    cast = _run("cast_spell", state, participant_data=data, **parameters).result
    guard = state[f"{SRD}.entities"]["guard"]
    other = state[f"{SRD}.entities"]["other"]
    if hit_points == 100:
        assert 2 <= 100 - guard["hp"] <= 5
        assert 4 <= 100 - other["hp"] <= 10
    else:
        assert guard["hp"] == other["hp"] == 0
        assert guard["is_alive"] is other["is_alive"] is False
    assert [row["label"] for row in cast["result_card"]["details"]] == [
        "Guard", "Other",
    ]


def test_timed_speed_bonus_upcast_targets_and_expires_with_saved_time():
    state, data, parameters = _spell_case("longstrider")
    parameters.update(targets=["guard", "other"], slot_resource="spell_slot_2")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="that many creatures"):
        _run("cast_spell", state, participant_data=data,
             **(parameters | {"slot_resource": "spell_slot_1"}))
    assert state == before

    cast = _run("cast_spell", state, participant_data=data, **parameters).result
    assert [row["label"] for row in cast["result_card"]["details"]] == [
        "Guard", "Other",
    ]
    for target in ("guard", "other"):
        assert state[f"{SRD}.entities"][target]["speed"] == 40
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _run("advance_srd_lasting_time", state, elapsed_rounds=599)
    assert state[f"{SRD}.entities"]["guard"]["speed"] == 40
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert state[f"{SRD}.entities"]["guard"]["speed"] == 30
    assert state[f"{SRD}.entities"]["other"]["speed"] == 30
    assert state[f"{SRD}.state"]["turn_effects"] == []


def test_timed_speed_sources_do_not_stack_and_refresh_active_movement():
    state, data, parameters = _spell_case("longstrider")
    _run("cast_spell", state, participant_data=data,
         **(parameters | {"targets": ["hero"]}))
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["speed"] == 40
    assert state[f"{SRD}.combat"]["movement_remaining"] == 40

    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _run("install_srd_timed_speed_bonus", state, target_state="hero",
         lasting_state="state", caster_state_id="hero", spell="longstrider",
         duration_rounds=2, speed_bonus=10)
    assert state[f"{SRD}.entities"]["hero"]["speed"] == 40
    _run("advance_srd_lasting_time", state, elapsed_rounds=2)
    assert state[f"{SRD}.entities"]["hero"]["speed"] == 40
    _run("advance_srd_lasting_time", state, elapsed_rounds=598)
    assert state[f"{SRD}.entities"]["hero"]["speed"] == 30


def test_timed_speed_bonus_survives_equipment_speed_recalculation():
    state, data, parameters = _spell_case("longstrider")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    hero = state[f"{SRD}.entities"]["hero"]
    hero["inventory"].append("plate_armor")
    data["abilities"]["strength"] = 14
    _run("cast_spell_outside_combat", state, participant_data=data,
         **(parameters | {"targets": ["hero"]}))
    assert state[f"{SRD}.entities"]["hero"]["speed"] == 40
    _run("change_srd_equipment", state, participant_data=data,
         actor="hero", actor_state="hero", slot="armor",
         item=[{"inventory_item": "plate_armor", "armor_type": "plate_armor",
                "magic_bonus": 0}])
    assert state[f"{SRD}.entities"]["hero"]["speed"] == 30
    _run("change_srd_equipment", state, participant_data=data,
         actor="hero", actor_state="hero", slot="armor", item=[])
    assert state[f"{SRD}.entities"]["hero"]["speed"] == 40


@pytest.mark.parametrize("save_modifier,failed", [(-100, True), (100, False)])
def test_vicious_mockery_penalizes_only_the_next_attack_after_failed_save(
    monkeypatch, save_modifier, failed,
):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case("vicious_mockery")
    guard = state[f"{SRD}.entities"]["guard"]
    guard["saving_throw_modifiers"]["wisdom"] = save_modifier
    cast = _run("cast_spell", state, participant_data=data, **parameters).result
    assert cast["context"]["_spell_save_outcome"] == ("failure" if failed else "success")
    assert ("Disadvantage" in str(cast["result_card"])) is failed

    def attack():
        return _run(
            "d20_test", state, actor_state="guard", ability="strength", kind="attack_roll",
            ability_modifier=0, proficiency_bonus=0, proficiency_factor="none",
            circumstantial_modifier=0, target_number=10, advantage=True, disadvantage=False,
            forego_save=False,
        ).result["context"]

    first = attack()
    assert first["_effective_disadvantage"] is failed
    assert len(first["_d20_test"]["results"]) == (1 if failed else 2)
    assert attack()["_effective_disadvantage"] is False
    assert state[f"{SRD}.state"].get("turn_effects", []) == []


@pytest.mark.parametrize("hit", [True, False], ids=["hit", "miss"])
def test_ray_of_sickness_poison_is_source_owned_and_expires(monkeypatch, hit):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case("ray_of_sickness")
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(hp=1000, max_hp=1000, armor_class=1 if hit else 99)
    cast = _run("cast_spell", state, participant_data=data, **parameters).result
    assert cast["context"]["_spell_attack_outcome"] == ("success" if hit else "failure")
    assert ("poisoned" in state[f"{SRD}.entities"]["guard"]["conditions"]) is hit
    if not hit:
        assert state[f"{SRD}.state"].get("turn_effects", []) == []
        return
    assert "Poisoned" in str(cast["result_card"])
    _run("end_combat_turn", state)
    state[f"{SRD}.combat"]["current_actor"] = "guard"
    _run("begin_combat_turn", state)
    _run("end_combat_turn", state)
    state[f"{SRD}.combat"]["current_actor"] = "hero"
    _run("begin_combat_turn", state)
    assert "poisoned" in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("end_combat_turn", state)
    assert "poisoned" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.state"]["turn_effects"] == []


def test_restoration_clears_ray_of_sickness_source_before_its_natural_expiry():
    state, data, parameters = _spell_case("ray_of_sickness")
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(hp=1000, max_hp=1000, armor_class=1)
    _run("cast_spell", state, participant_data=data, **parameters)
    assert "poisoned" in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("remove_srd_condition", state, target="guard", condition="poisoned")
    assert "poisoned" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.state"]["turn_effects"] == []
    _run("project_srd_turn_modifiers", state, target_state="guard", lasting_state="state")
    assert "poisoned" not in state[f"{SRD}.entities"]["guard"]["conditions"]


@pytest.mark.parametrize("spell,sign", [("bless", 1), ("bane", -1), ("enhance_ability", 0)])
def test_self_cast_d20_effect_applies_to_later_roll_and_leaves_on_concentration_end(
    monkeypatch, spell, sign,
):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case(spell)
    state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"] = dict.fromkeys(
        data["abilities"], 0,
    )
    parameters.update(targets=["hero", "guard"])
    if spell == "bane":
        parameters["forego_save_targets"] = ["hero", "guard"]
    if spell == "enhance_ability":
        parameters.update(strength_targets=["hero", "guard"], slot_resource="spell_slot_3")
    kind = "ability_check" if spell == "enhance_ability" else "saving_throw"
    _run("cast_spell", state, participant_data=data, **parameters)
    for actor in ("hero", "guard"):
        result = _run(
            "d20_test", state, actor_state=actor, ability="strength", kind=kind,
            ability_modifier=0, proficiency_bonus=0, proficiency_factor="none",
            circumstantial_modifier=0, target_number=10, advantage=False, disadvantage=False,
            forego_save=False,
        ).result
        if spell == "enhance_ability":
            assert result["context"]["_effective_advantage"] is True
            assert len(result["context"]["_d20_test"]["results"]) == 2
        else:
            assert 1 <= result["context"]["_combined_modifier"] * sign <= 4
    _run("end_concentration", state, caster_state="hero")
    for actor in ("hero", "guard"):
        assert state[f"{SRD}.entities"][actor]["concentration_d20_modifier_source_ids"] == []
        result = _run(
            "d20_test", state, actor_state=actor, ability="strength", kind=kind,
            ability_modifier=0, proficiency_bonus=0, proficiency_factor="none",
            circumstantial_modifier=0, target_number=10, advantage=False, disadvantage=False,
            forego_save=False,
        ).result
        assert result["context"]["_combined_modifier"] == 0
        assert result["context"]["_effective_advantage"] is False


def _opportunity(state):
    return _run(
        "trigger_opportunity_attack",
        state,
        reactor_state="guard",
        actor_can_see_target=True,
        target_can_see_actor=True,
        attack_distance=5,
        attack_modifier=3,
        damage_dice_count=1,
        damage_die_size="4",
        damage_modifier=1,
        damage_type="bludgeoning",
    )


@pytest.mark.parametrize("hit", [True, False], ids=["hit", "miss"])
def test_attack_secondary_effect_blocks_only_opportunity_until_targets_next_turn(monkeypatch, hit):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case("shocking_grasp")
    state[f"{SRD}.entities"]["hero"]["melee_reach"] = 5
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(
        hp=1000,
        max_hp=1000,
        armor_class=1 if hit else 99,
        melee_reach=5,
        available_reactions=["opportunity_attack", "shield"],
    )
    cast = _run("cast_spell", state, participant_data=data, **parameters)
    assert cast.result["context"]["_spell_attack_outcome"] == ("success" if hit else "failure")
    before = deepcopy(state)
    if hit:
        with pytest.raises(RulesetPreconditionRejected, match="Opportunity Attacks"):
            _opportunity(state)
        assert state == before
        assert state[f"{SRD}.entities"]["guard"]["reaction_available"] is True
        assert "Opportunity Attacks" in str(cast.result["result_card"])
    else:
        assert _opportunity(state).result["status"] == "committed"
        return
    _run("end_combat_turn", state)
    state[f"{SRD}.combat"]["current_actor"] = "guard"
    _run("begin_combat_turn", state)
    _run("end_combat_turn", state)
    state[f"{SRD}.combat"]["current_actor"] = "hero"
    _run("begin_combat_turn", state)
    assert _opportunity(state).result["status"] == "committed"


def test_opportunity_restrictions_preserve_other_reactions_and_expire_all_targets():
    state, data, _ = _spell_case("shocking_grasp")
    state[f"{SRD}.entities"]["hero"]["available_reactions"] = ["shield", "opportunity_attack"]
    for target in ("hero", "guard", "other"):
        _run("block_srd_opportunity_attacks", state, target_state=target, lasting_state="state")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Opportunity Attacks"):
        _run(
            "spend_srd_reaction",
            state,
            participant_data=data,
            actor="hero",
            actor_state="hero",
            reaction="opportunity_attack",
        )
    assert state == before
    assert (
        _run(
            "spend_srd_reaction",
            state,
            participant_data=data,
            actor="hero",
            actor_state="hero",
            reaction="shield",
        ).result["status"]
        == "committed"
    )
    _run("clear_srd_opportunity_block", state, target_state="guard", lasting_state="state")
    assert state[f"{SRD}.state"]["opportunity_blocked_targets"] == ["hero", "other"]
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert state[f"{SRD}.state"]["opportunity_blocked_targets"] == []
    assert all(not e["opportunity_attacks_blocked"] for e in state[f"{SRD}.entities"].values())


@pytest.mark.parametrize("creatures,skip", [(2, False), (3, False), (4, False), (4, True)])
def test_lasting_spell_time_counts_rounds_independently_of_party_size(monkeypatch, creatures, skip):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case("guidance")
    entities = state[f"{SRD}.entities"]
    entities["ally"] = deepcopy(entities["guard"])
    entities["ally"]["name"] = "Ally"
    for entity in entities.values():
        entity["initiative_modifier"] = 0
    entities["hero"]["initiative_modifier"] = 20
    combat = state[f"{SRD}.combat"]
    combat.update(encounter_active=False, turn_active=False)
    if creatures == 2:
        _run(
            "start_combat_encounter_entities",
            state,
            first_actor="hero",
            second_actor="guard",
            first_surprised=False,
            second_surprised=False,
            tie_winner="first",
        )
    else:
        _run(
            "start_group_combat_encounter",
            state,
            combatants=["hero", "guard", "other", "ally"][:creatures],
        )
    if skip:
        state[f"{SRD}.entities"]["ally"]["is_alive"] = False
    _run("begin_combat_turn", state)
    _run(
        "cast_spell", state, participant_data=data, **(parameters | {"selected_skill": "athletics"})
    )
    advance = (
        "advance_combat_encounter_turn" if creatures == 2 else "advance_group_combat_encounter_turn"
    )
    for completed_round in range(1, 11):
        for _ in range(creatures - int(skip)):
            _run("end_combat_turn", state)
            _run(advance, state)
            _run("begin_combat_turn", state)
        assert state[f"{SRD}.combat"]["round"] == completed_round + 1
        assert state[f"{SRD}.state"]["lasting_tick"] == pytest.approx(completed_round * 2)
        current = _run(
            "refresh_srd_concentration_source",
            state,
            source_state="hero",
            target_state="guard",
            lasting_state="state",
        )
        assert current.result["context"]["_source_concentration_active"] is (completed_round < 10)
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
