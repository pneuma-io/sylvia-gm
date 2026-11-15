"""Starry Wisp's actual hit retains source light and suppression through the owning turn."""

from copy import deepcopy

import pytest

from sylvia_gm import dice
from tests import test_srd_action_budget as budget
from tests.srd_full.test_srd_minor_attack_packets import (
    actual_receipt_owner as actual_receipt_owner,
)
from tests.srd_full.test_srd_minor_attack_packets import (
    receipt_runtime as receipt_runtime,
)
from tests.srd_full.test_srd_spell_result_cards import SRD
from tests.srd_full.test_srd_spell_wave3_information import _case


class ActualStarryFaces:
    def __init__(self, hit=True):
        self.hit = hit

    def randint(self, low, high):
        assert low == 1
        return (10 if self.hit else 1) if high == 20 else 1


def _starry_case():
    state, data, parameters = _case("starry_wisp")
    data["classes"] = [{"id": "druid", "level": 17}]
    data["spellcasting_grants"] = [
        {"source": "druid", "ability": "wisdom", "spells": ["starry_wisp"]}
    ]
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(hp=1000, max_hp=1000, temp_hp=1000, armor_class=1, conditions=["invisible"])
    return state, data, parameters


def _suppression(state):
    return budget._run("check_srd_illumination_subject", state, subject="guard").result["context"]


@pytest.mark.parametrize("hit,immune", [(True, False), (True, True), (False, False)])
def test_actual_starry_hit_installs_dim_light_and_suppresses_invisible_even_when_immune(
    monkeypatch, hit, immune
):
    state, data, parameters = _starry_case()
    state[f"{SRD}.entities"]["guard"]["damage_immunities"] = ["radiant"] if immune else []
    monkeypatch.setattr(dice, "_rng", ActualStarryFaces(hit))
    result = budget._run(
        "cast_spell_outside_combat", state, participant_data=data, **parameters
    ).result
    records = [
        r for r in state[f"{SRD}.state"].get("turn_effects", []) if r.get("spell") == "starry_wisp"
    ]
    assert len(records) == int(hit)
    source = _suppression(state)
    assert source["_illumination_suppressed"] is hit
    assert source["_illumination_faerie"] is False
    light = budget._run(
        "resolve_srd_information_light",
        state,
        point=[19, 0, 0],
        plane="material",
        ambient="darkness",
    ).result["context"]
    assert light["_information_light"] == ("dim" if hit else "darkness")
    if immune or not hit:
        assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == 1000
    else:
        assert state[f"{SRD}.entities"]["guard"]["temp_hp"] < 1000
    assert result["status"] == "committed"


def test_actual_starry_source_survives_own_next_start_and_ends_at_own_next_end(monkeypatch):
    state, data, parameters = _starry_case()
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False)
    budget._run("begin_combat_turn", state, participant_data=data, actor="hero")
    monkeypatch.setattr(dice, "_rng", ActualStarryFaces())
    budget._run("cast_spell", state, participant_data=data, **parameters)
    installed = deepcopy(
        [r for r in state[f"{SRD}.state"]["turn_effects"] if r.get("spell") == "starry_wisp"]
    )
    assert len(installed) == 1
    budget._run("end_combat_turn", state, participant_data=data, actor="hero")
    assert _suppression(state)["_illumination_suppressed"] is True
    budget._run("begin_combat_turn", state, participant_data=data, actor="hero")
    assert _suppression(state)["_illumination_suppressed"] is True
    budget._run("end_combat_turn", state, participant_data=data, actor="hero")
    assert _suppression(state)["_illumination_suppressed"] is False
    assert not any(r.get("spell") == "starry_wisp" for r in state[f"{SRD}.state"]["turn_effects"])
    assert "invisible" in state[f"{SRD}.entities"]["guard"]["conditions"]
