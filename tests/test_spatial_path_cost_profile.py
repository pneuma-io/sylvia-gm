"""Optional retained path costs leave legacy path queries unchanged."""

from copy import deepcopy

import pytest

from sylvia_gm.spatial import SpatialEngine, SpatialValidationError, admit_spatial_result
from tests.test_spatial_engine import _path, _scene


@pytest.mark.parametrize("diagonal", [False, True])
@pytest.mark.parametrize("weight", [1, 3])
def test_retained_profile_matches_exact_prefix_costs_and_preserves_legacy(diagonal, weight):
    scene = _scene(
        terrain=[
            {"cell": [x, y], "layer_id": "floor", "cost": weight}
            for x in range(6)
            for y in range(6)
        ]
    )
    before = deepcopy(scene)
    engine = SpatialEngine(scene)
    goal = [2, 2] if diagonal else [0, 2]
    query = _path([0, 0], goal)
    legacy = engine.query(query).as_payload()
    profiled = engine.query({**query, "cost_profile": True}).as_payload()
    assert {k: v for k, v in profiled.items() if k != "cost_profile"} == legacy
    assert "cost_profile" not in engine.query({**query, "cost_profile": False}).as_payload()
    assert scene == before
    assert len(profiled["cost_profile"]) == len(profiled["cells"])
    for cell, row in zip(profiled["cells"], profiled["cost_profile"], strict=True):
        assert row["terrain"] == weight
        assert row["cost"] == pytest.approx(engine.query(_path([0, 0], cell)).as_payload()["cost"])


def test_profile_uses_retained_detour_and_world_distance_without_repricing_edges():
    scene = _scene(terrain=[{"cell": [1, 0], "layer_id": "floor", "cost": 50}])
    scene["map"]["grid"]["cell_size"] = 2
    engine = SpatialEngine(scene)
    result = engine.query({**_path([0, 0], [2, 0]), "cost_profile": True}).as_payload()
    assert [1, 0] not in result["cells"]
    assert result["cost_profile"][-1]["cost"] == result["cost"]
    assert result["cost_profile"][-1]["distance"] == pytest.approx(2 * result["cost"])


@pytest.mark.parametrize("bad", [None, 1, "yes", {}])
def test_profile_query_flag_is_a_closed_boolean(bad):
    with pytest.raises(SpatialValidationError):
        SpatialEngine(_scene()).query({**_path([0, 0], [1, 0]), "cost_profile": bad})


@pytest.mark.parametrize("bad", ["extra", "length", "negative", "nonmonotone", "total"])
def test_profile_result_is_closed_and_consistent(bad):
    engine = SpatialEngine(_scene())
    result = engine.query({**_path([0, 0], [2, 0]), "cost_profile": True}).as_payload()
    if bad == "extra":
        result["cost_profile"][1]["surprise"] = 1
    elif bad == "length":
        result["cost_profile"].pop()
    elif bad == "negative":
        result["cost_profile"][1]["terrain"] = -1
    elif bad == "nonmonotone":
        result["cost_profile"][1]["distance"] = 0
    else:
        result["cost_profile"][-1]["cost"] = 999
    with pytest.raises(SpatialValidationError):
        admit_spatial_result(engine.snapshot, result)


def test_profile_retained_length_is_bounded_without_narrowing_legacy_paths():
    engine = SpatialEngine(_scene(width=300, depth=1))
    query = _path([0, 0], [299, 0], maximum=1000)
    ordinary = engine.query(query).as_payload()
    assert ordinary["found"] and len(ordinary["cells"]) == 300
    with pytest.raises(SpatialValidationError, match="bound"):
        engine.query({**query, "cost_profile": True})
    assert engine.query(query).as_payload() == ordinary


def test_forged_oversized_profile_is_rejected_at_result_admission():
    engine = SpatialEngine(_scene(width=300, depth=1))
    result = engine.query(_path([0, 0], [299, 0], maximum=1000)).as_payload()
    result["cost_profile"] = [
        {"distance": index, "cost": index, "terrain": 1} for index in range(300)
    ]
    with pytest.raises(SpatialValidationError):
        admit_spatial_result(engine.snapshot, result)
