//! The provider error hierarchy, from `provider/_exceptions.py`.
//!
//! Python models these as exception classes with single inheritance, and callers branch with
//! `isinstance`. Rust keeps one error type with a [`ErrorKind`] naming the concrete class;
//! [`ProviderError::is`] answers `isinstance` by walking the same class tree. The message is
//! `str(error)` and is behaviour: it is copied byte for byte and never reworded.

use sylvia_base::Json;

/// `ErrorCategory`: classifies a failure without authorising another request.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ErrorCategory {
    RateLimit,
    AuthFailure,
    ContextOverflow,
    OutputLimit,
    ProviderUnavailable,
    Transient,
    InvalidRequest,
}

impl ErrorCategory {
    /// The enum's Python value.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::RateLimit => "rate_limit",
            Self::AuthFailure => "auth_failure",
            Self::ContextOverflow => "context_overflow",
            Self::OutputLimit => "output_limit",
            Self::ProviderUnavailable => "provider_unavailable",
            Self::Transient => "transient",
            Self::InvalidRequest => "invalid_request",
        }
    }
}

/// The concrete Python exception class.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum ErrorKind {
    /// `ProviderError` itself.
    Provider,
    /// `AuthenticationError`.
    Authentication,
    /// `RateLimitError`.
    RateLimit,
    /// `ModelNotFoundError`.
    ModelNotFound,
    /// `ProviderTimeoutError`.
    Timeout,
    /// `StaleConnectionError` (a `ProviderTimeoutError`).
    StaleConnection,
    /// `ProviderConnectionError`.
    Connection,
    /// `InvalidResponseError`.
    InvalidResponse,
    /// `ProviderRefusalError` (an `InvalidResponseError`).
    Refusal,
    /// `ProviderPausedError` (an `InvalidResponseError`).
    Paused,
    /// `InvalidRequestError` (an `InvalidResponseError`).
    InvalidRequest,
    /// `ContextCompactionError` (an `InvalidResponseError`).
    ContextCompaction,
    /// `IncompleteProviderStreamError` (an `InvalidResponseError`).
    IncompleteStream,
    /// `InvalidToolArgumentsError` (an `InvalidResponseError`).
    InvalidToolArguments,
    /// `TokenLimitError`.
    TokenLimit,
    /// `ContextCompactionRequiredError` (a `TokenLimitError`).
    ContextCompactionRequired,
    /// `ProviderOutputLimitError`.
    OutputLimit,
    /// `ProviderContinuationRequiredError` (a `ProviderOutputLimitError`).
    ContinuationRequired,
    /// `ProviderUnavailableError`.
    Unavailable,
    /// `ModelCapacityError` (a `ProviderUnavailableError`).
    ModelCapacity,
}

impl ErrorKind {
    /// Every class, for tests and vectors.
    pub const ALL: [ErrorKind; 20] = [
        Self::Provider,
        Self::Authentication,
        Self::RateLimit,
        Self::ModelNotFound,
        Self::Timeout,
        Self::StaleConnection,
        Self::Connection,
        Self::InvalidResponse,
        Self::Refusal,
        Self::Paused,
        Self::InvalidRequest,
        Self::ContextCompaction,
        Self::IncompleteStream,
        Self::InvalidToolArguments,
        Self::TokenLimit,
        Self::ContextCompactionRequired,
        Self::OutputLimit,
        Self::ContinuationRequired,
        Self::Unavailable,
        Self::ModelCapacity,
    ];

    /// The Python class name (`type(error).__name__`), which the owner records in diagnostics.
    pub fn class_name(self) -> &'static str {
        match self {
            Self::Provider => "ProviderError",
            Self::Authentication => "AuthenticationError",
            Self::RateLimit => "RateLimitError",
            Self::ModelNotFound => "ModelNotFoundError",
            Self::Timeout => "ProviderTimeoutError",
            Self::StaleConnection => "StaleConnectionError",
            Self::Connection => "ProviderConnectionError",
            Self::InvalidResponse => "InvalidResponseError",
            Self::Refusal => "ProviderRefusalError",
            Self::Paused => "ProviderPausedError",
            Self::InvalidRequest => "InvalidRequestError",
            Self::ContextCompaction => "ContextCompactionError",
            Self::IncompleteStream => "IncompleteProviderStreamError",
            Self::InvalidToolArguments => "InvalidToolArgumentsError",
            Self::TokenLimit => "TokenLimitError",
            Self::ContextCompactionRequired => "ContextCompactionRequiredError",
            Self::OutputLimit => "ProviderOutputLimitError",
            Self::ContinuationRequired => "ProviderContinuationRequiredError",
            Self::Unavailable => "ProviderUnavailableError",
            Self::ModelCapacity => "ModelCapacityError",
        }
    }

    /// The Python base class, or `None` for `ProviderError`.
    pub fn parent(self) -> Option<ErrorKind> {
        match self {
            Self::Provider => None,
            Self::StaleConnection => Some(Self::Timeout),
            Self::Refusal
            | Self::Paused
            | Self::InvalidRequest
            | Self::ContextCompaction
            | Self::IncompleteStream
            | Self::InvalidToolArguments => Some(Self::InvalidResponse),
            Self::ContextCompactionRequired => Some(Self::TokenLimit),
            Self::ContinuationRequired => Some(Self::OutputLimit),
            Self::ModelCapacity => Some(Self::Unavailable),
            _ => Some(Self::Provider),
        }
    }

    /// `issubclass(self, other)`.
    pub fn is_a(self, other: ErrorKind) -> bool {
        let mut current = Some(self);
        while let Some(kind) = current {
            if kind == other {
                return true;
            }
            current = kind.parent();
        }
        false
    }

    /// The class attribute `category`, inherited where a subclass does not set its own.
    pub fn category(self) -> ErrorCategory {
        match self {
            Self::Provider | Self::Connection | Self::Unavailable | Self::ModelCapacity => {
                ErrorCategory::ProviderUnavailable
            }
            Self::Authentication => ErrorCategory::AuthFailure,
            Self::RateLimit => ErrorCategory::RateLimit,
            Self::Timeout | Self::StaleConnection => ErrorCategory::Transient,
            Self::TokenLimit | Self::ContextCompactionRequired => ErrorCategory::ContextOverflow,
            Self::OutputLimit | Self::ContinuationRequired => ErrorCategory::OutputLimit,
            Self::ModelNotFound
            | Self::InvalidResponse
            | Self::Refusal
            | Self::Paused
            | Self::InvalidRequest
            | Self::ContextCompaction
            | Self::IncompleteStream
            | Self::InvalidToolArguments => ErrorCategory::InvalidRequest,
        }
    }

    /// Whether the class accepts a `retry_after` (only these two define the attribute).
    pub fn has_retry_after(self) -> bool {
        matches!(self, Self::RateLimit | Self::Connection)
    }
}

/// The opaque values a `ProviderContinuationRequiredError` keeps privately.
#[derive(Debug, Clone, PartialEq)]
pub struct Continuation {
    /// Never appears in the message or in `Debug` output of the message.
    pub continuation_token: String,
    /// Native parts the runtime returned.
    pub native_parts: Json,
}

/// One provider failure: the class, `str(error)` and the attributes Python attaches.
#[derive(Debug, Clone, PartialEq, thiserror::Error)]
#[error("{message}")]
pub struct ProviderError {
    /// The concrete Python class.
    pub kind: ErrorKind,
    /// `str(error)`.
    pub message: String,
    /// `retry_after` for `RateLimitError` and `ProviderConnectionError`; `None` elsewhere.
    pub retry_after: Option<f64>,
    /// Set by the session loop once the player's message was durably accepted.
    pub accepted_message_id: Option<String>,
    /// Only for `ProviderContinuationRequiredError`.
    pub continuation: Option<Continuation>,
}

impl ProviderError {
    /// An error of `kind` with `message`.
    pub fn new(kind: ErrorKind, message: impl Into<String>) -> Self {
        Self {
            kind,
            message: message.into(),
            retry_after: None,
            accepted_message_id: None,
            continuation: None,
        }
    }

    /// `isinstance(error, kind)`.
    pub fn is(&self, kind: ErrorKind) -> bool {
        self.kind.is_a(kind)
    }

    /// `error.category`.
    pub fn category(&self) -> ErrorCategory {
        self.kind.category()
    }

    /// `RateLimitError(message, retry_after)`.
    pub fn rate_limit(message: impl Into<String>, retry_after: Option<f64>) -> Self {
        Self { retry_after, ..Self::new(ErrorKind::RateLimit, message) }
    }

    /// `ProviderConnectionError(message, retry_after=...)`.
    pub fn connection(message: impl Into<String>, retry_after: Option<f64>) -> Self {
        Self { retry_after, ..Self::new(ErrorKind::Connection, message) }
    }

    /// `ProviderContinuationRequiredError(message, continuation_token=..., native_parts=...)`.
    pub fn continuation_required(
        message: impl Into<String>,
        continuation_token: impl Into<String>,
        native_parts: Json,
    ) -> Self {
        Self {
            continuation: Some(Continuation { continuation_token: continuation_token.into(), native_parts }),
            ..Self::new(ErrorKind::ContinuationRequired, message)
        }
    }

    /// `TokenLimitError.provider_rejection()`: never reflects the provider's request text.
    pub fn token_limit_rejection() -> Self {
        Self::new(
            ErrorKind::TokenLimit,
            "The provider rejected this request because of a context or token limit. \
             Previously saved work is kept. Compact context, use a smaller step, or choose \
             a model with more context capacity before retrying.",
        )
    }

    /// The same error marked with the accepted player message.
    #[must_use]
    pub fn with_accepted_message_id(mut self, message_id: impl Into<String>) -> Self {
        self.accepted_message_id = Some(message_id.into());
        self
    }
}

/// Result alias for provider calls.
pub type ProviderResult<T> = Result<T, ProviderError>;

const CONTEXT_PHRASES: [&str; 11] = [
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
];

/// `is_context_rejection(error)` applied to `str(error)`: casefolded, then searched for the
/// capacity phrases (unsupported token or schema options are not overflow).
pub fn is_context_rejection(text: &str) -> bool {
    let folded = sylvia_base::pystr::casefold(text);
    CONTEXT_PHRASES.iter().any(|phrase| folded.contains(phrase))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn hierarchy_matches_isinstance() {
        assert!(ErrorKind::StaleConnection.is_a(ErrorKind::Timeout));
        assert!(ErrorKind::StaleConnection.is_a(ErrorKind::Provider));
        assert!(!ErrorKind::StaleConnection.is_a(ErrorKind::Connection));
        assert!(ErrorKind::ContextCompaction.is_a(ErrorKind::InvalidResponse));
        assert!(ErrorKind::ModelCapacity.is_a(ErrorKind::Unavailable));
        assert!(!ErrorKind::TokenLimit.is_a(ErrorKind::InvalidResponse));
    }
}
