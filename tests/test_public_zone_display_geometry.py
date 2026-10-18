"""Closed display-only geometry admission; no physical or recipient queries execute."""

from copy import deepcopy

import pytest
from test_tactical_grid_ui import _scene

from sylvia_gm.application import tactical_grid
from sylvia_gm.domain.authority import thaw_json_value

CONE = {"kind": "cone", "apex": [7, 3, 3], "direction": [1, 0, 1], "length": 10, "radius": 4}
PRISM = {
    "kind": "prism",
    "origin": [1, 1, 1],
    "direction": [1, 1, 0],
    "up": [0, 0, 2],
    "length": 3,
    "width": 1,
    "height": 1,
}


def _source(shape):
    scene = _scene()
    zone = scene["zones"][0]
    zone["metadata"]["player"]["display_geometry"] = deepcopy(shape)
    zone["metadata"]["private_source_id"] = "not-public-source"
    zone["metadata"]["private_recipients"] = ["not-public-recipient"]
    return scene


@pytest.mark.parametrize("shape", [CONE, PRISM])
def test_public_display_admits_only_shape_without_executing_queries(monkeypatch, shape):
    def forbidden(*args, **kwargs):
        raise AssertionError("Public display must never execute spatial queries")

    monkeypatch.setattr(tactical_grid.SpatialEngine, "query", forbidden)
    scene = _source(shape)
    original = deepcopy(scene)
    public = thaw_json_value(
        tactical_grid.project_coordinate_map({"scene": scene, "enabled": True})
    )
    zone = public["zones"][0]
    assert zone["shape"] == original["zones"][0]["shape"]
    assert zone["display"]["display_geometry"]["kind"] == shape["kind"]
    assert "not-public" not in str(public)
    known = thaw_json_value(tactical_grid.player_known_spatial_scene(scene))
    assert known["zones"][0]["metadata"] == {"player": zone["display"]}
    assert [row["id"] for row in known["zones"]] == ["objective-zone"]
    assert scene == original
    if shape["kind"] == "prism":
        assert zone["display"]["display_geometry"]["up"] == [0.0, 0.0, 1.0]


@pytest.mark.parametrize(
    "bad",
    [
        {**CONE, "recipients": ["private-recipient"]},
        {**CONE, "source_id": "private-source"},
        {**CONE, "direction": [0, 0, 0]},
        {**CONE, "kind": []},
        {**CONE, "radius": float("nan")},
        {**CONE, "apex": [9, 0, 0]},
        {**PRISM, "up": [1, 1, 0]},
        {"kind": "box", "min": [0, 0, 0], "max": [1, 1, 1]},
    ],
)
def test_invalid_public_geometry_fails_closed_and_never_exposes_private_metadata(bad):
    scene = _source(bad)
    # Metadata JSON itself rejects non-finite values before any presentation projection.
    if bad.get("radius") != bad.get("radius"):
        with pytest.raises(tactical_grid.TacticalGridError):
            tactical_grid.project_coordinate_map({"scene": scene, "enabled": True})
        return
    public = thaw_json_value(
        tactical_grid.project_coordinate_map({"scene": scene, "enabled": True})
    )
    assert public["zones"] == []
    known = thaw_json_value(tactical_grid.player_known_spatial_scene(scene))
    assert known["zones"] == []
    assert "not-public" not in str(public)


def test_display_geometry_never_makes_unknown_zone_or_other_object_public():
    scene = _source(CONE)
    private = scene["zones"][1]
    private["metadata"]["display_geometry"] = CONE
    scene["entities"][0]["metadata"]["player"]["display_geometry"] = CONE
    public = thaw_json_value(
        tactical_grid.project_coordinate_map({"scene": scene, "enabled": True})
    )
    assert [row["id"] for row in public["zones"]] == ["objective-zone"]
    assert public["entities"] == []
    assert "hidden-trigger" not in str(public)
