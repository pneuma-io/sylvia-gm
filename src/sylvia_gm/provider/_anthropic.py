"""Anthropic Messages API transport and provider.

Native support for Anthropic's /v1/messages endpoint with multi-part
content blocks, tool-use format, and streaming SSE events.

Requires ``pip install anthropic>=0.39.0,<1.0.0`` (optional extra).
"""

from __future__ import annotations

import asyncio
import json
import logging
import time as _time
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, datetime
from typing import Any

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
from sylvia_gm.provider._credentials import Credential, CredentialPool
from sylvia_gm.provider._exceptions import (
    AuthenticationError,
    IncompleteProviderStreamError,
    InvalidRequestError,
    InvalidResponseError,
    InvalidToolArgumentsError,
    ProviderConnectionError,
    ProviderError,
    ProviderPausedError,
    ProviderRefusalError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
    TokenLimitError,
    is_context_rejection,
)
from sylvia_gm.provider._failover import retry_with_backoff
from sylvia_gm.provider._hooks import HookContext
from sylvia_gm.provider._retry_after import retry_after_seconds

logger = logging.getLogger(__name__)


def _content_block_dict(block: Any) -> dict[str, Any]:
    """Copy native blocks without rebuilding or losing signatures and provider fields."""
    if isinstance(block, dict):
        return dict(block)
    if callable(getattr(block, "model_dump", None)):
        return dict(block.model_dump(mode="json", exclude_none=True))
    return dict(vars(block))


class _AnthropicStreamContent:
    """Retain one response's ordered native blocks for private tool-result continuation."""

    def __init__(self) -> None:
        self.blocks: dict[int, dict[str, Any]] = {}
        self.arguments: dict[int, str] = {}
        self.completed = False

    def accept(self, event: Any) -> list[dict[str, Any]] | None:
        """Return complete native content only after the provider's message-stop event."""
        event_type = getattr(event, "type", "")
        index = getattr(event, "index", 0)
        if event_type == "content_block_start":
            self.blocks[index] = _content_block_dict(event.content_block)
        elif event_type == "content_block_delta":
            delta = event.delta
            delta_type = getattr(delta, "type", "")
            field = {
                "text_delta": "text",
                "thinking_delta": "thinking",
                "signature_delta": "signature",
            }.get(delta_type)
            if field is not None:
                block = self.blocks[index]
                block[field] = block.get(field, "") + getattr(delta, field, "")
            elif delta_type == "input_json_delta":
                self.arguments[index] = self.arguments.get(index, "") + delta.partial_json
        elif event_type == "message_stop":
            for block_index, fragments in self.arguments.items():
                if fragments:
                    try:
                        self.blocks[block_index]["input"] = json.loads(fragments)
                    except (KeyError, json.JSONDecodeError) as exc:
                        raise InvalidToolArgumentsError(
                            f"Anthropic returned invalid streamed tool input at block {block_index}"
                        ) from exc
            self.completed = True
            return [self.blocks[key] for key in sorted(self.blocks)]
        return None

    def require_complete(self) -> None:
        """Reject a partial native response that cannot be replayed safely."""
        if not self.completed:
            raise IncompleteProviderStreamError(
                "Anthropic stream ended before the terminal message_stop event"
            )


class AnthropicMessagesTransport(ProviderTransport):
    """Transport for Anthropic's /v1/messages API.

    Converts internal OpenAI-format messages to Anthropic's multipart
    content blocks and maps Anthropic-specific error codes.
    """

    def format_request(
        self,
        model_id: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream: bool,
        **kwargs: object,
    ) -> dict[str, Any]:
        """Convert internal messages → Anthropic Messages API request.

        Mapping:
        - System messages → top-level ``system`` field
        - Tool definitions → Anthropic tool format with input_schema
        - Tool calls → ``tool_use`` content blocks
        - Tool results → ``tool_result`` content blocks
        - Native assistant blocks → unchanged private tool-result continuation
        """
        system_messages: list[dict[str, Any]] = []
        anthropic_messages: list[dict[str, Any]] = []

        for msg in messages:
            role = msg.get("role")

            if role == "system":
                system_messages.append(msg)
                continue

            if role == "assistant":
                native_content = msg.get("anthropic_content")
                if native_content is not None:
                    anthropic_messages.append({"role": "assistant", "content": native_content})
                    continue
                content_blocks: list[dict[str, Any]] = []

                # Text content
                text = msg.get("content")
                if text:
                    content_blocks.append({"type": "text", "text": text})

                # Tool calls → tool_use blocks
                tool_calls = msg.get("tool_calls") or []
                for tc in tool_calls:
                    func = tc.get("function", {})
                    args_str = func.get("arguments", "{}")
                    try:
                        import json

                        args = json.loads(args_str) if isinstance(args_str, str) else args_str
                    except (json.JSONDecodeError, TypeError):
                        args = {}
                    content_blocks.append(
                        {
                            "type": "tool_use",
                            "id": tc.get("id", ""),
                            "name": func.get("name", ""),
                            "input": args,
                        }
                    )

                anthropic_messages.append({"role": "assistant", "content": content_blocks})

            elif role == "tool":
                # Tool result → tool_result content block
                tool_result_content = msg.get("content", "")
                anthropic_messages.append(
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": msg.get("tool_call_id", ""),
                                "content": str(tool_result_content),
                            }
                        ],
                    }
                )

            elif role == "user":
                content = msg.get("content", "")
                anthropic_messages.append({"role": "user", "content": content})
            else:
                raise InvalidResponseError(
                    f"Unsupported internal message role for Anthropic transport: {role!r}"
                )

        # System prompt — handle both legacy plain-string and new
        # multi-block (cache-annotated) system messages.

        # Check if ANY system message has a list-based content (cache blocks)
        has_cache_blocks = any(isinstance(m.get("content"), list) for m in system_messages)

        if has_cache_blocks:
            # Multi-block path: collect all content blocks from tiered
            # system messages into the Anthropic system array.
            system_blocks: list[dict[str, Any]] = []
            for m in system_messages:
                content = m.get("content")
                if isinstance(content, list):
                    system_blocks.extend(content)
                elif isinstance(content, str) and content.strip():
                    # Plain string → wrap in text block (no cache_control)
                    system_blocks.append({"type": "text", "text": content})
            system_text_or_blocks: Any = system_blocks if system_blocks else None
        else:
            # Legacy string path (backward compat)
            system_text = "\n\n".join(m.get("content", "") for m in system_messages).strip()
            system_text_or_blocks = system_text if system_text else None

        request: dict[str, Any] = {
            "model": model_id,
            "messages": anthropic_messages,
            "max_tokens": kwargs.get("max_tokens", 4096),
            "stream": stream,
        }
        if "temperature" in kwargs:
            request["temperature"] = kwargs["temperature"]
        if "extra_headers" in kwargs:
            request["extra_headers"] = kwargs["extra_headers"]
        if "thinking" in kwargs:
            request["thinking"] = kwargs["thinking"]
        if "output_config" in kwargs:
            request["output_config"] = kwargs["output_config"]

        if system_text_or_blocks:
            request["system"] = system_text_or_blocks

        # Convert tools to Anthropic format
        if tools:
            anthropic_tools = []
            for tool in tools:
                func_def = tool.get("function", {})
                anthropic_tools.append(
                    {
                        "name": func_def.get("name", ""),
                        "description": func_def.get("description", ""),
                        "input_schema": func_def.get("parameters", {"type": "object"}),
                    }
                )
            request["tools"] = anthropic_tools
            if kwargs.get("tool_choice") == "required":
                request["tool_choice"] = (
                    {"type": "tool", "name": anthropic_tools[0]["name"]}
                    if len(anthropic_tools) == 1
                    else {"type": "any"}
                )

        return request

    def parse_response(self, raw_response: Any, model_id: str) -> ProviderResponse:
        """Parse an Anthropic Messages API response into ProviderResponse."""
        try:
            content_blocks: list[dict[str, Any]] = getattr(raw_response, "content", []) or []

            text_parts: list[str] = []
            tool_calls: list[ToolCall] = []

            for block in content_blocks:
                block_type = (
                    block.get("type", "") if isinstance(block, dict) else getattr(block, "type", "")
                )

                if block_type == "text":
                    text_parts.append(
                        block.get("text", "")
                        if isinstance(block, dict)
                        else getattr(block, "text", "")
                    )
                elif block_type == "tool_use":
                    tool_calls.append(
                        ToolCall(
                            id=block.get("id", "")
                            if isinstance(block, dict)
                            else getattr(block, "id", ""),
                            name=block.get("name", "")
                            if isinstance(block, dict)
                            else getattr(block, "name", ""),
                            arguments=json.dumps(
                                block.get("input", {})
                                if isinstance(block, dict)
                                else getattr(block, "input", {})
                            ),
                        )
                    )

            usage_data = getattr(raw_response, "usage", None)
            usage = TokenUsage(
                prompt_tokens=getattr(usage_data, "input_tokens", 0) if usage_data else 0,
                completion_tokens=getattr(usage_data, "output_tokens", 0) if usage_data else 0,
                total_tokens=(
                    getattr(usage_data, "input_tokens", 0) + getattr(usage_data, "output_tokens", 0)
                )
                if usage_data
                else 0,
            )

            # Extract prompt caching fields (Anthropic)
            cache_read = (
                getattr(usage_data, "cache_read_input_tokens", None) if usage_data else None
            )
            cache_write = (
                getattr(usage_data, "cache_creation_input_tokens", None) if usage_data else None
            )
            cache_usage = CacheUsage(
                cache_read_tokens=cache_read or 0,
                cache_write_tokens=cache_write or 0,
                cache_miss_tokens=usage.prompt_tokens,
                total_prompt_tokens=usage.prompt_tokens + (cache_read or 0) + (cache_write or 0),
            )

            usage = TokenUsage(
                prompt_tokens=cache_usage.total_prompt_tokens,
                completion_tokens=usage.completion_tokens,
                total_tokens=cache_usage.total_prompt_tokens + usage.completion_tokens,
            )
            stop_reason = getattr(raw_response, "stop_reason", "end_turn") or "end_turn"
            finish_reason_map = {
                "end_turn": "stop",
                "max_tokens": "length",
                "stop_sequence": "stop",
                "tool_use": "tool_calls",
                "model_context_window_exceeded": "context_length",
            }

            if stop_reason == "refusal":
                raise ProviderRefusalError("Anthropic declined the request.")
            if stop_reason == "pause_turn":
                raise ProviderPausedError(
                    "Anthropic requires an unsupported server-tool continuation."
                )
            if stop_reason not in finish_reason_map:
                raise InvalidResponseError(
                    f"Anthropic returned unsupported stop reason: {stop_reason}"
                )
            return ProviderResponse(
                content="\n".join(text_parts) if text_parts else None,
                reasoning_content="".join(
                    str(block.get("thinking", ""))
                    for block in map(_content_block_dict, content_blocks)
                    if block.get("type") == "thinking"
                )
                or None,
                anthropic_content=[_content_block_dict(block) for block in content_blocks],
                tool_calls=tool_calls,
                usage=usage,
                usage_reported=usage_data is not None,
                model=model_id,
                finish_reason=finish_reason_map.get(stop_reason, "stop"),
                cache_usage=cache_usage,
            )
        except ProviderError:
            raise
        except Exception as exc:
            raise InvalidResponseError(f"Failed to parse Anthropic response: {exc}") from exc

    def parse_stream_chunk(self, raw_chunk: Any) -> ProviderStreamChunk | None:
        """Parse an Anthropic SSE event into ProviderStreamChunk.

        SSE event types: message_start, content_block_start,
        content_block_delta, content_block_stop, message_delta, message_stop.
        """
        event_type = getattr(raw_chunk, "type", "")
        index = getattr(raw_chunk, "index", 0)

        if event_type == "content_block_start":
            block = getattr(raw_chunk, "content_block", None)
            block_type = getattr(block, "type", "") if block is not None else ""
            if block_type == "text":
                text = getattr(block, "text", "") or ""
                return ProviderStreamChunk(content=text) if text else None
            if block_type == "thinking":
                thinking = getattr(block, "thinking", "") or ""
                return ProviderStreamChunk(reasoning_content=thinking) if thinking else None
            if block_type == "tool_use":
                initial_input = getattr(block, "input", {}) or {}
                return ProviderStreamChunk(
                    tool_call_delta={
                        "index": index,
                        "id": getattr(block, "id", "") or "",
                        "name": getattr(block, "name", "") or "",
                        "arguments": json.dumps(
                            initial_input,
                            sort_keys=True,
                            separators=(",", ":"),
                        )
                        if initial_input
                        else "",
                    }
                )
        elif event_type == "content_block_delta":
            delta = getattr(raw_chunk, "delta", None)
            if delta is None:
                return None
            delta_type = getattr(delta, "type", "")

            if delta_type == "text_delta":
                return ProviderStreamChunk(content=getattr(delta, "text", "") or "")
            elif delta_type == "thinking_delta":
                return ProviderStreamChunk(reasoning_content=getattr(delta, "thinking", "") or "")
            elif delta_type == "input_json_delta":
                return ProviderStreamChunk(
                    tool_call_delta={
                        "index": index,
                        "partial_json": getattr(delta, "partial_json", ""),
                    }
                )
        elif event_type == "message_delta":
            delta = getattr(raw_chunk, "delta", None)
            if delta:
                stop_reason = getattr(delta, "stop_reason", None)
                if stop_reason is None:
                    return None
                finishes = {
                    "end_turn": "end_turn",
                    "stop_sequence": "stop_sequence",
                    "tool_use": "tool_use",
                    "max_tokens": "max_tokens",
                    "model_context_window_exceeded": "context_length",
                }
                if stop_reason == "refusal":
                    raise ProviderRefusalError("Anthropic declined the request.")
                if stop_reason == "pause_turn":
                    raise ProviderPausedError(
                        "Anthropic requires an unsupported server-tool continuation."
                    )
                if stop_reason not in finishes:
                    raise InvalidResponseError(
                        f"Anthropic returned unsupported stop reason: {stop_reason}"
                    )
                return ProviderStreamChunk(finish_reason=finishes[stop_reason])
        return None

    def _raise_from_anthropic_error(
        self,
        exc: Any,
        retryable_statuses: set[int] | None = None,
    ) -> None:
        """Map Anthropic API errors to provider exception hierarchy.

        When ``retryable_statuses`` is provided, it is the SINGLE SOURCE OF TRUTH
        for which HTTP statuses map to retryable exceptions.  Statuses in the set
        are mapped to RateLimitError (429, 529) or ProviderConnectionError (everything
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
                if status in (429, 529):
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
            if status in (502, 503):
                raise ProviderUnavailableError(str(exc)) from exc
            raise ProviderUnavailableError(str(exc)) from exc
        else:
            # ── backward compat: hardcoded ranges ──────────────
            if status in (401, 403):
                raise AuthenticationError(str(exc)) from exc
            if status in (429, 529):  # 529 = Anthropic overloaded → RATE_LIMIT
                raise RateLimitError(str(exc), retry_after_seconds(exc)) from exc
            if status == 400 and (is_context_rejection(exc)):
                raise TokenLimitError.provider_rejection() from exc
            if status in (502, 503):
                raise ProviderUnavailableError(str(exc)) from exc
            if status == 400:
                raise InvalidRequestError(str(exc)) from exc
            raise ProviderConnectionError(str(exc)) from exc


class AnthropicProvider(Provider):
    """Provider for Anthropic's native Messages API.

    Uses the anthropic Python SDK with max_retries=0 so our failover
    layer is the sole retry authority.
    """

    MAX_CONSECUTIVE_CONNECTION_ERRORS = 3

    def __init__(
        self,
        base_url: str,
        credential_pool: CredentialPool,
        model_info: ModelInfo,
        retry_config: RetryConfig | None = None,
        timeout: float = 60.0,
        max_tokens: int = 4096,
        timeout_config: TimeoutConfig | None = None,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        super().__init__()

        self._base_url = base_url
        self._credential_pool = credential_pool
        self._model_info = model_info
        self._retry_config = retry_config or RetryConfig()
        self._timeout = timeout
        self._max_tokens = max_tokens
        self._timeout_config = timeout_config or TimeoutConfig()
        self._default_headers = dict(default_headers or {})
        self._transport = AnthropicMessagesTransport()
        self._consecutive_connection_errors = 0
        self._closed = False

        # Lazy import so anthropic is optional
        try:
            import anthropic as _anthropic_mod
        except ImportError:
            raise ImportError(
                "anthropic package is required for AnthropicProvider. "
                "Install with: pip install sylvia-gm[anthropic]"
            ) from None

        self._client = self._build_client(_anthropic_mod)

    def _build_client(self, anthropic_mod: Any | None = None) -> Any:
        """Build an AsyncAnthropic client with configured timeouts."""
        try:
            import anthropic as _anthropic_mod
        except ImportError:
            raise ImportError(
                "anthropic package is required for AnthropicProvider. "
                "Install with: pip install sylvia-gm[anthropic]"
            ) from None
        if anthropic_mod is None:
            anthropic_mod = _anthropic_mod

        sdk_base_url = self._base_url.rstrip("/")
        if sdk_base_url.endswith("/v1"):
            sdk_base_url = sdk_base_url[:-3]
        return _anthropic_mod.AsyncAnthropic(
            base_url=sdk_base_url,
            api_key="",  # rotated per request
            default_headers=self._default_headers,
            max_retries=0,  # we handle retries ourselves
            timeout=self._timeout_config.to_httpx_timeout(),
        )

    def _credential_request_options(
        self,
        credential: Credential,
        headers: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Tell the SDK when no authentication is intended, preserving custom headers."""
        import anthropic

        from sylvia_gm.provider._protocol_routes import is_opencode_inference

        if is_opencode_inference(self._base_url):
            self._client.api_key = ""
            self._client.auth_token = credential.value
            return {"extra_headers": {**(headers or {}), "X-Api-Key": anthropic.omit}}
        self._client.api_key = credential.value
        if not credential.value and not any(
            name.lower() == "x-api-key" for name in self._default_headers
        ):
            return {"extra_headers": {**(headers or {}), "X-Api-Key": anthropic.omit}}
        return {}

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
        """Build the exact Anthropic client request object without sending it."""

        request_options = dict(options)
        stream = bool(request_options.pop("stream", False))
        request_options.pop("timeout", None)
        request_options.pop("_retry_attempt", None)
        max_tokens = request_options.pop("max_tokens", self._max_tokens)
        request_options.pop("max_output_tokens", None)
        return self._transport.format_request(
            model_id=self._model_info.wire_model_id,
            messages=messages,
            tools=tools,
            stream=stream,
            max_tokens=max_tokens,
            **request_options,
        )

    # ── Hook context helpers ──────────────────────────────────────

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
        """Send a request to Anthropic Messages API with retry."""
        raw_retry_attempt = kwargs.pop("_retry_attempt", 0)
        retry_attempt = (
            raw_retry_attempt
            if isinstance(raw_retry_attempt, int) and not isinstance(raw_retry_attempt, bool)
            else 0
        )
        request_dict = self._transport.format_request(
            model_id=self._model_info.wire_model_id,
            messages=messages,
            tools=tools,
            stream=False,
            max_tokens=kwargs.get("max_tokens", self._max_tokens),
            **{k: v for k, v in kwargs.items() if k != "max_tokens"},
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
            import anthropic

            credential = await self._credential_pool.get_credential()
            auth_options = self._credential_request_options(
                credential, request_dict.get("extra_headers")
            )

            try:
                raw_response = await asyncio.wait_for(
                    self._client.messages.create(**{**request_dict, **auth_options}),
                    timeout=timeout,
                )
            except TimeoutError:
                raise ProviderTimeoutError(
                    f"Anthropic request timed out after {timeout}s"
                ) from None
            except anthropic.APIStatusError as exc:
                self._transport._raise_from_anthropic_error(
                    exc, self._retry_config.retryable_statuses
                )
            except anthropic.APITimeoutError as exc:
                raise ProviderTimeoutError(f"Anthropic SDK timeout: {exc}") from exc
            except anthropic.APIConnectionError as exc:
                raise ProviderConnectionError(f"Anthropic connection error: {exc}") from exc

            return self._transport.parse_response(raw_response, self._model_info.id)

        t0 = _time.monotonic()
        try:
            result = await retry_with_backoff(_call, self._retry_config)
            self._consecutive_connection_errors = 0
            duration_ms = (_time.monotonic() - t0) * 1000.0
            enriched = self._enrich_context_response(
                request_ctx,
                result,
                duration_ms,
                200,
                {},
            )
            await self.hooks.fire_on_response(enriched)
            return result
        except (ProviderConnectionError, ProviderTimeoutError):
            duration_ms = (_time.monotonic() - t0) * 1000.0
            self._consecutive_connection_errors += 1
            if self._consecutive_connection_errors >= self.MAX_CONSECUTIVE_CONNECTION_ERRORS:
                await self._recycle_pool()
            enriched = self._enrich_context_error(
                request_ctx,
                _captured_error(),
                duration_ms,
            )
            await self.hooks.fire_on_error(enriched)
            raise
        except AuthenticationError as exc:
            duration_ms = (_time.monotonic() - t0) * 1000.0
            enriched = self._enrich_context_error(request_ctx, exc, duration_ms)
            await self.hooks.fire_on_error(enriched)
            raise
        except Exception as exc:
            duration_ms = (_time.monotonic() - t0) * 1000.0
            enriched = self._enrich_context_error(request_ctx, exc, duration_ms)
            await self.hooks.fire_on_error(enriched)
            raise

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        """Stream from Anthropic Messages API."""
        import anthropic
        import httpx

        raw_retry_attempt = kwargs.pop("_retry_attempt", 0)
        retry_attempt = (
            raw_retry_attempt
            if isinstance(raw_retry_attempt, int) and not isinstance(raw_retry_attempt, bool)
            else 0
        )
        request_dict = self._transport.format_request(
            model_id=self._model_info.wire_model_id,
            messages=messages,
            tools=tools,
            stream=True,
            max_tokens=kwargs.get("max_tokens", self._max_tokens),
            **{k: v for k, v in kwargs.items() if k != "max_tokens"},
        )
        request_ctx = self._build_request_context(
            messages,
            tools,
            stream=True,
            retry_attempt=retry_attempt,
            request_json=request_dict,
        )
        await self.hooks.fire_on_request(request_ctx)

        credential = await self._credential_pool.get_credential()
        auth_options = self._credential_request_options(
            credential, request_dict.get("extra_headers")
        )

        t0 = _time.monotonic()
        input_tokens = 0
        output_tokens = 0
        cache_read_tokens = 0
        cache_write_tokens = 0
        usage_reported = False
        native_content = _AnthropicStreamContent()
        try:
            # Consume the raw SSE stream because Sylvia already owns event parsing.
            # The SDK's messages.stream() helper also accumulates a message snapshot and
            # can reject otherwise usable provider-compatible tool event ordering before
            # our transport sees it.
            raw_stream = await asyncio.wait_for(
                self._client.messages.create(**{**request_dict, **auth_options}),
                timeout=timeout,
            )
            async with raw_stream as stream_ctx:
                async for event in stream_ctx:
                    completed_content = native_content.accept(event)
                    if completed_content is not None:
                        yield ProviderStreamChunk(anthropic_content=completed_content)
                    event_type = getattr(event, "type", "")
                    usage_data = None
                    if event_type == "message_start":
                        usage_data = getattr(getattr(event, "message", None), "usage", None)
                    elif event_type == "message_delta":
                        usage_data = getattr(event, "usage", None)
                    if usage_data is not None:
                        usage_reported = True
                        input_tokens = max(
                            input_tokens,
                            int(getattr(usage_data, "input_tokens", 0) or 0),
                        )
                        output_tokens = max(
                            output_tokens,
                            int(getattr(usage_data, "output_tokens", 0) or 0),
                        )
                        cache_read_tokens = max(
                            cache_read_tokens,
                            int(getattr(usage_data, "cache_read_input_tokens", 0) or 0),
                        )
                        cache_write_tokens = max(
                            cache_write_tokens,
                            int(getattr(usage_data, "cache_creation_input_tokens", 0) or 0),
                        )
                    parsed = self._transport.parse_stream_chunk(event)
                    if parsed is not None:
                        yield parsed
            native_content.require_complete()
        except (TimeoutError, httpx.TimeoutException, anthropic.APITimeoutError):
            duration_ms = (_time.monotonic() - t0) * 1000.0
            # The SDK can expose httpx timeouts directly after response headers arrive.
            enriched = self._enrich_context_error(
                request_ctx,
                ProviderTimeoutError(f"Anthropic stream timed out after {timeout}s"),
                duration_ms,
            )
            await self.hooks.fire_on_error(enriched)
            raise ProviderTimeoutError(f"Anthropic stream timed out after {timeout}s") from None
        except anthropic.APIStatusError as exc:
            duration_ms = (_time.monotonic() - t0) * 1000.0
            try:
                self._transport._raise_from_anthropic_error(
                    exc, self._retry_config.retryable_statuses
                )
            except Exception as mapped_exc:
                enriched = self._enrich_context_error(request_ctx, mapped_exc, duration_ms)
                await self.hooks.fire_on_error(enriched)
                raise
        except anthropic.APIConnectionError as exc:
            duration_ms = (_time.monotonic() - t0) * 1000.0
            mapped = ProviderConnectionError(str(exc))
            enriched = self._enrich_context_error(request_ctx, mapped, duration_ms)
            await self.hooks.fire_on_error(enriched)
            raise ProviderConnectionError(str(exc)) from exc
        except Exception as exc:
            from dataclasses import replace

            duration_ms = (_time.monotonic() - t0) * 1000.0
            enriched = self._enrich_context_error(request_ctx, exc, duration_ms)
            if usage_reported:
                total_input = input_tokens + cache_read_tokens + cache_write_tokens
                enriched = replace(
                    enriched,
                    response=ProviderResponse(
                        usage=TokenUsage(
                            prompt_tokens=total_input,
                            completion_tokens=output_tokens,
                            total_tokens=total_input + output_tokens,
                        ),
                        usage_reported=True,
                        cache_usage=CacheUsage(
                            cache_read_tokens=cache_read_tokens,
                            cache_write_tokens=cache_write_tokens,
                            cache_miss_tokens=input_tokens,
                            total_prompt_tokens=total_input,
                        ),
                    ),
                )
            await self.hooks.fire_on_error(enriched)
            raise

        # Stream completed — fire on_response with aggregate usage
        duration_ms = (_time.monotonic() - t0) * 1000.0
        stream_response = ProviderResponse(
            usage=TokenUsage(
                prompt_tokens=input_tokens + cache_read_tokens + cache_write_tokens,
                completion_tokens=output_tokens,
                total_tokens=input_tokens + cache_read_tokens + cache_write_tokens + output_tokens,
            ),
            usage_reported=usage_reported,
            cache_usage=CacheUsage(
                cache_read_tokens=cache_read_tokens,
                cache_write_tokens=cache_write_tokens,
                cache_miss_tokens=input_tokens,
                total_prompt_tokens=input_tokens + cache_read_tokens + cache_write_tokens,
            ),
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

    captured = sys.exc_info()[1]
    return captured if isinstance(captured, Exception) else RuntimeError("unknown error")
