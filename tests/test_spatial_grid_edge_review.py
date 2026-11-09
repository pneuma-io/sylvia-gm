"""Review regressions: floating grid construction and adjoining-edge projectile ownership."""

import math
from copy import deepcopy

import pytest

from sylvia_gm.spatial import SpatialEngine, SpatialValidationError, admit_spatial_scene
from tests.test_spatial_engine import _ray
from tests.test_spatial_grid_edge import edge_scene, orient


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("origin,size,index", [(0.1, 0.2, 1), (-0.3, 0.1, 3), (0.1, 0.2, 1996)])
def test_generated_fractional_vertices_are_admitted_and_canonical(horizontal, origin, size, index):
    scene = edge_scene(horizontal)
    extent = origin + (index + 2) * size
    scene["map"].update(bounds={"min": [origin, origin, 0], "max": [extent, extent, 4]})
    scene["map"]["grid"].update(origin=[origin, origin], cell_size=size)
    start, end = origin + index * size, origin + (index + 1) * size
    scene["geometry"][0]["volume"].update(
        min=orient([start, start, 0], horizontal), max=orient([start, end, 2], horizontal)
    )
    admitted = admit_spatial_scene(scene)
    assert admit_spatial_scene(admitted.as_payload()).as_payload() == admitted.as_payload()
    # Independently authored decimal and generated vertices describe the same edge.
    decimal = deepcopy(scene)
    decimal["geometry"][0]["volume"].update(
        min=orient([round(start, 10), round(start, 10), 0], horizontal),
        max=orient([round(start, 10), round(end, 10), 2], horizontal),
    )
    assert admit_spatial_scene(decimal).as_payload() == admitted.as_payload()


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("offset", [1e-8, -1e-8, 9 * math.ulp(0.3), 64 * math.ulp(0.3)])
def test_fractional_grid_rejects_actual_off_grid_vertices(horizontal, offset):
    scene = edge_scene(horizontal)
    scene["map"].update(bounds={"min": [0.1, 0.1, 0], "max": [1.3, 1.3, 4]})
    scene["map"]["grid"].update(origin=[0.1, 0.1], cell_size=0.2)
    scene["geometry"][0]["volume"].update(
        min=orient([0.3 + offset, 0.3, 0], horizontal),
        max=orient([0.3 + offset, 0.5, 2], horizontal),
    )
    with pytest.raises(SpatialValidationError):
        admit_spatial_scene(scene)


def test_grid_vertex_roundoff_tolerance_is_bounded_by_ulps_and_cell_size():
    scene = edge_scene()
    scene["map"].update(bounds={"min": [0.1, 0.1, 0], "max": [1.3, 1.3, 4]})
    scene["map"]["grid"].update(origin=[0.1, 0.1], cell_size=0.2)
    scene["geometry"][0]["volume"].update(
        min=[0.3 + 8 * math.ulp(0.3), 0.3, 0], max=[0.3 + 8 * math.ulp(0.3), 0.5, 2]
    )
    assert admit_spatial_scene(scene).as_payload()["geometry"][0]["volume"]["min"] == [0.3, 0.3, 0]
    # At large origins and tiny cells, even one ULP can be a real off-grid offset.
    origin, size = 999999, 1e-6
    scene["map"].update(
        bounds={"min": [origin, origin, 0], "max": [origin + 8 * size, origin + 8 * size, 4]}
    )
    scene["map"]["grid"].update(origin=[origin, origin], cell_size=size)
    vertex = origin + size
    scene["geometry"][0]["volume"].update(
        min=[vertex, vertex, 0], max=[vertex, origin + 2 * size, 2]
    )
    admit_spatial_scene(scene)
    for field in ("min", "max"):
        scene["geometry"][0]["volume"][field][0] = math.nextafter(vertex, math.inf)
    with pytest.raises(SpatialValidationError):
        admit_spatial_scene(scene)


def joined_scene(horizontal=False, layers=1):
    scene = edge_scene(horizontal)
    scene["geometry"] = []
    for layer in range(layers):
        for lower in (2, 3):
            edge = deepcopy(edge_scene(horizontal)["geometry"][0])
            edge["id"] = f"layer-{layer}-segment-{lower}"
            edge["edge_layer_id"] = f"layer-{layer}"
            edge["volume"].update(
                min=orient([2, lower, 0], horizontal), max=orient([2, lower + 1, 2], horizontal)
            )
            edge["metadata"]["material_resistance"] = lower
            scene["geometry"].append(edge)
    return scene


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("slope", [-1, 0, 1])
def test_one_contact_at_collinear_seam_has_directional_ownership(horizontal, reverse, slope):
    scene = joined_scene(horizontal)
    first, last = [1.5, 3 - slope * 0.5, 1], [2.5, 3 + slope * 0.5, 1]
    if reverse:
        first, last = last, first
    hits = (
        SpatialEngine(scene)
        .query(_ray(orient(first, horizontal), orient(last, horizontal)))
        .as_payload()["intersections"]
    )
    # Incoming tangent side owns an oblique seam crossing; perpendicular ties use the
    # positive grid segment in either shooting direction. Free endpoints stay closed.
    expected_segment = 2 if last[1] > first[1] else 3
    assert [hit["id"] for hit in hits] == [f"layer-0-segment-{expected_segment}"]
    assert hits[0]["metadata"]["material_resistance"] == expected_segment
    assert hits[0]["point"] == orient([2, 3, 1], horizontal)
    assert hits[0]["normal"] == orient([1 if reverse else -1, 0, 0], horizontal)


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_generated_fractional_segments_share_one_directional_vertex(horizontal, reverse):
    scene = joined_scene(horizontal)
    scene["map"].update(bounds={"min": [0.1, 0.1, 0], "max": [1.3, 1.3, 4]})
    scene["map"]["grid"].update(origin=[0.1, 0.1], cell_size=0.2)
    for edge in scene["geometry"]:
        for field in ("min", "max"):
            edge["volume"][field][:2] = [0.1 + (v - 1) * 0.2 for v in edge["volume"][field][:2]]
    first, last = [0.2, 0.4, 1], [0.4, 0.6, 1]
    if reverse:
        first, last = last, first
    result = (
        SpatialEngine(scene)
        .query(_ray(orient(first, horizontal), orient(last, horizontal)))
        .as_payload()["intersections"]
    )
    assert [row["id"] for row in result] == [f"layer-0-segment-{3 if reverse else 2}"]


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
def test_seam_ownership_preserves_layers_corners_and_nearby_hits(horizontal, reverse):
    def hits(scene, first, last):
        if reverse:
            first, last = last, first
        return (
            SpatialEngine(scene)
            .query(_ray(orient(first, horizontal), orient(last, horizontal)))
            .as_payload()["intersections"]
        )

    # Independent coincident layers retain one charge per layer, even at their seams.
    assert len(hits(joined_scene(horizontal, layers=2), [1.5, 3, 1], [2.5, 3, 1])) == 2
    unequal = joined_scene(horizontal, layers=2)
    unequal["geometry"].pop()  # An independent layer terminates at the shared vertex.
    result = hits(unequal, [1.5, 3, 1], [2.5, 3, 1])
    assert {row["id"] for row in result} == {"layer-0-segment-3", "layer-1-segment-2"}
    # Parallel, physically separated layers must not merge by hit position or grid vertex.
    scene = joined_scene(horizontal)
    second = deepcopy(scene["geometry"])
    for edge in second:
        edge["id"] += "-parallel"
        for field in ("min", "max"):
            edge["volume"][field][int(horizontal)] += 1
    scene["geometry"].extend(second)
    assert len(hits(scene, [1.5, 3, 1], [3.5, 3, 1])) == 2
    # A perpendicular corner is two different crossed planes, not a collinear seam.
    scene = joined_scene(horizontal)
    scene["geometry"] = scene["geometry"][:1]
    corner = deepcopy(scene["geometry"][0])
    corner["id"] = "corner"
    corner["volume"].update(min=orient([2, 3, 0], horizontal), max=orient([3, 3, 2], horizontal))
    scene["geometry"].append(corner)
    assert len(hits(scene, [1.5, 2.5, 1], [2.5, 3.5, 1])) == 2
    assert len(hits(scene, [1.5, 3, 1], [2.5, 3, 1])) == 1  # tangent to the corner's other plane
    # One-sided endpoints, coincident independent segments and infinitesimal side
    # selection keep their existing exact contact/material behavior.
    scene = joined_scene(horizontal)
    scene["geometry"] = scene["geometry"][:1]
    assert len(hits(scene, [1.5, 3, 1], [2.5, 3, 1])) == 1
    duplicate = deepcopy(scene["geometry"][0])
    duplicate["id"] = "independent-coincident"
    duplicate["edge_layer_id"] = "independent-coincident"
    scene["geometry"].append(duplicate)
    assert len(hits(scene, [1.5, 3, 1], [2.5, 3, 1])) == 2
    for y, segment in [(3 - 1e-9, 2), (3 + 1e-9, 3)]:
        result = hits(joined_scene(horizontal), [1.5, y, 1], [2.5, y, 1])
        assert [row["id"] for row in result] == [f"layer-0-segment-{segment}"]
