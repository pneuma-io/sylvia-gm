"""Printed attack components spend the same real Ray penalty before their typed defenses."""

import pytest

from sylvia_gm import dice
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_attack_budget import _attack
from tests.srd_full.test_srd_base_damage_receipt import actual_receipt_owner as actual_receipt_owner
from tests.srd_full.test_srd_base_damage_receipt import receipt_runtime as receipt_runtime
from tests.srd_full.test_srd_spell_damage_consumers import _enfeeble, _record_rolls
from tests.srd_full.test_srd_spell_wave3_afflictions import _cast as _curse_cast

SRD = cards.SRD


class ActualAttackFaces:
    def __init__(self, critical):
        self.critical = critical

    def randint(self, low, high):
        assert low == 1
        return (20 if self.critical else 10) if high == 20 else (high if high in (4, 8) else 1)


@pytest.mark.parametrize("critical,resisted", [(False, False), (True, False), (True, True)])
def test_actual_weapon_favor_curse_and_ray_subtract_one_penalty_before_typed_defenses(
    monkeypatch, critical, resisted
):
    state, data, _, _ = _curse_cast(
        monkeypatch, "bestow_curse", curse_mode="damage", slot_resource="spell_slot_5"
    )
    data["classes"] = [{"id": "wizard", "level": 8}, {"id": "paladin", "level": 9}]
    data["spellcasting_grants"].append(
        {"source": "paladin", "ability": "charisma", "spells": ["divine_favor"]}
    )
    hero = state[f"{SRD}.entities"]["hero"]
    hero["prepared_spells"].append("divine_favor")
    _, _, favor = cards._spell_case("divine_favor")
    favor.pop("target")
    budget._run("cast_spell_outside_combat", state, participant_data=data, **favor)
    _enfeeble(state, data)
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(
        hp=1000,
        max_hp=1000,
        temp_hp=1000,
        armor_class=1,
        damage_resistances=["radiant", "necrotic"] if resisted else [],
    )
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="hero",
        current_actor="hero",
        action_available=True,
        normal_action_spent=False,
    )
    hero.update(inventory=["spear"], ammunition={}, fighter_level=0)
    data["weapon_proficiencies"] = ["simple_weapons"]
    monkeypatch.setattr(dice, "_rng", ActualAttackFaces(critical))
    rolls = _record_rolls(monkeypatch)
    result = _attack(state, data).result["context"]
    penalty, curse = [r for r in rolls if r.sides == 8]
    assert penalty.count == 1 and penalty.total == 8
    assert curse.count == (2 if critical else 1)
    (base,) = [r for r in rolls if r.sides == 6]
    (favor_roll,) = [r for r in rolls if r.sides == 4]
    raw_base = base.total + result["_damage_modifier"]
    carry = max(0, penalty.total - raw_base)
    favor_amount = max(0, favor_roll.total - carry)
    final_carry = max(0, carry - favor_roll.total)
    curse_amount = max(0, curse.total - final_carry)
    assert result["_divine_favor_amount"] == favor_amount
    expected = max(0, raw_base - penalty.total)
    expected += favor_amount // 2 if resisted else favor_amount
    expected += curse_amount // 2 if resisted else curse_amount
    assert result["_damage_applied"] == expected
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 1000 and guard["temp_hp"] == 1000 - expected


def _prepared_caster(state, identity, spell, level=1):
    budget._run(
        "prepare_srd_creature",
        state,
        creature_id=identity,
        name=identity,
        source="Synthetic source caster for the real later attack.",
        armor_class=12,
        hit_points=25,
        speed=30,
        initiative_modifier=0,
        size_rank=2,
        constitution_save=6,
        wisdom_save=0,
        creature_type="humanoid",
        spellcasting_ability="wisdom",
        spellcasting_modifier=3,
        spell_attack_bonus=7,
        spell_save_dc=15,
        spells=[{"spell": spell, "uses_per_day": 1, "casting_level": level}],
    )


def _npc_spell(state, data, caster, spell, target, **choices):
    _, _, parameters = cards._spell_case(spell)
    parameters.pop("caster")
    parameters.pop("slot_resource")
    parameters.update(
        caster_state=caster,
        target=target,
        targets=[target],
        forego_save_targets=[target],
        **choices,
    )
    budget._run("cast_spell_outside_combat", state, participant_data=data, **parameters)


@pytest.mark.parametrize("critical,immune,resisted", [(False, False, False), (True, True, True)])
def test_actual_form_poison_curse_and_ray_keep_raw_faces_one_penalty_and_one_lifecycle(
    monkeypatch, receipt_runtime, critical, immune, resisted
):
    from tests import test_srd_spell_wave6_transformations as forms

    state, data, parameters = forms._setup(
        "polymorph", transformation_form="venomous_snake", forego_save_targets=["guard"]
    )
    del state[f"{SRD}.entities"]["guard"]
    _prepared_caster(state, "guard", "bestow_curse", 5)
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(challenge_rating_eighths=80, ability_scores=dict.fromkeys(data["abilities"], 12))
    _npc_spell(state, data, "guard", "bestow_curse", "other", curse_mode="damage")
    budget._run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    _prepared_caster(state, "mage", "ray_of_enfeeblement", 2)
    _npc_spell(state, data, "mage", "ray_of_enfeeblement", "guard")
    other = state[f"{SRD}.entities"]["other"]
    other.update(
        hp=1000,
        max_hp=1000,
        temp_hp=1000,
        armor_class=1,
        damage_immunities=["poison"] if immune else [],
        damage_resistances=["necrotic"] if resisted else [],
    )
    state[f"{SRD}.entities"]["guard"]["ally_state_ids"] = []
    budget._run(
        "install_srd_guidance_concentration",
        state,
        caster_state="other",
        target_state="other",
        lasting_state="state",
        casting_ability="wisdom",
        casting_modifier=1,
        casting_attack_bonus=3,
        casting_save_dc=11,
        casting_scaling_level=1,
        casting_concentration_save_modifier=100,
        selected_skill="athletics",
        duration_rounds=10,
    )
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        current_actor="guard",
        action_available=True,
        attacks_remaining=0,
        normal_action_spent=False,
    )
    monkeypatch.setattr(dice, "_rng", ActualAttackFaces(critical))
    rolls = _record_rolls(monkeypatch)
    result = forms._source_attack(state)
    penalty, curse = [r for r in rolls if r.sides == 8]
    assert penalty.count == 1 and penalty.total == 8
    assert curse.count == (2 if critical else 1)
    (base,) = [r for r in rolls if r.sides == 4]
    (poison,) = [r for r in rolls if r.sides == 6]
    runtime, _ = receipt_runtime
    body = runtime["definitions"]["beast_forms"]["venomous_snake"]["attacks"]["Bite"]
    raw_base = base.total + body["damage_modifier"]
    carry = max(0, penalty.total - raw_base)
    raw_poison = poison.total + body["extra_damage_modifier"]
    poison_amount = max(0, raw_poison - carry)
    curse_amount = max(0, curse.total - max(0, carry - raw_poison))
    expected = max(0, raw_base - penalty.total) + (0 if immune else poison_amount)
    expected += curse_amount // 2 if resisted else curse_amount
    assert result["_form_extra_damage_roll"]["results"] == list(poison.results)
    assert result["_form_extra_damage_amount"] == poison_amount
    assert result["_effective_damage"] == expected
    other = state[f"{SRD}.entities"]["other"]
    assert other["hp"] == 1000 and other["temp_hp"] == 1000 - expected
    assert len([r for r in rolls if r.sides == 20]) == 2
    assert result["_form_concentration_save_dc"] == max(10, expected // 2)
