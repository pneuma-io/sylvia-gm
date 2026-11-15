"""Actual Meteor components retain type locals; native Ice attacks keep their suffix."""

import importlib.util
import sys
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from scripts import srd_object_packet_types as types
from scripts import srd_spell_object_damage as damage
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm import dice
from sylvia_gm.application import package_runtime
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene

BASE = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-object-packet-types/input-1"))
ICE_RUNTIME = BASE / "environment/.scratch/fc14-environment-wall-ice-runtime-v1.yaml"
LIGHT = BASE / "illumination/.scratch/area-object-consequences-v2/authored/scripts"
SUN = BASE / "illumination/.scratch/area-consequences-ancestry-v1/srd_sunburst_geometry.py"
STORAGE_RUNTIME = Path(".scratch/prepared-object-storage-scope-v1/runtime.yaml")
SRD = budget.SRD


def _read(path, expected):
    raw = path.read_bytes()
    assert sha256(raw).hexdigest() == expected
    return raw


def _module(name, path, expected):
    _read(path, expected)
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@lru_cache
def _runtime(corrected=True):
    """Compose genuine frozen Ice and current area owners; no family regeneration."""
    assert sha256(Path(damage.__file__).read_bytes()).hexdigest() == (
        "561d4e2ba20b5b8a483b3a4b8f193376032ee9d707b469199cef32feaa85d343"
    )
    _module(
        "scripts.srd_full.srd_sunburst_geometry", SUN,
        "66def10ee1dcb8061a28691f78cb1495a9b9ca4826e1c9e9ec64cf3f88438231",
    )
    light = _module(
        "scripts._object_packet_light", LIGHT / "srd_spell_light_damage_riders.py",
        "b01ae764f7d22f5526fbb814ec6ad0928b0bcb4f47b0fdf5b402bb9f4a662bb5",
    )
    raw = _read(
        ICE_RUNTIME, "4f913b88be364f6d9c415021c12152b4a38b3fa1b88ba8437c8c89f7db93ff4e",
    ).decode().replace("\r\n", "\n")
    encoded = light.bind_area_consequences(damage.bind_runtime_consumers(raw))
    if corrected:
        encoded = types.bind_runtime_consumers(encoded)
    return encoded.encode(), yaml.load(encoded, Loader=yaml.CSafeLoader)


@lru_cache
def _mechanic(name):
    encoded, runtime = _runtime()
    return materialize_package_mechanic(
        package_id=SRD, package_version="0.3.1", package_sha256="a" * 64,
        runtime_bytes=encoded,
        declaration=ToolDeclaration(
            name=name, capability="dnd.srd.5.2.1.action-economy",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",), turn_statuses=("open",), state_predicates=(),
        ),
    )


@pytest.fixture
def owners(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[1]["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)


def test_only_component_type_segments_change_and_canonical_storage_remains_exact():
    encoded, before = _runtime(False)
    changed = types.bind_runtime_consumers(encoded.decode())
    after = yaml.load(changed, Loader=yaml.CSafeLoader)
    assert types.bind_runtime_consumers(changed) == changed
    assert {
        name for name, tool in after["tools"].items() if tool != before["tools"][name]
    } == set(types.PACKETS)
    for name in types.PACKETS:
        restored = deepcopy(after["tools"][name])
        for effects in types._lists(restored["effects"]):
            for index, effect in enumerate(effects):
                assignment = effect.get("set", {})
                if assignment.get("field") != types.LOCAL:
                    continue
                number = assignment["value"].split("_")[1]
                end = next(
                    i for i in range(index + 1, len(effects))
                    if effects[i].get("set", {}).get("field")
                    == f"_area_object_component_{number}_applied"
                )
                assignment["field"] = "damage_type"
                # Reversing only the two copied segments must recover every original byte of data.
                def original(value):
                    if isinstance(value, str):
                        return value.replace(types.LOCAL, "damage_type")
                    if isinstance(value, list):
                        return [original(v) for v in value]
                    if isinstance(value, dict):
                        return {k: original(v) for k, v in value.items()}
                    return value
                effects[index + 1 : end] = original(effects[index + 1 : end])
        assert restored == before["tools"][name]
    storage = _read(
        STORAGE_RUNTIME, "d4f579cd7cfa4a6f01e6dc44e91aacbca390e7b6189addc63f71ed38118341f9",
    )
    stored = yaml.load(storage, Loader=yaml.CSafeLoader)
    rebound = yaml.load(types.bind_runtime_consumers(storage.decode()), Loader=yaml.CSafeLoader)
    for name in ("apply_srd_entity_object_damage", "apply_srd_entity_teleport_object_damage",
                 "persist_srd_entity_object_damage", "qualify_srd_entity_prepared_object"):
        assert rebound["tools"][name] == stored["tools"][name]


def _paid_ice():
    state, data, meteor = cards._spell_case("meteor_swarm")
    _admit_scene(state)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    for row in scene["entities"]:
        if row["id"] == "guard":
            row["position"] = [5, -5, 0]
        elif row["id"] == "other":
            row["position"] = [10, 8, 0]
    scene["geometry"].append({
        "id": "floor", "map_id": scene["map"]["id"],
        "volume": {"kind": "box", "min": [-500, -500, -1], "max": [500, 500, 0]},
        "active": True, "movement": "blocking", "occupies_cells": False,
        "visibility": "opaque", "metadata": {"plane": "material", "ground": True},
        "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
    })
    scene["zones"].append({
        "id": "daylight", "map_id": scene["map"]["id"],
        "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
        "metadata": {"plane": "material", "lighting": "bright"},
    })
    row = deepcopy(scene["entities"][0])
    row.update(id="ice_mage", position=[0, 30, 0])
    row["metadata"]["participant_ref"] = "ice_mage"
    scene["entities"].append(row)
    ice_state, _, ice = cards._spell_case("wall_of_ice")
    materials = ice_state[f"{SRD}.entities"]["hero"]["spell_materials"]
    budget._run(
        "prepare_srd_creature", state, creature_id="ice_mage", name="Ice caster",
        source="Synthetic spellcaster.", armor_class=12, hit_points=25, speed=30,
        initiative_modifier=0, size_rank=2, constitution_save=6,
        spellcasting_ability="intelligence", spellcasting_modifier=4,
        spell_attack_bonus=7, spell_save_dc=15,
        spells=[{"spell": "wall_of_ice", "uses_per_day": 1}],
        inventory=[m["inventory_item"] for m in materials], spell_materials=materials,
    )
    ice.pop("caster")
    ice.pop("slot_resource")
    ice.update(caster_state="ice_mage", environment_origin=[20, 0, 0],
               environment_wall_axis="north_south")
    assert budget._run(
        "cast_spell_outside_combat", state, participant_data=data, **ice,
    ).result["status"] == "committed"
    return state, data, meteor


def test_paid_meteor_component_types_and_native_paid_ice_fire_suffix(owners, monkeypatch):
    state, data, params = _paid_ice()
    panels = [r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"]
              if r["metadata"].get("srd_spell_object", {}).get("kind") == "wall_ice_panel"]
    assert len(panels) == 10

    class Rolls:
        def __init__(self):
            self.faces = []

        def randint(self, low, high):
            self.faces.append(high)
            return 10 if high == 20 else 1

    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    # The ordinary singleton attack keeps its original damage_type formal and Ice suffix.
    single = budget._run(
        "resolve_srd_spell_object_attack", state, caster_state="hero", object_id=panels[3]["id"],
        attack_modifier=10, damage_die="d6", dice_count=1, damage_type="fire", max_range_feet=120,
    ).result["context"]
    assert single["_object_damage_rolled"] == 1
    assert single["_object_damage_applied"] == 2
    assert single["_object_hp_after"] == 28
    # Meteor selects nonmagical objects; spell Ice is checked through its own native attack path.
    scene = state[f"{SRD}.state"]["spatial_scene"]
    ordinary = deepcopy(scene["entities"][0])
    ordinary.update(id="stone_block", position=[5, 5, 0], height=1)
    ordinary["metadata"] = {"is_object": True, "plane": "material", "fiction": "Stone block"}
    scene["entities"].append(ordinary)
    budget._run(
        "prepare_srd_spell_object", state, object_id="stone_block", material="stone",
        size="large", durability="resilient", magic_status="nonmagical",
    )
    budget._run(
        "record_srd_ordinary_flame", state, object_id="stone_block", exposed=False,
        burning=False, flammability="nonflammable",
    )
    rolls.faces.clear()
    observed = []
    execute = package_runtime.execute_tool

    def packet(*args, **kwargs):
        result = execute(*args, **kwargs)
        if args[0] == damage.PACKET:
            observed.append(deepcopy(result.context))
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", packet)
    state[f"{SRD}.combat"].update(
        encounter_active=True, turn_active=True, active_actor="hero",
    )
    before = state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"]
    params.update(targets=[], area_object_targets=["stone_block"])
    result = budget._run("cast_spell", state, participant_data=data, **params)
    assert result.result["status"] == "committed"
    assert rolls.faces == [6] * 40
    assert len(observed) == 1
    context = observed[0]
    assert context[types.LOCAL] == "bludgeoning"
    assert context["_area_object_component_1_applied"] == 20
    assert context["_area_object_component_2_applied"] == 7
    assert context["_object_damage_applied"] == 27
    assert context["_object_hp_before"] == 27 and context["_object_hp_after"] == 0
    rows = {r["id"]: r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"]}
    breached = rows["stone_block"]
    assert breached["metadata"]["srd_spell_object"]["destroyed"] is True
    assert rows[panels[3]["id"]]["metadata"]["srd_spell_object"]["hp"] == 28
    assert rows[panels[5]["id"]]["metadata"]["srd_spell_object"]["hp"] == 30
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == before - 1
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert any("Object damage 27; HP 27 -> 0" in r["value"]
               for r in result.result["result_card"]["details"])
