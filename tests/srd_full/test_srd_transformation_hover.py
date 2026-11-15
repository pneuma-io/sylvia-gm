"""Replacement bodies use printed Hover instead of inheriting the original body's feature."""

import json
import os
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.build_srd_beast_forms import build_index
from scripts.srd_full.build_srd_non_beast_forms import build_index as build_non_beast_index
from scripts.srd_full.srd_spell_barriers import _effect_lists
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools
from scripts.srd_full.srd_transformation_spells import _projection_tool

from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_wave6_transformations as forms

SRD = budget.SRD


@lru_cache
def _runtime():
    """Compose the genuine generated projection hunk onto the admitted native owner graph."""
    base = Path(os.environ.get("FC14_MATERIAL_RUNTIME", budget.ROOT / "runtime/ruleset.yaml"))
    text = base.read_text()
    runtime = _load_runtime(text)
    owner = deepcopy(runtime["tools"]["project_srd_transformation"])
    candidates = [
        effect
        for effects in _effect_lists(_projection_tool()["effects"])
        for effect in effects
        if effect.get("set", {}).get("field") == "target_state.can_hover"
        and "speed_text" in str(effect)
    ]
    assert len(candidates) == 1
    replacements = 0
    for effects in _effect_lists(owner["effects"]):
        for i, effect in list(enumerate(effects)):
            if effect.get("set") == {
                "field": "target_state.base_speed",
                "value": "_projected_beast.walk_speed",
            }:
                effects.insert(i + 1, deepcopy(candidates[0]))
                replacements += 1
    assert replacements == 1
    text = _replace_tools(text, {"project_srd_transformation": owner})
    assert _replace_tools(text, {"project_srd_transformation": owner}) == text
    runtime = _load_runtime(text)
    assert not (errors := validate_ruleset_data(runtime)), errors
    return text.encode(), runtime


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
    monkeypatch.setattr(budget, "_mechanic", _mechanic)


def _install(state, spell, form_id="", mode="beast"):
    if form_id:
        budget._run(
            "validate_srd_transformation_form",
            state,
            target_state="guard",
            spell=spell,
            form_id=form_id,
            seen_before=True,
        )
    budget._run(
        "install_srd_transformation_source",
        state,
        target_state="guard",
        caster_state="hero",
        lasting_state="state",
        spell=spell,
        form_id=form_id,
        mode=mode,
        actual_spell_level=8 if spell == "animal_shapes" else 9 if spell == "true_polymorph" else 3,
    )
    return forms._entity(state)["form_current_source_id"]


def test_source_rows_preserve_actual_speed_hover_and_do_not_admit_unsupported_bodies():
    beasts = build_index()["forms"]
    non_beasts = build_non_beast_index()["forms"]
    assert len(beasts) == 90
    assert all("(hover)" not in row["speed_text"] for row in beasts.values())
    assert non_beasts["air_elemental"]["speed_text"] == "10 ft., Fly 90 ft. (hover)"
    assert non_beasts["air_elemental"]["execution_supported"] is False
    assert all(
        "(hover)" not in row["speed_text"]
        for row in non_beasts.values()
        if row["execution_supported"]
    )


@pytest.mark.parametrize(
    "spell,form_id", [("animal_shapes", "hawk"), ("true_polymorph", "owlbear")]
)
def test_real_replacement_source_clears_native_hover_and_saved_removal_restores_it(spell, form_id):
    state, data, parameters = forms._setup(
        spell, transformation_form=form_id, forego_save_targets=["guard"]
    )
    forms._entity(state).update(can_hover=True, flying_speed=40)
    if spell == "true_polymorph":
        forms._cast(state, data, parameters)
        sid = forms._entity(state)["form_current_source_id"]
    else:
        sid = _install(state, spell, form_id)
    target = forms._entity(state)
    assert target["can_hover"] is False
    assert target["form_base_statistics"]["can_hover"] is True
    reopened = json.loads(json.dumps(state))
    before = deepcopy(reopened)
    budget._run("project_srd_transformation", reopened, target_state="guard", lasting_state="state")
    assert forms._entity(reopened)["can_hover"] is False
    assert reopened == before
    budget._run(
        "end_srd_transformation_source",
        reopened,
        target_state="guard",
        source_id=sid,
        lasting_state="state",
    )
    assert forms._entity(reopened)["can_hover"] is True
    assert forms._entity(reopened)["flying_speed"] == 40


def test_actual_newer_beast_masks_mist_hover_and_exact_removal_restores_each_body():
    state, data, parameters = forms._setup("gaseous_form")
    forms._entity(state).update(can_hover=False)
    forms._cast(state, data, parameters)
    mist = forms._entity(state)["form_current_source_id"]
    assert forms._entity(state)["can_hover"] is True
    hawk = _install(state, "animal_shapes", "hawk")
    assert forms._entity(state)["can_hover"] is False
    budget._run(
        "end_srd_transformation_source",
        state,
        target_state="guard",
        source_id=hawk,
        lasting_state="state",
    )
    assert forms._entity(state)["can_hover"] is True
    assert forms._entity(state)["form_current_source_id"] == mist
    budget._run(
        "end_srd_transformation_source",
        state,
        target_state="guard",
        source_id=mist,
        lasting_state="state",
    )
    assert forms._entity(state)["can_hover"] is False
