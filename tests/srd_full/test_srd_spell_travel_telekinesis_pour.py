"""Plain-water fine control shares the real vial mutation and keeps hand contact intact."""

import json
import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_travel_telekinesis_pour import emit_late_pour
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application import bundled_packages
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from sylvia_gm.domain.package_library import canonical_json_bytes
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests import test_srd_spell_travel_telekinesis_fine as fine
from tests import test_srd_spell_travel_telekinesis_initial_fine as initial
from tests import test_srd_spell_travel_telekinesis_magic as magic
from tests.srd_full.test_srd_spell_travel import SRD, _point
from tests.srd_full.test_srd_spell_travel_telekinesis import _tk_case


@pytest.fixture
def pour_scope(monkeypatch):
    monkeypatch.setattr(fall, "RUNTIME_PATH", Path(os.environ.get(
        "FC14_TK_POUR_RUNTIME", str(fall.RUNTIME_PATH))))
    original = bundled_packages._srd_drafts

    def drafts():
        primary, reference = original()
        record = deepcopy(primary.record)
        template = next(row for row in record["tools"]
                        if row["name"] == "operate_srd_telekinesis_object")
        name = "pour_srd_telekinesis_vial"
        declaration = deepcopy(template)
        declaration["name"] = name
        declaration["input_schema"] = _tool_schema(
            fall._runtime()[0]["tools"][name], tool_name=name)
        record["tools"].append(declaration)
        payloads = dict(primary.payloads)
        path = "content/content-declarations.json"
        declarations = json.loads(payloads[path])
        inclusion = next(
            row for row in declarations["inclusions"]
            if "operate_srd_telekinesis_object" in row.get("links", {}).get("tools", []))
        inclusion["links"]["tools"] = sorted(inclusion["links"]["tools"] + [name])
        payloads[path] = canonical_json_bytes(declarations)
        return NormalizedPackageDraft(record, payloads, primary.format_version), reference

    monkeypatch.setattr(bundled_packages, "_srd_drafts", drafts)
    yield from fall.actual_fall_scope.__wrapped__(monkeypatch)


def _facts(state, *, water=0.5, is_open=True, kind="vial", food=0, capacity=1, food_capacity=1):
    if fine._object(state)["metadata"].get("size_rank") is None:
        budget._run("record_srd_telekinesis_object_size", state, object_id="orb", size="small")
    budget._run("record_srd_object_utility_facts", state, object_id="orb", weight_pounds=1,
                manufactured=True, metal=False, object_kind=kind, inventory_item="")
    budget._run("record_srd_object_container", state, object_id="orb", capacity_gallons=capacity,
                water_gallons=water, is_open=is_open,
                food_capacity_pounds=food_capacity, food_pounds=food)


def _cast(state):
    return budget._run("install_srd_telekinesis", state, caster_state="hero", subject="orb",
                       destination=[0, 0, 0], operation="pour", slot_level=5, save_dc=15,
                       concentration_modifier=3)


@pytest.mark.parametrize("water", [0, 0.5, 1])
def test_initial_pour_records_actual_quantity_plane_position_and_source_once(
    pour_scope, monkeypatch, water
):
    state, _ = _tk_case()
    actual = initial._dice(monkeypatch)
    _facts(state, water=water)
    before = deepcopy(fine._object(state))
    revision = state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"]
    _cast(state)
    source = state[f"{SRD}.state"]["telekinesis_sources"][-1]
    after = fine._object(state)
    metadata = deepcopy(before["metadata"])
    metadata["srd_object_container"]["water_gallons"] = 0
    metadata["srd_poured_contents"] = {
        "water_gallons": water, "tick": source["created_tick"],
        "position": before["position"], "plane": before["metadata"]["plane"],
        "source_id": source["id"],
    }
    assert after == before | {"metadata": metadata}
    assert state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"] == revision + 1
    assert source["subject"] is None and source["restraint_id"] is None
    assert source["expires_tick"] == source["created_tick"] + 200
    assert actual.consumed == []


@pytest.mark.parametrize("defect", ["closed", "kind", "mixed", "held", "far"])
def test_invalid_initial_pour_rejects_before_any_source_action_or_vial_change(
    pour_scope, monkeypatch, defect
):
    state, _ = _tk_case()
    actual = initial._dice(monkeypatch)
    _facts(state, is_open=defect != "closed", kind="container" if defect == "kind" else "vial",
           food=1 if defect == "mixed" else 0)
    if defect == "held":
        fine._object(state)["metadata"]["carried_by"] = "guard"
    if defect == "far":
        fine._object(state)["position"] = [70, 0, 0]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _cast(state)
    assert state == before and actual.consumed == []


def test_original_mage_hand_still_requires_contact_then_pours_same_plain_contents(
    pour_scope, monkeypatch
):
    state, _ = _tk_case()
    actual = initial._dice(monkeypatch)
    _facts(state)
    budget._run("record_srd_hand_sized_form", state, caster_state="hero", form_id="hand-form",
                fiction="Synthetic translucent hand depiction", kind="image",
                extent=[0.5, 0.5, 0.5])
    budget._run("cast_srd_mage_hand", state, caster_state="hero", spell="mage_hand",
                slot_level=0, output_id="hand", form_id="hand-form", point=[0, 5, 0],
                operation="move", object_id="")
    source = next(row for row in state[f"{SRD}.state"]["turn_effects"]
                  if row.get("spell") == "mage_hand" and row.get("active") is True)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="contact|touch"):
        budget._run("operate_srd_mage_hand", state, caster_state="hero",
                    source_id=source["source_id"], object_id="orb", operation="pour")
    assert state == before
    # A second genuine cast creates its admitted hand at the object's actual origin.
    budget._run("cast_srd_mage_hand", state, caster_state="hero", spell="mage_hand",
                slot_level=0, output_id="hand-touching", form_id="hand-form",
                point=_point(state, "orb"), operation="pour", object_id="orb")
    poured = fine._object(state)["metadata"]["srd_poured_contents"]
    assert poured["water_gallons"] == 0.5
    assert poured["position"] == _point(state, "orb")
    assert fine._object(state)["metadata"]["srd_object_container"]["water_gallons"] == 0
    assert actual.consumed == []


def test_actual_created_supply_cannot_be_silently_emptied_as_plain_water(pour_scope, monkeypatch):
    state, _ = _tk_case()
    actual = initial._dice(monkeypatch)
    _facts(state, water=0, capacity=40, food_capacity=50)
    budget._run("cast_spell_object_content_spell", state, caster_state="hero",
                spell="create_food_and_water", slot_level=3, object_container_ids=["orb"])
    assert fine._object(state)["metadata"]["srd_created_supply"]["water_gallons"] == 30
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Generated supply or mixed"):
        _cast(state)
    assert state == before and actual.consumed == []


def test_later_pour_requires_actual_magic_ability_and_restores_after_exact_field_end(
    pour_scope, monkeypatch
):
    state, source, actual = magic._ready(monkeypatch, [25, 5, 0])
    _facts(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="suppresses this Telekinesis"):
        budget._run("pour_srd_telekinesis_vial", state, caster_state="hero",
                    source_id=source["id"], object_id="orb")
    assert state == before
    budget._run("end_concentration", state, caster_state="guard")
    budget._run("pour_srd_telekinesis_vial", state, caster_state="hero",
                source_id=source["id"], object_id="orb")
    assert fine._object(state)["metadata"]["srd_poured_contents"]["water_gallons"] == 0.5
    current = state[f"{SRD}.state"]["telekinesis_sources"][-1]
    assert current["id"] == source["id"] and current["created_tick"] == source["created_tick"]
    assert actual.consumed == []


@pytest.mark.parametrize("initial_pour", [True, False], ids=["initial", "later"])
def test_genuine_paid_pour_keeps_same_source_costs_and_receipt_after_reopen_and_retry(
    pour_scope, tmp_path, monkeypatch, initial_pour
):
    def case(spell):
        state, data, params = fall._cast_case(spell)
        params.update(telekinesis_subject="orb" if initial_pour else "",
                      telekinesis_destination=[0, 0, 0],
                      telekinesis_operation="pour" if initial_pour else "move")
        return state, data, params

    monkeypatch.setattr(public, "_spell_case", case)
    actual = initial._dice(monkeypatch)
    path = tmp_path / "paid-pour.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "telekinesis")
        mechanics = StatelessMcpMechanics(owner)
        command = initial._command(params)

        def action(identity, name, parameters):
            request = replace(command, action_id=identity, idempotency_key=identity,
                              expected_revision=owner.snapshot("casting").revision,
                              tool_name=name, parameters=parameters)
            receipt = mechanics.execute_action(request)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
            return request, receipt

        action("size", "record_srd_telekinesis_object_size", {"object_id": "orb", "size": "small"})
        action("vial", "record_srd_object_utility_facts", {
            "object_id": "orb", "weight_pounds": 1, "manufactured": True, "metal": False,
            "object_kind": "vial", "inventory_item": "",
        })
        action("contents", "record_srd_object_container", {
            "object_id": "orb", "capacity_gallons": 1, "water_gallons": 0.5,
            "is_open": True, "food_capacity_pounds": 0, "food_pounds": 0,
        })
        before = owner.snapshot("casting")
        bad = replace(command, action_id="bad", idempotency_key="bad",
                      expected_revision=before.revision, parameters=params | {
                          "telekinesis_subject": "orb", "telekinesis_operation": "pour",
                          "telekinesis_destination": [5, 0, 0]})
        with pytest.raises(MechanicsRejected):
            mechanics.execute_action(bad)
        assert owner.snapshot("casting") == before
        command = replace(command, expected_revision=before.revision)
        paid = mechanics.execute_action(command)
        assert paid.status is ActionStatus.COMMITTED, paid.result
        saved = owner.snapshot("casting")
        source = saved.state[f"{SRD}.state"]["telekinesis_sources"][-1]
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
        assert mechanics.execute_action(command) == paid
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        if not initial_pour:
            clock = initial._command({"elapsed_rounds": 1}, saved.revision,
                                     "advance_srd_lasting_time", "clock")
            elapsed = mechanics.execute_action(clock)
            assert elapsed.status is ActionStatus.COMMITTED, elapsed.result
            pour = initial._command({"caster_state": "hero", "source_id": source["id"],
                                     "object_id": "orb"}, owner.snapshot("casting").revision,
                                    "pour_srd_telekinesis_vial", "pour")
            receipt = mechanics.execute_action(pour)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
            assert mechanics.execute_action(pour) == receipt
        final = owner.snapshot("casting")
        current = final.state[f"{SRD}.state"]["telekinesis_sources"][-1]
        assert current["id"] == source["id"] and current["cast_id"] == source["cast_id"]
        assert current["created_tick"] == source["created_tick"]
        assert current["expires_tick"] == source["expires_tick"]
        vial = fine._object(final.state)
        assert vial["metadata"]["srd_object_container"]["water_gallons"] == 0
        assert vial["metadata"]["srd_poured_contents"]["water_gallons"] == 0.5
        assert vial["metadata"]["srd_poured_contents"]["source_id"] == source["id"]
        assert _point(final.state, "orb") == _point(before.state, "orb")
        assert final.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
        assert mechanics.execute_action(command) == paid
        assert owner.snapshot("casting") == final
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == final and actual.consumed == []


def test_late_extraction_is_definition_only_and_keeps_original_hand_body(pour_scope):
    encoded = fall._runtime()[1].decode()
    original = _load_runtime(encoded)
    final = emit_late_pour(encoded)
    assert emit_late_pour(final) == final
    assert _load_runtime(final)["tools"]["operate_srd_mage_hand"] == (
        original["tools"]["operate_srd_mage_hand"])
