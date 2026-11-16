"""A kept runtime thread continues only a request that extends exactly what it holds.

Every runtime here is an in-memory script behind the real connection class: no Codex
process, credential store or network is used.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from sylvia_gm.provider import _chatgpt
from sylvia_gm.provider._base import ModelInfo
from sylvia_gm.provider._chatgpt import ChatGPTProvider, app_server_request
from sylvia_gm.provider._codex_rpc import CodexConnection, CodexConnectionError
from sylvia_gm.provider._exceptions import ModelNotFoundError, ProviderConnectionError
from sylvia_gm.provider._hooks import HookContext

MODEL = "gpt-6.1-sol"
SYSTEM = {"role": "system", "content": "Stable synthetic instructions."}
OPENING = {"role": "user", "content": "Open the synthetic door."}


def call(call_id: str) -> dict[str, Any]:
    return {"role": "assistant", "content": None, "tool_calls": [{
        "id": call_id, "type": "function", "function": {"name": "choose", "arguments": "{}"},
    }]}


def result(call_id: str, text: str = "The synthetic door opens.") -> dict[str, Any]:
    return {"role": "tool", "tool_call_id": call_id, "content": text}


def user(text: str) -> dict[str, Any]:
    return {"role": "user", "content": text}


def tool(name: str = "choose") -> dict[str, Any]:
    return {"type": "function", "function": {
        "name": name, "description": name.title(), "parameters": {"type": "object"},
    }}


TOOLS = [tool()]
FIRST = [SYSTEM, OPENING]
SECOND = [*FIRST, call("call-1"), result("call-1")]


def items_of(messages: list[dict[str, Any]], tools: list[dict[str, Any]] = TOOLS) -> list:
    return app_server_request(MODEL, messages, tools)[1]


class Turn:
    """What the runtime does for one turn/start, and after an interrupt of it."""

    def __init__(
        self, events: list[dict[str, Any]], after_interrupt: list[dict[str, Any]] | None = None,
    ) -> None:
        self.events = events
        self.after_interrupt = after_interrupt or []


def usage(prompt: int, cached: int) -> dict[str, Any]:
    return {"method": "thread/tokenUsage/updated", "params": {"tokenUsage": {"last": {
        "inputTokens": prompt, "outputTokens": 5, "totalTokens": prompt + 5,
        "cachedInputTokens": cached,
    }}}}


def tool_turn(call_id: str = "call-1", *, ends: bool = True) -> Turn:
    """Like the live runtime: usage and the turn's end arrive only after the interrupt."""
    after = [usage(100, 90)]
    if ends:
        after.append({"method": "turn/completed", "params": {"turn": {"status": "interrupted"}}})
    return Turn([{"method": "item/tool/call", "params": {
        "namespace": "sylvia", "tool": "choose", "callId": call_id, "arguments": {},
    }}], after)


def text_turn(text: str = "Synthetic narration.") -> Turn:
    return Turn([
        {"method": "item/agentMessage/delta", "params": {"delta": text}},
        usage(120, 100),
        {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
    ])


class Child:
    """An in-memory process double."""

    stdin = stdout = None

    def __init__(self) -> None:
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 1

    kill = terminate

    def wait(self, timeout: float) -> int | None:
        return self.returncode


class Scripted(CodexConnection):
    """The real connection class, answering from the shared runtime script."""

    _process: Any

    def __init__(self, home: Path, script: Runtime, *, dynamic_tools: frozenset[str]) -> None:
        super().__init__(home, dynamic_tools=dynamic_tools)
        self.script = script
        self.children: list[Child] = []
        self.sent: list[tuple[str, dict[str, Any]]] = []
        self.current: tuple[str, str, Turn] | None = None

    def _start_process(self) -> None:
        child = Child()
        self.children.append(child)
        self._process = child

    def _write(self, message: dict[str, Any]) -> None:
        if "id" not in message:
            return
        method, params = message["method"], message.get("params", {})
        self.sent.append((method, params))
        if self._process is None or self._process.poll() is not None:
            raise CodexConnectionError("The ChatGPT connection has closed. Reconnect to continue.")
        if self.script.failures.get(method):
            self.script.failures[method] -= 1
            raise CodexConnectionError("The ChatGPT connection could not send a request.")
        reply: dict[str, Any] = {}
        if method == "account/read":
            reply = {"account": {"type": "chatgpt"}}
        elif method == "thread/start":
            self.script.threads += 1
            reply = {"thread": {"id": f"thread-{self.script.threads}"}, "model": MODEL}
        elif method == "turn/start":
            self.script.turns_started += 1
            turn_id = f"turn-{self.script.turns_started}"
            turn = self.script.turns.pop(0)
            self.current = (params["threadId"], turn_id, turn)
            reply = {"turn": {"id": turn_id}}
            self._emit(turn.events)
        elif method == "turn/interrupt":
            assert self.current is not None
            assert params == {"threadId": self.current[0], "turnId": self.current[1]}
            self._emit(self.current[2].after_interrupt)
        self._responses.put({"id": message["id"], "result": reply})

    def _emit(self, events: list[dict[str, Any]]) -> None:
        assert self.current is not None
        thread_id, turn_id, _ = self.current
        for event in events:
            params = dict(event["params"])
            params.setdefault("threadId", thread_id)
            if event["method"] != "turn/completed":
                params.setdefault("turnId", turn_id)
            self._notifications.put({"method": event["method"], "params": params})

    def methods(self, name: str) -> list[dict[str, Any]]:
        return [params for method, params in self.sent if method == name]

    @property
    def alive(self) -> bool:
        return bool(self.children) and self.children[-1].poll() is None


class Runtime:
    """Every connection one provider creates, and the turns they will play in order."""

    def __init__(self, *turns: Turn) -> None:
        self.turns = list(turns)
        self.connections: list[Scripted] = []
        self.failures: dict[str, int] = {}
        self.threads = 0
        self.turns_started = 0

    def factory(
        self, home: Path, executable: Path | None = None, *,
        dynamic_tools: frozenset[str] = frozenset(),
    ) -> Scripted:
        connection = Scripted(home, self, dynamic_tools=dynamic_tools)
        self.connections.append(connection)
        return connection

    def alive(self) -> list[Scripted]:
        return [connection for connection in self.connections if connection.alive]

    def sent(self, name: str) -> list[dict[str, Any]]:
        return [params for connection in self.connections for params in connection.methods(name)]


class Audit:
    def __init__(self) -> None:
        self.reuse: list[dict[str, Any]] = []
        self.receipts: list[dict[str, Any]] = []

    async def on_request(self, context: HookContext) -> None:
        value = context.request_json or {}
        if value.get("method") == "sylvia/thread_reuse":
            self.reuse.append(value["params"])
        elif value.get("method") == "sylvia/provider_lifecycle":
            self.receipts.append(value["params"])


@pytest.fixture
def pool(monkeypatch: pytest.MonkeyPatch):
    kept = _chatgpt._KeptThreads()
    monkeypatch.setattr(_chatgpt, "_KEPT_THREADS", kept)
    monkeypatch.setattr(_chatgpt, "_USAGE_SETTLE_SECONDS", 0.01)
    yield kept
    kept.close_all()


@pytest.fixture
def stable(monkeypatch: pytest.MonkeyPatch, pool: _chatgpt._KeptThreads):
    monkeypatch.setenv("SYLVIA_CHATGPT_THREAD", "stable")
    return pool


def provider_for(runtime: Runtime, home: Path) -> tuple[ChatGPTProvider, Audit]:
    provider = ChatGPTProvider(home, ModelInfo(
        id=f"chatgpt-subscription/{MODEL}", name="Sol", provider_name="ChatGPT",
        provider_key="chatgpt_subscription", provider_mode="subscription",
        transport="codex_app_server",
    ), connection_factory=runtime.factory)
    audit = Audit()
    provider.hooks.register(audit)
    return provider, audit


@pytest.mark.asyncio
@pytest.mark.parametrize("switch", [None, "", "off", "Stable"])
async def test_without_the_switch_each_request_starts_its_own_process_and_thread(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pool: _chatgpt._KeptThreads,
    switch: str | None,
) -> None:
    if switch is None:
        monkeypatch.delenv("SYLVIA_CHATGPT_THREAD", raising=False)
    else:
        monkeypatch.setenv("SYLVIA_CHATGPT_THREAD", switch)
    runtime = Runtime(tool_turn("call-1"), tool_turn("call-2"))
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    await provider.generate(SECOND, tools=TOOLS)
    assert len(runtime.connections) == 2 and runtime.alive() == []
    assert [len(c.methods("thread/start")) for c in runtime.connections] == [1, 1]
    assert [len(params["items"]) for params in runtime.sent("thread/inject_items")] == [1, 3]
    assert audit.reuse == [] and all("thread_reuse" not in r for r in audit.receipts)
    assert len(pool) == 0


@pytest.mark.asyncio
async def test_the_switch_is_read_when_the_provider_is_created(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pool: _chatgpt._KeptThreads,
) -> None:
    monkeypatch.delenv("SYLVIA_CHATGPT_THREAD", raising=False)
    runtime = Runtime(tool_turn())
    provider, _ = provider_for(runtime, tmp_path)
    monkeypatch.setenv("SYLVIA_CHATGPT_THREAD", "stable")
    await provider.generate(FIRST, tools=TOOLS)
    assert runtime.alive() == [] and len(pool) == 0


@pytest.mark.asyncio
async def test_a_request_extending_a_kept_thread_continues_it_with_only_the_new_items(
    tmp_path: Path, stable: _chatgpt._KeptThreads,
) -> None:
    runtime = Runtime(tool_turn("call-1"), tool_turn("call-2"))
    provider, audit = provider_for(runtime, tmp_path)
    first = await provider.generate(FIRST, tools=TOOLS)
    assert [c.name for c in first.tool_calls] == ["choose"]
    assert runtime.alive() == runtime.connections and len(stable) == 1

    second = await provider.generate(SECOND, tools=TOOLS)
    [connection] = runtime.connections
    assert connection.alive and len(connection.children) == 1
    assert len(connection.methods("thread/start")) == 1
    injected = connection.methods("thread/inject_items")
    assert [params["items"] for params in injected] == [
        items_of(FIRST), items_of(SECOND)[1:],
    ]
    assert injected[1]["items"][0]["type"] == "function_call"
    assert injected[1]["threadId"] == "thread-1"
    assert len(connection.methods("turn/start")) == 2
    # The usage pause, interrupt and report stay as they were, on both requests.
    assert len(connection.methods("turn/interrupt")) == 2
    for response in (first, second):
        assert response.usage_reported and response.usage.prompt_tokens == 100
        assert response.cache_usage.cache_read_tokens == 90
        assert response.finish_reason == "tool_calls"
    assert audit.reuse == [
        {"continued": False, "held_items": 0, "injected_items": 1,
         "retried_fresh": False, "kept": True},
        {"continued": True, "held_items": 1, "injected_items": 2,
         "retried_fresh": False, "kept": True},
    ]
    assert [r["thread_reuse"] for r in audit.receipts] == audit.reuse
    assert [r["status"] for r in audit.receipts] == ["completed", "completed"]
    assert "runtime_start" not in {span["name"] for span in audit.receipts[1]["spans"]}

    await provider.close()
    assert runtime.alive() == [] and len(stable) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["instructions", "tools", "diverged", "shorter", "same"])
async def test_a_request_that_does_not_extend_closes_the_kept_thread_and_starts_afresh(
    tmp_path: Path, stable: _chatgpt._KeptThreads, change: str,
) -> None:
    held = [SYSTEM, OPENING, user("Look around.")]
    tools = TOOLS
    if change == "instructions":
        messages = [{"role": "system", "content": "Changed instructions."}, *held[1:],
                    call("call-1"), result("call-1")]
    elif change == "tools":
        tools = [tool(), tool("inspect")]
        messages = [*held, call("call-1"), result("call-1")]
    elif change == "diverged":
        messages = [SYSTEM, OPENING, user("Leave instead."), user("Now.")]
    elif change == "shorter":
        messages = [SYSTEM, OPENING]
    else:
        messages = list(held)
    runtime = Runtime(tool_turn("call-1"), text_turn())
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(held, tools=TOOLS)
    kept = runtime.connections[0]
    assert kept.alive

    await provider.generate(messages, tools=tools)
    assert len(runtime.connections) == 2 and not kept.alive
    fresh = runtime.connections[1]
    assert len(fresh.methods("thread/start")) == 1
    assert fresh.methods("thread/inject_items")[0]["items"] == items_of(messages, tools)
    assert audit.reuse[1]["continued"] is False
    assert runtime.alive() == [] and len(stable) == 0


@pytest.mark.asyncio
async def test_a_text_reply_closes_its_thread(
    tmp_path: Path, stable: _chatgpt._KeptThreads,
) -> None:
    runtime = Runtime(text_turn(), tool_turn())
    provider, audit = provider_for(runtime, tmp_path)
    response = await provider.generate(FIRST, tools=TOOLS)
    assert response.content == "Synthetic narration." and response.finish_reason == "stop"
    assert runtime.alive() == [] and len(stable) == 0
    await provider.generate([*FIRST, {"role": "assistant", "content": "Synthetic narration."},
                             user("Again.")], tools=TOOLS)
    assert len(runtime.connections) == 2
    assert [r["kept"] for r in audit.reuse] == [False, True]
    assert [r["continued"] for r in audit.reuse] == [False, False]


@pytest.mark.asyncio
async def test_a_turn_that_has_not_ended_after_the_interrupt_is_not_kept(
    tmp_path: Path, stable: _chatgpt._KeptThreads, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(_chatgpt, "_USAGE_REPORT_SECONDS", 0.2)
    runtime = Runtime(tool_turn(ends=False))
    provider, audit = provider_for(runtime, tmp_path)
    response = await provider.generate(FIRST, tools=TOOLS)
    assert response.usage_reported and [c.name for c in response.tool_calls] == ["choose"]
    assert runtime.alive() == [] and len(stable) == 0
    assert audit.reuse[0]["kept"] is False


def fail_continuation(runtime: Runtime, kind: str) -> None:
    if kind in {"thread/inject_items", "turn/start", "account/read"}:
        runtime.failures[kind] = 1
    elif kind == "exited":
        runtime.connections[0].children[-1].returncode = 0
    elif kind == "rerouted":
        runtime.turns.insert(0, Turn([{"method": "model/rerouted", "params": {}}]))
    elif kind == "failed_turn":
        runtime.turns.insert(0, Turn([
            {"method": "turn/completed", "params": {"turn": {"status": "failed"}}},
        ]))


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", [
    "thread/inject_items", "turn/start", "account/read", "exited", "rerouted", "failed_turn",
])
async def test_a_continuation_failing_before_output_falls_back_to_one_fresh_thread(
    tmp_path: Path, stable: _chatgpt._KeptThreads, kind: str,
) -> None:
    runtime = Runtime(tool_turn("call-1"), tool_turn("call-2"))
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    fail_continuation(runtime, kind)
    chunks = [chunk async for chunk in provider.generate_stream(SECOND, tools=TOOLS)]
    assert [chunk.tool_call_delta["id"] for chunk in chunks if chunk.tool_call_delta] == [
        "call-2",
    ]
    assert [chunk.finish_reason for chunk in chunks if chunk.finish_reason] == ["tool_calls"]
    kept, fresh = runtime.connections
    assert not kept.alive and fresh.alive
    assert fresh.methods("thread/inject_items")[0]["items"] == items_of(SECOND)
    assert audit.reuse[1] == {"continued": False, "held_items": 0, "injected_items": 3,
                              "retried_fresh": True, "kept": True}
    assert len(audit.receipts) == 2 and audit.receipts[1]["status"] == "completed"
    await provider.close()
    assert runtime.alive() == []


@pytest.mark.asyncio
async def test_the_fresh_retry_happens_only_once(
    tmp_path: Path, stable: _chatgpt._KeptThreads,
) -> None:
    runtime = Runtime(tool_turn("call-1"))
    provider, _ = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    runtime.failures["thread/inject_items"] = 2
    with pytest.raises(ProviderConnectionError):
        await provider.generate(SECOND, tools=TOOLS)
    assert len(runtime.connections) == 2 and runtime.alive() == [] and len(stable) == 0


@pytest.mark.asyncio
async def test_a_continuation_failing_after_output_is_not_retried(
    tmp_path: Path, stable: _chatgpt._KeptThreads,
) -> None:
    runtime = Runtime(tool_turn("call-1"), Turn([
        {"method": "item/agentMessage/delta", "params": {"delta": "Partial"}},
        {"method": "model/rerouted", "params": {}},
    ]))
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    seen: list[str] = []
    with pytest.raises(ModelNotFoundError):
        async for chunk in provider.generate_stream(SECOND, tools=TOOLS):
            seen.append(chunk.content)
    assert seen == ["Partial"]
    assert len(runtime.connections) == 1 and runtime.alive() == [] and len(stable) == 0
    assert audit.reuse[1]["continued"] is True and audit.reuse[1]["retried_fresh"] is False
    assert audit.receipts[1]["status"] == "error"


@pytest.mark.asyncio
async def test_an_idle_kept_thread_closes_on_its_timer(
    tmp_path: Path, stable: _chatgpt._KeptThreads,
) -> None:
    stable.idle_seconds = 0.05
    runtime = Runtime(tool_turn())
    provider, _ = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    deadline = time.monotonic() + 5
    while runtime.alive() and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    assert runtime.alive() == [] and len(stable) == 0


@pytest.mark.asyncio
async def test_an_expired_kept_thread_is_closed_when_the_next_request_arrives(
    tmp_path: Path, stable: _chatgpt._KeptThreads,
) -> None:
    runtime = Runtime(tool_turn("call-1"), tool_turn("call-2"))
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    [kept] = stable._idle
    kept.idle_since -= stable.idle_seconds
    await provider.generate(SECOND, tools=TOOLS)
    assert not runtime.connections[0].alive and len(runtime.connections) == 2
    assert audit.reuse[1]["continued"] is False


@pytest.mark.asyncio
async def test_stopping_a_continued_request_leaves_no_child(
    tmp_path: Path, stable: _chatgpt._KeptThreads,
) -> None:
    runtime = Runtime(tool_turn("call-1"), Turn([]))
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    task = asyncio.create_task(provider.generate(SECOND, tools=TOOLS))
    deadline = time.monotonic() + 5
    while runtime.turns_started < 2 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(runtime.connections) == 1 and runtime.alive() == [] and len(stable) == 0
    assert audit.receipts[1]["status"] == "cancelled"
    assert provider._active is None


@pytest.mark.asyncio
async def test_closing_the_provider_mid_continuation_leaves_no_child(
    tmp_path: Path, stable: _chatgpt._KeptThreads,
) -> None:
    runtime = Runtime(tool_turn("call-1"), Turn([]))
    provider, _ = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    task = asyncio.create_task(provider.generate(SECOND, tools=TOOLS))
    deadline = time.monotonic() + 5
    while runtime.turns_started < 2 and time.monotonic() < deadline:
        await asyncio.sleep(0.01)
    await provider.close()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime.alive() == [] and len(stable) == 0


@pytest.mark.asyncio
async def test_closing_a_stream_before_its_last_chunk_keeps_nothing(
    tmp_path: Path, stable: _chatgpt._KeptThreads,
) -> None:
    runtime = Runtime(tool_turn())
    provider, audit = provider_for(runtime, tmp_path)
    stream = provider.generate_stream(FIRST, tools=TOOLS)
    first = await anext(stream)
    assert first.tool_call_delta is not None
    await stream.aclose()
    assert runtime.alive() == [] and len(stable) == 0
    assert audit.receipts[0]["status"] == "cancelled"


@pytest.mark.asyncio
async def test_signing_in_or_out_closes_kept_threads_of_that_home(
    tmp_path: Path, stable: _chatgpt._KeptThreads,
) -> None:
    runtime = Runtime(tool_turn())
    provider, _ = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    account = Runtime().factory(tmp_path)
    account.start()
    account.call("account/logout", {})
    account.close()
    assert runtime.alive() == [] and len(stable) == 0


@pytest.mark.asyncio
async def test_a_request_from_another_runtime_home_closes_the_old_homes_threads(
    tmp_path: Path, stable: _chatgpt._KeptThreads,
) -> None:
    old, new = Runtime(tool_turn()), Runtime(tool_turn())
    first, _ = provider_for(old, tmp_path / "old")
    await first.generate(FIRST, tools=TOOLS)
    second, _ = provider_for(new, tmp_path / "new")
    await second.generate(FIRST, tools=TOOLS)
    assert old.alive() == [] and len(new.alive()) == 1 and len(stable) == 1


@pytest.mark.asyncio
async def test_other_sessions_keep_their_threads_until_the_limit(
    tmp_path: Path, stable: _chatgpt._KeptThreads,
) -> None:
    sessions = [[SYSTEM, user(f"Synthetic session {n}.")] for n in range(5)]
    runtime = Runtime(*(tool_turn(f"call-{n}") for n in range(7)))
    provider, audit = provider_for(runtime, tmp_path)
    for messages in sessions[:4]:
        await provider.generate(messages, tools=TOOLS)
    assert len(runtime.alive()) == 4
    # Session 0 continues; session 1 is now the least recently used.
    await provider.generate([*sessions[0], call("call-0"), result("call-0")], tools=TOOLS)
    assert audit.reuse[-1]["continued"] is True
    await provider.generate(sessions[4], tools=TOOLS)
    assert len(stable) == 4 and len(runtime.alive()) == 4
    assert not runtime.connections[1].alive
    await provider.generate([*sessions[2], call("call-2"), result("call-2")], tools=TOOLS)
    assert audit.reuse[-1]["continued"] is True and len(runtime.connections) == 5


class Closable:
    def __init__(self) -> None:
        self.closed = False

    def close(self) -> None:
        self.closed = True


def kept_thread(home: Path, items: list[dict[str, Any]], **fields: Any) -> _chatgpt._KeptThread:
    values: dict[str, Any] = {
        "connection": Closable(), "home": home, "model": MODEL, "thread_id": "thread",
        "instructions": "instructions", "dynamic_tools": [], "items": items, "generation": 0,
    }
    values.update(fields)
    return _chatgpt._KeptThread(**values)


def test_the_longest_held_prefix_wins_and_a_taken_thread_is_not_offered_twice(
    tmp_path: Path,
) -> None:
    pool = _chatgpt._KeptThreads()
    a, b, c, d = ({"type": "message", "n": n} for n in range(4))
    short, long = kept_thread(tmp_path, [a]), kept_thread(tmp_path, [a, b])
    other = kept_thread(tmp_path, [a, b], instructions="other")
    try:
        for each in (long, short, other):
            assert pool.keep(each) == []
        chosen, stale = pool.take(tmp_path, MODEL, "instructions", [], [a, b, c])
        assert chosen is long and stale == []
        again, stale = pool.take(tmp_path, MODEL, "instructions", [], [a, b, c])
        assert again is short and stale == []
        assert pool.take(tmp_path, MODEL, "instructions", [], [a, b, c]) == (None, [other])
        assert pool.take(tmp_path, MODEL, "instructions", [], [d]) == (None, [])
    finally:
        pool.close_all()


def test_the_least_recently_used_thread_is_closed_at_the_limit(tmp_path: Path) -> None:
    pool = _chatgpt._KeptThreads(limit=2)
    first, second, third = (
        kept_thread(tmp_path, [{"type": "message", "n": n}]) for n in range(3)
    )
    try:
        assert pool.keep(first) == [] and pool.keep(second) == []
        assert pool.keep(third) == [first]
        assert len(pool) == 2 and first.timer is None
    finally:
        pool.close_all()
    assert len(pool) == 0 and second.timer is None and third.timer is None


def test_a_release_refuses_a_thread_that_was_in_use_during_it(tmp_path: Path) -> None:
    pool = _chatgpt._KeptThreads()
    busy = kept_thread(tmp_path, [], generation=pool.generation(tmp_path))
    assert pool.release(tmp_path) == []
    assert pool.keep(busy) == [busy] and len(pool) == 0
