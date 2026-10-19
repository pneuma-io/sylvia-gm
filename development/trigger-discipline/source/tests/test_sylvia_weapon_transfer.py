"""Physical handoff conserves the actual firearm and keeps ownership distinct from possession."""

import json
from copy import deepcopy

import pytest

from tests.test_sylvia_weapon_handling import (
    compiled_runtime as compiled_runtime,  # noqa: PLC0414
)
from tests.test_sylvia_weapon_handling import game as game  # noqa: PLC0414
from tests.test_sylvia_weapon_handling import parts
from tests.test_sylvia_weapon_switching import armed as armed  # noqa: PLC0414


@pytest.fixture
def handoff(armed):
    actor, _ = parts(armed)
    actor["weapon"]["location"] = "STOWED"
    actor["reserve_weapons"][0]["owner_id"] = "vesper"
    armed.state["pneuma-io.trigger-discipline.entities"]["hex"]["weapon"]["location"] = "STOWED"
    return armed


def transfer(game, **changes):
    return game.execute("transfer_reserve_weapon", {
        "actor": "vesper", "recipient": "hex", "weapon_id": "sidearm-1", "ap_cost": 1,
        **changes,
    })


def test_receiver_can_reopen_and_equip_the_same_borrowed_weapon(handoff):
    actor, _ = parts(handoff)
    before = deepcopy(actor["reserve_weapons"][0])
    transfer(handoff)
    handoff.state = json.loads(json.dumps(handoff.state))
    entities = handoff.state["pneuma-io.trigger-discipline.entities"]
    assert entities["vesper"]["reserve_weapons"] == []
    assert entities["hex"]["reserve_weapons"] == [before]
    assert entities["vesper"]["ap"] == 5
    assert entities["hex"]["ap"] == 6
    assert parts(handoff)[1]["last_equipment"]["status"] == "TRANSFERRED"
    handoff.execute("switch_weapon", {"actor": "hex", "current_weapon_id": "service_rifle",
        "next_weapon_id": "sidearm-1", "store_location": "QUICK_ACCESS", "ap_cost": 1})
    entities = handoff.state["pneuma-io.trigger-discipline.entities"]
    before["location"] = "READY"
    assert entities["hex"]["weapon"] == before
    assert entities["hex"]["weapon"]["owner_id"] == "vesper"
    assert not any(item["id"] == "sidearm-1" for item in entities["vesper"]["reserve_weapons"])


@pytest.mark.parametrize("fault", ["not_carried", "self", "ready_source", "ready_recipient", "far", "barrier", "inactive_recipient", "downed_source", "downed_recipient", "no_ap", "duplicate", "already_received"])
def test_invalid_handoff_preserves_both_loadouts_and_resources(handoff, fault):
    actor, state = parts(handoff)
    entities = handoff.state["pneuma-io.trigger-discipline.entities"]
    recipient = entities["hex"]
    request = {}
    if fault == "not_carried":
        request["weapon_id"] = "not-present"
    elif fault == "self":
        request["recipient"] = "vesper"
    elif fault == "ready_source":
        actor["weapon"]["location"] = "READY"
    elif fault == "ready_recipient":
        recipient["weapon"]["location"] = "READY"
    elif fault == "far":
        recipient["position_cell"] = [20, 20]
    elif fault == "barrier":
        wall = deepcopy(state["spatial_scene"]["geometry"][1])
        wall.update(id="handoff-wall", volume={"kind": "box", "min": [1, 3, 0], "max": [4, 3.1, 3]})
        state["spatial_scene"]["geometry"].append(wall)
    elif fault == "inactive_recipient":
        recipient["active"] = False
    elif fault == "downed_source":
        actor["downed"] = True
    elif fault == "downed_recipient":
        recipient["downed"] = True
    elif fault == "no_ap":
        actor["ap"] = 0
    elif fault == "duplicate":
        actor["reserve_weapons"].append(deepcopy(actor["reserve_weapons"][0]))
    else:
        recipient["reserve_weapons"] = [deepcopy(actor["reserve_weapons"][0])]
    before = deepcopy(entities)
    transfer(handoff, **request)
    assert handoff.state["pneuma-io.trigger-discipline.entities"] == before
    assert parts(handoff)[1]["last_equipment"]["status"] == "TRANSFER_REJECTED"
