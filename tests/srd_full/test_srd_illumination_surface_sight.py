"""Physical surface contact does not obscure sight without an actual solid interior crossing."""

import hashlib
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_illumination import _point_sight_tool

from sylvia_gm.application import bundled_packages, package_runtime
from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import PackageRuntimeError, materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget, test_srd_spell_result_cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_illumination_point_sight import _with_ambient
from tests.srd_full.test_srd_spell_illumination import _case
from tests.srd_full.test_srd_spell_wave3_information import _wall

PARENT = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-illumination-surface-sight/input-1"))


@lru_cache
def _runtime():
    payload = (PARENT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_bytes()
    assert hashlib.sha256(payload).hexdigest() == (
        "fe51e2c7ba951b7b36d39a492f3ff0ca436614332020c50db9e161ab2f56bb93"
    )
    runtime = yaml.load(payload, Loader=yaml.CSafeLoader)
    runtime["tools"]["resolve_srd_illumination_point_sight"] = _point_sight_tool(
        projected=True, environment=True
    )
    runtime["tools"]["resolve_srd_illumination_target_sight"] = _point_sight_tool(
        projected=True, environment=True, subject=True
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
    monkeypatch.setattr(test_srd_action_budget, "_mechanic", _mechanic)
    monkeypatch.setattr(
        test_srd_spell_result_cards,
        "_spell_definitions",
        lambda: _runtime()[0]["definitions"]["spells"],
    )


def _scene(volume, origin=(0, 0, 0), *, blinded=False):
    state, _, _ = _case("light")
    _with_ambient(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(e for e in scene["entities"] if e["id"] == "hero")["position"] = list(origin)
    barrier = _wall(2)
    barrier["volume"] = deepcopy(volume)
    scene["geometry"] = [barrier]
    if blinded:
        state[f"{SRD}.entities"]["hero"].update(conditions=["blinded"], blindsight_feet=60)
    return state


def _point(state, destination):
    return _run(
        "resolve_srd_illumination_point_sight",
        state,
        observer_state="hero",
        point=list(destination),
    ).result["context"]


@pytest.mark.parametrize("blinded", [False, True])
def test_actual_ground_face_preserves_physical_contact_without_opacity_or_total_cover(blinded):
    state = _scene({"kind": "box", "min": [-50, -50, -2], "max": [50, 50, 0]}, blinded=blinded)
    saved = deepcopy(state)
    result = _point(state, [30, 0, 0])
    assert result["_segment_hit"] is True and result["_segment_thickness"] == 30
    assert result["_illumination_point_can_see"] is True
    assert not result["_illumination_point_opaque"]
    assert not result["_illumination_point_total_cover"]
    assert state == saved


@pytest.mark.parametrize(
    "volume,origin,destination,visible",
    [
        ({"kind": "box", "min": [10, 0, -5], "max": [12, 5, 5]}, [0, 0, 0], [30, 0, 0], True),
        ({"kind": "box", "min": [10, 0, 0], "max": [12, 5, 5]}, [0, 0, 0], [30, 0, 0], True),
        ({"kind": "box", "min": [10, -5, -5], "max": [12, 5, 5]}, [0, 0, 0], [10, 0, 0], True),
        ({"kind": "box", "min": [10, -5, -5], "max": [12, 5, 5]}, [0, 0, 0], [11, 0, 0], False),
        ({"kind": "box", "min": [10, -5, -5], "max": [12, 5, 5]}, [0, 0, 0], [30, 0, 0], False),
        ({"kind": "box", "min": [10, -5, -5], "max": [12, 5, 5]}, [11, 0, 0], [30, 0, 0], False),
    ],
    ids=["face", "edge", "endpoint-touch", "endpoint-inside", "wall-crossing", "origin-inside"],
)
def test_admitted_box_interval_requires_strict_interior_for_sight(
    volume, origin, destination, visible
):
    state = _scene(volume, origin)
    result = _point(state, destination)
    assert result["_illumination_point_can_see"] is visible
    assert result["_illumination_point_opaque"] is not visible
    assert result["_illumination_point_total_cover"] is not visible


def test_actual_target_sight_reuses_surface_correction_and_preserves_real_wall():
    state = _scene({"kind": "box", "min": [-50, -50, -2], "max": [50, 50, 0]})
    result = _run(
        "resolve_srd_illumination_target_sight", state, observer_state="hero", target_state="guard"
    ).result["context"]
    assert result["_illumination_target_can_see"] is True
    state[f"{SRD}.state"]["spatial_scene"]["geometry"][0]["volume"] = {
        "kind": "box",
        "min": [2, -5, -5],
        "max": [3, 5, 5],
    }
    result = _run(
        "resolve_srd_illumination_target_sight", state, observer_state="hero", target_state="guard"
    ).result["context"]
    assert result["_illumination_target_can_see"] is False


def test_curved_barrier_hit_preserves_existing_closed_geometry_rejection(monkeypatch):
    state = _scene({"kind": "cylinder", "center": [10, 0, -5], "radius": 2, "height": 10})
    saved = deepcopy(state)
    actual = package_runtime.execute_tool
    errors = []

    def observe(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            errors.append(result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", observe)
    with pytest.raises(PackageRuntimeError):
        _point(state, [30, 0, 0])
    assert any("closed schema at $.geometry[0].volume" in e for e in errors)
    assert state == saved


def test_changed_private_sight_consumers_pass_actual_strict_wave12_archive(monkeypatch):
    monkeypatch.setattr(
        bundled_packages, "_bundled_srd_root", lambda: PARENT / "rulesets/dnd-srd-5.2.1"
    )
    primary, reference = _srd_drafts()
    runtime, payload = _runtime()
    record = deepcopy(primary.record)
    for tool in record["tools"]:
        if tool["name"] in runtime["tools"]:
            tool["input_schema"] = _tool_schema(
                runtime["tools"][tool["name"]], tool_name=tool["name"]
            )
    assert _archive(
        (
            NormalizedPackageDraft(
                record,
                dict(primary.payloads) | {"runtime/ruleset.yaml": payload},
                primary.format_version,
            ),
            reference,
        )
    )
