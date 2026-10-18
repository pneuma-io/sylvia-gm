"""Selected real DSL display invariants; no full package build or saved campaign."""

import hashlib
import json
import os
from copy import deepcopy
from pathlib import Path

import pytest
from scripts import srd_lite_pending_area_preview as preview
from scripts.srd_timed_actor_defenses import _call, _parameter, _set, _tool

from sylvia_gm.application import tactical_grid as tactical_grid_owner
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.application.tactical_grid import project_coordinate_map
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ExecutionBudget
from sylvia_gm.spatial import SpatialEngine

NS = "test.pending-area-preview"


def _scene():
    return {
        "format": 1,
        "map": {
            "id": "arena",
            "revision": 1,
            "bounds": {"min": [-100, -100, -100], "max": [100, 100, 100]},
            "grid": {"cell_size": 5, "unit": "ft", "origin": [-100, -100]},
            "layers": [{"id": "floor", "min_elevation": -100, "max_elevation": 100}],
        },
        "geometry": [],
        "terrain": [],
        "entities": [
            {
                "id": "hidden-enemy-body",
                "map_id": "arena",
                "position": [10, 0, 0],
                "footprint": {"kind": "circle", "radius": 0.5},
                "height": 6,
                "facing": [1, 0],
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": {"participant_ref": "hidden-enemy", "plane": "material"},
            }
        ],
        "zones": [
            {
                "id": "private-zone",
                "map_id": "arena",
                "shape": {"kind": "box", "min": [40, 40, 0], "max": [45, 45, 5]},
                "metadata": {"private": "Secret hazard"},
            }
        ],
    }


def _state(shape, *, known=True, status="open"):
    shapes = shape if isinstance(shape, list) else [shape]
    return {
        "spatial_scene": _scene(),
        "pending_srd_action_stack": ["action-7"],
        "pending_srd_actions": {
            "action-7": {
                "kind": "spell_cast",
                "status": status,
                "spell": "fireball",
                "actor_state_id": "hidden-caster",
                "parameters": {"targets": ["hidden-enemy"]},
                "area_preview": {
                    "known_area": known,
                    "map_id": "arena",
                    "basis_scene_revision": 1,
                    "label": "Selected spell",
                    "shapes": deepcopy(shapes),
                },
            }
        },
    }


def _formatter():
    return _tool(
        "Format public display.",
        [_parameter("message")],
        [_set("_info_projection_message", "message")],
    )


def _execute(tools, state, name=preview.REFRESH, parameters=None, *, combat=True, definitions=None):
    runtime = RulesetRuntime(ruleset_dict={"tools": tools, "definitions": definitions or {}})
    bridge = AuthorityMemoryStateBridge(
        NS,
        {
            NS + ".state": deepcopy(state),
            NS + ".combat": {"encounter_active": combat},
            NS + ".entities": {},
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
        name,
        parameters or {},
        runtime.tools,
        bridge,
        runtime.ruleset_config,
        runtime.definitions,
        combat_state=bridge.combat_state,
        tool_executor=nested,
        budget=ExecutionBudget(),
    )
    return result, bridge.get_entity("state")


def _refresh(state, *, combat=True):
    result, updated = _execute(
        {
            preview.REFRESH: preview._refresh_tool(),
            "format_srd_information_projection": _formatter(),
        },
        state,
        combat=combat,
    )
    assert result.success, result.error
    public = thaw_json_value(
        project_coordinate_map({"scene": updated["spatial_scene"], "enabled": True})
    )
    return updated, public


ROUND = {"kind": "sphere", "center": [10, 10, 0], "radius": 5}


@pytest.mark.parametrize(
    ("shape", "expected"),
    [
        (ROUND, {"kind": "cylinder", "center": [10, 10, -5], "radius": 5, "height": 10}),
        (
            {"kind": "cylinder", "center": [10, 10, 0], "radius": 10, "height": 40},
            {"kind": "cylinder", "center": [10, 10, 0], "radius": 10, "height": 40},
        ),
        (
            {"kind": "box", "min": [0, 0, 0], "max": [10, 10, 10]},
            {"kind": "box", "min": [0, 0, 0], "max": [10, 10, 10]},
        ),
    ],
)
def test_known_pending_area_is_public_inert_and_does_not_resolve_spell(shape, expected):
    state = _state(shape)
    original = deepcopy(state)
    updated, public = _refresh(state)
    assert public["entities"] == []
    assert len(public["zones"]) == 1
    assert public["zones"][0]["shape"] == expected
    display = public["zones"][0]["display"]
    assert "pending preview" in display["label"]
    assert display["state"] == "Pending; spell has not resolved"
    assert display["interactive"] is False
    for private in ("hidden-caster", "hidden-enemy", "Secret hazard"):
        assert private not in str(public)
    assert updated["spatial_scene"]["entities"] == original["spatial_scene"]["entities"]
    assert updated["spatial_scene"]["zones"][0] == original["spatial_scene"]["zones"][0]
    pending = updated["pending_srd_actions"]["action-7"]
    assert {key: value for key, value in pending.items() if key != "area_preview"} == {
        key: value
        for key, value in original["pending_srd_actions"]["action-7"].items()
        if key != "area_preview"
    }
    assert (
        pending["area_preview"]["shapes"]
        == original["pending_srd_actions"]["action-7"]["area_preview"]["shapes"]
    )
    metadata = updated["spatial_scene"]["zones"][1]["metadata"]
    assert set(metadata) == {preview.TAG, "overlay_pending_id", "player"}
    assert (
        not {"hazard", "movement", "source_environment_id", "source_spell", "plane"}
        & metadata.keys()
    )
    assert _refresh(updated)[0] == updated


@pytest.mark.parametrize("status", ["completed", "lost", "cancelled"])
def test_terminal_or_cancelled_window_removes_only_its_overlay(status):
    active, _ = _refresh(_state(ROUND))
    active["pending_srd_actions"]["action-7"]["status"] = status
    updated, public = _refresh(active)
    assert public["zones"] == []
    assert updated["spatial_scene"]["zones"] == [_scene()["zones"][0]]
    assert updated["pending_srd_actions"]["action-7"]["status"] == status


@pytest.mark.parametrize("change", ["unknown", "missing", "map", "revision", "exploration"])
def test_hidden_absent_foreign_stale_or_exploration_area_is_not_projected(change):
    active, _ = _refresh(_state(ROUND))
    capsule = active["pending_srd_actions"]["action-7"]["area_preview"]
    combat = True
    if change == "unknown":
        capsule["known_area"] = False
    elif change == "missing":
        active["pending_srd_actions"] = {}
    elif change == "map":
        capsule["map_id"] = "former-arena"
    elif change == "revision":
        active["spatial_scene"]["map"]["revision"] += 1
    else:
        combat = False
    updated, public = _refresh(active, combat=combat)
    assert public["zones"] == []
    assert updated["spatial_scene"]["zones"] == [_scene()["zones"][0]]


@pytest.mark.parametrize(
    ("shape", "minimum", "maximum"),
    [
        (
            {
                "kind": "cone",
                "apex": [0.5, 0, 3],
                "direction": [1, 0, 0],
                "length": 30,
                "radius": 15,
            },
            [0.5, -15, -12],
            [30.5, 15, 18],
        ),
        (
            {"kind": "cone", "apex": [0, 0, 6], "direction": [0, 0, 1], "length": 30, "radius": 15},
            [-15, -15, 6],
            [15, 15, 36],
        ),
        (
            {
                "kind": "prism",
                "origin": [0.5, 0, 3],
                "direction": [1, 0, 0],
                "up": [0, 0, 2],
                "length": 100,
                "width": 5,
                "height": 7,
            },
            [0.5, -2.5, -0.5],
            [100, 2.5, 6.5],
        ),
    ],
)
def test_cone_and_line_keep_inert_envelope_and_exact_public_display(shape, minimum, maximum):
    _, public = _refresh(_state(shape))
    zone = public["zones"][0]
    assert zone["shape"] == {"kind": "box", "min": minimum, "max": maximum}
    assert "extent only" not in zone["display"]["label"]
    assert "Exact XY footprint" in zone["display"]["height_label"]
    actual = zone["display"]["display_geometry"]
    expected = deepcopy(shape)
    if expected["kind"] == "prism":
        expected["up"] = [0.0, 0.0, 1.0]
    assert actual == expected


def test_edge_sphere_is_labelled_clipped_extent_without_rejecting_pending_cast():
    state = _state({"kind": "sphere", "center": [98, 10, 0], "radius": 5})
    _, public = _refresh(state)
    zone = public["zones"][0]
    assert zone["shape"] == {"kind": "box", "min": [93, 5, -5], "max": [100, 15, 5]}
    assert "extent only" in zone["display"]["label"]


@pytest.mark.parametrize(
    "shape",
    [
        {"kind": "cone", "apex": [0, 0, 100], "direction": [0, 0, 1], "length": 30, "radius": 15},
        {
            "kind": "cone",
            "apex": [0, 0, 100],
            "direction": [0.6, 0, 0.8],
            "length": 30,
            "radius": 15,
        },
        {
            "kind": "prism",
            "origin": [0, 0, -100],
            "direction": [0, 0, -1],
            "up": [1, 0, 0],
            "length": 30,
            "width": 5,
            "height": 7,
        },
    ],
)
def test_exact_xy_survives_empty_vertical_intersection_without_changing_native_area(shape):
    original = _state(shape)
    query = {
        "kind": "area",
        "map_id": "arena",
        "shape": shape,
        "include": ["entities"],
        "max_results": 4096,
    }
    before = SpatialEngine(original["spatial_scene"]).query(query).as_payload()["results"]
    updated, public = _refresh(original)
    zone = public["zones"][0]
    assert zone["shape"]["min"][2] == -100
    assert zone["shape"]["max"][2] == 100
    assert zone["display"]["display_geometry"] == shape
    assert updated["pending_srd_actions"]["action-7"]["area_preview"]["shapes"] == [shape]
    after = SpatialEngine(updated["spatial_scene"]).query(query).as_payload()["results"]
    assert after == before
    assert updated["spatial_scene"]["entities"] == original["spatial_scene"]["entities"]
    metadata = updated["spatial_scene"]["zones"][-1]["metadata"]
    assert (
        not {"hazard", "movement", "source_environment_id", "source_spell", "plane"}
        & metadata.keys()
    )


def test_exact_direction_change_with_same_box_refreshes_only_owned_public_geometry():
    first = {
        "kind": "prism",
        "origin": [-30, -40, 0],
        "direction": [0.6, 0.8, 0],
        "up": [0, 0, 1],
        "length": 100,
        "width": 5,
        "height": 6,
    }
    second = {**first, "origin": [-30, 40, 0], "direction": [0.6, -0.8, 0]}
    active, first_public = _refresh(_state(first))
    capsule = active["pending_srd_actions"]["action-7"]["area_preview"]
    capsule["shapes"] = [deepcopy(second)]
    revision = active["spatial_scene"]["map"]["revision"]
    updated, second_public = _refresh(active)
    assert first_public["zones"][0]["shape"] == second_public["zones"][0]["shape"]
    assert (
        first_public["zones"][0]["display"]["display_geometry"]
        != second_public["zones"][0]["display"]["display_geometry"]
    )
    actual = second_public["zones"][0]["display"]["display_geometry"]
    assert actual["direction"] == pytest.approx(second["direction"])
    assert {key: value for key, value in actual.items() if key != "direction"} == {
        key: value for key, value in second.items() if key != "direction"
    }
    assert updated["spatial_scene"]["map"]["revision"] == revision + 1
    assert updated["spatial_scene"]["zones"][0] == _scene()["zones"][0]
    assert _refresh(updated)[0] == updated


def test_zero_xy_intersection_keeps_honest_absence_and_genuine_query():
    shape = {
        "kind": "cone",
        "apex": [100, 0, 0],
        "direction": [1, 0, 0],
        "length": 30,
        "radius": 15,
    }
    state = _state(shape)
    result = SpatialEngine(state["spatial_scene"]).query(
        {
            "kind": "area",
            "map_id": "arena",
            "shape": shape,
            "include": ["entities"],
            "max_results": 4096,
        }
    )
    assert result.kind == "area"
    updated, public = _refresh(state)
    assert public["zones"] == []
    assert updated == state


def test_exact_preview_generated_effects_keep_bounded_local_dsl():
    effects = preview._refresh_tool()["effects"]

    def walk(value):
        if isinstance(value, dict):
            if "condition" in value:
                assert len(value["condition"]) <= 500
            if "for_each" in value:
                collection = value["for_each"]["collection"]
                assert (
                    collection.startswith("_") and "." not in collection and "[" not in collection
                )
            if "set" in value:
                field = value["set"]["field"]
                assert field.startswith(("_", "state.", "lasting_state."))
            for item in value.values():
                walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)
        elif isinstance(value, str) and value.startswith(
            ("_", "state.", "lasting_state.", "combat.")
        ):
            assert len(value) <= 500
        elif isinstance(value, str) and value.startswith(("'", '"')):
            assert len(value) <= 202

    walk(effects)


@pytest.mark.skipif(
    not os.environ.get("SRD_LITE_EXACT_PREVIEW_RENDER_INPUTS"),
    reason="Selected actual DSL-to-frontend artifact export only",
)
def test_export_actual_dsl_exact_preview_for_current_frontend():
    common_line = {"kind": "prism", "up": [0, 0, 1], "length": 100, "width": 5, "height": 6}
    cases = [
        (
            "horizontal-cone",
            {
                "kind": "cone",
                "apex": [-20, 0, 3],
                "direction": [1, 0, 0],
                "length": 30,
                "radius": 15,
            },
            [[0, 0], [8, 12]],
            [[-18, 10], [8, 14.8]],
        ),
        (
            "pitched-boundary-cone",
            {
                "kind": "cone",
                "apex": [0, 0, 100],
                "direction": [0.6, 0, 0.8],
                "length": 30,
                "radius": 15,
            },
            [[1, 0], [18, 14], [29, 0]],
            [[2, 10], [29, 10]],
        ),
        (
            "vertical-boundary-cone",
            {
                "kind": "cone",
                "apex": [0, 0, 100],
                "direction": [0, 0, 1],
                "length": 30,
                "radius": 15,
            },
            [[0, 0], [14, 0]],
            [[14, 14], [16, 0]],
        ),
        (
            "clipped-cone",
            {
                "kind": "cone",
                "apex": [90, 0, 3],
                "direction": [1, 0, 0],
                "length": 30,
                "radius": 15,
            },
            [[99, 0]],
            [[99, 10]],
        ),
        (
            "diagonal-line",
            {**common_line, "origin": [-30, -40, 3], "direction": [0.6, 0.8, 0]},
            [[0, 0], [18, 24]],
            [[0, 10], [18, -24]],
        ),
        (
            "same-box-other-line",
            {**common_line, "origin": [-30, 40, 3], "direction": [0.6, -0.8, 0]},
            [[0, 0], [18, -24]],
            [[0, 10], [18, 24]],
        ),
        (
            "pitched-line",
            {
                "kind": "prism",
                "origin": [-12, -9, 20],
                "direction": [0.48, 0.36, 0.8],
                "up": [0, 0, 1],
                "length": 50,
                "width": 5,
                "height": 10,
            },
            [[0, 0], [-14.4, -10.8]],
            [[0, 10]],
        ),
    ]
    rows = []
    for name, shape, inside, outside in cases:
        original = _state(shape)
        qualification = _tool(
            "Qualify the selected actual display geometry before capture.",
            [],
            [
                {
                    "spatial_query": {
                        "scene": "state.spatial_scene",
                        "operation": "area",
                        "query": {
                            "map_id": "{state.spatial_scene.map.id}",
                            "shape": deepcopy(shape),
                            "include": ["entities"],
                            "max_results": 4096,
                        },
                    },
                    "store_as": "_actual_area",
                }
            ],
        )
        preview._capture_query(qualification, "_actual_area")
        result, unchanged = _execute({"qualify_actual": qualification}, original, "qualify_actual")
        assert result.success, result.error
        assert unchanged == original
        captured = result.context[preview.SHAPES]
        assert captured == [shape]
        original["pending_srd_actions"]["action-7"]["area_preview"]["shapes"] = deepcopy(captured)
        updated, public = _refresh(original)
        assert updated["pending_srd_actions"]["action-7"]["area_preview"]["shapes"] == [shape]
        assert len(public["zones"]) == 1
        assert public["entities"] == []
        for private in ("hidden-caster", "hidden-enemy", "Secret hazard"):
            assert private not in str(public)
        public_shape = public["zones"][0]["display"]["display_geometry"]
        assert public_shape["kind"] == shape["kind"]
        cleaned = deepcopy(updated)
        cleaned["pending_srd_actions"]["action-7"]["status"] = "completed"
        _, completed = _refresh(cleaned)
        assert completed["zones"] == []
        rows.append(
            {
                "name": name,
                "source_query_geometry": shape,
                "pending": public,
                "completed": completed,
                "xy_inside": inside,
                "xy_outside": outside,
            }
        )
    assert rows[4]["pending"]["zones"][0]["shape"] == rows[5]["pending"]["zones"][0]["shape"]
    payload = {
        "actual_dsl_refresh_and_public_coordinate_projection": True,
        "actual_dsl_qualified_query_capture": True,
        "actual_paid_cast_or_compiled_app_transaction": False,
        "private_recipients_projected": False,
        "cases": rows,
        "binding": {
            "package_module_sha256": hashlib.sha256(
                Path(preview.__file__).read_bytes()
            ).hexdigest(),
            "core_module_sha256": hashlib.sha256(
                Path(tactical_grid_owner.__file__).read_bytes()
            ).hexdigest(),
        },
    }
    destination = Path(os.environ["SRD_LITE_EXACT_PREVIEW_RENDER_INPUTS"])
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def test_adjacent_fire_storm_cubes_use_exact_actual_boxes_without_merging_or_target_markers():
    boxes = [{"kind": "box", "min": [x, 0, 0], "max": [x + 10, 10, 10]} for x in (5, 15)]
    _, public = _refresh(_state(boxes))
    assert [row["shape"] for row in public["zones"]] == boxes
    assert len({row["id"] for row in public["zones"]}) == 2
    assert public["entities"] == []


@pytest.mark.parametrize(
    "shape",
    [
        {"kind": "sphere", "center": "{origin}", "radius": "{radius}"},
        {"kind": "cone", "apex": "{origin}", "direction": [1, 0, 0], "length": 30, "radius": 15},
        {
            "kind": "prism",
            "origin": "{origin}",
            "direction": [1, 0, 0],
            "up": [0, 0, 1],
            "length": 30,
            "width": 5,
            "height": 7,
        },
        {"kind": "box", "min": [10, 0, 0], "max": [20, 10, 10]},
    ],
)
def test_capture_copies_native_qualified_shape_without_private_query_recipients(shape):
    tool = _tool(
        "Query an actual area.",
        [
            _parameter("origin", "array", items={"type": "number"}),
            _parameter("radius", "number"),
        ],
        [
            {
                "spatial_query": {
                    "scene": "state.spatial_scene",
                    "operation": "area",
                    "query": {
                        "map_id": "{state.spatial_scene.map.id}",
                        "shape": shape,
                        "include": ["entities"],
                        "max_results": 4096,
                    },
                },
                "store_as": "_actual_area",
            }
        ],
    )
    preview._capture_query(tool, "_actual_area")
    result, updated = _execute(
        {"query_actual": tool}, _state(ROUND), "query_actual", {"origin": [10, 0, 3], "radius": 5}
    )
    assert result.success, result.error
    expected = {
        key: [10, 0, 3] if value == "{origin}" else 5 if value == "{radius}" else value
        for key, value in shape.items()
    }
    assert result.context[preview.SHAPES] == [expected]
    assert "hidden-enemy" not in str(result.context[preview.SHAPES])
    assert updated == _state(ROUND)


def test_capture_failing_native_admission_does_not_create_preview_or_change_state():
    shape = {
        "kind": "cone",
        "apex": "{origin}",
        "direction": [0, 0, 0],
        "length": 15,
        "radius": 7.5,
    }
    tool = _tool(
        "Reject invalid actual geometry.",
        [_parameter("origin", "array")],
        [
            {
                "spatial_query": {
                    "scene": "state.spatial_scene",
                    "operation": "area",
                    "query": {
                        "map_id": "{state.spatial_scene.map.id}",
                        "shape": shape,
                        "include": ["entities"],
                        "max_results": 4096,
                    },
                },
                "store_as": "_actual_area",
            },
        ],
    )
    preview._capture_query(tool, "_actual_area")
    original = _state(ROUND)
    result, updated = _execute(
        {"query_actual": tool}, original, "query_actual", {"origin": [0, 0, 3]}
    )
    assert not result.success
    assert updated == original


def test_forwarding_keeps_renamed_and_list_exports():
    for prior in ({"renamed_target": "_actual_target"}, ["_actual_target"]):
        row = {"call": "owner", "params": {"origin": "{origin}"}, "export_context": deepcopy(prior)}
        preview._export(row)
        assert row["params"] == {"origin": "{origin}"}
        if isinstance(prior, dict):
            assert row["export_context"] == {
                **prior,
                **{name: name for name in (preview.SHAPES, preview.REVISION, preview.MAP)},
            }
        else:
            assert row["export_context"] == [*prior, preview.SHAPES, preview.REVISION, preview.MAP]


@pytest.mark.skipif(
    not os.environ.get("SRD_LITE_PREVIEW_989_OWNERS"),
    reason="Needs bounded admitted-owner fragments",
)
def test_late_binder_keeps_actual_989_profiles_and_existing_call_params():
    owners = json.loads(Path(os.environ["SRD_LITE_PREVIEW_989_OWNERS"]).read_text(encoding="utf-8"))
    # Package materialization pools public descriptors; this seam check needs only known_area.
    for tool in owners.values():
        tool["parameters"] = [row for row in tool["parameters"] if isinstance(row, dict)]
    owners["begin_srd_spell_action"]["parameters"].append({"name": "known_area", "type": "boolean"})
    definitions = {
        "spells": {name: {"unchanged": name} for name in preview.LABELLED_SPELLS},
        "spell_casting": {"unchanged": "headers"},
    }
    runtime = {"tools": deepcopy(owners), "definitions": deepcopy(definitions)}
    prior_calls = {
        name: [
            (row["call"], deepcopy(row.get("params", {})))
            for rows in preview._lists(owner["effects"])
            for row in rows
            if "call" in row
        ]
        for name, owner in owners.items()
    }
    changed = preview.bind_pending_area_preview(runtime)
    assert preview.REFRESH in changed
    assert runtime["definitions"] == definitions
    for name, old in prior_calls.items():
        new = [
            (row["call"], row.get("params", {}))
            for rows in preview._lists(runtime["tools"][name]["effects"])
            for row in rows
            if "call" in row and row["call"] != preview.REFRESH
        ]
        assert new == old
    begin = runtime["tools"]["begin_srd_spell_action"]
    assert begin["effects"][-1] == _call(preview.REFRESH, {})
    assert runtime["tools"]["complete_srd_pending_action"]["effects"][-1] == _call(
        preview.REFRESH, {}
    )
