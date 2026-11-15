"""Actual final Exile completion tails survive True Strike inverse and regeneration."""

from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

import yaml
from scripts.srd_full.srd_spell_exile_composition import bind_runtime as bind_exile
from scripts.srd_full.srd_true_strike import WEAPON_NODE, bind_runtime_consumers, restore_runtime

from tests.srd_full.test_srd_true_strike import _dependency_runtime, _runtime


@lru_cache
def _joined():
    dependency = Path(__file__).resolve().parents[2] / "scripts/srd_spell_exile_composition.py"
    assert sha256(dependency.read_bytes()).hexdigest() == (
        "fea07731b9fe4f86be93b3f5e63284477ae663f296b58ff247d41e67301bea0b"
    )
    encoded = _runtime()[1].decode()
    joined = bind_exile(encoded, {"resolve_combat_attack"})
    tools = yaml.load(joined, Loader=yaml.CSafeLoader)["tools"]
    weapon = tools["resolve_combat_attack"]
    assert weapon["effects"][0]["call"] == WEAPON_NODE
    assert len(weapon["effects"]) > 1
    return joined, deepcopy(weapon["effects"][1:])


def test_actual_final_exile_inverse_preserves_outer_tails_and_native_typed_weapon():
    joined, tails = _joined()
    restored = yaml.load(restore_runtime(joined), Loader=yaml.CSafeLoader)["tools"]
    actual = restored["resolve_combat_attack"]
    ordinary = bind_exile(_dependency_runtime()[1].decode(), {"resolve_combat_attack"})
    expected = yaml.load(ordinary, Loader=yaml.CSafeLoader)["tools"]["resolve_combat_attack"]
    assert actual == expected
    assert actual["effects"][-len(tails) :] == tails
    assert any(e.get("call") == "resolve_srd_attack_conditions" for e in actual["effects"])
    assert any(e.get("set", {}).get("field") == "_critical_hit" for e in actual["effects"])
    assert "_true_strike_context" not in str(actual)


def test_actual_final_exile_regeneration_retains_one_outer_settlement_and_repeats():
    joined, tails = _joined()
    rebound = bind_runtime_consumers(restore_runtime(joined))
    tools = yaml.load(rebound, Loader=yaml.CSafeLoader)["tools"]
    assert tools["resolve_combat_attack"]["effects"][1:] == tails
    assert not any(e == tail for e in tools[WEAPON_NODE]["effects"] for tail in tails)
    assert bind_exile(rebound, {"resolve_combat_attack"}) == rebound
    assert bind_runtime_consumers(rebound) == rebound
    assert restore_runtime(restore_runtime(rebound)) == restore_runtime(rebound)
    assert bind_runtime_consumers(restore_runtime(rebound)) == rebound
    before = yaml.load(joined, Loader=yaml.CSafeLoader)["tools"]
    assert tools[WEAPON_NODE] == before[WEAPON_NODE]
    assert tools["apply_srd_true_strike_weapon_hit"] == before["apply_srd_true_strike_weapon_hit"]
    assert tools["d20_test"] == before["d20_test"]
