"""Generate reference vectors for sylvia-provider's base types, errors and retry timing.

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe crates/sylvia-provider/tests/vectors/generate_base_vectors.py

Everything comes from the frozen Python source: the owner's error replies are produced by the
`except (..., ProviderError, ...)` handler of `interfaces/owner_process.py`, lifted out of the
command loop with `ast` and run unchanged. The output is deterministic and checked in, so the
Rust tests need no Python. Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import ast
import asyncio
import inspect
import json
import random
import struct
import sys
from datetime import UTC, datetime
from pathlib import Path

import httpx
from openai.types.chat.chat_completion_chunk import ChoiceDeltaToolCall

import sylvia_gm.interfaces.owner_process as owner_process
import sylvia_gm.provider._exceptions as exceptions
import sylvia_gm.provider._retry as retry
import sylvia_gm.provider._retry_after as retry_after
from sylvia_gm.provider._base import (
    PROVIDER_RETRY_DEFAULTS,
    CacheUsage,
    ModelInfo,
    RetryConfig,
)
from sylvia_gm.provider._stream_health import StreamHealthMonitor
from sylvia_gm.provider._tool_delta import parse_tool_call_delta

OUTPUT = Path(__file__).with_name("base_vectors.json")
NOW = datetime(2026, 11, 21, 12, 30, 15, 250_000, tzinfo=UTC)


def error_classes() -> list[type]:
    return [
        value for value in vars(exceptions).values()
        if isinstance(value, type) and issubclass(value, exceptions.ProviderError)
    ]


def owner_internal_reply() -> dict:
    """Run the owner's `except Exception` handler (the one after the provider-error handler)."""
    tree = ast.parse(inspect.getsource(owner_process))
    for node in ast.walk(tree):
        if isinstance(node, ast.Try):
            names = [getattr(h.type, "id", None) for h in node.handlers]
            if "Exception" in names and any(isinstance(h.type, ast.Tuple) for h in node.handlers):
                handler = node.handlers[names.index("Exception")]
                break
    else:
        raise SystemExit("owner internal-error handler not found")
    body = [stmt for stmt in handler.body if not (isinstance(stmt, ast.Expr) and isinstance(stmt.value, ast.Call)
                                                   and getattr(stmt.value.func, "id", None) == "print")]
    function = ast.FunctionDef(
        name="build_internal", args=ast.arguments(posonlyargs=[], args=[ast.arg("error"), ast.arg("request_id")],
                                                  kwonlyargs=[], kw_defaults=[], defaults=[]),
        body=body + [ast.Return(ast.Name("reply", ast.Load()))], decorator_list=[], returns=None, type_params=[],
    )
    namespace = dict(vars(owner_process))
    exec(compile(ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[])), owner_process.__file__, "exec"), namespace)
    return namespace["build_internal"](TypeError("unexpected keyword"), "r-1")


def owner_error_reply_function():
    """Compile the owner's provider-error handler into a function of (error, method, params)."""
    tree = ast.parse(inspect.getsource(owner_process))
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and isinstance(node.type, ast.Tuple):
            names = {getattr(item, "id", None) for item in node.type.elts}
            if "ProviderError" in names and "ConfigError" in names:
                handler = node
                break
    else:
        raise SystemExit("owner provider-error handler not found")
    body = handler.body + [ast.Return(ast.Name("reply", ast.Load()))]
    function = ast.FunctionDef(
        name="build_reply",
        args=ast.arguments(
            posonlyargs=[], args=[ast.arg(name) for name in ("error", "method", "params", "request_id")],
            kwonlyargs=[], kw_defaults=[], defaults=[],
        ),
        body=body, decorator_list=[], returns=None, type_params=[],
    )
    module = ast.fix_missing_locations(ast.Module(body=[function], type_ignores=[]))
    namespace = dict(vars(owner_process))
    exec(compile(module, owner_process.__file__, "exec"), namespace)
    return namespace["build_reply"]


def error_vectors() -> dict:
    classes = error_classes()
    hierarchy = [
        {
            "class": cls.__name__,
            "category": cls.category.value,
            "bases": [base.__name__ for base in cls.__mro__ if base in classes and base is not cls],
        }
        for cls in classes
    ]
    build_reply = owner_error_reply_function()

    def make(cls, message):
        if cls is exceptions.ProviderContinuationRequiredError:
            return cls(message, continuation_token="secret-token", native_parts=[{"type": "x"}])
        return cls(message)

    replies = []
    for cls in classes:
        for message in ("Upstream said: context_length_exceeded", ""):
            for accepted, method, submitted in (
                (None, "submit_session_input", "m-1"),
                ("m-1", "submit_session_input", "m-1"),
                ("m-1", "submit_session_input", "m-2"),
                ("m-1", "create_guided_campaign_draft", "m-1"),
            ):
                error = make(cls, message)
                if accepted is not None:
                    error.accepted_message_id = accepted
                reply = build_reply(error, method, {"message_id": submitted}, "r-1")
                replies.append({
                    "class": cls.__name__, "message": message, "accepted_message_id": accepted,
                    "method": method, "submitted_message_id": submitted, "reply": reply,
                })
    delays = [
        None, 0.0, -1.0, 0.5, 1.0, 30.0, 1e-05, 0.0001, 1.5, 2.25, 59.999999, 120.0, 0.1 + 0.2,
        123456.789, 999999.5, 604799.0, 604799.9999, 604800.0, 1e9, 3.0000005, 7.123456789,
        float("inf"), float("nan"), 5e-324, 0.00001234567,
    ]
    for delay in delays:
        error = exceptions.RateLimitError("limited", retry_after=delay)
        reply = build_reply(error, "submit_session_input", {}, "r-1")
        replies.append({
            "class": "RateLimitError", "message": "limited", "retry_after": _json_float(delay),
            "method": "submit_session_input", "submitted_message_id": None,
            "accepted_message_id": None, "reply": reply,
        })
    rejection = exceptions.TokenLimitError.provider_rejection()
    return {"hierarchy": hierarchy, "replies": replies, "token_limit_rejection": str(rejection),
            "internal_reply": owner_internal_reply()}


def _json_float(value):
    if value is None:
        return None
    if value != value:
        return "nan"
    if value in (float("inf"), float("-inf")):
        return "inf" if value > 0 else "-inf"
    return value


def g_vectors() -> list[list]:
    rng = random.Random(20261121)
    values = [0.0, 1.0, 30.0, 1e-05, 1e-4, 1e-5 * 9.99999, 123456.0, 1234567.0, 999999.5, 999995.0,
              0.000123456789, 9.9999995, 99999.95, 100000.0, 1e16, 1e-300, 5e-324, 1.7976931348623157e308,
              604799.0, 0.5, 2.5, 0.125, 1.0000005, 1.0000015, 123.4565, 0.30000000000000004]
    while len(values) < 3000:
        values.append(abs(struct.unpack("<d", struct.pack("<Q", rng.getrandbits(64)))[0]))
    for _ in range(2000):
        values.append(rng.uniform(0, 604800))
        values.append(round(rng.uniform(0, 1000), rng.randint(0, 7)))
        values.append(rng.randint(1, 10**7) / 10 ** rng.randint(0, 9))
    values = [value for value in values if value == value and value != float("inf")]
    return [[value, f"{value:g}"] for value in values]


def context_vectors() -> list[list]:
    texts = [
        "context_length_exceeded", "This model's maximum context length is 8192 tokens",
        "CONTEXT WINDOW full", "Prompt Is Too Long", "too many tokens", "token limit",
        "unsupported parameter max_tokens", "", "context  window", "conte℮xt limit",
        "too many tokenſ", "INPUT TOKEN LIMIT reached", "context window",
        "Kontext window", "CONTEXT_LENGTH_EXCEEDED", "İnput token limit", "maßimum",
        "context length exceeded!", "The context exceeded the limit",
    ]
    return [[text, exceptions.is_context_rejection(text)] for text in texts]


def tool_delta_vectors() -> list[dict]:
    deltas = [
        {"index": 0, "id": "call_1", "type": "function", "function": {"name": "roll", "arguments": ""}},
        {"index": 1, "function": {"arguments": "{\"a\":"}},
        {"index": 2, "id": None, "function": {"name": None, "arguments": None}},
        {"id": "x", "name": "top", "arguments": "{}"},
        {"id": "x", "name": "top", "partial_json": "{\"q\""},
        {"id": "x", "name": "top", "arguments": None, "partial_json": "{\"q\""},
        {"index": -3, "id": 5, "function": {"name": 7, "arguments": ["no"]}},
        {"index": True, "function": None, "name": "n", "arguments": "a"},
        {"index": 1.0, "function": "text"},
        {"index": 99999999999, "function": {}},
        {},
        "not an object",
        None,
        [1, 2],
    ]
    out = []
    for delta in deltas:
        parsed = list(parse_tool_call_delta(delta)) if isinstance(delta, dict) else list(parse_tool_call_delta(delta))
        out.append({"delta": delta, "parsed": parsed})
        # The SDK object and its serialized form must read the same.
        if isinstance(delta, dict) and isinstance(delta.get("index"), int) and not isinstance(
                delta.get("index"), bool) and delta.get("index", -1) >= 0 and isinstance(delta.get("function"), dict):
            try:
                sdk = ChoiceDeltaToolCall.model_validate(delta)
            except Exception:
                continue
            assert list(parse_tool_call_delta(sdk)) == parsed, delta
    return out


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW


def retry_after_vectors() -> list[list]:
    retry_after.datetime = _FixedDatetime
    values = [
        None, "", "5", " 5 ", "5.5", "+2", "-1", "0", "1e3", "1_000", "1__0", "_1", "1_", "inf", "nan",
        "-inf", "Infinity", ".5", "5.", "1e", "abc", " 5 ", "\x1c7\x1f", "x" * 129, "1" * 128,
        "Sat, 21 Nov 2026 12:31:00 GMT", "Sat, 21 Nov 2026 12:30:00 GMT", "21 Nov 2026 12:31:00 +0100",
        "Sat, 21 Nov 2026 12:31:00", "Sat, 21 Nov 2026 12:31:00 -0000", "Sat, 21 Nov 2026 12:31:00 +0000",
        "Saturday, 21-Nov-26 13:00:00 GMT", "Sat Nov 21 13:00:00 2026", "Sat, 21 Nov 2026 13:00 EST",
        "Sat, 21 Nov 2026 13:00:00 PDT", "Sat,21 Nov 2026 13:00:00 GMT", "21 November 2026 13:00:00 UT",
        "Nov 21 2026 13:00:00 GMT", "Sat, 31 Nov 2026 13:00:00 GMT", "Sat, 21 Nov 2026 24:00:00 GMT",
        "Sat, 21 Nov 2026 13:00:00 +2400", "Sat, 21 Nov 2026 13:00:00 +2359", "Sat, 21 Nov 26 13.00 GMT",
        "Sat, 21 Nov 2026 13.00.30 GMT", "Sat, 21 Nov 99 13:00:00 GMT", "Sat, 21 Nov 68 13:00:00 GMT",
        "Mon, 29 Feb 2028 13:00:00 GMT", "Mon, 29 Feb 2027 13:00:00 GMT", "Sat, 21 Nov 2026 13:00:00 XYZ",
        "21 Nov 2026 13:00:00+0130", "21 Nov 2026 13:00:00-0130", "Sat, 21 Nov 10000 13:00:00 GMT",
        "Sat, 21 Nov 2026 13:00:00 +99999", "21 Nov 2026 13:00:00 GMT extra", "2026 Nov 21 13:00:00 GMT",
        "Sat, 21 Nov 2026 1_3:00:00 GMT", "Sat, 21 Nov 2026 +13:00:00 GMT", "Sat 21 Nov 2026 13:00:00 GMT",
        "Wed, 01 Jan 1986 00:00:00 GMT", "21 Nov 2026 13:00:00 z", "Sat, 21 nov 2026 13:00:60 GMT",
        "21 Nov 2026 12:30:15 GMT",
        # Unicode decimal digits: Python's float() and int() accept them (httpx decodes UTF-8).
        chr(0x665), chr(0x661) + chr(0x660), chr(0xFF11) + chr(0xFF12), chr(0x966) + "." + chr(0x967),
        "Sat, " + chr(0x662) + chr(0x661) + " Nov 2026 13:00:00 GMT", "21 Nov 2026 1" + chr(0x663) + ":00:00 GMT",
        chr(0xB2), "1" * 30, "21 Nov 2026 13:00:00 +" + "9" * 25, "Sat, 21 Nov " + "9" * 25 + " 13:00:00 GMT",
    ]
    out = []
    for value in values:
        headers = {} if value is None else {"retry-after": value.encode("utf-8")}
        error = type("E", (), {"response": httpx.Response(429, headers=headers)})()
        out.append([value, _json_float(retry_after.retry_after_seconds(error))])
    return out


def delay_vectors() -> list[dict]:
    class FixedRandom:
        def __init__(self, unit):
            self.unit = unit

        def uniform(self, a, b):
            return a + (b - a) * self.unit

    configs = {"default": RetryConfig(), **PROVIDER_RETRY_DEFAULTS}
    out = []
    for name, config in configs.items():
        for attempt in range(1, 8):
            for unit in (0.0, 0.5, 0.999):
                for server in (None, 2.5, -1.0, float("inf"), 0.0):
                    retry.random = FixedRandom(unit)
                    out.append({
                        "config": name, "attempt": attempt, "unit": unit,
                        "retry_after": _json_float(server),
                        "delay": retry._build_delay(attempt, config, server),
                    })
    retry.random = random
    return out


def backoff_vectors() -> list[dict]:
    """Which failures retry_with_backoff retries, how often, and the server delays it honours."""

    scenarios = [
        ["RateLimitError", "RateLimitError", None],
        ["ProviderTimeoutError", None],
        ["StaleConnectionError", "ProviderConnectionError", None],
        ["AuthenticationError", None],
        ["InvalidResponseError", None],
        ["ProviderUnavailableError", None],
        ["ModelCapacityError", None],
        ["TokenLimitError", None],
        ["RateLimitError"] * 6 + [None],
        ["ProviderConnectionError"] * 2 + ["AuthenticationError", None],
        ["ProviderConnectionError"] * 3 + [None],
        ["IncompleteProviderStreamError", None],
    ]
    out = []
    for config_name, config in (("default", RetryConfig()), *PROVIDER_RETRY_DEFAULTS.items()):
        for scenario in scenarios:
            calls = []
            sleeps = []

            async def call():
                position = len(calls)
                calls.append(position)
                name = scenario[min(position, len(scenario) - 1)]
                if name is None:
                    return "ok"
                cls = getattr(exceptions, name)
                if cls in (exceptions.RateLimitError, exceptions.ProviderConnectionError):
                    raise cls(name, retry_after=0.25 * (position + 1))
                raise cls(name)

            async def fake_sleep(delay):
                sleeps.append(delay)

            retry.asyncio.sleep = fake_sleep
            try:
                outcome = asyncio.run(retry.retry_with_backoff(call, config))
            except exceptions.ProviderError as error:
                outcome = type(error).__name__ + ": " + str(error)
            finally:
                retry.asyncio.sleep = asyncio.sleep
            server_sleeps = [delay for delay in sleeps if delay in {0.25 * n for n in range(1, 10)}]
            out.append({"config": config_name, "scenario": scenario, "calls": len(calls),
                        "sleeps": len(sleeps), "server_sleeps": server_sleeps, "outcome": outcome})
    return out


def retry_configs() -> dict:
    def project(config: RetryConfig) -> dict:
        return {"max_retries": config.max_retries, "base_delay": config.base_delay,
                "max_delay": config.max_delay, "backoff_multiplier": config.backoff_multiplier,
                "retryable_statuses": sorted(config.retryable_statuses)}

    return {"default": project(RetryConfig()),
            "providers": [[key, project(value)] for key, value in PROVIDER_RETRY_DEFAULTS.items()]}


def model_info_vectors() -> list[dict]:
    cases = [
        {"id": "opencode-go/deepseek-v4"},
        {"id": "plain"},
        {"id": "a/b/c"},
        {"id": "a/b", "provider_model_id": "wire-x"},
        {"id": "x", "context_window": 8192},
        {"id": "x", "context_window": 8192, "declared_context_window": 4096},
        {"id": "x", "context_window": 8192, "context_window_source": "configured"},
        {"id": "/lead"},
    ]
    out = []
    for case in cases:
        info = ModelInfo(name="N", provider_name="P", provider_key="k", provider_mode="byok", **case)
        out.append({"fields": case, "wire_model_id": info.wire_model_id,
                    "context_window_source": info.context_window_source,
                    "max_output_tokens": info.max_output_tokens,
                    "builder_output_tokens": info.builder_output_tokens})
    return out


def cache_vectors() -> list[dict]:
    out = []
    for read, total in ((None, 0), (0, 0), (5, 0), (3, 7), (7, 7), (0, 10), (1, 3)):
        usage = CacheUsage(cache_read_tokens=read, total_prompt_tokens=total)
        out.append({"read": read, "total": total, "hit_ratio": usage.hit_ratio,
                    "savings_pct": usage.savings_pct})
    return out


def stale_messages() -> list[list]:
    async def never():
        await asyncio.sleep(10)
        yield None

    out = []
    for interval in (0.01, 0.02):
        monitor = StreamHealthMonitor(never(), health_interval=interval, provider_name="OpenCode Go")
        try:
            asyncio.run(monitor.__anext__())
        except exceptions.StaleConnectionError as error:
            out.append([interval, str(error)])
    return out


def main() -> int:
    document = {
        "python": sys.version.split()[0],
        "now": NOW.isoformat(),
        "now_micros": int(NOW.timestamp()) * 1_000_000 + NOW.microsecond,
        "errors": error_vectors(),
        "format_g": g_vectors(),
        "context_rejection": context_vectors(),
        "tool_deltas": tool_delta_vectors(),
        "retry_after": retry_after_vectors(),
        "delays": delay_vectors(),
        "retry_configs": retry_configs(),
        "backoff": backoff_vectors(),
        "model_info": model_info_vectors(),
        "cache_usage": cache_vectors(),
        "stale_messages": stale_messages(),
    }
    OUTPUT.write_bytes((json.dumps(document, ensure_ascii=True, indent=1) + "\n").encode("ascii"))
    print(f"{len(document['errors']['replies'])} replies, {len(document['format_g'])} g values -> {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
