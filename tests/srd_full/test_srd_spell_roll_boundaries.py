"""Actual miss-half and saved later spell events keep one game-roll penalty and typed packet."""

import pytest

from sylvia_gm import dice
from tests import test_srd_action_budget as budget
from tests.srd_full.test_srd_base_damage_receipt import actual_receipt_owner as actual_receipt_owner
from tests.srd_full.test_srd_minor_attack_packets import SRD
from tests.srd_full.test_srd_minor_attack_packets import receipt_runtime as receipt_runtime
from tests.srd_full.test_srd_spell_damage_consumers import _enfeeble, _grant, _record_rolls
from tests.srd_full.test_srd_spell_wave3_afflictions import _cast as _curse_cast
from tests.srd_full.test_srd_spell_wave3_orders import _turn


def _concentrating_target(state):
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


class ActualMissFaces:
    def __init__(self, ray, extra):
        self.d4 = iter([1, 1, 1, 2])
        self.d8 = iter(([8] if ray else []) + [extra])

    def randint(self, low, high):
        assert low == 1
        return next(self.d4) if high == 4 else next(self.d8) if high == 8 else 1


@pytest.mark.parametrize(
    "ray,extra,acid_immune,resisted",
    [(False, 8, False, False), (True, 8, True, True), (False, 7, False, True)],
)
def test_actual_acid_arrow_miss_halves_one_combined_roll_before_type_defenses(
    monkeypatch, ray, extra, acid_immune, resisted
):
    state, data, _, _ = _curse_cast(
        monkeypatch, "bestow_curse", curse_mode="damage", slot_resource="spell_slot_5"
    )
    if ray:
        _enfeeble(state, data)
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(
        hp=1000,
        max_hp=1000,
        temp_hp=1000,
        armor_class=1,
        damage_immunities=["acid"] if acid_immune else [],
        damage_resistances=["acid", "necrotic"] if resisted else [],
    )
    _concentrating_target(state)
    monkeypatch.setattr(dice, "_rng", ActualMissFaces(ray, extra))
    rolls = _record_rolls(monkeypatch)
    result = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **_grant(state, data, "acid_arrow"),
    ).result
    (base,) = [r for r in rolls if r.sides == 4]
    assert base.results == (1, 1, 1, 2)
    d8 = [r for r in rolls if r.sides == 8]
    assert [r.total for r in d8] == ([8] if ray else []) + [extra]
    raw = base.total - (8 if ray else 0)
    base_amount = max(0, raw) // 2
    extra_amount = (max(0, extra - max(0, -raw)) + max(0, raw) % 2) // 2
    expected = 0 if acid_immune else base_amount // 2 if resisted else base_amount
    expected += extra_amount // 2 if resisted else extra_amount
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 1000 and guard["temp_hp"] == 1000 - expected
    assert guard["concentration_spell_id"] == "guidance"
    assert len([r for r in rolls if r.sides == 20]) == 2
    assert (
        f"Damage {expected} (HP 0; temporary HP {expected})"
        in result["result_card"]["details"][0]["value"]
    )
    assert not any(e["kind"] == "delayed_damage" for e in state[f"{SRD}.state"]["turn_effects"])


@pytest.mark.parametrize("ray", [False, True])
def test_actual_acid_arrow_saved_next_turn_damage_keeps_immune_base_curse_and_one_penalty(
    monkeypatch, ray
):
    state, data, _, _ = _curse_cast(
        monkeypatch, "bestow_curse", curse_mode="damage", slot_resource="spell_slot_5"
    )
    state[f"{SRD}.entities"]["guard"].update(
        hp=1000,
        max_hp=1000,
        temp_hp=1000,
        armor_class=1,
        damage_immunities=["acid"],
        damage_resistances=["necrotic"],
    )

    class InitialHitFaces:
        def randint(self, low, high):
            return 10 if high == 20 else high

    monkeypatch.setattr(dice, "_rng", InitialHitFaces())
    budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **_grant(state, data, "acid_arrow"),
    )
    if ray:
        _enfeeble(state, data)
    _concentrating_target(state)
    _turn(state, "guard", data)
    before = state[f"{SRD}.entities"]["guard"]["temp_hp"]

    class LaterFaces:
        def __init__(self):
            self.d4 = iter([1, 2])
            self.d8 = iter(([8] if ray else []) + [8])

        def randint(self, low, high):
            assert low == 1
            return next(self.d4) if high == 4 else next(self.d8) if high == 8 else 10

    monkeypatch.setattr(dice, "_rng", LaterFaces())
    rolls = _record_rolls(monkeypatch)
    result = budget._run("end_combat_turn", state).result
    (base,) = [r for r in rolls if r.sides == 4]
    assert base.results == (1, 2)
    assert [r.total for r in rolls if r.sides == 8] == ([8] if ray else []) + [8]
    expected = max(0, 8 - max(0, (8 if ray else 0) - base.total)) // 2
    assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == before - expected
    assert len([r for r in rolls if r.sides == 20]) == 1
    assert state[f"{SRD}.entities"]["guard"]["concentration_spell_id"] == "guidance"
    summary = {r["label"]: r["value"] for r in result["result_card"]["summary"]}
    assert summary["Damage applied"] == expected
    _turn(state, "guard", data)
    again = budget._run("end_combat_turn", state).result
    assert again["result_card"] is None
    assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == before - expected
