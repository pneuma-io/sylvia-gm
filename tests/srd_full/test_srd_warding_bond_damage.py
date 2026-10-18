"""Real Bond receipts preserve typed extras and one caster-side receiving packet."""

import hashlib
import importlib.util
import sys
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_actor_spell_modifiers import _emit
from scripts.srd_full.srd_spell_damage_consumers import extend_runtime
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.runtime import _effect_executor
from sylvia_gm.runtime._schema_validator import validate_ruleset_data
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards
from tests.srd_full.test_srd_base_damage_receipt import actual_receipt_owner as actual_receipt_owner
from tests.srd_full.test_srd_minor_attack_packets import _minor_case
from tests.srd_full.test_srd_minor_attack_packets import receipt_runtime as base_receipt_runtime
from tests.srd_full.test_srd_spell_damage_consumers import _grant, _record_rolls

SRD = cards.SRD


@pytest.fixture(scope="module")
def receipt_runtime():
    _, encoded = base_receipt_runtime.__wrapped__()
    source = Path(
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-warding-bond-damage/srd_spell_wards.py")
    )
    assert hashlib.sha256(source.read_bytes()).hexdigest() == (
        "438b899f1b34b222bb199359bd4d4df8d3109301d2ec38d8e4a59c1fd81f8c93"
    )
    spec = importlib.util.spec_from_file_location("scripts._immutable_bond_wards", source)
    module = importlib.util.module_from_spec(spec)
    original = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = original
    text = extend_runtime(module.extend_runtime(encoded.decode()))
    runtime = _load_runtime(text)
    # These exact frozen Ward3 declarations are not yet in Wave14's generated spell table.
    runtime["definitions"]["spells"].update(module.PROFILES)
    selectors = {}
    for name in (
        "cast_spell",
        "cast_spell_outside_combat",
        "begin_srd_spell_cast_internal",
        "begin_srd_spell_action",
    ):
        tool = runtime["tools"][name]
        for parameter in tool["parameters"]:
            if parameter["name"] == "spell" and "enum_values" in parameter:
                parameter["enum_values"] = list(runtime["definitions"]["spells"])
                selectors[name] = tool
    text = _emit(text, selectors)
    start = text.index("    # BEGIN GENERATED SPELL DEFINITIONS")
    end = text.index("    # END GENERATED SPELL DEFINITIONS", start)
    rows = yaml.safe_dump(runtime["definitions"]["spells"], sort_keys=False)
    text = (
        text[:start]
        + "    # BEGIN GENERATED SPELL DEFINITIONS\n"
        + "".join("    " + line for line in rows.splitlines(True))
        + text[end:]
    )
    assert not validate_ruleset_data(runtime)
    assert extend_runtime(text) == text
    return runtime, text.encode()


def _ring(state, actor):
    entity = state[f"{SRD}.entities"][actor]
    name = f"{actor}'s platinum ring"
    entity.setdefault("inventory", []).append(name)
    entity.setdefault("spell_materials", []).append(
        {
            "id": f"{actor}_ring",
            "inventory_item": name,
            "kind": "specific",
            "components": ["warding_bond.1"],
            "quantity": 1,
            "value_cp": 5000,
        }
    )
    budget._run("wear_srd_warding_bond_ring", state, actor_state=actor, material_id=f"{actor}_ring")


def _bond_case():
    state, data = _minor_case(curse=True)
    for actor in ("hero", "guard"):
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=1000, armor_class=1)
        _ring(state, actor)
    result = budget._run(
        "cast_srd_spell_ward",
        state,
        caster_state="hero",
        spell="warding_bond",
        slot_level=2,
        concentration_save_modifier=3,
        save_dc=18,
        target_state="guard",
        target_willing=True,
        caster_can_touch_target=True,
    ).result["context"]
    return state, data, result["_new_ward_id"]


class ActualBondFaces:
    def randint(self, low, high):
        assert low == 1
        return 8 if high == 8 else 10 if high == 20 else 1


@pytest.mark.parametrize("base_immune,caster_resists", [(False, False), (True, True)])
def test_actual_minor_curse_packet_reflects_post_target_types_and_one_caster_concentration(
    monkeypatch, base_immune, caster_resists
):
    state, data, source = _bond_case()
    hero = state[f"{SRD}.entities"]["hero"]
    hero["concentration_save_modifier"] = 50
    hero["damage_resistances"] = ["cold", "necrotic"] if caster_resists else []
    state[f"{SRD}.entities"]["guard"]["damage_immunities"] = ["fire"] if base_immune else []
    fire = _grant(state, data, "fire_bolt")
    monkeypatch.setattr(dice, "_rng", ActualBondFaces())
    rolls = _record_rolls(monkeypatch)
    packets = []
    dealt_events = []
    original = _effect_executor._EFFECT_HANDLERS["call"]

    def observe(body, effect, context, executor):
        if body == "apply_srd_reflected_warding_bond_damage":
            packets.append(
                (context.vars["_bond_receipt_source_id"], context.vars["_bond_damage_components"])
            )
        if context.current_tool == "apply_srd_reflected_warding_bond_damage" and body in {
            "end_srd_invisibility_damage_source",
            "end_srd_mislead_damage_source",
        }:
            dealt_events.append(body)
        return original(body, effect, context, executor)

    monkeypatch.setitem(_effect_executor._EFFECT_HANDLERS, "call", observe)
    budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(fire | {"minor_elementals_damage_type": "cold"}),
    )
    base = 0 if base_immune else 2
    assert packets == [
        (
            source,
            [
                {"damage_type": "cold", "amount": 8},
                {"damage_type": "necrotic", "amount": 4},
                {"damage_type": "fire", "amount": base},
            ],
        )
    ]
    assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == 1000 - base - 12
    reflected = 6 if caster_resists else 14
    assert state[f"{SRD}.entities"]["hero"]["temp_hp"] == 1000 - reflected
    assert [(r.sides, r.count) for r in rolls] == [(20, 1), (10, 4), (8, 2), (8, 1), (20, 1)]
    assert not dealt_events
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "conjure_minor_elementals"


def test_actual_same_type_components_share_caster_mitigation_without_second_roll(monkeypatch):
    state, data, _ = _bond_case()
    state[f"{SRD}.entities"]["hero"]["damage_resistances"] = ["fire"]
    fire = _grant(state, data, "fire_bolt")

    class ActualMatchingFaces:
        def __init__(self):
            self.d10 = iter([1, 1, 2, 2])
            self.d8 = iter([1, 1, 8])

        def randint(self, low, high):
            assert low == 1
            return next(self.d10) if high == 10 else next(self.d8) if high == 8 else 10

    monkeypatch.setattr(dice, "_rng", ActualMatchingFaces())
    rolls = _record_rolls(monkeypatch)
    budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(fire | {"minor_elementals_damage_type": "fire"}),
    )
    # Recipient Fire3+Fire1+Necrotic4; caster's Fire4 becomes2, plus Necrotic4.
    assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == 992
    assert state[f"{SRD}.entities"]["hero"]["temp_hp"] == 994
    assert len([r for r in rolls if r.sides == 20]) == 2
    assert [r.count for r in rolls if r.sides == 8] == [2, 1]


@pytest.mark.parametrize("falls", ["recipient", "caster"])
def test_actual_lethal_packet_retains_receipt_and_caster_zero_ends_bond_once(monkeypatch, falls):
    state, data, source = _bond_case()
    actor = "guard" if falls == "recipient" else "hero"
    state[f"{SRD}.entities"][actor].update(hp=1, temp_hp=0)
    fire = _grant(state, data, "fire_bolt")
    monkeypatch.setattr(dice, "_rng", ActualBondFaces())
    rolls = _record_rolls(monkeypatch)
    calls = []
    original = _effect_executor._EFFECT_HANDLERS["call"]

    def observe(body, effect, context, executor):
        if body == "apply_srd_reflected_warding_bond_damage":
            calls.append(context.current_tool)
        return original(body, effect, context, executor)

    monkeypatch.setitem(_effect_executor._EFFECT_HANDLERS, "call", observe)
    budget._run(
        "cast_spell_outside_combat",
        state,
        participant_data=data,
        **(fire | {"minor_elementals_damage_type": "cold"}),
    )
    assert calls == ["apply_srd_qualifying_damage"]
    assert state[f"{SRD}.entities"][actor]["hp"] == 0
    if falls == "recipient":
        assert state[f"{SRD}.entities"]["hero"]["temp_hp"] == 986
        assert state[f"{SRD}.state"]["spell_ward_sources"][source]["installed"]
    else:
        assert not state[f"{SRD}.state"]["spell_ward_sources"][source]["installed"]
        assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
        assert state[f"{SRD}.entities"]["hero"].get("death_failures", 0) == 0
        assert len([r for r in rolls if r.sides == 20]) == 1
