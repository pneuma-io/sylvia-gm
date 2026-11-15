"""Saved native Bite completion retains its actual secondary dice and received packet."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_runtime_defaults import compact_runtime_defaults
from scripts.srd_full.srd_spell_trigger_composition import _effect_lists
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from tests import test_srd_spell_summoning as summoning
from tests.srd_full.test_srd_action_budget import SRD, _run


@pytest.fixture(scope="module")
def _created_undead_runtime():
    runtime, _ = summoning._created_undead_runtime.__wrapped__()
    parent = Path(
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-pending-ghoul-feedback/ruleset.yaml")
    )
    raw = parent.read_bytes()
    assert sha256(raw).hexdigest() == (
        "20c50891a72da87bb2f0156b73512e6c2de227b264aa3567f448c0d080713756"
    )
    actual = _load_runtime(raw.decode())["tools"]
    while True:
        missing = {
            effect["call"]
            for tool in runtime["tools"].values()
            for branch in _effect_lists(tool["effects"])
            for effect in branch
            if "call" in effect
        } - runtime["tools"].keys()
        if not missing:
            break
        assert missing <= actual.keys()
        runtime["tools"].update({name: deepcopy(actual[name]) for name in missing})
    encoded = compact_runtime_defaults(
        yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    ).encode()
    return runtime, encoded


@pytest.fixture
def created_undead_owner(monkeypatch, _created_undead_runtime):
    return summoning.created_undead_owner.__wrapped__(monkeypatch, _created_undead_runtime)


@pytest.mark.parametrize(
    "outcome,immune", [("critical_hit", False), ("hit", True), ("miss", False)]
)
def test_saved_bite_returns_actual_secondary_roll_components_and_card(
    created_undead_owner, monkeypatch, outcome, immune
):
    state, data = summoning._ghoul_bite_case()
    summoning._command_ghoul_attack_turn(state, data)
    target = state[f"{SRD}.entities"]["other"]
    if immune:
        target["damage_immunities"] = ["necrotic"]
    hp = target["hp"]
    rolls = []

    class BiteDice:
        def randint(self, low, high):
            assert low == 1 and high in (6, 20)
            rolls.append(high)
            if high == 20:
                return {"critical_hit": 20, "hit": 18, "miss": 1}[outcome]
            count = rolls.count(6)
            return 3 if count <= (2 if outcome == "critical_hit" else 1) else 4

    monkeypatch.setattr(dice, "_rng", BiteDice())
    begun = _run(
        "begin_srd_creature_attack",
        state,
        actor="ghoul_0",
        target="other",
        attack_name="Bite",
        attack_modifier=4,
        attack_mode="melee",
        damage_expression="d6",
        damage_modifier=2,
        damage_type="piercing",
        normal_range=5,
        long_range=5,
        attack_distance=5,
        use_multiattack=True,
        actor_can_see_target=True,
        target_can_see_actor=True,
        target_is_charmer=False,
        fear_source_in_sight=False,
        advantage=False,
        disadvantage=False,
        attack_uses_weapon=False,
    )
    pending = begun.result["context"]["_pending_action_id"]
    saved_roll = deepcopy(state[f"{SRD}.state"]["pending_srd_actions"][pending]["attack_roll"])
    assert state[f"{SRD}.entities"]["other"]["hp"] == hp
    result = _run("complete_srd_pending_action", state, pending_action_id=pending).result
    context = result["context"]
    assert context["_attack_roll"] == saved_roll and context["_attack_outcome"] == outcome
    assert rolls.count(20) == 1
    names = (
        "_ghoul_necrotic_damage_roll",
        "_ghoul_necrotic_damage_amount",
        "_ghoul_piercing_damage_amount",
        "_component_1_effective_damage",
        "_component_2_effective_damage",
    )
    if outcome == "miss":
        assert all(context[name] is None for name in names)
        assert rolls == [20]
        assert state[f"{SRD}.entities"]["other"]["hp"] == hp
        assert all("Ghoul Bite" not in row["label"] for row in result["result_card"]["details"])
        return
    expected = (8, 8) if outcome == "critical_hit" else (5, 0)
    assert len(context["_ghoul_necrotic_damage_roll"]["results"]) == (
        2 if outcome == "critical_hit" else 1
    )
    assert context["_ghoul_necrotic_damage_amount"] == (8 if outcome == "critical_hit" else 4)
    assert context["_ghoul_piercing_damage_amount"] == expected[0]
    assert tuple(context[name] for name in names[-2:]) == expected
    assert context["_damage_applied"] == sum(expected)
    assert state[f"{SRD}.entities"]["other"]["hp"] == hp - sum(expected)
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 1
    rows = {row["label"]: row["value"] for row in result["result_card"]["details"]}
    assert str(context["_ghoul_necrotic_damage_amount"]) in rows["Ghoul Bite Necrotic"]
    assert all(str(amount) in rows["Ghoul Bite components"] for amount in expected)
    assert not any("Poison Jab" in label for label in rows)
