"""Compare the actual package movement-cost owner with and without display zones."""

from copy import deepcopy

import pytest
from scripts import srd_lite_retained_area_map as areas
from scripts.srd_information_spells import _barrier_tools, _plant_tools
from scripts.srd_scene_relations import _scene_tools
from scripts.srd_timed_actor_defenses import _parameter, _set, _tool

from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget


def _execute(name, tools, state, parameters=None):
    parsed = RulesetRuntime(ruleset_dict={"tools": tools})
    bridge = AuthorityMemoryStateBridge("overlay.test", {"overlay.test.state": deepcopy(state)})
    def nested(child, values, parent):
        return execute_tool(
            child, values, parsed.tools, bridge, parsed.ruleset_config, parsed.definitions,
            combat_state=parent.combat_state, tool_executor=nested, budget=parent.budget,
        )

    result = execute_tool(
        name,
        parameters or {},
        parsed.tools,
        bridge,
        parsed.ruleset_config,
        parsed.definitions,
        tool_executor=nested,
        budget=ExecutionBudget(),
    )
    assert result.success, result.error
    return result.context, bridge.get_entity("state")


def test_actual_movement_owner_ignores_cloud_and_web_display_metadata():
    state = {
        "lasting_tick": 0,
        "turn_effects": [],
        "spatial_scene": {
            "format": 1,
            "map": {
                "id": "map",
                "revision": 1,
                "bounds": {"min": [0, 0, 0], "max": [100, 100, 30]},
                "grid": {"cell_size": 5, "unit": "ft", "origin": [0, 0]},
                "layers": [{"id": "world", "min_elevation": 0, "max_elevation": 30}],
            },
            "geometry": [],
            "entities": [],
            "terrain": [],
            "zones": [
                {
                    "id": "real-terrain",
                    "map_id": "map",
                    "shape": {"kind": "box", "min": [20, 20, 0], "max": [25, 40, 10]},
                    "metadata": {"plane": "material", "difficult": True},
                }
            ],
        },
        "persistent_spell_area_ids": ["cloud", "web"],
        "persistent_spell_areas": {},
    }
    common = {
        "profile": "environment",
        "active": True,
        "expires_tick": 20,
        "known_area": True,
        "map_id": "map",
        "origin": [30, 30, 0],
    }
    cell = {
        "volume": {"kind": "box", "min": [25, 25, 0], "max": [30, 30, 5]},
        "source_sequence": 2,
        "status": "burning",
        "burn_end_tick": 2,
    }
    state["persistent_spell_areas"] = {
        "cloud": {
            **common,
            "spell": "cloud_of_daggers",
            "mode": "lite_daggers",
            "sequence": 1,
            "radius": 3.5,
        },
        "web": {
            **common,
            "spell": "web",
            "mode": "web",
            "sequence": 2,
            "web_cell_schema": 1,
            "web_cell_ids": ["cell"],
            "web_cells": {"cell": cell},
        },
    }
    tools = {
        "measure_srd_information_distance": _scene_tools()["measure_srd_information_distance"],
        "srd_information_segment_interval": _barrier_tools()["srd_information_segment_interval"],
        "srd_information_movement_cost": _plant_tools()["srd_information_movement_cost"],
        "srd_plants_route_interval": _plant_tools()["srd_plants_route_interval"],
        areas.REFRESH: areas._refresh_tool(),
        "format_srd_information_projection": _tool(
            "Existing formatter",
            [_parameter("message")],
            [_set("_info_projection_message", "message")],
        ),
    }
    parameters = {
        "start": [15, 27, 0],
        "end": [40, 27, 0],
        "base_difficult": False,
        "plane": "material",
    }
    before, _ = _execute("srd_information_movement_cost", tools, state, parameters)
    _, shown = _execute(areas.REFRESH, tools, state)
    after, _ = _execute("srd_information_movement_cost", tools, shown, parameters)
    assert before["_route_distance"] == after["_route_distance"] == 25
    assert before["_route_cost"] == pytest.approx(30)
    assert after["_route_cost"] == pytest.approx(before["_route_cost"])
    assert shown["persistent_spell_areas"] == state["persistent_spell_areas"]
    assert shown["spatial_scene"]["zones"][0] == state["spatial_scene"]["zones"][0]
    overlays = shown["spatial_scene"]["zones"][1:]
    assert {row["shape"]["kind"] for row in overlays} == {"box", "cylinder"}
    forbidden = {
        "plane",
        "difficult",
        "plants",
        "plant_growth",
        "ground_environment",
        "source_environment_id",
        "source_spell",
        "magical_effect",
    }
    assert all(not forbidden.intersection(row["metadata"]) for row in overlays)
