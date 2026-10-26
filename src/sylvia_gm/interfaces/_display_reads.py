"""Bounded workers for expensive, cancellable desktop projections in the sole owner."""

from __future__ import annotations

import threading
from collections import OrderedDict
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor


class DisplayReads:
    """Cancel queued presentation work without interrupting game actions or admitted reads."""

    def __init__(self, cancelled_reply: Callable[[str], None]) -> None:
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="sylvia-display")
        self._lock = threading.Lock()
        self._pending: dict[Future[None], tuple[str, str | None]] = {}
        self._cancelled: OrderedDict[str, None] = OrderedDict()
        self._cancelled_reply = cancelled_reply

    def submit(self, request_id: str, load_id: str | None, execute: Callable[[], None]) -> None:
        def run() -> None:
            with self._lock:
                cancelled = load_id in self._cancelled
            if cancelled:
                self._cancelled_reply(request_id)
            else:
                execute()

        with self._lock:
            future = self._executor.submit(run)
            self._pending[future] = (request_id, load_id)

        def finished(result: Future[None]) -> None:
            with self._lock:
                self._pending.pop(result, None)
            if result.cancelled():
                self._cancelled_reply(request_id)

        future.add_done_callback(finished)

    def cancel(self, load_id: str) -> None:
        with self._lock:
            self._cancelled[load_id] = None
            self._cancelled.move_to_end(load_id)
            while len(self._cancelled) > 128:
                self._cancelled.popitem(last=False)
            queued = [
                future for future, (_, identity) in self._pending.items() if identity == load_id
            ]
        # Future callbacks run synchronously during cancellation, outside the bookkeeping lock.
        for future in queued:
            future.cancel()

    def close(self) -> None:
        self._executor.shutdown(wait=True, cancel_futures=True)

    def idle(self) -> bool:
        """Preparation must wait for admitted projections rather than close their authority."""
        with self._lock:
            return not self._pending
