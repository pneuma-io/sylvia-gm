"""Actual ward effects receive one source check while independent door spells survive."""

import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.srd_spell_removals import extend_runtime as removal
from scripts.srd_full.srd_spell_source_instances import extend_runtime as source

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_lifetime_source_adoption as lifetime
from tests import test_srd_public_spell_casting as public
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_npc_spell_casting import _act

budget = lifetime.budget
SRD = lifetime.SRD


@pytest.fixture(autouse=True)
def _compiled_owner(monkeypatch):
    yield from lifetime._compiled_owner.__wrapped__(monkeypatch)


def _add_spell(state, data, spell):
    """Add admitted source preparation and materials without replacing the ward's supplies."""
    hero = state[f"{SRD}.entities"]["hero"]
    prepared = hero["prepared_spells"][:]
    materials = deepcopy(hero["spell_materials"])
    granted = data["spellcasting_grants"][0]["spells"][:]
    book = data.get("wizard_spellbook", [])[:]
    added = lifetime._prepare(state, data, spell)
    hero["prepared_spells"] = list(dict.fromkeys(prepared + [spell]))
    hero["spell_materials"] = materials + hero["spell_materials"]
    data["spellcasting_grants"][0]["spells"] = list(dict.fromkeys(granted + [spell]))
    data["wizard_spellbook"] = list(dict.fromkeys(book + [spell]))
    return added


def _case(spell="guards_and_wards"):
    state, data, params = lifetime._case(spell)
    _add_spell(state, data, "dispel_magic")
    for name, x in [("door", 3), ("second-door", 15)]:
        scene = state[f"{SRD}.state"]["spatial_scene"]
        row = deepcopy(next(e for e in scene["entities"] if e["id"] == "paper"))
        row.update(id=name, position=[x, 0, 0])
        row["metadata"].pop("participant_ref", None)
        scene["entities"].append(row)
        budget._run(
            "record_srd_object_access",
            state,
            object_id=name,
            object_kind="door",
            closed=True,
            locks=[],
            stuck=False,
            barred=False,
        )
    params.update(
        guards_wards_origin=[0, 0, 0],
        guards_wards_height=20,
        guards_wards_corridors=[
            {"min": [x, 0, 0], "max": [x + 10, 10, 20]} for x in [0, 12, 24, 36]
        ],
        guards_wards_lamps=[[x + 5, 5, 5] for x in [0, 12, 24, 36]],
    )
    return state, data, params


def _cast(state, data, params):
    serial = state[f"{SRD}.state"].get("spell_source_serial")
    budget._run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    assert state[f"{SRD}.state"].get("spell_source_serial") == serial
    budget._run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=600,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    return budget._run("cast_spell_outside_combat", state, participant_data=data, **params)


def _ward(state):
    lasting = state[f"{SRD}.state"]
    sid = lasting["spell_guards_wards_source_ids"][-1]
    return sid, lasting["spell_guards_wards_sources"][sid]


def _locks(state, door="door"):
    row = next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == door)
    return row["metadata"].get("srd_arcane_locks", [])


def _dispel(params, sid, group="", *, slot=6):
    return params | {
        "spell": "dispel_magic",
        "slot_resource": f"spell_slot_{slot}",
        "material_item_ids": [],
        "recovery_choice": "guards_wards",
        "recovery_source_id": sid,
        "recovery_effect": group,
        "recovery_position": [5, 5, 5],
        "recovery_item": "door" if group == "doors" else "",
    }


def test_actual_paid_hour_ward_stamps_finite_identity_and_parent_dispel_has_no_effect(monkeypatch):
    state, data, params = _case()
    materials = deepcopy(state[f"{SRD}.entities"]["hero"]["spell_materials"])
    _cast(state, data, params)
    sid, ward = _ward(state)
    assert ward["cast_id"] == "srd_cast:1" and ward["source_caster_id"] == "hero"
    assert ward["ongoing_duration"] is True and ward["dispel_ongoing"] is False
    assert (
        ward["spell_level"] == 6
        and state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
    )
    assert state[f"{SRD}.entities"]["hero"]["spell_materials"] == materials
    monkeypatch.setattr(dice, "_rng", Random(2))
    before_rng = dice._rng.getstate()
    result = budget._run(
        "cast_spell_outside_combat", state, participant_data=data, **_dispel(params, sid, slot=3)
    )
    assert dice._rng.getstate() == before_rng
    assert "no effect on Guards and Wards itself" in str(result.result["result_card"])
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
    assert _ward(state)[1]["groups"] == dict.fromkeys(
        ["corridors", "doors", "stairs", "other"], True
    )


def test_actual_lower_slot_failed_effect_check_pays_and_preserves_all_groups(monkeypatch):
    state, data, params = _case()
    _cast(state, data, params)
    sid, _ = _ward(state)
    monkeypatch.setattr(dice, "_rng", Random(2))
    result = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **_dispel(params, sid, "corridors", slot=3),
    )
    assert "d20 2;" in str(result.result["result_card"]) and "DC 16" in str(
        result.result["result_card"]
    )
    assert _ward(state)[1]["groups"]["corridors"] is True
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2


def test_selected_door_effect_ends_one_group_and_keeps_independent_actual_arcane_lock():
    state, data, params = _case()
    mats = _add_spell(state, data, "arcane_lock")
    budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(
            params
            | {
                "spell": "arcane_lock",
                "slot_resource": "spell_slot_2",
                "material_item_ids": mats,
                "object_target_id": "door",
            }
        ),
    )
    independent = deepcopy(_locks(state)[0])
    _cast(state, data, params)
    sid, _ = _ward(state)
    assert len(_locks(state)) == 2 and len(_locks(state, "second-door")) == 1
    result = budget._run(
        "cast_spell_outside_combat", state, participant_data=data, **_dispel(params, sid, "doors")
    )
    assert str(result.result["result_card"]).count(sid) == 1
    assert _locks(state) == [independent] and _locks(state, "second-door") == []
    assert _ward(state)[1]["groups"] == {
        "corridors": True,
        "doors": False,
        "stairs": True,
        "other": True,
    }
    assert _ward(state)[1]["installed"] is True


def test_actual_object_child_dispel_checks_once_then_ends_only_its_parent_door_group(monkeypatch):
    state, data, params = _case()
    _cast(state, data, params)
    sid, _ = _ward(state)
    child = _locks(state)[0]
    monkeypatch.setattr(dice, "_rng", Random(5))
    expected_rng = Random(5)
    assert expected_rng.randint(1, 20) == 20
    result = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(
            _dispel(params, sid, slot=3)
            | {
                "recovery_choice": "object_content",
                "recovery_source_id": child["source_id"],
                "recovery_item": "door",
            }
        ),
    )
    assert (
        _ward(state)[1]["groups"]["doors"] is False
        and _ward(state)[1]["groups"]["corridors"] is True
    )
    assert _locks(state) == [] and _locks(state, "second-door") == []
    assert dice._rng.getstate() == expected_rng.getstate()
    assert result.result["status"] == "committed"


def test_finite_cast_dismissal_and_unsupported_stair_are_source_owned():
    state, data, params = _case()
    _cast(state, data, params)
    sid, ward = _ward(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="stair Web qualifier"):
        budget._run(
            "cast_spell_outside_combat",
            state,
            participant_data=data,
            **_dispel(params, sid, "stairs"),
        )
    assert state == before
    budget._run("dismiss_srd_spell", state, caster_state="hero", cast_id=ward["cast_id"])
    assert _ward(state)[1]["installed"] is False and _locks(state) == []


def test_selected_lights_qualify_real_contribution_and_leave_corridors_active():
    state, data, params = _case()
    _cast(state, data, params)
    sid, _ = _ward(state)
    bad = _dispel(params, sid, "other") | {"recovery_position": [45, 45, 5]}
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="light.*contribution"):
        budget._run("cast_spell_outside_combat", state, participant_data=data, **bad)
    assert state == before
    budget._run(
        "cast_spell_outside_combat", state, participant_data=data, **_dispel(params, sid, "other")
    )
    assert (
        _ward(state)[1]["groups"]["other"] is False
        and _ward(state)[1]["groups"]["corridors"] is True
    )


def test_adoption_semantic_repeat_preserves_group_exception():
    text = (budget.ROOT / "runtime/ruleset.yaml").read_text()
    first = removal(source(text))
    assert yaml.load(first, Loader=yaml.CSafeLoader) == yaml.load(
        removal(source(first)), Loader=yaml.CSafeLoader
    )


def test_public_paid_ward_reopen_selected_group_removal_and_retry(tmp_path, monkeypatch):
    parent = os.environ.get("FC14_SOURCE_PACKAGE_ROOT")
    if os.environ.get("FC14_SOURCE_RUNTIME_ROOT"):
        assert parent
        monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: Path(parent))
        monkeypatch.setattr(public, "bundled_srd_archive", lifetime._composed_archive)
    monkeypatch.setattr(public, "_spell_case", _case)
    path = tmp_path / "ward.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "guards_and_wards")
        begin, command = _act(owner, "begin_srd_spell_cast", **_begin(params))
        assert begin.status is ActionStatus.COMMITTED, begin.result
        assert owner.execute_action(command) == begin
        continued, _ = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=600,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert continued.status is ActionStatus.COMMITTED, continued.result
        cast, cast_command = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = owner.snapshot("casting")
        sid, _ = _ward(saved.state)
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        ended, end_command = _act(
            owner, "cast_spell_outside_combat", **_dispel(params, sid, "doors")
        )
        assert ended.status is ActionStatus.COMMITTED, ended.result
        snapshot = owner.snapshot("casting")
        assert _ward(snapshot.state)[1]["groups"]["doors"] is False
        assert _ward(snapshot.state)[1]["groups"]["corridors"] is True
        assert owner.execute_action(cast_command) == cast and owner.snapshot("casting") == snapshot
        assert owner.execute_action(end_command) == ended and owner.snapshot("casting") == snapshot
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == snapshot
