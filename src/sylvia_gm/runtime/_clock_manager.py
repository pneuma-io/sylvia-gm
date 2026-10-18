"""ClockManager — progress/countdown tracks with threshold triggers.

Clocks are progress/countdown tracks (inspired by Forged in the Dark).
Each clock has a configurable number of segments. When segments are
filled (via ``tick()``), threshold triggers fire procedures via
``ctx.procedure_executor``.

Clocks are persisted on the ``_campaign`` entity via StateBridge
(LOCK-4).  ClockManager is stored in ``ctx.extras["clock_manager"]``
(LOCK-3).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class Clock:
    """A progress / countdown track with threshold triggers.

    Attributes:
        clock_id: Unique identifier for this clock.
        name: Human-readable name (e.g. "Doom Clock").
        max_segments: Total number of segments (e.g. 4, 6, 8).
        filled_segments: Currently filled segments.
        thresholds: Mapping of ``filled_segments`` → ``procedure_name``.
            When ``tick()`` crosses a threshold, the named procedure fires.
        metadata: Arbitrary extra data (source, description, etc.).

    Properties:
        is_full: True when filled_segments >= max_segments.
        remaining: How many unfilled segments remain.
    """

    clock_id: str
    name: str
    max_segments: int = 4
    filled_segments: int = 0
    thresholds: dict[int, str] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def is_full(self) -> bool:
        """True when filled_segments has reached or exceeded max_segments."""
        return self.filled_segments >= self.max_segments

    @property
    def remaining(self) -> int:
        """Number of unfilled segments (never negative)."""
        return max(0, self.max_segments - self.filled_segments)


class ClockManager:
    """Manages progress / countdown clocks with threshold triggers.

    Stored in ``ctx.extras["clock_manager"]``.  Clocks persist on the
    ``_campaign`` entity via ``StateBridge``.

    Usage::

        mgr = ClockManager(state_bridge)
        mgr.create_clock("doom", "Doom Clock", max_segments=6,
                         thresholds={4: "on_doom_impending",
                                     6: "on_doom_arrives"})
        triggered = mgr.tick("doom", segments=2, ctx=ctx)
        # → ["on_doom_impending"] if threshold crossed
    """

    def __init__(self, state_bridge: Any = None) -> None:
        """Initialise the clock manager.

        Args:
            state_bridge: ``StateBridge`` instance for persistence.
                If ``None``, clocks live in memory only.
        """
        self._state_bridge = state_bridge
        self._clocks: dict[str, Clock] = {}
        self._load()

    # ── persistence ───────────────────────────────────────────────────

    def _load(self) -> None:
        """Load clocks from the ``_campaign`` entity."""
        if self._state_bridge is None:
            return
        try:
            self._state_bridge._ensure_campaign_entity()
            clocks_data = (
                self._state_bridge.read_field("_campaign", "clocks") or {}
            )
            for cid, data in clocks_data.items():
                if not isinstance(data, dict):
                    continue
                self._clocks[cid] = Clock(
                    clock_id=cid,
                    name=data.get("name", cid),
                    max_segments=data.get("max_segments", 4),
                    filled_segments=data.get("filled_segments", 0),
                    thresholds={
                        int(k): str(v)
                        for k, v in data.get("thresholds", {}).items()
                    },
                    metadata=data.get("metadata", {}),
                )
        except Exception:
            self._clocks = {}

    def _save(self) -> None:
        """Persist all clocks to the ``_campaign`` entity."""
        if self._state_bridge is None:
            return
        try:
            payload: dict[str, dict[str, Any]] = {}
            for cid, clock in self._clocks.items():
                payload[cid] = {
                    "name": clock.name,
                    "max_segments": clock.max_segments,
                    "filled_segments": clock.filled_segments,
                    "thresholds": {
                        str(k): v for k, v in clock.thresholds.items()
                    },
                    "metadata": clock.metadata,
                }
            self._state_bridge.write_field("_campaign", "clocks", payload)
        except Exception:
            pass

    # ── public API ────────────────────────────────────────────────────

    def create_clock(
        self,
        clock_id: str,
        name: str,
        max_segments: int = 4,
        thresholds: dict[int, str] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> Clock:
        """Create (or overwrite) a clock.

        Args:
            clock_id: Unique identifier.
            name: Human-readable name.
            max_segments: Total segments (≥ 1).
            thresholds: ``{filled_segments: procedure_name}`` map.
            metadata: Arbitrary extra data.

        Returns:
            The newly created ``Clock``.
        """
        if max_segments < 1:
            max_segments = 1

        clock = Clock(
            clock_id=clock_id,
            name=name,
            max_segments=max_segments,
            thresholds=dict(thresholds or {}),
            metadata=dict(metadata or {}),
        )
        self._clocks[clock_id] = clock
        self._save()
        return clock

    def tick(
        self,
        clock_id: str,
        segments: int = 1,
        ctx: Any = None,
    ) -> list[str]:
        """Advance a clock by *segments*.

        When the new ``filled_segments`` crosses one or more thresholds,
        the corresponding procedures are fired via
        ``ctx.procedure_executor``.

        Args:
            clock_id: Which clock to advance.
            segments: How many segments to fill (default 1).
            ctx: ``ExecutionContext`` for procedure triggering.
                If ``None``, the clock is advanced but no procedures fire.

        Returns:
            List of procedure names that were triggered (in threshold order).
        """
        clock = self._clocks.get(clock_id)
        if clock is None:
            return []

        if segments < 0:
            segments = 0

        old_filled = clock.filled_segments
        new_filled = min(clock.max_segments, old_filled + segments)
        clock.filled_segments = new_filled
        self._save()

        triggered: list[str] = []
        if ctx is None:
            return triggered

        for threshold_segments in sorted(clock.thresholds):
            if old_filled < threshold_segments <= new_filled:
                procedure_name = clock.thresholds[threshold_segments]

                # Inject clock state so the procedure can reference it
                ctx.vars["_clock_id"] = clock_id
                ctx.vars["_clock_name"] = clock.name
                ctx.vars["_clock_filled"] = new_filled
                ctx.vars["_clock_max"] = clock.max_segments
                ctx.vars["_clock_triggered_threshold"] = threshold_segments

                if ctx.procedure_executor:
                    ctx.procedure_executor(procedure_name, ctx)
                triggered.append(procedure_name)

        return triggered

    def untick(
        self,
        clock_id: str,
        segments: int = 1,
    ) -> bool:
        """Remove segments from a clock (move it backward).

        Clamped to 0.  Does NOT fire any procedures (thresholds only
        fire on forward progress).

        Args:
            clock_id: Which clock to unwind.
            segments: How many segments to remove.

        Returns:
            ``True`` if the clock was found and modified.
        """
        clock = self._clocks.get(clock_id)
        if clock is None:
            return False

        clock.filled_segments = max(0, clock.filled_segments - segments)
        self._save()
        return True

    def get(self, clock_id: str) -> Clock | None:
        """Return a clock by ID, or ``None``."""
        return self._clocks.get(clock_id)

    def get_all(self) -> list[Clock]:
        """Return a snapshot of every clock."""
        return list(self._clocks.values())

    def reset(self, clock_id: str) -> Clock:
        """Reset a clock to 0 segments.

        Raises:
            KeyError: If the clock does not exist.
        """
        clock = self._clocks.get(clock_id)
        if clock is None:
            raise KeyError(f"Clock '{clock_id}' not found")
        clock.filled_segments = 0
        self._save()
        return clock

    def remove(self, clock_id: str) -> Clock | None:
        """Remove a clock entirely.

        Returns:
            The removed ``Clock``, or ``None`` if it didn't exist.
        """
        clock = self._clocks.pop(clock_id, None)
        if clock is not None:
            self._save()
        return clock

    def clear(self) -> None:
        """Remove every clock."""
        self._clocks.clear()
        self._save()

    # ── dunders ───────────────────────────────────────────────────────

    def __len__(self) -> int:
        return len(self._clocks)

    def __contains__(self, clock_id: str) -> bool:
        return clock_id in self._clocks

    def __repr__(self) -> str:
        return f"ClockManager(clocks={len(self._clocks)})"
