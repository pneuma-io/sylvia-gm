"""Canonical access synchronizes admitted vessels for later supply and water effects."""

import os
from copy import deepcopy
from dataclasses import replace
from pathlib import Path

import pytest

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import MechanicsRejected, RulesetPreconditionRejected
from tests import test_srd_action_budget as budget
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as fall
from tests.srd_full.test_srd_spell_travel import SRD, _case, _point, _TravelDice, dice
from tests.srd_full.test_srd_spell_travel_telekinesis import _tk_case
from tests.srd_full.test_srd_spell_travel_telekinesis_fine import _object, _operate, _ready


@pytest.fixture
def container_scope(monkeypatch):
    monkeypatch.setattr(
        fall, "RUNTIME_PATH",
        Path(os.environ.get("FC14_CONTAINER_ACCESS_RUNTIME", str(fall.RUNTIME_PATH))),
    )
    yield from fall.actual_fall_scope.__wrapped__(monkeypatch)


def _admit_container(state):
    budget._run(
        "record_srd_object_container", state, object_id="orb", capacity_gallons=40,
        water_gallons=8, is_open=False, food_capacity_pounds=50, food_pounds=0,
    )


@pytest.mark.parametrize("operator", ["ordinary", "telekinesis"])
def test_same_access_mutation_preserves_actual_container_contents_and_other_metadata(
    container_scope, monkeypatch, operator
):
    state, source, actual = _ready(monkeypatch)
    _admit_container(state)
    before = deepcopy(_object(state)["metadata"])
    origin = _point(state, "orb")
    revision = state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"]
    if operator == "ordinary":
        budget._run(
            "operate_srd_locked_object", state, actor_state="guard", object_id="orb",
            operation="open",
        )
    else:
        _operate(state, source)
    after = _object(state)["metadata"]
    expected = deepcopy(before)
    expected["srd_object_access"]["closed"] = False
    expected["srd_object_container"]["is_open"] = True
    assert after == expected
    assert _point(state, "orb") == origin
    assert state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"] == revision + 1
    if operator == "ordinary":
        budget._run(
            "operate_srd_locked_object", state, actor_state="guard", object_id="orb",
            operation="close",
        )
    else:
        _operate(state, source, "close")
    assert _object(state)["metadata"] == before
    assert actual.consumed == [(20, 1)]


def test_noncontainer_access_does_not_create_an_unadmitted_vessel(container_scope, monkeypatch):
    state, source, _ = _ready(monkeypatch)
    _operate(state, source)
    assert "srd_object_container" not in _object(state)["metadata"]
    budget._run(
        "operate_srd_locked_object", state, actor_state="guard", object_id="orb",
        operation="close",
    )
    assert "srd_object_container" not in _object(state)["metadata"]


@pytest.mark.parametrize("operator", ["ordinary", "telekinesis"])
def test_locked_vessel_never_changes_either_access_fact(container_scope, monkeypatch, operator):
    state, source, actual = _ready(monkeypatch, locks=("padlock",))
    _admit_container(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="mundane access restriction"):
        if operator == "ordinary":
            budget._run(
                "operate_srd_locked_object", state, actor_state="guard", object_id="orb",
                operation="open",
            )
        else:
            _operate(state, source)
    assert state == before and actual.consumed == [(20, 1)]


@pytest.mark.parametrize("operator", ["ordinary", "telekinesis"])
def test_paid_access_then_actual_water_and_supply_survive_reopen_and_exact_retries(
    container_scope, tmp_path, monkeypatch, operator
):
    def actual_case(_spell):
        state, data = _tk_case("telekinesis")
        data.update(level=14, classes=[{"id": "wizard", "level": 9},
                                      {"id": "cleric", "level": 5}])
        data["abilities"]["wisdom"] = 18
        data["spellcasting_grants"].append({
            "source": "cleric", "ability": "wisdom",
            "spells": ["create_or_destroy_water", "create_food_and_water"],
        })
        actor = state[f"{SRD}.entities"]["hero"]
        actor["prepared_spells"].extend(["create_or_destroy_water", "create_food_and_water"])
        water, _, parameters = _case("create_or_destroy_water")
        actor["spell_materials"] = deepcopy(water[f"{SRD}.entities"]["hero"]["spell_materials"])
        actor["inventory"].extend(water[f"{SRD}.entities"]["hero"]["inventory"])
        if operator == "ordinary":
            _object(state)["position"] = [3, 0, 0]
        return state, data, parameters

    monkeypatch.setattr(public, "_spell_case", actual_case)
    actual = _TravelDice([])
    monkeypatch.setattr(dice, "_rng", actual)
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / f"container-{operator}.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, water_parameters = public._create_spell_campaign(owner, "create_or_destroy_water")
        command = ActionCommand(
            campaign_id="casting", action_id="access-facts", idempotency_key="access-facts",
            owner_id="casting-owner", writer=WriterRole.DM, expected_revision=0,
            turn_id="casting-turn", package_id=SRD, tool_name="record_srd_object_access",
            parameters={"object_id": "orb", "object_kind": "container", "closed": True,
                        "locks": [], "stuck": False, "barred": False},
        )
        mechanics = StatelessMcpMechanics(owner)

        def action(identity, name, parameters):
            request = replace(
                command, action_id=identity, idempotency_key=identity,
                expected_revision=owner.snapshot("casting").revision,
                tool_name=name, parameters=parameters,
            )
            receipt = mechanics.execute_action(request)
            assert receipt.status is ActionStatus.COMMITTED, receipt.result
            return request, receipt

        action("access-facts", command.tool_name, command.parameters)
        action("container-facts", "record_srd_object_container", {
            "object_id": "orb", "capacity_gallons": 40, "water_gallons": 0,
            "is_open": False, "food_capacity_pounds": 50, "food_pounds": 0,
        })
        if operator == "telekinesis":
            action("object-size", "record_srd_telekinesis_object_size", {
                "object_id": "orb", "size": "small",
            })
            _, _, cast_parameters = _case("telekinesis")
            action("paid-ability", "cast_spell_outside_combat", cast_parameters)
            action("later", "advance_srd_lasting_time", {"elapsed_rounds": 1})
            source = owner.snapshot("casting").state[f"{SRD}.state"]["telekinesis_sources"][-1]
            source_id = source["id"]
            access_name = "operate_srd_telekinesis_object"
            access_parameters = {"caster_state": "hero", "source_id": source_id,
                                 "object_id": "orb", "operation": "open"}
        else:
            access_name = "operate_srd_locked_object"
            access_parameters = {"actor_state": "hero", "object_id": "orb", "operation": "open"}
        water_parameters.update(
            object_target_id="orb", object_mode="create_water", object_quantity=8,
        )
        closed = owner.snapshot("casting")
        rejected = replace(
            command, action_id="closed-water", idempotency_key="closed-water",
            expected_revision=closed.revision, tool_name="cast_spell_outside_combat",
            parameters=water_parameters,
        )
        with pytest.raises(MechanicsRejected):
            mechanics.execute_action(rejected)
        assert owner.snapshot("casting") == closed
        opened, opened_receipt = action("open", access_name, access_parameters)
        water, water_receipt = action("paid-water", "cast_spell_outside_combat", water_parameters)
        saved = owner.snapshot("casting")
        assert _object(saved.state)["metadata"]["srd_object_container"]["water_gallons"] == 8
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
        assert mechanics.execute_action(opened) == opened_receipt
        assert mechanics.execute_action(water) == water_receipt
        assert owner.snapshot("casting") == saved
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        mechanics = StatelessMcpMechanics(owner)
        _, _, food_parameters = _case("create_food_and_water")
        food_parameters.update(object_target_id="", object_container_ids=["orb"])
        food = replace(
            command, action_id="paid-supply", idempotency_key="paid-supply",
            expected_revision=saved.revision, tool_name="cast_spell_outside_combat",
            parameters=food_parameters,
        )
        supplied = mechanics.execute_action(food)
        assert supplied.status is ActionStatus.COMMITTED, supplied.result
        saved = owner.snapshot("casting")
        metadata = _object(saved.state)["metadata"]
        assert metadata["srd_created_supply"]["food_pounds"] == 45
        assert metadata["srd_created_supply"]["water_gallons"] == 30
        assert metadata["srd_object_container"]["water_gallons"] == 8
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
        assert mechanics.execute_action(food) == supplied
        assert owner.snapshot("casting") == saved
        close = replace(
            opened, action_id="close", idempotency_key="close", expected_revision=saved.revision,
            parameters={**access_parameters, "operation": "close"},
        )
        closed_receipt = mechanics.execute_action(close)
        assert closed_receipt.status is ActionStatus.COMMITTED, closed_receipt.result
        saved = owner.snapshot("casting")
        assert _object(saved.state)["metadata"]["srd_object_container"]["is_open"] is False
        assert _object(saved.state)["metadata"]["srd_object_access"]["closed"] is True
        assert mechanics.execute_action(close) == closed_receipt
        assert owner.snapshot("casting") == saved and actual.consumed == []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        assert StatelessMcpMechanics(owner).execute_action(food) == supplied
        assert owner.snapshot("casting") == saved
