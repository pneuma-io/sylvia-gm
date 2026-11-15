"""Default point sight uses its actual derived plane through an installed secure source."""

import importlib.util
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime, _RuntimeDumper

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_action_budget as budget
from tests.srd_full.test_srd_spell_secure_areas import SRD, _case

ROOT = Path(__file__).resolve().parents[2]


@lru_cache
def _runtime():
    import scripts.srd_full.srd_spell_secure_areas as secure

    if path := os.environ.get("FC14_SECURE_PLANE_OWNER"):
        spec = importlib.util.spec_from_file_location("scripts._secure_plane_owner", path)
        secure = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(secure)
    text = Path(
        os.environ.get(
            "FC14_SECURE_PLANE_RUNTIME", ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
        )
    ).read_text()
    text = secure.bind_runtime_consumers(secure.extend_runtime(text))
    assert secure.bind_runtime_consumers(secure.extend_runtime(text)) == text
    runtime = _load_runtime(text)
    runtime["definitions"]["spells"].update(secure.PROFILES)
    pending, selected = ["cast_srd_spell_secure_area", "resolve_srd_illumination_point_sight"], {}
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
    runtime, encoded = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="5.2.1",
        package_sha256="b" * 64,
        runtime_bytes=encoded,
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
    monkeypatch.setattr(budget, "_mechanic", _mechanic)


def test_default_point_plane_qualifies_actual_active_circle(monkeypatch):
    import sylvia_gm.application.package_runtime as package

    state, data, _ = _case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"] = [
        {
            "id": "ambient",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
            "metadata": {"plane": "material", "lighting": "bright"},
        }
    ]
    context = budget._run(
        "cast_srd_spell_secure_area",
        state,
        participant_data=data,
        caster_state="hero",
        spell="magic_circle",
        slot_level=3,
        save_dc=18,
        origin=[0, 0, 0],
        caster_can_see_origin=True,
        caster_can_touch_origin=True,
        chosen_types=["fiend"],
    ).result["context"]
    sid = context["_new_secure_source_id"]
    source = state[f"{SRD}.state"]["spell_secure_area_sources"][sid]
    assert source["installed"] is True and source["plane"] == "material"
    calls = []
    original = package.execute_tool

    def observed(name, parameters, *args, **kwargs):
        result = original(name, parameters, *args, **kwargs)
        if name == "check_srd_secure_area_information":
            calls.append(parameters["plane"])
        assert result.success, (name, result.error)
        return result

    monkeypatch.setattr(package, "execute_tool", observed)
    omitted = budget._run(
        "resolve_srd_illumination_point_sight",
        state,
        observer_state="hero",
        point=[20, 0, 0],
    ).result["context"]
    explicit = budget._run(
        "resolve_srd_illumination_point_sight",
        state,
        observer_state="hero",
        point=[20, 0, 0],
        perception_plane="material",
    ).result["context"]
    assert calls == ["material", "material"]
    assert omitted["_illumination_point_can_see"] == explicit["_illumination_point_can_see"]
    assert omitted["_illumination_point_plane"] == "material"
    assert state[f"{SRD}.state"]["spell_secure_area_sources"][sid]["installed"] is True
