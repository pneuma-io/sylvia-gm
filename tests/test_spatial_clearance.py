"""Edge barriers preserve adjoining cells while real footprints constrain movement."""

from copy import deepcopy

import pytest

from sylvia_gm.application.tactical_grid import project_tactical_grid
from sylvia_gm.spatial import (
    SpatialEngine,
    SpatialValidationError,
    admit_spatial_scene,
    mutate_spatial_scene,
)
from tests.test_spatial_engine import _entity, _geometry, _ray, _scene


def _edge(identity, lower, upper):
    return {**_geometry(identity, lower, upper), "occupies_cells": False}


def _path(start, goal, actor="walker"):
    return {
        "kind": "path",
        "map_id": "spatial-fixture",
        "start": start,
        "goal": goal,
        "allow_diagonal": True,
        "corner_policy": "forbid_cutting",
        "include_entities": True,
        "moving_entity_id": actor,
        "max_steps": 256,
    }


def test_edge_wall_preserves_both_adjoining_lanes_but_blocks_crossing():
    scene = _scene(
        width=6,
        depth=6,
        geometry=[
            _edge("wall", [1.95, 0, 0], [2.05, 6, 2.5]),
        ],
        entities=[_entity("walker", [1.5, 0.5, 0])],
    )
    engine = SpatialEngine(scene)
    assert engine.query(_path([1, 0], [1, 5])).as_payload()["found"] is True
    assert engine.query(_path([1, 0], [4, 0])).as_payload()["found"] is False
    scene["entities"][0]["position"] = [2.5, 0.5, 0]
    assert SpatialEngine(scene).query(_path([2, 0], [2, 5])).as_payload()["found"] is True
    scene["geometry"][0]["occupies_cells"] = True
    assert SpatialEngine(scene).query(_path([2, 0], [2, 5])).as_payload()["found"] is False


def test_diagonal_cannot_escape_a_sealed_corner():
    scene = _scene(
        width=4,
        depth=4,
        geometry=[
            _edge("north-wall", [1.95, 0, 0], [2.05, 2.05, 2.5]),
            _edge("west-wall", [0, 1.95, 0], [2.05, 2.05, 2.5]),
        ],
        entities=[_entity("walker", [1.5, 1.5, 0])],
    )
    assert SpatialEngine(scene).query(_path([1, 1], [2, 2])).as_payload()["found"] is False


@pytest.mark.parametrize("footprint", ["box", "circle"])
def test_opening_uses_actual_body_width_and_accepts_an_exact_fit(footprint):
    scene = _scene(
        width=6,
        depth=5,
        geometry=[
            _edge("wall-north", [1.95, 0, 0], [2.05, 2.05, 2.5]),
            _edge("wall-south", [1.95, 2.95, 0], [2.05, 5, 2.5]),
        ],
        entities=[_entity("walker", [0.5, 2.5, 0])],
    )
    for half_width, expected in ((0.4, True), (0.45, True), (0.4501, False)):
        scene["entities"][0]["footprint"] = (
            {"kind": "circle", "radius": half_width}
            if footprint == "circle"
            else {"kind": "box", "half_extents": [half_width, half_width]}
        )
        result = SpatialEngine(scene).query(_path([0, 2], [5, 2])).as_payload()
        assert result["found"] is expected


def test_circular_clearance_does_not_use_the_circles_bounding_square():
    scene = _scene(
        width=5,
        depth=5,
        geometry=[
            _edge("corner", [2.3, 2.3, 0], [3, 3, 2]),
        ],
        entities=[_entity("walker", [1.5, 2.5, 0], footprint={"kind": "circle", "radius": 0.4})],
    )
    query = _path([1, 2], [2, 1])
    circular = SpatialEngine(scene).query(query).as_payload()
    assert circular["cells"] == [[1, 2], [2, 1]]
    scene["entities"][0]["footprint"] = {"kind": "box", "half_extents": [0.4, 0.4]}
    box = SpatialEngine(scene).query(query).as_payload()
    assert box["found"] is True and len(box["cells"]) > 2


def test_open_hinged_door_keeps_its_authored_obstruction_and_retraction_clears_it():
    closed = _scene(
        width=6,
        depth=4,
        geometry=[
            _edge("door", [1.95, 0, 0], [2.05, 4, 2.5]),
        ],
        entities=[_entity("walker", [0.5, 1.5, 0])],
    )
    query = _path([0, 1], [5, 1])
    assert SpatialEngine(closed).query(query).as_payload()["found"] is False
    opened = mutate_spatial_scene(
        admit_spatial_scene(closed),
        {
            "map_id": "spatial-fixture",
            "expected_revision": 0,
            "target": {"kind": "geometry", "id": "door"},
            "patch": {"volume": {"kind": "box", "min": [2, 1.4, 0], "max": [4, 1.6, 2.5]}},
        },
    ).scene
    engine = SpatialEngine(opened.as_payload())
    route = engine.query(query).as_payload()
    assert route["found"] is True and route["geometry_revision"] == 1
    assert [2, 1] not in route["cells"] and [3, 1] not in route["cells"]
    ray = engine.query(_ray([3, 0.5, 1], [3, 2.5, 1])).as_payload()
    assert ray["intersections"][0]["id"] == "door"
    retracted = mutate_spatial_scene(
        opened,
        {
            "map_id": "spatial-fixture",
            "expected_revision": 1,
            "target": {"kind": "geometry", "id": "door"},
            "patch": {"active": False},
        },
    ).scene
    straight = SpatialEngine(retracted.as_payload()).query(query).as_payload()
    assert straight["cells"] == [[x, 1] for x in range(6)]
    assert straight["geometry_revision"] == 2
    assert engine.query(query).as_payload() == route


def test_public_projection_carries_only_visible_geometry_placement_and_height_label():
    wall = _edge("visible", [2, 0, 0], [2.1, 4, 1.2])
    wall["metadata"] = {
        "player": {"label": "Fence", "kind": "cover", "height_label": "Chest-high"},
        "private": "secret material detail",
    }
    hidden = _edge("hidden", [4, 0, 0], [4.1, 4, 2])
    source = {"scene": _scene(geometry=[wall, hidden]), "selection": {}, "preview": {}}
    public = project_tactical_grid(source)
    assert len(public["geometry"]) == 1
    assert public["geometry"][0]["occupies_cells"] is False
    assert public["geometry"][0]["display"]["height_label"] == "Chest-high"
    assert "secret material" not in str(public) and "hidden" not in str(public)
    bad = deepcopy(source["scene"])
    bad["geometry"][0]["occupies_cells"] = "false"
    with pytest.raises(SpatialValidationError):
        admit_spatial_scene(bad)


def test_geometry_material_label_is_explicit_public_bounded_text():
    wall = _edge("visible", [2, 0, 0], [2.1, 4, 1.2])
    wall["metadata"] = {"player": {
        "label": "Fence", "kind": "cover", "material_label": "Steel / resistance 3",
    }, "material_resistance": 3, "private": "concealed weak spot"}
    source = {"scene": _scene(geometry=[wall]), "selection": {}, "preview": {}}
    projected = project_tactical_grid(source)
    assert projected["geometry"][0]["display"]["material_label"] == "Steel / resistance 3"
    assert "concealed weak spot" not in str(projected)
    assert "material_resistance" not in str(projected)
    wall["metadata"]["player"]["material_label"] = "x" * 257
    assert project_tactical_grid(source)["geometry"] == []
