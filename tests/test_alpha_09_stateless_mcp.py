"""Alpha-09 final MCP 2026-07-28 stateless mechanics qualification."""

from __future__ import annotations

import importlib.metadata
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from typing import cast

import pytest
from mcp import types
from mcp.client import Client
from mcp.shared.exceptions import MCPError

from sylvia_gm.agent._mode import AgentMode
from sylvia_gm.agent._runner import AgentRunner
from sylvia_gm.agent._stateless_mcp import (
    MCP_PROTOCOL_VERSION,
    MCP_SDK_VERSION,
    StatelessMcpMechanics,
    _safe_error,
    build_stateless_mcp_server,
)
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CatalogTool
from sylvia_gm.domain.authority import (
    ActionReceipt,
    ActionStatus,
    AwaitingChoiceReceipt,
    MechanicOutcome,
    MechanicsChoiceOption,
    MechanicsChoiceRequest,
    TurnStatus,
    WriterRole,
)
from sylvia_gm.domain.errors import CommandRejected, MechanicsRejected
from sylvia_gm.provider._base import FakeProvider

ROOT = Path(__file__).parents[1]
PACKAGE_ID = "synthetic.stateless"


class _OwnerProjection:
    def __init__(self, *, enabled: bool = True, disappear_on_call: int | None = None) -> None:
        self.enabled = enabled
        self.disappear_on_call = disappear_on_call
        self.catalog_calls = 0
        self.commands: list[ActionCommand] = []
        self.audit: list[object] = []
        self.tool = CatalogTool(
            package_id=PACKAGE_ID,
            package_version="1",
            package_sha256="a" * 64,
            name="advance_clock",
            capability="clock.advance",
            input_schema={
                "type": "object",
                "properties": {"steps": {"type": "integer"}},
                "required": ["steps"],
                "additionalProperties": False,
            },
            execute=lambda _context: MechanicOutcome(result={}),
        )

    def catalog(self, campaign_id: str) -> tuple[CatalogTool, ...]:
        assert campaign_id == "campaign-stateless"
        self.catalog_calls += 1
        if self.disappear_on_call == self.catalog_calls:
            self.enabled = False
        return (self.tool,) if self.enabled else ()

    def execute_action(self, command: ActionCommand) -> ActionReceipt | AwaitingChoiceReceipt:
        self.commands.append(command)
        if command.action_id == "action-choice":
            return AwaitingChoiceReceipt(
                campaign_id=command.campaign_id,
                action_id=command.action_id,
                basis_revision=command.expected_revision,
                turn_id=command.turn_id,
                choice=MechanicsChoiceRequest(
                    choice_id="route-signal",
                    prompt="Choose a route.",
                    options=(
                        MechanicsChoiceOption("left", "Left"),
                        MechanicsChoiceOption("right", "Right"),
                    ),
                ),
            )
        return ActionReceipt(
            campaign_id=command.campaign_id,
            action_id=command.action_id,
            status=ActionStatus.COMMITTED,
            result={"steps": command.parameters["steps"]},
            resulting_revision=command.expected_revision + 1,
        )

    def snapshot(self, campaign_id: str) -> object:
        assert campaign_id == "campaign-stateless"
        return SimpleNamespace(
            revision=4,
            writer=WriterRole.DM,
            turns={"turn-stateless": TurnStatus.OPEN},
            state={"synthetic.stateless.combat": {"phase": "signal"}},
        )

    def record_admin_full_audit(self, payload: object) -> None:
        self.audit.append(payload)


def _command(*, suffix: str = "one") -> ActionCommand:
    return ActionCommand(
        campaign_id="campaign-stateless",
        action_id=f"action-{suffix}",
        idempotency_key=f"idempotency-{suffix}",
        owner_id="owner-stateless",
        writer=WriterRole.DM,
        expected_revision=4,
        turn_id="turn-stateless",
        package_id=PACKAGE_ID,
        tool_name="advance_clock",
        parameters={"steps": 2},
    )


def test_mcp_sdk_is_exactly_locked_to_final_stateless_release() -> None:
    assert importlib.metadata.version("mcp") == MCP_SDK_VERSION == "2.0.0"
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    lock = (ROOT / "uv.lock").read_text(encoding="utf-8")
    assert '"mcp==2.0.0"' in pyproject
    assert 'name = "mcp"' in lock and 'version = "2.0.0"' in lock
    assert MCP_PROTOCOL_VERSION == "2026-07-28"


def test_legacy_session_bound_dm_runner_is_retired_fail_closed() -> None:
    with pytest.raises(ValueError, match="canonical CampaignSessionLoop stateless MCP"):
        AgentRunner(
            mode=AgentMode.DM_SESSION,
            provider=FakeProvider(responses=[]),
            history_path=None,
        )


def test_legacy_http_and_pywebview_applications_are_absent() -> None:
    assert not list((ROOT / "src/sylvia_gm/server").glob("*.py"))
    assert not list((ROOT / "src/sylvia_gm/desktop").glob("*.py"))
    assert not list((ROOT / "src/sylvia_gm/desktop").glob("*.ico"))
    assert not list((ROOT / "src/sylvia_gm/ui").glob("*"))
    assert not (ROOT / "scripts/assemble_launcher.py").exists()
    assert not (ROOT / "scripts/generate_logo_b64.py").exists()

    main_source = (ROOT / "src/sylvia_gm/main.py").read_text(encoding="utf-8")
    project = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    for retired_surface in (
        "create_app",
        "pywebview",
        "uvicorn",
        "fastapi",
        "sylvia_gm.server",
        "sylvia_gm.desktop",
    ):
        assert retired_surface not in main_source.casefold()
        assert retired_surface not in project.casefold()


def test_canonical_model_path_has_no_direct_or_duplicate_mechanics_dispatch() -> None:
    session_loop = (ROOT / "src/sylvia_gm/agent/_session_loop.py").read_text(encoding="utf-8")
    provider_adapter = (ROOT / "src/sylvia_gm/agent/_provider_session.py").read_text(
        encoding="utf-8"
    )
    assert "_owner.catalog(" not in session_loop
    assert "_owner.execute_action(" not in session_loop
    assert "_owner.catalog(" not in provider_adapter
    assert "_owner.execute_action(" not in provider_adapter
    assert not (ROOT / "src/sylvia_gm/tools/_mcp_server.py").exists()
    assert not (ROOT / "src/sylvia_gm/server/_mcp_server.py").exists()


def test_fresh_and_alternating_handlers_have_no_catalog_or_action_affinity() -> None:
    owner = _OwnerProjection()
    first = StatelessMcpMechanics(cast(AuthorityOwner, owner))
    second = StatelessMcpMechanics(cast(AuthorityOwner, owner))

    assert [tool.name for tool in first.list_tools("campaign-stateless")] == ["advance_clock"]
    listed_trace = owner.audit[-1]
    assert isinstance(listed_trace, dict)
    assert listed_trace["kind"] == "mcp_list_tools"
    assert listed_trace["basis_revision"] == 4
    assert [tool["name"] for tool in listed_trace["tools"]] == ["advance_clock"]
    assert [tool.name for tool in second.list_tools("campaign-stateless")] == ["advance_clock"]
    receipt = first.execute_action(_command())

    assert receipt.status is ActionStatus.COMMITTED
    assert receipt.result == {"steps": 2}
    assert owner.commands == [_command()]
    assert owner.catalog_calls >= 5
    assert any(
        isinstance(trace, dict)
        and trace.get("kind") == "mcp_call_tool"
        and trace.get("action_id") == "action-one"
        for trace in owner.audit
    )


def test_stateless_mcp_round_trips_pending_choice_and_session_correlation() -> None:
    owner = _OwnerProjection()
    mechanics = StatelessMcpMechanics(cast(AuthorityOwner, owner))
    command = replace(
        _command(suffix="choice"),
        initiating_session_id="session-dm-choice",
    )
    receipt = mechanics.execute_action(command)

    assert isinstance(receipt, AwaitingChoiceReceipt)
    assert receipt.choice.choice_id == "route-signal"
    assert tuple(option.option_id for option in receipt.choice.options) == ("left", "right")
    assert owner.commands == [command]


def test_no_rules_catalog_is_exactly_zero_tools() -> None:
    owner = _OwnerProjection(enabled=False)
    mechanics = StatelessMcpMechanics(cast(AuthorityOwner, owner))
    assert mechanics.list_tools("campaign-stateless") == ()
    with pytest.raises(CommandRejected, match="unavailable"):
        mechanics.execute_action(_command())
    assert owner.commands == []


def test_planless_dm_still_gets_campaign_file_memory_tools() -> None:
    class PlanlessNarrativeOwner(_OwnerProjection):
        def accepted_campaign_plan(self, campaign_id: str) -> None:
            assert campaign_id == "campaign-stateless"
            return None

        def campaign_narrative_options(self, session_id: str) -> object:
            assert session_id == "session-dm"
            return {
                "operations": ["record_memory", "record_entity", "update_scene"],
                "scene_act_ids": [],
                "eligible_secret_ids": [],
            }

    mechanics = StatelessMcpMechanics(
        cast(AuthorityOwner, PlanlessNarrativeOwner(enabled=False))
    )
    catalog = mechanics.list_tools("campaign-stateless", session_id="session-dm")

    assert [tool.name for tool in catalog] == [
        "search_campaign_knowledge",
        "read_campaign_reference",
        "update_campaign_file",
    ]
    assert catalog[0].input_schema["required"] == ["query"]
    assert catalog[0].input_schema["properties"]["limit"]["maximum"] == 8
    reference_sources = catalog[1].input_schema["properties"]["source"]["enum"]
    assert reference_sources == ["campaign_state", "participant", "engine_format"]
    assert set(catalog[2].input_schema["properties"]) == {
        "entities",
        "facts",
        "scenes",
        "state_change",
        "state_change_summary",
    }
    assert "act_id" not in catalog[2].input_schema["properties"]["scenes"]["items"][
        "properties"
    ]


def test_campaign_state_tools_are_exact_gm_screen_writes() -> None:
    class NarrativeOwner(_OwnerProjection):
        def accepted_campaign_plan(self, campaign_id: str) -> object:
            assert campaign_id == "campaign-stateless"
            return SimpleNamespace(content={"campaign_plan": {"id": "plan-stateless"}})

        def campaign_narrative_options(self, session_id: str) -> object:
            assert session_id == "session-dm"
            return {
                "operations": [
                    "record_memory",
                    "record_entity",
                    "update_scene",
                    "update_plan_progress",
                    "reveal_secret",
                ],
                "scene_act_ids": ["act-1"],
                "plan_item_ids": ["arrival-and-waystation", "act-1"],
                "eligible_secret_ids": ["secret-stateless"],
            }

        def current_open_turn(self, campaign_id: str) -> str:
            assert campaign_id == "campaign-stateless"
            return "turn-stateless"

    owner = NarrativeOwner()
    mechanics = StatelessMcpMechanics(cast(AuthorityOwner, owner))
    catalog = mechanics.list_tools("campaign-stateless", session_id="session-dm")
    narrative = {
        tool.name: tool
        for tool in catalog
        if tool.category == "narrative_state"
    }

    assert list(narrative) == ["update_campaign_file"]
    tool = narrative["update_campaign_file"]
    assert "post-action truth" in tool.description
    assert "every lasting truth the response establishes or changes" in tool.description
    assert "must not advance beyond or contradict" in tool.description
    schema = tool.input_schema
    assert schema["required"] == ["state_change", "state_change_summary"]
    assert "minProperties" not in schema
    assert schema["additionalProperties"] is False
    assert set(schema["properties"]) == {
        "entities",
        "facts",
        "scenes",
        "reveals",
        "plan_progress",
        "state_change",
        "state_change_summary",
    }
    assert schema["properties"]["state_change"]["enum"] == ["changed", "unchanged"]
    assert "compare the complete response" in schema["properties"]["state_change"][
        "description"
    ]
    assert schema["properties"]["state_change_summary"]["minLength"] == 1
    reference = next(tool for tool in catalog if tool.name == "read_campaign_reference")
    assert reference.name == "read_campaign_reference"
    assert reference.description.startswith("READ-ONLY:")
    assert "Character File" in reference.description
    assert "authored summary" in reference.description
    assert "cannot update, write, save, or commit Campaign File state" in reference.description
    assert "not to decorate or expand narration" in reference.description
    assert "current action and vantage reveal it" in reference.description
    assert "Do not reread Plan or Setting material" in reference.description
    assert "stable Campaign File record ID" in str(
        reference.input_schema["properties"]["path"]["description"]
    )
    assert "exact participant ID" in str(
        reference.input_schema["properties"]["path"]["description"]
    )
    search = next(tool for tool in catalog if tool.name == "search_campaign_knowledge")
    assert search.category == "reference"
    assert "discovery" in search.description.lower()
    assert "exact read" in search.description.lower()
    expected_items = {
        "facts": ["category", "fact_id", "known_by", "text", "visibility"],
        "entities": [
            "attributes",
            "entity_id",
            "entity_kind",
            "known_by",
            "name",
            "visibility",
        ],
        "scenes": ["summary"],
        "plan_progress": ["plan_item_id", "progress_status", "progress_summary"],
        "reveals": ["audience", "secret_id"],
    }
    for collection, required in expected_items.items():
        assert schema["properties"][collection]["minItems"] == 1
        item_schema = schema["properties"][collection]["items"]
        assert item_schema["required"] == required
        assert item_schema["additionalProperties"] is False
        assert "operation" not in item_schema["properties"]
    entity_attributes_description = schema["properties"]["entities"]["items"]["properties"][
        "attributes"
    ]["description"]
    assert "never infer posture, alertness, equipment use" in entity_attributes_description
    entity_properties = schema["properties"]["entities"]["items"]["properties"]
    assert "unrevealed true name" in entity_properties["name"]["description"]
    assert "entire entity record" in entity_properties["visibility"]["description"]
    assert "actually learned" in entity_properties["known_by"]["description"]
    assert entity_properties["source_ref"]["minLength"] == 1
    assert "Omit when there is no admitted source" in entity_properties["source_ref"][
        "description"
    ]
    fact_properties = schema["properties"]["facts"]["items"]["properties"]
    assert "entire fact" in fact_properties["visibility"]["description"]
    assert "actually learned" in fact_properties["known_by"]["description"]
    scene_summary_description = schema["properties"]["scenes"]["items"]["properties"]["summary"][
        "description"
    ]
    assert "Never add a player action or equipment posture" in scene_summary_description
    progress_properties = schema["properties"]["plan_progress"]["items"]["properties"]
    progress_status_description = progress_properties["progress_status"]["description"]
    assert "'engaged' requires a player choice" in progress_status_description
    assert "'deferred' means the player clearly declined, avoided" in progress_status_description
    assert "not a current obligation" in progress_status_description
    assert "record an express player choice" in progress_properties[
        "progress_summary"
    ]["description"]
    assert "Otherwise leave the player's response unresolved" in progress_properties[
        "progress_summary"
    ]["description"]
    assert "Never infer the player's interest, intent, acceptance, route, or next action" in (
        progress_properties["progress_summary"]["description"]
    )
    assert "Do not describe the player as weighing" in progress_properties[
        "progress_summary"
    ]["description"]
    assert tool.parameter_derivations == {}
    assert "durable Campaign File" in tool.description
    assert "whole update is validated and committed together" in tool.description
    assert "Any player-facing response and this disposition" in tool.description
    assert "releases a settled response only after this call succeeds" in tool.description
    assert "either delivers the held response or asks" not in tool.description
    assert "not a summary of the player's request" in tool.description
    assert "omit all state collections only when" in tool.description
    assert "An unchanged assessment asserts that location, scene, time" in tool.description
    assert "player or NPC decisions" in tool.description
    assert "Omit unchanged collections" in tool.description
    assert "An unsuccessful outcome can still change location" in tool.description
    assert "even when the player's goal remains unmet" in tool.description
    assert "Restating supplied Campaign State is not a change" in tool.description
    assert schema["properties"]["reveals"]["items"]["properties"]["secret_id"][
        "enum"
    ] == ["secret-stateless"]
    assert schema["properties"]["scenes"]["items"]["properties"]["act_id"]["enum"] == [
        "act-1"
    ]
    assert schema["properties"]["plan_progress"]["items"]["properties"][
        "plan_item_id"
    ]["enum"] == ["arrival-and-waystation", "act-1"]
    unchanged = mechanics.commit_campaign_file(
        "campaign-stateless",
        session_id="session-dm",
        basis_message_id="message-stateless",
        turn_id="turn-stateless",
        parameters={
            "state_change": "unchanged",
            "state_change_summary": "The response only repeats the current scene.",
        },
    )
    assert unchanged == {
        "status": "campaign_file_unchanged",
        "update_id": unchanged["update_id"],
        "campaign_revision": 4,
        "state_changed": False,
    }
    assert str(unchanged["update_id"]).startswith("campaign-file-")
    assert owner.audit[-1]["kind"] == "mcp_update_campaign_file"
    assert owner.audit[-1]["parameters"] == {
        "state_change": "unchanged",
        "state_change_summary": "The response only repeats the current scene.",
    }
    assert owner.audit[-1]["result"]["status"] == "campaign_file_unchanged"

    with pytest.raises(MechanicsRejected) as changed_without_notes:
        mechanics.commit_campaign_file(
            "campaign-stateless",
            session_id="session-dm",
            basis_message_id="message-stateless",
            turn_id="turn-stateless",
            parameters={
                "state_change": "changed",
                "state_change_summary": "The party departed the clearing.",
            },
        )
    assert changed_without_notes.value.code == "campaign_file_disposition_inconsistent"
    assert changed_without_notes.value.field_paths == ("state_change",)

    with pytest.raises(MechanicsRejected) as unchanged_with_notes:
        mechanics.commit_campaign_file(
            "campaign-stateless",
            session_id="session-dm",
            basis_message_id="message-stateless",
            turn_id="turn-stateless",
            parameters={
                "state_change": "unchanged",
                "state_change_summary": "Nothing changed.",
                "scenes": [{"summary": "The party departed the clearing."}],
            },
        )
    assert unchanged_with_notes.value.code == "campaign_file_disposition_inconsistent"
    assert unchanged_with_notes.value.field_paths == ("state_change",)

    with pytest.raises(MechanicsRejected) as empty_collection:
        mechanics.commit_campaign_file(
            "campaign-stateless",
            session_id="session-dm",
            basis_message_id="message-stateless",
            turn_id="turn-stateless",
            parameters={
                "state_change": "changed",
                "state_change_summary": "An entity changed.",
                "entities": [],
            },
        )
    assert empty_collection.value.code == "campaign_file_update_invalid"
    assert empty_collection.value.field_paths == ("entities",)
    assert empty_collection.value.validation_issues[0].kind == "empty_collection"
    assert empty_collection.value.validation_issues[0].minimum == 1

    with pytest.raises(MechanicsRejected) as invalid_collection:
        mechanics.commit_campaign_file(
            "campaign-stateless",
            session_id="session-dm",
            basis_message_id="message-stateless",
            turn_id="turn-stateless",
            parameters={
                "state_change": "changed",
                "state_change_summary": "The scene changed.",
                "scenes": {"summary": "The clearing is quiet."},
            },
        )
    assert invalid_collection.value.code == "campaign_file_update_invalid"
    assert invalid_collection.value.field_paths == ("scenes",)

    with pytest.raises(MechanicsRejected) as invalid_item:
        mechanics.commit_campaign_file(
            "campaign-stateless",
            session_id="session-dm",
            basis_message_id="message-stateless",
            turn_id="turn-stateless",
            parameters={
                "state_change": "changed",
                "state_change_summary": "The scene changed.",
                "scenes": [{"location_id": "verdant-margin"}],
            },
        )
    assert invalid_item.value.code == "campaign_file_update_invalid"
    assert invalid_item.value.field_paths == ("scenes[0].summary",)

    with pytest.raises(MechanicsRejected) as invalid_batch:
        mechanics.commit_campaign_file(
            "campaign-stateless",
            session_id="session-dm",
            basis_message_id="message-stateless",
            turn_id="turn-stateless",
            parameters={
                "state_change": "changed",
                "state_change_summary": "Several records changed.",
                "entities": [
                    {
                        "entity_id": "scout",
                        "entity_kind": "person",
                        "name": "Scout",
                        "attributes": {},
                    }
                ],
                "facts": "not-an-array",
                "item": {"fact_id": "foreign-collection"},
                "scenes": [{}],
            },
        )
    assert invalid_batch.value.code == "campaign_file_update_invalid"
    assert invalid_batch.value.field_paths == (
        "entities[0].known_by",
        "entities[0].visibility",
        "facts",
        "item",
        "scenes[0].summary",
    )
    assert [issue.as_payload() for issue in invalid_batch.value.validation_issues] == [
        {
            "path": "entities[0].known_by",
            "kind": "missing_required_field",
            "expected_type": "array",
            "parent_path": "entities[0]",
        },
        {
            "path": "entities[0].visibility",
            "kind": "missing_required_field",
            "expected_type": "string",
            "parent_path": "entities[0]",
            "allowed_values": ["player_known", "gm_private"],
        },
        {
            "path": "facts",
            "kind": "wrong_type",
            "expected_type": "array",
            "actual_type": "string",
            "parent_path": "$",
            "minimum": 1,
            "maximum": 64,
        },
        {
            "path": "item",
            "kind": "unexpected_field",
            "actual_type": "object",
            "parent_path": "$",
        },
        {
            "path": "scenes[0].summary",
            "kind": "missing_required_field",
            "expected_type": "string",
            "parent_path": "scenes[0]",
            "minimum": 1,
            "maximum": 2000,
        },
    ]

    with pytest.raises(MechanicsRejected) as empty_source_reference:
        mechanics.commit_campaign_file(
            "campaign-stateless",
            session_id="session-dm",
            basis_message_id="message-stateless",
            turn_id="turn-stateless",
            parameters={
                "state_change": "changed",
                "state_change_summary": "A pool was discovered.",
                "entities": [
                    {
                        "entity_id": "thread-pool",
                        "entity_kind": "place",
                        "name": "Thread-pool",
                        "attributes": {"location": "verdant-margin"},
                        "visibility": "player_known",
                        "known_by": ["player"],
                        "source_ref": "",
                    }
                ],
            },
        )
    assert empty_source_reference.value.code == "campaign_file_update_invalid"
    assert empty_source_reference.value.field_paths == ("entities[0].source_ref",)
    assert empty_source_reference.value.validation_issues[0].kind == "string_too_short"
    assert empty_source_reference.value.validation_issues[0].minimum == 1

    with pytest.raises(MechanicsRejected) as v23_shape:
        mechanics.commit_campaign_file(
            "campaign-stateless",
            session_id="session-dm",
            basis_message_id="message-stateless",
            turn_id="turn-stateless",
            parameters={
                "state_change": "changed",
                "state_change_summary": "The entity records changed.",
                "entities": [
                    {
                        "entity_id": "daniel",
                        "entity_kind": "person",
                        "name": "Daniel",
                        "attributes": {"location": "anchor-clearing"},
                    },
                    {
                        "entity_id": "chloe",
                        "entity_kind": "person",
                        "name": "Chloe",
                        "attributes": {"location": "anchor-clearing"},
                    },
                ]
            },
        )
    assert [issue.as_payload() for issue in v23_shape.value.validation_issues] == [
        {
            "path": "entities[0].known_by",
            "kind": "missing_required_field",
            "expected_type": "array",
            "parent_path": "entities[0]",
        },
        {
            "path": "entities[0].visibility",
            "kind": "missing_required_field",
            "expected_type": "string",
            "parent_path": "entities[0]",
            "allowed_values": ["player_known", "gm_private"],
        },
        {
            "path": "entities[1].known_by",
            "kind": "missing_required_field",
            "expected_type": "array",
            "parent_path": "entities[1]",
        },
        {
            "path": "entities[1].visibility",
            "kind": "missing_required_field",
            "expected_type": "string",
            "parent_path": "entities[1]",
            "allowed_values": ["player_known", "gm_private"],
        },
    ]

    with pytest.raises(MechanicsRejected) as invalid_plan_item:
        mechanics.commit_campaign_file(
            "campaign-stateless",
            session_id="session-dm",
            basis_message_id="message-stateless",
            turn_id="turn-stateless",
            parameters={
                "state_change": "changed",
                "state_change_summary": "Plan progress changed.",
                "plan_progress": [
                    {
                        "plan_item_id": "act-1.planned_encounters.invented",
                        "progress_status": "introduced",
                        "progress_summary": "The encounter entered play.",
                    }
                ]
            },
        )
    assert invalid_plan_item.value.code == "campaign_file_update_invalid"
    assert invalid_plan_item.value.field_paths == (
        "plan_progress[0].plan_item_id",
    )
    assert invalid_plan_item.value.validation_issues[0].kind == "invalid_enum_value"
    assert invalid_plan_item.value.validation_issues[0].allowed_values == (
        "arrival-and-waystation",
        "act-1",
    )


def test_rejected_campaign_state_write_preserves_safe_diagnostic() -> None:
    class RejectingNarrativeOwner(_OwnerProjection):
        def __init__(self) -> None:
            super().__init__()
            self.diagnostics: list[dict[str, object]] = []

        def accepted_campaign_plan(self, campaign_id: str) -> object:
            assert campaign_id == "campaign-stateless"
            return SimpleNamespace(content={"campaign_plan": {"id": "plan-stateless"}})

        def campaign_narrative_options(self, session_id: str) -> object:
            assert session_id == "session-dm"
            return {"operations": ["record_memory"], "eligible_secret_ids": []}

        def current_open_turn(self, campaign_id: str) -> str:
            assert campaign_id == "campaign-stateless"
            return "turn-stateless"

        def record_campaign_memory(self, **_kwargs: object) -> object:
            raise CommandRejected("Campaign memory field 'known_by' is invalid")

        def record_mechanics_diagnostic(self, **diagnostic: object) -> None:
            self.diagnostics.append(dict(diagnostic))

    owner = RejectingNarrativeOwner()
    mechanics = StatelessMcpMechanics(cast(AuthorityOwner, owner))

    with pytest.raises(CommandRejected) as captured:
        mechanics.commit_narrative_state(
            "campaign-stateless",
            session_id="session-dm",
            basis_message_id="message-stateless",
            parameters={
                "operation": "record_memory",
                "fact_id": "fact-stateless",
                "category": "continuity",
                "text": "The marker remains beside the cache.",
                "visibility": "player_known",
                "known_by": ["player"],
            },
        )

    assert captured.value.code == "mechanics_validation_failed"
    assert len(owner.diagnostics) == 1
    diagnostic = owner.diagnostics[0]
    assert str(diagnostic.pop("action_id")).startswith("narrative-")
    assert diagnostic == {
        "session_id": "session-dm",
        "turn_id": "turn-stateless",
        "tool_name": "record_campaign_memory",
        "rejection_code": "mechanics_validation_failed",
        "validation_field_paths": ("known_by",),
        "state_changed": False,
    }


def test_campaign_scene_rejection_preserves_specific_code_and_field() -> None:
    result = _safe_error(
        CommandRejected(
            "Campaign scene field 'summary' is invalid",
            code="campaign_scene_invalid",
        )
    )

    assert result.structured_content == {
        "status": "rejected",
        "code": "campaign_scene_invalid",
        "reason": "Mechanics authority rejected the operation.",
        "validation": {"field_paths": ["summary"]},
        "state_changed": False,
    }


def test_call_revalidates_canonical_catalog_after_listing() -> None:
    owner = _OwnerProjection(disappear_on_call=3)
    mechanics = StatelessMcpMechanics(cast(AuthorityOwner, owner))

    with pytest.raises(CommandRejected) as rejected:
        mechanics.execute_action(_command(suffix="changed"))

    assert rejected.value.code == "mcp_tool_unavailable"
    assert owner.commands == []


@pytest.mark.asyncio
async def test_modern_request_has_no_session_authority_and_legacy_version_rejects() -> None:
    owner = cast(AuthorityOwner, _OwnerProjection())
    server = build_stateless_mcp_server(owner)
    modern_meta = cast(
        types.RequestParamsMeta,
        {"sylvia_gm/campaign_id": "campaign-stateless"},
    )
    async with Client(server) as modern:
        assert modern.protocol_version == MCP_PROTOCOL_VERSION
        listed = await modern.list_tools(meta=modern_meta, cache_mode="bypass")
        assert [tool.name for tool in listed.tools] == ["advance_clock"]

    session_meta = cast(
        types.RequestParamsMeta,
        {
            "sylvia_gm/campaign_id": "campaign-stateless",
            "Mcp-Session-Id": "forbidden-affinity",
        },
    )
    async with Client(build_stateless_mcp_server(owner)) as modern:
        with pytest.raises(MCPError, match="sessions cannot carry mechanics authority"):
            await modern.list_tools(meta=session_meta, cache_mode="bypass")

    async with Client(build_stateless_mcp_server(owner), mode="legacy") as legacy:
        assert legacy.protocol_version != MCP_PROTOCOL_VERSION
        with pytest.raises(MCPError, match="Unsupported MCP protocol version"):
            await legacy.list_tools(meta=modern_meta, cache_mode="bypass")
