//! The application-owner reads the GM request assembly makes (`ProviderSessionAdapter._owner`).
//!
//! Python's adapter holds an `AuthorityOwner` and calls these methods on it, some directly and
//! some through `getattr`; every one exists on the real owner, so they are plain methods here.
//! Lane 3 implements this trait over the store in `sylvia-app`. The shapes are Python's: the
//! arguments it passes and the values it reads back.

use sylvia_base::{CodedResult, Json, JsonMap};
use sylvia_domain::authority::{
    CampaignConstructionBasis, CampaignDisplaySnapshot, CampaignSnapshot, CanonicalReplay,
    ModelDecisionBoundary,
};
use sylvia_domain::package_library::PackageLockRecord;
use sylvia_domain::session::{SessionContext, SessionMessage, SessionMessageKind};
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
}
