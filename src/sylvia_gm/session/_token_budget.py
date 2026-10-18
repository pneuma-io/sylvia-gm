"""Token estimation and budget management.

Provides real token counting via tiktoken (cl100k_base) with graceful
fallback to the chars/3 heuristic when tiktoken is not installed.

Architecture:
    TokenEstimator   — Pluggable estimation strategies
    TokenBudget      — Budget tracking per turn with compression signals

The budget reserves space for:
    - Completion tokens (the model's response, max_output_tokens)
    - Tokenizer mismatch error (15% of context_window)
    - Total safety margin: max_output_tokens + 15% of context_window

For small models where the safety margin would exceed the context window,
max_output_tokens is capped at 25% of context_window, and a minimum
floor of 500 tokens is enforced.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

# ── Graceful import ──────────────────────────────────────────────────

try:
    import tiktoken

    _TIKTOKEN_AVAILABLE = True
    _DEFAULT_ENCODING = tiktoken.get_encoding("cl100k_base")
except ImportError:
    _TIKTOKEN_AVAILABLE = False
    _DEFAULT_ENCODING = None


# ── Budget formula ──────────────────────────────────────────────────


def compute_effective_budget(
    context_window: int,
    max_output_tokens: int | None = None,
) -> int:
    """Compute the token budget available for the prompt.

    The budget reserves space for:
    - Completion tokens (the model's response)
    - Tokenizer mismatch error (15% of context_window)
    - Total safety margin: max_output_tokens + 15% of context_window

    For small models where the safety margin would exceed the context window,
    max_output_tokens is capped at 25% of context_window, and a minimum
    floor of 500 tokens is enforced.

    Args:
        context_window: Model's total context window in tokens.
        max_output_tokens: Maximum tokens the model can produce in a
            completion. Defaults to 4096. Capped for small models.

    Returns:
        Effective token budget available for the prompt (>= 500).
    """
    # Cap max_output_tokens at 25% of context_window for small models,
    # but never below 512 and never above the standard 4096.
    effective_max_tokens = min(
        max_output_tokens or 4096,
        max(512, context_window // 4),
    )
    safety_margin = effective_max_tokens + int(context_window * 0.15)
    effective_budget = context_window - safety_margin
    # Floor: never return a negative or absurdly small budget
    return max(effective_budget, 500)


# ── Token estimation strategies ─────────────────────────────────────


class TokenEstimator:
    """Pluggable token estimation strategies.

    Three strategies:
    - TiktokenEstimator: uses cl100k_base encoding (primary)
    - CharHeuristicEstimator: chars/3 (fallback when tiktoken missing)
    - CalibratedEstimator: adjusts heuristic based on provider-reported
      token counts and per-provider-family calibration ratios
    """

    def estimate(self, text: str) -> int:
        """Estimate token count for a text string.

        Uses tiktoken when available; falls back to chars/3.
        """
        if _TIKTOKEN_AVAILABLE:
            return self._tiktoken_estimate(text)
        return self._char_heuristic_estimate(text)

    def estimate_messages(self, messages: list[dict[str, Any]]) -> int:
        """Estimate total tokens for a list of message dicts.

        Each message dict is expected to have a 'content' key (str)
        and optionally 'tool_calls', 'tool_call_id', etc.
        """
        total = 0
        for msg in messages:
            content = msg.get("content", "")
            if isinstance(content, str):
                total += self.estimate(content)
            elif content is not None:
                total += self.estimate(json.dumps(content, default=str))
            # Tool calls
            tool_calls = msg.get("tool_calls", [])
            for tc in tool_calls:
                if isinstance(tc, dict):
                    fn = tc.get("function", {})
                    total += self.estimate(
                        fn.get("name", "") + fn.get("arguments", "")
                    )
        return total

    @staticmethod
    def _tiktoken_estimate(text: str) -> int:
        """Count tokens using cl100k_base encoding."""
        assert _DEFAULT_ENCODING is not None
        return len(_DEFAULT_ENCODING.encode(text))

    @staticmethod
    def _char_heuristic_estimate(text: str) -> int:
        """Simple chars/3 heuristic for structured content.

        For content containing JSON, code blocks, or character-sheet
        syntax, uses chars/2.5 instead of chars/3.
        """
        # Check for structured content indicators
        structured_markers = ["{", "[", "|", "```"]
        is_structured = any(m in text for m in structured_markers)

        # Count the density of structural characters
        if is_structured:
            struct_chars = sum(
                1 for c in text if c in '{}[]|:"'
            )
            if struct_chars > len(text) * 0.05:  # >5% structural chars
                return max(1, len(text) // 2)  # chars/2 (more conservative)

        return max(1, len(text) // 3)  # chars/3 for prose


@dataclass
class CalibrationEntry:
    """Per-provider-family calibration ratio."""

    provider_family: str  # e.g. "deepseek", "anthropic", "llama"
    ratio: float  # actual_tokens / tiktoken_estimate
    sample_count: int = 0


class CalibratedEstimator(TokenEstimator):
    """Token estimator that applies per-family calibration ratios.

    Reads ratios from a calibration file produced by Gate 0.1.
    If no ratio is found for a family, defaults to the base estimate.
    """

    def __init__(
        self,
        calibrations: list[CalibrationEntry] | None = None,
    ) -> None:
        self._calibrations: dict[str, float] = {}
        for entry in (calibrations or []):
            self._calibrations[entry.provider_family] = entry.ratio

    def estimate_for_provider(
        self, text: str, provider_family: str | None = None,
    ) -> int:
        """Estimate tokens with per-family calibration if available.

        Args:
            text: The text to estimate.
            provider_family: e.g. "deepseek", "anthropic", "llama".
                If None or unknown, uses the default estimate.

        Returns:
            Calibrated token estimate.
        """
        base = self.estimate(text)
        if provider_family and provider_family in self._calibrations:
            ratio = self._calibrations[provider_family]
            # Apply ratio but never go below 1
            return max(1, int(base * ratio))
        return base

    def update_from_usage(
        self,
        provider_family: str,
        actual_tokens: int,
        estimated_tokens: int,
    ) -> None:
        """Update calibration ratio from observed usage data.

        Uses a simple exponential moving average to smooth across
        multiple observations.

        Args:
            provider_family: e.g. "deepseek", "anthropic".
            actual_tokens: Prompt tokens reported by the API.
            estimated_tokens: Our tiktoken estimate before calibration.
        """
        if estimated_tokens <= 0:
            return
        new_ratio = actual_tokens / estimated_tokens
        old_ratio = self._calibrations.get(provider_family, 1.0)
        # Exponential moving average: 70% old, 30% new
        blended = old_ratio * 0.7 + new_ratio * 0.3
        self._calibrations[provider_family] = blended
        logger.debug(
            "Calibration update: family=%s old=%.3f new=%.3f blended=%.3f",
            provider_family, old_ratio, new_ratio, blended,
        )


# ── TokenBudget ─────────────────────────────────────────────────────


@dataclass
class BudgetStatus:
    """Snapshot of current budget state."""

    effective_budget: int = 0
    used_tokens: int = 0
    remaining_tokens: int = 0
    used_pct: float = 0.0
    compressed_count: int = 0
    needs_compression: bool = False


class TokenBudget:
    """Token estimation and budget management.

    Responsibilities:
    - Estimate token count of messages using tiktoken (primary) or chars/3 (fallback)
    - Track budget consumption per turn
    - Signal when compression is needed (90% threshold)
    - Provide budget remaining for history tier
    - Enforce small-model floor via compute_effective_budget()

    Feature flag: ``use_token_estimation`` — defaults to True when
    tiktoken is available; False otherwise (falls back to chars/3).
    """

    COMPRESSION_THRESHOLD = 0.90  # 90% usage triggers compression signal

    def __init__(
        self,
        context_window: int,
        max_output_tokens: int | None = None,
        *,
        estimator: TokenEstimator | None = None,
        use_token_estimation: bool | None = None,
    ) -> None:
        """Initialize the token budget tracker.

        Args:
            context_window: Model's total context window in tokens.
            max_output_tokens: Max completion tokens. None → use default/auto-cap.
            estimator: Pluggable token estimator. Creates default if None.
            use_token_estimation: Override tiktoken usage. None → auto-detect.
        """
        self._context_window = context_window
        self._max_output_tokens = max_output_tokens
        self._effective_budget = compute_effective_budget(
            context_window, max_output_tokens,
        )
        self._estimator = estimator or TokenEstimator()
        self._use_token_estimation = (
            use_token_estimation
            if use_token_estimation is not None
            else _TIKTOKEN_AVAILABLE
        )
        self._total_used: int = 0
        self._compressed_count: int = 0
        self._last_prompt_tokens: int = 0

    # ── Properties ──────────────────────────────────────────────

    @property
    def effective_budget(self) -> int:
        """Token budget available for the prompt (after safety margin)."""
        return self._effective_budget

    @property
    def context_window(self) -> int:
        """The model's total context window."""
        return self._context_window

    @property
    def used_tokens(self) -> int:
        """Total estimated tokens consumed so far."""
        return self._total_used

    @property
    def remaining_tokens(self) -> int:
        """Remaining token budget."""
        return max(0, self._effective_budget - self._total_used)

    @property
    def status(self) -> BudgetStatus:
        """Snapshot of current budget state."""
        used_pct = (
            (self._total_used / self._effective_budget * 100)
            if self._effective_budget > 0
            else 100.0
        )
        return BudgetStatus(
            effective_budget=self._effective_budget,
            used_tokens=self._total_used,
            remaining_tokens=self.remaining_tokens,
            used_pct=round(used_pct, 1),
            compressed_count=self._compressed_count,
            needs_compression=self.needs_compression(),
        )

    def needs_compression(self) -> bool:
        """True when estimated usage exceeds the compression threshold."""
        if self._effective_budget <= 0:
            return True
        return (self._total_used / self._effective_budget) >= self.COMPRESSION_THRESHOLD

    # ── Estimation methods ───────────────────────────────────────

    def estimate(self, text: str) -> int:
        """Estimate token count for a single text string."""
        return self._estimator.estimate(text)

    def estimate_messages(self, messages: list[dict[str, Any]]) -> int:
        """Estimate total tokens for a list of message dicts.

        Each message includes ~4 tokens of overhead (role + formatting)
        in addition to the content.
        """
        content_tokens = self._estimator.estimate_messages(messages)
        # Add ~4 tokens per message for role/formatting overhead
        overhead = len(messages) * 4
        return content_tokens + overhead

    def estimate_prompt(self, system_prompt: str, messages: list[dict[str, Any]]) -> int:
        """Estimate the full prompt token count (system + messages)."""
        total = self.estimate(system_prompt)
        total += self.estimate_messages(messages)
        return total

    def update_used(self, tokens: int) -> None:
        """Record token consumption (e.g. from prompt assembly)."""
        self._total_used = tokens
        if self.needs_compression():
            logger.warning(
                "Token budget at %.1f%% (%d/%d) — compression recommended",
                self.status.used_pct,
                self._total_used,
                self._effective_budget,
            )

    def record_compression(self, tokens_saved: int) -> None:
        """Record that compression occurred, saving tokens from budget."""
        self._total_used = max(0, self._total_used - tokens_saved)
        self._compressed_count += 1
        logger.info(
            "Compression #%d saved ~%d tokens (usage now %d/%d)",
            self._compressed_count,
            tokens_saved,
            self._total_used,
            self._effective_budget,
        )

    def record_api_usage(self, prompt_tokens: int) -> None:
        """Calibrate estimates from actual API-reported prompt tokens."""
        self._last_prompt_tokens = prompt_tokens
        if isinstance(self._estimator, CalibratedEstimator):
            if self._total_used > 0:
                self._estimator.update_from_usage(
                    "default", prompt_tokens, self._total_used,
                )

    def remaining_for_history(
        self, stable_chars: int, context_chars: int, char_chars: int,
    ) -> int:
        """Compute remaining token budget for the HISTORY tier.

        Converts the char lengths of the fixed tiers to token estimates
        and subtracts them from the effective budget.

        Args:
            stable_chars: Character length of STABLE tier.
            context_chars: Character length of CONTEXT tier.
            char_chars: Character length of CHARACTER tier.

        Returns:
            Remaining token budget for HISTORY (minimum 500).
        """
        fixed_tokens = (
            self.estimate("x" * stable_chars)  # proxy for STABLE tier density
            + self.estimate("x" * context_chars)
            + self.estimate("x" * char_chars)
        )
        remaining = self._effective_budget - fixed_tokens
        return max(remaining, 500)


# ── TokenCounter (lightweight, for PromptAssembler integration) ─────


@dataclass
class TokenCount:
    """Result of token counting for tiered prompt output."""

    stable_tokens: int = 0
    context_tokens: int = 0
    character_tokens: int = 0
    history_tokens: int = 0
    total_tokens: int = 0

    def __add__(self, other: "TokenCount") -> "TokenCount":
        return TokenCount(
            stable_tokens=self.stable_tokens + other.stable_tokens,
            context_tokens=self.context_tokens + other.context_tokens,
            character_tokens=self.character_tokens + other.character_tokens,
            history_tokens=self.history_tokens + other.history_tokens,
            total_tokens=self.total_tokens + other.total_tokens,
        )


def estimate_tokens(text: str) -> int:
    """Convenience function: estimate token count for a string.

    Uses tiktoken when available; chars/3 fallback otherwise.
    """
    estimator = TokenEstimator()
    return estimator.estimate(text)
