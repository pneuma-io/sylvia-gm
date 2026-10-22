"""Contingent locating reuses native choices and the current self information owner."""

import hashlib
import os
from copy import deepcopy
from pathlib import Path
from random import Random

import pytest
from scripts import srd_contingency_child_casting as child
from scripts.srd_full.srd_control_behaviors import _walk

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.runtime._effect_executor import execute_effects
from sylvia_gm.runtime._types import ExecutionContext
from tests import test_srd_contingency_child_casting as native
from tests import test_srd_contingency_child_public as prior
from tests import test_srd_contingency_combined_public as combined
from tests import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_npc_spell_casting import _act

PROOF = Path(
    os.environ.get(
        "FC14_CONTINGENCY_INFORMATION_RUNTIME",
        str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/fc14-contingency-information-runtime/selected-runtime.yaml"),
    )
)


def test_native_locate_selection_and_family_effects_are_reused():
    tools, definitions = native.actual.__wrapped__()
    original = tools[child.DISPATCHES["information_spell"]]
    selection = child._native_information_selection(original)
    assert selection == [
        e for e in original["effects"] if e.get("condition") == selection[0]["condition"]
    ]
    preparation = deepcopy(tools["prepare_srd_contingent_child"])
    child._prepare(preparation, child._selected(definitions), information_selection=selection)
    saved = next(
        e["set"]["value"]
        for e in preparation["effects"]
        if e.get("set", {}).get("field") == child.PREPARED
    )
    assert {name: saved[name] for name in child.INFORMATION_CHOICES} == {
        name: name for name in child.INFORMATION_CHOICES
    }
    assert selection[0] in preparation["effects"]
    assert preparation["effects"].index(selection[0]) < next(
        i
        for i, e in enumerate(preparation["effects"])
        if e.get("set", {}).get("field") == child.PREPARED
    )
    retained = child._retained_family(original)
    assert retained["effects"] == original["effects"]
    assert retained["metadata"] == original["metadata"]
    assert any(n.get("call") == "scan_srd_information_spell" for n in _walk(retained["effects"]))
    repeated = deepcopy(preparation)
    child._prepare(repeated, child._selected(definitions), information_selection=selection)
    assert repeated == preparation


def test_locate_factual_requirements_reject_missing_setup_choices():
    tools, _ = native.actual.__wrapped__()
    selection = child._native_information_selection(tools[child.DISPATCHES["information_spell"]])
    params = {
        "spell": "locate_object",
        "information_kind": "weapon",
        "information_subject": "",
        "information_familiar": False,
        "information_seen_close": False,
    }
    with pytest.raises(Exception, match="familiar"):
        execute_effects(selection, ExecutionContext(params=params))
    params["information_familiar"] = True
    execute_effects(selection, ExecutionContext(params=params))
    params.update(spell="locate_creature", information_seen_close=False)
    with pytest.raises(Exception, match="30 feet"):
        execute_effects(selection, ExecutionContext(params=params))
    params["information_seen_close"] = True
    execute_effects(selection, ExecutionContext(params=params))


def test_actual_information_dispatch_forwards_saved_facts_and_keeps_native_exports():
    tools, _ = native.actual.__wrapped__()
    original = next(
        n
        for n in _walk(tools["cast_spell"]["effects"])
        if n.get("call") == child.DISPATCHES["information_spell"]
    )
    retained = child._dispatch(original)
    assert retained["export_context"] == original["export_context"]
    for name in child.INFORMATION_CHOICES:
        assert retained["params"][name] == "{_contingent_" + name + "}"
    assert retained["params"]["target_state"] == "{_target_state_id}"
    assert retained["params"]["caster_state"] == "{_target_state_id}"
    assert original == next(
        n
        for n in _walk(tools["cast_spell"]["effects"])
        if n.get("call") == child.DISPATCHES["information_spell"]
    )


@pytest.fixture(scope="module")
def actual_archive():
    assert hashlib.sha256(PROOF.read_bytes()).hexdigest() == (
        "0dfd38a8b61952ab9cafc0a672ee9cecff5a7548f46916e1b3591356aa4f8c1b"
    )
    original = combined.PROOF, combined.PUBLIC
    combined.PROOF = PROOF
    combined.PUBLIC = combined.PUBLIC | {"query_srd_information_spell"}
    combined._runtime.cache_clear()
    combined._selected_archive.cache_clear()
    try:
        yield combined._selected_archive()
    finally:
        combined.PROOF, combined.PUBLIC = original
        combined._runtime.cache_clear()
        combined._selected_archive.cache_clear()


def test_paid_locating_retains_factual_subject_then_starts_native_source_once(
    tmp_path, monkeypatch, actual_archive
):
    state, data, parameters, _, level = prior._case(monkeypatch, "locate_object")
    hero = state[f"{combined.SRD}.entities"]["hero"]
    hero["resources"][f"spell_slot_{level}"] = 1
    hero["resources"]["spell_slot_1"] = 1
    scene = state[f"{combined.SRD}.state"]["spatial_scene"]
    scene["map"]["bounds"] = {"min": [-1500, -1500, -1500], "max": [1500, 1500, 1500]}
    scene["map"]["grid"]["origin"] = [-1500, -1500]
    scene["map"]["layers"][0].update(min_elevation=-1500, max_elevation=1500)
    object_row = next(row for row in scene["entities"] if row["id"] == "reliquary")
    object_row["position"] = [25, 0, 0]
    object_row["metadata"]["kind"] = "jewel"
    facts = {
        "information_kind": "",
        "information_subject": "reliquary",
        "information_familiar": True,
        "information_seen_close": True,
    }
    parameters.update(facts)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _: (state, data, parameters))
    monkeypatch.setattr(public_casting, "bundled_srd_archive", lambda: actual_archive)
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    database = tmp_path / "locating.sqlite3"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        public_casting._create_spell_campaign(owner, "contingency")
        parent_receipt, parent_command = combined._cast(owner, parameters, 100)
        armed = owner.snapshot("casting")
        actor = armed.state[f"{combined.SRD}.entities"]["hero"]
        source_id = actor["contingency_source_id"]
        parent = armed.state[f"{combined.SRD}.state"]["contingency_sources"][source_id]
        assert {name: parent["child"][name] for name in facts} == facts
        assert actor["resources"][f"spell_slot_{level}"] == 0
        assert actor["concentration_spell_id"] is None
        assert not any(
            r.get("spell") == "locate_object"
            for r in armed.state[f"{combined.SRD}.state"]["turn_effects"]
        )
        resources = deepcopy(actor["resources"])
        quantities = {r["id"]: r["quantity"] for r in actor["spell_materials"]}
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == armed
        monkeypatch.setattr(dice, "_rng", Random(0))
        missile = {
            key: value
            for key, value in parameters.items()
            if not key.startswith(("contingent_", "information_"))
        } | {
            "spell": "magic_missile",
            "slot_resource": "spell_slot_1",
            "target": "hero",
            "material_item_ids": [],
        }
        hit, hit_command = _act(owner, "cast_spell_outside_combat", **missile)
        assert hit.status is ActionStatus.COMMITTED, hit.result
        released = owner.snapshot("casting")
        lasting = released.state[f"{combined.SRD}.state"]
        actor = released.state[f"{combined.SRD}.entities"]["hero"]
        parent = lasting["contingency_sources"][source_id]
        assert parent["status"] == "released" and parent["child_effect_applied"] is True
        assert parent["trigger_hp_before"] > parent["trigger_hp_after"]
        assert actor["concentration_spell_id"] == "locate_object"
        record = next(r for r in lasting["turn_effects"] if r.get("spell") == "locate_object")
        assert record["target"] == "hero"
        assert record["sought_subject"] == "reliquary"
        assert actor["last_spell_source_cast_id"] == parent["child_cast_id"] != source_id
        assert actor["last_spell_source_spell"] == "locate_object"
        assert record["expires_tick"] > armed.state[f"{combined.SRD}.state"]["lasting_tick"]
        expected = dict(resources)
        expected["spell_slot_1"] -= 1
        assert dict(actor["resources"]) == expected
        assert {r["id"]: r["quantity"] for r in actor["spell_materials"]} == quantities
        query, query_command = _act(
            owner, "query_srd_information_spell", caster_state="hero", spell="locate_object"
        )
        assert query.status is ActionStatus.COMMITTED, query.result
        rows = {row["label"]: row["value"] for row in query.result["result_card"]["summary"]}
        assert rows["Present"] is True and rows["Direction"] == [1, 0, 0]
        assert rows["Distance"] is None
        final = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
        monkeypatch.setattr(dice, "roll", lambda *_: pytest.fail("A saved retry must not reroll."))
        for command, receipt in (
            (parent_command, parent_receipt),
            (hit_command, hit),
            (query_command, query),
        ):
            assert owner.execute_action(command) == receipt
            assert owner.snapshot("casting") == final
