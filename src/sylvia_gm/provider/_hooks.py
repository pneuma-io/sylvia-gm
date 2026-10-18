"""Pre/post API request hook system for provider observability.

Defines the HookContext snapshot, the ProviderHook protocol,
HookRegistry (ordered listener list), HookManager (attached to Provider ABC),
and built-in hook implementations: LoggingHook, MetricsHook,
UsageTrackingHook, DiagnosticsHook, DumpHook.
"""

from __future__ import annotations

import itertools
import json
import logging
import os
import time
import traceback
import uuid
from collections import defaultdict
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Protocol, runtime_checkable

from sylvia_gm.provider._base import (
    ProviderResponse,
)

if TYPE_CHECKING:
    from sylvia_gm.provider._diagnostics import DiagnosticsCollector
    from sylvia_gm.provider._usage_tracker import SessionUsageTracker

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════
# HookContext — immutable snapshot passed to every hook
# ═══════════════════════════════════════════════════════════════


@dataclass(frozen=True)
class HookContext:
    """Immutable snapshot of a provider call lifecycle.

    Passed to every hook callback. Fields are populated progressively:
    request-time fields are present for all hooks; response fields
    only for on_response; error fields only for on_error.
    """

    provider_key: str
    model_id: str
    message_count: int = 0
    tool_count: int = 0
    request_json: dict[str, Any] | None = None
    response: ProviderResponse | None = None
    error: Exception | None = None
    duration_ms: float = 0.0
    http_status: int | None = None
    response_headers: dict[str, str] = field(default_factory=dict)
    retry_attempt: int = 0
    timestamp_iso: str = ""
    stream: bool = False


# ═══════════════════════════════════════════════════════════════
# ProviderHook protocol — what hooks implement
# ═══════════════════════════════════════════════════════════════


@runtime_checkable
class ProviderHook(Protocol):
    """Protocol for hook callbacks registered with HookRegistry.

    Implement any or all of the three lifecycle methods.
    Each method receives the same HookContext at different stages.
    """

    async def on_request(self, context: HookContext) -> None:
        """Called before the provider sends a request."""
        ...

    async def on_response(self, context: HookContext) -> None:
        """Called after the provider receives a successful response."""
        ...

    async def on_error(self, context: HookContext) -> None:
        """Called when a provider call raises an exception."""
        ...


# ═══════════════════════════════════════════════════════════════
# HookRegistry — ordered listener list with isolation
# ═══════════════════════════════════════════════════════════════


class HookRegistry:
    """Ordered registry of ProviderHook instances.

    Fires hooks in registration order. Each hook is wrapped in
    try/except — one hook failing does not prevent others from running.
    """

    def __init__(self) -> None:
        self._hooks: list[ProviderHook] = []

    def register(self, hook: ProviderHook) -> None:
        """Append a hook to the end of the call chain. Idempotent-ish."""
        if hook not in self._hooks:
            self._hooks.append(hook)

    def deregister(self, hook: ProviderHook) -> None:
        """Remove a hook from the call chain."""
        with suppress(ValueError):
            self._hooks.remove(hook)

    @property
    def hooks(self) -> list[ProviderHook]:
        return list(self._hooks)

    async def fire_on_request(self, ctx: HookContext) -> None:
        """Fire on_request on all registered hooks in order."""
        for hook in self._hooks:
            try:
                if hasattr(hook, "on_request"):
                    await hook.on_request(ctx)
            except Exception:
                logger.warning(
                    "Hook %s.on_request failed: %s",
                    type(hook).__name__,
                    traceback.format_exc().strip().split("\n")[-1],
                )

    async def fire_on_response(self, ctx: HookContext) -> None:
        """Fire on_response on all registered hooks in order."""
        for hook in self._hooks:
            try:
                if hasattr(hook, "on_response"):
                    await hook.on_response(ctx)
            except Exception:
                logger.warning(
                    "Hook %s.on_response failed: %s",
                    type(hook).__name__,
                    traceback.format_exc().strip().split("\n")[-1],
                )

    async def fire_on_error(self, ctx: HookContext) -> None:
        """Fire on_error on all registered hooks in order."""
        for hook in self._hooks:
            try:
                if hasattr(hook, "on_error"):
                    await hook.on_error(ctx)
            except Exception:
                logger.warning(
                    "Hook %s.on_error failed: %s",
                    type(hook).__name__,
                    traceback.format_exc().strip().split("\n")[-1],
                )


# ═══════════════════════════════════════════════════════════════
# HookManager — attached to Provider ABC
# ═══════════════════════════════════════════════════════════════


class HookManager:
    """Per-provider entry point for hook registration and firing.

    Usage::

        provider.hooks.register(my_hook)
        await provider.hooks.fire_on_request(ctx)
    """

    def __init__(self) -> None:
        self._registry = HookRegistry()
        self._scoped_hooks: ContextVar[tuple[ProviderHook, ...]] = ContextVar(
            "sylvia_provider_scoped_hooks",
            default=(),
        )

    @property
    def registry(self) -> HookRegistry:
        return self._registry

    def register(self, hook: ProviderHook) -> None:
        self._registry.register(hook)

    def deregister(self, hook: ProviderHook) -> None:
        self._registry.deregister(hook)

    @contextmanager
    def scoped(self, hook: ProviderHook) -> Iterator[None]:
        """Observe only provider work performed in the current execution context."""

        token = self._scoped_hooks.set((*self._scoped_hooks.get(), hook))
        try:
            yield
        finally:
            self._scoped_hooks.reset(token)

    async def _fire_scoped(self, callback: str, ctx: HookContext) -> None:
        """Fire task-local hooks without exposing concurrent calls to one another."""

        registered = self._registry.hooks
        for hook in self._scoped_hooks.get():
            if hook in registered:
                continue
            try:
                method = getattr(hook, callback, None)
                if method is not None:
                    await method(ctx)
            except Exception:
                logger.warning(
                    "Hook %s.%s failed: %s",
                    type(hook).__name__,
                    callback,
                    traceback.format_exc().strip().split("\n")[-1],
                )

    async def fire_on_request(self, ctx: HookContext) -> None:
        await self._registry.fire_on_request(ctx)
        await self._fire_scoped("on_request", ctx)

    async def fire_on_response(self, ctx: HookContext) -> None:
        await self._registry.fire_on_response(ctx)
        await self._fire_scoped("on_response", ctx)

    async def fire_on_error(self, ctx: HookContext) -> None:
        await self._registry.fire_on_error(ctx)
        await self._fire_scoped("on_error", ctx)


# ═══════════════════════════════════════════════════════════════
# Built-in Hook: LoggingHook
# ═══════════════════════════════════════════════════════════════


class LoggingHook:
    """Replaces inline logger calls with structured hook-based logging.

    Formats key=value lines for provider_request, provider_response,
    cache_stats, and provider_error events. Works for ALL providers
    (including Anthropic, which previously had zero structured logging).
    """

    async def on_request(self, context: HookContext) -> None:
        logger.info(
            "provider_request provider=%s model=%s message_count=%d tool_count=%d stream=%s",
            context.provider_key,
            context.model_id,
            context.message_count,
            context.tool_count,
            context.stream,
        )

    async def on_response(self, context: HookContext) -> None:
        response = context.response
        usage = response.usage if response else None
        total_tokens = usage.total_tokens if usage else 0
        finish_reason = response.finish_reason if response else "unknown"
        logger.info(
            "provider_response model=%s status=%s tokens=%d finish_reason=%s "
            "duration_ms=%.1f attempt=%d",
            context.model_id,
            context.http_status or "unknown",
            total_tokens,
            finish_reason,
            context.duration_ms,
            context.retry_attempt,
        )

        # Cache stats
        if response and response.cache_usage:
            cu = response.cache_usage
            if cu.cache_read_tokens > 0:
                logger.info(
                    "cache_stats hit_ratio=%.1f%% cached=%d total=%d write=%d",
                    cu.savings_pct,
                    cu.cache_read_tokens,
                    cu.total_prompt_tokens,
                    cu.cache_write_tokens,
                )

    async def on_error(self, context: HookContext) -> None:
        error_msg = str(context.error)[:200] if context.error else "unknown"
        logger.error(
            "provider_error provider=%s model=%s status=%s attempt=%d duration_ms=%.1f error=%s",
            context.provider_key,
            context.model_id,
            context.http_status or "unknown",
            context.retry_attempt,
            context.duration_ms,
            error_msg,
        )


# ═══════════════════════════════════════════════════════════════
# Built-in Hook: MetricsHook
# ═══════════════════════════════════════════════════════════════


class MetricsHook:
    """Accumulates call counts, latency histogram, error counts per provider."""

    def __init__(self) -> None:
        self._call_count: dict[str, int] = defaultdict(int)
        self._error_count: dict[str, int] = defaultdict(int)
        self._latencies: dict[str, list[float]] = defaultdict(list)
        self._token_totals: dict[str, int] = defaultdict(int)
        self._max_latency_samples = 1000

    async def on_request(self, context: HookContext) -> None:
        self._call_count[context.provider_key] += 1

    async def on_response(self, context: HookContext) -> None:
        key = context.provider_key
        latencies = self._latencies[key]
        if len(latencies) < self._max_latency_samples:
            latencies.append(context.duration_ms)
        if context.response and context.response.usage:
            self._token_totals[key] += context.response.usage.total_tokens

    async def on_error(self, context: HookContext) -> None:
        self._error_count[context.provider_key] += 1

    def call_count(self, provider_key: str | None = None) -> int:
        if provider_key:
            return self._call_count.get(provider_key, 0)
        return sum(self._call_count.values())

    def error_count(self, provider_key: str | None = None) -> int:
        if provider_key:
            return self._error_count.get(provider_key, 0)
        return sum(self._error_count.values())

    def error_rate(self, provider_key: str | None = None) -> float:
        total = self.call_count(provider_key)
        if total == 0:
            return 0.0
        return self.error_count(provider_key) / total

    def latency_stats(self, provider_key: str) -> dict[str, float]:
        latencies = sorted(self._latencies.get(provider_key, []))
        if not latencies:
            return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "avg": 0.0, "count": 0}
        return {
            "p50": _percentile(latencies, 50),
            "p95": _percentile(latencies, 95),
            "p99": _percentile(latencies, 99),
            "avg": sum(latencies) / len(latencies),
            "count": len(latencies),
        }

    def total_tokens(self, provider_key: str | None = None) -> int:
        if provider_key:
            return self._token_totals.get(provider_key, 0)
        return sum(self._token_totals.values())

    def reset(self) -> None:
        self._call_count.clear()
        self._error_count.clear()
        self._latencies.clear()
        self._token_totals.clear()


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Compute percentile from sorted list."""
    if not sorted_values:
        return 0.0
    k = (len(sorted_values) - 1) * pct / 100.0
    f = int(k)
    c = k - f
    if f + 1 < len(sorted_values):
        return sorted_values[f] + c * (sorted_values[f + 1] - sorted_values[f])
    return sorted_values[f]


# ═══════════════════════════════════════════════════════════════
# Built-in Hook: DumpHook
# ═══════════════════════════════════════════════════════════════


class DumpHook:
    """Opt-in request/response dump to disk.

    Enabled via env var ``SYLVIA_GM_DUMP_REQUESTS=1``.
    Writes JSON files to ``~/.sylvia-gm/dumps/<session-uuid>/``.
    Uses atomic writes (tmp file + rename).
    """

    def __init__(self) -> None:
        self._enabled = os.environ.get("SYLVIA_GM_DUMP_REQUESTS", "").lower() in (
            "1",
            "true",
            "yes",
        )
        self._session_id: str | None = None
        self._counter = itertools.count(1)
        self._dump_dir: Path | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def _ensure_dir(self) -> Path:
        if self._dump_dir is None:
            if self._session_id is None:
                self._session_id = uuid.uuid4().hex[:12]
            self._dump_dir = Path.home() / ".sylvia-gm" / "dumps" / self._session_id
            self._dump_dir.mkdir(parents=True, exist_ok=True)
        return self._dump_dir

    def _atomic_write(self, path: Path, data: dict[str, Any]) -> None:
        """Write JSON atomically via tmp + rename."""
        tmp_path = path.with_suffix(path.suffix + ".tmp")
        try:
            tmp_path.write_text(json.dumps(data, indent=2, default=str), encoding="utf-8")
            tmp_path.replace(path)
        except Exception:
            logger.debug("DumpHook: failed to write %s", path, exc_info=True)

    async def on_request(self, context: HookContext) -> None:
        if not self._enabled:
            return
        dump_dir = self._ensure_dir()
        seq = next(self._counter)
        data: dict[str, Any] = {
            "timestamp": context.timestamp_iso,
            "provider_key": context.provider_key,
            "model_id": context.model_id,
            "method": "generate_stream" if context.stream else "generate",
            "attempt": context.retry_attempt,
            "request": context.request_json,
        }
        self._atomic_write(dump_dir / f"{seq:06d}-request.json", data)

    async def on_response(self, context: HookContext) -> None:
        if not self._enabled:
            return
        dump_dir = self._ensure_dir()
        seq = next(self._counter)
        response = context.response
        data: dict[str, Any] = {
            "timestamp": context.timestamp_iso,
            "duration_ms": context.duration_ms,
            "http_status": context.http_status,
            "response": {
                "content": response.content if response else None,
                "tool_calls": [
                    {"id": tc.id, "name": tc.name, "arguments": tc.arguments}
                    for tc in (response.tool_calls if response else [])
                ],
                "usage": {
                    "prompt_tokens": response.usage.prompt_tokens
                    if response and response.usage
                    else 0,
                    "completion_tokens": response.usage.completion_tokens
                    if response and response.usage
                    else 0,
                    "total_tokens": response.usage.total_tokens
                    if response and response.usage
                    else 0,
                },
                "finish_reason": response.finish_reason if response else "unknown",
            },
            "headers": context.response_headers,
        }
        self._atomic_write(dump_dir / f"{seq:06d}-response.json", data)

    async def on_error(self, context: HookContext) -> None:
        if not self._enabled:
            return
        dump_dir = self._ensure_dir()
        seq = next(self._counter)
        data: dict[str, Any] = {
            "timestamp": context.timestamp_iso,
            "provider_key": context.provider_key,
            "model_id": context.model_id,
            "error": str(context.error) if context.error else "unknown",
            "error_type": type(context.error).__name__ if context.error else "Unknown",
            "traceback": traceback.format_exc() if context.error else "",
            "http_status": context.http_status,
            "duration_ms": context.duration_ms,
            "attempt": context.retry_attempt,
        }
        self._atomic_write(dump_dir / f"{seq:06d}-error.json", data)


# ═══════════════════════════════════════════════════════════════
# Built-in Hook: UsageTrackingHook
# ═══════════════════════════════════════════════════════════════


class UsageTrackingHook:
    """Records token usage via SessionUsageTracker on every response.

    Always enabled — this hook is the bridge between provider hooks
    and the session-level usage accumulator.
    """

    def __init__(self, tracker: SessionUsageTracker | None = None) -> None:
        self._tracker = tracker

    def set_tracker(self, tracker: SessionUsageTracker) -> None:
        """Attach or replace the session-level usage tracker."""
        self._tracker = tracker

    async def on_response(self, context: HookContext) -> None:
        if self._tracker is None or context.response is None:
            return
        from sylvia_gm.provider._base import ModelInfo

        # Build minimal ModelInfo for cost lookup
        model_info = ModelInfo(
            id=context.model_id,
            name=context.model_id,
            provider_name=context.provider_key,
            provider_key=context.provider_key,
            provider_mode="byok",
        )
        self._tracker.record(context.response, model_info)

    async def on_error(self, context: HookContext) -> None:
        # A terminal failure can still report consumed tokens. Count only an
        # explicitly reported usage snapshot; absent usage remains unknown.
        if context.response is not None and context.response.usage_reported:
            await self.on_response(context)


# ═══════════════════════════════════════════════════════════════
# Built-in Hook: DiagnosticsHook
# ═══════════════════════════════════════════════════════════════


class DiagnosticsHook:
    """Captures ProviderCallDiagnostic records on every call.

    Hooks into on_request (timing start) and on_response/on_error
    (record creation). Attached to a DiagnosticsCollector.
    """

    def __init__(self, collector: DiagnosticsCollector | None = None) -> None:
        from sylvia_gm.provider._diagnostics import DiagnosticsCollector

        self._collector = collector or DiagnosticsCollector()
        self._start_times: dict[str, float] = {}  # request_id → monotonic start

    def set_collector(self, collector: DiagnosticsCollector) -> None:
        self._collector = collector

    @property
    def collector(self) -> DiagnosticsCollector:
        return self._collector

    def _request_id(self, ctx: HookContext) -> str:
        return f"{ctx.provider_key}:{ctx.model_id}:{ctx.timestamp_iso}"

    async def on_request(self, context: HookContext) -> None:
        self._start_times[self._request_id(context)] = time.monotonic()

    async def on_response(self, context: HookContext) -> None:
        from sylvia_gm.provider._diagnostics import ProviderCallDiagnostic

        rid = self._request_id(context)
        start = self._start_times.pop(rid, time.monotonic())
        duration_ms = context.duration_ms or (time.monotonic() - start) * 1000.0

        cache_hit = None
        if context.response and context.response.cache_usage:
            cache_hit = context.response.cache_usage.hit_ratio

        diagnostic = ProviderCallDiagnostic(
            timestamp=context.timestamp_iso,
            provider_key=context.provider_key,
            model_id=context.model_id,
            duration_ms=duration_ms,
            http_status=context.http_status,
            retry_attempt=context.retry_attempt,
            success=True,
            response_headers=context.response_headers,
            usage=context.response.usage if context.response else None,
            cache_hit_ratio=cache_hit,
            cf_ray=context.response_headers.get("cf-ray"),
            openai_organization=context.response_headers.get("openai-organization"),
            openai_processing_ms=context.response_headers.get("openai-processing-ms"),
            anthropic_request_id=context.response_headers.get("anthropic-request-id"),
        )
        self._collector.record(diagnostic)

    async def on_error(self, context: HookContext) -> None:
        from sylvia_gm.provider._diagnostics import ProviderCallDiagnostic

        rid = self._request_id(context)
        start = self._start_times.pop(rid, time.monotonic())
        duration_ms = context.duration_ms or (time.monotonic() - start) * 1000.0

        diagnostic = ProviderCallDiagnostic(
            timestamp=context.timestamp_iso,
            provider_key=context.provider_key,
            model_id=context.model_id,
            duration_ms=duration_ms,
            http_status=context.http_status,
            retry_attempt=context.retry_attempt,
            success=False,
            error_type=type(context.error).__name__ if context.error else "Unknown",
            response_headers=context.response_headers,
        )
        self._collector.record(diagnostic)
