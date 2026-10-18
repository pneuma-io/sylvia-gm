"""Shared next-turn spell effects change later public actions and expire at their owner."""

from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from tests.test_srd_action_budget import _run
from tests.test_srd_attack_budget import _combat_case, _npc_attack
from tests.test_srd_npc_spell_casting import _act
from tests.test_srd_public_spell_casting import _create_spell_campaign
from tests.test_srd_spell_result_cards import SRD, _spell_case, _spell_definitions


def _cast(monkeypatch, spell, *, bard_spells=(), **choices):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case(spell)
    if bard_spells:
        data["classes"] = [{"id": "bard", "level": 17}]
        data["abilities"]["charisma"] = 18
        data["spellcasting_grants"] = [
            {"source": "bard", "ability": "charisma", "spells": [spell, *bard_spells]},
        ]
        state[f"{SRD}.entities"]["hero"]["prepared_spells"].extend(bard_spells)
    state[f"{SRD}.entities"]["guard"].update(hp=1000, max_hp=1000, temp_hp=0)
    result = _run("cast_spell", state, participant_data=data, **(parameters | choices))
    return state, data, result


def _next(state, actor):
    _run("end_combat_turn", state)
    state[f"{SRD}.combat"]["current_actor"] = actor
    _run("begin_combat_turn", state)


def test_speed_reduction_changes_next_turn_movement_until_casters_next_start(monkeypatch):
    state, _, _ = _cast(monkeypatch, "ray_of_frost")
    _next(state, "guard")
    assert state[f"{SRD}.combat"]["movement_remaining"] == 20
    _run("spend_combat_movement", state, distance=20, difficult_terrain=False)
    _next(state, "hero")
    _next(state, "guard")
    assert state[f"{SRD}.combat"]["movement_remaining"] == 30
    assert state[f"{SRD}.entities"]["guard"]["speed"] == 30


def test_dodge_loses_its_attack_benefit_when_a_spell_reduces_speed_to_zero(monkeypatch):
    state, _, _ = _cast(monkeypatch, "ray_of_frost")
    state[f"{SRD}.entities"]["guard"].update(speed=10, dodging=True)
    _next(state, "other")
    attack = _npc_attack(state, "Spear", actor="other", target="guard", use_multiattack=False)
    assert len(attack.result["context"]["_attack_roll"]["results"]) == 1


@pytest.mark.parametrize("disadvantage", [False, True])
def test_next_attack_advantage_is_consumed_even_if_cancelled_or_attack_misses(
    monkeypatch,
    disadvantage,
):
    state, _, _ = _cast(monkeypatch, "guiding_bolt")
    state[f"{SRD}.entities"]["guard"]["armor_class"] = 99
    _next(state, "other")
    first = _npc_attack(
        state,
        "Spear",
        actor="other",
        target="guard",
        use_multiattack=False,
        disadvantage=disadvantage,
    )
    roll = first.result["context"]["_attack_roll"]
    assert len(roll["results"]) == (1 if disadvantage else 2)
    if not disadvantage:
        assert roll["total"] == max(roll["results"])
    _next(state, "other")
    second = _npc_attack(state, "Spear", actor="other", target="guard", use_multiattack=False)
    assert len(second.result["context"]["_attack_roll"]["results"]) == 1


def test_delayed_damage_occurs_once_at_targets_next_end_and_uses_upcast_dice(monkeypatch):
    state, _, _ = _cast(monkeypatch, "acid_arrow", slot_resource="spell_slot_4")
    hp = state[f"{SRD}.entities"]["guard"]["hp"]
    _next(state, "guard")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp
    ended = _run("end_combat_turn", state)
    after = state[f"{SRD}.entities"]["guard"]["hp"]
    assert hp - 16 <= after <= hp - 4
    detail = ended.result["result_card"]["details"][0]
    assert detail["label"] == "Acid Arrow"
    assert "4d4" in detail["value"] and "HP lost" in detail["value"]
    summary = {row["label"]: row["value"] for row in ended.result["result_card"]["summary"]}
    assert summary == {
        "Delayed spell effects": 1, "Damage applied": hp - after, "Creature": "Guard",
        "Conditions after": "None",
    }
    state[f"{SRD}.combat"]["current_actor"] = "guard"
    _run("begin_combat_turn", state)
    quiet = _run("end_combat_turn", state)
    assert quiet.result["result_card"] is None
    assert state[f"{SRD}.entities"]["guard"]["hp"] == after


def test_unused_next_attack_effect_expires_at_casters_next_end_not_start(monkeypatch):
    state, _, _ = _cast(monkeypatch, "guiding_bolt")
    _next(state, "guard")
    _next(state, "hero")
    assert state[f"{SRD}.state"]["turn_effects"]
    _next(state, "other")
    attack = _npc_attack(state, "Spear", actor="other", target="guard", use_multiattack=False)
    assert len(attack.result["context"]["_attack_roll"]["results"]) == 1


def test_another_spell_attack_consumes_the_saved_next_attack_benefit(monkeypatch):
    state, data, _ = _cast(monkeypatch, "guiding_bolt")
    _next(state, "guard")
    _next(state, "hero")
    _, _, parameters = _spell_case("guiding_bolt")
    cast = _run("cast_spell", state, participant_data=data, **parameters)
    roll = cast.result["context"]["_spell_attack"]
    assert len(roll["results"]) == 2 and roll["total"] == max(roll["results"])
    # The new hit installs its own benefit; the old benefit did not boost its own hit.
    assert len(state[f"{SRD}.state"]["turn_effects"]) == 1


def test_attack_miss_never_installs_delayed_consequences(monkeypatch):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case("acid_arrow")
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(hp=1000, max_hp=1000, temp_hp=0, armor_class=99)
    cast = _run("cast_spell", state, participant_data=data, **parameters)
    assert cast.result["context"]["_spell_attack_outcome"] == "failure"
    hp = state[f"{SRD}.entities"]["guard"]["hp"]
    assert hp < 1000  # Acid Arrow retains its initial half damage on a miss.
    _next(state, "guard")
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp


def test_multiple_spell_attacks_consume_advantage_on_only_the_first_roll(monkeypatch):
    state, data, _ = _cast(monkeypatch, "guiding_bolt", bard_spells=["scorching_ray"])
    _next(state, "guard")
    _next(state, "hero")
    _, _, parameters = _spell_case("scorching_ray")
    cast = _run(
        "cast_spell",
        state,
        participant_data=data,
        **(parameters | {"attack_targets": ["guard", "guard", "guard"]}),
    )
    rolls = cast.result["context"]["_spell_multi_attacks"]
    assert [len(roll["results"]) for roll in rolls] == [2, 1, 1]


def test_delayed_effect_survives_reopen_and_free_time_retry_cannot_repeat_damage(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "delayed.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "acid_arrow")
        receipt, _ = _act(owner, "cast_spell_outside_combat", **(parameters | {"target": "other"}))
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting").state
        assert saved[f"{SRD}.state"]["turn_effects"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved
        receipt, command = _act(owner, "advance_srd_lasting_time", elapsed_rounds=1)
        assert receipt.status is ActionStatus.COMMITTED
        after = owner.snapshot("casting").state
        assert after[f"{SRD}.entities"]["other"]["hp"] < saved[f"{SRD}.entities"]["other"]["hp"]
        assert after[f"{SRD}.state"]["turn_effects"] == []
        assert receipt.result["result_card"]["details"][0]["label"] == "Acid Arrow"
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting").state == after


@pytest.mark.parametrize("moved", [0, 25])
def test_speed_sources_do_not_stack_and_dash_uses_the_reduced_speed(moved):
    state, data, _ = _spell_case("ray_of_frost")
    _next(state, "hero")
    if moved:
        _run("spend_combat_movement", state, distance=moved, difficult_terrain=False)
    definition = _spell_definitions()["ray_of_frost"]
    effect = definition["turn_effect"]
    for source in ("hero", "other"):
        _run(
            "install_srd_turn_effect",
            state,
            caster_state=source,
            target_state="hero",
            lasting_state="state",
            spell="ray_of_frost",
            effect_label=effect["label"],
            damage_die=definition["damage_die"],
            **{
                k: effect[k]
                for k in (
                    "kind",
                    "expiry_owner",
                    "expiry_phase",
                    "magnitude",
                    "dice_count",
                    "damage_type",
                )
            },
        )
    assert state[f"{SRD}.combat"]["movement_remaining"] == max(0, 20 - moved)
    _run("dash_combat_action", state, participant_data=data, actor="hero", actor_state="hero")
    assert state[f"{SRD}.combat"]["movement_remaining"] == 40 - moved
    _next(state, "hero")
    assert state[f"{SRD}.combat"]["movement_remaining"] == 20
    assert len(state[f"{SRD}.state"]["turn_effects"]) == 1
    _next(state, "other")
    _next(state, "hero")
    assert state[f"{SRD}.combat"]["movement_remaining"] == 30


@pytest.mark.parametrize("healing_spell", ["cure_wounds", "power_word_heal"])
def test_healing_block_survives_later_casting_until_casters_next_end(monkeypatch, healing_spell):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    state, data, parameters = _spell_case("chill_touch")
    data["level"] = 18
    data["classes"] = [{"id": "wizard", "level": 1}, {"id": "cleric", "level": 17}]
    data["abilities"]["wisdom"] = 18
    data["spellcasting_grants"].append(
        {"source": "cleric", "ability": "wisdom", "spells": [healing_spell]},
    )
    state[f"{SRD}.entities"]["hero"]["melee_reach"] = 5
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append(healing_spell)
    state[f"{SRD}.entities"]["guard"].update(hp=1000, max_hp=1000, temp_hp=0)
    hit = _run("cast_spell", state, participant_data=data, **parameters)
    guard = state[f"{SRD}.entities"]["guard"]
    assert hit.result["context"]["_spell_attack_outcome"] == "success"
    # A later injury leaves the target unconscious; blocked healing cannot wake it.
    guard.update(hp=0, conditions=["unconscious", "prone"], death_failures=2)
    _next(state, "guard")
    _next(state, "hero")
    _, _, parameters = _spell_case(healing_spell)
    if healing_spell == "power_word_heal":
        parameters["use_prone_reaction_to_stand"] = True
    healed = _run("cast_spell", state, participant_data=data, **parameters)
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 0
    assert "unconscious" in guard["conditions"] and "prone" in guard["conditions"]
    assert guard["reaction_available"] is True
    assert guard["death_failures"] == 2
    assert "HP restored 0" in str(healed.result["result_card"])
    assert state[f"{SRD}.entities"]["hero"]["resources"][parameters["slot_resource"]] == 2
    _run("end_combat_turn", state)
    _run("restore_hit_points", state, target="guard", amount=1)
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 1
    assert "unconscious" not in guard["conditions"]
    assert guard["death_failures"] == 0


@pytest.mark.parametrize("recovery", ["ordinary", "second_wind", "death_save", "stable_time"])
def test_healing_sources_share_prohibition_and_only_real_recovery_wakes(monkeypatch, recovery):
    state, data = _combat_case()
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(hp=1, death_successes=0, death_failures=2, stable=False,
                stable_recovery_hours_remaining=0, available_bonus_actions=["second_wind"],
                resources={"second_wind": 1})
    effect = _spell_definitions()["chill_touch"]["turn_effect"]
    _run("install_srd_turn_effect", state, caster_state="guard", target_state="hero",
         lasting_state="state", spell="chill_touch", effect_label=effect["label"],
         damage_die="d10", **{key: effect[key] for key in (
             "kind", "expiry_owner", "expiry_phase", "magnitude", "dice_count", "damage_type",
         )})
    if recovery == "ordinary":
        _run("restore_hit_points", state, target="hero", amount=10)
    elif recovery == "second_wind":
        result = _run("use_srd_second_wind", state, participant_data=data,
                      actor="hero", actor_state="hero")
        assert result.result["context"]["_second_wind_hp_restored"] == 0
        assert state[f"{SRD}.entities"]["hero"]["resources"]["second_wind"] == 0
    else:
        hero = state[f"{SRD}.entities"]["hero"]
        hero.update(hp=0, conditions=["unconscious"])
        if recovery == "death_save":
            monkeypatch.setattr(dice, "_rng", Random(5))
            monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
            result = _run("death_saving_throw", state, participant_data=data)
            assert result.result["context"]["_death_save"]["total"] == 20
        else:
            state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
            hero.update(stable=True, stable_recovery_hours_remaining=1)
            _run("advance_stable_recovery", state, target="hero", elapsed_hours=1)
            hero = state[f"{SRD}.entities"]["hero"]
            assert hero["hp"] == 1 and "unconscious" not in hero["conditions"]
            assert hero["hit_point_recovery_blocked"] is False
            assert state[f"{SRD}.state"]["lasting_tick"] == 1200
            return
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["hp"] == (0 if recovery == "death_save" else 1)
    assert hero["death_failures"] == 2
    if recovery == "death_save":
        assert "unconscious" in hero["conditions"]
