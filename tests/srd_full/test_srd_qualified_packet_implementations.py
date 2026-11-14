"""Final packet reuse retains exact source mechanics; bounded fragments preserve real caps."""

import ast
import hashlib
from collections import Counter
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_divination_spells import _normalize
from scripts.srd_full.srd_prismatic_damage_packets import bind_runtime as bind_prism
from scripts.srd_full.srd_qualified_packet_implementations import (
    COHORTS,
    RENDER,
    _forward,
    bind_runtime,
    restore_runtime,
)
from scripts.srd_full.srd_received_damage_lifecycle import bind_runtime as bind_received
from scripts.srd_full.srd_timed_actor_defenses import _call, _load_runtime, _require, _set, _when
from scripts.srd_full.srd_timed_senses import _optional, _replace_tools
from scripts.srd_full.srd_warding_bond_damage import bind_runtime_consumers as bind_bond

from sylvia_gm import dice
from sylvia_gm.runtime import _effect_executor
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests import test_srd_spell_special_damage as special
from tests.srd_full.test_srd_received_damage_lifecycle import (
    test_actual_two_type_temp_damage_interrupts_rest_once as actual_received_packet,
)

PARENT = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-qualified-packet-implementations/ruleset.yaml")
)
PARENT_SHA = "20c50891a72da87bb2f0156b73512e6c2de227b264aa3567f448c0d080713756"


def _dream_successor(text):
    """Borrow only the immutable final Dream binder, preserving every generated source leaf."""
    path = Path(
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-qualified-packet-implementations/srd_dream_spell.py")
    )
    raw = path.read_bytes()
    assert (
        hashlib.sha256(raw).hexdigest()
        == "4e25ff4ad3a15c3db1575e286d3a9f74a37938ea744b839e1cd55b40545b24af"
    )
    node = next(
        n
        for n in ast.parse(raw.decode()).body
        if isinstance(n, ast.FunctionDef) and n.name == "bind_runtime_consumers"
    )
    namespace = {
        "deepcopy": deepcopy,
        "_load_runtime": _load_runtime,
        "_set": _set,
        "_when": _when,
        "_call": _call,
        "_require": _require,
        "_optional": _optional,
        "_normalize": _normalize,
        "_replace_tools": _replace_tools,
    }
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(path), "exec"), namespace)
    return namespace["bind_runtime_consumers"](text)


@lru_cache
def _graphs():
    raw = PARENT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PARENT_SHA
    parent = bind_prism(bind_bond(_dream_successor(bind_received(raw.decode()))))
    original = yaml.load(parent, Loader=yaml.CSafeLoader)
    text = bind_runtime(parent)
    assert bind_runtime(text) == text
    factored = yaml.load(text, Loader=yaml.CSafeLoader)
    restored = restore_runtime(text)
    assert _load_runtime(restored) == original
    assert restore_runtime(restored) == restored
    assert bind_runtime(restored) == text
    assert not validate_ruleset_data(factored)
    return original, factored


def _nodes(value):
    if isinstance(value, dict):
        return 1 + sum(_nodes(key) + _nodes(item) for key, item in value.items())
    if isinstance(value, list):
        return 1 + sum(map(_nodes, value))
    return 1


def _expanded(core, render):
    result = []
    for effect in core["effects"]:
        if effect.get("condition") == RENDER + " == true":
            if render:
                result.extend(deepcopy(effect["on_true"]))
        else:
            result.append(deepcopy(effect))
    return result


def test_final_graph_retains_all_entry_schemas_and_ordered_source_effects():
    original, factored = _graphs()
    changed = set()
    for core, owner, prism, dream in COHORTS:
        changed.add(core)
        for name, render in ((owner, True), (prism, False), (dream, True)):
            if name is None:
                continue
            changed.add(name)
            before, after = original["tools"][name], factored["tools"][name]
            assert {k: v for k, v in before.items() if k != "effects"} == {
                k: v for k, v in after.items() if k != "effects"
            }
            assert (
                before["effects"]
                == _expanded(factored["tools"][core], render) + after["effects"][1:]
            )
            assert after["effects"][0]["export_context"] == "*"
        assert "observe_srd_dream_sleep" in str(factored["tools"][COHORTS[0][0]])
        assert "drain_srd_dream_wakes" not in str(factored["tools"][COHORTS[0][0]])
    assert all(
        factored["tools"][name] == tool
        for name, tool in original["tools"].items()
        if name not in changed
    )
    assert _nodes(original) - _nodes(factored) == 3922
    print("final received/Dream/Bond packet nodes", _nodes(original), _nodes(factored))


def _calls(value):
    if isinstance(value, dict):
        if "call" in value:
            assert isinstance(value["call"], str), "This fragment needs literal owned calls."
            yield value["call"]
        for item in value.values():
            yield from _calls(item)
    elif isinstance(value, list):
        for item in value:
            yield from _calls(item)


def _fragment(graph, roots):
    """Select only test roots and their genuine dependencies; never alter an emitted owner body."""
    result = deepcopy(graph)
    selected, pending = set(), list(roots)
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        assert name in graph["tools"]
        selected.add(name)
        pending.extend(_calls(graph["tools"][name]))
    result["tools"] = {name: deepcopy(graph["tools"][name]) for name in selected}
    assert _nodes(result) < 500000
    assert not validate_ruleset_data(result)
    return result


def _prismatic_parameters(graph, data):
    profile = graph["definitions"]["spells"]["prismatic_spray"]
    ability = "intelligence"
    modifier = (data["abilities"][ability] - 10) // 2
    proficiency = 2 + (data["level"] - 1) // 4
    params = {
        **profile,
        "spell": "prismatic_spray",
        "caster_state": "hero",
        "casting_ability": ability,
        "casting_modifier": modifier,
        "casting_attack_bonus": proficiency + modifier,
        "casting_save_dc": 8 + proficiency + modifier,
        "casting_scaling_level": data["level"],
        "casting_concentration_save_modifier": 0,
        "primary_target_id": "",
        "slot_resource": "spell_slot_7",
        "slot_level": 7,
        "failed_save_condition": "none",
        "failed_save_condition_rounds": 0,
        "failed_save_condition_expiry": "rounds_repeat_save",
        "failed_save_delayed_damage_dice": 0,
        "origin_distance": 0,
        "caster_can_see_target": True,
    }
    names = {p["name"] for p in graph["tools"]["cast_srd_prismatic_spray"]["parameters"]}
    return {name: value for name, value in params.items() if name in names}


@pytest.mark.parametrize("bond", [False, True])
def test_factored_real_two_type_temp_packet_preserves_paid_bond_and_received_rest(
    monkeypatch, bond
):
    _, graph = _graphs()
    actual_received_packet(monkeypatch, graph, "apply_srd_qualifying_composite_damage", bond)


@pytest.mark.parametrize("success", [True, False])
def test_current_private64_fragment_preserves_actual_saves_packets_and_rows(monkeypatch, success):
    _, graph = _graphs()
    fragment = _fragment(graph, ["cast_srd_prismatic_spray"])
    area = fragment["tools"]["cast_srd_prismatic_spray"]
    # The full public cast owns these two presentation initializers before its genuine area Call.
    entry = deepcopy(area)
    entry["effects"] = [
        {"set": {"field": "_spell_detail_rows", "value": []}},
        {"set": {"field": "_spell_card_summary", "value": []}},
        {
            "call": "cast_srd_prismatic_spray",
            "params": {p["name"]: _forward(p) for p in area["parameters"]},
            "export_context": "*",
        },
    ]
    fragment["tools"]["test_srd_private_prism_entry"] = entry
    encoded = yaml.safe_dump(fragment, sort_keys=False).encode()
    special.compiled_special.__wrapped__(monkeypatch, (encoded, graph["definitions"]["spells"]))
    state, data, _ = cards._spell_case("prismatic_spray")
    entities = state[f"{cards.SRD}.entities"]
    identities = [f"packet-target-{index}" for index in range(64)]
    for identity in identities:
        entities[identity] = deepcopy(entities["guard"])
        entities[identity].update(name=identity, hp=1000, max_hp=1000, temp_hp=0)
        entities[identity]["saving_throw_modifiers"]["dexterity"] = 100 if success else -100
    scene = state[f"{cards.SRD}.state"]["spatial_scene"]
    scene["entities"] = scene["entities"][:1] + [
        special._entity(identity, [20, 0, 0], participant_ref=identity, creature_type="humanoid")
        for identity in identities
    ]
    monkeypatch.setattr(dice, "_rng", special.ActualDiceSequence(d8=[1] * 64))
    counts, budgets = Counter(), []
    actual = _effect_executor._execute_effect

    def measured(effect, context, tool_executor=None):
        counts[context.current_tool] += 1
        if not budgets and context.budget is not None:
            budgets.append(context.budget)
        return actual(effect, context, tool_executor)

    monkeypatch.setattr(_effect_executor, "_execute_effect", measured)
    result = budget._run(
        "test_srd_private_prism_entry",
        state,
        participant_data=data,
        **_prismatic_parameters(graph, data),
    ).result
    assert result["status"] == "committed"
    assert result["context"]["_spell_area_target_count"] == 64
    assert len(result["context"]["_spell_detail_rows"]) == 64
    assert all(
        state[f"{cards.SRD}.entities"][identity]["hp"] == (994 if success else 988)
        for identity in identities
    )
    assert counts["d20_test"] == 67 * 64
    assert budgets[0].max_operations == 10000
    assert budgets[0].operations <= 10000
    print("Actual282 private64 fragment operations", success, budgets[0].operations)
