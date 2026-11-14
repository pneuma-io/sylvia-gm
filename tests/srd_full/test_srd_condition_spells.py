"""Condition spell records share saves, concentration ownership and later combat effects."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_attack_budget import _npc_attack
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case


def _hold_case(monkeypatch, spell="hold_person"):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case(spell)
    for target in ("guard", "other"):
        state[f"{SRD}.entities"][target]["saving_throw_modifiers"].update(
            wisdom=-30, constitution=30,
        )
    parameters["targets"] = ["guard"]
    return state, data, parameters


def test_area_failed_save_blind_repeats_and_releases_individual_target(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case("sunburst")
    for target, modifier in (("guard", -100), ("other", 100)):
        state[f"{SRD}.entities"][target].update(hp=1000, max_hp=1000)
        state[f"{SRD}.entities"][target]["saving_throw_modifiers"]["constitution"] = modifier
    parameters["targets"] = ["guard", "other"]
    cast = _run("cast_spell", state, participant_data=data, **parameters).result
    assert cast["result_card"]["title"] == "Sunburst"
    assert "blinded" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert "blinded" not in state[f"{SRD}.entities"]["other"]["conditions"]
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["constitution"] = 100
    _run("end_combat_turn", state)
    state[f"{SRD}.combat"]["current_actor"] = "guard"
    _run("begin_combat_turn", state)
    ended = _run("end_combat_turn", state).result
    assert "constitution save" in str(ended["result_card"])
    assert "blinded" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_area_blind_expires_after_saved_duration_without_a_successful_repeat(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case("sunburst")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False, phase="exploration")
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(hp=1000, max_hp=1000)
    guard["saving_throw_modifiers"]["constitution"] = -100
    parameters["targets"] = ["guard"]
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert "blinded" in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=9)
    assert "blinded" in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert "blinded" not in state[f"{SRD}.entities"]["guard"]["conditions"]


@pytest.mark.parametrize("save_modifier,released", [(30, True), (-30, False)])
def test_condition_cast_disables_actions_and_each_wisdom_save_releases_only_its_target(
    monkeypatch, save_modifier, released,
):
    state, data, parameters = _hold_case(monkeypatch)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    for actor, modifier in [("hero", 20), ("guard", 0), ("other", 0)]:
        state[f"{SRD}.entities"][actor]["initiative_modifier"] = modifier
    _run("start_group_combat_encounter", state, combatants=["hero", "guard", "other"])
    _run("begin_combat_turn", state)
    state[f"{SRD}.entities"]["guard"].update(
        concentration_spell_id="guidance",
        concentration_effect_family="lasting_reactive_concentration",
        concentration_target_state_id="other", concentration_sequence=1,
        concentration_expires_tick=20,
    )
    state[f"{SRD}.entities"]["other"]["guidance_head_caster_state_id"] = "guard"
    parameters.update(targets=["guard", "other"], slot_resource="spell_slot_3")
    cast = _run("cast_spell", state, participant_data=data, **parameters)
    assert cast.result["result_card"]["title"] == "Hold Person"
    assert state[f"{SRD}.entities"]["guard"]["concentration_spell_id"] is None
    assert state[f"{SRD}.entities"]["other"]["guidance_head_caster_state_id"] is None
    for target in parameters["targets"]:
        assert "paralyzed" in state[f"{SRD}.entities"][target]["conditions"]
    quiet_end = _run("end_combat_turn", state)
    assert quiet_end.result["result_card"] is None
    _run("advance_group_combat_encounter_turn", state)
    _run("begin_combat_turn", state)
    assert state[f"{SRD}.combat"]["active_actor_can_act"] is False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _npc_attack(state, "Spear", actor="guard", target="hero", use_multiattack=False)
    assert state == before
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"].update(
        wisdom=save_modifier, constitution=-save_modifier,
    )
    ended = _run("end_combat_turn", state)
    assert "wisdom save" in str(ended.result["result_card"])
    assert ("paralyzed" not in state[f"{SRD}.entities"]["guard"]["conditions"]) is released
    assert "paralyzed" in state[f"{SRD}.entities"]["other"]["conditions"]
    advanced = _run("advance_group_combat_encounter_turn", state)
    assert advanced.result["context"]["_timed_condition_save_details"] == []
    _run("end_concentration", state, caster_state="hero")
    for target in ("guard", "other"):
        assert "paralyzed" not in state[f"{SRD}.entities"][target]["conditions"]


@pytest.mark.parametrize("spell,kind,distance,allowed", [
    ("hold_person", "humanoid", 60, True),
    ("hold_person", "humanoid", 61, False),
    ("hold_person", "beast", 30, False),
    ("hold_monster", "beast", 90, True),
    ("hold_monster", "beast", 91, False),
])
def test_condition_records_enforce_target_type_and_range(
    monkeypatch, spell, kind, distance, allowed,
):
    state, data, parameters = _hold_case(monkeypatch, spell)
    state[f"{SRD}.entities"]["guard"]["creature_type"] = kind
    parameters["targets_max_distance"] = distance
    before = deepcopy(state)
    if not allowed:
        with pytest.raises(RulesetPreconditionRejected):
            _run("cast_spell", state, participant_data=data, **parameters)
        assert state == before
    else:
        _run("cast_spell", state, participant_data=data, **parameters)
        assert "paralyzed" in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_condition_immunity_prevents_application_without_refunding_the_cast(monkeypatch):
    state, data, parameters = _hold_case(monkeypatch)
    state[f"{SRD}.entities"]["guard"]["condition_immunities"] = ["paralyzed"]
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    cast = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert "paralyzed" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert "immune" in str(cast.result["result_card"])
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2

    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None


def test_condition_ownership_preserves_an_independent_source_when_caster_is_incapacitated(
    monkeypatch,
):
    state, data, parameters = _hold_case(monkeypatch)
    _run("cast_spell", state, participant_data=data, **parameters)
    _run("apply_srd_condition", state, target="guard", condition="paralyzed")
    _run("apply_srd_condition", state, target="hero", condition="stunned")
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert "paralyzed" in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("remove_srd_condition", state, target="guard", condition="paralyzed")
    assert "paralyzed" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_saved_condition_expires_through_public_free_time_and_retry_is_idempotent(
    tmp_path, monkeypatch,
):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "held.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "hold_person")
        receipt, _ = _act(owner, "cast_spell_outside_combat", **(parameters | {
            "targets": ["other"], "forego_save_targets": ["other"],
        }))
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting").state
        assert "paralyzed" in saved[f"{SRD}.entities"]["other"]["conditions"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved
        receipt, command = _act(owner, "advance_srd_lasting_time", elapsed_rounds=10)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        after = owner.snapshot("casting").state
        assert "paralyzed" not in after[f"{SRD}.entities"]["other"]["conditions"]
        assert after[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == after


def test_self_incapacitation_ends_concentration_without_leaving_a_phantom_condition(monkeypatch):
    state, data, parameters = _hold_case(monkeypatch)
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(creature_type="humanoid",
                saving_throw_modifiers=dict.fromkeys(data["abilities"], -30))
    cast = _run("cast_spell", state, participant_data=data,
                **(parameters | {"targets": ["hero"]}))
    hero = state[f"{SRD}.entities"]["hero"]
    assert "paralyzed" not in hero["conditions"]
    assert hero["concentration_spell_id"] is None
    assert "No condition applied" in str(cast.result["result_card"])


def test_threshold_condition_repeats_saved_end_turn_save_until_success():
    state, data, parameters = _spell_case("power_word_stun")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    target = state[f"{SRD}.entities"]["guard"]
    target.update(hp=150, max_hp=200, temp_hp=20, conditions=[])
    target["saving_throw_modifiers"]["constitution"] = -100

    cast = _run("cast_spell_outside_combat", state, participant_data=data,
                **parameters).result
    assert "stunned" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert "Stunned" in str(cast["result_card"])
    _run("resolve_srd_timed_condition_end_turn", state,
         target_state="guard", lasting_state="state")
    assert "stunned" in state[f"{SRD}.entities"]["guard"]["conditions"]
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["constitution"] = 100
    state[f"{SRD}.entities"]["guard"]["turns_started"] = 1
    _run("resolve_srd_timed_condition_end_turn", state,
         target_state="guard", lasting_state="state")
    assert "stunned" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_threshold_condition_above_limit_stops_movement_until_caster_next_turn():
    state, data, parameters = _spell_case("power_word_stun")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    target = state[f"{SRD}.entities"]["guard"]
    target.update(hp=151, max_hp=200, conditions=[])
    _run("cast_spell_outside_combat", state, participant_data=data,
         **parameters)
    target = state[f"{SRD}.entities"]["guard"]
    assert target["spell_speed_reduction"] == 30
    assert "stunned" not in target["conditions"]
    target["movement_rates"] = {"ground": 30, "fly": 60}
    target["movement_remaining"] = {"ground": 30, "fly": 60}
    with pytest.raises(RulesetPreconditionRejected):
        _run("spend_srd_movement", state, target="guard", mode="fly",
             distance=1, difficult_terrain=False)
    _run("reset_srd_movement", state, target="guard", mode="fly")
    assert state[f"{SRD}.entities"]["guard"]["movement_remaining"]["fly"] == 0
    _run("process_srd_turn_effects", state, lasting_state="state",
         boundary_actor="hero", boundary_phase="start", boundary_turn=1)
    assert state[f"{SRD}.entities"]["guard"]["spell_speed_reduction"] == 0
    assert state[f"{SRD}.state"]["turn_effects"] == []
    _run("reset_srd_movement", state, target="guard", mode="fly")
    assert state[f"{SRD}.entities"]["guard"]["movement_remaining"]["fly"] == 60


def test_public_threshold_movement_penalty_reopens_then_expires(tmp_path):
    database = tmp_path / "threshold-condition.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "power_word_stun",
            target_overrides={"hp": 151, "max_hp": 200, "conditions": []})
        receipt, command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting").state
        assert saved[f"{SRD}.entities"]["guard"]["spell_speed_reduction"] == 30
        assert saved[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 2
        assert owner.execute_action(command) == receipt
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting").state == saved
        elapsed, command = _act(reopened, "advance_srd_lasting_time", elapsed_rounds=1)
        assert elapsed.status is ActionStatus.COMMITTED
        after = reopened.snapshot("casting").state
        assert after[f"{SRD}.entities"]["guard"]["spell_speed_reduction"] == 0
        assert reopened.execute_action(command) == elapsed
        assert reopened.snapshot("casting").state == after


def test_repeat_save_clears_only_its_stunned_source():
    state, data, parameters = _spell_case("power_word_stun")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(hp=150, max_hp=200, conditions=[])
    guard["saving_throw_modifiers"]["constitution"] = 100
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    _run("install_srd_turn_effect", state,
         caster_state="hero", target_state="guard", lasting_state="state",
         spell="stunning_strike", effect_label="Stunning Strike", kind="stunned",
         expiry_owner="target", expiry_phase="end", magnitude=0, dice_count=0,
         damage_die="d4", damage_type="force")
    _run("resolve_srd_timed_condition_end_turn", state,
         target_state="guard", lasting_state="state")
    assert "stunned" in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("process_srd_turn_effects", state, lasting_state="state",
         boundary_actor="guard", boundary_phase="end", boundary_turn=1)
    assert "stunned" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_area_condition_uses_caster_turn_expiry_and_preserves_other_blindness():
    state, data, parameters = _spell_case("color_spray")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    guard = state[f"{SRD}.entities"]["guard"]
    other = state[f"{SRD}.entities"]["other"]
    immune = deepcopy(guard)
    immune["name"] = "Immune"
    immune["condition_immunities"] = ["blinded"]
    immune["saving_throw_modifiers"]["constitution"] = -100
    state[f"{SRD}.entities"]["immune"] = immune
    guard["saving_throw_modifiers"]["constitution"] = -100
    other["saving_throw_modifiers"]["constitution"] = 100
    parameters["targets"] = ["guard", "other", "immune"]
    cast = _run("cast_spell_outside_combat", state, participant_data=data,
                **parameters).result
    assert "blinded" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert "blinded" not in state[f"{SRD}.entities"]["other"]["conditions"]
    assert "blinded" not in state[f"{SRD}.entities"]["immune"]["conditions"]
    assert cast["result_card"]["title"] == "Color Spray"
    _run("install_srd_timed_condition_source", state, target_state="guard",
         lasting_state="state", spell="sunburst", condition="blinded",
         save_dc=30, duration_rounds=10,
         repeat_save_timing="end_of_target_turn", save_ability="constitution")
    _run("process_srd_turn_effects", state, lasting_state="state",
         boundary_actor="hero", boundary_phase="end", boundary_turn=1)
    assert "blinded" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.entities"]["guard"]["turn_effect_blinded"] is False
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert "blinded" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_public_area_condition_reopens_then_expires(tmp_path):
    database = tmp_path / "area-condition.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "color_spray",
            target_overrides={"hp": 20, "max_hp": 20,
                              "saving_throw_modifiers": dict.fromkeys(
                                  ("strength", "dexterity", "constitution", "intelligence",
                                   "wisdom", "charisma"), -100)})
        parameters["targets"] = ["guard"]
        receipt, command = _act(owner, "cast_spell_outside_combat", **parameters)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting").state
        assert "blinded" in saved[f"{SRD}.entities"]["guard"]["conditions"]
        assert saved[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
        assert owner.execute_action(command) == receipt
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting").state == saved
        elapsed, command = _act(reopened, "advance_srd_lasting_time", elapsed_rounds=1)
        assert elapsed.status is ActionStatus.COMMITTED
        after = reopened.snapshot("casting").state
        assert "blinded" not in after[f"{SRD}.entities"]["guard"]["conditions"]
        assert reopened.execute_action(command) == elapsed
