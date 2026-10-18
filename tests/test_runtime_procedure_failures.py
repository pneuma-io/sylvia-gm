"""Procedure failures reach the actual owner boundary without partial commits."""

from pathlib import Path

import pytest
import yaml

from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.runtime._procedure_executor import trigger_procedure
from sylvia_gm.runtime._schema_validator import _validate_procedures
from sylvia_gm.runtime._types import ProcedureDef, ToolDef
from tests.test_alpha_f3_package_execution import _archive, _command, _create, _runtime_yaml


def test_accepted_condition_evaluation_failure_is_not_a_success() -> None:
    definition = {"trigger": "on_turn_start", "condition": "1 % 0 == 0", "steps": []}
    assert _validate_procedures({"broken": definition}, set()) == []
    result = trigger_procedure(
        "on_turn_start", {"broken": ProcedureDef("broken", **definition)}, {}, None, {}, {}
    )
    assert not result.success
    assert "Modulo by zero" in result.error


def test_condition_failure_rolls_back_prior_tool_mutation_and_reopen(tmp_path: Path) -> None:
    raw = yaml.safe_load(_runtime_yaml())
    raw["procedures"]["counter_advanced"]["condition"] = "1 % 0 == 0"
    database = tmp_path / "authority.db"
    with AuthorityOwner.open(database, "owner-runtime") as owner:
        _create(owner, _archive(runtime_bytes=yaml.safe_dump(raw).encode()))
        before = owner.snapshot("campaign-runtime")
        receipt = owner.execute_action(_command(revision=0, suffix="broken-condition", steps=3))
        assert receipt.status is ActionStatus.FAILED
        failed = owner.snapshot("campaign-runtime")
        assert failed.state == before.state
        assert failed.mutations == before.mutations
        assert failed.consequences == before.consequences
        assert failed.revision == before.revision + 1
    with AuthorityOwner.open(database, "owner-reopened") as owner:
        assert owner.snapshot("campaign-runtime") == failed


@pytest.mark.parametrize("condition,bonus", [("1 == 0", 0), ("1 == 1", 1)])
def test_valid_false_condition_stays_skipped_and_true_executes(
    tmp_path: Path, condition: str, bonus: int
) -> None:
    raw = yaml.safe_load(_runtime_yaml())
    raw["procedures"]["counter_advanced"]["condition"] = condition
    with AuthorityOwner.open(tmp_path / "authority.db", "owner-runtime") as owner:
        _create(owner, _archive(runtime_bytes=yaml.safe_dump(raw).encode()))
        owner.execute_action(_command(revision=0, suffix="valid-condition", steps=3))
        assert owner.snapshot("campaign-runtime").state["synthetic.clockwork.state"] == {
            "counter": 3,
            "bonus": bonus,
        }


def test_admitted_call_with_same_procedure_name_calls_tool_and_propagates_failure() -> None:
    # Admission defines call as a tool reference; explicit trigger invokes procedures.
    definitions = {
        "outer": {"trigger": "on_start", "steps": [{"call": "inner"}]},
        "inner": {"trigger": "on_other", "steps": []},
    }
    assert _validate_procedures(definitions, {"inner"}) == []
    procedures = {name: ProcedureDef(name, **value) for name, value in definitions.items()}
    tools = {
        "inner": ToolDef(
            "inner", effects=[{"require": {"condition": "1 == 0", "error": "inner rejected"}}]
        )
    }
    result = trigger_procedure("on_start", procedures, tools, None, {}, {})
    assert not result.success
    assert "inner rejected" in result.error


def test_explicit_nested_trigger_failure_is_not_outer_success() -> None:
    definitions = {
        "outer": ProcedureDef("outer", "on_start", steps=[{"trigger": {"name": "on_inner"}}]),
        "inner": ProcedureDef(
            "inner",
            "on_inner",
            steps=[{"require": {"condition": "1 == 0", "error": "nested rejected"}}],
        ),
    }
    result = trigger_procedure("on_start", definitions, {}, None, {}, {})
    assert not result.success
    assert "nested rejected" in result.error


@pytest.mark.parametrize("kind", ["call", "trigger"])
def test_admitted_nested_failure_preserves_authority(tmp_path: Path, kind: str) -> None:
    raw = yaml.safe_load(_runtime_yaml())
    failure = {"require": {"condition": "1 == 0", "error": "nested rejected"}}
    raw["procedures"]["inner"] = {"trigger": "on_inner", "steps": [failure]}
    if kind == "call":
        raw["tools"]["inner"] = {
            "description": "Internal rejecting tool",
            "parameters": [],
            "effects": [failure],
        }
        raw["procedures"]["counter_advanced"]["steps"] = [{"call": "inner"}]
    else:
        raw["procedures"]["counter_advanced"]["steps"] = [{"trigger": {"name": "on_inner"}}]
    with AuthorityOwner.open(tmp_path / "authority.db", "owner-runtime") as owner:
        _create(owner, _archive(runtime_bytes=yaml.safe_dump(raw).encode()))
        before = owner.snapshot("campaign-runtime")
        receipt = owner.execute_action(_command(revision=0, suffix=kind, steps=3))
        assert receipt.status is ActionStatus.FAILED
        after = owner.snapshot("campaign-runtime")
        assert after.state == before.state
        assert after.mutations == before.mutations
        assert after.consequences == before.consequences
