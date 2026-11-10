"""Credential and explicit foothold re-entry through an already valid physical route."""

from typing import Any


def parameters(param: Any) -> list[dict[str, Any]]:
    """Choose access material without accepting a caller-supplied validity or privilege claim."""
    return [
        param("method", "enum", enum=("INTRUDE", "CREDENTIAL", "FOOTHOLD"),
              required=False, default="INTRUDE"),
        param("credential_id", "string", required=False, default=""),
    ]


def checks() -> list[dict[str, str]]:
    """A credential needs current possession/scope; a foothold needs its real surviving method."""
    return [
        {"condition": (
            "method != 'CREDENTIAL' or (credential_id in nc and "
            "nc[credential_id].active == true and _actor_id in nc[credential_id].held_by and "
            "nc[credential_id].held_by[_actor_id] == nc[credential_id].generation and "
            "node_id in nc[credential_id].grants)"
        ), "reason": "This actor has no current credential valid for this device."},
        {"condition": (
            "method != 'FOOTHOLD' or (_actor_id in nh and node_id in nh[_actor_id].nodes and "
            "nh[_actor_id].nodes[node_id].active == true and "
            "nh[_actor_id].nodes[node_id].method == ni[node_id].actions.FOOTHOLD.persistence.method and "
            "nh[_actor_id].nodes[node_id].method_generation == "
            "ni[node_id].actions.FOOTHOLD.persistence.generation)"
        ), "reason": "This actor has no surviving foothold on this device."},
    ]


def cost_condition(ordinary: str) -> str:
    """Keep each method's cost explicit; persistent access never invents a discount."""
    return (
        f"(method == 'INTRUDE' and {ordinary}.ap >= 0 and actor.ap >= {ordinary}.ap) or "
        "(method != 'INTRUDE' and node_id in ni and method in ni[node_id].reentry and "
        "ni[node_id].reentry[method].ap >= 0 and actor.ap >= ni[node_id].reentry[method].ap)"
    )


def rule_effects(ordinary: str) -> list[dict[str, Any]]:
    """Read the method only after guards; authenticated access has no intrusion roll."""
    return [
        {"set": {"field": "_rule", "value": ordinary}},
        {"set": {"field": "_rule", "value": "ni[node_id].reentry[method]"},
         "condition": "method != 'INTRUDE'"},
        {"set": {"field": "_granted_privilege", "value": "_rule.privilege"}},
        {"set": {"field": "_granted_privilege", "value": "nc[credential_id].grants[node_id]"},
         "condition": "method == 'CREDENTIAL'"},
        {"set": {"field": "_granted_privilege", "value": "nh[_actor_id].nodes[node_id].privilege"},
         "condition": "method == 'FOOTHOLD'"},
    ]
