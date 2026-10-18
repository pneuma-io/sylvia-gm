"""Provider diagnostics capture: latency, HTTP status, headers, cache hit rate.

Provides ProviderCallDiagnostic (immutable snapshot of a single call),
DiagnosticsCollector (ring-buffer of last N diagnostics), and the
DIAGNOSTIC_HEADERS constant (subset of interesting response headers).
"""

from __future__ import annotations

import statistics
from collections import deque
from dataclasses import dataclass, field

from sylvia_gm.provider._base import TokenUsage


# ── DIAGNOSTIC_HEADERS — subset of interesting response headers ──────

DIAGNOSTIC_HEADERS: set[str] = {
    "cf-ray", "cf-cache-status",
    "x-request-id", "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests", "x-ratelimit-reset-requests",
    "x-ratelimit-limit-tokens", "x-ratelimit-remaining-tokens",
    "openai-organization", "openai-processing-ms",
    "openai-version",
    "anthropic-request-id",
    "retry-after",
}


@dataclass(frozen=True)
class ProviderCallDiagnostic:
    """Immutable snapshot of a single provider call for diagnostics.

    Captures latency, HTTP status, provider-specific headers, cache
    hit ratio, and retry attempt count.
    """

    timestamp: str                     # ISO-8601
    provider_key: str
    model_id: str
    duration_ms: float
    http_status: int | None
    retry_attempt: int                 # zero-based (0 = first attempt)
    success: bool
    error_type: str | None = None     # Exception class name if failed
    response_headers: dict[str, str] = field(default_factory=dict)
    usage: TokenUsage | None = None
    cache_hit_ratio: float | None = None  # 0.0-1.0 if caching active
    # Provider-specific fields (nullable)
    cf_ray: str | None = None
    openai_organization: str | None = None
    openai_processing_ms: str | None = None
    anthropic_request_id: str | None = None


class DiagnosticsCollector:
    """Ring-buffer collector for the last N ProviderCallDiagnostic records.

    Attached to SessionUsageTracker. Provides recent_errors, latency_stats,
    and cache_stats queries.

    Usage::

        collector = DiagnosticsCollector(max_records=100)
        collector.record(diagnostic)
        p50 = collector.latency_stats()["p50"]
    """

    def __init__(self, max_records: int = 100) -> None:
        self._max = max_records
        self._buffer: deque[ProviderCallDiagnostic] = deque(maxlen=max_records)

    def record(self, diagnostic: ProviderCallDiagnostic) -> None:
        """Push a diagnostic into the ring buffer."""
        self._buffer.append(diagnostic)

    def recent_errors(self, count: int = 10) -> list[ProviderCallDiagnostic]:
        """Return the last N error diagnostics."""
        errors = [d for d in self._buffer if not d.success]
        return errors[-count:]

    def latency_stats(self) -> dict[str, float]:
        """Return p50/p95/p99/avg/count from buffer."""
        latencies = sorted(d.duration_ms for d in self._buffer)
        if not latencies:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "avg": 0.0, "count": 0}
        return {
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
            "p99": _percentile(latencies, 99),
            "avg": sum(latencies) / len(latencies),
            "count": len(latencies),
        }

    def cache_stats(self) -> dict[str, float]:
        """Return aggregate cache hit ratio from all diagnostics."""
        ratios = [
            d.cache_hit_ratio for d in self._buffer
            if d.cache_hit_ratio is not None
        ]
        if not ratios:
            return {"avg_hit_ratio": 0.0, "count": 0}
        return {
            "avg_hit_ratio": sum(ratios) / len(ratios),
            "count": len(ratios),
        }

    @property
    def records(self) -> list[ProviderCallDiagnostic]:
        return list(self._buffer)

    @property
    def count(self) -> int:
        return len(self._buffer)

    @property
    def error_count(self) -> int:
        return sum(1 for d in self._buffer if not d.success)

    @property
    def success_rate(self) -> float:
        if not self._buffer:
            return 0.0
        return 1.0 - (self.error_count / len(self._buffer))


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Compute percentile from sorted list using linear interpolation."""
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * pct / 100.0
    f = int(k)
    c = k - f
    if f + 1 < len(sorted_values):
        return sorted_values[f] + c * (sorted_values[f + 1] - sorted_values[f])
    return sorted_values[f]
