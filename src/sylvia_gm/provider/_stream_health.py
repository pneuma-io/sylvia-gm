"""Stream health monitoring — per-chunk timeout for SSE streams.

Wraps an async stream iterator with a configurable health interval.
When no chunk arrives within the interval, ``StaleConnectionError`` is raised.
"""

from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import AsyncIterator

from sylvia_gm.provider._base import ProviderStreamChunk
from sylvia_gm.provider._exceptions import StaleConnectionError

logger = logging.getLogger(__name__)


class StreamHealthMonitor:
    """Wraps an async stream iterator with per-chunk health checks.

    Each ``__anext__()`` call is bounded by *health_interval* seconds.
    If no chunk arrives within that window the stream is declared dead
    and ``StaleConnectionError`` is raised.

    This is an application-layer check (SSE chunk arrival), not a
    transport-layer check (TCP keepalive / httpx read timeout).
    """

    def __init__(
        self,
        stream: AsyncIterator[ProviderStreamChunk],
        health_interval: float = 60.0,
        provider_name: str = "unknown",
    ) -> None:
        self._stream = stream
        self._health_interval = health_interval
        self._provider_name = provider_name
        self._chunk_count = 0
        self._last_chunk_at = time.monotonic()

    def __aiter__(self) -> StreamHealthMonitor:
        return self

    async def __anext__(self) -> ProviderStreamChunk:
        try:
            chunk = await asyncio.wait_for(
                self._stream.__anext__(),
                timeout=self._health_interval,
            )
            self._chunk_count += 1
            self._last_chunk_at = time.monotonic()
            return chunk
        except TimeoutError:
            logger.warning(
                "Stream health check failed for %s after %.0fs (%d chunks received)",
                self._provider_name,
                self._health_interval,
                self._chunk_count,
            )
            raise StaleConnectionError(
                f"Stream from {self._provider_name} dead: "
                f"no chunks for {self._health_interval}s "
                f"({self._chunk_count} received)"
            ) from None

    @property
    def chunk_count(self) -> int:
        """Number of chunks received so far."""
        return self._chunk_count

    @property
    def idle_seconds(self) -> float:
        """Seconds since the last chunk arrived."""
        return time.monotonic() - self._last_chunk_at
