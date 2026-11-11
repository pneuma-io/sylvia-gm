"""Shooter lanes in the existing lab, using Sylvia's accepted spatial authority.

The lab's integer positions denote cell centres. The host uses physical metres,
so the adapter adds 0.5 on x/y. Authored host scenes may retain thin walls/layers;
ordinary lab cell cover projects to cell volumes without changing its source data.
"""

from copy import deepcopy
import math
from typing import Any, Callable

from sylvia_gm.spatial import SpatialEngine
from sylvia_gm.spatial._grid_lane import grid_lane_zone

from .engine import EventEngine, TriggerEffect
from .geometry import GridMap, STANCE_HEIGHT_M
from .model import Position, PreparedAction, SimulationState
from .policies import OverwatchSelection
from .rules import apply_damage, resolve_material


def point(actor, position=None):
    position = position or actor.position
    return [position.x + 0.5, position.y + 0.5, actor.elevation + STANCE_HEIGHT_M[actor.stance] / 2]


def muzzle(actor):
    result = point(actor)
    norm = math.hypot(*actor.facing)
    # The default box fixture places the muzzle just beyond its 0.4m half extent.
    # Imported TD fixtures retain their actual offset, including nondefault facing.
    dx, dy = actor.muzzle_offset or (0.41 * actor.facing[0] / norm, 0.41 * actor.facing[1] / norm)
    result[0] += dx
    result[1] += dy
    result[2] = actor.elevation + STANCE_HEIGHT_M[actor.stance] * 7 / 9
    return result


def movement_path(state, grid, actor_id, goal, maximum_distance, *, allow_diagonal=True):
    actor = state.actors[actor_id]
    result = (
        SpatialEngine(scene(state, grid))
        .query(
            {
                "kind": "path",
                "map_id": grid.map_id,
                "start": [math.floor(actor.position.x + 0.5), math.floor(actor.position.y + 0.5)],
                "goal": [round(goal.x), round(goal.y)],
                "allow_diagonal": allow_diagonal,
                "corner_policy": "forbid_cutting",
                "include_entities": True,
                "moving_entity_id": actor_id,
                "max_steps": 256,
                "cost_profile": True,
            }
        )
        .as_payload()
    )
    if not result["found"]:
        return []
    # This adapter currently prices the lab's uniform metre grid. Never silently
    # price a weighted host route by Euclidean length.
    if grid.host_scene and grid.host_scene["terrain"]:
        raise ValueError("weighted host terrain is outside this lab adapter")
    path, cost, previous = [], 0.0, actor.position
    for x, y in result["cells"][1:]:
        p = Position(x, y)
        cost += previous.distance_to(p)
        if cost > maximum_distance + 1e-9:
            break
        path.append(p)
        previous = p
    if not path and allow_diagonal and maximum_distance >= 1:
        return movement_path(state, grid, actor_id, goal, maximum_distance, allow_diagonal=False)
    return path


def scene(state: SimulationState, grid: GridMap) -> dict[str, Any]:
    if grid.host_scene is not None:
        result = deepcopy(grid.host_scene)
        result["map"]["revision"] = state.revision
    else:
        result = {
            "format": 1,
            "map": {
                "id": grid.map_id,
                "revision": state.revision,
                "bounds": {"min": [0, 0, 0], "max": [grid.width, grid.height, 3]},
                "grid": {"cell_size": 1, "unit": "m", "origin": [0, 0]},
                "layers": [{"id": "floor", "min_elevation": 0, "max_elevation": 3}],
            },
            "geometry": [],
            "entities": [],
            "zones": [],
            "terrain": [],
        }
        for x, y in sorted(grid.full_blockers):
            result["geometry"].append(
                {
                    "id": f"block-{x}-{y}",
                    "map_id": grid.map_id,
                    "volume": {"kind": "box", "min": [x, y, 0], "max": [x + 1, y + 1, 3]},
                    "active": True,
                    "movement": "blocking",
                    "visibility": "opaque",
                    "metadata": {},
                    "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
                }
            )
        for cover in sorted(state.covers.values(), key=lambda c: c.cover_id):
            for x, y in sorted(cover.cells):
                result["geometry"].append(
                    {
                        "id": f"{cover.cover_id}-{x}-{y}",
                        "map_id": grid.map_id,
                        "volume": {
                            "kind": "box",
                            "min": [x, y, 0],
                            "max": [x + 1, y + 1, cover.height_m],
                        },
                        "active": cover.active,
                        "movement": "blocking",
                        "visibility": "opaque",
                        "metadata": {
                            "lab_cover_id": cover.cover_id,
                            "material_resistance": cover.material_resistance,
                            "cover_hp": cover.cover_hp,
                        },
                        "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
                    }
                )
    for geometry in result["geometry"]:
        key = geometry["metadata"].get("lab_cover_id", geometry["id"])
        if key in state.covers:
            cover = state.covers[key]
            geometry["active"] = cover.active
            geometry["metadata"].update(
                cover_hp=cover.cover_hp, material_resistance=cover.material_resistance
            )
    templates = {body["id"]: body for body in result["entities"]}
    result["entities"] = []
    for actor in sorted(state.actors.values(), key=lambda a: a.actor_id):
        body = deepcopy(
            templates.get(
                actor.actor_id,
                {
                    "id": actor.actor_id,
                    "map_id": grid.map_id,
                    "footprint": {"kind": "box", "half_extents": [0.4, 0.4]},
                    "movement": "blocking",
                    "visibility": "opaque",
                    "metadata": {},
                },
            )
        )
        body.update(
            position=[actor.position.x + 0.5, actor.position.y + 0.5, actor.elevation],
            height=STANCE_HEIGHT_M[actor.stance],
            facing=list(actor.facing),
            active=actor.active,
        )
        result["entities"].append(body)
    zones = {z["id"]: z for z in result["zones"]}
    zones.update(deepcopy(state.spatial_zones))
    # Preparations retain armed snapshots; the current zone representation is separate.
    for prepared in state.prepared.values():
        if prepared.fixed_zone:
            zones.setdefault(prepared.fixed_zone["id"], deepcopy(prepared.fixed_zone))
    result["zones"] = list(zones.values())
    return result


def declaration(state, grid, actor_id, selection: OverwatchSelection):
    payload = scene(state, grid)
    body = next(e for e in payload["entities"] if e["id"] == actor_id)
    return grid_lane_zone(
        payload,
        actor_id,
        {
            "pattern": selection.pattern,
            "direction": selection.direction,
            "extent": selection.extent,
            "half_width": (selection.width - 1) // 2,
            "origin": body["position"],
            "facing": body["facing"],
        },
    )


def valid(state, prepared):
    actor = state.actors.get(prepared.owner_id)
    if actor is None or not prepared.active or prepared.shots_remaining <= 0:
        return False
    if prepared.lane_zone is not None:
        lane = prepared.lane_zone["metadata"]["lane"]
        current = state.spatial_zones.get(prepared.lane_zone["id"])
        if (
            actor.pose_revision != prepared.bound_pose_revision
            or actor.stance != prepared.bound_stance
            or lane["origin"] != [actor.position.x + 0.5, actor.position.y + 0.5, actor.elevation]
            or lane["facing"] != list(actor.facing)
            or current is None
            or current["metadata"].get("owner") != prepared.owner_id
            or current["shape"] != prepared.lane_zone["shape"]
        ):
            prepared.active, prepared.shots_remaining = False, 0
            return False
    return (
        actor.active
        and not actor.downed
        and actor.weapon is not None
        and not actor.weapon.needs_cycle
        and actor.weapon.chamber == "LIVE"
        and actor.weapon.location == "READY"
        and actor.weapon.controlled_by is None
    )


def replace_watch(state, prepared):
    # Replacement invalidates every old ID owned by this shooter, including a legacy one.
    for old in state.prepared.values():
        if old.owner_id == prepared.owner_id:
            old.active, old.shots_remaining = False, 0
    state.prepared = {
        key: value for key, value in state.prepared.items() if value.owner_id != prepared.owner_id
    }
    state.prepared[prepared.prep_id] = prepared


def exposure(state, grid, owner_id, target_id):
    actor = state.actors[owner_id]
    engine = SpatialEngine(scene(state, grid))
    observer = muzzle(actor)
    result = engine.query(
        {
            "kind": "exposure",
            "map_id": grid.map_id,
            "observer": {"point": observer},
            "target": {"entity_id": target_id},
            "samples": {"horizontal": 3, "vertical": 3},
            "include": ["geometry", "entities"],
        }
    ).as_payload()
    return result["exposure"]


def contains(state, grid, prepared, actor_id):
    here = point(state.actors[actor_id])
    result = (
        SpatialEngine(scene(state, grid))
        .query(
            {
                "kind": "crossed_zones",
                "map_id": grid.map_id,
                "path": [here, here],
                "max_crossings": 4096,
            }
        )
        .as_payload()
    )
    return any(hit["zone_id"] == prepared.spatial_zone["id"] for hit in result["crossings"])


def ray(state, grid, owner_id, target_id):
    owner, target = state.actors[owner_id], state.actors[target_id]
    origin, destination = muzzle(owner), point(target)
    destination[0] += 0.000001
    destination[1] += 0.000001
    return (
        SpatialEngine(scene(state, grid))
        .query(
            {
                "kind": "ray",
                "map_id": grid.map_id,
                "origin": origin,
                "destination": destination,
                "include": ["geometry", "entities"],
                "max_intersections": 64,
            }
        )
        .as_payload()["intersections"]
    )


def route_shot(state, grid, owner_id, target_id, result, parameters, *, preserve_target_ap=True, connection_index=0):
    """Route this shot through the host's ordered intersections and current cover."""
    from .model import HitQuality

    if result.attack.quality is HitQuality.MISS:
        return []
    damage, penetration = result.attack.effective_damage, result.attack.effective_target_pen
    if connection_index > 0 and result.attack.quality is HitQuality.CRITICAL_HIT:
        if result.attack.critical_effect == "DEVASTATING HIT":
            damage -= parameters.values["critical.devastating_damage"]
        elif result.attack.critical_effect == "DEEP HIT":
            penetration -= parameters.values["critical.deep_target_pen"]
    material = parameters.values[
        f"equipment.{state.actors[owner_id].weapon.weapon_id}.material_pen"
    ]
    geometry = {g["id"]: g for g in scene(state, grid)["geometry"]}
    contacts = []
    for hit in ray(state, grid, owner_id, target_id):
        identity = hit["id"]
        if identity == owner_id:
            continue
        if identity == target_id:
            before_ap = state.actors[target_id].ap
            resolved = apply_damage(state.actors[target_id], damage, penetration)
            # TD movement charges its actual prefix once even when a shot downs it.
            if preserve_target_ap:
                state.actors[target_id].ap = before_ap
            contacts.append({"target": resolved.to_dict()})
            break
        item = geometry.get(identity)
        if item is None:
            raise ValueError("lane shot encountered an unsupported intervening entity: " + identity)
        metadata = item["metadata"]
        if "material_resistance" not in metadata:
            contacts.append({"blocker": identity, "route": "STOP"})
            break
        resistance = metadata["material_resistance"]
        routed = resolve_material(damage, penetration, material, resistance)
        cover = state.covers.get(metadata.get("lab_cover_id", identity))
        if cover is None:
            raise ValueError("host cover must have a bound lab durability object: " + identity)
        if cover.cover_hp is not None:
            cover.cover_hp = max(0, cover.cover_hp - max(0, damage - resistance))
            if cover.cover_hp == 0:
                cover.active = False
        contacts.append({"cover": identity, "route": routed.to_dict(), "cover_hp": cover.cover_hp})
        if routed.outcome == "STOP":
            break
        damage, penetration, material = (
            routed.remaining_damage,
            routed.remaining_target_pen,
            routed.remaining_material_pen,
        )
    return contacts


def move(
    engine: EventEngine,
    grid: GridMap,
    actor_id: str,
    path: list[Position],
    resolve: Callable[[PreparedAction], Any],
    order=(),
    legacy_provider=None,
):
    """Pause at the first physical exposure, then re-query after every response window."""
    state, responded, windows, visited = engine.state, set(), [], []
    actor = state.actors[actor_id]
    remaining = list(path)
    while remaining and actor.active and not actor.downed:
        start = actor.position
        route = [point(actor, p) for p in [start, *remaining]]
        events = []
        payload = scene(state, grid)
        host = SpatialEngine(payload)
        for prepared in state.prepared.values():
            if (
                not prepared.spatial_zone
                or prepared.owner_id in responded
                or not valid(state, prepared)
            ):
                continue
            owner = state.actors[prepared.owner_id]
            if owner.side is actor.side:
                continue
            observer = muzzle(owner)
            result = host.query(
                {
                    "kind": "exposure_path",
                    "map_id": grid.map_id,
                    "observer": {"point": observer},
                    "target": {"entity_id": actor_id},
                    "samples": {"horizontal": 3, "vertical": 3},
                    "include": ["geometry", "entities"],
                    "path": route,
                    "zone_id": prepared.spatial_zone["id"],
                    "max_tests": 4096,
                }
            ).as_payload()
            if not result["supported"]:
                raise ValueError(
                    "shared host cannot resolve this exposure path: " + result["reason"]
                )
            if result["found"]:
                events.append((result["distance"], result["point"], prepared))
        legacy = legacy_provider is not None and (
            bool(state.suppression)
            or any(p.active and p.spatial_zone is None for p in state.prepared.values())
        )
        endpoint_distance = start.distance_to(remaining[0]) if legacy else math.inf
        if not events and not legacy:
            for p in remaining:
                actor.position = p
                visited.append({"x": p.x, "y": p.y})
            break
        distance, physical = min(((e[0], e[1]) for e in events), default=(math.inf, None))
        if endpoint_distance < distance:
            distance, physical = endpoint_distance, route[1]
        actor.position = Position(physical[0] - 0.5, physical[1] - 0.5)
        visited.append({"x": actor.position.x, "y": actor.position.y})
        effects, reactions = [], []

        def respond(prepared):
            result = resolve(prepared)
            reactions.append(
                {
                    "owner": prepared.owner_id,
                    "kind": "LANE" if prepared.lane_zone else "LEGACY",
                    "point": point(actor),
                    "result": result,
                }
            )
            return None

        for d, _, prepared in events:
            if not math.isclose(d, distance, abs_tol=1e-9):
                continue
            owner = state.actors[prepared.owner_id]
            effects.append(
                TriggerEffect(
                    "LANE:" + owner.actor_id,
                    owner.actor_id,
                    owner.side,
                    prepared.explicit_before,
                    lambda s, p=prepared: (
                        valid(s, p)
                        and s.actors[actor_id].active
                        and not s.actors[actor_id].downed
                        and contains(s, grid, p, actor_id)
                        and exposure(s, grid, p.owner_id, actor_id) > 0
                    ),
                    lambda s, p=prepared: respond(p),
                )
            )
            responded.add(owner.actor_id)
        if legacy and math.isclose(endpoint_distance, distance, abs_tol=1e-9):
            effects.extend(legacy_provider())
        log = engine.resolve_trigger_window(effects, order=order, require_choice=True)
        if effects:
            windows.append(
                {
                    "position": {"x": actor.position.x, "y": actor.position.y},
                    "effects": log,
                    "reactions": reactions,
                }
            )
        # Remove only the already traversed path prefix; a boundary lies within a segment.
        traversed = 0.0
        previous = start
        for index, p in enumerate(remaining):
            traversed += previous.distance_to(p)
            if traversed > distance + 1e-9:
                remaining = remaining[index:]
                break
            previous = p
        else:
            remaining = []
    return {
        "visited": visited,
        "trigger_windows": windows,
        "continued": actor.active and not actor.downed,
        "stopped_reason": "DOWNED" if actor.downed else "INACTIVE" if not actor.active else None,
    }
