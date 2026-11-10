"""Phases of a GM provider turn; the adapter retains escaping loop control."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any

from ._provider_turn_state import ProviderTurnState
from ._session_loop import ProviderEvent, SessionProviderInput

if TYPE_CHECKING:
    from ._provider_session import ProviderSessionAdapter

from sylvia_gm.domain.authority import CanonicalEventType
from sylvia_gm.domain.errors import CommandRejected
from collections.abc import Mapping
from sylvia_gm.domain.authority import ModelDecisionOwner
from sylvia_gm.domain.authority import ModelToolBehavior
from collections.abc import Sequence
from sylvia_gm.domain.session import SessionMessageKind
from typing import cast
import json
from sylvia_gm.domain.authority import validate_response_blocks
from ._provider_session import (
    _CAMPAIGN_FILE_REVISED_NARRATION_ARGUMENT,
    _GM_SYSTEM_CONTRACT_VERSION,
    _PREPARATION_INTENT_ARGUMENT,
    _UNSPECIFIED_PREPARATION_INTENT,
    _action_sequence_narration_facts,
    _canonical_player_narration,
    _contains_serialized_tool_call,
    _persisted_campaign_file_mutation_attempted,
    _plain_json,
    _player_turn_text,
    _preparation_keeps_intent_pending,
    _public_action_result,
    _saved_action_sequence_results,
    _validated_raw_response_blocks,
)


def prepare_turn_recovery(
    self: ProviderSessionAdapter, state: ProviderTurnState,
    provider_input: SessionProviderInput, *, turn_id: str,
) -> Iterable[ProviderEvent]:
    state.campaign_id = provider_input.session.campaign_id
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
    state.context_fields = {"context_owner": state.context_owner} if state.context_owner is not None else {}
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
                    and message.content.get("kind") in {
                        "campaign_start", "player_action_response",
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
    redirected = next((message for message in provider_input.history.messages
                       if message.message_id == state.basis_message_id), None)
    if redirected is not None and redirected.content.get("redirect_interruption_id"):
        start_sequence = redirected.content.get("canonical_start_sequence", 0)
        saved_events = self._owner.snapshot(state.campaign_id).events
        saved_action_ids = {
            event.action_id for event in saved_events
            if isinstance(start_sequence, int) and event.sequence > start_sequence
            and event.event_type is CanonicalEventType.ACTION_COMMITTED
        }
        saved_results = [
            {"action_id": event.action_id, "tool_result": {
                **_plain_json(event.safe_payload),
                "result": _public_action_result(
                    event.action_id, event.safe_payload.get("result"),
                ),
            }}
            for event in saved_events
            if event.action_id in saved_action_ids
            and event.event_type is CanonicalEventType.TOOL_RESULT
        ]
        state.messages.append({
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
        })
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
                                retry_receipt.action_id, retry_receipt.result,
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
    state.opening_request = not state.direct_response and any(
        message.kind is SessionMessageKind.EVIDENCE
        and message.content.get("kind") == "campaign_start"
        for message in provider_input.history.messages
    ) and not any(
        message.kind in {SessionMessageKind.USER, SessionMessageKind.TALK}
        for message in provider_input.history.messages
    )
    state.participants = tuple(getattr(self._owner.snapshot(state.campaign_id), "participants", ()))
    state.opening_footing = self._opening_footing_payload(provider_input) if state.opening_request else None
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
        state.facts for result in state.saved_sequence_results
        if (
            facts := state.bind('facts', _action_sequence_narration_facts(self._owner, state.campaign_id, result))
        ) is not None
    ]
    state.provisional_tool_prose_pending = False
    state.player_intent_pending = False
    state.committed_action_ids = {
        event.action_id
        for event in self._owner.snapshot(state.campaign_id).events
        if event.event_type is CanonicalEventType.ACTION_COMMITTED
        and event.action_id is not None
    }


def restore_saved_turn_progress(
    self: ProviderSessionAdapter, state: ProviderTurnState,
    provider_input: SessionProviderInput, *, turn_id: str,
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
        state.action_id = contract.get("action_id")
        raw_behavior = contract.get("model_behavior")
        decision_owner = contract.get("decision_owner")
        if (
            not isinstance(state.action_id, str)
            or not state.action_id
            or not isinstance(raw_behavior, str)
            or decision_owner not in {None, *tuple(owner.value for owner in ModelDecisionOwner)}
        ):
            raise CommandRejected(
                "Persisted mechanics action contract is invalid",
                code="provider_tool_evidence_invalid",
            )
        if state.action_id not in state.committed_action_ids:
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
                state.player_intent_pending = _preparation_keeps_intent_pending(
                    contract.get(_PREPARATION_INTENT_ARGUMENT, _UNSPECIFIED_PREPARATION_INTENT),
                    pending=state.player_intent_pending,
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
            state.player_intent_pending = False
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
        state.context_owner is not None and state.ooc_ack_required
        and not str(state.current_player_action or "").strip()
    )
    state.buffered_narration_needs_disposition = False
    state.any_commit_in_exchange = state.post_mechanics_recovery or state.direct_response
    state.campaign_file_commit_in_exchange = False
    state.campaign_file_disposition_in_exchange = False
    state.campaign_file_narration_pending = False
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
    state.pending_failed_tool_call = False
    state.force_campaign_file_update = False
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
            index for index, message in enumerate(state.messages)
            if message.get("role") == "system"
            and str(message.get("content", "")).startswith(
                "SYLVIA_PLAYER_ACTION_RESPONSE_TASK "
            )
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
    self: ProviderSessionAdapter, state: ProviderTurnState,
    provider_input: SessionProviderInput, *, turn_id: str,
) -> None:
    for state.message in state.resumed_messages:
        if state.message.get("role") == "assistant":
            content = state.message.get("content")
            if isinstance(content, str) and content:
                state.buffered_narration = content
                state.buffered_response_blocks = None
                state.buffered_raw_response_blocks = None
                state.buffered_narration_needs_disposition = True
            state.raw_calls = state.message.get("tool_calls")
            if isinstance(state.raw_calls, Sequence) and not isinstance(
                state.raw_calls, (str, bytes, bytearray)
            ):
                for raw_call in state.raw_calls:
                    state.function = (
                        raw_call.get("function")
                        if isinstance(raw_call, Mapping)
                        else None
                    )
                    if not isinstance(state.function, Mapping):
                        continue
                    state.name = state.function.get("name")
                    if not isinstance(state.name, str) or not (
                        state.name == "update_campaign_file"
                        or state.name.endswith("_update_campaign_file")
                    ):
                        continue
                    try:
                        state.arguments = json.loads(str(state.function.get("arguments", "{}")))
                        raw_blocks = state.arguments.get(
                            _CAMPAIGN_FILE_REVISED_NARRATION_ARGUMENT
                        )
                        if isinstance(raw_blocks, list):
                            state.buffered_raw_response_blocks = (
                                _validated_raw_response_blocks(
                                    raw_blocks, allow_legacy_dialogue_text=True
                                )
                            )
                            state.buffered_narration = None
                            state.buffered_response_blocks = None
                            state.buffered_narration_needs_disposition = True
                        elif (
                            isinstance(raw_blocks, str)
                            and raw_blocks.strip()
                            and not _contains_serialized_tool_call(raw_blocks)
                        ):
                            state.buffered_narration = raw_blocks
                            state.buffered_response_blocks = None
                            state.buffered_raw_response_blocks = None
                            state.buffered_narration_needs_disposition = True
                    except (json.JSONDecodeError, ValueError, AttributeError):
                        pass
            if state.raw_calls:
                state.pending_failed_tool_call = False
        if state.message.get("role") != "tool":
            continue
        try:
            resumed_result = json.loads(str(state.message.get("content", "{}")))
        except json.JSONDecodeError:
            continue
        if not isinstance(resumed_result, Mapping):
            continue
        status = resumed_result.get("status")
        if status in {"rejected", CanonicalEventType.ACTION_FAILED.value} and not bool(
            resumed_result.get("correction_optional")
        ):
            state.pending_failed_tool_call = True
        if status in {
            "campaign_file_updated",
            CanonicalEventType.ACTION_COMMITTED.value,
        }:
            state.any_commit_in_exchange = True
        if status == "campaign_file_updated":
            state.settled_narration = resumed_result.get("player_facing_response")
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
            if state.settled_narration is not None:
                if not isinstance(state.settled_narration, str) or not state.settled_narration.strip():
                    raise CommandRejected(
                        "Persisted Campaign File disposition narration is invalid",
                        code="provider_tool_evidence_invalid",
                    )
                state.buffered_narration = state.settled_narration
                if state.buffered_response_blocks is None:
                    state.buffered_raw_response_blocks = None
            state.campaign_file_commit_in_exchange = True
            state.campaign_file_disposition_in_exchange = True
            if state.buffered_narration is not None:
                state.buffered_narration_needs_disposition = False
                state.campaign_file_narration_pending = False
            else:
                state.campaign_file_narration_pending = True
        if status == "campaign_file_unchanged":
            state.settled_narration = resumed_result.get("player_facing_response")
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
            if state.settled_narration is not None:
                if not isinstance(state.settled_narration, str) or not state.settled_narration.strip():
                    raise CommandRejected(
                        "Persisted Campaign File disposition narration is invalid",
                        code="provider_tool_evidence_invalid",
                    )
                state.buffered_narration = state.settled_narration
                if state.buffered_response_blocks is None:
                    state.buffered_raw_response_blocks = None
            state.campaign_file_disposition_in_exchange = True
            if state.buffered_narration is not None:
                state.buffered_narration_needs_disposition = False
                state.campaign_file_narration_pending = False
            else:
                state.campaign_file_narration_pending = True
        if status == CanonicalEventType.ACTION_COMMITTED.value:
            state.campaign_file_disposition_in_exchange = False
            state.campaign_file_narration_pending = False
            state.buffered_narration = None
            state.buffered_response_blocks = None
            state.buffered_raw_response_blocks = None
            state.buffered_narration_needs_disposition = False
        elif status not in {
            "rejected",
            CanonicalEventType.ACTION_FAILED.value,
            "campaign_file_updated",
            "campaign_file_unchanged",
        }:
            state.buffered_narration = None
            state.buffered_response_blocks = None
            state.buffered_raw_response_blocks = None
            state.buffered_narration_needs_disposition = False
    if state.ooc_ack_required and state.campaign_file_disposition_in_exchange and not (
        state.buffered_response_blocks is not None
        and any(block.get("kind") == "ooc" for block in state.buffered_response_blocks)
    ):
        raise CommandRejected(
            "Persisted OOC turn response omits its acknowledgement",
            code="provider_tool_evidence_invalid",
        )
    state.force_campaign_file_update = bool(
        (state.buffered_narration is not None or state.buffered_raw_response_blocks is not None)
        and state.buffered_narration_needs_disposition
    )
