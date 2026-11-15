"""Late Storm visibility reads its real stage/source and preserves existing sense owners."""

from copy import deepcopy
from functools import lru_cache

import pytest
import yaml
from scripts import srd_illumination_body_consumers as bodies
from scripts import srd_illumination_object_consumers as objects
from scripts import srd_storm_conditions as conditions
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_retained_spell_attacks import _lists

from sylvia_gm import dice
from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_storm_later_turns as base
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_sunbeam import Rolls

_BASE_RUNTIME = base._runtime


@lru_cache
def _parent_runtime():
    parent = deepcopy(_BASE_RUNTIME()[0])
    parent["tools"].update(objects._tools(parent))
    parent["tools"].update(bodies._tools(parent))
    return parent


@lru_cache
def _runtime():
    parent = _parent_runtime()
    encoded = conditions.bind_runtime_consumers(
        yaml.dump(parent, Dumper=yaml.CSafeDumper, sort_keys=False)
    )
    return yaml.load(encoded, Loader=yaml.CSafeLoader), encoded.encode()


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


def _case(stage=5):
    state, data, params = base.base._case()
    base.base._cast(state, data, params)
    for turn in range(6, 5 + stage):
        base._start(state, data, turn)
    assert base.base._sources(state)[0]["storm_ordinal"] == stage
    return state, data, params


def _point(state, point, plane="material"):
    return _run(conditions.POINT, state, point=point, plane=plane).result["context"]


def _segment(state, start, end, plane="material"):
    return _run(conditions.SEGMENT, state, start=start, end=end, plane=plane).result["context"]


def _sight(state):
    return _run(
        "resolve_srd_illumination_target_sight",
        state,
        observer_state="hero",
        target_state="guard",
    ).result["context"]["_illumination_point_can_see"]


def test_same_actual_source_stage_point_plane_altitude_and_query_nonmutation():
    state, data, _ = _case(4)
    assert _point(state, [30, 0, 0])["_storm_heavy_obscurement"] is False
    base._start(state, data, 9)
    before = deepcopy(state)
    for point, plane, expected in (
        ([30, 0, 0], "material", True),
        ([320, 5, 49.9], "material", True),
        ([320, 5, 50], "material", False),
        ([620, 5, 0], "material", True),
        ([620.001, 5, 0], "material", False),
        ([30, 0, 0], "ethereal", False),
    ):
        result = _point(state, point, plane)
        assert all(result[name] is expected for name in conditions.POINT_EXPORTS)
    assert state == before


def test_strict_source_intervals_distinguish_floor_clip_top_and_side_tangency(monkeypatch):
    state, _, _ = _case()
    floor = state[f"{SRD}.state"]["spatial_scene"]["map"]["bounds"]["min"][2]
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    before = deepcopy(state)
    result = _segment(state, [0, 5, floor], [640, 5, floor])
    assert result["_storm_heavy_segment"] is True and result["_storm_strong_wind_segment"] is True
    interval = result["_storm_difficult_intervals"][0]
    assert interval["source_id"] == base.base._sources(state)[0]["source_id"]
    assert interval["entry"] == pytest.approx(20 / 640)
    assert interval["exit"] == pytest.approx(620 / 640)
    for start, end in (
        ([20, 5, 50], [620, 5, 50]),
        ([20, 305, 0], [620, 305, 0]),
    ):
        result = _segment(state, start, end)
        assert (
            result["_storm_heavy_segment"] is False and result["_storm_difficult_intervals"] == []
        )
    assert _segment(state, [30, 0, 0], [30, 0, 0])["_storm_heavy_segment"] is True
    assert state == before and rolls.sides == []


def test_actual_target_sight_and_bounded_native_blindsight_then_exact_source_end():
    state, data, _ = _case(4)
    assert _sight(state) is True
    base._start(state, data, 9)
    assert state[f"{SRD}.state"].get("has_spell_environments") is not True
    assert _sight(state) is False
    state[f"{SRD}.entities"]["hero"].update(conditions=["blinded"], blindsight_feet=30)
    assert _sight(state) is True
    state[f"{SRD}.entities"]["hero"]["blindsight_feet"] = 29
    assert _sight(state) is False
    _run("end_concentration", state, caster_state="hero")
    assert _point(state, [30, 0, 0])["_storm_heavy_obscurement"] is False
    assert "blinded" in state[f"{SRD}.entities"]["hero"]["conditions"]
    state[f"{SRD}.entities"]["hero"]["conditions"] = []
    assert _sight(state) is True


def test_real_admitted_object_samples_use_storm_guard_without_fabricating_environment_source():
    state, data, params = base.base._case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    obj = next(row for row in scene["entities"] if row["id"] == "orb")
    obj["position"] = [60, 50, 0]
    obj["metadata"].update(is_object=True, plane="material", lighting="bright")
    _run(
        "prepare_srd_spell_object",
        state,
        object_id="orb",
        material="wood",
        size="small",
        durability="resilient",
    )
    base.base._cast(state, data, params)
    for turn in range(6, 10):
        base._start(state, data, turn)
    assert not scene["zones"] and state[f"{SRD}.state"].get("has_spell_environments") is not True
    result = _run(
        "resolve_srd_spell_object_relation",
        state,
        caster_state="hero",
        object_id="orb",
        max_range_feet=120,
    ).result["context"]
    assert result["_object_can_see"] is False
    _run("end_concentration", state, caster_state="hero")
    result = _run(
        "resolve_srd_spell_object_relation",
        state,
        caster_state="hero",
        object_id="orb",
        max_range_feet=120,
    ).result["context"]
    assert result["_object_can_see"] is True


def test_repeated_emission_preserves_native_roll_packet_and_only_widens_actual_sight_guard():
    runtime, encoded = _runtime()
    assert (
        yaml.load(conditions.bind_runtime_consumers(encoded.decode()), Loader=yaml.CSafeLoader)
        == runtime
    )
    parent = _BASE_RUNTIME()[0]
    for name in ("apply_srd_qualifying_damage", "modify_srd_actor_damage_roll"):
        assert runtime["tools"][name] == parent["tools"][name]
    original_d20 = deepcopy(runtime["tools"]["d20_test"])
    for sequence in _lists(original_d20["effects"]):
        for effect in sequence:
            if "condition" in effect:
                for state_name in ("lasting_state", "state"):
                    old = state_name + ".has_spell_environments == true"
                    qualified = "(" + old + " or " + state_name + ".has_storm_sources == true)"
                    effect["condition"] = effect["condition"].replace(qualified, old)
    assert original_d20 == parent["tools"]["d20_test"]
    relation = runtime["tools"]["resolve_srd_spell_object_relation"]
    guards = [
        e["condition"]
        for s in _lists(relation["effects"])
        for e in s
        if "_illumination_body_samples" in str(e.get("on_true", []))
    ]
    assert any("has_storm_sources" in guard for guard in guards)


def test_paid_native_turns_public_sight_reopen_end_and_retry_keep_original_cloud(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(
        base.base.base.public_casting, "bundled_srd_archive", base.base.base._archive
    )
    monkeypatch.setattr(
        base.base.base.public_casting, "_spell_case", lambda _spell: base.base._case()
    )
    rolls, measured = Rolls(), []
    monkeypatch.setattr(dice, "_rng", rolls)
    actual = ExecutionBudget.consume_operation

    def count(budget):
        if not measured or measured[-1] is not budget:
            measured.append(budget)
        actual(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    database, receipts = tmp_path / "storm-weather.db", []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = base.base.base.public_casting._create_spell_campaign(
            owner, base.base.storm.SPELL, target_overrides={"hp": 1000, "max_hp": 1000}
        )
        receipts.append(_act(owner, "cast_spell_outside_combat", **params))
        receipts.append(
            _act(
                owner,
                "start_combat_encounter_entities",
                first_actor="hero",
                second_actor="guard",
                first_surprised=False,
                second_surprised=False,
                tie_winner="first",
            )
        )
        for index in range(4):
            receipts.append(_act(owner, "begin_combat_turn"))
            if index < 3:
                for name in (
                    "end_combat_turn",
                    "advance_combat_encounter_turn",
                    "begin_combat_turn",
                    "end_combat_turn",
                    "advance_combat_encounter_turn",
                ):
                    receipts.append(_act(owner, name))
        saved = owner.snapshot("casting")
        assert base.base._sources(saved.state)[0]["storm_ordinal"] == 5
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        receipts.append(
            _act(owner, "resolve_srd_scene_relation", actor_state="hero", target_state="guard")
        )
        assert receipts[-1][0].result["context"]["_scene_can_see"] is False
        receipts.append(_act(owner, "end_concentration", caster_state="hero"))
        receipts.append(
            _act(owner, "resolve_srd_scene_relation", actor_state="hero", target_state="guard")
        )
        assert receipts[-1][0].result["context"]["_scene_can_see"] is True
        saved, faces = owner.snapshot("casting"), deepcopy(rolls.sides)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        for result, command in receipts:
            assert result.status is ActionStatus.COMMITTED
            assert owner.execute_action(command) == result
        assert owner.snapshot("casting") == saved and rolls.sides == faces
    assert all(b.max_operations == 10000 for b in measured)
    print("Storm weather/public max operations:", max(b.operations for b in measured))
