"""Toolset constants and AgentMode → toolset mapping.

Defines the canonical ToolsetName string constants and the
MODE_TOOLSETS mapping that gates which tools appear in which
AgentRunner mode.

NOTE: MODE_TOOLSETS uses string keys (matching AgentMode values)
to avoid circular imports with agent/_runner.py. Use resolve_mode_toolsets()
to convert an AgentMode enum to its toolset list.
"""

from __future__ import annotations

from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from sylvia_gm.agent._mode import AgentMode


class ToolsetName:
    """String constants for toolset identifiers."""

    DM_TOOLS = "dm_tools"             # Ruleset-defined tools (dice, harm, rest, etc.)
    CAMPAIGN_TOOLS = "campaign_tools"  # DM campaign-file tools (read_act, read_campaign_file, etc.)
    BUILDER_TOOLS = "builder_tools"   # File I/O (read_file, write_file, search_files, delete_file)
    INTERVIEW_TOOLS = "interview_tools"  # Widget question tools (ask_question only)
    COMMON_TOOLS = "common_tools"     # Future: tools available in ALL modes (e.g., help, status)


# Mode → Toolset mapping (single source of truth for gating).
# Uses string keys to avoid circular imports. Values match AgentMode enum values.
_MODE_TOOLSETS: dict[str, list[str]] = {
    "dm_session":          [ToolsetName.DM_TOOLS, ToolsetName.CAMPAIGN_TOOLS],
    "builder_freeform":    [ToolsetName.BUILDER_TOOLS, ToolsetName.INTERVIEW_TOOLS],
    "builder_interview":   [ToolsetName.INTERVIEW_TOOLS],  # ask_question only, no file I/O
}


def resolve_mode_toolsets(mode: "AgentMode") -> list[str]:
    """Return the enabled toolset names for a given AgentMode.

    Args:
        mode: An AgentMode enum value (str subclass).

    Returns:
        List of toolset name strings enabled for this mode.
    """
    return _MODE_TOOLSETS.get(mode.value, [])


# Re-export for backward compatibility — callers should prefer resolve_mode_toolsets().
# This is a deprecated alias. Use resolve_mode_toolsets(mode) instead.
MODE_TOOLSETS: dict[Any, list[str]] = {}  # type: ignore[assignment]
# MODE_TOOLSETS is populated lazily via the _lazy_init() below to avoid
# importing AgentMode at module level. Access via resolve_mode_toolsets().
