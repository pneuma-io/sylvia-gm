"""Weather choices settle through actual time and influence real later outdoor fog and movement."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_spell_environment import SRD, _fixture, _ground, _position
from tests.srd_full.test_srd_spell_wave3_orders import _turn


def _case():
    state, data, params = _fixture("control_weather", (0, 0, 0))
    _ground(state)
    _position(state, "hero")["metadata"].update(
        setting="outdoors",
        srd_weather={
            "precipitation_stage": 1,
            "temperature_stage": 3,
            "wind_stage": 2,
            "direction": "east",
        },
    )
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"].append(
        {
            "id": "outdoor-patch",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", "min": [-10, -10, -1], "max": [10, 10, 30]},
            "metadata": {"plane": "material", "setting": "outdoors"},
        }
    )
    params["environment_weather_wind_change"] = 1
    return state, data, params


def _cast():
    state, data, params = _case()
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    assert state[f"{SRD}.entities"]["hero"]["extended_casting"]["required_rounds"] == 100
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=100,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    return state, data, params


def _source(state):
    return state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]


def _settle(state):
    source = _source(state)
    wait = (source["weather_ready_tick"] - state[f"{SRD}.state"]["lasting_tick"]) // 2
    _run("advance_srd_lasting_time", state, elapsed_rounds=wait)


def test_weather_actual_paid_long_cast_roll_deadline_and_later_stage():
    state, _, _ = _cast()
    source = _source(state)
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 2
    assert source["weather_current"]["wind_stage"] == 2
    assert source["weather_chosen"]["wind_stage"] == 3
    assert source["weather_pending"]
    assert source["weather_delay_roll"]["count"] == 1 and source["weather_delay_roll"]["sides"] == 4
    assert (
        source["weather_ready_tick"] - state[f"{SRD}.state"]["lasting_tick"]
        == source["weather_delay_roll"]["total"] * 200
    )
    assert source["expires_tick"] - state[f"{SRD}.state"]["lasting_tick"] == 9600
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="not yet taken effect"):
        _run(
            "choose_srd_control_weather", state, caster_state="hero", area_id="hero", wind_change=1
        )
    assert state == before
    _settle(state)
    assert not _source(state)["weather_pending"]
    assert _source(state)["weather_current"]["wind_stage"] == 3
    assert _position(state, "hero")["metadata"]["srd_weather"]["wind_stage"] == 2


def test_weather_actual_settled_strong_wind_ends_native_outdoor_fog():
    state, _, _ = _cast()
    _run(
        "cast_spell_environment",
        state,
        caster_state="other",
        spell="fog_cloud",
        slot_level=1,
        concentration_save_modifier=0,
        origin=[5, 0, 0],
    )
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["other"]["active"]
    _settle(state)
    assert not state[f"{SRD}.state"]["persistent_spell_areas"]["other"]["active"]
    assert _source(state)["active"]


def test_weather_actual_strong_wind_preserves_native_fog_in_an_admitted_indoor_room():
    state, _, _ = _cast()
    state[f"{SRD}.state"]["spatial_scene"]["zones"].append(
        {
            "id": "indoor-fog-room",
            "map_id": "orders",
            "shape": {"kind": "box", "min": [20, -10, 0], "max": [40, 10, 20]},
            "metadata": {"plane": "material", "setting": "indoors"},
        }
    )
    _run(
        "cast_spell_environment",
        state,
        caster_state="other",
        spell="fog_cloud",
        slot_level=1,
        concentration_save_modifier=0,
        origin=[30, 0, 0],
    )
    _settle(state)
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["other"]["active"]
    assert _source(state)["weather_current"]["wind_stage"] == 3


def test_weather_actual_committed_indoor_entry_ends_source_and_retains_gradual_return():
    state, data, _ = _cast()
    _settle(state)
    state[f"{SRD}.state"]["spatial_scene"]["zones"].append(
        {
            "id": "actual-room",
            "map_id": state[f"{SRD}.state"]["spatial_scene"]["map"]["id"],
            "shape": {"kind": "box", "min": [15, -10, 0], "max": [35, 10, 20]},
            "metadata": {"plane": "material", "setting": "indoors"},
        }
    )
    _turn(state, "hero", data)
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[30, 0, 0])
    assert _position(state, "hero")["position"] == [30, 0, 0]
    assert not _source(state)["active"]
    returns = [
        r
        for r in state[f"{SRD}.state"]["turn_effects"]
        if r.get("kind") == "environment_weather_return"
    ]
    assert len(returns) == 1 and returns[0]["phase"] == "gradual_return"
    assert returns[0]["origin"] == [30, 0, 0]
    assert returns[0]["current"]["wind_stage"] == 3 and returns[0]["normal"]["wind_stage"] == 2
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None


@pytest.mark.parametrize("setting", ["indoors", "unknown"])
def test_weather_actual_cast_setting_rejects_before_channel_cost(setting):
    state, data, params = _case()
    _position(state, "hero")["metadata"]["setting"] = setting
    state[f"{SRD}.state"]["spatial_scene"]["zones"] = []
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="outdoors"):
        _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    assert state == before


def test_weather_actual_unknown_stage_rejects_before_starting_the_channel():
    state, data, params = _case()
    del _position(state, "hero")["metadata"]["srd_weather"]["wind_stage"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="weather stage table"):
        _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    assert state == before


def test_weather_actual_indoor_entry_interrupts_unfinished_channel_without_a_slot():
    state, data, params = _case()
    state[f"{SRD}.state"]["spatial_scene"]["zones"].append(
        {
            "id": "actual-room",
            "map_id": state[f"{SRD}.state"]["spatial_scene"]["map"]["id"],
            "shape": {"kind": "box", "min": [15, -10, 0], "max": [35, 10, 20]},
            "metadata": {"plane": "material", "setting": "indoors"},
        }
    )
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    _turn(state, "hero", data)
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[30, 0, 0])
    assert state[f"{SRD}.entities"]["hero"]["extended_casting"] is None
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 3


@pytest.mark.parametrize(
    "point,plane,known",
    [
        ([26400, 0, 0], "material", True),
        ([26400.01, 0, 0], "material", False),
        ([0, 0, 0], "ethereal", False),
    ],
)
def test_weather_actual_five_mile_point_scope_and_plane(point, plane, known):
    state, _, _ = _cast()
    state[f"{SRD}.state"]["spatial_scene"]["map"]["bounds"].update(
        min=[-30000, -30000, -1000], max=[30000, 30000, 1000]
    )
    state[f"{SRD}.state"]["spatial_scene"]["map"]["grid"]["origin"] = [-30000, -30000]
    state[f"{SRD}.state"]["spatial_scene"]["map"]["grid"]["cell_size"] = 500
    result = _run("resolve_srd_control_weather_point", state, point=point, plane=plane)
    assert result.result["context"]["_weather_known"] is known


def test_weather_actual_later_choice_after_settlement_changes_other_stages_and_direction():
    state, _, _ = _cast()
    _settle(state)
    resources = deepcopy(state[f"{SRD}.entities"]["hero"]["resources"])
    _run(
        "choose_srd_control_weather",
        state,
        caster_state="hero",
        area_id="hero",
        precipitation_change=1,
        temperature_change=-1,
        wind_change=1,
        direction="north",
    )
    assert _source(state)["weather_current"]["precipitation_stage"] == 1
    _settle(state)
    assert _source(state)["weather_current"] == {
        "precipitation_stage": 2,
        "temperature_stage": 2,
        "wind_stage": 4,
        "direction": "north",
    }
    assert state[f"{SRD}.entities"]["hero"]["resources"] == resources


@pytest.mark.parametrize("choice", [{"precipitation_change": -1}, {"direction": "north"}])
def test_weather_actual_choice_requires_one_printed_stage_and_stays_within_its_table(choice):
    state, _, _ = _cast()
    _settle(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("choose_srd_control_weather", state, caster_state="hero", area_id="hero", **choice)
    assert state == before


@pytest.mark.parametrize("retirement", ["concentration", "expiry", "dispel"])
def test_weather_actual_exact_source_removal_preserves_the_returning_consequence(retirement):
    state, _, _ = _cast()
    if retirement == "concentration":
        _settle(state)
        _run("end_concentration", state, caster_state="hero")
    elif retirement == "expiry":
        # Cross the real stage deadline and source expiry in one actual clock action.
        _run("advance_srd_lasting_time", state, elapsed_rounds=4800)
    else:
        _settle(state)
        _run(
            "dispel_srd_environment_source",
            state,
            caster_state="hero",
            area_id="hero",
            slot_level=8,
            casting_modifier=4,
            casting_ability="wisdom",
            point=[0, 0, 0],
        )
    assert not _source(state)["active"]
    returns = [
        r
        for r in state[f"{SRD}.state"]["turn_effects"]
        if r.get("kind") == "environment_weather_return"
    ]
    assert len(returns) == 1 and returns[0]["current"]["wind_stage"] == 3
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert [
        r
        for r in state[f"{SRD}.state"]["turn_effects"]
        if r.get("kind") == "environment_weather_return"
    ] == returns


def test_public_weather_paid_long_cast_reopen_retry_transition_fog_and_indoor_end(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as casting
    from tests.srd_full.test_srd_spell_environment import _woodland_test_archive

    def case():
        state, data, params = _case()
        state[f"{SRD}.state"]["spatial_scene"]["zones"].append(
            {
                "id": "actual-room",
                "map_id": state[f"{SRD}.state"]["spatial_scene"]["map"]["id"],
                "shape": {"kind": "box", "min": [15, -10, 0], "max": [35, 10, 20]},
                "metadata": {"plane": "material", "setting": "indoors"},
            }
        )
        _run(
            "cast_spell_environment",
            state,
            caster_state="other",
            spell="fog_cloud",
            slot_level=1,
            concentration_save_modifier=0,
            origin=[5, 0, 0],
        )
        return state, data, params

    monkeypatch.setattr(casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(casting, "_spell_case", lambda _name: case())
    database = tmp_path / "weather.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = casting._create_spell_campaign(owner, "control_weather")
        template = ActionCommand(
            campaign_id="casting",
            action_id="weather-begin",
            idempotency_key="weather-begin",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="begin_srd_spell_cast",
            parameters=_begin(params),
        )
        begun = StatelessMcpMechanics(owner).execute_action(template)
        assert begun.status is ActionStatus.COMMITTED, begun.result
        channel = owner.snapshot("casting")
        assert channel.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 3
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == channel.state
        assert owner.execute_action(template).result == begun.result
        mechanics = StatelessMcpMechanics(owner)

        def command(label, tool, parameters):
            return replace(
                template,
                action_id=label,
                idempotency_key=label,
                expected_revision=owner.snapshot("casting").revision,
                tool_name=tool,
                parameters=parameters,
            )

        progress = command(
            "weather-progress",
            "continue_srd_spell_cast",
            {
                "caster_state": "hero",
                "elapsed_rounds": 100,
                "uninterrupted": True,
                "verbal_component_available": True,
                "somatic_component_available": True,
                "material_component_available": True,
            },
        )
        continued = mechanics.execute_action(progress)
        assert continued.status is ActionStatus.COMMITTED, continued.result
        paid_command = command("weather-paid", "cast_spell_outside_combat", params)
        paid = mechanics.execute_action(paid_command)
        assert paid.status is ActionStatus.COMMITTED, paid.result
        pending = owner.snapshot("casting")
        assert pending.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 2
        source = pending.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
        assert source["weather_current"]["wind_stage"] == 2 and source["weather_pending"]
        assert pending.state[f"{SRD}.state"]["persistent_spell_areas"]["other"]["active"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == pending.state
        assert owner.execute_action(paid_command).result == paid.result
        mechanics = StatelessMcpMechanics(owner)
        wait = (source["weather_ready_tick"] - pending.state[f"{SRD}.state"]["lasting_tick"]) // 2
        elapsed = command("weather-settle", "advance_srd_lasting_time", {"elapsed_rounds": wait})
        transitioned = mechanics.execute_action(elapsed)
        assert transitioned.status is ActionStatus.COMMITTED, transitioned.result
        settled = owner.snapshot("casting")
        assert (
            settled.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["weather_current"][
                "wind_stage"
            ]
            == 3
        )
        assert not settled.state[f"{SRD}.state"]["persistent_spell_areas"]["other"]["active"]
        later_choice_command = command(
            "weather-next-choice",
            "choose_srd_control_weather",
            {"caster_state": "hero", "area_id": "hero", "wind_change": 1, "direction": "north"},
        )
        later_choice = mechanics.execute_action(later_choice_command)
        assert later_choice.status is ActionStatus.COMMITTED, later_choice.result
        next_pending = owner.snapshot("casting")
        next_source = next_pending.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
        assert next_source["weather_current"]["wind_stage"] == 3 and next_source["weather_pending"]
        assert next_source["weather_chosen"]["wind_stage"] == 4
        assert next_pending.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_8"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == next_pending.state
        assert owner.execute_action(later_choice_command).result == later_choice.result
        mechanics = StatelessMcpMechanics(owner)
        wait = (
            next_source["weather_ready_tick"] - next_pending.state[f"{SRD}.state"]["lasting_tick"]
        ) // 2
        next_elapsed = command(
            "weather-next-settle", "advance_srd_lasting_time", {"elapsed_rounds": wait}
        )
        next_transitioned = mechanics.execute_action(next_elapsed)
        assert next_transitioned.status is ActionStatus.COMMITTED, next_transitioned.result
        final_conditions = owner.snapshot("casting").state[f"{SRD}.state"][
            "persistent_spell_areas"
        ]["hero"]["weather_current"]
        assert final_conditions["wind_stage"] == 4 and final_conditions["direction"] == "north"
        assert (
            mechanics.execute_action(
                command(
                    "weather-combat",
                    "start_group_combat_encounter",
                    {"combatants": ["hero", "guard", "other"]},
                )
            ).status
            is ActionStatus.COMMITTED
        )
        assert (
            mechanics.execute_action(command("weather-turn", "begin_combat_turn", {})).status
            is ActionStatus.COMMITTED
        )
        moved_command = command(
            "weather-entry",
            "move_srd_scene_creature",
            {"actor_state": "hero", "destination": [30, 0, 0]},
        )
        moved = mechanics.execute_action(moved_command)
        assert moved.status is ActionStatus.COMMITTED, moved.result
        ended = owner.snapshot("casting")
        assert not ended.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
        assert any(
            r.get("kind") == "environment_weather_return"
            for r in ended.state[f"{SRD}.state"]["turn_effects"]
        )
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == ended.state
        for original, result in (
            (template, begun),
            (progress, continued),
            (paid_command, paid),
            (elapsed, transitioned),
            (later_choice_command, later_choice),
            (next_elapsed, next_transitioned),
            (moved_command, moved),
        ):
            assert owner.execute_action(original).result == result.result
        assert owner.snapshot("casting").state == ended.state
