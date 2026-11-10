"""Quiet discovery keeps actual access boundaries and monitoring consequences."""

from copy import deepcopy
from unittest.mock import patch

import pytest

from tests import test_sylvia_network_security as support

network = support.network


@pytest.mark.parametrize(
    ("authored", "resolved"),
    [
        (None, "LOW"),
        ("LOW", "LOW"),
        ("STANDARD", "LOW"),
        ("HIGH", "STANDARD"),
    ],
)
def test_quiet_discovery_lowers_one_band_and_preserves_the_authored_rule(
    network, authored, resolved
):
    state = support.world(network)
    entities = network.state["pneuma-io.trigger-discipline.entities"]
    entities["engineer"]["specialties"] = ["specialty.networker.tier_1.quiet_enumeration"]
    state["network"]["nodes"]["floodgate"]["visible"] = False
    state["network"]["links"]["dock_cable"]["visible"] = False
    rule = state["network_intrusions"]["dock_console"]["actions"]["DISCOVER"]
    if authored:
        rule["visibility"] = authored
    before = deepcopy(state["network_intrusions"])
    before_ap = entities["engineer"]["ap"]
    result = network.execute(
        "discover_network_quietly",
        {
            "actor": "engineer",
            "node_id": "dock_console",
        },
    )
    after = support.world(network)
    assert after["last_network"]["success"]
    assert result["context"]["_quiet_visibility_after"] == resolved
    assert result["result_card"]["summary"][0]["value"] == "Discovery completed"
    assert after["network_intrusions"] == before
    assert after["network"]["nodes"]["floodgate"]["visible"]
    assert network.state["pneuma-io.trigger-discipline.entities"]["engineer"]["ap"] == before_ap - 1


def test_lower_visibility_remains_detectable_by_relevant_certain_monitoring(network):
    support.author_monitor(network, notice="CERTAIN")
    network.state["pneuma-io.trigger-discipline.entities"]["engineer"]["specialties"] = [
        "specialty.networker.tier_1.quiet_enumeration",
    ]
    result = network.execute(
        "discover_network_quietly",
        {
            "actor": "engineer",
            "node_id": "dock_console",
        },
    )
    assert result["context"]["_quiet_visibility_after"] == "LOW"
    assert (
        support.world(network)["network_defenders"]["dock_security"]["awareness"]["engineer"][
            "level"
        ]
        == 2
    )


@pytest.mark.parametrize("quiet", [False, True])
def test_lower_band_changes_the_real_uncertain_monitoring_check(network, quiet):
    from sylvia_gm import dice

    actor = network.state["pneuma-io.trigger-discipline.entities"]["engineer"]
    actor["specialties"] = ["specialty.networker.tier_1.quiet_enumeration"]
    total = 10 + actor["skills"]["networking"] + actor["aptitudes"]["reason"]
    support.author_monitor(network, difficulty=total + 1)
    params = {"actor": "engineer", "node_id": "dock_console"}
    if not quiet:
        params["action"] = "DISCOVER"
    with patch.object(dice._rng, "randint", return_value=10):
        network.execute("discover_network_quietly" if quiet else "intrude_network", params)
    awareness = support.world(network)["network_defenders"]["dock_security"]["awareness"]
    assert ("engineer" in awareness) is (not quiet)


@pytest.mark.parametrize("reason", ["specialty", "session", "connection", "ap"])
def test_no_knowledge_or_resource_change_without_actual_prerequisites(network, reason):
    state = support.world(network)
    actor = network.state["pneuma-io.trigger-discipline.entities"]["engineer"]
    actor["specialties"] = ["specialty.networker.tier_1.quiet_enumeration"]
    if reason == "specialty":
        actor["specialties"] = []
    elif reason == "session":
        state["network_sessions"] = {}
    elif reason == "connection":
        state["network_entries"]["engineer"]["nodes"]["dock_console"]["active"] = False
    else:
        actor["ap"] = 0
    before = deepcopy(network.state)
    result = network.execute(
        "discover_network_quietly",
        {
            "actor": "engineer",
            "node_id": "dock_console",
        },
    )
    assert result["result_card"]["summary"][0]["value"] == "Discovery prerequisites not met"
    assert not support.world(network)["last_network"]["success"]
    before["pneuma-io.trigger-discipline.state"]["last_network"] = support.world(network)[
        "last_network"
    ]
    assert network.state == before
