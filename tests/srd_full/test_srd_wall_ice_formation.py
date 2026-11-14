"""Actual paid Ice formation receipts bind chosen-side movement and initial damage."""

from copy import deepcopy
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_wall_of_ice import SRD, _case, _panels, _position


def test_wall_ice_empty_formation_retains_paid_panels_without_appearance_roll():
    state, data, params = _case()
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["appearance_completed"] and not source["appearance_in_progress"]
    assert source["formation_targets"] == []
    assert "appearance_damage_roll" not in source
    assert len(_panels(state)) == 10
    assert not any(
        "wall_ice_space_probe" in e["id"]
        for e in state[f"{SRD}.state"]["spatial_scene"]["entities"]
    )
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2


@pytest.mark.parametrize("subject", ["hero", "guard"])
def test_wall_ice_push_requires_the_exact_active_formation_receipt(subject):
    state, data, params = _case()
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "qualify_srd_wall_ice_push",
            state,
            subject=subject,
            destination=[23, -5, 0],
            plane="material",
        )
    assert state == before


def _occupied_case(*, axis="north_south", side="positive", slot=6, success=False):
    state, data, params = _case(axis=axis, slot=slot)
    _position(state, "guard")["position"] = [20, -5 if axis == "north_south" else 0, 0]
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = (
        100 if success else -100
    )
    params["environment_ice_push_side"] = side
    return state, data, params


@pytest.mark.parametrize("axis", ["north_south", "east_west"])
@pytest.mark.parametrize("side", ["positive", "negative"])
def test_wall_ice_actual_occupied_chosen_side_push_and_initial_packet(monkeypatch, axis, side):
    state, data, params = _occupied_case(axis=axis, side=side)
    original = deepcopy(_position(state, "guard"))
    monkeypatch.setattr(dice, "_rng", Random(8))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    coordinate = 0 if axis == "north_south" else 1
    expected = deepcopy(original["position"])
    expected[coordinate] = params["environment_origin"][coordinate] + (
        (0.5 + original["footprint"]["half_extents"][coordinate])
        * (1 if side == "positive" else -1)
    )
    assert _position(state, "guard")["position"] == expected
    guard = state[f"{SRD}.entities"]["guard"]
    result = guard["last_wall_of_ice_appearance"]
    assert result["origin"] == original["position"] and result["destination"] == expected
    assert result["chosen_side"] == side and result["outcome"] == "failure"
    assert result["raw_damage"]["count"] == 10
    assert result["applied_damage"] == result["raw_damage"]["total"]
    assert guard["hp"] == 1000 - result["applied_damage"]
    assert state[f"{SRD}.state"]["wall_ice_pending_push"] is None
    assert len(_panels(state)) == 10
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2


def test_wall_ice_upcast_shared_roll_and_actual_success_half(monkeypatch):
    state, data, params = _occupied_case(slot=8, success=True)
    _position(state, "other")["position"] = [20, 8, 0]
    state[f"{SRD}.entities"]["other"]["saving_throw_modifiers"]["dexterity"] = -100
    monkeypatch.setattr(dice, "_rng", Random(9))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    guard, other = (state[f"{SRD}.entities"][key] for key in ("guard", "other"))
    first, second = (target["last_wall_of_ice_appearance"] for target in (guard, other))
    assert first["raw_damage"] == second["raw_damage"]
    assert first["raw_damage"]["count"] == 14
    assert first["outcome"] == "success" and second["outcome"] == "failure"
    assert first["applied_damage"] == first["raw_damage"]["total"] // 2
    assert second["applied_damage"] == second["raw_damage"]["total"]
    assert _position(state, "guard")["position"][0] == 20.9
    assert _position(state, "other")["position"][0] == 20.9
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("settle_srd_wall_ice_appearance", state, area_id="hero", target_state="guard")
    assert state == before


@pytest.mark.parametrize("defense", ["resistance", "immunity"])
def test_wall_ice_initial_typed_defense_and_temp_hp_have_one_packet(monkeypatch, defense):
    state, data, params = _occupied_case(success=True)
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(temp_hp=3)
    guard["damage_resistances" if defense == "resistance" else "damage_immunities"] = ["cold"]
    monkeypatch.setattr(dice, "_rng", Random(8))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    guard = state[f"{SRD}.entities"]["guard"]
    packet = guard["last_wall_of_ice_appearance"]
    expected = packet["raw_damage"]["total"] // 2 // 2 if defense == "resistance" else 0
    assert packet["applied_damage"] == expected
    assert guard["temp_hp"] == max(0, 3 - expected)
    assert guard["hp"] == 1000 - max(0, expected - 3)


@pytest.mark.parametrize("admitted", ["passable", "tangent", "other_plane"])
def test_wall_ice_formation_uses_actual_living_body_plane_and_interior(monkeypatch, admitted):
    state, data, params = _occupied_case()
    body = _position(state, "guard")
    if admitted == "passable":
        body["movement"] = "passable"
    elif admitted == "tangent":
        body["position"][0] = 20.9
    else:
        body["metadata"]["plane"] = "ethereal"
    monkeypatch.setattr(dice, "_rng", Random(8))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    guard = state[f"{SRD}.entities"]["guard"]
    assert ("last_wall_of_ice_appearance" in guard) == (admitted == "passable")
    if admitted == "passable":
        assert _position(state, "guard")["position"][0] == 20.9


def test_wall_ice_actual_passable_living_destination_rejects_atomic_cast():
    state, data, params = _occupied_case()
    _position(state, "other").update(position=[21.3, -5, 0], movement="passable")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before


def test_public_occupied_wall_ice_push_save_reopen_retry_and_later_solid_removal(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import MechanicsRejected, StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as casting
    from tests.srd_full.test_srd_spell_environment import _woodland_test_archive

    monkeypatch.setattr(casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(casting, "_spell_case", lambda _name: _occupied_case())
    monkeypatch.setattr(dice, "_rng", Random(8))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    database = tmp_path / "occupied-ice.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = casting._create_spell_campaign(
            owner, "wall_of_ice", target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0}
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="ice-formation",
            idempotency_key="ice-formation",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        mechanics = StatelessMcpMechanics(owner)
        result = mechanics.execute_action(command)
        assert result.status is ActionStatus.COMMITTED, result.result
        saved = owner.snapshot("casting")
        guard = saved.state[f"{SRD}.entities"]["guard"]
        packet = guard["last_wall_of_ice_appearance"]
        assert _position(saved.state, "guard")["position"] == [20.9, -5, 0]
        assert packet["raw_damage"]["count"] == 10
        assert guard["hp"] == 1000 - packet["applied_damage"]
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(command).result == result.result
        assert owner.snapshot("casting").state == saved.state
        move = replace(
            command,
            action_id="ice-return",
            idempotency_key="ice-return",
            expected_revision=saved.revision,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "guard", "destination": [15, -5, 0]},
        )
        with pytest.raises(MechanicsRejected, match="intact Ice section"):
            mechanics.execute_action(move)
        assert owner.snapshot("casting").state == saved.state
        end = replace(
            command,
            action_id="ice-end",
            idempotency_key="ice-end",
            expected_revision=saved.revision,
            tool_name="end_concentration",
            parameters={"caster_state": "hero"},
        )
        ended = mechanics.execute_action(end)
        assert ended.status is ActionStatus.COMMITTED
        moved = mechanics.execute_action(
            replace(move, expected_revision=owner.snapshot("casting").revision)
        )
        assert moved.status is ActionStatus.COMMITTED, moved.result
        after = owner.snapshot("casting")
        assert _position(after.state, "guard")["position"] == [15, -5, 0]
        assert after.state[f"{SRD}.entities"]["guard"]["hp"] == guard["hp"]
        assert all(not p["active"] for p in _panels(after.state))
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == after.state
        assert mechanics.execute_action(command).result == result.result
        assert mechanics.execute_action(end).result == ended.result
        assert owner.snapshot("casting").state == after.state
