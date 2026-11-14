"""The actual paid Fire ring preserves its inner hole and later shell consequences."""

from copy import deepcopy

import pytest

from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_environment import SRD, _fire_wall_case, _position


def _case(*, side="positive", diameter=20, target=(49.5, 0, 0), slot=4):
    state, data, params = _fire_wall_case(side=side, slot=slot)
    params.update(environment_fire_wall_shape="ring", environment_fire_ring_diameter=diameter)
    _position(state, "guard")["position"] = list(target)
    return state, data, params


def test_wall_fire_paid_ring_retains_shell_and_damages_actual_shell_body():
    state, data, params = _case()
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    source = state[f"{SRD}.state"]["persistent_spell_areas"]["hero"]
    assert source["volume"] == {
        "kind": "annulus",
        "outer": {"kind": "cylinder", "center": [40, 0, 0], "radius": 10, "height": 20},
        "inner": {"kind": "cylinder", "center": [40, 0, 0], "radius": 9, "height": 20},
    }
    assert source["appearance_completed"] and source["wall_shape"] == "ring"
    result = state[f"{SRD}.entities"]["guard"]["last_wall_of_fire_save"]
    assert result["timing"] == "initial" and result["raw_damage"]["count"] == 5
    assert state[f"{SRD}.entities"]["guard"]["hp"] < 1000


@pytest.mark.parametrize("point", [(40, 0, 0), (43, 2, 0), (61, 0, 0), (49.5, 0, 21)])
def test_wall_fire_ring_appearance_preserves_hole_outside_and_height(point):
    state, data, params = _case(target=point)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] == 1000 and guard.get("last_wall_of_fire_save") is None


@pytest.mark.parametrize("invalid", ["support", "bounds"])
def test_wall_fire_ring_whole_support_and_bounds_reject_atomically(invalid):
    from sylvia_gm.domain.errors import RulesetPreconditionRejected

    state, data, params = _case()
    if invalid == "support":
        state[f"{SRD}.state"]["spatial_scene"]["geometry"][0]["volume"]["max"][0] = 45
    else:
        scene = state[f"{SRD}.state"]["spatial_scene"]
        scene["map"]["bounds"]["max"][2] = 10
        scene["map"]["layers"][0]["max_elevation"] = 10
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("cast_spell_outside_combat", state, participant_data=data, **params)
    assert state == before


@pytest.mark.parametrize(
    "start,end,plane,opaque",
    [
        ([40, 0, 3], [43, 2, 3], "material", False),
        ([40, 0, 3], [52, 0, 3], "material", True),
        ([49.1, 0, 3], [49.9, 0, 3], "material", True),
        ([20, 10, 3], [60, 10, 3], "material", False),
        ([20, 9, 3], [60, 9, 3], "material", True),
        ([20, 0, 0], [60, 0, 0], "material", False),
        ([20, 0, 20], [60, 0, 20], "material", False),
        ([20, 0, 3], [60, 0, 3], "ethereal", False),
    ],
)
def test_wall_fire_ring_opacity_preserves_real_hole_and_cap_side_tangency(
    start, end, plane, opaque
):
    state, data, params = _case(target=(28, 0, 0))
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    result = _run("resolve_srd_environment_segment", state, start=start, end=end, plane=plane)
    assert result.result["context"]["_environment_sight_obscured"] is opaque


def test_wall_fire_ring_actual_crossings_have_one_entry_packet_then_separate_hot_end():
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, params = _case(target=(28, 0, 0))
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    _turn(state, "guard")
    moved = _run("move_srd_scene_creature", state, actor_state="guard", destination=[52, 0, 0])
    assert _position(state, "guard")["position"] == [52, 0, 0]
    assert moved.result["context"]["_scene_movement_cost"] == 24
    guard = state[f"{SRD}.entities"]["guard"]
    first = deepcopy(guard["last_wall_of_fire_save"])
    assert first["timing"] == "entry" and first["outcome"] == "no_save" and first["roll"] is None
    assert guard["hp"] == 1000 - first["applied_damage"]
    before = guard["hp"]
    _run("end_combat_turn", state)
    guard = state[f"{SRD}.entities"]["guard"]
    assert guard["hp"] < before and guard["last_wall_of_fire_save"]["timing"] == "end"


@pytest.mark.parametrize(
    "side,target,eligible",
    [
        ("positive", (52, 0, 0), True),
        ("negative", (52, 0, 0), False),
        ("positive", (40, 0, 0), False),
        ("negative", (40, 0, 0), True),
        ("positive", (60.4, 0, 0), True),
        ("positive", (60.41, 0, 0), False),
        ("positive", (52, 0, 31), False),
    ],
)
def test_wall_fire_ring_actual_inner_outer_hot_face_body_distance(side, target, eligible):
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, params = _case(side=side, target=target)
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    _turn(state, "guard")
    _run("end_combat_turn", state)
    assert (state[f"{SRD}.entities"]["guard"]["hp"] < 1000) is eligible


def test_wall_fire_ring_hot_face_retains_raw_source_key_when_target_is_its_caster():
    state, data, params = _case(target=(24.5, 0, 0))
    params["environment_origin"] = [15, 0, 0]
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    result = _run(
        "qualify_srd_wall_of_fire_hot_side",
        state,
        participant_data=data,
        target_state="hero",
        area_id="hero",
    )
    assert result.result["context"]["_fire_hot_side_inside"] is True


@pytest.mark.parametrize("removal", ["concentration", "expiry", "dispel"])
def test_wall_fire_ring_exact_removal_ends_later_opacity_and_packets(removal):
    from tests.srd_full.test_srd_spell_wave3_orders import _turn

    state, data, params = _case(target=(52, 0, 0))
    _run("cast_spell_outside_combat", state, participant_data=data, **params)
    if removal == "concentration":
        _run("end_concentration", state, caster_state="hero")
    elif removal == "expiry":
        _run("advance_srd_lasting_time", state, elapsed_rounds=10)
    else:
        before = deepcopy(state)
        from sylvia_gm.domain.errors import RulesetPreconditionRejected

        with pytest.raises(RulesetPreconditionRejected, match="outside that effect"):
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
        assert state == before
        _run(
            "dispel_srd_environment_source",
            state,
            participant_data=data,
            caster_state="hero",
            caster="hero",
            area_id="hero",
            point=[49.5, 0, 3],
            slot_level=4,
            casting_modifier=4,
            casting_ability="intelligence",
        )
    assert not state[f"{SRD}.state"]["has_wall_of_fire"]
    assert not _run(
        "resolve_srd_environment_segment", state, start=[40, 0, 3], end=[52, 0, 3], plane="material"
    ).result["context"]["_environment_sight_obscured"]
    _turn(state, "guard")
    _run("end_combat_turn", state)
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 1000


def test_public_fire_ring_paid_save_opacity_entry_end_reopen_retry_and_retirement(
    tmp_path, monkeypatch
):
    from dataclasses import replace

    from sylvia_gm.agent._stateless_mcp import MechanicsRejected, StatelessMcpMechanics
    from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
    from sylvia_gm.domain.authority import ActionStatus, WriterRole
    from tests import test_srd_public_spell_casting as casting
    from tests.srd_full.test_srd_spell_environment import _spell_case, _woodland_test_archive

    def actual_case(_name):
        state, data, params = _case(target=(24.5, 0, 0))
        params["environment_origin"] = [15, 0, 0]
        _position(state, "hero")["position"] = [-10, 0, 0]
        data["spellcasting_grants"][0]["spells"].append("misty_step")
        data["wizard_spellbook"].append("misty_step")
        state[f"{SRD}.entities"]["hero"]["prepared_spells"].append("misty_step")
        scene = state[f"{SRD}.state"]["spatial_scene"]
        scene["zones"].append(
            {
                "id": "ring-daylight",
                "map_id": scene["map"]["id"],
                "shape": {"kind": "box", **deepcopy(scene["map"]["bounds"])},
                "metadata": {"plane": "material", "lighting": "bright"},
            }
        )
        return state, data, params

    monkeypatch.setattr(casting, "bundled_srd_archive", _woodland_test_archive)
    monkeypatch.setattr(casting, "_spell_case", actual_case)
    database = tmp_path / "fire-ring.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = casting._create_spell_campaign(
            owner, "wall_of_fire", target_overrides={"hp": 1000, "max_hp": 1000, "temp_hp": 0}
        )
        command = ActionCommand(
            campaign_id="casting",
            action_id="ring-cast",
            idempotency_key="ring-cast",
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
        cast_state = owner.snapshot("casting")
        guard = cast_state.state[f"{SRD}.entities"]["guard"]
        initial = guard["last_wall_of_fire_save"]
        assert initial["timing"] == "initial" and initial["raw_damage"]["count"] == 5
        assert guard["hp"] == 1000 - initial["applied_damage"]
        assert cast_state.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2

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

        _, _, misty_params = _spell_case("misty_step")
        misty_params["travel_destination"] = [15, 0, 3]
        blocked = replace(
            command,
            action_id="ring-blocked-misty",
            idempotency_key="ring-blocked-misty",
            expected_revision=cast_state.revision,
            parameters=misty_params,
        )
        with pytest.raises(MechanicsRejected, match="actually see"):
            mechanics.execute_action(blocked)
        assert owner.snapshot("casting").state == cast_state.state
        assert owner.snapshot("casting").revision == cast_state.revision
        assert (
            act(
                "ring-combat", "start_group_combat_encounter", combatants=["hero", "guard", "other"]
            ).status
            is ActionStatus.COMMITTED
        )
        for i in range(3):
            current = owner.snapshot("casting").state[f"{SRD}.combat"]["current_actor"]
            assert act(f"ring-begin-{i}", "begin_combat_turn").status is ActionStatus.COMMITTED
            if current == "guard":
                break
            assert act(f"ring-end-{i}", "end_combat_turn").status is ActionStatus.COMMITTED
            assert (
                act(f"ring-advance-{i}", "advance_group_combat_encounter_turn").status
                is ActionStatus.COMMITTED
            )
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "guard"
        assert (
            act(
                "ring-hole",
                "move_srd_scene_creature",
                actor_state="guard",
                destination=[22.5, 0, 0],
            ).status
            is ActionStatus.COMMITTED
        )
        entry = replace(
            command,
            action_id="ring-entry",
            idempotency_key="ring-entry",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="move_srd_scene_creature",
            parameters={"actor_state": "guard", "destination": [24.5, 0, 0]},
        )
        entered = mechanics.execute_action(entry)
        assert entered.status is ActionStatus.COMMITTED, entered.result
        saved = owner.snapshot("casting")
        assert _position(saved.state, "guard")["position"] == [24.5, 0, 0]
        packet = saved.state[f"{SRD}.entities"]["guard"]["last_wall_of_fire_save"]
        assert packet["timing"] == "entry" and packet["roll"] is None

    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == saved.state
        assert owner.execute_action(command).result == first.result
        assert mechanics.execute_action(entry).result == entered.result
        assert owner.snapshot("casting").state == saved.state
        end = replace(
            command,
            action_id="ring-end",
            idempotency_key="ring-end",
            expected_revision=saved.revision,
            tool_name="end_combat_turn",
            parameters={},
        )
        ended = mechanics.execute_action(end)
        assert ended.status is ActionStatus.COMMITTED, ended.result
        after_end = owner.snapshot("casting")
        ended_guard = after_end.state[f"{SRD}.entities"]["guard"]
        assert ended_guard["last_wall_of_fire_save"]["timing"] == "end"
        assert ended_guard["hp"] < saved.state[f"{SRD}.entities"]["guard"]["hp"]
        retire = replace(
            command,
            action_id="ring-retire",
            idempotency_key="ring-retire",
            expected_revision=after_end.revision,
            tool_name="end_concentration",
            parameters={"caster_state": "hero"},
        )
        retired = mechanics.execute_action(retire)
        assert retired.status is ActionStatus.COMMITTED, retired.result
        finish = replace(
            command,
            action_id="ring-finish",
            idempotency_key="ring-finish",
            expected_revision=owner.snapshot("casting").revision,
            tool_name="end_combat_encounter",
            parameters={"end_reason": "fighting_stopped"},
        )
        assert mechanics.execute_action(finish).status is ActionStatus.COMMITTED
        misty = replace(
            blocked,
            action_id="ring-cleared-misty",
            idempotency_key="ring-cleared-misty",
            expected_revision=owner.snapshot("casting").revision,
        )
        teleported = mechanics.execute_action(misty)
        assert teleported.status is ActionStatus.COMMITTED, teleported.result
        cleared = owner.snapshot("casting")
        assert _position(cleared.state, "hero")["position"] == [15, 0, 3]
        assert not cleared.state[f"{SRD}.state"]["has_wall_of_fire"]
        assert cleared.state[f"{SRD}.entities"]["guard"]["hp"] == ended_guard["hp"]
        assert cleared.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2
    with AuthorityOwner.open(database, "casting-owner") as owner:
        mechanics = StatelessMcpMechanics(owner)
        assert owner.snapshot("casting").state == cleared.state
        assert owner.execute_action(command).result == first.result
        assert owner.execute_action(entry).result == entered.result
        assert owner.execute_action(end).result == ended.result
        assert owner.execute_action(retire).result == retired.result
        assert mechanics.execute_action(misty).result == teleported.result
        assert owner.snapshot("casting").state == cleared.state
