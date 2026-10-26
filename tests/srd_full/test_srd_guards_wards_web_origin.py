"""Ward stair Boxes use their derived centers without bypassing actual opaque solids."""

import json
from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_guards_wards as wards
from tests import test_srd_guards_wards_web_burning as burn
from tests import test_srd_spell_object_content as objects

SRD = objects.SRD


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(objects, "_mechanic", burn._mechanic)


def _floor_case():
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
    burn._position(state, "hero")["position"] = [0, 0, 0]
    for actor in ("guard", "other"):
        burn._position(state, actor)["position"] = [20, 20 if actor == "guard" else -20, 0]
        target = state[f"{SRD}.entities"][actor]
        target.update(hp=1000, max_hp=1000, temp_hp=0)
        target["saving_throw_modifiers"]["dexterity"] = -100
    sid = wards._cast_ward(state, save_dc=40, stairs=[{"min": [2, 0, 0], "max": [7, 5, 5]}])
    source = state[f"{SRD}.state"]["spell_guards_wards_sources"][sid]
    return state, sid, source["web_section_ids"][0]


def test_floor_cube_real_restraint_burn_and_saved_retry_keep_exact_source():
    state, sid, area = _floor_case()
    section = burn._section(state, area)
    assert section["volume"] == {"kind": "box", "min": [2, 0, 0], "max": [7, 5, 5]}
    assert section["origin"] == [4.5, 2.5, 2.5]
    burn._move_inside(state)
    assert burn._inside(state, area)
    objects._run("resolve_srd_guards_wards_web_event", state, target_state="guard", area_id=area)
    target = state[f"{SRD}.entities"]["guard"]
    receipt = deepcopy(target["last_guards_wards_web_save"])
    assert receipt["outcome"] == "failure"
    assert "restrained" in target["conditions"]
    cid = receipt["condition_source_id"]
    condition = state[f"{SRD}.state"]["timed_condition_sources"][cid]
    assert condition["guarded_web_parent_serial"] == section["parent_serial"]
    burn._fire(state)
    state[f"{SRD}.combat"].update(turn_active=True, active_actor="guard", turn_sequence=7)
    hp = target["hp"]
    objects._run("resolve_srd_environment_hazard_start_turn", state, target_state="guard")
    target = state[f"{SRD}.entities"]["guard"]
    damage = deepcopy(target["last_guards_wards_web_fire"])
    assert damage["raw_damage"]["count"] == 2 and damage["raw_damage"]["sides"] == 4
    assert target["hp"] == hp - damage["applied_damage"]
    reopened = json.loads(json.dumps(state))
    prior = deepcopy(reopened)
    objects._run("resolve_srd_guards_wards_web_burning_start", reopened, target_state="guard")
    assert reopened == prior
    reopened[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    objects._run("end_concentration", reopened, caster_state="hero")
    objects._run("advance_srd_lasting_time", reopened, elapsed_rounds=1)
    assert burn._section(reopened, area)["web_destroyed"]
    assert not reopened[f"{SRD}.state"]["timed_condition_sources"][cid]["installed"]
    assert "restrained" not in reopened[f"{SRD}.entities"]["guard"]["conditions"]
    assert reopened[f"{SRD}.state"]["spell_guards_wards_sources"][sid]["groups"]["stairs"]


def test_independent_opaque_box_still_blocks_floor_cube_effect_and_fire_install():
    state, _, area = _floor_case()
    solid = deepcopy(state[f"{SRD}.state"]["spatial_scene"]["geometry"][-1])
    solid.update(
        id="independent_opaque_wall",
        volume={"kind": "box", "min": [4.3, -10, 0], "max": [4.45, 10, 10]},
        metadata={"plane": "material"},
    )
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(solid)
    objects._run(
        "move_srd_travel_subject",
        state,
        subject="guard",
        destination=[3.8, 3, 0],
        plane="material",
        teleport=True,
    )
    assert not burn._inside(state, area)
    objects._run("resolve_srd_guards_wards_web_event", state, target_state="guard", area_id=area)
    target = state[f"{SRD}.entities"]["guard"]
    assert "restrained" not in target["conditions"]
    assert "last_guards_wards_web_save" not in target
    prior = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="solid|obstruction|exposure"):
        burn._fire(state)
    assert state == prior
    assert "web_burn" not in burn._section(state, area)
