"""Actual same-roll penalty remainders compose before half damage and typed defenses."""

import pytest

from sylvia_gm import dice
from tests import test_srd_action_budget as budget
from tests.srd_full.test_srd_spell_damage_consumers import (
    SRD,
    _begin,
    _curse_cast,
    _divination_case,
    _enfeeble,
    _grant,
    _npc_attack,
    _record_rolls,
)
from tests.srd_full.test_srd_spell_damage_consumers import (
    actual_consumers as actual_consumers,
)
from tests.srd_full.test_srd_spell_damage_consumers import (
    compiled_consumers as compiled_consumers,
)
from tests.srd_full.test_srd_spell_damage_consumers import (
    composed_runtime as composed_runtime,
)


class _LowBaseRollFaces:
    """Supply raw faces to the real damage owners without substituting their results."""

    def __init__(self, d8):
        self.d8 = iter(d8)

    def randint(self, low, high):
        assert low == 1
        return next(self.d8) if high == 8 else 1 if high == 6 else 10


@pytest.mark.parametrize("success,resisted", [(False, False), (True, False), (False, True)])
def test_mind_spike_remainder_precedes_save_half_and_necrotic_defense(
    monkeypatch, success, resisted
):
    state, data, _, _ = _curse_cast(
        monkeypatch, "bestow_curse", curse_mode="damage", slot_resource="spell_slot_5"
    )
    parameters = _grant(state, data, "mind_spike")
    scene, _, _ = _divination_case("mind_spike")
    state[f"{SRD}.state"]["spatial_scene"] = scene[f"{SRD}.state"]["spatial_scene"]
    _enfeeble(state, data)
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(hp=1000, max_hp=1000, temp_hp=0, damage_immunities=["psychic"])
    guard["saving_throw_modifiers"]["wisdom"] = 100 if success else -100
    guard["damage_resistances"] = ["necrotic"] if resisted else []
    monkeypatch.setattr(dice, "_rng", _LowBaseRollFaces([1, 1, 1, 8, 8]))
    rolls = _record_rolls(monkeypatch)
    budget._run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    raw, penalty, extra = [r for r in rolls if r.sides == 8]
    assert raw.results == (1, 1, 1) and penalty.total == extra.total == 8
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["last_curse_damage_roll"]["total"] == 8
    assert guard["hp"] == 1000 - (1 if success or resisted else 3)


def test_contact_remainder_uses_one_actual_penalty_before_independent_necrotic_defense(monkeypatch):
    state, data, parameters = _divination_case("contact_other_plane")
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(hp=500, max_hp=500, temp_hp=0)
    hero["saving_throw_modifiers"]["wisdom"] = -100
    curse = _grant(state, data, "bestow_curse")
    curse.update(target="hero", targets=["hero"], curse_mode="damage", slot_resource="spell_slot_5")
    budget._run("cast_spell_outside_combat", state, participant_data=data, **curse)
    budget._run("begin_srd_spell_cast", state, participant_data=data, **_begin(parameters))
    budget._run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _enfeeble(state, data)
    hero = state[f"{SRD}.entities"]["hero"]
    hero["saving_throw_modifiers"]["intelligence"] = -100
    hero.update(damage_immunities=["psychic"], damage_resistances=["necrotic"])
    monkeypatch.setattr(dice, "_rng", _LowBaseRollFaces([8, 8]))
    rolls = _record_rolls(monkeypatch)
    budget._run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    (raw,) = [r for r in rolls if r.sides == 6]
    assert raw.results == (1, 1, 1, 1, 1, 1)
    assert [r.total for r in rolls if r.sides == 8] == [8, 8]
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["hp"] == 497 and "incapacitated" in hero["conditions"]
    assert hero["last_curse_damage_roll"]["total"] == 8


def test_mind_spike_halves_the_actual_combined_odd_damage_roll_once(monkeypatch):
    state, data, _, _ = _curse_cast(
        monkeypatch, "bestow_curse", curse_mode="damage", slot_resource="spell_slot_5"
    )
    parameters = _grant(state, data, "mind_spike")
    scene, _, _ = _divination_case("mind_spike")
    state[f"{SRD}.state"]["spatial_scene"] = scene[f"{SRD}.state"]["spatial_scene"]
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(hp=1000, max_hp=1000, temp_hp=0)
    guard["saving_throw_modifiers"]["wisdom"] = 100
    monkeypatch.setattr(dice, "_rng", _LowBaseRollFaces([1, 1, 1, 1]))
    rolls = _record_rolls(monkeypatch)
    budget._run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    raw, extra = [r for r in rolls if r.sides == 8]
    assert raw.total == 3 and extra.total == 1
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 998


def test_fire_shield_remainder_preserves_the_later_hit_single_packet(monkeypatch):
    state, data, _, _ = _curse_cast(
        monkeypatch, "bestow_curse", curse_mode="damage", slot_resource="spell_slot_5"
    )
    parameters = _grant(state, data, "fire_shield")
    parameters.pop("target")
    parameters["actor_spell_choice"] = "warm"
    budget._run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    _enfeeble(state, data)
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="guard",
        current_actor="guard",
        action_available=True,
        normal_action_spent=False,
    )
    state[f"{SRD}.entities"]["guard"].update(
        hp=1000,
        max_hp=1000,
        temp_hp=0,
        multiattack=[{"attack": "Claw", "count": 1}],
        damage_immunities=["fire"],
    )
    monkeypatch.setattr(dice, "_rng", _LowBaseRollFaces([1, 1, 8, 8]))
    rolls = _record_rolls(monkeypatch)
    result = _npc_attack(
        state, "Claw", attack_modifier=100, attack_mode="melee", attack_distance=5
    ).result["context"]
    raw, penalty, extra = [r for r in rolls if r.sides == 8]
    assert raw.results == (1, 1) and penalty.total == extra.total == 8
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 998
    assert result["_fire_shield_damage_applied"] == 2
