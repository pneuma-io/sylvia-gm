"""Abstract Provider ABC, transport interface, and shared data types.

Defines the contract every provider must implement,
plus data classes for requests, responses, streaming chunks,
retry configuration, and provider configuration.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Any, Literal


@dataclass
class TokenUsage:
    """Token usage statistics for a single request."""

    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    reasoning_tokens: int | None = None


@dataclass
class ToolCall:
    """A tool call requested by the model."""

    id: str
    name: str
    arguments: str  # JSON string


@dataclass
class CacheUsage:
    """Prompt caching statistics from provider response.

    Tracks how many prompt tokens were served from cache vs processed fresh.
    Different providers expose different fields; this is the unified view.

    - ``cache_read_tokens``: tokens served from cache (cached_tokens in OpenAI,
      cache_read_input_tokens in Anthropic)
    Missing fields remain ``None``; a reported zero is a measured value.
    - ``cache_write_tokens``: tokens written to cache where the provider reports them
    - ``cache_miss_tokens``: calculated as total - read; tokens NOT in cache
    - ``total_prompt_tokens``: total prompt tokens for the request
    """

    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    cache_miss_tokens: int | None = None
    total_prompt_tokens: int = 0

    @property
    def hit_ratio(self) -> float | None:
        """Observed read fraction; unavailable when cache reads were not reported."""
        if self.cache_read_tokens is None:
            return None
        if self.total_prompt_tokens == 0:
            return 0.0 if self.cache_read_tokens == 0 else None
        return self.cache_read_tokens / self.total_prompt_tokens

    @property
    def savings_pct(self) -> float | None:
        """Cached-token percentage, not an estimate of billing or subscription quota savings."""
        ratio = self.hit_ratio
        return None if ratio is None else ratio * 100


@dataclass
class ProviderResponse:
    """Complete (non-streaming) response from a provider."""

    content: str | None = None
    reasoning_content: str | None = None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: TokenUsage = field(default_factory=TokenUsage)
    usage_reported: bool | None = None
    model: str = ""
    finish_reason: str = "stop"
    cache_usage: CacheUsage = field(default_factory=CacheUsage)
    anthropic_content: list[dict[str, Any]] | None = None
    """Opaque native continuation blocks; private, never canonical conversation history."""
    chat_completion_metadata: dict[str, Any] | None = field(default=None, repr=False)
    """Private signed/tool continuation fields, never canonical conversation history."""
    provider_reported_reasoning_effort: str | None = None
    """Response metadata reported by the provider, never copied from our request."""


@dataclass
class ProviderStreamChunk:
    """A single chunk from a streaming response."""

    content: str = ""
    reasoning_content: str = ""
    tool_call_delta: dict[str, Any] | None = None
    finish_reason: str | None = None
    anthropic_content: list[dict[str, Any]] | None = None
    """Complete native assistant content at message end, including signed thinking blocks."""
    chat_completion_metadata: dict[str, Any] | None = field(default=None, repr=False)
    """Private signed/tool continuation fields, never canonical conversation history."""
    provider_reported_reasoning_effort: str | None = None


@asynccontextmanager
async def closing_provider_stream(stream: AsyncIterator[ProviderStreamChunk]):
    """Close the upstream iterator in the same task when a consumer stops at a terminal error."""
    try:
        yield stream
    finally:
        close = getattr(stream, "aclose", None)
        if close is not None:
            await close()


@dataclass
class ModelInfo:
    """Metadata for a model known to the registry."""

    id: str  # Canonical: "opencode-go/deepseek-v4"
    name: str  # Display: "DeepSeek V4"
    provider_name: str  # "OpenCode Go"
    provider_key: str  # Config key: "opencode_go"
    provider_mode: str  # "byok" or "ollama"
    recommended: bool = False
    premium: bool = False
    context_window: int | None = None
    """Effective context capacity used for request admission.

    This compatibility field is derived from the distinct facts below. It may be ``None`` when a
    configured model has no trustworthy runtime capacity or explicit operating limit.
    """
    declared_context_window: int | None = None
    declared_context_source: str = "unknown"
    runtime_context_window: int | None = None
    runtime_context_source: str = "unknown"
    configured_context_window: int | None = None
    context_window_source: str = "unknown"
    declared_context_observed_at: str | None = None
    runtime_context_observed_at: str | None = None
    configured_context_observed_at: str | None = None
    context_observed_at: str | None = None
    configured_runtime_mismatch: bool = False
    configured_declared_mismatch: bool = False
    max_output_tokens: int | None = 4096
    """Sylvia's completion allowance; None is valid for runtime-managed output.

    GM requests use this allowance; Builder has its separate capped allowance below.
    Admission reserves only an enforceable request allowance. Codex manages its own output budget.
    This value never changes the context capacity or substitutes for a missing capacity fact.
    """
    builder_output_tokens: int = 32_000
    """Builder response allowance, capped by max_output_tokens for supported transports."""
    cost_per_1k_tokens: float = 0.0
    transport: Literal[
        "chat_completions", "responses", "anthropic_messages", "codex_app_server",
        "gemini_generate_content"
    ] = "chat_completions"
    """Wire protocol needed to reach this model.

    - ``"chat_completions"`` — OpenAI-compatible (OpenCode Go, OpenRouter, Ollama)
    - ``"responses"`` — OpenAI Responses-compatible (OpenAI, selected OpenCode models)
    - ``"anthropic_messages"`` — Anthropic Messages API (native)
    """
    provider_model_id: str = ""
    """Exact model identifier sent to the provider."""
    supports_streaming: bool | None = None
    supports_tools: bool | None = None
    supported_reasoning_efforts: tuple[str, ...] | None = None
    """Native values advertised by this provider; None means capability is unknown."""
    default_reasoning_effort: str | None = None
    reasoning_control: str | None = None
    reasoning_source: str = "unknown"

    def __post_init__(self) -> None:
        """Keep legacy constructors honest without pretending their value was runtime-detected."""

        if (
            self.context_window is not None
            and self.declared_context_window is None
            and self.runtime_context_window is None
            and self.configured_context_window is None
            and self.context_window_source == "unknown"
        ):
            self.context_window_source = "legacy_unverified"

    @property
    def wire_model_id(self) -> str:
        """Return the upstream identifier without changing canonical identity."""
        if self.provider_model_id:
            return self.provider_model_id
        return self.id.split("/", 1)[-1]


@dataclass
class RetryConfig:
    """Configuration for retry-with-backoff behaviour.

    Auth errors (401, 403) are NEVER retried regardless of config.
    """

    max_retries: int = 3
    base_delay: float = 1.0  # seconds
    max_delay: float = 30.0  # seconds
    backoff_multiplier: float = 2.0
    retryable_statuses: set[int] = field(default_factory=lambda: {429, 500, 502, 503, 504})


@dataclass
class TimeoutConfig:
    """Per-operation timeout configuration.

    Mirrors httpx.Timeout with sensible defaults for LLM API calls.
    """

    connect: float = 15.0  # TCP handshake + TLS (seconds)
    read: float = 120.0  # Max silence between bytes (seconds)
    write: float = 30.0  # Max time to send request body (seconds)
    pool: float = 5.0  # Max time to acquire connection from pool (seconds)

    # Stream health
    stream_health_interval: float = 60.0  # Max seconds between stream chunks before declared dead

    def to_httpx_timeout(self, *, read: float | None = None) -> Any:
        """Build an httpx.Timeout from this config.

        Args:
            read: Override ``read`` timeout (e.g. for streaming with tighter bounds).
        """
        import httpx

        return httpx.Timeout(
            connect=self.connect,
            read=read if read is not None else self.read,
            write=self.write,
            pool=self.pool,
        )

    def to_stream_timeout(self) -> Any:
        """Build an httpx.Timeout with the stream-health interval as read timeout."""
        return self.to_httpx_timeout(read=self.stream_health_interval)


# Per-provider retry defaults.
# These are sensible starting points — user config YAML can override.
PROVIDER_RETRY_DEFAULTS: dict[str, RetryConfig] = {
    "opencode_go": RetryConfig(
        max_retries=2,
        base_delay=2.0,
        max_delay=15.0,
        backoff_multiplier=2.0,
        retryable_statuses={429, 500, 502, 503},
    ),
    "openrouter": RetryConfig(
        max_retries=3,
        base_delay=1.0,
        max_delay=30.0,
        backoff_multiplier=2.0,
        retryable_statuses={429, 500, 502, 503, 504},
    ),
    "anthropic": RetryConfig(
        max_retries=4,
        base_delay=5.0,
        max_delay=60.0,
        backoff_multiplier=2.0,
        retryable_statuses={429, 529, 500, 502, 503},
    ),
    "ollama": RetryConfig(
        max_retries=1,
        base_delay=0.5,
        max_delay=5.0,
        backoff_multiplier=1.5,
        retryable_statuses={500, 502, 503},
    ),
    "sylvia_cloud": RetryConfig(
        max_retries=2,
        base_delay=1.0,
        max_delay=15.0,
        backoff_multiplier=2.0,
        retryable_statuses={429, 500, 502, 503, 504},
    ),
}


@dataclass
class ProviderConfig:
    """Configuration for building a provider instance.

    Used by ProviderFactory to construct the correct provider chain.
    """

    provider_key: str  # "opencode_go", "anthropic", etc.
    transport: str  # "chat_completions" | "responses" | "anthropic_messages"
    base_url: str  # endpoint URL
    runtime_home: str = ""  # owner-selected private profile for managed subscription runtime
    default_headers: dict[str, str] = field(default_factory=dict)
    credentials_list: list[dict[str, str]] = field(default_factory=list)
    # credentials_list elements: {"type": "api_key", "value": "sk-..."}
    models: list[ModelInfo] = field(default_factory=list)
    default_model: str = ""
    timeout: float = 60.0
    max_tokens: int = 4096
    fallback_providers: list[ProviderConfig] = field(default_factory=list)
    retry_config: RetryConfig | None = None  # None → use provider default


class Provider(ABC):
    """Abstract base class for all LLM providers."""

    def __init__(self) -> None:
        from sylvia_gm.provider._hooks import HookManager

        self.hooks: HookManager = HookManager()

    @abstractmethod
    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        """Send a chat completion request and return the complete response.

        Args:
            messages: List of message dicts (role, content).
            tools: Optional tool definitions for function calling.
            stream: If True, may delegate to `generate_stream` internally.
            timeout: Seconds before ProviderTimeoutError.
            **kwargs: Additional provider-specific parameters.

        Returns:
            A complete ProviderResponse with content, tool calls, and usage.
        """
        ...

    @abstractmethod
    def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        """Stream chat completion chunks as they arrive.

        ``timeout`` is the maximum wait for the initial response or the next
        stream chunk. Receiving a chunk resets that inactivity window.

        Desktop Builder turns call this method. Providers offered for that
        journey must implement streaming; generate() alone does not satisfy
        the desktop contract. Report unsupported streaming clearly instead
        of presenting it as a completed response.
        """
        ...

    @abstractmethod
    async def close(self) -> None:
        """Release all resources (HTTP connections, pools, etc.).

        Must be idempotent — calling twice is safe.
        """
        ...


class ProviderTransport(ABC):
    """Abstract base for wire-format conversion.

    One transport per API shape: chat_completions, responses, anthropic_messages.
    The transport does NOT own the HTTP client or handle retries —
    it only converts messages and parses responses.
    """

    @abstractmethod
    def format_request(
        self,
        model_id: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        stream: bool,
        **kwargs: object,
    ) -> dict[str, Any]:
        """Convert internal messages → provider-native request dict."""

    @abstractmethod
    def parse_response(
        self,
        raw_response: Any,
        model_id: str,
    ) -> ProviderResponse:
        """Convert provider-native response → ProviderResponse."""

    @abstractmethod
    def parse_stream_chunk(
        self,
        raw_chunk: Any,
    ) -> ProviderStreamChunk | None:
        """Convert one provider-native SSE chunk → ProviderStreamChunk."""


class FakeProvider(Provider):
    """Provider that returns predetermined responses for testing.

    Use this in integration tests to simulate LLM outputs without
    calling a real API. Pass a list of :class:`ProviderResponse`
    objects — each ``generate()`` call consumes the next response.

    Example::

        fake = FakeProvider(responses=[
            ProviderResponse(
                tool_calls=[ToolCall(id="c1", name="harm", arguments='{}')],
            ),
            ProviderResponse(content="You strike the goblin!"),
        ])

    Streaming tests: pass ``stream_chunks`` — each ``generate_stream()``
    call yields the pre-configured chunks followed by a finish chunk.
    """

    def __init__(
        self,
        responses: list[ProviderResponse] | None = None,
        stream_chunks: list[ProviderStreamChunk] | None = None,
    ) -> None:
        super().__init__()
        self._responses: list[ProviderResponse] = responses or []
        self._stream_chunks: list[ProviderStreamChunk] = stream_chunks or []
        self._index = 0
        self._stream_index = 0
        self.calls: list[dict[str, Any]] = []

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        self.calls.append({"messages": messages, "tools": tools})
        if self._index < len(self._responses):
            response = self._responses[self._index]
            self._index += 1
            return response
        return ProviderResponse(content="I don't know what to do next.")

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        """Yield pre-configured stream chunks for testing."""
        self.calls.append({"messages": messages, "tools": tools})
        # Yield pre-configured chunks first
        for chunk in self._stream_chunks:
            yield chunk
        # If no chunks configured, yield the next non-streaming response as one chunk
        if not self._stream_chunks:
            if self._index < len(self._responses):
                resp = self._responses[self._index]
                self._index += 1
                yield ProviderStreamChunk(content=resp.content or "")
            else:
                yield ProviderStreamChunk(content="I don't know what to do next.")
        # Always yield a final finish_reason chunk
        yield ProviderStreamChunk(finish_reason="stop")

    async def close(self) -> None:
        """No-op — FakeProvider owns no connections."""
