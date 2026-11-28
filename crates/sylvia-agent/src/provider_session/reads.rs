//! The application-owner reads the GM request assembly makes (`ProviderSessionAdapter._owner`).
//!
//! Python's adapter holds an `AuthorityOwner` and calls these methods on it, some directly and
//! some through `getattr`; every one exists on the real owner, so they are plain methods here.
//! Lane 3 implements this trait over the store in `sylvia-app`. The shapes are Python's: the
//! arguments it passes and the values it reads back.

use crate::stateless_mcp::AuthorityFailure;
use std::sync::Arc;
use sylvia_base::{CodedResult, Json, JsonMap};
use sylvia_domain::authority::{
    ActionReceipt, CampaignConstructionBasis, CampaignDisplaySnapshot, CampaignSnapshot, CanonicalEvent,
    CanonicalReplay, ModelDecisionBoundary, NarrationInterruption, ProseChannel,
};
use sylvia_domain::package_library::PackageLockRecord;
use sylvia_domain::provider_records::ProviderPrivateRecord;
use sylvia_domain::session::{SessionContext, SessionMessage, SessionMessageKind, SessionRecord};
use sylvia_provider::BoxFuture;

/// The reads the request assembly makes, in Python's shapes.
pub trait OpeningReads: Send + Sync {
    /// `session_context(session_id)`.
    fn session_context<'a>(&'a self, session_id: &'a str) -> BoxFuture<'a, CodedResult<SessionContext>>;

    /// `model_context_owner(campaign_id)`: the GM context owner a package declares, if any.
    fn model_context_owner<'a>(&'a self, campaign_id: &'a str)
        -> BoxFuture<'a, CodedResult<Option<JsonMap>>>;

    /// `player_action_context(campaign_id)`.
    fn player_action_context<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<Vec<JsonMap>>>;

    /// `player_action_response_context(campaign_id, response)`.
    fn player_action_response_context<'a>(
        &'a self,
        campaign_id: &'a str,
        response: &'a JsonMap,
    ) -> BoxFuture<'a, CodedResult<Vec<JsonMap>>>;

    /// `action_sequence_response_results(session_id=..., basis_message_id=...)`.
    fn action_sequence_response_results<'a>(
        &'a self,
        session_id: &'a str,
        basis_message_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Vec<JsonMap>>>;

    /// `canonical_replay(campaign_id, acknowledged_sequence=...)`.
    fn canonical_replay<'a>(
        &'a self,
        campaign_id: &'a str,
        acknowledged_sequence: i64,
    ) -> BoxFuture<'a, CodedResult<CanonicalReplay>>;

    /// `ruleset_gm_operating_guide(campaign_id)`.
    fn ruleset_gm_operating_guide<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Option<String>>>;

    /// `model_context_snapshot(campaign_id)`.
    fn model_context_snapshot<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<(CampaignDisplaySnapshot, Option<ModelDecisionBoundary>)>>;

    /// `campaign_knowledge_catalog(session_id)`: read when the adapter was given no knowledge.
    fn campaign_knowledge_catalog<'a>(&'a self, session_id: &'a str) -> BoxFuture<'a, CodedResult<JsonMap>>;

    /// `package_text_file(package_id, version, sha256, path, maximum_bytes=...)`.
    fn package_text_file<'a>(
        &'a self,
        package_id: &'a str,
        version: &'a str,
        sha256: &'a str,
        path: &'a str,
        maximum_bytes: i64,
    ) -> BoxFuture<'a, CodedResult<String>>;

    /// `snapshot(campaign_id)`.
    fn snapshot<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<CampaignSnapshot>>;

    /// `campaign_construction_basis(campaign_id)`.
    fn campaign_construction_basis<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<CampaignConstructionBasis>>;
}

/// The owner calls a GM turn makes beyond the request assembly's reads (`TurnOwner`, agreed
/// with lane 3): each is the owner's whole operation, locks included. The turn-loop PRs add
/// their calls as they land; one not yet ported answers `rust_port_pending`.
pub trait TurnOwner: OpeningReads {
    /// `model_decision_boundary(campaign_id)`: the current actor or side decision scope.
    fn model_decision_boundary<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Option<ModelDecisionBoundary>>>;

    /// `campaign_preparation_boundary(campaign_id)`: the required starting choices, if any.
    fn campaign_preparation_boundary<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Option<ModelDecisionBoundary>>>;

    /// `current_package_lock(campaign_id)`.
    fn current_package_lock<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<PackageLockRecord>>;

    /// `record_admin_full_audit(payload)`: kept only while Admin Full Audit is on.
    fn record_admin_full_audit(&self, payload: JsonMap) -> CodedResult<()>;

    /// `record_mechanics_diagnostic(session_id=..., turn_id=..., action_id=..., tool_name=...,
    /// rejection_code=..., validation_field_paths=..., state_changed=...)`: bounded
    /// metadata-only mechanics evidence. `diagnostic` is the payload Python builds from those
    /// arguments, in its key order (`kind`, `privacy`, then the arguments).
    fn record_mechanics_diagnostic(&self, diagnostic: JsonMap) -> CodedResult<()>;

    /// `campaign_panels(campaign_id)`: the campaign's current panels and player controls.
    fn campaign_panels<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<JsonMap>>;

    /// `action_sequence(session_id=..., sequence_id=...)`: a saved action sequence, as read.
    fn action_sequence<'a>(
        &'a self,
        session_id: &'a str,
        sequence_id: &'a Json,
    ) -> BoxFuture<'a, CodedResult<Json>>;

    /// `_compose_action_sequence_from_agent(campaign_id=..., session_id=..., sequence_id=...,
    /// expected_revision=..., expected_version=..., steps=...)`.
    fn compose_action_sequence_from_agent<'a>(
        &'a self,
        campaign_id: &'a str,
        session_id: &'a str,
        arguments: &'a JsonMap,
    ) -> BoxFuture<'a, CodedResult<Json>>;

    /// `_execute_action_sequence_from_agent(campaign_id=..., session_id=..., sequence_id=...,
    /// expected_version=..., confirmation=...)`.
    fn execute_action_sequence_from_agent<'a>(
        &'a self,
        campaign_id: &'a str,
        session_id: &'a str,
        arguments: &'a JsonMap,
    ) -> BoxFuture<'a, CodedResult<Json>>;

    /// `provider_continuation(session_id, response_id, provider_id=..., model_id=...)`: one
    /// selected provider exchange's private continuation, as stored.
    fn provider_continuation<'a>(
        &'a self,
        session_id: &'a str,
        response_id: &'a str,
        provider_id: &'a str,
        model_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Option<Json>>>;

    /// `package_record(package_id, version, sha256)`.
    fn package_record<'a>(
        &'a self,
        package_id: &'a str,
        version: &'a str,
        sha256: &'a str,
    ) -> BoxFuture<'a, CodedResult<JsonMap>>;

    /// `package_lock_lineage(campaign_id)`: every lock the campaign has had, oldest first.
    fn package_lock_lineage<'a>(
        &'a self,
        campaign_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Vec<PackageLockRecord>>>;

    /// `append_agent_message(session_id=..., message_id=..., kind=..., content=...)`.
    fn append_agent_message<'a>(
        &'a self,
        session_id: &'a str,
        message_id: &'a str,
        kind: SessionMessageKind,
        content: &'a JsonMap,
    ) -> BoxFuture<'a, CodedResult<SessionMessage>>;

    /// `catalog(campaign_id)`, or `catalog(campaign_id, include_unexposed=True)`: the
    /// campaign's admitted tools, as the MCP listing reads them.
    fn catalog<'a>(
        &'a self,
        campaign_id: &'a str,
        include_unexposed: bool,
    ) -> BoxFuture<'a, CodedResult<Vec<crate::stateless_mcp::listing::CatalogEntry>>>;

    /// `accepted_campaign_plan(campaign_id).content`: refused with `CommandRejected("Accepted
    /// Campaign Plan is unavailable")` when there is none.
    fn accepted_campaign_plan<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<JsonMap>>;

    /// `campaign_narrative_options(session_id)`: the Campaign File note types one DM session
    /// may write, and their choices.
    fn campaign_narrative_options<'a>(&'a self, session_id: &'a str) -> BoxFuture<'a, CodedResult<JsonMap>>;

    /// `execute_action(command)`: one mechanic executed by the owner, refused in Python's class.
    fn execute_action<'a>(
        &'a self,
        command: &'a sylvia_domain::authority::ActionCommand,
    ) -> BoxFuture<
        'a,
        Result<sylvia_app::authority::actions::ActionExecution, crate::stateless_mcp::AuthorityFailure>,
    >;

    /// `session_activity_basis(session_id, attempt_id=None)`: the saved attempt facts the
    /// activity indicator reads, without the conversation.
    fn session_activity_basis<'a>(
        &'a self,
        session_id: &'a str,
        attempt_id: Option<&'a str>,
    ) -> BoxFuture<'a, CodedResult<sylvia_domain::session::SessionActivityBasis>>;

    /// `current_open_turn(campaign_id)`: the campaign's open TURN, if any.
    fn current_open_turn<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<Option<String>>>;

    /// `update_campaign_file(session_id=..., update_id=..., updates=..., basis_message_ids=...,
    /// turn_id=...)`: one validated Campaign File batch committed, refused in Python's class.
    fn update_campaign_file<'a>(
        &'a self,
        session_id: &'a str,
        update_id: &'a str,
        updates: Vec<JsonMap>,
        basis_message_ids: Vec<String>,
        turn_id: Option<&'a str>,
    ) -> BoxFuture<'a, Result<JsonMap, crate::stateless_mcp::AuthorityFailure>>;

    /// `search_campaign_knowledge(campaign_id, query=..., sources=..., limit=..., offset=...)`:
    /// the arguments as the MCP request gave them (the owner checks their types itself).
    fn search_campaign_knowledge<'a>(
        &'a self,
        campaign_id: &'a str,
        query: &'a Json,
        sources: Option<&'a Json>,
        limit: &'a Json,
        offset: &'a Json,
    ) -> BoxFuture<'a, Result<JsonMap, crate::stateless_mcp::AuthorityFailure>>;

    /// `actor_decision_request(campaign_id, turn_id=...)`: one opted-in actor's observation and
    /// offered operations, or `None` when the campaign's package does not isolate participants.
    fn actor_decision_request<'a>(
        &'a self,
        campaign_id: &'a str,
        turn_id: &'a str,
    ) -> BoxFuture<'a, CodedResult<Option<sylvia_app::actor_decisions::ActorDecisionRequest>>>;

    /// `read_campaign_reference(campaign_id, source=..., path=...)`.
    fn read_campaign_reference<'a>(
        &'a self,
        campaign_id: &'a str,
        source: &'a Json,
        path: &'a Json,
    ) -> BoxFuture<'a, Result<JsonMap, crate::stateless_mcp::AuthorityFailure>>;

    // The session loop's DM run (T5a-2). Each answer keeps its Python class
    // (`AuthorityFailure`), since the loop catches `CommandRejected` and reports its code.

    /// `agent_session_work(session_id)`: the session's context, read once its campaign's work is
    /// held; the work is released when the returned guard drops.
    fn agent_session_work<'a>(
        &'a self,
        session_id: &'a str,
    ) -> BoxFuture<'a, Result<HeldWork, AuthorityFailure>>;

    /// `builder_session_work(session_id)`: a Builder session's context under its held work.
    fn builder_session_work<'a>(&'a self, session_id: &'a str) -> BoxFuture<'a, Result<HeldWork, AuthorityFailure>>;

    /// `campaign_ending_choice_text(session_id, message_id, choice)`.
    fn campaign_ending_choice_text<'a>(
        &'a self,
        session_id: &'a str,
        message_id: &'a str,
        choice: &'a JsonMap,
    ) -> BoxFuture<'a, Result<String, AuthorityFailure>>;

    /// `player_action_response_basis(session_id, action_id, automatic=...)`.
    fn player_action_response_basis<'a>(
        &'a self,
        session_id: &'a str,
        action_id: &'a str,
        automatic: bool,
    ) -> BoxFuture<'a, Result<JsonMap, AuthorityFailure>>;

    /// `unresolved_session_interruption(context)`: the interruption plain input would leave
    /// behind, and whether it can be redirected.
    fn unresolved_session_interruption<'a>(
        &'a self,
        context: &'a SessionContext,
    ) -> BoxFuture<'a, Result<Option<(NarrationInterruption, bool)>, AuthorityFailure>>;

    /// `narration_interruption(campaign_id, interruption_id)`.
    fn narration_interruption<'a>(
        &'a self,
        campaign_id: &'a str,
        interruption_id: &'a str,
    ) -> BoxFuture<'a, Result<NarrationInterruption, AuthorityFailure>>;

    /// `redirect_narration(session_id=..., message_id=..., interruption_id=..., content=...)`.
    fn redirect_narration<'a>(
        &'a self,
        session_id: &'a str,
        message_id: &'a str,
        content: &'a JsonMap,
        interruption_id: &'a str,
    ) -> BoxFuture<'a, Result<NarrationInterruption, AuthorityFailure>>;

    /// `saved_action_receipt(campaign_id, action_id)`.
    fn saved_action_receipt<'a>(
        &'a self,
        campaign_id: &'a str,
        action_id: &'a str,
    ) -> BoxFuture<'a, Result<ActionReceipt, AuthorityFailure>>;

    /// `start_narration_attempt(campaign_id=..., attempt_id=..., turn_id=..., cancel=...,
    /// last_action_id=..., interruption_id=...)`.
    fn start_narration_attempt(
        &self,
        start: NarrationAttemptStart,
    ) -> BoxFuture<'_, Result<(), AuthorityFailure>>;

    /// `emit_narration_token(campaign_id, attempt_id, channel, token, response_blocks=...)`.
    fn emit_narration_token<'a>(
        &'a self,
        campaign_id: &'a str,
        attempt_id: &'a str,
        channel: ProseChannel,
        token: &'a str,
        response_blocks: Option<&'a Json>,
    ) -> BoxFuture<'a, Result<(), AuthorityFailure>>;

    /// `flush_narration(campaign_id, attempt_id)`.
    fn flush_narration<'a>(
        &'a self,
        campaign_id: &'a str,
        attempt_id: &'a str,
    ) -> BoxFuture<'a, Result<(), AuthorityFailure>>;

    /// `bind_narration_action(campaign_id, attempt_id, action_id)`.
    fn bind_narration_action<'a>(
        &'a self,
        campaign_id: &'a str,
        attempt_id: &'a str,
        action_id: &'a str,
    ) -> BoxFuture<'a, Result<(), AuthorityFailure>>;

    /// `complete_narration_attempt(campaign_id, attempt_id)`.
    fn complete_narration_attempt<'a>(
        &'a self,
        campaign_id: &'a str,
        attempt_id: &'a str,
    ) -> BoxFuture<'a, Result<Vec<CanonicalEvent>, AuthorityFailure>>;

    /// `interrupt_narration(campaign_id, attempt_id)`.
    fn interrupt_narration<'a>(
        &'a self,
        campaign_id: &'a str,
        attempt_id: &'a str,
    ) -> BoxFuture<'a, Result<NarrationInterruption, AuthorityFailure>>;

    /// `disconnect_narration(campaign_id, attempt_id, now=...)`.
    fn disconnect_narration<'a>(
        &'a self,
        campaign_id: &'a str,
        attempt_id: &'a str,
        now: f64,
    ) -> BoxFuture<'a, Result<f64, AuthorityFailure>>;

    /// `reconnect_narration(campaign_id, attempt_id, now=..., acknowledged_sequence=...)`.
    fn reconnect_narration<'a>(
        &'a self,
        campaign_id: &'a str,
        attempt_id: &'a str,
        now: f64,
        acknowledged_sequence: i64,
    ) -> BoxFuture<'a, Result<CanonicalReplay, AuthorityFailure>>;

    /// `expire_narration(campaign_id, attempt_id, now=...)`.
    fn expire_narration<'a>(
        &'a self,
        campaign_id: &'a str,
        attempt_id: &'a str,
        now: f64,
    ) -> BoxFuture<'a, Result<NarrationInterruption, AuthorityFailure>>;

    /// `append_private_provider_record(record)`.
    fn append_private_provider_record<'a>(
        &'a self,
        record: &'a ProviderPrivateRecord,
    ) -> BoxFuture<'a, Result<(), AuthorityFailure>>;

    /// `finish_private_provider_attempt(session_id, attempt_id, completed=...)`.
    fn finish_private_provider_attempt<'a>(
        &'a self,
        session_id: &'a str,
        attempt_id: &'a str,
        completed: bool,
    ) -> BoxFuture<'a, Result<(), AuthorityFailure>>;

    /// `append_session_canonical_references(session_id, events)`.
    fn append_session_canonical_references<'a>(
        &'a self,
        session_id: &'a str,
        events: &'a [CanonicalEvent],
    ) -> BoxFuture<'a, Result<Vec<SessionMessage>, AuthorityFailure>>;

    /// `append_live_steering_message(session_id=..., message_id=..., kind=..., content=...)`.
    fn append_live_steering_message<'a>(
        &'a self,
        session_id: &'a str,
        message_id: &'a str,
        kind: SessionMessageKind,
        content: &'a JsonMap,
    ) -> BoxFuture<'a, Result<SessionMessage, AuthorityFailure>>;

    /// `stop_agent_session(session_id)`: the owner's Stop Session.
    fn stop_agent_session<'a>(
        &'a self,
        session_id: &'a str,
    ) -> BoxFuture<'a, Result<SessionRecord, AuthorityFailure>>;
}

/// `agent_session_work`'s answer: the session's context, with the held work. The work is
/// released when this drops.
pub struct HeldWork {
    pub context: SessionContext,
    pub work: Box<dyn Send>,
}

/// `start_narration_attempt`'s keyword arguments.
pub struct NarrationAttemptStart {
    pub campaign_id: String,
    pub attempt_id: String,
    pub turn_id: String,
    /// `cancel`: the run's `control.cancel`.
    pub cancel: Arc<dyn Fn() + Send + Sync>,
    pub last_action_id: Option<String>,
    pub interruption_id: Option<String>,
}
