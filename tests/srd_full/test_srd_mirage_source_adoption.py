"""Mirage's paid cast identity owns exact terrain dismissal and selected area Dispel."""

import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
import yaml

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_lifetime_source_adoption as lifetime
from tests import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_shared_source_adoption import _case as _source_case

budget = lifetime.budget
SRD = lifetime.SRD


@pytest.fixture(autouse=True)
def _compiled_owner(monkeypatch):
    yield from lifetime._compiled_owner.__wrapped__(monkeypatch)


def _case(spell="mirage_arcane"):
    state, data, params = _source_case(spell)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["geometry"].append(
        {
            "id": "terrain",
            "map_id": scene["map"]["id"],
            "volume": {"kind": "box", "min": [15, -1, -1], "max": [17, 1, 4]},
            "active": True,
            "movement": "blocking",
            "occupies_cells": True,
            "visibility": "opaque",
            "metadata": {"plane": "material", "natural_terrain": True, "ground": True},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )
    params.update(
        mirage_corner=[-20, -20, 0],
        mirage_side_feet=40,
        mirage_description="A soft marsh with a low wall",
        mirage_terrain_kind="marsh",
        mirage_tactile_kind="soft mud",
        mirage_structures=[
            {
                "id": "mirage-wall",
                "position": [10, 10, 0],
                "extent": [2, 5, 4],
                "description": "A low wall",
                "tactile_kind": "cold stone",
                "blocks_movement": True,
                "ground": False,
            }
        ],
        mirage_terrain_changes=[
            {
                "subject": "terrain",
                "minimum": [15, -1, -1],
                "maximum": [17, 1, 1],
                "movement": "passable",
                "ground": True,
                "terrain_kind": "marsh",
                "tactile_kind": "soft mud",
            }
        ],
    )
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("dispel_magic")
    data["spellcasting_grants"][0]["spells"].append("dispel_magic")
    data["wizard_spellbook"].append("dispel_magic")
    return state, data, params


def _cast(state, data, params):
    state[f"{SRD}.entities"]["hero"]["spell_slot_cast_this_turn"] = False
    previous = state[f"{SRD}.entities"]["hero"].get("last_spell_source_cast_id")
    budget._run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    assert state[f"{SRD}.entities"]["hero"].get("last_spell_source_cast_id") == previous
    budget._run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=100,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    return budget._run("cast_spell_outside_combat", state, participant_data=data, **params)


def _terrain(state):
    return next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["geometry"] if row["id"] == "terrain"
    )


def _source(state):
    return tuple(state[f"{SRD}.state"]["mirage_sources"].values())[-1]


def test_actual_paid_ten_minutes_stamp_identity_and_exact_dismissal_restores_terrain():
    state, data, params = _case()
    original = deepcopy(_terrain(state))
    before = state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"]
    _cast(state, data, params)
    source = _source(state)
    assert source["cast_id"] == "srd_cast:1" and source["source_caster_id"] == "hero"
    assert source["spell_level"] == 7 and source["ongoing_duration"] is True
    assert state[f"{SRD}.state"]["lasting_tick"] == 200
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == before - 1
    assert _terrain(state)["volume"]["max"][2] == 1
    budget._run("dismiss_srd_spell", state, caster_state="hero", cast_id=source["cast_id"])
    assert _source(state)["installed"] is False
    assert _terrain(state)["volume"] == original["volume"]
    assert _terrain(state)["movement"] == original["movement"]
    assert not any(
        row["metadata"].get("mirage_source_id") == source["id"]
        for row in state[f"{SRD}.state"]["spatial_scene"]["geometry"]
    )


def test_independent_paid_cast_dismissal_reprojects_remaining_actual_source():
    state, data, params = _case()
    original = deepcopy(_terrain(state))
    _cast(state, data, params)
    first = deepcopy(_source(state))
    params["mirage_structures"][0]["id"] = "second-wall"
    params["mirage_terrain_changes"][0]["maximum"][2] = 2
    _cast(state, data, params)
    second = deepcopy(_source(state))
    assert {first["cast_id"], second["cast_id"]} == {"srd_cast:1", "srd_cast:2"}
    budget._run("dismiss_srd_spell", state, caster_state="hero", cast_id=first["cast_id"])
    assert _source(state)["installed"] is True and _terrain(state)["volume"]["max"][2] == 2
    budget._run("dismiss_srd_mirage_arcane", state, caster_state="hero", mirage_id=second["id"])
    assert _source(state)["installed"] is False
    assert _terrain(state)["volume"] == original["volume"]


@pytest.mark.parametrize("seed,ended", [(2, False), (5, True)])
def test_paid_selected_area_dispel_uses_real_lower_slot_roll(monkeypatch, seed, ended):
    state, data, params = _case()
    _cast(state, data, params)
    source = deepcopy(_source(state))
    monkeypatch.setattr(dice, "_rng", Random(seed))
    result = lifetime._dispel(
        state,
        data,
        params,
        slot=3,
        recovery_choice="mirage",
        recovery_source_id=source["id"],
        recovery_position=[0, 0, 0],
    )
    assert _source(state)["installed"] is not ended
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
    assert "DC 17" in str(result.result["result_card"])


def test_caster_bookkeeping_is_not_area_subject_and_outside_point_rejects_atomically():
    state, data, params = _case()
    _cast(state, data, params)
    source = deepcopy(_source(state))
    lifetime._dispel(state, data, params, target="hero")
    assert _source(state)["installed"] is True
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="saved Mirage Square"):
        lifetime._dispel(
            state,
            data,
            params,
            recovery_choice="mirage",
            recovery_source_id=source["id"],
            recovery_position=[30, 0, 0],
        )
    assert state == before


def test_invented_identity_rejects_and_unstamped_source_cannot_be_grouped():
    state, _, params = _case()
    choices = {k: v for k, v in params.items() if k.startswith("mirage_")}
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="actual lifetime cast owner"):
        budget._run(
            "cast_srd_mirage_arcane",
            state,
            caster_state="hero",
            slot_level=7,
            spell_cast_id="invented",
            spell_cast_owner="hero",
            actual_spell_level=7,
            **choices,
        )
    assert state == before
    budget._run("cast_srd_mirage_arcane", state, caster_state="hero", slot_level=7, **choices)
    source = _source(state)
    assert source["cast_id"] == "" and source["source_caster_id"] is None
    assert source["ongoing_duration"] is False
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="authentic finite Mirage"):
        budget._run("dismiss_srd_mirage_arcane", state, caster_state="hero", mirage_id=source["id"])
    assert state == before


def test_source_and_removal_emission_is_semantically_stable():
    text = (budget.ROOT / "runtime/ruleset.yaml").read_text()
    first = lifetime.removals(lifetime.sources(text))
    second = lifetime.removals(lifetime.sources(first))
    assert yaml.load(first, Loader=yaml.CSafeLoader) == yaml.load(second, Loader=yaml.CSafeLoader)


def test_public_paid_completion_reopen_selected_area_dispel_and_retry(tmp_path, monkeypatch):
    parent = os.environ.get("FC14_SOURCE_PACKAGE_ROOT")
    if os.environ.get("FC14_SOURCE_RUNTIME_ROOT"):
        assert parent
        monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: Path(parent))
        monkeypatch.setattr(public_casting, "bundled_srd_archive", lifetime._composed_archive)
    monkeypatch.setattr(public_casting, "_spell_case", _case)
    path = tmp_path / "mirage-source.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "mirage_arcane")
        beginning, _ = _act(owner, "begin_srd_spell_cast", **_begin(params))
        assert beginning.status is ActionStatus.COMMITTED, beginning.result
        continued, _ = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=100,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert continued.status is ActionStatus.COMMITTED, continued.result
        cast, cast_command = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = owner.snapshot("casting")
        source = _source(saved.state)
        assert source["cast_id"] == "srd_cast:1" and source["source_caster_id"] == "hero"
        assert owner.execute_action(cast_command) == cast and owner.snapshot("casting") == saved
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        # The public campaign already prepared Dispel Magic in its validated source.
        dispelled, command = _act(
            owner,
            "cast_spell_outside_combat",
            **(
                params
                | {
                    "spell": "dispel_magic",
                    "slot_resource": "spell_slot_9",
                    "material_item_ids": [],
                    "recovery_choice": "mirage",
                    "recovery_source_id": source["id"],
                    "recovery_position": [0, 0, 0],
                }
            ),
        )
        assert dispelled.status is ActionStatus.COMMITTED, dispelled.result
        ended = owner.snapshot("casting")
        assert _source(ended.state)["installed"] is False
        assert _terrain(ended.state)["volume"]["max"][2] == 4
        assert owner.execute_action(command) == dispelled and owner.snapshot("casting") == ended
        assert owner.execute_action(cast_command) == cast and owner.snapshot("casting") == ended
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == ended
