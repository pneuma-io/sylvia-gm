"""Fresh player input reaches the GM before an exhausted NPC turn advances."""

from dataclasses import replace
from typing import cast

import pytest

from sylvia_gm.agent._provider_session import ProviderSessionAdapter
from sylvia_gm.agent._session_loop import SessionProviderInput
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ModelToolBehavior
from sylvia_gm.provider._base import ProviderStreamChunk
from tests.test_alpha_f4_provider_session_adapter import (
    _next_action_event,
    _OwnerProjection,
    _provider_input,
    _ScriptedProvider,
)


@pytest.mark.parametrize("continue_combat", [False, True])
def test_fresh_input_can_stop_before_exhausted_monster_bookkeeping(continue_combat):
    source = _provider_input(user_text="Stop immediately; the fallen contestant is defeated.")
    lifecycle = replace(
        source.catalog[0],
        name="finish_srd_monster_turn",
        capability="synthetic.finish",
        model_behavior=ModelToolBehavior.REQUIRED_ENGINE_TRANSITION,
        input_schema={"type": "object", "properties": {}, "required": []},
    )
    ending = replace(
        source.catalog[0],
        name="end_combat_encounter",
        capability="synthetic.end",
        input_schema={
            "type": "object",
            "properties": {"end_reason": {"type": "string"}},
            "required": ["end_reason"],
        },
    )
    chosen = "finish_srd_monster_turn" if continue_combat else "end_combat_encounter"
    args = "{}" if continue_combat else '{"end_reason":"fighting_stopped"}'
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "decision",
                        "function": {"name": chosen, "arguments": args},
                    },
                    finish_reason="tool_calls",
                )
            ]
        ]
    )
    owner = _OwnerProjection(
        state={
            "sylvia.srd-lite.bg3.entities": {
                "rift-player-character": {"hp": 0, "is_alive": True, "conditions": ["unconscious"]},
                "rift-scarred-hound": {"hp": 12, "is_alive": True},
            },
            "sylvia.srd-lite.bg3.combat": {
                "encounter_active": True,
                "turn_active": True,
                "active_actor": "rift-scarred-hound",
                "current_actor": "rift-scarred-hound",
                "active_actor_kind": "monster",
                "action_available": False,
            },
        }
    )
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(lifecycle, ending),
        control=source.control,
    )
    events = iter(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner), provider, owner_id="owner-provider"
        ).for_turn("turn-provider")(provider_input)
    )
    action = _next_action_event(events)
    assert action.action_command.tool_name == chosen
    assert action.action_command.parameters == (
        {} if continue_combat else {"end_reason": "fighting_stopped"}
    )
    assert len(provider.calls) == 1, "The fresh instruction must be read before automatic changes"
    assert (
        owner.revision == 4
        and owner.state["sylvia.srd-lite.bg3.entities"]["rift-player-character"]["hp"] == 0
    )
    assert any(
        "SYLVIA_GM_ENGINE_TURN_CONTROL" in str(message.get("content"))
        for message in provider.calls[0][0]
    )


def _interrupted_monster_context(provider, *, reference_reader=None, control=None):
    source = _provider_input(user_text="Stop the fight before another turn or Death Save.")
    lifecycle = replace(
        source.catalog[0],
        name="finish_srd_monster_turn",
        model_behavior=ModelToolBehavior.REQUIRED_ENGINE_TRANSITION,
        input_schema={"type": "object", "properties": {}, "required": []},
    )
    ending = replace(
        source.catalog[0],
        name="end_combat_encounter",
        input_schema={
            "type": "object",
            "properties": {"end_reason": {"type": "string"}},
            "required": ["end_reason"],
        },
    )
    reference = replace(
        source.catalog[0],
        name="read_campaign_reference",
        category="reference",
        input_schema={
            "type": "object",
            "properties": {"source": {"type": "string"}, "path": {"type": "string"}},
            "required": ["source", "path"],
        },
    )
    owner = _OwnerProjection(
        state={
            "sylvia.srd-lite.bg3.entities": {
                "rift-player-character": {"hp": 0, "is_alive": True, "conditions": ["unconscious"]},
                "rift-scarred-hound": {"hp": 12, "is_alive": True},
            },
            "sylvia.srd-lite.bg3.combat": {
                "encounter_active": True,
                "turn_active": True,
                "active_actor": "rift-scarred-hound",
                "current_actor": "rift-scarred-hound",
                "active_actor_kind": "monster",
                "action_available": False,
            },
        }
    )
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(lifecycle, ending, reference),
        control=control or source.control,
        reference_reader=reference_reader,
    )
    events = iter(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner), provider, owner_id="owner-provider"
        ).for_turn("turn-provider")(provider_input)
    )
    return owner, provider_input, events


def _stop_round(identity="stop"):
    return [
        ProviderStreamChunk(
            tool_call_delta={
                "index": 0,
                "id": identity,
                "function": {
                    "name": "end_combat_encounter",
                    "arguments": '{"end_reason":"fighting_stopped"}',
                },
            },
            finish_reason="tool_calls",
        )
    ]


def _reference_round():
    return [
        ProviderStreamChunk(
            tool_call_delta={
                "index": 0,
                "id": "read",
                "function": {
                    "name": "read_campaign_reference",
                    "arguments": '{"source":"plan","path":"act-1"}',
                },
            },
            finish_reason="tool_calls",
        )
    ]


def test_reference_only_call_keeps_stop_review_before_automatic_transition():
    references = []

    def reader(source, path):
        references.append((source, path))
        return {"text": "The bout stops when a contestant falls."}

    provider = _ScriptedProvider([_reference_round(), _stop_round()])
    owner, _, events = _interrupted_monster_context(provider, reference_reader=reader)
    action = _next_action_event(events)
    assert action.action_command.tool_name == "end_combat_encounter"
    assert len(provider.calls) == 2 and references == [("plan", "act-1")]
    assert owner.revision == 4 and owner.events == ()


def test_rejected_end_combat_keeps_stop_review_before_automatic_transition():
    provider = _ScriptedProvider([_stop_round("rejected-stop"), _stop_round("corrected-stop")])
    owner, _, events = _interrupted_monster_context(provider)
    first = _next_action_event(events)
    assert first.action_command.tool_name == "end_combat_encounter"
    first.action_feedback.rejection = {
        "status": "rejected",
        "code": "mechanics_validation_failed",
        "state_changed": False,
    }
    second = _next_action_event(events)
    assert second.action_command.tool_name == "end_combat_encounter"
    assert len(provider.calls) == 2 and owner.revision == 4 and owner.events == ()


def test_cancellation_and_fresh_retry_cannot_advance_before_stop_decision():
    from sylvia_gm.agent._session_loop import ProviderControl
    from sylvia_gm.domain.errors import CommandRejected

    control = ProviderControl()

    def reader(_source, _path):
        control.cancel()
        return {"text": "The bout should already be stopped."}

    provider = _ScriptedProvider([_reference_round()])
    owner, provider_input, events = _interrupted_monster_context(
        provider, reference_reader=reader, control=control
    )
    with pytest.raises(CommandRejected, match="cancel"):
        tuple(events)
    assert owner.revision == 4 and owner.events == () and len(provider.calls) == 1
    retry = _ScriptedProvider([_stop_round("retry-stop")])
    resumed = replace(provider_input, control=ProviderControl())
    action = _next_action_event(
        iter(
            ProviderSessionAdapter(
                cast(AuthorityOwner, owner), retry, owner_id="owner-provider"
            ).for_turn("turn-provider")(resumed)
        )
    )
    assert action.action_command.tool_name == "end_combat_encounter"
    assert len(retry.calls) == 1 and owner.revision == 4 and owner.events == ()
