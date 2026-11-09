"""OpenAI-compatible provider and transport implementation.

Handles ALL providers that expose an OpenAI-compatible API:
BYOK (OpenCode Go, OpenRouter, Anthropic via proxy) and local Ollama.

Integrates retry-with-backoff via the failover layer, inactivity
timeouts via asyncio.wait_for, and maps HTTP errors to the provider
exception hierarchy.

The ChatCompletionsTransport handles message formatting and response
parsing. OpenAICompatProvider owns the session-scoped HTTP client and
delegates to the transport.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time as _time
from collections.abc import AsyncIterator, Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any, cast

import httpx
from openai import APIConnectionError, APIStatusError, APITimeoutError, AsyncOpenAI

from sylvia_gm.provider._base import (
    CacheUsage,
    ModelInfo,
    Provider,
    ProviderResponse,
    ProviderStreamChunk,
    ProviderTransport,
    RetryConfig,
    TimeoutConfig,
    TokenUsage,
    ToolCall,
)
from sylvia_gm.provider._chat_metadata import ChatCompletionMetadata
from sylvia_gm.provider._credentials import Credential, CredentialPool
from sylvia_gm.provider._exceptions import (
    AuthenticationError,
    IncompleteProviderStreamError,
    InvalidRequestError,
    InvalidResponseError,
    ProviderConnectionError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
    TokenLimitError,
    is_context_rejection,
)
from sylvia_gm.provider._failover import retry_with_backoff
from sylvia_gm.provider._hooks import HookContext
from sylvia_gm.provider._native_output import (
    DoneTrackingStream,
    native_output,
    responses_finish,
    split_chat_content,
)
from sylvia_gm.provider._retry_after import retry_after_seconds

logger = logging.getLogger(__name__)

_CREDENTIALLESS_API_KEY = "sylvia-gm-local"


class _InlineReasoningFilter:
    """Separate provider-emitted channel markup from player-visible content.

    Some OpenAI-compatible local servers return model-native reasoning channel
    markers inside ``delta.content`` instead of exposing ``reasoning_content``.
    The filter is stream-local so split markers cannot leak a partial private
    channel into Sylvia's TALK stream.
    """

    _MARKERS: tuple[tuple[str, bool | None], ...] = (
        ("<|channel|>analysis", True),
        ("<|channel|>thought", True),
        ("<|channel|>reasoning", True),
        ("<thinking>", True),
        ("<reasoning>", True),
        ("<think>", True),
        ("<mm:think>", True),
        ("<thought>", True),
        ("<reasoning_scratchpad>", True),
        ("<|channel|>final", False),
        ("<|channel|>commentary", False),
        ("</thinking>", False),
        ("</reasoning>", False),
        ("</think>", False),
        ("</mm:think>", False),
        ("</thought>", False),
        ("</reasoning_scratchpad>", False),
        ("<|start|>assistant", None),
        ("<|message|>", None),
        ("<|end|>", None),
    )

    def __init__(self) -> None:
        self._buffer = ""
        self._private = False

    def feed(self, text: str, *, final: bool = False) -> tuple[str, str]:
        """Return visible and private text while retaining split marker tails."""

        self._buffer += text
        visible: list[str] = []
        private: list[str] = []
        while self._buffer:
            lowered = self._buffer.casefold()
            found = [
                (index, marker, private_mode)
                for marker, private_mode in self._MARKERS
                if (index := lowered.find(marker.casefold())) >= 0
            ]
            if found:
                index, marker, private_mode = min(found, key=lambda item: item[0])
                self._append(self._buffer[:index], visible, private)
                self._buffer = self._buffer[index + len(marker) :]
                if private_mode is not None:
                    self._private = private_mode
                continue

            held = 0 if final else self._partial_marker_suffix(lowered)
            ready = self._buffer if held == 0 else self._buffer[:-held]
            self._append(ready, visible, private)
            self._buffer = "" if held == 0 else self._buffer[-held:]
            break
        return "".join(visible), "".join(private)

    def filter_chunk(self, chunk: ProviderStreamChunk) -> ProviderStreamChunk:
        """Move inline private-channel text to ``reasoning_content``."""

        final = chunk.finish_reason is not None or chunk.tool_call_delta is not None
        content, inline_reasoning = self.feed(chunk.content, final=final)
        return replace(
            chunk,
            content=content,
            reasoning_content=chunk.reasoning_content + inline_reasoning,
        )

    def _append(self, text: str, visible: list[str], private: list[str]) -> None:
        if not text:
            return
        (private if self._private else visible).append(text)

    @classmethod
    def _partial_marker_suffix(cls, lowered: str) -> int:
        max_length = min(len(lowered), max(len(marker) for marker, _ in cls._MARKERS) - 1)
        for length in range(max_length, 0, -1):
            suffix = lowered[-length:]
            if any(marker.casefold().startswith(suffix) for marker, _ in cls._MARKERS):
                return length
        return 0


def _local_model_messages(
    model_info: ModelInfo,
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Preserve canonical messages without silent model-family prefills."""

    del model_info
    return list(messages)


def _chat_reasoning(message: object) -> str:
    """Normalize compatible text channels; Groq/Together also return `reasoning`."""
    for field in ("reasoning_content", "reasoning"):
        value = getattr(message, field, None)
        if value is None or value == "":
            continue
        if not isinstance(value, str):
            raise InvalidResponseError("Provider reasoning channel must be text")
        # Some services send both names for the same text. Prefer the existing native field.
        return value
    details = getattr(message, "reasoning_details", None)
    if isinstance(details, list):
        return "".join(
            item.get("text", item.get("summary", ""))
            for item in details
            if isinstance(item, Mapping)
            and item.get("type") in {"reasoning.text", "reasoning.summary"}
            and isinstance(item.get("text", item.get("summary", "")), str)
        )
    return ""


def _reported_cache_usage(raw_usage: object, prompt_tokens: int) -> CacheUsage:
    """Keep optional provider cache fields distinct from reported zero counts."""
    def field(value: object, key: str) -> object:
        return value.get(key) if isinstance(value, Mapping) else getattr(value, key, None)

    details = field(raw_usage, "prompt_tokens_details") or field(raw_usage, "input_tokens_details")

    def observed(*values: object) -> int | None:
        return next((value for value in values if type(value) is int and value >= 0), None)

    read = observed(field(details, "cached_tokens"), field(raw_usage, "cached_tokens"),
                    field(raw_usage, "prompt_cache_hit_tokens"))
    write = observed(field(details, "cache_write_tokens"), field(raw_usage, "cache_write_tokens"))
    return CacheUsage(cache_read_tokens=read, cache_write_tokens=write,
                      cache_miss_tokens=None if read is None else max(0, prompt_tokens - read),
                      total_prompt_tokens=prompt_tokens)


def _stream_usage(raw_usage: object) -> tuple[TokenUsage, CacheUsage]:
    """Normalize usage from delta-bearing or usage-only streaming chunks."""

    prompt_tokens = int(
        getattr(raw_usage, "prompt_tokens", getattr(raw_usage, "input_tokens", 0)) or 0
    )
    completion_tokens = int(
        getattr(
            raw_usage,
            "completion_tokens",
            getattr(raw_usage, "output_tokens", 0),
        )
        or 0
    )
    completion_details = getattr(raw_usage, "completion_tokens_details", None) or getattr(
        raw_usage, "output_tokens_details", None
    )
    usage = TokenUsage(
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        total_tokens=int(
            getattr(raw_usage, "total_tokens", prompt_tokens + completion_tokens) or 0
        ),
        reasoning_tokens=(
            int(getattr(completion_details, "reasoning_tokens", 0) or 0)
            if completion_details is not None
            else None
        ),
    )
    return usage, _reported_cache_usage(raw_usage, prompt_tokens)


class ChatCompletionsTransport(ProviderTransport):
    """Transport for OpenAI-compatible chat/completions endpoints.

    Handles message formatting (pass-through — the internal format
    already matches OpenAI) and response parsing.
    """

    def __init__(self, *, preserve_reasoning: bool = False, preserve_content: bool = False):
        self.preserve_reasoning = preserve_reasoning
        self.preserve_content = preserve_content

    def format_request(
        self,
        model_id: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream: bool,
        **kwargs: object,
    ) -> dict[str, Any]:
        """Build an OpenAI-compatible request dict.

        The internal message format is already OpenAI-compatible,
        so this is mostly pass-through with model_id injection.
        """
        request: dict[str, Any] = {
            "model": model_id,
            "messages": messages,
            "stream": stream,
            **kwargs,
        }
        if tools:
            request["tools"] = tools
        if stream:
            request.setdefault("stream_options", {"include_usage": True})
        return request

    def parse_response(self, raw_response: Any, model_id: str) -> ProviderResponse:
        """Parse an OpenAI chat completion object into ProviderResponse."""
        return _parse_completion(
            raw_response,
            model_id,
            preserve_reasoning=self.preserve_reasoning,
            preserve_content=self.preserve_content,
        )

    def parse_stream_chunk(self, raw_chunk: Any) -> ProviderStreamChunk | None:
        """Parse an OpenAI SSE delta chunk into ProviderStreamChunk."""
        delta = raw_chunk.choices[0].delta if raw_chunk.choices else None
        if delta is None:
            return None

        content, block_reasoning = split_chat_content(getattr(delta, "content", ""))
        reasoning = _chat_reasoning(delta) + block_reasoning

        # DeepSeek and other thinking-mode providers echo reasoning into
        # the content stream.  When a chunk carries both fields and the
        # content is a prefix/subset of the reasoning, suppress the echo
        # so the user never sees leaked internal monologue.
        if reasoning and content:
            r_stripped = reasoning.strip()
            c_stripped = content.strip()
            if c_stripped and r_stripped.startswith(c_stripped[: min(len(c_stripped), 40)]):
                content = ""
        finish_reason = raw_chunk.choices[0].finish_reason if raw_chunk.choices else None

        tool_call_delta = None
        raw_tool_calls = getattr(delta, "tool_calls", None)
        if raw_tool_calls:
            tool_call_delta = (
                raw_tool_calls[0] if isinstance(raw_tool_calls, list) else raw_tool_calls
            )

        if not content and not reasoning and not tool_call_delta and not finish_reason:
            return None

        return ProviderStreamChunk(
            content=content,
            reasoning_content=reasoning,
            tool_call_delta=tool_call_delta,
            finish_reason=finish_reason,
        )

    def parse_stream_chunks(self, raw_chunk: Any) -> tuple[ProviderStreamChunk, ...]:
        """Preserve every tool delta, emitting finish only after all calls."""
        first = self.parse_stream_chunk(raw_chunk)
        if first is None:
            return ()
        choices = getattr(raw_chunk, "choices", None)
        delta = getattr(choices[0], "delta", None) if choices else None
        calls = getattr(delta, "tool_calls", None)
        if not isinstance(calls, list) or len(calls) < 2:
            return (first,)
        chunks = [replace(first, finish_reason=None)]
        chunks.extend(ProviderStreamChunk(tool_call_delta=call) for call in calls[1:])
        chunks[-1] = replace(chunks[-1], finish_reason=first.finish_reason)
        return tuple(chunks)

    def _raise_from_openai_error(
        self,
        exc: Any,
        retryable_statuses: set[int] | None = None,
    ) -> None:
        """Map OpenAI API errors to provider exception hierarchy.

        When ``retryable_statuses`` is provided, it is the SINGLE SOURCE OF TRUTH
        for which HTTP statuses map to retryable exceptions.  Statuses in the set
        are mapped to RateLimitError (429) or ProviderConnectionError (everything
        else).  Statuses NOT in the set map to non-retryable exceptions.

        When ``retryable_statuses`` is None (backward compat), hardcoded ranges
        are used.
        """
        status = getattr(exc, "status_code", 500)

        if retryable_statuses is not None:
            # ── retryable_statuses-driven routing ──────────────
            # Auth errors (401, 403) are NEVER retryable regardless of config.
            if status in (401, 403):
                raise AuthenticationError(str(exc)) from exc

            if status in retryable_statuses:
                if status == 429:
                    raise RateLimitError(str(exc), retry_after_seconds(exc)) from exc
                else:
                    raise ProviderConnectionError(
                        str(exc), retry_after=retry_after_seconds(exc)
                    ) from exc

            # Not in retryable set — non-retryable.
            if status == 404:
                from sylvia_gm.provider._exceptions import ModelNotFoundError

                raise ModelNotFoundError(str(exc)) from exc
            if status == 400:
                if is_context_rejection(exc):
                    raise TokenLimitError.provider_rejection() from exc
                raise InvalidRequestError(str(exc)) from exc
            raise ProviderUnavailableError(str(exc)) from exc
        else:
            # ── backward compat: hardcoded ranges ──────────────
            if status in (401, 403):
                raise AuthenticationError(str(exc)) from exc
            if status == 429:
                raise RateLimitError(str(exc), retry_after_seconds(exc)) from exc
            if status == 400 and (is_context_rejection(exc)):
                raise TokenLimitError.provider_rejection() from exc
            if status in (502, 503):
                raise ProviderUnavailableError(str(exc)) from exc
            if status == 400:
                raise InvalidRequestError(str(exc)) from exc
            raise ProviderConnectionError(str(exc)) from exc

    def _raise_from_timeout(self, exc: Exception) -> None:
        """Map timeout exceptions to ProviderTimeoutError."""
        raise ProviderTimeoutError(str(exc)) from exc


class ResponsesTransport(ChatCompletionsTransport):
    """Transport for OpenAI-compatible ``/responses`` endpoints.

    The internal conversation remains provider-neutral.  This adapter converts
    Chat-Completions-shaped history and tools at the provider boundary, then
    normalizes Responses output events back into Sylvia GM stream chunks.
    """

    @staticmethod
    def _input(messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        result: list[dict[str, Any]] = []
        for message in messages:
            role = message.get("role")
            if role == "tool":
                call_id = message.get("tool_call_id")
                if not isinstance(call_id, str) or not call_id:
                    raise InvalidResponseError("Tool result is missing its call identity")
                result.append(
                    {
                        "type": "function_call_output",
                        "call_id": call_id,
                        "output": str(message.get("content", "")),
                    }
                )
                continue
            if role == "assistant":
                retained = message.get("responses_output")
                if isinstance(retained, list):
                    from copy import deepcopy

                    result.extend(deepcopy(retained))
                    continue
                content = message.get("content")
                if isinstance(content, str) and content:
                    result.append({"role": "assistant", "content": content})
                raw_calls = message.get("tool_calls")
                if isinstance(raw_calls, list):
                    for raw_call in raw_calls:
                        if not isinstance(raw_call, dict):
                            raise InvalidResponseError("Assistant tool call is malformed")
                        function = raw_call.get("function")
                        if not isinstance(function, dict):
                            raise InvalidResponseError("Assistant tool call has no function")
                        call_id = raw_call.get("id")
                        name = function.get("name")
                        arguments = function.get("arguments", "{}")
                        if not isinstance(call_id, str) or not call_id or not isinstance(name, str):
                            raise InvalidResponseError("Assistant tool call identity is malformed")
                        result.append(
                            {
                                "type": "function_call",
                                "call_id": call_id,
                                "name": name,
                                "arguments": arguments if isinstance(arguments, str) else "{}",
                            }
                        )
                continue
            if role in {"system", "developer", "user"}:
                result.append({"role": str(role), "content": str(message.get("content", ""))})
                continue
            raise InvalidResponseError(f"Unsupported internal message role: {role!r}")
        return result

    @staticmethod
    def _tools(tools: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
        converted: list[dict[str, Any]] = []
        for tool in tools or []:
            function = tool.get("function") if isinstance(tool, dict) else None
            if not isinstance(function, dict):
                continue
            name = function.get("name")
            parameters = function.get("parameters")
            if not isinstance(name, str) or not isinstance(parameters, dict):
                continue
            converted.append(
                {
                    "type": "function",
                    "name": name,
                    "description": str(function.get("description", "")),
                    "parameters": parameters,
                }
            )
            if "strict" in function:
                converted[-1]["strict"] = function["strict"]
        return converted

    def format_request(
        self,
        model_id: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream: bool,
        **kwargs: object,
    ) -> dict[str, Any]:
        request: dict[str, Any] = {
            "model": model_id,
            "input": self._input(messages),
            "stream": stream,
        }
        converted_tools = self._tools(tools)
        if converted_tools:
            request["tools"] = converted_tools
        for key, value in kwargs.items():
            if key == "max_tokens":
                request["max_output_tokens"] = value
            elif (
                key == "tool_choice"
                and isinstance(value, dict)
                and isinstance(value.get("function"), dict)
            ):
                request[key] = {"type": "function", "name": value["function"]["name"]}
            elif key == "response_format" and isinstance(value, dict):
                format_type = value.get("type")
                if format_type == "json_schema" and isinstance(value.get("json_schema"), dict):
                    request.setdefault("text", {})["format"] = {
                        "type": "json_schema",
                        **value["json_schema"],
                    }
                elif format_type in {"json_object", "text"}:
                    request.setdefault("text", {})["format"] = dict(value)
                else:
                    raise InvalidRequestError("Unsupported Responses output format")
            elif key == "text" and isinstance(value, dict):
                request.setdefault("text", {}).update(value)
            elif key not in {"messages", "input", "stream_options"}:
                request[key] = value
        return request

    def parse_response(self, raw_response: Any, model_id: str) -> ProviderResponse:
        content = getattr(raw_response, "output_text", None)
        text = content if isinstance(content, str) else ""
        tool_calls: list[ToolCall] = []
        output = getattr(raw_response, "output", None)
        for item in output if isinstance(output, list) else []:
            if getattr(item, "type", None) == "function_call":
                tool_calls.append(
                    ToolCall(
                        id=str(getattr(item, "call_id", None) or getattr(item, "id", "")),
                        name=str(getattr(item, "name", "")),
                        arguments=str(getattr(item, "arguments", "{}") or "{}"),
                    )
                )
        usage = getattr(raw_response, "usage", None)
        prompt_tokens = int(getattr(usage, "input_tokens", 0) or 0)
        completion_tokens = int(getattr(usage, "output_tokens", 0) or 0)
        output_details = getattr(usage, "output_tokens_details", None)
        finish = responses_finish(raw_response)
        retained = native_output(output)
        return ProviderResponse(
            chat_completion_metadata={"responses_output": retained} if retained else None,
            content=text or None,
            provider_reported_reasoning_effort=_responses_reasoning_effort(raw_response),
            tool_calls=tool_calls,
            usage=TokenUsage(
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
                total_tokens=prompt_tokens + completion_tokens,
                reasoning_tokens=(
                    int(getattr(output_details, "reasoning_tokens", 0) or 0)
                    if output_details is not None
                    else None
                ),
            ),
            model=model_id,
            finish_reason=finish,
            usage_reported=usage is not None,
            cache_usage=_reported_cache_usage(usage, prompt_tokens),
        )

    def parse_stream_chunk(self, raw_chunk: Any) -> ProviderStreamChunk | None:
        event_type = getattr(raw_chunk, "type", "")
        if event_type == "response.output_text.delta":
            return ProviderStreamChunk(content=str(getattr(raw_chunk, "delta", "")))
        if event_type in {
            "response.reasoning_text.delta",
            "response.reasoning_summary_text.delta",
        }:
            return ProviderStreamChunk(reasoning_content=str(getattr(raw_chunk, "delta", "")))
        if event_type == "response.output_item.added":
            item = getattr(raw_chunk, "item", None)
            if getattr(item, "type", None) != "function_call":
                return None
            return ProviderStreamChunk(
                tool_call_delta={
                    "index": int(getattr(raw_chunk, "output_index", 0) or 0),
                    "id": str(getattr(item, "call_id", None) or getattr(item, "id", "")),
                    "function": {
                        "name": str(getattr(item, "name", "")),
                        "arguments": str(getattr(item, "arguments", "") or ""),
                    },
                }
            )
        if event_type == "response.function_call_arguments.delta":
            return ProviderStreamChunk(
                tool_call_delta={
                    "index": int(getattr(raw_chunk, "output_index", 0) or 0),
                    "id": "",
                    "function": {
                        "name": "",
                        "arguments": str(getattr(raw_chunk, "delta", "")),
                    },
                }
            )
        if event_type == "error":
            raise ProviderUnavailableError("Provider returned a Responses stream error")
        if event_type in {"response.completed", "response.incomplete", "response.failed"}:
            response = getattr(raw_chunk, "response", None)
            finish = responses_finish(response)
            retained = native_output(getattr(response, "output", None))
            return ProviderStreamChunk(
                finish_reason=finish,
                chat_completion_metadata={"responses_output": retained} if retained else None,
                provider_reported_reasoning_effort=_responses_reasoning_effort(response),
            )
        return None


def _responses_reasoning_effort(response: Any) -> str | None:
    """Read Responses' returned reasoning.effort; absence is not confirmation of our request.

    This is the SDK Response.reasoning field, also present on terminal stream events.
    Other transports do not advertise an equivalent response field and remain unreported.
    """
    from sylvia_gm.provider._reasoning import reasoning_values

    value = getattr(getattr(response, "reasoning", None), "effort", None)
    if value is None:
        return None
    try:
        supported, _ = reasoning_values([value], None)
    except ValueError:
        return None
    return supported[0] if supported else None


# ── DIAGNOSTIC_HEADERS — subset of interesting response headers ──────

DIAGNOSTIC_HEADERS = {
    "cf-ray",
    "cf-cache-status",
    "x-request-id",
    "x-ratelimit-limit-requests",
    "x-ratelimit-remaining-requests",
    "x-ratelimit-reset-requests",
    "x-ratelimit-limit-tokens",
    "x-ratelimit-remaining-tokens",
    "openai-organization",
    "openai-processing-ms",
    "openai-version",
    "anthropic-request-id",
    "retry-after",
}


def _extract_headers(sdk_response: Any) -> dict[str, str]:
    """Extract diagnostic headers from an SDK response object.

    Accesses the private ``_response.headers`` attribute with broad
    try/except — if the SDK changes its internals, this returns {}.
    """
    mode = os.environ.get("SYLVIA_GM_HEADER_EXTRACTION", "warn")
    if mode == "off":
        return {}

    try:
        raw_resp = getattr(sdk_response, "_response", None)
        if raw_resp is None:
            return {}
        headers = getattr(raw_resp, "headers", None)
        if headers is None:
            return {}
        result: dict[str, str] = {}
        for key in DIAGNOSTIC_HEADERS:
            val = headers.get(key)
            if val is not None:
                result[key] = val
        return result
    except Exception:
        if mode == "strict":
            raise
        logger.debug("Header extraction failed", exc_info=True)
        return {}


class OpenAICompatProvider(Provider):
    """Provider for any OpenAI-compatible API endpoint.

    Works with OpenCode Go, OpenRouter, Anthropic (via proxy), and Ollama.
    All share the same chat/completions wire protocol.

    Supports both the original ``api_key`` constructor (for backward compat)
    and the new ``credential_pool`` constructor (for key rotation).
    """

    MAX_CONSECUTIVE_CONNECTION_ERRORS = 3

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        model_info: ModelInfo | None = None,
        retry_config: RetryConfig | None = None,
        http_client: httpx.AsyncClient | None = None,
        credential_pool: CredentialPool | None = None,
        timeout_config: TimeoutConfig | None = None,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        """Initialise the provider.

        Args:
            base_url: API base URL (e.g. ``https://api.opencode.go/v1``).
            api_key: API key (plain text). Deprecated in favor of credential_pool.
            model_info: Metadata for the model this provider serves.
            retry_config: Retry parameters. Uses defaults if None.
            http_client: Optional shared httpx client.
            credential_pool: New-style credential pool for key rotation.
            timeout_config: Optional timeout configuration.
        """
        super().__init__()

        self._base_url = base_url
        self._model_info = model_info or ModelInfo(
            id="unknown",
            name="Unknown",
            provider_name="BYOK",
            provider_key="byok",
            provider_mode="byok",
        )
        self._retry_config = retry_config or RetryConfig()
        self._timeout_config = timeout_config or TimeoutConfig()
        from urllib.parse import urlsplit

        # Replay only fields documented by the selected service, never a universal
        # OpenAI-compatible extension inferred from another provider's output.
        hostname = urlsplit(base_url).hostname
        replay_hosts = {
            "openrouter.ai",
            "api.deepseek.com",
            "api.moonshot.ai",
            "api.moonshot.cn",
            "api.together.ai",
            "api.z.ai",
            "api.fireworks.ai",
            "api.cerebras.ai",
            "dashscope-intl.aliyuncs.com",
            "dashscope.aliyuncs.com",
            "dashscope-us.aliyuncs.com",
        }
        preserve_reasoning = hostname in replay_hosts or self._model_info.provider_key in {
            "openrouter",
            "deepseek",
            "moonshot",
            "together",
            "zai",
            "fireworks",
            "cerebras",
            "alibaba",
        }
        preserve_content = (
            hostname == "api.mistral.ai" or self._model_info.provider_key == "mistral"
        )
        self._transport: ChatCompletionsTransport = (
            ResponsesTransport()
            if self._model_info.transport == "responses"
            else ChatCompletionsTransport(
                preserve_reasoning=preserve_reasoning, preserve_content=preserve_content
            )
        )
        self._default_headers = dict(default_headers or {})
        self._custom_http_client = http_client
        self._consecutive_connection_errors = 0
        self._closed = False

        # Resolve credential pool: credential_pool > api_key > error
        if credential_pool is not None:
            self._credential_pool = credential_pool
        elif api_key is not None:
            self._credential_pool = CredentialPool([Credential(type="api_key", value=api_key)])
        else:
            raise ValueError("Either api_key or credential_pool is required")

        # Session-scoped client — created ONCE, reused for all requests
        self._client = self._build_client(http_client)

    def _build_client(self, http_client: httpx.AsyncClient | None = None) -> AsyncOpenAI:
        """Build an AsyncOpenAI client with configured timeouts."""
        return AsyncOpenAI(
            base_url=self._base_url,
            api_key="placeholder",  # Rotated per request; placeholder satisfies SDK validation
            default_headers=self._default_headers,
            http_client=http_client or self._custom_http_client,
            max_retries=0,  # We handle retries ourselves.
            timeout=self._timeout_config.to_httpx_timeout(),
        )

    async def _recycle_pool(self) -> None:
        """Close and recreate HTTP client to clear stale connections."""
        logger.warning(
            "Recycling connection pool for %s after %d consecutive errors",
            self._model_info.provider_name,
            self._consecutive_connection_errors,
        )
        await self._client.close()
        self._client = self._build_client()
        self._consecutive_connection_errors = 0

    @property
    def model_info(self) -> ModelInfo:
        return self._model_info

    def provider_request_representation(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        options: Mapping[str, object],
    ) -> dict[str, Any]:
        """Build the exact provider-client request object without sending it."""

        request_options = dict(options)
        stream = bool(request_options.pop("stream", False))
        request_options.pop("timeout", None)
        request_options.pop("_retry_attempt", None)
        self._response_continuation_options(request_options)
        return self._transport.format_request(
            model_id=self._model_info.wire_model_id,
            messages=self._wire_messages(messages),
            tools=tools,
            stream=stream,
            **request_options,
        )

    # ── Hook context helpers ──────────────────────────────────────

    def _wire_messages(self, messages: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Apply documented route restrictions without modifying saved neutral messages."""
        from urllib.parse import urlsplit

        selected = _local_model_messages(self._model_info, messages)
        if (
            urlsplit(self._base_url).hostname == "api.groq.com"
            or self._model_info.provider_key == "groq"
        ):
            return [
                {key: value for key, value in message.items() if key != "name"}
                for message in selected
            ]
        return selected

    def _response_continuation_options(self, options: dict[str, object]) -> None:
        """Request documented stateless native reasoning only on direct supported APIs."""
        from urllib.parse import urlsplit

        context_id = options.pop("_request_context_id", None)
        if (urlsplit(self._base_url).hostname == "openrouter.ai"
                and isinstance(context_id, str) and context_id.startswith("sylvia-")
                and len(context_id) <= 256):
            extra = options.get("extra_body")
            options["extra_body"] = {
                **(extra if isinstance(extra, dict) else {}), "session_id": context_id,
            }
        if self._model_info.transport != "responses" or urlsplit(self._base_url).hostname not in {
            "api.openai.com",
            "api.x.ai",
        }:
            return
        options.setdefault("store", False)
        include = options.get("include", [])
        if isinstance(include, list) and "reasoning.encrypted_content" not in include:
            options["include"] = [*include, "reasoning.encrypted_content"]

    def _build_request_context(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream: bool,
        retry_attempt: int = 0,
        request_json: dict[str, Any] | None = None,
    ) -> HookContext:
        return HookContext(
            provider_key=self._model_info.provider_key,
            model_id=self._model_info.id,
            message_count=len(messages),
            tool_count=len(tools) if tools else 0,
            request_json=request_json,
            retry_attempt=retry_attempt,
            stream=stream,
            timestamp_iso=datetime.now(UTC).isoformat(),
        )

    def _enrich_context_response(
        self,
        ctx: HookContext,
        response: ProviderResponse,
        duration_ms: float,
        http_status: int | None,
        response_headers: dict[str, str],
    ) -> HookContext:
        return HookContext(
            provider_key=ctx.provider_key,
            model_id=ctx.model_id,
            message_count=ctx.message_count,
            tool_count=ctx.tool_count,
            request_json=ctx.request_json,
            response=response,
            duration_ms=duration_ms,
            http_status=http_status,
            response_headers=response_headers,
            retry_attempt=ctx.retry_attempt,
            timestamp_iso=ctx.timestamp_iso,
            stream=ctx.stream,
        )

    def _enrich_context_error(
        self,
        ctx: HookContext,
        error: Exception,
        duration_ms: float,
        http_status: int | None = None,
    ) -> HookContext:
        return HookContext(
            provider_key=ctx.provider_key,
            model_id=ctx.model_id,
            message_count=ctx.message_count,
            tool_count=ctx.tool_count,
            request_json=ctx.request_json,
            error=error,
            duration_ms=duration_ms,
            http_status=http_status,
            retry_attempt=ctx.retry_attempt,
            timestamp_iso=ctx.timestamp_iso,
            stream=ctx.stream,
        )

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        """Send a chat completion and return the complete response.

        Retryable errors are automatically retried with backoff.
        The overall call is bounded by *timeout* seconds.
        """
        retry_attempt = cast(int, kwargs.pop("_retry_attempt", 0))
        self._response_continuation_options(kwargs)
        request_dict = self._transport.format_request(
            model_id=self._model_info.wire_model_id,
            messages=self._wire_messages(messages),
            tools=tools,
            stream=False,
            **kwargs,
        )
        request_ctx = self._build_request_context(
            messages,
            tools,
            stream=False,
            retry_attempt=retry_attempt,
            request_json=request_dict,
        )
        await self.hooks.fire_on_request(request_ctx)

        async def _call() -> ProviderResponse:
            # Rotate API key for this request
            credential = await self._credential_pool.get_credential()
            # OpenAI-compatible local servers such as LM Studio do not require
            # authentication, but the OpenAI SDK still requires a non-empty
            # internal API-key value before it will construct a request.
            self._client.api_key = credential.value or _CREDENTIALLESS_API_KEY

            try:
                endpoint = (
                    self._client.responses.create
                    if self._model_info.transport == "responses"
                    else self._client.chat.completions.create
                )
                completion = await asyncio.wait_for(endpoint(**request_dict), timeout=timeout)
            except TimeoutError:
                raise ProviderTimeoutError(
                    f"Request to {self._model_info.provider_name} timed out after {timeout}s"
                ) from None
            except APITimeoutError as exc:
                raise ProviderTimeoutError("Provider request timed out") from exc
            except APIConnectionError as exc:
                raise ProviderConnectionError("Provider connection failed") from exc
            except APIStatusError as exc:
                # Map SDK error to provider exception INSIDE the retry loop
                # so retry_with_backoff can retry it.
                self._transport._raise_from_openai_error(exc, self._retry_config.retryable_statuses)

            # Parse response
            response = self._transport.parse_response(completion, self._model_info.id)
            return response

        t0 = _time.monotonic()
        try:
            response = await retry_with_backoff(_call, self._retry_config)
            duration_ms = (_time.monotonic() - t0) * 1000.0

            # Extract diagnostics headers
            response_headers: dict[str, str] = {}

            # Fire hooks
            enriched = self._enrich_context_response(
                request_ctx,
                response,
                duration_ms,
                200,
                response_headers,
            )
            await self.hooks.fire_on_response(enriched)
            return response

        except (RateLimitError, ProviderTimeoutError, ProviderConnectionError):
            duration_ms = (_time.monotonic() - t0) * 1000.0
            self._consecutive_connection_errors += 1
            if self._consecutive_connection_errors >= self.MAX_CONSECUTIVE_CONNECTION_ERRORS:
                await self._recycle_pool()
            # Fire error hook
            enriched = self._enrich_context_error(
                request_ctx,
                _captured_error(),
                duration_ms,
            )
            await self.hooks.fire_on_error(enriched)
            raise
        except ProviderError as exc:
            duration_ms = (_time.monotonic() - t0) * 1000.0
            enriched = self._enrich_context_error(request_ctx, exc, duration_ms)
            await self.hooks.fire_on_error(enriched)
            raise
        except Exception as exc:
            duration_ms = (_time.monotonic() - t0) * 1000.0
            # Catch-all for unexpected errors; inspect HTTP status if available.
            import openai as _openai_mod

            if isinstance(exc, _openai_mod.APIStatusError):
                self._transport._raise_from_openai_error(exc)
            status = getattr(exc, "status_code", None)
            if isinstance(status, int):
                self._transport._raise_from_openai_error(exc)
            enriched = self._enrich_context_error(request_ctx, exc, duration_ms)
            await self.hooks.fire_on_error(enriched)
            raise ProviderConnectionError(str(exc)) from exc

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        """Stream chat completion chunks from an OpenAI-compatible API.

        Captures content, reasoning_content, and tool_call_delta from
        each streaming delta. Empty/sentinel chunks are skipped. ``timeout``
        bounds the initial response and each wait for the next raw stream
        chunk; every received chunk resets the inactivity window.
        """
        retry_attempt = cast(int, kwargs.pop("_retry_attempt", 0))
        self._response_continuation_options(kwargs)
        request_dict = self._transport.format_request(
            model_id=self._model_info.wire_model_id,
            messages=self._wire_messages(messages),
            tools=tools,
            stream=True,
            **kwargs,
        )
        request_ctx = self._build_request_context(
            messages,
            tools,
            stream=True,
            retry_attempt=retry_attempt,
            request_json=request_dict,
        )
        await self.hooks.fire_on_request(request_ctx)

        # Rotate API key for this stream
        credential = await self._credential_pool.get_credential()
        # Keep credentialless local endpoints usable without persisting or
        # projecting a fabricated user credential.
        self._client.api_key = credential.value or _CREDENTIALLESS_API_KEY

        t0 = _time.monotonic()
        try:
            endpoint = (
                self._client.responses.create
                if self._model_info.transport == "responses"
                else self._client.chat.completions.create
            )
            response = await asyncio.wait_for(endpoint(**request_dict), timeout=timeout)
        except TimeoutError:
            duration_ms = (_time.monotonic() - t0) * 1000.0
            enriched = self._enrich_context_error(
                request_ctx,
                ProviderTimeoutError(
                    f"Stream request to {self._model_info.provider_name} timed out after {timeout}s"
                ),
                duration_ms,
            )
            await self.hooks.fire_on_error(enriched)
            raise ProviderTimeoutError(
                f"Stream request to {self._model_info.provider_name} timed out after {timeout}s"
            ) from None
        except APITimeoutError as exc:
            raise ProviderTimeoutError("Provider stream request timed out") from exc
        except APIConnectionError as exc:
            raise ProviderConnectionError("Provider stream connection failed") from exc
        except APIStatusError as exc:
            # Initial HTTP rejections happen before a stream iterator exists.
            self._transport._raise_from_openai_error(exc)

        # Accumulate usage from final chunk
        stream_usage: TokenUsage | None = None
        stream_cache_usage = CacheUsage()
        reported_effort: str | None = None
        inline_reasoning_filter = _InlineReasoningFilter()
        chat_metadata = ChatCompletionMetadata(
            preserve_reasoning=self._transport.preserve_reasoning,
            preserve_content=self._transport.preserve_content,
        )
        terminal_seen = False
        tracker = None
        http_response = getattr(response, "response", None)
        if isinstance(http_response, httpx.Response):
            tracker = DoneTrackingStream(http_response.stream)
            if http_response.is_stream_consumed:
                tracker.observe(http_response.content)
            http_response.stream = tracker
        try:
            stream_iterator = response.__aiter__()
            while True:
                try:
                    chunk = await asyncio.wait_for(
                        stream_iterator.__anext__(),
                        timeout=timeout,
                    )
                except StopAsyncIteration:
                    break

                # Usage is frequently delivered in a final chunk with no choices or delta.
                # Capture it before parsing so exact audit accounting does not discard it.
                raw_usage = getattr(chunk, "usage", None)
                if raw_usage is None and getattr(chunk, "type", "") in {
                    "response.completed",
                    "response.failed",
                    "response.incomplete",
                }:
                    raw_usage = getattr(getattr(chunk, "response", None), "usage", None)
                if raw_usage is not None:
                    stream_usage, stream_cache_usage = _stream_usage(raw_usage)
                choices = getattr(chunk, "choices", None)
                if choices:
                    chat_metadata.add(choices[0].delta)
                for parsed in self._transport.parse_stream_chunks(chunk):
                    if parsed.finish_reason is not None:
                        terminal_seen = True
                    if parsed.chat_completion_metadata is not None:
                        yield ProviderStreamChunk(
                            chat_completion_metadata=parsed.chat_completion_metadata
                        )
                    if parsed.provider_reported_reasoning_effort is not None:
                        reported_effort = parsed.provider_reported_reasoning_effort
                    filtered = inline_reasoning_filter.filter_chunk(
                        replace(parsed, chat_completion_metadata=None)
                    )
                    if (
                        filtered.content
                        or filtered.reasoning_content
                        or filtered.tool_call_delta is not None
                        or filtered.finish_reason is not None
                    ):
                        yield filtered
            if not terminal_seen or (
                self._model_info.transport != "responses"
                and tracker is not None
                and not tracker.done
            ):
                raise IncompleteProviderStreamError(
                    "Provider stream ended before its terminal event"
                )
        except TimeoutError:
            duration_ms = (_time.monotonic() - t0) * 1000.0
            timeout_error = ProviderTimeoutError(
                f"Stream from {self._model_info.provider_name} was inactive for {timeout}s"
            )
            enriched = self._enrich_context_error(request_ctx, timeout_error, duration_ms)
            await self.hooks.fire_on_error(enriched)
            raise timeout_error from None
        except Exception as exc:
            duration_ms = (_time.monotonic() - t0) * 1000.0
            import openai as _openai_mod

            if isinstance(exc, _openai_mod.APIStatusError):
                self._transport._raise_from_openai_error(exc)
            status = getattr(exc, "status_code", None)
            if isinstance(status, int):
                self._transport._raise_from_openai_error(exc)
            enriched = self._enrich_context_error(request_ctx, exc, duration_ms)
            if stream_usage is not None:
                enriched = replace(
                    enriched,
                    response=ProviderResponse(
                        usage=stream_usage,
                        usage_reported=True,
                        cache_usage=stream_cache_usage,
                    ),
                )
            await self.hooks.fire_on_error(enriched)
            if isinstance(exc, ProviderError):
                raise
            raise ProviderConnectionError(str(exc)) from exc
        finally:
            close = getattr(response, "close", None)
            if callable(close):
                await close()

        native_metadata = chat_metadata.payload()
        if native_metadata is not None:
            yield ProviderStreamChunk(chat_completion_metadata=native_metadata)

        # Stream completed — fire on_response with aggregate usage
        duration_ms = (_time.monotonic() - t0) * 1000.0
        stream_response = ProviderResponse(
            usage=stream_usage or TokenUsage(),
            usage_reported=stream_usage is not None,
            cache_usage=stream_cache_usage,
            provider_reported_reasoning_effort=reported_effort,
        )
        enriched = self._enrich_context_response(
            request_ctx,
            stream_response,
            duration_ms,
            200,
            {},
        )
        await self.hooks.fire_on_response(enriched)

    async def close(self) -> None:
        """Clean up the session-scoped client. Idempotent."""
        if self._closed:
            return
        self._closed = True
        await self._client.close()


def _captured_error() -> Exception:
    """Return the currently active exception (call only in except block)."""
    import sys

    error = sys.exc_info()[1]
    return error if isinstance(error, Exception) else RuntimeError("unknown error")


def _parse_completion(
    completion: Any,
    model_id: str,
    *,
    preserve_reasoning: bool = False,
    preserve_content: bool = False,
) -> ProviderResponse:
    """Parse an OpenAI chat completion object into a ProviderResponse."""
    try:
        choice = completion.choices[0]
        message = choice.message
    except (AttributeError, IndexError, TypeError) as exc:
        raise InvalidResponseError(f"Failed to parse completion: {exc}") from exc

    content, block_reasoning = split_chat_content(getattr(message, "content", None))
    reasoning_content = (_chat_reasoning(message) + block_reasoning) or None
    if isinstance(content, str):
        visible_content, inline_reasoning = _InlineReasoningFilter().feed(content, final=True)
        content = visible_content or None
        combined_reasoning = (reasoning_content or "") + inline_reasoning
        reasoning_content = combined_reasoning or None

    tool_calls: list[ToolCall] = []
    raw_tool_calls = getattr(message, "tool_calls", None) or []
    for tc in raw_tool_calls:
        tool_calls.append(
            ToolCall(
                id=getattr(tc, "id", ""),
                name=getattr(tc.function, "name", "") if hasattr(tc, "function") else "",
                arguments=getattr(tc.function, "arguments", "{}")
                if hasattr(tc, "function")
                else "{}",
            )
        )

    usage_raw = getattr(completion, "usage", None)
    usage = TokenUsage()
    if usage_raw is not None:
        usage = TokenUsage(
            prompt_tokens=getattr(usage_raw, "prompt_tokens", 0),
            completion_tokens=getattr(usage_raw, "completion_tokens", 0),
            total_tokens=getattr(usage_raw, "total_tokens", 0),
            reasoning_tokens=(
                int(
                    getattr(
                        getattr(usage_raw, "completion_tokens_details", None),
                        "reasoning_tokens",
                        0,
                    )
                    or 0
                )
                if getattr(usage_raw, "completion_tokens_details", None) is not None
                else None
            ),
        )

    _, cache_usage = _stream_usage(usage_raw) if usage_raw is not None else (usage, CacheUsage())

    finish_reason = getattr(choice, "finish_reason", "stop") or "stop"

    # Map "length" finish reason to TokenLimitError-friendly state.
    if finish_reason == "length":
        logger.warning(
            "Model %s returned finish_reason='length' — possible context window exceeded.",
            model_id,
        )

    chat_metadata = ChatCompletionMetadata(
        preserve_reasoning=preserve_reasoning, preserve_content=preserve_content
    )
    chat_metadata.add(message)
    return ProviderResponse(
        chat_completion_metadata=chat_metadata.payload(),
        content=content,
        reasoning_content=reasoning_content,
        tool_calls=tool_calls,
        usage=usage,
        usage_reported=usage_raw is not None,
        model=model_id,
        finish_reason=finish_reason,
        cache_usage=cache_usage,
    )
