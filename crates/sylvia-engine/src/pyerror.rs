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
    /// `OverflowError`.
    OverflowError,
    /// `ZeroDivisionError`.
    ZeroDivisionError,
    /// `RuntimeError` (the package budget; the evaluator lets it through unwrapped).
    RuntimeError,
    /// `MemoryError`.
    MemoryError,
}

impl PyErrorKind {
    /// The Python class name, as `type(error).__name__` gives it.
    pub const fn name(self) -> &'static str {
        match self {
            Self::ValueError => "ValueError",
            Self::TypeError => "TypeError",
            Self::IndexError => "IndexError",
            Self::OverflowError => "OverflowError",
            Self::ZeroDivisionError => "ZeroDivisionError",
            Self::RuntimeError => "RuntimeError",
            Self::MemoryError => "MemoryError",
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

    /// An exception of any class.
    pub fn of(kind: PyErrorKind, message: impl Into<String>) -> Self {
        Self { kind, message: message.into() }
    }
}

impl PyError {
    /// The owner's rejection for this exception: code `command_rejected`, `str(error)` as the
    /// message, and the Python class name for diagnostics and saved reports.
    pub fn to_coded(&self) -> sylvia_base::CodedError {
        sylvia_base::CodedError::rejected(self.message.clone()).with_error_type(self.kind.name())
    }
}

/// The refusal for an integer beyond the signed 64-bit range, which Python would compute
/// exactly (`differences.md` row 5).
pub fn int_range_error() -> PyError {
    PyError::value(sylvia_base::pystr::INT_RANGE_MESSAGE)
}

/// `a + b` for engine integers, refused beyond `i64`.
pub fn add(a: i64, b: i64) -> PyResult<i64> {
    a.checked_add(b).ok_or_else(int_range_error)
}

/// `a - b` for engine integers, refused beyond `i64`.
pub fn sub(a: i64, b: i64) -> PyResult<i64> {
    a.checked_sub(b).ok_or_else(int_range_error)
}

/// `sum(values)` for engine integers, refused beyond `i64`.
pub fn sum(values: &[i64]) -> PyResult<i64> {
    values.iter().try_fold(0i64, |total, value| add(total, *value))
}

/// Result alias for engine code that mirrors a Python built-in exception.
pub type PyResult<T> = Result<T, PyError>;
