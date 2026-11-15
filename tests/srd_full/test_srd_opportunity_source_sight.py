"""Opportunity triggers retain actual scene sight rather than supplied verdicts."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_opportunity_attacks import _reaction_case
from tests.srd_full.test_srd_spell_illumination import _case as _light_case


def _scene_reaction():
    state, data = _reaction_case()
    scene, _, _ = _light_case("light")
    state[f"{SRD}.state"] = deepcopy(scene[f"{SRD}.state"])
    for entity in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        entity["metadata"]["lighting"] = "bright"
    return state, data


def _trigger(state, data, **choices):
    return _run(
        "trigger_opportunity_attack", state, participant_data=data,
        reactor_state="hero", actor_can_see_target=True, target_can_see_actor=True,
        attack_distance=5, weapon="spear", **choices,
    )


@pytest.mark.parametrize("condition,subject", [("blinded", "hero"), ("invisible", "guard")])
def test_actual_unseen_target_rejects_opportunity_despite_supplied_visible(condition, subject):
    state, data = _scene_reaction()
    state[f"{SRD}.entities"][subject]["conditions"].append(condition)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="seeing"):
        _trigger(state, data)
    assert state == before


def test_actual_visible_target_overrides_supplied_unseen_and_saves_reverse_sight():
    state, data = _scene_reaction()
    state[f"{SRD}.entities"]["guard"]["conditions"].append("blinded")
    _run(
        "trigger_opportunity_attack", state, participant_data=data,
        reactor_state="hero", actor_can_see_target=False, target_can_see_actor=True,
        attack_distance=5, weapon="spear",
    )
    pending = state[f"{SRD}.combat"]["pending_reaction"]
    assert pending["actor_can_see_target"] is True
    assert pending["target_can_see_actor"] is False
