"""Deterministic in-process implementation of the public spatial contract."""

from __future__ import annotations

import heapq
import math
from collections import OrderedDict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal, cast

from sylvia_gm.domain.package_library import canonical_json_bytes
from sylvia_gm.spatial._clearance import swept_footprint_hits_box
from sylvia_gm.spatial._convex import convex_overlap
from sylvia_gm.spatial._interface import (
    SPATIAL_ALGORITHM_VERSION,
    AdmittedSpatialResult,
    AdmittedSpatialScene,
    SpatialValidationError,
    admit_spatial_query,
    admit_spatial_result,
    admit_spatial_scene,
)

_EPSILON = 1e-12
_CACHE_LIMIT = 256
_SQRT_TWO = math.sqrt(2.0)

Vec2 = tuple[float, float]
Vec3 = tuple[float, float, float]
Cell = tuple[int, int]
PlacementNumber = float | Fraction
PlacementPoint = tuple[PlacementNumber, PlacementNumber, PlacementNumber]
ShapeKind = Literal["box", "cylinder", "sphere", "cone", "prism"]
ObjectKind = Literal["geometry", "entity", "zone"]


@dataclass(frozen=True)
class _Shape:
    kind: ShapeKind
    lower: Vec3
    upper: Vec3
    center: Vec3 | None = None
    radius: float | None = None
    axis: Vec3 | None = None
    length: float | None = None
    basis: tuple[Vec3, Vec3, Vec3] | None = None
    half_extents: Vec3 | None = None


@dataclass(frozen=True)
class _SpatialObject:
    kind: ObjectKind
    item_id: str
    shape: _Shape
    active: bool
    movement: str
    visibility: str
    metadata: dict[str, object]
    occupies_cells: bool = True


@dataclass(frozen=True)
class _RayHit:
    kind: Literal["geometry", "entity"]
    item_id: str
    distance: float
    parameter: float
    point: Vec3
    normal: Vec3
    metadata: dict[str, object]


@dataclass(frozen=True)
class _CompiledScene:
    admitted: AdmittedSpatialScene
    lower: Vec3
    upper: Vec3
    origin: Vec2
    cell_size: float
    cell_counts: Cell
    objects: tuple[_SpatialObject, ...]
    zones: tuple[_SpatialObject, ...]
    terrain: Mapping[Cell, float]


def _vec3(value: object) -> Vec3:
    items = cast(Sequence[int | float], value)
    return (float(items[0]), float(items[1]), float(items[2]))


def _clean(value: float) -> float:
    rounded = round(value, 12)
    return 0.0 if abs(rounded) < _EPSILON else rounded


def _clean_point(point: Vec3) -> list[float]:
    return [_clean(value) for value in point]


def _shape_from_payload(value: Mapping[str, object]) -> _Shape:
    if value["kind"] == "box":
        return _Shape("box", _vec3(value["min"]), _vec3(value["max"]))
    if value["kind"] == "prism":
        origin, direction, up = (
            _vec3(value["origin"]),
            _vec3(value["direction"]),
            _vec3(value["up"]),
        )
        magnitude = math.hypot(*direction)
        axis = cast(Vec3, tuple(component / magnitude for component in direction))
        side = (
            up[1] * axis[2] - up[2] * axis[1],
            up[2] * axis[0] - up[0] * axis[2],
            up[0] * axis[1] - up[1] * axis[0],
        )
        side_norm = math.hypot(*side)
        width_axis = cast(Vec3, tuple(component / side_norm for component in side))
        height_axis = (
            axis[1] * width_axis[2] - axis[2] * width_axis[1],
            axis[2] * width_axis[0] - axis[0] * width_axis[2],
            axis[0] * width_axis[1] - axis[1] * width_axis[0],
        )
        height_norm = math.hypot(*height_axis)
        height_axis = cast(Vec3, tuple(component / height_norm for component in height_axis))
        basis = (axis, width_axis, height_axis)
        half_extents = cast(
            Vec3,
            tuple(
                float(cast(int | float, value[field])) * 0.5
                for field in ("length", "width", "height")
            ),
        )
        center = cast(Vec3, tuple(origin[i] + axis[i] * half_extents[0] for i in range(3)))
        extent = tuple(sum(abs(basis[j][i]) * half_extents[j] for j in range(3)) for i in range(3))
        lower = cast(Vec3, tuple(center[i] - extent[i] for i in range(3)))
        upper = cast(Vec3, tuple(center[i] + extent[i] for i in range(3)))
        return _Shape("prism", lower, upper, center=center, basis=basis, half_extents=half_extents)
    if value["kind"] == "cone":
        apex = _vec3(value["apex"])
        direction = _vec3(value["direction"])
        magnitude = math.hypot(*direction)
        axis = cast(Vec3, tuple(component / magnitude for component in direction))
        length = float(cast(int | float, value["length"]))
        radius = float(cast(int | float, value["radius"]))
        base = tuple(apex[i] + axis[i] * length for i in range(3))
        extents = tuple(radius * math.sqrt(max(0.0, 1 - axis[i] ** 2)) for i in range(3))
        lower = cast(Vec3, tuple(min(apex[i], base[i] - extents[i]) for i in range(3)))
        upper = cast(Vec3, tuple(max(apex[i], base[i] + extents[i]) for i in range(3)))
        return _Shape("cone", lower, upper, apex, radius, axis, length)
    center = _vec3(value["center"])
    radius = float(cast(int | float, value["radius"]))
    if value["kind"] == "sphere":
        return _Shape(
            "sphere",
            cast(Vec3, tuple(component - radius for component in center)),
            cast(Vec3, tuple(component + radius for component in center)),
            center,
            radius,
        )
    height = float(cast(int | float, value["height"]))
    return _Shape(
        "cylinder",
        (center[0] - radius, center[1] - radius, center[2]),
        (center[0] + radius, center[1] + radius, center[2] + height),
        center,
        radius,
    )


def _entity_shape(value: Mapping[str, object]) -> _Shape:
    position = _vec3(value["position"])
    height = float(cast(int | float, value["height"]))
    footprint = cast(Mapping[str, object], value["footprint"])
    if footprint["kind"] == "box":
        extents = cast(Sequence[int | float], footprint["half_extents"])
        half_x, half_y = float(extents[0]), float(extents[1])
        return _Shape(
            "box",
            (position[0] - half_x, position[1] - half_y, position[2]),
            (position[0] + half_x, position[1] + half_y, position[2] + height),
        )
    radius = float(cast(int | float, footprint["radius"]))
    return _Shape(
        "cylinder",
        (position[0] - radius, position[1] - radius, position[2]),
        (position[0] + radius, position[1] + radius, position[2] + height),
        position,
        radius,
    )


def _compile(scene: AdmittedSpatialScene) -> _CompiledScene:
    payload = scene.as_payload()
    map_value = cast(Mapping[str, object], payload["map"])
    bounds = cast(Mapping[str, object], map_value["bounds"])
    lower, upper = _vec3(bounds["min"]), _vec3(bounds["max"])
    grid = cast(Mapping[str, object], map_value["grid"])
    grid_origin = cast(Sequence[int | float], grid["origin"])
    origin = (float(grid_origin[0]), float(grid_origin[1]))
    cell_size = float(cast(int | float, grid["cell_size"]))
    counts = (
        math.ceil((upper[0] - lower[0]) / cell_size),
        math.ceil((upper[1] - lower[1]) / cell_size),
    )
    objects: list[_SpatialObject] = []
    for value in cast(Sequence[Mapping[str, object]], payload["geometry"]):
        objects.append(
            _SpatialObject(
                "geometry",
                cast(str, value["id"]),
                _shape_from_payload(cast(Mapping[str, object], value["volume"])),
                cast(bool, value["active"]),
                cast(str, value["movement"]),
                cast(str, value["visibility"]),
                cast(dict[str, object], value["metadata"]),
                cast(bool, value["occupies_cells"]),
            )
        )
    for value in cast(Sequence[Mapping[str, object]], payload["entities"]):
        objects.append(
            _SpatialObject(
                "entity",
                cast(str, value["id"]),
                _entity_shape(value),
                cast(bool, value["active"]),
                cast(str, value["movement"]),
                cast(str, value["visibility"]),
                cast(dict[str, object], value["metadata"]),
            )
        )
    zones = tuple(
        _SpatialObject(
            "zone",
            cast(str, value["id"]),
            _shape_from_payload(cast(Mapping[str, object], value["shape"])),
            True,
            "passable",
            "clear",
            cast(dict[str, object], value["metadata"]),
        )
        for value in cast(Sequence[Mapping[str, object]], payload["zones"])
    )
    terrain = {
        (
            cast(Sequence[int], value["cell"])[0],
            cast(Sequence[int], value["cell"])[1],
        ): float(cast(int | float, value["cost"]))
        for value in cast(Sequence[Mapping[str, object]], payload["terrain"])
    }
    return _CompiledScene(
        scene,
        lower,
        upper,
        origin,
        cell_size,
        counts,
        tuple(objects),
        zones,
        terrain,
    )


def _cell_for(scene: _CompiledScene, point: Vec3) -> Cell:
    return (
        min(
            scene.cell_counts[0] - 1,
            math.floor((point[0] - scene.origin[0]) / scene.cell_size),
        ),
        min(
            scene.cell_counts[1] - 1,
            math.floor((point[1] - scene.origin[1]) / scene.cell_size),
        ),
    )


def _in_cells(scene: _CompiledScene, cell: Cell) -> bool:
    return 0 <= cell[0] < scene.cell_counts[0] and 0 <= cell[1] < scene.cell_counts[1]


def _supercover(scene: _CompiledScene, origin: Vec3, destination: Vec3) -> list[list[int]]:
    """Retain cells touched by the finite segment, including tied endpoint cells."""
    current = _cell_for(scene, origin)
    retained: list[Cell] = [current]
    seen = {current}
    delta_x = destination[0] - origin[0]
    delta_y = destination[1] - origin[1]
    step_x = 1 if delta_x > 0 else -1 if delta_x < 0 else 0
    step_y = 1 if delta_y > 0 else -1 if delta_y < 0 else 0

    if step_x:
        boundary_x = scene.origin[0] + (current[0] + (1 if step_x > 0 else 0)) * scene.cell_size
        maximum_x = (boundary_x - origin[0]) / delta_x
        increment_x = scene.cell_size / abs(delta_x)
    else:
        maximum_x = math.inf
        increment_x = math.inf
    if step_y:
        boundary_y = scene.origin[1] + (current[1] + (1 if step_y > 0 else 0)) * scene.cell_size
        maximum_y = (boundary_y - origin[1]) / delta_y
        increment_y = scene.cell_size / abs(delta_y)
    else:
        maximum_y = math.inf
        increment_y = math.inf

    def retain(cell: Cell) -> None:
        if _in_cells(scene, cell) and cell not in seen:
            seen.add(cell)
            retained.append(cell)

    while min(maximum_x, maximum_y) <= 1 + _EPSILON:
        if maximum_x + _EPSILON < maximum_y:
            current = (current[0] + step_x, current[1])
            maximum_x += increment_x
            retain(current)
        elif maximum_y + _EPSILON < maximum_x:
            current = (current[0], current[1] + step_y)
            maximum_y += increment_y
            retain(current)
        else:
            x_side = (current[0] + step_x, current[1])
            y_side = (current[0], current[1] + step_y)
            diagonal = (current[0] + step_x, current[1] + step_y)
            retain(x_side)
            retain(y_side)
            retain(diagonal)
            current = diagonal
            maximum_x += increment_x
            maximum_y += increment_y
    return [[cell[0], cell[1]] for cell in retained]


def _point_at(origin: Vec3, delta: Vec3, parameter: float) -> Vec3:
    return (
        origin[0] + delta[0] * parameter,
        origin[1] + delta[1] * parameter,
        origin[2] + delta[2] * parameter,
    )


def _box_interval(shape: _Shape, origin: Vec3, delta: Vec3) -> tuple[float, float, Vec3] | None:
    entry = -math.inf
    exit_value = math.inf
    entry_normal: Vec3 = (0.0, 0.0, 0.0)
    inside = True
    for axis in range(3):
        coordinate = origin[axis]
        if coordinate < shape.lower[axis] - _EPSILON or coordinate > shape.upper[axis] + _EPSILON:
            inside = False
        direction = delta[axis]
        if abs(direction) <= _EPSILON:
            if coordinate < shape.lower[axis] or coordinate > shape.upper[axis]:
                return None
            continue
        first = (shape.lower[axis] - coordinate) / direction
        second = (shape.upper[axis] - coordinate) / direction
        first_normal = [0.0, 0.0, 0.0]
        first_normal[axis] = -1.0 if direction > 0 else 1.0
        if first > second:
            first, second = second, first
        if first > entry:
            entry = first
            entry_normal = cast(Vec3, tuple(first_normal))
        exit_value = min(exit_value, second)
        if entry - _EPSILON > exit_value:
            return None
    if exit_value < -_EPSILON or entry > 1 + _EPSILON:
        return None
    if inside:
        return (0.0, min(1.0, exit_value), (0.0, 0.0, 0.0))
    return (max(0.0, entry), min(1.0, exit_value), entry_normal)


def _interval_intersection(
    first: tuple[float, float], second: tuple[float, float]
) -> tuple[float, float] | None:
    lower, upper = max(first[0], second[0]), min(first[1], second[1])
    return None if lower - _EPSILON > upper else (lower, upper)


def _cylinder_interval(
    shape: _Shape, origin: Vec3, delta: Vec3
) -> tuple[float, float, Vec3] | None:
    center = cast(Vec3, shape.center)
    radius = cast(float, shape.radius)
    offset_x, offset_y = origin[0] - center[0], origin[1] - center[1]
    coefficient = delta[0] * delta[0] + delta[1] * delta[1]
    inside_horizontal = offset_x * offset_x + offset_y * offset_y <= radius * radius + _EPSILON
    if coefficient <= _EPSILON:
        if not inside_horizontal:
            return None
        horizontal = (-math.inf, math.inf)
    else:
        linear = 2 * (offset_x * delta[0] + offset_y * delta[1])
        constant = offset_x * offset_x + offset_y * offset_y - radius * radius
        discriminant = linear * linear - 4 * coefficient * constant
        if discriminant < -_EPSILON:
            return None
        root = math.sqrt(max(0.0, discriminant))
        horizontal = (
            (-linear - root) / (2 * coefficient),
            (-linear + root) / (2 * coefficient),
        )
    if abs(delta[2]) <= _EPSILON:
        if origin[2] < shape.lower[2] or origin[2] > shape.upper[2]:
            return None
        vertical = (-math.inf, math.inf)
    else:
        vertical_values = (
            (shape.lower[2] - origin[2]) / delta[2],
            (shape.upper[2] - origin[2]) / delta[2],
        )
        vertical = (min(vertical_values), max(vertical_values))
    interval = _interval_intersection(horizontal, vertical)
    if interval is None or interval[1] < -_EPSILON or interval[0] > 1 + _EPSILON:
        return None
    inside = inside_horizontal and shape.lower[2] <= origin[2] <= shape.upper[2]
    parameter = 0.0 if inside else max(0.0, interval[0])
    if inside:
        normal = (0.0, 0.0, 0.0)
    else:
        point = _point_at(origin, delta, parameter)
        if abs(point[2] - shape.lower[2]) <= 1e-9:
            normal = (0.0, 0.0, -1.0)
        elif abs(point[2] - shape.upper[2]) <= 1e-9:
            normal = (0.0, 0.0, 1.0)
        else:
            magnitude = math.hypot(point[0] - center[0], point[1] - center[1])
            normal = (
                (point[0] - center[0]) / magnitude,
                (point[1] - center[1]) / magnitude,
                0.0,
            )
    return (parameter, min(1.0, interval[1]), normal)


def _shape_interval(shape: _Shape, origin: Vec3, delta: Vec3) -> tuple[float, float, Vec3] | None:
    return (
        _box_interval(shape, origin, delta)
        if shape.kind == "box"
        else _cylinder_interval(shape, origin, delta)
    )


def _ray_hits(
    scene: _CompiledScene,
    origin: Vec3,
    destination: Vec3,
    include: frozenset[str],
) -> list[_RayHit]:
    delta = (
        destination[0] - origin[0],
        destination[1] - origin[1],
        destination[2] - origin[2],
    )
    length = math.sqrt(sum(value * value for value in delta))
    hits: list[_RayHit] = []
    for item in scene.objects:
        plural = "geometry" if item.kind == "geometry" else "entities"
        if not item.active or plural not in include:
            continue
        interval = _shape_interval(item.shape, origin, delta)
        if interval is None:
            continue
        parameter, _exit, normal = interval
        hits.append(
            _RayHit(
                cast(Literal["geometry", "entity"], item.kind),
                item.item_id,
                parameter * length,
                parameter,
                _point_at(origin, delta, parameter),
                normal,
                item.metadata,
            )
        )
    hits.sort(
        key=lambda hit: (
            _clean(hit.parameter),
            0 if hit.kind == "geometry" else 1,
            hit.item_id,
        )
    )
    return hits


def _hit_payload(hit: _RayHit) -> dict[str, object]:
    return {
        "kind": hit.kind,
        "id": hit.item_id,
        "distance": _clean(hit.distance),
        "t": _clean(hit.parameter),
        "point": _clean_point(hit.point),
        "normal": _clean_point(hit.normal),
        "metadata": hit.metadata,
    }


def _provenance(scene: _CompiledScene, kind: str) -> dict[str, object]:
    return {
        "kind": kind,
        "map_id": scene.admitted.map_id,
        "snapshot_id": scene.admitted.snapshot_id,
        "geometry_revision": scene.admitted.geometry_revision,
        "algorithm_version": SPATIAL_ALGORITHM_VERSION,
    }


def _resolve_ray(scene: _CompiledScene, query: Mapping[str, object]) -> dict[str, object]:
    origin, destination = _vec3(query["origin"]), _vec3(query["destination"])
    maximum = cast(int, query["max_intersections"])
    hits = _ray_hits(
        scene,
        origin,
        destination,
        frozenset(cast(Sequence[str], query["include"])),
    )
    return {
        **_provenance(scene, "ray"),
        "cells": _supercover(scene, origin, destination),
        "intersections": [_hit_payload(hit) for hit in hits[:maximum]],
        "truncated": len(hits) > maximum,
    }


def _target_samples(
    shape: _Shape,
    observer: Vec3,
    horizontal_count: int,
    vertical_count: int,
) -> list[Vec3]:
    """Sample the actual near surface, preserving the established Box sample grid.

    An exterior observer sees a circular body's tangent-bounded near arc. An
    observer inside that footprint has no exterior silhouette; use the near
    semicircle as a deterministic surface sample, choosing west when concentric.
    Neither fallback substitutes the circle's enclosing Box for its body.
    """
    center_x = (shape.lower[0] + shape.upper[0]) / 2
    center_y = (shape.lower[1] + shape.upper[1]) / 2
    delta_x, delta_y = center_x - observer[0], center_y - observer[1]
    horizontal: list[Vec2] = []
    if shape.kind == "cylinder":
        radius = cast(float, shape.radius)
        distance = math.hypot(delta_x, delta_y)
        normal_x, normal_y = (
            (delta_x / distance, delta_y / distance) if distance > 0 else (1.0, 0.0)
        )
        half_width = radius
        if distance > radius:
            half_width *= math.sqrt(max(0.0, 1.0 - (radius / distance) ** 2))
        for index in range(horizontal_count):
            lateral = half_width * (2 * (index + 0.5) / horizontal_count - 1)
            near = math.sqrt(max(0.0, radius * radius - lateral * lateral))
            horizontal.append(
                (
                    center_x - near * normal_x - lateral * normal_y,
                    center_y - near * normal_y + lateral * normal_x,
                )
            )
    else:
        for index in range(horizontal_count):
            fraction = (index + 0.5) / horizontal_count
            if abs(delta_x) >= abs(delta_y):
                x = shape.lower[0] if delta_x >= 0 else shape.upper[0]
                y = shape.lower[1] + (shape.upper[1] - shape.lower[1]) * fraction
            else:
                x = shape.lower[0] + (shape.upper[0] - shape.lower[0]) * fraction
                y = shape.lower[1] if delta_y >= 0 else shape.upper[1]
            horizontal.append((x, y))
    return [
        (
            point[0],
            point[1],
            shape.lower[2]
            + (shape.upper[2] - shape.lower[2]) * ((vertical + 0.5) / vertical_count),
        )
        for vertical in range(vertical_count)
        for point in horizontal
    ]


def _crosses_shape_interior(shape: _Shape, origin: Vec3, destination: Vec3) -> bool:
    """Ignore surface-only visibility contact without changing physical ray hits."""
    delta = cast(Vec3, tuple(destination[axis] - origin[axis] for axis in range(3)))
    interval = _shape_interval(shape, origin, delta)
    if interval is None or interval[1] - interval[0] <= _EPSILON:
        return False
    midpoint = _point_at(origin, delta, (interval[0] + interval[1]) / 2)
    if shape.kind == "box":
        return all(
            shape.lower[axis] + _EPSILON < midpoint[axis] < shape.upper[axis] - _EPSILON
            for axis in range(3)
        )
    center = cast(Vec3, shape.center)
    radius = cast(float, shape.radius)
    return (
        shape.lower[2] + _EPSILON < midpoint[2] < shape.upper[2] - _EPSILON
        and math.hypot(midpoint[0] - center[0], midpoint[1] - center[1]) < radius - _EPSILON
    )


def _resolve_exposure(scene: _CompiledScene, query: Mapping[str, object]) -> dict[str, object]:
    observer = _vec3(cast(Mapping[str, object], query["observer"])["point"])
    target_id = cast(str, cast(Mapping[str, object], query["target"])["entity_id"])
    target = next(
        item for item in scene.objects if item.kind == "entity" and item.item_id == target_id
    )
    sample_counts = cast(Mapping[str, int], query["samples"])
    samples = _target_samples(
        target.shape,
        observer,
        sample_counts["horizontal"],
        sample_counts["vertical"],
    )
    include = frozenset(cast(Sequence[str], query["include"]))
    object_lookup = {(item.kind, item.item_id): item for item in scene.objects}
    sample_results: list[dict[str, object]] = []
    references: dict[tuple[str, str], dict[str, object]] = {}
    visible_count = 0
    for sample in samples:
        occluding_hits = [
            hit
            for hit in _ray_hits(scene, observer, sample, include)
            if hit.item_id != target_id
            and hit.parameter < 1 - _EPSILON
            and object_lookup[(hit.kind, hit.item_id)].visibility == "opaque"
            and _matches_metadata(
                object_lookup[(hit.kind, hit.item_id)],
                cast(Mapping[str, object] | None, query.get("metadata_filter")),
            )
            and _crosses_shape_interior(
                object_lookup[(hit.kind, hit.item_id)].shape, observer, sample
            )
        ][:1]
        visible = not occluding_hits
        visible_count += int(visible)
        for hit in occluding_hits:
            references[(hit.kind, hit.item_id)] = {
                "kind": hit.kind,
                "id": hit.item_id,
                "metadata": hit.metadata,
            }
        sample_results.append(
            {
                "point": _clean_point(sample),
                "visible": visible,
                "occluders": [_hit_payload(hit) for hit in occluding_hits],
            }
        )
    return {
        **_provenance(scene, "exposure"),
        "samples": sample_results,
        "visible_samples": visible_count,
        "exposure": _clean(visible_count / len(samples)),
        "occluders": [references[key] for key in sorted(references)],
    }


def _cell_span(scene: _CompiledScene, shape: _Shape) -> tuple[range, range]:
    minimum_x = max(0, math.floor((shape.lower[0] - scene.origin[0]) / scene.cell_size))
    minimum_y = max(0, math.floor((shape.lower[1] - scene.origin[1]) / scene.cell_size))
    maximum_x = min(
        scene.cell_counts[0] - 1,
        math.ceil((shape.upper[0] - scene.origin[0]) / scene.cell_size) - 1,
    )
    maximum_y = min(
        scene.cell_counts[1] - 1,
        math.ceil((shape.upper[1] - scene.origin[1]) / scene.cell_size) - 1,
    )
    return range(minimum_x, maximum_x + 1), range(minimum_y, maximum_y + 1)


def _matches_metadata(item: _SpatialObject, selector: Mapping[str, object] | None) -> bool:
    """Apply an optional admitted metadata selector without changing unfiltered queries."""
    if selector is None or not selector["key"]:
        return True
    actual = item.metadata.get(cast(str, selector["key"]))
    return actual == selector["value"] or (actual is None and selector["missing_matches"] is True)


def _blocked_cells(
    scene: _CompiledScene,
    include_entities: bool,
    moving_entity_id: str | None = None,
    metadata_filter: Mapping[str, object] | None = None,
) -> frozenset[Cell]:
    blocked: set[Cell] = set()
    for item in scene.objects:
        if (
            not item.active
            or item.movement != "blocking"
            or not item.occupies_cells
            or (item.kind == "entity" and not include_entities)
            or (item.kind == "entity" and item.item_id == moving_entity_id)
            or not _matches_metadata(item, metadata_filter)
        ):
            continue
        x_cells, y_cells = _cell_span(scene, item.shape)
        blocked.update((x, y) for y in y_cells for x in x_cells)
    return frozenset(blocked)


def _path_clearance(
    scene: _CompiledScene,
    blocked: frozenset[Cell],
    moving: _SpatialObject | None,
    start: Cell,
    metadata_filter: Mapping[str, object] | None = None,
) -> Callable[[Cell, Cell], bool]:
    """Index physical barriers once and check the mover's whole footprint along each step."""

    if moving is None:
        half: Vec2 = (0.0, 0.0)
        radius = None
        start_point = None
    else:
        half = cast(
            Vec2,
            tuple((moving.shape.upper[axis] - moving.shape.lower[axis]) / 2 for axis in range(2)),
        )
        radius = moving.shape.radius if moving.shape.kind == "cylinder" else None
        start_point = cast(
            Vec2,
            tuple((moving.shape.upper[axis] + moving.shape.lower[axis]) / 2 for axis in range(2)),
        )
    edge_cells: dict[Cell, list[_Shape]] = {}
    for item in scene.objects:
        if (
            item.kind != "geometry"
            or item.occupies_cells
            or not item.active
            or item.movement != "blocking"
            or not _matches_metadata(item, metadata_filter)
        ):
            continue
        if moving is not None and (
            item.shape.upper[2] <= moving.shape.lower[2] + _EPSILON
            or item.shape.lower[2] >= moving.shape.upper[2] - _EPSILON
        ):
            continue
        x_cells, y_cells = _cell_span(scene, item.shape)
        for y in y_cells:
            for x in x_cells:
                edge_cells.setdefault((x, y), []).append(item.shape)

    def point(cell: Cell) -> Vec2:
        if cell == start and start_point is not None:
            return start_point
        return (
            scene.origin[0] + (cell[0] + 0.5) * scene.cell_size,
            scene.origin[1] + (cell[1] + 0.5) * scene.cell_size,
        )

    def clear(first: Cell, second: Cell) -> bool:
        origin, destination = point(first), point(second)
        if any(
            value[axis] - half[axis] < scene.lower[axis] - _EPSILON
            or value[axis] + half[axis] > scene.upper[axis] + _EPSILON
            for value in (origin, destination)
            for axis in range(2)
        ):
            return False
        swept = _Shape(
            "box",
            (
                min(origin[0], destination[0]) - half[0],
                min(origin[1], destination[1]) - half[1],
                scene.lower[2],
            ),
            (
                max(origin[0], destination[0]) + half[0],
                max(origin[1], destination[1]) + half[1],
                scene.upper[2],
            ),
        )
        x_cells, y_cells = _cell_span(scene, swept)
        edges: set[_Shape] = set()
        for y in y_cells:
            for x in x_cells:
                if (x, y) in blocked:
                    lower = (
                        scene.origin[0] + x * scene.cell_size,
                        scene.origin[1] + y * scene.cell_size,
                    )
                    upper = (lower[0] + scene.cell_size, lower[1] + scene.cell_size)
                    if swept_footprint_hits_box(
                        origin,
                        destination,
                        lower,
                        upper,
                        half_extents=half,
                        radius=radius,
                    ):
                        return False
                edges.update(edge_cells.get((x, y), ()))
        return not any(
            swept_footprint_hits_box(
                origin,
                destination,
                (shape.lower[0], shape.lower[1]),
                (shape.upper[0], shape.upper[1]),
                half_extents=half,
                radius=radius,
            )
            for shape in edges
        )

    return clear


def _heuristic(first: Cell, second: Cell, diagonal: bool, minimum_cost: float) -> float:
    delta_x, delta_y = abs(first[0] - second[0]), abs(first[1] - second[1])
    if not diagonal:
        return (delta_x + delta_y) * minimum_cost
    shared = min(delta_x, delta_y)
    return (shared * _SQRT_TWO + max(delta_x, delta_y) - shared) * minimum_cost


def _resolve_path(scene: _CompiledScene, query: Mapping[str, object]) -> dict[str, object]:
    start_values = cast(Sequence[int], query["start"])
    goal_values = cast(Sequence[int], query["goal"])
    start, goal = (start_values[0], start_values[1]), (goal_values[0], goal_values[1])
    allow_diagonal = cast(bool, query["allow_diagonal"])
    forbid_cutting = query["corner_policy"] == "forbid_cutting"
    moving_entity_id = cast(str | None, query.get("moving_entity_id"))
    moving_entity = None
    if moving_entity_id is not None:
        moving_entity = next(
            (
                item
                for item in scene.objects
                if item.kind == "entity" and item.item_id == moving_entity_id and item.active
            ),
            None,
        )
        if moving_entity is None:
            raise SpatialValidationError(
                "Moving entity is unavailable",
                code="spatial_query_invalid",
                path="$.moving_entity_id",
            )
        x_cells, y_cells = _cell_span(scene, moving_entity.shape)
        if start not in {(x, y) for y in y_cells for x in x_cells}:
            raise SpatialValidationError(
                "Path start is outside the moving entity footprint",
                code="spatial_query_invalid",
                path="$.start",
            )
    blocked = _blocked_cells(
        scene,
        cast(bool, query["include_entities"]),
        moving_entity_id,
        cast(Mapping[str, object] | None, query.get("metadata_filter")),
    )
    clear = _path_clearance(
        scene,
        blocked,
        moving_entity,
        start,
        cast(Mapping[str, object] | None, query.get("metadata_filter")),
    )
    if start in blocked or goal in blocked or not clear(start, start) or not clear(goal, goal):
        return {
            **_provenance(scene, "path"),
            "found": False,
            "cells": [],
            "cost": None,
            "expanded": 0,
            "truncated": False,
        }
    directions: tuple[tuple[int, int, float], ...] = (
        (-1, 0, 1.0),
        (0, -1, 1.0),
        (1, 0, 1.0),
        (0, 1, 1.0),
    )
    if allow_diagonal:
        directions += (
            (-1, -1, _SQRT_TWO),
            (1, -1, _SQRT_TWO),
            (-1, 1, _SQRT_TWO),
            (1, 1, _SQRT_TWO),
        )
    minimum_cost = min((1.0, *scene.terrain.values()))
    costs: dict[Cell, float] = {start: 0.0}
    parents: dict[Cell, Cell] = {}
    frontier: list[tuple[float, float, int, int]] = [
        (_heuristic(start, goal, allow_diagonal, minimum_cost), 0.0, start[1], start[0])
    ]
    expanded = 0
    maximum = cast(int, query["max_steps"])
    found = False
    while frontier and expanded < maximum:
        _priority, cost, y, x = heapq.heappop(frontier)
        current = (x, y)
        if cost > costs.get(current, math.inf) + _EPSILON:
            continue
        expanded += 1
        if current == goal:
            found = True
            break
        for delta_x, delta_y, step_cost in directions:
            neighbor = (current[0] + delta_x, current[1] + delta_y)
            if not _in_cells(scene, neighbor) or neighbor in blocked:
                continue
            if not clear(current, neighbor):
                continue
            if (
                delta_x
                and delta_y
                and forbid_cutting
                and (
                    (current[0] + delta_x, current[1]) in blocked
                    or (current[0], current[1] + delta_y) in blocked
                )
            ):
                continue
            candidate = cost + step_cost * scene.terrain.get(neighbor, 1.0)
            if candidate + _EPSILON >= costs.get(neighbor, math.inf):
                continue
            costs[neighbor] = candidate
            parents[neighbor] = current
            priority = candidate + _heuristic(neighbor, goal, allow_diagonal, minimum_cost)
            heapq.heappush(frontier, (priority, candidate, neighbor[1], neighbor[0]))
    truncated = bool(frontier) and not found and expanded >= maximum
    if not found:
        return {
            **_provenance(scene, "path"),
            "found": False,
            "cells": [],
            "cost": None,
            "expanded": expanded,
            "truncated": truncated,
        }
    retained = [goal]
    while retained[-1] != start:
        retained.append(parents[retained[-1]])
    retained.reverse()
    return {
        **_provenance(scene, "path"),
        "found": True,
        "cells": [[cell[0], cell[1]] for cell in retained],
        "cost": _clean(costs[goal]),
        "expanded": expanded,
        "truncated": False,
    }


def _inside_shape(shape: _Shape, point: Vec3) -> bool:
    if not all(
        shape.lower[index] - _EPSILON <= point[index] <= shape.upper[index] + _EPSILON
        for index in range(3)
    ):
        return False
    if shape.kind == "box":
        return True
    center = cast(Vec3, shape.center)
    radius = cast(float, shape.radius)
    return (point[0] - center[0]) ** 2 + (point[1] - center[1]) ** 2 <= radius**2 + _EPSILON


def _resolve_zones(scene: _CompiledScene, query: Mapping[str, object]) -> dict[str, object]:
    path = [_vec3(value) for value in cast(Sequence[object], query["path"])]
    candidates: list[tuple[float, str, dict[str, object]]] = []
    for zone in scene.zones:
        cumulative = 0.0
        for index in range(len(path) - 1):
            start, end = path[index], path[index + 1]
            delta = (end[0] - start[0], end[1] - start[1], end[2] - start[2])
            length = math.sqrt(sum(value * value for value in delta))
            interval = _shape_interval(zone.shape, start, delta)
            if interval is not None:
                parameter = 0.0 if index == 0 and _inside_shape(zone.shape, start) else interval[0]
                if parameter >= -_EPSILON:
                    distance = cumulative + max(0.0, parameter) * length
                    point = _point_at(start, delta, max(0.0, parameter))
                    candidates.append(
                        (
                            distance,
                            zone.item_id,
                            {
                                "zone_id": zone.item_id,
                                "segment_index": index,
                                "t": _clean(max(0.0, parameter)),
                                "point": _clean_point(point),
                                "distance": _clean(distance),
                                "metadata": zone.metadata,
                            },
                        )
                    )
                    break
            cumulative += length
    candidates.sort(key=lambda value: (_clean(value[0]), value[1]))
    maximum = cast(int, query["max_crossings"])
    return {
        **_provenance(scene, "crossed_zones"),
        "crossings": [candidate[2] for candidate in candidates[:maximum]],
        "truncated": len(candidates) > maximum,
    }


def _box_overlap(first: _Shape, second: _Shape) -> bool:
    return all(
        first.lower[index] <= second.upper[index] + _EPSILON
        and second.lower[index] <= first.upper[index] + _EPSILON
        for index in range(3)
    )


def _cylinder_box_overlap(cylinder: _Shape, box: _Shape) -> bool:
    if cylinder.upper[2] < box.lower[2] or box.upper[2] < cylinder.lower[2]:
        return False
    center = cast(Vec3, cylinder.center)
    closest_x = min(max(center[0], box.lower[0]), box.upper[0])
    closest_y = min(max(center[1], box.lower[1]), box.upper[1])
    radius = cast(float, cylinder.radius)
    return (center[0] - closest_x) ** 2 + (center[1] - closest_y) ** 2 <= radius**2 + _EPSILON


def _sphere_overlap(sphere: _Shape, body: _Shape) -> bool:
    """Measure to the nearest occupied point, including vertical body extent."""
    center = cast(Vec3, sphere.center)
    vertical = max(body.lower[2] - center[2], center[2] - body.upper[2], 0.0)
    if body.kind == "box":
        gaps = [max(body.lower[i] - center[i], center[i] - body.upper[i], 0.0) for i in range(2)]
        distance = math.hypot(*gaps, vertical)
    else:
        body_center = cast(Vec3, body.center)
        horizontal = max(
            math.hypot(center[0] - body_center[0], center[1] - body_center[1])
            - cast(float, body.radius),
            0.0,
        )
        distance = math.hypot(horizontal, vertical)
    return distance <= cast(float, sphere.radius) + _EPSILON


def _cone_overlap(cone: _Shape, body: _Shape) -> bool:
    """Intersect an arbitrarily directed cone with the complete occupied body."""
    if not _box_overlap(cone, body):
        return False
    apex, axis = cast(Vec3, cone.center), cast(Vec3, cone.axis)
    length, radius = cast(float, cone.length), cast(float, cone.radius)
    scale = max(length, radius, *(body.upper[i] - body.lower[i] for i in range(3)), 1.0)
    lower = tuple((body.lower[i] - apex[i]) / scale for i in range(3))
    upper = tuple((body.upper[i] - apex[i]) / scale for i in range(3))
    base = tuple(component * length / scale for component in axis)

    def cone_support(direction: Vec3) -> Vec3:
        axial = sum(direction[i] * axis[i] for i in range(3))
        radial = tuple(direction[i] - axial * axis[i] for i in range(3))
        magnitude = math.hypot(*radial)
        point = cast(
            Vec3,
            tuple(
                base[i] + (radius / scale * radial[i] / magnitude if magnitude else 0.0)
                for i in range(3)
            ),
        )
        return point if sum(point[i] * direction[i] for i in range(3)) > 0 else (0.0, 0.0, 0.0)

    def body_support(direction: Vec3) -> Vec3:
        if body.kind == "box":
            return cast(Vec3, tuple(upper[i] if direction[i] >= 0 else lower[i] for i in range(3)))
        center = cast(Vec3, body.center)
        magnitude = math.hypot(direction[0], direction[1])
        body_radius = cast(float, body.radius) / scale
        return (
            (center[0] - apex[0]) / scale
            + (body_radius * direction[0] / magnitude if magnitude else 0.0),
            (center[1] - apex[1]) / scale
            + (body_radius * direction[1] / magnitude if magnitude else 0.0),
            upper[2] if direction[2] >= 0 else lower[2],
        )

    return convex_overlap(cone_support, body_support, tolerance=max(1e-12, 1e-9 / scale))


def _prism_overlap(prism: _Shape, body: _Shape) -> bool:
    """Intersect the complete rectangular volume and actual Box or circular body.

    An upright frame admits exact disk/rectangle contact, including circular tangencies.
    """
    if not _box_overlap(prism, body):
        return False
    center = cast(Vec3, prism.center)
    basis = cast(tuple[Vec3, Vec3, Vec3], prism.basis)
    extents = cast(Vec3, prism.half_extents)
    if body.kind == "cylinder":
        vertical = next((j for j, axis in enumerate(basis) if axis[0] == 0 and axis[1] == 0), None)
        if vertical is not None:
            body_center = cast(Vec3, body.center)
            squared = sum(
                max(
                    abs(sum((body_center[i] - center[i]) * basis[j][i] for i in range(2)))
                    - extents[j],
                    0.0,
                )
                ** 2
                for j in range(3)
                if j != vertical
            )
            return squared <= cast(float, body.radius) ** 2 + _EPSILON
    scale = max(*extents, *(body.upper[i] - body.lower[i] for i in range(3)), 1.0)
    lower = tuple((body.lower[i] - center[i]) / scale for i in range(3))
    upper = tuple((body.upper[i] - center[i]) / scale for i in range(3))

    def prism_support(direction: Vec3) -> Vec3:
        signed = [
            extents[j]
            / scale
            * (1 if sum(direction[i] * basis[j][i] for i in range(3)) >= 0 else -1)
            for j in range(3)
        ]
        return cast(Vec3, tuple(sum(signed[j] * basis[j][i] for j in range(3)) for i in range(3)))

    def body_support(direction: Vec3) -> Vec3:
        if body.kind == "box":
            return cast(Vec3, tuple(upper[i] if direction[i] >= 0 else lower[i] for i in range(3)))
        body_center = cast(Vec3, body.center)
        magnitude = math.hypot(direction[0], direction[1])
        radius = cast(float, body.radius) / scale
        return (
            (body_center[0] - center[0]) / scale
            + (radius * direction[0] / magnitude if magnitude else 0.0),
            (body_center[1] - center[1]) / scale
            + (radius * direction[1] / magnitude if magnitude else 0.0),
            upper[2] if direction[2] >= 0 else lower[2],
        )

    return convex_overlap(prism_support, body_support, tolerance=max(1e-12, 1e-9 / scale))


def _overlap(first: _Shape, second: _Shape) -> bool:
    if first.kind == "sphere":
        return _sphere_overlap(first, second)
    if first.kind == "cone":
        return _cone_overlap(first, second)
    if first.kind == "prism":
        return _prism_overlap(first, second)
    if first.kind == "box" and second.kind == "box":
        return _box_overlap(first, second)
    if first.kind == "cylinder" and second.kind == "box":
        return _cylinder_box_overlap(first, second)
    if first.kind == "box" and second.kind == "cylinder":
        return _cylinder_box_overlap(second, first)
    if first.upper[2] < second.lower[2] or second.upper[2] < first.lower[2]:
        return False
    first_center, second_center = cast(Vec3, first.center), cast(Vec3, second.center)
    radius = cast(float, first.radius) + cast(float, second.radius)
    return (
        math.hypot(first_center[0] - second_center[0], first_center[1] - second_center[1])
        <= radius + _EPSILON
    )


def _resolve_area(scene: _CompiledScene, query: Mapping[str, object]) -> dict[str, object]:
    area = _shape_from_payload(cast(Mapping[str, object], query["shape"]))
    include = frozenset(cast(Sequence[str], query["include"]))
    retained: list[dict[str, object]] = []
    for item in (*scene.objects, *scene.zones):
        plural = {"geometry": "geometry", "entity": "entities", "zone": "zones"}[item.kind]
        if plural not in include or not item.active or not _overlap(area, item.shape):
            continue
        retained.append({"kind": item.kind, "id": item.item_id, "metadata": item.metadata})
    retained.sort(key=lambda item: (cast(str, item["kind"]), cast(str, item["id"])))
    maximum = cast(int, query["max_results"])
    return {
        **_provenance(scene, "area"),
        "results": retained[:maximum],
        "truncated": len(retained) > maximum,
    }


def _segment_enters_box(origin: Vec3, destination: Vec3, lower: Vec3, upper: Vec3) -> bool:
    """Test strict box interiors so face, edge, and corner contact stay clear."""
    near, far = 0.0, 1.0
    for axis in range(3):
        delta = destination[axis] - origin[axis]
        if delta == 0:
            if not lower[axis] < origin[axis] < upper[axis]:
                return False
            continue
        first = (lower[axis] - origin[axis]) / delta
        second = (upper[axis] - origin[axis]) / delta
        near, far = max(near, min(first, second)), min(far, max(first, second))
        if near >= far:
            return False
    return near < far


def _sweep_enters_solid(
    mover: _Shape,
    solid: _Shape,
    origin: Vec3,
    destination: Vec3,
    half: Vec2,
    height: float,
) -> bool:
    """Intersect whole translated bodies, clipping curved clearance to actual shared height."""
    lower_z, upper_z = solid.lower[2] - height, solid.upper[2]
    if mover.kind == solid.kind == "box":
        return _segment_enters_box(
            origin,
            destination,
            (solid.lower[0] - half[0], solid.lower[1] - half[1], lower_z),
            (solid.upper[0] + half[0], solid.upper[1] + half[1], upper_z),
        )
    near, far = 0.0, 1.0
    delta_z = destination[2] - origin[2]
    if delta_z == 0:
        if not lower_z < origin[2] < upper_z:
            return False
    else:
        first, second = (lower_z - origin[2]) / delta_z, (upper_z - origin[2]) / delta_z
        near, far = max(near, min(first, second)), min(far, max(first, second))
        if near >= far:
            return False
    start = cast(Vec2, tuple(origin[i] + near * (destination[i] - origin[i]) for i in range(2)))
    end = cast(Vec2, tuple(origin[i] + far * (destination[i] - origin[i]) for i in range(2)))
    if solid.kind == "box":
        lower, upper = solid.lower[:2], solid.upper[:2]
        radius = cast(float, mover.radius)
    else:
        center = cast(Vec3, solid.center)
        if mover.kind == "box":
            lower = (center[0] - half[0], center[1] - half[1])
            upper = (center[0] + half[0], center[1] + half[1])
            radius = cast(float, solid.radius)
        else:
            lower = upper = center[:2]
            radius = cast(float, solid.radius) + cast(float, mover.radius)
    return swept_footprint_hits_box(
        start,
        end,
        lower,
        upper,
        half_extents=(0.0, 0.0),
        radius=radius,
    )


def _resolve_body_proximity(
    scene: _CompiledScene, query: Mapping[str, object]
) -> dict[str, object]:
    """Find the continuous near-body interval during translation of an actual entity.

    The Minkowski difference of Box/circular footprints is a rounded rectangle.
    Its horizontal gap combines with the actual vertical interval gap. Squared
    distance to this convex volume is convex along the segment, so bounded
    minimization and bisection find the whole interval without sampling gaps.
    No metadata or game consequences enter this physical calculation.
    """
    entities = cast(Sequence[Mapping[str, object]], scene.admitted.as_payload()["entities"])
    moving = next(row for row in entities if row["id"] == query["moving_entity_id"])
    fixed = next(row for row in entities if row["id"] == query["fixed_entity_id"])
    origin, destination, center = (
        _vec3(moving["position"]),
        _vec3(query["destination"]),
        _vec3(fixed["position"]),
    )
    half = [0.0, 0.0]
    rounding = 0.0
    for row in (moving, fixed):
        footprint = cast(Mapping[str, object], row["footprint"])
        if footprint["kind"] == "circle":
            rounding += float(cast(float, footprint["radius"]))
        else:
            extents = cast(Sequence[float], footprint["half_extents"])
            half = [half[i] + float(extents[i]) for i in range(2)]
    lower_z = center[2] - float(cast(float, moving["height"]))
    upper_z = center[2] + float(cast(float, fixed["height"]))
    delta = tuple(destination[i] - origin[i] for i in range(3))

    def distance_squared(t: float) -> float:
        point = tuple(origin[i] + delta[i] * t for i in range(3))
        gaps = [max(0.0, abs(point[i] - center[i]) - half[i]) for i in range(2)]
        horizontal = max(0.0, math.hypot(*gaps) - rounding)
        vertical = max(0.0, lower_z - point[2], point[2] - upper_z)
        return horizontal * horizontal + vertical * vertical

    left, right = 0.0, 1.0
    for _ in range(90):
        a, b = left + (right - left) / 3, right - (right - left) / 3
        if distance_squared(a) <= distance_squared(b):
            right = b
        else:
            left = a
    closest = min((0.0, (left + right) / 2, 1.0), key=distance_squared)
    minimum = math.sqrt(distance_squared(closest))
    radius = float(cast(float, query["radius"]))
    hit = minimum <= radius + _EPSILON
    result: dict[str, object] = {
        **_provenance(scene, "body_proximity"),
        "hit": hit,
        "entry": None,
        "exit": None,
        "minimum_distance": _clean(minimum),
    }
    if not hit:
        return result

    def boundary(outside: float) -> float:
        inside = closest
        for _ in range(70):
            midpoint = (outside + inside) / 2
            if distance_squared(midpoint) <= radius * radius:
                inside = midpoint
            else:
                outside = midpoint
        return _clean(inside)

    # A numerical tangency within engine tolerance is a single contact, not an
    # artificially widened interval. Endpoint contact uses that same tolerance.
    result["entry"] = (
        0.0
        if math.sqrt(distance_squared(0)) <= radius + _EPSILON
        else boundary(0)
        if minimum <= radius
        else _clean(closest)
    )
    result["exit"] = (
        1.0
        if math.sqrt(distance_squared(1)) <= radius + _EPSILON
        else boundary(1)
        if minimum <= radius
        else _clean(closest)
    )
    return result


def _resolve_sweep(scene: _CompiledScene, query: Mapping[str, object]) -> dict[str, object]:
    """Sweep the actual box or circular mover continuously through selected solids."""
    moving_id = query["moving_entity_id"]
    mover = next(
        item for item in scene.objects if item.kind == "entity" and item.item_id == moving_id
    )
    mover_value = next(
        item
        for item in cast(Sequence[Mapping[str, object]], scene.admitted.as_payload()["entities"])
        if item["id"] == moving_id
    )
    origin = _vec3(mover_value["position"])
    destination = _vec3(query["destination"])
    result: dict[str, object] = {
        **_provenance(scene, "sweep"),
        "supported": True,
        "clear": False,
        "distance": _clean(math.dist(origin, destination)),
        "blockers": [],
        "reason": "out_of_bounds",
    }
    excluded = frozenset(cast(Sequence[str], query["exclude_entity_ids"])) | {cast(str, moving_id)}
    selector = cast(Mapping[str, object], query["metadata_filter"])
    selected = [
        item
        for item in scene.objects
        if item.active
        and item.movement == "blocking"
        and (item.kind != "entity" or (query["include_entities"] and item.item_id not in excluded))
        and _matches_metadata(item, selector)
    ]
    footprint = cast(Mapping[str, object], mover_value["footprint"])
    if footprint["kind"] == "circle":
        half: Vec2 = (cast(float, mover.shape.radius), cast(float, mover.shape.radius))
    else:
        extents = cast(Sequence[int | float], footprint["half_extents"])
        half = (float(extents[0]), float(extents[1]))
    height = float(cast(int | float, mover_value["height"]))
    offset_lower = (-half[0], -half[1], 0.0)
    offset_upper = (half[0], half[1], height)
    lower = tuple(scene.lower[i] - offset_lower[i] for i in range(3))
    upper = tuple(scene.upper[i] - offset_upper[i] for i in range(3))
    if any(
        not lower[i] <= point[i] <= upper[i] for point in (origin, destination) for i in range(3)
    ):
        return result
    blockers = [
        {"kind": item.kind, "id": item.item_id}
        for item in selected
        if _sweep_enters_solid(mover.shape, item.shape, origin, destination, half, height)
    ]
    blockers.sort(key=lambda item: (item["kind"], item["id"]))
    return {
        **result,
        "clear": not blockers,
        "blockers": blockers,
        "reason": "blocked" if blockers else "clear",
    }


def _placement_box(value: Mapping[str, object], kind: str) -> tuple[PlacementPoint, PlacementPoint]:
    """Derive exact decimal box bounds from admitted geometry or entity body facts."""
    if kind == "geometry":
        volume = cast(Mapping[str, object], value["volume"])
        return (
            cast(
                PlacementPoint,
                tuple(Fraction(str(v)) for v in cast(Sequence[object], volume["min"])),
            ),
            cast(
                PlacementPoint,
                tuple(Fraction(str(v)) for v in cast(Sequence[object], volume["max"])),
            ),
        )
    position = tuple(Fraction(str(v)) for v in cast(Sequence[object], value["position"]))
    footprint = cast(Mapping[str, object], value["footprint"])
    half_x, half_y = (Fraction(str(v)) for v in cast(Sequence[object], footprint["half_extents"]))
    return (
        (position[0] - half_x, position[1] - half_y, position[2]),
        (position[0] + half_x, position[1] + half_y, position[2] + Fraction(str(value["height"]))),
    )


def _resolve_placement(scene: _CompiledScene, query: Mapping[str, object]) -> dict[str, object]:
    """Find the exact nearest box placement outside the union of solid box interiors.

    Each arrangement cell has an exact closest point to the requested point or
    admitted face rectangle. Face bounds, the origin, domain bounds, and expanded
    obstacle bounds contain that minimizer, including the origin tie-breaker.
    Enumerating their Cartesian product in lexicographic distance order proves
    the first clear point is nearest. Surface contact stays clear. Curved solids
    and references fail explicitly instead of using bounding-box approximations.
    """
    result: dict[str, object] = {
        **_provenance(scene, "placement"),
        "supported": True,
        "found": False,
        "position": None,
        "distance": None,
        "reason": "no_placement",
        "examined": 0,
    }
    moving_id = query["moving_entity_id"]
    mover = next(
        item for item in scene.objects if item.kind == "entity" and item.item_id == moving_id
    )
    precise = "reference_face" in query
    payload = scene.admitted.as_payload()
    rows = (
        {
            (kind, row["id"]): row
            for kind, collection in (("geometry", "geometry"), ("entity", "entities"))
            for row in cast(Sequence[Mapping[str, object]], payload[collection])
        }
        if precise
        else {}
    )
    origin = cast(
        PlacementPoint,
        tuple(
            Fraction(str(value)) if precise else float(cast(int | float, value))
            for value in cast(Sequence[object], query["origin"])
        ),
    )
    objective_lower, objective_upper = origin, origin
    if precise:
        reference = cast(Mapping[str, object], query["reference_face"])
        target = cast(Mapping[str, object], reference["target"])
        source = next(
            item
            for item in scene.objects
            if item.kind == target["kind"] and item.item_id == target["id"]
        )
        if source.shape.kind != "box":
            return {**result, "supported": False, "reason": "unsupported_geometry"}
        axis, use_upper = {
            "west": (0, False),
            "east": (0, True),
            "south": (1, False),
            "north": (1, True),
            "down": (2, False),
            "up": (2, True),
        }[cast(str, reference["face"])]
        source_lower, source_upper = _placement_box(
            rows[(source.kind, source.item_id)], source.kind
        )
        face_coordinate = source_upper[axis] if use_upper else source_lower[axis]
        objective_lower = cast(
            PlacementPoint,
            tuple(face_coordinate if i == axis else source_lower[i] for i in range(3)),
        )
        objective_upper = cast(
            PlacementPoint,
            tuple(face_coordinate if i == axis else source_upper[i] for i in range(3)),
        )
    if mover.shape.kind != "box":
        return {**result, "supported": False, "reason": "unsupported_geometry"}
    mover_value = next(
        item
        for item in cast(Sequence[Mapping[str, object]], payload["entities"])
        if item["id"] == moving_id
    )
    current = cast(
        PlacementPoint,
        tuple(
            Fraction(str(value)) if precise else float(cast(int | float, value))
            for value in cast(Sequence[object], mover_value["position"])
        ),
    )
    mover_lower, mover_upper = (
        _placement_box(mover_value, "entity") if precise else (mover.shape.lower, mover.shape.upper)
    )
    offset_lower = tuple(mover_lower[i] - current[i] for i in range(3))
    offset_upper = tuple(
        mover_upper[i] - current[i]
        if precise
        else mover.shape.upper[i] - mover.shape.lower[i] + offset_lower[i]
        for i in range(3)
    )
    # A selected admitted box constrains the existing search domain, not its algorithm.
    domain_lower, domain_upper = (
        (
            cast(PlacementPoint, tuple(Fraction(str(v)) for v in scene.lower)),
            cast(PlacementPoint, tuple(Fraction(str(v)) for v in scene.upper)),
        )
        if precise
        else (scene.lower, scene.upper)
    )
    if "containing_zone_id" in query:
        zone = next(item for item in scene.zones if item.item_id == query["containing_zone_id"])
        if zone.shape.kind != "box":
            return {**result, "supported": False, "reason": "unsupported_geometry"}
        zone_lower = tuple(Fraction(str(v)) if precise else v for v in zone.shape.lower)
        zone_upper = tuple(Fraction(str(v)) if precise else v for v in zone.shape.upper)
        domain_lower = cast(
            PlacementPoint, tuple(max(domain_lower[i], zone_lower[i]) for i in range(3))
        )
        domain_upper = cast(
            PlacementPoint, tuple(min(domain_upper[i], zone_upper[i]) for i in range(3))
        )
    lower = tuple(domain_lower[i] - offset_lower[i] for i in range(3))
    upper = tuple(domain_upper[i] - offset_upper[i] for i in range(3))
    if any(lower[i] > upper[i] for i in range(3)):
        return result
    excluded = frozenset(cast(Sequence[str], query["exclude_entity_ids"])) | {cast(str, moving_id)}
    selector = cast(Mapping[str, object], query["metadata_filter"])
    obstacles = []
    for item in scene.objects:
        if (
            not item.active
            or item.movement != "blocking"
            or (item.kind == "entity" and item.item_id in excluded)
        ):
            continue
        if not _matches_metadata(item, selector):
            continue
        if item.shape.kind != "box":
            return {**result, "supported": False, "reason": "unsupported_geometry"}
        solid_lower, solid_upper = (
            _placement_box(rows[(item.kind, item.item_id)], item.kind)
            if precise
            else (item.shape.lower, item.shape.upper)
        )
        obstacle_lower = tuple(solid_lower[i] - offset_upper[i] for i in range(3))
        obstacle_upper = tuple(solid_upper[i] - offset_lower[i] for i in range(3))
        if all(obstacle_upper[i] > lower[i] and obstacle_lower[i] < upper[i] for i in range(3)):
            obstacles.append((obstacle_lower, obstacle_upper))
    axes = []
    for i in range(3):
        values = {min(max(origin[i], lower[i]), upper[i]), lower[i], upper[i]}
        for coordinate in (objective_lower[i], objective_upper[i]):
            values.add(min(max(coordinate, lower[i]), upper[i]))
        for obstacle_lower, obstacle_upper in obstacles:
            for coordinate in (obstacle_lower[i], obstacle_upper[i]):
                if lower[i] <= coordinate <= upper[i]:
                    values.add(coordinate)
        axes.append(
            sorted(
                values,
                key=lambda coordinate: (
                    (coordinate - min(max(coordinate, objective_lower[i]), objective_upper[i]))
                    ** 2,
                    (coordinate - origin[i]) ** 2,
                    coordinate,
                ),
            )
        )

    def candidate(
        index: tuple[int, int, int],
    ) -> tuple[PlacementNumber, PlacementNumber, PlacementPoint, tuple[int, int, int]]:
        point = cast(PlacementPoint, tuple(axes[i][index[i]] for i in range(3)))
        primary = sum(
            (point[i] - min(max(point[i], objective_lower[i]), objective_upper[i])) ** 2
            for i in range(3)
        )
        secondary = sum((point[i] - origin[i]) ** 2 for i in range(3))
        return primary, secondary, point, index

    queue = [candidate((0, 0, 0))]
    visited = {(0, 0, 0)}
    maximum = cast(int, query["max_candidates"])
    examined = 0
    while queue and examined < maximum:
        distance_squared, _, point, index = heapq.heappop(queue)
        examined += 1
        if not any(all(lo[i] < point[i] < hi[i] for i in range(3)) for lo, hi in obstacles):
            return {
                **result,
                "found": True,
                "position": _clean_point(cast(Vec3, tuple(float(v) for v in point))),
                "distance": _clean(math.sqrt(distance_squared)),
                "reason": "placed",
                "examined": examined,
            }
        for axis in range(3):
            following = tuple(index[i] + (1 if i == axis else 0) for i in range(3))
            if following[axis] < len(axes[axis]) and following not in visited:
                visited.add(following)
                heapq.heappush(queue, candidate(cast(tuple[int, int, int], following)))
    return {
        **result,
        "supported": not bool(queue),
        "reason": "candidate_limit" if queue else "no_placement",
        "examined": examined,
    }


class SpatialEngine:
    """Deep public module for authoritative deterministic spatial facts."""

    def __init__(self, scene: object) -> None:
        self._current = _compile(admit_spatial_scene(scene))
        self._compiled: OrderedDict[str, _CompiledScene] = OrderedDict(
            ((self._current.admitted.snapshot_id, self._current),)
        )
        self._cache: OrderedDict[tuple[str, bytes], AdmittedSpatialResult] = OrderedDict()

    @property
    def snapshot(self) -> AdmittedSpatialScene:
        """Return the immutable identity currently used by unsnapshotted queries."""

        return self._current.admitted

    def replace_scene(self, scene: object) -> AdmittedSpatialScene:
        """Advance current geometry while retaining immutable prior snapshots."""

        admitted = admit_spatial_scene(scene)
        if admitted.map_id != self._current.admitted.map_id:
            raise SpatialValidationError(
                "Replacement scene belongs to another map",
                code="spatial_cross_map",
                path="$.map.id",
            )
        if admitted.geometry_revision <= self._current.admitted.geometry_revision:
            raise SpatialValidationError(
                "Replacement geometry revision must advance",
                code="spatial_revision_invalid",
                path="$.map.revision",
            )
        compiled = _compile(admitted)
        self._current = compiled
        self._compiled[admitted.snapshot_id] = compiled
        while len(self._compiled) > _CACHE_LIMIT:
            self._compiled.popitem(last=False)
        return admitted

    def query(
        self,
        request: object,
        *,
        snapshot: AdmittedSpatialScene | None = None,
    ) -> AdmittedSpatialResult:
        """Resolve a closed query to physical facts against one immutable scene."""

        admitted_scene = snapshot or self._current.admitted
        compiled = self._compiled.get(admitted_scene.snapshot_id)
        if compiled is None:
            compiled = _compile(admitted_scene)
            self._compiled[admitted_scene.snapshot_id] = compiled
        query = admit_spatial_query(admitted_scene, request)
        query_payload = query.as_payload()
        cache_key = (admitted_scene.snapshot_id, canonical_json_bytes(query_payload))
        cached = self._cache.get(cache_key)
        if cached is not None:
            self._cache.move_to_end(cache_key)
            return cached
        resolver = {
            "ray": _resolve_ray,
            "exposure": _resolve_exposure,
            "path": _resolve_path,
            "placement": _resolve_placement,
            "sweep": _resolve_sweep,
            "body_proximity": _resolve_body_proximity,
            "crossed_zones": _resolve_zones,
            "area": _resolve_area,
        }[query.kind]
        result = admit_spatial_result(admitted_scene, resolver(compiled, query_payload))
        self._cache[cache_key] = result
        while len(self._cache) > _CACHE_LIMIT:
            self._cache.popitem(last=False)
        return result
