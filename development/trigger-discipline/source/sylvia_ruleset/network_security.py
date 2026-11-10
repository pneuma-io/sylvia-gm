"""Authored monitoring and defender actions emitted as ordinary package declarations."""

from typing import Any


def parameters(param: Any) -> list[dict[str, Any]]:
    """Keep security coverage, evidence and response rules out of caller-controlled inputs."""
    return [
        param("monitors", "array", items="object", min_items=0, max_items=64,
              from_state="state.network_monitors"),
        param("defenders", "object", from_state="state.network_defenders"),
    ]


def _response_checks(intruder: str, *, created_session: bool = False) -> list[str]:
    """Accept only the first supported, explicitly authored response targets."""
    session = (f"({intruder} in ns and _response.target in ns[{intruder}].nodes and "
               f"ns[{intruder}].nodes[_response.target].active == true)")
    if created_session:
        session = f"({session} or (_created_session == true and _response.target == node_id))"
    return [
        ("_response.kind == 'END_SESSION' or _response.kind == 'ISOLATE_LINK' or "
         "_response.kind == 'SHUTDOWN_NODE' or _response.kind == 'REVOKE_CREDENTIAL' or "
         "_response.kind == 'REMOVE_FOOTHOLD'"),
        f"_response.kind != 'END_SESSION' or {session}",
        "_response.kind != 'ISOLATE_LINK' or _response.target in nt.links",
        "_response.kind != 'SHUTDOWN_NODE' or _response.target in nt.nodes",
        "_response.kind != 'REVOKE_CREDENTIAL' or _response.target in nc",
        (f"_response.kind != 'REMOVE_FOOTHOLD' or ({intruder} in nh and "
         f"_response.target in nh[{intruder}].nodes)"),
    ]


def _apply_response(intruder: str) -> list[dict[str, Any]]:
    """Advance generations so bringing a device/link online cannot revive old access."""
    effects: list[dict[str, Any]] = [{
        "condition": "_response.kind == 'END_SESSION'", "on_true": [
            {"set": {"field": f"state.network_sessions.{{{intruder}}}.nodes.{{_response.target}}.active",
                     "value": False}},
            {"increment": {"field": f"state.network_sessions.{{{intruder}}}.nodes.{{_response.target}}.generation",
                           "amount": 1}},
        ], "on_false": [],
    }]
    for kind, group in (("ISOLATE_LINK", "links"), ("SHUTDOWN_NODE", "nodes")):
        effects.append({"condition": f"_response.kind == '{kind}'", "on_true": [
            {"set": {"field": f"state.network_topology.{group}.{{_response.target}}.online",
                     "value": False}},
            {"increment": {"field": f"state.network_topology.{group}.{{_response.target}}.generation",
                           "amount": 1}},
        ], "on_false": []})
    effects.extend([
        {"condition": "_response.kind == 'REVOKE_CREDENTIAL'", "on_true": [
            {"set": {"field": "state.network_credentials.{_response.target}.active", "value": False}},
            {"increment": {"field": "state.network_credentials.{_response.target}.generation", "amount": 1}},
        ], "on_false": []},
        {"condition": "_response.kind == 'REMOVE_FOOTHOLD'", "on_true": [
            {"set": {"field": f"state.network_footholds.{{{intruder}}}.nodes.{{_response.target}}.active", "value": False}},
        ], "on_false": []},
    ])
    return effects


def monitoring_effects(action: str, *, link: str = "''", intrusion: bool = False) -> list[dict[str, Any]]:
    """Resolve only authored relevant coverage, independently of an attempt's success."""
    observed = [
        {"set": {"field": "_noticed", "value": "_monitor.notice == 'CERTAIN'"}},
        {"condition": "_monitor.notice == 'UNCERTAIN'", "on_true": [
            {"dice": {"expression": "d20"}, "store_as": "_quiet_roll"},
            {"set": {"field": "_noticed", "value": (
                "_quiet_roll.total + actor.skills[_monitor.skill] + "
                "actor.aptitudes[_monitor.aptitude] < "
                "_monitor.difficulty + _monitor.visibility_modifiers[_visibility]"
            )}},
        ], "on_false": []},
        {"condition": "_noticed == true", "on_true": [
            {"set": {"field": "_defender_id", "value": "_monitor.defender"}},
            {"set": {"field": "_awareness", "value": 0}},
            {"set": {"field": "_awareness", "value": "defenders[_defender_id].awareness[_actor_id].level"},
             "condition": "_actor_id in defenders[_defender_id].awareness"},
            {"for_each": {"collection": "_notices", "as": "_prior_notice", "do": [
                {"set": {"field": "_awareness", "value": "max(_awareness, _prior_notice.level)"},
                 "condition": "_prior_notice.defender == _defender_id"},
            ]}},
            {"set": {"field": "_awareness", "value": "max(_awareness, _monitor.evidence)"}},
            {"set": {"field": "state.network_defenders.{_defender_id}.awareness.{_actor_id}",
                     "value": {"level": "_awareness", "node": "node_id", "action": action}}},
            {"set": {"field": "_new_notice", "value": {"defender": "_defender_id", "level": "_awareness"}}},
            {"list_push": {"list": "_notices", "value": "_new_notice"}},
            {"condition": ("_monitor.response != null and "
                           "_monitor.response in defenders[_defender_id].responses"),
             "on_true": [
                 {"set": {"field": "_response", "value": "defenders[_defender_id].responses[_monitor.response]"}},
                 {"set": {"field": "_respond", "value": (
                     "_response.mode == 'AUTOMATIC' and _awareness >= _response.min_awareness"
                 )}},
                 *({"set": {"field": "_respond", "value": False},
                    "condition": f"_respond == true and not ({condition})"}
                   for condition in _response_checks("_actor_id", created_session=intrusion)),
                 {"condition": "_respond == true", "on_true": _apply_response("_actor_id"), "on_false": []},
             ], "on_false": []},
        ], "on_false": []},
    ]
    effects: list[dict[str, Any]] = [
        {"set": {"field": "_notices", "value": []}},
        {"set": {"field": "_created_session", "value": (
            f"_access_succeeded == true and ({action} == 'GET_IN' or {action} == 'PIVOT')"
            if intrusion else False
        )}},
        {"set": {"field": "_visibility", "value": "'STANDARD'"}},
        {"set": {"field": "_visibility", "value": "_rule.visibility"},
         "condition": "'visibility' in _rule"},
        {"for_each": {"collection": "monitors", "as": "_monitor", "do": [{
            "condition": (
                f"_monitor.active == true and {action} in _monitor.actions and "
                f"(node_id in _monitor.nodes or {link} in _monitor.links) and "
                "_monitor.defender in defenders and defenders[_monitor.defender].active == true"
            ), "on_true": observed, "on_false": [],
        }]}},
    ]
    for field in ("_monitor", "_response", "_defender_id", "_awareness", "_quiet_roll", "_noticed", "_respond", "_visibility", "_notices", "_prior_notice", "_new_notice", "_created_session"):
        effects.append({"set": {"field": field, "value": None}})
    return effects


def defense_parameters(param: Any) -> list[dict[str, Any]]:
    """A human defender selects one authored response and a known intruder; no free-form writes."""
    return [
        param("actor", "string", resolve="entity"), param("intruder", "string", resolve="entity"),
        param("defender_id", "string"), param("response_id", "string"),
        param("defenders", "object", from_state="state.network_defenders"),
        param("ns", "object", from_state="state.network_sessions"),
        param("nt", "object", from_state="state.network_topology"),
        param("nc", "object", from_state="state.network_credentials"),
        param("nh", "object", from_state="state.network_footholds"),
    ]


def defense_effects() -> list[dict[str, Any]]:
    """GM-controlled human responses require the actual defender's turn, AP and evidence."""
    return [
        {"set": {"field": "state.last_defense", "value": {"success": False, "status": "'REJECTED'"}}},
        {"condition": (
            "actor.active == true and actor.downed == false and defender_id in defenders and "
            "defenders[defender_id].active == true and defenders[defender_id].operator == _actor_id and "
            "response_id in defenders[defender_id].responses and _intruder_id in defenders[defender_id].awareness"
        ), "on_true": [
            {"set": {"field": "_response", "value": "defenders[defender_id].responses[response_id]"}},
            {"set": {"field": "_respond", "value": (
                "_response.mode == 'ACTOR' and _response.ap >= 0 and actor.ap >= _response.ap and "
                "defenders[defender_id].awareness[_intruder_id].level >= _response.min_awareness"
            )}},
            *({"set": {"field": "_respond", "value": False},
               "condition": f"_respond == true and not ({condition})"}
              for condition in _response_checks("_intruder_id")),
            {"condition": "_respond == true", "on_true": [
                *_apply_response("_intruder_id"),
                {"decrement": {"field": "{_actor_id}.ap", "amount": "_response.ap"}},
                {"set": {"field": "state.last_defense", "value": {
                    "success": True, "status": "'RESPONDED'", "actor": "_actor_id",
                    "defender": "defender_id", "response": "response_id",
                }}},
            ], "on_false": []},
        ], "on_false": []},
        {"set": {"field": "_response", "value": None}},
        {"set": {"field": "_respond", "value": None}},
    ]
