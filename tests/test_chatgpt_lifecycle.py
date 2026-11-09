"""Lifecycle observations do not change owner tool admission or expose request content."""

from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from sylvia_gm.provider._base import ModelInfo
from sylvia_gm.provider._chatgpt import ChatGPTProvider
from sylvia_gm.provider._codex_rpc import CodexConnectionError
from sylvia_gm.provider._exceptions import (
    AuthenticationError,
    InvalidResponseError,
    ProviderConnectionError,
)
from sylvia_gm.provider._hooks import HookContext
from sylvia_gm.provider._lifecycle import LifecycleTiming


class Runtime:
    def __init__(self, *, invalid: bool = False) -> None:
        self.closed = False
        self.invalid = invalid
        self.observer = None
        self.account_reads: list[dict[str, Any]] = []

    def set_lifecycle_observer(self, observer: Any) -> None:
        self.observer = observer

    def start(self) -> None:
        for name in ("process_boot", "initialize"):
            start = time.perf_counter_ns()
            if self.observer is not None:
                self.observer(name, start, time.perf_counter_ns(), "completed")

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        if method == "account/read":
            self.account_reads.append(dict(params))
            return {"account": {"type": "chatgpt"}}
        if method == "thread/start":
            return {"thread": {"id": "private-thread"}, "model": "gpt-6.1-sol"}
        if method == "turn/start":
            return {"turn": {"id": "private-turn"}}
        return {}

    def next_event(self) -> dict[str, Any]:
        return {"method": "item/tool/call", "params": {
            "namespace": "host" if self.invalid else "sylvia", "tool": "inspect",
            "callId": "private-call", "arguments": {"query": "private-payload"},
        }}

    def close(self) -> None:
        self.closed = True


class Capture:
    def __init__(self) -> None:
        self.receipts: list[dict[str, Any]] = []

    async def on_request(self, context: HookContext) -> None:
        value = context.request_json or {}
        if value.get("method") == "sylvia/provider_lifecycle":
            self.receipts.append(value["params"])


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [False, True])
async def test_provider_records_separate_lifecycle_and_stops_before_tool(
    tmp_path: Path, invalid: bool,
) -> None:
    runtime = Runtime(invalid=invalid)
    provider = ChatGPTProvider(tmp_path, ModelInfo(
        id="chatgpt-subscription/gpt-6.1-sol", name="Sol", provider_name="ChatGPT",
        provider_key="chatgpt_subscription", provider_mode="subscription",
        transport="codex_app_server",
    ), connection_factory=lambda *args, **kwargs: runtime)
    capture = Capture()
    provider.hooks.register(capture)
    chunks = []
    try:
        async for chunk in provider.generate_stream(
            [{"role": "user", "content": "private-payload"}],
            [{"type": "function", "function": {"name": "inspect", "description": "Inspect",
              "parameters": {"type": "object", "properties": {}}}}],
        ):
            if chunk.tool_call_delta:
                assert runtime.closed
            chunks.append(chunk)
    except InvalidResponseError:
        assert invalid
    assert runtime.closed and provider._active is None
    assert len(capture.receipts) == 1
    receipt = capture.receipts[0]
    assert receipt["status"] == ("error" if invalid else "completed")
    spans = receipt["spans"]
    names = [span["name"] for span in spans]
    assert set(names) == {
        "runtime_start", "process_boot", "initialize", "account_read", "thread_start",
        "history_injection", "turn_start", "turn_stream", "shutdown",
    }
    for span in spans:
        assert 0 <= span["start_ns"] <= span["end_ns"] <= receipt["elapsed_ns"]
        assert span["duration_ns"] == span["end_ns"] - span["start_ns"]
    assert receipt["covered_ns"] + receipt["unclassified_ns"] == receipt["elapsed_ns"]
    assert receipt["clock"] == "perf_counter_ns"
    assert "private-" not in json.dumps(receipt)
    assert receipt["scope"] == "adapter_lifecycle_not_model_service_time"
    assert runtime.account_reads == [{"refreshToken": False}]


def test_lifecycle_uses_interval_union_and_bounds_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("sylvia_gm.provider._lifecycle.time.perf_counter_ns", lambda: 1000)
    timing = LifecycleTiming()
    timing.record("runtime_start", 1010, 1090, "completed")
    timing.record("process_boot", 1020, 1040, "completed", parent="runtime_start")
    timing.record("initialize", 1040, 1080, "completed", parent="runtime_start")
    timing.record("private-payload", 1010, 1090, "completed")
    monkeypatch.setattr("sylvia_gm.provider._lifecycle.time.perf_counter_ns", lambda: 1100)
    result = timing.snapshot("completed")
    assert result["elapsed_ns"] == 100
    assert result["covered_ns"] == 80
    assert result["unclassified_ns"] == 20
    assert len(result["spans"]) == 3


@pytest.mark.asyncio
async def test_cancelled_stream_records_terminal_observation_and_closes_child(
    tmp_path: Path,
) -> None:
    entered = threading.Event()

    class Waiting(Runtime):
        def next_event(self) -> None:
            entered.set()
            time.sleep(0.005)

    runtime = Waiting()
    provider = ChatGPTProvider(tmp_path, ModelInfo(
        id="chatgpt-subscription/gpt-6.1-sol", name="Sol", provider_name="ChatGPT",
        provider_key="chatgpt_subscription", provider_mode="subscription",
    ), connection_factory=lambda *args, **kwargs: runtime)
    capture = Capture()
    provider.hooks.register(capture)
    task = asyncio.create_task(provider.generate([]))
    while not entered.is_set():
        await asyncio.sleep(0.005)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime.closed and provider._active is None
    assert capture.receipts[0]["status"] == "cancelled"
    stream = next(s for s in capture.receipts[0]["spans"] if s["name"] == "turn_stream")
    assert stream["status"] == "cancelled"


@pytest.mark.asyncio
async def test_boot_failure_is_recorded_without_exception_text(tmp_path: Path) -> None:
    class Broken(Runtime):
        def start(self) -> None:
            start = time.perf_counter_ns()
            self.observer("process_boot", start, time.perf_counter_ns(), "error")
            raise CodexConnectionError("Safe existing connection failure")

    runtime = Broken()
    provider = ChatGPTProvider(tmp_path, ModelInfo(
        id="chatgpt-subscription/gpt-6.1-sol", name="Sol", provider_name="ChatGPT",
        provider_key="chatgpt_subscription", provider_mode="subscription",
    ), connection_factory=lambda *args, **kwargs: runtime)
    capture = Capture()
    provider.hooks.register(capture)
    with pytest.raises(ProviderConnectionError):
        await provider.generate([])
    assert runtime.closed
    receipt = capture.receipts[0]
    assert receipt["status"] == "error"
    assert {s["name"] for s in receipt["spans"]} == {
        "process_boot", "runtime_start", "shutdown",
    }
    assert "Safe existing" not in json.dumps(receipt)


@pytest.mark.asyncio
@pytest.mark.parametrize("account", [None, {"type": "apiKey"}])
async def test_account_status_still_rejects_missing_or_non_chatgpt_auth(
    tmp_path: Path, account: object,
) -> None:
    class WrongAccount(Runtime):
        def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
            assert method == "account/read", "No thread or turn may start before admission"
            assert params == {"refreshToken": False}
            return {"account": account}

    runtime = WrongAccount()
    provider = ChatGPTProvider(tmp_path, ModelInfo(
        id="chatgpt-subscription/gpt-6.1-sol", name="Sol", provider_name="ChatGPT",
        provider_key="chatgpt_subscription", provider_mode="subscription",
    ), connection_factory=lambda *args, **kwargs: runtime)
    with pytest.raises(AuthenticationError):
        await provider.generate([])
    assert runtime.closed and provider._active is None


@pytest.mark.asyncio
@pytest.mark.parametrize("response_kind", ["text", "tool"])
@pytest.mark.parametrize("close_after_terminal", [False, True])
async def test_explicit_stream_close_preserves_delivered_terminal_status(
    tmp_path: Path, response_kind: str, close_after_terminal: bool,
) -> None:
    class TextRuntime(Runtime):
        def __init__(self) -> None:
            super().__init__()
            self.events = [
                {"method": "item/agentMessage/delta", "params": {"delta": "Synthetic reply"}},
                {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
            ]

        def next_event(self) -> dict[str, Any]:
            return self.events.pop(0)

    runtime = TextRuntime() if response_kind == "text" else Runtime()
    provider = ChatGPTProvider(tmp_path, ModelInfo(
        id="chatgpt-subscription/gpt-6.1-sol", name="Sol", provider_name="ChatGPT",
        provider_key="chatgpt_subscription", provider_mode="subscription",
    ), connection_factory=lambda *args, **kwargs: runtime)
    capture = Capture()
    provider.hooks.register(capture)
    stream = provider.generate_stream([], [{"type": "function", "function": {
        "name": "inspect", "description": "Inspect",
        "parameters": {"type": "object", "properties": {}},
    }}])
    first = await anext(stream)
    assert first.finish_reason is None
    if response_kind == "tool":
        assert first.tool_call_delta and runtime.closed
    if close_after_terminal:
        terminal = await anext(stream)
        assert terminal.finish_reason == ("stop" if response_kind == "text" else "tool_calls")
        assert runtime.closed
    await stream.aclose()
    assert runtime.closed and provider._active is None
    assert len(capture.receipts) == 1
    assert capture.receipts[0]["status"] == (
        "completed" if close_after_terminal else "cancelled"
    )
