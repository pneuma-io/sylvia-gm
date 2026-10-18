"""Preparation-only requests must retain the player's next decision."""

import json
from dataclasses import replace

import pytest

from sylvia_gm.agent._provider_session import ProviderSessionAdapter, _model_tool_schema
from sylvia_gm.agent._session_loop import ProviderEventKind
from sylvia_gm.agent._stateless_mcp import ModelMechanicsTool
from sylvia_gm.domain.authority import (
    CanonicalEvent,
    CanonicalEventType,
    EventVisibility,
    ModelDecisionBoundary,
    ModelDecisionOwner,
    ModelToolBehavior,
)
from sylvia_gm.domain.session import SessionMessage, SessionMessageKind
from sylvia_gm.provider._base import ProviderStreamChunk
from tests.test_alpha_f4_provider_session_adapter import (
    _next_action_event,
    _OwnerProjection,
    _provider_input,
    _ScriptedProvider,
)

ARGUMENT = "player_intent_after_preparation"


def tool(name, behavior=ModelToolBehavior.OPTIONAL, *, narrative=False):
    return ModelMechanicsTool(
        package_id="synthetic.rules",
        package_version="1",
        package_sha256="a" * 64,
        name=name,
        capability="narrative_state.update_campaign_file" if narrative else "synthetic.preparation",
        description=f"Execute {name}.",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        category="narrative_state" if narrative else "mechanic",
        model_behavior=behavior,
    )


def call(name, arguments, identity):
    return [
        ProviderStreamChunk(
            tool_call_delta={
                "index": 0,
                "id": identity,
                "function": {"name": name, "arguments": json.dumps(arguments)},
            },
            finish_reason="tool_calls",
        )
    ]


def commit(owner, action):
    command = action.action_command
    assert command is not None
    owner.revision += 1
    owner.events = (
        *owner.events,
        CanonicalEvent(
            sequence=len(owner.events) + 1,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=command.action_id,
            safe_payload={"status": "committed", "result": {"status": "committed"}},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=len(owner.events) + 2,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )
    owner.boundary = ModelDecisionBoundary(
        owner=ModelDecisionOwner.PLAYER,
        actor_id="rift-player-character",
        actor_name="Mara Venn",
        actor_state={"hp": 12},
        campaign_revision=owner.revision,
    )


@pytest.mark.parametrize("restart", [False, True])
def test_preparation_only_can_narrate_without_inventing_an_action(restart):
    prepare = tool("start_preparation", ModelToolBehavior.LEAVES_PLAYER_INTENT_PENDING)
    attack = tool("resolve_attack", ModelToolBehavior.RESOLVES_PLAYER_INTENT)
    settle = tool("update_campaign_file", narrative=True)
    response = {
        "state_change": "unchanged",
        "state_change_summary": "Preparation is established.",
        "revised_player_facing_response": [
            {"kind": "narration", "text": "Initiative is established. Choose your first action."}
        ],
    }
    provider = _ScriptedProvider(
        [
            call(prepare.name, {ARGUMENT: "await_player_choice"}, "prepare"),
            call(settle.name, response, "settle"),
        ]
    )
    owner = _OwnerProjection()
    writes = []
    source = _provider_input(
        user_text="Begin combat and roll initiative. I will choose my first action using the map."
    )
    source = replace(
        source,
        catalog=(prepare,),
        catalog_supplier=lambda _: (prepare,) if owner.revision == 4 else (attack, settle),
        narrative_writer=lambda args: writes.append(args) or {"status": "campaign_file_unchanged"},
    )
    adapter = ProviderSessionAdapter(owner, provider, owner_id="preparation-test")
    events = iter(adapter.for_turn("turn-provider")(source))
    evidence = []
    for event in events:
        if event.kind is ProviderEventKind.ACTION:
            action = event
            break
        evidence.append(event)
    else:
        raise AssertionError("No preparation action")
    assert action.action_command.parameters == {}, "Provider-only intent leaked into Ruleset inputs"
    contract = next(
        e.evidence
        for e in evidence
        if e.evidence and e.evidence.get("kind") == "provider_mechanics_action_contract"
    )
    assert contract[ARGUMENT] == "await_player_choice"
    commit(owner, action)
    if restart:
        events.close()
        history = SessionMessage(
            session_id=source.session.session_id,
            sequence=2,
            message_id="saved-preparation-contract",
            kind=SessionMessageKind.EVIDENCE,
            content=contract,
        )
        source = replace(
            source, history=replace(source.history, messages=(*source.history.messages, history))
        )
        events = iter(
            ProviderSessionAdapter(owner, provider, owner_id="preparation-restart").for_turn(
                "turn-provider"
            )(source)
        )
    assert [e.text for e in events if e.kind is ProviderEventKind.TALK] == [
        response["revised_player_facing_response"][0]["text"]
    ]
    assert len(writes) == 1 and owner.revision == 5
    advertised = [v["function"]["name"] for v in provider.calls[-1][1]]
    assert any(name.endswith("_update_campaign_file") for name in advertised)
    assert not any(
        e.action_id
        and e.event_type is CanonicalEventType.ACTION_COMMITTED
        and e.action_id != action.action_command.action_id
        for e in owner.events
    )


def test_preparation_intent_schema_is_generic_and_does_not_change_engine_schema():
    prepare = tool("negotiate_start", ModelToolBehavior.LEAVES_PLAYER_INTENT_PENDING)
    projected = _model_tool_schema(prepare)
    assert projected["properties"][ARGUMENT]["enum"] == ["unresolved_action", "await_player_choice"]
    assert ARGUMENT not in prepare.input_schema["properties"]
    assert ARGUMENT not in _model_tool_schema(tool("resolve_attack"))["properties"]


@pytest.mark.parametrize("bad", [None, False, {}, "resolved", ""])
def test_invalid_preparation_scope_cannot_reach_engine(bad):
    prepare = tool("start_preparation", ModelToolBehavior.LEAVES_PLAYER_INTENT_PENDING)
    provider = _ScriptedProvider([call(prepare.name, {ARGUMENT: bad}, "bad")])
    owner = _OwnerProjection()
    source = replace(_provider_input(), catalog=(prepare,))
    events = ProviderSessionAdapter(owner, provider, owner_id="invalid-test").for_turn(
        "turn-provider"
    )(source)
    with pytest.raises(AssertionError, match="Unexpected scripted provider call"):
        for event in events:
            assert event.kind is not ProviderEventKind.ACTION, "Invalid intent reached engine"
    result = [json.loads(m["content"]) for m in provider.calls[-1][0] if m.get("role") == "tool"][
        -1
    ]
    assert (
        result["code"] == "provider_player_intent_scope_invalid"
        and result["state_changed"] is False
    )
    assert owner.revision == 4


def test_later_preparation_cannot_erase_an_unresolved_action():
    prepare = tool("start_preparation", ModelToolBehavior.LEAVES_PLAYER_INTENT_PENDING)
    attack = tool("resolve_attack", ModelToolBehavior.RESOLVES_PLAYER_INTENT)
    source = _provider_input(user_text="I attack the opponent.")
    provider = _ScriptedProvider(
        [
            call(prepare.name, {}, "initial"),
            call(prepare.name, {ARGUMENT: "await_player_choice"}, "bypass"),
        ]
    )
    owner = _OwnerProjection()
    source = replace(
        source,
        catalog=(prepare,),
        catalog_supplier=lambda _: (prepare,) if owner.revision == 4 else (prepare, attack),
    )
    events = iter(
        ProviderSessionAdapter(owner, provider, owner_id="pending-test").for_turn("turn-provider")(
            source
        )
    )
    first = _next_action_event(events)
    commit(owner, first)
    with pytest.raises(AssertionError, match="Unexpected scripted provider call"):
        for event in events:
            assert event.kind is not ProviderEventKind.ACTION, "Pending action was bypassed"
    results = [json.loads(m["content"]) for m in provider.calls[-1][0] if m.get("role") == "tool"]
    assert results[-1]["code"] == "provider_player_intent_scope_invalid"
    assert owner.revision == 5


@pytest.mark.parametrize("transport", ["chat_completions", "responses", "gemini_generate_content"])
def test_required_native_metadata_is_retained_without_a_tool_surface(transport):
    from types import SimpleNamespace

    from sylvia_gm.domain.errors import CommandRejected
    from sylvia_gm.provider._base import ModelInfo

    source = _provider_input()
    provider = _ScriptedProvider([])
    provider.model_info = ModelInfo(
        id="synthetic/model",
        name="Synthetic",
        provider_name="Synthetic",
        provider_key="synthetic",
        provider_mode="byok",
        provider_model_id="model",
        transport=transport,
    )
    reads = []

    def read(*args, **kwargs):
        reads.append((args, kwargs))
        return {
            "chat_completion_metadata": {
                "reasoning_details": [{"type": "opaque", "data": "synthetic"}]
            }
        }

    owner = SimpleNamespace(provider_continuation=read)
    adapter = ProviderSessionAdapter(owner, provider, owner_id="native-metadata-test")
    requirement = {
        "provider_id": source.session.provider_id,
        "model_id": source.session.model_id,
        "chat_completion_required": True,
    }
    message = {
        "role": "assistant",
        "content": "Saved answer",
        "_native_reference": {"response_id": "response", "requirement": requirement},
    }
    result = adapter._attach_native_continuation([message], source, has_tools=False)
    assert result[0]["reasoning_details"] == [{"type": "opaque", "data": "synthetic"}]
    assert len(reads) == 1 and reads[0][1] == {
        "provider_id": source.session.provider_id,
        "model_id": source.session.model_id,
    }
    owner.provider_continuation = lambda *args, **kwargs: None
    with pytest.raises(CommandRejected) as rejected:
        adapter._attach_native_continuation([message], source, has_tools=False)
    assert rejected.value.code == "provider_native_continuation_missing"
    reads.clear()
    owner.provider_continuation = read
    foreign = {
        **message,
        "_native_reference": {
            "response_id": "response",
            "requirement": {**requirement, "provider_id": "another-provider"},
        },
    }
    assert adapter._attach_native_continuation([foreign], source, has_tools=False) == [
        {"role": "assistant", "content": "Saved answer"}
    ]
    assert reads == []


def test_preparation_scope_cannot_be_attached_to_an_unmarked_operation():
    operation = tool("ordinary_resolution")
    provider = _ScriptedProvider(
        [call(operation.name, {ARGUMENT: "await_player_choice"}, "invalid")]
    )
    owner = _OwnerProjection()
    source = replace(_provider_input(), catalog=(operation,))
    events = ProviderSessionAdapter(owner, provider, owner_id="unmarked-test").for_turn(
        "turn-provider"
    )(source)
    with pytest.raises(AssertionError, match="Unexpected scripted provider call"):
        for event in events:
            assert event.kind is not ProviderEventKind.ACTION
    result = [json.loads(m["content"]) for m in provider.calls[-1][0] if m.get("role") == "tool"][
        -1
    ]
    assert result["code"] == "provider_player_intent_scope_invalid" and owner.revision == 4


def test_preparation_scope_name_collision_fails_clearly():
    operation = tool("custom_preparation", ModelToolBehavior.LEAVES_PLAYER_INTENT_PENDING)
    operation = replace(
        operation,
        input_schema={**operation.input_schema, "properties": {ARGUMENT: {"type": "integer"}}},
    )
    with pytest.raises(ValueError, match="collides"):
        _model_tool_schema(operation)
