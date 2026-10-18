"""Ruleset-owned display data survives real action persistence without driving mechanics."""

import io
import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
import yaml

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.application.result_card_admission import (
    validate_activity_label,
    validate_result_card_declaration,
)
from sylvia_gm.application.result_cards import project_activity_label, project_result_card
from sylvia_gm.domain.authority import (
    ActionStatus,
    MechanicContext,
    PlayerProjectionKind,
    StateValue,
    WriterRole,
    owned_payload,
)
from sylvia_gm.domain.content_validation import ContentShapeError
from sylvia_gm.domain.errors import CommandRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests.test_spatial_runtime import _PACKAGE_ID, _archive, _effect, _runtime, _scene


def test_routine_labels_are_bounded_public_values_and_cannot_read_live_or_private_state():
    for declaration, expected in [("Prepare creature", "Prepare creature"),
                                  ({"path": "context.label"}, "Advance turn")]:
        validate_activity_label(declaration, path="$.activity_label")
        assert project_activity_label(declaration, parameters={},
                                      public_context={"label": "Advance turn"}) == expected
    for invalid in ["", "x" * 81, {"path": "state.secret"},
                    {"path": "context..secret"}, {"path": "context.label", "extra": True}, 4]:
        with pytest.raises(ContentShapeError):
            validate_activity_label(invalid, path="$.activity_label")
    for value in [None, "", "x" * 81, {"private": "value"}]:
        assert project_activity_label({"path": "context.label"}, parameters={},
                                      public_context={"label": value}) is None
    assert project_activity_label({"path": "state.secret"}, parameters={},
                                  public_context={"secret": "Hidden"}) is None


@pytest.mark.parametrize("footprint", ["inline", "compact"])
def test_declared_footprint_and_zero_detail_filter_are_saved_and_rendered(footprint):
    declaration = {
        "version": 1, "title": "Relay", "footprint": footprint,
        "details_label": "Calculation <recorded>",
        "summary": [{"value": 0, "emphasis": True}],
        "details": [
            {"label": "Empty term", "value": {"path": "context.term"}, "omit_zero": True},
            {"label": "Switch", "value": False, "omit_zero": True},
            {"label": "Literal", "value": "0", "omit_zero": True},
            {"label": "Nonzero", "value": -2, "omit_zero": True},
        ],
    }
    validate_result_card_declaration(declaration)
    card = project_result_card(declaration, parameters={}, public_context={"term": 0})
    assert card["footprint"] == footprint
    assert card["summary"][0]["value"] == 0
    assert [row["value"] for row in card["details"]] == [False, "0", -2]
    declaration["footprint"] = "changed"
    assert card["footprint"] == footprint
    source = Path(__file__).parents[1] / "src-tauri/frontend/app.js"
    script = r"""
const fs = require('fs'), assert = require('node:assert/strict');
global.window = {}; global.document = {addEventListener(){}};
eval(fs.readFileSync(process.argv[1], 'utf8'));
const card = JSON.parse(process.argv[2]);
const html = window.SylviaGMFrontend.toolResultMarkup(
  {tool_name:'relay', result:{status:'committed', result_card:card}}, '');
assert(html.includes(`result-card-${card.footprint}`));
assert(html.includes('Calculation &lt;recorded&gt;'));
assert(!html.includes('<details open'));
assert(!html.includes('D20') && !html.includes('Empty term'));
"""
    subprocess.run(["node", "-e", script, str(source), json.dumps(card)], check=True,
                   capture_output=True, text=True)


@pytest.mark.parametrize("sides", [6, 20])
def test_equation_displays_recorded_numbers_without_recalculation(sides):
    declaration = {
        "version": 1, "title": "Recorded check",
        "equation": {"die_sides": sides, "roll": {"path": "context.roll"},
                     "modifier": 0, "total": 99},
    }
    validate_result_card_declaration(declaration)
    card = project_result_card(declaration, parameters={}, public_context={"roll": 4})
    assert card["equation"] == {"die_sides": sides, "roll": 4, "modifier": 0, "total": 99}
    source = Path(__file__).parents[1] / "src-tauri/frontend/app.js"
    script = r"""
const fs = require('fs'), assert = require('node:assert/strict');
global.window = {}; global.document = {addEventListener(){}};
eval(fs.readFileSync(process.argv[1], 'utf8'));
const card = JSON.parse(process.argv[2]);
const html = window.SylviaGMFrontend.toolResultMarkup(
  {tool_name:'check', result:{status:'committed', result_card:card}}, '');
assert(html.includes(`D${card.equation.die_sides} roll: 4`));
assert(html.includes('result-modifier">+0'));
assert(html.includes('result-total">99'));
assert.equal(html.includes('result-die-facets'), card.equation.die_sides === 20);
assert.equal(html.includes('<rect'), card.equation.die_sides === 6);
"""
    subprocess.run(["node", "-e", script, str(source), json.dumps(card)], check=True,
                   capture_output=True, text=True)
    declaration["equation"]["roll"] = {"path": "state.private"}
    assert project_result_card(declaration, parameters={}, public_context={}) is None


@pytest.mark.parametrize("sides", [4, 6, 8, 10, 12])
def test_equation_uses_recorded_die_size_and_remains_detached(sides):
    declaration = {
        "version": 1, "title": "Recovery",
        "equation": {"die_sides": {"path": "context.die.sides"},
                     "roll": {"path": "context.die.total"}, "modifier": 2, "total": 5},
    }
    validate_result_card_declaration(declaration)
    context = {"die": {"sides": sides, "total": 3}}
    card = project_result_card(declaration, parameters={}, public_context=context)
    assert card["equation"] == {"die_sides": sides, "roll": 3, "modifier": 2, "total": 5}
    context["die"]["sides"] = 100
    assert card["equation"]["die_sides"] == sides
    for invalid in (None, True, 1, 1001, "d6"):
        context["die"]["sides"] = invalid
        assert project_result_card(declaration, parameters={}, public_context=context) is None
    declaration["equation"]["die_sides"] = {"path": "state.private"}
    with pytest.raises(ValueError):
        validate_result_card_declaration(declaration)


def test_recorded_result_rows_are_bounded_detached_and_cannot_resolve_new_references():
    declaration = {"version": 1, "title": "Initiative", "summary": {"path": "context.order"}}
    validate_result_card_declaration(declaration)
    context = {"order": [{"label": "Mira <recorded>", "value": 18},
                         {"label": "Guard", "value": 7}]}
    card = project_result_card(declaration, parameters={}, public_context=context)
    assert [(r["label"], r["value"]) for r in card["summary"]] == [
        ("Mira <recorded>", 18), ("Guard", 7),
    ]
    context["order"][0]["value"] = 1
    assert card["summary"][0]["value"] == 18
    for invalid in ([{"value": {"path": "context.other"}}], [{"value": 1}] * 17,
                    [{"label": "x" * 201, "value": 1}], [{"value": {"private": 1}}]):
        assert project_result_card(
            declaration, parameters={}, public_context={"order": invalid},
        ) is None
    declaration["summary"] = {"path": "state.private"}
    with pytest.raises(ValueError):
        validate_result_card_declaration(declaration)


def test_empty_recorded_rows_do_not_create_an_empty_result_card():
    declaration = {"version": 1, "summary": {"path": "context.results"}}
    validate_result_card_declaration(declaration)
    assert project_result_card(declaration, parameters={}, public_context={"results": []}) is None


def test_detail_rows_retain_large_composed_results_with_a_finite_bound():
    declaration = {"version": 1, "details": {"path": "context.results"}}
    rows = [{"label": f"Result {index}", "value": index} for index in range(128)]
    validate_result_card_declaration(declaration)
    card = project_result_card(declaration, parameters={}, public_context={"results": rows})
    assert len(card["details"]) == 128
    rows.append({"value": 129})
    assert project_result_card(declaration, parameters={}, public_context={"results": rows}) is None


def test_invalid_authored_card_reports_its_field_before_package_publication(tmp_path):
    runtime = yaml.safe_load(_runtime([]))
    runtime["tools"]["inspect_scene"]["metadata"]["result_card"] = {
        "version": 1, "summary": [{"value": {"path": "state.first"}}],
    }
    with AuthorityOwner.open(tmp_path / "invalid-card.db", "cards") as owner:
        with pytest.raises(CommandRejected) as rejected:
            owner.install_package_archive(
                io.BytesIO(_archive(yaml.safe_dump(runtime, sort_keys=False).encode())),
                install_id="invalid-card-package",
            )
        assert rejected.value.diagnostics[0].path == (
            "$.tools.inspect_scene.metadata.result_card.summary[0].value.path"
        )
        assert owner.package_candidates() == ()


def test_ruleset_controls_card_fields_labels_details_and_historical_button_arguments():
    declaration = {
        "version": 1,
        "title": "Door control",
        "summary": [{"value": {"path": "context.position", "labels": {"open": "Opened"}}}],
        "details": [{"label": "Noise", "value": 0}, {"value": ["Power available", "Manual latch"]}],
        "actions": [{
            "label": "Close", "tool": "close_door",
            "parameters": {"door_id": {"path": "parameters.door_id"}},
        }],
    }
    parameters = {"door_id": "cargo-door"}
    card = project_result_card(
        declaration, parameters=parameters, public_context={"position": "open"},
    )
    retained = deepcopy(card)
    assert card["summary"] == [{"value": "Opened", "emphasis": False}]
    assert card["details"][0] == {"label": "Noise", "value": 0, "emphasis": False}
    assert card["actions"][0]["parameters"] == {"door_id": "cargo-door"}
    parameters["door_id"] = "another-door"
    declaration["details"][1]["value"].append("Changed after execution")
    assert card == retained


@pytest.mark.parametrize("value", [
    {"path": "state.secret"},
    {"path": "spatial_evidence.intersections"},
    {"path": "context.unavailable"},
    {"path": "context.secret_object"},
    float("nan"),
])
def test_invalid_or_nonpublic_card_values_fall_back(value):
    assert project_result_card(
        {"version": 1, "summary": [{"value": value}]}, parameters={},
        public_context={"secret_object": {"hidden": "Do not render arbitrary objects"}},
    ) is None


@pytest.mark.parametrize(
    "card_path", ["context._card_value", "context._private_ray", "context._unavailable"],
)
def test_card_is_saved_with_action_and_invalid_presentation_does_not_undo_it(tmp_path, card_path):
    runtime = yaml.safe_load(_runtime([
        _effect("ray", store_as="_private_ray"),
        {"set": {"field": "state.first", "value": "'opened'"}},
        {"set": {"field": "_card_value", "value": "'Opened'"}},
    ]))
    runtime["tools"]["inspect_scene"]["metadata"]["result_card"] = {
        "version": 1, "title": "Access panel", "summary": [{"value": {"path": card_path}}],
        "footprint": "compact", "details_label": "Calculation",
        "equation": {"die_sides": 6, "roll": 4, "modifier": 0, "total": 4},
    }
    runtime["tools"]["inspect_scene"]["metadata"]["activity_label"] = "Open access panel"
    database = tmp_path / "cards.db"
    command = ActionCommand(
        campaign_id="card-campaign", action_id="open-panel", idempotency_key="open-panel",
        owner_id="cards", writer=WriterRole.DM, expected_revision=0, turn_id="turn-cards",
        package_id=_PACKAGE_ID, tool_name="inspect_scene", parameters={},
    )
    with AuthorityOwner.open(database, "cards") as owner:
        candidate = owner.install_package_archive(
            io.BytesIO(_archive(yaml.safe_dump(runtime, sort_keys=False).encode())),
            install_id="card-package",
        )
        owner.create_campaign(CampaignCreate(
            campaign_id=command.campaign_id, display_name="Cards", writer=WriterRole.DM,
            package_lock=candidate.package_lock, turn_id=command.turn_id,
            initial_state=(StateValue(_PACKAGE_ID, "state", {
                "spatial_scene": _scene(), "first": None, "second": None,
            }),),
        ))
        result = StatelessMcpMechanics(owner).execute_action(command)
        assert result.status is ActionStatus.COMMITTED
        assert result.result["activity_label"] == "Open access panel"
        state = owner.snapshot(command.campaign_id).state
        assert state[f"{_PACKAGE_ID}.state"]["first"] == "opened"
        assert "_private_ray" not in result.result["context"]
        if card_path == "context._card_value":
            assert result.result["result_card"]["summary"][0]["value"] == "Opened"
            assert result.result["result_card"]["equation"]["total"] == 4
        else:
            assert result.result["result_card"] is None
        before = next(item for item in owner.player_projection(command.campaign_id)
                      if item.kind is PlayerProjectionKind.TOOL_CARD)
    with AuthorityOwner.open(database, "cards") as owner:
        after = next(item for item in owner.player_projection(command.campaign_id)
                     if item.kind is PlayerProjectionKind.TOOL_CARD)
        assert after == before
        replay = StatelessMcpMechanics(owner).execute_action(command)
        assert replay.result == result.result
        assert replay.resulting_revision == 1


def test_shared_renderer_uses_saved_fields_and_invalid_cards_show_only_operation_status():
    card = project_result_card({
        "version": 1, "title": "Cargo <door>",
        "summary": [{"label": "Latch", "value": "Released", "emphasis": True}],
        "details": [{"label": "Noise", "value": 0}],
        "actions": [{"label": "Close", "tool": "close_door", "parameters": {"id": "cargo"}}],
    }, parameters={}, public_context={})
    source = Path(__file__).parents[1] / "src-tauri/frontend/app.js"
    script = r"""
const fs = require('fs'), assert = require('node:assert/strict');
global.window = {}; global.document = {addEventListener(){}};
eval(fs.readFileSync(process.argv[1], 'utf8'));
const render = window.SylviaGMFrontend.toolResultMarkup;
const card = JSON.parse(process.argv[2]);
const html = render({tool_name:'open_door', result:{status:'committed', result_card:card}}, '');
assert(html.includes('Cargo &lt;door&gt;'));
assert(html.includes('Released'));
assert(html.includes('<details class="result-card-details">'));
assert(!html.includes('<details open'));
assert(html.includes('data-result-card-source-action-id=""'));
assert(html.includes('data-result-card-button-index="0"'));
assert(html.includes('disabled'));
assert(!html.includes('data-result-card-tool') && !html.includes('data-result-card-parameters'));
assert(!html.includes('D20') && !html.includes('Skill check'));
const fallback = render({tool_name:'d20_test', result:{status:'committed',
  result_card:{version:999}, context:{_outcome:'success'}}}, '');
assert(fallback.includes('The saved Ruleset result cannot be displayed.'));
assert(fallback.includes('role="status"'));
assert(!fallback.includes('Success') && !fallback.includes('Skill check'));
const rejected = render({tool_name:'open_door', result:{status:'rejected', result_card:null}}, '');
assert(rejected.includes('This action was rejected.'));
assert(rejected.includes('role="status"'));
assert(!rejected.includes('Success') && !rejected.includes('Skill check'));
"""
    subprocess.run(
        ["node", "-e", script, str(source), json.dumps(card)],
        check=True, capture_output=True, text=True,
    )


@pytest.mark.parametrize("tool_name", ("d20_test", "unbound_d20_test"))
def test_bundled_dnd_d20_cards_present_recorded_success_failure_and_no_roll(tool_name) -> None:
    root = Path(__file__).parents[1]
    package_root = root / "rulesets" / "srd-lite"
    record = json.loads((package_root / "package-record.json").read_text(encoding="utf-8"))
    character = json.loads(
        (root / "src/sylvia_gm/campaign/the-rift-awakening.json").read_text(encoding="utf-8")
    )["player_archetypes"][0]
    declared = next(tool for tool in record["tools"] if tool["name"] == tool_name)
    mechanic = materialize_package_mechanic(
        package_id=record["id"],
        package_version=record["version"],
        package_sha256="a" * 64,
        declaration=ToolDeclaration(
            name=declared["name"],
            capability=declared["capability"],
            input_schema=owned_payload(declared["input_schema"]),
            state_predicates=(),
            turn_statuses=tuple(declared["turn_statuses"]),
            writers=tuple(declared["writers"]),
        ),
        runtime_bytes=(package_root / "runtime" / "ruleset.yaml").read_bytes(),
    )
    parameters = {
        "kind": "ability_check",
        "ability_modifier": 2,
        "proficiency_bonus": 3,
        "proficiency_factor": "normal",
        "circumstantial_modifier": 1,
        "target_number": 15,
        "advantage": False,
        "disadvantage": False,
        "forego_save": False,
    }

    def result(action_id: str, values: dict[str, object]) -> dict[str, object]:
        outcome = mechanic(MechanicContext(
            campaign_id="dnd-card-campaign",
            turn_id="dnd-card-turn",
            action_id=action_id,
            state=owned_payload({
                f"{record['id']}.entities": {"synthetic-character-id": character["entity_state"]},
            }),
            parameters=owned_payload(values),
            participants=owned_payload({
                "synthetic-character-id": {
                    "kind": "player_character",
                    "validation_status": "validated",
                    "package_id": record["id"],
                    "data": {**character["participant"], "name": "Elara Reed"},
                },
            }),
        ))
        return dict(outcome.result)

    success = result("dnd-card-success", {**parameters, "target_number": 5})
    failure = result("dnd-card-failure", {**parameters, "target_number": 30})
    automatic = result(
        "dnd-card-no-roll",
        {**parameters, "kind": "saving_throw", "forego_save": True},
    )

    def rows(card: dict[str, object], section: str) -> dict[str, object]:
        return {row["label"]: row["value"] for row in card[section]}

    success_card = success["result_card"]
    failure_card = failure["result_card"]
    automatic_card = automatic["result_card"]
    success_summary = rows(success_card, "summary")
    failure_summary = rows(failure_card, "summary")
    automatic_summary = rows(automatic_card, "summary")
    assert success_card["title"] == failure_card["title"] == "Ability check"
    assert automatic_card["title"] == "Saving throw"
    assert success_card["subtitle"] == "Ability check"
    assert success_summary["Outcome"] == "Success"
    assert failure_summary["Outcome"] == "Failure"
    assert success_summary["DC"] == 5
    assert failure_summary["DC"] == 30
    for outcome in (success, failure):
        equation = outcome["result_card"]["equation"]
        assert equation["total"] == outcome["context"]["_adjusted_total"]
        assert equation["roll"] == outcome["context"]["_d20_test"]["total"]
        assert equation["modifier"] == equation["total"] - equation["roll"]
        assert equation["die_sides"] == 20
    assert rows(success_card, "details")["D20 results"] == list(
        success["context"]["_d20_test"]["results"]
    )
    assert all(row["value"] != 0 for row in failure_card["details"])
    assert automatic_summary == {
        "Outcome": "Failure",
        "DC": 15,
        "Resolution": "Automatic failure — no roll",
    }
    assert "D20 results" not in rows(automatic_card, "details")
    assert "Total" not in automatic_summary
    assert "equation" not in automatic_card
    assert success_card["actions"] == failure_card["actions"] == automatic_card["actions"] == []
    zero = result("dnd-card-zero-modifiers", {
        **parameters, "ability_modifier": 0, "proficiency_bonus": 0,
        "circumstantial_modifier": 0,
    })["result_card"]
    assert zero["equation"]["modifier"] == 0
    assert [row["label"] for row in zero["details"]] == ["D20 results"]
    assert "Resolution" not in rows(zero, "summary")
    if tool_name == "d20_test":
        named = result("dnd-card-character", {
            **parameters, "actor": "synthetic-character-id",
            "actor_state": "synthetic-character-id",
            "ability": "dexterity",
        })
        assert rows(named["result_card"], "summary")["Actor"] == "Elara Reed"
        assert named["result_card"]["title"] == "Dexterity"
        assert "synthetic-character-id" not in json.dumps(named["result_card"])
