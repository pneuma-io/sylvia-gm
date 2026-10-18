"""Combat cards describe the committed effect, not just successful tool execution."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from tests.test_srd_action_budget import _run, _state, _surge
from tests.test_srd_attack_budget import SRD, _attack, _combat_case, _npc_attack


def _rows(card, section="summary"):
    return {row["label"]: row["value"] for row in card[section]}


@pytest.mark.parametrize("kind", ["character", "npc"])
@pytest.mark.parametrize("outcome", ["miss", "resisted", "immune", "critical_hit"])
def test_attack_card_preserves_actual_damage_and_explains_modifiers(monkeypatch, kind, outcome):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data = _combat_case()
    actor_id, target_id = ("hero", "guard") if kind == "character" else ("guard", "hero")
    state[f"{SRD}.combat"].update(active_actor=actor_id, current_actor=actor_id)
    actor = state[f"{SRD}.entities"][actor_id]
    target = state[f"{SRD}.entities"][target_id]
    actor.update(name="Budget Hero" if kind == "character" else "Guard", exhaustion_level=1)
    target.update(name="Guard" if kind == "character" else "Budget Hero",
                  hp=1000, max_hp=1000, armor_class=99 if outcome == "miss" else 1,
                  temp_hp=1, conditions=["unconscious"] if outcome == "critical_hit" else [])
    target["damage_resistances"] = ["piercing"] if outcome == "resisted" else []
    target["damage_immunities"] = ["piercing"] if outcome == "immune" else []
    before = deepcopy(target)
    receipt = (_attack(state, data, circumstantial_modifier=2) if kind == "character" else
               _npc_attack(state, "Spear", use_multiattack=False, damage_expression="d6",
                           damage_modifier=3))
    context = receipt.result["context"]
    card = receipt.result["result_card"]
    summary, details = _rows(card), _rows(card, "details")
    after = state[f"{SRD}.entities"][target_id]
    hp_lost = before["hp"] - after["hp"]
    absorbed = before["temp_hp"] - after["temp_hp"]
    assert summary["Damage"] == hp_lost + absorbed
    assert summary["Actor"] == actor["name"]
    assert summary["Target"] == before["name"]
    assert summary["Weapon"] == "Spear"
    expected = {"miss": "Miss", "critical_hit": "Critical hit"}.get(outcome, "Hit")
    assert summary["Outcome"] == expected
    assert details.get("HP lost", 0) == hp_lost
    assert details.get("Temporary HP absorbed", 0) == absorbed
    assert details["Exhaustion"] == -2
    assert details["Roll mode"] == ("Advantage" if outcome == "critical_hit" else "Normal")
    modifier_terms = ["Ability modifier", "Proficiency", "Circumstance", "Stat block modifier",
                      "Exhaustion", "Ongoing effects"]
    assert sum(details.get(label, 0) for label in modifier_terms) == card["equation"]["modifier"]
    assert card["equation"]["total"] == context["_attack_adjusted_total"]
    assert "Armor Class" not in details
    if outcome == "miss":
        assert "Damage dice" not in details
    saved = deepcopy(card)
    after["hp"] = 42
    assert card == saved


def test_action_surge_card_reports_granted_action_and_charge_change():
    state = _state()
    state[f"{SRD}.entities"]["hero"]["resource_maxima"] = {"action_surge": 2}
    receipt = _surge(state)
    card = receipt.result.get("result_card")
    assert card is not None
    assert card["title"] == "Action Surge"
    summary, details = _rows(card), _rows(card, "details")
    assert summary["Effect"] == "One additional non-Magic action this turn"
    assert summary["Uses remaining"] == 1
    assert details["Uses before"] == 2
    assert details["Uses spent"] == 1
    assert details["Maximum uses"] == 2
    assert state[f"{SRD}.combat"]["action_surge_available"] is True


@pytest.mark.parametrize("hp,healed", [(19, 1), (20, 0)])
def test_second_wind_card_distinguishes_healing_roll_from_actual_recovery(monkeypatch, hp, healed):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state = _state()
    state[f"{SRD}.combat"]["encounter_active"] = False
    state[f"{SRD}.entities"]["hero"].update(
        hp=hp, fighter_level=2, resources={"second_wind": 2}, resource_maxima={"second_wind": 2},
    )
    receipt = _run("use_srd_second_wind", state, actor="hero", actor_state="hero")
    card = receipt.result.get("result_card")
    assert card is not None
    assert card["title"] == "Second Wind"
    summary, details = _rows(card), _rows(card, "details")
    assert summary["HP restored"] == healed
    assert summary["Uses remaining"] == 1
    assert details["HP before"] == hp
    assert details["HP after"] == 20
    assert details["Uses spent"] == 1
    assert card["equation"]["total"] == receipt.result["context"]["_second_wind_healing"]


@pytest.mark.parametrize("restore", [False, True])
def test_resource_card_records_actual_change_with_capped_recovery(restore):
    state = _state()
    state[f"{SRD}.entities"]["hero"].update(
        name="Budget Hero", resources={"spell_slot_1": 2}, resource_maxima={"spell_slot_1": 3},
    )
    receipt = _run("restore_srd_resource" if restore else "spend_srd_resource", state,
                   target="hero", resource="spell_slot_1", amount=2)
    card = receipt.result.get("result_card")
    assert card is not None
    summary, details = _rows(card), _rows(card, "details")
    assert summary["Resource"] == "Level 1 spell slot"
    assert summary["Recovered" if restore else "Spent"] == (1 if restore else 2)
    assert summary["Remaining"] == (3 if restore else 0)
    assert details["Before"] == 2
    assert details["Maximum"] == 3
    assert summary["Effect"] == "Resource balance updated"
