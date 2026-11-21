//! The plain records of `domain/authority.py`: frozen dataclasses without `__post_init__`
//! checks, so their fields are public. Field names and order follow Python. Python's
//! subclassing (`CampaignSnapshot(CampaignState)`) becomes a `state` field.
//!
//! Not ported here: `Mechanic` (a callable; lane 2 defines the engine's mechanic trait) and
//! `NarrationStream` (a callback wrapper; lane 4 owns streaming).

use super::{
    ActionStatus, CanonicalEventType, EventVisibility, LifecycleState, MechanicsChoiceAnswer,
    MechanicsChoiceRequest, ModelDecisionOwner, PackageLock, Participant, PlayerProjectionKind, TurnStatus,
    WriterRole,
};
use sylvia_base::{Json, JsonMap};

/// One campaign row.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CampaignRecord {
    pub campaign_id: String,
    pub display_name: String,
    pub lifecycle: LifecycleState,
    pub writer: WriterRole,
    pub revision: i64,
}

/// Durable result of confirming NEW CAMPAIGN before Builder lane work.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CampaignConfirmation {
    pub campaign_id: String,
    pub resulting_revision: i64,
}

/// Stable unbound campaign identity and deterministic interview entry.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GuidedCampaignDraft {
    pub campaign_id: String,
    pub builder_session_id: String,
    pub current_step: String,
    pub construction_revision: i64,
    pub resulting_revision: i64,
    pub answer_message_id: Option<String>,
}

/// One explicit Builder chat linked to a suspended Campaign Interview question.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct GuidedCreationSubflow {
    pub campaign_id: String,
    pub parent_session_id: String,
    pub child_session_id: String,
    pub purpose: String,
    pub current_step: String,
    pub status: String,
    pub construction_revision: i64,
}

/// Durable result of binding one Builder lane to a confirmed campaign.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CampaignConstructionBegun {
    pub campaign_id: String,
    pub lane: String,
    pub resulting_revision: i64,
}

/// Durable lane-audited, lane-neutral campaign construction identity.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CampaignConstructionCompleted {
    pub campaign_id: String,
    pub lane: String,
    pub canonical_representation_sha256: String,
    pub resulting_revision: i64,
}

/// Owner-only durable materialization basis for construction finalization.
#[derive(Debug, Clone, PartialEq)]
pub struct CampaignConstructionBasis {
    pub campaign_id: String,
    pub display_name: String,
    pub lane: String,
    pub canonical_input: JsonMap,
    pub canonical_plan: Option<JsonMap>,
    pub authority_turn_id: String,
    pub package_lock_id: String,
    pub package_candidate_id: Option<String>,
    pub builder_session_id: String,
    pub begin_revision: i64,
    pub completion_revision: Option<i64>,
    pub canonical_representation_sha256: Option<String>,
}

/// Durable receipt for one reversible campaign metadata/lifecycle operation.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CampaignLifecycleResult {
    pub campaign_id: String,
    pub lifecycle: LifecycleState,
    pub writer: WriterRole,
    pub resulting_revision: i64,
    pub session_id: Option<String>,
}

impl CampaignLifecycleResult {
    /// The campaign revision spelling used by snapshot-shaped callers.
    pub fn revision(&self) -> i64 {
        self.resulting_revision
    }
}

/// Content-free durable result of one explicit irreversible campaign purge.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CampaignPurgeResult {
    pub campaign_id: String,
    pub resulting_revision: i64,
}

/// Path-free immutable identity of one completely published portable archive.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CampaignExportResult {
    pub campaign_id: String,
    pub captured_revision: i64,
    pub archive_sha256: String,
    pub byte_count: i64,
}

/// Path-free immutable result of one exact portable campaign recovery.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CampaignRecoveryResult {
    pub campaign_id: String,
    pub captured_revision: i64,
    pub archive_sha256: String,
    pub byte_count: i64,
}

/// One record of the campaign's canonical event sequence.
#[derive(Debug, Clone, PartialEq)]
pub struct CanonicalEvent {
    pub sequence: i64,
    pub event_type: CanonicalEventType,
    pub turn_id: String,
    pub action_id: Option<String>,
    pub safe_payload: JsonMap,
    /// Python's default is [`EventVisibility::InternalSafe`].
    pub visibility: EventVisibility,
    pub attempt_id: Option<String>,
    pub attempt_ordinal: Option<i64>,
}

/// One immutable player-safe canonical or live provisional projection.
#[derive(Debug, Clone, PartialEq)]
pub struct PlayerProjection {
    pub kind: PlayerProjectionKind,
    pub sequence: Option<i64>,
    pub text: Option<String>,
    pub safe_payload: JsonMap,
    pub collapsed_by_default: bool,
    pub provisional: bool,
    pub action_id: Option<String>,
    pub attempt_id: Option<String>,
}

/// Durable replay and separately identified current live provisional state.
#[derive(Debug, Clone, PartialEq)]
pub struct CanonicalReplay {
    pub canonical: Vec<CanonicalEvent>,
    pub provisional: Vec<PlayerProjection>,
    pub high_water: i64,
}

/// One recorded state change of an action.
#[derive(Debug, Clone, PartialEq)]
pub struct MutationRecord {
    pub action_id: String,
    pub ordinal: i64,
    pub namespace: String,
    pub key: String,
    pub before: Json,
    pub after: Json,
}

/// One recorded consequence of an action.
#[derive(Debug, Clone, PartialEq)]
pub struct ConsequenceRecord {
    pub action_id: String,
    pub ordinal: i64,
    pub payload: JsonMap,
}

/// Terminal receipt of one mechanics action.
#[derive(Debug, Clone, PartialEq)]
pub struct ActionReceipt {
    pub campaign_id: String,
    pub action_id: String,
    pub status: ActionStatus,
    pub result: JsonMap,
    pub resulting_revision: i64,
}

/// Durable nonterminal mechanics reservation projected without action ambiguity.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AwaitingChoiceReceipt {
    pub campaign_id: String,
    pub action_id: String,
    pub basis_revision: i64,
    pub turn_id: String,
    pub choice: MechanicsChoiceRequest,
    pub choice_sequence: i64,
}

impl AwaitingChoiceReceipt {
    /// Python's `status` property.
    pub fn status(&self) -> &'static str {
        "awaiting_choice"
    }
}

/// `MechanicsExecutionReceipt = ActionReceipt | AwaitingChoiceReceipt`.
#[derive(Debug, Clone, PartialEq)]
pub enum MechanicsExecutionReceipt {
    Action(ActionReceipt),
    AwaitingChoice(AwaitingChoiceReceipt),
}

/// Exact immutable basis needed to deterministically reconstruct one mechanic.
#[derive(Debug, Clone, PartialEq)]
pub struct PendingMechanicsAction {
    pub campaign_id: String,
    pub action_id: String,
    pub turn_id: String,
    pub basis_revision: i64,
    pub writer: WriterRole,
    pub package_id: String,
    pub package_version: String,
    pub package_sha256: String,
    pub lock_id: String,
    pub tool_name: String,
    pub parameters: JsonMap,
    pub payload_hash: String,
    pub initiating_session_id: Option<String>,
    pub choice: MechanicsChoiceRequest,
    pub descriptor_sha256: String,
    pub player_action_origin: Option<JsonMap>,
    pub prior_answers: Vec<MechanicsChoiceAnswer>,
}

/// One interrupted narration.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct NarrationInterruption {
    pub campaign_id: String,
    pub interruption_id: String,
    pub turn_id: String,
    pub last_action_id: Option<String>,
    pub failed_attempt_id: String,
    pub retryable: bool,
    pub resolving_retry_id: Option<String>,
    pub resolved_sequence: Option<i64>,
}

/// Immutable safe input for one explicit manual narration retry.
#[derive(Debug, Clone, PartialEq)]
pub struct RetryNarrationInput {
    pub campaign_id: String,
    pub interruption_id: String,
    pub retry_id: String,
    pub failed_attempt_id: String,
    pub canonical_high_water: i64,
    pub turn_id: String,
    pub action_receipt: Option<ActionReceipt>,
}

/// Result of one manual retry.
#[derive(Debug, Clone, PartialEq)]
pub struct ManualRetryResult {
    pub input: RetryNarrationInput,
    pub canonical: Vec<CanonicalEvent>,
    pub replayed: bool,
}

/// One stored mechanics action.
#[derive(Debug, Clone, PartialEq)]
pub struct ActionRecord {
    pub action_id: String,
    pub turn_id: String,
    pub status: ActionStatus,
    pub expected_revision: i64,
    pub resulting_revision: i64,
    pub writer: WriterRole,
    pub package_id: String,
    pub package_version: String,
    pub package_sha256: String,
    pub tool_name: String,
    pub parameters: JsonMap,
    pub result: JsonMap,
}

/// One immutable change to campaign-owned fictional continuity.
#[derive(Debug, Clone, PartialEq)]
pub struct CampaignContinuityRecord {
    pub entry_id: String,
    pub record_id: String,
    pub kind: String,
    pub status: String,
    pub payload: JsonMap,
    pub basis_message_ids: Vec<String>,
    pub admitted_revision: i64,
    pub operation_id: Option<String>,
}

/// Materialized current continuity plus its immutable campaign history.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct CampaignContinuityView {
    pub current_scene: Option<JsonMap>,
    pub facts: Vec<JsonMap>,
    pub reveals: JsonMap,
    pub plan_progress: Vec<JsonMap>,
    pub records: Vec<CampaignContinuityRecord>,
}

/// Owner-authoritative request to change one Campaign State continuity record.
#[derive(Debug, Clone, PartialEq)]
pub struct CampaignContinuityCommand {
    pub campaign_id: String,
    pub operation_id: String,
    pub session_id: String,
    pub expected_revision: i64,
    pub kind: String,
    pub record_id: String,
    pub status: String,
    pub payload: JsonMap,
    pub basis_message_ids: Vec<String>,
    pub turn_id: String,
}

/// Replayable result of one committed Campaign State continuity change.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct CampaignContinuityReceipt {
    pub campaign_id: String,
    pub operation_id: String,
    pub record_id: String,
    pub kind: String,
    pub resulting_revision: i64,
}

/// Current campaign authority shared by complete snapshots and display reads.
#[derive(Debug, Clone, PartialEq)]
pub struct CampaignState {
    pub campaign_id: String,
    pub display_name: String,
    pub lifecycle: LifecycleState,
    pub writer: WriterRole,
    pub revision: i64,
    pub package_lock: PackageLock,
    pub state: JsonMap,
    pub participants: Vec<Participant>,
}

/// Current state with only requested display history; never an engine snapshot.
#[derive(Debug, Clone, PartialEq)]
pub struct CampaignDisplaySnapshot {
    pub state: CampaignState,
    pub actions: Vec<ActionRecord>,
    pub events: Vec<CanonicalEvent>,
    /// Python's `Mapping[str, TurnStatus]`, in insertion order.
    pub turns: Vec<(String, TurnStatus)>,
    pub continuity: CampaignContinuityView,
}

/// The complete campaign snapshot the engine runs on.
#[derive(Debug, Clone, PartialEq)]
pub struct CampaignSnapshot {
    pub state: CampaignState,
    pub actions: Vec<ActionRecord>,
    pub events: Vec<CanonicalEvent>,
    pub mutations: Vec<MutationRecord>,
    pub consequences: Vec<ConsequenceRecord>,
    /// Python's `Mapping[str, TurnStatus]`, in insertion order.
    pub turns: Vec<(String, TurnStatus)>,
    pub continuity: CampaignContinuityView,
}

/// One owner-safe actor included in a package-declared decision scope.
#[derive(Debug, Clone, PartialEq)]
pub struct ModelDecisionActor {
    pub actor_id: String,
    pub actor_name: String,
    pub actor_state: JsonMap,
}

/// Ruleset-neutral projection of the current actor or side decision scope.
#[derive(Debug, Clone, PartialEq)]
pub struct ModelDecisionBoundary {
    pub owner: ModelDecisionOwner,
    pub actor_id: Option<String>,
    pub actor_name: Option<String>,
    pub actor_state: JsonMap,
    pub campaign_revision: i64,
    pub active_side_id: Option<String>,
    pub focused_actor_id: Option<String>,
    pub eligible_actors: Vec<ModelDecisionActor>,
    pub preparation: Option<JsonMap>,
}
