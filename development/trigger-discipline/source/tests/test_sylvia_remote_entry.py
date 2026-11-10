"""A remote service is reachable only through its authored actor-bound connection."""

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_sylvia_network_functions import args, connection, harness, world
from test_sylvia_network_functions import network as network  # noqa: PLC0414


@pytest.fixture
def remote(network):
    state = world(network)
    state["network_interfaces"]["dock_console"] = {
        "kind": "remote_service", "active": True, "generation": 3, "ap": 1,
        "connections": {"engineer": {"active": True, "generation": 8}},
    }
    state["network_entries"] = {}
    state["network_sessions"] = {}
    return network


def test_remote_entry_is_actor_bound_and_grants_no_access_on_its_own(remote):
    assert remote.check("network_connection", connection()) is None
    remote.execute("network_connection", connection())
    state = world(remote)
    assert state["network_entries"]["engineer"]["nodes"]["dock_console"]["connection_generation"] == 8
    assert state["network_sessions"] == {}
    assert remote.check("operate_remote_function", args()) is not None
    remote.execute("intrude_network", connection(action="GET_IN"))
    remote.execute("pivot_network", {"actor": "engineer", "node_id": "floodgate",
        "route_step": ["dock_console", "floodgate", "dock_cable"]})
    remote.execute("intrude_network", {"actor": "engineer", "node_id": "floodgate", "action": "ESCALATE"})
    assert remote.check("operate_remote_function", args()) is None
    remote.execute("move_actor", {"actor": "engineer", "destination": [3, 3]})
    remote.state = json.loads(json.dumps(remote.state))
    assert world(remote)["network_motion"]["engineer"] == 1
    assert remote.check("operate_remote_function", args()) is None
    remote.execute("operate_remote_function", args())
    door = next(item for item in world(remote)["spatial_scene"]["geometry"] if item["id"] == "security-door")
    assert not door["active"]


@pytest.mark.parametrize("fault", ["missing", "wrong_actor", "inactive", "generation", "offline", "kind"])
def test_remote_entry_never_infers_connectivity(remote, fault):
    state = world(remote)
    interface = state["network_interfaces"]["dock_console"]
    if fault == "missing":
        interface.pop("connections")
    elif fault == "wrong_actor":
        interface["connections"] = {"vesper": {"active": True, "generation": 8}}
    elif fault == "inactive":
        interface["connections"]["engineer"]["active"] = False
    elif fault == "generation":
        interface["connections"]["engineer"]["generation"] = 0
    elif fault == "offline":
        state["network_topology"]["nodes"]["dock_console"]["online"] = False
    else:
        interface["kind"] = "internet"
    before = deepcopy(remote.state)
    assert remote.check("network_connection", connection()) is not None
    remote.execute("network_connection", connection())
    namespace = harness.builder.PACKAGE_ID
    assert remote.state[f"{namespace}.entities"] == before[f"{namespace}.entities"]
    assert world(remote)["network_entries"] == {}


def test_replaced_remote_connection_invalidates_saved_access_until_fresh_entry(remote):
    remote.execute("network_connection", connection())
    remote.execute("intrude_network", connection(action="GET_IN"))
    state = world(remote)
    state["network_interfaces"]["dock_console"]["connections"]["engineer"]["generation"] += 1
    assert remote.check("intrude_network", connection(action="GET_IN")) is not None
    assert remote.check("intrude_network", connection(action="ESCALATE")) is not None
    assert remote.check("network_connection", connection(action="DISCONNECT")) is None
    remote.execute("network_connection", connection())
    assert remote.check("intrude_network", connection(action="ESCALATE")) is not None
    remote.execute("intrude_network", connection(action="GET_IN"))
    assert remote.check("intrude_network", connection(action="ESCALATE")) is None


@pytest.mark.parametrize("fault", ["ended", "missing", "replaced_interface"])
def test_existing_remote_access_requires_the_connection_to_remain_valid(remote, fault):
    remote.execute("network_connection", connection())
    remote.execute("intrude_network", connection(action="GET_IN"))
    interface = world(remote)["network_interfaces"]["dock_console"]
    if fault == "ended":
        interface["connections"]["engineer"]["active"] = False
    elif fault == "missing":
        interface["connections"] = {}
    else:
        interface["generation"] += 1
    before = deepcopy(remote.state)
    request = connection(action="ESCALATE")
    assert remote.check("intrude_network", request) is not None
    remote.execute("intrude_network", request)
    namespace = harness.builder.PACKAGE_ID
    assert remote.state[f"{namespace}.entities"] == before[f"{namespace}.entities"]
    assert world(remote)["network_sessions"] == before[f"{namespace}.state"]["network_sessions"]
    assert remote.check("network_connection", connection(action="DISCONNECT")) is None
