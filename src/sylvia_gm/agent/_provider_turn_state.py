"""Typed values and independent obligations shared by the GM provider-turn phases."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Never

if TYPE_CHECKING:
    from sylvia_gm.application.actor_decisions import ActorDecisionRequest
    from sylvia_gm.domain.authority import (
        CampaignSnapshot,
        JsonValue,
        ModelDecisionBoundary,
        ModelToolBehavior,
        Participant,
        SafePayload,
    )
    from sylvia_gm.domain.session import SessionMessage

    from ._provider_session import _ToolCallAssembly
    from ._stateless_mcp import ModelMechanicsTool


class _UnassignedTurnLocal:
    __slots__ = ()

    def __getattr__(self, name: str) -> Never:
        raise UnboundLocalError(
            f"cannot access local variable '{name}' where it is not associated with a value"
        )


@dataclass(slots=True, repr=False)
class TurnObligations(_UnassignedTurnLocal):
    """Assignments stay at their original exchange or round boundary."""

    # Held narration still needs a Campaign File disposition.
    buffered_narration_needs_disposition: bool = field(init=False)
    # A settled Campaign File still needs its player-facing narration.
    campaign_file_narration_pending: bool = field(init=False)
    # The next model result must settle held Campaign File work.
    force_campaign_file_update: bool = field(init=False)
    # A rejected required call still needs correction in this exchange.
    pending_failed_tool_call: bool = field(init=False)
    # A declared player intention remains unresolved across rounds.
    player_intent_pending: bool = field(init=False)
    # This round must resolve the player's pending intention.
    player_intent_resolution_required: bool = field(init=False)
    # The GM must review the current engine transition before continuing.
    engine_transition_review_pending: bool = field(init=False)
    # Provisional tool prose from this exchange remains unreviewed.
    provisional_tool_prose_pending: bool = field(init=False)


@dataclass(slots=True, repr=False)
class TurnProgress(_UnassignedTurnLocal):
    """Assignments stay at their original exchange or round boundary."""

    # The current disposition is settled; new work can invalidate it.
    campaign_file_disposition_in_exchange: bool = field(init=False)
    # This exchange has committed authoritative work.
    any_commit_in_exchange: bool = field(init=False)
    # This exchange has committed a Campaign File update.
    campaign_file_commit_in_exchange: bool = field(init=False)
    # This tool batch has already submitted a state-changing call.
    state_changing_call_submitted: bool = field(init=False)
    # The current tool batch contains a rejected or failed call.
    round_failed: bool = field(init=False)
    # A saved or current action sequence stopped before completing.
    sequence_execution_stopped: bool = field(init=False)


@dataclass(slots=True, repr=False)
class TurnDecisions(_UnassignedTurnLocal):
    """Assignments stay at their original exchange or round boundary."""

    # This round must finish a Ruleset decision owned by the GM.
    gm_boundary_decision: bool = field(init=False)
    # This round narrates at a Ruleset boundary owned by the player.
    player_boundary_narration: bool = field(init=False)


@dataclass(slots=True, repr=False)
class ProviderTurnState(_UnassignedTurnLocal):
    """Keep shared locals uninitialized until their original assignment executes."""

    obligations: TurnObligations = field(default_factory=TurnObligations)
    progress: TurnProgress = field(default_factory=TurnProgress)
    decisions: TurnDecisions = field(default_factory=TurnDecisions)

    actor_request: ActorDecisionRequest | None = field(init=False)
    aliases: dict[str, ModelMechanicsTool] = field(init=False)
    assemblies: dict[int, _ToolCallAssembly] = field(init=False)
    assembly: _ToolCallAssembly = field(init=False)
    assistant_reasoning: list[str] = field(init=False)
    assistant_text: list[str] = field(init=False)
    basis_message_id: str | None = field(init=False)
    behavior: ModelToolBehavior = field(init=False)
    buffered_narration: str | None = field(init=False)
    buffered_raw_response_blocks: tuple[SafePayload, ...] | None = field(init=False)
    buffered_response_blocks: tuple[SafePayload, ...] | None = field(init=False)
    campaign_file_disposition_supported: bool = field(init=False)
    campaign_file_mutation_attempted: bool = field(init=False)
    campaign_id: str = field(init=False)
    canonical_player_narration: tuple[dict[str, object], ...] = field(init=False)
    catalog_tool: ModelMechanicsTool = field(init=False)
    committed_action_ids: set[str] = field(init=False)
    context_fields: dict[str, JsonValue] = field(init=False)
    context_owner: dict[str, object] | None = field(init=False)
    current_application_controls: list[dict[str, object]] = field(init=False)
    current_catalog: tuple[ModelMechanicsTool, ...] = field(init=False)
    current_player_action: str | None = field(init=False)
    current_player_message: SessionMessage | None = field(init=False)
    current_user_wire_text: str | None = field(init=False)
    decision_boundary: ModelDecisionBoundary | None = field(init=False)
    direct_response: bool = field(init=False)
    discovery_id: str = field(init=False)
    empty_collection_paths: tuple[str, ...] = field(init=False)
    empty_completion_corrections_used: int = field(init=False)
    entity_display_names: dict[str, str] = field(init=False)
    exchange_messages: list[dict[str, object]] = field(init=False)
    facts: dict[str, object] | None = field(init=False)
    finish_reason: str = field(init=False)
    inference_id: str = field(init=False)
    inference_messages: list[dict[str, object]] = field(init=False)
    local_tool_failures: set[str] = field(init=False)
    local_tool_results: dict[str, dict[str, object]] = field(init=False)
    max_empty_completion_corrections: int = field(init=False)
    max_missing_required_tool_corrections: int = field(init=False)
    max_private_deliberation_corrections: int = field(init=False)
    max_rejected_tool_corrections: int = field(init=False)
    max_repeated_response_corrections: int = field(init=False)
    mechanics_action_ids: dict[str, str] = field(init=False)
    mechanics_parameters: dict[str, dict[str, object]] = field(init=False)
    mechanics_pre_entities: dict[str, dict[str, object]] = field(init=False)
    mechanics_preparation_scopes: dict[str, object] = field(init=False)
    mechanics_tool_behaviors: dict[str, ModelToolBehavior] = field(init=False)
    mechanics_tool_names: dict[str, str] = field(init=False)
    messages: list[dict[str, object]] = field(init=False)
    missing: tuple[str, ...] = field(init=False)
    missing_required_tool_corrections_used: int = field(init=False)
    mutation_attempted_before_call: bool = field(init=False)
    narration: str = field(init=False)
    native_assistant_content: list[dict[str, Any]] | None = field(init=False)
    native_chat_metadata: dict[str, Any] | None = field(init=False)
    ooc_ack_missing: bool = field(init=False)
    ooc_ack_required: bool = field(init=False)
    opening_footing: dict[str, object] | None = field(init=False)
    opening_request: bool = field(init=False)
    ordered: list[_ToolCallAssembly] = field(init=False)
    parameters: dict[str, object] = field(init=False)
    parameters_establish_changes: bool = field(init=False)
    participant_ooc_only: bool = field(init=False)
    participants: tuple[Participant, ...] = field(init=False)
    pending_boundary: ModelDecisionBoundary | None = field(init=False)
    pending_narration_facts: list[dict[str, object]] = field(init=False)
    post_commit_tool_required: bool = field(init=False)
    post_mechanics_recovery: bool = field(init=False)
    previous_player_action: str | None = field(init=False)
    previous_player_facing_response: str | None = field(init=False)
    private_deliberation_correction_message: dict[str, object] | None = field(init=False)
    private_deliberation_corrections_used: int = field(init=False)
    provider_round: int = field(init=False)
    provider_tool_call_required: bool = field(init=False)
    provider_tool_schemas: dict[str, dict[str, object]] = field(init=False)
    rejected_tool_corrections_used: int = field(init=False)
    repeated_response_corrections_used: int = field(init=False)
    required_tool_call_correction_message: dict[str, object] | None = field(init=False)
    required_tool_failures: set[str] = field(init=False)
    required_transitions: tuple[ModelMechanicsTool, ...] = field(init=False)
    resumed_messages: list[dict[str, object]] = field(init=False)
    revised_narration: object = field(init=False)
    revised_narration_supplied: bool = field(init=False)
    revised_raw_blocks: tuple[SafePayload, ...] | None = field(init=False)
    round_finish_reason: str | None = field(init=False)
    round_has_non_narrative_call: bool = field(init=False)
    saved_sequence_results: tuple[Mapping[str, object], ...] = field(init=False)
    selected_raw_blocks: tuple[SafePayload, ...] | None = field(init=False)
    sequence_narration_facts: list[dict[str, object]] = field(init=False)
    settled_narration: str | None = field(init=False)
    snapshot: CampaignSnapshot = field(init=False)
    tools: list[dict[str, object]] = field(init=False)
    unsupported_speakers: bool = field(init=False)
    update: Mapping[str, object] = field(init=False)
