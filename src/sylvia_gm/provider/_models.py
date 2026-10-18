"""Model catalog: 18 entries spanning BYOK and Ollama providers.

The canonical model ID format is ``provider_key/model_name``
(e.g. ``opencode-go/deepseek-v4``). This eliminates routing ambiguity.

Each entry carries full ModelInfo metadata so the registry can
resolve short names, list recommended models, and validate requests.
"""

from __future__ import annotations

from dataclasses import replace

from sylvia_gm.provider._base import ModelInfo


def build_catalog() -> list[ModelInfo]:
    """Build the v0.2 model catalog with 18 entries.

    v0.2: All context_window values audited against public specifications.
    DeepSeek V4: 1,048,576 (1M), DeepSeek V3/R1: 131,072 (128K).
    Added max_output_tokens for small-model floor support.

    Returns:
        A list of ModelInfo entries covering OpenCode Go, OpenRouter,
        Ollama local, and Anthropic providers.
    """
    models = [
        # ── OpenCode Go (BYOK) ──────────────────────────────────────────
        ModelInfo(
            id="opencode-go/deepseek-v4",
            name="DeepSeek V4",
            provider_name="OpenCode Go",
            provider_key="opencode_go",
            provider_mode="byok",
            recommended=True,
            context_window=1048576,  # 1M — fixed from 65536
            max_output_tokens=4096,
            cost_per_1k_tokens=0.00055,
        ),
        ModelInfo(
            id="opencode-go/deepseek-v3",
            name="DeepSeek V3",
            provider_name="OpenCode Go",
            provider_key="opencode_go",
            provider_mode="byok",
            context_window=131072,  # 128K — fixed from 65536
            max_output_tokens=4096,
            cost_per_1k_tokens=0.00027,
        ),
        ModelInfo(
            id="opencode-go/deepseek-r1",
            name="DeepSeek R1",
            provider_name="OpenCode Go",
            provider_key="opencode_go",
            provider_mode="byok",
            context_window=131072,  # 128K — fixed from 65536
            max_output_tokens=4096,
            cost_per_1k_tokens=0.00055,
        ),
        ModelInfo(
            id="opencode-go/claude-opus-4-5",
            name="Claude Opus 4.5",
            provider_name="OpenCode Go",
            provider_key="opencode_go",
            provider_mode="byok",
            premium=True,
            context_window=200000,
            max_output_tokens=4096,
            cost_per_1k_tokens=0.015,
        ),
        ModelInfo(
            id="opencode-go/claude-sonnet-4-5",
            name="Claude Sonnet 4.5",
            provider_name="OpenCode Go",
            provider_key="opencode_go",
            provider_mode="byok",
            premium=True,
            context_window=200000,
            max_output_tokens=4096,
            cost_per_1k_tokens=0.003,
        ),
        ModelInfo(
            id="opencode-go/gpt-5",
            name="GPT-5",
            provider_name="OpenCode Go",
            provider_key="opencode_go",
            provider_mode="byok",
            premium=True,
            context_window=128000,
            max_output_tokens=4096,
            cost_per_1k_tokens=0.015,
        ),
        # ── OpenRouter (BYOK) ───────────────────────────────────────────
        ModelInfo(
            id="openrouter/deepseek-v4",
            name="DeepSeek V4",
            provider_name="OpenRouter",
            provider_key="openrouter",
            provider_mode="byok",
            context_window=1048576,  # 1M — fixed from 65536
            max_output_tokens=4096,
            cost_per_1k_tokens=0.00055,
        ),
        ModelInfo(
            id="openrouter/deepseek-r1",
            name="DeepSeek R1",
            provider_name="OpenRouter",
            provider_key="openrouter",
            provider_mode="byok",
            context_window=131072,  # 128K — fixed from 65536
            max_output_tokens=4096,
            cost_per_1k_tokens=0.00055,
        ),
        ModelInfo(
            id="openrouter/claude-sonnet-4-5",
            name="Claude Sonnet 4.5",
            provider_name="OpenRouter",
            provider_key="openrouter",
            provider_mode="byok",
            premium=True,
            context_window=200000,
            max_output_tokens=4096,
            cost_per_1k_tokens=0.003,
        ),
        ModelInfo(
            id="openrouter/gemini-2.5-pro",
            name="Gemini 2.5 Pro",
            provider_name="OpenRouter",
            provider_key="openrouter",
            provider_mode="byok",
            premium=True,
            context_window=1048576,
            max_output_tokens=4096,
            cost_per_1k_tokens=0.00125,
        ),
        # ── Ollama (local) ──────────────────────────────────────────────
        ModelInfo(
            id="ollama/qwen2.5:14b",
            name="Qwen 2.5 14B",
            provider_name="Ollama",
            provider_key="ollama",
            provider_mode="ollama",
            context_window=32768,
            max_output_tokens=4096,
            cost_per_1k_tokens=0.0,
        ),
        ModelInfo(
            id="ollama/qwen2.5:7b",
            name="Qwen 2.5 7B",
            provider_name="Ollama",
            provider_key="ollama",
            provider_mode="ollama",
            context_window=32768,
            max_output_tokens=4096,
            cost_per_1k_tokens=0.0,
        ),
        ModelInfo(
            id="ollama/llama3.1:8b",
            name="Llama 3.1 8B",
            provider_name="Ollama",
            provider_key="ollama",
            provider_mode="ollama",
            recommended=True,
            context_window=131072,
            max_output_tokens=4096,
            cost_per_1k_tokens=0.0,
        ),
        ModelInfo(
            id="ollama/llama3.1:70b",
            name="Llama 3.1 70B",
            provider_name="Ollama",
            provider_key="ollama",
            provider_mode="ollama",
            context_window=131072,
            max_output_tokens=4096,
            cost_per_1k_tokens=0.0,
        ),
        ModelInfo(
            id="ollama/mistral:7b",
            name="Mistral 7B",
            provider_name="Ollama",
            provider_key="ollama",
            provider_mode="ollama",
            context_window=32768,
            max_output_tokens=4096,
            cost_per_1k_tokens=0.0,
        ),
        ModelInfo(
            id="ollama/phi3:mini",
            name="Phi-3 Mini",
            provider_name="Ollama",
            provider_key="ollama",
            provider_mode="ollama",
            context_window=4096,
            max_output_tokens=1024,  # Capped: 4096 // 4 = 1024
            cost_per_1k_tokens=0.0,
        ),
        # ── Anthropic (BYOK) ────────────────────────────────────────────
        ModelInfo(
            id="anthropic/claude-sonnet-4-5",
            name="Claude Sonnet 4.5",
            provider_name="Anthropic",
            provider_key="anthropic",
            provider_mode="byok",
            premium=True,
            context_window=200000,
            max_output_tokens=4096,
            cost_per_1k_tokens=0.003,
            transport="anthropic_messages",
        ),
        ModelInfo(
            id="anthropic/claude-haiku-3-5",
            name="Claude Haiku 3.5",
            provider_name="Anthropic",
            provider_key="anthropic",
            provider_mode="byok",
            context_window=200000,
            max_output_tokens=4096,
            cost_per_1k_tokens=0.00025,
            transport="anthropic_messages",
        ),
    ]
    return [
        replace(
            model,
            declared_context_window=model.context_window,
            declared_context_source="model_catalog",
            context_window=(None if model.provider_mode == "ollama" else model.context_window),
            context_window_source=(
                "unknown" if model.provider_mode == "ollama" else "model_catalog"
            ),
        )
        for model in models
    ]
