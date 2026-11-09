"""Subscription inference through Codex, with Sylvia retaining the tool loop.

Each call uses an ephemeral runtime thread loaded from Sylvia's authoritative
history. At the first Sylvia tool request the runtime is stopped before returning
the call to the existing owner. Codex never executes a Sylvia operation itself.
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sylvia_gm.provider._base import (
    CacheUsage,
    ModelInfo,
    Provider,
    ProviderResponse,
    ProviderStreamChunk,
    TokenUsage,
    ToolCall,
)
from sylvia_gm.provider._codex_rpc import (
    CodexCancelledError,
    CodexConnection,
    CodexConnectionError,
)
from sylvia_gm.provider._context_capacity import resolve_context_capacity
from sylvia_gm.provider._exceptions import (
    AuthenticationError,
    ContextCompactionRequiredError,
    InvalidResponseError,
    ModelCapacityError,
    ModelNotFoundError,
    ProviderConnectionError,
    ProviderError,
    ProviderTimeoutError,
    RateLimitError,
    TokenLimitError,
)
from sylvia_gm.provider._hooks import HookContext
from sylvia_gm.provider._lifecycle import LifecycleTiming
from sylvia_gm.provider._openai_compat import ResponsesTransport


class ChatGPTProvider(Provider):
    """Adapt one runtime generation to Sylvia's existing streaming provider interface."""

    def __init__(
        self,
        home: Path,
        model_info: ModelInfo,
        *,
        executable: Path | None = None,
        connection_factory: Callable[..., CodexConnection] = CodexConnection,
    ) -> None:
        super().__init__()
        self.home = home
        self._model_info = model_info
        self._executable = executable
        self._connection_factory = connection_factory
        self._lock = asyncio.Lock()
        self._active: CodexConnection | None = None
        self._last_response = ProviderResponse()

    def _request_stop(self, connection: CodexConnection) -> None:
        cancel = getattr(connection, "request_stop", None)
        if callable(cancel):
            cancel()

    def _check_stopped(self, connection: CodexConnection) -> None:
        check = getattr(connection, "_check_stopped", None)
        if callable(check):
            check()

    async def _close_owned(self, connection: CodexConnection) -> None:
        """Collect the retained child before propagating repeated caller cancellation."""
        closing = asyncio.create_task(asyncio.to_thread(connection.close))
        interrupted: asyncio.CancelledError | None = None
        while not closing.done():
            try:
                await asyncio.shield(closing)
            except asyncio.CancelledError as error:
                interrupted = error
                self._request_stop(connection)
        closing.result()
        if interrupted is not None:
            raise interrupted

    @property
    def model_info(self) -> ModelInfo:
        return self._model_info

    def _observe_context_window(self, value: object) -> None:
        """Retain the runtime's reported window without increasing a configured operating cap."""
        if type(value) is not int or value <= 0:
            return
        model = self._model_info
        observed_at = datetime.now(UTC).isoformat()
        capacity = resolve_context_capacity(
            declared_context_window=model.declared_context_window,
            declared_context_source=model.declared_context_source,
            runtime_context_window=value,
            runtime_context_source="runtime_reported",
            configured_context_window=model.configured_context_window,
            declared_observed_at=model.declared_context_observed_at,
            runtime_observed_at=observed_at,
            configured_observed_at=model.configured_context_observed_at,
        )
        # Wrappers retain this ModelInfo object, so update it rather than replacing it.
        model.runtime_context_window = value
        model.runtime_context_source = "runtime_reported"
        model.runtime_context_observed_at = observed_at
        model.context_observed_at = observed_at
        model.context_window = capacity.effective_context_window
        model.context_window_source = capacity.effective_context_source
        model.configured_runtime_mismatch = capacity.configured_runtime_mismatch

    async def _audit(self, method: str, params: dict[str, Any], *, received: bool = False) -> None:
        """Capture the actual runtime boundary without claiming downstream wire visibility."""
        await self.hooks.fire_on_request(
            HookContext(
                provider_key="chatgpt_subscription",
                model_id=self.model_info.id,
                stream=True,
                request_json={
                    "direction": "receive" if received else "send",
                    "method": method,
                    "params": params,
                    "downstream_model_request": "not_exposed_by_runtime",
                },
            )
        )

    def _capture_usage(
        self, response: ProviderResponse, params: dict[str, Any],
        thread_id: str | None, turn_id: str | None,
    ) -> None:
        """Use the latest owned last-request report; cumulative thread totals are not additive."""
        if (not thread_id or not turn_id or params.get("threadId") != thread_id
                or params.get("turnId") != turn_id):
            return
        token_usage = params.get("tokenUsage")
        if not isinstance(token_usage, dict):
            return
        self._observe_context_window(token_usage.get("modelContextWindow"))
        last = token_usage.get("last")
        if not isinstance(last, dict):
            return

        def count(key: str) -> int | None:
            value = last.get(key)
            return value if type(value) is int and value >= 0 else None

        prompt, output, total = (
            count(key) for key in ("inputTokens", "outputTokens", "totalTokens")
        )
        if prompt is None or output is None or total is None:
            return
        response.usage = TokenUsage(prompt, output, total, count("reasoningOutputTokens"))
        response.usage_reported = True
        read, write = count("cachedInputTokens"), count("cacheWriteInputTokens")
        response.cache_usage = CacheUsage(
            cache_read_tokens=read, cache_write_tokens=write,
            cache_miss_tokens=None if read is None else max(0, prompt - read),
            total_prompt_tokens=prompt,
        )

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60,
        **kwargs: object,
    ) -> ProviderResponse:
        async for _ in self.generate_stream(messages, tools, timeout, **kwargs):
            pass
        return self._last_response

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        async with self._lock:
            timing = LifecycleTiming()
            converted = ResponsesTransport().format_request(
                self.model_info.wire_model_id,
                messages,
                tools,
                True,
            )
            dynamic = [
                {
                    "type": "function",
                    "name": tool["name"],
                    "description": tool["description"],
                    "inputSchema": tool["parameters"],
                }
                for tool in converted.get("tools", [])
            ]
            allowed = frozenset(tool["name"] for tool in dynamic)
            items = []
            system_instructions: list[str] = []
            for original in converted["input"]:
                item = dict(original)
                # App Server excludes injected system messages; its dedicated field owns them.
                if item.get("role") == "system":
                    system_instructions.append(item["content"])
                    continue
                if item.get("type") == "function_call":
                    item["namespace"] = "sylvia"
                if "role" in item:
                    role = item["role"]
                    item["type"] = "message"
                    item["content"] = [
                        {
                            "type": "output_text" if role == "assistant" else "input_text",
                            "text": item["content"],
                        }
                    ]
                items.append(item)
            connection = self._connection_factory(
                self.home,
                self._executable,
                dynamic_tools=allowed,
            )
            self._active = connection
            observe = getattr(connection, "set_lifecycle_observer", None)
            if callable(observe):
                observe(lambda name, start, end, status: timing.record(
                    name, start, end, status, parent="runtime_start",
                ))
            response = ProviderResponse(model=self.model_info.wire_model_id, usage_reported=False)
            text_parts: list[str] = []
            thread_id: str | None = None
            turn_id: str | None = None
            status = "error"
            stream_start: int | None = None

            def end_stream(outcome: str) -> None:
                nonlocal stream_start
                if stream_start is not None:
                    timing.record("turn_stream", stream_start, time.perf_counter_ns(), outcome)
                    stream_start = None

            async def stop() -> None:
                with timing.measure("shutdown"):
                    await self._close_owned(connection)

            try:
                with timing.measure("runtime_start"):
                    await asyncio.to_thread(connection.start)
                await self._audit(
                    "sylvia/runtime_controls",
                    {
                        "managed_by_runtime": ["max_tokens", "temperature", "tool_choice"],
                        "application_requested": {
                            key: kwargs[key]
                            for key in ("max_tokens", "temperature", "tool_choice")
                            if key in kwargs
                        },
                    },
                )
                with timing.measure("account_read"):
                    account = await asyncio.to_thread(
                        connection.call, "account/read", {"refreshToken": False}
                    )
                if (
                    not isinstance(account.get("account"), dict)
                    or account["account"].get("type") != "chatgpt"
                ):
                    raise AuthenticationError(
                        "Sign in to ChatGPT in Sylvia Settings to use this model."
                    )
                start = {
                    "model": self.model_info.wire_model_id,
                    "allowProviderModelFallback": False,
                    "ephemeral": True,
                    "environments": [],
                    "selectedCapabilityRoots": [],
                    "baseInstructions": "\n\n".join(system_instructions)
                    or "Follow the supplied Sylvia instructions and history.",
                    "approvalPolicy": "never",
                    "dynamicTools": [
                        {
                            "type": "namespace",
                            "name": "sylvia",
                            "description": "Authorized Sylvia operations.",
                            "tools": dynamic,
                        }
                    ]
                    if dynamic
                    else [],
                }
                await self._audit("thread/start", start)
                with timing.measure("thread_start"):
                    thread = await asyncio.to_thread(connection.call, "thread/start", start)
                await self._audit("thread/start/result", thread, received=True)
                actual_model = thread.get("model")
                if isinstance(actual_model, str) and actual_model != self.model_info.wire_model_id:
                    raise ModelNotFoundError("ChatGPT did not select the requested model.")
                thread_id = thread["thread"]["id"]
                inject = {"threadId": thread_id, "items": items}
                await self._audit("thread/inject_items", inject)
                with timing.measure("history_injection"):
                    injected = await asyncio.to_thread(
                        connection.call, "thread/inject_items", inject,
                    )
                await self._audit("thread/inject_items/result", injected, received=True)
                turn: dict[str, Any] = {
                    "threadId": thread_id,
                    "input": [],
                    "environments": [],
                    "model": self.model_info.wire_model_id,
                }
                if kwargs.get("reasoning_effort") is not None:
                    turn["effort"] = kwargs["reasoning_effort"]
                await self._audit("turn/start", turn)
                with timing.measure("turn_start"):
                    started = await asyncio.to_thread(connection.call, "turn/start", turn)
                await self._audit("turn/start/result", started, received=True)
                turn_id = started["turn"]["id"]
                last_event = time.monotonic()
                stream_start = time.perf_counter_ns()
                while True:
                    event = await asyncio.to_thread(connection.next_event)
                    if event is None:
                        if time.monotonic() - last_event > timeout:
                            raise ProviderTimeoutError(
                                "The ChatGPT response stopped making progress."
                            )
                        continue
                    method, params = event.get("method"), event.get("params", {})
                    if not isinstance(params, dict) or params.get("threadId") not in {
                        None,
                        thread_id,
                    }:
                        continue
                    if params.get("turnId") not in {None, turn_id}:
                        continue
                    last_event = time.monotonic()
                    # Account notifications are settings metadata, never campaign audit content.
                    if isinstance(method, str) and not method.startswith(
                        ("account/", "remoteControl/")
                    ):
                        await self._audit(method, params, received=True)
                    self._check_stopped(connection)
                    if method == "model/rerouted":
                        raise ModelNotFoundError(
                            "ChatGPT routed to a different model; Sylvia stopped the response."
                        )
                    if (
                        method == "item/started"
                        and isinstance(params.get("item"), dict)
                        and params["item"].get("type") == "contextCompaction"
                    ):
                        raise ContextCompactionRequiredError(
                            "ChatGPT requested context compaction. Sylvia must prepare a "
                            "smaller working context; saved history is retained."
                        )
                    if method == "item/agentMessage/delta":
                        delta = params.get("delta")
                        if isinstance(delta, str):
                            text_parts.append(delta)
                            yield ProviderStreamChunk(content=delta)
                    elif method in {"item/reasoning/textDelta", "item/reasoning/summaryTextDelta"}:
                        delta = params.get("delta")
                        if isinstance(delta, str):
                            yield ProviderStreamChunk(reasoning_content=delta)
                    elif method == "item/tool/call":
                        name, call_id = params.get("tool"), params.get("callId")
                        if (
                            not isinstance(name, str)
                            or name not in allowed
                            or params.get("namespace") != "sylvia"
                            or not isinstance(call_id, str)
                        ):
                            raise InvalidResponseError(
                                "ChatGPT requested an unavailable Sylvia operation."
                            )
                        arguments = json.dumps(
                            params.get("arguments"), ensure_ascii=True, allow_nan=False
                        )
                        response.tool_calls = [ToolCall(id=call_id, name=name, arguments=arguments)]
                        response.finish_reason = "tool_calls"
                        # Stop the runtime before the owner can execute this returned call.
                        end_stream("completed")
                        await stop()
                        self._check_stopped(connection)
                        yield ProviderStreamChunk(
                            tool_call_delta={
                                "index": 0,
                                "id": call_id,
                                "type": "function",
                                "function": {"name": name, "arguments": arguments},
                            }
                        )
                        break
                    elif method == "thread/tokenUsage/updated":
                        self._capture_usage(response, params, thread_id, turn_id)
                    elif method == "turn/completed":
                        completed = params.get("turn", {})
                        if completed.get("status") != "completed":
                            error = completed.get("error") or {}
                            code = error.get("codexErrorInfo") if isinstance(error, dict) else None
                            if code == "serverOverloaded":
                                raise ModelCapacityError(
                                    "The selected model is at capacity. Saved work is kept. "
                                    "Try again later or choose another model for this chat."
                                )
                            if code == "usageLimitExceeded":
                                raise RateLimitError(
                                    "ChatGPT subscription allowance is unavailable. "
                                    "Try after it resets."
                                )
                            if code == "contextWindowExceeded":
                                raise TokenLimitError(
                                    "ChatGPT rejected this request because its context window "
                                    "was exceeded. Saved conversation and completed work remain."
                                )
                            raise ProviderError(
                                "ChatGPT did not complete this response. Saved work is retained."
                            )
                        break
                # Tool admission has already stopped this owned child. Preserve any usage
                # notification received around shutdown without starting another model call.
                end_stream("completed")
                await stop()
                drain = getattr(connection, "notifications", None)
                if callable(drain):
                    for pending in drain():
                        if (pending.get("method") == "thread/tokenUsage/updated"
                                and isinstance(pending.get("params"), dict)):
                            self._capture_usage(response, pending["params"], thread_id, turn_id)
                response.content = "".join(text_parts) or None
                await self.hooks.fire_on_response(
                    HookContext(
                        provider_key="chatgpt_subscription",
                        model_id=self.model_info.id,
                        response=response,
                        stream=True,
                    )
                )
                self._check_stopped(connection)
                self._last_response = response
                status = "completed"
                yield ProviderStreamChunk(finish_reason=response.finish_reason)
            except (asyncio.CancelledError, GeneratorExit):
                if status != "completed":
                    status = "cancelled"
                    self._request_stop(connection)
                raise
            except CodexCancelledError:
                if status != "completed":
                    status = "cancelled"
                raise asyncio.CancelledError("The ChatGPT response was cancelled.") from None
            except CodexConnectionError as error:
                raise ProviderConnectionError(str(error)) from None
            finally:
                # Closing the owned child cancels unresolved runtime calls and ephemeral history.
                end_stream(status)
                try:
                    await stop()
                finally:
                    self._active = None
                    await self._audit("sylvia/provider_lifecycle", timing.snapshot(status))

    async def close(self) -> None:
        connection = self._active
        if connection is not None:
            self._request_stop(connection)
            await self._close_owned(connection)
