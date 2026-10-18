"""Extra typed components consume the actual saved resistance/reduction source exports."""

import pytest

from sylvia_gm import dice
from tests import test_srd_action_budget as budget
from tests.srd_full.test_srd_attack_roll_components import _npc_spell, _prepared_caster
from tests.srd_full.test_srd_minor_attack_packets import (
    ActualPacketFaces,
    _minor_case,
    _position,
)
from tests.srd_full.test_srd_minor_attack_packets import (
    actual_receipt_owner as actual_receipt_owner,
)
from tests.srd_full.test_srd_minor_attack_packets import (
    receipt_runtime as receipt_runtime,
)
from tests.srd_full.test_srd_spell_damage_consumers import _grant, _record_rolls
from tests.srd_full.test_srd_spell_result_cards import SRD
from tests.srd_full.test_srd_spell_wave3_information import _entity


@pytest.mark.parametrize(
    "spell,damage_type", [("protection_from_energy", "cold"), ("resistance", "necrotic")]
)
def test_actual_minor_and_curse_components_consume_saved_source_defenses(
    monkeypatch, spell, damage_type
):
    state, data = _minor_case()
    _prepared_caster(state, "warder", spell, 3 if spell == "protection_from_energy" else 0)
    row = _entity("warder", [5, 5, 0], participant_ref="warder", creature_type="humanoid")
    row["map_id"] = _position(state, "guard")["map_id"]
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(row)
    _npc_spell(state, data, "warder", spell, "guard", selected_damage_type=damage_type)
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(hp=1000, max_hp=1000, temp_hp=1000, armor_class=1, damage_immunities=["fire"])
    parameters = _grant(state, data, "fire_bolt")
    monkeypatch.setattr(dice, "_rng", ActualPacketFaces())
    rolls = _record_rolls(monkeypatch)
    result = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(parameters | {"minor_elementals_damage_type": "cold"}),
    ).result
    minor, curse = [roll for roll in rolls if roll.sides == 8]
    assert minor.total == 2 and curse.total == 8
    if spell == "protection_from_energy":
        assert not [roll for roll in rolls if roll.sides == 4]
        expected = minor.total // 2 + curse.total
    else:
        (reduction,) = [roll for roll in rolls if roll.sides == 4]
        expected = minor.total + max(0, curse.total - reduction.total)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == 1000 - expected
    assert (
        f"Damage {expected} (HP 0; temporary HP {expected})"
        in result["result_card"]["details"][0]["value"]
    )
