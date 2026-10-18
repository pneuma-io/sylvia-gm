"""A paid self mask retains native choices, consumed material, lifetime and concentration."""

import hashlib
import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
from scripts import srd_contingency_child_casting as child
from scripts import srd_spell_detection_masks as masks
from scripts.srd_control_behaviors import _walk
from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.runtime._effect_executor import execute_effects
from sylvia_gm.runtime._types import ExecutionContext

from tests import test_srd_contingency_child_casting as native
from tests import test_srd_contingency_child_public as prior
from tests import test_srd_contingency_combined_public as combined
from tests import test_srd_public_spell_casting as public_casting
from tests.test_srd_npc_spell_casting import _act

PROOF = Path(
    os.environ.get(
        "FC14_CONTINGENCY_MASK_RUNTIME",
        "historical/srd-full/validation-inputs/fc14-spells-20260930-wave11/defenses/"
        ".scratch/contingency-mask-children-frozen-v1/excluded/selected-runtime.yaml",
    )
)


def test_actual_mask_effects_choices_and_preparation_are_reused():
    tools, definitions = native.actual.__wrapped__()
    assert {"arcanists_magic_aura", "nondetection", "pass_without_trace"} <= set(
        child._selected(definitions)
    )
    original = tools[child.DISPATCHES["detection_mask_spell"]]
    expected = deepcopy(original["effects"])
    initializer = next(
        n["set"] for n in expected if n.get("set", {}).get("field") == "_mask_subject"
    )
    assert initializer["value"] == "mask_subject"
    initializer["value"] = "_caster_state_id"
    assert child._retained_family(original)["effects"] == expected
    selection = child._native_mask_selection(original)
    native_creature = next(
        e["on_true"][0]["on_false"]
        for e in original["effects"]
        if e.get("condition") == "spell == 'arcanists_magic_aura'"
        and e["on_true"][0].get("condition") == "_mask_subject_object == true"
    )
    assert selection[-1]["on_true"] == native_creature
    preparation = deepcopy(tools["prepare_srd_contingent_child"])
    child._prepare(preparation, child._selected(definitions), mask_selection=selection)
    descriptor = next(p for p in preparation["parameters"] if p["name"] == child.MASK_TYPE)
    assert descriptor == next(p for p in masks.CHOICES if p["name"] == child.MASK_TYPE)
    saved = next(
        e["set"]["value"]
        for e in preparation["effects"]
        if e.get("set", {}).get("field") == child.PREPARED
    )
    assert saved["mask_creature_type"] == child.MASK_TYPE
    assert not any("decrement" in n for n in _walk(preparation["effects"]))
    call = next(
        n
        for n in _walk(tools["finish_srd_retained_cast_tail"]["effects"])
        if n.get("call") == "apply_srd_detection_mask"
    )
    mapped = child._dispatch(call)
    assert mapped["export_context"] == call["export_context"]
    assert mapped["params"]["mask_subject"] == "{_target_state_id}"
    assert mapped["params"]["mask_willing"] is True
    assert mapped["params"]["mask_chosen_creatures"] == []
    assert mapped["params"]["mask_creature_type"] == "{_contingency_child.mask_creature_type}"
    normalized = child.normalized_tool(
        preparation["description"],
        preparation["parameters"],
        preparation["effects"],
        preparation.get("pre_conditions", ()),
    )
    repeated = deepcopy(normalized)
    child._prepare(repeated, child._selected(definitions), mask_selection=selection)
    assert (
        child.normalized_tool(
            repeated["description"],
            repeated["parameters"],
            repeated["effects"],
            repeated.get("pre_conditions", ()),
        )
        == normalized
    )


def test_changed_native_type_consumes_with_no_effect_before_strict_mask_selection():
    for actual_type, allowed in (("humanoid", True), ("fiend", False), (None, False)):
        context = ExecutionContext(
            params={"target_state": {"creature_type": actual_type}},
            vars={
                "_target_state_id": "hero",
                "_contingent_effect_eligible": True,
                "_contingency_child": {
                    "spell": "arcanists_magic_aura",
                    "mask_creature_type": "fiend",
                },
                "_contingent_self_scene": {"metadata": {"participant_ref": "hero"}},
            },
        )
        execute_effects(child._mask_eligibility(), context)
        assert context.vars["_contingent_effect_eligible"] is allowed
        if not allowed:
            assert "mask" in context.vars["_contingent_no_effect_reason"]


@pytest.fixture(scope="module")
def actual_archive():
    assert (
        hashlib.sha256(PROOF.read_bytes()).hexdigest()
        == "713f53fc7f9e758dbd0902fb4adc6f62b22a22f016cb384257137cbb7164f787"
    )
    original = combined.PROOF
    combined.PROOF = PROOF
    combined._runtime.cache_clear()
    combined._selected_archive.cache_clear()
    try:
        yield combined._selected_archive()
    finally:
        combined.PROOF = original
        combined._runtime.cache_clear()
        combined._selected_archive.cache_clear()


@pytest.mark.parametrize("spell", ["nondetection", "pass_without_trace"])
def test_paid_mask_releases_on_self_with_native_source_and_no_second_cost(
    tmp_path, monkeypatch, actual_archive, spell
):
    state, data, parameters, _, level = prior._case(monkeypatch, spell)
    hero = state[f"{combined.SRD}.entities"]["hero"]
    hero["resources"][f"spell_slot_{level}"] = 2
    hero["resources"]["spell_slot_1"] = 2
    if spell == "pass_without_trace":
        data["classes"] = [{"id": "wizard", "level": 11}, {"id": "druid", "level": 6}]
        data["wizard_spellbook"].remove(spell)
        data["spellcasting_grants"][0]["spells"].remove(spell)
        data["spellcasting_grants"].append(
            {"source": "druid", "ability": "wisdom", "spells": [spell]}
        )
    before_quantities = {r["id"]: r["quantity"] for r in hero["spell_materials"]}
    monkeypatch.setattr(public_casting, "_spell_case", lambda _: (state, data, parameters))
    monkeypatch.setattr(public_casting, "bundled_srd_archive", lambda: actual_archive)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    database = tmp_path / f"{spell}.sqlite3"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public_casting._create_spell_campaign(owner, "contingency")
        parent_receipt, parent_command = combined._cast(owner, parameters, 100)
        armed = owner.snapshot("casting")
        actor = armed.state[f"{combined.SRD}.entities"]["hero"]
        source_id = actor["contingency_source_id"]
        assert actor["concentration_spell_id"] is None
        assert not any(
            r.get("spell") == spell for r in armed.state[f"{combined.SRD}.state"]["turn_effects"]
        )
        resources = deepcopy(actor["resources"])
        quantities = {r["id"]: r["quantity"] for r in actor["spell_materials"]}
        for item in parameters["contingent_material_item_ids"]:
            assert quantities[item] == before_quantities[item] - (
                1 if spell == "nondetection" else 0
            )
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == armed
        monkeypatch.setattr(dice, "_rng", Random(0))
        missile = {k: v for k, v in parameters.items() if not k.startswith("contingent_")} | {
            "spell": "magic_missile",
            "slot_resource": "spell_slot_1",
            "target": "hero",
            "material_item_ids": [],
        }
        hit, hit_command = _act(owner, "cast_spell_outside_combat", **missile)
        assert hit.status is ActionStatus.COMMITTED, hit.result
        final = owner.snapshot("casting")
        lasting = final.state[f"{combined.SRD}.state"]
        actor = final.state[f"{combined.SRD}.entities"]["hero"]
        parent = lasting["contingency_sources"][source_id]
        assert parent["status"] == "released" and parent["child_effect_applied"] is True
        assert parent["trigger_hp_before"] > parent["trigger_hp_after"]
        expected = dict(resources)
        expected["spell_slot_1"] -= 1
        assert dict(actor["resources"]) == expected
        assert {r["id"]: r["quantity"] for r in actor["spell_materials"]} == quantities
        records = [r for r in lasting["turn_effects"] if r.get("spell") == spell]
        assert len(records) == 1
        record = records[0]
        assert record["kind"] == "detection_mask" and record["masked_subject"] == "hero"
        assert record["caster"] == record["target_actor"] == record["source_caster_id"] == "hero"
        assert record["cast_id"] == parent["child_cast_id"] != source_id
        assert record["spell_level"] == level and record["expires_tick"] > record["started_tick"]
        assert record["chosen_creatures"] == []
        assert actor["concentration_spell_id"] == (spell if spell == "pass_without_trace" else None)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
        monkeypatch.setattr(dice, "roll", lambda *_: pytest.fail("A saved retry must not reroll."))
        for command, receipt in ((parent_command, parent_receipt), (hit_command, hit)):
            assert owner.execute_action(command) == receipt
            assert owner.snapshot("casting") == final
