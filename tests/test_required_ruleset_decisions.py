"""Required choices reach the provider; automatic transitions still cannot guess inputs."""

import json
from dataclasses import replace
from typing import cast

from sylvia_gm.agent._provider_session import ProviderSessionAdapter
from sylvia_gm.agent._session_loop import ProviderEventKind, SessionProviderInput
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ModelToolBehavior
from sylvia_gm.provider._base import ProviderStreamChunk
from tests.test_alpha_f4_provider_session_adapter import (
    _next_action_event,
    _OwnerProjection,
    _provider_input,
    _ScriptedProvider,
    _tool_operations,
)


def test_required_ruleset_decision_is_offered_without_an_automatic_action():
    source = _provider_input()
    choice = replace(
        source.catalog[0],
        name="choose_reaction",
        input_schema={
            "type": "object",
            "properties": {"take_reaction": {"type": "boolean"}},
            "required": ["take_reaction"],
            "additionalProperties": False,
        },
        model_behavior=ModelToolBehavior.REQUIRED_RULESET_DECISION,
    )
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    content="The saved movement is waiting for its reaction decision.",
                    finish_reason="stop",
                )
            ]
        ]
    )
    request = SessionProviderInput(
        session=source.session, history=source.history, catalog=(choice,), control=source.control
    )
    owner = _OwnerProjection()
    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner),
            provider,
            owner_id="required-choice",
        ).for_turn("turn-provider")(request)
    )
    assert len(provider.calls) == 1
    assert _tool_operations(provider.calls[0][1] or []) == ["choose_reaction"]
    assert not any(event.kind is ProviderEventKind.ACTION for event in events)
    assert owner.revision == 4
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        "The saved movement is waiting for its reaction decision.",
    ]


def test_required_ruleset_decision_executes_only_the_model_selected_arguments():
    source = _provider_input()
    choice = replace(
        source.catalog[0],
        name="choose_reaction",
        input_schema={
            "type": "object",
            "properties": {"take_reaction": {"type": "boolean"}},
            "required": ["take_reaction"],
            "additionalProperties": False,
        },
        model_behavior=ModelToolBehavior.REQUIRED_RULESET_DECISION,
    )
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "selected-reaction",
                        "function": {
                            "name": "choose_reaction",
                            "arguments": json.dumps({"take_reaction": False}),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ]
        ]
    )
    request = SessionProviderInput(
        session=source.session, history=source.history, catalog=(choice,), control=source.control
    )
    stream = iter(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="required-choice",
        ).for_turn("turn-provider")(request)
    )
    try:
        action = _next_action_event(stream)
        assert len(provider.calls) == 1
        assert action.action_command.tool_name == "choose_reaction"
        assert action.action_command.parameters == {"take_reaction": False}
    finally:
        stream.close()
