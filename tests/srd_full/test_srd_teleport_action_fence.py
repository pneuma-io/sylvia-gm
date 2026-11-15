"""Paid instantaneous stages retain their actual pending action until arrival."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_spell_travel import SRD, _run, _teleport_case, _teleport_continue


@pytest.mark.parametrize("saved_cast", [False, True])
def test_paid_teleport_keeps_actual_action_fence_until_final_arrival(monkeypatch, saved_cast):
    state, data, params, actual_dice = _teleport_case(monkeypatch, [(100, 100)])
    if saved_cast:
        _run("begin_srd_spell_action", state, participant_data=data, **params)
        original = state[f"{SRD}.state"]["pending_srd_action_stack"][-1]
        _run("complete_srd_pending_action", state, participant_data=data,
             pending_action_id=original)
    else:
        _run("cast_spell", state, participant_data=data, **params)
    lasting = state[f"{SRD}.state"]
    record = state[f"{SRD}.entities"]["hero"]["teleport_resolution"]
    pending = record["reaction_action_id"]
    assert pending is not None
    assert lasting["pending_srd_action_stack"] == [pending]
    assert lasting["pending_srd_actions"][pending]["kind"] == "teleport_resolution"
    if saved_cast:
        assert pending == original
    before = deepcopy(state)
    for name, arguments in [
        ("advance_srd_lasting_time", {"elapsed_rounds": 1}),
        ("move_srd_scene_creature", {"actor_state": "guard", "destination": [10, 0, 0]}),
        ("complete_srd_pending_action", {"pending_action_id": pending}),
    ]:
        with pytest.raises(RulesetPreconditionRejected, match="pending|paid Teleport"):
            _run(name, state, **arguments)
        assert state == before
    _teleport_continue(state)
    assert state[f"{SRD}.state"]["pending_srd_action_stack"] == []
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
    assert actual_dice.consumed == [(100, 100)]
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
