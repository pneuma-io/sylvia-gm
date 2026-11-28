//! Command dispatch: where a ported command gets its handler.
//!
//! Python's `_dispatch_selected` looks the method up in `COMMAND_REGISTRY` and calls the handler
//! with a `DispatchContext`. The Rust owner does the same through [`dispatch`]. Until a command
//! is ported it answers with the code `rust_port_pending`, which no Python owner ever returns,
//! so a comparison run shows exactly which commands are still missing.

use crate::commands::{self, Status};
use crate::services::{ProviderResolver, SessionLoop};
use std::path::PathBuf;
use std::sync::Arc;
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};
use sylvia_provider::error::ProviderError;
use sylvia_store::{StoreError, StoreHandle};

/// The code a known but unported command answers with.
pub const PORT_PENDING: &str = "rust_port_pending";

/// What handlers get: Python's `DispatchContext`, one handle per service.
///
/// Cloned per request with `method` set. A field is `None` until the lane that owns the service
/// lands it: the provider resolver and session loop (lane 4).
#[derive(Clone)]
pub struct DispatchContext {
    /// `owner_id` from the launch envelope.
    pub owner_id: String,
    /// The folder holding the database: Python's `storage_root`.
    pub storage_root: PathBuf,
    /// The launch handshake was verified (the supervisor's `handshake_verified`). The run loop
    /// only starts after it is, so this is true whenever a command runs.
    pub handshake_verified: bool,
    /// The command being answered: `ctx.method`.
    pub method: String,
    /// `gm_system_prompt`: a replacement GM system prompt, `None` in the product.
    pub gm_system_prompt: Option<Arc<str>>,
    /// The application owner's store (lane 3): Python's `application_owner` and the bus reads
    /// that go through it.
    pub store: StoreHandle,
    /// `provider_resolver`, `model_choices` and `reload_provider_resolver` (lane 4).
    pub providers: Option<Arc<dyn ProviderResolver>>,
    /// `session_loop` (lane 4).
    pub session_loop: Option<Arc<dyn SessionLoop>>,
    /// `session_loop` as the commands use it: lane 4's `CampaignSessionLoop`, built at launch
    /// over the GM turn's owner reads. `None` only in a context a unit test builds by hand.
    pub campaign_loop: Option<Arc<sylvia_agent::session_loop::campaign::CampaignSessionLoop>>,
    /// The bundled package sources (the shipped Ruleset, Setting, Plan and archives), built once at
    /// launch from [`crate::util::source_root`].
    pub bundled: Arc<sylvia_app::bundled_packages::BundledPackages>,
    /// `record_provider_warning(code)`: a `provider_warning` record in local diagnostics.
    pub record_provider_warning: Arc<dyn Fn(&str) + Send + Sync>,
}

impl std::fmt::Debug for DispatchContext {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("DispatchContext")
            .field("owner_id", &self.owner_id)
            .field("storage_root", &self.storage_root)
            .field("method", &self.method)
            .field("providers", &self.providers.is_some())
            .field("session_loop", &self.session_loop.is_some())
            .field("campaign_loop", &self.campaign_loop.is_some())
            .finish_non_exhaustive()
    }
}

/// Why a command failed, as the run loop needs it.
#[derive(Debug, Clone, PartialEq)]
pub enum Failure {
    /// Python's `AuthorityError`, `ConfigError`, `OwnerProtocolError` or `ValueError` branch:
    /// the reply carries the error's code and message. Local diagnostics records the error's
    /// `error_type`, or `CommandRejected` when it names none.
    Rejected(CodedError),
    /// Python's `ProviderError` branch: the reply's code and player-facing sentence come from
    /// `sylvia_provider::reply::provider_error_reply`, and diagnostics records the class.
    Provider(ProviderError),
    /// Any other exception: the reply is the fixed `internal_error` text.
    Internal {
        /// The exception class name Python would record.
        error_type: &'static str,
    },
}

impl From<ProviderError> for Failure {
    fn from(error: ProviderError) -> Self {
        Self::Provider(error)
    }
}

impl From<StoreError> for Failure {
    /// A store failure as Python's owner sees it: a coded error is a rejection; a SQLite or
    /// other exception is an internal fault (Python's `except Exception`); a store path not
    /// ported yet is an internal fault too, never a Python outcome.
    fn from(error: StoreError) -> Self {
        // Lane 3's `rejection()` is the one place that decides which store failures Python's
        // owner catches.
        match error.rejection() {
            Some(error) => Failure::from(error),
            None => Self::Internal { error_type: "RuntimeError" },
        }
    }
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
    // Its preflight's provider errors keep Python's provider-error reply, so it answers a
    // `Failure` itself.
    if method == "finalize_campaign_construction" {
        return commands::campaigns::handle_finalize_campaign_construction(context, params).await;
    }
    // A submitted turn's provider errors keep Python's provider-error reply too.
    if method == "submit_session_input" {
        return commands::sessions::handle_submit_session_input(context, params).await;
    }
    if method == "start_dm_session" {
        return commands::sessions::handle_start_dm_session(context, params).await;
    }
    if method == "manual_retry_session" {
        return commands::sessions::handle_manual_retry_session(context, params).await;
    }
    let handled: CodedResult<Json> = match method {
        "handshake_status" => commands::campaigns::handle_handshake_status(context, params).await,
        "campaign_index" => commands::campaigns::handle_campaign_index(context, params).await,
        "player_projection_page" => commands::campaigns::handle_player_projection_page(context, params).await,
        "catalog" => commands::campaigns::handle_catalog(context, params).await,
        "backup_database" => commands::campaigns::handle_backup_database(context, params).await,
        "campaign_creation_catalog" => {
            commands::campaigns::handle_campaign_creation_catalog(context, params).await
        }
        "campaign_party_options" => commands::campaigns::handle_campaign_party_options(context, params).await,
        "advance_guided_campaign_draft" => {
            commands::campaigns::handle_advance_guided_campaign_draft(context, params).await
        }
        "start_guided_campaign" => commands::campaigns::handle_start_guided_campaign(context, params).await,
        "create_guided_campaign_draft" => {
            commands::campaigns::handle_create_guided_campaign_draft(context, params).await
        }
        "rename_campaign" => commands::campaigns::handle_rename_campaign(context, params).await,
        "archive_campaign" => commands::campaigns::handle_archive_campaign(context, params).await,
        "restore_archived_campaign" => {
            commands::campaigns::handle_restore_archived_campaign(context, params).await
        }
        "trash_campaign" => commands::campaigns::handle_trash_campaign(context, params).await,
        "purge_campaign" => commands::campaigns::handle_purge_campaign(context, params).await,
        "export_campaign" | "export_campaign_file" => {
            commands::campaigns::handle_export_campaign(context, params).await
        }
        "install_package" => commands::packages::handle_install_package(context, params).await,
        "install_package_file" => commands::packages::handle_install_package_file(context, params).await,
        "export_package_candidate" | "export_package_candidate_file" => {
            commands::packages::handle_export_package_candidate(context, params).await
        }
        "recover_campaign" => commands::campaigns::handle_recover_campaign(context, params).await,
        "recover_campaign_file" => commands::campaigns::handle_recover_campaign_file(context, params).await,
        "restore_trashed_campaign" => {
            commands::campaigns::handle_restore_trashed_campaign(context, params).await
        }
        "builder_chat_index" => commands::builder::handle_builder_chat_index(context, params).await,
        "create_builder_chat" => commands::builder::handle_create_builder_chat(context, params).await,
        "resume_agent_session" => commands::sessions::handle_resume_agent_session(context, params).await,
        "switch_session_reasoning" => {
            commands::sessions::handle_switch_session_reasoning(context, params).await
        }
        "switch_session_speed" => commands::sessions::handle_switch_session_speed(context, params).await,
        "steer_session" => commands::sessions::handle_steer_session(context, params).await,
        "switch_session_model" => commands::sessions::handle_switch_session_model(context, params).await,
        "stop_agent_session" => commands::sessions::handle_stop_agent_session(context, params).await,
        "interrupt_stream" => commands::sessions::handle_interrupt_stream(context, params).await,
        "disconnect_stream" => commands::sessions::handle_disconnect_stream(context, params).await,
        "stream_heartbeat" => commands::sessions::handle_stream_heartbeat(context, params).await,
        "narration_attempt_status" => {
            commands::sessions::handle_narration_attempt_status(context, params).await
        }
        "reconnect_stream" => commands::sessions::handle_reconnect_stream(context, params).await,
        "session_context" | "session_history_page" => {
            commands::sessions::handle_session_context(context, params).await
        }
        "rename_builder_chat" => commands::builder::handle_rename_builder_chat(context, params).await,
        "delete_builder_chat" => commands::builder::handle_delete_builder_chat(context, params).await,
        "model_choices" => commands::providers::handle_model_choices(context, params).await,
        "provider_settings" => commands::providers::handle_provider_settings(context, params).await,
        "detect_local_providers" => commands::providers::handle_detect_local_providers(context, params).await,
        "discover_provider_models" => {
            commands::providers::handle_discover_provider_models(context, params).await
        }
        "validate_provider_model" => {
            commands::providers::handle_validate_provider_model(context, params).await
        }
        "save_provider_settings" => commands::providers::handle_save_provider_settings(context, params).await,
        "open_provider_setup" => commands::providers::handle_open_provider_setup(context, params).await,
        "admin_full_audit_status" => {
            commands::providers::handle_admin_full_audit_status(context, params).await
        }
        "admin_full_audit_record" => {
            commands::providers::handle_admin_full_audit_record(context, params).await
        }
        "export_admin_full_audit" => {
            commands::providers::handle_export_admin_full_audit(context, params).await
        }
        "set_admin_full_audit" => commands::providers::handle_set_admin_full_audit(context, params).await,
        "claude_connection" => commands::providers::handle_claude_connection(context, params).await,
        "chatgpt_connection" => commands::providers::handle_chatgpt_connection(context, params).await,
        "remove_provider_settings" => {
            commands::providers::handle_remove_provider_settings(context, params).await
        }
        "set_default_model" => commands::providers::handle_set_default_model(context, params).await,
        "set_chat_defaults" => commands::providers::handle_set_chat_defaults(context, params).await,
        // A command marked Ported must have an arm above this one.
        _ => Err(CodedError::new("internal_error", format!("{method} is marked ported but has no handler."))),
    };
    handled.map_err(Failure::from)
}
