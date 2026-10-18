"""Authenticated ordinary swimming spends the existing pool once across Water and Tsunami."""

import hashlib
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_ordinary_swimming import MODE, WET, bind_runtime_consumers
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_tsunami import PROFILES
from scripts.srd_full.srd_tsunami import bind_runtime_consumers as bind_tsunami
from scripts.srd_full.srd_tsunami import extend_runtime as extend_tsunami

from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as scope
from tests import test_srd_tsunami as tsunami
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_spell_environment import SRD, _fixture, _ground, _position
from tests.srd_full.test_srd_spell_wave3_orders import _turn
from tests.srd_full.test_srd_wall_ice_relocation import _calls

BASE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-ordinary-swimming/fc14-environment-control-water-flood-runtime-v1.yaml")
)
BASE_SHA256 = "ebbb14a19a500cd742fa00d9d9fd35bed339004b7ac28bbd893d87012ef5fb06"
FALL_SHA256 = "e3c4858d2b42efd1b4aba54ae570ca55a53cb87f83b6eab9b8b894fe0ad1a813"
PUBLIC = {"record_srd_water_body", "change_srd_control_water", *tsunami.PUBLIC_TOOLS}


@lru_cache
def _runtime():
    encoded = BASE.read_bytes()
    assert hashlib.sha256(encoded).hexdigest() == BASE_SHA256
    # Normal source generation reads text with universal-newline conversion.
    text = bind_tsunami(extend_tsunami(encoded.decode().replace("\r\n", "\n")))
    runtime = _load_runtime(bind_runtime_consumers(text))
    # Water's selected scope omits fall entry. Add only missing authentic leaves,
    # keeping every newer Water movement, source, damage and clock owner intact.
    falling = tsunami.BASE.read_bytes()
    assert hashlib.sha256(falling).hexdigest() == FALL_SHA256
    actual = _load_runtime(falling)["tools"]
    pending = ["begin_srd_scene_fall"]
    while pending:
        name = pending.pop()
        if name in runtime["tools"]:
            continue
        runtime["tools"][name] = deepcopy(actual[name])
        pending.extend(_calls(actual[name]))
    runtime["definitions"]["spells"].update(deepcopy(PROFILES))
    for name in (
        "cast_spell",
        "cast_spell_node",
        "cast_spell_outside_combat",
        "begin_srd_spell_cast",
        "begin_srd_spell_cast_internal",
        "begin_srd_spell_action",
    ):
        for parameter in runtime["tools"].get(name, {}).get("parameters", []):
            if parameter["name"] == "spell" and "enum_values" in parameter:
                parameter["enum_values"] = sorted(set(parameter["enum_values"]) | PROFILES.keys())
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@pytest.fixture
def swimming_scope(monkeypatch):
    monkeypatch.setattr(scope, "_runtime", _runtime)
    monkeypatch.setattr(scope, "EXTRA_PUBLIC_TOOLS", frozenset(PUBLIC))
    yield from scope.actual_fall_scope.__wrapped__(monkeypatch)


def _case(_spell="control_water", *, swim_speed=None):
    state, data, params = _fixture("control_water", (50, 0, 0))
    _ground(state)
    state[f"{SRD}.state"]["spatial_scene"]["geometry"].append(
        {
            "id": "pool",
            "map_id": "orders",
            "volume": {"kind": "box", "min": [20, -30, 0], "max": [80, 30, 20]},
            "active": True,
            "movement": "passable",
            "visibility": "clear",
            "occupies_cells": False,
            "metadata": {"plane": "material"},
            "destroyed": {"active": False, "movement": "passable", "visibility": "clear"},
        }
    )
    _position(state, "guard")["position"] = [30, 0, 0]
    state[f"{SRD}.entities"]["guard"]["swim_speed"] = swim_speed
    _position(state, "other")["position"] = [10, 8, 0]
    params.update(
        environment_water_geometry_id="pool",
        environment_water_side=50,
        environment_trench_width=10,
        environment_trench_axis="east",
        environment_water_effect="flood",
        environment_flood_rise=20,
    )
    return state, data, params


def _reject(state, **parameters):
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        _run("move_srd_scene_creature", state, actor_state="guard", **parameters)
    assert state == before


def test_actual_water_and_tsunami_cost_binding_is_singular_on_repeat():
    runtime, encoded = _runtime()
    assert _load_runtime(bind_runtime_consumers(encoded.decode())) == runtime
    movement = runtime["tools"]["move_srd_scene_creature_environment_segment"]
    assert any(effect.get("set", {}).get("field") == WET for effect in movement["effects"])
    assert MODE in str(movement["effects"])
    assert "_tsunami_ordinary_swim == true" in str(movement["effects"])


@pytest.mark.parametrize(
    "native,difficult,cost", [(None, False, 10), (None, True, 15), (30, True, 10)]
)
def test_ordinary_and_native_swims_spend_real_remaining_combat_pool_once(
    swimming_scope, native, difficult, cost
):
    state, _, _ = _case(swim_speed=native)
    _run("record_srd_water_body", state, geometry_id="pool", flow="standing")
    _turn(state, "guard")
    _run(
        "move_srd_scene_creature",
        state,
        actor_state="guard",
        destination=[35, 0, 0],
        mode="swim",
        difficult_terrain=difficult,
    )
    assert _position(state, "guard")["position"] == [35, 0, 0]
    assert state[f"{SRD}.combat"]["movement_spent"] == cost
    assert state[f"{SRD}.combat"]["movement_remaining"] == 30 - cost
    _reject(state, destination=[65, 0, 0], mode="swim", difficult_terrain=difficult)
    _reject(state, destination=[90, 0, 0], mode="swim")


def test_ordinary_exploration_swim_and_actual_clock_keep_one_pool(swimming_scope):
    state, _, _ = _case()
    _run("record_srd_water_body", state, geometry_id="pool", flow="standing")
    for destination in ([35, 0, 0], [45, 0, 0]):
        _run(
            "move_srd_scene_creature",
            state,
            actor_state="guard",
            destination=destination,
            mode="swim",
        )
    assert state[f"{SRD}.entities"]["guard"]["scene_movement_spent"] == 30
    _reject(state, destination=[46, 0, 0], mode="swim")
    _run("advance_srd_lasting_time", state, elapsed_rounds=1)
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[50, 0, 0], mode="swim")
    assert state[f"{SRD}.entities"]["guard"]["scene_movement_spent"] == 10
    assert _position(state, "guard")["position"] == [50, 0, 0]


def test_paid_tsunami_athletics_swim_then_walk_preserves_shared_cost_once(
    swimming_scope, monkeypatch
):
    state, data, params = tsunami._case()
    actual = tsunami._TravelDice([(10, 1)] * 6 + [(20, 20), (20, 20)])
    monkeypatch.setattr(tsunami.dice, "_rng", actual)
    monkeypatch.setattr(tsunami.dice, "set_seed", lambda _seed: None)
    tsunami._complete(state, data, params)
    water = deepcopy(tsunami._source(state))
    _turn(state, "guard")
    _run(
        "swim_srd_tsunami",
        state,
        actor_state="guard",
        source_id=water["id"],
        destination=[10, 0, 0],
    )
    assert state[f"{SRD}.combat"]["movement_spent"] == 10
    _run("end_concentration", state, caster_state="hero")
    _run("settle_srd_tsunami_exits", state)
    _run("move_srd_scene_creature", state, actor_state="guard", destination=[15, 0, 0], mode="walk")
    assert state[f"{SRD}.combat"]["movement_spent"] == 15
    assert _position(state, "guard")["position"] == [15, 0, 0]


def test_paid_flood_ordinary_swim_reopens_and_retries_actual_budget(
    swimming_scope, monkeypatch, tmp_path
):
    monkeypatch.setattr(public, "_spell_case", _case)
    receipts = []
    database = tmp_path / "ordinary-swim.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "control_water")
        base = ActionCommand(
            campaign_id="casting",
            action_id="fact",
            idempotency_key="fact",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="record_srd_water_body",
            parameters={"geometry_id": "pool", "flow": "standing", "body_category": "bounded"},
        )

        def act(identity, name, parameters):
            command = replace(
                base,
                action_id=identity,
                idempotency_key=identity,
                expected_revision=owner.snapshot("casting").revision,
                tool_name=name,
                parameters=parameters,
            )
            result = owner.execute_action(command)
            assert result.status is ActionStatus.COMMITTED, result.result
            saved = owner.snapshot("casting")
            assert owner.execute_action(command) == result and owner.snapshot("casting") == saved
            receipts.append((command, result))
            return saved

        act("fact", "record_srd_water_body", base.parameters)
        paid = act("paid-flood", "cast_spell_outside_combat", params)
        assert paid.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_4"] == 2
        act("combat", "start_group_combat_encounter", {"combatants": ["hero", "guard", "other"]})
        act("first-turn", "begin_combat_turn", {})
        for index in range(3):
            if owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "guard":
                break
            act(f"end-{index}", "end_combat_turn", {})
            act(f"advance-{index}", "advance_group_combat_encounter_turn", {})
            act(f"begin-{index}", "begin_combat_turn", {})
        assert owner.snapshot("casting").state[f"{SRD}.combat"]["active_actor"] == "guard"
        saved = act(
            "swim",
            "move_srd_scene_creature",
            {"actor_state": "guard", "destination": [35, 0, 0], "mode": "swim"},
        )
        assert saved.state[f"{SRD}.combat"]["movement_spent"] == 10
        assert saved.state[f"{SRD}.combat"]["movement_remaining"] == 20
        assert _position(saved.state, "guard")["position"] == [35, 0, 0]
        assert saved.state[f"{SRD}.entities"]["guard"].get("swim_speed") is None
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        for command, result in receipts:
            assert owner.execute_action(command) == result
        assert owner.snapshot("casting") == saved
