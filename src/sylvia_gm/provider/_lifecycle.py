"""Bounded metadata-only observations of an adapter's local lifecycle boundaries."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sylvia_gm.provider._codex_rpc import CodexCancelledError

_PHASES = frozenset({
    "runtime_start", "process_boot", "initialize", "account_read", "thread_start",
    "history_injection", "turn_start", "turn_stream", "shutdown",
})


class LifecycleTiming:
    """Use one monotonic clock, retaining nested spans without adding their durations twice."""

    def __init__(self) -> None:
        self.started_utc = datetime.now(UTC).isoformat()
        self.origin = time.perf_counter_ns()
        self._spans: list[dict[str, Any]] = []
        self._lock = threading.Lock()

    def record(
        self, name: str, start: int, end: int, status: str,
        *, parent: str | None = None,
    ) -> None:
        if (name not in _PHASES or status not in {"completed", "error", "cancelled"}
                or start < self.origin or end < start):
            return
        with self._lock:
            if len(self._spans) < 24:
                self._spans.append({
                    "name": name, "parent": parent, "start_ns": start - self.origin,
                    "end_ns": end - self.origin, "duration_ns": end - start, "status": status,
                })

    @contextmanager
    def measure(self, name: str) -> Iterator[None]:
        start, status = time.perf_counter_ns(), "completed"
        try:
            yield
        except BaseException as error:
            cancelled = isinstance(
                error, (asyncio.CancelledError, GeneratorExit, CodexCancelledError)
            )
            status = "cancelled" if cancelled else "error"
            raise
        finally:
            self.record(name, start, time.perf_counter_ns(), status)

    def snapshot(self, status: str) -> dict[str, Any]:
        """Exclude receipt emission itself; keep audit/consumer/scheduling residual visible."""
        elapsed = time.perf_counter_ns() - self.origin
        with self._lock:
            spans = sorted((dict(span) for span in self._spans), key=lambda span: span["start_ns"])
        covered, last = 0, 0
        for span in spans:
            covered += max(0, span["end_ns"] - max(last, span["start_ns"]))
            last = max(last, span["end_ns"])
        return {
            "schema_version": 1, "clock": "perf_counter_ns", "started_utc": self.started_utc,
            "scope": "adapter_lifecycle_not_model_service_time", "status": status,
            "elapsed_ns": elapsed, "covered_ns": covered,
            "unclassified_ns": elapsed - covered, "spans": spans,
            "runtime_start_detail": "observed" if any(
                span["name"] == "process_boot" for span in spans
            ) else "unavailable",
        }
