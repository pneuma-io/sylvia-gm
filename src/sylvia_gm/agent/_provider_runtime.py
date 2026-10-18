"""Process-lifetime event loop shared by cached Builder and GM provider clients."""

from __future__ import annotations

import asyncio
import threading


class _ProviderAsyncRuntime:
    """Keep pooled HTTP connections on the loop that first opened them."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._loop: asyncio.AbstractEventLoop | None = None

    def loop(self) -> asyncio.AbstractEventLoop:
        with self._lock:
            if self._loop is not None:
                return self._loop
            loop = asyncio.new_event_loop()
            ready = threading.Event()

            def worker() -> None:
                asyncio.set_event_loop(loop)
                ready.set()
                loop.run_forever()

            threading.Thread(
                target=worker,
                name="sylvia-gm-provider-runtime",
                daemon=True,
            ).start()
            ready.wait()
            self._loop = loop
            return loop


_PROVIDER_ASYNC_RUNTIME = _ProviderAsyncRuntime()
