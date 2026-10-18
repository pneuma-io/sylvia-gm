"""Simulation Mode Architecture — ruleset-defined tool-gating with mode switching.

Provides:
    SimulationMode     — dataclass for a mode definition
    ToolGatingManager  — tracks active modes, filters available tools
    parse_simulation_modes — parse ruleset YAML simulation_modes section

Modes define which tools are available to the LLM at any given time.
Multiple modes can be active simultaneously (e.g., combat + time_management
during a timed battle).  Active modes are tracked on the ``_campaign``
entity; the manager itself is stored in ``ctx.extras["tool_gating"]``.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Default exploration tools ──────────────────────────────────────────────
# These are always available unless overridden by a ruleset.  The exploration
# mode is always active.

_DEFAULT_EXPLORATION_TOOLS: tuple[str, ...] = (
    "advance_time",
    "schedule_event",
    "set_flag",
    "clear_flag",
    "transition_scene",
    "describe",
    "narrate",
    "look",
    "search",
    "talk",
    "use_skill",
    "check_inventory",
    "rest",
    "travel",
)


# ═══════════════════════════════════════════════════════════════════════════
# SimulationMode
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class SimulationMode:
    """A ruleset-defined simulation mode with associated tools.

    Modes define which tools are available to the LLM at any given time.
    Multiple modes can be active simultaneously (e.g., combat +
    time_management during a timed battle).

    Attributes:
        name: Mode identifier (e.g. ``"combat"``, ``"time_management"``).
        trigger: Event that activates this mode.
            ``"always_active"`` — always on (activated at init).
            Other strings — activate when that trigger fires via
            :meth:`ToolGatingManager.on_trigger`.
        deactivate_trigger: Optional event that deactivates this mode.
            ``None`` — the mode stays active until explicitly deactivated.
            Set to e.g. ``"combat_ended"`` to auto-deactivate combat mode.
        tools: List of tool names available in this mode.
    """

    name: str
    trigger: str = "always_active"
    deactivate_trigger: str | None = None
    tools: list[str] = field(default_factory=list)


# ═══════════════════════════════════════════════════════════════════════════
# ToolGatingManager
# ═══════════════════════════════════════════════════════════════════════════


class ToolGatingManager:
    """Manages active simulation modes and filters available tools.

    Stored in ``ctx.extras["tool_gating"]``.  Reads mode definitions
    from ruleset YAML's ``simulation_modes`` section.  Active modes
    are persisted on the ``_campaign`` entity via StateBridge.

    The default ``"exploration"`` mode is always active unless explicitly
    removed — it provides access to all non-combat tools.

    Usage::

        tgm = ToolGatingManager(state_bridge, mode_defs)
        tgm.on_trigger("initiative_rolled")       # → activates combat
        allowed = tgm.get_allowed_tools(all_tools) # → union of active mode tools
        tgm.is_tool_allowed("attack")             # → True (combat mode)
    """

    def __init__(
        self,
        state_bridge: Any = None,
        mode_defs: dict[str, SimulationMode] | None = None,
    ) -> None:
        """Initialise the manager.

        Args:
            state_bridge: StateBridge for persistence (active modes stored
                on ``_campaign.active_modes``).
            mode_defs: Pre-parsed mode definitions.  The ``"exploration"``
                mode is auto-registered if not present.
        """
        self._state_bridge = state_bridge

        # Store mode definitions (always includes exploration)
        self._mode_defs: dict[str, SimulationMode] = dict(mode_defs or {})
        self._ensure_exploration_mode()

        # Active mode names (loaded from _campaign or initialised fresh)
        self._active_modes: set[str] = set()
        self._load()

        # Activate all always_active modes
        for mode in self._mode_defs.values():
            if mode.trigger == "always_active":
                self._active_modes.add(mode.name)

        self._save()

    # ── Persistence ────────────────────────────────────────────────────

    def _load(self) -> None:
        """Load active modes from ``_campaign.active_modes``."""
        if self._state_bridge is None:
            return
        try:
            getattr(self._state_bridge, "_ensure_campaign_entity", lambda: None)()
            modes = self._state_bridge.read_field("_campaign", "active_modes")
            if isinstance(modes, list):
                self._active_modes = set(modes)
        except Exception:
            self._active_modes = {"exploration"}

    def _save(self) -> None:
        """Persist active modes to ``_campaign.active_modes``."""
        if self._state_bridge is None:
            return
        try:
            self._state_bridge.write_field(
                "_campaign", "active_modes", sorted(self._active_modes)
            )
        except Exception:
            pass

    # ── Mode management ────────────────────────────────────────────────

    def activate_mode(self, name: str) -> bool:
        """Activate a simulation mode by name.

        Args:
            name: Mode name to activate.

        Returns:
            ``True`` if the mode was newly activated, ``False`` if it was
            already active or doesn't exist.
        """
        if name not in self._mode_defs:
            logger.debug("activate_mode: unknown mode %r", name)
            return False
        if name in self._active_modes:
            return False
        self._active_modes.add(name)
        self._save()
        logger.debug("mode_activated mode=%s", name)
        return True

    def deactivate_mode(self, name: str) -> bool:
        """Deactivate a simulation mode by name.

        The ``"exploration"`` mode cannot be deactivated.

        Args:
            name: Mode name to deactivate.

        Returns:
            ``True`` if the mode was active and is now deactivated.
        """
        if name == "exploration":
            return False
        if name not in self._active_modes:
            return False
        self._active_modes.discard(name)
        self._save()
        logger.debug("mode_deactivated mode=%s", name)
        return True

    def on_trigger(self, trigger_name: str) -> dict[str, bool]:
        """Process a trigger event and switch modes accordingly.

        For each registered mode:

        * If the mode's ``trigger`` matches *trigger_name*, activate it.
        * If the mode's ``deactivate_trigger`` matches *trigger_name*,
          deactivate it.

        ``"always_active"`` triggers are NOT re-processed — they are
        already active from initialisation.

        Args:
            trigger_name: The event that fired (e.g. ``"initiative_rolled"``,
                ``"combat_ended"``, ``"ball_snapped"``).

        Returns:
            Dict mapping mode names to their new active state (only modes
            that changed are included).
        """
        changes: dict[str, bool] = {}

        for mode in self._mode_defs.values():
            # Activate on matching trigger
            if mode.trigger != "always_active" and mode.trigger == trigger_name:
                if mode.name not in self._active_modes:
                    self._active_modes.add(mode.name)
                    changes[mode.name] = True
                    logger.debug("mode_activated mode=%s trigger=%s", mode.name, trigger_name)

            # Deactivate on matching deactivation trigger
            if (
                mode.deactivate_trigger is not None
                and mode.deactivate_trigger == trigger_name
            ):
                if mode.name in self._active_modes and mode.name != "exploration":
                    self._active_modes.discard(mode.name)
                    changes[mode.name] = False
                    logger.debug(
                        "mode_deactivated mode=%s deactivate_trigger=%s",
                        mode.name, trigger_name,
                    )

        if changes:
            self._save()

        return changes

    # ── Tool filtering ─────────────────────────────────────────────────

    def get_allowed_tools(
        self, all_tool_names: list[str] | None = None,
    ) -> set[str]:
        """Return the union of tools from all active modes.

        Args:
            all_tool_names: If provided, only return tools that exist
                in this list AND are allowed by active modes.  Useful for
                intersecting with the full tool registry.

        Returns:
            Set of allowed tool names.
        """
        allowed: set[str] = set()
        for mode_name in self._active_modes:
            mode = self._mode_defs.get(mode_name)
            if mode:
                allowed.update(mode.tools)

        if all_tool_names is not None:
            allowed &= set(all_tool_names)

        return allowed

    def is_tool_allowed(self, tool_name: str) -> bool:
        """Check if a specific tool is allowed in current active modes.

        Args:
            tool_name: The tool to check.

        Returns:
            ``True`` if *tool_name* appears in at least one active mode's
            tool list.
        """
        for mode_name in self._active_modes:
            mode = self._mode_defs.get(mode_name)
            if mode and tool_name in mode.tools:
                return True
        return False

    def filter_tool_list(self, tool_names: list[str]) -> list[str]:
        """Filter a list of tool names to only those currently allowed.

        Args:
            tool_names: Full (or partial) list of tool names to filter.

        Returns:
            Sorted list of tool names that are allowed.
        """
        allowed = self.get_allowed_tools(tool_names)
        return sorted(allowed)

    # ── Query ──────────────────────────────────────────────────────────

    @property
    def active_modes(self) -> set[str]:
        """Currently active mode names (read-only)."""
        return set(self._active_modes)

    @property
    def mode_defs(self) -> dict[str, SimulationMode]:
        """All registered mode definitions (read-only)."""
        return dict(self._mode_defs)

    def add_mode_def(self, mode: SimulationMode) -> None:
        """Register a new mode definition at runtime.

        Does NOT auto-activate the mode — call :meth:`activate_mode`
        separately.

        Args:
            mode: The mode definition to register.
        """
        self._mode_defs[mode.name] = mode

    def remove_mode_def(self, name: str) -> bool:
        """Remove a mode definition.

        The ``"exploration"`` mode cannot be removed.

        Args:
            name: Mode name to remove.

        Returns:
            ``True`` if the mode was removed.
        """
        if name == "exploration":
            return False
        if name in self._mode_defs:
            del self._mode_defs[name]
            self._active_modes.discard(name)
            self._save()
            return True
        return False

    # ── Internal ───────────────────────────────────────────────────────

    def _ensure_exploration_mode(self) -> None:
        """Ensure the default exploration mode is registered."""
        if "exploration" not in self._mode_defs:
            self._mode_defs["exploration"] = SimulationMode(
                name="exploration",
                trigger="always_active",
                tools=list(_DEFAULT_EXPLORATION_TOOLS),
            )


# ═══════════════════════════════════════════════════════════════════════════
# YAML parsing
# ═══════════════════════════════════════════════════════════════════════════


def parse_simulation_modes(
    raw: dict[str, Any] | None,
) -> dict[str, SimulationMode]:
    """Parse the ``simulation_modes`` section from ruleset YAML.

    Expected YAML shape::

        simulation_modes:
          combat:
            trigger: initiative_rolled
            deactivate_trigger: combat_ended   # optional
            tools: [resolve_action, move, end_turn, use_item]
          time_management:
            trigger: always_active
            tools: [advance_time, schedule_event]
          football:
            trigger: ball_snapped
            deactivate_trigger: play_ended
            tools: [pass_ball, tackle, score]

    Args:
        raw: The ``simulation_modes`` dict from parsed ruleset YAML,
            or ``None`` / empty.

    Returns:
        Dict of mode name → SimulationMode (may be empty).
    """
    result: dict[str, SimulationMode] = {}

    if not raw or not isinstance(raw, dict):
        return result

    for name, defn in raw.items():
        if not isinstance(defn, dict):
            logger.warning("parse_simulation_modes: skipping non-dict entry %r", name)
            continue

        trigger = defn.get("trigger", "always_active")
        if not isinstance(trigger, str):
            trigger = "always_active"

        deactivate_trigger = defn.get("deactivate_trigger")
        if deactivate_trigger is not None and not isinstance(deactivate_trigger, str):
            deactivate_trigger = None

        tools = defn.get("tools", [])
        if not isinstance(tools, list):
            tools = []
        tools = [str(t) for t in tools]

        result[name] = SimulationMode(
            name=name,
            trigger=trigger,
            deactivate_trigger=deactivate_trigger,
            tools=tools,
        )

    return result
