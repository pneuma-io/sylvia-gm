"""Mansion staff pour real plain water through the unchanged canonical vial owner."""

from copy import deepcopy
from pathlib import Path

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_dwelling as dwelling
from tests import test_srd_spell_travel_furnishings as furnishings
from tests import test_srd_spell_travel_servants as staff
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests import test_srd_spell_travel_telekinesis_initial_fine as initial
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_spell_travel import SRD
from tests.srd_full.test_srd_spell_travel_telekinesis import _tk_case

servant_scope = staff.servant_scope
VIAL = [
    {
        "id": "vial",
        "offset": [5.75, 2, 0],
        "extent": [0.5, 0.5, 0.5],
        "material": "glass",
        "appearance": "an open clear glass vial",
    }
]


@pytest.fixture
def servant_pour_scope(servant_scope, monkeypatch):
    monkeypatch.setattr(
        fall, "RUNTIME_PATH", Path(".scratch/mansion-servant-pour-composition/runtime.yaml")
    )


def _facts(state, *, water=0.25, opened=True, food=0):
    budget._run(
        "record_srd_object_utility_facts",
        state,
        object_id="vial",
        weight_pounds=1,
        manufactured=True,
        metal=False,
        object_kind="vial",
        inventory_item="",
    )
    budget._run(
        "record_srd_object_container",
        state,
        object_id="vial",
        capacity_gallons=0.5,
        water_gallons=water,
        is_open=opened,
        food_capacity_pounds=1,
        food_pounds=food,
    )


def _ready(**facts):
    state, _ = _tk_case()
    dwelling._install(state, servants=deepcopy(staff.SERVANT), furnishings=deepcopy(VIAL))
    _facts(state, **facts)
    dwelling._enter(state)
    return state


def _pour(state):
    return budget._run(
        "command_srd_mansion_servant",
        state,
        caster_state="hero",
        source_id="house",
        servant_id="attendant",
        object_id="vial",
        operation="pour",
    )


@pytest.mark.parametrize("water", [0, 0.25, 0.5])
def test_staff_pour_keeps_actual_quantity_source_plane_position_and_one_mutation(
    servant_pour_scope, water
):
    state = _ready(water=water)
    before = deepcopy(furnishings._body(state, "vial"))
    resources = deepcopy(state[f"{SRD}.entities"]["hero"])
    revision = state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"]
    tick = state[f"{SRD}.state"]["lasting_tick"]
    _pour(state)
    body = furnishings._body(state, "vial")
    metadata = deepcopy(before["metadata"])
    metadata["srd_object_container"]["water_gallons"] = 0
    metadata["srd_poured_contents"] = {
        "water_gallons": water,
        "tick": tick,
        "position": before["position"],
        "plane": before["metadata"]["plane"],
        "source_id": "house",
    }
    assert body == before | {"metadata": metadata}
    assert state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"] == revision + 1
    order = dwelling._source(state)["servant_orders"][-1]
    assert order["operation"] == "pour" and order["water_gallons"] == water
    assert order["servant_id"] == "attendant" and order["source_id"] == "house"
    assert state[f"{SRD}.entities"]["hero"] == resources
    assert state[f"{SRD}.state"]["lasting_tick"] == tick


@pytest.mark.parametrize("defect", ["closed", "mixed", "contact", "expiry", "antimagic"])
def test_staff_invalid_pour_preserves_vial_resources_and_orders(servant_pour_scope, defect):
    state = _ready(opened=defect != "closed", food=1 if defect == "mixed" else 0)
    if defect == "contact":
        destination = list(furnishings._body(state, "attendant")["position"])
        destination[1] += 4
        budget._run(
            "move_srd_travel_subject",
            state,
            subject="attendant",
            destination=destination,
            plane=dwelling._source(state)["room_id"],
        )
    if defect == "expiry":
        budget._run("advance_srd_lasting_time", state, elapsed_rounds=14400)
    if defect == "antimagic":
        dwelling._enter(state, "guard")
        budget._run(
            "cast_srd_spell_magic_zone",
            state,
            caster_state="guard",
            spell="antimagic_field",
            slot_level=8,
            concentration_save_modifier=0,
            save_dc=15,
        )
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _pour(state)
    assert state == before
    if defect == "antimagic":
        budget._run("end_concentration", state, caster_state="guard")
        _pour(state)
        assert (
            furnishings._body(state, "vial")["metadata"]["srd_poured_contents"]["water_gallons"]
            == 0.25
        )


def _case(spell):
    state, data, params = staff._servant_case(spell)
    params["mansion_furnishings"] = deepcopy(VIAL)
    return state, data, params


def test_paid_staff_pour_saved_reopen_retry_consumes_canonical_contents_once(
    servant_pour_scope, monkeypatch, tmp_path
):
    monkeypatch.setattr(public, "_spell_case", _case)
    actual = initial._dice(monkeypatch)
    path = tmp_path / "staff-pour.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "magnificent_mansion")
        mechanics = StatelessMcpMechanics(owner)
        steps = [
            ("begin_srd_spell_cast", _begin(params), "begin"),
            (
                "continue_srd_spell_cast",
                {
                    "caster_state": "hero",
                    "elapsed_rounds": 10,
                    "uninterrupted": True,
                    "verbal_component_available": True,
                    "somatic_component_available": True,
                    "material_component_available": True,
                },
                "ready",
            ),
            ("cast_spell_outside_combat", params, "paid"),
            (
                "record_srd_object_utility_facts",
                {
                    "object_id": "vial",
                    "weight_pounds": 1,
                    "manufactured": True,
                    "metal": False,
                    "object_kind": "vial",
                    "inventory_item": "",
                },
                "vial",
            ),
            (
                "record_srd_object_container",
                {
                    "object_id": "vial",
                    "capacity_gallons": 0.5,
                    "water_gallons": 0.25,
                    "is_open": True,
                    "food_capacity_pounds": 0,
                    "food_pounds": 0,
                },
                "contents",
            ),
        ]
        for name, arguments, key in steps:
            receipt = mechanics.execute_action(
                dwelling._command(arguments, owner.snapshot("casting").revision, name, key)
            )
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        source, room = dwelling._source(saved.state), dwelling._room(saved.state)
        for name, arguments, key in (
            (
                "move_srd_scene_creature",
                {"actor_state": "hero", "destination": [5, 9, 0], "mode": "walk"},
                "approach",
            ),
            (
                "traverse_srd_dwelling_door",
                {
                    "actor_state": "hero",
                    "source_id": source["id"],
                    "door_subject": source["outer_id"],
                    "destination": [room["shape"]["min"][0] + 1.5, 0, room["shape"]["min"][2]],
                },
                "enter",
            ),
            (
                "command_srd_mansion_servant",
                {
                    "caster_state": "hero",
                    "source_id": source["id"],
                    "servant_id": "attendant",
                    "object_id": "vial",
                    "operation": "pour",
                },
                "pour",
            ),
        ):
            command = dwelling._command(arguments, owner.snapshot("casting").revision, name, key)
            receipt = mechanics.execute_action(command)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
            after = owner.snapshot("casting")
            assert mechanics.execute_action(command) == receipt
            assert owner.snapshot("casting") == after
        assert (
            furnishings._body(after.state, "vial")["metadata"]["srd_object_container"][
                "water_gallons"
            ]
            == 0
        )
        assert (
            furnishings._body(after.state, "vial")["metadata"]["srd_poured_contents"][
                "water_gallons"
            ]
            == 0.25
        )
        assert dwelling._source(after.state)["servant_orders"][0]["water_gallons"] == 0.25
        assert len(dwelling._source(after.state)["servant_orders"]) == 1
        assert after.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_7"] == 2
        assert actual.consumed == []
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == after
