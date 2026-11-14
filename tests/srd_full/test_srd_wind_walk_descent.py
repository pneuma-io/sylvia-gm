"Actual ended cloud flight descends over real time onto admitted supporting geometry."

from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from random import Random

import pytest

from sylvia_gm import dice
from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner, CampaignCreate
from sylvia_gm.domain.authority import ActionStatus, StateValue, WriterRole, thaw_json_value
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests.test_spatial_engine import _geometry
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_public_spell_casting import _create_spell_campaign
from tests.srd_full.test_srd_scene_relations import _state
from tests.srd_full.test_srd_spell_result_cards import SRD
from tests.srd_full.test_srd_spell_wave6_transformations import _entity, _run, _setup


def _scene_flight(height=120, ground=0):
    state, _, _ = _setup("wind_walk")
    scene = deepcopy(_state()[f"{SRD}.state"]["spatial_scene"])
    scene["map"]["bounds"]["max"][2] = 1100
    scene["map"]["layers"][0]["max_elevation"] = 1100
    scene["entities"][1]["position"] = [5, 0, height]
    floor = _geometry("supported-ground", [-100, -100, ground - 10], [100, 100, ground])
    floor["map_id"] = scene["map"]["id"]
    floor["metadata"].update(ground=True, plane="material")
    scene["geometry"] = [floor]
    state[f"{SRD}.state"]["spatial_scene"] = scene
    _run(
        "install_srd_transformation_source",
        state,
        target_state="guard",
        caster_state="hero",
        lasting_state="state",
        spell="wind_walk",
        mode="cloud",
        actual_spell_level=6,
    )
    return state, _entity(state)["form_current_source_id"]


def _height(state):
    entities = state[f"{SRD}.state"]["spatial_scene"]["entities"]
    return next(body["position"][2] for body in entities if body["id"] == "guard")


def test_ended_wind_cloud_descends_sixty_feet_each_real_round_then_lands_safely():
    state, source_id = _scene_flight()
    initial_hp = _entity(state)["hp"]
    _run("end_srd_transformation_source", state, target_state="guard", source_id=source_id)
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _height(state) == 60
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _height(state) == 0
    assert _entity(state)["hp"] == initial_hp
    assert "prone" not in _entity(state)["conditions"]
    assert state[f"{SRD}.state"]["has_wind_walk_descent"] is False
    assert state[f"{SRD}.state"]["wind_walk_descent_target_ids"] == []


def test_expiry_during_larger_clock_jump_uses_exact_end_and_elevated_ground():
    state, source_id = _scene_flight(height=180, ground=30)
    _run("advance_srd_lasting_time", state, elapsed_rounds=4801)
    target = _entity(state)
    assert _height(state) == 120
    assert target["wind_walk_descent"]["started_tick"] == 9600
    assert state[f"{SRD}.state"]["transformation_sources"][source_id]["installed"] is False
    _run("advance_srd_lasting_time", state, elapsed_rounds=2)
    assert _height(state) == 30
    assert _entity(state)["wind_walk_descent"]["status"] == "landed"


def test_tenth_round_falls_only_remaining_distance_once(monkeypatch):
    state, source_id = _scene_flight(height=720)
    _entity(state).update(hp=1000, max_hp=1000, temp_hp=0)
    _run("end_srd_transformation_source", state, target_state="guard", source_id=source_id)
    monkeypatch.setattr(dice, "_rng", Random(0))
    monkeypatch.setattr(dice, "set_seed", lambda _: None)
    rng = Random(0)
    expected = sum(rng.randint(1, 6) for _ in range(12))
    _run("advance_srd_lasting_time", state, elapsed_rounds=9)
    assert _height(state) == 180 and _entity(state)["hp"] == 1000
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _height(state) == 0
    assert _entity(state)["hp"] == 1000 - expected
    assert "prone" in _entity(state)["conditions"]
    assert _entity(state)["wind_walk_descent"]["status"] == "fallen"
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _entity(state)["hp"] == 1000 - expected


@pytest.mark.parametrize("blocked", ["solid", "absent_ground", "other_plane"])
def test_unresolved_or_blocked_landing_rejects_clock_without_leaked_movement(blocked):
    state, source_id = _scene_flight()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    if blocked == "solid":
        barrier = _geometry("ceiling", [-100, -100, 70], [100, 100, 80])
        barrier["map_id"] = scene["map"]["id"]
        barrier["metadata"]["plane"] = "material"
        scene["geometry"].append(barrier)
    elif blocked == "absent_ground":
        scene["geometry"] = []
    else:
        scene["geometry"][0]["metadata"]["plane"] = "ethereal"
    _run("end_srd_transformation_source", state, target_state="guard", source_id=source_id)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="supporting ground|clear path"):
        _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert state == before


def test_new_actual_wind_source_supersedes_old_descent_window():
    state, source_id = _scene_flight()
    _run("end_srd_transformation_source", state, target_state="guard", source_id=source_id)
    old_serial = _entity(state)["wind_walk_descent"]["source_serial"]
    _run(
        "install_srd_transformation_source",
        state,
        target_state="guard",
        caster_state="hero",
        lasting_state="state",
        spell="wind_walk",
        mode="cloud",
        actual_spell_level=6,
    )
    new_id = _entity(state)["form_current_source_id"]
    assert state[f"{SRD}.state"]["transformation_sources"][new_id]["serial"] != old_serial
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    assert _height(state) == 120
    assert _entity(state)["wind_walk_descent"]["status"] == "superseded"
    assert state[f"{SRD}.state"]["has_wind_walk_descent"] is False


def test_descent_composition_repeats_without_duplicate_clock_or_registration():
    from scripts.srd_full.srd_form_descent import compose
    from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

    runtime = _load_runtime(
        (Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text(
            encoding="utf-8"
        )
    )
    tools = runtime["tools"]
    changed = {"project_srd_transformation": deepcopy(tools["project_srd_transformation"])}
    compose(tools, changed)
    once = deepcopy(changed)
    compose(tools, changed)
    assert changed == once
    assert "state.spatial_scene" in str(changed["settle_srd_wind_walk_descent_target"])


def test_public_minute_cast_expiry_descent_reopens_and_retries_once(tmp_path):
    database = tmp_path / "wind-descent.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, parameters = _create_spell_campaign(owner, "wind_walk")
        original = owner.snapshot("casting")
        world = {key: thaw_json_value(value) for key, value in original.state.items()}
        flight, _ = _scene_flight()
        scene = deepcopy(flight[f"{SRD}.state"]["spatial_scene"])
        # The real public cast occurs within thirty feet before the cloud flight expires.
        for body in scene["entities"]:
            body["position"][2] = 120
        world[f"{SRD}.state"]["spatial_scene"] = scene
        owner.create_campaign(
            CampaignCreate(
                campaign_id="wind",
                display_name="Saved Wind descent",
                writer=WriterRole.DM,
                package_lock=original.package_lock,
                turn_id="wind-turn",
                participants=original.participants,
                initial_state=tuple(
                    StateValue(SRD, key.removeprefix(f"{SRD}."), value)
                    for key, value in world.items()
                ),
            )
        )
        command = ActionCommand(
            campaign_id="wind",
            action_id="begin-wind",
            idempotency_key="begin-wind",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="wind-turn",
            package_id=SRD,
            tool_name="begin_srd_spell_cast",
            parameters=_begin(parameters),
        )
        bridge = StatelessMcpMechanics(owner)
        assert bridge.execute_action(command).status is ActionStatus.COMMITTED
        continued = replace(
            command,
            action_id="minute",
            idempotency_key="minute",
            expected_revision=owner.snapshot("wind").revision,
            tool_name="continue_srd_spell_cast",
            parameters={
                "caster_state": "hero",
                "elapsed_rounds": 10,
                "uninterrupted": True,
                "verbal_component_available": True,
                "somatic_component_available": True,
                "material_component_available": True,
            },
        )
        assert bridge.execute_action(continued).status is ActionStatus.COMMITTED
        cast = replace(
            command,
            action_id="clouds",
            idempotency_key="clouds",
            expected_revision=owner.snapshot("wind").revision,
            tool_name="cast_spell_outside_combat",
            parameters=parameters | {"targets": ["hero", "guard"]},
        )
        assert bridge.execute_action(cast).status is ActionStatus.COMMITTED
        saved_cast = owner.snapshot("wind")
        assert saved_cast.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
        advance = replace(
            command,
            action_id="expiry-round",
            idempotency_key="expiry-round",
            expected_revision=saved_cast.revision,
            tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 4801},
        )
        receipt = bridge.execute_action(advance)
        assert receipt.status is ActionStatus.COMMITTED
        saved = owner.snapshot("wind")
        assert _height(saved.state) == 60
    with AuthorityOwner.open(database, "casting-owner") as owner:
        bridge = StatelessMcpMechanics(owner)
        assert owner.snapshot("wind").state == saved.state
        assert bridge.execute_action(advance) == receipt
        assert owner.snapshot("wind").state == saved.state
        landing = replace(
            advance,
            action_id="landing-round",
            idempotency_key="landing-round",
            expected_revision=saved.revision,
            parameters={"elapsed_rounds": 1},
        )
        assert bridge.execute_action(landing).status is ActionStatus.COMMITTED
        landed = owner.snapshot("wind")
        assert _height(landed.state) == 0
        assert landed.state[f"{SRD}.entities"]["guard"]["hp"] == 1
        assert "prone" not in landed.state[f"{SRD}.entities"]["guard"]["conditions"]
        assert landed.state[f"{SRD}.state"]["has_wind_walk_descent"] is False
