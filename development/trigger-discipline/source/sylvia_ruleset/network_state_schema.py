"""TD-owned authoring checks for private network records; no balance values or defaults."""

from typing import Any


def _object(properties: dict[str, Any], required: tuple[str, ...] | None = None,
            *, additional: bool = False) -> dict[str, Any]:
    return {"type": "object", "properties": properties,
            "required": list(properties) if required is None else list(required),
            "additionalProperties": additional}


def _records(schema: dict[str, Any]) -> dict[str, Any]:
    return {"type": "object", "additionalProperties": schema}


def _array(schema: dict[str, Any], maximum: int = 512, minimum: int = 0) -> dict[str, Any]:
    return {"type": "array", "items": schema, "minItems": minimum, "maxItems": maximum}


def _enum(*values: str) -> dict[str, Any]:
    return {"type": "string", "enum": list(values)}


def state_schemas() -> list[dict[str, Any]]:
    """Validate shape and declared references; current access and route validity stay in tools."""
    text = {"type": "string", "minLength": 1}
    boolean = {"type": "boolean"}
    number = {"type": "integer"}
    count = {"type": "integer", "minimum": 0}
    generation = {"type": "integer", "minimum": 1}
    visibility = _enum("LOW", "STANDARD", "HIGH")
    lifetime = _object({"active": boolean, "generation": generation})
    persistence = _object({"method": text, "generation": generation})
    action = _object({
        "ap": count, "check_required": boolean, "difficulty": number,
        "privilege": count, "reveal_links": _array(text), "persistence": persistence,
        "visibility": visibility,
    }, ("ap", "check_required", "difficulty", "privilege", "reveal_links"))
    intrusion_actions = {name: action for name in ("GET_IN", "DISCOVER", "ESCALATE")}
    intrusion_actions["FOOTHOLD"] = {**action, "required": [*action["required"], "persistence"]}
    reentry = _object({
        name: _object({"ap": count, "visibility": visibility}, ("ap",))
        for name in ("CREDENTIAL", "FOOTHOLD", "ROUTE_PERSISTENCE")
    }, ())
    interface = _object({
        "kind": _enum("local_stationary", "remote_service"), "active": boolean,
        "generation": generation, "ap": count, "map_id": text,
        "position_cell": _array(number, 2, 2), "connections": _records(lifetime),
    }, ("kind", "active", "generation", "ap"))
    interface["allOf"] = [{
        "type": "object",
        "if": _object({"kind": _enum(kind)}, additional=True),
        "then": _object({name: interface["properties"][name] for name in required}, additional=True),
    } for kind, required in [
        ("local_stationary", ("map_id", "position_cell")),
        ("remote_service", ("connections",)),
    ]]
    session = _object({
        "active": boolean, "generation": generation, "privilege": count,
        "route_label": text, "entry_generation": generation,
        "nodes": _array(text, 8, 1), "links": _array(text, 7),
        "node_generations": _array(generation, 8, 1),
        "link_generations": _array(generation, 7),
        "session_generations": _array(generation, 8, 1),
    })
    entry = _object({
        "active": boolean, "generation": generation, "interface_generation": generation,
        "node_generation": generation, "motion_generation": count, "connection_generation": count,
    }, ("active", "generation", "interface_generation", "node_generation", "motion_generation"))
    response = _object({
        "kind": _enum("END_SESSION", "ISOLATE_LINK", "SHUTDOWN_NODE", "REVOKE_CREDENTIAL", "REMOVE_FOOTHOLD"),
        "target": text, "mode": _enum("AUTOMATIC", "ACTOR"), "min_awareness": count, "ap": count,
    }, ("kind", "target", "mode", "min_awareness"))
    response.update({
        "if": _object({"mode": _enum("ACTOR")}, additional=True),
        "then": _object({"ap": count}, additional=True),
    })
    monitor = _object({
        "id": text, "active": boolean, "defender": text, "nodes": _array(text),
        "links": _array(text), "actions": _array(_enum(
            "GET_IN", "DISCOVER", "PIVOT", "ESCALATE", "FOOTHOLD", "OPERATE", "ROUTE_PERSISTENCE",
        ), 7),
        "notice": _enum("CERTAIN", "UNCERTAIN"), "skill": text, "aptitude": text,
        "difficulty": number, "visibility_modifiers": _object({name: number for name in ("LOW", "STANDARD", "HIGH")}),
        "evidence": count, "response": {"type": ["string", "null"], "minLength": 1},
    })
    def node_records(record: dict[str, Any]) -> dict[str, Any]:
        return _records(_object({"nodes": _records(record)}))
    properties = {
        "network": {"type": "object"},
        "network_intrusions": _records(_object({
            "visible_links": _array(text), "actions": _object(intrusion_actions, ()),
            "reentry": reentry,
        })),
        "network_topology": _object({
            "nodes": _records(_object({"online": boolean, "generation": generation})),
            "links": _records(_object({"online": boolean, "generation": generation, "barrier": action},
                                      ("online", "generation", "barrier"))),
        }),
        "network_functions": _records(_object({"functions": _records(_object({
            "kind": _enum("geometry"), "map_id": text, "geometry_id": text,
            "ap": count, "privilege": count, "patch": _object({
                "active": boolean, "movement": _enum("blocking", "passable"),
                "visibility": _enum("opaque", "clear"),
            }),
        }))})),
        "network_interfaces": _records(interface),
        "network_entries": node_records(entry),
        "network_sessions": node_records(session),
        "network_footholds": node_records(_object({
            "active": boolean, "method": text, "method_generation": generation, "privilege": count,
        })),
        "network_credentials": _records(_object({
            "active": boolean, "generation": generation, "held_by": _records(generation),
            "grants": _records(count),
        })),
        "network_motion": _records(count),
        "network_monitors": _array(monitor, 64),
        "network_defenders": _records(_object({
            "active": boolean, "operator": text, "responses": _records(response),
            "awareness": _records(_object({"level": count, "node": text, "action": text})),
        })),
    }
    # A world without networking remains valid. Once authored, tools need each collection.
    schema = _object(properties, (), additional=True)
    collection_types = {name: {"type": item["type"]} for name, item in properties.items()}
    collection_types["network_monitors"]["items"] = {"type": "object"}
    schema["allOf"] = [{
        "type": "object",
        "if": _object({name: collection_types[name]}, additional=True),
        "then": _object(collection_types, additional=True),
    } for name in properties if name != "network_motion"]
    references = []

    def reference(source: list[str], target: list[str]) -> None:
        references.append({"source": source, "target_key": "state", "target": target})

    nodes = ["network", "nodes", "*", "$key"]
    links = ["network", "links", "*", "$key"]
    for field in ("network_intrusions", "network_interfaces", "network_functions"):
        reference([field, "*", "$key"], nodes)
    for field in ("nodes", "links"):
        reference(["network_topology", field, "*", "$key"], ["network", field, "*", "$key"])
        reference(["network", field, "*", "$key"], ["network_topology", field, "*", "$key"])
    for field in ("network_entries", "network_sessions", "network_footholds"):
        reference([field, "*", "nodes", "*", "$key"], nodes)
    for field, target in (("nodes", nodes), ("links", links)):
        reference(["network_sessions", "*", "nodes", "*", field, "*"], target)
        reference(["network_monitors", "*", field, "*"], target)
    reference(["network_intrusions", "*", "visible_links", "*"], links)
    reference(["network_intrusions", "*", "actions", "*", "reveal_links", "*"], links)
    reference(["network_topology", "links", "*", "barrier", "reveal_links", "*"], links)
    reference(["network_credentials", "*", "grants", "*", "$key"], nodes)
    reference(["network_defenders", "*", "awareness", "*", "node"], nodes)
    reference(["network_monitors", "*", "defender"], ["network_defenders", "*", "$key"])
    reference(["network_functions", "$node", "functions", "*", "$key"],
              ["network", "nodes", "$node", "functions", "*", "$key"])
    reference(["network", "nodes", "$node", "functions", "*", "$key"],
              ["network_functions", "$node", "functions", "*", "$key"])
    reference(["network_functions", "*", "functions", "*", "map_id"], ["spatial_scene", "map", "id"])
    reference(["network_functions", "*", "functions", "*", "geometry_id"], ["spatial_scene", "geometry", "*", "id"])
    reference(["network_interfaces", "*", "map_id"], ["spatial_scene", "map", "id"])
    return [{"key": "state", "schema": schema, "references": references}]
