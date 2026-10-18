"""Bind provider-protocol request metadata without mutating shared clients."""

from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator, Mapping
from typing import Any

from sylvia_gm import __version__
from sylvia_gm.provider._base import (
    Provider,
    ProviderResponse,
    ProviderStreamChunk,
    closing_provider_stream,
)

_OPENCODE_SESSION_HEADER = "x-opencode-session"
_OPENCODE_CLIENT_HEADER = "x-opencode-client"
_USER_AGENT_HEADER = "User-Agent"
_OPENCODE_PROTOCOL_HEADER_NAMES = frozenset(
    {
        _OPENCODE_SESSION_HEADER.casefold(),
        _OPENCODE_CLIENT_HEADER.casefold(),
        _USER_AGENT_HEADER.casefold(),
    }
)


def opencode_request_headers(session_id: str) -> dict[str, str]:
    """Return opaque, stable OpenCode request metadata for one durable Sylvia session."""

    digest = hashlib.sha256(session_id.encode("utf-8")).hexdigest()
    return {
        _OPENCODE_SESSION_HEADER: f"sylvia_{digest}",
        _OPENCODE_CLIENT_HEADER: "sylvia-gm",
        _USER_AGENT_HEADER: f"Sylvia-GM/{__version__}",
    }


def without_opencode_protocol_headers(headers: Mapping[str, str]) -> dict[str, str]:
    """Remove static values whose OpenCode meaning must be owned per request by Sylvia."""

    return {
        str(name): str(value)
        for name, value in headers.items()
        if str(name).casefold() not in _OPENCODE_PROTOCOL_HEADER_NAMES
    }


def _with_protocol_headers(
    options: Mapping[str, object],
    protocol_headers: Mapping[str, str],
) -> dict[str, object]:
    """Merge one call's extra headers with protocol-owned values taking precedence."""

    result = dict(options)
    configured = result.get("extra_headers")
    configured_items = configured.items() if isinstance(configured, Mapping) else ()
    merged = {
        str(name): str(value)
        for name, value in configured_items
        if str(name).casefold() not in _OPENCODE_PROTOCOL_HEADER_NAMES
    }
    merged.update(protocol_headers)
    result["extra_headers"] = merged
    return result


class SessionBoundRequestMetadataProvider(Provider):
    """Attach immutable per-session request metadata to a shared provider client."""

    def __init__(self, provider: Provider, *, headers: Mapping[str, str]) -> None:
        super().__init__()
        self._provider = provider
        self._headers = dict(headers)
        self.model_info = getattr(provider, "model_info", None)
        self.hooks = provider.hooks
        self._base_url = getattr(provider, "_base_url", None)

    def provider_request_representation(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        options: Mapping[str, object],
    ) -> dict[str, Any]:
        """Include the same per-request headers in previews used for fit and Full Audit."""

        preview = getattr(self._provider, "provider_request_representation", None)
        if not callable(preview):
            return {
                "messages": messages,
                "tools": tools or [],
                "options": _with_protocol_headers(options, self._headers),
            }
        value: dict[str, Any] = preview(
            messages,
            tools,
            _with_protocol_headers(options, self._headers),
        )
        return value

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        return await self._provider.generate(
            messages,
            tools=tools,
            stream=stream,
            timeout=timeout,
            **_with_protocol_headers(kwargs, self._headers),
        )

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        async with closing_provider_stream(self._provider.generate_stream(
            messages,
            tools=tools,
            timeout=timeout,
            **_with_protocol_headers(kwargs, self._headers),
        )) as stream:
            async for chunk in stream:
                yield chunk

    async def close(self) -> None:
        await self._provider.close()
