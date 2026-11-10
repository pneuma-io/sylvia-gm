"""Prove later encounter admission keeps existing spell/object authority before initiative."""

import json
import os
import re
import zipfile
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.srd_information_spells import _movement_tool
from scripts.srd_lite_combat_scene_reentry import (
    ADMIT,
    CAPTURE,
    MOVEMENT_ERROR,
    MOVEMENT_ROOTS,
    RECONCILE,
    RECONCILE_ACTORS,
    RECONCILE_MAGE_HAND,
    RECONCILE_STEED,
    REENTRY_GUIDANCE,
    _admission_tool,
    bind_combat_only_movement,
    bind_combat_scene_reentry,
)
from scripts.srd_lite_combat_scene_setup import GUIDANCE, ROOTS, _setup_effects
from scripts.srd_lite_exploration_objects import _capture_tool, _reconcile_tool
from scripts.srd_timed_actor_defenses import _load_runtime
from tests.srd_lite_combat_scene_setup_fixtures import initial
from tests.srd_lite_mass_cure_geometry_fixtures import NAMESPACE, _calls, _wall

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._schema_validator import validate_parameter_declarations
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget


def test_reentry_guidance_keeps_initial_scene_format_and_actor_binding():
    assert "format=1" in REENTRY_GUIDANCE
    assert "grid.cell_size/unit='ft'/origin" in REENTRY_GUIDANCE
    assert "participant_ref equal to its saved actor ID" in REENTRY_GUIDANCE
    assert "creature_type, plane and lighting" in REENTRY_GUIDANCE
    assert "For the same map ID, keep the existing bounds, grid and layers" in REENTRY_GUIDANCE


@pytest.mark.parametrize("root", MOVEMENT_ROOTS)
def test_coordinate_movement_rejects_exploration_before_scene_reads_or_costs(root):
    native = _movement_tool({})
    data = {"tools": {name: deepcopy(native) for name in MOVEMENT_ROOTS}}
    bind_combat_only_movement(data)
    expected = deepcopy(native["effects"])
    assert all(data["tools"][name]["effects"] == expected for name in MOVEMENT_ROOTS)
    parsed = RulesetRuntime(ruleset_dict=data)
    value = {
        NAMESPACE + ".state": {"lasting_tick": 20},
        NAMESPACE + ".combat": {"encounter_active": False},
        NAMESPACE + ".entities": {"hero": {"speed": 30}},
    }
    before = deepcopy(value)
    bridge = AuthorityMemoryStateBridge(NAMESPACE, value)
    rng = dice._rng.getstate()
    result = execute_tool(
        root,
        {"actor_state": "hero", "destination": [3, 4, 0]},
        parsed.tools,
        bridge,
        parsed.ruleset_config,
        parsed.definitions,
        combat_state=bridge.combat_state,
        budget=ExecutionBudget(),
    )
    assert not result.success and MOVEMENT_ERROR in result.error
    assert bridge.get_entity("state") == before[NAMESPACE + ".state"]
    assert bridge.get_entity("hero") == before[NAMESPACE + ".entities"]["hero"]
    assert bridge.combat_state == before[NAMESPACE + ".combat"]
    assert value == before and dice._rng.getstate() == rng


@pytest.mark.parametrize("exploration_objects", [False, True])
@pytest.mark.parametrize("created_actors", [False, True])
@pytest.mark.parametrize("mage_hand", [False, True])
def test_generated_admission_uses_bounded_expressions_and_local_collections(
    exploration_objects, created_actors, mage_hand
):
    """The runtime caps expressions at 500, literals at 200 and resolves exact loop locals."""

    def check(value):
        if isinstance(value, str):
            assert len(value) <= 500, value
            for literal in re.findall(r"'([^']*)'", value):
                assert len(literal) <= 200, value
        elif isinstance(value, list):
            for child in value:
                check(child)
        elif isinstance(value, dict):
            if "for_each" in value:
                assert re.fullmatch(r"[A-Za-z_][A-Za-z_0-9]*", value["for_each"]["collection"])
            for child in value.values():
                check(child)

    check(_admission_tool(exploration_objects, created_actors, mage_hand)["effects"])


def test_pending_steed_hook_follows_generic_merge_before_created_actor_admission():
    calls = [row for row in _admission_tool(True, True, True, True)["effects"] if "call" in row]
    assert [row["call"] for row in calls] == [
        CAPTURE, RECONCILE_MAGE_HAND, RECONCILE, RECONCILE_STEED, RECONCILE_ACTORS
    ]
    assert calls[3]["params"] == {"lasting_state": "{_lasting_state_id}", "scenes": "{scenes}"}


def test_reconciliation_hook_order_keeps_pending_mage_hand_placement_before_object_admission():
    effects = _admission_tool(True, True, True)["effects"]
    calls = [effect for effect in effects if "call" in effect]
    assert [effect["call"] for effect in calls] == [
        CAPTURE,
        RECONCILE_MAGE_HAND,
        RECONCILE,
        RECONCILE_ACTORS,
    ]
    assert calls[1]["params"] == {
        "lasting_state": "{_lasting_state_id}",
        "scenes": "{scenes}",
    }
    assert (
        effects.index(calls[0])
        < next(
            index
            for index, effect in enumerate(effects)
            if effect.get("condition") == "len(scenes) == 0"
        )
        < effects.index(calls[1])
    )
    assert effects.index(calls[-1]) < next(
        index
        for index, effect in enumerate(effects)
        if effect.get("store_as") == "_reentry_snapshot"
    )


@pytest.fixture(scope="module")
def admission_source():
    """Root parses admitted product 989 once in its contained background job."""
    with zipfile.ZipFile(os.environ["SRD_LITE_CANDIDATE"]) as archive:
        data = _load_runtime(
            archive.read("packages/" + NAMESPACE + "/runtime/ruleset.yaml").decode()
        )
    record = json.loads(
        Path(os.environ["SRD_LITE_CANDIDATE"]).with_name("package-record.json").read_text()
    )
    return data, record


@pytest.fixture(scope="module")
def admission(admission_source):
    source, record = admission_source
    data = deepcopy(source)
    original = deepcopy(data)
    declarations = deepcopy(record)
    assert bind_combat_scene_reentry(data) == [*ROOTS, ADMIT]
    assert not validate_parameter_declarations(data)
    assert record == declarations
    assert data["definitions"] == original["definitions"]
    assert len(data["definitions"]["spells"]) == 96
    for name, owner in original["tools"].items():
        if name not in ROOTS:
            assert data["tools"][name] == owner, name
    for name in ROOTS:
        owner = data["tools"][name]
        assert owner["parameters"] == original["tools"][name]["parameters"]
        assert owner["pre_conditions"] == original["tools"][name]["pre_conditions"]
        assert owner["metadata"] == original["tools"][name]["metadata"]
        assert owner["effects"][2:] == original["tools"][name]["effects"][1:]
        assert owner["description"] == original["tools"][name]["description"].replace(
            GUIDANCE, REENTRY_GUIDANCE
        )
    with pytest.raises(ValueError, match="already bound"):
        bind_combat_scene_reentry(deepcopy(data))
    return _preflights(data)


@pytest.fixture(scope="module")
def admission_with_objects(admission_source):
    data = deepcopy(admission_source[0])
    data["tools"].update({CAPTURE: _capture_tool(), RECONCILE: _reconcile_tool()})
    bind_combat_scene_reentry(data)
    return _preflights(data)


def _preflights(data):
    pending = []
    for name in ROOTS:
        tool = deepcopy(data["tools"][name])
        tool["effects"] = tool["effects"][: len(_setup_effects(name)) + 1]
        data["tools"]["preflight_" + name] = tool
        pending.append("preflight_" + name)
    closure = {}
    while pending:
        name = pending.pop()
        if name not in closure:
            closure[name] = data["tools"][name]
            pending.extend(_calls(closure[name]["effects"]))
    subset = {key: value for key, value in data.items() if key not in ("tools", "procedures")}
    subset["tools"] = closure
    return RulesetRuntime(ruleset_dict=subset)


def check(parsed, root, value, requested=None):
    parameters = (
        {"combatants": ["hero", "guard", "other"]}
        if root == ROOTS[1]
        else {
            "first_actor": "hero",
            "second_actor": "guard",
            "tie_winner": "first",
            "first_surprised": False,
            "second_surprised": False,
        }
    )
    if requested is not None:
        parameters["combat_scenes"] = [requested]
    before = json.dumps(value, sort_keys=True)
    rng = dice._rng.getstate()
    bridge = AuthorityMemoryStateBridge(NAMESPACE, value)

    def nested(name, values, parent):
        return execute_tool(
            name,
            values,
            parsed.tools,
            bridge,
            parsed.ruleset_config,
            parsed.definitions,
            combat_state=parent.combat_state,
            tool_executor=nested,
            budget=parent.budget,
        )

    result = execute_tool(
        "preflight_" + root,
        parameters,
        parsed.tools,
        bridge,
        parsed.ruleset_config,
        parsed.definitions,
        combat_state=bridge.combat_state,
        tool_executor=nested,
        budget=ExecutionBudget(),
    )
    assert dice._rng.getstate() == rng
    assert json.dumps(value, sort_keys=True) == before
    assert "" not in bridge.combat_state
    assert bridge.read_field("state", "spell_force_records") == value[NAMESPACE + ".state"].get(
        "spell_force_records"
    )
    assert bridge.read_field("state", "created_actor_records") == value[NAMESPACE + ".state"].get(
        "created_actor_records"
    )
    return result, bridge


def _saved():
    value, saved = initial()
    saved["map"]["revision"] = 17
    value[NAMESPACE + ".state"].update(spatial_scene=saved, lasting_tick=3)
    return value, saved


def _object(scene, identity="lantern", position=(0, 10, 0), **metadata):
    return {
        "id": identity,
        "map_id": scene["map"]["id"],
        "position": list(position),
        "footprint": {"kind": "box", "half_extents": [0.25, 0.25]},
        "height": 0.5,
        "facing": [1, 0],
        "active": True,
        "movement": "passable",
        "visibility": "clear",
        "metadata": {"is_object": True, "plane": "material", "lighting": "bright", **metadata},
    }


def _new_arena(saved):
    incoming = deepcopy(saved)
    incoming["map"].update(id="second-arena", revision=0)
    incoming.update(geometry=[], zones=[], terrain=[])
    for body in incoming["entities"]:
        body["map_id"] = incoming["map"]["id"]
        body["position"][0] += 20.125
        body["facing"] = [0, 1]
        body["metadata"]["lighting"] = "dim"
    return incoming


















def test_admission_calls_both_object_hooks_before_original_initiative():
    root = ROOTS[0]
    owner = {
        "effects": _setup_effects(root) + [{"dice": {"expression": "d20"}}],
        "description": "Original." + GUIDANCE,
    }
    data = {"tools": {name: deepcopy(owner) for name in ROOTS}}
    data["tools"].update({CAPTURE: _capture_tool(), RECONCILE: _reconcile_tool()})
    bind_combat_scene_reentry(data)
    effects = data["tools"][ADMIT]["effects"]
    calls = [effect["call"] for effect in effects if "call" in effect]
    assert calls == [CAPTURE, RECONCILE]
    capture_index = next(i for i, effect in enumerate(effects) if effect.get("call") == CAPTURE)
    reconcile_index = next(i for i, effect in enumerate(effects) if effect.get("call") == RECONCILE)
    assign_index = next(i for i, effect in enumerate(effects) if "on_false" in effect)
    assert capture_index < assign_index < reconcile_index
    assert data["tools"][root]["effects"][-1] == {"dice": {"expression": "d20"}}


def _manage(value, body, pending_patch=None):
    state = value[NAMESPACE + ".state"]
    state["lite_exploration_object_ids"] = [body["id"]]
    state["lite_exploration_objects"] = {
        body["id"]: {
            "id": body["id"],
            "body": deepcopy(body),
            "pending_scene_reconciliation": bool(pending_patch),
            "pending_patch": pending_patch or {},
            "pending_frame_patch": {},
        }
    }
