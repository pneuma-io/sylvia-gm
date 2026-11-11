"""An actual carried interface moves with its carrier without inventing network access."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from tests import test_sylvia_network_functions as support

network = support.network


def carried_interface(game):
    state = support.world(game)
    state["network"]["nodes"]["dock_console"]["physical_binding"] = {
        "surface_id": "operations.map",
        "entity_id": "engineer",
    }
    state["network_interfaces"]["dock_console"] = {
        "kind": "local_carried",
        "active": True,
        "generation": 1,
        "ap": 1,
        "carrier_id": "engineer",
        "connections": {"engineer": {"active": True, "generation": 1}},
    }
    return state


def connect_and_control(game):
    state = carried_interface(game)
    state["network_sessions"] = {}
    state["network_topology"]["links"]["dock_cable"]["barrier"]["privilege"] = 2
    game.execute(
        "network_connection",
        {
            "actor": "engineer",
            "node_id": "dock_console",
            "action": "CONNECT",
        },
    )
    game.execute(
        "intrude_network",
        {
            "actor": "engineer",
            "node_id": "dock_console",
            "action": "GET_IN",
        },
    )
    game.execute(
        "pivot_network",
        {
            "actor": "engineer",
            "node_id": "floodgate",
            "route_step": ["dock_console", "floodgate", "dock_cable"],
        },
    )
    assert game.check("operate_remote_function", support.args()) is None


def test_carried_connection_survives_actual_movement_and_operates_the_real_device(network):
    from sylvia_gm import dice

    connect_and_control(network)
    before = deepcopy(support.world(network)["network_entries"])
    network.execute("move_actor", {"actor": "engineer", "destination": [3, 3]})
    assert support.world(network)["network_motion"]["engineer"] == 1
    assert support.world(network)["network_entries"] == before
    assert network.check("operate_remote_function", support.args()) is None
    with patch.object(
        dice._rng, "randint", side_effect=AssertionError("operation rolled intrusion")
    ):
        network.execute("operate_remote_function", support.args())
    state = support.world(network)
    assert state["last_network"]["success"]
    assert not next(
        item for item in state["spatial_scene"]["geometry"] if item["id"] == "security-door"
    )["active"]


def test_another_carrier_cannot_be_borrowed_by_a_connection_declaration(network):
    state = carried_interface(network)
    state["network_interfaces"]["dock_console"]["carrier_id"] = "vesper"
    before = deepcopy(network.state)
    command = {"actor": "engineer", "node_id": "dock_console", "action": "CONNECT"}
    assert network.check("network_connection", command) is not None
    network.execute("network_connection", command)
    assert not support.world(network)["last_network"]["success"]
    before["pneuma-io.trigger-discipline.state"]["last_network"] = support.world(network)[
        "last_network"
    ]
    assert network.state == before


@pytest.mark.parametrize("change", ["disconnect", "replace", "transfer", "returned"])
def test_lost_or_replaced_carriage_cannot_spend_or_revive_old_privilege(network, change):
    connect_and_control(network)
    state = support.world(network)
    interface = state["network_interfaces"]["dock_console"]
    if change == "disconnect":
        interface["connections"]["engineer"]["active"] = False
    elif change == "transfer":
        interface["carrier_id"] = "vesper"
    else:
        interface["connections"]["engineer"]["generation"] += 1
    before = deepcopy(network.state)
    assert network.check("operate_remote_function", support.args()) is not None
    network.execute("operate_remote_function", support.args())
    assert not support.world(network)["last_network"]["success"]
    before["pneuma-io.trigger-discipline.state"]["last_network"] = support.world(network)[
        "last_network"
    ]
    assert network.state == before
    if change == "returned":
        network.execute(
            "network_connection",
            {
                "actor": "engineer",
                "node_id": "dock_console",
                "action": "CONNECT",
            },
        )
        assert network.check("operate_remote_function", support.args()) is not None
