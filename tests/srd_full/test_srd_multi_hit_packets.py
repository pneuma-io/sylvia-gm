"""NPC multi-hit targets use the same typed packet and one real save per damage event."""

import pytest

from sylvia_gm import dice
from tests import test_srd_action_budget as budget
from tests.srd_full.test_srd_base_damage_receipt import actual_receipt_owner as actual_receipt_owner
from tests.srd_full.test_srd_minor_attack_packets import SRD, _minor_case
from tests.srd_full.test_srd_minor_attack_packets import receipt_runtime as receipt_runtime
from tests.srd_full.test_srd_spell_damage_consumers import _enfeeble, _grant, _record_rolls
from tests.srd_full.test_srd_spell_wave3_afflictions import _cast as _curse_cast


@pytest.mark.parametrize("ray", [False, True])
@pytest.mark.parametrize("with_minor", [False, True])
def test_actual_three_ray_npc_target_has_three_qualified_packets_and_three_concentration_saves(
    monkeypatch, ray, with_minor
):
    if with_minor:
        state, data = _minor_case(ray=ray)
    else:
        state, data, _, _ = _curse_cast(
            monkeypatch, "bestow_curse", curse_mode="damage", slot_resource="spell_slot_5"
        )
        if ray:
            _enfeeble(state, data)
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["creature_kind"] == "monster"
    guard.update(
        hp=1000,
        max_hp=1000,
        temp_hp=1000,
        armor_class=1,
        damage_immunities=["fire"],
        damage_resistances=["cold", "necrotic"],
    )
    budget._run(
        "install_srd_guidance_concentration",
        state,
        caster_state="guard",
        target_state="guard",
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
    parameters = _grant(state, data, "scorching_ray")
    parameters.update(attack_targets=["guard"] * 3, minor_elementals_damage_type="cold")

    class ActualThreeRayFaces:
        def __init__(self):
            self.d8 = iter((([8] if ray else []) + ([1, 1] if with_minor else []) + [8]) * 3)

        def randint(self, low, high):
            assert low == 1
            return next(self.d8) if high == 8 else 10 if high == 20 else 1

    monkeypatch.setattr(dice, "_rng", ActualThreeRayFaces())
    rolls = _record_rolls(monkeypatch)
    result = budget._run(
        "cast_spell_outside_combat", state, participant_data=data, **parameters
    ).result
    d8 = [roll for roll in rolls if roll.sides == 8]
    assert [roll.total for roll in d8] == (
        ([8] if ray else []) + ([2] if with_minor else []) + [8]
    ) * 3
    assert [roll.results for roll in rolls if roll.sides == 6] == [(1, 1)] * 3
    assert len([roll for roll in rolls if roll.sides == 20]) == 6
    expected_per_ray = (2 if ray else 5) if with_minor else (1 if ray else 4)
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 1000 and guard["temp_hp"] == 1000 - 3 * expected_per_ray
    assert guard["concentration_spell_id"] == "guidance"
    assert len(result["result_card"]["details"]) == 3
    assert all(
        f"Damage {expected_per_ray} (HP 0; temporary HP {expected_per_ray})" in detail["value"]
        for detail in result["result_card"]["details"]
    )
    assert result["status"] == "committed"
