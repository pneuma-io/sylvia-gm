"""Quiet lifecycle receipts must retain meaningful nested saves and pending reactions."""

import json
from copy import deepcopy
from random import Random

import pytest
import yaml

from sylvia_gm import dice
from tests.test_srd_action_budget import ROOT, _run
from tests.test_srd_attack_budget import SRD, _combat_case, _npc_attack


@pytest.mark.parametrize("first_group,second_group", [(False, False), (True, False), (False, True)])
def test_finished_encounter_does_not_carry_dodge_or_old_order_into_the_next_bout(
    monkeypatch, first_group, second_group,
):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data = _combat_case()
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    entities = state[f"{SRD}.entities"]
    entities["hero"].update(name="Budget Hero", initiative_modifier=20)
    entities["guard"].update(speed=30, initiative_modifier=-10)
    entities["ally"] = deepcopy(entities["guard"])
    entities["ally"]["name"] = "Ally"
    from tests.srd_fixture_scene import fixture_scene
    state[f"{SRD}.state"]["spatial_scene"] = fixture_scene(entities)

    def start(group):
        if group:
            _run("start_group_combat_encounter", state, combatants=["hero", "guard", "ally"])
        else:
            _run("start_combat_encounter_entities", state, first_actor="hero", second_actor="guard",
                 first_surprised=False, second_surprised=False, tie_winner="first")
        _run("begin_combat_turn", state)

    start(first_group)
    assert state[f"{SRD}.combat"]["active_actor"] == "hero"
    _run("use_srd_action_surge", state, participant_data=data, actor="hero", actor_state="hero")
    _run("take_dodge_action", state, participant_data=data, actor="hero", actor_state="hero")
    assert state[f"{SRD}.entities"]["hero"]["dodging"] is True
    resources = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    _run("end_combat_encounter", state, end_reason="fighting_stopped")
    assert state[f"{SRD}.entities"]["hero"]["resources"] == resources
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 20

    # The opponent acts before the Hero gets a new turn, as in the installed Cal bout.
    state[f"{SRD}.entities"]["hero"]["initiative_modifier"] = -10
    state[f"{SRD}.entities"]["guard"]["initiative_modifier"] = 20
    start(second_group)
    assert state[f"{SRD}.combat"]["active_actor"] == "guard"
    result = _npc_attack(state, "Practice staff", use_multiattack=False).result
    assert len(result["context"]["_attack_roll"]["results"]) == 1
    assert result["context"]["_attack_roll_mode"] == "Normal"
    assert bool(state[f"{SRD}.combat"].get("group_order")) is second_group
    if not second_group:
        assert not state[f"{SRD}.combat"].get("order")


def test_encounter_end_clears_short_lived_budgets_but_keeps_lasting_effects():
    state, _ = _combat_case()
    entities = state[f"{SRD}.entities"]
    entities["bystander"] = deepcopy(entities["guard"])
    entities["bystander"].update(help_source="guard", help_attack_target="hero")
    entities["guard"].update(helped_ally_state_id="bystander", help_mode="attack")
    entities["hero"].update(
        hp=11, temp_hp=3, conditions=["poisoned"], concentrating=True,
        concentration_spell="bless", concentration_head_by_spell={"bless": "hero"},
        dodging=True, spell_slot_cast_this_turn=True, light_extra_attack_available=True,
        light_extra_attack_used=True, light_attack_source_weapons=["dagger"],
        light_attack_source_weapon="dagger", reaction_available=False,
    )
    before = deepcopy(entities["hero"])
    state[f"{SRD}.combat"].update(
        order_first="hero", order_second="guard", opportunity_attack_pending=True,
        pending_reaction={"kind": "opportunity_attack", "reacting_entity": "guard"},
        action_surge_available=True, disengaged_actor="hero", light_attack_followup_available=True,
    )
    _run("end_combat_encounter", state, end_reason="fighting_stopped")
    hero = state[f"{SRD}.entities"]["hero"]
    for field in ["hp", "temp_hp", "conditions", "concentrating", "concentration_spell",
                  "concentration_head_by_spell", "resources"]:
        assert hero[field] == before[field]
    for field in ["dodging", "spell_slot_cast_this_turn", "light_extra_attack_available",
                  "light_extra_attack_used"]:
        assert hero[field] is False
    assert hero["light_attack_source_weapons"] == []
    assert hero["light_attack_source_weapon"] is None
    assert hero["reaction_available"] is True
    assert state[f"{SRD}.entities"]["bystander"]["help_source"] is None
    assert state[f"{SRD}.entities"]["guard"]["helped_ally_state_id"] is None
    combat = state[f"{SRD}.combat"]
    assert combat["pending_reaction"] is None
    assert combat["opportunity_attack_pending"] is False
    assert combat["action_surge_available"] is False
    assert combat["light_attack_followup_available"] is False
    assert combat["movement_remaining"] == 0


def test_every_public_operation_declares_a_result_or_routine_activity_disposition():
    runtime = yaml.safe_load((ROOT / "runtime/ruleset.yaml").read_text(encoding="utf-8"))
    record = json.loads((ROOT / "package-record.json").read_text(encoding="utf-8"))
    for tool in record["tools"]:
        metadata = runtime["tools"][tool["name"]]["metadata"]
        # Explicit GM-only visibility is a declared disposition, not a player receipt.
        assert (metadata.get("visible_to_player") is False or
                metadata.get("result_card") or metadata.get("activity_label") or
                "x-sylvia-gm-character-update" in tool["input_schema"]), tool["name"]


@pytest.mark.parametrize("group,sources,expire", [
    (False, 0, False), (True, 0, False), (False, 2, False), (True, 2, False),
    (False, 2, True),
])
def test_end_turn_shows_each_repeating_save_once_and_advance_never_rolls_again(
    monkeypatch, group, sources, expire,
):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data = _combat_case()
    combat = state[f"{SRD}.combat"]
    combat.update(encounter_active=False, turn_active=False)
    entities = state[f"{SRD}.entities"]
    entities["hero"].update(name="Budget Hero", initiative_modifier=20, exhaustion_level=2,
                            saving_throw_modifiers={"constitution": 0})
    entities["guard"].update(initiative_modifier=0)
    if group:
        entities["ally"] = deepcopy(entities["guard"])
        entities["ally"]["name"] = "Ally"
        from tests.srd_fixture_scene import fixture_scene
        state[f"{SRD}.state"]["spatial_scene"] = fixture_scene(entities)
        _run("start_group_combat_encounter", state, combatants=["hero", "guard", "ally"])
    else:
        _run("start_combat_encounter_entities", state, first_actor="hero", second_actor="guard",
             first_surprised=False, second_surprised=False, tie_winner="first")
    begin = _run("begin_combat_turn", state)
    assert begin.result.get("result_card") is None
    assert begin.result.get("activity_label") == "Start combat turn"
    monkeypatch.setattr(dice, "_rng", Random(0))
    for condition, dc in [("blinded", 1), ("deafened", 40)][:sources]:
        _run("install_srd_timed_condition_source", state, target_state="hero",
             lasting_state="state", spell="blindness_deafness", condition=condition,
             save_dc=dc, duration_rounds=1 if expire else 10,
             repeat_save_timing="end_of_target_turn")
    if expire:
        _run("advance_srd_lasting_clock", state, lasting_state="state", elapsed_ticks=1)
    end = _run("end_combat_turn", state)
    card = end.result.get("result_card")
    assert end.result.get("activity_label") == "End combat turn"
    after_end = deepcopy(state[f"{SRD}.entities"]["hero"]["conditions"])
    rng_after_end = dice._rng.getstate()
    result = _run("advance_group_combat_encounter_turn" if group else
                  "advance_combat_encounter_turn", state, participant_data=data).result
    assert dice._rng.getstate() == rng_after_end
    assert result.get("activity_label") == "Advance combat turn"
    assert result.get("result_card") is None
    if not sources:
        assert card is None
        return
    assert card is not None
    assert card["title"] == "End of turn"
    summary = {row["label"]: row["value"] for row in card["summary"]}
    assert summary["Creature"] == "Budget Hero"
    assert summary["Conditions after"] == after_end == ["deafened"]
    assert state[f"{SRD}.entities"]["hero"]["conditions"] == ([] if expire else ["deafened"])
    calculations = card["details"]
    equations = [row["equation"] for row in card["details"] if "equation" in row]
    assert len(calculations) == len(equations) == sources
    for index, (condition, dc, outcome) in enumerate([
        ("blinded", 1, "success"), ("deafened", 40, "failure"),
    ]):
        row = calculations[index]
        roll = end.result["context"]["_timed_condition_end_turn_rolls"][index]
        assert row["label"] == "Budget Hero · Blindness/Deafness"
        assert "constitution saving throw" in row["value"]
        assert condition in row["value"]
        assert equations[index]["die_sides"] == 20
        assert equations[index]["roll"] == roll["total"]
        assert equations[index]["modifier"] == -4
        assert equations[index]["total"] == roll["total"] - 4
        assert f"DC {dc}" in row["value"]
        assert outcome in row["value"]
        assert ("source ended" if outcome == "success" else "source remains") in row["value"]
    saved = deepcopy(card)
    state[f"{SRD}.entities"]["hero"].update(name="Changed", conditions=[])
    assert saved == card


@pytest.mark.parametrize("group", [False, True])
def test_automatic_encounter_ending_is_meaningful_and_never_folded_into_routine_activity(group):
    state, data = _combat_case()
    combat = state[f"{SRD}.combat"]
    combat.update(encounter_active=False, turn_active=False)
    entities = state[f"{SRD}.entities"]
    entities["hero"].update(name="Budget Hero", initiative_modifier=20)
    entities["guard"].update(initiative_modifier=0)
    if group:
        entities["ally"] = deepcopy(entities["guard"])
        entities["ally"]["name"] = "Ally"
        from tests.srd_fixture_scene import fixture_scene
        state[f"{SRD}.state"]["spatial_scene"] = fixture_scene(entities)
        _run("start_group_combat_encounter", state, combatants=["hero", "guard", "ally"])
    else:
        _run("start_combat_encounter_entities", state, first_actor="hero", second_actor="guard",
             first_surprised=False, second_surprised=False, tie_winner="first")
    _run("begin_combat_turn", state)
    entities = state[f"{SRD}.entities"]
    entities["guard"].update(hp=0, is_alive=False)
    if group:
        entities["hero"].update(hp=0, is_alive=False)
        entities["ally"].update(hp=0, is_alive=False)
    _run("end_combat_turn", state)
    result = _run("advance_group_combat_encounter_turn" if group else
                  "advance_combat_encounter_turn", state, participant_data=data).result
    assert state[f"{SRD}.combat"]["encounter_active"] is False
    assert result.get("activity_label") is None
    assert result["result_card"]["title"] == "Encounter ended"
    assert result["result_card"]["details"] == []


def test_pending_opportunity_card_does_not_claim_an_attack_or_spent_reaction():
    state, data = _combat_case()
    state[f"{SRD}.entities"]["hero"]["name"] = "Budget Hero"
    state[f"{SRD}.entities"]["guard"].update(
        available_reactions=["opportunity_attack"], reaction_available=True, melee_reach=5,
    )
    result = _run("trigger_opportunity_attack", state, participant_data=data,
                  reactor_state="guard", actor_can_see_target=True, target_can_see_actor=True,
                  attack_distance=5, attack_modifier=3, damage_dice_count=1, damage_die_size="6",
                  damage_modifier=1, damage_type="piercing").result
    card = result.get("result_card")
    assert card is not None
    assert card["title"] == "Opportunity Attack available"
    assert {row["label"]: row["value"] for row in card["summary"]} == {
        "Actor": "Guard", "Target": "Budget Hero", "Outcome": "Awaiting Reaction choice",
    }
    assert "equation" not in card
    assert state[f"{SRD}.entities"]["guard"]["reaction_available"] is True
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 20
