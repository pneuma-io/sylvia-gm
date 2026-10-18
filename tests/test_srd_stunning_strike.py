"""Stunning Strike shares saved turn effects across attack budgets and later turns."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.test_srd_action_budget import SRD, _run
from tests.test_srd_attack_budget import _attack
from tests.test_srd_unarmed_creatures import _case, _strike


def _monk(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(6))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 5}], level=5, fighting_styles=[])
    data["abilities"]["wisdom"] = 16
    state[f"{SRD}.entities"]["hero"].update(
        resources={"focus_point": 5}, resource_maxima={"focus_point": 5},
        available_reactions=["opportunity_attack"], melee_reach=5,
    )
    state[f"{SRD}.entities"]["guard"].update(
        armor_class=1, saving_throw_modifiers={"constitution": 0},
    )
    state[f"{SRD}.combat"].update(turn_sequence=1, movement_spent=0, dashes_taken=0)
    return state, data


@pytest.mark.parametrize("route", [
    "unarmed", "weapon", "bonus", "unarmed_reaction", "weapon_reaction",
])
def test_stunning_strike_hits_use_fixed_save_focus_and_shared_expiry(monkeypatch, route):
    state, data = _monk(monkeypatch)
    choices = {"use_stunning_strike": True, "stunning_forego_save": True}
    if route == "unarmed":
        result = _strike(state, data, "damage", **choices)
    elif route == "weapon":
        result = _attack(state, data, weapon="spear", **choices)
    elif route == "bonus":
        result = _run("resolve_monk_bonus_strike", state, participant_data=data,
                      actor="hero", actor_state="hero", target_state="guard", effect="damage",
                      sequence="flurry", distance=5, actor_can_see_target=True,
                      target_can_see_actor=True, target_is_charmer=False,
                      fear_source_in_sight=False, **choices)
    else:
        state[f"{SRD}.combat"].update(active_actor="guard", current_actor="guard")
        _run("trigger_opportunity_attack", state, participant_data=data,
             reactor_state="hero", actor_can_see_target=True, target_can_see_actor=True,
             attack_distance=5, weapon="spear" if route == "weapon_reaction" else "unarmed_strike")
        params = {"effect": "damage"} if route == "unarmed_reaction" else {
            "advantage": False, "disadvantage": False,
        }
        result = _run("resolve_unarmed_opportunity_attack" if route == "unarmed_reaction"
                      else "resolve_opportunity_attack", state, participant_data=data,
                      target_is_charmer=False, fear_source_in_sight=False, **params, **choices)
    assert result.result["context"]["_stunning_save_dc"] == 14
    assert result.result["context"]["_stunning_focus_spent"] == 1
    assert "Stunning Strike" in str(result.result["result_card"])
    expected_focus = 3 if route == "bonus" else 4
    assert state[f"{SRD}.entities"]["hero"]["resources"]["focus_point"] == expected_focus
    assert "stunned" in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("end_combat_turn", state)
    state[f"{SRD}.combat"]["current_actor"] = "hero"
    _run("begin_combat_turn", state, participant_data=data)
    assert "stunned" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_successful_save_halves_movement_and_consumes_next_attack_advantage(monkeypatch):
    state, data = _monk(monkeypatch)
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["constitution"] = 30
    result = _strike(state, data, "damage", use_stunning_strike=True)
    assert result.result["context"]["_stunning_save_total"] >= 31
    assert "stunned" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    next_attack = _attack(state, data, weapon="spear")
    assert next_attack.result["context"]["_effective_advantage"] is True
    assert not any(
        e["kind"] == "next_attack_advantage" for e in state[f"{SRD}.state"]["turn_effects"]
    )
    _run("end_combat_turn", state)
    state[f"{SRD}.combat"]["current_actor"] = "guard"
    _run("begin_combat_turn", state)
    assert state[f"{SRD}.combat"]["movement_remaining"] == 15
    _run("apply_srd_dash", state, actor_state="guard")
    assert state[f"{SRD}.combat"]["movement_remaining"] == 30
    _run("end_combat_turn", state)
    state[f"{SRD}.combat"]["current_actor"] = "hero"
    _run("begin_combat_turn", state, participant_data=data)
    assert state[f"{SRD}.entities"]["guard"]["turn_speed_halved"] is False


@pytest.mark.parametrize("problem", ["level", "focus", "weapon", "grapple", "repeat"])
def test_illegal_stunning_choice_rejects_without_attack_or_resource_changes(monkeypatch, problem):
    state, data = _monk(monkeypatch)
    if problem == "level":
        data["classes"][0]["level"] = 4
    elif problem == "focus":
        state[f"{SRD}.entities"]["hero"]["resources"]["focus_point"] = 0
    elif problem == "repeat":
        _strike(state, data, "damage", use_stunning_strike=True, stunning_forego_save=True)
    elif problem == "weapon":
        state[f"{SRD}.entities"]["hero"]["inventory"].append("longsword")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        if problem == "weapon":
            _attack(state, data, weapon="longsword", use_stunning_strike=True)
        else:
            _strike(state, data, "grapple" if problem == "grapple" else "damage",
                    use_stunning_strike=True, stunning_forego_save=True)
    assert state == before


def test_miss_preserves_focus_and_use_while_stun_immunity_spends_both(monkeypatch):
    state, data = _monk(monkeypatch)
    state[f"{SRD}.entities"]["guard"]["armor_class"] = 99
    _strike(state, data, "damage", use_stunning_strike=True)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["focus_point"] == 5
    assert state[f"{SRD}.entities"]["hero"].get("stunning_strike_turn") is None
    state[f"{SRD}.entities"]["guard"].update(armor_class=1, condition_immunities=["stunned"])
    _strike(state, data, "damage", use_stunning_strike=True, stunning_forego_save=True)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["focus_point"] == 4
    assert "stunned" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_stunning_can_be_used_again_on_another_creatures_turn(monkeypatch):
    state, data = _monk(monkeypatch)
    _strike(state, data, "damage", use_stunning_strike=True, stunning_forego_save=True)
    _run("end_combat_turn", state)
    state[f"{SRD}.combat"]["current_actor"] = "guard"
    _run("begin_combat_turn", state)
    assert state[f"{SRD}.combat"]["turn_sequence"] == 2
    assert state[f"{SRD}.combat"]["active_actor_can_act"] is False
    # An Opportunity Attack is not possible against this stunned target's movement;
    # exercise the once-per-turn consumer independently of that physical trigger.
    _run("resolve_srd_stunning_strike", state, participant_data=data,
         actor="hero", actor_state="hero", target_state="guard", weapon="spear",
         hit=True, forego_save=True)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["focus_point"] == 3
    _run("remove_srd_condition", state, target="guard", condition="stunned")
    assert state[f"{SRD}.combat"]["active_actor_can_act"] is True


def test_stun_ends_concentration_and_elapsed_time_expires_it_outside_combat(monkeypatch):
    state, data = _monk(monkeypatch)
    state[f"{SRD}.entities"]["guard"].update(
        concentration_spell_id="guidance",
        concentration_effect_family="lasting_reactive_concentration",
        concentration_target_state_id="hero", concentration_sequence=1,
        concentration_expires_tick=20,
    )
    state[f"{SRD}.entities"]["hero"]["guidance_head_caster_state_id"] = "guard"
    _run("resolve_srd_stunning_strike", state, participant_data=data,
         actor="hero", actor_state="hero", target_state="guard", weapon="spear",
         hit=True, forego_save=True)
    assert state[f"{SRD}.entities"]["guard"]["concentration_spell_id"] is None
    assert state[f"{SRD}.entities"]["hero"]["guidance_head_caster_state_id"] is None
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert "stunned" not in state[f"{SRD}.entities"]["guard"]["conditions"]


@pytest.mark.parametrize("other_source", ["before", "after", "timed", "removed"])
def test_stun_expiry_preserves_sources_and_explicit_removal_stays_removed(
    monkeypatch, other_source,
):
    state, data = _monk(monkeypatch)
    if other_source == "before":
        _run("apply_srd_condition", state, target="guard", condition="stunned")
    _strike(state, data, "damage", use_stunning_strike=True, stunning_forego_save=True)
    if other_source == "after":
        _run("apply_srd_condition", state, target="guard", condition="stunned")
    if other_source == "timed":
        _run("install_srd_turn_effect", state, caster_state="guard", target_state="guard",
             lasting_state="state", spell="another_stun", effect_label="Another Stun",
             kind="stunned", expiry_owner="caster", expiry_phase="start", magnitude=0,
             dice_count=0, damage_die="d4", damage_type="bludgeoning")
    if other_source == "removed":
        _run("remove_srd_condition", state, target="guard", condition="stunned")
        _run("project_srd_turn_modifiers", state, target_state="guard", lasting_state="state")
        assert "stunned" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("end_combat_turn", state)
    state[f"{SRD}.combat"]["current_actor"] = "hero"
    _run("begin_combat_turn", state, participant_data=data)
    stunned = "stunned" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert stunned is (other_source != "removed")
