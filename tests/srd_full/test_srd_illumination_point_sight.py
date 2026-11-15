"""Unoccupied destinations use admitted ambient zones, source light and actual observer sight."""

import hashlib
import importlib.util
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_illumination import _point_sight_tool

from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget, test_srd_spell_result_cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_spell_illumination import _case, _cast
from tests.srd_full.test_srd_spell_wave3_information import _wall

ROOT = Path(__file__).resolve().parents[2]


def _travel_owner():
    path = ROOT.parent / "portals/.scratch/travel-delta-06/files/scripts/srd_spell_travel.py"
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "ab91e8e8cf9d9ae1b709fce2e071c0252248c076c043c523a258bfc7bdea66e8"
    )
    spec = importlib.util.spec_from_file_location("scripts._point_frozen_travel", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@lru_cache
def _runtime():
    runtime = yaml.load(
        (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text(), Loader=yaml.CSafeLoader
    )
    if "resolve_srd_travel_perception" not in runtime["tools"]:
        travel = _travel_owner()
        # Use the exact owner until the combined canonical includes its authored source guard.
        changed = travel._tools() | travel._ethereal_tools() | travel._perception_tool()
        travel._normalize(changed)
        runtime["tools"].update(changed)
    runtime["tools"]["resolve_srd_illumination_point_sight"] = _point_sight_tool(
        projected=True, environment=True
    )
    return runtime, yaml.safe_dump(runtime, sort_keys=False).encode()


@lru_cache
def _mechanic(name):
    runtime, payload = _runtime()
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
def _actual_runtime(monkeypatch):
    runtime, _ = _runtime()
    monkeypatch.setattr(test_srd_action_budget, "_mechanic", _mechanic)
    monkeypatch.setattr(
        test_srd_spell_result_cards, "_spell_definitions", lambda: runtime["definitions"]["spells"]
    )


def _zone(identity="ambient", *, plane="material", lighting="bright", **metadata):
    return {
        "id": identity,
        "map_id": "information",
        "shape": {"kind": "box", "min": [-100, -100, -10], "max": [100, 100, 10]},
        "metadata": {"plane": plane, "lighting": lighting, **metadata},
    }


def _with_ambient(state, lighting="bright", plane="material"):
    state[f"{SRD}.state"]["spatial_scene"]["zones"].append(_zone(lighting=lighting, plane=plane))
    return state


def _point(state, point=(30, 0, 0), **parameters):
    return _run(
        "resolve_srd_illumination_point_sight",
        state,
        observer_state="hero",
        point=list(point),
        **parameters,
    ).result["context"]


@pytest.mark.parametrize("lighting,visible", [("bright", True), ("dim", True), ("darkness", False)])
def test_actual_unoccupied_point_reads_saved_zone_ambient_without_mutating_scene(lighting, visible):
    state, _, _ = _case("light")
    _with_ambient(state, lighting)
    before = deepcopy(state)
    result = _point(state)
    assert result["_illumination_point_lighting"] == lighting
    assert result["_illumination_point_can_see"] is visible
    assert result["_illumination_point_origin"] == [0, 0, 0]
    assert result["_illumination_point_distance"] == 30
    assert state == before


@pytest.mark.parametrize("kind", ["missing", "wrong_plane", "inactive", "conflicting"])
def test_missing_or_conflicting_actual_ambient_fails_without_inventing_a_verdict(kind):
    state, _, _ = _case("light")
    if kind == "wrong_plane":
        _with_ambient(state, plane="ethereal")
    if kind == "inactive":
        state[f"{SRD}.state"]["spatial_scene"]["zones"].append(_zone(active=False))
    if kind == "conflicting":
        _with_ambient(state, "bright")
        state[f"{SRD}.state"]["spatial_scene"]["zones"].append(_zone("dark", lighting="darkness"))
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="ambient lighting"):
        _point(state)
    assert state == before


def test_actual_box_and_cylinder_membership_accept_boundary_but_reject_outside():
    state, _, _ = _case("light")
    zone = _zone()
    zone["shape"] = {"kind": "cylinder", "center": [20, 0, -5], "radius": 10, "height": 10}
    state[f"{SRD}.state"]["spatial_scene"]["zones"] = [zone]
    assert _point(state)["_illumination_point_can_see"] is True
    with pytest.raises(RulesetPreconditionRejected, match="ambient lighting"):
        _point(state, [30.1, 0, 0])


def test_saved_light_cover_movement_and_expiry_change_the_point_after_reopen():
    state, _, _, _ = _cast("light")
    _with_ambient(state, "darkness")
    assert _point(state)["_illumination_point_lighting"] == "dim"
    _run("set_srd_illumination_cover", state, object_id="orb", covered=True)
    state = json.loads(json.dumps(state))
    assert _point(state)["_illumination_point_can_see"] is False
    _run("set_srd_illumination_cover", state, object_id="orb", covered=False)
    state[f"{SRD}.state"]["spatial_scene"]["entities"][2]["metadata"]["carried_by"] = "hero"
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[80, 0, 0])
    assert _point(state, [90, 0, 0])["_illumination_point_lighting"] == "bright"
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    assert _point(state, [90, 0, 0])["_illumination_point_can_see"] is False


def test_actual_darkness_blocks_darkvision_but_actual_truesight_sees_the_destination():
    state, _, _, _ = _cast("darkness", illumination_object="", illumination_position=[30, 0, 0])
    _with_ambient(state)
    hero = state[f"{SRD}.entities"]["hero"]
    hero["darkvision_feet"] = 60
    assert _point(state)["_illumination_point_lighting"] == "magical_darkness"
    assert _point(state)["_illumination_point_can_see"] is False
    hero["truesight_feet"] = 60
    assert _point(state)["_illumination_point_can_see"] is True


@pytest.mark.parametrize(
    "visibility,movement,blindsight,visible",
    [
        ("opaque", "blocking", 0, False),
        ("opaque", "blocking", 60, False),
        ("opaque", "passable", 60, True),
        ("clear", "blocking", 0, True),
        ("clear", "blocking", 60, False),
    ],
)
def test_actual_barriers_distinguish_ordinary_sight_and_blindsight_total_cover(
    visibility, movement, blindsight, visible
):
    state, _, _ = _case("light")
    _with_ambient(state)
    wall = _wall(2)
    wall.update(visibility=visibility, movement=movement)
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = [wall]
    if blindsight:
        state[f"{SRD}.entities"]["hero"].update(conditions=["blinded"], blindsight_feet=blindsight)
    assert _point(state)["_illumination_point_can_see"] is visible
    wall["metadata"]["plane"] = "ethereal"
    assert _point(state)["_illumination_point_can_see"] is True


def test_actual_heavy_obscuration_zone_and_blindsight_resolve_the_saved_ray():
    state, _, _ = _case("light")
    _with_ambient(state)
    cloud = _zone("obscured", obstruction="heavy_obscurement")
    cloud["shape"] = {"kind": "box", "min": [10, -5, -5], "max": [12, 5, 5]}
    state[f"{SRD}.state"]["spatial_scene"]["zones"].append(cloud)
    assert _point(state)["_illumination_point_can_see"] is False
    assert _point(state)["_illumination_point_obscured"] is True
    state[f"{SRD}.entities"]["hero"]["blindsight_feet"] = 60
    assert _point(state)["_illumination_point_can_see"] is True
    cloud["metadata"]["plane"] = "ethereal"
    assert _point(state)["_illumination_point_obscured"] is False


def test_actual_fog_segment_blocks_unoccupied_point_until_source_expiry():
    state, data, params = _case("fog_cloud")
    _with_ambient(state)
    params["environment_origin"] = [15, 0, 0]
    _run("cast_spell", state, participant_data=data, **params)
    assert _point(state)["_illumination_point_can_see"] is False
    state = json.loads(json.dumps(state))
    assert _point(state)["_illumination_point_obscured"] is True
    _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    assert _point(state)["_illumination_point_can_see"] is True


def test_live_ethereal_source_qualifies_return_plane_lighting_barriers_and_sixty_foot_boundary():
    state, _, _ = _case("light")
    _with_ambient(state)
    _run(
        "apply_srd_etherealness_target",
        state,
        target_state="hero",
        caster_state="hero",
        slot_level=7,
    )
    state = json.loads(json.dumps(state))
    result = _point(state, [60, 0, 0], perception_plane="material")
    assert result["_illumination_point_plane"] == "material"
    assert result["_illumination_point_origin"] == [0, 0, 0]
    assert result["_illumination_point_can_see"] is True
    with pytest.raises(RulesetPreconditionRejected, match="sixty"):
        _point(state, [61, 0, 0], perception_plane="material")
    wall = _wall(2)
    wall["metadata"]["plane"] = "material"
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(wall)
    assert _point(state, perception_plane="material")["_illumination_point_can_see"] is False
    wall["metadata"]["plane"] = "ethereal"
    assert _point(state, perception_plane="material")["_illumination_point_can_see"] is True


def test_unsourced_projection_rejects_and_expired_source_returns_before_point_resolution():
    state, _, _ = _case("light")
    _with_ambient(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="no active source"):
        _point(state, perception_plane="ethereal")
    assert state == before
    _run(
        "apply_srd_etherealness_target",
        state,
        target_state="hero",
        caster_state="hero",
        slot_level=7,
    )
    state[f"{SRD}.state"]["lasting_tick"] = 9600
    result = _point(state, perception_plane="material")
    assert result["_illumination_point_plane"] == "material"
    assert result["_illumination_point_can_see"] is True
    assert state[f"{SRD}.entities"]["hero"]["travel_ethereal"] is None


def test_expired_projection_uses_actual_shunted_origin_before_lighting_and_ray_resolution():
    state, _, _ = _case("light")
    _with_ambient(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"][0]["shape"]["max"][2] = 100
    wall = _wall(4)
    wall["volume"] = {"kind": "box", "min": [-10, -10, -10], "max": [10, 10, 5]}
    scene["geometry"] = [wall]
    state[f"{SRD}.entities"]["hero"].update(hp=500, max_hp=500)
    _run(
        "apply_srd_etherealness_target",
        state,
        target_state="hero",
        caster_state="hero",
        slot_level=7,
    )
    state[f"{SRD}.state"]["lasting_tick"] = 9600
    result = _point(state, [0, 0, 65], perception_plane="material")
    assert result["_illumination_point_origin"] == [0, 0, 5]
    assert result["_illumination_point_distance"] == 60
    assert result["_illumination_point_can_see"] is True
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 490


def test_private_point_consumer_is_admitted_by_actual_strict_package_archive():
    runtime, payload = _runtime()
    primary, reference = _srd_drafts()
    record = deepcopy(primary.record)
    for tool in record["tools"]:
        if tool["name"] in runtime["tools"]:
            tool["input_schema"] = _tool_schema(
                runtime["tools"][tool["name"]], tool_name=tool["name"]
            )
    archive = _archive(
        (
            NormalizedPackageDraft(
                record,
                dict(primary.payloads) | {"runtime/ruleset.yaml": payload},
                primary.format_version,
            ),
            reference,
        )
    )
    assert archive
