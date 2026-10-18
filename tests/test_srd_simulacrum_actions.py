"""A genuinely copied Fighter spends only its own native charge and action budget."""

from copy import deepcopy

import pytest
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_simulacrum_characters as pc
from tests.actions1_fixture import qualified_runtime

SRD = pc.SRD


@pytest.fixture(autouse=True)
def _compiled(monkeypatch):
    yield from qualified_runtime(monkeypatch)


def _fighter_case():
    state, caster, params, original_data = pc._case()
    original_data.update(
        name="Original Fighter",
        level=2,
        speed=30,
        classes=[{"id": "fighter", "level": 2}],
        spellcasting_grants=[],
        wizard_spellbook=[],
        saving_throw_proficiencies=["strength", "constitution"],
    )
    original = state[f"{SRD}.entities"]["guard"]
    original.update(
        resources={"action_surge": 1, "hit_die": 1},
        resource_maxima={"action_surge": 1, "hit_die": 2},
        prepared_spells=[],
    )
    return state, caster, params, original_data


def test_paid_fighter_copy_uses_native_dodge_and_action_surge_on_its_own_subphase():
    state, caster, params, original_data = _fighter_case()
    pc._cast(state, caster, params, original_data)
    original = deepcopy(state[f"{SRD}.entities"]["guard"])
    combat = state[f"{SRD}.combat"]
    combat.update(encounter_active=True, turn_active=False, current_actor="hero")
    pc._run(state, caster, original_data, "begin_combat_turn")
    pc._run(state, caster, original_data, "take_dodge_action", actor="hero", actor_state="hero")
    creator_budget = deepcopy(state[f"{SRD}.combat"])
    tick = state[f"{SRD}.state"]["lasting_tick"]
    pc._run(
        state,
        caster,
        original_data,
        "begin_srd_simulacrum_turn",
        caster_state="hero",
        duplicate_state="snow",
        command="Dodge, then use Action Surge and Dodge again",
    )
    pc._run(state, caster, original_data, "take_srd_simulacrum_dodge_action", actor_state="snow")
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert state[f"{SRD}.entities"]["snow"]["dodging"] is True
    pc._run(state, caster, original_data, "use_srd_simulacrum_action_surge", actor_state="snow")
    assert state[f"{SRD}.combat"]["action_surge_available"] is True
    assert state[f"{SRD}.entities"]["snow"]["resources"]["action_surge"] == 0
    pc._run(state, caster, original_data, "take_srd_simulacrum_dodge_action", actor_state="snow")
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert state[f"{SRD}.combat"]["action_surge_available"] is False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="no Action Surge"):
        pc._run(state, caster, original_data, "use_srd_simulacrum_action_surge", actor_state="snow")
    assert state == before
    assert state[f"{SRD}.entities"]["guard"] == original
    pc._run(
        state,
        caster,
        original_data,
        "end_srd_simulacrum_turn",
        caster_state="hero",
        duplicate_state="snow",
    )
    for field in ("active_actor", "action_available", "normal_action_spent", "turn_sequence"):
        assert state[f"{SRD}.combat"][field] == creator_budget[field]
    assert state[f"{SRD}.state"]["lasting_tick"] == tick
