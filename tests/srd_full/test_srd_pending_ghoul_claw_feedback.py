"""A saved Ghoul Claw exposes only its genuine native save and installed source."""

from copy import deepcopy

import pytest

from sylvia_gm import dice
from tests import test_srd_pending_ghoul_feedback as feedback
from tests import test_srd_spell_summoning as summoning
from tests.srd_full.test_srd_action_budget import SRD, _run

_created_undead_runtime = feedback._created_undead_runtime
created_undead_owner = feedback.created_undead_owner


@pytest.mark.parametrize(
    "species,save,hit,expected",
    [
        ("human", 1, True, "failure"),
        ("human", 20, True, "success"),
        ("elf", 1, True, "exempt"),
        ("human", 1, False, None),
    ],
)
def test_saved_claw_returns_real_save_source_and_card(
    created_undead_owner, monkeypatch, species, save, hit, expected
):
    state, data = summoning._ghoul_bite_case()
    data["species"] = species
    summoning._command_ghoul_attack_turn(state, data)
    hp = state[f"{SRD}.entities"]["hero"]["hp"]
    rolls = []

    class ClawDice:
        def randint(self, low, high):
            assert low == 1 and high in (4, 20)
            rolls.append(high)
            if high == 4:
                return 2
            return (18 if hit else 1) if rolls.count(20) == 1 else save

    monkeypatch.setattr(dice, "_rng", ClawDice())
    begun = _run(
        "begin_srd_creature_attack",
        state,
        participant_data=data,
        actor="ghoul_0",
        target="hero",
        attack_name="Claw",
        attack_modifier=4,
        attack_mode="melee",
        damage_expression="d4",
        damage_modifier=2,
        damage_type="slashing",
        normal_range=5,
        long_range=5,
        attack_distance=3,
        use_multiattack=False,
        actor_can_see_target=True,
        target_can_see_actor=True,
        target_is_charmer=False,
        fear_source_in_sight=False,
        advantage=False,
        disadvantage=False,
        attack_uses_weapon=False,
    )
    pending = begun.result["context"]["_pending_action_id"]
    saved = deepcopy(state[f"{SRD}.state"]["pending_srd_actions"][pending]["attack_roll"])
    assert rolls == [20] and state[f"{SRD}.entities"]["hero"]["hp"] == hp
    result = _run(
        "complete_srd_pending_action", state, participant_data=data, pending_action_id=pending
    ).result
    context = result["context"]
    assert context["_attack_roll"] == saved
    assert context["_ghoul_claw_save_outcome"] == expected
    rows = {r["label"]: r["value"] for r in result["result_card"]["details"]}
    assert not any("Ghoul Bite" in label or "Poison Jab" in label for label in rows)
    target = state[f"{SRD}.entities"]["hero"]
    assert target["hp"] == hp - (4 if hit else 0)
    assert ("paralyzed" in target["conditions"]) is (expected == "failure")
    if expected in ("failure", "success"):
        roll = context["_ghoul_claw_save_roll"]
        assert len(roll["results"]) == 1 and roll["results"][0] == save
        assert rolls == [20, 4, 20]
        assert expected in rows["Ghoul Claw save"] and "DC 10" in rows["Ghoul Claw save"]
        assert str(context["_ghoul_claw_save_total"]) in rows["Ghoul Claw save"]
        if expected == "failure":
            sid = context["_ghoul_claw_source_id"]
            source = state[f"{SRD}.state"]["timed_condition_sources"][sid]
            assert source["native_body_source"] == "ghoul_0"
            assert source["native_body_source_sequence"] == source["sequence"]
            assert sid in rows["Ghoul Claw paralysis source"]
        else:
            assert context["_ghoul_claw_source_id"] is None
            assert "Ghoul Claw paralysis source" not in rows
    else:
        assert context["_ghoul_claw_save_roll"] is None
        assert context["_ghoul_claw_source_id"] is None
        assert "Ghoul Claw save" not in rows and "Ghoul Claw paralysis source" not in rows
        assert rolls == ([20, 4] if hit else [20])
