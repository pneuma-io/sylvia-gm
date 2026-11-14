"""Actual paid formation and repeat own exact taper-qualified ten-foot forced pulls."""

from copy import deepcopy

import pytest

from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_control_water import _reject, _source
from tests.srd_full.test_srd_spell_environment import SRD, _position
from tests.srd_full.test_srd_spell_wave3_orders import _turn
from tests.srd_full.test_srd_water_whirlpool import _whirlpool_case


def _cast(*, position=(22, 0, 5), conditions=()):
    state, data, params = _whirlpool_case(position=position)
    state[f"{SRD}.entities"]["guard"]["conditions"] = list(conditions)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    return state, data, params


@pytest.mark.parametrize("conditions", [(), ("stunned",), ("restrained",)])
def test_paid_formation_pulls_exact_ten_without_recipient_action_movement_or_clock(conditions):
    state, _, _ = _cast(conditions=conditions)
    assert _position(state, "guard")["position"] == [32, 0, 5]
    assert _position(state, "guard")["metadata"]["previous_position"] == [22, 0, 5]
    assert state[f"{SRD}.entities"]["guard"]["conditions"] == list(conditions)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
    world = state[f"{SRD}.state"]
    assert world["lasting_tick"] == 0 and world["whirlpool_pending_motion"] is None
    assert _source(state)["whirlpool_pull_settled_sequence"] == 1
    assert not _source(state)["whirlpool_pull_batch_active"]
    assert state[f"{SRD}.entities"]["guard"].get("last_whirlpool_save") is None
    _reject(
        state, "resolve_srd_whirlpool_pull_batch", "already settled", area_id="hero"
    )
    _reject(
        state, "move_srd_whirlpool_subject", "motion receipt",
        subject="guard", destination=[42, 0, 5], plane="material",
    )


def test_later_genuine_magic_action_repeats_same_source_then_real_entry_save():
    state, data, _ = _cast()
    prior = deepcopy(_source(state))
    _turn(state, "hero", data)
    before_budget = state[f"{SRD}.combat"]["movement_remaining"]
    _run(
        "change_srd_control_water", state, participant_data=data,
        caster_state="hero", area_id="hero", water_effect="whirlpool",
    )
    assert _position(state, "guard")["position"] == [42, 0, 5]
    source = _source(state)
    assert source["sequence"] == prior["sequence"]
    assert source["expires_tick"] == prior["expires_tick"]
    assert source["whirlpool_effect_sequence"] == source["whirlpool_pull_settled_sequence"] == 2
    assert source["whirlpool_pull_batch"] == 2
    guard = state[f"{SRD}.entities"]["guard"]
    packet = guard["last_whirlpool_save"]
    assert packet["timing"] == "entry" and len(packet["raw_damage"]["results"]) == 2
    assert guard["hp"] == 1000 - packet["applied_damage"]
    assert not state[f"{SRD}.combat"]["action_available"]
    assert state[f"{SRD}.combat"]["movement_remaining"] == before_budget
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
    _reject(state, "resolve_srd_whirlpool_pull_batch", "already settled", area_id="hero")


@pytest.mark.parametrize("position", [(45, 0, 5), (50, 0, 5), (22, 0, 26)])
def test_closer_coincident_vertical_eligible_pull_rejects_atomically(position):
    state, data, params = _whirlpool_case(position=position)
    _reject(
        state, "cast_spell_outside_combat", "Closer, coincident or vertical",
        participant_data=data, **params,
    )


def test_actual_receiving_solid_rejects_paid_formation_without_any_leaked_mutation():
    state, data, params = _whirlpool_case(position=(22, 0, 5))
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append({
        "id": "outside-cube-post", "map_id": "orders", "active": True,
        "volume": {"kind": "box", "min": [24, -2, 1], "max": [24.5, 2, 15]},
        "movement": "blocking", "visibility": "clear", "occupies_cells": True,
        "metadata": {"plane": "material"},
        "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
    })
    _reject(
        state, "cast_spell_outside_combat", "actual body collision",
        participant_data=data, **params,
    )


@pytest.mark.parametrize(
    "position,expected_near,expected_squared",
    [
        ((14.6, 0, 0), True, (35 - 7.9) ** 2 / 1.81),
        ((-0.4, 0, 0), False, (50 - 25) ** 2 + (25 - 6) ** 2),
        ((50, 0, 30), True, 25),
    ],
)
def test_true_box_taper_proximity_uses_actual_side_projection_and_cap(
    position, expected_near, expected_squared
):
    state, data, params = _whirlpool_case(position=(22, 0, 5))
    # A larger admitted pool permits this independent geometry qualifier fixture.
    pool = state[f"{SRD}.state"]["spatial_scene"]["geometry"][-1]
    pool["volume"]["min"][0] = -10
    _run("record_srd_water_body", state, geometry_id="pool", flow="standing")
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    _position(state, "guard")["position"] = list(position)
    result = _run(
        "qualify_srd_whirlpool_pull_target", state, target_state="guard", area_id="hero"
    )
    context = result.result["context"]
    assert context["_whirl_pull_near"] is expected_near
    assert context["_whirl_pull_distance_squared"] == pytest.approx(expected_squared)


def test_actual_source_end_foreign_plane_and_receiver_has_no_bypass_parameters():
    from tests.srd_full.test_srd_action_budget import ROOT, _runtime_definition

    runtime = _runtime_definition((ROOT / "runtime/ruleset.yaml").read_bytes())
    receiver = runtime["tools"]["move_srd_whirlpool_subject"]
    assert not {"teleport", "levitate_source_id", "telekinesis_source_id", "sphere_source_id"} & {
        p["name"] for p in receiver["parameters"]
    }
    state, _, _ = _cast()
    _position(state, "guard")["metadata"]["plane"] = "ethereal"
    result = _run(
        "qualify_srd_whirlpool_pull_target", state, target_state="guard", area_id="hero"
    )
    assert not result.result["context"]["_whirl_pull_near"]
    _run("end_concentration", state, caster_state="hero")
    result = _run(
        "qualify_srd_whirlpool_pull_target", state, target_state="guard", area_id="hero"
    )
    assert not result.result["context"]["_whirl_pull_near"]
    _reject(state, "resolve_srd_whirlpool_pull_batch", "current paid Whirlpool", area_id="hero")


def test_public_paid_pull_repeat_entry_end_escape_reopen_and_exact_retries(tmp_path, monkeypatch):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as casting
    from tests.srd_full.test_srd_spell_environment import _woodland_test_archive

    monkeypatch.setattr(casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(casting, "_spell_case", lambda _name: _whirlpool_case(position=(22, 0, 5)))
    database = tmp_path / "whirlpool-pull.db"
    receipts = []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = casting._create_spell_campaign(
            owner, "control_water", target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0}
        )
        template = ActionCommand(
            campaign_id="casting", action_id="pull-paid", idempotency_key="pull-paid",
            owner_id="casting-owner", writer=WriterRole.DM, expected_revision=0,
            turn_id="casting-turn", package_id=SRD, tool_name="cast_spell_outside_combat",
            parameters=params,
        )

        def act(label, tool, parameters):
            command = replace(
                template, action_id=label, idempotency_key=label,
                expected_revision=owner.snapshot("casting").revision,
                tool_name=tool, parameters=parameters,
            )
            result = StatelessMcpMechanics(owner).execute_action(command)
            assert result.status is ActionStatus.COMMITTED, result.result
            receipts.append((command, result))
            return result

        act("pull-paid", "cast_spell_outside_combat", params)
        paid = owner.snapshot("casting")
        assert _position(dict(paid.state), "guard")["position"] == [32, 0, 5]
        source = paid.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
        assert source["whirlpool_pull_settled_sequence"] == 1
        assert paid.state[f"{SRD}.state"]["whirlpool_pending_motion"] is None
        assert paid.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == paid.state
        assert owner.execute_action(receipts[-1][0]).result == receipts[-1][1].result
        act(
            "pull-combat", "start_group_combat_encounter",
            {"combatants": ["hero", "guard", "other"]},
        )
        act("pull-begin-first", "begin_combat_turn", {})

        def reach_actor(actor, label):
            for index in range(3):
                if owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == actor:
                    return
                act(f"{label}-end-{index}", "end_combat_turn", {})
                act(f"{label}-advance-{index}", "advance_group_combat_encounter_turn", {})
                act(f"{label}-begin-{index}", "begin_combat_turn", {})
            raise AssertionError("Native initiative did not reach the actual actor.")

        reach_actor("hero", "pull-caster")
        act(
            "pull-repeat", "change_srd_control_water",
            {"caster_state": "hero", "area_id": "hero", "water_effect": "whirlpool"},
        )
        repeated = owner.snapshot("casting")
        current = repeated.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
        assert current["sequence"] == source["sequence"]
        assert current["expires_tick"] == source["expires_tick"]
        assert current["whirlpool_pull_settled_sequence"] == 2
        assert _position(dict(repeated.state), "guard")["position"] == [42, 0, 5]
        entry = repeated.state[f"{SRD}.entities"]["guard"]["last_whirlpool_save"]
        assert entry["timing"] == "entry" and len(entry["raw_damage"]["results"]) == 2
        assert not repeated.state[f"{SRD}.combat"]["action_available"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == repeated.state
        assert owner.execute_action(receipts[-1][0]).result == receipts[-1][1].result
        reach_actor("guard", "pull-recipient")
        act("pull-end-inside", "end_combat_turn", {})
        ended_turn = owner.snapshot("casting")
        ending = ended_turn.state[f"{SRD}.entities"]["guard"]["last_whirlpool_save"]
        assert ending["timing"] == "end"
        assert ended_turn.state[f"{SRD}.entities"]["guard"]["hp"] == (
            1000 - entry["applied_damage"] - ending["applied_damage"]
        )
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == ended_turn.state
        assert owner.execute_action(receipts[-1][0]).result == receipts[-1][1].result
        act("pull-next-advance", "advance_group_combat_encounter_turn", {})
        act("pull-next-begin", "begin_combat_turn", {})
        reach_actor("guard", "pull-next-recipient")
        act("pull-escape", "escape_srd_whirlpool", {"actor_state": "guard", "area_id": "hero"})
        escaped = owner.snapshot("casting")
        assert escaped.state[f"{SRD}.entities"]["guard"]["whirlpool_escape"]["succeeded"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == escaped.state
        assert owner.execute_action(receipts[-1][0]).result == receipts[-1][1].result
        act(
            "pull-swim-away", "move_srd_scene_creature",
            {"actor_state": "guard", "destination": [32, 0, 5], "mode": "swim"},
        )
        assert _position(dict(owner.snapshot("casting").state), "guard")["position"] == [32, 0, 5]
        act("pull-source-end", "end_concentration", {"caster_state": "hero"})
        ended = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == ended.state
        for command, result in receipts:
            assert owner.execute_action(command).result == result.result
        assert owner.snapshot("casting").state == ended.state
