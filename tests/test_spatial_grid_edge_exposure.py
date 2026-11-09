"""Exposure eligibility precedes seam ownership; physical contacts keep their identity."""

from copy import deepcopy

import pytest

from sylvia_gm.spatial import SpatialEngine, SpatialValidationError
from tests.test_spatial_engine import _entity, _ray
from tests.test_spatial_grid_edge import orient
from tests.test_spatial_grid_edge_identity import mixed_join


def exposure_seam(horizontal=False, reverse=False, eligibility="clear", declared=False):
    scene = mixed_join(horizontal, declared=declared)
    scene["geometry"] = scene["geometry"][1:]  # Exactly two adjoining legacy segments.
    for edge in scene["geometry"]:
        edge["volume"]["max"][2] = 2
        edge["metadata"]["plane"] = "ethereal"
        if eligibility == "clear":
            edge["visibility"] = "clear"
        elif eligibility == "missing":
            edge["metadata"].pop("plane")
    observer = orient([3 if reverse else 1, 3, 1], horizontal)
    center = orient([1.5 if reverse else 2.5, 3, 1], horizontal)
    scene["entities"] = [_entity("target", [*center[:2], 0], height=2)]
    scene["zones"] = [{
        "id": "lane", "map_id": scene["map"]["id"],
        "shape": {"kind": "box", "min": [0, 0, 0], "max": [6, 6, 4]},
        "metadata": {},
    }]
    query = {
        "kind": "exposure", "map_id": scene["map"]["id"],
        "observer": {"point": observer}, "target": {"entity_id": "target"},
        "samples": {"horizontal": 1, "vertical": 1}, "include": ["geometry", "entities"],
    }
    if eligibility in ("metadata", "missing", "missing-matches"):
        query["metadata_filter"] = {
            "key": "plane", "value": "material",
            "missing_matches": eligibility == "missing-matches",
        }
        if eligibility == "missing-matches":
            for edge in scene["geometry"]:
                edge["metadata"].pop("plane")
    return scene, query, center


def as_path(query, center, horizontal=False):
    return {
        **query, "kind": "exposure_path", "zone_id": "lane", "max_tests": 4096,
        "path": [center, [center[i] + orient([0, 0.1, 0], horizontal)[i] for i in range(3)]],
    }


def assert_open(result, kind, center):
    if kind == "exposure":
        assert result["exposure"] == 1 and result["visible_samples"] == 1
        assert result["occluders"] == [] and result["samples"][0]["occluders"] == []
    else:
        assert result["supported"] and result["found"] and result["reason"] == "FOUND"
        assert result["distance"] == 0 and result["point"] == center and result["exposure"] == 1


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("reverse", [False, True])
@pytest.mark.parametrize("eligibility", ["clear", "metadata", "missing"])
@pytest.mark.parametrize("kind", ["exposure", "exposure_path"])
def test_ineligible_legacy_seam_is_open_but_physical_ray_still_refuses(
    horizontal, reverse, eligibility, kind,
):
    scene, query, center = exposure_seam(horizontal, reverse, eligibility)
    if kind == "exposure_path":
        query = as_path(query, center, horizontal)
    before = deepcopy(scene)
    engine = SpatialEngine(scene)
    snapshot = engine.snapshot
    result = engine.query(query).as_payload()
    assert_open(result, kind, center)
    assert engine.query(query).as_payload() == result
    assert scene == before and engine.snapshot == snapshot
    physical = _ray(query["observer"]["point"], center)
    with pytest.raises(SpatialValidationError) as error:
        engine.query(physical)
    assert error.value.code == "spatial_ambiguous_edge_join"
    assert scene == before and engine.snapshot == snapshot


@pytest.mark.parametrize("kind", ["exposure", "exposure_path"])
@pytest.mark.parametrize("eligibility", ["opaque", "missing-matches"])
def test_eligible_legacy_seam_still_refuses(kind, eligibility):
    scene, query, center = exposure_seam(eligibility=eligibility)
    if kind == "exposure_path":
        query = as_path(query, center)
    with pytest.raises(SpatialValidationError) as error:
        SpatialEngine(scene).query(query)
    assert error.value.code == "spatial_ambiguous_edge_join"


@pytest.mark.parametrize("declared", [False, True])
@pytest.mark.parametrize("excluded_end", [0, 1])
@pytest.mark.parametrize("eligibility", ["clear", "metadata"])
def test_excluded_member_cannot_hide_the_eligible_endpoint(declared, excluded_end, eligibility):
    scene, query, _ = exposure_seam(eligibility=eligibility, declared=declared)
    eligible = scene["geometry"][1 - excluded_end]
    eligible["visibility"] = "opaque"
    eligible["metadata"]["plane"] = "material"
    result = SpatialEngine(scene).query(query).as_payload()
    assert result["exposure"] == 0
    assert [row["id"] for row in result["occluders"]] == [eligible["id"]]


def test_independent_eligible_layer_survives_ineligible_legacy_seam():
    scene, query, _ = exposure_seam()
    independent = deepcopy(scene["geometry"][0])
    independent.update(id="independent", visibility="opaque", edge_layer_id="separate")
    scene["geometry"].append(independent)
    result = SpatialEngine(scene).query(query).as_payload()
    assert result["exposure"] == 0
    assert [row["id"] for row in result["occluders"]] == ["independent"]


@pytest.mark.parametrize("horizontal", [False, True])
@pytest.mark.parametrize("contact", ["observer", "destination"])
def test_surface_only_contact_is_ineligible_before_seam_resolution(horizontal, contact):
    scene, query, _ = exposure_seam(horizontal, eligibility="opaque")
    if contact == "observer":
        query["observer"]["point"] = orient([2, 3, 1], horizontal)
    else:
        scene["entities"][0]["footprint"]["half_extents"] = [0.5, 0.5]
    result = SpatialEngine(scene).query(query).as_payload()
    assert result["exposure"] == 1 and result["occluders"] == []
    with pytest.raises(SpatialValidationError) as error:
        SpatialEngine(scene).query(
            _ray(query["observer"]["point"], orient([2.5, 3, 1], horizontal))
        )
    assert error.value.code == "spatial_ambiguous_edge_join"
