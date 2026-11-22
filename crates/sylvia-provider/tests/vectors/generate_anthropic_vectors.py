"""Generate reference vectors for sylvia-provider's Anthropic Messages transport (lane 5).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-provider/tests/vectors/generate_anthropic_vectors.py

The frozen Python `AnthropicProvider` runs unchanged on the locked anthropic SDK. Its client is
built with an httpx mock transport (`anthropic.AsyncAnthropic` is wrapped to pass `http_client`)
that replays the given bytes, through lane 4's harness (`generate_chat_vectors.py`: the hook
recorder and projections). Recorded:

- `requests`: `format_request` (with the provider's cache breakpoints where it adds them) and
  the exact HTTP request the SDK sends, or the error either raises;
- `completions`: `generate` on Messages JSON bodies and error statuses;
- `streams`: `generate_stream` on Messages event streams, in pieces;
- `breakpoints`: `with_cache_breakpoints` on assorted requests.

No real provider, key or network is used. Reference tooling only; retired with the Python
source.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
from pathlib import Path

import anthropic
import httpx

from sylvia_gm.provider import _anthropic
from sylvia_gm.provider._anthropic import AnthropicMessagesTransport, AnthropicProvider, with_cache_breakpoints
from sylvia_gm.provider._base import ModelInfo, RetryConfig
from sylvia_gm.provider._exceptions import ProviderError

HERE = Path(__file__).parent
OUTPUT = HERE / "anthropic_vectors.json"
spec = importlib.util.spec_from_file_location("chat_harness", HERE / "generate_chat_vectors.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)

KEY = harness.KEY
BASE_URL = "http://standin.test/v1"
MESSAGES = [
    {"role": "system", "content": "You are the GM."},
    {"role": "user", "content": "I open the door. Étienne waits \U0001f3b2."},
]
TOOLS = harness.TOOLS + [{"type": "function", "defer_loading": True, "function": {
    "name": "later", "description": "Loaded on demand.", "parameters": {"type": "object"}}}]


class Mocked:
    """Each provider's SDK client sends through the current mock transport."""

    transport: httpx.MockTransport | None = None
    original = anthropic.AsyncAnthropic

    @classmethod
    def client(cls, **kwargs):
        return cls.original(http_client=httpx.AsyncClient(transport=cls.transport), **kwargs)


anthropic.AsyncAnthropic = Mocked.client


def make_provider(responses, *, provider_key="byok", base_url=BASE_URL, retry=None, api_key=KEY,
                  model="claude-sonnet-5-5", headers=None, max_tokens=4096):
    sent: list[dict] = []
    queue = list(responses)

    async def handler(request: httpx.Request) -> httpx.Response:
        sent.append(harness.request_projection(request))
        status, response_headers, pieces = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(status, headers=response_headers, stream=harness.Pieces(pieces))

    Mocked.transport = httpx.MockTransport(handler)
    info = ModelInfo(id=f"test/{model}", name="Model", provider_name="Test Provider", provider_key=provider_key,
                     provider_mode="byok", transport="anthropic_messages")
    provider = AnthropicProvider(base_url=base_url, api_key=api_key, model_info=info,
                                 retry_config=retry or RetryConfig(max_retries=0), max_tokens=max_tokens,
                                 default_headers=headers)
    recorder = harness.Recorder()
    provider.hooks.register(recorder)
    return provider, sent, recorder


def error_record(exc: BaseException) -> dict:
    return {"class": type(exc).__name__, "message": str(exc), "retry_after": getattr(exc, "retry_after", None)}


def chunk_projection(chunk) -> dict:
    return {**harness.chunk_projection(chunk), "anthropic_content": chunk.anthropic_content}


def message(content, stop_reason="end_turn", usage=None, **extra) -> bytes:
    body = {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5",
            "content": content, "stop_reason": stop_reason, "stop_sequence": None,
            "usage": usage if usage is not None else {"input_tokens": 10, "output_tokens": 4}, **extra}
    return json.dumps(body).encode()


def events(*items, crlf=False) -> bytes:
    out = ""
    for item in items:
        name, data = item if isinstance(item, tuple) else (item["type"], item)
        text = data if isinstance(data, str) else json.dumps(data)
        out += f"event: {name}\ndata: {text}\n\n"
    return (out.replace("\n", "\r\n") if crlf else out).encode()


# ── Requests ────────────────────────────────────────────────────────────────────────────

HISTORY = [
    {"role": "system", "content": "Rules first."},
    {"role": "system", "content": "  "},
    {"role": "user", "content": "Hello"},
    {"role": "assistant", "content": "I roll.", "tool_calls": [
        {"id": "toolu_1", "type": "function", "function": {"name": "roll", "arguments": "{\"n\": 2}"}},
        {"id": "toolu_2", "type": "function", "function": {"name": "roll", "arguments": "not json"}},
        {"id": "toolu_3", "type": "function", "function": {"name": "roll", "arguments": {"n": 3}}},
        {"type": "function", "function": {}}]},
    {"role": "tool", "tool_call_id": "toolu_1", "content": {"total": 7}},
    {"role": "tool", "content": "orphan"},
    {"role": "assistant", "anthropic_content": [
        {"type": "thinking", "thinking": "hmm", "signature": "sig"},
        {"type": "text", "text": "Native."}]},
    {"role": "user", "content": [{"type": "text", "text": "blocks"}]},
]
TOOL_CHANGE = {"role": "system", "content": [{"type": "tool_addition", "name": "later"}]}


def request_scenarios() -> list[dict]:
    return [
        {"name": "minimal", "stream": True, "kwargs": {}},
        {"name": "history", "stream": False, "messages": HISTORY, "kwargs": {"max_tokens": 64}},
        {"name": "cache-blocks", "stream": True, "messages": [
            {"role": "system", "content": [{"type": "text", "text": "Stable", "cache_control": {"type": "ephemeral"}}]},
            {"role": "system", "content": "Plain"}, {"role": "system", "content": " "},
            {"role": "user", "content": "x"}], "kwargs": {}},
        {"name": "assorted", "stream": True, "kwargs": {
            "temperature": 0.3, "thinking": {"type": "adaptive"}, "output_config": {"effort": "high"},
            "speed": "fast", "tool_choice": "required", "extra_headers": {"anthropic-beta": "a, b", "X-Trace": "1"},
            "ignored": "dropped"}},
        {"name": "temperature-refused", "stream": False, "model": "claude-opus-5-5", "kwargs": {"temperature": 1.0}},
        {"name": "temperature-other-model", "stream": False, "model": "some-local-model", "kwargs": {"temperature": 0.0}},
        {"name": "one-tool-required", "stream": True, "tools": harness.TOOLS, "kwargs": {"tool_choice": "required"}},
        {"name": "tool-changes", "stream": True, "messages": [
            {"role": "system", "content": "GM"}, {"role": "user", "content": "a"}, TOOL_CHANGE,
            {"role": "assistant", "content": "b"}, {"role": "user", "content": "c"}, TOOL_CHANGE],
         "tools": harness.TOOLS, "kwargs": {}},
        {"name": "beta-present", "stream": True, "kwargs": {"extra_headers": {"anthropic-beta": "inline-tools-2026-09-15"}}},
        {"name": "breakpoints-anthropic", "stream": True, "provider_key": "anthropic", "messages": HISTORY, "kwargs": {}},
        {"name": "breakpoints-tool-change", "stream": False, "provider_key": "anthropic", "messages": [
            {"role": "system", "content": "GM"}, {"role": "user", "content": "a"}, TOOL_CHANGE], "kwargs": {}},
        {"name": "opencode", "stream": True, "base_url": "https://opencode.ai/zen/v1", "kwargs": {}},
        {"name": "no-key", "stream": False, "api_key": "", "kwargs": {}},
        {"name": "no-key-configured-header", "stream": False, "api_key": "", "headers": {"x-api-key": "configured"},
         "kwargs": {}},
        {"name": "configured-headers", "stream": True, "headers": {"X-Team": "gm", "anthropic-version": "2099-01-01"},
         "kwargs": {}},
        {"name": "base-without-v1", "stream": True, "base_url": "http://standin.test/", "kwargs": {}},
        {"name": "bad-role", "stream": True, "messages": [{"role": "narrator", "content": "x"}], "kwargs": {}},
    ]


STREAM_OK = events(
    {"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant", "content": [],
                                          "model": "claude-sonnet-5-5", "usage": {"input_tokens": 1, "output_tokens": 1}}},
    {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
    {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "x"}},
    {"type": "content_block_stop", "index": 0},
    {"type": "message_delta", "delta": {"stop_reason": "end_turn"}, "usage": {"output_tokens": 2}},
    {"type": "message_stop"})


async def run_request(case: dict) -> dict:
    messages = case.get("messages", MESSAGES)
    tools = case.get("tools", TOOLS)
    model = case.get("model", "claude-sonnet-5-5")
    out = {"name": case["name"], "stream": case["stream"], "kwargs": case["kwargs"], "messages": messages,
           "tools": tools, "model": model, "base_url": case.get("base_url", BASE_URL),
           "provider_key": case.get("provider_key", "byok"), "api_key": case.get("api_key", KEY),
           "headers": case.get("headers")}
    try:
        request = AnthropicMessagesTransport().format_request(
            model, messages, tools, case["stream"], max_tokens=case["kwargs"].get("max_tokens", 4096),
            **{key: value for key, value in case["kwargs"].items() if key != "max_tokens"})
        if out["provider_key"] == "anthropic":
            request = with_cache_breakpoints(request)
        out["format_request"] = request
    except Exception as exc:  # noqa: BLE001
        out["format_error"] = error_record(exc)
    body = STREAM_OK if case["stream"] else message([{"type": "text", "text": "x"}])
    provider, sent, recorder = make_provider(
        [(200, {"content-type": "text/event-stream" if case["stream"] else "application/json"}, [body])],
        provider_key=out["provider_key"], base_url=out["base_url"], api_key=out["api_key"], model=model,
        headers=case.get("headers"))
    try:
        if case["stream"]:
            async for _ in provider.generate_stream(messages, tools=tools, **dict(case["kwargs"])):
                pass
        else:
            await provider.generate(messages, tools=tools, **dict(case["kwargs"]))
        out["provider_error"] = None
    except Exception as exc:  # noqa: BLE001
        out["provider_error"] = error_record(exc)
    await provider.close()
    out["requests"] = sent
    out["hooks"] = recorder.events
    return out


# ── Completions ─────────────────────────────────────────────────────────────────────────

def completion_scenarios() -> list[dict]:
    return [
        {"name": "text", "body": message([{"type": "text", "text": "The keeper nods."}])},
        {"name": "two-texts", "body": message([{"type": "text", "text": "A"}, {"type": "text", "text": "B",
                                                                               "citations": None}])},
        {"name": "tool-use", "body": message([
            {"type": "thinking", "thinking": "Plan", "signature": "s1"},
            {"type": "redacted_thinking", "data": "opaque"},
            {"type": "tool_use", "id": "toolu_1", "name": "roll", "input": {"n": 2, "why": "é"}}], stop_reason="tool_use")},
        {"name": "caches", "body": message([{"type": "text", "text": "x"}], usage={
            "input_tokens": 5, "output_tokens": 3, "cache_read_input_tokens": 100, "cache_creation_input_tokens": 20})},
        {"name": "no-usage", "body": json.dumps({"type": "message", "content": [{"type": "text", "text": "x"}],
                                                 "stop_reason": "end_turn"}).encode()},
        {"name": "max-tokens", "body": message([{"type": "text", "text": "cut"}], stop_reason="max_tokens")},
        {"name": "stop-sequence", "body": message([], stop_reason="stop_sequence")},
        {"name": "context-window", "body": message([], stop_reason="model_context_window_exceeded")},
        {"name": "no-stop-reason", "body": message([{"type": "text", "text": "x"}], stop_reason=None)},
        {"name": "refusal", "body": message([], stop_reason="refusal")},
        {"name": "pause-turn", "body": message([], stop_reason="pause_turn")},
        {"name": "odd-stop", "body": message([], stop_reason="sideways")},
        {"name": "text-missing", "body": message([{"type": "text"}])},
        {"name": "usage-missing-output", "body": message([], usage={"input_tokens": 3})},
        {"name": "block-not-object", "body": message(["plain"])},
        {"name": "unknown-block", "body": message([{"type": "brand_new", "x": 1, "id": "b"}])},
        {"name": "http-400-context", "status": 400, "body": b'{"type":"error","error":{"type":"invalid_request_error","message":"prompt is too long: 300000 tokens"}}'},
        {"name": "http-400", "status": 400, "body": b'{"type":"error","error":{"message":"bad thing"}}'},
        {"name": "http-401", "status": 401, "body": b'{"error":{"message":"invalid x-api-key"}}'},
        {"name": "http-404", "status": 404, "body": b"not found"},
        {"name": "http-429-retry", "status": 429, "headers": {"retry-after": "3"}, "body": b"{}", "retries": 1},
        {"name": "http-529", "status": 529, "body": b'{"error":{"message":"overloaded"}}'},
        {"name": "http-500", "status": 500, "body": b""},
        {"name": "http-418", "status": 418, "body": b"teapot"},
    ]


async def run_completion(scenario: dict) -> dict:
    status = scenario.get("status", 200)
    headers = {"content-type": "application/json", **scenario.get("headers", {})}
    retries = scenario.get("retries", 0)
    provider, sent, recorder = make_provider(
        [(status, headers, [scenario["body"]])], retry=RetryConfig(max_retries=retries, base_delay=0.0, max_delay=0.0))
    response, error = None, None
    try:
        result = await provider.generate(MESSAGES, tools=TOOLS, timeout=60.0, max_tokens=256)
        response = {"content": result.content, "reasoning_content": result.reasoning_content,
                    "tool_calls": [[c.id, c.name, c.arguments] for c in result.tool_calls],
                    "finish_reason": result.finish_reason,
                    "usage": [result.usage.prompt_tokens, result.usage.completion_tokens, result.usage.total_tokens,
                              result.usage.reasoning_tokens],
                    "usage_reported": result.usage_reported,
                    "cache": [result.cache_usage.cache_read_tokens, result.cache_usage.cache_write_tokens,
                              result.cache_usage.cache_miss_tokens, result.cache_usage.total_prompt_tokens],
                    "anthropic_content": result.anthropic_content}
    except Exception as exc:  # noqa: BLE001
        error = error_record(exc)
    await provider.close()
    return {"name": scenario["name"], "status": status, "response_headers": headers, "retries": retries,
            "body": harness.b64([scenario["body"]])[0], "response": response, "error": error,
            "hooks": recorder.events, "requests": sent}


# ── Streams ─────────────────────────────────────────────────────────────────────────────

def stream_scenarios() -> list[dict]:
    start = {"type": "message_start", "message": {"id": "m", "type": "message", "role": "assistant", "content": [],
                                                  "model": "claude-sonnet-5-5",
                                                  "usage": {"input_tokens": 12, "output_tokens": 1,
                                                            "cache_read_input_tokens": 40}}}
    text = events(
        start, ("ping", {"type": "ping"}),
        {"type": "content_block_start", "index": 0, "content_block": {"type": "thinking", "thinking": "", "signature": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "thinking_delta", "thinking": "Plan"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "signature_delta", "signature": "sig"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": "The "}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "keeper nods."}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None},
         "usage": {"input_tokens": 12, "output_tokens": 9}},
        {"type": "message_stop"})
    tool = events(
        start,
        {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "toolu_1", "name": "roll", "input": {}}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{\"n\":"}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": " 2}"}},
        {"type": "content_block_stop", "index": 0},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}, "usage": {"output_tokens": 5}},
        {"type": "message_stop"})
    initial_input = events(
        {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "t", "name": "roll",
                                                                       "input": {"z": 1, "a": "é"}}},
        {"type": "message_delta", "delta": {"stop_reason": "tool_use"}},
        {"type": "message_stop"})
    return [
        {"name": "text", "pieces": [text]},
        {"name": "text-small-pieces", "pieces": harness.split_every(text, 13)},
        {"name": "text-crlf", "pieces": [events(
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "Hi"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}, {"type": "message_stop"}, crlf=True)]},
        {"name": "tool", "pieces": harness.split_every(tool, 37)},
        {"name": "tool-initial-input", "pieces": [initial_input]},
        {"name": "bad-tool-json", "pieces": [events(
            {"type": "content_block_start", "index": 0, "content_block": {"type": "tool_use", "id": "t", "name": "r", "input": {}}},
            {"type": "content_block_delta", "index": 0, "delta": {"type": "input_json_delta", "partial_json": "{oops"}},
            {"type": "message_delta", "delta": {"stop_reason": "tool_use"}}, {"type": "message_stop"})]},
        {"name": "max-tokens", "pieces": [events(
            start, {"type": "message_delta", "delta": {"stop_reason": "max_tokens"}}, {"type": "message_stop"})]},
        {"name": "refusal", "pieces": [events(start, {"type": "message_delta", "delta": {"stop_reason": "refusal"}})]},
        {"name": "odd-stop", "pieces": [events({"type": "message_delta", "delta": {"stop_reason": "sideways"}})]},
        {"name": "no-message-stop", "pieces": [events(
            start, {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": "half"}})]},
        {"name": "error-event", "pieces": [events(
            start, ("error", {"type": "error", "error": {"type": "overloaded_error", "message": "Overloaded"}}))]},
        {"name": "error-event-text", "pieces": [events(("error", "plain failure"))]},
        {"name": "delta-for-missing-block", "pieces": [events(
            {"type": "content_block_delta", "index": 3, "delta": {"type": "text_delta", "text": "x"}})]},
        {"name": "unknown-event", "pieces": [events(
            ("mystery", {"type": "mystery"}),
            {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": "ok"}},
            {"type": "message_delta", "delta": {"stop_reason": "end_turn"}}, {"type": "message_stop"})]},
        {"name": "http-401", "status": 401, "pieces": [b'{"error":{"message":"bad key"}}']},
        {"name": "http-529", "status": 529, "pieces": [b'{"error":{"message":"overloaded"}}']},
    ]


async def run_stream(scenario: dict) -> dict:
    status = scenario.get("status", 200)
    headers = {"content-type": "text/event-stream" if status == 200 else "application/json"}
    provider, sent, recorder = make_provider([(status, headers, scenario["pieces"])])
    kwargs = {"max_tokens": 4096}
    chunks, error = [], None
    try:
        async for item in provider.generate_stream(MESSAGES, tools=TOOLS, timeout=30.0, **kwargs):
            chunks.append(chunk_projection(item))
    except Exception as exc:  # noqa: BLE001
        error = error_record(exc)
    await provider.close()
    return {"name": scenario["name"], "status": status, "response_headers": headers,
            "pieces": harness.b64(scenario["pieces"]), "kwargs": kwargs, "chunks": chunks, "error": error,
            "hooks": recorder.events, "requests": sent}


# ── Cache breakpoints ───────────────────────────────────────────────────────────────────

def breakpoint_vectors() -> list[dict]:
    cases = [
        {"model": "m", "messages": [{"role": "user", "content": "x"}], "system": "S",
         "tools": [{"name": "a"}, {"name": "b", "defer_loading": True}]},
        {"model": "m", "messages": [{"role": "user", "content": ""}, {"role": "assistant", "content": [
            {"type": "text", "text": "t"}, {"type": "thinking", "thinking": "x"}]}],
         "system": [{"type": "text", "text": "a"}, "not a block"]},
        {"model": "m", "messages": [{"role": "user", "content": "x"}, TOOL_CHANGE, TOOL_CHANGE],
         "tools": [{"name": "a", "defer_loading": True}]},
        {"model": "m", "messages": [TOOL_CHANGE], "system": ""},
        {"model": "m", "messages": [], "system": [{"type": "text", "text": "a"}]},
        {"model": "m", "messages": [{"role": "user", "content": [{"type": "redacted_thinking", "data": "d"}]}]},
    ]
    return [{"request": case, "marked": with_cache_breakpoints(case)} for case in cases]


async def main_async() -> dict:
    os.environ.pop("SYLVIA_GM_CONTEXT_MODE", None)
    return {
        "python": sys.version.split()[0], "anthropic": anthropic.__version__,
        "tool_changes_beta": _anthropic.TOOL_CHANGES_BETA, "tools": TOOLS, "messages": MESSAGES,
        "requests": [await run_request(case) for case in request_scenarios()],
        "completions": [await run_completion(case) for case in completion_scenarios()],
        "streams": [await run_stream(case) for case in stream_scenarios()],
        "breakpoints": breakpoint_vectors(),
    }


def main() -> int:
    vectors = asyncio.run(main_async())
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
