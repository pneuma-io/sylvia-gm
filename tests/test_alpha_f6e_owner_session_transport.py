"""Concurrent owner-process transport for canonical provider sessions."""

from __future__ import annotations

import asyncio
import hashlib
import json
import os
import re
import threading
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.config import provider_settings as provider_settings_module
from sylvia_gm.config._resolver import ConfigError
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.session import SessionMessageKind, SessionRecord
from sylvia_gm.infrastructure import builder_workspace_migration
from sylvia_gm.infrastructure.launch_handshake import LaunchHandshake
from sylvia_gm.infrastructure.sqlite_authority import SQLiteAuthorityStore
from sylvia_gm.interfaces import owner_process
from sylvia_gm.interfaces.local_owner import LocalOwnerSupervisor
from sylvia_gm.interfaces.owner_process import run
from sylvia_gm.provider._base import (
    FakeProvider,
    Provider,
    ProviderResponse,
    ProviderStreamChunk,
    ToolCall,
)


def _campaign_update_tool(tools: list[dict[str, Any]] | None) -> str:
    """Find the atomic completion tool among the GM's read and write tools."""
    return next(
        (str(tool["function"]["name"]) for tool in tools or []
         if str(tool["function"]["name"]).endswith("_update_campaign_file")),
        "",
    )


class _ScriptedProvider(Provider):
    def __init__(self, chunks: tuple[ProviderStreamChunk, ...]) -> None:
        super().__init__()
        self._chunks = chunks
        self.started = threading.Event()
        self.cancelled = threading.Event()

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        raise NotImplementedError

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        self.started.set()
        try:
            if kwargs.get("tool_choice") == "required" and (
                tool_name := _campaign_update_tool(tools)
            ):
                yield ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "settle-scripted-campaign-file",
                        "function": {
                            "name": tool_name,
                            "arguments": json.dumps(
                                {
                                    "state_change": "unchanged",
                                    "state_change_summary": "No durable state changes.",
                                    "revised_player_facing_response": [{
                                        "kind": "narration",
                                        "text": "".join(
                                            chunk.content or "" for chunk in self._chunks
                                        ),
                                    }],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
                return
            for chunk in self._chunks:
                await asyncio.sleep(0)
                yield chunk
        finally:
            self.cancelled.set()

    async def close(self) -> None:
        return None


class _BuilderToolProvider(_ScriptedProvider):
    def __init__(self) -> None:
        super().__init__(())
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        del stream, timeout, kwargs
        self.calls.append((messages.copy(), tools))
        if len(self.calls) == 1:
            return ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="builder-write-plan",
                        name="write_file",
                        arguments=json.dumps(
                            {
                                "path": "campaign-plan.md",
                                "content": "# Campaign Plan\n\nThe observatory waits.",
                            }
                        ),
                    )
                ]
            )
        return ProviderResponse(content="The campaign plan file is ready.")

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        del timeout, kwargs
        response = await self.generate(messages, tools)
        if response.tool_calls:
            call = response.tool_calls[0]
            yield ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": call.id,
                    "function": {"name": call.name, "arguments": call.arguments},
                },
                finish_reason="tool_calls",
            )
            return
        yield ProviderStreamChunk(content=response.content or "", finish_reason="stop")


class _BuilderResponseProvider(_ScriptedProvider):
    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        del messages, tools, stream, timeout, kwargs
        return ProviderResponse(content="BUILDER TALK MUST NOT PERSIST AFTER STOP")

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        response = await self.generate(messages, tools, timeout=timeout, **kwargs)
        yield ProviderStreamChunk(content=response.content or "", finish_reason="stop")


class _BuilderToolSurfaceProvider(_ScriptedProvider):
    def __init__(self) -> None:
        super().__init__(())
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        del timeout, kwargs
        self.calls.append((messages.copy(), tools))
        if len(self.calls) == 1:
            rendered = json.dumps(messages, sort_keys=True)
            match = re.search(
                r"\.builder-sources/batch-[0-9a-f]{24}/source-[0-9a-f]{24}\.txt",
                rendered,
            )
            assert match is not None
            yield ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "read-attached-source",
                    "function": {
                        "name": "read_file",
                        "arguments": json.dumps({"path": match.group(0)}),
                    },
                },
                finish_reason="tool_calls",
            )
            return
        yield ProviderStreamChunk(content="Source context received.", finish_reason="stop")


class _CampaignDiscoveryProvider(_ScriptedProvider):
    def __init__(self) -> None:
        super().__init__(())
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        del timeout, kwargs
        self.calls.append((messages.copy(), tools))
        if len(self.calls) == 1:
            yield ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "discover-rift",
                    "function": {
                        "name": "discover_campaign_content",
                        "arguments": json.dumps({"query": "Rift Awakening"}),
                    },
                },
                finish_reason="tool_calls",
            )
            return
        if len(self.calls) == 2:
            yield ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "prepare-rift",
                    "function": {
                        "name": "prepare_campaign_setup",
                        "arguments": json.dumps({"campaign_plan_id": "the-rift-awakening"}),
                    },
                },
                finish_reason="tool_calls",
            )
            return
        yield ProviderStreamChunk(
            content="The Rift Awakening is available. Use Configure Campaign to confirm setup.",
            finish_reason="stop",
        )


class _DMPlanProvider(_ScriptedProvider):
    def __init__(self) -> None:
        super().__init__(())
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []
        self._round = 0

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        self.calls.append((messages.copy(), tools))
        if kwargs.get("tool_choice") == "required" and (tool_name := _campaign_update_tool(tools)):
            payload: dict[str, Any] = {
                "state_change": "unchanged",
                "state_change_summary": "No durable state changes.",
                "revised_player_facing_response": [{
                    "kind": "narration",
                    "text": "The accepted campaign plan remains authoritative.",
                }],
            }
            if self._round == 0:
                payload.update(
                    {
                        "state_change": "changed",
                        "state_change_summary": "The campaign opening is recorded.",
                        "scenes": [
                            {
                                "summary": (
                                    "The accepted campaign plan has begun in current play."
                                )
                            }
                        ]
                    }
                )
            self._round += 1
            arguments = json.dumps(payload)
            yield ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": f"settle-dm-plan-campaign-file-{self._round}",
                    "function": {"name": tool_name, "arguments": arguments},
                },
                finish_reason="tool_calls",
            )
            return
        chunks = [
            ProviderStreamChunk(
                content="The accepted campaign plan remains authoritative.",
                finish_reason="stop",
            )
        ]
        self._round += 1
        for chunk in chunks:
            yield chunk


class _BlockingProvider(_ScriptedProvider):
    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        self.started.set()
        try:
            await asyncio.sleep(30)
            yield ProviderStreamChunk(content="MUST NOT ARRIVE")
        finally:
            self.cancelled.set()


class _ControlledBlockingProvider(_ScriptedProvider):
    """Block until owner cancellation or explicit test cleanup releases the call."""

    def __init__(self) -> None:
        super().__init__(())
        self.release = threading.Event()

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        del messages, tools, timeout, kwargs
        self.started.set()
        try:
            while not self.release.is_set():
                await asyncio.sleep(0.01)
        finally:
            self.cancelled.set()
        if False:  # pragma: no cover - keeps this an async generator
            yield ProviderStreamChunk(content="unreachable")


class _ControlledBlockingBuilderProvider(_ScriptedProvider):
    """Expose cancellation of the Builder's non-streaming provider boundary."""

    def __init__(self) -> None:
        super().__init__(())
        self.release = threading.Event()

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        del messages, tools, stream, timeout, kwargs
        self.started.set()
        try:
            while not self.release.is_set():
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        return ProviderResponse(content="BUILDER OUTPUT MUST NOT PERSIST")

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        del messages, tools, timeout, kwargs
        self.started.set()
        try:
            while not self.release.is_set():
                await asyncio.sleep(0.01)
        except asyncio.CancelledError:
            self.cancelled.set()
            raise
        yield ProviderStreamChunk(content="BUILDER OUTPUT MUST NOT PERSIST", finish_reason="stop")


class _FailingBuilderProvider(_ScriptedProvider):
    """Fail one Builder turn after provider admission."""

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        del messages, tools, timeout, kwargs
        self.started.set()
        raise RuntimeError("scripted provider failure")
        if False:  # pragma: no cover - keeps this an async generator
            yield ProviderStreamChunk(content="unreachable")


class _StreamingSteerBuilderProvider(_ScriptedProvider):
    def __init__(self) -> None:
        super().__init__(())
        self.calls: list[list[dict[str, Any]]] = []
        self.tool_names: list[set[str]] = []
        self.first_chunk = threading.Event()
        self.release_first = threading.Event()

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        raise AssertionError("Canonical Builder must use generate_stream")

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        del timeout, kwargs
        self.calls.append(messages.copy())
        self.tool_names.append({item["function"]["name"] for item in tools or []})
        if len(self.calls) == 1:
            self.first_chunk.set()
            yield ProviderStreamChunk(
                content="First ",
                reasoning_content="PRIVATE BUILDER REASONING",
            )
            while not self.release_first.is_set():
                await asyncio.sleep(0.01)
            yield ProviderStreamChunk(content="phase.", finish_reason="stop")
            return
        yield ProviderStreamChunk(content="Steering applied.", finish_reason="stop")


class _ToolBoundarySteerBuilderProvider(_ScriptedProvider):
    def __init__(self) -> None:
        super().__init__(())
        self.calls: list[list[dict[str, Any]]] = []
        self.first_chunk = threading.Event()
        self.release_tool_call = threading.Event()

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        del tools, timeout, kwargs
        self.calls.append(messages.copy())
        if len(self.calls) == 1:
            self.first_chunk.set()
            yield ProviderStreamChunk(content="Before tool.")
            while not self.release_tool_call.is_set():
                await asyncio.sleep(0.01)
            yield ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "builder-tool-boundary",
                    "function": {
                        "name": "write_file",
                        "arguments": json.dumps(
                            {
                                "path": "campaign-plan.md",
                                "content": "# Tool Boundary\n",
                            }
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
            return
        yield ProviderStreamChunk(content="Steered after tool.", finish_reason="stop")


class _TruncatedBuilderProvider(_ScriptedProvider):
    def __init__(self) -> None:
        super().__init__(())
        self.calls = 0

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        raise AssertionError("Canonical Builder must use generate_stream")

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        del messages, tools, timeout, kwargs
        self.calls += 1
        yield ProviderStreamChunk(
            content=(
                "TRUNCATED BUILDER PROSE MUST NOT PERSIST"
                if self.calls == 1
                else "AUTOMATIC RETRY MUST NOT RUN"
            ),
            reasoning_content="PRIVATE TRUNCATED REASONING",
            finish_reason="length" if self.calls == 1 else "stop",
        )


class _FailThenRetryProvider(_ScriptedProvider):
    def __init__(self) -> None:
        super().__init__(())
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        self.calls.append((messages.copy(), tools))
        if len(self.calls) == 1:
            yield ProviderStreamChunk(content="INCOMPLETE PROSE")
            raise RuntimeError("scripted provider disconnect")
        if kwargs.get("tool_choice") == "required" and (tool_name := _campaign_update_tool(tools)):
            yield ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "settle-retried-campaign-file",
                    "function": {
                        "name": tool_name,
                        "arguments": json.dumps(
                            {
                                "state_change": "unchanged",
                                "state_change_summary": "No durable state changes.",
                                "revised_player_facing_response": [{
                                    "kind": "narration", "text": "Recovered narration.",
                                }],
                            }
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
            return
        yield ProviderStreamChunk(content="Recovered narration.", finish_reason="stop")


class _FailThenBlockingRetryProvider(_ScriptedProvider):
    def __init__(self) -> None:
        super().__init__(())
        self.calls = 0
        self.retry_started = threading.Event()
        self.retry_cancelled = threading.Event()
        self.redirect_messages: list[dict[str, Any]] = []

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        del timeout, kwargs
        self.calls += 1
        if self.calls == 1:
            yield ProviderStreamChunk(content="DISCARD FAILED PROSE")
            raise RuntimeError("scripted provider disconnect")
        if self.calls > 2:
            self.redirect_messages = messages.copy()
            yield ProviderStreamChunk(
                tool_call_delta={"index": 0, "id": "redirect-response", "function": {
                    "name": _campaign_update_tool(tools),
                    "arguments": json.dumps({
                        "state_change": "unchanged",
                        "state_change_summary": "No durable state changes.",
                        "revised_player_facing_response": [
                            {"kind": "narration", "text": "Redirected response."},
                            {"kind": "ooc", "text": "Kept brief."},
                        ],
                    }),
                }}, finish_reason="tool_calls",
            )
            return
        self.retry_started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.retry_cancelled.set()
            raise
        if False:  # pragma: no cover - keeps this an async generator
            yield ProviderStreamChunk(content="unreachable")


class _HarnessResolver:
    def __init__(self, provider: Provider) -> None:
        self._provider = provider
        self.sessions: list[SessionRecord] = []

    def __call__(self, session: SessionRecord) -> Provider:
        self.sessions.append(session)
        return self._provider

    def model_choices(self) -> tuple[dict[str, object], ...]:
        return (
            {
                "provider_id": "test.transport",
                "provider_name": "Test Transport",
                "model_id": "test.transport/model0",
                "model_name": "Model 0",
                "context_window": 8192,
                "capability_registry_version": "sylvia.capability.registry.v1",
                "capability_tier_id": None,
                "capability_evidence_sha256": None,
            },
        )


class _OwnerHarness:
    def __init__(self, database: Path, provider: Provider | None) -> None:
        self.storage_root = database.parent
        owner_read_fd, client_write_fd = os.pipe()
        client_read_fd, owner_write_fd = os.pipe()
        self._owner_input = os.fdopen(owner_read_fd, "r", encoding="utf-8", newline="\n")
        self._client_output = os.fdopen(
            client_write_fd, "w", encoding="utf-8", newline="\n", buffering=1
        )
        self._client_input = os.fdopen(client_read_fd, "r", encoding="utf-8", newline="\n")
        self._owner_output = os.fdopen(
            owner_write_fd, "w", encoding="utf-8", newline="\n", buffering=1
        )
        self._pending_replies: dict[str, dict[str, Any]] = {}
        self.resolver = None if provider is None else _HarnessResolver(provider)
        kwargs = {} if self.resolver is None else {"provider_resolver": self.resolver}
        def run_owner() -> None:
            """An owner exit must close its reply pipe, including startup failure."""
            try:
                run(self._owner_input, self._owner_output, **kwargs)
            finally:
                self._owner_output.close()

        self._thread = threading.Thread(target=run_owner, daemon=True)
        self._thread.start()

        handshake = LaunchHandshake()
        credential = handshake.issue()
        self.send(
            "launch",
            {
                "type": "launch",
                "secret_hex": credential.secret_hex,
                "database_path": str(database),
                "owner_id": "owner-session-transport",
            },
            envelope=True,
        )
        ready = self.read()
        assert ready["ok"] is True
        handshake.verify(ready["response_hex"])

    def send(
        self,
        request_id: str,
        payload: dict[str, Any],
        *,
        envelope: bool = False,
    ) -> None:
        message = payload if envelope else {"id": request_id, **payload}
        self._client_output.write(json.dumps(message, separators=(",", ":")) + "\n")
        self._client_output.flush()

    def request(self, request_id: str, method: str, params: dict[str, Any] | None = None) -> Any:
        self.send(request_id, {"method": method, "params": params or {}})
        if request_id in self._pending_replies:
            reply = self._pending_replies.pop(request_id)
        else:
            while True:
                reply = self._read_stream()
                reply_id = str(reply["id"])
                if reply_id == request_id:
                    break
                self._pending_replies[reply_id] = reply
        assert reply["ok"] is True, reply
        return reply["result"]

    def _read_stream(self) -> dict[str, Any]:
        line = self._client_input.readline()
        assert line, "owner transport closed before replying"
        value = json.loads(line)
        assert isinstance(value, dict)
        return value

    def read(self) -> dict[str, Any]:
        if self._pending_replies:
            _request_id, reply = self._pending_replies.popitem()
            return reply
        return self._read_stream()

    def close(self) -> None:
        if self._thread.is_alive():
            self.send("shutdown", {"method": "shutdown", "params": {}})
            while True:
                reply = self.read()
                if reply.get("id") == "shutdown":
                    break
        self._thread.join(timeout=10)
        assert not self._thread.is_alive()
        for stream in (
            self._client_output,
            self._client_input,
            self._owner_input,
            self._owner_output,
        ):
            stream.close()


def test_display_history_transport_returns_pages_without_full_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "history.sqlite3"
    with AuthorityOwner.open(database, "fixture") as owner:
        owner.create_builder_chat(provider_id="test", model_id="test", session_id="paged")
        for index in range(60):
            owner.append_agent_message(
                session_id="paged", message_id=f"input-{index}", kind=SessionMessageKind.USER,
                content={"text": f"Synthetic saved message {index}"},
            )
    harness = _OwnerHarness(database, None)
    try:
        def reject_full_context(*args, **kwargs):
            raise AssertionError("Display paging must not read full agent context")

        monkeypatch.setattr(AuthorityOwner, "session_context", reject_full_context)
        page = harness.request("recent", "session_history_page", {
            "session_id": "paged", "limit": 10,
        })
        assert len(page["messages"]) == 10
        assert page["messages"][-1]["message_id"] == "input-59"
        assert page["latest_user_sequence"] == page["latest_sequence"]
        older = harness.request("older", "session_history_page", {
            "session_id": "paged", "limit": 10,
            "before_sequence": page["next_before_sequence"],
        })
        assert older["messages"][-1]["message_id"] == "input-49"
        assert older["session"]["session_id"] == "paged"
    finally:
        harness.close()


def test_slow_display_read_keeps_owner_navigation_available(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A held projection must not stop the real pipe loop from serving the sidebar."""
    started = threading.Event()
    release = threading.Event()

    def held_projection(self: AuthorityOwner, campaign_id: str) -> tuple[()]:
        del self, campaign_id
        started.set()
        assert release.wait(5)
        return ()

    monkeypatch.setattr(AuthorityOwner, "player_projection", held_projection)
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", None)
    deadline = threading.Timer(2, release.set)
    try:
        deadline.start()
        harness.send("slow-display", {"method": "player_projection", "params": {
            "campaign_id": "synthetic-display", "load_id": "old-selection",
        }})
        assert started.wait(3)
        assert harness.request("sidebar", "campaign_index") == []
        assert not release.is_set(), "sidebar waited behind the held display read"
    finally:
        release.set()
        deadline.cancel()
        harness.close()


def test_display_cancel_skips_queued_and_late_reads_but_finishes_admitted_reads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    entered = threading.Semaphore(0)
    release = threading.Event()
    calls: list[str] = []

    def projection(self: AuthorityOwner, campaign_id: str) -> tuple[()]:
        del self
        calls.append(campaign_id)
        if campaign_id.startswith("held"):
            entered.release()
            assert release.wait(5)
        return ()

    monkeypatch.setattr(AuthorityOwner, "player_projection", projection)
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", None)
    try:
        for name in ("held-one", "held-two", "abandoned"):
            harness.send(name, {"method": "player_projection", "params": {
                "campaign_id": name, "load_id": "old-selection",
            }})
        assert entered.acquire(timeout=3) and entered.acquire(timeout=3)
        harness.request("cancel", "cancel_display_load", {"load_id": "old-selection"})
        harness.send("late-old", {"method": "player_projection", "params": {
            "campaign_id": "late-old", "load_id": "old-selection",
        }})
        release.set()
        assert harness.request("new", "player_projection", {
            "campaign_id": "new", "load_id": "new-selection",
        }) == []
        replies = {reply["id"]: reply for reply in (harness.read() for _ in range(4))}
        for name in ("abandoned", "late-old"):
            assert replies[name]["error"]["code"] == "display_load_cancelled"
        assert replies["held-one"]["ok"] and replies["held-two"]["ok"]
        assert set(calls) == {"held-one", "held-two", "new"}
    finally:
        release.set()
        harness.close()


def test_fresh_owner_preinstalls_the_bundled_srd_once(tmp_path: Path) -> None:
    """A fresh installation can select the SRD without an import or campaign-start side effect."""

    database = tmp_path / "fresh-owner.db"
    first = _OwnerHarness(database, None)
    try:
        candidates = first.request("fresh-candidates", "package_candidates")
        package_ids = {
            entry["package_id"]
            for candidate in candidates
            for entry in candidate["package_lock"]["entries"]
        }
        assert "sylvia.srd-lite.bg3" in package_ids
    finally:
        first.close()

    reopened = _OwnerHarness(database, None)
    try:
        candidates = reopened.request("reopened-candidates", "package_candidates")
        srd_candidates = [
            candidate
            for candidate in candidates
            if any(
                entry["package_id"] == "sylvia.srd-lite.bg3"
                for entry in candidate["package_lock"]["entries"]
            )
        ]
        assert len(srd_candidates) == 1
    finally:
        reopened.close()


def _create_campaign(harness: _OwnerHarness, suffix: str) -> tuple[str, str, str]:
    builder_turn_id = f"turn-builder-{suffix}"
    confirmation = harness.request(
        f"confirm-{suffix}",
        "confirm_new_campaign",
        {
            "operation_id": f"confirm-{suffix}",
            "display_name": "Transport Test",
            "candidate_id": None,
            "turn_id": builder_turn_id,
            "provider_id": "test.transport",
            "model_id": "test.transport/model0",
        },
    )
    campaign_id = confirmation["campaign_id"]
    harness.request(
        f"begin-{suffix}",
        "begin_campaign_construction",
        {
            "operation_id": f"begin-{suffix}",
            "campaign_id": campaign_id,
            "expected_revision": confirmation["resulting_revision"],
            "lane": "customize",
            "canonical_input": {"test_fixture": suffix},
        },
    )
    plan_path = harness.storage_root / "campaigns" / campaign_id / "campaign-plan.md"
    plan_path.parent.mkdir(parents=True, exist_ok=True)
    plan_path.write_text(f"# Transport Plan {suffix}\n", encoding="utf-8")
    finalized = harness.request(
        f"finalize-{suffix}",
        "finalize_campaign_construction",
        {
            "campaign_id": campaign_id,
            "dm_provider_id": "test.transport",
            "dm_model_id": "test.transport/model0",
        },
    )
    session_id = finalized["handoff"]["dm_session_id"]
    turn_id = finalized["dm_turn_id"]
    return campaign_id, turn_id, session_id


def _create_builder_session(harness: _OwnerHarness, suffix: str) -> tuple[str, str]:
    confirmation = harness.request(
        f"confirm-builder-{suffix}",
        "confirm_new_campaign",
        {
            "operation_id": f"confirm-builder-{suffix}",
            "display_name": "Builder Lifecycle Test",
            "candidate_id": None,
            "turn_id": f"turn-builder-{suffix}",
            "provider_id": "test.transport",
            "model_id": "test.transport/model0",
        },
    )
    return confirmation["campaign_id"], confirmation["builder_session_id"]


def test_testing_startup_enables_current_process_full_audit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SYLVIA_GM_TEST_FULL_AUDIT", "1")
    harness = _OwnerHarness(tmp_path / "startup-full-audit.db", None)
    try:
        assert harness.request("audit-status", "admin_full_audit_status") == {
            "enabled": True,
            "trace_count": 0,
            "safe_mechanics_diagnostics": [],
        }
    finally:
        harness.close()


@pytest.mark.parametrize("purpose", ("player_character", "companion"))
def test_owner_transport_opens_independent_participant_builder(
    tmp_path: Path, purpose: str
) -> None:
    harness = _OwnerHarness(tmp_path / "owner-subflow.sqlite3", provider=None)
    try:
        draft = harness.request(
            "create-subflow-draft",
            "create_guided_campaign_draft",
            {
                "operation_id": "transport-subflow-draft",
                "display_name": "Transport Subflow",
                "provider_id": "unconfigured.local",
                "model_id": "unconfigured.model",
            },
        )
        revision = int(draft["construction_revision"])
        for step, visible, answer in (
            ("ruleset", "D&D SRD", {"candidate_id": "sylvia.srd-lite.bg3"}),
            ("setting", "Severed Lands", {"setting_id": "pneuma.severed-lands"}),
            ("plan", "The Rift Awakening", {"plan_id": "the-rift-awakening"}),
            ("name", "Transport Subflow", {"display_name": "Transport Subflow"}),
        ):
            advanced = harness.request(
                f"answer-subflow-{step}",
                "advance_guided_campaign_draft",
                {
                    "operation_id": f"transport-subflow-{step}",
                    "campaign_id": draft["campaign_id"],
                    "builder_session_id": draft["builder_session_id"],
                    "expected_construction_revision": revision,
                    "current_step": step,
                    "visible_answer": visible,
                    "answer": answer,
                },
            )
            revision = int(advanced["construction_revision"])

        subflow = harness.request(
            "open-player-character-subflow",
            "start_guided_creation_subflow",
            {
                "operation_id": f"transport-open-{purpose}-subflow",
                "campaign_id": draft["campaign_id"],
                "parent_session_id": draft["builder_session_id"],
                "expected_construction_revision": revision,
                "purpose": purpose,
                "visible_answer": f"Create New {purpose.replace('_', ' ').title()}",
                "trigger_message_id": None,
            },
        )

        assert subflow["campaign_id"] == draft["campaign_id"]
        assert subflow["parent_session_id"] == draft["builder_session_id"]
        assert subflow["purpose"] == purpose
        assert subflow["status"] == "independent"
        assert subflow["construction_revision"] == revision
        context = harness.request(
            "independent-character-context",
            "session_context",
            {
                "session_id": subflow["child_session_id"],
            },
        )
        assert context["session"]["campaign_id"] is None
        assert context["messages"][0]["content"]["purpose"] == purpose
    finally:
        harness.close()


def test_interview_builder_starts_from_application_context_without_player_speech(
    tmp_path: Path,
) -> None:
    provider = FakeProvider(
        responses=[ProviderResponse(content="What should this ruleset support?")]
    )
    harness = _OwnerHarness(tmp_path / "launch.sqlite3", provider)
    try:
        draft = harness.request(
            "new-interview",
            "create_guided_campaign_draft",
            {
                "operation_id": "new-interview",
                "display_name": "Ruleset campaign",
                "provider_id": "test.transport",
                "model_id": "test.transport/model0",
            },
        )
        parent_before = harness.request(
            "parent-before",
            "session_context",
            {
                "session_id": draft["builder_session_id"],
            },
        )
        launched = harness.request(
            "launch-ruleset",
            "start_guided_creation_subflow",
            {
                "operation_id": "launch-ruleset",
                "campaign_id": draft["campaign_id"],
                "parent_session_id": draft["builder_session_id"],
                "expected_construction_revision": 0,
                "purpose": "ruleset",
                "visible_answer": "New Ruleset",
            },
        )
        assert launched["status"] == "independent"
        session_id = launched["child_session_id"]
        assert (
            harness.request(
                "parent-after",
                "session_context",
                {
                    "session_id": draft["builder_session_id"],
                },
            )
            == parent_before
        )
        result = harness.request(
            "begin-builder",
            "submit_session_input",
            {
                "session_id": session_id,
                "attempt_id": "attempt-builder-start",
                "message_id": "builder-start",
                "user_text": "",
                "builder_start": True,
            },
        )
        assert result["interruption"] is None
        assert len(provider.calls) == 1
        messages = provider.calls[0]["messages"]
        # Messages transports require a turn; only the labelled app signal occupies it.
        user_messages = [message for message in messages if message["role"] == "user"]
        assert len(user_messages) == 1
        assert user_messages[0]["content"].startswith(
            "SYLVIA_GM_BUILDER_START (application-owned start signal, not player speech):"
        )
        rendered = json.dumps(messages)
        assert "New Ruleset" in rendered
        assert "builder_launch_context" in rendered
        assert "No return handshake is required" in rendered
        offered = {tool["function"]["name"] for tool in provider.calls[0]["tools"]}
        assert "load_builder_skill" in offered
        assert "complete_creation_subflow" not in offered
        context = harness.request("started-context", "session_context", {"session_id": session_id})
        assert not any(message["kind"] == "user" for message in context["messages"])
        assert "SYLVIA_GM_BUILDER_START" not in json.dumps(context["messages"])
        talk = [message for message in context["messages"] if message["kind"] == "talk"]
        assert talk[-1]["content"]["text"] == "What should this ruleset support?"
        harness.send(
            "duplicate-start",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "attempt_id": "another-attempt",
                    "message_id": "another-start",
                    "user_text": "",
                    "builder_start": True,
                },
            },
        )
        rejected = harness.read()
        assert rejected["ok"] is False
        assert len(provider.calls) == 1
    finally:
        harness.close()


def test_character_builder_reads_original_sources_through_owner_transport(tmp_path: Path) -> None:
    class ReferenceProvider(FakeProvider):
        async def generate_stream(self, messages, tools=None, **_kwargs):
            response = await self.generate(messages, tools)
            if response.tool_calls:
                for index, call in enumerate(response.tool_calls):
                    yield ProviderStreamChunk(
                        tool_call_delta={
                            "index": index,
                            "id": call.id,
                            "function": {"name": call.name, "arguments": call.arguments},
                        }
                    )
                yield ProviderStreamChunk(finish_reason="tool_calls")
            else:
                yield ProviderStreamChunk(content=response.content or "", finish_reason="stop")

    provider = ReferenceProvider(
        responses=[
            ProviderResponse(
                tool_calls=[
                    ToolCall(
                        id="read-original-plan",
                        name="inspect_builder_references",
                        arguments=json.dumps(
                            {"action": "read", "path": "campaign-plan/plan.md", "limit": 1500}
                        ),
                    )
                ]
            ),
            ProviderResponse(
                content="What kind of character would you like to bring into this setting?"
            ),
        ]
    )
    harness = _OwnerHarness(tmp_path / "owner-character-sources.db", provider)
    try:
        draft = harness.request(
            "source-draft",
            "create_guided_campaign_draft",
            {
                "operation_id": "source-draft",
                "display_name": "Character sources",
                "provider_id": "test.transport",
                "model_id": "test.transport/model0",
            },
        )
        for revision, (step, answer) in enumerate(
            (
                ("ruleset", {"candidate_id": "sylvia.srd-lite.bg3"}),
                ("setting", {"setting_id": "pneuma.severed-lands"}),
                ("plan", {"plan_id": "the-rift-awakening"}),
                ("name", {"display_name": "Character sources"}),
            )
        ):
            harness.request(
                f"select-source-{step}",
                "advance_guided_campaign_draft",
                {
                    "operation_id": f"select-source-{step}",
                    "campaign_id": draft["campaign_id"],
                    "builder_session_id": draft["builder_session_id"],
                    "expected_construction_revision": revision,
                    "current_step": step,
                    "visible_answer": step,
                    "answer": answer,
                },
            )
        launched = harness.request(
            "launch-character-source",
            "start_guided_creation_subflow",
            {
                "operation_id": "launch-character-source",
                "campaign_id": draft["campaign_id"],
                "parent_session_id": draft["builder_session_id"],
                "expected_construction_revision": 4,
                "purpose": "player_character",
                "visible_answer": "New Character",
            },
        )
        harness.request(
            "reselect-after-launch",
            "advance_guided_campaign_draft",
            {
                "operation_id": "reselect-after-launch",
                "campaign_id": draft["campaign_id"],
                "builder_session_id": draft["builder_session_id"],
                "expected_construction_revision": 4,
                "current_step": "ruleset",
                "visible_answer": "No Ruleset",
                "answer": {"candidate_id": None},
            },
        )
        result = harness.request(
            "read-builder-launch",
            "submit_session_input",
            {
                "session_id": launched["child_session_id"],
                "attempt_id": "attempt-read-launch",
                "message_id": "read-launch",
                "user_text": "",
                "builder_start": True,
            },
        )
        assert result["interruption"] is None
        assert len(provider.calls) == 2
        user_messages = [item for item in provider.calls[0]["messages"] if item["role"] == "user"]
        assert len(user_messages) == 1
        assert user_messages[0]["content"].startswith(
            "SYLVIA_GM_BUILDER_START (application-owned start signal, not player speech):"
        )
        context = harness.request(
            "source-reader-context", "session_context", {"session_id": launched["child_session_id"]}
        )
        assert not any(message["kind"] == "user" for message in context["messages"])
        assert "SYLVIA_GM_BUILDER_START" not in json.dumps(context["messages"])
        tool_result = next(
            item
            for item in provider.calls[1]["messages"]
            if item.get("tool_call_id") == "read-original-plan"
        )
        source = json.loads(tool_result["content"])
        assert source["success"] is True
        assert "Rift Awakening" in source["content"]
        assert source["path"] == "campaign-plan/plan.md"
        assert len(source["content"]) <= 1500
    finally:
        harness.close()


def test_general_builder_chat_is_durable_and_campaign_independent(tmp_path: Path) -> None:
    provider = _BuilderToolProvider()
    database = tmp_path / "authority.sqlite3"
    harness = _OwnerHarness(database, provider)
    try:
        chat = harness.request(
            "create-general-builder",
            "create_builder_chat",
            {
                "operation_id": "create-general-builder",
                "provider_id": "test.transport",
                "model_id": "test.transport/model0",
            },
        )
        session_id = chat["session"]["session_id"]
        assert chat["session"]["campaign_id"] is None
        assert chat["session"]["selected_skill"] is None
        assert harness.request("campaigns-remain-empty", "campaign_index") == []

        result = harness.request(
            "general-builder-message",
            "submit_session_input",
            {
                "session_id": session_id,
                "attempt_id": "attempt-general-builder",
                "message_id": "message-general-builder",
                "user_text": "Help me build a campaign plan for an observatory mystery.",
            },
        )
        assert result["session_id"] == session_id
        first_messages = provider.calls[0][0]
        rendered_prompt = json.dumps(first_messages, sort_keys=True)
        assert "BEGIN TRACKED BUILDER SKILL" not in rendered_prompt
        assert "tracked Builder capabilities available on demand" in rendered_prompt
        assert "Ruleset -> Campaign Setting -> Campaign Plan" in rendered_prompt
        offered_tools = {tool["function"]["name"] for tool in (provider.calls[0][1] or [])}
        assert "discover_campaign_content" in offered_tools
        assert "prepare_campaign_setup" in offered_tools
        assert "load_builder_skill" in offered_tools
        assert (
            sum(
                message.get("role") == "user"
                and message.get("content")
                == "Help me build a campaign plan for an observatory mystery."
                for message in first_messages
            )
            == 1
        )
        assert (tmp_path / "builder-chats" / session_id / "campaign-plan.md").read_text(
            encoding="utf-8"
        ) == "# Campaign Plan\n\nThe observatory waits."
        index = harness.request("builder-chat-index", "builder_chat_index")
        assert [entry["session"]["session_id"] for entry in index] == [session_id]
        assert index[0]["display_name"] == (
            "Help me build a campaign plan for an observatory mystery."
        )
        context = harness.request(
            "general-builder-context",
            "session_context",
            {"session_id": session_id},
        )
        assert [
            message["kind"] for message in context["messages"]
            if message["content"].get("kind") != "builder_attempt_terminal"
        ] == [
            "user",
            "evidence",
            "talk",
        ]
        assert harness.request("campaigns-still-empty", "campaign_index") == []
    finally:
        harness.close()

    reopened = _OwnerHarness(database, _ScriptedProvider(()))
    try:
        index = reopened.request("reopened-builder-chat-index", "builder_chat_index")
        assert [entry["session"]["session_id"] for entry in index] == [session_id]
        context = reopened.request(
            "reopened-general-builder-context",
            "session_context",
            {"session_id": session_id},
        )
        talk = [message for message in context["messages"] if message["kind"] == "talk"]
        assert talk[-1]["content"]["text"] == "The campaign plan file is ready."
        assert reopened.request("reopened-campaigns-empty", "campaign_index") == []
    finally:
        reopened.close()


def test_delete_builder_chat_removes_only_its_workspace_and_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    database = tmp_path / "delete-builder.sqlite3"
    with AuthorityOwner.open(database, "owner-delete-transport") as owner:
        deleted_chat = owner.create_builder_chat(
            provider_id="test.transport", model_id="test.transport/model0", session_id="delete-me"
        )
        kept_chat = owner.create_builder_chat(
            provider_id="test.transport", model_id="test.transport/model0", session_id="keep-me"
        )
        owner.append_agent_message(
            session_id="delete-me",
            message_id="unfinished",
            kind=owner_process.SessionMessageKind.USER,
            content={"text": "Synthetic unfinished work"},
        )
        session_id = deleted_chat.session.session_id
        workspace = tmp_path / "builder-chats" / session_id
        workspace.mkdir(parents=True)
        (workspace / "campaign-plan.md").write_text("Synthetic draft", encoding="utf-8")
        (workspace / "notes.md").write_text("Synthetic notes", encoding="utf-8")
        library = tmp_path / "reusable-library" / "synthetic-library-entry.md"
        library.parent.mkdir(parents=True)
        library.write_text("Reusable synthetic content", encoding="utf-8")
        dispatch = lambda target, confirmation: owner_process._dispatch(  # noqa: E731, SLF001
            None,  # type: ignore[arg-type]
            "delete_builder_chat",
            {"session_id": target, "confirmation": confirmation},
            application_owner=owner,
            owner_id="owner-delete-transport",
            provider_resolver=lambda _session: None,
            model_choices=lambda: (),
            session_loop=owner_process.CampaignSessionLoop(owner),
            storage_root=tmp_path,
        )
        with pytest.raises(CommandRejected) as inexact:
            dispatch(session_id, "delete")
        assert inexact.value.code == "builder_chat_delete_confirmation_inexact"
        assert workspace.is_dir()

        campaign = owner.create_guided_campaign_draft(
            operation_id="campaign-delete-rejection",
            display_name="Kept campaign",
            builder_session_id="campaign-interview",
            builder_provider_id="test.transport",
            builder_model_id="test.transport/model0",
        )
        campaign_workspace = tmp_path / "builder-chats" / campaign.builder_session_id
        campaign_workspace.mkdir(parents=True)
        (campaign_workspace / "unfinished.md").write_text("Keep", encoding="utf-8")
        with pytest.raises(CommandRejected):
            dispatch(
                campaign.builder_session_id,
                f"permanently delete {campaign.builder_session_id}",
            )
        assert (campaign_workspace / "unfinished.md").read_text(encoding="utf-8") == "Keep"

        real_rmtree = builder_workspace_migration.shutil.rmtree

        def fail_cleanup(path: Path) -> None:
            (path / "campaign-plan.md").unlink()
            raise OSError("synthetic cleanup failure")

        monkeypatch.setattr(builder_workspace_migration.shutil, "rmtree", fail_cleanup)
        with pytest.raises(CommandRejected) as incomplete:
            dispatch(session_id, f"permanently delete {session_id}")
        assert incomplete.value.code == "builder_workspace_cleanup_incomplete"
        retained = owner.session_context(session_id)
        assert retained.messages[0].content["text"] == "Synthetic unfinished work"
        assert (workspace / "notes.md").is_file()
        monkeypatch.setattr(builder_workspace_migration.shutil, "rmtree", real_rmtree)

        result = dispatch(session_id, f"permanently delete {session_id}")
        assert result.session.session_id == session_id
        assert not workspace.exists()
        assert library.read_text(encoding="utf-8") == "Reusable synthetic content"
        assert [chat.session.session_id for chat in owner.builder_chat_index()] == [
            kept_chat.session.session_id
        ]

    with AuthorityOwner.open(database, "owner-delete-transport-restarted") as reopened:
        assert [chat.session.session_id for chat in reopened.builder_chat_index()] == [
            kept_chat.session.session_id
        ]
        assert library.read_text(encoding="utf-8") == "Reusable synthetic content"


def test_new_campaign_builder_discovers_content_on_demand_without_starting_play(
    tmp_path: Path,
) -> None:
    provider = _CampaignDiscoveryProvider()
    harness = _OwnerHarness(tmp_path / "campaign-discovery.sqlite3", provider)
    try:
        chat = harness.request(
            "create-campaign-discovery",
            "create_builder_chat",
            {
                "operation_id": "create-campaign-discovery",
                "display_name": "New Campaign",
                "provider_id": "test.transport",
                "model_id": "test.transport/model0",
            },
        )
        session_id = chat["session"]["session_id"]
        harness.request(
            "submit-campaign-discovery",
            "submit_session_input",
            {
                "session_id": session_id,
                "attempt_id": "attempt-campaign-discovery",
                "message_id": "message-campaign-discovery",
                "user_text": "Find The Rift Awakening, but do not start playing.",
            },
        )

        first_system = next(
            message["content"] for message in provider.calls[0][0] if message["role"] == "system"
        )
        assert "Ruleset -> Campaign Setting -> Campaign Plan" in first_system
        assert "The Rift Awakening" not in first_system
        context = harness.request(
            "campaign-discovery-context",
            "session_context",
            {"session_id": session_id},
        )
        evidence = [
            message["content"] for message in context["messages"]
            if message["kind"] == "evidence" and "tool_name" in message["content"]
        ]
        assert [item["tool_name"] for item in evidence] == [
            "discover_campaign_content",
            "prepare_campaign_setup",
        ]
        assert evidence[0]["result"]["items"][0]["title"] == "The Rift Awakening"
        assert evidence[1]["result"]["status"] == "setup_ready"
        assert evidence[1]["result"]["setup_id"] == "the-rift-awakening"
        assert harness.request("campaign-discovery-index", "campaign_index") == []
    finally:
        harness.close()


def _configure_ollama_models(config_path: Path) -> None:
    for model_id in ("model0", "model1"):
        provider_settings_module.save_provider_settings(
            config_path,
            provider_id="ollama",
            preset_id="ollama",
            display_name="Ollama",
            base_url="http://127.0.0.1:11434/v1",
            credential="",
            model_id=model_id,
            model_name=model_id,
            context_window=8192,
            max_output_tokens=1024,
            transport="chat_completions",
            headers={},
        )


def test_owner_model_switch_current_once_global_and_restart_are_exact(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _configure_ollama_models(config_path)
    provider = _ScriptedProvider(
        (ProviderStreamChunk(content="One-turn model used.", finish_reason="stop"),)
    )
    database = tmp_path / "authority.sqlite3"
    harness = _OwnerHarness(database, provider)
    try:
        chat = harness.request(
            "model-switch-create",
            "create_builder_chat",
            {
                "operation_id": "model-switch-create",
                "provider_id": "ollama",
                "model_id": "ollama/model0",
            },
        )
        session_id = chat["session"]["session_id"]
        current = harness.request(
            "model-switch-current",
            "switch_session_model",
            {
                "session_id": session_id,
                "provider_id": "ollama",
                "model_id": "ollama/model1",
                "mode": "current",
            },
        )
        assert current["session"]["model_id"] == "ollama/model1"

        staged = harness.request(
            "model-switch-once",
            "switch_session_model",
            {
                "session_id": session_id,
                "provider_id": "ollama",
                "model_id": "ollama/model0",
                "mode": "once",
            },
        )
        assert staged["once"]["model_id"] == "ollama/model0"
        before_turn = harness.request(
            "model-switch-before-turn",
            "session_context",
            {"session_id": session_id},
        )
        assert before_turn["session"]["model_id"] == "ollama/model1"
        assert before_turn["model_state"]["once"]["model_id"] == "ollama/model0"

        harness.request(
            "model-switch-once-turn",
            "submit_session_input",
            {
                "session_id": session_id,
                "attempt_id": "model-switch-once-attempt",
                "message_id": "model-switch-once-message",
                "user_text": "Use the staged model for this turn.",
            },
        )
        assert harness.resolver is not None
        assert harness.resolver.sessions[-1].model_id == "ollama/model0"
        after_turn = harness.request(
            "model-switch-after-turn",
            "session_context",
            {"session_id": session_id},
        )
        assert after_turn["session"]["model_id"] == "ollama/model1"
        assert after_turn["model_state"]["once"] is None

        global_result = harness.request(
            "model-switch-global",
            "switch_session_model",
            {
                "session_id": session_id,
                "provider_id": "ollama",
                "model_id": "ollama/model0",
                "mode": "global",
            },
        )
        assert global_result["session"]["model_id"] == "ollama/model0"
        assert global_result["default_model"] == {
            "provider_id": "ollama",
            "model_id": "ollama/model0",
        }
    finally:
        harness.close()

    reopened = _OwnerHarness(database, _ScriptedProvider(()))
    try:
        restored = reopened.request(
            "model-switch-reopened",
            "session_context",
            {"session_id": session_id},
        )
        assert restored["session"]["model_id"] == "ollama/model0"
        assert restored["model_state"]["default_model"]["model_id"] == "ollama/model0"
        assert restored["model_state"]["once"] is None
    finally:
        reopened.close()


def test_owner_once_model_is_consumed_when_the_provider_fails(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _configure_ollama_models(config_path)
    provider = _FailingBuilderProvider(())
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", provider)
    try:
        chat = harness.request(
            "once-failure-create",
            "create_builder_chat",
            {
                "operation_id": "once-failure-create",
                "provider_id": "ollama",
                "model_id": "ollama/model0",
            },
        )
        session_id = chat["session"]["session_id"]
        harness.request(
            "once-failure-stage",
            "switch_session_model",
            {
                "session_id": session_id,
                "provider_id": "ollama",
                "model_id": "ollama/model1",
                "mode": "once",
            },
        )
        harness.send(
            "once-failure-submit",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "attempt_id": "once-failure-attempt",
                    "message_id": "once-failure-message",
                    "user_text": "This provider call will fail.",
                },
            },
        )
        reply = harness.read()
        assert reply["id"] == "once-failure-submit"
        assert reply["ok"] is False
        assert provider.started.wait(2)

        context = harness.request(
            "once-failure-context",
            "session_context",
            {"session_id": session_id},
        )
        assert context["session"]["model_id"] == "ollama/model0"
        assert context["model_state"]["once"] is None
        assert harness.resolver is not None
        assert harness.resolver.sessions[-1].model_id == "ollama/model1"
    finally:
        harness.close()


def test_owner_rejects_model_switch_during_active_builder_generation(tmp_path: Path) -> None:
    config_path = tmp_path / "config.yaml"
    _configure_ollama_models(config_path)
    provider = _ControlledBlockingBuilderProvider()
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", provider)
    try:
        chat = harness.request(
            "active-model-create",
            "create_builder_chat",
            {
                "operation_id": "active-model-create",
                "provider_id": "ollama",
                "model_id": "ollama/model0",
            },
        )
        session_id = chat["session"]["session_id"]
        harness.request(
            "active-model-stage-once",
            "switch_session_model",
            {
                "session_id": session_id,
                "provider_id": "ollama",
                "model_id": "ollama/model1",
                "mode": "once",
            },
        )
        harness.send(
            "active-model-submit",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "attempt_id": "active-model-attempt",
                    "message_id": "active-model-message",
                    "user_text": "Keep generating until interrupted.",
                },
            },
        )
        assert provider.started.wait(2)
        harness.send(
            "active-model-switch",
            {
                "method": "switch_session_model",
                "params": {
                    "session_id": session_id,
                    "provider_id": "ollama",
                    "model_id": "ollama/model1",
                    "mode": "current",
                },
            },
        )
        rejected = harness.read()
        assert rejected["id"] == "active-model-switch"
        assert rejected["ok"] is False
        assert rejected["error"]["code"] == "command_rejected"

        interrupted = harness.request(
            "active-model-interrupt",
            "interrupt_provider_stream",
            {
                "campaign_id": session_id,
                "attempt_id": "active-model-attempt",
            },
        )
        assert interrupted == {"status": "interrupted"}
        submit = harness.read()
        assert submit["id"] == "active-model-submit"
        assert submit["ok"] is False
        context = harness.request(
            "active-model-context",
            "session_context",
            {"session_id": session_id},
        )
        assert context["session"]["model_id"] == "ollama/model0"
        assert context["model_state"]["once"] is None
        assert harness.resolver is not None
        assert harness.resolver.sessions[-1].model_id == "ollama/model1"
    finally:
        provider.release.set()
        harness.close()


def test_owner_protocol_rejects_direct_dm_creation_without_builder_artifact(
    tmp_path: Path,
) -> None:
    provider = _ScriptedProvider((ProviderStreamChunk(content="MUST NOT RUN"),))
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", provider)
    try:
        harness.send(
            "create-unadmitted",
            {
                "method": "create_no_rules_campaign",
                "params": {
                    "campaign_id": "campaign-unadmitted-dm",
                    "display_name": "Unadmitted DM",
                    "turn_id": "turn-unadmitted-dm",
                    "provider_id": "test.transport",
                    "model_id": "test.transport/model0",
                },
            },
        )
        reply = harness.read()
        assert reply["id"] == "create-unadmitted"
        assert reply["ok"] is False
        assert reply["error"]["code"] == "command_rejected"
        assert reply["error"]["message"] == ("Unsupported owner method: create_no_rules_campaign")
        assert harness.request("index-unadmitted", "campaign_index") == []
    finally:
        harness.close()


def test_owner_transport_lists_safe_models_and_persists_selected_identity(
    tmp_path: Path,
) -> None:
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", _ScriptedProvider(()))
    try:
        choices = harness.request("models", "model_choices")
        assert choices == [
            {
                "provider_id": "test.transport",
                "provider_name": "Test Transport",
                "model_id": "test.transport/model0",
                "model_name": "Model 0",
                "context_window": 8192,
                "capability_registry_version": "sylvia.capability.registry.v1",
                "capability_tier_id": None,
                "capability_evidence_sha256": None,
            }
        ]
        campaign_id, _, session_id = _create_campaign(harness, "selected-model")
        index = harness.request("index", "campaign_index")
        entry = next(item for item in index if item["campaign_id"] == campaign_id)
        assert entry["session_id"] == session_id
        assert entry["provider_id"] == "test.transport"
        assert entry["model_id"] == "test.transport/model0"
        print(
            "F6E OWNER MODEL SELECTION",
            "choices=safe",
            "provider=test.transport",
            "model=test.transport/model0",
        )
    finally:
        harness.close()


def test_owner_loads_exact_models_from_its_data_folder_config(tmp_path: Path) -> None:
    database = tmp_path / "authority.sqlite3"
    sentinel_key = "OWNER-ONLY-SENTINEL-KEY"
    sentinel_url = "https://owner-config.invalid/v1"
    (tmp_path / "config.yaml").write_text(
        "\n".join(
            (
                "providers:",
                "  ollama:",
                "    enabled: false",
                "  local_test:",
                "    enabled: true",
                "    mode: byok",
                '    name: "Local Test"',
                f'    base_url: "{sentinel_url}"',
                f'    api_key: "{sentinel_key}"',
                "    models:",
                '      - id: "upstream/private-model"',
                '        name: "Configured Private Model"',
                "        context_window: 65536",
                "        max_output_tokens: 4096",
            )
        )
        + "\n",
        encoding="utf-8",
    )
    harness = _OwnerHarness(database, None)
    try:
        choices = harness.request("configured-models", "model_choices")
        assert choices == [
            {
                "provider_id": "local_test",
                "provider_name": "Local Test",
                "model_id": "local-test/upstream/private-model",
                "model_name": "Configured Private Model",
                "context_window": 65536,
                "declared_context_window": None,
                "declared_context_source": "unknown",
                "runtime_context_window": None,
                "runtime_context_source": "unknown",
                "configured_context_window": None,
                "effective_context_window": 65536,
                "effective_context_source": "legacy_unverified",
                "declared_context_observed_at": None,
                "runtime_context_observed_at": None,
                "configured_context_observed_at": None,
                "context_observed_at": None,
                "configured_runtime_mismatch": False,
                "configured_declared_mismatch": False,
                "max_output_tokens": 4096,
                "builder_output_tokens": 4096,
                "output_budget_mode": "enforced",
                "transport": "chat_completions",
                "supports_streaming": None,
                "supports_tools": None,
                "supported_reasoning_efforts": None,
                "default_reasoning_effort": None,
                "reasoning_source": "unknown",
                "reasoning_control": None,
                "reasoning_mode_defaults": {"dm": None, "builder": None},
                "capability_registry_version": "sylvia.capability.registry.v1",
                "capability_tier_id": None,
                "capability_evidence_sha256": None,
            }
        ]
        serialized = json.dumps(choices, sort_keys=True)
        assert sentinel_key not in serialized
        assert sentinel_url not in serialized
        assert str(tmp_path) not in serialized
    finally:
        harness.close()


def test_owner_provider_settings_save_reload_and_remove_without_echoing_keys(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authority.sqlite3"
    harness = _OwnerHarness(database, None)
    provider_key = "OWNER-ONLY-PROVIDER-KEY"
    try:
        settings = harness.request("provider-settings", "provider_settings")
        assert [entry["provider_id"] for entry in settings["presets"]] == [
            "chatgpt_subscription",
            "lm_studio",
            "ollama",
            "llama_cpp",
            "vllm",
            "localai",
            "opencode_go",
            "opencode_zen",
            "openrouter",
            "featherless",
            "openai",
            "custom",
        ]
        assert settings["configured"] == []

        saved = harness.request(
            "save-provider",
            "save_provider_settings",
            {
                "provider_id": "opencode_go",
                "base_url": "https://opencode.ai/zen/go/v1",
                "credential": provider_key,
                "model_id": "deepseek-v4",
                "model_name": "DeepSeek V4",
                "context_window": 1048576,
                "max_output_tokens": 4096,
            },
        )
        assert saved == {
            "provider_id": "opencode_go",
            "preset_id": "opencode_go",
            "name": "OpenCode Go",
            "model_id": "deepseek-v4",
            "canonical_model_id": "opencode-go/deepseek-v4",
            "credential_configured": True,
        }
        choices = harness.request("provider-models", "model_choices")
        assert len(choices) == 1
        assert choices[0]["provider_id"] == "opencode_go"
        assert choices[0]["model_id"] == "opencode-go/deepseek-v4"
        safe_projection = harness.request("provider-settings-safe", "provider_settings")
        assert safe_projection["configured"][0]["credential_configured"] is True
        assert provider_key not in json.dumps(safe_projection, sort_keys=True)
        assert provider_key not in json.dumps(choices, sort_keys=True)

        removed = harness.request(
            "remove-provider",
            "remove_provider_settings",
            {"provider_id": "opencode_go"},
        )
        assert removed == {"status": "removed"}
        assert harness.request("provider-models-removed", "model_choices") == []
        assert provider_key not in (tmp_path / "config.yaml").read_text(encoding="utf-8")
    finally:
        harness.close()


def test_lm_studio_model_discovery_uses_the_explicit_local_endpoint(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={"data": [{"id": "local/gemma-3"}, {"id": "local/gemma-3"}]},
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        provider_settings_module.httpx,
        "Client",
        lambda **_: real_client(transport=httpx.MockTransport(handle)),
    )
    models = provider_settings_module.discover_provider_models(
        tmp_path / "config.yaml",
        provider_id="lm_studio",
        base_url="http://127.0.0.1:1234/v1",
        credential="",
    )
    assert models["total"] == 1
    assert models["offset"] == 0
    assert models["limit"] == 100
    assert models["has_more"] is False
    assert models["models"] == [
        {
            "id": "local/gemma-3",
            "name": "local/gemma-3",
            "declared_context_window": None,
            "declared_context_source": "unknown",
            "runtime_context_window": None,
            "runtime_context_source": "unknown",
            "configured_context_window": None,
            "effective_context_window": None,
            "effective_context_source": "unknown",
            "declared_context_observed_at": None,
            "runtime_context_observed_at": None,
            "configured_context_observed_at": None,
            "context_observed_at": None,
            "configured_runtime_mismatch": False,
            "configured_declared_mismatch": False,
            "context_window": None,
            "context_window_source": "unknown",
            "max_output_tokens": None,
            "transport": "chat_completions",
            "supports_tools": None,
            "supports_streaming": None,
        }
    ]
    assert requests[0].url == httpx.URL("http://127.0.0.1:1234/v1/models")
    assert requests[1].url == httpx.URL("http://127.0.0.1:1234/api/v1/models")
    assert "authorization" not in requests[0].headers

    with pytest.raises(ConfigError, match="official API endpoint"):
        provider_settings_module.save_provider_settings(
            tmp_path / "config.yaml",
            provider_id="opencode_go",
            base_url="https://example.invalid/v1",
            credential="not-reported",
            model_id="model",
            model_name="Model",
            context_window=8192,
            max_output_tokens=2048,
        )


def test_owner_confirmation_atomically_creates_selected_builder_session(tmp_path: Path) -> None:
    database = tmp_path / "builder-confirmation.sqlite3"
    provider = _ScriptedProvider(())
    harness = _OwnerHarness(database, provider)
    try:
        confirmation = harness.request(
            "confirm-builder",
            "confirm_new_campaign",
            {
                "operation_id": "confirm-builder-selected-model",
                "display_name": "Builder Selected Model",
                "candidate_id": None,
                "turn_id": "turn-builder-selected-model",
                "provider_id": "test.transport",
                "model_id": "test.transport/model0",
            },
        )
        campaign_id = confirmation["campaign_id"]
        builder_session_id = confirmation["builder_session_id"]
        assert builder_session_id.startswith("session-builder-")
        campaign = harness.request("index-builder", "campaign_index")[0]
        assert campaign["campaign_id"] == campaign_id
        assert campaign["writer"] == "builder"
        assert campaign["session_id"] == builder_session_id
        assert campaign["session_status"] == "active"
        assert campaign["provider_id"] == "test.transport"
        assert campaign["model_id"] == "test.transport/model0"
        session = harness.request(
            "builder-session-context",
            "session_context",
            {"session_id": builder_session_id},
        )["session"]
        assert session["campaign_id"] == campaign_id
        assert session["role"] == "builder"
        replay = harness.request(
            "confirm-builder-replay",
            "confirm_new_campaign",
            {
                "operation_id": "confirm-builder-selected-model",
                "display_name": "Builder Selected Model",
                "candidate_id": None,
                "turn_id": "turn-builder-selected-model",
                "provider_id": "test.transport",
                "model_id": "test.transport/model0",
            },
        )
        assert replay == confirmation
        assert len(harness.request("index-builder-replay", "campaign_index")) == 1
        stopped = harness.request(
            "stop-builder-selected-model",
            "stop_agent_session",
            {"session_id": builder_session_id},
        )
        assert stopped["status"] == "stopped"
        resumed = harness.request(
            "resume-builder-selected-model",
            "resume_agent_session",
            {"session_id": builder_session_id},
        )
        assert resumed["status"] == "active"
        campaign = harness.request("index-builder-resumed", "campaign_index")[0]
        assert campaign["session_status"] == "active"
        print(
            "F6E BUILDER MODEL SELECTION",
            "confirmation=atomic",
            "replay=idempotent",
            "resume=explicit",
            campaign["provider_id"],
            campaign["model_id"],
        )
    finally:
        harness.close()


def test_owner_transport_archives_and_restores_with_designated_session(
    tmp_path: Path,
) -> None:
    provider = _ScriptedProvider(())
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", provider)
    try:
        campaign_id, turn_id, session_id = _create_campaign(harness, "lifecycle")
        stopped = harness.request(
            "stop-session",
            "stop_agent_session",
            {"session_id": session_id},
        )
        assert stopped["status"] == "stopped"
        before_archive = harness.request(
            "snapshot-before-archive", "snapshot", {"campaign_id": campaign_id}
        )
        archived = harness.request(
            "archive",
            "archive_campaign",
            {
                "operation_id": "archive-lifecycle",
                "campaign_id": campaign_id,
                "expected_revision": before_archive["revision"],
                "designated_session_id": session_id,
            },
        )
        assert archived["lifecycle"] == "archived"
        restored = harness.request(
            "restore",
            "restore_archived_campaign",
            {
                "operation_id": "restore-lifecycle",
                "campaign_id": campaign_id,
                "expected_revision": archived["resulting_revision"],
                "session_id": session_id,
            },
        )
        assert restored["lifecycle"] == "active"
    finally:
        harness.close()


def test_owner_submission_persists_provider_talk_through_canonical_session(tmp_path: Path) -> None:
    provider = _ScriptedProvider(
        (
            ProviderStreamChunk(content="Welcome "),
            ProviderStreamChunk(content="traveler."),
        )
    )
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", provider)
    try:
        campaign_id, turn_id, session_id = _create_campaign(harness, "talk")
        result = harness.request(
            "submit",
            "submit_session_input",
            {
                "session_id": session_id,
                "turn_id": turn_id,
                "attempt_id": "attempt-talk",
                "message_id": "message-talk",
                "user_text": "Begin.",
            },
        )
        assert result["interruption"] is None
        assert result["canonical"]
        projection = harness.request(
            "projection", "player_projection", {"campaign_id": campaign_id}
        )
        assert "Welcome traveler." in json.dumps(projection)
        assert provider.cancelled.wait(2)
    finally:
        harness.close()


def test_builder_source_ingestion_is_path_private_durable_and_web_gated(tmp_path: Path) -> None:
    provider = _BuilderToolSurfaceProvider()
    database = tmp_path / "authority.sqlite3"
    harness = _OwnerHarness(database, provider)
    private_root = tmp_path / "user-selected-private-location"
    private_root.mkdir()
    selected = private_root / "private-licensed-notes.md"
    selected.write_text("SYNTHETIC PRIVATE SOURCE TEXT", encoding="utf-8")
    try:
        campaign_id, session_id = _create_builder_session(harness, "sources")
        result = harness.request(
            "ingest-sources",
            "ingest_builder_sources",
            {
                "session_id": session_id,
                "ingestion_id": "ingestion-sources",
                "source_paths": [str(selected)],
            },
        )
        serialized_result = json.dumps(result, sort_keys=True)
        assert result["status"] == "ingested"
        assert result["source_count"] == 1
        assert str(private_root) not in serialized_result
        assert selected.name not in serialized_result
        assert "SYNTHETIC PRIVATE SOURCE TEXT" not in serialized_result
        opaque_path = result["sources"][0]["workspace_path"]
        assert opaque_path.startswith(".builder-sources/batch-")
        extracted = tmp_path / "campaigns" / campaign_id / opaque_path
        assert extracted.read_text(encoding="utf-8") == "SYNTHETIC PRIVATE SOURCE TEXT"

        replay = harness.request(
            "ingest-sources-replay",
            "ingest_builder_sources",
            {
                "session_id": session_id,
                "ingestion_id": "ingestion-sources",
                "source_paths": [str(selected)],
            },
        )
        assert replay == result
        changed = private_root / "changed-private-name.md"
        changed.write_text("CHANGED PRIVATE SOURCE MUST NOT PERSIST", encoding="utf-8")
        harness.send(
            "ingest-sources-collision",
            {
                "method": "ingest_builder_sources",
                "params": {
                    "session_id": session_id,
                    "ingestion_id": "ingestion-sources",
                    "source_paths": [str(changed)],
                },
            },
        )
        collision = harness.read()
        assert collision["id"] == "ingest-sources-collision"
        assert collision["ok"] is False
        assert collision["error"]["code"] == "command_rejected"
        source_root = tmp_path / "campaigns" / campaign_id / ".builder-sources"
        assert "CHANGED PRIVATE SOURCE MUST NOT PERSIST" not in "".join(
            path.read_text(encoding="utf-8") for path in source_root.rglob("*.txt")
        )

        context = harness.request("source-context", "session_context", {"session_id": session_id})
        serialized_context = json.dumps(context, sort_keys=True)
        assert "attached_local_sources" in serialized_context
        assert str(private_root) not in serialized_context
        assert selected.name not in serialized_context
        assert "SYNTHETIC PRIVATE SOURCE TEXT" not in serialized_context

        for ordinal, allowed in enumerate((False, True), start=1):
            harness.request(
                f"source-submit-{ordinal}",
                "submit_session_input",
                {
                    "session_id": session_id,
                    "turn_id": "turn-builder-sources",
                    "attempt_id": f"attempt-source-{ordinal}",
                    "message_id": f"message-source-{ordinal}",
                    "user_text": "Review the attached source metadata.",
                    "allow_public_web": allowed,
                },
            )
        assert len(provider.calls) == 3
        first_tools = {item["function"]["name"] for item in provider.calls[0][1] or []}
        continued_tools = {item["function"]["name"] for item in provider.calls[1][1] or []}
        second_turn_tools = {item["function"]["name"] for item in provider.calls[2][1] or []}
        assert "web_search" not in first_tools
        assert "web_search" not in continued_tools
        assert "web_search" in second_turn_tools
        assert str(private_root) not in json.dumps(provider.calls, sort_keys=True)
        assert selected.name not in json.dumps(provider.calls, sort_keys=True)
        assert "SYNTHETIC PRIVATE SOURCE TEXT" in json.dumps(provider.calls[1], sort_keys=True)
        durable_after_read = harness.request(
            "source-context-after-read", "session_context", {"session_id": session_id}
        )
        serialized_after_read = json.dumps(durable_after_read, sort_keys=True)
        assert "SYNTHETIC PRIVATE SOURCE TEXT" not in serialized_after_read
        assert "content_omitted_from_history" in serialized_after_read
    finally:
        harness.close()

    reopened = _OwnerHarness(database, _ScriptedProvider(()))
    try:
        context = reopened.request(
            "source-context-reopened", "session_context", {"session_id": session_id}
        )
        assert "attached_local_sources" in json.dumps(context, sort_keys=True)
        assert extracted.is_file()
        reopened.request("stop-source-session", "stop_agent_session", {"session_id": session_id})
        snapshot = reopened.request(
            "source-snapshot-before-trash", "snapshot", {"campaign_id": campaign_id}
        )
        trashed = reopened.request(
            "trash-source-campaign",
            "trash_campaign",
            {
                "operation_id": "trash-source-campaign",
                "campaign_id": campaign_id,
                "expected_revision": snapshot["revision"],
                "designated_session_id": session_id,
            },
        )
        reopened.request(
            "purge-source-campaign",
            "purge_campaign",
            {
                "operation_id": "purge-source-campaign",
                "campaign_id": campaign_id,
                "expected_revision": trashed["resulting_revision"],
                "confirmation": f"permanently purge {campaign_id}",
            },
        )
        assert not extracted.parent.exists()
    finally:
        reopened.close()


def test_builder_source_purge_failure_is_retryable_and_reconciles_after_restart(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    database = tmp_path / "authority.sqlite3"
    harness = _OwnerHarness(database, _ScriptedProvider(()))
    source = tmp_path / "private-source.txt"
    source.write_text("PRIVATE PURGE RETRY SOURCE", encoding="utf-8")
    original_finalize = owner_process.finalize_builder_source_purge
    try:
        campaign_id, session_id = _create_builder_session(harness, "purge-retry")
        harness.request(
            "ingest-purge-retry",
            "ingest_builder_sources",
            {
                "session_id": session_id,
                "ingestion_id": "ingestion-purge-retry",
                "source_paths": [str(source)],
            },
        )
        harness.request(
            "export-purge-retry",
            "export_campaign",
            {
                "export_id": "export-purge-retry",
                "operation_id": "export-purge-retry",
                "campaign_id": campaign_id,
            },
        )
        harness.request("stop-purge-retry", "stop_agent_session", {"session_id": session_id})
        snapshot = harness.request("snapshot-purge-retry", "snapshot", {"campaign_id": campaign_id})
        trashed = harness.request(
            "trash-purge-retry",
            "trash_campaign",
            {
                "operation_id": "trash-purge-retry",
                "campaign_id": campaign_id,
                "expected_revision": snapshot["revision"],
                "designated_session_id": session_id,
            },
        )

        def fail_finalize(_stage: object) -> None:
            raise CommandRejected("synthetic private-source deletion failure")

        monkeypatch.setattr(owner_process, "finalize_builder_source_purge", fail_finalize)
        purge_params = {
            "operation_id": "purge-purge-retry",
            "campaign_id": campaign_id,
            "expected_revision": trashed["resulting_revision"],
            "confirmation": f"permanently purge {campaign_id}",
        }
        harness.send(
            "purge-purge-retry-fails",
            {"method": "purge_campaign", "params": purge_params},
        )
        failed = harness.read()
        assert failed["id"] == "purge-purge-retry-fails"
        assert failed["ok"] is False
        workspace = tmp_path / "campaigns" / campaign_id
        assert not (workspace / ".builder-sources").exists()
        assert (workspace / ".builder-sources-purge-pending").is_dir()
        recovered = harness.request(
            "recover-purge-retry",
            "recover_campaign",
            {
                "recovery_id": "recover-purge-retry",
                "export_id": "export-purge-retry",
            },
        )
        assert recovered["campaign_id"] == campaign_id
        recovered_source = (
            workspace / ".builder-sources" / "batch-recovered-after-purge" / "source-recovered.txt"
        )
        recovered_source.parent.mkdir(parents=True, exist_ok=True)
        recovered_source.write_text("RECOVERED SOURCE SURVIVES", encoding="utf-8")
    finally:
        harness.close()

    monkeypatch.setattr(owner_process, "finalize_builder_source_purge", original_finalize)
    reopened = _OwnerHarness(database, _ScriptedProvider(()))
    try:
        workspace = tmp_path / "campaigns" / campaign_id
        assert not (workspace / ".builder-sources-purge-pending").exists()
        assert recovered_source.read_text(encoding="utf-8") == "RECOVERED SOURCE SURVIVES"
        replay = reopened.request(
            "purge-purge-retry-replay",
            "purge_campaign",
            purge_params,
        )
        assert replay["campaign_id"] == campaign_id
        assert recovered_source.read_text(encoding="utf-8") == "RECOVERED SOURCE SURVIVES"
    finally:
        reopened.close()


def test_rejected_purge_does_not_quarantine_sources_during_active_builder_work(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _ControlledBlockingBuilderProvider()
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", provider)
    source = tmp_path / "private-active-source.txt"
    source.write_text("PRIVATE ACTIVE SOURCE", encoding="utf-8")
    stage_calls: list[Path] = []
    original_stage = owner_process.stage_builder_sources_for_purge

    def track_stage(
        workspace: Path,
        *,
        campaign_id: str,
        operation_id: str,
    ) -> object:
        stage_calls.append(workspace)
        return original_stage(
            workspace,
            campaign_id=campaign_id,
            operation_id=operation_id,
        )

    monkeypatch.setattr(owner_process, "stage_builder_sources_for_purge", track_stage)
    try:
        campaign_id, session_id = _create_builder_session(harness, "active-purge")
        ingested = harness.request(
            "ingest-active-purge",
            "ingest_builder_sources",
            {
                "session_id": session_id,
                "ingestion_id": "ingestion-active-purge",
                "source_paths": [str(source)],
            },
        )
        published = tmp_path / "campaigns" / campaign_id / ingested["sources"][0]["workspace_path"]
        snapshot = harness.request(
            "snapshot-active-purge", "snapshot", {"campaign_id": campaign_id}
        )
        harness.send(
            "submit-active-purge",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "turn_id": "turn-builder-active-purge",
                    "attempt_id": "attempt-builder-active-purge",
                    "message_id": "message-builder-active-purge",
                    "user_text": "Keep this source open.",
                },
            },
        )
        assert provider.started.wait(2)
        harness.send(
            "purge-during-active-work",
            {
                "method": "purge_campaign",
                "params": {
                    "operation_id": "purge-during-active-work",
                    "campaign_id": campaign_id,
                    "expected_revision": snapshot["revision"],
                    "confirmation": f"permanently purge {campaign_id}",
                },
            },
        )
        rejected = harness.read()
        assert rejected["id"] == "purge-during-active-work"
        assert rejected["ok"] is False
        assert rejected["error"]["code"] == "campaign_work_in_progress"
        assert stage_calls == []
        assert published.read_text(encoding="utf-8") == "PRIVATE ACTIVE SOURCE"
        assert not (
            tmp_path / "campaigns" / campaign_id / ".builder-sources-purge-pending"
        ).exists()
    finally:
        provider.release.set()
        if provider.started.is_set():
            completed = harness.read()
            assert completed["id"] == "submit-active-purge"
        harness.close()


def test_uncertain_purge_outcome_stays_quarantined_until_exact_replay(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", _ScriptedProvider(()))
    source = tmp_path / "uncertain-private-source.txt"
    source.write_text("UNCERTAIN PRIVATE SOURCE", encoding="utf-8")
    original_commit = SQLiteAuthorityStore.commit_campaign_purge
    try:
        campaign_id, session_id = _create_builder_session(harness, "uncertain-purge")
        harness.request(
            "ingest-uncertain-purge",
            "ingest_builder_sources",
            {
                "session_id": session_id,
                "ingestion_id": "ingestion-uncertain-purge",
                "source_paths": [str(source)],
            },
        )
        harness.request("stop-uncertain-purge", "stop_agent_session", {"session_id": session_id})
        snapshot = harness.request(
            "snapshot-uncertain-purge", "snapshot", {"campaign_id": campaign_id}
        )
        trashed = harness.request(
            "trash-uncertain-purge",
            "trash_campaign",
            {
                "operation_id": "trash-uncertain-purge",
                "campaign_id": campaign_id,
                "expected_revision": snapshot["revision"],
                "designated_session_id": session_id,
            },
        )

        def uncertain_commit(_store: object, **_kwargs: object) -> object:
            raise CommandRejected(
                "Campaign purge commit outcome is uncertain",
                code="campaign_purge_commit_outcome_uncertain",
            )

        monkeypatch.setattr(SQLiteAuthorityStore, "commit_campaign_purge", uncertain_commit)
        purge_params = {
            "operation_id": "purge-uncertain-purge",
            "campaign_id": campaign_id,
            "expected_revision": trashed["resulting_revision"],
            "confirmation": f"permanently purge {campaign_id}",
        }
        harness.send(
            "purge-uncertain-purge-fails",
            {"method": "purge_campaign", "params": purge_params},
        )
        uncertain = harness.read()
        assert uncertain["id"] == "purge-uncertain-purge-fails"
        assert uncertain["ok"] is False
        assert uncertain["error"]["code"] == "campaign_purge_commit_outcome_uncertain"
        workspace = tmp_path / "campaigns" / campaign_id
        assert not (workspace / ".builder-sources").exists()
        assert (workspace / ".builder-sources-purge-pending").is_dir()

        monkeypatch.setattr(SQLiteAuthorityStore, "commit_campaign_purge", original_commit)
        harness.request("purge-uncertain-purge-replay", "purge_campaign", purge_params)
        assert not (workspace / ".builder-sources-purge-pending").exists()
    finally:
        monkeypatch.setattr(SQLiteAuthorityStore, "commit_campaign_purge", original_commit)
        harness.close()


def test_old_purge_replay_cannot_delete_sources_from_recovered_campaign_generation(
    tmp_path: Path,
) -> None:
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", _ScriptedProvider(()))
    try:
        campaign_id, session_id = _create_builder_session(harness, "purge-generation")
        harness.request(
            "export-purge-generation",
            "export_campaign",
            {
                "export_id": "export-purge-generation",
                "operation_id": "export-purge-generation",
                "campaign_id": campaign_id,
            },
        )
        harness.request(
            "stop-purge-generation",
            "stop_agent_session",
            {"session_id": session_id},
        )
        snapshot = harness.request(
            "snapshot-purge-generation", "snapshot", {"campaign_id": campaign_id}
        )
        trashed = harness.request(
            "trash-purge-generation",
            "trash_campaign",
            {
                "operation_id": "trash-purge-generation",
                "campaign_id": campaign_id,
                "expected_revision": snapshot["revision"],
                "designated_session_id": session_id,
            },
        )
        purge_params = {
            "operation_id": "purge-generation",
            "campaign_id": campaign_id,
            "expected_revision": trashed["resulting_revision"],
            "confirmation": f"permanently purge {campaign_id}",
        }
        harness.request("purge-generation", "purge_campaign", purge_params)
        recovered = harness.request(
            "recover-purge-generation",
            "recover_campaign",
            {
                "recovery_id": "recover-purge-generation",
                "export_id": "export-purge-generation",
            },
        )
        assert recovered["campaign_id"] == campaign_id

        recovered_source = (
            tmp_path
            / "campaigns"
            / campaign_id
            / ".builder-sources"
            / "batch-recovered-generation"
            / "source-recovered.txt"
        )
        recovered_source.parent.mkdir(parents=True, exist_ok=True)
        recovered_source.write_text("RECOVERED GENERATION SOURCE", encoding="utf-8")

        harness.request("purge-generation-replay", "purge_campaign", purge_params)
        assert recovered_source.read_text(encoding="utf-8") == "RECOVERED GENERATION SOURCE"
        assert (
            harness.request(
                "snapshot-recovered-generation", "snapshot", {"campaign_id": campaign_id}
            )["campaign_id"]
            == campaign_id
        )
    finally:
        harness.close()


def test_builder_stream_poll_steer_completion_is_private_and_chronological(
    tmp_path: Path,
) -> None:
    provider = _StreamingSteerBuilderProvider()
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", provider)
    try:
        _campaign_id, session_id = _create_builder_session(harness, "stream-steer")
        harness.send(
            "submit-builder-stream-steer",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "turn_id": "turn-builder-stream-steer",
                    "attempt_id": "attempt-builder-stream-steer",
                    "message_id": "message-builder-stream-steer",
                    "user_text": "Stream this once.",
                    "allow_public_web": True,
                },
            },
        )
        assert provider.first_chunk.wait(2)

        live: dict[str, Any] = {}
        for poll in range(50):
            live = harness.request(
                f"builder-stream-live-{poll}",
                "session_context",
                {"session_id": session_id},
            )
            if live["provisional"]:
                break
            provider.release_first.wait(0.02)
        assert live["provisional"] == [
            {
                "kind": "talk",
                "text": "First ",
                "collapsed_by_default": False,
                "provisional": True,
                "attempt_id": "attempt-builder-stream-steer",
            }
        ]
        assert "PRIVATE BUILDER REASONING" not in json.dumps(live)

        heartbeat = harness.request(
            "builder-stream-heartbeat",
            "provider_stream_heartbeat",
            {
                "campaign_id": session_id,
                "attempt_id": "attempt-builder-stream-steer",
                "attachment_generation": None,
            },
        )
        assert heartbeat["attachment_generation"] == 1
        steered = harness.request(
            "builder-stream-steer",
            "steer_session",
            {
                "session_id": session_id,
                "message_id": "steering-builder-stream",
                "text": "Keep the ending concise.",
            },
        )
        assert steered == {"status": "accepted"}
        during = harness.request(
            "builder-stream-during",
            "session_context",
            {"session_id": session_id},
        )
        assert [message["kind"] for message in during["messages"]] == [
            "user",
            "talk",
            "steering",
        ]

        provider.release_first.set()
        submit = harness.read()
        assert submit["id"] == "submit-builder-stream-steer"
        assert submit["ok"] is True
        final = harness.request(
            "builder-stream-final",
            "session_context",
            {"session_id": session_id},
        )
        assert final["provisional"] == []
        assert final["activity"] == {
            "attempt_id": "attempt-builder-stream-steer", "status": "completed"
        }
        assert [
            message["kind"] for message in final["messages"]
            if message["content"].get("kind") != "builder_attempt_terminal"
        ] == [
            "user",
            "talk",
            "steering",
            "talk",
            "talk",
        ]
        assert "PRIVATE BUILDER REASONING" not in json.dumps(final)
        assert (
            sum(
                1
                for message in provider.calls[0]
                if message.get("role") == "user" and message.get("content") == "Stream this once."
            )
            == 1
        )
        assert "Keep the ending concise." in json.dumps(provider.calls[1])
        assert "web_search" in provider.tool_names[0]
        assert "web_search" not in provider.tool_names[1]
    finally:
        provider.release_first.set()
        harness.close()


def test_builder_steering_reaches_the_next_provider_call_after_a_tool_boundary(
    tmp_path: Path,
) -> None:
    provider = _ToolBoundarySteerBuilderProvider()
    harness = _OwnerHarness(tmp_path / "tool-boundary-steer.sqlite3", provider)
    try:
        _campaign_id, session_id = _create_builder_session(harness, "tool-boundary-steer")
        harness.send(
            "submit-tool-boundary-steer",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "turn_id": "turn-tool-boundary-steer",
                    "attempt_id": "attempt-tool-boundary-steer",
                    "message_id": "message-tool-boundary-steer",
                    "user_text": "Write the plan and continue.",
                },
            },
        )
        assert provider.first_chunk.wait(2)
        for poll in range(50):
            live = harness.request(
                f"tool-boundary-live-{poll}",
                "session_context",
                {"session_id": session_id},
            )
            if live["provisional"]:
                break
            provider.release_tool_call.wait(0.02)
        assert live["provisional"][0]["text"] == "Before tool."
        assert harness.request(
            "steer-at-tool-boundary",
            "steer_session",
            {
                "session_id": session_id,
                "message_id": "steering-at-tool-boundary",
                "text": "Mention the observatory after the tool completes.",
            },
        ) == {"status": "accepted"}
        provider.release_tool_call.set()

        submit = harness.read()
        assert submit["id"] == "submit-tool-boundary-steer"
        assert submit["ok"] is True
        assert len(provider.calls) == 2
        assert "Mention the observatory" in json.dumps(provider.calls[1])
        context = harness.request(
            "context-tool-boundary-steer",
            "session_context",
            {"session_id": session_id},
        )
        assert [
            message["kind"] for message in context["messages"]
            if message["content"].get("kind") != "builder_attempt_terminal"
        ] == [
            "user",
            "talk",
            "steering",
            "evidence",
            "talk",
        ]
    finally:
        provider.release_tool_call.set()
        harness.close()


def test_builder_truncation_drops_incomplete_prose_without_automatic_retry(
    tmp_path: Path,
) -> None:
    provider = _TruncatedBuilderProvider()
    harness = _OwnerHarness(tmp_path / "builder-truncation.sqlite3", provider)
    try:
        _campaign_id, session_id = _create_builder_session(harness, "truncation")
        harness.send(
            "submit-builder-truncation",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "turn_id": "turn-builder-truncation",
                    "attempt_id": "attempt-builder-truncation",
                    "message_id": "message-builder-truncation",
                    "user_text": "Produce one bounded response.",
                },
            },
        )
        reply = harness.read()
        assert reply["id"] == "submit-builder-truncation"
        assert reply["ok"] is False
        assert provider.calls == 1

        context = harness.request(
            "context-builder-truncation",
            "session_context",
            {"session_id": session_id},
        )
        assert context["provisional"] == []
        assert [message["kind"] for message in context["messages"]] == ["user", "evidence"]
        serialized = json.dumps(context)
        assert "TRUNCATED BUILDER PROSE" not in serialized
        assert "PRIVATE TRUNCATED REASONING" not in serialized
        assert "AUTOMATIC RETRY" not in serialized
    finally:
        harness.close()


def test_campaign_independent_builder_steering_does_not_reacquire_its_live_lane(
    tmp_path: Path,
) -> None:
    provider = _StreamingSteerBuilderProvider()
    harness = _OwnerHarness(tmp_path / "independent-steer.sqlite3", provider)
    try:
        chat = harness.request(
            "create-independent-steer",
            "create_builder_chat",
            {
                "operation_id": "create-independent-steer",
                "provider_id": "test.transport",
                "model_id": "test.transport/model0",
            },
        )
        session_id = chat["session"]["session_id"]
        harness.send(
            "submit-independent-steer",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "turn_id": None,
                    "attempt_id": "attempt-independent-steer",
                    "message_id": "message-independent-steer",
                    "user_text": "Stream this once.",
                    "allow_public_web": False,
                },
            },
        )
        assert provider.first_chunk.wait(2)
        heartbeat = harness.request(
            "independent-steer-heartbeat",
            "provider_stream_heartbeat",
            {
                "campaign_id": session_id,
                "attempt_id": "attempt-independent-steer",
                "attachment_generation": None,
            },
        )
        assert heartbeat["attachment_generation"] == 1

        steered = harness.request(
            "independent-steer",
            "steer_session",
            {
                "session_id": session_id,
                "message_id": "steering-independent",
                "text": "Keep the ending concise.",
            },
        )
        assert steered == {"status": "accepted"}
        provider.release_first.set()
        submit = harness.read()
        assert submit["id"] == "submit-independent-steer"
        assert submit["ok"] is True, submit

        final = harness.request(
            "independent-steer-final",
            "session_context",
            {"session_id": session_id},
        )
        kinds = [
            message["kind"] for message in final["messages"]
            if message["content"].get("kind") != "builder_attempt_terminal"
        ]
        assert kinds[0] == "user"
        assert "steering" in kinds
        assert kinds[-2:] == ["talk", "talk"]
    finally:
        provider.release_first.set()
        harness.close()


def test_builder_provider_stream_interrupt_and_reconnect_do_not_stop_session(
    tmp_path: Path,
) -> None:
    provider = _ControlledBlockingBuilderProvider()
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", provider)
    try:
        campaign_id, session_id = _create_builder_session(harness, "provider-lease")
        harness.send(
            "submit-builder-provider-lease",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "turn_id": "turn-builder-provider-lease",
                    "attempt_id": "attempt-builder-provider-lease",
                    "message_id": "message-builder-provider-lease",
                    "user_text": "Wait for provider stream control.",
                },
            },
        )
        assert provider.started.wait(2)
        heartbeat = harness.request(
            "provider-lease-heartbeat",
            "provider_stream_heartbeat",
            {
                "campaign_id": session_id,
                "attempt_id": "attempt-builder-provider-lease",
                "attachment_generation": None,
            },
        )
        disconnected = harness.request(
            "provider-lease-disconnect",
            "disconnect_provider_stream",
            {
                "campaign_id": session_id,
                "attempt_id": "attempt-builder-provider-lease",
                "attachment_generation": heartbeat["attachment_generation"],
            },
        )
        assert disconnected["disconnect_deadline"] > 0
        reconnected = harness.request(
            "provider-lease-reconnect",
            "reconnect_provider_stream",
            {
                "campaign_id": session_id,
                "attempt_id": "attempt-builder-provider-lease",
                "attachment_generation": heartbeat["attachment_generation"],
            },
        )
        assert reconnected["attachment_generation"] == 2
        interrupted = harness.request(
            "provider-lease-interrupt",
            "interrupt_provider_stream",
            {
                "campaign_id": session_id,
                "attempt_id": "attempt-builder-provider-lease",
            },
        )
        assert interrupted == {"status": "interrupted"}
        submit = harness.read()
        assert submit["id"] == "submit-builder-provider-lease"
        assert submit["ok"] is False
        context = harness.request(
            "provider-lease-context",
            "session_context",
            {"session_id": session_id},
        )
        assert context["session"]["status"] == "active"
        assert context["activity"] == {
            "attempt_id": "attempt-builder-provider-lease", "status": "stopped"
        }
        saved_status = harness.request(
            "provider-lease-saved-status", "provider_stream_status",
            {"campaign_id": session_id, "attempt_id": "attempt-builder-provider-lease"},
        )
        assert saved_status["status"] == "stopped"
        assert [message["kind"] for message in context["messages"]] == ["user", "evidence"]
    finally:
        provider.release.set()
        harness.close()

    reopened = _OwnerHarness(tmp_path / "authority.sqlite3", None)
    try:
        saved = reopened.request(
            "reopened-provider-status", "provider_stream_status",
            {"campaign_id": session_id, "attempt_id": "attempt-builder-provider-lease"},
        )
        assert saved["status"] == "stopped"
        context = reopened.request(
            "reopened-builder-context", "session_context", {"session_id": session_id}
        )
        assert context["activity"] == saved
        assert context["session"]["status"] == "active"
    finally:
        reopened.close()


def test_builder_provider_disconnect_grace_cancels_unfinished_work() -> None:
    cancelled = threading.Event()
    supervisor = LocalOwnerSupervisor(
        disconnect_grace_seconds=0.05,
        attachment_lease_seconds=1.0,
    )
    try:
        supervisor.track_provider_work(
            "campaign-provider-grace",
            "attempt-provider-grace",
            cancelled.set,
        )
        _deadline, generation = supervisor.heartbeat_provider_work(
            "campaign-provider-grace",
            "attempt-provider-grace",
        )
        supervisor.disconnect_provider_work(
            "campaign-provider-grace",
            "attempt-provider-grace",
            expected_generation=generation,
        )

        assert cancelled.wait(1)
        assert (
            supervisor.provider_work_status("campaign-provider-grace", "attempt-provider-grace")
            == "disconnected"
        )
        supervisor.release_provider_work("campaign-provider-grace", "attempt-provider-grace")
        assert (
            supervisor.provider_work_status("campaign-provider-grace", "attempt-provider-grace")
            == "completed"
        )
    finally:
        supervisor.shutdown()


def test_builder_provider_can_reattach_before_its_first_heartbeat() -> None:
    supervisor = LocalOwnerSupervisor(
        disconnect_grace_seconds=1.0,
        attachment_lease_seconds=1.0,
    )
    try:
        supervisor.track_provider_work(
            "campaign-provider-fast-reattach",
            "attempt-provider-fast-reattach",
            lambda: None,
        )
        supervisor.disconnect_provider_work(
            "campaign-provider-fast-reattach",
            "attempt-provider-fast-reattach",
        )
        assert (
            supervisor.reconnect_provider_work(
                "campaign-provider-fast-reattach",
                "attempt-provider-fast-reattach",
            )
            == 2
        )
        _deadline, generation = supervisor.heartbeat_provider_work(
            "campaign-provider-fast-reattach",
            "attempt-provider-fast-reattach",
            expected_generation=2,
        )
        assert generation == 2
    finally:
        supervisor.release_provider_work(
            "campaign-provider-fast-reattach",
            "attempt-provider-fast-reattach",
        )
        supervisor.shutdown()


def test_builder_provider_requires_exact_generation_after_attachment_claim() -> None:
    supervisor = LocalOwnerSupervisor(
        disconnect_grace_seconds=1.0,
        attachment_lease_seconds=1.0,
    )
    try:
        supervisor.track_provider_work(
            "campaign-provider-generation",
            "attempt-provider-generation",
            lambda: None,
        )
        _deadline, generation = supervisor.heartbeat_provider_work(
            "campaign-provider-generation",
            "attempt-provider-generation",
        )
        assert generation == 1
        with pytest.raises(CommandRejected, match="generation is required"):
            supervisor.disconnect_provider_work(
                "campaign-provider-generation",
                "attempt-provider-generation",
            )
        with pytest.raises(CommandRejected, match="generation is stale"):
            supervisor.disconnect_provider_work(
                "campaign-provider-generation",
                "attempt-provider-generation",
                expected_generation=0,
            )
        supervisor.disconnect_provider_work(
            "campaign-provider-generation",
            "attempt-provider-generation",
            expected_generation=1,
        )
        with pytest.raises(CommandRejected, match="generation is required"):
            supervisor.reconnect_provider_work(
                "campaign-provider-generation",
                "attempt-provider-generation",
            )
        assert (
            supervisor.reconnect_provider_work(
                "campaign-provider-generation",
                "attempt-provider-generation",
                expected_generation=1,
            )
            == 2
        )
        with pytest.raises(CommandRejected, match="generation is required"):
            supervisor.heartbeat_provider_work(
                "campaign-provider-generation",
                "attempt-provider-generation",
            )
        _deadline, generation = supervisor.heartbeat_provider_work(
            "campaign-provider-generation",
            "attempt-provider-generation",
            expected_generation=2,
        )
        assert generation == 2
    finally:
        supervisor.release_provider_work(
            "campaign-provider-generation",
            "attempt-provider-generation",
        )
        supervisor.shutdown()


@pytest.mark.parametrize("artifact", [None, ""])
def test_dm_start_rejects_missing_or_empty_builder_plan(
    tmp_path: Path,
    artifact: str | None,
) -> None:
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", _ScriptedProvider(()))
    try:
        confirmation = harness.request(
            "confirm-plan-required",
            "confirm_new_campaign",
            {
                "operation_id": "confirm-plan-required",
                "display_name": "Plan Required",
                "candidate_id": None,
                "turn_id": "turn-plan-required",
                "provider_id": "test.transport",
                "model_id": "test.transport/model0",
            },
        )
        campaign_id = confirmation["campaign_id"]
        harness.request(
            "begin-plan-required",
            "begin_campaign_construction",
            {
                "operation_id": "begin-plan-required",
                "campaign_id": campaign_id,
                "lane": "quick_start",
                "expected_revision": 0,
                "canonical_input": {},
            },
        )
        if artifact is not None:
            plan_path = tmp_path / "campaigns" / campaign_id / "campaign-plan.md"
            plan_path.parent.mkdir(parents=True)
            plan_path.write_text(artifact, encoding="utf-8")

        harness.send(
            "finalize-plan-required",
            {
                "method": "finalize_campaign_construction",
                "params": {
                    "campaign_id": campaign_id,
                    "dm_provider_id": "test.transport",
                    "dm_model_id": "test.transport/model0",
                },
            },
        )
        reply = harness.read()
        assert reply["ok"] is False
        assert reply["error"]["code"] == "command_rejected"
        assert "campaign plan file" in reply["error"]["message"].lower()
        snapshot = harness.request(
            "plan-required-snapshot",
            "snapshot",
            {"campaign_id": campaign_id},
        )
        assert snapshot["writer"] == "builder"
    finally:
        harness.close()


def test_persistent_builder_uses_ordinary_tools_after_dm_starts(tmp_path: Path) -> None:
    provider = _BuilderToolProvider()
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", provider)
    try:
        confirmation = harness.request(
            "confirm-persistent-builder",
            "confirm_new_campaign",
            {
                "operation_id": "confirm-persistent-builder",
                "display_name": "Persistent Builder",
                "candidate_id": None,
                "turn_id": "turn-persistent-builder",
                "provider_id": "test.transport",
                "model_id": "test.transport/model0",
            },
        )
        campaign_id = confirmation["campaign_id"]
        builder_session_id = confirmation["builder_session_id"]
        harness.request(
            "begin-persistent-builder",
            "begin_campaign_construction",
            {
                "operation_id": "begin-persistent-builder",
                "campaign_id": campaign_id,
                "lane": "quick_start",
                "expected_revision": 0,
                "canonical_input": {"premise": "A storm-bound observatory"},
            },
        )
        plan_turn = harness.request(
            "submit-builder-plan",
            "submit_session_input",
            {
                "session_id": builder_session_id,
                "turn_id": "turn-persistent-builder-plan",
                "attempt_id": "attempt-persistent-builder-plan",
                "message_id": "message-persistent-builder-plan",
                "user_text": "Write the campaign plan file.",
            },
        )
        assert plan_turn["session_id"] == builder_session_id
        first_provider_messages = json.dumps(provider.calls[0][0], sort_keys=True)
        assert "campaign_construction_brief" in first_provider_messages
        assert "A storm-bound observatory" in first_provider_messages
        plan_artifact_text = (
            (tmp_path / "campaigns" / campaign_id / "campaign-plan.md").read_bytes().decode("utf-8")
        )
        finalized = harness.request(
            "finalize-persistent-builder",
            "finalize_campaign_construction",
            {
                "campaign_id": campaign_id,
                "dm_provider_id": "test.transport",
                "dm_model_id": "test.transport/model0",
            },
        )
        assert finalized["plan"]["content"] == {"campaign_plan_markdown": plan_artifact_text}
        revision_before = finalized["handoff"]["resulting_campaign_revision"]

        result = harness.request(
            "submit-persistent-builder",
            "submit_session_input",
            {
                "session_id": builder_session_id,
                "turn_id": "turn-persistent-builder-followup",
                "attempt_id": "attempt-persistent-builder-followup",
                "message_id": "message-persistent-builder-followup",
                "user_text": "Remain available after the DM starts.",
            },
        )

        assert result["session_id"] == builder_session_id
        assert (tmp_path / "campaigns" / campaign_id / "campaign-plan.md").read_text(
            encoding="utf-8"
        ) == "# Campaign Plan\n\nThe observatory waits."
        context = harness.request(
            "persistent-builder-context",
            "session_context",
            {"session_id": builder_session_id},
        )
        assert [
            message["kind"] for message in context["messages"]
            if message["content"].get("kind") != "builder_attempt_terminal"
        ][-2:] == ["user", "talk"]
        talk = [message for message in context["messages"] if message["kind"] == "talk"]
        assert talk[-1]["content"]["text"] == "The campaign plan file is ready."
        snapshot = harness.request(
            "persistent-builder-snapshot",
            "snapshot",
            {"campaign_id": campaign_id},
        )
        assert snapshot["writer"] == "dm"
        assert snapshot["revision"] == revision_before
        campaigns = harness.request("persistent-builder-index", "campaign_index")
        assert len(campaigns) == 1
        assert {(session["role"], session["status"]) for session in campaigns[0]["sessions"]} == {
            ("builder", "active"),
            ("dm", "active"),
        }
        advertised = {tool["function"]["name"] for tool in (provider.calls[0][1] or [])}
        assert {
            "read_file", "write_file", "edit_file", "search_files", "delete_file"
        } <= advertised
        assert "web_search" not in advertised
    finally:
        harness.close()


def test_no_rules_dm_receives_plan_without_builder_file_or_ruleset_tools(
    tmp_path: Path,
) -> None:
    provider = _DMPlanProvider()
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", provider)
    try:
        confirmation = harness.request(
            "confirm-dm-plan-tools",
            "confirm_new_campaign",
            {
                "operation_id": "confirm-dm-plan-tools",
                "display_name": "DM Plan Tools",
                "candidate_id": None,
                "turn_id": "turn-builder-dm-plan-tools",
                "provider_id": "test.transport",
                "model_id": "test.transport/model0",
            },
        )
        campaign_id = confirmation["campaign_id"]
        harness.request(
            "begin-dm-plan-tools",
            "begin_campaign_construction",
            {
                "operation_id": "begin-dm-plan-tools",
                "campaign_id": campaign_id,
                "lane": "quick_start",
                "expected_revision": 0,
                "canonical_input": {"premise": "A plan-tool boundary"},
            },
        )
        plan_path = tmp_path / "campaigns" / campaign_id / "campaign-plan.md"
        plan_path.parent.mkdir(parents=True)
        plan_path.write_text("# Initial DM Plan\n", encoding="utf-8")
        finalized = harness.request(
            "finalize-dm-plan-tools",
            "finalize_campaign_construction",
            {
                "campaign_id": campaign_id,
                "dm_provider_id": "test.transport",
                "dm_model_id": "test.transport/model0",
            },
        )
        dm_session_id = finalized["handoff"]["dm_session_id"]
        dm_turn_id = finalized["dm_turn_id"]
        assert dm_turn_id.startswith("turn-dm-")
        assert finalized["dm_attempt_id"] == (
            "attempt-campaign-start-" + hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:24]
        )
        revision_before = finalized["handoff"]["resulting_campaign_revision"]

        started = harness.request(
            "start-dm-plan-tools",
            "start_dm_session",
            {
                "campaign_id": campaign_id,
                "session_id": dm_session_id,
                "turn_id": dm_turn_id,
            },
        )
        assert started["receipts"] == []
        start_messages = provider.calls[0][0]
        assert not any(
            message.get("role") == "user"
            and str(message.get("content", "")).strip() in {"Begin the story", "Begin."}
            for message in start_messages
        )
        assert any(
            message.get("role") == "system"
            and "application-owned, not player speech" in str(message.get("content", ""))
            and "campaign_start" in str(message.get("content", ""))
            for message in start_messages
        )

        result = harness.request(
            "submit-dm-plan-tools",
            "submit_session_input",
            {
                "session_id": dm_session_id,
                "turn_id": dm_turn_id,
                "attempt_id": "attempt-dm-plan-tools",
                "message_id": "message-dm-plan-tools",
                "user_text": "Continue from the accepted campaign plan.",
            },
        )

        assert result["receipts"] == []
        assert plan_path.read_text(encoding="utf-8") == "# Initial DM Plan\n"
        context = harness.request(
            "dm-plan-tools-context",
            "session_context",
            {"session_id": dm_session_id},
        )
        evidence = [message for message in context["messages"] if message["kind"] == "evidence"]
        evidence_kinds = [message["content"]["kind"] for message in evidence]
        assert evidence_kinds[0] == "campaign_start"
        assert evidence_kinds.count("gm_system_contract") == 1
        assert "provider_held_narration" not in evidence_kinds
        assert evidence_kinds.count("provider_tool_calls") == 2
        assert evidence_kinds.count("provider_tool_results") == 2
        assert not any(
            message["kind"] == "user" and message["content"].get("text") == "Begin the story"
            for message in context["messages"]
        )
        canonical = [
            message for message in context["messages"] if message["kind"] == "canonical_reference"
        ]
        assert canonical[-1]["content"]["safe_payload"]["text"] == (
            "The accepted campaign plan remains authoritative."
        )
        first_provider_messages = json.dumps(provider.calls[0][0], sort_keys=True)
        assert "# Initial DM Plan" in first_provider_messages
        advertised = {tool["function"]["name"] for tool in (provider.calls[0][1] or [])}
        assert len(advertised) == 3
        assert any(name.endswith("_search_campaign_knowledge") for name in advertised)
        assert any(name.endswith("_read_campaign_reference") for name in advertised)
        assert any(name.endswith("_update_campaign_file") for name in advertised)
        later_tools = {
            tool["function"]["name"]
            for _messages, tools in provider.calls[1:]
            for tool in (tools or [])
        }
        assert any(name.endswith("_update_campaign_file") for name in later_tools)
        assert all(
            name.endswith((
                "_read_campaign_reference", "_search_campaign_knowledge", "_update_campaign_file",
            ))
            for name in later_tools
        )
        snapshot = harness.request(
            "dm-plan-tools-snapshot",
            "snapshot",
            {"campaign_id": campaign_id},
        )
        assert snapshot["writer"] == "dm"
        assert snapshot["revision"] >= revision_before
    finally:
        harness.close()


def test_stop_agent_session_cancels_live_builder_provider_and_fences_output(
    tmp_path: Path,
) -> None:
    provider = _ControlledBlockingBuilderProvider()
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", provider)
    try:
        _campaign_id, session_id = _create_builder_session(harness, "stop-live")
        harness.send(
            "submit-builder-stop-live",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "turn_id": "turn-builder-stop-live",
                    "attempt_id": "attempt-builder-stop-live",
                    "message_id": "message-builder-stop-live",
                    "user_text": "Wait until Stop cancels this Builder turn.",
                },
            },
        )
        # Wait for normal Builder preparation; this test times cancellation after admission.
        assert provider.started.wait(30), "Builder never reached the provider before Stop"

        stopped = harness.request(
            "stop-builder-live",
            "stop_agent_session",
            {"session_id": session_id},
        )
        assert stopped["status"] == "stopped"
        assert provider.cancelled.wait(2)
        submit = harness.read()
        assert submit["id"] == "submit-builder-stop-live"
        assert submit["ok"] is False
        assert submit["error"]["code"] == "command_rejected"
        assert "cancelled" in submit["error"]["message"].lower()

        context = harness.request(
            "builder-stopped-context",
            "session_context",
            {"session_id": session_id},
        )
        assert [message["kind"] for message in context["messages"]] == ["user", "evidence"]
        assert "BUILDER OUTPUT MUST NOT PERSIST" not in json.dumps(context)
    finally:
        provider.release.set()
        harness.close()


def test_stop_after_builder_segment_completion_preserves_completed_talk(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sylvia_gm.interfaces import owner_process

    persist_waiting = threading.Event()
    release_persist = threading.Event()
    original_persist = owner_process.persist_builder_turn

    def paused_persist(*args: Any, **kwargs: Any) -> tuple[object, ...]:
        persist_waiting.set()
        assert release_persist.wait(5)
        return original_persist(*args, **kwargs)

    monkeypatch.setattr(owner_process, "persist_builder_turn", paused_persist)
    provider = _BuilderResponseProvider(())
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", provider)
    try:
        _campaign_id, session_id = _create_builder_session(harness, "persist-race")
        harness.send(
            "submit-builder-persist-race",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "turn_id": "turn-builder-persist-race",
                    "attempt_id": "attempt-builder-persist-race",
                    "message_id": "message-builder-persist-race",
                    "user_text": "Pause after provider completion.",
                },
            },
        )
        assert persist_waiting.wait(2)

        stopped = harness.request(
            "stop-builder-persist-race",
            "stop_agent_session",
            {"session_id": session_id},
        )
        assert stopped["status"] == "stopped"
        release_persist.set()
        submit = harness.read()
        assert submit["id"] == "submit-builder-persist-race"
        assert submit["ok"] is True

        context = harness.request(
            "builder-persist-race-context",
            "session_context",
            {"session_id": session_id},
        )
        assert [message["kind"] for message in context["messages"]] == ["user", "talk", "evidence"]
        assert "BUILDER TALK MUST NOT PERSIST AFTER STOP" in json.dumps(context)
    finally:
        release_persist.set()
        harness.close()


def test_owner_shutdown_cancels_live_builder_provider_and_fences_output(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authority.sqlite3"
    provider = _ControlledBlockingBuilderProvider()
    harness = _OwnerHarness(database, provider)
    session_id = ""
    try:
        _campaign_id, session_id = _create_builder_session(harness, "shutdown-live")
        # Qualify shutdown after provider admission, independently of first catalog materialization.
        harness.request("prepare-shutdown-catalog", "campaign_creation_catalog")
        harness.send(
            "submit-builder-shutdown-live",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "turn_id": "turn-builder-shutdown-live",
                    "attempt_id": "attempt-builder-shutdown-live",
                    "message_id": "message-builder-shutdown-live",
                    "user_text": "Wait until owner shutdown cancels this Builder turn.",
                },
            },
        )
        assert provider.started.wait(2)

        result = harness.request("shutdown-builder-live", "shutdown")
        assert result["status"] == "closed"
        assert provider.cancelled.wait(2)
        harness._thread.join(timeout=2)
        assert not harness._thread.is_alive()

        with AuthorityOwner.open(database, "owner-after-builder-shutdown") as reopened:
            context = reopened.session_context(session_id)
            assert [message.kind.value for message in context.messages] == ["user"]
            assert "BUILDER OUTPUT MUST NOT PERSIST" not in repr(context)
    finally:
        provider.release.set()
        harness._thread.join(timeout=6)
        for stream in (
            harness._client_output,
            harness._client_input,
            harness._owner_input,
            harness._owner_output,
        ):
            stream.close()


def test_owner_shutdown_cancels_blocked_provider_before_closing(tmp_path: Path) -> None:
    provider = _ControlledBlockingProvider()
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", provider)
    try:
        campaign_id, turn_id, session_id = _create_campaign(harness, "shutdown-blocked")
        harness.send(
            "submit-shutdown-blocked",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "attempt_id": "attempt-shutdown-blocked",
                    "message_id": "message-shutdown-blocked",
                    "user_text": "Wait through shutdown.",
                },
            },
        )
        assert provider.started.wait(2)

        result = harness.request("shutdown-blocked", "shutdown")
        assert result["status"] == "closed"
        assert provider.cancelled.wait(2)
        harness._thread.join(timeout=2)
        assert not harness._thread.is_alive()
    finally:
        provider.release.set()
        harness._thread.join(timeout=6)
        for stream in (
            harness._client_output,
            harness._client_input,
            harness._owner_input,
            harness._owner_output,
        ):
            stream.close()


def test_owner_shutdown_overtakes_dm_attempt_before_lease_publication(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    provider = _ControlledBlockingProvider()
    lease_publication_entered = threading.Event()
    release_lease_publication = threading.Event()
    captured_supervisor: list[LocalOwnerSupervisor] = []
    original_track = LocalOwnerSupervisor.track_narration_attempt

    def blocked_track(
        supervisor: LocalOwnerSupervisor,
        campaign_id: str,
        attempt_id: str,
        cancel: Any,
        *,
        turn_id: str = "",
    ) -> None:
        captured_supervisor.append(supervisor)
        lease_publication_entered.set()
        assert release_lease_publication.wait(2)
        original_track(
            supervisor,
            campaign_id,
            attempt_id,
            cancel,
            turn_id=turn_id,
        )

    monkeypatch.setattr(LocalOwnerSupervisor, "track_narration_attempt", blocked_track)
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", provider)
    provider_started_after_shutdown = False
    shutdown_completed_without_provider_cleanup = False
    try:
        _campaign_id, turn_id, session_id = _create_campaign(harness, "dm-pre-lease")
        harness.send(
            "submit-dm-pre-lease",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "attempt_id": "attempt-dm-pre-lease",
                    "message_id": "message-dm-pre-lease",
                    "user_text": "Wait at the lease boundary.",
                },
            },
        )
        assert lease_publication_entered.wait(2)
        assert captured_supervisor

        harness.send("shutdown-dm-pre-lease", {"method": "shutdown", "params": {}})
        for _attempt in range(200):
            if captured_supervisor[0]._shutdown:  # noqa: SLF001 - authority seam probe
                break
            threading.Event().wait(0.005)
        assert captured_supervisor[0]._shutdown  # noqa: SLF001 - authority seam probe

        release_lease_publication.set()
        provider_started_after_shutdown = provider.started.wait(1)
        harness._thread.join(timeout=1)
        shutdown_completed_without_provider_cleanup = not harness._thread.is_alive()
    finally:
        release_lease_publication.set()
        provider.release.set()
        harness._thread.join(timeout=5)
        harness.close()

    assert provider_started_after_shutdown is False
    assert shutdown_completed_without_provider_cleanup is True


def test_stop_agent_session_cancels_live_provider_before_marking_stopped(
    tmp_path: Path,
) -> None:
    provider = _ControlledBlockingProvider()
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", provider)
    try:
        _campaign_id, turn_id, session_id = _create_campaign(harness, "stop-session-live")
        harness.send(
            "submit-stop-session-live",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "attempt_id": "attempt-stop-session-live",
                    "message_id": "message-stop-session-live",
                    "user_text": "Keep waiting.",
                },
            },
        )
        assert provider.started.wait(2)

        stopped = harness.request(
            "stop-session-live",
            "stop_agent_session",
            {"session_id": session_id},
        )
        assert stopped["status"] == "stopped"
        assert provider.cancelled.wait(2)
    finally:
        provider.release.set()
        harness.close()


def test_campaign_start_keeps_owner_transport_available_for_stream_heartbeat(
    tmp_path: Path,
) -> None:
    provider = _ControlledBlockingProvider()
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", provider)
    try:
        campaign_id, turn_id, session_id = _create_campaign(harness, "start-heartbeat")
        attempt_id = (
            "attempt-campaign-start-" + hashlib.sha256(campaign_id.encode("utf-8")).hexdigest()[:24]
        )
        harness.send(
            "start-dm-heartbeat",
            {
                "method": "start_dm_session",
                "params": {
                    "campaign_id": campaign_id,
                    "session_id": session_id,
                    "turn_id": turn_id,
                },
            },
        )
        assert provider.started.wait(2)

        heartbeat = harness.request(
            "campaign-start-heartbeat",
            "stream_heartbeat",
            {"campaign_id": campaign_id, "attempt_id": attempt_id},
        )
        assert heartbeat["attachment_deadline"] > 0
        assert heartbeat["attachment_generation"] > 0

        harness.send(
            "stop-campaign-start",
            {
                "method": "interrupt_stream",
                "params": {"campaign_id": campaign_id, "attempt_id": attempt_id},
            },
        )
        replies = {reply["id"]: reply for reply in (harness.read(), harness.read())}
        assert replies["stop-campaign-start"]["ok"] is True
        start_reply = replies["start-dm-heartbeat"]
        if start_reply["ok"] is False:
            assert "Disconnected narration" not in start_reply["error"]["message"]
        assert provider.cancelled.wait(2)
    finally:
        provider.release.set()
        harness.close()


def test_stop_overtakes_pending_owner_submission_and_cancels_provider(tmp_path: Path) -> None:
    provider = _BlockingProvider(())
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", provider)
    try:
        campaign_id, turn_id, session_id = _create_campaign(harness, "stop")
        harness.send(
            "submit",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "attempt_id": "attempt-stop",
                    "message_id": "message-stop",
                    "user_text": "Wait.",
                },
            },
        )
        assert provider.started.wait(2)
        initial_heartbeat = harness.request(
            "initial-heartbeat",
            "stream_heartbeat",
            {"campaign_id": campaign_id, "attempt_id": "attempt-stop"},
        )
        assert initial_heartbeat["attachment_deadline"] > 0
        attachment_generation = initial_heartbeat["attachment_generation"]
        disconnected = harness.request(
            "disconnect",
            "disconnect_stream",
            {
                "campaign_id": campaign_id,
                "attempt_id": "attempt-stop",
                "attachment_generation": attachment_generation,
            },
        )
        assert disconnected["disconnect_deadline"] > 0
        replay = harness.request(
            "reconnect",
            "reconnect_stream",
            {
                "campaign_id": campaign_id,
                "attempt_id": "attempt-stop",
                "acknowledged_sequence": 0,
                "attachment_generation": attachment_generation,
            },
        )
        assert "canonical" in replay
        assert "provisional" in replay
        reattached = harness.request(
            "reattached-heartbeat",
            "stream_heartbeat",
            {
                "campaign_id": campaign_id,
                "attempt_id": "attempt-stop",
                "attachment_generation": replay["attachment_generation"],
            },
        )
        assert reattached["attachment_generation"] == replay["attachment_generation"]
        assert reattached["attachment_generation"] > attachment_generation
        harness.send(
            "stale-disconnect",
            {
                "method": "disconnect_stream",
                "params": {
                    "campaign_id": campaign_id,
                    "attempt_id": "attempt-stop",
                    "attachment_generation": attachment_generation,
                },
            },
        )
        stale_disconnect = harness.read()
        assert stale_disconnect["ok"] is False
        assert stale_disconnect["error"]["code"] == "command_rejected"
        active_status = harness.request(
            "active-status",
            "narration_attempt_status",
            {"campaign_id": campaign_id, "attempt_id": "attempt-stop"},
        )
        assert active_status == {"status": "active"}
        harness.send(
            "stop",
            {
                "method": "interrupt_stream",
                "params": {
                    "campaign_id": campaign_id,
                    "attempt_id": "attempt-stop",
                },
            },
        )
        replies = {reply["id"]: reply for reply in (harness.read(), harness.read())}
        assert replies["stop"]["ok"] is True
        assert replies["submit"]["ok"] is True
        assert replies["submit"]["result"]["interruption"] is not None
        assert provider.cancelled.wait(2)
        terminal_status = harness.request(
            "terminal-status",
            "narration_attempt_status",
            {"campaign_id": campaign_id, "attempt_id": "attempt-stop"},
        )
        assert terminal_status == {"status": "interrupted"}
        projection = harness.request(
            "projection", "player_projection", {"campaign_id": campaign_id}
        )
        assert "MUST NOT ARRIVE" not in json.dumps(projection)
    finally:
        harness.close()


def test_owner_manual_retry_reuses_action_and_resettles_campaign_file(tmp_path: Path) -> None:
    provider = _FailThenRetryProvider()
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", provider)
    try:
        campaign_id, turn_id, session_id = _create_campaign(harness, "retry")
        harness.send(
            "submit-failed",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "attempt_id": "attempt-failed",
                    "message_id": "message-failed",
                    "user_text": "Continue.",
                },
            },
        )
        failed = harness.read()
        assert failed["id"] == "submit-failed"
        assert failed["ok"] is False

        interruption = harness.request(
            "retry-basis",
            "retryable_interruption",
            {"campaign_id": campaign_id, "failed_attempt_id": "attempt-failed"},
        )
        assert interruption["retryable"] is True
        result = harness.request(
            "manual-retry",
            "manual_retry_session",
            {
                "session_id": session_id,
                "interruption_id": interruption["interruption_id"],
                "retry_id": "attempt-retry",
            },
        )
        assert result["replayed"] is False
        projection = harness.request(
            "projection-after-retry",
            "player_projection",
            {"campaign_id": campaign_id},
        )
        serialized = json.dumps(projection)
        assert "Recovered narration." in serialized
        assert "INCOMPLETE PROSE" not in serialized
        context = harness.request(
            "session-after-retry", "session_context", {"session_id": session_id}
        )
        canonical = [
            message for message in context["messages"] if message["kind"] == "canonical_reference"
        ]
        assert canonical[-1]["content"]["event_type"] == "talk"
        assert canonical[-1]["content"]["safe_payload"] == {
            "text": "Recovered narration.",
            "response_blocks": [{"kind": "narration", "text": "Recovered narration."}],
        }
        # Retry commits the accepted narration in its Campaign File call, in one round.
        assert len(provider.calls) == 2
        assert provider.calls[1][1] is not None
        retry_tool_names = {tool["function"]["name"] for tool in (provider.calls[1][1] or [])}
        assert retry_tool_names
        assert any(name.endswith("_read_campaign_reference") for name in retry_tool_names)
        assert any(name.endswith("_update_campaign_file") for name in retry_tool_names)
        assert all(
            name.endswith((
                "_read_campaign_reference", "_search_campaign_knowledge", "_update_campaign_file",
            ))
            for name in retry_tool_names
        )
        retry_messages = json.dumps(provider.calls[1][0])
        assert [message for message in provider.calls[1][0] if message["role"] == "user"] == [
            {"role": "user", "content": "Continue."},
        ]
        assert "SYLVIA_GM_CURRENT_PLAYER_MESSAGE" in retry_messages
        assert "Transport Plan retry" in retry_messages
        assert "Campaign State is the GM-screen ledger" in retry_messages
        assert "SYLVIA_GM_CANONICAL_RETRY_DATA" not in retry_messages
    finally:
        harness.close()


@pytest.mark.parametrize("failure_boundary", ("publication_rejection", "disconnected"))
def test_retry_publication_failure_preserves_checkpoint_across_owner_restart(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failure_boundary: str,
) -> None:
    """A saved retry response survives failed publication without another model call."""
    provider = _FailThenRetryProvider()
    database = tmp_path / "publication-recovery.sqlite3"
    complete = AuthorityOwner._complete_narration_attempt
    rejected: list[str] = []

    def reject_selected_publication(
        owner: AuthorityOwner, campaign_id: str, attempt_id: str,
    ) -> Any:
        if attempt_id == "retry-publication-fails":
            rejected.append(attempt_id)
            if failure_boundary == "disconnected":
                owner.disconnect_narration(campaign_id, attempt_id, now=0.0)
            else:
                raise CommandRejected("Synthetic interruption after saved Campaign File checkpoint")
        return complete(owner, campaign_id, attempt_id)

    monkeypatch.setattr(AuthorityOwner, "_complete_narration_attempt", reject_selected_publication)
    harness = _OwnerHarness(database, provider)
    try:
        campaign_id, turn_id, session_id = _create_campaign(harness, "publication-recovery")
        harness.send("initial", {"method": "submit_session_input", "params": {
            "session_id": session_id, "turn_id": turn_id,
            "attempt_id": "initial-provider-fails", "message_id": "one-player-input",
            "user_text": "Continue.",
        }})
        assert harness.read()["ok"] is False
        basis = harness.request("initial-basis", "retryable_interruption", {
            "campaign_id": campaign_id, "failed_attempt_id": "initial-provider-fails",
        })
        harness.send("first-retry", {"method": "manual_retry_session", "params": {
            "session_id": session_id, "interruption_id": basis["interruption_id"],
            "retry_id": "retry-publication-fails",
        }})
        failure = harness.read()
        assert failure["ok"] is False
        assert failure["error"]["code"] == "command_rejected"
        assert rejected == ["retry-publication-fails"]
        assert len(provider.calls) == 2
        saved = harness.request("saved-context", "session_context", {"session_id": session_id})
        saved_results = [m for m in saved["messages"]
                         if m["content"].get("kind") == "provider_tool_results"]
        assert len(saved_results) == 1
        assert "campaign_file_unchanged" in json.dumps(saved_results)
        assert "Recovered narration." in json.dumps(saved_results)
        projection = harness.request("before-reopen", "player_projection", {"campaign_id": campaign_id})
        assert "Recovered narration." not in json.dumps(projection)
    finally:
        harness.close()

    harness = _OwnerHarness(database, provider)
    try:
        basis = harness.request("saved-basis", "retryable_interruption", {
            "campaign_id": campaign_id, "failed_attempt_id": "retry-publication-fails",
        })
        assert basis["retryable"] is True
        result = harness.request("recover-saved-reply", "manual_retry_session", {
            "session_id": session_id, "interruption_id": basis["interruption_id"],
            "retry_id": "retry-after-owner-reopen",
        })
        assert result["replayed"] is False
        assert len(provider.calls) == 2
        context = harness.request("recovered-context", "session_context", {"session_id": session_id})
        recovered_results = [m for m in context["messages"]
                             if m["content"].get("kind") == "provider_tool_results"]
        assert recovered_results == saved_results
        talks = [m for m in context["messages"]
                 if m["kind"] == "canonical_reference" and m["content"].get("event_type") == "talk"]
        assert len(talks) == 1
        assert talks[0]["content"]["safe_payload"]["text"] == "Recovered narration."
        assert sum(m["message_id"] == "one-player-input" for m in context["messages"]) == 1
    finally:
        harness.close()


def test_stop_overtakes_blocked_manual_retry_and_discards_retry_prose(tmp_path: Path) -> None:
    provider = _FailThenBlockingRetryProvider()
    harness = _OwnerHarness(tmp_path / "authority.sqlite3", provider)
    try:
        campaign_id, turn_id, session_id = _create_campaign(harness, "blocked-retry")
        harness.send(
            "submit-failed",
            {
                "method": "submit_session_input",
                "params": {
                    "session_id": session_id,
                    "turn_id": turn_id,
                    "attempt_id": "attempt-before-blocked-retry",
                    "message_id": "message-before-blocked-retry",
                    "user_text": "Continue.",
                },
            },
        )
        failed = harness.read()
        assert failed["ok"] is False
        interruption = harness.request(
            "blocked-retry-basis",
            "retryable_interruption",
            {
                "campaign_id": campaign_id,
                "failed_attempt_id": "attempt-before-blocked-retry",
            },
        )

        harness.send(
            "blocked-manual-retry",
            {
                "method": "manual_retry_session",
                "params": {
                    "session_id": session_id,
                    "interruption_id": interruption["interruption_id"],
                    "retry_id": "retry-blocked-owner",
                },
            },
        )
        assert provider.retry_started.wait(timeout=3.0)
        retry_heartbeat = harness.request(
            "retry-heartbeat",
            "stream_heartbeat",
            {"campaign_id": campaign_id, "attempt_id": "retry-blocked-owner"},
        )
        assert retry_heartbeat["attachment_deadline"] > 0
        harness.send(
            "stop-blocked-manual-retry",
            {
                "method": "interrupt_stream",
                "params": {
                    "campaign_id": campaign_id,
                    "attempt_id": "retry-blocked-owner",
                },
            },
        )
        replies = {reply["id"]: reply for reply in (harness.read(), harness.read())}
        assert replies["stop-blocked-manual-retry"]["ok"] is True
        assert replies["blocked-manual-retry"]["ok"] is False
        assert provider.retry_cancelled.wait(timeout=1.0)
        projection = harness.request(
            "projection-after-blocked-retry",
            "player_projection",
            {"campaign_id": campaign_id},
        )
        assert [item for item in projection if item["kind"] == "talk"] == []
        assert "DISCARD FAILED PROSE" not in json.dumps(projection)
        assert next(item for item in projection if item["kind"] == "retry")[
            "safe_payload"
        ]["can_redirect"] is True
        redirected = harness.request("redirect-after-stop", "submit_session_input", {
            "session_id": session_id, "turn_id": turn_id,
            "attempt_id": "redirect-attempt", "message_id": "redirect-input",
            "user_text": "Talk instead.", "ooc_text": "Keep it brief.",
            "redirect_interruption_id": interruption["interruption_id"],
        })
        assert redirected["interruption"] is None
        assert provider.calls == 3
        rendered = json.dumps(provider.redirect_messages)
        assert "SYLVIA_GM_REDIRECTED_CONTINUATION" in rendered
        assert "Talk instead." in rendered and "Keep it brief." in rendered
        after = harness.request("after-redirection", "player_projection", {
            "campaign_id": campaign_id,
        })
        assert not any(item["kind"] == "retry" for item in after)
        assert "Redirected response." in json.dumps(after)
    finally:
        harness.close()


def test_builder_provider_gets_initial_attachment_window_before_first_heartbeat() -> None:
    cancelled = threading.Event()
    supervisor = LocalOwnerSupervisor(
        attachment_lease_seconds=0.05,
        initial_attachment_lease_seconds=0.30,
        disconnect_grace_seconds=0.05,
    )
    try:
        supervisor.track_provider_work("builder-session", "attempt", cancelled.set)

        # A newly published provider attempt may race a busy renderer before its first beat.
        assert not cancelled.wait(0.15)
        assert supervisor.provider_work_status("builder-session", "attempt") == "active"

        _deadline, generation = supervisor.heartbeat_provider_work(
            "builder-session", "attempt"
        )
        assert generation == 1

        # Once attached, the ordinary short renewal lease and disconnect grace apply.
        assert cancelled.wait(1.0)
        assert supervisor.provider_work_status("builder-session", "attempt") == "disconnected"
    finally:
        supervisor.release_provider_work("builder-session", "attempt")
        supervisor.shutdown()
