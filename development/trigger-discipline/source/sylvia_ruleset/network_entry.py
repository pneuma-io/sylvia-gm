"""Real local interfaces or authored remote-service connections, without implied privilege."""

from typing import Any


def parameters(param: Any) -> list[dict[str, Any]]:
    """Expose only the selected actor/device; interface and connection facts are state-bound."""
    return [
        param("actor", "string", resolve="entity"),
        param("node_id", "string"),
        param("action", "enum", enum=("CONNECT", "DISCONNECT")),
        param("ne", "object", from_state="state.network_entries"),
        param("nx", "object", from_state="state.network_interfaces"),
        param("nm", "object", from_state="state.network_motion"),
        param("nt", "object", from_state="state.network_topology"),
        param("ng", "object", from_state="state.network.nodes"),
    ]


def live_checks(node: str) -> list[tuple[str, str]]:
    """Check the actual interface, its generation and the actor's continuous presence."""
    entry = f"ne[_actor_id].nodes[{node}]"
    interface = f"nx[{node}]"
    return [
        (
            f"_actor_id in ne and {node} in ne[_actor_id].nodes and {entry}.active == true",
            "This actor has no active entry connection.",
        ),
        (
            (f"{node} in nx and {interface}.active == true and "
            f"{entry}.interface_generation == {interface}.generation and "
            f"{entry}.node_generation == nt.nodes[{node}].generation"),
            "The entry interface has changed or ended. Reconnect to it.",
        ),
        (
            _connection_condition(node),
            "The actor left the local interface or the remote connection ended. Reconnect before establishing access.",
        ),
        (
            (f"({interface}.kind != 'local_stationary' or (_actor_id in nm and "
             f"{entry}.motion_generation == nm[_actor_id])) and "
             f"({interface}.kind != 'remote_service' or "
             f"{entry}.connection_generation == {interface}.connections[_actor_id].generation)"),
            "This connection was interrupted or replaced. Reconnect before establishing access.",
        ),
    ]


def _connection_condition(node: str) -> str:
    """Remote reachability is an actor-bound authored connection, never inferred from visibility."""
    interface = f"nx[{node}]"
    local = (
        f"{interface}.kind == 'local_stationary' and {interface}.map_id == map_id and "
        f"actor.position_cell[0] == {interface}.position_cell[0] and "
        f"actor.position_cell[1] == {interface}.position_cell[1]"
    )
    remote = (
        f"{interface}.kind == 'remote_service' and 'connections' in {interface} and "
        f"_actor_id in {interface}.connections and "
        f"{interface}.connections[_actor_id].active == true and "
        f"{interface}.connections[_actor_id].generation >= 1"
    )
    return f"(({local}) or ({remote}))"


def checks() -> list[dict[str, str]]:
    """Keep eligibility and committed entry guards identical without granting privilege."""
    conditions = [
        ("actor.active == true and actor.downed == false", "This actor cannot act."),
        (
            ("node_id in ng and ng[node_id].visible == true and '.' not in node_id and "
            "'{' not in node_id and '}' not in node_id"),
            "Select a known supported device.",
        ),
        (
            ("action == 'DISCONNECT' or (node_id in nt.nodes and nt.nodes[node_id].online == true "
            "and node_id in nx and nx[node_id].active == true)"),
            "This device has no active entry interface.",
        ),
        (
            "action == 'DISCONNECT' or " + _connection_condition("node_id"),
            "Move to the local interface or establish this actor's actual remote-service connection first.",
        ),
        (
            "action == 'DISCONNECT' or (nx[node_id].ap >= 0 and actor.ap >= nx[node_id].ap)",
            "This actor lacks the connection's authored AP cost.",
        ),
        (
            ("action == 'CONNECT' or (_actor_id in ne and node_id in ne[_actor_id].nodes "
            "and ne[_actor_id].nodes[node_id].active == true)"),
            "This actor has no connection to disconnect.",
        ),
    ]
    return [{"condition": condition, "reason": reason} for condition, reason in conditions]


def effects() -> list[dict[str, Any]]:
    """Replace the connection generation, leaving knowledge and physical effects untouched."""
    return [
        {"set": {"field": "state.last_network", "value": {
            "action": "action", "node": "node_id", "success": False,
            "status": "'ENTRY_UNAVAILABLE'",
        }}},
        {"set": {"field": "_entry_allowed", "value": True}},
        *({"set": {"field": "_entry_allowed", "value": False},
           "condition": f"_entry_allowed == true and not ({check['condition']})"}
          for check in checks()),
        {"condition": "_entry_allowed == true", "on_true": [
            {"set": {"field": "_entry_generation", "value": 1}},
            {"set": {"field": "_entry_generation",
                     "value": "ne[_actor_id].nodes[node_id].generation + 1"},
             "condition": "_actor_id in ne and node_id in ne[_actor_id].nodes"},
            {"condition": "action == 'CONNECT'", "on_true": [
                {"set": {"field": "_connection_generation", "value": 0}},
                {"set": {"field": "_connection_generation",
                         "value": "nx[node_id].connections[_actor_id].generation"},
                 "condition": "nx[node_id].kind == 'remote_service'"},
                {"set": {"field": "_motion_generation", "value": 0}},
                {"set": {"field": "_motion_generation", "value": "nm[_actor_id]"},
                 "condition": "_actor_id in nm"},
                {"set": {"field": "state.network_motion.{_actor_id}", "value": 0},
                 "condition": "_actor_id not in nm"},
                {"set": {"field": "state.network_entries.{_actor_id}.nodes.{node_id}",
                         "value": {
                             "active": True, "generation": "_entry_generation",
                             "interface_generation": "nx[node_id].generation",
                             "node_generation": "nt.nodes[node_id].generation",
                             "motion_generation": "_motion_generation",
                             "connection_generation": "_connection_generation",
                         }}},
                {"decrement": {"field": "{_actor_id}.ap", "amount": "nx[node_id].ap"}},
            ], "on_false": [
                {"set": {"field": "state.network_entries.{_actor_id}.nodes.{node_id}.active",
                         "value": False}},
                {"set": {"field": "state.network_entries.{_actor_id}.nodes.{node_id}.generation",
                         "value": "_entry_generation"}},
            ]},
            {"increment": {"field": "state.network.revision", "amount": 1}},
            {"set": {"field": "state.last_network.success", "value": True}},
            {"set": {"field": "state.last_network.status", "value": "action"}},
        ], "on_false": []},
    ]


def movement_effects(actor_id: str = "_actor_id") -> list[dict[str, Any]]:
    """Invalidate stationary access without requiring a network in an ordinary physical scene."""
    return [
        {"increment": {"field": f"state.network_motion.{{{actor_id}}}", "amount": 1}},
    ]
