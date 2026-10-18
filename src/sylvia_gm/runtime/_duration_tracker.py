"""Effect duration tracking with auto-expiry.

DurationTracker is stored in ctx.extras["duration_tracker"] and
tracks active effects with expiration conditions. When effects expire,
it triggers on_effect_expired procedures via ctx.procedure_executor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DurationEntry:
    """A single tracked duration entry.

    Attributes:
        effect_id: Unique identifier for this effect instance.
        source: Source tool/procedure that created the effect.
        target_entity_id: Entity the effect is applied to.
        effect_name: Name of the effect (e.g., "blinded", "blessed").
        rounds_remaining: How many rounds before the effect expires.
        rounds_total: Original duration for reference.
        metadata: Arbitrary extra data.
    """

    effect_id: str
    source: str = ""
    target_entity_id: str = ""
    effect_name: str = ""
    rounds_remaining: int = 0
    rounds_total: int = 0
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def expired(self) -> bool:
        """True if rounds_remaining has reached zero or below."""
        return self.rounds_remaining <= 0


class DurationTracker:
    """Tracks effect durations and handles auto-expiry.

    Stored in ctx.extras["duration_tracker"].  The ``tick()`` method is
    called from ``advance_time`` or initiative turn advancement to
    decrement all active durations and fire expiry procedures.

    Usage::

        tracker = DurationTracker()
        tracker.add("fx_001", "blinded", "goblin_1", 3)
        tracker.tick(ctx)  # decrements all, fires on_effect_expired
    """

    def __init__(self, state_bridge: Any = None) -> None:
        """Initialize the duration tracker.

        Args:
            state_bridge: Optional StateBridge for reading entity data
                (used to verify entities still exist during tick).
        """
        self._state_bridge = state_bridge
        self._entries: dict[str, DurationEntry] = {}

    # ── public API ────────────────────────────────────────────────────

    def add(
        self,
        effect_id: str,
        effect_name: str,
        target_entity_id: str,
        rounds: int,
        *,
        source: str = "",
        metadata: dict[str, Any] | None = None,
    ) -> DurationEntry:
        """Register a new duration-tracked effect.

        Args:
            effect_id: Unique ID for this effect instance.
            effect_name: Name (e.g. "blinded", "blessed").
            target_entity_id: Entity this effect applies to.
            rounds: Number of rounds the effect lasts.
            source: Tool/procedure that created this effect.
            metadata: Arbitrary extra data.

        Returns:
            The created DurationEntry.
        """
        entry = DurationEntry(
            effect_id=effect_id,
            source=source,
            target_entity_id=target_entity_id,
            effect_name=effect_name,
            rounds_remaining=rounds,
            rounds_total=rounds,
            metadata=dict(metadata or {}),
        )
        self._entries[effect_id] = entry
        return entry

    def remove(self, effect_id: str) -> DurationEntry | None:
        """Remove a tracked effect by ID.

        Args:
            effect_id: The effect ID to remove.

        Returns:
            The removed entry, or None if not found.
        """
        return self._entries.pop(effect_id, None)

    def get(self, effect_id: str) -> DurationEntry | None:
        """Get a tracked effect without removing it."""
        return self._entries.get(effect_id)

    def get_all_for_entity(self, entity_id: str) -> list[DurationEntry]:
        """Return all active effects for a given entity."""
        return [
            e for e in self._entries.values()
            if e.target_entity_id == entity_id and not e.expired
        ]

    def get_all_active(self) -> list[DurationEntry]:
        """Return all non-expired entries."""
        return [e for e in self._entries.values() if not e.expired]

    def get_all(self) -> list[DurationEntry]:
        """Return *all* entries (including already-expired)."""
        return list(self._entries.values())

    def tick(
        self,
        ctx: Any = None,
        *,
        rounds: int = 1,
    ) -> list[DurationEntry]:
        """Advance all active durations by one or more rounds.

        Effects that expire during this tick are fired via
        ``ctx.procedure_executor("on_effect_expired", ctx)``.

        Args:
            ctx: ExecutionContext for procedure triggering.
            rounds: Number of rounds to decrement (default 1).

        Returns:
            List of entries that expired during this tick.
        """
        expired: list[DurationEntry] = []

        for entry in self._entries.values():
            if entry.expired:
                continue
            entry.rounds_remaining -= rounds
            if entry.expired:
                expired.append(entry)

        # Fire expiry procedures
        if expired and ctx is not None and ctx.procedure_executor:
            for entry in expired:
                # Inject expiry context so the procedure knows what expired
                ctx.vars["_expired_effect_id"] = entry.effect_id
                ctx.vars["_expired_effect_name"] = entry.effect_name
                ctx.vars["_expired_target_entity"] = entry.target_entity_id
                try:
                    ctx.procedure_executor("on_effect_expired", ctx)
                except Exception:
                    pass

        # Clean up fully-expired entries
        for entry in expired:
            self._entries.pop(entry.effect_id, None)

        return expired

    def clear(self) -> None:
        """Remove all tracked durations."""
        self._entries.clear()

    def __bool__(self) -> bool:
        return True  # Always truthy — use __len__ for emptiness check

    def __len__(self) -> int:
        return len([e for e in self._entries.values() if not e.expired])

    def __repr__(self) -> str:
        active = sum(1 for e in self._entries.values() if not e.expired)
        return f"DurationTracker(active={active}, total={len(self._entries)})"
