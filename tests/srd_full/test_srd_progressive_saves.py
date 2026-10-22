"""Cumulative saves use actual dice and refuse invented prior progression."""

from copy import deepcopy
from random import Random

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_progressive_saves import progressive_save_tool
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget

TOOL = "resolve_srd_progressive_save"
SRD = budget.SRD


@pytest.fixture
def run(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    tool = progressive_save_tool()
    runtime = _replace_tools(
        (budget.ROOT / "runtime/ruleset.yaml").read_text(encoding="utf-8"), {TOOL: tool}
    ).encode()
    mechanic = materialize_package_mechanic(
        package_id=SRD,
        package_version="5.2.1",
        package_sha256="a" * 64,
        runtime_bytes=runtime,
        declaration=ToolDeclaration(
            name=TOOL,
            capability="dnd.srd.5.2.1.action-economy",
            input_schema=owned_payload(_tool_schema(tool, tool_name=TOOL)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )
    original = budget._mechanic
    monkeypatch.setattr(
        budget, "_mechanic", lambda name: mechanic if name == TOOL else original(name)
    )
    return budget._run


def _state():
    state = budget._state()
    state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"] = {"constitution": 100}
    state[f"{SRD}.state"] = {
        "timed_condition_sources": {
            "tc001": {
                "installed": True,
                "target_state_id": "hero",
                "save_dc": 20,
                "save_ability": "constitution",
                "condition": "none",
                "progressive_successes": 0,
                "progressive_failures": 0,
            }
        }
    }
    return state


def test_mixed_actual_saves_are_cumulative_and_leave_outcome_to_producer(run):
    state = _state()
    for modifier, expected in ((100, (1, 0)), (-100, (1, 1)), (100, (2, 1)), (100, (3, 1))):
        state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"]["constitution"] = modifier
        result = run(TOOL, state, target_state="hero", source_id="tc001").result
        context = result["context"]
        assert context["_progressive_save_roll"]["count"] == 1
        assert context["_progressive_save_roll"]["sides"] == 20
        assert context["_progressive_save_outcome"] == ("success" if modifier > 0 else "failure")
        source = state[f"{SRD}.state"]["timed_condition_sources"]["tc001"]
        assert (source["progressive_successes"], source["progressive_failures"]) == expected
        assert (context["_progressive_successes"], context["_progressive_failures"]) == expected
        assert source["installed"] is True


@pytest.mark.parametrize(
    "field,value",
    [("progressive_failures", None), ("progressive_successes", 3), ("target_state_id", "guard")],
)
def test_invalid_or_finished_source_rejects_without_dice_or_mutation(
    run, monkeypatch, field, value
):
    state = _state()
    state[f"{SRD}.state"]["timed_condition_sources"]["tc001"][field] = value
    before = deepcopy(state)
    monkeypatch.setattr(
        dice, "roll", lambda *args, **kwargs: pytest.fail("Rejected source rolled dice")
    )
    with pytest.raises(RulesetPreconditionRejected):
        run(TOOL, state, target_state="hero", source_id="tc001")
    assert state == before
