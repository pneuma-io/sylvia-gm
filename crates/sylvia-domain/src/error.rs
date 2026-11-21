//! The two Python exception classes the domain layer raises.
//!
//! Python's domain code raises `ValueError` for invalid content and `TypeError` for values of
//! the wrong kind. Callers catch them differently: the owner turns an uncaught `ValueError` into
//! a `command_rejected` reply with `str(error)` as the message, and several parsers wrap both
//! into one message of their own. The message is behaviour and is copied byte for byte.

use sylvia_base::CodedError;

/// A Python `ValueError` or `TypeError` with its exact `str(error)`.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum DomainError {
    /// `raise ValueError(message)`.
    #[error("{0}")]
    Value(String),
    /// `raise TypeError(message)`.
    #[error("{0}")]
    Type(String),
}

impl DomainError {
    /// `ValueError(message)`.
    pub fn value(message: impl Into<String>) -> Self {
        Self::Value(message.into())
    }

    /// `TypeError(message)`.
    pub fn type_error(message: impl Into<String>) -> Self {
        Self::Type(message.into())
    }

    /// `str(error)`.
    pub fn message(&self) -> &str {
        match self {
            Self::Value(message) | Self::Type(message) => message,
        }
    }

    /// Whether Python would have raised a `ValueError`.
    pub fn is_value_error(&self) -> bool {
        matches!(self, Self::Value(_))
    }
}

impl DomainError {
    /// The owner reply for this error if it reaches the owner uncaught.
    ///
    /// The Python owner catches `ValueError` and replies `command_rejected` with `str(error)`.
    /// It does not catch `TypeError`: that becomes its `internal_error` reply, which the owner
    /// lane writes, so this returns `None`.
    pub fn rejection(&self) -> Option<CodedError> {
        match self {
            Self::Value(message) => Some(CodedError::rejected(message.clone())),
            Self::Type(_) => None,
        }
    }
}

/// Result alias for domain validation.
pub type DomainResult<T> = Result<T, DomainError>;
