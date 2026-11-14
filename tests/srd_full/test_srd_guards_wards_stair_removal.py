"""Selected stair Dispel checks the real ward Web once and retires only its parent group."""

import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_guards_wards_source_adoption as ward
from tests import test_srd_public_spell_casting as public
from tests.srd_full.test_srd_npc_spell_casting import _act

budget = ward.budget
SRD = ward.SRD


@pytest.fixture(autouse=True)
def _compiled_owner(monkeypatch):
    yield from ward.lifetime._compiled_owner.__wrapped__(monkeypatch)


def _case():
    state, data, params = ward._case()
    params["guards_wards_stairs"] = [
        {"min": [2, 3, 0], "max": [12, 8, 15]},
        {"min": [20, 3, 0], "max": [30, 8, 15]},
    ]
    return state, data, params


def _sections(state, source_id):
    lasting = state[f"{SRD}.state"]
    source = lasting["spell_guards_wards_sources"][source_id]
    return [lasting["spell_guarded_web_sections"][sid] for sid in source["web_section_ids"]]


def test_actual_stair_sections_require_real_point_and_failed_lower_slot_preserves_them(monkeypatch):
    state, data, params = _case()
    ward._cast(state, data, params)
    source_id, record = ward._ward(state)
    assert record["save_dc"] == 18 and len(_sections(state, source_id)) == 2
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="stair Web scenery"):
        budget._run(
            "cast_spell_outside_combat",
            state,
            participant_data=data,
            **(ward._dispel(params, source_id, "stairs") | {"recovery_position": [45, 45, 5]}),
        )
    assert state == before
    monkeypatch.setattr(dice, "_rng", Random(2))
    expected = Random(2)
    assert expected.randint(1, 20) == 2
    result = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **ward._dispel(params, source_id, "stairs", slot=3),
    )
    assert "DC 16" in str(result.result["result_card"])
    assert dice._rng.getstate() == expected.getstate()
    assert all(section["active"] for section in _sections(state, source_id))
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2


def test_actual_paid_overlap_removes_one_parent_stair_group_and_preserves_all_other_effects():
    state, data, params = _case()
    ward._cast(state, data, params)
    first, _ = ward._ward(state)
    ward._cast(state, data, params)
    second, _ = ward._ward(state)
    assert first != second
    first_locks = deepcopy(ward._locks(state))
    budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **ward._dispel(params, first, "stairs"),
    )
    lasting = state[f"{SRD}.state"]
    assert lasting["spell_guards_wards_sources"][first]["groups"] == {
        "corridors": True,
        "doors": True,
        "stairs": False,
        "other": True,
    }
    assert lasting["spell_guards_wards_sources"][second]["groups"]["stairs"] is True
    assert not any(section["active"] for section in _sections(state, first))
    assert all(section["active"] for section in _sections(state, second))
    assert ward._locks(state) == first_locks
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="already ended"):
        budget._run(
            "cast_spell_outside_combat",
            state,
            participant_data=data,
            **ward._dispel(params, first, "stairs", slot=3),
        )
    assert state == before


def test_public_real_ward_web_group_dispel_reopen_retry(tmp_path, monkeypatch):
    parent = os.environ.get("FC14_SOURCE_PACKAGE_ROOT")
    if os.environ.get("FC14_SOURCE_RUNTIME_ROOT"):
        assert parent
        monkeypatch.setattr(bundled_packages, "_bundled_srd_root", lambda: Path(parent))
        monkeypatch.setattr(public, "bundled_srd_archive", ward.lifetime._composed_archive)
    monkeypatch.setattr(public, "_spell_case", lambda _spell: _case())
    path = tmp_path / "stairs.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "guards_and_wards")
        begin, _ = _act(owner, "begin_srd_spell_cast", **ward._begin(params))
        assert begin.status is ActionStatus.COMMITTED, begin.result
        progressed, _ = _act(
            owner,
            "continue_srd_spell_cast",
            caster_state="hero",
            elapsed_rounds=600,
            uninterrupted=True,
            verbal_component_available=True,
            somatic_component_available=True,
            material_component_available=True,
        )
        assert progressed.status is ActionStatus.COMMITTED, progressed.result
        cast, cast_command = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        saved = owner.snapshot("casting")
        source_id, _ = ward._ward(saved.state)
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        ended, end_command = _act(
            owner, "cast_spell_outside_combat", **ward._dispel(params, source_id, "stairs")
        )
        assert ended.status is ActionStatus.COMMITTED, ended.result
        final = owner.snapshot("casting")
        assert not any(section["active"] for section in _sections(final.state, source_id))
        assert ward._ward(final.state)[1]["groups"]["doors"] is True
        assert owner.execute_action(cast_command) == cast and owner.snapshot("casting") == final
        assert owner.execute_action(end_command) == ended and owner.snapshot("casting") == final
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
