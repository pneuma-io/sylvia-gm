"""Retained Flame Blade uses real prepared object AC, attack dice, raw Fire and persistence."""

from copy import deepcopy
from functools import lru_cache
from unittest.mock import patch

import pytest
import yaml
from scripts.srd_full.srd_flame_blade_objects import PUBLIC_TOOLS, bind_runtime_consumers
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import validate_ruleset_data
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_flame_blade as flame
from tests import test_srd_flame_blade_light as light
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _entity


@lru_cache
def _runtime():
    original = light._runtime()[0]
    text = yaml.dump(original, Dumper=yaml.CSafeDumper, sort_keys=False)
    runtime = _load_runtime(bind_runtime_consumers(text))
    changed = {}
    with patch.object(flame.reactions, "ORDINARY_SUPPORT_TOOLS", PUBLIC_TOOLS):
        flame.reactions._ordinary_support_fences(runtime["tools"], changed)
    runtime["tools"].update(changed)
    assert not validate_ruleset_data(runtime), validate_ruleset_data(runtime)
    assert runtime["tools"]["d20_test"] == original["tools"]["d20_test"]
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    _runtime()
    monkeypatch.setattr(flame, "_runtime", _runtime)
    monkeypatch.setattr(
        flame, "PUBLIC_TOOLS", flame.PUBLIC_TOOLS | PUBLIC_TOOLS | {"prepare_srd_spell_object"}
    )
    flame._mechanic.cache_clear()
    flame._archive.cache_clear()
    monkeypatch.setattr(flame.budget, "_mechanic", flame._mechanic)
    monkeypatch.setattr(
        flame.cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"]
    )
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", flame.attacks.Rolls())
    actual = package_runtime.execute_tool

    def diagnosed(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnosed)


def _case(slot=2):
    state, data, params = flame._case(slot)
    _run(
        "prepare_srd_spell_object",
        state,
        object_id="orb",
        material="wood",
        size="large",
        durability="resilient",
    )
    flame._cast(state, data, params)
    return state, data, params


def _profile(state):
    return next(r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "orb")[
        "metadata"
    ]["srd_spell_object"]


def _attack(state, data):
    return _run(
        "use_srd_flame_blade_object",
        state,
        participant_data=data,
        caster_state="hero",
        caster="hero",
        object_id="orb",
    ).result


@pytest.mark.parametrize(
    "roll,slot,raw,applied", [(10, 2, 13, 13), (20, 2, 22, 22), (1, 2, 0, 0), (10, 4, 19, 19)]
)
def test_one_real_object_roll_original_slot_modifier_once_and_one_durability_event(
    monkeypatch, roll, slot, raw, applied
):
    state, data, _ = _case(slot)
    source = deepcopy(flame._source(state))
    rolls = flame.attacks.Rolls(attack=roll)
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _attack(state, data)
    context = result["context"]
    assert context["_object_damage_rolled"] == raw
    assert context["_object_damage_applied"] == applied
    assert _profile(state)["hp"] == 27 - applied
    assert "orb" not in state[f"{SRD}.entities"]
    assert flame._source(state) == source
    assert state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{slot}"] == 2
    assert rolls.sides == [20] + ([] if roll == 1 else [6] * (slot + 1) * (2 if roll == 20 else 1))
    assert result["result_card"]
    assert context["_flame_object_target"] == "orb"


def test_actual_object_range_rejects_before_roll_and_retains_action_source_and_hp(monkeypatch):
    state, data, _ = _case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(r for r in scene["entities"] if r["id"] == "orb")["position"] = [10, 0, 0]
    before = deepcopy(state)
    rolls = flame.attacks.Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    with pytest.raises(RulesetPreconditionRejected, match="range"):
        _attack(state, data)
    assert state == before and rolls.sides == []


def _enfeeble(state, data, *, caster_id="mage", position=(40, 0, 0)):
    """Install the genuine prepared caster's Ray through its paid public source path."""
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(
        _entity(caster_id, list(position), participant_ref=caster_id, creature_type="humanoid")
    )
    _run(
        "prepare_srd_creature",
        state,
        creature_id=caster_id,
        name="Ray caster",
        source="Synthetic spellcaster.",
        armor_class=12,
        hit_points=25,
        speed=30,
        initiative_modifier=0,
        size_rank=2,
        constitution_save=6,
        spellcasting_ability="intelligence",
        spellcasting_modifier=4,
        spell_attack_bonus=7,
        spell_save_dc=15,
        spells=[{"spell": "ray_of_enfeeblement", "uses_per_day": 1}],
    )
    _, _, params = flame.cards._spell_case("ray_of_enfeeblement")
    params.pop("caster")
    params.pop("slot_resource")
    params.update(
        caster_state=caster_id, target="hero", targets=["hero"], forego_save_targets=["hero"]
    )
    state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"] = dict.fromkeys(data["abilities"], 0)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)


def test_actual_paid_ray_penalty_occurs_once_after_fire_dice_plus_modifier(monkeypatch):
    state, data, _ = _case()
    _enfeeble(state, data)

    class Rolls:
        sides = []

        def randint(self, _low, high):
            self.sides.append(high)
            return 10 if high == 20 else 8 if high == 8 else 3

    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    context = _attack(state, data)["context"]
    assert context["_object_damage_roll"]["total"] == 9
    assert context["_object_damage_rolled"] == 13
    assert context["_object_damage_applied"] == 5
    assert rolls.sides == [20, 6, 6, 6, 8]
    assert _profile(state)["hp"] == 22


def test_paid_public_object_magic_reopens_repeats_on_real_clock_and_retries(tmp_path, monkeypatch):
    monkeypatch.setattr(flame.public_casting, "bundled_srd_archive", flame._archive)
    monkeypatch.setattr(flame.public_casting, "_spell_case", lambda _spell: flame._case())
    path = tmp_path / "flame-object.sqlite"
    receipts = []
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = flame.public_casting._create_spell_campaign(owner, "flame_blade")
        receipts.append(
            _act(
                owner,
                "prepare_srd_spell_object",
                object_id="orb",
                material="wood",
                size="large",
                durability="resilient",
            )
        )
        receipts.append(_act(owner, "cast_spell_outside_combat", **params))
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        receipts.append(
            _act(
                owner,
                "use_srd_flame_blade_object",
                caster="hero",
                caster_state="hero",
                object_id="orb",
            )
        )
        assert receipts[-1][0].result["context"]["_object_damage_applied"] == 13
        receipts.append(_act(owner, "advance_srd_lasting_time", elapsed_rounds=1))
        receipts.append(
            _act(
                owner,
                "use_srd_flame_blade_object",
                caster="hero",
                caster_state="hero",
                object_id="orb",
            )
        )
        assert receipts[-1][0].result["context"]["_object_hp_after"] == 1
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        for receipt, command in receipts:
            assert receipt.status is ActionStatus.COMMITTED
            assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == saved


def test_repeated_binding_preserves_signature_cards_roll_owner_and_actual_scalar_cap(monkeypatch):
    runtime, encoded = _runtime()
    repeated = _load_runtime(bind_runtime_consumers(encoded.decode()))
    changed = {}
    with patch.object(flame.reactions, "ORDINARY_SUPPORT_TOOLS", PUBLIC_TOOLS):
        flame.reactions._ordinary_support_fences(repeated["tools"], changed)
    repeated["tools"].update(changed)
    assert repeated == runtime
    assert {p["name"] for p in runtime["tools"]["use_srd_flame_blade_object"]["parameters"]} == {
        "caster",
        "caster_state",
        "object_id",
        "lasting_state",
    }
    state, data, _ = _case()
    budgets = []
    actual = flame.ExecutionBudget.consume_operation

    def count(budget):
        if not budgets or budgets[-1] is not budget:
            budgets.append(budget)
        actual(budget)

    monkeypatch.setattr(flame.ExecutionBudget, "consume_operation", count)
    assert _attack(state, data)["result_card"]
    assert budgets and all(b.max_operations == 10000 for b in budgets)
    print("Flame object scalar operations:", max(b.operations for b in budgets))

    def nodes(value):
        if isinstance(value, dict):
            return 1 + sum(nodes(k) + nodes(v) for k, v in value.items())
        if isinstance(value, list):
            return 1 + sum(map(nodes, value))
        return 1

    print("Flame object expanded-node delta:", nodes(runtime) - nodes(light._runtime()[0]))
