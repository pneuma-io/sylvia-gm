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
from scripts.build_srd_5_2_1_mechanics import _tool_schema
from scripts.build_srd_5_2_1_spells import _casting_requirements
from scripts.srd_solid_spell_walls import (
    FAMILY,
    POOL,
    PROFILES,
    SOURCE_SHA256,
    bind_runtime_consumers,
    extend_runtime,
)
from scripts.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_action_budget as actions
from tests import srd_prismatic_wall_fixtures as wall
from tests import test_srd_spell_object_content as objects
from tests import test_srd_spell_result_cards as cards

ROOT = Path(__file__).resolve().parents[1]
SRD = objects.SRD
BASE = Path(
    os.environ.get("FC14_SOLID_WALL_RUNTIME", ROOT / "rulesets/srd-lite/runtime/ruleset.yaml")
)
SOURCE_PARENT = Path(
    os.environ.get("FC14_SOLID_WALL_SOURCE_PARENT", ROOT / "scripts/srd_spell_source_instances.py")
)


def _header():
    corpus = json.loads((ROOT / "rulesets/srd-lite/content/corpus/spells.json").read_text())
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
