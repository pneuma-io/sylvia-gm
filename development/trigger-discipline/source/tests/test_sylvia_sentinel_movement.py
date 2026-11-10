"""All successful TD movement paths must retire a stationary prepared sightline."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from sylvia_gm import dice
from tests import test_sylvia_sentinel as support

NS = "pneuma-io.trigger-discipline"


@pytest.fixture
def game():
    game = support.game.__wrapped__()
    game.state[NS + ".entities"]["vesper"]["specialties"].append(
        "specialty.sentinel.entry.prepared_sightline",
    )
    support.sightline(game, shot_allowance=2)
    return game


def reaction_scene(game):
    """An enemy's active suppression crosses the watcher's short actual movement path."""
    actors = game.state[NS + ".entities"]
    # Explicit technical survivability, not starting stats.
    actors["vesper"].update(hp=100, res=100)
    actors["hostile_alpha"]["weapon"] = deepcopy(actors["hex"]["weapon"])
    zone = next(row for row in game.state[NS + ".state"]["spatial_scene"]["zones"]
                if row["id"] == "suppression-lane")
    zone["shape"] = {"kind": "box", "min": [2.8, 1, 0], "max": [3.1, 4, 2.5]}
    game.execute("end_allies_turn", {})
    game.execute("establish_suppression", {
        "actor": "hostile_alpha", "zone_id": "suppression-lane", "rounds": 20,
    })
    game.execute("end_hostiles_turn", {})


def crossing(game, destination):
    return game.execute("resolve_trigger_crossing", {
        "actor": "vesper", "overwatcher": "vesper", "suppressor": "hostile_alpha",
        "destination": destination, "first_effect": "SUPPRESSION",
        "overwatch_critical_effect": "DEEP_HIT",
        "suppression_critical_effect": "DEVASTATING_HIT",
    })


@pytest.mark.parametrize("path", ["forced", "reaction"])
def test_away_and_back_never_revives_stationary_watch_or_aim(game, path):
    if path == "forced":
        game.state[NS + ".entities"]["hex"]["skills"]["cqc"] = 100
    else:
        reaction_scene(game)
    own_weapon = deepcopy(game.state[NS + ".entities"]["vesper"]["weapon"])
    for destination in ([3, 2], [2, 2]):
        if path == "forced":
            game.execute("resolve_cqc_displacement", {
                "actor": "hex", "target": "vesper", "maneuver": "SHOVE",
                "destination": destination,
            })
            assert game.state[NS + ".state"]["last_cqc"]["success"]
        else:
            crossing(game, destination)
            assert game.state[NS + ".state"]["last_trigger"]["status"] == "RESUMED"
        assert game.state[NS + ".entities"]["vesper"]["position_cell"] == destination
    watch = game.state[NS + ".state"]["prepared"]["overwatch"]
    assert watch["active"] is False
    assert watch["shots"] == 0
    assert game.state[NS + ".entities"]["vesper"]["aim"] == {
        "ap": 0, "target": None, "point": None,
    }
    assert game.state[NS + ".entities"]["vesper"]["weapon"] == own_weapon


@pytest.mark.parametrize("path", ["forced", "reaction"])
@pytest.mark.parametrize("reason", ["no_op", "blocked", "no_ap"])
def test_rejected_or_stationary_movement_preserves_watch_and_aim(game, path, reason):
    if path == "reaction":
        reaction_scene(game)
    if reason == "no_ap":
        game.state[NS + ".entities"]["hex" if path == "forced" else "vesper"]["ap"] = 0
    destination = {"no_op": [2, 2], "blocked": [2, 3], "no_ap": [3, 2]}[reason]
    before = deepcopy(game.state[NS + ".entities"]["vesper"])
    prepared = deepcopy(game.state[NS + ".state"]["prepared"])
    if path == "forced":
        game.execute("resolve_cqc_displacement", {
            "actor": "hex", "target": "vesper", "maneuver": "SHOVE",
            "destination": destination,
        })
    else:
        crossing(game, destination)
    assert game.state[NS + ".entities"]["vesper"] == before
    assert game.state[NS + ".state"]["prepared"] == prepared


def test_invalidation_during_reaction_also_retires_the_cached_watch_readiness(game):
    reaction_scene(game)
    zones = game.state[NS + ".state"]["spatial_scene"]["zones"]
    watched = next(row for row in zones if row["id"] == "overwatch-lane")
    watched["shape"] = deepcopy(next(row for row in zones
                                    if row["id"] == "suppression-lane")["shape"])
    before = deepcopy(game.state[NS + ".entities"]["vesper"]["weapon"])
    crossing(game, [3, 2])
    assert game.state[NS + ".entities"]["vesper"]["weapon"] == before
    watch = game.state[NS + ".state"]["prepared"]["overwatch"]
    assert not watch["active"] and watch["shots"] == 0


def test_failed_opposed_displacement_keeps_stationary_preparation(game):
    game.state[NS + ".entities"]["hex"]["skills"]["cqc"] = -100
    before = deepcopy(game.state[NS + ".entities"]["vesper"])
    prepared = deepcopy(game.state[NS + ".state"]["prepared"])
    game.execute("resolve_cqc_displacement", {
        "actor": "hex", "target": "vesper", "maneuver": "SHOVE", "destination": [3, 2],
    })
    assert not game.state[NS + ".state"]["last_cqc"]["success"]
    assert game.state[NS + ".entities"]["vesper"] == before
    assert game.state[NS + ".state"]["prepared"] == prepared


def test_another_actors_successful_move_keeps_the_stationary_watch(game):
    before = deepcopy(game.state[NS + ".entities"]["vesper"])
    prepared = deepcopy(game.state[NS + ".state"]["prepared"])
    game.execute("move_actor", {"actor": "hex", "destination": [3, 3]})
    assert game.state[NS + ".entities"]["hex"]["position_cell"] == [3, 3]
    assert game.state[NS + ".entities"]["vesper"] == before
    assert game.state[NS + ".state"]["prepared"] == prepared


def test_interruption_at_subcell_crossing_cancels_watch_before_any_resume(game):
    reaction_scene(game)
    game.state[NS + ".entities"]["vesper"].update(hp=1, res=0)
    game.state[NS + ".entities"]["hostile_alpha"]["skills"]["marksmanship"] = 100
    with patch.object(dice._rng, "randint", return_value=10):
        crossing(game, [3, 2])
    world = game.state[NS + ".state"]
    assert world["last_trigger"]["status"] == "INTERRUPTED"
    # The physical body moved within its original cell; there was no resume movement.
    assert game.state[NS + ".entities"]["vesper"]["position_cell"] == [2, 2]
    body = next(row for row in world["spatial_scene"]["entities"] if row["id"] == "vesper")
    assert body["position"][0] == pytest.approx(2.8)
    assert not world["prepared"]["overwatch"]["active"]
    assert world["prepared"]["overwatch"]["shots"] == 0
    assert game.state[NS + ".entities"]["vesper"]["aim"]["ap"] == 0
