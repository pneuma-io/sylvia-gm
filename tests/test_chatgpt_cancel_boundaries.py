"""Cancellation must collect this request's child, including queued startup and RPC work."""

from __future__ import annotations

import asyncio
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from sylvia_gm.provider._base import ModelInfo
from sylvia_gm.provider._chatgpt import ChatGPTProvider
from sylvia_gm.provider._codex_rpc import CodexConnection, CodexConnectionError
from sylvia_gm.provider._exceptions import ProviderConnectionError
from tests.test_chatgpt_lifecycle import Capture, Runtime


def provider_for(runtime: Any, tmp_path: Path) -> ChatGPTProvider:
    return ChatGPTProvider(tmp_path, ModelInfo(
        id="chatgpt-subscription/gpt-6.1-sol", name="Sol", provider_name="ChatGPT",
        provider_key="chatgpt_subscription", provider_mode="subscription",
    ), connection_factory=lambda *args, **kwargs: runtime)


class OwnedChild:
    """An in-memory process double; no OS process or credential store is opened."""

    stdin = stdout = None

    def __init__(self) -> None:
        self.returncode: int | None = None

    def poll(self) -> int | None:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = 1

    def wait(self, timeout: float) -> int | None:
        return self.returncode


class ProtocolConnection(CodexConnection):
    """Exercise the real startup/call/close locks against deterministic protocol replies."""

    _process: Any
    _call_lock: Any

    def __init__(self, home: Path, blocked_method: str | None = None) -> None:
        super().__init__(home)
        self.blocked_method = blocked_method
        self.blocked = threading.Event()
        self.timeouts = 0
        self.children: list[OwnedChild] = []
        self.methods: list[str] = []

    def _start_process(self) -> None:
        child = OwnedChild()
        self.children.append(child)
        self._process = child

    def _write(self, message: dict[str, Any]) -> None:
        method = message.get("method")
        assert isinstance(method, str)
        if "id" not in message:
            return
        self.methods.append(method)
        if method == self.blocked_method:
            self.blocked.set()
            return
        result: dict[str, Any] = {}
        if method == "account/read":
            result = {"account": {"type": "chatgpt"}}
        elif method == "thread/start":
            result = {"thread": {"id": "fixture-thread"}, "model": "gpt-6.1-sol"}
        elif method == "turn/start":
            result = {"turn": {"id": "fixture-turn"}}
        self._responses.put({"id": message["id"], "result": result})

    def call(self, method: str, params: dict[str, Any], *, timeout: float = 0.15) -> dict:
        try:
            return super().call(method, params, timeout=timeout)
        except CodexConnectionError as error:
            self.timeouts += "timed out" in str(error)
            raise


@pytest.mark.asyncio
@pytest.mark.parametrize("method", [
    "initialize", "account/read", "thread/start", "thread/inject_items", "turn/start",
])
async def test_cancel_interrupts_pending_rpc_without_waiting_for_its_timeout(
    tmp_path: Path, method: str,
) -> None:
    connection = ProtocolConnection(tmp_path, method)
    provider = provider_for(connection, tmp_path)
    capture = Capture()
    provider.hooks.register(capture)
    task = asyncio.create_task(provider.generate([]))
    try:
        assert await asyncio.to_thread(connection.blocked.wait, 2)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(task, 2)
        assert connection.timeouts == 0
        assert provider._active is None and connection._process is None
        assert all(child.poll() is not None for child in connection.children)
        assert capture.receipts[0]["status"] == "cancelled"
    finally:
        connection._responses.put(None)
        connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stop_method", ["cancel_task", "provider_close"])
async def test_cancel_before_start_lock_cannot_leave_a_late_child(
    tmp_path: Path, stop_method: str,
) -> None:
    entered, release, finished = (threading.Event() for _ in range(3))

    class StartupLock:
        def __init__(self) -> None:
            self.lock = threading.RLock()
            self.first = True

        def __enter__(self):
            if self.first:
                self.first = False
                entered.set()
                assert release.wait(2)
            return self.lock.__enter__()

        def __exit__(self, *args):
            return self.lock.__exit__(*args)

    class Delayed(ProtocolConnection):
        def start(self) -> None:
            try:
                super().start()
            finally:
                finished.set()

        def close(self) -> None:
            try:
                super().close()
            finally:
                release.set()

        def next_event(self, timeout: float = 0.25) -> dict[str, Any]:
            return {"method": "turn/completed", "params": {"turn": {"status": "completed"}}}

    connection = Delayed(tmp_path)
    connection._call_lock = StartupLock()
    provider = provider_for(connection, tmp_path)
    capture = Capture()
    provider.hooks.register(capture)
    task = asyncio.create_task(provider.generate([]))
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        if stop_method == "cancel_task":
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        else:
            assert await provider.close() is None
            with pytest.raises(asyncio.CancelledError):
                await task
        assert await asyncio.to_thread(finished.wait, 2)
        assert connection._process is None
        assert all(child.poll() is not None for child in connection.children)
        assert capture.receipts[0]["status"] == "cancelled"
    finally:
        release.set()
        await asyncio.to_thread(finished.wait, 2)
        connection.close()


@pytest.mark.asyncio
async def test_second_cancel_waits_for_owned_child_cleanup(tmp_path: Path) -> None:
    streaming, closing, release, collected = (threading.Event() for _ in range(4))

    class Closing(Runtime):
        def next_event(self) -> None:
            streaming.set()
            time.sleep(0.005)

        def close(self) -> None:
            closing.set()
            assert release.wait(2)
            super().close()
            collected.set()

    runtime = Closing()
    provider = provider_for(runtime, tmp_path)
    capture = Capture()
    provider.hooks.register(capture)
    task = asyncio.create_task(provider.generate([]))
    premature = False
    try:
        assert await asyncio.to_thread(streaming.wait, 2)
        task.cancel()
        assert await asyncio.to_thread(closing.wait, 2)
        task.cancel("fixture repeated stop")
        await asyncio.sleep(0)
        await asyncio.sleep(0)
        premature = task.done()
    finally:
        release.set()
        with pytest.raises(asyncio.CancelledError) as error:
            await task
        assert await asyncio.to_thread(collected.wait, 2)
    assert not premature
    assert runtime.closed and provider._active is None
    assert capture.receipts[0]["status"] == "cancelled"
    assert error.value.args == ("fixture repeated stop",)


def test_inference_stop_is_per_connection_and_ordinary_close_can_restart(tmp_path: Path) -> None:
    cancelled = ProtocolConnection(tmp_path / "cancelled")
    ordinary = ProtocolConnection(tmp_path / "ordinary")
    try:
        cancelled.start()
        cancelled.request_stop()
        cancelled.close()
        with pytest.raises(CodexConnectionError, match="cancelled"):
            cancelled.start()
        with pytest.raises(CodexConnectionError, match="cancelled"):
            cancelled.call("account/read", {})
        assert len(cancelled.children) == 1
        ordinary.start()
        ordinary.close()
        ordinary.start()
        assert len(ordinary.children) == 2
        assert ordinary.call("account/read", {}) == {"account": {"type": "chatgpt"}}
    finally:
        cancelled.close()
        ordinary.close()
    assert all(child.poll() is not None for c in (cancelled, ordinary) for child in c.children)


@pytest.mark.asyncio
async def test_provider_close_does_not_forward_a_queued_tool_call(tmp_path: Path) -> None:
    observed, release = asyncio.Event(), asyncio.Event()

    class ToolConnection(ProtocolConnection):
        def _write(self, message: dict[str, Any]) -> None:
            super()._write(message)
            if message.get("method") == "turn/start":
                self._notifications.put({"method": "item/tool/call", "params": {
                    "namespace": "sylvia", "tool": "inspect", "callId": "fixture-call",
                    "arguments": {},
                }})

    class AuditGate:
        async def on_request(self, context) -> None:
            if (context.request_json or {}).get("method") == "item/tool/call":
                observed.set()
                await release.wait()

    connection = ToolConnection(tmp_path)
    provider = provider_for(connection, tmp_path)
    provider.hooks.register(AuditGate())
    capture = Capture()
    provider.hooks.register(capture)
    before = provider._last_response
    observed_chunks = []

    async def consume() -> None:
        async for chunk in provider.generate_stream([], [{"type": "function", "function": {
            "name": "inspect", "description": "Inspect",
            "parameters": {"type": "object", "properties": {}},
        }}]):
            observed_chunks.append(chunk)

    task = asyncio.create_task(consume())
    try:
        await asyncio.wait_for(observed.wait(), 2)
        assert await provider.close() is None
        release.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not any(chunk.tool_call_delta for chunk in observed_chunks)
        assert provider._last_response is before
        assert provider._active is None and connection._process is None
        assert all(child.poll() is not None for child in connection.children)
        assert capture.receipts[0]["status"] == "cancelled"
    finally:
        release.set()
        if not task.done():
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
        connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", [
    "initialize", "account/read", "thread/start", "thread/inject_items", "turn/start",
])
async def test_direct_close_is_cancellation_not_connection_failure(
    tmp_path: Path, method: str,
) -> None:
    connection = ProtocolConnection(tmp_path, method)
    provider = provider_for(connection, tmp_path)
    capture = Capture()
    provider.hooks.register(capture)
    before = provider._last_response
    task = asyncio.create_task(provider.generate([]))
    try:
        assert await asyncio.to_thread(connection.blocked.wait, 2)
        assert await provider.close() is None
        with pytest.raises(asyncio.CancelledError):
            await task
        assert task.cancelled()
        assert connection.timeouts == 0
        assert provider._last_response is before
        assert provider._active is None and connection._process is None
        assert all(child.poll() is not None for child in connection.children)
        assert len(capture.receipts) == 1
        assert capture.receipts[0]["status"] == "cancelled"
    finally:
        connection._responses.put(None)
        connection.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["initialize", "account/read", "stream"])
async def test_unintentional_connection_failure_stays_an_error(
    tmp_path: Path, stage: str,
) -> None:
    class Failed(ProtocolConnection):
        def _write(self, message: dict[str, Any]) -> None:
            if message.get("method") == stage:
                self._responses.put(None)
            else:
                super()._write(message)

        def next_event(self, timeout: float = 0.25) -> None:
            raise CodexConnectionError("Safe fixture connection failure")

    connection = Failed(tmp_path)
    provider = provider_for(connection, tmp_path)
    capture = Capture()
    provider.hooks.register(capture)
    with pytest.raises(ProviderConnectionError):
        await provider.generate([])
    assert not connection._stop_requested.is_set()
    assert provider._active is None and connection._process is None
    assert all(child.poll() is not None for child in connection.children)
    assert capture.receipts[0]["status"] == "error"


@pytest.mark.asyncio
async def test_direct_close_collects_child_spawned_during_stop_race(tmp_path: Path) -> None:
    entered, release, stopped = (threading.Event() for _ in range(3))

    class DuringStartup(ProtocolConnection):
        def _start_process(self) -> None:
            entered.set()
            assert release.wait(2)
            super()._start_process()

        def request_stop(self) -> None:
            super().request_stop()
            stopped.set()

    connection = DuringStartup(tmp_path)
    provider = provider_for(connection, tmp_path)
    capture = Capture()
    provider.hooks.register(capture)
    generation = asyncio.create_task(provider.generate([]))
    closing = None
    try:
        assert await asyncio.to_thread(entered.wait, 2)
        closing = asyncio.create_task(provider.close())
        assert await asyncio.to_thread(stopped.wait, 2)
        release.set()
        assert await closing is None
        with pytest.raises(asyncio.CancelledError):
            await generation
        assert len(connection.children) == 1
        assert all(child.poll() is not None for child in connection.children)
        assert connection._process is None and provider._active is None
        assert capture.receipts[0]["status"] == "cancelled"
    finally:
        release.set()
        connection._responses.put(None)
        if closing is not None:
            await closing
        connection.close()
