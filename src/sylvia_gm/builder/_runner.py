"""Backward-compat wrapper for Builder-specific test imports.

The unified AgentRunner replaces both the old SessionRunner and BuilderRunner.
BuilderRunner is a thin subclass that defaults to AgentMode.BUILDER_FREEFORM.
"""

from __future__ import annotations

from pathlib import Path

from sylvia_gm.agent._mode import AgentMode
from sylvia_gm.agent._runner import AgentLoopError, AgentRunner, AgentTurnResult
from sylvia_gm.provider._base import Provider


# Aliases for backward compat
BuilderLoopError = AgentLoopError
BuilderTurnResult = AgentTurnResult


class BuilderRunner(AgentRunner):
    """Backward-compat subclass that defaults to BUILDER_FREEFORM mode."""

    def __init__(
        self,
        provider: Provider,
        history_path: Path,
        *,
        project_root: Path | None = None,
        ruleset_name: str | None = None,
        guardrail_enabled: bool = True,
        system_prompt_override: str | None = None,
        prompt_mode: str = "append",
    ) -> None:
        # Determine mode from prompt_mode
        if prompt_mode == "replace":
            mode = AgentMode.BUILDER_INTERVIEW
        else:
            mode = AgentMode.BUILDER_FREEFORM

        super().__init__(
            mode=mode,
            provider=provider,
            history_path=history_path,
            project_root=project_root,
            ruleset_name=ruleset_name,
            guardrail_enabled=guardrail_enabled,
            system_prompt_override=system_prompt_override,
            prompt_mode=prompt_mode,
        )


__all__ = [
    "AgentLoopError",
    "AgentRunner",
    "AgentTurnResult",
    "BuilderLoopError",
    "BuilderRunner",
    "BuilderTurnResult",
]
