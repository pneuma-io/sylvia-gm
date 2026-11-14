"""Native self-target damage retains logical attacker and the original physical body."""

import json

from sylvia_gm import dice
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_summoning import (
    SRD,
    _native_mummy_glare_case,
)
from tests.srd_full.test_srd_spell_summoning import (
    _native_mummy_fist_runtime as _native_mummy_fist_runtime,
)
from tests.srd_full.test_srd_spell_summoning import (
    _native_mummy_glare_runtime as _native_mummy_glare_runtime,
)
from tests.srd_full.test_srd_spell_summoning import (
    native_mummy_fist_owner as native_mummy_fist_owner,
)


def test_true_native_self_fist_dust_retains_attacker_id_and_physical_corpse(
    native_mummy_fist_owner,
):
    state, data = _native_mummy_glare_case()
    body = state[f"{SRD}.entities"]["native_mummy"]
    physical = body["created_body_source_id"]
    _run(
        "apply_srd_damage",
        state,
        target="native_mummy",
        amount=body["hp"] - 1,
        adjustment=0,
        damage_type="force",
        critical_hit=False,
    )
    assert state[f"{SRD}.entities"]["native_mummy"]["hp"] == 1
    state = json.loads(json.dumps(state))
    dice._rng.seed(5)
    context = _run(
        "resolve_adjudicated_creature_attack",
        state,
        participant_data=data,
        actor="native_mummy",
        target="native_mummy",
        attack_name="Rotting Fist",
        attack_modifier=5,
        damage_expression="d10",
        damage_modifier=3,
        damage_type="bludgeoning",
        attack_mode="melee",
        normal_range=5,
        long_range=5,
        attack_distance=0,
        actor_can_see_target=True,
        target_can_see_actor=True,
        target_is_charmer=False,
        fear_source_in_sight=False,
        advantage=False,
        disadvantage=False,
        use_multiattack=False,
        attack_uses_weapon=False,
    ).result["context"]
    body = state[f"{SRD}.entities"]["native_mummy"]
    assert context["_attack_roll"]["results"] == [20]
    assert context["_component_2_effective_damage"] == 0
    assert context["_mummy_fist_dust"] is True and body["is_alive"] is False
    record = state[f"{SRD}.state"]["native_mummy_dust_bodies"][physical]
    assert record["target"] == record["attacker"] == "native_mummy"
    assert record["body_source_id"] == physical and record["tick"] == body["death_tick"]
    assert body["created_body_source_id"] == physical
