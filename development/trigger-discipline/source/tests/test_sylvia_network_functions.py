"""Authored remote operations through the real package runtime; no provider or desktop."""

import json
import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_sylvia_player_controls as harness


@pytest.fixture
def network():
    harness.PlayerControlRuntimeTests.setUpClass()
    test = harness.PlayerControlRuntimeTests()
    test.setUp()
    world = test.state[f"{harness.builder.PACKAGE_ID}.state"]
    entities = test.state[f"{harness.builder.PACKAGE_ID}.entities"]
    entities["engineer"] = entities.pop("hex")
    entities["engineer"]["id"] = "engineer"
    body = next(item for item in world["spatial_scene"]["entities"] if item["id"] == "hex")
    body["id"] = "engineer"
    world["eligible_actor_ids"] = ["engineer"]
    world["network"] = {
        "graph_id": "harbor",
        "revision": 1,
        "groups": {},
        "selection": {},
        "nodes": {
            "dock_console": {
                "label": "Dock console",
                "kind": "terminal",
                "visible": True,
                "functions": {},
            },
            "floodgate": {
                "label": "Floodgate",
                "kind": "controller",
                "visible": True,
                "functions": {
                    "raise_gate": {
                        "label": "Raise gate",
                        "visible": True,
                        "enabled": True,
                        "state": "closed",
                    }
                },
            },
        },
        "links": {
            "dock_cable": {
                "source": "dock_console",
                "target": "floodgate",
                "direction": "directed",
                "visible": True,
            }
        },
    }
    world["network_functions"] = {
        "floodgate": {
            "functions": {
                "raise_gate": {
                    "kind": "geometry",
                    "geometry_id": "security-door",
                    "map_id": "td-conformance-floor",
                    "ap": 1,
                    "privilege": 2,
                    "patch": {"active": False, "movement": "passable", "visibility": "clear"},
                }
            }
        }
    }
    world["network_topology"] = {
        "nodes": {node: {"online": True, "generation": 1} for node in world["network"]["nodes"]},
        "links": {"dock_cable": {"online": True, "generation": 1}},
    }
    world["network_entries"] = {
        "engineer": {"nodes": {"dock_console": {
            "active": True, "generation": 1, "interface_generation": 1,
            "node_generation": 1, "motion_generation": 0,
        }}}
    }
    world["network_motion"] = {"engineer": 0}
    world["network_interfaces"] = {"dock_console": {
        "kind": "local_stationary", "active": True, "generation": 1,
        "map_id": world["spatial_scene"]["map"]["id"], "position_cell": [2, 3], "ap": 1,
    }}
    root = {
        "active": True,
        "privilege": 1,
        "route_label": "Dock console",
        "entry_generation": 1,
        "nodes": ["dock_console"],
        "links": [],
        "node_generations": [1],
        "link_generations": [],
        "generation": 1,
        "session_generations": [1],
    }
    world["network_sessions"] = {
        "engineer": {
            "nodes": {
                "dock_console": root,
                "floodgate": {
                    **root,
                    "privilege": 2,
                    "route_label": "Dock console → Floodgate",
                    "nodes": ["dock_console", "floodgate"],
                    "links": ["dock_cable"],
                    "node_generations": [1, 1],
                    "link_generations": [1],
                    "session_generations": [1, 1],
                },
            }
        }
    }
    rule = {"ap": 1, "check_required": True, "difficulty": 0, "privilege": 1, "reveal_links": []}
    world["network_intrusions"] = {
        node: {
            "visible_links": ["dock_cable"] if node == "dock_console" else [],
            "actions": {
                "GET_IN": deepcopy(rule),
                "DISCOVER": {**rule, "reveal_links": ["dock_cable"]},
                "ESCALATE": {**rule, "privilege": 2},
                "FOOTHOLD": {**rule, "persistence": {"method": "harbor_implant", "generation": 1}},
            },
            "reentry": {},
        }
        for node in world["network"]["nodes"]
    }
    world["network_topology"]["links"]["dock_cable"]["barrier"] = deepcopy(rule)
    return test


def args(**changes):
    return {"actor": "engineer", "node_id": "floodgate", "remote_function": "raise_gate", **changes}


@pytest.mark.parametrize("action,reveals", [("GET_IN", True), ("ESCALATE", False), ("FOOTHOLD", False)])
def test_map_the_links_reveals_only_on_successful_entry(network, action, reveals):
    state = world(network)
    state["network"]["nodes"]["floodgate"]["visible"] = False
    state["network"]["links"]["dock_cable"]["visible"] = False
    network.execute("intrude_network", {"actor": "engineer", "node_id": "dock_console", "action": action})
    state = world(network)
    assert state["last_network"]["success"]
    assert state["network"]["nodes"]["floodgate"]["visible"] is reveals
    assert state["network"]["links"]["dock_cable"]["visible"] is reveals


def test_entry_without_map_the_links_does_not_reveal_neighbors(network):
    state = world(network)
    network.state[f"{harness.builder.PACKAGE_ID}.entities"]["engineer"]["specialties"] = []
    state["network"]["nodes"]["floodgate"]["visible"] = False
    state["network"]["links"]["dock_cable"]["visible"] = False
    network.execute("intrude_network", {"actor": "engineer", "node_id": "dock_console", "action": "GET_IN"})
    assert world(network)["last_network"]["success"]
    assert not world(network)["network"]["nodes"]["floodgate"]["visible"]


def world(test):
    return test.state[f"{harness.builder.PACKAGE_ID}.state"]


def test_authorized_device_changes_real_geometry_once_without_mission_side_effects(network):
    from sylvia_gm.spatial import SpatialEngine

    scene = world(network)["spatial_scene"]
    gate = next(item for item in scene["geometry"] if item["id"] == "security-door")
    gate["id"] = "harbor-floodgate"
    world(network)["network_functions"]["floodgate"]["functions"]["raise_gate"]["geometry_id"] = (
        gate["id"]
    )
    mission = deepcopy(world(network)["mission"])
    query = {
        "kind": "ray",
        "map_id": scene["map"]["id"],
        "origin": [9, 4.5, 1],
        "destination": [11, 4.5, 1],
        "include": ["geometry"],
        "max_intersections": 8,
    }
    assert SpatialEngine(scene).query(query).as_payload()["intersections"]
    assert network.check("operate_remote_function", args()) is None
    result = network.execute("operate_remote_function", args())
    assert world(network)["last_network"]["success"] is True
    assert (
        not SpatialEngine(world(network)["spatial_scene"])
        .query(query)
        .as_payload()["intersections"]
    )
    assert network.state[f"{harness.builder.PACKAGE_ID}.entities"]["engineer"]["ap"] == 5
    assert world(network)["mission"] == mission
    assert "_intrusion" not in result["context"]
    assert result["context"]["_function"] is None
    # The serialized authoritative state is sufficient after reopening; no Python session cache.
    network.state = json.loads(json.dumps(network.state))
    assert network.check("operate_remote_function", args()) is None


@pytest.mark.parametrize(
    "fault",
    [
        "other_actor",
        "privilege",
        "entry_ended",
        "entry_reopened",
        "offline_node",
        "restarted_node",
        "offline_link",
        "replaced_link",
        "wrong_direction",
        "unrelated_link",
        "hidden_function",
        "disabled_function",
        "missing_function",
        "missing_node",
        "missing_session",
        "intermediate_session",
        "wrong_map",
        "negative_cost",
        "no_ap",
        "wrong_side",
        "too_long",
    ],
)
def test_stale_or_unauthorized_operation_never_spends_or_changes_geometry(network, fault):
    state = world(network)
    request = args()
    grant = state["network_sessions"]["engineer"]["nodes"]["floodgate"]
    function = state["network_functions"]["floodgate"]["functions"]["raise_gate"]
    if fault == "other_actor":
        request["actor"] = "vesper"
    elif fault == "privilege":
        grant["privilege"] = 1
    elif fault == "entry_ended":
        state["network_entries"]["engineer"]["nodes"]["dock_console"]["active"] = False
    elif fault == "entry_reopened":
        state["network_entries"]["engineer"]["nodes"]["dock_console"]["generation"] = 2
    elif fault == "offline_node":
        state["network_topology"]["nodes"]["dock_console"]["online"] = False
    elif fault == "restarted_node":
        state["network_topology"]["nodes"]["dock_console"]["generation"] = 2
    elif fault == "offline_link":
        state["network_topology"]["links"]["dock_cable"]["online"] = False
    elif fault == "replaced_link":
        state["network_topology"]["links"]["dock_cable"]["generation"] = 2
    elif fault == "wrong_direction":
        state["network"]["links"]["dock_cable"].update(source="floodgate", target="dock_console")
    elif fault == "unrelated_link":
        state["network"]["links"]["dock_cable"]["source"] = "absent"
    elif fault == "hidden_function":
        state["network"]["nodes"]["floodgate"]["functions"]["raise_gate"]["visible"] = False
    elif fault == "disabled_function":
        state["network"]["nodes"]["floodgate"]["functions"]["raise_gate"]["enabled"] = False
    elif fault == "missing_function":
        request["remote_function"] = "missing"
    elif fault == "missing_node":
        request["node_id"] = "missing"
    elif fault == "missing_session":
        state["network_sessions"]["engineer"]["nodes"].pop("floodgate")
    elif fault == "intermediate_session":
        state["network_sessions"]["engineer"]["nodes"]["dock_console"]["active"] = False
    elif fault == "wrong_map":
        function["map_id"] = "other-map"
    elif fault == "negative_cost":
        function["ap"] = -1
    elif fault == "no_ap":
        network.state[f"{harness.builder.PACKAGE_ID}.entities"]["engineer"]["ap"] = 0
    elif fault == "wrong_side":
        state["active_side"] = "hostiles"
    elif fault == "too_long":
        grant["nodes"] *= 5
    before = deepcopy(network.state)
    assert network.check("operate_remote_function", request) is not None
    network.execute("operate_remote_function", request)
    assert (
        network.state[f"{harness.builder.PACKAGE_ID}.entities"]
        == before[f"{harness.builder.PACKAGE_ID}.entities"]
    )
    assert (
        world(network)["spatial_scene"]
        == before[f"{harness.builder.PACKAGE_ID}.state"]["spatial_scene"]
    )


def test_hidden_bindings_and_unknown_defender_state_never_enter_player_projection(network):
    from sylvia_gm.application.network_graph import project_network_graph

    state = world(network)
    state["network"]["nodes"]["hidden_sensor"] = {"visible": False, "private_defender": "secret"}
    projected = harness.thaw_json_value(project_network_graph(state["network"]))
    assert "hidden_sensor" not in json.dumps(projected)
    assert "network_sessions" not in json.dumps(projected)
    assert "geometry_id" not in json.dumps(projected)


def test_entry_discovery_pivot_escalate_and_operate_use_authored_ids(network):
    state = world(network)
    state["network_sessions"] = {}
    state["network"]["nodes"]["floodgate"]["visible"] = False
    state["network"]["links"]["dock_cable"]["visible"] = False
    enter = {"actor": "engineer", "node_id": "dock_console", "action": "GET_IN"}
    assert network.check("intrude_network", enter) is None
    network.execute("intrude_network", enter)
    assert world(network)["network"]["nodes"]["floodgate"]["visible"] is True
    assert network.check("operate_remote_function", args()) is not None
    pivot = {
        "actor": "engineer",
        "node_id": "floodgate",
        "route_step": ["dock_console", "floodgate", "dock_cable"],
    }
    assert network.check("pivot_network", pivot) is None
    network.execute("pivot_network", pivot)
    assert world(network)["last_network"]["success"] is True
    assert network.check("operate_remote_function", args()) is not None
    network.execute(
        "intrude_network", {"actor": "engineer", "node_id": "floodgate", "action": "ESCALATE"}
    )
    assert network.check("operate_remote_function", args()) is None
    network.execute("operate_remote_function", args())
    assert world(network)["last_network"]["status"] == "OPERATED"
    assert network.state[f"{harness.builder.PACKAGE_ID}.entities"]["engineer"]["ap"] == 2


def test_failed_attempt_pays_without_revoking_access_or_automatically_alerting(network):
    state = world(network)
    state["network_intrusions"]["floodgate"]["actions"]["ESCALATE"]["difficulty"] = 1000
    before = deepcopy(state["network_sessions"])
    graph = deepcopy(state["network"])
    attempt = {"actor": "engineer", "node_id": "floodgate", "action": "ESCALATE"}
    network.execute("intrude_network", attempt)
    assert world(network)["last_network"]["status"] == "FAILED_CHECK"
    assert world(network)["network_sessions"] == before
    assert world(network)["network"] == graph
    assert network.state[f"{harness.builder.PACKAGE_ID}.entities"]["engineer"]["ap"] == 5
    assert network.check("intrude_network", attempt) is None


def test_authorized_link_does_not_roll_and_reentry_cannot_revive_old_downstream_privilege(network):
    state = world(network)
    state["network_sessions"]["engineer"]["nodes"].pop("floodgate")
    state["network_topology"]["links"]["dock_cable"]["barrier"].update(
        check_required=False, difficulty=1000
    )
    pivot = {
        "actor": "engineer",
        "node_id": "floodgate",
        "route_step": ["dock_console", "floodgate", "dock_cable"],
    }
    result = network.execute("pivot_network", pivot)
    assert world(network)["last_network"]["success"] is True
    assert "_intrusion" not in result["context"]
    network.execute(
        "intrude_network", {"actor": "engineer", "node_id": "floodgate", "action": "ESCALATE"}
    )
    assert network.check("operate_remote_function", args()) is None
    network.execute("operate_remote_function", args())
    geometry = deepcopy(world(network)["spatial_scene"])
    # Replacing a root session gets a new generation; its old dependent grant stays expired.
    network.execute(
        "intrude_network", {"actor": "engineer", "node_id": "dock_console", "action": "GET_IN"}
    )
    assert network.check("operate_remote_function", args()) is not None
    assert world(network)["network"]["nodes"]["floodgate"]["visible"] is True
    assert world(network)["spatial_scene"] == geometry


def connection(**changes):
    return {"actor": "engineer", "node_id": "dock_console", "action": "CONNECT", **changes}


def test_local_connection_pays_authored_cost_without_granting_privilege(network):
    state = world(network)
    state["network_entries"] = {}
    state["network_sessions"] = {}
    state["network_motion"] = {}
    assert network.check("network_connection", connection()) is None
    network.execute("network_connection", connection())
    state = world(network)
    assert state["network_entries"]["engineer"]["nodes"]["dock_console"] == {
        "active": True, "generation": 1, "interface_generation": 1,
        "node_generation": 1, "motion_generation": 0, "connection_generation": 0,
    }
    assert state["network_sessions"] == {}
    assert network.state[f"{harness.builder.PACKAGE_ID}.entities"]["engineer"]["ap"] == 5
    assert network.check("operate_remote_function", args()) is not None
    assert network.check("intrude_network", connection(action="GET_IN")) is None


@pytest.mark.parametrize("fault", ["wrong_cell", "wrong_map", "offline", "no_ap", "missing_interface"])
def test_invalid_connection_does_not_spend_or_replace_access(network, fault):
    state = world(network)
    interface = state["network_interfaces"]["dock_console"]
    if fault == "wrong_cell":
        interface["position_cell"] = [7, 7]
    elif fault == "wrong_map":
        interface["map_id"] = "other-map"
    elif fault == "offline":
        state["network_topology"]["nodes"]["dock_console"]["online"] = False
    elif fault == "no_ap":
        network.state[f"{harness.builder.PACKAGE_ID}.entities"]["engineer"]["ap"] = 0
    else:
        state["network_interfaces"] = {}
    before = deepcopy(network.state)
    assert network.check("network_connection", connection()) is not None
    network.execute("network_connection", connection())
    assert world(network)["network_entries"] == before[f"{harness.builder.PACKAGE_ID}.state"]["network_entries"]
    assert network.state[f"{harness.builder.PACKAGE_ID}.entities"] == before[f"{harness.builder.PACKAGE_ID}.entities"]


def test_disconnect_reconnect_preserves_knowledge_and_effects_but_not_old_privilege(network):
    network.execute("operate_remote_function", args())
    scene = deepcopy(world(network)["spatial_scene"])
    network.execute("network_connection", connection(action="DISCONNECT"))
    assert network.check("operate_remote_function", args()) is not None
    network.execute("network_connection", connection())
    network.state = json.loads(json.dumps(network.state))
    assert world(network)["network"]["nodes"]["floodgate"]["visible"] is True
    assert world(network)["spatial_scene"] == scene
    assert network.check("operate_remote_function", args()) is not None
    network.execute("intrude_network", connection(action="GET_IN"))
    assert network.check("operate_remote_function", args()) is not None


def test_disconnect_is_actor_owned_and_remains_possible_offline(network):
    world(network)["network_topology"]["nodes"]["dock_console"]["online"] = False
    assert network.check("network_connection", connection(actor="vesper", action="DISCONNECT")) is not None
    assert network.check("network_connection", connection(action="DISCONNECT")) is None
    network.execute("network_connection", connection(action="DISCONNECT"))
    assert world(network)["network_entries"]["engineer"]["nodes"]["dock_console"]["active"] is False


def test_walking_away_and_back_requires_a_new_connection(network):
    assert network.check("move_actor", {"actor": "engineer", "destination": [3, 3]}) is None
    network.execute("move_actor", {"actor": "engineer", "destination": [3, 3]})
    assert world(network)["network_motion"]["engineer"] == 1
    assert network.check("intrude_network", connection(action="GET_IN")) is not None
    network.execute("move_actor", {"actor": "engineer", "destination": [2, 3]})
    assert world(network)["network_motion"]["engineer"] == 2
    assert network.check("intrude_network", connection(action="GET_IN")) is not None
    network.execute("network_connection", connection())
    assert network.check("intrude_network", connection(action="GET_IN")) is None
    assert network.check("operate_remote_function", args()) is not None


def test_rejected_movement_keeps_the_stationary_connection(network):
    network.state[f"{harness.builder.PACKAGE_ID}.entities"]["engineer"]["ap"] = 0
    network.execute("move_actor", {"actor": "engineer", "destination": [3, 3]})
    assert world(network)["network_motion"]["engineer"] == 0
    network.state[f"{harness.builder.PACKAGE_ID}.entities"]["engineer"]["ap"] = 6
    assert network.check("operate_remote_function", args()) is None


@pytest.mark.parametrize("changed", ["interface", "node"])
def test_replaced_interface_or_node_requires_reconnecting_before_get_in(network, changed):
    state = world(network)
    if changed == "interface":
        state["network_interfaces"]["dock_console"]["generation"] += 1
    else:
        state["network_topology"]["nodes"]["dock_console"]["generation"] += 1
    assert network.check("intrude_network", connection(action="GET_IN")) is not None
    assert network.check("operate_remote_function", args()) is not None
    network.execute("network_connection", connection())
    assert network.check("intrude_network", connection(action="GET_IN")) is None


def test_escalation_does_not_downgrade_existing_higher_privilege(network):
    world(network)["network_sessions"]["engineer"]["nodes"]["floodgate"]["privilege"] = 3
    network.execute("intrude_network", {"actor": "engineer", "node_id": "floodgate", "action": "ESCALATE"})
    assert world(network)["network_sessions"]["engineer"]["nodes"]["floodgate"]["privilege"] == 3


def test_forced_movement_expires_the_targets_entry_not_the_pushers(network):
    entities = network.state[f"{harness.builder.PACKAGE_ID}.entities"]
    # Guarantee the representative shove succeeds; this is fixture input, not a balance default.
    entities["vesper"]["skills"]["cqc"] = 100
    network.execute("resolve_cqc_displacement", {
        "actor": "vesper", "target": "engineer", "maneuver": "SHOVE", "destination": [3, 3],
    })
    assert world(network)["last_cqc"]["success"] is True
    assert world(network)["network_motion"]["engineer"] == 1
    assert world(network)["network_motion"].get("vesper", 0) == 0
    network.execute("move_actor", {"actor": "engineer", "destination": [2, 3]})
    assert network.check("intrude_network", connection(action="GET_IN")) is not None


def test_route_preview_is_friendly_text_from_this_actors_current_session(network):
    preview = network.preview("operate_remote_function", args())
    assert [row["value"] for row in preview["preview"]["summary"][:2]] == [
        "Dock console", "Floodgate",
    ]
    facts = {row["label"]: row["value"] for row in preview["preview"]["summary"]}
    assert facts["Access"] == "Permitted; no intrusion check"
    assert facts["AP cost"] == 1
    assert "network_sessions" not in json.dumps(harness.thaw_json_value(preview))
    other = network.preview("operate_remote_function", args(actor="vesper"))
    assert "Floodgate" not in json.dumps(harness.thaw_json_value(other))


def test_function_preview_uses_current_cost_without_private_bindings(network):
    state = world(network)
    state["network_functions"]["floodgate"]["functions"]["raise_gate"].update(
        ap=3, private_marker="not-player-visible",
    )
    preview = network.preview("operate_remote_function", args())
    assert {row["label"]: row["value"] for row in preview["preview"]["summary"]}["AP cost"] == 3
    assert "not-player-visible" not in json.dumps(harness.thaw_json_value(preview))
    assert "geometry_id" not in json.dumps(harness.thaw_json_value(preview))
    state["network_sessions"]["engineer"]["nodes"]["floodgate"]["privilege"] = 0
    # The owner runs this gate before exposing a non-map preview; the raw preview helper
    # only calculates presentation and deliberately does not authorize an action.
    assert network.check("operate_remote_function", args()) == "This actor lacks the required privilege."


def test_eight_node_route_keeps_its_last_hop_checked(network):
    state = world(network)
    names = ["dock_console", *(f"relay_{i}" for i in range(6)), "floodgate"]
    template = deepcopy(state["network_sessions"]["engineer"]["nodes"]["floodgate"])
    for index, node in enumerate(names):
        state["network"]["nodes"].setdefault(node, {"label": node, "kind": "relay", "visible": True, "functions": {}})
        state["network_topology"]["nodes"][node] = {"online": True, "generation": 1}
        state["network_sessions"]["engineer"]["nodes"][node] = {
            **template, "nodes": names[:index + 1], "links": [f"hop_{i}" for i in range(index)],
            "node_generations": [1] * (index + 1), "session_generations": [1] * (index + 1),
            "link_generations": [1] * index, "route_label": " → ".join(names[:index + 1]),
        }
        if index:
            state["network"]["links"][f"hop_{index - 1}"] = {
                "source": names[index - 1], "target": node, "direction": "directed", "visible": True,
            }
            state["network_topology"]["links"][f"hop_{index - 1}"] = {"online": True, "generation": 1}
    assert network.check("operate_remote_function", args()) is None
    card = network.preview("operate_remote_function", args())["preview"]
    assert [row["value"] for row in card["summary"][:8]] == [
        state["network"]["nodes"][node]["label"] for node in names
    ]
    assert card["summary"][-1] == {"label": "AP cost", "value": 1, "emphasis": False}
    state["network_topology"]["links"]["hop_6"]["generation"] = 2
    assert network.check("operate_remote_function", args()) is not None
    network.execute("operate_remote_function", args())
    assert not world(network)["last_network"]["success"]
    assert network.state[f"{harness.builder.PACKAGE_ID}.entities"]["engineer"]["ap"] == 6
    state = world(network)
    state["network_topology"]["links"]["hop_6"]["generation"] = 1
    state["network_topology"]["nodes"]["floodgate"]["generation"] = 2
    assert network.check("operate_remote_function", args()) is not None
