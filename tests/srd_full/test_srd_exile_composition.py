"""Actual completed condition and damage actions settle their exact queued exile returns."""

from sylvia_gm import dice
from tests import test_srd_spell_travel as travel
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_spell_special_damage import ActualDiceSequence


def test_actual_public_incapacitation_returns_current_maze_recipient_before_next_action(
    monkeypatch,
):
    state, data, params, _ = travel._exile_case(monkeypatch)
    _run("cast_spell", state, participant_data=data, **params)
    assert travel._plane(state, "guard").startswith("srd_exile:")
    _run("apply_srd_condition", state, target="hero", condition="incapacitated")
    assert travel._plane(state, "guard") == "material"
    assert state[f"{SRD}.entities"]["guard"]["travel_exile"] is None
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None


def test_actual_public_fall_damage_concentration_failure_returns_exiled_creature(monkeypatch):
    state, data, params, _ = travel._exile_case(monkeypatch)
    state[f"{SRD}.entities"]["hero"].update(hp=1000, max_hp=1000)
    _run("cast_spell", state, participant_data=data, **params)
    monkeypatch.setattr(dice, "_rng", ActualDiceSequence(d20=1, d6=[6] * 20))
    _run("resolve_srd_fall", state, target_state="hero", distance=200)
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 880
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert travel._plane(state, "guard") == "material"
    assert state[f"{SRD}.entities"]["guard"]["travel_exile"] is None
