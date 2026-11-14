"""True Strike inverts its own delegate and refreshes the actual regenerated weapon source."""

from copy import deepcopy

import yaml
from scripts.srd_full.srd_true_strike import WEAPON_NODE, bind_runtime_consumers, restore_runtime

from tests.srd_full.test_srd_true_strike import _dependency_runtime, _runtime


def test_inverse_recovers_exact_ordinary_top_level_source_for_reaction_discovery():
    original = _dependency_runtime()[0]["tools"]["resolve_combat_attack"]
    restored = yaml.load(restore_runtime(_runtime()[1].decode()), Loader=yaml.CSafeLoader)
    assert restored["tools"]["resolve_combat_attack"] == original
    assert any(e.get("call") == "resolve_srd_attack_conditions" for e in original["effects"])
    assert any(e.get("set", {}).get("field") == "_critical_hit" for e in original["effects"])


def test_final_binder_refreshes_existing_private_copy_from_new_actual_source():
    runtime = yaml.load(restore_runtime(_runtime()[1].decode()), Loader=yaml.CSafeLoader)
    actual = deepcopy(runtime["tools"]["resolve_combat_attack"])
    actual["description"] += " Current owning source."
    actual["effects"].append({"set": {"field": "_regenerated_source_witness", "value": 1}})
    runtime["tools"]["resolve_combat_attack"] = actual
    encoded = yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    bound = bind_runtime_consumers(encoded)
    tools = yaml.load(bound, Loader=yaml.CSafeLoader)["tools"]
    assert tools[WEAPON_NODE]["effects"][-1] == actual["effects"][-1]
    assert tools[WEAPON_NODE]["description"] == actual["description"]
    restored = yaml.load(restore_runtime(bound), Loader=yaml.CSafeLoader)["tools"]
    assert restored["resolve_combat_attack"] == actual
    assert bind_runtime_consumers(bound) == bound


def test_inverse_and_final_regeneration_repeat_without_duplicate_dispatch():
    encoded = _runtime()[1].decode()
    restored = restore_runtime(encoded)
    assert restore_runtime(restored) == restored
    regenerated = bind_runtime_consumers(restored)
    assert yaml.load(regenerated, Loader=yaml.CSafeLoader) == _runtime()[0]
