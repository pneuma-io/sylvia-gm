"""Package-owned remote operations over authored devices and actor-owned sessions.

This module emits ordinary Sylvia declarations. It does not execute Python in a campaign.
"""

from typing import Any

from sylvia_ruleset import network_entry, network_security

MAX_ROUTE_NODES = 8


def operation_parameters(param: Any) -> list[dict[str, Any]]:
    """Bind private rules and sessions from authority state, never from a panel request."""
    return [
        param("actor", "string", resolve="entity"),
        param("node_id", "string"),
        param("remote_function", "string"),
        param("ns", "object", from_state="state.network_sessions"),
        param("ne", "object", from_state="state.network_entries"),
        param("ng", "object", from_state="state.network.nodes"),
        param("nl", "object", from_state="state.network.links"),
        param("nf", "object", from_state="state.network_functions"),
        param("nt", "object", from_state="state.network_topology"),
        param("nx", "object", from_state="state.network_interfaces"),
        param("nm", "object", from_state="state.network_motion"),
        param("nc", "object", from_state="state.network_credentials"),
        param("nh", "object", from_state="state.network_footholds"),
        *network_security.parameters(param),
    ]


def route_calculations() -> list[dict[str, Any]]:
    """Keep route guards compact and preview each current visible device label."""
    grant = "ns[_actor_id].nodes[node_id]"
    available = "_actor_id in ns and node_id in ns[_actor_id].nodes"
    calculations = []
    for index in range(MAX_ROUTE_NODES - 1):
        calculations.extend([
            {"set": {"field": f"_r{index}", "value": f"{grant}.nodes[{index}]"},
             "condition": f"{available} and len({grant}.nodes) > {index}"},
            {"set": {"field": f"_l{index}", "value": f"{grant}.links[{index}]"},
             "condition": f"{available} and len({grant}.links) > {index}"},
        ])
    for index in range(MAX_ROUTE_NODES):
        node = f"_r{index}" if index < MAX_ROUTE_NODES - 1 else "node_id"
        calculations.append({
            "set": {"field": f"_network_route_{index}", "value": f"ng[{node}].label"},
            "condition": (
                f"{available} and len({grant}.nodes) > {index} and "
                f"{node} in ng and ng[{node}].visible == true"
            ),
        })
    return calculations


def route_checks() -> list[dict[str, str]]:
    """Validate a live actor-owned session, including every saved route generation."""
    grant = "ns[_actor_id].nodes[node_id]"
    count = f"len({grant}.nodes)"
    conditions = [
        ("actor.active == true and actor.downed == false", "This actor cannot act."),
        (
            f"_actor_id in ns and node_id in ns[_actor_id].nodes and {grant}.active == true",
            "This actor has no live session on this device.",
        ),
        (
            (
                f"{count} >= 1 and {count} <= {MAX_ROUTE_NODES} and "
                f"len({grant}.links) == {count} - 1"
            ),
            "This session has no supported route.",
        ),
        (
            f"{grant}.nodes[{count} - 1] == node_id",
            "This session does not reach the selected device.",
        ),
        (
            (
                f"ne[_actor_id].nodes[_r0].active == true and "
                f"{grant}.entry_generation == ne[_actor_id].nodes[_r0].generation"
            ),
            "The entry connection has ended. Establish a new session.",
        ),
    ]
    conditions.extend(network_entry.live_checks("_r0")[1:])
    for index in range(MAX_ROUTE_NODES):
        node = f"_r{index}" if index < MAX_ROUTE_NODES - 1 else "node_id"
        conditions.append(
            (
                (
                    f"{count} <= {index} or (ng[{node}].visible == true and "
                    f"nt.nodes[{node}].online == true and "
                    f"ns[_actor_id].nodes[{node}].active == true and "
                    f"{grant}.node_generations[{index}] == nt.nodes[{node}].generation "
                    f"and {grant}.session_generations[{index}] == "
                    f"ns[_actor_id].nodes[{node}].generation)"
                ),
                "A device or session in this route has ended. Establish a new route.",
            )
        )
    for index in range(MAX_ROUTE_NODES - 1):
        link_id = f"_l{index}"
        link, source = f"nl[{link_id}]", f"_r{index}"
        target = f"_r{index + 1}" if index < MAX_ROUTE_NODES - 2 else "node_id"
        conditions.append(
            (
                (
                    f"{count} <= {index + 1} or (nt.links[{link_id}].online == true "
                    f"and {grant}.link_generations[{index}] == "
                    f"nt.links[{link_id}].generation and {link}.visible == true and "
                    f"(({link}.source == {source} and {link}.target == {target}) or "
                    f"({link}.direction == 'bidirectional' and {link}.target == {source} "
                    f"and {link}.source == {target})))"
                ),
                "A known connection in this route is no longer usable.",
            )
        )
    return [{"condition": condition, "reason": reason} for condition, reason in conditions]


def operation_preview_calculations() -> list[dict[str, Any]]:
    """Expose the whole route and function cost within the host's bounded scalar budget."""
    return [
        *route_calculations(),
        {"set": {"field": "_network_function_ap",
                 "value": "nf[node_id].functions[remote_function].ap"},
         "condition": "node_id in nf and remote_function in nf[node_id].functions"},
    ]


def operation_checks() -> list[dict[str, str]]:
    """Keep direct-control availability and committed operation guards identical."""
    grant = "ns[_actor_id].nodes[node_id]"
    function = "nf[node_id].functions[remote_function]"
    conditions = [
        (
            (
                "node_id in ng and ng[node_id].visible == true and remote_function "
                "in ng[node_id].functions and "
                "ng[node_id].functions[remote_function].visible == true"
            ),
            "This function is not known.",
        ),
        (
            (
                "node_id in nf and remote_function in nf[node_id].functions and "
                "ng[node_id].functions[remote_function].enabled == true"
            ),
            "This device does not currently expose that function.",
        ),
        (
            f"{function}.kind == 'geometry' and {function}.map_id == map_id",
            "This function has no supported effect on the current map.",
        ),
        (f"{grant}.privilege >= {function}.privilege", "This actor lacks the required privilege."),
        (
            f"{function}.ap >= 0 and actor.ap >= {function}.ap",
            "This actor lacks the function's action-point cost.",
        ),
    ]
    return route_checks() + [{"condition": c, "reason": r} for c, r in conditions]


def operation_effects() -> list[dict[str, Any]]:
    """Apply the authored physical patch and AP cost in the existing atomic command."""
    checks = operation_checks()
    return [
        {
            "set": {
                "field": "state.last_network",
                "value": {
                    "action": "'OPERATE'",
                    "node": "node_id",
                    "remote_function": "remote_function",
                    "success": False,
                    "status": "'OPERATE_REJECTED'",
                },
            }
        },
        *route_calculations(),
        {"set": {"field": "_operation_allowed", "value": True}},
        *(
            {
                "set": {"field": "_operation_allowed", "value": False},
                "condition": f"_operation_allowed == true and not ({check['condition']})",
            }
            for check in checks
        ),
        {
            "condition": "_operation_allowed == true",
            "on_true": [
                {"set": {"field": "_function", "value": "nf[node_id].functions[remote_function]"}},
                {
                    "spatial_query": {"scene": "state.spatial_scene", "operation": "snapshot"},
                    "store_as": "_device_snapshot",
                },
                {
                    "spatial_mutation": {
                        "scene": "state.spatial_scene",
                        "map_id": "{map_id}",
                        "expected_revision": "{_device_snapshot.geometry_revision}",
                        "target": {"kind": "geometry", "id": "{_function.geometry_id}"},
                        "patch": {
                            "active": "{_function.patch.active}",
                            "movement": "{_function.patch.movement}",
                            "visibility": "{_function.patch.visibility}",
                        },
                    },
                    "store_as": "_device_mutation",
                },
                {"decrement": {"field": "{_actor_id}.ap", "amount": "_function.ap"}},
                {
                    "set": {
                        "field": "state.network.nodes.{node_id}.functions.{remote_function}.state",
                        "value": "'operated'",
                    }
                },
                {"increment": {"field": "state.network.revision", "amount": 1}},
                {
                    "set": {
                        "field": "state.last_network.geometry_revision",
                        "value": "_device_mutation.geometry_revision",
                    }
                },
                {"set": {"field": "state.last_network.success", "value": True}},
                {"set": {"field": "state.last_network.status", "value": "'OPERATED'"}},
                {"set": {"field": "state.focused_actor", "value": "_actor_id"}},
                {"set": {"field": "_rule", "value": "_function"}},
                *network_security.monitoring_effects("'OPERATE'"),
                {"set": {"field": "_rule", "value": None}},
            ],
            "on_false": [],
        },
        # Do not return the private function binding in the tool's context.
        {"set": {"field": "_function", "value": None}},
    ]
