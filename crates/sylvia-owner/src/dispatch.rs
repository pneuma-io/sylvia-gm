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
/// resolver, session loop, storage root) as they are ported; it mirrors Python's
/// `DispatchContext`.
#[derive(Debug, Clone)]
pub struct DispatchContext {
    /// `owner_id` from the launch envelope.
    pub owner_id: String,
    /// The folder holding the database: Python's `storage_root`.
    pub storage_root: std::path::PathBuf,
}

/// Answer one registry command.
pub async fn dispatch(context: &DispatchContext, method: &str, params: &JsonMap) -> CodedResult<Json> {
    let _ = (context, params);
    match commands::find(method) {
        // Python: `raise OwnerProtocolError(f'Unsupported owner method: {method}')`.
        None => Err(CodedError::rejected(format!("Unsupported owner method: {method}"))),
        Some(command) if command.status == Status::Pending => {
            Err(CodedError::new(PORT_PENDING, format!("The Rust owner does not answer {method} yet.")))
        }
        // A command marked Ported must have an arm above this one.
        Some(_) => {
            Err(CodedError::new("internal_error", format!("{method} is marked ported but has no handler.")))
        }
    }
}
