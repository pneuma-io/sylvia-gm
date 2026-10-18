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
from tests.test_srd_lite_combat_scene_setup import initial
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls, _wall

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


@pytest.mark.parametrize("root", ROOTS)
def test_same_map_roster_addition_preserves_every_saved_row(admission, root):
    value, saved = _saved()
    joining_body = saved["entities"].pop(2)
    saved["entities"].append(
        _object(
            saved,
            carried_by="hero",
            srd_spell_object={"hp": 7, "magic": False},
            srd_object_breaks=[],
            srd_object_access={"is_locked": True},
        )
    )
    panel = _object(
        saved,
        "permanent-stone",
        (40, 20, 0),
        solid_wall_source_id="stone-source",
        solid_wall_serial=7,
        srd_spell_object={"kind": "wall_stone_panel", "hp": 29, "destroyed": False},
    )
    saved["entities"].append(panel)
    obstacle = _wall()
    obstacle.update(
        id="saved-door",
        map_id=saved["map"]["id"],
        volume={"kind": "box", "min": [40, 0, 0], "max": [41, 1, 6]},
    )
    saved["geometry"] = [obstacle]
    saved["zones"] = [
        {
            "id": "saved-zone",
            "map_id": saved["map"]["id"],
            "shape": {"kind": "box", "min": [30, 0, 0], "max": [35, 5, 6]},
            "metadata": {"source_environment_id": "retained-web", "private": True},
        }
    ]
    layer = saved["map"]["layers"][0]["id"]
    saved["terrain"] = [{"cell": [40, 40], "layer_id": layer, "cost": 2}]
    prior = deepcopy(saved)
    requested = deepcopy(saved)
    requested["map"]["revision"] = 0
    requested["entities"] = [deepcopy(saved["entities"][0])]
    requested["entities"][0]["position"] = [99, 0, 0]
    requested["entities"][0]["metadata"].update(srd_spell_object={"hp": 999})
    new_object = _object(saved, "new-stool", (60, 10, 0))
    requested["entities"].extend([joining_body, new_object])
    new_terrain = {"cell": [41, 40], "layer_id": layer, "cost": 3}
    requested.update(
        geometry=[],
        zones=[],
        terrain=[
            {"cell": [40, 40], "layer_id": layer, "cost": 1},
            new_terrain,
        ],
    )
    result, bridge = check(admission, root, value, requested)
    assert result.success, result.error
    actual = bridge.read_field("state", "spatial_scene")
    assert actual["entities"] == prior["entities"] + [joining_body, new_object]
    assert actual["geometry"] == prior["geometry"]
    assert actual["zones"] == prior["zones"]
    assert actual["terrain"] == prior["terrain"] + [new_terrain]
    assert actual["map"] == {**prior["map"], "revision": 18}


@pytest.mark.parametrize("root", ROOTS)
def test_same_map_and_omitted_scene_are_idempotent(admission, root):
    value, saved = _saved()
    for requested in (None, deepcopy(saved)):
        result, bridge = check(admission, root, value, requested)
        assert result.success, result.error
        assert bridge.read_field("state", "spatial_scene") == saved


def test_same_map_ignored_edits_do_not_change_authoritative_revision_or_pollute_query_cache(
    admission,
):
    value, saved = _saved()
    requested = deepcopy(saved)
    requested["map"]["revision"] = 1000
    requested["entities"][0]["position"] = [99, 0, 0]
    requested["entities"][0]["metadata"]["srd_spell_object"] = {"hp": 999}
    result, bridge = check(admission, ROOTS[0], value, requested)
    assert result.success, result.error
    assert bridge.read_field("state", "spatial_scene") == saved
    assert bridge.read_field("state", "lite_combat_scene_admission") is None


@pytest.mark.parametrize("defect", ["grid", "bounds", "layers", "duplicate", "cross_kind"])
def test_roster_addition_cannot_replace_coordinate_frame_or_reuse_identity(admission, defect):
    value, saved = _saved()
    requested = deepcopy(saved)
    if defect == "grid":
        requested["map"]["grid"]["cell_size"] = 2.5
    elif defect == "bounds":
        requested["map"]["bounds"]["max"][0] = 300
    elif defect == "layers":
        requested["map"]["layers"][0]["id"] = "renamed-layer"
    elif defect == "duplicate":
        requested["entities"].append(deepcopy(requested["entities"][0]))
    else:
        obstacle = _wall()
        obstacle.update(
            id=saved["entities"][0]["id"],
            map_id=saved["map"]["id"],
            volume={"kind": "box", "min": [40, 0, 0], "max": [41, 1, 6]},
        )
        requested["entities"] = []
        requested["geometry"] = [obstacle]
    result, _ = check(admission, ROOTS[0], value, requested)
    assert not result.success


@pytest.mark.parametrize("root", ROOTS)
def test_new_arena_admits_actual_positions_but_preserves_portable_consequences(admission, root):
    value, saved = _saved()
    saved["entities"].append(
        _object(
            saved,
            carried_by="hero",
            srd_object_breaks=[
                {
                    "id": "crack",
                    "min": [0, 10, 0],
                    "max": [0.125, 10.125, 0.125],
                }
            ],
            srd_spell_object={"hp": 7, "magic": True},
            opaque_cover=True,
        )
    )
    summon = deepcopy(saved["entities"][1])
    summon.update(id="dragon-body", position=[20, 10, 0])
    summon["metadata"].update(
        participant_ref="dragon",
        source_ref="summon_dragon",
        source_caster="hero",
        creature_type="dragon",
    )
    saved["entities"].append(summon)
    value[NAMESPACE + ".state"].update(
        created_actor_records={"dragon": {"active": True, "caster": "hero", "hp": 47}},
        turn_effects=[
            {
                "kind": "illumination_spell",
                "spell": "light",
                "source": "lantern",
                "caster": "hero",
                "origin": [0, 10, 0],
                "expires_tick": 603,
            }
        ],
    )
    value[NAMESPACE + ".entities"]["dragon"] = deepcopy(value[NAMESPACE + ".entities"]["guard"])
    requested = _new_arena(saved)
    target = next(row for row in requested["entities"] if row["id"] == "lantern")
    target.update(footprint={"kind": "circle", "radius": 0.1}, active=False)
    target["metadata"].update(
        srd_spell_object={"hp": 999}, opaque_cover=False, srd_object_breaks=[], carried_by="guard"
    )
    result, bridge = check(admission, root, value, requested)
    assert result.success, result.error
    actual = bridge.read_field("state", "spatial_scene")
    assert actual["map"]["revision"] == saved["map"]["revision"] + 1
    assert bridge.read_field("state", "lite_combat_scene_admission") is None
    lantern = next(row for row in actual["entities"] if row["id"] == "lantern")
    assert lantern["position"] == [20.125, 10, 0]
    assert lantern["footprint"] == {"kind": "box", "half_extents": [0.25, 0.25]}
    assert lantern["active"] is True
    assert lantern["metadata"]["srd_spell_object"] == {"hp": 7, "magic": True}
    assert lantern["metadata"]["opaque_cover"] is True
    assert lantern["metadata"]["carried_by"] == "hero"
    assert lantern["metadata"]["lighting"] == "dim"
    assert lantern["metadata"]["srd_object_breaks"] == [
        {
            "id": "crack",
            "min": [20.125, 10, 0],
            "max": [20.25, 10.125, 0.125],
        }
    ]
    assert (
        next(row for row in actual["entities"] if row["id"] == "dragon-body")["metadata"][
            "source_caster"
        ]
        == "hero"
    )


@pytest.mark.parametrize("defect", ["body_id", "binding", "summon", "holder", "point_cover"])
def test_new_arena_rejects_lost_body_or_source_identity(admission, defect):
    value, saved = _saved()
    saved["entities"].append(_object(saved, carried_by="hero"))
    requested = _new_arena(saved)
    if defect == "body_id":
        requested["entities"][0]["id"] = "replacement-hero-body"
    elif defect == "binding":
        requested["entities"][0]["metadata"]["participant_ref"] = "guard"
    elif defect == "summon":
        saved["entities"][2]["metadata"]["source_ref"] = "find_steed"
        requested["entities"].pop(2)
    elif defect == "holder":
        saved["entities"][-1]["metadata"]["carried_by"] = "missing-holder"
    else:
        saved["entities"][0]["metadata"]["cover_from_points"] = [{"point": [0, 0, 0]}]
    result, _ = check(admission, ROOTS[0], value, requested)
    assert not result.success


@pytest.mark.parametrize(
    "kind",
    [
        "object",
        "destroyed_object",
        "permanent_wall",
        "area",
        "terrain",
        "point_light",
        "owned_zone",
    ],
)
def test_anchored_consequences_block_new_arena_without_being_erased(admission, kind):
    value, saved = _saved()
    state = value[NAMESPACE + ".state"]
    if kind in ("object", "destroyed_object"):
        saved["entities"].append(_object(saved, srd_object_breaks=[]))
        saved["entities"][-1]["active"] = kind != "destroyed_object"
    elif kind == "permanent_wall":
        state.update(
            solid_spell_wall_source_ids=["stone"],
            solid_spell_wall_sources={
                "stone": {
                    "installed": True,
                    "permanent": True,
                    "expires_tick": None,
                    "panel_ids": ["permanent-stone"],
                    "serial": 3,
                },
            },
        )
    elif kind == "area":
        state.update(
            persistent_spell_area_ids=["cloud"],
            persistent_spell_areas={
                "cloud": {"active": True, "origin": [20, 0, 0], "expires_tick": 103},
            },
        )
    elif kind == "terrain":
        state["turn_effects"] = [
            {
                "kind": "ice_storm_terrain",
                "expires_tick": 4,
                "ground_intersections": [{"ground_id": "floor"}],
            }
        ]
    elif kind == "point_light":
        state["turn_effects"] = [
            {
                "kind": "illumination_spell",
                "spell": "darkness",
                "source": "",
                "origin": [20, 0, 0],
                "expires_tick": 103,
            }
        ]
    else:
        saved["zones"] = [
            {
                "id": "owned-zone",
                "map_id": saved["map"]["id"],
                "shape": {"kind": "box", "min": [30, 0, 0], "max": [35, 5, 6]},
                "metadata": {"source_environment_id": "web", "source_sequence": 3},
            }
        ]
    before = deepcopy(state)
    result, _ = check(admission, ROOTS[0], value, _new_arena(saved))
    assert not result.success
    assert state == before


@pytest.mark.parametrize("defect", [None, "cargo_missing", "caster_missing", "separated", "range"])
def test_live_mage_hand_keeps_source_and_physical_cargo_contact(admission, defect):
    value, saved = _saved()
    hand = _object(
        saved,
        "spectral-hand",
        (0, 10, 0),
        srd_object_force={
            "spell": "mage_hand",
            "source_id": "hand-source",
            "caster": "hero",
        },
    )
    cargo = _object(
        saved,
        "cargo",
        (0, 10, 0),
        srd_spell_carrier={
            "spell": "mage_hand",
            "source_id": "hand-source",
            "force_id": "spectral-hand",
        },
        srd_object_container={"is_open": False, "contents": ["saved-letter"]},
    )
    saved["entities"].extend([hand, cargo])
    value[NAMESPACE + ".state"]["turn_effects"] = [
        {
            "kind": "object_utility_spell",
            "spell": "mage_hand",
            "active": True,
            "source_id": "hand-source",
            "caster": "hero",
            "expires_tick": 23,
            "force_id": "spectral-hand",
            "cargo_id": "cargo",
        }
    ]
    requested = _new_arena(saved)
    if defect == "cargo_missing":
        requested["entities"].pop()
    elif defect == "caster_missing":
        saved["entities"][-2]["metadata"]["srd_object_force"]["caster"] = "missing"
    elif defect == "separated":
        requested["entities"][-1]["position"][0] += 5
    elif defect == "range":
        requested["entities"][-2]["position"] = [90, 10, 0]
    result, bridge = check(admission, ROOTS[0], value, requested)
    assert result.success is (defect is None), result.error
    if result.success:
        actual = bridge.read_field("state", "spatial_scene")
        hand = next(row for row in actual["entities"] if row["id"] == "spectral-hand")
        cargo = next(row for row in actual["entities"] if row["id"] == "cargo")
        assert hand["metadata"]["srd_object_force"]["source_id"] == "hand-source"
        assert cargo["metadata"]["srd_spell_carrier"]["force_id"] == hand["id"]
        assert cargo["metadata"]["srd_object_container"] == {
            "is_open": False,
            "contents": ["saved-letter"],
        }


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


@pytest.mark.parametrize("holder", [None, "other"])
def test_managed_distant_objects_remain_saved_without_entering_new_arena(
    admission_with_objects, holder
):
    value, saved = _saved()
    body = _object(
        saved, srd_spell_object={"hp": 7, "magic": False}, srd_object_breaks=[], carried_by=holder
    )
    saved["entities"].append(body)
    _manage(value, body)
    value[NAMESPACE + ".state"]["turn_effects"] = [
        {
            "kind": "illumination_spell",
            "spell": "light",
            "source": "lantern",
            "caster": "hero",
            "origin": [0, 10, 0],
            "expires_tick": 603,
        }
    ]
    body["metadata"]["srd_spell_object"]["hp"] = 3
    requested = _new_arena(saved)
    requested["entities"] = requested["entities"][:2]
    result, bridge = check(admission_with_objects, ROOTS[0], value, requested)
    assert result.success, result.error
    assert all(
        row["id"] != "lantern" for row in bridge.read_field("state", "spatial_scene")["entities"]
    )
    retained = bridge.read_field("state", "lite_exploration_objects")["lantern"]["body"]
    assert retained["map_id"] == saved["map"]["id"]
    assert retained["metadata"]["srd_spell_object"]["hp"] == 3
    assert bridge.read_field("state", "turn_effects") == value[NAMESPACE + ".state"]["turn_effects"]


def test_managed_carried_object_must_accompany_its_present_holder(admission_with_objects):
    value, saved = _saved()
    body = _object(saved, carried_by="hero", srd_object_breaks=[])
    saved["entities"].append(body)
    _manage(value, body)
    requested = _new_arena(saved)
    requested["entities"].pop()
    result, _ = check(admission_with_objects, ROOTS[0], value, requested)
    assert not result.success


def test_retired_managed_mage_hand_does_not_require_its_stale_active_body(admission_with_objects):
    value, saved = _saved()
    force = _object(
        saved,
        "retired-hand",
        srd_object_force={"spell": "mage_hand", "source_id": "hand-source", "caster": "hero"},
    )
    saved["entities"].append(force)
    _manage(value, force)
    record = value[NAMESPACE + ".state"]["lite_exploration_objects"]["retired-hand"]
    record["pending_scene_reconciliation"] = True
    record["pending_frame_patch"] = {"active": False}
    requested = _new_arena(saved)
    requested["entities"].pop()
    result, bridge = check(admission_with_objects, ROOTS[0], value, requested)
    assert result.success, result.error
    assert all(
        row["id"] != "retired-hand"
        for row in bridge.read_field("state", "spatial_scene")["entities"]
    )
    retained = bridge.read_field("state", "lite_exploration_objects")["retired-hand"]["body"]
    assert retained["active"] is False
    assert retained["metadata"]["srd_object_force"]["source_id"] == "hand-source"


def test_pending_exploration_changes_reconcile_with_latest_scene_hp(admission_with_objects):
    value, saved = _saved()
    body = _object(
        saved,
        carried_by="hero",
        srd_spell_object={"hp": 7, "magic": True},
        srd_object_breaks=[
            {
                "id": "crack",
                "min": [0, 10, 0],
                "max": [0.125, 10.125, 0.125],
            }
        ],
        opaque_cover=True,
    )
    saved["entities"].append(body)
    _manage(value, body, {"srd_object_breaks": [], "opaque_cover": False})
    body["metadata"]["srd_spell_object"]["hp"] = 3
    requested = _new_arena(saved)
    requested["entities"][-1]["metadata"]["srd_spell_object"]["hp"] = 999
    result, bridge = check(admission_with_objects, ROOTS[0], value, requested)
    assert result.success, result.error
    actual = next(
        row
        for row in bridge.read_field("state", "spatial_scene")["entities"]
        if row["id"] == "lantern"
    )
    assert actual["metadata"]["srd_spell_object"] == {"hp": 3, "magic": True}
    assert actual["metadata"]["srd_object_breaks"] == []
    assert actual["metadata"]["opaque_cover"] is False
    assert actual["position"] == [20.125, 10, 0]
