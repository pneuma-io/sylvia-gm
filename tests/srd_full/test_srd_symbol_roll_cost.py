"""Symbol uses existing attack/check and Frightened branches without charging ordinary saves."""

from copy import deepcopy
from functools import lru_cache
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_triggers import _symbol_consumers

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_action_budget, test_srd_spell_result_cards
from tests import test_srd_spell_triggers as owner
from tests import test_srd_symbol_triggers as symbol
from tests.srd_full.test_srd_action_budget import SRD, _run


@lru_cache
def _runtimes():
    """Compare the actual completed owner with only its two owning Symbol hooks removed."""
    runtime = deepcopy(symbol._runtime()[0])
    base = deepcopy(runtime)
    native = next(
        effect
        for effect in base["tools"]["d20_test"]["effects"]
        if effect.get("condition") == "actor_state != null"
        and "'invisible' in actor_state.conditions" in str(effect)
    )
    attack_checks = next(
        effect
        for effect in native["on_true"]
        if effect.get("condition") == "kind == 'ability_check' or kind == 'attack_roll'"
    )
    hooks = [
        effect
        for effect in attack_checks["on_true"]
        if effect.get("condition") == "actor_state.symbol_affliction_managed == true"
        and "check_srd_symbol_afflictions" in str(effect)
    ]
    assert len(hooks) == 1
    attack_checks["on_true"].remove(hooks[0])
    frightened = next(
        effect
        for effect in base["tools"]["resolve_srd_control_behavior_conditions"]["effects"]
        if effect.get("condition") == "target_state.timed_condition_unowned_frightened != null"
    )
    hooks = [
        effect
        for effect in frightened["on_true"]
        if effect.get("condition") == "target_state.symbol_affliction_managed == true"
        and "check_srd_symbol_afflictions" in str(effect)
    ]
    assert len(hooks) == 1
    frightened["on_true"].remove(hooks[0])
    return [
        (value, yaml.safe_dump(value, sort_keys=False).encode()) for value in (base, runtime)
    ]


@lru_cache
def _mechanic(name, version):
    runtime, payload = _runtimes()[version]
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=payload,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _owning_runtime(monkeypatch):
    monkeypatch.setattr(owner, "_runtime", symbol._runtime)
    monkeypatch.setattr(owner, "_mechanic", symbol._mechanic)
    monkeypatch.setattr(test_srd_action_budget, "_mechanic", symbol._mechanic)
    monkeypatch.setattr(
        test_srd_spell_result_cards,
        "_spell_definitions",
        lambda: symbol._runtime()[0]["definitions"]["spells"],
    )
    monkeypatch.setattr(dice, "set_seed", lambda _: None)


def _compare(monkeypatch, tool, state, parameters):
    count = 0
    consume = ExecutionBudget.consume_operation

    def measure(budget):
        nonlocal count
        count += 1
        consume(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", measure)
    outcomes = []
    for version in (0, 1):
        monkeypatch.setattr(
            test_srd_action_budget,
            "_mechanic",
            lambda name, v=version: _mechanic(name, v),
        )
        saved = deepcopy(state)
        count = 0
        monkeypatch.setattr(dice, "_rng", Random(73))
        result = _run(tool, saved, **parameters)
        outcomes.append((result, saved, count))
    return outcomes


@pytest.mark.parametrize("effect", [None, "discord", "fear"])
def test_actual_dex_save_does_not_pay_a_symbol_guard(monkeypatch, effect):
    if effect:
        state, identity = symbol._symbol(effect)
        owner._event(state, identity, event="open")
    else:
        state, _, _, _ = owner._cast("alarm")
    outcomes = _compare(
        monkeypatch,
        "d20_test",
        state,
        {
            "actor_state": "other",
            "kind": "saving_throw",
            "ability": "dexterity",
            "ability_modifier": 0,
            "proficiency_bonus": 0,
            "proficiency_factor": "none",
            "circumstantial_modifier": 0,
            "target_number": 18,
        },
    )
    base, current = outcomes
    assert current[2] == base[2]
    assert base[0].result["result_card"] == current[0].result["result_card"]
    assert base[1] == current[1]
    for field in ("_d20_test", "_outcome", "_adjusted_total", "_effective_disadvantage"):
        assert base[0].result["context"][field] == current[0].result["context"][field]
    print(f"Symbol {effect}: Dex save {current[2]} equals original {base[2]} operations")
    assert ExecutionBudget().max_operations == 10_000


def test_non_frightened_projection_does_not_pay_a_symbol_guard(monkeypatch):
    state, _, _, _ = owner._cast("alarm")
    base, current = _compare(
        monkeypatch,
        "resolve_srd_control_behavior_conditions",
        state,
        {"target_state": "other", "lasting_state": "state"},
    )
    assert current[2] == base[2]
    assert base[1] == current[1]
    print(f"Non-Frightened projection: {current[2]} equals original {base[2]} operations")


def test_symbol_consumer_emission_is_idempotent_and_retains_the_native_branch():
    base, _ = _runtimes()[0]
    runtime, _ = _runtimes()[1]
    assert _symbol_consumers(runtime) == {}
    attack_checks = [
        child
        for effect in runtime["tools"]["d20_test"]["effects"]
        if effect.get("condition") == "actor_state != null"
        for child in effect.get("on_true", [])
        if child.get("condition") == "kind == 'ability_check' or kind == 'attack_roll'"
    ]
    assert len(attack_checks) == 1
    assert "check_srd_symbol_afflictions" in str(attack_checks[0])
    unowned = deepcopy(attack_checks[0])
    unowned["on_true"] = [
        effect
        for effect in unowned["on_true"]
        if not (
            effect.get("condition") == "actor_state.symbol_affliction_managed == true"
            and "check_srd_symbol_afflictions" in str(effect)
        )
    ]
    native = next(
        child
        for effect in base["tools"]["d20_test"]["effects"]
        if effect.get("condition") == "actor_state != null"
        for child in effect.get("on_true", [])
        if child.get("condition") == "kind == 'ability_check' or kind == 'attack_roll'"
    )
    assert unowned == native



@pytest.mark.parametrize("effect", ["discord", "fear"])
@pytest.mark.parametrize("kind", ["attack_roll", "ability_check"])
def test_actual_active_symbol_roll_uses_saved_source_and_retains_native_card(
    monkeypatch, effect, kind
):
    state, identity = symbol._symbol(effect)
    owner._event(state, identity, event="open")
    _, current = _compare(
        monkeypatch,
        "d20_test",
        state,
        {
            "actor_state": "other",
            "kind": kind,
            "ability": "wisdom",
            "ability_modifier": 0,
            "proficiency_bonus": 0,
            "proficiency_factor": "none",
            "circumstantial_modifier": 0,
            "target_number": 18,
        },
    )
    context = current[0].result["context"]
    assert context["_effective_disadvantage"] is True
    assert len(context["_d20_test"]["results"]) == 2
    assert current[0].result["result_card"]["title"] == "Wisdom"
    assert current[1] == state
