"""The naked copy derives its own defenses without taking the original's equipment."""

from copy import deepcopy

import pytest

from tests import test_srd_simulacrum_characters as pc

SRD = pc.SRD


@pytest.fixture(autouse=True)
def _compiled(monkeypatch):
    yield from pc.sim._compiled.__wrapped__(monkeypatch)


def _equipped_case():
    state, caster, params, original_data = pc._case()
    original_data["speed"] = 30
    pc._run(
        state,
        caster,
        original_data,
        "set_srd_carried_inventory",
        actor_state="guard",
        inventory=["plate_armor", "shield", "dagger"],
    )
    original = state[f"{SRD}.entities"]["guard"]
    # These are starting worn-item facts; their consequences come from the real resolver.
    for slot, item in (("armor", "plate_armor"), ("shield", "shield")):
        original[f"worn_{slot}"] = {
            "inventory_item": item, "armor_type": item, "magic_bonus": 0
        }
    projected = pc._run(
        state, caster, original_data, "resolve_srd_equipment", actor="guard", actor_state="guard"
    ).result["context"]
    for field, result in (
        ("armor_class", "_equipment_ac"),
        ("speed", "_equipment_speed"),
        ("armor_speed_penalty", "_equipment_speed_penalty"),
        ("armor_untrained", "_equipment_untrained_armor"),
        ("armor_stealth_disadvantage", "_equipment_stealth_disadvantage"),
    ):
        original[field] = projected[result]
    return state, caster, params, original_data


def test_paid_equipped_original_keeps_stock_and_derived_stats_while_copy_is_naked():
    state, caster, params, original_data = _equipped_case()
    original = deepcopy(state[f"{SRD}.entities"]["guard"])
    assert original["armor_class"] == 18 and original["speed"] == 20
    assert original["armor_untrained"] is True
    pc._cast(state, caster, params, original_data)
    assert state[f"{SRD}.entities"]["guard"] == original
    duplicate = state[f"{SRD}.entities"]["snow"]
    assert duplicate["inventory"] == []
    assert duplicate.get("worn_armor") is None and duplicate.get("worn_shield") is None
    assert duplicate["armor_class"] == 12 and duplicate["speed"] == 30
    assert duplicate.get("armor_untrained") is not True
    pc._run(
        state,
        caster,
        original_data,
        "cast_spell_outside_combat",
        **pc._missile(params, source="snow"),
    )
    assert state[f"{SRD}.entities"]["snow"]["resources"]["spell_slot_1"] == 1
    assert state[f"{SRD}.entities"]["guard"] == original


def test_public_equipped_original_creation_and_later_copy_slot_reopens_and_retries(
    tmp_path, monkeypatch
):
    original_case = pc._case

    def equipped_case():
        monkeypatch.setattr(pc, "_case", original_case)
        try:
            return _equipped_case()
        finally:
            monkeypatch.setattr(pc, "_case", equipped_case)

    monkeypatch.setattr(pc, "_case", equipped_case)
    pc.test_public_paid_pc_original_reopens_for_independent_slot_cast_and_exact_retries(
        tmp_path, monkeypatch
    )
