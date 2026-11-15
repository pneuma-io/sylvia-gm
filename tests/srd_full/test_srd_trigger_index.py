"""Actual generic expiry cannot leave the saved ward index active without any source."""

import json

import pytest
from scripts import srd_spell_triggers as repair

from tests import test_srd_action_budget, test_srd_spell_result_cards
from tests import test_srd_spell_triggers as owner
from tests import test_srd_trigger_radial_cover as radial
from tests.srd_full.test_srd_action_budget import SRD, _run


@pytest.fixture(autouse=True)
def _actual_owner(monkeypatch):
    radial._runtime.cache_clear()
    radial._mechanic.cache_clear()
    monkeypatch.setattr(radial, "repair", repair)
    monkeypatch.setattr(owner, "PROFILES", repair.PROFILES)
    monkeypatch.setattr(owner, "CHOICES", repair.CHOICES)
    monkeypatch.setattr(owner, "_schema", repair._schema)
    monkeypatch.setattr(owner, "_runtime", radial._runtime)
    monkeypatch.setattr(owner, "_mechanic", radial._mechanic)
    monkeypatch.setattr(test_srd_action_budget, "_mechanic", radial._mechanic)
    monkeypatch.setattr(
        test_srd_spell_result_cards,
        "_spell_definitions",
        lambda: radial._runtime()[0]["definitions"]["spells"],
    )
    yield
    radial._runtime.cache_clear()
    radial._mechanic.cache_clear()


def test_actual_alarm_paid_clock_boundary_clears_index_after_generic_source_retirement():
    state, _, _, identity = owner._cast("alarm")
    assert state[f"{SRD}.state"]["lasting_tick"] == 20
    assert owner._source(state)["expires_tick"] == 9620
    state = json.loads(json.dumps(state))
    _run("advance_srd_lasting_time", state, elapsed_rounds=4799)
    assert state[f"{SRD}.state"]["lasting_tick"] == 9618
    assert owner._source(state)["instance"] == identity
    assert state[f"{SRD}.state"]["has_spell_triggers"] is True
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert not any(s.get("kind") == "spell_trigger" for s in state[f"{SRD}.state"]["turn_effects"])
    assert state[f"{SRD}.state"]["has_spell_triggers"] is False
    after = json.loads(json.dumps(state))
    _run("refresh_srd_spell_triggers", state)
    assert state == after
