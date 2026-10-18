"""Physical area membership for three-dimensional combat volumes."""

from __future__ import annotations

import copy
import math
import random

import pytest
import yaml

from sylvia_gm.application.package_runtime import (
    materialize_package_mechanic,
    validate_package_runtime,
)
from sylvia_gm.domain.authority import MechanicContext, owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.spatial import SpatialEngine, SpatialValidationError, admit_spatial_scene


def _body(item_id, position, *, radius=None, half=0.1, height=0.2, active=True):
    footprint = (
        {"kind": "box", "half_extents": [half, half]}
        if radius is None
        else {"kind": "circle", "radius": radius}
    )
    return {
        "id": item_id,
        "map_id": "areas",
        "position": position,
        "footprint": footprint,
        "height": height,
        "facing": [1, 0],
        "active": active,
        "movement": "blocking",
        "visibility": "opaque",
        "metadata": {"participant_ref": item_id},
    }


def _scene(*bodies):
    return {
        "format": 1,
        "map": {
            "id": "areas",
            "revision": 7,
            "bounds": {"min": [-100, -100, -100], "max": [100, 100, 100]},
            "grid": {"cell_size": 5, "unit": "ft", "origin": [-100, -100]},
            "layers": [{"id": "space", "min_elevation": -100, "max_elevation": 100}],
        },
        "entities": list(bodies),
        "geometry": [],
        "zones": [],
        "terrain": [],
    }


def _query(shape, *, maximum=100):
    return {
        "kind": "area",
        "map_id": "areas",
        "shape": shape,
        "include": ["entities"],
        "max_results": maximum,
    }


def _hits(shape, *bodies):
    return [
        item["id"]
        for item in SpatialEngine(_scene(*bodies)).query(_query(shape)).as_payload()["results"]
    ]


def _cone(direction=(0, 0, 1), *, length=10, radius=5, apex=(0, 0, 0)):
    return {
        "kind": "cone",
        "apex": list(apex),
        "direction": list(direction),
        "length": length,
        "radius": radius,
    }


def test_sphere_uses_nearest_occupied_point_and_actual_circle():
    sphere = {"kind": "sphere", "center": [0, 0, 0], "radius": 5}
    assert _hits(
        sphere,
        _body("box-touch", [6, 0, 0], half=1),
        _body("box-gap", [6.00001, 0, 0], half=1),
        _body("circle-touch", [6, 0, 0], radius=1),
        _body("circle-gap", [6.00001, 0, 0], radius=1),
        _body("corner-outside", [5, 5, 0], radius=1),
        _body("high-touch", [0, 0, 5], radius=1),
        _body("high-gap", [0, 0, 5.00001], radius=1),
    ) == ["box-touch", "circle-touch", "high-touch"]


def test_sphere_height_and_horizontal_gap_combine_in_three_dimensions():
    sphere = {"kind": "sphere", "center": [0, 0, 0], "radius": 5}
    assert _hits(
        sphere, _body("touch", [4, 0, 4], radius=1), _body("gap", [4, 0, 4.00001], radius=1)
    ) == ["touch"]


@pytest.mark.parametrize(
    "shape",
    [
        {"kind": "sphere", "center": [99, 0, 0], "radius": 20},
        _cone((1, 0, 0), apex=(99, 0, 0), length=20),
    ],
)
def test_query_volume_can_extend_beyond_map_edge(shape):
    assert _hits(shape, _body("edge", [99.5, 0, 0])) == ["edge"]


@pytest.mark.parametrize(
    "axis",
    [(1, 0, 0), (-1, 0, 0), (0, 1, 0), (0, -1, 0), (0, 0, 1), (0, 0, -1), (1, 0, 1), (1, 2, -3)],
)
@pytest.mark.parametrize("radius", [None, 0.1])
def test_cone_includes_forward_bodies_and_excludes_beyond_base_and_behind_apex(axis, radius):
    unit = [value / math.hypot(*axis) for value in axis]

    def position(distance):
        result = [value * distance for value in unit]
        result[2] -= 0.1
        return result

    assert _hits(
        _cone(axis),
        _body("forward", position(5), radius=radius),
        _body("past-end", position(11), radius=radius),
        _body("behind", position(-1), radius=radius),
    ) == ["forward"]


def test_cone_occupied_sliver_counts_even_when_body_center_is_outside():
    assert _hits(
        _cone((1, 0, 0)),
        _body("sliver", [8, 4.4, 0], half=0.6),
        _body("miss", [8, 5, 0], half=0.1),
        _body("endpoint-touch", [10.1, 0, 0], half=0.1),
        _body("endpoint-gap", [10.10001, 0, 0], half=0.1),
    ) == ["endpoint-touch", "sliver"]


def test_tilted_cone_has_round_cross_section_in_its_own_plane():
    assert _hits(
        _cone((1, 0, 1)),
        _body("inside", [4, 0, 3.9]),
        _body("side-miss", [4, 4, 3.9]),
        _body("height-miss", [4, 0, 0]),
    ) == ["inside"]


def test_vertical_cone_circle_membership_matches_independent_disk_oracle():
    rng = random.Random(862)
    cases = [
        (rng.uniform(0, 8), rng.uniform(-2, 12), rng.uniform(0.05, 2), rng.uniform(0.05, 3))
        for _ in range(48)
    ]
    cases += [(4, 5, 1, 1), (4.00001, 5, 1, 1), (0.5, -1, 0.5, 1), (0.50001, -1, 0.5, 1)]
    for distance, bottom, body_radius, height in cases:
        top = min(bottom + height, 10)
        expected = top >= max(bottom, 0) and max(distance - body_radius, 0) <= 0.5 * top + 1e-12
        assert (
            bool(
                _hits(
                    _cone(), _body("body", [distance, 0, bottom], radius=body_radius, height=height)
                )
            )
            == expected
        )


def test_membership_is_sorted_truncated_and_excludes_inactive_bodies():
    scene = _scene(
        _body("z", [0, 0, 0]), _body("a", [0, 0, 0]), _body("inactive", [0, 0, 0], active=False)
    )
    before = copy.deepcopy(scene)
    result = (
        SpatialEngine(scene)
        .query(_query({"kind": "sphere", "center": [0, 0, 0], "radius": 1}, maximum=1))
        .as_payload()
    )
    assert [item["id"] for item in result["results"]] == ["a"]
    assert result["truncated"] is True
    assert result["geometry_revision"] == 7
    assert scene == before


@pytest.mark.parametrize(
    "shape",
    [
        {"kind": "sphere", "center": [101, 0, 0], "radius": 1},
        {"kind": "sphere", "center": [0, 0, 0], "radius": 0},
        {"kind": "sphere", "center": [0, 0, 0], "radius": True},
        {"kind": "sphere", "center": [0, 0, 0], "radius": float("nan")},
        {"kind": "sphere", "center": [0, 0, 0], "radius": 1, "extra": 1},
        _cone((0, 0, 0)),
        _cone((1, 0)),
        _cone((1, 0, float("inf"))),
        _cone(length=0),
        _cone(radius=False),
        _cone(apex=(101, 0, 0)),
    ],
)
def test_invalid_query_shapes_fail_clearly(shape):
    with pytest.raises(SpatialValidationError):
        SpatialEngine(_scene()).query(_query(shape))


@pytest.mark.parametrize("shape", [{"kind": "sphere", "center": [0, 0, 0], "radius": 1}, _cone()])
def test_new_query_shapes_are_not_admitted_as_persistent_scene_geometry(shape):
    scene = _scene()
    scene["zones"] = [{"id": "unsupported", "map_id": "areas", "shape": shape, "metadata": {}}]
    with pytest.raises(SpatialValidationError):
        admit_spatial_scene(scene)


@pytest.mark.parametrize(
    "shape",
    [
        {"kind": "sphere", "center": "{_origin}", "radius": "{_radius}"},
        {
            "kind": "cone",
            "apex": "{_origin}",
            "direction": [1, 0, 1],
            "length": 10,
            "radius": "{_radius}",
        },
    ],
)
def test_declarative_runtime_admits_and_executes_area_templates(shape):
    package_id = "sylvia-gm.conformance.area-volumes"
    runtime = {
        "sylvia_gm_runtime": {"format": 1, "state_namespace": package_id},
        "tools": {
            "inspect": {
                "description": "Inspect occupied volume.",
                "parameters": [],
                "effects": [
                    {"set": {"field": "_origin", "value": [0, 0, 0]}},
                    {"set": {"field": "_radius", "value": 5}},
                    {
                        "spatial_query": {
                            "scene": "state.spatial_scene",
                            "operation": "area",
                            "query": {
                                "map_id": "areas",
                                "shape": shape,
                                "include": ["entities"],
                                "max_results": 10,
                            },
                        },
                        "store_as": "_hits",
                    },
                    {"set": {"field": "state.hits", "value": "_hits.results"}},
                ],
                "metadata": {"costs_action": False},
            }
        },
        "procedures": {},
        "definitions": {},
        "narrative": {},
    }
    declaration = ToolDeclaration(
        name="inspect",
        capability="spatial.inspect",
        input_schema=owned_payload(
            {"type": "object", "properties": {}, "required": [], "additionalProperties": False}
        ),
        state_predicates=(),
        turn_statuses=("open",),
        writers=("dm",),
    )
    runtime_bytes = yaml.safe_dump(runtime, sort_keys=False).encode()
    validate_package_runtime(
        package_id=package_id, declarations=(declaration,), runtime_bytes=runtime_bytes
    )
    mechanic = materialize_package_mechanic(
        package_id=package_id,
        package_version="1.0.0",
        package_sha256="a" * 64,
        declaration=declaration,
        runtime_bytes=runtime_bytes,
    )
    context = MechanicContext(
        campaign_id="areas",
        turn_id="turn",
        action_id="action",
        state=owned_payload(
            {
                f"{package_id}.state": {
                    "spatial_scene": _scene(_body("near", [1, 0, 1]), _body("far", [30, 0, 0])),
                    "hits": [],
                }
            }
        ),
        parameters=owned_payload({}),
    )
    outcome = mechanic(context)
    assert [item["id"] for item in outcome.mutations[0].value["hits"]] == ["near"]
    assert outcome.result["spatial_evidence"][0]["geometry_revision"] == 7
