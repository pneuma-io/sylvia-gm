"""The actual paid cloud must persist and deliver its printed later movement and events."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_environment import SRD, _fixture, _position


def _case(*, inside=True, success=False):
    state, data, params = _fixture("incendiary_cloud", origin=(40, 0, 3))
    for actor, point in (
        ("guard", [40, 0, 0] if inside else [66, 0, 0]),
        ("other", [42, 0, 0] if inside else [90, 0, 0]),
    ):
        _position(state, actor)["position"] = point
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=0)
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["dexterity"] = (
            100 if success else -100
        )
    return state, data, params


@pytest.mark.parametrize("success", [False, True])
def test_incendiary_paid_initial_shared_ten_dice_save_and_heavy_obscuration(success):
    state, data, params = _case(success=success)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["spell"] == "incendiary_cloud" and source["appearance_completed"]
    results = [
        state[f"{SRD}.entities"][actor]["last_environment_sphere_save"]
        for actor in ("guard", "other")
    ]
    assert results[0]["raw_damage"] == results[1]["raw_damage"]
    assert results[0]["raw_damage"]["count"] == 10 and results[0]["raw_damage"]["sides"] == 8
    assert all(r["outcome"] == ("success" if success else "failure") for r in results)
    assert results[0]["applied_damage"] == results[0]["raw_damage"]["total"] // (
        2 if success else 1
    )
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 2
    result = _run(
        "resolve_srd_environment_segment", state, start=[0, 0, 3], end=[42, 0, 3], plane="material"
    )
    assert result.result["context"]["_environment_sight_obscured"]


def _begin(state, data, direction):
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor="hero")
    return _run(
        "begin_combat_turn",
        state,
        participant_data=data,
        environment_incendiary_direction=direction,
    )


@pytest.mark.parametrize("boundary", ["whole_map", "caster_plane"])
def test_incendiary_actual_whole_motion_or_foreign_caster_plane_rejects_atomically(boundary):
    state, data, params = _case()
    if boundary == "whole_map":
        state[f"{SRD}.state"]["spatial_scene"]["map"]["bounds"]["max"][0] = 62
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    if boundary == "caster_plane":
        _position(state, "hero")["metadata"]["plane"] = "ethereal"
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor="hero")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "begin_combat_turn",
            state,
            participant_data=data,
            environment_incendiary_direction=[1, 0, 0],
        )
    assert state == before


def test_incendiary_actual_higher_slot_preserves_printed_ten_dice():
    state, data, params = _case()
    params["slot_resource"] = "spell_slot_9"
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["spell_level"] == 9
    assert (
        state[f"{SRD}.entities"]["guard"]["last_environment_sphere_save"]["raw_damage"]["count"]
        == 10
    )
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == 2


def test_incendiary_actual_start_choice_moves_into_creature_once_then_own_end_delivers():
    state, data, params = _case(inside=False)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    _begin(state, data, [1, 0, 0])
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["origin"] == [50, 0, 3] and source["cloud_last_move"]["to"] == [50, 0, 3]
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] < 1000 and guard["last_environment_sphere_save"]["raw_damage"]["count"] == 10
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 2
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "move_srd_incendiary_cloud_source",
            state,
            caster_state="hero",
            area_id="hero",
            direction=[1, 0, 0],
        )
    assert state == before
    state[f"{SRD}.combat"].update(turn_active=False, current_actor="guard")
    _run("begin_combat_turn", state, participant_data=data)
    hp = state[f"{SRD}.entities"]["guard"]["hp"]
    _run("end_combat_turn", state, participant_data=data)
    assert state[f"{SRD}.entities"]["guard"]["hp"] < hp


@pytest.mark.parametrize("direction", [[0, 0, 0], [-1, 0, 0], None])
def test_incendiary_missing_zero_or_toward_direction_rejects_real_start_atomically(direction):
    state, data, params = _case(inside=False)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor="hero")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "begin_combat_turn",
            state,
            participant_data=data,
            environment_incendiary_direction=direction,
        )
    assert state == before


@pytest.mark.parametrize("retirement", ["wind", "concentration", "expiry"])
def test_incendiary_actual_wind_and_lifetime_remove_both_damage_and_obscuration(retirement):
    state, data, params = _case()
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    if retirement == "wind":
        scene = state[f"{SRD}.state"]["spatial_scene"]
        scene["zones"].append(
            {
                "id": "actual-strong-wind",
                "map_id": scene["map"]["id"],
                "shape": {"kind": "box", "min": [-100, -100, -100], "max": [100, 100, 100]},
                "metadata": {"plane": "material", "strong_wind": True},
            }
        )
        _run("refresh_srd_environment_wind", state)
    elif retirement == "concentration":
        _run("end_concentration", state, caster_state="hero")
    else:
        _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert not state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    before = state[f"{SRD}.entities"]["guard"]["hp"]
    _run("resolve_srd_environment_sphere_event", state, target_state="guard", area_id="hero")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == before
    result = _run(
        "resolve_srd_environment_segment", state, start=[0, 0, 3], end=[42, 0, 3], plane="material"
    )
    assert not result.result["context"]["_environment_sight_obscured"]


def test_public_incendiary_paid_cloud_chosen_turn_movement_reopen_retry_and_retirement(
    tmp_path,
    monkeypatch,
):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as casting
    from tests.srd_full.test_srd_spell_environment import _woodland_test_archive

    monkeypatch.setattr(casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(casting, "_spell_case", lambda _name: _case(inside=False))
    database = tmp_path / "incendiary-cloud.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = casting._create_spell_campaign(
            owner,
            "incendiary_cloud",
            target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0},
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="incendiary-cast",
            idempotency_key="incendiary-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=params,
        )
        mechanics = StatelessMcpMechanics(owner)
        first = mechanics.execute_action(command)
        assert first.status is ActionStatus.COMMITTED, first.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["guard"]["hp"] == 1000
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 2
        assert saved.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["appearance_completed"]

    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(command).result == first.result
        assert owner.snapshot("casting").state == saved.state

        def act(label, tool, **parameters):
            return mechanics.execute_action(
                replace(
                    command,
                    action_id=label,
                    idempotency_key=label,
                    expected_revision=owner.snapshot("casting").revision,
                    tool_name=tool,
                    parameters=parameters,
                )
            )

        assert (
            act(
                "incendiary-combat",
                "start_group_combat_encounter",
                combatants=["hero", "guard", "other"],
            ).status
            is ActionStatus.COMMITTED
        )
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"] == "hero"
        start = replace(
            command,
            action_id="incendiary-start",
            idempotency_key="incendiary-start",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="begin_combat_turn",
            parameters={"environment_incendiary_direction": [1, 0, 0]},
        )
        moved = mechanics.execute_action(start)
        assert moved.status is ActionStatus.COMMITTED, moved.result
        struck = owner.snapshot("casting")
        source = struck.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
        assert source["origin"] == [50, 0, 3] and source["cloud_last_move"]["to"] == [50, 0, 3]
        assert struck.state[f"{SRD}.entities"]["guard"]["hp"] < 1000
        assert (
            struck.state[f"{SRD}.entities"]["guard"]["last_environment_sphere_save"]["raw_damage"][
                "count"
            ]
            == 10
        )
        assert struck.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 2

    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == struck.state
        assert owner.execute_action(command).result == first.result
        assert owner.execute_action(start).result == moved.result
        assert owner.snapshot("casting").state == struck.state
        retire = replace(
            command,
            action_id="incendiary-retire",
            idempotency_key="incendiary-retire",
            expected_revision=struck.revision,
            tool_name="end_concentration",
            parameters={"caster_state": "hero"},
        )
        retired = mechanics.execute_action(retire)
        assert retired.status is ActionStatus.COMMITTED, retired.result
        closed = owner.snapshot("casting")
        assert not closed.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
        assert (
            closed.state[f"{SRD}.entities"]["guard"]["hp"]
            == struck.state[f"{SRD}.entities"]["guard"]["hp"]
        )
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == closed.state
        assert owner.execute_action(command).result == first.result
        assert owner.execute_action(start).result == moved.result
        assert owner.execute_action(retire).result == retired.result
        assert owner.snapshot("casting").state == closed.state
