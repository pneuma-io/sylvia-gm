"""Documented model capabilities on Anthropic's Messages API.

Effort, sampling, tool choice, and thinking replay follow the Claude Platform docs:
https://platform.claude.com/docs/en/build-with-claude/effort
https://platform.claude.com/docs/en/build-with-claude/tool-use
https://platform.claude.com/docs/en/build-with-claude/extended-thinking
https://platform.claude.com/docs/en/build-with-claude/fast-mode
"""

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class ClaudeModelFacts:
    efforts: tuple[str, ...] | None = None
    forced_tool_choice: bool = False
    temperature: bool = False
    binds_thinking: bool = False
    fast: bool = False
    claude_code_context_window: int | None = None


_EFFORT = ("low", "medium", "high", "max")
_EXTENDED_EFFORT = ("low", "medium", "high", "xhigh", "max")
_MODELS = {
    "claude-fable-5": ClaudeModelFacts(forced_tool_choice=True),
    "claude-fable-5-2": ClaudeModelFacts(efforts=_EXTENDED_EFFORT, binds_thinking=True, fast=True, claude_code_context_window=1_000_000),
    "opus": ClaudeModelFacts(claude_code_context_window=1_000_000),
    "sonnet": ClaudeModelFacts(claude_code_context_window=1_000_000),
    "haiku": ClaudeModelFacts(claude_code_context_window=1_000_000),
    "fable": ClaudeModelFacts(claude_code_context_window=1_000_000),
    "claude-haiku-4-5": ClaudeModelFacts(forced_tool_choice=True, temperature=True),
    "claude-haiku-5-5": ClaudeModelFacts(efforts=_EXTENDED_EFFORT, forced_tool_choice=True, claude_code_context_window=1_000_000),
    "claude-mythos-5-2": ClaudeModelFacts(binds_thinking=True, fast=True),
    "claude-opus-4": ClaudeModelFacts(forced_tool_choice=True, temperature=True),
    "claude-opus-4-1": ClaudeModelFacts(forced_tool_choice=True, temperature=True),
    "claude-opus-4-5": ClaudeModelFacts(forced_tool_choice=True, temperature=True),
    "claude-opus-4-6": ClaudeModelFacts(efforts=_EFFORT, forced_tool_choice=True, temperature=True),
    "claude-opus-4-7": ClaudeModelFacts(efforts=_EXTENDED_EFFORT, forced_tool_choice=True),
    "claude-opus-4-8": ClaudeModelFacts(efforts=_EXTENDED_EFFORT, forced_tool_choice=True),
    "claude-opus-5": ClaudeModelFacts(efforts=_EXTENDED_EFFORT, forced_tool_choice=True),
    "claude-opus-5-5": ClaudeModelFacts(efforts=_EXTENDED_EFFORT, fast=True, claude_code_context_window=1_000_000),
    "claude-sonnet-4": ClaudeModelFacts(forced_tool_choice=True, temperature=True),
    "claude-sonnet-4-5": ClaudeModelFacts(forced_tool_choice=True, temperature=True),
    "claude-sonnet-4-6": ClaudeModelFacts(efforts=_EFFORT, forced_tool_choice=True, temperature=True),
    "claude-sonnet-5": ClaudeModelFacts(efforts=_EXTENDED_EFFORT, forced_tool_choice=True),
    "claude-sonnet-5-5": ClaudeModelFacts(efforts=_EXTENDED_EFFORT, claude_code_context_window=1_000_000),
}


def claude_model_id(model_id: str) -> str | None:
    bare = re.sub(r"[-@]\d{8}$", "", model_id.strip().casefold().rsplit("/", 1)[-1])
    return bare if bare.startswith("claude-") else None


def claude_model_facts(model_id: str) -> ClaudeModelFacts:
    return _MODELS.get(claude_model_id(model_id) or "", _UNKNOWN)


_UNKNOWN = ClaudeModelFacts()


def claude_code_context_window(model_id: str) -> int | None:
    return _MODELS.get(model_id, _UNKNOWN).claude_code_context_window
