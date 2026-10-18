"""Condition spell records share saves, concentration ownership and later combat effects."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.test_srd_action_budget import _run
from tests.test_srd_attack_budget import _npc_attack
from tests.test_srd_npc_spell_casting import _act
from tests.test_srd_public_spell_casting import _create_spell_campaign
from tests.test_srd_spell_result_cards import SRD, _spell_case


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
    assert "wisdom saving throw" in str(ended.result["result_card"])
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
    for body in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        if body["id"] in parameters["targets"]:
            body["position"] = [distance, 0, 0]
            body["metadata"]["creature_type"] = kind
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
