"""Ground pickup moves one real firearm; AP costs are explicit synthetic adjudications."""

import json
from copy import deepcopy
from unittest.mock import patch

import pytest

from sylvia_ruleset import builder
from tests.test_sylvia_weapon_handling import (
    compiled_runtime as compiled_runtime,  # noqa: PLC0414
)
from tests.test_sylvia_weapon_handling import game as game  # noqa: PLC0414
from tests.test_sylvia_weapon_handling import parts
from tests.test_sylvia_weapon_switching import armed as armed  # noqa: PLC0414


def drop(game):
    actor, _ = parts(game)
    return game.execute("drop_weapon", {
        "actor": "vesper", "weapon_id": actor["weapon"]["id"], "ap_cost": 1,
    })


def pickup(game, **changes):
    return game.execute("pickup_weapon", {
        "actor": "vesper", "ground_id": "weapon_1", "weapon_id": "vespers-rifle",
        "location": "READY", "ap_cost": 1, **changes,
    })


@pytest.fixture
def dropped(armed):
    actor, _ = parts(armed)
    actor["weapon"].update(id="vespers-rifle", owner_id="vesper", current_recoil=2)
    drop(armed)
    return armed


def test_drop_and_pickup_preserve_the_complete_object_across_reopen(armed):
    actor, state = parts(armed)
    actor["weapon"].update(id="vespers-rifle", owner_id="vesper", current_recoil=2)
    before = deepcopy(actor)
    actor["aim"]["ap"] = 2
    state["prepared"]["overwatch"].update(owner="vesper", active=True)
    drop(armed)
    armed.state = json.loads(json.dumps(armed.state))
    actor, state = parts(armed)
    ground = state["ground_objects"]["weapon_1"]
    assert ground == {
        "kind": "WEAPON", "map_id": state["spatial_scene"]["map"]["id"],
        "position_cell": before["position_cell"], "dropped_by": "vesper",
        "weapon": {**before["weapon"], "location": "GROUND"},
    }
    assert actor["weapon"] is None
    assert actor["reserve_weapons"] == before["reserve_weapons"]
    assert not actor["aim"]["ap"]
    assert not state["prepared"]["overwatch"]["active"]
    pickup(armed)
    actor, state = parts(armed)
    assert actor["weapon"] == before["weapon"]
    assert actor["ap"] == before["ap"] - 2
    assert state["ground_objects"]["weapon_1"] is None
    assert state["last_equipment"]["status"] == "PICKED_UP"
    before = deepcopy(armed.state)
    pickup(armed)
    assert parts(armed)[0] == before[f"{builder.PACKAGE_ID}.entities"]["vesper"]
    assert parts(armed)[1]["last_equipment"]["status"] == "PICKUP_REJECTED"


def test_another_actor_can_pick_up_store_switch_and_fire_the_same_weapon(dropped):
    from sylvia_gm import dice

    entities = dropped.state[f"{builder.PACKAGE_ID}.entities"]
    entities["hex"]["weapon"]["location"] = "STOWED"
    before = deepcopy(parts(dropped)[1]["ground_objects"]["weapon_1"]["weapon"])
    pickup(dropped, actor="hex", location="QUICK_ACCESS")
    dropped.state = json.loads(json.dumps(dropped.state))
    entities = dropped.state[f"{builder.PACKAGE_ID}.entities"]
    assert entities["vesper"]["weapon"] is None
    assert entities["hex"]["reserve_weapons"] == [{**before, "location": "QUICK_ACCESS"}]
    dropped.execute("switch_weapon", {"actor": "hex", "current_weapon_id": "service_rifle",
        "next_weapon_id": "vespers-rifle", "store_location": "STOWED", "ap_cost": 1})
    with patch.object(dice._rng, "randint", return_value=20):
        dropped.execute("fire_single", {"attacker": "hex", "target": "hostile_alpha",
            "aim_point": [9.5, 2.5, 0.9], "critical_effect": "DEVASTATING_HIT"})
    weapon = dropped.state[f"{builder.PACKAGE_ID}.entities"]["hex"]["weapon"]
    assert weapon["owner_id"] == "vesper"
    assert weapon["magazine"]["rounds"] == before["magazine"]["rounds"] - 1
    assert parts(dropped)[1]["ground_objects"]["weapon_1"] is None


@pytest.mark.parametrize("fault", ["far", "barrier", "wrong_map", "stale", "wrong_weapon",
    "occupied", "duplicate", "no_ap", "downed", "other_side", "unsafe_id"])
def test_rejected_pickup_preserves_equipment_and_ap(dropped, fault):
    actor, state = parts(dropped)
    args = {}
    if fault == "far":
        actor["position_cell"] = [10, 10]
    elif fault == "barrier":
        actor["position_cell"] = [2, 3]
        wall = deepcopy(state["spatial_scene"]["geometry"][1])
        wall.update(id="pickup-wall", volume={"kind": "box", "min": [1, 3, 0], "max": [4, 3.1, 3]})
        state["spatial_scene"]["geometry"].append(wall)
    elif fault == "wrong_map":
        state["ground_objects"]["weapon_1"]["map_id"] = "elsewhere"
    elif fault == "stale":
        args["ground_id"] = "already-taken"
    elif fault == "wrong_weapon":
        args["weapon_id"] = "not-that-rifle"
    elif fault == "occupied":
        actor["weapon"] = deepcopy(actor["reserve_weapons"][0])
        actor["weapon"]["location"] = "READY"
        actor["reserve_weapons"] = []
    elif fault == "duplicate":
        actor["reserve_weapons"][0]["id"] = "vespers-rifle"
    elif fault == "no_ap":
        actor["ap"] = 0
    elif fault == "downed":
        actor["downed"] = True
    elif fault == "other_side":
        state["active_side"] = "hostiles"
    else:
        args["ground_id"] = "weapon_1.weapon"
    before = deepcopy(actor), deepcopy(state["ground_objects"])
    pickup(dropped, **args)
    actor, state = parts(dropped)
    assert (actor, state["ground_objects"]) == before
    assert state.get("last_equipment", {}).get("status") != "PICKED_UP"


def test_an_unarmed_actor_can_switch_to_a_reserve_and_hand_it_to_an_unarmed_recipient(dropped):
    dropped.execute("switch_weapon", {"actor": "vesper", "next_weapon_id": "sidearm-1",
        "store_location": "STOWED", "ap_cost": 1})
    actor, state = parts(dropped)
    assert actor["weapon"]["id"] == "sidearm-1"
    assert actor["reserve_weapons"] == []
    assert state["last_equipment"]["stored_weapon"] is None
    # The rifle remains on the ground; changing the current slot cannot duplicate it.
    assert state["ground_objects"]["weapon_1"]["weapon"]["id"] == "vespers-rifle"
    dropped.execute("handle_weapon", {"actor": "vesper", "weapon_id": "sidearm-1",
        "location": "STOWED", "ap_cost": 0})
    pickup(dropped, location="QUICK_ACCESS")
    dropped.execute("drop_weapon", {"actor": "hex", "weapon_id": "service_rifle", "ap_cost": 0})
    dropped.execute("transfer_reserve_weapon", {"actor": "vesper", "recipient": "hex",
        "weapon_id": "vespers-rifle", "ap_cost": 1})
    entities = dropped.state[f"{builder.PACKAGE_ID}.entities"]
    assert entities["hex"]["weapon"] is None
    assert entities["hex"]["reserve_weapons"][0]["id"] == "vespers-rifle"
    assert entities["vesper"]["reserve_weapons"] == []


def test_disarm_leaves_the_whole_weapon_for_a_separate_pickup(game):
    from sylvia_gm import dice

    entities = game.state[f"{builder.PACKAGE_ID}.entities"]
    before = deepcopy(entities["hex"])
    with patch.object(dice._rng, "randint", return_value=20):
        game.execute("resolve_cqc", {"actor": "vesper", "target": "hex",
            "maneuver": "DISARM", "held_item": "PRIMARY_WEAPON"})
    entities = game.state[f"{builder.PACKAGE_ID}.entities"]
    state = parts(game)[1]
    assert state["last_cqc"]["success"]
    assert entities["hex"]["weapon"] is None
    assert state["ground_objects"]["weapon_1"]["weapon"] == {**before["weapon"], "location": "GROUND"}
    assert entities["hex"]["hp"] == before["hp"]
    assert entities["hex"]["position_cell"] == before["position_cell"]
    pickup(game, actor="hex", weapon_id=before["weapon"]["id"])
    assert game.state[f"{builder.PACKAGE_ID}.entities"]["hex"]["weapon"] == before["weapon"]


def test_empty_weapon_slot_does_not_break_turns_or_create_a_phantom_gun(dropped):
    for tool, args in (("cycle_weapon", {"actor": "vesper"}),
                       ("recover_recoil", {"actor": "vesper", "ap": 1}),
                       ("reload_weapon", {"actor": "vesper", "disposition": "RETAIN"}),
                       ("fire_single", {"attacker": "vesper", "target": "hostile_alpha",
                            "aim_point": [9.5, 2.5, 0.9], "critical_effect": "DEVASTATING_HIT"})):
        before = deepcopy(parts(dropped)[0])
        dropped.execute(tool, args)
        assert parts(dropped)[0] == before
    dropped.execute("end_allies_turn", {})
    dropped.execute("end_hostiles_turn", {})
    assert parts(dropped)[0]["weapon"] is None
    dropped.execute("move_actor", {"actor": "vesper", "destination": [3, 2]})
    assert parts(dropped)[0]["position_cell"] == [3, 2]


def test_empty_current_slot_accepts_pickup_with_a_full_reserve_inventory(dropped):
    actor, _ = parts(dropped)
    spare = actor["reserve_weapons"][0]
    actor["reserve_weapons"] = [{**spare, "id": f"spare-{i}"} for i in range(16)]
    pickup(dropped)
    assert parts(dropped)[0]["weapon"]["id"] == "vespers-rifle"
    assert len(parts(dropped)[0]["reserve_weapons"]) == 16


def test_stale_ground_counter_cannot_overwrite_an_existing_weapon(game):
    from sylvia_gm.application.package_runtime import PackageRuntimeError

    _, state = parts(game)
    state["ground_objects"]["weapon_1"] = {"kind": "WEAPON", "weapon": {"id": "someone-elses"}}
    before = deepcopy(game.state)
    with pytest.raises(PackageRuntimeError):
        drop(game)
    assert game.state == before
