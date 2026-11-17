"""Pure questions asked at the existing GM turn's separate decision points."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from sylvia_gm.domain.authority import ModelToolBehavior, WriterRole

from ._provider_turn_state import ProviderTurnState

if TYPE_CHECKING:
    from ._session_loop import SessionProviderInput


def _buffered_narration_is_settled(state: ProviderTurnState) -> bool:
    return bool(
        state.buffered_narration is not None
        and state.progress.campaign_file_disposition_in_exchange
        and (not state.obligations.buffered_narration_needs_disposition)
    )


def _required_transition_can_run(state: ProviderTurnState) -> bool:
    return bool(
        state.required_transitions and (not state.obligations.engine_transition_review_pending)
    )


def _actor_decision_can_be_requested(state: ProviderTurnState) -> bool:
    return bool(
        not state.obligations.engine_transition_review_pending and (not state.participant_ooc_only)
    )


def _tool_round_needs_correction(state: ProviderTurnState) -> bool:
    return bool(state.progress.round_failed)


def _tool_round_can_release_narration(state: ProviderTurnState) -> bool:
    return bool(
        not state.round_has_non_narrative_call
        and state.buffered_narration is not None
        and state.progress.campaign_file_disposition_in_exchange
        and (not state.obligations.buffered_narration_needs_disposition)
    )


def _held_campaign_file_narration_is_needed(state: ProviderTurnState) -> bool:
    return bool(
        not state.narration
        and state.buffered_narration is not None
        and state.obligations.campaign_file_narration_pending
    )


def _narration_repeats_previous_player_response(state: ProviderTurnState) -> bool:
    return bool(
        not state.obligations.force_campaign_file_update
        and (not state.obligations.campaign_file_narration_pending)
        and (not state.provider_tool_call_required)
        and (state.current_player_action is not None)
        and (state.previous_player_action is not None)
        and (state.current_player_action != state.previous_player_action)
        and (state.previous_player_facing_response is not None)
        and (state.narration == state.previous_player_facing_response)
    )


def _required_tool_call_is_missing(state: ProviderTurnState) -> bool:
    return bool(
        state.provider_tool_call_required and (not state.obligations.force_campaign_file_update)
    )


def _narration_needs_private_correction(
    state: ProviderTurnState, _looks_like_private_deliberation: Callable[[str], bool]
) -> bool:
    return bool(
        not state.obligations.force_campaign_file_update
        and _looks_like_private_deliberation(state.narration)
    )


def _campaign_file_needs_narration(state: ProviderTurnState) -> bool:
    return bool(state.obligations.campaign_file_narration_pending)


def _campaign_file_must_be_settled(state: ProviderTurnState) -> bool:
    return bool(state.obligations.force_campaign_file_update)


def _required_tool_correction_was_abandoned(state: ProviderTurnState) -> bool:
    return bool(state.obligations.pending_failed_tool_call)


def _round_needs_gm_decision(state: ProviderTurnState) -> bool:
    return bool(state.decisions.gm_boundary_decision)


def _round_needs_player_intent_resolution(state: ProviderTurnState) -> bool:
    return bool(state.obligations.player_intent_resolution_required)


def _opening_needs_campaign_file_update(state: ProviderTurnState) -> bool:
    return bool(state.opening_request and (not state.progress.campaign_file_commit_in_exchange))


def _committed_work_needs_campaign_file_disposition(state: ProviderTurnState) -> bool:
    return bool(
        state.progress.any_commit_in_exchange
        and (not state.progress.campaign_file_disposition_in_exchange)
        and state.campaign_file_disposition_supported
        and (not state.obligations.campaign_file_narration_pending)
    )


def _saved_gm_boundary_should_be_read(state: ProviderTurnState) -> bool:
    return bool(
        state.decision_boundary is None
        and (not state.progress.any_commit_in_exchange)
        and (not state.participant_ooc_only)
    )


def _engine_transition_needs_review(state: ProviderTurnState) -> bool:
    return bool(state.obligations.engine_transition_review_pending)


def _campaign_file_requires_atomic_completion(
    state: ProviderTurnState, provider_input: SessionProviderInput
) -> bool:
    return bool(
        provider_input.session.role is WriterRole.DM
        and state.campaign_file_disposition_supported
        and (not state.progress.campaign_file_disposition_in_exchange)
        and (not state.obligations.campaign_file_narration_pending)
    )


def _round_requires_tool_call(
    state: ProviderTurnState, atomic_campaign_file_completion_required: bool
) -> bool:
    return bool(
        state.obligations.engine_transition_review_pending
        or state.obligations.force_campaign_file_update
        or state.post_commit_tool_required
        or state.decisions.gm_boundary_decision
        or state.obligations.player_intent_resolution_required
        or atomic_campaign_file_completion_required
    )


def _settled_disposition_still_owes_ooc_acknowledgement(state: ProviderTurnState) -> bool:
    return bool(
        state.ooc_ack_required
        and state.progress.campaign_file_disposition_in_exchange
        and (
            not (
                state.buffered_response_blocks is not None
                and any(block.get("kind") == "ooc" for block in state.buffered_response_blocks)
            )
        )
    )


def _held_disposition_must_be_retried(state: ProviderTurnState) -> bool:
    return bool(
        state.obligations.force_campaign_file_update
        and (state.buffered_narration is not None or state.buffered_raw_response_blocks is not None)
    )


def _tool_call_needs_fresh_catalog(state: ProviderTurnState, position: int) -> bool:
    return bool(
        position > 0
        and state.progress.state_changing_call_submitted
        and (state.catalog_tool.category != "reference")
    )


def _exchange_has_committed_work(state: ProviderTurnState) -> bool:
    return bool(state.progress.any_commit_in_exchange)


def _round_allows_history_tool(state: ProviderTurnState) -> bool:
    return bool(
        not (
            state.obligations.force_campaign_file_update
            or state.obligations.campaign_file_narration_pending
        )
    )


def _player_intent_remains_pending(state: ProviderTurnState) -> bool:
    return bool(state.obligations.player_intent_pending)


def _buffered_narration_can_be_replaced(state: ProviderTurnState) -> bool:
    return bool(not state.obligations.force_campaign_file_update)


def _committed_tool_resolves_player_boundary(state: ProviderTurnState) -> bool:
    return bool(
        state.behavior is ModelToolBehavior.RESOLVES_PLAYER_INTENT
        and state.decisions.player_boundary_narration
    )


def _round_is_at_player_boundary(state: ProviderTurnState) -> bool:
    return bool(state.decisions.player_boundary_narration)


def _provisional_tool_prose_needs_review(state: ProviderTurnState) -> bool:
    return bool(state.obligations.provisional_tool_prose_pending)
