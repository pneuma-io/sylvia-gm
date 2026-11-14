"""Printed Whirlpool taper and later native saves/escape use the real paid Water source."""

from copy import deepcopy

import pytest

from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_control_water import _case, _reject, _source
from tests.srd_full.test_srd_spell_environment import SRD, _position
from tests.srd_full.test_srd_spell_wave3_orders import _turn


def _whirlpool_case(*, success=False, position=(30, 0, 5), diameter=50):
    state, data, params = _case()
    water = state[f"{SRD}.state"]["spatial_scene"]["geometry"][-1]
    water["volume"]["max"][2] = 40
    _run("record_srd_water_body", state, geometry_id="pool", flow="standing")
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(hp=1000, max_hp=1000, temp_hp=0)
    guard["saving_throw_modifiers"]["strength"] = 100 if success else -100
    guard["skill_check_modifiers"] = {"athletics": 100}
    _position(state, "guard")["position"] = list(position)
    params.update(environment_water_effect="whirlpool", environment_whirlpool_top_diameter=diameter)
    return state, data, params


def _whirlpool_cast(**choices):
    state, data, params = _whirlpool_case(**choices)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    return state, data, params


def test_paid_whirlpool_keeps_printed_taper_actual_body_source_and_no_appearance_save():
    state, _, _ = _whirlpool_cast(position=(50, 0, 5))
    source = _source(state)
    assert source["water_effect"] == "whirlpool"
    assert source["water_whirlpool"] == {
        "kind": "tapered_whirlpool",
        "origin": [50, 0, 0],
        "base_radius": 2.5,
        "top_radius": 25,
        "height": 25,
    }
    assert source["expires_tick"] == 200
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
    assert state[f"{SRD}.state"]["spatial_scene"]["geometry"][-1]["volume"]["max"][2] == 40
    assert state[f"{SRD}.entities"]["guard"].get("last_whirlpool_save") is None


@pytest.mark.parametrize(
    "position,expected",
    [
        ((50, 0, 0), True),
        ((30, 0, 0), False),
        ((30, 0, 20), True),
        ((75.4, 0, 20), False),
        ((50, 0, 25), False),
        ((50, 0, 40), False),
    ],
)
def test_true_taper_membership_changes_with_actual_body_height_and_strict_caps(position, expected):
    state, _, _ = _whirlpool_cast()
    _position(state, "guard")["position"] = list(position)
    result = _run("qualify_srd_whirlpool_target", state, target_state="guard", area_id="hero")
    assert result.result["context"]["_whirl_target_inside"] is expected


def test_taper_horizontal_route_strict_tangency_and_real_intermediate_contact():
    state, _, _ = _whirlpool_cast(position=(30, 0, 5))
    actual = _position(state, "guard")
    radius = 2.5 + 22.5 * (5 + actual["height"]) / 25
    actual["position"] = [30, radius + actual["footprint"]["half_extents"][1], 5]
    tangent = _run(
        "resolve_srd_whirlpool_body_segment",
        state,
        moving_subject="guard",
        area_id="hero",
        start=actual["position"],
        end=[70, actual["position"][1], 5],
    )
    assert not tangent.result["context"]["_body_hit"]
    actual["position"] = [30, 0, 5]
    cross = _run(
        "resolve_srd_whirlpool_body_segment",
        state,
        moving_subject="guard",
        area_id="hero",
        start=actual["position"],
        end=[70, 0, 5],
    )
    assert cross.result["context"]["_body_hit"]
    assert 0 < cross.result["context"]["_body_entry"] < cross.result["context"]["_body_exit"] < 1


@pytest.mark.parametrize("choice", ["square", "depth"])
def test_whirlpool_real_minimum_area_depth_reject_before_payment(choice):
    state, data, params = _whirlpool_case()
    if choice == "square":
        params["environment_water_side"] = 40
    else:
        state[f"{SRD}.state"]["spatial_scene"]["geometry"][-1]["volume"]["max"][2] = 24
    _reject(
        state, "cast_spell_outside_combat", "fifty feet square", participant_data=data, **params
    )


@pytest.mark.parametrize("success", [False, True])
def test_actual_first_entry_and_independent_end_turn_strength_half_packets(success):
    state, _, _ = _whirlpool_cast(success=success)
    _turn(state, "guard")
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[45, 0, 5], mode="swim")
    guard = state[f"{SRD}.entities"]["guard"]
    entered = deepcopy(guard["last_whirlpool_save"])
    assert entered["timing"] == "entry"
    assert entered["outcome"] == ("success" if success else "failure")
    assert len(entered["raw_damage"]["results"]) == 2
    expected = entered["raw_damage"]["total"] // (2 if success else 1)
    assert entered["applied_damage"] == expected and guard["hp"] == 1000 - expected
    _run("resolve_srd_environment_hazard_event", state, target_state="guard", area_id="hero")
    assert state[f"{SRD}.entities"]["guard"]["last_whirlpool_save"] == entered
    _run("end_combat_turn", state)
    guard = state[f"{SRD}.entities"]["guard"]
    ending = guard["last_whirlpool_save"]
    assert ending["timing"] == "end" and ending["turn"] == entered["turn"]
    assert guard["hp"] == 1000 - entered["applied_damage"] - ending["applied_damage"]


@pytest.mark.parametrize("success", [False, True])
def test_real_athletics_action_gates_later_swimming_away_without_a_second_cost(success):
    state, _, _ = _whirlpool_cast(position=(50, 0, 5))
    _turn(state, "guard")
    guard = state[f"{SRD}.entities"]["guard"]
    guard["skill_check_modifiers"]["athletics"] = 100 if success else -100
    _reject(
        state,
        "move_srd_scene_creature",
        "Athletics Action",
        actor_state="guard",
        destination=[40, 0, 5],
        mode="swim",
    )
    _run("escape_srd_whirlpool", state, actor_state="guard", area_id="hero")
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["whirlpool_escape"]["succeeded"] is success
    assert state[f"{SRD}.combat"]["action_available"] is False
    if success:
        _run(
            "move_srd_scene_creature",
            state,
            actor_state="guard",
            destination=[40, 0, 5],
            mode="swim",
        )
        assert _position(state, "guard")["position"] == [40, 0, 5]
        assert state[f"{SRD}.combat"]["movement_remaining"] == 20
    else:
        _reject(
            state,
            "move_srd_scene_creature",
            "Athletics Action",
            actor_state="guard",
            destination=[40, 0, 5],
            mode="swim",
        )


def test_whirlpool_source_end_foreign_plane_and_curved_or_vertical_routes_are_honest():
    state, _, _ = _whirlpool_cast(position=(50, 0, 5))
    actor = _position(state, "guard")
    actor["metadata"]["plane"] = "ethereal"
    result = _run("qualify_srd_whirlpool_target", state, target_state="guard", area_id="hero")
    assert not result.result["context"]["_whirl_target_inside"]
    actor = _position(state, "guard")
    actor["metadata"]["plane"] = "material"
    _reject(
        state,
        "resolve_srd_whirlpool_body_segment",
        "Vertical Whirlpool",
        moving_subject="guard",
        area_id="hero",
        start=actor["position"],
        end=[50, 0, 6],
    )
    _run("end_concentration", state, caster_state="hero")
    result = _run("qualify_srd_whirlpool_target", state, target_state="guard", area_id="hero")
    assert not result.result["context"]["_whirl_target_inside"]
    result = _run(
        "qualify_srd_whirlpool_route", state, actor_state="guard", area_id="hero",
        start=_position(state, "guard")["position"], end=[40, 0, 5],
    )
    assert not result.result["context"]["_whirl_current"]


def test_whirlpool_later_magic_action_repeats_same_paid_source_and_retires_old_escape():
    state, data, _ = _whirlpool_cast(position=(50, 0, 5))
    _turn(state, "guard")
    _run("escape_srd_whirlpool", state, actor_state="guard", area_id="hero")
    prior = deepcopy(_source(state))
    slots = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    _turn(state, "hero", data)
    _run(
        "change_srd_control_water",
        state,
        participant_data=data,
        caster_state="hero",
        area_id="hero",
        water_effect="whirlpool",
        whirlpool_top_diameter=30,
    )
    current = _source(state)
    assert current["sequence"] == prior["sequence"]
    assert current["expires_tick"] == prior["expires_tick"]
    assert current["whirlpool_effect_sequence"] == prior["whirlpool_effect_sequence"] + 1
    assert current["water_whirlpool"]["top_radius"] == 15
    assert state[f"{SRD}.entities"]["hero"]["resources"] == slots
    assert not state[f"{SRD}.combat"]["action_available"]
    assert state[f"{SRD}.entities"]["guard"].get("last_whirlpool_save") is None
    _turn(state, "guard")
    _reject(
        state,
        "move_srd_scene_creature",
        "Athletics Action",
        actor_state="guard",
        destination=[45, 0, 5],
        mode="swim",
    )


def test_whirlpool_actual_source_clock_expires_without_later_damage():
    state, _, _ = _whirlpool_cast(position=(50, 0, 5))
    _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    result = _run("qualify_srd_whirlpool_target", state, target_state="guard", area_id="hero")
    assert not result.result["context"]["_whirl_target_inside"]
    assert not _source(state)["active"]
    assert state[f"{SRD}.entities"]["guard"].get("last_whirlpool_save") is None


def test_whirlpool_real_later_flood_change_keeps_source_cost_lifetime_and_actual_taper():
    state, data, params = _whirlpool_case()
    _run(
        "record_srd_water_body", state, geometry_id="pool", flow="standing", body_category="bounded"
    )
    params.update(environment_water_effect="flood", environment_flood_rise=5)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    prior = deepcopy(_source(state))
    _turn(state, "hero", data)
    _run(
        "change_srd_control_water", state, participant_data=data,
        caster_state="hero", area_id="hero", water_effect="whirlpool",
    )
    current = _source(state)
    assert current["water_effect"] == "whirlpool"
    assert current["sequence"] == prior["sequence"]
    assert current["expires_tick"] == prior["expires_tick"]
    assert current["water_whirlpool"]["height"] == 25
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
    assert not state[f"{SRD}.combat"]["action_available"]


def test_whirlpool_does_not_invent_water_before_the_genuine_part_refill():
    state, data, params = _whirlpool_case()
    params.update(environment_water_effect="part_water")
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    _turn(state, "hero", data)
    _reject(
        state, "change_srd_control_water", "next-round refill",
        caster_state="hero", area_id="hero", water_effect="whirlpool",
    )


def test_public_whirlpool_paid_later_entry_end_escape_reopen_and_exact_retries(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as casting
    from tests.srd_full.test_srd_spell_environment import _woodland_test_archive

    monkeypatch.setattr(casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(casting, "_spell_case", lambda _name: _whirlpool_case())
    database = tmp_path / "whirlpool.db"
    receipts = []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = casting._create_spell_campaign(
            owner,
            "control_water",
            target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0},
        )
        template = ActionCommand(
            campaign_id="casting",
            action_id="whirlpool-paid",
            idempotency_key="whirlpool-paid",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=params,
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

        act("whirlpool-paid", "cast_spell_outside_combat", params)
        paid = owner.snapshot("casting")
        source = paid.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
        assert source["water_whirlpool"]["kind"] == "tapered_whirlpool"
        assert paid.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == paid.state
        assert owner.execute_action(receipts[-1][0]).result == receipts[-1][1].result
        act(
            "whirlpool-combat",
            "start_group_combat_encounter",
            {"combatants": ["hero", "guard", "other"]},
        )
        act("whirlpool-begin-first", "begin_combat_turn", {})

        def reach_guard(label):
            for index in range(3):
                if owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "guard":
                    return
                act(f"{label}-end-{index}", "end_combat_turn", {})
                act(f"{label}-advance-{index}", "advance_group_combat_encounter_turn", {})
                act(f"{label}-begin-{index}", "begin_combat_turn", {})
            raise AssertionError("Native initiative did not reach the real Guard.")

        reach_guard("whirlpool-first")
        act(
            "whirlpool-enter",
            "move_srd_scene_creature",
            {"actor_state": "guard", "destination": [45, 0, 5], "mode": "swim"},
        )
        entered = owner.snapshot("casting")
        packet = entered.state[f"{SRD}.entities"]["guard"]["last_whirlpool_save"]
        assert packet["timing"] == "entry" and len(packet["raw_damage"]["results"]) == 2
        assert _position(dict(entered.state), "guard")["position"] == [45, 0, 5]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == entered.state
        assert owner.execute_action(receipts[-1][0]).result == receipts[-1][1].result
        act("whirlpool-end-inside", "end_combat_turn", {})
        ended_turn = owner.snapshot("casting")
        ending = ended_turn.state[f"{SRD}.entities"]["guard"]["last_whirlpool_save"]
        assert ending["timing"] == "end" and ending["turn"] == packet["turn"]
        assert ended_turn.state[f"{SRD}.entities"]["guard"]["hp"] == (
            1000 - packet["applied_damage"] - ending["applied_damage"]
        )
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == ended_turn.state
        assert owner.execute_action(receipts[-1][0]).result == receipts[-1][1].result
        act("whirlpool-next-advance", "advance_group_combat_encounter_turn", {})
        act("whirlpool-next-begin", "begin_combat_turn", {})
        reach_guard("whirlpool-next")
        act("whirlpool-escape", "escape_srd_whirlpool", {"actor_state": "guard", "area_id": "hero"})
        escaped = owner.snapshot("casting")
        assert escaped.state[f"{SRD}.entities"]["guard"]["whirlpool_escape"]["succeeded"]
        assert not escaped.state[f"{SRD}.combat"]["action_available"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == escaped.state
        assert owner.execute_action(receipts[-1][0]).result == receipts[-1][1].result
        act(
            "whirlpool-swim-away",
            "move_srd_scene_creature",
            {"actor_state": "guard", "destination": [30, 0, 5], "mode": "swim"},
        )
        swam = owner.snapshot("casting")
        assert _position(dict(swam.state), "guard")["position"] == [30, 0, 5]
        assert swam.state[f"{SRD}.combat"]["movement_remaining"] == 15
        act("whirlpool-source-end", "end_concentration", {"caster_state": "hero"})
        ended = owner.snapshot("casting")
        assert not ended.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == ended.state
        for command, result in receipts:
            assert owner.execute_action(command).result == result.result
        assert owner.snapshot("casting").state == ended.state
