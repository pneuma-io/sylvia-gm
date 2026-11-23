"""Generate reference vectors for sylvia-provider's ChatGPT provider rules (lane 5, PR 6a).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-provider/tests/vectors/generate_chatgpt_rules_vectors.py

Recorded from the frozen `provider/_chatgpt.py` and `provider/_lifecycle.py`: `thread_setting`,
`_reported_usage`, `_usage_figures`, `app_server_request`, `_call_tail`, `_in_place_answer`,
`_PendingCall.credit`, `_continuation_failed`, scripts over a `_KeptThreads` pool (with stand-in
connections that record when they close), and `LifecycleTiming` spans and snapshots.

Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from pathlib import Path

from sylvia_gm.provider import _chatgpt, _lifecycle
from sylvia_gm.provider._base import CacheUsage, TokenUsage
from sylvia_gm.provider._codex_rpc import CodexConnectionError
from sylvia_gm.provider._exceptions import (
    AuthenticationError, ContextCompactionRequiredError, IncompleteProviderStreamError, InvalidResponseError,
    ModelCapacityError, ModelNotFoundError, ProviderConnectionError, ProviderError, ProviderTimeoutError,
    RateLimitError, StaleConnectionError, TokenLimitError,
)

HERE = Path(__file__).parent
OUTPUT = HERE / "chatgpt_rules_vectors.json"


def settings() -> list[dict]:
    environs = [
        {}, {"SYLVIA_CHATGPT_THREAD": "fresh"}, {"SYLVIA_CHATGPT_THREAD": " Stable "},
        {"SYLVIA_CHATGPT_THREAD": "ANSWER-DRAFT"}, {"SYLVIA_CHATGPT_THREAD": "answer"},
        {"SYLVIA_CHATGPT_THREAD": "bogus"}, {"SYLVIA_GM_CONTEXT_MODE": "legacy"},
        {"SYLVIA_GM_CONTEXT_MODE": "ledger"}, {"SYLVIA_GM_CONTEXT_MODE": "legacy", "SYLVIA_CHATGPT_THREAD": "stable"},
        {"SYLVIA_GM_CONTEXT_MODE": "nonsense"},
    ]
    return [{"environ": environ, "setting": _chatgpt.thread_setting(environ)} for environ in environs]


def usage_view(usage: TokenUsage, cache: CacheUsage) -> dict:
    return {"tokens": [usage.prompt_tokens, usage.completion_tokens, usage.total_tokens, usage.reasoning_tokens],
            "cache": [cache.cache_read_tokens, cache.cache_write_tokens, cache.cache_miss_tokens,
                      cache.total_prompt_tokens]}


def usages() -> list[dict]:
    last = {"inputTokens": 100, "outputTokens": 10, "totalTokens": 110}
    cases = [
        {"tokenUsage": {"last": last}},
        {"tokenUsage": {"last": {**last, "cachedInputTokens": 80, "reasoningOutputTokens": 4, "cacheWriteInputTokens": 3}}},
        {"tokenUsage": {"last": {**last, "cachedInputTokens": 150}}},
        {"tokenUsage": {"last": {**last, "cachedInputTokens": -1, "reasoningOutputTokens": True}}},
        {"tokenUsage": {"last": {**last, "inputTokens": 1.0}}}, {"tokenUsage": {"last": {**last, "totalTokens": -5}}},
        {"tokenUsage": {"last": {"inputTokens": 1, "outputTokens": 1}}}, {"tokenUsage": {"total": last}},
        {"tokenUsage": {"last": [1]}}, {"tokenUsage": 5}, [1], None, {},
    ]
    out = []
    for params in cases:
        parsed = _chatgpt._reported_usage(params)
        entry = {"params": params, "parsed": None if parsed is None else usage_view(*parsed)}
        if parsed is not None:
            entry["figures"] = _chatgpt._usage_figures(*parsed)
        out.append(entry)
    return out


TOOLS = [
    {"type": "function", "function": {"name": "roll", "description": "Roll dice.",
                                      "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "look", "description": "Look.", "parameters": {"type": "object"}}},
]
SYSTEM = {"role": "system", "content": "You are the GM."}
USER = {"role": "user", "content": "I open the door."}
CALL = {"role": "assistant", "content": "Rolling.", "tool_calls": [
    {"id": "call_1", "type": "function", "function": {"name": "roll", "arguments": "{\"n\": 2}"}}]}
RESULT = {"role": "tool", "tool_call_id": "call_1", "content": "7"}
REPLY = {"role": "assistant", "content": "It creaks."}


def requests() -> list[dict]:
    cases = [
        {"messages": [SYSTEM, USER], "tools": TOOLS},
        {"messages": [SYSTEM, {"role": "system", "content": "Second."}, USER, CALL, RESULT, REPLY, USER], "tools": TOOLS},
        {"messages": [USER], "tools": None},
        {"messages": [SYSTEM, USER, {"role": "assistant", "content": None, "tool_calls": CALL["tool_calls"]}, RESULT],
         "tools": TOOLS[:1]},
        {"messages": [], "tools": []},
    ]
    out = []
    for case in cases:
        try:
            dynamic, items, system = _chatgpt.app_server_request("gpt-6-sol", case["messages"], case["tools"])
            out.append({**case, "dynamic": dynamic, "items": items, "system": system})
        except Exception as exc:  # noqa: BLE001
            out.append({**case, "error": [type(exc).__name__, str(exc)]})
    return out


def item(kind, **fields):
    return {"type": kind, **fields}


def msg(role, text):
    return item("message", role=role, content=[{"type": "output_text" if role == "assistant" else "input_text",
                                                "text": text}])


def fcall(call_id="call_1", arguments='{"n": 2}'):
    return item("function_call", call_id=call_id, name="roll", arguments=arguments, namespace="sylvia")


def fout(call_id="call_1", output="7"):
    return item("function_call_output", call_id=call_id, output=output)


def tails() -> list[dict]:
    cases = [
        ("spoken-call-result", [msg("assistant", "Rolling."), fcall(), fout()], "Rolling.", False),
        ("reasoning-first", [item("reasoning", summary=[]), msg("assistant", "Rol"), msg("assistant", "ling."), fcall(), fout()],
         "Rolling.", False),
        ("silent-call", [fcall(), fout(), msg("user", "Next.")], "", False),
        ("dropped-draft", [fcall(), fout()], "Rolling.", False),
        ("dropped-draft-ok", [fcall(), fout()], "Rolling.", True),
        ("text-differs", [msg("assistant", "Other."), fcall(), fout()], "Rolling.", False),
        ("text-where-none", [msg("assistant", "Extra."), fcall(), fout()], "", False),
        ("no-result", [fcall()], "", False),
        ("other-call", [fcall("call_2"), fout("call_2")], "", False),
        ("result-for-other", [fcall(), fout("call_2")], "", False),
        ("empty", [], "", False),
        ("user-after", [fcall(), fout(), msg("user", "Then I run."), msg("user", "Fast.")], "", False),
        ("copy-of-result", [fcall(), fout(output='{"said": "The goblin falls."}'), msg("assistant", "The goblin falls.")], "", False),
        ("copy-of-arguments", [fcall(arguments='{"narration": "You roll."}'), fout(), msg("assistant", "You roll.")], "", False),
        ("assistant-other", [fcall(), fout(), msg("assistant", "Something new.")], "", False),
        ("assistant-empty", [fcall(), fout(), msg("assistant", "")], "", False),
        ("non-message-after", [fcall(), fout(), fcall("call_3")], "", False),
        ("result-not-text", [fcall(), fout(output={"a": "é", "b": [1, "two"]}), msg("assistant", "two")], "", False),
        ("result-missing", [fcall(), {"type": "function_call_output", "call_id": "call_1"}], "", False),
        ("content-text-only", [fcall(), fout(), {"type": "message", "role": "user", "content": "plain"}], "", False),
        ("content-odd-parts", [fcall(), fout(), {"type": "message", "role": "user", "content": [{"text": 5}, "x", {"y": 1}]}],
         "", False),
        ("system-role-after", [fcall(), fout(), msg("system", "x")], "", False),
    ]
    out = []
    for name, tail, spoken, dropped in cases:
        entry = {"name": name, "tail": tail, "spoken": spoken, "dropped_draft_ok": dropped}
        try:
            call, output, rest = _chatgpt._call_tail(tail, "call_1", spoken, dropped_draft_ok=dropped)
            entry["call_tail"] = {"call": call, "output": output, "rest": rest}
        except _chatgpt._KeptThreadMismatchError as exc:
            entry["call_tail"] = {"error": [type(exc).__name__, exc.reason, str(exc)]}
        try:
            result, texts = _chatgpt._in_place_answer(tail, "call_1", spoken, dropped)
            entry["in_place"] = {"result": result, "texts": texts}
        except _chatgpt._KeptThreadMismatchError as exc:
            entry["in_place"] = {"error": [type(exc).__name__, exc.reason, str(exc)]}
        out.append(entry)
    return out


def credits() -> list[dict]:
    out = []
    for exchange in (None, "ex-1"):
        received = []

        def good(report, received=received):
            received.append({"model": report.model, "reported": report.usage_reported,
                             **usage_view(report.usage, report.cache_usage)})

        def bad(report):
            raise RuntimeError("listener broke")

        pending = _chatgpt._PendingCall(request_id=3, call_id="call_1", turn_id="turn_1", exchange_id=exchange,
                                        model="gpt-6-sol", listeners=[good, bad, good])
        usage = TokenUsage(100, 10, 110, 4)
        cache = CacheUsage(cache_read_tokens=80, cache_write_tokens=None, cache_miss_tokens=20, total_prompt_tokens=100)
        figures = pending.credit(usage, cache)
        out.append({"exchange_id": exchange, "figures": figures, "received": received, "owed": pending.usage_owed})
    return out


def failures() -> list[dict]:
    errors = [
        ProviderConnectionError("c"), StaleConnectionError("s"), ModelNotFoundError("m"), AuthenticationError("a"),
        ProviderError("p"), RateLimitError("r"), TokenLimitError("t"), ContextCompactionRequiredError("cc"),
        ModelCapacityError("mc"), InvalidResponseError("i"), IncompleteProviderStreamError("is"),
        ProviderTimeoutError("pt"), _chatgpt._KeptThreadMismatchError("x", "y"), CodexConnectionError("codex"),
        ValueError("v"), asyncio.CancelledError(),
    ]
    return [{"class": type(error).__name__, "failed": _chatgpt._continuation_failed(error)} for error in errors]


class StandInConnection:
    """What `_KeptThread` needs of a `CodexConnection` that never started."""

    def __init__(self, closed: list[str], name: str):
        self.closed, self.name = closed, name

    def call(self, method, params, *, timeout=30):
        raise CodexConnectionError("The ChatGPT connection ended before replying.")

    def next_event(self, timeout=0.25):
        raise CodexConnectionError("The ChatGPT runtime stopped before completing its response.")

    def close(self):
        self.closed.append(self.name)


THREAD = {"home": "/h1", "model": "gpt-6-sol", "instructions": "GM.", "tools": [{"name": "roll"}],
          "items": [{"a": 1}], "generation": 0, "pending": False}


def thread(name, **changes):
    return {"op": "keep", "name": name, **{**THREAD, **changes}}


def take(**changes):
    request = {key: THREAD[key] for key in ("home", "model", "instructions", "tools")}
    return {"op": "take", **request, "items": [{"a": 1}, {"b": 2}], **changes}


def pool_scripts() -> list[dict]:
    return [
        {"name": "keep-and-take", "ops": [thread("t1"), {"op": "len"}, take(), {"op": "len"}]},
        {"name": "longest-wins", "ops": [thread("short"), thread("long", items=[{"a": 1}, {"b": 2}]),
                                         thread("same-long", items=[{"a": 1}, {"b": 2}]),
                                         take(items=[{"a": 1}, {"b": 2}, {"c": 3}]), {"op": "len"}]},
        {"name": "must-extend", "ops": [thread("t1"), take(items=[{"a": 1}]), {"op": "len"}]},
        {"name": "earlier-forms-close", "ops": [thread("t1"), thread("other-session", items=[{"z": 0}]),
                                                take(items=[{"a": 1}], instructions="Changed."), {"op": "len"}]},
        {"name": "other-model-stays", "ops": [thread("t1", model="gpt-6-luna"), take(items=[{"a": 1}]), {"op": "len"}]},
        {"name": "other-home-closes", "ops": [thread("t1", home="/h2"), take(), {"op": "len"}]},
        {"name": "tools-differ", "ops": [thread("t1"), take(tools=[{"name": "look"}]), {"op": "len"}]},
        {"name": "limit", "limit": 2, "ops": [thread("a", items=[{"a": 1}]), thread("b", items=[{"b": 1}]),
                                              thread("c", items=[{"c": 1}]), {"op": "len"}]},
        {"name": "limit-zero", "limit": 0, "ops": [thread("a"), {"op": "len"}]},
        {"name": "generation", "ops": [{"op": "generation", "home": "/h1"}, thread("t1"),
                                       {"op": "release", "home": "/h1"}, {"op": "generation", "home": "/h1"},
                                       thread("stale"), thread("fresh", generation=1), {"op": "len"},
                                       {"op": "release"}, {"op": "generation", "home": "/h1"},
                                       {"op": "generation", "home": "/h2"}]},
        {"name": "release-by-model", "ops": [thread("a", items=[{"a": 1}]), thread("b", model="gpt-6-luna", items=[{"b": 1}]),
                                             {"op": "release", "model": "gpt-6-luna"}, {"op": "len"},
                                             {"op": "models"}]},
        {"name": "release-by-home-and-model", "ops": [thread("a", items=[{"a": 1}]), thread("b", home="/h2", items=[{"b": 1}]),
                                                      {"op": "release", "home": "/h2", "model": "gpt-6-sol"},
                                                      {"op": "len"}, {"op": "generation", "home": "/h1"}]},
        {"name": "ordinals", "ops": [{"op": "next_ordinal", "home": "/h1", "take": False},
                                     {"op": "next_ordinal", "home": "/h1", "take": True},
                                     {"op": "next_ordinal", "home": "/h1", "take": True},
                                     {"op": "next_ordinal", "home": "/h2", "take": False},
                                     {"op": "next_ordinal", "home": "/h1", "take": False}]},
        {"name": "abandoned", "ops": [{"op": "abandon", "home": "/h1", "ordinal": 3, "figures": {"input": 5}},
                                      {"op": "abandon", "home": "/h2", "ordinal": 1, "figures": {"input": 6}},
                                      {"op": "abandon", "home": "/h1", "ordinal": 1, "figures": {"input": 7}},
                                      {"op": "take_abandoned", "home": "/h1", "ordinal": 4},
                                      {"op": "take_abandoned", "home": "/h1", "ordinal": 9},
                                      {"op": "take_abandoned", "home": "/h2", "ordinal": 2}]},
        {"name": "abandoned-limit", "ops": [*({"op": "abandon", "home": "/h1", "ordinal": n, "figures": {"n": n}}
                                              for n in range(70)),
                                            {"op": "take_abandoned", "home": "/h1", "ordinal": 70}]},
        {"name": "idle-expiry", "idle": 0.2, "pending_idle": 30.0,
         "ops": [thread("idle"), thread("waiting", items=[{"w": 1}], pending=True), {"op": "sleep", "seconds": 0.8},
                 {"op": "len"}, take(items=[{"w": 1}, {"x": 2}])]},
        {"name": "close-all", "ops": [thread("a", items=[{"a": 1}]), thread("b", home="/h2", items=[{"b": 1}]),
                                      {"op": "close_all"}, {"op": "len"}, {"op": "generation", "home": "/h2"}]},
    ]


def run_pool(script: dict) -> dict:
    pool = _chatgpt._KeptThreads(limit=script.get("limit", 4), idle_seconds=script.get("idle", 600.0),
                                 pending_idle_seconds=script.get("pending_idle", 1800.0))
    closed: list[str] = []
    names: dict[int, str] = {}
    results = []
    for op in script["ops"]:
        kind = op["op"]
        if kind == "keep":
            kept = _chatgpt._KeptThread(
                connection=StandInConnection(closed, op["name"]), home=Path(op["home"]), model=op["model"],
                thread_id=f"thread-{op['name']}", instructions=op["instructions"], dynamic_tools=op["tools"],
                items=op["items"], generation=op["generation"],
                pending=_chatgpt._PendingCall(request_id=1, call_id="call_1", turn_id="turn_1") if op["pending"] else None,
            )
            names[id(kept)] = op["name"]
            results.append([names[id(each)] for each in pool.keep(kept)])
        elif kind == "take":
            chosen, close = pool.take(Path(op["home"]), op["model"], op["instructions"], op["tools"], op["items"])
            results.append({"chosen": None if chosen is None else names[id(chosen)],
                            "close": [names[id(each)] for each in close]})
        elif kind == "release":
            released = pool.release(Path(op["home"]) if "home" in op else None, op.get("model"))
            results.append(sorted(names[id(each)] for each in released))
        elif kind == "generation":
            results.append(pool.generation(Path(op["home"])))
        elif kind == "next_ordinal":
            results.append(pool.next_ordinal(Path(op["home"]), take=op["take"]))
        elif kind == "abandon":
            pool.abandon(Path(op["home"]), op["ordinal"], op["figures"])
            results.append(None)
        elif kind == "take_abandoned":
            results.append(pool.take_abandoned(Path(op["home"]), op["ordinal"]))
        elif kind == "len":
            results.append(len(pool))
        elif kind == "models":
            with pool._lock:  # noqa: SLF001
                results.append(sorted({kept.model for kept in pool._idle}))  # noqa: SLF001
        elif kind == "sleep":
            time.sleep(op["seconds"])
            results.append(None)
        elif kind == "close_all":
            pool.close_all()
            results.append(None)
    return {**script, "results": results, "closed": sorted(closed)}


def lifecycles() -> list[dict]:
    cases = [
        {"records": [["process_boot", 1100, 1500, "completed", None], ["initialize", 1500, 1900, "completed", None],
                     ["turn_stream", 1400, 2400, "error", "turn_start"]], "now": 3000, "status": "completed"},
        {"records": [["bogus", 1100, 1200, "completed", None], ["turn_start", 900, 1200, "completed", None],
                     ["turn_start", 1300, 1200, "completed", None], ["turn_start", 1300, 1400, "weird", None],
                     ["shutdown", 1000, 1000, "cancelled", None]], "now": 1000, "status": "error"},
        {"records": [["thread_start", 1000 + n, 1000 + 2 * n, "completed", None] for n in range(30)],
         "now": 5000, "status": "completed"},
        {"records": [["turn_stream", 2000, 2500, "completed", None], ["account_read", 1200, 1300, "completed", None],
                     ["history_injection", 2100, 2200, "completed", None]], "now": 900, "status": "cancelled"},
    ]
    out = []
    for case in cases:
        timing = _lifecycle.LifecycleTiming()
        timing.origin, timing.started_utc = 1000, "2026-11-23T09:30:00+00:00"
        for name, start, end, status, parent in case["records"]:
            timing.record(name, start, end, status, parent=parent)
        _lifecycle.time.perf_counter_ns = lambda now=case["now"]: now
        out.append({**case, "snapshot": timing.snapshot(case["status"])})
        _lifecycle.time.perf_counter_ns = time.perf_counter_ns
    return out


def main() -> int:
    vectors = {
        "python": sys.version.split()[0], "settings": settings(), "usages": usages(), "requests": requests(),
        "tails": tails(), "credits": credits(), "failures": failures(),
        "pools": [run_pool(script) for script in pool_scripts()], "lifecycles": lifecycles(),
    }
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
