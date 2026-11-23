"""Generate reference runs of sylvia-provider's ChatGPTProvider (lane 5, PR 6b).

Run with the snapshot's own interpreter, from the repository root (Windows: the stand-in's
interpreter must be an .exe):

    .venv/Scripts/python.exe -I crates/sylvia-provider/tests/vectors/generate_chatgpt_provider_vectors.py

The frozen `provider/_chatgpt.py` runs unchanged on Python's real `CodexConnection` against
`chatgpt_turn_standin.py`, a scripted Codex app-server (`datetime.now` fixed). Each case records
what the provider yielded or raised per request, every audit its hooks received (the lifecycle
receipt's clock readings and start time normalised), the stand-in's log, the model's context
figures and the kept-thread count.

Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import asyncio
import datetime as real_datetime
import json
import os
import shutil
import sys
import tempfile
from pathlib import Path

from sylvia_gm.agent._context_ledger import LEDGER_REQUEST
from sylvia_gm.provider import _chatgpt
from sylvia_gm.provider._base import ModelInfo

HERE = Path(__file__).parent
STANDIN = HERE / "chatgpt_turn_standin.py"
OUTPUT = HERE / "chatgpt_provider_vectors.json"
FIXED_NOW = "2026-11-23T09:30:00.123456+00:00"


class FixedDatetime(real_datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return real_datetime.datetime.fromisoformat(FIXED_NOW)


_chatgpt.datetime = FixedDatetime

TOOLS = [
    {"type": "function", "function": {"name": "roll", "description": "Roll dice.",
                                      "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "look", "description": "Look.", "parameters": {"type": "object"}}},
]
SYSTEM = {"role": "system", "content": "You are the GM."}
USER = {"role": "user", "content": "I open the door."}
CALL = {"role": "assistant", "content": "Rolling.", "tool_calls": [
    {"id": "call_1", "type": "function", "function": {"name": "roll", "arguments": "{\"n\": 2}"}}]}
OTHER_CALL = {**CALL, "content": "Other."}
RESULT = {"role": "tool", "tool_call_id": "call_1", "content": "7"}
USER2 = {"role": "user", "content": "I step inside."}
SIGNED_IN = {"account": {"type": "chatgpt", "planType": "plus"}}

READ = {"method": "account/read", "result": SIGNED_IN}
START = {"method": "thread/start", "result": {"thread": {"id": "th1"}, "model": "gpt-6-sol"}}
INJECT = {"method": "thread/inject_items", "result": {}}
STEER = {"method": "turn/steer", "result": {"turnId": "tu1"}}


def turn(turn_id, after):
    return {"method": "turn/start", "result": {"turn": {"id": turn_id}}, "after": after}


def interrupt(after):
    return {"method": "turn/interrupt", "result": {}, "after": after}


def answered(after):
    return {"response": True, "after": after}


def note(method, turn_id="tu1", **params):
    return {"method": method, "params": {"threadId": "th1", "turnId": turn_id, **params}}


def delta(text, turn_id="tu1"):
    return note("item/agentMessage/delta", turn_id, delta=text)


def usage(turn_id="tu1", prompt=100, cached=80, output=10, window=None):
    figures = {"last": {"inputTokens": prompt, "outputTokens": output, "totalTokens": prompt + output,
                        "cachedInputTokens": cached}, "total": {"inputTokens": 999}}
    if window is not None:
        figures["modelContextWindow"] = window
    return note("thread/tokenUsage/updated", turn_id, tokenUsage=figures)


def completed(turn_id="tu1", status="completed", code=None):
    body = {"id": turn_id, "status": status}
    if code is not None:
        body["error"] = {"codexErrorInfo": code}
    return {"method": "turn/completed", "params": {"threadId": "th1", "turn": body}}


def tool_call(request_id=100, call_id="call_1", tool="roll", namespace="sylvia", turn_id="tu1"):
    return {"id": request_id, "method": "item/tool/call",
            "params": {"threadId": "th1", "turnId": turn_id, "callId": call_id, "tool": tool,
                       "namespace": namespace, "arguments": {"n": 2}}}


OPEN = [READ, START, INJECT]
NARRATION = [delta("It "), delta("creaks."), note("item/reasoning/textDelta", delta="Hm."), usage(window=272000), completed()]
CALLED = [delta("Rolling."), tool_call()]
STEP1 = [SYSTEM, USER]
STEP2 = [SYSTEM, USER, CALL, RESULT, USER2]


def request(messages=STEP1, marked=None, timeout=20, options=None):
    return {"messages": messages, "marked": marked, "timeout": timeout, "options": options or {}}


CASES = [
    {"name": "narrate", "thread": "fresh", "steps": OPEN + [turn("tu1", NARRATION)], "requests": [request()]},
    {"name": "call", "thread": "fresh", "steps": OPEN + [turn("tu1", CALLED), interrupt([usage(), completed(status="interrupted")])],
     "requests": [request()]},
    {"name": "call-without-report", "thread": "fresh", "steps": OPEN + [turn("tu1", CALLED), interrupt([])],
     "requests": [request()]},
    {"name": "effort-and-tier", "thread": "fresh", "steps": OPEN + [turn("tu1", NARRATION)],
     "requests": [request(options={"reasoning_effort": "high", "service_tier": "fast", "max_tokens": 9, "temperature": 0.5})]},
    {"name": "bad-call-id", "thread": "fresh", "steps": OPEN + [turn("tu1", [tool_call(call_id=5)])],
     "requests": [request()]},
    {"name": "rerouted", "thread": "fresh", "steps": OPEN + [turn("tu1", [delta("x"), note("model/rerouted")])],
     "requests": [request()]},
    {"name": "compaction", "thread": "fresh", "steps": OPEN + [turn("tu1", [note("item/started", item={"type": "contextCompaction"})])],
     "requests": [request()]},
    *({"name": f"failed-{code or 'other'}", "thread": "fresh",
       "steps": OPEN + [turn("tu1", [completed(status="failed", code=code)])], "requests": [request()]}
      for code in ("usageLimitExceeded", "serverOverloaded", "contextWindowExceeded", None)),
    {"name": "signed-out", "thread": "fresh", "steps": [{"method": "account/read", "result": {"account": None}}],
     "requests": [request()]},
    {"name": "wrong-model", "thread": "fresh",
     "steps": [READ, {"method": "thread/start", "result": {"thread": {"id": "th1"}, "model": "gpt-6-luna"}}],
     "requests": [request()]},
    {"name": "other-thread-ignored", "thread": "fresh",
     "steps": OPEN + [turn("tu1", [{"method": "item/agentMessage/delta", "params": {"threadId": "th9", "delta": "no"}},
                                   note("item/agentMessage/delta", "tu9", delta="no"), *NARRATION])],
     "requests": [request()]},
    {"name": "answer-in-place", "thread": "answer",
     "steps": OPEN + [turn("tu1", CALLED), READ, STEER,
                      answered([usage(), delta("A seven."), usage(prompt=200, cached=150, output=20), completed()])],
     "requests": [request(STEP1, "ex-1"), request(STEP2, "ex-2")]},
    {"name": "stable-continue", "thread": "stable",
     "steps": OPEN + [turn("tu1", CALLED), interrupt([usage(), completed(status="interrupted")]),
                      READ, INJECT, turn("tu2", [delta("Inside.", "tu2"), usage("tu2", 300, 280, 5), completed("tu2")])],
     "requests": [request(STEP1, "ex-1"), request(STEP2, "ex-2")]},
    {"name": "answer-retried-fresh", "thread": "answer",
     "steps": OPEN + [turn("tu1", CALLED), READ, interrupt([usage()]),
                      READ, START, INJECT, turn("tu2", [delta("Fresh.", "tu2"), completed("tu2")])],
     "requests": [request(STEP1, "ex-1"), request([SYSTEM, USER, OTHER_CALL, RESULT, USER2], "ex-2")]},
    {"name": "unmarked-is-fresh", "thread": "answer", "steps": OPEN + [turn("tu1", CALLED), interrupt([usage(), completed(status="interrupted")])],
     "requests": [request(STEP1, None)]},
    {"name": "extra-call", "thread": "answer",
     "steps": OPEN + [turn("tu1", [delta("Rolling."), tool_call(), tool_call(101, "call_2")])],
     "requests": [request(STEP1, "ex-1")]},
    {"name": "release-collects-usage", "thread": "answer", "release": True,
     "steps": OPEN + [turn("tu1", CALLED), interrupt([usage()])],
     "requests": [request(STEP1, "ex-1")]},
    {"name": "stalled", "thread": "fresh", "steps": OPEN + [turn("tu1", [])], "requests": [request(timeout=1)]},
]

FIGURES = ("context_window", "context_window_source", "runtime_context_window", "runtime_context_source",
           "runtime_context_observed_at", "context_observed_at", "configured_runtime_mismatch")


def normalise(value):
    if isinstance(value, dict):
        out = {}
        for key, item in value.items():
            if key.endswith("_ns") and isinstance(item, int):
                out[key] = 0
            elif key == "started_utc":
                out[key] = "<utc>"
            else:
                out[key] = normalise(item)
        return out
    if isinstance(value, (list, tuple)):
        return [normalise(item) for item in value]
    return value


def chunk_view(chunk) -> dict:
    return {"content": chunk.content, "reasoning_content": chunk.reasoning_content,
            "tool_call_delta": chunk.tool_call_delta, "finish_reason": chunk.finish_reason}


def response_view(response) -> dict:
    return {"content": response.content,
            "tool_calls": [[call.id, call.name, call.arguments] for call in response.tool_calls],
            "usage": [response.usage.prompt_tokens, response.usage.completion_tokens, response.usage.total_tokens],
            "usage_reported": response.usage_reported, "model": response.model, "finish_reason": response.finish_reason,
            "cache": [response.cache_usage.cache_read_tokens, response.cache_usage.cache_write_tokens,
                      response.cache_usage.cache_miss_tokens, response.cache_usage.total_prompt_tokens]}


class Recorder:
    def __init__(self):
        self.events = []

    async def on_request(self, context):
        self.events.append({"request": context.request_json})

    async def on_response(self, context):
        self.events.append({"response": response_view(context.response)})

    async def on_error(self, context):
        self.events.append({"error": True})


async def run_case(case: dict, base: Path) -> dict:
    home = (base / case["name"]).resolve() / "chatgpt-runtime"
    (home / "workspace").mkdir(parents=True)
    shutil.copy(STANDIN, home / "workspace" / "app-server")
    (home / "standin.json").write_text(json.dumps({"steps": case["steps"]}), encoding="utf-8")
    os.environ["SYLVIA_CHATGPT_THREAD"] = case["thread"]
    info = ModelInfo(id="chatgpt_subscription/gpt-6-sol", name="GPT-6 Sol", provider_name="ChatGPT",
                     provider_key="chatgpt_subscription", provider_mode="subscription", transport="codex_app_server",
                     max_output_tokens=None)
    provider = _chatgpt.ChatGPTProvider(home, info, executable=Path(sys.executable))
    recorder = Recorder()
    provider.hooks.register(recorder)
    steps = []
    for spec in case["requests"]:
        LEDGER_REQUEST.set(spec["marked"] if spec["marked"] is not None else False)
        chunks, error = [], None
        try:
            async for chunk in provider.generate_stream(spec["messages"], TOOLS, timeout=spec["timeout"], **spec["options"]):
                chunks.append(chunk_view(chunk))
        except BaseException as exc:  # noqa: BLE001
            error = [type(exc).__name__, str(exc)]
        steps.append({"chunks": chunks, "error": error, "kept": len(_chatgpt._KEPT_THREADS),
                      "figures": {name: getattr(provider.model_info, name) for name in FIGURES}})
    if case.get("release"):
        await provider.release_idle()
    await provider.close()
    _chatgpt._KEPT_THREADS.close_all()
    log_path = home / "standin-log.jsonl"
    log = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()] if log_path.exists() else []
    return {**case, "results": steps, "audits": normalise(recorder.events), "log": log}


async def run_all() -> list[dict]:
    base = Path(tempfile.mkdtemp(prefix="sylvia-chatgpt-provider-"))
    try:
        return [await run_case(case, base) for case in CASES]
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("Generate these vectors on Windows, where the stand-in's interpreter is an .exe.")
    cases = asyncio.run(run_all())
    OUTPUT.write_text(json.dumps({"python": sys.version.split()[0], "now": FIXED_NOW, "cases": cases}, indent=1,
                                 ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
    for case in cases:
        print(case["name"], [(step["error"], len(step["chunks"]), step["kept"]) for step in case["results"]],
              sum(1 for line in case["log"] if "unexpected" in line))
        for audit in case["audits"]:
            request = audit.get("request") or {}
            if request.get("method") in ("sylvia/thread_reuse", "sylvia/thread_reuse_closed"):
                print("   ", request["method"], json.dumps(request["params"])[:260])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
