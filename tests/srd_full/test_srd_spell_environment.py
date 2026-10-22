"""Lasting environment sources affect real scene consumers and admitted public actions."""

from copy import deepcopy

import pytest

from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_result_cards import SRD, _spell_case
from tests.srd_full.test_srd_spell_wave3_orders import _admit_scene


def _fixture(spell="fog_cloud", origin=(10, 0, 0), slot=None):
    state, data, parameters = _spell_case(spell)
    state[f"{SRD}.combat"].update(encounter_active=False, turn_active=False)
    _admit_scene(state)
    state[f"{SRD}.state"].setdefault("turn_effects", [])
    parameters.update(environment_origin=list(origin))
    if slot:
        parameters["slot_resource"] = f"spell_slot_{slot}"
    return state, data, parameters


def _cast(spell="fog_cloud", origin=(10, 0, 0), slot=None):
    state, data, parameters = _fixture(spell, origin, slot)
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert result.result["status"] == "committed"
    return state, data, parameters


def _woodland_case(*, slot=4, targets=("guard", "other"), include_origin=False):
    """Supply real casting grants and actual creature spaces for the self Emanation."""
    state, data, parameters = _fixture("conjure_woodland_beings", slot=slot)
    for actor in ("guard", "other"):
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=0)
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["wisdom"] = -100
    parameters.update(
        environment_force_targets=list(targets), environment_include_origin=include_origin
    )
    return state, data, parameters


def _woodland_cast(**choices):
    state, data, parameters = _woodland_case(**choices)
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert result.result["status"] == "committed"
    return state, data, parameters


def _minor_case(*, slot=4):
    state, data, parameters = _fixture("conjure_minor_elementals", slot=slot)
    _ground(state)
    _position(state, "guard")["metadata"]["enemy_of"] = ["hero"]
    return state, data, parameters


def _minor_cast(**choices):
    state, data, parameters = _minor_case(**choices)
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert result.result["status"] == "committed"
    return state, data, parameters


def test_actual_environment_does_not_require_a_scalar_hearing_target_for_three_rays():
    state, data, _ = _minor_cast()
    _, _, parameters = _spell_case("scorching_ray")
    data["spellcasting_grants"][0]["spells"].append("scorching_ray")
    data["wizard_spellbook"].append("scorching_ray")
    hero = state[f"{SRD}.entities"]["hero"]
    hero["prepared_spells"].append("scorching_ray")
    for target in ("guard", "other"):
        state[f"{SRD}.entities"][target].update(hp=1000, max_hp=1000, temp_hp=0)
    parameters["attack_targets"] = ["guard", "other", "guard"]
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert result.result["status"] == "committed"
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    assert len(result.result["result_card"]["details"]) == 3


def _insects_case(*, slot=5, inside=False, success=False):
    state, data, parameters = _fixture("insect_plague", origin=(40, 0, 3), slot=slot)
    _ground(state)
    for actor in ("guard", "other"):
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=0)
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["constitution"] = (
            100 if success else -100
        )
    _position(state, "guard")["position"] = [35 if inside else 10, 0, 0]
    _position(state, "other")["position"] = [10, 8, 0]
    return state, data, parameters


def _insects_cast(**choices):
    state, data, parameters = _insects_case(**choices)
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert result.result["status"] == "committed"
    return state, data, parameters


def _cloud_case(*, slot=5, inside=False, success=False):
    state, data, parameters = _fixture("cloudkill", origin=(40, 0, 0), slot=slot)
    _ground(state)
    for actor in ("guard", "other"):
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=0)
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["constitution"] = (
            100 if success else -100
        )
    _position(state, "guard")["position"] = [35 if inside else 10, 0, 0]
    _position(state, "other")["position"] = [10, 8, 0]
    return state, data, parameters


def _cloud_cast(**choices):
    state, data, parameters = _cloud_case(**choices)
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert result.result["status"] == "committed"
    return state, data, parameters


def _stinking_case(*, success=False, origin=(40, 0, 0)):
    state, data, parameters = _fixture("stinking_cloud", origin=origin)
    _ground(state)
    for actor in ("guard", "other"):
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=0)
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["constitution"] = (
            100 if success else -100
        )
    _position(state, "guard")["position"] = [35, 0, 0]
    _position(state, "other")["position"] = [10, 8, 0]
    return state, data, parameters


def _stinking_cast(**choices):
    state, data, parameters = _stinking_case(**choices)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    return state, data, parameters


def _web_case(*, slot=2, inside=False, success=False, plane="material"):
    state, data, parameters = _fixture("web", origin=(40, 0, 0), slot=slot)
    _ground(state, plane=plane)
    for actor in ("hero", "guard", "other"):
        _position(state, actor)["metadata"]["plane"] = plane
    for actor in ("guard", "other"):
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["dexterity"] = (
            100 if success else -100
        )
        state[f"{SRD}.entities"][actor]["skill_check_modifiers"] = {"athletics": 100}
    _position(state, "guard")["position"] = [40 if inside else 20, 0, 0]
    _position(state, "other")["position"] = [10, 8, 0]
    return state, data, parameters


def _web_cast(**choices):
    state, data, parameters = _web_case(**choices)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    return state, data, parameters


def test_web_actual_floor_layer_has_fixed_geometry_lifetime_and_no_appearance_save():
    state, _, _ = _web_cast(slot=9, inside=True)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["volume"] == {"kind": "box", "min": [30, -10, 0], "max": [50, 10, 5]}
    assert source["layer"] == "floor" and source["ground_id"] == "floor"
    assert source["expires_tick"] == 1200 and state[f"{SRD}.state"]["has_web"]
    assert state[f"{SRD}.entities"]["guard"].get("last_web_save") is None
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_9"] == 2


@pytest.mark.parametrize("success", [False, True])
def test_web_actual_first_entry_dexterity_without_damage_and_new_restraint_stops(success):
    state, _, _ = _web_cast(success=success)
    hp = state[f"{SRD}.entities"]["guard"]["hp"]
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[40, 0, 0])
    guard = state[f"{SRD}.entities"]["guard"]
    saved = deepcopy(guard["last_web_save"])
    assert saved["timing"] == "entry" and saved["outcome"] == ("success" if success else "failure")
    assert guard["hp"] == hp and ("restrained" in guard["conditions"]) is (not success)
    position = _position(state, "guard")["position"][0]
    assert position == 40 if success else 29 <= position <= 30
    _run("resolve_srd_environment_hazard_event", state, target_state="guard", area_id="hero")
    assert state[f"{SRD}.entities"]["guard"]["last_web_save"] == saved


def test_web_actual_start_save_is_distinct_from_first_entry_and_never_end_turn():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _web_cast(inside=True)
    _turn(state, "guard")
    guard = state[f"{SRD}.entities"]["guard"]
    saved = deepcopy(guard["last_web_save"])
    assert saved["timing"] == "start" and saved["outcome"] == "failure"
    condition_id = saved["condition_source_id"]
    _run("resolve_srd_environment_hazard_start_turn", state, target_state="guard")
    assert state[f"{SRD}.entities"]["guard"]["last_web_save"] == saved
    _run("resolve_srd_environment_hazard_event", state, target_state="guard", area_id="hero")
    later = deepcopy(state[f"{SRD}.entities"]["guard"]["last_web_save"])
    assert later["timing"] == "entry" and later["condition_source_id"] == condition_id
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["last_web_save"] == later


def test_web_real_athletics_action_escape_and_later_failed_turn_start_rebind():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _web_cast(inside=True)
    _turn(state, "guard")
    condition_id = state[f"{SRD}.entities"]["guard"]["last_web_save"]["condition_source_id"]
    _run("escape_srd_web", state, actor_state="guard", source_id=condition_id)
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["last_web_escape"]["success"] and "restrained" not in guard["conditions"]
    assert state[f"{SRD}.combat"]["action_available"] is False
    assert state[f"{SRD}.state"]["timed_condition_sources"][condition_id]["installed"] is False
    _run("end_combat_turn", state)
    _turn(state, "guard")
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]
    rebound = state[f"{SRD}.entities"]["guard"]["last_web_save"]["condition_source_id"]
    assert state[f"{SRD}.state"]["timed_condition_sources"][rebound]["installed"] is True
    assert state[f"{SRD}.state"]["timed_condition_sources"][rebound]["expires_tick"] == 1200


@pytest.mark.parametrize("invalid", ["no_floor", "partial_floor", "nonflat_origin", "bounds"])
def test_web_actual_whole_floor_qualification_rejects_paid_cast_atomically(invalid):
    state, data, parameters = _web_case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if invalid == "no_floor":
        scene["geometry"] = []
    elif invalid == "partial_floor":
        scene["geometry"][-1]["volume"]["max"][0] = 45
    elif invalid == "nonflat_origin":
        parameters["environment_origin"][2] = 1
    else:
        scene["map"]["bounds"]["max"][2] = 4
        for layer in scene["map"]["layers"]:
            layer["max_elevation"] = 4
        for entity in scene["entities"]:
            entity["height"] = 3
    _reject(state, "cast_spell_outside_combat", participant_data=data, **parameters)


def test_web_light_obscurement_uses_actual_floor_box_and_source_removal():
    state, _, _ = _web_cast()
    inside = _run("resolve_srd_environment_point", state, point=[40, 4, 2], plane="material")
    assert inside.result["context"]["_environment_lightly_obscured"] is True
    outside = _run("resolve_srd_environment_point", state, point=[40, 4, 6], plane="material")
    assert outside.result["context"]["_environment_lightly_obscured"] is False
    crossed = _run(
        "resolve_srd_environment_segment", state, start=[20, 0, 2], end=[60, 0, 2], plane="material"
    )
    assert crossed.result["context"]["_environment_lightly_obscured"] is True
    assert crossed.result["context"]["_environment_sight_obscured"] is False
    _run("end_concentration", state, caster_state="hero")
    assert state[f"{SRD}.state"]["has_web"] is False
    cleared = _run("resolve_srd_environment_point", state, point=[40, 4, 2], plane="material")
    assert cleared.result["context"]["_environment_lightly_obscured"] is False


@pytest.mark.parametrize("removal", ["end", "expiry", "dispel"])
def test_web_actual_source_removal_retires_exact_restraint_and_preserves_another(removal):
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, _ = _web_cast(inside=True)
    _run("apply_srd_condition", state, target="other", condition="restrained")
    _turn(state, "guard")
    condition_id = state[f"{SRD}.entities"]["guard"]["last_web_save"]["condition_source_id"]
    if removal == "expiry":
        _run("end_combat_encounter", state, end_reason="fighting_stopped")
        _run("advance_srd_lasting_time", state, elapsed_rounds=600)
    elif removal == "dispel":
        _run(
            "dispel_srd_environment_source",
            state,
            participant_data=data,
            caster_state="hero",
            caster="hero",
            area_id="hero",
            slot_level=3,
            casting_modifier=4,
            casting_ability="intelligence",
        )
    else:
        _run("end_concentration", state, caster_state="hero")
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert "restrained" in state[f"{SRD}.entities"]["other"]["conditions"]
    assert not state[f"{SRD}.state"]["timed_condition_sources"][condition_id]["installed"]
    assert not state[f"{SRD}.state"]["has_web"]


def test_web_suppressed_actual_restraint_retires_after_real_movement_leaves_web():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _web_cast(inside=True)
    _turn(state, "guard")
    condition_id = state[f"{SRD}.entities"]["guard"]["last_web_save"]["condition_source_id"]
    _run(
        "install_srd_actor_spell_modifier",
        state,
        caster_state="other",
        target_state="guard",
        spell="freedom_of_movement",
        lasting_state="state",
        mode="freedom",
        save_dc=18,
        duration_rounds=600,
        concentration=False,
        actual_spell_level=4,
    )
    assert state[f"{SRD}.state"]["timed_condition_sources"][condition_id]["installed"]
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[54, 0, 0])
    assert _position(state, "guard")["position"] == [54, 0, 0]
    assert not state[f"{SRD}.state"]["timed_condition_sources"][condition_id]["installed"]


def test_web_actual_cast_path_filters_foreign_plane_solid_and_real_wall_still_blocks():
    state, data, parameters = _web_case(plane="ethereal")
    wall = deepcopy(state[f"{SRD}.state"]["spatial_scene"]["geometry"][0])
    wall.update(
        id="web-wall",
        volume={"kind": "box", "min": [10, -10, -1], "max": [11, 10, 20]},
        metadata={"plane": "material"},
    )
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(wall)
    before = deepcopy(state)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["plane"] == "ethereal"
    before[f"{SRD}.state"]["spatial_scene"]["geometry"][-1]["metadata"]["plane"] = "ethereal"
    _reject(before, "cast_spell_outside_combat", participant_data=data, **parameters)


def test_public_web_paid_cast_entry_escape_reopen_retry_and_source_removal(tmp_path, monkeypatch):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as public_casting

    def actual_fixture(_name):
        state, data, parameters = _web_case(inside=False)
        state[f"{SRD}.entities"]["guard"]["skill_check_modifiers"] = {"athletics": 100}
        return state, data, parameters

    monkeypatch.setattr(public_casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(public_casting, "_spell_case", actual_fixture)
    database = tmp_path / "web.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public_casting._create_spell_campaign(
            owner, "web", target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0}
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="web-cast",
            idempotency_key="web-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        first = StatelessMcpMechanics(owner).execute_action(command)
        assert first.status is ActionStatus.COMMITTED
        cast = owner.snapshot("casting")
        assert cast.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
        entered = replace(
            command,
            action_id="web-enter",
            idempotency_key="web-enter",
            expected_revision=cast.revision,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "guard", "destination": [40, 0, 0]},
        )
        moved = StatelessMcpMechanics(owner).execute_action(entered)
        assert moved.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        guard = saved.state[f"{SRD}.entities"]["guard"]
        assert guard["hp"] == 1000 and "restrained" in guard["conditions"]
        scene = saved.state[f"{SRD}.state"]["spatial_scene"]
        position = next(row["position"] for row in scene["entities"] if row["id"] == "guard")
        assert 29 <= position[0] <= 30 and guard["scene_movement_spent"] < 30
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(entered).result == moved.result
        assert mechanics.execute_action(command).result == first.result
        assert owner.snapshot("casting").state == saved.state
        escaped_command = replace(
            command,
            action_id="web-escape",
            idempotency_key="web-escape",
            expected_revision=saved.revision,
            tool_name="escape_srd_web",
            parameters={
                "actor_state": "guard",
                "source_id": guard["last_web_save"]["condition_source_id"],
            },
        )
        escaped = mechanics.execute_action(escaped_command)
        assert escaped.status is ActionStatus.COMMITTED
        freed = owner.snapshot("casting")
        assert "restrained" not in freed.state[f"{SRD}.entities"]["guard"]["conditions"]
        assert freed.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == freed.state
        assert mechanics.execute_action(escaped_command).result == escaped.result
        assert owner.snapshot("casting").state == freed.state
        ended = mechanics.execute_action(
            replace(
                command,
                action_id="web-end",
                idempotency_key="web-end",
                expected_revision=freed.revision,
                tool_name="end_concentration",
                parameters={"caster_state": "hero"},
            )
        )
        assert ended.status is ActionStatus.COMMITTED
        later = mechanics.execute_action(
            replace(
                entered,
                action_id="web-gone",
                idempotency_key="web-gone",
                expected_revision=owner.snapshot("casting").revision,
                parameters={"actor_state": "other", "destination": [35, 8, 0]},
            )
        )
        assert later.status is ActionStatus.COMMITTED
        assert later.result["context"]["_scene_movement_cost"] == 25
        assert (
            owner.snapshot("casting").state[f"{SRD}.entities"]["other"].get("last_web_save") is None
        )


def _fire_wall_case(*, slot=4, inside=False, success=False, side="positive", length=40):
    state, data, parameters = _fixture("wall_of_fire", origin=(40, 0, 0), slot=slot)
    _ground(state)
    parameters.update(
        environment_wall_axis="north_south",
        environment_fire_wall_length=length,
        environment_fire_wall_side=side,
    )
    for actor in ("guard", "other"):
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=0)
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["dexterity"] = (
            100 if success else -100
        )
    _position(state, "guard")["position"] = [40 if inside else 30, 0, 0]
    _position(state, "other")["position"] = [10, 8, 0]
    return state, data, parameters


def _fire_wall_cast(**choices):
    state, data, parameters = _fire_wall_case(**choices)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    return state, data, parameters


def test_wall_of_fire_actual_appearance_shared_roll_upcast_dexterity_half():
    state, data, parameters = _fire_wall_case(slot=6, inside=True)
    _position(state, "other")["position"] = [40, 8, 0]
    state[f"{SRD}.entities"]["other"]["saving_throw_modifiers"]["dexterity"] = 100
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    area = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert area["volume"] == {"kind": "box", "min": [39.5, -20, 0], "max": [40.5, 20, 20]}
    assert area["ground_id"] == "floor" and area["expires_tick"] == 20
    guard, other = (state[f"{SRD}.entities"][actor] for actor in ("guard", "other"))
    first, second = (actor["last_wall_of_fire_save"] for actor in (guard, other))
    assert first["raw_damage"] == second["raw_damage"] == area["initial_damage_roll"]
    assert first["raw_damage"]["count"] == 7 and first["raw_damage"]["sides"] == 8
    assert first["timing"] == second["timing"] == "initial"
    assert first["outcome"] == "failure" and second["outcome"] == "success"
    assert first["applied_damage"] == first["raw_damage"]["total"]
    assert second["applied_damage"] == first["raw_damage"]["total"] // 2


@pytest.mark.parametrize("invalid", ["air", "missing", "short_support", "bounds"])
def test_wall_of_fire_whole_supported_surface_rejects_paid_cast_atomically(invalid):
    state, data, parameters = _fire_wall_case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if invalid == "air":
        parameters["environment_origin"][2] = 1
    elif invalid == "missing":
        scene["geometry"] = []
    elif invalid == "short_support":
        scene["geometry"][0]["volume"]["max"][1] = 10
    else:
        scene["map"]["bounds"]["max"][2] = 10
        scene["map"]["layers"][0]["max_elevation"] = 10
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state == before


def test_wall_of_fire_real_entry_no_save_no_terrain_then_distinct_end_damage():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _fire_wall_cast(success=True)
    _turn(state, "guard")
    moved = _run("move_srd_scene_creature", state, actor_state="guard", destination=[45, 0, 0])
    assert _position(state, "guard")["position"] == [45, 0, 0]
    assert moved.result["context"]["_scene_movement_cost"] == 15
    first = deepcopy(state[f"{SRD}.entities"]["guard"]["last_wall_of_fire_save"])
    assert first["timing"] == "entry" and first["outcome"] == "no_save" and first["roll"] is None
    assert first["applied_damage"] == first["raw_damage"]["total"]
    before_end = state[f"{SRD}.entities"]["guard"]["hp"]
    _run("end_combat_turn", state)
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] < before_end and guard["last_wall_of_fire_save"]["timing"] == "end"
    before_start = deepcopy(guard["last_wall_of_fire_save"])
    _turn(state, "guard")
    assert state[f"{SRD}.entities"]["guard"]["last_wall_of_fire_save"] == before_start


@pytest.mark.parametrize(
    "position,side,eligible",
    [
        ([45, 0, 0], "positive", True),
        ([35, 0, 0], "positive", False),
        ([35, 0, 0], "negative", True),
        ([45, 0, 0], "negative", False),
        ([50.9, 0, 0], "positive", True),
        ([50.91, 0, 0], "positive", False),
        ([45, 35, 0], "positive", False),
        ([45, 0, 31], "positive", False),
    ],
)
def test_wall_of_fire_actual_hot_face_body_distance_and_cold_side(position, side, eligible):
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _fire_wall_cast(side=side)
    _position(state, "guard")["position"] = position
    _turn(state, "guard")
    _run("end_combat_turn", state)
    assert (state[f"{SRD}.entities"]["guard"]["hp"] < 1000) is eligible


def test_wall_of_fire_opacity_reads_current_wall_interior_plane_and_retirement():
    state, _, _ = _fire_wall_cast()
    for start, end, plane, opaque in (
        ([30, 0, 3], [50, 0, 3], "material", True),
        ([30, 0, 0], [50, 0, 0], "material", False),
        ([30, 0, 21], [50, 0, 21], "material", False),
        ([30, 0, 3], [50, 0, 3], "ethereal", False),
        ([30, 25, 3], [50, 25, 3], "material", False),
    ):
        result = _run("resolve_srd_environment_segment", state, start=start, end=end, plane=plane)
        assert result.result["context"]["_environment_sight_obscured"] is opaque
    _run("end_concentration", state, caster_state="hero")
    assert not state[f"{SRD}.state"]["has_wall_of_fire"]
    assert not _run(
        "resolve_srd_environment_segment", state, start=[30, 0, 3], end=[50, 0, 3], plane="material"
    ).result["context"]["_environment_sight_obscured"]


def test_wall_of_fire_appearance_does_not_consume_actual_later_entry_receipt():
    state, _, _ = _fire_wall_cast(inside=True, success=True)
    initial = state[f"{SRD}.entities"]["guard"]["last_wall_of_fire_save"]
    assert initial["timing"] == "initial" and initial["outcome"] == "success"
    before_entry = state[f"{SRD}.entities"]["guard"]["hp"]
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[38, 0, 0])
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[40, 0, 0])
    entered = deepcopy(state[f"{SRD}.entities"]["guard"]["last_wall_of_fire_save"])
    assert entered["timing"] == "entry" and entered["outcome"] == "no_save"
    assert state[f"{SRD}.entities"]["guard"]["hp"] == before_entry - entered["raw_damage"]["total"]
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[38, 0, 0])
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[40, 0, 0])
    assert state[f"{SRD}.entities"]["guard"]["last_wall_of_fire_save"] == entered


def test_wall_of_fire_default_cardinal_straight_wall_retains_actual_selected_length():
    state, data, parameters = _fire_wall_case(length=20)
    parameters.pop("environment_wall_axis")
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    area = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert area["wall_axis"] == "east_west" and area["wall_length"] == 20
    assert area["volume"] == {"kind": "box", "min": [30, -0.5, 0], "max": [50, 0.5, 20]}


def test_wall_of_fire_hot_side_uses_existing_actual_opaque_exposure():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, params = _fire_wall_case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    wall = deepcopy(scene["geometry"][0])
    wall.update(
        id="hot-side-stone", volume={"kind": "box", "min": [42, -50, -1], "max": [43, 50, 30]}
    )
    wall["metadata"]["ground"] = False
    scene["geometry"].append(wall)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    _position(state, "guard")["position"] = [45, 0, 0]
    _turn(state, "guard")
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000


@pytest.mark.parametrize("removal", ["expiry", "dispel"])
def test_wall_of_fire_actual_clock_and_dispel_retire_opacity_and_later_damage(removal):
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, _ = _fire_wall_cast()
    if removal == "expiry":
        _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    else:
        _run(
            "dispel_srd_environment_source",
            state,
            participant_data=data,
            caster_state="hero",
            caster="hero",
            area_id="hero",
            point=[40, 0, 3],
            slot_level=4,
            casting_modifier=4,
            casting_ability="intelligence",
        )
    assert not state[f"{SRD}.state"]["has_wall_of_fire"]
    _position(state, "guard")["position"] = [45, 0, 0]
    _turn(state, "guard")
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    assert not _run(
        "resolve_srd_environment_point", state, point=[40, 0, 3], plane="material"
    ).result["context"]["_environment_fog"]


def test_public_wall_of_fire_paid_cast_entry_end_reopen_retry_and_retirement(tmp_path, monkeypatch):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import MechanicsRejected, StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as public_casting

    monkeypatch.setattr(public_casting, "bundled_srd_archive", _woodland_test_archive)

    def actual_case(_name):
        state, data, params = _fire_wall_case(success=True)
        params["environment_origin"] = [15, 0, 0]
        _position(state, "guard")["position"] = [5, 0, 0]
        data["spellcasting_grants"][0]["spells"].append("misty_step")
        data["wizard_spellbook"].append("misty_step")
        state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("misty_step")
        scene = state[f"{SRD}.state"]["spatial_scene"]
        scene["zones"].append(
            {
                "id": "fire-wall-daylight",
                "map_id": scene["map"]["id"],
                "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
                "metadata": {"plane": "material", "lighting": "bright"},
            }
        )
        return state, data, params

    monkeypatch.setattr(public_casting, "_spell_case", actual_case)
    database = tmp_path / "wall-of-fire.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public_casting._create_spell_campaign(
            owner, "wall_of_fire", target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0}
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="fire-cast",
            idempotency_key="fire-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        mechanics = StatelessMcpMechanics(owner)
        first = mechanics.execute_action(command)
        assert first.status is ActionStatus.COMMITTED, first.result
        assert (
            owner.snapshot("casting").state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"]
            == 2
        )

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

        _, _, misty_parameters = _spell_case("misty_step")
        misty_parameters["travel_destination"] = [25, 0, 3]
        blocked_misty = replace(
            command,
            action_id="fire-blocked-misty",
            idempotency_key="fire-blocked-misty",
            expected_revision=owner.snapshot("casting").revision,
            parameters=misty_parameters,
        )
        before_blocked = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="actually see"):
            mechanics.execute_action(blocked_misty)
        assert owner.snapshot("casting").state == before_blocked.state
        assert owner.snapshot("casting").revision == before_blocked.revision

        started = act(
            "fire-combat", "start_group_combat_encounter", combatants=["hero", "guard", "other"]
        )
        assert started.status is ActionStatus.COMMITTED, started.result
        for i in range(3):
            current = owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"]
            begun = act(f"fire-begin-{i}", "begin_combat_turn")
            assert begun.status is ActionStatus.COMMITTED, begun.result
            if current == "guard":
                break
            assert act(f"fire-end-{i}", "end_combat_turn").status is ActionStatus.COMMITTED
            assert (
                act(f"fire-advance-{i}", "advance_group_combat_encounter_turn").status
                is ActionStatus.COMMITTED
            )
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "guard"
        entered = replace(
            command,
            action_id="fire-enter",
            idempotency_key="fire-enter",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "guard", "destination": [20, 0, 0]},
        )
        moved = mechanics.execute_action(entered)
        assert moved.status is ActionStatus.COMMITTED, moved.result
        assert moved.result["context"]["_scene_movement_cost"] == 15
        saved = owner.snapshot("casting")
        guard = saved.state[f"{SRD}.entities"]["guard"]
        packet = guard["last_wall_of_fire_save"]
        assert packet["timing"] == "entry" and packet["roll"] is None
        assert guard["hp"] == 1000 - packet["raw_damage"]["total"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(command).result == first.result
        assert mechanics.execute_action(entered).result == moved.result
        assert owner.snapshot("casting").state == saved.state
        ended_command = replace(
            command,
            action_id="fire-target-end",
            idempotency_key="fire-target-end",
            expected_revision=saved.revision,
            tool_name="end_combat_turn",
            parameters={},
        )
        ended = mechanics.execute_action(ended_command)
        assert ended.status is ActionStatus.COMMITTED, ended.result
        after_end = owner.snapshot("casting")
        later_guard = after_end.state[f"{SRD}.entities"]["guard"]
        assert later_guard["last_wall_of_fire_save"]["timing"] == "end"
        assert later_guard["hp"] < guard["hp"]
        removed = mechanics.execute_action(
            replace(
                command,
                action_id="fire-source-end",
                idempotency_key="fire-source-end",
                expected_revision=after_end.revision,
                tool_name="end_concentration",
                parameters={"caster_state": "hero"},
            )
        )
        assert removed.status is ActionStatus.COMMITTED, removed.result
        final = owner.snapshot("casting")
        assert not final.state[f"{SRD}.state"]["has_wall_of_fire"]
        assert final.state[f"{SRD}.entities"]["guard"]["hp"] == later_guard["hp"]
        assert owner.execute_action(ended_command).result == ended.result
        assert owner.snapshot("casting").state == final.state

    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == final.state
        finished = mechanics.execute_action(
            replace(
                command,
                action_id="fire-encounter-finish",
                idempotency_key="fire-encounter-finish",
                expected_revision=final.revision,
                tool_name="end_combat_encounter",
                parameters={"end_reason": "fighting_stopped"},
            )
        )
        assert finished.status is ActionStatus.COMMITTED, finished.result
        misty = replace(
            blocked_misty,
            action_id="fire-cleared-misty",
            idempotency_key="fire-cleared-misty",
            expected_revision=owner.snapshot("casting").revision,
        )
        teleported = mechanics.execute_action(misty)
        assert teleported.status is ActionStatus.COMMITTED, teleported.result
        cleared = owner.snapshot("casting")
        assert _position(cleared.state, "hero")["position"] == [25, 0, 3]
        assert cleared.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
        assert mechanics.execute_action(command).result == first.result
        assert mechanics.execute_action(misty).result == teleported.result
        assert owner.snapshot("casting").state == cleared.state


def _blade_case(*, slot=6, inside=True, success=False, axis="north_south", length=40, top=0):
    state, data, parameters = _fixture("blade_barrier", origin=(40, 0, top), slot=slot)
    parameters.update(environment_wall_axis=axis, environment_wall_length=length)
    for actor in ("guard", "other"):
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=0)
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["dexterity"] = (
            100 if success else -100
        )
    _position(state, "guard")["position"] = [40 if inside else 30, 0, top]
    _position(state, "other")["position"] = [10, 8, 0]
    return state, data, parameters


def _blade_cast(**choices):
    state, data, parameters = _blade_case(**choices)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    return state, data, parameters


@pytest.mark.parametrize("success", [False, True])
def test_blade_barrier_actual_initial_dexterity_save_fixed_force_packet_and_wall(success):
    state, _, _ = _blade_cast(slot=9, success=success)
    area = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert area["volume"] == {"kind": "box", "min": [37.5, -20, 0], "max": [42.5, 20, 20]}
    assert area["expires_tick"] == 200 and area["wall_length"] == 40
    guard = state[f"{SRD}.entities"]["guard"]
    saved = guard["last_blade_barrier_save"]
    assert saved["raw_damage"]["count"] == 6 and saved["raw_damage"]["sides"] == 10
    assert saved["outcome"] == ("success" if success else "failure")
    damage = saved["raw_damage"]["total"] // (2 if success else 1)
    assert saved["applied_damage"] == damage and guard["hp"] == 1000 - damage
    assert not guard["conditions"]
    assert state[f"{SRD}.entities"]["other"].get("last_blade_barrier_save") is None


def test_blade_barrier_actual_wall_space_is_difficult_and_entry_saves_once_without_stopping():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _blade_cast(inside=False)
    _turn(state, "guard")
    moved = _run("move_srd_scene_creature", state, actor_state="guard", destination=[45, 0, 0])
    assert _position(state, "guard")["position"] == [45, 0, 0]
    assert moved.result["context"]["_scene_movement_cost"] == pytest.approx(20)
    guard = state[f"{SRD}.entities"]["guard"]
    saved = deepcopy(guard["last_blade_barrier_save"])
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[40, 0, 0])
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["last_blade_barrier_save"] == saved
    _turn(state, "guard")
    assert state[f"{SRD}.entities"]["guard"]["last_blade_barrier_save"] == saved
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["hp"] < guard["hp"]


def test_blade_barrier_actual_airborne_wall_uses_height_and_has_no_ground_requirement():
    state, _, _ = _blade_cast(top=30, axis="east_west", length=20)
    area = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert area["volume"]["min"] == [30, -2.5, 30]
    assert area["volume"]["max"] == [50, 2.5, 50]
    assert state[f"{SRD}.entities"]["guard"]["hp"] < 1000
    _position(state, "other")["position"] = [40, 0, 0]
    _run("resolve_srd_blade_barrier_event", state, target_state="other", area_id="hero")
    assert state[f"{SRD}.entities"]["other"]["hp"] == 1000


@pytest.mark.parametrize("removal", ["end_concentration", "expiry", "dispel"])
def test_blade_barrier_actual_source_removal_clears_later_damage_and_terrain(removal):
    state, data, _ = _blade_cast(inside=False)
    if removal == "expiry":
        _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    elif removal == "dispel":
        _run(
            "dispel_srd_environment_source",
            state,
            participant_data=data,
            caster_state="hero",
            caster="hero",
            area_id="hero",
            point=[40, 0, 10],
            slot_level=6,
            casting_modifier=4,
            casting_ability="wisdom",
        )
    else:
        _run("end_concentration", state, caster_state="hero")
    assert not state[f"{SRD}.state"]["has_blade_barrier"]
    moved = _run("move_srd_scene_creature", state, actor_state="guard", destination=[45, 0, 0])
    assert moved.result["context"]["_scene_movement_cost"] == 15
    assert state[f"{SRD}.entities"]["guard"].get("last_blade_barrier_save") is None


def test_blade_barrier_actual_cover_query_reads_current_source_plane_and_volume():
    state, _, _ = _blade_cast(inside=False)
    _run("record_srd_scene_sight_origin", state, observer_state="hero", offset=[0, 0, 3])
    _position(state, "guard")["position"] = [50, 0, 0]
    result = _run(
        "resolve_srd_environment_blade_cover",
        state,
        observer_subject="hero",
        target_subject="guard",
    )
    assert result.result["context"]["_environment_blade_cover"] == "three_quarters"
    _position(state, "guard")["metadata"]["plane"] = "ethereal"
    assert (
        _run(
            "resolve_srd_environment_blade_cover",
            state,
            observer_subject="hero",
            target_subject="guard",
        ).result["context"]["_environment_blade_cover"]
        == "none"
    )
    _position(state, "guard")["metadata"]["plane"] = "material"
    _run("end_concentration", state, caster_state="hero")
    assert (
        _run(
            "resolve_srd_environment_blade_cover",
            state,
            observer_subject="hero",
            target_subject="guard",
        ).result["context"]["_environment_blade_cover"]
        == "none"
    )


def _blade_body_cover_case():
    state, data, parameters = _blade_case(inside=False)
    data["classes"] = [{"id": "cleric", "level": 14}, {"id": "wizard", "level": 3}]
    data["abilities"].update(wisdom=18, intelligence=18)
    data["spellcasting_grants"] = [
        {"source": "cleric", "ability": "wisdom", "spells": ["blade_barrier"]},
        {"source": "wizard", "ability": "intelligence", "spells": ["fire_bolt"]},
    ]
    data["wizard_spellbook"] = []
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("fire_bolt")
    state[f"{SRD}.entities"]["guard"].update(armor_class=15, hp=1000, max_hp=1000)
    _position(state, "guard")["position"] = [60, 0, 0]
    _run("record_srd_scene_sight_origin", state, observer_state="hero", offset=[0, 0, 3])
    return state, data, parameters


def _blade_body_cover_cast():
    state, data, parameters = _blade_body_cover_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    return state, data, parameters


@pytest.mark.parametrize("cover,expected", [("none", 20), ("half", 20), ("three_quarters", 20)])
def test_blade_actual_body_cover_inserts_one_printed_attack_bonus_without_summing(cover, expected):
    state, data, _ = _blade_body_cover_cast()
    _, _, attack = _spell_case("fire_bolt")
    attack.update(target_distance=60, cover=cover)
    result = _run("cast_spell_outside_combat", state, participant_data=data, **attack)
    assert result.result["status"] == "committed"
    assert result.result["context"]["_spell_target_armor_class"] == expected
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2


def test_blade_actual_body_cover_keeps_physical_total_cover_and_partial_model_explicit():
    state, data, _ = _blade_body_cover_cast()
    _, _, attack = _spell_case("fire_bolt")
    attack.update(target_distance=60, cover="total")
    _reject(state, "cast_spell_outside_combat", participant_data=data, **attack)
    _position(state, "guard")["height"] = 40
    result = _run(
        "resolve_srd_environment_blade_cover",
        state,
        observer_subject="hero",
        target_subject="guard",
    )
    assert result.result["context"]["_environment_blade_cover_supported"] is False


def test_blade_actual_area_source_frame_preserves_total_and_upgrades_half_without_summing():
    state, _, _ = _blade_body_cover_cast()
    _position(state, "guard")["metadata"]["cover_from"] = {"hero": "half"}
    _position(state, "other")["position"] = [65, 5, 0]
    _position(state, "other")["metadata"]["cover_from"] = {"hero": "total"}
    result = _run(
        "collect_srd_spell_area_cover_targets",
        state,
        source_state="hero",
        targets=["guard", "other"],
        origin=[0, 0, 3],
    )
    context = result.result["context"]
    assert context["_srd_area_targets"] == ["guard"]
    assert context["_srd_area_three_quarters"] == ["guard"]
    assert context["_srd_area_half_cover"] == []


@pytest.mark.parametrize("ability,expected_bonus", [("dexterity", 5), ("constitution", 0)])
def test_blade_actual_area_cover_reaches_existing_native_save_owner(ability, expected_bonus):
    state, _, _ = _blade_body_cover_cast()
    collected = _run(
        "collect_srd_spell_area_cover_targets",
        state,
        source_state="hero",
        targets=["guard"],
        origin=[0, 0, 3],
    ).result["context"]
    result = _run(
        "resolve_spell_area_save_damage_target",
        state,
        spell="fireball",
        target=collected["_srd_area_targets"][0],
        save_ability=ability,
        spell_save_dc=18,
        damage_total=20,
        damage_type="fire",
        success_damage="half",
        construct_save_disadvantage=False,
        half_cover_targets=collected["_srd_area_half_cover"],
        three_quarters_cover_targets=collected["_srd_area_three_quarters"],
        source_state_id="hero",
    )
    context = result.result["context"]
    assert context["_spell_save_cover_bonus"] == expected_bonus
    assert context["_card_d20_modifier"] == (
        state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"][ability] + expected_bonus
    )


def test_blade_actual_body_cover_excludes_own_wall_save_and_actual_same_side_targets():
    state, _, _ = _blade_cast()
    saved = state[f"{SRD}.entities"]["guard"]["last_blade_barrier_save"]
    assert saved["roll"]["modifier"] == -100
    _run("record_srd_scene_sight_origin", state, observer_state="hero", offset=[0, 0, 3])
    _position(state, "guard")["position"] = [20, 0, 0]
    same_side = _run(
        "resolve_srd_environment_blade_cover",
        state,
        observer_subject="hero",
        target_subject="guard",
    ).result["context"]
    assert same_side["_environment_blade_cover"] == "none"
    assert same_side["_environment_blade_cover_supported"]


def test_public_blade_body_cover_paid_source_later_attack_reopen_retry_and_retirement(
    tmp_path, monkeypatch
):
    from dataclasses import replace
    from random import Random

    from sylvia_gm import dice
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as public_casting

    monkeypatch.setattr(dice, "_rng", Random(5))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _name: _blade_body_cover_case())
    database = tmp_path / "blade-body-cover.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public_casting._create_spell_campaign(
            owner, "blade_barrier", target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0}
        )
        cast_command = ActionCommand(
            campaign_id="casting",
            action_id="blade-cover-cast",
            idempotency_key="blade-cover-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        mechanics = StatelessMcpMechanics(owner)
        cast = mechanics.execute_action(cast_command)
        assert cast.status is ActionStatus.COMMITTED
        after_cast = owner.snapshot("casting")
        _, _, attack = _spell_case("fire_bolt")
        attack.update(target_distance=60, cover="half")
        attack_command = replace(
            cast_command,
            action_id="blade-cover-attack",
            idempotency_key="blade-cover-attack",
            expected_revision=after_cast.revision,
            parameters=attack,
        )
        hit = mechanics.execute_action(attack_command)
        assert hit.status is ActionStatus.COMMITTED
        assert hit.result["context"]["_spell_target_armor_class"] == 20
        assert hit.result["context"]["_spell_attack_outcome"] == "success"
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["guard"]["hp"] < 1000
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(cast_command).result == cast.result
        assert mechanics.execute_action(attack_command).result == hit.result
        assert owner.snapshot("casting").state == saved.state
        ended = mechanics.execute_action(
            replace(
                cast_command,
                action_id="blade-cover-end",
                idempotency_key="blade-cover-end",
                expected_revision=saved.revision,
                tool_name="end_concentration",
                parameters={"caster_state": "hero"},
            )
        )
        assert ended.status is ActionStatus.COMMITTED
        removed = owner.snapshot("casting")
        later = mechanics.execute_action(
            replace(
                attack_command,
                action_id="blade-cover-gone",
                idempotency_key="blade-cover-gone",
                expected_revision=removed.revision,
            )
        )
        assert later.status is ActionStatus.COMMITTED
        assert later.result["context"]["_spell_target_armor_class"] == 17


def test_blade_barrier_actual_whole_wall_bounds_reject_before_costs():
    state, data, parameters = _blade_case()
    state[f"{SRD}.state"]["spatial_scene"]["map"]["bounds"]["max"][1] = 100
    parameters["environment_origin"] = [40, 80, 0]
    parameters["environment_wall_length"] = 100
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state == before


def test_public_blade_barrier_paid_cast_entry_reopen_retry_then_real_source_removal(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as public_casting

    monkeypatch.setattr(public_casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _name: _blade_case(inside=False))
    database = tmp_path / "blade-barrier.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public_casting._create_spell_campaign(
            owner, "blade_barrier", target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0}
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="blade-cast",
            idempotency_key="blade-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        first = StatelessMcpMechanics(owner).execute_action(command)
        assert first.status is ActionStatus.COMMITTED
        cast = owner.snapshot("casting")
        assert cast.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
        entered = replace(
            command,
            action_id="blade-enter",
            idempotency_key="blade-enter",
            expected_revision=cast.revision,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "guard", "destination": [45, 0, 0]},
        )
        moved = StatelessMcpMechanics(owner).execute_action(entered)
        assert moved.status is ActionStatus.COMMITTED
        assert moved.result["context"]["_scene_movement_cost"] == 20
        saved = owner.snapshot("casting")
        guard = saved.state[f"{SRD}.entities"]["guard"]
        assert guard["hp"] < 1000 and guard["last_blade_barrier_save"]["raw_damage"]["count"] == 6
        assert not guard["conditions"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(command).result == first.result
        assert mechanics.execute_action(entered).result == moved.result
        assert owner.snapshot("casting").state == saved.state
        returned = mechanics.execute_action(
            replace(
                entered,
                action_id="blade-return",
                idempotency_key="blade-return",
                expected_revision=saved.revision,
                parameters={"actor_state": "guard", "destination": [40, 0, 0]},
            )
        )
        assert returned.status is ActionStatus.COMMITTED
        before_end = owner.snapshot("casting")
        assert before_end.state[f"{SRD}.entities"]["guard"]["hp"] == guard["hp"]
        ended = mechanics.execute_action(
            replace(
                command,
                action_id="blade-end",
                idempotency_key="blade-end",
                expected_revision=before_end.revision,
                tool_name="end_concentration",
                parameters={"caster_state": "hero"},
            )
        )
        assert ended.status is ActionStatus.COMMITTED
        removed = owner.snapshot("casting")
        assert not removed.state[f"{SRD}.state"]["has_blade_barrier"]
        later = mechanics.execute_action(
            replace(
                entered,
                action_id="blade-gone",
                idempotency_key="blade-gone",
                expected_revision=removed.revision,
                parameters={"actor_state": "guard", "destination": [38, 0, 0]},
            )
        )
        assert later.status is ActionStatus.COMMITTED
        assert later.result["context"]["_scene_movement_cost"] == 2
        assert owner.snapshot("casting").state[f"{SRD}.entities"]["guard"]["hp"] == guard["hp"]


def _tentacles_case(*, slot=4, inside=True, success=False, origin=(40, 0, 0)):
    state, data, parameters = _fixture("black_tentacles", origin=origin, slot=slot)
    _ground(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"].append(
        {
            "id": "tentacles-daylight",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
            "metadata": {"plane": "material", "lighting": "bright"},
        }
    )
    for actor in ("guard", "other"):
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=0)
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["strength"] = (
            100 if success else -100
        )
    _position(state, "guard")["position"] = [40 if inside else 10, 0, 0]
    _position(state, "other")["position"] = [10, 8, 0]
    return state, data, parameters


def _tentacles_cast(**choices):
    state, data, parameters = _tentacles_case(**choices)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    return state, data, parameters


@pytest.mark.parametrize("success", [False, True])
def test_black_tentacles_actual_initial_strength_save_only_failure_damages_and_restrains(success):
    state, _, _ = _tentacles_cast(slot=6, success=success)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    guard = state[f"{SRD}.entities"]["guard"]
    saved = guard["last_black_tentacles_save"]
    assert saved["outcome"] == ("success" if success else "failure")
    assert ("restrained" in guard["conditions"]) is (not success)
    assert source["volume"]["min"] == [30, -10, 0]
    assert source["expires_tick"] == 20 and source["spell_level"] == 6
    if success:
        assert saved["raw_damage"] is None and guard["hp"] == 1000
    else:
        assert saved["raw_damage"]["count"] == 3 and saved["raw_damage"]["sides"] == 6
        assert guard["hp"] == 1000 - saved["applied_damage"]
        condition = state[f"{SRD}.state"]["timed_condition_sources"][saved["condition_source_id"]]
        assert condition["expires_tick"] == source["expires_tick"]
        assert condition["environment_sequence"] == source["sequence"]
    assert state[f"{SRD}.entities"]["other"].get("last_black_tentacles_save") is None


def test_black_tentacles_actual_restrained_immunity_still_takes_failed_save_damage():
    state, data, parameters = _tentacles_case()
    state[f"{SRD}.entities"]["guard"]["condition_immunities"] = ["restrained"]
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] < 1000 and "restrained" not in guard["conditions"]
    assert guard["last_black_tentacles_save"]["condition_source_id"] is None


def test_black_tentacles_actual_first_entry_commits_and_stops_on_new_restraint():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _tentacles_cast(inside=False)
    _turn(state, "guard")
    moved = _run("move_srd_scene_creature", state, actor_state="guard", destination=[40, 0, 0])
    assert moved.result["status"] == "committed"
    assert 29 <= _position(state, "guard")["position"][0] <= 30
    assert state[f"{SRD}.combat"]["movement_spent"] < 30
    saved = deepcopy(state[f"{SRD}.entities"]["guard"]["last_black_tentacles_save"])
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["last_black_tentacles_save"] == saved
    _turn(state, "guard")
    before = state[f"{SRD}.entities"]["guard"]["hp"]
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["hp"] < before
    assert len(state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["condition_source_ids"]) == 1


def test_black_tentacles_successful_entry_spends_actual_difficult_ground_and_has_no_start_save():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _tentacles_cast(inside=False, success=True)
    _turn(state, "guard")
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[35, 0, 0])
    assert state[f"{SRD}.combat"]["movement_spent"] == 30
    saved = deepcopy(state[f"{SRD}.entities"]["guard"]["last_black_tentacles_save"])
    _run("end_combat_turn", state)
    _turn(state, "guard")
    assert state[f"{SRD}.entities"]["guard"]["last_black_tentacles_save"] == saved


def test_black_tentacles_actual_caster_occupant_lethal_packet_does_not_install_orphan_restraint():
    state, data, parameters = _tentacles_case(inside=False, origin=(0, 0, 0))
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(hp=1, temp_hp=0)
    hero.setdefault("saving_throw_modifiers", {})["strength"] = -100
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["hp"] == 0 and hero["last_black_tentacles_save"]["condition_source_id"] is None
    assert not state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]


def test_black_tentacles_actual_dying_living_character_keeps_source_restraint():
    state, data, parameters = _tentacles_case()
    state[f"{SRD}.entities"]["guard"].update(creature_kind="character", hp=1)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 0 and guard["is_alive"]
    assert "restrained" in guard["conditions"]
    assert guard["last_black_tentacles_save"]["condition_source_id"] is not None


def test_black_tentacles_real_athletics_escape_action_retires_only_selected_condition():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _tentacles_cast()
    source_id = state[f"{SRD}.entities"]["guard"]["last_black_tentacles_save"][
        "condition_source_id"
    ]
    state[f"{SRD}.entities"]["guard"]["skill_check_modifiers"] = {"athletics": 100}
    _turn(state, "guard")
    _run("escape_srd_black_tentacles", state, actor_state="guard", source_id=source_id)
    assert not state[f"{SRD}.combat"]["action_available"]
    assert not state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"]
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]


@pytest.mark.parametrize("removal", ["end_concentration", "expiry", "dispel"])
def test_black_tentacles_actual_source_removal_preserves_independent_restraint(removal):
    state, data, parameters = _tentacles_case()
    _run("apply_srd_condition", state, target="guard", condition="restrained")
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    condition_id = state[f"{SRD}.entities"]["guard"]["last_black_tentacles_save"][
        "condition_source_id"
    ]
    if removal == "expiry":
        _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    elif removal == "dispel":
        _run(
            "dispel_srd_environment_source",
            state,
            participant_data=data,
            caster_state="hero",
            caster="hero",
            area_id="hero",
            point=[40, 0, 0],
            slot_level=4,
            casting_modifier=4,
            casting_ability="wisdom",
        )
    else:
        _run("end_concentration", state, caster_state="hero")
    assert not state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    assert not state[f"{SRD}.state"]["timed_condition_sources"][condition_id]["installed"]
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert not any(
        row.get("metadata", {}).get("source_environment_id") == "hero"
        for row in state[f"{SRD}.state"]["spatial_scene"]["zones"]
    )


def test_black_tentacles_successful_later_save_preserves_restraint_then_escape_can_rebind():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _tentacles_cast()
    condition_id = state[f"{SRD}.entities"]["guard"]["last_black_tentacles_save"][
        "condition_source_id"
    ]
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["strength"] = 100
    _turn(state, "guard")
    _run("end_combat_turn", state)
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.state"]["timed_condition_sources"][condition_id]["installed"]
    _turn(state, "guard")
    state[f"{SRD}.entities"]["guard"]["skill_check_modifiers"] = {"athletics": 100}
    _run("escape_srd_black_tentacles", state, actor_state="guard", source_id=condition_id)
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["strength"] = -100
    _run("end_combat_turn", state)
    rebound = state[f"{SRD}.entities"]["guard"]["last_black_tentacles_save"]["condition_source_id"]
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.state"]["timed_condition_sources"][rebound]["installed"]


@pytest.mark.parametrize("protection", ["freedom", "plane", "airborne"])
def test_black_tentacles_actual_freedom_plane_and_ground_body_control_later_entry(protection):
    state, _, _ = _tentacles_cast(inside=False)
    if protection == "freedom":
        _run(
            "install_srd_actor_spell_modifier",
            state,
            caster_state="other",
            target_state="guard",
            spell="freedom_of_movement",
            lasting_state="state",
            mode="freedom",
            save_dc=18,
            duration_rounds=600,
            concentration=False,
            actual_spell_level=4,
        )
    elif protection == "plane":
        _position(state, "guard")["metadata"]["plane"] = "ethereal"
    else:
        _position(state, "guard")["position"][2] = 5
        _position(state, "guard")["metadata"]["flying"] = True
        state[f"{SRD}.entities"]["guard"]["flying_speed"] = 30
    destination = [35, 0, 5 if protection == "airborne" else 0]
    moved = _run("move_srd_scene_creature", state, actor_state="guard", destination=destination)
    guard = state[f"{SRD}.entities"]["guard"]
    assert _position(state, "guard")["position"] == destination
    assert "restrained" not in guard["conditions"]
    assert moved.result["context"]["_scene_movement_cost"] == 25
    assert (guard["hp"] < 1000) is (protection == "freedom")
    if protection == "freedom":
        assert guard["last_black_tentacles_save"]["condition_source_id"] is None
    else:
        assert guard.get("last_black_tentacles_save") is None


def test_black_tentacles_actual_ground_visibility_rejects_before_costs():
    state, data, parameters = _tentacles_case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    wall = deepcopy(scene["geometry"][0])
    wall.update(
        id="tentacles-wall",
        volume={"kind": "box", "min": [20, -20, -1], "max": [21, 20, 20]},
        metadata={"plane": "material"},
    )
    scene["geometry"].append(wall)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state == before


def test_public_black_tentacles_paid_cast_entry_escape_reopen_retry_and_source_removal(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as public_casting

    def actual_fixture(_name):
        state, data, parameters = _tentacles_case(inside=False)
        state[f"{SRD}.entities"]["guard"]["skill_check_modifiers"] = {"athletics": 100}
        return state, data, parameters

    monkeypatch.setattr(public_casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(public_casting, "_spell_case", actual_fixture)
    database = tmp_path / "black-tentacles.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public_casting._create_spell_campaign(
            owner, "black_tentacles", target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0}
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="tentacles-cast",
            idempotency_key="tentacles-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        first = StatelessMcpMechanics(owner).execute_action(command)
        assert first.status is ActionStatus.COMMITTED
        cast = owner.snapshot("casting")
        assert cast.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
        entered = replace(
            command,
            action_id="tentacles-enter",
            idempotency_key="tentacles-enter",
            expected_revision=cast.revision,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "guard", "destination": [40, 0, 0]},
        )
        moved = StatelessMcpMechanics(owner).execute_action(entered)
        assert moved.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        guard = saved.state[f"{SRD}.entities"]["guard"]
        assert guard["hp"] < 1000 and "restrained" in guard["conditions"]
        scene = saved.state[f"{SRD}.state"]["spatial_scene"]
        position = next(row["position"] for row in scene["entities"] if row["id"] == "guard")
        assert 29 <= position[0] <= 30 and guard["scene_movement_spent"] < 30
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(entered).result == moved.result
        assert mechanics.execute_action(command).result == first.result
        assert owner.snapshot("casting").state == saved.state
        escaped_command = replace(
            command,
            action_id="tentacles-escape",
            idempotency_key="tentacles-escape",
            expected_revision=saved.revision,
            tool_name="escape_srd_black_tentacles",
            parameters={
                "actor_state": "guard",
                "source_id": guard["last_black_tentacles_save"]["condition_source_id"],
            },
        )
        escaped = mechanics.execute_action(escaped_command)
        assert escaped.status is ActionStatus.COMMITTED
        freed = owner.snapshot("casting")
        assert "restrained" not in freed.state[f"{SRD}.entities"]["guard"]["conditions"]
        assert freed.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == freed.state
        assert mechanics.execute_action(escaped_command).result == escaped.result
        assert owner.snapshot("casting").state == freed.state
        ended = mechanics.execute_action(
            replace(
                command,
                action_id="tentacles-end",
                idempotency_key="tentacles-end",
                expected_revision=freed.revision,
                tool_name="end_concentration",
                parameters={"caster_state": "hero"},
            )
        )
        assert ended.status is ActionStatus.COMMITTED
        later = mechanics.execute_action(
            replace(
                entered,
                action_id="tentacles-gone",
                idempotency_key="tentacles-gone",
                expected_revision=owner.snapshot("casting").revision,
                parameters={"actor_state": "other", "destination": [35, 8, 0]},
            )
        )
        assert later.status is ActionStatus.COMMITTED
        assert later.result["context"]["_scene_movement_cost"] == 25
        assert (
            owner.snapshot("casting")
            .state[f"{SRD}.entities"]["other"]
            .get("last_black_tentacles_save")
            is None
        )


def _sleet_case(*, slot=3, inside=False, success=False, top=False):
    state, data, parameters = _fixture("sleet_storm", origin=(40, 0, 40 if top else 0), slot=slot)
    _ground(state)
    parameters["environment_cylinder_face"] = "top" if top else "bottom"
    for actor in ("guard", "other"):
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["dexterity"] = (
            100 if success else -100
        )
    _position(state, "guard")["position"] = [40 if inside else 10, 0, 0]
    _position(state, "other")["position"] = [10, 8, 0]
    return state, data, parameters


def _sleet_cast(**choices):
    state, data, parameters = _sleet_case(**choices)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    return state, data, parameters


@pytest.mark.parametrize("top", [False, True])
def test_sleet_actual_fixed_cylinder_has_no_appearance_save_or_upcast_change(top):
    state, _, _ = _sleet_cast(slot=6, inside=True, top=top)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["height"] == 40 and source["radius"] == 20
    assert source["cylinder_min"] == 0 and source["cylinder_max"] == 40
    assert source["expires_tick"] == 20 and source["volume"]["kind"] == "cylinder"
    assert state[f"{SRD}.state"]["has_sleet_storm"]
    assert state[f"{SRD}.entities"]["guard"].get("last_sleet_storm_save") is None
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2


@pytest.mark.parametrize("success", [False, True])
@pytest.mark.parametrize("immune", [False, True])
def test_sleet_actual_start_dexterity_save_prone_immunity_and_exact_concentration_loss(
    success, immune
):
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _sleet_cast(inside=True, success=success)
    _run(
        "cast_spell_environment",
        state,
        caster_state="guard",
        spell="fog_cloud",
        slot_level=1,
        concentration_save_modifier=0,
        origin=[90, 0, 0],
    )
    if immune:
        state[f"{SRD}.entities"]["guard"]["condition_immunities"] = ["prone"]
    hp = state[f"{SRD}.entities"]["guard"]["hp"]
    _turn(state, "guard")
    guard = state[f"{SRD}.entities"]["guard"]
    saved = deepcopy(guard["last_sleet_storm_save"])
    assert saved["outcome"] == ("success" if success else "failure")
    assert saved["lost_concentration"] is (not success)
    assert ("prone" in guard["conditions"]) is (not success and not immune)
    assert (guard["concentration_spell_id"] == "fog_cloud") is success
    assert guard["hp"] == hp
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    _run("resolve_srd_environment_hazard_start_turn", state, target_state="guard")
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["last_sleet_storm_save"] == saved


def test_sleet_real_entry_save_and_actual_ground_cost_union_then_source_removal():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _sleet_cast(success=True)
    _turn(state, "guard")
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[25, 0, 0])
    saved = deepcopy(state[f"{SRD}.entities"]["guard"]["last_sleet_storm_save"])
    assert saved["outcome"] == "success"
    assert state[f"{SRD}.combat"]["movement_spent"] == 20
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["last_sleet_storm_save"] == saved
    _run("end_concentration", state, caster_state="hero")
    assert not state[f"{SRD}.state"]["has_sleet_storm"]
    _turn(state, "guard")
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    assert state[f"{SRD}.combat"]["movement_spent"] == 5


@pytest.mark.parametrize(
    "point,inside", [([40, 0, 39], True), ([40, 0, 40.1], False), ([61, 0, 10], False)]
)
def test_sleet_actual_cylinder_obscuration_respects_height_radius_and_plane(point, inside):
    state, _, _ = _sleet_cast()
    result = _run("resolve_srd_environment_point", state, point=point, plane="material")
    assert result.result["context"]["_environment_fog"] is inside
    result = _run("resolve_srd_environment_point", state, point=point, plane="ethereal")
    assert result.result["context"]["_environment_fog"] is False


def test_sleet_actual_start_and_first_reentry_have_distinct_turn_receipts(monkeypatch):
    from tests.srd_full.test_srd_spell_damage_consumers import _record_rolls
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _sleet_cast(inside=True, success=True)
    _position(state, "guard")["position"] = [21, 0, 0]
    state[f"{SRD}.entities"]["guard"]["speed"] = 100
    rolls = _record_rolls(monkeypatch)
    _turn(state, "guard")
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[10, 0, 0])
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[22, 0, 0])
    assert len([r for r in rolls if r.sides == 20]) == 2
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[10, 0, 0])
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[22, 0, 0])
    _run("end_combat_turn", state)
    assert len([r for r in rolls if r.sides == 20]) == 2


def _sleet_flame(state, identity, position, *, exposed=True, plane="material", holder=None):
    flame = deepcopy(_position(state, "guard"))
    flame.update(
        id=identity, position=list(position), height=1, movement="passable", visibility="clear"
    )
    flame["footprint"] = {"kind": "box", "half_extents": [0.4, 0.4]}
    flame["metadata"].update(participant_ref=None, is_object=True, plane=plane)
    if holder:
        flame["metadata"]["carried_by"] = holder
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(flame)
    _run("record_srd_ordinary_flame", state, object_id=identity, exposed=exposed)


def test_sleet_actual_exposed_flames_doused_but_covered_outside_and_other_plane_preserved():
    state, data, parameters = _sleet_case()
    _sleet_flame(state, "fire", [40, 0, 1])
    _sleet_flame(state, "covered", [40, 3, 1], exposed=False)
    _sleet_flame(state, "outside", [65, 0, 1])
    _sleet_flame(state, "foreign", [40, 5, 1], plane="ethereal")
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    rows = {r["id"]: r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"]}
    assert rows["fire"]["metadata"]["srd_ordinary_flame"]["burning"] is False
    assert all(
        rows[i]["metadata"]["srd_ordinary_flame"]["burning"]
        for i in ("covered", "outside", "foreign")
    )
    assert (
        rows["fire"]["metadata"]["is_object"]
        and rows["fire"]["metadata"]["participant_ref"] is None
    )


def test_sleet_current_holder_geometry_controls_flame_then_actual_holder_walk_douses():
    state, data, parameters = _sleet_case(success=True)
    _sleet_flame(state, "torch", [40, 0, 1], holder="guard")
    _position(state, "torch")["footprint"]["half_extents"] = [0.1, 0.1]
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert _position(state, "torch")["metadata"]["srd_ordinary_flame"]["burning"] is True
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[25, 0, 0])
    assert _position(state, "torch")["metadata"]["srd_ordinary_flame"]["burning"] is False
    assert _position(state, "torch")["metadata"]["carried_by"] == "guard"


def test_sleet_partly_overlapped_flame_section_rejects_paid_cast_atomically():
    state, data, parameters = _sleet_case()
    _sleet_flame(state, "partial", [60, 0, 1])
    _reject(state, "cast_spell_outside_combat", participant_data=data, **parameters)


@pytest.mark.parametrize("protection", ["freedom", "fly", "foreign"])
def test_sleet_ground_cost_immunity_flight_and_plane_preserve_real_entry_save(protection):
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _sleet_cast(success=True)
    if protection == "freedom":
        _run(
            "install_srd_actor_spell_modifier",
            state,
            target_state="guard",
            caster_state="guard",
            spell="freedom_of_movement",
            lasting_state="state",
            mode="freedom",
            save_dc=18,
            duration_rounds=600,
            concentration=False,
            actual_spell_level=4,
        )
    elif protection == "fly":
        state[f"{SRD}.entities"]["guard"]["flying_speed"] = 60
        _position(state, "guard")["metadata"]["flying"] = True
    else:
        _position(state, "guard")["metadata"]["plane"] = "ethereal"
    _turn(state, "guard")
    _run(
        "move_srd_scene_creature",
        state,
        actor_state="guard",
        destination=[25, 0, 0],
        mode="fly" if protection == "fly" else "walk",
    )
    assert state[f"{SRD}.combat"]["movement_spent"] == 15
    assert (state[f"{SRD}.entities"]["guard"].get("last_sleet_storm_save") is None) is (
        protection == "foreign"
    )


def test_sleet_source_expiry_removes_obscuration_and_ground_after_real_clock():
    state, _, _ = _sleet_cast(success=True)
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert not state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    assert not state[f"{SRD}.state"]["has_sleet_storm"]
    point = _run("resolve_srd_environment_point", state, point=[40, 0, 10], plane="material")
    assert point.result["context"]["_environment_fog"] is False
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[25, 0, 0])
    assert state[f"{SRD}.entities"]["guard"]["scene_movement_spent"] == 15
    assert state[f"{SRD}.entities"]["guard"].get("last_sleet_storm_save") is None


def test_sleet_dispel_actual_cylinder_high_point_and_outside_radius():
    state, _, _ = _sleet_cast()
    parameters = {
        "caster_state": "hero",
        "area_id": "hero",
        "slot_level": 3,
        "casting_modifier": 4,
        "casting_ability": "intelligence",
    }
    _reject(state, "dispel_srd_environment_source", point=[61, 0, 1], **parameters)
    _run("dispel_srd_environment_source", state, point=[40, 0, 39], **parameters)
    assert not state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]


def test_sleet_real_heavy_obscuration_changes_sight_while_physical_line_stays_clear():
    state, _, _ = _sleet_cast()
    _position(state, "guard")["position"] = [80, 0, 0]
    relation = _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
    assert relation.result["context"]["_scene_line_of_sight"] is True
    assert relation.result["context"]["_scene_can_see"] is False
    _run("end_concentration", state, caster_state="hero")
    relation = _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
    assert relation.result["context"]["_scene_can_see"] is True


def test_sleet_printed_precipitation_is_not_dispersed_by_fog_strong_wind_rule():
    state, data, parameters = _sleet_case()
    state[f"{SRD}.state"]["spatial_scene"]["zones"].append(
        {
            "id": "sleet-wind",
            "map_id": "orders",
            "shape": {"kind": "cylinder", "center": [40, 0, 0], "radius": 5, "height": 10},
            "metadata": {"strong_wind": True, "plane": "material"},
        }
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    _reject(state, "disperse_srd_fog", area_id="hero", wind_zone_id="sleet-wind")


def test_public_sleet_paid_cast_real_save_flame_reopen_retry_and_later_source_removal(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as public_casting

    def actual_fixture(_name):
        state, data, parameters = _sleet_case()
        _sleet_flame(state, "fire", [40, 0, 1])
        return state, data, parameters

    monkeypatch.setattr(public_casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(public_casting, "_spell_case", actual_fixture)
    database = tmp_path / "sleet.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public_casting._create_spell_campaign(owner, "sleet_storm")
        command = ActionCommand(
            campaign_id="casting",
            action_id="sleet-cast",
            idempotency_key="sleet-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        first = StatelessMcpMechanics(owner).execute_action(command)
        assert first.status is ActionStatus.COMMITTED
        cast = owner.snapshot("casting")
        assert cast.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
        rows = {r["id"]: r for r in cast.state[f"{SRD}.state"]["spatial_scene"]["entities"]}
        assert rows["fire"]["metadata"]["srd_ordinary_flame"]["burning"] is False
        entered = replace(
            command,
            action_id="sleet-enter",
            idempotency_key="sleet-enter",
            expected_revision=cast.revision,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "guard", "destination": [21, 0, 0]},
        )
        moved = StatelessMcpMechanics(owner).execute_action(entered)
        assert moved.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert "prone" in saved.state[f"{SRD}.entities"]["guard"]["conditions"]
        assert saved.state[f"{SRD}.entities"]["guard"]["last_sleet_storm_save"]["fell"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(command).result == first.result
        assert mechanics.execute_action(entered).result == moved.result
        assert owner.snapshot("casting").state == saved.state
        ended = mechanics.execute_action(
            replace(
                command,
                action_id="sleet-end",
                idempotency_key="sleet-end",
                expected_revision=saved.revision,
                tool_name="end_concentration",
                parameters={"caster_state": "hero"},
            )
        )
        assert ended.status is ActionStatus.COMMITTED
        assert not owner.snapshot("casting").state[f"{SRD}.state"]["has_sleet_storm"]
        later = mechanics.execute_action(
            replace(
                entered,
                action_id="sleet-gone",
                idempotency_key="sleet-gone",
                expected_revision=owner.snapshot("casting").revision,
                parameters={"actor_state": "other", "destination": [25, 8, 0]},
            )
        )
        assert later.status is ActionStatus.COMMITTED
        assert (
            owner.snapshot("casting").state[f"{SRD}.entities"]["other"].get("last_sleet_storm_save")
            is None
        )


def _moonbeam_case(*, slot=2, inside=False, success=False, top=False):
    state, data, parameters = _fixture("moonbeam", origin=(40, 0, 40 if top else 0), slot=slot)
    _ground(state)
    parameters["environment_cylinder_face"] = "top" if top else "bottom"
    for actor in ("guard", "other"):
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=0)
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["constitution"] = (
            100 if success else -100
        )
    _position(state, "guard")["position"] = [40 if inside else 10, 0, 30]
    _position(state, "other")["position"] = [10, 8, 0]
    return state, data, parameters


def _moonbeam_cast(**choices):
    state, data, parameters = _moonbeam_case(**choices)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    return state, data, parameters


@pytest.mark.parametrize("success", [False, True])
@pytest.mark.parametrize("top", [False, True])
def test_moonbeam_fixed_cylinder_height_face_actual_constitution_radiant_half_and_upcast(
    success, top
):
    state, _, _ = _moonbeam_cast(slot=4, inside=True, success=success, top=top)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["radius"] == 5 and source["height"] == 40
    assert source["cylinder_min"] == 0 and source["cylinder_max"] == 40
    guard = state[f"{SRD}.entities"]["guard"]
    saved = guard["last_environment_sphere_save"]
    assert saved["raw_damage"]["count"] == 4 and saved["raw_damage"]["sides"] == 10
    assert saved["applied_damage"] == saved["raw_damage"]["total"] // (2 if success else 1)
    assert guard["hp"] == 1000 - saved["applied_damage"]


@pytest.mark.parametrize(
    "position,inside",
    [
        ([45.4, 0, 30], True),
        ([45.41, 0, 30], False),
        ([43.9, 3.9, 30], True),
        ([45, 5, 30], False),
        ([40, 0, 40], True),
        ([40, 0, 40.01], False),
        ([40, 0, -6.01], False),
    ],
)
def test_moonbeam_actual_box_body_cylinder_membership_is_horizontal_circle_plus_vertical_slab(
    position, inside
):
    state, _, _ = _moonbeam_cast()
    _position(state, "guard")["position"] = position
    result = _run(
        "qualify_srd_environment_sphere_target", state, target_state="guard", area_id="hero"
    )
    assert result.result["context"]["_sphere_target_inside"] is inside


def test_moonbeam_actual_entry_end_turn_once_and_shared_cost_without_difficult_terrain():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _moonbeam_cast()
    _position(state, "guard")["position"] = [20, 0, 30]
    _turn(state, "guard")
    assert "last_environment_sphere_save" not in state[f"{SRD}.entities"]["guard"]
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[40, 0, 30])
    saved = deepcopy(state[f"{SRD}.entities"]["guard"]["last_environment_sphere_save"])
    assert state[f"{SRD}.combat"]["movement_spent"] == 20
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["last_environment_sphere_save"] == saved
    _turn(state, "guard")
    before = state[f"{SRD}.entities"]["guard"]["hp"]
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["hp"] < before


def test_moonbeam_later_turn_magic_action_moves_actual_crossed_creature_not_only_endpoints():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, _ = _moonbeam_cast()
    _position(state, "guard")["position"] = [60, 0, 30]
    _reject(
        state,
        "move_srd_moonbeam_source",
        caster_state="hero",
        area_id="hero",
        destination=[80, 0, 0],
    )
    _turn(state, "hero", data)
    _run(
        "move_srd_moonbeam_source",
        state,
        caster_state="hero",
        area_id="hero",
        destination=[80, 0, 0],
    )
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["origin"] == [80, 0, 0]
    assert source["moonbeam_last_move"]["to"] == [80, 0, 0]
    saved = state[f"{SRD}.entities"]["guard"]["last_environment_sphere_save"]
    assert 54 < saved["origin"][0] < 60 and saved["applied_damage"] > 0
    assert not state[f"{SRD}.combat"]["action_available"]
    _reject(
        state,
        "move_srd_moonbeam_source",
        caster_state="hero",
        area_id="hero",
        destination=[90, 0, 0],
    )


def test_moonbeam_current_cylinder_dim_light_plane_height_concentration_and_expiry():
    state, _, _ = _moonbeam_cast()

    def lit(point, plane="material"):
        return _run("resolve_srd_environment_light", state, point=point, plane=plane).result[
            "context"
        ]["_environment_magic_dim"]

    assert lit([40, 0, 39]) and not lit([40, 0, 40.1])
    assert not lit([46, 0, 10]) and not lit([40, 0, 10], "ethereal")
    _run("end_concentration", state, caster_state="hero")
    assert not lit([40, 0, 10])
    state, _, _ = _moonbeam_cast()
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert not lit([40, 0, 10])


@pytest.mark.parametrize("deplete", [False, True])
@pytest.mark.parametrize("success", [False, True])
def test_moonbeam_real_transformation_reverts_on_failed_save_and_preserves_pre_damage_shape_receipt(
    deplete, success
):
    state, data, parameters = _moonbeam_case(inside=True, success=success)
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(
        challenge_rating_eighths=32,
        ability_scores=dict.fromkeys(data["abilities"], 12),
        size_rank=2,
        languages=["common"],
        passive_perception=11,
    )
    _run(
        "cast_spell_transformation",
        state,
        caster_state="other",
        lasting_state="state",
        spell="polymorph",
        actual_spell_level=4,
        save_dc=18,
        casting_modifier=4,
        casting_ability="intelligence",
        target_state="guard",
        form_id="camel",
        forego_save_targets=["guard"],
        caster_can_see_target=True,
        target_distance=40,
        concentration_save_modifier=0,
    )
    source_id = state[f"{SRD}.entities"]["guard"]["form_current_source_id"]
    assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == 17
    if deplete:
        _run(
            "apply_srd_damage", state, target="guard", amount=16, adjustment=0, damage_type="force"
        )
        assert state[f"{SRD}.entities"]["guard"]["temp_hp"] == 1
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["constitution"] = (
        100 if success else -100
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    guard = state[f"{SRD}.entities"]["guard"]
    if not success:
        assert (
            guard["form_current_source_id"] is None
            and not state[f"{SRD}.state"]["transformation_sources"][source_id]["installed"]
        )
        assert guard["moonbeam_shapechange_sources"] == [
            {
                "area_id": "hero",
                "sequence": state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["sequence"],
            }
        ]
        assert not _run("check_srd_moonbeam_shapechange", state, target_state="guard").result[
            "context"
        ]["_moonbeam_shapechange_allowed"]
        _run("move_srd_scene_creature", state, actor_state="guard", destination=[55, 0, 30])
        assert state[f"{SRD}.entities"]["guard"]["moonbeam_shapechange_sources"] == []
        _run("move_srd_scene_creature", state, actor_state="guard", destination=[40, 0, 30])
        assert _run("check_srd_moonbeam_shapechange", state, target_state="guard").result[
            "context"
        ]["_moonbeam_shapechange_allowed"]
    else:
        assert not guard.get("moonbeam_shapechange_sources")
        assert _run("check_srd_moonbeam_shapechange", state, target_state="guard").result[
            "context"
        ]["_moonbeam_shapechange_allowed"]


@pytest.mark.parametrize(
    "slot,dark_origin,plane,ended",
    [
        (2, [40, 0, 45], "material", True),
        (3, [40, 0, 45], "material", False),
        (2, [40, 0, 55.01], "material", False),
        (2, [40, 0, 45], "ethereal", False),
        (2, [58, 0, 30], "material", True),
    ],
)
def test_moonbeam_actual_darkness_cylinder_sphere_overlap_level_and_plane(
    slot, dark_origin, plane, ended
):
    state, _, _ = _moonbeam_cast(slot=slot)
    _position(state, "other")["position"] = [40, 8, 30]
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
        illumination_position=dark_origin,
    )
    _run("refresh_srd_moonbeam_darkness", state)
    assert bool(state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]) is not ended
    assert (
        _run("resolve_srd_environment_light", state, point=[40, 0, 30], plane="material").result[
            "context"
        ]["_environment_magic_dim"]
        is not ended
    )


def test_moonbeam_other_plane_true_wall_and_curved_body_keep_actual_qualification_honest():
    state, _, _ = _moonbeam_cast()
    _position(state, "guard")["position"] = [40, 0, 30]
    _position(state, "guard")["metadata"]["plane"] = "ethereal"
    assert not _run(
        "qualify_srd_environment_sphere_target", state, target_state="guard", area_id="hero"
    ).result["context"]["_sphere_target_inside"]
    _position(state, "guard")["metadata"]["plane"] = "material"
    scene = state[f"{SRD}.state"]["spatial_scene"]
    floor = deepcopy(scene["geometry"][0])
    floor.update(
        id="moonbeam-wall", volume={"kind": "box", "min": [30, -20, 20], "max": [50, 20, 21]}
    )
    floor["metadata"]["ground"] = False
    scene["geometry"].append(floor)
    assert not _run(
        "qualify_srd_environment_sphere_target", state, target_state="guard", area_id="hero"
    ).result["context"]["_sphere_target_inside"]
    scene["geometry"].pop()
    _position(state, "guard")["footprint"] = {"kind": "circle", "radius": 0.4}
    _reject(state, "qualify_srd_environment_sphere_target", target_state="guard", area_id="hero")


def test_moonbeam_new_source_already_inside_real_darkness_pays_then_retires():
    state, data, parameters = _moonbeam_case()
    _position(state, "other")["position"] = [40, 8, 30]
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
        illumination_position=[40, 0, 45],
    )
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert result.result["status"] == "committed"
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
    assert not state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert "source ended during the cast" in str(result.result)


def test_moonbeam_actual_moved_final_origin_settles_existing_darkness_source():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, _ = _moonbeam_cast()
    _position(state, "other")["position"] = [90, 8, 30]
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
        illumination_position=[90, 0, 30],
    )
    _turn(state, "hero", data)
    _run(
        "move_srd_moonbeam_source",
        state,
        caster_state="hero",
        area_id="hero",
        destination=[80, 0, 0],
    )
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["origin"] == [80, 0, 0] and not source["active"]
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None


def test_moonbeam_actual_caster_lethal_contact_stops_later_motion_and_other_damage():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, _ = _moonbeam_cast()
    _position(state, "hero")["position"] = [60, 0, 0]
    _position(state, "guard")["position"] = [70, 0, 30]
    state[f"{SRD}.entities"]["hero"].update(
        hp=1, temp_hp=0, saving_throw_modifiers={"constitution": -100}
    )
    _turn(state, "hero", data)
    _run(
        "move_srd_moonbeam_source",
        state,
        caster_state="hero",
        area_id="hero",
        destination=[80, 0, 0],
    )
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert not source["active"] and state[f"{SRD}.entities"]["hero"]["hp"] == 0
    assert 54 < source["origin"][0] < 55
    assert source["moonbeam_last_move"]["to"] == source["origin"]
    assert source["moonbeam_last_move"]["to"] != source["moonbeam_last_move"]["destination"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000


@pytest.mark.parametrize("success", [False, True])
def test_stinking_cloud_only_actual_start_turn_saves_and_exact_end_turn_retires(success):
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _stinking_cast(success=success)
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 1000 and "poisoned" not in guard["conditions"]
    assert _fog_point(state, [40, 0, 0])
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    assert "last_stinking_cloud_save" not in state[f"{SRD}.entities"]["guard"]
    _turn(state, "guard")
    guard = state[f"{SRD}.entities"]["guard"]
    save = deepcopy(guard["last_stinking_cloud_save"])
    assert save["outcome"] == ("success" if success else "failure")
    assert ("poisoned" in guard["conditions"]) is not success
    before = deepcopy(state)
    _run("resolve_srd_environment_hazard_start_turn", state, target_state="guard")
    assert state == before
    _run("end_combat_turn", state)
    guard = state[f"{SRD}.entities"]["guard"]
    assert "poisoned" not in guard["conditions"]
    if not success:
        assert not state[f"{SRD}.state"]["timed_condition_sources"][save["condition_source_id"]][
            "installed"
        ]


def test_stinking_cloud_actual_poison_blocks_action_bonus_but_allows_movement_and_reaction():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, parameters = _stinking_case()
    _position(state, "hero")["position"] = [35, 0, 0]
    _position(state, "guard")["position"] = [10, 0, 0]
    state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"] = {"constitution": -100}
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    _turn(state, "hero", data)
    _reject(state, "spend_srd_action")
    _reject(state, "consume_srd_bonus_action", actor_state="hero", bonus_action="fixture")
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[30, 0, 0])
    state[f"{SRD}.entities"]["hero"]["available_reactions"] = ["opportunity_attack"]
    _run(
        "spend_srd_reaction", state, actor="hero", actor_state="hero", reaction="opportunity_attack"
    )
    assert "poisoned" in state[f"{SRD}.entities"]["hero"]["conditions"]
    _run("end_combat_turn", state)
    _position(state, "hero")["position"] = [0, 0, 0]
    _turn(state, "hero", data)
    _run("spend_srd_action", state)
    _run("consume_srd_bonus_action", state, actor_state="hero", bonus_action="fixture")


def test_stinking_cloud_blocks_the_actual_existing_woodland_bonus_disengage_grant():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, _ = _woodland_cast()
    state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"] = {"constitution": -100}
    _run(
        "cast_spell_environment",
        state,
        caster_state="other",
        spell="stinking_cloud",
        slot_level=3,
        concentration_save_modifier=0,
        save_dc=18,
        origin=[0, 0, 0],
    )
    _turn(state, "hero", data)
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "conjure_woodland_beings"
    _reject(state, "disengage_srd_conjure_woodland", actor_state="hero")


def test_stinking_cloud_real_poison_reaches_the_existing_shared_ability_check_disadvantage():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _stinking_cast()
    _turn(state, "guard")
    result = _run(
        "d20_test",
        state,
        actor_state="guard",
        kind="ability_check",
        ability="strength",
        ability_modifier=0,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=10,
        advantage=False,
        disadvantage=False,
    ).result["context"]
    assert result["_effective_disadvantage"]
    assert len(result["_d20_test"]["results"]) == 2


@pytest.mark.parametrize("source_end", ["concentration", "wind"])
def test_stinking_cloud_current_turn_poison_survives_cloud_end_then_only_exact_turn_end_removes(
    source_end,
):
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _stinking_cast()
    _turn(state, "guard")
    source_id = state[f"{SRD}.entities"]["guard"]["stinking_cloud_condition_source_id"]
    if source_end == "concentration":
        _run("end_concentration", state, caster_state="hero")
    else:
        state[f"{SRD}.state"]["spatial_scene"]["zones"].append(
            {
                "id": "wind",
                "map_id": "orders",
                "shape": {"kind": "box", "min": [39, -1, 0], "max": [41, 1, 6]},
                "metadata": {"strong_wind": True, "plane": "material"},
            }
        )
        _run("refresh_srd_environment_wind", state)
    assert not state[f"{SRD}.state"]["has_spell_environments"]
    assert state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"]
    _reject(state, "spend_srd_action")
    _run("end_combat_turn", state)
    assert "poisoned" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert not state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"]


def test_stinking_cloud_immune_creature_saves_without_poison_or_action_ban():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, parameters = _stinking_case()
    state[f"{SRD}.entities"]["guard"]["condition_immunities"] = ["poisoned"]
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    _turn(state, "guard")
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["last_stinking_cloud_save"]["immune"]
    assert "poisoned" not in guard["conditions"]
    _run("spend_srd_action", state)


def test_stinking_cloud_turn_end_preserves_actual_other_poison_source():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _stinking_cast()
    _run(
        "install_srd_timed_condition_source",
        state,
        target_state="guard",
        lasting_state="state",
        spell="ray_of_sickness",
        condition="poisoned",
        save_dc=12,
        duration_rounds=10,
        repeat_save_timing="none",
    )
    _turn(state, "guard")
    _run("end_combat_turn", state)
    guard = state[f"{SRD}.entities"]["guard"]
    assert "poisoned" in guard["conditions"]
    assert guard["stinking_cloud_condition_source_id"] is None


def test_stinking_cloud_actual_source_immunity_or_early_retirement_releases_only_its_action_ban():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _stinking_cast()
    _turn(state, "guard")
    source_id = state[f"{SRD}.entities"]["guard"]["stinking_cloud_condition_source_id"]
    _run("retire_srd_timed_condition_source", state, lasting_state="state", source_id=source_id)
    _run("refresh_srd_timed_condition_target", state, lasting_state="state", target_state="guard")
    _run("spend_srd_action", state)
    assert "poisoned" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("end_combat_turn", state)


def test_stinking_cloud_other_plane_creature_does_not_save_on_its_actual_start():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _stinking_cast()
    _position(state, "guard")["metadata"]["plane"] = "ethereal"
    _turn(state, "guard")
    assert "last_stinking_cloud_save" not in state[f"{SRD}.entities"]["guard"]


def test_stinking_cloud_actual_immunity_suppression_keeps_source_and_restores_current_turn_poison():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _stinking_cast()
    _turn(state, "guard")
    source_id = state[f"{SRD}.entities"]["guard"]["stinking_cloud_condition_source_id"]
    state[f"{SRD}.entities"]["guard"]["condition_immunities"] = ["poisoned"]
    _run("refresh_srd_timed_condition_target", state, lasting_state="state", target_state="guard")
    assert "poisoned" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"]
    _run("check_srd_active_control_action", state, actor_state="guard", action_kind="action")
    state[f"{SRD}.entities"]["guard"]["condition_immunities"] = []
    _run("refresh_srd_timed_condition_target", state, lasting_state="state", target_state="guard")
    assert "poisoned" in state[f"{SRD}.entities"]["guard"]["conditions"]
    _reject(state, "spend_srd_action")


def test_stinking_cloud_managed_projection_preserves_actual_contagion_and_its_cure_veto(
    monkeypatch,
):
    from tests.srd_full.test_srd_progressive_afflictions import _case, _cast, _source
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, parameters = _case(monkeypatch, "contagion")
    _cast(state, data, parameters)
    contagion_id, _ = _source(state)
    _run(
        "cast_spell_environment",
        state,
        caster_state="other",
        spell="stinking_cloud",
        slot_level=3,
        concentration_save_modifier=0,
        save_dc=18,
        origin=[3, 0, 0],
    )
    _turn(state, "guard")
    _run("remove_srd_condition", state, target="guard", condition="poisoned")
    assert state[f"{SRD}.state"]["timed_condition_sources"][contagion_id]["installed"]
    assert "poisoned" in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("end_combat_turn", state)
    assert state[f"{SRD}.state"]["timed_condition_sources"][contagion_id]["installed"]
    assert "poisoned" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.entities"]["guard"]["stinking_cloud_condition_source_id"] is None


def test_stinking_cloud_actual_wall_protects_creature_start_turn():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _stinking_cast()
    wall = deepcopy(state[f"{SRD}.state"]["spatial_scene"]["geometry"][0])
    wall.update(id="wall", volume={"kind": "box", "min": [36, -10, 0], "max": [38, 10, 20]})
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(wall)
    _turn(state, "guard")
    assert "last_stinking_cloud_save" not in state[f"{SRD}.entities"]["guard"]


def test_stinking_cloud_latest_overlap_forces_one_start_save_then_old_source_waits_next_turn():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _stinking_cast()
    _run(
        "cast_spell_environment",
        state,
        caster_state="other",
        spell="stinking_cloud",
        slot_level=3,
        concentration_save_modifier=0,
        save_dc=18,
        origin=[40, 0, 0],
    )
    _turn(state, "guard")
    guard = state[f"{SRD}.entities"]["guard"]
    saved = deepcopy(guard["last_stinking_cloud_save"])
    assert saved["area_id"] == "other"
    _run("end_concentration", state, caster_state="other")
    _run("resolve_srd_environment_hazard_start_turn", state, target_state="guard")
    assert state[f"{SRD}.entities"]["guard"]["last_stinking_cloud_save"] == saved
    _run("end_combat_turn", state)
    _turn(state, "guard")
    assert state[f"{SRD}.entities"]["guard"]["last_stinking_cloud_save"]["area_id"] == "hero"


def test_stinking_cloud_managed_projection_preserves_actual_turn_effect_poison():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _stinking_cast()
    _turn(state, "guard")
    _run(
        "install_srd_turn_effect",
        state,
        target_state="guard",
        lasting_state="state",
        spell="ray_of_sickness",
        kind="poisoned",
        effect_label="Independent current-turn poison",
        caster_state="hero",
        expiry_owner="caster",
        expiry_phase="end",
        magnitude=0,
        dice_count=0,
        damage_die="d8",
        damage_type="poison",
    )
    source_id = state[f"{SRD}.entities"]["guard"]["stinking_cloud_condition_source_id"]
    _run("retire_srd_timed_condition_source", state, lasting_state="state", source_id=source_id)
    _run("refresh_srd_timed_condition_target", state, lasting_state="state", target_state="guard")
    assert "poisoned" in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("spend_srd_action", state)


@pytest.mark.parametrize("success", [False, True])
def test_cloudkill_initial_actual_constitution_poison_upcast_and_heavy_obscuration(success):
    state, _, _ = _cloud_cast(slot=7, inside=True, success=success)
    guard = state[f"{SRD}.entities"]["guard"]
    saved = guard["last_environment_sphere_save"]
    assert saved["spell"] == "cloudkill" and saved["raw_damage"]["count"] == 7
    assert saved["raw_damage"]["sides"] == 8
    assert saved["applied_damage"] == saved["raw_damage"]["total"] // (2 if success else 1)
    assert guard["hp"] == 1000 - saved["applied_damage"]
    point = _run("resolve_srd_environment_point", state, point=[40, 0, 0], plane="material").result[
        "context"
    ]
    assert point["_environment_fog"] and not point["_environment_lightly_obscured"]
    relation = _run(
        "resolve_srd_scene_relation", state, actor_state="hero", target_state="guard"
    ).result["context"]
    assert not relation["_scene_can_see"] and relation["_scene_line_of_sight"]


def test_cloudkill_actual_entry_and_end_turn_save_once_without_difficult_terrain():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _cloud_cast()
    _turn(state)
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[25, 0, 0])
    assert state[f"{SRD}.combat"]["movement_spent"] == 15
    saved = deepcopy(state[f"{SRD}.entities"]["guard"]["last_environment_sphere_save"])
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["last_environment_sphere_save"] == saved
    before = state[f"{SRD}.entities"]["guard"]["hp"]
    _turn(state)
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["hp"] < before


def test_cloudkill_moves_exact_ten_on_own_actual_later_start_and_crossing_saves_once():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, _ = _cloud_cast()
    _position(state, "guard")["position"] = [65, 0, 0]
    _turn(state, "guard")
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["origin"] == [40, 0, 0]
    _run("end_combat_turn", state)
    _turn(state, "hero", data)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["origin"] == [50, 0, 0]
    assert source["cloud_last_move"]["from"] == [40, 0, 0]
    assert source["cloud_last_move"]["to"] == [50, 0, 0]
    assert state[f"{SRD}.entities"]["guard"]["hp"] < 1000
    before = deepcopy(state)
    _run("resolve_srd_environment_hazard_start_turn", state, target_state="hero")
    assert state == before
    _reject(state, "move_srd_cloudkill_source", caster_state="hero", area_id="hero")


@pytest.mark.parametrize("coincident", [False, True])
def test_cloudkill_away_vector_uses_actual_caster_position_or_admitted_facing(coincident):
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, _ = _cloud_cast()
    hero = state[f"{SRD}.entities"]["hero"]
    hero["damage_immunities"] = ["poison"]
    hero["saving_throw_modifiers"] = {"constitution": 100}
    _position(state, "hero")["position"] = [40, 0, 0] if coincident else [0, 10, 0]
    _position(state, "hero")["facing"] = [0, 2]
    _turn(state, "hero", data)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    expected = [40, 10, 0] if coincident else [40 + 400 * (1700**-0.5), -100 * (1700**-0.5), 0]
    assert source["origin"] == pytest.approx(expected)
    assert sum((source["origin"][i] - [40, 0, 0][i]) ** 2 for i in range(3)) ** 0.5 == (
        pytest.approx(10)
    )


def test_cloudkill_actual_later_strong_wind_overlap_ends_cloud_and_obscuration():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, _ = _cloud_cast()
    _position(state, "guard")["position"] = [65, 0, 0]
    state[f"{SRD}.state"]["spatial_scene"]["zones"].append(
        {
            "id": "actual-cloud-wind",
            "map_id": "orders",
            "shape": {"kind": "box", "min": [69, -5, -5], "max": [70, 5, 5]},
            "metadata": {"strong_wind": True, "plane": "material"},
        }
    )
    _turn(state, "hero", data)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["origin"] == [50, 0, 0] and not source["active"]
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    point = _run("resolve_srd_environment_point", state, point=[50, 0, 0], plane="material").result[
        "context"
    ]
    assert not point["_environment_fog"]


def test_cloudkill_saved_sweep_damages_actual_contact_that_is_outside_both_endpoints():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, parameters = _cloud_case()
    _position(state, "guard")["position"] = [45, 20.35, 0]
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    _turn(state, "hero", data)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    saved = state[f"{SRD}.entities"]["guard"]["last_environment_sphere_save"]
    assert source["origin"] == [50, 0, 0] and saved["applied_damage"] > 0
    assert 40 < saved["origin"][0] < 45
    assert not _run(
        "qualify_srd_environment_sphere_target", state, target_state="guard", area_id="hero"
    ).result["context"]["_sphere_target_inside"]


def test_cloudkill_real_source_displacement_stops_when_its_caster_lethal_contact_ends_spell():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, _ = _cloud_cast()
    _position(state, "hero")["position"] = [40, 0, 0]
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(hp=1, temp_hp=0, saving_throw_modifiers={"constitution": -100})
    _turn(state, "hero", data)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 0 and not source["active"]
    assert source["origin"][0] < 40.001
    assert source["cloud_last_move"]["to"] == source["origin"]
    assert source["cloud_last_move"]["to"] != source["cloud_last_move"]["destination"]


def test_cloudkill_initial_all_recipients_settle_after_actual_caster_lethal_damage():
    state, data, parameters = _cloud_case()
    parameters["environment_origin"] = [0, 0, 0]
    state[f"{SRD}.entities"]["hero"].update(
        hp=1, temp_hp=0, saving_throw_modifiers={"constitution": -100}
    )
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert not source["active"] and source["appearance_completed"]
    assert "source ended during the cast" in str(result.result)
    for target in ("guard", "other"):
        assert (
            state[f"{SRD}.entities"][target]["last_environment_sphere_save"]["applied_damage"] > 0
        )


def test_cloudkill_different_plane_strong_wind_does_not_disperse_actual_source():
    state, data, parameters = _cloud_case(inside=True)
    state[f"{SRD}.state"]["spatial_scene"]["zones"].append(
        {
            "id": "other-plane-wind",
            "map_id": "orders",
            "shape": {"kind": "box", "min": [30, -5, -5], "max": [31, 5, 5]},
            "metadata": {"strong_wind": True, "plane": "ethereal"},
        }
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] < 1000


def test_cloudkill_unmapped_mandatory_destination_rejects_actual_start_atomically():
    state, data, _ = _cloud_cast()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["map"]["bounds"]["max"][0] = 45
    scene["geometry"][0]["volume"]["max"][0] = 45
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor="hero")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="larger actual map"):
        _run("begin_combat_turn", state, participant_data=data)
    assert state == before


@pytest.mark.parametrize("success", [False, True])
def test_insects_initial_actual_constitution_and_upcast_typed_damage(success):
    state, _, _ = _insects_cast(slot=7, inside=True, success=success)
    guard = state[f"{SRD}.entities"]["guard"]
    saved = guard["last_environment_sphere_save"]
    assert saved["spell"] == "insect_plague"
    assert saved["outcome"] == ("success" if success else "failure")
    assert saved["raw_damage"]["count"] == 6 and saved["raw_damage"]["sides"] == 10
    amount = saved["raw_damage"]["total"] // (2 if success else 1)
    assert saved["applied_damage"] == amount and guard["hp"] == 1000 - amount
    assert "last_environment_sphere_save" not in state[f"{SRD}.entities"]["other"]
    assert "last_environment_sphere_save" not in state[f"{SRD}.entities"]["hero"]


def test_insects_real_entry_spends_source_terrain_and_saves_once_on_actual_turn():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _insects_cast()
    _turn(state)
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[25, 0, 0])
    guard = state[f"{SRD}.entities"]["guard"]
    assert state[f"{SRD}.combat"]["movement_spent"] == pytest.approx(
        15 + (25 - 40 + (400 - 9) ** 0.5)
    )
    assert _position(state, "guard")["position"] == [25, 0, 0]
    assert guard["hp"] < 1000
    saved = deepcopy(guard["last_environment_sphere_save"])
    _run("end_combat_turn", state, actor_state="guard")
    assert state[f"{SRD}.entities"]["guard"]["last_environment_sphere_save"] == saved
    _turn(state)
    _run("end_combat_turn", state, actor_state="guard")
    assert state[f"{SRD}.entities"]["guard"]["hp"] < guard["hp"]


def test_insects_box_body_membership_uses_sphere_not_its_bounding_cube_or_other_plane():
    state, _, _ = _insects_cast()
    _position(state, "guard")["position"] = [59.9, 19.9, 0]
    corner = _run(
        "qualify_srd_environment_sphere_target", state, target_state="guard", area_id="hero"
    ).result["context"]
    assert not corner["_sphere_target_inside"]
    _position(state, "guard")["position"] = [60.4, 0, 0]
    touch = _run(
        "qualify_srd_environment_sphere_target", state, target_state="guard", area_id="hero"
    ).result["context"]
    assert touch["_sphere_target_inside"]
    _position(state, "guard")["metadata"]["plane"] = "ethereal"
    plane = _run(
        "qualify_srd_environment_sphere_target", state, target_state="guard", area_id="hero"
    ).result["context"]
    assert not plane["_sphere_target_inside"]


@pytest.mark.parametrize("defense", ["resistance", "immunity"])
def test_insects_actual_typed_defense_uses_one_shared_damage_lifecycle(defense):
    state, data, parameters = _insects_case(inside=True)
    state[f"{SRD}.entities"]["guard"][
        f"damage_{'resistances' if defense == 'resistance' else 'immunities'}"
    ] = ["piercing"]
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    guard = state[f"{SRD}.entities"]["guard"]
    saved = guard["last_environment_sphere_save"]
    assert saved["applied_damage"] == (
        saved["raw_damage"]["total"] // 2 if defense == "resistance" else 0
    )
    assert guard["hp"] == 1000 - saved["applied_damage"]


@pytest.mark.parametrize("retirement", ["end", "clock", "dispel"])
def test_insects_actual_retirement_removes_later_terrain_obscuration_and_saves(retirement):
    state, _, _ = _insects_cast(inside=True)
    if retirement == "end":
        _run("end_concentration", state, caster_state="hero")
    elif retirement == "clock":
        _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    else:
        _run(
            "dispel_srd_environment_source",
            state,
            caster_state="hero",
            area_id="hero",
            point=[40, 0, 3],
            slot_level=5,
            casting_modifier=4,
            casting_ability="wisdom",
        )
    assert not state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    point = _run("resolve_srd_environment_point", state, point=[40, 0, 3], plane="material").result[
        "context"
    ]
    assert not point["_environment_lightly_obscured"]
    cost = _run(
        "resolve_srd_environment_sphere_terrain_intervals",
        state,
        actor_subject="guard",
        start=[35, 0, 0],
        end=[40, 0, 0],
    ).result["context"]
    assert not cost["_sphere_route_intervals"]
    before = deepcopy(state[f"{SRD}.entities"]["guard"])
    _run("resolve_srd_environment_sphere_event", state, target_state="guard", area_id="hero")
    assert state[f"{SRD}.entities"]["guard"] == before


def test_insects_light_obscuration_affects_actual_perception_without_blocking_sight():
    state, _, _ = _insects_cast(inside=True)
    _position(state, "guard")["metadata"]["lighting"] = "bright"
    roll = _run(
        "d20_test",
        state,
        actor_state="guard",
        kind="ability_check",
        ability="wisdom",
        skill="perception",
        ability_modifier=0,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=10,
        requires_sight=True,
    ).result["context"]
    assert roll["_effective_disadvantage"]
    assert len(roll["_d20_test"]["results"]) == 2
    sight = _run(
        "resolve_srd_environment_segment", state, start=[0, 0, 3], end=[50, 0, 3], plane="material"
    ).result["context"]
    assert sight["_environment_lightly_obscured"] and not sight["_environment_sight_obscured"]


def test_insects_actual_opaque_wall_blocks_damage_and_source_terrain_behind_it():
    state, _, _ = _insects_cast()
    _position(state, "guard")["position"] = [24, 0, 0]
    scene = state[f"{SRD}.state"]["spatial_scene"]
    wall = deepcopy(scene["geometry"][0])
    wall.update(
        id="actual-insect-wall",
        volume={"kind": "box", "min": [29, -20, 0], "max": [30, 20, 8]},
        metadata={"plane": "material"},
    )
    scene["geometry"].append(wall)
    moved = _run(
        "move_srd_scene_creature", state, actor_state="guard", destination=[25, 0, 0]
    ).result["context"]
    assert moved["_scene_movement_cost"] == 1
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    assert "last_environment_sphere_save" not in state[f"{SRD}.entities"]["guard"]


def test_insects_source_terrain_applies_in_flight_and_joins_existing_difficult_union():
    state, _, _ = _insects_cast()
    _position(state, "guard")["position"] = [25, 0, 10]
    _position(state, "guard")["metadata"]["flying"] = True
    cost = _run(
        "srd_information_movement_cost",
        state,
        actor_subject="guard",
        start=[25, 0, 10],
        end=[30, 0, 10],
        plane="material",
        ignore_ground_environments=True,
        base_difficult=True,
    ).result["context"]
    assert cost["_route_distance"] == 5 and cost["_route_cost"] == 10
    after = _run(
        "srd_information_movement_cost",
        state,
        actor_subject="guard",
        start=[25, 0, 10],
        end=[30, 0, 10],
        plane="material",
        ignore_ground_environments=True,
        base_difficult=False,
    ).result["context"]
    assert after["_route_cost"] == 10


def test_insects_rejects_unmapped_actual_source_origin_atomically():
    state, data, parameters = _insects_case()
    state[f"{SRD}.state"]["spatial_scene"]["map"]["bounds"]["max"][0] = 100
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = []
    parameters["environment_origin"] = [150, 0, 3]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="map bounds"):
        _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state == before


def test_insects_initial_recipients_survive_source_retirement_after_actual_caster_lethal_damage():
    state, data, parameters = _insects_case()
    parameters["environment_origin"] = [0, 0, 3]
    state[f"{SRD}.entities"]["hero"].update(hp=1, temp_hp=0)
    state[f"{SRD}.entities"]["hero"]["saving_throw_modifiers"] = {"constitution": -100}
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 0
    for target in ("guard", "other"):
        saved = state[f"{SRD}.entities"][target]["last_environment_sphere_save"]
        assert saved["applied_damage"] > 0 and state[f"{SRD}.entities"][target]["hp"] < 1000
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert not source["active"] and not source["appearance_in_progress"]
    assert not source["appearance_targets"]
    prior = deepcopy(state[f"{SRD}.entities"]["guard"])
    _run("resolve_srd_environment_sphere_event", state, target_state="guard", area_id="hero")
    assert state[f"{SRD}.entities"]["guard"] == prior


def test_insects_initial_damage_includes_actual_living_dying_character():
    state, data, parameters = _insects_case(inside=True)
    state[f"{SRD}.entities"]["guard"].update(
        creature_kind="character",
        hp=0,
        is_alive=True,
        conditions=["unconscious"],
        death_failures=0,
        death_successes=0,
        stable=False,
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["last_environment_sphere_save"]["applied_damage"] > 0
    assert guard["hp"] == 0 and guard["is_alive"] and guard["death_failures"] == 1


@pytest.mark.parametrize("stronger_slot", [5, 7])
def test_insects_overlap_applies_actual_strongest_or_latest_and_restores_older(stronger_slot):
    state, _, _ = _insects_cast()
    _run(
        "cast_spell_environment",
        state,
        caster_state="other",
        spell="insect_plague",
        slot_level=stronger_slot,
        concentration_save_modifier=0,
        save_dc=18,
        origin=[40, 0, 3],
    )
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[25, 0, 0])
    guard = state[f"{SRD}.entities"]["guard"]
    saved = guard["last_environment_sphere_save"]
    assert saved["area_id"] == "other"
    assert saved["raw_damage"]["count"] == 4 + stronger_slot - 5
    assert guard["hp"] == 1000 - saved["raw_damage"]["total"]
    before = guard["hp"]
    _run("end_concentration", state, caster_state="other")
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _run("resolve_srd_environment_sphere_event", state, target_state="guard", area_id="hero")
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["last_environment_sphere_save"]["area_id"] == "hero"
    assert guard["last_environment_sphere_save"]["raw_damage"]["count"] == 4
    assert guard["hp"] < before


def _animals_case(*, slot=3, targets=("guard",), ground=True):
    state, data, parameters = _fixture("conjure_animals", origin=(30, 0, 0), slot=slot)
    if ground:
        _ground(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"].append(
        {
            "id": "actual-bright-space",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
            "metadata": {"plane": "material", "lighting": "bright"},
        }
    )
    _position(state, "guard")["position"] = [10, 0, 0]
    guard = state[f"{SRD}.entities"]["guard"]
    guard.update(hp=1000, max_hp=1000, temp_hp=0)
    guard["saving_throw_modifiers"]["dexterity"] = -100
    parameters.update(environment_pack_height=6, environment_force_targets=list(targets))
    return state, data, parameters


def _animals_cast(**choices):
    state, data, parameters = _animals_case(**choices)
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert result.result["status"] == "committed"
    return state, data, parameters


def _elemental_case(*, slot=5, element="air", targets=("guard", "other"), unseen=False):
    state, data, parameters = _fixture("conjure_elemental", origin=(30, 0, 0), slot=slot)
    _ground(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"].append(
        {
            "id": "actual-elemental-lighting",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
            "metadata": {"plane": "material", "lighting": "darkness" if unseen else "bright"},
        }
    )
    for actor in ("guard", "other"):
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=0)
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["dexterity"] = -100
    _position(state, "guard")["position"] = [10, 0, 0]
    _position(state, "other")["position"] = [10, 8, 0]
    parameters.update(
        environment_pack_height=6,
        environment_force_targets=list(targets),
        environment_element=element,
    )
    if unseen:
        state[f"{SRD}.entities"]["hero"].update(darkvision_feet=0, blindsight_feet=0)
        for actor in ("guard", "other"):
            _position(state, actor)["metadata"]["lighting"] = "darkness"
    return state, data, parameters


def _elemental_cast(**choices):
    state, data, parameters = _elemental_case(**choices)
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert result.result["status"] == "committed"
    return state, data, parameters


@pytest.mark.parametrize(
    "element,damage_type",
    [("air", "lightning"), ("earth", "thunder"), ("fire", "fire"), ("water", "cold")],
)
def test_elemental_actual_entry_damage_restrains_and_stops_later_movement(element, damage_type):
    state, _, _ = _elemental_cast(slot=6, element=element)
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    guard = state[f"{SRD}.entities"]["guard"]
    saved = guard["last_conjure_elemental_save"]
    assert source["damage_type"] == damage_type
    assert saved["raw_damage"]["count"] == 9 and saved["raw_damage"]["sides"] == 8
    assert saved["applied_damage"] == saved["raw_damage"]["total"]
    assert "restrained" in guard["conditions"]
    assert _position(state, "guard")["position"][0] == pytest.approx(24.600001)
    condition = state[f"{SRD}.state"]["timed_condition_sources"][source["restrained_source_id"]]
    assert condition["environment_sequence"] == source["sequence"]
    assert condition["expires_tick"] == source["expires_tick"] and condition["installed"]
    _run("end_concentration", state, caster_state="hero")
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert not _position(state, source["anchor_id"])["active"]


def test_elemental_appears_in_actual_unseen_unoccupied_space_without_placing_as_creature():
    state, _, _ = _elemental_cast(unseen=True)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    anchor = _position(state, source["anchor_id"])
    assert anchor["footprint"]["half_extents"] == [5, 5]
    assert anchor["movement"] == "passable" and anchor["visibility"] == "clear"
    assert (
        anchor["metadata"]["participant_ref"] is None
        and source["anchor_id"] not in state[f"{SRD}.entities"]
    )
    sight = _run(
        "resolve_srd_illumination_point_sight",
        state,
        observer_state="hero",
        point=anchor["position"],
    ).result["context"]
    assert not sight["_illumination_point_can_see"]


def test_elemental_success_rolls_no_damage_and_later_same_turn_entry_can_fail():
    state, _, _ = _elemental_cast()
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = 100
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[25, 0, 0])
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 1000 and guard["last_conjure_elemental_save"]["raw_damage"] is None
    assert "restrained" not in guard["conditions"]
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[24, 0, 0])
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = -100
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[25, 0, 0])
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_elemental_one_actual_binding_blocks_other_creature_entry_and_repeats_at_start():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _elemental_cast(slot=7)
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    _run("move_srd_scene_creature", state, actor_state="other", destination=[30, 8, 0])
    assert state[f"{SRD}.entities"]["other"]["hp"] == 1000
    _position(state, "guard")["position"] = [45, 0, 0]
    _turn(state, "guard")
    saved = deepcopy(state[f"{SRD}.entities"]["guard"]["last_conjure_elemental_save"])
    assert saved["repeat"] and saved["raw_damage"]["count"] == 6
    hp = state[f"{SRD}.entities"]["guard"]["hp"]
    _run("resolve_srd_environment_hazard_start_turn", state, target_state="guard")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = 100
    state[f"{SRD}.combat"].update(turn_active=False, current_actor="guard")
    _turn(state, "guard")
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.entities"]["guard"]["last_conjure_elemental_save"]["raw_damage"] is None


def test_elemental_start_within_five_is_initial_damage_and_does_not_repeat_same_start():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _elemental_cast()
    _position(state, "guard")["position"] = [20.4, 0, 0]
    _turn(state, "guard")
    saved = deepcopy(state[f"{SRD}.entities"]["guard"]["last_conjure_elemental_save"])
    assert not saved["repeat"] and saved["raw_damage"]["count"] == 8
    _run("resolve_srd_environment_hazard_start_turn", state, target_state="guard")
    assert state[f"{SRD}.entities"]["guard"]["last_conjure_elemental_save"] == saved


@pytest.mark.parametrize("change", ["occupied", "passable_creature", "bounds", "wall"])
def test_elemental_source_placement_rejects_actual_unsupported_space_atomically(change):
    state, data, parameters = _elemental_case()
    if change == "occupied":
        parameters["environment_origin"] = [10, 0, 0]
    elif change == "passable_creature":
        _position(state, "other")["movement"] = "passable"
        _position(state, "other")["position"] = [30, 0, 0]
    elif change == "bounds":
        parameters["environment_origin"] = [99, 0, 0]
    else:
        scene = state[f"{SRD}.state"]["spatial_scene"]
        wall = deepcopy(scene["geometry"][0])
        wall.update(
            id="true-intervening-wall",
            volume={"kind": "box", "min": [15, -5, -1], "max": [16, 5, 10]},
        )
        wall["metadata"] = {"plane": "material"}
        scene["geometry"].append(wall)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state == before


def test_elemental_real_face_tangency_and_unselected_or_unseen_creatures_do_not_force_save():
    state, _, _ = _elemental_cast(targets=("guard",))
    _position(state, "guard")["position"] = [10, 5.4, 0]
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 5.4, 0])
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    _run("move_srd_scene_creature", state, actor_state="other", destination=[30, 8, 0])
    assert state[f"{SRD}.entities"]["other"]["hp"] == 1000
    state, _, _ = _elemental_cast(unseen=True)
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000


@pytest.mark.parametrize("defense", ["condition_immunity", "damage_immunity", "resistance"])
def test_elemental_shared_typed_packet_and_restraint_immunity_are_distinct(defense):
    state, _, _ = _elemental_cast(element="water")
    guard = state[f"{SRD}.entities"]["guard"]
    if defense == "condition_immunity":
        guard["condition_immunities"] = ["restrained"]
    elif defense == "damage_immunity":
        guard["damage_immunities"] = ["cold"]
    else:
        guard["damage_resistances"] = ["cold"]
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    saved = state[f"{SRD}.entities"]["guard"]["last_conjure_elemental_save"]
    raw = saved["raw_damage"]["total"]
    assert saved["applied_damage"] == (
        0 if defense == "damage_immunity" else raw // 2 if defense == "resistance" else raw
    )
    assert ("restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]) == (
        defense != "condition_immunity"
    )


def test_elemental_real_freedom_suppression_replacement_retires_only_prior_spirit_binding():
    state, _, _ = _elemental_cast()
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    old_id = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["restrained_source_id"]
    _run(
        "install_srd_actor_spell_modifier",
        state,
        target_state="guard",
        caster_state="other",
        lasting_state="state",
        spell="freedom_of_movement",
        mode="freedom",
        save_dc=18,
        duration_rounds=600,
        concentration=False,
        actual_spell_level=4,
    )
    assert state[f"{SRD}.state"]["timed_condition_sources"][old_id]["installed"]
    assert state[f"{SRD}.state"]["timed_condition_sources"][old_id]["suppressed"]
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    _position(state, "other")["position"] = [10, 3, 0]
    _run("move_srd_scene_creature", state, actor_state="other", destination=[30, 3, 0])
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert "restrained" in state[f"{SRD}.entities"]["other"]["conditions"]
    assert (
        source["restrained_source_id"] != old_id
        or state[f"{SRD}.state"]["timed_condition_sources"][old_id]["target_state_id"] == "other"
    )
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("end_concentration", state, caster_state="hero")
    assert "restrained" not in state[f"{SRD}.entities"]["other"]["conditions"]


@pytest.mark.parametrize("retirement", ["clock", "dispel"])
def test_elemental_exact_source_retirement_removes_spirit_and_owned_restraint(retirement):
    state, _, _ = _elemental_cast()
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    source = deepcopy(state[f"{SRD}.state"]["persistent_spell_areas"]["hero"])
    if retirement == "clock":
        _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    else:
        _run(
            "dispel_srd_environment_source",
            state,
            caster_state="hero",
            area_id="hero",
            slot_level=5,
            casting_modifier=4,
            casting_ability="intelligence",
        )
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert not _position(state, source["anchor_id"])["active"]


@pytest.mark.parametrize("stronger_slot", [5, 7])
def test_elemental_actual_overlapping_sources_apply_strongest_or_latest_and_reveal_older(
    stronger_slot,
):
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _elemental_cast()
    old = deepcopy(state[f"{SRD}.state"]["persistent_spell_areas"]["hero"])
    _run(
        "cast_spell_environment",
        state,
        caster_state="other",
        spell="conjure_elemental",
        slot_level=stronger_slot,
        concentration_save_modifier=0,
        save_dc=old["save_dc"],
        origin=[30, 0, 0],
        pack_height=6,
        force_targets=["guard"],
        element="earth",
    )
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["last_conjure_elemental_save"]["area_id"] == "other"
    assert guard["last_conjure_elemental_save"]["raw_damage"]["count"] == 8 + stronger_slot - 5
    assert guard["hp"] == 1000 - guard["last_conjure_elemental_save"]["raw_damage"]["total"]
    assert (
        state[f"{SRD}.state"]["persistent_spell_areas"]["hero"].get("restrained_source_id") is None
    )
    _run("end_concentration", state, caster_state="other")
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]
    _turn(state, "guard")
    assert state[f"{SRD}.entities"]["guard"]["last_conjure_elemental_save"]["area_id"] == "hero"
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_elemental_actual_other_plane_entry_does_not_force_save():
    state, _, _ = _elemental_cast()
    _position(state, "guard")["metadata"]["plane"] = "ethereal"
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_elemental_successful_repeat_and_new_actual_entry_reuse_one_tracked_condition_identity():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _elemental_cast()
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    original_ids = deepcopy(
        state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["condition_source_ids"]
    )
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = 100
    _turn(state, "guard")
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[24, 0, 0])
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = -100
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[25, 0, 0])
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert (
        state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["condition_source_ids"]
        == original_ids
    )


def test_elemental_actual_caster_lethal_damage_ends_spirit_before_installing_orphan_restraint():
    state, _, _ = _elemental_cast(targets=("hero",))
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(hp=1, temp_hp=0)
    hero["saving_throw_modifiers"] = {"dexterity": -100}
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[30, 0, 0])
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 0
    assert not source["active"] and not _position(state, source["anchor_id"])["active"]
    assert source["condition_source_ids"] == []


def test_elemental_actual_bound_creature_death_frees_spirit_for_another_eligible_creature():
    state, _, _ = _elemental_cast()
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    _run("apply_srd_damage", state, target="guard", amount=2000, adjustment=0, damage_type="force")
    assert not state[f"{SRD}.entities"]["guard"]["is_alive"]
    _position(state, "other")["position"] = [10, 3, 0]
    _run("move_srd_scene_creature", state, actor_state="other", destination=[30, 3, 0])
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    binding = state[f"{SRD}.state"]["timed_condition_sources"][source["restrained_source_id"]]
    assert binding["target_state_id"] == "other"
    assert "restrained" in state[f"{SRD}.entities"]["other"]["conditions"]


def test_elemental_actual_dying_character_keeps_existing_restraint_binding():
    state, _, _ = _elemental_cast()
    state[f"{SRD}.entities"]["guard"]["creature_kind"] = "character"
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    source = deepcopy(state[f"{SRD}.state"]["persistent_spell_areas"]["hero"])
    _run(
        "apply_srd_damage",
        state,
        target="guard",
        amount=state[f"{SRD}.entities"]["guard"]["hp"],
        adjustment=0,
        damage_type="force",
    )
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 0 and guard["is_alive"] and "restrained" in guard["conditions"]
    bound = _run("qualify_srd_conjure_elemental_binding", state, area_id="hero").result["context"]
    assert bound["_elemental_binding_busy"]
    _position(state, "other")["position"] = [10, 3, 0]
    _run("move_srd_scene_creature", state, actor_state="other", destination=[30, 3, 0])
    assert state[f"{SRD}.entities"]["other"]["hp"] == 1000
    assert (
        state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["restrained_source_id"]
        == (source["restrained_source_id"])
    )


def test_elemental_failed_initial_save_installs_restraint_on_actual_dying_character():
    state, _, _ = _elemental_cast()
    state[f"{SRD}.entities"]["guard"].update(creature_kind="character", hp=1)
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 0 and guard["is_alive"] and "restrained" in guard["conditions"]
    bound = _run("qualify_srd_conjure_elemental_binding", state, area_id="hero").result["context"]
    assert bound["_elemental_binding_busy"]


@pytest.mark.parametrize(
    "spell,occupancy,allowed",
    [
        ("conjure_animals", "far", True),
        ("conjure_elemental", "far", True),
        ("conjure_animals", "face", True),
        ("conjure_elemental", "circle_tangent", True),
        ("conjure_animals", "circle_inside", False),
        ("conjure_elemental", "other_plane", True),
        ("conjure_elemental", "dead", True),
        ("conjure_elemental", "dying", False),
    ],
)
def test_conjure_source_uses_actual_passable_living_space_instead_of_global_rejection(
    spell, occupancy, allowed
):
    factory = _animals_case if spell == "conjure_animals" else _elemental_case
    state, data, parameters = factory(targets=())
    other = _position(state, "other")
    other["movement"] = "passable"
    other["position"] = [30, 0, 0]
    if occupancy == "far":
        other["position"] = [10, 8, 0]
    elif occupancy == "face":
        other["position"] = [35.4, 0, 0]
    elif occupancy in ("circle_tangent", "circle_inside"):
        other["footprint"] = {"kind": "circle", "radius": 1}
        other["position"] = [36 if occupancy == "circle_tangent" else 35.5, 0, 0]
    elif occupancy == "other_plane":
        other["metadata"]["plane"] = "ethereal"
    elif occupancy == "dead":
        state[f"{SRD}.entities"]["other"].update(hp=0, is_alive=False)
    else:
        state[f"{SRD}.entities"]["other"].update(
            hp=0, is_alive=True, creature_kind="character", conditions=["unconscious"]
        )
    if allowed:
        result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
        assert result.result["status"] == "committed"
        source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
        assert _position(state, source["anchor_id"])["position"] == [30, 0, 0]
        assert _position(state, "other")["movement"] == "passable"
    else:
        before = deepcopy(state)
        with pytest.raises(RulesetPreconditionRejected, match="living creature interiors"):
            _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
        assert state == before


@pytest.mark.parametrize("shape,tangent", [("box", True), ("circle", True), ("circle", False)])
def test_animals_optional_destination_reuses_actual_passable_occupancy_atomically(shape, tangent):
    state, _, _ = _animals_cast(targets=())
    other = _position(state, "other")
    other["movement"] = "passable"
    if shape == "box":
        other["position"] = [25.4, 0, 0]
    else:
        other["footprint"] = {"kind": "circle", "radius": 1}
        other["position"] = [26 if tangent else 25.5, 0, 0]
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[5, 0, 0])
    if tangent:
        _run("move_srd_conjure_animals_pack", state, actor_state="hero", destination=[20, 0, 0])
        source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
        assert _position(state, source["anchor_id"])["position"] == [20, 0, 0]
        assert source["pack_movement_receipt"] is None
    else:
        _reject(state, "move_srd_conjure_animals_pack", actor_state="hero", destination=[20, 0, 0])


def test_animals_pack_is_actual_large_intangible_source_and_retires_with_concentration():
    state, _, _ = _animals_cast()
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    pack = _position(state, source["anchor_id"])
    assert pack["position"] == [30, 0, 0]
    assert pack["footprint"] == {"kind": "box", "half_extents": [5, 5]}
    assert pack["height"] == 6 and pack["movement"] == "passable"
    assert pack["visibility"] == "clear" and pack["metadata"]["participant_ref"] is None
    assert source["anchor_id"] not in state[f"{SRD}.entities"]
    assert "subject_entity_id" not in source
    _run("end_concentration", state, caster_state="hero")
    assert not _position(state, source["anchor_id"])["active"]


@pytest.mark.parametrize("modifier,damage", [(-100, True), (100, False)])
def test_animals_actual_entry_uses_dexterity_slot_damage_and_once_per_turn(modifier, damage):
    state, _, _ = _animals_cast(slot=5)
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = modifier
    moved = _run("move_srd_scene_creature", state, actor_state="guard", destination=[20, 0, 0])
    assert moved.result["context"]["_scene_movement_cost"] == 10
    saved = deepcopy(state[f"{SRD}.entities"]["guard"]["last_conjure_animals_save"])
    if damage:
        assert saved["raw_damage"]["count"] == 5 and saved["raw_damage"]["sides"] == 10
        assert saved["applied_damage"] == saved["raw_damage"]["total"]
    else:
        assert saved["raw_damage"] is None and saved["applied_damage"] == 0
    hp = state[f"{SRD}.entities"]["guard"]["hp"]
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[25, 0, 0])
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp
    assert state[f"{SRD}.entities"]["guard"]["last_conjure_animals_save"] == saved


def test_animals_strength_save_advantage_uses_current_pack_space_and_source():
    state, _, _ = _animals_cast()
    _position(state, "hero")["position"] = [19.6, 0, 0]
    parameters = {
        "actor_state": "hero",
        "kind": "saving_throw",
        "ability": "strength",
        "ability_modifier": 3,
        "proficiency_bonus": 0,
        "proficiency_factor": "none",
        "circumstantial_modifier": 0,
        "target_number": 20,
        "advantage": False,
        "disadvantage": False,
    }
    result = _run("d20_test", state, **parameters).result["context"]
    assert result["_effective_advantage"]
    _position(state, "hero")["position"] = [19.59, 0, 0]
    result = _run("d20_test", state, **parameters).result["context"]
    assert not result["_effective_advantage"]
    _position(state, "hero")["position"] = [25, 0, 0]
    _run("end_concentration", state, caster_state="hero")
    result = _run("d20_test", state, **parameters).result["context"]
    assert not result["_effective_advantage"]


def test_animals_pack_movement_requires_actual_caster_receipt_and_commits_before_damage():
    state, _, _ = _animals_cast()
    _reject(state, "move_srd_conjure_animals_pack", actor_state="hero", destination=[20, 0, 0])
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[5, 0, 0])
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    receipt = deepcopy(source["pack_movement_receipt"])
    assert receipt["caster"] == "hero" and receipt["source_id"] == "hero"
    assert receipt["serial"] == 1 and receipt["position"] == [5, 0, 0]
    _run("move_srd_conjure_animals_pack", state, actor_state="hero", destination=[20, 0, 0])
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["pack_movement_receipt"] is None
    assert _position(state, source["anchor_id"])["position"] == [20, 0, 0]
    assert _position(state, "hero")["position"] == [5, 0, 0]
    assert state[f"{SRD}.entities"]["hero"]["scene_movement_spent"] == 5
    assert state[f"{SRD}.entities"]["guard"]["hp"] < 1000
    _reject(state, "move_srd_conjure_animals_pack", actor_state="hero", destination=[30, 0, 0])


def test_animals_pack_moves_within_an_existing_visible_occupant_and_forces_once():
    state, _, _ = _animals_cast()
    _position(state, "guard")["position"] = [20, 0, 0]
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[5, 0, 0])
    _run("move_srd_conjure_animals_pack", state, actor_state="hero", destination=[40, 0, 0])
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert _position(state, source["anchor_id"])["position"] == [40, 0, 0]
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] < 1000 and guard["last_conjure_animals_save"]["applied_damage"] > 0
    hp = guard["hp"]
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[10, -2, 0])
    _run("move_srd_conjure_animals_pack", state, actor_state="hero", destination=[30, 0, 0])
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp


@pytest.mark.parametrize(
    "change", ["tick", "other_turn", "occupied", "unseen", "distance", "passable_creature"]
)
def test_animals_pack_rejects_stale_or_unqualified_destinations_without_consuming_receipt(change):
    state, _, _ = _animals_cast()
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[5, 0, 0])
    destination = [20, 0, 0]
    if change == "tick":
        state[f"{SRD}.state"]["lasting_tick"] += 1
    elif change == "other_turn":
        state[f"{SRD}.combat"].update(
            encounter_active=True, turn_active=True, active_actor="guard", turn_sequence=2
        )
    elif change == "occupied":
        destination = [10, 0, 0]
    elif change == "unseen":
        state[f"{SRD}.state"]["spatial_scene"]["zones"] = []
    elif change == "passable_creature":
        _position(state, "other")["movement"] = "passable"
        _position(state, "other")["position"] = list(destination)
    else:
        destination = [61, 0, 0]
    _reject(state, "move_srd_conjure_animals_pack", actor_state="hero", destination=destination)


def test_animals_another_creature_movement_does_not_grant_the_caster_a_receipt():
    state, _, _ = _animals_cast()
    _run("move_srd_scene_creature", state, actor_state="other", destination=[30, 3, 0])
    _reject(state, "move_srd_conjure_animals_pack", actor_state="hero", destination=[20, 0, 0])


def test_animals_caster_damage_ends_source_and_stops_later_pack_segments():
    state, _, _ = _animals_cast(targets=("hero",))
    hero = state[f"{SRD}.entities"]["hero"]
    hero.update(hp=1, temp_hp=0)
    hero["saving_throw_modifiers"] = {"dexterity": -100}
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[5, 0, 0])
    _run("move_srd_conjure_animals_pack", state, actor_state="hero", destination=[18, 0, 0])
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    pack = _position(state, source["anchor_id"])
    assert state[f"{SRD}.entities"]["hero"]["hp"] == 0
    assert not source["active"] and not pack["active"]
    assert pack["position"][0] == pytest.approx(20.4)


def test_animals_caster_turn_receipt_and_actual_end_turn_share_once_per_turn():
    state, _, _ = _animals_cast()
    state[f"{SRD}.combat"].update(
        encounter_active=True,
        turn_active=True,
        active_actor="hero",
        movement_remaining=30,
        turn_sequence=4,
    )
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[5, 0, 0])
    receipt = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["pack_movement_receipt"]
    assert receipt["clock"] == "combat" and receipt["turn"] == 4
    _run("move_srd_conjure_animals_pack", state, actor_state="hero", destination=[20, 0, 0])
    hp = state[f"{SRD}.entities"]["guard"]["hp"]
    _run("resolve_srd_environment_hazard_end_turn", state, target_state="guard")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp
    state[f"{SRD}.combat"]["turn_sequence"] = 5
    _run("resolve_srd_environment_hazard_end_turn", state, target_state="guard")
    assert state[f"{SRD}.entities"]["guard"]["hp"] < hp


@pytest.mark.parametrize("damage_type", ["acid", "cold", "fire", "lightning"])
def test_minor_roll_only_component_uses_actual_slot_body_and_typed_critical(damage_type):
    state, _, _ = _minor_cast(slot=6)
    _position(state, "guard")["position"] = [15.8, 0, 0]
    hp_before = state[f"{SRD}.entities"]["guard"]["hp"]
    result = _run(
        "roll_srd_conjure_minor_damage_component",
        state,
        caster_state="hero",
        target_state="guard",
        critical_hit=True,
        damage_type=damage_type,
    ).result["context"]
    assert result["_conjure_minor_qualifies"] and result["_conjure_minor_type"] == damage_type
    assert result["_conjure_minor_roll"]["count"] == 8
    assert result["_conjure_minor_amount"] == result["_conjure_minor_roll"]["total"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp_before
    _position(state, "guard")["position"] = [15.8001, 0, 0]
    result = _run(
        "roll_srd_conjure_minor_damage_component",
        state,
        caster_state="hero",
        target_state="guard",
        damage_type=damage_type,
    ).result["context"]
    assert not result["_conjure_minor_qualifies"] and result["_conjure_minor_amount"] == 0


def test_minor_ground_cost_uses_actual_enemy_relation_and_union_then_source_removal():
    state, _, _ = _minor_cast()
    enemy = _run("move_srd_scene_creature", state, actor_state="guard", destination=[10, 0, 0])
    assert enemy.result["context"]["_scene_movement_cost"] == pytest.approx(15.4)
    _position(state, "other")["position"] = [20, 4, 0]
    unrelated = _run("move_srd_scene_creature", state, actor_state="other", destination=[10, 4, 0])
    assert unrelated.result["context"]["_scene_movement_cost"] == 10
    _run("end_concentration", state, caster_state="hero")
    ordinary = _run("move_srd_scene_creature", state, actor_state="guard", destination=[5, 0, 0])
    assert ordinary.result["context"]["_scene_movement_cost"] == 5
    result = _run(
        "roll_srd_conjure_minor_damage_component", state, caster_state="hero", target_state="guard"
    ).result["context"]
    assert not result["_conjure_minor_qualifies"]


def test_minor_ground_follows_actual_caster_and_preserves_existing_difficult_union():
    state, _, _ = _minor_cast()
    _position(state, "hero")["position"] = [0, 6, 0]
    _run(
        "cast_spell_environment",
        state,
        caster_state="other",
        spell="grease",
        slot_level=1,
        concentration_save_modifier=0,
        save_dc=18,
        origin=[15, 0, 0],
    )
    moved = _run("move_srd_scene_creature", state, actor_state="guard", destination=[10, 0, 0])
    assert moved.result["context"]["_scene_movement_cost"] == pytest.approx(20)
    assert _position(state, "guard")["position"] == [10, 0, 0]


@pytest.mark.parametrize("mode,plane", [("fly", "material"), ("walk", "ethereal")])
def test_minor_ground_does_not_charge_flight_or_another_actual_plane(mode, plane):
    state, _, _ = _minor_cast()
    state[f"{SRD}.entities"]["guard"]["flying_speed"] = 30
    _position(state, "guard")["metadata"]["plane"] = plane
    moved = _run(
        "move_srd_scene_creature", state, actor_state="guard", destination=[10, 0, 0], mode=mode
    )
    assert moved.result["context"]["_scene_movement_cost"] == 10


def test_minor_unknown_ground_and_unresolved_shadow_reject_the_movement_atomically():
    state, _, _ = _minor_cast()
    floor = state[f"{SRD}.state"]["spatial_scene"]["geometry"].pop()
    _reject(state, "move_srd_scene_creature", actor_state="guard", destination=[10, 0, 0])
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(floor)
    wall = deepcopy(floor)
    wall.update(id="shadow", volume={"kind": "box", "min": [3, 5, -1], "max": [4, 6, 8]})
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(wall)
    _reject(state, "move_srd_scene_creature", actor_state="guard", destination=[10, 0, 0])


def test_public_minor_cast_enemy_ground_reopens_retries_and_ends_with_actual_source(
    tmp_path, monkeypatch
):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as public_casting

    monkeypatch.setattr(public_casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _name: _minor_case())
    database = tmp_path / "minor-ground.db"

    def command(action, tool, parameters, revision):
        return ActionCommand(
            campaign_id="casting",
            action_id=action,
            idempotency_key=action,
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=revision,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name=tool,
            parameters=parameters,
        )

    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public_casting._create_spell_campaign(owner, "conjure_minor_elementals")
        cast_command = command("minor-cast", "cast_spell_outside_combat", parameters, 0)
        cast = StatelessMcpMechanics(owner).execute_action(cast_command)
        assert cast.status is ActionStatus.COMMITTED
        after_cast = owner.snapshot("casting")
        assert after_cast.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
        assert after_cast.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == after_cast.state
        assert StatelessMcpMechanics(owner).execute_action(cast_command).result == cast.result
        move_command = command(
            "minor-ground-enter",
            "move_srd_scene_creature",
            {"actor_state": "guard", "destination": [10, 0, 0]},
            after_cast.revision,
        )
        moved = StatelessMcpMechanics(owner).execute_action(move_command)
        assert moved.status is ActionStatus.COMMITTED
        assert moved.result["context"]["_scene_movement_cost"] == pytest.approx(15.4)
        after_move = owner.snapshot("casting")
        assert StatelessMcpMechanics(owner).execute_action(move_command).result == moved.result
        assert owner.snapshot("casting").state == after_move.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == after_move.state
        assert StatelessMcpMechanics(owner).execute_action(move_command).result == moved.result
        ended = StatelessMcpMechanics(owner).execute_action(
            command(
                "minor-end",
                "end_concentration",
                {"caster_state": "hero"},
                after_move.revision,
            )
        )
        assert ended.status is ActionStatus.COMMITTED
        after_end = owner.snapshot("casting")
        ordinary = StatelessMcpMechanics(owner).execute_action(
            command(
                "minor-ground-cleared",
                "move_srd_scene_creature",
                {"actor_state": "guard", "destination": [5, 0, 0]},
                after_end.revision,
            )
        )
        assert ordinary.status is ActionStatus.COMMITTED
        assert ordinary.result["context"]["_scene_movement_cost"] == 5


def test_woodland_body_membership_uses_real_footprints_height_plane_and_current_anchor():
    state, _, _ = _woodland_cast()
    _position(state, "hero")["footprint"]["half_extents"] = [5, 5]
    _position(state, "guard")["footprint"]["half_extents"] = [2.5, 2.5]
    for point, inside in [
        ([17.5, 0, 0], True),
        ([17.50001, 0, 0], False),
        ([14.5, 14.5, 0], True),
        ([15, 15, 0], False),
        ([0, 0, 16], True),
        ([0, 0, 16.00001], False),
    ]:
        _position(state, "guard")["position"] = point
        result = _run(
            "qualify_srd_environment_emanation_target",
            state,
            caster_state="hero",
            target_state="guard",
            area_id="hero",
        )
        assert result.result["context"]["_emanation_inside"] is inside
    _position(state, "hero")["position"] = [30, 0, 0]
    _position(state, "guard")["position"] = [47.5, 0, 0]
    assert _run(
        "qualify_srd_environment_emanation_target",
        state,
        caster_state="hero",
        target_state="guard",
        area_id="hero",
    ).result["context"]["_emanation_inside"]
    _position(state, "guard")["metadata"]["plane"] = "ethereal"
    assert not _run(
        "qualify_srd_environment_emanation_target",
        state,
        caster_state="hero",
        target_state="guard",
        area_id="hero",
    ).result["context"]["_emanation_inside"]


def test_woodland_entry_damages_once_per_turn_then_real_end_turn_can_damage_next_turn():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _woodland_cast()
    _turn(state, "guard")
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[5, 0, 0])
    guard = state[f"{SRD}.entities"]["guard"]
    hp = guard["hp"]
    assert hp < 1000 and guard["last_woodland_save"]["raw_damage"]["count"] == 5
    assert _position(state, "guard")["position"] == [5, 0, 0]
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp
    _turn(state, "guard")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["hp"] < hp


def test_woodland_caster_movement_enters_selected_spaces_and_preserves_total_cost():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, _ = _woodland_cast()
    _position(state, "other")["position"] = [30, 4, 0]
    _turn(state, "hero", data)
    moved = _run("move_srd_scene_creature", state, actor_state="hero", destination=[30, 0, 0])
    assert moved.result["context"]["_scene_movement_cost"] == pytest.approx(30)
    assert _position(state, "hero")["position"] == [30, 0, 0]
    assert all(state[f"{SRD}.entities"][actor]["hp"] < 1000 for actor in ("guard", "other"))
    assert state[f"{SRD}.entities"]["hero"]["hp"] > 0


def test_woodland_lethal_entry_stops_committed_movement_at_the_actual_boundary():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _woodland_cast()
    state[f"{SRD}.entities"]["guard"]["hp"] = 1
    _turn(state, "guard")
    moved = _run("move_srd_scene_creature", state, actor_state="guard", destination=[5, 0, 0])
    assert _position(state, "guard")["position"] == pytest.approx([10.8, 0, 0])
    assert moved.result["context"]["_scene_movement_cost"] == pytest.approx(9.2)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 0


def test_woodland_optional_targets_sight_upcast_and_save_halving_use_actual_sources():
    state, _, _ = _woodland_cast(slot=6, targets=())
    _position(state, "guard")["position"] = [5, 0, 0]
    _run("resolve_srd_woodland_event", state, target_state="guard", area_id="hero")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    _run("select_srd_environment_targets", state, caster_state="hero", targets=["guard", "guard"])
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = 100
    _position(state, "guard")["metadata"]["lighting"] = "darkness"
    _run("resolve_srd_woodland_event", state, target_state="guard", area_id="hero")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    _position(state, "guard")["metadata"]["lighting"] = "bright"
    _run("resolve_srd_woodland_event", state, target_state="guard", area_id="hero")
    saved = state[f"{SRD}.entities"]["guard"]["last_woodland_save"]
    assert saved["raw_damage"]["count"] == 7 and saved["outcome"] == "success"
    assert saved["applied_damage"] == saved["raw_damage"]["total"] // 2
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["force_target_ids"] == ["guard"]


def test_woodland_disengage_consumes_bonus_budget_and_current_source_is_required():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, _ = _woodland_cast()
    _turn(state, "hero", data)
    _run("disengage_srd_conjure_woodland", state, actor_state="hero")
    assert state[f"{SRD}.combat"]["disengaged_actor"] == "hero"
    assert not state[f"{SRD}.entities"]["hero"]["bonus_action_available"]
    _reject(state, "disengage_srd_conjure_woodland", actor_state="hero")
    _run("end_concentration", state, caster_state="hero")
    _reject(state, "disengage_srd_conjure_woodland", actor_state="hero")


def test_woodland_dispel_uses_current_creature_space_then_removes_real_damage_source():
    state, _, _ = _woodland_cast()
    _position(state, "hero")["position"] = [100, 0, 0]
    _reject(
        state,
        "dispel_srd_environment_source",
        caster_state="other",
        area_id="hero",
        slot_level=4,
        casting_modifier=4,
        casting_ability="wisdom",
        point=[0, 0, 0],
    )
    _run(
        "dispel_srd_environment_source",
        state,
        caster_state="other",
        area_id="hero",
        slot_level=4,
        casting_modifier=4,
        casting_ability="wisdom",
        point=[110.4, 0, 0],
    )
    assert not state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    _position(state, "guard")["position"] = [100, 5, 0]
    _run("resolve_srd_woodland_event", state, target_state="guard", area_id="hero")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000


@pytest.mark.parametrize("second_level", [4, 5])
def test_woodland_overlapping_casts_apply_only_the_latest_tie_or_stronger_effect(second_level):
    state, _, _ = _woodland_cast()
    _position(state, "guard")["position"] = [5, 0, 0]
    _position(state, "other")["position"] = [0, 4, 0]
    _run(
        "cast_spell_environment",
        state,
        caster_state="other",
        spell="conjure_woodland_beings",
        slot_level=second_level,
        concentration_save_modifier=0,
        save_dc=18,
        origin=[0, 4, 0],
        force_targets=["guard"],
    )
    _run("resolve_srd_woodland_event", state, target_state="guard", area_id="hero")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    _run("resolve_srd_woodland_event", state, target_state="guard", area_id="other")
    result = state[f"{SRD}.entities"]["guard"]["last_woodland_save"]
    assert result["area_id"] == "other" and result["raw_damage"]["count"] == 5 + second_level - 4
    hp = state[f"{SRD}.entities"]["guard"]["hp"]
    _run("resolve_srd_woodland_event", state, target_state="guard", area_id="hero")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == hp
    _run("end_concentration", state, caster_state="other")
    _run("resolve_srd_woodland_event", state, target_state="guard", area_id="hero")
    assert state[f"{SRD}.entities"]["guard"]["hp"] < hp


def test_woodland_lifetime_total_cover_origin_exclusion_and_unsupported_curves_are_honest():
    state, _, _ = _woodland_cast(targets=("hero", "guard"))
    _position(state, "guard")["position"] = [5, 0, 0]
    _run("resolve_srd_woodland_event", state, target_state="hero", area_id="hero")
    assert "last_woodland_save" not in state[f"{SRD}.entities"]["hero"]
    _ground(state)
    wall = deepcopy(state[f"{SRD}.state"]["spatial_scene"]["geometry"][0])
    wall.update(id="wall", volume={"kind": "box", "min": [2, -20, -20], "max": [3, 20, 20]})
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(wall)
    _run("resolve_srd_woodland_event", state, target_state="guard", area_id="hero")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].pop()
    _position(state, "guard")["footprint"] = {"kind": "circle", "radius": 1}
    _reject(state, "resolve_srd_woodland_event", target_state="guard", area_id="hero")
    _position(state, "guard")["footprint"] = {"kind": "box", "half_extents": [0.4, 0.4]}
    _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    assert not state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    _run("resolve_srd_woodland_event", state, target_state="guard", area_id="hero")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000


@pytest.mark.parametrize("ray", [False, True])
def test_woodland_shared_packet_preserves_actual_curse_half_remainder_and_one_penalty(
    monkeypatch, ray
):
    from sylvia_gm import dice
    from tests.srd_full.test_srd_spell_damage_consumers import _enfeeble, _record_rolls
    from tests.srd_full.test_srd_spell_wave3_afflictions import _cast as _curse_cast

    state, data, _, _ = _curse_cast(
        monkeypatch, "bestow_curse", curse_mode="damage", slot_resource="spell_slot_5"
    )
    _admit_scene(state)
    state[f"{SRD}.entities"]["guard"]["temp_hp"] = 0
    data["classes"] = [{"id": "druid", "level": 10}, {"id": "wizard", "level": 7}]
    data["spellcasting_grants"].append(
        {"source": "druid", "ability": "wisdom", "spells": ["conjure_woodland_beings"]}
    )
    state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("conjure_woodland_beings")
    _, _, parameters = _woodland_case()
    if ray:
        _enfeeble(state, data)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    _position(state, "guard")["position"] = [5, 0, 0]
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = 100
    hp_before = state[f"{SRD}.entities"]["guard"]["hp"]

    class OddActualDice:
        def randint(self, low, high):
            assert low == 1
            return 1 if high == 8 else 10

    monkeypatch.setattr(dice, "_rng", OddActualDice())
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    rolls = _record_rolls(monkeypatch)
    _run("resolve_srd_woodland_event", state, target_state="guard", area_id="hero")
    raw = state[f"{SRD}.entities"]["guard"]["last_woodland_save"]
    assert raw["raw_damage"]["total"] == 5 and raw["outcome"] == "success"
    assert state[f"{SRD}.entities"]["guard"]["last_curse_damage_roll"]["total"] == 1
    assert hp_before - state[f"{SRD}.entities"]["guard"]["hp"] == (5 + 1 - int(ray)) // 2
    assert len([r for r in rolls if r.sides == 8 and r.count == 1]) == 1 + int(ray)


def _woodland_test_archive():
    """Admit the authored runtime and its new support declarations before root regeneration."""
    import json

    import yaml
    from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
    from scripts.srd_full.srd_spell_environment import PUBLIC_TOOLS

    from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
    from sylvia_gm.application.package_materialization import NormalizedPackageDraft
    from sylvia_gm.domain.package_library import canonical_json_bytes

    primary, reference = _srd_drafts()
    runtime = yaml.load(primary.payloads["runtime/ruleset.yaml"], Loader=yaml.CSafeLoader)
    record = deepcopy(primary.record)
    declarations = {tool["name"]: tool for tool in record["tools"]}
    for name in declarations.keys() | PUBLIC_TOOLS:
        schema = _tool_schema(runtime["tools"][name], tool_name=name)
        if name in declarations:
            declarations[name]["input_schema"] = schema
        else:
            record["tools"].append(
                {
                    "name": name,
                    "capability": "dnd.srd.5.2.1.spells",
                    "input_schema": schema,
                    "writers": ["dm"],
                    "turn_statuses": ["open"],
                    "state_predicates": [],
                }
            )
    payload = dict(primary.payloads)
    content = json.loads(payload["content/content-declarations.json"])
    content["inclusions"].append(
        {
            "id": "synthetic-environment-source-fixture",
            "kind": "ruleset_runtime",
            "family": "synthetic-environment-fixture",
            "disposition": "included_mechanical",
            "classifications": ["sylvia_gm_implementation"],
            "sources": [],
            "links": {
                "artifacts": ["runtime/ruleset.yaml"],
                "capabilities": ["dnd.srd.5.2.1.spells"],
                "mechanics": [],
                "procedures": [],
                "prompt_fragments": [],
                "schemas": [],
                "state_namespaces": [],
                "tools": sorted(PUBLIC_TOOLS),
                "ui_surfaces": [],
            },
            "evidence": {"tests": ["synthetic-woodland-public-cast"]},
        }
    )
    payload["content/content-declarations.json"] = canonical_json_bytes(content)
    return _archive((NormalizedPackageDraft(record, payload, primary.format_version), reference))


def test_public_woodland_cast_entry_reopens_and_retry_preserves_source_cost_and_damage(
    tmp_path, monkeypatch
):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as public_casting

    monkeypatch.setattr(public_casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(public_casting, "_spell_case", lambda _name: _woodland_case())
    database = tmp_path / "woodland.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public_casting._create_spell_campaign(
            owner, "conjure_woodland_beings", target_overrides={"hp": 1000, "temp_hp": 0}
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="woodland-cast",
            idempotency_key="woodland-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        cast = StatelessMcpMechanics(owner).execute_action(command)
        assert cast.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert StatelessMcpMechanics(owner).execute_action(command).result == cast.result
        move = ActionCommand(
            campaign_id="casting",
            action_id="woodland-enter",
            idempotency_key="woodland-enter",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=saved.revision,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "guard", "destination": [5, 0, 0]},
        )
        moved = StatelessMcpMechanics(owner).execute_action(move)
        assert moved.status is ActionStatus.COMMITTED
        after_move = owner.snapshot("casting")
        assert after_move.state[f"{SRD}.entities"]["guard"]["hp"] < 1000
        assert _position(after_move.state, "guard")["position"] == [5, 0, 0]
        assert StatelessMcpMechanics(owner).execute_action(move).result == moved.result
        assert owner.snapshot("casting").state == after_move.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == after_move.state
        assert StatelessMcpMechanics(owner).execute_action(move).result == moved.result


def test_public_moonbeam_paid_cast_real_magic_movement_crossed_save_reopen_retry(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as public_casting

    def fixture(_name):
        state, data, parameters = _moonbeam_case()
        _position(state, "guard")["position"] = [60, 0, 30]
        return state, data, parameters

    monkeypatch.setattr(public_casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(public_casting, "_spell_case", fixture)
    database = tmp_path / "moonbeam.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public_casting._create_spell_campaign(
            owner,
            "moonbeam",
            entity_overrides={"initiative_modifier": 20},
            target_overrides={"hp": 1000, "temp_hp": 0, "initiative_modifier": 0},
        )
        cast_command = ActionCommand(
            campaign_id="casting",
            action_id="moon-cast",
            idempotency_key="moon-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        mechanics = StatelessMcpMechanics(owner)
        cast = mechanics.execute_action(cast_command)
        assert cast.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
        assert mechanics.execute_action(cast_command).result == cast.result
        assert owner.snapshot("casting").state == saved.state
        started = mechanics.execute_action(
            replace(
                cast_command,
                action_id="moon-encounter",
                idempotency_key="moon-encounter",
                expected_revision=saved.revision,
                tool_name="start_combat_encounter_entities",
                parameters={
                    "first_actor": "hero",
                    "second_actor": "guard",
                    "first_surprised": False,
                    "second_surprised": False,
                    "tie_winner": "first",
                },
            )
        )
        assert started.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        began = mechanics.execute_action(
            replace(
                cast_command,
                action_id="moon-begin",
                idempotency_key="moon-begin",
                expected_revision=saved.revision,
                tool_name="begin_combat_turn",
                parameters={},
            )
        )
        assert began.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        move_command = replace(
            cast_command,
            action_id="moon-move",
            idempotency_key="moon-move",
            expected_revision=saved.revision,
            tool_name="move_srd_moonbeam_source",
            parameters={"caster_state": "hero", "area_id": "hero", "destination": [80, 0, 0]},
        )
        moved = mechanics.execute_action(move_command)
        assert moved.status is ActionStatus.COMMITTED
        moved_snapshot = owner.snapshot("casting")
        assert moved_snapshot.state[f"{SRD}.entities"]["guard"]["hp"] < 1000
        assert moved_snapshot.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["origin"] == [
            80,
            0,
            0,
        ]
        assert not moved_snapshot.state[f"{SRD}.combat"]["action_available"]
        assert mechanics.execute_action(move_command).result == moved.result
        assert owner.snapshot("casting").state == moved_snapshot.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == moved_snapshot.state
        assert StatelessMcpMechanics(owner).execute_action(move_command).result == moved.result
        assert owner.snapshot("casting").state == moved_snapshot.state


def test_public_stinking_cloud_paid_cast_actual_start_poison_action_rejection_reopen_retry_end(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from sylvia_gm.domain.errors import MechanicsRejected
    from tests import test_srd_public_spell_casting as public_casting

    def fixture(_name):
        state, data, parameters = _stinking_case()
        _position(state, "hero")["position"] = [40, 0, 0]
        return state, data, parameters

    monkeypatch.setattr(public_casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(public_casting, "_spell_case", fixture)
    database = tmp_path / "stinking-cloud.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public_casting._create_spell_campaign(
            owner,
            "stinking_cloud",
            entity_overrides={"saving_throw_modifiers": {"constitution": -100}},
            target_overrides={"hp": 1000, "temp_hp": 0},
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="stinking-cast",
            idempotency_key="stinking-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        mechanics = StatelessMcpMechanics(owner)
        cast = mechanics.execute_action(command)
        assert cast.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
        assert "poisoned" not in saved.state[f"{SRD}.entities"]["hero"]["conditions"]
        assert mechanics.execute_action(command).result == cast.result
        assert owner.snapshot("casting").state == saved.state
        started = mechanics.execute_action(
            replace(
                command,
                action_id="stinking-encounter",
                idempotency_key="stinking-encounter",
                expected_revision=saved.revision,
                tool_name="start_combat_encounter_entities",
                parameters={
                    "first_actor": "hero",
                    "second_actor": "guard",
                    "first_surprised": False,
                    "second_surprised": False,
                    "tie_winner": "first",
                },
            )
        )
        assert started.status is ActionStatus.COMMITTED
        start_snapshot = owner.snapshot("casting")
        beginning = replace(
            command,
            action_id="stinking-begin",
            idempotency_key="stinking-begin",
            expected_revision=start_snapshot.revision,
            tool_name="begin_combat_turn",
            parameters={},
        )
        began = mechanics.execute_action(beginning)
        assert began.status is ActionStatus.COMMITTED
        affected = owner.snapshot("casting")
        assert affected.state[f"{SRD}.combat"]["active_actor"] == "hero"
        assert "poisoned" in affected.state[f"{SRD}.entities"]["hero"]["conditions"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == affected.state
        rejected = replace(
            command,
            action_id="stinking-dash",
            idempotency_key="stinking-dash",
            expected_revision=affected.revision,
            tool_name="dash_combat_action",
            parameters={"actor": "hero", "actor_state": "hero"},
        )
        with pytest.raises(MechanicsRejected, match="Stinking Cloud poison"):
            mechanics.execute_action(rejected)
        assert owner.snapshot("casting").state == affected.state
        removed = mechanics.execute_action(
            replace(
                rejected,
                action_id="stinking-source-end",
                idempotency_key="stinking-source-end",
                tool_name="end_concentration",
                parameters={"caster_state": "hero"},
            )
        )
        assert removed.status is ActionStatus.COMMITTED
        source_ended = owner.snapshot("casting")
        assert not source_ended.state[f"{SRD}.state"]["has_spell_environments"]
        assert "poisoned" in source_ended.state[f"{SRD}.entities"]["hero"]["conditions"]
        ending = replace(
            command,
            action_id="stinking-turn-end",
            idempotency_key="stinking-turn-end",
            expected_revision=source_ended.revision,
            tool_name="end_combat_turn",
            parameters={},
        )
        ended = mechanics.execute_action(ending)
        assert ended.status is ActionStatus.COMMITTED
        closed = owner.snapshot("casting")
        assert "poisoned" not in closed.state[f"{SRD}.entities"]["hero"]["conditions"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == closed.state


@pytest.mark.parametrize("spell", ["insect_plague", "cloudkill"])
def test_public_insects_paid_cast_entry_terrain_reopen_retry_and_expiry(
    tmp_path, monkeypatch, spell
):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as public_casting

    monkeypatch.setattr(public_casting, "bundled_srd_archive", _woodland_test_archive)
    factory = _insects_case if spell == "insect_plague" else _cloud_case
    monkeypatch.setattr(public_casting, "_spell_case", lambda _name: factory())
    database = tmp_path / "insects.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public_casting._create_spell_campaign(
            owner, spell, target_overrides={"hp": 1000, "temp_hp": 0}
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="insects-cast",
            idempotency_key="insects-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        cast = StatelessMcpMechanics(owner).execute_action(command)
        assert cast.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(command).result == cast.result
        entry = replace(
            command,
            action_id="insects-entry",
            idempotency_key="insects-entry",
            expected_revision=saved.revision,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "guard", "destination": [25, 0, 0]},
        )
        entered = mechanics.execute_action(entry)
        assert entered.status is ActionStatus.COMMITTED
        affected = owner.snapshot("casting")
        guard = affected.state[f"{SRD}.entities"]["guard"]
        assert guard["hp"] < 1000
        expected_cost = 15 + (25 - 40 + (400 - 9) ** 0.5) if spell == "insect_plague" else 15
        assert guard["scene_movement_spent"] == pytest.approx(expected_cost)
        assert guard["last_environment_sphere_save"]["spell"] == spell
        assert mechanics.execute_action(entry).result == entered.result
        assert owner.snapshot("casting").state == affected.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == affected.state
        assert mechanics.execute_action(entry).result == entered.result
        ended = mechanics.execute_action(
            replace(
                command,
                action_id="insects-expiry",
                idempotency_key="insects-expiry",
                expected_revision=affected.revision,
                tool_name="advance_srd_lasting_time",
                parameters={"elapsed_rounds": 100},
            )
        )
        assert ended.status is ActionStatus.COMMITTED
        closed = owner.snapshot("casting")
        assert not closed.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
        later = mechanics.execute_action(
            replace(
                entry,
                action_id="insects-after",
                idempotency_key="insects-after",
                expected_revision=closed.revision,
                parameters={"actor_state": "guard", "destination": [30, 0, 0]},
            )
        )
        assert later.status is ActionStatus.COMMITTED
        after = owner.snapshot("casting")
        assert after.state[f"{SRD}.entities"]["guard"]["hp"] == guard["hp"]
        assert after.state[f"{SRD}.entities"]["guard"]["scene_movement_spent"] == 5


def test_public_elemental_paid_cast_entry_restraint_reopen_retry_and_expiry(tmp_path, monkeypatch):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as public_casting

    monkeypatch.setattr(public_casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(
        public_casting, "_spell_case", lambda _name: _elemental_case(element="fire")
    )
    database = tmp_path / "elemental.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public_casting._create_spell_campaign(
            owner, "conjure_elemental", target_overrides={"hp": 1000, "temp_hp": 0}
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="elemental-cast",
            idempotency_key="elemental-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        cast = StatelessMcpMechanics(owner).execute_action(command)
        assert cast.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(command).result == cast.result
        entry = replace(
            command,
            action_id="elemental-entry",
            idempotency_key="elemental-entry",
            expected_revision=saved.revision,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "guard", "destination": [30, 0, 0]},
        )
        entered = mechanics.execute_action(entry)
        assert entered.status is ActionStatus.COMMITTED
        affected = owner.snapshot("casting")
        creature = affected.state[f"{SRD}.entities"]["guard"]
        assert creature["hp"] < 1000 and "restrained" in creature["conditions"]
        assert creature["scene_movement_spent"] == pytest.approx(14.600001)
        assert mechanics.execute_action(entry).result == entered.result
        assert owner.snapshot("casting").state == affected.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == affected.state
        assert mechanics.execute_action(entry).result == entered.result
        ended = mechanics.execute_action(
            replace(
                command,
                action_id="elemental-expiry",
                idempotency_key="elemental-expiry",
                expected_revision=affected.revision,
                tool_name="advance_srd_lasting_time",
                parameters={"elapsed_rounds": 100},
            )
        )
        assert ended.status is ActionStatus.COMMITTED
        final = owner.snapshot("casting")
        assert "restrained" not in final.state[f"{SRD}.entities"]["guard"]["conditions"]
        source = final.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
        assert not _position(final.state, source["anchor_id"])["active"]


def test_public_animals_paid_cast_pack_movement_reopens_and_exact_retries(tmp_path, monkeypatch):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as public_casting

    monkeypatch.setattr(public_casting, "bundled_srd_archive", _woodland_test_archive)

    def passable_fixture(_name):
        state, data, parameters = _animals_case()
        _position(state, "other")["movement"] = "passable"
        return state, data, parameters

    monkeypatch.setattr(public_casting, "_spell_case", passable_fixture)
    database = tmp_path / "animals.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public_casting._create_spell_campaign(
            owner, "conjure_animals", target_overrides={"hp": 1000, "temp_hp": 0}
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="animals-cast",
            idempotency_key="animals-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        cast = StatelessMcpMechanics(owner).execute_action(command)
        assert cast.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(command).result == cast.result
        caster_move = replace(
            command,
            action_id="animals-caster-move",
            idempotency_key="animals-caster-move",
            expected_revision=saved.revision,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "hero", "destination": [5, 0, 0]},
        )
        moved = mechanics.execute_action(caster_move)
        assert moved.status is ActionStatus.COMMITTED
        ready = owner.snapshot("casting")
        assert ready.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"][
            "pack_movement_receipt"
        ]["position"] == [5, 0, 0]
        assert mechanics.execute_action(caster_move).result == moved.result
        assert owner.snapshot("casting").state == ready.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == ready.state
        pack_move = replace(
            command,
            action_id="animals-pack-move",
            idempotency_key="animals-pack-move",
            expected_revision=ready.revision,
            tool_name="move_srd_conjure_animals_pack",
            parameters={"actor_state": "hero", "destination": [20, 0, 0]},
        )
        pack_moved = mechanics.execute_action(pack_move)
        assert pack_moved.status is ActionStatus.COMMITTED
        after = owner.snapshot("casting")
        source = after.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
        assert _position(after.state, source["anchor_id"])["position"] == [20, 0, 0]
        assert after.state[f"{SRD}.entities"]["guard"]["hp"] < 1000
        assert source["pack_movement_receipt"] is None
        assert mechanics.execute_action(pack_move).result == pack_moved.result
        assert owner.snapshot("casting").state == after.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == after.state
        assert mechanics.execute_action(pack_move).result == pack_moved.result
        ended = mechanics.execute_action(
            replace(
                command,
                action_id="animals-end",
                idempotency_key="animals-end",
                expected_revision=after.revision,
                tool_name="end_concentration",
                parameters={"caster_state": "hero"},
            )
        )
        assert ended.status is ActionStatus.COMMITTED
        closed = owner.snapshot("casting")
        assert not _position(closed.state, source["anchor_id"])["active"]


def _position(state, actor):
    return next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == actor)


def _destroy_fog(state, *, origin=(0, 0, 0), face="east", slot=1):
    """Use the actual Water owner's source Cube and nested saved-record consumer."""
    return _run(
        "cast_spell_object_content_spell",
        state,
        caster_state="hero",
        spell="create_or_destroy_water",
        slot_level=slot,
        object_id="",
        object_mode="destroy_fog",
        object_point=list(origin),
        object_cube_face=face,
    )


def _fog_point(state, point, plane="material"):
    return _run(
        "resolve_srd_environment_point",
        state,
        point=list(point),
        plane=plane,
    ).result["context"]["_environment_fog"]


@pytest.mark.parametrize(
    "face,axis,direction",
    [
        ("east", 0, 1),
        ("west", 0, -1),
        ("north", 1, 1),
        ("south", 1, -1),
        ("up", 2, 1),
        ("down", 2, -1),
    ],
)
def test_destroy_water_preserves_outside_fog_for_each_actual_source_face(face, axis, direction):
    origin = [0, 0, 0]
    origin[axis] = direction * 10
    state, _, _ = _cast(origin=origin)
    _destroy_fog(state, face=face)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    clearance = source["fog_clearances"][0]
    assert clearance["water_source"] in state[f"{SRD}.state"]["turn_effects"]
    assert clearance["water_source"]["source_sha256"] == (
        "564c41de7bcb0225e84a7af1d5b36062c0f42ee3be59dba253a51a3502d3ad9d"
    )
    assert source["active"] and state[f"{SRD}.entities"]["hero"]["concentration_spell_id"]
    assert not _fog_point(state, origin)
    outside = [0, 0, 0]
    outside[axis] = -direction * 5
    assert _fog_point(state, outside)


def test_destroy_water_clearance_union_changes_real_sight_and_preserves_a_gap():
    state, _, _ = _cast(origin=(10, 0, 0))
    _destroy_fog(state)
    assert not _fog_point(state, [10, 0, 0])
    assert _fog_point(state, [10, 18, 0])
    relation = _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
    assert relation.result["context"]["_scene_can_see"]
    _position(state, "guard")["position"] = [-5, 0, 0]
    relation = _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
    assert not relation.result["context"]["_scene_can_see"]
    _destroy_fog(state, face="west")
    relation = _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
    assert relation.result["context"]["_scene_can_see"]
    assert _fog_point(state, [10, 18, 0])
    result = _run(
        "resolve_srd_environment_segment",
        state,
        start=[-30, 0, 0],
        end=[40, 0, 0],
        plane="material",
    )
    assert not result.result["context"]["_environment_sight_obscured"]
    # Repeating the same actual current Cube does not grow the saved interval list.
    _destroy_fog(state, face="west")
    assert len(state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["fog_clearances"]) == 2
    _run("end_concentration", state, caster_state="hero")
    assert not _fog_point(state, [10, 18, 0])
    state, _, _ = _cast(origin=(0, 0, 0))
    _destroy_fog(state, origin=(5, 0, 0))
    _destroy_fog(state, origin=(-5, 0, 0), face="west")
    result = _run(
        "resolve_srd_environment_segment",
        state,
        start=[-30, 0, 0],
        end=[40, 0, 0],
        plane="material",
    )
    assert result.result["context"]["_environment_sight_obscured"]


def test_destroy_water_uses_upcast_size_and_leaves_silence_and_other_plane_fog():
    state, _, _ = _cast(origin=(10, 0, 0))
    _position(state, "other")["metadata"]["plane"] = "ethereal"
    _run(
        "cast_spell_environment",
        state,
        caster_state="other",
        spell="fog_cloud",
        slot_level=1,
        concentration_save_modifier=0,
        origin=[10, 0, 0],
    )
    _run(
        "cast_spell_environment",
        state,
        caster_state="guard",
        spell="silence",
        slot_level=2,
        concentration_save_modifier=0,
        origin=[10, 0, 0],
    )
    _destroy_fog(state, origin=(-10, 0, 0), slot=3)
    registry = state[f"{SRD}.state"]["persistent_spell_areas"]
    assert registry["hero"]["fog_clearances"][0]["volume"] == {
        "kind": "box",
        "min": [-10, -20, -20],
        "max": [30, 20, 20],
    }
    assert not _fog_point(state, [10, 19, 0])
    assert _fog_point(state, [10, 19, 0], "ethereal")
    assert "fog_clearances" not in registry["other"] and "fog_clearances" not in registry["guard"]
    assert _run(
        "resolve_srd_environment_point",
        state,
        point=[10, 0, 0],
        plane="material",
    ).result["context"]["_environment_silence"]
    _reject(
        state,
        "dispel_srd_environment_source",
        caster_state="hero",
        caster="hero",
        area_id="hero",
        slot_level=3,
        casting_modifier=4,
        casting_ability="wisdom",
        point=[10, 0, 0],
    )


def test_destroy_water_clipped_fog_changes_the_actual_remote_sensor_path():
    state, _, _ = _cast(origin=(10, 0, 3))
    _destroy_fog(state)
    _position(state, "other")["position"] = [20, 5, 0]
    _run(
        "cast_spell_information_spell",
        state,
        caster_state="other",
        target_state="other",
        spell="arcane_eye",
        caster_can_touch_target=True,
        concentration_modifier=0,
        material_item_ids=[],
        information_position=[0, 0, 0],
        information_lighting="bright",
        information_familiar=True,
    )
    result = _run(
        "query_srd_information_spell",
        state,
        caster_state="other",
        spell="arcane_eye",
        mode="remote_perception",
    )
    assert "guard: seeing" in result.result["context"]["_info_results"]
    _position(state, "guard")["position"] = [10, 18, 0]
    result = _run(
        "query_srd_information_spell",
        state,
        caster_state="other",
        spell="arcane_eye",
        mode="remote_perception",
    )
    assert "guard: seeing" not in result.result["context"]["_info_results"]


def test_destroy_water_rejects_actual_out_of_range_cube_and_intervening_solid():
    state, _, _ = _cast()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _destroy_fog(state, origin=(31, 0, 0))
    assert state == before
    _ground(state)
    wall = deepcopy(state[f"{SRD}.state"]["spatial_scene"]["geometry"][0])
    wall.update(id="wall", volume={"kind": "box", "min": [4, -10, -1], "max": [6, 10, 10]})
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(wall)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _destroy_fog(state, origin=(10, 0, 0))
    assert state == before


def test_destroy_water_rejects_actual_damaging_fog_overlap_without_claiming_clearance():
    state, _, _ = _cloud_cast()
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="remaining creature-space owner"):
        _destroy_fog(state, origin=(20, 0, 0))
    assert state == before
    assert _fog_point(state, [40, 0, 0])


def test_destroy_water_pure_cube_sphere_tangency_preserves_actual_cloudkill():
    state, _, _ = _cloud_cast()
    _destroy_fog(state, origin=(20, 0, 0), face="west")
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["active"] and "fog_clearances" not in source
    assert _fog_point(state, [40, 0, 0])


def test_public_destroy_water_clears_partial_fog_pays_once_and_reopens(tmp_path, monkeypatch):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as public_casting

    def actual_water_fixture(spell):
        state, data, parameters = _fixture(spell)
        _run(
            "cast_spell_environment",
            state,
            caster_state="other",
            spell="fog_cloud",
            slot_level=1,
            concentration_save_modifier=0,
            origin=[10, 0, 0],
        )
        parameters.update(
            object_mode="destroy_fog", object_point=[0, 0, 0], object_cube_face="east"
        )
        return state, data, parameters

    monkeypatch.setattr(public_casting, "_spell_case", actual_water_fixture)
    database = tmp_path / "water-fog.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public_casting._create_spell_campaign(owner, "create_or_destroy_water")
        command = ActionCommand(
            campaign_id="casting",
            action_id="water-cast",
            idempotency_key="water-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        first = StatelessMcpMechanics(owner).execute_action(command)
        assert first.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
        source = saved.state[f"{SRD}.state"]["persistent_spell_areas"]["other"]
        assert source["active"] and len(source["fog_clearances"]) == 1
        assert not _fog_point(deepcopy(saved.state), [10, 0, 0])
        assert _fog_point(deepcopy(saved.state), [-5, 0, 0])
        assert StatelessMcpMechanics(owner).execute_action(command).result == first.result
        assert owner.snapshot("casting").state == saved.state
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert StatelessMcpMechanics(owner).execute_action(command).result == first.result
        assert owner.snapshot("casting").state == saved.state
        relation = ActionCommand(
            campaign_id="casting",
            action_id="water-sight",
            idempotency_key="water-sight",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=saved.revision,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="resolve_srd_scene_relation",
            parameters={"actor_state": "hero", "target_state": "guard"},
        )
        observed = StatelessMcpMechanics(owner).execute_action(relation)
        assert observed.status is ActionStatus.COMMITTED
        assert observed.result["context"]["_scene_can_see"]
        movement = ActionCommand(
            campaign_id="casting",
            action_id="remaining-fog",
            idempotency_key="remaining-fog",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=owner.snapshot("casting").revision,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "guard", "destination": [-5, 0, 0]},
        )
        assert (
            StatelessMcpMechanics(owner).execute_action(movement).status is ActionStatus.COMMITTED
        )
        remaining = ActionCommand(
            campaign_id="casting",
            action_id="remaining-sight",
            idempotency_key="remaining-sight",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=owner.snapshot("casting").revision,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="resolve_srd_scene_relation",
            parameters={"actor_state": "hero", "target_state": "guard"},
        )
        observed = StatelessMcpMechanics(owner).execute_action(remaining)
        assert observed.status is ActionStatus.COMMITTED
        assert not observed.result["context"]["_scene_can_see"]


def _reject(state, tool, **parameters):
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run(tool, state, **parameters)
    assert state == before


def _ground(state, *, plane="material"):
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(
        {
            "id": "floor",
            "map_id": "orders",
            "volume": {"kind": "box", "min": [-500, -500, -1], "max": [500, 500, 0]},
            "active": True,
            "movement": "blocking",
            "occupies_cells": False,
            "visibility": "opaque",
            "metadata": {"plane": plane, "ground": True, "material": "stone"},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )


def test_supporting_ground_uses_the_actual_surface_hit_and_floor_tangency():
    state, _, _ = _fixture()
    _reject(state, "qualify_srd_environment_ground", point=[20, 0, 0], plane="material")
    _ground(state)
    result = _run("qualify_srd_environment_ground", state, point=[20, 0, 0], plane="material")
    assert result.result["context"]["_environment_ground_id"] == "floor"
    _reject(state, "qualify_srd_environment_ground", point=[20, 0, 0.1], plane="material")
    _reject(state, "qualify_srd_environment_ground", point=[20, 0, 0], plane="ethereal")
    result = _run("srd_information_barriers", state, start=[0, 0, 0], end=[20, 0, 0], rule="solid")
    assert result.result["context"]["_information_blocked"] is False
    wall = deepcopy(state[f"{SRD}.state"]["spatial_scene"]["geometry"][0])
    wall.update(id="wall", volume={"kind": "box", "min": [9, -10, -1], "max": [11, 10, 10]})
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(wall)
    result = _run("srd_information_barriers", state, start=[0, 0, 0], end=[20, 0, 0], rule="solid")
    assert result.result["context"]["_information_blocked"] is True


def _grease_case(origin=(30, 0, 0)):
    state, data, parameters = _fixture("grease", origin)
    _ground(state)
    return state, data, parameters


def _entangle_case(origin=(30, 0, 0)):
    state, data, parameters = _fixture("entangle", origin)
    _ground(state)
    entities = state[f"{SRD}.entities"]
    entities["guard"]["saving_throw_modifiers"]["strength"] = 30
    entities["other"]["saving_throw_modifiers"]["strength"] = -30
    _position(state, "other")["position"] = [30, 2, 0]
    return state, data, parameters


def _entangle_condition(state, target="other"):
    entity = state[f"{SRD}.entities"][target]
    return entity["last_entangle_save"]["condition_source_id"]


def test_entangle_initial_strength_save_uses_actual_space_excludes_caster_and_has_no_repeat():
    state, data, parameters = _entangle_case(origin=(0, 0, 0))
    _position(state, "guard")["position"] = [5, 0, 0]
    # The center is outside; the admitted creature footprint still overlaps the square.
    _position(state, "other")["position"] = [10.2, 2, 0]
    _run("apply_srd_condition", state, target="other", condition="prone")
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    entities = state[f"{SRD}.entities"]
    assert "restrained" not in entities["hero"]["conditions"]
    assert entities["hero"].get("last_entangle_save") is None
    assert entities["guard"]["last_entangle_save"]["outcome"] == "success"
    assert "restrained" in entities["other"]["conditions"]
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["volume"] == {"kind": "box", "min": [-10, -10, 0], "max": [10, 10, 0.000001]}
    assert source["save_dc"] == 18 and source["expires_tick"] == 20
    condition = state[f"{SRD}.state"]["timed_condition_sources"][_entangle_condition(state)]
    assert condition["repeat_save_timing"] == "none" and condition["spell_level"] == 1
    assert entities["hero"]["resources"]["spell_slot_1"] == 2
    assert state[f"{SRD}.state"]["spatial_scene"]["zones"][0]["metadata"]["plant_growth"]


def test_entangle_escape_uses_athletics_action_and_removes_only_its_restraint():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, parameters = _entangle_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    source_id = _entangle_condition(state)
    save = deepcopy(state[f"{SRD}.entities"]["other"]["last_entangle_save"])
    _reject(state, "move_srd_scene_creature", actor_state="other", destination=[35, 2, 0])
    _reject(state, "escape_srd_entangle", actor_state="other", source_id=source_id)
    state[f"{SRD}.entities"]["other"]["skill_check_modifiers"] = {"athletics": -30}
    _turn(state, "other")
    result = _run("escape_srd_entangle", state, actor_state="other", source_id=source_id)
    assert result.result["context"]["_escape_success"] is False
    assert not state[f"{SRD}.combat"]["action_available"]
    assert "restrained" in state[f"{SRD}.entities"]["other"]["conditions"]
    _reject(state, "escape_srd_entangle", actor_state="other", source_id=source_id)
    _run("end_combat_turn", state)
    _turn(state, "other")
    state[f"{SRD}.entities"]["other"]["skill_check_modifiers"]["athletics"] = 30
    result = _run("escape_srd_entangle", state, actor_state="other", source_id=source_id)
    assert result.result["context"]["_escape_success"] is True
    assert "restrained" not in state[f"{SRD}.entities"]["other"]["conditions"]
    result = _run("move_srd_scene_creature", state, actor_state="other", destination=[35, 2, 0])
    assert result.result["context"]["_scene_movement_cost"] == 10
    assert state[f"{SRD}.entities"]["other"]["last_entangle_save"] == save
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    _run("end_combat_turn", state)
    assert "restrained" not in state[f"{SRD}.entities"]["other"]["conditions"]


@pytest.mark.parametrize("removal", ["end_concentration", "expiry", "dispel"])
def test_entangle_source_retirement_removes_plants_and_preserves_independent_restraint(removal):
    state, data, parameters = _entangle_case()
    _run("apply_srd_condition", state, target="other", condition="restrained")
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    source_id = _entangle_condition(state)
    if removal == "expiry":
        _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    elif removal == "dispel":
        _run(
            "dispel_srd_environment_source",
            state,
            caster_state="hero",
            caster="hero",
            area_id="hero",
            slot_level=3,
            casting_modifier=4,
            casting_ability="wisdom",
            participant_data=data,
        )
    else:
        _run("end_concentration", state, caster_state="hero")
    assert state[f"{SRD}.state"]["spatial_scene"]["zones"] == []
    assert not state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"]
    assert "restrained" in state[f"{SRD}.entities"]["other"]["conditions"]


def test_entangle_ending_one_overlap_preserves_the_other_actual_source():
    state, data, parameters = _entangle_case()
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["strength"] = -30
    _position(state, "guard")["position"] = [30, 0, 0]
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    first_id = _entangle_condition(state, "guard")
    _run(
        "cast_spell_environment",
        state,
        caster_state="other",
        spell="entangle",
        slot_level=1,
        concentration_save_modifier=0,
        save_dc=18,
        origin=[30, 0, 0],
    )
    second_id = _entangle_condition(state, "guard")
    assert first_id != second_id
    _run("end_concentration", state, caster_state="hero")
    pool = state[f"{SRD}.state"]["timed_condition_sources"]
    assert not pool[first_id]["installed"] and pool[second_id]["installed"]
    assert "restrained" in state[f"{SRD}.entities"]["guard"]["conditions"]
    _run("end_concentration", state, caster_state="other")
    assert "restrained" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_entangle_immunity_planes_airborne_space_and_dead_subjects_do_not_install_restraints():
    for change in ("immune", "plane", "airborne", "dead"):
        state, data, parameters = _entangle_case()
        if change == "immune":
            state[f"{SRD}.entities"]["other"]["condition_immunities"] = ["restrained"]
        elif change == "plane":
            _position(state, "other")["metadata"]["plane"] = "ethereal"
        elif change == "airborne":
            _position(state, "other")["position"][2] = 5
        else:
            state[f"{SRD}.entities"]["other"]["is_alive"] = False
        _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
        assert "restrained" not in state[f"{SRD}.entities"]["other"]["conditions"]


def test_entangle_retirement_does_not_retire_a_reused_condition_pool_identity():
    state, data, parameters = _entangle_case()
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    source_id = _entangle_condition(state)
    state[f"{SRD}.entities"]["other"]["skill_check_modifiers"] = {"athletics": 30}
    _run("escape_srd_entangle", state, actor_state="other", source_id=source_id)
    result = _run(
        "install_srd_environment_deafened",
        state,
        target_state="other",
        lasting_state="state",
        spell="silence",
        condition="deafened",
        save_dc=1,
        duration_rounds=100,
        repeat_save_timing="none",
        caster_state="hero",
    )
    assert result.result["context"]["_timed_condition_source_id"] == source_id
    _run("end_concentration", state, caster_state="hero")
    assert state[f"{SRD}.state"]["timed_condition_sources"][source_id]["installed"]
    assert "deafened" in state[f"{SRD}.entities"]["other"]["conditions"]


def _grease_id(state):
    return next(
        key
        for key, source in state[f"{SRD}.state"]["persistent_spell_areas"].items()
        if source["mode"] == "grease" and source["active"]
    )


def test_grease_initial_standing_saves_and_source_ground_terrain():
    state, data, parameters = _grease_case()
    _position(state, "guard")["position"] = [30, 0, 0]
    _position(state, "other")["position"] = [30, 2, 0]
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    entities = state[f"{SRD}.entities"]
    assert "prone" not in entities["guard"]["conditions"]
    assert "prone" in entities["other"]["conditions"]
    source = state[f"{SRD}.state"]["persistent_spell_areas"][_grease_id(state)]
    assert source["save_dc"] == 18 and source["expires_tick"] == 20
    zone = state[f"{SRD}.state"]["spatial_scene"]["zones"][0]
    assert zone["shape"]["min"] == [25, -5, 0]
    assert zone["shape"]["max"] == [35, 5, 0.000001]
    assert zone["metadata"]["difficult"] and not zone["metadata"]["flammable"]
    assert entities["hero"]["resources"]["spell_slot_1"] == 2


def test_grease_crossing_spends_real_terrain_cost_and_failed_entry_stops_then_crawls():
    state, data, parameters = _grease_case()
    _position(state, "other")["position"] = [20, 2, 0]
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    result = _run("move_srd_scene_creature", state, actor_state="guard", destination=[40, 0, 0])
    assert result.result["context"]["_scene_movement_cost"] == 30
    assert _position(state, "guard")["position"] == [40, 0, 0]
    result = _run("move_srd_scene_creature", state, actor_state="other", destination=[40, 2, 0])
    assert result.result["context"]["_scene_movement_cost"] == 5
    assert _position(state, "other")["position"] == [25, 2, 0]
    assert "prone" in state[f"{SRD}.entities"]["other"]["conditions"]
    result = _run("move_srd_scene_creature", state, actor_state="other", destination=[30, 2, 0])
    assert result.result["context"]["_scene_movement_cost"] == 15
    assert state[f"{SRD}.entities"]["other"]["scene_movement_spent"] == 20


def test_grease_expiry_preserves_concentration_and_prone_but_removes_own_terrain():
    state, data, parameters = _grease_case()
    _run(
        "cast_spell_environment",
        state,
        caster_state="hero",
        spell="fog_cloud",
        slot_level=1,
        concentration_save_modifier=0,
        origin=[0, 0, 0],
    )
    _position(state, "other")["position"] = [30, 2, 0]
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    source_id = _grease_id(state)
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "fog_cloud"
    _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    assert not state[f"{SRD}.state"]["persistent_spell_areas"][source_id]["active"]
    assert state[f"{SRD}.state"]["spatial_scene"]["zones"] == []
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "fog_cloud"
    assert state[f"{SRD}.state"]["has_spell_environments"]
    assert "prone" in state[f"{SRD}.entities"]["other"]["conditions"]


def test_grease_end_turn_uses_actual_membership_and_new_save():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, parameters = _grease_case()
    _position(state, "guard")["position"] = [30, 0, 0]
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["dexterity"] = -30
    _turn(state)
    _run("end_combat_turn", state)
    assert "prone" in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_grease_ground_rejection_is_atomic_and_flight_avoids_ground_cost():
    state, data, parameters = _fixture("grease", origin=(30, 0, 0))
    _reject(state, "cast_spell_outside_combat", participant_data=data, **parameters)
    _ground(state)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    state[f"{SRD}.entities"]["guard"]["flying_speed"] = 30
    result = _run(
        "move_srd_scene_creature", state, actor_state="guard", destination=[40, 0, 0], mode="fly"
    )
    assert result.result["context"]["_scene_movement_cost"] == 20
    assert state[f"{SRD}.entities"]["guard"].get("last_grease_save") is None


def test_grease_condition_immunity_and_other_plane_do_not_fake_prone():
    state, data, parameters = _grease_case()
    _position(state, "other")["position"] = [30, 2, 0]
    state[f"{SRD}.entities"]["other"]["condition_immunities"] = ["prone"]
    _position(state, "guard")["position"] = [30, 0, 0]
    _position(state, "guard")["metadata"]["plane"] = "ethereal"
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert "prone" not in state[f"{SRD}.entities"]["other"]["conditions"]
    assert not state[f"{SRD}.entities"]["other"]["last_grease_save"]["fell"]
    assert state[f"{SRD}.entities"]["guard"].get("last_grease_save") is None


def test_grease_initial_save_only_applies_to_creatures_actually_standing():
    state, data, parameters = _grease_case()
    _position(state, "other")["position"] = [30, 2, 0]
    _run("apply_srd_condition", state, target="other", condition="prone")
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state[f"{SRD}.entities"]["other"].get("last_grease_save") is None
    _run("resolve_srd_grease_end_turn", state, target_state="other")
    assert state[f"{SRD}.entities"]["other"]["last_grease_save"]["outcome"] == "failure"


def test_selected_environment_dispel_uses_shared_source_check_and_exact_retirement():
    from tests.srd_full.test_srd_action_budget import ROOT

    if "dispel_srd_environment_source:" not in (ROOT / "runtime/ruleset.yaml").read_text():
        pytest.skip("The frozen recovery owner is required for this composition.")
    state, data, parameters = _grease_case()
    _run(
        "cast_spell_environment",
        state,
        caster_state="hero",
        spell="fog_cloud",
        slot_level=1,
        concentration_save_modifier=0,
        origin=[0, 0, 0],
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    source_id = _grease_id(state)
    _reject(
        state,
        "dispel_srd_environment_source",
        caster_state="hero",
        area_id=source_id,
        slot_level=3,
        casting_modifier=4,
        casting_ability="intelligence",
        point=[90, 0, 0],
    )
    _run(
        "dispel_srd_environment_source",
        state,
        caster_state="hero",
        area_id=source_id,
        slot_level=3,
        casting_modifier=4,
        casting_ability="intelligence",
    )
    assert not state[f"{SRD}.state"]["persistent_spell_areas"][source_id]["active"]
    assert state[f"{SRD}.state"]["spatial_scene"]["zones"] == []
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "fog_cloud"


def test_public_dispel_magic_selects_the_actual_environment_effect():
    from tests.srd_full.test_srd_action_budget import ROOT

    if "cast_srd_recovery:" not in (ROOT / "runtime/ruleset.yaml").read_text():
        pytest.skip("The frozen recovery owner is required for this public composition.")
    state, data, parameters = _fixture("dispel_magic")
    _ground(state)
    _run(
        "cast_spell_environment",
        state,
        caster_state="hero",
        spell="grease",
        slot_level=1,
        concentration_save_modifier=0,
        save_dc=18,
        origin=[30, 0, 0],
    )
    source_id = _grease_id(state)
    parameters.update(recovery_choice="environment", recovery_source_id=source_id)
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert result.result["status"] == "committed"
    assert not state[f"{SRD}.state"]["persistent_spell_areas"][source_id]["active"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2


def test_higher_level_environment_dispel_uses_the_actual_check_and_failure_preserves_source():
    from tests.srd_full.test_srd_action_budget import ROOT

    if "dispel_srd_environment_source:" not in (ROOT / "runtime/ruleset.yaml").read_text():
        pytest.skip("The frozen recovery owner is required for this composition.")
    state, data, parameters = _grease_case()
    parameters["slot_resource"] = "spell_slot_4"
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    source_id = _grease_id(state)
    result = _run(
        "dispel_srd_environment_source",
        state,
        caster_state="hero",
        area_id=source_id,
        slot_level=3,
        casting_modifier=-30,
        casting_ability="intelligence",
    )
    assert not result.result["context"]["_dispel_succeeded"]
    assert result.result["context"]["_dispel_roll"] is not None
    assert state[f"{SRD}.state"]["persistent_spell_areas"][source_id]["active"]
    assert len(state[f"{SRD}.state"]["spatial_scene"]["zones"]) == 1


def test_fog_actual_crossing_blocks_sight_and_upcasting_binds_source_radius():
    state, _, _ = _cast(origin=(30, 0, 0), slot=2)
    area = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert state[f"{SRD}.state"]["lasting_serial"] == 1
    assert area["radius"] == 40 and area["expires_tick"] == 1200
    _position(state, "guard")["position"] = [90, 0, 0]
    result = _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
    assert result.result["context"]["_scene_can_see"] is False
    assert result.result["context"]["_scene_line_of_sight"] is True
    _run("end_concentration", state, caster_state="hero")
    result = _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
    assert result.result["context"]["_scene_can_see"] is True


def test_environment_placement_derives_range_and_clear_path_before_spending():
    state, data, parameters = _fixture(origin=(121, 0, 0))
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state == before


@pytest.mark.parametrize("spell", ["fog_cloud", "silence"])
def test_selected_concentrated_environment_key_can_equal_the_loaded_dispel_caster(spell):
    state, data, _ = _cast(spell)
    assert state[f"{SRD}.entities"]["hero"]["environment_area_id"] == "hero"
    result = _run(
        "dispel_srd_environment_source",
        state,
        caster_state="hero",
        caster="hero",
        area_id="hero",
        slot_level=3,
        casting_modifier=4,
        casting_ability="wisdom",
        participant_data=data,
    )
    assert result.result["context"]["_environment_dispel_checks"] == [
        {"source": "hero", "roll": None, "ended": True}
    ]
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None


def test_silence_entire_creature_boundary_and_real_thunder_immunity():
    state, _, _ = _cast("silence", origin=(20, 0, 3))
    entities = state[f"{SRD}.entities"]
    assert "deafened" in entities["guard"]["conditions"]
    assert "deafened" not in entities["hero"]["conditions"]
    before = entities["guard"]["hp"]
    _run("apply_srd_damage", state, target="guard", amount=10, adjustment=0, damage_type="thunder")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == before
    _run(
        "apply_srd_composite_damage",
        state,
        target="guard",
        component_1_amount=10,
        component_1_damage_type="thunder",
        component_2_amount=0,
        component_2_damage_type="cold",
    )
    assert state[f"{SRD}.entities"]["guard"]["hp"] == before
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[41, 0, 0])
    entities = state[f"{SRD}.entities"]
    assert "deafened" not in entities["guard"]["conditions"]
    before = entities["guard"]["hp"]
    _run("apply_srd_damage", state, target="guard", amount=10, adjustment=0, damage_type="thunder")
    assert state[f"{SRD}.entities"]["guard"]["hp"] < before


def test_silence_rejects_verbal_components_despite_caller_available_claim():
    state, data, _ = _cast("silence", origin=(0, 0, 3))
    _, _, parameters = _spell_case("healing_word")
    state[f"{SRD}.entities"]["hero"]["prepared_spells"] = ["healing_word"]
    data["spellcasting_grants"][0]["spells"] = ["healing_word"]
    parameters["spell_grant"] = data["spellcasting_grants"][0]["source"]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Silence"):
        _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state == before


def test_clock_expiry_and_independent_deafened_condition_are_preserved():
    state, _, _ = _cast("silence", origin=(20, 0, 3))
    _run("apply_srd_condition", state, target="guard", condition="deafened")
    _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert "deafened" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.state"]["has_persistent_spell_areas"] is False


def test_only_admitted_overlapping_strong_wind_can_disperse_fog():
    state, _, _ = _cast()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"].append(
        {
            "id": "wind",
            "map_id": "orders",
            "shape": {"kind": "box", "min": [-10, -10, -10], "max": [10, 10, 10]},
            "metadata": {"strong_wind": False, "plane": "material"},
        }
    )
    _reject(state, "disperse_srd_fog", area_id="hero", wind_zone_id="wind")
    scene["zones"][0]["metadata"]["strong_wind"] = True
    _run("disperse_srd_fog", state, area_id="hero", wind_zone_id="wind")
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None


def test_admitted_strong_wind_ends_new_fog_and_later_scene_queries():
    state, data, parameters = _fixture()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"].append(
        {
            "id": "wind",
            "map_id": "orders",
            "shape": {"kind": "cylinder", "center": [10, 0, 0], "radius": 5, "height": 10},
            "metadata": {"strong_wind": True, "plane": "material"},
        }
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_1"] == 2
    state, _, _ = _cast()
    state[f"{SRD}.state"]["spatial_scene"]["zones"] = scene["zones"]
    result = _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
    assert result.result["context"]["_scene_can_see"] is True
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None


def test_fog_and_silence_use_remote_sensor_origin_and_actual_sound_path():
    state, _, _ = _cast(origin=(10, 0, 3))
    _position(state, "guard")["position"] = [50, 0, 0]
    _run(
        "cast_spell_information_spell",
        state,
        caster_state="other",
        target_state="other",
        spell="arcane_eye",
        caster_can_touch_target=True,
        concentration_modifier=0,
        material_item_ids=[],
        information_position=[50, 5, 0],
        information_lighting="bright",
        information_familiar=True,
    )
    result = _run(
        "query_srd_information_spell",
        state,
        caster_state="other",
        spell="arcane_eye",
        mode="remote_perception",
    )
    assert "guard: seeing" in result.result["context"]["_info_results"]
    _run(
        "operate_srd_information_proxy",
        state,
        caster_state="other",
        spell="arcane_eye",
        destination=[20, 5, 0],
        modality="seeing",
    )
    result = _run(
        "query_srd_information_spell",
        state,
        caster_state="other",
        spell="arcane_eye",
        mode="remote_perception",
    )
    assert "guard: seeing" not in result.result["context"]["_info_results"]
    state, _, _ = _cast("silence", origin=(20, 0, 3))
    _position(state, "hero")["position"] = [-10, 0, 0]
    _position(state, "guard")["position"] = [50, 0, 0]
    for actor in ("hero", "guard"):
        state[f"{SRD}.entities"][actor]["languages"] = ["common"]
    _run("refresh_srd_environment_scene", state)
    result = _run(
        "resolve_srd_spell_language",
        state,
        listener_state="hero",
        speaker_state="guard",
        channel="speech",
        perceived=True,
        language="common",
    )
    assert result.result["context"]["_language_understands"] is False
    relation = _run("resolve_srd_scene_relation", state, actor_state="hero", target_state="guard")
    assert relation.result["context"]["_scene_can_hear"] is False


def test_affinity_pruning_preserves_a_real_environment_source(monkeypatch):
    from tests.srd_full.test_srd_spell_persistent_areas import _cast as affinity_cast

    state, _, _ = affinity_cast(monkeypatch)
    state[f"{SRD}.entities"]["other"].update(
        concentration_spell_id=None,
        conditions=[],
        can_take_action=True,
    )
    _run(
        "cast_spell_environment",
        state,
        caster_state="other",
        spell="fog_cloud",
        slot_level=1,
        concentration_save_modifier=0,
        origin=[150, 0, 0],
    )
    _run("prune_srd_affinity_anchors", state, lasting_state="state")
    registry = state[f"{SRD}.state"]
    assert "other" in registry["persistent_spell_area_ids"]
    assert registry["persistent_spell_areas"]["other"]["spell"] == "fog_cloud"
    assert registry["affinity_cast_ids"]


def test_overlapping_silence_tracks_the_remaining_source_expiry():
    state, _, _ = _cast("silence", origin=(20, 0, 3))
    _run("advance_srd_lasting_time", state, elapsed_rounds=50)
    _run(
        "cast_spell_environment",
        state,
        caster_state="other",
        spell="silence",
        slot_level=2,
        concentration_save_modifier=0,
        origin=[20, 0, 3],
    )
    guard = state[f"{SRD}.entities"]["guard"]
    source = guard["environment_silence_condition_id"]
    assert state[f"{SRD}.state"]["timed_condition_sources"][source]["expires_tick"] == 300
    _run("end_concentration", state, caster_state="other")
    assert "deafened" in state[f"{SRD}.entities"]["guard"]["conditions"]
    assert state[f"{SRD}.state"]["timed_condition_sources"][source]["expires_tick"] == 200
    _run("advance_srd_lasting_time", state, elapsed_rounds=50)
    assert "deafened" not in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_fog_obscures_vision_checks_and_the_real_trap_sight_consumer():
    state, _, _ = _cast(origin=(10, 0, 3))
    check = _run(
        "d20_test",
        state,
        actor_state="guard",
        ability="wisdom",
        kind="ability_check",
        ability_modifier=100,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=1,
        advantage=False,
        disadvantage=False,
        requires_sight=True,
    )
    assert check.result["context"]["_automatic_failure_reason"] == "blinded_sight"
    _position(state, "other")["position"] = [0, 0, 0]
    _position(state, "guard")["metadata"].update(
        intentional_trap=True,
        general_danger="poison",
        is_object=True,
    )
    parameters = {
        "caster_state": "other",
        "target_state": "other",
        "spell": "find_traps",
        "caster_can_touch_target": True,
        "concentration_modifier": 0,
        "material_item_ids": [],
    }
    result = _run("cast_spell_information_spell", state, **parameters)
    assert {"label": "Trap present", "value": False} in result.result["context"][
        "_spell_detail_rows"
    ]
    _run("end_concentration", state, caster_state="hero")
    result = _run("cast_spell_information_spell", state, **parameters)
    assert {"label": "Trap present", "value": True} in result.result["context"][
        "_spell_detail_rows"
    ]


@pytest.mark.parametrize("spell", ["fog_cloud", "grease", "entangle"])
def test_public_environment_cast_reopens_and_retry_preserves_source_and_cost(
    tmp_path,
    monkeypatch,
    spell,
):
    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as public_casting

    def public_fixture(name):
        if name == "entangle":
            fixture = _entangle_case()
            fixture[0][f"{SRD}.entities"]["other"]["skill_check_modifiers"] = {"athletics": 30}
            return fixture
        return _grease_case() if name == "grease" else _fixture(name)

    monkeypatch.setattr(public_casting, "_spell_case", public_fixture)
    database = tmp_path / "environment.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public_casting._create_spell_campaign(owner, spell)
        command = ActionCommand(
            campaign_id="casting",
            action_id="fog-cast",
            idempotency_key="fog-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        first = StatelessMcpMechanics(owner).execute_action(command)
        assert first.status is ActionStatus.COMMITTED
        receipt = deepcopy(first.result)
        saved = owner.snapshot("casting")
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        retry = StatelessMcpMechanics(owner).execute_action(command)
        assert retry.result == receipt
        if spell == "grease":
            movement = ActionCommand(
                campaign_id="casting",
                action_id="grease-enter",
                idempotency_key="grease-enter",
                owner_id="casting-owner",
                writer=WriterRole.DM,
                expected_revision=saved.revision,
                turn_id="casting-turn",
                package_id=SRD,
                tool_name="move_srd_scene_creature",
                parameters={"actor_state": "guard", "destination": [40, 0, 0]},
            )
            moved = StatelessMcpMechanics(owner).execute_action(movement)
            assert moved.status is ActionStatus.COMMITTED
            assert moved.result["context"]["_scene_movement_cost"] == 30
        elif spell == "entangle":
            source_id = _entangle_condition(saved.state)
            escape = ActionCommand(
                campaign_id="casting",
                action_id="entangle-escape",
                idempotency_key="entangle-escape",
                owner_id="casting-owner",
                writer=WriterRole.DM,
                expected_revision=saved.revision,
                turn_id="casting-turn",
                package_id=SRD,
                tool_name="escape_srd_entangle",
                parameters={"actor_state": "other", "source_id": source_id},
            )
            escaped = StatelessMcpMechanics(owner).execute_action(escape)
            assert escaped.status is ActionStatus.COMMITTED
            assert escaped.result["context"]["_escape_success"]
            escape_state = owner.snapshot("casting")
            assert "restrained" not in escape_state.state[f"{SRD}.entities"]["other"]["conditions"]
            assert escape_state.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
            assert StatelessMcpMechanics(owner).execute_action(escape).result == escaped.result
            assert owner.snapshot("casting").state == escape_state.state


def _spike_case(*, slot=2, blind=False):
    state, data, parameters = _fixture("spike_growth", origin=(40, 0, 0), slot=slot)
    _ground(state)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"].append(
        {
            "id": "actual-spike-lighting",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
            "metadata": {"plane": "material", "lighting": "bright"},
        }
    )
    for actor in ("guard", "other"):
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=0)
    _position(state, "guard")["position"] = [10, 0, 0]
    _position(state, "other")["position"] = [10, 8, 0]
    if blind:
        _run("apply_srd_condition", state, target="other", condition="blinded")
    return state, data, parameters


def _spike_cast(**choices):
    state, data, parameters = _spike_case(**choices)
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert result.result["status"] == "committed"
    return state, data, parameters


def test_spike_growth_fixed_source_actual_ground_no_appearance_damage_and_visible_recognition():
    state, _, _ = _spike_cast(slot=5, blind=True)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["origin"] == [40, 0, 0] and source["radius"] == 20
    assert source["ground_id"] == "floor" and source["expires_tick"] == 200
    assert "guard" in source["recognized_actor_ids"]
    assert "other" not in source["recognized_actor_ids"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2


def test_spike_growth_actual_entry_travel_cost_and_two_printed_damage_packets(monkeypatch):
    state, _, _ = _spike_cast(slot=5)
    result = _run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    guard = state[f"{SRD}.entities"]["guard"]
    assert result.result["context"]["_scene_movement_distance"] == 20
    assert result.result["context"]["_scene_movement_cost"] == 30
    assert _position(state, "guard")["position"] == [30, 0, 0]
    assert 984 <= guard["hp"] <= 996
    assert guard["last_spike_growth_damage"]["raw_damage"]["count"] == 2
    assert guard["last_spike_growth_damage"]["damage_type"] == "piercing"
    assert guard["spike_growth_travel"]["hero"]["remainder"] == pytest.approx(0)


def test_spike_growth_fractional_ground_distance_survives_separate_moves():
    state, _, _ = _spike_cast()
    _position(state, "guard")["position"] = [23, 0, 0]
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[25, 0, 0])
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 1000 and guard["spike_growth_travel"]["hero"][
        "remainder"
    ] == pytest.approx(2)
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[28, 0, 0])
    guard = state[f"{SRD}.entities"]["guard"]
    assert 992 <= guard["hp"] <= 998
    assert guard["spike_growth_travel"]["hero"]["remainder"] == pytest.approx(0)


def test_spike_growth_lethal_first_packet_stops_actual_travel_at_five_feet():
    state, _, _ = _spike_cast()
    state[f"{SRD}.entities"]["guard"].update(hp=1, max_hp=1000)
    result = _run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    assert _position(state, "guard")["position"] == [25, 0, 0]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 0
    assert result.result["context"]["_scene_movement_distance"] == 15
    assert result.result["context"]["_scene_movement_cost"] == 20


@pytest.mark.parametrize("removal", ["end_concentration", "expiry", "dispel"])
def test_spike_growth_actual_source_retirement_clears_later_ground_cost_and_damage(removal):
    state, _, _ = _spike_cast()
    if removal == "expiry":
        _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    elif removal == "dispel":
        _run(
            "dispel_srd_environment_source",
            state,
            caster_state="other",
            area_id="hero",
            slot_level=3,
            casting_modifier=0,
            casting_ability="wisdom",
        )
    else:
        _run("end_concentration", state, caster_state="hero")
    result = _run("move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0])
    assert result.result["context"]["_scene_movement_cost"] == 20
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000


def test_spike_growth_search_actual_source_dc_and_action_has_real_failure_and_success():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _spike_cast(blind=True)
    state[f"{SRD}.entities"]["other"]["skill_check_modifiers"] = {"survival": -100}
    _turn(state, "other")
    result = _run(
        "search_srd_spike_growth", state, actor_state="other", area_id="hero", skill="survival"
    )
    assert not result.result["context"]["_spike_search_recognized"]
    assert not state[f"{SRD}.combat"]["action_available"]
    assert (
        "other"
        not in state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["recognized_actor_ids"]
    )
    _reject(state, "search_srd_spike_growth", actor_state="other", area_id="hero", skill="survival")
    _run("end_combat_turn", state)
    _turn(state, "other")
    state[f"{SRD}.entities"]["other"]["skill_check_modifiers"]["survival"] = 100
    result = _run(
        "search_srd_spike_growth", state, actor_state="other", area_id="hero", skill="survival"
    )
    assert result.result["context"]["_spike_search_recognized"]
    assert (
        "other" in state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["recognized_actor_ids"]
    )


def test_spike_growth_actual_unknown_ground_and_support_shadow_reject_atomically():
    state, data, parameters = _spike_case()
    state[f"{SRD}.state"]["spatial_scene"]["geometry"] = []
    _reject(state, "cast_spell_outside_combat", participant_data=data, **parameters)
    state, _, _ = _spike_cast()
    wall = deepcopy(state[f"{SRD}.state"]["spatial_scene"]["geometry"][0])
    wall.update(id="shadow", volume={"kind": "box", "min": [45, 9, 0], "max": [46, 10, 10]})
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(wall)
    _reject(state, "move_srd_scene_creature", actor_state="guard", destination=[30, 0, 0])


@pytest.mark.parametrize("change", ["fly", "plane"])
def test_spike_growth_actual_flight_and_other_plane_avoid_ground_damage(change):
    state, _, _ = _spike_cast()
    kwargs = {}
    if change == "fly":
        state[f"{SRD}.entities"]["guard"]["flying_speed"] = 30
        kwargs["mode"] = "fly"
    else:
        _position(state, "guard")["metadata"]["plane"] = "ethereal"
    result = _run(
        "move_srd_scene_creature", state, actor_state="guard", destination=[30, 0, 0], **kwargs
    )
    assert result.result["context"]["_scene_movement_cost"] == 20
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000


def test_spike_growth_real_overlapping_sources_fail_clearly_without_stacked_damage():
    state, _, _ = _spike_cast()
    _run(
        "cast_spell_environment",
        state,
        caster_state="other",
        spell="spike_growth",
        slot_level=2,
        concentration_save_modifier=0,
        save_dc=18,
        origin=[40, 0, 0],
    )
    _reject(state, "move_srd_scene_creature", actor_state="guard", destination=[30, 0, 0])


def test_public_spike_growth_paid_cast_partial_distance_reopens_retries_and_source_removes(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as public_casting

    def actual_fixture(_name):
        state, data, parameters = _spike_case()
        _position(state, "guard")["position"] = [23, 0, 0]
        return state, data, parameters

    monkeypatch.setattr(public_casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(public_casting, "_spell_case", actual_fixture)
    database = tmp_path / "spike-growth.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public_casting._create_spell_campaign(
            owner, "spike_growth", target_overrides={"hp": 1000, "temp_hp": 0}
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="spike-cast",
            idempotency_key="spike-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        first = StatelessMcpMechanics(owner).execute_action(command)
        assert first.status is ActionStatus.COMMITTED
        snapshot = owner.snapshot("casting")
        assert snapshot.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
        partial = replace(
            command,
            action_id="spike-two-feet",
            idempotency_key="spike-two-feet",
            expected_revision=snapshot.revision,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "guard", "destination": [25, 0, 0]},
        )
        moved = StatelessMcpMechanics(owner).execute_action(partial)
        assert moved.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["guard"]["hp"] == 1000
        assert saved.state[f"{SRD}.entities"]["guard"]["spike_growth_travel"]["hero"][
            "remainder"
        ] == pytest.approx(2)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(partial).result == moved.result
        assert mechanics.execute_action(command).result == first.result
        assert owner.snapshot("casting").state == saved.state
        continued = replace(
            partial,
            action_id="spike-three-feet",
            idempotency_key="spike-three-feet",
            expected_revision=saved.revision,
            parameters={"actor_state": "guard", "destination": [28, 0, 0]},
        )
        damaged = mechanics.execute_action(continued)
        assert damaged.status is ActionStatus.COMMITTED
        hurt = owner.snapshot("casting")
        guard = hurt.state[f"{SRD}.entities"]["guard"]
        assert 992 <= guard["hp"] <= 998
        assert guard["spike_growth_travel"]["hero"]["remainder"] == pytest.approx(0)
        assert guard["scene_movement_spent"] == 10
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert mechanics.execute_action(continued).result == damaged.result
        assert owner.snapshot("casting").state == hurt.state
        ended = mechanics.execute_action(
            replace(
                command,
                action_id="spike-end",
                idempotency_key="spike-end",
                expected_revision=hurt.revision,
                tool_name="end_concentration",
                parameters={"caster_state": "hero"},
            )
        )
        assert ended.status is ActionStatus.COMMITTED
        freed = owner.snapshot("casting")
        assert not freed.state[f"{SRD}.state"]["has_spike_growth"]
        ordinary = mechanics.execute_action(
            replace(
                partial,
                action_id="spike-gone",
                idempotency_key="spike-gone",
                expected_revision=freed.revision,
                parameters={"actor_state": "guard", "destination": [33, 0, 0]},
            )
        )
        assert ordinary.status is ActionStatus.COMMITTED
        assert ordinary.result["context"]["_scene_movement_cost"] == 5
        assert owner.snapshot("casting").state[f"{SRD}.entities"]["guard"]["hp"] == guard["hp"]


def _actual_cylinder_floor_case():
    """Use the reported source's admitted vertical cylinder with its base at minus one."""
    state, _, _ = _fixture()
    _ground(state)
    floor = state[f"{SRD}.state"]["spatial_scene"]["geometry"][0]
    floor["volume"] = {"kind": "cylinder", "center": [0, 0, -1], "radius": 30, "height": 1}
    return state


@pytest.mark.parametrize(
    "start,end,blocked",
    [
        ([0, 0, 0], [3, 0, 0], False),
        ([0, 0, -1], [3, 0, -1], False),
        ([30, 0, -1], [30, 0, 0], False),
        ([0, 0, -0.5], [3, 0, -0.5], True),
        ([0, 0, 1], [0, 0, -0.5], True),
        ([0, 0, 2], [0, 0, 0], False),
    ],
)
def test_environment_actual_cylinder_barrier_cap_side_tangency_and_solid_interior(
    start, end, blocked
):
    state = _actual_cylinder_floor_case()
    context = _run(
        "srd_information_barriers", state, start=start, end=end, rule="solid", plane="material"
    ).result["context"]
    assert context["_information_blocked"] is blocked


def test_environment_actual_cylinder_ground_support_remains_a_physical_top_cap_hit():
    state = _actual_cylinder_floor_case()
    ground = _run(
        "qualify_srd_environment_ground", state, point=[3, 0, 0], plane="material"
    ).result["context"]
    assert (
        ground["_environment_ground_id"]
        == state[f"{SRD}.state"]["spatial_scene"]["geometry"][0]["id"]
    )


def test_environment_actual_cylinder_cap_tangent_does_not_clear_intervening_box_wall():
    state = _actual_cylinder_floor_case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    wall = deepcopy(scene["geometry"][0])
    wall.update(
        id="true-cargo-wall", volume={"kind": "box", "min": [1, -10, -1], "max": [2, 10, 8]}
    )
    scene["geometry"].append(wall)
    context = _run(
        "srd_information_barriers",
        state,
        start=[0, 0, 0],
        end=[3, 0, 0],
        rule="solid",
        plane="material",
    ).result["context"]
    assert context["_information_blocked"]


@pytest.mark.parametrize(
    "spell,flag", [("moonbeam", "has_moonbeam"), ("spike_growth", "has_spike_growth")]
)
def test_environment_mode_index_is_derived_from_actual_cast_and_exact_source_removal(spell, flag):
    state, _, _ = _moonbeam_cast() if spell == "moonbeam" else _spike_cast()
    assert state[f"{SRD}.state"][flag]
    _run("end_concentration", state, caster_state="hero")
    assert not state[f"{SRD}.state"][flag]
    _run(
        "cast_spell_environment",
        state,
        caster_state="other",
        spell="silence",
        slot_level=2,
        concentration_save_modifier=0,
        origin=[10, 0, 0],
    )
    assert state[f"{SRD}.state"]["has_spell_environments"]
    assert not state[f"{SRD}.state"][flag]


def test_spike_growth_actual_character_search_derives_wisdom_and_skill_proficiency():
    state, data, _ = _spike_cast()
    data["skill_proficiencies"] = ["survival"]
    result = _run(
        "search_srd_spike_growth",
        state,
        participant_data=data,
        actor="hero",
        actor_state="hero",
        area_id="hero",
        skill="survival",
    )
    expected = (data["abilities"]["wisdom"] - 10) // 2
    if "survival" in data["skill_proficiencies"]:
        expected += 2 + (data["level"] - 1) // 4
    assert result.result["context"]["_combined_modifier"] == expected
    assert state[f"{SRD}.entities"]["hero"]["last_spike_growth_search"]["skill"] == "survival"


def test_spike_growth_actual_existing_fog_prevents_origin_appearance_recognition():
    state, data, parameters = _spike_case()
    _run(
        "cast_spell_environment",
        state,
        caster_state="other",
        spell="fog_cloud",
        slot_level=1,
        concentration_save_modifier=0,
        origin=[40, 0, 0],
    )
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert not source["recognized_actor_ids"]
    assert source["active"] and source["ground_id"] == "floor"


def test_spike_growth_true_sphere_face_tangent_ground_route_has_no_terrain_or_damage():
    state, _, _ = _spike_cast()
    _position(state, "guard")["position"] = [30, 20, 0]
    result = _run("move_srd_scene_creature", state, actor_state="guard", destination=[50, 20, 0])
    assert result.result["context"]["_scene_movement_cost"] == 20
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000


@pytest.mark.parametrize("defense", ["resistance", "immunity"])
def test_spike_growth_typed_defenses_and_temp_hp_share_one_real_packet(defense):
    state, _, _ = _spike_cast()
    _position(state, "guard")["position"] = [23, 0, 0]
    guard = state[f"{SRD}.entities"]["guard"]
    guard["temp_hp"] = 1
    guard["damage_resistances" if defense == "resistance" else "damage_immunities"] = ["piercing"]
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[28, 0, 0])
    guard = state[f"{SRD}.entities"]["guard"]
    packet = guard["last_spike_growth_damage"]
    expected = packet["raw_damage"]["total"] // 2 if defense == "resistance" else 0
    assert packet["applied_damage"] == expected
    assert guard["hp"] == 1000 - max(0, expected - 1)
    assert guard["temp_hp"] == max(0, 1 - expected)
    assert packet["point"] == [28, 0, 0]


def test_spike_growth_genuine_out_of_range_source_cast_preserves_paid_cost_atomically():
    state, data, parameters = _spike_case()
    parameters["environment_origin"] = [151, 0, 0]
    _reject(state, "cast_spell_outside_combat", participant_data=data, **parameters)


def test_spike_growth_recast_exact_sequence_does_not_reuse_old_fractional_travel():
    state, _, _ = _spike_cast()
    _position(state, "guard")["position"] = [23, 0, 0]
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[25, 0, 0])
    prior = state[f"{SRD}.entities"]["guard"]["spike_growth_travel"]["hero"]["sequence"]
    _run(
        "cast_spell_environment",
        state,
        caster_state="hero",
        spell="spike_growth",
        slot_level=2,
        concentration_save_modifier=0,
        save_dc=18,
        origin=[40, 0, 0],
    )
    current = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["sequence"]
    assert current != prior
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[28, 0, 0])
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 1000
    assert guard["spike_growth_travel"]["hero"] == {
        "sequence": current,
        "remainder": pytest.approx(3),
    }


def test_spike_growth_actual_caster_lethal_packet_ends_source_and_stops_at_contact():
    state, data, parameters = _spike_case()
    parameters["environment_origin"] = [0, 0, 0]
    state[f"{SRD}.entities"]["hero"].update(hp=1, temp_hp=0)
    _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    result = _run("move_srd_scene_creature", state, actor_state="hero", destination=[10, 0, 0])
    hero = state[f"{SRD}.entities"]["hero"]
    assert hero["hp"] == 0 and hero["concentration_spell_id"] is None
    assert _position(state, "hero")["position"] == [5, 0, 0]
    assert not state[f"{SRD}.state"]["has_spike_growth"]
    assert not state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    assert result.result["context"]["_scene_movement_distance"] == 5
    assert result.result["context"]["_scene_movement_cost"] == 10


@pytest.mark.parametrize(
    "spell,flag,rounds",
    [("moonbeam", "has_moonbeam", 10), ("spike_growth", "has_spike_growth", 100)],
)
def test_environment_mode_index_real_clock_expiry_clears_exact_active_source(spell, flag, rounds):
    state, _, _ = _moonbeam_cast() if spell == "moonbeam" else _spike_cast()
    assert state[f"{SRD}.state"][flag]
    _run("advance_srd_lasting_time", state, elapsed_rounds=rounds)
    assert not state[f"{SRD}.state"][flag]


def test_spike_growth_search_rejects_nonexistent_or_other_spell_source_atomically():
    state, _, _ = _spike_cast()
    _reject(
        state, "search_srd_spike_growth", actor_state="guard", area_id="missing", skill="survival"
    )
    _run(
        "cast_spell_environment",
        state,
        caster_state="other",
        spell="silence",
        slot_level=2,
        concentration_save_modifier=0,
        origin=[10, 0, 0],
    )
    _reject(
        state, "search_srd_spike_growth", actor_state="guard", area_id="other", skill="survival"
    )


def _guardians_case(*, ethic="good", unaffected=(), slot=3, appearance="angelic"):
    state, data, parameters = _fixture("spirit_guardians", slot=slot)
    _ground(state)
    _position(state, "hero")["metadata"]["alignment_ethic"] = ethic
    _position(state, "guard")["position"] = [10, 0, 0]
    _position(state, "other")["position"] = [10, 8, 0]
    for actor in ("guard", "other"):
        state[f"{SRD}.entities"][actor].update(hp=1000, max_hp=1000, temp_hp=0)
        state[f"{SRD}.entities"][actor]["saving_throw_modifiers"]["wisdom"] = -100
    parameters.update(
        environment_unaffected_targets=list(unaffected), environment_spirit_appearance=appearance
    )
    return state, data, parameters


def _guardians_cast(**choices):
    state, data, parameters = _guardians_case(**choices)
    result = _run("cast_spell_outside_combat", state, participant_data=data, **parameters)
    assert result.result["status"] == "committed"
    return state, data, parameters


@pytest.mark.parametrize(
    "ethic,damage,appearance",
    [
        ("good", "radiant", "fey"),
        ("neutral", "radiant", "angelic"),
        ("evil", "necrotic", "fiendish"),
    ],
)
def test_spirit_guardians_retains_actual_alignment_source_and_cast_choices(
    ethic, damage, appearance
):
    state, _, _ = _guardians_cast(
        ethic=ethic,
        appearance="fey" if ethic == "good" else "angelic",
        unaffected=("other",),
        slot=5,
    )
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["origin"] == [0, 0, 0] and source["radius"] == 15
    assert source["alignment_ethic"] == ethic and source["damage_type"] == damage
    assert source["spirit_appearance"] == appearance and source["unaffected_actor_ids"] == ["other"]
    assert source["subject_entity_id"] == "hero" and source["expires_tick"] == 200
    assert state[f"{SRD}.state"]["has_spirit_guardians"]
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_5"] == 2
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    assert state[f"{SRD}.entities"]["other"]["hp"] == 1000


def test_spirit_guardians_unknown_actual_alignment_rejects_paid_cast_atomically():
    state, data, parameters = _guardians_case(ethic="unknown")
    _reject(state, "cast_spell_outside_combat", participant_data=data, **parameters)


def test_spirit_guardians_end_turn_real_save_once_and_upcast_alignment_damage():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _guardians_cast(ethic="evil", slot=5)
    _turn(state, "guard")
    assert state[f"{SRD}.combat"]["movement_remaining"] == 15
    assert state[f"{SRD}.entities"]["guard"].get("last_spirit_guardians_save") is None
    _run("resolve_srd_environment_hazard_end_turn", state, target_state="guard")
    guard = state[f"{SRD}.entities"]["guard"]
    saved = deepcopy(guard["last_spirit_guardians_save"])
    assert saved["raw_damage"]["count"] == 5 and 960 <= guard["hp"] <= 995
    before = guard["hp"]
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == before
    _turn(state, "guard")
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["hp"] < before


def test_spirit_guardians_speed_halves_real_speed_preserves_dash_spent_and_removes():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _guardians_cast()
    _turn(state, "guard")
    state[f"{SRD}.combat"].update(dashes_taken=1, movement_spent=7)
    _run("refresh_srd_movement_budget", state, actor_state="guard")
    assert state[f"{SRD}.combat"]["movement_remaining"] == 23
    _run("end_concentration", state, caster_state="hero")
    assert not state[f"{SRD}.state"]["has_spirit_guardians"]
    assert state[f"{SRD}.combat"]["movement_remaining"] == 53
    assert state[f"{SRD}.combat"]["movement_spent"] == 7


def test_spirit_guardians_actual_unaffected_creature_keeps_speed_and_no_save():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _guardians_cast(unaffected=("guard",))
    _turn(state, "guard")
    assert state[f"{SRD}.combat"]["movement_remaining"] == 30
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    assert state[f"{SRD}.entities"]["guard"].get("last_spirit_guardians_save") is None


def test_spirit_guardians_actual_entry_reduces_speed_without_difficult_terrain():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _guardians_cast(unaffected=("other",))
    _position(state, "guard")["position"] = [20, 0, 0]
    _turn(state, "guard")
    assert state[f"{SRD}.combat"]["movement_remaining"] == 30
    result = _run("move_srd_scene_creature", state, actor_state="guard", destination=[10, 0, 0])
    assert result.result["context"]["_scene_movement_cost"] == 10
    assert _position(state, "guard")["position"] == [10, 0, 0]
    assert state[f"{SRD}.combat"]["movement_remaining"] == 5
    assert (
        state[f"{SRD}.entities"]["guard"]["last_spirit_guardians_save"]["raw_damage"]["count"] == 3
    )
    saved = deepcopy(state[f"{SRD}.entities"]["guard"]["last_spirit_guardians_save"])
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["last_spirit_guardians_save"] == saved


def test_spirit_guardians_actual_exit_restores_speed_without_resetting_spending():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _guardians_cast(unaffected=("other",))
    _position(state, "guard")["position"] = [14, 0, 0]
    _turn(state, "guard")
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[20, 0, 0])
    assert _position(state, "guard")["position"] == [20, 0, 0]
    assert state[f"{SRD}.combat"]["movement_remaining"] == pytest.approx(24)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000


def test_spirit_guardians_caster_actual_walking_enters_other_space_once():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, _ = _guardians_cast(unaffected=("other",))
    _position(state, "guard")["position"] = [25, 0, 0]
    _turn(state, "hero", data)
    moved = _run(
        "move_srd_scene_creature",
        state,
        participant_data=data,
        actor_state="hero",
        destination=[10, 0, 0],
    )
    assert moved.result["context"]["_scene_movement_cost"] == 10
    saved = state[f"{SRD}.entities"]["guard"]["last_spirit_guardians_save"]
    assert 9 < saved["origin"][0] < 10 and saved["raw_damage"]["count"] == 3
    _run(
        "move_srd_scene_creature",
        state,
        participant_data=data,
        actor_state="hero",
        destination=[11, 0, 0],
    )
    assert state[f"{SRD}.entities"]["guard"]["last_spirit_guardians_save"] == saved


def test_spirit_guardians_successful_save_typed_resistance_temp_hp_share_one_packet():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _guardians_cast()
    state[f"{SRD}.entities"]["guard"].update(temp_hp=3, damage_resistances=["radiant"])
    state[f"{SRD}.entities"]["guard"]["saving_throw_modifiers"]["wisdom"] = 100
    _turn(state, "guard")
    _run("end_combat_turn", state)
    guard = state[f"{SRD}.entities"]["guard"]
    saved = guard["last_spirit_guardians_save"]
    typed = (saved["raw_damage"]["total"] // 2) // 2
    assert saved["outcome"] == "success" and saved["applied_damage"] == typed
    assert guard["temp_hp"] == max(0, 3 - typed)
    assert guard["hp"] == 1000 - max(0, typed - 3)


@pytest.mark.parametrize("change", ["freedom", "plane", "wall"])
def test_spirit_guardians_actual_freedom_plane_wall_qualify_speed_and_damage(change):
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _guardians_cast(unaffected=("other",))
    if change == "freedom":
        _run(
            "install_srd_actor_spell_modifier",
            state,
            caster_state="other",
            target_state="guard",
            spell="freedom_of_movement",
            lasting_state="state",
            mode="freedom",
            save_dc=18,
            duration_rounds=600,
            concentration=False,
            actual_spell_level=4,
        )
    elif change == "plane":
        _position(state, "guard")["metadata"]["plane"] = "ethereal"
    else:
        scene = state[f"{SRD}.state"]["spatial_scene"]
        wall = deepcopy(scene["geometry"][0])
        wall.update(
            id="spirit-wall",
            volume={"kind": "box", "min": [4, -10, 0], "max": [5, 10, 10]},
            metadata={"plane": "material"},
        )
        scene["geometry"].append(wall)
    _turn(state, "guard")
    assert state[f"{SRD}.combat"]["movement_remaining"] == 30
    _run("end_combat_turn", state)
    assert (state[f"{SRD}.entities"]["guard"]["hp"] < 1000) == (change == "freedom")


def test_spirit_guardians_actual_clock_expiry_clears_source_speed_and_later_damage():
    state, _, _ = _guardians_cast()
    _run("advance_srd_lasting_time", state, elapsed_rounds=100)
    assert not state[f"{SRD}.state"]["has_spirit_guardians"]
    _run("resolve_srd_spirit_guardians_event", state, target_state="guard", area_id="hero")
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    out = _run("move_srd_scene_creature", state, actor_state="guard", destination=[15, 0, 0])
    assert out.result["context"]["_scene_movement_cost"] == 5


def test_spirit_guardians_new_admitted_creature_is_affected_without_a_cast_target_alias():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, _, _ = _guardians_cast(unaffected=("guard", "other"))
    state[f"{SRD}.entities"]["late"] = deepcopy(state[f"{SRD}.entities"]["guard"])
    entry = deepcopy(_position(state, "guard"))
    entry.update(
        id="late",
        position=[10, 4, 0],
        metadata={"participant_ref": "late", "plane": "material", "lighting": "bright"},
    )
    state[f"{SRD}.state"]["spatial_scene"]["entities"].append(entry)
    _turn(state, "late")
    assert state[f"{SRD}.combat"]["movement_remaining"] == 15
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["late"]["hp"] < 1000
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000


def test_public_spirit_guardians_paid_cast_entry_reopens_retry_then_real_removal(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as public_casting

    def actual_fixture(_name):
        state, data, parameters = _guardians_case(ethic="evil", unaffected=("other",))
        _position(state, "guard")["position"] = [20, 0, 0]
        return state, data, parameters

    monkeypatch.setattr(public_casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(public_casting, "_spell_case", actual_fixture)
    database = tmp_path / "spirit-guardians.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = public_casting._create_spell_campaign(
            owner, "spirit_guardians", target_overrides={"hp": 1000, "temp_hp": 0}
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="guardian-cast",
            idempotency_key="guardian-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=parameters,
        )
        first = StatelessMcpMechanics(owner).execute_action(command)
        assert first.status is ActionStatus.COMMITTED
        snapshot = owner.snapshot("casting")
        assert snapshot.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
        entered = replace(
            command,
            action_id="guardian-enter",
            idempotency_key="guardian-enter",
            expected_revision=snapshot.revision,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "guard", "destination": [10, 0, 0]},
        )
        moved = StatelessMcpMechanics(owner).execute_action(entered)
        assert moved.status is ActionStatus.COMMITTED
        saved = owner.snapshot("casting")
        guard = saved.state[f"{SRD}.entities"]["guard"]
        assert guard["hp"] < 1000 and guard["scene_movement_spent"] == 10
        assert guard["last_spirit_guardians_save"]["raw_damage"]["count"] == 3
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert mechanics.execute_action(entered).result == moved.result
        assert mechanics.execute_action(command).result == first.result
        assert owner.snapshot("casting").state == saved.state
        ordinary = replace(
            entered,
            action_id="guardian-extra",
            idempotency_key="guardian-extra",
            expected_revision=saved.revision,
            parameters={"actor_state": "guard", "destination": [12, 0, 0]},
        )
        continued = mechanics.execute_action(ordinary)
        assert continued.status is ActionStatus.COMMITTED
        current = owner.snapshot("casting")
        assert current.state[f"{SRD}.entities"]["guard"]["hp"] == guard["hp"]
        ended = mechanics.execute_action(
            replace(
                command,
                action_id="guardian-end",
                idempotency_key="guardian-end",
                expected_revision=current.revision,
                tool_name="end_concentration",
                parameters={"caster_state": "hero"},
            )
        )
        assert ended.status is ActionStatus.COMMITTED
        freed = owner.snapshot("casting")
        assert not freed.state[f"{SRD}.state"]["has_spirit_guardians"]
        later = mechanics.execute_action(
            replace(
                ordinary,
                action_id="guardian-gone",
                idempotency_key="guardian-gone",
                expected_revision=freed.revision,
                parameters={"actor_state": "guard", "destination": [20, 0, 0]},
            )
        )
        assert later.status is ActionStatus.COMMITTED
        assert owner.snapshot("casting").state[f"{SRD}.entities"]["guard"]["hp"] == guard["hp"]
