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
import logging
import os
import threading
import time
from collections.abc import AsyncGenerator, AsyncIterator, Callable, Iterable, Mapping
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

#: Environment switch, read when a provider is created.
#: "fresh" starts a new runtime process and thread for every request and interrupts the turn
#: at a tool call (the behaviour before kept threads). "answer" keeps the thread and leaves
#: the turn waiting at a tool call; the next request, which brings the call's result,
#: answers the call inside that same turn. "stable" keeps the thread but interrupts the turn
#: at a tool call and continues with a further turn (experimental). "answer-draft" is
#: "answer" that also answers when the owner dropped the text the model streamed before its
#: call, so the thread still holds that draft (experimental).
#: Unset, or any other value: "answer" with the context ledger, "fresh" without it, so
#: ``SYLVIA_GM_CONTEXT_MODE=legacy`` alone restores the whole previous behaviour.
#: The mode applies only to requests the context ledger built (``LEDGER_REQUEST``); every
#: other request takes the fresh path whatever the switch says.
_THREAD_SWITCH = "SYLVIA_CHATGPT_THREAD"
_FRESH_THREADS = "fresh"
_DEFAULT_THREAD_MODE = "answer"
_THREAD_MODES = {"stable": "interrupt", "answer": "answer", "answer-draft": "answer"}
_DRAFT_KEEPING_VALUE = "answer-draft"


def thread_setting(environ: Mapping[str, str] | None = None) -> str:
    """The ``SYLVIA_CHATGPT_THREAD`` value that applies: one of ``_THREAD_MODES`` or fresh."""
    value = (os.environ if environ is None else environ).get(_THREAD_SWITCH, "")
    setting = value.strip().casefold()
    if setting == _FRESH_THREADS or setting in _THREAD_MODES:
        return setting
    from sylvia_gm.agent._context_ledger import LEDGER_MODE, context_mode

    # A kept thread only pays with requests that extend the previous one (the ledger).
    ledger = context_mode(environ, transport="codex_app_server") == LEDGER_MODE
    return _DEFAULT_THREAD_MODE if ledger else _FRESH_THREADS


def _ledger_request() -> str | bool:
    """Whether the context ledger built this request. Reading it clears it: one request."""
    from sylvia_gm.agent._context_ledger import LEDGER_REQUEST

    marked = LEDGER_REQUEST.get()
    LEDGER_REQUEST.set(False)
    return marked

#: Idle runtime threads kept open at once; the least recently used is closed first.
_KEPT_THREAD_LIMIT = 4
#: A kept thread left unused this long is closed.
_KEPT_THREAD_IDLE_SECONDS = 600.0
#: The same for a thread whose turn is still open, waiting for a call's result. A GM turn
#: ends with such a call, so the next player message answers it: the wait is the player's
#: reading and typing time. Measured: the runtime keeps an unanswered call open for at least
#: 30 minutes, and the provider's cache retention is 30 minutes, so waiting longer would
#: only hold the process. A stopped turn does not wait for this (``release_idle``).
_PENDING_TURN_IDLE_SECONDS = 1800.0
#: Abandoned threads' usage reports held until a request of their runtime home records them.
_ABANDONED_REPORT_LIMIT = 64


def _reported_usage(params: object) -> tuple[TokenUsage, CacheUsage] | None:
    """Parse the runtime's last response, never its cumulative thread total."""
    token_usage = params.get("tokenUsage") if isinstance(params, dict) else None
    last = token_usage.get("last") if isinstance(token_usage, dict) else None
    if not isinstance(last, dict):
        return None

    def count(key: str) -> int | None:
        value = last.get(key)
        return value if type(value) is int and value >= 0 else None

    prompt, output, total = count("inputTokens"), count("outputTokens"), count("totalTokens")
    if prompt is None or output is None or total is None:
        return None
    read = count("cachedInputTokens")
    return (
        TokenUsage(prompt, output, total, count("reasoningOutputTokens")),
        CacheUsage(cache_read_tokens=read, cache_write_tokens=count("cacheWriteInputTokens"),
                   cache_miss_tokens=None if read is None else max(0, prompt - read),
                   total_prompt_tokens=prompt),
    )


def _usage_figures(usage: TokenUsage, cache: CacheUsage) -> dict[str, object]:
    return {"input": usage.prompt_tokens, "cached": cache.cache_read_tokens,
            "output": usage.completion_tokens}


#: Events of a waiting turn that show the runtime went on without the call's answer.
_TURN_MOVED_ON = frozenset({
    "turn/completed", "item/tool/call", "item/agentMessage/delta",
    "item/reasoning/textDelta", "item/reasoning/summaryTextDelta", "error",
})
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


class _KeptThreadMismatchError(ProviderConnectionError):
    """A kept thread cannot serve this request; a fresh thread must. ``reason`` is a code."""

    def __init__(self, reason: str, message: str) -> None:
        super().__init__(message)
        self.reason = reason


class _AnswerInPlaceError(_KeptThreadMismatchError):
    """What the next request appends cannot be delivered as the pending call's answer."""


@dataclass
class _PendingCall:
    """A Sylvia tool call the runtime is still waiting on, inside an open turn."""

    request_id: object
    call_id: str
    turn_id: str
    #: The runtime has not yet reported the usage of the response that made this call.
    usage_owed: bool = True
    exchange_id: str | None = None
    model: str = ""
    listeners: list[Callable[[ProviderResponse], None]] = field(default_factory=list)

    def credit(self, usage: TokenUsage, cache: CacheUsage) -> dict[str, object]:
        self.usage_owed = False
        report = ProviderResponse(model=self.model, usage=usage, cache_usage=cache,
                                  usage_reported=True)
        for listener in self.listeners:
            try:
                listener(report)
            except Exception:
                logging.getLogger(__name__).exception("Late usage listener failed")
        figures = _usage_figures(usage, cache)
        if self.exchange_id is not None:
            figures["exchange_id"] = self.exchange_id
        return figures


def _item_text(item: dict[str, Any]) -> str:
    content = item.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(part.get("text", "")) for part in content if isinstance(part, dict)
        )
    return ""


def _strings(value: object) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [text for item in value.values() for text in _strings(item)]
    if isinstance(value, list):
        return [text for item in value for text in _strings(item)]
    return []


def _call_tail(
    tail: list[dict[str, Any]], call_id: str, spoken: str,
    error: type[_KeptThreadMismatchError] = _KeptThreadMismatchError,
    dropped_draft_ok: bool = False,
) -> tuple[dict[str, Any], dict[str, Any], list[dict[str, Any]]]:
    """Sylvia's copy of the kept thread's call, its result and what follows, from a tail.

    A kept thread ends with what the model streamed (``spoken``) and its call ``call_id``.
    A request continues it only when it appends exactly that: the same text (absent when
    nothing was streamed, and never absent when something was: the owner drops text it does
    not keep), the call, and the call's result. Otherwise the thread would hold something
    the request does not, and a fresh thread must serve it.
    """
    index = 0
    text = ""
    while index < len(tail) and (
        tail[index].get("type") == "reasoning"
        or (tail[index].get("type") == "message" and tail[index].get("role") == "assistant")
    ):
        if tail[index].get("type") == "message":
            text += _item_text(tail[index])
        index += 1
    if len(tail) < index + 2:
        raise error("no_call_result", "The request does not answer the kept tool call.")
    call, output = tail[index], tail[index + 1]
    if (
        call.get("type") != "function_call" or call.get("call_id") != call_id
        or output.get("type") != "function_call_output" or output.get("call_id") != call_id
    ):
        raise error("no_call_result", "The request does not answer the kept tool call.")
    if spoken and not text:
        # The owner drops the text of a round whose call was not narrative.
        if not dropped_draft_ok:
            raise error("model_text_dropped", "The request dropped the model's own text.")
    elif text != spoken:
        raise error("model_text_differs", "The request does not hold the model's own text.")
    return call, output, tail[index + 2:]


def _in_place_answer(
    tail: list[dict[str, Any]], call_id: str, spoken: str = "", dropped_draft_ok: bool = False,
) -> tuple[str, list[str]]:
    """The pending call's result and the user-role texts that follow it, from a request's tail.

    After the call and its result (``_call_tail``) only user-role messages may follow. An
    assistant message after the result is accepted, and not sent, only when it is exactly
    one text value of the result or of the call's arguments: that is Sylvia's copy of
    narration the model wrote in the call. Anything else cannot be delivered into an open
    turn.
    """
    call, output, rest = _call_tail(
        tail, call_id, spoken, _AnswerInPlaceError, dropped_draft_ok,
    )
    raw = output.get("output")
    result = raw if isinstance(raw, str) else json.dumps(raw, ensure_ascii=False)
    known: list[str] = [result, str(call.get("arguments", ""))]
    for source in (result, call.get("arguments")):
        if isinstance(source, str):
            with suppress(ValueError):
                known.extend(_strings(json.loads(source)))
    texts: list[str] = []
    for item in rest:
        if item.get("type") != "message":
            raise _AnswerInPlaceError(
                "tail_not_deliverable", "The request adds more than messages after the result.",
            )
        text = _item_text(item)
        if item.get("role") == "user":
            texts.append(text)
        elif item.get("role") == "assistant" and text and text in known:
            continue
        else:
            raise _AnswerInPlaceError(
                "tail_not_deliverable", "The request adds a message the open turn cannot take.",
            )
    return result, texts


@dataclass(eq=False)
class _KeptThread:
    """An idle runtime thread that a later request may continue.

    Its last turn either ended in an interrupted Sylvia tool call, or (``pending``) is still
    open, waiting for that call's result.
    """

    connection: CodexConnection
    home: Path
    model: str
    thread_id: str
    instructions: str
    dynamic_tools: list[dict[str, Any]]
    items: list[dict[str, Any]]
    generation: int
    #: The call the thread's last response made, and the text it streamed before it.
    call_id: str = ""
    spoken: str = ""
    #: The reasoning effort and speed tier the thread's current turn was started with.
    effort: object = None
    tier: object = None
    pending: _PendingCall | None = None
    #: The number of the request whose response made the pending call (``next_ordinal``).
    ordinal: int = 0
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

    def collect_owed_usage(self) -> dict[str, object] | None:
        """End a waiting turn whose response's usage is unreported, and return that report.

        The runtime reports a response's usage when its call is answered, or when its turn is
        interrupted. A waiting turn that will not be answered is interrupted, so the report
        is not lost with the thread. Bounded; never raises.
        """
        pending = self.pending
        if pending is None or not pending.usage_owed:
            return None
        pending.usage_owed = False
        identity = {"threadId": self.thread_id, "turnId": pending.turn_id}
        try:
            self.connection.call("turn/interrupt", identity, timeout=_USAGE_REPORT_SECONDS * 5)
            deadline = time.monotonic() + _USAGE_REPORT_SECONDS
            while time.monotonic() < deadline:
                event = self.connection.next_event(timeout=0.1)
                params = event.get("params") if isinstance(event, dict) else None
                if (
                    event is not None and event.get("method") == "thread/tokenUsage/updated"
                    and isinstance(params, dict) and params.get("threadId") == self.thread_id
                    and params.get("turnId") == pending.turn_id
                ):
                    parsed = _reported_usage(params)
                    if parsed is not None:
                        return pending.credit(*parsed)
        except Exception:  # noqa: BLE001 - a report that cannot be had is an explained miss
            return None
        return None

    def close_quietly(self, pool: _KeptThreads | None = None, *, collect: bool = True) -> None:
        _stop_timers([self])
        figures = self.collect_owed_usage() if collect else None
        if figures is not None and pool is not None:
            pool.abandon(self.home, self.ordinal, figures)
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
        pending_idle_seconds: float = _PENDING_TURN_IDLE_SECONDS,
    ) -> None:
        self.limit = limit
        self.idle_seconds = idle_seconds
        self.pending_idle_seconds = pending_idle_seconds
        self._lock = threading.Lock()
        self._idle: list[_KeptThread] = []  # least recently used first
        self._generations: dict[Path, int] = {}
        self._ordinals: dict[Path, int] = {}
        self._abandoned: list[tuple[Path, int, dict[str, object]]] = []

    def __len__(self) -> int:
        with self._lock:
            return len(self._idle)

    def generation(self, home: Path) -> int:
        with self._lock:
            return self._generations.get(home, 0)

    def next_ordinal(self, home: Path, *, take: bool = True) -> int:
        """Number this home's requests in order, so a late report names its request.

        ``take=False`` only says which number the next request will get.
        """
        with self._lock:
            ordinal = self._ordinals.get(home, 0) + 1
            if take:
                self._ordinals[home] = ordinal
            return ordinal

    def abandon(self, home: Path, ordinal: int, figures: dict[str, object]) -> None:
        """Hold the usage report of a thread closed without its call being answered."""
        with self._lock:
            self._abandoned.append((home, ordinal, figures))
            del self._abandoned[:-_ABANDONED_REPORT_LIMIT]

    def take_abandoned(self, home: Path, ordinal: int) -> list[dict[str, object]]:
        """This home's held reports, each saying how many requests before *ordinal* it is."""
        with self._lock:
            mine = [entry for entry in self._abandoned if entry[0] == home]
            self._abandoned = [entry for entry in self._abandoned if entry[0] != home]
        return [
            {"response": "previous" if ordinal - made == 1 else "earlier",
             "requests_ago": ordinal - made, "closed_unanswered": True, **figures}
            for _home, made, figures in mine
        ]

    def idle_limit(self, kept: _KeptThread) -> float:
        if kept.pending is None:
            return self.idle_seconds
        return self.pending_idle_seconds

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
                if kept.home != home or now - kept.idle_since >= self.idle_limit(kept)
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
                kept.timer = threading.Timer(self.idle_limit(kept), self._expire, (kept,))
                kept.timer.daemon = True
                kept.timer.start()
        return evicted

    def _expire(self, kept: _KeptThread) -> None:
        with self._lock:
            if kept not in self._idle:
                return
            self._idle.remove(kept)
        kept.close_quietly(self)

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
        """Close every kept thread at once, at process exit.

        No later request can record a waiting turn's usage then, so none is collected: the
        app closes without waiting on the runtime.
        """
        for kept in self.release():
            kept.close_quietly(self, collect=False)


_KEPT_THREADS = _KeptThreads()


def _close_kept_threads_for(home: Path) -> None:
    """A sign-in or sign-out changes the account a kept child would answer for."""
    for kept in _KEPT_THREADS.release(home.resolve()):
        kept.close_quietly(_KEPT_THREADS)


def _close_all_kept_threads() -> None:
    _KEPT_THREADS.close_all()


def kept_thread_models() -> frozenset[str]:
    """The models that kept threads are held for, in any home."""
    with _KEPT_THREADS._lock:  # noqa: SLF001
        return frozenset(kept.model for kept in _KEPT_THREADS._idle)  # noqa: SLF001


def release_kept_threads(models: Iterable[str]) -> threading.Thread | None:
    """Close the kept threads of these models, in every home, without waiting on them.

    For a chat's model change, after which its old model's thread would only wait for its
    idle limit, and for a settings reload that no longer offers a model. (A reload that
    still offers it keeps its threads: they are held here, not on the replaced provider,
    and its replacement continues them.) Each waiting turn's usage is still collected, on a
    background thread (returned, so a caller may wait for it), and held for its home's next
    request.
    """
    released = [kept for model in set(models) for kept in _KEPT_THREADS.release(model=model)]
    if not released:
        return None

    def close() -> None:
        for kept in released:
            kept.close_quietly(_KEPT_THREADS)

    closing = threading.Thread(target=close, name="sylvia-chatgpt-release", daemon=True)
    closing.start()
    return closing


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
    #: The kept-thread mode for this request: the switch's, or None (fresh) when the
    #: context ledger did not build it.
    thread_mode: str | None = None
    exchange_id: str | None = None
    generation: int = 0
    #: This request's number among its home's requests (``_KeptThreads.next_ordinal``).
    ordinal: int = 0
    status: str | None = None
    keep: _KeptThread | None = None
    #: The call was answered although the request dropped the model's text before it.
    kept_dropped_draft: bool = False
    #: Every usage report the runtime sent during the current attempt, in order.
    usage_reports: list[dict[str, object]] = field(default_factory=list)


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
        switch = thread_setting()
        self._thread_mode = _THREAD_MODES.get(switch)
        self._stable_threads = self._thread_mode is not None
        self._keeps_dropped_draft = switch == _DRAFT_KEEPING_VALUE
        # State of the attempt in progress; attempts are serialised by ``_lock``.
        self._usage_reports: list[dict[str, object]] = []
        # The waiting call whose usage the attempt in progress expects first, if any.
        self._owing: _PendingCall | None = None
        self._owing_ago = 1
        self._extra_call_seen = False

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
        parsed = _reported_usage(params)
        if parsed is None:
            return
        usage, cache = parsed
        if self._owing is not None and self._owing.usage_owed:
            # The first complete report after an answer belongs to the call's exchange.
            figures = self._owing.credit(usage, cache)
            ago = self._owing_ago
            self._usage_reports.append({
                "response": "previous" if ago == 1 else "earlier", "requests_ago": ago,
                **figures,
            })
            return
        own = {"response": "own", **_usage_figures(usage, cache)}
        if not self._usage_reports or self._usage_reports[-1] != own:
            self._usage_reports.append(own)
        response.usage = usage
        response.cache_usage = cache
        response.usage_reported = True

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
                    self._note_extra_call(method, params, thread_id)
                    await asyncio.sleep(0)
        except CodexCancelledError:
            raise
        except CodexConnectionError:
            return "failed"
        return "interrupted"

    def _note_extra_call(
        self, method: object, params: dict[str, Any], thread_id: str | None,
    ) -> None:
        """A second tool call of the same response: Sylvia returns one, so nothing is kept."""
        if method == "item/tool/call" and params.get("threadId") in {None, thread_id}:
            self._extra_call_seen = True

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
                    self._note_extra_call(method, params, thread_id)
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

        The appended items must start with the model's own text, call and result
        (``_call_tail``), so the model sees its call answered rather than asking again.
        """
        _call_tail(request.items[len(kept.items):], kept.call_id, kept.spoken)
        drain = getattr(connection, "notifications", None)
        if callable(drain):
            # Events of the finished turn must not be read as events of the next one.
            drain()
        await self._inject(connection, kept.thread_id, request.items[len(kept.items):], timing)

    async def _answer_pending_call(
        self, connection: CodexConnection, kept: _KeptThread, request: _Request,
        timing: LifecycleTiming,
    ) -> None:
        """Deliver what this request appends as the answer to the call the turn waits on.

        User-role messages after the result (the application's records, or the player's next
        message) are queued into the open turn first, so the model reads them with the
        result. The turn then continues by itself: no further turn is started.
        """
        pending = kept.pending
        assert pending is not None
        tail = request.items[len(kept.items):]
        result, texts = _in_place_answer(
            tail, pending.call_id, kept.spoken, self._keeps_dropped_draft,
        )
        request.kept_dropped_draft = bool(kept.spoken) and not any(
            item.get("type") == "message" and item.get("role") == "assistant"
            for item in tail[:1]
        )
        options = request.options
        if (options.get("reasoning_effort"), options.get("service_tier")) != (
            kept.effort, kept.tier,
        ):
            # The open turn keeps the effort and tier it was started with.
            raise _AnswerInPlaceError(
                "effort_or_tier_changed", "The request asks for another effort or tier.",
            )
        drain = getattr(connection, "notifications", None)
        if callable(drain):
            for event in drain():
                method, params = event.get("method"), event.get("params")
                if not isinstance(params, dict) or params.get("threadId") not in {
                    None, kept.thread_id,
                }:
                    continue
                if method == "thread/tokenUsage/updated":
                    # The report for the response that made the call, sent while it waited.
                    await self._audit(method, params, received=True)
                    if pending.usage_owed:
                        self._capture_usage(
                            ProviderResponse(), params, kept.thread_id, pending.turn_id,
                        )
                elif method in _TURN_MOVED_ON:
                    # The runtime gave up on the call, or went on without its answer.
                    raise _AnswerInPlaceError(
                        "turn_moved_on", "The runtime turn went on before its answer.",
                    )
        with timing.measure("history_injection"):
            for text in texts:
                # One message each, as a fresh thread would hold them.
                steer = {
                    "threadId": kept.thread_id, "expectedTurnId": pending.turn_id,
                    "input": [{"type": "text", "text": text}],
                }
                await self._audit("turn/steer", steer)
                steered = await asyncio.to_thread(connection.call, "turn/steer", steer)
                await self._audit("turn/steer/result", steered, received=True)
            answer = {"contentItems": [{"type": "inputText", "text": result}], "success": True}
            await self._audit(
                "item/tool/call/result", {"callId": pending.call_id, "result": answer},
            )
            await asyncio.to_thread(connection.respond, pending.request_id, answer)

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
        request.ordinal = _KEPT_THREADS.next_ordinal(home)
        kept, stale = _KEPT_THREADS.take(
            home, request.model, request.instructions, request.dynamic_tools, request.items,
        )
        try:
            await self._close_kept(*stale)
        except BaseException:
            # Taken from the pool, so nothing else would ever close it.
            if kept is not None:
                await self._close_kept(kept)
            raise
        return kept

    async def _keep_thread(self, kept: _KeptThread) -> bool:
        stale = _KEPT_THREADS.keep(kept)
        await self._close_kept(*stale)
        return kept not in stale

    async def _close_kept(self, *threads: _KeptThread) -> None:
        """Collect idle kept children; a failure to close never fails a request.

        These threads are out of the pool, so nothing else would close them: a cancellation
        while one closes still closes the rest, and is raised again afterwards.
        """
        cancelled: asyncio.CancelledError | None = None
        for kept in threads:
            try:
                _stop_timers([kept])
                if cancelled is None:
                    figures = None
                    with suppress(Exception):
                        figures = await asyncio.to_thread(kept.collect_owed_usage)
                    if figures is not None:
                        _KEPT_THREADS.abandon(kept.home, kept.ordinal, figures)
                with suppress(Exception):
                    await self._close_owned(kept.connection)
            except asyncio.CancelledError as error:
                cancelled = error
        if cancelled is not None:
            raise cancelled

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
        marked = _ledger_request()
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
                thread_mode=self._thread_mode if marked else None,
                exchange_id=marked if isinstance(marked, str) else None,
            )
            mode = request.thread_mode
            kept = await self._take_kept_thread(request) if mode is not None else None
            reuse: dict[str, object] = {
                "continued": kept is not None,
                "held_items": len(kept.items) if kept is not None else 0,
                "injected_items": len(items) - (len(kept.items) if kept is not None else 0),
                "retried_fresh": False,
                "kept": False,
            }
            if mode == "answer":
                reuse["answered_in_place"] = bool(kept is not None and kept.pending)
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
                            # Why the kept thread could not serve this request.
                            fresh_reason=getattr(error, "reason", "runtime_error"),
                        )
                        if "answered_in_place" in reuse:
                            reuse["answered_in_place"] = False
                    finally:
                        await attempt.aclose()
                finished = True
            finally:
                cancelled: asyncio.CancelledError | None = None
                if request.keep is not None:
                    held, request.keep = request.keep, None
                    try:
                        # A stream closed or cancelled after its last chunk keeps nothing.
                        if finished:
                            reuse["kept"] = await self._keep_thread(held)
                        else:
                            await self._close_kept(held)
                    except asyncio.CancelledError as error:
                        cancelled = error
                if request.status is not None:
                    receipt = timing.snapshot(request.status)
                    if mode is not None:
                        if mode == "answer":
                            # In order. "previous" (or "earlier", with how many requests
                            # before) is a response whose call this request answered, or whose
                            # waiting turn was closed unanswered; never this response's.
                            reuse["usage_reports"] = [
                                *_KEPT_THREADS.take_abandoned(
                                    self.home.resolve(), request.ordinal,
                                ),
                                *request.usage_reports,
                            ]
                        if self._keeps_dropped_draft:
                            reuse["kept_dropped_draft"] = bool(
                                reuse.get("answered_in_place") and request.kept_dropped_draft
                            )
                        await self._audit("sylvia/thread_reuse", reuse)
                        receipt["thread_reuse"] = reuse
                    await self._audit("sylvia/provider_lifecycle", receipt)
                if cancelled is not None:
                    raise cancelled

    async def _attempt(
        self, request: _Request, timing: LifecycleTiming, kept: _KeptThread | None,
    ) -> AsyncGenerator[ProviderStreamChunk, None]:
        """Run one turn on a fresh runtime thread, or on the kept thread it continues.

        The child is closed when the attempt ends, unless the turn ended in a tool call with
        a kept-thread mode on for this request; ``request.keep`` then names the thread for the
        caller to keep.
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
        self._usage_reports = request.usage_reports
        self._owing = kept.pending if kept is not None else None
        self._owing_ago = request.ordinal - kept.ordinal if kept is not None else 1
        self._extra_call_seen = False
        status = "error"
        request.status = status
        stream_start: int | None = None
        keep = False
        open_call: _PendingCall | None = None

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
                if kept.pending is None:
                    await self._continue_thread(connection, kept, request, timing)
                else:
                    await self._answer_pending_call(connection, kept, request, timing)
            if kept is not None and kept.pending is not None:
                # The answered turn goes on; its effort and tier were set when it started.
                turn_id = kept.pending.turn_id
            else:
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
                    if request.thread_mode == "answer" and event.get("id") is not None:
                        # Leave the turn waiting on this call. The runtime starts nothing
                        # until the call is answered, and reports this response's usage
                        # then, so this request ends without a usage report of its own.
                        open_call = _PendingCall(
                            event["id"], call_id, str(turn_id),
                            usage_owed=not response.usage_reported,
                            exchange_id=request.exchange_id,
                            model=request.model,
                            listeners=self.hooks.late_usage_listeners(
                                "chatgpt_subscription", request.exchange_id,
                            ) if request.exchange_id is not None else [],
                        )
                        keep = True
                    else:
                        interrupt = await self._await_usage_after_tool_call(
                            connection, response, thread_id, turn_id,
                        )
                        # A kept thread must have ended its turn; one still awaiting the
                        # call's result is stopped like any other.
                        keep = request.thread_mode is not None and (
                            interrupt == "ended" or (
                                interrupt == "interrupted"
                                and await self._await_interrupted_turn_end(
                                    connection, response, thread_id, turn_id,
                                )
                            )
                        )
                    if keep:
                        # Sylvia returns one call. Another call of the same response already
                        # waiting means the thread would hold a call the request never will.
                        queue_of = getattr(connection, "notifications", None)
                        for queued in queue_of() if callable(queue_of) else []:
                            queued_params = queued.get("params")
                            if isinstance(queued_params, dict):
                                self._note_extra_call(
                                    queued.get("method"), queued_params, thread_id,
                                )
                                if queued.get("method") == "thread/tokenUsage/updated":
                                    self._capture_usage(
                                        response, queued_params, thread_id, turn_id,
                                    )
                        if open_call is not None:
                            open_call.usage_owed = not response.usage_reported
                        if self._extra_call_seen:
                            keep, open_call = False, None
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
                    ordinal=request.ordinal,
                    call_id=response.tool_calls[0].id,
                    spoken="".join(text_parts),
                    # An answered turn keeps what it was started with.
                    effort=(
                        kept.effort if kept is not None and kept.pending is not None
                        else kwargs.get("reasoning_effort")
                    ),
                    tier=(
                        kept.tier if kept is not None and kept.pending is not None
                        else kwargs.get("service_tier")
                    ),
                    pending=open_call,
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
            if kept is not None:
                # A stop that lands while a kept thread waits for events can surface as the
                # child having gone. That is a cancellation: reported as a lost connection
                # it would be retried on a fresh thread, a new model request after a stop.
                try:
                    self._check_stopped(connection)
                except CodexCancelledError:
                    if status != "completed":
                        status = "cancelled"
                        request.status = status
                    raise asyncio.CancelledError(
                        "The ChatGPT response was cancelled."
                    ) from None
            raise ProviderConnectionError(str(error)) from None
        finally:
            # Closing the owned child cancels unresolved runtime calls and ephemeral history.
            end_stream(status)
            try:
                if request.keep is None:
                    if (kept is not None and kept.pending is not None
                            and kept.pending.usage_owed and status != "cancelled"):
                        # The waiting turn will not be answered (the request did not match
                        # it, or it failed): its response's report is collected before the
                        # child closes. A stopped request does not wait for it.
                        figures = None
                        with suppress(Exception):
                            figures = await asyncio.to_thread(kept.collect_owed_usage)
                        if figures is not None:
                            _KEPT_THREADS.abandon(kept.home, kept.ordinal, figures)
                    await stop()
            finally:
                self._active = None
                self._owing = None

    async def close(self) -> None:
        connection = self._active
        if connection is not None:
            self._request_stop(connection)
            await self._close_owned(connection)
        await self.release_idle()

    async def release_idle(self) -> None:
        """Close the runtime threads kept between requests for this home and model.

        A waiting turn's unreported usage is collected first and written to the audit now,
        since no later request may come to record it.
        """
        if self._stable_threads:
            home = self.home.resolve()
            await self._close_kept(
                *_KEPT_THREADS.release(home, self.model_info.wire_model_id)
            )
            reports = _KEPT_THREADS.take_abandoned(
                home, _KEPT_THREADS.next_ordinal(home, take=False))
            if reports:
                await self._audit("sylvia/thread_reuse_closed", {"usage_reports": reports})
