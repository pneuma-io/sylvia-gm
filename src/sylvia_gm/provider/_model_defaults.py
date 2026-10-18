"""Small exact-service/model defaults for facts omitted by discovery, never availability."""

from __future__ import annotations

from typing import Any

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


def documented_model_defaults(preset_id: str, model_id: str) -> dict[str, Any]:
    """Return facts only for an exact route and ID; caller metadata takes precedence."""
    return dict(_DEFAULTS.get((preset_id, model_id), {}))


def documented_model_ids() -> tuple[tuple[str, str], ...]:
    """Expose known identifiers for manual-entry assistance, not an account model list."""
    return tuple(_DEFAULTS)
