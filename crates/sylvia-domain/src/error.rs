//! The Python exceptions the domain layer raises, and `domain/errors.py`'s diagnostics.
//!
//! Python's domain code raises `ValueError` for invalid content and `TypeError` for values of
//! the wrong kind. Callers catch them differently: the owner turns an uncaught `ValueError` into
//! a `command_rejected` reply with `str(error)` as the message (and the exception's
//! `diagnostics`, when it has them), and several parsers wrap both into one message of their
//! own. The message is behaviour and is copied byte for byte.

use sylvia_base::{CodedError, Json, JsonMap};

/// One bounded, portable validation failure shared by Builder and the desktop
/// (`RulesetDiagnostic`).
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct RulesetDiagnostic {
    pub code: String,
    pub phase: String,
    pub message: String,
    pub file: Option<String>,
    pub path: Option<String>,
    pub package_id: Option<String>,
    pub item_id: Option<String>,
    pub expected: Option<String>,
    pub actual: Option<String>,
    pub guidance: Option<String>,
}

impl RulesetDiagnostic {
    /// `as_payload()`: safe size-bounded fields without machine-private paths or source dumps.
    pub fn as_payload(&self) -> JsonMap {
        let mut payload = JsonMap::new();
        let fields: [(&str, Option<&str>); 10] = [
            ("code", Some(&self.code)),
            ("phase", Some(&self.phase)),
            ("message", Some(&self.message)),
            ("file", self.file.as_deref()),
            ("path", self.path.as_deref()),
            ("package_id", self.package_id.as_deref()),
            ("item_id", self.item_id.as_deref()),
            ("expected", self.expected.as_deref()),
            ("actual", self.actual.as_deref()),
            ("guidance", self.guidance.as_deref()),
        ];
        for (name, value) in fields {
            let Some(value) = value else { continue };
            let mut text = value.replace('\\', "/");
            if name == "file" && (text.starts_with('/') || text.contains(":/")) {
                text = text.rsplit('/').next().unwrap_or_default().to_owned();
            }
            // `text[:500]` counts code points.
            let text: String = text.chars().take(500).collect();
            payload.insert(name.to_owned(), Json::String(text));
        }
        payload
    }
}

/// A Python `ValueError` or `TypeError` with its exact `str(error)`.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
pub enum DomainError {
    /// `raise ValueError(message)`.
    #[error("{0}")]
    Value(String),
    /// `raise TypeError(message)`.
    #[error("{0}")]
    Type(String),
    /// A `ValueError` subclass that carries diagnostics, such as `ContentShapeError`.
    #[error("{message}")]
    Diagnosed {
        /// `type(error).__name__`, for example `ContentShapeError`.
        error_type: &'static str,
        /// `str(error)`.
        message: String,
        /// `error.diagnostics`.
        diagnostics: Vec<RulesetDiagnostic>,
    },
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
            Self::Value(message) | Self::Type(message) | Self::Diagnosed { message, .. } => message,
        }
    }

    /// `error.diagnostics`, empty for a plain `ValueError` or `TypeError`.
    pub fn diagnostics(&self) -> &[RulesetDiagnostic] {
        match self {
            Self::Diagnosed { diagnostics, .. } => diagnostics,
            _ => &[],
        }
    }

    /// `type(error).__name__` of the exception Python raised.
    pub fn python_class(&self) -> &'static str {
        match self {
            Self::Value(_) => "ValueError",
            Self::Type(_) => "TypeError",
            Self::Diagnosed { error_type, .. } => error_type,
        }
    }

    /// Whether Python would have raised a `ValueError` (or a subclass).
    pub fn is_value_error(&self) -> bool {
        !matches!(self, Self::Type(_))
    }

    /// The owner reply for this error if it reaches the owner uncaught.
    ///
    /// The Python owner catches `ValueError` and replies `command_rejected` with `str(error)` and
    /// `[d.as_payload() for d in error.diagnostics]`; `error_type` is `type(error).__name__`. It does not catch `TypeError`: that becomes
    /// its `internal_error` reply, which the owner lane writes, so this returns `None`.
    pub fn rejection(&self) -> Option<CodedError> {
        match self {
            Self::Type(_) => None,
            other => Some(
                CodedError::rejected(other.message())
                    .with_diagnostics(
                        other.diagnostics().iter().map(|d| Json::Object(d.as_payload())).collect(),
                    )
                    .with_error_type(other.python_class()),
            ),
        }
    }
}

/// Result alias for domain validation.
pub type DomainResult<T> = Result<T, DomainError>;

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn payload_bounds_and_strips_private_paths() {
        let diagnostic = RulesetDiagnostic {
            code: "c".into(),
            phase: "content".into(),
            message: "é".repeat(600),
            file: Some("C:\\Users\\someone\\pkg\\ruleset.json".into()),
            path: Some("$.a\\b".into()),
            ..Default::default()
        };
        let payload = diagnostic.as_payload();
        assert_eq!(payload["file"], "ruleset.json");
        assert_eq!(payload["path"], "$.a/b");
        assert_eq!(payload["message"].as_str().unwrap().chars().count(), 500);
        assert_eq!(payload.keys().collect::<Vec<_>>(), ["code", "phase", "message", "file", "path"]);
    }

    #[test]
    fn rejections_name_the_python_class() {
        assert_eq!(DomainError::value("x").rejection().unwrap().error_type, Some("ValueError"));
        let shaped = DomainError::Diagnosed {
            error_type: "ContentShapeError",
            message: "m".into(),
            diagnostics: vec![RulesetDiagnostic { code: "c".into(), ..Default::default() }],
        };
        let reply = shaped.rejection().unwrap();
        assert_eq!(reply.error_type, Some("ContentShapeError"));
        assert_eq!(reply.diagnostics.len(), 1);
        assert!(DomainError::type_error("x").rejection().is_none());
    }
}
