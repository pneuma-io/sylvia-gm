"""Exact generated Dream/Bond owners drain a reflected sleeper's earned nightmare once."""

import importlib.util
import os
import sys
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_dream_spell import bind_runtime_consumers
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime, _RuntimeDumper

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime import _effect_executor
from tests import test_srd_action_budget as budget
from tests import test_srd_dream_spell as dream
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run

ROOTS = {
    "cast_srd_dream_spell",
    "cast_srd_spell_ward",
    "wear_srd_warding_bond_ring",
    "begin_srd_rest",
    "apply_srd_qualifying_damage",
    "apply_srd_composite_damage",
    "drain_srd_dream_wakes",
}


def _calls(value):
    if isinstance(value, dict):
        if isinstance(value.get("call"), str):
            yield value["call"]
        for child in value.values():
            yield from _calls(child)
    elif isinstance(value, list):
        for child in value:
            yield from _calls(child)


def _immutable_module(source, expected, name):
    source = Path(source)
    assert sha256(source.read_bytes()).hexdigest() == expected
    spec = importlib.util.spec_from_file_location(name, source)
    module = importlib.util.module_from_spec(spec)
    original = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = original
    assert sha256(source.read_bytes()).hexdigest() == expected
    return module


@lru_cache
def _runtime():
    """Scope unchanged transitive owners, retaining both production parser/execution limits."""
    default = Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
    source = Path(os.environ.get("FC14_DREAM_BOND_RUNTIME", default))
    content = source.read_bytes()
    expected = os.environ.get("FC14_DREAM_BOND_RUNTIME_SHA256")
    if expected:
        assert sha256(content).hexdigest() == expected
    text = content.decode()
    rest_path = os.environ.get("FC14_DREAM_BOND_REST_SOURCE")
    if rest_path:
        lifecycle = _immutable_module(
            rest_path,
            os.environ["FC14_DREAM_BOND_REST_SHA256"],
            "scripts._immutable_dream_receiving_rest",
        )
        text = lifecycle.bind_runtime(text)
    if os.environ.get("FC14_DREAM_BOND_BIND_SUCCESSOR") == "1":
        text = bind_runtime_consumers(text)
    receiver_path = os.environ.get("FC14_DREAM_BOND_RECEIVER_SOURCE")
    if receiver_path:
        module = _immutable_module(
            receiver_path,
            os.environ["FC14_DREAM_BOND_RECEIVER_SHA256"],
            "scripts._immutable_dream_receiver",
        )
        text = module.bind_runtime_consumers(text)
    actual = _load_runtime(text)
    pending = list(ROOTS)
    selected = set()
    while pending:
        name = pending.pop()
        if name not in selected:
            assert name in actual["tools"], name
            selected.add(name)
            pending.extend(_calls(actual["tools"][name]))
    runtime = deepcopy(actual)
    runtime["tools"] = {name: tool for name, tool in actual["tools"].items() if name in selected}
    assert all(runtime["tools"][name] == actual["tools"][name] for name in selected)
    if expected:
        assert sha256(source.read_bytes()).hexdigest() == expected
    return runtime, yaml.dump(runtime, Dumper=_RuntimeDumper, sort_keys=False).encode()


@lru_cache
def _mechanic(name):
    runtime, content = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=content,
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
def _actual_graph(monkeypatch):
    runtime, _ = _runtime()
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])


def _ring(state, actor):
    entity = state[f"{SRD}.entities"][actor]
    name = f"{actor}'s platinum ring"
    entity.setdefault("inventory", []).append(name)
    entity.setdefault("spell_materials", []).append(
        {
            "id": f"{actor}_ring",
            "inventory_item": name,
            "kind": "specific",
            "components": ["warding_bond.1"],
            "quantity": 1,
            "value_cp": 5000,
        }
    )
    _run("wear_srd_warding_bond_ring", state, actor_state=actor, material_id=f"{actor}_ring")


@pytest.mark.parametrize(
    "packet", ["scalar_reflection", "composite_reflection", "composite_direct", "immune_reflection"]
)
def test_actual_completed_packet_wakes_sleeper_and_drains_earned_dream_once(monkeypatch, packet):
    state, _, _ = dream._case()
    entities = state[f"{SRD}.entities"]
    for actor in ("guard", "other"):
        entities[actor].update(hp=1000, max_hp=1000, temp_hp=0)
    if packet == "immune_reflection":
        entities["guard"]["damage_immunities"] = ["fire"]
    for actor in ("guard", "other"):
        _ring(state, actor)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    guard = next(row for row in scene["entities"] if row["id"] == "guard")
    guard["position"] = [3, 0, 0]
    other = deepcopy(guard)
    other.update(id="other", position=[6, 0, 0])
    other["metadata"]["participant_ref"] = "other"
    scene["entities"].append(other)
    _run(
        "cast_srd_spell_ward",
        state,
        caster_state="guard",
        target_state="other",
        spell="warding_bond",
        slot_level=2,
        save_dc=18,
        concentration_save_modifier=3,
        target_willing=True,
        caster_can_touch_target=True,
    )
    entities = state[f"{SRD}.entities"]
    entities["guard"]["saving_throw_modifiers"]["wisdom"] = -30
    _run("begin_srd_rest", state, target_state="guard", kind="long", activity="sleep")

    class ActualFaces:
        def randint(self, low, high):
            assert low == 1
            return 2 if high == 6 else 1

    monkeypatch.setattr(dice, "_rng", ActualFaces())
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    dream_id = dream._cast(state, dream_terrifying=True, dream_message_words=["Beware"])
    record = state[f"{SRD}.state"]["dream_spell_sources"][dream_id]
    assert record["save_success"] is False and record["wake_damage_pending"] is True
    calls = []
    original = _effect_executor._EFFECT_HANDLERS["call"]

    def observe(body, effect, context, executor):
        if body in {"apply_srd_reflected_warding_bond_damage", "apply_srd_dream_wake_damage"}:
            calls.append((body, context.current_tool))
        return original(body, effect, context, executor)

    monkeypatch.setitem(_effect_executor._EFFECT_HANDLERS, "call", observe)
    if packet in {"scalar_reflection", "immune_reflection"}:
        _run(
            "apply_srd_qualifying_damage",
            state,
            target="other",
            amount=2,
            adjustment=0,
            damage_type="fire",
            source_state_id="other",
        )
    else:
        _run(
            "apply_srd_composite_damage",
            state,
            target="other" if packet == "composite_reflection" else "guard",
            component_1_amount=2,
            component_1_damage_type="fire",
            component_2_amount=0,
            component_2_damage_type="cold",
            source_state_id="other",
        )
    record = state[f"{SRD}.state"]["dream_spell_sources"][dream_id]
    if packet == "immune_reflection":
        assert state[f"{SRD}.entities"]["other"]["hp"] == 999
        assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
        assert record["woke"] is False and record["wake_damage_pending"] is True
        assert record.get("wake_damage_roll") is None
        assert [name for name, _ in calls] == ["apply_srd_reflected_warding_bond_damage"]
        before = deepcopy(state)
        _run("drain_srd_dream_wakes", state)
        assert state == before
        return
    assert state[f"{SRD}.entities"]["other"]["hp"] == (
        1000 if packet == "composite_direct" else 999
    )
    assert state[f"{SRD}.entities"]["guard"]["hp"] == (
        992 if packet == "composite_direct" else 993
    ), {
        "record": record,
        "calls": calls,
        "guard": state[f"{SRD}.entities"]["guard"],
        "queue": state[f"{SRD}.state"].get("dream_wake_target_ids"),
    }
    assert record["woke"] is True and record["wake_damage_settled"] is True
    assert record["wake_damage_roll"]["total"] == record["wake_hp_temp_loss"] == 6
    expected_calls = ["apply_srd_dream_wake_damage"]
    if packet != "composite_direct":
        expected_calls.insert(0, "apply_srd_reflected_warding_bond_damage")
    assert [name for name, _ in calls] == expected_calls
    assert state[f"{SRD}.state"]["dream_wake_target_ids"] == []
    before = deepcopy(state)
    _run("drain_srd_dream_wakes", state)
    assert state == before
