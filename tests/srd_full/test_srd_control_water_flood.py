"""Bounded Flood and genuine later Part changes retain the actual Water source and clock."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_control_water import _case, _reject, _source
from tests.srd_full.test_srd_spell_environment import SRD, _position
from tests.srd_full.test_srd_spell_wave3_orders import _turn


def _flood_case(effect="flood", category="bounded", rise=20):
    state, data, params = _case()
    _run(
        "record_srd_water_body", state, geometry_id="pool", flow="standing", body_category=category
    )
    params.update(environment_water_effect=effect, environment_flood_rise=rise)
    return state, data, params


def _flood_cast():
    state, data, params = _flood_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    return state, data, params


def test_redirect_paid_source_query_later_choice_and_end_restore_real_terrain_flow():
    state, data, params = _case()
    _run(
        "record_srd_water_body",
        state,
        geometry_id="pool",
        flow="flowing",
        terrain_flow_direction=[2, 0, 0],
    )
    params.update(
        environment_water_effect="redirect_flow", environment_redirect_direction=[0, 0, 4]
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    inside = _run("resolve_srd_water_flow", state, point=[30, 0, 10], plane="material")
    assert inside.result["context"]["_water_flow_direction"] == [0, 0, 1]
    assert inside.result["context"]["_water_flow_source_id"] == "hero"
    outside = _run("resolve_srd_water_flow", state, point=[21, 0, 10], plane="material")
    assert outside.result["context"]["_water_flow_direction"] == [1, 0, 0]
    assert outside.result["context"]["_water_flow_source_id"] is None
    original = deepcopy(_source(state))
    _turn(state, "hero", data)
    _run(
        "change_srd_control_water",
        state,
        caster_state="hero",
        area_id="hero",
        water_effect="redirect_flow",
        redirect_direction=[-5, 0, 0],
    )
    later = _run("resolve_srd_water_flow", state, point=[30, 0, 10], plane="material")
    assert later.result["context"]["_water_flow_direction"] == [-1, 0, 0]
    assert _source(state)["sequence"] == original["sequence"]
    assert _source(state)["expires_tick"] == original["expires_tick"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
    assert not state[f"{SRD}.combat"]["action_available"]
    _run("end_concentration", state, caster_state="hero")
    ended = _run("resolve_srd_water_flow", state, point=[30, 0, 10], plane="material")
    assert ended.result["context"]["_water_flow_direction"] == [1, 0, 0]
    assert ended.result["context"]["_water_flow_source_id"] is None


def test_large_body_paid_wave_transports_vehicle_rolls_capsize_and_repeats_at_caster_start(
    monkeypatch,
):
    state, data, params = _flood_case(category="large_body")
    for name, point, size in (("barge", [35, 0, 20], "huge"), ("ship", [60, 15, 20], "gargantuan")):
        body = deepcopy(_position(state, "guard"))
        body.update(
            id=name, position=point, height=2, footprint={"kind": "box", "half_extents": [2, 2]}
        )
        body["metadata"] = {"plane": "material"}
        state[f"{SRD}.state"]["spatial_scene"]["entities"].append(body)
        _run("record_srd_water_vehicle", state, object_id=name, size=size)
    ship_before = deepcopy(_position(state, "ship"))
    monkeypatch.setattr(dice, "_rng", Random(19))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert _position(state, "barge")["position"] == [73, 0, 20]
    first = _position(state, "barge")["metadata"]["srd_water_vehicle"]
    assert first["capsize_roll"] == 87 and not first["capsized"]
    assert _position(state, "ship") == ship_before
    source = deepcopy(_source(state))
    _turn(state, "hero", data)
    second = _position(state, "barge")["metadata"]["srd_water_vehicle"]
    assert second["capsize_roll"] == 6 and second["capsized"]
    assert second["last_wave_sequence"] == 2
    assert _source(state)["sequence"] == source["sequence"]
    assert _source(state)["expires_tick"] == source["expires_tick"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
    assert state[f"{SRD}.combat"]["action_available"]
    assert state[f"{SRD}.state"]["control_water_pending_motion"] is None
    _run("end_concentration", state, caster_state="hero")
    _turn(state, "hero", data)
    assert _position(state, "barge")["metadata"]["srd_water_vehicle"] == second


def test_flood_paid_source_derives_current_level_without_mutating_real_body():
    state, _, _ = _flood_cast()
    source = _source(state)
    assert source["water_effect"] == "flood" and source["water_flood_rise"] == 20
    assert source["water_body_category"] == "bounded"
    assert source["water_flood_volume"] == {
        "kind": "box",
        "min": [25, -25, 0],
        "max": [75, 25, 40],
    }
    assert state[f"{SRD}.state"]["spatial_scene"]["geometry"][-1]["volume"]["max"][2] == 20
    point = _run("resolve_srd_water_point", state, point=[30, 0, 25], plane="material")
    context = point.result["context"]
    assert context["_water_present"] and context["_water_surface"] == 40
    assert context["_water_depth"] == 40 and context["_water_geometry_id"] == "pool"
    outside = _run("resolve_srd_water_point", state, point=[21, 0, 25], plane="material")
    assert not outside.result["context"]["_water_present"]
    assert source["expires_tick"] == 200
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2


def test_flood_genuine_submerged_native_swimming_uses_raised_source_volume():
    state, _, _ = _flood_cast()
    _position(state, "guard")["position"] = [30, 0, 25]
    _turn(state, "guard")
    _run(
        "move_srd_scene_creature", state, actor_state="guard", destination=[40, 0, 25], mode="swim"
    )
    assert _position(state, "guard")["position"] == [40, 0, 25]
    assert state[f"{SRD}.combat"]["movement_remaining"] == 20


def test_flood_unknown_body_category_rejects_before_any_paid_effect():
    state, data, params = _flood_case(category="unknown")
    _reject(
        state, "cast_spell_outside_combat", "body fact", participant_data=data, **params
    )


def test_flood_cube_cannot_clip_the_actual_chosen_rise():
    state, data, params = _flood_case()
    params["environment_water_side"] = 30
    _reject(
        state, "cast_spell_outside_combat", "raised water must fit", participant_data=data, **params
    )


@pytest.mark.parametrize("end", ["concentration", "expiry", "dispel"])
def test_flood_exact_end_restores_recorded_level_without_part_refill(end):
    state, _, _ = _flood_cast()
    if end == "concentration":
        _run("end_concentration", state, caster_state="hero")
    elif end == "expiry":
        _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    else:
        _run(
            "dispel_srd_environment_source",
            state,
            caster_state="other",
            area_id="hero",
            slot_level=4,
            casting_modifier=0,
            casting_ability="wisdom",
        )
    high = _run("resolve_srd_water_point", state, point=[30, 0, 25], plane="material")
    assert not high.result["context"]["_water_present"]
    low = _run("resolve_srd_water_point", state, point=[30, 0, 10], plane="material")
    assert low.result["context"]["_water_present"]
    assert low.result["context"]["_water_surface"] == 20
    assert not any(
        r.get("kind") == "environment_water_refill"
        for r in state[f"{SRD}.state"].get("turn_effects", [])
    )


def test_part_to_flood_real_later_action_retains_one_round_refill():
    state, data, params = _flood_case(effect="part_water")
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    old_source = deepcopy(_source(state))
    _turn(state, "hero")
    _run(
        "change_srd_control_water",
        state,
        caster_state="hero",
        area_id="hero",
        water_effect="flood",
        flood_rise=10,
    )
    source = _source(state)
    assert source["sequence"] == old_source["sequence"]
    assert source["expires_tick"] == old_source["expires_tick"]
    assert source["water_effect"] == "flood" and source["water_flood_volume"]["max"][2] == 30
    assert not state[f"{SRD}.combat"]["action_available"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
    refills = [
        r
        for r in state[f"{SRD}.state"]["turn_effects"]
        if r.get("kind") == "environment_water_refill"
    ]
    assert len(refills) == 1 and refills[0]["trench"] == old_source["water_trench"]
    assert refills[0]["ready_tick"] == refills[0]["started_tick"] + 2
    point = _run("resolve_srd_water_point", state, point=[30, 0, 10], plane="material")
    assert point.result["context"]["_water_uncertain"]
    _run("end_combat_encounter", state, end_reason="fighting_stopped")
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    settled = _run("resolve_srd_water_point", state, point=[30, 0, 10], plane="material")
    assert settled.result["context"]["_water_present"]
    assert not settled.result["context"]["_water_uncertain"]


def test_flood_to_part_real_action_removes_elevated_level_and_installs_trench():
    state, _, _ = _flood_cast()
    _turn(state, "hero")
    _run(
        "change_srd_control_water",
        state,
        caster_state="hero",
        area_id="hero",
        water_effect="part_water",
        trench_width=20,
    )
    high = _run("resolve_srd_water_point", state, point=[30, 0, 25], plane="material")
    assert not high.result["context"]["_water_present"]
    low = _run("resolve_srd_water_point", state, point=[30, 0, 10], plane="material")
    assert low.result["context"]["_water_parted"] and not low.result["context"]["_water_present"]
    assert not _source(state)["water_refill_retained"]


def test_flood_changed_body_category_rejects_current_effect_instead_of_rebinding():
    state, _, _ = _flood_cast()
    water = state[f"{SRD}.state"]["spatial_scene"]["geometry"][-1]
    water["metadata"]["srd_water"]["body_category"] = "large_body"
    _reject(
        state, "resolve_srd_water_point", "body classification", point=[30, 0, 25], plane="material"
    )


def test_flood_raised_body_contact_cannot_be_skipped_between_outside_endpoints():
    state, _, _ = _flood_cast()
    _position(state, "guard")["position"] = [22, 0, 25]
    _turn(state, "guard")
    _reject(
        state,
        "move_srd_scene_creature",
        "raised-water routes",
        actor_state="guard",
        destination=[78, 0, 25],
        mode="swim",
    )


def test_public_flood_paid_source_later_swim_reopen_retry_and_normal_level(tmp_path, monkeypatch):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as casting
    from tests.srd_full.test_srd_spell_environment import _woodland_test_archive

    def actual_scene(_name):
        state, data, params = _case()
        _position(state, "guard")["position"] = [30, 0, 25]
        params.update(environment_water_effect="flood", environment_flood_rise=20)
        return state, data, params

    monkeypatch.setattr(casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(casting, "_spell_case", actual_scene)
    database = tmp_path / "flood.db"
    receipts = []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = casting._create_spell_campaign(owner, "control_water")
        template = ActionCommand(
            campaign_id="casting",
            action_id="flood-fact",
            idempotency_key="flood-fact",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="record_srd_water_body",
            parameters={"geometry_id": "pool", "flow": "standing", "body_category": "bounded"},
        )

        def act(label, tool, parameters):
            command = replace(
                template,
                action_id=label,
                idempotency_key=label,
                expected_revision=owner.snapshot("casting").revision,
                tool_name=tool,
                parameters=parameters,
            )
            result = StatelessMcpMechanics(owner).execute_action(command)
            assert result.status is ActionStatus.COMMITTED, result.result
            receipts.append((command, result))
            return result

        act("flood-fact", "record_srd_water_body", template.parameters)
        known = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == known.state
        act("flood-paid", "cast_spell_outside_combat", params)
        paid = owner.snapshot("casting")
        source = paid.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
        assert source["water_flood_volume"]["max"][2] == 40 and source["water_effect"] == "flood"
        assert paid.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == paid.state
        assert owner.execute_action(receipts[-1][0]).result == receipts[-1][1].result
        act(
            "flood-combat",
            "start_group_combat_encounter",
            {"combatants": ["hero", "guard", "other"]},
        )
        act("flood-first-turn", "begin_combat_turn", {})
        for index in range(3):
            if owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "guard":
                break
            act(f"flood-end-{index}", "end_combat_turn", {})
            act(f"flood-advance-{index}", "advance_group_combat_encounter_turn", {})
            act(f"flood-begin-{index}", "begin_combat_turn", {})
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "guard"
        act(
            "flood-swim",
            "move_srd_scene_creature",
            {
                "actor_state": "guard",
                "destination": [40, 0, 25],
                "mode": "swim",
            },
        )
        swam = owner.snapshot("casting")
        assert _position(dict(swam.state), "guard")["position"] == [40, 0, 25]
        assert swam.state[f"{SRD}.combat"]["movement_remaining"] == 20
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == swam.state
        assert owner.execute_action(receipts[-1][0]).result == receipts[-1][1].result
        act("flood-end-source", "end_concentration", {"caster_state": "hero"})
        ended = owner.snapshot("casting")
        assert not ended.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
        actual = ended.state[f"{SRD}.state"]["spatial_scene"]["geometry"][-1]
        assert actual["volume"]["max"][2] == 20
        assert not any(
            row.get("kind") == "environment_water_refill"
            for row in ended.state[f"{SRD}.state"]["turn_effects"]
        )
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == ended.state
        for command, result in receipts:
            assert owner.execute_action(command).result == result.result
        assert owner.snapshot("casting").state == ended.state
