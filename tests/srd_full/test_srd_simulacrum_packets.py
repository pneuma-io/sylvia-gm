"""Native Simulacrum death remains at the real Jar/qualified receiving owner."""

import ast
import importlib
import json
import os
import sys
import types
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_simulacrum_consumers import (
    _lists,
    bind_death_consumers,
)

from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_simulacrum as sim

GUARD = "target.hp == 0 and target.created_spell_id == 'simulacrum'"
OLD_GUARD = "target.created_spell_id == 'simulacrum'"
MONSTER_ZERO = "target.hp == 0 and target.creature_kind == 'monster'"


def _dependency(name):
    """Use the selected frozen production source without substituting a compiled42 claim."""
    root = os.environ.get("FC14_SIMULACRUM_PACKET_SOURCE_ROOT")
    if not root:
        return importlib.import_module("scripts." + name)
    root = Path(root)
    manifest = json.loads((root / "manifest.json").read_text())
    import hashlib

    relative = "scripts/" + name + ".py"
    assert hashlib.sha256((root / relative).read_bytes()).hexdigest() == manifest["files"][relative]
    package = "fc14_simulacrum_packet_dependency"
    if package not in sys.modules:
        module = types.ModuleType(package)
        module.__path__ = [str(root / "scripts"), str(Path(sim.__file__).parents[1] / "scripts")]
        sys.modules[package] = module
    return importlib.import_module(package + "." + name)


def _actual_jar_damage_excerpt(tools):
    """Run only the exact frozen owner statements that restore and wrap scalar reception."""
    owner = _dependency("srd_magic_jar_consumers")
    source = Path(owner.__file__)
    function = next(
        node
        for node in ast.parse(source.read_text()).body
        if isinstance(node, ast.FunctionDef) and node.name == "bind_consumers"
    )
    start = next(
        i
        for i, node in enumerate(function.body)
        if isinstance(node, ast.Assign)
        and ast.unparse(node) == "damage = deepcopy(tools['apply_srd_damage'])"
    )
    stop = next(
        i
        for i, node in enumerate(function.body[start:], start)
        if isinstance(node, ast.Assign)
        and ast.unparse(node) == "changed['apply_srd_damage'] = damage"
    )
    namespace = vars(owner).copy() | {"tools": tools, "changed": {}}
    selected = ast.Module(body=function.body[start : stop + 1], type_ignores=[])
    exec(compile(selected, str(source), "exec"), namespace)
    return namespace["changed"]


def _native_packet_factory(runtime):
    """Preserve proven receiving bodies and factor with the actual production factories."""
    value = deepcopy(runtime)
    tools = value["tools"]
    # Re-emission starts without only this consumer's previous authored hooks.
    for tool in tools.values():
        for effects in _lists(tool["effects"]):
            effects[:] = [e for e in effects if e.get("condition") not in {GUARD, OLD_GUARD}]
    tools.update(_actual_jar_damage_excerpt(tools))
    packets = _dependency("srd_qualified_packet_implementations")
    for core, owner, prism, dream in packets.COHORTS:
        original = tools[owner]
        base, drain = packets._drain(original)
        fields = packets.PRESENTATION_FIELDS - ({"_hp_lost"} if "composite" in owner else set())
        # These are the same qualified/prismatic bodies issued by the production factory.
        prismatic = packets._without_presentation_snapshot(base, fields)
        tools[core] = packets._canonical(base, fields)
        tools[owner] = packets._delegate(original, core, True, drain)
        tools[prism] = packets._delegate(prismatic, core, False, drain)
        if dream:
            tools[dream] = packets._delegate(base, core, True, [])
        assert packets._expanded_core(tools[core], True) == base["effects"]
        assert packets._expanded_core(tools[core], False) == prismatic["effects"]
    return value


@pytest.fixture(scope="module")
def _packet_runtime(tmp_path_factory):
    root = Path(os.environ["FC14_SIMULACRUM_RUNTIME_ROOT"])
    original = yaml.load((root / "runtime/ruleset.yaml").read_text(), Loader=yaml.CSafeLoader)
    raw = _native_packet_factory(original)
    source = yaml.safe_dump(raw, sort_keys=False)
    bound = bind_death_consumers(source)
    runtime = yaml.load(bound, Loader=yaml.CSafeLoader)
    assert not validate_ruleset_data(runtime)

    # Restrict to actual reachable owners rather than importing unrelated fixture tools.
    def calls(value):
        if isinstance(value, dict):
            if "call" in value:
                yield value["call"]
            for child in value.values():
                yield from calls(child)
        elif isinstance(value, list):
            for child in value:
                yield from calls(child)

    missing = {name for name in calls(runtime["tools"]) if name not in runtime["tools"]}
    assert not missing
    selected = tmp_path_factory.mktemp("simulacrum-packet-runtime")
    (selected / "runtime").mkdir()
    (selected / "runtime/ruleset.yaml").write_text(bound)
    (selected / "package-record.json").write_bytes((root / "package-record.json").read_bytes())
    return raw, runtime, selected


@pytest.fixture(autouse=True)
def _compiled(monkeypatch, _packet_runtime):
    monkeypatch.setenv("FC14_SIMULACRUM_RUNTIME_ROOT", str(_packet_runtime[2]))
    yield from sim._compiled.__wrapped__(monkeypatch)


def _count(tool):
    return sum(
        effect.get("condition") == GUARD
        for effects in _lists(tool["effects"])
        for effect in effects
    )


def test_exact_facades_survive_and_each_true_native_owner_has_one_post_prevention_hook(
    _packet_runtime,
):
    raw, bound, _ = _packet_runtime
    for name in (
        "apply_srd_qualifying_damage",
        "apply_srd_qualifying_composite_damage",
        "apply_srd_prismatic_damage",
        "apply_srd_prismatic_composite_damage",
        "apply_srd_dream_wake_damage",
    ):
        assert bound["tools"][name] == raw["tools"][name]
        assert _count(bound["tools"][name]) == 0
        assert bound["tools"][name]["effects"][0]["export_context"] == "*"
    for name in (
        "apply_srd_damage",
        "apply_srd_composite_damage",
        "execute_srd_qualified_scalar_packet",
        "execute_srd_qualified_composite_packet",
    ):
        assert _count(raw["tools"][name]) == 0
        assert _count(bound["tools"][name]) == 1
        death = next(
            effect
            for effects in _lists(bound["tools"][name]["effects"])
            for effect in effects
            if effect.get("condition") == MONSTER_ZERO
        )
        assert death["on_true"][-1]["condition"] == GUARD
    before = raw["tools"]["apply_srd_damage"]["effects"][0]
    after = bound["tools"]["apply_srd_damage"]["effects"][0]
    assert before["on_true"] == after["on_true"]
    assert _count({"effects": after["on_false"]}) == 1
    text = yaml.safe_dump(bound, sort_keys=False)
    assert yaml.load(bind_death_consumers(text), Loader=yaml.CSafeLoader) == bound


@pytest.mark.parametrize("change", ["forward", "render", "export", "extra"])
def test_changed_facade_is_rejected_without_silently_expanding_or_discarding_it(
    _packet_runtime, change
):
    runtime = deepcopy(_packet_runtime[0])
    facade = runtime["tools"]["apply_srd_qualifying_damage"]
    if change == "forward":
        facade["effects"][0]["params"]["amount"] = 0
    elif change == "render":
        facade["effects"][0]["params"]["render_damage_snapshot"] = False
    elif change == "export":
        facade["effects"][0]["export_context"] = {}
    else:
        facade["effects"].append({"set": {"field": "_later_packet_change", "value": True}})
    before = deepcopy(runtime)
    with pytest.raises(ValueError, match="changed qualified packet facade"):
        bind_death_consumers(yaml.safe_dump(runtime, sort_keys=False))
    assert runtime == before


@pytest.mark.parametrize(
    "receiver",
    ["apply_srd_damage", "apply_srd_qualifying_damage", "apply_srd_qualifying_composite_damage"],
)
def test_actual_paid_copy_survives_death_ward_then_melts_at_true_receiving_zero_hp(receiver):
    state, data, params = sim._case()
    state[f"{sim.SRD}.state"]["spatial_scene"]["entities"].append(
        sim._entity("warden", [0, -3, 0], participant_ref="warden")
    )
    sim._run(
        "prepare_srd_creature",
        state,
        data,
        **(
            sim.awaken._native_params(creature_type="humanoid")
            | {
                "creature_id": "warden",
                "name": "Admitted native ward caster",
                "spellcasting_ability": "wisdom",
                "spellcasting_modifier": 0,
                "spell_attack_bonus": 2,
                "spell_save_dc": 10,
                "spells": [{"spell": "death_ward", "uses_per_day": 1}],
            }
        ),
    )
    sim._cast(state, data, params)
    original = deepcopy(sim._target(state, "beast"))
    ward_cast = {
        key: value
        for key, value in params.items()
        if key not in {"caster", "slot_resource", "simulacrum_pile", "simulacrum_friends"}
    }
    ward_cast.update(
        spell="death_ward",
        caster_state="warden",
        target="snow",
        target_distance=5,
        material_item_ids=[],
    )
    sim._run(
        "cast_spell_outside_combat",
        state,
        data,
        **ward_cast,
    )
    assert sim._target(state, "warden")["spell_uses_remaining"]["death_ward"] == 0
    damage = {"target": "snow", "source_state_id": "hero"}
    if "composite" in receiver:
        damage |= {
            "component_1_amount": 12,
            "component_1_damage_type": "force",
            "component_2_amount": 8,
            "component_2_damage_type": "fire",
        }
    else:
        damage |= {"amount": 20, "adjustment": 0, "damage_type": "force"}
    sim._run(receiver, state, data, **damage)
    assert sim._target(state)["hp"] == 1
    assert sim._target(state)["created_active"] is True
    assert not sim._target(state).get("simulacrum_melted", False)
    sim._run(receiver, state, data, **damage)
    assert sim._target(state)["hp"] == 0
    assert sim._target(state)["simulacrum_melted"] is True
    assert sim._target(state)["created_active"] is False
    assert sim._target(state, "beast") == original
    assert state[f"{sim.SRD}.state"]["created_actor_records"]["snow"]["active"] is False
