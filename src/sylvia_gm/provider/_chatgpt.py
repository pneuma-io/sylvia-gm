"""Subscription inference through Codex, with Sylvia retaining the tool loop.

Each call uses an ephemeral runtime thread loaded from Sylvia's authoritative
history. At the first Sylvia tool request the runtime is stopped before returning
the call to the existing owner. Codex never executes a Sylvia operation itself.

With ``SYLVIA_CHATGPT_THREAD=stable`` a request that ends in a tool call instead
leaves its interrupted, idle thread open, and the next request that extends exactly
what that thread holds continues it with only the new items. The runtime is idle
while Sylvia executes the call: its turn has ended and nothing starts another.
"""

from __future__ import annotations

import asyncio
import atexit
import json
import os
import threading
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sylvia_gm.provider._base import (
    CacheUsage,
    ModelInfo,
    Provider,
    ProviderResponse,
    ProviderStreamChunk,
    TokenUsage,
    ToolCall,
)
from sylvia_gm.provider._codex_rpc import (
    CodexCancelledError,
    CodexConnection,
    CodexConnectionError,
    add_account_change_listener,
)
from sylvia_gm.provider._context_capacity import resolve_context_capacity
from sylvia_gm.provider._exceptions import (
    AuthenticationError,
    ContextCompactionRequiredError,
    InvalidResponseError,
    ModelCapacityError,
    ModelNotFoundError,
    ProviderConnectionError,
    ProviderError,
    ProviderTimeoutError,
    RateLimitError,
    TokenLimitError,
)
from sylvia_gm.provider._hooks import HookContext
from sylvia_gm.provider._lifecycle import LifecycleTiming
from sylvia_gm.provider._openai_compat import ResponsesTransport

#: A request that ends in a tool call: the runtime reports its token and cache usage only once
#: the tool call resolves or the turn is interrupted, and Sylvia never answers the call. So
#: the adapter lets the runtime record the finished model response for this long, interrupts
#: the turn, and collects the report before it stops the runtime. An interrupt that arrives
#: before the runtime has recorded the response gets no report: at 0.15 s a live fight lost
#: 2 of 32 reports, and the live probe that never missed used 0.3 s.
_USAGE_SETTLE_SECONDS = 0.3
#: How long to wait for that report after the interrupt. It normally arrives at once.
_USAGE_REPORT_SECONDS = 1.0

#: Environment switch, read when a provider is created: "stable" keeps runtime threads.
_THREAD_SWITCH = "SYLVIA_CHATGPT_THREAD"
#: Idle runtime threads kept open at once; the least recently used is closed first.
_KEPT_THREAD_LIMIT = 4
#: A kept thread left unused this long is closed.
_KEPT_THREAD_IDLE_SECONDS = 600.0
#: Errors of a continued thread that a fresh thread may not repeat. Capacity, allowance,
#: context-size and cancellation outcomes would recur or must reach the owner unchanged.
_CONTINUATION_FAILURES = (
    ProviderConnectionError, ModelNotFoundError, AuthenticationError,
)


def app_server_request(
    model_id: str,
    messages: list[dict[str, Any]],
    tools: list[dict[str, Any]] | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    """Project messages into App Server dynamic tools, injected items and base instructions."""
    converted = ResponsesTransport().format_request(model_id, messages, tools, True)
    dynamic = [
        {
            "type": "function",
            "name": tool["name"],
            "description": tool["description"],
            "inputSchema": tool["parameters"],
        }
        for tool in converted.get("tools", [])
    ]
    items = []
    system_instructions: list[str] = []
    for original in converted["input"]:
        item = dict(original)
        # App Server excludes injected system messages; its dedicated field owns them.
        if item.get("role") == "system":
            system_instructions.append(item["content"])
            continue
        if item.get("type") == "function_call":
            item["namespace"] = "sylvia"
        if "role" in item:
            role = item["role"]
            item["type"] = "message"
            item["content"] = [
                {
                    "type": "output_text" if role == "assistant" else "input_text",
                    "text": item["content"],
                }
            ]
        items.append(item)
    return dynamic, items, system_instructions


@dataclass(eq=False)
class _KeptThread:
    """An idle runtime thread whose last turn ended in an interrupted Sylvia tool call."""

    connection: CodexConnection
    home: Path
    model: str
    thread_id: str
    instructions: str
    dynamic_tools: list[dict[str, Any]]
    items: list[dict[str, Any]]
    generation: int
    idle_since: float = field(default_factory=time.monotonic)
    timer: threading.Timer | None = None

    def extended_by(
        self, home: Path, model: str, instructions: str,
        dynamic_tools: list[dict[str, Any]], items: list[dict[str, Any]],
    ) -> bool:
        """True when a request holds everything this thread holds, in order, and more."""
        held = len(self.items)
        return (
            self.home == home and self.model == model
            and self.instructions == instructions and self.dynamic_tools == dynamic_tools
            and len(items) > held and items[:held] == self.items
        )

    def close_quietly(self) -> None:
        _stop_timers([self])
        with suppress(Exception):
            self.connection.close()


def _stop_timers(threads: list[_KeptThread]) -> None:
    for kept in threads:
        if kept.timer is not None:
            kept.timer.cancel()
            kept.timer = None


class _KeptThreads:
    """Process-wide idle runtime threads, keyed by runtime home and matched by content.

    One provider instance serves every session on its model for the life of the owner
    process, and a settings reload replaces it without closing it, so threads are kept
    here rather than on the instance. A thread is removed while a request uses it, so no
    two requests can share one. Closing a child can block, so callers close what these
    methods return outside the lock.
    """

    def __init__(
        self, limit: int = _KEPT_THREAD_LIMIT, idle_seconds: float = _KEPT_THREAD_IDLE_SECONDS,
    ) -> None:
        self.limit = limit
        self.idle_seconds = idle_seconds
        self._lock = threading.Lock()
        self._idle: list[_KeptThread] = []  # least recently used first
        self._generations: dict[Path, int] = {}

    def __len__(self) -> int:
        with self._lock:
            return len(self._idle)

    def generation(self, home: Path) -> int:
        with self._lock:
            return self._generations.get(home, 0)

    def take(
        self, home: Path, model: str, instructions: str,
        dynamic_tools: list[dict[str, Any]], items: list[dict[str, Any]],
    ) -> tuple[_KeptThread | None, list[_KeptThread]]:
        """Remove the thread this request extends furthest, and the threads now to close.

        Threads of another home or idle too long close. Without a match, threads of this
        model whose history starts like this request's are its superseded earlier forms (a
        retry, a changed tool list or instructions) and close; threads that start
        differently may belong to another session and stay for the limit or the idle timer.
        """
        now = time.monotonic()
        with self._lock:
            close = [
                kept for kept in self._idle
                if kept.home != home or now - kept.idle_since >= self.idle_seconds
            ]
            candidates = [kept for kept in self._idle if kept not in close]
            chosen = max(
                (kept for kept in candidates
                 if kept.extended_by(home, model, instructions, dynamic_tools, items)),
                key=lambda kept: len(kept.items), default=None,
            )
            if chosen is None:
                close.extend(
                    kept for kept in candidates
                    if kept.model == model and kept.items[:1] == items[:1]
                )
            self._idle = [kept for kept in candidates if kept is not chosen and kept not in close]
            _stop_timers([*close, *([chosen] if chosen is not None else [])])
        return chosen, close

    def keep(self, kept: _KeptThread) -> list[_KeptThread]:
        """Hold an idle thread; return the threads to close, including it when refused."""
        with self._lock:
            if kept.generation != self._generations.get(kept.home, 0):
                return [kept]
            kept.idle_since = time.monotonic()
            self._idle.append(kept)
            evicted = self._idle[:-self.limit] if self.limit > 0 else list(self._idle)
            self._idle = self._idle[len(evicted):]
            _stop_timers(evicted)
            if kept not in evicted:
                kept.timer = threading.Timer(self.idle_seconds, self._expire, (kept,))
                kept.timer.daemon = True
                kept.timer.start()
        return evicted

    def _expire(self, kept: _KeptThread) -> None:
        with self._lock:
            if kept not in self._idle:
                return
            self._idle.remove(kept)
        kept.close_quietly()

    def release(self, home: Path | None = None, model: str | None = None) -> list[_KeptThread]:
        """Remove idle threads of a home (and model); a busy one of that home is not kept."""
        with self._lock:
            homes = (
                {home} if home is not None
                else {kept.home for kept in self._idle} | set(self._generations)
            )
            for each in homes:
                self._generations[each] = self._generations.get(each, 0) + 1
            released = [
                kept for kept in self._idle
                if (home is None or kept.home == home) and (model is None or kept.model == model)
            ]
            self._idle = [kept for kept in self._idle if kept not in released]
            _stop_timers(released)
        return released

    def close_all(self) -> None:
        for kept in self.release():
            kept.close_quietly()


_KEPT_THREADS = _KeptThreads()


def _close_kept_threads_for(home: Path) -> None:
    """A sign-in or sign-out changes the account a kept child would answer for."""
    for kept in _KEPT_THREADS.release(home.resolve()):
        kept.close_quietly()


def _close_all_kept_threads() -> None:
    _KEPT_THREADS.close_all()


add_account_change_listener(_close_kept_threads_for)
atexit.register(_close_all_kept_threads)


def _continuation_failed(error: BaseException) -> bool:
    """An error a continued thread may cause that a fresh thread can be expected to avoid."""
    return isinstance(error, _CONTINUATION_FAILURES) or type(error) is ProviderError


@dataclass(eq=False)
class _Request:
    """One generation request, as the runtime will see it, and how it ended."""

    model: str
    allowed: frozenset[str]
    items: list[dict[str, Any]]
    instructions: str
    dynamic_tools: list[dict[str, Any]]
    timeout: float
    options: dict[str, object]
    generation: int = 0
    status: str | None = None
    keep: _KeptThread | None = None


class ChatGPTProvider(Provider):
    """Adapt one runtime generation to Sylvia's existing streaming provider interface."""

    def __init__(
        self,
        home: Path,
        model_info: ModelInfo,
        *,
        executable: Path | None = None,
        connection_factory: Callable[..., CodexConnection] = CodexConnection,
    ) -> None:
        super().__init__()
        self.home = home
        self._model_info = model_info
        self._executable = executable
        self._connection_factory = connection_factory
        self._lock = asyncio.Lock()
        self._active: CodexConnection | None = None
        self._last_response = ProviderResponse()
        self._stable_threads = os.environ.get(_THREAD_SWITCH) == "stable"

    def _request_stop(self, connection: CodexConnection) -> None:
        cancel = getattr(connection, "request_stop", None)
        if callable(cancel):
            cancel()

    def _check_stopped(self, connection: CodexConnection) -> None:
        check = getattr(connection, "_check_stopped", None)
        if callable(check):
            check()

    async def _close_owned(self, connection: CodexConnection) -> None:
        """Collect the retained child before propagating repeated caller cancellation."""
        closing = asyncio.create_task(asyncio.to_thread(connection.close))
        interrupted: asyncio.CancelledError | None = None
        while not closing.done():
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError as error:
                interrupted = error
                self._request_stop(connection)
        closing.result()
        if interrupted is not None:
            raise interrupted

    @property
    def model_info(self) -> ModelInfo:
        return self._model_info

    def _observe_context_window(self, value: object) -> None:
        """Retain the runtime's reported window without increasing a configured operating cap."""
        if type(value) is not int or value <= 0:
            return
        model = self._model_info
        observed_at = datetime.now(UTC).isoformat()
        capacity = resolve_context_capacity(
            declared_context_window=model.declared_context_window,
            declared_context_source=model.declared_context_source,
            runtime_context_window=value,
            runtime_context_source="runtime_reported",
            configured_context_window=model.configured_context_window,
            declared_observed_at=model.declared_context_observed_at,
            runtime_observed_at=observed_at,
            configured_observed_at=model.configured_context_observed_at,
        )
        # Wrappers retain this ModelInfo object, so update it rather than replacing it.
        model.runtime_context_window = value
        model.runtime_context_source = "runtime_reported"
        model.runtime_context_observed_at = observed_at
        model.context_observed_at = observed_at
        model.context_window = capacity.effective_context_window
        model.context_window_source = capacity.effective_context_source
        model.configured_runtime_mismatch = capacity.configured_runtime_mismatch

    async def _audit(self, method: str, params: dict[str, Any], *, received: bool = False) -> None:
        """Capture the actual runtime boundary without claiming downstream wire visibility."""
        await self.hooks.fire_on_request(
            HookContext(
                provider_key="chatgpt_subscription",
                model_id=self.model_info.id,
                stream=True,
                request_json={
                    "direction": "receive" if received else "send",
                    "method": method,
                    "params": params,
                    "downstream_model_request": "not_exposed_by_runtime",
                },
            )
        )

    def _capture_usage(
        self, response: ProviderResponse, params: dict[str, Any],
        thread_id: str | None, turn_id: str | None,
    ) -> None:
        """Use the latest owned last-request report; cumulative thread totals are not additive."""
        if (not thread_id or not turn_id or params.get("threadId") != thread_id
                or params.get("turnId") != turn_id):
            return
        token_usage = params.get("tokenUsage")
        if not isinstance(token_usage, dict):
            return
        self._observe_context_window(token_usage.get("modelContextWindow"))
        last = token_usage.get("last")
        if not isinstance(last, dict):
            return

        def count(key: str) -> int | None:
            value = last.get(key)
            return value if type(value) is int and value >= 0 else None

        prompt, output, total = (
            count(key) for key in ("inputTokens", "outputTokens", "totalTokens")
        )
        if prompt is None or output is None or total is None:
            return
        response.usage = TokenUsage(prompt, output, total, count("reasoningOutputTokens"))
        response.usage_reported = True
        read, write = count("cachedInputTokens"), count("cacheWriteInputTokens")
        response.cache_usage = CacheUsage(
            cache_read_tokens=read, cache_write_tokens=write,
            cache_miss_tokens=None if read is None else max(0, prompt - read),
            total_prompt_tokens=prompt,
        )

    async def _await_usage_after_tool_call(
        self, connection: CodexConnection, response: ProviderResponse,
        thread_id: str | None, turn_id: str | None,
    ) -> str:
        """Collect this request's usage report before the runtime is stopped.

        The runtime holds the report until the tool call resolves, and Sylvia never sends a
        result, so without this the report is lost for every request that ends in a tool
        call. Interrupting the turn makes the runtime send the report and end the turn; it
        starts no further model request. The whole step is bounded and never fails the
        request: a runtime that rejects the interrupt, sends no report or stops is simply a
        request without reported usage, as before.

        Returns "ended" when the interrupted turn's end was seen, "interrupted" when the
        interrupt was accepted, "skipped" when none was sent and "failed" when the runtime
        failed; only a kept thread needs to know.
        """
        if response.usage_reported or not thread_id or not turn_id:
            return "skipped"
        try:
            await asyncio.sleep(_USAGE_SETTLE_SECONDS)
            await self._audit("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
            await asyncio.to_thread(
                connection.call, "turn/interrupt", {"threadId": thread_id, "turnId": turn_id},
            )
            deadline = time.monotonic() + _USAGE_REPORT_SECONDS
            while not response.usage_reported and time.monotonic() < deadline:
                event = await asyncio.to_thread(connection.next_event)
                if event is None:
                    continue
                method, params = event.get("method"), event.get("params")
                if not isinstance(params, dict):
                    continue
                if method == "thread/tokenUsage/updated":
                    await self._audit(method, params, received=True)
                    self._capture_usage(response, params, thread_id, turn_id)
                elif method == "turn/completed" and params.get("threadId") in {None, thread_id}:
                    # The interrupted turn has ended; its report, if any, came before this.
                    # Recorded so a request left without usage can be told from a slow report.
                    await self._audit(method, params, received=True)
                    return "ended"
                else:
                    await asyncio.sleep(0)
        except CodexCancelledError:
            raise
        except CodexConnectionError:
            return "failed"
        return "interrupted"

    async def _await_interrupted_turn_end(
        self, connection: CodexConnection, response: ProviderResponse,
        thread_id: str, turn_id: str,
    ) -> bool:
        """Wait, bounded, until the interrupted turn ends, so a further turn can start.

        A turn that has not ended is still waiting for the call's result; such a thread is
        closed, not kept. A late usage report for this turn is still recorded.
        """
        deadline = time.monotonic() + _USAGE_REPORT_SECONDS
        try:
            while time.monotonic() < deadline:
                event = await asyncio.to_thread(connection.next_event)
                if event is None:
                    continue
                method, params = event.get("method"), event.get("params")
                if not isinstance(params, dict):
                    continue
                if method == "thread/tokenUsage/updated":
                    await self._audit(method, params, received=True)
                    self._capture_usage(response, params, thread_id, turn_id)
                elif method == "turn/completed" and params.get("threadId") in {None, thread_id}:
                    await self._audit(method, params, received=True)
                    return True
                else:
                    await asyncio.sleep(0)
        except CodexCancelledError:
            raise
        except CodexConnectionError:
            return False
        return False

    async def _open_thread(
        self, connection: CodexConnection, request: _Request, timing: LifecycleTiming,
    ) -> str:
        """Start an ephemeral thread and load Sylvia's whole history into it."""
        start = {
            "model": request.model,
            "allowProviderModelFallback": False,
            "ephemeral": True,
            "environments": [],
            "selectedCapabilityRoots": [],
            "baseInstructions": request.instructions,
            "approvalPolicy": "never",
            "dynamicTools": request.dynamic_tools,
        }
        await self._audit("thread/start", start)
        with timing.measure("thread_start"):
            thread = await asyncio.to_thread(connection.call, "thread/start", start)
        await self._audit("thread/start/result", thread, received=True)
        actual_model = thread.get("model")
        if isinstance(actual_model, str) and actual_model != request.model:
            raise ModelNotFoundError("ChatGPT did not select the requested model.")
        thread_id: str = thread["thread"]["id"]
        await self._inject(connection, thread_id, request.items, timing)
        return thread_id

    async def _continue_thread(
        self, connection: CodexConnection, kept: _KeptThread, request: _Request,
        timing: LifecycleTiming,
    ) -> None:
        """Add only what this request appends to a kept thread; the next turn reads it all.

        The appended items start with the model's own call and its result, so the model
        sees its call answered rather than asking again.
        """
        drain = getattr(connection, "notifications", None)
        if callable(drain):
            # Events of the finished turn must not be read as events of the next one.
            drain()
        await self._inject(connection, kept.thread_id, request.items[len(kept.items):], timing)

    async def _inject(
        self, connection: CodexConnection, thread_id: str, items: list[dict[str, Any]],
        timing: LifecycleTiming,
    ) -> None:
        inject = {"threadId": thread_id, "items": items}
        await self._audit("thread/inject_items", inject)
        with timing.measure("history_injection"):
            injected = await asyncio.to_thread(
                connection.call, "thread/inject_items", inject,
            )
        await self._audit("thread/inject_items/result", injected, received=True)

    async def _take_kept_thread(self, request: _Request) -> _KeptThread | None:
        home = self.home.resolve()
        request.generation = _KEPT_THREADS.generation(home)
        kept, stale = _KEPT_THREADS.take(
            home, request.model, request.instructions, request.dynamic_tools, request.items,
        )
        try:
            for each in stale:
                await self._close_kept(each)
        except BaseException:
            # Taken from the pool, so nothing else would ever close it.
            if kept is not None:
                await self._close_kept(kept)
            raise
        return kept

    async def _keep_thread(self, kept: _KeptThread) -> bool:
        stale = _KEPT_THREADS.keep(kept)
        for each in stale:
            await self._close_kept(each)
        return kept not in stale

    async def _close_kept(self, kept: _KeptThread) -> None:
        """Collect an idle kept child; its failure to close never fails a request."""
        with suppress(Exception):
            await self._close_owned(kept.connection)

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60,
        **kwargs: object,
    ) -> ProviderResponse:
        async for _ in self.generate_stream(messages, tools, timeout, **kwargs):
            pass
        return self._last_response

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        async with self._lock:
            timing = LifecycleTiming()
            dynamic, items, system_instructions = app_server_request(
                self.model_info.wire_model_id, messages, tools,
            )
            request = _Request(
                model=self.model_info.wire_model_id,
                allowed=frozenset(tool["name"] for tool in dynamic),
                items=items,
                instructions="\n\n".join(system_instructions)
                or "Follow the supplied Sylvia instructions and history.",
                dynamic_tools=[
                    {
                        "type": "namespace",
                        "name": "sylvia",
                        "description": "Authorized Sylvia operations.",
                        "tools": dynamic,
                    }
                ]
                if dynamic
                else [],
                timeout=timeout,
                options=kwargs,
            )
            kept = await self._take_kept_thread(request) if self._stable_threads else None
            reuse: dict[str, object] = {
                "continued": kept is not None,
                "held_items": len(kept.items) if kept is not None else 0,
                "injected_items": len(items) - (len(kept.items) if kept is not None else 0),
                "retried_fresh": False,
                "kept": False,
            }
            finished = False
            try:
                while True:
                    attempt = self._attempt(request, timing, kept)
                    yielded = False
                    try:
                        async for chunk in attempt:
                            yielded = True
                            yield chunk
                        break
                    except Exception as error:
                        # Retry once, on a fresh thread, only before the caller saw anything.
                        if kept is None or yielded or not _continuation_failed(error):
                            raise
                        kept = None
                        reuse.update(
                            continued=False, held_items=0, injected_items=len(items),
                            retried_fresh=True,
                        )
                    finally:
                        await attempt.aclose()
                finished = True
            finally:
                if request.keep is not None:
                    held, request.keep = request.keep, None
                    # A stream closed or cancelled after its last chunk still keeps nothing.
                    if finished:
                        reuse["kept"] = await self._keep_thread(held)
                    else:
                        await self._close_kept(held)
                if request.status is not None:
                    receipt = timing.snapshot(request.status)
                    if self._stable_threads:
                        await self._audit("sylvia/thread_reuse", reuse)
                        receipt["thread_reuse"] = reuse
                    await self._audit("sylvia/provider_lifecycle", receipt)

    async def _attempt(
        self, request: _Request, timing: LifecycleTiming, kept: _KeptThread | None,
    ) -> AsyncGenerator[ProviderStreamChunk, None]:
        """Run one turn on a fresh runtime thread, or on the kept thread it continues.

        The child is closed when the attempt ends, unless the turn ended in a tool call with
        stable threads on; ``request.keep`` then names the thread for the caller to keep.
        """
        kwargs = request.options
        timeout = request.timeout
        allowed = request.allowed
        request.status = None
        if kept is None:
            connection = self._connection_factory(
                self.home,
                self._executable,
                dynamic_tools=allowed,
            )
        else:
            connection = kept.connection
        self._active = connection
        observe = getattr(connection, "set_lifecycle_observer", None)
        if callable(observe):
            observe(lambda name, start, end, status: timing.record(
                name, start, end, status, parent="runtime_start",
            ))
        response = ProviderResponse(model=self.model_info.wire_model_id, usage_reported=False)
        text_parts: list[str] = []
        thread_id: str | None = None
        turn_id: str | None = None
        status = "error"
        request.status = status
        stream_start: int | None = None
        keep = False

        def end_stream(outcome: str) -> None:
            nonlocal stream_start
            if stream_start is not None:
                timing.record("turn_stream", stream_start, time.perf_counter_ns(), outcome)
                stream_start = None

        async def stop() -> None:
            with timing.measure("shutdown"):
                await self._close_owned(connection)

        try:
            if kept is None:
                with timing.measure("runtime_start"):
                    await asyncio.to_thread(connection.start)
            await self._audit(
                "sylvia/runtime_controls",
                {
                    "managed_by_runtime": ["max_tokens", "temperature", "tool_choice"],
                    "application_requested": {
                        key: kwargs[key]
                        for key in ("max_tokens", "temperature", "tool_choice")
                        if key in kwargs
                    },
                },
            )
            with timing.measure("account_read"):
                account = await asyncio.to_thread(
                    connection.call, "account/read", {"refreshToken": False}
                )
            if (
                not isinstance(account.get("account"), dict)
                or account["account"].get("type") != "chatgpt"
            ):
                raise AuthenticationError(
                    "Sign in to ChatGPT in Sylvia Settings to use this model."
                )
            if kept is None:
                thread_id = await self._open_thread(connection, request, timing)
            else:
                thread_id = kept.thread_id
                await self._continue_thread(connection, kept, request, timing)
            turn: dict[str, Any] = {
                "threadId": thread_id,
                "input": [],
                "environments": [],
                "model": self.model_info.wire_model_id,
            }
            if kwargs.get("reasoning_effort") is not None:
                turn["effort"] = kwargs["reasoning_effort"]
            if kwargs.get("service_tier") is not None:
                # A tier id advertised by model/list; absent keeps the runtime default.
                turn["serviceTier"] = kwargs["service_tier"]
            await self._audit("turn/start", turn)
            with timing.measure("turn_start"):
                started = await asyncio.to_thread(connection.call, "turn/start", turn)
            await self._audit("turn/start/result", started, received=True)
            turn_id = started["turn"]["id"]
            last_event = time.monotonic()
            stream_start = time.perf_counter_ns()
            while True:
                event = await asyncio.to_thread(connection.next_event)
                if event is None:
                    if time.monotonic() - last_event > timeout:
                        raise ProviderTimeoutError(
                            "The ChatGPT response stopped making progress."
                        )
                    continue
                method, params = event.get("method"), event.get("params", {})
                if not isinstance(params, dict) or params.get("threadId") not in {
                    None,
                    thread_id,
                }:
                    continue
                if params.get("turnId") not in {None, turn_id}:
                    continue
                last_event = time.monotonic()
                # Account notifications are settings metadata, never campaign audit content.
                if isinstance(method, str) and not method.startswith(
                    ("account/", "remoteControl/")
                ):
                    await self._audit(method, params, received=True)
                self._check_stopped(connection)
                if method == "model/rerouted":
                    raise ModelNotFoundError(
                        "ChatGPT routed to a different model; Sylvia stopped the response."
                    )
                if (
                    method == "item/started"
                    and isinstance(params.get("item"), dict)
                    and params["item"].get("type") == "contextCompaction"
                ):
                    raise ContextCompactionRequiredError(
                        "ChatGPT requested context compaction. Sylvia must prepare a "
                        "smaller working context; saved history is retained."
                    )
                if method == "item/agentMessage/delta":
                    delta = params.get("delta")
                    if isinstance(delta, str):
                        text_parts.append(delta)
                        yield ProviderStreamChunk(content=delta)
                elif method in {"item/reasoning/textDelta", "item/reasoning/summaryTextDelta"}:
                    delta = params.get("delta")
                    if isinstance(delta, str):
                        yield ProviderStreamChunk(reasoning_content=delta)
                elif method == "item/tool/call":
                    name, call_id = params.get("tool"), params.get("callId")
                    if (
                        not isinstance(name, str)
                        or name not in allowed
                        or params.get("namespace") != "sylvia"
                        or not isinstance(call_id, str)
                    ):
                        raise InvalidResponseError(
                            "ChatGPT requested an unavailable Sylvia operation."
                        )
                    arguments = json.dumps(
                        params.get("arguments"), ensure_ascii=True, allow_nan=False
                    )
                    response.tool_calls = [ToolCall(id=call_id, name=name, arguments=arguments)]
                    response.finish_reason = "tool_calls"
                    interrupt = await self._await_usage_after_tool_call(
                        connection, response, thread_id, turn_id,
                    )
                    # A kept thread must have ended its turn; one still awaiting the call's
                    # result is stopped like any other.
                    keep = self._stable_threads and (
                        interrupt == "ended" or (
                            interrupt == "interrupted"
                            and await self._await_interrupted_turn_end(
                                connection, response, thread_id, turn_id,
                            )
                        )
                    )
                    # Stop the runtime before the owner can execute this returned call; a kept
                    # runtime has ended its turn and starts nothing until it is continued.
                    end_stream("completed")
                    if not keep:
                        await stop()
                    self._check_stopped(connection)
                    yield ProviderStreamChunk(
                        tool_call_delta={
                            "index": 0,
                            "id": call_id,
                            "type": "function",
                            "function": {"name": name, "arguments": arguments},
                        }
                    )
                    break
                elif method == "thread/tokenUsage/updated":
                    self._capture_usage(response, params, thread_id, turn_id)
                elif method == "turn/completed":
                    completed = params.get("turn", {})
                    if completed.get("status") != "completed":
                        error = completed.get("error") or {}
                        code = error.get("codexErrorInfo") if isinstance(error, dict) else None
                        if code == "serverOverloaded":
                            raise ModelCapacityError(
                                "The selected model is at capacity. Saved work is kept. "
                                "Try again later or choose another model for this chat."
                            )
                        if code == "usageLimitExceeded":
                            raise RateLimitError(
                                "ChatGPT subscription allowance is unavailable. "
                                "Try after it resets."
                            )
                        if code == "contextWindowExceeded":
                            raise TokenLimitError(
                                "ChatGPT rejected this request because its context window "
                                "was exceeded. Saved conversation and completed work remain."
                            )
                        raise ProviderError(
                            "ChatGPT did not complete this response. Saved work is retained."
                        )
                    break
            # Tool admission has already stopped this owned child. Preserve any usage
            # notification received around shutdown without starting another model call.
            # A kept child stays open; draining also clears its queue for the next turn.
            end_stream("completed")
            if not keep:
                await stop()
            drain = getattr(connection, "notifications", None)
            if callable(drain):
                for pending in drain():
                    if (pending.get("method") == "thread/tokenUsage/updated"
                            and isinstance(pending.get("params"), dict)):
                        self._capture_usage(response, pending["params"], thread_id, turn_id)
            response.content = "".join(text_parts) or None
            await self.hooks.fire_on_response(
                HookContext(
                    provider_key="chatgpt_subscription",
                    model_id=self.model_info.id,
                    response=response,
                    stream=True,
                )
            )
            self._check_stopped(connection)
            self._last_response = response
            status = "completed"
            request.status = status
            if keep and thread_id is not None:
                request.keep = _KeptThread(
                    connection=connection,
                    home=self.home.resolve(),
                    model=request.model,
                    thread_id=thread_id,
                    instructions=request.instructions,
                    dynamic_tools=request.dynamic_tools,
                    items=request.items,
                    generation=request.generation,
                )
            yield ProviderStreamChunk(finish_reason=response.finish_reason)
        except (asyncio.CancelledError, GeneratorExit):
            if status != "completed":
                status = "cancelled"
                request.status = status
                self._request_stop(connection)
            raise
        except CodexCancelledError:
            if status != "completed":
                status = "cancelled"
                request.status = status
            raise asyncio.CancelledError("The ChatGPT response was cancelled.") from None
        except CodexConnectionError as error:
            raise ProviderConnectionError(str(error)) from None
        finally:
            # Closing the owned child cancels unresolved runtime calls and ephemeral history.
            end_stream(status)
            try:
                if request.keep is None:
                    await stop()
            finally:
                self._active = None

    async def close(self) -> None:
        connection = self._active
        if connection is not None:
            self._request_stop(connection)
            await self._close_owned(connection)
        if self._stable_threads:
            for kept in _KEPT_THREADS.release(self.home.resolve(), self.model_info.wire_model_id):
                await self._close_kept(kept)
