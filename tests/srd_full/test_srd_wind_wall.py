"""Actual Wind Wall appearance must retain later source-derived passage and missiles."""

from copy import deepcopy

import pytest

from sylvia_gm.application.package_runtime import PackageRuntimeError
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_attack_budget import _attack
from tests.srd_full.test_srd_spell_environment import SRD, _fixture, _ground, _position
from tests.srd_full.test_srd_spell_wave3_orders import _turn


def _case(*, inside=False, slot=3):
    state, data, params = _fixture("wind_wall", (40, 0, 0), slot)
    _ground(state)
    params.update(environment_wall_axis="north_south")
    for name, point in (
        ("guard", [40 if inside else 30, 0, 0]),
        ("other", [40 if inside else 10, 8, 0]),
    ):
        _position(state, name)["position"] = point
        state[f"{SRD}.entities"][name].update(hp=1000, max_hp=1000, temp_hp=0, size_rank=2)
        state[f"{SRD}.entities"][name]["saving_throw_modifiers"]["strength"] = (
            -100 if name == "guard" else 100
        )
    return state, data, params


def _cast(**choices):
    state, data, params = _case(**choices)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    return state, data, params


def test_wind_actual_paid_appearance_is_shared_four_d8_strength_half_without_upcast_scaling():
    state, _, _ = _cast(inside=True, slot=6)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["volume"] == {"kind": "box", "min": [39.5, -25, 0], "max": [40.5, 25, 15]}
    assert source["ground_id"] == "floor" and source["appearance_completed"]
    first = state[f"{SRD}.entities"]["guard"]["last_wind_wall_save"]
    second = state[f"{SRD}.entities"]["other"]["last_wind_wall_save"]
    assert first["raw_damage"] == second["raw_damage"] == source["initial_damage_roll"]
    assert first["raw_damage"]["count"] == 4 and first["raw_damage"]["sides"] == 8
    assert first["outcome"] == "failure" and second["outcome"] == "success"
    assert first["applied_damage"] == first["raw_damage"]["total"]
    assert second["applied_damage"] == first["raw_damage"]["total"] // 2
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2


@pytest.mark.parametrize("boundary", ["missing_ground", "air", "partial_ground", "dimensions"])
def test_wind_actual_whole_supported_path_rejects_before_paid_consequences(boundary):
    state, data, params = _case()
    if boundary == "missing_ground":
        state[f"{SRD}.state"]["spatial_scene"]["geometry"] = []
    elif boundary == "air":
        params["environment_origin"][2] = 1
    elif boundary == "partial_ground":
        state[f"{SRD}.state"]["spatial_scene"]["geometry"][-1]["volume"]["max"][1] = 20
    else:
        params["environment_wind_wall_length"] = 51
    before = deepcopy(state)
    rejection = PackageRuntimeError if boundary == "dimensions" else RulesetPreconditionRejected
    with pytest.raises(rejection):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before


@pytest.mark.parametrize(
    "size,mode,blocked", [(1, "fly", True), (2, "fly", False), (1, "walk", False)]
)
def test_wind_actual_creature_size_and_flying_mode_own_later_passage(size, mode, blocked):
    state, _, _ = _cast()
    actor = state[f"{SRD}.entities"]["guard"]
    actor.update(size_rank=size, flying_speed=30)
    _turn(state, "guard")
    before = deepcopy(state)
    if blocked:
        with pytest.raises(RulesetPreconditionRejected):
            _run(
                "move_srd_scene_creature",
                state,
                actor_state="guard",
                destination=[50, 0, 0],
                mode=mode,
            )
        assert state == before
    else:
        _run(
            "move_srd_scene_creature", state, actor_state="guard", destination=[50, 0, 0], mode=mode
        )
        assert _position(state, "guard")["position"] == [50, 0, 0]
        assert state[f"{SRD}.entities"]["guard"].get("last_wind_wall_save") is None


def test_wind_actual_face_tangency_is_clear_and_plane_is_source_bound():
    state, _, _ = _cast()
    state[f"{SRD}.entities"]["guard"].update(size_rank=1, flying_speed=30)
    _position(state, "guard")["position"] = [30, 25.4, 0]
    _turn(state, "guard")
    _run(
        "move_srd_scene_creature", state, actor_state="guard", destination=[50, 25.4, 0], mode="fly"
    )
    assert _position(state, "guard")["position"] == [50, 25.4, 0]


@pytest.mark.parametrize("retirement", ["concentration", "expiry", "dispel"])
def test_wind_actual_source_removal_restores_small_flying_passage(retirement):
    state, _, _ = _cast()
    if retirement == "concentration":
        _run("end_concentration", state, caster_state="hero")
    elif retirement == "expiry":
        _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    else:
        _run(
            "dispel_srd_environment_source",
            state,
            caster_state="hero",
            area_id="hero",
            slot_level=3,
            casting_modifier=4,
            casting_ability="intelligence",
            point=[40, 0, 3],
        )
    assert not state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]
    state[f"{SRD}.entities"]["guard"].update(size_rank=1, flying_speed=30)
    _turn(state, "guard")
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[50, 0, 0], mode="fly")
    assert _position(state, "guard")["position"] == [50, 0, 0]


def test_wind_actual_gaseous_form_source_blocks_medium_creature_passage():
    state, _, _ = _cast()
    _position(state, "other")["position"] = [30.8, 0, 0]
    _run(
        "cast_spell_transformation",
        state,
        caster_state="other",
        lasting_state="state",
        spell="gaseous_form",
        actual_spell_level=3,
        save_dc=18,
        casting_modifier=4,
        casting_ability="intelligence",
        targets=["guard"],
        caster_can_touch_all_targets=True,
        all_targets_willing=True,
        concentration_save_modifier=0,
    )
    _turn(state, "guard")
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="gaseous or Small flying"):
        _run(
            "move_srd_scene_creature",
            state,
            actor_state="guard",
            destination=[45, 0, 0],
            mode="fly",
        )
    assert state == before
    assert state[f"{SRD}.entities"]["guard"]["form_current_source_id"] is not None


def test_wind_actual_canonical_arrow_attack_misses_but_spends_native_attack_and_ammunition(
    monkeypatch,
):
    from random import Random

    from sylvia_gm import dice

    state, data, _ = _cast()
    _position(state, "guard")["position"] = [50, 0, 0]
    hero = state[f"{SRD}.entities"]["hero"]
    hero["inventory"].append("shortbow")
    hero["ammunition"]["arrow"] = 2
    for actor in ("hero", "guard"):
        _run("record_srd_scene_sight_origin", state, observer_state=actor, offset=[0, 0, 3])
    _turn(state, "hero", data)
    monkeypatch.setattr(dice, "_rng", Random(5))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    result = _attack(
        state,
        data,
        weapon="shortbow",
        attack_mode="ranged",
        attack_ability="dexterity",
        wielding_two_handed=True,
        attack_distance=50,
        circumstantial_modifier=100,
    )
    assert result.result["context"]["_attack_outcome"] == "miss"
    assert result.result["context"]["_attack_roll"]["total"] == 20
    assert not result.result["context"]["_critical_hit"]
    assert result.result["context"]["_wind_projectile_deflected"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000
    assert not state[f"{SRD}.combat"]["action_available"]
    assert state[f"{SRD}.entities"]["hero"]["ammunition"]["arrow"] == 1


@pytest.mark.parametrize(
    "height,y,plane,deflected",
    [
        (3, 0, "material", True),
        (15, 0, "material", False),
        (3, 25, "material", False),
        (3, 0, "ethereal", False),
    ],
)
def test_wind_actual_projectile_eye_segment_respects_cap_side_and_foreign_plane(
    height, y, plane, deflected
):
    state, _, _ = _cast()
    for actor, x in (("hero", 0), ("guard", 50)):
        _position(state, actor)["position"] = [x, y, height - 3]
        _position(state, actor)["metadata"]["plane"] = plane
        _run("record_srd_scene_sight_origin", state, observer_state=actor, offset=[0, 0, 3])
    result = _run(
        "resolve_srd_wind_wall_projectile", state, actor_subject="hero", target_subject="guard"
    )
    assert result.result["context"]["_wind_projectile_deflected"] is deflected


def test_wind_actual_retirement_restores_the_native_arrow_hit(monkeypatch):
    from random import Random

    from sylvia_gm import dice

    state, data, _ = _cast()
    _position(state, "guard")["position"] = [50, 0, 0]
    hero = state[f"{SRD}.entities"]["hero"]
    hero["inventory"].append("shortbow")
    hero["ammunition"]["arrow"] = 2
    for actor in ("hero", "guard"):
        _run("record_srd_scene_sight_origin", state, observer_state=actor, offset=[0, 0, 3])
    _run("end_concentration", state, caster_state="hero")
    _turn(state, "hero", data)
    monkeypatch.setattr(dice, "_rng", Random(5))
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    result = _attack(
        state,
        data,
        weapon="shortbow",
        attack_mode="ranged",
        attack_ability="dexterity",
        wielding_two_handed=True,
        attack_distance=50,
        circumstantial_modifier=100,
    )
    assert result.result["context"]["_attack_outcome"] == "critical_hit"
    assert result.result["context"]["_critical_hit"]
    assert state[f"{SRD}.entities"]["guard"]["hp"] < 1000
    assert state[f"{SRD}.entities"]["hero"]["ammunition"]["arrow"] == 1


@pytest.mark.parametrize("plane,dispersed", [("material", True), ("ethereal", False)])
def test_wind_actual_clear_contact_consumes_native_fog_removal_without_foreign_plane(
    plane, dispersed
):
    state, data, params = _case()
    _position(state, "other")["metadata"]["plane"] = plane
    _run(
        "cast_spell_environment",
        state,
        caster_state="other",
        spell="fog_cloud",
        slot_level=1,
        concentration_save_modifier=0,
        origin=[50, 0, 0],
    )
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["other"]["active"]
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert bool(state[f"{SRD}.state"]["persistent_spell_areas"]["other"]["active"]) is not dispersed
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]


def test_wind_actual_retained_wall_disperses_a_later_native_fog_cast():
    state, _, _ = _cast()
    _run(
        "cast_spell_environment",
        state,
        caster_state="other",
        spell="fog_cloud",
        slot_level=1,
        concentration_save_modifier=0,
        origin=[50, 0, 0],
    )
    assert not state[f"{SRD}.state"]["persistent_spell_areas"]["other"]["active"]
    assert state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["active"]


def test_public_wind_paid_appearance_reopen_retry_later_flight_and_removal(tmp_path, monkeypatch):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from sylvia_gm.domain.errors import MechanicsRejected
    from tests import test_srd_public_spell_casting as casting
    from tests.srd_full.test_srd_spell_environment import _woodland_test_archive

    def case():
        state, data, params = _case()
        state[f"{SRD}.entities"]["guard"].update(size_rank=1, flying_speed=30)
        _position(state, "other")["position"] = [40, 8, 0]
        return state, data, params

    monkeypatch.setattr(casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(casting, "_spell_case", lambda _name: case())
    database = tmp_path / "wind-wall.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = casting._create_spell_campaign(
            owner, "wind_wall", target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0}
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="wind-cast",
            idempotency_key="wind-cast",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="cast_spell_outside_combat",
            parameters=params,
        )
        paid = StatelessMcpMechanics(owner).execute_action(command)
        assert paid.status is ActionStatus.COMMITTED, paid.result
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.entities"]["other"]["hp"] < 1000
        assert saved.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
        assert saved.state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]["appearance_completed"]
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(command).result == paid.result
        mechanics = StatelessMcpMechanics(owner)

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
                "wind-combat", "start_group_combat_encounter", combatants=["hero", "guard", "other"]
            ).status
            is ActionStatus.COMMITTED
        )
        for i in range(3):
            current = owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"]
            assert act(f"wind-begin-{i}", "begin_combat_turn").status is ActionStatus.COMMITTED
            if current == "guard":
                break
            assert act(f"wind-end-{i}", "end_combat_turn").status is ActionStatus.COMMITTED
            assert (
                act(f"wind-next-{i}", "advance_group_combat_encounter_turn").status
                is ActionStatus.COMMITTED
            )
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "guard"
        before = owner.snapshot("casting")
        with pytest.raises(MechanicsRejected, match="gaseous or Small flying"):
            act(
                "wind-blocked",
                "move_srd_scene_creature",
                actor_state="guard",
                destination=[50, 0, 0],
                mode="fly",
            )
        assert owner.snapshot("casting").state == before.state
        assert owner.snapshot("casting").revision == before.revision
        retired = replace(
            command,
            action_id="wind-retire",
            idempotency_key="wind-retire",
            expected_revision=before.revision,
            tool_name="end_concentration",
            parameters={"caster_state": "hero"},
        )
        ended = mechanics.execute_action(retired)
        assert ended.status is ActionStatus.COMMITTED, ended.result
        flight = replace(
            command,
            action_id="wind-flight",
            idempotency_key="wind-flight",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "guard", "destination": [50, 0, 0], "mode": "fly"},
        )
        moved = mechanics.execute_action(flight)
        assert moved.status is ActionStatus.COMMITTED, moved.result
        closed = owner.snapshot("casting")
        scene_guard = next(
            e
            for e in closed.state[f"{SRD}.state"]["spatial_scene"]["entities"]
            if e["id"] == "guard"
        )
        assert list(scene_guard["position"]) == [50, 0, 0]
        assert closed.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_3"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting").state == closed.state
        assert owner.execute_action(command).result == paid.result
        assert owner.execute_action(retired).result == ended.result
        assert owner.execute_action(flight).result == moved.result
        assert owner.snapshot("casting").state == closed.state
