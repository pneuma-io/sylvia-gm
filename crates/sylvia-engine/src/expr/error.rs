//! `ExpressionError` and `FieldNotFoundError` from `runtime/_exceptions.py`.
//!
//! Python builds the message once in `ExpressionError.__init__`: with an expression and a
//! position, `"{message} at position {position} in '{expr}'"`; with an expression only,
//! `"{message} in expression '{expr}'"`; otherwise the message alone. The expression is quoted
//! with plain single quotes, not `repr`.

/// Which `RuntimeException` subclass an expression error is.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ExpressionErrorKind {
    /// `ExpressionError`.
    Expression,
    /// `FieldNotFoundError(ExpressionError)`: a field path could not be resolved.
    FieldNotFound,
}

/// An expression parse or evaluation error; `message` is `str(error)`.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[error("{message}")]
pub struct ExpressionError {
    /// `str(error)`, the full message.
    pub message: String,
    /// The Python class.
    pub kind: ExpressionErrorKind,
    /// `error.expr` (empty when none was given).
    pub expr: String,
    /// `error.position`, `-1` when none was given (in code points, as Python counts).
    pub position: i64,
}

impl ExpressionError {
    /// `ExpressionError(message)`.
    pub fn new(message: impl Into<String>) -> Self {
        Self::build(message.into(), String::new(), -1, ExpressionErrorKind::Expression)
    }

    /// `ExpressionError(message, expr=expr)`.
    pub fn in_expression(message: impl Into<String>, expr: &str) -> Self {
        Self::build(message.into(), expr.to_owned(), -1, ExpressionErrorKind::Expression)
    }

    /// `ExpressionError(message, expr=expr, position=position)`.
    pub fn at(message: impl Into<String>, expr: &str, position: usize) -> Self {
        let position = i64::try_from(position).expect("position fits in i64");
        Self::build(message.into(), expr.to_owned(), position, ExpressionErrorKind::Expression)
    }

    /// `ExpressionError(message, position=position)`: no expression, so the message stands
    /// alone, as the parser's errors do.
    pub fn at_position(message: impl Into<String>, position: usize) -> Self {
        let position = i64::try_from(position).expect("position fits in i64");
        Self::build(message.into(), String::new(), position, ExpressionErrorKind::Expression)
    }

    /// `FieldNotFoundError(message)`.
    pub fn field_not_found(message: impl Into<String>) -> Self {
        Self::build(message.into(), String::new(), -1, ExpressionErrorKind::FieldNotFound)
    }

    fn build(message: String, expr: String, position: i64, kind: ExpressionErrorKind) -> Self {
        let message = if !expr.is_empty() && position >= 0 {
            format!("{message} at position {position} in '{expr}'")
        } else if !expr.is_empty() {
            format!("{message} in expression '{expr}'")
        } else {
            message
        };
        Self { message, kind, expr, position }
    }

    /// `type(error).__name__`.
    pub const fn class_name(&self) -> &'static str {
        match self.kind {
            ExpressionErrorKind::Expression => "ExpressionError",
            ExpressionErrorKind::FieldNotFound => "FieldNotFoundError",
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn messages_follow_python() {
        assert_eq!(ExpressionError::at("Bad", "a b", 2).message, "Bad at position 2 in 'a b'");
        assert_eq!(ExpressionError::in_expression("Long", "x").message, "Long in expression 'x'");
        assert_eq!(ExpressionError::at_position("Expected EOF", 3).message, "Expected EOF");
        assert_eq!(ExpressionError::field_not_found("gone").class_name(), "FieldNotFoundError");
    }
}
