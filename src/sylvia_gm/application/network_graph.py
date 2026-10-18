"""Safe projection and dynamic-intent binding for persistent graph surfaces.

The host understands only visible nodes, visible links, presentation state, and
references to already-visible tactical entities. Rulesets retain ownership of
discovery, traversal, privilege, detection, sessions, and remote operations.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from copy import deepcopy
from typing import cast

from sylvia_gm.domain.authority import SafePayload, owned_payload, thaw_json_value
from sylvia_gm.domain.package_library import NETWORK_UI_DYNAMIC_SELECTIONS

NETWORK_SELECTION_BINDINGS = NETWORK_UI_DYNAMIC_SELECTIONS

_MAX_GRAPH_ITEMS = 512
_MAX_FUNCTIONS_PER_NODE = 64
_MAX_VISIBLE_FUNCTIONS = 2_048
_MAX_LABEL_BYTES = 256
_NODE_FIELDS = frozenset(
    {
        "label",
        "kind",
        "group_id",
        "status",
        "access",
        "control",
        "detection",
        "functions",
        "physical_binding",
        "visible",
    }
)
_LINK_FIELDS = frozenset(
    {"source", "target", "label", "kind", "status", "direction", "visible"}
)
_FUNCTION_FIELDS = frozenset({"label", "state", "enabled", "visible"})
_GROUP_FIELDS = frozenset({"label", "visible"})


class NetworkGraphError(ValueError):
    """A safe rejection of a graph projection or renderer selection."""


def _small_text(value: object, label: str, *, maximum: int = _MAX_LABEL_BYTES) -> str:
    if (
        not isinstance(value, str)
        or not value
        or value != value.strip()
        or len(value.encode("utf-8")) > maximum
    ):
        raise NetworkGraphError(f"Network graph {label} is invalid")
    return value


def _records(
    value: object,
    label: str,
    *,
    maximum: int = _MAX_GRAPH_ITEMS,
) -> Mapping[object, object]:
    if not isinstance(value, Mapping) or len(value) > maximum:
        raise NetworkGraphError(f"Network graph {label} are invalid")
    return value


def _visible_record(value: object, fields: Set[str], label: str) -> Mapping[str, object] | None:
    if not isinstance(value, Mapping):
        return None
    if value.get("visible") is not True:
        return None
    if set(value) - fields:
        raise NetworkGraphError(f"Visible network {label} fields are invalid")
    return cast(Mapping[str, object], value)


def _optional_text(
    source: Mapping[str, object],
    target: dict[str, object],
    field: str,
) -> None:
    value = source.get(field)
    if value is not None:
        target[field] = _small_text(value, field)


def _project_groups(value: object) -> tuple[list[dict[str, object]], set[str]]:
    groups: list[dict[str, object]] = []
    visible_ids: set[str] = set()
    for identity, raw in sorted(_records(value, "groups").items(), key=lambda item: str(item[0])):
        record = _visible_record(raw, _GROUP_FIELDS, "group")
        if record is None:
            continue
        group_id = _small_text(identity, "group identity", maximum=128)
        groups.append({"id": group_id, "label": _small_text(record.get("label"), "group label")})
        visible_ids.add(group_id)
    return groups, visible_ids


def _project_functions(value: object) -> list[dict[str, object]]:
    functions: list[dict[str, object]] = []
    for identity, raw in sorted(
        _records(value, "functions", maximum=_MAX_FUNCTIONS_PER_NODE).items(),
        key=lambda item: str(item[0]),
    ):
        record = _visible_record(raw, _FUNCTION_FIELDS, "function")
        if record is None:
            continue
        enabled = record.get("enabled")
        if not isinstance(enabled, bool):
            raise NetworkGraphError("Visible network function enabled state is invalid")
        function: dict[str, object] = {
            "id": _small_text(identity, "function identity", maximum=128),
            "label": _small_text(record.get("label"), "function label"),
            "enabled": enabled,
        }
        _optional_text(record, function, "state")
        functions.append(function)
    return functions


def _physical_binding(
    value: object,
    visible_physical_entities: Set[tuple[str, str]],
) -> dict[str, str] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping) or set(value) != {"surface_id", "entity_id"}:
        raise NetworkGraphError("Network physical binding is invalid")
    surface_id = _small_text(value.get("surface_id"), "physical surface identity", maximum=128)
    entity_id = _small_text(value.get("entity_id"), "physical entity identity", maximum=128)
    if (surface_id, entity_id) not in visible_physical_entities:
        return None
    return {"surface_id": surface_id, "entity_id": entity_id}


def _project_nodes(
    value: object,
    visible_groups: set[str],
    visible_physical_entities: Set[tuple[str, str]],
) -> tuple[list[dict[str, object]], set[str]]:
    nodes: list[dict[str, object]] = []
    visible_ids: set[str] = set()
    for identity, raw in sorted(_records(value, "nodes").items(), key=lambda item: str(item[0])):
        record = _visible_record(raw, _NODE_FIELDS, "node")
        if record is None:
            continue
        node_id = _small_text(identity, "node identity", maximum=128)
        node: dict[str, object] = {
            "id": node_id,
            "label": _small_text(record.get("label"), "node label"),
            "kind": _small_text(record.get("kind"), "node kind", maximum=128),
            "functions": _project_functions(record.get("functions", {})),
        }
        group_id = record.get("group_id")
        if group_id is not None:
            group = _small_text(group_id, "node group identity", maximum=128)
            if group in visible_groups:
                node["group_id"] = group
        for field in ("status", "access", "control", "detection"):
            _optional_text(record, node, field)
        binding = _physical_binding(record.get("physical_binding"), visible_physical_entities)
        if binding is not None:
            node["physical_binding"] = binding
        nodes.append(node)
        visible_ids.add(node_id)
    return nodes, visible_ids


def _project_links(value: object, visible_nodes: set[str]) -> list[dict[str, object]]:
    links: list[dict[str, object]] = []
    for identity, raw in sorted(_records(value, "links").items(), key=lambda item: str(item[0])):
        if not isinstance(raw, Mapping) or raw.get("visible") is not True:
            continue
        source = raw.get("source")
        target = raw.get("target")
        if source not in visible_nodes or target not in visible_nodes:
            continue
        record = _visible_record(raw, _LINK_FIELDS, "link")
        if record is None:  # pragma: no cover - guarded above
            continue
        direction = record.get("direction", "bidirectional")
        if direction not in {"directed", "bidirectional"}:
            raise NetworkGraphError("Visible network link direction is invalid")
        link: dict[str, object] = {
            "id": _small_text(identity, "link identity", maximum=128),
            "source": cast(str, source),
            "target": cast(str, target),
            "direction": direction,
        }
        for field in ("label", "kind", "status"):
            _optional_text(record, link, field)
        links.append(link)
    return links


def _link_by_id(projection: Mapping[str, object], link_id: str) -> Mapping[str, object] | None:
    links = cast(Sequence[Mapping[str, object]], projection.get("links", ()))
    return next((item for item in links if item.get("id") == link_id), None)


def _selection_value_valid(
    source: str,
    value: object,
    projection: Mapping[str, object],
    *,
    action_id: str | None = None,
    controlled_actor_ids: Set[str] = frozenset(),
) -> bool:
    if source == "actor":
        return isinstance(value, str) and value in controlled_actor_ids
    nodes = cast(Sequence[Mapping[str, object]], projection.get("nodes", ()))
    node_ids = {item.get("id") for item in nodes}
    if source == "node":
        return isinstance(value, str) and value in node_ids
    if source == "link":
        return isinstance(value, str) and _link_by_id(projection, value) is not None
    if source == "route_step":
        if (
            not isinstance(value, Sequence)
            or isinstance(value, (str, bytes, bytearray))
            or len(value) != 3
            or any(not isinstance(item, str) for item in value)
        ):
            return False
        start, end, link_id = cast(Sequence[str], value)
        link = _link_by_id(projection, link_id)
        if link is None:
            return False
        if start not in node_ids or end not in node_ids:
            return False
        direct = start == link.get("source") and end == link.get("target")
        reverse = (
            link.get("direction") == "bidirectional"
            and start == link.get("target")
            and end == link.get("source")
        )
        return direct or reverse
    if source == "remote_function":
        if not isinstance(value, str):
            return False
        return any(
            function.get("id") == value and function.get("enabled") is True
            for node in nodes
            for function in cast(Sequence[Mapping[str, object]], node.get("functions", ()))
        )
    if source == "declared_action":
        return action_id is not None and value == action_id
    return False


def _project_selection(value: object, projection: Mapping[str, object]) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    result: dict[str, object] = {}
    for source in sorted(NETWORK_SELECTION_BINDINGS - {"declared_action"}):
        selected = value.get(source)
        if selected is not None and _selection_value_valid(source, selected, projection):
            result[source] = deepcopy(selected)
    return result


def project_network_graph(
    value: object,
    *,
    visible_physical_entities: Set[tuple[str, str]] = frozenset(),
) -> SafePayload:
    """Project only explicitly visible graph topology and safe physical bindings."""

    if not isinstance(value, Mapping):
        raise NetworkGraphError("Network graph source must be an object")
    if set(value) != {"graph_id", "revision", "groups", "nodes", "links", "selection"}:
        raise NetworkGraphError("Network graph source fields are invalid")
    graph_id = _small_text(value.get("graph_id"), "identity", maximum=128)
    revision = value.get("revision")
    if isinstance(revision, bool) or not isinstance(revision, int) or revision < 0:
        raise NetworkGraphError("Network graph revision is invalid")
    groups, visible_groups = _project_groups(value.get("groups"))
    nodes, visible_nodes = _project_nodes(
        value.get("nodes"), visible_groups, visible_physical_entities
    )
    if sum(len(cast(Sequence[object], node["functions"])) for node in nodes) > (
        _MAX_VISIBLE_FUNCTIONS
    ):
        raise NetworkGraphError("Network graph exposes too many functions")
    links = _project_links(value.get("links"), visible_nodes)
    projection: dict[str, object] = {
        "format": 1,
        "graph_id": graph_id,
        "graph_revision": revision,
        "groups": groups,
        "nodes": nodes,
        "links": links,
    }
    projection["selection"] = _project_selection(value.get("selection"), projection)
    return owned_payload(projection)


def resolve_network_action_parameters(
    action: Mapping[str, object],
    selection: object,
    projection: Mapping[str, object],
    *,
    controlled_actor_ids: Set[str] = frozenset(),
) -> SafePayload:
    """Bind a current visible graph selection to an ordinary package operation."""

    bindings = action.get("bindings")
    fixed = action.get("params")
    action_id = action.get("id")
    if not isinstance(bindings, Mapping) or not isinstance(fixed, Mapping):
        raise NetworkGraphError("Network action declaration is invalid")
    if not isinstance(action_id, str) or not isinstance(selection, Mapping):
        raise NetworkGraphError("Network selection is invalid")
    if selection.get("graph_revision") != projection.get("graph_revision"):
        raise NetworkGraphError("Network selection is stale")
    sources = set(bindings.values())
    if (
        not sources <= NETWORK_SELECTION_BINDINGS
        or set(selection) != sources | {"graph_revision"}
    ):
        raise NetworkGraphError("Network selection contains undeclared input")
    if {"node", "remote_function"} <= sources:
        selected_node = selection.get("node")
        selected_function = selection.get("remote_function")
        nodes = cast(Sequence[Mapping[str, object]], projection.get("nodes", ()))
        node = next((item for item in nodes if item.get("id") == selected_node), None)
        functions = () if node is None else cast(
            Sequence[Mapping[str, object]], node.get("functions", ())
        )
        if not any(
            function.get("id") == selected_function and function.get("enabled") is True
            for function in functions
        ):
            raise NetworkGraphError("Network remote_function selection is invalid")
    parameters = {str(key): deepcopy(value) for key, value in fixed.items()}
    if set(parameters) & set(bindings):
        raise NetworkGraphError("Network action binds a fixed parameter")
    for parameter, raw_source in bindings.items():
        if not isinstance(parameter, str) or not isinstance(raw_source, str):
            raise NetworkGraphError("Network selection binding is invalid")
        selected = selection.get(raw_source)
        if not _selection_value_valid(
            raw_source,
            selected,
            projection,
            action_id=action_id,
            controlled_actor_ids=controlled_actor_ids,
        ):
            raise NetworkGraphError(f"Network {raw_source} selection is invalid")
        parameters[parameter] = deepcopy(selected)
    try:
        return owned_payload(parameters)
    except (TypeError, ValueError) as error:
        raise NetworkGraphError("Network parameters are not canonical JSON") from error


def visible_physical_entity_ids(
    surfaces: Sequence[Mapping[str, object]],
) -> frozenset[tuple[str, str]]:
    """Return active entity identities already admitted to tactical projections."""

    result: set[tuple[str, str]] = set()
    for surface in surfaces:
        if surface.get("renderer") not in {"tactical_grid", "coordinate_map"} or not isinstance(
            surface.get("data"), Mapping
        ):
            continue
        raw_entities = cast(Mapping[str, object], surface["data"]).get("entities", [])
        entities = (
            raw_entities
            if isinstance(raw_entities, Sequence)
            and not isinstance(raw_entities, (str, bytes, bytearray))
            else ()
        )
        for entity in entities:
            if isinstance(entity, Mapping) and entity.get("active") is True:
                result.add((str(surface.get("id")), str(entity.get("id"))))
    return frozenset(result)


def bind_physical_network_surfaces(
    surfaces: Sequence[Mapping[str, object]],
) -> list[SafePayload]:
    """Add reverse navigation using only identities visible on both projected surfaces."""

    copied = [cast(dict[str, object], thaw_json_value(owned_payload(item))) for item in surfaces]
    visible_entities = visible_physical_entity_ids(copied)
    tactical_entities: dict[tuple[str, str], dict[str, object]] = {}
    for surface in copied:
        if surface.get("renderer") not in {"tactical_grid", "coordinate_map"} or not isinstance(
            surface.get("data"), Mapping
        ):
            continue
        data = cast(Mapping[str, object], surface["data"])
        for entity in cast(list[dict[str, object]], data.get("entities", [])):
            key = (str(surface.get("id")), str(entity.get("id")))
            if key in visible_entities:
                tactical_entities[key] = entity

    for surface in copied:
        if surface.get("renderer") != "network_graph" or not isinstance(
            surface.get("data"), Mapping
        ):
            continue
        data = cast(dict[str, object], surface["data"])
        for node in cast(list[dict[str, object]], data.get("nodes", [])):
            binding = node.get("physical_binding")
            if not isinstance(binding, dict):
                continue
            key = (str(binding.get("surface_id")), str(binding.get("entity_id")))
            target_entity = tactical_entities.get(key)
            if target_entity is None:
                node.pop("physical_binding", None)
                continue
            references = target_entity.setdefault("network_bindings", [])
            cast(list[object], references).append(
                {"surface_id": str(surface["id"]), "node_id": str(node["id"])}
            )
    return [owned_payload(surface) for surface in copied]


__all__ = (
    "NETWORK_SELECTION_BINDINGS",
    "NetworkGraphError",
    "bind_physical_network_surfaces",
    "project_network_graph",
    "resolve_network_action_parameters",
    "visible_physical_entity_ids",
)
