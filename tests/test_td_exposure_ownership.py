"""Authored suppression exposure events through exact generated package procedures."""

import json
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_player_action_previewer
from tests.td_source_fixture import candidate as candidate_fixture
from tests.test_td_suppression_aim import PackageProcedures

candidate = candidate_fixture

NS = "pneuma-io.trigger-discipline"
FIXTURE = Path(__file__).parent / "fixtures/td-exposure-ownership-v47.json"


def game_for(archive, mirrored=False, condition="empty", wall_end=None):
    game = PackageProcedures(archive)
    game.state = deepcopy(
        json.loads(FIXTURE.read_bytes())["states"][f"{str(mirrored).lower()}-{condition}"]
    )
    if wall_end is not None:
        game.world["spatial_scene"]["geometry"][0]["volume"]["max"][1] = wall_end
    game.shooter = "B_VESPER_4" if mirrored else "A_VESPER_4"
    game.mover = "A_VESPER_1" if mirrored else "B_VESPER_1"
    return game


def setup(game):
    return game.execute(
        "establish_suppression",
        {
            "actor": game.shooter,
            "zone_id": "declared-prepared-lane",
            "rounds": 20,
        },
    )


def cross(game):
    side = game.actors[game.shooter]["side"]
    game.execute("end_allies_turn" if side == "allies" else "end_hostiles_turn", {})
    return game.execute(
        "resolve_trigger_crossing",
        {
            "actor": game.mover,
            "destination": [10, 7],
            "overwatcher": game.shooter,
            "suppressor": game.shooter,
            "first_effect": "SUPPRESSION",
            "overwatch_critical_effect": "DEEP_HIT",
            "suppression_critical_effect": "DEEP_HIT",
        },
    )


@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize("condition", ["visible", "empty", "protected"])
def test_initial_occupant_dispatches_only_actual_exposure(candidate, mirrored, condition):  # noqa: F811
    game = game_for(candidate, mirrored, condition)
    before = deepcopy(game.actors[game.shooter])
    with patch.object(dice._rng, "randint", return_value=1) as draw:
        setup(game)
    assert draw.call_count == (1 if condition == "visible" else 0)
    prepared = game.world["prepared"]["suppression"]
    assert prepared["capacity"] == (1 if condition == "visible" else 2)
    actor = game.actors[game.shooter]
    assert actor["ap"] == before["ap"] - 2
    assert actor["weapon"]["magazine"]["rounds"] == before["weapon"]["magazine"]["rounds"] - 20


@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize("tool", ["aim", "fire_single"])
def test_new_exposed_action_dispatches_remaining_fire(candidate, mirrored, tool):  # noqa: F811
    game = game_for(candidate, mirrored)
    setup(game)
    with patch.object(dice._rng, "randint", return_value=1):
        cross(game)
    assert game.world["prepared"]["suppression"]["capacity"] == 1
    before = deepcopy(game.actors[game.shooter])
    mover = deepcopy(game.actors[game.mover])
    args = {
        "attacker": game.mover,
        "target": game.shooter,
        "aim_point": game.actors[game.shooter]["center"],
    }
    args.update({"ap": 1} if tool == "aim" else {"critical_effect": "DEEP_HIT"})
    with patch.object(dice._rng, "randint", return_value=1) as draw:
        game.execute(tool, args)
    assert draw.call_count == (1 if tool == "aim" else 2)
    assert game.world["prepared"]["suppression"]["capacity"] == 0
    assert game.actors[game.shooter]["weapon"] == before["weapon"]
    assert game.actors[game.shooter]["ap"] == before["ap"]
    assert game.actors[game.mover]["ap"] == mover["ap"] - (1 if tool == "aim" else 2)


@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize("wall_end", [5.71, 6.05, 20])
def test_move_finds_first_valid_exposure_once(candidate, mirrored, wall_end):  # noqa: F811
    game = game_for(candidate, mirrored, wall_end=wall_end)
    setup(game)
    before = deepcopy(game.actors[game.shooter])
    with patch.object(dice._rng, "randint", return_value=1) as draw:
        cross(game)
    assert draw.call_count == (0 if wall_end == 20 else 1)
    assert game.world["prepared"]["suppression"]["capacity"] == (2 if wall_end == 20 else 1)
    assert game.actors[game.shooter]["weapon"] == before["weapon"]
    assert game.actors[game.mover]["position_cell"] == [10, 7]


@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize("invalid", ["downed", "inactive", "depleted", "ended", "weapon", "no_ap"])
def test_unavailable_fire_or_rejected_action_never_dispatches(candidate, mirrored, invalid):  # noqa: F811
    game = game_for(candidate, mirrored)
    setup(game)
    with patch.object(dice._rng, "randint", return_value=1):
        cross(game)
    if invalid == "downed":
        game.actors[game.shooter].update(downed=True, hp=0)
    elif invalid == "inactive":
        game.actors[game.shooter]["active"] = False
    elif invalid == "depleted":
        game.world["prepared"]["suppression"].update(active=False, capacity=0)
    elif invalid == "ended":
        game.world["prepared"]["suppression"]["active"] = False
    elif invalid == "weapon":
        game.actors[game.shooter]["weapon"]["location"] = "SLUNG"
    else:
        game.actors[game.mover]["ap"] = 0
    before = deepcopy(game.actors[game.shooter])
    capacity = game.world["prepared"]["suppression"]["capacity"]
    with patch.object(dice._rng, "randint", return_value=1) as draw:
        game.execute(
            "aim",
            {
                "attacker": game.mover,
                "target": game.shooter,
                "ap": 1,
                "aim_point": game.actors[game.shooter]["center"],
            },
        )
    assert draw.call_count == 0
    assert game.actors[game.shooter] == before
    assert game.world["prepared"]["suppression"]["capacity"] == capacity


@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize("tool", ["aim", "fire_single"])
def test_incoming_incapacitation_stops_outgoing_action_without_its_cost(candidate, mirrored, tool):  # noqa: F811
    game = game_for(candidate, mirrored)
    setup(game)
    with patch.object(dice._rng, "randint", return_value=1):
        cross(game)
    # Explicit diagnostic injury input, not a starting pool, healing or damage rule.
    game.actors[game.mover].update(hp=1, res=0)
    before = deepcopy(game.actors[game.mover])
    weapon = deepcopy(game.actors[game.shooter]["weapon"])
    args = {
        "attacker": game.mover,
        "target": game.shooter,
        "aim_point": game.actors[game.shooter]["center"],
    }
    args.update({"ap": 1} if tool == "aim" else {"critical_effect": "DEEP_HIT"})
    with patch.object(dice._rng, "randint", return_value=15) as draw:
        game.execute(tool, args)
    after = game.actors[game.mover]
    assert draw.call_count == 1
    assert after["downed"] and after["hp"] == 0
    assert after["ap"] == before["ap"] and after["weapon"] == before["weapon"]
    assert game.actors[game.shooter]["weapon"] == weapon
    assert game.world["last_attack"]["outcome"] == "INTERRUPTED_BY_SUPPRESSION"


@pytest.mark.parametrize("mirrored", [False, True])
@pytest.mark.parametrize("tool", ["fire_burst", "fire_full_auto"])
def test_other_supported_fire_modes_dispatch_once_and_keep_real_preview_cost(
    candidate,
    mirrored,
    tool,  # noqa: F811
):
    game = game_for(candidate, mirrored)
    setup(game)
    with patch.object(dice._rng, "randint", return_value=1):
        cross(game)
    args = {
        "attacker": game.mover,
        "target": game.shooter,
        "aim_point": game.actors[game.shooter]["center"],
        "critical_effect": "DEEP_HIT",
    }
    if tool == "fire_full_auto":
        args["rounds"] = 3
    declaration = next(row for row in game.manifest.tools if row.name == tool)
    previewer = materialize_player_action_previewer(
        package_id=NS,
        declaration=declaration,
        runtime_bytes=game.runtime,
    )
    basis = deepcopy(game.state)
    preview = previewer(args, game.state, {})
    assert game.state == basis
    cost = next(row["value"] for row in preview["preview"]["summary"] if row["label"] == "AP cost")
    with patch.object(dice._rng, "randint", return_value=1) as draw:
        game.execute(tool, args)
    assert draw.call_count == 2
    assert game.world["prepared"]["suppression"]["capacity"] == 0
    before = basis[NS + ".entities"][game.mover]
    assert game.actors[game.mover]["ap"] == before["ap"] - cost
    assert game.actors[game.mover]["weapon"]["magazine"]["rounds"] == (
        before["weapon"]["magazine"]["rounds"] - 3
    )


@pytest.mark.parametrize("shape", ["target", "occluder"])
def test_unsupported_path_projection_does_not_commit_movement_or_spend(shape, candidate):
    game = game_for(candidate)
    scene = game.world["spatial_scene"]
    if shape == "target":
        next(row for row in scene["entities"] if row["id"] == game.mover)["footprint"] = {
            "kind": "circle",
            "radius": 0.4,
        }
    else:
        body = deepcopy(scene["entities"][0])
        body.update(
            id="circular-occluder",
            position=[8, 6, 0],
            height=3,
            footprint={"kind": "circle", "radius": 0.05},
        )
        scene["entities"].append(body)
    setup(game)
    game.execute("end_allies_turn", {})
    before = deepcopy(game.actors)
    geometry = deepcopy(game.world["spatial_scene"])
    with patch.object(dice._rng, "randint", return_value=1) as draw:
        game.execute(
            "resolve_trigger_crossing",
            {
                "actor": game.mover,
                "destination": [10, 7],
                "overwatcher": game.shooter,
                "suppressor": game.shooter,
                "first_effect": "SUPPRESSION",
                "overwatch_critical_effect": "DEEP_HIT",
                "suppression_critical_effect": "DEEP_HIT",
            },
        )
    assert draw.call_count == 0 and game.actors == before
    assert game.world["spatial_scene"] == geometry
    assert game.world["prepared"]["suppression"]["capacity"] == 2
    assert game.world["last_trigger"]["status"] == "UNSUPPORTED_EXPOSURE_PATH_OR_SEPARATE_WINDOWS"


@pytest.mark.parametrize("mirrored", [False, True])
def test_downed_source_cannot_start_new_suppression(candidate, mirrored):
    game = game_for(candidate, mirrored, "visible")
    game.actors[game.shooter].update(downed=True, hp=0)
    before = deepcopy(game.actors[game.shooter])
    prepared = deepcopy(game.world["prepared"])
    with patch.object(dice._rng, "randint", return_value=1) as draw:
        setup(game)
    assert draw.call_count == 0
    assert game.actors[game.shooter] == before
    assert game.world["prepared"] == prepared
    assert game.world["last_trigger"]["status"] == "PREPARATION_REJECTED"
