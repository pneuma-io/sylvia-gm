"""Continuity must be authored; rounded boundary vertices must remain in the map."""

import math
import uuid
from copy import deepcopy

import pytest

from sylvia_gm.builder._ruleset_source import _required_engine_features
from sylvia_gm.domain.content_validation import validate_content_shape
from sylvia_gm.spatial import (
    SpatialEngine,
    SpatialValidationError,
    admit_spatial_scene,
    mutate_spatial_scene,
)
from tests.test_spatial_engine import _ray
from tests.test_spatial_grid_edge import edge_scene, orient


def mixed_join(horizontal=False, declared=False, same_profile=False):
    scene = edge_scene(horizontal)
    left = scene["geometry"][0]
    left["id"] = "b-left"
    left["volume"]["max"][2] = 1
    left["metadata"].update(material_resistance=1, cover_hp=12, authored={"part": "left"})
    right = deepcopy(left)
    right["id"] = "c-right"
    right["volume"].update(min=orient([2, 3, 0], horizontal), max=orient([2, 4, 1], horizontal))
    right["metadata"]["authored"] = {"part": "right"}
    steel = deepcopy(left)
    steel["id"] = "a-steel"
    if not same_profile:
        steel["volume"]["max"][2] = 2
        steel["metadata"].update(material_resistance=4, cover_hp=30, authored={"material": "steel"})
    else:
        steel["metadata"] = deepcopy(left["metadata"])
        right["metadata"] = deepcopy(left["metadata"])
    if declared:
        left["edge_layer_id"] = right["edge_layer_id"] = "continuous-wall"
        steel["edge_layer_id"] = "independent-steel"
    scene["geometry"] = [steel, left, right]
    return scene


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("same_profile", [False, True])
def test_legacy_ambiguous_join_never_selects_a_layer_by_id_or_profile(
    horizontal, reverse, same_profile
):
    scene = mixed_join(horizontal, same_profile=same_profile)
    admitted = admit_spatial_scene(scene)
    assert all("edge_layer_id" not in row for row in admitted.as_payload()["geometry"])
    a, b = [1.5, 3, 0.5], [2.5, 3, 0.5]
    if reverse:
        a, b = b, a
    with pytest.raises(SpatialValidationError) as error:
        SpatialEngine(admitted.as_payload()).query(
            _ray(orient(a, horizontal), orient(b, horizontal))
        )
    assert error.value.code == "spatial_ambiguous_edge_join"
    # A legacy scene still loads, and adjacency away from the exact join is unchanged.
    ordinary = (
        SpatialEngine(scene)
        .query(_ray(orient([1.5, 3.5, 0.5], horizontal), orient([2.5, 3.5, 0.5], horizontal)))
        .as_payload()
    )
    assert [row["id"] for row in ordinary["intersections"]] == ["c-right"]


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("slope", [-1, 0, 1])
def test_explicit_continuity_keeps_the_independent_taller_source(horizontal, reverse, slope):
    scene = mixed_join(horizontal, declared=True)
    validate_content_shape("spatial_scene", scene)
    a, b = [1.5, 3 - slope * 0.5, 0.5], [2.5, 3 + slope * 0.5, 0.5]
    if reverse:
        a, b = b, a
    result = (
        SpatialEngine(scene).query(_ray(orient(a, horizontal), orient(b, horizontal))).as_payload()
    )
    wall = "b-left" if b[1] > a[1] else "c-right"
    assert {row["id"] for row in result["intersections"]} == {"a-steel", wall}
    assert (
        next(row for row in result["intersections"] if row["id"] == "a-steel")["metadata"][
            "cover_hp"
        ]
        == 30
    )
    scene["geometry"][0].pop("edge_layer_id")  # An isolated legacy end is not the declared wall.
    untagged_steel = (
        SpatialEngine(scene).query(_ray(orient(a, horizontal), orient(b, horizontal))).as_payload()
    )
    assert {row["id"] for row in untagged_steel["intersections"]} == {"a-steel", wall}
    # Height filters contacts; it never establishes continuity between different sources.
    upper = (
        SpatialEngine(scene)
        .query(_ray(orient([1.5, 3, 1.5], horizontal), orient([2.5, 3, 1.5], horizontal)))
        .as_payload()
    )
    assert [row["id"] for row in upper["intersections"]] == ["a-steel"]


def test_matching_profiles_do_not_prove_continuity_and_duplicate_members_fail_safe():
    scene = mixed_join(declared=True, same_profile=True)
    for row in scene["geometry"]:
        row["edge_layer_id"] = row["id"]
    assert (
        len(
            SpatialEngine(scene)
            .query(_ray([1.5, 3, 0.5], [2.5, 3, 0.5]))
            .as_payload()["intersections"]
        )
        == 3
    )
    for row in scene["geometry"]:
        row["edge_layer_id"] = "invalid-overlap"
    with pytest.raises(SpatialValidationError) as error:
        SpatialEngine(scene).query(_ray([1.5, 3, 0.5], [2.5, 3, 0.5]))
    assert error.value.code == "spatial_ambiguous_edge_join"


def test_explicit_authoring_intent_can_assign_machine_identity_with_revision_guards():
    original = mixed_join()
    scene = admit_spatial_scene(original)
    layer = "layer-" + uuid.uuid4().hex  # Allocated internally after an explicit connect choice.
    for identity in ("b-left", "c-right"):
        scene = mutate_spatial_scene(
            scene,
            {
                "map_id": "spatial-fixture",
                "expected_revision": scene.geometry_revision,
                "target": {"kind": "geometry", "id": identity},
                "patch": {"edge_layer_id": layer},
            },
        ).scene
    result = (
        SpatialEngine(scene.as_payload()).query(_ray([1.5, 3, 0.5], [2.5, 3, 0.5])).as_payload()
    )
    assert {row["id"] for row in result["intersections"]} == {"a-steel", "c-right"}
    assert all("edge_layer_id" not in row for row in original["geometry"])
    with pytest.raises(SpatialValidationError) as error:
        mutate_spatial_scene(
            scene,
            {
                "map_id": "spatial-fixture",
                "expected_revision": 0,
                "target": {"kind": "geometry", "id": "a-steel"},
                "patch": {"edge_layer_id": layer},
            },
        )
    assert error.value.code == "spatial_stale_revision"
    features = _required_engine_features({"patch": {"edge_layer_id": layer}})
    assert "spatial_grid_edge.v1" in features
    assert "spatial_edge_continuity.v1" in features


@pytest.mark.parametrize("invalid", [None, 7, ""])
def test_layer_identity_is_closed_and_validated(invalid):
    scene = edge_scene()
    scene["geometry"][0]["edge_layer_id"] = invalid
    with pytest.raises(SpatialValidationError):
        admit_spatial_scene(scene)


def boundary_scene(horizontal=False, maximum=None):
    scene = edge_scene(horizontal)
    generated_max = 0.3 + 2 * 0.3 if maximum is None else maximum
    scene["map"].update(bounds={"min": [0.3, 0.3, 0], "max": [generated_max, generated_max, 4]})
    scene["map"]["grid"].update(origin=[0.3, 0.3], cell_size=0.3)
    scene["geometry"][0]["volume"].update(
        min=orient([0.6, 0.6, 0], horizontal), max=orient([0.6, 0.9, 2], horizontal)
    )
    return scene


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("maximum", [0.3 + 2 * 0.3, 0.9, math.nextafter(0.9, math.inf)])
def test_generated_map_boundary_and_canonical_edge_use_the_same_limit(horizontal, maximum):
    scene = boundary_scene(horizontal, maximum)
    admitted = admit_spatial_scene(scene)
    payload = admitted.as_payload()
    assert payload["map"]["bounds"] == scene["map"]["bounds"]
    assert payload["geometry"][0]["volume"]["max"][int(not horizontal)] == maximum
    assert admit_spatial_scene(payload).as_payload() == payload
    result = (
        SpatialEngine(payload)
        .query(
            _ray(orient([0.3, maximum, 1], horizontal), orient([maximum, maximum, 1], horizontal))
        )
        .as_payload()
    )
    assert [row["id"] for row in result["intersections"]] == ["shared-wall"]


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("maximum", [0.89, 0.9 - 9 * math.ulp(0.9)])
def test_genuinely_outside_boundary_is_not_widened_to_admit_an_edge(horizontal, maximum):
    with pytest.raises(SpatialValidationError):
        admit_spatial_scene(boundary_scene(horizontal, maximum))
