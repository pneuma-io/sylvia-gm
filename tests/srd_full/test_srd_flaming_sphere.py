"""A genuine grounded paid Sphere must settle later turns and stop its real Bonus roll."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_environment import SRD, _fixture, _ground, _position
from tests.srd_full.test_srd_spell_wave3_orders import _turn


def _case(*, origin=(20, 0, 0), slot=2, target=(30, 0, 0), success=False):
    state, data, params = _fixture("flaming_sphere", origin, slot)
    _ground(state)
    for actor, point in (("guard", list(target)), ("other", [70, 0, 0])):
        _position(state, actor)["position"] = point
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=0)
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["dexterity"] = (
            100 if success else -100
        )
    return state, data, params


def _cast(**choices):
    state, data, params = _case(**choices)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    return state, data, params


def test_flaming_actual_paid_five_foot_sphere_has_ground_center_and_no_appearance_packet():
    state, _, _ = _cast(target=(26, 0, 0))
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["origin"] == [20, 0, 2.5] and source["radius"] == 2.5
    assert source["ground_point"] == [20, 0, 0] and source["ground_id"] == "floor"
    assert source["expires_tick"] == 20 and source["active"]
    assert state[f"{SRD}.entities"]["guard"].get("last_flaming_sphere_save") is None
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2


@pytest.mark.parametrize("success", [False, True])
def test_flaming_actual_target_end_within_five_of_body_saves_once(success):
    state, data, _ = _cast(target=(27, 0, 0), slot=4, success=success)
    _turn(state, "guard")
    _run("end_combat_turn", state, participant_data=data)
    guard = state[f"{SRD}.entities"]["guard"]
    saved = deepcopy(guard["last_flaming_sphere_save"])
    assert saved["trigger"] == "end" and saved["raw_damage"]["count"] == 4
    assert saved["applied_damage"] == saved["raw_damage"]["total"] // (2 if success else 1)
    assert saved["outcome"] == ("success" if success else "failure")
    assert guard["hp"] == 1000 - saved["applied_damage"]


def test_flaming_actual_bonus_ground_roll_rams_first_creature_and_stops_before_requested_point():
    state, _, _ = _cast(target=(30, 0, 0))
    _turn(state, "hero")
    _run("roll_srd_flaming_sphere", state, caster_state="hero", area_id="hero", point=[45, 0, 0])
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["origin"] == pytest.approx([27.1, 0, 2.5])
    assert (
        source["last_roll"]["requested"] == [45, 0, 2.5]
        and source["last_roll"]["target"] == "guard"
    )
    assert source["roll_stopped_turn"] == state[f"{SRD}.combat"]["turn_sequence"]
    saved = state[f"{SRD}.entities"]["guard"]["last_flaming_sphere_save"]
    assert saved["trigger"] == "ram" and saved["raw_damage"]["count"] == 2
    assert state[f"{SRD}.entities"]["guard"]["hp"] < 1000
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "roll_srd_flaming_sphere", state, caster_state="hero", area_id="hero", point=[35, 0, 0]
        )
    assert state == before


@pytest.mark.parametrize("boundary", ["occupied", "passable_creature", "no_ground", "off_ground"])
def test_flaming_actual_formation_space_and_ground_reject_atomically(boundary):
    state, data, params = _case(
        target=(20, 0, 0) if boundary in ("occupied", "passable_creature") else (30, 0, 0)
    )
    if boundary == "passable_creature":
        _position(state, "guard")["movement"] = "passable"
    if boundary == "no_ground":
        state[f"{SRD}.state"]["spatial_scene"]["geometry"] = []
    if boundary == "off_ground":
        params["environment_origin"][2] = 1
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before


@pytest.mark.parametrize("boundary", ["solid", "route_range", "foreign_plane"])
def test_flaming_actual_whole_body_roll_rejects_before_cost_or_movement(boundary):
    state, _, _ = _cast(target=(90, 0, 0))
    _turn(state, "hero")
    destination = [45, 0, 0]
    if boundary == "solid":
        wall = deepcopy(state[f"{SRD}.state"]["spatial_scene"]["geometry"][0])
        wall.update(
            id="actual-solid", volume={"kind": "box", "min": [30, -10, 0], "max": [31, 10, 10]}
        )
        state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(wall)
    elif boundary == "route_range":
        destination[0] = 51
    else:
        _position(state, "hero")["metadata"]["plane"] = "ethereal"
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(
            "roll_srd_flaming_sphere", state, caster_state="hero", area_id="hero", point=destination
        )
    assert state == before


@pytest.mark.parametrize("retirement", ["concentration", "expiry", "dispel"])
def test_flaming_actual_lifetime_ends_damage_roll_and_light(retirement):
    state, _, _ = _cast(target=(27, 0, 0))
    assert state[f"{SRD}.state"]["has_flaming_sphere"]
    light = _run("resolve_srd_flaming_sphere_light", state, point=[20, 0, 2.5], plane="material")
    assert light.result["context"]["_flaming_magic_bright"]
    if retirement == "concentration":
        _run("end_concentration", state, caster_state="hero")
    elif retirement == "expiry":
        _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    else:
        _run(
            "dispel_srd_environment_source",
            state,
            caster_state="other",
            area_id="hero",
            slot_level=3,
            casting_modifier=4,
            casting_ability="intelligence",
            point=[20, 0, 2.5],
        )
    assert not state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    light = _run("resolve_srd_flaming_sphere_light", state, point=[20, 0, 2.5], plane="material")
    assert not light.result["context"]["_flaming_magic_bright"]
    _turn(state, "guard")
    before = state[f"{SRD}.entities"]["guard"]["hp"]
    _run(
        "resolve_srd_flaming_sphere_event",
        state,
        target_state="guard",
        area_id="hero",
        trigger="end",
    )
    assert state[f"{SRD}.entities"]["guard"]["hp"] == before


def test_flaming_actual_body_boundary_and_foreign_plane_light():
    state, _, _ = _cast(target=(90, 0, 0))
    for point, expected in (
        ([40, 0, 2.5], (True, False)),
        ([41, 0, 2.5], (False, True)),
        ([60, 0, 2.5], (False, True)),
        ([61, 0, 2.5], (False, False)),
    ):
        result = _run("resolve_srd_flaming_sphere_light", state, point=point, plane="material")
        context = result.result["context"]
        assert (context["_flaming_magic_bright"], context["_flaming_magic_dim"]) == expected
        light = _run(
            "resolve_srd_information_light",
            state,
            point=point,
            plane="material",
            ambient="darkness",
        ).result["context"]["_information_light"]
        assert light == ("bright" if expected[0] else "dim" if expected[1] else "darkness")
    result = _run("resolve_srd_flaming_sphere_light", state, point=[20, 0, 2.5], plane="ethereal")
    assert not result.result["context"]["_flaming_magic_bright"]


@pytest.mark.parametrize(
    "slot,plane,ended", [(2, "material", True), (3, "material", False), (2, "ethereal", False)]
)
def test_flaming_actual_darkness_install_retires_low_slot_whole_source_only(slot, plane, ended):
    state, _, _ = _cast(target=(90, 0, 0), slot=slot)
    _position(state, "other")["metadata"]["plane"] = plane
    _run(
        "cast_spell_illumination_spell",
        state,
        caster_state="other",
        target_state="other",
        spell="darkness",
        slot_level=2,
        concentration_modifier=0,
        casting_ability="intelligence",
        save_dc=18,
        caster_can_touch_target=False,
        illumination_position=[60, 0, 2.5],
    )
    assert bool(state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]) is not ended
    light = _run(
        "resolve_srd_information_light",
        state,
        point=[60, 0, 2.5],
        plane="material",
        ambient="darkness",
    ).result["context"]["_information_light"]
    assert light == ("magical_darkness" if ended else "dim")


def test_flaming_actual_object_touch_relights_consumed_ordinary_flame_and_rain_extinguishes():
    state, data, params = _case(target=(90, 0, 0))
    for name, y, extra in (
        ("touched", 3, {}),
        ("outside", 4, {}),
        ("carried", 3, {"carried_by": "guard"}),
        ("unknown", -3, {"flammable": None}),
    ):
        row = deepcopy(_position(state, "guard"))
        row.update(id=name, position=[20, y, 1], height=2, movement="passable")
        row["footprint"] = {"kind": "box", "half_extents": [0.5, 0.5]}
        row["metadata"] = {
            "plane": "material",
            "lighting": "bright",
            "is_object": True,
            "flammable": True,
            "fiction": "Synthetic candle",
            **extra,
        }
        state[f"{SRD}.state"]["spatial_scene"]["entities"].append(row)
        _run("record_srd_ordinary_flame", state, object_id=name, exposed=True)
    _run(
        "cast_spell_object_content_spell",
        state,
        caster_state="hero",
        object_id="",
        spell="create_or_destroy_water",
        slot_level=1,
        object_mode="rain",
        object_point=[0, 0, 0],
        object_cube_face="east",
    )

    def flame(name):
        return next(
            e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == name
        )["metadata"]["srd_ordinary_flame"]

    assert not flame("touched")["burning"]
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert flame("touched") == {"kind": "ordinary_flame", "burning": True, "exposed": True}
    assert not flame("outside")["burning"] and not flame("unknown")["burning"]
    # Carried objects are skipped by the actual fire provider; its existing holder
    # qualification remains the rain owner's responsibility.
    _run(
        "cast_spell_object_content_spell",
        state,
        caster_state="hero",
        object_id="",
        spell="create_or_destroy_water",
        slot_level=1,
        object_mode="rain",
        object_point=[0, 0, 0],
        object_cube_face="east",
    )
    assert not flame("touched")["burning"]
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]


def test_flaming_actual_roll_into_existing_darkness_retires_source_at_committed_origin():
    state, _, _ = _cast(target=(90, 0, 0))
    _run(
        "cast_spell_illumination_spell",
        state,
        caster_state="other",
        target_state="other",
        spell="darkness",
        slot_level=2,
        concentration_modifier=0,
        casting_ability="intelligence",
        save_dc=18,
        caster_can_touch_target=False,
        illumination_position=[80, 0, 2.5],
    )
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    _turn(state, "hero")
    _run("roll_srd_flaming_sphere", state, caster_state="hero", area_id="hero", point=[45, 0, 0])
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["origin"] == [45, 0, 2.5] and not source["active"]
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2


def test_public_flaming_paid_ground_bonus_ram_reopen_retry_and_removal(tmp_path, monkeypatch):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as casting
    from tests.srd_full.test_srd_spell_environment import _woodland_test_archive

    monkeypatch.setattr(casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(casting, "_spell_case", lambda _name: _case())
    database = tmp_path / "flaming-sphere.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = casting._create_spell_campaign(
            owner, "flaming_sphere", target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0}
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="flaming-cast",
            idempotency_key="flaming-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=params,
        )
        mechanics = StatelessMcpMechanics(owner)
        paid = mechanics.execute_action(command)
        assert paid.status is ActionStatus.COMMITTED, paid.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["guard"]["hp"] == 1000
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(command).result == paid.result

        def act(label, tool, **params):
            return mechanics.execute_action(
                replace(
                    command,
                    action_id=label,
                    idempotency_key=label,
                    expected_revision=owner.snapshot("casting").revision,
                    tool_name=tool,
                    parameters=params,
                )
            )

        assert (
            act(
                "flaming-combat",
                "start_group_combat_encounter",
                combatants=["hero", "guard", "other"],
            ).status
            is ActionStatus.COMMITTED
        )
        assert act("flaming-start", "begin_combat_turn").status is ActionStatus.COMMITTED
        roll = replace(
            command,
            action_id="flaming-roll",
            idempotency_key="flaming-roll",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="roll_srd_flaming_sphere",
            parameters={"caster_state": "hero", "area_id": "hero", "point": [45, 0, 0]},
        )
        moved = mechanics.execute_action(roll)
        assert moved.status is ActionStatus.COMMITTED, moved.result
        struck = owner.snapshot("casting")
        assert list(
            struck.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["origin"]
        ) == pytest.approx([27.1, 0, 2.5])
        assert struck.state[f"{SRD}.entities"]["guard"]["hp"] < 1000
        assert struck.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == struck.state
        assert owner.execute_action(command).result == paid.result
        assert owner.execute_action(roll).result == moved.result
        assert owner.snapshot("casting").state == struck.state
        retire = replace(
            command,
            action_id="flaming-retire",
            idempotency_key="flaming-retire",
            expected_revision=struck.revision,
            tool_name="end_concentration",
            parameters={"caster_state": "hero"},
        )
        ended = mechanics.execute_action(retire)
        assert ended.status is ActionStatus.COMMITTED, ended.result
        closed = owner.snapshot("casting")
        assert not closed.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == closed.state
        assert owner.execute_action(roll).result == moved.result
        assert owner.execute_action(retire).result == ended.result
        assert owner.snapshot("casting").state == closed.state
