"""Ordinary time and committed movement reach the actual Symbol source owner."""

from copy import deepcopy
from functools import lru_cache

import pytest
from scripts.srd_full.srd_spell_trigger_composition import bind_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_triggers as owner
from tests import test_srd_symbol_exploration as exploration

SRD = owner.SRD
_parent_runtime = exploration._runtime


@lru_cache
def _runtime():
    text = bind_runtime(_parent_runtime()[1].decode())
    return _load_runtime(text), text.encode()


@pytest.fixture(autouse=True)
def _composed_owner(monkeypatch):
    monkeypatch.setattr(exploration, "_runtime", _runtime)
    monkeypatch.setattr(owner, "_runtime", _runtime)
    owner._mechanic.cache_clear()
    monkeypatch.setattr(budget, "_mechanic", owner._mechanic)
    yield
    owner._mechanic.cache_clear()


def test_normal_exploration_time_applies_actual_symbol_round_automatically():
    state, _ = exploration._activated()
    hp = state[f"{SRD}.entities"]["other"]["hp"]
    budget._run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert state[f"{SRD}.entities"]["other"]["hp"] < hp
    saved = deepcopy(state)
    exploration._round(state)
    assert state == saved


def test_active_aura_rejects_skipped_rounds_before_clock_or_other_state_changes():
    state, _ = exploration._activated()
    saved = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="one round"):
        budget._run("advance_srd_lasting_time", state, elapsed_rounds=2)
    assert state == saved


def test_actual_exploration_relocation_entry_reaches_symbol_without_combat_counter():
    state, _, _, identity = owner._cast("symbol", symbol_effect="death")
    owner._entry(state, "hero")["position"] = [200, 0, 0]
    owner._entry(state, "other")["position"] = [100, 0, 0]
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False, turn_sequence=9000)
    owner._event(state, identity, event="open")
    hp = state[f"{SRD}.entities"]["other"]["hp"]
    budget._run(
        "move_srd_travel_subject", state, subject="other", destination=[10, 10, 0], plane="material"
    )
    assert state[f"{SRD}.entities"]["other"]["hp"] < hp


def test_actual_exploration_walking_segment_reaches_symbol_after_committed_entry():
    state, _, _, identity = owner._cast("symbol", symbol_effect="death")
    owner._entry(state, "hero")["position"] = [200, 0, 0]
    owner._entry(state, "other")["position"] = [70, 10, 0]
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False, turn_sequence=9000)
    owner._event(state, identity, event="open")
    hp = state[f"{SRD}.entities"]["other"]["hp"]
    budget._run("move_srd_scene_creature", state, actor_state="other", destination=[50, 10, 0])
    assert owner._entry(state, "other")["position"] == [50, 10, 0]
    assert state[f"{SRD}.entities"]["other"]["hp"] < hp


def test_symbol_clock_and_entry_composition_repeats_without_duplicate_consumers():
    runtime, payload = _runtime()
    again = _load_runtime(bind_runtime(payload.decode()))
    for name in (
        "advance_srd_lasting_clock",
        "process_srd_turn_effects",
        "move_srd_travel_subject",
        "move_srd_scene_creature_environment_segment",
    ):
        assert again["tools"][name] == runtime["tools"][name]
