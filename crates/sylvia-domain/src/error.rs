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

/// One privacy-safe schema problem a model can correct without seeing values
/// (`MechanicsValidationIssue`).
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct MechanicsValidationIssue {
    pub path: String,
    pub kind: String,
    pub expected_type: Option<String>,
    pub actual_type: Option<String>,
    pub parent_path: Option<String>,
    pub allowed_values: Vec<String>,
    /// A Python `int` of any size, so kept as the JSON number.
    pub minimum: Option<serde_json::Number>,
    pub maximum: Option<serde_json::Number>,
    pub actual_length: Option<i64>,
}

impl MechanicsValidationIssue {
    /// `as_payload()`: schema facts and submitted types and lengths, never submitted text.
    pub fn as_payload(&self) -> JsonMap {
        let mut payload = JsonMap::new();
        payload.insert("path".into(), Json::from(self.path.as_str()));
        payload.insert("kind".into(), Json::from(self.kind.as_str()));
        let text = [
            ("expected_type", &self.expected_type),
            ("actual_type", &self.actual_type),
            ("parent_path", &self.parent_path),
        ];
        for (name, value) in text {
            if let Some(value) = value {
                payload.insert(name.into(), Json::from(value.as_str()));
            }
        }
        for (name, value) in [("minimum", &self.minimum), ("maximum", &self.maximum)] {
            if let Some(value) = value {
                payload.insert(name.into(), Json::Number(value.clone()));
            }
        }
        if let Some(length) = self.actual_length {
            payload.insert("actual_length".into(), Json::from(length));
        }
        if !self.allowed_values.is_empty() {
            payload.insert(
                "allowed_values".into(),
                Json::Array(self.allowed_values.iter().map(|v| Json::from(v.as_str())).collect()),
            );
        }
        payload
    }
}

/// The extra fields of a `MechanicsRejected` (and `RulesetPreconditionRejected`).
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct MechanicsDetails {
    pub field_paths: Vec<String>,
    /// At most 64, as Python keeps `validation_issues[:64]`.
    pub validation_issues: Vec<MechanicsValidationIssue>,
    pub state_changed: bool,
    /// At most 64.
    pub refusal_reasons: Vec<String>,
}

/// A Python exception the domain raises, with its exact `str(error)`.
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
    /// An `AuthorityError` (a `RuntimeError` in Python): `CommandRejected`, `MechanicsRejected`,
    /// `StaleRevision` and the rest, with the stable `code` the owner replies with.
    #[error("{message}")]
    Rejected {
        /// `type(error).__name__`.
        error_type: &'static str,
        /// `error.code`.
        code: String,
        /// `str(error)`.
        message: String,
        /// `error.diagnostics` (at most 16).
        diagnostics: Vec<RulesetDiagnostic>,
        /// The fields of a `MechanicsRejected`.
        mechanics: Option<Box<MechanicsDetails>>,
    },
    /// An exception neither the domain nor the owner catches (`KeyError`, `AttributeError`,
    /// `IndexError`, `zlib.error`, `EOFError`): the owner's `internal_error`.
    #[error("{message}")]
    Uncaught {
        /// `type(error).__name__`.
        error_type: &'static str,
        /// `str(error)`.
        message: String,
    },
}

/// `reason` is model-safe: nonempty, trimmed, at most 500 code points, no control characters.
fn model_safe(reason: &str) -> bool {
    !reason.is_empty()
        && reason == sylvia_base::pystr::strip(reason)
        && sylvia_base::pystr::len(reason) <= 500
        && !reason.chars().any(|c| (c as u32) < 32)
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

    /// `CommandRejected(message)` with the default code `command_rejected`.
    pub fn command_rejected(message: impl Into<String>) -> Self {
        Self::command_rejected_with(message, "command_rejected", Vec::new())
    }

    /// `CommandRejected(message, code=..., diagnostics=...)`; Python keeps `diagnostics[:16]`.
    pub fn command_rejected_with(
        message: impl Into<String>,
        code: impl Into<String>,
        mut diagnostics: Vec<RulesetDiagnostic>,
    ) -> Self {
        diagnostics.truncate(16);
        Self::Rejected {
            error_type: "CommandRejected",
            code: code.into(),
            message: message.into(),
            diagnostics,
            mechanics: None,
        }
    }

    /// An `AuthorityError` subclass with a class code: `SchemaIdentityError`
    /// (`schema_identity_mismatch`), `WrongOwner`, `WrongWriter`, `StaleRevision`,
    /// `IdempotencyConflict`, or `OwnerConflict` (no code of its own: `command_rejected`).
    pub fn authority(error_type: &'static str, message: impl Into<String>) -> Self {
        let code = match error_type {
            "SchemaIdentityError" => "schema_identity_mismatch",
            "WrongOwner" => "wrong_owner",
            "WrongWriter" => "wrong_writer",
            "StaleRevision" => "stale_revision",
            "IdempotencyConflict" => "idempotency_conflict",
            _ => "command_rejected",
        };
        Self::Rejected {
            error_type,
            code: code.into(),
            message: message.into(),
            diagnostics: Vec::new(),
            mechanics: None,
        }
    }

    /// `MechanicsRejected(message, code=..., field_paths=..., validation_issues=...,
    /// state_changed=..., refusal_reasons=...)`. Python raises `ValueError` while constructing
    /// it when a refusal reason is not model-safe.
    pub fn mechanics_rejected(
        message: impl Into<String>,
        code: impl Into<String>,
        mut details: MechanicsDetails,
    ) -> DomainResult<Self> {
        details.validation_issues.truncate(64);
        details.refusal_reasons.truncate(64);
        if details.refusal_reasons.iter().any(|reason| !model_safe(reason)) {
            return Err(Self::value("Ruleset precondition reasons are not model-safe"));
        }
        Ok(Self::Rejected {
            error_type: "MechanicsRejected",
            code: code.into(),
            message: message.into(),
            diagnostics: Vec::new(),
            mechanics: Some(Box::new(details)),
        })
    }

    /// `RulesetPreconditionRejected(safe_reason, reasons=...)`.
    pub fn ruleset_precondition_rejected(safe_reason: &str, reasons: &[String]) -> DomainResult<Self> {
        if !model_safe(safe_reason) {
            return Err(Self::value("Ruleset precondition reason is not model-safe"));
        }
        let mut refusal_reasons: Vec<String> = Vec::new();
        for reason in std::iter::once(safe_reason).chain(reasons.iter().map(String::as_str)) {
            if !refusal_reasons.iter().any(|existing| existing == reason) {
                refusal_reasons.push(reason.to_owned());
            }
        }
        let details = MechanicsDetails { refusal_reasons, ..Default::default() };
        match Self::mechanics_rejected(safe_reason, "ruleset_precondition_failed", details)? {
            Self::Rejected { code, message, diagnostics, mechanics, .. } => Ok(Self::Rejected {
                error_type: "RulesetPreconditionRejected",
                code,
                message,
                diagnostics,
                mechanics,
            }),
            _ => unreachable!("mechanics_rejected builds a rejection"),
        }
    }

    /// An uncaught exception of the named class.
    pub fn uncaught(error_type: &'static str, message: impl Into<String>) -> Self {
        Self::Uncaught { error_type, message: message.into() }
    }

    /// `str(error)`.
    pub fn message(&self) -> &str {
        match self {
            Self::Value(message)
            | Self::Type(message)
            | Self::Diagnosed { message, .. }
            | Self::Rejected { message, .. }
            | Self::Uncaught { message, .. } => message,
        }
    }

    /// `error.diagnostics`, empty for a plain `ValueError` or `TypeError`.
    pub fn diagnostics(&self) -> &[RulesetDiagnostic] {
        match self {
            Self::Diagnosed { diagnostics, .. } | Self::Rejected { diagnostics, .. } => diagnostics,
            _ => &[],
        }
    }

    /// `error.code` of an `AuthorityError`.
    pub fn code(&self) -> Option<&str> {
        match self {
            Self::Rejected { code, .. } => Some(code),
            _ => None,
        }
    }

    /// The fields of a `MechanicsRejected`.
    pub fn mechanics(&self) -> Option<&MechanicsDetails> {
        match self {
            Self::Rejected { mechanics: Some(details), .. } => Some(details),
            _ => None,
        }
    }

    /// `type(error).__name__` of the exception Python raised.
    pub fn python_class(&self) -> &'static str {
        match self {
            Self::Value(_) => "ValueError",
            Self::Type(_) => "TypeError",
            Self::Diagnosed { error_type, .. }
            | Self::Rejected { error_type, .. }
            | Self::Uncaught { error_type, .. } => error_type,
        }
    }

    /// Whether Python would have raised a `ValueError` (or a subclass). An `AuthorityError`
    /// is a `RuntimeError`, not a `ValueError`.
    pub fn is_value_error(&self) -> bool {
        matches!(self, Self::Value(_) | Self::Diagnosed { .. })
    }

    /// The owner reply for this error if it reaches the owner uncaught.
    ///
    /// The Python owner catches `ValueError` and `AuthorityError`, and replies with
    /// `getattr(error, "code", "command_rejected")`, `str(error)` and the diagnostics payloads;
    /// `error_type` is `type(error).__name__`. It does not catch `TypeError` or the
    /// [`DomainError::Uncaught`] classes: those become its `internal_error` reply, which the owner lane writes, so this returns `None`.
    pub fn rejection(&self) -> Option<CodedError> {
        if let Self::Type(_) | Self::Uncaught { .. } = self {
            return None;
        }
        let code = self.code().unwrap_or("command_rejected");
        Some(
            CodedError::new(code, self.message())
                .with_diagnostics(self.diagnostics().iter().map(|d| Json::Object(d.as_payload())).collect())
                .with_error_type(self.python_class()),
        )
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
