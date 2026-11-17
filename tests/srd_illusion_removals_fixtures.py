"""Real image sources retain cast provenance through exact dismissal and Dispel."""

import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_spell_removals import extend_runtime

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public_casting
from tests.test_srd_npc_spell_casting import _act
from tests.test_srd_spell_result_cards import SRD, _casting_headers
from tests.test_srd_spell_wave3_information import _case as _information_case


@pytest.fixture(autouse=True)
def _compiled_owner(monkeypatch):
    runtime_root = os.environ.get("FC14_SOURCE_RUNTIME_ROOT")
    if runtime_root:
        monkeypatch.setattr(budget, "ROOT", Path(runtime_root))
        monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: Path(runtime_root))
    budget._mechanic.cache_clear()
    bundled_packages.bundled_srd_archive.cache_clear()
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    yield
    budget._mechanic.cache_clear()
    bundled_packages.bundled_srd_archive.cache_clear()


def _prepare(state, data, spell):
    hero = state[f"{SRD}.entities"]["hero"]
    hero["prepared_spells"] = [spell]
    hero["spell_slot_cast_this_turn"] = False
    data["spellcasting_grants"][0]["spells"] = [spell]
    data["wizard_spellbook"] = [spell] if _casting_headers()[spell]["level"] else []
    materials = [
        {
            "id": f"{spell}_material_{i}",
            "inventory_item": f"{spell}_material_{i}",
            "kind": "specific",
            "components": [part["id"]],
            "quantity": part["quantity"] * 3,
            "value_cp": part["minimum_value_cp"],
        }
        for i, part in enumerate(_casting_headers()[spell]["material_parts"])
    ]
    hero["spell_materials"] = materials
    hero["inventory"] += [
        m["inventory_item"] for m in materials if m["inventory_item"] not in hero["inventory"]
    ]
    return [m["id"] for m in materials]


def _fresh_case(spell="minor_illusion", *, slot=None):
    state, data, params = _information_case("mind_blank")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"].append(
        {
            "id": "illusion-removal-ambient",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
            "metadata": {"plane": "material", "lighting": "bright"},
        }
    )
    level = _casting_headers()[spell]["level"]
    params.update(
        spell=spell,
        slot_resource=f"spell_slot_{slot or level}" if level else "cantrip",
        target="hero",
        illusion_position=[10, 0, 0],
        illusion_size=[5, 5, 5],
        illusion_description="A stone screen",
        illusion_subject_kind="object",
        illusion_trigger="a visible creature",
        illusion_performance_seconds=60,
        material_item_ids=_prepare(state, data, spell),
    )
    return state, data, params


def _cast(state, data, params):
    state[f"{SRD}.entities"]["hero"]["spell_slot_cast_this_turn"] = False
    return budget._run("cast_spell_outside_combat", state, participant_data=data, **params)


def _case(spell="minor_illusion", *, slot=None):
    state, data, params = _fresh_case(spell, slot=slot)
    result = _cast(state, data, params)
    source = next(reversed(state[f"{SRD}.state"]["illusion_spell_sources"].values()))
    return state, data, params, source, result


def _proxy(state, source):
    return next(
        e
        for e in state[f"{SRD}.state"]["spatial_scene"]["entities"]
        if e["id"] == source["proxy_id"]
    )


def _source(state, source):
    return state[f"{SRD}.state"]["illusion_spell_sources"][source["id"]]


def _dispel(state, data, params, source, *, slot=3, target="hero", select_proxy=True):
    materials = _prepare(state, data, "dispel_magic")
    choices = (
        {"recovery_choice": "illusion", "recovery_source_id": source["id"]} if select_proxy else {}
    )
    return _cast(
        state,
        data,
        params
        | {
            "spell": "dispel_magic",
            "target": target,
            "slot_resource": f"spell_slot_{slot}",
            "material_item_ids": materials,
        }
        | choices,
    )
