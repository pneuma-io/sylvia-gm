from __future__ import annotations

import pytest

from sylvia_gm.application.shot_effects import project_shot_effect
from sylvia_gm.spatial import SpatialEngine


def _public(label: str) -> dict[str, object]:
    return {"player": {"label": label, "kind": "unit"}}


def test_area_fire_uses_a_visible_zone_without_inventing_a_target_or_impact():
    scene = _scene()
    scene["zones"] = [{
        "id": "lane", "map_id": "range",
        "shape": {"kind": "box", "min": [6, 3, 0], "max": [8, 5, 2]},
        "metadata": _public("Firing lane"),
    }]
    request = {"source_id": "shooter", "target_kind": "zone", "target_id": "lane",
               "origin": [1, 4, 1], "destination": [7, 4, 1], "miss": False,
               "contacts": [], "delay_ms": 100}
    projected = project_shot_effect(request, scene=scene)
    assert projected is not None
    assert projected["destination"] == [7, 4, 1]
    assert projected["contacts"] == []
    assert "target_id" not in projected and "target_kind" not in projected
    assert project_shot_effect({**request, "miss": True}, scene=scene) is None
    scene["zones"][0]["metadata"] = {}
    assert project_shot_effect(request, scene=scene) is None


def _scene(footprint: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "format": 1,
        "map": {
            "id": "range",
            "revision": 1,
            "bounds": {"min": [0, 0, 0], "max": [12, 8, 4]},
            "grid": {"cell_size": 1, "unit": "m", "origin": [0, 0]},
            "layers": [{"id": "floor", "min_elevation": 0, "max_elevation": 4}],
        },
        "geometry": [
            {
                "id": "cover",
                "map_id": "range",
                "volume": {"kind": "box", "min": [5.4, 1, 0], "max": [5.6, 2, 2]},
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": _public("Cover"),
                "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
            },
            {
                "id": "back-wall",
                "map_id": "range",
                "volume": {"kind": "box", "min": [8.58, 0, 0], "max": [8.8, 8, 4]},
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": _public("Wall"),
                "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
            },
            {
                "id": "secret-panel",
                "map_id": "range",
                "volume": {"kind": "box", "min": [6, 1, 0], "max": [6.2, 2, 2]},
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": {"secret": True},
                "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
            },
        ],
        "entities": [
            {
                "id": "shooter",
                "map_id": "range",
                "position": [1, 4, 0],
                "footprint": {"kind": "circle", "radius": 0.35},
                "height": 1.8,
                "facing": [1, 0],
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": _public("Shooter"),
            },
            {
                "id": "target",
                "map_id": "range",
                "position": [8, 4, 0],
                "footprint": footprint or {"kind": "box", "half_extents": [0.5, 0.4]},
                "height": 1.8,
                "facing": [-1, 0],
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": _public("Target"),
            },
            {
                "id": "hidden",
                "map_id": "range",
                "position": [3, 6, 0],
                "footprint": {"kind": "circle", "radius": 0.3},
                "height": 1.8,
                "facing": [1, 0],
                "active": True,
                "movement": "blocking",
                "visibility": "opaque",
                "metadata": {"secret": True},
            },
        ],
        "zones": [],
        "terrain": [],
    }


def _request(**changes: object) -> dict[str, object]:
    request: dict[str, object] = {
        "source_id": "shooter",
        "target_id": "target",
        "origin": [1, 4, 1],
        "destination": [8, 4, 1],
        "delay_ms": 80,
        "miss": False,
        "contacts": [],
    }
    request.update(changes)
    return request


def test_hit_preserves_penetration_and_stop_order_and_ends_at_final_contact() -> None:
    contacts = [
        {"kind": "geometry", "id": "cover", "point": [5.5, 4, 1], "cue": "penetration"},
        {"kind": "entity", "id": "target", "point": [8, 4, 1], "cue": "character"},
        {"kind": "geometry", "id": "back-wall", "point": [8.6, 4, 1], "cue": "stop"},
    ]
    result = project_shot_effect(_request(contacts=contacts), scene=_scene())

    assert result is not None
    assert result["contacts"] == [
        {"point": [5.5, 4, 1], "cue": "penetration"},
        {"point": [8, 4, 1], "cue": "character"},
        {"point": [8.6, 4, 1], "cue": "stop"},
    ]
    assert result["destination"] == [8.6, 4, 1]


@pytest.mark.parametrize(("field", "value"), [("source_id", "hidden"), ("target_id", "hidden")])
def test_hidden_source_or_target_suppresses_the_whole_cue(field: str, value: str) -> None:
    assert project_shot_effect(_request(**{field: value}), scene=_scene()) is None


@pytest.mark.parametrize(
    "contact",
    [
        {"kind": "entity", "id": "hidden", "point": [3, 6, 1], "cue": "character"},
        {"kind": "geometry", "id": "secret-panel", "point": [6.1, 1.5, 1], "cue": "cover"},
    ],
)
def test_hidden_contact_suppresses_the_whole_cue(contact: dict[str, object]) -> None:
    assert project_shot_effect(_request(contacts=[contact]), scene=_scene()) is None


@pytest.mark.parametrize(
    "footprint",
    [{"kind": "box", "half_extents": [0.5, 0.4]}, {"kind": "circle", "radius": 0.45}],
)
def test_miss_passes_beside_target_and_fades_before_wall(footprint: dict[str, object]) -> None:
    result = project_shot_effect(_request(miss=True), scene=_scene(footprint))

    assert result is not None
    endpoint = result["destination"]
    assert endpoint[0] <= 8.58
    lateral_extent = 0.4 if footprint["kind"] == "box" else 0.45
    assert abs(endpoint[1] - 4) > lateral_extent
    assert result["contacts"] == []


def test_close_miss_ray_does_not_cross_target_body() -> None:
    scene = _scene({"kind": "box", "half_extents": [0.4, 0.4]})
    scene["entities"][0]["position"] = [1, 3, 0]
    scene["entities"][1]["position"] = [3, 3, 0]
    result = project_shot_effect(
        _request(origin=[1, 3, 1], destination=[3, 3, 1], miss=True), scene=scene
    )

    assert result is not None
    ray = SpatialEngine(scene).query(
        {
            "kind": "ray",
            "map_id": "range",
            "origin": result["origin"],
            "destination": result["destination"],
            "include": ["entities"],
            "max_intersections": 16,
        }
    )
    assert "target" not in {item["id"] for item in ray.as_payload()["intersections"]}


def test_obstacle_before_target_keeps_a_fading_miss_segment_without_an_impact() -> None:
    scene = _scene()
    scene["geometry"][0]["volume"] = {
        "kind": "box",
        "min": [4, 0, 0],
        "max": [4.2, 8, 4],
    }

    result = project_shot_effect(_request(miss=True), scene=scene)

    assert result is not None
    assert 1 < result["destination"][0] < 4
    assert result["contacts"] == []


def test_visible_bystander_clips_miss_but_source_body_does_not() -> None:
    scene = _scene()
    scene["entities"].append(
        {
            "id": "bystander",
            "map_id": "range",
            "position": [4, 4, 0],
            "footprint": {"kind": "circle", "radius": 0.8},
            "height": 2,
            "facing": [1, 0],
            "active": True,
            "movement": "blocking",
            "visibility": "opaque",
            "metadata": _public("Bystander"),
        }
    )

    result = project_shot_effect(_request(miss=True), scene=scene)

    assert result is not None
    assert 1 < result["destination"][0] < 3.2
    assert result["contacts"] == []


def test_miss_is_suppressed_when_muzzle_starts_inside_visible_geometry() -> None:
    scene = _scene()
    scene["geometry"][0]["volume"] = {
        "kind": "box", "min": [0.9, 3.5, 0], "max": [1.2, 4.5, 2],
    }
    assert project_shot_effect(_request(miss=True), scene=scene) is None


def test_miss_is_suppressed_when_source_overlaps_target_footprint() -> None:
    scene = _scene()
    scene["entities"][1]["position"] = [1.2, 4, 0]
    assert project_shot_effect(_request(destination=[1.2, 4, 1], miss=True), scene=scene) is None


@pytest.mark.parametrize(
    "change",
    [
        {"origin": [float("nan"), 4, 1]},
        {"destination": [99, 4, 1]},
        {"delay_ms": 60_001},
        {"miss": "false"},
        {"contacts": [{}]},
        {
            "contacts": [
                {"kind": "entity", "id": "target", "point": [8, 4, 1], "cue": "character"}
            ],
            "miss": True,
        },
        {"style": {"color": "red"}},
        {"style": {"width": True}},
        {
            "style": {
                "fire_sound": {
                    "waveform": {},
                    "frequency": 120,
                    "end_frequency": 80,
                    "duration_ms": 90,
                    "volume": 0.4,
                }
            }
        },
        {"contacts": [{"kind": "entity", "id": "target", "point": [8, 4, 1], "cue": []}]},
        {"style": None},
        {"unexpected": "value"},
    ],
)
def test_malformed_presentations_are_rejected(change: dict[str, object]) -> None:
    assert project_shot_effect(_request(**change), scene=_scene()) is None


def test_ruleset_style_is_copied_without_metadata_or_extra_fields() -> None:
    sound = {
        "waveform": "triangle",
        "frequency": 120,
        "end_frequency": 80,
        "duration_ms": 90,
        "volume": 0.4,
    }
    style = {
        "color": "#a1b2c3",
        "width": 3,
        "travel_ms": 70,
        "impact_ms": 180,
        "fire_sound": sound,
        "cues": {"penetration": {"color": "#abcdef", "sound": sound}},
    }
    result = project_shot_effect(_request(style=style), scene=_scene())

    assert result is not None
    assert result["style"]["color"] == "#A1B2C3"
    assert result["style"]["cues"]["penetration"] == {"color": "#ABCDEF", "sound": sound}
    assert set(result) == {
        "map_id",
        "origin",
        "destination",
        "delay_ms",
        "miss",
        "contacts",
        "style",
    }
    assert "source_id" not in repr(result) and "target_id" not in repr(result)

    style["cues"]["penetration"]["color"] = "#000000"
    assert result["style"]["cues"]["penetration"]["color"] == "#ABCDEF"
