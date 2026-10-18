"""Self concealment retains native effects and factual anatomy while setup owns costs."""

import hashlib
import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
from scripts import srd_contingency_child_casting as child
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
        "FC14_CONTINGENCY_CONCEALMENT_RUNTIME",
        "historical/srd-full/validation-inputs/fc14-spells-20260930-wave11/defenses/"
        ".scratch/contingency-concealment-children-frozen-v1/excluded/selected-runtime.yaml",
    )
)


def test_native_concealment_effects_and_factual_appearance_descriptor_are_unchanged():
    tools, definitions = native.actual.__wrapped__()
    assert {"invisibility", "greater_invisibility", "mirror_image", "disguise_self", "seeming"} <= (
        set(child._selected(definitions))
    )
    cast = deepcopy(tools["cast_spell"])
    original_spec = next(p for p in cast["parameters"] if p["name"] == "appearance_specs")
    child._add_choice(cast)
    assert next(p for p in cast["parameters"] if p["name"] == "appearance_specs") == original_spec
    for family in ("illumination_spell", "illusion_image", "illusion_appearance"):
        original = tools[child.DISPATCHES[family]]
        assert child._retained_family(original)["effects"] == original["effects"]
        call = next(
            n
            for n in _walk(tools["finish_srd_retained_cast_tail"]["effects"])
            if n.get("call") == child.DISPATCHES[family]
        )
        mapped = child._dispatch(call)
        assert mapped["export_context"] == call["export_context"]
        if "targets" in mapped["params"]:
            assert mapped["params"]["targets"] == "{_contingent_self_targets}"
        if family == "illusion_appearance":
            assert mapped["params"]["appearance_specs"] == "{_contingency_child.appearance_specs}"
    changed = deepcopy(definitions)
    changed["mirror_image"]["range_feet"] = 30
    with pytest.raises(ValueError, match="self-effect profile"):
        child._selected(changed)
    preparation = deepcopy(tools["prepare_srd_contingent_child"])
    child._prepare(preparation, child._selected(definitions))
    normalized = child.normalized_tool(
        preparation["description"],
        preparation["parameters"],
        preparation["effects"],
        preparation.get("pre_conditions", ()),
    )
    repeated = deepcopy(normalized)
    child._prepare(repeated, child._selected(definitions))
    repeated = child.normalized_tool(
        repeated["description"],
        repeated["parameters"],
        repeated["effects"],
        repeated.get("pre_conditions", ()),
    )
    assert repeated == normalized


def test_changed_anatomy_has_no_effect_before_native_strict_appearance_validation():
    for limb, allowed in (("humanoid", True), ("quadruped", False)):
        context = ExecutionContext(
            params={"target_state": {"form_id": None}},
            vars={
                "_target_state_id": "hero",
                "_contingent_effect_eligible": True,
                "_contingency_child": {
                    "spell": "disguise_self",
                    "appearance_specs": [
                        {
                            "subject": "hero",
                            "description": "silver cloak",
                            "height_offset_inches": 6,
                            "limb_arrangement": "humanoid",
                            "willing": True,
                        }
                    ],
                },
                "_contingent_self_scene": {
                    "height": 6,
                    "metadata": {"basic_limb_arrangement": limb},
                },
            },
        )
        execute_effects(child._appearance_eligibility(), context)
        assert context.vars["_contingent_effect_eligible"] is allowed
        if not allowed:
            assert "appearance" in context.vars["_contingent_no_effect_reason"]


@pytest.fixture(scope="module")
def actual_archive():
    assert hashlib.sha256(PROOF.read_bytes()).hexdigest() == (
        "07771143ffd50698e22abce17273f1a5504bcf657ddf07401e09b654e84609b7"
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


@pytest.mark.parametrize("spell", ["invisibility", "disguise_self"])
def test_paid_concealment_starts_only_on_real_hp_release_then_reopens_without_second_cost(
    tmp_path, monkeypatch, actual_archive, spell
):
    state, data, parameters, _, level = prior._case(monkeypatch, spell)
    hero = state[f"{combined.SRD}.entities"]["hero"]
    hero["resources"][f"spell_slot_{level}"] = 2
    hero["resources"]["spell_slot_1"] = 2
    body = next(
        r for r in state[f"{combined.SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "hero"
    )
    body["metadata"]["basic_limb_arrangement"] = "humanoid"
    spec = {
        "subject": "hero",
        "description": "silver cloak",
        "height_offset_inches": 6,
        "limb_arrangement": "humanoid",
        "willing": True,
    }
    if spell == "disguise_self":
        parameters["appearance_specs"] = [spec]
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
        assert "invisible" not in actor["conditions"]
        quantities = {r["id"]: r["quantity"] for r in actor["spell_materials"]}
        resources = deepcopy(actor["resources"])
        if spell == "disguise_self":
            assert armed.state[f"{combined.SRD}.state"]["contingency_sources"][source_id]["child"][
                "appearance_specs"
            ] == [spec]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == armed
        monkeypatch.setattr(dice, "_rng", Random(0))
        missile = {
            key: value
            for key, value in parameters.items()
            if not key.startswith(("contingent_", "appearance_"))
        } | {
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
        if spell == "invisibility":
            assert actor["concentration_spell_id"] == spell
            assert "invisible" in actor["conditions"]
        else:
            records = [
                r
                for r in lasting["appearance_spell_sources"].values()
                if r["spell"] == spell and r["installed"] is True
            ]
            assert len(records) == 1 and records[0]["spec"] == spec
            assert records[0]["subject"] == "hero"
            assert records[0]["cast_id"] == parent["child_cast_id"] != source_id
            assert actor["concentration_spell_id"] is None
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
        monkeypatch.setattr(dice, "roll", lambda *_: pytest.fail("A saved retry must not reroll."))
        for command, receipt in ((parent_command, parent_receipt), (hit_command, hit)):
            assert owner.execute_action(command) == receipt
            assert owner.snapshot("casting") == final
