"""Real Ring Fire coexists with the ward's explicitly Box-only Web burn owner."""

import json
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_guards_wards import extend_runtime as extend_parent
from scripts.srd_full.srd_guards_wards_web import bind_runtime_consumers as bind_web
from scripts.srd_full.srd_guards_wards_web import extend_runtime as extend_web
from scripts.srd_full.srd_guards_wards_web_burning import bind_runtime_consumers
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime, _RuntimeDumper

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_guards_wards_web_burning as burning
from tests import test_srd_spell_object_content as objects

SRD = objects.SRD
ROOT = Path(__file__).resolve().parents[2]


@lru_cache
def _runtime():
    base = Path(
        os.environ.get(
            "FC14_RING_BURN_RUNTIME", str(ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml")
        )
    )
    text = base.read_text()
    assert "resolve_srd_wall_of_fire_ring_body_segment:" in text, "Use the genuine Ring owner."
    text = bind_runtime_consumers(bind_web(extend_web(extend_parent(text))))
    assert bind_runtime_consumers(text) == text
    runtime = _load_runtime(text)
    # The approved compact descriptor dialect defaults an omitted type to
    # string. Expand only those exact declarations for this older isolated
    # parser; spell/source/effect behavior and runtime caps are unchanged.
    declarations = runtime.pop("parameter_declarations", {})
    for tool in runtime["tools"].values():
        expanded = []
        for descriptor in tool.get("parameters", []):
            descriptor = (
                deepcopy(declarations[descriptor]) if isinstance(descriptor, str) else descriptor
            )
            descriptor.setdefault("type", "string")
            expanded.append(descriptor)
        tool["parameters"] = expanded
    pending = [
        "record_srd_object_access",
        "cast_srd_guards_wards",
        "cast_spell_environment",
        "observe_srd_guards_wards_web_fire",
        "expose_srd_guards_wards_web_fire",
        "advance_srd_lasting_time",
        "end_concentration",
    ]
    selected = {}
    while pending:
        name = pending.pop()
        if name in selected:
            continue
        selected[name] = runtime["tools"][name]
        stack = [selected[name]]
        while stack:
            item = stack.pop()
            if isinstance(item, dict):
                if "call" in item:
                    pending.append(item["call"])
                stack.extend(item.values())
            elif isinstance(item, list):
                stack.extend(item)
    closure = runtime | {"tools": selected}
    assert not (errors := validate_ruleset_data(closure)), errors
    return closure, yaml.dump(closure, Dumper=_RuntimeDumper, sort_keys=False).encode()


@lru_cache
def _mechanic(name):
    runtime, raw = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="5.2.1",
        package_sha256="d" * 64,
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
def _owner(monkeypatch):
    monkeypatch.setattr(objects, "_mechanic", _mechanic)


def _ring(state):
    return objects._run(
        "cast_spell_environment",
        state,
        caster_state="hero",
        spell="wall_of_fire",
        slot_level=4,
        save_dc=18,
        concentration_save_modifier=0,
        origin=[14, 2.5, 0],
        wall_axis="north_south",
        fire_wall_length=5,
        fire_wall_side="positive",
        fire_wall_shape="ring",
        fire_ring_diameter=20,
    )


def test_actual_ring_install_reopen_and_observation_skip_box_only_web_exposure():
    state, sid, area = burning._case()
    before = deepcopy(burning._section(state, area))
    _ring(state)
    ring = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert ring["volume"]["kind"] == "annulus"
    assert ring["volume"]["outer"]["radius"] == 10
    assert ring["volume"]["inner"]["radius"] == 9
    assert ring["active"] and ring["appearance_completed"]
    assert burning._section(state, area) == before
    state = json.loads(json.dumps(state))
    for _ in range(2):
        objects._run("observe_srd_guards_wards_web_fire", state)
    assert burning._section(state, area) == before
    result = objects._run("expose_srd_guards_wards_web_fire", state, fire_area_id="hero")
    assert result["_ward_web_fire_exposure_supported"] is False
    assert state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["groups"]["stairs"]


def test_box_fire_still_burns_after_the_real_ring_source_ends():
    state, _, area = burning._case()
    _ring(state)
    objects._run("end_concentration", state, caster_state="hero")
    burning._fire(state)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["volume"]["kind"] == "box" and source["active"]
    burn = deepcopy(burning._section(state, area)["web_burn"])
    assert burn["status"] == "burning" and burn["fire_sequence"] == source["sequence"]
    result = objects._run("expose_srd_guards_wards_web_fire", state, fire_area_id="hero")
    assert result["_ward_web_fire_exposure_supported"] is True
    assert burning._section(state, area)["web_burn"] == burn
