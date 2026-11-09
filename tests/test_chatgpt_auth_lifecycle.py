"""Offline adapter behavior at simulated auth rejection; no real token refresh is claimed."""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from sylvia_gm.provider._chatgpt import ChatGPTProvider
from sylvia_gm.provider._exceptions import (
    AuthenticationError,
    ProviderConnectionError,
    ProviderError,
)
from tests.test_chatgpt_cancel_boundaries import ProtocolConnection, provider_for
from tests.test_chatgpt_lifecycle import Capture, Runtime


class SessionRuntime(Runtime):
    def __init__(self, *, revoked: bool = False, rejected_turn: bool = False) -> None:
        super().__init__()
        self.revoked = revoked
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.events = [{"method": "item/agentMessage/delta", "params": {
            "delta": "Uncommitted fixture fragment" if rejected_turn else "Fixture reply",
        }}, {"method": "turn/completed", "params": {"turn": {
            "status": "failed" if rejected_turn else "completed",
            "error": {"codexErrorInfo": "other", "message": "HTTP 401 fixture-expired-secret"}
            if rejected_turn else None,
        }}}]

    def call(self, method: str, params: dict[str, Any]) -> dict[str, Any]:
        self.calls.append((method, copy.deepcopy(params)))
        if method == "account/read" and self.revoked:
            self.account_reads.append(dict(params))
            return {"account": None}
        return super().call(method, params)

    def next_event(self) -> dict[str, Any]:
        return self.events.pop(0)


def session_provider(tmp_path: Path, runtimes: list[Runtime]) -> tuple[ChatGPTProvider, list]:
    created = []

    def create(*args, **kwargs):
        runtime = runtimes[len(created)]
        created.append(runtime)
        return runtime

    provider = provider_for(runtimes[0], tmp_path)
    provider._connection_factory = create
    return provider, created


@pytest.mark.asyncio
async def test_account_revocation_is_rechecked_after_a_successful_response(tmp_path: Path) -> None:
    first, revoked, restored = SessionRuntime(), SessionRuntime(revoked=True), SessionRuntime()
    provider, created = session_provider(tmp_path, [first, revoked, restored])
    history = [{"role": "user", "content": "Fictional fixture request"}]
    original = copy.deepcopy(history)
    assert (await provider.generate(history)).content == "Fixture reply"
    with pytest.raises(AuthenticationError):
        await provider.generate(history)
    assert len(created) == 2 and revoked.closed
    assert [name for name, _ in revoked.calls] == ["account/read"]
    assert (await provider.generate(history)).content == "Fixture reply"
    assert len(created) == 3 and restored.closed and history == original
    assert all(r.account_reads == [{"refreshToken": False}] for r in created)


@pytest.mark.asyncio
async def test_turn_auth_rejection_keeps_history_and_requires_explicit_retry(
    tmp_path: Path,
) -> None:
    rejected, restored = SessionRuntime(rejected_turn=True), SessionRuntime()
    provider, created = session_provider(tmp_path, [rejected, restored])
    capture = Capture()
    provider.hooks.register(capture)
    history = [{"role": "user", "content": "Fictional fixture request"}]
    original, previous_response = copy.deepcopy(history), provider._last_response
    with pytest.raises(ProviderError) as error:
        await provider.generate(history, reasoning_effort="xhigh")
    assert "fixture-expired-secret" not in str(error.value)
    assert len(created) == 1 and rejected.closed and provider._active is None
    assert provider._last_response is previous_response and history == original
    result = await provider.generate(history, reasoning_effort="xhigh")
    assert result.content == "Fixture reply" and not result.tool_calls
    assert len(created) == 2 and restored.closed and history == original
    injected = next(p for name, p in restored.calls if name == "thread/inject_items")
    assert "Uncommitted fixture fragment" not in json.dumps(injected)
    assert [r["status"] for r in capture.receipts] == ["error", "completed"]
    for runtime in created:
        assert runtime.account_reads == [{"refreshToken": False}]
        assert next(p for name, p in runtime.calls if name == "turn/start")["effort"] == "xhigh"


@pytest.mark.asyncio
async def test_account_rpc_rejection_redacts_auth_detail_and_never_starts_turn(
    tmp_path: Path,
) -> None:
    class Rejected(ProtocolConnection):
        def _write(self, message: dict[str, Any]) -> None:
            if message.get("method") == "account/read":
                self.methods.append("account/read")
                self._responses.put({"id": message["id"], "error": {
                    "code": -32000, "message": "HTTP 403 fixture-revoked-secret",
                }})
            else:
                super()._write(message)

    connection = Rejected(tmp_path)
    provider = provider_for(connection, tmp_path)
    with pytest.raises(ProviderConnectionError) as error:
        await provider.generate([])
    assert "fixture-revoked-secret" not in str(error.value)
    assert connection.methods == ["initialize", "account/read"]
    assert connection._process is None and provider._active is None
    assert all(child.poll() is not None for child in connection.children)
