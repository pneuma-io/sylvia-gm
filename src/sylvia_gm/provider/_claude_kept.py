"""Claude Code processes kept between the requests of a GM session (issue 171).

A GM step on the Claude subscription used to start a new Claude Code process, which replayed
the whole saved transcript. Instead, the process that served one ledger request is kept, and a
later request that extends exactly what the process holds continues it through stdin:

- a reply that ended in text is followed by a new prompt written to stdin;
- a reply that ended in a Sylvia tool call is left waiting on that call, and the next request
  answers it: the call's result and everything after it (host records and the player's next
  message, as text blocks of the result) go back as the MCP tool result, and the reply goes on
  in the same native turn. The ledger's continue record is not sent: the turn continues by
  itself.

Anything else (a Retry, a compaction, a changed system text or tool list, another model) starts
a fresh process from the saved transcript, as before. ``SYLVIA_CLAUDE_PROCESS=per-step`` keeps
the previous behaviour, a new process for every step.
"""

from __future__ import annotations

import atexit
import json
import os
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Environment switch, read for every request: ``persistent`` (the default) or ``per-step``.
PROCESS_SWITCH = "SYLVIA_CLAUDE_PROCESS"
PERSISTENT = "persistent"
PER_STEP = "per-step"
_PER_STEP_VALUES = {PER_STEP, "per_step", "step", "fresh"}
#: The audit's ``persist_session`` value for a kept process's session file.
KEPT_SESSION = "kept_until_process_closes"
#: Idle processes kept at once; the least recently used is closed first.
KEPT_PROCESS_LIMIT = 4
#: A kept process left unused this long is closed, as for ChatGPT's kept threads. A GM turn
#: ends waiting on a call, so this is the player's reading and typing time.
KEPT_PROCESS_IDLE_SECONDS = 1800.0
#: How every sealed host record starts (``_context_ledger.seal_prefix``).
_SEAL_START = "[host-seal "
#: Kept files are touched this often, so another owner process sharing the runtime home does
#: not sweep them as leftovers (its sweep removes files unchanged for 15 minutes).
_KEEPALIVE_SECONDS = 600.0


def process_setting(environ: Mapping[str, str] | None = None) -> str:
    """``persistent`` unless ``SYLVIA_CLAUDE_PROCESS`` asks for per-step processes."""
    value = (os.environ if environ is None else environ).get(PROCESS_SWITCH, "")
    return PER_STEP if value.strip().casefold() in _PER_STEP_VALUES else PERSISTENT


class ContinuationMismatchError(Exception):
    """A kept process cannot serve this request; ``reason`` is a code for the audit."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(eq=False)
class KeptProcess:
    """One idle Claude Code process and what it holds."""

    connection: Any
    home: Path
    model: str
    system: str
    tools_key: str
    #: The Anthropic-form messages of the last request it served, as Sylvia sent them.
    held: list[dict[str, Any]]
    session_id: str
    step: Path
    config: Path
    cache: Path | None
    #: The text the model streamed in its last reply (before its call, if it made one).
    spoken: str = ""
    #: The ``tools/call`` control request the process is waiting on, if any.
    pending: dict[str, Any] | None = None
    call_id: str = ""
    call_arguments: Any = None
    idle_since: float = 0.0
    timer: threading.Timer | None = None
    keepalive: threading.Timer | None = None
    closed: bool = False
    on_close: list[Callable[[KeptProcess], None]] = field(default_factory=list)

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        _stop_timers([self])
        with suppress(Exception):
            self.connection.close()
        for callback in self.on_close:
            with suppress(Exception):
                callback(self)

    def touch(self) -> None:
        """Keep this process's files recent for other owners' sweeps (never for its own)."""
        now = time.time()
        for path in (self.step, *self.config.glob(f"projects/*/{self.session_id}.jsonl")):
            with suppress(OSError):
                os.utime(path, (now, now))


def _stop_timers(kept: list[KeptProcess]) -> None:
    for each in kept:
        for timer in (each.timer, each.keepalive):
            if timer is not None:
                timer.cancel()
        each.timer = each.keepalive = None


class KeptProcesses:
    """Process-wide idle Claude Code processes, matched by home, model, system and tools."""

    def __init__(
        self, limit: int = KEPT_PROCESS_LIMIT, idle_seconds: float = KEPT_PROCESS_IDLE_SECONDS,
    ) -> None:
        self.limit = limit
        self.idle_seconds = idle_seconds
        self._lock = threading.Lock()
        self._idle: list[KeptProcess] = []  # least recently used first
        self._generations: dict[Path, int] = {}

    def __len__(self) -> int:
        with self._lock:
            return len(self._idle)

    def generation(self, home: Path) -> int:
        with self._lock:
            return self._generations.get(home, 0)

    def take(
        self, home: Path, model: str, system: str, tools_key: str,
        history: list[dict[str, Any]],
    ) -> tuple[KeptProcess | None, list[KeptProcess]]:
        """Remove the process this request extends, and the processes now to close.

        Processes of another home, idle too long, or of this model and system whose history
        starts like this request's but which it does not extend (its earlier forms: a Retry,
        a compaction, a changed tool list) close. Others may belong to another session.
        """
        now = time.monotonic()
        with self._lock:
            close = [k for k in self._idle
                     if k.home != home or now - k.idle_since >= self.idle_seconds]
            candidates = [k for k in self._idle if k not in close]
            chosen = next(
                (k for k in reversed(candidates)
                 if (k.model, k.system, k.tools_key) == (model, system, tools_key)
                 and _extends(history, k.held)),
                None,
            )
            if chosen is None:
                close.extend(
                    k for k in candidates
                    if k.model == model and k.system == system and k.held[:1] == history[:1]
                )
            self._idle = [k for k in candidates if k is not chosen and k not in close]
            _stop_timers([*close, *([chosen] if chosen is not None else [])])
        return chosen, close

    def keep(self, kept: KeptProcess, generation: int) -> list[KeptProcess]:
        """Hold an idle process; return the processes to close, including it when refused."""
        with self._lock:
            if generation != self._generations.get(kept.home, 0):
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
                self._schedule_keepalive(kept)
        return evicted

    def _schedule_keepalive(self, kept: KeptProcess) -> None:
        def tick() -> None:
            with self._lock:
                if kept not in self._idle:
                    return
                self._schedule_keepalive(kept)
            kept.touch()

        kept.keepalive = threading.Timer(_KEEPALIVE_SECONDS, tick)
        kept.keepalive.daemon = True
        kept.keepalive.start()

    def _expire(self, kept: KeptProcess) -> None:
        with self._lock:
            if kept not in self._idle:
                return
            self._idle.remove(kept)
        kept.close()

    def release(self, home: Path | None = None, model: str | None = None) -> list[KeptProcess]:
        """Remove idle processes of a home (and model); a busy one there is not kept after."""
        with self._lock:
            homes = ({home} if home is not None
                     else {k.home for k in self._idle} | set(self._generations))
            for each in homes:
                self._generations[each] = self._generations.get(each, 0) + 1
            released = [k for k in self._idle
                        if (home is None or k.home == home) and (model is None or k.model == model)]
            self._idle = [k for k in self._idle if k not in released]
            _stop_timers(released)
        return released

    def models(self) -> frozenset[str]:
        with self._lock:
            return frozenset(k.model for k in self._idle)

    def close_all(self) -> None:
        for kept in self.release():
            kept.close()


KEPT_PROCESSES = KeptProcesses()


def release_kept_processes(home: Path | None = None, models: Any = None) -> int:
    """Close kept processes of a home, or of these models; return how many closed."""
    pool = KEPT_PROCESSES
    released = (
        pool.release(home) if models is None
        else [k for model in set(models) for k in pool.release(home, model)]
    )
    for kept in released:
        kept.close()
    return len(released)


atexit.register(lambda: KEPT_PROCESSES.close_all())


def _plain(value: Any) -> Any:
    """Without cache markers, which move between requests and are not content."""
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items() if k != "cache_control"}
    if isinstance(value, list):
        return [_plain(v) for v in value]
    return value


def _same(a: Any, b: Any) -> bool:
    return json.dumps(_plain(a), sort_keys=True, ensure_ascii=False) == json.dumps(
        _plain(b), sort_keys=True, ensure_ascii=False)


def _extends(history: list[dict[str, Any]], held: list[dict[str, Any]]) -> bool:
    return len(history) > len(held) and all(
        _same(a, b) for a, b in zip(history, held, strict=False))


def _blocks(content: Any) -> list[dict[str, Any]]:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    if isinstance(content, list) and all(isinstance(b, dict) for b in content):
        return content
    raise ContinuationMismatchError("content_not_deliverable")


def _text(content: Any) -> str:
    return "".join(b.get("text", "") for b in _blocks(content) if b.get("type") == "text")


def _strings(value: Any) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for item in value.values() for s in _strings(item)]
    if isinstance(value, list):
        return [s for item in value for s in _strings(item)]
    return []


def _text_blocks_after(message: dict[str, Any], skip: str) -> list[dict[str, Any]]:
    """A user message's text blocks, without the ledger's continue record."""
    out = []
    for block in _blocks(message.get("content")):
        if block.get("type") != "text":
            raise ContinuationMismatchError("content_not_deliverable")
        text = block.get("text", "")
        if skip and text.startswith(_SEAL_START) and text.endswith(skip):
            continue  # the ledger's sealed continue record
        out.append({"type": "text", "text": block.get("text", "")})
    return out


def continuation(
    kept: KeptProcess, history: list[dict[str, Any]], continue_text: str,
) -> tuple[str, Any]:
    """What to write to the kept process for this request: a prompt, or the call's answer.

    Returns ``("prompt", content)`` after a reply that ended in text, or ``("answer", result)``
    for a waiting call, where ``result`` is the MCP tool result. Raises
    ``ContinuationMismatchError`` when the request does not continue exactly what the process holds.
    """
    tail = history[len(kept.held):]
    if not tail or tail[0].get("role") != "assistant":
        raise ContinuationMismatchError("no_reply_copy")
    reply = _blocks(tail[0].get("content"))
    if _text(reply) != kept.spoken:
        raise ContinuationMismatchError("model_text_differs")
    calls = [b for b in reply if b.get("type") == "tool_use"]
    if kept.pending is None:
        # The user messages after the reply are this request's stdin prompt, together, as the
        # per-step transcript would send them.
        if calls or len(tail) < 2 or any(m.get("role") != "user" for m in tail[1:]):
            raise ContinuationMismatchError("tail_not_deliverable")
        content = [b for m in tail[1:] for b in _text_blocks_after(m, continue_text)]
        if not content:
            raise ContinuationMismatchError("no_prompt")
        return "prompt", content
    if len(calls) != 1 or calls[0].get("id") != kept.call_id:
        raise ContinuationMismatchError("no_call_result")
    if len(tail) < 2 or tail[1].get("role") != "user":
        raise ContinuationMismatchError("no_call_result")
    first, *rest = _blocks(tail[1].get("content"))
    if first.get("type") != "tool_result" or first.get("tool_use_id") != kept.call_id:
        raise ContinuationMismatchError("no_call_result")
    result = _blocks(first.get("content", ""))
    if any(b.get("type") != "text" for b in result):
        raise ContinuationMismatchError("content_not_deliverable")
    known = [_text(result), *_strings(kept.call_arguments)]
    extras = _text_blocks_after({"content": rest}, continue_text) if rest else []
    for message in tail[2:]:
        if message.get("role") == "assistant":
            # Sylvia's copy of narration the model wrote into the call is not sent again.
            text = _text(message.get("content"))
            if text and text in known and not any(
                b.get("type") != "text" for b in _blocks(message.get("content"))
            ):
                continue
            raise ContinuationMismatchError("tail_not_deliverable")
        extras.extend(_text_blocks_after(message, continue_text))
    answer: dict[str, Any] = {
        "content": [{"type": "text", "text": b.get("text", "")} for b in result] + extras
    }
    if first.get("is_error"):
        answer["isError"] = True
    return "answer", answer
