"""Ordinary paid marks reuse factual range and sight without a coordinate scene."""

from copy import deepcopy

import pytest
from tests import test_srd_spell_damage_riders as marks
from tests.srd_full.test_srd_spell_damage_riders import _owners as _owners
from tests.srd_full.test_srd_spell_result_cards import SRD

from sylvia_gm.domain.errors import RulesetPreconditionRejected


def _ordinary_hex():
    state, data, params = marks._case("hex")
    state[f"{SRD}.state"].pop("spatial_scene")
    params.update(target_distance=30, caster_can_see_target=True, caster_can_touch_target=False)
    return state, data, params


def test_prepared_paid_hex_uses_measured_thirty_feet_without_coordinate_scene():
    state, data, params = _ordinary_hex()
    caster = state[f"{SRD}.entities"]["hero"]
    assert "hex" in caster["prepared_spells"]
    before = caster["resources"]["spell_slot_1"]
    source_id = marks._cast(state, data, params)
    caster = state[f"{SRD}.entities"]["hero"]
    source = state[f"{SRD}.state"]["attack_rider_sources"][source_id]
    assert source["spell"] == "hex" and source["target"] == "guard"
    assert source["cast_id"] == caster["last_spell_source_cast_id"]
    assert source["slot_level"] == 1 and source["installed"] is True
    assert caster["resources"]["spell_slot_1"] == before - 1
    assert caster["bonus_action_available"] is False
    assert "spatial_scene" not in state[f"{SRD}.state"]


@pytest.mark.parametrize("distance,seen", [(91, True), (30, False)])
def test_ordinary_mark_range_or_sight_rejection_keeps_costs_and_source_unchanged(distance, seen):
    state, data, params = _ordinary_hex()
    params.update(target_distance=distance, caster_can_see_target=seen)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="visible.*90"):
        marks._cast(state, data, params)
    assert state == before
    assert state[f"{SRD}.state"].get("attack_rider_sources") is None
