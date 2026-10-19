"""A small hidden-token Ruleset exercises isolated decisions without Mahjong declarations."""

import json
import sqlite3
from dataclasses import replace

import pytest
import yaml

from sylvia_gm.agent._actor_decisions import run_actor_decision
from sylvia_gm.agent._provider_session import ProviderSessionAdapter
from sylvia_gm.agent._session_loop import CampaignSessionLoop, ProviderEventKind, SessionRunRequest
from sylvia_gm.agent._stateless_mcp import ModelMechanicsTool
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.application.package_materialization import materialize_package_archive
from sylvia_gm.builder._ruleset_source import compile_ruleset_source
from sylvia_gm.domain.authority import (
    Participant,
    ParticipantValidationStatus,
    StateValue,
    WriterRole,
    thaw_json_value,
)
from sylvia_gm.domain.errors import AuthorityError
from sylvia_gm.domain.provider_records import ProviderPrivateRecord
from sylvia_gm.domain.session import SessionContext, SessionMessage, SessionMessageKind
from sylvia_gm.provider._anthropic import AnthropicMessagesTransport
from sylvia_gm.provider._base import ModelInfo, ProviderStreamChunk
from sylvia_gm.provider._gemini import GeminiProvider
from sylvia_gm.provider._openai_compat import ChatCompletionsTransport, ResponsesTransport
from tests.test_alpha_f4_provider_session_adapter import (
    _campaign_file_tool,
    _provider_input,
    _ScriptedProvider,
)

NS = "example.hidden-tokens"


@pytest.mark.parametrize("declaration", [None, {}, {"instructions": "unclosed", "foreign": True}])
def test_isolated_optin_requires_a_complete_closed_declaration(declaration):
    from sylvia_gm.runtime._schema_validator import _validate_model_decision_boundary

    boundary = token_ruleset()["model_decision_boundary"]
    boundary["isolated_participant"] = declaration
    assert _validate_model_decision_boundary(boundary)


def token_ruleset():
    actor = {
        "name": "actor",
        "type": "entity_id",
        "source": "state",
        "from_state": "state.actor",
        "resolve": "entity",
    }
    parameters = [actor, {"name": "choice", "type": "string", "source": "choice"}]
    effects = [
        {"require": {"condition": "choice == actor.offers[0].id", "error": "Stale token choice"}},
        {"require": {"condition": "not state.blocked", "error": "The token is blocked"}},
        {"set": {"field": "state.counter", "value": "state.counter + 1"}},
        {"set": {"field": "state.actor", "value": "definitions.order[state.counter]"}},
        {"set": {"field": "state.public.counter", "value": "state.counter"}},
        {"set": {"field": "_seats", "value": "definitions.seats"}},
        {
            "for_each": {
                "collection": "_seats",
                "as": "_seat",
                "do": [
                    {"call": "_refresh", "params": {"actor": "{_seat}"}},
                ],
            }
        },
        {"publish": {"fields": {"message": "'A public token moved.'", "counter": "state.counter"}}},
    ]
    family = {
        "parameters": parameters,
        "pre_conditions": [],
        "effects": effects,
        "narrative": {},
        "metadata": {"model_behavior": "resolves_player_intent"},
    }
    refresh = {
        "parameters": [{**actor, "source": "choice", "from_state": None}],
        "pre_conditions": [],
        "narrative": {},
        "effects": [
            {"set": {"field": "actor.observation.public_counter", "value": "state.counter"}},
            {
                "set": {
                    "field": "actor.offers",
                    "value": [
                        {
                            "id": "_actor_id + ':' + text(state.counter)",
                            "label": "'Move a token'",
                            "operation": "'advance'",
                            "arguments": {"choice": "_actor_id + ':' + text(state.counter)"},
                        }
                    ],
                }
            },
        ],
    }
    refresh["parameters"][0].pop("from_state")
    schema = {
        "$id": NS + ".player",
        "type": "object",
        "required": ["name", "control"],
        "properties": {
            "name": {"type": "string"},
            "control": {"type": "string", "enum": ["player"]},
        },
        "additionalProperties": False,
    }
    return {
        "format": 1,
        "id": NS,
        "version": "1.0.0",
        "label": "Hidden tokens",
        "publisher": "Test",
        "mechanic_families": {"advance": family, "refresh": refresh},
        "public_actions": [
            {
                "name": "advance",
                "family": "advance",
                "description": "Move a token",
                "configure": {},
                "capability": NS + ".play",
                "state_predicates": [],
                "turn_statuses": ["open"],
                "writers": ["dm"],
                "reference_availability": "included",
                "gameplay_resolution": "engine_family",
                "deterministic_part": None,
                "gm_part": None,
            }
        ],
        "private_mechanics": [
            {"name": "_refresh", "family": "refresh", "description": "Refresh", "configure": {}}
        ],
        "definitions": {
            "order": ["a", "a", "b", "c", "human", "a", "human"],
            "seats": ["a", "b", "c", "human"],
        },
        "participant_artifacts": [
            {
                "kind": "player",
                "schema_id": schema["$id"],
                "schema": schema,
                "entity_state_schema": {
                    "type": "object",
                    "properties": {},
                    "additionalProperties": False,
                },
                "initial_state_calculations": [],
            }
        ],
        "state_schemas": [
            {"key": key, "schema": {"type": "object"}, "references": [], "visibility": "engine"}
            for key in ("state", "entities")
        ],
        "operating_guide": (
            "Narrate public token moves; isolated participants choose their own offers."
        ),
        "model_decision_boundary": {
            "active_actor": {"namespace": NS, "key": "state.actor"},
            "actor_entities": {"namespace": NS, "key": "entities"},
            "participant_control": {"field": "control", "player_values": ["player"]},
            "default_owner": "gm",
            "isolated_participant": {
                "instructions": "Choose exactly one offered token move. Keep your token secret.",
                "observation_path": ["observation"],
                "persona_path": ["persona"],
                "actions_path": ["offers"],
                "public_observation": {"namespace": NS, "key": "state.public"},
            },
        },
    }


@pytest.fixture
def isolated_game(tmp_path, request):
    declaration = token_ruleset()
    options = getattr(request, "param", None)
    pending = isinstance(options, dict) and options.get("housekeeping", False)
    if pending:
        declaration["mechanic_families"]["housekeeping"] = {
            "parameters": [],
            "pre_conditions": [],
            "narrative": {},
            "metadata": {"model_behavior": "required_engine_transition"},
            "effects": [
                {"set": {"field": "state.pending", "value": False}},
                {"set": {"field": "state.counter", "value": 1}},
                {"set": {"field": "state.public.counter", "value": 1}},
                {"call": "_refresh", "params": {"actor": "{state.actor}"}},
                {"publish": {"fields": {"message": "'HOUSEKEEPING_PUBLIC_RESULT'", "counter": 1}}},
            ],
        }
        declaration["public_actions"].append(
            {
                **declaration["public_actions"][0],
                "name": "housekeeping",
                "family": "housekeeping",
                "state_predicates": [{"namespace": NS, "key": "state.pending", "equals": True}],
            }
        )
    compiled = compile_ruleset_source(yaml.safe_dump(declaration).encode())
    database = tmp_path / "tokens.sqlite3"
    with AuthorityOwner.open(database, "isolated-test", disconnect_grace_seconds=1.0) as owner:
        with materialize_package_archive((compiled.draft,)) as artifact:
            manifest = artifact.manifest
            declaration = manifest.participant_artifacts[0]
            candidate = owner.install_package_archive(artifact.archive, install_id="tokens")
        entities = {
            seat: {
                "name": seat,
                "control": "player" if seat == "human" else "gm",
                "observation": {"secret": seat.upper() + "_HAND_SENTINEL", "public_counter": 0},
                "persona": seat.upper() + "_PERSONA_SENTINEL",
                "offers": [
                    {
                        "id": seat + ":0",
                        "label": "Move a token",
                        "operation": "advance",
                        "arguments": {"choice": seat + ":0"},
                    }
                ],
            }
            for seat in ("a", "b", "c", "human")
        }
        owner.create_campaign(
            CampaignCreate(
                campaign_id="tokens",
                display_name="Hidden tokens",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="turn",
                participants=(
                    Participant(
                        participant_id="human",
                        kind="player",
                        package_id=NS,
                        package_version=manifest.version,
                        package_sha256=manifest.content_sha256,
                        schema_id=declaration.schema_id,
                        schema_sha256=declaration.schema_sha256,
                        validation_status=ParticipantValidationStatus.VALIDATED,
                        data={"name": "Human", "control": "player"},
                    ),
                ),
                initial_state=(
                    StateValue(NS, "entities", entities),
                    StateValue(
                        NS,
                        "state",
                        {
                            "actor": "a",
                            "counter": 0,
                            "blocked": options is True,
                            "pending": pending,
                            "public": {"counter": 0},
                        },
                    ),
                ),
            )
        )
        session = owner.create_agent_session(
            campaign_id="tokens",
            session_id="gm-tokens",
            role=WriterRole.DM,
            provider_id="synthetic",
            model_id="synthetic",
        )
        yield owner, session, database


def test_envelope_and_command_are_bound_to_the_authoritative_actor_and_revision(isolated_game):
    owner, session, _ = isolated_game
    request = owner.actor_decision_request("tokens", turn_id="turn")
    assert request.actor_id == "a" and request.revision == 0
    assert request.observation["secret"] == "A_HAND_SENTINEL"
    assert request.persona == "A_PERSONA_SENTINEL"
    assert "B_HAND_SENTINEL" not in json.dumps(thaw_json_value(request.observation))
    _, gm_scope = owner.model_context_snapshot("tokens")
    assert "A_HAND_SENTINEL" not in json.dumps(thaw_json_value(gm_scope.actor_state))
    with pytest.raises(TypeError):
        request.observation["secret"] = "tampered"
    for bad in ("b:0", "unknown"):
        with pytest.raises(AuthorityError):
            owner.actor_decision_command(
                request, choice_id=bad, session_id=session.session_id, owner_id="isolated-test"
            )
    with pytest.raises(AuthorityError):
        owner.actor_decision_command(
            replace(request, actor_id="b"),
            choice_id="a:0",
            session_id=session.session_id,
            owner_id="isolated-test",
        )
    command = owner.actor_decision_command(
        request, choice_id="a:0", session_id=session.session_id, owner_id="isolated-test"
    )
    receipt = owner.execute_action(command)
    assert receipt.status.value == "committed" and receipt.resulting_revision == 1
    assert owner.execute_action(command) == receipt
    next_request = owner.actor_decision_request("tokens", turn_id="turn")
    assert next_request.observation["public_counter"] == 1
    assert next_request.actions[0].identity == "a:1"
    desktop = owner.desktop_snapshot("tokens")
    assert all(not action.parameters for action in desktop.actions)
    assert all(not event.safe_payload.get("parameters") for event in desktop.events)
    with pytest.raises(AuthorityError):
        owner.actor_decision_command(
            request, choice_id="a:0", session_id=session.session_id, owner_id="isolated-test"
        )


class IsolatedFlowProvider(_ScriptedProvider):
    def __init__(self, transport, model):
        super().__init__([])
        self.wire = []
        self.actor_calls = []
        self.before_proposal = None
        self.proposal = None
        self.require_second_gm_history = False
        self.ooc_only = False
        self.transport = {
            "anthropic_messages": AnthropicMessagesTransport,
            "chat_completions": ChatCompletionsTransport,
            "responses": ResponsesTransport,
        }.get(transport, ChatCompletionsTransport)()
        self.model_info = ModelInfo(
            id="synthetic",
            provider_model_id=model,
            name=model,
            provider_name="Synthetic",
            provider_key="synthetic",
            provider_mode="byok",
            transport=transport,
        )

    async def generate_stream(self, messages, tools=None, timeout=120, **kwargs):
        del timeout
        self.calls.append((messages.copy(), tools))
        if self.model_info.transport == "gemini_generate_content":
            # This pure production formatter does not initialize a client or credentials.
            wire = GeminiProvider.provider_request_representation(self, messages, tools, kwargs)
        else:
            wire = self.transport.format_request(
                self.model_info.wire_model_id, messages, tools, True
            )
        self.wire.append(wire)
        names = [tool["function"]["name"] for tool in tools or []]
        if self.ooc_only:
            assert "select_actor_action" not in names
            assert not any(
                name.endswith("_advance") or name.endswith("_housekeeping") for name in names
            )
            assert "HAND_SENTINEL" not in json.dumps(wire)
            operation = next(name for name in names if name.endswith("_update_campaign_file"))
            arguments = {
                "revised_player_facing_response": [
                    {"kind": "ooc", "text": "The table stays paused while we discuss."}
                ]
            }
            call_id, marker = "ooc-gm-call", "GM"
        elif "select_actor_action" in names:
            assert names == ["select_actor_action"]
            envelope = json.loads(messages[-1]["content"])
            seat = envelope["actor"]["id"]
            self.actor_calls.append(seat)
            rendered = json.dumps(wire)
            assert seat.upper() + "_HAND_SENTINEL" in rendered
            assert seat.upper() + "_PERSONA_SENTINEL" in rendered
            assert "GM_PRIVATE_INSTRUCTIONS_SENTINEL" not in rendered
            for kind in ("NATIVE", "SIGNATURE", "OPAQUE"):
                assert "GM_" + kind + "_SENTINEL" not in rendered
            assert "GM_PLAN_SENTINEL" not in rendered
            assert "PLAYER_CONVERSATION_SENTINEL" not in rendered
            for other in {"a", "b", "c"} - {seat}:
                for kind in ("HAND", "PERSONA", "NATIVE", "SIGNATURE", "OPAQUE"):
                    assert other.upper() + "_" + kind + "_SENTINEL" not in rendered
            if self.actor_calls == ["a", "a"]:
                assert "A_NATIVE_SENTINEL" in rendered
                assert "actor-call-1" in rendered
                assert "A public token moved." in rendered
            if seat == "a" and self.actor_calls.count("a") > 1:
                assert rendered.count("actor-call-1") == 2
            operation = "select_actor_action"
            arguments = {"action_id": envelope["actions"][0]["id"]}
            call_id = "actor-call-" + str(len(self.calls))
            marker = seat.upper()
        else:
            rendered = json.dumps(wire)
            if self.require_second_gm_history:
                assert "GM_PUBLIC_TALK_SENTINEL" in rendered
                assert "PLAYER_SECOND_TURN_SENTINEL" in rendered
                assert "GM_NATIVE_SENTINEL" in rendered
                assert "GM_OPAQUE_SENTINEL" in rendered
            assert "GM_PRIVATE_INSTRUCTIONS_SENTINEL" in rendered
            assert "GM_PLAN_SENTINEL" in rendered
            assert "PLAYER_CONVERSATION_SENTINEL" in rendered
            assert "A public token moved." in rendered
            for seat in ("a", "b", "c"):
                for kind in ("HAND", "PERSONA", "NATIVE", "SIGNATURE", "OPAQUE"):
                    assert seat.upper() + "_" + kind + "_SENTINEL" not in rendered
            operation = next(name for name in names if name.endswith("_update_campaign_file"))
            arguments = {
                "revised_player_facing_response": [
                    {
                        "kind": "narration",
                        "text": "The public tokens moved. Your turn.",
                    }
                ]
            }
            call_id, marker = "gm-call", "GM"
        if self.before_proposal is not None:
            callback, self.before_proposal = self.before_proposal, None
            callback()
        if self.proposal is not None:
            arguments = self.proposal
        metadata = None
        if self.model_info.transport == "chat_completions":
            metadata = {
                "reasoning_fields": {"reasoning_content": marker + "_NATIVE_SENTINEL"},
                "reasoning_details": [{"data": marker + "_OPAQUE_SENTINEL"}],
            }
        elif self.model_info.transport == "responses":
            metadata = {
                "responses_output": [
                    {
                        "type": "reasoning",
                        "id": marker + "_SIGNATURE_SENTINEL",
                        "summary": [{"type": "summary_text", "text": marker + "_NATIVE_SENTINEL"}],
                        "encrypted_content": marker + "_OPAQUE_SENTINEL",
                    },
                    {
                        "type": "function_call",
                        "call_id": call_id,
                        "name": operation,
                        "arguments": json.dumps(arguments),
                    },
                ]
            }
        elif self.model_info.transport == "gemini_generate_content":
            metadata = {
                "gemini_parts": [
                    {
                        "thought": True,
                        "text": marker + "_NATIVE_SENTINEL",
                        "thoughtSignature": marker + "_OPAQUE_SENTINEL",
                    },
                    {
                        "functionCall": {"id": call_id, "name": operation, "args": arguments},
                        "thoughtSignature": marker + "_SIGNATURE_SENTINEL",
                    },
                ],
                "gemini_call_ids": [call_id],
            }
        yield ProviderStreamChunk(reasoning_content=marker + "_NATIVE_SENTINEL")
        yield ProviderStreamChunk(
            tool_call_delta={
                "index": 0,
                "id": call_id,
                "function": {
                    "name": operation,
                    "arguments": json.dumps(arguments),
                },
            },
            anthropic_content=[
                {
                    "type": "thinking",
                    "thinking": marker + "_NATIVE_SENTINEL",
                    "signature": marker + "_SIGNATURE_SENTINEL",
                },
                {"type": "redacted_thinking", "data": marker + "_OPAQUE_SENTINEL"},
                {"type": "tool_use", "id": call_id, "name": operation, "input": arguments},
            ]
            if self.model_info.transport == "anthropic_messages"
            else None,
            chat_completion_metadata=metadata,
            finish_reason="tool_calls",
        )


def gm_input(owner, session):
    base = _provider_input(user_text="PLAYER_CONVERSATION_SENTINEL: watch the token moves.")

    def current_catalog():
        return (
            *tuple(
                ModelMechanicsTool(
                    package_id=tool.package_id,
                    package_version=tool.package_version,
                    package_sha256=tool.package_sha256,
                    name=tool.name,
                    capability=tool.capability,
                    input_schema=tool.input_schema,
                    description=tool.description,
                    model_behavior=tool.model_behavior,
                )
                for tool in owner.catalog("tokens")
            ),
            _campaign_file_tool(allow_unchanged=True),
        )

    return replace(
        base,
        session=session,
        history=SessionContext(
            session=session,
            messages=(replace(base.history.messages[0], session_id=session.session_id),),
        ),
        catalog=current_catalog(),
        catalog_supplier=lambda _discovery: current_catalog(),
        narrative_writer=lambda _parameters: {
            "status": "campaign_file_unchanged",
            "campaign_revision": owner.snapshot("tokens").revision,
        },
    )


@pytest.mark.parametrize(
    "transport,model",
    [
        ("anthropic_messages", "minimax-m3"),
        ("chat_completions", "deepseek-v3.2"),
        ("chat_completions", "kimi-k3"),
        ("responses", "gpt-synthetic"),
        ("gemini_generate_content", "gemini-synthetic"),
    ],
)
def test_three_npcs_keep_native_contexts_separate_and_preserve_the_gm(
    isolated_game, transport, model
):
    owner, session, database = isolated_game
    provider = IsolatedFlowProvider(transport, model)
    adapter = ProviderSessionAdapter(
        owner,
        provider,
        owner_id="isolated-test",
        gm_system_prompt="GM_PRIVATE_INSTRUCTIONS_SENTINEL: narrate the public token game.",
        campaign_plan={"structure": [{"id": "one", "title": "GM_PLAN_SENTINEL"}], "objectives": []},
    )
    talks = []
    first = gm_input(owner, session)
    public_events = []
    saved_evidence = []
    for order, event in enumerate(adapter.for_turn("turn")(first)):
        if event.kind is ProviderEventKind.ACTION:
            receipt = owner.execute_action(event.action_command)
            assert receipt.status.value == "committed"
        if event.kind is ProviderEventKind.TALK:
            talks.append(event.text)
        if event.kind is ProviderEventKind.EVIDENCE:
            saved_evidence.append(
                SessionMessage(
                    session_id=session.session_id,
                    sequence=len(saved_evidence) + 2,
                    message_id=event.message_id,
                    kind=SessionMessageKind.EVIDENCE,
                    content=event.evidence,
                )
            )
        if event.kind is ProviderEventKind.PRIVATE_CONTINUATION:
            owner.append_private_provider_record(
                ProviderPrivateRecord(
                    session.session_id,
                    "synthetic-gm-first",
                    event.response_id,
                    "turn",
                    first.history.messages[0].message_id,
                    order,
                    session.provider_id,
                    session.model_id,
                    "continuation",
                    event.text,
                )
            )
        if (
            event.kind is ProviderEventKind.EVIDENCE
            and event.evidence.get("kind") == "isolated_participant_result"
        ):
            public_events.append(event)
    owner.finish_private_provider_attempt(session.session_id, "synthetic-gm-first", completed=True)
    assert provider.actor_calls == ["a", "a", "b", "c"]
    assert talks == ["The public tokens moved. Your turn."]
    assert owner.snapshot("tokens").revision == 4
    assert len(public_events) == 4
    reconstructed = replace(
        first,
        history=SessionContext(
            session=session,
            messages=(
                *first.history.messages,
                *(
                    SessionMessage(
                        session_id=session.session_id,
                        sequence=index + 2,
                        message_id=event.message_id,
                        kind=SessionMessageKind.EVIDENCE,
                        content=event.evidence,
                    )
                    for index, event in enumerate(public_events)
                ),
            ),
        ),
    )
    rebuilt = json.dumps(adapter._messages(reconstructed))
    assert rebuilt.count("A public token moved.") == 4
    assert "A_HAND_SENTINEL" not in rebuilt and "a:0" not in rebuilt
    owner.execute_action(
        ActionCommand(
            campaign_id="tokens",
            action_id="human-selection",
            idempotency_key="human-selection",
            turn_id="turn",
            owner_id="isolated-test",
            writer=WriterRole.DM,
            expected_revision=4,
            package_id=NS,
            tool_name="advance",
            parameters={"choice": "human:4"},
        )
    )
    first.control.cancel()
    second = gm_input(owner, session)
    old_user = second.history.messages[0]
    second = replace(
        second,
        history=SessionContext(
            session=session,
            messages=(
                old_user,
                *saved_evidence,
                SessionMessage(
                    session_id=session.session_id,
                    sequence=len(saved_evidence) + 2,
                    message_id="public-gm-talk",
                    kind=SessionMessageKind.TALK,
                    content={"text": "GM_PUBLIC_TALK_SENTINEL"},
                ),
                replace(
                    old_user,
                    sequence=len(saved_evidence) + 3,
                    message_id="second-player-turn",
                    content={
                        **old_user.content,
                        "text": "PLAYER_SECOND_TURN_SENTINEL",
                        "attempt_id": "attempt-2",
                    },
                ),
            ),
        ),
    )
    provider.require_second_gm_history = True
    for event in adapter.for_turn("turn")(second):
        if event.kind is ProviderEventKind.ACTION:
            owner.execute_action(event.action_command)
    assert provider.actor_calls == ["a", "a", "b", "c", "a"]
    resumed = next(
        wire for wire in reversed(provider.wire) if "select_actor_action" in json.dumps(wire)
    )
    assert "A_NATIVE_SENTINEL" in json.dumps(resumed)
    assert owner.snapshot("tokens").revision == 6
    with sqlite3.connect(database) as connection:
        stored = connection.execute(
            "SELECT payload FROM provider_private_records WHERE response_id LIKE 'actor-context-%'"
        ).fetchall()
    assert len(stored) == 5
    assert all(len(json.loads(row[0])["actor_context"]["messages"]) == 3 for row in stored)


def test_optin_keeps_public_gm_dialogue_without_replaying_unowned_native_history(isolated_game):
    owner, session, _ = isolated_game
    source = gm_input(owner, session)
    basis = source.history.messages[0].message_id
    saved = [
        SessionMessage(
            session_id=session.session_id,
            sequence=2,
            message_id="legacy-npc-call",
            kind=SessionMessageKind.EVIDENCE,
            content={
                "kind": "provider_tool_calls",
                "basis_message_id": basis,
                "inference_id": "legacy-npc",
                "assistant": {
                    "role": "assistant",
                    "content": "A_LEGACY_PRIVATE_SENTINEL",
                    "tool_calls": [
                        {
                            "id": "legacy-call",
                            "type": "function",
                            "function": {
                                "name": "advance",
                                "arguments": '{"choice":"A_HAND_SENTINEL"}',
                            },
                        }
                    ],
                },
            },
        ),
        SessionMessage(
            session_id=session.session_id,
            sequence=3,
            message_id="legacy-npc-result",
            kind=SessionMessageKind.EVIDENCE,
            content={
                "kind": "provider_tool_results",
                "basis_message_id": basis,
                "inference_id": "legacy-npc",
                "results": [
                    {
                        "role": "tool",
                        "tool_call_id": "legacy-call",
                        "content": "A_LEGACY_PRIVATE_SENTINEL",
                    }
                ],
            },
        ),
    ]
    source = replace(
        source, history=SessionContext(session=session, messages=(*source.history.messages, *saved))
    )
    adapter = ProviderSessionAdapter(owner, _ScriptedProvider([]), owner_id="isolated-test")
    messages = adapter._messages(source)
    assert "PLAYER_CONVERSATION_SENTINEL" in json.dumps(messages)
    assert "A_LEGACY_PRIVATE_SENTINEL" not in json.dumps(messages)


def actor_events(owner, session, provider):
    source = gm_input(owner, session)
    adapter = ProviderSessionAdapter(owner, provider, owner_id="isolated-test")
    request = owner.actor_decision_request("tokens", turn_id="turn")
    return iter(
        run_actor_decision(
            owner,
            provider,
            source,
            request,
            owner_id="isolated-test",
            stream=adapter._stream_round,
        )
    ), source


def test_participant_tool_contract_stays_identical_when_current_offers_change(isolated_game):
    owner, session, _ = isolated_game
    provider = IsolatedFlowProvider("chat_completions", "deepseek-v3.2")
    for _ in range(2):
        events, _ = actor_events(owner, session, provider)
        try:
            event = next(events)
            owner.execute_action(event.action_command)
            list(events)
        finally:
            events.close()
    first_messages, first_tools = provider.calls[0]
    second_messages, second_tools = provider.calls[1]
    assert json.dumps(first_tools, sort_keys=True) == json.dumps(second_tools, sort_keys=True)
    assert json.loads(first_messages[-1]["content"])["actions"][0]["id"] == "a:0"
    assert json.loads(second_messages[-1]["content"])["actions"][0]["id"] == "a:1"
    assert first_messages[0] == second_messages[0]
    assert second_messages[1] == first_messages[-1]


@pytest.mark.asyncio
async def test_fresh_gm_state_follows_history_without_entering_chatgpt_base_instructions(
    isolated_game, tmp_path,
):
    from sylvia_gm.provider._chatgpt import ChatGPTProvider
    from tests.test_pes82_chatgpt_failures import SyntheticRuntime, provider_for

    owner, session, _ = isolated_game
    captured = []

    class Runtime(SyntheticRuntime):
        def call(self, method, params):
            captured.append((method, json.loads(json.dumps(params))))
            return super().call(method, params)

    def connect(*_args, **_kwargs):
        return Runtime([{"method": "turn/completed", "params": {"turn": {"status": "completed"}}}])

    model = provider_for(SyntheticRuntime([]), tmp_path).model_info
    provider = ChatGPTProvider(tmp_path, model, connection_factory=connect)
    adapter = ProviderSessionAdapter(owner, provider, owner_id="isolated-test",
        gm_system_prompt="FIXED_GM_INSTRUCTIONS")
    source = gm_input(owner, session)
    for index in range(2):
        messages = adapter._messages(source)
        tools, _ = adapter._provider_tools(source.catalog)
        await provider.generate(messages, tools=tools)
        if index == 0:
            request = owner.actor_decision_request("tokens", turn_id="turn")
            owner.execute_action(owner.actor_decision_command(request, choice_id="a:0",
                session_id=session.session_id, owner_id="isolated-test"))
    starts = [params for method, params in captured if method == "thread/start"]
    injected = [params["items"] for method, params in captured if method == "thread/inject_items"]
    assert json.dumps(starts[0]["dynamicTools"], sort_keys=True) == json.dumps(
        starts[1]["dynamicTools"], sort_keys=True)
    assert starts[0]["baseInstructions"] == starts[1]["baseInstructions"]
    assert "SYLVIA_GM_FRESH_CAMPAIGN_STATE" not in starts[0]["baseInstructions"]
    for items in injected:
        assert items[-1]["content"][0]["text"].startswith("SYLVIA_GM_FRESH_CAMPAIGN_STATE")
        assert "PLAYER_CONVERSATION_SENTINEL" in json.dumps(items[:-1])
        assert "HAND_SENTINEL" not in json.dumps(items)
    assert injected[0][-1] != injected[1][-1]


@pytest.mark.parametrize("route", ["chatgpt", "openrouter"])
def test_real_serializers_interleave_owned_contexts_and_reopen(isolated_game, tmp_path, route):
    """Capture actual App Server RPC or SDK HTTP JSON, with no external inference."""
    import httpx

    from sylvia_gm.provider._chatgpt import ChatGPTProvider
    from sylvia_gm.provider._openai_compat import OpenAICompatProvider
    from tests.test_pes82_chatgpt_failures import SyntheticRuntime, provider_for

    owner, session, database = isolated_game
    frames = []

    def proposed(messages, tools):
        if any(tool["function"]["name"] == "select_actor_action" for tool in tools):
            return json.loads(messages[-1]["content"])["actions"][0]["id"]
        return None

    if route == "openrouter":
        def handle(request):
            body = json.loads(request.content)
            frames.append(body)
            action = proposed(body["messages"], body.get("tools", []))
            delta = {"content": "GM_PUBLIC_TALK_SENTINEL"} if action is None else {
                "tool_calls": [{"index": 0, "id": "choice-" + str(len(frames)),
                    "type": "function", "function": {"name": "select_actor_action",
                        "arguments": json.dumps({"action_id": action})}}]}
            event = {"id": "synthetic", "created": 1, "object": "chat.completion.chunk",
                "model": "synthetic", "choices": [{"index": 0, "delta": delta,
                    "finish_reason": "stop" if action is None else "tool_calls"}]}
            return httpx.Response(200, headers={"content-type": "text/event-stream"},
                text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n")

        provider = OpenAICompatProvider(
            base_url="https://openrouter.ai/api/v1", api_key="synthetic",
            model_info=ModelInfo("synthetic", "Test", "OpenRouter", "openrouter", "byok"),
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)))
    else:
        class Runtime(SyntheticRuntime):
            def call(self, method, params):
                if method == "thread/start":
                    self.frame = {"baseInstructions": params["baseInstructions"],
                                  "tools": params["dynamicTools"]}
                if method == "thread/inject_items":
                    self.frame["messages"] = params["items"]
                if method == "turn/start":
                    frames.append(json.loads(json.dumps(self.frame)))
                    names = [tool["name"] for namespace in self.frame["tools"]
                             for tool in namespace["tools"]]
                    if "select_actor_action" in names:
                        envelope = json.loads(self.frame["messages"][-1]["content"][0]["text"])
                        self.events = [{"method": "item/tool/call", "params": {
                            "namespace": "sylvia", "tool": "select_actor_action",
                            "callId": "choice-" + str(len(frames)),
                            "arguments": {"action_id": envelope["actions"][0]["id"]}}}]
                    else:
                        self.events = [{"method": "item/agentMessage/delta", "params": {
                            "delta": "GM_PUBLIC_TALK_SENTINEL"}}, {"method": "turn/completed",
                            "params": {"turn": {"status": "completed"}}}]
                return super().call(method, params)

        model = provider_for(SyntheticRuntime([]), tmp_path).model_info
        provider = ChatGPTProvider(tmp_path, model,
            connection_factory=lambda *_args, **_kwargs: Runtime([]))

    adapter = ProviderSessionAdapter(owner, provider, owner_id="isolated-test",
        gm_system_prompt="GM_PRIVATE_INSTRUCTIONS_SENTINEL")
    source = gm_input(owner, session)
    # Advance the fixture's first repeated A decision so the captured order is GM,A,B,C,GM,A.
    request = owner.actor_decision_request("tokens", turn_id="turn")
    owner.execute_action(owner.actor_decision_command(request, choice_id="a:0",
        session_id=session.session_id, owner_id="isolated-test"))

    def gm():
        tools, _ = adapter._provider_tools(source.catalog)
        list(adapter._stream_round(adapter._messages(source), tools, control=source.control,
                                   provider_input=source))

    def actor():
        request = owner.actor_decision_request("tokens", turn_id="turn")
        events = iter(run_actor_decision(owner, provider, source, request,
            owner_id="isolated-test", stream=adapter._stream_round))
        try:
            event = next(events)
            owner.execute_action(event.action_command)
            list(events)
        finally:
            events.close()

    gm()
    owner.append_agent_message(session_id=session.session_id, message_id="saved-gm-talk",
        kind=SessionMessageKind.TALK, content={"text": "GM_PUBLIC_TALK_SENTINEL"})
    for _ in range(3):
        actor()
    saved_talk = owner.session_context(session.session_id).messages
    source = replace(source, history=SessionContext(session=session,
        messages=(*source.history.messages, *saved_talk)))
    gm_id = adapter._gm_request_context_id(source)
    gm()
    owner.execute_action(ActionCommand(campaign_id="tokens", action_id="human-selection",
        idempotency_key="human-selection", turn_id="turn", owner_id="isolated-test",
        writer=WriterRole.DM, expected_revision=4, package_id=NS, tool_name="advance",
        parameters={"choice": "human:4"}))
    owner.close()
    with AuthorityOwner.open(database, "isolated-test") as reopened:
        owner = reopened
        session = owner.session_activity_basis("gm-tokens").session
        source = replace(source, session=session)
        adapter = ProviderSessionAdapter(owner, provider, owner_id="isolated-test",
            gm_system_prompt="GM_PRIVATE_INSTRUCTIONS_SENTINEL")
        assert adapter._gm_request_context_id(source) == gm_id
        actor()
    assert len(frames) == 6
    for index, seat in ((1, "a"), (2, "b"), (3, "c"), (5, "a")):
        rendered = json.dumps(frames[index])
        assert seat.upper() + "_HAND_SENTINEL" in rendered
        assert "GM_PRIVATE_INSTRUCTIONS_SENTINEL" not in rendered
        assert "PLAYER_CONVERSATION_SENTINEL" not in rendered
        for other in "abc":
            if other != seat:
                assert other.upper() + "_HAND_SENTINEL" not in rendered
    for index in (0, 4):
        for seat in "abc":
            assert seat.upper() + "_HAND_SENTINEL" not in json.dumps(frames[index])
        assert "PLAYER_CONVERSATION_SENTINEL" in json.dumps(frames[index])
    assert "GM_PUBLIC_TALK_SENTINEL" in json.dumps(frames[4])
    assert frames[1]["tools"] == frames[5]["tools"]
    if route == "openrouter":
        assert frames[0]["session_id"] == frames[4]["session_id"] == gm_id
        assert frames[1]["session_id"] == frames[5]["session_id"]
        assert len({frames[i]["session_id"] for i in range(4)}) == 4
        assert all("provider" not in frame and "_request_context_id" not in frame
                   for frame in frames)
        assert frames[5]["messages"][1] == frames[1]["messages"][-1]
        prior_call, prior_receipt = frames[5]["messages"][2:4]
        assert prior_call["tool_calls"][0]["id"] == prior_receipt["tool_call_id"]
        assert json.loads(prior_receipt["content"])["status"] == "committed"
    else:
        assert frames[0]["baseInstructions"] == frames[4]["baseInstructions"]
        assert frames[1]["baseInstructions"] == frames[5]["baseInstructions"]
        assert "SYLVIA_GM_FRESH_CAMPAIGN_STATE" not in frames[4]["baseInstructions"]
    assert "a:1" in json.dumps(frames[5]) and "a:5" in json.dumps(frames[5])


@pytest.mark.parametrize("isolated_game", [{"housekeeping": True}], indirect=True)
def test_required_transition_refreshes_the_actor_before_any_npc_request(isolated_game):
    owner, session, _ = isolated_game
    old = owner.actor_decision_request("tokens", turn_id="turn")
    provider = IsolatedFlowProvider("anthropic_messages", "minimax-m3")
    adapter = ProviderSessionAdapter(
        owner,
        provider,
        owner_id="isolated-test",
        gm_system_prompt="GM_PRIVATE_INSTRUCTIONS_SENTINEL",
        campaign_plan={"structure": [{"id": "one", "title": "GM_PLAN_SENTINEL"}], "objectives": []},
    )
    events = iter(adapter.for_turn("turn")(gm_input(owner, session)))
    try:
        event = next(event for event in events if event.kind is ProviderEventKind.ACTION)
        assert provider.calls == []
        assert event.action_command.tool_name == "housekeeping"
        receipt = owner.execute_action(event.action_command)
        assert receipt.status.value == "committed", receipt.result
        assert receipt.result["context"]["message"] == "HOUSEKEEPING_PUBLIC_RESULT"
        current = owner.actor_decision_request("tokens", turn_id="turn")
        assert current.actor_id == "a" and current.observation["public_counter"] == 1
        assert current.actions[0].identity == "a:1"
        for request in (old, current):
            with pytest.raises(AuthorityError):
                owner.actor_decision_command(
                    request,
                    choice_id="a:0",
                    session_id=session.session_id,
                    owner_id="isolated-test",
                )
        for event in events:
            if event.kind is ProviderEventKind.ACTION:
                owner.execute_action(event.action_command)
        first = json.loads(provider.calls[0][0][-1]["content"])
        assert first["observation"]["public_counter"] == 1
        assert first["actions"][0]["id"] == "a:1"
        assert provider.actor_calls == ["a", "b", "c"]
        assert "HOUSEKEEPING_PUBLIC_RESULT" in json.dumps(provider.wire[-1])
        assert owner.snapshot("tokens").revision == 4
    finally:
        events.close()


@pytest.mark.parametrize("committed", [False, True])
def test_interrupted_actor_reopens_from_its_own_complete_receipts(isolated_game, committed):
    owner, session, database = isolated_game
    provider = IsolatedFlowProvider("anthropic_messages", "minimax-m3")
    events, source = actor_events(owner, session, provider)
    event = next(events)
    if committed:
        owner.execute_action(event.action_command)
    source.control.cancel()
    events.close()
    assert owner.snapshot("tokens").revision == int(committed)
    owner.close()
    with AuthorityOwner.open(database, "isolated-test") as reopened:
        session = reopened.session_activity_basis("gm-tokens").session
        provider = IsolatedFlowProvider("anthropic_messages", "minimax-m3")
        events, _ = actor_events(reopened, session, provider)
        try:
            event = next(events)
            wire = json.dumps(provider.wire[0])
            assert ("A_NATIVE_SENTINEL" in wire) is committed
            assert ("A_SIGNATURE_SENTINEL" in wire) is committed
            assert ("A_OPAQUE_SENTINEL" in wire) is committed
            assert event.action_command.parameters["choice"] == ("a:1" if committed else "a:0")
            reopened.execute_action(event.action_command)
            list(events)
            assert reopened.snapshot("tokens").revision == 1 + int(committed)
        finally:
            events.close()


@pytest.mark.parametrize("stop_pending", [False, True])
def test_ooc_and_stop_redirect_leave_pending_npcs_until_gameplay_resumes(
    isolated_game, stop_pending
):
    owner, session, _ = isolated_game
    loop = CampaignSessionLoop(owner)

    def session_provider(provider):
        adapter = ProviderSessionAdapter(
            owner,
            provider,
            owner_id="isolated-test",
            gm_system_prompt="GM_PRIVATE_INSTRUCTIONS_SENTINEL",
            campaign_plan={
                "structure": [{"id": "one", "title": "GM_PLAN_SENTINEL"}],
                "objectives": [],
            },
        )

        def run(source):
            # Keep the real session/Stop/catalog path, with a synthetic Campaign File writer.
            supplier = source.current_catalog
            return adapter.for_turn("turn")(
                replace(
                    source,
                    catalog_supplier=lambda discovery: (
                        *(
                            tool
                            for tool in supplier(discovery)
                            if tool.category != "narrative_state"
                        ),
                        _campaign_file_tool(allow_unchanged=True),
                    ),
                    narrative_writer=lambda _parameters: {
                        "status": "campaign_file_unchanged",
                        "campaign_revision": owner.snapshot("tokens").revision,
                    },
                )
            )

        return run

    interruption_id = None
    if stop_pending:
        pending = IsolatedFlowProvider("anthropic_messages", "minimax-m3")
        stopped = []
        pending.before_proposal = lambda: stopped.append(loop.stop(session.session_id))
        result = loop.run(
            SessionRunRequest(
                session.session_id,
                "turn",
                "pending",
                "start-input",
                "PLAYER_CONVERSATION_SENTINEL: Continue the token game.",
            ),
            session_provider(pending),
        )
        assert result.interruption == stopped[0].interruption
        assert pending.actor_calls == ["a"]
        assert owner.snapshot("tokens").revision == 0
        assert owner.session_context(session.session_id).session.status.value == "stopped"
        interruption_id = result.interruption.interruption_id
        owner.resume_agent_session(session.session_id)

    ooc = IsolatedFlowProvider("anthropic_messages", "minimax-m3")
    ooc.ooc_only = True
    loop.run(
        SessionRunRequest(
            session.session_id,
            "turn",
            "ooc",
            "ooc-input",
            "",
            "Pause the game. I want to discuss the table before anyone acts.",
            redirect_interruption_id=interruption_id,
        ),
        session_provider(ooc),
    )
    assert ooc.actor_calls == [] and len(ooc.wire) == 1
    assert owner.snapshot("tokens").revision == 0
    request = owner.actor_decision_request("tokens", turn_id="turn")
    assert request.actor_id == "a" and request.actions[0].identity == "a:0"
    assert any(
        item.kind.value == "talk" and "paused" in str(item.safe_payload)
        for item in owner.player_projection("tokens")
    )

    resumed = IsolatedFlowProvider("anthropic_messages", "minimax-m3")
    loop.run(
        SessionRunRequest(
            session.session_id,
            "turn",
            "resume",
            "resume-input",
            "PLAYER_CONVERSATION_SENTINEL: Continue the token game now.",
        ),
        session_provider(resumed),
    )
    assert resumed.actor_calls == ["a", "a", "b", "c"]
    assert owner.snapshot("tokens").revision == 4


@pytest.mark.parametrize(
    "proposal",
    [
        {"action_id": "b:0"},
        {"action_id": "unknown"},
        {"action_id": "a:0", "actor": "b"},
        {"action_id": 4},
    ],
)
def test_npc_proposals_cannot_choose_another_actor_or_unoffered_arguments(isolated_game, proposal):
    owner, session, _ = isolated_game
    provider = IsolatedFlowProvider("chat_completions", "deepseek-v3.2")
    provider.proposal = proposal
    events, _ = actor_events(owner, session, provider)
    try:
        with pytest.raises(AuthorityError):
            list(events)
        assert owner.snapshot("tokens").revision == 0
    finally:
        events.close()


def test_late_npc_completion_cannot_act_on_the_next_seat(isolated_game):
    owner, session, _ = isolated_game
    provider = IsolatedFlowProvider("chat_completions", "kimi-k3")

    def advance_twice():
        for choice in ("a:0", "a:1"):
            request = owner.actor_decision_request("tokens", turn_id="turn")
            command = owner.actor_decision_command(
                request,
                choice_id=choice,
                session_id=session.session_id,
                owner_id="isolated-test",
            )
            owner.execute_action(command)

    provider.before_proposal = advance_twice
    events, _ = actor_events(owner, session, provider)
    try:
        with pytest.raises(AuthorityError):
            list(events)
        assert owner.snapshot("tokens").revision == 2
        assert owner.actor_decision_request("tokens", turn_id="turn").actor_id == "b"
    finally:
        events.close()


def test_provider_change_during_a_decision_discards_the_proposal(isolated_game):
    owner, session, _ = isolated_game
    provider = IsolatedFlowProvider("chat_completions", "deepseek-v3.2")
    provider.before_proposal = lambda: setattr(
        provider,
        "model_info",
        replace(provider.model_info, provider_model_id="kimi-k3"),
    )
    events, _ = actor_events(owner, session, provider)
    try:
        with pytest.raises(AuthorityError):
            list(events)
        assert owner.snapshot("tokens").revision == 0
    finally:
        events.close()


def test_new_model_reconstructs_actor_only_context_without_incompatible_native_replay(
    isolated_game,
):
    owner, session, _ = isolated_game
    first = IsolatedFlowProvider("chat_completions", "deepseek-v3.2")
    events, _ = actor_events(owner, session, first)
    event = next(events)
    owner.execute_action(event.action_command)
    list(events)
    second = IsolatedFlowProvider("chat_completions", "kimi-k3")
    events, _ = actor_events(owner, session, second)
    try:
        event = next(events)
        wire = json.dumps(second.wire[0])
        assert "A_HAND_SENTINEL" in wire and "A_PERSONA_SENTINEL" in wire
        assert "A_NATIVE_SENTINEL" not in wire and "A_OPAQUE_SENTINEL" not in wire
        owner.execute_action(event.action_command)
        list(events)
        assert owner.snapshot("tokens").revision == 2
    finally:
        events.close()


@pytest.mark.parametrize("isolated_game", [True], indirect=True)
def test_engine_rejects_an_offered_action_when_its_mechanics_disallow_it(isolated_game):
    owner, session, _ = isolated_game
    provider = IsolatedFlowProvider("chat_completions", "deepseek-v3.2")
    events, _ = actor_events(owner, session, provider)
    try:
        event = next(events)
        with pytest.raises(AuthorityError, match="blocked"):
            owner.execute_action(event.action_command)
        with pytest.raises(AuthorityError, match="not committed"):
            list(events)
    finally:
        events.close()
    assert owner.snapshot("tokens").revision == 0
    assert provider.actor_calls == ["a"]


def test_cancellation_during_inference_discards_the_proposal(isolated_game):
    owner, session, _ = isolated_game
    provider = IsolatedFlowProvider("chat_completions", "deepseek-v3.2")
    events, source = actor_events(owner, session, provider)
    provider.before_proposal = source.control.cancel
    with pytest.raises(AuthorityError):
        list(events)
    assert owner.snapshot("tokens").revision == 0


def test_binding_rejects_wrong_turn_provider_and_stopped_session(isolated_game):
    owner, session, _ = isolated_game
    request = owner.actor_decision_request("tokens", turn_id="turn")
    for invalid, provider in (
        (replace(request, turn_id="other"), "synthetic"),
        (request, "different-provider"),
    ):
        with pytest.raises(AuthorityError):
            owner.actor_decision_command(
                invalid,
                choice_id="a:0",
                session_id=session.session_id,
                owner_id="isolated-test",
                provider_id=provider,
            )
    owner.stop_agent_session(session.session_id)
    with pytest.raises(AuthorityError):
        owner.actor_decision_command(
            request, choice_id="a:0", session_id=session.session_id, owner_id="isolated-test"
        )


def test_post_commit_gm_recovery_receives_only_the_published_result(isolated_game):
    owner, session, _ = isolated_game
    receipts = []
    for _ in range(4):
        request = owner.actor_decision_request("tokens", turn_id="turn")
        command = owner.actor_decision_command(
            request,
            choice_id=request.actions[0].identity,
            session_id=session.session_id,
            owner_id="isolated-test",
        )
        receipts.append(owner.execute_action(command))
    receipt = replace(
        receipts[-1], result={**receipts[-1].result, "engine_private": "B_HAND_SENTINEL"}
    )
    source = replace(
        gm_input(owner, session),
        recovery_kind="post_mechanics_narration",
        retry_action_receipt=receipt,
    )
    provider = IsolatedFlowProvider("anthropic_messages", "minimax-m3")
    adapter = ProviderSessionAdapter(
        owner,
        provider,
        owner_id="isolated-test",
        gm_system_prompt="GM_PRIVATE_INSTRUCTIONS_SENTINEL",
        campaign_plan={"structure": [{"id": "one", "title": "GM_PLAN_SENTINEL"}], "objectives": []},
    )
    list(adapter.for_turn("turn")(source))
    assert len(provider.wire) == 1
    assert "B_HAND_SENTINEL" not in json.dumps(provider.wire)
    assert owner.snapshot("tokens").revision == 4


@pytest.mark.asyncio
async def test_compaction_notes_and_exact_native_tail_stay_with_their_context_owner():
    from sylvia_gm.agent._working_context import WorkingContextProvider
    from sylvia_gm.provider._base import ProviderResponse
    from tests.test_working_context import Model, history, pair

    messages = history()
    tail = pair(99)
    tail[0]["anthropic_content"] = [
        {"type": "thinking", "thinking": "A_NATIVE_SENTINEL", "signature": "A_SIGNATURE_SENTINEL"},
        {"type": "redacted_thinking", "data": "A_OPAQUE_SENTINEL"},
    ]
    messages.extend(tail)
    saved = []

    async def save(payload):
        saved.append(payload)

    actor_owner = {"kind": "participant", "context_id": "campaign-session-package-model-actor-a"}
    wrapper = WorkingContextProvider(
        Model(responses=[ProviderResponse(content="A_OWN_NOTE")] * 30),
        save=save,
        record_owner=actor_owner,
    )
    projected = await wrapper.prepare(messages, force=True)
    assert saved and saved[0]["context_owner"] == actor_owner
    assert "A_OWN_NOTE" in json.dumps(projected)
    assert projected[-2:] == tail
    assert "A_NATIVE_SENTINEL" not in json.dumps(saved)
    for selected in (
        actor_owner,
        {"kind": "participant", "context_id": "actor-b"},
        {"kind": "participant", "context_id": "actor-a-other-model"},
        {"kind": "gm", "campaign_id": "tokens"},
    ):
        reopened = WorkingContextProvider(Model(), records=saved, save=save, record_owner=selected)
        restored = reopened.project(messages)
        assert ("A_OWN_NOTE" in json.dumps(restored)) is (selected == actor_owner)
        assert restored[-2:] == tail


@pytest.mark.parametrize("interrupted", [False, True])
def test_actor_thinking_uses_the_private_inspection_sink(isolated_game, interrupted):
    owner, session, _ = isolated_game

    class PartialProvider(IsolatedFlowProvider):
        async def generate_stream(self, messages, **kwargs):
            async for chunk in super().generate_stream(messages, **kwargs):
                yield chunk
                if interrupted:
                    raise RuntimeError("Synthetic interrupted response")

    provider = PartialProvider("anthropic_messages", "minimax-m3")
    events, _ = actor_events(owner, session, provider)
    try:
        if interrupted:
            with pytest.raises(RuntimeError, match="interrupted"):
                next(events)
        else:
            event = next(events)
            owner.execute_action(event.action_command)
            list(events)
    finally:
        events.close()
    index = owner.private_provider_index(session.session_id)
    assert len(index) == 1
    assert index[0]["status"] == ("interrupted" if interrupted else "completed")
    assert "SENTINEL" not in json.dumps(index)
    thinking = owner.inspect_provider_thinking(
        session.session_id, index[0]["attempt_id"], index[0]["response_id"]
    )
    assert thinking == "A_NATIVE_SENTINEL"
    assert "OPAQUE" not in thinking
