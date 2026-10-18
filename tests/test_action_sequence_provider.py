"""FC-17 provider tools use declared controls and one shared authoritative queue."""

import json
from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace
from typing import cast

import pytest

from sylvia_gm.agent._provider_session import ProviderSessionAdapter
from sylvia_gm.agent._session_loop import ProviderEventKind
from sylvia_gm.agent._stateless_mcp import action_sequence_model_tools, call_action_sequence_tool
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import (
    CanonicalEvent,
    CanonicalEventType,
    EventVisibility,
    PackageLock,
    PackageLockEntry,
    PackageRole,
)
from sylvia_gm.domain.errors import CommandRejected, MechanicsRejected
from sylvia_gm.provider._base import ProviderStreamChunk
from tests.test_action_sequences import lite as lite
from tests.test_action_sequences import td as td
from tests.test_alpha_f4_provider_session_adapter import (
    _campaign_file_tool,
    _OwnerProjection,
    _provider_input,
    _ScriptedProvider,
)

CAMPAIGN = "campaign-provider"
SESSION = "session-provider"
STEPS = [
    {
        "step_id": "step-move",
        "source": {"kind": "panel", "surface_id": "map", "action_id": "move"},
        "selection": {"actor": "hero", "destination": [1, 0, 0], "map_revision": 7},
    },
    {
        "step_id": "step-aim",
        "source": {"kind": "panel", "surface_id": "map", "action_id": "aim"},
        "selection": {"actor": "companion", "target": "enemy", "map_revision": 7},
    },
]


class QueueOwner(_OwnerProjection):
    def __init__(self, status="completed"):
        super().__init__()
        self.calls = []
        self.status = status
        self.sequence = None
        self.panels = {
            "campaign_id": CAMPAIGN,
            "revision": 4,
            "direct_actions_available": False,
            "player_control": {
                "available": True,
                "actors": [
                    {"id": "hero", "name": "Hero"},
                    {"id": "companion", "name": "Companion"},
                ],
            },
            "surfaces": [
                {
                    "id": "map",
                    "renderer": "tactical_map",
                    "actions": [
                        {
                            "id": "move",
                            "intent": "move_actor",
                            "bindings": {"actor": "actor", "destination": "destination"},
                        },
                        {
                            "id": "aim",
                            "intent": "aim",
                            "bindings": {"attacker": "actor", "target": "target"},
                        },
                    ],
                    "data": {
                        "map_revision": 7,
                        "entities": [{"id": "hero"}, {"id": "companion"}, {"id": "enemy"}],
                    },
                }
            ],
        }

    def snapshot(self, campaign_id):
        snapshot = super().snapshot(campaign_id)
        snapshot.package_lock = PackageLock(
            (
                PackageLockEntry(
                    package_id="synthetic.queue",
                    version="1",
                    sha256="1" * 64,
                    role=PackageRole.PRIMARY,
                    load_order=0,
                    dependencies=(),
                    capabilities=(),
                ),
            )
        )
        return snapshot

    def session_context(self, session_id):
        assert session_id == SESSION
        return _provider_input().history

    def campaign_panels(self, campaign_id):
        assert campaign_id == CAMPAIGN
        return self.panels

    def action_sequence(self, *, session_id, sequence_id=None):
        assert session_id == SESSION
        self.calls.append(("read", sequence_id))
        return self.sequence

    def _compose_action_sequence_from_agent(self, **parameters):
        self.calls.append(("compose", deepcopy(parameters)))
        assert parameters["campaign_id"] == CAMPAIGN
        assert parameters["session_id"] == SESSION
        self.sequence = {
            "sequence_id": parameters["sequence_id"],
            "version": 1,
            "status": "ready",
            "steps": deepcopy(parameters["steps"]),
            "confirmation": "queue-confirmation",
        }
        return self.sequence

    def execute_action_sequence(self, **parameters):
        pytest.fail("The public idle-only entry must not be called by the admitted GM operation")

    def _execute_action_sequence_from_agent(self, **parameters):
        self.calls.append(("execute", deepcopy(parameters)))
        assert parameters["campaign_id"] == CAMPAIGN
        assert parameters["session_id"] == SESSION
        for i, identity in enumerate(("committed-move", "committed-aim")):
            if self.status != "completed" and i:
                break
            self.events += (
                CanonicalEvent(
                    sequence=2 * i + 1,
                    event_type=CanonicalEventType.TOOL_RESULT,
                    turn_id="turn-provider",
                    action_id=identity,
                    safe_payload={"result": {"total": i + 1}},
                    visibility=EventVisibility.PLAYER_SAFE,
                ),
                CanonicalEvent(
                    sequence=2 * i + 2,
                    event_type=CanonicalEventType.ACTION_COMMITTED,
                    turn_id="turn-provider",
                    action_id=identity,
                    safe_payload={"status": "committed"},
                    visibility=EventVisibility.PLAYER_SAFE,
                ),
            )
        self.revision += len(self.events) // 2
        self.sequence = {
            "sequence_id": "intent-1",
            "version": 2,
            "status": self.status,
            "steps": [
                {"step_id": "step-move", "status": "committed", "action_id": "committed-move"},
                {
                    "step_id": "step-aim",
                    "status": "committed" if self.status == "completed" else "pending",
                },
            ],
        }
        self.sequence["committed_action_ids"] = [
            event.action_id
            for event in self.events
            if event.event_type is CanonicalEventType.ACTION_COMMITTED
        ]
        return self.sequence


def tool(owner, name, parameters):
    return call_action_sequence_tool(
        cast(AuthorityOwner, owner),
        campaign_id=CAMPAIGN,
        session_id=SESSION,
        name=name,
        parameters=parameters,
    )


def call(name, parameters, identity="call"):
    return [
        ProviderStreamChunk(
            tool_call_delta={
                "index": 0,
                "id": identity,
                "function": {"name": name, "arguments": json.dumps(parameters)},
            },
            finish_reason="tool_calls",
        )
    ]


def test_control_discovery_uses_declared_sources_without_granting_displayed_actor_control():
    owner = QueueOwner()
    before = deepcopy(owner.panels)
    result = tool(owner, "read_action_sequence_controls", {})
    assert result["player_control"] == before["player_control"]
    assert [row["source"] for row in result["controls"]] == [step["source"] for step in STEPS]
    assert "enemy" not in [row["id"] for row in result["player_control"]["actors"]]
    assert result["surfaces"] == before["surfaces"]
    assert owner.panels == before
    assert owner.events == () and owner.revision == 4


def test_compose_and_read_forward_exact_intentions_and_versions_without_executing():
    owner = QueueOwner()
    composed = tool(
        owner,
        "compose_action_sequence",
        {"sequence_id": "intent-1", "expected_revision": 4, "steps": STEPS},
    )
    assert composed["result"]["status"] == "ready"
    assert owner.calls == [
        (
            "compose",
            {
                "campaign_id": CAMPAIGN,
                "session_id": SESSION,
                "sequence_id": "intent-1",
                "expected_revision": 4,
                "expected_version": 0,
                "steps": STEPS,
            },
        )
    ]
    assert (
        tool(owner, "read_action_sequence", {"sequence_id": "intent-1"})["result"] == owner.sequence
    )
    assert owner.events == () and owner.revision == 4


def test_execute_uses_only_trusted_agent_entry_and_preserves_confirmation():
    owner = QueueOwner()
    result = tool(
        owner,
        "execute_action_sequence",
        {"sequence_id": "intent-1", "expected_version": 1, "confirmation": "queue-confirmation"},
    )
    assert result["result"]["status"] == "completed"
    assert owner.calls[0] == (
        "execute",
        {
            "campaign_id": CAMPAIGN,
            "session_id": SESSION,
            "sequence_id": "intent-1",
            "expected_version": 1,
            "confirmation": "queue-confirmation",
        },
    )


@pytest.mark.parametrize(
    "parameters",
    [
        {
            "sequence_id": "intent-1",
            "expected_revision": 4,
            "steps": STEPS,
            "session_id": "foreign",
        },
        {
            "sequence_id": "intent-1",
            "expected_revision": 4,
            "steps": [
                {
                    "step_id": "raw",
                    "source": {"kind": "mechanic", "tool_name": "attack"},
                    "selection": {},
                }
            ],
        },
        {"sequence_id": "intent-1", "expected_revision": True, "steps": STEPS},
    ],
)
def test_foreign_fields_and_raw_mechanics_never_reach_owner(parameters):
    owner = QueueOwner()
    with pytest.raises((CommandRejected, MechanicsRejected)):
        tool(owner, "compose_action_sequence", parameters)
    assert owner.calls == []


def test_sequence_tools_require_matching_active_campaign_context():
    owner = QueueOwner()
    owner.session_context = lambda session_id: SimpleNamespace(
        session=SimpleNamespace(campaign_id="foreign")
    )
    with pytest.raises(CommandRejected):
        tool(owner, "execute_action_sequence", {"sequence_id": "intent-1", "expected_version": 1})
    assert owner.calls == []


def test_sequence_schema_uses_the_shared_service_step_bound():
    tools = action_sequence_model_tools(cast(AuthorityOwner, QueueOwner()))
    compose = next(tool for tool in tools if tool.name == "compose_action_sequence")
    steps = compose.input_schema["properties"]["steps"]
    assert steps["minItems"] == 0
    assert steps["maxItems"] == 32


def test_sequence_schema_has_no_raw_mechanics_or_session_authority_arguments():
    tools = action_sequence_model_tools(cast(AuthorityOwner, QueueOwner()))
    assert {row.name for row in tools} == {
        "read_action_sequence_controls",
        "read_action_sequence",
        "compose_action_sequence",
        "execute_action_sequence",
    }
    assert all(
        "campaign_id" not in row.input_schema["properties"]
        and "session_id" not in row.input_schema["properties"]
        for row in tools
    )
    assert action_sequence_model_tools(cast(AuthorityOwner, object())) == ()


@pytest.mark.parametrize("status", ["completed", "paused", "failed", "stale"])
def test_adapter_runs_ordered_queue_then_one_combined_response_and_refreshes_catalog(status):
    owner = QueueOwner(status)
    provider = _ScriptedProvider(
        [
            call("read_action_sequence_controls", {}, "read-controls"),
            call(
                "compose_action_sequence",
                {"sequence_id": "intent-1", "expected_revision": 4, "steps": STEPS},
                "compose",
            ),
            call(
                "execute_action_sequence",
                {
                    "sequence_id": "intent-1",
                    "expected_version": 1,
                    "confirmation": "queue-confirmation",
                },
                "execute",
            ),
            call(
                "update_campaign_file",
                {
                    "revised_player_facing_response": [
                        {
                            "kind": "narration",
                            "text": "The committed actions are described together.",
                        }
                    ]
                },
                "finish",
            ),
        ]
    )
    discovered = []
    source = _provider_input(
        user_text="Move Hero, then have Companion aim. Describe both afterward."
    )
    value = replace(
        source,
        catalog=(),
        catalog_supplier=lambda identity: (
            discovered.append((identity, owner.revision))
            or (_campaign_file_tool(allow_unchanged=True),)
        ),
        narrative_writer=lambda parameters: {"status": "campaign_file_unchanged"},
    )
    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner), provider, owner_id="owner-provider"
        ).for_turn("turn-provider")(value)
    )
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        "The committed actions are described together."
    ]
    assert not any(event.kind is ProviderEventKind.ACTION for event in events), (
        "Queue service owns commits"
    )
    assert len(discovered) == 4 and discovered[-1][1] == owner.revision
    final_messages = provider.calls[-1][0]
    exact = next(
        row["content"]
        for row in final_messages
        if str(row.get("content", "")).startswith("SYLVIA_GM_EXACT_NARRATION_FACTS")
    )
    assert "committed-move" in exact
    if status == "completed":
        assert "committed-aim" in exact
    else:
        names = [row["function"]["name"] for row in provider.calls[-1][1]]
        assert not any(name.endswith("_execute_action_sequence") for name in names)
        assert any(
            "SYLVIA_ACTION_SEQUENCE_STOP" in str(row.get("content", "")) for row in final_messages
        )
    assert sum(name == "execute" for name, _ in owner.calls) == 1


def test_same_catalog_batch_cannot_execute_queue_twice():
    owner = QueueOwner()
    first = call("execute_action_sequence", {"sequence_id": "intent-1", "expected_version": 1})[0]
    second = ProviderStreamChunk(
        tool_call_delta={
            "index": 1,
            "id": "double",
            "function": {
                "name": "execute_action_sequence",
                "arguments": json.dumps({"sequence_id": "intent-1", "expected_version": 1}),
            },
        },
        finish_reason="tool_calls",
    )
    provider = _ScriptedProvider(
        [
            [first, second],
            call(
                "update_campaign_file",
                {"revised_player_facing_response": [{"kind": "narration", "text": "Once."}]},
            ),
        ]
    )
    source = _provider_input()
    value = replace(
        source,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        narrative_writer=lambda parameters: {"status": "campaign_file_unchanged"},
    )
    tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner), provider, owner_id="owner-provider"
        ).for_turn("turn-provider")(value)
    )
    assert sum(name == "execute" for name, _ in owner.calls) == 1
    assert any(
        "superseded_by_catalog_refresh" in str(row.get("content", ""))
        for row in provider.calls[-1][0]
    )


def test_read_between_mutations_cannot_reset_catalog_refresh_guard():
    owner = QueueOwner()
    chunks = []
    for index, name in enumerate(
        ("execute_action_sequence", "read_action_sequence", "execute_action_sequence")
    ):
        parameters = {"sequence_id": "intent-1"}
        if name.startswith("execute"):
            parameters["expected_version"] = 1
        chunks.append(
            ProviderStreamChunk(
                tool_call_delta={
                    "index": index,
                    "id": f"call-{index}",
                    "function": {"name": name, "arguments": json.dumps(parameters)},
                },
                finish_reason="tool_calls",
            )
        )
    provider = _ScriptedProvider(
        [
            chunks,
            call(
                "update_campaign_file",
                {"revised_player_facing_response": [{"kind": "narration", "text": "Once."}]},
            ),
        ]
    )
    source = _provider_input()
    value = replace(
        source,
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        narrative_writer=lambda parameters: {"status": "campaign_file_unchanged"},
    )
    tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner), provider, owner_id="owner-provider"
        ).for_turn("turn-provider")(value)
    )
    assert [name for name, _ in owner.calls] == ["execute", "read"]


def test_saved_chat_queue_results_do_not_absorb_later_control_outcomes():
    from sylvia_gm.agent._provider_session import _saved_action_sequence_results
    from sylvia_gm.domain.session import SessionContext, SessionMessage, SessionMessageKind

    owner = QueueOwner()
    saved = tool(
        owner, "execute_action_sequence", {"sequence_id": "intent-1", "expected_version": 1}
    )
    records = [
        {"action_id": identity} for identity in ("committed-move", "committed-aim", "later-move")
    ]
    owner.player_action_context = lambda campaign_id: records
    owner.player_action_response_context = lambda campaign_id, basis: [
        row for row in records if row["action_id"] in basis["player_action_ids"]
    ]
    source = _provider_input()
    messages = [*source.history.messages]
    for index, basis_id in enumerate(("message-provider", "later-message")):
        payload = deepcopy(saved)
        if index:
            payload["result"]["committed_action_ids"] = ["later-move"]
        messages.append(
            SessionMessage(
                session_id=SESSION,
                sequence=index + 2,
                message_id=f"results-{index}",
                kind=SessionMessageKind.EVIDENCE,
                content={
                    "kind": "provider_tool_results",
                    "basis_message_id": basis_id,
                    "results": [{"role": "tool", "content": json.dumps(payload)}],
                },
            )
        )
    value = replace(
        source,
        basis_message_id="message-provider",
        history=SessionContext(session=source.session, messages=tuple(messages)),
    )
    assert _saved_action_sequence_results(value)[0]["committed_action_ids"] == [
        "committed-move",
        "committed-aim",
    ]
    projected = ProviderSessionAdapter(
        cast(AuthorityOwner, owner), _ScriptedProvider([]), owner_id="owner-provider"
    )._messages(value)
    controlled = next(
        row["content"]
        for row in projected
        if str(row.get("content", "")).startswith("SYLVIA_PLAYER_CONTROL_RESULTS")
    )
    assert "committed-move" in controlled and "committed-aim" in controlled
    assert "later-move" not in controlled


def test_actorless_declared_control_accepts_null_selection_without_inventing_actor():
    owner = QueueOwner()
    step = {
        "step_id": "explicit-turn",
        "source": {"kind": "panel", "surface_id": "map", "action_id": "end-turn"},
        "selection": None,
    }
    tool(
        owner,
        "compose_action_sequence",
        {"sequence_id": "turn-intent", "expected_revision": 4, "steps": [step]},
    )
    assert owner.calls[0][1]["steps"] == [step]


def test_real_td_queue_provider_commits_multiple_controlled_actors_then_one_saved_response(td):
    from sylvia_gm.agent._provider_session import _saved_action_sequence_results
    from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
    from tests.test_action_sequences import GM, TD, td_step

    before = td.snapshot()
    td.close()
    with AuthorityOwner.open(td.path, td.owner_id, disconnect_grace_seconds=1.0) as owner:
        steps = [
            td_step("hex-down", "stance-crouched"),
            td_step("vesper-down", "stance-crouched", "vesper"),
        ]
        sequence_id = "chat-native-sequence"

        class NativeProvider(_ScriptedProvider):
            async def generate_stream(self, messages, tools=None, **kwargs):
                if len(self.calls) == 2:
                    queue = owner.action_sequence(session_id=GM, sequence_id=sequence_id)
                    self.rounds[0] = call(
                        "execute_action_sequence",
                        {
                            "sequence_id": sequence_id,
                            "expected_version": queue["version"],
                            "confirmation": queue["confirmation"],
                        },
                        "execute-native",
                    )
                async for chunk in super().generate_stream(messages, tools, **kwargs):
                    yield chunk

        provider = NativeProvider(
            [
                call("read_action_sequence_controls", {}, "discover-native"),
                call(
                    "compose_action_sequence",
                    {
                        "sequence_id": sequence_id,
                        "expected_revision": before.revision,
                        "steps": steps,
                    },
                    "compose-native",
                ),
                [],
                call(
                    "update_campaign_file",
                    {
                        "state_change": "unchanged",
                        "state_change_summary": "The native stance outcomes are already saved.",
                        "revised_player_facing_response": [
                            {
                                "kind": "narration",
                                "text": "Hex and Vesper lower into crouches together.",
                            }
                        ],
                    },
                    "narrate-native",
                ),
            ]
        )
        adapter = ProviderSessionAdapter(owner, provider, owner_id=td.owner_id)
        result = CampaignSessionLoop(owner).run(
            SessionRunRequest(
                session_id=GM,
                turn_id="turn",
                attempt_id="native-chat-sequence",
                message_id="native-chat-input",
                user_text="Hex crouches, then Vesper crouches. Describe both together.",
            ),
            adapter.for_turn("turn"),
        )
        queue = owner.action_sequence(session_id=GM, sequence_id=sequence_id)
        assert queue["status"] == "completed"
        assert len(queue["committed_action_ids"]) == 2
        after = owner.snapshot(td.campaign_id)
        assert (
            after.state[TD + ".entities"]["hex"]["ap"]
            == before.state[TD + ".entities"]["hex"]["ap"] - 1
        )
        assert (
            after.state[TD + ".entities"]["vesper"]["ap"]
            == before.state[TD + ".entities"]["vesper"]["ap"] - 1
        )
        narration = [
            event for event in result.canonical if event.event_type is CanonicalEventType.TALK
        ]
        assert (
            len(narration) == 1
            and narration[0].safe_payload["text"] == "Hex and Vesper lower into crouches together."
        )
        source = replace(
            _provider_input(),
            session=owner.session_context(GM).session,
            history=owner.session_context(GM),
            basis_message_id="native-chat-input",
        )
        assert _saved_action_sequence_results(source)[0]["committed_action_ids"] == list(
            queue["committed_action_ids"]
        )
    with AuthorityOwner.open(td.path, td.owner_id, disconnect_grace_seconds=1.0) as owner:
        assert owner.snapshot(td.campaign_id) == after
        assert owner.action_sequence(session_id=GM, sequence_id=sequence_id) == queue


def test_uncertain_execute_reply_recovers_saved_receipts_without_resubmission():
    class UncertainOwner(QueueOwner):
        def _execute_action_sequence_from_agent(self, **parameters):
            super()._execute_action_sequence_from_agent(**parameters)
            raise CommandRejected("Synthetic reply loss after saved mechanics", code="reply_lost")

    owner = UncertainOwner()
    provider = _ScriptedProvider(
        [
            call("execute_action_sequence", {"sequence_id": "intent-1", "expected_version": 1}),
            call(
                "update_campaign_file",
                {
                    "revised_player_facing_response": [
                        {"kind": "narration", "text": "Both saved outcomes."}
                    ]
                },
            ),
        ]
    )
    value = replace(
        _provider_input(),
        catalog=(_campaign_file_tool(allow_unchanged=True),),
        narrative_writer=lambda parameters: {"status": "campaign_file_unchanged"},
    )
    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner), provider, owner_id="owner-provider"
        ).for_turn("turn-provider")(value)
    )
    assert [name for name, _ in owner.calls] == ["execute", "read"]
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        "Both saved outcomes."
    ]
    payload = next(
        row for row in provider.calls[-1][0] if "reply_lost" in str(row.get("content", ""))
    )
    assert "committed-move" in payload["content"] and "committed-aim" in payload["content"]
    assert '"state_changed":false' not in payload["content"]
    assert not any(
        row["function"]["name"].endswith("_execute_action_sequence")
        for row in provider.calls[-1][1]
    )


def narration_call(text):
    return call(
        "update_campaign_file",
        {
            "state_change": "unchanged",
            "state_change_summary": "The native action outcomes are already saved.",
            "revised_player_facing_response": [{"kind": "narration", "text": text}],
        },
        "narrate-saved",
    )


class NativeIntentProvider(_ScriptedProvider):
    """Script the provider only; discovery, controls and native mechanics are real."""

    def __init__(
        self, owner, session_id, sequence_id, revision, steps, text, *, fail_narration=False
    ):
        super().__init__(
            [
                call(
                    "compose_action_sequence",
                    {"sequence_id": sequence_id, "expected_revision": revision, "steps": steps},
                    "compose-native",
                ),
                [],
                narration_call(text),
            ]
        )
        self.owner = owner
        self.session_id = session_id
        self.sequence_id = sequence_id
        self.fail_narration = fail_narration
        self.composed_queue = None

    async def generate_stream(self, messages, tools=None, **kwargs):
        if len(self.calls) == 1:
            queue = self.owner.action_sequence(
                session_id=self.session_id, sequence_id=self.sequence_id
            )
            self.composed_queue = queue
            self.rounds[0] = call(
                "execute_action_sequence",
                {
                    "sequence_id": self.sequence_id,
                    "expected_version": queue["version"],
                    "confirmation": queue["confirmation"],
                },
                "execute-native",
            )
        if self.fail_narration and len(self.calls) == 2:
            from sylvia_gm.provider._exceptions import ProviderConnectionError

            raise ProviderConnectionError(
                "Synthetic narration connection failure after native commits"
            )
        async for chunk in super().generate_stream(messages, tools, **kwargs):
            yield chunk


def controlled_result_ids(messages):
    text = next(
        row["content"]
        for row in messages
        if str(row.get("content", "")).startswith("SYLVIA_PLAYER_CONTROL_RESULTS")
    )
    return [row["action_id"] for row in json.loads(text[text.index("[") :])]


def test_real_lite_provider_stops_at_spent_action_and_describes_only_committed_prefix(lite):
    from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
    from tests.test_action_sequences import GM, NS, lite_step

    before = lite.snapshot()
    steps = [
        lite_step(lite, "cast-once", "cast-magic_missile"),
        lite_step(lite, "cast-again", "cast-magic_missile"),
        lite_step(lite, "move-later", "move-walk", destination=[0, 5, 0]),
    ]
    lite.close()
    with AuthorityOwner.open(lite.path, "lite-owner", disconnect_grace_seconds=1.0) as owner:
        provider = NativeIntentProvider(
            owner,
            GM,
            "lite-chat-native",
            before.revision,
            steps,
            "The first Magic Missile lands. Another cast awaits a fresh action.",
        )
        adapter = ProviderSessionAdapter(owner, provider, owner_id="lite-owner")
        result = CampaignSessionLoop(owner).run(
            SessionRunRequest(
                session_id=GM,
                turn_id="turn",
                attempt_id="lite-chat",
                message_id="lite-chat-input",
                user_text=(
                    "Cast Magic Missile twice, then move five feet. Describe the results together."
                ),
            ),
            adapter.for_turn("turn"),
        )
        queue = owner.action_sequence(session_id=GM, sequence_id="lite-chat-native")
        assert queue["status"] == "failed"
        assert [row["step_id"] for row in queue["steps"] if row["status"] == "committed"] == [
            "cast-once"
        ]
        assert queue["steps"][1].get("error") and queue["steps"][2]["status"] == "pending"
        after = owner.snapshot("lite")
        assert len(after.actions) == len(before.actions) + 1
        assert after.state[NS + ".entities"]["hero"]["resources"]["spell_slot_1"] == (
            before.state[NS + ".entities"]["hero"]["resources"]["spell_slot_1"] - 1
        )
        assert after.state[NS + ".combat"]["turn_active"] is True
        assert after.state[NS + ".combat"]["action_available"] is False
        narrations = [
            event for event in result.canonical if event.event_type is CanonicalEventType.TALK
        ]
        assert len(narrations) == 1
        messages = provider.calls[-1][0]
        facts = next(
            row["content"]
            for row in messages
            if str(row.get("content", "")).startswith("SYLVIA_GM_EXACT_NARRATION_FACTS")
        )
        assert all(identity in facts for identity in queue["committed_action_ids"])
        assert queue["steps"][1]["operation_id"] not in facts
        assert any("SYLVIA_ACTION_SEQUENCE_STOP" in str(row.get("content", "")) for row in messages)
        assert not any(
            row["function"]["name"].endswith("_execute_action_sequence")
            for row in provider.calls[-1][1]
        )
    with AuthorityOwner.open(lite.path, "lite-owner", disconnect_grace_seconds=1.0) as owner:
        assert owner.snapshot("lite") == after
        assert owner.action_sequence(session_id=GM, sequence_id="lite-chat-native") == queue


@pytest.mark.parametrize(
    "interruption_mode",
    [
        "failed_narration",
        "stop_before_tool_evidence",
        "stop_before_queue_receipt",
    ],
)
def test_real_td_saved_retry_freezes_queue_range_after_commit_and_reopen(
    td, interruption_mode, monkeypatch
):
    from contextlib import ExitStack

    from sylvia_gm.agent._provider_session import _saved_action_sequence_results
    from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
    from sylvia_gm.provider._exceptions import ProviderConnectionError
    from tests.test_action_sequences import GM, TD, td_step

    before = td.snapshot()
    td.close()
    original_basis = "interrupted-queue-input"
    original_sequence = "interrupted-chat-native"
    with ExitStack() as owned_contexts:
        owner = owned_contexts.enter_context(
            AuthorityOwner.open(td.path, td.owner_id, disconnect_grace_seconds=1.0)
        )
        loop = CampaignSessionLoop(owner)
        provider = NativeIntentProvider(
            owner,
            GM,
            original_sequence,
            before.revision,
            [
                td_step("hex-down", "stance-crouched"),
                td_step("vesper-down", "stance-crouched", "vesper"),
            ],
            "Hex and Vesper crouch together.",
            fail_narration=interruption_mode == "failed_narration",
        )
        adapter = ProviderSessionAdapter(owner, provider, owner_id=td.owner_id)
        actual_provider = adapter.for_turn("turn")
        if interruption_mode == "stop_before_queue_receipt":
            native_execute = owner._execute_action

            def stop_after_native_commit(command, **kwargs):
                receipt = native_execute(command, **kwargs)
                if (
                    provider.composed_queue is not None
                    and command.action_id == provider.composed_queue["steps"][0]["operation_id"]
                ):
                    saved_intention = next(
                        message.content
                        for message in reversed(owner.session_context(GM).messages)
                        if message.content.get("kind") == "action_sequence"
                    )
                    assert saved_intention["steps"][0]["status"] == "executing"
                    assert saved_intention["steps"][0].get("execution")
                    assert saved_intention["narration_basis_message_id"] == original_basis
                    assert saved_intention["narration_attempt_id"] == "interrupted-queue-attempt"
                    loop.stop(GM)
                    raise CommandRejected("Synthetic Stop after native commit before queue receipt")
                return receipt

            monkeypatch.setattr(owner, "_execute_action", stop_after_native_commit)

        def stop_before_tool_evidence(value):
            for event in actual_provider(value):
                if (
                    event.kind is ProviderEventKind.EVIDENCE
                    and event.evidence.get("kind") == "provider_tool_results"
                    and any(
                        "execute_action_sequence" in str(row.get("content", ""))
                        for row in event.evidence.get("results", ())
                    )
                ):
                    loop.stop(GM)
                    raise CommandRejected(
                        "Synthetic Stop after native commit before adapter evidence"
                    )
                yield event

        request = SessionRunRequest(
            session_id=GM,
            turn_id="turn",
            attempt_id="interrupted-queue-attempt",
            message_id=original_basis,
            user_text="Hex crouches, then Vesper crouches. Describe both afterward.",
        )
        if interruption_mode == "failed_narration":
            with pytest.raises(ProviderConnectionError):
                loop.run(request, actual_provider)
        else:
            stopped = loop.run(
                request,
                stop_before_tool_evidence
                if interruption_mode == "stop_before_tool_evidence"
                else actual_provider,
            )
            assert stopped.interruption is not None
            assert not any(
                event.event_type is CanonicalEventType.TALK for event in stopped.canonical
            )
            stopped_snapshot = owner.snapshot(td.campaign_id)
            stopped_context = owner.session_context(GM)
            assert stopped_context.session.status.value == "stopped"
            stopped_queue = owner.action_sequence(session_id=GM, sequence_id=original_sequence)
            assert owner.snapshot(td.campaign_id) == stopped_snapshot
            assert owner.session_context(GM) == stopped_context, (
                "Stopped read must not append session evidence"
            )
            owner.close()
            owner = owned_contexts.enter_context(
                AuthorityOwner.open(td.path, td.owner_id, disconnect_grace_seconds=1.0)
            )
            assert owner.snapshot(td.campaign_id) == stopped_snapshot
            assert owner.session_context(GM) == stopped_context
            assert (
                owner.action_sequence(session_id=GM, sequence_id=original_sequence) == stopped_queue
            )
            assert owner.snapshot(td.campaign_id) == stopped_snapshot
            assert owner.session_context(GM) == stopped_context
            expected_steps = (
                ["hex-down"]
                if interruption_mode == "stop_before_queue_receipt"
                else ["hex-down", "vesper-down"]
            )
            assert [
                row["step_id"] for row in stopped_queue["steps"] if row["status"] == "committed"
            ] == expected_steps
            if len(expected_steps) == 1:
                assert stopped_queue["steps"][1]["status"] == "pending"
            owner.resume_agent_session(GM)
        queue = owner.action_sequence(session_id=GM, sequence_id=original_sequence)
        if interruption_mode != "failed_narration":
            for key in (
                "steps",
                "status",
                "committed_action_ids",
                "expected_revision",
                "event_watermark",
                "scope",
                "turn_id",
                "narration_basis_message_id",
                "narration_attempt_id",
            ):
                assert queue[key] == stopped_queue[key], (
                    "Resume must preserve native outcomes and intentions"
                )
            if interruption_mode == "stop_before_queue_receipt":
                assert queue["version"] == stopped_queue["version"] + 1
                assert queue["confirmation"] != stopped_queue["confirmation"]
            else:
                assert queue == stopped_queue
            assert owner.snapshot(td.campaign_id) == stopped_snapshot
        original_ids = list(queue["committed_action_ids"])
        committed_count = 1 if interruption_mode == "stop_before_queue_receipt" else 2
        assert len(original_ids) == committed_count
        if committed_count == 1:
            assert queue["steps"][0]["status"] == "committed"
            assert queue["steps"][1]["status"] == "pending"
        after_commit = owner.snapshot(td.campaign_id)
        assert len(after_commit.actions) == len(before.actions) + committed_count
        assert (
            after_commit.state[TD + ".entities"]["hex"]["ap"]
            == before.state[TD + ".entities"]["hex"]["ap"] - 1
        )
        assert after_commit.state[TD + ".entities"]["vesper"]["ap"] == (
            before.state[TD + ".entities"]["vesper"]["ap"] - (committed_count - 1)
        )
        interruption = owner.retryable_interruption(
            td.campaign_id, failed_attempt_id="interrupted-queue-attempt"
        )
        assert interruption.last_action_id == original_ids[-1], (
            "Retry must recover the last native commit"
        )
        if interruption_mode == "stop_before_tool_evidence":
            assert not any(
                message.content.get("kind") == "provider_tool_results"
                and any(
                    "execute_action_sequence" in str(row.get("content", ""))
                    for row in message.content.get("results", ())
                )
                for message in owner.session_context(GM).messages
            )
        later = owner.compose_action_sequence(
            campaign_id=td.campaign_id,
            session_id=GM,
            sequence_id="later-native",
            expected_revision=after_commit.revision,
            steps=[td_step("later-stand", "stance-standing")],
        )
        later = owner.execute_action_sequence(
            campaign_id=td.campaign_id,
            session_id=GM,
            sequence_id=later["sequence_id"],
            expected_version=later["version"],
            confirmation=later["confirmation"],
        )
        later_ids = list(later["committed_action_ids"])
        assert len(later_ids) == 1 and not set(original_ids).intersection(later_ids)
        before_retry = owner.snapshot(td.campaign_id)
        assert [
            row["action_id"] for row in owner.player_action_context(td.campaign_id)
        ] == later_ids

    with AuthorityOwner.open(td.path, td.owner_id, disconnect_grace_seconds=1.0) as owner:
        assert owner.snapshot(td.campaign_id) == before_retry
        assert owner.action_sequence(session_id=GM, sequence_id=original_sequence) == queue
        source = replace(
            _provider_input(),
            session=owner.session_context(GM).session,
            history=owner.session_context(GM),
            basis_message_id=original_basis,
        )
        recovered = _saved_action_sequence_results(source, owner)
        assert [
            identity for row in recovered for identity in row["committed_action_ids"]
        ] == original_ids
        retry_text = (
            "Hex lowers into a crouch."
            if committed_count == 1
            else "Hex and Vesper lower into crouches."
        )
        retry_provider = _ScriptedProvider([narration_call(retry_text)])
        retry_adapter = ProviderSessionAdapter(owner, retry_provider, owner_id=td.owner_id)
        result = CampaignSessionLoop(owner).retry(
            GM,
            interruption_id=interruption.interruption_id,
            retry_id="queue-saved-retry",
            narrator=lambda *args: pytest.fail("The actual tool-capable adapter is required"),
            provider_factory=retry_adapter.for_turn,
        )
        assert controlled_result_ids(retry_provider.calls[0][0]) == original_ids
        assert not any(
            row["function"]["name"].endswith("_execute_action_sequence")
            for row in retry_provider.calls[0][1]
        ), "Retry cannot resubmit the native sequence"
        after_retry = owner.snapshot(td.campaign_id)
        assert (
            after_retry.state == before_retry.state and after_retry.actions == before_retry.actions
        )
        assert [
            row["action_id"] for row in owner.player_action_context(td.campaign_id)
        ] == later_ids
        assert (
            owner.campaign_panels(td.campaign_id)["response_action"]["action_id"] == later_ids[-1]
        )
        assert [
            event.safe_payload["text"]
            for event in result.canonical
            if event.event_type is CanonicalEventType.TALK
        ] == [retry_text]
        replay = CampaignSessionLoop(owner).retry(
            GM,
            interruption_id=interruption.interruption_id,
            retry_id="queue-saved-retry",
            narrator=lambda *args: pytest.fail("An already saved Retry must replay"),
            provider_factory=lambda turn: pytest.fail("No second provider call is permitted"),
        )
        assert replay.replayed
        assert owner.snapshot(td.campaign_id) == after_retry
    with AuthorityOwner.open(td.path, td.owner_id, disconnect_grace_seconds=1.0) as owner:
        assert owner.snapshot(td.campaign_id) == after_retry
        assert [
            row["action_id"] for row in owner.player_action_context(td.campaign_id)
        ] == later_ids


def test_ephemeral_preflight_has_no_admitted_queue_results(td):
    from sylvia_gm.agent._provider_session import _saved_action_sequence_results
    from sylvia_gm.domain.session import SessionContext, SessionMessage, SessionMessageKind
    from tests.test_action_sequences import GM

    before = td.snapshot()
    session = replace(
        td.owner.session_context(GM).session, session_id="synthetic-preflight-session"
    )
    history = SessionContext(
        session=session,
        messages=(
            SessionMessage(
                session_id=session.session_id,
                sequence=1,
                message_id="preflight-campaign-start",
                kind=SessionMessageKind.EVIDENCE,
                content={"kind": "campaign_start", "event": "campaign_start"},
            ),
        ),
    )
    value = replace(
        _provider_input(),
        session=session,
        history=history,
        basis_message_id="preflight-campaign-start",
    )
    assert _saved_action_sequence_results(value, td.owner) == ()
    assert td.snapshot() == before


@pytest.mark.parametrize("campaign_id,skill", [(None, None), (CAMPAIGN, "BUILD CAMPAIGN PLAN")])
def test_non_dm_input_does_not_read_campaign_queue_authority(campaign_id, skill):
    from sylvia_gm.agent._provider_session import _saved_action_sequence_results
    from sylvia_gm.domain.authority import WriterRole
    from sylvia_gm.domain.session import SessionContext

    owner = QueueOwner()
    owner.action_sequence_response_results = lambda **kwargs: pytest.fail(
        "Builder projection must not acquire campaign queue authority"
    )
    source = _provider_input()
    session = replace(
        source.session, role=WriterRole.BUILDER, campaign_id=campaign_id, selected_skill=skill
    )
    value = replace(
        source,
        session=session,
        history=SessionContext(session=session, messages=source.history.messages),
    )
    assert _saved_action_sequence_results(value, owner) == ()


def test_real_lite_discovery_supplies_every_required_map_selection(lite):
    """A provider can compose from discovery without hidden renderer knowledge."""
    from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
    from tests.test_action_sequences import GM, NS

    before = lite.snapshot()
    lite.close()
    with AuthorityOwner.open(lite.path, "lite-owner", disconnect_grace_seconds=1.0) as owner:

        class DiscoveredIntentProvider(_ScriptedProvider):
            def __init__(self):
                super().__init__(
                    [
                        call("read_action_sequence_controls", {}, "discover"),
                        [],
                        [],
                        narration_call("The move, cast and final move are recorded."),
                    ]
                )

            async def generate_stream(self, messages, tools=None, **kwargs):
                if len(self.calls) == 1:
                    discovered = next(
                        json.loads(row["content"])
                        for row in messages
                        if row.get("role") == "tool"
                        and isinstance(row.get("content"), str)
                        and '"action_sequence_controls"' in row["content"]
                    )
                    assert owner.snapshot("lite") == before
                    controls = {
                        row["source"]["action_id"]: row
                        for row in discovered["controls"]
                        if row["source"].get("surface_id") == "srd-coordinate-map"
                    }
                    steps = []
                    for step_id, action_id, intent in [
                        ("first", "move-walk", {"actor": "hero", "aim_point": [0, 5, 0]}),
                        ("cast", "cast-magic_missile", {"actor": "hero", "target": "guard"}),
                        ("last", "move-walk", {"actor": "hero", "aim_point": [0, 10, 0]}),
                    ]:
                        control = controls[action_id]
                        contract = control["selection_contract"]
                        selection = dict(intent, **contract["context"])
                        assert set(selection) == set(contract["required_keys"])
                        steps.append(
                            {
                                "step_id": step_id,
                                "source": control["source"],
                                "selection": selection,
                            }
                        )
                    self.rounds[0] = call(
                        "compose_action_sequence",
                        {
                            "sequence_id": "discovered-native",
                            "expected_revision": discovered["revision"],
                            "steps": steps,
                        },
                        "compose-discovered",
                    )
                elif len(self.calls) == 2:
                    assert owner.snapshot("lite") == before
                    queue = owner.action_sequence(session_id=GM)
                    self.rounds[0] = call(
                        "execute_action_sequence",
                        {
                            "sequence_id": queue["sequence_id"],
                            "expected_version": queue["version"],
                            "confirmation": queue["confirmation"],
                        },
                        "execute-discovered",
                    )
                async for chunk in super().generate_stream(messages, tools, **kwargs):
                    yield chunk

        provider = DiscoveredIntentProvider()
        adapter = ProviderSessionAdapter(owner, provider, owner_id="lite-owner")
        result = CampaignSessionLoop(owner).run(
            SessionRunRequest(
                session_id=GM,
                turn_id="turn",
                attempt_id="discovered-map-attempt",
                message_id="discovered-map-input",
                user_text="Move five feet, cast, then move five feet.",
            ),
            adapter.for_turn("turn"),
        )
        assert result.interruption is None
        queue = owner.action_sequence(session_id=GM)
        assert queue["status"] == "completed"
        assert [row["status"] for row in queue["steps"]] == ["committed"] * 3
        after = owner.snapshot("lite")
        assert len(after.actions) == len(before.actions) + 3
        assert after.state[NS + ".entities"]["hero"]["resources"]["spell_slot_1"] == (
            before.state[NS + ".entities"]["hero"]["resources"]["spell_slot_1"] - 1
        )
        assert after.state[NS + ".combat"]["movement_remaining"] == 20
        assert after.state[NS + ".combat"]["turn_active"] is True
        assert next(
            row["position"]
            for row in after.state[NS + ".state"]["spatial_scene"]["entities"]
            if row["id"] == "hero"
        ) == [0, 10, 0]
        assert (
            len(
                [event for event in result.canonical if event.event_type is CanonicalEventType.TALK]
            )
            == 1
        )
    with AuthorityOwner.open(lite.path, "lite-owner", disconnect_grace_seconds=1.0) as owner:
        assert owner.snapshot("lite") == after
        assert owner.action_sequence(session_id=GM) == queue


def test_real_lite_longsword_then_end_turn_has_one_combined_narration(tmp_path):
    from sylvia_gm.agent._session_loop import CampaignSessionLoop, SessionRunRequest
    from tests.test_action_sequences import GM, NS, create_session, lite_step
    from tests.test_lite_panel_combat import case

    value = case(tmp_path / "longsword-chat.sqlite3", melee_weapon="longsword")
    try:
        create_session(value.owner, "lite")
        before = value.snapshot()
        steps = [
            lite_step(value, "sword", "attack-melee-strength"),
            lite_step(value, "end", "end-turn"),
        ]
        value.close()
        with AuthorityOwner.open(value.path, "lite-owner", disconnect_grace_seconds=1.0) as owner:
            provider = NativeIntentProvider(
                owner,
                GM,
                "sword-and-end",
                before.revision,
                steps,
                "The long sword strikes the guard. The guard hesitates; your next turn begins.",
            )
            # The ordinary GM policy completes its NPC turn before the one final response.
            # Choosing to finish that NPC turn adds no player intention or player cost.
            provider.rounds.insert(2, call("end_combat_turn", {}, "guard-finishes"))
            result = CampaignSessionLoop(owner).run(
                SessionRunRequest(
                    session_id=GM,
                    turn_id="turn",
                    attempt_id="sword-chat",
                    message_id="sword-input",
                    user_text="I attack with my long sword and then end my turn.",
                ),
                ProviderSessionAdapter(owner, provider, owner_id="lite-owner").for_turn("turn"),
            )
            queue = owner.action_sequence(session_id=GM)
            assert [step["status"] for step in queue["steps"]] == ["committed", "committed"]
            assert [step["tool"] for step in queue["steps"]] == [
                "resolve_combat_attack",
                "end_combat_turn",
            ]
            assert queue["steps"][-1]["response_policy"] == "automatic"
            after = owner.snapshot("lite")
            new_actions = after.actions[len(before.actions) :]
            assert [action.action_id for action in new_actions[:2]] == list(
                queue["committed_action_ids"]
            )
            assert [action.tool_name for action in new_actions].count("resolve_combat_attack") == 1
            assert all(
                sum(action.action_id == identity for action in new_actions) == 1
                for identity in queue["committed_action_ids"]
            )
            assert after.state[NS + ".combat"]["turn_active"] is True
            assert after.state[NS + ".combat"]["active_actor"] == "hero"
            assert after.state[NS + ".combat"]["action_available"] is True
            assert (
                len(
                    [
                        event
                        for event in result.canonical
                        if event.event_type is CanonicalEventType.TALK
                    ]
                )
                == 1
            )
            assert all(
                identity in str(provider.calls[-1][0]) for identity in queue["committed_action_ids"]
            )
        value.open()
        assert value.snapshot() == after
        assert value.owner.action_sequence(session_id=GM) == queue
    finally:
        value.close()


def test_adapter_does_not_advertise_queue_tools_for_a_no_rules_campaign():
    owner = QueueOwner()
    ordinary_snapshot = owner.snapshot

    def no_rules_snapshot(campaign_id):
        snapshot = ordinary_snapshot(campaign_id)
        snapshot.package_lock = PackageLock(())
        return snapshot

    owner.snapshot = no_rules_snapshot
    provider = _ScriptedProvider(
        [[ProviderStreamChunk(content="The conversation continues.", finish_reason="stop")]]
    )
    source = replace(_provider_input(), catalog=())
    events = tuple(
        ProviderSessionAdapter(
            cast(AuthorityOwner, owner),
            provider,
            owner_id="no-rules-queue",
        ).for_turn("turn-provider")(source)
    )
    assert len(provider.calls) == 1
    assert not provider.calls[0][1]
    assert owner.calls == []
    assert [event.text for event in events if event.kind is ProviderEventKind.TALK] == [
        "The conversation continues."
    ]
