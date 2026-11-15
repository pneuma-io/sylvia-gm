"""Printed consequences reuse the native shared area, object and body owners."""

import ast
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts import srd_spell_light_damage_riders as consequences
from scripts import srd_spell_object_damage as object_damage
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_control_behaviors import _when as branch

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene

PARENT = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-area-spell-consequences/ruleset.yaml")
)
PARENT_SHA = "64bc7b418f7918e7e7bf4e8bb46079b48fd19e72fbda5199ed6453fa9c011305"


@lru_cache
def _runtime():
    """Apply only these two binders to the retained runtime, with no family regeneration."""
    raw = PARENT.read_bytes()
    assert sha256(raw).hexdigest() == PARENT_SHA
    original = PARENT.read_text(encoding="utf-8")
    encoded = consequences.bind_area_consequences(object_damage.bind_runtime_consumers(original))
    assert (
        consequences.bind_area_consequences(object_damage.bind_runtime_consumers(encoded))
        == encoded
    )
    runtime = yaml.load(encoded, Loader=yaml.CSafeLoader)
    parent = yaml.load(original, Loader=yaml.CSafeLoader)
    assert runtime["tools"]["d20_test"] == parent["tools"]["d20_test"]
    assert (
        runtime["tools"]["validate_spell_chain_target_count"]
        == parent["tools"]["validate_spell_chain_target_count"]
    )
    return encoded.encode(), runtime


@lru_cache
def _mechanic(name):
    encoded, runtime = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=encoded,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.action-economy",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def native_owner(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)


@pytest.mark.parametrize("spell,slot", [("burning_hands", 1), ("fire_storm", 7)])
def test_paid_empty_creature_area_ignites_only_selected_unheld_combustible_objects(spell, slot):
    state, data, params = _spell_case(spell)
    _admit_scene(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    for name in ("fuel", "held_fuel", "stone"):
        row = deepcopy(scene["entities"][1])
        row.update(id=name, position=[5, 0, 0], height=1)
        row["metadata"] = {"is_object": True, "plane": "material", "fiction": name}
        scene["entities"].append(row)
    combat = state[f"{SRD}.combat"]
    combat.update(encounter_active=False, turn_active=False)
    for name in ("fuel", "held_fuel", "stone"):
        result = budget._run(
            "record_srd_ordinary_flame",
            state,
            object_id=name,
            exposed=False,
            burning=False,
            flammability="nonflammable" if name == "stone" else "flammable",
        )
        assert result.result["status"] == "committed"
    rows = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    next(r for r in rows if r["id"] == "held_fuel")["metadata"]["carried_by"] = "hero"
    combat = state[f"{SRD}.combat"]
    combat.update(encounter_active=True, turn_active=True, active_actor="hero")
    hp = state[f"{SRD}.entities"]["guard"]["hp"]
    resource = f"spell_slot_{slot}"
    slots = state[f"{SRD}.entities"]["hero"]["resources"][resource]
    params.update(targets=[], area_object_targets=["fuel", "held_fuel", "stone"])
    result = budget._run("cast_spell", state, participant_data=data, **params)
    assert result.result["status"] == "committed"
    rows = {r["id"]: r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"]}
    assert rows["fuel"]["metadata"]["srd_ordinary_flame"]["burning"] is True
    assert rows["held_fuel"]["metadata"]["srd_ordinary_flame"]["burning"] is False
    assert rows["stone"]["metadata"]["srd_ordinary_flame"]["burning"] is False
    assert rows["fuel"]["metadata"]["srd_ordinary_flame"]["exposed"] is False
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp
    assert state[f"{SRD}.entities"]["hero"]["resources"][resource] == slots - 1
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert any(row["value"] == "Starts burning." for row in result.result["result_card"]["details"])


def test_paid_cone_cold_freezes_actual_killed_body_until_observed_thaw():
    state, data, params = _spell_case("cone_of_cold")
    entities = state[f"{SRD}.entities"]
    entities["guard"].update(hp=1, temp_hp=0)
    entities["guard"]["saving_throw_modifiers"]["constitution"] = -30
    entities["other"]["damage_immunities"] = ["cold"]
    params.update(targets=["guard", "other"])
    result = budget._run("cast_spell", state, participant_data=data, **params)
    assert result.result["status"] == "committed"
    entities = state[f"{SRD}.entities"]
    target = entities["guard"]
    assert target["is_alive"] is False
    assert target["body_frozen_statue"] == {
        "spell": "cone_of_cold",
        "body_source_id": "guard",
        "death_tick": target["death_tick"],
    }
    assert entities["other"]["is_alive"] is True
    assert entities["other"].get("body_frozen_statue") is None
    assert any("frozen statue" in row["value"] for row in result.result["result_card"]["details"])
    before = deepcopy(target)
    assert "dead" in target["conditions"]
    result = budget._run(
        "record_srd_deceased_body",
        state,
        target_state="guard",
        death_tick=0,
        old_age=False,
        soul_willing=True,
        body_available=True,
        vital_parts_present=True,
        living_creature_type="humanoid",
        name="Guard",
        thawed=True,
    )
    assert result.result["status"] == "committed"
    target = state[f"{SRD}.entities"]["guard"]
    assert target["body_frozen_statue"] is None
    assert target["is_alive"] is False and target["hp"] == before["hp"]
    assert target["conditions"] == before["conditions"]


def test_paid_meteor_uses_shared_two_type_roll_for_nonmagical_unheld_objects_once():
    state, data, params = _spell_case("meteor_swarm")
    _admit_scene(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    for name in ("fuel", "held_fuel", "magical_fuel"):
        row = deepcopy(scene["entities"][1])
        row.update(id=name, position=[5, 0, 0], height=1)
        row["metadata"] = {"is_object": True, "plane": "material", "fiction": name}
        scene["entities"].append(row)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    for name in ("fuel", "held_fuel", "magical_fuel"):
        result = budget._run(
            "prepare_srd_spell_object",
            state,
            object_id=name,
            material="wood",
            size="large",
            durability="resilient",
            magic_status="magical" if name == "magical_fuel" else "nonmagical",
        )
        assert result.result["status"] == "committed"
        result = budget._run(
            "record_srd_ordinary_flame",
            state,
            object_id=name,
            exposed=False,
            burning=False,
            flammability="flammable",
        )
        assert result.result["status"] == "committed"
    rows = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    next(r for r in rows if r["id"] == "held_fuel")["metadata"]["carried_by"] = "hero"
    revision = state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"]
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=True, active_actor="hero")
    params.update(targets=[], area_object_targets=["fuel", "held_fuel", "magical_fuel"])
    result = budget._run("cast_spell", state, participant_data=data, **params)
    assert result.result["status"] == "committed"
    rows = {r["id"]: r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"]}
    assert rows["fuel"]["metadata"]["srd_spell_object"]["hp"] == 0
    assert rows["fuel"]["metadata"]["srd_spell_object"]["destroyed"] is True
    assert rows["fuel"]["metadata"]["srd_ordinary_flame"]["burning"] is True
    for name in ("held_fuel", "magical_fuel"):
        assert rows[name]["metadata"]["srd_spell_object"]["hp"] == 27
        assert rows[name]["metadata"]["srd_ordinary_flame"]["burning"] is False
    # One original object-HP transition, then one ignition mutation; no second damage event.
    assert state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"] == revision + 2
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == 2
    assert state[f"{SRD}.combat"]["action_available"] is False
    details = result.result["result_card"]["details"]
    assert any("Object damage 27; HP 27 -> 0" in row["value"] for row in details)


def test_object_receiver_retains_final_ice_mitigation_and_destruction_callbacks():
    ice_path = Path(
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-area-spell-consequences/srd_environment_wall_ice.py")
    )
    jar_path = Path(
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-area-spell-consequences/srd_magic_jar_container.py")
    )
    assert sha256(ice_path.read_bytes()).hexdigest() == (
        "0b92572e7e7a42efdc1a6564394ed096cd9931e8f09143268bbea226f1a62f77"
    )
    assert sha256(jar_path.read_bytes()).hexdigest() == (
        "0d6ce0938999e9a1c61df8d0f69ea27d8e845a243eae20896f8cbfaedfbc644b"
    )
    # Evaluate only the genuine small object binder, without regenerating its family.
    tree = ast.parse(ice_path.read_text(encoding="utf-8"))
    binder = next(
        n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "bind_objects"
    )
    namespace = {
        "deepcopy": deepcopy,
        "_effect_lists": consequences._effect_lists,
        **{name: getattr(consequences, name) for name in ("_call", "_set", "_require")},
        "_when": branch,
        "_push": consequences._push,
    }
    exec(compile(ast.Module(body=[binder], type_ignores=[]), str(ice_path), "exec"), namespace)
    tools = deepcopy(_runtime()[1]["tools"])
    tools.update(namespace["bind_objects"](tools))
    jar_tree = ast.parse(jar_path.read_text(encoding="utf-8"))
    literals = {}
    for node in jar_tree.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"HOOK_CONDITION", "CALLBACK"}
        ):
            literals[node.targets[0].id] = ast.literal_eval(node.value)
    owner = tools["resolve_srd_spell_object_attack"]
    owner["effects"].append(
        consequences._when(
            literals["HOOK_CONDITION"],
            [consequences._call(literals["CALLBACK"], {"object_id": "{object_id}"})],
        )
    )
    packet = object_damage._packet(tools)
    qualified = packet["effects"][-1]["on_true"][-1]["on_true"]
    hit_index = next(
        i
        for i, e in enumerate(owner["effects"])
        if e.get("condition")
        == "_object_attack_kind == 'hit' or _object_attack_kind == 'critical_hit'"
    )
    tail = owner["effects"][hit_index + 1 :]
    normalized_tail = object_damage._tool_scene("", [], deepcopy(tail))["effects"]
    assert qualified[-len(tail) :] == normalized_tail
    assert "settle_srd_wall_of_ice_panel" in str(tail)
    assert "end_srd_destroyed_magic_jar_container" in str(tail)
    assert str(qualified).count("(_object_damage_rolled) * 2") == 2
    assert "qualify_srd_wall_of_ice_object" in str(object_damage._qualification(tools))
    assert "_object_attack_kind" not in str(packet)
