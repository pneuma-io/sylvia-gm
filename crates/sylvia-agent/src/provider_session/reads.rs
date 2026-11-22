//! The application-owner reads the GM request assembly makes (`ProviderSessionAdapter._owner`).
//!
//! Python's adapter holds an `AuthorityOwner` and calls these methods on it, some directly and
//! some through `getattr`; every one exists on the real owner, so they are plain methods here.
//! Lane 3 implements this trait over the store in `sylvia-app`. The shapes are Python's: the
//! arguments it passes and the values it reads back.

use sylvia_base::{CodedResult, JsonMap};
use sylvia_domain::authority::{
    CampaignConstructionBasis, CampaignDisplaySnapshot, CampaignSnapshot, CanonicalReplay,
    ModelDecisionBoundary,
};
use sylvia_domain::package_library::PackageLockRecord;
use sylvia_domain::session::SessionContext;
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
    fn current_package_lock<'a>(&'a self, campaign_id: &'a str) -> BoxFuture<'a, CodedResult<PackageLockRecord>>;

    /// `record_admin_full_audit(payload)`: kept only while Admin Full Audit is on.
    fn record_admin_full_audit(&self, payload: JsonMap) -> CodedResult<()>;
}
