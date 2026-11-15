"""Fresh matching-type receipts preserve actual source riders and the one typed lifecycle."""

import hashlib
from functools import lru_cache

import pytest
import yaml
from scripts.srd_full.srd_damage_type_receipts import CHOICE, OWNERS, RECEIPT, bind_runtime, restore_runtime
from scripts.srd_full.srd_prismatic_damage_packets import bind_runtime as bind_prism
from scripts.srd_full.srd_qualified_packet_implementations import bind_runtime as bind_packets
from scripts.srd_full.srd_received_damage_lifecycle import bind_runtime as bind_received
from scripts.srd_full.srd_retained_nullable_captures import bind_runtime as bind_nullable
from scripts.srd_full.srd_runtime_defaults import compact_runtime_defaults
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_warding_bond_damage import bind_runtime_consumers as bind_bond

from sylvia_gm import dice
from sylvia_gm.runtime import _effect_executor
from tests import test_srd_action_budget as budget
from tests import test_srd_qualified_packet_implementations as packets
from tests import test_srd_spell_special_damage as special
from tests.srd_full.test_srd_spell_damage_consumers import _record_rolls
from tests.srd_full.test_srd_spell_result_cards import SRD
from tests.srd_full.test_srd_spell_wave3_afflictions import _cast as cast_curse


@lru_cache
def _graphs():
    raw = packets.PARENT.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == packets.PARENT_SHA
    original = bind_received(raw.decode())
    receipt = bind_runtime(original)
    assert bind_runtime(receipt) == receipt
    assert _load_runtime(restore_runtime(receipt)) == _load_runtime(original)
    assert restore_runtime(restore_runtime(receipt)) == restore_runtime(receipt)
    assert bind_runtime(restore_runtime(receipt)) == receipt
    before = _load_runtime(original)
    after = _load_runtime(receipt)
    # The normal builder loads LF text; normalize only this frozen clone's native CRLF header.
    receipt = receipt.replace(
        "  apply_srd_dream_wake_damage:\r\n", "  apply_srd_dream_wake_damage:\n", 1
    )
    final = bind_prism(bind_bond(packets._dream_successor(receipt)))
    final = compact_runtime_defaults(bind_nullable(bind_packets(final)))
    return before, after, _load_runtime(final)


def test_exact_final_owners_and_inverse_preserve_every_unowned_effect_and_schema():
    before, after, final = _graphs()
    assert all(
        after["tools"][name] == tool for name, tool in before["tools"].items() if name not in OWNERS
    )
    for name in OWNERS:
        old, new = before["tools"][name], after["tools"][name]
        assert {k: v for k, v in old.items() if k not in {"effects", "parameters"}} == {
            k: v for k, v in new.items() if k not in {"effects", "parameters"}
        }
        assert new["parameters"][:-1] == old["parameters"]
        assert new["parameters"][-1]["name"] == CHOICE
    assert packets._nodes(final) < 500000


def _producer(composite):
    parameters = {
        "target": "guard",
        "source_state_id": "hero",
        "game_damage_roll": True,
        "observe_damage_type": "necrotic",
    }
    if composite:
        name = "apply_srd_qualifying_composite_damage"
        parameters.update(
            component_1_amount="{_receipt_base_amount}",
            component_1_damage_type="necrotic",
            component_2_amount="{_receipt_second_amount}",
            component_2_damage_type="radiant",
        )
    else:
        name = "apply_srd_qualifying_damage"
        parameters.update(amount="{_receipt_base_amount}", adjustment=0, damage_type="necrotic")
    return {
        "description": "Observe the actual typed owner after a genuine raw game damage roll.",
        "parameters": [],
        "pre_conditions": [],
        "effects": [
            {"dice": {"expression": "2d6" if composite else "1d6"}, "store_as": "_receipt_raw"},
            {"set": {"field": "_receipt_base_amount", "value": "_receipt_raw.results[0]"}},
            *(
                [{"set": {"field": "_receipt_second_amount", "value": "_receipt_raw.results[1]"}}]
                if composite
                else []
            ),
            {"call": name, "params": parameters, "export_context": "*"},
        ],
        "metadata": {"category": "internal", "visible_to_player": False, "costs_action": False},
    }


@pytest.mark.parametrize("composite", [False, True])
@pytest.mark.parametrize("defense", ["ordinary", "resistance", "immunity"])
def test_actual_paid_curse_matching_base_and_rider_receipt_with_temp_hp(
    monkeypatch, composite, defense
):
    _, _, graph = _graphs()
    fragment = packets._fragment(
        graph,
        [
            "cast_spell_outside_combat",
            "apply_srd_qualifying_damage",
            "apply_srd_qualifying_composite_damage",
            "start_srd_condition_concentration",
        ],
    )
    fragment["tools"]["test_srd_actual_typed_roll"] = _producer(composite)
    encoded = compact_runtime_defaults(yaml.safe_dump(fragment, sort_keys=False)).encode()
    special.compiled_special.__wrapped__(monkeypatch, (encoded, graph["definitions"]["spells"]))
    state, data, _, _ = cast_curse(
        monkeypatch, "bestow_curse", curse_mode="damage", slot_resource="spell_slot_5"
    )
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(temp_hp=100, concentration_spell_id=None)
    if defense == "resistance":
        guard["damage_resistances"] = ["necrotic"]
    elif defense == "immunity":
        guard["damage_immunities"] = ["necrotic"]
    monkeypatch.setattr(dice, "_rng", special.ActualDiceSequence(d6=[5, 4], d8=[7]))
    rolls = _record_rolls(monkeypatch)
    calls = []
    actual = _effect_executor._EFFECT_HANDLERS["call"]

    def record(body, effect, context, executor):
        if body in {"apply_srd_qualifying_damage", "apply_srd_qualifying_composite_damage"}:
            calls.append(body)
        return actual(body, effect, context, executor)

    monkeypatch.setitem(_effect_executor._EFFECT_HANDLERS, "call", record)
    result = budget._run("test_srd_actual_typed_roll", state, participant_data=data).result
    expected = 12 if defense == "ordinary" else 5 if defense == "resistance" else 0
    assert result["context"][RECEIPT] == expected
    assert result["context"]["_effective_damage"] == expected + (4 if composite else 0)
    target = state[f"{SRD}.entities"]["guard"]
    assert target["hp"] == 1000 and target["temp_hp"] == 100 - expected - (4 if composite else 0)
    assert len(calls) == 1
    assert [r.total for r in rolls if r.sides == 8] == [7]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2


def test_every_invocation_reinitializes_receipt_after_stale_alias_and_default_none(monkeypatch):
    _, _, graph = _graphs()
    fragment = packets._fragment(graph, ["apply_srd_qualifying_damage"])
    producer = _producer(False)
    producer["effects"] += [
        {"set": {"field": "_receipt_first_observed", "value": RECEIPT}},
        {
            "call": "apply_srd_qualifying_damage",
            "params": {
                "target": "guard",
                "source_state_id": "hero",
                "amount": "{_receipt_base_amount}",
                "adjustment": 0,
                "damage_type": "radiant",
                "game_damage_roll": True,
            },
            "export_context": {RECEIPT: RECEIPT},
        },
    ]
    fragment["tools"]["test_srd_actual_typed_roll"] = producer
    encoded = yaml.safe_dump(fragment, sort_keys=False).encode()
    special.compiled_special.__wrapped__(monkeypatch, (encoded, graph["definitions"]["spells"]))
    state, data, _ = special.cases._spell_case("bestow_curse")
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(hp=1000, max_hp=1000, temp_hp=100)
    monkeypatch.setattr(dice, "_rng", special.ActualDiceSequence(d6=[5]))
    result = budget._run("test_srd_actual_typed_roll", state, participant_data=data).result
    assert result["context"]["_receipt_first_observed"] == 5
    assert result["context"][RECEIPT] == 0
    assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == 90


@pytest.mark.parametrize("success", [True, False])
def test_real_ordinary64_packet_cards_saves_and_cap_remain_unchanged(monkeypatch, success):
    _, _, graph = _graphs()
    monkeypatch.setattr(packets, "_graphs", lambda: (None, graph))
    packets.test_current_private64_fragment_preserves_actual_saves_packets_and_rows(
        monkeypatch, success
    )
