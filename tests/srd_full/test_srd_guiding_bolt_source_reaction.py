"""Actual deferred spell consequences retain their paid source slot and identity."""

from random import Random

import pytest

from sylvia_gm import dice
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_reaction_spells import _cast_parameters
from tests.srd_full.test_srd_spell_attack_reactions import _case


@pytest.mark.parametrize("shielded", [False, True])
def test_guiding_bolt_paid_source_survives_saved_hit_and_shield(monkeypatch, shielded):
    state, data, parameters = _case("guiding_bolt")
    parameters["slot_resource"] = "spell_slot_4"
    state[f"{SRD}.entities"]["mage"].update(hp=1000, max_hp=1000)
    monkeypatch.setattr(dice, "_rng", Random(1))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _run("begin_srd_spell_action", state, participant_data=data, **parameters)
    pending = state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
    assert state[f"{SRD}.state"].get("spell_source_serial") is None
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=pending)
    assert state[f"{SRD}.state"]["spell_source_serial"] == 1
    assert not state[f"{SRD}.state"].get("turn_effects")
    if shielded:
        _run("cast_srd_reaction_spell", state, **_cast_parameters("shield", pending))
    _run("complete_srd_pending_action", state, participant_data=data, pending_action_id=pending)
    records = [r for r in state[f"{SRD}.state"].get("turn_effects", [])
               if r.get("spell") == "guiding_bolt"]
    assert bool(records) is not shielded
    if records:
        assert records[0]["cast_id"] == "srd_cast:1"
        assert records[0]["source_caster_id"] == "hero"
        assert records[0]["spell_level"] == 4
    assert state[f"{SRD}.state"]["spell_source_serial"] == 1
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
    assert state[f"{SRD}.state"]["pending_srd_action_stack"] == []
