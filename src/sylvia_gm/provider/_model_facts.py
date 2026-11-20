"""Documented model facts keyed by service and exact model identity.

Discovered account capabilities take precedence where the route supplies them.
API limits are never used as subscription limits.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any

from sylvia_gm.provider._base import ModelInfo


@dataclass(frozen=True)
class ModelFacts:
    efforts: tuple[str, ...] | None = None
    default_effort: str = "medium"
    forced_tool_choice: bool = False
    temperature: bool = False
    binds_thinking: bool = False
    #: Mid-conversation tool changes (``tool_addition`` / ``tool_removal``) are accepted.
    tool_changes: bool = False
    fast: bool = False
    flex: bool = False
    context_window: int | None = None
    gm_narration_hint: bool = False
    catalog: Mapping[str, Any] = field(default_factory=dict)


_CLAUDE_EFFORT = ("low", "medium", "high", "max")
_CLAUDE_EXTENDED_EFFORT = ("low", "medium", "high", "xhigh", "max")
_OPENAI_EFFORT = ("none", "low", "medium", "high", "xhigh")
_OPENAI_EXTENDED_EFFORT = (*_OPENAI_EFFORT, "max")


# Claude API effort, forced tool choice, sampling, thinking and Fast mode (2026-11-18):
# https://platform.claude.com/docs/en/build-with-claude/effort
# https://platform.claude.com/docs/en/agents-and-tools/tool-use/define-tools
# https://platform.claude.com/docs/en/api/errors
# https://platform.claude.com/docs/en/build-with-claude/extended-thinking
# https://platform.claude.com/docs/en/build-with-claude/fast-mode
# Mid-conversation tool changes (beta; models checked 2026-11-20):
# https://platform.claude.com/docs/en/build-with-claude/mid-conversation-system-messages
# Claude Code context and aliases: https://code.claude.com/docs/en/model-config (2026-11-18).
# OpenAI API effort and service tiers (catalog checked 2026-11-12):
# https://developers.openai.com/api/docs/models
# https://developers.openai.com/api/docs/pricing
# ChatGPT subscription narration hints are based on Sylvia's GM runs, not provider limits.
_MODEL_FACTS = {
    ("anthropic", "claude-fable-5"): ModelFacts(forced_tool_choice=True, tool_changes=True),
    ("anthropic", "claude-fable-5-2"): ModelFacts(
        efforts=_CLAUDE_EXTENDED_EFFORT, binds_thinking=True, fast=True, tool_changes=True
    ),
    ("anthropic", "claude-haiku-4-5"): ModelFacts(forced_tool_choice=True, temperature=True),
    ("anthropic", "claude-haiku-5-5"): ModelFacts(
        efforts=_CLAUDE_EXTENDED_EFFORT, forced_tool_choice=True, tool_changes=True
    ),
    ("anthropic", "claude-mythos-5-2"): ModelFacts(
        binds_thinking=True, fast=True, tool_changes=True
    ),
    ("anthropic", "claude-opus-4"): ModelFacts(forced_tool_choice=True, temperature=True),
    ("anthropic", "claude-opus-4-1"): ModelFacts(forced_tool_choice=True, temperature=True),
    ("anthropic", "claude-opus-4-5"): ModelFacts(forced_tool_choice=True, temperature=True),
    ("anthropic", "claude-opus-4-6"): ModelFacts(
        efforts=_CLAUDE_EFFORT, forced_tool_choice=True, temperature=True
    ),
    ("anthropic", "claude-opus-4-7"): ModelFacts(
        efforts=_CLAUDE_EXTENDED_EFFORT, forced_tool_choice=True
    ),
    ("anthropic", "claude-opus-4-8"): ModelFacts(
        efforts=_CLAUDE_EXTENDED_EFFORT, forced_tool_choice=True, tool_changes=True
    ),
    ("anthropic", "claude-opus-5"): ModelFacts(
        efforts=_CLAUDE_EXTENDED_EFFORT, forced_tool_choice=True, tool_changes=True
    ),
    # Opus 5.5 and Sonnet 5.5 bind replayed thinking on every account created from 31 August
    # 2026; a live check on 19 November 2026 found a newly made Console key enforced on both.
    ("anthropic", "claude-opus-5-5"): ModelFacts(
        efforts=_CLAUDE_EXTENDED_EFFORT, binds_thinking=True, fast=True, tool_changes=True
    ),
    ("anthropic", "claude-sonnet-4"): ModelFacts(forced_tool_choice=True, temperature=True),
    ("anthropic", "claude-sonnet-4-5"): ModelFacts(forced_tool_choice=True, temperature=True),
    ("anthropic", "claude-sonnet-4-6"): ModelFacts(
        efforts=_CLAUDE_EFFORT, forced_tool_choice=True, temperature=True
    ),
    ("anthropic", "claude-sonnet-5"): ModelFacts(
        efforts=_CLAUDE_EXTENDED_EFFORT, forced_tool_choice=True
    ),
    ("anthropic", "claude-sonnet-5-5"): ModelFacts(
        efforts=_CLAUDE_EXTENDED_EFFORT, binds_thinking=True, tool_changes=True
    ),
    ("claude_runtime", "claude-fable-5-2"): ModelFacts(context_window=1_000_000),
    ("claude_runtime", "opus"): ModelFacts(context_window=1_000_000),
    ("claude_runtime", "sonnet"): ModelFacts(context_window=1_000_000),
    ("claude_runtime", "haiku"): ModelFacts(context_window=1_000_000),
    ("claude_runtime", "fable"): ModelFacts(context_window=1_000_000),
    ("claude_runtime", "claude-haiku-5-5"): ModelFacts(context_window=1_000_000),
    ("claude_runtime", "claude-opus-5-5"): ModelFacts(context_window=1_000_000),
    ("claude_runtime", "claude-sonnet-5-5"): ModelFacts(context_window=1_000_000),
    ("openai", "gpt-4.1"): ModelFacts(fast=True),
    ("openai", "gpt-4.1-mini"): ModelFacts(fast=True),
    ("openai", "gpt-4o"): ModelFacts(fast=True),
    ("openai", "gpt-4o-mini"): ModelFacts(fast=True),
    ("openai", "gpt-5"): ModelFacts(fast=True, flex=True),
    ("openai", "gpt-5-mini"): ModelFacts(fast=True, flex=True),
    ("openai", "gpt-5-nano"): ModelFacts(flex=True),
    ("openai", "gpt-5.1"): ModelFacts(fast=True, flex=True),
    ("openai", "gpt-5.2"): ModelFacts(
        efforts=_OPENAI_EFFORT, default_effort="none", fast=True, flex=True
    ),
    ("openai", "gpt-5.4"): ModelFacts(fast=True, flex=True),
    ("openai", "gpt-5.4-mini"): ModelFacts(fast=True, flex=True),
    ("openai", "gpt-5.4-nano"): ModelFacts(flex=True),
    ("openai", "gpt-5.4-pro"): ModelFacts(flex=True),
    ("openai", "gpt-5.5"): ModelFacts(efforts=_OPENAI_EFFORT, fast=True, flex=True),
    ("openai", "gpt-5.5-pro"): ModelFacts(flex=True),
    ("openai", "gpt-5.6-luna"): ModelFacts(fast=True, flex=True),
    ("openai", "gpt-5.6-sol"): ModelFacts(efforts=_OPENAI_EXTENDED_EFFORT, fast=True, flex=True),
    ("openai", "gpt-5.6-terra"): ModelFacts(fast=True, flex=True),
    ("openai", "gpt-6-astra"): ModelFacts(fast=True, flex=True),
    ("openai", "gpt-6-luna"): ModelFacts(fast=True, flex=True),
    ("openai", "gpt-6-sol"): ModelFacts(fast=True, flex=True),
    ("openai", "gpt-6.1-sol"): ModelFacts(fast=True, flex=True),
    ("openai", "gpt-6.1-terra"): ModelFacts(fast=True, flex=True),
    ("chatgpt_subscription", "gpt-6.1-sol"): ModelFacts(gm_narration_hint=True),
    ("chatgpt_subscription", "gpt-6-sol"): ModelFacts(gm_narration_hint=True),
    ("chatgpt_subscription", "gpt-5.6-sol"): ModelFacts(gm_narration_hint=True),
}
_OPENAI_ALIASES = {"gpt-5.6": "gpt-5.6-sol", "gpt-5.5-2026-04-23": "gpt-5.5"}
_UNKNOWN = ModelFacts()


def documented_openai_id(model_id: str) -> str:
    return _OPENAI_ALIASES.get(model_id, model_id)


def openai_model_facts(model_id: str) -> ModelFacts:
    return _MODEL_FACTS.get(("openai", documented_openai_id(model_id)), _UNKNOWN)


def claude_model_id(model_id: str) -> str | None:
    bare = re.sub(r"[-@]\d{8}$", "", model_id.strip().casefold().rsplit("/", 1)[-1])
    return bare if bare.startswith("claude-") else None


def claude_model_facts(model_id: str) -> ModelFacts:
    return _MODEL_FACTS.get(("anthropic", claude_model_id(model_id) or ""), _UNKNOWN)


def claude_code_context_window(model_id: str) -> int | None:
    return _MODEL_FACTS.get(("claude_runtime", model_id), _UNKNOWN).context_window


def uses_gm_narration_hint(model: ModelInfo) -> bool:
    wire_id = model.wire_model_id
    return (
        model.id == f"chatgpt-subscription/{wire_id}"
        and _MODEL_FACTS.get((model.provider_key, wire_id), _UNKNOWN).gm_narration_hint
    )


# API ceilings only: subscription and proxy limits must come from their own routes.
# https://developers.openai.com/api/docs/models (checked 2026-09-23).
_DEFAULTS: dict[tuple[str, str], dict[str, Any]] = {
    ("openai", model_id): {
        "name": name,
        "context_window": 1_050_000,
        "max_output_tokens": 128_000,
        "transport": "responses",
        "supports_tools": True,
        "supports_streaming": True,
        "supported_reasoning_efforts": efforts,
        "default_reasoning_effort": None if model_id == "gpt-6-astra" else "medium",
        "documented_source": f"https://developers.openai.com/api/docs/models/{model_id}",
        "documented_at": "2026-09-23",
    }
    for model_id, name, efforts in (
        ("gpt-6-sol", "GPT-6 Sol", ("none", "low", "medium", "high", "xhigh", "max")),
        ("gpt-6-astra", "GPT-6 Astra", ("low", "medium", "high", "xhigh", "max")),
        ("gpt-6-luna", "GPT-6 Luna", ("none", "low", "medium", "high", "xhigh", "max")),
    )
}

# These are Groq's served limits, not the upstream open-weight model's limits.
# Streaming/tool support: https://console.groq.com/docs/tool-use/overview (2026-09-23).
for _model_id in ("openai/gpt-oss-120b", "openai/gpt-oss-20b"):
    _DEFAULTS[("groq", _model_id)] = {
        "context_window": 131_072,
        "max_output_tokens": 65_536,
        "transport": "chat_completions",
        "supports_tools": True,
        "supports_streaming": True,
        "supported_reasoning_efforts": ("low", "medium", "high"),
        "default_reasoning_effort": None,
        "documented_source": "https://console.groq.com/docs/models",
        "documented_at": "2026-09-23",
    }

# DeepSeek publishes context as "1M"; use that decimal allowance without claiming
# an undocumented larger binary ceiling. The API explicitly defines 384K as 393216.
# https://api-docs.deepseek.com/api/create-chat-completion/ (2026-09-23).
for _model_id in ("deepseek-flash", "deepseek-v4-pro"):
    _DEFAULTS[("deepseek", _model_id)] = {
        "context_window": 1_000_000,
        "max_output_tokens": 393_216,
        "transport": "chat_completions",
        "supports_tools": True,
        "supports_streaming": True,
        "supported_reasoning_efforts": ("none", "low", "high", "max"),
        "default_reasoning_effort": "high",
        "documented_source": "https://api-docs.deepseek.com/quick_start/pricing/",
        "documented_at": "2026-09-23",
    }


# OpenCode's own route-specific catalog, rather than the upstream MiniMax allowance.
_DEFAULTS[("opencode_go", "minimax-m3")] = {
    "name": "MiniMax-M3",
    "context_window": 1_000_000,
    "max_output_tokens": 131_072,
    "transport": "anthropic_messages",
    "supports_tools": True,
    "documented_source": (
        "https://github.com/anomalyco/models.dev/blob/dev/"
        "providers/opencode-go/models/minimax-m3.toml"
    ),
    "documented_at": "2026-09-28",
}


for _key, _catalog in _DEFAULTS.items():
    _MODEL_FACTS[_key] = replace(_MODEL_FACTS.get(_key, _UNKNOWN), catalog=_catalog)


def documented_model_defaults(preset_id: str, model_id: str) -> dict[str, Any]:
    """Return facts only for an exact route and ID; caller metadata takes precedence."""
    return dict(_MODEL_FACTS.get((preset_id, model_id), _UNKNOWN).catalog)


def documented_model_ids() -> tuple[tuple[str, str], ...]:
    """Expose known identifiers for manual-entry assistance, not an account model list."""
    return tuple(_DEFAULTS)


# OpenCode's documented model families can include newly discovered IDs.
_OPENCODE_ROUTES = {
    "opencode_zen": (
        ("exact", ("qwen3.8-max",), "chat_completions"),
        ("prefix", ("gemini-",), "gemini_generate_content"),
        ("prefix", ("gpt-5", "gpt-6", "grok-", "muse-spark-"), "responses"),
        ("prefix", ("claude-", "qwen3.5", "qwen3.6", "qwen3.7", "qwen3.8"), "anthropic_messages"),
    ),
    "opencode_go": (
        ("prefix", ("gemini-",), "gemini_generate_content"),
        ("prefix", ("gpt-5", "gpt-6", "grok-", "muse-spark-"), "responses"),
        (
            "prefix",
            ("claude-", "qwen3.5", "qwen3.6", "qwen3.7", "qwen3.8", "minimax-m"),
            "anthropic_messages",
        ),
    ),
}


def inferred_model_protocol(preset: str, model_id: str) -> str | None:
    """Resolve the protocol for a documented model family."""
    identity = model_id.casefold()
    for mode, identities, protocol in _OPENCODE_ROUTES.get(preset, ()):
        matches = identity in identities if mode == "exact" else identity.startswith(identities)
        if matches:
            return protocol
    if preset == "openai" and identity.startswith(("gpt-5", "gpt-6", "o1", "o3", "o4")):
        return "responses"
    return None
