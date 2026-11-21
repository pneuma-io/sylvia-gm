//! Python exceptions the engine reproduces.
//!
//! Engine code in Python raises built-in exceptions whose class decides who catches them
//! (`ValueError` is caught by the effect executor and by the owner, `TypeError` is not always)
//! and whose `str()` is read by the model or the player. Rust keeps both: the class as
//! [`PyErrorKind`] and the text, copied byte for byte from the snapshot, as `message`.

/// The Python exception class an engine error stands for.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash)]
pub enum PyErrorKind {
    /// `ValueError`.
    ValueError,
    /// `TypeError`.
    TypeError,
    /// `IndexError`.
    IndexError,
}

impl PyErrorKind {
    /// The Python class name, as `type(error).__name__` gives it.
    pub const fn name(self) -> &'static str {
        match self {
            Self::ValueError => "ValueError",
            Self::TypeError => "TypeError",
            Self::IndexError => "IndexError",
        }
    }
}

/// A Python built-in exception: its class and `str(error)`.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("{message}")]
pub struct PyError {
    /// Which built-in exception Python raises.
    pub kind: PyErrorKind,
    /// `str(error)`, exactly as Python formats it.
    pub message: String,
}

impl PyError {
    /// `ValueError(message)`.
    pub fn value(message: impl Into<String>) -> Self {
        Self { kind: PyErrorKind::ValueError, message: message.into() }
    }

    /// `TypeError(message)`.
    pub fn type_error(message: impl Into<String>) -> Self {
        Self { kind: PyErrorKind::TypeError, message: message.into() }
    }

    /// `IndexError(message)`.
    pub fn index(message: impl Into<String>) -> Self {
        Self { kind: PyErrorKind::IndexError, message: message.into() }
    }
}

/// Result alias for engine code that mirrors a Python built-in exception.
pub type PyResult<T> = Result<T, PyError>;
