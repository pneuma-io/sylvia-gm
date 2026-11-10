"""Saved authentication material is useful only through real routes and current validity."""

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


def authorize_methods(test):
    """Explicit synthetic costs and grants are fixtures, not campaign defaults."""
    state = world(test)
    state["network_credentials"] = {"dock_pass": {
        "active": True, "generation": 1, "held_by": {"engineer": 1}, "grants": {"floodgate": 2},
    }}
    for node in state["network_intrusions"].values():
        node["reentry"] = {
            "CREDENTIAL": {"ap": 1}, "FOOTHOLD": {"ap": 1},
        }


def pivot(**changes):
    return {"actor": "engineer", "node_id": "floodgate", "route_step": ["dock_console", "floodgate", "dock_cable"], **changes}


def test_scoped_credential_authenticates_without_intrusion_and_grants_only_its_privilege(network):
    from sylvia_gm import dice

    authorize_methods(network)
    world(network)["network_sessions"]["engineer"]["nodes"].pop("floodgate")
    request = pivot(method="CREDENTIAL", credential_id="dock_pass")
    assert network.check("pivot_network", request) is None
    with patch.object(dice._rng, "randint", side_effect=AssertionError("authorized authentication rolled")):
        network.execute("pivot_network", request)
    grant = world(network)["network_sessions"]["engineer"]["nodes"]["floodgate"]
    assert grant["privilege"] == 2
    assert network.check("operate_remote_function", args()) is None
    assert network.state[f"{harness.builder.PACKAGE_ID}.entities"]["engineer"]["ap"] == 5


@pytest.mark.parametrize("fault", ["revoked", "rotated", "wrong_holder", "wrong_scope", "missing_link", "hidden_link", "no_cost"])
def test_credential_cannot_invent_scope_possession_validity_or_connectivity(network, fault):
    authorize_methods(network)
    state = world(network)
    credential = state["network_credentials"]["dock_pass"]
    if fault == "revoked":
        credential["active"] = False
    elif fault == "rotated":
        credential["generation"] = 2
    elif fault == "wrong_holder":
        credential["held_by"] = {"vesper": 1}
    elif fault == "wrong_scope":
        credential["grants"] = {"dock_console": 2}
    elif fault == "missing_link":
        state["network_topology"]["links"]["dock_cable"]["online"] = False
    elif fault == "hidden_link":
        state["network"]["links"]["dock_cable"]["visible"] = False
    else:
        state["network_intrusions"]["floodgate"]["reentry"] = {}
    before = deepcopy(network.state)
    request = pivot(method="CREDENTIAL", credential_id="dock_pass")
    assert network.check("pivot_network", request) is not None
    network.execute("pivot_network", request)
    assert world(network)["network_sessions"] == before[f"{harness.builder.PACKAGE_ID}.state"]["network_sessions"]
    assert network.state[f"{harness.builder.PACKAGE_ID}.entities"] == before[f"{harness.builder.PACKAGE_ID}.entities"]


def test_foothold_reentry_survives_disconnect_and_restart_but_not_remediation(network):
    from sylvia_gm import dice

    authorize_methods(network)
    state = world(network)
    state["network_intrusions"]["floodgate"]["actions"]["FOOTHOLD"]["privilege"] = 2
    network.execute("intrude_network", {"actor": "engineer", "node_id": "floodgate", "action": "FOOTHOLD"})
    marker = deepcopy(world(network)["network_footholds"])
    network.execute("network_connection", connection(action="DISCONNECT"))
    # Ordinary node restart ends the old session but does not itself remediate an authored implant.
    world(network)["network_topology"]["nodes"]["floodgate"]["generation"] += 1
    network.execute("network_connection", connection())
    network.execute("intrude_network", connection(action="GET_IN"))
    network.state = json.loads(json.dumps(network.state))
    assert world(network)["network_footholds"] == marker
    assert network.check("operate_remote_function", args()) is not None
    request = pivot(method="FOOTHOLD")
    assert network.check("pivot_network", request) is None
    with patch.object(dice._rng, "randint", side_effect=AssertionError("surviving foothold rolled intrusion")):
        network.execute("pivot_network", request)
    assert network.check("operate_remote_function", args()) is None
    world(network)["network_intrusions"]["floodgate"]["actions"]["FOOTHOLD"]["persistence"]["generation"] += 1
    assert network.check("pivot_network", request) is not None


def test_foothold_requires_explicit_method_and_cannot_upgrade_privilege(network):
    rule = world(network)["network_intrusions"]["floodgate"]["actions"]["FOOTHOLD"]
    attempt = {"actor": "engineer", "node_id": "floodgate", "action": "FOOTHOLD"}
    rule["privilege"] = 3
    assert network.check("intrude_network", attempt) is not None
    rule["privilege"] = 2
    rule.pop("persistence")
    assert network.check("intrude_network", attempt) is not None


@pytest.mark.parametrize("kind", ["REVOKE_CREDENTIAL", "REMOVE_FOOTHOLD"])
def test_defender_removes_future_access_without_erasing_stolen_information(network, kind):
    authorize_methods(network)
    state = world(network)
    state["network_footholds"] = {"engineer": {"nodes": {"floodgate": {
        "active": True, "method": "harbor_implant", "method_generation": 1, "privilege": 1,
    }}}}
    state["network_defenders"] = {"dock_security": {
        "active": True, "operator": "vesper", "awareness": {"engineer": {"level": 3}},
        "responses": {"remediate": {"mode": "ACTOR", "kind": kind, "ap": 1,
                                    "target": "dock_pass" if kind == "REVOKE_CREDENTIAL" else "floodgate",
                                    "min_awareness": 2}},
    }}
    network.execute("respond_network_defense", {
        "actor": "vesper", "intruder": "engineer", "defender_id": "dock_security", "response_id": "remediate",
    })
    state = world(network)
    assert state["last_defense"]["success"] is True
    assert state["network_credentials"]["dock_pass"]["held_by"]["engineer"] == 1
    assert state["network"]["nodes"]["floodgate"]["visible"] is True
    method = "CREDENTIAL" if kind == "REVOKE_CREDENTIAL" else "FOOTHOLD"
    assert network.check("pivot_network", pivot(method=method, credential_id="dock_pass")) is not None
    # Existing sessions end through END_SESSION or route loss, not a fabricated blanket erasure.
    assert network.check("operate_remote_function", args()) is None


def author_alternate_route(test):
    state = world(test)
    state["network"]["links"]["backup_cable"] = {
        **deepcopy(state["network"]["links"]["dock_cable"]), "visible": True,
    }
    state["network_topology"]["links"]["backup_cable"] = deepcopy(state["network_topology"]["links"]["dock_cable"])
    state["network_topology"]["links"]["dock_cable"]["online"] = False
    state["network_intrusions"]["floodgate"]["reentry"]["ROUTE_PERSISTENCE"] = {"ap": 1}
    test.state[f"{harness.builder.PACKAGE_ID}.entities"]["engineer"]["specialties"].append(
        "specialty.networker.tier_3.route_persistence"
    )
    return pivot(route_step=["dock_console", "floodgate", "backup_cable"])


def test_route_persistence_retains_one_live_grant_on_a_real_discovered_alternative(network):
    from sylvia_gm import dice

    request = author_alternate_route(network)
    before = deepcopy(world(network)["network_sessions"]["engineer"]["nodes"]["floodgate"])
    assert network.check("operate_remote_function", args()) is not None
    with patch.object(dice._rng, "randint", side_effect=AssertionError("authorized reroute rolled intrusion")):
        network.execute("preserve_network_route", request)
    after = world(network)["network_sessions"]["engineer"]["nodes"]["floodgate"]
    assert after["privilege"] == before["privilege"]
    assert after["generation"] == before["generation"]
    assert after["links"] == ["backup_cable"]
    assert network.check("operate_remote_function", args()) is None
    assert network.state[f"{harness.builder.PACKAGE_ID}.entities"]["engineer"]["ap"] == 5
    # Repeating a completed transition does not purchase any additional benefit.
    network.execute("preserve_network_route", request)
    assert world(network)["last_network"]["success"] is False
    assert network.state[f"{harness.builder.PACKAGE_ID}.entities"]["engineer"]["ap"] == 5


@pytest.mark.parametrize("fault", ["ended_session", "entry_ended", "node_replaced", "missing_specialty", "missing_cost", "unknown_alternative", "offline_alternative"])
def test_route_persistence_cannot_revive_expired_access_or_invent_an_alternative(network, fault):
    request = author_alternate_route(network)
    state = world(network)
    if fault == "ended_session":
        state["network_sessions"]["engineer"]["nodes"]["floodgate"]["active"] = False
    elif fault == "entry_ended":
        state["network_entries"]["engineer"]["nodes"]["dock_console"]["generation"] += 1
    elif fault == "node_replaced":
        state["network_topology"]["nodes"]["floodgate"]["generation"] += 1
    elif fault == "missing_specialty":
        network.state[f"{harness.builder.PACKAGE_ID}.entities"]["engineer"]["specialties"] = []
    elif fault == "missing_cost":
        state["network_intrusions"]["floodgate"]["reentry"] = {}
    elif fault == "unknown_alternative":
        state["network"]["links"]["backup_cable"]["visible"] = False
    else:
        state["network_topology"]["links"]["backup_cable"]["online"] = False
    before = deepcopy(network.state)
    network.execute("preserve_network_route", request)
    assert world(network)["last_network"]["success"] is False
    assert world(network)["network_sessions"] == before[f"{harness.builder.PACKAGE_ID}.state"]["network_sessions"]
    assert network.state[f"{harness.builder.PACKAGE_ID}.entities"] == before[f"{harness.builder.PACKAGE_ID}.entities"]
