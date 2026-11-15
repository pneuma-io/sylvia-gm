"""Storm's earned rain damages real prepared objects without a fictional attack."""

from copy import deepcopy
from functools import lru_cache

import pytest
import yaml
from scripts import srd_storm_objects as objects
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm import dice
from sylvia_gm.application import package_runtime
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_storm_later_turns as base
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_sunbeam import Rolls

_BASE_RUNTIME = base._runtime


@lru_cache
def _runtime():
    original = _BASE_RUNTIME()[0]
    encoded = objects.bind_runtime_consumers(
        yaml.dump(original, Dumper=yaml.CSafeDumper, sort_keys=False)
    )
    runtime = yaml.load(encoded, Loader=yaml.CSafeLoader)
    for name in (
        "d20_test",
        "apply_srd_qualifying_damage",
        "modify_srd_actor_damage_roll",
        "resolve_srd_spell_object_attack",
        "resolve_srd_spell_object_relation",
    ):
        assert runtime["tools"][name] == original["tools"][name]
    return runtime, encoded.encode()


@lru_cache
def _mechanic(name):
    runtime, encoded = _runtime()
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=encoded,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=_tool_schema(runtime["tools"][name], tool_name=name),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _owners(monkeypatch):
    runtime, _ = _runtime()
    monkeypatch.setattr(base.base.base, "_runtime", _runtime)
    monkeypatch.setattr(base.base.base.budget, "_mechanic", _mechanic)
    monkeypatch.setattr(
        base.base.base.cards, "_spell_definitions", lambda: runtime["definitions"]["spells"]
    )
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Rolls())
    actual = package_runtime.execute_tool

    def diagnose(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnose)


def _case():
    state, data, params = base.base._case()
    _run(
        "prepare_srd_spell_object",
        state,
        object_id="orb",
        material="wood",
        size="large",
        durability="resilient",
    )
    base.base._cast(state, data, params)
    return state, data


def _profile(state):
    row = next(r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "orb")
    return row["metadata"]["srd_spell_object"]


def test_actual_paid_storm_stage_two_uses_one_shared_roll_and_one_object_hp_transition(monkeypatch):
    state, data = _case()
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    revision = state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"]
    result = base._start(state, data, 6)
    assert _profile(state)["hp"] == 15
    assert state[f"{SRD}.state"]["spatial_scene"]["map"]["revision"] == revision + 1
    assert rolls.sides == [6] * 4
    record = base.base._sources(state)[0]
    assert record["storm_last_stage_result"]["object_targets"] == ["orb"]
    assert record["storm_last_stage_result"]["targets"] == ["guard", "other"]
    assert "object damage" in str(result["context"]["_turn_effect_rows"])
    before = deepcopy(state)
    base._start(state, data, 6)
    assert state == before and rolls.sides == [6] * 4
    assert "orb" not in state[f"{SRD}.entities"]


def test_outside_plane_and_unprepared_object_paths_do_not_manufacture_statistics(monkeypatch):
    state, data = _case()
    row = next(r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "orb")
    row["metadata"]["plane"] = "ethereal"
    base._start(state, data, 6)
    assert _profile(state)["hp"] == 27
    assert base.base._sources(state)[0]["storm_last_stage_result"]["object_targets"] == []
    state, data = _case()
    row = next(r for r in state[f"{SRD}.state"]["spatial_scene"]["entities"] if r["id"] == "orb")
    row["metadata"].pop("srd_spell_object")
    state[f"{SRD}.combat"]["encounter_active"] = True
    state[f"{SRD}.entities"]["hero"]["turns_started"] = 6
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="Breaking Objects"):
        base._start(state, data, 6)
    assert state == before


def test_repeat_and_final_native_object_callbacks_survive_earned_packet_capture():
    from tests.srd_full.test_srd_flame_blade_ice_objects import CALLBACK, _calls, _parent

    original = _parent()[0]
    qualified = objects._qualification(original["tools"])
    packet = objects._packet(original["tools"])
    assert "qualify_srd_wall_of_ice_object" in set(_calls(qualified["effects"]))
    for callback in (CALLBACK, "settle_srd_wall_of_ice_panel"):
        actual = [
            effect
            for sequence in objects._lists(
                original["tools"]["resolve_srd_spell_object_attack"]["effects"]
            )
            for effect in sequence
            if effect.get("call") == callback
        ]
        copied = [
            effect
            for sequence in objects._lists(packet["effects"])
            for effect in sequence
            if effect.get("call") == callback
        ]
        assert copied == actual and len(copied) == 1
    assert "d20_test" not in set(_calls(packet["effects"]))
    runtime, encoded = _runtime()
    assert (
        yaml.load(objects.bind_runtime_consumers(encoded.decode()), Loader=yaml.CSafeLoader)
        == runtime
    )
