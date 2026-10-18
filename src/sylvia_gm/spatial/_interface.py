"""Closed spatial data contracts without package-specific consequences."""

from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Never, cast

from sylvia_gm.domain.authority import SafePayload, owned_payload, thaw_json_value
from sylvia_gm.domain.package_library import canonical_json_bytes

SPATIAL_ALGORITHM_VERSION = "sylvia.spatial.supercover.v2"

_IDENTITY = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$")
_MAX_ABSOLUTE_COORDINATE = 1_000_000.0
_MAX_AXIS_CELLS = 2_048
_MAX_TOTAL_CELLS = 4_000_000
_MAX_GEOMETRY = 20_000
_MAX_ENTITIES = 5_000
_MAX_ZONES = 5_000
_MAX_TERRAIN = 100_000
_MAX_JSON_DEPTH = 24
_MAX_JSON_NODES = 100_000
_MAX_METADATA_BYTES = 16_384
_INCLUDE_ORDER = ("geometry", "entities", "zones")

_SCENE_KEYS = frozenset({"format", "map", "geometry", "entities", "zones", "terrain"})
_MAP_KEYS = frozenset({"id", "revision", "bounds", "grid", "layers"})
_BOUNDS_KEYS = frozenset({"min", "max"})
_GRID_KEYS = frozenset({"cell_size", "unit", "origin"})
_LAYER_KEYS = frozenset({"id", "min_elevation", "max_elevation"})
_GEOMETRY_KEYS = frozenset(
    {
        "id",
        "map_id",
        "volume",
        "active",
        "movement",
        "occupies_cells",
        "visibility",
        "metadata",
        "destroyed",
    }
)
_DESTROYED_KEYS = frozenset({"active", "movement", "visibility"})
_ENTITY_KEYS = frozenset(
    {
        "id",
        "map_id",
        "position",
        "footprint",
        "height",
        "facing",
        "active",
        "movement",
        "visibility",
        "metadata",
    }
)
_ZONE_KEYS = frozenset({"id", "map_id", "shape", "metadata"})
_TERRAIN_KEYS = frozenset({"cell", "layer_id", "cost"})
_MUTATION_KEYS = frozenset({"map_id", "expected_revision", "target", "patch"})
_MUTATION_TARGET_KEYS = frozenset({"kind", "id"})
_MUTABLE_FIELDS = {
    "geometry": frozenset(
        {
            "volume",
            "active",
            "movement",
            "occupies_cells",
            "visibility",
            "metadata",
        }
    ),
    "entity": frozenset(
        {
            "position",
            "footprint",
            "height",
            "facing",
            "active",
            "movement",
            "visibility",
            "metadata",
        }
    ),
}

_QUERY_KEYS = {
    "body_proximity": frozenset(
        {
            "kind",
            "map_id",
            "moving_entity_id",
            "fixed_entity_id",
            "destination",
            "radius",
        }
    ),
    "sweep": frozenset(
        {
            "kind",
            "map_id",
            "moving_entity_id",
            "destination",
            "include_entities",
            "exclude_entity_ids",
            "metadata_filter",
        }
    ),
    "placement": frozenset(
        {
            "kind",
            "map_id",
            "moving_entity_id",
            "origin",
            "exclude_entity_ids",
            "metadata_filter",
            "max_candidates",
            "containing_zone_id",
            "reference_face",
        }
    ),
    "ray": frozenset({"kind", "map_id", "origin", "destination", "include", "max_intersections"}),
    "exposure": frozenset(
        {"kind", "map_id", "observer", "target", "samples", "include", "metadata_filter"}
    ),
    "path": frozenset(
        {
            "kind",
            "map_id",
            "start",
            "goal",
            "allow_diagonal",
            "corner_policy",
            "include_entities",
            "moving_entity_id",
            "metadata_filter",
            "max_steps",
        }
    ),
    "crossed_zones": frozenset({"kind", "map_id", "path", "max_crossings"}),
    "area": frozenset({"kind", "map_id", "shape", "include", "max_results"}),
}

_RESULT_KEYS = {
    "body_proximity": frozenset(
        {
            "kind",
            "map_id",
            "snapshot_id",
            "geometry_revision",
            "algorithm_version",
            "hit",
            "entry",
            "exit",
            "minimum_distance",
        }
    ),
    "sweep": frozenset(
        {
            "kind",
            "map_id",
            "snapshot_id",
            "geometry_revision",
            "algorithm_version",
            "supported",
            "clear",
            "distance",
            "blockers",
            "reason",
        }
    ),
    "placement": frozenset(
        {
            "kind",
            "map_id",
            "snapshot_id",
            "geometry_revision",
            "algorithm_version",
            "supported",
            "found",
            "position",
            "distance",
            "reason",
            "examined",
        }
    ),
    "ray": frozenset(
        {
            "kind",
            "map_id",
            "snapshot_id",
            "geometry_revision",
            "algorithm_version",
            "cells",
            "intersections",
            "truncated",
        }
    ),
    "exposure": frozenset(
        {
            "kind",
            "map_id",
            "snapshot_id",
            "geometry_revision",
            "algorithm_version",
            "samples",
            "visible_samples",
            "exposure",
            "occluders",
        }
    ),
    "path": frozenset(
        {
            "kind",
            "map_id",
            "snapshot_id",
            "geometry_revision",
            "algorithm_version",
            "found",
            "cells",
            "cost",
            "expanded",
            "truncated",
        }
    ),
    "crossed_zones": frozenset(
        {
            "kind",
            "map_id",
            "snapshot_id",
            "geometry_revision",
            "algorithm_version",
            "crossings",
            "truncated",
        }
    ),
    "area": frozenset(
        {
            "kind",
            "map_id",
            "snapshot_id",
            "geometry_revision",
            "algorithm_version",
            "results",
            "truncated",
        }
    ),
}


class SpatialValidationError(ValueError):
    """A safe, classified rejection of spatial state, request, or result."""

    def __init__(self, message: str, *, code: str, path: str = "$") -> None:
        super().__init__(f"{message} at {path}")
        self.code = code
        self.path = path


@dataclass(frozen=True)
class AdmittedSpatialScene:
    """An immutable authoritative scene with stable snapshot provenance."""

    _payload: SafePayload
    map_id: str
    geometry_revision: int
    snapshot_id: str
    algorithm_version: str = SPATIAL_ALGORITHM_VERSION

    def as_payload(self) -> dict[str, object]:
        return cast(dict[str, object], thaw_json_value(self._payload))


@dataclass(frozen=True)
class AdmittedSpatialQuery:
    """One closed bounded query against an admitted scene."""

    _payload: SafePayload
    kind: str
    map_id: str

    def as_payload(self) -> dict[str, object]:
        return cast(dict[str, object], thaw_json_value(self._payload))


@dataclass(frozen=True)
class AdmittedSpatialResult:
    """One closed canonical result bound to an immutable scene identity."""

    _payload: SafePayload
    kind: str
    snapshot_id: str

    def as_payload(self) -> dict[str, object]:
        return cast(dict[str, object], thaw_json_value(self._payload))


@dataclass(frozen=True)
class AdmittedSpatialMutation:
    """One validated scene replacement and its before/after evidence."""

    _payload: SafePayload
    scene: AdmittedSpatialScene

    def as_payload(self) -> dict[str, object]:
        return cast(dict[str, object], thaw_json_value(self._payload))


def _reject(message: str, *, code: str, path: str) -> Never:
    raise SpatialValidationError(message, code=code, path=path)


def _capture_payload(value: object, *, code: str) -> dict[str, object]:
    stack: list[tuple[object, int, str]] = [(value, 1, "$")]
    nodes = 0
    while stack:
        item, depth, path = stack.pop()
        if depth > _MAX_JSON_DEPTH:
            _reject(
                "Spatial JSON nesting exceeds the limit", code="spatial_limit_exceeded", path=path
            )
        nodes += 1
        if nodes > _MAX_JSON_NODES:
            _reject(
                "Spatial JSON node count exceeds the limit",
                code="spatial_limit_exceeded",
                path=path,
            )
        if isinstance(item, Mapping):
            for key, child in item.items():
                if not isinstance(key, str):
                    _reject("Spatial JSON keys must be strings", code=code, path=path)
                stack.append((child, depth + 1, f"{path}.{key}"))
        elif isinstance(item, Sequence) and not isinstance(item, (str, bytes, bytearray)):
            stack.extend((child, depth + 1, f"{path}[{index}]") for index, child in enumerate(item))
        elif (
            item is None
            or isinstance(item, (str, bool, int))
            or isinstance(item, float)
            and math.isfinite(item)
        ):
            continue
        else:
            _reject("Spatial values must be finite JSON", code=code, path=path)
    try:
        captured = thaw_json_value(value)
    except (TypeError, ValueError) as error:
        raise SpatialValidationError("Spatial value is invalid", code=code) from error
    if not isinstance(captured, dict):
        _reject("Spatial payload must be an object", code=code, path="$")
    return cast(dict[str, object], captured)


def _object(value: object, keys: frozenset[str], *, code: str, path: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _reject("Spatial object is required", code=code, path=path)
    captured = {str(key): item for key, item in value.items()}
    if frozenset(captured) != keys:
        _reject("Spatial object uses a closed schema", code=code, path=path)
    return captured


def _array(value: object, *, minimum: int, maximum: int, code: str, path: str) -> list[object]:
    if (
        not isinstance(value, Sequence)
        or isinstance(value, (str, bytes, bytearray))
        or not minimum <= len(value) <= maximum
    ):
        _reject("Spatial array length is invalid", code=code, path=path)
    return list(value)


def _number(value: object, *, minimum: float | None, code: str, path: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        _reject("Finite number is required", code=code, path=path)
    number = float(value)
    if not math.isfinite(number) or abs(number) > _MAX_ABSOLUTE_COORDINATE:
        _reject("Spatial number is non-finite or outside limits", code=code, path=path)
    if minimum is not None and number < minimum:
        _reject("Spatial number is below its minimum", code=code, path=path)
    return value


def _integer(
    value: object,
    *,
    minimum: int,
    maximum: int,
    code: str,
    path: str,
) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        _reject("Bounded integer is required", code=code, path=path)
    return value


def _boolean(value: object, *, code: str, path: str) -> bool:
    if not isinstance(value, bool):
        _reject("Boolean is required", code=code, path=path)
    return value


def _identity(value: object, *, code: str, path: str) -> str:
    if not isinstance(value, str) or _IDENTITY.fullmatch(value) is None:
        _reject("Stable spatial identity is invalid", code=code, path=path)
    return value


def _coordinate(value: object, dimensions: int, *, code: str, path: str) -> list[int | float]:
    items = _array(value, minimum=dimensions, maximum=dimensions, code=code, path=path)
    return [
        _number(item, minimum=None, code=code, path=f"{path}[{index}]")
        for index, item in enumerate(items)
    ]


def _cell(value: object, *, code: str, path: str) -> list[int]:
    items = _array(value, minimum=2, maximum=2, code=code, path=path)
    return [
        _integer(item, minimum=0, maximum=_MAX_AXIS_CELLS - 1, code=code, path=f"{path}[{index}]")
        for index, item in enumerate(items)
    ]


def _enum(value: object, allowed: frozenset[str], *, code: str, path: str) -> str:
    if not isinstance(value, str) or value not in allowed:
        _reject("Spatial enum value is invalid", code=code, path=path)
    return value


def _metadata(value: object, *, code: str, path: str) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _reject("Spatial metadata must be an object", code=code, path=path)
    try:
        payload = cast(dict[str, object], thaw_json_value(value))
        if len(canonical_json_bytes(payload)) > _MAX_METADATA_BYTES:
            _reject(
                "Spatial metadata exceeds its byte limit", code="spatial_limit_exceeded", path=path
            )
        return payload
    except (TypeError, ValueError) as error:
        raise SpatialValidationError("Spatial metadata is invalid", code=code, path=path) from error


def _inside(point: Sequence[int | float], bounds: Mapping[str, object]) -> bool:
    lower = cast(Sequence[int | float], bounds["min"])
    upper = cast(Sequence[int | float], bounds["max"])
    return all(
        float(lower[index]) <= float(value) <= float(upper[index])
        for index, value in enumerate(point)
    )


def _grid_cell_counts(map_value: Mapping[str, object]) -> tuple[int, int]:
    bounds = cast(Mapping[str, object], map_value["bounds"])
    lower = cast(Sequence[int | float], bounds["min"])
    upper = cast(Sequence[int | float], bounds["max"])
    grid = cast(Mapping[str, object], map_value["grid"])
    cell_size = cast(int | float, grid["cell_size"])
    return cast(
        tuple[int, int],
        tuple(
            math.ceil((float(upper[index]) - float(lower[index])) / float(cell_size))
            for index in range(2)
        ),
    )


def _box(value: object, *, bounds: Mapping[str, object], code: str, path: str) -> dict[str, object]:
    item = _object(value, frozenset({"kind", "min", "max"}), code=code, path=path)
    if item["kind"] != "box":
        _reject("Box volume kind is required", code=code, path=f"{path}.kind")
    lower = _coordinate(item["min"], 3, code=code, path=f"{path}.min")
    upper = _coordinate(item["max"], 3, code=code, path=f"{path}.max")
    if any(float(lower[index]) >= float(upper[index]) for index in range(3)):
        _reject("Box volume must have positive extent", code=code, path=path)
    if not _inside(lower, bounds) or not _inside(upper, bounds):
        _reject("Box volume is outside map bounds", code="spatial_out_of_bounds", path=path)
    return {"kind": "box", "min": lower, "max": upper}


def _shape(
    value: object, *, bounds: Mapping[str, object], code: str, path: str
) -> dict[str, object]:
    if not isinstance(value, Mapping):
        _reject("Spatial shape must be an object", code=code, path=path)
    if value.get("kind") == "box":
        return _box(value, bounds=bounds, code=code, path=path)
    item = _object(
        value,
        frozenset({"kind", "center", "radius", "height"}),
        code=code,
        path=path,
    )
    if item["kind"] != "cylinder":
        _reject("Spatial shape kind is invalid", code=code, path=f"{path}.kind")
    center = _coordinate(item["center"], 3, code=code, path=f"{path}.center")
    radius = _number(item["radius"], minimum=0.000001, code=code, path=f"{path}.radius")
    height = _number(item["height"], minimum=0.000001, code=code, path=f"{path}.height")
    cylinder_min = [float(center[0]) - float(radius), float(center[1]) - float(radius), center[2]]
    cylinder_max = [
        float(center[0]) + float(radius),
        float(center[1]) + float(radius),
        float(center[2]) + float(height),
    ]
    if not _inside(cylinder_min, bounds) or not _inside(cylinder_max, bounds):
        _reject("Cylinder is outside map bounds", code="spatial_out_of_bounds", path=path)
    return {"kind": "cylinder", "center": center, "radius": radius, "height": height}


def _area_prism(
    value: Mapping[str, object], *, bounds: Mapping[str, object], code: str, path: str
) -> dict[str, object]:
    """Admit a rectangular query volume extending from the center of its origin face."""
    item = _object(
        value,
        frozenset({"kind", "origin", "direction", "up", "length", "width", "height"}),
        code=code,
        path=path,
    )
    origin = _coordinate(item["origin"], 3, code=code, path=f"{path}.origin")
    if not _inside(origin, bounds):
        _reject(
            "Area origin is outside map bounds", code="spatial_out_of_bounds", path=f"{path}.origin"
        )
    vectors = {}
    for field in ("direction", "up"):
        vector = _coordinate(item[field], 3, code=code, path=f"{path}.{field}")
        scale = max(abs(component) for component in vector)
        if scale == 0:
            _reject("Prism direction and up must be nonzero", code=code, path=f"{path}.{field}")
        scaled = [component / scale for component in vector]
        magnitude = math.hypot(*scaled)
        vectors[field] = [component / magnitude for component in scaled]
    axis, reference = vectors["direction"], vectors["up"]
    perpendicular = [
        reference[1] * axis[2] - reference[2] * axis[1],
        reference[2] * axis[0] - reference[0] * axis[2],
        reference[0] * axis[1] - reference[1] * axis[0],
    ]
    if math.hypot(*perpendicular) <= 1e-12:
        _reject("Prism up must be distinguishable from its direction", code=code, path=f"{path}.up")
    dimensions = {
        field: _number(item[field], minimum=0.000001, code=code, path=f"{path}.{field}")
        for field in ("length", "width", "height")
    }
    return {"kind": "prism", "origin": origin, **vectors, **dimensions}


def _area_shape(
    value: object, *, bounds: Mapping[str, object], code: str, path: str
) -> dict[str, object]:
    """Admit query-only volumes; their origin must be on the selected map."""
    if isinstance(value, Mapping) and value.get("kind") == "prism":
        return _area_prism(value, bounds=bounds, code=code, path=path)
    if not isinstance(value, Mapping) or (
        value.get("kind") != "sphere" and value.get("kind") != "cone"
    ):
        return _shape(value, bounds=bounds, code=code, path=path)
    kind = value["kind"]
    fields = (
        {"kind", "center", "radius"}
        if kind == "sphere"
        else {"kind", "apex", "direction", "length", "radius"}
    )
    item = _object(value, frozenset(fields), code=code, path=path)
    origin_field = "center" if kind == "sphere" else "apex"
    origin = _coordinate(item[origin_field], 3, code=code, path=f"{path}.{origin_field}")
    if not _inside(origin, bounds):
        _reject(
            "Area origin is outside map bounds",
            code="spatial_out_of_bounds",
            path=f"{path}.{origin_field}",
        )
    radius = _number(item["radius"], minimum=0.000001, code=code, path=f"{path}.radius")
    normalized: dict[str, object] = {"kind": kind, origin_field: origin, "radius": radius}
    extent = float(radius)
    if kind == "cone":
        direction = _coordinate(item["direction"], 3, code=code, path=f"{path}.direction")
        magnitude = math.hypot(*direction)
        if magnitude == 0 or not math.isfinite(magnitude):
            _reject(
                "Cone direction must be finite and nonzero", code=code, path=f"{path}.direction"
            )
        length = _number(item["length"], minimum=0.000001, code=code, path=f"{path}.length")
        normalized.update({"direction": direction, "length": length})
        extent += float(length)
    if not all(
        math.isfinite(float(component) + extent) and math.isfinite(float(component) - extent)
        for component in origin
    ):
        _reject("Area extent must be finite", code=code, path=path)
    return normalized


def _movement(value: object, *, code: str, path: str) -> str:
    return _enum(value, frozenset({"passable", "blocking"}), code=code, path=path)


def _visibility(value: object, *, code: str, path: str) -> str:
    return _enum(value, frozenset({"clear", "opaque"}), code=code, path=path)


def _validate_scene(payload: dict[str, object]) -> dict[str, object]:
    code = "spatial_scene_invalid"
    scene = _object(payload, _SCENE_KEYS, code=code, path="$")
    if scene["format"] != 1:
        _reject("Spatial scene format is unsupported", code=code, path="$.format")
    map_value = _object(scene["map"], _MAP_KEYS, code=code, path="$.map")
    map_id = _identity(map_value["id"], code=code, path="$.map.id")
    revision = _integer(
        map_value["revision"], minimum=0, maximum=2**63 - 1, code=code, path="$.map.revision"
    )
    bounds_value = _object(map_value["bounds"], _BOUNDS_KEYS, code=code, path="$.map.bounds")
    lower = _coordinate(bounds_value["min"], 3, code=code, path="$.map.bounds.min")
    upper = _coordinate(bounds_value["max"], 3, code=code, path="$.map.bounds.max")
    if any(float(lower[index]) >= float(upper[index]) for index in range(3)):
        _reject("Map bounds must have positive extent", code=code, path="$.map.bounds")
    bounds: dict[str, object] = {"min": lower, "max": upper}

    grid_value = _object(map_value["grid"], _GRID_KEYS, code=code, path="$.map.grid")
    cell_size = _number(
        grid_value["cell_size"], minimum=0.000001, code=code, path="$.map.grid.cell_size"
    )
    unit = _identity(grid_value["unit"], code=code, path="$.map.grid.unit")
    origin = _coordinate(grid_value["origin"], 2, code=code, path="$.map.grid.origin")
    if [float(item) for item in origin] != [float(lower[0]), float(lower[1])]:
        _reject("Grid origin must match the lower map bounds", code=code, path="$.map.grid.origin")
    axis_cells = list(
        _grid_cell_counts(
            {
                "bounds": bounds,
                "grid": {"cell_size": cell_size, "unit": unit, "origin": origin},
            }
        )
    )
    if (
        any(count > _MAX_AXIS_CELLS for count in axis_cells)
        or math.prod(axis_cells) > _MAX_TOTAL_CELLS
    ):
        _reject(
            "Map cell count exceeds spatial limits", code="spatial_limit_exceeded", path="$.map"
        )

    layers_input = _array(
        map_value["layers"], minimum=1, maximum=128, code=code, path="$.map.layers"
    )
    layers: list[dict[str, object]] = []
    layer_ids: set[str] = set()
    for index, raw in enumerate(layers_input):
        path = f"$.map.layers[{index}]"
        item = _object(raw, _LAYER_KEYS, code=code, path=path)
        layer_id = _identity(item["id"], code=code, path=f"{path}.id")
        if layer_id in layer_ids:
            _reject(
                "Layer identities must be unique", code="spatial_duplicate_id", path=f"{path}.id"
            )
        layer_ids.add(layer_id)
        minimum = _number(
            item["min_elevation"], minimum=None, code=code, path=f"{path}.min_elevation"
        )
        maximum = _number(
            item["max_elevation"], minimum=None, code=code, path=f"{path}.max_elevation"
        )
        if (
            float(minimum) >= float(maximum)
            or not _inside([lower[0], lower[1], minimum], bounds)
            or not _inside([upper[0], upper[1], maximum], bounds)
        ):
            _reject(
                "Layer elevation is outside map bounds", code="spatial_out_of_bounds", path=path
            )
        layers.append({"id": layer_id, "min_elevation": minimum, "max_elevation": maximum})
    layers.sort(key=lambda item: cast(str, item["id"]))

    seen_ids: set[str] = set()
    geometry_input = _array(
        scene["geometry"], minimum=0, maximum=_MAX_GEOMETRY, code=code, path="$.geometry"
    )
    geometry: list[dict[str, object]] = []
    for index, raw in enumerate(geometry_input):
        path = f"$.geometry[{index}]"
        if isinstance(raw, Mapping):
            raw = {"occupies_cells": True, **raw}
        item = _object(raw, _GEOMETRY_KEYS, code=code, path=path)
        item_id = _identity(item["id"], code=code, path=f"{path}.id")
        if item_id in seen_ids:
            _reject(
                "Spatial identities must be unique", code="spatial_duplicate_id", path=f"{path}.id"
            )
        seen_ids.add(item_id)
        if item["map_id"] != map_id:
            _reject(
                "Spatial object belongs to another map",
                code="spatial_cross_map",
                path=f"{path}.map_id",
            )
        destroyed = _object(item["destroyed"], _DESTROYED_KEYS, code=code, path=f"{path}.destroyed")
        geometry.append(
            {
                "id": item_id,
                "map_id": map_id,
                "volume": _box(item["volume"], bounds=bounds, code=code, path=f"{path}.volume"),
                "active": _boolean(item["active"], code=code, path=f"{path}.active"),
                "movement": _movement(item["movement"], code=code, path=f"{path}.movement"),
                "occupies_cells": _boolean(
                    item["occupies_cells"],
                    code=code,
                    path=f"{path}.occupies_cells",
                ),
                "visibility": _visibility(item["visibility"], code=code, path=f"{path}.visibility"),
                "metadata": _metadata(item["metadata"], code=code, path=f"{path}.metadata"),
                "destroyed": {
                    "active": _boolean(
                        destroyed["active"], code=code, path=f"{path}.destroyed.active"
                    ),
                    "movement": _movement(
                        destroyed["movement"], code=code, path=f"{path}.destroyed.movement"
                    ),
                    "visibility": _visibility(
                        destroyed["visibility"], code=code, path=f"{path}.destroyed.visibility"
                    ),
                },
            }
        )
    geometry.sort(key=lambda item: cast(str, item["id"]))

    entities_input = _array(
        scene["entities"], minimum=0, maximum=_MAX_ENTITIES, code=code, path="$.entities"
    )
    entities: list[dict[str, object]] = []
    for index, raw in enumerate(entities_input):
        path = f"$.entities[{index}]"
        item = _object(raw, _ENTITY_KEYS, code=code, path=path)
        item_id = _identity(item["id"], code=code, path=f"{path}.id")
        if item_id in seen_ids:
            _reject(
                "Spatial identities must be unique", code="spatial_duplicate_id", path=f"{path}.id"
            )
        seen_ids.add(item_id)
        if item["map_id"] != map_id:
            _reject(
                "Spatial object belongs to another map",
                code="spatial_cross_map",
                path=f"{path}.map_id",
            )
        position = _coordinate(item["position"], 3, code=code, path=f"{path}.position")
        footprint_raw = cast(Mapping[str, object], item["footprint"])
        if footprint_raw.get("kind") == "box":
            footprint = _object(
                item["footprint"],
                frozenset({"kind", "half_extents"}),
                code=code,
                path=f"{path}.footprint",
            )
            half_extents = _coordinate(
                footprint["half_extents"], 2, code=code, path=f"{path}.footprint.half_extents"
            )
            if any(float(value) <= 0 for value in half_extents):
                _reject("Footprint extent must be positive", code=code, path=f"{path}.footprint")
            normalized_footprint: dict[str, object] = {"kind": "box", "half_extents": half_extents}
            extents = [float(half_extents[0]), float(half_extents[1])]
        else:
            footprint = _object(
                item["footprint"],
                frozenset({"kind", "radius"}),
                code=code,
                path=f"{path}.footprint",
            )
            if footprint["kind"] != "circle":
                _reject("Footprint kind is invalid", code=code, path=f"{path}.footprint.kind")
            radius = _number(
                footprint["radius"], minimum=0.000001, code=code, path=f"{path}.footprint.radius"
            )
            normalized_footprint = {"kind": "circle", "radius": radius}
            extents = [float(radius), float(radius)]
        height = _number(item["height"], minimum=0.000001, code=code, path=f"{path}.height")
        entity_min = [float(position[0]) - extents[0], float(position[1]) - extents[1], position[2]]
        entity_max = [
            float(position[0]) + extents[0],
            float(position[1]) + extents[1],
            float(position[2]) + float(height),
        ]
        if not _inside(entity_min, bounds) or not _inside(entity_max, bounds):
            _reject("Entity volume is outside map bounds", code="spatial_out_of_bounds", path=path)
        facing = _coordinate(item["facing"], 2, code=code, path=f"{path}.facing")
        if math.hypot(float(facing[0]), float(facing[1])) == 0:
            _reject("Entity facing must be nonzero", code=code, path=f"{path}.facing")
        entities.append(
            {
                "id": item_id,
                "map_id": map_id,
                "position": position,
                "footprint": normalized_footprint,
                "height": height,
                "facing": facing,
                "active": _boolean(item["active"], code=code, path=f"{path}.active"),
                "movement": _movement(item["movement"], code=code, path=f"{path}.movement"),
                "visibility": _visibility(item["visibility"], code=code, path=f"{path}.visibility"),
                "metadata": _metadata(item["metadata"], code=code, path=f"{path}.metadata"),
            }
        )
    entities.sort(key=lambda item: cast(str, item["id"]))

    zones_input = _array(scene["zones"], minimum=0, maximum=_MAX_ZONES, code=code, path="$.zones")
    zones: list[dict[str, object]] = []
    for index, raw in enumerate(zones_input):
        path = f"$.zones[{index}]"
        item = _object(raw, _ZONE_KEYS, code=code, path=path)
        item_id = _identity(item["id"], code=code, path=f"{path}.id")
        if item_id in seen_ids:
            _reject(
                "Spatial identities must be unique", code="spatial_duplicate_id", path=f"{path}.id"
            )
        seen_ids.add(item_id)
        if item["map_id"] != map_id:
            _reject(
                "Spatial object belongs to another map",
                code="spatial_cross_map",
                path=f"{path}.map_id",
            )
        zones.append(
            {
                "id": item_id,
                "map_id": map_id,
                "shape": _shape(item["shape"], bounds=bounds, code=code, path=f"{path}.shape"),
                "metadata": _metadata(item["metadata"], code=code, path=f"{path}.metadata"),
            }
        )
    zones.sort(key=lambda item: cast(str, item["id"]))

    terrain_input = _array(
        scene["terrain"], minimum=0, maximum=_MAX_TERRAIN, code=code, path="$.terrain"
    )
    terrain: list[dict[str, object]] = []
    terrain_cells: set[tuple[int, int, str]] = set()
    for index, raw in enumerate(terrain_input):
        path = f"$.terrain[{index}]"
        item = _object(raw, _TERRAIN_KEYS, code=code, path=path)
        cell = _cell(item["cell"], code=code, path=f"{path}.cell")
        if cell[0] >= axis_cells[0] or cell[1] >= axis_cells[1]:
            _reject(
                "Terrain cell is outside map bounds",
                code="spatial_out_of_bounds",
                path=f"{path}.cell",
            )
        layer_id = _identity(item["layer_id"], code=code, path=f"{path}.layer_id")
        if layer_id not in layer_ids:
            _reject("Terrain layer is unknown", code=code, path=f"{path}.layer_id")
        key = (cell[0], cell[1], layer_id)
        if key in terrain_cells:
            _reject(
                "Terrain cells must be unique per layer", code="spatial_duplicate_id", path=path
            )
        terrain_cells.add(key)
        terrain.append(
            {
                "cell": cell,
                "layer_id": layer_id,
                "cost": _number(item["cost"], minimum=0.000001, code=code, path=f"{path}.cost"),
            }
        )
    terrain.sort(
        key=lambda item: (
            cast(list[int], item["cell"])[1],
            cast(list[int], item["cell"])[0],
            cast(str, item["layer_id"]),
        )
    )

    return {
        "format": 1,
        "map": {
            "id": map_id,
            "revision": revision,
            "bounds": bounds,
            "grid": {"cell_size": cell_size, "unit": unit, "origin": origin},
            "layers": layers,
        },
        "geometry": geometry,
        "entities": entities,
        "zones": zones,
        "terrain": terrain,
    }


def admit_spatial_scene(value: object) -> AdmittedSpatialScene:
    """Validate and immutably capture one complete owner-authoritative scene."""

    payload = _validate_scene(_capture_payload(value, code="spatial_scene_invalid"))
    map_value = cast(dict[str, object], payload["map"])
    snapshot_id = hashlib.sha256(
        canonical_json_bytes({"algorithm_version": SPATIAL_ALGORITHM_VERSION, "scene": payload})
    ).hexdigest()
    return AdmittedSpatialScene(
        _payload=owned_payload(payload),
        map_id=cast(str, map_value["id"]),
        geometry_revision=cast(int, map_value["revision"]),
        snapshot_id=snapshot_id,
    )


def _scene_sha256(scene: AdmittedSpatialScene) -> str:
    return hashlib.sha256(canonical_json_bytes(scene.as_payload())).hexdigest()


def mutate_spatial_scene(
    scene: AdmittedSpatialScene,
    value: object,
) -> AdmittedSpatialMutation:
    """Apply one exact neutral object patch and advance the authoritative map revision."""

    code = "spatial_mutation_invalid"
    request = _object(_capture_payload(value, code=code), _MUTATION_KEYS, code=code, path="$")
    if request["map_id"] != scene.map_id:
        _reject(
            "Spatial mutation belongs to another map",
            code="spatial_cross_map",
            path="$.map_id",
        )
    expected_revision = _integer(
        request["expected_revision"],
        minimum=0,
        maximum=2**63 - 1,
        code=code,
        path="$.expected_revision",
    )
    if expected_revision != scene.geometry_revision:
        _reject(
            "Spatial mutation expected revision is stale",
            code="spatial_stale_revision",
            path="$.expected_revision",
        )
    target = _object(request["target"], _MUTATION_TARGET_KEYS, code=code, path="$.target")
    target_kind = _enum(target["kind"], frozenset(_MUTABLE_FIELDS), code=code, path="$.target.kind")
    target_id = _identity(target["id"], code=code, path="$.target.id")
    patch = _capture_payload(request["patch"], code=code)
    if not patch or set(patch) - _MUTABLE_FIELDS[target_kind]:
        _reject("Spatial mutation patch uses unsupported fields", code=code, path="$.patch")

    scene_payload = scene.as_payload()
    collection_name = "geometry" if target_kind == "geometry" else "entities"
    collection = cast(list[dict[str, object]], scene_payload[collection_name])
    matches = [item for item in collection if item["id"] == target_id]
    if not matches:
        _reject(
            "Spatial mutation target is missing",
            code="spatial_target_missing",
            path="$.target.id",
        )
    if len(matches) != 1:
        _reject(
            "Spatial mutation target is duplicated",
            code="spatial_duplicate_id",
            path="$.target.id",
        )
    before_target = matches[0]
    replacement = {**before_target, **patch}
    collection[collection.index(before_target)] = replacement
    map_value = cast(dict[str, object], scene_payload["map"])
    map_value["revision"] = scene.geometry_revision + 1
    advanced = admit_spatial_scene(scene_payload)
    advanced_payload = advanced.as_payload()
    after_target = next(
        item
        for item in cast(list[dict[str, object]], advanced_payload[collection_name])
        if item["id"] == target_id
    )
    result = {
        "kind": "scene_mutation",
        "map_id": scene.map_id,
        "geometry_revision": advanced.geometry_revision,
        "snapshot_id": advanced.snapshot_id,
        "algorithm_version": SPATIAL_ALGORITHM_VERSION,
        "target": {"kind": target_kind, "id": target_id},
        "patch": {key: after_target[key] for key in sorted(patch)},
        "before": {
            "geometry_revision": scene.geometry_revision,
            "snapshot_id": scene.snapshot_id,
            "scene_sha256": _scene_sha256(scene),
            "target_sha256": hashlib.sha256(canonical_json_bytes(before_target)).hexdigest(),
        },
        "after": {
            "geometry_revision": advanced.geometry_revision,
            "snapshot_id": advanced.snapshot_id,
            "scene_sha256": _scene_sha256(advanced),
            "target_sha256": hashlib.sha256(canonical_json_bytes(after_target)).hexdigest(),
        },
    }
    return AdmittedSpatialMutation(owned_payload(result), advanced)


def _query_include(value: object, allowed: frozenset[str], *, path: str) -> list[str]:
    items = _array(value, minimum=1, maximum=len(allowed), code="spatial_query_invalid", path=path)
    normalized: list[str] = []
    for index, item in enumerate(items):
        normalized.append(
            _enum(item, allowed, code="spatial_query_invalid", path=f"{path}[{index}]")
        )
    if len(set(normalized)) != len(normalized):
        _reject("Query includes must be unique", code="spatial_query_invalid", path=path)
    return [item for item in _INCLUDE_ORDER if item in normalized]


def _metadata_selector(value: object, *, code: str, path: str) -> dict[str, object]:
    """Admit the shared optional spatial selector with bounded keys and exact missing semantics."""
    selector = _object(value, frozenset({"key", "value", "missing_matches"}), code=code, path=path)
    if (
        not isinstance(selector["key"], str)
        or len(selector["key"]) > 128
        or not isinstance(selector["value"], str)
        or len(selector["value"]) > 128
    ):
        _reject("Spatial metadata filter requires bounded strings", code=code, path=path)
    return {
        "key": selector["key"],
        "value": selector["value"],
        "missing_matches": _boolean(
            selector["missing_matches"], code=code, path=path + ".missing_matches"
        ),
    }


def admit_spatial_query(scene: AdmittedSpatialScene, value: object) -> AdmittedSpatialQuery:
    """Validate a bounded query against one authoritative admitted scene."""

    code = "spatial_query_invalid"
    payload = _capture_payload(value, code=code)
    kind = payload.get("kind")
    if not isinstance(kind, str) or kind not in _QUERY_KEYS:
        _reject("Spatial query kind is invalid", code=code, path="$.kind")
    expected_keys = _QUERY_KEYS[kind]
    if kind == "path" and "moving_entity_id" not in payload:
        expected_keys = expected_keys - {"moving_entity_id"}
    if kind in {"path", "exposure"} and "metadata_filter" not in payload:
        expected_keys = expected_keys - {"metadata_filter"}
    if kind == "sweep":
        expected_keys = expected_keys - ({"metadata_filter", "exclude_entity_ids"} - set(payload))
    if kind == "placement":
        expected_keys = expected_keys - ({"containing_zone_id", "reference_face"} - set(payload))
    item = _object(payload, expected_keys, code=code, path="$")
    if item["map_id"] != scene.map_id:
        _reject("Spatial query belongs to another map", code="spatial_cross_map", path="$.map_id")
    admitted = scene.as_payload()
    map_value = cast(dict[str, object], admitted["map"])
    bounds = cast(dict[str, object], map_value["bounds"])
    normalized: dict[str, object] = {"kind": kind, "map_id": scene.map_id}
    if kind == "ray":
        origin = _coordinate(item["origin"], 3, code=code, path="$.origin")
        destination = _coordinate(item["destination"], 3, code=code, path="$.destination")
        if not _inside(origin, bounds) or not _inside(destination, bounds):
            _reject("Ray endpoint is outside map bounds", code="spatial_out_of_bounds", path="$")
        normalized.update(
            {
                "origin": origin,
                "destination": destination,
                "include": _query_include(
                    item["include"], frozenset({"geometry", "entities"}), path="$.include"
                ),
                "max_intersections": _integer(
                    item["max_intersections"],
                    minimum=1,
                    maximum=1024,
                    code=code,
                    path="$.max_intersections",
                ),
            }
        )
    elif kind == "exposure":
        observer = _object(item["observer"], frozenset({"point"}), code=code, path="$.observer")
        point = _coordinate(observer["point"], 3, code=code, path="$.observer.point")
        if not _inside(point, bounds):
            _reject(
                "Observer is outside map bounds",
                code="spatial_out_of_bounds",
                path="$.observer.point",
            )
        target = _object(item["target"], frozenset({"entity_id"}), code=code, path="$.target")
        entity_id = _identity(target["entity_id"], code=code, path="$.target.entity_id")
        entity_ids = {
            cast(str, entry["id"]) for entry in cast(list[dict[str, object]], admitted["entities"])
        }
        if entity_id not in entity_ids:
            _reject("Exposure target entity is unknown", code=code, path="$.target.entity_id")
        samples = _object(
            item["samples"], frozenset({"horizontal", "vertical"}), code=code, path="$.samples"
        )
        normalized.update(
            {
                "observer": {"point": point},
                "target": {"entity_id": entity_id},
                "samples": {
                    "horizontal": _integer(
                        samples["horizontal"],
                        minimum=1,
                        maximum=9,
                        code=code,
                        path="$.samples.horizontal",
                    ),
                    "vertical": _integer(
                        samples["vertical"],
                        minimum=1,
                        maximum=9,
                        code=code,
                        path="$.samples.vertical",
                    ),
                },
                "include": _query_include(
                    item["include"], frozenset({"geometry", "entities"}), path="$.include"
                ),
            }
        )
        if "metadata_filter" in item:
            normalized["metadata_filter"] = _metadata_selector(
                item["metadata_filter"], code=code, path="$.metadata_filter"
            )
    elif kind == "body_proximity":
        entities = cast(list[dict[str, object]], admitted["entities"])
        active_ids = {entry["id"] for entry in entities if entry["active"]}
        for name in ("moving_entity_id", "fixed_entity_id"):
            entity_id = _identity(item[name], code=code, path="$." + name)
            if entity_id not in active_ids:
                _reject("Proximity entity is unavailable", code=code, path="$." + name)
            normalized[name] = entity_id
        destination = _coordinate(item["destination"], 3, code=code, path="$.destination")
        if not _inside(destination, bounds):
            _reject(
                "Destination is outside map bounds",
                code="spatial_out_of_bounds",
                path="$.destination",
            )
        normalized.update(
            {
                "destination": destination,
                "radius": _number(item["radius"], minimum=0, code=code, path="$.radius"),
            }
        )
    elif kind == "sweep":
        moving_id = _identity(item["moving_entity_id"], code=code, path="$.moving_entity_id")
        entities = cast(list[dict[str, object]], admitted["entities"])
        active_ids = {entry["id"] for entry in entities if entry["active"]}
        if moving_id not in active_ids:
            _reject("Moving entity is unavailable", code=code, path="$.moving_entity_id")
        exclusions = [
            _identity(value, code=code, path="$.exclude_entity_ids")
            for value in _array(
                item.get("exclude_entity_ids", []),
                minimum=0,
                maximum=64,
                code=code,
                path="$.exclude_entity_ids",
            )
        ]
        if len(set(exclusions)) != len(exclusions) or any(
            value not in active_ids for value in exclusions
        ):
            _reject(
                "Sweep exclusions must name unique active entities",
                code=code,
                path="$.exclude_entity_ids",
            )
        normalized.update(
            {
                "moving_entity_id": moving_id,
                "destination": _coordinate(item["destination"], 3, code=code, path="$.destination"),
                "include_entities": _boolean(
                    item["include_entities"],
                    code=code,
                    path="$.include_entities",
                ),
                "exclude_entity_ids": exclusions,
                "metadata_filter": _metadata_selector(
                    item.get("metadata_filter", {"key": "", "value": "", "missing_matches": False}),
                    code=code,
                    path="$.metadata_filter",
                ),
            }
        )
    elif kind == "placement":
        moving_id = _identity(item["moving_entity_id"], code=code, path="$.moving_entity_id")
        entities = cast(list[dict[str, object]], admitted["entities"])
        active_ids = {entry["id"] for entry in entities if entry["active"]}
        if moving_id not in active_ids:
            _reject("Moving entity is unavailable", code=code, path="$.moving_entity_id")
        exclusions = [
            _identity(value, code=code, path="$.exclude_entity_ids")
            for value in _array(
                item["exclude_entity_ids"],
                minimum=0,
                maximum=64,
                code=code,
                path="$.exclude_entity_ids",
            )
        ]
        if len(set(exclusions)) != len(exclusions) or any(
            value not in active_ids for value in exclusions
        ):
            _reject(
                "Placement exclusions must name unique active entities",
                code=code,
                path="$.exclude_entity_ids",
            )
        selector = _object(
            item["metadata_filter"],
            frozenset({"key", "value", "missing_matches"}),
            code=code,
            path="$.metadata_filter",
        )
        if (
            not isinstance(selector["key"], str)
            or len(selector["key"]) > 128
            or not isinstance(selector["value"], str)
            or len(selector["value"]) > 128
        ):
            _reject(
                "Placement metadata filter requires bounded strings",
                code=code,
                path="$.metadata_filter",
            )
        normalized.update(
            {
                "moving_entity_id": moving_id,
                "origin": _coordinate(item["origin"], 3, code=code, path="$.origin"),
                "exclude_entity_ids": exclusions,
                "metadata_filter": {
                    "key": selector["key"],
                    "value": selector["value"],
                    "missing_matches": _boolean(
                        selector["missing_matches"],
                        code=code,
                        path="$.metadata_filter.missing_matches",
                    ),
                },
                "max_candidates": _integer(
                    item["max_candidates"],
                    minimum=1,
                    maximum=100000,
                    code=code,
                    path="$.max_candidates",
                ),
            }
        )
        if "containing_zone_id" in item:
            zone_id = _identity(item["containing_zone_id"], code=code, path="$.containing_zone_id")
            zone = next(
                (
                    entry
                    for entry in cast(list[dict[str, object]], admitted["zones"])
                    if entry["id"] == zone_id
                ),
                None,
            )
            if zone is None:
                _reject("Containing zone is unavailable", code=code, path="$.containing_zone_id")
            metadata = cast(dict[str, object], zone["metadata"])
            actual = metadata.get(cast(str, selector["key"]))
            if selector["key"] and not (
                actual == selector["value"]
                or (actual is None and selector["missing_matches"] is True)
            ):
                _reject(
                    "Containing zone does not match the placement metadata filter",
                    code=code,
                    path="$.containing_zone_id",
                )
            normalized["containing_zone_id"] = zone_id
        if "reference_face" in item:
            path = "$.reference_face"
            reference = _object(
                item["reference_face"],
                frozenset({"target", "face"}),
                code=code,
                path=path,
            )
            target = _object(
                reference["target"],
                frozenset({"kind", "id"}),
                code=code,
                path=path + ".target",
            )
            target_kind = _enum(
                target["kind"],
                frozenset({"geometry", "entity"}),
                code=code,
                path=path + ".target.kind",
            )
            target_id = _identity(target["id"], code=code, path=path + ".target.id")
            collection = "geometry" if target_kind == "geometry" else "entities"
            source = next(
                (
                    entry
                    for entry in cast(list[dict[str, object]], admitted[collection])
                    if entry["id"] == target_id
                ),
                None,
            )
            if source is None:
                _reject("Placement reference is unavailable", code=code, path=path + ".target.id")
            actual = cast(dict[str, object], source["metadata"]).get(cast(str, selector["key"]))
            if selector["key"] and not (
                actual == selector["value"]
                or (actual is None and selector["missing_matches"] is True)
            ):
                _reject(
                    "Placement reference does not match the placement metadata filter",
                    code=code,
                    path=path + ".target",
                )
            normalized["reference_face"] = {
                "target": {"kind": target_kind, "id": target_id},
                "face": _enum(
                    reference["face"],
                    frozenset({"east", "west", "north", "south", "up", "down"}),
                    code=code,
                    path=path + ".face",
                ),
            }
    elif kind == "path":
        start = _cell(item["start"], code=code, path="$.start")
        goal = _cell(item["goal"], code=code, path="$.goal")
        axis_cells = _grid_cell_counts(map_value)
        if any(start[index] >= axis_cells[index] for index in range(2)):
            _reject(
                "Path start is outside map bounds", code="spatial_out_of_bounds", path="$.start"
            )
        if any(goal[index] >= axis_cells[index] for index in range(2)):
            _reject("Path goal is outside map bounds", code="spatial_out_of_bounds", path="$.goal")
        include_entities = _boolean(item["include_entities"], code=code, path="$.include_entities")
        moving_entity_id: str | None = None
        if "moving_entity_id" in item:
            moving_entity_id = _identity(
                item["moving_entity_id"], code=code, path="$.moving_entity_id"
            )
            entities = cast(list[dict[str, object]], admitted["entities"])
            if not include_entities or not any(
                entry["id"] == moving_entity_id and entry["active"] for entry in entities
            ):
                _reject(
                    "Moving entity is unavailable",
                    code=code,
                    path="$.moving_entity_id",
                )
        normalized.update(
            {
                "start": start,
                "goal": goal,
                "allow_diagonal": _boolean(
                    item["allow_diagonal"], code=code, path="$.allow_diagonal"
                ),
                "corner_policy": _enum(
                    item["corner_policy"],
                    frozenset({"allow_cutting", "forbid_cutting"}),
                    code=code,
                    path="$.corner_policy",
                ),
                "include_entities": include_entities,
                "max_steps": _integer(
                    item["max_steps"],
                    minimum=1,
                    maximum=_MAX_TOTAL_CELLS,
                    code=code,
                    path="$.max_steps",
                ),
            }
        )
        if moving_entity_id is not None:
            normalized["moving_entity_id"] = moving_entity_id
        if "metadata_filter" in item:
            normalized["metadata_filter"] = _metadata_selector(
                item["metadata_filter"], code=code, path="$.metadata_filter"
            )
    elif kind == "crossed_zones":
        points = _array(item["path"], minimum=2, maximum=256, code=code, path="$.path")
        normalized_path = [
            _coordinate(point, 3, code=code, path=f"$.path[{index}]")
            for index, point in enumerate(points)
        ]
        if any(not _inside(point, bounds) for point in normalized_path):
            _reject("Zone path is outside map bounds", code="spatial_out_of_bounds", path="$.path")
        normalized.update(
            {
                "path": normalized_path,
                "max_crossings": _integer(
                    item["max_crossings"],
                    minimum=1,
                    maximum=4096,
                    code=code,
                    path="$.max_crossings",
                ),
            }
        )
    else:
        normalized.update(
            {
                "shape": _area_shape(item["shape"], bounds=bounds, code=code, path="$.shape"),
                "include": _query_include(
                    item["include"], frozenset({"geometry", "entities", "zones"}), path="$.include"
                ),
                "max_results": _integer(
                    item["max_results"], minimum=1, maximum=4096, code=code, path="$.max_results"
                ),
            }
        )
    return AdmittedSpatialQuery(owned_payload(normalized), kind, scene.map_id)


def _result_intersection(
    value: object,
    *,
    scene_ids: Mapping[str, frozenset[str]],
    path: str,
) -> dict[str, object]:
    code = "spatial_result_invalid"
    item = _object(
        value,
        frozenset({"kind", "id", "distance", "t", "point", "normal", "metadata"}),
        code=code,
        path=path,
    )
    kind = _enum(item["kind"], frozenset({"geometry", "entity"}), code=code, path=f"{path}.kind")
    item_id = _identity(item["id"], code=code, path=f"{path}.id")
    if item_id not in scene_ids[kind]:
        _reject("Intersection identity is unknown", code=code, path=f"{path}.id")
    distance = _number(item["distance"], minimum=0, code=code, path=f"{path}.distance")
    parameter = _number(item["t"], minimum=0, code=code, path=f"{path}.t")
    if float(parameter) > 1:
        _reject("Intersection parameter exceeds the ray", code=code, path=f"{path}.t")
    return {
        "kind": kind,
        "id": item_id,
        "distance": distance,
        "t": parameter,
        "point": _coordinate(item["point"], 3, code=code, path=f"{path}.point"),
        "normal": _coordinate(item["normal"], 3, code=code, path=f"{path}.normal"),
        "metadata": _metadata(item["metadata"], code=code, path=f"{path}.metadata"),
    }


def _result_reference(
    value: object,
    *,
    scene_ids: Mapping[str, frozenset[str]],
    allowed: frozenset[str],
    path: str,
) -> dict[str, object]:
    code = "spatial_result_invalid"
    item = _object(value, frozenset({"kind", "id", "metadata"}), code=code, path=path)
    kind = _enum(item["kind"], allowed, code=code, path=f"{path}.kind")
    item_id = _identity(item["id"], code=code, path=f"{path}.id")
    if item_id not in scene_ids[kind]:
        _reject("Spatial result identity is unknown", code=code, path=f"{path}.id")
    return {
        "kind": kind,
        "id": item_id,
        "metadata": _metadata(item["metadata"], code=code, path=f"{path}.metadata"),
    }


def admit_spatial_result(scene: AdmittedSpatialScene, value: object) -> AdmittedSpatialResult:
    """Validate a result's closed shape and immutable snapshot provenance."""

    code = "spatial_result_invalid"
    payload = _capture_payload(value, code=code)
    kind = payload.get("kind")
    if not isinstance(kind, str) or kind not in _RESULT_KEYS:
        _reject("Spatial result kind is invalid", code=code, path="$.kind")
    item = _object(payload, _RESULT_KEYS[kind], code=code, path="$")
    if item["map_id"] != scene.map_id:
        _reject("Spatial result belongs to another map", code="spatial_cross_map", path="$.map_id")
    if item["snapshot_id"] != scene.snapshot_id:
        _reject("Spatial result snapshot is stale", code=code, path="$.snapshot_id")
    if item["geometry_revision"] != scene.geometry_revision:
        _reject("Spatial result revision is stale", code=code, path="$.geometry_revision")
    if item["algorithm_version"] != SPATIAL_ALGORITHM_VERSION:
        _reject(
            "Spatial result algorithm version is invalid", code=code, path="$.algorithm_version"
        )
    scene_payload = scene.as_payload()
    map_value = cast(dict[str, object], scene_payload["map"])
    bounds = cast(dict[str, object], map_value["bounds"])
    axis_cells = _grid_cell_counts(map_value)
    scene_ids = {
        "geometry": frozenset(
            cast(str, entry["id"])
            for entry in cast(list[dict[str, object]], scene_payload["geometry"])
        ),
        "entity": frozenset(
            cast(str, entry["id"])
            for entry in cast(list[dict[str, object]], scene_payload["entities"])
        ),
        "zone": frozenset(
            cast(str, entry["id"])
            for entry in cast(list[dict[str, object]], scene_payload["zones"])
        ),
    }
    normalized: dict[str, object] = {
        "kind": kind,
        "map_id": scene.map_id,
        "snapshot_id": scene.snapshot_id,
        "geometry_revision": scene.geometry_revision,
        "algorithm_version": SPATIAL_ALGORITHM_VERSION,
    }
    if kind == "ray":
        cells_input = _array(
            item["cells"], minimum=1, maximum=sum(axis_cells) + 2, code=code, path="$.cells"
        )
        cells = [
            _cell(value, code=code, path=f"$.cells[{index}]")
            for index, value in enumerate(cells_input)
        ]
        if any(cell[index] >= axis_cells[index] for cell in cells for index in range(2)):
            _reject("Ray traversal cell is outside map bounds", code=code, path="$.cells")
        intersections_input = _array(
            item["intersections"], minimum=0, maximum=1024, code=code, path="$.intersections"
        )
        intersections = [
            _result_intersection(value, scene_ids=scene_ids, path=f"$.intersections[{index}]")
            for index, value in enumerate(intersections_input)
        ]
        intersections.sort(
            key=lambda value: (
                float(cast(int | float, value["t"])),
                cast(str, value["kind"]),
                cast(str, value["id"]),
            )
        )
        normalized.update(
            {
                "cells": cells,
                "intersections": intersections,
                "truncated": _boolean(item["truncated"], code=code, path="$.truncated"),
            }
        )
    elif kind == "exposure":
        samples_input = _array(item["samples"], minimum=1, maximum=81, code=code, path="$.samples")
        samples: list[dict[str, object]] = []
        for index, raw in enumerate(samples_input):
            path = f"$.samples[{index}]"
            sample = _object(
                raw,
                frozenset({"point", "visible", "occluders"}),
                code=code,
                path=path,
            )
            point = _coordinate(sample["point"], 3, code=code, path=f"{path}.point")
            if not _inside(point, bounds):
                _reject("Exposure sample is outside map bounds", code=code, path=f"{path}.point")
            occluders_input = _array(
                sample["occluders"], minimum=0, maximum=1024, code=code, path=f"{path}.occluders"
            )
            samples.append(
                {
                    "point": point,
                    "visible": _boolean(sample["visible"], code=code, path=f"{path}.visible"),
                    "occluders": [
                        _result_intersection(
                            value,
                            scene_ids=scene_ids,
                            path=f"{path}.occluders[{occluder_index}]",
                        )
                        for occluder_index, value in enumerate(occluders_input)
                    ],
                }
            )
        visible_samples = _integer(
            item["visible_samples"],
            minimum=0,
            maximum=len(samples),
            code=code,
            path="$.visible_samples",
        )
        exposure = _number(item["exposure"], minimum=0, code=code, path="$.exposure")
        if float(exposure) > 1:
            _reject("Exposure fraction exceeds one", code=code, path="$.exposure")
        occluders_input = _array(
            item["occluders"], minimum=0, maximum=1024, code=code, path="$.occluders"
        )
        occluders = [
            _result_reference(
                value,
                scene_ids=scene_ids,
                allowed=frozenset({"geometry", "entity"}),
                path=f"$.occluders[{index}]",
            )
            for index, value in enumerate(occluders_input)
        ]
        occluders.sort(key=lambda value: (cast(str, value["kind"]), cast(str, value["id"])))
        normalized.update(
            {
                "samples": samples,
                "visible_samples": visible_samples,
                "exposure": exposure,
                "occluders": occluders,
            }
        )
    elif kind == "body_proximity":
        hit = _boolean(item["hit"], code=code, path="$.hit")
        interval: dict[str, object] = {}
        for name in ("entry", "exit"):
            value = item[name]
            if hit:
                value = _number(value, minimum=0, code=code, path="$." + name)
                if float(value) > 1:
                    _reject("Proximity parameter exceeds one", code=code, path="$." + name)
            elif value is not None:
                _reject("Missed proximity interval must be null", code=code, path="$." + name)
            interval[name] = value
        if hit and cast(float, interval["entry"]) > cast(float, interval["exit"]):
            _reject("Proximity interval is reversed", code=code, path="$")
        normalized.update(
            {
                "hit": hit,
                **interval,
                "minimum_distance": _number(
                    item["minimum_distance"], minimum=0, code=code, path="$.minimum_distance"
                ),
            }
        )
    elif kind == "sweep":
        supported = _boolean(item["supported"], code=code, path="$.supported")
        clear = _boolean(item["clear"], code=code, path="$.clear")
        reason = _enum(
            item["reason"],
            frozenset({"clear", "blocked", "out_of_bounds", "unsupported_geometry"}),
            code=code,
            path="$.reason",
        )
        blockers = []
        for index, value in enumerate(
            _array(
                item["blockers"],
                minimum=0,
                maximum=_MAX_GEOMETRY + _MAX_ENTITIES,
                code=code,
                path="$.blockers",
            )
        ):
            path = f"$.blockers[{index}]"
            blocker = _object(value, frozenset({"kind", "id"}), code=code, path=path)
            blocker_kind = _enum(
                blocker["kind"],
                frozenset({"geometry", "entity"}),
                code=code,
                path=path + ".kind",
            )
            blocker_id = _identity(blocker["id"], code=code, path=path + ".id")
            if blocker_id not in scene_ids[blocker_kind]:
                _reject("Sweep blocker is unknown", code=code, path=path + ".id")
            blockers.append({"kind": blocker_kind, "id": blocker_id})
        if len({(entry["kind"], entry["id"]) for entry in blockers}) != len(blockers):
            _reject("Sweep blockers must be unique", code=code, path="$.blockers")
        if (
            clear != (reason == "clear")
            or supported != (reason != "unsupported_geometry")
            or bool(blockers) != (reason == "blocked")
        ):
            _reject("Sweep result is inconsistent", code=code, path="$")
        blockers.sort(key=lambda entry: (entry["kind"], entry["id"]))
        normalized.update(
            {
                "supported": supported,
                "clear": clear,
                "reason": reason,
                "blockers": blockers,
                "distance": _number(item["distance"], minimum=0, code=code, path="$.distance"),
            }
        )
    elif kind == "placement":
        supported = _boolean(item["supported"], code=code, path="$.supported")
        found = _boolean(item["found"], code=code, path="$.found")
        reason = _enum(
            item["reason"],
            frozenset({"placed", "no_placement", "unsupported_geometry", "candidate_limit"}),
            code=code,
            path="$.reason",
        )
        position = (
            None
            if item["position"] is None
            else _coordinate(item["position"], 3, code=code, path="$.position")
        )
        distance = (
            None
            if item["distance"] is None
            else _number(item["distance"], minimum=0, code=code, path="$.distance")
        )
        if (
            found != (position is not None and distance is not None)
            or (found and (not supported or reason != "placed"))
            or supported != (reason in {"placed", "no_placement"})
        ):
            _reject("Placement result is inconsistent", code=code, path="$")
        if position is not None and not _inside(position, bounds):
            _reject("Placement position is outside map bounds", code=code, path="$.position")
        normalized.update(
            {
                "supported": supported,
                "found": found,
                "position": position,
                "distance": distance,
                "reason": reason,
                "examined": _integer(
                    item["examined"], minimum=0, maximum=100000, code=code, path="$.examined"
                ),
            }
        )
    elif kind == "path":
        cells_input = _array(
            item["cells"], minimum=0, maximum=_MAX_TOTAL_CELLS, code=code, path="$.cells"
        )
        cells = [
            _cell(value, code=code, path=f"$.cells[{index}]")
            for index, value in enumerate(cells_input)
        ]
        if any(cell[index] >= axis_cells[index] for cell in cells for index in range(2)):
            _reject("Path cell is outside map bounds", code=code, path="$.cells")
        found = _boolean(item["found"], code=code, path="$.found")
        raw_cost = item["cost"]
        cost = None if raw_cost is None else _number(raw_cost, minimum=0, code=code, path="$.cost")
        if found != bool(cells) or found != (cost is not None):
            _reject("Path found state is inconsistent", code=code, path="$")
        normalized.update(
            {
                "found": found,
                "cells": cells,
                "cost": cost,
                "expanded": _integer(
                    item["expanded"],
                    minimum=0,
                    maximum=_MAX_TOTAL_CELLS,
                    code=code,
                    path="$.expanded",
                ),
                "truncated": _boolean(item["truncated"], code=code, path="$.truncated"),
            }
        )
    elif kind == "crossed_zones":
        crossings_input = _array(
            item["crossings"], minimum=0, maximum=4096, code=code, path="$.crossings"
        )
        crossings: list[dict[str, object]] = []
        for index, raw in enumerate(crossings_input):
            path = f"$.crossings[{index}]"
            crossing = _object(
                raw,
                frozenset({"zone_id", "segment_index", "t", "point", "distance", "metadata"}),
                code=code,
                path=path,
            )
            zone_id = _identity(crossing["zone_id"], code=code, path=f"{path}.zone_id")
            if zone_id not in scene_ids["zone"]:
                _reject("Zone crossing identity is unknown", code=code, path=f"{path}.zone_id")
            parameter = _number(crossing["t"], minimum=0, code=code, path=f"{path}.t")
            if float(parameter) > 1:
                _reject("Zone crossing parameter exceeds segment", code=code, path=f"{path}.t")
            crossings.append(
                {
                    "zone_id": zone_id,
                    "segment_index": _integer(
                        crossing["segment_index"],
                        minimum=0,
                        maximum=254,
                        code=code,
                        path=f"{path}.segment_index",
                    ),
                    "t": parameter,
                    "point": _coordinate(crossing["point"], 3, code=code, path=f"{path}.point"),
                    "distance": _number(
                        crossing["distance"], minimum=0, code=code, path=f"{path}.distance"
                    ),
                    "metadata": _metadata(crossing["metadata"], code=code, path=f"{path}.metadata"),
                }
            )
        crossings.sort(
            key=lambda value: (
                cast(int, value["segment_index"]),
                float(cast(int | float, value["t"])),
                cast(str, value["zone_id"]),
            )
        )
        normalized.update(
            {
                "crossings": crossings,
                "truncated": _boolean(item["truncated"], code=code, path="$.truncated"),
            }
        )
    else:
        results_input = _array(
            item["results"], minimum=0, maximum=4096, code=code, path="$.results"
        )
        results = [
            _result_reference(
                value,
                scene_ids=scene_ids,
                allowed=frozenset({"geometry", "entity", "zone"}),
                path=f"$.results[{index}]",
            )
            for index, value in enumerate(results_input)
        ]
        results.sort(key=lambda value: (cast(str, value["kind"]), cast(str, value["id"])))
        normalized.update(
            {
                "results": results,
                "truncated": _boolean(item["truncated"], code=code, path="$.truncated"),
            }
        )
    return AdmittedSpatialResult(owned_payload(normalized), kind, scene.snapshot_id)
