"""Genuine ward Cubes and installed fire exercise exact burn, damage and regrowth owners."""

import importlib.util
import json
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.build_srd_5_2_1_spells import (
    SPELL_DEFINITIONS_END,
    SPELL_DEFINITIONS_START,
    _replace_generated_block,
)
from scripts.srd_full.srd_guards_wards_obscuration import extend_runtime as extend_sight
from scripts.srd_full.srd_guards_wards_web_burning import bind_runtime_consumers
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_guards_wards as wards
from tests import test_srd_guards_wards_web as web
from tests import test_srd_spell_object_content as objects

SRD = objects.SRD
DEPENDENCY = (
    Path(__file__).resolve().parents[2]
    / ".scratch/guards-burning-parent-v1/wall-fire-dependency.py"
)


@lru_cache
def _runtime():
    text = web._runtime()[0].decode()
    runtime = _load_runtime(text)
    if "qualify_srd_wall_of_fire_source" not in runtime["tools"]:
        # The immutable foreign owner is test composition only, excluded from delivery.
        path = Path(os.environ.get("FC14_WALL_FIRE_SOURCE", DEPENDENCY))
        spec = importlib.util.spec_from_file_location("scripts._fc14_wall_fire_dependency", path)
        environment = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(environment)
        text = environment.extend_runtime(text)
        runtime = _load_runtime(text)
        runtime["definitions"]["spells"]["wall_of_fire"] = environment.PROFILES["wall_of_fire"]
    profiles = runtime["definitions"]["spells"]
    text = _replace_generated_block(
        text,
        SPELL_DEFINITIONS_START,
        SPELL_DEFINITIONS_END,
        [
            "    " + spell + ": " + json.dumps(profile)
            for spell, profile in sorted(profiles.items())
        ],
    )
    changes = {}
    for name, tool in runtime["tools"].items():
        for parameter in tool["parameters"]:
            if (
                parameter["name"] == "spell"
                and "enum_values" in parameter
                and "wall_of_fire" not in parameter["enum_values"]
            ):
                parameter["enum_values"] = sorted(set(parameter["enum_values"]) | {"wall_of_fire"})
                changes[name] = tool
    text = _replace_tools(text, changes)
    # Recompose the final ward consumers after the genuine Environment owner, as production does.
    text = web.bind_runtime_consumers(web.extend_runtime(text))
    text = bind_runtime_consumers(extend_sight(text))
    assert bind_runtime_consumers(text) == text
    runtime = _load_runtime(text)
    assert not (errors := validate_ruleset_data(runtime)), errors
    if destination := os.environ.get("FC14_BURN_OUTPUT_RUNTIME"):
        Path(destination).write_text(text)
    # Keep every statically reachable genuine owner, but omit unrelated public roots.
    # This is an owning test closure, not a claim of full-package archive admission.
    pending, selected = (
        [
            "record_srd_object_access",
            "cast_srd_guards_wards",
            "cast_spell_environment",
            "observe_srd_guards_wards_web_fire",
            "qualify_srd_guards_wards_web_target",
            "qualify_srd_guards_wards_web_source",
            "move_srd_travel_subject",
            "resolve_srd_environment_hazard_start_turn",
            "resolve_srd_guards_wards_web_burning_start",
            "resolve_srd_guards_wards_web_event",
            "apply_srd_condition",
            "end_concentration",
            "advance_srd_lasting_time",
            "end_srd_guards_wards_effect",
        ],
        {},
    )
    while pending:
        candidate = pending.pop()
        if candidate in selected:
            continue
        selected[candidate] = runtime["tools"][candidate]
        nodes = [selected[candidate]]
        while nodes:
            node = nodes.pop()
            if isinstance(node, dict):
                if "call" in node:
                    assert node["call"] in runtime["tools"], node["call"]
                    pending.append(node["call"])
                nodes.extend(node.values())
            elif isinstance(node, list):
                nodes.extend(node)

    class NoAliases(yaml.SafeDumper):
        def ignore_aliases(self, data):
            return True

    closure = {**runtime, "tools": selected}
    raw = yaml.dump(closure, Dumper=NoAliases, sort_keys=False).encode()
    return raw, closure


@lru_cache
def _mechanic(name):
    raw, runtime = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="5.2.1",
        package_sha256="f" * 64,
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


def _position(state, actor):
    return next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == actor)


def _case(*, cube=True, designated=()):
    state = objects._state()
    objects._access(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"].append(
        {
            "id": "floor",
            "map_id": scene["map"]["id"],
            "volume": {"kind": "box", "min": [-100, -100, -1], "max": [100, 100, 0]},
            "active": True,
            "movement": "blocking",
            "occupies_cells": False,
            "visibility": "opaque",
            "metadata": {"plane": "material", "ground": True},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )
    _position(state, "hero")["position"] = [0, 0, 0]
    for actor in ("guard", "other"):
        _position(state, actor)["position"] = [20, 20 if actor == "guard" else -20, 0]
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=0)
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["dexterity"] = -100
    sid = wards._cast_ward(
        state,
        save_dc=40,
        designated_creatures=list(designated),
        stairs=[{"min": [2, 0, 2], "max": [7 if cube else 12, 5, 7]}],
    )
    source = state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]
    return state, sid, source["web_section_ids"][0]


def _fire(state, *, origin=(4, 2.5, 0)):
    return objects._run(
        "cast_spell_environment",
        state,
        caster_state="hero",
        spell="wall_of_fire",
        slot_level=4,
        save_dc=18,
        concentration_save_modifier=0,
        origin=list(origin),
        wall_axis="north_south",
        fire_wall_length=5,
        fire_wall_side="positive",
    )


def _section(state, area):
    return state[f"{SRD}.state"]["spell_guarded_web_sections"][area]


def _inside(state, area):
    return objects._run(
        "qualify_srd_guards_wards_web_target",
        state,
        target_state="guard",
        area_id=area,
    )["_sphere_target_inside"]


def _move_inside(state):
    objects._run(
        "move_srd_travel_subject",
        state,
        subject="guard",
        destination=[4, 3, 0],
        plane="material",
        teleport=True,
    )


def test_real_wall_fire_install_starts_one_round_burn_and_saved_retry_keeps_source_time():
    state, sid, area = _case()
    _fire(state)
    burn = deepcopy(_section(state, area)["web_burn"])
    assert burn == {
        "fire_area_id": "hero",
        "fire_sequence": state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["sequence"],
        "parent_serial": 1,
        "started_tick": 0,
        "burn_end_tick": 2,
        "regrow_tick": 202,
        "status": "burning",
    }
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "wall_of_fire"
    reopened = json.loads(json.dumps(state))
    objects._run("observe_srd_guards_wards_web_fire", reopened)
    objects._run("observe_srd_guards_wards_web_fire", reopened)
    assert _section(reopened, area)["web_burn"] == burn
    assert reopened[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["installed"]


def test_actual_start_turn_rolls_one_typed_fire_packet_and_replay_does_not_repeat():
    state, _, area = _case()
    _fire(state)
    _move_inside(state)
    target = state[f"{SRD}.entities"]["guard"]
    target.update(temp_hp=3, damage_resistances=["fire"])
    state[f"{SRD}.combat"].update(turn_active=True, active_actor="guard", turn_sequence=7)
    before_hp = target["hp"]
    objects._run("resolve_srd_environment_hazard_start_turn", state, target_state="guard")
    target = state[f"{SRD}.entities"]["guard"]
    receipt = deepcopy(target["last_guards_wards_web_fire"])
    assert receipt["raw_damage"]["count"] == 2 and receipt["raw_damage"]["sides"] == 4
    assert receipt["applied_damage"] == receipt["raw_damage"]["total"] // 2
    assert target["hp"] == before_hp - max(0, receipt["applied_damage"] - 3)
    assert target["temp_hp"] == max(0, 3 - receipt["applied_damage"])
    reopened = json.loads(json.dumps(state))
    prior = deepcopy(reopened)
    objects._run("resolve_srd_guards_wards_web_burning_start", reopened, target_state="guard")
    assert reopened == prior
    assert _section(reopened, area)["web_burn"]["status"] == "burning"


def test_destroyed_cube_retires_only_original_restraint_then_regrows_without_old_restraint():
    state, sid, area = _case()
    _move_inside(state)
    objects._run("resolve_srd_guards_wards_web_event", state, target_state="guard", area_id=area)
    cid = state[f"{SRD}.entities"]["guard"]["last_guards_wards_web_save"]["condition_source_id"]
    objects._run("apply_srd_condition", state, target="guard", condition="restrained")
    _fire(state)
    # Burn continues its own round even when the actual fire source ends.
    objects._run("end_concentration", state, caster_state="hero")
    objects._run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _section(state, area)["web_destroyed"]
    assert not _inside(state, area)
    assert not state[f"{SRD}.state"]["timed_condition_sources"][cid]["installed"]
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]
    source_current = objects._run("qualify_srd_guards_wards_web_source", state, area_id=area)
    assert source_current["_sphere_source_current"]
    objects._run("advance_srd_lasting_time", state, elapsed_rounds=99)
    assert _section(state, area)["web_destroyed"]
    reopened = json.loads(json.dumps(state))
    objects._run("advance_srd_lasting_time", reopened, elapsed_rounds=1)
    assert _section(reopened, area)["web_burn"]["status"] == "regrown"
    assert _inside(reopened, area)
    assert not reopened[f"{SRD}.state"]["timed_condition_sources"][cid]["installed"]
    assert reopened[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["groups"]["stairs"]


def test_exempt_creature_tangency_other_plane_and_parent_end_preserve_source_rules():
    state, sid, area = _case(designated=("guard",))
    _fire(state)
    _move_inside(state)
    state[f"{SRD}.combat"].update(turn_active=True, active_actor="guard", turn_sequence=1)
    objects._run("resolve_srd_guards_wards_web_burning_start", state, target_state="guard")
    assert "last_guards_wards_web_fire" not in state[f"{SRD}.entities"]["guard"]
    objects._run("end_srd_guards_wards_effect", state, source_id=sid, group="stairs")
    state[f"{SRD}.combat"].update(turn_active=False, encounter_active=False)
    objects._run("advance_srd_lasting_time", state, elapsed_rounds=101)
    assert not _section(state, area)["active"]
    assert not _inside(state, area)
    state, _, area = _case()
    _fire(state, origin=(1.5, 2.5, 0))
    assert "web_burn" not in _section(state, area)
    state, _, area = _case()
    _position(state, "hero")["metadata"]["plane"] = "ethereal"
    state[f"{SRD}.state"]["spatial_scene"]["geometry"][0]["metadata"]["plane"] = "ethereal"
    _fire(state)
    assert "web_burn" not in _section(state, area)


def test_larger_sections_and_intersecting_solids_fail_honestly_before_fire_install_commits():
    for obstruction in ("large", "solid"):
        state, _, _ = _case(cube=obstruction != "large")
        if obstruction == "solid":
            solid = deepcopy(state[f"{SRD}.state"]["spatial_scene"]["geometry"][0])
            solid.update(id="wall", volume={"kind": "box", "min": [3, 1, 1], "max": [5, 4, 4]})
            state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(solid)
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected):
            _fire(state)
        assert state == before
