"""Simulation Strictness — ruleset-defined enforcement levels per subsystem.

Provides:
    StrictnessLevel   — enum-like constants for enforcement strength
    StrictnessManager — validates LLM actions against ruleset strictness config
    parse_strictness  — parse ruleset YAML ``simulation_strictness`` section

The manager is stored in ``ctx.extras["strictness"]`` and provides both
pre-action validation (should this tool be allowed?) and post-action
validation (did the result violate any enforced rules?).

Levels:
    enforced  — engine rejects violations entirely (success=False + error)
    validated — engine validates and warns but allows (logs warning)
    advisory  — engine suggests but doesn't enforce (LLM tracks loosely)
    narrative — no enforcement at all (pure LLM narration)

Subsystem names are any string the ruleset author declares. Common ones:
    turn_order, resource_tracking, position_tracking, condition_tracking,
    tool_availability, range_validation, line_of_sight.
"""

from __future__ import annotations

import enum
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


# ═══════════════════════════════════════════════════════════════════════════
# StrictnessLevel
# ═══════════════════════════════════════════════════════════════════════════


class StrictnessLevel(enum.Enum):
    """Enforcement strength for a simulation subsystem.

    Values are ordered weakest → strongest for comparison:

        >>> StrictnessLevel.ADVISORY < StrictnessLevel.ENFORCED
        True
    """

    NARRATIVE = "narrative"   # No enforcement — pure LLM narration
    ADVISORY = "advisory"     # Engine suggests but doesn't enforce
    VALIDATED = "validated"   # Engine validates, warns, but allows
    ENFORCED = "enforced"     # Engine rejects violations entirely

    def __lt__(self, other: StrictnessLevel) -> bool:
        if not isinstance(other, StrictnessLevel):
            return NotImplemented
        _order = {
            StrictnessLevel.NARRATIVE: 0,
            StrictnessLevel.ADVISORY: 1,
            StrictnessLevel.VALIDATED: 2,
            StrictnessLevel.ENFORCED: 3,
        }
        return _order[self] < _order[other]

    def __le__(self, other: StrictnessLevel) -> bool:
        return self < other or self == other

    def __gt__(self, other: StrictnessLevel) -> bool:
        return not self <= other

    def __ge__(self, other: StrictnessLevel) -> bool:
        return not self < other

    def __hash__(self) -> int:
        return hash(self.value)


# ── String helpers for easy construction ────────────────────────────────

_LEVEL_MAP: dict[str, StrictnessLevel] = {
    "narrative": StrictnessLevel.NARRATIVE,
    "advisory": StrictnessLevel.ADVISORY,
    "validated": StrictnessLevel.VALIDATED,
    "enforced": StrictnessLevel.ENFORCED,
}


def strictness_level_from_string(value: str) -> StrictnessLevel:
    """Convert a string to a StrictnessLevel.

    Args:
        value: One of ``"narrative"``, ``"advisory"``, ``"validated"``,
               ``"enforced"`` (case-insensitive).

    Returns:
        The corresponding StrictnessLevel.

    Raises:
        ValueError: If *value* is not a recognised level.
    """
    lowered = value.strip().lower()
    if lowered not in _LEVEL_MAP:
        valid = ", ".join(sorted(_LEVEL_MAP))
        raise ValueError(
            f"Unknown strictness level {value!r}. Must be one of: {valid}"
        )
    return _LEVEL_MAP[lowered]


# ═══════════════════════════════════════════════════════════════════════════
# StrictnessManager
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class StrictnessManager:
    """Reads ruleset config and validates LLM actions against strictness.

    Stored in ``ctx.extras["strictness"]`` after initialisation.

    Provides:

    * ``check_action(subsystem, tool_name)`` — pre-action gate.
    * ``check_result(subsystem, tool_name, success)`` — post-action gate.

    Behaviour by level:

    ========== ======================================================
    Level       Behaviour
    ========== ======================================================
    enforced    Pre: blocks wrong-turn-order tools (success=False).
                Post: blocks if result is failure and subsystem is
                critical (success=False + error).
    validated   Pre: warns if tool seems out of place (adds log).
                Post: warns if result failed (adds log).
    advisory    Pre: adds hint log but never blocks.
                Post: adds hint log but never blocks.
    narrative   No enforcement at all — passes everything through.
    ========== ======================================================

    Usage::

        # 1. Parse from ruleset YAML
        mgr = parse_strictness(ruleset_dict.get("simulation_strictness", {}))

        # 2. Store in ctx.extras
        ctx.extras["strictness"] = mgr

        # 3. Pre-action check (before tool execution)
        result = mgr.check_action("turn_order", "attack")
        if not result.success:
            return result  # blocked by enforced strictness

        # 4. Post-action check (after tool execution)
        result = mgr.check_result("resource_tracking", "use_item", actual_result)
        # Warnings/logs are appended to actual_result.logs
    """

    # Subsystem → strictness level mapping
    levels: dict[str, StrictnessLevel] = field(default_factory=dict)

    # ── Query ──────────────────────────────────────────────────────────

    def get_level(self, subsystem: str) -> StrictnessLevel:
        """Return the strictness level for a subsystem.

        Defaults to ``NARRATIVE`` (no enforcement) if not declared.
        """
        return self.levels.get(subsystem, StrictnessLevel.NARRATIVE)

    def set_level(self, subsystem: str, level: StrictnessLevel | str) -> None:
        """Set the strictness level for a subsystem.

        Args:
            subsystem: Subsystem name (e.g. ``"turn_order"``).
            level: A ``StrictnessLevel`` or string name.
        """
        if isinstance(level, str):
            level = strictness_level_from_string(level)
        self.levels[subsystem] = level

    @property
    def declared_subsystems(self) -> set[str]:
        """All subsystems with an explicitly declared level."""
        return set(self.levels.keys())

    # ── Pre-action validation ──────────────────────────────────────────

    def check_action(
        self,
        subsystem: str,
        tool_name: str,
        *,
        reason: str | None = None,
    ) -> "StrictnessCheckResult":
        """Validate that an action is appropriate for the current subsystem.

        Called **before** tool execution to gate actions.

        Args:
            subsystem: The subsystem to check against (e.g. ``"turn_order"``).
            tool_name: The tool being invoked.
            reason: Optional human-readable explanation for the violation
                    (e.g. ``"Not your turn"``).

        Returns:
            ``StrictnessCheckResult`` with ``success=True`` if the action
            should proceed, or ``success=False`` with an error if blocked.
        """
        level = self.get_level(subsystem)

        # Narrative — pass everything
        if level == StrictnessLevel.NARRATIVE:
            return StrictnessCheckResult(success=True, level=level)

        # Advisory — add hint, never block
        if level == StrictnessLevel.ADVISORY:
            msg = f"[advisory:{subsystem}] Tool '{tool_name}' was invoked."
            if reason:
                msg += f" {reason}"
            return StrictnessCheckResult(
                success=True,
                level=level,
                warnings=[msg],
            )

        # Validated — warn but allow
        if level == StrictnessLevel.VALIDATED:
            msg = f"[validated:{subsystem}] Tool '{tool_name}' may be out of sequence."
            if reason:
                msg += f" {reason}"
            return StrictnessCheckResult(
                success=True,
                level=level,
                warnings=[msg],
            )

        # Enforced — block if a reason is provided (specific violation)
        if level == StrictnessLevel.ENFORCED:
            if reason:
                # Specific violation reason → block hard
                error_msg = f"[enforced:{subsystem}] Tool '{tool_name}' is not allowed."
                error_msg += f" {reason}"
                return StrictnessCheckResult(
                    success=False,
                    level=level,
                    errors=[error_msg],
                )
            # No specific violation → allow (gate is open by default)
            return StrictnessCheckResult(success=True, level=level)

        # Fallback (shouldn't reach)
        return StrictnessCheckResult(success=True, level=level)

    # ── Post-action validation ─────────────────────────────────────────

    def check_result(
        self,
        subsystem: str,
        tool_name: str,
        success: bool,
        *,
        error: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> "StrictnessCheckResult":
        """Validate the result of a tool execution against strictness.

        Called **after** tool execution to check whether the outcome
        violates any enforced subsystem rules.

        Args:
            subsystem: The subsystem to check against.
            tool_name: The executed tool's name.
            success: Whether the tool reported success.
            error: Error message from a failed tool (if any).
            context: Optional context vars from the execution.

        Returns:
            ``StrictnessCheckResult`` with warnings/errors appropriate
            for the subsystem's level.  Even when ``success=True`` (the
            action is allowed), ``warnings`` may carry log entries.
        """
        level = self.get_level(subsystem)

        # Narrative — no post-action checks
        if level == StrictnessLevel.NARRATIVE:
            return StrictnessCheckResult(success=True, level=level)

        # Advisory — add hint about result
        if level == StrictnessLevel.ADVISORY:
            if not success:
                warn = (
                    f"[advisory:{subsystem}] Tool '{tool_name}' "
                    f"failed but continuing: {error or 'no error detail'}"
                )
            else:
                warn = f"[advisory:{subsystem}] Tool '{tool_name}' completed."
            return StrictnessCheckResult(
                success=True,
                level=level,
                warnings=[warn],
            )

        # Validated — warn on failure but allow
        if level == StrictnessLevel.VALIDATED:
            if not success:
                warn = (
                    f"[validated:{subsystem}] Tool '{tool_name}' "
                    f"failed: {error or 'no error detail'}"
                )
            else:
                warn = (
                    f"[validated:{subsystem}] Tool '{tool_name}' "
                    f"completed successfully."
                )
            return StrictnessCheckResult(
                success=True,
                level=level,
                warnings=[warn],
            )

        # Enforced — failure in an enforced subsystem is hard-blocked
        if level == StrictnessLevel.ENFORCED:
            if not success:
                err = (
                    f"[enforced:{subsystem}] Tool '{tool_name}' "
                    f"failed and subsystem is enforced: {error or 'no error detail'}"
                )
                return StrictnessCheckResult(
                    success=False,
                    level=level,
                    errors=[err],
                )
            return StrictnessCheckResult(success=True, level=level)

        return StrictnessCheckResult(success=True, level=level)

    # ── Convenience: apply warnings/logs to a ToolResult ────────────────

    def apply_check(
        self,
        result: "StrictnessCheckResult",
        tool_result: Any,  # ToolResult (avoid circular import)
    ) -> None:
        """Apply strictness check results to a ToolResult.

        Warnings are appended as log entries.  Does NOT set ``success=False``
        — the caller must handle blocking themselves.

        Args:
            result: A check result from ``check_action`` / ``check_result``.
            tool_result: The ``ToolResult`` to annotate.
        """
        for warn in result.warnings:
            tool_result.logs.append({
                "level": "warning",
                "source": "strictness",
                "message": warn,
            })


# ═══════════════════════════════════════════════════════════════════════════
# StrictnessCheckResult — lightweight result of a check
# ═══════════════════════════════════════════════════════════════════════════


@dataclass
class StrictnessCheckResult:
    """Result of a strictness pre-action or post-action check.

    Attributes:
        success: ``True`` if the action should proceed.
        level: The strictness level that was consulted.
        warnings: Log-level warning messages (advisory/validated).
        errors: Hard error messages (enforced blocks).
        blocked: ``True`` when ``success is False`` (convenience).
    """

    success: bool
    level: StrictnessLevel
    warnings: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def blocked(self) -> bool:
        """``True`` if this check blocked the action."""
        return not self.success


# ═══════════════════════════════════════════════════════════════════════════
# YAML parsing
# ═══════════════════════════════════════════════════════════════════════════


def parse_strictness(
    raw: dict[str, Any] | None,
) -> StrictnessManager:
    """Parse the ``simulation_strictness`` section from ruleset YAML.

    Expected YAML shape::

        simulation_strictness:
          turn_order: enforced
          resource_tracking: enforced
          position_tracking: advisory
          condition_tracking: validated

    Args:
        raw: The ``simulation_strictness`` dict from parsed ruleset YAML,
            or ``None`` / empty.

    Returns:
        A configured ``StrictnessManager`` ready for ``ctx.extras``.
    """
    mgr = StrictnessManager()

    if not raw or not isinstance(raw, dict):
        return mgr

    for subsystem, level_value in raw.items():
        subsystem = str(subsystem).strip()
        if not subsystem:
            continue

        try:
            level = _parse_level_value(level_value)
        except (ValueError, TypeError) as exc:
            logger.warning(
                "parse_strictness: skipping subsystem %r — %s",
                subsystem, exc,
            )
            continue

        mgr.set_level(subsystem, level)

    return mgr


def _parse_level_value(value: Any) -> StrictnessLevel:
    """Parse a single strictness level value from YAML.

    Accepts:
    - String: ``"enforced"``, ``"validated"``, ``"advisory"``, ``"narrative"``
    - Dict with a ``level`` key: ``{"level": "enforced"}`` (future extension)
    - StrictnessLevel instances are passed through.

    Raises:
        ValueError: If the value cannot be parsed.
    """
    if isinstance(value, StrictnessLevel):
        return value

    if isinstance(value, str):
        return strictness_level_from_string(value)

    if isinstance(value, dict):
        level_str = value.get("level")
        if level_str and isinstance(level_str, str):
            return strictness_level_from_string(level_str)
        raise ValueError(
            f"Dict value for strictness must have a 'level' key (got {value!r})"
        )

    raise ValueError(
        f"Expected string or dict for strictness level, got {type(value).__name__}"
    )


# ═══════════════════════════════════════════════════════════════════════════
# Run-time helpers for tool execution integration
# ═══════════════════════════════════════════════════════════════════════════

# Known tool → subsystem mappings.
# Extend this as new tools are added to rulesets.  Rulesets can also
# declare their own mappings via a ``tool_subsystem_map`` YAML key.

_DEFAULT_TOOL_SUBSYSTEM: dict[str, str] = {
    # Turn order
    "end_turn": "turn_order",
    "advance_turn": "turn_order",
    "initiative": "turn_order",
    # Resource tracking
    "use_item": "resource_tracking",
    "equip": "resource_tracking",
    "consume": "resource_tracking",
    "harm": "resource_tracking",
    "heal": "resource_tracking",
    # Position
    "move": "position_tracking",
    "travel": "position_tracking",
    # Conditions
    "apply_condition": "condition_tracking",
    "remove_condition": "condition_tracking",
    # Range
    "ranged_attack": "range_validation",
    "spell": "range_validation",
    # Visibility
    "look": "line_of_sight",
    "search": "line_of_sight",
}


def subsystem_for_tool(
    tool_name: str,
    *,
    mapping: dict[str, str] | None = None,
) -> str | None:
    """Return the subsystem a tool belongs to.

    Args:
        tool_name: The tool name to look up.
        mapping: Optional custom tool→subsystem mapping.  Falls back to
                 ``_DEFAULT_TOOL_SUBSYSTEM``.

    Returns:
        Subsystem name or ``None`` if the tool isn't mapped.
    """
    combined = dict(_DEFAULT_TOOL_SUBSYSTEM)
    if mapping:
        combined.update(mapping)
    return combined.get(tool_name)
