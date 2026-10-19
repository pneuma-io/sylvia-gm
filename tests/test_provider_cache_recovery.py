"""Cache wire projection must not change the GM's logical recovery boundaries."""

import json
from dataclasses import replace
from typing import cast

import pytest

from sylvia_gm.agent._provider_session import ProviderSessionAdapter
from sylvia_gm.agent._session_loop import ProviderEventKind
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import (
    ActionReceipt,
    ActionStatus,
    CanonicalEvent,
    CanonicalEventType,
    EventVisibility,
    ModelDecisionBoundary,
    ModelDecisionOwner,
)
from sylvia_gm.domain.session import SessionContext, SessionMessage, SessionMessageKind
from sylvia_gm.provider._base import ModelInfo, ProviderStreamChunk
from tests.test_alpha_f4_provider_session_adapter import (
    _campaign_file_tool,
    _OwnerProjection,
    _provider_input,
    _ScriptedProvider,
)

OLD_NARRATION = "The old gate creaks open after your earlier approach."
CURRENT_NARRATION = "The guard ducks after your latest shot."


def direct_response_input(*, status, recovery, action_id="current-shot"):
    source = _provider_input(user_text="I approach the old gate.")
    prior_user = replace(
        source.history.messages[0],
        message_id="old-user",
        content={
            "text": "I approach the old gate.",
            "attempt_id": "old-attempt",
            "turn_id": "old-turn",
        },
    )
    history = [prior_user]
    for sequence, kind, payload in (
        (
            2,
            "provider_tool_calls",
            {
                "assistant": {
                    "role": "assistant",
                    "content": OLD_NARRATION,
                    "tool_calls": [
                        {
                            "id": "old-file-call",
                            "type": "function",
                            "function": {
                                "name": "update_campaign_file",
                                "arguments": json.dumps(
                                    {
                                        "revised_player_facing_response": [
                                            {"kind": "narration", "text": OLD_NARRATION}
                                        ]
                                    }
                                ),
                            },
                        }
                    ],
                }
            },
        ),
        (
            3,
            "provider_tool_results",
            {
                "results": [
                    {
                        "role": "tool",
                        "name": "update_campaign_file",
                        "tool_call_id": "old-file-call",
                        "content": json.dumps(
                            {
                                "status": status,
                                "campaign_revision": 3,
                                "state_changed": status == "campaign_file_updated",
                                "player_facing_response": OLD_NARRATION,
                            }
                        ),
                    }
                ]
            },
        ),
    ):
        history.append(
            SessionMessage(
                session_id=source.session.session_id,
                sequence=sequence,
                message_id=f"old-evidence-{sequence}",
                kind=SessionMessageKind.EVIDENCE,
                content={
                    "kind": kind,
                    "basis_message_id": prior_user.message_id,
                    "inference_id": "old-inference",
                    "turn_id": "old-turn",
                    **payload,
                },
            )
        )
    history.append(
        SessionMessage(
            session_id=source.session.session_id,
            sequence=4,
            message_id="old-talk",
            kind=SessionMessageKind.TALK,
            content={"text": OLD_NARRATION, "attempt_id": "old-attempt", "turn_id": "old-turn"},
        )
    )
    task = SessionMessage(
        session_id=source.session.session_id,
        sequence=5,
        message_id="current-action-response",
        kind=SessionMessageKind.EVIDENCE,
        content={
            "kind": "player_action_response",
            "player_action_id": action_id,
            "automatic_response": True,
            "attempt_id": "current-attempt",
            "turn_id": "turn-provider",
        },
    )
    history.append(task)
    writes = []

    def write(parameters):
        writes.append(dict(parameters))
        return {"status": "campaign_file_unchanged", "campaign_revision": 4, "state_changed": False}

    source = replace(
        source,
        history=SessionContext(session=source.session, messages=tuple(history)),
        basis_message_id=task.message_id,
        recovery_kind="post_mechanics_narration" if recovery else None,
        retry_action_receipt=ActionReceipt(
            campaign_id=source.session.campaign_id,
            action_id=action_id,
            status=ActionStatus.COMMITTED,
            result={"status": "committed"},
            resulting_revision=4,
        )
        if recovery
        else None,
        catalog=(*source.catalog, _campaign_file_tool(allow_unchanged=True)),
        narrative_writer=write,
    )
    owner = _OwnerProjection()
    owner.events = (
        CanonicalEvent(
            sequence=1,
            event_type=CanonicalEventType.TALK,
            turn_id="old-turn",
            action_id=None,
            attempt_id="old-attempt",
            safe_payload={"text": OLD_NARRATION},
            visibility=EventVisibility.PLAYER_SAFE,
        ),
    )
    return source, owner, writes


def campaign_file_chunk():
    return ProviderStreamChunk(
        tool_call_delta={
            "index": 0,
            "id": "current-file-call",
            "function": {
                "name": "update_campaign_file",
                "arguments": json.dumps(
                    {
                        "revised_player_facing_response": [
                            {"kind": "narration", "text": CURRENT_NARRATION}
                        ]
                    }
                ),
            },
        },
        finish_reason="tool_calls",
    )


@pytest.mark.parametrize("route", ["chatgpt_subscription", "openrouter"])
@pytest.mark.parametrize("status", ["campaign_file_updated", "campaign_file_unchanged"])
@pytest.mark.parametrize("recovery", [False, True])
def test_current_direct_response_never_reuses_a_completed_prior_disposition(
    route, status, recovery
):
    source, owner, writes = direct_response_input(status=status, recovery=recovery)
    provider = _ScriptedProvider([[campaign_file_chunk()]])
    provider.model_info = ModelInfo(
        "synthetic-model",
        "Test",
        route,
        route,
        "byok",
        transport="codex_app_server" if route == "chatgpt_subscription" else "chat_completions",
    )
    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner), provider, owner_id="owner-provider"
        ).for_turn("turn-provider")(source)
    )
    assert len(provider.calls) == 1, "The new action needs its own GM inference"
    assert len(writes) == 1, "Only a current Campaign File disposition may settle this response"
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        CURRENT_NARRATION
    ]
    assert OLD_NARRATION not in [
        event.text for event in events if event.kind is ProviderEventKind.TALK
    ]


@pytest.mark.parametrize("route", ["chatgpt", "openrouter"])
@pytest.mark.parametrize("recovery", [False, True])
@pytest.mark.parametrize(
    "boundary_kind,narrate_after_disposition",
    [
        (None, False),
        ("player", False),
        ("gm", False),
        (None, True),
        ("player", True),
    ],
)
def test_full_direct_response_cycles_keep_variable_task_data_out_of_fixed_instructions(
    tmp_path,
    route,
    recovery,
    boundary_kind,
    narrate_after_disposition,
):
    import httpx

    from sylvia_gm.provider._chatgpt import ChatGPTProvider
    from sylvia_gm.provider._openai_compat import OpenAICompatProvider
    from tests.test_pes82_chatgpt_failures import SyntheticRuntime, provider_for

    frames = []
    arguments = {
        "revised_player_facing_response": [{"kind": "narration", "text": CURRENT_NARRATION}]
    }

    def proposal(names):
        if not names:
            return None, None
        campaign_file = next(
            (name for name in names if name.endswith("update_campaign_file")), None
        )
        if campaign_file is not None:
            return campaign_file, arguments
        return next(name for name in names if name.endswith("_advance")), {
            "steps": 1,
            "actor": "rift-companion-chloe",
            "actor_state": "ready",
        }

    if route == "openrouter":

        def handle(request):
            body = json.loads(request.content)
            frames.append(body)
            operation, params = proposal(
                [tool["function"]["name"] for tool in body.get("tools", [])]
            )
            event = {
                "id": "synthetic",
                "created": 1,
                "object": "chat.completion.chunk",
                "model": "synthetic-model",
                "choices": [
                    {
                        "index": 0,
                        "delta": {
                            "tool_calls": [
                                {
                                    "index": 0,
                                    "id": "current-file-call",
                                    "type": "function",
                                    "function": {
                                        "name": operation,
                                        "arguments": json.dumps(params),
                                    },
                                }
                            ]
                        },
                        "finish_reason": "tool_calls",
                    }
                ],
            }
            if operation is None:
                event["choices"][0].update(
                    delta={"content": CURRENT_NARRATION}, finish_reason="stop"
                )
            return httpx.Response(
                200,
                headers={"content-type": "text/event-stream"},
                text="data: " + json.dumps(event) + "\n\ndata: [DONE]\n\n",
            )

        provider = OpenAICompatProvider(
            base_url="https://openrouter.ai/api/v1",
            api_key="synthetic",
            model_info=ModelInfo("synthetic-model", "Test", "OpenRouter", "openrouter", "byok"),
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        )
    else:

        class Runtime(SyntheticRuntime):
            def call(self, method, params):
                if method == "thread/start":
                    self.frame = {
                        "baseInstructions": params["baseInstructions"],
                        "tools": params["dynamicTools"],
                    }
                elif method == "thread/inject_items":
                    self.frame["messages"] = params["items"]
                elif method == "turn/start":
                    frames.append(json.loads(json.dumps(self.frame)))
                    operation, params = proposal(
                        [
                            tool["name"]
                            for namespace in self.frame["tools"]
                            for tool in namespace["tools"]
                        ]
                    )
                    self.events = [
                        {
                            "method": "item/tool/call",
                            "params": {
                                "namespace": "sylvia",
                                "tool": operation,
                                "callId": "current-file-call",
                                "arguments": params,
                            },
                        }
                    ]
                    if operation is None:
                        self.events = [
                            {
                                "method": "item/agentMessage/delta",
                                "params": {"delta": CURRENT_NARRATION},
                            },
                            {
                                "method": "turn/completed",
                                "params": {"turn": {"status": "completed"}},
                            },
                        ]
                return super().call(method, params)

        provider = ChatGPTProvider(
            tmp_path,
            provider_for(SyntheticRuntime([]), tmp_path).model_info,
            connection_factory=lambda *_args, **_kwargs: Runtime([]),
        )

    for cycle, action_id in enumerate(("shot-one", "shot-two")):
        source, owner, writes = direct_response_input(
            status="campaign_file_unchanged", recovery=recovery, action_id=action_id
        )
        if narrate_after_disposition:
            task = source.history.messages[-1]
            resumed = []
            for sequence, kind, payload in (
                (
                    6,
                    "provider_tool_calls",
                    {
                        "assistant": {
                            "role": "assistant",
                            "content": None,
                            "tool_calls": [
                                {
                                    "id": "current-saved-file",
                                    "type": "function",
                                    "function": {"name": "update_campaign_file", "arguments": "{}"},
                                }
                            ],
                        }
                    },
                ),
                (
                    7,
                    "provider_tool_results",
                    {
                        "results": [
                            {
                                "role": "tool",
                                "tool_call_id": "current-saved-file",
                                "name": "update_campaign_file",
                                "content": json.dumps(
                                    {
                                        "status": "campaign_file_updated",
                                        "campaign_revision": 4,
                                        "player_facing_response": None,
                                    }
                                ),
                            }
                        ]
                    },
                ),
            ):
                resumed.append(
                    SessionMessage(
                        session_id=source.session.session_id,
                        sequence=sequence,
                        message_id=f"current-saved-{sequence}",
                        kind=SessionMessageKind.EVIDENCE,
                        content={
                            "kind": kind,
                            "basis_message_id": task.message_id,
                            "inference_id": "current-saved-inference",
                            "turn_id": "turn-provider",
                            **payload,
                        },
                    )
                )
            source = replace(
                source,
                history=SessionContext(
                    session=source.session, messages=(*source.history.messages, *resumed)
                ),
            )
        if boundary_kind is not None:
            owner.boundary = ModelDecisionBoundary(
                owner=ModelDecisionOwner.GM if boundary_kind == "gm" else ModelDecisionOwner.PLAYER,
                actor_id="rift-companion-chloe"
                if boundary_kind == "gm"
                else "rift-player-character",
                actor_name="Chloe" if boundary_kind == "gm" else "Daniel",
                actor_state={"hp": 7 + cycle},
                campaign_revision=owner.revision,
            )
        events = []
        for event in ProviderSessionAdapter(
            cast(AuthorityOwner, owner), provider, owner_id="owner-provider"
        ).for_turn("turn-provider")(source):
            events.append(event)
            if event.kind is ProviderEventKind.ACTION:
                assert boundary_kind == "gm" and event.action_command.tool_name == "advance"
                owner.revision += 1
                for kind, payload in (
                    (
                        CanonicalEventType.TOOL_RESULT,
                        {"status": "committed", "result": {"status": "committed", "context": {}}},
                    ),
                    (CanonicalEventType.ACTION_COMMITTED, {"status": "committed"}),
                ):
                    owner.events = (
                        *owner.events,
                        CanonicalEvent(
                            sequence=len(owner.events) + 1,
                            event_type=kind,
                            turn_id="turn-provider",
                            action_id=event.action_command.action_id,
                            safe_payload=payload,
                            visibility=EventVisibility.PLAYER_SAFE,
                        ),
                    )
                owner.boundary = ModelDecisionBoundary(
                    owner=ModelDecisionOwner.PLAYER,
                    actor_id="rift-player-character",
                    actor_name="Daniel",
                    actor_state={"hp": 7 + cycle},
                    campaign_revision=owner.revision,
                )
        assert len(writes) == (0 if narrate_after_disposition else 1)
        assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
            CURRENT_NARRATION
        ]
    rounds = 2 if boundary_kind == "gm" else 1
    assert len(frames) == 2 * rounds
    instructions = [
        frame["baseInstructions"]
        if route == "chatgpt"
        else "\n\n".join(
            message["content"] for message in frame["messages"] if message["role"] == "system"
        )
        for frame in frames
    ]
    assert instructions[:rounds] == instructions[rounds:]
    assert not any(
        action in instruction for instruction in instructions for action in ("shot-one", "shot-two")
    )
    for frame, action_id in zip(
        frames, ("shot-one",) * rounds + ("shot-two",) * rounds, strict=True
    ):
        rendered = json.dumps(frame["messages"])
        assert action_id in rendered
        assert OLD_NARRATION in rendered
        last = frame["messages"][-1]["content"]
        if route == "chatgpt":
            last = last[0]["text"]
        assert last.startswith("SYLVIA_GM_FRESH_CAMPAIGN_STATE")
    assert [frame.get("tools", []) for frame in frames[:rounds]] == [
        frame.get("tools", []) for frame in frames[rounds:]
    ]
    if boundary_kind == "gm":
        assert "SYLVIA_GM_DECISION_REQUEST" in instructions[0]
        assert "SYLVIA_GM_PLAYER_BOUNDARY_NARRATION" in instructions[1]
        assert '"hp":7' not in instructions[0] and '"hp":8' not in instructions[rounds]
        assert instructions[0] != instructions[1], (
            "GM and player workflows have different directives"
        )

        def names(frame):
            if route == "chatgpt":
                return [tool["name"] for namespace in frame["tools"] for tool in namespace["tools"]]
            return [tool["function"]["name"] for tool in frame["tools"]]

        assert not any(name.endswith("update_campaign_file") for name in names(frames[0]))
        assert any(name.endswith("update_campaign_file") for name in names(frames[1]))
    elif boundary_kind == "player":
        assert "SYLVIA_GM_PLAYER_BOUNDARY_NARRATION" in instructions[0]
    if narrate_after_disposition:
        assert "SYLVIA_GM_CAMPAIGN_FILE_UPDATED_NARRATION" in instructions[rounds - 1]
