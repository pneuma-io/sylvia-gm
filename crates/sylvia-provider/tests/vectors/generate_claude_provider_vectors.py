"""Generate reference runs of sylvia-provider's ClaudeProvider (lane 5, PR 4b).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-provider/tests/vectors/generate_claude_provider_vectors.py

The frozen `provider/_claude.py` runs unchanged against `claude_standin.py`, a scripted Claude
Code started with this interpreter (`find_runtime` and `runtime_version` are replaced so the
stand-in needs no install; `uuid.uuid4` is a counter and `datetime.now` fixed, which the Rust
test supplies too). Each case records what the provider yielded or raised, every audit its
hooks received, the stand-in's log (arguments, transcript, system text, every stdin line), what
the ledger's route collected, the model's context figures and the files left in the home.

Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import asyncio
import datetime as real_datetime
import itertools
import json
import os
import shutil
import sys
import tempfile
import types
import uuid
from pathlib import Path

from sylvia_gm.provider import _claude, _claude_kept
from sylvia_gm.provider._base import ModelInfo
from sylvia_gm.provider._claude_native import CURRENT_NATIVE_ROUTE, NativeRoute
from sylvia_gm.provider._claude_runtime import ClaudeConnection

HERE = Path(__file__).parent
STANDIN = HERE / "claude_standin.py"
OUTPUT = HERE / "claude_provider_vectors.json"
FIXED_NOW = "2026-11-23T09:30:00.123456+00:00"


class FixedDatetime(real_datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return real_datetime.datetime.fromisoformat(FIXED_NOW)


_claude.datetime = FixedDatetime
_claude.find_runtime = lambda home: Path(sys.executable)
_claude.runtime_version = lambda executable, home: "2.1.322"


def reset_ids() -> None:
    counter = itertools.count(1)
    _claude.uuid = types.SimpleNamespace(uuid4=lambda: uuid.UUID(int=next(counter)))


def factory(executable, workspace, arguments, environment):
    return ClaudeConnection(executable, workspace, ["-I", str(STANDIN), *arguments], environment)


# ── Scripts ──────────────────────────────────────────────────────────────────────────────

M = "claude-opus-5-5"
TOOLS = [
    {"type": "function", "function": {"name": "roll", "description": "Roll dice.",
                                      "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}}}},
    {"type": "function", "function": {"name": "look", "description": "Look.", "parameters": {"type": "object"}}},
]
DEFERRED = TOOLS + [{"type": "function", "defer_loading": True,
                     "function": {"name": "later", "description": "Later.", "parameters": {"type": "object"}}}]
SYSTEM = {"role": "system", "content": "You are the GM."}
USER = {"role": "user", "content": "I open the door."}
CALL = {"role": "assistant", "content": "Rolling.", "tool_calls": [
    {"id": "toolu_1", "type": "function", "function": {"name": "roll", "arguments": "{\"n\": 2}"}}]}
RESULT = {"role": "tool", "tool_call_id": "toolu_1", "content": "7"}
REPLY = {"role": "assistant", "content": "It creaks."}
USER2 = {"role": "user", "content": "I step inside."}
#: The ledger's sealed continue record after a tool-result tail (`_context_ledger.CONTINUE_RECORD_TEXT`).
CONTINUE = {"role": "user", "content": "[host-seal 0123abcd] "
            + __import__("sylvia_gm.agent._context_ledger", fromlist=["x"]).CONTINUE_RECORD_TEXT}
ATTACHMENT = {"type": "attachment", "attachment": {"kind": "env"}, "rendered": "Env: x", "renderedRole": "user"}
API = {"type": "api-request"}


def control(request_id, message, subtype="mcp_message", server="sylvia"):
    return {"emit": {"type": "control_request", "request_id": request_id,
                     "request": {"subtype": subtype, "server_name": server, "message": message}}}


def stream(event):
    return {"emit": {"type": "stream_event", "event": event}}


def handshake(names, model=M, written=2):
    """The CLI's MCP handshake and init after Sylvia's initialize request and prompt."""
    return [
        {"await": written},
        control("r1", {"jsonrpc": "2.0", "id": 1, "method": "initialize"}),
        {"await": written + 1},
        control("r2", {"jsonrpc": "2.0", "method": "notifications/initialized"}),
        {"await": written + 2},
        control("r3", {"jsonrpc": "2.0", "id": 2, "method": "tools/list"}),
        {"await": written + 3},
        {"emit": {"type": "system", "subtype": "init", "model": model, "tools": names}},
    ]


NAMES = ["mcp__sylvia__roll", "mcp__sylvia__look"]


def narration(text, message_id="msg_1", model=M, usage=None):
    return [
        stream({"type": "message_start", "message": {"id": message_id, "model": model,
                                                      "usage": usage or {"input_tokens": 10, "cache_read_input_tokens": 100}}}),
        stream({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        stream({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text[:3]}}),
        stream({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text[3:]}}),
        stream({"type": "content_block_stop", "index": 0}),
        stream({"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 4}}),
        stream({"type": "message_stop"}),
        {"emit": {"type": "assistant", "message": {"id": message_id, "content": [{"type": "text", "text": text}]}}},
    ]


def result(text=None, window=200000, model=M, **extra):
    event = {"type": "result", "subtype": "success", "is_error": False,
             "usage": {"input_tokens": 10, "output_tokens": 4, "cache_read_input_tokens": 100},
             "modelUsage": {model: {"inputTokens": 10, "outputTokens": 4, "cacheReadInputTokens": 100,
                                    "contextWindow": window, "costUSD": 0.5}}}
    if text is not None:
        event["result"] = text
    event.update(extra)
    return {"emit": event}


def call_blocks(text="Rolling.", arguments='{"n": 2}', stop=True):
    events = [
        stream({"type": "message_start", "message": {"id": "msg_c", "model": M, "usage": {"input_tokens": 12}}}),
        stream({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        stream({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": text}}),
        stream({"type": "content_block_stop", "index": 0}),
        stream({"type": "content_block_start", "index": 1,
                "content_block": {"type": "tool_use", "id": "toolu_1", "name": "mcp__sylvia__roll", "input": {}}}),
        stream({"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": arguments}}),
        stream({"type": "content_block_stop", "index": 1}),
        stream({"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 6}}),
    ]
    return events + ([stream({"type": "message_stop"})] if stop else [])


def tool_call(name="roll", arguments=None):
    return control("r9", {"jsonrpc": "2.0", "id": 7, "method": "tools/call",
                          "params": {"name": name, "arguments": {"n": 2} if arguments is None else arguments}})


def route(stable, entries, boundaries=(), records=None, answers=None):
    return {"stable_count": stable, "entry_count": entries, "boundaries": list(boundaries),
            "native_records": records or {}, "native_answers": answers or {}}


def step(messages, tools=TOOLS, native=None, pause=0.0):
    return {"messages": messages, "tools": tools, "route": native, "pause": pause}


ANSWER_ROW = {"type": "user", "message": {"role": "user", "content": [
    {"type": "tool_result", "tool_use_id": "toolu_1", "content": "7"}]}}

CASES = [
    {"name": "narrate", "runs": [handshake(NAMES) + narration("It creaks.") + [result()]],
     "steps": [step([SYSTEM, USER])]},
    {"name": "narrate-after-history", "runs": [handshake(NAMES) + narration("Inside.") + [result()]],
     "steps": [step([SYSTEM, USER, CALL, RESULT, REPLY, USER2])]},
    {"name": "call", "runs": [handshake(NAMES) + call_blocks() + [tool_call()]], "steps": [step([SYSTEM, USER])]},
    {"name": "call-before-stop", "runs": [handshake(NAMES) + call_blocks(stop=False) + [tool_call(), stream({"type": "message_stop"})]],
     "steps": [step([SYSTEM, USER])]},
    {"name": "call-without-arguments", "runs": [handshake(NAMES) + call_blocks(arguments="") + [tool_call(arguments={})]],
     "steps": [step([SYSTEM, USER])]},
    {"name": "uncorrelated", "runs": [handshake(NAMES) + call_blocks() + [tool_call("look")]], "steps": [step([SYSTEM, USER])]},
    {"name": "not-exposed", "runs": [handshake(NAMES[:1])], "steps": [step([SYSTEM, USER])]},
    {"name": "wrong-model", "runs": [handshake(NAMES, model="claude-sonnet-5-5")], "steps": [step([SYSTEM, USER])]},
    {"name": "rate-limited", "runs": [handshake(NAMES) + [{"emit": {"type": "result", "subtype": "error_during_execution",
                                                                    "is_error": True, "api_error_status": 429}}]],
     "steps": [step([SYSTEM, USER])]},
    {"name": "window-then-too-long", "runs": [
        handshake(NAMES) + narration("It creaks.") + [result(window=500000)],
        handshake(NAMES) + [{"emit": {"type": "result", "subtype": "success", "is_error": True,
                                      "result": "Prompt is too long: 600000 tokens"}}]],
     "steps": [step([SYSTEM, USER]), step([SYSTEM, USER])]},
    {"name": "malformed", "runs": [handshake(NAMES) + [stream({"type": "content_block_start", "content_block": {"type": "text"}})]],
     "steps": [step([SYSTEM, USER])]},
    {"name": "exits-early", "runs": [handshake(NAMES) + [{"exit": 0}]], "steps": [step([SYSTEM, USER])]},
    {"name": "unknown-control", "runs": [handshake(NAMES) + [
        control("p1", {"x": 1}, subtype="can_use_tool", server=None), {"await": 6}] + narration("It creaks.") + [result("ignored")]],
     "steps": [step([SYSTEM, USER])]},
    {"name": "result-text-only", "runs": [handshake(NAMES) + [result("Plain reply.", window=1)]], "steps": [step([SYSTEM, USER])]},
    {"name": "tool-search", "runs": [handshake(NAMES + ["mcp__sylvia__later", "ToolSearch"]) + [
        stream({"type": "message_start", "message": {"id": "msg_a", "model": M, "usage": {"input_tokens": 20}}}),
        stream({"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}}),
        stream({"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Looking."}}),
        stream({"type": "content_block_stop", "index": 0}),
        stream({"type": "content_block_start", "index": 1,
                "content_block": {"type": "tool_use", "id": "ts_1", "name": "ToolSearch", "input": {}}}),
        stream({"type": "content_block_delta", "index": 1, "delta": {"type": "input_json_delta", "partial_json": '{"query": "later"}'}}),
        stream({"type": "content_block_stop", "index": 1}),
        stream({"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 3, "x": True}}),
        stream({"type": "message_stop"}),
        {"emit": {"type": "user", "message": {"role": "user", "content": [
            {"type": "tool_result", "tool_use_id": "ts_1", "content": [
                {"type": "tool_reference", "tool_name": "mcp__sylvia__later"}, {"type": "text", "text": "x"}]}]}}},
    ] + narration(" Found it.", message_id="msg_b") + [result()]],
     "steps": [step([SYSTEM, USER], tools=DEFERRED)]},
    {"name": "route-first-step", "runs": [handshake(NAMES) + narration("It creaks.") + [
        {"session": ["$prompt", ATTACHMENT, API]}, result()]],
     "steps": [step([SYSTEM, USER], native=route(1, 1))]},
    {"name": "route-call", "runs": [handshake(NAMES) + call_blocks() + [{"session": ["$prompt", ATTACHMENT, API]}, tool_call()]],
     "steps": [step([SYSTEM, USER], native=route(1, 1))]},
    {"name": "kept-answer", "persistent": True, "runs": [
        handshake(NAMES) + call_blocks() + [{"session": ["$prompt", ATTACHMENT, API]}, tool_call(), {"await": 6}]
        + narration("A seven.", message_id="msg_2") + [{"session": [ANSWER_ROW, ATTACHMENT, API]}, result()]],
     "steps": [step([SYSTEM, USER], native=route(1, 1)),
               step([SYSTEM, USER, CALL, RESULT, CONTINUE], native=route(1, 4, boundaries=[1]))]},
    {"name": "kept-prompt", "persistent": True, "runs": [
        handshake(NAMES) + narration("It creaks.") + [{"session": ["$prompt", ATTACHMENT, API]}, result(), {"await": 6}]
        + narration("Inside.", message_id="msg_2") + [{"session": ["$prompt", API]}, result()]],
     "steps": [step([SYSTEM, USER], native=route(1, 1)),
               step([SYSTEM, USER, REPLY, USER2], native=route(1, 3, boundaries=[1]))]},
    # The kept process exits after its reply; the next request finds it gone (after a pause, so
    # its exit has been read) and starts fresh.
    {"name": "kept-gone", "persistent": True, "runs": [
        handshake(NAMES) + narration("It creaks.") + [{"session": ["$prompt", API]}, result(), {"exit": 0}],
        handshake(NAMES) + narration("Inside.", message_id="msg_2") + [{"session": ["$prompt", API]}, result()]],
     "steps": [step([SYSTEM, USER], native=route(1, 1)),
               step([SYSTEM, USER, REPLY, USER2], native=route(1, 3, boundaries=[1]), pause=1.0)]},
    # It takes the next prompt and exits before answering: served once on a fresh process.
    {"name": "kept-dies-on-prompt", "persistent": True, "runs": [
        handshake(NAMES) + narration("It creaks.") + [{"session": ["$prompt", API]}, result(), {"await": 6}, {"exit": 0}],
        handshake(NAMES) + narration("Inside.", message_id="msg_2") + [{"session": ["$prompt", API]}, result()]],
     "steps": [step([SYSTEM, USER], native=route(1, 1)),
               step([SYSTEM, USER, REPLY, USER2], native=route(1, 3, boundaries=[1]))]},
]


# ── Running ──────────────────────────────────────────────────────────────────────────────

def normalise(value, home: Path):
    plain = str(home)
    escaped = json.dumps(plain)[1:-1]
    if isinstance(value, str):
        return value.replace(escaped, "<home>").replace(plain, "<home>")
    if isinstance(value, list):
        return [normalise(item, home) for item in value]
    if isinstance(value, dict):
        return {key: normalise(item, home) for key, item in value.items()}
    return value


def chunk_view(chunk) -> dict:
    return {"content": chunk.content, "reasoning_content": chunk.reasoning_content,
            "tool_call_delta": chunk.tool_call_delta, "finish_reason": chunk.finish_reason,
            "anthropic_content": chunk.anthropic_content}


def response_view(response) -> dict:
    return {"content": response.content,
            "tool_calls": [[call.id, call.name, call.arguments] for call in response.tool_calls],
            "usage": [response.usage.prompt_tokens, response.usage.completion_tokens, response.usage.total_tokens],
            "usage_reported": response.usage_reported, "model": response.model,
            "finish_reason": response.finish_reason,
            "cache": [response.cache_usage.cache_read_tokens, response.cache_usage.cache_write_tokens,
                      response.cache_usage.cache_miss_tokens, response.cache_usage.total_prompt_tokens],
            "anthropic_content": response.anthropic_content}


class Recorder:
    def __init__(self):
        self.events = []

    async def on_request(self, context):
        self.events.append({"request": context.request_json})

    async def on_response(self, context):
        self.events.append({"response": response_view(context.response)})

    async def on_error(self, context):
        self.events.append({"error": True})


FIGURES = ("context_window", "context_window_source", "runtime_context_window", "runtime_context_source",
           "runtime_context_observed_at", "context_observed_at", "configured_runtime_mismatch")


async def run_case(case: dict, base: Path) -> dict:
    reset_ids()
    home = (base / case["name"]).resolve() / "claude-runtime"
    home.mkdir(parents=True)
    (home / "standin.json").write_text(json.dumps({"runs": case["runs"]}), encoding="utf-8")
    os.environ["SYLVIA_CLAUDE_PROCESS"] = "persistent" if case.get("persistent") else "per-step"
    info = ModelInfo(id=f"claude_runtime/{M}", name="Opus", provider_name="Claude", provider_key="claude_runtime",
                     provider_mode="subscription", transport="claude_code", max_output_tokens=None)
    provider = _claude.ClaudeProvider(home, info, connection_factory=factory)
    recorder = Recorder()
    provider.hooks.register(recorder)
    steps = []
    for spec in case["steps"]:
        collected = []

        async def on_collected(found, collected=collected):
            collected.append({"status": found.status, "records": list(found.records), "reason": found.reason,
                              "answer": found.answer})

        native = None
        if spec["route"] is not None:
            r = spec["route"]
            native = NativeRoute(stable_count=r["stable_count"], entry_count=r["entry_count"],
                                 boundaries=tuple(r["boundaries"]),
                                 native_records={int(k): v for k, v in r["native_records"].items()},
                                 native_answers={int(k): v for k, v in r["native_answers"].items()},
                                 on_collected=on_collected)
        await asyncio.sleep(spec["pause"])
        CURRENT_NATIVE_ROUTE.set(native)
        chunks, error = [], None
        try:
            async for chunk in provider.generate_stream(spec["messages"], spec["tools"], timeout=20):
                chunks.append(chunk_view(chunk))
        except Exception as exc:  # noqa: BLE001
            error = [type(exc).__name__, str(exc)]
        steps.append({"chunks": chunks, "error": error, "collected": collected,
                      "kept": len(_claude_kept.KEPT_PROCESSES),
                      "figures": {name: getattr(provider.model_info, name) for name in FIGURES}})
    await provider.close()
    log_path = home / "standin-log.jsonl"
    log = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()] if log_path.exists() else []
    files = sorted(
        path.relative_to(home).as_posix() for path in home.rglob("*")
        if path.is_file() and path.name not in {"standin.json", "standin-count", "standin-log.jsonl"}
    )
    return normalise({"name": case["name"], "persistent": bool(case.get("persistent")), "runs": case["runs"],
                      "steps_in": case["steps"], "steps": steps, "audits": recorder.events, "log": log,
                      "files": files}, home)


async def run_all() -> list[dict]:
    base = Path(tempfile.mkdtemp(prefix="sylvia-claude-provider-"))
    try:
        return [await run_case(case, base) for case in CASES]
    finally:
        shutil.rmtree(base, ignore_errors=True)


def main() -> int:
    if sys.platform != "win32":
        raise SystemExit("Generate these vectors on Windows; the Rust test adapts the transcript's line endings.")
    cases = asyncio.run(run_all())
    vectors = {"python": sys.version.split()[0], "now": FIXED_NOW, "cases": cases}
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
