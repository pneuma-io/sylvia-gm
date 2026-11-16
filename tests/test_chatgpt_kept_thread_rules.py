"""When a kept runtime thread may serve a request, and what closes it.

A kept thread must hold exactly what the request holds; whenever it would not, the request
is served by a fresh thread and the audit says why. Every runtime here is the in-memory
script of the stable-thread tests: no Codex process, credential or network is used.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from sylvia_gm.provider import _chatgpt
from sylvia_gm.provider._chatgpt_account import ChatGPTAccount
from sylvia_gm.provider._codex_rpc import add_account_change_listener
from sylvia_gm.provider._exceptions import (
    ModelCapacityError,
    ProviderTimeoutError,
    RateLimitError,
    TokenLimitError,
)
from tests.test_chatgpt_answer_in_place import (
    NARRATION,
    AnswerRuntime,
    Waiting,
    answer_to,
    answering,  # noqa: F401  (fixture)
    asks,
)
from tests.test_chatgpt_stable_thread import (
    FIRST,
    OPENING,
    SECOND,
    SYSTEM,
    TOOLS,
    Audit,
    Runtime,
    Turn,
    call,
    items_of,
    pool,  # noqa: F401  (fixture)
    provider_for,
    result,
    stable,  # noqa: F401  (fixture)
    text_turn,
    tool_turn,
    usage,
    user,
)

PLAN = "Private plan: the guard lies."
SPOKEN = {"method": "item/agentMessage/delta", "params": {"delta": PLAN}}


def spoken_call(call_id: str = "call-1") -> dict[str, Any]:
    """Sylvia's copy of a round in which the model spoke and then called."""
    return {**call(call_id), "content": PLAN}


def ended(status: str) -> dict[str, Any]:
    return {"method": "turn/completed", "params": {"turn": {"status": status}}}


def failed_turn(code: str) -> Turn:
    return Turn([{"method": "turn/completed", "params": {"turn": {
        "status": "failed", "error": {"codexErrorInfo": code},
    }}}])


# ---- the request must bring the call's result and the model's own text ------------------

@pytest.mark.asyncio
async def test_a_request_that_does_not_answer_the_interrupted_call_is_not_continued(
    tmp_path: Path, stable: _chatgpt._KeptThreads,  # noqa: F811
) -> None:
    runtime = Runtime(tool_turn("call-1"), text_turn())
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    messages = [*FIRST, user("Forget that; look around.")]
    await provider.generate(messages, tools=TOOLS)
    kept, fresh = runtime.connections
    # The kept thread holds a call the request does not: nothing is added to it.
    assert len(kept.methods("thread/inject_items")) == 1 and not kept.alive
    assert fresh.methods("thread/inject_items")[0]["items"] == items_of(messages)
    assert audit.reuse[1]["continued"] is False
    assert audit.reuse[1]["retried_fresh"] is True
    assert audit.reuse[1]["fresh_reason"] == "no_call_result"
    assert runtime.alive() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("kept_text", "expected"), [
    (True, None), (False, "model_text_dropped"),
])
async def test_stable_continues_only_when_the_request_holds_what_the_model_said(
    tmp_path: Path, stable: _chatgpt._KeptThreads, kept_text: bool,  # noqa: F811
    expected: str | None,
) -> None:
    speaking = tool_turn("call-1")
    speaking.events.insert(0, SPOKEN)
    runtime = Runtime(speaking, text_turn())
    provider, audit = provider_for(runtime, tmp_path)
    first = await provider.generate(FIRST, tools=TOOLS)
    assert first.content == PLAN
    # The owner drops the text of a round whose call was not narrative.
    round_one = spoken_call() if kept_text else call("call-1")
    await provider.generate([*FIRST, round_one, result("call-1")], tools=TOOLS)
    assert audit.reuse[1]["continued"] is (expected is None)
    assert audit.reuse[1].get("fresh_reason") == expected
    assert len(runtime.connections) == (1 if expected is None else 2)
    assert runtime.alive() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("value", "round_one", "expected", "draft"), [
    # The request holds the model's text; the request dropped it; the request changed it.
    ("answer", "same", None, None),
    ("answer", "dropped", "model_text_dropped", None),
    ("answer", "other", "model_text_differs", None),
    ("answer-draft", "same", None, False),
    ("answer-draft", "dropped", None, True),
    ("answer-draft", "other", "model_text_differs", False),
])
async def test_the_draft_keeping_value_answers_a_call_whose_text_the_request_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pool: _chatgpt._KeptThreads,  # noqa: F811
    value: str, round_one: str, expected: str | None, draft: bool | None,
) -> None:
    monkeypatch.setenv("SYLVIA_CHATGPT_THREAD", value)
    runtime = AnswerRuntime(Waiting([SPOKEN, asks("call-1", 7)], [*NARRATION]), text_turn())
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    copy = {
        "same": spoken_call(), "dropped": call("call-1"),
        "other": {**call("call-1"), "content": "Something else."},
    }[round_one]
    await provider.generate([*FIRST, copy, result("call-1")], tools=TOOLS)
    record = audit.reuse[1]
    assert record["answered_in_place"] is (expected is None)
    assert record.get("fresh_reason") == expected
    assert record.get("kept_dropped_draft") == draft
    assert ("kept_dropped_draft" in audit.reuse[0]) is (value == "answer-draft")
    assert runtime.alive() == []


@pytest.mark.asyncio
@pytest.mark.parametrize(("kept_text", "expected"), [
    (True, None), (False, "model_text_dropped"),
])
async def test_a_call_is_answered_only_when_the_request_holds_what_the_model_said(
    tmp_path: Path, answering: _chatgpt._KeptThreads, kept_text: bool,  # noqa: F811
    expected: str | None,
) -> None:
    runtime = AnswerRuntime(Waiting([SPOKEN, asks("call-1", 7)], [*NARRATION]), text_turn())
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    round_one = spoken_call() if kept_text else call("call-1")
    await provider.generate([*FIRST, round_one, result("call-1")], tools=TOOLS)
    waiting = runtime.connections[0]
    assert audit.reuse[1]["answered_in_place"] is (expected is None)
    assert audit.reuse[1].get("fresh_reason") == expected
    assert len(waiting.methods("answer")) == (1 if expected is None else 0)
    assert runtime.alive() == []


# ---- usage ------------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_an_answered_request_never_carries_or_loses_another_responses_usage(
    tmp_path: Path, answering: _chatgpt._KeptThreads,  # noqa: F811
) -> None:
    runtime = AnswerRuntime(Waiting([asks("call-1", 7)], [usage(100, 0), *NARRATION]))
    provider, audit = provider_for(runtime, tmp_path)
    first = await provider.generate(FIRST, tools=TOOLS)
    second = await provider.generate(SECOND, tools=TOOLS)
    assert not first.usage_reported
    # Its own report only; the answered response's is in the record, not overwritten.
    assert second.usage.prompt_tokens == 160 and second.cache_usage.cache_read_tokens == 150
    assert audit.reuse[1]["usage_reports"] == [
        {"response": "previous", "input": 100, "cached": 0, "output": 5},
        {"response": "own", "input": 160, "cached": 150, "output": 5},
    ]


@pytest.mark.asyncio
async def test_a_report_that_arrives_while_the_turn_waits_is_the_previous_responses(
    tmp_path: Path, answering: _chatgpt._KeptThreads,  # noqa: F811
) -> None:
    runtime = AnswerRuntime(Waiting([asks("call-1", 7)], [*NARRATION]))
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    [connection] = runtime.connections
    connection._emit([usage(100, 0)])  # noqa: SLF001
    second = await provider.generate(SECOND, tools=TOOLS)
    assert second.usage.prompt_tokens == 160
    assert [r["response"] for r in audit.reuse[1]["usage_reports"]] == ["previous", "own"]
    assert connection.methods("answer") == [answer_to(7, "The synthetic door opens.")]


@pytest.mark.asyncio
async def test_stable_mode_has_no_usage_list_and_each_request_its_own_report(
    tmp_path: Path, stable: _chatgpt._KeptThreads,  # noqa: F811
) -> None:
    runtime = Runtime(tool_turn("call-1"), tool_turn("call-2"))
    provider, audit = provider_for(runtime, tmp_path)
    first = await provider.generate(FIRST, tools=TOOLS)
    second = await provider.generate(SECOND, tools=TOOLS)
    assert first.usage.prompt_tokens == second.usage.prompt_tokens == 100
    assert all("usage_reports" not in record for record in audit.reuse)


# ---- a turn that went on without its answer ---------------------------------------------

@pytest.mark.asyncio
async def test_a_second_call_of_one_response_keeps_nothing_in_answer_mode(
    tmp_path: Path, answering: _chatgpt._KeptThreads,  # noqa: F811
) -> None:
    runtime = AnswerRuntime(Waiting([asks("call-1", 7), asks("call-2", 8)]), text_turn())
    provider, audit = provider_for(runtime, tmp_path)
    first = await provider.generate(FIRST, tools=TOOLS)
    assert [c.id for c in first.tool_calls] == ["call-1"]
    # Sylvia returns one call; a thread that already holds another is closed, as before.
    assert audit.reuse[0]["kept"] is False and runtime.alive() == [] and len(answering) == 0
    second = await provider.generate(SECOND, tools=TOOLS, timeout=2)
    assert second.content == "Synthetic narration."
    assert runtime.connections[0].methods("answer") == []
    assert runtime.connections[1].methods("thread/inject_items")[0]["items"] == items_of(SECOND)


@pytest.mark.asyncio
async def test_a_second_call_of_one_response_keeps_nothing_in_stable_mode(
    tmp_path: Path, stable: _chatgpt._KeptThreads,  # noqa: F811
) -> None:
    both = tool_turn("call-1")
    both.after_interrupt.insert(0, {"method": "item/tool/call", "params": {
        "namespace": "sylvia", "tool": "choose", "callId": "call-2", "arguments": {},
    }})
    runtime = Runtime(both, text_turn())
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    assert audit.reuse[0]["kept"] is False and runtime.alive() == [] and len(stable) == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("event", [
    {"method": "item/tool/call", "id": 9, "params": {
        "namespace": "sylvia", "tool": "choose", "callId": "call-2", "arguments": {}}},
    {"method": "item/agentMessage/delta", "params": {"delta": "Never mind, I carry on."}},
    {"method": "item/reasoning/summaryTextDelta", "params": {"delta": "Moving on."}},
    {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
    {"method": "error", "params": {"message": "tool call timed out"}},
])
async def test_a_waiting_turn_that_moved_on_is_not_answered(
    tmp_path: Path, answering: _chatgpt._KeptThreads, event: dict[str, Any],  # noqa: F811
) -> None:
    runtime = AnswerRuntime(Waiting([asks("call-1", 7)]), text_turn())
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    waiting = runtime.connections[0]
    waiting._emit([event])  # noqa: SLF001

    response = await provider.generate(SECOND, tools=TOOLS, timeout=2)
    # Nothing the runtime wrote on its own is returned as this request's reply.
    assert response.content == "Synthetic narration."
    assert waiting.methods("answer") == [] and not waiting.alive
    assert audit.reuse[1]["fresh_reason"] == "turn_moved_on"
    assert audit.reuse[1]["answered_in_place"] is False
    assert runtime.alive() == []


@pytest.mark.asyncio
async def test_events_that_only_describe_the_waiting_call_do_not_prevent_its_answer(
    tmp_path: Path, answering: _chatgpt._KeptThreads,  # noqa: F811
) -> None:
    runtime = AnswerRuntime(Waiting([asks("call-1", 7)], [*NARRATION]))
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    [connection] = runtime.connections
    connection._emit([  # noqa: SLF001
        {"method": "item/started", "params": {"item": {"type": "dynamicToolCall"}}},
        {"method": "thread/status/changed", "params": {"status": "waiting"}},
    ])
    await provider.generate(SECOND, tools=TOOLS)
    assert audit.reuse[1]["answered_in_place"] is True and len(runtime.connections) == 1


# ---- effort and tier --------------------------------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(("options", "expected"), [
    ({"reasoning_effort": "low"}, None),
    ({"reasoning_effort": "high"}, "effort_or_tier_changed"),
    ({"reasoning_effort": "low", "service_tier": "fast"}, "effort_or_tier_changed"),
    ({}, "effort_or_tier_changed"),
])
async def test_a_call_is_answered_only_at_the_effort_and_tier_its_turn_started_with(
    tmp_path: Path, answering: _chatgpt._KeptThreads, options: dict[str, str],  # noqa: F811
    expected: str | None,
) -> None:
    runtime = AnswerRuntime(Waiting([asks("call-1", 7)], [*NARRATION]), text_turn())
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS, reasoning_effort="low")
    await provider.generate(SECOND, tools=TOOLS, **options)
    assert audit.reuse[1].get("fresh_reason") == expected
    starts = runtime.sent("turn/start")
    if expected is None:
        assert [start.get("effort") for start in starts] == ["low"]
    else:
        # The fresh thread's turn carries what this request asked for.
        assert starts[1].get("effort") == options.get("reasoning_effort")
        assert starts[1].get("serviceTier") == options.get("service_tier")
    assert runtime.alive() == []


@pytest.mark.asyncio
async def test_each_message_after_the_result_is_queued_as_its_own_message(
    tmp_path: Path, answering: _chatgpt._KeptThreads,  # noqa: F811
) -> None:
    runtime = AnswerRuntime(Waiting([asks("call-1", 7)], [*NARRATION]))
    provider, _ = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    await provider.generate([*SECOND, user("Record one."), user("Record two.")], tools=TOOLS)
    [connection] = runtime.connections
    assert [steer["input"] for steer in connection.methods("turn/steer")] == [
        [{"type": "text", "text": "Record one."}], [{"type": "text", "text": "Record two."}],
    ]
    sent = [method for method, _ in connection.sent]
    assert sent.index("answer") > max(i for i, m in enumerate(sent) if m == "turn/steer")


# ---- errors a fresh thread would only repeat --------------------------------------------

@pytest.mark.asyncio
@pytest.mark.parametrize(("code", "error"), [
    ("usageLimitExceeded", RateLimitError),
    ("contextWindowExceeded", TokenLimitError),
    ("serverOverloaded", ModelCapacityError),
])
async def test_allowance_size_and_capacity_errors_of_a_continued_thread_are_not_retried(
    tmp_path: Path, stable: _chatgpt._KeptThreads, code: str,  # noqa: F811
    error: type[Exception],
) -> None:
    runtime = Runtime(tool_turn("call-1"), failed_turn(code), text_turn())
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    with pytest.raises(error):
        await provider.generate(SECOND, tools=TOOLS)
    assert len(runtime.connections) == 1 and runtime.alive() == []
    assert audit.reuse[1]["retried_fresh"] is False and len(runtime.turns) == 1


@pytest.mark.asyncio
async def test_a_stalled_continued_thread_is_not_retried(
    tmp_path: Path, stable: _chatgpt._KeptThreads,  # noqa: F811
) -> None:
    runtime = Runtime(tool_turn("call-1"), Turn([]), text_turn())
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    with pytest.raises(ProviderTimeoutError):
        await provider.generate(SECOND, tools=TOOLS, timeout=0.3)
    assert len(runtime.connections) == 1 and runtime.alive() == []


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stable", "answer"])
async def test_a_stop_that_surfaces_as_a_lost_child_is_a_cancellation_not_a_retry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pool: _chatgpt._KeptThreads,  # noqa: F811
    mode: str,
) -> None:
    from sylvia_gm.provider._codex_rpc import CodexConnectionError

    monkeypatch.setenv("SYLVIA_CHATGPT_THREAD", mode)
    first = tool_turn("call-1") if mode == "stable" else Waiting([asks("call-1", 7)], [])
    runtime = AnswerRuntime(first, Turn([]), text_turn())
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    [connection] = runtime.connections
    waited = connection.next_event

    def stopped_while_waiting(timeout: float = 0.25) -> dict[str, Any] | None:
        # The stop arrives during the wait, and the wait then finds the child gone.
        event = waited(timeout)
        if event is not None:
            return event
        connection.request_stop()
        raise CodexConnectionError("The ChatGPT runtime stopped before completing its response.")

    connection.next_event = stopped_while_waiting  # type: ignore[method-assign]
    with pytest.raises(asyncio.CancelledError):
        await provider.generate(SECOND, tools=TOOLS)
    # No fresh thread, so no further model request after the stop.
    assert len(runtime.connections) == 1 and runtime.alive() == [] and len(pool) == 0
    assert audit.reuse[1]["retried_fresh"] is False
    assert audit.receipts[1]["status"] == "cancelled"


# ---- what a kept thread is matched on, and leftovers of its last turn ---------------------

@pytest.mark.asyncio
async def test_a_request_for_another_model_never_continues_a_kept_thread(
    tmp_path: Path, stable: _chatgpt._KeptThreads,  # noqa: F811
) -> None:
    runtime = Runtime(tool_turn("call-1"), tool_turn("call-2"))
    provider, _ = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    [kept] = stable._idle  # noqa: SLF001
    dynamic = kept.dynamic_tools
    assert kept.extended_by(kept.home, kept.model, kept.instructions, dynamic, items_of(SECOND))
    assert not kept.extended_by(
        kept.home, "gpt-6.1-terra", kept.instructions, dynamic, items_of(SECOND),
    )
    chosen, _ = stable.take(
        kept.home, "gpt-6.1-terra", kept.instructions, dynamic, items_of(SECOND),
    )
    assert chosen is None
    await provider.close()


@pytest.mark.asyncio
async def test_leftover_events_of_the_finished_turn_are_not_read_as_the_next_turns(
    tmp_path: Path, stable: _chatgpt._KeptThreads,  # noqa: F811
) -> None:
    runtime = Runtime(tool_turn("call-1"), text_turn())
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    [connection] = runtime.connections
    # A late end-of-turn notice of the interrupted turn, arriving while the thread is idle.
    connection._notifications.put({"method": "turn/completed", "params": {  # noqa: SLF001
        "threadId": "thread-1", "turn": {"status": "interrupted"},
    }})
    response = await provider.generate(SECOND, tools=TOOLS)
    assert response.content == "Synthetic narration." and audit.reuse[1]["continued"] is True
    assert audit.reuse[1]["retried_fresh"] is False


@pytest.mark.asyncio
async def test_events_after_the_call_are_cleared_before_the_thread_is_kept(
    tmp_path: Path, stable: _chatgpt._KeptThreads,  # noqa: F811
) -> None:
    trailing = tool_turn("call-1")
    trailing.after_interrupt.append(
        {"method": "thread/status/changed", "params": {"status": "idle"}},
    )
    runtime = Runtime(trailing, text_turn())
    provider, _ = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    [connection] = runtime.connections
    assert connection.alive and connection._notifications.empty()  # noqa: SLF001
    await provider.close()


# ---- closing ----------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_cancellation_while_stale_threads_close_still_closes_all_of_them(
    tmp_path: Path, stable: _chatgpt._KeptThreads,  # noqa: F811
) -> None:
    old = Runtime(*(tool_turn(f"call-{n}") for n in range(3)))
    first, _ = provider_for(old, tmp_path / "old")
    for n in range(3):
        await first.generate([SYSTEM, user(f"Session {n}.")], tools=TOOLS)
    assert len(stable) == 3 and len(old.alive()) == 3
    for connection in old.connections:
        original = connection.close

        def slow_close(original=original) -> None:
            time.sleep(0.2)
            original()

        connection.close = slow_close  # type: ignore[method-assign]

    new = Runtime(tool_turn("call-new"))
    second, _ = provider_for(new, tmp_path / "new")
    task = asyncio.create_task(second.generate([SYSTEM, user("New home.")], tools=TOOLS))
    await asyncio.sleep(0.05)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert len(stable) == 0 and old.alive() == [] and new.alive() == []


@pytest.mark.asyncio
async def test_a_cancellation_while_a_finished_requests_thread_closes_still_writes_the_receipt(
    tmp_path: Path, stable: _chatgpt._KeptThreads,  # noqa: F811
) -> None:
    runtime = Runtime(tool_turn("call-1"))
    provider, audit = provider_for(runtime, tmp_path)
    stream = provider.generate_stream(FIRST, tools=TOOLS)
    async for chunk in stream:
        if chunk.tool_call_delta:
            break
    [connection] = runtime.connections
    original = connection.close

    def slow_close() -> None:
        time.sleep(0.2)
        original()

    connection.close = slow_close  # type: ignore[method-assign]
    closing = asyncio.create_task(stream.aclose())
    await asyncio.sleep(0.05)
    closing.cancel()
    with pytest.raises(asyncio.CancelledError):
        await closing
    assert runtime.alive() == [] and len(stable) == 0
    assert len(audit.receipts) == 1 and audit.reuse[0]["kept"] is False


@pytest.mark.asyncio
async def test_a_turn_left_waiting_is_closed_sooner_than_an_idle_thread(
    tmp_path: Path, answering: _chatgpt._KeptThreads,  # noqa: F811
) -> None:
    assert answering.pending_idle_seconds == 90.0 < answering.idle_seconds == 600.0
    answering.pending_idle_seconds = 0.05
    runtime = AnswerRuntime(Waiting([asks("call-1", 7)]))
    provider, _ = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    assert runtime.alive() == runtime.connections
    for _ in range(100):
        if not runtime.alive():
            break
        await asyncio.sleep(0.02)
    assert runtime.alive() == [] and len(answering) == 0


@pytest.mark.asyncio
async def test_an_idle_waiting_turn_past_its_limit_is_closed_when_the_next_request_arrives(
    tmp_path: Path, answering: _chatgpt._KeptThreads,  # noqa: F811
) -> None:
    runtime = AnswerRuntime(Waiting([asks("call-1", 7)]), text_turn())
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    [kept] = answering._idle  # noqa: SLF001
    kept.idle_since -= 91
    await provider.generate(SECOND, tools=TOOLS)
    assert not runtime.connections[0].alive and audit.reuse[1]["continued"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["stable", "answer"])
async def test_release_idle_closes_this_models_kept_threads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pool: _chatgpt._KeptThreads,  # noqa: F811
    mode: str,
) -> None:
    monkeypatch.setenv("SYLVIA_CHATGPT_THREAD", mode)
    turn = tool_turn("call-1") if mode == "stable" else Waiting([asks("call-1", 7)])
    runtime = AnswerRuntime(turn)
    provider, _ = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    assert runtime.alive() == runtime.connections and len(pool) == 1
    await provider.release_idle()
    assert runtime.alive() == [] and len(pool) == 0
    await provider.release_idle()


@pytest.mark.asyncio
async def test_without_the_switch_release_idle_does_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pool: _chatgpt._KeptThreads,  # noqa: F811
) -> None:
    monkeypatch.delenv("SYLVIA_CHATGPT_THREAD", raising=False)
    provider, _ = provider_for(Runtime(), tmp_path)
    await provider.release_idle()
    assert len(pool) == 0


@pytest.mark.asyncio
async def test_exit_cleanup_closes_every_kept_thread(
    tmp_path: Path, stable: _chatgpt._KeptThreads,  # noqa: F811
) -> None:
    runtime = Runtime(tool_turn("call-1"), tool_turn("call-2"))
    provider, _ = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    await provider.generate([SYSTEM, OPENING, user("Another session.")], tools=TOOLS)
    assert len(runtime.alive()) == len(stable) > 0
    _chatgpt._close_all_kept_threads()  # noqa: SLF001
    assert runtime.alive() == [] and len(stable) == 0


def test_a_process_that_exits_with_a_kept_thread_closes_it() -> None:
    """The cleanup is registered for interpreter exit, not only callable."""
    import os
    import subprocess
    import sys

    script = "\n".join([
        "from pathlib import Path",
        "from sylvia_gm.provider import _chatgpt",
        "class Connection:",
        "    def close(self):",
        "        print('kept thread closed at exit')",
        "kept = _chatgpt._KeptThread(connection=Connection(), home=Path('.'), model='m',",
        "    thread_id='t', instructions='', dynamic_tools=[], items=[], generation=0)",
        "assert _chatgpt._KEPT_THREADS.keep(kept) == []",
        "print('exiting with', len(_chatgpt._KEPT_THREADS), 'kept')",
    ])
    done = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=120,
        env={**os.environ, "PYTHONPATH": os.pathsep.join(sys.path)}, check=False,
    )
    assert done.returncode == 0, done.stderr[-400:]
    assert done.stdout.splitlines()[:2] == ["exiting with 1 kept", "kept thread closed at exit"]



# ---- the account ------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_a_request_that_overlaps_an_account_change_keeps_nothing(
    tmp_path: Path, stable: _chatgpt._KeptThreads,  # noqa: F811
) -> None:
    runtime = Runtime(tool_turn("call-1"))
    provider, _ = provider_for(runtime, tmp_path)

    class SignOutMidRequest(Audit):
        async def on_request(self, context: Any) -> None:
            if (context.request_json or {}).get("method") == "turn/start":
                _chatgpt._close_kept_threads_for(tmp_path)  # noqa: SLF001

    provider.hooks.register(SignOutMidRequest())
    await provider.generate(FIRST, tools=TOOLS)
    # The thread was started for the account that has since changed.
    assert runtime.alive() == [] and len(stable) == 0


def test_a_sign_in_that_completes_announces_the_account_change(tmp_path: Path) -> None:
    changed: list[Path] = []
    add_account_change_listener(changed.append)

    class Connection:
        home = tmp_path

        def __init__(self) -> None:
            self.events: list[dict[str, Any]] = []

        def start(self) -> None:
            pass

        def notifications(self) -> list[dict[str, Any]]:
            events, self.events = self.events, []
            return events

        def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
            if method == "account/login/start":
                return {"type": "chatgpt", "loginId": "login-1",
                        "authUrl": "https://auth.openai.com/consent"}
            return {"account": {"type": "chatgpt"}}

    connection = Connection()
    account = ChatGPTAccount(connection)  # type: ignore[arg-type]
    account.begin_login()
    # Switching accounts: the earlier account is still signed in, so this status read
    # already stops waiting for the sign-in. Its later completion must still be announced.
    assert account.status()["login_pending"] is False
    assert tmp_path not in changed
    connection.events = [
        {"method": "account/login/completed", "params": {"loginId": "login-1", "success": False}},
    ]
    account.status()
    assert tmp_path not in changed
    connection.events = [
        {"method": "account/login/completed", "params": {"loginId": "login-1", "success": True}},
    ]
    account.status()
    assert changed.count(tmp_path) == 1


@pytest.mark.asyncio
async def test_a_completed_sign_in_closes_threads_kept_during_the_consent(
    tmp_path: Path, stable: _chatgpt._KeptThreads,  # noqa: F811
) -> None:
    from sylvia_gm.provider._codex_rpc import account_changed

    runtime = Runtime(tool_turn("call-1"))
    provider, _ = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    assert len(stable) == 1
    account_changed(tmp_path)
    assert runtime.alive() == [] and len(stable) == 0
