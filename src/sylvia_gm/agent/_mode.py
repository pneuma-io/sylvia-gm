"""AgentMode enum — the operating mode of the unified AgentRunner."""

from __future__ import annotations

from enum import Enum


class AgentMode(str, Enum):
    """The operating mode of the AgentRunner."""

    BUILDER_INTERVIEW = "builder_interview"  # rigid system prompt, WIDGET markers
    BUILDER_FREEFORM = "builder_freeform"  # file I/O tools, flexible prompt
    DM_SESSION = "dm_session"  # ruleset tools, PromptAssembler
