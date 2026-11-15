"""Final exploration delegation retains actual cast inputs, private outputs and bound snapshots."""

import hashlib
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
from scripts.srd_full.srd_exploration_cast_delegate import CAST, OWNER, bind_runtime
from scripts.srd_full.srd_retained_spell_parameters import bind_runtime as trim_retained
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.runtime import _effect_executor
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cases
from tests import test_srd_spell_special_damage as special

NATIVE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-exploration-cast-delegate/runtime.yaml")
)


@lru_cache
def _native():
    encoded = NATIVE.read_bytes()
    assert hashlib.sha256(encoded).hexdigest() == (
        "9f9ab63e3a26ee866bdf2e24ff7693e2ae0a8d542aaba276deed611c7c69fd3f"
    )
    return encoded.decode()


@lru_cache
def _compact():
    text = bind_runtime(trim_retained(_native()))
    runtime = _load_runtime(text)
    assert not validate_ruleset_data(runtime)
    return text.encode(), runtime["definitions"]["spells"]


def test_final_delegate_preserves_complete_public_interface_and_all_unowned_tools():
    before = _load_runtime(trim_retained(_native()))
    text = _compact()[0].decode()
    after = _load_runtime(text)
    assert bind_runtime(text) == text
    assert before["tools"][CAST] == before["tools"][OWNER]
    original, delegated = before["tools"][OWNER], after["tools"][OWNER]
    assert {key: value for key, value in original.items() if key != "effects"} == {
        key: value for key, value in delegated.items() if key != "effects"
    }
    assert len(delegated["effects"]) == 1
    call = delegated["effects"][0]
    assert call["call"] == CAST and call["export_context"] == "*"
    assert set(call["params"]) == {p["name"] for p in original["parameters"]}
    assert call["params"]["target"] == "{_target_id}"
    assert call["params"]["targets"] == "{targets}"
    assert {name: tool for name, tool in before["tools"].items() if name != OWNER} == {
        name: tool for name, tool in after["tools"].items() if name != OWNER
    }
    assert {key: value for key, value in before.items() if key != "tools"} == {
        key: value for key, value in after.items() if key != "tools"
    }


@pytest.fixture
def actual_delegate(monkeypatch):
    special.compiled_special.__wrapped__(monkeypatch, _compact())


@pytest.mark.parametrize(
    "spell,extra",
    [
        ("fire_bolt", {}),
        ("cure_wounds", {}),
        ("resistance", {"selected_damage_type": "fire"}),
        ("scorching_ray", {"attack_targets": ["guard", "other", "guard"]}),
    ],
)
def test_actual_exploration_cast_preserves_all_context_snapshots_costs_and_outputs(
    monkeypatch, actual_delegate, spell, extra
):
    state, data, params = cases._spell_case(spell)
    state[f"{cases.SRD}.combat"].update(
        encounter_active=False,
        turn_active=False,
        active_actor=None,
        phase="exploration",
        action_available=False,
    )
    params.update(extra)
    direct, delegated = deepcopy(state), deepcopy(state)
    monkeypatch.setattr(dice, "_rng", Random(1))
    original = budget._run(CAST, direct, participant_data=data, **params)
    monkeypatch.setattr(dice, "_rng", Random(1))
    result = budget._run(OWNER, delegated, participant_data=data, **params)
    assert original.result["status"] == result.result["status"] == "committed"
    assert direct == delegated
    assert original.mutations == result.mutations
    before, after = original.result["context"], result.result["context"]
    assert {key: value for key, value in before.items() if not key.startswith("_")} == {
        key: value for key, value in after.items() if not key.startswith("_")
    }
    assert {key: value for key, value in before.items() if key.startswith("_")} == {
        key: value for key, value in after.items() if key.startswith("_")
    }
    assert original.result["result_card"] == result.result["result_card"]
    assert original.result["narrative_hooks"] == result.result["narrative_hooks"]
    assert original.result["narrative_directives"] == result.result["narrative_directives"]


def test_actual_exploration_precondition_failure_preserves_state(actual_delegate):
    state, data, params = cases._spell_case("fire_bolt")
    params["target_distance"] = 1000
    before = deepcopy(state)
    for name in (CAST, OWNER):
        with pytest.raises(RulesetPreconditionRejected):
            budget._run(name, state, participant_data=data, **params)
        assert state == before


@pytest.mark.parametrize("success", [True, False])
def test_actual_compacted_exploration64_retains_every_save_packet_and_final_card(
    monkeypatch, actual_delegate, success
):
    actual = _effect_executor._execute_effect
    budgets = []

    def measured(effect, context, tool_executor=None):
        if not budgets and context.budget is not None:
            budgets.append(context.budget)
        return actual(effect, context, tool_executor)

    monkeypatch.setattr(_effect_executor, "_execute_effect", measured)
    special.test_public_prismatic_complete64_scene_creatures_and_packets(monkeypatch, success)
    assert budgets[0].max_operations == 10000
    assert budgets[0].operations <= 10000
    print("compacted exploration64 operations", success, budgets[0].operations)
