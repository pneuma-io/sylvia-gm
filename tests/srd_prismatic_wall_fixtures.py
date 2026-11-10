"""Genuine installed Wall bodies retain ordered saves and independent ray consequences."""

import json
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.build_srd_5_2_1_mechanics import _tool_schema
from scripts.build_srd_5_2_1_spells import (
    SPELL_DEFINITIONS_END,
    SPELL_DEFINITIONS_START,
    _replace_generated_block,
)
from scripts.srd_prismatic_wall import PROFILES, bind_runtime_consumers, extend_runtime
from scripts.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_spell_object_content as objects

SRD = objects.SRD
ROOT = Path(__file__).resolve().parents[1]


@lru_cache
def _runtime():
    base = Path(
        os.environ.get(
            "FC14_PRISMATIC_WALL_RUNTIME", ROOT / "rulesets/srd-lite/runtime/ruleset.yaml"
        )
    )
    text = extend_runtime(base.read_text())
    assert extend_runtime(text) == text
    text = bind_runtime_consumers(text)
    assert bind_runtime_consumers(text) == text
    runtime = _load_runtime(text)
    definitions = runtime["definitions"]["spells"] | PROFILES
    text = _replace_generated_block(
        text,
        SPELL_DEFINITIONS_START,
        SPELL_DEFINITIONS_END,
        ["    " + key + ": " + json.dumps(row) for key, row in sorted(definitions.items())],
    )
    runtime = _load_runtime(text)
    for tool in runtime["tools"].values():
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "enum_values" in parameter:
                parameter["enum_values"] = sorted(set(parameter["enum_values"]) | set(PROFILES))
    assert not (errors := validate_ruleset_data(runtime)), errors
    pending, selected = (
        [
            "record_srd_object_access",
            "cast_srd_prismatic_wall",
            "qualify_srd_prismatic_wall_source",
            "end_srd_prismatic_wall_source",
            "retire_srd_prismatic_wall_layer",
            "refresh_srd_prismatic_walls",
            "resolve_srd_prismatic_wall_contact",
            "reach_into_srd_prismatic_wall",
            "resolve_srd_prismatic_wall_near",
            "resolve_srd_prismatic_indigo_end_turn",
            "resolve_srd_prismatic_violet_start_turn",
            "resolve_srd_prismatic_wall_segment",
            "check_srd_prismatic_wall_light",
            "check_srd_prismatic_wall_spell_segment",
            "check_srd_prismatic_wall_observation",
            "check_srd_prismatic_wall_attack_segment",
            "resolve_srd_prismatic_wall_body_segment",
            "move_srd_travel_subject",
            "settle_srd_prismatic_wall_movements",
            "move_srd_scene_creature_environment_segment",
            "begin_combat_turn",
            "advance_srd_lasting_time",
        ],
        {},
    )
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected[name] = runtime["tools"][name]
        stack = [selected[name]]
        while stack:
            value = stack.pop()
            if isinstance(value, dict):
                if "call" in value:
                    pending.append(value["call"])
                stack.extend(value.values())
            elif isinstance(value, list):
                stack.extend(value)

    class NoAliases(yaml.SafeDumper):
        def ignore_aliases(self, data):
            return True

    closure = runtime | {"tools": selected}
    return yaml.dump(closure, Dumper=NoAliases, sort_keys=False).encode(), closure


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
    return next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == actor
    )


def _case(**changes):
    state = objects._state()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"] = [
        {
            "id": "ambient",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
            "metadata": {"plane": "material", "lighting": "bright"},
        }
    ]
    _position(state, "hero")["position"] = [-10, -5, 0]
    _position(state, "guard")["position"] = [10, -5, 0]
    _position(state, "other")["position"] = [20, -5, 0]
    for actor in ("guard", "other"):
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=0)
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"].update(
            dexterity=-100, constitution=-100, wisdom=-100
        )
    objects._run(
        "cast_srd_prismatic_wall",
        state,
        **{
            "caster_state": "hero",
            "slot_level": 9,
            "save_dc": 40,
            "origin": [10, 0, 5],
            "length": 20,
            "height": 10,
            **changes,
        },
    )
    return state, state[f"{SRD}.entities"]["hero"]["last_prismatic_wall_cast"]["source_id"]


def _source(state, sid):
    return state[f"{SRD}.state"]["spell_prismatic_wall_sources"][sid]
