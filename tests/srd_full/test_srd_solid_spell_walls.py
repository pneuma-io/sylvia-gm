"""Real Force panels block later body movement and retire through actual Concentration."""

import importlib.util
import json
import os
import re
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.build_srd_5_2_1_spells import _casting_requirements
from scripts.srd_full.srd_solid_spell_walls import (
    FAMILY,
    POOL,
    PROFILES,
    SOURCE_SHA256,
    bind_runtime_consumers,
    extend_runtime,
)
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests.srd_full import test_srd_action_budget as actions
from tests.srd_full import test_srd_prismatic_wall as wall
from tests.srd_full import test_srd_spell_object_content as objects
from tests.srd_full import test_srd_spell_result_cards as cards

ROOT = Path(__file__).resolve().parents[2]
SRD = objects.SRD
BASE = Path(
    os.environ.get("FC14_SOLID_WALL_RUNTIME", ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml")
)
SOURCE_PARENT = Path(
    os.environ.get("FC14_SOLID_WALL_SOURCE_PARENT", ROOT / "scripts/srd_full/srd_spell_source_instances.py")
)


def _header():
    corpus = json.loads((ROOT / "rulesets/dnd-srd-5.2.1/content/corpus/spells.json").read_text())
    page = next(p for p in corpus["page_records"] if p["page"] == 172)
    assert page["text_sha256"] == SOURCE_SHA256
    lines = [(172, line) for line in page["text"].splitlines()]
    start = next(i for i, (_, line) in enumerate(lines) if line.strip() == "Wall of Force")
    return {
        "name": lines[start][1].strip(),
        **_casting_requirements(lines, start + 1, "wall_of_force"),
    }


def _source_parent():
    spec = importlib.util.spec_from_file_location(
        "scripts._solid_wall_source_parent", SOURCE_PARENT
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.SUPPORTED_FAMILIES.add(FAMILY)
    module.INSTALLERS.add("cast_srd_solid_spell_wall")
    return module


@lru_cache
def _runtime():
    runtime = _load_runtime(BASE.read_text())
    runtime["definitions"]["spells"].update(PROFILES)
    runtime["definitions"]["spell_casting"]["wall_of_force"] = _header()
    for tool in runtime["tools"].values():
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "enum_values" in parameter:
                parameter["enum_values"] = sorted(set(parameter["enum_values"]) | set(PROFILES))

    class NoAliases(getattr(yaml, "CSafeDumper", yaml.SafeDumper)):
        def ignore_aliases(self, data):
            return True

    text = yaml.dump(runtime, Dumper=NoAliases, sort_keys=False)
    text = extend_runtime(text)
    assert extend_runtime(text) == text
    text = _source_parent().extend_runtime(text)
    text = bind_runtime_consumers(text)
    assert bind_runtime_consumers(text) == text
    runtime = _load_runtime(text)
    runtime["tools"]["cast_spell_outside_combat"] = deepcopy(runtime["tools"]["cast_spell"])
    selected, pending = (
        {},
        [
            "cast_spell",
            "cast_spell_outside_combat",
            "cast_srd_solid_spell_wall",
            "qualify_srd_solid_spell_wall_source",
            "end_srd_solid_spell_wall_source",
            "advance_srd_lasting_time",
            "move_srd_scene_creature_environment_segment",
            "move_srd_travel_subject",
            "prepare_srd_spell_object",
            "dismiss_srd_spell",
        ],
    )
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected[name] = runtime["tools"][name]
        stack = [selected[name]]
        while stack:
            node = stack.pop()
            if isinstance(node, dict):
                if "call" in node:
                    pending.append(node["call"])
                for kind in ("spatial_query", "spatial_mutation"):
                    if kind in node:
                        node[kind]["scene"] = re.sub(
                            r"^lasting_state\.", "state.", node[kind]["scene"]
                        )
                stack.extend(node.values())
            elif isinstance(node, list):
                stack.extend(node)
    runtime["tools"] = selected
    assert not (errors := validate_ruleset_data(runtime)), errors
    raw = yaml.dump(runtime, Dumper=NoAliases, sort_keys=False).encode()
    (ROOT / ".scratch/solid-wall-proof-runtime.yaml").write_bytes(raw)
    return raw, runtime


@lru_cache
def _mechanic(name):
    raw, runtime = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="5.2.1",
        package_sha256="c" * 64,
        runtime_bytes=raw,
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
def _owners(monkeypatch):
    monkeypatch.setattr(objects, "_mechanic", _mechanic)
    monkeypatch.setattr(actions, "_mechanic", _mechanic)


def _case():
    state = objects._state()
    for actor, position in (
        ("hero", [-20, -20, 0]),
        ("guard", [10, -5, 0]),
        ("other", [20, -5, 0]),
        ("door", [0, -30, 0]),
    ):
        wall._position(state, actor)["position"] = position
    objects._run(
        "cast_srd_solid_spell_wall",
        state,
        caster_state="hero",
        spell="wall_of_force",
        slot_level=5,
        concentration_save_modifier=0,
        origin=[0, 0, 5],
    )
    sid = state[f"{SRD}.entities"]["hero"]["solid_wall_source_id"]
    return state, sid


@pytest.mark.parametrize("plane", ["material", "ethereal"])
def test_actual_force_panels_block_budgeted_full_body_route_and_ending_restores_it(plane):
    state, sid = _case()
    wall._position(state, "guard")["metadata"]["plane"] = plane
    panels = [
        body
        for body in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if body["metadata"].get("solid_wall_source_id") == sid
    ]
    assert len(panels) == 10
    assert all(body["footprint"]["half_extents"] == [5, 1 / 96] for body in panels)
    assert all(body["metadata"]["invisible"] is True for body in panels)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Force panel"):
        objects._run(
            "move_srd_scene_creature_environment_segment",
            state,
            actor_state="guard",
            destination=[10, 5, 0],
        )
    assert state == before
    state = json.loads(json.dumps(state))
    objects._run("end_srd_solid_spell_wall_source", state, source_id=sid, reason="owning removal")
    result = objects._run(
        "move_srd_scene_creature_environment_segment",
        state,
        actor_state="guard",
        destination=[10, 5, 0],
    )
    assert result["_scene_movement_cost"] == 10
    assert wall._position(state, "guard")["position"] == [10, 5, 0]
    assert not state[f"{SRD}.state"][POOL][sid]["installed"]


def test_expiration_real_concentration_recast_and_damage_immunity_use_exact_sources():
    state, first = _case()
    panel_id = state[f"{SRD}.state"][POOL][first]["panel_ids"][0]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        objects._run(
            "prepare_srd_spell_object",
            state,
            object_id=panel_id,
            material="wood",
            size="large",
            durability="resilient",
        )
    assert state == before
    objects._run(
        "cast_srd_solid_spell_wall",
        state,
        caster_state="hero",
        spell="wall_of_force",
        slot_level=6,
        concentration_save_modifier=1,
        origin=[0, 10, 5],
    )
    second = state[f"{SRD}.entities"]["hero"]["solid_wall_source_id"]
    assert second != first and not state[f"{SRD}.state"][POOL][first]["installed"]
    objects._run("advance_srd_lasting_time", state, elapsed_rounds=100)
    assert not state[f"{SRD}.state"][POOL][second]["installed"]
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None


def test_unsupported_forming_push_and_out_of_range_reject_without_cost_or_scene_mutation():
    state = objects._state()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="push owner"):
        objects._run(
            "cast_srd_solid_spell_wall",
            state,
            caster_state="hero",
            spell="wall_of_force",
            slot_level=5,
            concentration_save_modifier=0,
            origin=[0, 0, 5],
        )
    assert state == before
    state = objects._state()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="beyond 120"):
        objects._run(
            "cast_srd_solid_spell_wall",
            state,
            caster_state="hero",
            spell="wall_of_force",
            slot_level=5,
            concentration_save_modifier=0,
            origin=[200, 0, 5],
        )
    assert state == before


def test_paid_named_force_cast_uses_actual_slot_material_source_and_later_saved_block(monkeypatch):
    scene_state = objects._state()
    monkeypatch.setattr(cards, "_casting_headers", lambda: {"wall_of_force": _header()})
    monkeypatch.setattr(cards, "_spell_definitions", lambda: PROFILES)
    state, data, parameters = cards._spell_case("wall_of_force")
    for actor, position in (
        ("hero", [-20, -20, 0]),
        ("guard", [10, -5, 0]),
        ("other", [20, -5, 0]),
        ("door", [0, -30, 0]),
    ):
        wall._position(scene_state, actor)["position"] = position
    state[f"{SRD}.state"]["spatial_scene"] = deepcopy(scene_state[f"{SRD}.state"]["spatial_scene"])
    result = actions._run(
        "cast_spell",
        state,
        participant_data=data,
        **(parameters | {"target": None, "solid_wall_origin": [0, 0, 5]}),
    )
    assert result.result["status"] == "committed"
    actor = state[f"{SRD}.entities"]["hero"]
    sid = actor["solid_wall_source_id"]
    source = state[f"{SRD}.state"][POOL][sid]
    assert actor["resources"]["spell_slot_5"] == 2
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert actor["spell_materials"][0]["quantity"] == 3
    assert source["spell_level"] == 5 and source["source_caster_id"] == "hero"
    assert source["cast_id"] == actor["last_spell_source_cast_id"]
    assert source["dispel_ongoing"] is False
    assert source["cast_id"] in str(result.result["result_card"])
    reopened = json.loads(json.dumps(state))
    reopened[f"{SRD}.combat"]["active_actor"] = "guard"
    before = deepcopy(reopened)
    with pytest.raises(RulesetPreconditionRejected, match="Force panel"):
        objects._run(
            "move_srd_scene_creature_environment_segment",
            reopened,
            actor_state="guard",
            destination=[10, 5, 0],
        )
    assert reopened == before
    before = deepcopy(reopened)
    with pytest.raises(RulesetPreconditionRejected):
        objects._run("dismiss_srd_spell", reopened, caster_state="guard", cast_id=source["cast_id"])
    assert reopened == before
    result = objects._run(
        "dismiss_srd_spell", reopened, caster_state="hero", cast_id=source["cast_id"]
    )
    assert result["_dismiss_count"] == 1
    assert reopened[f"{SRD}.state"][POOL][sid]["installed"] is False
    assert all(
        not body["active"]
        for body in reopened[f"{SRD}.state"]["spatial_scene"]["entities"]
        if body["metadata"].get("solid_wall_source_id") == sid
    )
