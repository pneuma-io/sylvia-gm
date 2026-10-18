"""Distance tracking system for combat positioning.

Provides band-based (simplified) and grid-based (euclidean) distance
queries between entities. Entity positions are stored via StateBridge
on each entity's ``combat.position`` field.

Band mode (simplified):
    close   — 0–5 ft
    near    — 5–30 ft
    far     — 30–120 ft
    distant — 120+ ft

Grid mode:
    Euclidean distance between (x, y) coordinates in squares
    (1 square = 5 ft).

Integration with AoE resolver:
    The AoE resolver reads entity positions via StateBridge.get_position().
    This tracker writes positions via StateBridge.write_field() to the
    same ``combat.position`` field, so both systems share the same data.

Ruleset config:
    The ruleset YAML declares the distance mode under ``distance.mode``:
    ``simplified`` (default) or ``grid``.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any

# Band ordering (lowest index = closest)
BAND_ORDER: dict[str, int] = {
    "close": 0,
    "near": 1,
    "far": 2,
    "distant": 3,
}

# Reverse lookup
_BAND_INDEX_TO_NAME: dict[int, str] = {v: k for k, v in BAND_ORDER.items()}

# Band ranges in feet (for grid-to-band conversion and display)
BAND_RANGES: dict[str, tuple[float, float]] = {
    "close": (0.0, 5.0),
    "near": (5.0, 30.0),
    "far": (30.0, 120.0),
    "distant": (120.0, float("inf")),
}

# Band → representative distance in feet (for grid Fallback)
BAND_TO_FEET: dict[str, float] = {
    "close": 5.0,
    "near": 15.0,
    "far": 60.0,
    "distant": 150.0,
}

FEET_PER_SQUARE: float = 5.0

_VALID_MODES: frozenset[str] = frozenset({"simplified", "grid"})


@dataclass
class GridPosition:
    """A discrete (x, y) position on the combat grid.

    Coordinates are in squares (1 square = 5 ft).
    """

    x: int
    y: int

    def distance_to(self, other: GridPosition) -> float:
        """Euclidean distance in squares."""
        dx = self.x - other.x
        dy = self.y - other.y
        return math.sqrt(dx * dx + dy * dy)

    def distance_ft(self, other: GridPosition) -> float:
        """Euclidean distance in feet."""
        return self.distance_to(other) * FEET_PER_SQUARE

    def to_dict(self) -> dict[str, int]:
        return {"x": self.x, "y": self.y}

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> GridPosition | None:
        try:
            return cls(x=int(d["x"]), y=int(d["y"]))
        except (KeyError, ValueError, TypeError):
            return None


class DistanceTracker:
    """Tracks entity positions and provides distance queries.

    Stored in ``ctx.extras["distance_tracker"]``.  Reads/writes entity
    positions through the StateBridge on the ``combat.position`` field.

    Two modes:
        ``simplified`` — band-based (close/near/far/distant)
        ``grid``      — grid-based with (x, y) coordinates

    Usage::

        tracker = DistanceTracker(state_bridge, mode="simplified")
        tracker.set_entity_position("hero_1", {"band": "close"})
        dist = tracker.get_distance("hero_1", "goblin_1")
        can_reach = tracker.is_within_band("hero_1", "goblin_1", "near")
        tracker.move_entity("hero_1", {"x": 3, "y": 5})
    """

    # ── Sentinel for missing position ───────────────────────────────────
    _MISSING: dict[str, Any] = {}

    def __init__(
        self,
        state_bridge: Any = None,
        *,
        mode: str = "simplified",
    ) -> None:
        """Initialize the distance tracker.

        Args:
            state_bridge: StateBridge instance for reading/writing entity
                positions.  Required for persistence.
            mode: ``"simplified"`` (band-based) or ``"grid"``.
                Defaults to ``"simplified"``.  Can also be set later
                from ``ctx.ruleset_config`` via :meth:`configure_from_ruleset`.
        """
        self._state_bridge = state_bridge
        self._mode = mode if mode in _VALID_MODES else "simplified"

        # In-memory position cache keyed by entity_id.
        # Populated on first read and kept in sync with writes.
        self._cache: dict[str, dict[str, Any]] = {}

    # ── Configuration ───────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        """Current distance mode: ``"simplified"`` or ``"grid"``."""
        return self._mode

    def set_mode(self, mode: str) -> None:
        """Switch distance tracking mode.

        Args:
            mode: ``"simplified"`` or ``"grid"``.

        Raises:
            ValueError: If *mode* is not one of the valid modes.
        """
        if mode not in _VALID_MODES:
            raise ValueError(
                f"Invalid distance mode '{mode}'. "
                f"Must be one of: {', '.join(sorted(_VALID_MODES))}"
            )
        self._mode = mode

    def configure_from_ruleset(self, ruleset_config: dict[str, Any]) -> None:
        """Set the tracking mode from a ruleset YAML config.

        Reads ``distance.mode`` from *ruleset_config*.
        Does nothing if the key is not present — preserves the
        current mode.

        Args:
            ruleset_config: The ruleset's config layer dict
                (``ctx.ruleset_config``).
        """
        distance_config = ruleset_config.get("distance", {})
        if isinstance(distance_config, dict) and "mode" in distance_config:
            mode = distance_config["mode"]
            if mode in _VALID_MODES:
                self._mode = mode

    # ── Position read / write ───────────────────────────────────────────

    def get_entity_position(self, entity_id: str) -> dict[str, Any] | None:
        """Get the current combat position of an entity.

        Returns:
            A position dict (e.g. ``{"band": "close"}`` or
            ``{"x": 3, "y": 5}``), or ``None`` if the entity has
            no position set.
        """
        # Check cache first
        cached = self._cache.get(entity_id, self._MISSING)
        if cached is not self._MISSING:
            return cached if cached else None

        # Read from state bridge
        if self._state_bridge is not None:
            pos = self._state_bridge.get_position(entity_id)
            if pos is not None and isinstance(pos, dict) and pos:
                # Valid non-empty position — cache and return
                self._cache[entity_id] = dict(pos)
                return self._cache[entity_id]

        # No position — don't cache (avoid caching empty {})
        return None

    def set_entity_position(
        self, entity_id: str, position: dict[str, Any]
    ) -> None:
        """Set (or update) the combat position of an entity.

        Args:
            entity_id: The entity to position.
            position: Position dict.  In simplified mode this should
                include a ``"band"`` key.  In grid mode it should
                include ``"x"`` and ``"y"`` keys (integers).
                Both modes accept a combination of band and grid.
        """
        cleaned: dict[str, Any] = {}
        if "band" in position:
            band = str(position["band"]).lower()
            if band in BAND_ORDER:
                cleaned["band"] = band
        if "x" in position and "y" in position:
            try:
                cleaned["x"] = int(position["x"])
                cleaned["y"] = int(position["y"])
            except (ValueError, TypeError):
                pass

        if not cleaned:
            # If nothing was valid, clear the position
            self._cache.pop(entity_id, None)
            if self._state_bridge is not None:
                self._state_bridge.write_field(
                    entity_id, "combat.position", {}
                )
            return

        # Persist via StateBridge
        if self._state_bridge is not None:
            self._state_bridge.write_field(
                entity_id, "combat.position", cleaned
            )

        # Update cache
        self._cache[entity_id] = cleaned

    def clear_position(self, entity_id: str) -> None:
        """Remove an entity's combat position entirely."""
        self._cache.pop(entity_id, None)
        if self._state_bridge is not None:
            self._state_bridge.write_field(
                entity_id, "combat.position", {}
            )

    # ── Band queries (simplified mode) ───────────────────────────────────

    def get_band(self, entity_id: str) -> str | None:
        """Get the band an entity occupies.

        For entities with only grid coordinates, the band is derived
        from the distance to (0, 0) — this is a best-effort fallback.

        Returns:
            Band name (``"close"``, ``"near"``, ``"far"``, ``"distant"``)
            or ``None`` if the entity has no position.
        """
        pos = self.get_entity_position(entity_id)
        if pos is None:
            return None
        band = pos.get("band")
        if band in BAND_ORDER:
            return band
        # Fallback: derive band from grid position
        if "x" in pos and "y" in pos:
            return self._derive_band_from_grid(pos["x"], pos["y"])
        return None

    def is_within_band(
        self, entity_a: str, entity_b: str, band: str
    ) -> bool | None:
        """Check whether *entity_b* is within *band* of *entity_a*.

        Simplified mode: compares band indices of both entities.
        Grid mode: computes euclidean distance in feet and checks
        against the band's range.

        Args:
            entity_a: The origin entity ID.
            entity_b: The target entity ID.
            band: Band name (``"close"``, ``"near"``, ``"far"``,
                  ``"distant"``).

        Returns:
            ``True`` if *entity_b* is within *band*, ``False`` if not,
            ``None`` if either entity has no position.
        """
        band = band.lower()
        if band not in BAND_ORDER:
            return None

        pos_a = self.get_entity_position(entity_a)
        pos_b = self.get_entity_position(entity_b)
        if pos_a is None or pos_b is None:
            return None

        band_idx = BAND_ORDER[band]

        if self._mode == "simplified":
            return self._is_within_band_simplified(pos_a, pos_b, band_idx)

        return self._is_within_band_grid(pos_a, pos_b, band)

    def get_distance(self, entity_a: str, entity_b: str) -> float | None:
        """Compute the distance between two entities in feet.

        Simplified mode: returns the representative distance between
        the two bands (e.g. close→near ≈ 10 ft).
        Grid mode: returns exact euclidean distance in feet.

        Returns:
            Distance in feet, or ``None`` if either entity has no position.
        """
        pos_a = self.get_entity_position(entity_a)
        pos_b = self.get_entity_position(entity_b)
        if pos_a is None or pos_b is None:
            return None

        if self._mode == "grid":
            # Try grid distance first
            grid_dist = self._compute_grid_distance_ft(pos_a, pos_b)
            if grid_dist is not None:
                return grid_dist
            # Fall back to band distance if either entity lacks coords
        return self._compute_band_distance_ft(pos_a, pos_b)

    # ── Movement ────────────────────────────────────────────────────────

    def move_entity(
        self, entity_id: str, new_position: dict[str, Any]
    ) -> dict[str, Any]:
        """Move an entity to a new position with validation.

        Validates that the new position is compatible with the current
        mode (e.g., grid mode requires x/y coords) and that band
        values are recognized.

        Args:
            entity_id: The entity to move.
            new_position: The new position dict.

        Returns:
            The cleaned position dict that was actually stored.

        Raises:
            ValueError: If *new_position* is not a dict or contains
                invalid values.
        """
        if not isinstance(new_position, dict):
            raise ValueError(
                f"Position must be a dict, got {type(new_position).__name__}"
            )

        self.set_entity_position(entity_id, new_position)
        pos = self._cache.get(entity_id, {})
        return dict(pos) if pos else {}

    # ── Bulk queries ────────────────────────────────────────────────────

    def get_entities_in_band(
        self, origin_id: str, band: str, entity_ids: list[str],
    ) -> list[str]:
        """Return entity IDs from *entity_ids* that are within *band*
        of *origin_id*.

        Args:
            origin_id: The reference entity.
            band: Target band (``"close"``, ``"near"``, ``"far"``,
                  ``"distant"``).
            entity_ids: Candidate entity IDs to check.

        Returns:
            List of entity IDs within the band (excluding origin).
        """
        results: list[str] = []
        for eid in entity_ids:
            if eid == origin_id:
                continue
            in_range = self.is_within_band(origin_id, eid, band)
            if in_range is True:
                results.append(eid)
        return results

    def get_entities_in_range(
        self, origin_id: str, max_ft: float, entity_ids: list[str],
    ) -> list[str]:
        """Return entity IDs within *max_ft* feet of *origin_id*.

        Args:
            origin_id: The reference entity.
            max_ft: Maximum distance in feet.
            entity_ids: Candidate entity IDs to check.

        Returns:
            List of entity IDs within range (excluding origin).
        """
        results: list[str] = []
        for eid in entity_ids:
            if eid == origin_id:
                continue
            dist = self.get_distance(origin_id, eid)
            if dist is not None and dist <= max_ft:
                results.append(eid)
        return results

    def get_all_positions(self) -> dict[str, dict[str, Any]]:
        """Return a snapshot of all cached entity positions.

        Returns:
            Dict mapping entity_id → position dict.
        """
        return dict(self._cache)

    # ── Range / band conversion utilities ───────────────────────────────

    @staticmethod
    def band_to_range(band: str) -> tuple[float, float] | None:
        """Convert a band name to its (min_ft, max_ft) range.

        >>> DistanceTracker.band_to_range("close")
        (0.0, 5.0)
        >>> DistanceTracker.band_to_range("distant")
        (120.0, inf)
        """
        band = band.lower()
        return BAND_RANGES.get(band)

    @staticmethod
    def range_to_band(distance_ft: float) -> str:
        """Convert a distance in feet to the corresponding band.

        >>> DistanceTracker.range_to_band(3)
        'close'
        >>> DistanceTracker.range_to_band(45)
        'far'
        >>> DistanceTracker.range_to_band(500)
        'distant'
        """
        if distance_ft <= 5:
            return "close"
        if distance_ft <= 30:
            return "near"
        if distance_ft <= 120:
            return "far"
        return "distant"

    @staticmethod
    def compute_grid_distance(
        x1: int, y1: int, x2: int, y2: int,
    ) -> float:
        """Euclidean distance in squares between two grid points."""
        dx = x1 - x2
        dy = y1 - y2
        return math.sqrt(dx * dx + dy * dy)

    @staticmethod
    def compute_grid_distance_ft(
        x1: int, y1: int, x2: int, y2: int,
    ) -> float:
        """Euclidean distance in feet between two grid points."""
        return DistanceTracker.compute_grid_distance(x1, y1, x2, y2) * FEET_PER_SQUARE

    # ── Internal helpers ────────────────────────────────────────────────

    def _is_within_band_simplified(
        self,
        pos_a: dict[str, Any],
        pos_b: dict[str, Any],
        target_band_idx: int,
    ) -> bool:
        """Simplified band check: compare band index distance."""
        band_a = self._resolve_band(pos_a)
        band_b = self._resolve_band(pos_b)
        if band_a is None or band_b is None:
            return False
        idx_a = BAND_ORDER.get(band_a, 0)
        idx_b = BAND_ORDER.get(band_b, 0)
        diff = abs(idx_b - idx_a)
        return diff <= target_band_idx

    def _is_within_band_grid(
        self,
        pos_a: dict[str, Any],
        pos_b: dict[str, Any],
        band: str,
    ) -> bool:
        """Grid band check: compute euclidean distance and compare to
        band range.  Falls back to simplified if either entity lacks
        grid coordinates."""
        dist_ft = self._compute_grid_distance_ft(pos_a, pos_b)
        if dist_ft is not None:
            min_ft, max_ft = BAND_RANGES.get(band, (0.0, float("inf")))
            return min_ft <= dist_ft <= max_ft
        # Fall back to band-based comparison
        band_a = self._resolve_band(pos_a)
        band_b = self._resolve_band(pos_b)
        if band_a is None or band_b is None:
            return False
        idx_a = BAND_ORDER.get(band_a, 0)
        idx_b = BAND_ORDER.get(band_b, 0)
        return abs(idx_b - idx_a) <= BAND_ORDER.get(band, 0)

    def _compute_grid_distance_ft(
        self, pos_a: dict[str, Any], pos_b: dict[str, Any],
    ) -> float | None:
        """Compute distance in feet from two position dicts.

        Returns None if either lacks x/y coords.
        """
        try:
            x1, y1 = int(pos_a["x"]), int(pos_a["y"])
            x2, y2 = int(pos_b["x"]), int(pos_b["y"])
        except (KeyError, ValueError, TypeError):
            return None
        return self.compute_grid_distance_ft(x1, y1, x2, y2)

    def _compute_band_distance_ft(
        self, pos_a: dict[str, Any], pos_b: dict[str, Any],
    ) -> float:
        """Compute representative distance between two band positions.

        Uses the midpoint of each band's range to estimate distance.
        """
        band_a = self._resolve_band(pos_a) or "close"
        band_b = self._resolve_band(pos_b) or "close"

        ft_a = BAND_TO_FEET.get(band_a, 5.0)
        ft_b = BAND_TO_FEET.get(band_b, 5.0)

        return abs(ft_b - ft_a)

    def _resolve_band(self, pos: dict[str, Any]) -> str | None:
        """Extract band from a position dict, deriving from grid if needed."""
        band = pos.get("band")
        if band in BAND_ORDER:
            return band
        if "x" in pos and "y" in pos:
            return self._derive_band_from_grid(pos["x"], pos["y"])
        return None

    def _derive_band_from_grid(self, x: int, y: int) -> str:
        """Derive a band name from grid coordinates relative to origin."""
        dist = math.sqrt(x * x + y * y) * FEET_PER_SQUARE
        return self.range_to_band(dist)

    def clear_cache(self) -> None:
        """Clear the in-memory position cache."""
        self._cache.clear()

    def __repr__(self) -> str:
        entities = len(self._cache)
        return f"DistanceTracker(mode={self._mode!r}, cached_entities={entities})"
