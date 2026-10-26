"""The final paired-observer composer preserves native receiving groups through repeat emission."""

import hashlib
from copy import deepcopy
from pathlib import Path

from scripts.srd_full.srd_spell_damage_consumers import _compose_mislead_damage, _effect_lists
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

NATIVE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-damage-observer-composition/ruleset.yaml")
)
CALLBACKS = {"end_srd_invisibility_damage_source", "end_srd_mislead_damage_source"}


def _calls(effect):
    return [row["call"] for effects in _effect_lists([effect]) for row in effects if "call" in row]


def test_actual_native_damage_observer_groups_survive_final_pair_composition_and_repeat():
    assert hashlib.sha256(NATIVE.read_bytes()).hexdigest() == (
        "8499f04a288bd35c92302c21cd01345c43111c9223b60f91a032af071ac62d07"
    )
    tools = deepcopy(_load_runtime(NATIVE.read_text())["tools"])
    names = ("apply_srd_damage", "apply_srd_composite_damage")
    receiving = {
        name: [e for e in tools[name]["effects"] if set(_calls(e)) - CALLBACKS] for name in names
    }
    once = {}
    _compose_mislead_damage(tools, once)
    combined = tools | once
    twice = {}
    _compose_mislead_damage(combined, twice)
    for name in names:
        assert combined[name] == (combined | twice)[name]
        actual_calls = [call for effect in combined[name]["effects"] for call in _calls(effect)]
        assert all(actual_calls.count(call) == 1 for call in CALLBACKS)
        assert all(effect in combined[name]["effects"] for effect in receiving[name])
        assert len(combined[name]["effects"]) <= len(tools[name]["effects"])
