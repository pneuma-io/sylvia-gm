"""Shooter-origin selection through the generated TD runtime, never a second simulator."""

import importlib.util
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected

path = (
    Path(__file__).resolve().parents[1]
    / "development/trigger-discipline/source/tests/test_shot_reaction_runtime.py"
)
spec = importlib.util.spec_from_file_location("td_shot_harness", path)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
Harness = module.Harness
NS = module.builder.PACKAGE_ID


def lane(game, actor="vesper", **changes):
    body = next(
        e for e in game.state[NS + ".state"]["spatial_scene"]["entities"] if e["id"] == actor
    )
    return [
        {
            "pattern": "lane",
            "direction": "east",
            "extent": 6,
            "half_width": 0,
            "origin": deepcopy(body["position"]),
            "facing": deepcopy(body["facing"]),
            **changes,
        }
    ]


def locate(game, actor, cell):
    values = game.state[NS + ".entities"][actor]
    dx, dy = cell[0] - values["position_cell"][0], cell[1] - values["position_cell"][1]
    for key in ("center", "muzzle"):
        values[key][0] += dx
        values[key][1] += dy
    values["position_cell"] = list(cell)
    body = next(
        e for e in game.state[NS + ".state"]["spatial_scene"]["entities"] if e["id"] == actor
    )
    body["position"][0] += dx
    body["position"][1] += dy


def cross(game, destination):
    game.state[NS + ".state"]["active_side"] = "hostiles"
    return game.execute(
        "resolve_trigger_crossing",
        {
            "actor": "hostile_alpha",
            "destination": destination,
            "overwatcher": "vesper",
            "suppressor": "hex",
            "first_effect": "OVERWATCH",
            "overwatch_critical_effect": "DEEP_HIT",
            "suppression_critical_effect": "DEEP_HIT",
        },
    )


def test_ordinary_map_watch_origin_is_the_shooter():
    game = Harness()
    game.execute(
        "prepare_overwatch_lane",
        {
            "actor": "vesper",
            "lane": [
                {
                    "pattern": "lane",
                    "direction": "east",
                    "extent": 6,
                    "half_width": 0,
                    "origin": [2.5, 2.5, 0],
                    "facing": [1, 0],
                }
            ],
        },
    )
    world = game.state[NS + ".state"]
    watch = world["prepared"]["overwatchers"]["vesper"]
    assert watch["active"] and watch["owner"] == "vesper"
    zone = next(z for z in world["spatial_scene"]["zones"] if z["id"] == watch["zone_id"])
    assert zone["shape"]["kind"] == "grid_cells", (
        "Actual setup still arms a free-floating fixed box"
    )
    assert zone["metadata"]["owner"] == "vesper" and [2, 2] in zone["shape"]["cells"]


@pytest.mark.parametrize("destination,expected", [([5, 2], 1), ([9, 4], 0)])
def test_actual_entry_vs_unrelated_move_and_accounting(destination, expected):
    game = Harness()
    world, actors = game.state[NS + ".state"], game.state[NS + ".entities"]
    world["spatial_scene"]["geometry"] = []
    before = deepcopy(actors["vesper"])
    with patch.object(dice._rng, "randint", return_value=10) as rng:
        game.execute("prepare_overwatch_lane", {"actor": "vesper", "lane": lane(game)})
        assert rng.call_count == 0
        assert (
            actors is not game.state[NS + ".entities"]
        )  # harness applies detached authority mutations
        cross(game, destination)
    after = game.state[NS + ".entities"]["vesper"]
    assert rng.call_count == expected
    assert after["ap"] == before["ap"] - 2
    assert (
        after["weapon"]["magazine"]["rounds"] == before["weapon"]["magazine"]["rounds"] - expected
    )
    assert game.state[NS + ".state"]["prepared"]["overwatchers"]["vesper"]["shots"] == 1 - expected


@pytest.mark.parametrize("x,expected", [(6, 1), (3, 0)])
def test_triangle_runtime_excludes_bounding_rectangle_corner(x, expected):
    game = Harness()
    game.state[NS + ".state"]["spatial_scene"]["geometry"] = []
    locate(game, "hostile_alpha", [x, 5])
    game.execute(
        "prepare_overwatch_lane",
        {"actor": "vesper", "lane": lane(game, pattern="triangle", extent=4, half_width=2)},
    )
    with patch.object(dice._rng, "randint", return_value=10) as rng:
        cross(game, [x, 4])
    assert rng.call_count == expected


@pytest.mark.parametrize(
    "change", ["pose", "facing", "stance", "owner", "watch_owner", "shape", "downed"]
)
def test_stale_armed_lane_never_retargets_or_fires(change):
    game = Harness()
    game.state[NS + ".state"]["spatial_scene"]["geometry"] = []
    game.execute("prepare_overwatch_lane", {"actor": "vesper", "lane": lane(game)})
    world, actors = game.state[NS + ".state"], game.state[NS + ".entities"]
    body = next(e for e in world["spatial_scene"]["entities"] if e["id"] == "vesper")
    zone = next(z for z in world["spatial_scene"]["zones"] if z["shape"]["kind"] == "grid_cells")
    if change == "pose":
        body["position"][0] += 0.1
    elif change == "facing":
        body["facing"] = [0, 1]
    elif change == "stance":
        actors["vesper"]["stance"] = "CROUCHED"
    elif change == "owner":
        zone["metadata"]["owner"] = "hex"
    elif change == "watch_owner":
        world["prepared"]["overwatchers"]["vesper"]["owner"] = "hex"
    elif change == "shape":
        zone["shape"]["cells"] = [[2, 2], [3, 2]]
    elif change == "downed":
        actors["vesper"]["downed"] = True
    with patch.object(dice._rng, "randint", return_value=10) as rng:
        cross(game, [5, 2])
    assert rng.call_count == 0
    assert game.state[NS + ".entities"]["vesper"]["weapon"] == actors["vesper"]["weapon"]


def test_multiple_watchers_cancel_and_legacy_area():
    game = Harness()
    old = deepcopy(game.state[NS + ".state"]["spatial_scene"]["zones"])
    game.execute("prepare_overwatch_lane", {"actor": "vesper", "lane": lane(game)})
    before = deepcopy(game.state)
    with pytest.raises(RulesetPreconditionRejected, match="Only the watcher"):
        game.execute("cancel_overwatch_lane", {"actor": "hex"})
    assert game.state == before
    game.execute("cancel_overwatch_lane", {"actor": "vesper"})
    cancelled = deepcopy(game.state)
    game.execute("cancel_overwatch_lane", {"actor": "vesper"})
    assert game.state == cancelled
    assert game.state[NS + ".entities"]["vesper"]["ap"] == before[NS + ".entities"]["vesper"]["ap"]
    assert game.state[NS + ".state"]["spatial_scene"]["zones"][:3] == old
    game.execute("prepare_overwatch", {"actor": "vesper", "zone_id": "overwatch-lane"})
    assert game.state[NS + ".state"]["prepared"]["overwatch"]["zone_id"] == "overwatch-lane"
    assert game.state[NS + ".state"]["prepared"]["overwatch"]["lane_origin"] is None


def test_watcher_movement_breaks_lane_and_return_does_not_rearm():
    game = Harness()
    game.state[NS + ".state"]["spatial_scene"]["geometry"] = []
    game.execute("prepare_overwatch_lane", {"actor": "vesper", "lane": lane(game)})
    game.execute("move_actor", {"actor": "vesper", "destination": [3, 2]})
    assert game.state[NS + ".state"]["prepared"]["overwatchers"]["vesper"]["active"] is False
    game.execute("move_actor", {"actor": "vesper", "destination": [2, 2]})
    assert game.state[NS + ".state"]["prepared"]["overwatchers"]["vesper"]["active"] is False


def test_same_watcher_can_pay_for_a_new_declaration():
    game = Harness()
    game.execute("prepare_overwatch_lane", {"actor": "vesper", "lane": lane(game)})
    game.execute(
        "prepare_overwatch_lane",
        {"actor": "vesper", "lane": lane(game, direction="south", extent=4)},
    )
    assert game.state[NS + ".entities"]["vesper"]["ap"] == 2
    watch = game.state[NS + ".state"]["prepared"]["overwatchers"]["vesper"]
    zone = next(
        z
        for z in game.state[NS + ".state"]["spatial_scene"]["zones"]
        if z["id"] == watch["zone_id"]
    )
    assert zone["metadata"]["lane"]["direction"] == "south" and [2, 6] in zone["shape"]["cells"]


@pytest.mark.parametrize("height,expected", [(3, 0), (1, 1)])
def test_real_edge_wall_los_and_partial_cover_at_lane_entry(height, expected):
    game = Harness()
    game.state[NS + ".state"]["spatial_scene"]["geometry"] = [
        {
            "id": f"edge-{y}",
            "map_id": "td-conformance-floor",
            "volume": {"kind": "grid_edge", "min": [6, y, 0], "max": [6, y + 1, height]},
            "edge_layer_id": "wall-edge",
            "active": True,
            "movement": "blocking",
            "visibility": "opaque",
            "occupies_cells": False,
            "metadata": {"material_resistance": 3, "cover_hp": 12},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
        for y in range(5)
    ]
    game.execute("prepare_overwatch_lane", {"actor": "vesper", "lane": lane(game)})
    with patch.object(dice._rng, "randint", return_value=10) as rng:
        cross(game, [7, 2])
    assert rng.call_count == expected


def test_repeated_seed_has_identical_runtime_reaction_and_evidence():
    before_rng = dice._rng.getstate()
    try:
        runs = []
        for _ in range(2):
            game = Harness()
            game.state[NS + ".state"]["spatial_scene"]["geometry"] = []
            game.execute("prepare_overwatch_lane", {"actor": "vesper", "lane": lane(game)})
            dice._rng.seed(552)
            result = cross(game, [5, 2])
            runs.append((game.state, result))
        assert runs[0] == runs[1]
    finally:
        dice._rng.setstate(before_rng)


def test_real_stance_change_and_return_cannot_revive_lane():
    game = Harness()
    game.state[NS + ".state"]["spatial_scene"]["geometry"] = []
    game.execute("prepare_overwatch_lane", {"actor": "vesper", "lane": lane(game)})
    game.execute("change_stance", {"actor": "vesper", "stance": "CROUCHED"})
    game.execute("change_stance", {"actor": "vesper", "stance": "STANDING"})
    with patch.object(dice._rng, "randint", return_value=10) as rng:
        cross(game, [5, 2])
    assert rng.call_count == 0


def test_two_shooters_can_arm_without_overwrite_or_extra_costs():
    game = Harness()
    game.execute("prepare_overwatch_lane", {"actor": "vesper", "lane": lane(game)})
    game.execute(
        "prepare_overwatch_lane", {"actor": "hex", "lane": lane(game, "hex", half_width=1)}
    )
    assert game.state[NS + ".entities"]["vesper"]["ap"] == 4
    assert game.state[NS + ".entities"]["hex"]["ap"] == 4
    watches = game.state[NS + ".state"]["prepared"]["overwatchers"]
    assert watches["vesper"]["active"] and watches["hex"]["active"]
    game.execute("cancel_overwatch_lane", {"actor": "vesper"})
    watches = game.state[NS + ".state"]["prepared"]["overwatchers"]
    assert not watches["vesper"]["active"] and watches["hex"]["active"]
    assert game.state[NS + ".entities"]["vesper"]["ap"] == 4


def test_separate_watch_windows_resolve_along_one_priced_route():
    game = Harness()
    game.state[NS + ".state"]["spatial_scene"]["geometry"] = []
    locate(game, "hex", [5, 5])
    game.execute("prepare_overwatch_lane", {"actor": "vesper", "lane": lane(game)})
    game.execute(
        "prepare_overwatch_lane",
        {"actor": "hex", "lane": lane(game, "hex", direction="north", extent=4)},
    )
    game.state[NS + ".state"]["active_side"] = "hostiles"
    before = game.state[NS + ".entities"]["hostile_alpha"]["ap"]
    with patch.object(dice._rng, "randint", return_value=1) as rng:
        game.execute("move_actor", {"actor": "hostile_alpha", "destination": [3, 2]})
    assert rng.call_count == 2
    world = game.state[NS + ".state"]
    assert [r["owner"] for r in world["last_trigger"]["reactions"]] == ["vesper", "hex"]
    import math

    rate = game.state[NS + ".entities"]["hostile_alpha"]["movement_m_per_ap"]
    assert game.state[NS + ".entities"]["hostile_alpha"]["ap"] == before - math.ceil(6 / rate)
    assert world["network_motion"]["hostile_alpha"] == 1


def test_legacy_and_lane_redeclarations_do_not_duplicate_own_allowance():
    game = Harness()
    game.execute("prepare_overwatch", {"actor": "vesper", "zone_id": "overwatch-lane"})
    game.execute("prepare_overwatch_lane", {"actor": "vesper", "lane": lane(game)})
    prepared = game.state[NS + ".state"]["prepared"]
    assert prepared["overwatchers"]["vesper"]["active"] and not prepared["overwatch"]["active"]
    game.execute("prepare_overwatch", {"actor": "vesper", "zone_id": "overwatch-lane"})
    prepared = game.state[NS + ".state"]["prepared"]
    assert prepared["overwatch"]["active"] and not prepared["overwatchers"]["vesper"]["active"]
    assert game.state[NS + ".entities"]["vesper"]["ap"] == 0


def test_configured_lane_counter_prep_uses_existing_eligibility_and_accounting():
    game = Harness()
    world, actors = game.state[NS + ".state"], game.state[NS + ".entities"]
    world["spatial_scene"]["geometry"] = []
    locate(game, "hostile_alpha", [6, 2])
    actors["vesper"].update(
        class_id="SHARPSHOOTER",
        level=6,
        specialties=[
            "specialty.sentinel.entry.prepared_sightline",
            "specialty.sentinel.tier_1.broad_sector",
            "specialty.sentinel.tier_1.stable_nest",
            "specialty.sentinel.tier_2.crossing_shot",
            "specialty.sentinel.tier_2.second_trigger",
            "specialty.sentinel.tier_3.counter_prep",
        ],
    )
    actors["hostile_alpha"].update(hp=1, res=0)
    actors["hostile_alpha"]["weapon"].update(chamber="LIVE", needs_cycle=False)
    game.execute(
        "prepare_overwatch_lane", {"actor": "vesper", "lane": lane(game), "counter_prep": True}
    )
    game.state[NS + ".state"]["active_side"] = "hostiles"
    with patch.object(dice._rng, "randint", return_value=1) as rng:
        game.execute(
            "prepare_overwatch_lane",
            {
                "actor": "hostile_alpha",
                "lane": lane(game, "hostile_alpha", direction="west", extent=3),
            },
        )
    assert rng.call_count == 1
    assert game.state[NS + ".state"]["prepared"]["overwatchers"]["vesper"]["shots"] == 0
    assert game.state[NS + ".entities"]["vesper"]["ap"] == 4


def test_cover_change_requeries_a_previously_occluded_watcher(monkeypatch):
    # One authored triggered effect opens a screen after the real first attack.
    # The second watch was wholly occluded at initial route inspection.
    builder = module.builder
    original = builder._trigger_attack_effects

    def attack(**kwargs):
        effects = original(**kwargs)
        if kwargs["prefix"] == "_shot":
            effects += [
                {
                    "condition": "_shot_ready == true",
                    "on_true": [
                        {
                            "spatial_query": {
                                "scene": "state.spatial_scene",
                                "operation": "snapshot",
                            },
                            "store_as": "_screen_snapshot",
                        },
                        {
                            "spatial_mutation": {
                                "scene": "state.spatial_scene",
                                "map_id": "{map_id}",
                                "expected_revision": "{_screen_snapshot.geometry_revision}",
                                "target": {"kind": "geometry", "id": "screen"},
                                "patch": {"active": False},
                            },
                            "store_as": "_screen_open",
                        },
                    ],
                    "on_false": [],
                }
            ]
        return effects

    monkeypatch.setattr(builder, "_trigger_attack_effects", attack)
    game = Harness()
    world = game.state[NS + ".state"]
    screen = deepcopy(world["spatial_scene"]["geometry"][0])
    screen.update(id="screen", volume={"kind": "box", "min": [2.9, 3.5, 0], "max": [3.2, 5.8, 3]})
    world["spatial_scene"]["geometry"] = [screen]
    locate(game, "hex", [2, 5])
    game.execute("prepare_overwatch_lane", {"actor": "vesper", "lane": lane(game)})
    game.execute(
        "prepare_overwatch_lane", {"actor": "hex", "lane": lane(game, "hex", half_width=3)}
    )
    game.state[NS + ".state"]["active_side"] = "hostiles"
    with patch.object(dice._rng, "randint", return_value=1) as rng:
        game.execute("move_actor", {"actor": "hostile_alpha", "destination": [5, 2]})
    assert rng.call_count == 2
    events = game.state[NS + ".state"]["last_trigger"]["reactions"]
    assert [e["owner"] for e in events] == ["vesper", "hex"]
    assert events[0]["point"] == events[1]["point"]


def test_forced_posture_change_breaks_target_lane_before_return():
    game = Harness()
    game.state[NS + ".state"]["spatial_scene"]["geometry"] = []
    locate(game, "hostile_alpha", [3, 2])
    game.execute("prepare_overwatch_lane", {"actor": "vesper", "lane": lane(game)})
    game.state[NS + ".state"]["active_side"] = "hostiles"
    with patch.object(dice._rng, "randint", return_value=20):
        game.execute(
            "resolve_cqc", {"actor": "hostile_alpha", "target": "vesper", "maneuver": "TRIP"}
        )
    assert game.state[NS + ".entities"]["vesper"]["stance"] == "PRONE"
    assert not game.state[NS + ".state"]["prepared"]["overwatchers"]["vesper"]["active"]
    game.state[NS + ".state"]["active_side"] = "allies"
    game.execute("change_stance", {"actor": "vesper", "stance": "STANDING"})
    assert not game.state[NS + ".state"]["prepared"]["overwatchers"]["vesper"]["active"]


def test_unsupported_field_path_identity_refuses_without_writes():
    game = Harness()
    actors = game.state[NS + ".entities"]
    actors["vesper.alpha"] = actors.pop("vesper")
    actors["vesper.alpha"]["id"] = "vesper.alpha"
    body = next(
        e for e in game.state[NS + ".state"]["spatial_scene"]["entities"] if e["id"] == "vesper"
    )
    body["id"] = "vesper.alpha"
    before = deepcopy(game.state)
    with pytest.raises(RulesetPreconditionRejected, match="identity is unsupported"):
        game.execute(
            "prepare_overwatch_lane", {"actor": "vesper.alpha", "lane": lane(game, "vesper.alpha")}
        )
    assert game.state == before


def test_retained_legacy_allowance_coexists_with_lane_registry():
    game = Harness()
    game.state[NS + ".state"]["spatial_scene"]["geometry"] = []
    game.execute("prepare_overwatch_lane", {"actor": "vesper", "lane": lane(game)})
    game.execute("cancel_overwatch_lane", {"actor": "vesper"})
    actors = game.state[NS + ".entities"]
    actors["vesper"]["specialties"] = ["specialty.sentinel.tier_2.second_trigger"]
    actors["hostile_alpha"].update(hp=100, res=100)
    game.execute("prepare_sentinel_overwatch", {
        "actor": "vesper", "zone_id": "overwatch-lane", "shot_allowance": 2,
    })
    rounds = game.state[NS + ".entities"]["vesper"]["weapon"]["magazine"]["rounds"]
    with patch.object(dice._rng, "randint", return_value=1) as rng:
        cross(game, [7, 0])
        watch = game.state[NS + ".state"]["prepared"]["overwatch"]
        assert watch["shots"] == 1 and watch["active"]
        cross(game, [7, 3])
    watch = game.state[NS + ".state"]["prepared"]["overwatch"]
    assert rng.call_count == 2 and watch["shots"] == 0 and not watch["active"]
    assert game.state[NS + ".entities"]["vesper"]["weapon"]["magazine"]["rounds"] == rounds - 2
