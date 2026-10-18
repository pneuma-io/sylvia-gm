"""Paid contingent concentration and consumed components use their real release owners."""

import hashlib
import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from tests.srd_full import test_srd_contingency_combined_public as combined
from tests.srd_full import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_npc_spell_casting import _act

ROOT = Path(__file__).resolve().parents[2]
PROOF = Path(
    os.environ.get(
        "FC14_CONTINGENCY_CHILD_RUNTIME",
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/fc14-contingency-child-runtime/selected-runtime.yaml"),
    )
)


@pytest.fixture(scope="module")
def actual_archive():
    assert hashlib.sha256(PROOF.read_bytes()).hexdigest() == (
        "0d3a7d46db611b379b35c9c1331ac5ee56fee22e778581daa0277f661195ccd7"
    )
    original = combined.PROOF, combined.PUBLIC
    combined.PROOF = PROOF
    combined.PUBLIC = combined.PUBLIC | {"change_srd_equipment"}
    combined._runtime.cache_clear()
    combined._selected_archive.cache_clear()
    try:
        yield combined._selected_archive()
    finally:
        combined.PROOF, combined.PUBLIC = original
        combined._runtime.cache_clear()
        combined._selected_archive.cache_clear()


def _case(monkeypatch, spell):
    from sylvia_gm.application import package_runtime

    execute = package_runtime.execute_tool

    def checked(*args, **kwargs):
        result = execute(*args, **kwargs)
        if not result.success:
            print("NATIVE", args[0], result.failure_kind, result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", checked)
    state, data, parameters, _ = combined._case(monkeypatch)
    spells = [*data["wizard_spellbook"], spell]
    data["wizard_spellbook"] = spells[:]
    data["spellcasting_grants"][0]["spells"] = spells[:]
    hero = state[f"{combined.SRD}.entities"]["hero"]
    hero["prepared_spells"] = spells[:]
    lots = []
    for index, part in enumerate(
        combined._runtime()["definitions"]["spell_casting"][spell]["material_parts"]
    ):
        lot = {
            "id": f"chosen_component_{index}",
            "inventory_item": f"chosen_inventory_{index}",
            "kind": "specific",
            "components": [part["id"]],
            "quantity": part["quantity"] * 3,
            "value_cp": part["minimum_value_cp"],
        }
        hero["spell_materials"].append(lot)
        hero["inventory"].append(lot["inventory_item"])
        lots.append(lot["id"])
    base_level = combined._runtime()["definitions"]["spells"][spell]["base_level"]
    parameters.update(
        contingent_spell=spell,
        contingent_slot_resource=f"spell_slot_{base_level}",
        contingent_material_item_ids=lots,
        contingent_effect_choice="warm",
    )
    return state, data, parameters, lots, base_level


@pytest.mark.parametrize("spell", ["haste", "protection_from_evil_and_good"])
def test_paid_chosen_spell_defers_real_effect_concentration_and_duration_until_saved_trigger(
    tmp_path, monkeypatch, actual_archive, spell
):
    state, data, parameters, lots, level = _case(monkeypatch, spell)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _: (state, data, parameters))
    monkeypatch.setattr(public_casting, "bundled_srd_archive", lambda: actual_archive)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    database = tmp_path / f"chosen-{spell}.sqlite3"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public_casting._create_spell_campaign(owner, "contingency")
        before = owner.snapshot("casting")
        initial = before.state[f"{combined.SRD}.entities"]["hero"]
        parent_receipt, parent_command = combined._cast(owner, parameters, 100)
        armed = owner.snapshot("casting")
        hero = armed.state[f"{combined.SRD}.entities"]["hero"]
        source_id = hero["contingency_source_id"]
        source = armed.state[f"{combined.SRD}.state"]["contingency_sources"][source_id]
        assert source["installed"] is True
        assert source["child"]["spell"] == spell
        assert (
            hero["resources"][f"spell_slot_{level}"]
            == initial["resources"][f"spell_slot_{level}"] - 1
        )
        assert hero["resources"]["spell_slot_6"] == initial["resources"]["spell_slot_6"] - 1
        assert hero["concentration_spell_id"] is None
        assert not any(
            row.get("spell") == spell
            for row in armed.state[f"{combined.SRD}.state"].get("turn_effects", [])
        )
        quantities = {row["id"]: row["quantity"] for row in hero["spell_materials"]}
        parts = combined._runtime()["definitions"]["spell_casting"][spell]["material_parts"]
        for part, item_id in zip(parts, lots, strict=True):
            expected = part["quantity"] * (2 if part["consumed"] else 3)
            assert quantities[item_id] == expected
        resources = deepcopy(hero["resources"])
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == armed
        monkeypatch.setattr(dice, "_rng", Random(0))
        missile = {
            key: value for key, value in parameters.items() if not key.startswith("contingent_")
        } | {
            "spell": "magic_missile",
            "slot_resource": "spell_slot_1",
            "target": "hero",
            "material_item_ids": [],
        }
        hit, hit_command = _act(owner, "cast_spell_outside_combat", **missile)
        assert hit.status is ActionStatus.COMMITTED, hit.result
        released = owner.snapshot("casting")
        hero = released.state[f"{combined.SRD}.entities"]["hero"]
        lasting = released.state[f"{combined.SRD}.state"]
        parent = lasting["contingency_sources"][source_id]
        assert parent["status"] == "released" and parent["installed"] is False
        assert parent["child_effect_applied"] is True
        assert parent["trigger_hp_before"] > parent["trigger_hp_after"]
        assert hero["has_contingency"] is False
        assert hero["concentration_spell_id"] == spell
        assert hero["concentration_cast_id"] == parent["child_cast_id"] != source_id
        records = [row for row in lasting["turn_effects"] if row.get("spell") == spell]
        assert len(records) == 1
        record = records[0]
        assert record["target"] == "hero" and record["caster"] == "hero"
        assert record["spell_level"] == level
        if spell == "haste":
            # Its existing native installer retains caster/target/lifetime but
            # has no common cast-ID parameter; do not invent a record stamp.
            assert "cast_id" not in record
        else:
            assert record["cast_id"] == parent["child_cast_id"]
        rounds = combined._runtime()["definitions"]["spells"][spell]["duration_rounds"]
        assert record["expires_tick"] - record["started_tick"] == rounds * 2
        assert record["started_tick"] >= armed.state[f"{combined.SRD}.state"]["lasting_tick"]
        expected_resources = dict(resources)
        expected_resources["spell_slot_1"] -= 1
        assert dict(hero["resources"]) == expected_resources
        assert {row["id"]: row["quantity"] for row in hero["spell_materials"]} == quantities
        if spell == "haste":
            assert hero["spell_haste"] is True
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == released
        monkeypatch.setattr(dice, "roll", lambda *_: pytest.fail("A saved retry must not reroll."))
        for command, receipt in ((parent_command, parent_receipt), (hit_command, hit)):
            assert owner.execute_action(command) == receipt
            assert owner.snapshot("casting") == released
        expired, _ = _act(owner, "advance_srd_lasting_time", elapsed_rounds=rounds)
        assert expired.status is ActionStatus.COMMITTED, expired.result
        final = owner.snapshot("casting")
        assert not any(
            row.get("spell") == spell
            for row in final.state[f"{combined.SRD}.state"].get("turn_effects", [])
        )
        if spell == "haste":
            assert final.state[f"{combined.SRD}.entities"]["hero"]["spell_haste"] is False
        assert final.state[f"{combined.SRD}.state"]["contingency_sources"][source_id] == parent


def test_paid_child_becoming_ineligible_has_no_effect_without_undoing_real_damage(
    tmp_path, monkeypatch, actual_archive
):
    state, data, parameters, _, _ = _case(monkeypatch, "mage_armor")
    # Actual Cleric multiclass training allows the later spell attack while
    # wearing the newly donned Light armor; armor possession is real inventory.
    data["classes"] = [{"id": "wizard", "level": 16}, {"id": "cleric", "level": 1}]
    hero = state[f"{combined.SRD}.entities"]["hero"]
    hero["inventory"].append("leather")
    monkeypatch.setattr(public_casting, "_spell_case", lambda _: (state, data, parameters))
    monkeypatch.setattr(public_casting, "bundled_srd_archive", lambda: actual_archive)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    database = tmp_path / "late-armor.sqlite3"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public_casting._create_spell_campaign(owner, "contingency")
        parent_receipt, parent_command = combined._cast(owner, parameters, 100)
        armed = owner.snapshot("casting")
        source_id = armed.state[f"{combined.SRD}.entities"]["hero"]["contingency_source_id"]
        worn, _ = _act(
            owner,
            "change_srd_equipment",
            actor="hero",
            actor_state="hero",
            slot="armor",
            item=[{"inventory_item": "leather", "armor_type": "leather_armor", "magic_bonus": 0}],
        )
        assert worn.status is ActionStatus.COMMITTED, worn.result
        dressed = owner.snapshot("casting")
        before = dressed.state[f"{combined.SRD}.entities"]["hero"]
        assert before["worn_armor"]["inventory_item"] == "leather"
        monkeypatch.setattr(dice, "_rng", Random(0))
        missile = {
            key: value for key, value in parameters.items() if not key.startswith("contingent_")
        } | {
            "spell": "magic_missile",
            "slot_resource": "spell_slot_1",
            "target": "hero",
            "material_item_ids": [],
        }
        hit, hit_command = _act(owner, "cast_spell_outside_combat", **missile)
        assert hit.status is ActionStatus.COMMITTED, hit.result
        released = owner.snapshot("casting")
        hero = released.state[f"{combined.SRD}.entities"]["hero"]
        parent = released.state[f"{combined.SRD}.state"]["contingency_sources"][source_id]
        assert hero["hp"] < before["hp"]
        assert hero["resources"]["spell_slot_1"] == before["resources"]["spell_slot_1"] - 1
        assert parent["status"] == "released" and parent["installed"] is False
        assert parent["child_release_completed"] is True and parent["child_effect_applied"] is False
        assert parent["child_status"] == "no_effect" and "child_cast_id" not in parent
        assert parent["child_no_effect_reason"] == "wearing armor"
        assert "no effect" in parent["release_summary"].lower()
        assert not any(
            row.get("spell") == "mage_armor"
            for row in released.state[f"{combined.SRD}.state"].get("turn_effects", [])
        )
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == released
        monkeypatch.setattr(dice, "roll", lambda *_: pytest.fail("A saved retry must not reroll."))
        assert owner.execute_action(parent_command) == parent_receipt
        assert owner.execute_action(hit_command) == hit
        assert owner.snapshot("casting") == released
        doffed, _ = _act(
            owner, "change_srd_equipment", actor="hero", actor_state="hero", slot="armor", item=[]
        )
        assert doffed.status is ActionStatus.COMMITTED, doffed.result
        final = owner.snapshot("casting")
        assert final.state[f"{combined.SRD}.state"]["contingency_sources"][source_id] == parent
        assert not any(
            row.get("spell") == "mage_armor"
            for row in final.state[f"{combined.SRD}.state"].get("turn_effects", [])
        )
