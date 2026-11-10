from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
import heapq
import math
from typing import Iterable

from .model import CoverObject, Position, Stance
from .parameters import ParameterSet
from .rules import MaterialResult, resolve_material


PROVENANCE = {
    "sources": (
        "Combat/GRID.md",
        "Combat/LINE OF SIGHT.md",
        "Combat/COVER.md",
        "Combat/DESTRUCTIBLE COVER.md",
        "Combat/BREACHING.md",
        "Firearms/PROJECTILE.md",
        "Firearms/MATERIAL PENETRATION.md",
        "Explosives/BLAST GEOMETRY.md",
    )
}


STANCE_HEIGHT_M = {Stance.STANDING: 1.8, Stance.CROUCHED: 1.15, Stance.PRONE: 0.45}
_PATH_CACHE: dict[tuple[object, ...], tuple[tuple[int, int], ...]] = {}
_PATH_CACHE_LIMIT = 20000
_LOS_CACHE: dict[tuple[object, ...], bool] = {}
_EXPOSURE_CACHE: dict[tuple[object, ...], str] = {}
_GEOMETRY_CACHE_LIMIT = 200000


@dataclass(slots=True)
class GridMap:
    map_id: str
    width: int
    height: int
    full_blockers: set[tuple[int, int]] = field(default_factory=set)
    covers: dict[str, CoverObject] = field(default_factory=dict)
    _static_geometry_signature: tuple[object, ...] | None = field(default=None, init=False, repr=False)
    _static_cover_keys: tuple[str, ...] = field(default=(), init=False, repr=False)

    def in_bounds(self, position: Position) -> bool:
        return 0 <= position.x < self.width and 0 <= position.y < self.height

    def occupied(self, cell: tuple[int, int]) -> bool:
        return cell in self.full_blockers or any(cover.active and cell in cover.cells for cover in self.covers.values())

    def first_intersection(self, start: Position, end: Position, *, ray_height_m: float = 1.4) -> CoverObject | str | None:
        for cell in trace_cells(start, end)[1:-1]:
            if cell in self.full_blockers:
                return "FULL BLOCKER"
            for cover in sorted(self.covers.values(), key=lambda item: item.cover_id):
                if cover.active and cell in cover.cells and cover.height_m >= ray_height_m:
                    return cover
        return None

    def line_of_sight(self, start: Position, end: Position, target_stance: Stance = Stance.STANDING) -> bool:
        key = (self._cache_signature(), start.x, start.y, end.x, end.y, target_stance.value)
        cached = _LOS_CACHE.get(key)
        if cached is not None:
            return cached
        target_height = STANCE_HEIGHT_M[target_stance]
        ray_height = max(0.2, target_height * 0.75)
        result = self.first_intersection(start, end, ray_height_m=ray_height) is None
        if len(_LOS_CACHE) >= _GEOMETRY_CACHE_LIMIT:
            _LOS_CACHE.clear()
        _LOS_CACHE[key] = result
        return result

    def exposure(self, start: Position, end: Position, stance: Stance) -> str:
        key = (self._cache_signature(), start.x, start.y, end.x, end.y, stance.value)
        cached = _EXPOSURE_CACHE.get(key)
        if cached is not None:
            return cached
        silhouette = STANCE_HEIGHT_M[stance]
        blocking_height = 0.0
        for cell in trace_cells(start, end)[1:-1]:
            if cell in self.full_blockers:
                result = "none"
                break
            for cover in self.covers.values():
                if cover.active and cell in cover.cells:
                    blocking_height = max(blocking_height, cover.height_m)
        else:
            if blocking_height <= 0:
                result = "full"
            else:
                visible_fraction = max(0.0, min(1.0, (silhouette - blocking_height) / silhouette))
                if visible_fraction <= 0:
                    result = "none"
                elif visible_fraction <= 0.25:
                    result = "quarter"
                elif visible_fraction <= 0.6:
                    result = "half"
                else:
                    result = "full"
        if len(_EXPOSURE_CACHE) >= _GEOMETRY_CACHE_LIMIT:
            _EXPOSURE_CACHE.clear()
        _EXPOSURE_CACHE[key] = result
        return result

    def _cache_signature(self) -> tuple[object, ...]:
        cover_keys = tuple(sorted(self.covers))
        if self._static_geometry_signature is None or cover_keys != self._static_cover_keys:
            self._static_cover_keys = cover_keys
            self._static_geometry_signature = (
                self.map_id,
                self.width,
                self.height,
                tuple(sorted(self.full_blockers)),
                tuple(
                    (cover.cover_id, cover.height_m, tuple(sorted(cover.cells)))
                    for cover in sorted(self.covers.values(), key=lambda item: item.cover_id)
                ),
            )
        dynamic_cover_state = tuple(
            (cover_id, self.covers[cover_id].active)
            for cover_id in cover_keys
        )
        return self._static_geometry_signature, dynamic_cover_state


def trace_cells(start: Position, end: Position) -> list[tuple[int, int]]:
    """Deterministic supercover-like trace across the 1m test grid."""
    return list(_trace_cells_cached(start.x, start.y, end.x, end.y))


@lru_cache(maxsize=200000)
def _trace_cells_cached(start_x: float, start_y: float, end_x: float, end_y: float) -> tuple[tuple[int, int], ...]:
    dx = end_x - start_x
    dy = end_y - start_y
    steps = max(1, math.ceil(max(abs(dx), abs(dy)) * 4))
    cells: list[tuple[int, int]] = []
    for step in range(steps + 1):
        ratio = step / steps
        cell = (math.floor(start_x + dx * ratio), math.floor(start_y + dy * ratio))
        if not cells or cell != cells[-1]:
            cells.append(cell)
    return tuple(cells)


def directional_facing_protects(
    shield_position: Position,
    protected_position: Position,
    threat_position: Position,
    facing_degrees: float,
    arc_degrees: float = 120.0,
) -> bool:
    facing = math.radians(facing_degrees)
    facing_vector = (math.cos(facing), math.sin(facing))
    threat_vector = (threat_position.x - shield_position.x, threat_position.y - shield_position.y)
    magnitude = math.hypot(*threat_vector)
    if magnitude == 0:
        return False
    dot = (facing_vector[0] * threat_vector[0] + facing_vector[1] * threat_vector[1]) / magnitude
    angle = math.degrees(math.acos(max(-1.0, min(1.0, dot))))
    shield_between = shield_position.distance_to(threat_position) < protected_position.distance_to(threat_position)
    return shield_between and angle <= arc_degrees / 2


@dataclass(frozen=True, slots=True)
class ProjectileGeometryResult:
    projectile_index: int
    intersection: str
    material: MaterialResult | None
    cover_hp_before: int | None
    cover_hp_after: int | None
    target_reached: bool


def cover_damage(damage: int, material_resistance: int) -> int:
    """Locked v0.1 fixture conversion; continuation remains MATERIAL PENETRATION."""
    return max(0, damage - material_resistance)


def actual_difficulty(open_difficulty: int, exposure: str, parameters: ParameterSet) -> int | None:
    normalized = exposure.lower()
    if normalized == "none":
        return None
    if normalized not in {"full", "half", "quarter"}:
        raise ValueError(f"unknown exposure {exposure}")
    return open_difficulty + int(parameters.values[f"cover.actual_difficulty_offset.{normalized}"])


def reduce_exposure_band(exposure: str, bands: int = 1) -> str:
    """Reduce visible silhouette but never manufacture full COVER."""
    order = ("full", "half", "quarter")
    normalized = exposure.lower()
    if normalized == "none":
        return "none"
    if normalized not in order:
        raise ValueError(f"unknown exposure {exposure}")
    return order[min(len(order) - 1, order.index(normalized) + max(0, bands))]


def resolve_sequential_projectiles(
    cover: CoverObject,
    projectile_count: int,
    *,
    damage: int,
    target_pen: int,
    material_pen: int,
) -> list[ProjectileGeometryResult]:
    """Resolve sequential impacts with immediate post-destruction geometry updates."""
    results: list[ProjectileGeometryResult] = []
    for index in range(projectile_count):
        if not cover.active:
            results.append(ProjectileGeometryResult(index, "CLEAR AFTER DESTRUCTION", None, cover.cover_hp, cover.cover_hp, True))
            continue
        before = cover.cover_hp
        material = resolve_material(damage, target_pen, material_pen, cover.material_resistance)
        if cover.cover_hp is not None:
            cover.cover_hp = max(0, cover.cover_hp - cover_damage(damage, cover.material_resistance))
            if cover.cover_hp == 0:
                cover.active = False
        results.append(
            ProjectileGeometryResult(
                index,
                "COVER",
                material,
                before,
                cover.cover_hp,
                material.continues,
            )
        )
    return results


@dataclass(frozen=True, slots=True)
class BlastSnapshotResult:
    component: str
    protected_by_snapshot: bool
    cover_destroyed_after: bool


def resolve_blast_snapshot(
    cover: CoverObject | None,
    components: Iterable[tuple[str, int]],
) -> list[BlastSnapshotResult]:
    """All simultaneous components see pre-detonation cover; damage applies after evaluation."""
    component_list = list(components)
    if cover is None:
        return [
            BlastSnapshotResult(component, False, False)
            for component, _damage in component_list
        ]
    pre_active = cover.active
    total_cover_damage = sum(cover_damage(damage, cover.material_resistance) for _, damage in component_list)
    results = [
        BlastSnapshotResult(component, pre_active, False)
        for component, _damage in component_list
    ]
    if cover.cover_hp is not None and pre_active:
        cover.cover_hp = max(0, cover.cover_hp - total_cover_damage)
        if cover.cover_hp == 0:
            cover.active = False
    return [BlastSnapshotResult(item.component, item.protected_by_snapshot, pre_active and not cover.active) for item in results]


def shortest_step_toward(start: Position, goal: Position, distance_m: float, grid: GridMap) -> Position:
    path = shortest_path(start, goal, grid, distance_m)
    return path[-1] if path else start


def _position_cell(position: Position) -> tuple[int, int]:
    return round(position.x), round(position.y)


def _cell_position(cell: tuple[int, int]) -> Position:
    return Position(float(cell[0]), float(cell[1]))


def _walkable(grid: GridMap, cell: tuple[int, int], goal: tuple[int, int]) -> bool:
    x, y = cell
    if not (0 <= x < grid.width and 0 <= y < grid.height):
        return False
    return cell == goal or not grid.occupied(cell)


def shortest_path(start: Position, goal: Position, grid: GridMap, max_distance_m: float | None = None) -> list[Position]:
    """Deterministic eight-neighbour A* path on the declared 1m fixture grid.

    Returned positions exclude ``start``. Diagonal moves cannot cut between two
    blocked orthogonal cells. ``max_distance_m`` truncates by travelled path
    length rather than straight-line displacement, keeping AP accounting honest
    around walls and physical COVER.
    """
    start_cell = _position_cell(start)
    goal_cell = _position_cell(goal)
    if start_cell == goal_cell:
        return []
    blocked = set(grid.full_blockers)
    for cover in grid.covers.values():
        if cover.active:
            blocked.update(cover.cells)
    blocked.discard(start_cell)
    blocked.discard(goal_cell)
    if not (0 <= start_cell[0] < grid.width and 0 <= start_cell[1] < grid.height):
        raise ValueError(f"start cell {start_cell} is not walkable")
    cache_key = (grid.map_id, grid.width, grid.height, tuple(sorted(blocked)), start_cell, goal_cell)
    cached = _PATH_CACHE.get(cache_key)
    if cached is not None:
        cells = list(cached)
        return _truncate_path(start, cells, max_distance_m)
    directions = (
        (-1, 0, 1.0),
        (0, -1, 1.0),
        (0, 1, 1.0),
        (1, 0, 1.0),
        (-1, -1, math.sqrt(2)),
        (-1, 1, math.sqrt(2)),
        (1, -1, math.sqrt(2)),
        (1, 1, math.sqrt(2)),
    )
    frontier: list[tuple[float, float, int, int]] = [(start.distance_to(goal), 0.0, start_cell[0], start_cell[1])]
    came_from: dict[tuple[int, int], tuple[int, int] | None] = {start_cell: None}
    cost: dict[tuple[int, int], float] = {start_cell: 0.0}
    while frontier:
        _priority, current_cost, x, y = heapq.heappop(frontier)
        current = (x, y)
        if current == goal_cell:
            break
        if current_cost > cost[current] + 1e-9:
            continue
        for dx, dy, step_cost in directions:
            neighbor = (x + dx, y + dy)
            if not (0 <= neighbor[0] < grid.width and 0 <= neighbor[1] < grid.height) or neighbor in blocked:
                continue
            if dx and dy:
                if (x + dx, y) in blocked or (x, y + dy) in blocked:
                    continue
            next_cost = current_cost + step_cost
            if next_cost + 1e-9 >= cost.get(neighbor, float("inf")):
                continue
            cost[neighbor] = next_cost
            came_from[neighbor] = current
            heuristic = math.hypot(goal_cell[0] - neighbor[0], goal_cell[1] - neighbor[1])
            heapq.heappush(frontier, (next_cost + heuristic, next_cost, neighbor[0], neighbor[1]))
    if goal_cell not in came_from:
        return []
    cells: list[tuple[int, int]] = []
    cursor = goal_cell
    while cursor != start_cell:
        cells.append(cursor)
        parent = came_from[cursor]
        if parent is None:
            break
        cursor = parent
    cells.reverse()
    if len(_PATH_CACHE) >= _PATH_CACHE_LIMIT:
        _PATH_CACHE.clear()
    _PATH_CACHE[cache_key] = tuple(cells)
    return _truncate_path(start, cells, max_distance_m)


def _truncate_path(start: Position, cells: list[tuple[int, int]], max_distance_m: float | None) -> list[Position]:
    if max_distance_m is None:
        return [_cell_position(cell) for cell in cells]
    selected: list[Position] = []
    travelled = 0.0
    previous = start
    for cell in cells:
        position = _cell_position(cell)
        step = previous.distance_to(position)
        if travelled + step > max_distance_m + 1e-9:
            break
        selected.append(position)
        travelled += step
        previous = position
    return selected


def nearest_reachable(
    start: Position,
    candidates: Iterable[Position],
    grid: GridMap,
    *,
    prefer_near: Position | None = None,
) -> Position | None:
    """Choose a reachable candidate by path length with stable tie breaking."""
    ranked: list[tuple[float, float, float, Position]] = []
    for candidate in candidates:
        path = shortest_path(start, candidate, grid)
        if not path and _position_cell(start) != _position_cell(candidate):
            continue
        distance = 0.0
        previous = start
        for position in path:
            distance += previous.distance_to(position)
            previous = position
        preference = candidate.distance_to(prefer_near) if prefer_near else 0.0
        ranked.append((distance, preference, candidate.x * grid.height + candidate.y, candidate))
    return min(ranked, default=(0, 0, 0, None))[-1]
