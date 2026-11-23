//! `agent/_session_loop.py`, the campaign session loop, ported in parts.
//!
//! - [`events`] holds `ProviderEvent`, `ProviderEventKind`, `ProviderActionFeedback` and
//!   `_mechanics_rejection_feedback`.
//! - [`control`] holds `ProviderControl`.
//! - [`provider`] holds `SessionProvider`, the provider turn the loop runs, with its event sink
//!   and error.
//! - This module holds `SessionInputKind`, `SessionRunRequest`, `SessionProviderInput` and the
//!   run results.

pub mod control;
pub mod events;
pub mod provider;

use crate::stateless_mcp::ModelMechanicsTool;
pub use control::ProviderControl;
pub use events::{mechanics_rejection_feedback, ProviderActionFeedback, ProviderEvent, ProviderEventKind};
pub use provider::{SessionEvents, SessionFailure, SessionProvider, SessionProviderError};
use std::sync::Arc;
use sylvia_base::{CodedResult, JsonMap};
use sylvia_domain::authority::{
    ActionReceipt, CanonicalEvent, MechanicsExecutionReceipt, NarrationInterruption,
};
use sylvia_domain::session::{SessionContext, SessionRecord};
use sylvia_provider::BoxFuture;

/// `SessionInputKind`.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub enum SessionInputKind {
    #[default]
    User,
    CampaignStart,
    BuilderStart,
    PlayerActionResponse,
}

impl SessionInputKind {
    /// The enum's value.
    pub fn as_str(self) -> &'static str {
        match self {
            Self::User => "user",
            Self::CampaignStart => "campaign_start",
            Self::BuilderStart => "builder_start",
            Self::PlayerActionResponse => "player_action_response",
        }
    }

    /// `SessionInputKind(value)`, without the exception.
    pub fn parse(value: &str) -> Option<Self> {
        Some(match value {
            "user" => Self::User,
            "campaign_start" => Self::CampaignStart,
            "builder_start" => Self::BuilderStart,
            "player_action_response" => Self::PlayerActionResponse,
            _ => return None,
        })
    }
}

/// `SessionRunRequest`, with the dataclass's defaults from [`SessionRunRequest::new`].
#[derive(Debug, Clone, PartialEq)]
pub struct SessionRunRequest {
    pub session_id: String,
    pub turn_id: String,
    pub attempt_id: String,
    pub message_id: String,
    pub user_text: String,
    pub ooc_text: Option<String>,
    pub input_kind: SessionInputKind,
    pub player_action_id: Option<String>,
    pub automatic_response: bool,
    pub redirect_interruption_id: Option<String>,
    pub campaign_ending_choice: Option<JsonMap>,
    pub builder_question_answers: Option<Vec<JsonMap>>,
}

impl SessionRunRequest {
    /// The required fields; every other field takes its default.
    pub fn new(
        session_id: impl Into<String>,
        turn_id: impl Into<String>,
        attempt_id: impl Into<String>,
        message_id: impl Into<String>,
        user_text: impl Into<String>,
    ) -> Self {
        Self {
            session_id: session_id.into(),
            turn_id: turn_id.into(),
            attempt_id: attempt_id.into(),
            message_id: message_id.into(),
            user_text: user_text.into(),
            ooc_text: None,
            input_kind: SessionInputKind::User,
            player_action_id: None,
            automatic_response: false,
            redirect_interruption_id: None,
            campaign_ending_choice: None,
            builder_question_answers: None,
        }
    }
}

/// `catalog_supplier(catalog_request_id)`: the current MCP surface.
pub type CatalogSupplier =
    Arc<dyn Fn(String) -> BoxFuture<'static, CodedResult<Vec<ModelMechanicsTool>>> + Send + Sync>;
/// `knowledge_searcher(query, sources, limit, offset)`.
pub type KnowledgeSearcher = Arc<
    dyn Fn(String, Option<Vec<String>>, i64, i64) -> BoxFuture<'static, CodedResult<JsonMap>> + Send + Sync,
>;
/// `reference_reader(source, path)`.
pub type ReferenceReader =
    Arc<dyn Fn(String, String) -> BoxFuture<'static, CodedResult<JsonMap>> + Send + Sync>;
/// `narrative_writer(parameters)`. A refusal keeps its class and a `MechanicsRejected`'s fields,
/// which the Campaign File rejection reports.
pub type NarrativeWriter = Arc<
    dyn Fn(JsonMap) -> BoxFuture<'static, Result<JsonMap, crate::stateless_mcp::AuthorityFailure>>
        + Send
        + Sync,
>;

/// `SessionProviderInput`: what one provider turn is run from. Build with
/// [`SessionProviderInput::new`]; the optional parts take the dataclass's defaults.
#[derive(Clone)]
pub struct SessionProviderInput {
    pub session: SessionRecord,
    pub history: SessionContext,
    pub catalog: Vec<ModelMechanicsTool>,
    pub control: Arc<ProviderControl>,
    pub catalog_supplier: Option<CatalogSupplier>,
    pub knowledge_searcher: Option<KnowledgeSearcher>,
    pub reference_reader: Option<ReferenceReader>,
    pub narrative_writer: Option<NarrativeWriter>,
    pub basis_message_id: Option<String>,
    pub recovery_kind: Option<String>,
    pub retry_action_receipt: Option<ActionReceipt>,
}

impl std::fmt::Debug for SessionProviderInput {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("SessionProviderInput")
            .field("session", &self.session)
            .field("catalog", &self.catalog.len())
            .field("catalog_supplier", &self.catalog_supplier.is_some())
            .field("knowledge_searcher", &self.knowledge_searcher.is_some())
            .field("reference_reader", &self.reference_reader.is_some())
            .field("narrative_writer", &self.narrative_writer.is_some())
            .field("basis_message_id", &self.basis_message_id)
            .field("recovery_kind", &self.recovery_kind)
            .field("retry_action_receipt", &self.retry_action_receipt)
            .finish_non_exhaustive()
    }
}

impl SessionProviderInput {
    /// `SessionProviderInput(session, history, catalog, control)`.
    pub fn new(
        session: SessionRecord,
        history: SessionContext,
        catalog: Vec<ModelMechanicsTool>,
        control: Arc<ProviderControl>,
    ) -> Self {
        Self {
            session,
            history,
            catalog,
            control,
            catalog_supplier: None,
            knowledge_searcher: None,
            reference_reader: None,
            narrative_writer: None,
            basis_message_id: None,
            recovery_kind: None,
            retry_action_receipt: None,
        }
    }

    /// `current_catalog(catalog_request_id="static-fixture")`: the current MCP surface, else the
    /// static catalog adapter tests give.
    pub async fn current_catalog(&self, catalog_request_id: &str) -> CodedResult<Vec<ModelMechanicsTool>> {
        match &self.catalog_supplier {
            Some(supplier) => supplier(catalog_request_id.to_owned()).await,
            None => Ok(self.catalog.clone()),
        }
    }
}

/// `SessionRunResult`.
#[derive(Debug, Clone, PartialEq)]
pub struct SessionRunResult {
    pub canonical: Vec<CanonicalEvent>,
    pub receipts: Vec<MechanicsExecutionReceipt>,
    pub interruption: Option<NarrationInterruption>,
}

/// `SessionStopResult`.
#[derive(Debug, Clone, PartialEq)]
pub struct SessionStopResult {
    pub interruption: NarrationInterruption,
    pub cancellation_count: i64,
}
