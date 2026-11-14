"""Prismatic's final private packets retain mechanics while omitting unused card snapshots."""

import hashlib
from collections import Counter
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
from scripts.srd_full.srd_prismatic_damage_packets import (
    PACKETS,
    PRESENTATION_FIELDS,
    _without_presentation_snapshot,
    bind_runtime,
)
from scripts.srd_full.srd_spell_damage_consumers import extend_runtime as extend_damage
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.runtime import _effect_executor
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_spell_special_damage as special

NATIVE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-prismatic-target-budget/ruleset.yaml")
)


@lru_cache
def _native_runtime():
    encoded = NATIVE.read_bytes()
    assert hashlib.sha256(encoded).hexdigest() == (
        "8499f04a288bd35c92302c21cd01345c43111c9223b60f91a032af071ac62d07"
    )
    assert b"procedures: {}\r\n" in encoded
    final_text = extend_damage(encoded.decode())
    assert "procedures: {}\r\n" in final_text
    final = _load_runtime(final_text)
    text = bind_runtime(final_text)
    settled = _load_runtime(text)
    assert not validate_ruleset_data(settled)
    assert bind_runtime(text) == text
    assert set(settled["tools"]) - set(final["tools"]) == set(PACKETS)
    for name, parent in PACKETS.items():
        original = final["tools"][parent]
        expected = deepcopy(original)
        expected["effects"] = [
            effect
            for effect in original["effects"]
            if effect.get("set", {}).get("field") not in PRESENTATION_FIELDS
        ]
        assert settled["tools"][name] == expected
        assert settled["tools"][parent] == original
        assert len(original["effects"]) - len(expected["effects"]) == (
            7 if parent == "apply_srd_qualifying_composite_damage" else 8
        )
        assert "observe_srd_time_stop_creature_effect" in str(expected["effects"])
    assert all(
        tool == settled["tools"][name]
        for name, tool in final["tools"].items()
        if name != "resolve_srd_prismatic_target"
    )
    return text.encode(), settled["definitions"]["spells"]


@pytest.fixture
def native_packets(monkeypatch):
    special.compiled_special.__wrapped__(monkeypatch, _native_runtime())
    actual = _effect_executor._execute_effect
    counts = Counter()
    budgets = []

    def measured(effect, context, tool_executor=None):
        counts[context.current_tool] += 1
        if not budgets and context.budget is not None:
            budgets.append(context.budget)
        return actual(effect, context, tool_executor)

    monkeypatch.setattr(_effect_executor, "_execute_effect", measured)
    return counts, budgets


@pytest.mark.parametrize("success", [True, False])
def test_actual_native64_prismatic_keeps_every_packet_and_final_card(
    monkeypatch, native_packets, success
):
    counts, budgets = native_packets
    special.test_public_prismatic_complete64_scene_creatures_and_packets(monkeypatch, success)
    assert counts["apply_srd_prismatic_damage"] == 29 * 64
    assert counts["record_srd_prismatic_target_result"] == 2 * 64
    assert counts["d20_test"] == 67 * 64
    assert budgets[0].max_operations == 10000
    assert budgets[0].operations <= 10000
    print("native64 operations", success, budgets[0].operations)


@pytest.mark.parametrize("success", [True, False])
def test_actual_special_ray_keeps_rerolls_shared_save_and_card(
    monkeypatch, native_packets, success
):
    special.test_public_prismatic_special_retains_rerolls_and_one_shared_save(monkeypatch, success)


@pytest.mark.parametrize(
    "rays,extra,immunities,expected",
    [([1], 1, [], 7), ([8, 1, 2], 3, ["fire", "acid"], 1)],
)
def test_actual_curse_and_typed_immunity_still_reach_final_observed_packet(
    monkeypatch, native_packets, rays, extra, immunities, expected
):
    special.test_actual_prismatic_odd_save_packet_carries_remainder_through_rays_and_curse(
        monkeypatch, rays, extra, immunities, expected
    )


@pytest.mark.parametrize("field", sorted(PRESENTATION_FIELDS))
def test_packet_clone_rejects_removed_value_required_by_a_retained_callback(field):
    original = _load_runtime(NATIVE.read_bytes())["tools"]["apply_srd_qualifying_damage"]
    original["effects"].append({"call": "required_callback", "export_context": {field: field}})
    with pytest.raises(SystemExit, match="required by a retained consumer"):
        _without_presentation_snapshot(original)
