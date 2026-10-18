"""Rectangular directional areas use occupied bodies and an explicit 3D frame."""

import copy
import math
import random

import pytest

from sylvia_gm.spatial import SpatialEngine, SpatialValidationError, admit_spatial_scene
from tests.test_spatial_area_volumes import (
    _body,
    _hits,
    _query,
    _scene,
)
from tests.test_spatial_area_volumes import (
    test_declarative_runtime_admits_and_executes_area_templates as _runtime_check,
)


def _prism(direction=(1, 0, 0), *, up=(0, 0, 1), origin=(0, 0, 0), length=10, width=4, height=4):
    return {
        "kind": "prism",
        "origin": list(origin),
        "direction": list(direction),
        "up": list(up),
        "length": length,
        "width": width,
        "height": height,
    }


@pytest.mark.parametrize(
    "axis",
    [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1), (1, 0, 1), (1, 2, -3)],
)
@pytest.mark.parametrize("radius", [None, 0.1])
def test_prism_body_membership_follows_the_complete_3d_direction(axis, radius):
    unit = [value / math.hypot(*axis) for value in axis]

    def position(distance):
        return [unit[0] * distance, unit[1] * distance, unit[2] * distance - 0.1]

    up = (0, 1, 0) if axis[:2] == (0, 0) else (0, 0, 1)
    assert _hits(
        _prism(axis, up=up),
        _body("forward", position(5), radius=radius),
        _body("past-end", position(11), radius=radius),
        _body("behind", position(-1), radius=radius),
    ) == ["forward"]


def test_rectangular_corners_are_included_and_closed_faces_use_body_extent():
    assert _hits(
        _prism(),
        _body("corner", [5, 1.9, 1.89], half=0.01, height=0.02),
        _body("side-gap", [5, 2.10001, 0], half=0.1),
        _body("side-touch", [5, 2.1, 0], half=0.1),
        _body("end-touch", [10.1, 0, 0]),
        _body("end-gap", [10.10001, 0, 0]),
        _body("start-touch", [-0.1, 0, 0]),
        _body("start-gap", [-0.10001, 0, 0]),
        _body("height-touch", [5, 0, 2]),
        _body("height-gap", [5, 0, 2.00001]),
    ) == ["corner", "end-touch", "height-touch", "side-touch", "start-touch"]


def test_horizontal_prism_circle_membership_matches_independent_rectangle_disk_oracle():
    rng = random.Random(895)
    cases = [
        (
            rng.uniform(-3, 13),
            rng.uniform(-5, 5),
            rng.uniform(-3, 3),
            rng.uniform(0.05, 2),
            rng.uniform(0.05, 2),
        )
        for _ in range(64)
    ]
    cases += [
        (11, 2, 0, 1, 0.2),
        (11.00001, 2, 0, 1, 0.2),
        (11, 3, 0, math.sqrt(2), 0.2),
        (11.00001, 3, 0, math.sqrt(2), 0.2),
    ]
    for x, y, bottom, radius, body_height in cases:
        dx = max(-x, x - 10, 0)
        dy = max(-2 - y, y - 2, 0)
        expected = (
            bottom <= 1
            and bottom + body_height >= -1
            and dx * dx + dy * dy <= radius * radius + 1e-12
        )
        assert (
            bool(
                _hits(
                    _prism(height=2),
                    _body("body", [x, y, bottom], radius=radius, height=body_height),
                )
            )
            == expected
        )


def test_explicit_up_rotates_an_unequal_rectangular_cross_section():
    offset = math.sqrt(2)
    assert _hits(
        _prism(up=(0, 1, 1), width=2, height=6),
        _body("height-axis", [5, offset, offset - 0.05], half=0.05, height=0.1),
        _body("width-gap", [5, offset, -offset - 0.05], half=0.05, height=0.1),
    ) == ["height-axis"]


def test_direction_and_up_magnitudes_do_not_change_the_area():
    bodies = [_body("inside", [4, 1, 3.9]), _body("outside", [4, 5, 3.9])]
    assert (
        _hits(_prism((1, 0, 1)), *bodies)
        == _hits(_prism((10, 0, 10), up=(0, 0, 7)), *bodies)
        == ["inside"]
    )


def test_subnormal_direction_magnitudes_preserve_the_physical_frame():
    bodies = [_body("inside", [4, 4, 0], radius=0.1), _body("outside", [4, 8, 0], radius=0.1)]
    assert (
        _hits(_prism((1, 1, 0)), *bodies)
        == _hits(_prism((1e-323, 1e-323, 0), up=(0, 0, 1e-323)), *bodies)
        == ["inside"]
    )


def test_prism_can_extend_beyond_map_bounds_from_an_admitted_origin():
    assert _hits(_prism(origin=(99, 0, 0), length=20), _body("edge", [99.5, 0, 0])) == ["edge"]


@pytest.mark.parametrize(
    "changes",
    [
        {"direction": [0, 0, 0]},
        {"up": [0, 0, 0]},
        {"up": [1, 0, 0]},
        {"up": [-3, 0, 0]},
        {"up": [1, 1e-14, 0]},
        {"direction": [1, 0]},
        {"origin": [101, 0, 0]},
        {"direction": [1, float("nan"), 0]},
        {"up": [0, 0, float("inf")]},
        {"height": 0},
        {"width": False},
        {"length": 1000001},
        {"extra": 1},
    ],
)
def test_prism_rejects_invalid_or_ambiguous_geometry(changes):
    with pytest.raises(SpatialValidationError):
        SpatialEngine(_scene()).query(_query({**_prism(), **changes}))


def test_prism_is_query_only_and_does_not_admit_persistent_scene_geometry():
    scene = _scene()
    scene["zones"] = [{"id": "unsupported", "map_id": "areas", "shape": _prism(), "metadata": {}}]
    with pytest.raises(SpatialValidationError):
        admit_spatial_scene(scene)


def test_prism_results_keep_revision_order_bounds_and_read_only_state():
    scene = _scene(
        _body("z", [1, 0, 0]), _body("a", [1, 0, 0]), _body("inactive", [1, 0, 0], active=False)
    )
    before = copy.deepcopy(scene)
    engine = SpatialEngine(scene)
    query = _query(_prism(), maximum=1)
    result = engine.query(query).as_payload()
    assert [row["id"] for row in result["results"]] == ["a"]
    assert result["truncated"] and result["geometry_revision"] == 7
    assert result == engine.query(query).as_payload() and scene == before


def test_declared_package_query_executes_the_prism_with_runtime_templates():
    _runtime_check(
        {
            "kind": "prism",
            "origin": "{_origin}",
            "direction": [1, 0, 0],
            "up": [0, 0, 1],
            "length": 10,
            "width": "{_radius}",
            "height": "{_radius}",
        }
    )
