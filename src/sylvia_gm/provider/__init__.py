"""LLM provider abstraction layer — public API.

Exports the provider ABC, registry, data types, exceptions,
model catalog, transports, hooks, usage tracker, diagnostics,
and the TestProvider for integration testing.
"""

from sylvia_gm.provider._base import (
    CacheUsage,
    FakeProvider,
    ModelInfo,
    Provider,
    ProviderConfig,
    ProviderResponse,
    ProviderStreamChunk,
    ProviderTransport,
    RetryConfig,
    TimeoutConfig,
    TokenUsage,
    ToolCall,
)
from sylvia_gm.provider._context_fit import (
    ContextFittingProvider,
    RequestTokenMeasurement,
    TokenCountPrecision,
)
from sylvia_gm.provider._credentials import Credential, CredentialPool
from sylvia_gm.provider._diagnostics import (
    DiagnosticsCollector,
    ProviderCallDiagnostic,
)
from sylvia_gm.provider._exceptions import (
    AuthenticationError,
    ErrorCategory,
    InvalidResponseError,
    ModelNotFoundError,
    ProviderConnectionError,
    ProviderError,
    ProviderTimeoutError,
    ProviderUnavailableError,
    RateLimitError,
    StaleConnectionError,
    TokenLimitError,
)
from sylvia_gm.provider._factory import ProviderFactory
from sylvia_gm.provider._failover import retry_with_backoff
from sylvia_gm.provider._fallback_chain import FallbackChain
from sylvia_gm.provider._hooks import (
    DiagnosticsHook,
    DumpHook,
    HookContext,
    HookManager,
    HookRegistry,
    LoggingHook,
    MetricsHook,
    ProviderHook,
    UsageTrackingHook,
)
from sylvia_gm.provider._models import build_catalog
from sylvia_gm.provider._openai_compat import (
    ChatCompletionsTransport,
    OpenAICompatProvider,
)
from sylvia_gm.provider._registry import ProviderRegistry
from sylvia_gm.provider._stream_health import StreamHealthMonitor
from sylvia_gm.provider._sylvia_cloud import SylviaCloudProvider
from sylvia_gm.provider._usage_tracker import (
    ModelUsageSnapshot,
    SessionUsageSnapshot,
    SessionUsageTracker,
)

# Anthropic is optional — only import if available
try:
    from sylvia_gm.provider._anthropic import (
        AnthropicMessagesTransport,
        AnthropicProvider,
    )

    _HAS_ANTHROPIC = True
except ImportError:
    _HAS_ANTHROPIC = False
    AnthropicProvider = None  # type: ignore[assignment]
    AnthropicMessagesTransport = None  # type: ignore[assignment]

__all__ = [
    # ABC + types
    "CacheUsage",
    "FakeProvider",
    "Provider",
    "ContextFittingProvider",
    "RequestTokenMeasurement",
    "TokenCountPrecision",
    "ProviderResponse",
    "ProviderStreamChunk",
    "ProviderConfig",
    "ProviderTransport",
    "ModelInfo",
    "TimeoutConfig",
    "TokenUsage",
    "ToolCall",
    "RetryConfig",
    # Registry
    "ProviderRegistry",
    # Factory
    "ProviderFactory",
    # Providers
    "OpenAICompatProvider",
    "SylviaCloudProvider",
    "ChatCompletionsTransport",
    "FallbackChain",
    # Credentials
    "Credential",
    "CredentialPool",
    # Hooks
    "HookContext",
    "HookManager",
    "HookRegistry",
    "ProviderHook",
    "LoggingHook",
    "MetricsHook",
    "UsageTrackingHook",
    "DiagnosticsHook",
    "DumpHook",
    # Usage Tracking
    "SessionUsageTracker",
    "SessionUsageSnapshot",
    "ModelUsageSnapshot",
    # Diagnostics
    "ProviderCallDiagnostic",
    "DiagnosticsCollector",
    # Exceptions
    "ErrorCategory",
    "ProviderError",
    "AuthenticationError",
    "RateLimitError",
    "ModelNotFoundError",
    "ProviderTimeoutError",
    "ProviderConnectionError",
    "InvalidResponseError",
    "TokenLimitError",
    "ProviderUnavailableError",
    "StaleConnectionError",
    # Utilities
    "build_catalog",
    "retry_with_backoff",
    "StreamHealthMonitor",
]

# Conditionally add Anthropic exports
if _HAS_ANTHROPIC:
    __all__.extend(["AnthropicProvider", "AnthropicMessagesTransport"])
