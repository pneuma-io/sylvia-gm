"""Generate reference vectors for `sylvia_provider::admin_audit` (Admin Full Audit).

Run with the snapshot's own interpreter, from the repository root:

    .venv/Scripts/python.exe -I crates/sylvia-provider/tests/vectors/generate_admin_audit_vectors.py

Writes `admin_audit_vectors.json`. Each case wraps a fake provider (which fires its hooks as a
transport does: `on_request` with its wire request, then `on_response` or `on_error`) in an
`AdminAuditedProvider`, runs one operation and records every sink record. `exchange_id` values
are numbered by first appearance, `duration_ms` is 0 and `started_at_utc` is fixed, on both
sides, since they are random or clock readings. Also `usage`: `_usage_projection` for
responses. Reference tooling only; retired with the Python source.
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[4]
sys.path.insert(0, str(ROOT / "src"))

from sylvia_gm.provider._admin_audit import AdminAuditedProvider, _usage_projection  # noqa: E402
from sylvia_gm.provider._base import (  # noqa: E402
    CacheUsage,
    ModelInfo,
    Provider,
    ProviderResponse,
    ProviderStreamChunk,
    TokenUsage,
    ToolCall,
)
from sylvia_gm.provider._exceptions import ProviderConnectionError, TokenLimitError  # noqa: E402
from sylvia_gm.provider._hooks import HookContext  # noqa: E402

OUTPUT = Path(__file__).with_name("admin_audit_vectors.json")


def plain(value: object) -> object:
    return json.loads(json.dumps(value, ensure_ascii=False))


def response_fields(response: dict[str, Any] | None) -> ProviderResponse | None:
    if response is None:
        return None
    fields = dict(response)
    fields["usage"] = TokenUsage(**fields.get("usage", {}))
    fields["cache_usage"] = CacheUsage(**fields.get("cache_usage", {}))
    fields["tool_calls"] = [ToolCall(**call) for call in fields.get("tool_calls", [])]
    return ProviderResponse(**fields)


class Fake(Provider):
    def __init__(self, case: dict[str, Any]) -> None:
        super().__init__()
        self.case = case
        model = case.get("model")
        self.model_info = ModelInfo(**model) if model else None
        if "base_url" in case:
            self._base_url = case["base_url"]
        self.late: list = []

    def _context(self, **fields: Any) -> HookContext:
        return HookContext(provider_key=self.case.get("provider_key", "test"),
                           model_id="test/model", message_count=1, tool_count=0,
                           request_json=self.case.get("wire", {"model": "m", "messages": []}),
                           retry_attempt=self.case.get("retry_attempt", 0), **fields)

    async def generate(self, messages, tools=None, stream=False, timeout=60.0, **kwargs):
        await self.hooks.fire_on_request(self._context())
        if self.case.get("late"):
            # A runtime binds its late-usage listeners during the call, then credits later.
            self.late = self.hooks.late_usage_listeners("test", "audit-late")
        if self.case.get("fail"):
            await self.hooks.fire_on_error(self._context())
            raise ProviderConnectionError("Fake connection lost.")
        response = response_fields(self.case.get("response")) or ProviderResponse(content="ok")
        await self.hooks.fire_on_response(self._context(response=response))
        return response

    async def generate_stream(self, messages, tools=None, timeout=120.0, **kwargs):
        await self.hooks.fire_on_request(self._context())
        for chunk in self.case.get("chunks", []):
            yield ProviderStreamChunk(**chunk)
        if self.case.get("fail"):
            await self.hooks.fire_on_error(self._context())
            raise TokenLimitError("Fake token limit.")
        response = response_fields(self.case.get("response"))
        if response is not None:
            await self.hooks.fire_on_response(self._context(response=response))

    async def close(self) -> None:
        pass


MODEL = {"id": "test/model-a", "name": "Model A", "provider_name": "Test", "provider_key": "test",
         "provider_mode": "byok", "provider_model_id": "model-a-wire"}
MESSAGES = [{"role": "user", "content": "Hola"}]
TOOLS = [{"type": "function", "function": {"name": "roll"}}]
CHUNKS = [
    {"content": "Hel", "reasoning_content": "think"},
    {"content": "lo", "tool_call_delta": {"index": 0, "id": "c1", "name": "roll"}},
    {"content": "", "finish_reason": "stop", "provider_reported_reasoning_effort": "high",
     "provider_reported_service_tier": "priority"},
]
REPORTED = {"content": "ok", "usage": {"prompt_tokens": 10, "completion_tokens": 2,
                                       "total_tokens": 12, "reasoning_tokens": 1},
            "usage_reported": True, "cache_usage": {"cache_read_tokens": 4, "total_prompt_tokens": 10},
            "tool_calls": [{"id": "c1", "name": "roll", "arguments": "{}"}], "model": "m",
            "finish_reason": "tool_calls", "chat_completion_metadata": {"x": 1}}


def cases() -> list[dict[str, Any]]:
    endpoints = ["http://127.0.0.1:8080/v1", "https://api.example.test/v1?key=secret#frag",
                 "http://[::1]:8080/v1/", "https://host", "https://user:pw@host:9/x",
                 "relative/path", "", None, "https://host:443/v1", "https://HOST.Example/v1"]
    out: list[dict[str, Any]] = [
        {"name": f"context_fit_endpoint_{index}", "model": MODEL, "base_url": url,
         "operation": "context_fit", "context": {"request_id": "r-1", "nested": {"a": [1]}}}
        for index, url in enumerate(endpoints)
    ]
    out += [
        {"name": "context_fit_no_model", "operation": "context_fit", "context": {}},
        {"name": "context_fit_no_base_url", "model": MODEL, "operation": "context_fit", "context": {}},
        {"name": "generate_plain", "model": MODEL, "base_url": "http://h/v1", "operation": "generate",
         "options": {"max_tokens": 50}},
        {"name": "generate_reported", "model": MODEL, "operation": "generate", "response": REPORTED,
         "audit_context": {"request_id": "r-2"}, "retry_attempt": 2},
        {"name": "generate_counts_unreported", "model": MODEL, "operation": "generate",
         "response": {"content": "ok", "usage": {"prompt_tokens": 3}}},
        {"name": "generate_counts_zero", "model": MODEL, "operation": "generate",
         "response": {"content": "ok", "usage": {"prompt_tokens": 0, "completion_tokens": 0}}},
        {"name": "generate_reported_false", "model": MODEL, "operation": "generate",
         "response": {"content": "ok", "usage": {"prompt_tokens": 9}, "usage_reported": False}},
        {"name": "generate_failure", "model": MODEL, "operation": "generate", "fail": True},
        {"name": "generate_runtime_chatgpt", "model": MODEL, "operation": "generate",
         "provider_key": "chatgpt_subscription"},
        {"name": "generate_runtime_claude", "model": MODEL, "operation": "generate",
         "provider_key": "claude_runtime"},
        {"name": "generate_no_tools", "model": MODEL, "operation": "generate", "tools": None},
        {"name": "stream_complete", "model": MODEL, "operation": "stream", "chunks": CHUNKS,
         "response": REPORTED, "retry_attempt": 1},
        {"name": "stream_no_response", "model": MODEL, "operation": "stream", "chunks": CHUNKS},
        {"name": "stream_failure", "model": MODEL, "operation": "stream", "chunks": CHUNKS[:2],
         "fail": True},
        {"name": "stream_empty", "model": MODEL, "operation": "stream"},
        {"name": "late_usage", "model": MODEL, "operation": "generate", "late": True,
         "response": REPORTED},
    ]
    return out


async def run(case: dict[str, Any]) -> list[object]:
    records: list[object] = []
    fake = Fake(case)
    audited = AdminAuditedProvider(fake, sink=records.append, session_id="session-1", role="dm",
                                   provider_id="test", model_id="test/model-a",
                                   selection_mode=case.get("selection_mode", "session"))
    if case.get("audit_context"):
        audited.set_audit_context(case["audit_context"])
    tools = case.get("tools", TOOLS)
    if case["operation"] == "context_fit":
        audited.record_context_fit(case["context"])
    elif case["operation"] == "generate":
        try:
            await audited.generate(MESSAGES, tools=tools, **case.get("options", {}))
        except Exception:
            pass
        for listener in fake.late:
            listener(response_fields(case["response"]))
    elif case["operation"] == "stream":
        try:
            async for _ in audited.generate_stream(MESSAGES, tools=tools, timeout=30.0):
                pass
        except Exception:
            pass
    return plain(records)


def normalise(records: list[object]) -> list[object]:
    ids: dict[str, str] = {}
    text = json.dumps(records, ensure_ascii=False)
    out = json.loads(text)

    def walk(value: object) -> object:
        if isinstance(value, dict):
            result = {}
            for key, item in value.items():
                if key == "exchange_id" and isinstance(item, str):
                    item = ids.setdefault(item, f"<exchange:{len(ids) + 1}>")
                elif key == "duration_ms":
                    item = 0
                elif key == "started_at_utc":
                    item = "<time>"
                result[key] = walk(item)
            return result
        if isinstance(value, list):
            return [walk(item) for item in value]
        return value

    return walk(out)  # type: ignore[return-value]


def usage_cases() -> list[dict[str, Any]]:
    responses = [None, {}, {"usage": {"prompt_tokens": 5}}, {"usage": {"reasoning_tokens": 0}},
                 {"usage": {"reasoning_tokens": 3}}, {"usage_reported": True},
                 {"usage_reported": False, "usage": {"total_tokens": 9}},
                 {"cache_usage": {"cache_read_tokens": 0, "cache_write_tokens": 7}},
                 {"usage": {"prompt_tokens": "12"}}]
    return [{"response": item, "usage": plain(_usage_projection(response_fields(item)))}
            for item in responses]


async def main_async() -> dict[str, object]:
    results = []
    for case in cases():
        results.append({**case, "records": normalise(await run(case))})
    return {"cases": results, "usage": usage_cases()}


def main() -> None:
    document = asyncio.run(main_async())
    text = json.dumps(document, ensure_ascii=False, indent=1, allow_nan=False) + "\n"
    OUTPUT.write_text(text, encoding="utf-8", newline="\n")
    print(len(document["cases"]), "cases,", len(document["usage"]), "usage,", len(text), "bytes")


if __name__ == "__main__":
    main()
