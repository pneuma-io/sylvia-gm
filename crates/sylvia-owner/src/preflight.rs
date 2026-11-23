//! `owner_process._preflight_guided_start`: prove the mandatory opening request fits the
//! selected model before a guided handoff commits.
//!
//! Lane 3's `finalize_campaign_construction` handler calls [`preflight_guided_start`] where
//! Python's calls `_preflight_guided_start`. The opening is assembled as the GM's first request
//! would be (P3's `ProviderSessionAdapter::messages` and the tool projection) for an unsaved
//! preflight session, and measured by the context-fitting wrapper without being sent.
//!
//! The context ledger is not ported yet: on a route whose request assembly is the ledger
//! (`claude_code`, `codex_app_server`, `anthropic_api`, or `SYLVIA_GM_CONTEXT_MODE=ledger`)
//! the preflight answers `rust_port_pending`.

use crate::dispatch::Failure;
use crate::owner_reads::OwnerReads;
use crate::services::ProviderResolver;
use futures_util::future::BoxFuture;
use serde_json::{json, Value as Json};
use std::sync::Arc;
use sylvia_agent::campaign_memory::{KnowledgeReference, RankReferences};
use sylvia_agent::provider_session::context::{ProviderSessionAdapter, SessionProviderInput, LEDGER_MODE};
use sylvia_agent::provider_session::reads::OpeningReads;
use sylvia_agent::provider_session::tools::provider_tools;
use sylvia_agent::session_loop::ProviderControl;
use sylvia_agent::stateless_mcp::ModelMechanicsTool;
use sylvia_app::knowledge_search;
use sylvia_base::hash::sha256_hex;
use sylvia_base::{CodedError, CodedResult, JsonMap};
use sylvia_domain::authority::{CampaignIndexEntry, WriterRole};
use sylvia_domain::session::{
    SessionContext, SessionMessage, SessionMessageKind, SessionRecord, SessionRecordFields, SessionStatus,
};
use sylvia_provider::admin_audit::{AdminAuditedProvider, AuditSink};
use sylvia_provider::config::env::Environment;
use sylvia_provider::context_fit::context_fitted_provider;
use sylvia_provider::reasoning::bind_session_reasoning;
use sylvia_provider::Provider;

/// The owner and bus reads the preflight adds to the request assembly's.
pub trait PreflightReads: OpeningReads {
    /// `campaign_index()`.
    fn campaign_index(&self) -> BoxFuture<'_, CodedResult<Vec<CampaignIndexEntry>>>;

    /// `admin_full_audit_enabled()`.
    fn admin_full_audit_enabled(&self) -> BoxFuture<'_, CodedResult<bool>>;

    /// `record_admin_full_audit`, the owner's synchronous in-memory sink.
    fn admin_full_audit_sink(&self) -> AuditSink;

    /// `campaign_knowledge_catalog_for_plan(campaign_id, structured_plan)`.
    fn campaign_knowledge_catalog_for_plan<'a>(
        &'a self,
        campaign_id: &'a str,
        plan: &'a JsonMap,
    ) -> BoxFuture<'a, CodedResult<JsonMap>>;

    /// `bus.catalog(campaign_id)`, as the model tools it is cast to.
    fn catalog<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<Vec<ModelMechanicsTool>>>;
}

/// `_preflight_guided_start`'s keyword arguments.
pub struct GuidedStartPreflight<'a> {
    pub owner_id: &'a str,
    pub campaign_id: &'a str,
    pub provider_id: &'a str,
    pub model_id: &'a str,
    pub plan_text: &'a str,
    pub structured_plan: Option<&'a JsonMap>,
    pub gm_system_prompt: Option<&'a str>,
    pub reasoning_effort: Option<&'a str>,
}

/// Python's bare `next(...)` on an exhausted generator.
fn stop_iteration() -> CodedError {
    CodedError::new("internal_error", "").with_error_type("StopIteration")
}

fn rejected(message: &str) -> CodedError {
    CodedError::rejected(message).with_error_type("CommandRejected")
}

/// A domain check's failure as the owner treats it: a `ValueError` is a rejection, any other
/// exception an internal fault.
fn domain(error: sylvia_domain::error::DomainError) -> CodedError {
    let class = error.python_class();
    let code = if class == "ValueError" { "command_rejected" } else { "internal_error" };
    CodedError::new(code, error.message()).with_error_type(class)
}

/// `_preflight_guided_start(...)`: assemble and measure the mandatory opening request.
///
/// `rank` is [`rank_references`] (lane 3's ranker) and `environment` the process environment
/// the context mode is read from.
pub async fn preflight_guided_start(
    reads: Arc<dyn PreflightReads>,
    resolver: &dyn ProviderResolver,
    request: GuidedStartPreflight<'_>,
    rank: &RankReferences,
    environment: &Environment,
) -> Result<(), Failure> {
    let campaign_id = request.campaign_id;
    let index = reads.campaign_index().await?;
    let campaign = index.iter().find(|entry| entry.campaign_id == campaign_id).ok_or_else(stop_iteration)?;
    let builder_session =
        campaign.sessions.iter().find(|session| session.role == "builder").ok_or_else(stop_iteration)?;
    let builder_context = reads.session_context(&builder_session.session_id).await?;
    let interviewed = builder_context.messages.iter().any(|message| {
        message.kind() == SessionMessageKind::Evidence
            && message.content().get("kind").and_then(Json::as_str) == Some("campaign_interview_state")
    });
    if !interviewed {
        return Ok(());
    }
    let ready = builder_context
        .messages
        .iter()
        .rev()
        .find(|message| {
            message.kind() == SessionMessageKind::User
                && message.content().get("interview_step").and_then(Json::as_str) == Some("ready")
                && message.content().get("text").and_then(Json::as_str) == Some("Ready to Begin")
        })
        .ok_or_else(|| rejected("Ready to Begin input is unavailable"))?;
    let session_id = format!("session-preflight-{}", &sha256_hex(campaign_id.as_bytes())[..24]);
    let preflight_session = SessionRecord::new(SessionRecordFields {
        session_id: session_id.clone(),
        campaign_id: Some(campaign_id.to_owned()),
        role: WriterRole::Dm,
        provider_id: request.provider_id.to_owned(),
        model_id: request.model_id.to_owned(),
        status: SessionStatus::Active,
        reasoning_effort: request.reasoning_effort.map(str::to_owned),
        ..SessionRecordFields::default()
    })
    .map_err(domain)?;
    let message = |sequence: i64, message_id: &str, content: Json| {
        SessionMessage::new(&session_id, sequence, message_id, SessionMessageKind::Evidence, &content, None)
            .map_err(domain)
    };
    let history = SessionContext {
        session: preflight_session.clone(),
        messages: vec![
            message(
                1,
                "preflight-ready-reference",
                json!({
                    "kind": "ready_to_begin_reference",
                    "source_session_id": builder_session.session_id,
                    "source_message_id": ready.message_id(),
                }),
            )?,
            message(
                2,
                "preflight-campaign-start",
                json!({"kind": "campaign_start", "event": "campaign_start"}),
            )?,
        ],
    };
    let mut base: Arc<dyn Provider> = resolver.resolve(&preflight_session).await?;
    if reads.admin_full_audit_enabled().await? {
        base = Arc::new(AdminAuditedProvider::new(
            base,
            reads.admin_full_audit_sink(),
            session_id.clone(),
            WriterRole::Dm.as_str(),
            request.provider_id,
            request.model_id,
            "start_preflight",
        ));
    }
    let bound = bind_session_reasoning(
        base,
        WriterRole::Dm.as_str(),
        preflight_session.fields().reasoning_effort.as_deref(),
        preflight_session.fields().reasoning_effort_once.as_deref(),
        None,
    )?;
    let preflight_provider = Arc::new(context_fitted_provider(Arc::new(bound), true));
    let knowledge = match request.structured_plan {
        Some(plan) => Some(reads.campaign_knowledge_catalog_for_plan(campaign_id, plan).await?),
        None => None,
    };
    let adapter = ProviderSessionAdapter::new(
        Arc::clone(&reads) as Arc<dyn OpeningReads>,
        Arc::clone(&preflight_provider) as Arc<dyn Provider>,
        request.owner_id,
        request.gm_system_prompt.map(str::to_owned),
        120.0,
        Some(request.plan_text.to_owned()),
        request.structured_plan.cloned(),
        knowledge,
        environment,
    )?;
    if adapter.context_mode() == LEDGER_MODE {
        return Err(Failure::Rejected(CodedError::new(
            sylvia_provider::factory::PORT_PENDING,
            "The Rust owner cannot assemble a context-ledger request yet (agent/_context_ledger.py).",
        )));
    }
    let catalog = reads.catalog(campaign_id).await?;
    let input = SessionProviderInput::new(
        preflight_session,
        history,
        catalog.clone(),
        Arc::new(ProviderControl::new()),
    );
    let messages: Vec<Json> = adapter
        .messages(&input, rank)
        .await?
        .into_iter()
        .map(|tagged| Json::Object(tagged.message))
        .collect();
    let (tools, _) = provider_tools(&catalog)?;
    // `max_tokens=getattr(preflight_provider.model_info, "max_output_tokens", 4096)`.
    let max_tokens = match preflight_provider.model_info() {
        Some(model) => model.max_output_tokens.map_or(Json::Null, Json::from),
        None => Json::from(4096),
    };
    let mut options = JsonMap::new();
    options.insert("max_tokens".into(), max_tokens);
    // A refusal here is Python's `TokenLimitError`, the owner's provider-error reply.
    preflight_provider.admit_request(&messages, Some(&tools), &options)?;
    Ok(())
}

/// The owner's reads, as the preflight takes them: lane 3's [`OwnerReads`] already answers
/// `OpeningReads`, and its preflight reads are plain methods, so this is delegation only.
impl PreflightReads for OwnerReads {
    fn campaign_index(&self) -> BoxFuture<'_, CodedResult<Vec<CampaignIndexEntry>>> {
        Box::pin(OwnerReads::campaign_index(self))
    }

    fn admin_full_audit_enabled(&self) -> BoxFuture<'_, CodedResult<bool>> {
        let enabled = OwnerReads::admin_full_audit_enabled(self);
        Box::pin(async move { Ok(enabled) })
    }

    /// The owner's `record_admin_full_audit`. Its only failure is a malformed payload, which
    /// the audit wrapper never builds, so a failure is dropped like an absent record.
    fn admin_full_audit_sink(&self) -> AuditSink {
        let reads = self.clone();
        Arc::new(move |payload| {
            let _ = reads.record_admin_full_audit(payload);
        })
    }

    fn campaign_knowledge_catalog_for_plan<'a>(
        &'a self,
        campaign_id: &'a str,
        plan: &'a JsonMap,
    ) -> BoxFuture<'a, CodedResult<JsonMap>> {
        Box::pin(OwnerReads::campaign_knowledge_catalog_for_plan(self, campaign_id, plan))
    }

    fn catalog<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<Vec<ModelMechanicsTool>>> {
        Box::pin(OwnerReads::catalog(self, campaign_id))
    }
}

/// `application/knowledge_search.rank_references` for the request assembly's memory selection:
/// lane 3's ranker over the same references. Its `KeyError` for a reference missing a field is
/// Python's internal fault.
pub fn rank_references(query: &str, references: &[KnowledgeReference]) -> CodedResult<Vec<JsonMap>> {
    let candidates: Vec<knowledge_search::KnowledgeReference> = references
        .iter()
        .map(|reference| knowledge_search::KnowledgeReference {
            result: reference.result.clone(),
            identity: reference.identity.clone(),
            body: reference.body.clone(),
        })
        .collect();
    knowledge_search::rank_references(query, &candidates).map_err(domain)
}
