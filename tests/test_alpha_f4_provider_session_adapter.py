"""Production provider-to-canonical-session adapter probes."""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import threading
import time
from collections.abc import AsyncIterator, Callable, Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import pytest

from sylvia_gm.agent._builder_session import _campaign_plan_example
from sylvia_gm.agent._provider_session import (
    ProviderSessionAdapter,
    _bounded_markdown_section,
    _bounded_reference_excerpt,
    _derive_mechanics_parameters,
    _dm_system_prompt,
    _forced_single_tool_call_from_json,
    _gm_decision_control_message,
    _mechanics_narration_facts,
    _missing_required_arguments,
    _native_content_for_text_operation,
    _normalize_state_references,
    _project_response_blocks,
    _require_checked_campaign_file_narration,
    _resolve_provider_tool_alias,
    _validated_raw_response_blocks,
)
from sylvia_gm.agent._session_loop import (
    ProviderControl,
    ProviderEvent,
    ProviderEventKind,
    SessionProviderInput,
    _mechanics_rejection_feedback,
)
from sylvia_gm.agent._stateless_mcp import (
    ModelMechanicsTool,
    _campaign_file_update_description,
    _safe_error,
)
from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.campaign_library_selection import (
    materialize_library_party,
    resolve_campaign_library_selection,
)
from sylvia_gm.application.campaign_plan_sources import campaign_plan_sources
from sylvia_gm.application.reusable_plans import save_reusable_campaign_plan
from sylvia_gm.domain.authority import (
    ActionReceipt,
    ActionStatus,
    CampaignContinuityView,
    CanonicalEvent,
    CanonicalEventType,
    EventVisibility,
    ModelDecisionBoundary,
    ModelDecisionOwner,
    ModelToolBehavior,
    NarrationStream,
    PackageLock,
    ProseChannel,
    RetryNarrationInput,
    WriterRole,
    owned_payload,
)
from sylvia_gm.domain.errors import (
    CommandRejected,
    MechanicsRejected,
    MechanicsValidationIssue,
)
from sylvia_gm.domain.session import (
    SessionContext,
    SessionMessage,
    SessionMessageKind,
    SessionRecord,
)
from sylvia_gm.provider._admin_audit import AdminAuditedProvider
from sylvia_gm.provider._anthropic import AnthropicMessagesTransport
from sylvia_gm.provider._base import (
    ModelInfo,
    Provider,
    ProviderResponse,
    ProviderStreamChunk,
)
from sylvia_gm.provider._exceptions import ProviderTimeoutError
from sylvia_gm.provider._openai_compat import (
    ChatCompletionsTransport,
    OpenAICompatProvider,
    _InlineReasoningFilter,
)
from tests.test_builder_library import install_character_packages


class _ScriptedProvider(Provider):
    def __init__(self, rounds: list[list[ProviderStreamChunk]]) -> None:
        super().__init__()
        self.rounds = rounds
        self.calls: list[tuple[list[dict[str, Any]], list[dict[str, Any]] | None]] = []
        self.request_kwargs: list[dict[str, object]] = []
        self.cancelled = threading.Event()

    async def generate(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        stream: bool = False,
        timeout: float = 60.0,
        **kwargs: object,
    ) -> ProviderResponse:
        raise NotImplementedError

    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        del timeout
        self.calls.append((messages.copy(), tools))
        self.request_kwargs.append(dict(kwargs))
        if not self.rounds:
            tail: list[str] = []
            for message in messages[-5:]:
                role = str(message.get("role", ""))
                if role == "tool":
                    result = json.loads(str(message.get("content", "{}")))
                    tail.append(f"tool:{result.get('status')}:{result.get('code')}")
                elif role == "system":
                    tail.append(f"system:{str(message.get('content', '')).split(' ', 1)[0]}")
                else:
                    tail.append(role)
            surfaces = [
                [tool["function"]["name"] for tool in surface or []]
                for _call_messages, surface in self.calls
            ]
            raise AssertionError(
                f"Unexpected scripted provider call {len(self.calls)} after {tail!r}; "
                f"surfaces={surfaces!r}"
            )
        chunks = self.rounds.pop(0)
        try:
            for chunk in chunks:
                await asyncio.sleep(0)
                yield chunk
        finally:
            self.cancelled.set()

    async def close(self) -> None:
        return None


class _BlockingProvider(_ScriptedProvider):
    async def generate_stream(
        self,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None = None,
        timeout: float = 120.0,
        **kwargs: object,
    ) -> AsyncIterator[ProviderStreamChunk]:
        self.calls.append((messages.copy(), tools))
        try:
            await asyncio.sleep(10)
            yield ProviderStreamChunk(content="MUST NOT ARRIVE")
        finally:
            self.cancelled.set()


def _next_action_event(events: object) -> ProviderEvent:
    """Advance through durable tool evidence to the next executable action."""

    for event in cast(Iterable[ProviderEvent], events):
        if event.kind is ProviderEventKind.ACTION:
            return event
    raise AssertionError("Provider exchange did not emit an action")


def _tool_operations(tools: Iterable[Mapping[str, Any]]) -> list[str]:
    """Read canonical operation names from deterministic provider aliases."""

    return [re.sub(r"^tool_[0-9a-f]{12}_", "", str(tool["function"]["name"])) for tool in tools]


def test_provider_tool_rounds_share_one_async_runtime() -> None:
    class LoopAffineProvider(_ScriptedProvider):
        def __init__(self) -> None:
            super().__init__([])
            self.observed_loops: list[asyncio.AbstractEventLoop] = []

        async def generate_stream(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            timeout: float = 120.0,
            **kwargs: object,
        ) -> AsyncIterator[ProviderStreamChunk]:
            self.request_kwargs.append(dict(kwargs))
            del messages, tools, timeout, kwargs
            current_loop = asyncio.get_running_loop()
            self.observed_loops.append(current_loop)
            if current_loop is not self.observed_loops[0]:
                raise RuntimeError("async provider client crossed event loops")
            yield ProviderStreamChunk(content="round complete", finish_reason="stop")

    provider = LoopAffineProvider()
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, object()),
        provider,
        owner_id="owner-provider-loop",
    )
    control = ProviderControl()
    first = tuple(adapter._stream_round([], [], control=control))  # noqa: SLF001
    second = tuple(adapter._stream_round([], [], control=control))  # noqa: SLF001

    assert [chunk.content for chunk in first] == ["round complete"]
    assert [chunk.content for chunk in second] == ["round complete"]
    assert len(provider.observed_loops) == 2
    assert provider.observed_loops[0] is provider.observed_loops[1]
    assert provider.request_kwargs == [{"max_tokens": 4096}, {"max_tokens": 4096}]


@pytest.mark.parametrize(
    ("transport_name", "configured_limit", "expected_limit"),
    [
        ("anthropic_messages", 131_072, 131_072),
        ("anthropic_messages", 8_192, 8_192),
        ("chat_completions", 8_192, 8_192),
        ("anthropic_messages", 2_048, 2_048),
        ("chat_completions", 2_048, 2_048),
    ],
)
def test_dm_respects_the_configured_output_allowance(
    transport_name: str,
    configured_limit: int,
    expected_limit: int,
) -> None:
    provider = _ScriptedProvider(
        [[ProviderStreamChunk(content="Opening complete.", finish_reason="stop")]]
    )
    provider.model_info = ModelInfo(
        id="opencode_go/minimax-m3",
        name="MiniMax M3",
        provider_name="OpenCode Go",
        provider_key="opencode_go",
        provider_mode="byok",
        max_output_tokens=configured_limit,
        transport=cast(Any, transport_name),
        provider_model_id="minimax-m3",
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, object()),
        provider,
        owner_id="owner-minimax-headroom",
    )

    chunks = tuple(adapter._stream_round([], [], control=ProviderControl()))  # noqa: SLF001

    assert [chunk.content for chunk in chunks] == ["Opening complete."]
    expected_kwargs: dict[str, object] = {"max_tokens": expected_limit}
    if transport_name == "anthropic_messages":
        expected_kwargs["thinking"] = {"type": "adaptive"}
    assert provider.request_kwargs == [expected_kwargs]
    wire_transport = (
        AnthropicMessagesTransport()
        if transport_name == "anthropic_messages"
        else ChatCompletionsTransport()
    )
    wire_request = wire_transport.format_request(
        "minimax-m3",
        [{"role": "user", "content": "Begin."}],
        None,
        True,
        **provider.request_kwargs[0],
    )
    assert wire_request["max_tokens"] == expected_limit


def test_anthropic_single_required_tool_transaction_uses_zero_temperature() -> None:
    provider = _ScriptedProvider([[ProviderStreamChunk(content="{}", finish_reason="stop")]])
    provider.model_info = ModelInfo(
        id="opencode_go/minimax-m3",
        name="MiniMax M3",
        provider_name="OpenCode Go",
        provider_key="opencode_go",
        provider_mode="byok",
        transport="anthropic_messages",
        provider_model_id="minimax-m3",
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, object()),
        provider,
        owner_id="owner-minimax-required-tool",
    )
    tools = [
        {
            "type": "function",
            "function": {
                "name": "update_campaign_file",
                "description": "Settle the Campaign File.",
                "parameters": {"type": "object", "properties": {}},
            },
        }
    ]

    tuple(
        adapter._stream_round(  # noqa: SLF001
            [],
            tools,
            control=ProviderControl(),
            tool_choice="required",
        )
    )

    assert provider.request_kwargs == [
        {
            "max_tokens": 4096,
            "thinking": {"type": "adaptive"},
            "tool_choice": "required",
            "temperature": 0,
        }
    ]


def test_provider_tool_alias_resolution_stays_within_the_current_catalog() -> None:
    end_turn = ModelMechanicsTool(
        package_id="synthetic.rules",
        package_version="1",
        package_sha256="a" * 64,
        name="end_combat_turn",
        capability="synthetic.combat",
        description="End the current combat turn.",
        input_schema={"type": "object", "properties": {}},
    )
    aliases = {"tool_3_end_combat_turn": end_turn}

    assert _resolve_provider_tool_alias("tool_3_end_combat_turn", aliases) == (
        "tool_3_end_combat_turn",
        end_turn,
    )
    assert _resolve_provider_tool_alias("tool_0_end_combat_turn", aliases) is None
    assert _resolve_provider_tool_alias("end_combat_turn", aliases) == (
        "tool_3_end_combat_turn",
        end_turn,
    )
    assert _resolve_provider_tool_alias("resolve_combat_attack", aliases) is None

    duplicate = replace(end_turn, package_id="other.synthetic.rules")
    ambiguous = {
        **aliases,
        "tool_4_end_combat_turn": duplicate,
    }
    assert _resolve_provider_tool_alias("end_combat_turn", ambiguous) is None


def test_provider_tool_aliases_are_stable_across_catalog_order_and_restart() -> None:
    first = ModelMechanicsTool(
        package_id="synthetic.one",
        package_version="1",
        package_sha256="a" * 64,
        name="resolve",
        capability="synthetic.one.resolve",
        description="Resolve one.",
        input_schema={"type": "object", "properties": {}},
    )
    duplicate_name = replace(
        first,
        package_id="synthetic.two",
        package_sha256="b" * 64,
        capability="synthetic.two.resolve",
    )

    def projected(catalog: tuple[ModelMechanicsTool, ...]) -> dict[str, ModelMechanicsTool]:
        adapter = ProviderSessionAdapter(
            cast(AuthorityOwner, object()), _ScriptedProvider([]), owner_id="owner-alias"
        )
        return adapter._provider_tools(catalog)[1]  # noqa: SLF001

    original = projected((first, duplicate_name))
    reordered = projected((duplicate_name, first))
    subset_after_restart = projected((duplicate_name,))
    assert {alias for alias, tool in original.items() if tool == first} == {
        alias for alias, tool in reordered.items() if tool == first
    }
    duplicate_alias = next(alias for alias, tool in original.items() if tool == duplicate_name)
    assert (
        next(alias for alias, tool in reordered.items() if tool == duplicate_name)
        == duplicate_alias
    )
    assert next(iter(subset_after_restart)) == duplicate_alias
    assert duplicate_alias != next(alias for alias, tool in original.items() if tool == first)
    assert len(duplicate_alias) <= 64


def test_application_boundary_control_remains_trusted_on_anthropic_wire() -> None:
    boundary = ModelDecisionBoundary(
        owner=ModelDecisionOwner.GM,
        actor_id="npc",
        actor_name="Tamsin",
        actor_state={},
        active_side_id=None,
        focused_actor_id=None,
        eligible_actors=(),
        campaign_revision=4,
    )
    player = {"role": "user", "content": "I keep searching."}
    control = _gm_decision_control_message(boundary)
    request = AnthropicMessagesTransport().format_request(
        "minimax-m3", [player, control], None, True, max_tokens=4096
    )

    assert control["role"] == "system"
    assert request["messages"][-1] == player
    assert "SYLVIA_GM_DECISION_REQUEST" in str(request["system"])


def test_parameterless_tool_preserves_invented_arguments_for_ordinary_rejection() -> None:
    tool = ModelMechanicsTool(
        package_id="synthetic.rules",
        package_version="1",
        package_sha256="a" * 64,
        name="begin_turn",
        capability="synthetic.turn",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
    )
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-invented-argument",
                        "function": {
                            "name": "begin_turn",
                            "arguments": '{"description":"Begin Daniel turn"}',
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [ProviderStreamChunk(content="The call was malformed.", finish_reason="stop")],
        ]
    )
    source = _provider_input()
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(tool,),
        control=source.control,
    )
    stream = iter(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    action = _next_action_event(stream)

    assert action.kind is ProviderEventKind.ACTION
    assert action.action_command is not None
    assert action.action_command.parameters == {"description": "Begin Daniel turn"}
    assert action.action_feedback is not None
    action.action_feedback.rejection = {
        "status": "rejected",
        "code": "mechanics_validation_failed",
        "validation": {"field_paths": ["description"]},
        "state_changed": False,
    }
    with pytest.raises(CommandRejected) as captured:
        tuple(stream)
    assert captured.value.code == "provider_tool_correction_abandoned"
    assert len(provider.calls) == 2


@pytest.mark.parametrize("optional_properties", [{}, {"lasting_state": {"type": "string"}, "hound_bite_choices": {"type": "array"}}])
def test_required_engine_transition_executes_without_a_model_inference(optional_properties) -> None:
    required = ModelMechanicsTool(
        package_id="synthetic.rules",
        package_version="1",
        package_sha256="a" * 64,
        name="death_saving_throw",
        capability="synthetic.death-save",
        description="Resolve the required death saving throw.",
        input_schema={
            "type": "object",
            "properties": optional_properties,
            "required": [],
            "additionalProperties": False,
        },
        model_behavior=ModelToolBehavior.REQUIRED_ENGINE_TRANSITION,
    )
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content="Daniel clings to life.", finish_reason="stop")],
        ]
    )
    owner = _OwnerProjection()
    source = _provider_input(user_text="Continue.")
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(required,),
        control=source.control,
        catalog_supplier=lambda _discovery_id: (required,) if owner.revision == 4 else (),
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
    )
    events = iter(adapter.for_turn("turn-provider")(provider_input))

    action = _next_action_event(events)
    assert action.action_command is not None
    assert action.action_command.tool_name == "death_saving_throw"
    assert action.action_command.parameters == {}
    owner.revision = 5
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={"status": "committed", "result": {"status": "committed"}},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=2,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )

    remaining = tuple(events)

    assert not any(event.kind is ProviderEventKind.ACTION for event in remaining)
    assert [event.text for event in remaining if event.kind is ProviderEventKind.TALK] == [
        "Daniel clings to life."
    ]
    assert len(provider.calls) == 1
    assert "SYLVIA_GM_AUTOMATIC_ENGINE_TRANSITION_RESULT" in str(provider.calls[0][0])
    assert all(
        tool["function"]["name"] != "death_saving_throw" for tool in provider.calls[0][1] or []
    )


@pytest.mark.parametrize("model_calls,automatic_steps", [(20, 0), (10, 6), (0, 20)])
def test_long_exchange_finishes_campaign_file_without_a_round_cutoff(
    model_calls: int, automatic_steps: int
) -> None:
    source = _provider_input()
    advance = source.catalog[0]
    automatic = replace(
        advance,
        name="settle_transition",
        input_schema={"type": "object", "properties": {}},
        model_behavior=ModelToolBehavior.REQUIRED_ENGINE_TRANSITION,
    )
    response = "The clock finishes advancing."
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": f"call-{index}",
                        "function": {
                            "name": "advance" if index < model_calls else "update_campaign_file",
                            "arguments": json.dumps(
                                {"steps": 1, "actor": "rift-player-character"}
                                if index < model_calls
                                else {
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": response}
                                    ]
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ]
            for index in range(model_calls + 1)
        ]
    )
    owner = _OwnerProjection()
    writes: list[dict[str, object]] = []
    total_steps = model_calls + automatic_steps
    provider_input = replace(
        source,
        catalog_supplier=lambda _discovery_id: (
            (automatic,)
            if owner.revision - 4 < automatic_steps
            else (advance,)
            if owner.revision - 4 < total_steps
            else (_campaign_file_tool(allow_unchanged=True),)
        ),
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_unchanged", "campaign_revision": owner.revision}
        ),
    )
    events = iter(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner), provider, owner_id="owner-provider"
        ).for_turn("turn-provider")(provider_input)
    )
    for index in range(total_steps):
        action = _next_action_event(events)
        assert action.action_command is not None
        assert writes == []
        owner.revision += 1
        owner.events = (
            *owner.events,
            CanonicalEvent(
                sequence=index * 2 + 1,
                event_type=CanonicalEventType.TOOL_RESULT,
                turn_id="turn-provider",
                action_id=action.action_command.action_id,
                safe_payload={"status": "committed", "result": {"after": index + 1}},
                visibility=EventVisibility.PLAYER_SAFE,
            ),
            CanonicalEvent(
                sequence=index * 2 + 2,
                event_type=CanonicalEventType.ACTION_COMMITTED,
                turn_id="turn-provider",
                action_id=action.action_command.action_id,
                safe_payload={"status": "committed"},
                visibility=EventVisibility.PLAYER_SAFE,
            ),
        )

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [response]
    assert writes == [{}]
    assert len(provider.calls) == model_calls + 1
    assert owner.revision == 4 + total_steps


@pytest.mark.parametrize("cancel_during_discovery", [False, True])
def test_cancelled_exchange_does_not_start_an_automatic_transition(
    cancel_during_discovery: bool,
) -> None:
    source = _provider_input()
    automatic = replace(
        source.catalog[0],
        input_schema={"type": "object", "properties": {}},
        model_behavior=ModelToolBehavior.REQUIRED_ENGINE_TRANSITION,
    )
    if not cancel_during_discovery:
        source.control.cancel()

    def discover(_discovery_id: str) -> tuple[ModelMechanicsTool, ...]:
        source.control.cancel()
        return (automatic,)

    provider = _ScriptedProvider([])
    events = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()), provider, owner_id="owner-provider"
    ).for_turn("turn-provider")(
        replace(source, catalog=(automatic,), catalog_supplier=discover)
    )

    with pytest.raises(CommandRejected, match="cancelled"):
        next(iter(events))
    assert provider.calls == []


def test_resolved_correction_is_absent_after_automatic_transition() -> None:
    source = _provider_input(user_text="I keep moving.")
    advance = source.catalog[0]
    automatic = ModelMechanicsTool(
        package_id="synthetic.rules",
        package_version="1",
        package_sha256="a" * 64,
        name="settle_transition",
        capability="synthetic.transition",
        description="Settle the automatic transition.",
        input_schema={"type": "object", "properties": {}},
        model_behavior=ModelToolBehavior.REQUIRED_ENGINE_TRANSITION,
    )
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-stale",
                        "function": {"name": "tool_0_advance", "arguments": '{"steps":1}'},
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-corrected",
                        "function": {
                            "name": "advance",
                            "arguments": '{"steps":1,"actor":"rift-player-character"}',
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [ProviderStreamChunk(content="You finish moving.", finish_reason="stop")],
        ]
    )
    owner = _OwnerProjection()
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(advance,),
        control=source.control,
        catalog_supplier=lambda _discovery_id: (
            (advance,) if owner.revision == 4 else (automatic,) if owner.revision == 5 else ()
        ),
    )
    events = iter(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner), provider, owner_id="owner-current-controls"
        ).for_turn("turn-provider")(provider_input)
    )

    advance_action = _next_action_event(events)
    assert "SYLVIA_GM_TOOL_CORRECTION" in str(provider.calls[1][0])
    owner.revision = 5
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=advance_action.action_command.action_id,
            safe_payload={"status": "committed", "result": {"after": 1}},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=2,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=advance_action.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )
    automatic_action = _next_action_event(events)
    owner.revision = 6
    owner.events = (
        *owner.events,
        CanonicalEvent(
            sequence=3,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=automatic_action.action_command.action_id,
            safe_payload={"status": "committed", "result": {"settled": True}},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=4,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=automatic_action.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        "You finish moving."
    ]
    final_messages = provider.calls[2][0]
    assert "SYLVIA_GM_TOOL_CORRECTION" not in str(final_messages)
    facts = [
        str(message["content"])
        for message in final_messages
        if str(message.get("content", "")).startswith("SYLVIA_GM_EXACT_NARRATION_FACTS")
    ]
    assert len(facts) == 1
    assert facts[0].count('"tool":"advance"') == 1
    assert facts[0].count('"tool":"settle_transition"') == 1
    assert "SYLVIA_GM_AUTOMATIC_ENGINE_TRANSITION_RESULT" in str(final_messages)
    user_contents = [
        message["content"] for message in final_messages if message.get("role") == "user"
    ]
    assert user_contents[-1] == "I keep moving."
    assert len(provider.calls) == 3


def test_only_one_state_changing_mechanic_executes_per_catalog_snapshot() -> None:
    first = ModelMechanicsTool(
        package_id="synthetic.rules",
        package_version="1",
        package_sha256="a" * 64,
        name="first_mechanic",
        capability="synthetic.first",
        description="Resolve the first mechanic.",
        input_schema={"type": "object", "properties": {}},
    )
    stale_second = ModelMechanicsTool(
        package_id="synthetic.rules",
        package_version="1",
        package_sha256="a" * 64,
        name="stale_second_mechanic",
        capability="synthetic.second",
        description="Resolve a second mechanic from the same snapshot.",
        input_schema={"type": "object", "properties": {}},
    )
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-first",
                        "function": {"name": "first_mechanic", "arguments": "{}"},
                    },
                ),
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 1,
                        "id": "call-stale-second",
                        "function": {
                            "name": "stale_second_mechanic",
                            "arguments": "{}",
                        },
                    },
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderStreamChunk(
                    content="Play continues from current state.", finish_reason="stop"
                )
            ],
        ]
    )
    owner = _OwnerProjection()
    source = _provider_input(user_text="Continue.")
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(first, stale_second),
        control=source.control,
        catalog_supplier=lambda _discovery_id: (first, stale_second) if owner.revision == 4 else (),
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
    )
    events = iter(adapter.for_turn("turn-provider")(provider_input))

    action = _next_action_event(events)
    assert action.action_command is not None
    assert action.action_command.tool_name == "first_mechanic"
    owner.revision = 5
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={"status": "committed", "result": {"status": "committed"}},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=2,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )

    remaining = tuple(events)

    assert not any(event.kind is ProviderEventKind.ACTION for event in remaining)
    assert [event.text for event in remaining if event.kind is ProviderEventKind.TALK] == [
        "Play continues from current state."
    ]
    second_messages = provider.calls[1][0]
    superseded = [
        json.loads(str(message["content"]))
        for message in second_messages
        if message.get("role") == "tool"
        and "superseded_by_catalog_refresh" in str(message.get("content"))
    ]
    assert len(superseded) == 1


class _OwnerProjection:
    def __init__(
        self,
        *,
        participants: tuple[object, ...] | None = None,
        state: dict[str, object] | None = None,
        ruleset_guide: str | None = None,
        continuity: CampaignContinuityView | None = None,
        construction_input: dict[str, object] | None = None,
    ) -> None:
        self.revision = 4
        self.events: tuple[CanonicalEvent, ...] = ()
        self.boundary: ModelDecisionBoundary | None = None
        self.ruleset_guide = ruleset_guide
        self.continuity = continuity or CampaignContinuityView()
        self.construction_input = construction_input
        self.state = state or {
            "sylvia.srd-lite.bg3.entities": {
                "rift-player-character": {"hp": 12, "armor_class": 16},
                "rift-scarred-hound": {"hp": 12, "armor_class": 13},
            }
        }
        self.participants = (
            participants
            if participants is not None
            else (
                SimpleNamespace(
                    participant_id="rift-player-character",
                    kind="player_character",
                    data={"name": "Mara Venn", "party_role": "player_character"},
                ),
            )
        )

    def snapshot(self, campaign_id: str) -> object:
        assert campaign_id == "campaign-provider"
        return SimpleNamespace(
            campaign_id=campaign_id,
            display_name="The Rift Awakening",
            revision=self.revision,
            state=self.state,
            participants=self.participants,
            events=self.events,
            continuity=self.continuity,
        )

    def canonical_replay(self, campaign_id: str, *, acknowledged_sequence: int) -> object:
        assert campaign_id == "campaign-provider"
        assert acknowledged_sequence == 0
        return SimpleNamespace(
            canonical=self.events,
            provisional=(),
            high_water=self.events[-1].sequence if self.events else 0,
        )

    def model_decision_boundary(self, campaign_id: str) -> ModelDecisionBoundary | None:
        assert campaign_id == "campaign-provider"
        return self.boundary

    def ruleset_gm_operating_guide(self, campaign_id: str) -> str | None:
        assert campaign_id == "campaign-provider"
        return self.ruleset_guide

    def package_text_file(
        self,
        package_id: str,
        version: str,
        content_sha256: str,
        path: str,
    ) -> str:
        assert package_id == "pneuma.severed-lands"
        assert version == "1.0.0"
        assert content_sha256 == "a" * 64
        assert path == "locations/verdant-margin.md"
        return (
            "# Verdant Margin\n\n"
            "**Region:** South of Waystation\n\n"
            "## Overview\n\n"
            "The Verdant Margin grows quickly around a stable Warden stone.\n\n"
            "## Hooks\n\n"
            "A private prepared lead waits elsewhere in the location record."
        )

    def campaign_construction_basis(self, campaign_id: str) -> object:
        assert campaign_id == "campaign-provider"
        if self.construction_input is None:
            raise CommandRejected(
                "Campaign construction is unavailable",
                code="construction_unavailable",
            )
        return SimpleNamespace(canonical_input=self.construction_input)


def _provider_input(
    control: ProviderControl | None = None,
    *,
    user_text: str = "Advance the neutral clock.",
    ooc_text: str | None = None,
) -> SessionProviderInput:
    session = SessionRecord(
        session_id="session-provider",
        campaign_id="campaign-provider",
        role=WriterRole.DM,
        provider_id="synthetic-provider",
        model_id="synthetic-model",
    )
    message = SessionMessage(
        session_id=session.session_id,
        sequence=1,
        message_id="message-provider",
        kind=SessionMessageKind.USER,
        content={
            "text": user_text,
            **({"ooc_text": ooc_text} if ooc_text is not None else {}),
            "input_id": "attempt-provider",
            "attempt_id": "attempt-provider",
            "turn_id": "turn-provider",
            "input_kind": "user",
        },
    )
    tool = ModelMechanicsTool(
        package_id="synthetic.clock",
        package_version="1",
        package_sha256="a" * 64,
        name="advance",
        capability="clock.advance",
        input_schema={
            "type": "object",
            "properties": {
                "steps": {"type": "integer"},
                "actor": {
                    "type": "string",
                    "x-sylvia-gm-reference": "participant",
                },
                "actor_state": {"type": "string"},
            },
            "required": ["steps", "actor", "actor_state"],
            "additionalProperties": False,
        },
    )
    return SessionProviderInput(
        session=session,
        history=SessionContext(session=session, messages=(message,)),
        catalog=(tool,),
        control=control or ProviderControl(),
    )


@pytest.mark.parametrize(
    ("user_text", "ooc_text", "expected"),
    [
        ("I attack the wolf.", None, "I attack the wolf."),
        (
            "I attack the wolf.",
            "Please portray Chloe as more affectionate going forward.",
            'SYLVIA_PLAYER_TURN\n{"in_character":"I attack the wolf.",'
            '"out_of_character":"Please portray Chloe as more affectionate going forward."}',
        ),
        (
            "",
            "No, I did not spend three hours searching.",
            'SYLVIA_PLAYER_TURN\n{"in_character":null,'
            '"out_of_character":"No, I did not spend three hours searching."}',
        ),
    ],
)
def test_player_turn_input_preserves_ic_and_explicitly_labels_ooc(
    user_text: str, ooc_text: str | None, expected: str
) -> None:
    provider_input = _provider_input(user_text=user_text, ooc_text=ooc_text)
    messages = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()),
        _ScriptedProvider([]),
        owner_id="owner-provider",
    )._messages(provider_input)  # noqa: SLF001

    user_messages = [message for message in messages if message.get("role") == "user"]
    assert user_messages[-1] == {"role": "user", "content": expected}
    assert provider_input.history.messages[0].content.get("text") == user_text
    assert provider_input.history.messages[0].content.get("ooc_text") == ooc_text


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        ({"name": "Chloe", "party_role": "companion"}, "Chloe"),
        ({"name": "Chloe", "party_role": "player_character"}, "Unknown speaker"),
        ({"name": "Chloe"}, "Unknown speaker"),
        (
            {"name": "Chloe", "party_role": "companion", "control_owner": "player"},
            "Unknown speaker",
        ),
        (
            {"name": "Chloe", "party_role": "companion", "visibility": "gm_private"},
            "Unknown speaker",
        ),
    ],
)
def test_dialogue_uses_party_control_not_character_schema_kind(
    data: dict[str, str], expected: str
) -> None:
    snapshot = SimpleNamespace(participants=(SimpleNamespace(
        participant_id="companion-chloe", kind="player_character", data=data,
    ),))
    _, blocks = _project_response_blocks(
        ({"kind": "npc_dialogue", "speaker_id": "companion-chloe", "text": "Wait."},),
        snapshot,
    )
    assert blocks[0]["speaker_label"] == expected
    assert "speaker_id" not in blocks[0]


def test_dialogue_prefers_persisted_companion_role_to_conflicting_character_data() -> None:
    snapshot = SimpleNamespace(participants=(SimpleNamespace(
        participant_id="companion-hex",
        kind="player_character",
        data={"name": "Hex", "party_role": "player_character"},
    ),))

    _, blocks = _project_response_blocks(
        ({"kind": "npc_dialogue", "speaker_id": "companion-hex", "text": "Ready."},),
        snapshot,
        party_roles={"companion-hex": "companion"},
    )

    assert blocks[0]["speaker_label"] == "Hex"


def test_required_campaign_file_response_preserves_its_block_contract() -> None:
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()), _ScriptedProvider([]),
        owner_id="owner-provider",
    )
    tools, aliases = adapter._provider_tools((_campaign_file_tool(allow_unchanged=True),))
    parameters = tools[0]["function"]["parameters"]
    original = parameters["properties"]["revised_player_facing_response"]["description"]
    _require_checked_campaign_file_narration(tools, aliases)
    description = parameters["properties"]["revised_player_facing_response"]["description"]
    assert description == original
    assert "spoken words only" in description
    assert "exact current Campaign Entity" in description
    assert "revised_player_facing_response" in parameters["required"]
    response_schema = parameters["properties"]["revised_player_facing_response"]
    assert response_schema["type"] == "array"
    assert "oneOf" not in response_schema
    variants = response_schema["items"]["oneOf"]
    dialogue = next(
        item for item in variants if item["properties"]["kind"]["const"] == "npc_utterance"
    )
    assert set(dialogue["required"]) == {"kind", "speaker_id", "spoken_words"}
    assert "text" not in dialogue["properties"]
    assert (
        "Only the words this NPC says aloud"
        in dialogue["properties"]["spoken_words"]["description"]
    )
    assert "Exact current Campaign Entity" in dialogue["properties"]["speaker_id"]["description"]
    _require_checked_campaign_file_narration(tools, aliases, narration_held=True)
    assert parameters["properties"]["revised_player_facing_response"] == response_schema
    assert "revised_player_facing_response" not in parameters["required"]


def test_provider_dialogue_requires_explicit_spoken_words() -> None:
    """Do not accept the undifferentiated paragraph shape observed in v139."""

    with pytest.raises(ValueError, match="spoken_words"):
        _validated_raw_response_blocks([
            {"kind": "npc_dialogue", "speaker_id": "rowan",
             "text": 'Rowan studies the ground. "From the center."'},
        ])


def test_provider_spoken_words_normalize_without_rewriting_or_guessing() -> None:
    blocks = [
        {"kind": "narration", "text": "Rowan studies the ground."},
        {"kind": "npc_utterance", "speaker_id": "anonymous",
         "spoken_words": 'From the center. I call it "home base".'},
        {"kind": "narration", "text": "He pauses."},
        {"kind": "npc_utterance", "speaker_id": "anonymous", "spoken_words": "Then the edge."},
    ]
    admitted = _validated_raw_response_blocks(blocks)
    text, projected = _project_response_blocks(admitted, SimpleNamespace())
    assert [block["text"] for block in projected] == [
        "Rowan studies the ground.", 'From the center. I call it "home base".',
        "He pauses.", "Then the edge.",
    ]
    assert [block["kind"] for block in projected] == [
        "narration", "npc_dialogue", "narration", "npc_dialogue",
    ]
    assert 'Unknown speaker: From the center. I call it "home base".' in text
    assert blocks[1]["spoken_words"] == projected[1]["text"]
    assert "text" not in blocks[1]


@pytest.mark.parametrize("text_key", ["text", "spoken_words"])
def test_saved_native_dialogue_keeps_legacy_text_on_resume_only(text_key: str) -> None:
    blocks = [{"kind": "npc_dialogue", "speaker_id": "anonymous", text_key: "Wait."}]
    assert _validated_raw_response_blocks(blocks, allow_legacy_dialogue_text=True) == (
        {"kind": "npc_dialogue", "speaker_id": "anonymous", "text": "Wait."},
    )
    with pytest.raises(ValueError, match="spoken_words"):
        _validated_raw_response_blocks(blocks)


@pytest.mark.parametrize("malformed", [
    [{"kind": "npc_dialogue", "speaker_id": "anonymous",
      "text": 'Rowan studies the ground. "From the center."'}],
    'Rowan studies the ground. "From the center."',
])
def test_old_dialogue_shape_is_rejected_before_write_then_same_gm_corrects_it(
    malformed: object,
) -> None:
    corrected = [
        {"kind": "narration", "text": "Rowan studies the ground."},
        {"kind": "npc_utterance", "speaker_id": "anonymous", "spoken_words": "From the center."},
    ]
    provider = _ScriptedProvider([
        [ProviderStreamChunk(
            tool_call_delta={"index": 0, "id": call_id, "function": {
                "name": "update_campaign_file",
                "arguments": json.dumps({"revised_player_facing_response": blocks}),
            }},
            finish_reason="tool_calls",
        )]
        for call_id, blocks in (("bad-dialogue", malformed), ("corrected-dialogue", corrected))
    ])
    writes: list[object] = []
    source = _provider_input()
    saved_contract = SessionMessage(
        session_id=source.session.session_id, sequence=2, message_id="saved-gm-contract",
        kind=SessionMessageKind.EVIDENCE,
        content={"kind": "gm_system_contract", "version": 1, "contract": "UNCHANGED OLD GM"},
    )
    provider_input = replace(
        source, catalog=(_campaign_file_tool(allow_unchanged=True),),
        history=replace(source.history, messages=(*source.history.messages, saved_contract)),
        narrative_writer=lambda parameters: writes.append(parameters)
        or {"status": "campaign_file_unchanged", "campaign_revision": 4},
    )
    events = tuple(ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()), provider, owner_id="owner-provider",
    ).for_turn("turn-provider")(provider_input))
    assert len(writes) == 1
    assert len(provider.calls) == 2
    for messages, _tools in provider.calls:
        assert messages[0] == {"role": "system", "content": "UNCHANGED OLD GM"}
        format_controls = [
            message for message in messages if message["role"] == "system"
            and str(message.get("content", "")).startswith("SYLVIA_GM_RESPONSE_FORMAT")
        ]
        assert len(format_controls) == 1
        assert "npc_utterance.spoken_words" in format_controls[0]["content"]
        assert (
            "gestures and attribution in adjacent narration blocks" in format_controls[0]["content"]
        )
        example = json.loads(str(format_controls[0]["content"]).split("Example layout: ", 1)[1])
        assert [block["kind"] for block in _validated_raw_response_blocks(example)] == [
            "narration", "npc_dialogue", "narration", "npc_dialogue",
        ]
    correction = next(message for message in provider.calls[1][0]
                      if message.get("tool_call_id") == "bad-dialogue")
    result = json.loads(correction["content"])
    assert result["state_changed"] is False
    assert "spoken_words" in result["instruction"]
    talks = [event for event in events if event.kind is ProviderEventKind.TALK]
    assert len(talks) == 1
    assert talks[0].response_blocks == (
        {"kind": "narration", "text": "Rowan studies the ground."},
        {"kind": "npc_dialogue", "speaker_label": "Unknown speaker", "text": "From the center."},
    )


def test_same_gm_can_repartition_held_dialogue_while_correcting_state() -> None:
    """A rejected save must not remove the model's way to fix its held presentation."""

    mixed = [{"kind": "npc_utterance", "speaker_id": "anonymous",
              "spoken_words": 'The guard lowers her spear. "You may pass."'}]
    separated = [
        {"kind": "narration", "text": "The guard lowers her spear."},
        {"kind": "npc_utterance", "speaker_id": "anonymous", "spoken_words": "You may pass."},
    ]
    provider = _ScriptedProvider([
        [ProviderStreamChunk(tool_call_delta={"index": 0, "id": call_id, "function": {
            "name": "update_campaign_file", "arguments": json.dumps({
                "facts": [{}], "revised_player_facing_response": blocks,
            }),
        }}, finish_reason="tool_calls")]
        for call_id, blocks in (("rejected-save", mixed), ("corrected-save", separated))
    ])
    writes: list[object] = []

    def write(parameters: Mapping[str, object]) -> Mapping[str, object]:
        writes.append(dict(parameters))
        if len(writes) == 1:
            raise MechanicsRejected(
                "Invalid fact.", code="campaign_file_update_invalid", state_changed=False,
            )
        return {"status": "campaign_file_updated", "campaign_revision": 5}

    source = replace(_provider_input(), catalog=(_campaign_file_tool(allow_unchanged=True),),
                     narrative_writer=write)
    events = tuple(ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()), provider, owner_id="owner-provider",
    ).for_turn("turn-provider")(source))
    correction_schema = (provider.calls[1][1] or [])[0]["function"]["parameters"]
    assert "revised_player_facing_response" in correction_schema["properties"]
    assert "revised_player_facing_response" not in correction_schema["required"]
    assert "complete replacement" in str(provider.calls[1][0])
    talks = [event for event in events if event.kind is ProviderEventKind.TALK]
    assert len(writes) == 2
    assert len(talks) == 1
    assert talks[0].response_blocks == (
        {"kind": "narration", "text": "The guard lowers her spear."},
        {"kind": "npc_dialogue", "speaker_label": "Unknown speaker", "text": "You may pass."},
    )


def test_campaign_file_rejects_plan_alias_speakers_before_writing() -> None:
    participant_id = "participant-01ab9ef11a40985dda9fffdf"
    invalid = [
        {"kind": "npc_utterance", "speaker_id": "sera_pike", "spoken_words": "Move."},
        {"kind": "npc_utterance", "speaker_id": "rola_mensah", "spoken_words": "With you."},
    ]
    corrected = [
        {"kind": "npc_utterance", "speaker_id": "sera_pike", "spoken_words": "Move."},
        {"kind": "npc_utterance", "speaker_id": participant_id, "spoken_words": "With you."},
        {"kind": "npc_utterance", "speaker_id": "anonymous", "spoken_words": "Stay low."},
    ]
    provider = _ScriptedProvider([
        [ProviderStreamChunk(
            tool_call_delta={"index": 0, "id": call_id, "function": {
                "name": "update_campaign_file",
                "arguments": json.dumps({
                    "revised_player_facing_response": blocks,
                    **({"entities": [{"entity_id": "sera_pike", "name": "Sera Pike",
                                      "attributes": {}}]}
                       if call_id == "exact-speakers" else {}),
                }),
            }},
            finish_reason="tool_calls",
        )]
        for call_id, blocks in (("unsupported-speakers", invalid), ("exact-speakers", corrected))
    ])
    owner = _OwnerProjection(participants=(SimpleNamespace(
        participant_id=participant_id,
        kind="trigger-discipline.character",
        data={"name": "Rola Mensah", "party_role": "companion"},
    ),))
    writes: list[object] = []
    source = _provider_input()
    def write(parameters: object) -> dict[str, object]:
        writes.append(parameters)
        owner.continuity = CampaignContinuityView(records=(SimpleNamespace(
            record_id="sera_pike", kind="entity", status="active", admitted_revision=5,
            payload={"name": "Sera Pike", "visibility": "player_known",
                     "known_by": ["player"]},
        ),))
        return {"status": "campaign_file_updated", "campaign_revision": 5}

    provider_input = replace(
        source,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        narrative_writer=write,
    )

    events = tuple(ProviderSessionAdapter(
        cast(AuthorityOwner, owner), provider, owner_id="owner-provider",
    ).for_turn("turn-provider")(provider_input))

    assert len(provider.calls) == 2
    assert writes == [{
        "entities": [{"entity_id": "sera_pike", "name": "Sera Pike", "attributes": {}}]
    }]
    talk = next(event for event in events if event.kind is ProviderEventKind.TALK)
    assert talk.response_blocks == (
        {"kind": "npc_dialogue", "speaker_label": "Sera Pike", "text": "Move."},
        {"kind": "npc_dialogue", "speaker_label": "Rola Mensah", "text": "With you."},
        {"kind": "npc_dialogue", "speaker_label": "Unknown speaker", "text": "Stay low."},
    )


def test_campaign_file_response_blocks_are_ordered_and_speaker_safe() -> None:
    blocks = [
        {"kind": "narration", "text": "The rain eases."},
        {"kind": "npc_utterance", "speaker_id": "chloe", "spoken_words": "We can move."},
        {"kind": "npc_utterance", "speaker_id": "hidden-npc", "spoken_words": "Not yet."},
        {"kind": "ooc", "text": "I will carry that preference forward."},
    ]
    provider = _ScriptedProvider(
        [[
            ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "response-blocks",
                    "function": {
                        "name": "update_campaign_file",
                        "arguments": json.dumps({
                            "entities": [{"entity_id": "chloe", "name": "Chloe",
                                          "attributes": {}}],
                            "revised_player_facing_response": blocks,
                        }),
                    },
                },
                finish_reason="tool_calls",
            )
        ]]
    )
    hidden = SimpleNamespace(
        record_id="hidden-npc",
        kind="entity",
        status="active",
        payload={"name": "Secret Name", "visibility": "gm_private", "known_by": ["gm"]},
    )
    owner = _OwnerProjection(continuity=CampaignContinuityView(records=(hidden,)))

    def record_new_chloe(_parameters: object) -> dict[str, object]:
        owner.continuity = CampaignContinuityView(
            records=(
                hidden,
                SimpleNamespace(
                    record_id="chloe",
                    kind="entity",
                    status="active",
                    payload={
                        "name": "Chloe",
                        "visibility": "player_known",
                        "known_by": ["player"],
                    },
                ),
            )
        )
        return {"status": "campaign_file_updated", "campaign_revision": 5}
    source = _provider_input(ooc_text="Please remember my scene preference.")
    provider_input = replace(
        source,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        narrative_writer=record_new_chloe,
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner), provider, owner_id="owner-provider"
        ).for_turn("turn-provider")(provider_input)
    )

    talk = next(event for event in events if event.kind is ProviderEventKind.TALK)
    assert talk.text == (
        "The rain eases.\n\nChloe: We can move.\n\nUnknown speaker: Not yet.\n\n"
        "OOC — Sylvia: I will carry that preference forward."
    )
    assert talk.response_blocks is not None
    assert [block["kind"] for block in talk.response_blocks] == [
        "narration",
        "npc_dialogue",
        "npc_dialogue",
        "ooc",
    ]
    assert talk.response_blocks[1]["speaker_label"] == "Chloe"
    assert talk.response_blocks[2]["speaker_label"] == "Unknown speaker"
    assert "hidden-npc" not in repr(talk.response_blocks)
    assert "Secret Name" not in repr(talk.response_blocks)


def test_ooc_turn_rejects_omitted_ack_before_campaign_file_write() -> None:
    acknowledgement = [{"kind": "ooc", "text": "I will keep that correction."}]
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "missing-ack",
                    "function": {"name": "update_campaign_file", "arguments": "{}"},
                },
                finish_reason="tool_calls",
            )],
            [ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "with-ack",
                    "function": {
                        "name": "update_campaign_file",
                        "arguments": json.dumps(
                            {"revised_player_facing_response": acknowledgement}
                        ),
                    },
                },
                finish_reason="tool_calls",
            )],
        ]
    )
    writes: list[object] = []
    source = _provider_input(user_text="", ooc_text="That detail was incorrect.")
    provider_input = replace(
        source,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        narrative_writer=lambda parameters: writes.append(parameters)
        or {"status": "campaign_file_unchanged", "campaign_revision": 4},
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()), provider, owner_id="owner-provider"
        ).for_turn("turn-provider")(provider_input)
    )

    assert len(writes) == 1
    assert len(provider.calls) == 2
    talk = next(event for event in events if event.kind is ProviderEventKind.TALK)
    assert talk.response_blocks == tuple(acknowledgement)


def test_rejected_disposition_keeps_exact_structured_response_when_retry_omits_it() -> None:
    blocks = [
        {"kind": "narration", "text": "Rain beads on the lintel."},
        {"kind": "npc_utterance", "speaker_id": "anonymous", "spoken_words": "Wait."},
    ]
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "rejected-disposition",
                    "function": {
                        "name": "update_campaign_file",
                        "arguments": json.dumps(
                            {
                                "revised_player_facing_response": blocks,
                                "state_change": "changed",
                                "state_change_summary": "The weather changes.",
                            }
                        ),
                    },
                },
                finish_reason="tool_calls",
            )],
            [ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "corrected-disposition",
                    "function": {
                        "name": "update_campaign_file",
                        "arguments": json.dumps(
                            {
                                "state_change": "changed",
                                "state_change_summary": "The weather changes.",
                                "facts": [
                                    {
                                        "fact_id": "rain-started",
                                        "category": "weather",
                                        "text": "Rain has begun.",
                                        "visibility": "player_known",
                                        "known_by": ["player"],
                                    }
                                ],
                            }
                        ),
                    },
                },
                finish_reason="tool_calls",
            )],
        ]
    )
    calls = 0

    def writer(_parameters: object) -> dict[str, object]:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise CommandRejected(
                "synthetic invalid update", code="campaign_file_update_invalid"
            )
        return {"status": "campaign_file_updated", "campaign_revision": 5}

    source = _provider_input()
    provider_input = replace(
        source,
        catalog=(_campaign_file_tool(allow_unchanged=True, include_disposition=True),),
        narrative_writer=writer,
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()), provider, owner_id="owner-provider"
        ).for_turn("turn-provider")(provider_input)
    )

    talk = next(event for event in events if event.kind is ProviderEventKind.TALK)
    assert talk.text == "Rain beads on the lintel.\n\nUnknown speaker: Wait."
    assert talk.response_blocks is not None
    assert [block["text"] for block in talk.response_blocks] == [
        "Rain beads on the lintel.",
        "Wait.",
    ]


def test_persisted_mixed_turn_recovers_exact_rejected_blocks_without_replaying_action() -> None:
    source = _provider_input(user_text="I wait here.", ooc_text="Keep Chloe affectionate.")
    basis = source.history.messages[-1]
    blocks = [
        {"kind": "narration", "text": "Rain beads on the lintel."},
        {"kind": "npc_dialogue", "speaker_id": "anonymous", "text": "Wait."},
        {"kind": "ooc", "text": "Saved your preference for Chloe."},
    ]
    facts = [{"fact_id": "chloe-portrayal", "category": "preference",
              "text": "Portray Chloe affectionately.", "visibility": "player_known",
              "known_by": ["player"]}]
    rejected_call = {"role": "assistant", "content": None, "tool_calls": [{
        "id": "held-call", "type": "function", "function": {
            "name": "update_campaign_file",
            "arguments": json.dumps({"facts": [{}], "revised_player_facing_response": blocks}),
        },
    }]}
    history = replace(source.history, messages=source.history.messages + tuple(
        SessionMessage(
            session_id=source.session.session_id, sequence=index, message_id=f"held-{index}",
            kind=SessionMessageKind.EVIDENCE,
            content={"kind": kind, "turn_id": "turn-provider", "inference_id": "held-inference",
                     "basis_message_id": basis.message_id, **payload},
        )
        for index, (kind, payload) in enumerate([
            ("provider_tool_calls", {"assistant": rejected_call}),
            ("provider_tool_results", {"results": [{
                "role": "tool", "tool_call_id": "held-call", "name": "update_campaign_file",
                "content": json.dumps({"status": "rejected", "code": "campaign_file_update_invalid",
                                       "state_changed": False}),
            }]}),
        ], start=2)
    ))
    provider = _ScriptedProvider([[ProviderStreamChunk(
        tool_call_delta={"index": 0, "id": "corrected-call", "function": {
            "name": "update_campaign_file", "arguments": json.dumps({"facts": facts}),
        }}, finish_reason="tool_calls",
    )]])
    writes: list[object] = []

    def writer(parameters: object) -> dict[str, object]:
        writes.append(parameters)
        return {"status": "campaign_file_updated", "campaign_revision": 5}

    provider_input = replace(
        source, history=history,
        catalog=(*source.catalog, _campaign_file_tool(allow_unchanged=True)),
        narrative_writer=writer,
    )
    events = tuple(ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()), provider, owner_id="owner-provider",
    ).for_turn("turn-provider")(provider_input))
    talk = next(event for event in events if event.kind is ProviderEventKind.TALK)
    assert len(writes) == 1
    assert len(provider.calls) == 1
    assert talk.response_blocks is not None
    assert [block["text"] for block in talk.response_blocks] == [block["text"] for block in blocks]
    assert talk.text == (
        "Rain beads on the lintel.\n\nUnknown speaker: Wait.\n\n"
        "OOC — Sylvia: Saved your preference for Chloe."
    )
    exposed = provider.calls[0][1]
    assert exposed is not None and len(exposed) == 1
    assert exposed[0]["function"]["name"].endswith("update_campaign_file")


def _campaign_file_tool(
    *,
    allow_unchanged: bool = False,
    include_disposition: bool = False,
) -> ModelMechanicsTool:
    """Return the one deep model-facing Campaign File update surface."""

    input_schema: dict[str, object] = {
        "type": "object",
        "properties": {
            "facts": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "fact_id": {"type": "string"},
                        "category": {"type": "string"},
                        "text": {"type": "string"},
                        "visibility": {"type": "string"},
                        "known_by": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["fact_id", "category", "text", "visibility", "known_by"],
                    "additionalProperties": False,
                },
            },
            "entities": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "entity_id": {"type": "string"},
                        "name": {"type": "string"},
                        "attributes": {"type": "object"},
                    },
                    "required": ["entity_id", "name", "attributes"],
                    "additionalProperties": False,
                },
            },
            "scenes": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "summary": {"type": "string"},
                        "location_id": {"type": "string"},
                        "act_id": {"type": "string", "enum": ["act-1"]},
                    },
                    "required": ["summary"],
                    "additionalProperties": False,
                },
            },
            "plan_progress": {
                "type": "array",
                "items": {
                    "type": "object",
                    "properties": {
                        "plan_item_id": {"type": "string", "enum": ["act-1"]},
                        "progress_status": {
                            "type": "string",
                            "enum": [
                                "introduced",
                                "engaged",
                                "deferred",
                                "resolved",
                                "retired",
                            ],
                        },
                        "progress_summary": {"type": "string"},
                    },
                    "required": [
                        "plan_item_id",
                        "progress_status",
                        "progress_summary",
                    ],
                    "additionalProperties": False,
                },
            },
        },
        "required": [],
        "additionalProperties": False,
    }
    if not allow_unchanged:
        input_schema["minProperties"] = 1
    if include_disposition:
        properties = cast(dict[str, object], input_schema["properties"])
        properties["state_change"] = {
            "type": "string",
            "enum": ["changed", "unchanged"],
        }
        properties["state_change_summary"] = {
            "type": "string",
            "minLength": 1,
        }
        input_schema["required"] = ["state_change", "state_change_summary"]
    return ModelMechanicsTool(
        package_id="sylvia.application",
        package_version="1",
        package_sha256="0" * 64,
        name="update_campaign_file",
        capability="narrative_state.update_campaign_file",
        description="Commit one atomic Campaign File update.",
        input_schema=input_schema,
        category="narrative_state",
    )


def _d20_provider_input(*, user_text: str) -> SessionProviderInput:
    """Return one character D20 input for post-mechanics narration probes."""

    source = _provider_input(user_text=user_text)
    tool = ModelMechanicsTool(
        package_id="sylvia.srd-lite.bg3",
        package_version="1",
        package_sha256="b" * 64,
        name="d20_test",
        capability="dnd.srd.5.2.1.d20-test",
        input_schema={
            "type": "object",
            "properties": {
                "actor": {
                    "type": "string",
                    "x-sylvia-gm-reference": "participant",
                },
                "skill": {"type": "string", "enum": ["athletics"]},
                "target_number": {"type": "integer"},
            },
            "required": ["actor", "skill", "target_number"],
            "additionalProperties": False,
        },
    )
    return SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(tool,),
        control=source.control,
    )


def _combat_attack_provider_input(*, user_text: str) -> SessionProviderInput:
    """Return a minimal exact attack boundary for post-mechanics narration probes."""

    source = _provider_input(user_text=user_text)
    tool = ModelMechanicsTool(
        package_id="sylvia.srd-lite.bg3",
        package_version="1",
        package_sha256="b" * 64,
        name="resolve_combat_attack",
        capability="dnd.srd.5.2.1.combat.attack",
        input_schema={
            "type": "object",
            "properties": {
                "actor": {
                    "type": "string",
                    "x-sylvia-gm-reference": "participant",
                },
                "target": {
                    "type": "string",
                    "x-sylvia-gm-reference": "entity",
                },
            },
            "required": ["actor", "target"],
            "additionalProperties": False,
        },
    )
    return SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(tool,),
        control=source.control,
    )


def test_gm_final_response_reference_survives_history_reconstruction() -> None:
    source = _provider_input(user_text="Continue")
    def message(sequence, identity, kind, content):
        return SessionMessage(
            session_id=source.session.session_id, sequence=sequence,
            message_id=identity, kind=kind, content=content,
        )
    history = (
        message(1, "prior-user", SessionMessageKind.USER, {"text": "Look around"}),
        message(2, "reference", SessionMessageKind.EVIDENCE, {
            "kind": "provider_final_response_reference", "basis_message_id": "prior-user",
            "response_id": "prior-response", "requirement": {"reasoning_required": True},
        }),
        message(3, "prior-talk", SessionMessageKind.TALK, {"text": "A quiet clearing."}),
        message(4, "current-user", SessionMessageKind.USER, {"text": "Continue"}),
    )
    selected = replace(source, history=replace(source.history, messages=history))
    adapter = ProviderSessionAdapter(_OwnerProjection(), _ScriptedProvider([]), owner_id="test")
    prepared = adapter._messages(selected)
    final = next(item for item in prepared if item.get("content") == "A quiet clearing.")
    assert final["_native_reference"] == {
        "response_id": "prior-response", "requirement": {"reasoning_required": True},
    }


def test_gm_deepseek_native_attachment_requires_tools_and_scoped_saved_data() -> None:
    provider = _ScriptedProvider([])
    provider.model_info = ModelInfo(
        id="synthetic/deepseek-v4", name="Synthetic", provider_name="Synthetic",
        provider_key="synthetic", provider_mode="byok", provider_model_id="deepseek-v4",
        transport="chat_completions",
    )
    source = _provider_input()
    reads = []
    def read(*args, **kwargs):
        reads.append((args, kwargs))
        return {"reasoning_content": "Synthetic native reasoning"}
    adapter = ProviderSessionAdapter(
        SimpleNamespace(provider_continuation=read), provider, owner_id="synthetic",
    )
    messages = [{"role": "assistant", "content": "Saved answer", "_native_reference": {
        "response_id": "response", "requirement": {
            "provider_id": source.session.provider_id, "model_id": source.session.model_id,
            "reasoning_required": True,
        },
    }}]
    assert adapter._attach_native_continuation(messages, source, has_tools=False) == [
        {"role": "assistant", "content": "Saved answer"},
    ]
    assert reads == []
    result = adapter._attach_native_continuation(messages, source, has_tools=True)
    assert result[0]["reasoning_content"] == "Synthetic native reasoning"
    assert "_native_reference" not in result[0]
    assert reads[0][1] == {
        "provider_id": source.session.provider_id, "model_id": source.session.model_id,
    }
    adapter._owner.provider_continuation = lambda *args, **kwargs: None
    with pytest.raises(CommandRejected) as rejected:
        adapter._attach_native_continuation(messages, source, has_tools=True)
    assert rejected.value.code == "provider_native_continuation_missing"


@pytest.mark.parametrize("native_continuation", [False, True, "chat"])
def test_stream_adapter_round_trips_exact_catalog_action_and_talk(
    native_continuation: bool | str,
) -> None:
    chat_metadata = {
        "reasoning_details": [{"type": "reasoning.encrypted", "data": "private-signature"}],
        "tool_call_extras": {"call-one": {"google": {"thought_signature": "private-signature"}}},
    }
    native_blocks = [
        {
            "type": "thinking",
            "thinking": "PRIVATE PROVIDER REASONING",
            "signature": "private-signature",
        },
        {"type": "text", "text": "The player wants to advance; I think I'll call the clock tool."},
        {
            "type": "tool_use",
            "id": "call-one",
            "name": "advance",
            "input": {"steps": 2, "actor": "rift-player-character"},
        },
    ]
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    reasoning_content="PRIVATE PROVIDER REASONING",
                    content="The player wants to advance; I think I'll call the clock tool.",
                    tool_call_delta={
                        "index": 0,
                        "id": "call-one",
                        "function": {
                            "name": "advance",
                            "arguments": '{"steps":2,"actor":"rift-player-character"}',
                        },
                    },
                ),
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "function": {"name": "", "arguments": ""},
                    },
                    finish_reason="tool_calls",
                    anthropic_content=native_blocks if native_continuation is True else None,
                    chat_completion_metadata=(
                        chat_metadata if native_continuation == "chat" else None
                    ),
                ),
            ],
            [
                ProviderStreamChunk(content="The clock "),
                ProviderStreamChunk(content="advanced.", finish_reason="stop"),
            ],
        ]
    )
    owner = _OwnerProjection()
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
    )
    observed_events: list[ProviderEvent] = []

    def record_events() -> Iterable[ProviderEvent]:
        for event in adapter.for_turn("turn-provider")(_provider_input()):
            observed_events.append(event)
            yield event

    events = iter(record_events())

    private = next(events)
    assert private.kind is ProviderEventKind.PRIVATE_REASONING
    action = _next_action_event(events)
    assert action.kind is ProviderEventKind.ACTION
    assert action.action_command is not None
    assert action.action_command.package_id == "synthetic.clock"
    assert action.action_command.tool_name == "advance"
    assert action.action_command.parameters == {
        "steps": 2,
        "actor": "rift-player-character",
        "actor_state": "rift-player-character",
    }
    assert action.action_command.expected_revision == 4
    assert action.action_command.turn_id == "turn-provider"

    owner.revision = 5
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={
                "status": "committed",
                "result": {"before": 0, "after": 2},
            },
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=2,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=3,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id="unrelated-action",
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )
    talk = [event for event in events if event.kind is ProviderEventKind.TALK]
    assert [event.text for event in talk] == ["The clock advanced."]
    assert len(provider.calls) == 2
    second_messages = provider.calls[1][0]
    facts_controls = [
        message
        for message in second_messages
        if str(message.get("content", "")).startswith("SYLVIA_GM_EXACT_NARRATION_FACTS")
    ]
    assert len(facts_controls) == 1
    assert "assistant prose emitted alongside a tool call" in str(facts_controls[0]["content"])
    assert [message for message in second_messages if message.get("role") == "user"] == [
        message for message in provider.calls[0][0] if message.get("role") == "user"
    ]
    tool_result = next(message for message in second_messages if message["role"] == "tool")
    result_payload = json.loads(str(tool_result["content"]))
    assert result_payload == {
        "action_id": action.action_command.action_id,
        "campaign_revision": 5,
        "current_entities": {"rift-player-character": {"armor_class": 16, "hp": 12}},
        "result": {"after": 2, "before": 0},
        "safe_payload": {"status": "committed"},
        "status": "action_committed",
    }
    if native_continuation == "chat":
        private_records = [
            event for event in observed_events
            if event.kind is ProviderEventKind.PRIVATE_CONTINUATION
        ]
        assert any(
            json.loads(event.text or "{}").get("chat_completion_metadata") == chat_metadata
            for event in private_records
        )
        native_message = next(
            message for message in second_messages if "reasoning_details" in message
        )
        assert native_message["reasoning_details"] == chat_metadata["reasoning_details"]
        assert native_message["tool_calls"][0]["extra_content"] == (
            chat_metadata["tool_call_extras"]["call-one"]
        )
    elif native_continuation:
        private_records = [
            event for event in observed_events
            if event.kind is ProviderEventKind.PRIVATE_CONTINUATION
        ]
        assert any(
            json.loads(event.text or "{}").get("anthropic_content") == native_blocks
            for event in private_records
        )
        assert all(event.response_id for event in private_records)
        native_message = next(
            message for message in second_messages if "anthropic_content" in message
        )
        assert native_message["anthropic_content"] == native_blocks
        request = AnthropicMessagesTransport().format_request(
            "minimax-m3", second_messages, None, True
        )
        assert {"role": "assistant", "content": native_blocks} in request["messages"]
    else:
        assert "PRIVATE PROVIDER REASONING" not in str(second_messages)
        assert "The player wants to advance" not in str(second_messages)
    safe_events = [
        event for event in observed_events if event.kind not in {
            ProviderEventKind.PRIVATE_REASONING, ProviderEventKind.PRIVATE_CONTINUATION,
        }
    ]
    assert "PRIVATE PROVIDER REASONING" not in str(safe_events)
    assert "private-signature" not in str(safe_events)
    assert any(event.evidence and event.evidence.get("kind") == "provider_final_response_reference"
               for event in safe_events)
    print("F4 PROVIDER ADAPTER", "tool=advance", "revision=4->5", "talk=streamed")


@pytest.mark.parametrize(
    "leaked",
    (
        "The player wants to follow the trail. I think I'll lead it to a stream.\n\n"
        "Actually, let me think about this more carefully. I should show three clues first.",
        "The player's declared action is a broad, light look around. I should describe only "
        "what is plainly visible. No Ruleset tool is needed.\n\nNow I will narrate it.",
        "I'll prepare the opening scene now.",
    ),
)
def test_private_deliberation_in_content_is_repaired_before_talk(leaked: str) -> None:
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content=leaked, finish_reason="stop")],
            [
                ProviderStreamChunk(
                    content="Crushed ferns lead away from the clearing toward running water.",
                    finish_reason="stop",
                )
            ],
        ]
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(_provider_input(user_text="I follow the trail."))
    )

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        "Crushed ferns lead away from the clearing toward running water."
    ]
    assert len(provider.calls) == 2
    assert "SYLVIA_GM_PRIVATE_DELIBERATION_CORRECTION" in str(provider.calls[1][0][-1])
    assert leaked not in " ".join(
        event.text or "" for event in events if event.kind is ProviderEventKind.TALK
    )
    assert not any(event.kind is ProviderEventKind.PRIVATE_REASONING for event in events)


def test_committed_mechanic_empty_completion_is_reprompted_in_the_same_turn() -> None:
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-advance",
                        "function": {
                            "name": "advance",
                            "arguments": ('{"steps":1,"actor":"rift-player-character"}'),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [ProviderStreamChunk(finish_reason="stop")],
            [
                ProviderStreamChunk(
                    content="The clock advanced.",
                    finish_reason="stop",
                )
            ],
        ]
    )
    owner = _OwnerProjection()
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
    )
    events = iter(adapter.for_turn("turn-provider")(_provider_input()))

    action = _next_action_event(events)
    assert action.action_command is not None
    owner.revision = 5
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={
                "status": "committed",
                "result": {"status": "committed", "before": 0, "after": 1},
            },
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=2,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )

    remaining = tuple(events)
    assert [event.text for event in remaining if event.kind is ProviderEventKind.TALK] == [
        "The clock advanced."
    ]
    assert len(provider.calls) == 3
    correction_messages = provider.calls[2][0]
    assert any(
        str(message.get("content", "")).startswith("SYLVIA_GM_EMPTY_COMPLETION_CORRECTION")
        for message in correction_messages
    )
    assert 'before":0' in str(correction_messages)


def test_invalid_provider_tool_json_has_a_safe_specific_failure_code() -> None:
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-invalid-json",
                        "function": {
                            "name": "advance",
                            "arguments": '{"steps":',
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [ProviderStreamChunk(content="I cannot safely continue.", finish_reason="stop")],
        ]
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()),
        provider,
        owner_id="owner-provider",
    )

    with pytest.raises(CommandRejected) as captured:
        tuple(adapter.for_turn("turn-provider")(_provider_input()))

    assert captured.value.code == "provider_tool_correction_abandoned"
    correction_input = provider.calls[1][0]
    assert "provider_tool_arguments_invalid_json" in str(correction_input)
    tool_result = next(
        json.loads(str(message["content"]))
        for message in correction_input
        if message.get("role") == "tool"
    )
    assert tool_result["provider_response_finish_reason"] == "tool_calls"
    assert tool_result["received_argument_characters"] == len('{"steps":')
    assert tool_result["json_error"] == {
        "message": "Expecting value",
        "line": 1,
        "column": 10,
        "position": 9,
    }
    assert "complete JSON object" in tool_result["instruction"]


def test_empty_completion_reports_the_failed_optional_tool_round_to_the_same_gm() -> None:
    narration = "Rain gathers on the leaves while you wait."
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(finish_reason="stop")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-file-after-empty",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": narration}
                                    ]
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _provider_input(user_text="I wait quietly.")
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=lambda _parameters: {
            "status": "campaign_file_unchanged",
            "campaign_revision": 4,
            "state_changed": False,
        },
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    diagnostic = next(
        event.evidence
        for event in events
        if event.kind is ProviderEventKind.EVIDENCE
        and event.evidence is not None
        and event.evidence.get("kind") == "provider_empty_completion"
    )
    assert diagnostic == {
        "kind": "provider_empty_completion",
        "turn_id": "turn-provider",
        "inference_id": diagnostic["inference_id"],
        "basis_message_id": "message-provider",
        "finish_reason": "stop",
        "tool_call_required": True,
        "correction_attempt": 1,
        "max_corrections": 2,
    }
    correction = next(
        str(message["content"])
        for message in provider.calls[1][0]
        if str(message.get("content", "")).startswith("SYLVIA_GM_EMPTY_COMPLETION_CORRECTION")
    )
    assert correction.startswith("SYLVIA_GM_EMPTY_COMPLETION_CORRECTION")
    assert "completed with finish_reason='stop'" in correction
    assert "A required tool call was expected" in correction
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [narration]
    assert len(provider.calls) == 2


def test_repeated_empty_completion_is_bounded_and_never_gets_host_fallback_prose() -> None:
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-missed-attack",
                        "function": {
                            "name": "advance",
                            "arguments": ('{"steps":1,"actor":"rift-player-character"}'),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [ProviderStreamChunk(finish_reason="stop")],
            [ProviderStreamChunk(finish_reason="stop")],
            [ProviderStreamChunk(finish_reason="stop")],
        ]
    )
    owner = _OwnerProjection()
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
    )
    events = iter(adapter.for_turn("turn-provider")(_provider_input()))

    action = _next_action_event(events)
    assert action.action_command is not None
    owner.revision = 5
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={
                "status": "committed",
                "result": {
                    "status": "committed",
                    "context": {"_attack_outcome": "miss"},
                },
            },
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=2,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )

    with pytest.raises(CommandRejected) as captured:
        tuple(events)
    assert captured.value.code == "provider_empty_completion"
    assert len(provider.calls) == 4


def test_empty_completion_retry_budget_resets_after_substantive_tool_attempt() -> None:
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(finish_reason="stop")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-invalid-json",
                        "function": {
                            "name": "advance",
                            "arguments": '{"steps":',
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [ProviderStreamChunk(finish_reason="stop")],
            [ProviderStreamChunk(finish_reason="stop")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-valid-after-progress",
                        "function": {
                            "name": "advance",
                            "arguments": ('{"steps":1,"actor":"rift-player-character"}'),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    content="The clock advances.",
                    finish_reason="stop",
                )
            ],
        ]
    )
    owner = _OwnerProjection()
    events = iter(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(_provider_input())
    )

    observed: list[ProviderEvent] = []
    while True:
        event = next(events)
        observed.append(event)
        if event.kind is ProviderEventKind.ACTION:
            action = event
            break
    assert action.action_command is not None
    owner.revision = 5
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={
                "status": "committed",
                "result": {"status": "committed", "before": 0, "after": 1},
            },
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=2,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )

    observed.extend(events)

    assert [event.text for event in observed if event.kind is ProviderEventKind.TALK] == [
        "The clock advances."
    ]
    empty_diagnostics = [
        event.evidence
        for event in observed
        if event.kind is ProviderEventKind.EVIDENCE
        and event.evidence is not None
        and event.evidence.get("kind") == "provider_empty_completion"
    ]
    assert [item["correction_attempt"] for item in empty_diagnostics] == [1, 1, 2]
    assert len(provider.calls) == 6


def test_automatic_lifecycle_commit_continues_unresolved_player_intent_with_tools() -> None:
    begin_turn = ModelMechanicsTool(
        package_id="synthetic.rules",
        package_version="1",
        package_sha256="a" * 64,
        name="begin_turn",
        capability="synthetic.turn",
        description="Begin the required turn.",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        model_behavior=ModelToolBehavior.REQUIRED_ENGINE_TRANSITION,
    )
    advance = _provider_input().catalog[0]
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-advance-after-begin",
                        "function": {
                            "name": "advance",
                            "arguments": ('{"steps":1,"actor":"rift-player-character"}'),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [ProviderStreamChunk(content="The action resolves.", finish_reason="stop")],
        ]
    )
    owner = _OwnerProjection()
    source = _provider_input()
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(begin_turn,),
        control=source.control,
        catalog_supplier=lambda _discovery_id: (begin_turn,) if owner.revision == 4 else (advance,),
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
    )
    events = iter(adapter.for_turn("turn-provider")(provider_input))

    begin_action = _next_action_event(events)
    assert begin_action.action_command is not None
    owner.revision = 5
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=begin_action.action_command.action_id,
            safe_payload={"status": "committed", "result": {"status": "committed"}},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=2,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=begin_action.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )

    advance_action = _next_action_event(events)
    assert advance_action.action_command is not None
    assert advance_action.action_command.tool_name == "advance"
    owner.revision = 6
    owner.events = (
        *owner.events,
        CanonicalEvent(
            sequence=3,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=advance_action.action_command.action_id,
            safe_payload={
                "status": "committed",
                "result": {"status": "committed", "before": 0, "after": 1},
            },
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=4,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=advance_action.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        "The action resolves."
    ]
    recovery_messages, recovery_tools = provider.calls[0]
    assert recovery_tools is not None
    assert all(str(tool["function"]["name"]).endswith("_advance") for tool in recovery_tools)
    assert "SYLVIA_GM_AUTOMATIC_ENGINE_TRANSITION_RESULT" in str(recovery_messages)


def test_model_selected_start_combat_resolves_the_already_declared_player_attack() -> None:
    """Initiative cannot turn the triggering player action into a second prompt."""

    def tool(
        name: str,
        *,
        model_behavior: ModelToolBehavior = ModelToolBehavior.OPTIONAL,
        category: str = "mechanic",
        capability: str = "synthetic.combat",
    ) -> ModelMechanicsTool:
        return ModelMechanicsTool(
            package_id="synthetic.rules",
            package_version="1",
            package_sha256="a" * 64,
            name=name,
            capability=capability,
            description=f"Execute {name}.",
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            category=category,
            model_behavior=model_behavior,
        )

    start_combat = tool(
        "start_combat_encounter_entities",
        model_behavior=ModelToolBehavior.LEAVES_PLAYER_INTENT_PENDING,
    )
    begin_turn = tool(
        "begin_combat_turn",
        model_behavior=ModelToolBehavior.REQUIRED_ENGINE_TRANSITION,
    )
    attack = tool(
        "resolve_combat_attack",
        model_behavior=ModelToolBehavior.RESOLVES_PLAYER_INTENT,
    )
    end_turn = tool("end_combat_turn")
    campaign_file = tool(
        "update_campaign_file",
        category="narrative_state",
        capability="narrative_state.update_campaign_file",
    )

    class _RequiredAwareAttackProvider(_ScriptedProvider):
        def __init__(self) -> None:
            super().__init__([])

        async def generate_stream(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            timeout: float = 120.0,
            **kwargs: object,
        ) -> AsyncIterator[ProviderStreamChunk]:
            del timeout
            call_index = len(self.calls)
            self.calls.append((messages.copy(), tools))
            self.request_kwargs.append(dict(kwargs))
            if call_index == 0:
                yield ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-start-combat",
                        "function": {
                            "name": "start_combat_encounter_entities",
                            "arguments": "{}",
                        },
                    },
                    finish_reason="tool_calls",
                )
            elif call_index == 1:
                yield ProviderStreamChunk(
                    content="Initiative puts you first. What do you do?",
                    finish_reason="stop",
                )
            elif call_index == 2:
                assert tools is not None
                assert len(tools) == 1
                assert str(tools[0]["function"]["name"]).endswith("_resolve_combat_attack")
                yield ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-attack",
                        "function": {
                            "name": tools[0]["function"]["name"],
                            "arguments": "{}",
                        },
                    },
                    finish_reason="tool_calls",
                )
            else:
                yield ProviderStreamChunk(
                    content=(
                        "Initiative puts you first. Your dart strikes the Riftwolf exactly as "
                        "you intended."
                    ),
                    finish_reason="stop",
                )

    provider = _RequiredAwareAttackProvider()
    owner = _OwnerProjection()
    source = _provider_input(user_text="I attack it with a dart.")
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(start_combat,),
        control=source.control,
        catalog_supplier=lambda _discovery_id: {
            4: (start_combat,),
            5: (begin_turn,),
            6: (attack, end_turn, campaign_file),
        }.get(owner.revision, ()),
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
    )
    events = iter(adapter.for_turn("turn-provider")(provider_input))

    def commit(action: ProviderEvent, revision: int, result: dict[str, object]) -> None:
        assert action.action_command is not None
        owner.revision = revision
        owner.events = (
            *owner.events,
            CanonicalEvent(
                sequence=len(owner.events) + 1,
                event_type=CanonicalEventType.TOOL_RESULT,
                turn_id="turn-provider",
                action_id=action.action_command.action_id,
                safe_payload={"status": "committed", "result": result},
                visibility=EventVisibility.PLAYER_SAFE,
            ),
            CanonicalEvent(
                sequence=len(owner.events) + 2,
                event_type=CanonicalEventType.ACTION_COMMITTED,
                turn_id="turn-provider",
                action_id=action.action_command.action_id,
                safe_payload={"status": "committed"},
                visibility=EventVisibility.PLAYER_SAFE,
            ),
        )

    start_action = _next_action_event(events)
    assert start_action.action_command is not None
    assert start_action.action_command.tool_name == "start_combat_encounter_entities"
    commit(start_action, 5, {"status": "committed"})

    begin_action = _next_action_event(events)
    assert begin_action.action_command is not None
    assert begin_action.action_command.tool_name == "begin_combat_turn"
    commit(begin_action, 6, {"status": "committed"})
    owner.boundary = ModelDecisionBoundary(
        owner=ModelDecisionOwner.PLAYER,
        actor_id="rift-player-character",
        actor_name="Daniel",
        actor_state={"hp": 12},
        campaign_revision=6,
    )

    attack_action = _next_action_event(events)
    assert attack_action.action_command is not None
    assert attack_action.action_command.tool_name == "resolve_combat_attack"
    commit(
        attack_action,
        7,
        {
            "status": "committed",
            "context": {
                "_attack_outcome": "hit",
                "_target_id": "rift-scarred-hound",
            },
        },
    )

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        "Initiative puts you first. Your dart strikes the Riftwolf exactly as you intended."
    ]
    assert len(provider.calls) == 4
    assert [kwargs.get("tool_choice") for kwargs in provider.request_kwargs] == [
        None,
        "required",
        "required",
        None,
    ]
    assert provider.calls[1][1] is not None
    assert provider.calls[2][1] is not None
    assert provider.calls[3][1] is None
    player_boundary = str(provider.calls[1][0][-1]["content"])
    assert '"current_player_action":"I attack it with a dart."' in player_boundary
    assert '"committed_operations_for_this_request"' in player_boundary
    assert "start_combat_encounter_entities" in player_boundary
    assert "resolve_combat_attack" not in player_boundary
    assert "uncertain part of that exact action" in player_boundary
    assert "Never choose a new action" in player_boundary
    assert "SYLVIA_GM_REQUIRED_TOOL_CALL_CORRECTION" in str(provider.calls[2][0])
    assert "Initiative puts you first. What do you do?" not in str(provider.calls[2][0])
    resolved_boundary = str(provider.calls[3][0][-1]["content"])
    assert "resolve_combat_attack" in resolved_boundary
    assert "Do not announce an NPC or companion turn" in resolved_boundary
    assert "SYLVIA_GM_REQUIRED_MECHANIC" not in str(provider.calls[0][0])


@pytest.mark.parametrize("with_preparation", [False, True])
def test_restart_recovers_pending_player_intent_before_tool_result_evidence(with_preparation) -> None:
    """A committed setup mechanic cannot lose the player's action across interruption."""

    def tool(
        name: str,
        *,
        behavior: ModelToolBehavior = ModelToolBehavior.OPTIONAL,
        category: str = "mechanic",
        capability: str = "synthetic.combat",
    ) -> ModelMechanicsTool:
        return ModelMechanicsTool(
            package_id="synthetic.rules",
            package_version="1",
            package_sha256="a" * 64,
            name=name,
            capability=capability,
            description=f"Execute {name}.",
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            category=category,
            model_behavior=behavior,
        )

    attack = tool(
        "resolve_combat_attack",
        behavior=ModelToolBehavior.RESOLVES_PLAYER_INTENT,
    )
    end_turn = tool("end_combat_turn")
    preparation = tool("move_actor_to", behavior=ModelToolBehavior.SUPPORTS_PLAYER_INTENT)
    campaign_file = tool(
        "update_campaign_file",
        category="narrative_state",
        capability="narrative_state.update_campaign_file",
    )
    source = _provider_input(user_text="I attack it with a dart.")
    user = source.history.messages[0]
    start_action_id = "action-start-before-interruption"
    mechanics_contract = SessionMessage(
        session_id=source.session.session_id,
        sequence=2,
        message_id="evidence-start-mechanics-contract",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_mechanics_action_contract",
            "turn_id": "turn-provider",
            "inference_id": "inference-start-before-interruption",
            "basis_message_id": user.message_id,
            "action_id": start_action_id,
            "operation": "start_combat_encounter_entities",
            "model_behavior": "leaves_player_intent_pending",
            "decision_owner": None,
        },
    )
    history = SessionContext(
        session=source.session,
        messages=(user, mechanics_contract),
    )
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-resume-attack",
                        "function": {
                            "name": "resolve_combat_attack",
                            "arguments": "{}",
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    content="Your dart strikes. What do you do next?",
                    finish_reason="stop",
                )
            ],
        ]
    )
    owner = _OwnerProjection()
    owner.revision = 5
    owner.boundary = ModelDecisionBoundary(
        owner=ModelDecisionOwner.PLAYER,
        actor_id="rift-player-character",
        actor_name="Daniel",
        actor_state={"hp": 12},
        campaign_revision=5,
    )
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=start_action_id,
            safe_payload={"status": "committed", "result": {"status": "committed"}},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=2,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=start_action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )
    provider_input = SessionProviderInput(
        session=source.session,
        history=history,
        catalog=(),
        control=source.control,
        basis_message_id=user.message_id,
        recovery_kind="post_mechanics_narration",
        retry_action_receipt=ActionReceipt(
            campaign_id="campaign-provider",
            action_id=start_action_id,
            status=ActionStatus.COMMITTED,
            result={"status": "committed"},
            resulting_revision=5,
        ),
        catalog_supplier=lambda _discovery_id: {
            5: (attack, end_turn, campaign_file, *((preparation,) if with_preparation else ())),
        }.get(owner.revision, ()),
    )
    events = iter(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    attack_action = _next_action_event(events)
    assert attack_action.action_command is not None
    assert attack_action.action_command.tool_name == attack.name
    owner.revision = 6
    owner.events = (
        *owner.events,
        CanonicalEvent(
            sequence=3,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=attack_action.action_command.action_id,
            safe_payload={
                "status": "committed",
                "result": {"status": "committed", "context": {"_attack_outcome": "hit"}},
            },
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=4,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=attack_action.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        "Your dart strikes. What do you do next?"
    ]
    assert provider.request_kwargs[0]["tool_choice"] == "required"
    assert provider.calls[0][1] is not None
    assert _tool_operations(provider.calls[0][1]) == [
        "resolve_combat_attack", *(("move_actor_to",) if with_preparation else ()),
    ]
    assert "end_combat_turn" not in _tool_operations(provider.calls[0][1])
    assert "update_campaign_file" not in _tool_operations(provider.calls[0][1])


def test_gm_boundary_asks_model_then_engine_advances_to_player() -> None:
    """A GM creature choice stays model-owned; lifecycle bookkeeping does not."""

    def tool(
        name: str,
        *,
        required_transition: bool,
        model_behavior: ModelToolBehavior | None = None,
    ) -> ModelMechanicsTool:
        return ModelMechanicsTool(
            package_id="synthetic.rules",
            package_version="1",
            package_sha256="a" * 64,
            name=name,
            capability="synthetic.combat",
            description=f"Execute {name}.",
            input_schema={
                "type": "object",
                "properties": {},
                "required": [],
                "additionalProperties": False,
            },
            model_behavior=(
                model_behavior
                if model_behavior is not None
                else (
                    ModelToolBehavior.REQUIRED_ENGINE_TRANSITION
                    if required_transition
                    else ModelToolBehavior.OPTIONAL
                )
            ),
        )

    lifecycle = (
        tool(
            "start_combat_encounter_entities",
            required_transition=False,
            model_behavior=ModelToolBehavior.LEAVES_PLAYER_INTENT_PENDING,
        ),
        tool("begin_combat_turn", required_transition=True),
        tool("resolve_adjudicated_creature_attack", required_transition=False),
        tool("end_combat_turn", required_transition=True),
        tool("advance_combat_encounter_turn", required_transition=True),
        tool("begin_combat_turn", required_transition=True),
    )
    player_attack = tool(
        "resolve_combat_attack",
        required_transition=False,
        model_behavior=ModelToolBehavior.RESOLVES_PLAYER_INTENT,
    )

    class _NpcFirstProvider(_ScriptedProvider):
        def __init__(self) -> None:
            super().__init__([])

        async def generate_stream(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            timeout: float = 120.0,
            **kwargs: object,
        ) -> AsyncIterator[ProviderStreamChunk]:
            del timeout
            call_index = len(self.calls)
            self.calls.append((messages.copy(), tools))
            self.request_kwargs.append(dict(kwargs))
            if call_index in {0, 1, 2}:
                operation = (
                    lifecycle[0].name
                    if call_index == 0
                    else lifecycle[2].name
                    if call_index == 1
                    else player_attack.name
                )
                assert tools is not None and len(tools) == 1
                yield ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": f"call-{call_index}-{operation}",
                        "function": {
                            "name": tools[0]["function"]["name"],
                            "arguments": "{}",
                        },
                    },
                    finish_reason="tool_calls",
                )
                return
            yield ProviderStreamChunk(
                content=(
                    "Combat begins and the Riftwolf acts first. Its attack misses. "
                    "Your quarterstaff then strikes the Riftwolf. What do you do next?"
                ),
                finish_reason="stop",
            )

    provider = _NpcFirstProvider()
    owner = _OwnerProjection()
    source = _provider_input(user_text="I attack the Riftwolf with my quarterstaff.")
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(lifecycle[0],),
        control=source.control,
        catalog_supplier=lambda _discovery_id: {
            4: (lifecycle[0],),
            5: (lifecycle[1],),
            6: (lifecycle[2],),
            7: (lifecycle[3],),
            8: (lifecycle[4],),
            9: (lifecycle[5],),
            10: (player_attack,),
        }.get(owner.revision, ()),
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
    )
    events = iter(adapter.for_turn("turn-provider")(provider_input))

    for index, expected_tool in enumerate(lifecycle):
        action = _next_action_event(events)
        assert action.action_command is not None, (index, expected_tool.name, action)
        assert action.action_command.tool_name == expected_tool.name
        owner.revision = 5 + index
        context: dict[str, object] = {}
        if expected_tool.name == "resolve_adjudicated_creature_attack":
            context["_attack_outcome"] = "miss"
        owner.events = (
            *owner.events,
            CanonicalEvent(
                sequence=len(owner.events) + 1,
                event_type=CanonicalEventType.TOOL_RESULT,
                turn_id="turn-provider",
                action_id=action.action_command.action_id,
                safe_payload={
                    "status": "committed",
                    "result": {"status": "committed", "context": context},
                },
                visibility=EventVisibility.PLAYER_SAFE,
            ),
            CanonicalEvent(
                sequence=len(owner.events) + 2,
                event_type=CanonicalEventType.ACTION_COMMITTED,
                turn_id="turn-provider",
                action_id=action.action_command.action_id,
                safe_payload={"status": "committed"},
                visibility=EventVisibility.PLAYER_SAFE,
            ),
        )
        if index == 1:
            owner.boundary = ModelDecisionBoundary(
                owner=ModelDecisionOwner.GM,
                actor_id="rift-scarred-hound",
                actor_name="Riftwolf",
                actor_state={"hp": 12},
                campaign_revision=owner.revision,
            )
        elif index == len(lifecycle) - 1:
            owner.boundary = ModelDecisionBoundary(
                owner=ModelDecisionOwner.PLAYER,
                actor_id="rift-player-character",
                actor_name="Daniel",
                actor_state={"hp": 12},
                campaign_revision=owner.revision,
            )

    attack_action = _next_action_event(events)
    assert attack_action.action_command is not None
    assert attack_action.action_command.tool_name == player_attack.name
    owner.revision = 11
    owner.events = (
        *owner.events,
        CanonicalEvent(
            sequence=len(owner.events) + 1,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=attack_action.action_command.action_id,
            safe_payload={
                "status": "committed",
                "result": {
                    "status": "committed",
                    "context": {"_attack_outcome": "hit"},
                },
            },
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=len(owner.events) + 2,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=attack_action.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        "Combat begins and the Riftwolf acts first. Its attack misses. "
        "Your quarterstaff then strikes the Riftwolf. What do you do next?"
    ]
    assert len(provider.calls) == 4
    assert [kwargs.get("tool_choice") for kwargs in provider.request_kwargs] == [
        None,
        "required",
        "required",
        None,
    ]
    assert "SYLVIA_GM_DECISION_REQUEST" in str(provider.calls[1][0])
    assert "SYLVIA_GM_PLAYER_BOUNDARY_NARRATION" in str(provider.calls[2][0])
    assert "resolve_combat_attack" in str(provider.calls[3][0])
    assert (
        sum(event.event_type is CanonicalEventType.ACTION_COMMITTED for event in owner.events)
        == len(lifecycle) + 1
    )


def test_gm_decision_boundary_cannot_finalize_campaign_file_before_turn_progression() -> None:
    """Held prose cannot strand an active GM-controlled combat turn."""

    source = _provider_input(user_text="I attack Chloe with my quarterstaff.")
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content="Chloe reacts.", finish_reason="stop")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-continue-gm-turn",
                        "function": {
                            "name": "advance",
                            "arguments": (
                                '{"steps":1,"actor":"rift-companion-chloe","actor_state":"ready"}'
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    owner = _OwnerProjection()
    owner.boundary = ModelDecisionBoundary(
        owner=ModelDecisionOwner.GM,
        actor_id="rift-companion-chloe",
        actor_name="Chloe",
        actor_state={"hp": 12},
        campaign_revision=4,
    )
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(*source.catalog, _campaign_file_tool(allow_unchanged=True)),
        control=source.control,
        basis_message_id=source.history.messages[0].message_id,
        recovery_kind="post_mechanics_narration",
        retry_action_receipt=ActionReceipt(
            campaign_id="campaign-provider",
            action_id="action-combat-started",
            status=ActionStatus.COMMITTED,
            result={"status": "committed"},
            resulting_revision=4,
        ),
    )

    events = iter(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )
    seen: list[ProviderEvent] = []
    while True:
        event = next(events)
        seen.append(event)
        if event.kind is ProviderEventKind.ACTION:
            action = event
            break

    assert action.action_command is not None
    assert action.action_command.tool_name == "advance"
    assert len(provider.calls) == 2
    messages, tools = provider.calls[0]
    assert tools is not None
    exposed = [str(tool["function"]["name"]) for tool in tools]
    assert any(name.endswith("_advance") for name in exposed)
    assert not any(name.endswith("_update_campaign_file") for name in exposed)
    assert provider.request_kwargs[0]["tool_choice"] == "required"
    assert "Campaign File disposition is unavailable" in str(messages[-1]["content"])
    assert any(
        event.evidence is not None
        and event.evidence.get("kind") == "provider_forced_tool_call_missing"
        for event in seen
    )
    assert "Chloe reacts." not in str(provider.calls[1][0])


def test_resume_keeps_gm_boundary_draft_private_and_ruleset_tools_available() -> None:
    source = _provider_input(user_text="I attack Chloe with my quarterstaff.")
    user = source.history.messages[0]
    draft = "Chloe recoils and reaches for her sword."
    held = SessionMessage(
        session_id=source.session.session_id,
        sequence=2,
        message_id="inference-gm-boundary-draft",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_gm_boundary_draft",
            "turn_id": "turn-provider",
            "inference_id": "inference-gm-boundary",
            "basis_message_id": user.message_id,
            "assistant": {"role": "assistant", "content": draft},
        },
    )
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-resumed-gm-turn",
                        "function": {
                            "name": "advance",
                            "arguments": (
                                '{"steps":1,"actor":"rift-companion-chloe","actor_state":"ready"}'
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ]
        ]
    )
    owner = _OwnerProjection()
    owner.boundary = ModelDecisionBoundary(
        owner=ModelDecisionOwner.GM,
        actor_id="rift-companion-chloe",
        actor_name="Chloe",
        actor_state={"hp": 12},
        campaign_revision=4,
    )
    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(session=source.session, messages=(user, held)),
        catalog=(*source.catalog, _campaign_file_tool(allow_unchanged=True)),
        control=source.control,
        basis_message_id=user.message_id,
        recovery_kind="post_mechanics_narration",
        retry_action_receipt=ActionReceipt(
            campaign_id="campaign-provider",
            action_id="action-combat-started",
            status=ActionStatus.COMMITTED,
            result={"status": "committed"},
            resulting_revision=4,
        ),
    )

    action = _next_action_event(
        iter(
            ProviderSessionAdapter(
                cast(AuthorityOwner, owner),
                provider,
                owner_id="owner-provider",
            ).for_turn("turn-provider")(provider_input)
        )
    )

    assert action.action_command is not None
    assert action.action_command.tool_name == "advance"
    messages, tools = provider.calls[0]
    assert tools is not None
    assert len(tools) == 1
    assert str(tools[0]["function"]["name"]).endswith("_advance")
    rendered = str(messages)
    assert "SYLVIA_GM_UNRELEASED_GM_BOUNDARY_DRAFT" in rendered
    assert draft in rendered
    assert "provider_held_narration" not in rendered
    assert provider.request_kwargs[0]["tool_choice"] == "required"


def test_unavailable_tool_is_rejected_without_hidden_correction_inference() -> None:
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-stale",
                        "function": {
                            "name": "tool_9_stale_operation",
                            "arguments": "{}",
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [ProviderStreamChunk(content="I wait for the current choice.", finish_reason="stop")],
        ]
    )
    owner = _OwnerProjection()
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
    )

    with pytest.raises(CommandRejected) as captured:
        tuple(adapter.for_turn("turn-provider")(_provider_input()))

    assert captured.value.code == "provider_tool_correction_abandoned"
    assert len(provider.calls) == 2
    assert owner.revision == 4
    correction_messages = str(provider.calls[1][0])
    assert '"available_operations"' in correction_messages
    assert '"provider_tool_name":"tool_' in correction_messages
    assert '_advance"' in correction_messages
    assert "Do not call it again in this correction" in correction_messages
    assert "a different currently exposed operation" in correction_messages


def test_session_mechanics_feedback_preserves_bounded_schema_details() -> None:
    feedback = _mechanics_rejection_feedback(
        MechanicsRejected(
            "The operation requires at least three combatants.",
            code="invalid_schema",
            field_paths=("combatants",),
            validation_issues=(
                MechanicsValidationIssue(
                    path="combatants",
                    kind="too_few_items",
                    expected_type="array",
                    parent_path="$",
                    minimum=3,
                    maximum=16,
                ),
            ),
            state_changed=False,
        )
    )

    assert feedback["validation"] == {
        "field_paths": ["combatants"],
        "issues": [
            {
                "path": "combatants",
                "kind": "too_few_items",
                "expected_type": "array",
                "parent_path": "$",
                "minimum": 3,
                "maximum": 16,
            }
        ],
    }
    assert "choose a different currently exposed operation" in str(feedback["instruction"])


def test_bare_unavailable_tool_is_rejected_without_alias_repair() -> None:
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-bare-stale",
                        "function": {
                            "name": "advance_combat_encounter_turn",
                            "arguments": "{}",
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [ProviderStreamChunk(content="I wait for the current choice.", finish_reason="stop")],
        ]
    )
    owner = _OwnerProjection()
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
    )

    with pytest.raises(CommandRejected) as captured:
        tuple(adapter.for_turn("turn-provider")(_provider_input()))

    assert captured.value.code == "provider_tool_correction_abandoned"
    assert len(provider.calls) == 2
    assert owner.revision == 4


def test_arbitrary_unavailable_tool_is_rejected_without_semantic_repair() -> None:
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-arbitrary-stale",
                        "function": {
                            "name": "search_campaign_catalog",
                            "arguments": '{"query":"nearby creatures"}',
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [ProviderStreamChunk(content="The path remains quiet.", finish_reason="stop")],
        ]
    )
    owner = _OwnerProjection()
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
    )

    with pytest.raises(CommandRejected) as captured:
        tuple(adapter.for_turn("turn-provider")(_provider_input()))

    assert captured.value.code == "provider_tool_correction_abandoned"
    assert len(provider.calls) == 2
    assert owner.revision == 4


def test_player_wording_never_forces_an_available_mechanic() -> None:
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    content="The clock advances by two steps and the gate opens.",
                    finish_reason="stop",
                )
            ]
        ]
    )
    owner = _OwnerProjection()
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
    )

    events = tuple(adapter.for_turn("turn-provider")(_provider_input()))

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        "The clock advances by two steps and the gate opens."
    ]
    assert provider.request_kwargs[0].get("tool_choice") is None
    assert "SYLVIA_GM_REQUIRED_MECHANIC" not in str(provider.calls[0][0])
    assert owner.revision == 4


def test_exact_narration_facts_survive_followup_tool_rounds() -> None:
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-attack",
                        "function": {
                            "name": "advance",
                            "arguments": '{"steps":1,"actor":"rift-player-character"}',
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-lifecycle",
                        "function": {
                            "name": "advance",
                            "arguments": '{"steps":1,"actor":"rift-player-character"}',
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [ProviderStreamChunk(content="The creature misses.", finish_reason="stop")],
        ]
    )
    owner = _OwnerProjection()
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
    )
    events = iter(adapter.for_turn("turn-provider")(_provider_input()))

    first_action = _next_action_event(events)
    assert first_action.action_command is not None
    owner.revision = 5
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=first_action.action_command.action_id,
            safe_payload={
                "status": "committed",
                "result": {
                    "status": "committed",
                    "context": {
                        "_attack_outcome": "miss",
                        "_attack_roll": {"total": 4, "modifier": 3},
                        "_target_id": "rift-player-character",
                    },
                },
            },
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=2,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=first_action.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )

    second_action = _next_action_event(events)
    assert second_action.action_command is not None
    owner.revision = 6
    owner.events = (
        *owner.events,
        CanonicalEvent(
            sequence=3,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=second_action.action_command.action_id,
            safe_payload={
                "status": "committed",
                "result": {"status": "committed", "context": {}},
            },
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=4,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=second_action.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        "The creature misses."
    ]
    final_messages = provider.calls[2][0]
    exact_facts = [
        str(message["content"])
        for message in final_messages
        if str(message.get("content", "")).startswith("SYLVIA_GM_EXACT_NARRATION_FACTS")
    ]
    assert '"attack_outcome":"miss"' in exact_facts[-1]
    assert exact_facts[-1].count('"tool":"advance"') == 2
    assert "Continue this same GM turn from these committed results" in exact_facts[-1]
    assert "finish with the Campaign File disposition" in exact_facts[-1]


def test_later_mechanic_failure_never_denies_an_earlier_commit() -> None:
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-committed",
                        "function": {
                            "name": "advance",
                            "arguments": '{"steps":1,"actor":"rift-player-character"}',
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-unavailable-after-commit",
                        "function": {
                            "name": "tool_9_stale_operation",
                            "arguments": "{}",
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [ProviderStreamChunk(content="I cannot safely continue.", finish_reason="stop")],
            [ProviderStreamChunk(content="I cannot safely continue.", finish_reason="stop")],
            [ProviderStreamChunk(content="I cannot safely continue.", finish_reason="stop")],
        ]
    )
    owner = _OwnerProjection()
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
    )
    events = iter(adapter.for_turn("turn-provider")(_provider_input()))

    committed = _next_action_event(events)
    assert committed.action_command is not None
    owner.revision = 5
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=committed.action_command.action_id,
            safe_payload={
                "status": "committed",
                "result": {"status": "committed", "before": 0, "after": 1},
            },
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=2,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=committed.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )

    with pytest.raises(CommandRejected) as captured:
        tuple(events)
    assert captured.value.code == "provider_tool_correction_abandoned"
    assert len(provider.calls) == 3
    assert owner.revision == 5


def test_package_declared_participant_facts_are_derived_from_authority() -> None:
    parameters = {
        "actor": "rift-player-character",
        "skill": "perception",
        "target_number": 13,
        "circumstantial_modifier": 0,
    }
    derivations = {
        "actor_state": {"kind": "copy", "source": "actor"},
        "kind": {"kind": "constant", "value": "ability_check"},
        "ability": {
            "kind": "enum_map",
            "source": "skill",
            "mapping": {"perception": "wisdom"},
        },
        "proficiency_factor": {
            "kind": "participant_membership",
            "participant": "actor",
            "source": "skill",
            "field": "skill_proficiencies",
            "present": "normal",
            "absent": "none",
        },
    }

    result = _derive_mechanics_parameters(
        parameters,
        derivations,
        (
            SimpleNamespace(
                participant_id="rift-player-character",
                data={"skill_proficiencies": []},
            ),
        ),
    )

    assert result == {
        **parameters,
        "actor_state": "rift-player-character",
        "kind": "ability_check",
        "ability": "wisdom",
        "proficiency_factor": "none",
    }


def test_missing_mechanic_arguments_are_rejected_without_correction_inference() -> None:
    invalid_call = [
        ProviderStreamChunk(
            tool_call_delta={
                "index": 0,
                "id": "call-invalid",
                "function": {
                    "name": "advance",
                    "arguments": '{"steps":2}',
                },
            },
            finish_reason="tool_calls",
        )
    ]
    provider = _ScriptedProvider([invalid_call] * 6)
    owner = _OwnerProjection()
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
    )

    with pytest.raises(CommandRejected) as captured:
        tuple(adapter.for_turn("turn-provider")(_provider_input()))

    assert captured.value.code == "missing_required_arguments"
    assert len(provider.calls) == 6
    assert owner.revision == 4


def _legacy_missing_reconciliation_patch_argument_gets_one_bounded_model_correction() -> None:
    narrative_tool = ModelMechanicsTool(
        package_id="sylvia.core.campaign-state",
        package_version="1",
        package_sha256="c" * 64,
        name="record_campaign_memory",
        capability="campaign_state.record_memory",
        description="Record one durable campaign fact.",
        input_schema={
            "type": "object",
            "properties": {
                "summary": {"type": "string"},
                "visibility": {"type": "string"},
            },
            "required": ["summary", "visibility"],
            "additionalProperties": False,
        },
        category="narrative_state",
        parameter_derivations={"operation": {"kind": "constant", "value": "record_memory"}},
    )
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    content="You secure the clearing for night.",
                    finish_reason="stop",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-memory-missing-summary",
                        "function": {
                            "name": "reconcile_campaign_state",
                            "arguments": '{"facts":[{"visibility":"player_known"}]}',
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-memory-corrected",
                        "function": {
                            "name": "reconcile_campaign_state",
                            "arguments": (
                                '{"facts":[{"summary":'
                                '"The journal page is in the adventurer\'s pack",'
                                '"visibility":"player_known"}]}'
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _provider_input()
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(narrative_tool,),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "narrative_state_committed", "resulting_revision": 5}
        ),
    )
    owner = _OwnerProjection()
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
    )

    events = tuple(adapter.for_turn("turn-provider")(provider_input))

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        "You secure the clearing for night."
    ]
    assert writes == [
        {
            "summary": "The journal page is in the adventurer's pack",
            "visibility": "player_known",
            "operation": "record_memory",
        }
    ]
    assert len(provider.calls) == 4
    correction_messages = provider.calls[2][0]
    assert "SYLVIA_GM_CAMPAIGN_STATE_CORRECTION" in str(correction_messages)
    assert "campaign_state_patch_shape_invalid" in str(correction_messages)


def _legacy_campaign_state_write_may_use_two_precommit_corrections() -> None:
    narrative_tool = ModelMechanicsTool(
        package_id="sylvia.core.campaign-state",
        package_version="1",
        package_sha256="c" * 64,
        name="update_campaign_scene",
        capability="campaign_state.update_scene",
        description="Record the current scene.",
        input_schema={
            "type": "object",
            "properties": {"summary": {"type": "string", "maxLength": 2000}},
            "required": ["summary"],
            "additionalProperties": False,
        },
        category="narrative_state",
        parameter_derivations={"operation": {"kind": "constant", "value": "update_scene"}},
    )
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    content="You finish securing the clearing.",
                    finish_reason="stop",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-scene-missing",
                        "function": {
                            "name": "reconcile_campaign_state",
                            "arguments": '{"scenes":[{}]}',
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-scene-invalid",
                        "function": {
                            "name": "reconcile_campaign_state",
                            "arguments": '{"scenes":[{"summary":"invalid first correction"}]}',
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-scene-corrected",
                        "function": {
                            "name": "reconcile_campaign_state",
                            "arguments": (
                                '{"scenes":[{"summary":"The clearing is secured for night."}]}'
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _provider_input()
    writes: list[dict[str, object]] = []

    def write(parameters: Mapping[str, object]) -> Mapping[str, object]:
        writes.append(dict(parameters))
        if len(writes) == 1:
            raise MechanicsRejected(
                "Campaign scene field did not satisfy its declaration.",
                code="campaign_scene_invalid",
                field_paths=("summary",),
                state_changed=False,
            )
        return {"status": "narrative_state_committed", "resulting_revision": 5}

    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(narrative_tool,),
        control=source.control,
        narrative_writer=write,
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()),
        provider,
        owner_id="owner-provider",
    )

    events = tuple(adapter.for_turn("turn-provider")(provider_input))

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        "You finish securing the clearing."
    ]
    assert len(provider.calls) == 4
    assert writes == [
        {"summary": "invalid first correction", "operation": "update_scene"},
        {"summary": "The clearing is secured for night.", "operation": "update_scene"},
    ]
    assert "correction 1 of 2" in str(provider.calls[2][0])
    assert "correction 2 of 2" in str(provider.calls[3][0])


def _legacy_provider_cannot_narrate_after_abandoning_campaign_state_write() -> None:
    narrative_tool = ModelMechanicsTool(
        package_id="sylvia.core.campaign-state",
        package_version="1",
        package_sha256="c" * 64,
        name="update_campaign_scene",
        capability="campaign_state.update_scene",
        description="Record the current scene.",
        input_schema={
            "type": "object",
            "properties": {"summary": {"type": "string", "maxLength": 2000}},
            "required": ["summary"],
            "additionalProperties": False,
        },
        category="narrative_state",
        parameter_derivations={"operation": {"kind": "constant", "value": "update_scene"}},
    )
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content="A private draft.", finish_reason="stop")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-scene-missing",
                        "function": {
                            "name": "reconcile_campaign_state",
                            "arguments": '{"scenes":[{}]}',
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [ProviderStreamChunk(content="Uncommitted narration.", finish_reason="stop")],
        ]
    )
    source = _provider_input()
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(narrative_tool,),
        control=source.control,
        narrative_writer=lambda _parameters: (_ for _ in ()).throw(
            MechanicsRejected(
                "Campaign scene field did not satisfy its declaration.",
                code="campaign_scene_invalid",
                field_paths=("summary",),
                state_changed=False,
            )
        ),
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()),
        provider,
        owner_id="owner-provider",
    )

    with pytest.raises(CommandRejected) as captured:
        tuple(adapter.for_turn("turn-provider")(provider_input))

    assert captured.value.code == "campaign_state_write_abandoned"
    assert len(provider.calls) == 3


def test_authority_rejection_is_returned_without_correction_inference() -> None:
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-authority-rejected",
                        "function": {
                            "name": "advance",
                            "arguments": ('{"steps":2,"actor":"rift-player-character"}'),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [ProviderStreamChunk(content="I cannot resolve that safely.", finish_reason="stop")],
        ]
    )
    owner = _OwnerProjection()
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
    )
    stream = iter(adapter.for_turn("turn-provider")(_provider_input()))

    action = _next_action_event(stream)
    assert action.kind is ProviderEventKind.ACTION
    assert action.action_feedback is not None
    action.action_feedback.rejection = {
        "status": "rejected",
        "code": "mechanics_validation_failed",
        "reason": "The operation did not satisfy current rules state.",
        "validation": {"field_paths": ["steps"]},
        "state_changed": False,
        "instruction": "Correct the declared fields and retry this operation once.",
    }
    with pytest.raises(CommandRejected) as captured:
        tuple(stream)

    assert captured.value.code == "provider_tool_correction_abandoned"
    assert len(provider.calls) == 2
    assert owner.revision == 4


def test_mcp_rejection_preserves_safe_reason_and_fields_without_arbitrary_text() -> None:
    result = _safe_error(
        CommandRejected("PRIVATE CAMPAIGN DETAIL: Tool parameter 'actor_state' is invalid")
    )

    payload = result.structured_content
    assert payload == {
        "status": "rejected",
        "code": "mechanics_validation_failed",
        "reason": "The operation did not satisfy its declared parameters or current rules state.",
        "validation": {"field_paths": ["actor_state"]},
        "state_changed": False,
    }
    assert "PRIVATE CAMPAIGN DETAIL" not in str(result.content)


def test_safe_mechanics_diagnostic_is_available_without_full_audit(tmp_path: Path) -> None:
    owner = AuthorityOwner.open(tmp_path / "mechanics-diagnostic.db", "owner-diagnostic")
    try:
        owner.record_mechanics_diagnostic(
            session_id="session-diagnostic",
            turn_id="turn-diagnostic",
            action_id="action-diagnostic",
            tool_name="d20_test",
            rejection_code="mechanics_validation_failed",
            validation_field_paths=("actor_state",),
            state_changed=False,
        )

        projection = owner.admin_full_audit_projection()

        assert projection["enabled"] is False
        assert projection["traces"] == []
        assert projection["safe_mechanics_diagnostics"] == [
            {
                "kind": "mechanics_rejection",
                "privacy": "safe_metadata_only",
                "session_id": "session-diagnostic",
                "turn_id": "turn-diagnostic",
                "action_id": "action-diagnostic",
                "tool_name": "d20_test",
                "rejection_code": "mechanics_validation_failed",
                "validation_field_paths": ["actor_state"],
                "state_changed": False,
            }
        ]
    finally:
        owner.close()


def test_failed_mechanic_is_explicitly_rejected_without_replacement_narration() -> None:
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-failed",
                        "function": {
                            "name": "advance",
                            "arguments": ('{"steps":2,"actor":"rift-player-character"}'),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    content=(
                        "Daniel's quarterstaff strikes true for 4 damage, dropping the "
                        "Riftwolf to 5 HP."
                    ),
                    finish_reason="stop",
                )
            ],
        ]
    )
    owner = _OwnerProjection()
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
    )
    events = iter(adapter.for_turn("turn-provider")(_provider_input()))
    action = _next_action_event(events)
    assert action.kind is ProviderEventKind.ACTION
    assert action.action_command is not None
    owner.revision = 5
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={
                "status": "failed",
                "result": {"status": "failed", "code": "tool_execution_failed"},
            },
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=2,
            event_type=CanonicalEventType.ACTION_FAILED,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={"status": "failed", "code": "tool_execution_failed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )

    with pytest.raises(CommandRejected) as captured:
        tuple(events)

    assert captured.value.code == "provider_tool_correction_abandoned"
    assert len(provider.calls) == 2


def test_dm_prompt_injects_admitted_plan_state_without_mutable_plan_tool(tmp_path: Path) -> None:
    provider = _ScriptedProvider([])
    owner = _OwnerProjection()
    plan_path = tmp_path / "campaign-plan.md"
    plan_path.write_text(
        "# THE RIFT AWAKENING\n\nOpen at the Wayfarer's Rest as the Rift flashes.\n",
        encoding="utf-8",
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
        campaign_plan_text=plan_path.read_text(encoding="utf-8"),
    )

    provider_input = _provider_input()
    messages = adapter._messages(  # noqa: SLF001 - exact adapter boundary under test
        provider_input,
        campaign_has_mechanics=True,
        tools_available=True,
    )
    tools, _aliases = adapter._provider_tools(  # noqa: SLF001
        provider_input.catalog
    )
    system = str(messages[0]["content"])
    system_lower = system.lower()
    campaign_data = "\n".join(str(message["content"]) for message in messages[1:])
    assert system.startswith("You are Sylvia, the Game Master of a live tabletop")
    assert "campaign state is the gm-screen ledger" in system_lower
    assert "campaign plan supplies the main story's required outcomes" in system_lower
    assert "prepared events are not facts until played" in system_lower
    assert "player may refuse or abandon the story" in system_lower
    assert "immediate aftermath first" in system_lower
    assert "longer future before that choice" in system_lower
    assert "ruleset is the adjacent rulebook" in system_lower
    assert "tool availability is permission, not an instruction" in system_lower
    assert "player owns their character's voluntary actions" in system_lower
    assert "feelings, memories, history, and decisions" in system_lower
    assert "never restate their declaration as new first- or third-person narration" in system_lower
    assert "supply their next action or dialogue" in system_lower
    assert "read an exact source when a missing concrete fact" in system_lower
    assert "gm, player, and character knowledge" in system_lower
    assert "make the campaign file disposition your final tool action" in system_lower
    assert "narration alone is not durable memory" in system_lower
    assert "SYLVIA_GM_CAMPAIGN_PREPARATION" in campaign_data
    assert "SYLVIA_GM_FRESH_CAMPAIGN_STATE" in campaign_data
    assert "Wayfarer's Rest" in campaign_data
    assert "Mara Venn" in campaign_data
    assert "rift-scarred-hound" in campaign_data
    assert owner.revision == 4
    advertised = {str(tool["function"]["name"]) for tool in tools or []}
    assert len(advertised) == 1
    assert next(iter(advertised)).endswith("_advance")
    assert advertised.isdisjoint({"web_search", "read_file", "write_file", "delete_file"})
    mechanics = next(
        tool for tool in tools or [] if str(tool["function"]["name"]).endswith("_advance")
    )
    assert "advance" in str(mechanics["function"]["description"]).lower()
    assert str(mechanics["function"]["description"]) != "clock.advance"
    actor_schema = mechanics["function"]["parameters"]["properties"]["actor"]
    assert "exact participant_id" in actor_schema["description"].lower()
    state_schema = mechanics["function"]["parameters"]["properties"]["actor_state"]
    assert "never a status word" in state_schema["description"].lower()


def test_dm_cannot_rewrite_admitted_campaign_plan(tmp_path: Path) -> None:
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-reject-plan",
                        "function": {
                            "name": "write_campaign_plan",
                            "arguments": '{"content":""}',
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [ProviderStreamChunk(content="Plan unchanged.", finish_reason="stop")],
        ]
    )
    owner = _OwnerProjection()
    plan_path = tmp_path / "campaign-plan.md"
    plan_path.write_text("# Initial Plan\n", encoding="utf-8")
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
        campaign_plan_text=plan_path.read_text(encoding="utf-8"),
    )

    with pytest.raises(CommandRejected) as captured:
        tuple(adapter.for_turn("turn-provider")(_provider_input()))

    assert captured.value.code == "provider_tool_correction_abandoned"
    assert len(provider.calls) == 2
    assert plan_path.read_text(encoding="utf-8") == "# Initial Plan\n"
    assert owner.revision == 4


def test_no_rules_dm_prompt_forbids_invented_mechanics(tmp_path: Path) -> None:
    provider = _ScriptedProvider(
        [[ProviderStreamChunk(content="The rain continues.", finish_reason="stop")]]
    )
    owner = _OwnerProjection()
    plan_path = tmp_path / "campaign-plan.md"
    plan_path.write_text("# Quiet Road\n\nOpen beside a rain-soaked road.\n", encoding="utf-8")
    source = _provider_input()
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(),
        control=source.control,
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
        campaign_plan_text=plan_path.read_text(encoding="utf-8"),
    )

    tuple(adapter.for_turn("turn-provider")(provider_input))

    messages, tools = provider.calls[0]
    assert tools is None
    system = str(messages[0]["content"]).lower()
    assert "if no ruleset is bound" in system
    assert "do not invent mechanics or mechanical authority" in system


def test_dm_operating_contract_is_identical_for_every_live_tool_surface() -> None:
    ruleless = _dm_system_prompt(campaign_has_mechanics=False, tools_available=False)
    mechanics = _dm_system_prompt(campaign_has_mechanics=True, tools_available=True)
    temporarily_empty = _dm_system_prompt(campaign_has_mechanics=True, tools_available=False)

    assert ruleless == mechanics == temporarily_empty
    assert "Campaign State is the GM-screen ledger" in ruleless
    assert "freedom in how the player approaches them" in ruleless
    assert "Prepared events are not facts until played" in ruleless
    assert "player may refuse or abandon the story" in ruleless
    assert "An ongoing final act remains open" in ruleless
    assert "Campaign Setting and Character Files are reusable baselines" in ruleless
    assert "current player speech exactly names a roster character" in ruleless
    assert "Query another exact file when its concrete authored detail would change" in ruleless
    assert "do not invent missing pre-campaign biography or competence" in ruleless
    assert "Leave missing history unspecified" in ruleless
    assert "form new opinions and relationships through current play" in ruleless
    assert "engine updates Ruleset-owned Campaign State" in ruleless
    assert "not a second campaign memory" in ruleless
    assert "These boundaries govern authority, not style" in ruleless
    assert "Resolve only the player's exact declared action" in ruleless
    assert "adjudicate how long that declared action itself takes" in ruleless
    assert "if you specifically establish that it takes an hour, that hour passes" in ruleless
    assert (
        "Adjudicate ordinary execution details and inherent consequences within the declared "
        "activity; do not extend it into another voluntary activity." in ruleless
    )
    assert (
        "Finding water or shelter does not by itself authorize drinking, making camp, resting, "
        "or skipping the night." in ruleless
    )
    assert (
        "Carry out those steps only if the player already declared them and any stated condition "
        "is met." in ruleless
    )
    assert "carry that branch's declared continued activity" in ruleless
    assert "do not reopen a choice the player already made" in ruleless
    assert (
        "ordinary execution details inside that declared action as GM adjudication" not in ruleless
    )
    assert "after the declared activity resolves or meets a concrete interruption" in ruleless
    assert "it is not amnesia or a lack of memory to narrate" in ruleless
    assert "do not perform a conditional step unless its stated condition is met" in ruleless
    assert "Determine which stated branch applies from the established outcome" in ruleless
    assert "A broad glance reveals what is plainly visible" in ruleless
    assert "closer examination requires a closer action" in ruleless
    assert "Select details that matter instead of cataloguing the scene" in ruleless
    assert "The player owns their character's voluntary actions" in ruleless
    assert "exists in play only through established Campaign State" in ruleless
    assert "unplayed between-turn actions" in ruleless
    assert "prior experiences" in ruleless
    assert "Never restate their declaration as new first- or third-person narration" in ruleless
    assert "supply their next action or dialogue" in ruleless
    assert "subsequent activity" in ruleless
    assert (
        "Do not turn summarized Player Character speech into invented quoted dialogue" in ruleless
    )
    assert "let the player decide what their character suspects" in ruleless
    assert "You own the world, NPCs, GM-controlled companions, consequences" in ruleless
    assert "elapsed time required to resolve the declared action" in ruleless
    assert "their decisions do not establish the Player Character's agreement" in ruleless
    assert "Tool availability is permission, not an instruction" in ruleless
    assert "Use an exposed deterministic mechanic before deciding" in ruleless
    assert "make the Campaign File disposition your final tool action" in ruleless
    assert "narration alone is not durable memory" in ruleless
    assert "If no Ruleset is bound, do not invent mechanics" in ruleless
    assert "D&D" not in ruleless
    assert "private editorial" not in ruleless.casefold()
    assert "word limit" not in ruleless.casefold()
    assert "word budget" not in ruleless.casefold()


def test_player_wording_cannot_change_the_engine_supplied_tool_surface() -> None:
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content="The scene holds.", finish_reason="stop")],
            [ProviderStreamChunk(content="The scene holds.", finish_reason="stop")],
            [ProviderStreamChunk(content="The scene holds.", finish_reason="stop")],
            [ProviderStreamChunk(content="The scene holds.", finish_reason="stop")],
        ]
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()),
        provider,
        owner_id="owner-provider",
    )

    for text in (
        "I tell the bartender how I attacked the Riftwolf yesterday.",
        "I strike a match and light the lantern.",
        "I describe the quarterstaff I carried during the old fight.",
        "I strike the Riftwolf with my quarterstaff.",
    ):
        tuple(adapter.for_turn("turn-provider")(_provider_input(user_text=text)))

    surfaces = [tools for _messages, tools in provider.calls]
    assert all(surface == surfaces[0] for surface in surfaces)
    first_tools = surfaces[0]
    assert len(first_tools or []) == 1
    assert str((first_tools or [])[0]["function"]["name"]).endswith("_advance")
    assert all(kwargs.get("tool_choice") is None for kwargs in provider.request_kwargs)
    for (messages, _tools), expected_text in zip(
        provider.calls,
        (
            "I tell the bartender how I attacked the Riftwolf yesterday.",
            "I strike a match and light the lantern.",
            "I describe the quarterstaff I carried during the old fight.",
            "I strike the Riftwolf with my quarterstaff.",
        ),
        strict=True,
    ):
        assert messages[-1] == {"role": "user", "content": expected_text}
        assert "SYLVIA_GM_CURRENT_TURN" not in json.dumps(messages)
        action_control = str(messages[-2]["content"])
        assert action_control.startswith("SYLVIA_GM_CURRENT_PLAYER_MESSAGE")
        assert "next and final User message is the exact current player message" in action_control
        assert "conditional step" not in action_control
        assert "prepare_player_action_response" not in action_control
        system_contract = str(messages[0]["content"])
        assert "Follow the player's explicit conditions and sequence" in system_contract
        assert "do not extend it into another voluntary activity" in system_contract
        assert "Finding water or shelter does not by itself authorize drinking" in system_contract
        assert "only if the player already declared them" in system_contract
        assert "You may adjudicate how long that declared action itself takes" in system_contract
        assert "Use an exposed deterministic mechanic before deciding" in system_contract
        assert "Run the whole turn as one continuous conversation" in system_contract


def test_openai_inline_reasoning_filter_handles_split_harmony_channels() -> None:
    filterer = _InlineReasoningFilter()

    chunks = [
        filterer.filter_chunk(ProviderStreamChunk(content="<|chan")),
        filterer.filter_chunk(ProviderStreamChunk(content="nel|>thoughtPrivate plan.")),
        filterer.filter_chunk(ProviderStreamChunk(content="<|channel|>fi")),
        filterer.filter_chunk(
            ProviderStreamChunk(content="nal<|message|>The path opens.", finish_reason="stop")
        ),
    ]

    assert "".join(chunk.content for chunk in chunks) == "The path opens."
    assert "".join(chunk.reasoning_content for chunk in chunks) == "Private plan."
    assert all("channel" not in chunk.content for chunk in chunks)


def test_openai_inline_reasoning_filter_handles_split_minimax_think_tags() -> None:
    filterer = _InlineReasoningFilter()

    chunks = [
        filterer.filter_chunk(ProviderStreamChunk(content="<mm:thi")),
        filterer.filter_chunk(ProviderStreamChunk(content="nk>Private GM plan.")),
        filterer.filter_chunk(ProviderStreamChunk(content="</mm:thi")),
        filterer.filter_chunk(
            ProviderStreamChunk(content="nk>The figure steps from the smoke.", finish_reason="stop")
        ),
    ]

    assert "".join(chunk.content for chunk in chunks) == "The figure steps from the smoke."
    assert "".join(chunk.reasoning_content for chunk in chunks) == "Private GM plan."
    assert all("mm:think" not in chunk.content for chunk in chunks)


def test_failed_d20_narration_passes_through_without_host_semantic_adjudication() -> None:
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-athletics",
                        "function": {
                            "name": "d20_test",
                            "arguments": (
                                '{"actor":"rift-player-character",'
                                '"skill":"athletics","target_number":12}'
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    content=(
                        "The successful Athletics check (6 vs 12) drives the blow into "
                        "the Riftwolf's side."
                    ),
                    finish_reason="stop",
                )
            ],
            [
                ProviderStreamChunk(
                    content=(
                        "The Athletics check fails (6 vs 12). The Riftwolf holds its "
                        "ground. What do you do?"
                    ),
                    finish_reason="stop",
                )
            ],
        ]
    )
    owner = _OwnerProjection()
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
    )
    events = iter(
        adapter.for_turn("turn-provider")(
            _d20_provider_input(user_text="I disengage and run for help. We stop attacking.")
        )
    )

    action = _next_action_event(events)
    assert action.kind is ProviderEventKind.ACTION
    assert action.action_command is not None
    owner.revision = 5
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={
                "status": "committed",
                "result": {
                    "status": "committed",
                    "context": {
                        "_test_kind": "ability_check",
                        "_d20_test": {
                            "sides": 20,
                            "count": 1,
                            "results": [6],
                            "total": 6,
                            "modifier": 0,
                        },
                        "_adjusted_total": 6,
                        "_outcome": "failure",
                    },
                },
            },
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=2,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )

    talk = [event.text for event in events if event.kind is ProviderEventKind.TALK]

    assert talk == [
        "The successful Athletics check (6 vs 12) drives the blow into the Riftwolf's side."
    ]
    assert len(provider.calls) == 2


def test_false_turn_handoff_passes_through_without_host_semantic_adjudication() -> None:
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-dart",
                        "function": {
                            "name": "resolve_combat_attack",
                            "arguments": (
                                '{"actor":"rift-player-character","target":"rift-scarred-hound"}'
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    content=(
                        "The dart strikes the Riftwolf. The turn of the juvenile Riftwolf begins."
                    ),
                    finish_reason="stop",
                )
            ],
            [
                ProviderStreamChunk(
                    content=(
                        "The dart strikes the Riftwolf, leaving it at 6 hit points. "
                        "The current turn still belongs to Daniel. What does Daniel do?"
                    ),
                    finish_reason="stop",
                )
            ],
        ]
    )
    owner = _OwnerProjection(
        participants=(
            SimpleNamespace(
                participant_id="rift-player-character",
                kind="player_character",
                data={"name": "Daniel", "party_role": "player_character"},
            ),
        ),
        state={
            "sylvia.srd-lite.bg3.entities": {
                "rift-player-character": {
                    "name": "Daniel",
                    "hp": 10,
                    "armor_class": 14,
                },
                "rift-scarred-hound": {
                    "name": "Juvenile Riftwolf",
                    "hp": 12,
                    "armor_class": 13,
                },
            },
            "sylvia.srd-lite.bg3.combat": {
                "current_actor": "rift-player-character",
                "turn_active": True,
            },
        },
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
    )
    events = iter(
        adapter.for_turn("turn-provider")(
            _combat_attack_provider_input(user_text="I attack it with a dart.")
        )
    )

    action = _next_action_event(events)
    assert action.kind is ProviderEventKind.ACTION
    assert action.action_command is not None
    owner.revision = 5
    entities = owner.state["sylvia.srd-lite.bg3.entities"]
    assert isinstance(entities, dict)
    target = entities["rift-scarred-hound"]
    assert isinstance(target, dict)
    target["hp"] = 6
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={
                "status": "committed",
                "result": {
                    "status": "committed",
                    "context": {
                        "_attack_outcome": "hit",
                        "_target_id": "rift-scarred-hound",
                    },
                },
            },
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=2,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )

    talk = [event.text for event in events if event.kind is ProviderEventKind.TALK]

    assert talk == ["The dart strikes the Riftwolf. The turn of the juvenile Riftwolf begins."]
    assert len(provider.calls) == 2


def test_required_tool_call_discards_prose_before_campaign_file() -> None:
    draft = "Mara closes the gate and gives Daniel the iron key."
    update = {
        "facts": [
            {
                "fact_id": "mara-gave-iron-key",
                "category": "promise",
                "text": "Mara gave Daniel the iron gate key.",
                "visibility": "player_known",
                "known_by": ["rift-player-character", "mara"],
            }
        ]
    }
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content=draft, finish_reason="stop")],
            [
                ProviderStreamChunk(
                    content=("The draft is already complete; I am only saving its durable state.")
                ),
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-campaign-file",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **update,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": draft}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                ),
            ],
        ]
    )
    source = _provider_input(user_text="I accept Mara's offer.")
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 5}
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [update]
    assert [event.kind for event in events] == [
        ProviderEventKind.EVIDENCE,
        ProviderEventKind.EVIDENCE,
        ProviderEventKind.EVIDENCE,
        ProviderEventKind.TALK,
    ]
    assert events[0].evidence is not None
    assert events[0].evidence["kind"] == "provider_forced_tool_call_missing"
    assert events[1].evidence is not None
    assert events[1].evidence["kind"] == "provider_tool_calls"
    assert events[2].evidence is not None
    assert events[2].evidence["kind"] == "provider_tool_results"
    assert events[3].text == draft
    assert len(provider.calls) == 2
    assert _tool_operations(provider.calls[0][1] or []) == ["update_campaign_file"]
    assert _tool_operations(provider.calls[1][1] or []) == ["update_campaign_file"]
    assert provider.request_kwargs[0]["tool_choice"] == "required"
    disposition_schema = (provider.calls[1][1] or [])[0]["function"]["parameters"]
    assert "revised_player_facing_response" in disposition_schema["properties"]
    assert "revised_player_facing_response" in disposition_schema["required"]
    assert "player_action_preparation_id" not in disposition_schema["properties"]
    assert "Use only the declared top-level fields" in str(
        (provider.calls[1][1] or [])[0]["function"]["description"]
    )
    assert provider.request_kwargs[1]["tool_choice"] == "required"
    disposition_message = str(provider.calls[1][0][-1]["content"])
    assert disposition_message.startswith("SYLVIA_GM_REQUIRED_TOOL_CALL_CORRECTION")
    assert provider.calls[1][0][-1]["role"] == "system"
    assert "provider returned prose" in disposition_message
    assert "latest User message" in disposition_message
    assert "complete tool surface still exposed" in disposition_message
    assert draft not in str(provider.calls[1][0])
    assert "RELEASE CHECK" not in disposition_message
    assert "SCOPE AND INFORMATION ECONOMY CHECK" not in disposition_message
    assert "only saving its durable state" not in " ".join(
        event.text or "" for event in events if event.kind is ProviderEventKind.TALK
    )
    assert "reconciliation" not in str(provider.calls).casefold()


def _configure_native_m3(provider: _ScriptedProvider) -> None:
    provider.model_info = ModelInfo(
        id="opencode-go/minimax-m3",
        name="MiniMax M3",
        provider_name="OpenCode Go",
        provider_key="opencode_go",
        provider_mode="byok",
        transport="anthropic_messages",
        provider_model_id="minimax-m3",
    )


def test_native_m3_legacy_sole_tool_json_builds_matched_signed_continuation() -> None:
    source = _provider_input()
    aliases = {"advance": source.catalog[0]}
    encoded = '{"steps":2,"actor":"rift-player-character","actor_state":"rift-player-character"}'
    assembly = _forced_single_tool_call_from_json(encoded, aliases)
    assert assembly is not None
    thinking = {
        "type": "thinking",
        "thinking": "PRIVATE SYNTHETIC REASONING",
        "signature": "opaque-signature",
    }
    normalized = _native_content_for_text_operation(
        [thinking, {"type": "text", "text": encoded}],
        encoded,
        assembly,
    )
    assert normalized is not None

    request = AnthropicMessagesTransport().format_request(
        "minimax-m3",
        [
            {
                "role": "assistant",
                "content": None,
                "anthropic_content": normalized,
            },
            {
                "role": "tool",
                "tool_call_id": assembly.call_id,
                "content": '{"status":"committed"}',
            },
        ],
        None,
        True,
    )

    assert request["messages"][0]["content"][0] == thinking
    tool_use = request["messages"][0]["content"][1]
    tool_result = request["messages"][1]["content"][0]
    assert tool_use["type"] == "tool_use"
    assert tool_use["id"] == assembly.call_id
    assert tool_result["type"] == "tool_result"
    assert tool_result["tool_use_id"] == assembly.call_id


@pytest.mark.parametrize("saved_thinking", [False, True, "missing"])
def test_completed_turn_keeps_recorded_native_tool_history_for_the_next_turn(
    tmp_path: Path, saved_thinking: bool | str,
) -> None:
    prior_action = "I inspect the clearing edge."
    prior_response = "You find no recent tracks along the stones."
    opening_response = "You stand with Tamsin inside the quiet clearing."
    current_action = 'I ask Tamsin, "Where would you live?"'
    source = _provider_input(user_text=current_action)
    campaign_start = SessionMessage(
        session_id=source.session.session_id,
        sequence=1,
        message_id="campaign-start-basis",
        kind=SessionMessageKind.EVIDENCE,
        content={"kind": "campaign_start"},
    )
    opening_call = SessionMessage(
        session_id=source.session.session_id,
        sequence=2,
        message_id="opening-campaign-file-call",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_tool_calls",
            "turn_id": "campaign-turn",
            "inference_id": "opening-inference",
            "basis_message_id": campaign_start.message_id,
            "assistant": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "opening-call-id",
                        "type": "function",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": opening_response}
                                    ]
                                }
                            ),
                        },
                    }
                ],
            },
        },
    )
    opening_result = SessionMessage(
        session_id=source.session.session_id,
        sequence=3,
        message_id="opening-campaign-file-result",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_tool_results",
            "turn_id": "campaign-turn",
            "inference_id": "opening-inference",
            "basis_message_id": campaign_start.message_id,
            "results": [
                {
                    "role": "tool",
                    "tool_call_id": "opening-call-id",
                    "name": "update_campaign_file",
                    "content": json.dumps(
                        {
                            "status": "campaign_file_updated",
                            "player_facing_response": opening_response,
                        }
                    ),
                }
            ],
        },
    )
    prior_user = SessionMessage(
        session_id=source.session.session_id,
        sequence=1,
        message_id="prior-player-action",
        kind=SessionMessageKind.USER,
        content={"text": prior_action, "attempt_id": "prior-attempt"},
    )
    prior_call = SessionMessage(
        session_id=source.session.session_id,
        sequence=2,
        message_id="prior-campaign-file-call",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_tool_calls",
            "turn_id": "prior-turn",
            "inference_id": "prior-inference",
            "basis_message_id": prior_user.message_id,
            "assistant": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "prior-call-id",
                        "type": "function",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "facts": [
                                        {
                                            "fact_id": "no-tracks",
                                            "category": "consequence",
                                            "text": "No recent tracks were found.",
                                            "visibility": "player_known",
                                            "known_by": ["player"],
                                        }
                                    ],
                                    "revised_player_facing_response": prior_response,
                                }
                            ),
                        },
                    }
                ],
            },
        },
    )
    prior_result = SessionMessage(
        session_id=source.session.session_id,
        sequence=3,
        message_id="prior-campaign-file-result",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_tool_results",
            "turn_id": "prior-turn",
            "inference_id": "prior-inference",
            "basis_message_id": prior_user.message_id,
            "results": [
                {
                    "role": "tool",
                    "tool_call_id": "prior-call-id",
                    "name": "update_campaign_file",
                    "content": json.dumps(
                        {
                            "status": "campaign_file_updated",
                            "player_facing_response": prior_response,
                        }
                    ),
                }
            ],
        },
    )
    current_user = SessionMessage(
        session_id=source.session.session_id,
        sequence=4,
        message_id="current-player-action",
        kind=SessionMessageKind.USER,
        content={"text": current_action, "attempt_id": "current-attempt"},
    )
    malformed_call = replace(
        prior_call,
        message_id="malformed-historical-call",
        content={
            **prior_call.content,
            "inference_id": "malformed-historical-inference",
            "assistant": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "malformed-call-id",
                        "type": "function",
                        "function": {"name": "", "arguments": "not-json"},
                    }
                ],
            },
        },
    )
    malformed_result_item = {
        "role": "tool",
        "tool_call_id": "malformed-call-id",
        "name": "",
        "content": "{}",
    }
    malformed_result = replace(
        prior_result,
        message_id="malformed-historical-result",
        content={
            **prior_result.content,
            "inference_id": "malformed-historical-inference",
            "results": [malformed_result_item, malformed_result_item],
        },
    )
    history = SessionContext(
        session=source.session,
        messages=(
            campaign_start,
            opening_call,
            opening_result,
            prior_user,
            prior_call,
            prior_result,
            malformed_call,
            malformed_result,
            current_user,
        ),
    )
    database = tmp_path / "provider-history.db"
    campaign_id = cast(str, source.session.campaign_id)
    with AuthorityOwner.open(
        database,
        "owner-provider-history-write",
        disconnect_grace_seconds=30.0,
    ) as owner:
        owner.create_campaign(
            CampaignCreate(
                display_name="Provider history",
                writer=WriterRole.DM,
                package_lock=PackageLock(entries=()),
                turn_id="campaign-turn",
                campaign_id=campaign_id,
            )
        )
        owner.create_agent_session(
            campaign_id=campaign_id,
            role=WriterRole.DM,
            provider_id="opencode_go",
            model_id="opencode-go/minimax-m3",
            session_id=source.session.session_id,
        )
        for message in history.messages[:-1]:
            owner.append_agent_message(
                session_id=source.session.session_id,
                message_id=message.message_id,
                kind=message.kind,
                content=message.content,
            )
        owner.start_narration_attempt(
            campaign_id=campaign_id,
            attempt_id="opening-attempt",
            turn_id="campaign-turn",
            cancel=lambda: None,
        )
        owner.emit_narration_token(
            campaign_id,
            "opening-attempt",
            ProseChannel.TALK,
            opening_response,
        )
        owner.complete_narration_attempt(campaign_id, "opening-attempt")
        owner.start_narration_attempt(
            campaign_id=campaign_id,
            attempt_id="prior-attempt",
            turn_id="campaign-turn",
            cancel=lambda: None,
        )
        owner.emit_narration_token(
            campaign_id,
            "prior-attempt",
            ProseChannel.TALK,
            prior_response,
        )
        owner.complete_narration_attempt(campaign_id, "prior-attempt")
        owner.append_agent_message(
            session_id=source.session.session_id,
            message_id=current_user.message_id,
            kind=current_user.kind,
            content=current_user.content,
        )
    current_response = "Tamsin says she would choose a quiet town beside a river."
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "current-call-id",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": current_response}
                                    ]
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ]
        ]
    )
    with AuthorityOwner.open(database, "owner-provider-history-read") as reopened:
        # Materialize current owner schema even for the legacy no-private-record fixture.
        reopened.create_builder_chat(
            provider_id="test", model_id="test", session_id="schema-fixture",
        )
        reopened_history = reopened.session_context(source.session.session_id)
        if saved_thinking is True:
            from sylvia_gm.domain.provider_records import ProviderPrivateRecord
            native_blocks = [{"type": "thinking", "thinking": "Synthetic saved reasoning",
                              "signature": "synthetic-signed"}, {
                "type": "tool_use", "id": "prior-call-id", "name": "update_campaign_file",
                "input": json.loads(prior_call.content["assistant"]["tool_calls"][0]
                                    ["function"]["arguments"]),
            }]
            reopened.append_private_provider_record(ProviderPrivateRecord(
                source.session.session_id, "prior-private", "prior-inference", "prior-turn",
                prior_user.message_id, 0, reopened_history.session.provider_id,
                reopened_history.session.model_id, "continuation",
                json.dumps({"anthropic_content": native_blocks}),
            ))
            _configure_native_m3(provider)
        if saved_thinking == "missing":
            _configure_native_m3(provider)
            marked = tuple(
                replace(message, content={**message.content, "native_continuation": {
                    "provider_id": reopened_history.session.provider_id,
                    "model_id": reopened_history.session.model_id,
                    "anthropic_required": True,
                }}) if message.content.get("kind") == "provider_tool_calls" else message
                for message in reopened_history.messages
            )
            missing_input = SessionProviderInput(
                session=reopened_history.session,
                history=replace(reopened_history, messages=marked),
                catalog=source.catalog, control=source.control,
            )
            with pytest.raises(CommandRejected) as rejected:
                adapter = ProviderSessionAdapter(
                    reopened, provider, owner_id="owner-provider-history-read",
                )
                prepared = adapter._messages(missing_input)
                adapter._attach_native_continuation(prepared, missing_input)
            # A compacted request without the old exchange must not read its missing record.
            assert adapter._attach_native_continuation(
                [{"role": "user", "content": "Continue"}], missing_input,
            ) == [{"role": "user", "content": "Continue"}]
            assert rejected.value.code == "provider_native_continuation_missing"
            assert provider.calls == []
            return
        events = tuple(
            ProviderSessionAdapter(
                reopened,
                provider,
                owner_id="owner-provider-history-read",
            ).for_turn("current-turn")(
                SessionProviderInput(
                    session=reopened_history.session,
                    history=reopened_history,
                    catalog=(*source.catalog, _campaign_file_tool(allow_unchanged=True)),
                    control=source.control,
                    narrative_writer=lambda _parameters: {
                        "status": "campaign_file_unchanged",
                        "campaign_revision": 4,
                        "state_changed": False,
                    },
                )
            )
        )

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        current_response
    ]
    wire = AnthropicMessagesTransport().format_request(
        "minimax-m3", provider.calls[0][0], provider.calls[0][1], True
    )
    prior_assistant = next(
        message
        for message in wire["messages"]
        if message["role"] == "assistant"
        and any(block.get("id") == "prior-call-id" for block in message["content"])
    )
    prior_assistant_index = wire["messages"].index(prior_assistant)
    assert any(
        block.get("id") == "opening-call-id"
        for message in wire["messages"]
        if message["role"] == "assistant"
        for block in message["content"]
    )
    assert prior_assistant["content"] == ([{
        "type": "thinking", "thinking": "Synthetic saved reasoning",
        "signature": "synthetic-signed",
    }] if saved_thinking else []) + [
        {
            "type": "tool_use",
            "id": "prior-call-id",
            "name": "update_campaign_file",
            "input": {
                "facts": [
                    {
                        "fact_id": "no-tracks",
                        "category": "consequence",
                        "text": "No recent tracks were found.",
                        "visibility": "player_known",
                        "known_by": ["player"],
                    }
                ],
                "revised_player_facing_response": prior_response,
            },
        },
    ]
    assert wire["messages"][prior_assistant_index + 1]["content"][0] == {
        "type": "tool_result",
        "tool_use_id": "prior-call-id",
        "content": prior_result.content["results"][0]["content"],
    }
    assert (
        sum(
            block.get("text") == prior_response
            for message in wire["messages"]
            if message["role"] == "assistant"
            for block in message["content"]
        )
        == 0
    )
    assert wire["messages"][-1] == {"role": "user", "content": current_action}
    assert "malformed-call-id" not in json.dumps(wire["messages"])


def test_ignored_required_prose_keeps_every_legal_tool_choice() -> None:
    previous_action = "I wait at the edge of the clearing."
    discarded = "Nothing beyond the obvious clearing calls for a closer check yet."
    current_action = "I look around."
    final_response = "The clearing is quiet, with two plainly visible paths leading away."
    reference_tool = ModelMechanicsTool(
        package_id="sylvia.application",
        package_version="1",
        package_sha256="0" * 64,
        name="read_campaign_reference",
        capability="context.read",
        description="Read one admitted campaign reference.",
        input_schema={
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["source", "path"],
            "additionalProperties": False,
        },
        category="reference",
    )
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content=discarded, finish_reason="stop")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-file-no-mechanic-needed",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": final_response}
                                    ]
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    _configure_native_m3(provider)
    source = _provider_input(user_text=current_action)
    history = SessionContext(
        session=source.session,
        messages=(
            SessionMessage(
                session_id=source.session.session_id,
                sequence=1,
                message_id="previous-action",
                kind=SessionMessageKind.USER,
                content={"text": previous_action},
            ),
            SessionMessage(
                session_id=source.session.session_id,
                sequence=2,
                message_id="previous-response",
                kind=SessionMessageKind.TALK,
                content={"text": discarded},
            ),
            SessionMessage(
                session_id=source.session.session_id,
                sequence=3,
                message_id="current-action",
                kind=SessionMessageKind.USER,
                content={"text": current_action},
            ),
        ),
    )
    provider_input = SessionProviderInput(
        session=source.session,
        history=history,
        catalog=(reference_tool, *source.catalog, _campaign_file_tool(allow_unchanged=True)),
        control=source.control,
        narrative_writer=lambda _parameters: {
            "status": "campaign_file_unchanged",
            "campaign_revision": 4,
            "state_changed": False,
        },
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    surfaces = [_tool_operations(tools or []) for _messages, tools in provider.calls]
    assert surfaces == [
        [
            "read_campaign_reference",
            "advance",
            "update_campaign_file",
        ],
        [
            "read_campaign_reference",
            "advance",
            "update_campaign_file",
        ],
    ]
    assert any(
        str(message.get("content", "")).startswith("SYLVIA_GM_REQUIRED_TOOL_CALL_CORRECTION")
        for message in provider.calls[1][0]
    )
    assert provider.calls[1][0].count({"role": "assistant", "content": discarded}) == 1
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        final_response
    ]


def test_required_tool_recovery_keeps_the_current_action_as_the_latest_user_message() -> None:
    previous_action = "I inspect the clearing for tracks."
    previous_response = "You find several overlapping boot prints beside the western stones."
    current_action = 'I ask Tamsin, "Where would you choose to live?"'
    current_response = "Tamsin says she would choose a small town beside moving water."
    source = _provider_input(user_text=current_action)
    history = SessionContext(
        session=source.session,
        messages=(
            SessionMessage(
                session_id=source.session.session_id,
                sequence=1,
                message_id="previous-action",
                kind=SessionMessageKind.USER,
                content={"text": previous_action},
            ),
            SessionMessage(
                session_id=source.session.session_id,
                sequence=2,
                message_id="previous-response",
                kind=SessionMessageKind.TALK,
                content={"text": previous_response},
            ),
            SessionMessage(
                session_id=source.session.session_id,
                sequence=3,
                message_id="current-action",
                kind=SessionMessageKind.USER,
                content={"text": current_action},
            ),
        ),
    )

    class CurrentActionSensitiveProvider(_ScriptedProvider):
        def __init__(self) -> None:
            super().__init__([])

        async def generate_stream(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            timeout: float = 120.0,
            **kwargs: object,
        ) -> AsyncIterator[ProviderStreamChunk]:
            del timeout
            self.calls.append((messages.copy(), tools))
            self.request_kwargs.append(dict(kwargs))
            if len(self.calls) == 1:
                yield ProviderStreamChunk(content=current_response, finish_reason="stop")
                return
            latest_user = next(
                str(message.get("content", ""))
                for message in reversed(messages)
                if message.get("role") == "user"
            )
            if latest_user != current_action and len(self.calls) == 2:
                yield ProviderStreamChunk(
                    content="I will choose the required operation now.",
                    finish_reason="stop",
                )
                return
            selected_response = (
                current_response if latest_user == current_action else previous_response
            )
            yield ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "call-campaign-file",
                    "function": {
                        "name": "update_campaign_file",
                        "arguments": json.dumps(
                            {
                                "revised_player_facing_response": [
                                    {"kind": "narration", "text": selected_response}
                                ]
                            }
                        ),
                    },
                },
                finish_reason="tool_calls",
            )

    provider = CurrentActionSensitiveProvider()
    provider_input = SessionProviderInput(
        session=source.session,
        history=history,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=lambda _parameters: {
            "status": "campaign_file_unchanged",
            "campaign_revision": 4,
            "state_changed": False,
        },
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        current_response
    ]
    assert len(provider.calls) == 2
    recovery_messages = provider.calls[1][0]
    assert [message for message in recovery_messages if message.get("role") == "user"][-1] == {
        "role": "user",
        "content": current_action,
    }
    corrections = [
        message
        for message in recovery_messages
        if str(message.get("content", "")).startswith("SYLVIA_GM_REQUIRED_TOOL_CALL_CORRECTION")
    ]
    assert len(corrections) == 1
    assert corrections[0]["role"] == "system"


def test_campaign_file_requires_complete_response_after_discarded_prose() -> None:
    draft = "Rain ticks softly against the shutter."
    update = {
        "scenes": [
            {
                "location_id": "roadside-inn",
                "summary": "Rain is falling outside the roadside inn.",
            }
        ]
    }
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content=draft, finish_reason="stop")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-missing-checked-response",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(update),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-complete-disposition",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **update,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": draft}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _provider_input(user_text="I listen to the rain.")
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 5}
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [update]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [draft]
    assert len(provider.calls) == 3
    schema = (provider.calls[2][1] or [])[0]["function"]["parameters"]
    assert "revised_player_facing_response" in schema["properties"]
    assert "revised_player_facing_response" in schema["required"]
    assert "missing_required_arguments" in str(provider.calls[2][0])


def test_same_gm_can_attach_final_narration_to_campaign_file_update() -> None:
    draft = "Mara closes the gate and gives Daniel the iron key."
    update = {
        "facts": [
            {
                "fact_id": "mara-gave-iron-key",
                "category": "promise",
                "text": "Mara gave Daniel the iron gate key.",
                "visibility": "player_known",
                "known_by": ["rift-player-character", "mara"],
            }
        ]
    }
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(content=draft),
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-campaign-file-with-narration",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **update,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": draft}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                ),
            ]
        ]
    )
    source = _provider_input(user_text="I accept Mara's offer.")
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 5}
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [update]
    assert [event.kind for event in events] == [
        ProviderEventKind.EVIDENCE,
        ProviderEventKind.EVIDENCE,
        ProviderEventKind.TALK,
    ]
    assert events[0].evidence is not None
    assert events[0].evidence["kind"] == "provider_tool_calls"
    assistant = events[0].evidence["assistant"]
    assert assistant["content"] == draft
    assert events[1].evidence is not None
    assert events[1].evidence["kind"] == "provider_tool_results"
    assert events[2].text == draft
    assert len(provider.calls) == 1
    assert _tool_operations(provider.calls[0][1] or []) == ["update_campaign_file"]
    assert provider.request_kwargs[0]["tool_choice"] == "required"


def test_same_gm_updates_campaign_file_then_narrates_the_admitted_outcome() -> None:
    narration = "Mara closes the gate and gives Daniel the iron key."
    update = {
        "facts": [
            {
                "fact_id": "mara-gave-iron-key",
                "category": "promise",
                "text": "Mara gave Daniel the iron gate key.",
                "visibility": "player_known",
                "known_by": ["rift-player-character", "mara"],
            }
        ]
    }
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-campaign-file-before-narration",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **update,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": narration}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _provider_input(user_text="I accept Mara's offer.")
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 5}
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [update]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [narration]
    assert len(provider.calls) == 1
    assert _tool_operations(provider.calls[0][1] or []) == ["update_campaign_file"]
    schema = (provider.calls[0][1] or [])[0]["function"]["parameters"]
    assert "revised_player_facing_response" in schema["properties"]
    assert "revised_player_facing_response" in schema["required"]
    assert "player_action_preparation_id" not in schema["properties"]
    assert provider.request_kwargs[0]["tool_choice"] == "required"
    assert not any(
        event.evidence is not None and event.evidence.get("kind") == "provider_held_narration"
        for event in events
    )


def test_campaign_start_can_settle_unchanged_when_construction_established_the_scene() -> None:
    narration = "You stand in the established clearing. What do you do?"
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-opening-unchanged",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": narration}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ]
        ]
    )
    source = _provider_input()
    start_message = SessionMessage(
        session_id=source.session.session_id,
        sequence=1,
        message_id="message-campaign-start-unchanged",
        kind=SessionMessageKind.EVIDENCE,
        content={"kind": "campaign_start", "event": "campaign_start"},
    )
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(session=source.session, messages=(start_message,)),
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=ProviderControl(),
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {
                "status": "campaign_file_unchanged",
                "campaign_revision": 4,
                "state_changed": False,
            }
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [{}]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [narration]
    assert len(provider.calls) == 1


@pytest.mark.parametrize(
    "rejection_code", ["campaign_file_update_invalid", "campaign_file_save_failed"]
)
def test_tool_only_campaign_file_rejection_stays_in_a_bounded_update_loop(
    rejection_code: str,
) -> None:
    narration = "Mara closes the gate and gives Daniel the iron key."
    invalid = {"facts": [{}]}
    corrected = {
        "facts": [
            {
                "fact_id": "mara-gave-iron-key",
                "category": "promise",
                "text": "Mara gave Daniel the iron gate key.",
                "visibility": "player_known",
                "known_by": ["rift-player-character", "mara"],
            }
        ]
    }
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-invalid-tool-only-update",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **invalid,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": narration}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-corrected-tool-only-update",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **corrected,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": narration}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _provider_input(user_text="I accept Mara's offer.")
    attempts: list[dict[str, object]] = []
    events: list[ProviderEvent] = []

    def write(parameters: Mapping[str, object]) -> Mapping[str, object]:
        # Neither the initial draft nor the rejected write may announce a completed transfer.
        assert not any(event.kind is ProviderEventKind.TALK for event in events)
        attempts.append(dict(parameters))
        if len(attempts) == 1:
            raise MechanicsRejected(
                "Campaign File update was not saved.",
                code=rejection_code,
                field_paths=("facts[0].fact_id",),
                state_changed=False,
            )
        return {"status": "campaign_file_updated", "resulting_revision": 5}

    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=write,
    )

    for event in ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()),
        provider,
        owner_id="owner-provider",
    ).for_turn("turn-provider")(provider_input):
        events.append(event)

    assert attempts == [invalid, corrected]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [narration]
    assert len(provider.calls) == 2
    assert all(
        _tool_operations(provider.calls[index][1] or []) == ["update_campaign_file"]
        for index in (0, 1)
    )
    assert provider.request_kwargs[1]["tool_choice"] == "required"
    assert "SYLVIA_GM_TOOL_CORRECTION" in str(provider.calls[1][0])
    assert rejection_code in str(provider.calls[1][0])
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [narration]


@pytest.mark.parametrize(
    "invalid_arguments",
    (
        {},
        {"revised_player_facing_response": ""},
        {"revised_player_facing_response": None},
        {"revised_player_facing_response": 3},
    ),
)
def test_rejected_response_keeps_legal_tools_when_no_narration_is_held(
    invalid_arguments: dict[str, object],
) -> None:
    narration = "The clearing remains quiet."
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": call_id,
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(arguments),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ]
            for call_id, arguments in (
                ("invalid-response", invalid_arguments),
                (
                    "corrected",
                    {"revised_player_facing_response": [{"kind": "narration", "text": narration}]},
                ),
            )
        ]
    )
    source = _provider_input(user_text="I listen quietly.")
    writes: list[dict[str, object]] = []
    provider_input = replace(
        source,
        catalog=(*source.catalog, _campaign_file_tool(allow_unchanged=True)),
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_unchanged", "campaign_revision": 4, "state_changed": False}
        ),
    )
    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()), provider, owner_id="owner-provider"
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [{}]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [narration]
    retry_messages, retry_tools = provider.calls[1]
    rejection = (
        "campaign_file_revised_narration_invalid"
        if invalid_arguments
        else "missing_required_arguments"
    )
    assert rejection in str(retry_messages)
    assert _tool_operations(retry_tools or []) == ["advance", "update_campaign_file"]
    assert provider.request_kwargs[1]["tool_choice"] == "required"
    assert not any(
        str(message.get("content", "")).startswith("SYLVIA_GM_CAMPAIGN_FILE_DISPOSITION")
        for message in retry_messages
    )
    assert (
        "revised_player_facing_response"
        in (retry_tools or [])[-1]["function"]["parameters"]["required"]
    )


def test_attached_narration_empty_collection_is_corrected_before_release() -> None:
    draft = "The scout's knife-mark points east toward the rocky rise."
    corrected_update = {
        "scenes": [
            {
                "location_id": "eastern-tree-line",
                "summary": "A scout trail and knife-mark point east toward the rocky rise.",
            }
        ]
    }
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(content=draft),
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-empty-collection",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "entities": [],
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": draft}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-corrected-campaign-file",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **corrected_update,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": draft}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _provider_input(user_text="I circle east and look for signs of passage.")
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 5}
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [corrected_update]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [draft]
    first_result = next(
        json.loads(str(event.evidence["results"][0]["content"]))
        for event in events
        if event.evidence is not None and event.evidence.get("kind") == "provider_tool_results"
    )
    assert first_result["code"] == "campaign_file_empty_collections"
    assert first_result["field_paths"] == ["entities"]
    assert "SYLVIA_GM_TOOL_CORRECTION" in str(provider.calls[1][0])
    assert provider.request_kwargs[1]["tool_choice"] == "required"


def test_campaign_file_update_cannot_precede_a_batched_ruleset_result() -> None:
    draft = "You reach the eastern trees and spot a knife-mark on an alder."
    update = {
        "scenes": [
            {
                "location_id": "eastern-tree-line",
                "summary": "The adventurer is at an alder marked with a scout's knife-sign.",
            }
        ]
    }
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(content=draft),
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-premature-campaign-file",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **update,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": draft}
                                    ],
                                }
                            ),
                        },
                    }
                ),
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 1,
                        "id": "call-required-ruleset-result",
                        "function": {
                            "name": "advance",
                            "arguments": (
                                '{"steps":1,"actor":"rift-player-character",'
                                '"actor_state":"rift-player-character"}'
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                ),
            ],
            [
                ProviderStreamChunk(content=draft),
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-final-campaign-file",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **update,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": draft}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                ),
            ],
        ]
    )
    source = _provider_input(user_text="I circle east and look for signs of passage.")
    owner = _OwnerProjection()
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(*source.catalog, _campaign_file_tool(allow_unchanged=True)),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 6}
        ),
    )
    events = iter(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    action = _next_action_event(events)
    assert action.action_command is not None
    owner.revision = 5
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={"result": {"status": "advanced", "steps": 1}},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=2,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )

    remaining = tuple(events)

    assert writes == [update]
    assert [event.text for event in remaining if event.kind is ProviderEventKind.TALK] == [draft]
    first_result = next(
        json.loads(str(result["content"]))
        for event in remaining
        if event.evidence is not None and event.evidence.get("kind") == "provider_tool_results"
        for result in event.evidence["results"]
        if result["tool_call_id"] == "call-premature-campaign-file"
    )
    assert first_result["code"] == "campaign_file_update_must_be_only_tool"
    facts = [
        str(message["content"])
        for message in provider.calls[1][0]
        if str(message.get("content", "")).startswith("SYLVIA_GM_EXACT_NARRATION_FACTS")
    ]
    assert len(facts) == 1
    assert facts[0].count('"tool":"advance"') == 1
    assert '"tool":"update_campaign_file"' not in facts[0]
    assert any(
        tool["function"]["name"].endswith("_update_campaign_file")
        for tool in provider.calls[0][1] or []
    )
    assert "SYLVIA_GM_TOOL_CORRECTION" in str(provider.calls[1][0])


def test_duplicate_campaign_file_collections_require_response_and_state_reconciliation() -> None:
    duplicate = {
        "facts": [
            {
                "fact_id": "current-road",
                "category": "continuity",
                "text": "The road is already known to be empty.",
                "visibility": "player_known",
                "known_by": ["player"],
            }
        ]
    }
    concise = "The empty road remains quiet."
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-duplicate-file-collection",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **duplicate,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": concise}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-reconciled-unchanged-file",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": concise}
                                    ]
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ],
    )
    source = _provider_input(user_text="I inspect the empty road for anything new.")
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {
                "status": "campaign_file_unchanged",
                "campaign_revision": 4,
                "state_changed": False,
            }
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [duplicate, {}]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [concise]
    assert len(provider.calls) == 2
    correction = str(provider.calls[1][0])
    assert "campaign_file_duplicate_collections" in correction
    assert "remove unsupported new material" in correction
    assert provider.request_kwargs[1]["tool_choice"] == "required"


def test_broad_glance_uses_the_ruleset_surface_without_a_host_editorial_inference() -> None:
    recent_narration = (
        "The cold fire pit and torn supply cache sit beneath a restless canopy while Chloe "
        "watches the tree line."
    )
    verbose = (
        "North, smoke rises beyond the trees. East, something heavy moves beside a stream. "
        "Behind you, the path you arrived by is closing under fresh growth. At your feet, "
        "bootprints lead toward an open supply cache containing rations, cord, tools, two water "
        "skins, and a folded map. The tracks have two distinct treads, one standard issue and one "
        "marked with a dotted heel, and the second traveler appears to favor their right foot."
    )
    concise = (
        "Nothing else immediately stands out in the clearing. A faint draft moves across the "
        "Warden stone without stirring the canopy, and Chloe tilts her head to listen."
    )

    def campaign_file_call(call_id: str, narration: str) -> list[ProviderStreamChunk]:
        return [
            ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": call_id,
                    "function": {
                        "name": "update_campaign_file",
                        "arguments": json.dumps(
                            {
                                "revised_player_facing_response": [
                                    {"kind": "narration", "text": narration}
                                ]
                            }
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
        ]

    provider = _ScriptedProvider(
        [
            campaign_file_call("call-overbroad-glance", verbose),
            [ProviderStreamChunk(content=concise, finish_reason="stop")],
            campaign_file_call("call-edited-glance", concise),
        ]
    )
    source = _provider_input(user_text="I look around.")
    owner = _OwnerProjection()
    owner.events = (
        CanonicalEvent(
            sequence=7,
            event_type=CanonicalEventType.TALK,
            turn_id="turn-opening",
            action_id=None,
            safe_payload={"text": recent_narration},
            visibility=EventVisibility.PLAYER_SAFE,
            attempt_id="attempt-opening",
        ),
        CanonicalEvent(
            sequence=8,
            event_type=CanonicalEventType.TALK,
            turn_id="turn-private-note",
            action_id=None,
            safe_payload={"text": "INTERNAL NOTE MUST NOT REACH THE PROVIDER"},
            visibility=EventVisibility.INTERNAL_SAFE,
            attempt_id="attempt-private-note",
        ),
    )
    current_user = SessionMessage(
        session_id=source.session.session_id,
        sequence=1,
        message_id=source.history.messages[0].message_id,
        kind=SessionMessageKind.USER,
        content=source.history.messages[0].content,
    )
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(
            session=source.session,
            messages=(current_user,),
        ),
        catalog=(*source.catalog, _campaign_file_tool(allow_unchanged=True)),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {
                "status": "campaign_file_unchanged",
                "campaign_revision": 4,
                "state_changed": False,
            }
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [{}]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [verbose]
    assert len(provider.calls) == 1
    normal_context = str(provider.calls[0][0])
    assert {"role": "assistant", "content": recent_narration} in provider.calls[0][0]
    assert "SYLVIA_GM_RECENT_PLAYER_FACING_NARRATION" not in normal_context
    assert "SYLVIA_GM_CURRENT_TURN" not in normal_context
    assert normal_context.count("SYLVIA_GM_CURRENT_PLAYER_MESSAGE") == 1
    assert {"role": "user", "content": "I look around."} in provider.calls[0][0]
    assert str(provider.calls[0][0][-2]["content"]).startswith("SYLVIA_GM_CURRENT_PLAYER_MESSAGE")
    assert recent_narration in normal_context
    assert "INTERNAL NOTE MUST NOT REACH THE PROVIDER" not in normal_context
    assert "SYLVIA_GM_PRIVATE_EDITORIAL_REVISION" not in normal_context
    assert _tool_operations(provider.calls[0][1] or []) == [
        "advance",
        "update_campaign_file",
    ]
    assert provider.request_kwargs[0]["tool_choice"] == "required"


def test_broad_glance_campaign_file_response_is_not_rewritten_by_the_host() -> None:
    verbose = "Every path, hidden track, and distant sound competes for Daniel's attention."
    concise = "Daniel and Chloe stand within the quiet stone clearing as the wet canopy stirs."

    def campaign_file_call(call_id: str, narration: str) -> list[ProviderStreamChunk]:
        return [
            ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": call_id,
                    "function": {
                        "name": "update_campaign_file",
                        "arguments": json.dumps(
                            {
                                "revised_player_facing_response": [
                                    {"kind": "narration", "text": narration}
                                ]
                            }
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
        ]

    serialized = (
        concise + ']<]minimax[>[<tool_call>\n]<]minimax[>[<invoke name="update_campaign_file">]'
    )
    provider = _ScriptedProvider(
        [
            campaign_file_call("call-overbroad-glance", verbose),
            [ProviderStreamChunk(content=serialized, finish_reason="stop")],
            [ProviderStreamChunk(content=concise, finish_reason="stop")],
            campaign_file_call("call-edited-glance", concise),
        ]
    )
    source = _provider_input(user_text="I look around.")
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(*source.catalog, _campaign_file_tool(allow_unchanged=True)),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {
                "status": "campaign_file_unchanged",
                "campaign_revision": 4,
                "state_changed": False,
            }
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection(participants=())),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [{}]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [verbose]
    assert len(provider.calls) == 1
    assert "SYLVIA_GM_PRIVATE_EDITORIAL_REVISION" not in str(provider.calls[0][0])
    assert serialized not in str(provider.calls[0][0])


def test_broad_glance_has_no_hidden_second_inference_or_host_word_limit() -> None:
    verbose = "Every visible feature competes for Daniel's attention in exhaustive detail."
    reviewed = (
        "The clearing is bounded by wet ferns and old stone. Near the tree line, one low branch "
        "moves against the wind while Chloe watches the open ground. Nothing requires a close "
        "inspection yet, and the scene remains yours to approach."
    )

    def campaign_file_call(call_id: str, narration: str) -> list[ProviderStreamChunk]:
        return [
            ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": call_id,
                    "function": {
                        "name": "update_campaign_file",
                        "arguments": json.dumps(
                            {
                                "revised_player_facing_response": [
                                    {"kind": "narration", "text": narration}
                                ]
                            }
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
        ]

    provider = _ScriptedProvider(
        [
            campaign_file_call("call-overbroad-glance", verbose),
            [ProviderStreamChunk(content=reviewed, finish_reason="stop")],
            campaign_file_call("call-edited-glance", reviewed),
        ]
    )
    source = _provider_input(user_text="I look around.")
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(*source.catalog, _campaign_file_tool(allow_unchanged=True)),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {
                "status": "campaign_file_unchanged",
                "campaign_revision": 4,
                "state_changed": False,
            }
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [{}]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [verbose]
    assert len(provider.calls) == 1
    request_text = str(provider.calls[0][0])
    assert "PRIVATE_EDITORIAL" not in request_text
    assert "80 words" not in request_text
    assert reviewed not in request_text


def test_campaign_file_commit_may_return_the_same_gm_response_without_host_judgment() -> None:
    verbose = "The entire forest is described before Daniel can choose where to look."
    reviewed = "Daniel sees one weathered stone and the ferns moving beyond it."
    rewritten = reviewed + " Chloe says they have been travelling all morning."

    def campaign_file_call(call_id: str, narration: str) -> list[ProviderStreamChunk]:
        return [
            ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": call_id,
                    "function": {
                        "name": "update_campaign_file",
                        "arguments": json.dumps(
                            {
                                "revised_player_facing_response": [
                                    {"kind": "narration", "text": narration}
                                ]
                            }
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
        ]

    provider = _ScriptedProvider(
        [
            campaign_file_call("call-overbroad-glance", verbose),
            [ProviderStreamChunk(content=reviewed, finish_reason="stop")],
            campaign_file_call("call-rewritten-glance", rewritten),
            campaign_file_call("call-exact-glance", reviewed),
        ]
    )
    source = _provider_input(user_text="I look around.")
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(*source.catalog, _campaign_file_tool(allow_unchanged=True)),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {
                "status": "campaign_file_unchanged",
                "campaign_revision": 4,
                "state_changed": False,
            }
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [{}]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [verbose]
    assert len(provider.calls) == 1
    assert "campaign_file_editorial_revision_mismatch" not in str(provider.calls)
    assert reviewed not in str(provider.calls[0][0])
    assert rewritten not in str(provider.calls[0][0])


def test_same_gm_searches_reads_updates_and_narrates_in_one_tool_loop() -> None:
    search_tool = ModelMechanicsTool(
        package_id="sylvia.application",
        package_version="1",
        package_sha256="0" * 64,
        name="search_campaign_knowledge",
        capability="context.search",
        description="Discover exact Campaign File references.",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "sources": {"type": "array", "items": {"type": "string"}},
                "limit": {"type": "integer"},
            },
            "required": ["query"],
            "additionalProperties": False,
        },
        category="reference",
    )
    reference_tool = ModelMechanicsTool(
        package_id="sylvia.application",
        package_version="1",
        package_sha256="0" * 64,
        name="read_campaign_reference",
        capability="context.read",
        description="Read one Campaign File path.",
        input_schema={
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["source", "path"],
            "additionalProperties": False,
        },
        category="reference",
    )
    update = {
        "entities": [
            {
                "entity_id": "alrya",
                "name": "Alrya",
                "attributes": {"location": "capital-hospital", "condition": "confined"},
            }
        ]
    }
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-search-alrya",
                        "function": {
                            "name": "search_campaign_knowledge",
                            "arguments": json.dumps(
                                {
                                    "query": "Where is Alrya now?",
                                    "sources": ["campaign_state"],
                                    "limit": 3,
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-query-alrya",
                        "function": {
                            "name": "read_campaign_reference",
                            "arguments": '{"source":"campaign_state","path":"entities.alrya"}',
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-update-alrya",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **update,
                                    "revised_player_facing_response": [
                                        {
                                            "kind": "narration",
                                            "text": (
                                                "A courier confirms Alrya remains confined to the "
                                                "capital hospital."
                                            ),
                                        }
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _provider_input(user_text="I ask where Alrya is now.")
    searches: list[tuple[str, tuple[str, ...] | None, int]] = []
    reads: list[tuple[str, str]] = []
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(search_tool, reference_tool, _campaign_file_tool(allow_unchanged=True)),
        control=source.control,
        knowledge_searcher=lambda query, sources, limit: (
            searches.append((query, tuple(sources) if sources is not None else None, limit))
            or {
                "results": [
                    {
                        "source": "campaign_state",
                        "path": "entities.alrya",
                        "provenance": "Campaign State",
                    }
                ]
            }
        ),
        reference_reader=lambda source_name, path: (
            reads.append((source_name, path))
            or {"entity_id": "alrya", "location": "capital-hospital"}
        ),
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 5}
        ),
    )

    owner = _OwnerProjection(participants=())
    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert searches == [("Where is Alrya now?", ("campaign_state",), 3)]
    assert reads == [("campaign_state", "entities.alrya")]
    assert writes == [update]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        "A courier confirms Alrya remains confined to the capital hospital."
    ]
    assert len(provider.calls) == 3
    assert provider.calls[0][0][0] == provider.calls[2][0][0]
    assert [message["role"] for message in provider.calls[2][0] if message["role"] != "system"] == [
        "user",
        "assistant",
        "tool",
        "assistant",
        "tool",
    ]
    assert all(kwargs["tool_choice"] == "required" for kwargs in provider.request_kwargs)
    assert not any(
        event.evidence is not None and event.evidence.get("kind") == "provider_held_narration"
        for event in events
    )
    assert "reconciliation" not in str(provider.calls).casefold()


def test_rejected_optional_reference_may_be_abandoned_before_narration_checkpoint() -> None:
    reference_tool = ModelMechanicsTool(
        package_id="sylvia.application",
        package_version="1",
        package_sha256="0" * 64,
        name="read_campaign_reference",
        capability="context.read",
        description="READ-ONLY Campaign File reference.",
        input_schema={
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["source", "path"],
            "additionalProperties": False,
        },
        category="reference",
    )
    draft = "You find a narrow deer-run beneath the ferns, away from the breathing."
    update = {
        "scenes": [
            {
                "location_id": "verdant-margin-deer-run",
                "summary": "The adventurer is following a quiet deer-run beneath the ferns.",
            }
        ]
    }
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-invalid-reference-write",
                        "function": {
                            "name": "read_campaign_reference",
                            "arguments": '{"source":"campaign_state","path":"update"}',
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [ProviderStreamChunk(content=draft, finish_reason="stop")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-update-after-abandoned-reference",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **update,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": draft}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _provider_input(user_text="I skirt the clearing's edge and look for a quieter route.")
    reads: list[tuple[str, str]] = []
    writes: list[dict[str, object]] = []

    def reject_reference(source_name: str, path: str) -> Mapping[str, object]:
        reads.append((source_name, path))
        raise CommandRejected("Campaign State reference is unavailable")

    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(reference_tool, _campaign_file_tool(allow_unchanged=True)),
        control=source.control,
        reference_reader=reject_reference,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 5}
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection(participants=())),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert reads == [("campaign_state", "update")]
    assert writes == [update]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [draft]
    assert len(provider.calls) == 3
    second_messages = str(provider.calls[1][0])
    assert "SYLVIA_GM_OPTIONAL_REFERENCE_RECOVERY" in second_messages
    assert '"correction_optional":true' in second_messages
    assert "read-only reference lookup" in second_messages
    assert "SYLVIA_GM_TOOL_CORRECTION" not in second_messages
    assert provider.request_kwargs[1]["tool_choice"] == "required"
    assert _tool_operations(provider.calls[2][1] or []) == [
        "read_campaign_reference",
        "update_campaign_file",
    ]
    assert provider.request_kwargs[2]["tool_choice"] == "required"


def test_rejected_campaign_file_call_is_corrected_by_the_same_gm() -> None:
    corrected = {
        "facts": [
            {
                "fact_id": "key-received",
                "category": "inventory_context",
                "text": "Daniel received Mara's iron key.",
                "visibility": "player_known",
                "known_by": ["rift-player-character", "mara"],
            }
        ]
    }
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content="Mara places the iron key in your hand.")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-invalid-file-update",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "facts": [{}],
                                    "revised_player_facing_response": [
                                        {
                                            "kind": "narration",
                                            "text": ("Mara places the iron key in your hand."),
                                        }
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-corrected-file-update",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **corrected,
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _provider_input(user_text="I accept the key.")
    attempts: list[dict[str, object]] = []

    def write(parameters: Mapping[str, object]) -> Mapping[str, object]:
        attempts.append(dict(parameters))
        if len(attempts) == 1:
            raise MechanicsRejected(
                "Campaign File fact fields are invalid.",
                code="campaign_file_update_invalid",
                field_paths=("facts[0].fact_id",),
                validation_issues=(
                    MechanicsValidationIssue(
                        path="facts[0].fact_id",
                        kind="missing_required_field",
                        expected_type="string",
                        parent_path="facts[0]",
                    ),
                ),
                state_changed=False,
            )
        return {"status": "campaign_file_updated", "resulting_revision": 5}

    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=write,
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert attempts == [{"facts": [{}]}, corrected]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        "Mara places the iron key in your hand."
    ]
    assert "campaign_file_update_invalid" in str(provider.calls[2][0])
    assert "SYLVIA_GM_TOOL_CORRECTION" in str(provider.calls[2][0])
    assert "correct every listed issue" in str(provider.calls[2][0]).casefold()
    assert '"kind":"missing_required_field"' in str(provider.calls[2][0])
    assert '"expected_type":"string"' in str(provider.calls[2][0])
    assert "Mara places the iron key in your hand." in str(provider.calls[2][0])
    assert "already holds the exact player-facing response" in str(provider.calls[2][0])
    assert "complete replacement" in str(provider.calls[2][0])
    correction_schema = (provider.calls[2][1] or [])[0]["function"]["parameters"]
    assert "revised_player_facing_response" in correction_schema["properties"]
    assert "revised_player_facing_response" not in correction_schema["required"]
    assert "instead of patching only one symptom" in str(provider.calls[2][0])
    assert len(provider.calls) == 3


def test_campaign_file_correction_keeps_its_alias_when_the_catalog_narrows() -> None:
    invalid = {"facts": [{}]}
    corrected = {
        "facts": [
            {
                "fact_id": "voices-heard-east",
                "category": "world_state",
                "text": "Approaching voices were heard on the eastern trail.",
                "visibility": "gm_private",
                "known_by": ["gm"],
            }
        ]
    }
    narration = "Voices carry between the eastern trees."
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-invalid-mixed-catalog-update",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **invalid,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": narration}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-corrected-mixed-catalog-update",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **corrected,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": narration}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _provider_input(user_text="I listen for the approaching voices.")
    attempts: list[dict[str, object]] = []

    def write(parameters: Mapping[str, object]) -> Mapping[str, object]:
        attempts.append(dict(parameters))
        if len(attempts) == 1:
            raise MechanicsRejected(
                "Campaign File fact fields are invalid.",
                code="campaign_file_update_invalid",
                field_paths=("facts[0].fact_id",),
                state_changed=False,
            )
        return {"status": "campaign_file_updated", "resulting_revision": 5}

    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(*source.catalog, _campaign_file_tool(allow_unchanged=True)),
        control=source.control,
        narrative_writer=write,
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert attempts == [invalid, corrected]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [narration]
    first_tools = provider.calls[0][1] or []
    correction_tools = provider.calls[1][1] or []
    assert _tool_operations(first_tools) == [
        "advance",
        "update_campaign_file",
    ]
    assert _tool_operations(correction_tools) == ["update_campaign_file"]
    assert provider.request_kwargs[1]["tool_choice"] == "required"


def test_rejected_campaign_file_change_cannot_be_retracted_as_unchanged() -> None:
    draft = "Mara places the iron key in your hand."
    invalid = {"facts": [{}]}
    corrected = {
        "facts": [
            {
                "fact_id": "key-received",
                "category": "promise",
                "text": "Mara gave the adventurer an iron key.",
                "visibility": "player_known",
                "known_by": ["rift-player-character", "mara"],
            }
        ]
    }
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content=draft, finish_reason="stop")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-invalid-file-change",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **invalid,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": draft}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-invalid-unchanged-escape",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": draft}
                                    ]
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-corrected-file-change",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **corrected,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": draft}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _provider_input(user_text="I accept the key.")
    writes: list[dict[str, object]] = []

    def write(parameters: Mapping[str, object]) -> Mapping[str, object]:
        writes.append(dict(parameters))
        if len(writes) == 1:
            raise MechanicsRejected(
                "Campaign File fact fields are invalid.",
                code="campaign_file_update_invalid",
                field_paths=("facts[0].fact_id",),
                state_changed=False,
            )
        if not parameters:
            return {
                "status": "campaign_file_unchanged",
                "campaign_revision": 4,
                "state_changed": False,
            }
        return {"status": "campaign_file_updated", "resulting_revision": 5}

    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=write,
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [invalid, corrected]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [draft]
    assert len(provider.calls) == 4
    assert "campaign_file_unchanged_after_rejected_update" in str(provider.calls[3][0])


def test_campaign_file_update_allows_a_fourth_model_correction_after_retractions() -> None:
    draft = "Mara places the iron key in your hand."
    invalid = {"facts": [{}]}
    corrected = {
        "facts": [
            {
                "fact_id": "key-received-after-corrections",
                "category": "promise",
                "text": "Mara gave the adventurer an iron key.",
                "visibility": "player_known",
                "known_by": ["rift-player-character", "mara"],
            }
        ]
    }

    def update_call(call_id: str, parameters: Mapping[str, object]) -> list[ProviderStreamChunk]:
        return [
            ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": call_id,
                    "function": {
                        "name": "update_campaign_file",
                        "arguments": json.dumps(
                            {
                                **parameters,
                                "revised_player_facing_response": [
                                    {"kind": "narration", "text": draft}
                                ],
                            }
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
        ]

    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content=draft, finish_reason="stop")],
            update_call("call-invalid-one", invalid),
            update_call("call-invalid-two", invalid),
            update_call("call-retraction-one", {}),
            update_call("call-retraction-two", {}),
            update_call("call-finally-corrected", corrected),
        ]
    )
    source = _provider_input(user_text="I accept the key.")
    writes: list[dict[str, object]] = []

    def write(parameters: Mapping[str, object]) -> Mapping[str, object]:
        writes.append(dict(parameters))
        if len(writes) < 3:
            raise MechanicsRejected(
                "Campaign File fact fields are invalid.",
                code="campaign_file_update_invalid",
                field_paths=("facts[0].fact_id",),
                state_changed=False,
            )
        return {"status": "campaign_file_updated", "resulting_revision": 5}

    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=write,
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [invalid, invalid, corrected]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [draft]
    assert len(provider.calls) == 6
    assert "correction 4 of 5" in str(provider.calls[5][0]).casefold()


def test_direct_narration_requires_an_explicit_unchanged_campaign_file_review() -> None:
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    content="Rain ticks softly against the shutter.", finish_reason="stop"
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-file-unchanged",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "revised_player_facing_response": [
                                        {
                                            "kind": "narration",
                                            "text": ("Rain ticks softly against the shutter."),
                                        }
                                    ]
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _provider_input(user_text="I wait quietly.")
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {
                "status": "campaign_file_unchanged",
                "campaign_revision": 4,
                "state_changed": False,
            }
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [{}]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        "Rain ticks softly against the shutter."
    ]
    assert len(provider.calls) == 2
    assert provider.request_kwargs[0]["tool_choice"] == "required"
    assert provider.request_kwargs[1]["tool_choice"] == "required"
    assert _tool_operations(provider.calls[1][1] or []) == ["update_campaign_file"]
    assert "SYLVIA_GM_REQUIRED_TOOL_CALL_CORRECTION" in str(provider.calls[1][0])
    assert provider.calls[1][0][-1]["role"] == "system"
    assert str(provider.calls[1][0][-1]["content"]).startswith(
        "SYLVIA_GM_REQUIRED_TOOL_CALL_CORRECTION"
    )


def test_forced_campaign_file_review_recovers_when_provider_narrates_again() -> None:
    draft = "You find a quiet deer-run beneath the ferns."
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content=draft, finish_reason="stop")],
            [
                ProviderStreamChunk(
                    content="You continue farther down the path.",
                    finish_reason="stop",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-file-after-missing-call",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": draft}
                                    ]
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _provider_input(user_text="I skirt the clearing and look for a quieter route.")
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {
                "status": "campaign_file_unchanged",
                "campaign_revision": 4,
                "state_changed": False,
            }
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [{}]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [draft]
    assert "You continue farther down the path." not in str(events)
    assert len(provider.calls) == 3
    assert provider.request_kwargs[1]["tool_choice"] == "required"
    assert provider.request_kwargs[2]["tool_choice"] == "required"
    assert "SYLVIA_GM_REQUIRED_TOOL_CALL_CORRECTION" in str(provider.calls[2][0])
    assert any(
        event.evidence is not None
        and event.evidence.get("kind") == "provider_forced_tool_call_missing"
        for event in events
    )


def test_forced_campaign_file_review_reports_an_empty_required_tool_response_exactly() -> None:
    draft = "Rain ticks softly against the shutter."
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content=draft, finish_reason="stop")],
            [ProviderStreamChunk(content="", finish_reason="stop")],
            [ProviderStreamChunk(content="", finish_reason="stop")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-file-after-empty-response",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": draft}
                                    ]
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _provider_input(user_text="I listen to the rain.")
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {
                "status": "campaign_file_unchanged",
                "campaign_revision": 4,
                "state_changed": False,
            }
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [{}]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [draft]
    assert len(provider.calls) == 4
    assert provider.request_kwargs[1]["tool_choice"] == "required"
    assert provider.request_kwargs[2]["tool_choice"] == "required"
    assert provider.request_kwargs[3]["tool_choice"] == "required"
    final_messages = provider.calls[3][0]
    empty_corrections = [
        message
        for message in final_messages
        if str(message.get("content", "")).startswith("SYLVIA_GM_EMPTY_COMPLETION_CORRECTION")
    ]
    assert len(empty_corrections) == 1
    assert final_messages[-1] == empty_corrections[0]
    assert final_messages[-1]["role"] == "system"
    assert "A required tool call was expected" in str(final_messages[-1]["content"])
    assert "correction 2 of 2" in str(final_messages[-1]["content"])
    assert "SYLVIA_GM_REQUIRED_TOOL_CALL_CORRECTION" in str(provider.calls[1][0])
    assert "SYLVIA_GM_REQUIRED_TOOL_CALL_CORRECTION" not in str(final_messages)
    assert any(
        event.evidence is not None and event.evidence.get("kind") == "provider_empty_completion"
        for event in events
    )


def test_missing_required_calls_do_not_consume_rejected_call_corrections() -> None:
    draft = "You and Chloe reach a dry rock overhang beyond the clearing."
    invalid = {"scenes": ["not a scene object"]}
    corrected = {
        "scenes": [
            {
                "location_id": "verdant-margin-overhang",
                "summary": "Daniel and Chloe are beneath a dry rock overhang in the Margin.",
            }
        ]
    }
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content="I continue into the trees.", finish_reason="stop")],
            [ProviderStreamChunk(content="I describe the overhang.", finish_reason="stop")],
            [ProviderStreamChunk(content="I finish the scene.", finish_reason="stop")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-file-after-three-missing-calls",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **invalid,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": draft}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-file-after-schema-correction",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(corrected),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ],
    )
    source = _provider_input(
        user_text="I leave the clearing with Chloe and look for a quiet place."
    )
    attempts: list[dict[str, object]] = []

    def write(parameters: Mapping[str, object]) -> Mapping[str, object]:
        attempts.append(dict(parameters))
        if len(attempts) == 1:
            raise MechanicsRejected(
                "Campaign File scene fields are invalid.",
                code="campaign_file_update_invalid",
                field_paths=("scenes[0]",),
                state_changed=False,
            )
        return {"status": "campaign_file_updated", "resulting_revision": 5}

    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=write,
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert attempts == [invalid, corrected]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [draft]
    assert len(provider.calls) == 5
    assert "SYLVIA_GM_TOOL_CORRECTION" in str(provider.calls[-1][0])
    missing = [
        event
        for event in events
        if event.evidence is not None
        and event.evidence.get("kind") == "provider_forced_tool_call_missing"
    ]
    assert len(missing) == 3


def test_forced_campaign_file_review_accepts_exact_json_without_tool_envelope() -> None:
    draft = "You find a quiet deer-run beneath the ferns."
    update = {
        "scenes": [
            {
                "location_id": "verdant-margin-deer-run",
                "summary": "The adventurer is following a quiet deer-run beneath the ferns.",
            }
        ]
    }
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content=draft, finish_reason="stop")],
            [
                ProviderStreamChunk(
                    content=json.dumps(
                        {
                            **update,
                            "revised_player_facing_response": [
                                {"kind": "narration", "text": draft}
                            ],
                        }
                    ),
                    finish_reason="stop",
                )
            ],
        ]
    )
    source = _provider_input(user_text="I skirt the clearing and look for a quieter route.")
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 5}
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [update]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [draft]
    assert provider.request_kwargs[1]["tool_choice"] == "required"
    assert _tool_operations(provider.calls[1][1] or []) == ["update_campaign_file"]


def test_ruleset_result_resets_campaign_file_review_before_final_narration() -> None:
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-d20-before-file-review",
                        "function": {
                            "name": "d20_test",
                            "arguments": (
                                '{"actor":"rift-player-character","skill":"athletics",'
                                '"target_number":12}'
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [ProviderStreamChunk(content="A watcher steps into the road.", finish_reason="stop")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-scene-after-d20",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "facts": [
                                        {
                                            "fact_id": "watcher-present",
                                            "category": "scene",
                                            "text": "A watcher is present in the road.",
                                            "visibility": "player_known",
                                            "known_by": ["player"],
                                        }
                                    ],
                                    "revised_player_facing_response": [
                                        {
                                            "kind": "narration",
                                            "text": ("A watcher steps into the road."),
                                        }
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _d20_provider_input(user_text="I push through the blocked road.")
    owner = _OwnerProjection()
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(*source.catalog, _campaign_file_tool(allow_unchanged=True)),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 6}
        ),
    )
    events = iter(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner), provider, owner_id="owner-provider"
        ).for_turn("turn-provider")(provider_input)
    )

    action = _next_action_event(events)
    assert action.action_command is not None
    owner.revision = 5
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={"result": {"total": 11, "outcome": "failure"}},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=2,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )
    talk = [event for event in events if event.kind is ProviderEventKind.TALK]

    assert writes == [
        {
            "facts": [
                {
                    "fact_id": "watcher-present",
                    "category": "scene",
                    "text": "A watcher is present in the road.",
                    "visibility": "player_known",
                    "known_by": ["player"],
                }
            ]
        }
    ]
    assert [event.text for event in talk] == ["A watcher steps into the road."]
    assert len(provider.calls) == 3
    assert provider.request_kwargs[2]["tool_choice"] == "required"


def test_committed_ruleset_result_requires_campaign_file_disposition_before_prose() -> None:
    class _MiniMaxDispositionProvider(_ScriptedProvider):
        def __init__(self) -> None:
            super().__init__([])

        async def generate_stream(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            timeout: float = 120.0,
            **kwargs: object,
        ) -> AsyncIterator[ProviderStreamChunk]:
            del timeout
            call_index = len(self.calls)
            self.calls.append((messages.copy(), tools))
            self.request_kwargs.append(dict(kwargs))
            if call_index == 0:
                yield ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-investigate-stone",
                        "function": {
                            "name": "d20_test",
                            "arguments": (
                                '{"actor":"rift-player-character",'
                                '"skill":"investigation","target_number":12}'
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
                return
            yield ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "call-record-investigation",
                    "function": {
                        "name": "update_campaign_file",
                        "arguments": json.dumps(
                            {
                                "facts": [
                                    {
                                        "fact_id": "stone-markings",
                                        "category": "scene",
                                        "text": (
                                            "The adventurer discerned recent marks on the stone."
                                        ),
                                        "visibility": "player_known",
                                        "known_by": ["player"],
                                    }
                                ],
                                "revised_player_facing_response": [{"kind": "narration", "text": (
                                    "Recent chisel marks interrupt the older pattern."
                                )}],
                            }
                        ),
                    },
                },
                finish_reason="tool_calls",
            )

    provider = _MiniMaxDispositionProvider()
    source = _d20_provider_input(user_text="I study the worn carvings without touching them.")
    owner = _OwnerProjection()
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(*source.catalog, _campaign_file_tool(allow_unchanged=True)),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 6}
        ),
    )
    events = iter(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner), provider, owner_id="owner-provider"
        ).for_turn("turn-provider")(provider_input)
    )

    action = _next_action_event(events)
    assert action.action_command is not None
    owner.revision = 5
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={"result": {"total": 13, "outcome": "success"}},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=2,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )

    remaining = tuple(events)

    assert writes == [
        {
            "facts": [
                {
                    "fact_id": "stone-markings",
                    "category": "scene",
                    "text": "The adventurer discerned recent marks on the stone.",
                    "visibility": "player_known",
                    "known_by": ["player"],
                }
            ]
        }
    ]
    assert [event.text for event in remaining if event.kind is ProviderEventKind.TALK] == [
        "Recent chisel marks interrupt the older pattern."
    ]
    assert provider.request_kwargs[0]["tool_choice"] == "required"
    assert provider.request_kwargs[1]["tool_choice"] == "required"
    assert any(
        tool["function"]["name"].endswith("_update_campaign_file")
        for tool in provider.calls[1][1] or []
    )
    assert "prepare_player_action_response" not in str(provider.calls)
    assert "compose_player_action_response" not in str(provider.calls)
    assert len(provider.calls) == 2


def test_ruleset_then_campaign_file_trajectory_is_told_to_commit_the_post_action_state() -> None:
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-d20-before-post-action-file",
                        "function": {
                            "name": "d20_test",
                            "arguments": (
                                '{"actor":"rift-player-character","skill":"athletics",'
                                '"target_number":12}'
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    content="You reach tree-cover with the wolf behind you.",
                    finish_reason="stop",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-post-action-scene",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "facts": [
                                        {
                                            "fact_id": "post-action-position",
                                            "category": "scene",
                                            "text": (
                                                "The adventurer has reached tree-cover with the "
                                                "wolf behind them."
                                            ),
                                            "visibility": "player_known",
                                            "known_by": ["player"],
                                        }
                                    ],
                                    "revised_player_facing_response": [
                                        {
                                            "kind": "narration",
                                            "text": (
                                                "You reach tree-cover with the wolf behind you."
                                            ),
                                        }
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _d20_provider_input(user_text="I skirt the clearing toward tree-cover.")
    owner = _OwnerProjection()
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(*source.catalog, _campaign_file_tool(allow_unchanged=True)),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 6}
        ),
    )
    events = iter(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner), provider, owner_id="owner-provider"
        ).for_turn("turn-provider")(provider_input)
    )

    action = _next_action_event(events)
    assert action.action_command is not None
    owner.revision = 5
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={"result": {"total": 16, "outcome": "success"}},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=2,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )

    talk = [event for event in events if event.kind is ProviderEventKind.TALK]

    assert writes == [
        {
            "facts": [
                {
                    "fact_id": "post-action-position",
                    "category": "scene",
                    "text": "The adventurer has reached tree-cover with the wolf behind them.",
                    "visibility": "player_known",
                    "known_by": ["player"],
                }
            ]
        }
    ]
    assert [event.text for event in talk] == ["You reach tree-cover with the wolf behind you."]
    assert (
        "continue this same gm turn from these committed results"
        in str(provider.calls[1][0]).casefold()
    )
    assert "finish with the campaign file disposition" in str(provider.calls[1][0]).casefold()
    assert "sylvia_gm_required_tool_call_correction" in str(provider.calls[2][0]).casefold()
    assert "You reach tree-cover with the wolf behind you." not in str(provider.calls[2][0])
    assert len(provider.calls) == 3


def test_ruleset_result_exposes_campaign_file_for_final_narration_and_keeps_fallback() -> None:
    draft = (
        "You slip northwest onto a deer-run between two Warden stones. "
        "Behind you, the wounded wolf settles at the clearing's edge."
    )
    post_action_update = {
        "scenes": [
            {
                "location_id": "verdant-margin-deer-run",
                "summary": (
                    "The adventurer is on a northwest deer-run between two Warden stones; "
                    "the wounded wolf remains behind at the clearing's edge."
                ),
            }
        ]
    }
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-d20-before-empty-file-review",
                        "function": {
                            "name": "d20_test",
                            "arguments": (
                                '{"actor":"rift-player-character","skill":"perception",'
                                '"target_number":12}'
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [ProviderStreamChunk(content=draft, finish_reason="stop")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-bound-post-action-file-review",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **post_action_update,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": draft}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _d20_provider_input(user_text="I skirt the clearing toward a quieter route.")
    owner = _OwnerProjection()
    writes: list[dict[str, object]] = []

    def write(parameters: Mapping[str, object]) -> Mapping[str, object]:
        payload = dict(parameters)
        writes.append(payload)
        return {"status": "campaign_file_updated", "resulting_revision": owner.revision + 1}

    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(*source.catalog, _campaign_file_tool(allow_unchanged=True)),
        control=source.control,
        narrative_writer=write,
    )
    events = iter(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner), provider, owner_id="owner-provider"
        ).for_turn("turn-provider")(provider_input)
    )

    action = _next_action_event(events)
    assert action.action_command is not None
    owner.revision = 5
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TOOL_RESULT,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={"result": {"total": 15, "outcome": "success"}},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
        CanonicalEvent(
            sequence=2,
            event_type=CanonicalEventType.ACTION_COMMITTED,
            turn_id="turn-provider",
            action_id=action.action_command.action_id,
            safe_payload={"status": "committed"},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )

    remaining = tuple(events)

    assert writes == [post_action_update]
    assert [event.text for event in remaining if event.kind is ProviderEventKind.TALK] == [draft]
    assert any(
        event.evidence is not None
        and event.evidence.get("kind") == "provider_forced_tool_call_missing"
        for event in remaining
    )
    assert "sylvia_gm_required_tool_call_correction" in str(provider.calls[2][0]).casefold()
    assert draft not in str(provider.calls[2][0])
    second_round_tools = _tool_operations(provider.calls[1][1] or [])
    assert "update_campaign_file" in second_round_tools
    assert _tool_operations(provider.calls[2][1] or []) == [
        "d20_test",
        "update_campaign_file",
    ]
    assert len(provider.calls) == 3


def test_retry_reconstructs_complete_tool_pairs_under_the_persisted_gm_contract() -> None:
    source = _provider_input(user_text="I accept the key.")
    user = source.history.messages[0]
    contract = SessionMessage(
        session_id=source.session.session_id,
        sequence=2,
        message_id="gm-system-contract-session-provider",
        kind=SessionMessageKind.EVIDENCE,
        content={"kind": "gm_system_contract", "version": 1, "contract": "STABLE GM CONTRACT"},
    )
    call = SessionMessage(
        session_id=source.session.session_id,
        sequence=3,
        message_id="inference-resume-assistant-tools",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_tool_calls",
            "turn_id": "turn-provider",
            "inference_id": "inference-resume",
            "basis_message_id": user.message_id,
            "assistant": {
                "role": "assistant",
                "content": "Mara offers the iron key.",
                "tool_calls": [
                    {
                        "id": "call-resume",
                        "type": "function",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": '{"facts":[]}',
                        },
                    }
                ],
            },
        },
    )
    result = SessionMessage(
        session_id=source.session.session_id,
        sequence=4,
        message_id="inference-resume-tool-results",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_tool_results",
            "turn_id": "turn-provider",
            "inference_id": "inference-resume",
            "basis_message_id": user.message_id,
            "results": [
                {
                    "role": "tool",
                    "tool_call_id": "call-resume",
                    "name": "update_campaign_file",
                    "content": '{"status":"campaign_file_updated","resulting_revision":5}',
                }
            ],
        },
    )
    incomplete = SessionMessage(
        session_id=source.session.session_id,
        sequence=5,
        message_id="inference-incomplete-assistant-tools",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_tool_calls",
            "turn_id": "turn-provider",
            "inference_id": "inference-incomplete",
            "basis_message_id": user.message_id,
            "assistant": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-incomplete",
                        "type": "function",
                        "function": {"name": "update_campaign_file", "arguments": "{}"},
                    }
                ],
            },
        },
    )
    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(
            session=source.session,
            messages=(user, contract, call, result, incomplete),
        ),
        catalog=(_campaign_file_tool(),),
        control=source.control,
        basis_message_id=user.message_id,
    )

    messages = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()),
        _ScriptedProvider([]),
        owner_id="owner-provider",
    )._messages(provider_input)  # noqa: SLF001

    assert messages[0] == {"role": "system", "content": "STABLE GM CONTRACT"}
    assert [message["role"] for message in messages][-3:] == ["user", "assistant", "tool"]
    assert any(
        str(message.get("content", "")).startswith("SYLVIA_GM_CAMPAIGN_PREPARATION")
        for message in messages
    )
    assert any(
        str(message.get("content", "")).startswith("SYLVIA_GM_FRESH_CAMPAIGN_STATE")
        for message in messages
    )
    assert "call-resume" in str(messages)
    assert "call-incomplete" not in str(messages)


def test_resume_may_abandon_a_persisted_optional_reference_failure() -> None:
    source = _provider_input(user_text="I skirt the clearing toward a quieter route.")
    user = source.history.messages[0]
    reference_tool = ModelMechanicsTool(
        package_id="sylvia.application",
        package_version="1",
        package_sha256="0" * 64,
        name="read_campaign_reference",
        capability="context.read",
        description="READ-ONLY Campaign File reference.",
        input_schema={
            "type": "object",
            "properties": {
                "source": {"type": "string"},
                "path": {"type": "string"},
            },
            "required": ["source", "path"],
            "additionalProperties": False,
        },
        category="reference",
    )
    call = SessionMessage(
        session_id=source.session.session_id,
        sequence=2,
        message_id="inference-reference-assistant-tools",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_tool_calls",
            "turn_id": "turn-provider",
            "inference_id": "inference-reference",
            "basis_message_id": user.message_id,
            "assistant": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-reference",
                        "type": "function",
                        "function": {
                            "name": "read_campaign_reference",
                            "arguments": '{"source":"campaign_state","path":"update"}',
                        },
                    }
                ],
            },
        },
    )
    result = SessionMessage(
        session_id=source.session.session_id,
        sequence=3,
        message_id="inference-reference-tool-results",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_tool_results",
            "turn_id": "turn-provider",
            "inference_id": "inference-reference",
            "basis_message_id": user.message_id,
            "results": [
                {
                    "role": "tool",
                    "tool_call_id": "call-reference",
                    "name": "read_campaign_reference",
                    "content": json.dumps(
                        {
                            "status": "rejected",
                            "code": "command_rejected",
                            "correction_optional": True,
                            "instruction": "Retry only if the reference is still needed.",
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            ],
        },
    )
    response = "A narrow deer-run passes beneath the ferns, away from the breathing."
    update = {
        "scenes": [
            {
                "location_id": "verdant-margin-deer-run",
                "summary": "The adventurer has found a quiet deer-run beneath the ferns.",
            }
        ]
    }
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content=response, finish_reason="stop")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-resumed-update",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **update,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": response}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(session=source.session, messages=(user, call, result)),
        catalog=(reference_tool, _campaign_file_tool(allow_unchanged=True)),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 5}
        ),
        basis_message_id=user.message_id,
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [update]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [response]
    assert len(provider.calls) == 2
    assert "correction_optional" in str(provider.calls[0][0])
    assert provider.request_kwargs[0]["tool_choice"] == "required"
    assert provider.request_kwargs[1]["tool_choice"] == "required"


def test_resume_reuses_held_narration_and_releases_it_only_after_disposition() -> None:
    source = _provider_input(user_text="I wait quietly.")
    user = source.history.messages[0]
    draft = "Rain ticks softly against the shutter."
    held = SessionMessage(
        session_id=source.session.session_id,
        sequence=2,
        message_id="inference-held-narration",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_held_narration",
            "turn_id": "turn-provider",
            "inference_id": "inference-held",
            "basis_message_id": user.message_id,
            "assistant": {"role": "assistant", "content": draft},
        },
    )
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-resumed-file-review",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": draft}
                                    ]
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ]
        ]
    )
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(session=source.session, messages=(user, held)),
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {
                "status": "campaign_file_unchanged",
                "campaign_revision": 4,
                "state_changed": False,
            }
        ),
        basis_message_id=user.message_id,
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [{}]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [draft]
    assert len(provider.calls) == 1
    assert any(message.get("content") == draft for message in provider.calls[0][0])
    assert provider.request_kwargs[0]["tool_choice"] == "required"
    assert provider.calls[0][0][-1]["role"] == "system"
    assert str(provider.calls[0][0][-1]["content"]).startswith(
        "SYLVIA_GM_CAMPAIGN_FILE_DISPOSITION_REQUIRED"
    )


def test_resume_releases_the_revision_accepted_by_campaign_file_disposition() -> None:
    source = _provider_input(user_text="I wait quietly.")
    user = source.history.messages[0]
    draft = "The ground beneath your boots shifts as you stand in the clearing."
    revised = "The clearing's soft ground shifts as white roots press up through the soil."
    held = SessionMessage(
        session_id=source.session.session_id,
        sequence=2,
        message_id="inference-held-narration",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_held_narration",
            "turn_id": "turn-provider",
            "inference_id": "inference-held",
            "basis_message_id": user.message_id,
            "assistant": {"role": "assistant", "content": draft},
        },
    )
    disposition_call = SessionMessage(
        session_id=source.session.session_id,
        sequence=3,
        message_id="inference-disposition-tools",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_tool_calls",
            "turn_id": "turn-provider",
            "inference_id": "inference-disposition",
            "basis_message_id": user.message_id,
            "assistant": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-disposition",
                        "type": "function",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "scenes": [
                                        {
                                            "location_id": "verdant-margin-clearing",
                                            "summary": "White roots press through the soil.",
                                        }
                                    ],
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": revised}
                                    ],
                                }
                            ),
                        },
                    }
                ],
            },
        },
    )
    disposition_result = SessionMessage(
        session_id=source.session.session_id,
        sequence=4,
        message_id="inference-disposition-results",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_tool_results",
            "turn_id": "turn-provider",
            "inference_id": "inference-disposition",
            "basis_message_id": user.message_id,
            "results": [
                {
                    "role": "tool",
                    "tool_call_id": "call-disposition",
                    "name": "update_campaign_file",
                    "content": json.dumps(
                        {
                            "status": "campaign_file_updated",
                            "result": {
                                "status": "campaign_file_updated",
                                "resulting_revision": 5,
                            },
                            "player_facing_response": revised,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            ],
        },
    )
    provider = _ScriptedProvider([])
    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(
            session=source.session,
            messages=(user, held, disposition_call, disposition_result),
        ),
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=lambda _parameters: pytest.fail("resume must not write again"),
        basis_message_id=user.message_id,
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [revised]
    assert provider.calls == []


@pytest.mark.parametrize("opening", ("ordinary", "legacy", "ready_reference"))
def test_resume_narrates_after_an_accepted_tool_only_campaign_file_update(opening: str) -> None:
    source = _provider_input(user_text="I accept Mara's offer.")
    user = source.history.messages[0]
    opening_messages: tuple[SessionMessage, ...] = ()
    ready_source = replace(
        user,
        message_id="builder-ready",
        content={"text": "Ready to Begin", "interview_step": "ready"},
    )

    class OpeningOwner(_OwnerProjection):
        def session_context(self, session_id: str) -> SessionContext:
            assert session_id == source.session.session_id
            return SessionContext(session=source.session, messages=(ready_source,))

    if opening != "ordinary":
        user = replace(
            user,
            kind=SessionMessageKind.EVIDENCE,
            content={"kind": "campaign_start", "event": "campaign_start"},
        )
        if opening == "ready_reference":
            opening_messages = (replace(user, message_id="campaign-start"),)
            user = replace(
                user,
                content={
                    "kind": "ready_to_begin_reference",
                    "source_session_id": source.session.session_id,
                    "source_message_id": ready_source.message_id,
                },
            )
    narration = "Mara closes the gate and gives Daniel the iron key."
    update = {
        "facts": [
            {
                "fact_id": "mara-gave-iron-key",
                "category": "promise",
                "text": "Mara gave Daniel the iron gate key.",
                "visibility": "player_known",
                "known_by": ["rift-player-character", "mara"],
            }
        ]
    }
    tool_call = SessionMessage(
        session_id=source.session.session_id,
        sequence=2,
        message_id="inference-tool-only-update-tools",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_tool_calls",
            "turn_id": "turn-provider",
            "inference_id": "inference-tool-only-update",
            "basis_message_id": user.message_id,
            "assistant": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-tool-only-update",
                        "type": "function",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(update),
                        },
                    }
                ],
            },
        },
    )
    tool_result = SessionMessage(
        session_id=source.session.session_id,
        sequence=3,
        message_id="inference-tool-only-update-results",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_tool_results",
            "turn_id": "turn-provider",
            "inference_id": "inference-tool-only-update",
            "basis_message_id": user.message_id,
            "results": [
                {
                    "role": "tool",
                    "tool_call_id": "call-tool-only-update",
                    "name": "update_campaign_file",
                    "content": json.dumps(
                        {
                            "status": "campaign_file_updated",
                            "result": {
                                "status": "campaign_file_updated",
                                "resulting_revision": 5,
                            },
                            "player_facing_response": None,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            ],
        },
    )
    provider = _ScriptedProvider([[ProviderStreamChunk(content=narration, finish_reason="stop")]])
    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(
            session=source.session,
            messages=tuple(
                replace(message, sequence=index + 1)
                for index, message in enumerate((*opening_messages, user, tool_call, tool_result))
            ),
        ),
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=lambda _parameters: pytest.fail("resume must not write again"),
        basis_message_id=user.message_id,
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, OpeningOwner()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [narration]
    assert len(provider.calls) == 1
    assert provider.calls[0][1] is None
    assert "campaign_file_updated" in str(provider.calls[0][0])
    assert "SYLVIA_GM_CAMPAIGN_FILE_UPDATED_NARRATION" in str(provider.calls[0][0])
    user_contents = [
        message["content"] for message in provider.calls[0][0] if message.get("role") == "user"
    ]
    assert user_contents == (
        ["Ready to Begin"]
        if opening == "ready_reference"
        else []
        if opening == "legacy"
        else ["I accept Mara's offer."]
    )


def test_resume_keeps_held_narration_through_rejected_campaign_file_correction() -> None:
    source = _provider_input(user_text="I take the quiet northern path.")
    user = source.history.messages[0]
    draft = "You slip beneath the ferns while the amber eyes remain behind."
    held = SessionMessage(
        session_id=source.session.session_id,
        sequence=2,
        message_id="inference-held-north-narration",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_held_narration",
            "turn_id": "turn-provider",
            "inference_id": "inference-held-north",
            "basis_message_id": user.message_id,
            "assistant": {"role": "assistant", "content": draft},
        },
    )
    rejected_call = SessionMessage(
        session_id=source.session.session_id,
        sequence=3,
        message_id="inference-rejected-update-tools",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_tool_calls",
            "turn_id": "turn-provider",
            "inference_id": "inference-rejected-update",
            "basis_message_id": user.message_id,
            "assistant": {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call-rejected-update",
                        "type": "function",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": '{"progress_status":"engaged"}',
                        },
                    }
                ],
            },
        },
    )
    rejected_result = SessionMessage(
        session_id=source.session.session_id,
        sequence=4,
        message_id="inference-rejected-update-results",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_tool_results",
            "turn_id": "turn-provider",
            "inference_id": "inference-rejected-update",
            "basis_message_id": user.message_id,
            "results": [
                {
                    "role": "tool",
                    "tool_call_id": "call-rejected-update",
                    "name": "update_campaign_file",
                    "content": json.dumps(
                        {
                            "status": "rejected",
                            "code": "campaign_file_update_invalid",
                            "field_paths": ["progress_status"],
                            "state_changed": False,
                        },
                        sort_keys=True,
                        separators=(",", ":"),
                    ),
                }
            ],
        },
    )
    corrected_update = {
        "plan_progress": [
            {
                "plan_item_id": "arrival-and-waystation",
                "progress_status": "engaged",
                "progress_summary": "The northern route is now in play.",
            }
        ]
    }
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-resumed-unchanged-escape",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": draft}
                                    ]
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-corrected-update",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **corrected_update,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": draft}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    writes: list[dict[str, object]] = []

    def write(parameters: Mapping[str, object]) -> Mapping[str, object]:
        writes.append(dict(parameters))
        if not parameters:
            return {
                "status": "campaign_file_unchanged",
                "campaign_revision": 5,
                "state_changed": False,
            }
        return {"status": "campaign_file_updated", "resulting_revision": 6}

    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(
            session=source.session,
            messages=(user, held, rejected_call, rejected_result),
        ),
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=write,
        basis_message_id=user.message_id,
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [corrected_update]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [draft]
    assert len(provider.calls) == 2
    assert _tool_operations(provider.calls[1][1] or []) == ["update_campaign_file"]
    assert all(request["tool_choice"] == "required" for request in provider.request_kwargs)
    assert "campaign_file_update_invalid" in str(provider.calls[0][0])
    assert "campaign_file_unchanged_after_rejected_update" in str(provider.calls[1][0])


def test_tool_call_evidence_is_emitted_before_campaign_file_side_effect() -> None:
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content="The ledger is current.", finish_reason="stop")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-write-order",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "entities": [
                                        {
                                            "entity_id": "alrya",
                                            "name": "Alrya",
                                            "attributes": {},
                                        }
                                    ],
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": "The ledger is current."}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _provider_input()
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 5}
        ),
    )
    stream = iter(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    held_evidence = next(stream)
    assert held_evidence.kind is ProviderEventKind.EVIDENCE
    assert held_evidence.evidence is not None
    assert held_evidence.evidence["kind"] == "provider_forced_tool_call_missing"
    assistant_evidence = next(stream)
    assert assistant_evidence.kind is ProviderEventKind.EVIDENCE
    assert assistant_evidence.evidence is not None
    assert assistant_evidence.evidence["kind"] == "provider_tool_calls"
    assert writes == []
    result_evidence = next(stream)
    assert result_evidence.evidence is not None
    assert result_evidence.evidence["kind"] == "provider_tool_results"
    assert writes == [{"entities": [{"entity_id": "alrya", "name": "Alrya", "attributes": {}}]}]
    assert [event.text for event in stream if event.kind is ProviderEventKind.TALK] == [
        "The ledger is current."
    ]


def _legacy_narrative_state_is_hidden_from_gm_draft_and_committed_only_by_reconciliation() -> None:
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content="The promise endures.", finish_reason="stop")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-reconcile",
                        "function": {
                            "name": "reconcile_campaign_state",
                            "arguments": (
                                '{"facts":[{"fact_id":"promise-kept",'
                                '"category":"promise","text":"Mara kept her promise.",'
                                '"visibility":"player_known","known_by":["player"]}]}'
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _provider_input()
    tool = ModelMechanicsTool(
        package_id="sylvia.application",
        package_version="1",
        package_sha256="0" * 64,
        name="record_campaign_memory",
        capability="narrative_state.record_memory",
        input_schema={
            "type": "object",
            "properties": {
                "fact_id": {"type": "string"},
                "category": {"type": "string"},
                "text": {"type": "string"},
                "visibility": {"type": "string"},
                "known_by": {"type": "array", "items": {"type": "string"}},
            },
            "required": [
                "fact_id",
                "category",
                "text",
                "visibility",
                "known_by",
            ],
        },
        parameter_derivations={"operation": {"kind": "constant", "value": "record_memory"}},
        category="narrative_state",
    )
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(tool,),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"message_id": "memory-promise-kept", "kind": "campaign_memory"}
        ),
    )
    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert [event.kind for event in events] == [ProviderEventKind.TALK]
    assert [event.text for event in events] == ["The promise endures."]
    assert writes == [
        {
            "operation": "record_memory",
            "fact_id": "promise-kept",
            "category": "promise",
            "text": "Mara kept her promise.",
            "visibility": "player_known",
            "known_by": ["player"],
        }
    ]
    assert (provider.calls[0][1] or []) == []
    reconciliation_messages, reconciliation_tools = provider.calls[1]
    assert [message["role"] for message in reconciliation_messages] == [
        "system",
        "system",
        "user",
    ]
    assert "Campaign State reconciliation pass" in str(reconciliation_messages[0]["content"])
    assert str(reconciliation_messages[1]["content"]).startswith("SYLVIA_GM_FRESH_CAMPAIGN_STATE")
    assert str(reconciliation_messages[2]["content"]).startswith(
        "SYLVIA_GM_PRIVATE_NARRATION_DRAFT"
    )
    assert "The promise endures." in str(reconciliation_messages[2]["content"])
    assert [tool["function"]["name"] for tool in reconciliation_tools or []] == [
        "reconcile_campaign_state"
    ]
    assert (reconciliation_tools or [])[0]["function"]["parameters"]["required"] == ["facts"]
    assert provider.request_kwargs[1]["tool_choice"] == "required"


def _legacy_private_narration_draft_gets_state_only_reconciliation_before_release() -> None:
    memory_tool = ModelMechanicsTool(
        package_id="sylvia.application",
        package_version="1",
        package_sha256="0" * 64,
        name="record_campaign_memory",
        capability="narrative_state.record_memory",
        input_schema={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
            "additionalProperties": False,
        },
        parameter_derivations={"operation": {"kind": "constant", "value": "record_memory"}},
        category="narrative_state",
    )
    entity_tool = ModelMechanicsTool(
        package_id="sylvia.application",
        package_version="1",
        package_sha256="0" * 64,
        name="record_campaign_entity",
        capability="narrative_state.record_entity",
        input_schema={
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "name": {"type": "string"},
                "attributes": {"type": "object"},
            },
            "required": ["entity_id", "name", "attributes"],
            "additionalProperties": False,
        },
        parameter_derivations={"operation": {"kind": "constant", "value": "record_entity"}},
        category="narrative_state",
    )
    plan_progress_tool = ModelMechanicsTool(
        package_id="sylvia.application",
        package_version="1",
        package_sha256="0" * 64,
        name="update_campaign_plan_progress",
        capability="narrative_state.update_plan_progress",
        input_schema={
            "type": "object",
            "properties": {
                "plan_item_id": {"type": "string"},
                "progress_status": {"type": "string"},
                "progress_summary": {"type": "string"},
            },
            "required": ["plan_item_id", "progress_status", "progress_summary"],
            "additionalProperties": False,
        },
        parameter_derivations={"operation": {"kind": "constant", "value": "update_plan_progress"}},
        category="narrative_state",
    )
    draft = "Tallow steps into the firelight, exhausted, at the Anchor Clearing."
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content=draft, finish_reason="stop")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-reconcile",
                        "function": {
                            "name": "reconcile_campaign_state",
                            "arguments": (
                                '{"entities":[{"entity_id":"tallow","name":"Tallow",'
                                '"attributes":{"location":"anchor-clearing",'
                                '"condition":"exhausted"}}],"facts":[],'
                                '"plan_progress":[{"plan_item_id":"arrival",'
                                '"progress_status":"engaged",'
                                '"progress_summary":"Play reached the first survivor lead."}]}'
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _provider_input()
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(memory_tool, entity_tool, plan_progress_tool),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "narrative_state_committed", "resulting_revision": 5 + len(writes)}
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
            campaign_plan={
                "id": "test-plan",
                "title": "Test Plan",
                "objectives": [
                    {
                        "id": "arrival",
                        "title": "Arrival",
                        "description": "Reach a believable first foothold.",
                        "completion_conditions": ["Engage a survivor lead."],
                    }
                ],
                "structure": [{"id": "act-1", "title": "Arrival", "objectives": ["arrival"]}],
            },
        ).for_turn("turn-provider")(provider_input)
    )

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [draft]
    assert writes == [
        {
            "entity_id": "tallow",
            "name": "Tallow",
            "attributes": {"location": "anchor-clearing", "condition": "exhausted"},
            "operation": "record_entity",
        },
        {
            "plan_item_id": "arrival",
            "progress_status": "engaged",
            "progress_summary": "Play reached the first survivor lead.",
            "operation": "update_plan_progress",
        },
    ]
    assert (provider.calls[0][1] or []) == []
    reconciliation_messages, reconciliation_tools = provider.calls[1]
    assert [message["role"] for message in reconciliation_messages] == [
        "system",
        "system",
        "system",
        "user",
    ]
    reconciliation_system = str(reconciliation_messages[0]["content"])
    reconciliation_state = str(reconciliation_messages[1]["content"])
    reconciliation_plan_reference = str(reconciliation_messages[2]["content"])
    reconciliation_draft = str(reconciliation_messages[3]["content"])
    assert "Campaign State reconciliation pass" in reconciliation_system
    assert "private draft" in reconciliation_system
    assert reconciliation_state.startswith("SYLVIA_GM_FRESH_CAMPAIGN_STATE")
    assert reconciliation_plan_reference.startswith("SYLVIA_GM_PLAN_PROGRESS_REFERENCE")
    assert '"id":"arrival"' in reconciliation_plan_reference
    assert "must never cause a prepared item to happen" in reconciliation_plan_reference
    assert reconciliation_draft.startswith("SYLVIA_GM_PRIVATE_NARRATION_DRAFT")
    assert draft in reconciliation_draft
    serialized_reconciliation = str(reconciliation_messages)
    assert "SYLVIA_GM_PLAYER_INTENT_CONTROL" not in serialized_reconciliation
    assert "SYLVIA_GM_CAMPAIGN_PREPARATION" not in serialized_reconciliation
    assert "SYLVIA_GM_RECENT_PLAY" not in serialized_reconciliation
    assert [tool["function"]["name"] for tool in reconciliation_tools or []] == [
        "reconcile_campaign_state",
    ]
    patch_schema = (reconciliation_tools or [])[0]["function"]["parameters"]
    assert patch_schema["required"] == ["entities", "facts", "plan_progress"]
    assert patch_schema["properties"]["entities"]["items"] == entity_tool.input_schema
    assert patch_schema["properties"]["facts"]["items"] == memory_tool.input_schema
    assert provider.request_kwargs[1]["tool_choice"] == "required"
    assert "unknown or missing location" in reconciliation_system
    assert "exact durable content of a revealed clue" in reconciliation_system


def _legacy_direct_narration_without_an_initial_write_still_requires_reconciliation() -> None:
    entity_tool = ModelMechanicsTool(
        package_id="sylvia.application",
        package_version="1",
        package_sha256="0" * 64,
        name="record_campaign_entity",
        capability="narrative_state.record_entity",
        input_schema={
            "type": "object",
            "properties": {
                "entity_id": {"type": "string"},
                "name": {"type": "string"},
                "attributes": {"type": "object"},
            },
            "required": ["entity_id", "name", "attributes"],
            "additionalProperties": False,
        },
        parameter_derivations={"operation": {"kind": "constant", "value": "record_entity"}},
        category="narrative_state",
    )
    ruleset_tool = ModelMechanicsTool(
        package_id="ruleset.example",
        package_version="1",
        package_sha256="1" * 64,
        name="inspect_trail",
        capability="exploration.inspect_trail",
        input_schema={
            "type": "object",
            "properties": {},
            "required": [],
            "additionalProperties": False,
        },
        category="mechanic",
    )
    draft = "Oast went northwest toward Old Crane's Spire."
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content=draft, finish_reason="stop")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-reconcile",
                        "function": {
                            "name": "reconcile_campaign_state",
                            "arguments": (
                                '{"entities":[{"entity_id":"oast","name":"Oast",'
                                '"attributes":{"location":'
                                '"en-route-northwest-to-old-cranes-spire"}}]}'
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    source = _provider_input()
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(entity_tool, ruleset_tool),
        control=source.control,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "narrative_state_committed", "resulting_revision": 5}
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [draft]
    assert writes == [
        {
            "entity_id": "oast",
            "name": "Oast",
            "attributes": {"location": "en-route-northwest-to-old-cranes-spire"},
            "operation": "record_entity",
        }
    ]
    assert len(provider.calls) == 2
    assert _tool_operations(provider.calls[0][1] or []) == ["inspect_trail"]
    assert provider.request_kwargs[1]["tool_choice"] == "required"


def test_campaign_start_keeps_the_ruleset_supplied_tool_surface() -> None:
    source = _provider_input()
    start_message = SessionMessage(
        session_id=source.session.session_id,
        sequence=1,
        message_id="message-campaign-start",
        kind=SessionMessageKind.EVIDENCE,
        content={"kind": "campaign_start", "event": "campaign_start"},
    )
    srd_tools = (
        ModelMechanicsTool(
            package_id="wotc.srd.synthetic",
            package_version="1",
            package_sha256="b" * 64,
            name="d20_test",
            capability="d20.test",
            input_schema={"type": "object", "properties": {}},
        ),
        ModelMechanicsTool(
            package_id="wotc.srd.synthetic",
            package_version="1",
            package_sha256="b" * 64,
            name="start_combat_encounter_entities",
            capability="combat.start",
            input_schema={"type": "object", "properties": {}},
        ),
    )
    for mechanics, narration in (
        ((), "Mara Venn faces a quiet road. What does Mara do?"),
        (
            srd_tools,
            "Mara Venn stands as the market wakes around her. What does Mara do?",
        ),
        (
            source.catalog,
            "Mara Venn hears the observatory clock tick. What does Mara do?",
        ),
    ):
        opening_update = {
            "facts": [
                {
                    "fact_id": "opening-scene",
                    "category": "scene",
                    "text": narration,
                    "visibility": "player_known",
                    "known_by": ["player"],
                }
            ]
        }
        opening_update_round = [
            ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "call-opening-update",
                    "function": {
                        "name": "update_campaign_file",
                        "arguments": json.dumps(
                            {
                                **opening_update,
                                "revised_player_facing_response": [
                                    {"kind": "narration", "text": narration}
                                ],
                            }
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
        ]
        provider = _ScriptedProvider(
            [
                [ProviderStreamChunk(content=narration, finish_reason="stop")],
                opening_update_round,
                [ProviderStreamChunk(content=narration, finish_reason="stop")],
                opening_update_round,
            ]
        )
        adapter = ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        )
        writes: list[dict[str, object]] = []
        provider_input = SessionProviderInput(
            session=source.session,
            history=SessionContext(session=source.session, messages=(start_message,)),
            catalog=(*mechanics, _campaign_file_tool(allow_unchanged=True)),
            control=ProviderControl(),
            narrative_writer=lambda parameters, writes=writes: (
                writes.append(dict(parameters))
                or {"status": "campaign_file_updated", "resulting_revision": 5}
            ),
        )
        events = tuple(adapter.for_turn("turn-provider")(provider_input))

        assert writes == [opening_update]
        assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
            narration
        ]
        first_surface = _tool_operations(provider.calls[0][1] or [])
        assert len(first_surface) == len(mechanics) + 1
        assert first_surface[-1] == "update_campaign_file"
        assert provider.request_kwargs[0]["tool_choice"] == "required"
        assert provider.request_kwargs[1]["tool_choice"] == "required"
        assert len(provider.calls[1][1] or []) == len(mechanics) + 1
        assert len(provider.calls) == 2
        disposition_context = str(provider.calls[1][0])
        assert "SYLVIA_GM_REQUIRED_TOOL_CALL_CORRECTION" in disposition_context
        assert narration not in disposition_context
        assert "OPENING-SPECIFIC RELEASE CHECK" not in disposition_context
        assert "word limit" not in disposition_context.casefold()
        assert "word budget" not in disposition_context.casefold()
        messages = provider.calls[0][0]
        lifecycle = [
            str(message.get("content", ""))
            for message in messages
            if message.get("role") == "system"
            and str(message.get("content", "")).startswith("SYLVIA_GM_LIFECYCLE_EVENT")
            and "campaign_start" in str(message.get("content", ""))
        ]
        assert len(lifecycle) == 1
        assert "application-owned, not player speech" in lifecycle[0]
        assert "campaign_start" in lifecycle[0]
        assert lifecycle[0].count("\n") == 1
        assert "Address the player" not in lifecycle[0]
        assert "read the exact Character Files" not in lifecycle[0]
        assert "Ruleset mechanic" not in lifecycle[0]
        assert "Begin the story" not in lifecycle[0]


def test_campaign_start_projects_persisted_roles_for_closed_character_schema(
    tmp_path: Path,
) -> None:
    characters = (
        {
            "id": "vesper",
            "summary": "The fixed Player Character.",
            "authored_profile": "# Vesper\n\nA range operator.",
            "participant": {"name": "Vesper", "callsign": "Vesper"},
            "entity_state": {"stress": 0},
        },
        {
            "id": "hex",
            "summary": "The required Companion.",
            "authored_profile": "# Hex\n\nA range hacker.",
            "participant": {"name": "Hex", "callsign": "Hex"},
            "entity_state": {"stress": 0},
        },
    )
    with AuthorityOwner.open(tmp_path / "party-role.sqlite3", "party-role-owner") as owner:
        candidate, setting_id = install_character_packages(
            owner, setting_characters=characters
        )
        sources = campaign_plan_sources(
            owner, candidate.package_lock, setting_id=setting_id
        )
        assert sources is not None
        payload = json.loads(_campaign_plan_example(sources))
        payload["starting_party"] = {
            "player_character": {
                "mode": "fixed",
                "character": {"id": "vesper", "path": "characters/vesper.json"},
            },
            "required_companions": [
                {"id": "hex", "path": "characters/hex.json"}
            ],
            "custom_companions": {"minimum": 0, "maximum": 0},
        }
        plan = save_reusable_campaign_plan(
            tmp_path,
            operation_id="save-party-role-plan",
            source_payload=payload,
            source_markdown="# Party Role Range",
            validate_plan=sources.validate,
        )
        selection = resolve_campaign_library_selection(
            owner,
            tmp_path,
            candidate_id=candidate.candidate_id,
            setting_id=setting_id,
            plan_id=plan.artifact_id,
        )
        participants, initial_state, selected_party = materialize_library_party(
            owner,
            selection,
            operation_id="materialize-party-roles",
            player_character_id="vesper",
            companion_ids=("hex",),
        )
        assert [dict(item.data) for item in participants] == [
            {"callsign": "Vesper", "name": "Vesper"},
            {"callsign": "Hex", "name": "Hex"},
        ]
        confirmation = owner.confirm_new_campaign(
            operation_id="confirm-party-role-campaign",
            display_name="Party Role Range",
            package_lock=candidate.package_lock,
            turn_id="turn-party-role-campaign",
            package_candidate_id=candidate.candidate_id,
            builder_session_id="builder-party-role-campaign",
            builder_provider_id="provider-party-role-campaign",
            builder_model_id="model-party-role-campaign",
            participants=participants,
            initial_state=initial_state,
        )
        owner.begin_campaign_construction(
            operation_id="begin-party-role-campaign",
            campaign_id=confirmation.campaign_id,
            lane="quick_start",
            expected_revision=confirmation.resulting_revision,
            canonical_input={"selected_party": list(reversed(selected_party))},
        )
        session = SessionRecord(
            session_id="dm-party-role-campaign",
            campaign_id=confirmation.campaign_id,
            role=WriterRole.DM,
            provider_id="provider-party-role-campaign",
            model_id="model-party-role-campaign",
        )
        provider_input = SessionProviderInput(
            session=session,
            history=SessionContext(session=session, messages=()),
            catalog=(),
            control=ProviderControl(),
        )
        adapter = ProviderSessionAdapter(
            owner, _ScriptedProvider([]), owner_id="party-role-owner"
        )

        opening = adapter._opening_footing_payload(provider_input)  # noqa: SLF001
        preparation = adapter._participant_prompt_payloads(provider_input)  # noqa: SLF001

        assert opening["player_character"] == {
            "participant_id": participants[0].participant_id,
            "party_role": "player_character",
            "name": "Vesper",
        }
        assert opening["present_companions"] == [
            {
                "participant_id": participants[1].participant_id,
                "party_role": "companion",
                "name": "Hex",
            }
        ]
        assert {item["name"]: item["party_role"] for item in preparation} == {
            "Vesper": "player_character",
            "Hex": "companion",
        }


def test_campaign_start_projects_selected_party_and_high_level_footing() -> None:
    source = _provider_input()
    start_message = SessionMessage(
        session_id=source.session.session_id,
        sequence=1,
        message_id="message-campaign-start-party-footing",
        kind=SessionMessageKind.EVIDENCE,
        content={"kind": "campaign_start", "event": "campaign_start"},
    )
    participants = (
        SimpleNamespace(
            participant_id="rift-player-character",
            kind="player_character",
            data={
                "name": "Daniel",
                "party_role": "companion",
                "species": "human",
                "background": "displaced",
                "level": 1,
                "classes": [{"id": "monk", "level": 1}],
                "private_biography": "Must not become opening exposition.",
            },
        ),
        SimpleNamespace(
            participant_id="rift-companion-chloe",
            kind="player_character",
            data={
                "name": "Chloe",
                "party_role": "player_character",
                "species": "human",
                "background": "returner",
                "level": 1,
                "classes": [{"id": "ranger", "level": 1}],
            },
        ),
    )
    narration = "Daniel and Chloe stand together at the edge of the road. What does Daniel do?"
    opening_round = [
        ProviderStreamChunk(
            tool_call_delta={
                "index": 0,
                "id": "call-opening-party-footing",
                "function": {
                    "name": "update_campaign_file",
                    "arguments": json.dumps(
                        {
                            "scenes": [
                                {
                                    "location_id": "road-edge",
                                    "summary": "Daniel and Chloe are at the road edge.",
                                }
                            ],
                            "revised_player_facing_response": [
                                {"kind": "narration", "text": narration}
                            ],
                        }
                    ),
                },
            },
            finish_reason="tool_calls",
        )
    ]
    provider = _ScriptedProvider(
        [
            opening_round,
            opening_round,
        ]
    )
    adapter = ProviderSessionAdapter(
        cast(
            AuthorityOwner,
            _OwnerProjection(
                participants=participants,
                continuity=CampaignContinuityView(
                    facts=(
                        {
                            "record_id": "opening-immediate-situation",
                            "category": "continuity",
                            "text": (
                                "The selected party stands alone in a bare Anchor Clearing; "
                                "its stone boundary is holding and no route is visible."
                            ),
                            "visibility": "player_known",
                            "known_by": ["player"],
                            "subjects": [],
                            "origin": "accepted_initial_state",
                        },
                        {
                            "record_id": "private-preparation",
                            "category": "continuity",
                            "text": "A hidden encounter might become relevant later.",
                            "visibility": "gm_private",
                            "known_by": [],
                            "subjects": [],
                            "origin": "accepted_initial_state",
                        },
                    )
                ),
                construction_input={
                    "selected_party": [
                        {
                            "participant_id": "rift-player-character",
                            "party_role": "player_character",
                            "name": "Daniel",
                            "summary": (
                                "A displaced former office worker given a second chance in a "
                                "body built for speed and precision."
                            ),
                        },
                        {
                            "participant_id": "rift-companion-chloe",
                            "party_role": "companion",
                            "name": "Chloe",
                            "summary": (
                                "A proud, exacting Riftborn archer who controls a fight through "
                                "distance and precision."
                            ),
                        },
                    ]
                },
            ),
        ),
        provider,
        owner_id="owner-provider",
    )
    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(session=source.session, messages=(start_message,)),
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=ProviderControl(),
        narrative_writer=lambda _parameters: {
            "status": "campaign_file_updated",
            "resulting_revision": 5,
        },
    )

    events = tuple(adapter.for_turn("turn-provider")(provider_input))
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [narration]
    assert len(provider.calls) == 1
    messages = provider.calls[0][0]
    assert not any(message.get("role") == "user" for message in messages)
    opening_task = str(messages[-1]["content"])
    assert opening_task.startswith("SYLVIA_GM_OPENING_TASK")
    assert '"name":"Daniel"' in opening_task
    assert '"id":"monk"' in opening_task
    assert '"name":"Chloe"' in opening_task
    assert "finish with the Campaign File disposition" in opening_task
    assert "supplied name and class" in opening_task
    assert "without backfilling anything before this moment" in opening_task
    assert "leave the player's inner life unstated" in opening_task
    assert "never turn it into amnesia, missing memory" in opening_task
    assert "Treat the footing and current State as binding" in opening_task
    assert "Use the supplied identity labels exactly" in opening_task
    assert "do not add an ancestry, culture, faction, or background label" in opening_task
    assert "do not turn an explicit absence into an exception" in opening_task
    assert (
        '"player_known_initial_facts":["The selected party stands alone in a bare '
        'Anchor Clearing; its stone boundary is holding and no route is visible."]' in opening_task
    )
    assert "A hidden encounter might become relevant later." not in opening_task
    preparation = next(
        str(message["content"])
        for message in messages
        if str(message.get("content", "")).startswith("SYLVIA_GM_CAMPAIGN_PREPARATION")
    )

    assert '"name":"Daniel"' in preparation
    assert '"classes":[{"id":"monk","level":1}]' in preparation
    assert '"name":"Chloe"' in preparation
    assert '"party_role":"companion"' in preparation
    assert "authored_summary" not in preparation
    lifecycle = next(
        str(message["content"])
        for message in messages
        if str(message.get("content", "")).startswith("SYLVIA_GM_LIFECYCLE_EVENT")
    )
    assert lifecycle.count("\n") == 1
    lifecycle_payload = json.loads(lifecycle.splitlines()[1])
    assert lifecycle_payload == {
        "event": "campaign_start",
        "opening_footing": {
            "player_character": {
                "classes": [{"id": "monk", "level": 1}],
                "name": "Daniel",
                "participant_id": "rift-player-character",
                "party_role": "player_character",
                "species": "human",
            },
            "present_companions": [
                {
                    "classes": [{"id": "ranger", "level": 1}],
                    "name": "Chloe",
                    "participant_id": "rift-companion-chloe",
                    "party_role": "companion",
                    "species": "human",
                }
            ],
            "starting_location": None,
            "player_known_initial_facts": [
                "The selected party stands alone in a bare Anchor Clearing; its stone boundary "
                "is holding and no route is visible."
            ],
            "first_player_choice_pending": True,
            "established_prior_player_actions": [],
            "established_shared_party_history": [],
        },
    }
    opening_tool = next(
        tool
        for tool in provider.calls[0][1] or []
        if str(tool["function"]["name"]).endswith("_update_campaign_file")
    )
    opening_schema = opening_tool["function"]["parameters"]
    assert "revised_player_facing_response" in opening_schema["properties"]
    assert "revised_player_facing_response" in opening_schema["required"]
    assert "player_action_preparation_id" not in opening_schema["properties"]
    assert "player_action_preparation_id" not in opening_schema["required"]
    response_description = opening_schema["properties"]["revised_player_facing_response"][
        "description"
    ]
    assert "complete ordered response" in response_description
    assert "spoken words only" in response_description
    assert "player name and class" not in response_description
    assert "prior player or shared history" not in response_description
    assert '"background"' not in preparation
    assert '"skill_proficiencies"' not in preparation


@pytest.mark.parametrize(
    ("invented", "expected_marker"),
    (
        (
            'Daniel and Chloe stand within a ring of Warden stone. Chloe says, "Something has '
            'watched us since we got here," while the forest tears nearby.',
            '"since we got here"',
        ),
        (
            "Daniel and Chloe stand within a ring of Warden stone. Chloe has waited all night for "
            "Daniel to decide where they go next while the forest tears nearby.",
            '"has waited all night"',
        ),
    ),
)
def test_campaign_start_does_not_host_rewrite_prose_that_claims_prior_travel(
    invented: str,
    expected_marker: str,
) -> None:
    source = _provider_input()
    start_message = SessionMessage(
        session_id=source.session.session_id,
        sequence=1,
        message_id="message-campaign-start-prior-travel",
        kind=SessionMessageKind.EVIDENCE,
        content={"kind": "campaign_start", "event": "campaign_start"},
    )
    participants = (
        SimpleNamespace(
            participant_id="rift-player-character",
            kind="player_character",
            data={"name": "Daniel", "party_role": "player_character"},
        ),
        SimpleNamespace(
            participant_id="rift-companion-chloe",
            kind="player_character",
            data={"name": "Chloe", "party_role": "companion"},
        ),
    )
    corrected = (
        "Daniel and Chloe stand within a ring of Warden stone. A fragment trail leads northeast "
        "toward Waystation while something tears through the forest nearby. What does Daniel do?"
    )
    scene = {
        "location_id": "anchor-clearing",
        "summary": "Daniel and Chloe face a disturbance at the Anchor Clearing.",
    }

    def opening_call(call_id: str, narration: str) -> list[ProviderStreamChunk]:
        return [
            ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": call_id,
                    "function": {
                        "name": "update_campaign_file",
                        "arguments": json.dumps(
                            {
                                "scenes": [scene],
                                "revised_player_facing_response": [
                                    {"kind": "narration", "text": narration}
                                ],
                            }
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
        ]

    provider = _ScriptedProvider(
        [
            opening_call("call-opening-invented-travel", invented),
            [ProviderStreamChunk(content=invented, finish_reason="stop")],
            [ProviderStreamChunk(content=corrected, finish_reason="stop")],
            opening_call("call-opening-corrected-travel", corrected),
        ]
    )
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(session=source.session, messages=(start_message,)),
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=ProviderControl(),
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 5}
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection(participants=participants)),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [invented]
    assert len(provider.calls) == 1
    assert "SYLVIA_GM_OPENING_CONTINUITY_CORRECTION" not in str(provider.calls)
    assert expected_marker not in str(provider.calls[0][0])
    assert writes == [{"scenes": [scene]}]


@pytest.mark.parametrize(
    ("invented", "corrected", "expected_marker"),
    (
        (
            "Daniel and Chloe occupy a pale stone clearing. Branches at the forest edge seem to "
            "shift whenever his gaze wanders, while a torn cache waits near the cold fire pit.",
            "Daniel and Chloe occupy a pale stone clearing. Branches at the forest edge shift "
            "whenever the wind drops, while a torn cache waits near the cold fire pit. What does "
            "Daniel do?",
            '"his gaze wanders"',
        ),
        (
            "Daniel and Chloe occupy a pale stone clearing. The Warden stone behind Daniel hums "
            "beneath his palm while branches shift at the forest edge.",
            "Daniel and Chloe occupy a pale stone clearing. The Warden stone behind Daniel hums "
            "while branches shift at the forest edge. What does Daniel do?",
            '"beneath his palm"',
        ),
        (
            "Daniel and Chloe occupy a pale stone clearing. Chloe is listening for movement in "
            "the forest. So are you.",
            "Daniel and Chloe occupy a pale stone clearing. Chloe listens for movement in the "
            "forest. What does Daniel do?",
            '"So are you"',
        ),
    ),
)
def test_campaign_start_does_not_host_rewrite_prose_that_spends_player_agency(
    invented: str,
    corrected: str,
    expected_marker: str,
) -> None:
    source = _provider_input()
    start_message = SessionMessage(
        session_id=source.session.session_id,
        sequence=1,
        message_id="message-campaign-start-player-gaze",
        kind=SessionMessageKind.EVIDENCE,
        content={"kind": "campaign_start", "event": "campaign_start"},
    )
    participants = (
        SimpleNamespace(
            participant_id="rift-player-character",
            kind="player_character",
            data={"name": "Daniel", "party_role": "player_character"},
        ),
        SimpleNamespace(
            participant_id="rift-companion-chloe",
            kind="player_character",
            data={"name": "Chloe", "party_role": "companion"},
        ),
    )
    scene = {
        "location_id": "anchor-clearing",
        "summary": "Daniel and Chloe face a disturbance at the Anchor Clearing.",
    }

    def opening_call(call_id: str, narration: str) -> list[ProviderStreamChunk]:
        return [
            ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": call_id,
                    "function": {
                        "name": "update_campaign_file",
                        "arguments": json.dumps(
                            {
                                "scenes": [scene],
                                "revised_player_facing_response": [
                                    {"kind": "narration", "text": narration}
                                ],
                            }
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
        ]

    provider = _ScriptedProvider(
        [
            opening_call("call-opening-invented-gaze", invented),
            [ProviderStreamChunk(content=invented, finish_reason="stop")],
            [ProviderStreamChunk(content=corrected, finish_reason="stop")],
            opening_call("call-opening-corrected-gaze", corrected),
        ]
    )
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(session=source.session, messages=(start_message,)),
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=ProviderControl(),
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 5}
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection(participants=participants)),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [invented]
    assert len(provider.calls) == 1
    assert "SYLVIA_GM_OPENING_PLAYER_AGENCY_CORRECTION" not in str(provider.calls)
    assert expected_marker not in str(provider.calls[0][0])
    assert writes == [{"scenes": [scene]}]


def test_campaign_start_does_not_host_rewrite_prose_for_release_shape() -> None:
    source = _provider_input()
    start_message = SessionMessage(
        session_id=source.session.session_id,
        sequence=1,
        message_id="message-campaign-start-release-shape",
        kind=SessionMessageKind.EVIDENCE,
        content={"kind": "campaign_start", "event": "campaign_start"},
    )
    participants = (
        SimpleNamespace(
            participant_id="rift-player-character",
            kind="player_character",
            data={"name": "Daniel", "party_role": "player_character"},
        ),
        SimpleNamespace(
            participant_id="rift-companion-chloe",
            kind="player_character",
            data={"name": "Chloe", "party_role": "companion"},
        ),
    )
    repeated_detail = "The clearing keeps another decorative but unhelpful detail in view. "
    invented = repeated_detail * 22 + (
        '"Your call, Daniel," Chloe says. "Follow the prints, inspect the cache, or wait."'
    )
    corrected = (
        "Daniel and Chloe stand inside a pale Warden-stone clearing where the forest cannot take "
        "root. His quarterstaff and her longbow are close at hand. Ahead, the undergrowth bends "
        "and cracks beneath something heavy moving straight toward the clearing, still hidden by "
        "the violet leaves. Chloe watches the moving brush and waits for Daniel's lead. What does "
        "Daniel do?"
    )
    scene = {
        "location_id": "anchor-clearing",
        "summary": "Daniel and Chloe face a disturbance at the Anchor Clearing.",
    }

    def opening_call(call_id: str, narration: str) -> list[ProviderStreamChunk]:
        return [
            ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": call_id,
                    "function": {
                        "name": "update_campaign_file",
                        "arguments": json.dumps(
                            {
                                "scenes": [scene],
                                "revised_player_facing_response": [
                                    {"kind": "narration", "text": narration}
                                ],
                            }
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
        ]

    provider = _ScriptedProvider(
        [
            opening_call("call-opening-poor-release-shape", invented),
            [ProviderStreamChunk(content=invented, finish_reason="stop")],
            [ProviderStreamChunk(content=corrected, finish_reason="stop")],
            opening_call("call-opening-corrected-release-shape", corrected),
        ]
    )
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(session=source.session, messages=(start_message,)),
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=ProviderControl(),
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 5}
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection(participants=participants)),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [invented]
    assert len(provider.calls) == 1
    assert "SYLVIA_GM_OPENING_RELEASE_CORRECTION" not in str(provider.calls)
    assert "word_count=" not in str(provider.calls)
    assert writes == [{"scenes": [scene]}]


def test_campaign_start_does_not_host_rewrite_missing_party_names() -> None:
    source = _provider_input()
    start_message = SessionMessage(
        session_id=source.session.session_id,
        sequence=1,
        message_id="message-campaign-start-missing-party-name",
        kind=SessionMessageKind.EVIDENCE,
        content={"kind": "campaign_start", "event": "campaign_start"},
    )
    participants = (
        SimpleNamespace(
            participant_id="rift-player-character",
            kind="player_character",
            data={"name": "Daniel", "party_role": "player_character"},
        ),
        SimpleNamespace(
            participant_id="rift-companion-chloe",
            kind="player_character",
            data={"name": "Chloe", "party_role": "companion"},
        ),
    )
    incomplete = "Chloe watches a strange deer standing motionless in the violet forest."
    corrected = (
        "Daniel, displaced into a body built for speed, stands beside Chloe as a strange deer "
        "watches them from the violet forest. What does Daniel do?"
    )
    scene = {
        "location_id": "verdant-margin",
        "summary": "Daniel and Chloe face a strange deer in the Verdant Margin.",
    }

    def opening_call(call_id: str, narration: str) -> list[ProviderStreamChunk]:
        return [
            ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": call_id,
                    "function": {
                        "name": "update_campaign_file",
                        "arguments": json.dumps(
                            {
                                "scenes": [scene],
                                "revised_player_facing_response": [
                                    {"kind": "narration", "text": narration}
                                ],
                            }
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
        ]

    provider = _ScriptedProvider(
        [
            opening_call("call-opening-missing-daniel", incomplete),
            opening_call("call-opening-with-party", corrected),
            [ProviderStreamChunk(content=corrected, finish_reason="stop")],
            opening_call("call-opening-after-editorial-review", corrected),
        ]
    )
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(session=source.session, messages=(start_message,)),
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=ProviderControl(),
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 5}
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection(participants=participants)),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [{"scenes": [scene]}]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [incomplete]
    assert len(provider.calls) == 1
    assert "campaign_opening_party_footing_incomplete" not in str(provider.calls)


def test_campaign_start_accepts_a_new_scene_without_artificial_plan_progress() -> None:
    source = _provider_input()
    start_message = SessionMessage(
        session_id=source.session.session_id,
        sequence=1,
        message_id="message-campaign-start-plan-progress",
        kind=SessionMessageKind.EVIDENCE,
        content={"kind": "campaign_start", "event": "campaign_start"},
    )
    narration = "Mara Venn sees a violet clearing ahead, waiting for her choice. What does Mara do?"
    scene = {
        "summary": "A violet clearing is available ahead of the player.",
        "location_id": "verdant-margin",
        "act_id": "act-1",
    }
    corrected_update = {"scenes": [scene]}
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content=narration, finish_reason="stop")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-opening-missing-progress",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "scenes": [scene],
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": narration}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-opening-corrected-progress",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **corrected_update,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": narration}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [ProviderStreamChunk(content=narration, finish_reason="stop")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-opening-after-editorial-review",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **corrected_update,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": narration}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(session=source.session, messages=(start_message,)),
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=ProviderControl(),
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 5}
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [corrected_update]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [narration]
    assert len(provider.calls) == 2
    assert provider.request_kwargs[1]["tool_choice"] == "required"
    assert "campaign_opening_plan_progress_incomplete" not in str(provider.calls)


def test_campaign_start_does_not_host_reclassify_supplied_plan_progress() -> None:
    source = _provider_input()
    start_message = SessionMessage(
        session_id=source.session.session_id,
        sequence=1,
        message_id="message-campaign-start-wrong-progress",
        kind=SessionMessageKind.EVIDENCE,
        content={"kind": "campaign_start", "event": "campaign_start"},
    )
    narration = "The violet clearing waits for your first choice."
    scene = {
        "summary": "A violet clearing is available ahead of the player.",
        "location_id": "verdant-margin",
        "act_id": "act-1",
    }
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-opening-wrong-progress",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "scenes": [scene],
                                    "plan_progress": [
                                        {
                                            "plan_item_id": "act-1",
                                            "progress_status": "engaged",
                                            "progress_summary": "The opening is available.",
                                        }
                                    ],
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": narration}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [ProviderStreamChunk(content="I cannot safely continue.", finish_reason="stop")],
            [ProviderStreamChunk(content="I cannot safely continue.", finish_reason="stop")],
            [ProviderStreamChunk(content="I cannot safely continue.", finish_reason="stop")],
        ]
    )
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(session=source.session, messages=(start_message,)),
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=ProviderControl(),
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 5}
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [
        {
            "scenes": [scene],
            "plan_progress": [
                {
                    "plan_item_id": "act-1",
                    "progress_status": "engaged",
                    "progress_summary": "The opening is available.",
                }
            ],
        }
    ]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [narration]
    assert len(provider.calls) == 1


def test_campaign_start_disposition_does_not_require_plan_progress() -> None:
    source = _provider_input()
    start_message = SessionMessage(
        session_id=source.session.session_id,
        sequence=1,
        message_id="message-campaign-start",
        kind=SessionMessageKind.EVIDENCE,
        content={"kind": "campaign_start", "event": "campaign_start"},
    )
    drafted = "Mara Venn reaches a violet clearing. What does Mara do?"
    reviewed = "Mara Venn stands in a violet clearing. What does Mara do?"
    rewritten = reviewed + " She remembers travelling all morning."
    scene = {
        "summary": "A violet clearing is available ahead of the player.",
        "location_id": "verdant-margin",
        "act_id": "act-1",
    }
    corrected_update = {"scenes": [scene]}

    def opening_call(
        call_id: str,
        narration: str,
        *,
        include_progress: bool,
    ) -> list[ProviderStreamChunk]:
        update = corrected_update if include_progress else {"scenes": [scene]}
        return [
            ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": call_id,
                    "function": {
                        "name": "update_campaign_file",
                        "arguments": json.dumps(
                            {
                                **update,
                                "revised_player_facing_response": [
                                    {"kind": "narration", "text": narration}
                                ],
                            }
                        ),
                    },
                },
                finish_reason="tool_calls",
            )
        ]

    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content=drafted, finish_reason="stop")],
            opening_call("call-missing-progress", drafted, include_progress=False),
            opening_call("call-corrected-progress", drafted, include_progress=True),
            [ProviderStreamChunk(content=reviewed, finish_reason="stop")],
            opening_call("call-rewritten-after-review", rewritten, include_progress=True),
            opening_call("call-exact-after-review", reviewed, include_progress=True),
        ]
    )
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(session=source.session, messages=(start_message,)),
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=ProviderControl(),
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 5}
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [corrected_update]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [drafted]
    assert len(provider.calls) == 2
    assert provider.request_kwargs[1]["tool_choice"] == "required"
    assert "campaign_file_editorial_revision_mismatch" not in str(provider.calls)
    assert reviewed not in str(provider.calls[0][0])
    assert rewritten not in str(provider.calls[0][0])


def test_campaign_start_accepts_a_tool_only_scene_without_plan_progress() -> None:
    source = _provider_input()
    start_message = SessionMessage(
        session_id=source.session.session_id,
        sequence=1,
        message_id="message-tool-only-campaign-start",
        kind=SessionMessageKind.EVIDENCE,
        content={"kind": "campaign_start", "event": "campaign_start"},
    )
    narration = "Mara Venn sees a violet clearing ahead, waiting for her choice. What does Mara do?"
    scene = {
        "summary": "A violet clearing is available ahead of the player.",
        "location_id": "verdant-margin",
        "act_id": "act-1",
    }
    corrected_update = {"scenes": [scene]}
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-tool-only-opening-missing-progress",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "scenes": [scene],
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": narration}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-tool-only-opening-corrected-progress",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **corrected_update,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": narration}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [ProviderStreamChunk(content=narration, finish_reason="stop")],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-tool-only-opening-after-editorial-review",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **corrected_update,
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": narration}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    writes: list[dict[str, object]] = []
    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(session=source.session, messages=(start_message,)),
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=ProviderControl(),
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 5}
        ),
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [corrected_update]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [narration]
    assert len(provider.calls) == 1
    assert _tool_operations(provider.calls[0][1] or []) == ["update_campaign_file"]
    assert "campaign_opening_plan_progress_incomplete" not in str(provider.calls)


def test_explicit_refusal_then_unrelated_play_keeps_the_player_action_in_control() -> None:
    refused_response = "The courier accepts your refusal and leaves. What do you do?"
    unrelated_response = "You reach the public river steps, where the morning fishers make room."
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-refuse-lead",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "plan_progress": [
                                        {
                                            "plan_item_id": "missing-scouts",
                                            "progress_status": "deferred",
                                            "progress_summary": "The player explicitly refused it.",
                                        }
                                    ],
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": refused_response}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-unrelated-river-scene",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    "scenes": [
                                        {
                                            "location_id": "capital-river-steps",
                                            "summary": (
                                                "The player is spending the morning fishing."
                                            ),
                                        }
                                    ],
                                    "revised_player_facing_response": [
                                        {"kind": "narration", "text": unrelated_response}
                                    ],
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    writes: list[dict[str, object]] = []
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()),
        provider,
        owner_id="owner-provider",
        campaign_plan={
            "id": "off-plan-test",
            "structure": [{"id": "act-1", "title": "The Missing Scouts"}],
        },
    )

    responses: list[str] = []
    for index, action in enumerate(
        ("I refuse the courier's job.", "I go to the river and spend the morning fishing."),
        start=1,
    ):
        source = _provider_input(user_text=action)
        events = tuple(
            adapter.for_turn(f"turn-off-plan-{index}")(
                SessionProviderInput(
                    session=source.session,
                    history=source.history,
                    catalog=(_campaign_file_tool(allow_unchanged=True),),
                    control=source.control,
                    narrative_writer=lambda parameters, resulting_revision=4 + index: (
                        writes.append(dict(parameters))
                        or {
                            "status": "campaign_file_updated",
                            "resulting_revision": resulting_revision,
                        }
                    ),
                )
            )
        )
        responses.extend(
            event.text or "" for event in events if event.kind is ProviderEventKind.TALK
        )
        assert {"role": "user", "content": action} in provider.calls[index - 1][0]
        assert str(provider.calls[index - 1][0][-2]["content"]).startswith(
            "SYLVIA_GM_CURRENT_PLAYER_MESSAGE"
        )

    assert responses == [refused_response, unrelated_response]
    assert writes[0]["plan_progress"][0]["progress_status"] == "deferred"
    assert "plan_progress" not in writes[1]
    assert writes[1]["scenes"][0]["location_id"] == "capital-river-steps"


def test_campaign_start_fails_clearly_without_a_campaign_file_update_surface() -> None:
    source = _provider_input()
    start_message = SessionMessage(
        session_id=source.session.session_id,
        sequence=1,
        message_id="message-campaign-start-no-file",
        kind=SessionMessageKind.EVIDENCE,
        content={"kind": "campaign_start", "event": "campaign_start"},
    )
    provider = _ScriptedProvider(
        [[ProviderStreamChunk(content="An opening that cannot be saved.", finish_reason="stop")]]
    )
    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(session=source.session, messages=(start_message,)),
        catalog=source.catalog,
        control=ProviderControl(),
    )

    with pytest.raises(CommandRejected) as caught:
        tuple(
            ProviderSessionAdapter(
                cast(AuthorityOwner, _OwnerProjection()),
                provider,
                owner_id="owner-provider",
            ).for_turn("turn-provider")(provider_input)
        )

    assert caught.value.code == "campaign_file_update_unavailable"
    assert provider.calls == []


def test_campaign_start_never_releases_text_when_required_update_is_ignored() -> None:
    source = _provider_input()
    start_message = SessionMessage(
        session_id=source.session.session_id,
        sequence=1,
        message_id="message-campaign-start-ignored-file",
        kind=SessionMessageKind.EVIDENCE,
        content={"kind": "campaign_start", "event": "campaign_start"},
    )
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content="An unsaved opening.", finish_reason="stop")],
            [ProviderStreamChunk(content="A second unsaved opening.", finish_reason="stop")],
            [ProviderStreamChunk(content="A third unsaved opening.", finish_reason="stop")],
            [ProviderStreamChunk(content="A fourth unsaved opening.", finish_reason="stop")],
            [ProviderStreamChunk(content="A fifth unsaved opening.", finish_reason="stop")],
        ]
    )
    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(session=source.session, messages=(start_message,)),
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        control=ProviderControl(),
        narrative_writer=lambda _parameters: {},
    )

    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()),
        provider,
        owner_id="owner-provider",
        campaign_plan={
            "opening_direction": "Begin with a social misunderstanding at the city gate."
        },
    )
    with pytest.raises(CommandRejected) as caught:
        tuple(adapter.for_turn("turn-provider")(provider_input))

    assert caught.value.code == "provider_required_tool_call_missing"
    assert provider.request_kwargs[0]["tool_choice"] == "required"
    assert all(kwargs["tool_choice"] == "required" for kwargs in provider.request_kwargs[1:])
    assert len(provider.calls) == 4
    assert all(
        "Begin with a social misunderstanding at the city gate." in str(messages)
        for messages, _tools in provider.calls
    )


def test_campaign_start_uses_plan_context_without_promoting_an_encounter_to_active_combat() -> None:
    source = _provider_input()
    start_message = SessionMessage(
        session_id=source.session.session_id,
        sequence=1,
        message_id="message-campaign-start-plan",
        kind=SessionMessageKind.EVIDENCE,
        content={"kind": "campaign_start", "event": "campaign_start"},
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()),
        _ScriptedProvider([]),
        owner_id="owner-provider",
        campaign_plan={
            "id": "the-rift-awakening",
            "title": "The Rift Awakening",
            "bindings": {
                "ruleset": {
                    "id": "sylvia.srd-lite.bg3",
                    "version": "0.3.0-alpha05",
                    "content_sha256": "b" * 64,
                }
            },
            "gm_guidance": {"instructions": "Run this as a D&D 5e campaign and follow the Plan."},
            "opening_direction": "Begin with a quiet character-led introduction.",
            "structure": [
                {
                    "id": "act-1",
                    "planned_encounters": [
                        "Immediately call the exposed start mechanic before narration."
                    ],
                },
                {
                    "id": "act-2",
                    "planned_encounters": ["Keep this future encounter out of the opening."],
                },
            ],
        },
    )
    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(session=source.session, messages=(start_message,)),
        catalog=source.catalog,
        control=ProviderControl(),
    )
    messages = adapter._messages(  # noqa: SLF001 - exact lifecycle projection under test
        provider_input
    )
    lifecycle = next(
        str(message["content"])
        for message in messages
        if str(message.get("content", "")).startswith("SYLVIA_GM_LIFECYCLE_EVENT")
    )
    application_context = str(
        adapter._campaign_context_message(  # noqa: SLF001
            provider_input,
            include_opening_direction=True,
        )["content"]
    )
    campaign_data = json.loads(application_context.split("\n", 1)[1])

    assert "CAMPAIGN_PLAN_OPENING_REQUIREMENTS" not in lifecycle
    assert "Immediately call the exposed start mechanic before narration." not in lifecycle
    assert "Keep this future encounter out of the opening." not in lifecycle
    assert lifecycle.count("\n") == 1
    assert "Opening Direction" not in lifecycle
    system = str(messages[0]["content"])
    assert "Opening Direction as a creative brief" in system
    assert "Campaign State is the GM-screen ledger" in system
    assert not any(
        str(message.get("content", "")).startswith("SYLVIA_GM_ACCEPTED_CAMPAIGN_DIRECTION")
        for message in messages
    )
    preparation = campaign_data["campaign_plan_preparation"]
    assert preparation["gm_guidance"]["instructions"] == (
        "Run this as a D&D 5e campaign and follow the Plan."
    )
    prepared_act = preparation["prepared_acts"][0]
    assert set(prepared_act) == {
        "id", "title", "reference", "objective_references", "detail_handling"
    }
    assert (
        "Read this act before framing its main quest or judging its ending"
        in (prepared_act["detail_handling"])
    )
    assert "Immediately call the exposed start mechanic before narration." not in json.dumps(
        preparation
    )
    assert len(preparation["prepared_acts"]) == 1
    assert preparation["opening_direction"] == ("Begin with a quiet character-led introduction.")
    assert (
        "Creative framing for this opening exchange only"
        in (preparation["opening_direction_meaning"])
    )
    assert "Keep this future encounter out of the opening." not in json.dumps(preparation)
    assert campaign_data["campaign_state"]["current_scene"] is None
    assert "Begin the story" not in lifecycle

    later_history = SessionContext(
        session=source.session,
        messages=(
            start_message,
            SessionMessage(
                session_id=source.session.session_id,
                sequence=2,
                message_id="opening-talk",
                kind=SessionMessageKind.TALK,
                content={"text": "The campaign has begun."},
            ),
            SessionMessage(
                session_id=source.session.session_id,
                sequence=3,
                message_id="player-action",
                kind=SessionMessageKind.USER,
                content={"text": "I look around."},
            ),
            SessionMessage(
                session_id=source.session.session_id,
                sequence=4,
                message_id="first-action-talk",
                kind=SessionMessageKind.TALK,
                content={"text": "The clearing remains quiet."},
            ),
            SessionMessage(
                session_id=source.session.session_id,
                sequence=5,
                message_id="second-player-action",
                kind=SessionMessageKind.USER,
                content={"text": "I ask Mara what she thinks."},
            ),
        ),
    )
    later_messages = adapter._messages(  # noqa: SLF001
        SessionProviderInput(
            session=source.session,
            history=later_history,
            catalog=source.catalog,
            control=ProviderControl(),
        )
    )
    later_preparation_message = next(
        str(message["content"])
        for message in later_messages
        if str(message.get("content", "")).startswith("SYLVIA_GM_CAMPAIGN_PREPARATION")
    )
    assert "quiet character-led introduction" not in later_preparation_message
    later_preparation = json.loads(later_preparation_message.split("\n", 1)[1])
    assert "opening_direction" not in later_preparation["campaign_plan_preparation"]
    assert "starting_location" not in later_preparation["campaign_plan_preparation"]
    assert "objectives" not in later_preparation["campaign_plan_preparation"]
    assert "prepared_secrets" not in later_preparation["private_preparation"]
    assert later_preparation["private_preparation"]["additional_material_available_via"] == {
        "source": "campaign_plan",
        "path": "structured",
    }


def test_current_act_navigation_keeps_objectives_and_future_preparation_on_demand() -> None:
    owner = _OwnerProjection(
        continuity=CampaignContinuityView(current_scene={"act_id": "act-2"})
    )
    plan = {
        "structure": [
            {"id": "act-1", "title": "Arrival", "objectives": ["foothold"]},
            {"id": "act-2", "title": "Investigation", "objectives": ["evidence", "An inline goal"]},
            {"id": "act-3", "title": "Hidden future", "situations": ["Future secret"]},
        ],
        "objectives": [
            {"id": "foothold", "completion_conditions": ["An earlier ending"]},
            {"id": "evidence", "completion_conditions": ["Private investigation ending"]},
        ],
    }
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner), _ScriptedProvider([]), owner_id="owner-provider",
        campaign_plan=plan,
    )
    payload = adapter._current_campaign_context_payload(_provider_input())  # noqa: SLF001
    preparation = payload["campaign_plan_preparation"]
    (act,) = preparation["prepared_acts"]
    assert act["id"] == "act-2"
    assert act["reference"] == {"source": "campaign_plan", "path": "structured/structure/act-2"}
    assert act["objective_references"] == [
        {"source": "campaign_plan", "path": "structured/objectives/evidence"}
    ]
    for private in (
        "An inline goal", "Private investigation ending", "Future secret", "Hidden future",
    ):
        assert private not in json.dumps(preparation)


def test_bounded_reference_excerpt_does_not_leave_a_dangling_heading() -> None:
    excerpt, complete = _bounded_reference_excerpt(
        "# Place\n\nOverview paragraph.\n\n## Optional material\n\nFuture detail.",
        limit=38,
    )

    assert excerpt == "# Place\n\nOverview paragraph."
    assert complete is False


def test_bounded_markdown_section_returns_only_the_named_section() -> None:
    selected = _bounded_markdown_section(
        ("# Place\n\nMetadata.\n\n## Overview\n\nImmediate texture.\n\n## Hooks\n\nPrivate lead."),
        heading="Overview",
        limit=100,
    )

    assert selected == ("Immediate texture.", True)


def test_opening_gets_bounded_starting_location_context_and_later_turns_do_not() -> None:
    plan = {
        "bindings": {
            "campaign_setting": {
                "id": "pneuma.severed-lands",
                "version": "1.0.0",
                "content_sha256": "a" * 64,
            }
        },
        "starting_location": {
            "id": "verdant-margin",
            "path": "locations/verdant-margin.md",
        },
    }
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()),
        _ScriptedProvider([]),
        owner_id="owner-provider",
        campaign_plan=plan,
    )

    opening = adapter._current_campaign_context_payload(  # noqa: SLF001
        _provider_input(),
        include_opening_direction=True,
    )
    later = adapter._current_campaign_context_payload(_provider_input())  # noqa: SLF001

    assert opening["campaign_plan_preparation"]["starting_location"] == {
        "id": "verdant-margin",
        "path": "locations/verdant-margin.md",
    }
    assert opening["campaign_setting_references"] == [
        {
            "package": {
                "id": "pneuma.severed-lands",
                "version": "1.0.0",
                "content_sha256": "a" * 64,
            },
            "path": "locations/verdant-margin.md",
            "content": "The Verdant Margin grows quickly around a stable Warden stone.",
            "content_complete": True,
            "document_complete": False,
            "purpose": "starting-location overview for opening texture",
            "interpretation": (
                "Reusable Setting baseline, not current Campaign State. Examples describe what "
                "can occur; they do not establish a current object, route, event, or prior action."
            ),
            "reference": {
                "source": "campaign_setting",
                "path": "locations/verdant-margin.md#overview",
            },
            "additional_sections_discoverable": True,
            "selection": {
                "kind": "markdown_section",
                "heading": "Overview",
                "complete": True,
            },
        }
    ]
    assert later["campaign_setting_references"] == []


def test_opening_direction_remains_through_first_player_action_only() -> None:
    source = _provider_input(user_text="I look around.")
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()),
        _ScriptedProvider([]),
        owner_id="owner-provider",
        campaign_plan={
            "id": "test-plan",
            "title": "Test Plan",
            "opening_direction": "Keep the first exchange quiet and without a lead.",
        },
    )

    first_messages = adapter._messages(  # noqa: SLF001
        source,
        canonical_player_narration=(),
    )
    first_preparation = next(
        str(message["content"])
        for message in first_messages
        if str(message.get("content", "")).startswith("SYLVIA_GM_CAMPAIGN_PREPARATION")
    )

    prior = SessionMessage(
        session_id=source.session.session_id,
        sequence=1,
        message_id="message-prior",
        kind=SessionMessageKind.USER,
        content={"text": "I wait.", "attempt_id": "attempt-prior"},
    )
    current = replace(source.history.messages[0], sequence=2)
    later = SessionProviderInput(
        session=source.session,
        history=SessionContext(session=source.session, messages=(prior, current)),
        catalog=source.catalog,
        control=source.control,
    )
    later_messages = adapter._messages(  # noqa: SLF001
        later,
        canonical_player_narration=(),
    )
    later_preparation = next(
        str(message["content"])
        for message in later_messages
        if str(message.get("content", "")).startswith("SYLVIA_GM_CAMPAIGN_PREPARATION")
    )

    assert '"opening_direction":"Keep the first exchange quiet and without a lead."' in (
        first_preparation
    )
    assert '"opening_direction"' not in later_preparation


def test_revealed_secret_fact_enters_player_and_character_knowledge() -> None:
    owner = _OwnerProjection(
        continuity=CampaignContinuityView(
            reveals=owned_payload({"rift-is-one-way": ["player", "v"]})
        )
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        _ScriptedProvider([]),
        owner_id="owner-provider",
        campaign_plan={
            "secrets": [
                {
                    "id": "rift-is-one-way",
                    "fact": "The Rift is one-way.",
                }
            ]
        },
    )

    payload = adapter._current_campaign_context_payload(  # noqa: SLF001
        _provider_input()
    )

    assert payload["PLAYER_KNOWN"]["revealed_facts"] == [
        {"secret_id": "rift-is-one-way", "fact": "The Rift is one-way."}
    ]
    assert payload["GM_PRIVATE"]["character_knowledge"]["v"] == ["The Rift is one-way."]
    assert "facts_and_entities" not in payload["campaign_state"]


def test_attack_narration_facts_preserve_the_exact_committed_mechanic() -> None:
    facts = _mechanics_narration_facts(
        "resolve_combat_attack",
        {
            "status": "committed",
            "context": {
                "_attack_outcome": "hit",
                "_attack_roll": {"total": 13, "modifier": 5},
                "_attack_adjusted_total": 18,
                "_effective_target_armor_class": 13,
                "_weapon": "longsword",
                "_weapon_damage_type": "slashing",
                "_target_id": "rift-scarred-hound",
                "target": {"hp": 6},
            },
        },
        {"rift-scarred-hound": {"hp": 6}},
        {"rift-scarred-hound": {"hp": 12}},
    )
    assert facts == {
        "tool": "resolve_combat_attack",
        "current_entities": {"rift-scarred-hound": {"hp": 6}},
        "status": "committed",
        "attack_outcome": "hit",
        "attack_roll": {"total": 13, "modifier": 5},
        "attack_adjusted_total": 18,
        "effective_target_armor_class": 13,
        "weapon": "longsword",
        "weapon_damage_type": "slashing",
        "target_id": "rift-scarred-hound",
        "target_hit_points_before": 12,
        "target_hit_points_after": 6,
        "hit_point_change": -6,
    }


def test_combat_narration_facts_preserve_player_facing_initiative_order() -> None:
    facts = _mechanics_narration_facts(
        "start_combat_encounter_entities",
        {
            "status": "committed",
            "context": {
                "_initiative_first_total": 11,
                "_initiative_second_total": 17,
            },
        },
        {
            "daniel": {"hp": 10},
            "riftwolf": {"hp": 9},
        },
        {
            "daniel": {"hp": 10},
            "riftwolf": {"hp": 9},
        },
        parameters={"first_actor": "daniel", "second_actor": "riftwolf"},
        current_combat={
            "order_first": "riftwolf",
            "order_second": "daniel",
            "initiative_first_actor": "daniel",
            "initiative_first_total": 11,
            "initiative_second_actor": "riftwolf",
            "initiative_second_total": 17,
            "current_actor": "riftwolf",
        },
        entity_display_names={
            "daniel": "Daniel",
            "riftwolf": "the Riftwolf",
        },
    )

    assert facts["initiative_order"] == [
        {"actor_id": "riftwolf", "name": "the Riftwolf", "total": 17},
        {"actor_id": "daniel", "name": "Daniel", "total": 11},
    ]


def test_attack_narration_facts_include_authoritative_current_actor() -> None:
    facts = _mechanics_narration_facts(
        "resolve_combat_attack",
        {"status": "committed", "context": {"_attack_outcome": "hit"}},
        {"daniel": {"name": "Daniel", "hp": 10}},
        {"daniel": {"name": "Daniel", "hp": 10}},
        current_combat={"current_actor": "daniel"},
    )

    assert facts["current_actor_id"] == "daniel"
    assert facts["current_actor_name"] == "Daniel"


def test_recent_narration_replay_passes_through_after_one_provider_inference() -> None:
    opening = (
        "The humid air of the Verdant Margin hangs thick and sweet while towering trees "
        "press in on every side. Vines thicker than a man's torso snake across paths that "
        "seem to shift behind you. A fading Veil distortion remains visible ahead of Daniel "
        "and Chloe, marking the point where they arrived in this strange forest."
    )
    source = _provider_input(user_text="Daniel ends his turn.")
    history = SessionContext(
        session=source.session,
        messages=(
            SessionMessage(
                session_id=source.session.session_id,
                sequence=1,
                message_id="message-earlier-action",
                kind=SessionMessageKind.USER,
                content={"text": "Daniel ends his turn."},
            ),
            SessionMessage(
                session_id=source.session.session_id,
                sequence=2,
                message_id="message-opening",
                kind=SessionMessageKind.TALK,
                content={"text": opening},
            ),
            SessionMessage(
                session_id=source.session.session_id,
                sequence=3,
                message_id="message-current-action",
                kind=SessionMessageKind.USER,
                content={"text": "Daniel ends his turn."},
            ),
        ),
    )
    provider_input = SessionProviderInput(
        session=source.session,
        history=history,
        catalog=(),
        control=source.control,
    )
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content=opening, finish_reason="stop")],
            [
                ProviderStreamChunk(
                    content="The wolf finishes circling; Daniel may act.",
                    finish_reason="stop",
                )
            ],
        ]
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()),
        provider,
        owner_id="owner-provider",
    )

    events = tuple(adapter.for_turn("turn-provider")(provider_input))

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [opening]
    assert len(provider.calls) == 1


def test_undeclared_player_behavior_passes_through_after_one_provider_inference() -> None:
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    content="Mara Venn's hand rests on her weapon as she walks.",
                    finish_reason="stop",
                )
            ],
            [
                ProviderStreamChunk(
                    content="Chloe points out fresh tracks beside the path. What does Mara do?",
                    finish_reason="stop",
                )
            ],
        ]
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()),
        provider,
        owner_id="owner-provider",
    )

    events = tuple(
        adapter.for_turn("turn-provider")(
            _provider_input(user_text="I walk beside Chloe and search the path ahead.")
        )
    )

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        "Mara Venn's hand rests on her weapon as she walks."
    ]
    assert len(provider.calls) == 1


def test_gm_companion_handoff_passes_through_after_one_provider_inference() -> None:
    participants = (
        SimpleNamespace(
            participant_id="rift-player-character",
            kind="player_character",
            data={"name": "Daniel", "party_role": "player_character"},
        ),
        SimpleNamespace(
            participant_id="rift-companion-chloe",
            kind="player_character",
            data={"name": "Chloe", "party_role": "companion"},
        ),
    )
    provider = _ScriptedProvider(
        [
            [ProviderStreamChunk(content="What does Chloe do?", finish_reason="stop")],
            [
                ProviderStreamChunk(
                    content=("Chloe raises her bow and watches the thicket. What does Daniel do?"),
                    finish_reason="stop",
                )
            ],
        ]
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection(participants=participants)),
        provider,
        owner_id="owner-provider",
    )

    events = tuple(
        adapter.for_turn("turn-provider")(
            _provider_input(user_text="I wait while Chloe decides for herself.")
        )
    )

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        "What does Chloe do?"
    ]
    assert len(provider.calls) == 1


def test_completed_dialogue_is_data_and_only_the_current_action_is_a_chat_turn() -> None:
    source = _provider_input(user_text="Daniel follows Chloe toward shelter.")
    history = SessionContext(
        session=source.session,
        messages=(
            SessionMessage(
                session_id=source.session.session_id,
                sequence=1,
                message_id="message-old-action",
                kind=SessionMessageKind.USER,
                content={"text": "Daniel asks Chloe about the shimmer."},
            ),
            SessionMessage(
                session_id=source.session.session_id,
                sequence=2,
                message_id="message-old-reply",
                kind=SessionMessageKind.TALK,
                content={"text": "Chloe says the shimmer resembles a Veil bleed-through."},
            ),
            SessionMessage(
                session_id=source.session.session_id,
                sequence=3,
                message_id="message-provider",
                kind=SessionMessageKind.USER,
                content={"text": "Daniel follows Chloe toward shelter."},
            ),
        ),
    )
    provider_input = SessionProviderInput(
        session=source.session,
        history=history,
        catalog=source.catalog,
        control=source.control,
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()),
        _ScriptedProvider([]),
        owner_id="owner-provider",
    )

    messages = adapter._messages(provider_input)  # noqa: SLF001

    assert messages[-1] == {
        "role": "user",
        "content": "Daniel follows Chloe toward shelter.",
    }
    dialogue = [message for message in messages if message.get("role") in {"user", "assistant"}]
    assert dialogue == [
        {"role": "user", "content": "Daniel asks Chloe about the shimmer."},
        {
            "role": "assistant",
            "content": "Chloe says the shimmer resembles a Veil bleed-through.",
        },
        messages[-1],
    ]
    assert "SYLVIA_GM_RECENT_PLAY" not in str(messages)


def test_exact_full_name_retrieves_only_the_matching_character_file() -> None:
    participants = (
        SimpleNamespace(
            participant_id="rift-player-daniel",
            kind="player_character",
            data={"name": "Daniel", "party_role": "player_character"},
        ),
        SimpleNamespace(
            participant_id="rift-companion-chloe",
            kind="player_character",
            data={"name": "Chloe", "party_role": "companion"},
        ),
    )
    reads: list[tuple[str, str]] = []

    def read_reference(source: str, path: str) -> dict[str, object]:
        reads.append((source, path))
        is_chloe = path == "rift-companion-chloe"
        return {
            "source": source,
            "path": path,
            "campaign_id": "campaign-provider",
            "content": json.dumps(
                {
                    "participant_id": path,
                    "source_character_file_id": (
                        "participant-source-chloe" if is_chloe else "participant-source-daniel"
                    ),
                    "selector": {
                        "summary": "A precise archer.",
                        "meaning": (
                            "Short setup-catalog aid, not a substitute for authored biography."
                        ),
                    },
                    "profile": {
                        "status": "authored",
                        "authored_text": (
                            "Chloe grew up in Bellweather and has never seen the sea."
                            if is_chloe
                            else "Daniel keeps a careful journal."
                        ),
                    },
                    "ruleset": {
                        "schema_kind": "player_character",
                        "starting_data": {
                            "name": "Chloe" if is_chloe else "Daniel",
                            "party_role": "companion" if is_chloe else "player_character",
                        },
                        "meaning": (
                            "Ruleset-compatible starting data. Opaque identifiers "
                            "establish only their exact stored values, not unwritten prose "
                            "biography."
                        ),
                    },
                }
            ),
            "precedence": "Campaign State overrides Character File values.",
        }

    source = _provider_input(user_text="I ask Chloe where she grew up.")
    provider_input = replace(source, reference_reader=read_reference)
    messages = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection(participants=participants)),
        _ScriptedProvider([]),
        owner_id="owner-provider",
    )._messages(provider_input)  # noqa: SLF001

    assert reads == [("participant", "rift-companion-chloe")]
    retrieved = next(
        message
        for message in messages
        if str(message.get("content", "")).startswith("SYLVIA_GM_RELEVANT_CHARACTER_FILES")
    )
    assert "Bellweather" in str(retrieved["content"])
    payload = json.loads(str(retrieved["content"]).split("\n", 1)[1])
    references = {item["participant_id"]: item for item in payload["references"]}
    assert set(references) == {"rift-companion-chloe"}
    assert references["rift-companion-chloe"]["directly_referenced_by_player"] is True
    assert messages.index(retrieved) < next(
        index
        for index, message in enumerate(messages)
        if str(message.get("content", "")).startswith("SYLVIA_GM_FRESH_CAMPAIGN_STATE")
    )
    assert messages[-1] == {"role": "user", "content": "I ask Chloe where she grew up."}


def test_unique_given_name_retrieves_the_matching_character_file() -> None:
    participants = (
        SimpleNamespace(
            participant_id="rift-player-mira",
            kind="player_character",
            data={"name": "Mira Sol", "party_role": "player_character"},
        ),
        SimpleNamespace(
            participant_id="rift-companion-tamsin",
            kind="player_character",
            data={"name": "Tamsin Vale", "party_role": "companion"},
        ),
    )
    reads: list[tuple[str, str]] = []

    def read_reference(source: str, path: str) -> dict[str, object]:
        reads.append((source, path))
        is_tamsin = path == "rift-companion-tamsin"
        return {
            "source": source,
            "path": path,
            "campaign_id": "campaign-provider",
            "content": json.dumps(
                {
                    "participant_id": path,
                    "profile": {
                        "status": "authored",
                        "authored_text": (
                            "Tamsin grew up beside the Bellweather locks."
                            if is_tamsin
                            else "Mira worked as an archive runner."
                        ),
                    },
                }
            ),
            "precedence": "Campaign State overrides Character File values.",
        }

    source = _provider_input(user_text="I ask Tamsin what she thinks.")
    messages = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection(participants=participants)),
        _ScriptedProvider([]),
        owner_id="owner-provider",
    )._messages(replace(source, reference_reader=read_reference))  # noqa: SLF001

    assert reads == [("participant", "rift-companion-tamsin")]
    retrieved = next(
        message
        for message in messages
        if str(message.get("content", "")).startswith("SYLVIA_GM_RELEVANT_CHARACTER_FILES")
    )
    assert "Bellweather" in str(retrieved["content"])
    payload = json.loads(str(retrieved["content"]).split("\n", 1)[1])
    references = {item["participant_id"]: item for item in payload["references"]}
    assert set(references) == {"rift-companion-tamsin"}
    assert references["rift-companion-tamsin"]["matched_name"] == "Tamsin"
    assert references["rift-companion-tamsin"]["directly_referenced_by_player"] is True


def test_generic_action_does_not_preload_unmentioned_character_files() -> None:
    participants = (
        SimpleNamespace(
            participant_id="rift-player-mira",
            kind="player_character",
            data={"name": "Mira Sol", "party_role": "player_character"},
        ),
        SimpleNamespace(
            participant_id="rift-companion-tamsin",
            kind="player_character",
            data={"name": "Tamsin Vale", "party_role": "companion"},
        ),
    )
    reads: list[tuple[str, str]] = []

    def read_reference(source: str, path: str) -> dict[str, object]:
        reads.append((source, path))
        return {
            "source": source,
            "path": path,
            "campaign_id": "campaign-provider",
            "content": json.dumps(
                {
                    "participant_id": path,
                    "profile": {
                        "status": "authored",
                        "authored_text": f"Authored profile for {path}.",
                    },
                }
            ),
            "precedence": "Campaign State overrides Character File values.",
        }

    source = _provider_input(user_text="I look around.")
    messages = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection(participants=participants)),
        _ScriptedProvider([]),
        owner_id="owner-provider",
    )._messages(replace(source, reference_reader=read_reference))  # noqa: SLF001

    assert reads == []
    assert not any(
        str(message.get("content", "")).startswith("SYLVIA_GM_RELEVANT_CHARACTER_FILES")
        for message in messages
    )


def test_shared_given_name_does_not_guess_between_character_files() -> None:
    participants = (
        SimpleNamespace(
            participant_id="rift-companion-tamsin-vale",
            kind="player_character",
            data={"name": "Tamsin Vale", "party_role": "companion"},
        ),
        SimpleNamespace(
            participant_id="rift-companion-tamsin-reed",
            kind="player_character",
            data={"name": "Tamsin Reed", "party_role": "companion"},
        ),
    )
    reads: list[tuple[str, str]] = []
    source = _provider_input(user_text="I ask Tamsin what she thinks.")

    def read_reference(source: str, path: str) -> dict[str, object]:
        reads.append((source, path))
        return {
            "source": source,
            "path": path,
            "campaign_id": "campaign-provider",
            "content": json.dumps(
                {
                    "participant_id": path,
                    "profile": {
                        "status": "authored",
                        "authored_text": f"Authored profile for {path}.",
                    },
                }
            ),
            "precedence": "Campaign State overrides Character File values.",
        }

    messages = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection(participants=participants)),
        _ScriptedProvider([]),
        owner_id="owner-provider",
    )._messages(  # noqa: SLF001
        replace(source, reference_reader=read_reference)
    )

    assert reads == []
    assert not any(
        str(message.get("content", "")).startswith("SYLVIA_GM_RELEVANT_CHARACTER_FILES")
        for message in messages
    )


def test_active_party_retrieval_does_not_mark_a_longer_word_as_a_name_match() -> None:
    participants = (
        SimpleNamespace(
            participant_id="rift-companion-chloe",
            kind="player_character",
            data={"name": "Chloe", "party_role": "companion"},
        ),
    )
    reads: list[tuple[str, str]] = []
    source = _provider_input(user_text="I ask Chloette where she grew up.")

    def read_reference(source: str, path: str) -> dict[str, object]:
        reads.append((source, path))
        return {
            "source": source,
            "path": path,
            "campaign_id": "campaign-provider",
            "content": json.dumps(
                {
                    "participant_id": path,
                    "profile": {
                        "status": "authored",
                        "authored_text": "Chloe's authored profile.",
                    },
                }
            ),
            "precedence": "Campaign State overrides Character File values.",
        }

    messages = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection(participants=participants)),
        _ScriptedProvider([]),
        owner_id="owner-provider",
    )._messages(  # noqa: SLF001
        replace(source, reference_reader=read_reference)
    )

    assert reads == []
    assert not any(
        str(message.get("content", "")).startswith("SYLVIA_GM_RELEVANT_CHARACTER_FILES")
        for message in messages
    )


def test_sparse_character_file_context_defines_its_missing_biography() -> None:
    participant_id = "rift-companion-chloe"
    participant = SimpleNamespace(
        participant_id=participant_id,
        kind="player_character",
        data={"name": "Chloe", "party_role": "companion"},
    )
    source = _provider_input(user_text="I ask Chloe about her childhood.")
    messages = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection(participants=(participant,))),
        _ScriptedProvider([]),
        owner_id="owner-provider",
    )._messages(  # noqa: SLF001
        replace(
            source,
            reference_reader=lambda source_name, path: {
                "source": source_name,
                "path": path,
                "campaign_id": "campaign-provider",
                "content": json.dumps(
                    {
                        "participant_id": participant_id,
                        "selector": {
                            "summary": "A precise archer.",
                            "meaning": (
                                "Short setup-catalog aid, not a substitute for authored biography."
                            ),
                        },
                        "profile": {"status": "sparse", "authored_text": None},
                        "ruleset": {
                            "schema_kind": "player_character",
                            "starting_data": {
                                "name": "Chloe",
                                "background": "riftborn_archer",
                            },
                            "meaning": (
                                "Ruleset-compatible starting data. Opaque identifiers establish "
                                "only their exact stored values, not unwritten prose biography."
                            ),
                        },
                    }
                ),
                "precedence": "Campaign State overrides Character File values.",
            },
        )
    )

    relevant = next(
        str(message["content"])
        for message in messages
        if str(message.get("content", "")).startswith("SYLVIA_GM_RELEVANT_CHARACTER_FILES")
    )
    assert "Sparse means no biography was authored" in relevant
    assert "not an in-world condition" in relevant
    assert "selector is only a setup aid" in relevant
    assert "supplies mechanics rather than unwritten past" in relevant
    assert "present-tense choice" in relevant


def test_changed_player_action_never_releases_an_exact_previous_gm_response() -> None:
    previous_action = "I refuse Chloe's request."
    older_response = "Chloe asks you to reconsider before you leave the clearing."
    previous_response = "Chloe watches you choose between the northern and southern trails."
    current_action = "I leave the clearing and head south."
    corrected_response = "You leave Chloe at the standing stone and head south beneath the trees."
    source = _provider_input(user_text=current_action)
    history = SessionContext(
        session=source.session,
        messages=(
            SessionMessage(
                session_id=source.session.session_id,
                sequence=1,
                message_id="previous-action",
                kind=SessionMessageKind.USER,
                content={"text": previous_action},
            ),
            SessionMessage(
                session_id=source.session.session_id,
                sequence=2,
                message_id="previous-response",
                kind=SessionMessageKind.TALK,
                content={"text": older_response},
            ),
            SessionMessage(
                session_id=source.session.session_id,
                sequence=3,
                message_id="current-action",
                kind=SessionMessageKind.USER,
                content={"text": current_action},
            ),
        ),
    )

    class ReplayThenResolveProvider(_ScriptedProvider):
        def __init__(self) -> None:
            super().__init__([])
            self.replay_was_corrected = False

        async def generate_stream(
            self,
            messages: list[dict[str, Any]],
            tools: list[dict[str, Any]] | None = None,
            timeout: float = 120.0,
            **kwargs: object,
        ) -> AsyncIterator[ProviderStreamChunk]:
            del timeout
            self.calls.append((messages.copy(), tools))
            self.request_kwargs.append(dict(kwargs))
            if len(self.calls) == 1:
                yield ProviderStreamChunk(content=previous_response, finish_reason="stop")
                return
            control = str(messages[-1].get("content", ""))
            assert control.startswith("SYLVIA_GM_CURRENT_ACTION_RESPONSE_CORRECTION")
            self.replay_was_corrected = True
            yield ProviderStreamChunk(content=corrected_response, finish_reason="stop")

    provider = ReplayThenResolveProvider()
    owner = _OwnerProjection()
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TALK,
            turn_id="previous-turn",
            action_id=None,
            safe_payload={"text": previous_response},
            visibility=EventVisibility.PLAYER_SAFE,
            attempt_id="previous-attempt",
            attempt_ordinal=1,
        ),
    )
    provider_input = SessionProviderInput(
        session=source.session,
        history=history,
        catalog=(),
        control=source.control,
    )

    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        corrected_response
    ]
    repeated = next(
        event.evidence
        for event in events
        if event.evidence is not None
        and event.evidence.get("kind") == "provider_repeated_player_facing_response"
    )
    assert (
        repeated["response_sha256"] == hashlib.sha256(previous_response.encode("utf-8")).hexdigest()
    )
    assert repeated["response_characters"] == len(previous_response)
    assert previous_response not in json.dumps(repeated)
    assert str(provider.calls[1][0][-1]["content"]).startswith(
        "SYLVIA_GM_CURRENT_ACTION_RESPONSE_CORRECTION"
    )
    assert provider.calls[1][0][-2] == {
        "role": "assistant",
        "content": previous_response,
    }


def test_canonical_narration_pairs_user_only_history_by_attempt() -> None:
    source = _provider_input(user_text="I choose the eastern trail.")
    owner = _OwnerProjection()
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TALK,
            turn_id="turn-provider",
            action_id=None,
            safe_payload={"text": "Chloe points out two possible routes."},
            visibility=EventVisibility.PLAYER_SAFE,
            attempt_id="attempt-one",
            attempt_ordinal=1,
        ),
        CanonicalEvent(
            sequence=2,
            event_type=CanonicalEventType.TALK,
            turn_id="turn-provider",
            action_id=None,
            safe_payload={"text": "The eastern trail descends toward water."},
            visibility=EventVisibility.PLAYER_SAFE,
            attempt_id="attempt-two",
            attempt_ordinal=1,
        ),
    )
    history = SessionContext(
        session=source.session,
        messages=(
            SessionMessage(
                session_id=source.session.session_id,
                sequence=1,
                message_id="player-one",
                kind=SessionMessageKind.USER,
                content={"text": "I ask Chloe about routes.", "attempt_id": "attempt-one"},
            ),
            SessionMessage(
                session_id=source.session.session_id,
                sequence=2,
                message_id="player-two",
                kind=SessionMessageKind.USER,
                content={"text": "I listen for water.", "attempt_id": "attempt-two"},
            ),
            SessionMessage(
                session_id=source.session.session_id,
                sequence=3,
                message_id="message-provider",
                kind=SessionMessageKind.USER,
                content={
                    "text": "I choose the eastern trail.",
                    "attempt_id": "attempt-three",
                },
            ),
        ),
    )
    messages = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        _ScriptedProvider([]),
        owner_id="owner-provider",
    )._messages(  # noqa: SLF001
        SessionProviderInput(
            session=source.session,
            history=history,
            catalog=source.catalog,
            control=source.control,
        )
    )

    dialogue = [message for message in messages if message.get("role") in {"user", "assistant"}]
    assert dialogue == [
        {"role": "user", "content": "I ask Chloe about routes."},
        {"role": "assistant", "content": "Chloe points out two possible routes."},
        {"role": "user", "content": "I listen for water."},
        {"role": "assistant", "content": "The eastern trail descends toward water."},
        {"role": "user", "content": "I choose the eastern trail."},
    ]


def test_ruleset_guide_follows_campaign_data_and_precedes_the_current_action() -> None:
    source = _provider_input(user_text="What does Daniel do?")
    provider_input = SessionProviderInput(
        session=source.session,
        history=SessionContext(
            session=source.session,
            messages=(
                SessionMessage(
                    session_id=source.session.session_id,
                    sequence=1,
                    message_id="prior-talk",
                    kind=SessionMessageKind.TALK,
                    content={"text": "The wolf circles through the brush."},
                ),
                SessionMessage(
                    session_id=source.session.session_id,
                    sequence=2,
                    message_id="message-provider",
                    kind=SessionMessageKind.USER,
                    content={"text": "What does Daniel do?"},
                ),
            ),
        ),
        catalog=source.catalog,
        control=source.control,
    )
    owner = _OwnerProjection(ruleset_guide="D&D has distinct out-of-combat and in-combat modes.")
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        _ScriptedProvider([]),
        owner_id="owner-provider",
        campaign_plan={
            "id": "rift-awakening",
            "title": "The Rift Awakening",
            "bindings": {"ruleset": {"id": "sylvia.srd-lite.bg3"}},
            "gm_guidance": {"tone": "grounded"},
            "structure": [],
        },
    )

    messages = adapter._messages(provider_input)  # noqa: SLF001
    labels = [str(message.get("content", "")).split(" ", 1)[0] for message in messages]

    assert labels.index("SYLVIA_GM_CAMPAIGN_PREPARATION") < labels.index(
        "SYLVIA_GM_FRESH_CAMPAIGN_STATE"
    )
    assert labels.index("SYLVIA_GM_FRESH_CAMPAIGN_STATE") < labels.index(
        "SYLVIA_GM_RULESET_OPERATING_GUIDE"
    )
    assert {"role": "assistant", "content": "The wolf circles through the brush."} in messages
    assert messages[-4]["content"].startswith("SYLVIA_GM_RULESET_OPERATING_GUIDE")
    assert messages[-3]["content"].startswith("SYLVIA_GM_RESPONSE_FORMAT")
    assert messages[-2]["content"].startswith("SYLVIA_GM_CURRENT_PLAYER_MESSAGE")
    assert messages[-1] == {"role": "user", "content": "What does Daniel do?"}


def test_gm_history_reaches_working_context_without_message_clipping_or_duplicate_results() -> None:
    source = _provider_input(user_text="I wait.")
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()),
        _ScriptedProvider([]),
        owner_id="owner-provider",
    )
    measurements: list[int] = []
    for turn_count in (1, 8, 32, 80):
        messages: list[SessionMessage] = []
        sequence = 0
        for turn in range(turn_count):
            sequence += 1
            messages.append(
                SessionMessage(
                    session_id=source.session.session_id,
                    sequence=sequence,
                    message_id=f"player-{turn}",
                    kind=SessionMessageKind.USER,
                    content={"text": f"Turn {turn}: I wait. " + ("quiet " * 220)},
                )
            )
            sequence += 1
            messages.append(
                SessionMessage(
                    session_id=source.session.session_id,
                    sequence=sequence,
                    message_id=f"talk-{turn}",
                    kind=SessionMessageKind.TALK,
                    content={"text": f"Turn {turn}: the scene advances. " + ("forest " * 220)},
                )
            )
        sequence += 1
        messages.append(
            SessionMessage(
                session_id=source.session.session_id,
                sequence=sequence,
                message_id="canonical-mechanic-result",
                kind=SessionMessageKind.CANONICAL_REFERENCE,
                content={
                    "event_type": "tool_result",
                    "visibility": "player_safe",
                    "safe_payload": {"result_marker": "MECHANIC-RESULT-ONCE"},
                },
                canonical_sequence=42,
            )
        )
        sequence += 1
        messages.append(
            SessionMessage(
                session_id=source.session.session_id,
                sequence=sequence,
                message_id="current-player-action",
                kind=SessionMessageKind.USER,
                content={"text": "I continue along the path."},
            )
        )
        provider_input = SessionProviderInput(
            session=source.session,
            history=SessionContext(session=source.session, messages=tuple(messages)),
            catalog=source.catalog,
            control=source.control,
        )

        request_messages = adapter._messages(provider_input)  # noqa: SLF001
        serialized = json.dumps(request_messages, ensure_ascii=False)
        measurements.append(len(serialized))
        recent_play = json.dumps(
            [
                message
                for message in request_messages
                if message.get("role") in {"user", "assistant"} and message != request_messages[-1]
            ],
            ensure_ascii=False,
        )
        if turn_count <= 8:
            assert "Turn 0: I wait." in recent_play
        else:
            assert "Turn 0: I wait." in recent_play
        assert f"Turn {turn_count - 1}: the scene advances." in recent_play
        assert (
            sum(message.get("role") in {"user", "assistant"} for message in request_messages[:-1])
            == turn_count * 2
        )
        assert serialized.count("MECHANIC-RESULT-ONCE") == 0
        assert "provisional" not in serialized

    assert measurements[0] < measurements[1] < measurements[2]
    # Token-aware working-context projection owns shortening; source assembly keeps history.
    assert measurements[-1] > measurements[-2]


def test_canonical_gm_checkpoint_survives_recent_reply_window_rollover(tmp_path: Path) -> None:
    from sylvia_gm.agent._working_context import WorkingContextProvider
    from tests.test_working_context import Model

    source = _provider_input()
    owner = _OwnerProjection()
    provider = Model(responses=[ProviderResponse(content="Saved historical decisions.")] * 20)
    adapter = ProviderSessionAdapter(cast(AuthorityOwner, owner), provider, owner_id="owner")
    records: list[dict[str, Any]] = []
    database = tmp_path / "checkpoints.db"
    with AuthorityOwner.open(database, "checkpoint-owner") as saved_owner:
        saved_owner.create_campaign(CampaignCreate(
            display_name="Synthetic checkpoint campaign", writer=WriterRole.DM,
            package_lock=PackageLock(entries=()), turn_id="turn", campaign_id="campaign-provider",
        ))
        saved_owner.create_agent_session(
            campaign_id="campaign-provider", role=WriterRole.DM, provider_id="test",
            model_id="test", session_id=source.session.session_id,
        )

    async def save(record: dict[str, Any]) -> None:
        with AuthorityOwner.open(database, "checkpoint-owner") as saved_owner:
            saved_owner.append_agent_message(
                session_id=source.session.session_id,
                message_id=f"checkpoint-{record['checkpoint_id']}",
                kind=SessionMessageKind.EVIDENCE, content=record,
            )
        records.append(record)

    def assemble(turns: int) -> list[dict[str, Any]]:
        owner.events = tuple(
            SimpleNamespace(
                event_type=CanonicalEventType.TALK, visibility=EventVisibility.PLAYER_SAFE,
                sequence=index + 1, turn_id="turn", attempt_id=f"attempt-{index}",
                safe_payload={"text": f"Reply {index}: the lantern stays lit."},
            )
            for index in range(turns)
        )
        messages = tuple(
            SessionMessage(
                session_id=source.session.session_id, sequence=index + 1,
                message_id=f"player-{index}", kind=SessionMessageKind.USER,
                content={"text": f"Action {index}: inspect the lantern.",
                         "attempt_id": f"attempt-{index}"},
            )
            for index in range(turns + 1)
        )
        return adapter._messages(replace(
            source, history=SessionContext(session=source.session, messages=messages),
        ))

    before = assemble(24)
    working = WorkingContextProvider(provider, save=save)
    asyncio.run(working.prepare(before, force=True))
    assert records
    calls_after_compaction = len(provider.calls)
    owner.state = {"current_marker": "Fresh authoritative state after rollover"}
    after = assemble(25)
    with AuthorityOwner.open(database, "reopened-owner") as saved_owner:
        saved_records = [
            message.content
            for message in saved_owner.session_context(source.session.session_id).messages
        ]
    restored = WorkingContextProvider(provider, records=saved_records, save=save)
    projected = asyncio.run(restored.prepare(after))
    assert any("SYLVIA_CONTINUATION_NOTE" in str(item.get("content")) for item in projected)
    assert len(provider.calls) == calls_after_compaction
    assert len(projected) < len(after)
    assert "Fresh authoritative state after rollover" in json.dumps(projected)
    assert "Action 25" in json.dumps(projected)
    assert "Reply 24" in json.dumps(projected)
    assert "Action 2:" not in json.dumps(projected)
    assert "Action 2:" in restored.read_history({"source_id": "current", "limit": 16000})["content"]

    asyncio.run(restored.prepare(after, force=True))
    assert records[-1]["previous_checkpoint_id"] == records[0]["checkpoint_id"]
    assert "Saved historical decisions." in json.dumps(provider.calls[calls_after_compaction:])
    altered = [dict(message) for message in after]
    historical = next(message for message in altered if message.get("role") == "user")
    historical["content"] = "Changed historical input"
    assert not any(
        "SYLVIA_CONTINUATION_NOTE" in str(item.get("content"))
        for item in restored.project(altered)
    )


def test_d20_narration_facts_preserve_roll_modifier_total_and_outcome() -> None:
    facts = _mechanics_narration_facts(
        "d20_test",
        {
            "status": "committed",
            "context": {
                "_test_kind": "ability_check",
                "_ability_modifier": 2,
                "_proficiency_bonus": 2,
                "_proficiency_modifier": 2,
                "_modifier": 4,
                "_d20_test": {
                    "sides": 20,
                    "count": 1,
                    "results": [16],
                    "total": 16,
                    "modifier": 4,
                },
                "_adjusted_total": 20,
                "_outcome": "success",
                "_effective_advantage": False,
                "_effective_disadvantage": False,
                "_automatic_failure_reason": "none",
                "_exhaustion_penalty": 0,
                "_guidance_bonus": 0,
                "_lasting_d20_modifier": 0,
            },
        },
        {},
        {},
        {
            "ability": "intelligence",
            "skill": "arcana",
            "kind": "ability_check",
            "target_number": 12,
        },
    )

    assert facts == {
        "tool": "d20_test",
        "current_entities": {},
        "status": "committed",
        "ability": "intelligence",
        "skill": "arcana",
        "kind": "ability_check",
        "target_number": 12,
        "test_kind": "ability_check",
        "ability_modifier": 2,
        "proficiency_bonus": 2,
        "proficiency_modifier": 2,
        "modifier": 4,
        "d20_test": {
            "sides": 20,
            "count": 1,
            "results": [16],
            "total": 16,
            "modifier": 4,
        },
        "adjusted_total": 20,
        "outcome": "success",
        "effective_advantage": False,
        "effective_disadvantage": False,
        "automatic_failure_reason": "none",
        "exhaustion_penalty": 0,
        "guidance_bonus": 0,
        "lasting_d20_modifier": 0,
    }


def test_missing_model_tool_arguments_are_detected_for_explicit_rejection() -> None:
    assert _missing_required_arguments(
        {"actor": "rift-player-character"},
        {"required": ["actor", "actor_state", "attack_ability"]},
    ) == ("actor_state", "attack_ability")


def test_exact_participant_id_fills_its_matching_optional_state_reference() -> None:
    assert _normalize_state_references(
        {"actor": "rift-player-character", "kind": "ability_check"},
        {
            "type": "object",
            "properties": {
                "actor": {
                    "type": "string",
                    "x-sylvia-gm-reference": "participant",
                },
                "actor_state": {"type": "string"},
                "kind": {"type": "string"},
            },
            "required": ["kind"],
        },
        {"sylvia.srd-lite.bg3.entities": {"rift-player-character": {"hp": 10}}},
    ) == {
        "actor": "rift-player-character",
        "actor_state": "rift-player-character",
        "kind": "ability_check",
    }


def test_state_reference_is_not_guessed_from_a_nonparticipant_parameter() -> None:
    assert _normalize_state_references(
        {"target": "riftwolf"},
        {
            "type": "object",
            "properties": {
                "target": {"type": "string"},
                "target_state": {"type": "string"},
            },
        },
        {"sylvia.srd-lite.bg3.entities": {"riftwolf": {"hp": 9}}},
    ) == {"target": "riftwolf"}


def test_stream_adapter_cancels_blocked_async_provider_without_fallback() -> None:
    provider = _BlockingProvider([[]])
    control = ProviderControl()
    owner = _OwnerProjection()
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner),
        provider,
        owner_id="owner-provider",
    )

    def cancel() -> None:
        time.sleep(0.08)
        control.cancel()

    canceller = threading.Thread(target=cancel)
    canceller.start()
    started = time.monotonic()
    with pytest.raises(CommandRejected, match="cancelled"):
        tuple(adapter.for_turn("turn-provider")(_provider_input(control)))
    canceller.join()
    assert time.monotonic() - started < 2.0
    assert provider.cancelled.wait(1.0)
    assert len(provider.calls) == 1
    print("F4 PROVIDER ADAPTER", "stop=cancelled", "fallback=none")


@pytest.mark.parametrize("mode", ("turn", "retry"))
def test_stop_before_stream_admission_never_calls_configured_provider(
    mode: str,
) -> None:
    """A cancelled control must close the worker gate before generate_stream starts."""

    admission_waiting = threading.Event()
    release_admission = threading.Event()
    errors: list[BaseException] = []

    class PausedAdmissionControl(ProviderControl):
        def admit(self, start: Callable[[], object]) -> object:
            admission_waiting.set()
            assert release_admission.wait(5)
            return super().admit(start)

    provider = _ScriptedProvider(
        [[ProviderStreamChunk(content="must not start", finish_reason="stop")]]
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()),
        provider,
        owner_id="owner-provider",
    )
    control = PausedAdmissionControl()

    def run() -> None:
        try:
            if mode == "turn":
                tuple(adapter.for_turn("turn-provider")(_provider_input(control)))
            else:
                adapter.for_retry(
                    "synthetic-model",
                    provider_input=_provider_input(control),
                    control=control,
                )(
                    RetryNarrationInput(
                        campaign_id="campaign-provider",
                        interruption_id="interruption-provider",
                        retry_id="retry-provider-admission",
                        failed_attempt_id="attempt-provider",
                        canonical_high_water=7,
                        turn_id="turn-provider",
                        action_receipt=ActionReceipt(
                            campaign_id="campaign-provider",
                            action_id="action-provider-admission",
                            status=ActionStatus.COMMITTED,
                            result={"status": "committed"},
                            resulting_revision=4,
                        ),
                    ),
                    NarrationStream(lambda _channel, _token: None),
                )
        except BaseException as error:  # pragma: no cover - thread transfer
            errors.append(error)

    thread = threading.Thread(target=run)
    thread.start()
    assert admission_waiting.wait(5)
    control.cancel()
    release_admission.set()
    thread.join(5)
    assert not thread.is_alive()
    assert provider.calls == []
    assert len(errors) == 1
    assert isinstance(errors[0], CommandRejected)
    assert "cancelled" in str(errors[0]).lower()
    print(
        "F4 PROVIDER ADMISSION",
        f"mode={mode}",
        "pre-cancelled=true",
        "provider calls=0",
    )


@pytest.mark.parametrize("mode", ["user_retry", "control_reply", "control_retry", "redirect"])
def test_post_mechanics_recovery_continues_same_gm_through_campaign_file_before_talk(
    mode: str,
) -> None:
    source = _provider_input(user_text="Advance the clock by four.")
    if mode in {"control_reply", "control_retry"}:
        original = source.history.messages[0]
        response_input = replace(
            original, sequence=original.sequence + 1, message_id="player-control-response",
            kind=SessionMessageKind.EVIDENCE,
            content={
                "kind": "player_action_response", "player_action_id": "action-post-mechanics",
                "automatic_response": True,
            },
        )
        source = replace(source, history=replace(
            source.history, messages=(*source.history.messages, response_input),
        ))
    if mode == "redirect":
        original = source.history.messages[0]
        unfinished_intent = replace(
            original, sequence=2, message_id="old-mechanics-contract",
            kind=SessionMessageKind.EVIDENCE,
            content={"kind": "provider_mechanics_action_contract",
                     "basis_message_id": original.message_id,
                     "action_id": "action-post-mechanics",
                     "model_behavior": "leaves_player_intent_pending", "decision_owner": None},
        )
        replacement = replace(
            original, sequence=3, message_id="replacement-input",
            content={"text": "Talk instead", "ooc_text": "Keep it brief",
                     "redirect_interruption_id": "stopped-turn",
                     "redirected_from_message_id": original.message_id,
                     "canonical_start_sequence": 0},
        )
        source = replace(source, history=replace(
            source.history, messages=(*source.history.messages, unfinished_intent, replacement),
        ))
    update = {
        "facts": [
            {
                "fact_id": "clock-now-four",
                "category": "world_state",
                "text": "The clock is now at four.",
                "visibility": "gm_private",
                "known_by": ["gm"],
            }
        ]
    }
    narration = "The clock settles on four."
    response_blocks = [{"kind": "narration", "text": narration}]
    if mode == "redirect":
        response_blocks.append({"kind": "ooc", "text": "Kept brief."})
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(
                    tool_call_delta={
                        "index": 0,
                        "id": "call-post-mechanics-campaign-file",
                        "function": {
                            "name": "update_campaign_file",
                            "arguments": json.dumps(
                                {
                                    **update,
                                    "revised_player_facing_response": response_blocks,
                                }
                            ),
                        },
                    },
                    finish_reason="tool_calls",
                )
            ],
        ]
    )
    receipt = ActionReceipt(
        campaign_id="campaign-provider",
        action_id="action-post-mechanics",
        status=ActionStatus.COMMITTED,
        result={"before": 0, "after": 4},
        resulting_revision=1,
    )
    writes: list[dict[str, object]] = []
    campaign_file_tool = replace(
        _campaign_file_tool(allow_unchanged=True),
        description=_campaign_file_update_description(),
    )
    provider_input = SessionProviderInput(
        session=source.session,
        history=source.history,
        catalog=(*source.catalog, campaign_file_tool),
        control=source.control,
        basis_message_id=source.history.messages[-1].message_id,
        recovery_kind=None if mode == "control_reply" else "post_mechanics_narration",
        retry_action_receipt=None if mode == "control_reply" else receipt,
        narrative_writer=lambda parameters: (
            writes.append(dict(parameters))
            or {"status": "campaign_file_updated", "resulting_revision": 2}
        ),
    )

    owner = _OwnerProjection()
    if mode == "redirect":
        owner.boundary = ModelDecisionBoundary(
            owner=ModelDecisionOwner.PLAYER, actor_id="rift-player-character", actor_name="Player",
            actor_state={"hp": 12}, campaign_revision=1,
        )
        owner.events = (
            CanonicalEvent(sequence=1, event_type=CanonicalEventType.TOOL_RESULT,
                           turn_id="turn-provider", action_id=receipt.action_id,
                           safe_payload={"result": {"before": 0, "after": 4}}),
            CanonicalEvent(sequence=2, event_type=CanonicalEventType.ACTION_COMMITTED,
                           turn_id="turn-provider", action_id=receipt.action_id,
                           safe_payload={"status": "committed", "resulting_revision": 1}),
        )
    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner),
            provider,
            owner_id="owner-provider",
        ).for_turn("turn-provider")(provider_input)
    )

    assert writes == [update]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        narration + ("\n\nOOC — Sylvia: Kept brief." if mode == "redirect" else "")
    ]
    assert len(provider.calls) == 1
    first_messages, first_tools = provider.calls[0]
    assert first_tools is not None
    assert _tool_operations(first_tools) == [
        "advance",
        "update_campaign_file",
    ]
    campaign_file_description = str(first_tools[1]["function"]["description"])
    assert "An unsuccessful outcome can still change location" in campaign_file_description
    assert "even when the player's goal remains unmet" in campaign_file_description
    assert "Any player-facing response and this disposition" in campaign_file_description
    assert "either delivers the held response or asks" not in campaign_file_description
    first_rendered = json.dumps(first_messages, sort_keys=True)
    if mode != "control_reply":
        assert "SYLVIA_GM_POST_MECHANICS_RECOVERY_CONTROL" in first_rendered
        assert "Do not repeat or reroll" in first_rendered
    if mode in {"control_reply", "control_retry"}:
        assert "SYLVIA_PLAYER_ACTION_RESPONSE_TASK" in first_rendered
    if mode == "redirect":
        assert "SYLVIA_GM_REDIRECTED_CONTINUATION" in first_rendered
        assert "Talk instead" in first_rendered and "Keep it brief" in first_rendered
        assert "enemy response" in first_rendered
    assert "action-post-mechanics" in first_rendered
    assert "Run the whole turn as one continuous conversation" in first_rendered
    assert "prepare_player_action_response" not in first_rendered
    assert provider.request_kwargs[0]["tool_choice"] == "required"


def test_retry_adapter_reconstructs_authoritative_context_and_never_exposes_tools() -> None:
    provider = _ScriptedProvider(
        [
            [
                ProviderStreamChunk(reasoning_content="PRIVATE RETRY REASONING"),
                ProviderStreamChunk(content="The narration resumes.", finish_reason="stop"),
            ]
        ]
    )
    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, _OwnerProjection()),
        provider,
        owner_id="owner-provider",
        campaign_plan_text="# The Rift Awakening\nFollow the accepted arc.",
        campaign_plan={
            "id": "rift-awakening",
            "title": "The Rift Awakening",
            "bindings": {"ruleset": {"id": "synthetic.clock"}},
            "gm_guidance": {"tone": "grounded"},
            "structure": [],
        },
    )
    source_input = _provider_input(user_text="I strike the rift-scarred hound.")
    retry_terminal = SessionMessage(
        session_id=source_input.session.session_id,
        sequence=2,
        message_id="terminal-retry-provider-prior",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "session_attempt_terminal",
            "attempt_id": "retry-provider-prior",
            "message_id": "message-provider",
            "input_id": "attempt-provider",
            "failure_category": "transient",
        },
    )
    provider_input = SessionProviderInput(
        session=source_input.session,
        history=SessionContext(
            session=source_input.session,
            messages=(*source_input.history.messages, retry_terminal),
        ),
        catalog=source_input.catalog,
        control=source_input.control,
    )
    emitted: list[tuple[ProseChannel, str]] = []
    retry_input = RetryNarrationInput(
        campaign_id="campaign-provider",
        interruption_id="interruption-provider",
        retry_id="retry-provider",
        failed_attempt_id="retry-provider-prior",
        canonical_high_water=7,
        turn_id="turn-provider",
        action_receipt=ActionReceipt(
            campaign_id="campaign-provider",
            action_id="action-provider",
            status=ActionStatus.COMMITTED,
            result={"attack_total": 17, "damage": 6},
            resulting_revision=4,
        ),
    )

    adapter.for_retry("synthetic-model", provider_input=provider_input)(
        retry_input,
        NarrationStream(lambda channel, token: emitted.append((channel, token))),
    )

    assert emitted == [(ProseChannel.TALK, "The narration resumes.")]
    assert len(provider.calls) == 1
    messages, tools = provider.calls[0]
    assert tools is None
    rendered = str(messages)
    assert "canonical_high_water" in rendered
    assert "I strike the rift-scarred hound." in rendered
    assert "The Rift Awakening" in rendered
    assert "Mara Venn" in rendered
    assert "armor_class" in rendered
    assert "attack_total" in rendered
    assert "already committed mechanic result" in rendered
    assert "PRIVATE RETRY REASONING" not in rendered
    print("F4 PROVIDER RETRY", "tools=none", "talk=streamed", "private=discarded")


def test_retry_adapter_rejects_serialized_minimax_tool_markup_before_talk() -> None:
    serialized_call = (
        "]<]minimax[>[<tool_call>\n"
        ']<]minimax[>[<invoke name="update_campaign_file">'
        "]<]minimax[>[<facts>]<]minimax[>[</facts>"
        "]<]minimax[>[</invoke>\n]<]minimax[>[</tool_call>"
    )
    provider = _ScriptedProvider(
        [[ProviderStreamChunk(content=serialized_call, finish_reason="stop")]]
    )
    source_input = _provider_input(user_text="I take the quiet northern path.")
    terminal = SessionMessage(
        session_id=source_input.session.session_id,
        sequence=2,
        message_id="terminal-retry-provider-markup",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "session_attempt_terminal",
            "attempt_id": "attempt-provider-markup",
            "message_id": "message-provider",
            "input_id": "attempt-provider",
            "failure_category": "provider_unavailable",
            "recovery_kind": "post_mechanics_narration",
        },
    )
    provider_input = SessionProviderInput(
        session=source_input.session,
        history=SessionContext(
            session=source_input.session,
            messages=(*source_input.history.messages, terminal),
        ),
        catalog=source_input.catalog,
        control=source_input.control,
    )
    retry_input = RetryNarrationInput(
        campaign_id="campaign-provider",
        interruption_id="interruption-provider-markup",
        retry_id="retry-provider-markup",
        failed_attempt_id="attempt-provider-markup",
        canonical_high_water=7,
        turn_id="turn-provider",
        action_receipt=ActionReceipt(
            campaign_id="campaign-provider",
            action_id="action-provider-markup",
            status=ActionStatus.COMMITTED,
            result={"status": "committed"},
            resulting_revision=4,
        ),
    )
    emitted: list[tuple[ProseChannel, str]] = []

    with pytest.raises(CommandRejected) as captured:
        ProviderSessionAdapter(
            cast(AuthorityOwner, _OwnerProjection()),
            provider,
            owner_id="owner-provider",
        ).for_retry("synthetic-model", provider_input=provider_input)(
            retry_input,
            NarrationStream(lambda channel, token: emitted.append((channel, token))),
        )

    assert captured.value.code == "provider_serialized_tool_call"
    assert emitted == []
    assert provider.calls[0][1] is None


def test_anthropic_stream_preserves_tool_identity_and_argument_index() -> None:
    transport = AnthropicMessagesTransport()
    start = transport.parse_stream_chunk(
        SimpleNamespace(
            type="content_block_start",
            index=3,
            content_block=SimpleNamespace(
                type="tool_use",
                id="toolu_one",
                name="advance",
                input={},
            ),
        )
    )
    delta = transport.parse_stream_chunk(
        SimpleNamespace(
            type="content_block_delta",
            index=3,
            delta=SimpleNamespace(type="input_json_delta", partial_json='{"steps":2}'),
        )
    )
    assert start is not None and start.tool_call_delta == {
        "index": 3,
        "id": "toolu_one",
        "name": "advance",
        "arguments": "",
    }
    assert delta is not None and delta.tool_call_delta == {
        "index": 3,
        "partial_json": '{"steps":2}',
    }


def test_anthropic_single_required_tool_choice_names_the_only_operation() -> None:
    request = AnthropicMessagesTransport().format_request(
        "claude-test",
        [{"role": "user", "content": "Advance the required engine transition."}],
        [
            {
                "type": "function",
                "function": {
                    "name": "advance",
                    "description": "Required transition.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ],
        True,
        tool_choice="required",
        temperature=0,
    )

    assert request["tool_choice"] == {"type": "tool", "name": "advance"}
    assert request["temperature"] == 0


def test_anthropic_multiple_required_tool_choices_preserve_model_choice() -> None:
    request = AnthropicMessagesTransport().format_request(
        "claude-test",
        [{"role": "user", "content": "Choose the required engine transition."}],
        [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": "Available transition.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            for name in ("advance", "wait")
        ],
        True,
        tool_choice="required",
    )

    assert request["tool_choice"] == {"type": "any"}


def test_configured_provider_choices_are_safe_catalog_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sylvia_gm.agent import _provider_resolver
    from sylvia_gm.config.loader import ProviderDef, SylviaGMConfig

    sentinel_key = "SENTINEL-SECRET-KEY"
    sentinel_url = "https://private-provider.invalid/v1"
    config = SylviaGMConfig(
        providers={
            "openrouter": ProviderDef(
                enabled=True,
                mode="byok",
                name="OpenRouter",
                base_url=sentinel_url,
                api_key=sentinel_key,
                models=[
                    {
                        "id": "vendor/private-model",
                        "name": "Private Model",
                        "context_window": 32768,
                        "max_output_tokens": 2048,
                    }
                ],
            ),
            "ollama": ProviderDef(enabled=False),
        }
    )
    monkeypatch.setattr(
        _provider_resolver,
        "load_config",
        lambda *_args, **_kwargs: config,
    )

    resolver = _provider_resolver.ConfiguredProviderResolver()
    choices = resolver.model_choices()

    assert len(choices) == 1
    assert {choice["provider_id"] for choice in choices} == {"openrouter"}
    assert choices[0]["model_id"] == "openrouter/vendor/private-model"
    assert choices[0]["model_name"] == "Private Model"
    assert choices[0]["context_window"] == 32768
    assert all(
        set(choice)
        == {
            "provider_id",
            "provider_name",
            "model_id",
            "model_name",
            "context_window",
            "declared_context_window",
            "declared_context_source",
            "runtime_context_window",
            "runtime_context_source",
            "configured_context_window",
            "effective_context_window",
            "effective_context_source",
            "declared_context_observed_at",
            "runtime_context_observed_at",
            "configured_context_observed_at",
            "context_observed_at",
            "configured_runtime_mismatch",
            "configured_declared_mismatch",
            "max_output_tokens",
            "builder_output_tokens",
            "output_budget_mode",
            "transport",
            "supports_streaming",
            "supports_tools",
            "supported_reasoning_efforts",
            "default_reasoning_effort",
            "reasoning_source",
            "reasoning_control",
            "reasoning_mode_defaults",
            "capability_registry_version",
            "capability_tier_id",
            "capability_evidence_sha256",
        }
        for choice in choices
    )
    assert all(choice["capability_tier_id"] is None for choice in choices)
    assert all(choice["capability_evidence_sha256"] is None for choice in choices)
    serialized = json.dumps(choices, sort_keys=True)
    assert "recommended" not in serialized and "premium" not in serialized
    assert sentinel_key not in serialized
    assert sentinel_url not in serialized
    provider = resolver(
        SessionRecord(
            session_id="session-configured-provider",
            campaign_id="campaign-configured-provider",
            role=WriterRole.DM,
            provider_id="openrouter",
            model_id="openrouter/vendor/private-model",
        )
    )
    assert cast(Any, provider).model_info.id == "openrouter/vendor/private-model"
    assert cast(Any, provider).model_info.wire_model_id == "vendor/private-model"
    asyncio.run(provider.close())
    print(
        "F4 SAFE MODEL CHOICES",
        f"choices={len(choices)}",
        "providers=openrouter",
        "credentials=owner-only",
    )


def test_configured_provider_resolves_each_model_to_its_exact_wire_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sylvia_gm.agent import _provider_resolver
    from sylvia_gm.config.loader import ProviderDef, SylviaGMConfig

    config = SylviaGMConfig(
        providers={
            "custom": ProviderDef(
                enabled=True,
                mode="byok",
                name="Custom",
                base_url="https://provider.invalid/v1",
                api_key="sentinel",
                models=[
                    {"id": "vendor/model-a", "context_window": 8192},
                    {"id": "vendor/model-b", "context_window": 16384},
                ],
            )
        }
    )
    monkeypatch.setattr(
        _provider_resolver,
        "load_config",
        lambda *_args, **_kwargs: config,
    )
    resolver = _provider_resolver.ConfiguredProviderResolver()

    providers = [
        resolver(
            SessionRecord(
                session_id=f"session-{suffix}",
                campaign_id=f"campaign-{suffix}",
                role=WriterRole.DM,
                provider_id="custom",
                model_id=f"custom/vendor/model-{suffix}",
            )
        )
        for suffix in ("a", "b")
    ]
    try:
        assert providers[0] is not providers[1]
        assert [cast(Any, provider).model_info.id for provider in providers] == [
            "custom/vendor/model-a",
            "custom/vendor/model-b",
        ]
        assert [cast(Any, provider).model_info.wire_model_id for provider in providers] == [
            "vendor/model-a",
            "vendor/model-b",
        ]
    finally:
        for provider in providers:
            asyncio.run(provider.close())


def test_configured_provider_rejects_noncanonical_short_model_identity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from sylvia_gm.agent import _provider_resolver
    from sylvia_gm.config.loader import ProviderDef, SylviaGMConfig

    monkeypatch.setattr(
        _provider_resolver,
        "load_config",
        lambda *_args, **_kwargs: SylviaGMConfig(
            providers={
                "custom": ProviderDef(
                    enabled=True,
                    mode="byok",
                    name="Custom",
                    base_url="https://provider.invalid/v1",
                    api_key="sentinel",
                    models=[{"id": "model-a", "context_window": 8192}],
                )
            }
        ),
    )
    resolver = _provider_resolver.ConfiguredProviderResolver()

    with pytest.raises(CommandRejected, match="not canonical"):
        resolver(
            SessionRecord(
                session_id="session-short-model",
                campaign_id="campaign-short-model",
                role=WriterRole.DM,
                provider_id="custom",
                model_id="model-a",
            )
        )


def test_openai_compatible_request_uses_wire_model_and_keeps_canonical_identity() -> None:
    captured_request: dict[str, object] = {}

    class FakeCompletions:
        async def create(self, **kwargs: object) -> object:
            captured_request.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Configured response.",
                            reasoning_content=None,
                            tool_calls=[],
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            )

    class FakeClient:
        def __init__(self) -> None:
            self.api_key = ""
            self.chat = SimpleNamespace(completions=FakeCompletions())

        async def close(self) -> None:
            return None

    async def exercise() -> ProviderResponse:
        provider = OpenAICompatProvider(
            base_url="https://provider.invalid/v1",
            api_key="sentinel",
            model_info=ModelInfo(
                id="custom-provider/stable-model",
                name="Stable Model",
                provider_name="Custom Provider",
                provider_key="custom_provider",
                provider_mode="byok",
                context_window=8192,
                provider_model_id="vendor/upstream-model",
            ),
        )
        original_client = cast(Any, provider)._client
        await original_client.close()
        cast(Any, provider)._client = FakeClient()
        try:
            return await provider.generate(
                [{"role": "user", "content": "Hello"}],
            )
        finally:
            await provider.close()

    response = asyncio.run(exercise())

    assert captured_request["model"] == "vendor/upstream-model"
    assert response.model == "custom-provider/stable-model"
    assert response.content == "Configured response."


def test_local_qwen_35_requests_do_not_add_synthetic_assistant_prefill() -> None:
    captured_request: dict[str, object] = {}
    audit_traces: list[object] = []

    class FakeCompletions:
        async def create(self, **kwargs: object) -> object:
            captured_request.update(kwargs)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Ready.",
                            reasoning_content=None,
                            tool_calls=[],
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            )

    class FakeClient:
        def __init__(self) -> None:
            self.api_key = ""
            self.chat = SimpleNamespace(completions=FakeCompletions())

        async def close(self) -> None:
            return None

    async def exercise() -> ProviderResponse:
        provider = OpenAICompatProvider(
            base_url="http://127.0.0.1:1234/v1",
            api_key="",
            model_info=ModelInfo(
                id="local/qwen3.5-9b",
                name="Qwen 3.5 9B",
                provider_name="Local model server",
                provider_key="local-profile",
                provider_mode="local",
                provider_model_id="qwen3.5-9b",
            ),
        )
        original_client = cast(Any, provider)._client
        await original_client.close()
        cast(Any, provider)._client = FakeClient()
        audited = AdminAuditedProvider(
            provider,
            sink=audit_traces.append,
            session_id="session-qwen-wire-audit",
            role="dm",
            provider_id="local",
            model_id="local/qwen3.5-9b",
        )
        try:
            return await audited.generate([{"role": "user", "content": "Hello"}])
        finally:
            await provider.close()

    response = asyncio.run(exercise())

    assert response.content == "Ready."
    assert captured_request["messages"] == [{"role": "user", "content": "Hello"}]
    wire_trace = next(
        trace
        for trace in audit_traces
        if isinstance(trace, dict) and trace.get("kind") == "provider_wire_request"
    )
    assert wire_trace["input_stage"] == "provider_specific_wire_request"
    assert wire_trace["request"] == captured_request
    response_trace = next(
        trace
        for trace in audit_traces
        if isinstance(trace, dict) and trace.get("kind") == "provider_response"
    )
    assert response_trace["usage"] == {
        "availability": "not_returned",
        "precision": "unavailable",
        "source": "unavailable",
        "scope": "unavailable",
        "input_tokens": None,
        "output_tokens": None,
        "total_tokens": None,
        "cached_tokens": None,
        "reasoning_tokens": None,
    }


def test_openai_compatible_credentialless_endpoint_uses_internal_sdk_key() -> None:
    observed_api_keys: list[str] = []

    class FakeStream:
        def __init__(self) -> None:
            self._chunks = iter(
                (
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(
                                    content="Streamed response.",
                                    reasoning_content=None,
                                    tool_calls=None,
                                ),
                                finish_reason="stop",
                            )
                        ],
                        usage=None,
                    ),
                )
            )

        def __aiter__(self) -> FakeStream:
            return self

        async def __anext__(self) -> object:
            try:
                return next(self._chunks)
            except StopIteration as error:
                raise StopAsyncIteration from error

    class FakeCompletions:
        def __init__(self, client: FakeClient) -> None:
            self._client = client

        async def create(self, **kwargs: object) -> object:
            observed_api_keys.append(self._client.api_key)
            if kwargs.get("stream") is True:
                return FakeStream()
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(
                            content="Configured response.",
                            reasoning_content=None,
                            tool_calls=[],
                        ),
                        finish_reason="stop",
                    )
                ],
                usage=None,
            )

    class FakeClient:
        def __init__(self) -> None:
            self.api_key = ""
            self.chat = SimpleNamespace(completions=FakeCompletions(self))

        async def close(self) -> None:
            return None

    async def exercise() -> tuple[ProviderResponse, list[ProviderStreamChunk]]:
        provider = OpenAICompatProvider(
            base_url="http://127.0.0.1:1234/v1",
            api_key="",
            model_info=ModelInfo(
                id="lm-studio/local-model",
                name="Local Model",
                provider_name="LM Studio",
                provider_key="lm_studio",
                provider_mode="local",
                context_window=8192,
                provider_model_id="local-model",
            ),
        )
        original_client = cast(Any, provider)._client
        await original_client.close()
        cast(Any, provider)._client = FakeClient()
        try:
            response = await provider.generate([{"role": "user", "content": "Hello"}])
            chunks = [
                chunk
                async for chunk in provider.generate_stream([{"role": "user", "content": "Hello"}])
            ]
            return response, chunks
        finally:
            await provider.close()

    response, chunks = asyncio.run(exercise())

    assert observed_api_keys == ["sylvia-gm-local", "sylvia-gm-local"]
    assert response.content == "Configured response."
    assert [chunk.content for chunk in chunks] == ["Streamed response."]


def test_openai_stream_captures_usage_from_final_chunk_without_choices() -> None:
    traces: list[object] = []

    class FakeStream:
        def __init__(self) -> None:
            self._chunks = iter(
                (
                    SimpleNamespace(
                        choices=[
                            SimpleNamespace(
                                delta=SimpleNamespace(
                                    content="Complete.",
                                    reasoning_content=None,
                                    tool_calls=None,
                                ),
                                finish_reason="stop",
                            )
                        ],
                        usage=None,
                    ),
                    SimpleNamespace(
                        choices=[],
                        usage=SimpleNamespace(
                            prompt_tokens=321,
                            completion_tokens=17,
                            total_tokens=338,
                            prompt_tokens_details=SimpleNamespace(cached_tokens=40),
                            completion_tokens_details=SimpleNamespace(reasoning_tokens=0),
                        ),
                    ),
                )
            )

        def __aiter__(self) -> FakeStream:
            return self

        async def __anext__(self) -> object:
            try:
                return next(self._chunks)
            except StopIteration as error:
                raise StopAsyncIteration from error

    class FakeCompletions:
        async def create(self, **_kwargs: object) -> FakeStream:
            return FakeStream()

    class FakeClient:
        def __init__(self) -> None:
            self.api_key = ""
            self.chat = SimpleNamespace(completions=FakeCompletions())

        async def close(self) -> None:
            return None

    async def exercise() -> None:
        base = OpenAICompatProvider(
            base_url="http://127.0.0.1:1234/v1",
            api_key="",
            model_info=ModelInfo(
                id="lm-studio/usage-model",
                name="Usage Model",
                provider_name="LM Studio",
                provider_key="lm-studio",
                provider_mode="local",
                provider_model_id="usage-model",
            ),
        )
        original_client = cast(Any, base)._client
        await original_client.close()
        cast(Any, base)._client = FakeClient()
        provider = AdminAuditedProvider(
            base,
            sink=traces.append,
            session_id="session-usage-only",
            role="dm",
        )
        try:
            async for _chunk in provider.generate_stream([{"role": "user", "content": "Hello"}]):
                pass
        finally:
            await provider.close()

    asyncio.run(exercise())

    response = next(
        trace
        for trace in traces
        if isinstance(trace, dict) and trace.get("kind") == "provider_stream_response"
    )
    assert response["usage"] == {
        "availability": "provider_returned",
        "precision": "authoritative_count",
        "source": "provider_reported_completed_request",
        "scope": "completed_request_only",
        "input_tokens": 321,
        "output_tokens": 17,
        "total_tokens": 338,
        "cached_tokens": 40,
        "reasoning_tokens": 0,
    }


def test_openai_compatible_timeout_detects_stream_inactivity() -> None:
    class SlowStream:
        def __aiter__(self) -> SlowStream:
            return self

        async def __anext__(self) -> object:
            await asyncio.sleep(10)
            raise StopAsyncIteration

    class FakeCompletions:
        async def create(self, **_kwargs: object) -> object:
            return SlowStream()

    class FakeClient:
        def __init__(self) -> None:
            self.api_key = ""
            self.chat = SimpleNamespace(completions=FakeCompletions())

        async def close(self) -> None:
            return None

    async def exercise() -> None:
        provider = OpenAICompatProvider(
            base_url="http://127.0.0.1:1234/v1",
            api_key="",
            model_info=ModelInfo(
                id="lm-studio/slow-model",
                name="Slow Model",
                provider_name="LM Studio",
                provider_key="lm_studio",
                provider_mode="local",
            ),
        )
        original_client = cast(Any, provider)._client
        await original_client.close()
        cast(Any, provider)._client = FakeClient()
        try:
            with pytest.raises(ProviderTimeoutError):
                _ = [
                    chunk
                    async for chunk in provider.generate_stream(
                        [{"role": "user", "content": "Hello"}], timeout=0.01
                    )
                ]
        finally:
            await provider.close()

    asyncio.run(exercise())


@pytest.mark.parametrize("terminal", [True, False])
def test_openai_compatible_timeout_resets_when_stream_chunks_arrive(terminal) -> None:
    class ActiveStream:
        def __init__(self) -> None:
            self._remaining = 4

        def __aiter__(self) -> ActiveStream:
            return self

        async def __anext__(self) -> object:
            if self._remaining == 0:
                raise StopAsyncIteration
            self._remaining -= 1
            await asyncio.sleep(0.04)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content="token",
                            reasoning_content=None,
                            tool_calls=None,
                        ),
                        finish_reason="stop" if terminal and self._remaining == 0 else None,
                    )
                ],
                usage=None,
            )

    class FakeCompletions:
        async def create(self, **_kwargs: object) -> object:
            return ActiveStream()

    class FakeClient:
        def __init__(self) -> None:
            self.api_key = ""
            self.chat = SimpleNamespace(completions=FakeCompletions())

        async def close(self) -> None:
            return None

    async def exercise() -> list[str]:
        provider = OpenAICompatProvider(
            base_url="http://127.0.0.1:1234/v1",
            api_key="",
            model_info=ModelInfo(
                id="lm-studio/active-model",
                name="Active Model",
                provider_name="LM Studio",
                provider_key="lm_studio",
                provider_mode="local",
            ),
        )
        original_client = cast(Any, provider)._client
        await original_client.close()
        cast(Any, provider)._client = FakeClient()
        try:
            return [
                chunk.content
                async for chunk in provider.generate_stream(
                    [{"role": "user", "content": "Hello"}], timeout=0.1
                )
            ]
        finally:
            await provider.close()

    # The whole stream lasts longer than 0.1s, but no individual chunk does.
    if terminal:
        assert asyncio.run(exercise()) == ["token"] * 4
    else:
        from sylvia_gm.provider._exceptions import IncompleteProviderStreamError
        with pytest.raises(IncompleteProviderStreamError):
            asyncio.run(exercise())


@pytest.mark.parametrize(
    "models,match",
    [
        (
            [{"id": "model-a", "context_window": 8192, "recommended": True}],
            "unsupported fields",
        ),
        (
            [
                {"id": "model-a", "context_window": 8192},
                {"id": "model-a", "context_window": 8192},
            ],
            "duplicated",
        ),
        (
            [{"id": "model-a", "context_window": 8192, "transport": []}],
            "transport",
        ),
    ],
)
def test_configured_provider_model_rows_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    models: list[dict[str, object]],
    match: str,
) -> None:
    from sylvia_gm.agent import _provider_resolver
    from sylvia_gm.config._resolver import ConfigError
    from sylvia_gm.config.loader import ProviderDef, SylviaGMConfig

    config = SylviaGMConfig(
        providers={
            "custom": ProviderDef(
                enabled=True,
                mode="byok",
                name="Custom",
                base_url="https://provider.invalid/v1",
                api_key="sentinel",
                models=models,
            )
        }
    )
    monkeypatch.setattr(
        _provider_resolver,
        "load_config",
        lambda *_args, **_kwargs: config,
    )
    with pytest.raises(ConfigError, match=match):
        _provider_resolver.ConfiguredProviderResolver()


@pytest.mark.parametrize("recovery", [False, True])
def test_direct_action_response_starts_new_provider_work_after_completed_opening(
    recovery: bool,
) -> None:
    opening = "The first scene opens at the old gate."
    response = "The guard recoils from the resolved shot."
    provider = _ScriptedProvider(
        [[
            ProviderStreamChunk(
                tool_call_delta={
                    "index": 0,
                    "id": "response-file-update",
                    "function": {
                        "name": "update_campaign_file",
                        "arguments": json.dumps({
                            "revised_player_facing_response": [
                                {"kind": "narration", "text": response}
                            ]
                        }),
                    },
                },
                finish_reason="tool_calls",
            )
        ]]
    )
    source = _provider_input()
    opening_start = SessionMessage(
        session_id=source.session.session_id,
        sequence=1,
        message_id="campaign-start",
        kind=SessionMessageKind.EVIDENCE,
        content={"kind": "campaign_start", "attempt_id": "opening-attempt"},
    )
    opening_calls = SessionMessage(
        session_id=source.session.session_id,
        sequence=2,
        message_id="opening-tool-calls",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_tool_calls",
            "basis_message_id": opening_start.message_id,
            "inference_id": "opening-inference",
            "assistant": {
                "role": "assistant",
                "content": None,
                "tool_calls": [{
                    "id": "opening-file-update",
                    "type": "function",
                    "function": {
                        "name": "update_campaign_file",
                        "arguments": json.dumps({"revised_player_facing_response": opening}),
                    },
                }],
            },
        },
    )
    opening_results = SessionMessage(
        session_id=source.session.session_id,
        sequence=3,
        message_id="opening-tool-results",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "provider_tool_results",
            "basis_message_id": opening_start.message_id,
            "inference_id": "opening-inference",
            "results": [{
                "role": "tool",
                "tool_call_id": "opening-file-update",
                "content": json.dumps({
                    "status": "campaign_file_unchanged",
                    "campaign_revision": 4,
                    "state_changed": False,
                    "player_facing_response": opening,
                }),
            }],
        },
    )
    action_response = SessionMessage(
        session_id=source.session.session_id,
        sequence=4,
        message_id="direct-action-response",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "player_action_response",
            "player_action_id": "direct-fire",
            "automatic_response": True,
            "attempt_id": "response-attempt",
            "turn_id": "turn-provider",
        },
    )
    provider_input = replace(
        source,
        history=SessionContext(
            session=source.session,
            messages=(opening_start, opening_calls, opening_results, action_response),
        ),
        basis_message_id=action_response.message_id,
        recovery_kind="post_mechanics_narration" if recovery else None,
        retry_action_receipt=ActionReceipt(
            campaign_id=source.session.campaign_id,
            action_id="direct-fire",
            status=ActionStatus.COMMITTED,
            result={"status": "committed"},
            resulting_revision=4,
        ) if recovery else None,
        catalog=(*source.catalog, _campaign_file_tool(allow_unchanged=True)),
        narrative_writer=lambda _parameters: {
            "status": "campaign_file_unchanged",
            "campaign_revision": 4,
            "state_changed": False,
        },
    )
    owner = _OwnerProjection()
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TALK,
            turn_id="turn-provider",
            action_id=None,
            attempt_id="opening-attempt",
            safe_payload={"text": opening},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )

    adapter = ProviderSessionAdapter(
        cast(AuthorityOwner, owner), provider, owner_id="owner-provider"
    )
    events = tuple(adapter.for_turn("turn-provider")(provider_input))

    assert len(provider.calls) == 1
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [response]
    assert opening not in [event.text for event in events if event.kind is ProviderEventKind.TALK]

    wire_request = AnthropicMessagesTransport().format_request(
        model_id="synthetic-native", messages=provider.calls[0][0], tools=None, stream=True,
    )
    latest_input = wire_request["messages"][-1]
    assert latest_input["role"] == "user"
    assert isinstance(latest_input["content"], str)
    assert "application event, not player speech" in latest_input["content"]
    assert '"action_id": "direct-fire"' in latest_input["content"]
    assert opening not in latest_input["content"]


def test_automatic_transition_with_required_judgment_fails_before_action_or_inference():
    source = _provider_input()
    required = replace(source.catalog[0], name="needs_judgment",
        input_schema={"type": "object", "properties": {"target": {"type": "string"}}, "required": ["target"]},
        model_behavior=ModelToolBehavior.REQUIRED_ENGINE_TRANSITION)
    provider = _ScriptedProvider([])
    provider_input = SessionProviderInput(session=source.session, history=source.history,
        catalog=(required,), control=source.control)
    adapter = ProviderSessionAdapter(cast(AuthorityOwner, _OwnerProjection()), provider, owner_id="owner-provider")
    with pytest.raises(CommandRejected) as captured:
        tuple(adapter.for_turn("turn-provider")(provider_input))
    assert captured.value.code == "invalid_engine_transition_surface"
    assert provider.calls == []
