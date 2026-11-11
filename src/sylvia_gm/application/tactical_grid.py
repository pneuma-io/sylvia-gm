"""Safe host projection and dynamic-intent binding for tactical grid surfaces.

Rulesets own spatial state and the meaning of actions.  This adapter exposes only
explicit player presentation records and turns bounded renderer selections into
ordinary package-tool parameters. It queries physical routes for parameter binding;
Rulesets retain movement costs, attacks, cover interpretation and committed outcomes.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import cast

from sylvia_gm.domain.authority import SafePayload, owned_payload, thaw_json_value
from sylvia_gm.domain.package_library import TACTICAL_UI_DYNAMIC_SELECTIONS
from sylvia_gm.spatial import (
    AdmittedSpatialScene,
    SpatialEngine,
    admit_spatial_query,
    admit_spatial_scene,
)

# The wall editor groups visible pieces with the same endpoint rule as owner authoring.
from sylvia_gm.spatial._wall_authoring import _components as _edge_components

TACTICAL_SELECTION_BINDINGS = TACTICAL_UI_DYNAMIC_SELECTIONS
# Owner-derived inputs are never accepted from the renderer's selection.
_DERIVED_SOURCES = frozenset({"path", "map_id", "map_revision"})
_EDGE_SOURCES = frozenset({"edges", "edge_anchor", "edge_walls"})

_DISPLAY_FIELDS = frozenset(
    {
        "label",
        "kind",
        "state",
        "stance",
        "side",
        "color",
        "icon",
        "interactive",
        "height_label",
        "material_label",
        "display_geometry",
    }
)
_SELECTION_FIELDS = TACTICAL_SELECTION_BINDINGS - {"map_id", "map_revision"} - _EDGE_SOURCES
_MAX_LABEL_BYTES = 256
_MAX_PATH_POINTS = 256


class TacticalGridError(ValueError):
    """A safe rejection of a tactical projection or renderer selection."""


def resolve_surface_source(
    source: object,
    *,
    state: Mapping[str, object],
    participants: Sequence[object],
) -> object:
    """Resolve one admitted player-surface query without reading private authorities."""

    if not isinstance(source, Mapping):
        return None
    kind = source.get("kind")
    if kind == "literal":
        return thaw_json_value(owned_payload(source.get("value")))
    if kind == "state":
        namespace = source.get("namespace")
        key = source.get("key")
        if not isinstance(namespace, str) or not isinstance(key, str):
            return None
        exact = f"{namespace}.{key}"
        if exact in state:
            return state[exact]
        root, separator, remainder = key.partition(".")
        current = state.get(f"{namespace}.{root}")
        if not separator:
            return current
        for field in remainder.split("."):
            if not isinstance(current, Mapping) or field not in current:
                return None
            current = current[field]
        return current
    if kind == "participants":
        kinds_value = _sequence(source.get("kinds"))
        kinds = frozenset(item for item in kinds_value or () if isinstance(item, str))
        result: list[dict[str, object]] = []
        for participant in participants:
            participant_kind = getattr(participant, "kind", None)
            participant_id = getattr(participant, "participant_id", None)
            data = getattr(participant, "data", None)
            if participant_kind in kinds and isinstance(participant_id, str):
                result.append(
                    {
                        "id": participant_id,
                        "kind": participant_kind,
                        "data": thaw_json_value(data),
                    }
                )
        return result
    if kind == "keyed_values":
        keyed_values: dict[str, object] = {}
        for entry in _sequence(source.get("entries")) or ():
            if not isinstance(entry, Mapping):
                continue
            key = resolve_surface_source(entry.get("key"), state=state, participants=participants)
            value = resolve_surface_source(entry.get("value"), state=state, participants=participants)
            if isinstance(key, str) and key.strip() and value is not None:
                keyed_values[key] = value
        return keyed_values
    if kind == "object":
        fields = source.get("fields")
        if not isinstance(fields, Mapping):
            return None
        return {
            str(name): resolve_surface_source(
                child,
                state=state,
                participants=participants,
            )
            for name, child in fields.items()
        }
    return None


def _sequence(value: object) -> Sequence[object] | None:
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return value
    return None


def _finite(value: object) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
    )


def _coordinate(value: object, dimensions: int, *, integer: bool = False) -> list[object] | None:
    items = _sequence(value)
    if items is None or len(items) != dimensions:
        return None
    if integer:
        if any(isinstance(item, bool) or not isinstance(item, int) for item in items):
            return None
    elif any(not _finite(item) for item in items):
        return None
    return list(items)


def _public_display(
    metadata: object, *, scene: AdmittedSpatialScene | None = None,
    visible_anchors: frozenset[str] | None = None,
) -> dict[str, object] | None:
    """Return the closed public presentation record; absence means hidden."""

    if not isinstance(metadata, Mapping):
        return None
    anchor = metadata.get("display_anchor")
    if anchor is not None and (
        not isinstance(anchor, str) or visible_anchors is None or anchor not in visible_anchors
    ):
        return None
    raw = metadata.get("player")
    if not isinstance(raw, Mapping) or set(raw) - _DISPLAY_FIELDS:
        return None
    label = raw.get("label")
    kind = raw.get("kind")
    if (
        not isinstance(label, str)
        or not label.strip()
        or label != label.strip()
        or len(label.encode("utf-8")) > _MAX_LABEL_BYTES
        or not isinstance(kind, str)
        or not kind.strip()
        or kind != kind.strip()
        or len(kind.encode("utf-8")) > 64
    ):
        return None
    result: dict[str, object] = {"label": label, "kind": kind}
    for field in ("state", "stance", "side", "color", "icon", "height_label", "material_label"):
        item = raw.get(field)
        if item is not None:
            if (
                not isinstance(item, str)
                or item != item.strip()
                or not item
                or len(item.encode("utf-8")) > _MAX_LABEL_BYTES
            ):
                return None
            result[field] = item
    interactive = raw.get("interactive")
    if interactive is not None:
        if not isinstance(interactive, bool):
            return None
        result["interactive"] = interactive
    if "display_geometry" in raw:
        geometry = raw["display_geometry"]
        if (
            scene is None
            or not isinstance(geometry, Mapping)
            or geometry.get("kind") not in ("cone", "prism")
        ):
            return None
        # Admission validates a closed public shape without executing a query or reading recipients.
        try:
            result["display_geometry"] = admit_spatial_query(
                scene,
                {
                    "kind": "area",
                    "map_id": scene.map_id,
                    "shape": geometry,
                    "include": ["zones"],
                    "max_results": 1,
                },
            ).as_payload()["shape"]
        except (TypeError, ValueError):
            return None
    return result


def _visible_anchor_ids(scene: Mapping[str, object]) -> frozenset[str]:
    """Keep attached displays behind the same active-entity visibility boundary as the map."""
    return frozenset(
        item["id"]
        for item in cast(list[dict[str, object]], scene["entities"])
        if item["active"] is True and _public_display(item["metadata"]) is not None
    )


def _project_objectives(value: object) -> list[dict[str, object]]:
    items = _sequence(value)
    if items is None or len(items) > 256:
        return []
    result: list[dict[str, object]] = []
    seen: set[str] = set()
    for item in items:
        if not isinstance(item, Mapping) or item.get("visible") is not True:
            continue
        if set(item) != {"id", "label", "status", "position", "visible"}:
            continue
        identity = item.get("id")
        label = item.get("label")
        status = item.get("status")
        position = _coordinate(item.get("position"), 3)
        if (
            not isinstance(identity, str)
            or not identity
            or identity in seen
            or not isinstance(label, str)
            or not label.strip()
            or len(label.encode("utf-8")) > _MAX_LABEL_BYTES
            or status not in {"active", "complete", "failed"}
            or position is None
        ):
            continue
        seen.add(identity)
        result.append(
            {"id": identity, "label": label.strip(), "status": status, "position": position}
        )
    return result


def _project_selection(value: object) -> dict[str, object]:
    if not isinstance(value, Mapping):
        return {}
    candidate = {
        str(key): deepcopy(item) for key, item in value.items() if key in _SELECTION_FIELDS
    }
    try:
        captured = thaw_json_value(owned_payload(candidate))
    except (TypeError, ValueError):
        return {}
    return cast(dict[str, object], captured)


def _project_preview(value: object, map_revision: int) -> dict[str, object]:
    result: dict[str, object] = {
        "authoritative": False,
        "label": "Preview only — the owner resolves the committed action",
        "map_revision": map_revision,
        "path_cost": None,
        "los": None,
        "exposure": None,
        "crossed_zones": [],
    }
    if not isinstance(value, Mapping) or value.get("map_revision") != map_revision:
        return result
    path_cost = value.get("path_cost")
    if path_cost is None or _finite(path_cost) and float(path_cost) >= 0:
        result["path_cost"] = path_cost
    if isinstance(value.get("los"), bool):
        result["los"] = value["los"]
    exposure = value.get("exposure")
    if exposure is None or _finite(exposure) and 0 <= float(exposure) <= 1:
        result["exposure"] = exposure
    zones = _sequence(value.get("crossed_zones"))
    if zones is not None and len(zones) <= 256 and all(isinstance(item, str) for item in zones):
        result["crossed_zones"] = list(dict.fromkeys(cast(Sequence[str], zones)))
    return result


def _visible_edge_walls(geometry: list[dict[str, object]]) -> dict[str, str]:
    """Name each visible grid-edge wall by its first visible segment.

    Grouping uses only visible segments, so a hidden piece never links two visible
    ones; owner continuity identities stay in storage.
    """
    layers: dict[object, list[dict[str, object]]] = {}
    for item in geometry:
        if cast(Mapping[str, object], item["volume"])["kind"] == "grid_edge":
            key = item.get("edge_layer_id", ("unlayered", item["id"]))
            layers.setdefault(key, []).append(item)
    walls: dict[str, str] = {}
    for members in layers.values():
        for component in _edge_components(members):
            for item in component:
                walls[cast(str, item["id"])] = cast(str, component[0]["id"])
    return walls


def public_edge_segment(template: object) -> dict[str, object] | None:
    """Show an action's authored barrier by height and public display only."""
    if not isinstance(template, Mapping):
        return None
    return {"height": template.get("height"), "display": _public_display(template.get("metadata"))}


def project_tactical_grid(value: object) -> SafePayload:
    """Create one bounded player-safe projection from package-owned spatial state."""

    if not isinstance(value, Mapping):
        raise TacticalGridError("Tactical grid source must be an object")
    try:
        admitted_scene = admit_spatial_scene(value.get("scene"))
        scene = admitted_scene.as_payload()
    except (TypeError, ValueError) as error:
        raise TacticalGridError("Tactical grid scene is invalid") from error
    map_value = cast(dict[str, object], scene["map"])
    revision = cast(int, map_value["revision"])

    visible_geometry = [
        (item, display)
        for item in cast(list[dict[str, object]], scene["geometry"])
        if (display := _public_display(item["metadata"])) is not None
    ]
    walls = _visible_edge_walls([item for item, _ in visible_geometry])
    geometry: list[dict[str, object]] = []
    for item, display in visible_geometry:
        geometry.append(
            {
                "id": item["id"],
                "map_id": item["map_id"],
                "volume": deepcopy(item["volume"]),
                "active": item["active"],
                "movement": item["movement"],
                "occupies_cells": item["occupies_cells"],
                "visibility": item["visibility"],
                "destroyed": deepcopy(item["destroyed"]),
                "display": display,
                **({"edge_wall": walls[cast(str, item["id"])]} if item["id"] in walls else {}),
            }
        )

    entities: list[dict[str, object]] = []
    for item in cast(list[dict[str, object]], scene["entities"]):
        display = _public_display(item["metadata"])
        if display is None:
            continue
        entities.append(
            {
                "id": item["id"],
                "map_id": item["map_id"],
                "position": deepcopy(item["position"]),
                "footprint": deepcopy(item["footprint"]),
                "height": item["height"],
                "facing": deepcopy(item["facing"]),
                "active": item["active"],
                "movement": item["movement"],
                "visibility": item["visibility"],
                "display": display,
            }
        )

    visible_anchors = _visible_anchor_ids(scene)
    zones: list[dict[str, object]] = []
    for item in cast(list[dict[str, object]], scene["zones"]):
        display = _public_display(
            item["metadata"], scene=admitted_scene, visible_anchors=visible_anchors
        )
        if display is None:
            continue
        lane = None
        if item["shape"]["kind"] == "grid_cells" and "lane" in item["metadata"]:
            raw_lane = item["metadata"]["lane"]
            owner = item["metadata"].get("owner")
            body = next((body for body in entities if body["id"] == owner), None)
            if (body is not None and isinstance(raw_lane, Mapping)
                    and _coordinate(raw_lane.get("origin"), 3) is not None
                    and raw_lane.get("direction") in ("east", "south", "west", "north")
                    and raw_lane.get("pattern") in ("lane", "triangle")):
                lane = {key: deepcopy(raw_lane.get(key)) for key in
                        ("origin", "direction", "pattern", "extent", "half_width")}
                status = value.get("zone_state")
                # Rulesets may supply one status or a map of owned status records.
                # Inspect only this explicit source, without knowing game-specific keys.
                pending = [status]
                for _ in range(3):
                    matched = next((entry for entry in pending if isinstance(entry, Mapping)
                                    and entry.get("zone_id") == item["id"]), None)
                    if matched is not None:
                        status = matched
                        break
                    pending = [child for entry in pending if isinstance(entry, Mapping)
                               for child in entry.values() if isinstance(child, Mapping)]
                display["state"] = "declared"
                if isinstance(status, Mapping) and status.get("zone_id") == item["id"]:
                    current_pose = (raw_lane.get("origin") == body["position"]
                                    and raw_lane.get("facing") == body["facing"]
                                    and status.get("owner") == owner
                                    and status.get("lane_shape") == item["shape"])
                    display["state"] = "armed" if status.get("active") is True and current_pose else "inactive"
        zones.append(
            {
                "id": item["id"],
                "map_id": item["map_id"],
                "shape": deepcopy(item["shape"]),
                "display": display,
                **({"lane": lane} if lane is not None else {}),
            }
        )

    return owned_payload(
        {
            "format": 1,
            "map_revision": revision,
            "map": deepcopy(map_value),
            "geometry": geometry,
            "entities": entities,
            "zones": zones,
            "terrain": deepcopy(scene["terrain"]),
            "objectives": _project_objectives(value.get("objectives")),
            "selection": _project_selection(value.get("selection")),
            "preview": _project_preview(value.get("preview"), revision),
        }
    )


def player_known_spatial_scene(value: object, *, retain_metadata: bool = False) -> SafePayload:
    """Filter scene membership; internal Ruleset previews retain visible rows' mechanical metadata."""

    admitted_scene = admit_spatial_scene(value)
    scene = admitted_scene.as_payload()
    visible_anchors = _visible_anchor_ids(scene)
    for category in ("geometry", "entities", "zones"):
        scene[category] = [
            {**item, "metadata": item["metadata"] if retain_metadata else {"player": display}}
            for item in scene[category]
            if (
                display := _public_display(
                    item["metadata"], scene=admitted_scene if category == "zones" else None,
                    visible_anchors=visible_anchors if category == "zones" else None,
                )
            )
            is not None
        ]
    return owned_payload(scene)


def player_known_spatial_value(value: object) -> object:
    """Apply the same scene membership boundary to nested reads before Ruleset preview evaluation."""
    if isinstance(value, Mapping):
        if value.get('format') == 1 and all(key in value for key in ('map', 'entities', 'geometry', 'zones', 'terrain')):
            return thaw_json_value(player_known_spatial_scene(value, retain_metadata=True))
        return {key: player_known_spatial_value(child) for key, child in value.items()}
    if _sequence(value) is not None:
        return [player_known_spatial_value(child) for child in value]
    return value


def coordinate_actor_reference(actions: object) -> str | None:
    """Read the map's consistent, explicitly declared actor metadata reference."""

    references = {
        action.get('actor_reference')
        for action in _sequence(actions) or ()
        if isinstance(action, Mapping) and isinstance(action.get('bindings'), Mapping)
        and 'actor' in action['bindings'].values()
    }
    if len(references) > 1:
        raise TacticalGridError('Coordinate actions disagree on actor references')
    return next(iter(references), None)


def project_coordinate_map(value: object, *, actor_reference: str | None = None) -> SafePayload:
    """Project public combat bodies and the Ruleset-selected mechanical actor reference."""

    if isinstance(value, Mapping) and 'enabled' in value and value['enabled'] is not True:
        raise TacticalGridError('Coordinate map is not active')
    projected = thaw_json_value(project_tactical_grid(value))
    assert isinstance(projected, dict)
    projected['entities'] = [item for item in projected['entities'] if item['active']]
    if actor_reference is not None:
        scene = admit_spatial_scene(value.get('scene')).as_payload()
        bodies = {item['id']: item for item in scene['entities']}
        references = {}
        for item in scene['entities']:
            reference = item['metadata'].get(actor_reference)
            if (item['active'] and isinstance(reference, str) and reference.strip() == reference
                    and reference and len(reference.encode('utf-8')) <= _MAX_LABEL_BYTES):
                references.setdefault(reference, []).append(item['id'])
        projected['actor_reference'] = actor_reference
        for item in projected['entities']:
            reference = bodies[item['id']]['metadata'].get(actor_reference)
            item['actor_ref'] = (reference if isinstance(reference, str)
                                 and references.get(reference) == [item['id']] else None)
    if isinstance(value, Mapping) and 'occupied_space' in value:
        declaration = value['occupied_space']
        for item in projected['entities']:
            item['occupied_space'] = _project_occupied_space(declaration, item.get('actor_ref'))
    return owned_payload(projected)


def _project_occupied_space(value: object, actor_ref: object) -> dict[str, object]:
    """Resolve a package-declared category/diameter table without inferring a body or category."""
    unavailable: dict[str, object] = {
        'status': 'unavailable',
        'reason': 'Occupied space needs an explicit supported size category.',
    }
    if not isinstance(value, Mapping) or set(value) != {'declaration', 'actors'}:
        return unavailable
    declaration, actors = value['declaration'], value['actors']
    if not isinstance(declaration, Mapping) or set(declaration) != {'convention', 'widths', 'category_field'}:
        return unavailable
    widths, field, convention = (declaration[key] for key in ('widths', 'category_field', 'convention'))
    if (not isinstance(widths, (list, tuple)) or not 1 <= len(widths) <= 32
            or any(not _finite(width) or not 0 < width <= 1_000_000 for width in widths)
            or not isinstance(field, str) or not field or not isinstance(actors, Mapping)
            or not isinstance(convention, str) or not 1 <= len(convention) <= 128):
        return unavailable
    actor = actors.get(actor_ref) if isinstance(actor_ref, str) else None
    rank = actor.get(field) if isinstance(actor, Mapping) else None
    if isinstance(rank, bool) or not isinstance(rank, int) or not 0 <= rank < len(widths):
        return unavailable
    return {'status': 'declared', 'kind': 'circle', 'diameter': widths[rank], 'convention': convention}


def _map_bounds(projection: Mapping[str, object]) -> tuple[list[float], list[float], float]:
    map_value = cast(Mapping[str, object], projection["map"])
    bounds = cast(Mapping[str, object], map_value["bounds"])
    lower = [float(item) for item in cast(Sequence[int | float], bounds["min"])]
    upper = [float(item) for item in cast(Sequence[int | float], bounds["max"])]
    grid = cast(Mapping[str, object], map_value["grid"])
    return lower, upper, float(cast(int | float, grid["cell_size"]))


def _cell_valid(value: object, projection: Mapping[str, object]) -> bool:
    cell = _coordinate(value, 2, integer=True)
    if cell is None or any(cast(int, item) < 0 for item in cell):
        return False
    lower, upper, cell_size = _map_bounds(projection)
    return all(
        cast(int, cell[index]) < math.ceil((upper[index] - lower[index]) / cell_size)
        for index in range(2)
    )


def _point_valid(value: object, projection: Mapping[str, object]) -> bool:
    point = _coordinate(value, 3)
    if point is None:
        return False
    lower, upper, _ = _map_bounds(projection)
    return all(
        lower[index] <= float(cast(int | float, point[index])) <= upper[index]
        for index in range(3)
    )


def _grid_edges(
    value: object, projection: Mapping[str, object],
) -> list[tuple[tuple[int, int], tuple[int, int]]] | None:
    """Read distinct unit cell borders as ordered pairs of in-map grid vertices."""
    items = _sequence(value)
    if items is None or not 1 <= len(items) <= 256:
        return None
    lower, upper, cell_size = _map_bounds(projection)
    limits = [math.ceil((upper[axis] - lower[axis]) / cell_size) for axis in range(2)]
    edges: list[tuple[tuple[int, int], tuple[int, int]]] = []
    for item in items:
        ends = _sequence(item)
        points = [_coordinate(end, 2, integer=True) for end in ends or ()]
        if len(points) != 2 or any(
            point is None
            or not all(0 <= cast(int, point[axis]) <= limits[axis] for axis in range(2))
            for point in points
        ):
            return None
        first, second = sorted(
            (cast(int, point[0]), cast(int, point[1])) for point in cast(list[list[object]], points)
        )
        if abs(first[0] - second[0]) + abs(first[1] - second[1]) != 1:
            return None
        edges.append((first, second))
    return edges if len(set(edges)) == len(edges) else None


def _edge_segments(
    edges: list[tuple[tuple[int, int], tuple[int, int]]],
    template: object,
    projection: Mapping[str, object],
) -> list[dict[str, object]]:
    """Build each selected border as the Ruleset's authored barrier on the map floor."""
    if not isinstance(template, Mapping):
        raise TacticalGridError("Tactical edge declaration is invalid")
    map_value = cast(Mapping[str, Mapping[str, object]], projection["map"])
    lower = cast(Sequence[int | float], map_value["bounds"]["min"])
    size = cast(int | float, map_value["grid"]["cell_size"])
    body = {key: value for key, value in template.items() if key != "height"}

    def point(vertex: tuple[int, int], height: int | float) -> list[int | float]:
        return [lower[0] + vertex[0] * size, lower[1] + vertex[1] * size, lower[2] + height]

    return [
        {
            **deepcopy(body),
            "occupies_cells": False,
            "volume": {
                "kind": "grid_edge",
                "min": point(first, 0),
                "max": point(second, cast(int | float, template["height"])),
            },
        }
        for first, second in edges
    ]


def _selection_value_valid(
    source: str,
    value: object,
    projection: Mapping[str, object],
    *,
    action_id: str,
) -> bool:
    if source in {"map_id", "map_revision"}:
        return True
    if source == "edges":
        return _grid_edges(value, projection) is not None
    if source in {"edge_anchor", "edge_walls"}:
        # Only visible grid-edge segments in this exact projection can be selected.
        segments = {
            item.get("id")
            for item in cast(Sequence[Mapping[str, object]], projection.get("geometry", ()))
            if cast(Mapping[str, object], item.get("volume", {})).get("kind") == "grid_edge"
        }
        items = _sequence(value)
        return (
            items is not None
            and (len(items) == 1 if source == "edge_anchor" else 1 <= len(items) <= 256)
            and all(isinstance(item, str) and item in segments for item in items)
            and len(set(cast(Sequence[str], items))) == len(items)
        )
    entity_ids = {
        item.get("id")
        for item in cast(Sequence[Mapping[str, object]], projection.get("entities", ()))
    }
    if source in {"actor", "target", "device"}:
        return isinstance(value, str) and value in entity_ids
    if source == "lane":
        from sylvia_gm.spatial._grid_lane import LANE_FIELDS
        values = _sequence(value)
        return (values is not None and len(values) == 1
                and isinstance(values[0], Mapping) and set(values[0]) == LANE_FIELDS)
    if source == "zone":
        return isinstance(value, str) and any(
            item.get("id") == value
            for item in cast(Sequence[Mapping[str, object]], projection.get("zones", ()))
        )
    if source == "destination":
        return _cell_valid(value, projection)
    if source == "path":
        items = _sequence(value)
        return (
            items is not None
            and 2 <= len(items) <= _MAX_PATH_POINTS
            and all(_cell_valid(item, projection) for item in items)
        )
    if source == "aim_point":
        return _point_valid(value, projection)
    if source == "orientation":
        orientation = _coordinate(value, 2)
        return orientation is not None and math.hypot(
            float(cast(int | float, orientation[0])),
            float(cast(int | float, orientation[1])),
        ) > 0
    if source == "declared_action":
        return value == action_id
    if source == "area":
        if not isinstance(value, Mapping):
            return False
        if value.get("kind") == "box" and set(value) == {"kind", "min", "max"}:
            return _point_valid(value.get("min"), projection) and _point_valid(
                value.get("max"), projection
            )
        if value.get("kind") == "cylinder" and set(value) == {
            "kind",
            "center",
            "radius",
            "height",
        }:
            return (
                _point_valid(value.get("center"), projection)
                and _finite(value.get("radius"))
                and float(cast(int | float, value["radius"])) > 0
                and _finite(value.get("height"))
                and float(cast(int | float, value["height"])) > 0
            )
    return False


def _owner_path(
    authoritative_scene: object,
    *,
    actor_id: object,
    destination: object,
) -> list[list[int]]:
    """Recompute a bounded route from raw intent against the complete owner scene."""

    if not isinstance(actor_id, str):
        raise TacticalGridError("Tactical path actor is invalid")
    try:
        scene = admit_spatial_scene(authoritative_scene)
        scene_payload = scene.as_payload()
        actor = next(
            (
                item
                for item in cast(list[dict[str, object]], scene_payload["entities"])
                if item["id"] == actor_id
            ),
            None,
        )
        cell = _coordinate(destination, 2, integer=True)
        if actor is None or cell is None:
            raise TacticalGridError("Tactical path endpoints are invalid")
        map_value = cast(dict[str, object], scene_payload["map"])
        bounds = cast(dict[str, object], map_value["bounds"])
        grid = cast(dict[str, object], map_value["grid"])
        lower = cast(Sequence[int | float], bounds["min"])
        upper = cast(Sequence[int | float], bounds["max"])
        cell_size = float(cast(int | float, grid["cell_size"]))
        position = cast(Sequence[int | float], actor["position"])
        start = [
            math.floor((float(position[index]) - float(lower[index])) / cell_size)
            for index in range(2)
        ]
        maximum = math.prod(
            math.ceil((float(upper[index]) - float(lower[index])) / cell_size)
            for index in range(2)
        )
        result = SpatialEngine(authoritative_scene).query(
            {
                "kind": "path",
                "map_id": scene.map_id,
                "start": start,
                "goal": list(cell),
                "allow_diagonal": True,
                "corner_policy": "forbid_cutting",
                "include_entities": True,
                "moving_entity_id": actor_id,
                "max_steps": maximum,
            }
        ).as_payload()
    except TacticalGridError:
        raise
    except (KeyError, TypeError, ValueError) as error:
        raise TacticalGridError("Tactical owner path could not be resolved") from error
    if not result["found"]:
        raise TacticalGridError("Tactical owner path is unavailable")
    return [list(cast(Sequence[int], item)) for item in cast(Sequence[object], result["cells"])]


def resolve_tactical_action_parameters(
    action: Mapping[str, object],
    selection: object,
    projection: Mapping[str, object],
    *,
    authoritative_scene: object | None = None,
) -> SafePayload:
    """Bind raw renderer intent while recomputing derived paths under owner authority."""

    bindings = action.get("bindings")
    fixed = action.get("params")
    action_id = action.get("id")
    if not isinstance(bindings, Mapping) or not isinstance(fixed, Mapping):
        raise TacticalGridError("Tactical action declaration is invalid")
    if not isinstance(action_id, str) or not isinstance(selection, Mapping):
        raise TacticalGridError("Tactical selection is invalid")
    map_revision = selection.get("map_revision")
    if map_revision != projection.get("map_revision"):
        raise TacticalGridError("Tactical selection is stale")
    binding_sources = set(bindings.values())
    client_sources = binding_sources - _DERIVED_SOURCES
    if (
        not binding_sources <= TACTICAL_SELECTION_BINDINGS
        or set(selection) != client_sources | {"map_revision"}
    ):
        raise TacticalGridError("Tactical selection contains undeclared input")
    parameters = {str(key): deepcopy(value) for key, value in fixed.items()}
    if set(parameters) & set(bindings):
        raise TacticalGridError("Tactical action binds a fixed parameter")
    for parameter, source_value in bindings.items():
        if not isinstance(parameter, str) or not isinstance(source_value, str):
            raise TacticalGridError("Tactical selection binding is invalid")
        selected = (
            _owner_path(
                authoritative_scene,
                actor_id=selection.get("actor"),
                destination=selection.get("destination"),
            )
            if source_value == "path" and authoritative_scene is not None
            else cast(Mapping[str, object], projection["map"])["id"] if source_value == "map_id"
            else projection["map_revision"] if source_value == "map_revision"
            else selection.get(source_value)
        )
        if source_value == "path" and authoritative_scene is None:
            raise TacticalGridError("Tactical owner path is unavailable")
        if not _selection_value_valid(
            source_value,
            selected,
            projection,
            action_id=action_id,
        ):
            raise TacticalGridError(f"Tactical {source_value} selection is invalid")
        if source_value == "edges":
            edges = cast(
                list[tuple[tuple[int, int], tuple[int, int]]], _grid_edges(selected, projection)
            )
            selected = _edge_segments(
                edges,
                action.get("edge_segment"),
                projection,
            )
        reference = action.get('actor_reference' if source_value == 'actor' else 'target_reference')
        if source_value in {'actor', 'target'} and reference is not None:
            if reference != projection.get('actor_reference'):
                raise TacticalGridError('Coordinate actor reference is unavailable')
            selected = next(item.get('actor_ref') for item in projection['entities']
                            if item['id'] == selected)
            if not isinstance(selected, str) or not selected:
                raise TacticalGridError('Coordinate actor reference is invalid or ambiguous')
        parameters[parameter] = deepcopy(selected)
    try:
        return owned_payload(parameters)
    except (TypeError, ValueError) as error:
        raise TacticalGridError("Tactical parameters are not canonical JSON") from error


__all__ = (
    "TACTICAL_SELECTION_BINDINGS",
    "TacticalGridError",
    "project_tactical_grid",
    "project_coordinate_map",
    "public_edge_segment",
    "coordinate_actor_reference",
    "resolve_surface_source",
    "resolve_tactical_action_parameters",
)
