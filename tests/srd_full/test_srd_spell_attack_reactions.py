"""Spell hits retain their actual roll and defer damage and riders until defense resolves."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_reaction_spells import _cast_parameters, _mage
from tests.srd_full.test_srd_scene_relations import _state as _scene_state
from tests.srd_full.test_srd_spell_result_cards import _spell_case


def _case(spell):
    state, data, params = _spell_case(spell)
    _mage(state, "shield")
    scene = deepcopy(_scene_state()[f"{SRD}.state"]["spatial_scene"])
    scene["entities"][1].update(id="mage", position=[10, 0, 0])
    scene["entities"][1]["metadata"]["participant_ref"] = "mage"
    state[f"{SRD}.state"]["spatial_scene"] = scene
    params.update(target="mage", target_distance=10)
    return state, data, params


@pytest.mark.parametrize("seed,critical", [(1, False), (5, True)])
def test_actual_spell_hit_opens_shield_window_without_early_damage_or_reroll(
    monkeypatch, seed, critical,
):
    state, data, params = _case("fire_bolt")
    monkeypatch.setattr(dice, "_rng", Random(seed))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _run("begin_srd_spell_action", state, participant_data=data, **params)
    saved = state[f"{SRD}.state"]
    event_id = saved["pending_srd_action_stack"][-1]
    hp = state[f"{SRD}.entities"]["mage"]["hp"]
    with pytest.raises(RulesetPreconditionRejected, match="actual attack hit"):
        _run("cast_srd_reaction_spell", state, **_cast_parameters("shield", event_id))
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=event_id)
    event = deepcopy(state[f"{SRD}.state"]["pending_srd_actions"][event_id])
    assert event["kind"] == "spell_attack"
    assert event["stage"] == "spell_attack_reaction"
    assert event["attack_outcome"] == ("critical_hit" if critical else "hit")
    assert state[f"{SRD}.entities"]["mage"]["hp"] == hp
    assert state[f"{SRD}.combat"]["action_available"] is False
    _run("cast_srd_reaction_spell", state, **_cast_parameters("shield", event_id))
    monkeypatch.setattr(dice, "_rng", Random(2))
    finished = _run("complete_srd_pending_action", state, participant_data=data,
                    pending_action_id=event_id).result
    assert finished["context"]["_spell_attack"] == event["spell_attack_context"]["_spell_attack"]
    assert (state[f"{SRD}.entities"]["mage"]["hp"] < hp) is critical
    assert state[f"{SRD}.entities"]["mage"]["spell_uses_remaining"]["shield"] == 1
    assert not state[f"{SRD}.state"]["pending_srd_action_stack"]


@pytest.mark.parametrize("shielded", [False, True])
def test_ray_of_frost_rider_waits_for_actual_spell_hit_completion(monkeypatch, shielded):
    state, data, params = _case("ray_of_frost")
    monkeypatch.setattr(dice, "_rng", Random(1))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _run("begin_srd_spell_action", state, participant_data=data, **params)
    event = state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=event)
    assert not any(record.get("spell") == "ray_of_frost"
                   for record in state[f"{SRD}.state"].get("turn_effects", []))
    if shielded:
        _run("cast_srd_reaction_spell", state, **_cast_parameters("shield", event))
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=event)
    assert any(record.get("spell") == "ray_of_frost"
               for record in state[f"{SRD}.state"].get("turn_effects", [])) is not shielded


def test_spell_attack_miss_has_no_shield_window_and_closes_original_cast(monkeypatch):
    state, data, params = _case("fire_bolt")
    monkeypatch.setattr(dice, "_rng", Random(2))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _run("begin_srd_spell_action", state, participant_data=data, **params)
    event = state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
    hp = state[f"{SRD}.entities"]["mage"]["hp"]
    finished = _run("complete_srd_pending_action", state, participant_data=data,
                    pending_action_id=event).result
    card = finished["result_card"]
    assert card["title"] == "Fire Bolt"
    assert any(row["value"] == "Miss; damage 0"
               for row in finished["context"]["_spell_summary_rows"])
    assert "selected 2" in card["details"][0]["value"]
    assert state[f"{SRD}.entities"]["mage"]["hp"] == hp
    assert not state[f"{SRD}.state"]["pending_srd_action_stack"]
