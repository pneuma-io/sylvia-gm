"""Real saved hits coalesce typed game-roll components before one receiving lifecycle."""

import hashlib
import importlib.util
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts import build_srd_beast_forms as native_beasts
from scripts import srd_spell_summoning as undead
from scripts import srd_spell_wards as wards
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_attack_hit_riders import bind_runtime, restore_runtime
from scripts.srd_full.srd_mirror_image_consumers import _weapon
from scripts.srd_full.srd_parameter_declarations import compact_runtime
from scripts.srd_full.srd_received_damage_reactions import bind_receiving_events
from scripts.srd_full.srd_warding_bond_damage import compose_warding_bond_damage

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime import _effect_executor
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_attack_hit_riders import BEGIN, Rolls, _begin_hit, _finish, _hit_case
from tests.srd_full.test_srd_attack_hit_riders import (
    test_authenticated_saved_packet_queues_earned_damage_before_outer_cleanup as queue_case,
)
from tests.srd_full.test_srd_spell_damage_consumers import _enfeeble, _record_rolls
from tests.srd_full.test_srd_spell_damage_riders import _cast
from tests.srd_full.test_srd_spell_damage_riders import _runtime as source_runtime
from tests.srd_full.test_srd_spell_wave3_information import _entity
from tests.srd_full.test_srd_warding_bond_damage import _ring


@lru_cache
def _runtime():
    assert callable(native_beasts.source_form_profile)
    assert callable(undead._zombie_actor_tools)
    assert callable(undead._undead_fortitude_tools)
    assert callable(wards.extend_runtime)
    runtime = deepcopy(source_runtime()[0])
    for kind, actor in (("character_attack", "actor_state"), ("creature_attack", "actor")):
        name = "complete_srd_" + kind
        runtime["tools"][name] = _weapon(runtime["tools"][name], actor, "target")
    text = wards.extend_runtime(yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False))
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    runtime["tools"].update(undead._zombie_actor_tools(runtime["tools"]))
    runtime["tools"].update(undead._undead_fortitude_tools())
    changed = {}
    compose_warding_bond_damage(runtime["tools"], changed)
    runtime["tools"].update(changed)
    text = undead.bind_undead_fortitude_damage_consumers(
        yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    )
    original = bind_receiving_events(text)
    bound = bind_runtime(original)
    assert bind_runtime(bound) == bound
    assert yaml.load(restore_runtime(bound), Loader=yaml.CSafeLoader) == yaml.load(
        original, Loader=yaml.CSafeLoader
    )
    runtime = yaml.load(bound, Loader=yaml.CSafeLoader)
    return runtime, compact_runtime(bound).encode()


@lru_cache
def _mechanic(name):
    runtime, encoded = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=encoded,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", _mechanic)
    monkeypatch.setattr(cards, "_spell_definitions", lambda: _runtime()[0]["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Rolls(15, 1))


def _mixed_case(spell, slot=1):
    state, data, parameters = _hit_case(spell, slot)
    data["classes"] = [
        {"id": "wizard", "level": 9},
        {"id": "warlock", "level": 3},
        {"id": "paladin", "level": 5},
    ]
    return state, data, parameters


def _admit_spell(state, data, spell):
    """Use the ordinary fixture's actual class grant and physical material declarations."""
    supplies, source_data, parameters = cards._spell_case(spell)
    hero = state[f"{SRD}.entities"]["hero"]
    hero["prepared_spells"].append(spell)
    grant = deepcopy(source_data["spellcasting_grants"][0])
    existing = next(
        (g for g in data["spellcasting_grants"] if g["source"] == grant["source"]), None
    )
    if existing is None:
        data["spellcasting_grants"].append(grant)
    else:
        existing["spells"].append(spell)
    if "wizard_spellbook" in source_data:
        data.setdefault("wizard_spellbook", []).append(spell)
    for item in supplies[f"{SRD}.entities"]["hero"]["spell_materials"]:
        material = deepcopy(item)
        material["id"] = spell + "_" + item["id"]
        hero["spell_materials"].append(material)
        hero["inventory"].append(material["inventory_item"])
    parameters["material_item_ids"] = [spell + "_" + i for i in parameters["material_item_ids"]]
    return parameters


def _curse(state, data, target="guard"):
    spell = _admit_spell(state, data, "bestow_curse")
    spell.update(
        target=target,
        targets=[target],
        forego_save_targets=[target],
        slot_resource="spell_slot_5",
        curse_mode="damage",
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **spell)


def _observe_packets(monkeypatch):
    calls = []
    original = _effect_executor._EFFECT_HANDLERS["apply_damage"]

    def observe(body, effect, context, executor):
        calls.append(
            (
                context.current_tool,
                context.vars.get("_effective_damage"),
                {
                    field: context.vars.get(field)
                    for field in (
                        "_source_base_effective_damage",
                        "_component_1_effective_damage",
                        "_component_2_effective_damage",
                    )
                },
            )
        )
        return original(body, effect, context, executor)

    monkeypatch.setitem(_effect_executor._EFFECT_HANDLERS, "apply_damage", observe)
    return calls


def test_paid_hex_and_curse_odd_necrotic_rolls_share_resistance_once(monkeypatch):
    state, data, mark = _mixed_case("hex")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _curse(state, data)
    _cast(state, data, mark | {"target": "guard", "target_distance": 5})
    target = state[f"{SRD}.entities"]["guard"]
    target.update(damage_resistances=["necrotic"], temp_hp=10)
    state[f"{SRD}.combat"].update(
        encounter_active=True, turn_active=True, active_actor="hero", current_actor="hero"
    )
    rolls = _record_rolls(monkeypatch)
    packets = _observe_packets(monkeypatch)
    hit = _begin_hit(state, data, pause=False)
    result = _finish(state, data, hit)
    necrotic = next(r for r in result["_hit_type_receipts"] if r["damage_type"] == "necrotic")
    assert necrotic == {
        "damage_type": "necrotic",
        "amount": 2,
        "effective": 1,
        "component_count": 2,
    }
    assert result["_hit_rider_receipts"][0]["effective"] is None
    assert result["_hit_rider_receipts"][0]["effective_type_amount"] == 1
    assert [(r.sides, r.count) for r in rolls] == [(20, 1), (6, 1), (8, 1), (6, 1)]
    assert len(packets) == 1
    piercing = next(r for r in result["_hit_type_receipts"] if r["damage_type"] == "piercing")
    assert packets[0][2]["_source_base_effective_damage"] == piercing["effective"]
    assert isinstance(packets[0][2]["_source_base_effective_damage"], int)
    target = state[f"{SRD}.entities"]["guard"]
    assert target["hp"] == 100
    assert target["temp_hp"] == 10 - sum(r["effective"] for r in result["_hit_type_receipts"])
    assert "received for this damage type 1" in str(result["_pending_card_details"])


def test_real_bond_reflects_added_smite_and_same_type_mark_receipts_once(monkeypatch):
    state, data, params = _mixed_case("searing_smite", 2)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _curse(state, data)
    mark = _admit_spell(state, data, "hex")
    _cast(state, data, mark | {"target": "guard", "target_distance": 5})
    for actor in ("hero", "guard"):
        _ring(state, actor)
    data["classes"].append({"id": "cleric", "level": 3})
    data["level"] = 20
    bond = _admit_spell(state, data, "warding_bond")
    bond.update(material_item_ids=["hero_ring"])
    _run("cast_spell_outside_combat", state, participant_data=data, **bond)
    target, caster = (state[f"{SRD}.entities"][actor] for actor in ("guard", "hero"))
    target.update(temp_hp=20)
    caster.update(hp=100, max_hp=100, temp_hp=20, damage_resistances=["necrotic"])
    state[f"{SRD}.combat"].update(
        encounter_active=True, turn_active=True, active_actor="hero", current_actor="hero"
    )
    rolls = _record_rolls(monkeypatch)
    packets = _observe_packets(monkeypatch)
    hit = _begin_hit(state, data)
    _finish(state, data, hit)
    child = _run(
        BEGIN, state, participant_data=data, **(params | {"rider_pending_action_id": hit})
    ).result["context"]["_pending_action_id"]
    _finish(state, data, child)
    result = _finish(state, data, hit)
    target, caster = (state[f"{SRD}.entities"][actor] for actor in ("guard", "hero"))
    types = {r["damage_type"]: r["effective"] for r in result["_hit_type_receipts"]}
    assert types["necrotic"] == 1 and types["fire"] == 1
    assert target["temp_hp"] == 20 - sum(types.values())
    assert caster["temp_hp"] == 20 - types["piercing"] - types["fire"]
    assert len(packets) == 2
    assert [r.count for r in rolls if r.sides == 20] == [1, 1]
    assert caster["concentration_spell_id"] == "hex"
    assert state[f"{SRD}.state"]["pending_srd_actions"] == {}


def test_real_favor_and_shining_share_one_odd_radiant_mitigation(monkeypatch):
    state, data, parameters = _mixed_case("shining_smite", 2)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    favor = _admit_spell(state, data, "divine_favor")
    favor.pop("target")
    _run("cast_spell_outside_combat", state, participant_data=data, **favor)
    target = state[f"{SRD}.entities"]["guard"]
    target.update(temp_hp=10, damage_resistances=["radiant"])
    state[f"{SRD}.combat"].update(
        encounter_active=True, turn_active=True, active_actor="hero", current_actor="hero"
    )

    class ActualOddFaces(Rolls):
        def __init__(self):
            super().__init__(15, 1)
            self.d6 = iter([1, 2, 1])

        def randint(self, low, high):
            return next(self.d6) if high == 6 else super().randint(low, high)

    monkeypatch.setattr(dice, "_rng", ActualOddFaces())
    rolls = _record_rolls(monkeypatch)
    packets = _observe_packets(monkeypatch)
    hit = _begin_hit(state, data)
    _finish(state, data, hit)
    child = _run(
        BEGIN, state, participant_data=data, **(parameters | {"rider_pending_action_id": hit})
    ).result["context"]["_pending_action_id"]
    _finish(state, data, child)
    result = _finish(state, data, hit)
    radiant = next(r for r in result["_hit_type_receipts"] if r["damage_type"] == "radiant")
    assert radiant == {"damage_type": "radiant", "amount": 4, "effective": 2, "component_count": 2}
    assert result["_hit_rider_receipts"][0]["effective"] is None
    assert [(r.sides, r.count) for r in rolls] == [(20, 1), (6, 2), (6, 1), (4, 1)]
    assert len(packets) == 1
    assert packets[0][2]["_component_2_effective_damage"] is None
    target = state[f"{SRD}.entities"]["guard"]
    assert target["temp_hp"] == 10 - sum(r["effective"] for r in result["_hit_type_receipts"])


def test_paid_added_radiant_bypasses_real_source_zombie_fortitude(monkeypatch):
    state, data, parameters = _mixed_case("divine_smite")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    corpse = state[f"{SRD}.entities"]["other"]
    corpse.update(body_available=True, vital_parts_present=True, soul_willing=True, size_rank=2)
    _run(
        "apply_srd_damage",
        state,
        target="other",
        amount=100,
        adjustment=0,
        damage_type="bludgeoning",
        source_state_id="hero",
    )
    _run(
        "prepare_srd_source_zombie",
        state,
        participant_data=data,
        creature_id="zombie_one",
        corpse_state="other",
        source_caster="hero",
        other_language="draconic",
    )
    lasting = state[f"{SRD}.state"]
    lasting["spatial_scene"]["entities"].append(
        _entity("zombie_one", [5, 0, 0], participant_ref="zombie_one", creature_type="undead")
    )
    for row in lasting["spatial_scene"]["entities"]:
        if row["id"] == "guard":
            row["position"] = [200, 0, 0]
    state[f"{SRD}.combat"].update(
        encounter_active=True, turn_active=True, active_actor="hero", current_actor="hero"
    )
    monkeypatch.setattr(dice, "_rng", Rolls(15, 5))
    rolls = _record_rolls(monkeypatch)
    packets = _observe_packets(monkeypatch)
    from scripts.srd_full.srd_attack_hit_riders import INTENT

    from tests.srd_full.test_srd_reaction_windows import _parameters

    hit = _run(
        "begin_srd_character_attack",
        state,
        participant_data=data,
        **(_parameters() | {INTENT: True, "target": "zombie_one"}),
    ).result["context"]["_pending_action_id"]
    _finish(state, data, hit)
    child = _run(
        BEGIN, state, participant_data=data, **(parameters | {"rider_pending_action_id": hit})
    ).result["context"]["_pending_action_id"]
    _finish(state, data, child)
    result = _finish(state, data, hit)
    zombie = state[f"{SRD}.entities"]["zombie_one"]
    fortitude = zombie["last_undead_fortitude"]
    assert fortitude["radiant"] is True and fortitude["critical"] is False
    assert fortitude["success"] is False and fortitude["damage_taken"] == result["_damage_incoming"]
    assert zombie["hp"] == 0 and "dead" in zombie["conditions"] and zombie["death_tick"] is not None
    assert [(r.sides, r.count) for r in rolls] == [(20, 1), (8, 3), (6, 1)]
    assert len(packets) == 1


def test_coalesced_packet_preserves_real_received_damage_and_temp_hp_witness():
    queue_case()


def test_actual_ray_debt_can_erase_a_mark_without_receiving_another_type_amount(monkeypatch):
    state, data, mark = _mixed_case("hex")
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _curse(state, data)
    _cast(state, data, mark | {"target": "guard", "target_distance": 5})
    _enfeeble(state, data)
    state[f"{SRD}.combat"].update(
        encounter_active=True, turn_active=True, active_actor="hero", current_actor="hero"
    )

    class ActualDebtFaces(Rolls):
        def __init__(self):
            super().__init__(15, 1)
            self.d8 = iter([1, 8])

        def randint(self, low, high):
            return next(self.d8) if high == 8 else super().randint(low, high)

    monkeypatch.setattr(dice, "_rng", ActualDebtFaces())
    rolls = _record_rolls(monkeypatch)
    hit = _begin_hit(state, data, pause=False)
    result = _finish(state, data, hit)
    receipt = result["_hit_rider_receipts"][0]
    assert receipt["amount"] == 0 and receipt["effective"] == 0
    assert receipt["effective_type_amount"] == 0 and receipt["type_coalesced"] is False
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 100
    # The genuine Ray grants Disadvantage; its D20 owner uses the kept-roll primitive.
    assert result["_attack_roll"]["results"] == [15, 15]
    assert [(r.sides, r.count) for r in rolls] == [(6, 1), (8, 1), (6, 1), (8, 1)]


def test_successor_restores_exact_frozen_v1_before_upgrading_its_receipts():
    runtime, _ = _runtime()
    original = restore_runtime(yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False))
    path = (
        Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-saved-hit-type-receipts/input-1"))
        / "damage-consumers/.scratch/fc14-attack-hit-riders-frozen-v1"
        / "files/scripts/srd_attack_hit_riders.py"
    )
    assert hashlib.sha256(path.read_bytes()).hexdigest() == (
        "364d8605cb73c1bc0b14960d192551bb207cd826f1f2af44dd631679a422a8d6"
    )
    spec = importlib.util.spec_from_file_location("scripts._frozen_after_hit_v1", path)
    frozen = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(frozen)
    old = frozen.bind_runtime(original)
    assert yaml.load(restore_runtime(old), Loader=yaml.CSafeLoader) == yaml.load(
        original, Loader=yaml.CSafeLoader
    )
    assert yaml.load(bind_runtime(old), Loader=yaml.CSafeLoader) == runtime
