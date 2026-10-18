"""Attached public extents never disclose a body rejected by the map's visibility gate."""

from copy import deepcopy

import pytest
from test_tactical_grid_ui import _scene

from sylvia_gm.application import tactical_grid
from sylvia_gm.domain.authority import thaw_json_value


def _anchored_scene():
    scene = _scene()
    scene["zones"][0]["metadata"]["display_anchor"] = "operator"
    return scene


def _zones(scene):
    coordinate = thaw_json_value(
        tactical_grid.project_coordinate_map({"scene": scene, "enabled": True})
    )
    known = thaw_json_value(tactical_grid.player_known_spatial_scene(scene))
    assert [zone["id"] for zone in coordinate["zones"]] == [zone["id"] for zone in known["zones"]]
    return coordinate["zones"], known["zones"]


def test_anchor_uses_current_visible_active_entity_without_exporting_the_reference():
    scene = _anchored_scene()
    original = deepcopy(scene)
    public, known = _zones(scene)
    assert [zone["id"] for zone in public] == ["objective-zone"]
    assert public[0]["shape"] == original["zones"][0]["shape"]
    assert public[0]["display"] == {"label": "Extraction", "kind": "objective"}
    assert known[0]["metadata"] == {"player": public[0]["display"]}
    assert "display_anchor" not in str((public, known))
    assert "operator" not in str((public, known))
    assert scene == original


@pytest.mark.parametrize(
    "change",
    [
        "omitted",
        "inactive",
        "unknown",
        "private-key",
        "whitespace",
        "unicode-bytes",
        "optional-type",
        "interactive-type",
        "other-map",
        "wrong-type",
    ],
)
def test_extent_fails_closed_for_an_anchor_hidden_by_the_existing_entity_contract(change):
    scene = _anchored_scene()
    actor = scene["entities"][0]
    if change == "omitted":
        scene["entities"].pop(0)
    elif change == "inactive":
        actor["active"] = False
    elif change == "unknown":
        actor["metadata"].pop("player")
    elif change == "private-key":
        actor["metadata"]["player"]["gm_only"] = "private"
    elif change == "whitespace":
        actor["metadata"]["player"]["label"] = " Operator "
    elif change == "unicode-bytes":
        actor["metadata"]["player"]["label"] = "🙂" * 65
    elif change == "optional-type":
        actor["metadata"]["player"]["state"] = ["concealed"]
    elif change == "interactive-type":
        actor["metadata"]["player"]["interactive"] = "true"
    elif change == "other-map":
        scene["zones"][0]["metadata"]["display_anchor"] = "other-map-actor"
    else:
        scene["zones"][0]["metadata"]["display_anchor"] = ["operator"]
    original = deepcopy(scene)
    assert _zones(scene) == ([], [])
    assert scene == original


def test_static_extent_without_anchor_keeps_the_existing_public_contract():
    scene = _anchored_scene()
    scene["zones"][0]["metadata"]["display_anchor"] = None
    scene["entities"] = []
    assert len(_zones(scene)[0]) == 1
