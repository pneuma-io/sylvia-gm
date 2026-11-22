//! `agent/_provider_turn_decisions.py`: the pure questions asked at the GM turn's decision
//! points.
//!
//! Each reads the turn locals in Python's order and short-circuits where Python's `and`/`or`
//! does, so a local read before its phase assigned it fails with `UnboundLocalError` at the
//! same point (see [`super::state`]).

use super::state::{Local, ProviderTurnState};
use crate::provider_session::context::{splitlines, SessionProviderInput};
use sylvia_base::{pystr, Json};
use sylvia_domain::authority::{ModelToolBehavior, WriterRole};

/// `_buffered_narration_is_settled`.
pub fn buffered_narration_is_settled(state: &ProviderTurnState) -> Local<bool> {
    Ok(state.locals.buffered_narration.get()?.is_some()
        && *state.progress.campaign_file_disposition_in_exchange.get()?
        && !*state.obligations.buffered_narration_needs_disposition.get()?)
}

/// `_required_transition_can_run`.
pub fn required_transition_can_run(state: &ProviderTurnState) -> Local<bool> {
    Ok(!state.locals.required_transitions.get()?.is_empty()
        && !*state.obligations.engine_transition_review_pending.get()?)
}

/// `_actor_decision_can_be_requested`.
pub fn actor_decision_can_be_requested(state: &ProviderTurnState) -> Local<bool> {
    Ok(!*state.obligations.engine_transition_review_pending.get()?
        && !*state.locals.participant_ooc_only.get()?)
}

/// `_tool_round_needs_correction`.
pub fn tool_round_needs_correction(state: &ProviderTurnState) -> Local<bool> {
    state.progress.round_failed.value()
}

/// `_tool_round_can_release_narration`.
pub fn tool_round_can_release_narration(state: &ProviderTurnState) -> Local<bool> {
    Ok(!*state.locals.round_has_non_narrative_call.get()?
        && state.locals.buffered_narration.get()?.is_some()
        && *state.progress.campaign_file_disposition_in_exchange.get()?
        && !*state.obligations.buffered_narration_needs_disposition.get()?)
}

/// `_held_campaign_file_narration_is_needed`.
pub fn held_campaign_file_narration_is_needed(state: &ProviderTurnState) -> Local<bool> {
    Ok(state.locals.narration.get()?.is_empty()
        && state.locals.buffered_narration.get()?.is_some()
        && *state.obligations.campaign_file_narration_pending.get()?)
}

/// `_narration_repeats_previous_player_response`.
pub fn narration_repeats_previous_player_response(state: &ProviderTurnState) -> Local<bool> {
    let locals = &state.locals;
    Ok(!*state.obligations.force_campaign_file_update.get()?
        && !*state.obligations.campaign_file_narration_pending.get()?
        && !*locals.provider_tool_call_required.get()?
        && locals.current_player_action.get()?.is_some()
        && locals.previous_player_action.get()?.is_some()
        && locals.current_player_action.get()? != locals.previous_player_action.get()?
        && locals.previous_player_facing_response.get()?.is_some()
        && Some(locals.narration.get()?) == locals.previous_player_facing_response.get()?.as_ref())
}

/// `_player_boundary_prose_can_end_turn`: Ruleset control is back with the player and nothing
/// declared remains to resolve, so prose here is the turn's narration (held for the Campaign
/// File disposition, which still settles the turn before anything is shown).
pub fn player_boundary_prose_can_end_turn(state: &ProviderTurnState) -> Local<bool> {
    Ok(*state.decisions.player_boundary_narration.get()?
        && !*state.obligations.player_intent_resolution_required.get()?
        && !*state.decisions.gm_boundary_decision.get()?
        && !*state.obligations.engine_transition_review_pending.get()?
        && !*state.obligations.pending_failed_tool_call.get()?
        && *state.progress.any_commit_in_exchange.get()?
        && *state.locals.campaign_file_disposition_supported.get()?)
}

/// `_required_tool_call_is_missing`.
pub fn required_tool_call_is_missing(state: &ProviderTurnState) -> Local<bool> {
    Ok(*state.locals.provider_tool_call_required.get()?
        && !*state.obligations.force_campaign_file_update.get()?
        && !player_boundary_prose_can_end_turn(state)?)
}

/// `_PRIVATE_DELIBERATION_STARTS`.
const PRIVATE_DELIBERATION_STARTS: [&str; 4] =
    ["the player wants ", "the player's declared action ", "the declared player action ", "the user wants "];

/// `_is_private_deliberation_line(line)`.
pub fn is_private_deliberation_line(line: &str) -> bool {
    let folded = pystr::casefold(pystr::strip(line));
    PRIVATE_DELIBERATION_STARTS.iter().any(|start| folded.starts_with(start))
}

/// `_looks_like_private_deliberation(text)`: provider prose describing the player request as an
/// instruction.
pub fn looks_like_private_deliberation(text: &str) -> bool {
    splitlines(text).into_iter().any(is_private_deliberation_line)
}

/// `_narration_needs_private_correction`.
pub fn narration_needs_private_correction(state: &ProviderTurnState) -> Local<bool> {
    Ok(!*state.obligations.force_campaign_file_update.get()?
        && looks_like_private_deliberation(state.locals.narration.get()?))
}

/// `_campaign_file_needs_narration`.
pub fn campaign_file_needs_narration(state: &ProviderTurnState) -> Local<bool> {
    state.obligations.campaign_file_narration_pending.value()
}

/// `_campaign_file_must_be_settled`.
pub fn campaign_file_must_be_settled(state: &ProviderTurnState) -> Local<bool> {
    state.obligations.force_campaign_file_update.value()
}

/// `_required_tool_correction_was_abandoned`.
pub fn required_tool_correction_was_abandoned(state: &ProviderTurnState) -> Local<bool> {
    state.obligations.pending_failed_tool_call.value()
}

/// `_round_needs_gm_decision`.
pub fn round_needs_gm_decision(state: &ProviderTurnState) -> Local<bool> {
    state.decisions.gm_boundary_decision.value()
}

/// `_round_needs_player_intent_resolution`.
pub fn round_needs_player_intent_resolution(state: &ProviderTurnState) -> Local<bool> {
    state.obligations.player_intent_resolution_required.value()
}

/// `_opening_needs_campaign_file_update`.
pub fn opening_needs_campaign_file_update(state: &ProviderTurnState) -> Local<bool> {
    Ok(*state.locals.opening_request.get()? && !*state.progress.campaign_file_commit_in_exchange.get()?)
}

/// `_committed_work_needs_campaign_file_disposition`.
pub fn committed_work_needs_campaign_file_disposition(state: &ProviderTurnState) -> Local<bool> {
    Ok(*state.progress.any_commit_in_exchange.get()?
        && !*state.progress.campaign_file_disposition_in_exchange.get()?
        && *state.locals.campaign_file_disposition_supported.get()?
        && !*state.obligations.campaign_file_narration_pending.get()?)
}

/// `_saved_gm_boundary_should_be_read`.
pub fn saved_gm_boundary_should_be_read(state: &ProviderTurnState) -> Local<bool> {
    Ok(state.locals.decision_boundary.get()?.is_none()
        && !*state.progress.any_commit_in_exchange.get()?
        && !*state.locals.participant_ooc_only.get()?)
}

/// `_engine_transition_needs_review`.
pub fn engine_transition_needs_review(state: &ProviderTurnState) -> Local<bool> {
    state.obligations.engine_transition_review_pending.value()
}

/// `_campaign_file_requires_atomic_completion`.
pub fn campaign_file_requires_atomic_completion(
    state: &ProviderTurnState,
    provider_input: &SessionProviderInput,
) -> Local<bool> {
    Ok(provider_input.session.role() == WriterRole::Dm
        && *state.locals.campaign_file_disposition_supported.get()?
        && !*state.progress.campaign_file_disposition_in_exchange.get()?
        && !*state.obligations.campaign_file_narration_pending.get()?)
}

/// `_round_requires_tool_call`.
pub fn round_requires_tool_call(
    state: &ProviderTurnState,
    atomic_campaign_file_completion_required: bool,
) -> Local<bool> {
    Ok(*state.obligations.engine_transition_review_pending.get()?
        || *state.obligations.force_campaign_file_update.get()?
        || *state.locals.post_commit_tool_required.get()?
        || *state.decisions.gm_boundary_decision.get()?
        || *state.obligations.player_intent_resolution_required.get()?
        || atomic_campaign_file_completion_required)
}

/// `_settled_disposition_still_owes_ooc_acknowledgement`.
pub fn settled_disposition_still_owes_ooc_acknowledgement(state: &ProviderTurnState) -> Local<bool> {
    Ok(*state.locals.ooc_ack_required.get()?
        && *state.progress.campaign_file_disposition_in_exchange.get()?
        && !state
            .locals
            .buffered_response_blocks
            .get()?
            .as_ref()
            .is_some_and(|blocks| blocks.iter().any(|block| block.get("kind") == Some(&Json::from("ooc")))))
}

/// `_held_disposition_must_be_retried`.
pub fn held_disposition_must_be_retried(state: &ProviderTurnState) -> Local<bool> {
    Ok(*state.obligations.force_campaign_file_update.get()?
        && (state.locals.buffered_narration.get()?.is_some()
            || state.locals.buffered_raw_response_blocks.get()?.is_some()))
}

/// `_tool_call_needs_fresh_catalog(state, position)`.
pub fn tool_call_needs_fresh_catalog(state: &ProviderTurnState, position: i64) -> Local<bool> {
    Ok(position > 0
        && *state.progress.state_changing_call_submitted.get()?
        && state.locals.catalog_tool.get()?.category != "reference")
}

/// `_exchange_has_committed_work`.
pub fn exchange_has_committed_work(state: &ProviderTurnState) -> Local<bool> {
    state.progress.any_commit_in_exchange.value()
}

/// `_round_allows_history_tool`.
pub fn round_allows_history_tool(state: &ProviderTurnState) -> Local<bool> {
    Ok(!(*state.obligations.force_campaign_file_update.get()?
        || *state.obligations.campaign_file_narration_pending.get()?))
}

/// `_player_intent_remains_pending`.
pub fn player_intent_remains_pending(state: &ProviderTurnState) -> Local<bool> {
    state.obligations.player_intent_pending.value()
}

/// `_buffered_narration_can_be_replaced`.
pub fn buffered_narration_can_be_replaced(state: &ProviderTurnState) -> Local<bool> {
    Ok(!*state.obligations.force_campaign_file_update.get()?)
}

/// `_committed_tool_resolves_player_boundary`.
pub fn committed_tool_resolves_player_boundary(state: &ProviderTurnState) -> Local<bool> {
    Ok(*state.locals.behavior.get()? == ModelToolBehavior::ResolvesPlayerIntent
        && *state.decisions.player_boundary_narration.get()?)
}

/// `_round_is_at_player_boundary`.
pub fn round_is_at_player_boundary(state: &ProviderTurnState) -> Local<bool> {
    state.decisions.player_boundary_narration.value()
}

/// `_provisional_tool_prose_needs_review`.
pub fn provisional_tool_prose_needs_review(state: &ProviderTurnState) -> Local<bool> {
    state.obligations.provisional_tool_prose_pending.value()
}
