"""Monitoring is authored evidence, independent of access rolls and defender action timing."""

import json
import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from test_sylvia_network_functions import args, connection, harness, world
from test_sylvia_network_functions import (
    network as network,  # noqa: PLC0414 - re-export pytest fixture
)


def author_monitor(test, *, difficulty=1000, notice="UNCERTAIN", response=None):
    state = world(test)
    state["network_defenders"] = {
        "dock_security": {"active": True, "operator": "vesper", "awareness": {}, "responses": {}}
    }
    state["network_monitors"] = [{
        "id": "console_monitor", "active": True, "defender": "dock_security",
        "nodes": ["dock_console", "floodgate"], "links": [],
        "actions": ["GET_IN", "DISCOVER", "PIVOT", "ESCALATE", "OPERATE"],
        "notice": notice, "skill": "networking", "aptitude": "reason",
        "difficulty": difficulty, "visibility_modifiers": {"LOW": -2, "STANDARD": 0, "HIGH": 2},
        "evidence": 2, "response": response,
    }]


@pytest.mark.parametrize("success", [False, True])
@pytest.mark.parametrize("noticed", [False, True])
def test_intrusion_success_and_notice_are_independent(network, success, noticed):
    author_monitor(network, difficulty=1000 if noticed else 0)
    state = world(network)
    state["network_sessions"] = {}
    state["network_intrusions"]["dock_console"]["actions"]["GET_IN"]["difficulty"] = 0 if success else 1000
    observed = network.execute("intrude_network", connection(action="GET_IN"))
    state = world(network)
    assert state["last_network"]["success"] is success
    awareness = state["network_defenders"]["dock_security"]["awareness"]
    assert ("engineer" in awareness) is noticed
    if noticed:
        assert awareness["engineer"]["level"] == 2  # Directly located, no mandatory ladder.
    assert network.state[f"{harness.builder.PACKAGE_ID}.entities"]["engineer"]["ap"] == 5
    assert observed["context"]["_monitor"] is None
    assert observed["context"]["_quiet_roll"] is None


@pytest.mark.parametrize("coverage", ["uncovered", "harmless", "certain", "disabled", "different_defender"])
def test_only_relevant_uncertain_monitoring_rolls(network, coverage):
    from sylvia_gm import dice

    author_monitor(network)
    monitor = world(network)["network_monitors"][0]
    if coverage == "uncovered":
        monitor["nodes"] = ["floodgate"]
    elif coverage == "harmless":
        monitor["actions"] = ["OPERATE"]
    elif coverage == "certain":
        monitor["notice"] = "CERTAIN"
    elif coverage == "disabled":
        monitor["active"] = False
    else:
        world(network)["network_defenders"]["dock_security"]["active"] = False
    with patch.object(dice._rng, "randint", return_value=10) as roll:
        network.execute("intrude_network", connection(action="GET_IN"))
    assert roll.call_count == 1  # Only the independently warranted intrusion check.
    awareness = world(network)["network_defenders"]["dock_security"]["awareness"]
    assert ("engineer" in awareness) is (coverage == "certain")


@pytest.mark.parametrize("visibility,noticed", [("LOW", False), ("STANDARD", False), ("HIGH", True)])
def test_visibility_uses_authored_modifiers_for_the_separate_quiet_check(network, visibility, noticed):
    from sylvia_gm import dice

    entities = network.state[f"{harness.builder.PACKAGE_ID}.entities"]
    total = 10 + entities["engineer"]["skills"]["networking"] + entities["engineer"]["aptitudes"]["reason"]
    author_monitor(network, difficulty=total)
    world(network)["network_intrusions"]["dock_console"]["actions"]["GET_IN"]["visibility"] = visibility
    with patch.object(dice._rng, "randint", return_value=10):
        network.execute("intrude_network", connection(action="GET_IN"))
    assert ("engineer" in world(network)["network_defenders"]["dock_security"]["awareness"]) is noticed


@pytest.mark.parametrize("kind", ["END_SESSION", "ISOLATE_LINK", "SHUTDOWN_NODE"])
def test_authored_automatic_response_changes_real_access_after_notice(network, kind):
    author_monitor(network, notice="CERTAIN", response="contain")
    state = world(network)
    state["network_sessions"] = {}
    target = "dock_cable" if kind == "ISOLATE_LINK" else "dock_console"
    state["network_defenders"]["dock_security"]["responses"]["contain"] = {
        "mode": "AUTOMATIC", "kind": kind, "target": target, "min_awareness": 2,
    }
    network.execute("intrude_network", connection(action="GET_IN"))
    state = world(network)
    assert state["last_network"]["success"] is True
    if kind == "END_SESSION":
        assert state["network_sessions"]["engineer"]["nodes"]["dock_console"]["active"] is False
    elif kind == "ISOLATE_LINK":
        assert state["network_topology"]["links"]["dock_cable"]["online"] is False
    else:
        assert state["network_topology"]["nodes"]["dock_console"]["online"] is False
    pivot = {"actor": "engineer", "node_id": "floodgate", "route_step": ["dock_console", "floodgate", "dock_cable"]}
    assert network.check("pivot_network", pivot) is not None


def test_distinct_defenders_and_overlapping_monitors_do_not_erase_evidence(network):
    author_monitor(network, notice="CERTAIN")
    state = world(network)
    state["network_defenders"]["other_security"] = {
        "active": True, "operator": "vesper", "awareness": {}, "responses": {},
    }
    strongest = state["network_monitors"][0]
    strongest["evidence"] = 3
    state["network_monitors"].extend([
        {**deepcopy(strongest), "id": "weak_notice", "evidence": 1},
        {**deepcopy(strongest), "id": "other_notice", "defender": "other_security", "evidence": 1},
    ])
    network.execute("intrude_network", connection(action="GET_IN"))
    defenders = world(network)["network_defenders"]
    assert defenders["dock_security"]["awareness"]["engineer"]["level"] == 3
    assert defenders["other_security"]["awareness"]["engineer"]["level"] == 1
    from sylvia_gm.application.network_graph import project_network_graph

    public = json.dumps(harness.thaw_json_value(project_network_graph(world(network)["network"])))
    assert "dock_security" not in public and "other_security" not in public


@pytest.mark.parametrize("fault", [None, "wrong_actor", "no_ap", "no_evidence", "wrong_side", "automatic_only"])
def test_human_defender_requires_actual_actor_turn_evidence_and_cost(network, fault):
    author_monitor(network, notice="CERTAIN", response="terminate")
    state = world(network)
    state["network_defenders"]["dock_security"]["responses"]["terminate"] = {
        "mode": "ACTOR", "kind": "END_SESSION", "target": "dock_console", "min_awareness": 2, "ap": 2,
    }
    network.execute("intrude_network", connection(action="GET_IN"))
    assert world(network)["network_sessions"]["engineer"]["nodes"]["dock_console"]["active"] is True
    request = {"actor": "vesper", "intruder": "engineer", "defender_id": "dock_security", "response_id": "terminate"}
    entities = network.state[f"{harness.builder.PACKAGE_ID}.entities"]
    if fault == "wrong_actor":
        request["actor"] = "engineer"
    elif fault == "no_ap":
        entities["vesper"]["ap"] = 0
    elif fault == "no_evidence":
        world(network)["network_defenders"]["dock_security"]["awareness"] = {}
    elif fault == "wrong_side":
        world(network)["active_side"] = "hostiles"
    elif fault == "automatic_only":
        world(network)["network_defenders"]["dock_security"]["responses"]["terminate"]["mode"] = "AUTOMATIC"
    before_ap = entities["vesper"]["ap"]
    network.execute("respond_network_defense", request)
    assert world(network)["network_sessions"]["engineer"]["nodes"]["dock_console"]["active"] is (fault is not None)
    assert network.state[f"{harness.builder.PACKAGE_ID}.entities"]["vesper"]["ap"] == before_ap - (2 if fault is None else 0)


def test_known_operation_needs_no_intrusion_roll_and_keeps_physical_effect_after_response(network):
    from sylvia_gm import dice

    author_monitor(network, notice="CERTAIN", response="shutdown")
    world(network)["network_defenders"]["dock_security"]["responses"]["shutdown"] = {
        "mode": "AUTOMATIC", "kind": "SHUTDOWN_NODE", "target": "floodgate", "min_awareness": 1,
    }
    with patch.object(dice._rng, "randint", side_effect=AssertionError("unwarranted check")):
        network.execute("operate_remote_function", args())
    gate = next(item for item in world(network)["spatial_scene"]["geometry"] if item["id"] == "security-door")
    assert gate["active"] is False
    assert world(network)["network_topology"]["nodes"]["floodgate"]["online"] is False
    assert network.check("operate_remote_function", args()) is not None
