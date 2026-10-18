"""Provider exception hierarchy.

All provider-layer errors inherit from ProviderError and carry an ErrorCategory
describing the failure. Recovery policy belongs to the caller. Desktop sessions
do not automatically retry ordinary inference failures or switch providers. Explicit
working-context recovery may compact and resubmit before visible output; see the product
contract and agent._provider_resolver. Optional _failover and _fallback_chain
helpers interpret these categories only when a caller explicitly uses them.
"""

from __future__ import annotations

from enum import Enum


class ErrorCategory(Enum):
    """Standard error categories for provider errors.

    Categories classify failures without authorizing another model request:
    - RATE_LIMIT: the service rejected the request for rate or quota limits
    - AUTH_FAILURE: credentials were rejected
    - CONTEXT_OVERFLOW: the request exceeded the model's context capacity
    - PROVIDER_UNAVAILABLE: the service could not be reached or was unavailable
    - TRANSIENT: a potentially temporary failure, such as a timeout
    - INVALID_REQUEST: invalid request or response data
    """

    RATE_LIMIT = "rate_limit"
    AUTH_FAILURE = "auth_failure"
    CONTEXT_OVERFLOW = "context_overflow"
    OUTPUT_LIMIT = "output_limit"
    PROVIDER_UNAVAILABLE = "provider_unavailable"
    TRANSIENT = "transient"
    INVALID_REQUEST = "invalid_request"


class ProviderError(Exception):
    """Base exception for all provider-layer errors."""

    category: ErrorCategory = ErrorCategory.PROVIDER_UNAVAILABLE


class AuthenticationError(ProviderError):
    """Credentials rejected, typically reported as HTTP 401 or 403."""

    category = ErrorCategory.AUTH_FAILURE


class RateLimitError(ProviderError):
    """Rate or capacity rejection with retry_after metadata for the caller."""

    category = ErrorCategory.RATE_LIMIT

    def __init__(self, message: str = "", retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class ModelNotFoundError(ProviderError):
    """Model not found in registry or returned 404 from provider."""

    category = ErrorCategory.INVALID_REQUEST


class ProviderTimeoutError(ProviderError):
    """The request timed out; classified as a potentially temporary failure."""

    category = ErrorCategory.TRANSIENT


class ProviderConnectionError(ProviderError):
    """Network error / connection refused — provider unavailable."""

    category = ErrorCategory.PROVIDER_UNAVAILABLE

    def __init__(self, message: str = "", retry_after: float | None = None) -> None:
        super().__init__(message)
        self.retry_after = retry_after


class InvalidResponseError(ProviderError):
    """Malformed or unexpected response from the provider."""

    category = ErrorCategory.INVALID_REQUEST


class ProviderRefusalError(InvalidResponseError):
    """The model explicitly declined the request rather than returning malformed output."""


class ProviderPausedError(InvalidResponseError):
    """The native response requires a server-tool continuation Sylvia cannot perform."""


class InvalidRequestError(InvalidResponseError):
    """The service rejected the request, rather than returning malformed output."""


class ContextCompactionError(InvalidResponseError):
    """A returned continuation note could not safely replace the working context."""


class IncompleteProviderStreamError(InvalidResponseError):
    """A provider stream ended without its required terminal event."""


class InvalidToolArgumentsError(InvalidResponseError):
    """A completed provider message contains malformed streamed tool arguments."""


class TokenLimitError(ProviderError):
    """The request exceeded the model's context capacity."""

    category = ErrorCategory.CONTEXT_OVERFLOW

    @classmethod
    def provider_rejection(cls) -> TokenLimitError:
        """Create a safe context-rejection notice without reflecting provider request text."""
        return cls(
            "The provider rejected this request because of a context or token limit. "
            "Previously saved work is kept. Compact context, use a smaller step, or choose "
            "a model with more context capacity before retrying."
        )


class ContextCompactionRequiredError(TokenLimitError):
    """The runtime requests a smaller working context before continuing."""


class ProviderOutputLimitError(ProviderError):
    """Generation exhausted its reply allowance; shortening input is not the remedy."""

    category = ErrorCategory.OUTPUT_LIMIT


class ProviderContinuationRequiredError(ProviderOutputLimitError):
    """Valid native continuation that cannot be completed within this request's allowance."""

    def __init__(self, message: str, *, continuation_token: str, native_parts: object) -> None:
        super().__init__(message)
        # Opaque values stay private; neither exception text nor repr contains them.
        self.continuation_token = continuation_token
        self.native_parts = native_parts


class ProviderUnavailableError(ProviderError):
    """The provider is unreachable (502, 503, DNS failure, connection refused)."""

    category = ErrorCategory.PROVIDER_UNAVAILABLE


class ModelCapacityError(ProviderUnavailableError):
    """The selected model is temporarily at capacity, not out of account allowance."""


class StaleConnectionError(ProviderTimeoutError):
    """Stream has been silent for too long — possibly dead/hung."""

    category = ErrorCategory.TRANSIENT


def is_context_rejection(error: object) -> bool:
    """Recognize capacity errors without treating unsupported token/schema options as overflow."""
    text = str(error).casefold()
    phrases = (
        "context_length_exceeded",
        "context exceeded",
        "context window",
        "context_window",
        "context limit",
        "maximum context length",
        "prompt is too long",
        "too many input tokens",
        "too many tokens",
        "input token limit",
        "context length exceeded",
    )
    return any(phrase in text for phrase in phrases)
