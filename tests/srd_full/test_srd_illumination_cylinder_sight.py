"""Admitted cylinder surfaces preserve sight while genuine interior crossings block it."""

import hashlib
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_illumination import _point_sight_tool

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_action_budget as budget
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_illumination_point_sight import _zone
from tests.srd_full.test_srd_spell_result_cards import _spell_case
from tests.srd_full.test_srd_spell_wave3_information import _entity, _scene, _wall

PARENT = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-illumination-cylinder-sight/input-1"))


@lru_cache
def _runtime(legacy=False):
    payload = (PARENT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_bytes()
    assert hashlib.sha256(payload).hexdigest() == (
        "64bc7b418f7918e7e7bf4e8bb46079b48fd19e72fbda5199ed6453fa9c011305"
    )
    runtime = yaml.load(payload, Loader=yaml.CSafeLoader)
    # Change only the exact point owner; the actual interval and neutral scene are retained.
    if not legacy:
        for name, subject in (
            ("resolve_srd_illumination_point_sight", False),
            ("resolve_srd_illumination_target_sight", True),
        ):
            tool = runtime["tools"][name]
            authored = _point_sight_tool(projected=True, environment=True, subject=subject)
            old_loop = next(
                e
                for e in tool["effects"]
                if e.get("for_each", {}).get("as") == "_illumination_point_barrier"
            )
            new_loop = next(
                e
                for e in authored["effects"]
                if e.get("for_each", {}).get("as") == "_illumination_point_barrier"
            )
            tool["effects"][tool["effects"].index(old_loop)] = deepcopy(new_loop)
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@lru_cache
def _mechanic(name, legacy=False):
    runtime, payload = _runtime(legacy)
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=payload,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _actual_owner(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)


def _case(start, *, kind="cylinder", active=True, plane="material"):
    state, _, _ = _spell_case("light")
    floor = {
        "id": "floor",
        "map_id": "information",
        "active": active,
        "volume": {"kind": "cylinder", "center": [0, 0, -1], "radius": 30, "height": 1},
        "movement": "blocking",
        "visibility": "opaque",
        "metadata": {"plane": plane},
        "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
    }
    if kind == "box":
        floor["volume"] = {"kind": "box", "min": [-30, -30, -1], "max": [30, 30, 0]}
    state[f"{SRD}.state"] = {
        "lasting_tick": 0,
        "turn_effects": [],
        "spatial_scene": _scene(
            _entity("hero", list(start), participant_ref="hero"),
            _entity("cargo", [3, 0, 0], is_object=True),
            geometry=[floor],
            zones=[_zone()],
        ),
    }
    return state


def _point(state, point):
    return _run(
        "resolve_srd_illumination_point_sight",
        state,
        observer_state="hero",
        point=list(point),
    ).result["context"]


@pytest.mark.parametrize("kind", ["box", "cylinder"])
@pytest.mark.parametrize("height,visible", [(0, True), (-0.5, False), (-1, True)])
def test_actual_ground_cap_and_interior_boundary(kind, height, visible):
    state = _case([0, 0, height], kind=kind)
    before = deepcopy(state)
    result = _point(state, [3, 0, height])
    assert result["_illumination_point_can_see"] is visible
    assert result["_illumination_point_total_cover"] is not visible
    assert state == before


@pytest.mark.parametrize("x,visible", [(30, True), (29.99, False), (30.01, True)])
def test_actual_cylinder_side_tangent_versus_interior_chord(x, visible):
    state = _case([x, -5, -0.5])
    assert _point(state, [x, 5, -0.5])["_illumination_point_can_see"] is visible


def test_real_vertical_crossing_blocks_while_grazing_endpoint_stays_clear():
    state = _case([0, 0, 1])
    assert _point(state, [0, 0, -2])["_illumination_point_can_see"] is False
    assert _point(state, [0, 0, 0])["_illumination_point_can_see"] is True


@pytest.mark.parametrize("active,plane", [(False, "material"), (True, "ethereal")])
def test_inactive_or_other_plane_actual_cylinder_never_blocks(active, plane):
    state = _case([0, 0, -0.5], active=active, plane=plane)
    assert _point(state, [3, 0, -0.5])["_illumination_point_can_see"] is True


def test_real_opaque_wall_remains_blocking_on_clear_cylinder_floor():
    state = _case([0, 0, 0])
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(_wall(1))
    assert _point(state, [20, 0, 0])["_illumination_point_can_see"] is False


@pytest.mark.parametrize("height,visible", [(0, True), (-0.5, False)])
def test_actual_saved_target_sight_consumes_same_cylinder_interior_owner(height, visible):
    state = _case([0, 0, height])
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity("guard", [3, 0, height], participant_ref="guard")
    )
    result = _run(
        "resolve_srd_illumination_target_sight",
        state,
        observer_state="hero",
        target_state="guard",
    ).result["context"]
    assert result["_illumination_target_can_see"] is visible


@pytest.mark.parametrize("height", [0, -0.5])
def test_box_operation_count_stays_exact_with_admitted_cylinder_support(monkeypatch, height):
    original = ExecutionBudget.consume_operation
    count = 0

    def consume(budget):
        nonlocal count
        count += 1
        original(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", consume)
    counts = []
    for legacy in (True, False):
        monkeypatch.setattr(budget, "_mechanic", lambda name, old=legacy: _mechanic(name, old))
        count = 0
        assert _point(_case([0, 0, height], kind="box"), [3, 0, height])[
            "_illumination_point_can_see"
        ] is (height == 0)
        counts.append(count)
    print(f"Box point sight operations {counts[0]} -> {counts[1]}")
    assert counts[0] == counts[1] and ExecutionBudget().max_operations == 10000
