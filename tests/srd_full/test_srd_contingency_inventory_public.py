"""Actual paid body change and heated-object loss retire the carried component once."""

import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from tests import test_srd_contingency_combined_public as combined
from tests import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_npc_spell_casting import _act, _prepare_mage

ROOT = Path(__file__).resolve().parents[2]
PROOF = Path(
    os.environ.get(
        "FC14_CONTINGENCY_INVENTORY_RUNTIME",
        ROOT / ".scratch/contingency-inventory-v1/selected-runtime.yaml",
    )
)
SRD = combined.SRD


@pytest.fixture(scope="module")
def actual_archive():
    """Reuse the verified closure with only authentic physical-loss/preparation owners added."""
    original = combined.PROOF, combined.PUBLIC
    combined.PROOF = PROOF
    combined.PUBLIC = combined.PUBLIC | {
        "prepare_srd_creature",
        "record_srd_object_utility_facts",
        "record_srd_object_holder",
    }
    combined._runtime.cache_clear()
    combined._selected_archive.cache_clear()
    archive = combined._selected_archive()
    yield archive
    combined.PROOF, combined.PUBLIC = original
    combined._runtime.cache_clear()
    combined._selected_archive.cache_clear()


def _case(monkeypatch):
    state, data, parameters, materials = combined._case(monkeypatch)
    parameters["contingent_hp_threshold"] = 0
    spells = data["wizard_spellbook"] + ["shapechange"]
    data["wizard_spellbook"] = spells[:]
    data["spellcasting_grants"][0]["spells"] = spells[:]
    hero = state[f"{SRD}.entities"]["hero"]
    hero["prepared_spells"] = spells[:]
    materials["shapechange"] = []
    for index, part in enumerate(
        combined._runtime()["definitions"]["spell_casting"]["shapechange"]["material_parts"]
    ):
        item = {
            "id": f"shapechange_component_{index}",
            "inventory_item": f"shapechange_inventory_{index}",
            "kind": "specific",
            "components": [part["id"]],
            "quantity": part["quantity"],
            "value_cp": part["minimum_value_cp"],
        }
        hero["spell_materials"].append(item)
        hero["inventory"].append(item["inventory_item"])
        materials["shapechange"].append(item["id"])
    return state, data, parameters, materials


def _setup(monkeypatch, archive, state, data, parameters):
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _: (state, data, parameters))
    monkeypatch.setattr(public_casting, "bundled_srd_archive", lambda: archive)


def _assert_ended(snapshot, source_id):
    hero = snapshot.state[f"{SRD}.entities"]["hero"]
    source = snapshot.state[f"{SRD}.state"]["contingency_sources"][source_id]
    assert source["installed"] is False and source["status"] == "ended"
    assert source["end_reason"] == "expired or statuette no longer carried"
    assert hero["has_contingency"] is False
    assert not snapshot.state[f"{SRD}.state"].get("contingency_release_queue")
    assert not any(
        effect.get("kind") == "timed_senses" and effect.get("spell") == "water_breathing"
        for effect in snapshot.state[f"{SRD}.state"].get("turn_effects", [])
    )


def test_paid_shapechange_actual_drop_ends_parent_preserves_form_and_reopens(
    tmp_path, monkeypatch, actual_archive
):
    state, data, parameters, materials = _case(monkeypatch)
    _setup(monkeypatch, actual_archive, state, data, parameters)
    database = tmp_path / "contingency-form-drop.sqlite3"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public_casting._create_spell_campaign(owner, "contingency")
        before = owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]
        parent_receipt, parent_command = combined._cast(owner, parameters, 100)
        armed = owner.snapshot("casting")
        source_id = armed.state[f"{SRD}.entities"]["hero"]["contingency_source_id"]
        assert armed.state[f"{SRD}.state"]["contingency_sources"][source_id]["installed"]
        shape = {
            key: value for key, value in parameters.items() if not key.startswith("contingent_")
        }
        shape.update(
            spell="shapechange",
            slot_resource="spell_slot_9",
            material_item_ids=materials["shapechange"],
            transformation_form="camel",
            transformation_seen_before=True,
            transformation_equipment="drop",
        )
        receipt, command = _act(owner, "cast_spell_outside_combat", **shape)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        _assert_ended(saved, source_id)
        hero = saved.state[f"{SRD}.entities"]["hero"]
        parent = saved.state[f"{SRD}.state"]["contingency_sources"][source_id]
        assert hero["inventory"] == [] and parent["statuette_item"] in hero["dropped_items"]
        form = saved.state[f"{SRD}.state"]["transformation_sources"][hero["form_current_source_id"]]
        assert form["installed"] is True and form["form_id"] == "camel"
        assert hero["concentration_spell_id"] == "shapechange"
        assert hero["resources"]["spell_slot_9"] == before["resources"]["spell_slot_9"] - 1
        assert hero["resources"]["spell_slot_3"] == before["resources"]["spell_slot_3"] - 1
    with AuthorityOwner.open(database, "casting-owner") as owner:
        monkeypatch.setattr(dice, "roll", lambda *a, **kw: pytest.fail("Saved retry rerolled"))
        assert owner.execute_action(parent_command) == parent_receipt
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == saved


def test_paid_heat_metal_actual_save_drop_ends_only_parent_and_reopens(
    tmp_path, monkeypatch, actual_archive
):
    state, data, parameters, _ = _case(monkeypatch)
    hero = state[f"{SRD}.entities"]["hero"]
    data["abilities"]["constitution"] = 3
    hero["ability_scores"] = deepcopy(data["abilities"])
    hero["saving_throw_modifiers"] = {"constitution": -4}
    statuette = next(
        lot for lot in hero["spell_materials"] if lot["id"] == parameters["material_item_ids"][0]
    )["inventory_item"]
    scene = state[f"{SRD}.state"]["spatial_scene"]
    statue = deepcopy(next(row for row in scene["entities"] if row["id"] == "reliquary"))
    statue.update(id="metal-statuette", position=[2, 0, 0])
    statue["metadata"] = {"is_object": True, "plane": "material", "lighting": "bright"}
    scene["entities"].append(statue)
    mage = deepcopy(next(row for row in scene["entities"] if row["id"] == "guard"))
    mage.update(id="mage", position=[10, 0, 0])
    mage["metadata"] = {"participant_ref": "mage", "plane": "material", "lighting": "bright"}
    scene["entities"].append(mage)
    _setup(monkeypatch, actual_archive, state, data, parameters)
    database = tmp_path / "contingency-heated-drop.sqlite3"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public_casting._create_spell_campaign(owner, "contingency")
        before = owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]
        parent_receipt, parent_command = combined._cast(owner, parameters, 100)
        source_id = owner.snapshot("casting").state[f"{SRD}.entities"]["hero"][
            "contingency_source_id"
        ]
        material = combined._runtime()["definitions"]["spell_casting"]["heat_metal"][
            "material_parts"
        ][0]
        prepared, _ = _prepare_mage(
            owner,
            [{"spell": "heat_metal", "uses_per_day": 1}],
            inventory=["heat-components"],
            spell_materials=[
                {
                    "id": "heat-supply",
                    "inventory_item": "heat-components",
                    "kind": "specific",
                    "components": [material["id"]],
                    "quantity": 1,
                    "value_cp": material["minimum_value_cp"],
                }
            ],
        )
        assert prepared.status is ActionStatus.COMMITTED, prepared.result
        fact, _ = _act(
            owner,
            "record_srd_object_utility_facts",
            object_id="metal-statuette",
            weight_pounds=1,
            manufactured=True,
            metal=True,
            object_kind="object",
            inventory_item=statuette,
        )
        assert fact.status is ActionStatus.COMMITTED, fact.result
        held, _ = _act(
            owner,
            "record_srd_object_holder",
            actor_state="hero",
            object_id="metal-statuette",
            held_as="carried",
        )
        assert held.status is ActionStatus.COMMITTED, held.result
        heat = {
            key: value for key, value in parameters.items() if not key.startswith("contingent_")
        }
        heat.pop("caster")
        heat.pop("slot_resource")
        heat.update(
            spell="heat_metal",
            caster_state="mage",
            heated_object_id="metal-statuette",
            material_item_ids=["heat-supply"],
        )
        receipt, command = _act(owner, "cast_spell_outside_combat", **heat)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
        _assert_ended(saved, source_id)
        hero = saved.state[f"{SRD}.entities"]["hero"]
        assert 0 < hero["hp"] < before["hp"]
        assert statuette not in hero["inventory"] and statuette in hero["dropped_items"]
        assert len(hero["inventory"]) == len(before["inventory"]) - 1
        assert hero["resources"]["spell_slot_3"] == before["resources"]["spell_slot_3"] - 1
        npc = saved.state[f"{SRD}.entities"]["mage"]
        assert npc["spell_uses_remaining"]["heat_metal"] == 0
        assert npc["concentration_spell_id"] == "heat_metal"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        monkeypatch.setattr(dice, "roll", lambda *a, **kw: pytest.fail("Saved retry rerolled"))
        assert owner.execute_action(parent_command) == parent_receipt
        assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == saved
