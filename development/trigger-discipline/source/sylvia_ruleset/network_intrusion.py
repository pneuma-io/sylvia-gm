"""Authored access barriers and session creation using the ordinary declarative runtime."""

from copy import deepcopy
from typing import Any

from sylvia_ruleset import network_access, network_entry, network_security
from sylvia_ruleset import network_functions as network


def parameters(param: Any, *, pivot: bool = False) -> list[dict[str, Any]]:
    values = [
        value
        for value in network.operation_parameters(param)
        if value["name"] not in {"remote_function", "nf"}
    ]
    values.append(param("ni", "object", from_state="state.network_intrusions"))
    values.extend(network_access.parameters(param))
    if pivot:
        values.append(param("route_step", "array", items="string", min_items=3, max_items=3))
    else:
        values.append(param("action", "enum", enum=("GET_IN", "DISCOVER", "ESCALATE", "FOOTHOLD")))
    return values


def calculations(*, pivot: bool = False) -> list[dict[str, Any]]:
    values = deepcopy(network.route_calculations())
    for item in values:
        if pivot:
            item["set"]["value"] = item["set"]["value"].replace("node_id", "route_step[0]")
            item["condition"] = item["condition"].replace("node_id", "route_step[0]")
        else:
            item["condition"] = "action != 'GET_IN' and (" + item["condition"] + ")"
    return values


def checks(*, pivot: bool = False) -> list[dict[str, str]]:
    rule = "nt.links[route_step[2]].barrier" if pivot else "ni[node_id].actions[action]"
    conditions = [
        ("actor.active == true and actor.downed == false", "This actor cannot act."),
        (
            (
                "node_id in ng and ng[node_id].visible == true and '.' not in "
                "node_id and '{' not in node_id and '}' not in node_id"
            ),
            "Select a known supported device.",
        ),
        ("node_id in nt.nodes and nt.nodes[node_id].online == true", "This device is offline."),
    ]
    if not pivot:
        # Entry and an existing route have separate lifecycle checks. Group their shared
        # actor/device gate to keep the bounded player-control contract within 32 checks.
        conditions = [(" and ".join(f"({value})" for value, _ in conditions),
                       "Use an active actor and a known online device.")]
    # Check access before reading session privilege or route length. A known device may
    # have no session yet, including after disconnect or a defender's remediation.
    route = network.route_checks()[1:]
    if pivot:
        route = [
            {**item, "condition": item["condition"].replace("node_id", "route_step[0]")}
            for item in route
        ]
    else:
        route = [
            {**item, "condition": "action == 'GET_IN' or (" + item["condition"] + ")"}
            for item in route
        ]
    conditions.extend((item["condition"], item["reason"]) for item in route)
    if pivot:
        conditions.extend(
            [
                (
                    (
                        "route_step[1] == node_id and route_step[2] in nl and "
                        "nl[route_step[2]].visible == true and route_step[2] in nt.links"
                    ),
                    "Select a known connection to this device.",
                ),
                (
                    (
                        "nt.links[route_step[2]].online == true and "
                        "((nl[route_step[2]].source == route_step[0] and "
                        "nl[route_step[2]].target == node_id) or "
                        "(nl[route_step[2]].direction == 'bidirectional' and "
                        "nl[route_step[2]].target == route_step[0] and "
                        "nl[route_step[2]].source == node_id))"
                    ),
                    "The selected link does not carry traffic from that session to this device.",
                ),
                (
                    ("len(ns[_actor_id].nodes[route_step[0]].nodes) < "
                     f"{network.MAX_ROUTE_NODES} and "
                     "node_id not in ns[_actor_id].nodes[route_step[0]].nodes"),
                    "This route is full or would loop through an existing node.",
                ),
            ]
        )
    else:
        conditions.extend(
            [
                (
                    ("node_id in ni and action in ni[node_id].actions and "
                     "(method == 'INTRUDE' or action == 'GET_IN') and "
                     "(action != 'FOOTHOLD' or ('persistence' in ni[node_id].actions.FOOTHOLD and "
                     "ni[node_id].actions.FOOTHOLD.persistence.generation >= 1 and "
                     "ni[node_id].actions.FOOTHOLD.privilege <= "
                     "ns[_actor_id].nodes[node_id].privilege))"),
                    "This action has no authored access rule.",
                ),
            ]
        )
    conditions.append(
        (network_access.cost_condition(rule),
         "This method has no authored cost or the actor lacks AP.")
    )
    if not pivot:
        # GET_IN has no existing route yet, so validate its actual entry separately.
        conditions.extend(
            (f"action != 'GET_IN' or ({condition})", reason)
            for condition, reason in network_entry.live_checks("node_id")
        )
    conditions.extend((item["condition"], item["reason"]) for item in network_access.checks())
    return [{"condition": c, "reason": r} for c, r in conditions]


def pivot_session_effects() -> list[dict[str, Any]]:
    """Extend one validated parent route using the selected privilege and generation."""
    return [
        {"set": {"field": "_new_session", "value": "ns[_actor_id].nodes[route_step[0]]"}},
        {"set": {"field": "_new_nodes", "value": "_new_session.nodes"}},
        {"set": {"field": "_new_links", "value": "_new_session.links"}},
        {"set": {"field": "_new_node_generations", "value": "_new_session.node_generations"}},
        {"set": {"field": "_new_link_generations", "value": "_new_session.link_generations"}},
        {
            "set": {
                "field": "_new_session_generations",
                "value": "_new_session.session_generations",
            }
        },
        {"list_push": {"list": "_new_nodes", "value": "node_id"}},
        {"list_push": {"list": "_new_links", "value": "route_step[2]"}},
        {
            "list_push": {
                "list": "_new_node_generations",
                "value": "nt.nodes[node_id].generation",
            }
        },
        {
            "list_push": {
                "list": "_new_link_generations",
                "value": "nt.links[route_step[2]].generation",
            }
        },
        {"list_push": {"list": "_new_session_generations", "value": "_session_generation"}},
        {
            "set": {
                "field": "state.network_sessions.{_actor_id}.nodes.{node_id}",
                "value": {
                    "active": True,
                    "privilege": "_granted_privilege",
                    "entry_generation": "_new_session.entry_generation",
                    "nodes": "_new_nodes",
                    "links": "_new_links",
                    "node_generations": "_new_node_generations",
                    "link_generations": "_new_link_generations",
                    "generation": "_session_generation",
                    "session_generations": "_new_session_generations",
                },
            }
        },
    ]


def effects(*, pivot: bool = False) -> list[dict[str, Any]]:
    """One attempt spends once and changes only its declared access or knowledge result."""
    rule = "nt.links[route_step[2]].barrier" if pivot else "ni[node_id].actions[action]"
    result = [
        {
            "set": {
                "field": "state.last_network",
                "value": {
                    "node": "node_id",
                    "action": "'PIVOT'" if pivot else "action",
                    "success": False,
                    "status": "'MISSING_ROUTE_OR_AP'",
                },
            }
        },
        *calculations(pivot=pivot),
        {"set": {"field": "_intrusion_allowed", "value": True}},
        *(
            {
                "set": {"field": "_intrusion_allowed", "value": False},
                "condition": f"_intrusion_allowed == true and not ({item['condition']})",
            }
            for item in checks(pivot=pivot)
        ),
    ]
    committed = [
        *network_access.rule_effects(rule),
        {"set": {"field": "_session_generation", "value": 1}},
        {
            "set": {
                "field": "_session_generation",
                "value": "ns[_actor_id].nodes[node_id].generation + 1",
            },
            "condition": "_actor_id in ns and node_id in ns[_actor_id].nodes",
        },
        {"decrement": {"field": "{_actor_id}.ap", "amount": "_rule.ap"}},
        {"set": {"field": "_access_succeeded", "value": True}},
        {
            "condition": "method == 'INTRUDE' and _rule.check_required == true",
            "on_true": [
                {"dice": {"expression": "d20"}, "store_as": "_intrusion"},
                {
                    "set": {
                        "field": "_access_succeeded",
                        "value": (
                            "_intrusion.total + actor.skills.networking + actor.aptitudes.reason "
                            ">= _rule.difficulty"
                        ),
                    }
                },
            ],
            "on_false": [],
        },
        {"set": {"field": "state.last_network.status", "value": "'FAILED_CHECK'"}},
    ]
    succeeded = []
    if pivot:
        succeeded += pivot_session_effects()
    else:
        succeeded += [
            {
                "set": {
                    "field": "state.network_sessions.{_actor_id}.nodes.{node_id}",
                    "value": {
                        "active": True,
                        "privilege": "_granted_privilege",
                        "entry_generation": "ne[_actor_id].nodes[node_id].generation",
                        "nodes": ["node_id"],
                        "links": [],
                        "node_generations": ["nt.nodes[node_id].generation"],
                        "link_generations": [],
                        "generation": "_session_generation",
                        "session_generations": ["_session_generation"],
                    },
                },
                "condition": "action == 'GET_IN'",
            },
            {
                "set": {
                    "field": "state.network_sessions.{_actor_id}.nodes.{node_id}.privilege",
                    "value": "max(ns[_actor_id].nodes[node_id].privilege, _rule.privilege)",
                },
                "condition": "action == 'ESCALATE'",
            },
            {
                "set": {
                    "field": "state.network_footholds.{_actor_id}.nodes.{node_id}",
                    "value": {
                        "active": True,
                        "method": "_rule.persistence.method",
                        "method_generation": "_rule.persistence.generation",
                        "privilege": "_rule.privilege",
                    },
                },
                "condition": "action == 'FOOTHOLD'",
            },
        ]
    succeeded += [
        {"set": {"field": "_discover_links", "value": []}},
        {
            "set": {"field": "_discover_links", "value": "ni[node_id].visible_links"},
            "condition": (
                "'specialty.networker.entry.map_the_links' in actor.specialties"
                + ("" if pivot else " and action == 'GET_IN'")
            ),
        },
        *(
            []
            if pivot
            else [
                {
                    "set": {"field": "_discover_links", "value": "_rule.reveal_links"},
                    "condition": "action == 'DISCOVER'",
                }
            ]
        ),
        {
            "for_each": {
                "collection": "_discover_links",
                "as": "_discovered_link",
                "do": [
                    {
                        "condition": (
                            "_discovered_link in nl and nl[_discovered_link].source == node_id"
                        ),
                        "on_true": [
                            {
                                "set": {
                                    "field": "_discovered_node",
                                    "value": "nl[_discovered_link].target",
                                }
                            },
                            {
                                "set": {
                                    "field": "state.network.nodes.{_discovered_node}.visible",
                                    "value": True,
                                }
                            },
                            {
                                "set": {
                                    "field": "state.network.links.{_discovered_link}.visible",
                                    "value": True,
                                }
                            },
                        ],
                        "on_false": [],
                    },
                    {
                        "condition": (
                            "_discovered_link in nl and nl[_discovered_link].target == node_id "
                            "and nl[_discovered_link].direction == 'bidirectional'"
                        ),
                        "on_true": [
                            {
                                "set": {
                                    "field": "_discovered_node",
                                    "value": "nl[_discovered_link].source",
                                }
                            },
                            {
                                "set": {
                                    "field": "state.network.nodes.{_discovered_node}.visible",
                                    "value": True,
                                }
                            },
                            {
                                "set": {
                                    "field": "state.network.links.{_discovered_link}.visible",
                                    "value": True,
                                }
                            },
                        ],
                        "on_false": [],
                    },
                ],
            }
        },
        {"increment": {"field": "state.network.revision", "amount": 1}},
        {"set": {"field": "state.last_network.success", "value": True}},
        {"set": {"field": "state.last_network.status", "value": "'COMMITTED'"}},
        {"set": {"field": "state.focused_actor", "value": "_actor_id"}},
    ]
    committed.append(
        {"condition": "_access_succeeded == true", "on_true": succeeded, "on_false": []}
    )
    committed.extend(network_security.monitoring_effects(
        "'PIVOT'" if pivot else "action", link="route_step[2]" if pivot else "''", intrusion=True,
    ))
    result.append({"condition": "_intrusion_allowed == true", "on_true": committed, "on_false": []})
    for field in (
        "_rule",
        "_granted_privilege",
        "_new_session",
        "_new_nodes",
        "_new_links",
        "_new_node_generations",
        "_new_link_generations",
        "_new_session_generations",
        "_discover_links",
    ):
        result.append({"set": {"field": field, "value": None}})
    return result
