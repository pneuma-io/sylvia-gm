"""Sphere scene IDs stay scalar when the same creature is an admitted cast target."""

import pytest

from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_barriers import _setup
from tests.srd_full.test_srd_spell_result_cards import SRD


@pytest.mark.parametrize("explicit_subject", [True, False])
def test_sphere_paid_creature_cast_normalizes_explicit_and_fallback_scene_id(explicit_subject):
    state, data, parameters = _setup("resilient_sphere")
    parameters.update(target="guard", target_willing=True)
    if explicit_subject:
        parameters["barrier_subject"] = "guard"
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    source = next(iter(state[f"{SRD}.state"]["spell_barrier_sources"].values()))
    assert source["installed"]
    assert source["subject_id"] == "guard" and source["target_state_id"] == "guard"
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
