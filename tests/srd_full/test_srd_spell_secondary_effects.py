"""Spell consequences must affect later actions, not stop at the initial damage roll."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case


@pytest.mark.parametrize("spell,sign", [("bless", 1), ("bane", -1), ("enhance_ability", 0)])
def test_self_cast_d20_effect_applies_to_later_roll_and_leaves_on_concentration_end(
    monkeypatch, spell, sign,
):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case(spell)
    state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"] = dict.fromkeys(
        data["abilities"], 0,
    )
    parameters.update(targets=["hero", "guard"])
    if spell == "bane":
        parameters["forego_save_targets"] = ["hero", "guard"]
    if spell == "enhance_ability":
        parameters.update(strength_targets=["hero", "guard"], slot_resource="spell_slot_3")
    kind = "ability_check" if spell == "enhance_ability" else "saving_throw"
    _run("cast_spell", state, participant_data=data, **parameters)
    for actor in ("hero", "guard"):
        result = _run(
            "d20_test", state, actor_state=actor, ability="strength", kind=kind,
            ability_modifier=0, proficiency_bonus=0, proficiency_factor="none",
            circumstantial_modifier=0, target_number=10, advantage=False, disadvantage=False,
            forego_save=False,
        ).result
        if spell == "enhance_ability":
            assert result["context"]["_effective_advantage"] is True
            assert len(result["context"]["_d20_test"]["results"]) == 2
        else:
            assert 1 <= result["context"]["_combined_modifier"] * sign <= 4
    _run("end_concentration", state, caster_state="hero")
    for actor in ("hero", "guard"):
        assert state[f"{SRD}.entities"][actor]["concentration_d20_modifier_source_ids"] == []
        result = _run(
            "d20_test", state, actor_state=actor, ability="strength", kind=kind,
            ability_modifier=0, proficiency_bonus=0, proficiency_factor="none",
            circumstantial_modifier=0, target_number=10, advantage=False, disadvantage=False,
            forego_save=False,
        ).result
        assert result["context"]["_combined_modifier"] == 0
        assert result["context"]["_effective_advantage"] is False


def _opportunity(state):
    return _run(
        "trigger_opportunity_attack",
        state,
        reactor_state="guard",
        actor_can_see_target=True,
        target_can_see_actor=True,
        attack_distance=5,
        attack_modifier=3,
        damage_dice_count=1,
        damage_die_size="4",
        damage_modifier=1,
        damage_type="bludgeoning",
    )


@pytest.mark.parametrize("hit", [True, False], ids=["hit", "miss"])
def test_attack_secondary_effect_blocks_only_opportunity_until_targets_next_turn(monkeypatch, hit):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case("shocking_grasp")
    state[f"{SRD}.entities"]["hero"]["melee_reach"] = 5
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(
        hp=1000,
        max_hp=1000,
        armor_class=1 if hit else 99,
        melee_reach=5,
        available_reactions=["opportunity_attack", "shield"],
    )
    cast = _run("cast_spell", state, participant_data=data, **parameters)
    assert cast.result["context"]["_spell_attack_outcome"] == ("success" if hit else "failure")
    before = deepcopy(state)
    if hit:
        with pytest.raises(RulesetPreconditionRejected, match="Opportunity Attacks"):
            _opportunity(state)
        assert state == before
        assert state[f"{SRD}.entities"]["guard"]["reaction_available"] is True
        assert "Opportunity Attacks" in str(cast.result["result_card"])
    else:
        assert _opportunity(state).result["status"] == "committed"
        return
    _run("end_combat_turn", state)
    state[f"{SRD}.combat"]["current_actor"] = "guard"
    _run("begin_combat_turn", state)
    _run("end_combat_turn", state)
    state[f"{SRD}.combat"]["current_actor"] = "hero"
    _run("begin_combat_turn", state)
    assert _opportunity(state).result["status"] == "committed"


def test_opportunity_restrictions_preserve_other_reactions_and_expire_all_targets():
    state, data, _ = _spell_case("shocking_grasp")
    state[f"{SRD}.entities"]["hero"]["available_reactions"] = ["shield", "opportunity_attack"]
    for target in ("hero", "guard", "other"):
        _run("block_srd_opportunity_attacks", state, target_state=target, lasting_state="state")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Opportunity Attacks"):
        _run(
            "spend_srd_reaction",
            state,
            participant_data=data,
            actor="hero",
            actor_state="hero",
            reaction="opportunity_attack",
        )
    assert state == before
    assert (
        _run(
            "spend_srd_reaction",
            state,
            participant_data=data,
            actor="hero",
            actor_state="hero",
            reaction="shield",
        ).result["status"]
        == "committed"
    )
    _run("clear_srd_opportunity_block", state, target_state="guard", lasting_state="state")
    assert state[f"{SRD}.state"]["opportunity_blocked_targets"] == ["hero", "other"]
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert state[f"{SRD}.state"]["opportunity_blocked_targets"] == []
    assert all(not e["opportunity_attacks_blocked"] for e in state[f"{SRD}.entities"].values())


@pytest.mark.parametrize("creatures,skip", [(2, False), (3, False), (4, False), (4, True)])
def test_lasting_spell_time_counts_rounds_independently_of_party_size(monkeypatch, creatures, skip):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case("guidance")
    entities = state[f"{SRD}.entities"]
    entities["ally"] = deepcopy(entities["guard"])
    entities["ally"]["name"] = "Ally"
    for entity in entities.values():
        entity["initiative_modifier"] = 0
    entities["hero"]["initiative_modifier"] = 20
    combat = state[f"{SRD}.combat"]
    combat.update(encounter_active=False, turn_active=False)
    if creatures == 2:
        _run(
            "start_combat_encounter_entities",
            state,
            first_actor="hero",
            second_actor="guard",
            first_surprised=False,
            second_surprised=False,
            tie_winner="first",
        )
    else:
        _run(
            "start_group_combat_encounter",
            state,
            combatants=["hero", "guard", "other", "ally"][:creatures],
        )
    if skip:
        state[f"{SRD}.entities"]["ally"]["is_alive"] = False
    _run("begin_combat_turn", state)
    _run(
        "cast_spell", state, participant_data=data, **(parameters | {"selected_skill": "athletics"})
    )
    advance = (
        "advance_combat_encounter_turn" if creatures == 2 else "advance_group_combat_encounter_turn"
    )
    for completed_round in range(1, 11):
        for _ in range(creatures - int(skip)):
            _run("end_combat_turn", state)
            _run(advance, state)
            _run("begin_combat_turn", state)
        assert state[f"{SRD}.combat"]["round"] == completed_round + 1
        assert state[f"{SRD}.state"]["lasting_tick"] == pytest.approx(completed_round * 2)
        current = _run(
            "refresh_srd_concentration_source",
            state,
            source_state="hero",
            target_state="guard",
            lasting_state="state",
        )
        assert current.result["context"]["_source_concentration_active"] is (completed_round < 10)
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
