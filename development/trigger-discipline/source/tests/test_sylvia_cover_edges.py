"""New TD cover fixtures use shared edge clearance without changing solid-prop occupancy."""

from copy import deepcopy

import pytest
from sylvia_ruleset import builder

from sylvia_gm.spatial import SpatialEngine


def path(scene, start, goal, max_steps=8):
    scene = deepcopy(scene)
    next(row for row in scene["entities"] if row["id"] == "vesper")["position"] = [
        start[0] + 0.5, start[1] + 0.5, 0,
    ]
    return SpatialEngine(scene).query({
        "kind": "path", "map_id": scene["map"]["id"], "start": start, "goal": goal,
        "allow_diagonal": True, "corner_policy": "forbid_cutting", "include_entities": True,
        "moving_entity_id": "vesper", "max_steps": max_steps,
    }).as_payload()


@pytest.mark.parametrize("lane", [5, 6])
def test_authored_td_cover_keeps_both_adjoining_grid_lanes_usable(lane):
    result = path(builder._scene(), [lane, 1], [lane, 3])
    assert result["found"] is True
    assert result["cost"] == 2


def test_authored_cover_blocks_direct_crossing_without_reserving_both_cells():
    assert path(builder._scene(), [5, 2], [6, 2], max_steps=1)["found"] is False


def test_other_solid_props_still_reserve_their_authored_grid_footprint():
    scene = builder._scene()
    prop = deepcopy(scene["geometry"][0])
    prop.update(id="one-cell-crate", occupies_cells=True)
    prop["volume"] = {"kind": "box", "min": [14, 6, 0], "max": [15, 7, 1.2]}
    prop["metadata"]["player"].update(kind="prop", label="Crate")
    scene["geometry"].append(prop)
    assert path(scene, [14, 5], [14, 6])["found"] is False
    assert path(scene, [13, 6], [13, 7])["found"] is True
