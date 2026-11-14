"""A paid, living Minor Elementals source contributes to real later attack packets."""

import hashlib
import importlib.util
import sys
from copy import deepcopy
from pathlib import Path

import pytest
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools

from sylvia_gm import dice
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_attack_budget import _attack
from tests.srd_full.test_srd_base_damage_receipt import actual_receipt_owner as actual_receipt_owner
from tests.srd_full.test_srd_base_damage_receipt import receipt_runtime as base_receipt_runtime
from tests.srd_full.test_srd_spell_damage_consumers import _enfeeble, _grant, _record_rolls
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene

SRD = cards.SRD


def _with_environment_hearing_guard(text):
    """Compose only the immutable environment owner's repaired scalar hearing binder."""
    source = Path(
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-minor-attack-packets/srd_spell_environment.py")
    )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == (
        "967be28463411461220dbdce1f1a3838b50933a32b1068663fd3d83c17e0b2b9"
    )
    spec = importlib.util.spec_from_file_location("scripts._immutable_environment_hearing", source)
    module = importlib.util.module_from_spec(spec)
    original = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = original
    runtime = _load_runtime(text)
    cast = runtime["tools"]["cast_spell"]
    module._bind_environment_hearing_guard(cast)
    return _replace_tools(text, {"cast_spell": cast})


@pytest.fixture(scope="module")
def receipt_runtime():
    _, encoded = base_receipt_runtime.__wrapped__()
    text = _with_environment_hearing_guard(encoded.decode())
    # The exploration alias must follow the actual repaired cast, just as the normal emitter does.
    from scripts.srd_full.srd_actor_spell_modifiers import refresh_exploration_runtime

    text = refresh_exploration_runtime(text)
    return _load_runtime(text), text.encode()


def _position(state, actor):
    return next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == actor
    )


def _minor_case(slot=4, curse=True, ray=False):
    """Reuse the immutable environment fixture's actual scene, grants and supporting floor."""
    state, data, minor = cards._spell_case("conjure_minor_elementals")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _admit_scene(state)
    state[f"{SRD}.state"].setdefault("turn_effects", [])
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(
        {
            "id": "floor",
            "map_id": "orders",
            "active": True,
            "volume": {"kind": "box", "min": [-500, -500, -1], "max": [500, 500, 0]},
            "movement": "blocking",
            "occupies_cells": False,
            "visibility": "opaque",
            "metadata": {"plane": "material", "ground": True, "material": "stone"},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )
    _position(state, "guard").update(position=[5, 0, 0])
    _position(state, "guard")["metadata"]["enemy_of"] = ["hero"]
    if curse:
        parameters = _grant(state, data, "bestow_curse")
        parameters.update(
            slot_resource="spell_slot_5",
            curse_mode="damage",
            targets=["guard"],
            forego_save_targets=["guard"],
        )
        budget._run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    if ray:
        _enfeeble(state, data)
    minor.update(environment_origin=[10, 0, 0], slot_resource=f"spell_slot_{slot}")
    budget._run("cast_spell_outside_combat", state, participant_data=data, **minor)
    return state, data


class ActualPacketFaces:
    def __init__(self, critical=False, ray=False, slot=4):
        self.critical = critical
        count = (slot - 2) * (2 if critical else 1)
        self.d8 = iter(([8] if ray else []) + [1] * count + [8, 8])

    def randint(self, low, high):
        assert low == 1
        return next(self.d8) if high == 8 else (20 if self.critical else 10) if high == 20 else 1


@pytest.mark.parametrize(
    "critical,ray,slot,damage_type",
    [
        (False, False, 4, "cold"),
        (False, True, 4, "acid"),
        (True, True, 4, "lightning"),
        (False, False, 6, "cold"),
    ],
)
def test_actual_spell_hit_qualifies_minor_raw_dice_before_curse_and_one_temp_hp_transition(
    monkeypatch, critical, ray, slot, damage_type
):
    state, data = _minor_case(slot, ray=ray)
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(
        hp=1000,
        max_hp=1000,
        temp_hp=1000,
        armor_class=1,
        damage_immunities=["fire"],
        damage_resistances=[damage_type, "necrotic"],
    )
    parameters = _grant(state, data, "fire_bolt")
    monkeypatch.setattr(dice, "_rng", ActualPacketFaces(critical, ray, slot))
    rolls = _record_rolls(monkeypatch)
    result = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(parameters | {"minor_elementals_damage_type": damage_type}),
    ).result
    raw = [r for r in rolls if r.sides == 10][0]
    d8 = [r for r in rolls if r.sides == 8]
    if ray:
        penalty, minor, curse = d8
        assert penalty.count == 1 and penalty.total == 8
    else:
        minor, curse = d8
    assert minor.count == (slot - 2) * (2 if critical else 1)
    assert minor.results == (1,) * minor.count
    assert curse.count == (2 if critical else 1)
    carry = max(0, 8 - raw.total) if ray else 0
    minor_amount = max(0, minor.total - carry)
    curse_amount = max(0, curse.total - max(0, carry - minor.total))
    expected = minor_amount // 2 + curse_amount // 2
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 1000 and guard["temp_hp"] == 1000 - expected
    assert len([r for r in rolls if r.sides == 20]) == 1
    assert len(result["result_card"]["details"]) == 1
    assert (
        f"Damage {expected} (HP 0; temporary HP {expected})"
        in result["result_card"]["details"][0]["value"]
    )
    assert result["status"] == "committed"


def test_actual_weapon_hit_combines_favor_minor_curse_and_one_ray_penalty(monkeypatch):
    state, data = _minor_case(ray=True)
    data["classes"] = [{"id": "wizard", "level": 8}, {"id": "paladin", "level": 9}]
    data["spellcasting_grants"].append(
        {"source": "paladin", "ability": "charisma", "spells": ["divine_favor"]}
    )
    hero = state[f"{SRD}.entities"]["hero"]
    hero["prepared_spells"].append("divine_favor")
    _, _, favor = cards._spell_case("divine_favor")
    favor.pop("target")
    budget._run("cast_spell_outside_combat", state, participant_data=data, **favor)
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(
        hp=1000,
        max_hp=1000,
        temp_hp=1000,
        armor_class=1,
        damage_immunities=["piercing"],
        damage_resistances=["radiant", "cold", "necrotic"],
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

    class ActualWeaponFaces(ActualPacketFaces):
        def randint(self, low, high):
            return 4 if high == 4 else super().randint(low, high)

    monkeypatch.setattr(dice, "_rng", ActualWeaponFaces(ray=True))
    rolls = _record_rolls(monkeypatch)
    result = _attack(state, data, minor_elementals_damage_type="cold").result
    context = result["context"]
    penalty, minor, curse = [r for r in rolls if r.sides == 8]
    (base,) = [r for r in rolls if r.sides == 6]
    (favor_roll,) = [r for r in rolls if r.sides == 4]
    carry = max(0, penalty.total - (base.total + context["_damage_modifier"]))
    favor_amount = max(0, favor_roll.total - carry)
    carry = max(0, carry - favor_roll.total)
    minor_amount = max(0, minor.total - carry)
    carry = max(0, carry - minor.total)
    curse_amount = max(0, curse.total - carry)
    expected = favor_amount // 2 + minor_amount // 2 + curse_amount // 2
    assert penalty.results == (8,) and minor.results == (1, 1) and curse.results == (8,)
    assert context["_divine_favor_amount"] == favor_amount
    assert context["_damage_applied"] == expected
    assert context["_attack_roll"]["count"] == 2  # Ray gives this Strength attack Disadvantage.
    assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == 1000 - expected
    assert result["status"] == "committed"


def test_actual_critical_form_hit_combines_poison_minor_curse_and_one_ray_penalty(
    monkeypatch, receipt_runtime
):
    from tests.srd_full.test_srd_attack_roll_components import _npc_spell, _prepared_caster
    from tests.srd_full.test_srd_spell_wave3_information import _entity

    state, data = _minor_case(ray=True)
    _prepared_caster(state, "changer", "polymorph", 4)
    supplies, _, _ = cards._spell_case("polymorph")
    changer_actor = state[f"{SRD}.entities"]["changer"]
    changer_actor["spell_materials"] = deepcopy(
        supplies[f"{SRD}.entities"]["hero"]["spell_materials"]
    )
    changer_actor["inventory"] = [
        material["inventory_item"] for material in changer_actor["spell_materials"]
    ]
    changer = _entity("changer", [-5, 0, 0], participant_ref="changer", creature_type="humanoid")
    changer["map_id"] = _position(state, "hero")["map_id"]
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(changer)
    _npc_spell(
        state,
        data,
        "changer",
        "polymorph",
        "hero",
        transformation_form="venomous_snake",
        target_character="hero",
    )
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(
        hp=1000,
        max_hp=1000,
        temp_hp=1000,
        armor_class=1,
        damage_immunities=["poison"],
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
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="hero",
        current_actor="hero",
        action_available=True,
        attacks_remaining=0,
        normal_action_spent=False,
    )
    monkeypatch.setattr(dice, "_rng", ActualPacketFaces(critical=True, ray=True))
    rolls = _record_rolls(monkeypatch)
    result = budget._run(
        "attack_srd_transformed_form",
        state,
        participant_data=data,
        actor_state="hero",
        target_state="guard",
        attack_name="Bite",
        attack_distance=5,
        actor_can_see_target=True,
        target_can_see_actor=True,
        target_is_charmer=False,
        fear_source_in_sight=False,
        minor_elementals_damage_type="cold",
    ).result["context"]
    penalty, minor, curse = [r for r in rolls if r.sides == 8]
    (base,) = [r for r in rolls if r.sides == 4]
    (poison,) = [r for r in rolls if r.sides == 6]
    runtime, _ = receipt_runtime
    body = runtime["definitions"]["beast_forms"]["venomous_snake"]["attacks"]["Bite"]
    raw_base = base.total + body["damage_modifier"]
    carry = max(0, penalty.total - raw_base)
    poison_amount = max(0, poison.total + body["extra_damage_modifier"] - carry)
    carry = max(0, carry - poison.total - body["extra_damage_modifier"])
    minor_amount = max(0, minor.total - carry)
    carry = max(0, carry - minor.total)
    curse_amount = max(0, curse.total - carry)
    expected = max(0, raw_base - penalty.total) + minor_amount // 2 + curse_amount // 2
    assert penalty.results == (8,) and minor.results == (1, 1, 1, 1)
    assert curse.results == (8, 8) and poison.results == (1, 1)
    assert result["_form_extra_damage_roll"]["results"] == list(poison.results)
    assert result["_form_extra_damage_amount"] == poison_amount
    assert result["_effective_damage"] == expected
    assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == 1000 - expected
    assert state[f"{SRD}.entities"]["guard"]["concentration_spell_id"] == "guidance"
    assert len([r for r in rolls if r.sides == 20]) == 2


@pytest.mark.parametrize("event", ["outside", "ended", "miss", "miss_damage"])
def test_actual_minor_source_does_not_roll_for_outside_ended_or_missed_attack(monkeypatch, event):
    state, data = _minor_case(curse=event != "miss_damage")
    if event == "outside":
        _position(state, "guard")["position"] = [100, 0, 0]
    elif event == "ended":
        budget._run("end_concentration", state, caster_state="hero")
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(
        hp=1000,
        max_hp=1000,
        temp_hp=1000,
        armor_class=1,
        damage_immunities=["acid" if event == "miss_damage" else "fire"],
    )

    class ActualUnusedMinorFaces:
        def randint(self, low, high):
            assert low == 1
            return (1 if event.startswith("miss") else 10) if high == 20 else high

    monkeypatch.setattr(dice, "_rng", ActualUnusedMinorFaces())
    rolls = _record_rolls(monkeypatch)
    result = budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(
            _grant(state, data, "acid_arrow" if event == "miss_damage" else "fire_bolt")
            | {"minor_elementals_damage_type": "cold"}
        ),
    ).result
    d8 = [r for r in rolls if r.sides == 8]
    no_curse_damage = event in ("miss", "miss_damage")
    assert [r.results for r in d8] == ([] if no_curse_damage else [(8,)])
    assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == (1000 if no_curse_damage else 992)
    assert result["status"] == "committed"


@pytest.mark.parametrize("success", [True, False])
def test_actual_ordinary_prismatic64_keeps_packet_budget_after_minor_composition(
    monkeypatch, success
):
    from tests.srd_full.test_srd_spell_special_damage import (
        _entity,
        _scene,
        test_public_prismatic_complete64_scene_creatures_and_packets,
    )

    actual_case = cards._spell_case

    def actual_scene_case(spell):
        state, data, parameters = actual_case(spell)
        state[f"{SRD}.entities"]["hero"]["creature_type"] = "humanoid"
        state[f"{SRD}.state"]["spatial_scene"] = _scene(
            _entity("hero", [0, 0, 0], participant_ref="hero", creature_type="humanoid"),
            _entity("guard", [20, 0, 0], participant_ref="guard", creature_type="humanoid"),
        )
        return state, data, parameters

    monkeypatch.setattr(cards, "_spell_case", actual_scene_case)
    test_public_prismatic_complete64_scene_creatures_and_packets(monkeypatch, success)
