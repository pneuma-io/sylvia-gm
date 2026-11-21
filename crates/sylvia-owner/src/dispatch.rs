//! Command dispatch: where a ported command gets its handler.
//!
//! Python's `_dispatch_selected` looks the method up in `COMMAND_REGISTRY` and calls the handler
//! with a `DispatchContext`. The Rust owner does the same through [`dispatch`]. Until a command
//! is ported it answers with the code `rust_port_pending`, which no Python owner ever returns,
//! so a comparison run shows exactly which commands are still missing.

use crate::commands::{self, Status};
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};

/// The code a known but unported command answers with.
pub const PORT_PENDING: &str = "rust_port_pending";

/// What handlers get. Lanes add the owner's shared services here (store handle, provider
/// resolver, session loop) as they are ported; it mirrors Python's `DispatchContext`.
#[derive(Debug, Clone)]
pub struct DispatchContext {
    /// `owner_id` from the launch envelope.
    pub owner_id: String,
    /// The folder holding the database: Python's `storage_root`.
    pub storage_root: std::path::PathBuf,
    /// The launch handshake was verified (the supervisor's `handshake_verified`). The run loop
    /// only starts after it is, so this is true whenever a command runs.
    pub handshake_verified: bool,
}

/// Why a command failed, as the run loop needs it.
#[derive(Debug, Clone, PartialEq)]
pub enum Failure {
    /// Python's `AuthorityError`, `ConfigError`, `OwnerProtocolError` or `ValueError` branch:
    /// the reply carries the error's code and message. Local diagnostics records the error's
    /// `error_type`, or `CommandRejected` when it names none.
    Rejected(CodedError),
    /// Any other exception: the reply is the fixed `internal_error` text.
    Internal {
        /// The exception class name Python would record.
        error_type: &'static str,
    },
}

impl Failure {
    /// `OwnerProtocolError(message)`: code `command_rejected`.
    pub fn protocol(message: impl Into<String>) -> Self {
        Self::Rejected(CodedError::rejected(message).with_error_type("OwnerProtocolError"))
    }
}

impl From<CodedError> for Failure {
    /// A handler's coded error. The code `internal_error` marks an internal fault.
    fn from(error: CodedError) -> Self {
        if error.code == "internal_error" {
            Self::Internal { error_type: error.error_type.unwrap_or("RuntimeError") }
        } else {
            Self::Rejected(error)
        }
    }
}

/// The outcome of one command.
pub type DispatchResult = Result<Json, Failure>;

/// `_required_text(payload, key)`.
pub fn required_text(params: &JsonMap, key: &str) -> Result<String, Failure> {
    match params.get(key) {
        Some(Json::String(value)) if !value.is_empty() => Ok(value.clone()),
        _ => Err(Failure::protocol(format!("{key} must be a nonempty string"))),
    }
}

/// Answer one registry command.
pub async fn dispatch(context: &DispatchContext, method: &str, params: &JsonMap) -> DispatchResult {
    let unsupported = || Failure::protocol(format!("Unsupported owner method: {method}"));
    let Some(command) = commands::find(method) else { return Err(unsupported()) };
    // Run-loop entries have no handler; one that reaches dispatch (`renderer`) is unsupported.
    if command.topic.is_empty() {
        return Err(unsupported());
    }
    if command.status == Status::Pending {
        return Err(
            CodedError::new(PORT_PENDING, format!("The Rust owner does not answer {method} yet.")).into()
        );
    }
    let handled: CodedResult<Json> = match method {
        "handshake_status" => commands::campaigns::handle_handshake_status(context, params).await,
        // A command marked Ported must have an arm above this one.
        _ => Err(CodedError::new("internal_error", format!("{method} is marked ported but has no handler."))),
    };
    handled.map_err(Failure::from)
}
