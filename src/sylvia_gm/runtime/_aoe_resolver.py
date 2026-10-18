"""Area-of-effect entity resolution.

Resolves which entities are within an AoE effect using either
band-based (Cairn/OSR) or grid-based (D&D) resolution.

Provides:
  AoESpec   — structured AoE definition
  resolve_aoe            — dispatch to band or grid resolver
  resolve_aoe_bands      — band-based resolution (close/near/far)
  resolve_aoe_grid       — grid-based resolution (x, y coords)
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

from sylvia_gm.runtime._condition_evaluator import evaluate_condition
from sylvia_gm.runtime._types import ExecutionContext


@dataclass
class AoESpec:
    """Structured definition of an area-of-effect.

    Attributes:
        shape: "sphere", "cone", "line", "cube", "cylinder" (grid)
               or "burst", "cone", "line" (band).
        size: Radius / length in feet (grid) or band reference
              "close" / "near" / "far" (band).
        origin: "self", an entity_id, or a position reference.
        filter: Optional condition expression to filter targets
                (e.g., ``"kind == 'hostile'"``).
    """

    shape: str
    size: int | str
    origin: str
    filter: str | None = None


# ── Public API ──────────────────────────────────────────────────────────────


def resolve_aoe(
    spec: AoESpec,
    ctx: ExecutionContext,
) -> list[str]:
    """Return list of entity IDs within the AoE.

    Dispatches to band-based or grid-based resolution depending on
    whether entity positions include x/y grid coordinates.
    """
    # Determine which entities to consider (all known entities)
    entity_ids = list(ctx.entities.keys())

    # Remove the origin entity itself if origin is an entity ID
    origin_id = _resolve_origin(spec.origin, ctx)
    entity_ids = [eid for eid in entity_ids if eid != origin_id]

    # Filter: only entities with a position
    positioned: list[tuple[str, dict[str, Any]]] = []
    grid_entities: list[tuple[str, int, int]] = []

    for eid in entity_ids:
        if ctx.state_bridge is None:
            continue
        pos = ctx.state_bridge.get_position(eid)
        if pos is None:
            continue
        positioned.append((eid, pos))
        if "x" in pos and "y" in pos:
            try:
                grid_entities.append((eid, int(pos["x"]), int(pos["y"])))
            except (ValueError, TypeError):
                pass

    # Use grid if we have grid positions for most entities
    if len(grid_entities) >= len(positioned) * 0.5 and len(grid_entities) >= 2:
        # Grid-based resolution
        origin_pos = _get_origin_grid(spec.origin, ctx)
        if origin_pos is None:
            return []
        ox, oy = origin_pos
        hits = resolve_aoe_grid(ox, oy, spec.shape, spec.size, grid_entities)
        return _apply_filter(hits, spec.filter, ctx)

    # Band-based resolution (default / fallback)
    origin_band, origin_id = _get_origin_band(spec.origin, ctx)
    hits = resolve_aoe_bands(origin_id, origin_band, spec.shape, spec.size, positioned)
    return _apply_filter(hits, spec.filter, ctx)


def resolve_aoe_bands(
    origin_id: str,
    origin_band: str,
    aoe_shape: str,
    aoe_size: int | str,
    positioned_entities: list[tuple[str, dict[str, Any]]],
) -> list[str]:
    """Band-based AoE resolution (Cairn/OSR style).

    Uses distance bands: close, near, far, distant.
    Only entities within the AoE's reachable bands are included.

    Args:
        origin_id: Entity ID of the origin (used to exclude from targets).
        origin_band: Current band of the origin entity (e.g., "close").
        aoe_shape: "burst", "cone", "line".
        aoe_size: Band reference string ("close", "near", "far") or
                  a numeric size interpreted as bands (1=close, 2=near, etc.).
        positioned_entities: List of (entity_id, position_dict) tuples.

    Returns:
        List of entity IDs within the AoE.
    """
    # Band ordering: close < near < far < distant
    BAND_ORDER: dict[str, int] = {"close": 0, "near": 1, "far": 2, "distant": 3}
    BAND_ORDER_REVERSE: dict[int, str] = {v: k for k, v in BAND_ORDER.items()}

    origin_idx = BAND_ORDER.get(origin_band.lower(), 0)

    # Resolve aoe_size to a band index
    if isinstance(aoe_size, (int, float)):
        size_idx = int(aoe_size)
    else:
        size_str = str(aoe_size).lower()
        if size_str in BAND_ORDER:
            size_idx = BAND_ORDER[size_str]
        else:
            size_idx = 1  # default to near

    shape = aoe_shape.lower()
    hits: list[str] = []

    for eid, pos in positioned_entities:
        if eid == origin_id:
            continue

        band_str = pos.get("band", "close").lower()
        band_idx = BAND_ORDER.get(band_str, 0)

        if shape == "burst":
            # Burst hits everything within size_idx bands
            if band_idx <= size_idx:
                hits.append(eid)
        elif shape == "cone":
            # Cone hits the next size_idx bands (in front)
            if origin_idx < band_idx <= origin_idx + size_idx:
                hits.append(eid)
        elif shape == "line":
            # Line hits the nearest size_idx entities in the same band
            # Simplified: hit any entity within size_idx bands
            if band_idx <= origin_idx + size_idx:
                hits.append(eid)
        else:
            # Unknown shape — skip
            pass

    return hits


def resolve_aoe_grid(
    origin_x: int,
    origin_y: int,
    shape: str,
    size: int | str,
    entity_positions: list[tuple[str, int, int]],
) -> list[str]:
    """Grid-based AoE resolution (D&D style).

    Uses x, y coordinates on a grid. Distances are in feet (5 ft per square).

    Args:
        origin_x: X coordinate of the origin.
        origin_y: Y coordinate of the origin.
        shape: "sphere", "cube", "cylinder", "cone", "line".
        size: Radius/side length in feet (or band reference which is
              converted: close=10, near=30, far=60, distant=120).
        entity_positions: List of (entity_id, x, y) tuples.

    Returns:
        List of entity IDs within the AoE.
    """
    # Resolve size to feet
    BAND_TO_FEET: dict[str, int] = {
        "close": 10,
        "near": 30,
        "far": 60,
        "distant": 120,
    }

    if isinstance(size, (int, float)):
        radius_ft = int(size)
    else:
        size_str = str(size).lower()
        radius_ft = BAND_TO_FEET.get(size_str, 30)

    shape = shape.lower()
    hits: list[str] = []

    for eid, ex, ey in entity_positions:
        dx = ex - origin_x
        dy = ey - origin_y
        dist = math.sqrt(dx * dx + dy * dy) * 5  # convert squares to feet

        if shape in ("sphere", "cylinder"):
            if dist <= radius_ft:
                hits.append(eid)
        elif shape == "cube":
            # Cube: half-size in each direction from origin
            half = radius_ft / 5 / 2  # convert feet to half-squares
            if abs(dx) <= half and abs(dy) <= half:
                hits.append(eid)
        elif shape == "cone":
            # Cone: within radius_ft distance AND within 45° arc
            if dist <= radius_ft:
                # Simple: check if target is "in front" (positive x from origin)
                if dx >= 0:
                    angle = abs(math.atan2(dy, dx))
                    if angle <= math.pi / 4:
                        hits.append(eid)
        elif shape == "line":
            # Line: within 5 ft of a line from origin extending radius_ft
            if dist <= radius_ft:
                # Check lateral distance from the x-axis line
                lateral = abs(dy) * 5  # convert squares to feet
                if lateral <= 5:  # 5 ft wide line
                    if dx >= 0:
                        hits.append(eid)
        else:
            # Unknown shape — skip
            pass

    return hits


# ── Internal helpers ────────────────────────────────────────────────────────


def _resolve_origin(origin: str, ctx: ExecutionContext) -> str | None:
    """Resolve the origin reference to an entity ID."""
    if origin == "self":
        # Try to find the current actor
        if "attacker" in ctx.params:
            attacker = ctx.params["attacker"]
            if isinstance(attacker, str):
                return attacker
            if isinstance(attacker, dict):
                return attacker.get("id", attacker.get("entity_id"))
        return None
    return origin


def _get_origin_grid(
    origin: str, ctx: ExecutionContext
) -> tuple[int, int] | None:
    """Get the grid position of the origin entity."""
    origin_id = _resolve_origin(origin, ctx)
    if origin_id is None or ctx.state_bridge is None:
        return None
    pos = ctx.state_bridge.get_position(origin_id)
    if pos is None:
        return None
    try:
        return int(pos["x"]), int(pos["y"])
    except (KeyError, ValueError, TypeError):
        return None


def _get_origin_band(
    origin: str, ctx: ExecutionContext
) -> tuple[str, str | None]:
    """Get the band and entity ID of the origin."""
    origin_id = _resolve_origin(origin, ctx)
    if origin_id is None or ctx.state_bridge is None:
        return "close", None
    pos = ctx.state_bridge.get_position(origin_id)
    band = pos.get("band", "close") if pos else "close"
    return band, origin_id


def _apply_filter(
    entity_ids: list[str],
    filter_expr: str | None,
    ctx: ExecutionContext,
) -> list[str]:
    """Apply a condition filter to a list of entity IDs.

    Before evaluating the filter for each entity, its fields are
    temporarily injected into ``ctx.vars`` so the filter expression
    can reference them directly (e.g., ``kind == 'hostile'``).
    """
    if not filter_expr:
        return entity_ids

    result: list[str] = []
    for eid in entity_ids:
        # Inject entity fields for filter evaluation
        entity = ctx.entities.get(eid, {})
        saved: dict[str, Any] = {}

        # Save any vars we'll overwrite
        for field, value in entity.items():
            if field in ctx.vars:
                saved[field] = ctx.vars[field]
            ctx.vars[field] = value

        # Also set _aoe_target for convenience
        if "_aoe_target" in ctx.vars:
            saved["_aoe_target"] = ctx.vars["_aoe_target"]
        ctx.vars["_aoe_target"] = eid

        try:
            if evaluate_condition(filter_expr, ctx):
                result.append(eid)
        except Exception:
            # If filter evaluation fails, skip this entity
            pass
        finally:
            # Restore overwritten vars
            for field in entity:
                if field in saved:
                    ctx.vars[field] = saved[field]
                else:
                    ctx.vars.pop(field, None)
            if "_aoe_target" in saved:
                ctx.vars["_aoe_target"] = saved["_aoe_target"]
            else:
                ctx.vars.pop("_aoe_target", None)

    return result
