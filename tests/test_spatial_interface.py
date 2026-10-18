"""Ruleset-neutral spatial state and query contract acceptance."""

from __future__ import annotations

import json
import math
import re
from pathlib import Path

import pytest

from sylvia_gm.spatial import (
    SPATIAL_ALGORITHM_VERSION,
    SpatialValidationError,
    admit_spatial_query,
    admit_spatial_result,
    admit_spatial_scene,
)


def _scene() -> dict[str, object]:
    return {
        "format": 1,
        "map": {
            "id": "gallery-floor",
            "revision": 7,
            "bounds": {"min": [0, 0, 0], "max": [12, 8, 4]},
            "grid": {"cell_size": 1, "unit": "m", "origin": [0, 0]},
            "layers": [
                {"id": "floor", "min_elevation": 0, "max_elevation": 4},
            ],
        },
        "geometry": [
            {
                "id": "partition",
                "map_id": "gallery-floor",
                "volume": {"kind": "box", "min": [4, 1, 0], "max": [4.2, 7, 3]},
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": {"catalog_ref": "gallery.partition"},
                "destroyed": {
                    "active": False,
                    "movement": "passable",
                    "visibility": "clear",
                },
            },
            {
                "id": "movable-screen",
                "map_id": "gallery-floor",
                "volume": {"kind": "box", "min": [7, 2, 0], "max": [7.2, 4, 2]},
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": {"catalog_ref": "gallery.screen"},
                "destroyed": {
                    "active": True,
                    "movement": "passable",
                    "visibility": "clear",
                },
            },
        ],
        "entities": [
            {
                "id": "visitor",
                "map_id": "gallery-floor",
                "position": [6, 3, 0],
                "footprint": {"kind": "box", "half_extents": [0.3, 0.25]},
                "height": 1.75,
                "facing": [1, 0],
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": {"participant_ref": "visitor"},
            }
        ],
        "zones": [
            {
                "id": "conservation-light",
                "map_id": "gallery-floor",
                "shape": {
                    "kind": "cylinder",
                    "center": [9, 3, 0],
                    "radius": 1.5,
                    "height": 3,
                },
                "metadata": {"effect_ref": "gallery.light"},
            }
        ],
        "terrain": [
            {"cell": [5, 4], "layer_id": "floor", "cost": 2},
        ],
    }


def test_scene_admission_captures_canonical_immutable_snapshot() -> None:
    source = _scene()
    admitted = admit_spatial_scene(source)
    reordered = {key: source[key] for key in reversed(tuple(source))}
    replay = admit_spatial_scene(reordered)

    assert admitted.map_id == "gallery-floor"
    assert admitted.geometry_revision == 7
    assert admitted.algorithm_version == SPATIAL_ALGORITHM_VERSION
    assert admitted.snapshot_id == replay.snapshot_id
    assert admitted.as_payload() == replay.as_payload()
    assert [item["id"] for item in admitted.as_payload()["geometry"]] == [
        "movable-screen",
        "partition",
    ]

    source["geometry"] = []
    assert len(admitted.as_payload()["geometry"]) == 2


@pytest.mark.parametrize(
    "query_payload",
    (
        {
            "kind": "ray",
            "map_id": "gallery-floor",
            "origin": [0.5, 0.5, 1.2],
            "destination": [11.5, 7.5, 1.2],
            "include": ["geometry", "entities"],
            "max_intersections": 32,
        },
        {
            "kind": "exposure",
            "map_id": "gallery-floor",
            "observer": {"point": [1, 3, 1.6]},
            "target": {"entity_id": "visitor"},
            "samples": {"horizontal": 3, "vertical": 3},
            "include": ["geometry", "entities"],
        },
        {
            "kind": "path",
            "map_id": "gallery-floor",
            "start": [1, 1],
            "goal": [10, 6],
            "allow_diagonal": True,
            "corner_policy": "forbid_cutting",
            "include_entities": True,
            "max_steps": 256,
        },
        {
            "kind": "crossed_zones",
            "map_id": "gallery-floor",
            "path": [[1, 1, 1], [9, 3, 1], [11, 3, 1]],
            "max_crossings": 32,
        },
        {
            "kind": "area",
            "map_id": "gallery-floor",
            "shape": {
                "kind": "cylinder",
                "center": [6, 3, 0],
                "radius": 3,
                "height": 3,
            },
            "include": ["geometry", "entities", "zones"],
            "max_results": 64,
        },
    ),
)
def test_query_admission_is_closed_and_canonical(query_payload: dict[str, object]) -> None:
    scene = admit_spatial_scene(_scene())
    admitted = admit_spatial_query(scene, query_payload)

    assert admitted.kind == query_payload["kind"]
    assert admitted.map_id == scene.map_id
    assert admitted.as_payload() == query_payload
    json.dumps(admitted.as_payload(), allow_nan=False, sort_keys=True)


@pytest.mark.parametrize(
    ("mutate", "code"),
    (
        (lambda value: value.update({"surprise": True}), "spatial_scene_invalid"),
        (lambda value: value["map"].update({"revision": -1}), "spatial_scene_invalid"),
        (
            lambda value: value["map"]["bounds"].update({"max": [math.inf, 8, 4]}),
            "spatial_scene_invalid",
        ),
        (lambda value: value["geometry"].append(value["geometry"][0]), "spatial_duplicate_id"),
        (lambda value: value["entities"][0].update({"map_id": "other-map"}), "spatial_cross_map"),
        (
            lambda value: value["entities"][0].update({"position": [99, 3, 0]}),
            "spatial_out_of_bounds",
        ),
    ),
)
def test_scene_admission_rejects_invalid_authoritative_state(mutate: object, code: str) -> None:
    source = _scene()
    mutate(source)  # type: ignore[operator]
    with pytest.raises(SpatialValidationError) as rejected:
        admit_spatial_scene(source)
    assert rejected.value.code == code


@pytest.mark.parametrize(
    ("query_payload", "code"),
    (
        (
            {
                "kind": "ray",
                "map_id": "other-map",
                "origin": [0, 0, 0],
                "destination": [1, 1, 1],
                "include": ["geometry"],
                "max_intersections": 4,
            },
            "spatial_cross_map",
        ),
        (
            {
                "kind": "ray",
                "map_id": "gallery-floor",
                "origin": [0, 0, 0],
                "destination": [1, 1, 1],
                "include": ["geometry"],
                "max_intersections": 4,
                "mechanic": "interpret-me",
            },
            "spatial_query_invalid",
        ),
        (
            {
                "kind": "path",
                "map_id": "gallery-floor",
                "start": [1, 1],
                "goal": [10, 6],
                "allow_diagonal": True,
                "corner_policy": "invent-a-policy",
                "include_entities": True,
                "max_steps": 256,
            },
            "spatial_query_invalid",
        ),
    ),
)
def test_query_admission_rejects_cross_map_open_or_invalid_requests(
    query_payload: dict[str, object],
    code: str,
) -> None:
    with pytest.raises(SpatialValidationError) as rejected:
        admit_spatial_query(admit_spatial_scene(_scene()), query_payload)
    assert rejected.value.code == code


def test_result_admission_requires_snapshot_provenance_and_closed_shape() -> None:
    scene = admit_spatial_scene(_scene())
    result = {
        "kind": "ray",
        "map_id": scene.map_id,
        "snapshot_id": scene.snapshot_id,
        "geometry_revision": scene.geometry_revision,
        "algorithm_version": SPATIAL_ALGORITHM_VERSION,
        "cells": [[0, 0], [1, 0]],
        "intersections": [],
        "truncated": False,
    }
    assert admit_spatial_result(scene, result).as_payload() == result

    result["mechanical_consequence"] = "package-owned"
    with pytest.raises(SpatialValidationError, match="closed"):
        admit_spatial_result(scene, result)


def test_result_admission_rejects_open_or_malformed_nested_facts() -> None:
    scene = admit_spatial_scene(_scene())
    result = {
        "kind": "ray",
        "map_id": scene.map_id,
        "snapshot_id": scene.snapshot_id,
        "geometry_revision": scene.geometry_revision,
        "algorithm_version": SPATIAL_ALGORITHM_VERSION,
        "cells": [[0, 0]],
        "intersections": [
            {
                "kind": "geometry",
                "id": "partition",
                "distance": 4,
                "t": 0.4,
                "point": [4, 1, 1],
                "normal": [-1, 0, 0],
                "metadata": {},
                "consequence": "not a physical fact",
            }
        ],
        "truncated": False,
    }

    with pytest.raises(SpatialValidationError, match="closed"):
        admit_spatial_result(scene, result)


def test_shared_contract_contains_no_ruleset_specific_vocabulary() -> None:
    root = Path(__file__).parents[1] / "src" / "sylvia_gm" / "spatial"
    source = "\n".join(path.read_text(encoding="utf-8") for path in root.glob("*.py"))
    forbidden = re.compile(
        r"\b(?:firearms?|dnd|d20|armor|hit points?|damage|suppression|hacking)\b",
        re.IGNORECASE,
    )
    assert forbidden.search(source) is None
