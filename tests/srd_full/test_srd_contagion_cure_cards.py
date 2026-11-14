"""Paid named cures report the actual Contagion condition-removal result."""

import pytest

from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_progressive_afflictions import _add_spell, _case, _cast, _source
from tests.srd_full.test_srd_spell_result_cards import SRD


@pytest.mark.parametrize("modifier,cured", [(-100, False), (100, True)])
def test_lesser_restoration_card_reports_actual_contagion_cure_save(monkeypatch, modifier, cured):
    state, data, params = _case(monkeypatch, "contagion")
    _cast(state, data, params)
    source_id, _ = _source(state)
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["constitution"] = modifier
    cure_data, cure = _add_spell(state, "lesser_restoration")
    result = _run(
        "cast_spell_outside_combat",
        state,
        participant_data=cure_data,
        **(cure | {"selected_restoration_condition": "poisoned"}),
    ).result
    target = state[f"{SRD}.entities"]["guard"]
    assert ("poisoned" not in target["conditions"]) is cured
    assert state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"] is not cured
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
    rows = result["result_card"]["details"]
    assert len(rows) == 1
    assert ("poisoned" in rows[0]["value"].lower()) is cured
