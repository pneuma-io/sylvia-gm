"""Printed Cylinder origins qualify real top/bottom creation before casting payment."""

from copy import deepcopy
from pathlib import Path

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_conjured_presences as presence
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests.srd_full.test_srd_spell_travel import SRD, _TravelDice, dice

actual_fall_scope = presence.actual_fall_scope
conjured_scope = presence.conjured_scope


@pytest.fixture
def origin_scope(conjured_scope, monkeypatch):
    monkeypatch.setattr(
        fall, "RUNTIME_PATH", Path(".scratch/celestial-lights-composition/origin-runtime.yaml")
    )


@pytest.mark.parametrize("face,height", [("bottom", 0), ("top", 40)])
def test_paid_circular_origin_faces_install_same_actual_forty_foot_height(
    origin_scope, actual_fall_scope, monkeypatch, face, height
):
    state, data, params = presence._case(celestial_origin_face=face, conjured_point=[5, 0, height])
    state[f"{SRD}.combat"].update(
        encounter_active=True, turn_active=True, active_actor="hero", phase="turn_active"
    )
    game = _TravelDice([(12, 2)] * 6 + [(12, 3)] * 4 + [(20, 10)])
    monkeypatch.setattr(dice, "_rng", game)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    budget._run("cast_spell", state, participant_data=data, **params)
    source = presence._source(state)
    assert source["origin"] == [5, 0, height]
    assert source["cylinder_origin_face"] == face
    assert source["cylinder_min"] == 0 and source["cylinder_max"] == 40
    assert source["cast_id"] == "srd_cast:1" and source["source_caster_id"] == "hero"
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 494 and not game.rolls


@pytest.mark.parametrize("defect", ["midpoint", "range", "map_top"])
def test_actual_origin_range_full_height_and_recipients_reject_atomically_before_payment(
    origin_scope, actual_fall_scope, defect
):
    choices = {
        "midpoint": {"conjured_point": [5, 0, 20]},
        "range": {"conjured_point": [91, 0, 0]},
        "map_top": {"conjured_point": [5, 0, 29990]},
    }[defect]
    state, data, params = presence._case(**choices)
    if defect == "map_top":
        scene = state[f"{SRD}.state"]["spatial_scene"]
        next(body for body in scene["entities"] if body["id"] == "hero")["position"] = [0, 0, 29990]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        budget._run("cast_spell", state, participant_data=data, **params)
    assert state == before
