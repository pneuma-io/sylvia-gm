"""Flame preserves actual Ice vulnerability and final object destruction callbacks."""

import hashlib
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_retained_spell_attacks import _lists

from sylvia_gm import dice
from tests import test_srd_flame_blade as flame
from tests import test_srd_flame_blade_light as light
from tests import test_srd_flame_blade_objects as objects
from tests import test_srd_retained_spell_attack_consumers as retained
from tests import test_srd_retained_spell_attacks as attacks
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_spell_wave3_information import _entity

ICE_RUNTIME = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-flame-blade-ice-objects/fc14-environment-wall-ice-runtime-v1.yaml")
)
JAR_RUNTIME = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-flame-blade-ice-objects/ruleset.yaml")
)
CALLBACK = "end_srd_destroyed_magic_jar_container"
_OBJECT_RUNTIME = objects._runtime


def _read(path, expected):
    encoded = path.read_bytes()
    assert hashlib.sha256(encoded).hexdigest() == expected
    return yaml.load(encoded, Loader=yaml.CSafeLoader)


def _calls(value):
    if isinstance(value, dict):
        if "call" in value:
            yield value["call"]
        for child in value.values():
            yield from _calls(child)
    elif isinstance(value, list):
        for child in value:
            yield from _calls(child)


@lru_cache
def _parent():
    """Borrow only authentic serialized Jar callback closure into the frozen paid Ice graph."""
    runtime = _read(ICE_RUNTIME, "4f913b88be364f6d9c415021c12152b4a38b3fa1b88ba8437c8c89f7db93ff4e")
    jar = _read(JAR_RUNTIME, "50e896ca5e32223a18c3f2ae6276339a4d8eae496a377feeec6ef7bbeafb6e97")
    pending = [CALLBACK]
    while pending:
        name = pending.pop()
        if name in runtime["tools"]:
            continue
        runtime["tools"][name] = deepcopy(jar["tools"][name])
        pending.extend(_calls(runtime["tools"][name]["effects"]))
    callbacks = [
        e
        for e in jar["tools"]["resolve_srd_spell_object_attack"]["effects"]
        if CALLBACK in set(_calls(e))
    ]
    assert len(callbacks) == 1
    runtime["tools"]["resolve_srd_spell_object_attack"]["effects"] += deepcopy(callbacks)
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@lru_cache
def _scoped_runtime():
    for owner in (attacks, retained, flame, light, objects):
        owner._runtime.cache_clear()
    return _OBJECT_RUNTIME()


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(attacks, "_source_runtime", _parent)
    _scoped_runtime()
    monkeypatch.setattr(objects, "_runtime", _scoped_runtime)
    objects._owners.__wrapped__(monkeypatch)


def _case():
    state, data, flame_params = flame._case(slot=6)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"].append(
        {
            "id": "floor",
            "map_id": scene["map"]["id"],
            "volume": {"kind": "box", "min": [-500, -500, -1], "max": [500, 500, 0]},
            "active": True,
            "movement": "blocking",
            "occupies_cells": False,
            "visibility": "opaque",
            "metadata": {"plane": "material", "ground": True, "material": "stone"},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )
    scene["zones"].append(
        {
            "id": "ice-daylight",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
            "metadata": {"plane": "material", "lighting": "bright"},
        }
    )
    scene["entities"].append(
        _entity("ice_mage", [0, 30, 0], participant_ref="ice_mage", creature_type="humanoid")
    )
    ice_state, _, ice_params = flame.cards._spell_case("wall_of_ice")
    materials = ice_state[f"{SRD}.entities"]["hero"]["spell_materials"]
    _run(
        "prepare_srd_creature",
        state,
        creature_id="ice_mage",
        name="Ice caster",
        source="Synthetic spellcaster.",
        armor_class=12,
        hit_points=25,
        speed=30,
        initiative_modifier=0,
        size_rank=2,
        constitution_save=6,
        spellcasting_ability="intelligence",
        spellcasting_modifier=4,
        spell_attack_bonus=7,
        spell_save_dc=15,
        spells=[{"spell": "wall_of_ice", "uses_per_day": 1}],
        inventory=[m["inventory_item"] for m in materials],
        spell_materials=materials,
    )
    ice_params.pop("caster")
    ice_params.pop("slot_resource")
    ice_params.update(
        caster_state="ice_mage", environment_origin=[20, 0, 0], environment_wall_axis="north_south"
    )
    paid = _run("cast_spell_outside_combat", state, participant_data=data, **ice_params).result
    assert paid["result_card"]
    # Spatial persistence replaces the scene snapshot, so use its authoritative current rows.
    panels = [
        r
        for r in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if r["metadata"].get("srd_spell_object", {}).get("kind") == "wall_ice_panel"
    ]
    assert len(panels) == 10
    assert state[f"{SRD}.entities"]["ice_mage"]["concentration_spell_id"] == "wall_of_ice"
    flame._cast(state, data, flame_params)
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[15, -5, 0])
    return state, data, panels[4]["id"]


@pytest.mark.parametrize("enfeebled,adjusted", [(False, 25), (True, 17)])
def test_actual_paid_flame_breaches_paid_ice_panel_with_original_typed_suffix(
    monkeypatch, enfeebled, adjusted
):
    state, data, panel_id = _case()
    if enfeebled:
        objects._enfeeble(state, data, caster_id="ray_mage", position=(0, -30, 0))
    source = deepcopy(flame._source(state))

    class Rolls(attacks.Rolls):
        def randint(self, low, high):
            value = super().randint(low, high)
            return 8 if high == 8 else value

    rolls = Rolls(attack=10)
    monkeypatch.setattr(dice, "_rng", rolls)
    budgets = []
    actual = flame.ExecutionBudget.consume_operation

    def count(budget):
        if not budgets or budgets[-1] is not budget:
            budgets.append(budget)
        actual(budget)

    monkeypatch.setattr(flame.ExecutionBudget, "consume_operation", count)
    result = _run(
        "use_srd_flame_blade_object",
        state,
        participant_data=data,
        caster="hero",
        caster_state="hero",
        object_id=panel_id,
    ).result
    context = result["context"]
    assert context["_object_damage_rolled"] == 25
    assert context["_object_damage_adjusted"] == adjusted
    assert context["_object_damage_applied"] == 30
    panel = next(
        r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == panel_id
    )
    assert panel["metadata"]["srd_spell_object"]["hp"] == 0
    assert panel["metadata"]["srd_spell_object"]["destroyed"] is True
    assert panel["active"] and panel["movement"] == "passable"
    assert source == flame._source(state)
    assert state[f"{SRD}.entities"]["ice_mage"]["concentration_spell_id"] == "wall_of_ice"
    assert rolls.sides == [20] + [6] * 7 + ([8] if enfeebled else [])
    assert result["result_card"]
    assert budgets and all(b.max_operations == 10000 for b in budgets)
    print("Actual Flame/Ice scalar operations:", max(b.operations for b in budgets))


def test_final_clone_preserves_exact_ice_settlement_and_jar_destruction_callbacks():
    runtime = objects._runtime()[0]
    original = runtime["tools"]["resolve_srd_spell_object_attack"]["effects"]
    clone = runtime["tools"]["resolve_srd_flame_blade_object_attack"]["effects"]
    for callback in (CALLBACK, "settle_srd_wall_of_ice_panel"):
        before = [e for e in original if callback in set(_calls(e))]
        after = [e for e in clone if callback in set(_calls(e))]
        assert before and after == before
    applied = [
        e["set"]["value"]
        for sequence in _lists(clone)
        for e in sequence
        if e.get("set", {}).get("field") == "_object_damage_applied" and e["set"]["value"] != 0
    ]
    assert set(applied) == {
        "min(_object_hp_before, (_object_damage_adjusted) * 2)",
        "min(_object_hp_before, _object_damage_adjusted)",
    }


def test_original_ordinary_wood_path_on_final_ice_jar_parent(monkeypatch):
    objects.test_one_real_object_roll_original_slot_modifier_once_and_one_durability_event(
        monkeypatch, 10, 2, 13, 13
    )


def test_original_paid_ray_path_on_final_ice_jar_parent(monkeypatch):
    objects.test_actual_paid_ray_penalty_occurs_once_after_fire_dice_plus_modifier(monkeypatch)
