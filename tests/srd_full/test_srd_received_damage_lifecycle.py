"""Actual mixed typed packets interrupt earned rest, including reflected TempHP damage."""

import hashlib
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_received_damage_lifecycle import OWNERS, bind_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_warding_bond_damage import bind_runtime_consumers

from sylvia_gm import dice
from sylvia_gm.runtime import _effect_executor
from sylvia_gm.runtime._expression_parser import BinaryOpNode, parse_expression
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests import test_srd_spell_special_damage as special
from tests.srd_full.test_srd_warding_bond_damage import _ring

PARENT = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-received-damage-lifecycle/ruleset.yaml")
)
PARENT_SHA = "20c50891a72da87bb2f0156b73512e6c2de227b264aa3567f448c0d080713756"


def _calls(value):
    if isinstance(value, dict):
        if "call" in value:
            assert isinstance(value["call"], str)
            yield value["call"]
        for item in value.values():
            yield from _calls(item)
    elif isinstance(value, list):
        for item in value:
            yield from _calls(item)


def _nodes(value):
    if isinstance(value, dict):
        return 1 + sum(_nodes(key) + _nodes(item) for key, item in value.items())
    if isinstance(value, list):
        return 1 + sum(map(_nodes, value))
    return 1


def _fragment(graph, roots):
    """Keep the exact generated bodies for these test roots and every literal dependency."""
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


@pytest.fixture(scope="module")
def received_graph():
    raw = PARENT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == PARENT_SHA
    text = raw.decode()
    original = _load_runtime(text)
    repaired = bind_runtime(text)
    assert bind_runtime(repaired) == repaired
    final = _load_runtime(repaired)
    assert all(
        tool == final["tools"][name]
        for name, tool in original["tools"].items()
        if name not in OWNERS
    )
    for name in OWNERS:
        before = original["tools"][name]
        after = final["tools"][name]
        assert {k: v for k, v in before.items() if k != "effects"} == {
            k: v for k, v in after.items() if k != "effects"
        }
        index = next(
            i
            for i, (a, b) in enumerate(zip(before["effects"], after["effects"], strict=True))
            if a != b
        )
        restored = deepcopy(after["effects"])
        group = restored[index]
        rest = group["on_true"].pop(0)
        old_condition = before["effects"][index]["condition"]
        expected = BinaryOpNode(
            operator="or",
            left=parse_expression(old_condition),
            right=parse_expression(rest["condition"]),
        )
        assert parse_expression(group["condition"]) == expected
        assert len(group["condition"]) <= 500
        group["condition"] = old_condition
        assert restored == before["effects"]
    repaired = bind_runtime_consumers(repaired)
    assert bind_runtime_consumers(repaired) == repaired
    return _load_runtime(repaired)


def _producer(graph, name):
    composite = name in OWNERS
    params = {
        "target": "guard",
        "source_state_id": "other",
        "game_damage_roll": True,
        "component_1_amount": "{_test_first}",
        "component_1_damage_type": "fire",
        "component_2_amount": "{_test_second}",
        "component_2_damage_type": "cold",
    }
    assert composite
    return {
        "description": "Test the actual typed receiving owner with a genuine two-die game roll.",
        "parameters": [],
        "pre_conditions": [],
        "effects": [
            {"dice": {"expression": "2d6"}, "store_as": "_test_raw"},
            {"set": {"field": "_test_first", "value": "_test_raw.results[0]"}},
            {"set": {"field": "_test_second", "value": "_test_raw.results[1]"}},
            {
                "call": name,
                "params": {
                    k: v
                    for k, v in params.items()
                    if k in {p["name"] for p in graph["tools"][name]["parameters"]}
                },
                "export_context": "*",
            },
        ],
        "metadata": {"category": "internal", "visible_to_player": False, "costs_action": False},
    }


@pytest.mark.parametrize("owner,bond", [(OWNERS[0], False), (OWNERS[1], False), (OWNERS[1], True)])
def test_actual_two_type_temp_damage_interrupts_rest_once(monkeypatch, received_graph, owner, bond):
    roots = [owner, "begin_srd_rest", "wear_srd_warding_bond_ring", "cast_srd_spell_ward"]
    graph = _fragment(received_graph, roots)
    graph["tools"]["test_srd_mixed_received_roll"] = _producer(graph, owner)
    encoded = yaml.safe_dump(graph, sort_keys=False).encode()
    special.compiled_special.__wrapped__(monkeypatch, (encoded, graph["definitions"]["spells"]))
    state, data, _ = cards._spell_case("warding_bond")
    state[f"{cards.SRD}.combat"].update(encounter_active=False, turn_active=False)
    state[f"{cards.SRD}.state"]["spatial_scene"] = special._scene(
        special._entity("hero", [0, 0, 0], participant_ref="hero", creature_type="humanoid"),
        special._entity("guard", [5, 0, 0], participant_ref="guard", creature_type="humanoid"),
        special._entity("other", [30, 0, 0], participant_ref="other", creature_type="humanoid"),
    )
    entities = state[f"{cards.SRD}.entities"]
    for entity in entities.values():
        entity.update(hp=1000, max_hp=1000, temp_hp=100, concentration_spell_id=None)
    if bond:
        for actor in ("hero", "guard"):
            _ring(state, actor)
        budget._run(
            "cast_srd_spell_ward",
            state,
            caster_state="hero",
            spell="warding_bond",
            slot_level=2,
            concentration_save_modifier=3,
            save_dc=18,
            target_state="guard",
            target_willing=True,
            caster_can_touch_target=True,
        )
        sleeper = "hero"
        state[f"{cards.SRD}.entities"][sleeper]["damage_resistances"] = ["cold"]
    else:
        sleeper = "guard"
    budget._run("begin_srd_rest", state, target_state=sleeper, kind="long", activity="sleep")
    assert "unconscious" in state[f"{cards.SRD}.entities"][sleeper]["conditions"]
    monkeypatch.setattr(
        dice, "_rng", type("ActualFives", (), {"randint": lambda _self, low, high: 5})()
    )
    calls = []
    actual = _effect_executor._EFFECT_HANDLERS["call"]

    def observe(body, effect, context, executor):
        if body == "interrupt_srd_rest":
            calls.append(context.current_tool)
        return actual(body, effect, context, executor)

    monkeypatch.setitem(_effect_executor._EFFECT_HANDLERS, "call", observe)
    result = budget._run("test_srd_mixed_received_roll", state, participant_data=data).result
    assert result["status"] == "committed"
    actor = state[f"{cards.SRD}.entities"][sleeper]
    assert actor["hp"] == 1000
    assert actor["temp_hp"] == (97 if bond else 90)
    assert "unconscious" not in actor["conditions"]
    assert actor["rest_phase"] != "active"
    assert len(calls) == 1
