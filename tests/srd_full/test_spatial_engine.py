"""Deterministic behavior behind the public spatial contract."""

from __future__ import annotations

import copy
import json
import random
import subprocess
import sys
import time

import pytest

from sylvia_gm.spatial import (
    SpatialEngine,
    SpatialValidationError,
    admit_spatial_scene,
    mutate_spatial_scene,
)


def _scene(
    *,
    width: int = 12,
    depth: int = 8,
    revision: int = 0,
    geometry: list[dict[str, object]] | None = None,
    entities: list[dict[str, object]] | None = None,
    zones: list[dict[str, object]] | None = None,
    terrain: list[dict[str, object]] | None = None,
) -> dict[str, object]:
    return {
        "format": 1,
        "map": {
            "id": "spatial-fixture",
            "revision": revision,
            "bounds": {"min": [0, 0, 0], "max": [width, depth, 4]},
            "grid": {"cell_size": 1, "unit": "m", "origin": [0, 0]},
            "layers": [{"id": "floor", "min_elevation": 0, "max_elevation": 4}],
        },
        "geometry": geometry or [],
        "entities": entities or [],
        "zones": zones or [],
        "terrain": terrain or [],
    }


def _geometry(
    item_id: str,
    lower: list[float],
    upper: list[float],
    *,
    active: bool = True,
    movement: str = "blocking",
    visibility: str = "opaque",
) -> dict[str, object]:
    return {
        "id": item_id,
        "map_id": "spatial-fixture",
        "volume": {"kind": "box", "min": lower, "max": upper},
        "active": active,
        "movement": movement,
        "visibility": visibility,
        "metadata": {"source_ref": item_id},
        "destroyed": {
            "active": False,
            "movement": "passable",
            "visibility": "clear",
        },
    }


def _entity(
    item_id: str,
    position: list[float],
    *,
    half_extents: list[float] | None = None,
    height: float = 1.8,
    footprint: dict[str, object] | None = None,
) -> dict[str, object]:
    return {
        "id": item_id,
        "map_id": "spatial-fixture",
        "position": position,
        "footprint": footprint or {"kind": "box", "half_extents": half_extents or [0.3, 0.3]},
        "height": height,
        "facing": [1, 0],
        "active": True,
        "movement": "blocking",
        "visibility": "opaque",
        "metadata": {"participant_ref": item_id},
    }


def _placement(origin: list[float], **options: object) -> dict[str, object]:
    return {
        "kind": "placement", "map_id": "spatial-fixture", "moving_entity_id": "mover",
        "origin": origin, "exclude_entity_ids": [],
        "metadata_filter": {"key": "plane", "value": "material", "missing_matches": True},
        "max_candidates": 10000, **options,
    }


def _containing_zone(**changes: object) -> dict[str, object]:
    return {
        "id": "room", "map_id": "spatial-fixture",
        "shape": {"kind": "box", "min": [4, 2, 0], "max": [8, 6, 4]},
        "metadata": {"plane": "material"}, **changes,
    }


@pytest.mark.parametrize("origin,position", [
    ([0, 3, 0], [4.3, 3, 0]), ([5, 4, 10], [5, 4, 2.2]),
    ([0, 10, 10], [4.3, 5.7, 2.2]),
])
def test_placement_containing_zone_uses_actual_body_and_three_dimensional_bounds(origin, position):
    scene = _scene(entities=[_entity("mover", [1, 1, 0])], zones=[_containing_zone()])
    before = copy.deepcopy(scene)
    engine = SpatialEngine(scene)
    result = engine.query(_placement(origin, containing_zone_id="room")).as_payload()
    assert result["found"] and result["supported"]
    assert result["position"] == position
    assert result["distance"] == pytest.approx(
        sum((a - b)**2 for a, b in zip(origin, position, strict=True))**0.5
    )
    assert engine.query(_placement(origin, containing_zone_id="room")).as_payload() == result
    assert scene == before
    assert engine.query(_placement([1, 1, 0])).as_payload()["position"] == [1, 1, 0]


def test_placement_containing_zone_keeps_actual_occupancy_planes_and_exclusions():
    mover, occupant = _entity("mover", [1, 1, 0]), _entity("occupant", [5, 4, 0])
    occupant["metadata"]["plane"] = "material"
    solid = _geometry("other-plane", [4, 2, 0], [8, 6, 4])
    solid["metadata"]["plane"] = "ethereal"
    scene = _scene(entities=[mover, occupant], zones=[_containing_zone()], geometry=[solid])
    engine = SpatialEngine(scene)
    query = _placement([5, 4, 0], containing_zone_id="room")
    blocked = engine.query(query).as_payload()
    assert blocked["position"] == [4.4, 4, 0]
    assert blocked["distance"] == pytest.approx(0.6)
    excluded = engine.query({**query, "exclude_entity_ids": ["occupant"]}).as_payload()
    assert excluded["position"] == [5, 4, 0] and excluded["distance"] == 0


@pytest.mark.parametrize("failure", ["too_small", "full", "curved", "limit"])
def test_placement_containing_zone_preserves_honest_failure_results(failure):
    zone = _containing_zone()
    scene = _scene(entities=[_entity("mover", [1, 1, 0])], zones=[zone])
    query = _placement([5, 4, 0], containing_zone_id="room")
    if failure == "too_small":
        zone["shape"]["max"][0] = 4.5
    elif failure == "full":
        scene["geometry"] = [_geometry("solid", [4, 2, 0], [8, 6, 4])]
    elif failure == "curved":
        zone["shape"] = {"kind": "cylinder", "center": [6, 4, 0], "radius": 2, "height": 4}
    else:
        scene["geometry"] = [_geometry("solid", [4, 2, 0], [6, 6, 4])]
        query["max_candidates"] = 1
    result = SpatialEngine(scene).query(query).as_payload()
    assert not result["found"] and result["position"] is None
    assert result["reason"] == (
        "unsupported_geometry" if failure == "curved" else
        "candidate_limit" if failure == "limit" else "no_placement"
    )
    assert result["supported"] is (failure in {"too_small", "full"})


def _sweep(destination: list[float], **options: object) -> dict[str, object]:
    return {
        "kind": "sweep", "map_id": "spatial-fixture", "moving_entity_id": "mover",
        "destination": destination, "include_entities": True, **options,
    }


def test_sweep_uses_actual_footprint_and_reports_all_blockers_deterministically() -> None:
    geometry = [
        _geometry("second", [6, 2, 0], [6.2, 4, 3]),
        _geometry("first", [4, 2, 0], [4.2, 4, 3]),
    ]
    mover = _entity("mover", [1, 1.75, 0])
    other = _entity("other", [8, 1.75, 0])
    first = SpatialEngine(_scene(geometry=geometry, entities=[mover, other])).query(
        _sweep([10, 1.75, 0])
    ).as_payload()
    second = SpatialEngine(_scene(
        geometry=list(reversed(geometry)), entities=[other, mover],
    )).query(
        _sweep([10, 1.75, 0])
    ).as_payload()
    assert first == second
    assert first["supported"] and not first["clear"] and first["reason"] == "blocked"
    assert first["blockers"] == [
        {"kind": "entity", "id": "other"},
        {"kind": "geometry", "id": "first"},
        {"kind": "geometry", "id": "second"},
    ]
    assert first["distance"] == 9


@pytest.mark.parametrize("destination,clear", [
    ([3.5, 2, 0], True), ([3.6, 2, 0], False),
    ([10, 1.5, 0], True), ([10, 1.500001, 0], False),
])
def test_sweep_preserves_face_and_parallel_tangency(destination: list[float], clear: bool) -> None:
    origin = [1, destination[1], 0]
    engine = SpatialEngine(_scene(
        geometry=[_geometry("wall", [4, 2, 0], [4.2, 4, 3])],
        entities=[_entity("mover", origin, half_extents=[0.5, 0.5])],
    ))
    result = engine.query(_sweep(destination)).as_payload()
    assert result["clear"] is clear
    assert result["reason"] == ("clear" if clear else "blocked")


def test_sweep_vertical_clearance_and_contracted_map_bounds() -> None:
    engine = SpatialEngine(_scene(
        geometry=[_geometry("ceiling", [1, 1, 3], [4, 4, 3.2])],
        entities=[_entity("mover", [2, 2, 0], height=1)],
    ))
    assert engine.query(_sweep([2, 2, 2])).as_payload()["clear"]
    assert engine.query(_sweep([2, 2, 2.1])).as_payload()["blockers"] == [
        {"kind": "geometry", "id": "ceiling"},
    ]
    assert engine.query(_sweep([2, 2, 3.1])).as_payload()["reason"] == "out_of_bounds"
    assert engine.query(_sweep([0.2, 2, 0])).as_payload()["reason"] == "out_of_bounds"
    assert engine.query(_sweep([0.3, 2, 0])).as_payload()["clear"]


def test_sweep_uses_actual_origin_and_preserves_corner_contact() -> None:
    engine = SpatialEngine(_scene(
        geometry=[_geometry("corner", [4, 4, 0], [5, 5, 3])],
        entities=[_entity("mover", [1, 6, 0], half_extents=[0.5, 0.5])],
    ))
    before = engine.snapshot
    query = _sweep([6, 1, 0])
    assert engine.query(query).as_payload()["clear"]
    advanced = mutate_spatial_scene(before, {
        "map_id": "spatial-fixture", "expected_revision": 0,
        "target": {"kind": "entity", "id": "mover"},
        "patch": {"position": [1, 6.1, 0]},
    }).scene
    engine.replace_scene(advanced.as_payload())
    assert not engine.query(query).as_payload()["clear"]
    assert engine.query(query, snapshot=before).as_payload()["clear"]


def test_sweep_stationary_overlap_does_not_report_a_clear_route() -> None:
    engine = SpatialEngine(_scene(
        geometry=[_geometry("wall", [4, 2, 0], [5, 4, 3])],
        entities=[_entity("mover", [4.5, 3, 0])],
    ))
    result = engine.query(_sweep([4.5, 3, 0])).as_payload()
    assert result["distance"] == 0 and result["reason"] == "blocked"


def test_sweep_filters_actual_planes_defaults_and_actor_exclusions() -> None:
    material = _geometry("material", [3, 0, 0], [3.2, 8, 3])
    ethereal = _geometry("ethereal", [6, 0, 0], [6.2, 8, 3])
    ethereal["metadata"]["plane"] = "ethereal"
    mover, other = _entity("mover", [1, 2, 0]), _entity("other", [8, 2, 0])
    other["metadata"]["plane"] = "ethereal"
    engine = SpatialEngine(_scene(geometry=[material, ethereal], entities=[mover, other]))
    ordinary = engine.query(_sweep([10, 2, 0])).as_payload()
    assert len(ordinary["blockers"]) == 3
    selector = {"key": "plane", "value": "ethereal", "missing_matches": False}
    selected = engine.query(_sweep(
        [10, 2, 0], metadata_filter=selector, exclude_entity_ids=["other"],
    )).as_payload()
    assert selected["blockers"] == [{"kind": "geometry", "id": "ethereal"}]
    selector = {"key": "plane", "value": "material", "missing_matches": True}
    assert engine.query(_sweep([10, 2, 0], metadata_filter=selector)).as_payload()["blockers"] == [
        {"kind": "geometry", "id": "material"},
    ]
    assert engine.query(_sweep([10, 2, 0], include_entities=False)).as_payload()["blockers"] == (
        ordinary["blockers"][1:]
    )


@pytest.mark.parametrize("curved_mover", [False, True])
def test_sweep_uses_selected_curves_without_box_approximation(curved_mover: bool) -> None:
    mover = _entity("mover", [1, 2, 0])
    solid = _geometry("curve", [4, 1, 0], [5, 3, 3])
    if curved_mover:
        mover["footprint"] = {"kind": "circle", "radius": 0.3}
        entities = [mover]
    else:
        entities = [
            mover, _entity("curve", [4.5, 2, 0], footprint={"kind": "circle", "radius": 0.5}),
        ]
    engine = SpatialEngine(_scene(geometry=[solid] if curved_mover else [], entities=entities))
    result = engine.query(_sweep([10, 2, 0])).as_payload()
    assert result["supported"] and not result["clear"]
    assert result["reason"] == "blocked" and result["blockers"] == [
        {"kind": "geometry" if curved_mover else "entity", "id": "curve"},
    ]
    if not curved_mover:
        selector = {"key": "plane", "value": "ethereal", "missing_matches": False}
        assert engine.query(_sweep([10, 2, 0], metadata_filter=selector)).as_payload()["clear"]


def _zone(item_id: str, shape: dict[str, object]) -> dict[str, object]:
    return {
        "id": item_id,
        "map_id": "spatial-fixture",
        "shape": shape,
        "metadata": {"zone_ref": item_id},
    }


def _ray(
    origin: list[float],
    destination: list[float],
    *,
    maximum: int = 32,
) -> dict[str, object]:
    return {
        "kind": "ray",
        "map_id": "spatial-fixture",
        "origin": origin,
        "destination": destination,
        "include": ["geometry", "entities"],
        "max_intersections": maximum,
    }


@pytest.mark.parametrize(
    ("origin", "destination", "cells"),
    (
        ([0.5, 0.5, 1], [4.5, 0.5, 1], [[0, 0], [1, 0], [2, 0], [3, 0], [4, 0]]),
        (
            [0.5, 0.5, 1],
            [2.5, 2.5, 1],
            [[0, 0], [1, 0], [0, 1], [1, 1], [2, 1], [1, 2], [2, 2]],
        ),
        ([0.5, 0.5, 1], [4.5, 2.5, 1], [[0, 0], [1, 0], [1, 1], [2, 1], [3, 1], [3, 2], [4, 2]]),
        ([0.5, 0.5, 1], [2.5, 4.5, 1], [[0, 0], [0, 1], [1, 1], [1, 2], [1, 3], [2, 3], [2, 4]]),
    ),
)
def test_supercover_traversal_is_exact_and_versioned(
    origin: list[float],
    destination: list[float],
    cells: list[list[int]],
) -> None:
    result = SpatialEngine(_scene()).query(_ray(origin, destination)).as_payload()

    assert result["cells"] == cells
    assert result["algorithm_version"] == "sylvia.spatial.supercover.v2"


@pytest.mark.parametrize("origin,destination,cells", [
    ([0, 1, 1], [2, 0, 1], [[0, 1], [0, 0], [1, 0], [2, 0]]),
    ([2, 0, 1], [0, 1, 1], [[2, 0], [1, 0], [0, 0], [0, 1]]),
])
def test_ray_ending_on_a_negative_axis_corner_completes(origin, destination, cells):
    # A child process bounds the authentic query even if grid traversal regresses to a hang.
    program = (
        "import json,sys; from sylvia_gm.spatial import SpatialEngine; "
        "scene,query=json.load(sys.stdin); "
        "print(json.dumps(SpatialEngine(scene).query(query).as_payload()))"
    )
    result = subprocess.run(
        [sys.executable, "-c", program], input=json.dumps([_scene(), _ray(origin, destination)]),
        text=True, capture_output=True, check=True, timeout=3,
    )
    payload = json.loads(result.stdout)
    assert payload["cells"] == cells
    assert payload["intersections"] == []


def test_corner_ties_and_simultaneous_intersections_have_stable_order() -> None:
    geometry = [
        _geometry("north", [0, 1, 0], [1, 2, 2]),
        _geometry("east", [1, 0, 0], [2, 1, 2]),
    ]
    first = SpatialEngine(_scene(geometry=geometry)).query(_ray([0.5, 0.5, 1], [2.5, 2.5, 1]))
    second = SpatialEngine(_scene(geometry=list(reversed(geometry)))).query(
        _ray([0.5, 0.5, 1], [2.5, 2.5, 1])
    )

    assert first.as_payload() == second.as_payload()
    intersections = first.as_payload()["intersections"]
    assert [(item["id"], item["t"]) for item in intersections] == [
        ("east", 0.25),
        ("north", 0.25),
    ]


def test_ray_orders_intervening_entity_and_all_later_surfaces() -> None:
    engine = SpatialEngine(
        _scene(
            geometry=[
                _geometry("far-surface", [7, 2, 0], [7.2, 4, 3]),
                _geometry("near-surface", [4, 2, 0], [4.2, 4, 3]),
            ],
            entities=[_entity("intervening-volume", [2.5, 3, 0])],
        )
    )
    result = engine.query(_ray([0.5, 3, 1], [9.5, 3, 1])).as_payload()

    assert [item["id"] for item in result["intersections"]] == [
        "intervening-volume",
        "near-surface",
        "far-surface",
    ]
    assert all(
        result["intersections"][index]["distance"] <= result["intersections"][index + 1]["distance"]
        for index in range(2)
    )


def test_ray_starting_inside_volume_reports_zero_parameter_and_normal() -> None:
    engine = SpatialEngine(_scene(geometry=[_geometry("containing-volume", [1, 1, 0], [3, 3, 3])]))
    result = engine.query(_ray([2, 2, 1], [6, 2, 1])).as_payload()

    assert result["intersections"][0] == {
        "kind": "geometry",
        "id": "containing-volume",
        "distance": 0.0,
        "t": 0.0,
        "point": [2.0, 2.0, 1.0],
        "normal": [0.0, 0.0, 0.0],
        "metadata": {"source_ref": "containing-volume"},
    }


def _exposure(observer: list[float], target: str = "subject") -> dict[str, object]:
    return {
        "kind": "exposure",
        "map_id": "spatial-fixture",
        "observer": {"point": observer},
        "target": {"entity_id": target},
        "samples": {"horizontal": 3, "vertical": 3},
        "include": ["geometry", "entities"],
    }


def test_exposure_returns_samples_and_occluding_geometry_for_partial_visibility() -> None:
    engine = SpatialEngine(
        _scene(
            geometry=[_geometry("low-partition", [3, 2.7, 0], [3.2, 3.3, 1.15])],
            entities=[_entity("subject", [6, 3, 0], half_extents=[0.6, 0.6], height=2)],
        )
    )
    result = engine.query(_exposure([1, 3, 1.6])).as_payload()

    assert 0 < result["exposure"] < 1
    assert result["visible_samples"] == sum(sample["visible"] for sample in result["samples"])
    assert result["occluders"] == [
        {
            "kind": "geometry",
            "id": "low-partition",
            "metadata": {"source_ref": "low-partition"},
        }
    ]
    assert any(sample["occluders"] for sample in result["samples"])


def test_exposure_changes_with_height_and_observer_angle() -> None:
    low = _scene(
        geometry=[_geometry("display", [3, 2.6, 0], [3.2, 3.4, 1.3])],
        entities=[_entity("subject", [6, 3, 0], half_extents=[0.5, 0.5], height=1)],
    )
    tall = copy.deepcopy(low)
    tall["map"]["revision"] = 1
    tall["entities"][0]["height"] = 2
    low_result = SpatialEngine(low).query(_exposure([1, 3, 1.5])).as_payload()
    tall_engine = SpatialEngine(tall)
    tall_result = tall_engine.query(_exposure([1, 3, 1.5])).as_payload()
    angled_result = tall_engine.query(_exposure([1, 1, 1.5])).as_payload()

    assert low_result["exposure"] < tall_result["exposure"]
    assert angled_result["exposure"] > tall_result["exposure"]


def _path(
    start: list[int],
    goal: list[int],
    *,
    allow_diagonal: bool = True,
    corner_policy: str = "forbid_cutting",
    include_entities: bool = False,
    moving_entity_id: str | None = None,
    maximum: int = 256,
) -> dict[str, object]:
    query: dict[str, object] = {
        "kind": "path",
        "map_id": "spatial-fixture",
        "start": start,
        "goal": goal,
        "allow_diagonal": allow_diagonal,
        "corner_policy": corner_policy,
        "include_entities": include_entities,
        "max_steps": maximum,
    }
    if moving_entity_id is not None:
        query["moving_entity_id"] = moving_entity_id
    return query


@pytest.mark.parametrize("occupies_cells", [False, True])
def test_path_metadata_filter_preserves_ordinary_paths_and_uses_actual_solid_planes(
    occupies_cells: bool,
) -> None:
    wall = _geometry("barrier", [2, 0, 0], [3, 1, 3])
    wall["occupies_cells"] = occupies_cells
    mover = _entity("traveler", [0.5, 0.5, 0])
    mover["metadata"]["plane"] = "ethereal"
    scene = _scene(width=6, depth=1, geometry=[wall], entities=[mover])
    ordinary = _path([0, 0], [5, 0], include_entities=True, moving_entity_id="traveler")
    ethereal = ordinary | {
        "metadata_filter": {"key": "plane", "value": "ethereal", "missing_matches": False}
    }
    material = ordinary | {
        "metadata_filter": {"key": "plane", "value": "material", "missing_matches": True}
    }
    assert not SpatialEngine(scene).query(ordinary).as_payload()["found"]
    assert not SpatialEngine(scene).query(material).as_payload()["found"]
    assert SpatialEngine(scene).query(ethereal).as_payload()["found"]
    wall["metadata"]["plane"] = "material"
    assert SpatialEngine(scene).query(ethereal).as_payload()["found"]
    wall["metadata"]["plane"] = "ethereal"
    assert not SpatialEngine(scene).query(ethereal).as_payload()["found"]
    assert SpatialEngine(scene).query(material).as_payload()["found"]
    unfiltered = ordinary | {
        "metadata_filter": {"key": "", "value": "", "missing_matches": False}
    }
    assert SpatialEngine(scene).query(unfiltered) == SpatialEngine(scene).query(ordinary)


def test_path_metadata_filter_excludes_actual_mover_and_retains_matching_occupants() -> None:
    mover = _entity("traveler", [0.5, 0.5, 0])
    mover["metadata"]["plane"] = "ethereal"
    occupant = _entity("other", [3.5, 0.5, 0])
    scene = _scene(width=6, depth=1, entities=[mover, occupant])
    query = _path([0, 0], [5, 0], include_entities=True, moving_entity_id="traveler") | {
        "metadata_filter": {"key": "plane", "value": "ethereal", "missing_matches": False}
    }
    assert SpatialEngine(scene).query(query).as_payload()["found"]
    occupant["metadata"]["plane"] = "ethereal"
    assert not SpatialEngine(scene).query(query).as_payload()["found"]
    scene["entities"].remove(occupant)
    assert SpatialEngine(scene).query(query).as_payload()["found"]
    assert SpatialEngine(scene).query(query | {
        "metadata_filter": {"key": "plane", "value": "material", "missing_matches": True}
    }).as_payload()["found"]


@pytest.mark.parametrize("selector", [
    {"key": "plane", "value": "material", "missing_matches": "true"},
    {"key": "plane", "value": "material", "missing_matches": True, "extra": True},
    {"key": "x" * 129, "value": "material", "missing_matches": True},
])
def test_path_metadata_filter_rejects_unadmitted_filter_shapes(selector: dict[str, object]) -> None:
    with pytest.raises(SpatialValidationError):
        SpatialEngine(_scene()).query(_path([0, 0], [5, 0]) | {"metadata_filter": selector})


def test_pathfinding_forbids_diagonal_corner_cutting_when_requested() -> None:
    scene = _scene(
        width=2,
        depth=2,
        geometry=[
            _geometry("east-cell", [1, 0, 0], [2, 1, 2]),
            _geometry("north-cell", [0, 1, 0], [1, 2, 2]),
        ],
    )
    engine = SpatialEngine(scene)

    forbidden = engine.query(_path([0, 0], [1, 1])).as_payload()
    allowed = engine.query(_path([0, 0], [1, 1], corner_policy="allow_cutting")).as_payload()
    assert forbidden["found"] is False
    assert allowed["cells"] == [[0, 0], [1, 1]]


def test_pathfinding_uses_weighted_terrain_and_deterministic_equal_cost_ties() -> None:
    weighted = _scene(
        width=5,
        depth=3,
        terrain=[{"cell": [2, 1], "layer_id": "floor", "cost": 20}],
    )
    weighted_result = (
        SpatialEngine(weighted).query(_path([0, 1], [4, 1], allow_diagonal=False)).as_payload()
    )
    assert [2, 1] not in weighted_result["cells"]

    tied = _scene(
        width=3,
        depth=3,
        geometry=[_geometry("middle", [1, 1, 0], [2, 2, 2])],
    )
    first = SpatialEngine(tied).query(_path([0, 1], [2, 1], allow_diagonal=False))
    reordered = copy.deepcopy(tied)
    reordered["geometry"] = list(reversed(reordered["geometry"]))
    second = SpatialEngine(reordered).query(_path([0, 1], [2, 1], allow_diagonal=False))
    assert first.as_payload() == second.as_payload()
    assert first.as_payload()["cells"] == [[0, 1], [0, 0], [1, 0], [2, 0], [2, 1]]


def test_pathfinding_excludes_only_the_moving_entity_footprint() -> None:
    engine = SpatialEngine(
        _scene(
            width=5,
            depth=3,
            entities=[
                _entity("mover", [0.5, 1.5, 0]),
                _entity("other-actor", [2.5, 1.5, 0]),
            ],
        )
    )

    blocked_by_self = engine.query(
        _path([0, 1], [4, 1], allow_diagonal=False, include_entities=True)
    ).as_payload()
    moving = engine.query(
        _path(
            [0, 1],
            [4, 1],
            allow_diagonal=False,
            include_entities=True,
            moving_entity_id="mover",
        )
    ).as_payload()

    assert blocked_by_self["found"] is False
    assert moving["found"] is True
    assert [2, 1] not in moving["cells"]
    assert moving["cells"] == [[0, 1], [1, 1], [1, 0], [2, 0], [3, 0], [4, 0], [4, 1]]


def test_pathfinding_rejects_unknown_or_mismatched_moving_entity() -> None:
    engine = SpatialEngine(_scene(entities=[_entity("mover", [1.5, 1.5, 0])]))

    with pytest.raises(SpatialValidationError) as unknown:
        engine.query(
            _path([1, 1], [3, 1], include_entities=True, moving_entity_id="other-map-actor")
        )
    assert unknown.value.code == "spatial_query_invalid"

    with pytest.raises(SpatialValidationError) as mismatched:
        engine.query(_path([0, 0], [3, 1], include_entities=True, moving_entity_id="mover"))
    assert mismatched.value.code == "spatial_query_invalid"


def test_zone_query_returns_exact_first_entry_in_path_order() -> None:
    engine = SpatialEngine(
        _scene(
            zones=[
                _zone(
                    "round-zone",
                    {"kind": "cylinder", "center": [5, 4, 0], "radius": 1, "height": 3},
                ),
                _zone(
                    "box-zone",
                    {"kind": "box", "min": [8, 3, 0], "max": [9, 5, 3]},
                ),
            ]
        )
    )
    result = engine.query(
        {
            "kind": "crossed_zones",
            "map_id": "spatial-fixture",
            "path": [[1, 4, 1], [11, 4, 1]],
            "max_crossings": 8,
        }
    ).as_payload()

    assert [(item["zone_id"], item["point"]) for item in result["crossings"]] == [
        ("round-zone", [4.0, 4.0, 1.0]),
        ("box-zone", [8.0, 4.0, 1.0]),
    ]
    assert [item["distance"] for item in result["crossings"]] == [3.0, 7.0]


def test_area_query_returns_stable_intersecting_physical_references() -> None:
    engine = SpatialEngine(
        _scene(
            geometry=[_geometry("display", [4, 3, 0], [5, 4, 2])],
            entities=[_entity("visitor", [6, 4, 0])],
            zones=[
                _zone(
                    "light-zone",
                    {"kind": "cylinder", "center": [6, 4, 0], "radius": 2, "height": 3},
                )
            ],
        )
    )
    result = engine.query(
        {
            "kind": "area",
            "map_id": "spatial-fixture",
            "shape": {"kind": "box", "min": [3.5, 2.5, 0], "max": [6.5, 4.5, 2.5]},
            "include": ["geometry", "entities", "zones"],
            "max_results": 8,
        }
    ).as_payload()

    assert [(item["kind"], item["id"]) for item in result["results"]] == [
        ("entity", "visitor"),
        ("geometry", "display"),
        ("zone", "light-zone"),
    ]


def test_geometry_revision_invalidates_current_queries_but_snapshots_remain_immutable() -> None:
    closed = _scene(
        revision=3,
        geometry=[_geometry("moving-panel", [4, 2, 0], [4.2, 5, 3])],
    )
    engine = SpatialEngine(closed)
    old_snapshot = engine.snapshot
    first = engine.query(_ray([1, 3, 1], [8, 3, 1])).as_payload()
    repeat = engine.query(_ray([1, 3, 1], [8, 3, 1]), snapshot=old_snapshot).as_payload()

    opened = copy.deepcopy(closed)
    opened["map"]["revision"] = 4
    opened["geometry"][0]["active"] = False
    engine.replace_scene(opened)
    current = engine.query(_ray([1, 3, 1], [8, 3, 1])).as_payload()
    historical = engine.query(_ray([1, 3, 1], [8, 3, 1]), snapshot=old_snapshot).as_payload()

    assert first == repeat == historical
    assert first["intersections"][0]["id"] == "moving-panel"
    assert current["intersections"] == []
    assert current["snapshot_id"] != old_snapshot.snapshot_id
    with pytest.raises(SpatialValidationError, match="advance"):
        engine.replace_scene(closed)


def test_scene_mutation_changes_one_exact_object_and_preserves_frozen_snapshots() -> None:
    closed = _scene(
        revision=3,
        geometry=[_geometry("door", [4, 2, 0], [4.2, 5, 3])],
    )
    admitted = admit_spatial_scene(closed)
    engine = SpatialEngine(closed)
    frozen = engine.snapshot

    mutation = mutate_spatial_scene(
        admitted,
        {
            "map_id": "spatial-fixture",
            "expected_revision": 3,
            "target": {"kind": "geometry", "id": "door"},
            "patch": {"active": False, "movement": "passable", "visibility": "clear"},
        },
    )
    result = mutation.as_payload()
    engine.replace_scene(mutation.scene.as_payload())

    assert result["target"] == {"kind": "geometry", "id": "door"}
    assert result["before"]["geometry_revision"] == 3
    assert result["after"]["geometry_revision"] == 4
    assert result["before"]["snapshot_id"] == frozen.snapshot_id
    assert result["after"]["snapshot_id"] == engine.snapshot.snapshot_id
    assert result["before"]["scene_sha256"] != result["after"]["scene_sha256"]
    assert result["before"]["target_sha256"] != result["after"]["target_sha256"]
    assert engine.query(_ray([1, 3, 1], [8, 3, 1])).as_payload()["intersections"] == []
    assert engine.query(
        _ray([1, 3, 1], [8, 3, 1]), snapshot=frozen
    ).as_payload()["intersections"][0]["id"] == "door"


def test_scene_mutation_moves_one_entity_and_updates_its_complete_footprint() -> None:
    scene = admit_spatial_scene(
        _scene(revision=7, entities=[_entity("mover", [1.5, 1.5, 0])])
    )

    mutation = mutate_spatial_scene(
        scene,
        {
            "map_id": "spatial-fixture",
            "expected_revision": 7,
            "target": {"kind": "entity", "id": "mover"},
            "patch": {
                "position": [3.5, 2.5, 0],
                "footprint": {"kind": "box", "half_extents": [0.45, 0.45]},
            },
        },
    )

    moved = mutation.scene.as_payload()["entities"][0]
    assert moved["position"] == [3.5, 2.5, 0]
    assert moved["footprint"] == {"kind": "box", "half_extents": [0.45, 0.45]}
    assert mutation.scene.geometry_revision == 8


@pytest.mark.parametrize(
    ("mutation_request", "code"),
    (
        (
            {
                "map_id": "spatial-fixture",
                "expected_revision": 2,
                "target": {"kind": "geometry", "id": "door"},
                "patch": {"active": False},
            },
            "spatial_stale_revision",
        ),
        (
            {
                "map_id": "spatial-fixture",
                "expected_revision": 3,
                "target": {"kind": "geometry", "id": "missing"},
                "patch": {"active": False},
            },
            "spatial_target_missing",
        ),
        (
            {
                "map_id": "spatial-fixture",
                "expected_revision": 3,
                "target": {"kind": "geometry", "id": "door"},
                "patch": {"damage": 12},
            },
            "spatial_mutation_invalid",
        ),
        (
            {
                "map_id": "spatial-fixture",
                "expected_revision": 3,
                "target": {"kind": "geometry", "id": "door"},
                "patch": {"volume": {"kind": "box", "min": [20, 0, 0], "max": [21, 1, 1]}},
            },
            "spatial_out_of_bounds",
        ),
    ),
)
def test_scene_mutation_rejects_stale_missing_or_invalid_changes_atomically(
    mutation_request: dict[str, object],
    code: str,
) -> None:
    original = _scene(
        revision=3,
        geometry=[_geometry("door", [4, 2, 0], [4.2, 5, 3])],
    )
    before = copy.deepcopy(original)

    with pytest.raises(SpatialValidationError) as rejected:
        mutate_spatial_scene(admit_spatial_scene(original), mutation_request)

    assert rejected.value.code == code
    assert original == before


def test_scene_mutation_and_scene_admission_reject_duplicate_or_cross_kind_targets() -> None:
    duplicate = _scene(
        geometry=[_geometry("same", [1, 1, 0], [2, 2, 2])],
        entities=[_entity("same", [3, 3, 0])],
    )
    with pytest.raises(SpatialValidationError) as rejected:
        admit_spatial_scene(duplicate)
    assert rejected.value.code == "spatial_duplicate_id"

    scene = admit_spatial_scene(
        _scene(geometry=[_geometry("door", [4, 2, 0], [4.2, 5, 3])])
    )
    with pytest.raises(SpatialValidationError) as wrong_kind:
        mutate_spatial_scene(
            scene,
            {
                "map_id": "spatial-fixture",
                "expected_revision": 0,
                "target": {"kind": "entity", "id": "door"},
                "patch": {"active": False},
            },
        )
    assert wrong_kind.value.code == "spatial_target_missing"


def test_representative_tactical_map_path_query_stays_practical() -> None:
    engine = SpatialEngine(_scene(width=100, depth=100))
    started = time.perf_counter()
    result = engine.query(_path([0, 0], [99, 99], allow_diagonal=True, maximum=20_000)).as_payload()
    elapsed = time.perf_counter() - started

    assert result["found"] is True
    assert len(result["cells"]) == 100
    assert elapsed < 2
    json.dumps(result, allow_nan=False, sort_keys=True)


def test_seeded_bounded_ray_properties_are_deterministic_and_json_safe() -> None:
    engine = SpatialEngine(_scene(width=100, depth=100))
    randomizer = random.Random(20260825)
    for _ in range(100):
        origin = [randomizer.uniform(0, 99.9), randomizer.uniform(0, 99.9), 1]
        destination = [randomizer.uniform(0, 99.9), randomizer.uniform(0, 99.9), 1]
        query = _ray(origin, destination, maximum=8)
        reordered_query = dict(reversed(tuple(query.items())))
        first = engine.query(query).as_payload()
        second = engine.query(reordered_query).as_payload()
        assert first == second
        assert len(first["cells"]) <= 400
        json.dumps(first, allow_nan=False, sort_keys=True)


def _reference_face(kind="geometry", item_id="reference", face="west"):
    return {"target": {"kind": kind, "id": item_id}, "face": face}


@pytest.mark.parametrize(
    "face,position",
    [
        ("west", [4, 6, 1]),
        ("east", [8, 6, 1]),
        ("south", [4, 2, 1]),
        ("north", [4, 6, 1]),
        ("down", [4, 6, 1]),
        ("up", [4, 6, 3]),
    ],
)
def test_placement_reference_face_uses_actual_rectangle_and_preserves_point_default(face, position):
    reference = _geometry("reference", [4, 2, 1], [8, 6, 3], active=False)
    scene = _scene(geometry=[reference], entities=[_entity("mover", [1, 7, 0], height=0.5)])
    before = copy.deepcopy(scene)
    engine, query = SpatialEngine(scene), _placement([1, 7, 0])
    assert engine.query(query).as_payload()["position"] == [1, 7, 0]
    result = engine.query(query | {"reference_face": _reference_face(face=face)}).as_payload()
    assert result["position"] == position and result["distance"] == 0
    assert result["supported"] and result["found"] and scene == before
    assert (
        engine.query(query | {"reference_face": _reference_face(face=face)}).as_payload() == result
    )


def test_placement_reference_face_selects_surface_nearest_before_origin_nearest():
    reference = _geometry("reference", [8, 2, 0], [9, 6, 4], active=False)
    # The nearer obstacle is at the old origin, not at the reference surface.
    obstacle = _geometry("origin-obstacle", [1, 6, 0], [3, 8, 4])
    engine = SpatialEngine(
        _scene(
            geometry=[reference, obstacle],
            entities=[_entity("mover", [2, 7, 0])],
        )
    )
    query = _placement([2, 7, 0])
    assert engine.query(query).as_payload()["position"] == [0.7, 7, 0]
    result = engine.query(query | {"reference_face": _reference_face()}).as_payload()
    assert result["position"] == [8, 6, 0] and result["distance"] == 0


def test_placement_reference_face_honors_union_clearance_and_origin_ties():
    reference = _geometry("reference", [4, 2, 0], [5, 6, 4])
    first = _geometry("first", [2, 1, 0], [3.9, 3.8, 4])
    second = _geometry("second", [2, 4.2, 0], [3.9, 7, 4])
    scene = _scene(geometry=[reference, first, second], entities=[_entity("mover", [4, 4, 0])])
    query = _placement([4, 4, 0], reference_face=_reference_face())
    result = SpatialEngine(scene).query(query).as_payload()
    # The .6-wide body cannot fit in the .4 gap; clear corner contact is closer.
    assert result["position"] == [4.2, 1.7, 0]
    assert result["distance"] == pytest.approx((0.2**2 + 0.3**2) ** 0.5)
    scene["geometry"].reverse()
    assert SpatialEngine(scene).query(query).as_payload() == result


def test_placement_reference_face_uses_inactive_entity_body_and_excludes_actual_occupant():
    panel = _entity("panel", [8, 4, 0], half_extents=[0.2, 2], height=4)
    panel["active"] = False
    occupant = _entity("occupant", [8.2, 4, 0], height=4)
    other_plane = _geometry("other-plane", [7, 0, 0], [9, 8, 4])
    other_plane["metadata"]["plane"] = "ethereal"
    scene = _scene(geometry=[other_plane], entities=[_entity("mover", [1, 1, 0]), panel, occupant])
    query = _placement([8.2, 4, 0], reference_face=_reference_face("entity", "panel", "east"))
    result = SpatialEngine(scene).query(query).as_payload()
    assert result["position"] == [8.2, 3.4, 0] and result["distance"] == 0
    result = SpatialEngine(scene).query(query | {"exclude_entity_ids": ["occupant"]}).as_payload()
    assert result["position"] == [8.2, 4, 0] and result["distance"] == 0


def test_placement_reference_face_contracts_map_and_containing_zone_by_actual_body():
    reference = _geometry("reference", [1, 1, 3], [3, 7, 4], active=False)
    scene = _scene(
        geometry=[reference], entities=[_entity("mover", [1, 1, 0])], zones=[_containing_zone()]
    )
    result = (
        SpatialEngine(scene)
        .query(
            _placement(
                [1, 1, 10],
                reference_face=_reference_face(face="up"),
                containing_zone_id="room",
            )
        )
        .as_payload()
    )
    assert result["position"] == [4.3, 2.3, 2.2]
    assert result["distance"] == pytest.approx((1.3**2 + 1.8**2) ** 0.5)


@pytest.mark.parametrize(
    "origin,position",
    [
        ([4, 4, 0], [4, 1.7, 0]),
        ([4, 5, 0], [4, 6.3, 0]),
    ],
)
def test_placement_reference_face_uses_origin_only_after_equal_surface_distance(origin, position):
    reference = _geometry("reference", [4, 2, 0], [5, 6, 4], active=False)
    # Two overlapping solids cover the whole rectangle; the two side exits tie.
    first = _geometry("first", [3, 2, 0], [5, 4.5, 4])
    second = _geometry("second", [3, 3.5, 0], [5, 6, 4])
    scene = _scene(geometry=[reference, first, second], entities=[_entity("mover", [1, 1, 0])])
    result = (
        SpatialEngine(scene)
        .query(_placement(origin, reference_face=_reference_face()))
        .as_payload()
    )
    assert result["position"] == position and result["distance"] == pytest.approx(0.3)


@pytest.mark.parametrize(
    "failure", ["curve_reference", "curve_mover", "curve_obstacle", "limit", "full"]
)
def test_placement_reference_face_keeps_unsupported_and_exhaustion_honest(failure):
    panel = _entity("reference", [4, 4, 0], half_extents=[1, 2], height=4)
    panel["active"] = False
    mover = _entity("mover", [4, 4, 0])
    scene = _scene(entities=[mover, panel])
    query = _placement([4, 4, 0], reference_face=_reference_face("entity"))
    if failure == "curve_reference":
        panel["footprint"] = {"kind": "circle", "radius": 1}
    elif failure == "curve_mover":
        mover["footprint"] = {"kind": "circle", "radius": 0.3}
    elif failure == "curve_obstacle":
        scene["entities"].append(
            _entity("curve", [3, 4, 0], footprint={"kind": "circle", "radius": 1})
        )
    elif failure == "limit":
        scene["geometry"] = [_geometry("solid", [2, 0, 0], [4, 8, 4])]
        query["max_candidates"] = 1
    else:
        scene["geometry"] = [_geometry("solid", [0, 0, 0], [12, 8, 4])]
    result = SpatialEngine(scene).query(query).as_payload()
    assert not result["found"] and result["position"] is None and result["distance"] is None
    assert result["reason"] == (
        "candidate_limit"
        if failure == "limit"
        else "no_placement"
        if failure == "full"
        else "unsupported_geometry"
    )
    assert result["supported"] is (failure == "full")
