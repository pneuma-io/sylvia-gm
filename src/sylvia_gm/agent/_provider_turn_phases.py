"""Phases of a GM provider turn; the adapter retains escaping loop control."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Any

from ._provider_turn_decisions import (
    _buffered_narration_can_be_replaced,
    _campaign_file_must_be_settled,
    _campaign_file_needs_narration,
    _campaign_file_requires_atomic_completion,
    _committed_tool_resolves_player_boundary,
    _committed_work_needs_campaign_file_disposition,
    _engine_transition_needs_review,
    _exchange_has_committed_work,
    _held_disposition_must_be_retried,
    _opening_needs_campaign_file_update,
    _player_intent_remains_pending,
    _provisional_tool_prose_needs_review,
    _round_allows_history_tool,
    _round_is_at_player_boundary,
    _round_requires_tool_call,
    _saved_gm_boundary_should_be_read,
    _settled_disposition_still_owes_ooc_acknowledgement,
    _tool_call_needs_fresh_catalog,
)
from ._provider_turn_state import ProviderTurnState
from ._session_loop import ProviderEvent, SessionProviderInput

if TYPE_CHECKING:
    from sylvia_gm.application.actor_decisions import ActorDecisionRequest

    from ._provider_session import ProviderSessionAdapter

import hashlib
import json
import uuid
from collections.abc import Mapping, Sequence
from typing import cast

from sylvia_gm.agent._native_continuation import attach_chat_metadata
from sylvia_gm.agent._session_loop import ProviderActionFeedback
from sylvia_gm.agent._stateless_mcp import (
    _safe_error,
    action_sequence_model_tools,
    call_action_sequence_tool,
)
from sylvia_gm.application.authority import ActionCommand
from sylvia_gm.domain.authority import (
    CanonicalEventType,
    JsonValue,
    ModelDecisionOwner,
    ModelToolBehavior,
    SafePayload,
    WriterRole,
    validate_response_blocks,
)
from sylvia_gm.domain.errors import AuthorityError, CommandRejected, MechanicsRejected
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.provider._tool_delta import parse_tool_call_delta

from ._provider_session import (
    _CAMPAIGN_FILE_REVISED_NARRATION_ARGUMENT,
    _GM_SYSTEM_CONTRACT_VERSION,
    _PREPARATION_INTENT_ARGUMENT,
    _UNSPECIFIED_PREPARATION_INTENT,
    _action_sequence_narration_facts,
    _assistant_draft_message,
    _campaign_file_arguments_establish_changes,
    _campaign_file_disposition_message,
    _campaign_file_empty_collection_paths,
    _campaign_file_updated_narration_message,
    _campaign_preparation_message,
    _canonical_player_narration,
    _contains_serialized_tool_call,
    _current_combat_state,
    _current_referenced_entities,
    _derive_mechanics_parameters,
    _engine_turn_control_message,
    _forced_single_tool_call_from_json,
    _gm_decision_control_message,
    _mechanics_narration_facts,
    _missing_required_arguments,
    _native_content_for_text_operation,
    _normalize_state_references,
    _persisted_campaign_file_mutation_attempted,
    _persisted_selected_party_roles,
    _plain_json,
    _player_boundary_narration_message,
    _player_turn_text,
    _preparation_keeps_intent_pending,
    _project_response_blocks,
    _public_action_result,
    _require_checked_campaign_file_narration,
    _resolve_provider_tool_alias,
    _saved_action_sequence_results,
    _ToolCallAssembly,
    _unsupported_response_speaker_ids,
    _validated_raw_response_blocks,
)


def execute_required_transition(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> Iterable[ProviderEvent]:
    if len(state.required_transitions) != 1:
        raise CommandRejected(
            "Ruleset exposed more than one automatic engine transition",
            code="ambiguous_engine_transition",
        )
    transition = state.required_transitions[0]
    properties = transition.input_schema.get("properties", {})
    required = transition.input_schema.get("required", ())
    # A package may expose optional context or optional overrides on
    # its automatic lifecycle tool. The owner executes the admitted
    # empty command; it never supplies or guesses those arguments.
    if not isinstance(properties, Mapping) or required:
        raise CommandRejected(
            "Automatic engine transitions cannot require model judgment",
            code="invalid_engine_transition_surface",
        )
    before = self._owner.snapshot(state.campaign_id)
    action_id = f"action-{uuid.uuid4().hex}"
    feedback = ProviderActionFeedback()
    recorder = getattr(self._owner, "record_admin_full_audit", None)
    if callable(recorder):
        recorder(
            {
                "kind": "automatic_engine_transition_requested",
                "campaign_id": state.campaign_id,
                "session_id": provider_input.session.session_id,
                "turn_id": turn_id,
                "action_id": action_id,
                "mcp_discovery_id": state.discovery_id,
                "tool_name": transition.name,
                "basis_revision": before.revision,
            }
        )
    yield ProviderEvent.action(
        ActionCommand(
            campaign_id=state.campaign_id,
            action_id=action_id,
            idempotency_key=f"engine-transition-{uuid.uuid4().hex}",
            owner_id=self._owner_id,
            writer=provider_input.session.role,
            expected_revision=before.revision,
            turn_id=turn_id,
            package_id=transition.package_id,
            tool_name=transition.name,
            parameters=cast(Mapping[str, JsonValue], {}),
            initiating_session_id=provider_input.session.session_id,
        ),
        feedback=feedback,
    )
    if feedback.rejection is not None:
        raise CommandRejected(
            "Automatic engine transition was rejected",
            code=str(feedback.rejection.get("code", "engine_transition_rejected")),
        )
    after = self._owner.snapshot(state.campaign_id)
    terminal_event = next(
        (
            event
            for event in reversed(after.events)
            if event.action_id == action_id
            and event.event_type
            in {
                CanonicalEventType.ACTION_COMMITTED,
                CanonicalEventType.ACTION_FAILED,
            }
        ),
        None,
    )
    tool_result_event = next(
        (
            event
            for event in reversed(after.events)
            if event.action_id == action_id and event.event_type is CanonicalEventType.TOOL_RESULT
        ),
        None,
    )
    if terminal_event is None or tool_result_event is None:
        raise CommandRejected("Canonical engine transition result is unavailable")
    if terminal_event.event_type is CanonicalEventType.ACTION_FAILED:
        raise CommandRejected(
            "Automatic engine transition failed",
            code="engine_transition_failed",
        )
    result_payload = _plain_json(tool_result_event.safe_payload.get("result", {}))
    combat = _current_combat_state(after.state)
    current_actor = combat.get("active_actor", combat.get("current_actor"))
    entity_parameters = (
        {"actor": current_actor} if isinstance(current_actor, str) and current_actor else {}
    )
    current_entities = _current_referenced_entities(
        entity_parameters,
        after.state,
    )
    state.facts = _mechanics_narration_facts(
        transition.name,
        result_payload,
        current_entities,
        {},
        {},
        combat,
        state.entity_display_names,
    )
    state.pending_narration_facts.append(state.facts)
    state.progress.any_commit_in_exchange = True
    if provider_input.session.role is WriterRole.DM:
        state.progress.campaign_file_disposition_in_exchange = False
    transition_result = {
        "status": terminal_event.event_type.value,
        "campaign_revision": after.revision,
        "tool": transition.name,
        "result": result_payload,
        "current_entities": current_entities,
    }
    state.messages.append(
        {
            "role": "system",
            "content": (
                "SYLVIA_GM_AUTOMATIC_ENGINE_TRANSITION_RESULT "
                "(authoritative application result; no model chose or called this "
                "bookkeeping operation):\n"
                + json.dumps(
                    transition_result,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                )
            ),
        }
    )
    if callable(recorder):
        recorder(
            {
                "kind": "automatic_engine_transition_completed",
                "campaign_id": state.campaign_id,
                "session_id": provider_input.session.session_id,
                "turn_id": turn_id,
                "action_id": action_id,
                "mcp_discovery_id": state.discovery_id,
                "tool_name": transition.name,
                "resulting_revision": after.revision,
                "result": transition_result,
            }
        )


def run_required_actor_decision(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> Iterable[ProviderEvent]:
    from sylvia_gm.agent._actor_decisions import run_actor_decision

    public_result = yield from run_actor_decision(
        self._owner,
        self._provider,
        provider_input,
        cast("ActorDecisionRequest", state.actor_request),
        owner_id=self._owner_id,
        stream=self._stream_round,
    )
    yield ProviderEvent.tool_evidence(
        message_id="participant-result-"
        + cast("ActorDecisionRequest", state.actor_request).decision_id,
        payload={
            "kind": "isolated_participant_result",
            "turn_id": turn_id,
            "basis_message_id": state.basis_message_id,
            "inference_id": cast("ActorDecisionRequest", state.actor_request).decision_id,
            "public_result": cast(JsonValue, public_result),
        },
    )
    state.messages.append(self._participant_result_message(public_result))
    state.progress.any_commit_in_exchange = True


def retry_empty_completion(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> None:
    state.empty_completion_corrections_used += 1
    required_output = (
        "A required tool call was expected. Return one complete call to an "
        "exposed tool. Do not return prose without that call."
        if state.provider_tool_call_required
        else (
            "Return one complete in-world response, or call an exposed tool "
            "if the unresolved current action genuinely needs it."
        )
    )
    empty_completion_correction: dict[str, object] = {
        "role": "system",
        "content": (
            "SYLVIA_GM_EMPTY_COMPLETION_CORRECTION: Your preceding "
            f"response completed with finish_reason='{state.finish_reason}' but "
            "contained no text, private reasoning, or complete tool call. "
            "Continue the same turn from the supplied context; do not "
            "restart the scene or repeat an already accepted operation. "
            f"{required_output} This is empty-completion correction "
            f"{state.empty_completion_corrections_used} of "
            f"{state.max_empty_completion_corrections}."
        ),
    }
    state.current_application_controls.append(empty_completion_correction)


def record_empty_completion(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> Iterable[ProviderEvent]:
    state.finish_reason = state.round_finish_reason or "stream_ended_without_finish_reason"
    next_correction = state.empty_completion_corrections_used + 1
    yield ProviderEvent.tool_evidence(
        message_id=f"{state.inference_id}-empty-completion",
        payload={
            "kind": "provider_empty_completion",
            "turn_id": turn_id,
            "inference_id": state.inference_id,
            "basis_message_id": state.basis_message_id,
            "finish_reason": state.finish_reason,
            "tool_call_required": state.provider_tool_call_required,
            "correction_attempt": next_correction,
            "max_corrections": state.max_empty_completion_corrections,
        },
    )


def retry_repeated_response(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> Iterable[ProviderEvent]:
    state.repeated_response_corrections_used += 1
    yield ProviderEvent.tool_evidence(
        message_id=(
            f"{state.inference_id}-repeated-response-{state.repeated_response_corrections_used}"
        ),
        payload={
            "kind": "provider_repeated_player_facing_response",
            "turn_id": turn_id,
            "inference_id": state.inference_id,
            "basis_message_id": state.basis_message_id,
            "response_sha256": hashlib.sha256(state.narration.encode("utf-8")).hexdigest(),
            "response_characters": len(state.narration),
            "correction_attempt": state.repeated_response_corrections_used,
            "max_corrections": state.max_repeated_response_corrections,
        },
    )
    state.exchange_messages.append({"role": "assistant", "content": state.narration})
    state.current_application_controls.append(
        {
            "role": "system",
            "content": (
                "SYLVIA_GM_CURRENT_ACTION_RESPONSE_CORRECTION "
                "(application-owned control, not player speech): The preceding "
                "completion exactly repeated the immediately prior "
                "player-facing response even though the player's current "
                "action is different. It has been discarded and will never be "
                "shown to the player. Resolve the current action from the "
                "authoritative context already supplied. Call an exposed "
                "operation if resolution genuinely requires one; otherwise "
                "return one complete in-world response. Do not repeat or "
                "continue the prior answer. This is correction "
                f"{state.repeated_response_corrections_used} of "
                f"{state.max_repeated_response_corrections}."
            ),
        }
    )


def retry_missing_tool_call(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> Iterable[ProviderEvent]:
    state.missing_required_tool_corrections_used += 1
    yield ProviderEvent.tool_evidence(
        message_id=f"{state.inference_id}-forced-tool-call-missing",
        payload={
            "kind": "provider_forced_tool_call_missing",
            "turn_id": turn_id,
            "inference_id": state.inference_id,
            "basis_message_id": state.basis_message_id,
            "correction_attempt": state.missing_required_tool_corrections_used,
            "max_corrections": state.max_missing_required_tool_corrections,
        },
    )
    state.required_tool_call_correction_message = {
        "role": "system",
        "content": (
            "SYLVIA_GM_REQUIRED_TOOL_CALL_CORRECTION "
            "(application-owned control, not player speech): The provider "
            "returned prose where this round required a tool call. That prose "
            "has been discarded, is not current fiction, and will never be "
            "shown or persisted. The exact unresolved player action remains "
            "the latest User message. Choose one operation from the complete "
            "tool surface still exposed. You may read a source, use a Ruleset "
            "operation, or call the Campaign File disposition directly when "
            "no mechanic is needed. Do not repeat or continue the discarded "
            "prose. This is correction "
            f"{state.missing_required_tool_corrections_used} of "
            f"{state.max_missing_required_tool_corrections}."
        ),
    }


def retry_private_deliberation(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> None:
    state.private_deliberation_corrections_used += 1
    state.exchange_messages.append({"role": "assistant", "content": state.narration})
    state.private_deliberation_correction_message = {
        "role": "system",
        "content": (
            "SYLVIA_GM_PRIVATE_DELIBERATION_CORRECTION "
            "(application-owned control, not player speech): The preceding "
            "assistant content is private GM planning, not player-facing "
            "narration. It has been discarded and will never be shown to the "
            "player or persisted as dialogue. Continue as the same GM from "
            "the current action and all authoritative results already above. "
            "If another exposed operation is genuinely required, call it "
            "without prose. Otherwise return only the complete in-world "
            "response, beginning with what the player experiences and "
            "containing no analysis, alternatives, check summary, statement "
            "of intent, or workflow note. This is correction "
            f"{state.private_deliberation_corrections_used} of "
            f"{state.max_private_deliberation_corrections}."
        ),
    }


def record_held_narration(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> Iterable[ProviderEvent]:
    yield ProviderEvent.tool_evidence(
        message_id=f"{state.inference_id}-final-response-reference",
        payload={
            "kind": "provider_final_response_reference",
            **state.context_fields,
            "response_id": state.inference_id,
            "basis_message_id": state.basis_message_id,
            "requirement": {
                "provider_id": provider_input.session.provider_id,
                "model_id": provider_input.session.model_id,
                "reasoning_required": bool(state.assistant_reasoning),
                "chat_completion_required": state.native_chat_metadata is not None,
            },
        },
    )
    yield ProviderEvent.talk(state.narration)


def retry_missing_campaign_file_update(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> Iterable[ProviderEvent]:
    state.missing_required_tool_corrections_used += 1
    yield ProviderEvent.tool_evidence(
        message_id=f"{state.inference_id}-forced-tool-call-missing",
        payload={
            "kind": "provider_forced_tool_call_missing",
            "turn_id": turn_id,
            "inference_id": state.inference_id,
            "basis_message_id": state.basis_message_id,
            "correction_attempt": state.missing_required_tool_corrections_used,
            "max_corrections": state.max_missing_required_tool_corrections,
        },
    )
    state.current_application_controls.append(
        {
            "role": "system",
            "content": (
                (
                    "SYLVIA_GM_CAMPAIGN_FILE_DISPOSITION_CORRECTION "
                    "(application-owned control, not player speech): Your "
                    "preceding completion did not call the required Campaign "
                    "File tool. That completion is discarded and will never be "
                    "shown to the player. Do not rewrite or continue the held "
                    "player-facing response. Return only one call to the "
                    "exposed Campaign File tool. Declare state_change as "
                    "'changed' and supply every lasting change, or declare it "
                    "'unchanged' and omit state collections only when the held "
                    "response changes no durable truth. The held response is "
                    "already selected. Do not repeat it in "
                    "the tool arguments. "
                    if (
                        state.buffered_narration is not None
                        or state.buffered_raw_response_blocks is not None
                    )
                    else "SYLVIA_GM_CAMPAIGN_FILE_UPDATE_CORRECTION "
                    "(application-owned control, not player speech): The prior "
                    "Campaign File call was rejected, and your preceding prose "
                    "completion is discarded and will never be shown to the "
                    "player. Return only one corrected call to the exposed "
                    "Campaign File update tool. Do not narrate until that call "
                    "succeeds. "
                )
                + "This is correction "
                f"{state.missing_required_tool_corrections_used} of "
                f"{state.max_missing_required_tool_corrections}."
            ),
        }
    )


def preserve_boundary_narration(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> Iterable[ProviderEvent]:
    draft_message = _assistant_draft_message(state.narration)
    state.exchange_messages.append(draft_message)
    yield ProviderEvent.tool_evidence(
        message_id=f"{state.inference_id}-gm-boundary-draft",
        payload={
            "kind": "provider_gm_boundary_draft",
            **state.context_fields,
            "turn_id": turn_id,
            "inference_id": state.inference_id,
            "basis_message_id": state.basis_message_id,
            "assistant": cast(SafePayload, _plain_json(draft_message)),
        },
    )
    state.current_application_controls.append(
        {
            "role": "system",
            "content": (
                "SYLVIA_GM_RULESET_DECISION_STILL_ACTIVE "
                "(application-owned control, not player speech): The preceding "
                "prose is held and has not been shown. A GM-controlled Ruleset "
                "decision is still active, so Campaign File disposition is not "
                "available yet. Continue from the fresh decision request. Call "
                "one currently exposed Ruleset operation that represents what "
                "this actor does next; if the actor is finished, call the exposed "
                "end-turn operation. Do not repeat a removed operation."
            ),
        }
    )


def preserve_player_intent_narration(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> Iterable[ProviderEvent]:
    draft_message = _assistant_draft_message(state.narration)
    state.exchange_messages.append(draft_message)
    yield ProviderEvent.tool_evidence(
        message_id=f"{state.inference_id}-player-intent-pending-draft",
        payload={
            "kind": "provider_player_intent_pending_draft",
            **state.context_fields,
            "turn_id": turn_id,
            "inference_id": state.inference_id,
            "basis_message_id": state.basis_message_id,
            "assistant": cast(SafePayload, _plain_json(draft_message)),
        },
    )
    state.current_application_controls.append(
        {
            "role": "system",
            "content": (
                "SYLVIA_GM_PLAYER_INTENT_STILL_PENDING "
                "(application-owned control, not player speech): The preceding "
                "prose is held and has not been shown. The Ruleset says the "
                "player's declared action is still unresolved. Call one exposed "
                "Ruleset resolver for that exact action. Do not ask the player "
                "to declare it again, choose a replacement action, end the turn, "
                "or update the Campaign File first."
            ),
        }
    )


def retain_campaign_file_narration(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> Iterable[ProviderEvent]:
    state.buffered_narration = state.narration
    state.buffered_response_blocks = None
    state.buffered_raw_response_blocks = None
    state.obligations.buffered_narration_needs_disposition = True
    state.progress.campaign_file_disposition_in_exchange = False
    state.obligations.force_campaign_file_update = True
    draft_message = _assistant_draft_message(state.narration)
    state.exchange_messages.append(draft_message)
    yield ProviderEvent.tool_evidence(
        message_id=f"{state.inference_id}-held-narration",
        payload={
            "kind": "provider_held_narration",
            **state.context_fields,
            "turn_id": turn_id,
            "inference_id": state.inference_id,
            "basis_message_id": state.basis_message_id,
            "assistant": cast(SafePayload, _plain_json(draft_message)),
        },
    )


def record_final_narration(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> Iterable[ProviderEvent]:
    yield ProviderEvent.tool_evidence(
        message_id=f"{state.inference_id}-final-response-reference",
        payload={
            "kind": "provider_final_response_reference",
            **state.context_fields,
            "response_id": state.inference_id,
            "basis_message_id": state.basis_message_id,
            "requirement": {
                "provider_id": provider_input.session.provider_id,
                "model_id": provider_input.session.model_id,
                "reasoning_required": bool(state.assistant_reasoning),
                "chat_completion_required": state.native_chat_metadata is not None,
            },
        },
    )
    yield ProviderEvent.talk(state.narration)


def reject_unavailable_tool(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> None:
    state.local_tool_results[state.assembly.call_id] = {
        "status": "rejected",
        "code": "unavailable_catalog_tool",
        "state_changed": False,
        "requested_provider_tool_name": state.assembly.name,
        "available_operations": [
            {
                "provider_tool_name": alias,
                "operation": tool.name,
            }
            for alias, tool in state.aliases.items()
        ],
        "instruction": (
            "This provider tool is absent from the current request and cannot "
            "be called. Do not call it again in this correction. Choose one "
            "exact provider_tool_name from available_operations only when its "
            "operation fits the current Ruleset state. If an engine transition "
            "is marked required, call that transition first."
        ),
    }
    state.local_tool_failures.add(state.assembly.call_id)


def reject_invalid_tool_json(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
    error: Any,
) -> None:
    state.local_tool_results[state.assembly.call_id] = {
        "status": "rejected",
        "code": "provider_tool_arguments_invalid_json",
        "state_changed": False,
        "provider_response_finish_reason": (
            state.round_finish_reason or "stream_ended_without_finish_reason"
        ),
        "received_argument_characters": len(state.assembly.arguments),
        "json_error": {
            "message": error.msg,
            "line": error.lineno,
            "column": error.colno,
            "position": error.pos,
        },
        "instruction": (
            "The streamed tool arguments were not a complete JSON object. Call "
            "the same tool again with the full valid JSON object from its first "
            "character; do not continue the partial text."
        ),
    }
    state.local_tool_failures.add(state.assembly.call_id)


def record_missing_tool_fields(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> None:
    if state.catalog_tool.category == "narrative_state":
        diagnostic_recorder = getattr(
            self._owner,
            "record_mechanics_diagnostic",
            None,
        )
        if callable(diagnostic_recorder):
            diagnostic_recorder(
                session_id=provider_input.session.session_id,
                turn_id=turn_id,
                action_id=state.assembly.call_id,
                tool_name=state.catalog_tool.name,
                rejection_code="missing_required_arguments",
                validation_field_paths=state.missing,
                state_changed=False,
            )
    state.local_tool_results[state.assembly.call_id] = {
        "status": "rejected",
        "code": "missing_required_arguments",
        "missing": list(state.missing),
        "instruction": "Retry this tool with every required argument.",
    }
    state.local_tool_failures.add(state.assembly.call_id)


def execute_action_sequence_call(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> None:
    state.progress.state_changing_call_submitted = (
        state.progress.state_changing_call_submitted or state.catalog_tool.category != "reference"
    )
    try:
        result = call_action_sequence_tool(
            self._owner,
            campaign_id=state.campaign_id,
            session_id=provider_input.session.session_id,
            name=state.catalog_tool.name,
            parameters=state.parameters,
        )
        state.local_tool_results[state.assembly.call_id] = result
        if state.catalog_tool.name == "execute_action_sequence":
            saved = result.get("result")
            if not isinstance(saved, Mapping):
                raise CommandRejected("Action sequence result is unavailable")
            state.facts = _action_sequence_narration_facts(self._owner, state.campaign_id, saved)
            if state.facts is not None:
                state.sequence_narration_facts.append(state.facts)
            if saved.get("status") in {"paused", "failed", "stale"}:
                state.progress.sequence_execution_stopped = True
                state.current_application_controls.append(
                    {
                        "role": "system",
                        "content": (
                            "SYLVIA_ACTION_SEQUENCE_STOP "
                            "(trusted application instruction): "
                            "The executable sequence stopped at an authoritative boundary. "
                            "Preserve its saved committed action IDs "
                            "and pending intentions. "
                            "Do not execute, replace, or resume the remaining "
                            "intentions in this "
                            "exchange. Resolve only a currently required decision/Ruleset "
                            "consequence; describe committed outcomes together through the "
                            "ordinary final Campaign File disposition. Wait for "
                            "fresh player "
                            "direction before a further sequence execution."
                        ),
                    }
                )
    except AuthorityError as error:
        feedback = cast(dict[str, object], _safe_error(error).structured_content)
        code = str(getattr(error, "code", "authority_rejected"))
        if state.catalog_tool.name == "execute_action_sequence":
            # The service may have persisted a prefix before its reply failed.
            # Recover receipts by reading; never retry execution implicitly.
            state.progress.sequence_execution_stopped = True
            saved = cast(Callable[..., object], self._owner.action_sequence)(
                session_id=provider_input.session.session_id,
                sequence_id=state.parameters.get("sequence_id"),
            )
            state.local_tool_results[state.assembly.call_id] = {
                "kind": "action_sequence_result",
                "operation": state.catalog_tool.name,
                "result": _plain_json(saved),
                "execution_stopped": True,
                "delivery_error": {"code": code, "reason": feedback["reason"]},
            }
            if isinstance(saved, Mapping):
                state.facts = _action_sequence_narration_facts(
                    self._owner, state.campaign_id, saved
                )
                if state.facts is not None:
                    state.sequence_narration_facts.append(state.facts)
            state.current_application_controls.append(
                {
                    "role": "system",
                    "content": (
                        "SYLVIA_ACTION_SEQUENCE_STOP "
                        "(trusted application instruction): "
                        "Execution did not return a confirmed reply. The saved queue and "
                        "committed receipts were recovered by reading. Do not submit "
                        "execution again in this exchange, infer an uncommitted outcome, "
                        "or spend costs again. Describe only the saved outcomes, preserve "
                        "remaining intentions, and wait for fresh player direction."
                    ),
                }
            )
        else:
            state.local_tool_results[state.assembly.call_id] = {
                "status": "rejected",
                "code": code,
                "reason": feedback["reason"],
                "validation": feedback["validation"],
                "state_changed": False,
            }
            state.local_tool_failures.add(state.assembly.call_id)


def reject_invalid_revision(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> None:
    state.local_tool_results[state.assembly.call_id] = {
        "status": "rejected",
        "code": "campaign_file_revised_narration_invalid",
        "field_paths": [
            _CAMPAIGN_FILE_REVISED_NARRATION_ARGUMENT,
        ],
        "state_changed": False,
        "instruction": (
            "revised_player_facing_response must be one complete non-empty "
            "ordered response-block list with no serialized tool call. "
            "npc_utterance blocks require speaker_id and spoken_words; "
            "speaker_id must be anonymous, an exact current participant or "
            "Campaign Entity ID, or an entity_id admitted by this same update; "
            "narration and ooc "
            "blocks use text. Put actions and attribution in separate "
            "narration blocks; start a new utterance when that NPC resumes "
            "speaking. OOC input requires an ooc acknowledgement "
            "block. Correct the complete Campaign File disposition and retry."
        ),
    }
    state.local_tool_failures.add(state.assembly.call_id)


def record_campaign_file_rejection(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
    error: Any,
) -> None:
    rejection: dict[str, object] = {
        "status": "rejected",
        "code": error.code,
    }
    if isinstance(error, MechanicsRejected):
        rejection.update(
            {
                "field_paths": list(error.field_paths),
                "validation": {
                    "field_paths": list(error.field_paths),
                    "issues": [issue.as_payload() for issue in error.validation_issues],
                },
                "state_changed": error.state_changed,
            }
        )
    if error.code in {
        "campaign_file_update_invalid",
        "campaign_file_disposition_inconsistent",
    }:
        rejection["instruction"] = (
            "Rebuild the rejected state arguments against the currently "
            "exposed tool schema. validation.issues identifies each path, "
            "problem kind, "
            "expected type, direct parent, and allowed values or size bound "
            "when the schema declares one. It never echoes submitted values. "
            "Correct every listed issue rather than patching one symptom. "
            "The state_change declaration and supplied non-empty state "
            "collections must agree."
            + (
                " Sylvia already holds the exact player-facing response from "
                "this rejected call. If its layout needs correction, supply a "
                "complete replacement in revised_player_facing_response, "
                "preserving the decided outcome and separating narration from "
                "spoken words. Otherwise omit that field to keep the held "
                "response."
                if (
                    state.buffered_narration is not None
                    or state.buffered_raw_response_blocks is not None
                )
                else ""
            )
        )
    state.local_tool_results[state.assembly.call_id] = rejection
    state.local_tool_failures.add(state.assembly.call_id)


def restore_unchanged_narration(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> None:
    state.campaign_file_mutation_attempted = state.mutation_attempted_before_call
    state.local_tool_results[state.assembly.call_id] = {
        "status": "rejected",
        "code": "campaign_file_duplicate_collections",
        "state_changed": False,
        "instruction": (
            "Campaign File authority found every supplied collection to "
            "be already current, so those collections must be omitted. "
            "Review the complete held response before retrying. If it "
            "introduces any new lasting clue, direction, discovery, "
            "relationship, commitment, or scene change, submit those "
            "actual changes. Otherwise remove unsupported new material "
            "from the response and send only the complete concise "
            "response with no state collections. Do not resubmit an "
            "unchanged scene merely because the prose mentions it."
        ),
    }
    state.local_tool_failures.add(state.assembly.call_id)


def record_unchanged_campaign_file(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> None:
    state.local_tool_results[state.assembly.call_id] = {
        "status": "campaign_file_unchanged",
        "result": dict(state.update),
        "player_facing_response": state.settled_narration,
        **(
            {"response_blocks": list(state.buffered_response_blocks)}
            if state.buffered_response_blocks is not None
            else {}
        ),
        "instruction": (
            "Campaign File review is settled without a database write. Sylvia "
            "will now deliver the settled player-facing response."
            if state.settled_narration is not None
            else "Campaign File review is settled without a database write. "
            "Now return only the complete in-world GM response for the "
            "reviewed outcome."
        ),
    }
    state.progress.campaign_file_disposition_in_exchange = True
    if state.settled_narration is not None:
        state.buffered_narration = state.settled_narration
        state.obligations.buffered_narration_needs_disposition = False
        state.obligations.campaign_file_narration_pending = False
    else:
        state.obligations.campaign_file_narration_pending = True
    state.obligations.force_campaign_file_update = False


def prepare_campaign_file_revision(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> None:
    state.revised_narration_supplied = _CAMPAIGN_FILE_REVISED_NARRATION_ARGUMENT in state.parameters
    state.revised_narration = state.parameters.pop(
        _CAMPAIGN_FILE_REVISED_NARRATION_ARGUMENT,
        None,
    )
    state.revised_raw_blocks = None
    if isinstance(state.revised_narration, list):
        try:
            state.revised_raw_blocks = _validated_raw_response_blocks(state.revised_narration)
            state.revised_narration = ""
        except ValueError:
            state.revised_raw_blocks = None
            state.revised_narration = None
    state.selected_raw_blocks = (
        state.revised_raw_blocks
        if state.revised_narration_supplied
        else state.buffered_raw_response_blocks
    )
    pending_reader = getattr(self._owner, "campaign_preparation_boundary", None)
    state.pending_boundary = pending_reader(state.campaign_id) if callable(pending_reader) else None


def validate_revision_speakers(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> None:
    state.ooc_ack_missing = state.ooc_ack_required and not (
        state.selected_raw_blocks is not None
        and any(block.get("kind") == "ooc" for block in state.selected_raw_blocks)
    )
    if state.ooc_ack_missing:
        state.revised_narration = None
    state.unsupported_speakers = state.selected_raw_blocks is not None and bool(
        _unsupported_response_speaker_ids(
            state.selected_raw_blocks,
            self._owner.snapshot(state.campaign_id),
            state.parameters,
        )
    )


def settle_campaign_file_narration(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> None:
    state.settled_narration = state.buffered_narration
    if state.revised_raw_blocks is None and isinstance(state.revised_narration, str):
        state.settled_narration = state.revised_narration
    if isinstance(state.settled_narration, str):
        # Keep the prose decision while a malformed state payload is corrected.
        # Omission in a later tool call means these exact bytes remain selected.
        state.buffered_narration = state.settled_narration
        if state.revised_narration_supplied:
            state.buffered_response_blocks = None
            state.buffered_raw_response_blocks = state.revised_raw_blocks
        state.obligations.buffered_narration_needs_disposition = True
    elif state.revised_raw_blocks is not None:
        state.buffered_raw_response_blocks = state.revised_raw_blocks
        state.buffered_response_blocks = None
        state.obligations.buffered_narration_needs_disposition = True
    state.parameters_establish_changes = _campaign_file_arguments_establish_changes(
        state.parameters
    )
    state.empty_collection_paths = _campaign_file_empty_collection_paths(state.parameters)


def record_updated_campaign_file(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> None:
    state.local_tool_results[state.assembly.call_id] = {
        "status": "campaign_file_updated",
        "result": dict(state.update),
        "player_facing_response": state.settled_narration,
        **(
            {"response_blocks": list(state.buffered_response_blocks)}
            if state.buffered_response_blocks is not None
            else {}
        ),
        "instruction": (
            "Campaign File review is settled at this admitted revision. Sylvia "
            "will now deliver the settled player-facing response."
            if state.settled_narration is not None
            else "Campaign File update is settled at this admitted revision. Now "
            "return only the complete in-world GM response for the admitted "
            "outcome."
        ),
    }
    state.progress.any_commit_in_exchange = True
    state.progress.campaign_file_commit_in_exchange = True
    state.progress.campaign_file_disposition_in_exchange = True
    if state.settled_narration is not None:
        state.buffered_narration = state.settled_narration
        state.obligations.buffered_narration_needs_disposition = False
        state.obligations.campaign_file_narration_pending = False
    else:
        state.obligations.campaign_file_narration_pending = True
    state.obligations.force_campaign_file_update = False


def submit_mechanics_call(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> Iterable[ProviderEvent]:
    action_id = f"action-{uuid.uuid4().hex}"
    command = ActionCommand(
        campaign_id=state.campaign_id,
        action_id=action_id,
        idempotency_key=f"provider-{uuid.uuid4().hex}",
        owner_id=self._owner_id,
        writer=provider_input.session.role,
        expected_revision=state.snapshot.revision,
        turn_id=turn_id,
        package_id=state.catalog_tool.package_id,
        tool_name=state.catalog_tool.name,
        parameters=cast(Mapping[str, JsonValue], state.parameters),
        initiating_session_id=provider_input.session.session_id,
    )
    state.mechanics_action_ids[state.assembly.call_id] = action_id
    state.mechanics_parameters[state.assembly.call_id] = state.parameters
    state.mechanics_pre_entities[state.assembly.call_id] = _current_referenced_entities(
        state.parameters, state.snapshot.state
    )
    state.progress.state_changing_call_submitted = True
    feedback = ProviderActionFeedback()
    yield ProviderEvent.tool_evidence(
        message_id=f"{state.inference_id}-{state.assembly.call_id}-mechanics-contract",
        payload={
            "kind": "provider_mechanics_action_contract",
            "turn_id": turn_id,
            "inference_id": state.inference_id,
            "basis_message_id": state.basis_message_id,
            "action_id": action_id,
            "operation": state.catalog_tool.name,
            "model_behavior": state.catalog_tool.model_behavior.value,
            **(
                {
                    _PREPARATION_INTENT_ARGUMENT: cast(
                        JsonValue, state.mechanics_preparation_scopes[state.assembly.call_id]
                    )
                }
                if state.assembly.call_id in state.mechanics_preparation_scopes
                else {}
            ),
            "decision_owner": (
                state.decision_boundary.owner.value if state.decision_boundary is not None else None
            ),
        },
    )
    yield ProviderEvent.action(command, feedback=feedback)
    if feedback.rejection is not None:
        state.local_tool_results[state.assembly.call_id] = dict(feedback.rejection)
        state.local_tool_failures.add(state.assembly.call_id)


def retry_rejected_tool_call(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> None:
    state.rejected_tool_corrections_used += 1
    state.current_application_controls.append(
        {
            "role": "system",
            "content": (
                "SYLVIA_GM_TOOL_CORRECTION: One or more preceding calls were "
                "rejected. Read each structured tool result, preserve every "
                "successful result, and address only the rejected operation. That "
                "may require corrected arguments or a different currently exposed "
                "operation when the result says the original is unavailable or "
                "unsuitable. Do not "
                "narrate an uncommitted outcome. Correct every reported issue "
                "against the currently exposed schema instead of patching only "
                "one symptom. "
                + (
                    "Sylvia already holds the exact player-facing response. Do not "
                    "repeat it in the corrected tool arguments. "
                    if (
                        state.buffered_narration is not None
                        or state.buffered_raw_response_blocks is not None
                    )
                    else ""
                )
                + "This is correction "
                f"{state.rejected_tool_corrections_used} of "
                f"{state.max_rejected_tool_corrections}."
            ),
        }
    )


def prepare_round_catalog(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> None:
    if provider_input.control.cancelled:
        raise CommandRejected("Agent session provider work was cancelled")
    state.inference_id = f"inference-{uuid.uuid4().hex}"
    state.discovery_id = f"mcp-discovery-{uuid.uuid4().hex}"
    state.current_catalog = provider_input.current_catalog(state.discovery_id)
    if provider_input.session.role is WriterRole.DM:
        queue_tools = action_sequence_model_tools(self._owner)
        if queue_tools and self._owner.snapshot(state.campaign_id).package_lock.entries:
            state.current_catalog = (
                *state.current_catalog,
                *(
                    tool
                    for tool in queue_tools
                    if not state.progress.sequence_execution_stopped or tool.category == "reference"
                ),
            )
    if state.participant_ooc_only:
        state.current_catalog = tuple(
            tool
            for tool in state.current_catalog
            if tool.category in {"reference", "narrative_state"}
        )
    if provider_input.control.cancelled:
        raise CommandRejected("Agent session provider work was cancelled")
    state.required_transitions = tuple(
        tool
        for tool in state.current_catalog
        if tool.model_behavior is ModelToolBehavior.REQUIRED_ENGINE_TRANSITION
    )
    if (
        state.required_transitions
        and state.provider_round == 0
        and state.current_player_message is not None
        and any(tool.name == "end_combat_encounter" for tool in state.current_catalog)
    ):
        combat_at_input = _current_combat_state(self._owner.snapshot(state.campaign_id).state)
        # A new instruction can end the fight at the just-resolved NPC action.
        # Read it before finishing that turn or advancing to a Death Save.
        state.obligations.engine_transition_review_pending = (
            combat_at_input.get("turn_active") is True
            and combat_at_input.get("active_actor_kind") == "monster"
            and combat_at_input.get("action_available") is False
        )


def prepare_provider_round(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> None:
    model_catalog = state.current_catalog
    opening_update_required = _opening_needs_campaign_file_update(state)
    state.campaign_file_disposition_supported = any(
        tool.category == "narrative_state"
        and tool.capability == "narrative_state.update_campaign_file"
        and not bool(tool.input_schema.get("minProperties"))
        for tool in state.current_catalog
    )
    # Keep every valid next operation available, but do not let a committed
    # Ruleset result skip the Campaign File checkpoint by ending in prose.
    state.post_commit_tool_required = _committed_work_needs_campaign_file_disposition(state)
    # The same GM completes each ordinary turn as one agent loop. Requiring a tool does
    # not select a mechanic: the model may read a source, resolve a Ruleset operation, or
    # finish directly with the Campaign File disposition that contains its narration.
    if _campaign_file_needs_narration(state):
        model_catalog = ()
    elif _campaign_file_must_be_settled(state):
        model_catalog = tuple(
            tool
            for tool in state.current_catalog
            if tool.category == "narrative_state"
            and tool.capability == "narrative_state.update_campaign_file"
        )
        if not model_catalog:
            raise CommandRejected(
                "Campaign File disposition operation became unavailable",
                code="campaign_file_update_unavailable",
            )
    else:
        if opening_update_required and not state.campaign_file_disposition_supported:
            raise CommandRejected(
                "Campaign opening requires the Campaign File update operation",
                code="campaign_file_update_unavailable",
            )
    state.tools, state.aliases = self._provider_tools(model_catalog)
    state.decision_boundary = None
    boundary_reader = getattr(
        self._owner,
        "model_decision_boundary"
        if _exchange_has_committed_work(state)
        else "campaign_preparation_boundary",
        None,
    )
    if callable(boundary_reader) and not state.participant_ooc_only:
        state.decision_boundary = boundary_reader(state.campaign_id)
    if _saved_gm_boundary_should_be_read(state):
        # An already active private game must resume its GM decision before narration.
        _, private_boundary = self._owner.model_context_snapshot(state.campaign_id)
        if private_boundary is not None and private_boundary.owner is ModelDecisionOwner.GM:
            state.decision_boundary = private_boundary
    state.decisions.player_boundary_narration = False
    state.obligations.player_intent_resolution_required = False
    state.decisions.gm_boundary_decision = False
    if state.decision_boundary is not None:
        recorder = getattr(self._owner, "record_admin_full_audit", None)
        if callable(recorder):
            recorder(
                {
                    "kind": "model_decision_boundary",
                    "campaign_id": state.campaign_id,
                    "session_id": provider_input.session.session_id,
                    "turn_id": turn_id,
                    "mcp_discovery_id": state.discovery_id,
                    "owner": state.decision_boundary.owner.value,
                    "actor_id": state.decision_boundary.actor_id,
                    "actor_name": state.decision_boundary.actor_name,
                    "active_side_id": state.decision_boundary.active_side_id,
                    "focused_actor_id": state.decision_boundary.focused_actor_id,
                    "eligible_actor_ids": [
                        actor.actor_id for actor in state.decision_boundary.eligible_actors
                    ],
                    "campaign_revision": state.decision_boundary.campaign_revision,
                }
            )
        if state.decision_boundary.preparation is not None:
            preparation = state.decision_boundary.preparation
            model_catalog = tuple(
                tool
                for tool in state.current_catalog
                if tool.category in {"reference", "narrative_state"}
                or (
                    tool.package_id == preparation["package_id"]
                    and tool.name == preparation["intent"]
                )
            )
            state.tools, state.aliases = self._provider_tools(model_catalog)
        elif state.decision_boundary.owner is ModelDecisionOwner.PLAYER:
            state.decisions.player_boundary_narration = True
            if _player_intent_remains_pending(state):
                state.obligations.player_intent_resolution_required = True
                state.obligations.force_campaign_file_update = False
                model_catalog = tuple(
                    tool
                    for tool in state.current_catalog
                    if tool.category == "reference"
                    or tool.model_behavior
                    in {
                        ModelToolBehavior.RESOLVES_PLAYER_INTENT,
                        ModelToolBehavior.SUPPORTS_PLAYER_INTENT,
                        ModelToolBehavior.LEAVES_PLAYER_INTENT_PENDING,
                    }
                )
                if not any(
                    tool.model_behavior is ModelToolBehavior.RESOLVES_PLAYER_INTENT
                    for tool in model_catalog
                ):
                    raise CommandRejected(
                        "Ruleset left player intent pending without exposing a resolver",
                        code="player_intent_resolution_unavailable",
                    )
                state.tools, state.aliases = self._provider_tools(model_catalog)
        else:
            state.decisions.gm_boundary_decision = True
            # A Campaign File disposition is terminal for this provider exchange. Keep
            # it unavailable until Ruleset control reaches the player, or prose can
            # strand an active GM-controlled combat turn.
            state.obligations.force_campaign_file_update = False
            model_catalog = tuple(
                tool for tool in state.current_catalog if tool.category != "narrative_state"
            )
            state.tools, state.aliases = self._provider_tools(model_catalog)
    _require_checked_campaign_file_narration(
        state.tools,
        state.aliases,
        narration_held=(
            state.buffered_narration is not None or state.buffered_raw_response_blocks is not None
        ),
    )
    state.provider_tool_schemas = {
        str(function["name"]): parameters
        for provider_tool in state.tools
        if isinstance((function := provider_tool.get("function")), dict)
        and isinstance(function.get("name"), str)
        and isinstance((parameters := function.get("parameters")), dict)
    }
    _, refreshed_context = self._campaign_context_messages(
        provider_input,
        include_opening_direction=state.opening_request,
    )
    if refreshed_context is not None:
        for _turn_index, _turn_message in enumerate(state.messages):
            index = _turn_index
            message = _turn_message
            owned_data = message.get("role") == "system" or message.get("_fresh_application_data")
            if owned_data and str(message.get("content", "")).startswith(
                "SYLVIA_GM_FRESH_CAMPAIGN_STATE "
            ):
                state.messages[index] = refreshed_context
                break
    state.assemblies = {}
    state.mechanics_action_ids = {}
    state.mechanics_parameters = {}
    state.mechanics_tool_names = {}
    state.mechanics_tool_behaviors = {}
    state.mechanics_preparation_scopes = {}
    state.mechanics_pre_entities = {}
    state.local_tool_results = {}
    state.local_tool_failures = set()
    state.sequence_narration_facts = []
    state.assistant_text = []
    state.assistant_reasoning = []
    state.native_assistant_content = None
    state.native_chat_metadata = None
    state.round_finish_reason = None
    state.provider_round += 1
    set_audit_context = getattr(self._provider, "set_audit_context", None)
    if callable(set_audit_context):
        set_audit_context(
            {
                "inference_id": state.inference_id,
                "mcp_discovery_id": state.discovery_id,
                "campaign_id": state.campaign_id,
                "turn_id": turn_id,
                "round": state.provider_round,
                "context_id": self._gm_request_context_id(provider_input),
            }
        )
    state.exchange_messages = state.messages
    state.inference_messages = list(state.exchange_messages)
    if _engine_transition_needs_review(state):
        engine_control = _engine_turn_control_message(state.aliases)
        if engine_control is not None:
            state.inference_messages.append(engine_control)


def stream_provider_round(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> Iterable[ProviderEvent]:
    if state.pending_narration_facts:
        state.inference_messages.append(
            {
                "role": "system",
                "content": (
                    "SYLVIA_GM_EXACT_NARRATION_FACTS "
                    "(committed mechanics and application-owned continuation control):\n"
                    + json.dumps(
                        state.pending_narration_facts,
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    )
                    + "\nContinue this same GM turn from these committed results. "
                    + (
                        "In this exchange, assistant prose emitted alongside a tool call "
                        "before its result is an unsettled draft, not established fiction; "
                        "revise or discard it wherever the result or the player's declared "
                        "limits require. "
                        if _provisional_tool_prose_needs_review(state)
                        else ""
                    )
                    + "Use another exposed "
                    "operation only if the current intent still requires one; otherwise "
                    "decide the outcome and finish with the Campaign File disposition."
                ),
            }
        )
    state.inference_messages.extend(state.current_application_controls)
    state.current_application_controls.clear()
    if _campaign_file_needs_narration(state):
        state.inference_messages.append(
            _campaign_file_updated_narration_message(
                opening_footing=state.opening_footing,
                current_player_action=state.current_player_action,
            )
        )
    elif _held_disposition_must_be_retried(state):
        state.inference_messages.append(
            _campaign_file_disposition_message(
                opening_footing=state.opening_footing,
                current_player_action=state.current_player_action,
            )
        )
    # Keep the player's exact message in its natural user role. The stable GM contract
    # owns action scope; replacing live speech with a synthetic prose recipe both repeats
    # that contract and makes an ordinary turn unlike the dialogue agent models expect.
    if state.decision_boundary is not None:
        state.inference_messages.append(
            _campaign_preparation_message(state.decision_boundary)
            if state.decision_boundary.preparation is not None
            else _player_boundary_narration_message(
                state.decision_boundary,
                current_player_action=state.current_player_action,
                committed_operations=tuple(
                    str(fact["tool"])
                    for fact in state.pending_narration_facts
                    if isinstance(fact.get("tool"), str)
                ),
                player_intent_pending=state.obligations.player_intent_pending,
            )
            if _round_is_at_player_boundary(state)
            else _gm_decision_control_message(state.decision_boundary)
        )
    if state.private_deliberation_correction_message is not None:
        state.inference_messages.append(state.private_deliberation_correction_message)
        state.private_deliberation_correction_message = None
    if state.required_tool_call_correction_message is not None:
        state.inference_messages.append(state.required_tool_call_correction_message)
        state.required_tool_call_correction_message = None
    atomic_campaign_file_completion_required = _campaign_file_requires_atomic_completion(
        state, provider_input
    )
    state.provider_tool_call_required = _round_requires_tool_call(
        state, atomic_campaign_file_completion_required
    )
    for chunk in self._stream_round(
        state.inference_messages,
        state.tools,
        control=provider_input.control,
        provider_input=provider_input,
        tool_choice="required" if state.provider_tool_call_required else None,
        history_allowed=_round_allows_history_tool(state),
    ):
        if chunk.finish_reason is not None:
            state.round_finish_reason = chunk.finish_reason
        if chunk.chat_completion_metadata is not None:
            state.native_chat_metadata = chunk.chat_completion_metadata
        if chunk.anthropic_content is not None:
            state.native_assistant_content = chunk.anthropic_content
        if chunk.reasoning_content:
            state.assistant_reasoning.append(chunk.reasoning_content)
            # Only provider-classified reasoning enters the private inspection sink.
            yield ProviderEvent.private_reasoning(
                chunk.reasoning_content,
                response_id=state.inference_id,
            )
        if chunk.content:
            state.assistant_text.append(chunk.content)
        if chunk.tool_call_delta is not None:
            index, call_id, name, arguments = parse_tool_call_delta(chunk.tool_call_delta)
            state.assembly = state.assemblies.setdefault(index, _ToolCallAssembly())
            state.assembly.call_id += call_id
            state.assembly.name += name
            state.assembly.arguments += arguments

    # Preserve the provider's original fields before any application normalization.
    # This event goes only to private owner storage, never canonical tool evidence.
    native_continuation: dict[str, object] = {}
    if state.native_chat_metadata is not None:
        native_continuation["chat_completion_metadata"] = state.native_chat_metadata
    if state.native_assistant_content is not None:
        native_continuation["anthropic_content"] = state.native_assistant_content
    if state.assistant_reasoning:
        native_continuation["reasoning_content"] = "".join(state.assistant_reasoning)
    if native_continuation:
        native_continuation.update(state.context_fields)
        yield ProviderEvent.private_continuation(
            native_continuation,
            response_id=state.inference_id,
        )

    if not state.assemblies and state.provider_tool_call_required:
        encoded_text = "".join(state.assistant_text)
        normalized_call = _forced_single_tool_call_from_json(encoded_text, state.aliases)
        if normalized_call is not None:
            normalized_native_content = _native_content_for_text_operation(
                state.native_assistant_content,
                encoded_text,
                normalized_call,
            )
            if state.native_assistant_content is None or normalized_native_content is not None:
                state.native_assistant_content = normalized_native_content
                state.assemblies[0] = normalized_call
                state.assistant_text.clear()
                yield ProviderEvent.tool_evidence(
                    message_id=f"{state.inference_id}-forced-tool-json-normalized",
                    payload={
                        "kind": "provider_forced_tool_json_normalized",
                        "turn_id": turn_id,
                        "inference_id": state.inference_id,
                        "basis_message_id": state.basis_message_id,
                        "tool_name": normalized_call.name,
                    },
                )

    if state.assemblies:
        # Empty-completion recovery is about consecutive empty rounds. Even a rejected
        # tool assembly is substantive provider progress and receives its own bounded
        # correction path, so a later empty round starts a fresh empty sequence.
        state.empty_completion_corrections_used = 0


def prepare_tool_batch(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> Iterable[ProviderEvent]:
    state.ordered = [state.assemblies[index] for index in sorted(state.assemblies)]
    if state.ordered and state.assistant_text:
        state.obligations.provisional_tool_prose_pending = True
    for _turn_assembly in state.ordered:
        state.assembly = _turn_assembly
        if not state.assembly.call_id:
            state.assembly.call_id = f"call-{uuid.uuid4().hex}"
    for _turn_assembly in state.ordered:
        state.assembly = _turn_assembly
        resolved_tool = _resolve_provider_tool_alias(state.assembly.name, state.aliases)
        if resolved_tool is not None:
            state.assembly.name = resolved_tool[0]
    state.round_has_non_narrative_call = any(
        resolved is None or resolved[1].category != "narrative_state"
        for assembly in state.ordered
        for resolved in (_resolve_provider_tool_alias(assembly.name, state.aliases),)
    )
    raw_calls = [
        {
            "id": assembly.call_id,
            "type": "function",
            "function": {
                "name": assembly.name or "unavailable_tool",
                "arguments": assembly.arguments or "{}",
            },
        }
        for assembly in state.ordered
    ]
    assistant_message: dict[str, object] = {
        "role": "assistant",
        "content": (
            None if state.round_has_non_narrative_call else "".join(state.assistant_text) or None
        ),
        "tool_calls": raw_calls,
    }
    # Signed native blocks belong only to this in-memory provider exchange. The separate
    # evidence message below stays player-safe and must never persist private thinking.
    state.exchange_messages.append(
        attach_chat_metadata(
            {
                **assistant_message,
                **(
                    {"anthropic_content": state.native_assistant_content}
                    if state.native_assistant_content is not None
                    else {}
                ),
                **(
                    {"reasoning_content": "".join(state.assistant_reasoning)}
                    if state.assistant_reasoning
                    else {}
                ),
            },
            state.native_chat_metadata,
        )
    )
    yield ProviderEvent.tool_evidence(
        message_id=f"{state.inference_id}-assistant-tools",
        payload={
            "kind": "provider_tool_calls",
            **state.context_fields,
            "turn_id": turn_id,
            "inference_id": state.inference_id,
            "basis_message_id": state.basis_message_id,
            "assistant": cast(SafePayload, _plain_json(assistant_message)),
            "native_continuation": {
                "provider_id": provider_input.session.provider_id,
                "model_id": provider_input.session.model_id,
                "anthropic_required": state.native_assistant_content is not None,
                "chat_completion_required": state.native_chat_metadata is not None,
                "reasoning_required": bool(state.assistant_reasoning),
            },
        },
    )
    state.progress.state_changing_call_submitted = False


def execute_tool_batch(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> Iterable[ProviderEvent]:
    for position, _turn_assembly in enumerate(state.ordered):
        state.assembly = _turn_assembly
        provider_input.control.progress.update("tool", state.assembly.name)
        if state.assembly.name == "read_session_history":
            try:
                history_parameters = json.loads(state.assembly.arguments or "{}")
                if not isinstance(history_parameters, dict):
                    raise ValueError("History parameters must be an object")
                working, _bridge = self._working_contexts[provider_input.session.session_id]
                if not working.history_retrieval_enabled or not working.records:
                    raise ValueError("Historical reads are unavailable in this transaction")
                state.local_tool_results[state.assembly.call_id] = working.read_history(
                    history_parameters,
                )
            except (ValueError, KeyError):
                state.local_tool_results[state.assembly.call_id] = {
                    "error": "Invalid history request"
                }
                state.local_tool_failures.add(state.assembly.call_id)
            continue
        resolved_tool = _resolve_provider_tool_alias(state.assembly.name, state.aliases)
        if resolved_tool is None:
            reject_unavailable_tool(self, state, provider_input, turn_id=turn_id)
            continue
        _, state.catalog_tool = resolved_tool
        if _tool_call_needs_fresh_catalog(state, position):
            state.local_tool_results[state.assembly.call_id] = {
                "status": "superseded_by_catalog_refresh",
                "state_changed": False,
                "instruction": (
                    "Only one state-changing operation may execute from one catalog "
                    "snapshot. Continue as the same GM and choose the next operation from "
                    "the freshly discovered tool surface."
                ),
            }
            continue
        try:
            state.parameters = json.loads(state.assembly.arguments or "{}")
        except json.JSONDecodeError as error:
            reject_invalid_tool_json(self, state, provider_input, turn_id=turn_id, error=error)
            continue
        if not isinstance(state.parameters, dict):
            state.local_tool_results[state.assembly.call_id] = {
                "status": "rejected",
                "code": "provider_tool_arguments_not_object",
                "state_changed": False,
                "instruction": "Call the tool again with a JSON object.",
            }
            state.local_tool_failures.add(state.assembly.call_id)
            continue
        scope = state.parameters.pop(
            _PREPARATION_INTENT_ARGUMENT,
            _UNSPECIFIED_PREPARATION_INTENT,
        )
        if scope is not _UNSPECIFIED_PREPARATION_INTENT:
            try:
                if (
                    state.catalog_tool.model_behavior
                    is not ModelToolBehavior.LEAVES_PLAYER_INTENT_PENDING
                ):
                    raise CommandRejected(
                        "This operation does not declare a preparation scope",
                        code="provider_player_intent_scope_invalid",
                    )
                _preparation_keeps_intent_pending(
                    scope, pending=state.obligations.player_intent_pending
                )
            except CommandRejected as error:
                state.local_tool_results[state.assembly.call_id] = {
                    "status": "rejected",
                    "code": error.code,
                    "field_paths": [_PREPARATION_INTENT_ARGUMENT],
                    "state_changed": False,
                    "instruction": str(error),
                }
                state.local_tool_failures.add(state.assembly.call_id)
                continue
            state.mechanics_preparation_scopes[state.assembly.call_id] = scope
        state.snapshot = self._owner.snapshot(state.campaign_id)
        try:
            state.parameters = _derive_mechanics_parameters(
                state.parameters,
                state.catalog_tool.parameter_derivations or {},
                state.snapshot.participants,
            )
        except CommandRejected as error:
            state.local_tool_results[state.assembly.call_id] = {
                "status": "rejected",
                "code": error.code,
                "reason": str(error),
                "field_paths": (
                    list(error.field_paths) if isinstance(error, MechanicsRejected) else []
                ),
                "state_changed": False,
                "instruction": (
                    "Correct the selected participant or operation fields and retry once."
                ),
            }
            state.local_tool_failures.add(state.assembly.call_id)
            continue
        try:
            state.parameters = _normalize_state_references(
                state.parameters,
                state.catalog_tool.input_schema,
                state.snapshot.state,
            )
        except CommandRejected as error:
            state.local_tool_results[state.assembly.call_id] = {
                "status": "rejected",
                "code": error.code,
                "state_changed": False,
                "instruction": (
                    "Correct the state reference from current Campaign State and retry."
                ),
            }
            state.local_tool_failures.add(state.assembly.call_id)
            continue
        state.mechanics_tool_names[state.assembly.call_id] = state.catalog_tool.name
        state.mechanics_tool_behaviors[state.assembly.call_id] = state.catalog_tool.model_behavior
        validation_schema = state.provider_tool_schemas.get(
            state.assembly.name,
            state.catalog_tool.input_schema,
        )
        state.missing = _missing_required_arguments(state.parameters, validation_schema)
        if state.missing:
            record_missing_tool_fields(self, state, provider_input, turn_id=turn_id)
            continue
        if (
            state.catalog_tool.package_id == "sylvia.application"
            and state.catalog_tool.capability.startswith("action_sequence.")
        ):
            execute_action_sequence_call(self, state, provider_input, turn_id=turn_id)
            continue
        if state.catalog_tool.category == "reference":
            if state.catalog_tool.capability == "context.search":
                searcher = provider_input.knowledge_searcher
                query = state.parameters.get("query")
                raw_sources = state.parameters.get("sources")
                limit = state.parameters.get("limit", 5)
                offset = state.parameters.get("offset", 0)
                sources = (
                    tuple(str(item) for item in raw_sources)
                    if isinstance(raw_sources, Sequence)
                    and not isinstance(raw_sources, (str, bytes, bytearray))
                    and all(isinstance(item, str) for item in raw_sources)
                    else None
                )
                if (
                    searcher is None
                    or not isinstance(query, str)
                    or not query.strip()
                    or (raw_sources is not None and sources is None)
                    or not isinstance(limit, int)
                    or isinstance(limit, bool)
                    or not isinstance(offset, int)
                    or isinstance(offset, bool)
                    or offset < 0
                ):
                    state.local_tool_results[state.assembly.call_id] = {
                        "status": "rejected",
                        "code": "campaign_knowledge_search_unavailable",
                    }
                    state.local_tool_failures.add(state.assembly.call_id)
                    continue
                try:
                    matches = searcher(query, sources, limit, offset)
                except CommandRejected as error:
                    state.local_tool_results[state.assembly.call_id] = {
                        "status": "rejected",
                        "code": error.code,
                    }
                    state.local_tool_failures.add(state.assembly.call_id)
                    continue
                state.local_tool_results[state.assembly.call_id] = {
                    "status": "knowledge_search",
                    "result": dict(matches),
                }
                continue
            reader = provider_input.reference_reader
            source = state.parameters.get("source")
            path = state.parameters.get("path")
            if reader is None or not isinstance(source, str) or not isinstance(path, str):
                state.local_tool_results[state.assembly.call_id] = {
                    "status": "rejected",
                    "code": "campaign_reference_unavailable",
                }
                state.local_tool_failures.add(state.assembly.call_id)
                continue
            try:
                reference = reader(source, path)
            except CommandRejected as error:
                state.local_tool_results[state.assembly.call_id] = {
                    "status": "rejected",
                    "code": error.code,
                }
                state.local_tool_failures.add(state.assembly.call_id)
                continue
            state.local_tool_results[state.assembly.call_id] = {
                "status": "reference_read",
                "result": dict(reference),
            }
            continue
        if state.catalog_tool.category == "narrative_state":
            if len(state.ordered) != 1:
                state.local_tool_results[state.assembly.call_id] = {
                    "status": "rejected",
                    "code": "campaign_file_update_must_be_only_tool",
                    "state_changed": False,
                    "instruction": (
                        "A final Campaign File update must be the only tool call in its "
                        "assistant message. Let any reference or Ruleset call finish "
                        "first, then make one Campaign File update the final tool action. "
                        "After it succeeds, narrate the admitted outcome."
                    ),
                }
                state.local_tool_failures.add(state.assembly.call_id)
                continue
            state.progress.state_changing_call_submitted = True
            attached_narration = (
                "".join(state.assistant_text) if _buffered_narration_can_be_replaced(state) else ""
            )
            if attached_narration.strip():
                if _contains_serialized_tool_call(attached_narration):
                    state.local_tool_results[state.assembly.call_id] = {
                        "status": "rejected",
                        "code": "provider_serialized_tool_call",
                        "state_changed": False,
                    }
                    state.local_tool_failures.add(state.assembly.call_id)
                    continue
                state.buffered_narration = attached_narration
                state.buffered_response_blocks = None
                state.buffered_raw_response_blocks = None
                state.obligations.buffered_narration_needs_disposition = True
                state.progress.campaign_file_disposition_in_exchange = False
            prepare_campaign_file_revision(self, state, provider_input, turn_id=turn_id)
            if (
                state.pending_boundary is not None
                and state.pending_boundary.preparation is not None
                and (
                    state.selected_raw_blocks is None
                    or any(block.get("kind") != "ooc" for block in state.selected_raw_blocks)
                    or _campaign_file_arguments_establish_changes(state.parameters)
                )
            ):
                state.local_tool_results[state.assembly.call_id] = {
                    "status": "rejected",
                    "code": "campaign_preparation_pending",
                    "state_changed": False,
                    "instruction": (
                        "Resolve queued starting choices first. Until then, return "
                        "only OOC questions with an unchanged Campaign File; no "
                        "story, rewards or act progress."
                    ),
                }
                state.local_tool_failures.add(state.assembly.call_id)
                continue
            validate_revision_speakers(self, state, provider_input, turn_id=turn_id)
            if (
                state.ooc_ack_missing
                or state.unsupported_speakers
                or (state.revised_narration_supplied and state.revised_raw_blocks is None)
            ):
                reject_invalid_revision(self, state, provider_input, turn_id=turn_id)
                continue
            settle_campaign_file_narration(self, state, provider_input, turn_id=turn_id)
            if state.empty_collection_paths:
                state.local_tool_results[state.assembly.call_id] = {
                    "status": "rejected",
                    "code": "campaign_file_empty_collections",
                    "field_paths": list(state.empty_collection_paths),
                    "state_changed": False,
                    "instruction": (
                        "Omit collections that have no items. If the decided outcome "
                        "changes no durable Campaign File truth, send only the required "
                        "complete narration; otherwise resubmit every lasting change as "
                        "a non-empty collection."
                    ),
                }
                state.local_tool_failures.add(state.assembly.call_id)
                continue
            if not state.parameters_establish_changes and state.campaign_file_mutation_attempted:
                state.local_tool_results[state.assembly.call_id] = {
                    "status": "rejected",
                    "code": "campaign_file_unchanged_after_rejected_update",
                    "state_changed": False,
                    "instruction": (
                        "The decided outcome already led you to attempt durable Campaign "
                        "File changes. An empty review cannot retract those facts. "
                        "Correct the rejected non-empty update; otherwise Sylvia must "
                        "fail the turn without releasing narration."
                    ),
                }
                state.local_tool_failures.add(state.assembly.call_id)
                continue
            state.mutation_attempted_before_call = state.campaign_file_mutation_attempted
            if state.parameters_establish_changes:
                state.campaign_file_mutation_attempted = True
            writer = provider_input.narrative_writer
            if writer is None:
                state.local_tool_results[state.assembly.call_id] = {
                    "status": "rejected",
                    "code": "campaign_narrative_state_unavailable",
                }
                state.local_tool_failures.add(state.assembly.call_id)
                continue
            try:
                state.update = writer(state.parameters)
            except CommandRejected as error:
                record_campaign_file_rejection(
                    self, state, provider_input, turn_id=turn_id, error=error
                )
                continue
            if state.buffered_raw_response_blocks is not None:
                response_snapshot = self._owner.snapshot(state.campaign_id)
                state.settled_narration, state.buffered_response_blocks = _project_response_blocks(
                    state.buffered_raw_response_blocks,
                    response_snapshot,
                    party_roles=_persisted_selected_party_roles(
                        self._owner,
                        state.campaign_id,
                        response_snapshot.participants,
                    ),
                )
                state.buffered_narration = state.settled_narration
            update_status = str(state.update.get("status", "campaign_file_updated"))
            if update_status == "campaign_file_unchanged":
                if state.parameters_establish_changes:
                    restore_unchanged_narration(self, state, provider_input, turn_id=turn_id)
                    continue
                record_unchanged_campaign_file(self, state, provider_input, turn_id=turn_id)
                continue
            record_updated_campaign_file(self, state, provider_input, turn_id=turn_id)
            continue
        yield from submit_mechanics_call(self, state, provider_input, turn_id=turn_id)


def reconcile_tool_batch(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> Iterable[ProviderEvent]:
    reference_failure_instruction = (
        "This was an optional read-only Campaign File reference lookup. It cannot update, "
        "write, save, or commit state. Valid paths are: campaign_state 'current' or a "
        "stable Campaign File record ID; campaign_plan 'structured', "
        "'structured/structure/<act-id>', 'structured/objectives/<objective-id>' "
        "or 'readable'; "
        "participant the exact participant ID; campaign_setting or ruleset an exact "
        "admitted package path; engine_format 'spatial_scene' for the installed scene "
        "schema and example. Large package text uses the returned '#text/<offset>' "
        "reference and next_path; search returns relevant bounded excerpt paths. "
        "Retry with a valid source and path only if the fact is "
        "still needed; otherwise continue from supplied authoritative context."
    )
    optional_reference_failures: set[str] = set()
    for _turn_assembly in state.ordered:
        state.assembly = _turn_assembly
        if state.assembly.call_id not in state.local_tool_failures:
            continue
        resolved_tool = _resolve_provider_tool_alias(state.assembly.name, state.aliases)
        if resolved_tool is None or resolved_tool[1].category != "reference":
            continue
        optional_reference_failures.add(state.assembly.call_id)
        state.local_tool_results[state.assembly.call_id].update(
            {
                "correction_optional": True,
                "instruction": reference_failure_instruction,
            }
        )
    state.required_tool_failures = state.local_tool_failures - optional_reference_failures
    state.progress.round_failed = bool(state.local_tool_failures)
    round_has_any_commit = bool(state.sequence_narration_facts)
    round_leaves_player_intent_pending = False
    round_resolves_player_intent = False
    round_narration_facts: list[dict[str, object]] = list(state.sequence_narration_facts)
    tool_messages_start = len(state.exchange_messages)
    for _turn_assembly in state.ordered:
        state.assembly = _turn_assembly
        if state.assembly.call_id in state.local_tool_results:
            if state.assembly.call_id in state.local_tool_failures:
                state.progress.round_failed = True
            state.exchange_messages.append(
                {
                    "role": "tool",
                    "tool_call_id": state.assembly.call_id,
                    "name": state.assembly.name,
                    "content": json.dumps(
                        state.local_tool_results[state.assembly.call_id],
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    ),
                }
            )
            continue
        state.snapshot = self._owner.snapshot(state.campaign_id)
        action_id = state.mechanics_action_ids[state.assembly.call_id]
        terminal_event = next(
            (
                item
                for item in reversed(state.snapshot.events)
                if item.action_id == action_id
                and item.event_type
                in {
                    CanonicalEventType.ACTION_COMMITTED,
                    CanonicalEventType.ACTION_FAILED,
                }
            ),
            None,
        )
        tool_result_event = next(
            (
                item
                for item in reversed(state.snapshot.events)
                if item.action_id == action_id and item.event_type is CanonicalEventType.TOOL_RESULT
            ),
            None,
        )
        if terminal_event is None or tool_result_event is None:
            raise CommandRejected(
                "Canonical mechanic result is unavailable",
                code="canonical_mechanic_result_unavailable",
            )
        if terminal_event.event_type is CanonicalEventType.ACTION_FAILED:
            state.progress.round_failed = True
            state.required_tool_failures.add(state.assembly.call_id)
        else:
            round_has_any_commit = True
            state.behavior = state.mechanics_tool_behaviors[state.assembly.call_id]
            if state.behavior is ModelToolBehavior.LEAVES_PLAYER_INTENT_PENDING:
                round_leaves_player_intent_pending = _preparation_keeps_intent_pending(
                    state.mechanics_preparation_scopes.get(
                        state.assembly.call_id,
                        _UNSPECIFIED_PREPARATION_INTENT,
                    ),
                    pending=state.obligations.player_intent_pending,
                )
            elif _committed_tool_resolves_player_boundary(state):
                round_resolves_player_intent = True
        result_payload = _plain_json(tool_result_event.safe_payload.get("result", {}))
        # Narration facts below use the full receipt. Model history needs the outcome/card,
        # while the canonical engine event and private Full Audit retain diagnostic context.
        committed = terminal_event.event_type is CanonicalEventType.ACTION_COMMITTED
        model_result = (
            {key: value for key, value in result_payload.items() if key != "context"}
            if committed and isinstance(result_payload, Mapping) else result_payload
        )
        audit = getattr(self._owner, "record_admin_full_audit", None)
        if callable(audit):
            audit({"kind": "gm_mechanic_receipt", "action_id": tool_result_event.action_id,
                   "campaign_id": state.campaign_id,
                   "session_id": provider_input.session.session_id,
                   "turn_id": turn_id, "result": result_payload})
        current_entities = _current_referenced_entities(
            state.mechanics_parameters[state.assembly.call_id], state.snapshot.state
        )
        round_result: object = {
            "status": terminal_event.event_type.value,
            "action_id": tool_result_event.action_id,
            "campaign_revision": state.snapshot.revision,
            "safe_payload": _plain_json(terminal_event.safe_payload),
            "result": model_result,
            "current_entities": current_entities,
        }
        if terminal_event.event_type is CanonicalEventType.ACTION_COMMITTED:
            if state.mechanics_tool_names[
                state.assembly.call_id
            ] == "end_combat_encounter" or state.mechanics_tool_names[state.assembly.call_id] in {
                tool.name for tool in state.required_transitions
            }:
                state.obligations.engine_transition_review_pending = False
            round_narration_facts.append(
                _mechanics_narration_facts(
                    state.mechanics_tool_names[state.assembly.call_id],
                    result_payload,
                    current_entities,
                    state.mechanics_pre_entities[state.assembly.call_id],
                    state.mechanics_parameters[state.assembly.call_id],
                    _current_combat_state(state.snapshot.state),
                    state.entity_display_names,
                ),
            )
        state.exchange_messages.append(
            {
                "role": "tool",
                "tool_call_id": state.assembly.call_id,
                "name": state.assembly.name,
                "content": json.dumps(
                    round_result,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                ),
            }
        )
    tool_messages = state.exchange_messages[tool_messages_start:]
    if tool_messages:
        yield ProviderEvent.tool_evidence(
            message_id=f"{state.inference_id}-tool-results",
            payload={
                "kind": "provider_tool_results",
                **state.context_fields,
                "turn_id": turn_id,
                "inference_id": state.inference_id,
                "basis_message_id": state.basis_message_id,
                "results": cast(
                    JsonValue,
                    _plain_json(tool_messages),
                ),
            },
        )
    provider_input.control.progress.update("working")
    if round_has_any_commit:
        state.progress.any_commit_in_exchange = True
        if round_leaves_player_intent_pending:
            state.obligations.player_intent_pending = True
        if round_resolves_player_intent:
            state.obligations.player_intent_pending = False
        if provider_input.session.role is WriterRole.DM:
            state.progress.campaign_file_disposition_in_exchange = False
        state.buffered_narration = None
        state.buffered_response_blocks = None
        state.buffered_raw_response_blocks = None
        state.obligations.buffered_narration_needs_disposition = False
    if round_has_any_commit:
        state.pending_narration_facts.extend(round_narration_facts)


def prepare_turn_recovery(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> Iterable[ProviderEvent]:
    state.campaign_id = cast(str, provider_input.session.campaign_id)
    if state.campaign_id is None:
        raise CommandRejected("A DM provider turn requires a campaign")
    state.canonical_player_narration = _canonical_player_narration(
        self._owner,
        state.campaign_id,
    )
    state.messages = self._messages(
        provider_input,
        campaign_has_mechanics=False,
        tools_available=False,
        canonical_player_narration=state.canonical_player_narration,
    )
    state.context_owner = self._gm_context_owner(provider_input)
    state.context_fields = cast(
        "dict[str, JsonValue]",
        {"context_owner": state.context_owner} if state.context_owner is not None else {},
    )
    state.basis_message_id = provider_input.basis_message_id
    persisted_system_contract = any(
        message.kind is SessionMessageKind.EVIDENCE
        and message.content.get("kind") == "gm_system_contract"
        for message in provider_input.history.messages
    )
    if state.basis_message_id is not None and not persisted_system_contract:
        yield ProviderEvent.tool_evidence(
            message_id=f"gm-system-contract-{provider_input.session.session_id}",
            payload={
                "kind": "gm_system_contract",
                "version": _GM_SYSTEM_CONTRACT_VERSION,
                "contract": cast(str, state.messages[0]["content"]),
            },
        )
    if state.basis_message_id is None:
        basis_message = next(
            (
                message
                for message in reversed(provider_input.history.messages)
                if message.kind is SessionMessageKind.USER
                or (
                    message.kind is SessionMessageKind.EVIDENCE
                    and message.content.get("kind")
                    in {
                        "campaign_start",
                        "player_action_response",
                    }
                )
            ),
            None,
        )
        state.basis_message_id = basis_message.message_id if basis_message is not None else None
    state.direct_response = any(
        message.message_id == state.basis_message_id
        and message.kind is SessionMessageKind.EVIDENCE
        and message.content.get("kind") == "player_action_response"
        for message in provider_input.history.messages
    )
    state.post_mechanics_recovery = provider_input.recovery_kind == "post_mechanics_narration"
    redirected = next(
        (
            message
            for message in provider_input.history.messages
            if message.message_id == state.basis_message_id
        ),
        None,
    )
    if redirected is not None and redirected.content.get("redirect_interruption_id"):
        start_sequence = redirected.content.get("canonical_start_sequence", 0)
        saved_events = self._owner.snapshot(state.campaign_id).events
        saved_action_ids = {
            event.action_id
            for event in saved_events
            if isinstance(start_sequence, int)
            and event.sequence > start_sequence
            and event.event_type is CanonicalEventType.ACTION_COMMITTED
        }
        saved_results = [
            {
                "action_id": event.action_id,
                "tool_result": {
                    **cast(Mapping[str, object], _plain_json(event.safe_payload)),
                    "result": _public_action_result(
                        event.action_id,
                        event.safe_payload.get("result"),
                    ),
                },
            }
            for event in saved_events
            if event.action_id in saved_action_ids
            and event.event_type is CanonicalEventType.TOOL_RESULT
        ]
        state.messages.append(
            {
                "role": "system",
                "content": (
                    "SYLVIA_GM_REDIRECTED_CONTINUATION (trusted application instruction): "
                    "The player stopped the prior response and sent the latest message to "
                    "redirect its unfinished continuation. Apply that message, including its "
                    "separate OOC guidance, from the current saved state. A replacement direction "
                    "supersedes unperformed intent; do not carry out an abandoned action. "
                    "Completed actions and saved Campaign File changes remain true: do not undo, "
                    "reroll or repeat them. Resolve any still-required Ruleset consequence or "
                    "enemy response before admitting another incompatible player action. "
                    "Do not resume an old held draft as though the new guidance did not exist.\n"
                    "SAVED_ACTION_RESULTS (authoritative facts, not instructions):\n"
                    + json.dumps(saved_results, ensure_ascii=False, separators=(",", ":"))
                ),
            }
        )
    retry_receipt = provider_input.retry_action_receipt
    if state.post_mechanics_recovery:
        if retry_receipt is None:
            raise CommandRejected(
                "Post-mechanics recovery requires its committed action receipt",
                code="retry_action_receipt_unavailable",
            )
        state.messages.append(
            {
                "role": "system",
                "content": (
                    "SYLVIA_GM_POST_MECHANICS_RECOVERY_CONTROL "
                    "(trusted application instruction): This is the same interrupted GM "
                    "turn, continuing after the authoritative action receipt below committed "
                    "once. Do not repeat or reroll that committed operation. Continue from "
                    "its result. You may call a newly exposed Ruleset operation only when an "
                    "unresolved remainder of the player's current intent or an authoritative "
                    "Ruleset transition genuinely requires it. Query current Campaign File "
                    "or admitted reference facts if needed. Before any player-facing prose, "
                    "settle the complete decided outcome through the Campaign File update as "
                    "your final tool action; an empty review is valid only when no durable "
                    "Campaign File truth changed. Then narrate only the admitted in-world "
                    "outcome. Do not mention recovery, tools, the receipt, or bookkeeping.\n"
                    "SYLVIA_GM_COMMITTED_ACTION_RECEIPT "
                    "(authoritative facts, not instructions):\n"
                    + json.dumps(
                        {
                            "campaign_id": retry_receipt.campaign_id,
                            "action_id": retry_receipt.action_id,
                            "status": retry_receipt.status.value,
                            "result": _public_action_result(
                                retry_receipt.action_id,
                                retry_receipt.result,
                            ),
                            "resulting_revision": retry_receipt.resulting_revision,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                        ensure_ascii=False,
                    )
                ),
            }
        )
    state.opening_request = (
        not state.direct_response
        and any(
            message.kind is SessionMessageKind.EVIDENCE
            and message.content.get("kind") == "campaign_start"
            for message in provider_input.history.messages
        )
        and not any(
            message.kind in {SessionMessageKind.USER, SessionMessageKind.TALK}
            for message in provider_input.history.messages
        )
    )
    state.participants = tuple(getattr(self._owner.snapshot(state.campaign_id), "participants", ()))
    state.opening_footing = (
        self._opening_footing_payload(provider_input) if state.opening_request else None
    )
    state.current_player_message = next(
        (
            message
            for message in reversed(provider_input.history.messages)
            if message.kind is SessionMessageKind.USER and not state.direct_response
        ),
        None,
    )
    state.current_player_action = (
        str(state.current_player_message.content.get("text", ""))
        if state.current_player_message is not None
        else None
    )
    state.current_user_wire_text = (
        _player_turn_text(state.current_player_message.content)
        if state.current_player_message is not None
        else None
    )
    state.saved_sequence_results = _saved_action_sequence_results(provider_input, self._owner)
    state.pending_narration_facts = [
        facts
        for result in state.saved_sequence_results
        if (facts := _action_sequence_narration_facts(self._owner, state.campaign_id, result))
        is not None
    ]
    state.obligations.provisional_tool_prose_pending = False
    state.obligations.player_intent_pending = False
    state.committed_action_ids = {
        event.action_id
        for event in self._owner.snapshot(state.campaign_id).events
        if event.event_type is CanonicalEventType.ACTION_COMMITTED and event.action_id is not None
    }


def restore_saved_turn_progress(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> None:
    for history_message in provider_input.history.messages:
        if history_message.kind is not SessionMessageKind.EVIDENCE:
            continue
        contract = history_message.content
        if (
            contract.get("kind") != "provider_mechanics_action_contract"
            or contract.get("basis_message_id") != state.basis_message_id
        ):
            continue
        action_id = contract.get("action_id")
        raw_behavior = contract.get("model_behavior")
        decision_owner = contract.get("decision_owner")
        if (
            not isinstance(action_id, str)
            or not action_id
            or not isinstance(raw_behavior, str)
            or decision_owner not in {None, *tuple(owner.value for owner in ModelDecisionOwner)}
        ):
            raise CommandRejected(
                "Persisted mechanics action contract is invalid",
                code="provider_tool_evidence_invalid",
            )
        if action_id not in state.committed_action_ids:
            continue
        try:
            state.behavior = ModelToolBehavior(raw_behavior)
        except ValueError as error:
            raise CommandRejected(
                "Persisted mechanics action behavior is invalid",
                code="provider_tool_evidence_invalid",
            ) from error
        if state.behavior is ModelToolBehavior.LEAVES_PLAYER_INTENT_PENDING:
            try:
                state.obligations.player_intent_pending = _preparation_keeps_intent_pending(
                    contract.get(_PREPARATION_INTENT_ARGUMENT, _UNSPECIFIED_PREPARATION_INTENT),
                    pending=state.obligations.player_intent_pending,
                )
            except CommandRejected as error:
                raise CommandRejected(
                    "Persisted preparation scope is invalid",
                    code="provider_tool_evidence_invalid",
                ) from error
        elif (
            state.behavior is ModelToolBehavior.RESOLVES_PLAYER_INTENT
            and decision_owner == ModelDecisionOwner.PLAYER.value
        ):
            state.obligations.player_intent_pending = False
    state.buffered_narration = None
    state.buffered_response_blocks = None
    state.buffered_raw_response_blocks = None
    state.ooc_ack_required = bool(
        state.current_player_message is not None
        and isinstance(state.current_player_message.content.get("ooc_text"), str)
        and str(state.current_player_message.content.get("ooc_text", "")).strip()
    )
    # An OOC-only correction does not resume an opted-in participant's pending game turn.
    state.participant_ooc_only = (
        state.context_owner is not None
        and state.ooc_ack_required
        and not str(state.current_player_action or "").strip()
    )
    state.obligations.buffered_narration_needs_disposition = False
    state.progress.any_commit_in_exchange = state.post_mechanics_recovery or state.direct_response
    state.progress.campaign_file_commit_in_exchange = False
    state.progress.campaign_file_disposition_in_exchange = False
    state.obligations.campaign_file_narration_pending = False
    state.missing_required_tool_corrections_used = 0
    state.max_missing_required_tool_corrections = 3
    state.rejected_tool_corrections_used = 0
    state.max_rejected_tool_corrections = 5
    state.empty_completion_corrections_used = 0
    state.max_empty_completion_corrections = 2
    state.private_deliberation_corrections_used = 0
    state.max_private_deliberation_corrections = 2
    state.private_deliberation_correction_message = None
    state.required_tool_call_correction_message = None
    state.current_application_controls = []
    state.repeated_response_corrections_used = 0
    state.max_repeated_response_corrections = 2
    state.obligations.pending_failed_tool_call = False
    state.obligations.force_campaign_file_update = False
    # Native assistant history before the current task is completed player-facing dialogue,
    # not a held draft. Persisted same-turn provider evidence is appended after the current
    # task by _messages(), so only that suffix may resume an unsettled draft or tool result.
    current_task_index = max(
        (
            index
            for index, message in enumerate(state.messages)
            if message.get("role") == "user"
            and state.current_user_wire_text is not None
            and message.get("content") == state.current_user_wire_text
        ),
        default=len(state.messages) - 1,
    )
    if state.opening_request:
        # _messages emits the trusted opening task, optional validated Ready input, then
        # persisted same-turn evidence. Anchor recovery before that evidence suffix.
        current_task_index = next(
            index
            for index, message in enumerate(state.messages)
            if message.get("role") == "system"
            and str(message.get("content", "")).startswith("SYLVIA_GM_OPENING_TASK ")
        )
        if (
            current_task_index + 1 < len(state.messages)
            and state.messages[current_task_index + 1].get("role") == "user"
        ):
            current_task_index += 1
    elif state.direct_response:
        current_task_index = next(
            index
            for index, message in enumerate(state.messages)
            if message.get("role") == "system"
            and str(message.get("content", "")).startswith("SYLVIA_PLAYER_ACTION_RESPONSE_TASK ")
        )
    state.campaign_file_mutation_attempted = _persisted_campaign_file_mutation_attempted(
        state.messages[current_task_index + 1 :]
    )
    player_actions = [
        str(message.content.get("text", ""))
        for message in provider_input.history.messages
        if message.kind is SessionMessageKind.USER
    ]
    state.previous_player_action = player_actions[-2] if len(player_actions) >= 2 else None
    state.previous_player_facing_response = next(
        (
            str(record.get("text"))
            for record in reversed(state.canonical_player_narration)
            if isinstance(record.get("text"), str) and bool(str(record.get("text")))
        ),
        None,
    )
    if state.previous_player_facing_response is None:
        state.previous_player_facing_response = next(
            (
                str(message.get("content"))
                for message in reversed(state.messages[:current_task_index])
                if message.get("role") == "assistant"
                and isinstance(message.get("content"), str)
                and bool(str(message.get("content")))
                and not message.get("tool_calls")
            ),
            None,
        )
    state.resumed_messages = state.messages[current_task_index + 1 :]


def resume_exchange_results(
    self: ProviderSessionAdapter,
    state: ProviderTurnState,
    provider_input: SessionProviderInput,
    *,
    turn_id: str,
) -> None:
    for _turn_message in state.resumed_messages:
        message = _turn_message
        if message.get("role") == "assistant":
            content = message.get("content")
            if isinstance(content, str) and content:
                state.buffered_narration = content
                state.buffered_response_blocks = None
                state.buffered_raw_response_blocks = None
                state.obligations.buffered_narration_needs_disposition = True
            raw_calls = message.get("tool_calls")
            if isinstance(raw_calls, Sequence) and not isinstance(
                raw_calls, (str, bytes, bytearray)
            ):
                for raw_call in raw_calls:
                    function = raw_call.get("function") if isinstance(raw_call, Mapping) else None
                    if not isinstance(function, Mapping):
                        continue
                    name = function.get("name")
                    if not isinstance(name, str) or not (
                        name == "update_campaign_file" or name.endswith("_update_campaign_file")
                    ):
                        continue
                    try:
                        arguments = json.loads(str(function.get("arguments", "{}")))
                        raw_blocks = arguments.get(_CAMPAIGN_FILE_REVISED_NARRATION_ARGUMENT)
                        if isinstance(raw_blocks, list):
                            state.buffered_raw_response_blocks = _validated_raw_response_blocks(
                                raw_blocks, allow_legacy_dialogue_text=True
                            )
                            state.buffered_narration = None
                            state.buffered_response_blocks = None
                            state.obligations.buffered_narration_needs_disposition = True
                        elif (
                            isinstance(raw_blocks, str)
                            and raw_blocks.strip()
                            and not _contains_serialized_tool_call(raw_blocks)
                        ):
                            state.buffered_narration = raw_blocks
                            state.buffered_response_blocks = None
                            state.buffered_raw_response_blocks = None
                            state.obligations.buffered_narration_needs_disposition = True
                    except (json.JSONDecodeError, ValueError, AttributeError):
                        pass
            if raw_calls:
                state.obligations.pending_failed_tool_call = False
        if message.get("role") != "tool":
            continue
        try:
            resumed_result = json.loads(str(message.get("content", "{}")))
        except json.JSONDecodeError:
            continue
        if not isinstance(resumed_result, Mapping):
            continue
        status = resumed_result.get("status")
        if status in {"rejected", CanonicalEventType.ACTION_FAILED.value} and not bool(
            resumed_result.get("correction_optional")
        ):
            state.obligations.pending_failed_tool_call = True
        if status in {
            "campaign_file_updated",
            CanonicalEventType.ACTION_COMMITTED.value,
        }:
            state.progress.any_commit_in_exchange = True
        if status == "campaign_file_updated":
            settled_narration = resumed_result.get("player_facing_response")
            persisted_blocks = resumed_result.get("response_blocks")
            if persisted_blocks is not None:
                try:
                    state.buffered_response_blocks = validate_response_blocks(persisted_blocks)
                except ValueError as error:
                    raise CommandRejected(
                        "Persisted Campaign File response blocks are invalid",
                        code="provider_tool_evidence_invalid",
                    ) from error
            else:
                state.buffered_response_blocks = None
            if settled_narration is not None:
                if not isinstance(settled_narration, str) or not settled_narration.strip():
                    raise CommandRejected(
                        "Persisted Campaign File disposition narration is invalid",
                        code="provider_tool_evidence_invalid",
                    )
                state.buffered_narration = settled_narration
                if state.buffered_response_blocks is None:
                    state.buffered_raw_response_blocks = None
            state.progress.campaign_file_commit_in_exchange = True
            state.progress.campaign_file_disposition_in_exchange = True
            if state.buffered_narration is not None:
                state.obligations.buffered_narration_needs_disposition = False
                state.obligations.campaign_file_narration_pending = False
            else:
                state.obligations.campaign_file_narration_pending = True
        if status == "campaign_file_unchanged":
            settled_narration = resumed_result.get("player_facing_response")
            persisted_blocks = resumed_result.get("response_blocks")
            if persisted_blocks is not None:
                try:
                    state.buffered_response_blocks = validate_response_blocks(persisted_blocks)
                except ValueError as error:
                    raise CommandRejected(
                        "Persisted Campaign File response blocks are invalid",
                        code="provider_tool_evidence_invalid",
                    ) from error
            else:
                state.buffered_response_blocks = None
            if settled_narration is not None:
                if not isinstance(settled_narration, str) or not settled_narration.strip():
                    raise CommandRejected(
                        "Persisted Campaign File disposition narration is invalid",
                        code="provider_tool_evidence_invalid",
                    )
                state.buffered_narration = settled_narration
                if state.buffered_response_blocks is None:
                    state.buffered_raw_response_blocks = None
            state.progress.campaign_file_disposition_in_exchange = True
            if state.buffered_narration is not None:
                state.obligations.buffered_narration_needs_disposition = False
                state.obligations.campaign_file_narration_pending = False
            else:
                state.obligations.campaign_file_narration_pending = True
        if status == CanonicalEventType.ACTION_COMMITTED.value:
            state.progress.campaign_file_disposition_in_exchange = False
            state.obligations.campaign_file_narration_pending = False
            state.buffered_narration = None
            state.buffered_response_blocks = None
            state.buffered_raw_response_blocks = None
            state.obligations.buffered_narration_needs_disposition = False
        elif status not in {
            "rejected",
            CanonicalEventType.ACTION_FAILED.value,
            "campaign_file_updated",
            "campaign_file_unchanged",
        }:
            state.buffered_narration = None
            state.buffered_response_blocks = None
            state.buffered_raw_response_blocks = None
            state.obligations.buffered_narration_needs_disposition = False
    if _settled_disposition_still_owes_ooc_acknowledgement(state):
        raise CommandRejected(
            "Persisted OOC turn response omits its acknowledgement",
            code="provider_tool_evidence_invalid",
        )
    state.obligations.force_campaign_file_update = bool(
        (state.buffered_narration is not None or state.buffered_raw_response_blocks is not None)
        and state.obligations.buffered_narration_needs_disposition
    )
