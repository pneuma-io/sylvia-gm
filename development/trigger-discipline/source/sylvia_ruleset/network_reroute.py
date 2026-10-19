"""One ROUTE PERSISTENCE transition after a link loss; whole-chain REROUTE remains separate."""

from typing import Any

from sylvia_ruleset import network_intrusion, network_security


def effects() -> list[dict[str, Any]]:
    """Retain one existing grant through a known alternative, never revive a terminated session."""
    checks = [
        item["condition"] for item in network_intrusion.checks(pivot=True)
        if not item["reason"].startswith("This method has")
    ]
    checks.extend([
        "method == 'INTRUDE' and 'specialty.networker.tier_3.route_persistence' in actor.specialties",
        ("node_id in ni and 'ROUTE_PERSISTENCE' in ni[node_id].reentry and "
         "ni[node_id].reentry.ROUTE_PERSISTENCE.ap >= 0 and "
         "actor.ap >= ni[node_id].reentry.ROUTE_PERSISTENCE.ap"),
        "_actor_id in ns and node_id in ns[_actor_id].nodes and ns[_actor_id].nodes[node_id].active == true",
        ("ns[_actor_id].nodes[node_id].nodes[0] == ns[_actor_id].nodes[route_step[0]].nodes[0] and "
         "ns[_actor_id].nodes[node_id].entry_generation == "
         "ns[_actor_id].nodes[route_step[0]].entry_generation"),
        ("len(ns[_actor_id].nodes[node_id].nodes) >= 2 and "
         "len(ns[_actor_id].nodes[node_id].nodes) <= 8 and "
         "len(ns[_actor_id].nodes[node_id].links) == len(ns[_actor_id].nodes[node_id].nodes) - 1"),
    ])
    return [
        {"set": {"field": "state.last_network", "value": {
            "action": "'ROUTE_PERSISTENCE'", "node": "node_id", "success": False,
            "status": "'NO_SURVIVING_SESSION_OR_ROUTE'",
        }}},
        *network_intrusion.calculations(pivot=True),
        {"set": {"field": "_reroute_allowed", "value": True}},
        *({"set": {"field": "_reroute_allowed", "value": False},
           "condition": f"_reroute_allowed == true and not ({condition})"}
          for condition in checks),
        {"condition": "_reroute_allowed == true", "on_true": [
            {"set": {"field": "_old_session", "value": "ns[_actor_id].nodes[node_id]"}},
            {"set": {"field": "_old_nodes", "value": "_old_session.nodes"}},
            {"set": {"field": "_old_links", "value": "_old_session.links"}},
            {"set": {"field": "_old_index", "value": 0}},
            {"for_each": {"collection": "_old_nodes", "as": "_old_node", "do": [
                {"set": {"field": "_reroute_allowed", "value": False}, "condition": (
                    "_reroute_allowed == true and not (_old_node in nt.nodes and "
                    "nt.nodes[_old_node].online == true and _old_node in ns[_actor_id].nodes and "
                    "ns[_actor_id].nodes[_old_node].active == true and "
                    "nt.nodes[_old_node].generation == _old_session.node_generations[_old_index] and "
                    "ns[_actor_id].nodes[_old_node].generation == _old_session.session_generations[_old_index])"
                )},
                {"set": {"field": "_old_index", "value": "_old_index + 1"}},
            ]}},
            {"set": {"field": "_old_index", "value": 0}},
            {"set": {"field": "_lost_links", "value": 0}},
            {"for_each": {"collection": "_old_links", "as": "_old_link", "do": [
                {"set": {"field": "_lost_links", "value": "_lost_links + 1"}, "condition": (
                    "_old_link not in nt.links or nt.links[_old_link].online == false or "
                    "nt.links[_old_link].generation != _old_session.link_generations[_old_index]"
                )},
                {"set": {"field": "_old_index", "value": "_old_index + 1"}},
            ]}},
            {"condition": "_reroute_allowed == true and _lost_links == 1", "on_true": [
                {"set": {"field": "_rule", "value": "ni[node_id].reentry.ROUTE_PERSISTENCE"}},
                {"set": {"field": "_session_generation", "value": "_old_session.generation"}},
                {"set": {"field": "_granted_privilege", "value": "_old_session.privilege"}},
                *network_intrusion.pivot_session_effects(),
                {"decrement": {"field": "{_actor_id}.ap", "amount": "_rule.ap"}},
                {"set": {"field": "state.last_network.success", "value": True}},
                {"set": {"field": "state.last_network.status", "value": "'REROUTED'"}},
                {"increment": {"field": "state.network.revision", "amount": 1}},
                *network_security.monitoring_effects("'ROUTE_PERSISTENCE'", link="route_step[2]"),
            ], "on_false": []},
        ], "on_false": []},
        *({"set": {"field": field, "value": None}} for field in (
            "_old_session", "_old_nodes", "_old_links", "_old_node", "_old_link", "_rule",
            "_new_session", "_new_nodes", "_new_links", "_new_node_generations",
            "_new_link_generations", "_new_session_generations", "_granted_privilege",
        )),
    ]
