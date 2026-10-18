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

TACTICAL_SELECTION_BINDINGS = TACTICAL_UI_DYNAMIC_SELECTIONS

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
_SELECTION_FIELDS = TACTICAL_SELECTION_BINDINGS
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

    geometry: list[dict[str, object]] = []
    for item in cast(list[dict[str, object]], scene["geometry"]):
        display = _public_display(item["metadata"])
        if display is None:
            continue
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
        zones.append(
            {
                "id": item["id"],
                "map_id": item["map_id"],
                "shape": deepcopy(item["shape"]),
                "display": display,
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


def player_known_spatial_scene(value: object) -> SafePayload:
    """Use the renderer's exact visibility boundary for a physical preview's scene inputs."""

    admitted_scene = admit_spatial_scene(value)
    scene = admitted_scene.as_payload()
    visible_anchors = _visible_anchor_ids(scene)
    for category in ("geometry", "entities", "zones"):
        scene[category] = [
            {**item, "metadata": {"player": display}}
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
    return owned_payload(projected)


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


def _selection_value_valid(
    source: str,
    value: object,
    projection: Mapping[str, object],
    *,
    action_id: str,
) -> bool:
    entity_ids = {
        item.get("id")
        for item in cast(Sequence[Mapping[str, object]], projection.get("entities", ()))
    }
    if source in {"actor", "target", "device"}:
        return isinstance(value, str) and value in entity_ids
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
    client_sources = binding_sources - {"path"}
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
    "coordinate_actor_reference",
    "resolve_surface_source",
    "resolve_tactical_action_parameters",
)
