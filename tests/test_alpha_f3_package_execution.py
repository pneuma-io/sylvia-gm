"""F3 admitted-package execution through the sole campaign authority.

These tests prove that immutable package bytes — not a test-only Python closure —
materialize the MCP/catalog mechanic that commits through ``AuthorityOwner``.
"""

from __future__ import annotations

import hashlib
import io
import zipfile
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from sylvia_gm.application.authority import (
    ActionCommand,
    AuthorityOwner,
    CampaignCreate,
    ResolveMechanicsChoiceCommand,
)
from sylvia_gm.application.package_runtime import AuthorityMemoryStateBridge
from sylvia_gm.dice import set_seed
from sylvia_gm.domain.authority import (
    ActionStatus,
    AwaitingChoiceReceipt,
    PlayerProjectionKind,
    StateValue,
    WriterRole,
)
from sylvia_gm.domain.errors import CommandRejected, IdempotencyConflict, MechanicsRejected
from sylvia_gm.domain.package_library import canonical_json_bytes, content_root_for_record
from sylvia_gm.runtime._condition_evaluator import evaluate_expression
from sylvia_gm.runtime._effect_executor import _EFFECT_HANDLERS, execute_effects
from sylvia_gm.runtime._exceptions import EffectExecutionError, MechanicsChoiceSuspended
from sylvia_gm.runtime._schema_validator import VALID_EFFECT_KEYS, validate_ruleset
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import (
    ChoiceResolutionControl,
    ExecutionBudget,
    ExecutionContext,
    ToolDef,
    ToolParameter,
)


def _runtime_yaml() -> bytes:
    return b"""sylvia_gm_runtime:
  format: 1
  state_namespace: synthetic.clockwork
features: {}
abilities:
  measure:
    name: Measure
    default_min: 1
    default_max: 20
    generation: 1d20
    modifier_formula: score
    used_for: []
tools:
  advance:
    description: Advance the package-owned counter.
    parameters:
      - name: steps
        type: integer
        minimum: 1
        required: true
      - name: witnesses
        type: entity_id_list
        resolve: entity_list
        min_items: 1
        max_items: 2
        unique_items: true
        required: false
    effects:
      - increment:
          field: state.counter
          amount: steps
      - trigger:
          name: on_counter_advanced
          context: {}
procedures:
  counter_advanced:
    trigger: on_counter_advanced
    steps:
      - increment:
          field: state.bonus
          amount: 1
definitions: {}
narrative: {}
"""


def _tool() -> dict[str, object]:
    return {
        "name": "advance",
        "capability": "clockwork.counter",
        "input_schema": {
            "type": "object",
            "properties": {
                "steps": {"type": "integer", "minimum": 1},
                "witnesses": {
                    "type": "array",
                    "items": {
                        "type": "string",
                        "x-sylvia-gm-reference": "entity",
                    },
                    "minItems": 1,
                    "maxItems": 2,
                    "uniqueItems": True,
                },
            },
            "required": ["steps"],
            "additionalProperties": False,
        },
        "state_predicates": [],
        "turn_statuses": ["open"],
        "writers": ["dm"],
    }


def _archive(*, include_runtime: bool = True, runtime_bytes: bytes | None = None) -> bytes:
    executable = _runtime_yaml() if runtime_bytes is None else runtime_bytes
    files = (
        {"runtime/ruleset.yaml": executable}
        if include_runtime
        else {"data/placeholder.json": b"{}"}
    )
    draft: dict[str, object] = {
        "id": "synthetic.clockwork",
        "version": "1.0.0",
        "role": "primary",
        "metadata": {"label": "Synthetic Clockwork"},
        "provenance": {
            "publisher": "sylvia-gm-tests",
            "source_sha256": hashlib.sha256(_runtime_yaml()).hexdigest(),
        },
        "compatibility": {"host_contract": 1, "runtime_format": 1, "requires": []},
        "minimum_model_capability_tier": "sylvia.capability.t00",
        "dependencies": [],
        "capabilities": ["clockwork.counter"],
        "tools": [_tool()],
        "prompt_fragments": [],
        "ui_surfaces": [],
        "files": [
            {
                "path": path,
                "bytes": len(value),
                "sha256": hashlib.sha256(value).hexdigest(),
            }
            for path, value in sorted(files.items())
        ],
    }
    record = {**draft, "content_sha256": content_root_for_record(draft)}
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(
            "manifest.json",
            canonical_json_bytes({"format": 1, "packages": [record]}),
        )
        for path, value in files.items():
            archive.writestr(f"packages/synthetic.clockwork/{path}", value)
    return stream.getvalue()


def _choice_runtime_yaml() -> bytes:
    raw = yaml.safe_load(_runtime_yaml())
    raw["tools"]["advance"]["effects"] = [
        {"dice": {"expression": "1d6"}, "store_as": "_signal"},
        {
            "request_choice": {
                "choice_id": "signal-response",
                "prompt": "Signal {_signal.total}: choose a response.",
                "options": [
                    {"id": "echo", "label": "Echo the signal"},
                    {"id": "ground", "label": "Ground the signal"},
                ],
            },
            "store_as": "_response",
        },
        {
            "condition": "_response == 'echo'",
            "on_true": [{"increment": {"field": "state.counter", "amount": "steps"}}],
            "on_false": [{"increment": {"field": "state.bonus", "amount": "steps"}}],
        },
    ]
    raw["procedures"] = {}
    return yaml.safe_dump(raw, sort_keys=False).encode("utf-8")


def _create(owner: AuthorityOwner, archive: bytes) -> None:
    candidate = owner.install_package_archive(io.BytesIO(archive), install_id="runtime-install")
    owner.create_campaign(
        CampaignCreate(
            campaign_id="campaign-runtime",
            display_name="Admitted runtime",
            writer=WriterRole.DM,
            package_lock=candidate.package_lock,
            turn_id="turn-runtime",
            initial_state=(
                StateValue(
                    "synthetic.clockwork",
                    "state",
                    {"counter": 0, "bonus": 0},
                ),
            ),
        )
    )


def _command(*, revision: int, suffix: str, steps: int) -> ActionCommand:
    return ActionCommand(
        campaign_id="campaign-runtime",
        action_id=f"action-{suffix}",
        idempotency_key=f"idem-{suffix}",
        owner_id="owner-runtime" if revision == 0 else "owner-reopened",
        writer=WriterRole.DM,
        expected_revision=revision,
        turn_id="turn-runtime",
        package_id="synthetic.clockwork",
        tool_name="advance",
        parameters={"steps": steps},
    )


def test_admitted_runtime_materializes_executes_and_reopens_without_registration(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authority.db"
    with AuthorityOwner.open(database, "owner-runtime") as owner:
        _create(owner, _archive())
        assert tuple(tool.name for tool in owner.catalog("campaign-runtime")) == ("advance",)
        first = owner.execute_action(_command(revision=0, suffix="first", steps=3))
        assert first.status is ActionStatus.COMMITTED
        assert owner.snapshot("campaign-runtime").state["synthetic.clockwork.state"] == {
            "bonus": 1,
            "counter": 3,
        }

    with AuthorityOwner.open(database, "owner-reopened") as reopened:
        assert tuple(tool.name for tool in reopened.catalog("campaign-runtime")) == ("advance",)
        second = reopened.execute_action(_command(revision=1, suffix="second", steps=2))
        assert second.status is ActionStatus.COMMITTED
        assert reopened.snapshot("campaign-runtime").state["synthetic.clockwork.state"] == {
            "bonus": 2,
            "counter": 5,
        }
        print(
            "F3 ADMITTED RUNTIME",
            first.resulting_revision,
            second.resulting_revision,
            reopened.snapshot("campaign-runtime").state["synthetic.clockwork.state"],
        )


def test_package_choice_reservation_is_durable_replayable_and_fences_campaign(
    tmp_path: Path,
) -> None:
    database = tmp_path / "authority.db"
    archive = _archive(runtime_bytes=_choice_runtime_yaml())
    command = _command(revision=0, suffix="choice", steps=3)
    with AuthorityOwner.open(database, "owner-runtime") as owner:
        _create(owner, archive)
        waiting = owner.execute_action(command)
        assert isinstance(waiting, AwaitingChoiceReceipt)
        assert waiting.status == "awaiting_choice"
        assert waiting.basis_revision == 0
        assert waiting.choice.choice_id == "signal-response"
        assert tuple(option.option_id for option in waiting.choice.options) == (
            "echo",
            "ground",
        )
        snapshot = owner.snapshot("campaign-runtime")
        assert snapshot.revision == 0
        assert snapshot.actions == ()
        assert snapshot.events == ()
        assert snapshot.state["synthetic.clockwork.state"] == {"counter": 0, "bonus": 0}
        choice_projection = tuple(
            item
            for item in owner.player_projection("campaign-runtime")
            if item.kind is PlayerProjectionKind.MECHANICS_CHOICE
        )
        assert len(choice_projection) == 1
        assert choice_projection[0].safe_payload == {
            "action_id": "action-choice",
            "basis_revision": 0,
            "turn_id": "turn-runtime",
            "choice_id": "signal-response",
            "prompt": waiting.choice.prompt,
            "options": [
                {"option_id": "echo", "label": "Echo the signal"},
                {"option_id": "ground", "label": "Ground the signal"},
            ],
        }
        assert "steps" not in repr(choice_projection)
        assert owner.execute_action(command) == waiting
        with pytest.raises(CommandRejected) as blocked:
            owner.execute_action(_command(revision=0, suffix="other", steps=1))
        assert blocked.value.code == "mechanics_choice_pending"
        with pytest.raises(CommandRejected) as turn_blocked:
            owner.complete_turn("campaign-runtime", "turn-runtime")
        assert turn_blocked.value.code == "mechanics_choice_pending"
        with pytest.raises(CommandRejected) as lifecycle_blocked:
            owner.rename_campaign(
                operation_id="rename-while-choice-pending",
                campaign_id="campaign-runtime",
                display_name="Blocked rename",
                expected_revision=0,
            )
        assert lifecycle_blocked.value.code == "mechanics_choice_pending"

        with pytest.raises(IdempotencyConflict):
            owner.resolve_mechanics_choice(
                ResolveMechanicsChoiceCommand(
                    campaign_id="campaign-runtime",
                    action_id="action-choice",
                    resolution_id="resolution-wrong",
                    owner_id="owner-runtime",
                    expected_revision=0,
                    choice_id="signal-response",
                    option_id="not-an-option",
                )
            )
        unchanged = owner.snapshot("campaign-runtime")
        assert unchanged.revision == 0
        assert unchanged.actions == ()
        assert unchanged.events == ()
        assert owner.execute_action(command) == waiting

        resolved = owner.resolve_mechanics_choice(
            ResolveMechanicsChoiceCommand(
                campaign_id="campaign-runtime",
                action_id="action-choice",
                resolution_id="resolution-choice",
                owner_id="owner-runtime",
                expected_revision=0,
                choice_id="signal-response",
                option_id="ground",
            )
        )
        assert resolved.status is ActionStatus.COMMITTED
        assert resolved.resulting_revision == 1
        resolved_snapshot = owner.snapshot("campaign-runtime")
        assert resolved_snapshot.state["synthetic.clockwork.state"] == {
            "counter": 0,
            "bonus": 3,
        }
        assert len(resolved_snapshot.actions) == 1
        assert all(
            item.kind is not PlayerProjectionKind.MECHANICS_CHOICE
            for item in owner.player_projection("campaign-runtime")
        )
        assert owner.execute_action(command) == resolved
        assert (
            owner.resolve_mechanics_choice(
                ResolveMechanicsChoiceCommand(
                    campaign_id="campaign-runtime",
                    action_id="action-choice",
                    resolution_id="resolution-choice",
                    owner_id="owner-runtime",
                    expected_revision=0,
                    choice_id="signal-response",
                    option_id="ground",
                )
            )
            == resolved
        )
        with pytest.raises(IdempotencyConflict):
            owner.resolve_mechanics_choice(
                ResolveMechanicsChoiceCommand(
                    campaign_id="campaign-runtime",
                    action_id="action-choice",
                    resolution_id="resolution-choice",
                    owner_id="owner-runtime",
                    expected_revision=0,
                    choice_id="signal-response",
                    option_id="echo",
                )
            )

    with AuthorityOwner.open(database, "owner-reopened") as reopened:
        replay = reopened.execute_action(replace(command, owner_id="owner-reopened"))
        assert replay == resolved
        reopened_resolution = reopened.resolve_mechanics_choice(
            ResolveMechanicsChoiceCommand(
                campaign_id="campaign-runtime",
                action_id="action-choice",
                resolution_id="resolution-choice",
                owner_id="owner-reopened",
                expected_revision=0,
                choice_id="signal-response",
                option_id="ground",
            )
        )
        assert reopened_resolution == resolved
        assert reopened.snapshot("campaign-runtime").revision == 1
        print(
            "F3 MECHANICS RESERVATION",
            waiting.choice.choice_id,
            "resolved durable revision 1",
        )


def test_pending_package_choice_survives_portable_export_and_recovery(tmp_path: Path) -> None:
    source_database = tmp_path / "source.db"
    target_database = tmp_path / "target.db"
    archive_path = tmp_path / "pending-choice.sylvia-gm-campaign"
    archive = _archive(runtime_bytes=_choice_runtime_yaml())
    command = _command(revision=0, suffix="choice", steps=4)

    with AuthorityOwner.open(source_database, "owner-runtime") as owner:
        _create(owner, archive)
        waiting = owner.execute_action(command)
        assert isinstance(waiting, AwaitingChoiceReceipt)
        owner.export_campaign(
            operation_id="export-pending-choice",
            campaign_id="campaign-runtime",
            target=archive_path,
        )

    with AuthorityOwner.open(target_database, "owner-recovered") as recovered:
        recovered.recover_campaign(
            recovery_id="recover-pending-choice",
            source=archive_path,
        )
        replay = recovered.execute_action(replace(command, owner_id="owner-recovered"))
        assert replay == waiting
        resolved = recovered.resolve_mechanics_choice(
            ResolveMechanicsChoiceCommand(
                campaign_id="campaign-runtime",
                action_id="action-choice",
                resolution_id="resolution-recovered-choice",
                owner_id="owner-recovered",
                expected_revision=0,
                choice_id="signal-response",
                option_id="echo",
            )
        )
        assert resolved.resulting_revision == 1
        assert recovered.snapshot("campaign-runtime").state["synthetic.clockwork.state"] == {
            "counter": 4,
            "bonus": 0,
        }
        print("F3 MECHANICS CHOICE RECOVERY", "pending-to-committed", 1)


def test_admitted_runtime_list_constraints_are_public_and_fail_closed(tmp_path: Path) -> None:
    database = tmp_path / "authority.db"
    with AuthorityOwner.open(database, "owner-runtime") as owner:
        _create(owner, _archive())
        catalog = owner.catalog("campaign-runtime")
        assert catalog[0].input_schema["properties"]["witnesses"] == {
            "type": "array",
            "items": {
                "type": "string",
                "x-sylvia-gm-reference": "entity",
            },
            "minItems": 1,
            "maxItems": 2,
            "uniqueItems": True,
        }

        for suffix, witnesses, expected_issue in (
            ("empty", [], {"kind": "too_few_items", "minimum": 1, "maximum": 2}),
            (
                "duplicate",
                ["entity-a", "entity-a"],
                {"kind": "duplicate_items", "minimum": 1, "maximum": 2},
            ),
            (
                "over-bound",
                ["entity-a", "entity-b", "entity-c"],
                {"kind": "too_many_items", "minimum": 1, "maximum": 2},
            ),
        ):
            command = _command(revision=0, suffix=suffix, steps=1)
            with pytest.raises(CommandRejected) as caught:
                owner.execute_action(
                    replace(command, parameters={"steps": 1, "witnesses": witnesses})
                )
            assert caught.value.code == "invalid_schema"
            assert isinstance(caught.value, MechanicsRejected)
            assert caught.value.field_paths == ("witnesses",)
            assert caught.value.validation_issues[0].as_payload() == {
                "path": "witnesses",
                "expected_type": "array",
                "actual_type": "array",
                "parent_path": "$",
                **expected_issue,
            }

        snapshot = owner.snapshot("campaign-runtime")
        assert snapshot.revision == 0
        assert snapshot.events == ()
        assert snapshot.state["synthetic.clockwork.state"] == {"counter": 0, "bonus": 0}


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ({"type": "string", "min_items": 1}, "only valid for entity_id_list"),
        ({"type": "entity_id_list", "min_items": True}, "min_items must be an integer"),
        (
            {"type": "entity_id_list", "min_items": 3, "max_items": 2},
            "min_items cannot exceed max_items",
        ),
        (
            {"type": "entity_id_list", "unique_items": "yes"},
            "unique_items must be a boolean",
        ),
        (
            {"type": "entity_id_list", "resolve": None},
            "entity_id_list requires resolve: entity_list",
        ),
    ),
)
def test_ruleset_list_constraint_declarations_fail_closed(
    mutation: dict[str, object], expected: str
) -> None:
    raw = yaml.safe_load(_runtime_yaml())
    raw["tools"]["advance"]["parameters"][1].update(mutation)

    errors = validate_ruleset(yaml.safe_dump(raw, sort_keys=False))

    assert any(expected in error for error in errors), errors


def test_runtime_entity_lists_enforce_type_cardinality_and_uniqueness() -> None:
    tool = ToolDef(
        name="observe",
        parameters=[
            ToolParameter(
                "witnesses",
                "entity_id_list",
                resolve="entity_list",
                min_items=1,
                max_items=2,
                unique_items=True,
            )
        ],
    )
    tool_defs = {tool.name: tool}

    accepted = execute_tool("observe", {"witnesses": ["a", "b"]}, tool_defs, None, {}, {})
    assert accepted.success is True

    for witnesses, expected in (
        ([], "at least 1 items"),
        (["a", "a"], "unique items"),
        (["a", "b", "c"], "at most 2 items"),
        (["a", 1], "only entity ID strings"),
    ):
        rejected = execute_tool("observe", {"witnesses": witnesses}, tool_defs, None, {}, {})
        assert rejected.success is False
        assert expected in (rejected.error or "")

    bypass_probe = ToolDef(
        name="bypass-probe",
        parameters=[
            ToolParameter(
                "witnesses",
                "entity_id_list",
                min_items=1,
                max_items=1,
                unique_items=True,
            )
        ],
    )
    bypass = execute_tool(
        "bypass-probe", {"witnesses": []}, {bypass_probe.name: bypass_probe}, None, {}, {}
    )
    assert bypass.success is False
    assert "at least 1 items" in (bypass.error or "")


def test_declared_tool_without_runtime_bytes_fails_closed(tmp_path: Path) -> None:
    with AuthorityOwner.open(tmp_path / "authority.db", "owner-runtime") as owner:
        _create(owner, _archive(include_runtime=False))
        assert owner.catalog("campaign-runtime") == ()
        with pytest.raises(CommandRejected) as rejected:
            owner.execute_action(_command(revision=0, suffix="missing", steps=1))
        assert rejected.value.code == "tool_unavailable"
        assert owner.snapshot("campaign-runtime").revision == 0
        print("F3 MISSING RUNTIME", rejected.value.code, "authority unchanged")


def test_invalid_executable_bytes_are_rejected_before_candidate_publication(
    tmp_path: Path,
) -> None:
    raw = yaml.safe_load(_runtime_yaml())
    raw["tools"]["advance"]["parameters"][0]["type"] = "string"
    invalid_runtime = yaml.safe_dump(raw, sort_keys=False).encode("utf-8")
    with AuthorityOwner.open(tmp_path / "authority.db", "owner-runtime") as owner:
        with pytest.raises(CommandRejected) as rejected:
            owner.install_package_archive(
                io.BytesIO(_archive(runtime_bytes=invalid_runtime)),
                install_id="invalid-runtime-install",
            )
        assert rejected.value.code == "package_runtime_invalid"
        print("F3 INVALID RUNTIME", rejected.value.code, "not published")


def test_unresolved_procedure_trigger_is_rejected_before_publication(tmp_path: Path) -> None:
    raw = yaml.safe_load(_runtime_yaml())
    raw["procedures"] = {}
    unresolved_runtime = yaml.safe_dump(raw, sort_keys=False).encode("utf-8")
    with AuthorityOwner.open(tmp_path / "authority.db", "owner-runtime") as owner:
        with pytest.raises(CommandRejected) as rejected:
            owner.install_package_archive(
                io.BytesIO(_archive(runtime_bytes=unresolved_runtime)),
                install_id="unresolved-trigger-install",
            )
        assert rejected.value.code == "package_runtime_invalid"
        print("F3 UNRESOLVED TRIGGER", rejected.value.code, "not published")


def test_dynamic_mapping_index_is_safe_and_ruleset_data_driven() -> None:
    context = ExecutionContext(
        params={"ability": "dexterity", "target": "hero"},
        entities={
            "hero": {
                "saving_throws": {"dexterity": True, "wisdom": False},
                "relationships": {"riku": {"affection": 7}},
            }
        },
    )
    assert evaluate_expression("target.saving_throws[ability] == true", context) is True
    context.params["person"] = "riku"
    assert evaluate_expression("target.relationships[person].affection >= 7", context) is True
    print("F3 DYNAMIC MAP INDEX", "dexterity", "riku", "bounded AST")


def test_executable_validator_matches_handler_registry_and_allows_neutral_triggers() -> None:
    assert frozenset(_EFFECT_HANDLERS) == VALID_EFFECT_KEYS
    raw = yaml.safe_load(_runtime_yaml())
    raw["tools"]["advance"]["effects"] = [
        {"advance_time": "1 day"},
        {"transition_scene": "courtyard"},
        {"narrate": {"text": "The scene changes.", "style": "ambient"}},
        {"clock_create": {"id": "bond", "segments": 4}},
    ]
    raw["procedures"] = {
        "period_transition": {
            "trigger": "on_period_transition",
            "steps": [{"narrate": "A new period begins."}],
        }
    }
    assert validate_ruleset(yaml.safe_dump(raw, sort_keys=False)) == []
    raw["procedures"]["period_transition"]["trigger"] = "period transition!"
    errors = validate_ruleset(yaml.safe_dump(raw, sort_keys=False))
    assert any("unknown trigger" in error for error in errors)
    print("F3 VALIDATOR REGISTRY", len(VALID_EFFECT_KEYS), "neutral on_* trigger")


def test_effect_metadata_order_is_irrelevant_and_multiple_handlers_are_rejected() -> None:
    context = ExecutionContext()
    execute_effects(
        [{"store_as": "_ordered_roll", "dice": {"expression": "1d2"}}],
        context,
    )
    assert context.vars["_ordered_roll"].sides == 2

    raw = yaml.safe_load(_runtime_yaml())
    raw["tools"]["advance"]["effects"] = [
        {
            "increment": {"field": "state.counter", "amount": 1},
            "decrement": {"field": "state.counter", "amount": 1},
        }
    ]
    errors = validate_ruleset(yaml.safe_dump(raw, sort_keys=False))
    assert any("exactly one effect" in error for error in errors)
    print("F3 EFFECT DISPATCH", "metadata order stable", "dual handler rejected")


def test_rules_neutral_choice_suspends_and_deterministically_resumes_nested_tool() -> None:
    child = ToolDef(
        name="choose_signal",
        effects=[
            {"dice": {"expression": "1d6"}, "store_as": "_signal_roll"},
            {
                "request_choice": {
                    "choice_id": "signal-response",
                    "prompt": "Signal {_signal_roll.total}: choose a response.",
                    "options": [
                        {"id": "echo", "label": "Echo the signal"},
                        {"id": "ground", "label": "Ground the signal"},
                    ],
                },
                "store_as": "_response",
            },
            {"set": {"field": "_resolved_response", "value": "_response"}},
        ],
    )
    parent = ToolDef(
        name="route_signal",
        effects=[
            {
                "call": "choose_signal",
                "params": {},
                "export_context": {"_resolved_response": "_resolved_response"},
            }
        ],
    )
    tools = {"choose_signal": child, "route_signal": parent}
    resolution: ChoiceResolutionControl | None = None

    def nested(name: str, parameters: dict[str, object], parent: ExecutionContext):
        return execute_tool(
            name,
            parameters,
            tools,
            None,
            {},
            {},
            {},
            tool_executor=nested,
            extras=({} if resolution is None else {"mechanics_choice_resolution": resolution}),
            budget=parent.budget,
        )

    set_seed(73)
    with pytest.raises(MechanicsChoiceSuspended) as suspended:
        execute_tool(
            "route_signal",
            {},
            tools,
            None,
            {},
            {},
            {},
            tool_executor=nested,
        )
    request = suspended.value.request
    assert request.choice_id == "signal-response"
    assert tuple(option.option_id for option in request.options) == ("echo", "ground")
    assert request.prompt.startswith("Signal ")

    resolution = ChoiceResolutionControl("signal-response", "ground", request)
    set_seed(73)
    resumed = execute_tool(
        "route_signal",
        {},
        tools,
        None,
        {},
        {},
        {},
        tool_executor=nested,
        extras={"mechanics_choice_resolution": resolution},
    )
    assert resumed.success is True
    assert resumed.context["_resolved_response"] == "ground"
    assert resolution.consumed is True
    print("F3 MECHANICS CHOICE", request.choice_id, "nested suspend/resume")


@pytest.mark.parametrize(
    "mutation",
    (
        {"unknown": True},
        {"choice_id": "bad choice"},
        {"options": [{"id": "only", "label": "Only"}]},
        {
            "options": [
                {"id": "same", "label": "First"},
                {"id": "same", "label": "Second"},
            ]
        },
    ),
)
def test_rules_neutral_choice_declaration_fails_closed(mutation: dict[str, object]) -> None:
    raw = yaml.safe_load(_runtime_yaml())
    choice = {
        "choice_id": "signal-response",
        "prompt": "Choose a response",
        "options": [
            {"id": "echo", "label": "Echo"},
            {"id": "ground", "label": "Ground"},
        ],
    }
    choice.update(mutation)
    raw["tools"]["advance"]["effects"] = [{"request_choice": choice, "store_as": "_response"}]
    assert validate_ruleset(yaml.safe_dump(raw, sort_keys=False))


def test_condition_and_heal_mutations_refresh_same_action_entity_context() -> None:
    apply_bridge = AuthorityMemoryStateBridge(
        "synthetic.clockwork",
        {"synthetic.clockwork.entities": {"hero": {"conditions": []}}},
    )
    apply_context = ExecutionContext(
        params={"target": "hero"},
        entities={"hero": apply_bridge.get_entity("hero")},
        state_bridge=apply_bridge,
    )
    execute_effects(
        [
            {"apply_condition": {"target": "target", "condition": "stunned"}},
            {"set": {"field": "_conditions_after", "value": "target.conditions"}},
        ],
        apply_context,
    )
    assert apply_context.vars["_conditions_after"] == ["stunned"]

    remove_bridge = AuthorityMemoryStateBridge(
        "synthetic.clockwork",
        {"synthetic.clockwork.entities": {"hero": {"conditions": ["stunned"]}}},
    )
    remove_context = ExecutionContext(
        params={"target": "hero"},
        entities={"hero": remove_bridge.get_entity("hero")},
        state_bridge=remove_bridge,
    )
    execute_effects(
        [
            {"remove_condition": {"target": "target", "condition": "stunned"}},
            {"set": {"field": "_conditions_after", "value": "target.conditions"}},
        ],
        remove_context,
    )
    assert remove_context.vars["_conditions_after"] == []

    heal_bridge = AuthorityMemoryStateBridge(
        "synthetic.clockwork",
        {"synthetic.clockwork.entities": {"hero": {"hp": 0, "max_hp": 5}}},
    )
    heal_context = ExecutionContext(
        params={"target": "hero"},
        entities={"hero": heal_bridge.get_entity("hero")},
        state_bridge=heal_bridge,
    )
    execute_effects(
        [
            {"heal": {"target": "target", "amount": 2}},
            {"set": {"field": "_hp_after", "value": "target.hp"}},
        ],
        heal_context,
    )
    assert heal_context.vars["_hp_after"] == 2
    print("F3 MUTATION CONTEXT", "apply/remove/heal refreshed")


def test_resolved_entities_and_empty_combat_refresh_after_nested_mutations() -> None:
    bridge = AuthorityMemoryStateBridge(
        "synthetic.clockwork",
        {"synthetic.clockwork.entities": {"thing": {"count": 0}}},
    )
    parameter = ToolParameter("thing", "string", resolve="entity")
    child = ToolDef(
        name="child",
        parameters=[parameter],
        effects=[
            {"increment": {"field": "thing.count", "amount": 1}},
            {"set": {"field": "_child_count", "value": "thing.count"}},
        ],
    )
    parent = ToolDef(
        name="parent",
        parameters=[parameter],
        effects=[
            {"set": {"field": "thing.marker", "value": "'set-before-child'"}},
            {
                "call": "child",
                "params": {"thing": "{_thing_id}"},
                "export_context": {"_exported_child_count": "_child_count"},
            },
            {"set": {"field": "_parent_count", "value": "thing.count"}},
        ],
    )
    tools = {"child": child, "parent": parent}

    def nested(name: str, parameters: dict[str, object], outer: ExecutionContext):
        return execute_tool(
            name,
            parameters,
            tools,
            bridge,
            {},
            {},
            {},
            tool_executor=nested,
            budget=outer.budget,
        )

    result = execute_tool(
        "parent",
        {"thing": "thing"},
        tools,
        bridge,
        {},
        {},
        {},
        tool_executor=nested,
    )
    assert result.success is True, result.error
    assert result.context["_exported_child_count"] == 1
    assert result.context["_parent_count"] == 1
    assert bridge.get_entity("thing") == {"count": 1, "marker": "set-before-child"}

    distinct_bridge = AuthorityMemoryStateBridge(
        "synthetic.clockwork",
        {"synthetic.clockwork.entities": {"hero": {"count": 0}}},
        {
            "hero": {
                "participant_id": "hero",
                "kind": "player_character",
                "data": {"level": 7},
            }
        },
    )
    distinct_tool = ToolDef(
        name="distinct",
        parameters=[
            ToolParameter("actor", "string", resolve="participant"),
            ToolParameter("actor_state", "string", resolve="entity"),
        ],
        effects=[
            {"increment": {"field": "actor_state.count", "amount": 1}},
            {"set": {"field": "_participant_level", "value": "actor.data.level"}},
            {"set": {"field": "_entity_count", "value": "actor_state.count"}},
        ],
    )
    distinct_result = execute_tool(
        "distinct",
        {"actor": "hero", "actor_state": "hero"},
        {"distinct": distinct_tool},
        distinct_bridge,
        {},
        {},
    )
    assert distinct_result.success is True, distinct_result.error
    assert distinct_result.context["_participant_level"] == 7
    assert distinct_result.context["_entity_count"] == 1

    combat: dict[str, object] = {}
    combat_tool = ToolDef(
        name="combat",
        effects=[{"set": {"field": "combat.tick", "value": 1}}],
    )
    combat_result = execute_tool("combat", {}, {"combat": combat_tool}, None, {}, {}, combat)
    assert combat_result.success is True
    assert combat == {"tick": 1}
    print("F3 SAME-ACTION REFRESH", "set/increment/nested/combat")


def test_typed_mutable_effect_literals_are_fresh_for_each_execution() -> None:
    tool = ToolDef(
        name="collect",
        parameters=[ToolParameter("item", "string")],
        effects=[
            {"set": {"field": "_items", "value": []}},
            {"list_push": {"list": "_items", "value": "item"}},
        ],
    )
    tools = {"collect": tool}

    first = execute_tool("collect", {"item": "first"}, tools, None, {}, {})
    second = execute_tool("collect", {"item": "second"}, tools, None, {}, {})

    assert first.success is True, first.error
    assert second.success is True, second.error
    assert first.context["_items"] == ["first"]
    assert second.context["_items"] == ["second"]


def test_effect_operations_and_nested_tool_calls_are_bounded() -> None:
    context = ExecutionContext(
        params={"items": list(range(100))},
        budget=ExecutionBudget(max_operations=5, max_call_depth=3),
    )
    with pytest.raises(EffectExecutionError, match="operation budget"):
        execute_effects(
            [
                {
                    "for_each": {
                        "collection": "items",
                        "as": "_item",
                        "do": [{"set": {"field": "_seen", "value": "_item"}}],
                    }
                }
            ],
            context,
        )

    child = ToolDef(
        name="child",
        effects=[
            {"set": {"field": "_child_total", "value": 17}},
            {"set": {"field": "_child_outcome", "value": "'success'"}},
        ],
    )
    parent = ToolDef(
        name="parent",
        effects=[
            {
                "call": "child",
                "params": {},
                "export_context": {
                    "_exported_total": "_child_total",
                    "_exported_outcome": "_child_outcome",
                },
            }
        ],
    )
    recursive = ToolDef(name="recursive", effects=[{"call": "recursive", "params": {}}])
    tools = {"child": child, "parent": parent, "recursive": recursive}

    def nested(name: str, parameters: dict[str, object], parent: ExecutionContext):
        return execute_tool(
            name,
            parameters,
            tools,
            None,
            {},
            {},
            {},
            tool_executor=nested,
            budget=parent.budget,
        )

    exported = execute_tool(
        "parent",
        {},
        tools,
        None,
        {},
        {},
        {},
        tool_executor=nested,
        budget=ExecutionBudget(max_operations=100, max_call_depth=3),
    )
    assert exported.success is True
    assert exported.context["_exported_total"] == 17
    assert exported.context["_exported_outcome"] == "success"

    result = execute_tool(
        "recursive",
        {},
        tools,
        None,
        {},
        {},
        {},
        tool_executor=nested,
        budget=ExecutionBudget(max_operations=100, max_call_depth=3),
    )
    assert result.success is False
    assert result.error is not None and "call depth" in result.error
    print("F3 EXECUTION BUDGET", "operations=5", "call_depth=3", "nested exports bounded")


def test_package_hp_bridge_leaves_life_state_to_the_ruleset() -> None:
    bridge = AuthorityMemoryStateBridge(
        "synthetic.neutral",
        {
            "synthetic.neutral.entities": {
                "actor": {
                    "hp": 2,
                    "max_hp": 5,
                    "temp_hp": 1,
                    "is_alive": "ruleset-owned",
                }
            }
        },
    )

    assert bridge.take_damage("actor", 4) == 0
    assert bridge.read_field("actor", "temp_hp") == 1
    assert bridge.read_field("actor", "is_alive") == "ruleset-owned"
    assert bridge.heal("actor", 3) == 3
    assert bridge.read_field("actor", "is_alive") == "ruleset-owned"
    print("F3 NEUTRAL HP BRIDGE", "hp arithmetic only", "life state ruleset-owned")


def test_effect_state_writes_resolve_entity_parameter_names() -> None:
    bridge = AuthorityMemoryStateBridge(
        "synthetic.neutral",
        {"synthetic.neutral.entities": {"actor-17": {"temp_hp": 4, "ruleset_status": "active"}}},
    )
    context = ExecutionContext(
        params={"target": "actor-17"},
        entities={"actor-17": bridge.get_entity("actor-17")},
        state_bridge=bridge,
    )

    execute_effects(
        [
            {"decrement": {"field": "target.temp_hp", "amount": 3}},
            {"set": {"field": "target.ruleset_status", "value": "down"}},
        ],
        context,
    )

    assert bridge.read_field("actor-17", "temp_hp") == 1
    assert bridge.read_field("actor-17", "ruleset_status") == "down"
    print("F3 PARAMETER STATE WRITE", "target -> actor-17", "bounded fields")


def test_reference_parameters_with_the_same_id_keep_distinct_named_snapshots() -> None:
    bridge = AuthorityMemoryStateBridge(
        "synthetic.neutral",
        {
            "synthetic.neutral.entities": {
                "hero": {"conditions": ["poisoned"], "exhaustion_level": 2}
            }
        },
        {
            "hero": {
                "participant_id": "hero",
                "kind": "player_character",
                "validation_status": "validated",
                "data": {"level": 5},
            }
        },
    )
    tool = ToolDef(
        name="inspect_distinct_references",
        parameters=[
            ToolParameter("actor", "string", resolve="participant"),
            ToolParameter("actor_state", "string", resolve="entity"),
        ],
        pre_conditions=[
            {
                "condition": (
                    "actor.kind == 'player_character' and 'poisoned' in actor_state.conditions"
                ),
                "message": "Named reference snapshots collided.",
            }
        ],
        effects=[
            {"set": {"field": "_actor_kind", "value": "actor.kind"}},
            {"set": {"field": "_actor_id_seen", "value": "_actor_id"}},
            {"set": {"field": "_actor_state_id_seen", "value": "_actor_state_id"}},
            {
                "set": {
                    "field": "_exhaustion_level",
                    "value": "actor_state.exhaustion_level",
                }
            },
        ],
    )

    result = execute_tool(
        tool.name,
        {"actor": "hero", "actor_state": "hero"},
        {tool.name: tool},
        bridge,
        {},
        {},
        bridge.combat_state,
    )

    assert result.success is True, result.error
    assert result.context["_actor_kind"] == "player_character"
    assert result.context["_actor_id_seen"] == "hero"
    assert result.context["_actor_state_id_seen"] == "hero"
    assert result.context["_exhaustion_level"] == 2
    print("F3 DISTINCT REFERENCES", "hero participant", "hero entity", "no collision")
