"""A shared cell boundary is an authoritative surface, with no occupied-cell volume."""

from copy import deepcopy

import pytest

from sylvia_gm.application.tactical_grid import project_tactical_grid
from sylvia_gm.builder._ruleset_source import _required_engine_features
from sylvia_gm.domain.content_validation import validate_content_shape
from sylvia_gm.spatial import SpatialEngine, SpatialValidationError, admit_spatial_scene
from tests.test_spatial_clearance import _path
from tests.test_spatial_engine import _entity, _geometry, _ray, _scene


def edge_scene(horizontal=False):
    wall = _geometry("shared-wall", [2, 2, 0], [2, 3, 2])
    wall["volume"]["kind"] = "grid_edge"
    if horizontal:
        wall["volume"]["max"] = [3, 2, 2]
    wall["occupies_cells"] = False
    wall["metadata"] = {
        "material_resistance": 3,
        "cover_hp": 12,
        "player": {"label": "Shared wall", "kind": "cover", "material_label": "Resistance 3"},
        "private": "hidden-edge-detail",
    }
    return _scene(width=6, depth=6, geometry=[wall])


def orient(point, horizontal):
    return [point[1], point[0], *point[2:]] if horizontal else list(point)


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("footprint", ["box", "circle", "point"])
def test_shared_edge_blocks_crossing_and_preserves_both_lanes(horizontal, reverse, footprint):
    scene = edge_scene(horizontal)
    first, second = ([2, 2], [1, 2]) if reverse else ([1, 2], [2, 2])
    if footprint != "point":
        actor = _entity("walker", orient([first[0] + 0.5, first[1] + 0.5, 0], horizontal))
        if footprint == "circle":
            actor["footprint"] = {"kind": "circle", "radius": 0.3}
        scene["entities"] = [actor]
    engine = SpatialEngine(scene)
    query = _path(orient(first, horizontal), orient(second, horizontal))
    if footprint == "point":
        query.pop("moving_entity_id")
    detour = engine.query(query).as_payload()
    assert detour["found"] and detour["cost"] > 1
    unblocked = deepcopy(scene)
    unblocked["geometry"] = []
    assert SpatialEngine(unblocked).query(query).as_payload()["cost"] == 1
    if footprint != "point":
        sweep = engine.query(
            {
                "kind": "sweep",
                "map_id": "spatial-fixture",
                "moving_entity_id": "walker",
                "destination": orient([second[0] + 0.5, second[1] + 0.5, 0], horizontal),
                "include_entities": False,
            }
        ).as_payload()
        assert sweep["supported"] and not sweep["clear"]
        assert sweep["blockers"] == [{"kind": "geometry", "id": "shared-wall"}]
    for side in (1, 2):
        adjacent = deepcopy(scene)
        if adjacent["entities"]:
            adjacent["entities"][0]["position"] = orient([side + 0.5, 1.5, 0], horizontal)
        query.update(
            start=orient([side, 1], horizontal), goal=orient([side, 3], horizontal), max_steps=256
        )
        route = SpatialEngine(adjacent).query(query).as_payload()
        assert route["found"] and route["cost"] == 2
        assert route["cells"] == [orient([side, y], horizontal) for y in (1, 2, 3)]


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_same_surface_material_identity_normal_and_exact_endpoints(horizontal, reverse):
    engine = SpatialEngine(edge_scene(horizontal))
    for y, blocked in [(2, True), (2.5, True), (3, True), (3.0001, False), (1.9999, False)]:
        a, b = [1.5, y, 1], [2.5, y, 1]
        if reverse:
            a, b = b, a
        result = engine.query(_ray(orient(a, horizontal), orient(b, horizontal))).as_payload()
        hits = result["intersections"]
        assert len(hits) == int(blocked)
        if blocked:
            assert hits[0]["id"] == "shared-wall"
            assert hits[0]["metadata"]["material_resistance"] == 3
            assert hits[0]["metadata"]["cover_hp"] == 12
            assert hits[0]["normal"] == orient([1 if reverse else -1, 0, 0], horizontal)
    # A shot along one side, including a coplanar ray, does not cross the barrier.
    assert not engine.query(
        _ray(orient([2, 1, 1], horizontal), orient([2, 4, 1], horizontal))
    ).as_payload()["intersections"]


@pytest.mark.parametrize("horizontal", [False, True])
def test_exposure_uses_the_surface_in_both_directions_without_extending_its_endpoint(horizontal):
    for reverse in (False, True):
        scene = edge_scene(horizontal)
        a, b = ([2.5, 2.5, 0], [1.5, 2.5, 0]) if reverse else ([1.5, 2.5, 0], [2.5, 2.5, 0])
        scene["entities"] = [_entity("target", orient(b, horizontal))]
        engine = SpatialEngine(scene)
        query = {
            "kind": "exposure",
            "map_id": "spatial-fixture",
            "observer": {"point": orient([a[0], a[1], 1], horizontal)},
            "target": {"entity_id": "target"},
            "samples": {"horizontal": 3, "vertical": 3},
            "include": ["geometry"],
        }
        result = engine.query(query).as_payload()
        assert result["exposure"] == 0 and result["occluders"][0]["id"] == "shared-wall"
        query["observer"]["point"] = orient([a[0], 4, 1], horizontal)
        assert engine.query(query).as_payload()["exposure"] > 0


def test_edge_admission_is_distinct_closed_aligned_and_non_occupying():
    original = edge_scene()
    admitted = admit_spatial_scene(original).as_payload()
    assert admitted["geometry"][0]["volume"] == original["geometry"][0]["volume"]
    for lower, upper in [
        ([2.1, 2, 0], [2.1, 3, 2]),
        ([2, 2, 0], [2, 4, 2]),
        ([2, 2, 0], [3, 3, 2]),
        ([2, 2, 0], [2, 2, 2]),
    ]:
        invalid = deepcopy(original)
        invalid["geometry"][0]["volume"].update(min=lower, max=upper)
        with pytest.raises(SpatialValidationError):
            admit_spatial_scene(invalid)
    invalid = deepcopy(original)
    invalid["geometry"][0]["occupies_cells"] = True
    with pytest.raises(SpatialValidationError):
        admit_spatial_scene(invalid)
    public = project_tactical_grid({"scene": original, "selection": {}, "preview": {}})
    assert public["geometry"][0]["volume"]["kind"] == "grid_edge"
    assert "hidden-edge-detail" not in str(public)


@pytest.mark.parametrize("horizontal", [False, True])
def test_authored_edge_schema_defaults_capability_and_non_unit_grid(horizontal):
    scene = edge_scene(horizontal)
    scene["map"]["bounds"] = {"min": [-10, -20, 0], "max": [2, -8, 4]}
    scene["map"]["grid"].update(cell_size=2, origin=[-10, -20])
    surface = scene["geometry"][0]
    for field in ("min", "max"):
        surface["volume"][field][:2] = [
            -10 + surface["volume"][field][0] * 2,
            -20 + surface["volume"][field][1] * 2,
        ]
    surface.pop("occupies_cells")
    validate_content_shape("spatial_scene", scene)
    result = admit_spatial_scene(scene).as_payload()
    assert result["geometry"][0]["occupies_cells"] is False
    assert result["geometry"][0]["volume"] == surface["volume"]
    assert "spatial_grid_edge.v1" in _required_engine_features(scene)
    legacy = _scene(geometry=[_geometry("legacy", [1, 1, 0], [2, 2, 2])])
    assert admit_spatial_scene(legacy).as_payload()["geometry"][0]["occupies_cells"] is True
    assert "spatial_grid_edge.v1" not in _required_engine_features(legacy)


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("circle", [False, True])
def test_edge_endpoint_clearance_sealed_corners_and_height(horizontal, circle):
    scene = edge_scene(horizontal)
    body = _entity("walker", orient([1.5, 3.3, 0], horizontal))
    if circle:
        body["footprint"] = {"kind": "circle", "radius": 0.3}
    scene["entities"] = [body]
    sweep = {
        "kind": "sweep",
        "map_id": "spatial-fixture",
        "moving_entity_id": "walker",
        "destination": orient([2.5, 3.3, 0], horizontal),
        "include_entities": False,
    }
    assert SpatialEngine(scene).query(sweep).as_payload()["clear"]  # exact endpoint clearance
    body["position"] = orient([1.5, 3.29, 0], horizontal)
    sweep["destination"] = orient([2.5, 3.29, 0], horizontal)
    assert not SpatialEngine(scene).query(sweep).as_payload()["clear"]
    body["position"] = orient([1.5, 2.5, 2], horizontal)
    sweep["destination"] = orient([2.5, 2.5, 2], horizontal)
    assert SpatialEngine(scene).query(sweep).as_payload()["clear"]  # actor wholly above the edge
    body["position"] = orient([1.5, 2.5, 0], horizontal)
    sweep["destination"] = orient([2.5, 1.5, 0], horizontal)
    second = deepcopy(scene["geometry"][0])
    second["id"] = "corner-edge"
    second["volume"].update(min=orient([1, 2, 0], horizontal), max=orient([2, 2, 2], horizontal))
    scene["geometry"].append(second)
    assert not SpatialEngine(scene).query(sweep).as_payload()["clear"]


@pytest.mark.parametrize(
    "kind,shape",
    [
        ("box", {"min": [1, 1, 0], "max": [4, 4, 3]}),
        ("cylinder", {"center": [2, 2.5, 0], "radius": 0.5, "height": 2}),
        ("sphere", {"center": [2, 2.5, 1], "radius": 0.5}),
    ],
)
def test_edge_remains_one_geometry_result_for_area_consumers(kind, shape):
    result = (
        SpatialEngine(edge_scene())
        .query(
            {
                "kind": "area",
                "map_id": "spatial-fixture",
                "shape": {"kind": kind, **shape},
                "include": ["geometry"],
                "max_results": 10,
            }
        )
        .as_payload()
    )
    assert [row["id"] for row in result["results"]] == ["shared-wall"]


def test_edge_is_a_surface_obstacle_for_placement_and_prepared_fire_visibility():
    scene = edge_scene()
    scene["entities"] = [_entity("mover", [2.5, 2.5, 0])]
    scene["zones"] = [
        {
            "id": "watch",
            "map_id": "spatial-fixture",
            "shape": {"kind": "box", "min": [0, 0, 0], "max": [6, 6, 4]},
            "metadata": {},
        }
    ]
    engine = SpatialEngine(scene)
    placement = engine.query(
        {
            "kind": "placement",
            "map_id": "spatial-fixture",
            "moving_entity_id": "mover",
            "origin": [2, 2.5, 0],
            "exclude_entity_ids": [],
            "metadata_filter": {"key": "plane", "value": "material", "missing_matches": True},
            "max_candidates": 10000,
        }
    ).as_payload()
    assert placement["supported"] and placement["found"]
    assert placement["position"] == [1.7, 2.5, 0]
    visibility = engine.query(
        {
            "kind": "exposure_path",
            "map_id": "spatial-fixture",
            "observer": {"point": [1.5, 2.5, 1]},
            "target": {"entity_id": "mover"},
            "samples": {"horizontal": 3, "vertical": 3},
            "include": ["geometry"],
            "path": [[2.5, 2.5, 0.9], [2.5, 4.5, 0.9]],
            "zone_id": "watch",
            "max_tests": 4096,
        }
    ).as_payload()
    assert visibility["supported"] and visibility["found"]
    assert visibility["exposure"] > 0
    assert 2.5 < visibility["point"][1] < 4.5
