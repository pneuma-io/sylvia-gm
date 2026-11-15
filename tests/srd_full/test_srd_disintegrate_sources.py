"""The paid force-source exceptions retire exact existing sources without an occupant packet."""

import ast
import importlib.util
import sys
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest
import yaml
from scripts import srd_disintegrate_sources as disintegrate
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_scene_relations import _tool_scene

from sylvia_gm.application import package_runtime
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_freezing_sphere as globe
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene

SOURCE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-disintegrate-sources/srd_solid_spell_walls.py")
)
SOURCE_SHA = "60b98043e0e1566fc211645ee061c415e9a6a5b88e4b784187f34a151788633f"
SRD = cards.SRD


@lru_cache
def _runtime():
    assert sha256(SOURCE.read_bytes()).hexdigest() == SOURCE_SHA
    barrier_source = SOURCE.with_name("srd_spell_barriers.py")
    tree = ast.parse(barrier_source.read_text(encoding="utf-8"))
    helpers = ast.Module(
        body=[
            n
            for n in tree.body
            if isinstance(n, ast.FunctionDef) and n.name in {"_tool", "_effect_lists"}
        ],
        type_ignores=[],
    )
    namespace = {"_tool_scene": _tool_scene}
    exec(compile(helpers, str(barrier_source), "exec"), namespace)
    barrier_helpers = ModuleType("scripts.srd_full.srd_spell_barriers")
    barrier_helpers._tool = namespace["_tool"]
    barrier_helpers._effect_lists = namespace["_effect_lists"]
    spec = importlib.util.spec_from_file_location("scripts._disintegrate_wall_dependency", SOURCE)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"scripts.srd_full.srd_spell_barriers": barrier_helpers}):
        spec.loader.exec_module(module)
    runtime = deepcopy(globe._runtime()[1])
    runtime["tools"].update(module._tools())
    text = yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    text = disintegrate.bind_runtime_consumers(text)
    assert disintegrate.bind_runtime_consumers(text) == text
    return text.encode(), yaml.load(text, Loader=yaml.CSafeLoader)


@lru_cache
def _mechanic(name):
    encoded, runtime = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=encoded,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=_tool_schema(runtime["tools"][name], tool_name=name),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def native(monkeypatch):
    actual = package_runtime.execute_tool

    def diagnose(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnose)
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[1]["definitions"]["spells"])


def test_paid_disintegrate_destroys_real_resilient_sphere_and_preserves_its_creature():
    state, data, params = cards._spell_case("resilient_sphere")
    for actor in state[f"{SRD}.entities"].values():
        actor.setdefault("size_rank", 2)
    _admit_scene(state)
    for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        row["metadata"]["plane"] = "material"
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    result = budget._run(
        "cast_srd_spell_barrier",
        state,
        caster_state="hero",
        spell="resilient_sphere",
        slot_level=4,
        concentration_save_modifier=3,
        save_dc=18,
        subject="guard",
        target_willing=True,
    )
    assert result.result["status"] == "committed"
    actor = state[f"{SRD}.entities"]["hero"]
    source = actor["barrier_source_id"]
    inside = deepcopy(state[f"{SRD}.entities"]["guard"])
    _, data, params = cards._spell_case("disintegrate")
    fresh, _, _ = cards._spell_case("disintegrate")
    actor.update(
        prepared_spells=["disintegrate"],
        spell_materials=fresh[f"{SRD}.entities"]["hero"]["spell_materials"],
    )
    actor["inventory"] += fresh[f"{SRD}.entities"]["hero"]["inventory"]
    params.pop("target")
    params.update(disintegrate_source_kind="resilient_sphere", disintegrate_source_id=source)
    result = budget._run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert result.result["status"] == "committed", result.result
    assert state[f"{SRD}.state"]["spell_barrier_sources"][source]["installed"] is False
    assert state[f"{SRD}.entities"]["guard"] == inside
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
    assert "no creature damage" in str(result.result["result_card"])


def test_force_wall_exception_reuses_exact_whole_source_end_not_object_damage():
    encoded, runtime = _runtime()
    leaf = runtime["tools"][disintegrate.LEAF]
    assert "end_srd_solid_spell_wall_source" in str(leaf["effects"])
    assert "apply_srd" not in str(leaf["effects"])
    assert "dice" not in str(leaf["effects"])
    assert "retire_srd_solid_spell_wall_record" in str(
        runtime["tools"]["end_srd_solid_spell_wall_source"]["effects"]
    )
    assert encoded
    state, _, _ = cards._spell_case("disintegrate")
    _admit_scene(state)
    for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        row["metadata"]["plane"] = "material"
    installed = budget._run(
        "cast_srd_solid_spell_wall",
        state,
        caster_state="hero",
        spell="wall_of_force",
        slot_level=5,
        concentration_save_modifier=3,
        origin=[0, 40, 0],
    ).result
    assert installed["status"] == "committed"
    source = installed["context"]["_new_solid_wall_id"]
    ended = budget._run(
        disintegrate.LEAF,
        state,
        caster_state="hero",
        kind="wall_of_force",
        source_id=source,
        target_distance=40,
        caster_can_see_target=True,
    ).result
    assert ended["status"] == "committed"
    assert not state[f"{SRD}.state"]["solid_spell_wall_sources"][source]["installed"]
    panels = [
        r
        for r in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if r["metadata"].get("solid_wall_source_id") == source
    ]
    assert panels and all(r["active"] is False for r in panels)
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
