"""Switch real carried weapon state without copying supplies or spending on stale choices."""

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


@pytest.fixture
def armed(game):
    actor, _ = parts(game)
    pistol = builder._entity("spare", side="allies", class_id="RIFLEMAN", position=[2, 2],
        weapon="pistol", parameter_values=game.parameters.values)["weapon"]
    pistol.update(id="sidearm-1", location="QUICK_ACCESS", current_recoil=1)
    actor["reserve_weapons"] = [pistol]
    return game


def switch(game, **changes):
    return game.execute("switch_weapon", {
        "actor": "vesper", "current_weapon_id": "service_rifle", "next_weapon_id": "sidearm-1",
        "store_location": "STOWED", "ap_cost": 1, **changes,
    })


def test_switch_preserves_each_weapon_and_fire_uses_the_new_weapon_after_reopen(armed):
    from sylvia_gm import dice

    actor, state = parts(armed)
    state["prepared"]["overwatch"].update(owner="vesper", active=True)
    actor["aim"]["ap"] = 2
    rifle, pistol = deepcopy(actor["weapon"]), deepcopy(actor["reserve_weapons"][0])
    switch(armed)
    armed.state = json.loads(json.dumps(armed.state))
    actor, state = parts(armed)
    rifle["location"] = "STOWED"
    pistol["location"] = "READY"
    assert actor["weapon"] == pistol
    assert actor["reserve_weapons"] == [rifle]
    assert actor["ap"] == 5
    assert not actor["aim"]["ap"]
    assert not state["prepared"]["overwatch"]["active"]
    assert state["last_equipment"]["status"] == "SWITCHED"
    with patch.object(dice._rng, "randint", return_value=20):
        armed.execute("fire_single", {"attacker": "vesper", "target": "hostile_alpha",
            "aim_point": [9.5, 2.5, 0.9], "critical_effect": "DEVASTATING_HIT"})
    actor, _ = parts(armed)
    assert actor["weapon"]["id"] == "sidearm-1"
    assert actor["weapon"]["magazine"]["rounds"] == pistol["magazine"]["rounds"] - 1
    assert actor["reserve_weapons"] == [rifle]
    assert actor["weapon"]["current_recoil"] > pistol["current_recoil"]


@pytest.mark.parametrize("fault", ["wrong_current", "absent_next", "no_ap", "inactive", "downed", "other_turn", "restrained", "ground", "reserve_ready", "reserve_restrained", "duplicate", "duplicate_current", "no_reserves"])
def test_invalid_or_stale_switch_does_not_move_equipment_or_spend(armed, fault):
    actor, state = parts(armed)
    request = {}
    if fault == "wrong_current":
        request["current_weapon_id"] = "not-this-rifle"
    elif fault == "absent_next":
        request["next_weapon_id"] = "not-carried"
    elif fault == "no_ap":
        actor["ap"] = 0
    elif fault == "inactive":
        actor["active"] = False
    elif fault == "downed":
        actor["downed"] = True
    elif fault == "other_turn":
        state["active_side"] = "hostiles"
    elif fault == "restrained":
        actor["weapon"]["controlled_by"] = "hostile_alpha"
    elif fault == "ground":
        actor["weapon"]["location"] = "GROUND"
    elif fault == "reserve_ready":
        actor["reserve_weapons"][0]["location"] = "READY"
    elif fault == "reserve_restrained":
        actor["reserve_weapons"][0]["controlled_by"] = "hostile_alpha"
    elif fault == "duplicate":
        duplicate = deepcopy(actor["reserve_weapons"][0])
        duplicate["magazine"]["rounds"] = 0
        actor["reserve_weapons"].append(duplicate)
    elif fault == "duplicate_current":
        actor["reserve_weapons"].append(deepcopy(actor["weapon"]))
    else:
        actor.pop("reserve_weapons")
    before = deepcopy(actor), deepcopy(state["prepared"])
    switch(armed, **request)
    actor, state = parts(armed)
    assert (actor, state["prepared"]) == before
    assert state.get("last_equipment", {}).get("status") != "SWITCHED"


def test_empty_chamber_is_not_filled_by_switching(armed):
    actor, _ = parts(armed)
    actor["reserve_weapons"][0]["chamber"] = "EMPTY"
    switch(armed)
    reason = armed.check("fire_single", {"attacker": "vesper", "target": "hostile_alpha",
        "aim_point": [9.5, 2.5, 0.9], "critical_effect": "DEVASTATING_HIT"})
    assert "live round" in reason
    assert parts(armed)[0]["weapon"]["chamber"] == "EMPTY"
