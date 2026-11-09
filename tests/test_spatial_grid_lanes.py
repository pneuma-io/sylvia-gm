"""Cell-centre rasterization and exact shared-map physical consumers."""

from copy import deepcopy
import pytest
from sylvia_gm.spatial import SpatialEngine, admit_spatial_scene
from sylvia_gm.spatial._grid_lane import grid_lane_zone
from sylvia_gm.spatial._interface import SpatialValidationError, mutate_spatial_scene
from tests.test_td_overwatch_lanes import Harness, NS


@pytest.fixture
def scene():
    game = Harness()
    scene = game.state[NS + ".state"]["spatial_scene"]
    scene["geometry"] = []
    scene["entities"] = [deepcopy(scene["entities"][0])]
    scene["entities"][0].update(id="shooter", position=[20.5, 20.5, 0], facing=[1, 0])
    scene["zones"] = []
    return scene


def choice(scene, **changes):
    body = scene["entities"][0]
    return {
        "pattern": "lane",
        "direction": "east",
        "extent": 4,
        "half_width": 0,
        "origin": body["position"][:],
        "facing": body["facing"][:],
        **changes,
    }


@pytest.mark.parametrize(
    "direction,step", [("east", (1, 0)), ("south", (0, 1)), ("west", (-1, 0)), ("north", (0, -1))]
)
@pytest.mark.parametrize("half_width", [0, 1, 2])
def test_rows_columns_and_wide_masks_rotate_exactly(scene, direction, step, half_width):
    zone = grid_lane_zone(
        scene, "shooter", choice(scene, direction=direction, half_width=half_width)
    )
    expected = {(20, 20)}
    dx, dy = step
    expected.update(
        (20 + dx * f - dy * s, 20 + dy * f + dx * s)
        for f in range(1, 5)
        for s in range(-half_width, half_width + 1)
    )
    assert {tuple(c) for c in zone["shape"]["cells"]} == expected
    assert len(expected) == 1 + 4 * (2 * half_width + 1)


@pytest.mark.parametrize(
    "direction,step", [("east", (1, 0)), ("south", (0, 1)), ("west", (-1, 0)), ("north", (0, -1))]
)
def test_triangle_integer_boundary_and_excluded_bounding_corners(scene, direction, step):
    zone = grid_lane_zone(
        scene, "shooter", choice(scene, pattern="triangle", direction=direction, half_width=2)
    )
    dx, dy = step
    transform = lambda f, s: (20 + dx * f - dy * s, 20 + dy * f + dx * s)
    cells = {tuple(c) for c in zone["shape"]["cells"]}
    assert transform(2, 1) in cells and transform(4, 2) in cells  # exact diagonal boundary
    assert transform(1, 1) not in cells and transform(2, 2) not in cells
    scene["zones"] = [zone]
    engine = SpatialEngine(scene)
    for forward, side, hit in [(2, 1, True), (4, 2, True), (1, 1, False), (2, 2, False)]:
        x, y = transform(forward, side)
        result = engine.query(
            {
                "kind": "crossed_zones",
                "map_id": scene["map"]["id"],
                "path": [[x + 0.4, y + 0.5, 0.9], [x + 0.6, y + 0.5, 0.9]],
                "max_crossings": 10,
            }
        ).as_payload()
        assert bool(result["crossings"]) is hit


def test_closed_cell_faces_and_clipping_without_range_invention(scene):
    scene["entities"][0]["position"] = [58.5, 20.5, 0]
    zone = grid_lane_zone(scene, "shooter", choice(scene, extent=8))
    assert zone["shape"]["cells"] == [[58, 20], [59, 20]]
    scene["zones"] = [zone]
    result = (
        SpatialEngine(scene)
        .query(
            {
                "kind": "crossed_zones",
                "map_id": scene["map"]["id"],
                "path": [[59, 20, 0.9], [59, 20, 0.9]],
                "max_crossings": 10,
            }
        )
        .as_payload()
    )
    assert len(result["crossings"]) == 1


@pytest.mark.parametrize(
    "change",
    [
        {"pattern": "cone"},
        {"direction": "ne"},
        {"direction": []},
        {"extent": 0},
        {"extent": True},
        {"half_width": -1},
        {"extent": 2048, "half_width": 2},
        {"origin": [1, 2, 0]},
        {"facing": [-1, 0]},
        {"unknown": 1},
    ],
)
def test_invalid_and_stale_declarations_reject_without_mutation(scene, change):
    before = deepcopy(scene)
    with pytest.raises(SpatialValidationError):
        grid_lane_zone(scene, "shooter", choice(scene, **change))
    assert scene == before


def test_preview_mutation_identical_and_old_zones_preserved(scene):
    old = {
        "id": "legacy",
        "map_id": scene["map"]["id"],
        "shape": {"kind": "box", "min": [1, 1, 0], "max": [4, 4, 2]},
        "metadata": {},
    }
    scene["zones"] = [old]
    lane = choice(scene, pattern="triangle", half_width=2)
    admitted = admit_spatial_scene(scene)
    preview = (
        SpatialEngine(scene)
        .query(
            {"kind": "grid_lane", "map_id": admitted.map_id, "owner_id": "shooter", "lane": [lane]}
        )
        .as_payload()
    )
    committed = mutate_spatial_scene(
        admitted,
        {
            "operation": "grid_lane",
            "map_id": admitted.map_id,
            "expected_revision": 1,
            "owner_id": "shooter",
            "lane": [lane],
        },
    )
    assert preview["zone"] == committed.as_payload()["zone"]
    assert next(z for z in committed.scene.as_payload()["zones"] if z["id"] == "legacy") == old
    assert scene["map"]["revision"] == 1
    with pytest.raises(SpatialValidationError, match="stale"):
        mutate_spatial_scene(
            committed.scene,
            {
                "operation": "grid_lane",
                "map_id": admitted.map_id,
                "expected_revision": 1,
                "owner_id": "shooter",
                "lane": [lane],
            },
        )


def test_layer_bounds_and_area_ray_consumers_use_cells(scene):
    zone = grid_lane_zone(scene, "shooter", choice(scene, pattern="triangle", half_width=2))
    scene["zones"] = [zone]
    engine = SpatialEngine(scene)
    with pytest.raises(SpatialValidationError):
        engine.query(
            {
                "kind": "ray",
                "map_id": scene["map"]["id"],
                "origin": [21.2, 22.5, 0.9],
                "destination": [21.8, 22.5, 0.9],
                "include": ["zones"],
                "max_intersections": 10,
            }
        )
    for x, y, hit in [(21, 22, False), (24, 22, True)]:
        area = engine.query(
            {
                "kind": "area",
                "map_id": scene["map"]["id"],
                "shape": {
                    "kind": "box",
                    "min": [x + 0.2, y + 0.2, 0],
                    "max": [x + 0.8, y + 0.8, 1],
                },
                "include": ["zones"],
                "max_results": 10,
            }
        ).as_payload()
        assert bool(area["results"]) is hit
    scene["map"]["bounds"]["max"][2] = 6
    scene["map"]["layers"].append({"id": "upper", "min_elevation": 3, "max_elevation": 6})
    upper = deepcopy(scene["entities"][0])
    upper.update(id="upper-target", position=[24.5, 22.5, 3])
    scene["entities"].append(upper)
    result = (
        SpatialEngine(scene)
        .query(
            {
                "kind": "exposure_path",
                "map_id": scene["map"]["id"],
                "observer": {"point": [20.9, 20.5, 1.4]},
                "target": {"entity_id": "upper-target"},
                "samples": {"horizontal": 3, "vertical": 3},
                "include": ["geometry"],
                "path": [[24.5, 23.5, 3.9], [24.5, 22.5, 3.9]],
                "zone_id": zone["id"],
                "max_tests": 4096,
            }
        )
        .as_payload()
    )
    assert result["supported"] and not result["found"]
