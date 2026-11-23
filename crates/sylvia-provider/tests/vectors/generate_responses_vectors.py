"""Generate reference vectors for sylvia-provider's Responses transport (lane 5).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-provider/tests/vectors/generate_responses_vectors.py

The frozen Python source runs unchanged, through lane 4's harness (`generate_chat_vectors.py`:
an httpx mock transport replaying the given bytes, the hook recorder and the projections), with
the model's transport set to `responses`:

- `requests`: `ResponsesTransport.format_request` for assorted histories and keywords, and the
  exact HTTP request the OpenAI SDK sends for it, or the error either raises;
- `completions`: `OpenAICompatProvider.generate` on Responses JSON bodies and error statuses;
- `streams`: `generate_stream` on Responses event streams, in pieces;
- `native_output`: `native_output` on output items the SDK constructed, the retained-item dump.

No real provider, key or network is used. Reference tooling only; retired with the Python
source.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import httpx
import openai
from openai._models import construct_type
from openai.types.responses.response import Response

from sylvia_gm.provider._base import ModelInfo, RetryConfig
from sylvia_gm.provider._exceptions import ProviderError
from sylvia_gm.provider._native_output import native_output
from sylvia_gm.provider._openai_compat import OpenAICompatProvider, ResponsesTransport

HERE = Path(__file__).parent
OUTPUT = HERE / "responses_vectors.json"
spec = importlib.util.spec_from_file_location("chat_harness", HERE / "generate_chat_vectors.py")
harness = importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)

BASE_URL = harness.BASE_URL
MESSAGES = harness.MESSAGES
TOOLS = harness.TOOLS + [
    {"type": "function", "function": {"name": "strict_roll", "description": 7, "strict": True,
                                      "parameters": {"type": "object", "properties": {}}}},
    {"type": "function", "function": {"name": "no_parameters"}},
    {"type": "custom", "custom": {"name": "free"}},
    "not a tool",
]


def make_provider(responses, *, provider_key="byok", base_url=BASE_URL, retry=None):
    sent: list[dict] = []
    queue = list(responses)

    async def handler(request: httpx.Request) -> httpx.Response:
        sent.append(harness.request_projection(request))
        status, response_headers, pieces = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(status, headers=response_headers, stream=harness.Pieces(pieces))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    info = ModelInfo(id="test/model-a", name="Model A", provider_name="Test Provider",
                     provider_key=provider_key, provider_mode="byok", transport="responses")
    provider = OpenAICompatProvider(base_url=base_url, api_key=harness.KEY, model_info=info,
                                    retry_config=retry or RetryConfig(max_retries=0), http_client=client)
    recorder = harness.Recorder()
    provider.hooks.register(recorder)
    return provider, sent, recorder


def error_record(exc: Exception) -> dict:
    return {"class": type(exc).__name__, "message": str(exc), "retry_after": getattr(exc, "retry_after", None)}


# ── Requests ────────────────────────────────────────────────────────────────────────────

HISTORY = [
    {"role": "system", "content": "You are the GM."},
    {"role": "developer", "content": 42},
    {"role": "user", "content": "I open the door. Étienne waits \U0001f3b2."},
    {"role": "assistant", "content": "It creaks.", "tool_calls": [
        {"id": "call_1", "type": "function", "function": {"name": "roll", "arguments": "{\"n\":2}"}},
        {"id": "call_2", "type": "function", "function": {"name": "roll", "arguments": {"n": 3}}},
        {"id": "call_3", "type": "function", "function": {"name": "roll"}}]},
    {"role": "tool", "tool_call_id": "call_1", "content": "7"},
    {"role": "tool", "tool_call_id": "call_2", "content": {"total": 9}},
    {"role": "tool", "tool_call_id": "call_3"},
    {"role": "assistant", "content": "", "responses_output": [
        {"type": "reasoning", "id": "rs_1", "summary": [], "encrypted_content": "opaque=="},
        {"type": "message", "id": "msg_1", "role": "assistant", "status": "completed",
         "content": [{"type": "output_text", "text": "Earlier", "annotations": []}]}]},
    {"role": "user", "content": None},
]


def request_scenarios() -> list[dict]:
    return [
        {"name": "minimal", "stream": True, "kwargs": {}},
        {"name": "history", "stream": False, "messages": HISTORY, "kwargs": {"max_tokens": 64}},
        {"name": "assorted", "stream": True, "kwargs": {
            "temperature": 0.7, "tool_choice": {"type": "function", "function": {"name": "roll"}},
            "reasoning": {"effort": "high", "summary": "auto"}, "service_tier": "priority",
            "parallel_tool_calls": False, "stream_options": {"include_usage": True}, "user": "é x"}},
        {"name": "tool-choice-text", "stream": True, "kwargs": {"tool_choice": "required"}},
        {"name": "json-schema", "stream": False, "kwargs": {"response_format": {
            "type": "json_schema", "json_schema": {"name": "turn", "schema": {"type": "object"}, "strict": True}}}},
        {"name": "json-object-then-text", "stream": False, "kwargs": {
            "response_format": {"type": "json_object"}, "text": {"verbosity": "low"}}},
        {"name": "text-then-format", "stream": False, "kwargs": {
            "text": {"verbosity": "high"}, "response_format": {"type": "text"}}},
        {"name": "extra-body", "stream": True, "kwargs": {
            "max_tokens": 5, "extra_body": {"max_output_tokens": 9, "top_k": 4}}},
        {"name": "openai-store", "stream": True, "base_url": "https://api.openai.com/v1",
         "kwargs": {"include": ["message.output_text.logprobs"]}},
        {"name": "xai-store-kept", "stream": False, "base_url": "https://api.x.ai/v1",
         "kwargs": {"store": True, "include": ["reasoning.encrypted_content"]}},
        {"name": "openrouter-session", "stream": True, "base_url": "https://openrouter.ai/api/v1",
         "kwargs": {"_request_context_id": "sylvia-turn-1"}},
        {"name": "unsupported-format", "stream": False, "kwargs": {"response_format": {"type": "grammar"}}},
        {"name": "unknown-keyword", "stream": True, "kwargs": {"seed": 3}},
        {"name": "bad-role", "stream": True, "messages": [{"role": "narrator", "content": "x"}], "kwargs": {}},
        {"name": "tool-result-no-id", "stream": True, "messages": [{"role": "tool", "content": "x"}], "kwargs": {}},
        {"name": "tool-call-not-object", "stream": True,
         "messages": [{"role": "assistant", "tool_calls": ["x"]}], "kwargs": {}},
        {"name": "tool-call-no-function", "stream": True,
         "messages": [{"role": "assistant", "tool_calls": [{"id": "a"}]}], "kwargs": {}},
        {"name": "tool-call-empty-id", "stream": True,
         "messages": [{"role": "assistant", "tool_calls": [{"id": "", "function": {"name": "roll"}}]}],
         "kwargs": {}},
    ]


TEXT_STREAM_BODY = None


def stream_events(*events, done=False) -> bytes:
    out = b""
    for event in events:
        out += f"event: {event['type']}\ndata: {json.dumps(event)}\n\n".encode()
    if done:
        out += b"data: [DONE]\n\n"
    return out


def response_body(output=None, status="completed", usage=None, **extra) -> dict:
    body = {"id": "resp_1", "object": "response", "created_at": 1, "model": "model-a", "status": status,
            "output": output if output is not None else [], **extra}
    if usage is not None:
        body["usage"] = usage
    return body


MESSAGE_ITEM = {"type": "message", "id": "msg_1", "status": "completed", "role": "assistant",
                "content": [{"type": "output_text", "text": "The keeper nods.", "annotations": [], "logprobs": None}]}
REASONING_ITEM = {"type": "reasoning", "id": "rs_1", "summary": [{"type": "summary_text", "text": "Plan"}],
                  "encrypted_content": "gAAAA==", "status": None}
CALL_ITEM = {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "roll",
             "arguments": "{\"n\":2}", "status": "completed"}
USAGE = {"input_tokens": 10, "output_tokens": 4, "total_tokens": 14,
         "input_tokens_details": {"cached_tokens": 6}, "output_tokens_details": {"reasoning_tokens": 2}}


async def run_request(case: dict) -> dict:
    messages = case.get("messages", MESSAGES)
    out = {"name": case["name"], "stream": case["stream"], "kwargs": case["kwargs"], "messages": messages,
           "base_url": case.get("base_url", BASE_URL)}
    try:
        out["format_request"] = ResponsesTransport().format_request(
            "model-a", messages, TOOLS, case["stream"], **{k: v for k, v in case["kwargs"].items()
                                                            if k != "_request_context_id"})
    except Exception as exc:  # noqa: BLE001  (recorded as Python raised it)
        out["format_error"] = {"class": type(exc).__name__, "message": str(exc)}
    body = (stream_events({"type": "response.completed", "response": response_body([MESSAGE_ITEM])})
            if case["stream"] else json.dumps(response_body([MESSAGE_ITEM])).encode())
    provider, sent, recorder = make_provider(
        [(200, {"content-type": "text/event-stream" if case["stream"] else "application/json"}, [body])],
        base_url=case.get("base_url", BASE_URL))
    try:
        if case["stream"]:
            async for _ in provider.generate_stream(messages, tools=TOOLS, **dict(case["kwargs"])):
                pass
        else:
            await provider.generate(messages, tools=TOOLS, **dict(case["kwargs"]))
        out["provider_error"] = None
    except Exception as exc:  # noqa: BLE001
        out["provider_error"] = {"class": type(exc).__name__, "message": str(exc)}
    await provider.close()
    out["requests"] = sent
    out["hooks"] = recorder.events
    return out


# ── Completions ─────────────────────────────────────────────────────────────────────────

def completion_scenarios() -> list[dict]:
    def body(**kwargs) -> bytes:
        return json.dumps(response_body(**kwargs)).encode()

    return [
        {"name": "text", "body": body(output=[MESSAGE_ITEM], usage=USAGE)},
        {"name": "reasoning-and-tools", "body": body(output=[REASONING_ITEM, CALL_ITEM, {
            "type": "function_call", "id": "fc_2", "call_id": None, "name": "roll", "arguments": ""}], usage={
            "input_tokens": 3, "output_tokens": 1})},
        {"name": "two-text-parts", "body": body(output=[{**MESSAGE_ITEM, "content": [
            {"type": "output_text", "text": "A", "annotations": []}, {"type": "refusal", "refusal": "no"},
            {"type": "output_text", "text": "B", "annotations": []}]}])},
        {"name": "effort-and-tier", "body": body(output=[MESSAGE_ITEM], reasoning={"effort": "high"},
                                                 service_tier="priority")},
        {"name": "effort-invalid", "body": body(output=[MESSAGE_ITEM], reasoning={"effort": "Very High"},
                                                service_tier="x" * 40)},
        {"name": "incomplete-length", "body": body(output=[MESSAGE_ITEM], status="incomplete",
                                                   incomplete_details={"reason": "max_output_tokens"})},
        {"name": "incomplete-filter", "body": body(output=[MESSAGE_ITEM], status="incomplete",
                                                   incomplete_details={"reason": "content_filter"})},
        {"name": "failed", "body": body(output=[], status="failed")},
        {"name": "no-status", "body": json.dumps({"id": "r", "output": [MESSAGE_ITEM]}).encode()},
        {"name": "unknown-item-reordered", "body": body(output=[
            {"status": "completed", "type": "web_search_call", "id": "ws_1", "action": {"type": "search"}},
            {"zeta": 1, "type": "brand_new_item", "id": "x1", "role": "tool", "alpha": None}])},
        {"name": "item-without-type", "body": body(output=[{"id": "x"}])},
        {"name": "item-not-object", "body": body(output=["text"])},
        {"name": "usage-malformed", "body": body(output=[MESSAGE_ITEM], usage={
            "input_tokens": "12", "output_tokens": 3.9, "output_tokens_details": None})},
        {"name": "text-not-string", "body": body(output=[{**MESSAGE_ITEM, "content": [
            {"type": "output_text", "text": 5, "annotations": []}]}])},
        {"name": "not-an-object", "body": b"\"just text\""},
        {"name": "call-missing-fields", "body": body(output=[{"type": "function_call"}])},
        {"name": "no-output", "body": json.dumps({"id": "r", "status": "completed"}).encode()},
        {"name": "part-missing-text", "body": body(output=[{"type": "message", "content": [{"type": "output_text"}]}])},
        {"name": "usage-without-total", "body": body(output=[MESSAGE_ITEM], usage={"input_tokens": 2, "output_tokens": 3})},
        {"name": "http-400-context", "status": 400, "body": b'{"error":{"message":"context length exceeded"}}'},
        {"name": "http-429-retry", "status": 429, "headers": {"retry-after": "2"}, "body": b"{}", "retries": 2},
        {"name": "http-500", "status": 500, "body": b"oops"},
    ]


async def run_completion(scenario: dict) -> dict:
    status = scenario.get("status", 200)
    headers = {"content-type": "application/json", **scenario.get("headers", {})}
    retries = scenario.get("retries", 0)
    provider, sent, recorder = make_provider(
        [(status, headers, [scenario["body"]])],
        retry=RetryConfig(max_retries=retries, base_delay=0.0, max_delay=0.0))
    response, error = None, None
    try:
        result = await provider.generate(MESSAGES, tools=TOOLS, timeout=60.0, max_tokens=256)
        response = {"content": result.content, "reasoning_content": result.reasoning_content,
                    "tool_calls": [[c.id, c.name, c.arguments] for c in result.tool_calls],
                    "finish_reason": result.finish_reason,
                    "usage": [result.usage.prompt_tokens, result.usage.completion_tokens,
                              result.usage.total_tokens, result.usage.reasoning_tokens],
                    "usage_reported": result.usage_reported,
                    "chat_completion_metadata": result.chat_completion_metadata,
                    "reported_effort": result.provider_reported_reasoning_effort,
                    "reported_tier": result.provider_reported_service_tier}
    except ProviderError as exc:
        error = error_record(exc)
    await provider.close()
    return {"name": scenario["name"], "status": status, "response_headers": headers, "retries": retries,
            "body": harness.b64([scenario["body"]])[0], "response": response, "error": error,
            "hooks": recorder.events, "requests": sent}


# ── Streams ─────────────────────────────────────────────────────────────────────────────

def stream_scenarios() -> list[dict]:
    completed = {"type": "response.completed", "response": response_body(
        [REASONING_ITEM, MESSAGE_ITEM], usage=USAGE, reasoning={"effort": "medium"}, service_tier="default")}
    text = stream_events(
        {"type": "response.created", "response": response_body(status="in_progress")},
        {"type": "response.reasoning_summary_text.delta", "delta": "Plan", "output_index": 0},
        {"type": "response.output_text.delta", "delta": "The keeper ", "output_index": 1},
        {"type": "response.output_text.delta", "delta": "nods.", "output_index": 1},
        completed)
    tool = stream_events(
        {"type": "response.output_item.added", "output_index": 0,
         "item": {"type": "function_call", "id": "fc_1", "call_id": "call_1", "name": "roll", "arguments": ""}},
        {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": "{\"n\":"},
        {"type": "response.function_call_arguments.delta", "output_index": 0, "delta": " 2}"},
        {"type": "response.output_item.added", "output_index": 1, "item": {"type": "message", "id": "m"}},
        {"type": "response.completed", "response": response_body([CALL_ITEM])})
    inline = stream_events(
        {"type": "response.output_text.delta", "delta": "Shown <think>hidden"},
        {"type": "response.output_text.delta", "delta": "</think> after"},
        {"type": "response.completed", "response": response_body([MESSAGE_ITEM])})
    return [
        {"name": "text", "pieces": [text]},
        {"name": "text-small-pieces", "pieces": harness.split_every(text, 11)},
        {"name": "tool", "pieces": harness.split_every(tool, 40)},
        {"name": "inline-think", "pieces": [inline]},
        {"name": "with-done", "pieces": [stream_events(
            {"type": "response.output_text.delta", "delta": "x"},
            {"type": "response.completed", "response": response_body([])}, done=True)]},
        {"name": "incomplete-length", "pieces": [stream_events(
            {"type": "response.output_text.delta", "delta": "cut"},
            {"type": "response.incomplete", "response": response_body(
                [MESSAGE_ITEM], status="incomplete", incomplete_details={"reason": "max_output_tokens"},
                usage={"input_tokens": 2, "output_tokens": 9})})]},
        {"name": "failed", "pieces": [stream_events(
            {"type": "response.failed", "response": response_body([], status="failed")})]},
        {"name": "error-event", "pieces": [stream_events(
            {"type": "response.output_text.delta", "delta": "x"},
            {"type": "error", "code": "server_error", "message": "boom", "sequence_number": 2})]},
        {"name": "error-field", "pieces": [stream_events(
            {"type": "response.output_text.delta", "delta": "x"},
            {"type": "response.failed", "error": {"message": "quota"}})]},
        {"name": "no-terminal", "pieces": [stream_events(
            {"type": "response.output_text.delta", "delta": "half"})]},
        {"name": "http-401", "status": 401, "pieces": [b'{"error":{"message":"bad key"}}']},
    ]


async def run_stream(scenario: dict) -> dict:
    status = scenario.get("status", 200)
    headers = {"content-type": "text/event-stream" if status == 200 else "application/json"}
    provider, sent, recorder = make_provider([(status, headers, scenario["pieces"])])
    kwargs = {"max_tokens": 4096, "tool_choice": "auto"}
    chunks, error = [], None
    try:
        async for item in provider.generate_stream(MESSAGES, tools=TOOLS, timeout=30.0, **kwargs):
            chunks.append(harness.chunk_projection(item))
    except ProviderError as exc:
        error = error_record(exc)
    await provider.close()
    return {"name": scenario["name"], "status": status, "response_headers": headers,
            "pieces": harness.b64(scenario["pieces"]), "kwargs": kwargs, "chunks": chunks, "error": error,
            "hooks": recorder.events, "requests": sent}


# ── Retained output items ───────────────────────────────────────────────────────────────

def native_output_vectors() -> list[dict]:
    cases = [
        [MESSAGE_ITEM, REASONING_ITEM, CALL_ITEM],
        [{"type": "message", "role": "assistant", "id": "m", "status": "completed", "phase": None,
          "content": [{"text": "no type", "annotations": [{"type": "url_citation", "url": "u", "title": "t",
                                                            "start_index": 1, "end_index": 2, "z": None}]},
                      {"type": "refusal", "refusal": "no", "extra": {"a": None, "b": [None, 1]}}]}],
        [{"type": "reasoning", "id": "r", "summary": [], "content": [{"type": "reasoning_text", "text": "t"}]}],
        [{"type": "function_call_output", "call_id": "c", "output": [{"type": "input_text", "text": "x"}],
          "id": "o", "status": None}],
        [{"type": "function_call_output", "call_id": "c", "output": "plain"}],
        [{"type": "message", "content": [{"type": "output_text", "text": "x", "annotations": [],
                                          "logprobs": [{"token": "x", "logprob": -1, "bytes": [120],
                                                        "top_logprobs": [{"token": "y", "logprob": 0,
                                                                          "bytes": []}]}]}]}],
        [{"type": "custom_unknown", "id": "u", "nested": {"k": None, "v": 1.5}}],
    ]
    out = []
    for items in cases:
        constructed = construct_type(type_=Response, value={"output": items}).output
        try:
            out.append({"items": items, "retained": native_output(constructed)})
        except Exception as exc:  # noqa: BLE001
            out.append({"items": items, "error": {"class": type(exc).__name__, "message": str(exc)}})
    return out


async def main_async() -> dict:
    return {
        "python": sys.version.split()[0], "openai": openai.__version__,
        "tools": TOOLS,
        "requests": [await run_request(case) for case in request_scenarios()],
        "completions": [await run_completion(case) for case in completion_scenarios()],
        "streams": [await run_stream(case) for case in stream_scenarios()],
        "native_output": native_output_vectors(),
    }


def main() -> int:
    vectors = asyncio.run(main_async())
    OUTPUT.write_text(json.dumps(vectors, indent=1, ensure_ascii=True) + "\n", encoding="utf-8", newline="\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
