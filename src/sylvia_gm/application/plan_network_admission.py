"""Check authored graph presentation and references without interpreting Ruleset access rules."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, NoReturn, cast

from sylvia_gm.application.network_graph import NetworkGraphError, project_network_graph
from sylvia_gm.application.plan_spatial_admission import _plan_state, _state_source_path
from sylvia_gm.application.tactical_grid import resolve_surface_source
from sylvia_gm.domain.authority import thaw_json_value
from sylvia_gm.domain.content_validation import ContentShapeError
from sylvia_gm.domain.errors import RulesetDiagnostic
from sylvia_gm.domain.package_library import SurfaceDeclaration


def _reject(path: str, message: str) -> NoReturn:
    """Return authoring locations and fixed explanations, never private record values."""
    raise ContentShapeError((RulesetDiagnostic(
        code="plan_network_graph_invalid", phase="content", file="plan.json", path=path,
        message=message,
        guidance="Correct the network_graph presentation or its declared map/entity reference.",
    ),))


def _reveal_records(value: object, path: str, maximum: int = 512) -> dict[str, Any]:
    """Validate future-visible records in a local copy; no discovery state is changed."""
    if not isinstance(value, dict) or len(value) > maximum:
        _reject(path, "Network records must be a bounded object.")
    for identity, record in value.items():
        if (not isinstance(identity, str) or not identity or identity != identity.strip()
                or len(identity.encode("utf-8")) > 128):
            _reject(path, "Network record identity is invalid.")
        if not isinstance(record, dict) or not isinstance(record.get("visible"), bool):
            _reject(f"{path}.{identity}", "Network record requires an explicit visible boolean.")
        record["visible"] = True
    return value


def _validate_graph(value: object, path: str, maps: Mapping[str, set[str] | None]) -> None:
    """Reuse closed renderer shapes and check references before visibility filtering."""
    if not isinstance(value, Mapping):
        _reject(path, "Network graph source must be an object.")
    graph = cast(dict[str, Any], thaw_json_value(value))
    groups = _reveal_records(graph.get("groups"), f"{path}.groups")
    nodes = _reveal_records(graph.get("nodes"), f"{path}.nodes")
    links = _reveal_records(graph.get("links"), f"{path}.links")
    for identity, node in nodes.items():
        _reveal_records(node.get("functions", {}), f"{path}.nodes.{identity}.functions", 64)
    for identity, link in links.items():
        for endpoint in ("source", "target"):
            target = link.get(endpoint)
            if not isinstance(target, str) or target not in nodes:
                _reject(f"{path}.links.{identity}.{endpoint}",
                        "Network link must reference an authored node.")
    try:
        project_network_graph(graph)
    except NetworkGraphError as error:
        _reject(path, str(error))
    for identity, node in nodes.items():
        node_path = f"{path}.nodes.{identity}"
        group = node.get("group_id")
        if group is not None and group not in groups:
            _reject(f"{node_path}.group_id", "Network group must reference an authored group.")
        binding = node.get("physical_binding")
        if binding is None:
            continue
        surface_id, entity_id = binding["surface_id"], binding["entity_id"]
        if surface_id not in maps:
            _reject(f"{node_path}.physical_binding.surface_id",
                    "Network physical binding must reference a declared tactical map surface.")
        entities = maps[surface_id]
        if entities is not None and entity_id not in entities:
            _reject(f"{node_path}.physical_binding.entity_id",
                    "Network physical binding must reference an entity in the authored map.")


def validate_plan_networks(
    payload: Mapping[str, object], surfaces: Sequence[SurfaceDeclaration],
) -> None:
    """Check present Plan graphs; absent state can still be created by ordinary runtime tools."""
    state, paths = _plan_state(payload)
    maps: dict[str, set[str] | None] = {}
    for surface in surfaces:
        if surface.renderer not in {"tactical_grid", "coordinate_map"}:
            continue
        data = resolve_surface_source(surface.source, state=state, participants=())
        scene = data.get("scene") if isinstance(data, Mapping) else None
        entities = scene.get("entities") if isinstance(scene, Mapping) else None
        maps[surface.identity] = (
            {item["id"] for item in entities if isinstance(item, Mapping)
             and isinstance(item.get("id"), str)}
            if isinstance(entities, Sequence) and not isinstance(entities, (str, bytes, bytearray))
            else None
        )
    for surface in surfaces:
        if surface.renderer != "network_graph":
            continue
        path = _state_source_path(surface.source, state, paths)
        if path is None and isinstance(surface.source, Mapping):
            fields = surface.source.get("fields")
            if isinstance(fields, Mapping) and any(
                _state_source_path(source, state, paths) is not None for source in fields.values()
            ):
                path = "$.initial_state.mechanics"
        if path is not None:
            _validate_graph(
                resolve_surface_source(surface.source, state=state, participants=()), path, maps,
            )
