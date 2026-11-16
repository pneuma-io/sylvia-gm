"""The "answer" thread mode: a tool call is answered inside the runtime turn that made it.

The runtime turn stays open at a Sylvia tool call. The next request, which brings the call's
result, answers that call instead of starting a further turn. Every runtime here is the
in-memory script of the stable-thread tests: no Codex process, credential or network is used.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from sylvia_gm.provider import _chatgpt
from sylvia_gm.provider._chatgpt import _AnswerInPlaceError, _in_place_answer
from tests.test_chatgpt_stable_thread import (
    FIRST,
    SECOND,
    TOOLS,
    Runtime,
    Scripted,
    Turn,
    call,
    items_of,
    pool,  # noqa: F401  (fixture)
    provider_for,
    result,
    text_turn,
    tool_turn,
    usage,
    user,
)

DOOR = "The synthetic door opens."


def asks(call_id: str, request_id: int) -> dict[str, Any]:
    """A Sylvia tool call as the runtime sends it: a request that waits for its answer."""
    return {"id": request_id, "method": "item/tool/call", "params": {
        "namespace": "sylvia", "tool": "choose", "callId": call_id, "arguments": {},
    }}


NARRATION = [
    {"method": "item/agentMessage/delta", "params": {"delta": "Synthetic narration."}},
    usage(160, 150),
    {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
]


class Waiting(Turn):
    """A turn that waits at each tool call, and what it does once each call is answered."""

    def __init__(self, events: list[dict[str, Any]], *after_answer: list[dict[str, Any]]) -> None:
        super().__init__(events)
        self.after_answer = list(after_answer)


class Answering(Scripted):
    """The scripted connection, also playing the rest of a turn when a call is answered."""

    def _write(self, message: dict[str, Any]) -> None:
        if "method" in message:
            super()._write(message)
            return
        # Sylvia's answer to the tool call the open turn is waiting on.
        self.sent.append(("answer", message))
        assert self.current is not None
        turn = self.current[2]
        assert isinstance(turn, Waiting)
        self._emit(turn.after_answer.pop(0))

    def _emit(self, events: list[dict[str, Any]]) -> None:
        assert self.current is not None
        thread_id, turn_id, _ = self.current
        for event in events:
            params = dict(event["params"])
            params.setdefault("threadId", thread_id)
            if event["method"] != "turn/completed":
                params.setdefault("turnId", turn_id)
            message = {"method": event["method"], "params": params}
            if "id" in event:
                message["id"] = event["id"]
            self._notifications.put(message)


class AnswerRuntime(Runtime):
    def factory(
        self, home: Path, executable: Path | None = None, *,
        dynamic_tools: frozenset[str] = frozenset(),
    ) -> Scripted:
        connection = Answering(home, self, dynamic_tools=dynamic_tools)
        self.connections.append(connection)
        return connection


@pytest.fixture
def answering(monkeypatch: pytest.MonkeyPatch, pool: _chatgpt._KeptThreads):  # noqa: F811
    monkeypatch.setenv("SYLVIA_CHATGPT_THREAD", "answer")
    return pool


def answer_to(request_id: int, text: str) -> dict[str, Any]:
    return {"id": request_id, "result": {
        "contentItems": [{"type": "inputText", "text": text}], "success": True,
    }}


@pytest.mark.asyncio
async def test_a_call_is_answered_inside_the_turn_that_made_it(
    tmp_path: Path, answering: _chatgpt._KeptThreads,
) -> None:
    runtime = AnswerRuntime(Waiting(
        [asks("call-1", 7)],
        [usage(100, 0), asks("call-2", 8)],
        [usage(150, 140), *NARRATION],
    ))
    provider, audit = provider_for(runtime, tmp_path)

    first = await provider.generate(FIRST, tools=TOOLS)
    [connection] = runtime.connections
    assert [c.id for c in first.tool_calls] == ["call-1"] and first.finish_reason == "tool_calls"
    # The turn is left waiting: no interrupt, and the runtime has reported no usage yet.
    assert connection.methods("turn/interrupt") == [] and not first.usage_reported
    assert connection.alive and len(answering) == 1

    second = await provider.generate(SECOND, tools=TOOLS)
    assert [c.id for c in second.tool_calls] == ["call-2"]
    assert connection.methods("answer") == [answer_to(7, DOOR)]
    # The usage the runtime reports on an answer belongs to the response that made the call:
    # it is recorded as the previous response's and never attached to this one.
    assert not second.usage_reported and second.usage.prompt_tokens == 0

    third_messages = [*SECOND, call("call-2"), result("call-2", "It is dark inside."),
                      user("SYLVIA_GM_STATE_UPDATE: the lamp is out.")]
    third = await provider.generate(third_messages, tools=TOOLS)
    assert third.content == "Synthetic narration." and third.finish_reason == "stop"
    assert third.usage.prompt_tokens == 160 and third.cache_usage.cache_read_tokens == 150
    # A user-role record after the result is queued into the open turn before the answer.
    assert connection.methods("turn/steer") == [{
        "threadId": "thread-1", "expectedTurnId": "turn-1",
        "input": [{"type": "text", "text": "SYLVIA_GM_STATE_UPDATE: the lamp is out."}],
    }]
    sent = [method for method, _ in connection.sent]
    assert sent.index("turn/steer") < len(sent) - 1 - sent[::-1].index("answer")
    assert connection.methods("answer")[1] == answer_to(8, "It is dark inside.")

    # One process, one thread, one turn; the history went in once and nothing was re-sent.
    assert len(runtime.connections) == 1 and len(connection.children) == 1
    assert len(connection.methods("thread/start")) == 1
    assert len(connection.methods("turn/start")) == 1
    assert connection.methods("turn/interrupt") == []
    assert [p["items"] for p in connection.methods("thread/inject_items")] == [items_of(FIRST)]
    assert audit.reuse == [
        {"continued": False, "held_items": 0, "injected_items": 1,
         "retried_fresh": False, "kept": True, "answered_in_place": False,
         "usage_reports": []},
        {"continued": True, "held_items": 1, "injected_items": 2,
         "retried_fresh": False, "kept": True, "answered_in_place": True,
         "usage_reports": [{"response": "previous", "input": 100, "cached": 0, "output": 5}]},
        {"continued": True, "held_items": 3, "injected_items": 3,
         "retried_fresh": False, "kept": False, "answered_in_place": True,
         "usage_reports": [
             {"response": "previous", "input": 150, "cached": 140, "output": 5},
             {"response": "own", "input": 160, "cached": 150, "output": 5},
         ]},
    ]
    # Every model response has exactly one report across the three records.
    reports = [report for record in audit.reuse for report in record["usage_reports"]]
    assert [report["input"] for report in reports] == [100, 150, 160]
    assert [r["thread_reuse"] for r in audit.receipts] == audit.reuse
    # The narrated reply ended the turn, so its thread is closed like any text reply.
    assert runtime.alive() == [] and len(answering) == 0


@pytest.mark.asyncio
async def test_a_request_that_does_not_answer_the_waiting_call_starts_afresh(
    tmp_path: Path, answering: _chatgpt._KeptThreads,
) -> None:
    runtime = AnswerRuntime(Waiting([asks("call-1", 7)]), text_turn())
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    waiting = runtime.connections[0]

    # The history grew, but not by the call and its result: the open turn cannot take it.
    messages = [*FIRST, user("Wait, stop.")]
    response = await provider.generate(messages, tools=TOOLS)
    assert response.content == "Synthetic narration."
    assert waiting.methods("answer") == [] and waiting.methods("turn/steer") == []
    assert not waiting.alive and len(runtime.connections) == 2
    fresh = runtime.connections[1]
    assert fresh.methods("thread/inject_items")[0]["items"] == items_of(messages)
    assert audit.reuse[1] == {
        "continued": False, "held_items": 0, "injected_items": 2,
        "retried_fresh": True, "fresh_reason": "no_call_result", "kept": False,
        "answered_in_place": False,
        "usage_reports": [{"response": "own", "input": 120, "cached": 100, "output": 5}],
    }
    assert runtime.alive() == [] and len(answering) == 0


@pytest.mark.asyncio
async def test_a_turn_that_ended_while_it_waited_is_not_answered(
    tmp_path: Path, answering: _chatgpt._KeptThreads,
) -> None:
    runtime = AnswerRuntime(Waiting([asks("call-1", 7)]), text_turn())
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    waiting = runtime.connections[0]
    waiting._notifications.put({"method": "turn/completed", "params": {  # noqa: SLF001
        "threadId": "thread-1", "turn": {"status": "failed"},
    }})

    response = await provider.generate(SECOND, tools=TOOLS)
    assert response.content == "Synthetic narration."
    assert waiting.methods("answer") == [] and not waiting.alive
    assert runtime.connections[1].methods("thread/inject_items")[0]["items"] == items_of(SECOND)
    assert audit.reuse[1]["retried_fresh"] is True
    assert audit.reuse[1]["answered_in_place"] is False
    assert runtime.alive() == []


@pytest.mark.asyncio
async def test_a_call_the_runtime_does_not_wait_on_is_interrupted_and_continued_as_before(
    tmp_path: Path, answering: _chatgpt._KeptThreads,
) -> None:
    # The stable-thread script sends its tool call as a notice without a request to answer.
    runtime = AnswerRuntime(tool_turn("call-1"), text_turn())
    provider, audit = provider_for(runtime, tmp_path)
    first = await provider.generate(FIRST, tools=TOOLS)
    assert first.usage_reported and first.usage.prompt_tokens == 100
    await provider.generate(SECOND, tools=TOOLS)
    [connection] = runtime.connections
    assert len(connection.methods("turn/interrupt")) == 1
    assert len(connection.methods("turn/start")) == 2 and connection.methods("answer") == []
    assert [p["items"] for p in connection.methods("thread/inject_items")] == [
        items_of(FIRST), items_of(SECOND)[1:],
    ]
    assert [r["answered_in_place"] for r in audit.reuse] == [False, False]
    assert [r["continued"] for r in audit.reuse] == [False, True]
    assert runtime.alive() == []


@pytest.mark.asyncio
async def test_closing_the_provider_ends_a_waiting_turn_and_leaves_no_child(
    tmp_path: Path, answering: _chatgpt._KeptThreads,
) -> None:
    runtime = AnswerRuntime(Waiting([asks("call-1", 7)]))
    provider, _ = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    assert runtime.alive() == runtime.connections and len(answering) == 1
    await provider.close()
    assert runtime.alive() == [] and len(answering) == 0


@pytest.mark.asyncio
async def test_the_stable_value_never_answers_in_place(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pool: _chatgpt._KeptThreads,  # noqa: F811
) -> None:
    monkeypatch.setenv("SYLVIA_CHATGPT_THREAD", "stable")
    after_interrupt = [usage(100, 90), {
        "method": "turn/completed", "params": {"turn": {"status": "interrupted"}},
    }]
    waiting = Waiting([asks("call-1", 7)])
    waiting.after_interrupt = after_interrupt
    runtime = AnswerRuntime(waiting, text_turn())
    provider, audit = provider_for(runtime, tmp_path)
    await provider.generate(FIRST, tools=TOOLS)
    await provider.generate(SECOND, tools=TOOLS)
    [connection] = runtime.connections
    assert len(connection.methods("turn/interrupt")) == 1 and connection.methods("answer") == []
    assert len(connection.methods("turn/start")) == 2
    assert all("answered_in_place" not in record for record in audit.reuse)


def tail(*messages: dict[str, Any]) -> list[dict[str, Any]]:
    """The items a request appends after FIRST."""
    return items_of([*FIRST, *messages])[len(items_of(FIRST)):]


def test_the_answer_is_the_calls_result_and_the_user_messages_after_it() -> None:
    assert _in_place_answer(tail(call("call-1"), result("call-1")), "call-1") == (DOOR, [])
    assert _in_place_answer(
        tail(call("call-1"), result("call-1"), user("Record one."), user("I go in.")), "call-1",
    ) == (DOOR, ["Record one.", "I go in."])
    # The model's own words before the call are already in the thread, and must be the same.
    spoken = {"role": "assistant", "content": "I try the handle.", "tool_calls": [{
        "id": "call-1", "type": "function", "function": {"name": "choose", "arguments": "{}"},
    }]}
    said = tail(spoken, result("call-1"))
    assert _in_place_answer(said, "call-1", "I try the handle.") == (DOOR, [])
    for held in ("", "I try the handle", "Something else."):
        with pytest.raises(_AnswerInPlaceError) as refused:
            _in_place_answer(said, "call-1", held)
        assert refused.value.reason == "model_text_differs"
        with pytest.raises(_AnswerInPlaceError):
            _in_place_answer(said, "call-1", held, dropped_draft_ok=True)
    # Text the thread holds and the request dropped is refused under its own code, unless
    # the experimental value accepts exactly that case.
    dropped = tail(call("call-1"), result("call-1"))
    with pytest.raises(_AnswerInPlaceError) as refused:
        _in_place_answer(dropped, "call-1", "I try the handle.")
    assert refused.value.reason == "model_text_dropped"
    assert _in_place_answer(
        dropped, "call-1", "I try the handle.", dropped_draft_ok=True,
    ) == (DOOR, [])
    # A result that is not text is sent as its JSON.
    items = tail(call("call-1"), result("call-1"))
    items[-1]["output"] = {"opened": True}
    assert _in_place_answer(items, "call-1") == ('{"opened": true}', [])


def test_narration_the_model_wrote_in_the_call_is_not_sent_back_to_it() -> None:
    narrated = {"role": "assistant", "content": None, "tool_calls": [{
        "id": "call-1", "type": "function",
        "function": {"name": "choose", "arguments": '{"narration": "The door creaks open."}'},
    }]}
    copy = {"role": "assistant", "content": "The door creaks open."}
    assert _in_place_answer(
        tail(narrated, result("call-1"), copy, user("I go in.")), "call-1",
    ) == (DOOR, ["I go in."])
    # The same holds when the result carries the text.
    echoed = result("call-1", '{"committed": true, "narration": "The door creaks open."}')
    assert _in_place_answer(tail(call("call-1"), echoed, copy), "call-1")[1] == []
    # Only a whole text value counts: a fragment of one is something the model never wrote.
    fragment = {"role": "assistant", "content": "The door"}
    with pytest.raises(_AnswerInPlaceError) as refused:
        _in_place_answer(tail(call("call-1"), echoed, fragment), "call-1")
    assert refused.value.reason == "tail_not_deliverable"


@pytest.mark.parametrize("appended", [
    [],
    [user("Wait, stop.")],
    [call("call-1")],
    [call("call-9"), result("call-9")],
    [call("call-1"), result("call-1"), {"role": "assistant", "content": "Something new."}],
    [call("call-1"), result("call-1"), call("call-2"), result("call-2")],
])
def test_anything_else_cannot_be_delivered_into_the_open_turn(
    appended: list[dict[str, Any]],
) -> None:
    with pytest.raises(_AnswerInPlaceError):
        _in_place_answer(tail(*appended), "call-1")
