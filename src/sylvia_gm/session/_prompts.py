"""Compatibility wrapper for the legacy PromptAssembler interface.

Delegates to PromptAssembler for the actual tiered prompt construction.
This module provides backward-compatible entry points for callers that
don't have direct access to the assembler.

Current desktop GM context is assembled through agent._provider_session;
Builder context is assembled through agent._builder_session. This wrapper
does not select the prompt architecture for new desktop work.
"""

from __future__ import annotations

from typing import Any

from sylvia_gm.provider._base import ModelInfo
from sylvia_gm.session._prompt_assembler import PromptAssembler


def build_system_prompt(
    ruleset_meta: dict[str, Any],
    active_abilities: list[str],
    features: dict[str, Any],
    context: dict[str, Any],
    *,
    model_info: ModelInfo | None = None,
    tool_names: list[str] | None = None,
) -> str:
    """Build a legacy DM prompt using PromptAssembler.

    This is a backward-compatible wrapper that creates a temporary
    PromptAssembler instance. For repeated use within this legacy interface,
    create a single PromptAssembler and call ``assemble()`` directly
    to benefit from tier caching.

    Args:
        ruleset_meta: Ruleset meta dict (name, version, author, license).
        active_abilities: List of active ability keys.
        features: Ruleset feature toggles dict.
        context: Campaign context dict (from SessionRunner._build_context()).
        model_info: Optional ModelInfo for budget calculation.
        tool_names: Optional tool name strings for context awareness.

    Returns:
        A formatted system prompt string (STABLE + CONTEXT tiers only,
        no CHARACTER or HISTORY since this wrapper is stateless).
    """
    mi = model_info or ModelInfo(
        id="default", name="Default", provider_name="Unknown",
        provider_key="unknown", provider_mode="byok",
    )

    # Build a minimal ruleset-like object for the assembler
    class _MinimalRuleset:
        pass

    ruleset = _MinimalRuleset()
    ruleset.meta = type("Meta", (), {"model_dump": lambda: ruleset_meta})()
    ruleset.active_abilities = lambda: active_abilities
    ruleset.features = type("Features", (), {"model_dump": lambda: features})()

    assembler = PromptAssembler(mi, ruleset)

    # Use the assembler's STABLE tier directly (already built at init)
    context_text = assembler.build_context(context)

    result = assembler._stable
    if tool_names:
        result += f"\n\nAvailable tools: {', '.join(tool_names)}"
    if context_text:
        result += f"\n\n{context_text}"

    return result
