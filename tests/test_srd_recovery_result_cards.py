"""Death, stabilization, escape and Reaction cards retain actual rolls and state changes."""

from random import Random

import pytest

from sylvia_gm import dice
from tests.test_srd_action_budget import _run
from tests.test_srd_attack_budget import SRD, _attack, _combat_case


def _rolls(monkeypatch, seed):
    monkeypatch.setattr(dice, "_rng", Random(seed))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)


def _rows(card, section="summary"):
    return {row["label"]: row["value"] for row in card[section]}


def test_natural_twenty_death_save_keeps_the_recovered_characters_turn(monkeypatch):
    state, data = _combat_case()
    state[f"{SRD}.entities"]["hero"].update(
        hp=0, stable=False, conditions=["unconscious", "prone"],
        death_successes=0, death_failures=0, stable_recovery_hours_remaining=0,
    )
    state[f"{SRD}.combat"]["turn_active"] = False
    _run("begin_combat_turn", state)
    _rolls(monkeypatch, 5)
    _run("death_saving_throw", state, participant_data=data)
    combat = state[f"{SRD}.combat"]
    assert combat["turn_active"] is True
    assert combat["active_actor_at_zero_hp"] is False
    assert combat["active_actor_can_act"] is True
    assert "prone" in state[f"{SRD}.entities"]["hero"]["conditions"]
    assert _attack(state, data).result["status"] == "committed"


@pytest.mark.parametrize("kind", ["healing_block", "delayed_damage"])
def test_unrecovered_death_save_runs_end_turn_effects(monkeypatch, kind):
    state, data = _combat_case()
    state[f"{SRD}.entities"]["hero"].update(
        hp=0, stable=False, conditions=["unconscious"], turns_started=0,
        death_successes=0, death_failures=1, stable_recovery_hours_remaining=0,
    )
    _run(
        "install_srd_turn_effect", state, caster_state="hero",
        target_state="guard" if kind == "healing_block" else "hero",
        lasting_state="state", spell="chill_touch" if kind == "healing_block" else "acid_arrow",
        effect_label="Chill Touch" if kind == "healing_block" else "Acid Arrow",
        kind=kind, expiry_owner="caster", expiry_phase="end",
        magnitude=0, dice_count=0 if kind == "healing_block" else 1,
        damage_die="d4", damage_type="acid",
    )
    if kind == "healing_block":
        assert state[f"{SRD}.entities"]["guard"]["hit_point_recovery_blocked"] is True
    state[f"{SRD}.combat"]["turn_active"] = False
    _run("begin_combat_turn", state)
    _rolls(monkeypatch, 1)
    result = _run("death_saving_throw", state, participant_data=data).result
    assert state[f"{SRD}.combat"]["phase"] == "awaiting_advance"
    if kind == "healing_block":
        assert state[f"{SRD}.entities"]["guard"]["hit_point_recovery_blocked"] is False
    else:
        assert state[f"{SRD}.entities"]["hero"]["is_alive"] is False
        assert _rows(result["result_card"])["Outcome"] == "Died"
        assert _rows(result["result_card"])["Failures"] == 3
        assert "Acid Arrow" in _rows(result["result_card"], "details")["End of turn"]


@pytest.mark.parametrize("seed,successes,failures,exhaustion,outcome,hp,alive,stable", [
    (31, 0, 1, 0, "Died", 0, False, False),
    (5, 0, 0, 0, "Regained 1 HP", 1, True, False),
    (0, 2, 0, 0, "Stable", 0, True, True),
    (1, 0, 0, 0, "Failure", 0, True, False),
    (0, 0, 0, 2, "Failure", 0, True, False),
])
def test_death_save_card_preserves_natural_roll_and_final_state(
    monkeypatch, seed, successes, failures, exhaustion, outcome, hp, alive, stable,
):
    _rolls(monkeypatch, seed)
    state, data = _combat_case()
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(name="Budget Hero", hp=0, stable=False, conditions=["unconscious"],
                death_successes=successes, death_failures=failures,
                exhaustion_level=exhaustion, stable_recovery_hours_remaining=0)
    result = _run("death_saving_throw", state, participant_data=data).result
    card = result.get("result_card")
    assert card is not None
    after = state[f"{SRD}.entities"]["hero"]
    assert (after["hp"], after["is_alive"], after["stable"]) == (hp, alive, stable)
    assert _rows(card)["Outcome"] == outcome
    assert card["equation"]["roll"] == Random(seed).randint(1, 20)
    assert card["equation"]["modifier"] == -2 * exhaustion
    assert _rows(card)["Successes"] == after["death_successes"]
    assert _rows(card)["Failures"] == after["death_failures"]


@pytest.mark.parametrize("modifier,expected", [(20, "Stable"), (-20, "Not stabilized")])
def test_stabilization_card_reports_check_and_committed_recovery(monkeypatch, modifier, expected):
    _rolls(monkeypatch, 0)
    state, _ = _combat_case()
    state[f"{SRD}.entities"]["hero"].update(name="Budget Hero", hp=0, stable=False,
        death_successes=1, death_failures=1, stable_recovery_hours_remaining=0,
        conditions=["unconscious"])
    result = _run("stabilize_character", state, target="hero", medicine_modifier=modifier,
                  advantage=False, disadvantage=False).result
    card = result.get("result_card")
    assert card is not None
    assert _rows(card)["Outcome"] == expected
    assert card["equation"] == {
        "die_sides": 20, "roll": 13, "modifier": modifier, "total": 13 + modifier,
    }
    assert state[f"{SRD}.entities"]["hero"]["stable"] is (modifier > 0)


@pytest.mark.parametrize("dc,expected", [(1, "Escaped"), (99, "Still grappled")])
def test_grapple_escape_card_retains_dc_after_success_clears_it(dc, expected):
    state, data = _combat_case()
    data["skill_proficiencies"] = ["athletics"]
    state[f"{SRD}.entities"]["hero"].update(conditions=["grappled"], grapple_escape_dc=dc,
                                            grappled_by="guard")
    result = _run("escape_srd_grapple_action", state, participant_data=data,
                  actor="hero", actor_state="hero", ability="strength",
                  advantage=False, disadvantage=False).result
    card = result.get("result_card")
    assert card is not None
    assert _rows(card)["Outcome"] == expected
    assert _rows(card)["DC"] == dc
    assert card["equation"]["modifier"] == 6
    assert ("grappled" in state[f"{SRD}.entities"]["hero"]["conditions"]) is (dc == 99)


def test_opportunity_card_matches_committed_damage_and_reaction(monkeypatch):
    _rolls(monkeypatch, 0)
    state, data = _combat_case()
    state[f"{SRD}.entities"]["hero"].update(name="Budget Hero", armor_class=1)
    state[f"{SRD}.entities"]["guard"].update(
        available_reactions=["opportunity_attack"], reaction_available=True, melee_reach=5,
    )
    _run("trigger_opportunity_attack", state, participant_data=data,
         reactor_state="guard", actor_can_see_target=True, target_can_see_actor=True,
         attack_distance=5, attack_modifier=3, damage_dice_count=1, damage_die_size="6",
         damage_modifier=1, damage_type="piercing")
    result = _run("resolve_opportunity_attack", state, target_is_charmer=False,
                  fear_source_in_sight=False, advantage=False, disadvantage=False).result
    card = result.get("result_card")
    assert card is not None
    assert card["title"] == "Opportunity Attack"
    summary = _rows(card)
    assert summary["Actor"] == "Guard" and summary["Target"] == "Budget Hero"
    assert summary["Damage"] == 20 - state[f"{SRD}.entities"]["hero"]["hp"]
    assert card["equation"]["modifier"] == 3
    assert state[f"{SRD}.entities"]["guard"]["reaction_available"] is False
    assert state[f"{SRD}.combat"]["pending_reaction"] is None
