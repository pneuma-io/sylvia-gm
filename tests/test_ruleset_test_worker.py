"""Disposable workers preserve campaign behaviour and isolate authored dice streams."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
import yaml

from sylvia_gm import dice
from sylvia_gm.application.package_materialization import (
    NormalizedPackageDraft,
    materialize_package_archive,
)
from sylvia_gm.application.ruleset_testing import run_ruleset_tests
from sylvia_gm.builder._ruleset_source import compile_ruleset_source
from sylvia_gm.domain._content_schema_ruleset import EXAMPLE as SOURCE
from sylvia_gm.domain._content_schema_ruleset_tests import EXAMPLE
from sylvia_gm.domain._runtime_executable_reference import RUNTIME_EXECUTABLE_DEFINITION
from sylvia_gm.domain.package_library import canonical_json_bytes

SIGNAL = Path(__file__).parent / "fixtures" / "rulesets" / "signal-and-static"
SIGNAL_SCENARIOS = (
    Path(__file__).parent / "fixtures" / "ruleset-scenarios" / "signal-and-static.json"
)


def test_nested_worker_error_identifies_rejection_without_raw_runtime_data():
    from mcp.shared.exceptions import MCPError

    from sylvia_gm.agent._ruleset_test_runner import _failure_message

    error = ExceptionGroup("private outer", [ExceptionGroup("private inner", [
        MCPError(-32602, "private runtime detail", {"code": "invalid_request_identity"}),
        RuntimeError("private path"),
    ])])
    assert _failure_message(error) == "MCPError (invalid_request_identity); RuntimeError"


def _run_signal(suite):
    declarations = json.loads((SIGNAL / "content/content-declarations.json").read_bytes())
    declarations["inclusions"][0]["links"]["artifacts"].append("tests/scenarios.json")
    draft = NormalizedPackageDraft(
        record=json.loads((SIGNAL / "package-record.json").read_bytes()),
        payloads={
            "runtime/ruleset.yaml": (SIGNAL / "runtime/ruleset.yaml").read_bytes(),
            "content/content-declarations.json": canonical_json_bytes(declarations),
            "tests/scenarios.json": canonical_json_bytes(suite),
        },
    )
    with materialize_package_archive((draft,)) as candidate:
        return run_ruleset_tests(candidate.archive)


def test_missing_rejection_checks_cannot_pass_a_parameterized_ruleset():
    suite = json.loads(SIGNAL_SCENARIOS.read_bytes())
    step = suite["scenarios"][0]["steps"][0]
    step["expect"] = [item for item in step["expect"] if item["subject"] != "tool_results"]
    result = _run_signal(suite)
    assert result["status"] == "incomplete_coverage", result
    assert result["coverage"]["missing_rejections"] == [
        "apply_operation_cost", "resolve_intrusion", "run_program",
    ]


def test_direct_panel_action_uses_the_actual_player_action_owner():
    suite = json.loads(SIGNAL_SCENARIOS.read_bytes())
    direct = deepcopy(suite["scenarios"][0])
    direct.update(id="panel-program", covers=["run_program"], rolls=[])
    direct["steps"] = [{
        "kind": "player_action",
        "source": {
            "kind": "panel", "surface_id": "signal-static-status", "action_id": "boost-signal",
        },
        "expect": [
            {"subject": "receipt", "path": ["status"], "equals": "committed"},
            {
                "subject": "state",
                "path": ["sylvia-gm.conformance.signal-static.state", "resources", "charge"],
                "equals": 3,
            },
            {
                "subject": "state",
                "path": ["sylvia-gm.conformance.signal-static.state", "last_program", "result"],
                "equals": 3,
            },
        ],
    }]
    direct["steps"].append({
        "kind": "player_action_response",
        "replies": [{"kind": "narration", "text": "The signal boost succeeds."}],
        "expect": [
            {"subject": "receipt", "path": [], "equals": None},
            {
                "subject": "state",
                "path": ["sylvia-gm.conformance.signal-static.state", "resources", "charge"],
                "equals": 3,
            },
        ],
    })
    suite["scenarios"].append(direct)
    result = _run_signal(suite)
    assert result["status"] == "passed", result
    assert result["scenarios"][1]["steps"][0]["coverage"]["actions"] == ["run_program"]


def _run(suite, *, source=None, **kwargs):
    source = deepcopy(SOURCE if source is None else source)
    if suite is not None:
        source["tests"] = suite
    compiled = compile_ruleset_source(yaml.safe_dump(source).encode())
    with materialize_package_archive((compiled.draft,)) as candidate:
        return run_ruleset_tests(candidate.archive, **kwargs)


def test_participant_reference_loads_through_the_real_campaign_adapter():
    source = deepcopy(SOURCE)
    source["participant_artifacts"] = [{
        "kind": "player_character",
        "schema_id": "example.visitor",
        "schema": {
            "$id": "example.visitor",
            "type": "object", "properties": {"name": {"type": "string"}},
            "required": ["name"], "additionalProperties": False,
        },
        "entity_state_schema": {
            "type": "object", "properties": {}, "additionalProperties": False,
        },
        "initial_state_calculations": [],
    }]
    suite = deepcopy(EXAMPLE)
    suite["scenarios"][0]["participants"] = [{
        "id": "visitor", "kind": "player_character", "schema_id": "example.visitor",
        "data": {"name": "Visitor"},
    }]
    suite["scenarios"][0]["steps"][0]["message"] = "Visitor requests a coin flip."
    result = _run(suite, source=source)
    assert result["status"] == "passed", result


def test_reported_dice_bonus_fails_then_passes_without_changing_expectations():
    source = deepcopy(SOURCE)
    source["mechanic_families"]["flip"]["effects"] = deepcopy(
        RUNTIME_EXECUTABLE_DEFINITION["effects"]["dice_result"]["reporting_example"]
    )
    suite = deepcopy(EXAMPLE)
    suite["scenarios"][0]["steps"][0]["expect"][-1]["path"] = [
        "result", "context", "_result",
    ]
    failed = _run(suite, source=source)
    assert failed["status"] == "failed", failed
    failure = failed["scenarios"][0]["steps"][0]["failures"][0]
    assert failure["expected"] == 2
    assert failure["actual"] == 3
    assert not failure.get("missing", False)
    source["mechanic_families"]["flip"]["effects"][0]["dice"]["modifier"] = "0"
    passed = _run(suite, source=source)
    assert passed["status"] == "passed", passed
    assert passed["closure_sha256"] != failed["closure_sha256"]


def test_worker_runs_multiple_turns_with_exact_dice_without_reseeding_parent():
    suite = deepcopy(EXAMPLE)
    scenario = suite["scenarios"][0]
    second = deepcopy(scenario["steps"][0])
    second["expect"][-1]["equals"] = 1
    scenario["steps"].append(second)
    scenario["rolls"].append({"sides": 2, "value": 1})
    before = dice._rng.getstate()
    result = _run(suite)
    assert dice._rng.getstate() == before
    assert result["status"] == "passed", result
    assert len(result["scenarios"][0]["steps"]) == 2
    assert result["scenarios"][0]["steps"][0]["coverage"]["actions"] == ["flip_coin"]


@pytest.mark.parametrize("check_rejection", [True, False])
def test_precondition_rejection_must_be_checked_to_establish_readiness(check_rejection):
    source = deepcopy(SOURCE)
    source["mechanic_families"]["flip"]["parameters"] = [
        {"name": "allowed", "type": "integer", "required": True, "source": "gm_judgment"}
    ]
    source["mechanic_families"]["flip"]["pre_conditions"] = [
        {"condition": "allowed > 0", "error": "A coin flip is not allowed now."}
    ]
    suite = deepcopy(EXAMPLE)
    step = suite["scenarios"][0]["steps"][0]
    step["replies"][0]["arguments"] = {"allowed": 1}
    step["replies"].insert(0, {
        "kind": "tool", "tool": "flip_coin", "arguments": {"allowed": 0},
    })
    if check_rejection:
        step["expect"].append({
            "subject": "tool_results", "path": [0, "result", "code"],
            "equals": "ruleset_precondition_failed",
        })
    result = _run(suite, source=source)
    assert result["status"] == ("passed" if check_rejection else "incomplete_coverage"), result
    assert result["coverage"]["missing_preconditions"] == ([] if check_rejection else ["flip_coin"])


def test_wrong_outcome_reports_expected_actual_and_step():
    suite = deepcopy(EXAMPLE)
    suite["scenarios"][0]["steps"][0]["expect"] = [
        {
            "subject": "receipt",
            "path": ["resulting_revision"],
            "equals": 900,
        }
    ]
    result = _run(suite)
    assert result["status"] == "failed", result
    failure = result["scenarios"][0]["steps"][0]
    assert failure["step"] == 0
    assert failure["failures"][0]["expected"] == 900
    assert failure["failures"][0]["actual"] == 1


def test_absent_tests_are_not_a_pass():
    assert _run(None)["status"] == "missing_tests"


def test_status_only_assertion_does_not_prove_mechanical_coverage():
    suite = deepcopy(EXAMPLE)
    suite["scenarios"][0]["steps"][0]["expect"] = [
        {"subject": "receipt", "path": ["status"], "equals": "committed"}
    ]
    result = _run(suite)
    assert result["status"] == "incomplete_coverage", result
    assert result["coverage"]["missing_actions"] == ["flip_coin"]


def test_claiming_an_unexecuted_action_does_not_count_as_coverage():
    suite = deepcopy(EXAMPLE)
    suite["scenarios"][0]["covers"].append("invented_action")
    result = _run(suite)
    assert result["status"] == "incomplete_coverage", result
    assert result["coverage"]["unproven_claims"] == ["invented_action"]


def test_saved_scenario_can_assert_rejection_then_success_in_the_real_loop():
    suite = deepcopy(EXAMPLE)
    step = suite["scenarios"][0]["steps"][0]
    step["replies"].insert(
        0, {"kind": "tool", "tool": "flip_coin", "arguments": {"invented": True}}
    )
    step["expect"].extend(
        [
            {"subject": "tool_results", "path": [0, "tool"], "equals": "flip_coin"},
            {
                "subject": "tool_results",
                "path": [0, "result", "status"],
                "equals": "rejected",
            },
            {"subject": "receipt", "path": ["resulting_revision"], "equals": 1},
        ]
    )
    result = _run(suite)
    assert result["status"] == "passed", result


@pytest.mark.parametrize(
    "options,status",
    [
        ({"timeout_seconds": 0.001}, "timed_out"),
        ({"cancelled": lambda: True}, "cancelled"),
    ],
)
def test_worker_termination_returns_an_unfinished_result(options, status):
    assert _run(deepcopy(EXAMPLE), **options)["status"] == status


@pytest.mark.parametrize("check_handoff", [True, False])
def test_real_game_loop_withholds_narration_until_the_gm_decision_finishes(check_handoff):
    source = deepcopy(SOURCE)
    source["mechanic_families"]["finish"] = {
        "parameters": [],
        "pre_conditions": [],
        "narrative": {},
        "effects": [{"set": {"field": "state.decision_owner", "value": "'player'"}}],
    }
    finish = deepcopy(source["public_actions"][0])
    finish.update(name="finish_turn", family="finish", description="Return control to the player.")
    source["public_actions"].append(finish)
    suite = deepcopy(EXAMPLE)
    case = suite["scenarios"][0]
    case["covers"].append("finish_turn")
    case["initial_state"] = [
        {
            "key": "state",
            "value": {
                "decision_owner": "gm",
                "active_side": "hostiles",
                "eligible_actor_ids": ["enemy"],
                "focused_actor": "enemy",
                "entities": {"enemy": {"name": "Enemy", "ap": 1}},
            },
        }
    ]
    case["steps"][0]["replies"] = [
        {
            "kind": "tool",
            "tool": "flip_coin",
            "arguments": {},
            "expect_request": {"decision_owner": "gm"},
        },
        {
            "kind": "tool",
            "tool": "finish_turn",
            "arguments": {},
            "expect_request": {
                "decision_owner": "gm",
                "unavailable_tools": ["update_campaign_file"],
            },
        },
        {
            "kind": "narration",
            "text": "The enemy finishes; it is your turn.",
            "expect_request": {
                "decision_owner": "player",
                "available_tools": ["update_campaign_file"],
            },
        },
    ]
    case["steps"][0]["expect"].append({"subject": "control", "path": ["owner"], "equals": "player"})
    case["steps"][0]["expect"][1] = {
        "subject": "tool_results",
        "path": [0, "result", "result", "context", "_result", "total"],
        "equals": 2,
    }
    if not check_handoff:
        for reply in case["steps"][0]["replies"]:
            reply.pop("expect_request", None)
    source["tests"] = suite
    compiled = compile_ruleset_source(yaml.safe_dump(source).encode())
    payloads = dict(compiled.draft.payloads)
    runtime = yaml.safe_load(payloads["runtime/ruleset.yaml"])
    runtime["model_decision_scope"] = {
        field: {"namespace": SOURCE["id"], "key": "state." + key}
        for field, key in {
            "decision_owner": "decision_owner",
            "active_side": "active_side",
            "eligible_actors": "eligible_actor_ids",
            "focused_actor": "focused_actor",
            "actor_entities": "entities",
        }.items()
    }
    payloads["runtime/ruleset.yaml"] = yaml.safe_dump(runtime).encode()
    with materialize_package_archive((replace(compiled.draft, payloads=payloads),)) as candidate:
        result = run_ruleset_tests(candidate.archive)
    assert result["status"] == ("passed" if check_handoff else "incomplete_coverage"), result
    assert result["coverage"]["missing_handoff"] is not check_handoff
