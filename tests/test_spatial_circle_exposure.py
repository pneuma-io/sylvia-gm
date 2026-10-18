"""Physical exposure samples for admitted circular combat bodies."""

import math

import pytest

from sylvia_gm.spatial import SpatialEngine


def scene(*, circle=True, geometry=()):
    return {
        "format": 1,
        "map": {
            "id": "circle-exposure",
            "revision": 0,
            "bounds": {"min": [-50, -50, -50], "max": [50, 50, 50]},
            "grid": {"cell_size": 5, "unit": "ft", "origin": [-50, -50]},
            "layers": [{"id": "world", "min_elevation": -50, "max_elevation": 50}],
        },
        "entities": [
            {
                "id": "recipient-body",
                "map_id": "circle-exposure",
                "position": [0, 0, 0],
                "footprint": {"kind": "circle", "radius": 1}
                if circle
                else {"kind": "box", "half_extents": [1, 1]},
                "height": 6,
                "facing": [1, 0],
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": {},
            }
        ],
        "geometry": list(geometry),
        "zones": [],
        "terrain": [],
    }


def exposure(value, observer=(-10, 0, 3)):
    return (
        SpatialEngine(value)
        .query(
            {
                "kind": "exposure",
                "map_id": "circle-exposure",
                "observer": {"point": observer},
                "target": {"entity_id": "recipient-body"},
                "samples": {"horizontal": 4, "vertical": 4},
                "include": ["geometry"],
            }
        )
        .as_payload()
    )


@pytest.mark.parametrize("observer", [(-10, 0, 3), (0, 10, 3), (-4, -3, 3), (-1.01, 0, 3)])
def test_circle_samples_follow_actual_visible_surface(observer):
    result = exposure(scene(), observer)
    assert len(result["samples"]) == 16
    for sample in result["samples"]:
        x, y, z = sample["point"]
        assert x * x + y * y == pytest.approx(1)
        assert z in (0.75, 2.25, 3.75, 5.25)
        assert x * observer[0] + y * observer[1] >= 1 - 1e-10
        assert sample["visible"] is True
    assert result["exposure"] == 1


@pytest.mark.parametrize("observer", [(0, 0, 3), (0.5, 0, 3), (1, 0, 3)])
def test_inside_footprint_fallback_stays_finite_on_actual_body(observer):
    result = exposure(scene(), observer)
    assert len(result["samples"]) == 16
    assert result["exposure"] == 1
    for sample in result["samples"]:
        x, y, z = sample["point"]
        assert all(math.isfinite(coordinate) for coordinate in (x, y, z))
        assert x * x + y * y == pytest.approx(1)


def test_box_samples_preserve_the_existing_grid_exactly():
    result = exposure(scene(circle=False))
    assert [row["point"] for row in result["samples"]] == [
        [-1, y, z] for z in (0.75, 2.25, 3.75, 5.25) for y in (-0.75, -0.25, 0.25, 0.75)
    ]


def test_thin_blocker_between_old_box_face_and_true_circle_surface_is_seen():
    wall = {
        "id": "small-solid",
        "map_id": "circle-exposure",
        "volume": {"kind": "box", "min": [-0.9, 0.65, 0], "max": [-0.8, 0.85, 6]},
        "active": True,
        "movement": "blocking",
        "visibility": "opaque",
        "metadata": {},
        "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
    }
    result = exposure(scene(geometry=[wall]))
    assert sum(not sample["visible"] for sample in result["samples"]) == 4
    assert result["exposure"] == 0.75
    assert exposure(scene(circle=False, geometry=[wall]))["exposure"] == 1
