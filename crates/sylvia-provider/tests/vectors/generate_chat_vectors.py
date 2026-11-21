"""Generate reference vectors for sylvia-provider's Chat Completions transport.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-provider/tests/vectors/generate_chat_vectors.py

Every vector comes from running the frozen Python source unchanged:

- `streams`: the real `OpenAICompatProvider.generate_stream` against an httpx mock transport
  that replays the given response bytes in the given pieces. Recorded: every chunk the provider
  yields, the error it ends with, the hooks it fires, and the exact HTTP request it sent.
- `completions`: `generate` (non-streaming) the same way, with JSON bodies and error statuses.
- `requests`: `format_request` and the bytes the OpenAI SDK puts on the wire for assorted
  keyword arguments, including the ruleless-turn session's own request.
- `sdk_models`: the declared fields of the SDK models the transport reads, so the Rust side
  can check its copies.
- `inline_filter`, `metadata`: the reasoning-markup filter and continuation-metadata
  collector fed directly.

No real provider, key or network is used. Reference tooling only; retired with the Python
source.
"""

from __future__ import annotations

import asyncio
import base64
import json
import sys
from pathlib import Path

import httpx
from openai.types import completion_usage
from openai.types.chat import ChatCompletionMessage, chat_completion, chat_completion_chunk
from openai.types.chat.chat_completion_message_custom_tool_call import (
    ChatCompletionMessageCustomToolCall,
)
from openai.types.chat.chat_completion_message_function_tool_call import (
    ChatCompletionMessageFunctionToolCall,
    Function,
)
from openai._compat import get_model_fields

from sylvia_gm.provider._base import ModelInfo, RetryConfig
from sylvia_gm.provider._chat_metadata import ChatCompletionMetadata
from sylvia_gm.provider._exceptions import ProviderError
from sylvia_gm.provider._openai_compat import (
    ChatCompletionsTransport,
    OpenAICompatProvider,
    _InlineReasoningFilter,
)
from sylvia_gm.provider._tool_delta import _delta_value, parse_tool_call_delta

HERE = Path(__file__).parent
OUTPUT = HERE / "chat_vectors.json"
RECORDING = HERE.parents[2] / "sylvia-compare" / "recordings" / "windows" / "ruleless-turn.recording.json"
BASE_URL = "http://standin.test/v1"
KEY = "test-key-not-a-secret"

MESSAGES = [
    {"role": "system", "content": "You are the GM."},
    {"role": "user", "content": "I open the door. Étienne waits \U0001f3b2."},
]
TOOLS = [{"type": "function", "function": {
    "name": "roll", "description": "Roll dice.",
    "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}, "required": ["n"]},
}}]


class Pieces(httpx.AsyncByteStream):
    def __init__(self, pieces: list[bytes]) -> None:
        self.pieces = pieces

    async def __aiter__(self):
        for piece in self.pieces:
            yield piece


class Recorder:
    def __init__(self) -> None:
        self.events: list[dict] = []

    async def on_request(self, context):
        self.events.append({"hook": "on_request", "context": hook_context(context)})

    async def on_response(self, context):
        self.events.append({"hook": "on_response", "context": hook_context(context)})

    async def on_error(self, context):
        self.events.append({"hook": "on_error", "context": hook_context(context)})


def hook_context(context) -> dict:
    response = context.response
    return {
        "provider_key": context.provider_key,
        "model_id": context.model_id,
        "message_count": context.message_count,
        "tool_count": context.tool_count,
        "request_json": context.request_json,
        "stream": context.stream,
        "retry_attempt": context.retry_attempt,
        "http_status": context.http_status,
        "response_headers": context.response_headers,
        "error_type": type(context.error).__name__ if context.error is not None else None,
        "error": str(context.error) if context.error is not None else None,
        "response": None if response is None else {
            "content": response.content,
            "reasoning_content": response.reasoning_content,
            "tool_calls": [[c.id, c.name, c.arguments] for c in response.tool_calls],
            "usage": [response.usage.prompt_tokens, response.usage.completion_tokens,
                      response.usage.total_tokens, response.usage.reasoning_tokens],
            "usage_reported": response.usage_reported,
            "finish_reason": response.finish_reason,
            "cache": [response.cache_usage.cache_read_tokens, response.cache_usage.cache_write_tokens,
                      response.cache_usage.cache_miss_tokens, response.cache_usage.total_prompt_tokens],
            "chat_completion_metadata": response.chat_completion_metadata,
            "reported_effort": response.provider_reported_reasoning_effort,
            "reported_tier": response.provider_reported_service_tier,
        },
    }


def tool_delta(delta) -> dict | None:
    if delta is None:
        return None
    return {"parsed": list(parse_tool_call_delta(delta)),
            "extra_content": _delta_value(delta, "extra_content"), "repr": str(delta)}


def chunk_projection(chunk) -> dict:
    return {
        "content": chunk.content,
        "reasoning_content": chunk.reasoning_content,
        "tool_call_delta": tool_delta(chunk.tool_call_delta),
        "finish_reason": chunk.finish_reason,
        "chat_completion_metadata": chunk.chat_completion_metadata,
        "reported_effort": chunk.provider_reported_reasoning_effort,
        "reported_tier": chunk.provider_reported_service_tier,
    }


def request_projection(request: httpx.Request) -> dict:
    return {
        "method": request.method,
        "url": str(request.url),
        "headers": [[name.decode("latin-1"), value.decode("latin-1")] for name, value in request.headers.raw],
        "body": request.content.decode("utf-8"),
    }


def make_provider(responses, *, provider_key="byok", base_url=BASE_URL, retry=None, headers=None,
                  model="model-a"):
    sent: list[dict] = []
    queue = list(responses)

    async def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request_projection(request))
        status, response_headers, pieces = queue.pop(0) if len(queue) > 1 else queue[0]
        return httpx.Response(status, headers=response_headers, stream=Pieces(pieces))

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    info = ModelInfo(id=f"test/{model}", name="Model A", provider_name="Test Provider",
                     provider_key=provider_key, provider_mode="byok")
    provider = OpenAICompatProvider(base_url=base_url, api_key=KEY, model_info=info,
                                    retry_config=retry or RetryConfig(max_retries=0),
                                    http_client=client, default_headers=headers)
    recorder = Recorder()
    provider.hooks.register(recorder)
    return provider, sent, recorder


def b64(pieces: list[bytes]) -> list[str]:
    return [base64.b64encode(piece).decode("ascii") for piece in pieces]


def sse(*events, done=True, crlf=False) -> bytes:
    text = "".join(f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
                   if not isinstance(event, str) else event for event in events)
    if done:
        text += "data: [DONE]\n\n"
    if crlf:
        text = text.replace("\n", "\r\n")
    return text.encode("utf-8")


def chunk(delta=None, finish=None, **extra):
    choice = {"index": 0, "delta": {} if delta is None else delta, "finish_reason": finish}
    return {"id": "c-1", "object": "chat.completion.chunk", "created": 1, "model": "m",
            "choices": [choice], **extra}


USAGE = {"id": "c-1", "object": "chat.completion.chunk", "created": 1, "model": "m", "choices": [],
         "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150,
                   "prompt_tokens_details": {"cached_tokens": 100},
                   "completion_tokens_details": {"reasoning_tokens": 7}}}


def split_every(data: bytes, size: int) -> list[bytes]:
    return [data[i:i + size] for i in range(0, len(data), size)]


def stream_scenarios() -> list[dict]:
    text = sse(chunk({"role": "assistant", "content": ""}), chunk({"content": "The keeper "}),
               chunk({"content": "nods."}), chunk(finish="stop"), USAGE)
    tool = sse(
        chunk({"tool_calls": [{"index": 0, "id": "call_1", "type": "function",
                               "function": {"name": "roll", "arguments": ""}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": "{\"n\":"}}]}),
        chunk({"tool_calls": [{"index": 0, "function": {"arguments": " 2}"}}]}),
        chunk(finish="tool_calls"), USAGE)
    parallel = sse(
        chunk({"tool_calls": [
            {"index": 0, "id": "a", "type": "function", "function": {"name": "roll", "arguments": "{}"}},
            {"index": 1, "id": "b", "type": "function", "function": {"name": "roll", "arguments": "{\"n\":1}"}},
        ]}, finish="tool_calls"))
    deepseek = sse(chunk({"reasoning_content": "I think the door is locked", "content": "I think the door"}),
                   chunk({"reasoning_content": " so", "content": "It is locked."}), chunk(finish="stop"))
    inline = sse(chunk({"content": "Visible <thi"}), chunk({"content": "nk>private"}),
                 chunk({"content": " thought</THINK> after"}), chunk({"content": " <|chan"}),
                 chunk({"content": "nel|>final done"}), chunk(finish="stop"))
    folded = sse(chunk({"content": "Straße <THINK>x</think> ﬁn"}), chunk(finish="stop"))
    openrouter = sse(
        chunk({"reasoning": "Plan", "reasoning_details": [
            {"type": "reasoning.text", "text": "Plan", "index": 0, "signature": None}]}),
        chunk({"reasoning": " more", "reasoning_details": [
            {"type": "reasoning.text", "text": " more", "index": 0, "signature": "sig-1"}]}),
        chunk({"content": "Done."}), chunk(finish="stop"))
    mistral = sse(chunk({"content": [{"type": "thinking", "thinking": [{"type": "text", "text": "hm"}]},
                                     {"type": "text", "text": "Answer"}]}), chunk(finish="stop"))
    gemini = sse(chunk({"tool_calls": [{"index": 0, "id": "g1", "type": "function",
                                        "function": {"name": "roll", "arguments": "{}"},
                                        "extra_content": {"google": {"thought_signature": "abc"}}}]},
                       finish="tool_calls"))
    scenarios = [
        {"name": "text", "pieces": [text]},
        {"name": "text-small-pieces", "pieces": split_every(text, 7)},
        {"name": "text-crlf", "pieces": split_every(sse(chunk({"content": "Hi"}), chunk(finish="stop"), crlf=True), 5)},
        {"name": "tool-split-arguments", "pieces": split_every(tool, 33)},
        {"name": "parallel-tools", "pieces": [parallel]},
        {"name": "deepseek-echo", "pieces": [deepseek]},
        {"name": "inline-markers", "pieces": [inline]},
        {"name": "casefold-length", "pieces": [folded]},
        {"name": "openrouter-details", "pieces": [openrouter], "provider_key": "openrouter"},
        {"name": "mistral-blocks", "pieces": [mistral], "provider_key": "mistral"},
        {"name": "tool-extra-content", "pieces": [gemini]},
        {"name": "no-done", "pieces": [sse(chunk({"content": "Hi"}), chunk(finish="stop"), done=False)]},
        {"name": "no-finish", "pieces": [sse(chunk({"content": "Hi"}))]},
        {"name": "done-without-blank-line", "pieces": [sse(chunk({"content": "Hi"}), chunk(finish="stop"), done=False) + b"data: [DONE]"]},
        {"name": "done-split", "pieces": [sse(chunk({"content": "Hi"}), chunk(finish="stop"), done=False) + b"data: [DO", b"NE]\n\n"]},
        {"name": "done-no-space", "pieces": [sse(chunk({"content": "Hi"}), chunk(finish="stop"), done=False) + b"data:[DONE]\n\n"]},
        {"name": "error-event", "pieces": [sse(chunk({"content": "Hi"}), {"error": {"message": "Overloaded"}})]},
        {"name": "error-event-no-message", "pieces": [sse({"error": {"code": 5}})]},
        {"name": "malformed-json", "pieces": [b"data: {not json\n\n"]},
        {"name": "comments-and-fields", "pieces": [b": keepalive\n\nevent: message\nid: 7\nretry: 100\n" + sse(chunk({"content": "Hi"}), chunk(finish="stop"))]},
        {"name": "multiline-data", "pieces": [b'data: {"choices":[{"delta":{"content":"a"},\ndata: "finish_reason":"stop"}]}\n\n' + b"data: [DONE]\n\n"]},
        {"name": "utf8-split", "pieces": split_every(sse(chunk({"content": "é\U0001f3b2日"}), chunk(finish="stop")), 3)},
        {"name": "finish-length", "pieces": [sse(chunk({"content": "cut"}, finish="length"))]},
        {"name": "content-block-not-object", "pieces": [sse(chunk({"content": ["text"]}), chunk(finish="stop"))]},
        {"name": "content-not-text", "pieces": [sse(chunk({"content": 5}), chunk(finish="stop"))]},
        {"name": "reasoning-not-text", "pieces": [sse(chunk({"reasoning_content": 5}), chunk(finish="stop"))]},
        {"name": "tool-identity-changed", "pieces": [sse(
            chunk({"tool_calls": [{"index": 0, "id": "a", "function": {"name": "roll", "arguments": ""}}]}),
            chunk({"tool_calls": [{"index": 0, "id": "b", "function": {"arguments": "{}"}}]}), chunk(finish="tool_calls"))]},
        {"name": "tool-index-missing", "pieces": [sse(chunk({"tool_calls": [{"id": "a", "function": {"name": "roll"}}]}, finish="tool_calls"))]},
        {"name": "tool-calls-dict", "pieces": [sse(chunk({"tool_calls": {"index": 0}}, finish="tool_calls"))]},
        {"name": "usage-raw-shapes", "pieces": [sse(chunk({"content": "x"}, finish="stop"),
            {"choices": [], "usage": {"prompt_tokens": "12", "completion_tokens": 3.9, "cached_tokens": 4,
                                      "cache_write_tokens": True, "output_tokens_details": {"reasoning_tokens": 2}}})]},
        {"name": "usage-missing-total", "pieces": [sse(chunk({"content": "x"}, finish="stop"),
            {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 2, "prompt_cache_hit_tokens": 9}})]},
        {"name": "empty-choices-and-null-delta", "pieces": [sse({"choices": []}, {"choices": [{"delta": None}]},
                                                                  chunk({"content": "x"}, finish="stop"))]},
        {"name": "delta-not-object", "pieces": [sse({"choices": [{"delta": "str", "finish_reason": "stop"}]})]},
        {"name": "chunk-not-object", "pieces": [sse([1, 2])]},
        {"name": "choices-not-list", "pieces": [sse({"choices": {"a": 1}})]},
        {"name": "finish-not-text", "pieces": [sse(chunk({"content": "x"}, finish=7))]},
        {"name": "empty-finish", "pieces": [sse(chunk({"content": "x"}, finish=""))]},
        {"name": "http-400-context", "status": 400, "pieces": [b'{"error":{"message":"This model\'s maximum context length is 8192 tokens","code":"context_length_exceeded"}}']},
        {"name": "http-400-plain", "status": 400, "pieces": [b'{"error":{"message":"Unsupported parameter: tool_choice"}}']},
        {"name": "http-401", "status": 401, "pieces": [b'{"error":{"message":"bad key"}}']},
        {"name": "http-404", "status": 404, "pieces": [b"not found"]},
        {"name": "http-429-retry-after", "status": 429, "headers": {"retry-after": "7"}, "pieces": [b'{"error":"slow down"}']},
        {"name": "http-500", "status": 500, "pieces": [b""]},
        {"name": "http-503", "status": 503, "pieces": [b"  Service Unavailable  "]},
        {"name": "groq-drops-names", "pieces": [text], "base_url": "https://api.groq.com/openai/v1",
         "messages": [{"role": "user", "name": "dan", "content": "hi"}]},
        {"name": "openrouter-session", "pieces": [text], "base_url": "https://openrouter.ai/api/v1",
         "kwargs": {"_request_context_id": "sylvia-session-1", "extra_body": {"provider": {"sort": "price"}}}},
        {"name": "configured-headers", "pieces": [text],
         "headers": {"HTTP-Referer": "https://example.test", "X-Title": "Sylvia"},
         "kwargs": {"extra_headers": {"x-opencode-session": "sylvia_abc", "User-Agent": "Sylvia-GM/0.4.1"}}},
    ]
    return scenarios


async def run_stream(scenario: dict) -> dict:
    status = scenario.get("status", 200)
    headers = {"content-type": "text/event-stream" if status == 200 else "application/json",
               **scenario.get("headers", {})}
    provider, sent, recorder = make_provider(
        [(status, headers, scenario["pieces"])],
        provider_key=scenario.get("provider_key", "byok"),
        base_url=scenario.get("base_url", BASE_URL),
        headers=scenario.get("headers") if "configured" in scenario["name"] else None,
    )
    kwargs = {"max_tokens": 4096, "tool_choice": "auto", **scenario.get("kwargs", {})}
    chunks, error = [], None
    try:
        async for item in provider.generate_stream(scenario.get("messages", MESSAGES), tools=TOOLS,
                                                   timeout=30.0, **kwargs):
            chunks.append(chunk_projection(item))
    except ProviderError as exc:
        error = {"class": type(exc).__name__, "message": str(exc),
                 "retry_after": getattr(exc, "retry_after", None)}
    await provider.close()
    return {"name": scenario["name"], "status": status, "response_headers": headers,
            "pieces": b64(scenario["pieces"]), "provider_key": scenario.get("provider_key", "byok"),
            "base_url": scenario.get("base_url", BASE_URL), "kwargs": kwargs,
            "messages": scenario.get("messages", MESSAGES), "configured_headers": scenario.get("headers") if "configured" in scenario["name"] else None,
            "chunks": chunks, "error": error, "hooks": recorder.events, "requests": sent}


def completion(message, finish="stop", usage=None, **extra):
    body = {"id": "x", "object": "chat.completion", "created": 1, "model": "m",
            "choices": [{"index": 0, "finish_reason": finish, "message": message}], **extra}
    if usage is not None:
        body["usage"] = usage
    return json.dumps(body).encode()


def completion_scenarios() -> list[dict]:
    return [
        {"name": "text", "body": completion({"role": "assistant", "content": "Hello"},
                                            usage={"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5})},
        {"name": "tools", "body": completion({"role": "assistant", "content": None, "tool_calls": [
            {"id": "a", "type": "function", "function": {"name": "roll", "arguments": "{\"n\":2}"}},
            {"id": "b", "type": "custom", "custom": {"name": "x", "input": "y"}},
            {"id": "c", "type": "function", "function": {"name": "roll"}}]}, finish="tool_calls")},
        {"name": "think-inline", "body": completion({"content": "<think>secret</think>Shown"})},
        {"name": "think-only", "body": completion({"content": "<think>all private"})},
        {"name": "reasoning-field", "body": completion({"content": "A", "reasoning": "B"})},
        {"name": "null-finish", "body": completion({"content": "A"}, finish=None)},
        {"name": "no-choices", "body": json.dumps({"choices": []}).encode()},
        {"name": "usage-details", "body": completion({"content": "A"}, usage={
            "prompt_tokens": 10, "completion_tokens": 4, "total_tokens": 14,
            "completion_tokens_details": {"reasoning_tokens": None},
            "prompt_tokens_details": {"cached_tokens": 6, "cache_write_tokens": 1}})},
        {"name": "openrouter-details", "provider_key": "openrouter", "body": completion({
            "content": "A", "reasoning": "R", "reasoning_details": [{"type": "reasoning.encrypted", "data": "xyz", "id": "r1"}]})},
        {"name": "http-400-context", "status": 400, "body": b'{"error":{"message":"prompt is too long"}}'},
        {"name": "http-403", "status": 403, "body": b'{"error":{"message":"forbidden"}}'},
        {"name": "http-429-retry", "status": 429, "headers": {"retry-after": "2"}, "body": b"{}", "retries": 2},
        {"name": "http-502-retry", "status": 502, "body": b"bad gateway", "retries": 1},
        {"name": "http-504", "status": 504, "body": b"timeout"},
    ]


async def run_completion(scenario: dict) -> dict:
    status = scenario.get("status", 200)
    headers = {"content-type": "application/json", **scenario.get("headers", {})}
    retries = scenario.get("retries", 0)
    retry = RetryConfig(max_retries=retries, base_delay=0.0, max_delay=0.0)
    provider, sent, recorder = make_provider([(status, headers, [scenario["body"]])],
                                             provider_key=scenario.get("provider_key", "byok"), retry=retry)
    result, error = None, None
    try:
        response = await provider.generate(MESSAGES, tools=TOOLS, max_tokens=256)
        result = hook_context(type("C", (), {"provider_key": "", "model_id": "", "message_count": 0,
                                             "tool_count": 0, "request_json": None, "stream": False,
                                             "retry_attempt": 0, "http_status": None,
                                             "response_headers": {}, "error": None,
                                             "response": response})())["response"]
    except ProviderError as exc:
        error = {"class": type(exc).__name__, "message": str(exc),
                 "retry_after": getattr(exc, "retry_after", None)}
    await provider.close()
    return {"name": scenario["name"], "status": status, "response_headers": headers,
            "body": base64.b64encode(scenario["body"]).decode("ascii"), "retries": retries,
            "provider_key": scenario.get("provider_key", "byok"), "response": result, "error": error,
            "hooks": recorder.events, "requests": sent}


def request_scenarios() -> list[dict]:
    recording = json.loads(RECORDING.read_text(encoding="utf-8"))
    cases = [
        {"name": "minimal", "stream": True, "kwargs": {}},
        {"name": "no-stream", "stream": False, "kwargs": {"max_tokens": None}},
        {"name": "assorted", "stream": True, "kwargs": {
            "temperature": 0, "tool_choice": {"type": "function", "function": {"name": "roll"}},
            "reasoning_effort": "high", "service_tier": "priority", "max_tokens": 10,
            "response_format": {"type": "json_object"}, "parallel_tool_calls": False,
            "stream_options": {"include_usage": False}}},
        {"name": "extra-body", "stream": True, "kwargs": {
            "max_tokens": 5, "extra_body": {"max_tokens": 9, "top_k": 4, "reasoning": {"effort": "low"}}}},
        {"name": "floats-and-unicode", "stream": False, "kwargs": {"temperature": 0.7, "top_p": 1.0,
                                                                     "seed": 12, "user": "é x"}},
    ]
    for index, request in enumerate(recording["model_requests"]):
        body = request["body"]
        cases.append({"name": f"ruleless-turn-{index}", "stream": True, "messages": body["messages"],
                      "tools": body["tools"], "kwargs": {"max_tokens": body["max_tokens"],
                                                         "tool_choice": body["tool_choice"]},
                      "model": body["model"], "recorded_body": body})
    return cases


async def run_request(case: dict) -> dict:
    messages = case.get("messages", MESSAGES)
    tools = case.get("tools", TOOLS)
    request_dict = ChatCompletionsTransport().format_request(
        case.get("model", "model-a"), messages, tools, case["stream"], **case["kwargs"])
    body = sse(chunk({"content": "x"}, finish="stop")) if case["stream"] else completion({"content": "x"})
    provider, sent, _ = make_provider([(200, {"content-type": "text/event-stream" if case["stream"]
                                             else "application/json"}, [body])],
                                  model=case.get("model", "model-a"))
    if case["stream"]:
        async for _ in provider.generate_stream(messages, tools=tools, **case["kwargs"]):
            pass
    else:
        await provider.generate(messages, tools=tools, **case["kwargs"])
    await provider.close()
    out = {"name": case["name"], "model": case.get("model", "model-a"), "stream": case["stream"],
           "kwargs": case["kwargs"],
           "format_request": request_dict, "wire": sent[0]}
    if "messages" in case:
        out["messages"] = messages
        out["tools"] = tools
        out["recorded_body"] = case["recorded_body"]
        assert json.loads(sent[0]["body"]) == case["recorded_body"], "SDK body differs from the recording"
        assert list(json.loads(sent[0]["body"])) == list(case["recorded_body"])
    return out


def inline_filter_vectors() -> list[dict]:
    sequences = [
        ["Hello world"], ["<think>a</think>b"], ["<thi", "nk>a", "</th", "ink>b"],
        ["x <|channel|>analysis y <|channel|>final z"], ["<|start|>assistant<|message|>hi<|end|>"],
        ["trailing <"], ["trailing <th"], ["STRASSE ß<think>", "q</think>w"],
        ["ßßß<thi", "nk>p</think>v"], ["ﬁ<think>p", "</think>v"],
        ["<THINKING>A</thinking><reasoning>B</reasoning>C"], ["<mm:think>m</mm:think>n", ""],
        ["</think>stray"], ["<"], ["", ""], ["İ<think>", "x</think>y"],
    ]
    out = []
    for pieces in sequences:
        flt = _InlineReasoningFilter()
        results = [list(flt.feed(piece)) for piece in pieces]
        results.append(list(flt.feed("", final=True)))
        out.append({"pieces": pieces, "results": results})
    return out


def metadata_vectors() -> list[dict]:
    construct = chat_completion_chunk.ChoiceDelta.construct
    from openai._models import construct_type
    sequences = [
        {"preserve_reasoning": True, "deltas": [{"reasoning": "a"}, {"reasoning": "b", "reasoning_content": "c"}]},
        {"preserve_reasoning": False, "deltas": [{"reasoning": "a"}]},
        {"preserve_content": True, "deltas": [{"content": "x"}, {"content": [{"type": "thinking", "thinking": []}]}]},
        {"preserve_content": True, "deltas": [{"content": "x"}]},
        {"deltas": [{"reasoning_details": [{"id": "r", "type": "t", "text": "a"}, {"index": 1, "data": "d"}]},
                    {"reasoning_details": [{"id": "r", "text": "b", "signature": None}]}]},
        {"deltas": [{"reasoning_details": [{"index": True, "text": "a"}, {"index": 1, "text": "b"}]}]},
        {"deltas": [{"reasoning_details": [{"type": "a"}]}, {"reasoning_details": [{"index": 0, "type": "b"}]}]},
        {"deltas": [{"reasoning_details": [{"index": 0, "n": 1}]}, {"reasoning_details": [{"index": 0, "n": 1.0}]}]},
        {"deltas": [{"reasoning_details": [{"index": 0.5}]}]},
        {"deltas": [{"reasoning_details": ["x"]}]},
        {"deltas": [{"reasoning_details": [{"index": 0, "text": 5}]}]},
        {"deltas": [{"tool_calls": [{"index": 0, "id": "a", "extra_content": {"k": 1}}]},
                    {"tool_calls": [{"index": 0, "extra_content": {"k": 1}}]}]},
        {"deltas": [{"tool_calls": [{"index": 0, "extra_content": {"k": 1}}]}]},
        {"deltas": [{"tool_calls": [{"index": 0, "id": "a", "extra_content": {"k": 1}}]},
                    {"tool_calls": [{"index": 0, "extra_content": {"k": 2}}]}]},
        {"deltas": [{"tool_calls": [{"index": 0, "id": "a", "extra_content": "no"}]}]},
        {"deltas": [{"tool_calls": [{"index": -1}]}]},
        {"deltas": [{"tool_calls": "ab"}]},
        {"deltas": [{"tool_calls": [3, None]}]},
        {"deltas": [{"tool_calls": [{"index": 0, "id": "a"}, {"index": 0, "id": "a"}]}]},
    ]
    out = []
    for case in sequences:
        meta = ChatCompletionMetadata(preserve_reasoning=case.get("preserve_reasoning", False),
                                      preserve_content=case.get("preserve_content", False))
        error = None
        try:
            for delta in case["deltas"]:
                meta.add(construct_type(value=delta, type_=chat_completion_chunk.ChoiceDelta))
            payload = meta.payload()
        except Exception as exc:  # noqa: BLE001 - vectors record whatever Python raises
            payload, error = None, {"class": type(exc).__name__, "message": str(exc)}
        out.append({**case, "payload": payload, "error": error})
    del construct
    return out


def sdk_models() -> dict:
    models = {
        "chunk": chat_completion_chunk.ChatCompletionChunk, "chunk_choice": chat_completion_chunk.Choice,
        "delta": chat_completion_chunk.ChoiceDelta, "delta_tool_call": chat_completion_chunk.ChoiceDeltaToolCall,
        "delta_function": chat_completion_chunk.ChoiceDeltaToolCallFunction,
        "usage": completion_usage.CompletionUsage, "completion_details": completion_usage.CompletionTokensDetails,
        "prompt_details": completion_usage.PromptTokensDetails, "completion": chat_completion.ChatCompletion,
        "completion_choice": chat_completion.Choice, "message": ChatCompletionMessage,
        "function_tool_call": ChatCompletionMessageFunctionToolCall, "function": Function,
        "custom_tool_call": ChatCompletionMessageCustomToolCall,
    }
    return {name: list(get_model_fields(model)) for name, model in models.items()}


async def main_async() -> dict:
    return {
        "python": sys.version.split()[0],
        "sdk_models": sdk_models(),
        "streams": [await run_stream(s) for s in stream_scenarios()],
        "completions": [await run_completion(s) for s in completion_scenarios()],
        "requests": [await run_request(c) for c in request_scenarios()],
        "inline_filter": inline_filter_vectors(),
        "metadata": metadata_vectors(),
    }


def main() -> int:
    document = asyncio.run(main_async())
    OUTPUT.write_bytes((json.dumps(document, ensure_ascii=True, indent=1) + "\n").encode("ascii"))
    print(f"{len(document['streams'])} streams, {len(document['completions'])} completions, "
          f"{len(document['requests'])} requests -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
