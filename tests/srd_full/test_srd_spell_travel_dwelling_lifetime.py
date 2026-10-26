"""Mansion lifetime adapters retain actual list identity and leave physical returns queued."""

from copy import deepcopy

import pytest

from sylvia_gm.application.package_runtime import PackageRuntimeError
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_travel_dwelling as dwelling
from tests import test_srd_spell_travel_rope as rope
from tests.srd_full.test_srd_spell_travel import SRD
from tests.srd_full.test_srd_spell_travel_dwelling import _install, _source
from tests.srd_full.test_srd_spell_travel_telekinesis import _tk_case


@pytest.fixture
def dwelling_scope(monkeypatch):
    yield from dwelling.dwelling_scope.__wrapped__(monkeypatch)


@pytest.fixture
def rope_lifetime_scope(monkeypatch):
    monkeypatch.setenv("FC14_ROPE_RUNTIME", ".scratch/dwelling-lifetime-rope/runtime.yaml")
    yield from rope.rope_scope.__wrapped__(monkeypatch)


def test_real_mansion_source_reader_and_raw_retirement_preserve_position(dwelling_scope):
    state, _ = _tk_case()
    _install(state)
    source = deepcopy(_source(state))
    actual_scene = deepcopy(state[f"{SRD}.state"]["spatial_scene"])
    result = budget._run("read_srd_lifetime_dwelling_source", state, source_id=source["id"])
    assert result.result["context"]["_lifetime_record"] == source
    assert result.result["context"]["_lifetime_active"] is True
    budget._run("retire_srd_owned_dwelling_source", state, source_id=source["id"])
    assert _source(state)["active"] is False and _source(state)["return_pending"] is True
    assert state[f"{SRD}.state"]["spatial_scene"] == actual_scene
    after = deepcopy(state)
    budget._run("retire_srd_owned_dwelling_source", state, source_id=source["id"])
    assert state == after


def test_real_source_doors_select_one_spell_and_unrelated_objects_select_none(dwelling_scope):
    state, _ = _tk_case()
    _install(state)
    source = deepcopy(_source(state))
    for subject in (source["outer_id"], source["inner_id"], "orb"):
        result = budget._run("collect_srd_dwelling_dispel_sources", state, subject=subject)
        assert result.result["context"]["_dwelling_dispel_source_ids"] == (
            [source["id"]] if subject != "orb" else []
        )
    budget._run("retire_srd_owned_dwelling_source", state, source_id=source["id"])
    result = budget._run("collect_srd_dwelling_dispel_sources", state, subject=source["outer_id"])
    assert result.result["context"]["_dwelling_dispel_source_ids"] == []


def test_legacy_source_cannot_be_grouped_as_an_allocated_finite_cast(dwelling_scope):
    state, _ = _tk_case()
    _install(state)
    assert _source(state)["cast_id"] == ""
    result = budget._run(
        "collect_srd_dwelling_cast_sources", state, caster_state="hero", cast_id="unallocated"
    )
    assert result.result["context"]["_dwelling_cast_source_ids"] == []
    before = deepcopy(state)
    with pytest.raises((RulesetPreconditionRejected, PackageRuntimeError)):
        budget._run("collect_srd_dwelling_cast_sources", state, caster_state="hero", cast_id="")
    assert state == before


def test_actual_rope_reader_entrance_selector_and_retirement_preserve_physical_return(
    rope_lifetime_scope,
):
    state, _ = rope._rope_case()
    rope._install(state)
    source = deepcopy(rope._source(state))
    before = deepcopy(state[f"{SRD}.state"]["spatial_scene"])
    result = budget._run("read_srd_lifetime_dwelling_source", state, source_id=source["id"])
    assert result.result["context"]["_lifetime_record"] == source
    assert result.result["context"]["_lifetime_active"] is True
    for subject in (source["outer_id"], source["inner_id"], "orb"):
        result = budget._run("collect_srd_dwelling_dispel_sources", state, subject=subject)
        assert result.result["context"]["_dwelling_dispel_source_ids"] == (
            [source["id"]] if subject != "orb" else []
        )
    result = budget._run(
        "collect_srd_dwelling_cast_sources", state, caster_state="hero", cast_id="unallocated"
    )
    assert result.result["context"]["_dwelling_cast_source_ids"] == []
    budget._run("retire_srd_owned_dwelling_source", state, source_id=source["id"])
    assert rope._source(state)["active"] is False
    assert rope._source(state)["return_pending"] is True
    assert state[f"{SRD}.state"]["spatial_scene"] == before
    after = deepcopy(state)
    budget._run("retire_srd_owned_dwelling_source", state, source_id=source["id"])
    assert state == after
