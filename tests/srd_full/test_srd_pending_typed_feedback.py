"""Saved creature hits retain the final typed leaf's actual dice and receiving feedback."""

from copy import deepcopy
from hashlib import sha256
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.srd_runtime_defaults import compact_runtime_defaults
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from tests import test_srd_spell_summoning as summoning
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_spell_summoning import (
    _insect_case,
    _insect_turn,
)


@pytest.fixture
def insect_owner(monkeypatch, _insect_runtime):
    """Use the existing owning mechanic with this test's exact dependency closure."""
    return summoning.insect_owner.__wrapped__(monkeypatch, _insect_runtime)


@pytest.fixture(scope="module")
def _insect_runtime():
    """Retain the actual later fall dependencies missing from the old retained baseline."""
    runtime, _ = summoning._insect_runtime.__wrapped__()
    parent = Path(
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-pending-typed-feedback/ruleset.yaml")
    )
    raw = parent.read_bytes()
    assert sha256(raw).hexdigest() == (
        "20c50891a72da87bb2f0156b73512e6c2de227b264aa3567f448c0d080713756"
    )
    actual = _load_runtime(raw.decode())["tools"]

    def calls(value):
        if isinstance(value, dict):
            if isinstance(value.get("call"), str):
                yield value["call"]
            for child in value.values():
                yield from calls(child)
        elif isinstance(value, list):
            for child in value:
                yield from calls(child)

    while True:
        missing = set(calls(runtime["tools"])) - runtime["tools"].keys()
        if not missing:
            break
        assert missing <= actual.keys()
        for name in sorted(missing):
            runtime["tools"][name] = deepcopy(actual[name])
    encoded = compact_runtime_defaults(
        yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    ).encode()
    return runtime, encoded


@pytest.mark.parametrize("immune,miss", [(False, False), (True, False), (False, True)])
def test_pending_poison_jab_returns_actual_components_and_card(
    monkeypatch,
    insect_owner,
    immune,
    miss,
):
    state, data, parameters = _insect_case("wasp", 6)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    _insect_turn(state)
    if immune:
        state[f"{SRD}.entities"]["guard"]["damage_immunities"] = ["poison"]
    monkeypatch.setattr(dice, "_rng", Random(31 if miss else 0))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    insect = state[f"{SRD}.entities"]["insect_one"]
    begun = _run(
        "begin_srd_creature_attack",
        state,
        actor="insect_one",
        target="guard",
        attack_name="Poison Jab",
        attack_modifier=insect["summon_attack_bonus"],
        attack_mode="melee",
        damage_expression="d6",
        damage_modifier=9,
        damage_type="piercing",
        normal_range=10,
        long_range=10,
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
    roll = deepcopy(state[f"{SRD}.state"]["pending_srd_actions"][pending]["attack_roll"])
    result = _run("complete_srd_pending_action", state, pending_action_id=pending).result
    context = result["context"]
    assert context["_attack_roll"] == roll
    if miss:
        assert context["_attack_outcome"] == "miss"
        assert all(
            context[name] is None
            for name in (
                "_insect_poison_damage_roll",
                "_insect_poison_damage_amount",
                "_component_1_effective_damage",
                "_component_2_effective_damage",
            )
        )
        assert all("Poison Jab" not in row["label"] for row in result["result_card"]["details"])
        assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
        return
    assert context["_attack_outcome"] == "hit"
    assert len(context["_insect_poison_damage_roll"]["results"]) == 1
    assert context["_insect_poison_damage_amount"] > 0
    assert context["_component_1_effective_damage"] > 0
    assert (context["_component_2_effective_damage"] == 0) is immune
    assert context["_damage_applied"] == (
        context["_component_1_effective_damage"] + context["_component_2_effective_damage"]
    )
    rows = {row["label"]: row["value"] for row in result["result_card"]["details"]}
    assert str(context["_insect_poison_damage_amount"]) in rows["Poison Jab Poison"]
    assert str(context["_component_1_effective_damage"]) in rows["Poison Jab components"]
    assert str(context["_component_2_effective_damage"]) in rows["Poison Jab components"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000 - context["_damage_applied"]
    assert state[f"{SRD}.combat"]["attacks_remaining"] == 2
