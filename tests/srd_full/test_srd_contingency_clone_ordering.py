"""The original body's HP event must survive an actual paid Clone replacement."""

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
from tests.srd_full.test_srd_npc_spell_casting import _act

ROOT = Path(__file__).resolve().parents[2]
PROOF = Path(
    os.environ.get(
        "FC14_CONTINGENCY_CLONE_RUNTIME",
        ROOT / ".scratch/contingency-clone-ordering-v1/selected-runtime.yaml",
    )
)
SRD = combined.SRD
BODY_TOOLS = {
    "record_srd_body_vessel",
    "record_srd_soul_disposition",
    "read_srd_clone_body",
}


def _calls(tool):
    """Read actual emitted calls, including the occupied-body and death branches."""
    if isinstance(tool, dict):
        if isinstance(tool.get("call"), str):
            yield tool["call"]
        for value in tool.values():
            yield from _calls(value)
    elif isinstance(tool, list):
        for value in tool:
            yield from _calls(value)


@pytest.fixture(scope="module")
def actual_archive():
    """Require a genuine final death callback, not a constructor-only Clone fixture."""
    original = combined.PROOF, combined.PUBLIC
    combined.PROOF = PROOF
    combined.PUBLIC = combined.PUBLIC | BODY_TOOLS
    combined._runtime.cache_clear()
    combined._selected_archive.cache_clear()
    try:
        runtime = combined._runtime()
        tools = runtime["tools"]
        pending, reached = ["cast_spell_outside_combat"], set()
        while pending:
            name = pending.pop()
            if name in reached:
                continue
            reached.add(name)
            pending.extend(_calls(tools[name]["effects"]))
        assert "cast_srd_body_soul_spell" in reached
        assert "settle_srd_clone_death" in reached
        assert "observe_srd_contingency_damage" in reached
        assert "drain_srd_contingency_releases" in reached
        yield combined._selected_archive()
    finally:
        combined.PROOF, combined.PUBLIC = original
        combined._runtime.cache_clear()
        combined._selected_archive.cache_clear()


def _case(monkeypatch):
    state, data, parameters, materials = combined._case(monkeypatch)
    parameters["contingent_hp_threshold"] = 0
    spells = data["wizard_spellbook"] + ["clone"]
    data["wizard_spellbook"] = spells[:]
    data["spellcasting_grants"][0]["spells"] = spells[:]
    hero = state[f"{SRD}.entities"]["hero"]
    # Current injury is admitted campaign fiction. Both the original and Clone
    # retain the existing maximum; the later lethal amount comes from real dice.
    hero.update(hp=1, prepared_spells=spells[:])
    materials["clone"] = []
    for index, part in enumerate(
        combined._runtime()["definitions"]["spell_casting"]["clone"]["material_parts"]
    ):
        lot = {
            "id": f"clone_component_{index}",
            "inventory_item": f"clone_inventory_{index}",
            "kind": "specific",
            "components": [part["id"]],
            "quantity": part["quantity"],
            "value_cp": part["minimum_value_cp"],
        }
        hero["spell_materials"].append(lot)
        hero["inventory"].append(lot["inventory_item"])
        materials["clone"].append(lot["id"])
    scene = state[f"{SRD}.state"]["spatial_scene"]
    vessel = deepcopy(next(row for row in scene["entities"] if row["id"] == "reliquary"))
    vessel.update(id="clone-vessel", footprint={"kind": "box", "half_extents": [3, 3]}, height=6)
    scene["entities"].append(vessel)
    return state, data, parameters, materials


def _body(snapshot):
    scene = snapshot.state[f"{SRD}.state"]["spatial_scene"]
    vessel = next(row for row in scene["entities"] if row["id"] == "clone-vessel")
    return vessel["metadata"]["srd_clone_body"]


def test_paid_lethal_hp_event_retains_first_trigger_across_clone_and_saved_retry(
    tmp_path, monkeypatch, actual_archive
):
    state, data, parameters, materials = _case(monkeypatch)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _: (state, data, parameters))
    monkeypatch.setattr(public_casting, "bundled_srd_archive", lambda: actual_archive)
    database = tmp_path / "contingency-clone-order.sqlite3"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public_casting._create_spell_campaign(owner, "contingency")
        recorded, _ = _act(
            owner,
            "record_srd_body_vessel",
            caster_state="hero",
            object_id="clone-vessel",
            material_item_id=materials["clone"][1],
            interior_feet=[5, 5, 6],
            sealed=True,
        )
        assert recorded.status is ActionStatus.COMMITTED, recorded.result
        disposition, _ = _act(
            owner, "record_srd_soul_disposition", target_state="hero", free=True, willing=True
        )
        assert disposition.status is ActionStatus.COMMITTED, disposition.result
        before_clone = owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]
        clone = {
            key: value for key, value in parameters.items() if not key.startswith("contingent_")
        } | {
            "spell": "clone",
            "target": "hero",
            "slot_resource": "spell_slot_8",
            "material_item_ids": materials["clone"],
            "body_vessel_id": "clone-vessel",
        }
        clone_receipt, clone_command = combined._cast(owner, clone, 600)
        created = owner.snapshot("casting")
        hero = created.state[f"{SRD}.entities"]["hero"]
        assert hero["resources"]["spell_slot_8"] == before_clone["resources"]["spell_slot_8"] - 1
        quantities = {lot["id"]: lot["quantity"] for lot in hero["spell_materials"]}
        assert quantities[materials["clone"][0]] == 0
        assert quantities[materials["clone"][1]] == 1
        assert _body(created)["matures_tick"] - _body(created)["started_tick"] == 3456000
        for rounds in [100000] * 17 + [28000]:
            mature, _ = _act(owner, "advance_srd_lasting_time", elapsed_rounds=rounds)
            assert mature.status is ActionStatus.COMMITTED, mature.result
        assert _body(owner.snapshot("casting"))["mature"] is True
        parent_receipt, parent_command = combined._cast(owner, parameters, 100)
        armed = owner.snapshot("casting")
        hero = armed.state[f"{SRD}.entities"]["hero"]
        resources = deepcopy(hero["resources"])
        source_id = hero["contingency_source_id"]
        source = armed.state[f"{SRD}.state"]["contingency_sources"][source_id]
        assert source["installed"] is True and source["hp_threshold"] == 0
        assert hero["hp"] == 1 and hero["max_hp"] == 40
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == armed
        # This is the native deterministic RNG, not a supplied damage/death outcome.
        monkeypatch.setattr(dice, "_rng", Random(0))
        missile = {
            key: value for key, value in parameters.items() if not key.startswith("contingent_")
        } | {
            "spell": "magic_missile",
            "target": "hero",
            "slot_resource": "spell_slot_9",
            "target_distance": 0,
            "material_item_ids": [],
        }
        damage_receipt, damage_command = _act(owner, "cast_spell_outside_combat", **missile)
        assert damage_receipt.status is ActionStatus.COMMITTED, damage_receipt.result
        saved = owner.snapshot("casting")
        hero = saved.state[f"{SRD}.entities"]["hero"]
        roll = damage_receipt.result["context"]["_spell_damage"]
        assert roll["count"] == 11 and roll["total"] + 11 >= 41
        assert _body(saved)["transferred"] is True
        assert hero["current_body_source_id"] == _body(saved)["source_id"]
        assert hero["is_alive"] is True and hero["hp"] == hero["max_hp"] == 40
        source = saved.state[f"{SRD}.state"]["contingency_sources"][source_id]
        statuette = source["statuette_item"]
        assert hero["inventory"] == []
        assert statuette in hero["prior_body_sources"]["hero"]["inventory"]
        assert hero["resources"]["spell_slot_9"] == resources["spell_slot_9"] - 1
        assert hero["resources"]["spell_slot_3"] == resources["spell_slot_3"]
        assert hero["resources"]["spell_slot_6"] == resources["spell_slot_6"]
        # The original positive HP transition is the selected first circumstance.
        # Healing the replacement body must not erase its causal receipt.
        assert source["status"] == "released", source
        assert source["trigger_hp_before"] == 1 and source["trigger_hp_after"] == 0
        assert source["installed"] is False and hero["has_contingency"] is False
        children = [
            effect
            for effect in saved.state[f"{SRD}.state"]["turn_effects"]
            if effect.get("kind") == "timed_senses" and effect.get("spell") == "water_breathing"
        ]
        assert len(children) == 1 and children[0]["target"] == "hero"
        assert children[0]["cast_id"] and children[0]["cast_id"] != source_id
        assert not saved.state[f"{SRD}.state"]["contingency_release_queue"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        monkeypatch.setattr(dice, "roll", lambda *a, **kw: pytest.fail("Saved retry rerolled"))
        for command, receipt in [
            (clone_command, clone_receipt),
            (parent_command, parent_receipt),
            (damage_command, damage_receipt),
        ]:
            assert owner.execute_action(command) == receipt
        assert owner.snapshot("casting") == saved
