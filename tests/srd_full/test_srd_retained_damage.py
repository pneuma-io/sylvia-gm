"""Retained damage sources must support their actual later action and exact retirement."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_environment import SRD, _fixture, _position
from tests.srd_full.test_srd_spell_wave3_orders import _turn


def _lightning_case(*, slot=3, setting="outdoors", weather="other", success=False):
    state, data, params = _fixture("call_lightning", origin=(40, 0, 60), slot=slot)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    _position(state, "hero")["metadata"].update(setting=setting, weather=weather)
    scene["zones"].append(
        {
            "id": "lightning-daylight",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
            "metadata": {"plane": "material", "lighting": "bright"},
        }
    )
    _position(state, "guard")["position"] = [25, 0, 0]
    _position(state, "other")["position"] = [27, 0, 0]
    for actor in ("guard", "other"):
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=0)
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["dexterity"] = (
            100 if success else -100
        )
    params["environment_lightning_point"] = [25, 0, 3]
    return state, data, params


@pytest.mark.parametrize(
    "slot,setting,weather,count",
    [
        (3, "outdoors", "other", 3),
        (3, "outdoors", "storm", 4),
        (5, "outdoors", "storm", 6),
        (3, "indoors", "storm", 3),
    ],
)
def test_call_lightning_paid_actual_cloud_shared_bolt_weather_and_upcast(
    slot, setting, weather, count
):
    state, data, params = _lightning_case(slot=slot, setting=setting, weather=weather)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["volume"] == {
        "kind": "cylinder",
        "center": [40, 0, 60],
        "radius": 60,
        "height": 10,
    }
    assert source["bolt_sequence"] == 1 and source["appearance_completed"]
    results = [state[f"{SRD}.entities"][a]["last_call_lightning_save"] for a in ("guard", "other")]
    assert results[0]["raw_damage"] == results[1]["raw_damage"]
    assert results[0]["raw_damage"]["count"] == count
    assert all(r["origin"] == [25, 0, 3] and r["outcome"] == "failure" for r in results)
    assert state[f"{SRD}.entities"]["hero"]["resources"][f"spell_slot_{slot}"] == 2


@pytest.mark.parametrize(
    "invalid", ["setting", "weather", "above", "below", "radius", "bounds", "sight"]
)
def test_call_lightning_actual_placement_and_world_facts_reject_atomic_cast(invalid):
    state, data, params = _lightning_case()
    if invalid in ("setting", "weather"):
        _position(state, "hero")["metadata"].pop(invalid)
    elif invalid == "above":
        params["environment_origin"] = [40, 0, 0]
    elif invalid == "below":
        params["environment_lightning_point"] = [25, 0, 60]
    elif invalid == "radius":
        params["environment_lightning_point"] = [101, 0, 3]
    elif invalid == "bounds":
        scene = state[f"{SRD}.state"]["spatial_scene"]
        scene["map"]["bounds"]["max"][2] = 65
        scene["map"]["layers"][0]["max_elevation"] = 65
        scene["zones"][0]["shape"]["max"][2] = 65
    else:
        state[f"{SRD}.state"]["spatial_scene"]["zones"][0]["metadata"]["lighting"] = "darkness"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before


def test_call_lightning_later_real_magic_action_retargets_without_slot_or_cloud_move():
    state, data, params = _lightning_case(weather="storm", success=True)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    source = deepcopy(state[f"{SRD}.state"]["persistent_spell_areas"]["hero"])
    before = state[f"{SRD}.entities"]["guard"]["hp"]
    _position(state, "hero")["metadata"]["weather"] = "other"
    _turn(state, "hero")
    _run(
        "call_srd_lightning_source",
        state,
        participant_data=data,
        caster_state="hero",
        area_id="hero",
        point=[26, 0, 3],
    )
    target = state[f"{SRD}.entities"]["guard"]
    assert target["hp"] < before
    assert target["last_call_lightning_save"]["outcome"] == "success"
    assert target["last_call_lightning_save"]["raw_damage"]["count"] == 4
    assert (
        target["last_call_lightning_save"]["applied_damage"]
        == target["last_call_lightning_save"]["raw_damage"]["total"] // 2
    )
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
    current = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert current["origin"] == source["origin"] and current["volume"] == source["volume"]
    assert current["bolt_point"] == [26, 0, 3] and current["bolt_sequence"] == 2
    assert state[f"{SRD}.combat"]["normal_action_spent"]
    held = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "call_srd_lightning_source",
            state,
            participant_data=data,
            caster_state="hero",
            area_id="hero",
            point=[26, 0, 3],
        )
    assert state == held
    with pytest.raises(RulesetPreconditionRejected):
        _run("settle_srd_call_lightning_target", state, target_state="guard", area_id="hero")
    assert state == held


@pytest.mark.parametrize("qualification", ["body_boundary", "outside", "plane", "opaque_wall"])
def test_call_lightning_affected_space_uses_real_body_plane_and_bolt_exposure(qualification):
    state, data, params = _lightning_case()
    half_width = _position(state, "other")["footprint"]["half_extents"][0]
    _position(state, "other")["position"] = [30 + half_width, 0, 0]
    if qualification == "outside":
        _position(state, "other")["position"][0] += 0.000001
    elif qualification == "plane":
        _position(state, "other")["metadata"]["plane"] = "ethereal"
    elif qualification == "opaque_wall":
        _position(state, "other")["position"][0] = 30
        scene = state[f"{SRD}.state"]["spatial_scene"]
        scene["geometry"].append(
            {
                "id": "bolt-wall",
                "map_id": scene["map"]["id"],
                "volume": {"kind": "box", "min": [27, -10, 0], "max": [28, 10, 10]},
                "active": True,
                "movement": "blocking",
                "occupies_cells": False,
                "visibility": "opaque",
                "metadata": {"plane": "material"},
                "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
            }
        )
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    other = state[f"{SRD}.entities"]["other"]
    if qualification == "body_boundary":
        assert other["hp"] < 1000 and other["last_call_lightning_save"]["origin"] == [25, 0, 3]
    else:
        assert other["hp"] == 1000 and other.get("last_call_lightning_save") is None


@pytest.mark.parametrize("retirement", ["concentration", "expiry", "dispel"])
def test_call_lightning_exact_source_removal_prevents_another_earned_bolt(retirement):
    state, data, params = _lightning_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    if retirement == "concentration":
        _run("end_concentration", state, caster_state="hero")
    elif retirement == "expiry":
        _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    else:
        _run(
            "dispel_srd_environment_source",
            state,
            participant_data=data,
            caster_state="hero",
            caster="hero",
            area_id="hero",
            slot_level=3,
            casting_modifier=4,
            casting_ability="wisdom",
            point=[40, 0, 65],
        )
    assert not state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    _turn(state, "hero")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "call_srd_lightning_source",
            state,
            participant_data=data,
            caster_state="hero",
            area_id="hero",
            point=[25, 0, 3],
        )
    assert state == before


def test_public_call_lightning_paid_initial_and_later_bolt_reopen_retry_then_retirement(
    tmp_path,
    monkeypatch,
):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from sylvia_gm.domain.errors import MechanicsRejected
    from tests import test_srd_public_spell_casting as casting
    from tests.srd_full.test_srd_spell_environment import _woodland_test_archive

    monkeypatch.setattr(casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(casting, "_spell_case", lambda _name: _lightning_case(weather="storm"))
    database = tmp_path / "call-lightning.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = casting._create_spell_campaign(
            owner,
            "call_lightning",
            target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0},
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="lightning-cast",
            idempotency_key="lightning-cast",
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
        source = saved.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
        guard = saved.state[f"{SRD}.entities"]["guard"]
        assert source["storm_bonus"] and source["bolt_sequence"] == 1
        assert guard["last_call_lightning_save"]["raw_damage"]["count"] == 4
        assert guard["hp"] == 1000 - guard["last_call_lightning_save"]["applied_damage"]
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2

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
                "lightning-combat",
                "start_group_combat_encounter",
                combatants=["hero", "guard", "other"],
            ).status
            is ActionStatus.COMMITTED
        )
        for i in range(3):
            current = owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"]
            assert act(f"lightning-begin-{i}", "begin_combat_turn").status is ActionStatus.COMMITTED
            if current == "hero":
                break
            assert act(f"lightning-end-{i}", "end_combat_turn").status is ActionStatus.COMMITTED
            assert (
                act(f"lightning-advance-{i}", "advance_group_combat_encounter_turn").status
                is ActionStatus.COMMITTED
            )
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "hero"
        bolt = replace(
            command,
            action_id="lightning-later",
            idempotency_key="lightning-later",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="call_srd_lightning_source",
            parameters={"caster_state": "hero", "area_id": "hero", "point": [26, 0, 3]},
        )
        later = mechanics.execute_action(bolt)
        assert later.status is ActionStatus.COMMITTED, later.result
        struck = owner.snapshot("casting")
        source = struck.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
        assert source["bolt_sequence"] == 2 and source["origin"] == [40, 0, 60]
        assert source["bolt_point"] == [26, 0, 3] and source["appearance_completed"]
        assert struck.state[f"{SRD}.entities"]["guard"]["hp"] < guard["hp"]
        assert struck.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
        assert struck.state[f"{SRD}.combat"]["normal_action_spent"]

    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == struck.state
        assert owner.execute_action(command).result == first.result
        assert mechanics.execute_action(bolt).result == later.result
        assert owner.snapshot("casting").state == struck.state
        retire = replace(
            command,
            action_id="lightning-retire",
            idempotency_key="lightning-retire",
            expected_revision=struck.revision,
            tool_name="end_concentration",
            parameters={"caster_state": "hero"},
        )
        retired = mechanics.execute_action(retire)
        assert retired.status is ActionStatus.COMMITTED, retired.result
        closed = owner.snapshot("casting")
        assert not closed.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
        attempt = replace(
            bolt,
            action_id="lightning-ended",
            idempotency_key="lightning-ended",
            expected_revision=closed.revision,
        )
        with pytest.raises(MechanicsRejected, match="current"):
            mechanics.execute_action(attempt)
        assert owner.snapshot("casting").state == closed.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == closed.state
        assert owner.execute_action(command).result == first.result
        assert owner.execute_action(bolt).result == later.result
        assert owner.execute_action(retire).result == retired.result
        assert owner.snapshot("casting").state == closed.state
