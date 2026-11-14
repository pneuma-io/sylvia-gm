"""The real turn owner enforces source-qualified Symbol retreat before ending."""

from copy import deepcopy
from functools import lru_cache

import pytest
from scripts.srd_full.srd_spell_trigger_composition import bind_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_triggers as owner
from tests import test_srd_symbol_fear_movement as fear

SRD = owner.SRD
_parent_runtime = fear._runtime


@lru_cache
def _runtime():
    text = bind_runtime(_parent_runtime()[1].decode())
    return _load_runtime(text), text.encode()


@pytest.fixture(autouse=True)
def _composed_owner(monkeypatch):
    monkeypatch.setattr(owner, "_runtime", _runtime)
    monkeypatch.setattr(fear, "_runtime", _runtime)
    fear._mechanic.cache_clear()
    monkeypatch.setattr(owner, "_mechanic", fear._mechanic)
    monkeypatch.setattr(budget, "_mechanic", fear._mechanic)
    yield
    fear._mechanic.cache_clear()


def _turn(*, speed=30, immobilized=False):
    state = fear._activated()
    actor = state[f"{SRD}.entities"]["hero"]
    actor["speed"] = speed
    if immobilized:
        actor["conditions"].append("restrained")
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor="hero")
    budget._run("begin_combat_turn", state, actor="hero")
    return state


def test_real_turn_cannot_end_before_actual_available_retreat_is_used():
    state = _turn()
    saved = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Symbol Fear retreat"):
        budget._run("end_combat_turn", state, actor="hero")
    assert state == saved
    fear._move(state, [-15, 0, 0])
    saved = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Symbol Fear retreat"):
        budget._run("end_combat_turn", state, actor="hero")
    assert state == saved
    fear._move(state, [-30, 0, 0])
    assert fear._qualify(state)["_symbol_fear_movement_sources"][0]["moved_feet"] == 30
    budget._run("end_combat_turn", state, actor="hero")
    assert state[f"{SRD}.combat"]["turn_active"] is False


def test_shorter_real_speed_uses_available_retreat_and_native_restraint_needs_no_fake_verdict():
    state = _turn(speed=20)
    fear._move(state, [-20, 0, 0])
    assert state[f"{SRD}.combat"]["movement_remaining"] == 0
    budget._run("end_combat_turn", state, actor="hero")
    state = _turn(immobilized=True)
    saved = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        fear._move(state, [-30, 0, 0])
    assert state == saved
    budget._run("end_combat_turn", state, actor="hero")
    assert state[f"{SRD}.combat"]["turn_active"] is False
