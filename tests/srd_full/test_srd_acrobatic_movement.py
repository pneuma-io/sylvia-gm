"""Acrobatic Movement spends ordinary movement without granting a lasting fly/swim mode."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_unarmed_creatures import _case


def _monk():
    state, data = _case()
    data.update(classes=[{"id": "monk", "level": 9}], level=9)
    state[f"{SRD}.combat"].update(movement_remaining=45, movement_spent=0)
    state[f"{SRD}.entities"]["hero"].update(worn_armor=None, worn_shield=None)
    return state, data


@pytest.mark.parametrize("kind", ["vertical_surface", "liquid_surface"])
@pytest.mark.parametrize("difficult", [False, True])
def test_surface_movement_spends_only_its_actual_movement_cost(kind, difficult):
    state, data = _monk()
    result = _run("spend_combat_movement", state, participant_data=data,
                  movement_kind=kind, distance=15, difficult_terrain=difficult)
    combat = state[f"{SRD}.combat"]
    assert combat["movement_remaining"] == 45 - (30 if difficult else 15)
    assert combat["movement_spent"] == (30 if difficult else 15)
    assert combat["action_available"] is True
    assert state[f"{SRD}.entities"]["hero"]["bonus_action_available"] is True
    assert "Acrobatic Movement" in str(result.result["result_card"])
    assert result.result["context"]["_jump_maximum"] is None


@pytest.mark.parametrize("problem", ["level", "armor", "shield", "other_turn", "budget"])
def test_surface_movement_rejects_without_earned_feature_and_available_movement(problem):
    state, data = _monk()
    if problem == "level":
        data["classes"][0]["level"] = 8
    elif problem == "armor":
        state[f"{SRD}.entities"]["hero"]["worn_armor"] = "leather_armor"
    elif problem == "shield":
        state[f"{SRD}.entities"]["hero"]["worn_shield"] = "shield"
    elif problem == "other_turn":
        state[f"{SRD}.combat"]["active_actor"] = "guard"
    else:
        state[f"{SRD}.combat"]["movement_remaining"] = 10
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("spend_combat_movement", state, participant_data=data,
             movement_kind="vertical_surface", distance=15, difficult_terrain=False)
    assert state == before
