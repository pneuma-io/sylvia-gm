"""Actor spell effects must retain source ownership and change later play."""

from copy import deepcopy
from dataclasses import replace
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_spell_result_cards import SRD
from tests.srd_full.test_srd_spell_wave_defenses import _case, _cast

NEW_SPELLS = [
    "beacon_of_hope",
    "calm_emotions",
    "freedom_of_movement",
    "ray_of_enfeeblement",
    "sanctuary",
]


def _modifier_case(spell, **choices):
    return _case(spell, targets=["guard"], all_targets_in_area=True, **choices)


def _test_roll(state, ability="wisdom", kind="saving_throw"):
    return _run(
        "d20_test",
        state,
        actor_state="guard",
        ability=ability,
        kind=kind,
        ability_modifier=0,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=10,
    ).result["context"]


@pytest.mark.parametrize("spell", NEW_SPELLS)
def test_new_modifier_records_install_real_source_owned_effects(spell):
    state, data, parameters = _modifier_case(spell, forego_save_targets=["guard"])
    result = _cast(state, data, parameters)
    records = state[f"{SRD}.state"]["turn_effects"]
    record = next(r for r in records if r["kind"] == "actor_spell_modifier")
    assert record["spell"] == spell
    assert record["caster"] == "hero"
    assert record["target"] == "guard"
    assert result["context"]["_spell_summary_rows"][0]["label"] == "Guard"
    assert "{" not in result["context"]["_spell_summary_rows"][0]["value"]


def test_beacon_changes_wisdom_and_death_saves_and_maximizes_cure_wounds(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _modifier_case("beacon_of_hope")
    _cast(state, data, parameters)
    wisdom = _test_roll(state)
    assert wisdom["_effective_advantage"] is True
    assert len(wisdom["_d20_test"]["results"]) == 2
    assert _test_roll(state, "constitution")["_effective_advantage"] is False
    guard = _guard(state)
    guard.update(hp=0, max_hp=100, creature_kind="character", conditions=["unconscious"])
    state[f"{SRD}.combat"]["active_actor"] = "guard"
    death = _run("death_saving_throw", state, target="guard").result["context"]
    assert len(death["_death_save"]["results"]) == 2
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("cure_wounds")
    _run(
        "cast_spell_healing",
        state,
        spell="cure_wounds",
        family="spell_healing",
        casting_ability="wisdom",
        casting_modifier=4,
        casting_attack_bonus=7,
        casting_save_dc=15,
        casting_scaling_level=5,
        caster_state="hero",
        target="guard",
        slot_resource="spell_slot_1",
        slot_level=1,
        base_level=1,
        base_dice=2,
        dice_per_slot_level=2,
        healing_die="d8",
        range_kind="touch",
        range_feet=0,
        requires_sight=False,
        target_distance=5,
        caster_can_see_target=True,
        caster_can_touch_target=True,
        cover="none",
    )
    assert _guard(state)["hp"] == 20
    _run("end_concentration", state, caster_state="hero")
    assert _test_roll(state)["_effective_advantage"] is False


def test_calm_suppresses_both_existing_conditions_and_rejects_unowned_attitude_choice():
    state, data, parameters = _modifier_case("calm_emotions", forego_save_targets=["guard"])
    _guard(state)["conditions"] = ["charmed", "frightened", "poisoned"]
    _cast(state, data, parameters)
    assert _guard(state)["conditions"] == ["poisoned"]
    with pytest.raises(RulesetPreconditionRejected, match="immune"):
        _run("apply_srd_condition", state, target="guard", condition="charmed")
    _run("end_concentration", state, caster_state="hero")
    assert set(_guard(state)["conditions"]) == {"charmed", "frightened", "poisoned"}
    state, data, parameters = _modifier_case("calm_emotions", forego_save_targets=["guard"])
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="indifference"):
        _cast(state, data, parameters | {"calm_indifference_targets": ["guard"]})
    assert state == before


def test_calm_requires_authoritative_creature_type_and_does_not_affect_fiends():
    state, data, parameters = _modifier_case("calm_emotions", forego_save_targets=["guard"])
    _guard(state).pop("creature_type")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="creature type"):
        _cast(state, data, parameters)
    assert state == before
    _guard(state)["creature_type"] = "fiend"
    _cast(state, data, parameters)
    assert not state[f"{SRD}.state"].get("turn_effects")


def test_freedom_ignores_difficult_terrain_and_supplies_actual_swim_speed_until_expiry():
    state, data, parameters = _modifier_case("freedom_of_movement")
    _cast(state, data, parameters)
    combat = state[f"{SRD}.combat"]
    combat.update(
        turn_active=True,
        encounter_active=True,
        active_actor="guard",
        movement_remaining=30,
        movement_spent=0,
        movement_mode="walk",
    )
    _run(
        "spend_combat_movement",
        state,
        actor="guard",
        actor_state="guard",
        distance=5,
        difficult_terrain=True,
        movement_kind="walk",
    )
    assert state[f"{SRD}.combat"]["movement_remaining"] == 25
    _run(
        "spend_combat_movement",
        state,
        actor="guard",
        actor_state="guard",
        distance=5,
        difficult_terrain=True,
        movement_kind="swim",
    )
    assert state[f"{SRD}.combat"]["movement_remaining"] == 20
    state[f"{SRD}.combat"].update(turn_active=False, encounter_active=False, active_actor=None)
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    assert _guard(state)["spell_freedom_of_movement"] is False


def test_enfeeblement_failure_changes_strength_rolls_and_end_turn_save_ends_it():
    state, data, parameters = _modifier_case("ray_of_enfeeblement", forego_save_targets=["guard"])
    _cast(state, data, parameters)
    assert _test_roll(state, "strength", "ability_check")["_effective_disadvantage"] is True
    assert _test_roll(state, "dexterity", "ability_check")["_effective_disadvantage"] is False
    _guard(state)["saving_throw_modifiers"]["constitution"] = 30
    _run(
        "process_srd_turn_effects",
        state,
        lasting_state="state",
        boundary_actor="guard",
        boundary_phase="end",
        boundary_turn=1,
    )
    assert _test_roll(state, "strength", "ability_check")["_effective_disadvantage"] is False
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None


def test_enfeeblement_penalty_is_one_d8_before_resistance(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _modifier_case("ray_of_enfeeblement", forego_save_targets=["guard"])
    _cast(state, data, parameters)
    result = _run(
        "modify_srd_actor_damage_roll", state, actor_state="guard", lasting_state="state", amount=12
    ).result["context"]
    penalty = result["_enfeeblement_penalty_roll"]["total"]
    assert 1 <= penalty <= 8
    assert result["_actor_modified_damage"] == 12 - penalty
    _guard(state)["damage_resistances"] = ["fire"]
    damage = _run(
        "apply_srd_damage",
        state,
        target="guard",
        amount=result["_actor_modified_damage"],
        adjustment=0,
        damage_type="fire",
        critical_hit=False,
    ).result["context"]
    assert damage["_effective_damage"] == (12 - penalty) // 2


def test_enfeeblement_subtracts_once_from_a_shared_area_roll_before_each_save(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _case(
        "ray_of_enfeeblement",
        target="hero",
        targets=["hero"],
        forego_save_targets=["hero"],
    )
    state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"] = dict.fromkeys(data["abilities"], 0)
    _cast(state, data, parameters)
    data["wizard_spellbook"].append("burning_hands")
    data["spellcasting_grants"][0]["spells"].append("burning_hands")
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("burning_hands")
    _cast(
        state,
        data,
        parameters
        | {
            "spell": "burning_hands",
            "slot_resource": "spell_slot_1",
            "target": "guard",
            "targets": ["guard", "other"],
            "forego_save_targets": [],
        },
    )
    # The single shared 3d6 roll is 9, its d8 penalty is 5, then each save uses 4.
    assert _guard(state)["hp"] == 18
    assert state[f"{SRD}.entities"]["other"]["hp"] == 16


def test_sanctuary_allows_successful_attacker_save_and_rejects_unresolved_retargeting():
    state, data, parameters = _modifier_case("sanctuary")
    _cast(state, data, parameters)
    state[f"{SRD}.entities"]["other"]["saving_throw_modifiers"]["wisdom"] = 30
    _run(
        "resolve_srd_attack_conditions",
        state,
        actor_state="other",
        target_state="guard",
        attack_distance=5,
        actor_can_see_target=True,
        target_can_see_actor=True,
        fear_source_in_sight=False,
    )
    state[f"{SRD}.entities"]["other"]["saving_throw_modifiers"]["wisdom"] = -30
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="cannot yet commit"):
        _run(
            "resolve_srd_attack_conditions",
            state,
            actor_state="other",
            target_state="guard",
            attack_distance=5,
            actor_can_see_target=True,
            target_can_see_actor=True,
            fear_source_in_sight=False,
        )
    assert state == before
    _test_roll(state, "strength", "attack_roll")
    assert not any(r.get("mode") == "sanctuary" for r in state[f"{SRD}.state"]["turn_effects"])


def test_public_beacon_retry_reopen_later_save_and_concentration_shutdown(tmp_path):
    path = tmp_path / "modifiers.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "beacon_of_hope")
        command = ActionCommand(
            campaign_id="casting",
            action_id="beacon",
            idempotency_key="beacon",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters | {"targets": ["guard"]},
        )
        mechanics = StatelessMcpMechanics(owner)
        receipt = mechanics.execute_action(command)
        assert receipt.status is ActionStatus.COMMITTED
        assert mechanics.execute_action(command) == receipt
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        state = deepcopy(dict(saved.state))
        assert _test_roll(state)["_effective_advantage"] is True
        receipt = StatelessMcpMechanics(owner).execute_action(
            replace(
                command,
                action_id="end-beacon",
                idempotency_key="end-beacon",
                expected_revision=saved.revision,
                tool_name="end_concentration",
                parameters={"caster_state": "hero"},
            )
        )
        assert receipt.status is ActionStatus.COMMITTED
        state = deepcopy(dict(owner.snapshot("casting").state))
        assert _test_roll(state)["_effective_advantage"] is False


def _guard(state):
    return state[f"{SRD}.entities"]["guard"]


def test_heroism_suppresses_existing_fear_then_restores_it_without_touching_other_conditions():
    state, data, parameters = _case("heroism")
    guard = state[f"{SRD}.entities"]["guard"]
    guard["conditions"] += ["frightened", "poisoned"]
    _cast(state, data, parameters)
    assert "frightened" not in _guard(state)["conditions"]
    assert "poisoned" in _guard(state)["conditions"]
    assert _guard(state)["timed_condition_unowned_frightened"] is True
    with pytest.raises(RulesetPreconditionRejected, match="immune"):
        _run("apply_srd_condition", state, target="guard", condition="frightened")
    _run("end_concentration", state, caster_state="hero")
    assert "frightened" in _guard(state)["conditions"]


def test_removing_suppressed_fear_prevents_heroism_from_restoring_it():
    state, data, parameters = _case("heroism")
    _guard(state)["conditions"] = ["frightened"]
    _cast(state, data, parameters)
    _run("remove_srd_condition", state, target="guard", condition="frightened")
    _run("end_concentration", state, caster_state="hero")
    assert "frightened" not in _guard(state)["conditions"]


def test_mind_blank_suppression_does_not_resurrect_a_cured_condition():
    state, data, parameters = _case("mind_blank")
    guard = state[f"{SRD}.entities"]["guard"]
    guard["conditions"].append("charmed")
    _cast(state, data, parameters)
    assert "charmed" not in _guard(state)["conditions"]
    _run("remove_srd_condition", state, target="guard", condition="charmed")
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    assert "charmed" not in _guard(state)["conditions"]
    assert _guard(state)["timed_condition_unowned_charmed"] is False


def test_mind_blank_overlap_keeps_existing_charm_suppressed_until_every_source_expires():
    state, data, parameters = _case("mind_blank")
    guard = state[f"{SRD}.entities"]["guard"]
    guard["conditions"].append("charmed")
    _cast(state, data, parameters)
    first = deepcopy(state[f"{SRD}.state"]["turn_effects"][0])
    first["caster"] = "other"
    first["expires_tick"] += 20
    state[f"{SRD}.state"]["turn_effects"].append(first)
    _run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    assert "charmed" not in _guard(state)["conditions"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert "charmed" in _guard(state)["conditions"]
