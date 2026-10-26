"""Private authored network mistakes fail admission before any game action or provider use."""

import io
import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from sylvia_gm.application.plan_state_admission import validate_plan_state
from sylvia_gm.domain.content_validation import ContentShapeError
from sylvia_gm.infrastructure.package_archive import admit_package_archive
from test_sylvia_authored_campaign import _fresh_package
from test_sylvia_network_access import author_alternate_route
from test_sylvia_network_functions import harness, world
from test_sylvia_network_functions import (
    network as network,  # noqa: PLC0414 - re-export pytest fixture
)
from test_sylvia_network_security import author_monitor
from test_sylvia_remote_entry import remote as remote  # noqa: PLC0414


@pytest.fixture(scope="module")
def manifest():
    parameters = harness.default_manifest().seed_set()
    with admit_package_archive(io.BytesIO(_fresh_package(parameters))) as package:
        return package.manifests[0]


def plan(state):
    return {"initial_state": {"mechanics": [{
        "namespace": harness.builder.PACKAGE_ID, "key": "state", "value": state,
    }]}}


def test_authored_network_shapes_and_bounded_monitoring_pass(manifest, network):
    state = world(network)
    author_monitor(network)
    before = deepcopy(state)
    validate_plan_state(plan(state), manifest)
    assert state == before
    values = harness.default_manifest().seed_set().values
    conformance = {**harness.builder._conformance_network_state(values),
                   "spatial_scene": harness.builder._scene()}
    validate_plan_state(plan(conformance), manifest)
    # Ordinary worlds and movement counters need no fabricated network collections.
    validate_plan_state(plan({}), manifest)
    validate_plan_state(plan({"network_motion": {"explorer": 1}}), manifest)


@pytest.mark.parametrize("include_cost", [False, True])
def test_route_persistence_cost_and_monitor_are_valid_authoring(manifest, network, include_cost):
    if include_cost:
        author_alternate_route(network)
        world(network)["network_intrusions"]["floodgate"]["reentry"]["ROUTE_PERSISTENCE"]["visibility"] = "HIGH"
    author_monitor(network)
    state = world(network)
    state["network_monitors"][0]["actions"] = ["ROUTE_PERSISTENCE"]
    validate_plan_state(plan(state), manifest)
    if include_cost:
        request = {"actor": "engineer", "node_id": "floodgate",
                   "route_step": ["dock_console", "floodgate", "backup_cable"]}
        network.execute("preserve_network_route", request)
        assert world(network)["last_network"]["success"]
        validate_plan_state(plan(world(network)), manifest)


def test_remote_service_interface_is_valid_authoring(manifest, remote):
    validate_plan_state(plan(world(remote)), manifest)


@pytest.mark.parametrize("fault, suffix", [
    ("cost", ".network_functions.floodgate.functions.raise_gate.ap"),
    ("geometry", ".network_functions.floodgate.functions.raise_gate.geometry_id"),
    ("map", ".network_functions.floodgate.functions.raise_gate.map_id"),
    ("node", ".network_intrusions.missing"),
    ("link", ".network_sessions.engineer.nodes.floodgate.links[0]"),
    ("wrong_function_node", ".network_functions.dock_console.functions.raise_gate"),
    ("generation", ".network_topology.nodes.floodgate.generation"),
    ("missing_collection", ".network_credentials"),
    ("remote_connection", ".network_interfaces.dock_console.connections"),
    ("foothold_method", ".network_intrusions.floodgate.actions.FOOTHOLD.persistence"),
    ("monitor_defender", ".network_monitors[0].defender"),
])
def test_broken_private_records_fail_with_paths_and_no_private_values(manifest, network, fault, suffix):
    state = world(network)
    function = state["network_functions"]["floodgate"]["functions"]["raise_gate"]
    if fault == "cost":
        function["ap"] = -1
    elif fault in ("geometry", "map"):
        function[f"{fault}_id"] = "PRIVATE_VALUE_DO_NOT_ECHO"
    elif fault == "node":
        state["network_intrusions"]["missing"] = state["network_intrusions"].pop("floodgate")
    elif fault == "link":
        state["network_sessions"]["engineer"]["nodes"]["floodgate"]["links"] = ["PRIVATE_VALUE_DO_NOT_ECHO"]
    elif fault == "wrong_function_node":
        state["network_functions"]["dock_console"] = state["network_functions"].pop("floodgate")
    elif fault == "generation":
        state["network_topology"]["nodes"]["floodgate"]["generation"] = 0
    elif fault == "missing_collection":
        del state["network_credentials"]
    elif fault == "remote_connection":
        state["network_interfaces"]["dock_console"]["kind"] = "remote_service"
    elif fault == "foothold_method":
        del state["network_intrusions"]["floodgate"]["actions"]["FOOTHOLD"]["persistence"]
    else:
        author_monitor(network)
        state["network_monitors"][0]["defender"] = "PRIVATE_VALUE_DO_NOT_ECHO"
    before = deepcopy(state)
    with pytest.raises(ContentShapeError) as failure:
        validate_plan_state(plan(state), manifest)
    diagnostic = failure.value.diagnostics[0]
    assert diagnostic.path == "$.initial_state.mechanics[0].value" + suffix
    assert "PRIVATE_VALUE_DO_NOT_ECHO" not in str(failure.value)
    assert state == before
