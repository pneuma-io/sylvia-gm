"""Compose SRD creature and Cover policy with the shared occupied-volume query."""

import os
import zipfile
from copy import deepcopy

import pytest
import yaml
from scripts.srd_lite_coordinate_areas import bind_coordinate_cones
from tests import test_srd_lite_vertical_cones as previous
from tests.test_srd_lite_mass_cure_geometry import NAMESPACE, _calls

from sylvia_gm.runtime import RulesetRuntime


@pytest.fixture(scope="module")
def cones():
    with zipfile.ZipFile(os.environ["SRD_LITE_CANDIDATE"]) as archive:
        data = yaml.load(
            archive.read("packages/" + NAMESPACE + "/runtime/ruleset.yaml"), Loader=yaml.CSafeLoader
        )
    bind_coordinate_cones(data)
    pool = data.get("parameter_declarations", {})
    closure, pending = {}, [previous.COLLECT, previous.COVER, "qualify_srd_cone_admitted_box"]
    while pending:
        name = pending.pop()
        if name not in closure:
            tool = deepcopy(data["tools"][name])
            tool["parameters"] = [
                deepcopy(pool[row]) if isinstance(row, str) else row for row in tool["parameters"]
            ]
            closure[name] = tool
            pending.extend(_calls(tool["effects"]))
    subset = {key: value for key, value in data.items() if key not in {"tools", "procedures"}}
    subset["tools"] = closure
    return RulesetRuntime(ruleset_dict=subset)


@pytest.mark.parametrize(
    ("position", "direction", "included"),
    [
        ([0, 0, 10], [0, 0, 2], True),
        ([0, 0, 21], [0, 0, 1], True),
        ([0, 0, 21.01], [0, 0, 1], False),
        ([5.9, 5.9, 21], [0, 0, 1], False),
        ([5.6, 5.6, 21], [0, 0, 1], True),
        ([0, 0, -12], [0, 0, -3], True),
        ([0, 0, -21.01], [0, 0, -1], False),
        ([0, 0, 10], [0, 0, -1], False),
    ],
)
def test_retained_vertical_cone_contract(cones, position, direction, included):
    previous.test_vertical_membership_uses_actual_box_body(cones, position, direction, included)


@pytest.mark.parametrize(
    ("position", "direction", "included"),
    [
        ([10, 0, 0], [1, 0], True),
        ([-10, 0, 0], [1, 0], False),
        ([5, 5, 0], [1, 1], True),
        ([5, -5, 0], [1, 1], False),
    ],
)
def test_retained_horizontal_cone_contract(cones, position, direction, included):
    previous.test_existing_horizontal_directions_are_preserved(cones, position, direction, included)


@pytest.mark.parametrize(
    ("minimum", "maximum", "visible"),
    [
        ([-2, -2, 8], [2, 2, 9], False),
        ([10, 10, 8], [12, 12, 9], True),
    ],
)
def test_retained_physical_cover_contract(cones, minimum, maximum, visible):
    previous.test_vertical_cover_uses_actual_overhead_blocker(cones, minimum, maximum, visible)


def test_retained_selected_apex_and_missing_cover_contract(cones):
    previous.test_selected_vertical_apex_stays_in_the_actual_source(cones)
    previous.test_overhead_obstruction_without_cover_facts_fails_cleanly(cones)
    previous.test_non_creature_scenery_is_not_a_breath_target(cones)


@pytest.mark.parametrize("source_circle", [False, True])
@pytest.mark.parametrize(
    ("position", "direction"), [([0, 0, 10], [0, 0, 1]), ([8, 0, 8], [1, 0, 1])]
)
def test_circle_targets_and_tilted_axes_use_actual_scene_bodies(
    cones, source_circle, position, direction
):
    state = previous.state_for(position)
    frames = state[NAMESPACE + ".state"]["spatial_scene"]["entities"]
    frames[1]["footprint"] = {"kind": "circle", "radius": 1}
    if source_circle:
        frames[0]["footprint"] = {"kind": "circle", "radius": 1}
    frames[0]["id"], frames[1]["id"] = "hero-body", "guard-body"
    selected = previous.run(
        cones,
        state,
        previous.COLLECT,
        {"source_state": "hero", "length_feet": 15, "direction": direction},
    )
    assert selected.success, selected.error
    assert selected.context["_srd_cone_targets"] == ["guard"]
    assert selected.context["_cone_area"]["geometry_revision"] == 0
    covered = previous.run(
        cones,
        state,
        previous.COVER,
        {
            "source_state": "hero",
            "targets": selected.context["_srd_cone_targets"],
            "origin": selected.context["_srd_cone_origin"],
            "axis": selected.context["_srd_cone_axis"],
            "source_frames": [selected.context["_srd_cone_source"]],
        },
    )
    assert covered.success, covered.error
    assert covered.context["_srd_area_targets"] == ["guard"]


def test_circle_source_apex_is_checked_against_disk_not_its_bounding_box(cones):
    state = previous.state_for([0, 0, 10])
    state[NAMESPACE + ".state"]["spatial_scene"]["entities"][0]["footprint"] = {
        "kind": "circle",
        "radius": 1,
    }
    rejected = previous.run(
        cones,
        state,
        previous.COLLECT,
        {"source_state": "hero", "length_feet": 15, "apex": [1, 1, 3], "direction": [0, 0, 1]},
    )
    assert not rejected.success and "source volume" in rejected.error


def test_explicit_origin_inclusion_and_other_plane_are_preserved(cones):
    state = previous.state_for([0, 0, 10])
    state[NAMESPACE + ".state"]["spatial_scene"]["entities"][1]["metadata"]["plane"] = "ethereal"
    selected = previous.run(
        cones,
        state,
        previous.COLLECT,
        {"source_state": "hero", "length_feet": 15, "direction": [0, 0, 1], "include_origin": True},
    )
    assert selected.success, selected.error
    assert selected.context["_srd_cone_targets"] == ["hero"]


@pytest.mark.parametrize("direction", [[0, 0, 0], [1], [1, 0, 0, 0]])
def test_invalid_direction_rejects_without_resource_or_random_changes(cones, direction):
    result = previous.run(
        cones,
        previous.state_for([0, 0, 10]),
        previous.COLLECT,
        {"source_state": "hero", "length_feet": 15, "direction": direction},
    )
    assert not result.success
