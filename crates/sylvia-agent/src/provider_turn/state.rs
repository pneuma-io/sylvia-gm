//! `agent/_provider_turn_state.py`: the typed locals shared by the GM provider-turn phases.
//!
//! Python's dataclasses leave every field unassigned (`field(init=False)`) until the phase that
//! originally assigned the local assigns it, and reading one before then raises
//! `UnboundLocalError` with CPython's own message. Here each field is a [`Slot`]: a getter
//! returns [`Unbound`] until a setter runs, so an early read fails the same way, at the same
//! point. Fields are named and ordered as in Python.

use crate::provider_session::tagged::Tagged;
use crate::stateless_mcp::ModelMechanicsTool;
use indexmap::{IndexMap, IndexSet};
use sylvia_app::actor_decisions::ActorDecisionRequest;
use sylvia_base::{CodedError, Json, JsonMap};
use sylvia_domain::authority::{CampaignSnapshot, ModelDecisionBoundary, ModelToolBehavior, Participant};
use sylvia_domain::session::SessionMessage;

/// `UnboundLocalError`: a turn local read before its phase assigned it.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Unbound(pub &'static str);

impl Unbound {
    /// CPython's message for the unassigned local.
    pub fn message(&self) -> String {
        format!("cannot access local variable '{}' where it is not associated with a value", self.0)
    }
}

impl std::fmt::Display for Unbound {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(&self.message())
    }
}

impl std::error::Error for Unbound {}

impl From<Unbound> for CodedError {
    /// An unexpected exception, as the owner reports any uncaught error.
    fn from(error: Unbound) -> Self {
        CodedError::new("internal_error", error.message()).with_error_type("UnboundLocalError")
    }
}

/// The result of reading a turn local.
pub type Local<T> = Result<T, Unbound>;

/// One turn local, named for its `UnboundLocalError`: unassigned until its phase assigns it.
#[derive(Debug, Clone, PartialEq)]
pub struct Slot<T> {
    name: &'static str,
    value: Option<T>,
}

impl<T> Slot<T> {
    /// An unassigned local called `name`.
    pub const fn unassigned(name: &'static str) -> Self {
        Self { name, value: None }
    }

    /// The value, or `UnboundLocalError`.
    pub fn get(&self) -> Local<&T> {
        self.value.as_ref().ok_or(Unbound(self.name))
    }

    /// The value to change in place, or `UnboundLocalError`.
    pub fn get_mut(&mut self) -> Local<&mut T> {
        self.value.as_mut().ok_or(Unbound(self.name))
    }

    /// Assign the local.
    pub fn set(&mut self, value: T) {
        self.value = Some(value);
    }

    /// Whether its phase has assigned it.
    pub fn is_assigned(&self) -> bool {
        self.value.is_some()
    }
}

impl<T: Clone> Slot<T> {
    /// A copy of the value, or `UnboundLocalError`.
    pub fn value(&self) -> Local<T> {
        self.get().cloned()
    }
}

/// Declares a struct of turn locals, each a [`Slot`] named after its field.
macro_rules! turn_locals {
    (
        $(#[$meta:meta])*
        pub struct $name:ident {
            $(
                $(#[$field_meta:meta])*
                $field:ident: $type:ty,
            )*
        }
    ) => {
        $(#[$meta])*
        #[derive(Debug, Clone, PartialEq)]
        pub struct $name {
            $(
                $(#[$field_meta])*
                pub $field: Slot<$type>,
            )*
        }

        impl Default for $name {
            /// Every local unassigned.
            fn default() -> Self {
                Self { $($field: Slot::unassigned(stringify!($field)),)* }
            }
        }
    };
}

turn_locals! {
    /// `TurnObligations`: independent obligations kept at their exchange or round boundary.
    pub struct TurnObligations {
        /// Held narration still needs a Campaign File disposition.
        buffered_narration_needs_disposition: bool,
        /// A settled Campaign File still needs its player-facing narration.
        campaign_file_narration_pending: bool,
        /// The next model result must settle held Campaign File work.
        force_campaign_file_update: bool,
        /// A rejected required call still needs correction in this exchange.
        pending_failed_tool_call: bool,
        /// A declared player intention remains unresolved across rounds.
        player_intent_pending: bool,
        /// This round must resolve the player's pending intention.
        player_intent_resolution_required: bool,
        /// The GM must review the current engine transition before continuing.
        engine_transition_review_pending: bool,
        /// Provisional tool prose from this exchange remains unreviewed.
        provisional_tool_prose_pending: bool,
    }
}

turn_locals! {
    /// `TurnProgress`.
    pub struct TurnProgress {
        /// The current disposition is settled; new work can invalidate it.
        campaign_file_disposition_in_exchange: bool,
        /// This exchange has committed authoritative work.
        any_commit_in_exchange: bool,
        /// This exchange has committed a Campaign File update.
        campaign_file_commit_in_exchange: bool,
        /// This tool batch has already submitted a state-changing call.
        state_changing_call_submitted: bool,
        /// The current tool batch contains a rejected or failed call.
        round_failed: bool,
        /// A saved or current action sequence stopped before completing.
        sequence_execution_stopped: bool,
    }
}

turn_locals! {
    /// `TurnDecisions`.
    pub struct TurnDecisions {
        /// This round must finish a Ruleset decision owned by the GM.
        gm_boundary_decision: bool,
        /// This round narrates at a Ruleset boundary owned by the player.
        player_boundary_narration: bool,
    }
}

/// `_ToolCallAssembly`: one streamed tool call as it is assembled.
#[derive(Debug, Clone, PartialEq, Eq, Default)]
pub struct ToolCallAssembly {
    pub call_id: String,
    pub name: String,
    pub arguments: String,
}

turn_locals! {
    /// `ProviderTurnState`: the turn's shared locals. Its three groups are always assigned.
    pub struct TurnLocals {
        actor_request: Option<ActorDecisionRequest>,
        aliases: IndexMap<String, ModelMechanicsTool>,
        assemblies: IndexMap<i64, ToolCallAssembly>,
        assembly: ToolCallAssembly,
        assistant_reasoning: Vec<String>,
        assistant_text: Vec<String>,
        basis_message_id: Option<String>,
        behavior: ModelToolBehavior,
        buffered_narration: Option<String>,
        buffered_raw_response_blocks: Option<Vec<JsonMap>>,
        buffered_response_blocks: Option<Vec<JsonMap>>,
        campaign_file_disposition_supported: bool,
        campaign_file_mutation_attempted: bool,
        campaign_id: String,
        canonical_player_narration: Vec<JsonMap>,
        catalog_tool: ModelMechanicsTool,
        committed_action_ids: IndexSet<String>,
        context_fields: JsonMap,
        context_owner: Option<JsonMap>,
        current_application_controls: Vec<JsonMap>,
        current_catalog: Vec<ModelMechanicsTool>,
        current_player_action: Option<String>,
        current_player_message: Option<SessionMessage>,
        current_user_wire_text: Option<String>,
        decision_boundary: Option<ModelDecisionBoundary>,
        direct_response: bool,
        discovery_id: String,
        empty_collection_paths: Vec<String>,
        empty_completion_corrections_used: i64,
        entity_display_names: IndexMap<String, String>,
        /// `exchange_messages`: Python assigns `state.messages` itself (the same list), so it is
        /// only a marker here and [`TurnLocals::exchange_messages_mut`] reaches `messages`.
        exchange_messages: (),
        facts: Option<JsonMap>,
        finish_reason: String,
        inference_id: String,
        inference_messages: Vec<Tagged>,
        local_tool_failures: IndexSet<String>,
        local_tool_results: IndexMap<String, JsonMap>,
        max_empty_completion_corrections: i64,
        max_missing_required_tool_corrections: i64,
        max_private_deliberation_corrections: i64,
        max_rejected_tool_corrections: i64,
        max_repeated_response_corrections: i64,
        mechanics_action_ids: IndexMap<String, String>,
        mechanics_parameters: IndexMap<String, JsonMap>,
        mechanics_pre_entities: IndexMap<String, JsonMap>,
        mechanics_preparation_scopes: IndexMap<String, Json>,
        mechanics_tool_behaviors: IndexMap<String, ModelToolBehavior>,
        mechanics_tool_names: IndexMap<String, String>,
        messages: Vec<Tagged>,
        missing: Vec<String>,
        missing_required_tool_corrections_used: i64,
        mutation_attempted_before_call: bool,
        narration: String,
        native_assistant_content: Option<Vec<JsonMap>>,
        native_chat_metadata: Option<JsonMap>,
        ooc_ack_missing: bool,
        ooc_ack_required: bool,
        opening_footing: Option<JsonMap>,
        opening_request: bool,
        ordered: Vec<ToolCallAssembly>,
        parameters: JsonMap,
        parameters_establish_changes: bool,
        participant_ooc_only: bool,
        participants: Vec<Participant>,
        pending_boundary: Option<ModelDecisionBoundary>,
        pending_narration_facts: Vec<JsonMap>,
        post_commit_tool_required: bool,
        post_mechanics_recovery: bool,
        previous_player_action: Option<String>,
        previous_player_facing_response: Option<String>,
        private_deliberation_correction_message: Option<JsonMap>,
        private_deliberation_corrections_used: i64,
        provider_round: i64,
        provider_tool_call_required: bool,
        provider_tool_schemas: IndexMap<String, JsonMap>,
        rejected_tool_corrections_used: i64,
        repeated_response_corrections_used: i64,
        required_tool_call_correction_message: Option<JsonMap>,
        required_tool_failures: IndexSet<String>,
        required_transitions: Vec<ModelMechanicsTool>,
        resumed_messages: Vec<Tagged>,
        /// `object`: the revised narration argument as the model sent it.
        revised_narration: Json,
        revised_narration_supplied: bool,
        revised_raw_blocks: Option<Vec<JsonMap>>,
        round_finish_reason: Option<String>,
        round_has_non_narrative_call: bool,
        saved_sequence_results: Vec<JsonMap>,
        selected_raw_blocks: Option<Vec<JsonMap>>,
        sequence_narration_facts: Vec<JsonMap>,
        settled_narration: Option<String>,
        snapshot: CampaignSnapshot,
        tools: Vec<JsonMap>,
        unsupported_speakers: bool,
        update: JsonMap,
    }
}

impl TurnLocals {
    /// `state.exchange_messages`: the round's messages, the same list as `state.messages` once
    /// the round assigned it (`state.exchange_messages = state.messages`).
    pub fn exchange_messages_mut(&mut self) -> Local<&mut Vec<Tagged>> {
        self.exchange_messages.get()?;
        self.messages.get_mut()
    }
}

/// `ProviderTurnState`: the three obligation groups (always assigned) and the turn locals.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct ProviderTurnState {
    pub obligations: TurnObligations,
    pub progress: TurnProgress,
    pub decisions: TurnDecisions,
    pub locals: TurnLocals,
}
