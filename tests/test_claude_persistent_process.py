"""One Claude Code process per GM session (issue 171), against a stand-in at the process boundary.

The stand-in keeps one conversation open in stream-json mode, as Claude Code does: each prompt
written to stdin, or answer to a waiting Sylvia call, starts its next scripted reply, and it
writes its session file the way the per-step tests' scripted CLI does. No CLI and no model call.
"""

from __future__ import annotations

import asyncio
import json
import queue
import re
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from sylvia_gm.agent._context_ledger import CONTINUE_RECORD_TEXT
from sylvia_gm.agent._provider_session import ProviderSessionAdapter
from sylvia_gm.provider import _claude, _claude_kept
from sylvia_gm.provider._claude import ClaudeProvider
from sylvia_gm.provider._claude_kept import (
    ContinuationMismatchError,
    KeptProcess,
    KeptProcesses,
    continuation,
    process_setting,
)
from sylvia_gm.provider._claude_native import CURRENT_NATIVE_ROUTE, FILLER_TEXT
from sylvia_gm.provider._exceptions import ProviderConnectionError, ProviderTimeoutError
from tests.gm_context_contract import first_step_records, provider_messages
from tests.test_claude_native_route import (
    TOOLS,
    Chain,
    Hook,
    assert_nothing_private_left,
    chain_canonicals,
    claude_model,
    text_reply,
    tool_reply,
)


class PersistentCLI:
    """Claude Code kept open in stream-json mode: one scripted reply per prompt or answer."""

    replies: list[list[Any]] = []
    instances: list[PersistentCLI] = []

    def __init__(self, executable, workspace, arguments, environment):
        self.workspace, self.arguments, self.environment = workspace, arguments, environment
        self.events: queue.Queue[Any] = queue.Queue()
        self.writes: list[dict[str, Any]] = []
        self.closed = False
        self.exited = False
        #: The connection breaks on the next write, though nothing showed it while idle.
        self.broken = False
        self.rows: list[dict[str, Any]] = []
        self.session_file: Path | None = None
        PersistentCLI.instances.append(self)

    def argument(self, flag):
        return self.arguments[self.arguments.index(flag) + 1] if flag in self.arguments else None

    def start(self):
        resume = self.argument("--resume")
        transcript = Path(resume).read_bytes().decode() if resume else ""
        self.rows = [json.loads(line) for line in transcript.splitlines()]
        session = self.argument("--session-id")
        if session is not None:
            cwd = re.sub(r"[^A-Za-z0-9]", "-", str(self.workspace))
            self.session_file = (Path(self.environment["CLAUDE_CONFIG_DIR"]) / "projects"
                                 / cwd / f"{session}.jsonl")
            self.session_file.parent.mkdir(parents=True, exist_ok=True)

    def _save(self):
        if self.session_file is not None:
            self.session_file.write_text("".join(json.dumps(r) + "\n" for r in self.rows),
                                         encoding="utf-8")

    def _reply(self):
        for event in PersistentCLI.replies.pop(0):
            self.events.put(event)

    def write(self, event):
        if self.closed or self.exited or self.broken:
            raise ProviderConnectionError("The Claude runtime has stopped.")
        self.writes.append(event)
        if event.get("type") == "user":
            if self.rows and self.rows[-1]["type"] == "user":
                self.rows.append({"type": "assistant", "message": {
                    "role": "assistant", "model": "<synthetic>",
                    "content": [{"type": "text", "text": FILLER_TEXT}]}})
            first = not any(r["type"] == "attachment" for r in self.rows)
            self.rows.append({"type": "user", "message": event["message"]})
            if first:
                self.rows.extend({"type": "attachment", **r} for r in first_step_records())
            self.rows.append({"type": "api-request", "message": "private request copy"})
            self._save()
            self._reply()
        elif event.get("type") == "control_response" and (
            event["response"].get("request_id") == "call"
        ):
            self.rows.append({"type": "user", "message": {"role": "user", "content": [
                {"type": "tool_result", "content": event["response"]["response"][
                    "mcp_response"]["result"]["content"]}]}})
            self.rows.append({"type": "api-request", "message": "private request copy"})
            self._save()
            self._reply()

    def answers(self) -> list[dict[str, Any]]:
        return [w["response"]["response"]["mcp_response"]["result"] for w in self.writes
                if w.get("type") == "control_response"
                and w["response"].get("request_id") == "call"]

    def prompts(self) -> list[dict[str, Any]]:
        return [w for w in self.writes if w.get("type") == "user"]

    def next_event(self, timeout):
        try:
            event = self.events.get(timeout=min(timeout, 5))
        except queue.Empty:
            raise ProviderTimeoutError("Claude stopped responding.") from None
        if event is None:
            raise ProviderConnectionError("Claude exited before completing its response.")
        return event

    def drain(self):
        events = []
        while True:
            try:
                events.append(self.events.get_nowait())
            except queue.Empty:
                break
        if self.exited and None not in events:
            events.append(None)
        return events

    def exit(self):
        self.exited = True

    def close(self):
        self.closed = True


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    monkeypatch.delenv("SYLVIA_CLAUDE_PROCESS", raising=False)
    monkeypatch.setattr(_claude, "find_runtime", lambda _: Path(sys.executable))
    monkeypatch.setattr(_claude, "runtime_version", lambda *_: "2.1.322")
    PersistentCLI.replies, PersistentCLI.instances = [], []
    home = tmp_path / "runtime"
    provider = ClaudeProvider(home, claude_model(), connection_factory=PersistentCLI)
    hook = Hook()
    provider.hooks.register(hook)
    return provider, home, hook


def step(provider, chain, canonical, reply):
    """One ledger-mode request: plan, set the route as the adapter does, run the provider."""
    frame = chain.ledger.plan(canonical, request_context_id="ctx", tools=TOOLS,
                              check_saved_tip=False)
    chain.ledger.commit(frame)
    route = ProviderSessionAdapter._claude_native_route(chain.ledger, frame, None, False)
    PersistentCLI.replies.append(reply)

    async def go():
        CURRENT_NATIVE_ROUTE.set(route)
        return [c async for c in provider.generate_stream(provider_messages(frame.wire), TOOLS)]

    return asyncio.run(go())


def reuse(hook) -> list[dict[str, Any]]:
    return [r["params"] for r in hook.requests if r.get("method") == "claude/process_reuse"]


SCRIPT = {"fresh": text_reply("The door creaks."), "player": tool_reply("c1", 1),
          "tool_result_with_records": tool_reply("c2", 2),
          "tool_result_no_records": text_reply("You reach the bottom."),
          "consecutive_user_entries": text_reply("Light floods the room."),
          "after_date_change": text_reply("Onward.")}


def test_one_process_serves_a_whole_session_of_turns_and_tool_rounds(runtime):
    provider, home, hook = runtime
    chain = Chain()
    finishes = []
    for name, canonical in chain_canonicals():
        chunks = step(provider, chain, canonical, SCRIPT[name])
        finishes.append(chunks[-1].finish_reason)
    assert finishes == ["stop", "tool_calls", "tool_calls", "stop", "stop", "stop"]
    # One process for the whole session, started without a transcript and never closed.
    [cli] = PersistentCLI.instances
    assert "--resume" not in cli.arguments and not cli.closed
    # Turns after a text reply are new stdin prompts; each tool round answered the call.
    assert len(cli.prompts()) == 4
    first, second = cli.answers()
    texts = [b["text"] for b in first["content"]]
    assert texts[0] == "Rolled 14." and len(texts) > 1
    assert any("SYLVIA_GM_EXACT_NARRATION_FACTS" in t for t in texts[1:])
    assert all(t.startswith("[host-seal ") for t in texts[1:])
    assert not any(t.endswith(CONTINUE_RECORD_TEXT) for t in texts)
    assert [b["text"] for b in second["content"]][0] == "Rolled 3."
    audits = reuse(hook)
    assert [a["continued"] for a in audits] == [False, True, True, True, True, True]
    assert [a["answered_in_place"] for a in audits] == [False, False, True, True, False, False]
    assert all(a["kept"] for a in audits)
    # The context records of the first prompt were saved for the ledger as before.
    assert sorted(chain.ledger.state.native)
    # The kept process's session file lives as long as it does, and no longer.
    assert cli.session_file is not None and cli.session_file.exists()
    _claude_kept.KEPT_PROCESSES.close_all()
    assert cli.closed
    assert_nothing_private_left(home)


def test_a_request_that_does_not_extend_the_kept_process_starts_a_fresh_one(runtime):
    provider, home, hook = runtime
    chain = Chain()
    steps = dict(chain_canonicals())
    step(provider, chain, steps["fresh"], SCRIPT["fresh"])
    step(provider, chain, steps["player"], SCRIPT["player"])
    # A Retry of the first turn: the conversation no longer extends what the process holds.
    retry = Chain()
    step(provider, retry, steps["fresh"], text_reply("The door creaks again."))
    old, new = PersistentCLI.instances
    assert old.closed and not new.closed
    assert reuse(hook)[-1]["continued"] is False
    _claude_kept.KEPT_PROCESSES.close_all()
    assert_nothing_private_left(home)


def test_a_kept_process_that_exited_is_replaced_by_a_fresh_one_from_the_transcript(runtime):
    provider, home, hook = runtime
    chain = Chain()
    steps = dict(chain_canonicals())
    step(provider, chain, steps["fresh"], SCRIPT["fresh"])
    PersistentCLI.instances[0].exit()
    chunks = step(provider, chain, steps["player"], SCRIPT["player"])
    assert chunks[-1].finish_reason == "tool_calls"
    old, new = PersistentCLI.instances
    assert old.closed and "--resume" in new.arguments
    assert reuse(hook)[-1]["fresh_reason"] == "runtime_exited"
    _claude_kept.KEPT_PROCESSES.close_all()
    assert_nothing_private_left(home)


def test_a_kept_process_that_fails_on_its_answer_is_retried_once_on_a_fresh_one(runtime):
    provider, home, hook = runtime
    chain = Chain()
    steps = dict(chain_canonicals())
    step(provider, chain, steps["fresh"], SCRIPT["fresh"])
    step(provider, chain, steps["player"], SCRIPT["player"])
    PersistentCLI.instances[0].broken = True
    chunks = step(provider, chain, steps["tool_result_with_records"],
                  SCRIPT["tool_result_with_records"])
    assert chunks[-1].finish_reason == "tool_calls"
    old, new = PersistentCLI.instances
    assert old.closed and "--resume" in new.arguments
    # One record for the request, saying it was retried fresh.
    assert reuse(hook)[2:] == [{"continued": False, "answered_in_place": False,
                                "fresh_reason": "runtime_error", "kept": True,
                                "retried_fresh": True}]
    _claude_kept.KEPT_PROCESSES.close_all()
    assert_nothing_private_left(home)


def test_the_switch_keeps_one_process_per_step(runtime, monkeypatch):
    provider, home, hook = runtime
    monkeypatch.setenv("SYLVIA_CLAUDE_PROCESS", "per-step")
    chain = Chain()
    for name, canonical in list(chain_canonicals())[:3]:
        step(provider, chain, canonical, SCRIPT[name])
        assert PersistentCLI.instances[-1].closed
        assert_nothing_private_left(home)
    assert len(PersistentCLI.instances) == 3
    assert reuse(hook) == [] and len(_claude_kept.KEPT_PROCESSES) == 0


@pytest.mark.parametrize(("value", "expected"), [
    (None, "persistent"), ("", "persistent"), ("persistent", "persistent"),
    ("per-step", "per-step"), (" Per-Step ", "per-step"), ("step", "per-step"),
    ("anything", "persistent"),
])
def test_the_process_switch(value, expected):
    environ = {} if value is None else {"SYLVIA_CLAUDE_PROCESS": value}
    assert process_setting(environ) == expected


def test_a_request_without_the_ledger_never_keeps_a_process(runtime):
    provider, home, hook = runtime
    PersistentCLI.replies.append(text_reply("Hello."))
    messages = [{"role": "system", "content": "Builder."}, {"role": "user", "content": "Hi"}]

    async def go():
        return [c async for c in provider.generate_stream(messages, TOOLS)]

    asyncio.run(go())
    assert PersistentCLI.instances[0].closed and len(_claude_kept.KEPT_PROCESSES) == 0


def test_stop_releases_the_kept_process_and_its_files(runtime):
    provider, home, hook = runtime
    chain = Chain()
    steps = dict(chain_canonicals())
    step(provider, chain, steps["fresh"], SCRIPT["fresh"])
    step(provider, chain, steps["player"], SCRIPT["player"])
    assert len(_claude_kept.KEPT_PROCESSES) == 1
    asyncio.run(provider.release_idle())
    assert PersistentCLI.instances[0].closed and len(_claude_kept.KEPT_PROCESSES) == 0
    assert_nothing_private_left(home)


def test_an_idle_process_is_closed_with_its_files_after_the_idle_limit(runtime, monkeypatch):
    provider, home, hook = runtime
    monkeypatch.setattr(_claude_kept, "KEPT_PROCESSES", KeptProcesses(idle_seconds=0.2))
    chain = Chain()
    step(provider, chain, dict(chain_canonicals())["fresh"], SCRIPT["fresh"])
    cli = PersistentCLI.instances[0]
    deadline = time.monotonic() + 5
    # The timer closes the process, then removes its files.
    while (not cli.closed or cli.session_file.exists()) and time.monotonic() < deadline:
        time.sleep(0.02)
    assert cli.closed and len(_claude_kept.KEPT_PROCESSES) == 0
    assert_nothing_private_left(home)


def test_a_failed_reply_in_a_kept_process_closes_it(runtime):
    provider, home, hook = runtime
    chain = Chain()
    steps = dict(chain_canonicals())
    step(provider, chain, steps["fresh"], SCRIPT["fresh"])
    failing = [{"type": "result", "subtype": "error_during_execution", "is_error": True}]
    with pytest.raises(Exception):  # noqa: B017 - the runtime's own failure classification
        step(provider, chain, steps["player"], failing)
    assert PersistentCLI.instances[0].closed and len(_claude_kept.KEPT_PROCESSES) == 0
    assert_nothing_private_left(home)


def test_a_release_during_a_request_keeps_nothing_after_it(runtime):
    provider, home, hook = runtime
    chain = Chain()
    steps = dict(chain_canonicals())

    class ReleaseMidRequest(Hook):
        async def on_request(self, context):
            if (context.request_json or {}).get("method") == "claude/native_step":
                _claude_kept.KEPT_PROCESSES.release(home.resolve())

    provider.hooks.register(ReleaseMidRequest())
    step(provider, chain, steps["fresh"], SCRIPT["fresh"])
    assert PersistentCLI.instances[0].closed and len(_claude_kept.KEPT_PROCESSES) == 0
    assert_nothing_private_left(home)


# ---- what a waiting call accepts as its answer ---------------------------------------------

def _kept(**overrides) -> KeptProcess:
    values = {"connection": None, "home": Path("."), "model": "m", "system": "s",
              "tools_key": "[]", "held": [{"role": "user", "content": "go"}],
              "session_id": "x", "step": Path("."), "config": Path("."), "cache": None,
              "pending": {"request_id": "call"}, "call_id": "c1",
              "call_arguments": {"narration": "The door opens."}}
    return KeptProcess(**{**values, **overrides})


def _call(text: str = "") -> dict[str, Any]:
    content = [{"type": "text", "text": text}] if text else []
    return {"role": "assistant", "content": [
        *content, {"type": "tool_use", "id": "c1", "name": "roll", "input": {}}]}


SEALED = "[host-seal abc] "


def _result(*extra: str) -> dict[str, Any]:
    return {"role": "user", "content": [
        {"type": "tool_result", "tool_use_id": "c1", "content": "Rolled 14."},
        *({"type": "text", "text": t} for t in extra)]}


def test_the_answer_carries_the_result_and_what_follows_but_not_the_continue_record():
    kept = _kept()
    history = [*kept.held, _call(), _result(SEALED + "STATE update", "I run!",
                                            SEALED + CONTINUE_RECORD_TEXT)]
    kind, answer = continuation(kept, history, CONTINUE_RECORD_TEXT)
    assert kind == "answer"
    assert [b["text"] for b in answer["content"]] == ["Rolled 14.", SEALED + "STATE update",
                                                      "I run!"]


def test_an_unsealed_continue_text_is_player_speech_and_is_delivered():
    kept = _kept()
    history = [*kept.held, _call(), _result(CONTINUE_RECORD_TEXT)]
    _kind, answer = continuation(kept, history, CONTINUE_RECORD_TEXT)
    assert [b["text"] for b in answer["content"]] == ["Rolled 14.", CONTINUE_RECORD_TEXT]


def test_sylvias_copy_of_narration_from_the_call_is_not_sent_again():
    kept = _kept()
    history = [*kept.held, _call(), _result(),
               {"role": "assistant", "content": "The door opens."},
               {"role": "user", "content": "I step through."}]
    _kind, answer = continuation(kept, history, CONTINUE_RECORD_TEXT)
    assert [b["text"] for b in answer["content"]] == ["Rolled 14.", "I step through."]


@pytest.mark.parametrize(("history_tail", "reason"), [
    ([], "no_reply_copy"),
    ([_call("Something else.")], "model_text_differs"),
    ([_call(), {"role": "user", "content": "no result"}], "no_call_result"),
    ([_call(), _result(), {"role": "assistant", "content": "New text."}],
     "tail_not_deliverable"),
    ([{"role": "assistant", "content": [{"type": "tool_use", "id": "other", "name": "roll",
                                         "input": {}}]}, _result()], "no_call_result"),
])
def test_a_request_that_does_not_answer_the_waiting_call_is_refused(history_tail, reason):
    kept = _kept()
    with pytest.raises(ContinuationMismatchError) as refused:
        continuation(kept, [*kept.held, *history_tail], CONTINUE_RECORD_TEXT)
    assert refused.value.reason == reason


def test_after_a_text_reply_the_next_user_content_is_the_prompt():
    kept = _kept(pending=None, call_id="", spoken="The door creaks.")
    history = [*kept.held, {"role": "assistant", "content": "The door creaks."},
               {"role": "user", "content": [{"type": "text", "text": "I go in."}]}]
    assert continuation(kept, history, CONTINUE_RECORD_TEXT) == (
        "prompt", [{"type": "text", "text": "I go in."}])


# ---- what nothing will continue -------------------------------------------------------------

def _waiting_session(provider) -> PersistentCLI:
    chain = Chain()
    steps = dict(chain_canonicals())
    step(provider, chain, steps["fresh"], SCRIPT["fresh"])
    step(provider, chain, steps["player"], SCRIPT["player"])
    assert len(_claude_kept.KEPT_PROCESSES) == 1
    return PersistentCLI.instances[0]


def _model_switch(provider, monkeypatch, *, to: str) -> None:
    from types import SimpleNamespace

    from sylvia_gm.interfaces import owner_process

    monkeypatch.setattr(owner_process, "selected_model_info",
                        lambda session, _choices: provider.model_info)
    prior = SimpleNamespace(provider_id="claude_runtime", model_id=provider.model_info.id)
    updated = SimpleNamespace(provider_id="claude_runtime", model_id=to)
    owner_process._release_previous_model_threads(prior, updated, ())  # type: ignore[arg-type]


@pytest.mark.parametrize("closer", [
    "sign_out", "sign_in_started", "model_switch", "reload_without_the_model", "provider_close",
])
def test_nothing_is_left_kept_once_nothing_could_continue_it(runtime, monkeypatch, closer):
    provider, home, _hook = runtime
    cli = _waiting_session(provider)
    if closer in {"sign_out", "sign_in_started"}:
        from sylvia_gm.config import claude_settings

        account = claude_settings.ClaudeAccount(home)
        monkeypatch.setattr(claude_settings.subprocess, "run", lambda *a, **k: None)
        monkeypatch.setattr(claude_settings.subprocess, "Popen",
                            lambda *a, **k: type("Login", (), {"poll": lambda self: 0})())
        if closer == "sign_out":
            account.logout(Path(sys.executable))
        else:
            account.begin_login(Path(sys.executable))
    elif closer == "model_switch":
        _model_switch(provider, monkeypatch, to="claude-runtime/another-model")
    elif closer == "reload_without_the_model":
        from sylvia_gm.interfaces import owner_process

        owner_process._release_unoffered_model_threads(frozenset(), frozenset({"other"}))
    else:
        asyncio.run(provider.close())
    assert cli.closed and len(_claude_kept.KEPT_PROCESSES) == 0
    assert_nothing_private_left(home)


def test_a_reload_that_still_offers_the_model_keeps_its_process(runtime, monkeypatch):
    from sylvia_gm.interfaces import owner_process

    provider, home, _hook = runtime
    cli = _waiting_session(provider)
    owner_process._release_unoffered_model_threads(
        frozenset(), frozenset({provider.model_info.wire_model_id}))
    _model_switch(provider, monkeypatch, to=provider.model_info.id)  # not a change of model
    assert not cli.closed and len(_claude_kept.KEPT_PROCESSES) == 1
    _claude_kept.KEPT_PROCESSES.close_all()
