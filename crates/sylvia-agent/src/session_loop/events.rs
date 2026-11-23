//! `ProviderEvent` and its return channel, from `agent/_session_loop.py`.
//!
//! Python's event is one frozen dataclass whose optional fields each kind fills through its
//! class-method constructor. Here each kind is a variant holding exactly what its constructor
//! sets, so the session loop's `action_command is None` branch cannot arise.

use std::sync::{Arc, Mutex};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};
use sylvia_domain::authority::ActionCommand;
use sylvia_domain::error::MechanicsDetails;

/// `ProviderEventKind`.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum ProviderEventKind {
    PrivateReasoning,
    PrivateContinuation,
    SafeThink,
    Talk,
    Action,
    Evidence,
    ProseComplete,
}

impl ProviderEventKind {
    /// The enum's value.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::PrivateReasoning => "private_reasoning",
            Self::PrivateContinuation => "private_continuation",
            Self::SafeThink => "safe_think",
            Self::Talk => "talk",
            Self::Action => "action",
            Self::Evidence => "evidence",
            Self::ProseComplete => "prose_complete",
        }
    }
}

/// `ProviderActionFeedback`: the mutable return channel for a privacy-safe rejected provider
/// action. The session loop writes `rejection` while the turn waits for its next event; the
/// turn reads it once resumed. Clones share the one channel, as Python's object is shared.
#[derive(Debug, Clone, Default)]
pub struct ProviderActionFeedback(Arc<Mutex<Option<JsonMap>>>);

impl ProviderActionFeedback {
    /// `ProviderActionFeedback()`: no rejection yet.
    pub fn new() -> Self {
        Self::default()
    }

    /// `rejection`.
    pub fn rejection(&self) -> Option<JsonMap> {
        self.0.lock().unwrap_or_else(std::sync::PoisonError::into_inner).clone()
    }

    /// `rejection = payload`.
    pub fn set_rejection(&self, rejection: Option<JsonMap>) {
        *self.0.lock().unwrap_or_else(std::sync::PoisonError::into_inner) = rejection;
    }
}

/// `_mechanics_rejection_feedback(error)`: the exact bounded correction facts a GM needs after
/// mechanics reject. `code` and `reason` are the error's `code` and `str(error)`.
pub fn mechanics_rejection_feedback(code: &str, reason: &str, details: &MechanicsDetails) -> JsonMap {
    let mut validation = JsonMap::new();
    validation.insert(
        "field_paths".into(),
        Json::Array(details.field_paths.iter().map(|path| Json::from(path.as_str())).collect()),
    );
    if !details.validation_issues.is_empty() {
        validation.insert(
            "issues".into(),
            Json::Array(
                details.validation_issues.iter().map(|issue| Json::Object(issue.as_payload())).collect(),
            ),
        );
    }
    let correction_source = if details.validation_issues.is_empty() {
        "the refusal reasons and validation.field_paths"
    } else {
        "validation.issues"
    };
    let mut feedback = JsonMap::new();
    feedback.insert("status".into(), "rejected".into());
    feedback.insert("code".into(), code.into());
    feedback.insert("reason".into(), reason.into());
    if details.refusal_reasons.len() > 1 {
        feedback.insert(
            "reasons".into(),
            Json::Array(details.refusal_reasons.iter().map(|reason| Json::from(reason.as_str())).collect()),
        );
    }
    feedback.insert("validation".into(), Json::Object(validation));
    feedback.insert("state_changed".into(), Json::Bool(details.state_changed));
    feedback.insert(
        "instruction".into(),
        format!(
            "Use {correction_source} and the current tool schemas to correct every declared field. If this \
             operation cannot represent the intended action, choose a different currently exposed operation that \
             can. Do not repeat an identical rejected call."
        )
        .into(),
    );
    feedback
}

/// `ProviderEvent`.
#[derive(Debug, Clone)]
pub enum ProviderEvent {
    /// `private_reasoning(text, response_id=None)`.
    PrivateReasoning { text: String, response_id: Option<String> },
    /// `private_continuation(payload, response_id)`: native protocol data as JSON text, kept out
    /// of public tool evidence.
    PrivateContinuation { text: String, response_id: String },
    /// `safe_think(text)`.
    SafeThink { text: String },
    /// `talk(text, response_blocks=None, response_id=None)`.
    Talk { text: String, response_blocks: Option<Vec<JsonMap>>, response_id: Option<String> },
    /// `action(command, feedback=None)`.
    Action { command: ActionCommand, feedback: Option<ProviderActionFeedback> },
    /// `tool_evidence(message_id, payload)`.
    Evidence { message_id: String, payload: JsonMap },
    /// `prose_complete()`.
    ProseComplete,
}

impl ProviderEvent {
    /// `private_continuation(payload, response_id)`: `json.dumps(payload, ensure_ascii=False,
    /// allow_nan=False)`; a JSON map cannot hold a non-finite number.
    pub fn private_continuation(payload: &JsonMap, response_id: &str) -> CodedResult<Self> {
        if response_id.is_empty() {
            return Err(CodedError::rejected("Native continuation requires a response identity")
                .with_error_type("ValueError"));
        }
        Ok(Self::PrivateContinuation {
            text: pyjson::dumps(&Json::Object(payload.clone()), Dump::DEFAULT.ascii(false)),
            response_id: response_id.to_owned(),
        })
    }

    /// `kind`.
    pub fn kind(&self) -> ProviderEventKind {
        match self {
            Self::PrivateReasoning { .. } => ProviderEventKind::PrivateReasoning,
            Self::PrivateContinuation { .. } => ProviderEventKind::PrivateContinuation,
            Self::SafeThink { .. } => ProviderEventKind::SafeThink,
            Self::Talk { .. } => ProviderEventKind::Talk,
            Self::Action { .. } => ProviderEventKind::Action,
            Self::Evidence { .. } => ProviderEventKind::Evidence,
            Self::ProseComplete => ProviderEventKind::ProseComplete,
        }
    }

    /// `text`.
    pub fn text(&self) -> Option<&str> {
        match self {
            Self::PrivateReasoning { text, .. }
            | Self::PrivateContinuation { text, .. }
            | Self::SafeThink { text }
            | Self::Talk { text, .. } => Some(text),
            _ => None,
        }
    }

    /// `response_id`.
    pub fn response_id(&self) -> Option<&str> {
        match self {
            Self::PrivateReasoning { response_id, .. } | Self::Talk { response_id, .. } => {
                response_id.as_deref()
            }
            Self::PrivateContinuation { response_id, .. } => Some(response_id),
            _ => None,
        }
    }
}
