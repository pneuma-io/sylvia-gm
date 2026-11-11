"""The generic locator finds visibility intervals without mutating physical truth."""

import json
from pathlib import Path
from copy import deepcopy
from unittest.mock import patch

import pytest

from sylvia_gm.spatial import SpatialEngine, SpatialValidationError
NS = "pneuma-io.trigger-discipline"
FIXTURE = Path(__file__).parent / "fixtures/td-exposure-ownership-v47.json"


def scene(wall_end=6.05):
    state = json.loads(FIXTURE.read_bytes())["states"]["false-empty"]
    value = state[NS + ".state"]["spatial_scene"]
    value["geometry"][0]["volume"]["max"][1] = wall_end
    return value, state[NS + ".entities"]["A_VESPER_4"]["muzzle"]


def query(value, observer, **changes):
    return {
        "kind": "exposure_path",
        "map_id": value["map"]["id"],
        "observer": {"point": observer},
        "target": {"entity_id": "B_VESPER_1"},
        "samples": {"horizontal": 3, "vertical": 3},
        "include": ["geometry", "entities"],
        "path": [[10.5, 5.5, 0.9], [10.5, 6.5, 0.9], [10.5, 7.5, 0.9]],
        "zone_id": "declared-prepared-lane",
        "max_tests": 4096,
        **changes,
    }


def at(value, observer, point):
    trial = deepcopy(value)
    body = next(row for row in trial["entities"] if row["id"] == "B_VESPER_1")
    body["position"] = [point[0], point[1], point[2] - body["height"] / 2]
    request = query(value, observer)
    for key in ("path", "zone_id", "max_tests"):
        request.pop(key)
    request["kind"] = "exposure"
    return SpatialEngine(trial).query(request).as_payload()["exposure"]


def test_first_exposed_point_is_verified_and_input_snapshot_unchanged():
    value, observer = scene()
    before = deepcopy(value)
    engine = SpatialEngine(value)
    snapshot = engine.snapshot
    result = engine.query(query(value, observer)).as_payload()
    assert result["supported"] and result["found"]
    point = result["point"]
    assert 6.2 < point[1] < 7.5
    assert at(value, observer, point) == result["exposure"] > 0
    assert at(value, observer, [point[0], point[1] - 1e-7, point[2]]) == 0
    assert result["distance"] == pytest.approx(point[1] - 5.5)
    assert value == before and engine.snapshot == snapshot
    assert engine.query(query(value, observer)).as_payload() == result


def test_visible_window_between_concealed_endpoints_is_not_skipped():
    value, observer = scene()
    second = deepcopy(value["geometry"][0])
    second["id"] = "second-edge-wall"
    second["volume"]["min"][1] = 6.15
    second["volume"]["max"][1] = 10
    value["geometry"].append(second)
    assert at(value, observer, [10.5, 6.2, 0.9]) == 0
    assert at(value, observer, [10.5, 7.5, 0.9]) == 0
    result = SpatialEngine(value).query(query(value, observer)).as_payload()
    assert result["found"] and result["supported"]
    assert at(value, observer, result["point"]) > 0


def test_unrelated_curved_body_outside_the_ray_envelope_does_not_block_query_support():
    value, observer = scene()
    body = deepcopy(value["entities"][0])
    body.update(
        id="unrelated-circle", position=[30, 30, 0], footprint={"kind": "circle", "radius": 0.4}
    )
    value["entities"].append(body)
    result = SpatialEngine(value).query(query(value, observer)).as_payload()
    assert result["supported"] and result["found"]


def test_budget_also_bounds_visibility_event_construction():
    from sylvia_gm.spatial import _exposure_path

    value, observer = scene()
    with patch.object(_exposure_path, "_root", wraps=_exposure_path._root) as root:
        result = SpatialEngine(value).query(query(value, observer, max_tests=1)).as_payload()
    assert not result["supported"] and result["reason"] == "TEST_LIMIT"
    assert root.call_count <= 50


@pytest.mark.parametrize("wall_end,found", [(5.71, True), (20, False)])
def test_open_and_fully_concealed_controls(wall_end, found):
    value, observer = scene(wall_end)
    result = SpatialEngine(value).query(query(value, observer)).as_payload()
    assert result["supported"] and result["found"] is found
    if found:
        assert result["point"][1] == pytest.approx(6.2)
    else:
        assert result["point"] is None and result["distance"] is None


@pytest.mark.parametrize("reason", ["shape", "limit"])
def test_unsupported_or_exhausted_query_never_claims_a_safe_route(reason):
    value, observer = scene()
    changes = {}
    if reason == "shape":
        body = next(row for row in value["entities"] if row["id"] == "B_VESPER_1")
        body["footprint"] = {"kind": "circle", "radius": 0.4}
    else:
        changes["max_tests"] = 1
    result = SpatialEngine(value).query(query(value, observer, **changes)).as_payload()
    assert not result["supported"] and not result["found"]
    assert result["point"] is None and result["distance"] is None


@pytest.mark.parametrize(
    "changes",
    [
        {"zone_id": "unknown"},
        {"max_tests": 0},
        {"map_id": "another"},
        {"path": [[10, 5, 0]]},
        {"unknown": True},
    ],
)
def test_closed_query_validation(changes):
    value, observer = scene()
    with pytest.raises(SpatialValidationError):
        SpatialEngine(value).query(query(value, observer, **changes))
