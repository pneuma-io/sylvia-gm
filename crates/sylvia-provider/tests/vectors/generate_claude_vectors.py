"""Generate reference vectors for sylvia-provider's Claude Code provider rules (lane 5, PR 4a).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-provider/tests/vectors/generate_claude_vectors.py

The frozen `provider/_claude.py` runs unchanged, with `uuid.uuid4` replaced by a counter
(`UUID(int=n)`) and `datetime.now` by a fixed time, which the Rust test supplies too. Recorded:
`prepare_request` (plain and with the ledger's native route), `tool_search`, `runtime_failure`,
`_check_still_waiting`, `_failure_code`, `_usage`, `_audit_model_usage` (the counts it returns
and the figures its hook records) and the window `_observe_context_window` takes.

Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import asyncio
import datetime as real_datetime
import itertools
import json
import sys
import types
import uuid
from pathlib import PureWindowsPath

from sylvia_gm.provider import _claude
from sylvia_gm.provider._base import ModelInfo, ProviderResponse
from sylvia_gm.provider._claude_kept import ContinuationMismatchError
from sylvia_gm.provider._claude_native import NativeRoute
from sylvia_gm.provider._exceptions import (
    AuthenticationError, IncompleteProviderStreamError, InvalidResponseError, ModelNotFoundError,
    ProviderConnectionError, ProviderError, ProviderTimeoutError, RateLimitError, StaleConnectionError,
    TokenLimitError,
)

HERE = __import__("pathlib").Path(__file__).parent
OUTPUT = HERE / "claude_vectors.json"
FIXED_NOW = "2026-11-23T09:30:00.123456+00:00"
WORKSPACE = PureWindowsPath("C:\\Sylvia\\claude-runtime\\workspace")


class FixedDatetime(real_datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        return real_datetime.datetime.fromisoformat(FIXED_NOW)


_claude.datetime = FixedDatetime


def reset_ids() -> None:
    counter = itertools.count(1)
    _claude.uuid = types.SimpleNamespace(uuid4=lambda: uuid.UUID(int=next(counter)))


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
SEALED = {"role": "user", "content": "[host-seal abc] The goblin moves."}


def plain_cases() -> list[dict]:
    return [
        {"name": "player-tail", "messages": [SYSTEM, USER, REPLY, USER], "tools": TOOLS},
        {"name": "application-tail", "messages": [SYSTEM, {**USER, "_application_data": {"x": 1}}], "tools": TOOLS},
        {"name": "tool-result-tail", "messages": [SYSTEM, USER, CALL, RESULT], "tools": TOOLS},
        {"name": "no-messages", "messages": [], "tools": None},
        {"name": "system-only", "messages": [SYSTEM], "tools": []},
        {"name": "deferred-tools", "messages": [SYSTEM, USER], "tools": DEFERRED},
        {"name": "cache-system-blocks", "messages": [
            {"role": "system", "content": [{"type": "text", "text": "A"}, {"type": "text", "text": "B"}]}, USER], "tools": TOOLS},
        {"name": "system-image", "messages": [{"role": "system", "content": [{"type": "image"}]}, USER], "tools": TOOLS},
        {"name": "system-text-not-string", "messages": [{"role": "system", "content": [{"type": "text", "text": 5}]}, USER],
         "tools": TOOLS},
        {"name": "bad-tool-name", "messages": [USER], "tools": [{"type": "function", "function": {"name": "a b"}}]},
        {"name": "long-tool-name", "messages": [USER], "tools": [{"type": "function", "function": {"name": "a" * 65}}]},
        {"name": "duplicate-tools", "messages": [USER], "tools": TOOLS + [TOOLS[0]]},
        {"name": "native-content", "messages": [SYSTEM, USER, {"role": "assistant", "anthropic_content": [
            {"type": "thinking", "thinking": "x", "signature": "s"},
            {"type": "tool_use", "id": "toolu_9", "name": "look", "input": {}}]}, RESULT | {"tool_call_id": "toolu_9"}],
         "tools": TOOLS},
    ]


def native_cases() -> list[dict]:
    entries = [USER, CALL, RESULT, SEALED, REPLY, USER]
    answer = {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "toolu_1", "content": "7 and sealed"}]}
    record = {"attachment": {"type": "env"}, "rendered": "Env.", "renderedRole": "user"}
    round_message = {"type": "assistant", "message": {"role": "assistant", "content": [{"type": "tool_use", "id": "ts", "name": "ToolSearch"}]}}
    return [
        {"name": "first-step", "messages": [SYSTEM, USER], "route": {"stable_count": 1, "entry_count": 1}},
        {"name": "boundaries", "messages": [SYSTEM, *entries], "route": {
            "stable_count": 1, "entry_count": 6, "boundaries": [1, 3],
            "native_records": {"1": [record], "3": [record, round_message]}}},
        {"name": "filler-after-user", "messages": [SYSTEM, USER, USER], "route": {
            "stable_count": 1, "entry_count": 2, "boundaries": [1]}},
        {"name": "kept-answer", "messages": [SYSTEM, *entries], "route": {
            "stable_count": 1, "entry_count": 6, "boundaries": [1, 4],
            "native_answers": {"4": answer}, "native_records": {"4": [record]}}},
        {"name": "answer-not-matching", "messages": [SYSTEM, *entries], "route": {
            "stable_count": 1, "entry_count": 6, "boundaries": [1, 4],
            "native_answers": {"4": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": "other"}]}}}},
        {"name": "empty-middle-group", "messages": [SYSTEM, USER, CALL, RESULT, USER], "route": {
            "stable_count": 1, "entry_count": 4, "boundaries": [2]}},
        {"name": "no-prompt", "messages": [SYSTEM, USER, CALL, RESULT], "route": {"stable_count": 1, "entry_count": 3}},
        {"name": "wrong-stable-count", "messages": [SYSTEM, USER], "route": {"stable_count": 0, "entry_count": 1}},
        {"name": "wrong-entry-count", "messages": [SYSTEM, USER], "route": {"stable_count": 1, "entry_count": 2}},
        {"name": "later-system", "messages": [SYSTEM, USER, SYSTEM], "route": {"stable_count": 1, "entry_count": 2}},
        {"name": "boundary-out-of-range", "messages": [SYSTEM, USER, REPLY, USER], "route": {
            "stable_count": 1, "entry_count": 3, "boundaries": [3]}},
        {"name": "boundaries-unsorted", "messages": [SYSTEM, USER, REPLY, USER, REPLY, USER], "route": {
            "stable_count": 1, "entry_count": 5, "boundaries": [3, 1]}},
        {"name": "user-content-not-text", "messages": [SYSTEM, {"role": "user", "content": 5}], "route": {
            "stable_count": 1, "entry_count": 1}},
    ]


def route_of(spec: dict) -> NativeRoute:
    return NativeRoute(
        stable_count=spec["stable_count"], entry_count=spec["entry_count"],
        boundaries=tuple(spec.get("boundaries", ())),
        native_records={int(k): v for k, v in spec.get("native_records", {}).items()},
        native_answers={int(k): v for k, v in spec.get("native_answers", {}).items()},
    )


def run_prepare(case: dict, native: bool) -> dict:
    reset_ids()
    try:
        system, records, prompt, tools = _claude.prepare_request(
            case["messages"], case.get("tools"), "claude-opus-5-5", WORKSPACE,
            route_of(case["route"]) if native else None)
        return {**case, "system": system, "records": records, "prompt": prompt, "native_tools": tools}
    except InvalidResponseError as exc:
        return {**case, "error": ["InvalidResponseError", str(exc)]}
    except Exception as exc:  # noqa: BLE001
        return {**case, "error": [type(exc).__name__, str(exc)]}


def failures() -> list[dict]:
    events = [
        {"api_error_status": 401}, {"api_error_status": 403.0}, {"result": "Not logged in"},
        {"errors": ["OAuth token expired"]}, {"api_error_status": 429}, {"error": "Usage limit reached"},
        {"result": "prompt is too long: 300000 tokens"}, {"api_error_status": 404},
        {"result": "Invalid model name"}, {"subtype": "error_during_execution"}, {"result": None},
        {"result": "You do not have access to this model"}, {"api_error_status": True}, {},
    ]
    out = []
    for event in events:
        error = _claude.runtime_failure(event)
        out.append({"event": event, "class": type(error).__name__, "message": str(error)})
    return out


def waiting() -> list[dict]:
    cases = [
        [], [{"type": "stream_event", "event": {"type": "content_block_delta"}}, {"type": "assistant"}],
        [{"type": "result"}], [{"type": "control_request"}], [None], ["error"],
        [{"type": "stream_event", "event": {"type": "message_start"}}],
        [{"type": "stream_event", "event": None}], [{"type": "stream_event", "event": "x"}],
        [{"type": "stream_event"}, None],
    ]
    out = []
    for events in cases:
        drained = [InvalidResponseError("x") if event == "error" else event for event in events]
        kept = types.SimpleNamespace(connection=types.SimpleNamespace(drain=lambda drained=drained: drained))
        try:
            _claude._check_still_waiting(kept)
            out.append({"events": events, "ok": True})
        except ContinuationMismatchError as exc:
            out.append({"events": events, "mismatch": exc.reason})
        except Exception as exc:  # noqa: BLE001
            out.append({"events": events, "raw": [type(exc).__name__, str(exc)]})
    return out


def codes() -> list[dict]:
    errors = [
        ("cancelled", asyncio.CancelledError()), ("ProviderTimeoutError", ProviderTimeoutError("t")),
        ("StaleConnectionError", StaleConnectionError("s")), ("IncompleteProviderStreamError", IncompleteProviderStreamError("i")),
        ("ProviderConnectionError", ProviderConnectionError("c")),
        ("InvalidResponseError", InvalidResponseError("Claude returned invalid text.")),
        ("InvalidResponseError", InvalidResponseError("Claude did not expose exactly this request's Sylvia tools.")),
        ("InvalidResponseError", InvalidResponseError("Something else.")),
        ("RateLimitError", RateLimitError("r")), ("AuthenticationError", AuthenticationError("a")),
        ("ModelNotFoundError", ModelNotFoundError("m")), ("TokenLimitError", TokenLimitError.provider_rejection()),
        ("ProviderError", ProviderError("p")), ("Exception", ValueError("v")),
    ]
    return [{"class": name, "message": str(error), "code": _claude._failure_code(error)} for name, error in errors]


def usages() -> list[dict]:
    cases = [
        {"input_tokens": 10, "output_tokens": 4},
        {"input_tokens": 10, "output_tokens": 4, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20},
        {"input_tokens": 10, "output_tokens": 4, "cache_read_input_tokens": -1, "cache_creation_input_tokens": True},
        {"input_tokens": True, "output_tokens": 4}, {"input_tokens": -1, "output_tokens": 4},
        {"input_tokens": 1.0, "output_tokens": 4}, {"output_tokens": 4}, [1], None,
    ]
    out = []
    for usage in cases:
        response = ProviderResponse()
        _claude.ClaudeProvider._usage(response, usage)
        out.append({"usage": usage, "reported": bool(response.usage_reported),
                    "tokens": [response.usage.prompt_tokens, response.usage.completion_tokens, response.usage.total_tokens],
                    "cache": [response.cache_usage.cache_read_tokens, response.cache_usage.cache_write_tokens,
                              response.cache_usage.cache_miss_tokens, response.cache_usage.total_prompt_tokens]})
    return out


class Recorder:
    def __init__(self):
        self.events = []

    async def on_request(self, context):
        self.events.append(context.request_json)

    async def on_response(self, context):
        pass

    async def on_error(self, context):
        pass


def provider() -> tuple[_claude.ClaudeProvider, Recorder]:
    info = ModelInfo(id="claude_runtime/test-model", name="M", provider_name="Claude", provider_key="claude_runtime",
                     provider_mode="subscription", transport="claude_code")
    instance = _claude.ClaudeProvider(__import__("pathlib").Path("unused-home"), info)
    recorder = Recorder()
    instance.hooks.register(recorder)
    return instance, recorder


def model_usages() -> list[dict]:
    rows = {"claude-opus-5-5": {"inputTokens": 10, "outputTokens": 5, "cacheReadInputTokens": 100,
                                "cacheCreationInputTokens": 2, "contextWindow": 1000000, "maxOutputTokens": 64000,
                                "costUSD": 0.12, "webSearchRequests": 0},
            "claude-haiku-5-5": {"inputTokens": 3, "outputTokens": True, "contextWindow": 200000.0}, "odd": 5}
    since = {"claude-opus-5-5": {"inputTokens": 4, "outputTokens": 1, "cacheReadInputTokens": 50}}
    out = []
    for usage, earlier in ((rows, None), (rows, since), ([1], None), ({}, None)):
        instance, recorder = provider()
        counts = asyncio.run(instance._audit_model_usage(usage, "claude-opus-5-5", since=earlier))
        out.append({"usage": usage, "since": earlier, "counts": counts,
                    "recorded": recorder.events[0]["params"]["model_usage"] if recorder.events else None,
                    "audit": recorder.events[0] if recorder.events else None})
    return out


def windows() -> list[dict]:
    cases = [
        ({"claude-opus-5-5": {"contextWindow": 1000000}}, "claude-opus-5-5"),
        ({"other": {"contextWindow": 200000}}, "claude-opus-5-5"),
        ({"a": {"contextWindow": 200000}, "b": {"contextWindow": 1}}, "claude-opus-5-5"),
        ({"claude-opus-5-5": {}, "test-model": {"contextWindow": 300000}}, "claude-opus-5-5"),
        ({"claude-opus-5-5": {}, "test-model": {}}, "claude-opus-5-5"),
        ({"test-model": {"contextWindow": 300000}}, None),
        ({"x": {"contextWindow": 511}}, None), ({"x": {"contextWindow": 10_000_001}}, None),
        ({"x": {"contextWindow": 5e5}}, None), ({"x": {"contextWindow": True}}, None), ([1], None),
    ]
    out = []
    for usage, resolved in cases:
        instance, _ = provider()
        instance._observe_context_window(usage, resolved)
        out.append({"usage": usage, "resolved": resolved, "window": instance.model_info.runtime_context_window})
    return out


def main() -> int:
    vectors = {
        "python": sys.version.split()[0], "now": FIXED_NOW, "workspace": str(WORKSPACE),
        "plain": [run_prepare(case, False) for case in plain_cases()],
        "native": [run_prepare(case, True) for case in native_cases()],
        "tool_search": [{"tools": tools, "search": _claude.tool_search(tools)} for tools in (
            [], [{"name": "a"}], [{"name": "a", "_meta": {_claude.ALWAYS_LOAD: True}}],
            [{"name": "a", "_meta": {_claude.ALWAYS_LOAD: False}}], [{"name": "a", "_meta": "x"}],
            [{"name": "a", "_meta": {_claude.ALWAYS_LOAD: 0}}])],
        "failures": failures(), "waiting": waiting(), "codes": codes(), "usages": usages(),
        "model_usages": model_usages(), "windows": windows(),
    }
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
