"""State Memo Builder — compact [STATE] block injection for LLM prompts.

Builds a human-readable state block injected at the top of every LLM turn
during both simulation AND exploration modes. The LLM always knows where it is.

Block format:
    [STATE] Location: Fortress Inn, Thornwall | Present: Old Vesper | HP: 18/21 | Gold: 45 | Day 3, Evening | Conditions: none [/STATE]

Reads from:
    - SceneManager  → current scene/location
    - StateBridge   → HP, gold, conditions
    - TimeManager   → day, time (via get_current_datestamp)
    - ExecutionContext → vars, extras, combat_state, state_bridge

Optional fields: if a field resolves to None/empty, it is omitted from the block.
"""

from __future__ import annotations

from typing import Any


class StateMemoBuilder:
    """Reads current engine state and formats a compact [STATE] block.

    The block is injected at the top of every LLM prompt so the model
    always has current context: location, present NPCs, HP, gold, time,
    and active conditions.

    Usage::

        builder = StateMemoBuilder(scene_manager, state_bridge)
        memo = builder.build(ctx, active_entity_id="sample_player")
        # → "[STATE] Location: Fortress Inn | HP: 18/21 | Gold: 45 | Day 3, Evening | Conditions: none [/STATE]"
    """

    # Recognised paths for gold/currency on the active entity.
    # Checked in order; the first that returns a non-None value wins.
    _GOLD_PATHS: tuple[str, ...] = (
        "inventory.gp",
        "resources.gold",
        "resources.gp",
        "gold",
        "gp",
    )

    def __init__(
        self,
        scene_manager: Any = None,
        state_bridge: Any = None,
    ) -> None:
        """Initialise the builder with runtime dependencies.

        Args:
            scene_manager: SceneManager instance (for current scene/location).
            state_bridge: StateBridge instance (for entity data).
        """
        self._scene_manager = scene_manager
        self._state_bridge = state_bridge

    # ── Public API ────────────────────────────────────────────────────────

    def build(
        self,
        ctx: Any = None,
        *,
        active_entity_id: str | None = None,
        extra_present: list[str] | None = None,
    ) -> str:
        """Build the [STATE] memo block from current engine state.

        Args:
            ctx: ExecutionContext with vars, extras, combat_state, state_bridge.
            active_entity_id: The primary entity to read HP / gold / conditions from.
            extra_present: Additional names to include in the Present field.

        Returns:
            A string like:
            ``[STATE] Location: ... | Present: ... | HP: 18/21 | Gold: 45 | Day 3, Evening | Conditions: none [/STATE]``
        """
        parts: list[str] = []

        # ── Location ──────────────────────────────────────────────────
        location = self._get_location(ctx)
        if location:
            parts.append(f"Location: {location}")

        # ── Present ───────────────────────────────────────────────────
        present = self._get_present(ctx, extra_present)
        if present:
            parts.append(f"Present: {present}")

        # ── HP ────────────────────────────────────────────────────────
        hp = self._get_hp(active_entity_id)
        if hp:
            parts.append(f"HP: {hp}")

        # ── Gold ──────────────────────────────────────────────────────
        gold = self._get_gold(active_entity_id)
        if gold is not None:
            parts.append(f"Gold: {gold}")

        # ── Time ──────────────────────────────────────────────────────
        time_str = self._get_time(ctx)
        if time_str:
            parts.append(time_str)

        # ── Conditions ────────────────────────────────────────────────
        conditions = self._get_conditions(active_entity_id)
        if conditions is not None:
            parts.append(f"Conditions: {conditions}")

        # ── Assemble ──────────────────────────────────────────────────
        if not parts:
            return "[STATE] [/STATE]"

        return f"[STATE] {' | '.join(parts)} [/STATE]"

    # ── Field helpers ─────────────────────────────────────────────────────

    def _get_location(self, ctx: Any) -> str:
        """Resolve the current location string.

        Priority:
        1. ctx.vars["_current_scene_name"] + ctx.vars["_current_scene"]
        2. SceneManager.current_scene.name
        3. SceneManager.current_scene_id
        4. ctx.vars["_current_scene"] (fallback)
        """
        # ── ctx.vars (set by SceneManager.transition_to) ──────────────
        if ctx is not None:
            scene_name = ctx.vars.get("_current_scene_name")
            scene_id = ctx.vars.get("_current_scene")
            if scene_name and scene_id:
                return f"{scene_name}, {scene_id}"
            if scene_name:
                return scene_name

        # ── SceneManager ──────────────────────────────────────────────
        if self._scene_manager is not None:
            scene = getattr(self._scene_manager, "current_scene", None)
            if scene is not None:
                name = getattr(scene, "name", "") or ""
                sid = getattr(scene, "scene_id", "") or ""
                if name and sid:
                    return f"{name}, {sid}"
                if name:
                    return name
            # Fall back to scene_id
            sid = getattr(self._scene_manager, "current_scene_id", None)
            if sid:
                return sid

        # ── ctx.vars fallback ─────────────────────────────────────────
        if ctx is not None:
            sid = ctx.vars.get("_current_scene")
            if sid:
                return sid

        return ""

    def _get_present(
        self,
        ctx: Any,
        extra_present: list[str] | None,
    ) -> str:
        """Resolve the 'Present' field: NPCs currently in the scene.

        Reads ``npcs_present`` from the current Scene object.  Extra names
        (e.g. party members) can be injected via *extra_present*.
        """
        names: list[str] = list(extra_present) if extra_present else []

        # ── From SceneManager ─────────────────────────────────────────
        if self._scene_manager is not None:
            scene = getattr(self._scene_manager, "current_scene", None)
            if scene is not None:
                npcs = getattr(scene, "npcs_present", None) or []
                for npc in npcs:
                    if isinstance(npc, str) and npc not in names:
                        names.append(npc)

        # ── From ctx.vars ─────────────────────────────────────────────
        if ctx is not None:
            ctx_npcs = ctx.vars.get("_current_scene_npcs")
            if isinstance(ctx_npcs, list):
                for npc in ctx_npcs:
                    if isinstance(npc, str) and npc not in names:
                        names.append(npc)

        return ", ".join(names) if names else ""

    def _get_hp(self, entity_id: str | None) -> str:
        """Return 'current/max' HP string for *entity_id*, or empty."""
        if not entity_id or self._state_bridge is None:
            return ""

        try:
            hp = self._state_bridge.read_field(entity_id, "hp")
            max_hp = self._state_bridge.read_field(entity_id, "max_hp")
        except Exception:
            return ""

        if hp is None:
            return ""

        if max_hp is not None and max_hp > 0:
            return f"{hp}/{max_hp}"
        return str(hp)

    def _get_gold(self, entity_id: str | None) -> int | None:
        """Return gold amount for *entity_id*, or None if not found."""
        if not entity_id or self._state_bridge is None:
            return None

        for path in self._GOLD_PATHS:
            try:
                value = self._state_bridge.read_field(entity_id, path)
                if value is not None:
                    return int(value)
            except Exception:
                continue

        return None

    def _get_time(self, ctx: Any) -> str:
        """Resolve the current time string.

        Priority:
        1. ctx.vars["_current_time"] (set by advance_time)
        2. get_current_datestamp via state_bridge + combat_state
        """
        # ── ctx.vars (State Memo injection from advance_time) ─────────
        if ctx is not None:
            time_str = ctx.vars.get("_current_time")
            if time_str:
                return time_str

        # ── get_current_datestamp ─────────────────────────────────────
        bridge = self._state_bridge
        if ctx is not None:
            bridge = getattr(ctx, "state_bridge", None) or bridge
        combat_state = None
        if ctx is not None:
            combat_state = getattr(ctx, "combat_state", None)

        if bridge is not None:
            try:
                from sylvia_gm.runtime._time_manager import get_current_datestamp

                stamp = get_current_datestamp(bridge, combat_state)
                if stamp and stamp != "Outside of time":
                    return stamp
            except Exception:
                pass

        return ""

    def _get_conditions(self, entity_id: str | None) -> str | None:
        """Return conditions string for *entity_id*, or None if no entity.

        Returns:
            None  — field not found (omit from memo).
            \"none\" — field exists but is empty (no active conditions).
            str   — comma-separated condition names.
        """
        if not entity_id or self._state_bridge is None:
            return None

        try:
            conditions = self._state_bridge.read_field(entity_id, "conditions")
        except Exception:
            return None

        # Field not present → omit
        if conditions is None:
            return None

        # Empty list → no active conditions
        if isinstance(conditions, list):
            if not conditions:
                return "none"
            return ", ".join(str(c) for c in conditions)

        return str(conditions)
