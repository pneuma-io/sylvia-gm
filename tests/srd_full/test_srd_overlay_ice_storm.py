"""Use the actual Ice Storm qualifier and native area query with inert map zones."""

from copy import deepcopy

import pytest
from scripts.srd_full import srd_retained_ice_storm_geometry_v3 as ice
from scripts.srd_full.srd_information_spells import _barrier_tools
from scripts.srd_full.srd_scene_relations import _scene_tools

from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget

NS = "test.overlay-ice-storm"
RETAINED = "srd_lite_retained_overlay"
PENDING = "srd_lite_pending_area_overlay"
ORIGIN = [0, 0, 2]
GROUND_VOLUME = {"kind": "box", "min": [-10, -10, 2], "max": [10, 10, 3]}


def _zone(zone_id, metadata):
    return {
        "id": zone_id,
        "map_id": "arena",
        "shape": deepcopy(GROUND_VOLUME),
        "metadata": deepcopy(metadata),
    }


def _scene(zones):
    return {
        "format": 1,
        "map": {
            "id": "arena",
            "revision": 1,
            "bounds": {"min": [-100, -100, -10], "max": [100, 100, 100]},
            "grid": {"cell_size": 5, "unit": "ft", "origin": [-100, -100]},
            "layers": [{"id": "floor", "min_elevation": -10, "max_elevation": 100}],
        },
        "entities": [
            {
                "id": "hero-body",
                "map_id": "arena",
                "position": [-30, 0, 2],
                "footprint": {"kind": "circle", "radius": 0.5},
                "height": 6,
                "facing": [1, 0],
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": {"participant_ref": "hero", "plane": "material"},
            }
        ],
        "geometry": [],
        "terrain": [],
        "zones": deepcopy(zones),
    }


def _physical_loop(owner):
    return next(
        row["for_each"]
        for rows in ice._lists(owner["effects"])
        for row in rows
        if row.get("for_each", {}).get("collection") == "_retained_ice_results"
    )


def _execute(scene, *, unpatched=False, retain_ground=True):
    qualifier = ice._qualifier()
    if unpatched:
        loop = _physical_loop(qualifier)
        # Recover precisely the pre-fix owning body; no replacement native tools.
        loop["do"] = loop["do"][0]["on_true"]
    tools = {
        ice.QUALIFY: qualifier,
        **_scene_tools(),
        **_barrier_tools(),
    }
    runtime = RulesetRuntime(ruleset_dict={"tools": tools})
    state = {"spatial_scene": deepcopy(scene), "turn_effects": [], "lasting_tick": 4}
    actor = {"is_alive": True, "spell_slots": {"4": 1}, "action_used": False}
    combat = {"encounter_active": True}
    bridge = AuthorityMemoryStateBridge(
        NS,
        {
            NS + ".state": deepcopy(state),
            NS + ".combat": deepcopy(combat),
            NS + ".entities": {"hero": deepcopy(actor)},
        },
    )

    def nested(child, values, parent):
        return execute_tool(
            child,
            values,
            runtime.tools,
            bridge,
            runtime.ruleset_config,
            runtime.definitions,
            combat_state=parent.combat_state,
            tool_executor=nested,
            budget=parent.budget,
        )

    result = execute_tool(
        ice.QUALIFY,
        {"caster_state": "hero", "origin": ORIGIN, "retain_ground": retain_ground},
        runtime.tools,
        bridge,
        runtime.ruleset_config,
        runtime.definitions,
        combat_state=bridge.combat_state,
        tool_executor=nested,
        budget=ExecutionBudget(),
    )
    assert bridge.get_entity("state") == state
    assert bridge.get_entity("hero") == actor
    assert bridge.combat_state == combat
    return result


def _ground():
    return _zone("floor-zone", {"ground": True, "plane": "material"})


@pytest.mark.parametrize("marker", [RETAINED, PENDING])
def test_actual_native_area_hit_skips_inert_overlay_before_plane_requirement(marker):
    overlay = _zone(
        "overlay", {marker: True, "player": {"label": "Spell area", "interactive": False}}
    )
    scene = _scene([_ground(), overlay])
    regression = _execute(scene, unpatched=True)
    assert not regression.success
    assert "Intersecting Ice Storm scene facts need their actual plane" in regression.error

    result = _execute(scene)
    assert result.success, result.error
    assert any(row["id"] == "overlay" for row in result.context["_retained_ice_area"]["results"])
    assert result.context["_retained_ice_targets"] == []
    assert result.context["_retained_ice_ground_intersections"] == [
        {
            "ground_kind": "zone",
            "ground_id": "floor-zone",
            "ground_volume": GROUND_VOLUME,
            "ice_volume": {"kind": "cylinder", "center": ORIGIN, "radius": 20, "height": 40},
            "plane": "material",
        }
    ]


@pytest.mark.parametrize(
    "metadata",
    [{}, {RETAINED: False}, {PENDING: False}, {"srd_lite_retained_area_overlay": True}],
)
def test_unmarked_false_and_misspelled_zone_markers_keep_real_plane_requirement(metadata):
    result = _execute(_scene([_ground(), _zone("actual-zone", metadata)]))
    assert not result.success
    assert "Intersecting Ice Storm scene facts need their actual plane" in result.error


def test_ground_qualification_and_plane_remain_authoritative_with_both_overlays():
    zones = [
        _ground(),
        _zone("nonground", {"plane": "material", "ground": False}),
        _zone("other-plane", {"plane": "ethereal", "ground": True}),
        _zone("retained", {RETAINED: True}),
        _zone("pending", {PENDING: True}),
    ]
    result = _execute(_scene(zones))
    assert result.success, result.error
    assert [row["ground_id"] for row in result.context["_retained_ice_ground_intersections"]] == [
        "floor-zone"
    ]


@pytest.mark.parametrize("kind", ["entity", "geometry"])
def test_marker_on_physical_entity_or_geometry_does_not_bypass_plane_requirement(kind):
    scene = _scene([_ground()])
    if kind == "entity":
        body = deepcopy(scene["entities"][0])
        body["id"] = "actual-creature"
        body["position"] = ORIGIN
        body["metadata"] = {RETAINED: True, PENDING: True, "participant_ref": "foe"}
        scene["entities"].append(body)
    else:
        scene["geometry"].append(
            {
                "id": "actual-support",
                "map_id": "arena",
                "volume": deepcopy(GROUND_VOLUME),
                "active": True,
                "movement": "passable",
                "visibility": "clear",
                "metadata": {RETAINED: True, PENDING: True},
                "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
            }
        )
    result = _execute(scene)
    assert not result.success
    assert "Intersecting Ice Storm scene facts need their actual plane" in result.error


def test_shared_cylinder_without_retained_ground_still_selects_entities_only():
    scene = _scene([_ground(), _zone("pending", {PENDING: True})])
    result = _execute(scene, retain_ground=False)
    assert result.success, result.error
    assert result.context["_retained_ice_area"]["results"] == []
    assert result.context["_retained_ice_ground_intersections"] == []
