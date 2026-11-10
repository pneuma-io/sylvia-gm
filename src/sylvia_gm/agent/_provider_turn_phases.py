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
from sylvia_gm.domain.session import SessionMessageKind
from typing import cast
import json
from ._provider_session import (
    _GM_SYSTEM_CONTRACT_VERSION,
    _action_sequence_narration_facts,
    _canonical_player_narration,
    _plain_json,
    _player_turn_text,
    _public_action_result,
    _saved_action_sequence_results,
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
