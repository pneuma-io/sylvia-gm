"""Opt-in, current-process capture of exact provider requests and responses."""

from __future__ import annotations

import dataclasses
import datetime as dt
import hashlib
import json
import time
import uuid
from collections.abc import AsyncIterator, Callable, Mapping, Sequence
from enum import Enum
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from sylvia_gm.provider._base import (
    Provider,
    ProviderResponse,
    ProviderStreamChunk,
    closing_provider_stream,
)
from sylvia_gm.provider._hooks import HookContext


def _plain(value: object) -> object:
    """Convert provider DTOs to bounded JSON-shaped trace values without invoking repr hooks."""

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _plain(dataclasses.asdict(value))
    if isinstance(value, Enum):
        return _plain(value.value)
    if isinstance(value, Mapping):
        return {str(key): _plain(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_plain(item) for item in value]
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    return str(value)


def _usage_projection(response: ProviderResponse | None) -> dict[str, object]:
    """Distinguish provider-reported token counts from absent usage metadata."""

    usage = response.usage if response is not None else None
    cache_usage = response.cache_usage if response is not None else None
    reported = (
        response.usage_reported
        if response is not None and response.usage_reported is not None
        else (
            response is not None
            and usage is not None
            and any(
                value not in {None, 0}
                for value in (
                    usage.prompt_tokens,
                    usage.completion_tokens,
                    usage.total_tokens,
                    usage.reasoning_tokens,
                )
            )
        )
    )
    return {
        "availability": "provider_returned" if reported else "not_returned",
        "precision": "authoritative_count" if reported else "unavailable",
        "source": "provider_reported_completed_request" if reported else "unavailable",
        "scope": "completed_request_only" if reported else "unavailable",
        "input_tokens": usage.prompt_tokens if reported and usage is not None else None,
        "output_tokens": usage.completion_tokens if reported and usage is not None else None,
        "total_tokens": usage.total_tokens if reported and usage is not None else None,
        "cached_tokens": (
            cache_usage.cache_read_tokens if reported and cache_usage is not None else None
        ),
        "reasoning_tokens": usage.reasoning_tokens if reported and usage is not None else None,
    }


def _serialization_projection(value: object, *, layer: str) -> dict[str, object]:
    """Measure one exact Sylvia-owned canonical serialization layer."""

    encoded = json.dumps(
        _plain(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    return {
        "layer": layer,
        "encoding": "canonical_json_utf8",
        "bytes": len(encoded),
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "claim_boundary": "sylvia_owned_representation",
    }


class _CompletedResponseObserver:
    """Capture the transport request and completed response for one audited call."""

    def __init__(
        self,
        *,
        sink: Callable[[object], None],
        session_id: str,
        role: str,
    ) -> None:
        self._sink = sink
        self._session_id = session_id
        self._role = role
        self._exchange_id: str | None = None
        self._correlation: dict[str, object] = {}
        self.response: ProviderResponse | None = None
        self.retry_attempt = 0

    def begin(self, exchange_id: str, correlation: Mapping[str, object]) -> None:
        """Associate subsequent provider hooks with the application request trace."""

        self._exchange_id = exchange_id
        self._correlation = {str(key): _plain(value) for key, value in correlation.items()}
        self.response = None
        self.retry_attempt = 0

    async def on_request(self, context: HookContext) -> None:
        self.retry_attempt = context.retry_attempt
        if self._exchange_id is not None and context.request_json is not None:
            request = _plain(context.request_json)
            runtime = context.provider_key == "chatgpt_subscription"
            self._sink(
                {
                    "kind": "provider_runtime_exchange" if runtime else "provider_wire_request",
                    "input_stage": "codex_app_server_boundary"
                    if runtime
                    else "provider_specific_wire_request",
                    "exchange_id": self._exchange_id,
                    "session_id": self._session_id,
                    "role": self._role,
                    "retry_attempt": context.retry_attempt,
                    "request": request,
                    "serialization": _serialization_projection(
                        request,
                        layer="codex_app_server_boundary"
                        if runtime
                        else "provider_client_request_representation",
                    ),
                    "correlation": self._correlation,
                }
            )

    async def on_response(self, context: HookContext) -> None:
        self.response = context.response
        self.retry_attempt = context.retry_attempt

    async def on_error(self, context: HookContext) -> None:
        self.retry_attempt = context.retry_attempt


class AdminAuditedProvider(Provider):
    """Mirror exact model I/O to a private in-memory sink when Admin Full Audit is enabled."""

    def __init__(
        self,
        provider: Provider,
        *,
        sink: Callable[[object], None],
        session_id: str,
        role: str,
        provider_id: str = "unavailable",
        model_id: str = "unavailable",
        selection_mode: str = "session",
    ) -> None:
        super().__init__()
        self._provider = provider
        self._sink = sink
        self._session_id = session_id
        self._role = role
        self._provider_id = provider_id
        self._model_id = model_id
        self._selection_mode = selection_mode
        self.model_info = getattr(provider, "model_info", None)
        self._audit_context: dict[str, object] = {}

    def _response_observer(self) -> _CompletedResponseObserver:
        """Create one observer whose lifetime and correlation belong to one provider call."""

        return _CompletedResponseObserver(
            sink=self._sink,
            session_id=self._session_id,
            role=self._role,
        )

    def set_audit_context(self, context: Mapping[str, object]) -> None:
        """Attach application/MCP correlation metadata to the next provider request."""

        self._audit_context = {str(key): _plain(value) for key, value in context.items()}

    def record_context_fit(self, context: Mapping[str, object]) -> None:
        """Record preflight even when a rejected request never reaches the transport."""

        self._sink(
            {
                "kind": "provider_context_fit",
                "session_id": self._session_id,
                "role": self._role,
                "provider": self._provider_metadata(),
                "correlation": _plain(context),
            }
        )

    def _provider_metadata(self) -> dict[str, object]:
        """Project provider identity without credentials, headers, or URL query values."""

        model_info = self.model_info
        raw_endpoint = getattr(self._provider, "_base_url", None)
        endpoint = "unavailable"
        if isinstance(raw_endpoint, str) and raw_endpoint:
            parts = urlsplit(raw_endpoint)
            if parts.scheme and parts.netloc:
                host = parts.hostname or ""
                if ":" in host and not host.startswith("["):
                    host = f"[{host}]"
                authority = host if parts.port is None else f"{host}:{parts.port}"
                endpoint = urlunsplit((parts.scheme, authority, parts.path, "", ""))
        return {
            "provider_id": self._provider_id,
            "provider_name": getattr(model_info, "provider_name", "unavailable"),
            "model_id": self._model_id,
            "wire_model_id": getattr(model_info, "wire_model_id", "unavailable"),
            "transport": getattr(model_info, "transport", "unavailable"),
            "endpoint": endpoint,
            "selection_mode": self._selection_mode,
        }

    def _request(
        self,
        exchange_id: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        options: Mapping[str, object],
    ) -> float:
        started_at = dt.datetime.now(dt.UTC).isoformat()
        request = {
            "messages": _plain(messages),
            "tools": _plain(tools or []),
            "options": _plain(options),
        }
        self._sink(
            {
                "kind": "provider_request",
                "input_stage": "application_provider_request",
                "exchange_id": exchange_id,
                "session_id": self._session_id,
                "role": self._role,
                "started_at_utc": started_at,
                "provider": self._provider_metadata(),
                **request,
                "serialization": _serialization_projection(
                    request,
                    layer="neutral_application_request",
                ),
                "correlation": self._audit_context,
            }
        )
        self._audit_context = {}
        return time.perf_counter()

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        exchange_id = f"audit-{uuid.uuid4().hex}"
        observer = self._response_observer()
        observer.begin(exchange_id, self._audit_context)
        started = self._request(
            exchange_id,
            messages,
            tools,
            {"stream": stream, "timeout": timeout, **kwargs},
        )
        try:
            with self._provider.hooks.scoped(observer):
                response = await self._provider.generate(
                    messages,
                    tools=tools,
                    stream=stream,
                    timeout=timeout,
                    **kwargs,
                )
        except BaseException as error:
            self._sink(
                {
                    "kind": "provider_error",
                    "exchange_id": exchange_id,
                    "error_type": type(error).__name__,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                }
            )
            raise
        self._sink(
            {
                "kind": "provider_response",
                "exchange_id": exchange_id,
                "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                "response": _plain(response),
                "usage": _usage_projection(response),
                "retry_count": "unavailable",
            }
        )
        return response

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        exchange_id = f"audit-{uuid.uuid4().hex}"
        observer = self._response_observer()
        observer.begin(exchange_id, self._audit_context)
        started = self._request(
            exchange_id,
            messages,
            tools,
            {"stream": True, "timeout": timeout, **kwargs},
        )
        chunks: list[object] = []
        content_parts: list[str] = []
        reasoning_parts: list[str] = []
        tool_call_deltas: list[object] = []
        finish_reason: str | None = None
        reported_effort: str | None = None
        status = "completed"
        try:
            with self._provider.hooks.scoped(observer):
                async with closing_provider_stream(self._provider.generate_stream(
                    messages,
                    tools=tools,
                    timeout=timeout,
                    **kwargs,
                )) as stream:
                    async for chunk in stream:
                        chunks.append(_plain(chunk))
                        if chunk.content:
                            content_parts.append(chunk.content)
                        if chunk.reasoning_content:
                            reasoning_parts.append(chunk.reasoning_content)
                        if chunk.tool_call_delta is not None:
                            tool_call_deltas.append(_plain(chunk.tool_call_delta))
                        if chunk.finish_reason is not None:
                            finish_reason = chunk.finish_reason
                        if chunk.provider_reported_reasoning_effort is not None:
                            reported_effort = chunk.provider_reported_reasoning_effort
                        yield chunk
        except BaseException as error:
            status = "interrupted" if type(error).__name__ == "CancelledError" else "failed"
            chunks.append({"error_type": type(error).__name__})
            raise
        finally:
            completed = observer.response
            self._sink(
                {
                    "kind": "provider_stream_response",
                    "exchange_id": exchange_id,
                    "status": status,
                    "duration_ms": round((time.perf_counter() - started) * 1000, 3),
                    "chunks": chunks,
                    "assembled_response": {
                        "content": "".join(content_parts),
                        "reasoning_content": "".join(reasoning_parts),
                        "tool_call_deltas": tool_call_deltas,
                        "finish_reason": finish_reason,
                        "provider_reported_reasoning_effort": reported_effort,
                    },
                    "usage": _usage_projection(completed),
                    "retry_count": observer.retry_attempt,
                }
            )

    async def close(self) -> None:
        await self._provider.close()
