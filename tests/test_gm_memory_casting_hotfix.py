"""GM hotfix regressions through the real owner, saved state and provider loop."""

import io
import json
import re
import shutil
from importlib.resources import files
from pathlib import Path

import pytest

from sylvia_gm.agent._provider_session import (
    ProviderSessionAdapter,
    _derive_mechanics_parameters,
    _normalize_state_references,
)
from sylvia_gm.agent._session_loop import (
    CampaignSessionLoop,
    SessionRunRequest,
    _mechanics_rejection_feedback,
)
from sylvia_gm.application.authority import AuthorityOwner, CampaignCreate
from sylvia_gm.application.bundled_packages import bundled_srd_archive
from sylvia_gm.application.reusable_participants import validate_reusable_participant_draft
from sylvia_gm.domain.authority import (
    PackageRole,
    Participant,
    ParticipantValidationStatus,
    StateValue,
    WriterRole,
)
from sylvia_gm.domain.errors import MechanicsRejected
from sylvia_gm.domain.session import SessionMessageKind
from sylvia_gm.provider._base import ProviderStreamChunk
from sylvia_gm.runtime._runtime import RulesetRuntime
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import ToolDef
from tests.qa_attached_request import request_attached
from tests.test_alpha_f4_generic_sessions import _campaign
from tests.test_alpha_f6e_owner_session_transport import _OwnerHarness
from tests.test_builder_library import LibraryProvider
from tests.test_fc_campaign_journey import _start_campaign
from tests.test_srd_action_budget import _state

SRD = "sylvia.srd-lite.bg3"


def test_all_top_level_refusals_are_reported_before_effects():
    tool = ToolDef(
        name="blocked",
        pre_conditions=[
            {"condition": "false", "error": "First missing input."},
            {"condition": "false", "error": "Second missing input."},
        ],
        effects=[{"set": {"field": "_written", "value": True}}],
    )
    result = execute_tool("blocked", {}, {"blocked": tool}, None, {}, {})
    assert not result.success
    assert result.failure_reasons == ("First missing input.", "Second missing input.")
    assert "_written" not in result.context


def test_missing_derivation_source_names_the_input_and_uses_actual_feedback_fields():
    with pytest.raises(MechanicsRejected) as refused:
        _derive_mechanics_parameters(
            {},
            {"actor": {"kind": "copy", "source": "actor_state"}},
            (),
        )
    feedback = _mechanics_rejection_feedback(refused.value)
    assert feedback["code"] == "mechanics_derivation_source_missing"
    assert "actor_state" in feedback["reason"] and "actor" in feedback["reason"]
    assert feedback["validation"]["field_paths"] == ["actor_state"]
    assert "validation.issues" not in feedback["instruction"]


@pytest.mark.parametrize("display", ["Cure Wounds", "CURE-WOUNDS", "cure_wounds"])
def test_spell_name_forms_and_exact_participant_fill_preserve_authorization(display):
    schema = {
        "properties": {
            "spell": {"type": "string", "enum": ["cure_wounds"]},
            "caster": {"type": "string", "x-sylvia-gm-reference": "participant", "enum": ["hero"]},
            "caster_state": {"type": "string"},
        }
    }
    state = {"game.entities": {"hero": {}, "monster": {}}}
    assert _normalize_state_references(
        {"caster_state": "hero", "spell": display}, schema, state
    ) == {
        "caster_state": "hero",
        "caster": "hero",
        "spell": "cure_wounds",
    }
    assert "caster" not in _normalize_state_references({"caster_state": "monster"}, schema, state)
    assert _normalize_state_references(
        {"caster": "hero", "caster_state": "monster"}, schema, state
    ) == {
        "caster": "hero",
        "caster_state": "monster",
    }


def test_permanent_refusal_detection_requires_first_unconditional_literal_false():
    refusal = {"require": {"condition": "false", "error": "Unavailable"}}
    runtime = RulesetRuntime(
        None,
        {
            "tools": {
                "stub": {"effects": [refusal]},
                "stub_with_tail": {"effects": [refusal, {"set": {"field": "x", "value": 1}}]},
                "conditional": {"effects": [{"require": {"condition": "x == false"}}]},
                "later": {"effects": [{"set": {"field": "x", "value": 1}}, refusal]},
                "nested": {"effects": [{"if": {"condition": "x", "on_true": [refusal]}}]},
            }
        },
    )
    assert runtime.permanently_refused_tool_names == frozenset({"stub", "stub_with_tail"})


def create_cleric_save(database, archive):
    """Create an admitted actual starter Cleric against the selected retained package."""
    with AuthorityOwner.open(database, "hotfix-owner") as owner:
        candidate = owner.install_package_archive(io.BytesIO(archive), install_id="starter")
        primary = next(p for p in candidate.package_lock.entries if p.role is PackageRole.PRIMARY)
        declaration = next(
            d
            for d in owner.exact_participant_declarations(
                primary.package_id,
                primary.version,
                primary.sha256,
            )
            if d.kind == "player_character"
        )
        starter = next(
            row
            for row in json.loads(
                files("sylvia_gm.campaign").joinpath("srd-starters.json").read_bytes()
            )
            if row["id"] == "cleric"
        )
        _, data, entity, _ = validate_reusable_participant_draft(
            role="player_character",
            summary=starter["summary"],
            authored_profile=starter["authored_profile"],
            participant=starter["participant"],
            entity_state=starter["entity_state"],
            declaration=declaration,
        )
        assert (entity["hp"], entity["armor_class"], entity["resources"]["spell_slot_1"]) == (
            9,
            17,
            2,
        )
        entity["hp"] = 4
        state = _state()
        state[f"{SRD}.state"] = {
            "advancement": {
                "method": "fixed",
                "starting_level": 1,
                "maximum_level": 1,
                "recipients": "whole_party",
                "timing": "No advancement awards.",
                "allocation": "No advancement awards.",
            }
        }
        state[f"{SRD}.entities"]["hero"] = entity
        state[f"{SRD}.entities"]["guard"] = {
            "name": "Guard",
            "creature_kind": "monster",
            "hp": 7,
            "max_hp": 9,
            "armor_class": 12,
            "is_alive": True,
            "size_rank": 1,
            "conditions": [],
            "prepared_spells": [],
            "resources": {},
            "resource_maxima": {},
        }
        state[f"{SRD}.combat"].update(
            encounter_active=False,
            turn_active=False,
            active_actor=None,
            phase="exploration",
            action_available=False,
        )
        owner.create_campaign(
            CampaignCreate(
                campaign_id="casting",
                display_name="Starter Cleric",
                writer=WriterRole.DM,
                package_lock=candidate.package_lock,
                turn_id="casting-turn",
                participants=(
                    Participant(
                        participant_id="hero",
                        kind=declaration.kind,
                        package_id=primary.package_id,
                        package_version=primary.version,
                        package_sha256=primary.sha256,
                        schema_id=declaration.schema_id,
                        schema_sha256=declaration.schema_sha256,
                        validation_status=ParticipantValidationStatus.VALIDATED,
                        data=data,
                    ),
                ),
                initial_state=tuple(
                    StateValue(SRD, key.removeprefix(f"{SRD}."), value)
                    for key, value in state.items()
                ),
            )
        )
        owner.create_agent_session(
            campaign_id="casting",
            session_id="gm",
            role=WriterRole.DM,
            provider_id="fixture",
            model_id="fixture",
        )
    return database


@pytest.fixture(scope="module")
def lite_save(tmp_path_factory):
    return create_cleric_save(
        tmp_path_factory.mktemp("hotfix-lite") / "starter.sqlite3",
        bundled_srd_archive(),
    )


def test_actual_gm_catalog_excludes_permanent_refusals_and_preserves_lock(lite_save, tmp_path):
    database = tmp_path / "catalog.sqlite3"
    shutil.copyfile(lite_save, database)
    with AuthorityOwner.open(database, "hotfix-owner", disconnect_grace_seconds=1.0) as owner:
        before = owner.current_package_lock("casting")
        request = play(owner, CaptureGM(), campaign="casting", turn="casting-turn")
        assert owner.current_package_lock("casting") == before
    tools = request["tools"]
    names = [tool["function"]["name"] for tool in tools]
    assert not any(name.endswith("_select_srd_clone_body") for name in names)
    assert any(name.endswith("_cast_spell_outside_combat") for name in names)
    assert any(name.endswith("_read_campaign_reference") for name in names)
    assert len(tools) == 154
    casting = next(
        t["function"] for t in tools if t["function"]["name"].endswith("_cast_spell_outside_combat")
    )
    assert set(casting["parameters"]["properties"]["spell"]["enum"]) == {
        "guidance",
        "sacred_flame",
        "light",
        "bless",
        "cure_wounds",
        "guiding_bolt",
        "healing_word",
        "spare_the_dying",
        "mending",
        "command",
    }


def test_retained_alpha4_catalog_filters_stubs_without_upgrading_the_saved_pin(tmp_path):
    archive = (Path(__file__).parent / "fixtures/alpha4-lite.sylvia-gm-package").read_bytes()
    database = create_cleric_save(tmp_path / "retained.sqlite3", archive)
    with AuthorityOwner.open(database, "hotfix-owner", disconnect_grace_seconds=1.0) as owner:
        before = owner.current_package_lock("casting")
        primary = next(p for p in before.package_lock.entries if p.role is PackageRole.PRIMARY)
        assert primary.version == "0.3.15"
        declarations = owner.package_record(primary.package_id, primary.version, primary.sha256)[
            "tools"
        ]
        assert any(tool["name"] == "select_srd_clone_body" for tool in declarations)
        request = play(owner, CaptureGM(), campaign="casting", turn="casting-turn")
        assert owner.current_package_lock("casting") == before
    names = [tool["function"]["name"] for tool in request["tools"]]
    assert not any(name.endswith("_select_srd_clone_body") for name in names)
    assert any(name.endswith("_cast_spell_outside_combat") for name in names)
    assert any(name.endswith("_read_campaign_reference") for name in names)
    assert len(names) == 154


class CaptureGM(LibraryProvider):
    """Capture assembled requests and settle using the advertised campaign-file tool."""

    def __init__(self):
        super().__init__()
        self.requests = []

    async def generate_stream(self, messages, tools=None, **kwargs):
        self.requests.append(json.loads(json.dumps({"messages": messages, "tools": tools})))
        name = next(
            t["function"]["name"]
            for t in tools
            if t["function"]["name"].endswith("_update_campaign_file")
        )
        yield ProviderStreamChunk(
            tool_call_delta={
                "index": 0,
                "id": f"settle-{len(self.requests)}",
                "function": {
                    "name": name,
                    "arguments": json.dumps(
                        {
                            "state_change": "unchanged",
                            "state_change_summary": "The saved scene remains current.",
                            "revised_player_facing_response": [
                                {"kind": "narration", "text": "You wait."}
                            ],
                        }
                    ),
                },
            },
            finish_reason="tool_calls",
        )


class HealingGM(CaptureGM):
    """Choose advertised inputs, then add only fields named in actual refusal feedback."""

    def __init__(self):
        super().__init__()
        self.arguments = None
        self.cast_calls = 0
        self.refusals = []
        self.results = []
        self.observe_state = None
        self.observations = []

    async def generate_stream(self, messages, tools=None, **kwargs):
        if self.observe_state is not None:
            self.observations.append(self.observe_state())
        result_message = next((m for m in reversed(messages) if m.get("role") == "tool"), None)
        result = json.loads(result_message["content"]) if result_message else None
        if result is not None:
            self.results.append(result)
        if result is not None and result.get("status") == "action_committed":
            async for chunk in super().generate_stream(messages, tools, **kwargs):
                yield chunk
            return
        self.requests.append(json.loads(json.dumps({"messages": messages, "tools": tools})))
        cast = next(
            t["function"]
            for t in tools
            if t["function"]["name"].endswith("_cast_spell_outside_combat")
        )
        schema = cast["parameters"]
        fresh = next(
            m
            for m in messages
            if str(m.get("content", "")).startswith("SYLVIA_GM_FRESH_CAMPAIGN_STATE")
        )
        state = json.loads(fresh["content"].split("\n", 1)[1])["campaign_state"][
            "ruleset_owned_values"
        ]
        actor = schema["properties"]["caster"]["enum"][0]
        entity = state[f"{SRD}.entities"][actor]
        if self.arguments is None:
            assert set(schema["required"]) == {"caster_state", "spell"}
            spell = next(
                value
                for value in schema["properties"]["spell"]["enum"]
                if value.replace("_", " ") == "cure wounds"
            )
            self.arguments = {"caster_state": actor, "spell": spell.replace("_", " ").title()}
        elif result is not None:
            self.refusals.append(result)
            feedback = json.dumps(result)
            missing = set(re.findall(r"Missing input '([^']+)'", feedback))
            for name in missing:
                assert name in schema["properties"], (name, result)
                if name in {"caster", "caster_state", "target"}:
                    value = actor
                elif name == "slot_resource":
                    value = next(
                        k
                        for k, v in entity["resources"].items()
                        if k.startswith("spell_slot_") and v > 0
                    )
                elif schema["properties"][name].get("type") == "boolean":
                    value = True
                else:
                    raise AssertionError(f"No scene fact supplies {name}")
                self.arguments[name] = value
        self.cast_calls += 1
        yield ProviderStreamChunk(
            tool_call_delta={
                "index": 0,
                "id": f"heal-{self.cast_calls}",
                "function": {"name": cast["name"], "arguments": json.dumps(self.arguments)},
            },
            finish_reason="tool_calls",
        )


class DefectGM(CaptureGM):
    """Exercise one advertised operation, then settle its actual result."""

    def __init__(self, operation, arguments):
        super().__init__()
        self.operation = operation
        self.arguments = arguments
        self.receipt = None
        self.sent = False

    async def generate_stream(self, messages, tools=None, **kwargs):
        if self.sent:
            result = next(m for m in reversed(messages) if m.get("role") == "tool")
            self.receipt = json.loads(result["content"])
            async for chunk in super().generate_stream(messages, tools, **kwargs):
                yield chunk
            return
        self.sent = True
        self.requests.append(json.loads(json.dumps({"messages": messages, "tools": tools})))
        name = next(
            t["function"]["name"]
            for t in tools
            if t["function"]["name"].endswith("_" + self.operation)
        )
        yield ProviderStreamChunk(
            tool_call_delta={
                "index": 0,
                "id": "defect-control",
                "function": {"name": name, "arguments": json.dumps(self.arguments)},
            },
            finish_reason="tool_calls",
        )


@pytest.mark.parametrize(
    "operation,arguments,expected",
    [
        (
            "restore_srd_resource",
            {"target": "hero", "resource": "action_surge", "amount": 1},
            "rejected",
        ),
        (
            "record_srd_contingency_world_fact",
            {"caster_state": "hero", "fact_tag": "bell_rang"},
            "action_committed",
        ),
    ],
)
def test_named_ruleset_failures_use_safe_refusal_or_valid_noop(
    lite_save, tmp_path, operation, arguments, expected
):
    database = tmp_path / "named-defect.sqlite3"
    shutil.copyfile(lite_save, database)
    provider = DefectGM(operation, arguments)
    with AuthorityOwner.open(database, "hotfix-owner", disconnect_grace_seconds=1.0) as owner:
        before = owner.snapshot("casting")
        play(owner, provider, campaign="casting", turn="casting-turn")
        after = owner.snapshot("casting")
    assert provider.receipt["status"] == expected, provider.receipt
    if expected == "rejected":
        assert provider.receipt["code"] == "ruleset_precondition_failed"
        assert "recorded resource" in provider.receipt["reason"]
        assert after.state == before.state
        assert after.revision == before.revision
    assert after.state[f"{SRD}.entities"]["hero"]["hp"] == 4
    assert after.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2


def test_advertised_healing_flow_corrects_once_and_commits_exact_effects(lite_save, tmp_path):
    database = tmp_path / "healing.sqlite3"
    shutil.copyfile(lite_save, database)
    provider = HealingGM()
    with AuthorityOwner.open(database, "hotfix-owner", disconnect_grace_seconds=1.0) as owner:
        before = owner.snapshot("casting")
        provider.observe_state = lambda: (
            owner.snapshot("casting").revision,
            owner.snapshot("casting").state,
        )
        play(
            owner,
            provider,
            campaign="casting",
            turn="casting-turn",
            user_text="I cast Cure Wounds on myself.",
        )
        after = owner.snapshot("casting")
    assert provider.cast_calls == 2
    assert provider.observations[0] == provider.observations[1]
    assert len(provider.refusals) == 1
    refused = provider.refusals[0]
    assert refused["state_changed"] is False
    missing = set(re.findall(r"Missing input '([^']+)'", json.dumps(refused)))
    assert missing == {
        "target",
        "slot_resource",
        "verbal_component_available",
        "somatic_component_available",
    }
    assert "validation.issues" not in refused["instruction"]
    assert after.state[f"{SRD}.entities"]["hero"]["hp"] == 9
    assert after.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 1
    assert after.state[f"{SRD}.entities"]["guard"] == before.state[f"{SRD}.entities"]["guard"]
    committed = [
        action for action in after.actions if action.tool_name == "cast_spell_outside_combat"
    ]
    assert len(committed) == 1
    assert committed[0].parameters["spell"] == "cure_wounds"
    assert committed[0].parameters["target"] == "hero"


def play(owner, provider, *, campaign="memory", session="gm", turn="turn", user_text="I wait."):
    adapter = ProviderSessionAdapter(owner, provider, owner_id="hotfix-owner")
    result = CampaignSessionLoop(owner).run(
        SessionRunRequest(session, turn, "capture", "capture-input", user_text),
        adapter.for_turn(turn),
    )
    assert result.interruption is None, result.interruption
    return provider.requests[0]


def test_saved_memory_reaches_actual_provider_after_reopen_without_history_answers(tmp_path):
    database = tmp_path / "memory.sqlite3"
    with AuthorityOwner.open(database, "hotfix-owner") as owner:
        _campaign(owner, campaign_id="memory", writer=WriterRole.DM, turn_id="turn")
        owner.create_agent_session(
            campaign_id="memory",
            session_id="gm",
            role=WriterRole.DM,
            provider_id="fixture",
            model_id="fixture",
        )
        owner.append_agent_message(
            session_id="gm",
            message_id="basis",
            kind=SessionMessageKind.USER,
            content={"text": "Remember the established scene."},
        )
        owner.update_campaign_file(
            session_id="gm",
            update_id="seed-memory",
            turn_id="turn",
            basis_message_ids=("basis",),
            updates=(
                {
                    "operation": "record_memory",
                    "fact_id": "public-fact",
                    "category": "discovery",
                    "text": "The north bell rings in pairs.",
                    "visibility": "player_known",
                    "known_by": ["player"],
                },
                {
                    "operation": "record_memory",
                    "fact_id": "private-fact",
                    "category": "discovery",
                    "text": "The sealed password is juniper.",
                    "visibility": "gm_private",
                    "known_by": [],
                },
                {
                    "operation": "update_scene",
                    "location_id": "north-belfry",
                    "summary": "Waiting under the bronze bell.",
                },
            ),
        )
        revision = owner.snapshot("memory").revision
    with AuthorityOwner.open(database, "hotfix-owner", disconnect_grace_seconds=1.0) as reopened:
        request = play(reopened, CaptureGM())
        fresh = next(
            m
            for m in request["messages"]
            if str(m.get("content", "")).startswith("SYLVIA_GM_FRESH_CAMPAIGN_STATE")
        )
        body = json.loads(fresh["content"].split("\n", 1)[1])
        remaining = json.dumps([m for m in request["messages"] if m is not fresh])
        assert "juniper" not in remaining and "north bell rings" not in remaining
        assert body["campaign"]["revision"] == revision
        assert "north bell rings" in json.dumps(body["PLAYER_KNOWN"])
        assert "juniper" in json.dumps(body["GM_PRIVATE_CURRENT"])
        assert "juniper" not in json.dumps(body["PLAYER_KNOWN"])
        assert body["campaign_state"]["current_scene"]["location_id"] == "north-belfry"


def test_real_transport_restores_current_act_progress_and_secret_audiences(tmp_path):
    database = tmp_path / "plan-memory.sqlite3"
    campaign, session, turn = _start_campaign(database, "a-debt-in-iron", character_level=1)
    with AuthorityOwner.open(database, "memory-seed") as owner:
        owner.append_agent_message(
            session_id=session,
            message_id="memory-basis",
            kind=SessionMessageKind.USER,
            content={"text": "Remember what has been established."},
        )
        owner.record_campaign_scene(
            session_id=session,
            scene_id="later-scene",
            act_id="act-2",
            summary="The returned survey has reached the bronze bell.",
            location_id="north-belfry",
            basis_message_ids=("memory-basis",),
            turn_id=turn,
        )
        owner.record_campaign_plan_progress(
            session_id=session,
            update_id="first-objective",
            plan_item_id="objective-1",
            progress_status="resolved",
            summary="The recovery objective was resolved in play.",
            basis_message_ids=("memory-basis",),
            turn_id=turn,
        )
        owner.record_campaign_secret_reveal(
            session_id=session,
            reveal_id="mentor-reveal",
            secret_id="mentor-knew",
            audience=("player",),
            basis_message_ids=("memory-basis",),
            turn_id=turn,
        )
        owner.update_campaign_file(
            session_id=session,
            update_id="transport-facts",
            turn_id=turn,
            basis_message_ids=("memory-basis",),
            updates=(
                {
                    "operation": "record_memory",
                    "fact_id": "returned-cart",
                    "category": "continuity",
                    "text": "A copper owl marks the returned cart.",
                    "visibility": "player_known",
                    "known_by": ["player"],
                },
                {
                    "operation": "record_memory",
                    "fact_id": "route-token",
                    "category": "continuity",
                    "text": "The hidden route token is marigold-pact.",
                    "visibility": "gm_private",
                    "known_by": [],
                },
            ),
        )
        revision = owner.snapshot(campaign).revision
    provider = CaptureGM()
    harness = _OwnerHarness(database, provider)
    try:
        reply = request_attached(
            harness,
            "recall",
            "submit_session_input",
            {
                "session_id": session,
                "turn_id": turn,
                "expected_revision": revision,
                "attempt_id": "recall",
                "message_id": "recall",
                "user_text": "I wait.",
            },
            campaign_id=campaign,
            attempt_id="recall",
        )
        assert reply["interruption"] is None, reply
    finally:
        harness.close()
    messages = provider.requests[0]["messages"]
    fresh = next(
        m
        for m in messages
        if str(m.get("content", "")).startswith("SYLVIA_GM_FRESH_CAMPAIGN_STATE")
    )
    body = json.loads(fresh["content"].split("\n", 1)[1])
    assert body["campaign_state"]["current_scene"]["act_id"] == "act-2"
    assert body["campaign_state"]["plan_progress"][0]["status"] == "resolved"
    assert body["campaign_state"]["reveals"]["mentor-knew"] == ["player"]
    preparation = next(
        m
        for m in messages
        if str(m.get("content", "")).startswith("SYLVIA_GM_CAMPAIGN_PREPARATION")
    )
    prepared = json.loads(preparation["content"].split("\n", 1)[1])
    assert prepared["campaign_plan_preparation"]["prepared_acts"][0]["id"] == "act-2"
    assert "mentor-knew" in body["PLAYER_KNOWN"]["revealed_secret_ids"]
    assert "rift-is-one-way" not in body["PLAYER_KNOWN"]["revealed_secret_ids"]
    assert "copper owl" in json.dumps(body["PLAYER_KNOWN"])
    assert "marigold-pact" in json.dumps(body["GM_PRIVATE_CURRENT"])
    assert "marigold-pact" not in json.dumps(body["PLAYER_KNOWN"])
    history = json.dumps([m for m in messages if m.get("role") in {"assistant", "tool"}])
    assert "bronze bell" not in history and "recovery objective was resolved" not in history
    assert "copper owl" not in history and "marigold-pact" not in history
