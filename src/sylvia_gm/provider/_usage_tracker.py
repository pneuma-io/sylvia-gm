"""Per-session token counter with cost estimation.

Tracks cumulative token usage across an entire session and estimates
dollar cost using ModelInfo.cost_per_1k_tokens (blended rate).

Survives provider swaps in FallbackChain — attached to the session,
not a single provider instance.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sylvia_gm.provider._base import ModelInfo, ProviderResponse, TokenUsage


@dataclass
class ModelUsageSnapshot:
    """Per-model usage breakdown."""

    model_id: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    request_count: int = 0
    estimated_cost_usd: float = 0.0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0


@dataclass
class SessionUsageSnapshot:
    """Immutable summary of all usage for a session."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    request_count: int = 0
    estimated_cost_usd: float = 0.0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    by_model: dict[str, ModelUsageSnapshot] = field(default_factory=dict)


class SessionUsageTracker:
    """Stateful accumulator for token usage across a session.

    Usage::

        tracker = SessionUsageTracker()
        tracker.record(response, model_info)
        snapshot = tracker.snapshot()
        print(f"Cost: ${snapshot.estimated_cost_usd:.4f}")
    """

    def __init__(self) -> None:
        self.prompt_tokens: int = 0
        self.completion_tokens: int = 0
        self.total_tokens: int = 0
        self.request_count: int = 0
        self.estimated_cost_usd: float = 0.0
        self.cache_read_tokens: int = 0
        self.cache_write_tokens: int = 0
        self._by_model: dict[str, ModelUsageSnapshot] = {}

    def record(
        self,
        response: ProviderResponse,
        model_info: ModelInfo | None = None,
    ) -> None:
        """Record token usage from a single provider response.

        Args:
            response: The complete provider response with usage data.
            model_info: Optional model metadata for cost estimation.
        """
        usage = response.usage
        prompt = usage.prompt_tokens
        completion = usage.completion_tokens
        total = usage.total_tokens

        self.prompt_tokens += prompt
        self.completion_tokens += completion
        self.total_tokens += total
        self.request_count += 1

        # Cache stats
        cu = response.cache_usage
        if cu:
            self.cache_read_tokens += cu.cache_read_tokens
            self.cache_write_tokens += cu.cache_write_tokens

        # Cost estimation (blended rate)
        cost_per_1k = model_info.cost_per_1k_tokens if model_info else 0.0
        cost = (total * cost_per_1k) / 1000.0
        self.estimated_cost_usd += cost

        # Per-model breakdown
        model_id = response.model or (model_info.id if model_info else "unknown")
        if model_id not in self._by_model:
            self._by_model[model_id] = ModelUsageSnapshot(model_id=model_id)
        ms = self._by_model[model_id]
        ms.prompt_tokens += prompt
        ms.completion_tokens += completion
        ms.total_tokens += total
        ms.request_count += 1
        ms.estimated_cost_usd += cost
        if cu:
            ms.cache_read_tokens += cu.cache_read_tokens
            ms.cache_write_tokens += cu.cache_write_tokens

    def reset(self) -> None:
        """Reset all accumulators to zero."""
        self.prompt_tokens = 0
        self.completion_tokens = 0
        self.total_tokens = 0
        self.request_count = 0
        self.estimated_cost_usd = 0.0
        self.cache_read_tokens = 0
        self.cache_write_tokens = 0
        self._by_model.clear()

    def snapshot(self) -> SessionUsageSnapshot:
        """Return an immutable snapshot of current usage."""
        return SessionUsageSnapshot(
            prompt_tokens=self.prompt_tokens,
            completion_tokens=self.completion_tokens,
            total_tokens=self.total_tokens,
            request_count=self.request_count,
            estimated_cost_usd=self.estimated_cost_usd,
            cache_read_tokens=self.cache_read_tokens,
            cache_write_tokens=self.cache_write_tokens,
            by_model=dict(self._by_model),
        )

    @property
    def by_model(self) -> dict[str, ModelUsageSnapshot]:
        """Per-model breakdown, keyed by model_id."""
        return dict(self._by_model)
