"""Generate reference vectors for sylvia-provider's Gemini GenerateContent transport (lane 5).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-provider/tests/vectors/generate_gemini_vectors.py

The frozen Python `GeminiProvider` runs unchanged over an `httpx.AsyncClient` whose transport
replays the given bytes (lane 4's harness, `generate_chat_vectors.py`: the hook recorder and
projections). The `uuid4` a call without an id is named from is replaced by a counter
(`UUID(int=n)`), which the Rust test supplies too. Recorded:

- `requests`: `provider_request_representation` and the exact HTTP request, or the error;
- `completions`: `generate` on GenerateContent documents, error statuses and transport failures;
- `streams`: `generate_stream` (and `generate(stream=True)`) on SSE bodies, in pieces.

No real provider, key or network is used. Reference tooling only; retired with the Python
source.
"""

from __future__ import annotations

import asyncio
import importlib.util
import itertools
import json
import sys
import uuid
from pathlib import Path

import httpx

from sylvia_gm.provider import _gemini
from sylvia_gm.provider._base import ModelInfo
from sylvia_gm.provider._gemini import GeminiProvider

HERE = Path(__file__).parent
OUTPUT = HERE / "gemini_vectors.json"
spec = importlib.util.spec_from_file_location("chat_harness", HERE / "generate_chat_vectors.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)

KEY = harness.KEY
BASE_URL = "https://standin.test/v1beta"
MESSAGES = harness.MESSAGES
TOOLS = harness.TOOLS + [{"type": "function", "function": {"name": "look"}}]


class Failing(httpx.AsyncByteStream):
    """Pieces, then a transport failure while the body is read."""

    def __init__(self, pieces: list[bytes], failure: Exception) -> None:
        self.pieces = pieces
        self.failure = failure

    async def __aiter__(self):
        for piece in self.pieces:
            yield piece
        raise self.failure


def failure_of(kind: str) -> Exception:
    return httpx.ReadTimeout("timed out") if kind == "timeout" else httpx.ReadError("reset")


def make_provider(reply: dict, *, base_url=BASE_URL, model="gemini-2.5-flash", headers=None, provider_key="byok"):
    sent: list[dict] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        sent.append(harness.request_projection(request))
        fail = reply.get("fail")
        if fail and fail["at"] == "send":
            raise httpx.ConnectTimeout("timed out") if fail["kind"] == "timeout" else httpx.ConnectError("refused")
        stream = (Failing(reply["pieces"], failure_of(fail["kind"])) if fail
                  else harness.Pieces(reply["pieces"]))
        return httpx.Response(reply.get("status", 200), headers=reply.get("headers", {}), stream=stream)

    info = ModelInfo(id=f"test/{model}", name="Model", provider_name="Test Provider", provider_key=provider_key,
                     provider_mode="byok", transport="gemini_generate_content")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=False)
    provider = GeminiProvider(base_url=base_url, model_info=info, api_key=KEY, http_client=client,
                              default_headers=headers)
    recorder = harness.Recorder()
    provider.hooks.register(recorder)
    return provider, sent, recorder


def error_record(exc: BaseException) -> dict:
    record = {"class": type(exc).__name__, "message": str(exc), "retry_after": getattr(exc, "retry_after", None)}
    continuation = getattr(exc, "continuation", None)
    if continuation is not None:
        record["continuation"] = {"token": continuation.token, "native_parts": continuation.native_parts}
    for name in ("continuation_token", "native_parts"):
        if hasattr(exc, name):
            record[name] = getattr(exc, name)
    return record


def response_record(result) -> dict:
    return {
        "content": result.content, "reasoning_content": result.reasoning_content,
        "tool_calls": [[c.id, c.name, c.arguments] for c in result.tool_calls],
        "finish_reason": result.finish_reason, "model": result.model,
        "usage": [result.usage.prompt_tokens, result.usage.completion_tokens, result.usage.total_tokens,
                  result.usage.reasoning_tokens],
        "usage_reported": result.usage_reported,
        "chat_completion_metadata": result.chat_completion_metadata,
    }


def reset_ids() -> None:
    counter = itertools.count(1)
    _gemini.uuid4 = lambda: uuid.UUID(int=next(counter))


def document(parts=None, finish="STOP", usage=None, candidate_extra=None, **extra) -> dict:
    candidate = {"content": {"role": "model", "parts": parts if parts is not None else [{"text": "The keeper nods."}]}}
    if finish is not None:
        candidate["finishReason"] = finish
    candidate.update(candidate_extra or {})
    body = {"candidates": [candidate], **extra}
    if usage is not False:
        body["usageMetadata"] = usage or {"promptTokenCount": 11, "candidatesTokenCount": 4, "totalTokenCount": 15}
    return body


def body(value) -> bytes:
    return value if isinstance(value, bytes) else json.dumps(value).encode()


# ── Requests ────────────────────────────────────────────────────────────────────────────

HISTORY = [
    {"role": "developer", "content": "Rules first."},
    {"role": "system", "content": None},
    {"role": "user", "content": "Hello"},
    {"role": "user", "content": "Again"},
    {"role": "assistant", "content": "I roll.", "tool_calls": [
        {"id": "c1", "type": "function", "function": {"name": "roll", "arguments": "{\"n\": 2}"}},
        {"id": "c2", "type": "function", "function": {"name": "roll", "arguments": {"n": 3}}},
        {"id": "c3", "type": "function", "function": {"name": "look"}}]},
    {"role": "tool", "tool_call_id": "c1", "content": "{\"total\": 7}"},
    {"role": "tool", "tool_call_id": "c2", "content": "[1, 2]"},
    {"role": "tool", "tool_call_id": "c3", "content": "not json"},
    {"role": "assistant", "content": "ignored", "tool_calls": [
        {"id": "c4", "type": "function", "function": {"name": "roll", "arguments": "{}"}},
        {"id": "c5", "type": "function", "function": {"name": "look", "arguments": "{}"}}],
     "gemini_parts": [{"text": "Native.", "thoughtSignature": "sig"},
                      {"functionCall": {"id": "n4", "name": "roll", "args": {}}},
                      {"functionCall": {"name": "look", "args": {}}}]},
    {"role": "tool", "tool_call_id": "c4", "content": {"total": 1}},
    {"role": "tool", "tool_call_id": "c5", "content": ""},
    {"role": "assistant", "content": ""},
    {"role": "narrator", "content": "An odd role is a user turn."},
]


def request_scenarios() -> list[dict]:
    return [
        {"name": "minimal", "stream": False, "kwargs": {}},
        {"name": "minimal-stream", "stream": True, "kwargs": {}},
        {"name": "history", "stream": False, "messages": HISTORY, "kwargs": {"max_tokens": 64}},
        {"name": "orphan-result", "stream": False, "messages": [
            {"role": "user", "content": "x"}, {"role": "tool", "tool_call_id": "nope", "content": "{}"}], "kwargs": {}},
        {"name": "calls-mismatch", "stream": False, "messages": [
            {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "roll"}}],
             "gemini_parts": [{"functionCall": {"name": "roll"}}, {"functionCall": {"name": "look"}}]}], "kwargs": {}},
        {"name": "bad-arguments", "stream": False, "messages": [
            {"role": "assistant", "tool_calls": [{"id": "c1", "function": {"name": "roll", "arguments": "{oops"}}]}],
         "kwargs": {}},
        {"name": "tool-choice-required", "stream": True, "kwargs": {"tool_choice": "required"}},
        {"name": "tool-choice-none", "stream": False, "kwargs": {"tool_choice": "none"}},
        {"name": "tool-choice-auto", "stream": False, "kwargs": {"tool_choice": "auto"}},
        {"name": "tool-choice-named", "stream": False, "kwargs": {"tool_choice": {"type": "function", "function": {"name": "roll"}}}},
        {"name": "tool-choice-unnamed", "stream": False, "kwargs": {"tool_choice": {"type": "function"}}},
        {"name": "tool-choice-other", "stream": False, "kwargs": {"tool_choice": "sometimes"}},
        {"name": "no-tools", "stream": False, "tools": None, "kwargs": {"tool_choice": "required"}},
        {"name": "generation", "stream": True, "kwargs": {
            "max_tokens": 100, "max_output_tokens": 200, "temperature": 0.25, "top_p": 1, "top_k": 40,
            "stop": ["END"], "_retry_attempt": 2, "_request_context_id": "sylvia-x"}},
        {"name": "generation-none", "stream": False, "kwargs": {"max_tokens": None, "temperature": 0.0}},
        {"name": "opencode", "stream": True, "base_url": "https://opencode.ai/zen/v1/",
         "headers": {"X-Goog-Api-Key": "configured", "X-Team": "gm"}, "kwargs": {}},
        {"name": "configured-headers", "stream": False, "headers": {"X-Team": "gm", "User-Agent": "Custom/1"},
         "kwargs": {"extra_headers": {"Content-Type": "application/json; charset=utf-8", "X-Trace": "1"}}},
        {"name": "configured-key", "stream": False, "headers": {"x-goog-api-key": "configured"}, "kwargs": {}},
        {"name": "model-path", "stream": False, "model": "models/gemini 2.5/pro@x", "kwargs": {}},
        {"name": "base-trailing-slash", "stream": True, "base_url": "https://standin.test/v1beta//", "kwargs": {}},
    ]


async def run_request(case: dict) -> dict:
    reset_ids()
    messages = case.get("messages", MESSAGES)
    tools = case.get("tools", TOOLS)
    out = {key: case.get(key) for key in ("name", "stream", "kwargs", "headers")}
    out.update({"messages": messages, "tools": tools, "model": case.get("model", "gemini-2.5-flash"),
                "base_url": case.get("base_url", BASE_URL)})
    stream_body = b'data: {"candidates": [{"content": {"parts": [{"text": "x"}]}, "finishReason": "STOP"}]}\n\n'
    reply = {"headers": {"content-type": "text/event-stream" if case["stream"] else "application/json"},
             "pieces": [stream_body if case["stream"] else body(document())]}
    provider, sent, recorder = make_provider(reply, base_url=out["base_url"], model=out["model"], headers=case.get("headers"))
    try:
        out["representation"] = provider.provider_request_representation(messages, tools, dict(case["kwargs"]))
    except Exception as exc:  # noqa: BLE001
        out["representation_error"] = error_record(exc)
    try:
        if case["stream"]:
            async for _ in provider.generate_stream(messages, tools, **dict(case["kwargs"])):
                pass
        else:
            await provider.generate(messages, tools, **dict(case["kwargs"]))
        out["provider_error"] = None
    except Exception as exc:  # noqa: BLE001
        out["provider_error"] = error_record(exc)
    await provider.close()
    out["requests"] = sent
    out["hooks"] = recorder.events
    return out


# ── Completions ─────────────────────────────────────────────────────────────────────────

def completion_scenarios() -> list[dict]:
    call = {"functionCall": {"id": "g1", "name": "roll", "args": {"n": 2, "why": "é"}}}
    return [
        {"name": "text", "body": document()},
        {"name": "thoughts", "body": document([{"text": "Plan", "thought": True}, {"text": "A"}, {"text": "B"},
                                               {"thoughtSignature": "s"}])},
        {"name": "call", "body": document([{"text": "Rolling."}, call])},
        {"name": "call-without-id", "body": document([{"functionCall": {"name": "roll"}},
                                                      {"functionCall": {"name": "look", "args": {}}}])},
        {"name": "max-tokens", "body": document(finish="MAX_TOKENS")},
        {"name": "max-tokens-call", "body": document([call], finish="MAX_TOKENS")},
        {"name": "no-finish", "body": document(finish=None)},
        {"name": "empty-finish", "body": document(finish="")},
        {"name": "safety", "body": document(finish="SAFETY")},
        {"name": "continuation", "body": document([{"text": "half"}], finish="CONTINUATION",
                                                  candidate_extra={"continuationToken": "tok-1"})},
        {"name": "continuation-no-token", "body": document(finish="CONTINUATION")},
        {"name": "no-usage", "body": document(usage=False)},
        {"name": "usage-odd", "body": document(usage={"promptTokenCount": "7"})},
        {"name": "usage-not-object", "body": document(usage=[1])},
        {"name": "no-candidates", "body": {"usageMetadata": {}}},
        {"name": "empty-candidates", "body": {"candidates": []}},
        {"name": "document-list", "body": [document()]},
        {"name": "candidate-not-object", "body": {"candidates": ["x"]}},
        {"name": "content-null", "body": {"candidates": [{"content": None, "finishReason": "STOP"}]}},
        {"name": "parts-not-list", "body": document({"text": "x"})},
        {"name": "part-not-object", "body": document(["x"])},
        {"name": "text-not-string", "body": document([{"text": 5}])},
        {"name": "bad-call-name", "body": document([{"functionCall": {"name": 5}}])},
        {"name": "bad-call-args", "body": document([{"functionCall": {"name": "roll", "args": [1]}}])},
        {"name": "call-not-object", "body": document([{"functionCall": "roll"}])},
        {"name": "finish-list", "body": document(finish=["STOP"])},
        {"name": "invalid-json", "body": b"{not json"},
        {"name": "utf16-json", "body": json.dumps(document()).encode("utf-16")},
        {"name": "http-401", "status": 401, "body": b'{"error": {"message": "bad key"}}'},
        {"name": "http-403", "status": 403, "body": b""},
        {"name": "http-429", "status": 429, "headers": {"retry-after": "3"}, "body": b"{}"},
        {"name": "http-429-date", "status": 429, "headers": {"retry-after": "Wed, 21 Oct 2026 07:28:00 GMT"}, "body": b"{}"},
        {"name": "http-429-none", "status": 429, "body": b"{}"},
        {"name": "http-404", "status": 404, "body": b"missing"},
        {"name": "http-500", "status": 500, "body": b"boom"},
        {"name": "http-503", "status": 503, "body": b""},
        {"name": "http-400-context", "status": 400, "body": b'{"error": {"message": "Input token count exceeds the maximum"}}'},
        {"name": "http-413", "status": 413, "body": "Request Too Long é".encode()},
        {"name": "http-400", "status": 400, "body": b'{"error": {"message": "bad schema"}}'},
        {"name": "http-302", "status": 302, "headers": {"location": "https://elsewhere.test/"}, "body": b""},
        {"name": "connect-error", "fail": {"at": "send", "kind": "connection"}, "body": b""},
        {"name": "connect-timeout", "fail": {"at": "send", "kind": "timeout"}, "body": b""},
        {"name": "read-error", "fail": {"at": "body", "kind": "connection"}, "body": b'{"candidates"'},
        {"name": "read-timeout", "fail": {"at": "body", "kind": "timeout"}, "body": b'{"candidates"'},
    ]


async def run_completion(scenario: dict) -> dict:
    reset_ids()
    status = scenario.get("status", 200)
    headers = {"content-type": "application/json", **scenario.get("headers", {})}
    pieces = [body(scenario["body"])]
    provider, sent, recorder = make_provider({"status": status, "headers": headers, "pieces": pieces,
                                              "fail": scenario.get("fail")})
    response, error = None, None
    try:
        response = response_record(await provider.generate(MESSAGES, TOOLS, timeout=60.0, max_tokens=256))
    except Exception as exc:  # noqa: BLE001
        error = error_record(exc)
    await provider.close()
    return {"name": scenario["name"], "status": status, "response_headers": headers, "fail": scenario.get("fail"),
            "pieces": harness.b64(pieces), "response": response, "error": error,
            "hooks": recorder.events, "requests": sent}


# ── Streams ─────────────────────────────────────────────────────────────────────────────

def events(*documents, crlf=False) -> bytes:
    out = ""
    for item in documents:
        out += item if isinstance(item, str) else f"data: {json.dumps(item)}\n\n"
    return (out.replace("\n", "\r\n") if crlf else out).encode()


def stream_scenarios() -> list[dict]:
    part = lambda text, **extra: {"candidates": [{"content": {"parts": [{"text": text, **extra}]}}]}  # noqa: E731
    last = document([{"text": "."}], usage={"promptTokenCount": 20, "candidatesTokenCount": 9})
    call = {"candidates": [{"content": {"parts": [{"functionCall": {"name": "roll", "args": {"n": 2}}}]}}],
            "usageMetadata": {"promptTokenCount": 20}}
    text = events(part("Plan", thought=True), part("The "), part("keeper nods"), last)
    return [
        {"name": "text", "pieces": [text]},
        {"name": "text-small-pieces", "pieces": harness.split_every(text, 7)},
        {"name": "text-crlf", "pieces": [events(part("Hi"), last, crlf=True)]},
        {"name": "via-generate", "generate": True, "pieces": [text]},
        {"name": "calls", "pieces": [events(part("Rolling."), call, document([{"functionCall": {"id": "g2", "name": "look"}}]))]},
        {"name": "calls-via-generate", "generate": True, "pieces": [events(call, document([], finish="STOP"))]},
        {"name": "max-tokens", "pieces": [events(part("cut"), document([], finish="MAX_TOKENS"))]},
        {"name": "max-tokens-after-call", "pieces": [events(call, document([], finish="MAX_TOKENS"))]},
        {"name": "no-finish", "pieces": [events(part("half"))]},
        {"name": "unterminated-data", "pieces": [events(part("a"), last)[:-2] + b"\ndata: {\"candidates\": []}"]},
        {"name": "usage-only-event", "pieces": [events(part("a"), {"usageMetadata": {"promptTokenCount": 3}})]},
        {"name": "continuation", "pieces": [events(part("a"), document([], finish="CONTINUATION",
                                                                       candidate_extra={"continuationToken": "t"}))]},
        {"name": "safety", "pieces": [events(part("a"), document([], finish="SAFETY"))]},
        {"name": "invalid-json", "pieces": [b"data: {oops\n\n"]},
        {"name": "comments-and-fields", "pieces": [
            b": keep-alive\nevent: message\nid: 3\ndata:" + json.dumps(part("x")).encode() + b"\nretry: 5\n\n"
            + events(last)]},
        {"name": "multi-line-data", "pieces": [b'data: {"candidates":\ndata:  [{"content": {"parts": [{"text": "y"}]},'
                                               b' "finishReason": "STOP"}]}\n\n']},
        {"name": "line-separator-in-data", "pieces": [
            ('data: {"candidates": [{"content": {"parts": [{"text": "a b"}]}, "finishReason": "STOP"}]}\n\n').encode()]},
        {"name": "latin-1", "headers": {"content-type": "text/event-stream; charset=latin-1"},
         "pieces": ['data: {"candidates": [{"content": {"parts": [{"text": "café"}]}, "finishReason": "STOP"}]}\n\n'.encode("latin-1")]},
        {"name": "split-utf8", "pieces": harness.split_every(events(part("Étienne 🎲"), last), 3)},
        {"name": "read-error", "fail": {"at": "body", "kind": "connection"}, "pieces": [events(part("a"))]},
        {"name": "read-timeout", "fail": {"at": "body", "kind": "timeout"}, "pieces": [events(part("a"))]},
        {"name": "connect-error", "fail": {"at": "send", "kind": "connection"}, "pieces": []},
        {"name": "http-401", "status": 401, "pieces": [b"{}"]},
        {"name": "http-429", "status": 429, "headers": {"retry-after": "1.5"}, "pieces": [b"{}"]},
        {"name": "http-500", "status": 500, "pieces": [b"{}"]},
        {"name": "http-400-context", "status": 400, "pieces": [b'{"error": "prompt is too long"}']},
    ]


async def run_stream(scenario: dict) -> dict:
    reset_ids()
    status = scenario.get("status", 200)
    headers = scenario.get("headers") or {"content-type": "text/event-stream" if status == 200 else "application/json"}
    provider, sent, recorder = make_provider({"status": status, "headers": headers, "pieces": scenario["pieces"],
                                              "fail": scenario.get("fail")})
    chunks, response, error = [], None, None
    try:
        if scenario.get("generate"):
            response = response_record(await provider.generate(MESSAGES, TOOLS, stream=True, timeout=30.0))
        else:
            async for item in provider.generate_stream(MESSAGES, TOOLS, timeout=30.0):
                chunks.append(harness.chunk_projection(item))
    except Exception as exc:  # noqa: BLE001
        error = error_record(exc)
    await provider.close()
    return {"name": scenario["name"], "status": status, "response_headers": headers, "fail": scenario.get("fail"),
            "generate": bool(scenario.get("generate")), "pieces": harness.b64(scenario["pieces"]),
            "chunks": chunks, "response": response, "error": error, "hooks": recorder.events, "requests": sent}


async def main_async() -> dict:
    return {
        "python": sys.version.split()[0], "httpx": httpx.__version__, "tools": TOOLS, "messages": MESSAGES,
        "requests": [await run_request(case) for case in request_scenarios()],
        "completions": [await run_completion(case) for case in completion_scenarios()],
        "streams": [await run_stream(case) for case in stream_scenarios()],
    }


def main() -> int:
    vectors = asyncio.run(main_async())
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
