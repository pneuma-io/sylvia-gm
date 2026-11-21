//! The coded error behind every rejected owner command.
//!
//! The Python owner turns an exception into `{"code": ..., "message": ..., "diagnostics": [...]}`.
//! `code` comes from the exception's `code` attribute (default `command_rejected`) and `message`
//! is `str(error)`. The desktop shows the message and branches on the code, and the GM model
//! reads many of these messages, so both are behaviour: copy them from the Python source byte
//! for byte and never reword them.

use crate::Json;

/// A rejection with the stable code and the exact message the Python owner would give.
#[derive(Debug, Clone, PartialEq, thiserror::Error)]
#[error("{message}")]
pub struct CodedError {
    /// Stable machine code, for example `command_rejected` or `stale_revision`.
    pub code: String,
    /// Player- or model-facing text, identical to Python's `str(error)`.
    pub message: String,
    /// Structured diagnostics (`diagnostic.as_payload()` in Python). Usually empty.
    pub diagnostics: Vec<Json>,
}

/// Result alias used across the port.
pub type CodedResult<T> = Result<T, CodedError>;

impl CodedError {
    /// A rejection with an explicit code.
    pub fn new(code: impl Into<String>, message: impl Into<String>) -> Self {
        Self { code: code.into(), message: message.into(), diagnostics: Vec::new() }
    }

    /// The default code Python uses when an exception carries none.
    pub fn rejected(message: impl Into<String>) -> Self {
        Self::new("command_rejected", message)
    }

    /// Attach diagnostics payloads.
    #[must_use]
    pub fn with_diagnostics(mut self, diagnostics: Vec<Json>) -> Self {
        self.diagnostics = diagnostics;
        self
    }

    /// The `error` object of an owner reply, in Python's key order.
    pub fn to_reply_error(&self) -> Json {
        let mut error = crate::JsonMap::new();
        error.insert("code".into(), Json::String(self.code.clone()));
        error.insert("message".into(), Json::String(self.message.clone()));
        error.insert("diagnostics".into(), Json::Array(self.diagnostics.clone()));
        Json::Object(error)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn reply_error_keeps_python_key_order() {
        let error = CodedError::rejected("campaign_id must be a nonempty string");
        let text = serde_json::to_string(&error.to_reply_error()).unwrap();
        assert_eq!(
            text,
            r#"{"code":"command_rejected","message":"campaign_id must be a nonempty string","diagnostics":[]}"#
        );
    }
}
