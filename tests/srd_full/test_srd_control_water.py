"""Actual admitted standing water and paid Part Water alter later whole-body movement."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_environment import SRD, _fixture, _ground, _position
from tests.srd_full.test_srd_spell_wave3_orders import _turn


def _case():
    state, data, params = _fixture("control_water", (50, 0, 0))
    _ground(state)
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(
        {
            "id": "pool",
            "map_id": "orders",
            "volume": {"kind": "box", "min": [20, -30, 0], "max": [80, 30, 20]},
            "active": True,
            "movement": "passable",
            "visibility": "clear",
            "occupies_cells": False,
            "metadata": {"plane": "material"},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )
    _position(state, "guard")["position"] = [30, 0, 0]
    state[f"{SRD}.entities"]["guard"]["swim_speed"] = 30
    _position(state, "other")["position"] = [10, 8, 0]
    params.update(
        environment_water_geometry_id="pool",
        environment_water_side=50,
        environment_trench_width=10,
        environment_trench_axis="east",
    )
    return state, data, params


def _cast():
    state, data, params = _case()
    _run("record_srd_water_body", state, geometry_id="pool", flow="standing")
    result = _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert result.result["status"] == "committed"
    return state, data, params


def _source(state):
    return state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]


def _reject(state, tool, message, **parameters):
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match=message):
        _run(tool, state, **parameters)
    assert state == before


def test_water_paid_source_keeps_real_body_cube_trench_and_clock():
    state, _, _ = _cast()
    source = _source(state)
    assert source["water_cube"] == {"kind": "box", "min": [25, -25, 0], "max": [75, 25, 50]}
    assert source["water_trench"] == {"kind": "box", "min": [25, -5, 0], "max": [75, 5, 20]}
    assert source["water_geometry_id"] == "pool" and source["expires_tick"] == 200
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
    assert _position(state, "guard")["position"] == [30, 0, 0]
    result = _run("resolve_srd_water_point", state, point=[30, 0, 0], plane="material")
    assert result.result["context"]["_water_parted"]
    assert not result.result["context"]["_water_present"]


def test_water_actual_dry_whole_body_walk_retains_normal_budget():
    state, _, _ = _cast()
    _turn(state, "guard")
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[40, 0, 0])
    assert _position(state, "guard")["position"] == [40, 0, 0]
    assert state[f"{SRD}.combat"]["movement_remaining"] == 20


def test_water_without_part_requires_real_swim_cost():
    state, _, _ = _case()
    _run("record_srd_water_body", state, geometry_id="pool", flow="standing")
    _turn(state, "guard")
    _reject(
        state, "move_srd_scene_creature", "swimming", actor_state="guard", destination=[40, 0, 0]
    )
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[40, 0, 0], mode="swim")
    assert _position(state, "guard")["position"] == [40, 0, 0]
    assert state[f"{SRD}.combat"]["movement_remaining"] == 20


def test_water_real_source_end_refill_then_actual_water_returns():
    state, _, _ = _cast()
    _run("end_concentration", state, caster_state="hero")
    returns = [
        r
        for r in state[f"{SRD}.state"]["turn_effects"]
        if r.get("kind") == "environment_water_refill"
    ]
    assert len(returns) == 1 and returns[0]["ready_tick"] == 2
    _turn(state, "guard")
    _reject(state, "move_srd_scene_creature", "refill", actor_state="guard", destination=[40, 0, 0])
    _run("end_combat_encounter", state, end_reason="fighting_stopped")
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _turn(state, "guard")
    _reject(
        state, "move_srd_scene_creature", "swimming", actor_state="guard", destination=[40, 0, 0]
    )
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[40, 0, 0], mode="swim")
    assert _position(state, "guard")["position"] == [40, 0, 0]


@pytest.mark.parametrize("change", ["unknown", "flowing", "plane", "width", "depth"])
def test_water_unknown_or_unsupported_source_facts_reject_before_cost(change):
    state, data, params = _case()
    if change != "unknown":
        _run(
            "record_srd_water_body",
            state,
            geometry_id="pool",
            flow="flowing" if change == "flowing" else "standing",
        )
    if change == "plane":
        state[f"{SRD}.state"]["spatial_scene"]["geometry"][-1]["metadata"]["plane"] = "ethereal"
    if change == "width":
        params["environment_trench_width"] = 50
    if change == "depth":
        params["environment_water_side"] = 10
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before


def test_water_dry_anchor_cannot_leave_part_of_the_real_body_in_water():
    state, _, _ = _cast()
    _position(state, "guard")["position"] = [30, 4.9, 0]
    _turn(state, "guard")
    _reject(
        state,
        "move_srd_scene_creature",
        "whole moving Box footprint",
        actor_state="guard",
        destination=[40, 4.9, 0],
    )


def test_water_whole_wet_route_cannot_cross_the_dry_trench():
    state, _, _ = _cast()
    _position(state, "guard")["position"] = [30, -10, 0]
    _turn(state, "guard")
    _reject(
        state,
        "move_srd_scene_creature",
        "wet route cannot cross",
        actor_state="guard",
        destination=[30, 10, 0],
        mode="swim",
    )


def test_water_outside_endpoints_cannot_skip_actual_body_contact_in_between():
    state, _, _ = _case()
    _run("record_srd_water_body", state, geometry_id="pool", flow="standing")
    _position(state, "guard")["position"] = [15, 29, 0]
    _turn(state, "guard")
    _reject(
        state,
        "qualify_srd_water_route",
        "Mixed water boundary",
        actor_state="guard",
        start=[15, 29, 0],
        end=[85, 29, 0],
        mode="walk",
    )


def test_water_actual_dry_trench_does_not_invent_bottom_support():
    state, _, _ = _cast()
    state[f"{SRD}.state"]["spatial_scene"]["geometry"][0]["active"] = False
    _turn(state, "guard")
    _reject(
        state,
        "move_srd_scene_creature",
        "ground|support",
        actor_state="guard",
        destination=[40, 0, 0],
    )


def test_water_real_later_magic_action_repeats_without_new_slot():
    state, data, _ = _cast()
    _reject(state, "change_srd_control_water", "later turn", caster_state="hero", area_id="hero")
    _turn(state, "hero", data)
    slots = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    _run(
        "change_srd_control_water",
        state,
        participant_data=data,
        caster_state="hero",
        area_id="hero",
        trench_width=20,
        trench_axis="north",
    )
    assert _source(state)["water_trench"] == {
        "kind": "box",
        "min": [40, -25, 0],
        "max": [60, 25, 20],
    }
    assert state[f"{SRD}.entities"]["hero"]["resources"] == slots
    assert not state[f"{SRD}.combat"]["action_available"]
    _reject(
        state,
        "change_srd_control_water",
        "Action|action",
        caster_state="hero",
        area_id="hero",
        trench_width=15,
    )


def test_water_refill_survives_actual_caster_source_replacement():
    state, _, _ = _cast()
    _run(
        "cast_spell_environment",
        state,
        caster_state="hero",
        spell="fog_cloud",
        slot_level=1,
        concentration_save_modifier=0,
        origin=[5, 0, 0],
    )
    assert _source(state)["spell"] == "fog_cloud"
    point = _run("resolve_srd_water_point", state, point=[30, 0, 0], plane="material")
    assert point.result["context"]["_water_uncertain"]
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    point = _run("resolve_srd_water_point", state, point=[30, 0, 0], plane="material")
    assert point.result["context"]["_water_present"]
    assert not point.result["context"]["_water_uncertain"]
    assert _source(state)["active"] and _source(state)["spell"] == "fog_cloud"


@pytest.mark.parametrize("elapsed,uncertain", [(100, True), (101, False)])
def test_water_actual_clock_expiry_starts_refill_at_the_real_deadline(elapsed, uncertain):
    state, _, _ = _cast()
    _run("advance_srd_lasting_time", state, elapsed_rounds=elapsed)
    assert not _source(state)["active"]
    point = _run("resolve_srd_water_point", state, point=[30, 0, 0], plane="material")
    assert point.result["context"]["_water_uncertain"] is uncertain
    assert point.result["context"]["_water_present"]


def test_water_same_actual_paid_combat_cast_turn_cannot_repeat():
    state, data, params = _case()
    _run("record_srd_water_body", state, geometry_id="pool", flow="standing")
    _turn(state, "hero", data)
    paid = _run("cast_spell", state, participant_data=data, **params)
    assert paid.result["status"] == "committed"
    assert _source(state)["water_cast_clock"] == "combat"
    assert _source(state)["water_cast_turn"] == state[f"{SRD}.combat"]["turn_sequence"]
    _reject(state, "change_srd_control_water", "later turn", caster_state="hero", area_id="hero")


def test_water_actual_selected_dispel_ends_only_exact_source_and_keeps_refill():
    state, _, _ = _cast()
    _run(
        "dispel_srd_environment_source",
        state,
        caster_state="hero",
        area_id="hero",
        slot_level=4,
        casting_modifier=4,
        casting_ability="intelligence",
    )
    assert not _source(state)["active"]
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    result = _run("resolve_srd_water_point", state, point=[30, 0, 0], plane="material")
    assert result.result["context"]["_water_uncertain"]


def test_water_refill_does_not_apply_to_body_relocated_to_another_plane():
    state, _, _ = _cast()
    _run("end_concentration", state, caster_state="hero")
    state[f"{SRD}.state"]["spatial_scene"]["geometry"][-1]["metadata"]["plane"] = "ethereal"
    _reject(state, "resolve_srd_water_point", "plane or map", point=[30, 0, 0], plane="ethereal")


def test_water_current_source_cannot_rebind_the_same_geometry_id_in_another_map():
    state, _, _ = _cast()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["map"]["id"] = "different-map"
    for body in scene["entities"] + scene["geometry"]:
        body["map_id"] = "different-map"
    _reject(
        state,
        "qualify_srd_control_water_source",
        "Changed water body",
        caster_state="hero",
        area_id="hero",
    )


@pytest.mark.parametrize("refill", [False, True])
def test_water_unsupported_concurrent_source_or_refill_rejects_before_payment(refill):
    state, data, params = _cast()
    if refill:
        _run("end_concentration", state, caster_state="hero")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Concurrent Part Water"):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before


@pytest.mark.parametrize("plane", ["material", "ethereal"])
def test_water_actual_interior_wall_rejects_before_cost_but_foreign_wall_does_not(plane):
    state, data, params = _case()
    _run("record_srd_water_body", state, geometry_id="pool", flow="standing")
    scene = state[f"{SRD}.state"]["spatial_scene"]
    wall = deepcopy(scene["geometry"][0])
    wall.update(
        id="internal-wall", volume={"kind": "box", "min": [60, -30, 0], "max": [61, 30, 20]}
    )
    wall["metadata"]["plane"] = plane
    scene["geometry"].append(wall)
    before = deepcopy(state)
    if plane == "material":
        with pytest.raises(RulesetPreconditionRejected, match="real solid interior"):
            _run("cast_spell_outside_combat", state, participant_data=data, **params)
        assert state == before
    else:
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
        assert _source(state)["active"]


def test_public_water_paid_part_later_magic_walk_reopen_retry_and_refill(tmp_path, monkeypatch):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as casting
    from tests.srd_full.test_srd_spell_environment import _woodland_test_archive

    monkeypatch.setattr(casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(casting, "_spell_case", lambda _name: _case())
    database = tmp_path / "water.db"
    receipts = []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = casting._create_spell_campaign(owner, "control_water")
        template = ActionCommand(
            campaign_id="casting",
            action_id="water-fact",
            idempotency_key="water-fact",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="record_srd_water_body",
            parameters={"geometry_id": "pool", "flow": "standing"},
        )
        admitted = StatelessMcpMechanics(owner).execute_action(template)
        assert admitted.status is ActionStatus.COMMITTED, admitted.result
        receipts.append((template, admitted))
        known = owner.snapshot("casting")
        assert known.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 3
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == known.state
        assert owner.execute_action(template).result == admitted.result
        mechanics = StatelessMcpMechanics(owner)

        def act(label, tool, parameters):
            command = replace(
                template,
                action_id=label,
                idempotency_key=label,
                expected_revision=owner.snapshot("casting").revision,
                tool_name=tool,
                parameters=parameters,
            )
            result = mechanics.execute_action(command)
            assert result.status is ActionStatus.COMMITTED, result.result
            receipts.append((command, result))
            return result

        act("water-paid", "cast_spell_outside_combat", params)
        paid = owner.snapshot("casting")
        assert paid.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
        assert paid.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["water_trench"] == {
            "kind": "box",
            "min": [25, -5, 0],
            "max": [75, 5, 20],
        }
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == paid.state
        assert owner.execute_action(receipts[-1][0]).result == receipts[-1][1].result
        mechanics = StatelessMcpMechanics(owner)
        act(
            "water-combat",
            "start_group_combat_encounter",
            {"combatants": ["hero", "guard", "other"]},
        )
        act("water-caster-turn", "begin_combat_turn", {})
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "hero"
        act(
            "water-repeat",
            "change_srd_control_water",
            {
                "caster_state": "hero",
                "area_id": "hero",
                "trench_width": 20,
                "trench_axis": "east",
            },
        )
        repeated = owner.snapshot("casting")
        assert not repeated.state[f"{SRD}.combat"]["action_available"]
        assert repeated.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
        assert repeated.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["water_trench"] == {
            "kind": "box",
            "min": [25, -10, 0],
            "max": [75, 10, 20],
        }
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == repeated.state
        assert owner.execute_action(receipts[-1][0]).result == receipts[-1][1].result
        mechanics = StatelessMcpMechanics(owner)
        for index in range(3):
            act(f"water-end-turn-{index}", "end_combat_turn", {})
            act(f"water-advance-turn-{index}", "advance_group_combat_encounter_turn", {})
            act(f"water-next-turn-{index}", "begin_combat_turn", {})
            if owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "guard":
                break
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "guard"
        act(
            "water-walk",
            "move_srd_scene_creature",
            {
                "actor_state": "guard",
                "destination": [40, 0, 0],
            },
        )
        walked = owner.snapshot("casting")
        assert _position(dict(walked.state), "guard")["position"] == [40, 0, 0]
        assert walked.state[f"{SRD}.combat"]["movement_remaining"] == 20
        act("water-end-source", "end_concentration", {"caster_state": "hero"})
        ended = owner.snapshot("casting")
        returns = [
            r
            for r in ended.state[f"{SRD}.state"]["turn_effects"]
            if r.get("kind") == "environment_water_refill"
        ]
        assert len(returns) == 1
        assert returns[0]["ready_tick"] == returns[0]["started_tick"] + 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == ended.state
        mechanics = StatelessMcpMechanics(owner)
        act("water-end-encounter", "end_combat_encounter", {"end_reason": "fighting_stopped"})
        act("water-refilled", "advance_srd_lasting_time", {"elapsed_rounds": 1})
        refilled = owner.snapshot("casting")
        assert not refilled.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
        assert refilled.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == refilled.state
        for command, result in receipts:
            assert owner.execute_action(command).result == result.result
        assert owner.snapshot("casting").state == refilled.state
