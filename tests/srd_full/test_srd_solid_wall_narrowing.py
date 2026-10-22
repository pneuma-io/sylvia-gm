"""Source-selected Gaseous movement narrows around real solids, never through them."""

import json
import os
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_solid_wall_narrowing import bind_runtime_consumers
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_stone_spell_wall as stone

SRD = stone.SRD
ROOT = stone.ROOT
NATIVE = Path(
    os.environ.get(
        "FC14_SOLID_NARROWING_NATIVE",
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/fc14-solid-narrowing-native/ruleset.yaml"),
    )
)
STONE = Path(
    os.environ.get("FC14_STONE_WALL_OWNER_RUNTIME", ROOT / ".scratch/stone-wall-proof-runtime.yaml")
)


@lru_cache
def _runtime():
    native = _load_runtime(NATIVE.read_text())
    runtime = _load_runtime(STONE.read_text())
    pending = [
        "cast_spell_transformation",
        "install_srd_transformation_source",
        "end_srd_transformation_source",
        "check_srd_form_movement_permissions",
    ]
    while pending:
        name = pending.pop()
        if name in runtime["tools"]:
            continue
        tool = runtime["tools"][name] = deepcopy(native["tools"][name])
        stack = [tool]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if "call" in node:
                    pending.append(node["call"])
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)

    class NoAliases(getattr(yaml, "CSafeDumper", yaml.SafeDumper)):
        def ignore_aliases(self, data):
            return True

    text = bind_runtime_consumers(yaml.dump(runtime, Dumper=NoAliases, sort_keys=False))
    assert bind_runtime_consumers(text) == text
    runtime = _load_runtime(text)
    for tool in runtime["tools"].values():
        stack = [tool]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                for kind in ("spatial_query", "spatial_mutation"):
                    if kind in node:
                        node[kind]["scene"] = re.sub(
                            r"^lasting_state\.", "state.", node[kind]["scene"]
                        )
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    assert not (errors := validate_ruleset_data(runtime)), errors
    raw = yaml.dump(runtime, Dumper=NoAliases, sort_keys=False).encode()
    return raw, runtime


@lru_cache
def _mechanic(name):
    raw, runtime = _runtime()
    return stone.force.materialize_package_mechanic(
        package_id=SRD,
        package_version="5.2.1",
        package_sha256="c" * 64,
        runtime_bytes=raw,
        declaration=stone.force.ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=stone.force.owned_payload(
                stone.force._tool_schema(runtime["tools"][name], tool_name=name)
            ),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(stone.force.objects, "_mechanic", _mechanic)


def _case(spell):
    state = stone._state()
    leaf = "cast_srd_stone_spell_wall" if spell == "wall_of_stone" else "cast_srd_solid_spell_wall"
    stone.force.objects._run(
        leaf,
        state,
        caster_state="hero",
        spell=spell,
        slot_level=5,
        concentration_save_modifier=0,
        origin=[0, 0, 5],
    )
    guard_body = stone.force.wall._position(state, "guard")
    guard_body["position"] = [5, -1, 2]
    guard_body["footprint"]["half_extents"] = [2, 2]
    stone.force.wall._position(state, "other")["position"] = [5, -2, 2]
    return state


def _move(state, destination):
    return stone.force.objects._run(
        "move_srd_scene_creature_environment_segment",
        state,
        actor_state="guard",
        destination=destination,
        mode="fly",
    )


def _install(state, spell):
    stone.force.objects._run(
        "cast_spell_transformation",
        state,
        caster_state="other",
        lasting_state="state",
        spell=spell,
        actual_spell_level=3 if spell == "gaseous_form" else 6,
        save_dc=10,
        casting_modifier=0,
        casting_ability="wisdom",
        target_state="guard",
        targets=["other", "guard"] if spell == "wind_walk" else [],
        target_willing=True,
        all_targets_willing=True,
        caster_can_touch_target=True,
        caster_can_see_all_targets=True,
        concentration_save_modifier=0,
    )
    source_id = state[f"{SRD}.entities"]["guard"]["form_current_source_id"]
    assert source_id
    assert state[f"{SRD}.state"]["transformation_sources"][source_id]["installed"] is True
    return source_id


@pytest.mark.parametrize("wall_spell", ["wall_of_force", "wall_of_stone"])
def test_actual_gaseous_narrows_beside_solid_panel_but_cannot_cross_it(wall_spell):
    state = _case(wall_spell)
    _install(state, "gaseous_form")
    state = json.loads(json.dumps(state))
    result = _move(state, [10, -1, 2])
    assert result["_scene_movement_cost"] == 5
    assert stone.force.wall._position(state, "guard")["position"] == [10, -1, 2]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="even actual Gaseous"):
        _move(state, [10, 1, 2])
    assert state == before


def test_wind_walk_cloud_does_not_inherit_gaseous_narrowing():
    state = _case("wall_of_force")
    _install(state, "wind_walk")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Force panel"):
        _move(state, [10, -1, 2])
    assert state == before


def test_retired_gaseous_source_restores_full_body_and_false_cloud_flag_is_not_authority():
    state = _case("wall_of_force")
    source_id = _install(state, "gaseous_form")
    stone.force.objects._run(
        "end_srd_transformation_source",
        state,
        target_state="guard",
        source_id=source_id,
    )
    guard = state[f"{SRD}.entities"]["guard"]
    guard["form_cloud"] = True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Force panel"):
        stone.force.objects._run(
            "check_srd_solid_wall_movement",
            state,
            subject="guard",
            destination=[10, -1, 2],
            plane="material",
        )
    assert state == before
