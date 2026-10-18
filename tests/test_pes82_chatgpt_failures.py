"""Subscription failures stop the owned runtime and never invent a completed tool result."""

from __future__ import annotations

import asyncio
import time
from pathlib import Path
from typing import Any

import pytest

from sylvia_gm.provider._base import ModelInfo
from sylvia_gm.provider._chatgpt import ChatGPTProvider
from sylvia_gm.provider._exceptions import (
    ContextCompactionRequiredError,
    InvalidResponseError,
    ModelNotFoundError,
    ProviderError,
    ProviderTimeoutError,
    RateLimitError,
)
from tests.test_alpha_f6e_owner_session_transport import _create_builder_session, _OwnerHarness


class SyntheticRuntime:
    def __init__(self, events: list[dict[str, Any]]) -> None:
        self.events = list(events)
        self.closed = False
        self.calls: list[str] = []

    def start(self) -> None:
        pass

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append(method)
        if method == "account/read":
            return {"account": {"type": "chatgpt"}}
        if method == "thread/start":
            return {"thread": {"id": "synthetic-thread"}, "model": "gpt-5.6-sol"}
        if method == "turn/start":
            return {"turn": {"id": "synthetic-turn"}}
        return {}

    def next_event(self) -> dict[str, Any] | None:
        if self.events:
            return self.events.pop(0)
        time.sleep(0.01)
        return None

    def close(self) -> None:
        self.closed = True


def provider_for(runtime: SyntheticRuntime, path: Path) -> ChatGPTProvider:
    return ChatGPTProvider(
        path,
        ModelInfo(
            id="chatgpt-subscription/gpt-5.6-sol",
            name="Sol",
            provider_name="ChatGPT",
            provider_key="chatgpt_subscription",
            provider_mode="subscription",
            transport="codex_app_server",
        ),
        connection_factory=lambda *args, **kwargs: runtime,
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event", "error"),
    [
        ({"method": "model/rerouted", "params": {}}, ModelNotFoundError),
        (
            {"method": "item/started", "params": {"item": {"type": "contextCompaction"}}},
            ContextCompactionRequiredError,
        ),
        (
            {
                "method": "turn/completed",
                "params": {
                    "turn": {"status": "failed", "error": {"codexErrorInfo": "usageLimitExceeded"}}
                },
            },
            RateLimitError,
        ),
        (
            {
                "method": "item/tool/call",
                "params": {"namespace": "host", "tool": "shell", "callId": "unavailable"},
            },
            InvalidResponseError,
        ),
    ],
)
async def test_failure_closes_runtime_without_tool_result(
    tmp_path: Path,
    event: dict[str, Any],
    error: type[Exception],
) -> None:
    runtime = SyntheticRuntime([event])
    provider = provider_for(runtime, tmp_path)
    with pytest.raises(error):
        await provider.generate([{"role": "user", "content": "Synthetic request"}])
    assert runtime.closed
    assert provider._active is None
    assert not provider._last_response.tool_calls


@pytest.mark.asyncio
async def test_unexplained_subscription_failure_does_not_invent_connection_cause(tmp_path):
    runtime = SyntheticRuntime([{
        "method": "turn/completed", "params": {"turn": {
            "status": "failed", "error": {"message": "PRIVATE_TRANSPORT_DETAIL"},
        }},
    }])
    with pytest.raises(ProviderError) as caught:
        await provider_for(runtime, tmp_path).generate([{"role": "user", "content": "Test"}])
    assert type(caught.value) is ProviderError
    assert "PRIVATE_TRANSPORT_DETAIL" not in str(caught.value)
    assert runtime.closed


@pytest.mark.asyncio
async def test_stop_closes_runtime_after_partial_text(tmp_path: Path) -> None:
    runtime = SyntheticRuntime(
        [{"method": "item/agentMessage/delta", "params": {"delta": "Partial"}}]
    )
    provider = provider_for(runtime, tmp_path)
    task = asyncio.create_task(
        provider.generate([{"role": "user", "content": "Synthetic request"}])
    )
    for _ in range(100):
        if not runtime.events:
            break
        await asyncio.sleep(0.01)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert runtime.closed
    assert provider._active is None


@pytest.mark.asyncio
async def test_stalled_runtime_times_out(tmp_path: Path) -> None:
    runtime = SyntheticRuntime([])
    with pytest.raises(ProviderTimeoutError):
        await provider_for(runtime, tmp_path).generate([], timeout=0.02)
    assert runtime.closed


@pytest.mark.asyncio
async def test_model_capacity_rejection_explains_recovery_without_raw_details(tmp_path):
    runtime = SyntheticRuntime([{
        "method": "turn/completed",
        "params": {"turn": {"status": "failed", "error": {
            "codexErrorInfo": "serverOverloaded",
            "message": "PRIVATE_RUNTIME_DETAIL",
        }}},
    }])
    provider = provider_for(runtime, tmp_path)
    with pytest.raises(ProviderError) as caught:
        await provider.generate([{"role": "user", "content": "Continue the draft."}])
    assert "capacity" in str(caught.value).lower()
    assert "PRIVATE_RUNTIME_DETAIL" not in str(caught.value)
    assert runtime.closed
    assert provider._active is None


def test_builder_capacity_notice_survives_transport_and_allows_manual_continuation(tmp_path):
    runtime = SyntheticRuntime([{
        "method": "turn/completed",
        "params": {"turn": {"status": "failed", "error": {
            "codexErrorInfo": "serverOverloaded", "message": "PRIVATE_RUNTIME_DETAIL",
        }}},
    }])
    harness = _OwnerHarness(tmp_path / "capacity.db", provider_for(runtime, tmp_path))
    try:
        _, session_id = _create_builder_session(harness, "capacity")
        params = {
            "session_id": session_id, "attempt_id": "capacity", "message_id": "capacity",
            "turn_id": "capacity-turn", "user_text": "Continue the saved draft.",
        }
        harness.send("capacity", {"method": "submit_session_input", "params": params})
        failed = harness.read()
        assert failed["ok"] is False
        assert failed["error"]["code"] == "provider_error"
        assert "capacity" in failed["error"]["message"].lower()
        assert "try again" in failed["error"]["message"].lower()
        assert "PRIVATE_RUNTIME_DETAIL" not in str(failed)
        assert runtime.calls.count("turn/start") == 1
        runtime.events.extend([
            {"method": "item/agentMessage/delta", "params": {"delta": "Ready to continue."}},
            {"method": "turn/completed", "params": {"turn": {"status": "completed"}}},
        ])
        resumed = harness.request("resume", "submit_session_input", {
            **params, "attempt_id": "resume", "message_id": "resume", "user_text": "Continue",
        })
        assert resumed["interruption"] is None
        assert runtime.calls.count("turn/start") == 2
        assert runtime.closed
    finally:
        harness.close()
