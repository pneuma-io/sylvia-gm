"""Saved GM strips become real rolled apertures and later actual falling bodies."""

from copy import deepcopy
from random import Random

import pytest
import yaml
from scripts import srd_earthquake_fissures as fissures
from scripts.srd_full.srd_runtime_defaults import restore_tool_defaults

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_earthquake as quake
from tests import test_srd_earthquake_sources as sources
from tests import test_srd_magic_jar as jar
from tests.srd_full.test_srd_npc_spell_casting import _act

SRD = quake.SRD


@pytest.fixture(scope="module", autouse=True)
def _compiled():
    with pytest.MonkeyPatch.context() as monkeypatch:
        yield from quake._compiled.__wrapped__(monkeypatch)


def _scene(state):
    return state[f"{SRD}.state"]["spatial_scene"]


def _body(state, actor):
    return next(e for e in _scene(state)["entities"] if e["id"] == actor)


def _case(*, begin=True, extra_targets=()):
    state, data, params = sources._case()
    scene = _scene(state)
    scene["map"]["bounds"] = {"min": [90, -60, -110], "max": [210, 60, 100]}
    scene["map"]["grid"].update(cell_size=5, origin=[90, -60])
    scene["map"]["layers"] = [{"id": "world", "min_elevation": -110, "max_elevation": 100}]
    scene["geometry"][0]["volume"] = {"kind": "box", "min": [90, -60, -110], "max": [210, 60, 0]}
    scene["zones"][0]["shape"] = {"kind": "box", "min": [90, -60, -110], "max": [210, 60, 100]}
    for actor, position in (
        ("hero", [150, 0, 0]),
        ("victim", [150, -50, 0]),
        ("successful", [150, -30, 0]),
    ):
        _body(state, actor)["position"] = position
    for actor, point in extra_targets:
        quake._prepare_target(state, data, actor, -10, point)
    state[f"{SRD}.combat"].update(encounter_active=True, turn_active=False, current_actor="hero")
    if begin:
        quake._run("begin_combat_turn", state, data, actor="hero")
    params.update(
        earthquake_fissures=True,
        earthquake_fissure_axis="x",
        earthquake_fissure_offsets=[-50, -30, -10, 10, 30, 50],
        earthquake_fissure_edges=["positive"] * 6,
    )
    return state, data, params


def _open(state, data, params, monkeypatch, *, seed=None):
    monkeypatch.setattr(dice, "_rng", Random(SEARCH_SEED if seed is None else seed))
    quake._cast(state, data, params)
    source = quake._source(state)
    assert source["fissures_opened"] is False and source["active"] is True
    return quake._run("end_combat_turn", state, data, actor="hero")


SEARCH_SEED = 1121


def test_paid_six_choices_open_only_at_casting_end_with_actual_saves_and_physical_slabs(
    monkeypatch,
):
    state, data, params = _case()
    slots = quake._target(state)["resources"]["spell_slot_8"]
    _open(state, data, params, monkeypatch)
    source = quake._source(state)
    print(
        "Actual fissure receipt:", source.get("fissure_count_roll"), source.get("last_event_rows")
    )
    assert source["fissure_count_roll"]["total"] == 6
    assert source["fissure_depths"] == [r["total"] * 10 for r in source["fissure_depth_rolls"]]
    assert source["fissure_opened_turn"] == source["fissure_casting_turn"]
    assert quake._target(state)["resources"]["spell_slot_8"] == slots - 1
    assert next(g for g in _scene(state)["geometry"] if g["id"] == "quake-floor")["active"] is False
    rows = {row["target"]: row for row in source["fissure_save_rows"]}
    assert rows["victim"]["outcome"] == "failure"
    assert rows["successful"]["outcome"] == "success"
    assert _body(state, "successful")["position"] == pytest.approx([150, -24.6, 0])
    assert _body(state, "victim")["position"] == [150, -50, 0]
    assert quake._target(state, "victim")["hp"] == 40
    for index, depth in enumerate(source["fissure_depths"]):
        floor = next(
            g
            for g in _scene(state)["geometry"]
            if g["id"] == f"{source['id']}:fissure:{index}:floor"
        )
        assert floor["active"] is True and floor["volume"]["max"][2] == -depth
        assert floor["metadata"]["magical_effect"] is False
    before = deepcopy(state)
    quake._run(
        "process_srd_earthquake_boundary",
        state,
        data,
        boundary_actor="hero",
        boundary_phase="end",
        boundary_turn=source["last_end_turn"],
    )
    assert state == before


def test_minimum_count_opens_only_one_vertical_strip_and_uses_its_chosen_negative_edge(monkeypatch):
    state, data, params = _case()
    params.update(
        earthquake_fissure_axis="y",
        earthquake_fissure_offsets=[100, 120, 140, 160, 180, 200],
        earthquake_fissure_edges=["negative"] * 6,
    )
    _body(state, "victim")["position"] = [100, -10, 0]
    _body(state, "successful")["position"] = [100, 10, 0]
    _open(state, data, params, monkeypatch, seed=8581)
    source = quake._source(state)
    assert source["fissure_count_roll"]["total"] == 1
    assert source["fissure_depths"] == [60]
    assert len(source["fissure_floor_ids"]) == 3
    assert _body(state, "successful")["position"] == pytest.approx([94.6, 10, 0])
    bottom = next(
        g for g in _scene(state)["geometry"] if g["id"] == f"{source['id']}:fissure:0:floor"
    )
    assert bottom["volume"] == {"kind": "box", "min": [95, -60, -110], "max": [105, 60, -60]}
    assert not any(g["metadata"].get("fissure_index") == 1 for g in _scene(state)["geometry"])


@pytest.mark.parametrize(
    "invalid",
    [
        "outside_corner",
        "shallow_floor",
        "map_floor_gap",
        "crowded_strips",
        "missing_choice",
        "edge_occupied",
    ],
)
def test_illegal_represented_plan_rejects_before_slot_or_physical_mutation(invalid):
    state, data, params = _case()
    if invalid == "outside_corner":
        _scene(state)["map"]["bounds"]["max"][0] = 250
        _scene(state)["geometry"][0]["volume"]["max"][0] = 250
        _scene(state)["zones"][0]["shape"]["max"][0] = 250
    elif invalid == "shallow_floor":
        _scene(state)["geometry"][0]["volume"]["min"][2] = -100
    elif invalid == "map_floor_gap":
        _scene(state)["geometry"][0]["volume"]["min"][0] = 91
    elif invalid == "crowded_strips":
        params["earthquake_fissure_offsets"][1] = -31
    elif invalid == "missing_choice":
        params["earthquake_fissure_edges"].pop()
    else:
        _body(state, "hero")["position"] = [150, -24.6, 0]
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        quake._cast(state, data, params)
    assert state == before


def test_fissure_emitters_preserve_selected_owners_and_repeat_exactly():
    from tests import test_srd_action_budget as budget

    text = (budget.ROOT / "runtime/ruleset.yaml").read_text()
    before = yaml.load(text, Loader=yaml.CSafeLoader)
    after = yaml.load(
        fissures.bind_completed_turn_consumer(
            fissures.bind_movement_consumers(fissures.extend_runtime(text))
        ),
        Loader=yaml.CSafeLoader,
    )
    restore_tool_defaults(before)
    restore_tool_defaults(after)
    assert before == after


def _fall_id(state):
    lasting = state[f"{SRD}.state"]
    action_id = lasting["pending_srd_action_stack"][-1]
    assert lasting["pending_srd_actions"][action_id]["kind"] == "scene_fall"
    return action_id


def test_actual_fall_completion_then_exact_dismissal_retains_physical_holes_and_prone(monkeypatch):
    state, data, params = _case()
    _open(state, data, params, monkeypatch)
    source = deepcopy(quake._source(state))
    action_id = _fall_id(state)
    event = state[f"{SRD}.state"]["pending_srd_actions"][action_id]
    assert state[f"{SRD}.combat"]["turn_active"] is False
    assert event["turn_actor"] is None
    assert event["context"]["earthquake_fissure_completed_turn"]["actor"] == "hero"
    assert event["parameters"]["falling_events"][0]["distance"] == 100
    assert event["context"]["earthquake_fissure_receipts"][0]["cast_id"] == source["cast_id"]
    assert not state[f"{SRD}.state"]["has_earthquake_fissure_falls"]
    quake._run("complete_srd_pending_action", state, data, pending_action_id=action_id)
    assert _body(state, "victim")["position"] == [150, -50, -100]
    assert quake._target(state, "victim")["scene_fall"]["status"] == "landed"
    assert quake._target(state, "victim")["hp"] < 40
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        quake._run("complete_srd_pending_action", state, data, pending_action_id=action_id)
    assert state == before
    assert quake._cost(state, data, [145, -50, -100], [150, -50, -100]) == pytest.approx(10)
    geometry = deepcopy(_scene(state)["geometry"])
    quake._run("dismiss_srd_spell", state, data, caster_state="hero", cast_id=source["cast_id"])
    assert quake._source(state)["active"] is False
    assert _scene(state)["geometry"] == geometry
    assert "prone" in quake._target(state, "victim")["conditions"]
    assert quake._cost(state, data, [145, -50, -100], [150, -50, -100]) == pytest.approx(5)


def test_eight_actual_bodies_reuse_current_scene_records_in_one_read_only_plan(monkeypatch):
    state, data, params = _case(
        extra_targets=[
            (f"strip-{index}", [170, y, 0])
            for index, y in enumerate((-50, -10, 10, 30, 50))
        ]
    )
    before = deepcopy(state)
    budgets = {}
    consume = ExecutionBudget.consume_operation

    def count(self):
        budgets[id(self)] = self
        consume(self)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    result = quake._run(
        "validate_srd_earthquake_fissure_plan",
        state,
        data,
        caster_state="hero",
        point=params["earthquake_point"],
        plane="material",
        ground_id="quake-floor",
        axis=params["earthquake_fissure_axis"],
        offsets=params["earthquake_fissure_offsets"],
        edges=params["earthquake_fissure_edges"],
    )
    assert state == before
    rows = result.result["context"]["_fissure_plan_bodies"]
    assert len(rows) == 8
    assert all(row["body"] == _body(state, row["target"]) for row in rows)
    operations = max(b.operations for b in budgets.values())
    print("Earthquake eight-body plan operations:", operations)
    assert operations <= 10000


def test_six_fissures_eight_actual_grounded_bodies_preserve_the_unchanged_operation_cap(
    monkeypatch,
):
    state, data, params = _case(
        extra_targets=[
            (f"strip-{index}", [170, y, 0])
            for index, y in enumerate((-50, -10, 10, 30, 50))
        ]
    )
    budgets = {}
    consume = ExecutionBudget.consume_operation

    def count(self):
        budgets[id(self)] = self
        consume(self)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    monkeypatch.setattr(dice, "_rng", Random(2797))
    quake._cast(state, data, params)
    quake._run("end_combat_turn", state, data, actor="hero")
    source = quake._source(state)
    assert source["fissure_count_roll"]["total"] == 6
    assert len(source["fissure_save_rows"]) == 7
    event = state[f"{SRD}.state"]["pending_srd_actions"][_fall_id(state)]
    assert len(event["parameters"]["falling_events"]) == 6
    operations = max(b.operations for b in budgets.values())
    print("Earthquake fissure operations:", operations)
    assert operations <= 10000


def test_public_paid_fissures_end_fall_reopen_dismiss_and_retry_once(tmp_path, monkeypatch):
    quake._public_fixture(monkeypatch)
    monkeypatch.setattr(jar.public_casting, "_spell_case", lambda *_a, **_k: _case(begin=False))
    database = tmp_path / "earthquake-fissures.db"
    with AuthorityOwner.open(database, "casting-owner") as authority:
        _, _, params = jar.public_casting._create_spell_campaign(authority, "earthquake")
        initial = authority.snapshot("casting")
        started, start_command = _act(
            authority,
            "start_combat_encounter_entities",
            first_actor="hero",
            second_actor="victim",
            first_surprised=False,
            second_surprised=False,
            tie_winner="first",
        )
        assert started.status is ActionStatus.COMMITTED, started.result
        began, begin_command = _act(authority, "begin_combat_turn")
        assert began.status is ActionStatus.COMMITTED, began.result
        monkeypatch.setattr(dice, "_rng", Random(SEARCH_SEED))
        cast, cast_command = _act(authority, "cast_spell", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        cast_state = authority.snapshot("casting")
        assert quake._source(cast_state.state)["fissures_opened"] is False
        assert quake._target(cast_state.state)["resources"]["spell_slot_8"] == (
            quake._target(initial.state)["resources"]["spell_slot_8"] - 1
        )
        assert authority.execute_action(cast_command) == cast
        assert authority.snapshot("casting") == cast_state
        ended, end_command = _act(authority, "end_combat_turn")
        assert ended.status is ActionStatus.COMMITTED, ended.result
        pending = authority.snapshot("casting")
        assert quake._source(pending.state)["fissure_count_roll"]["total"] == 6
        action_id = _fall_id(pending.state)
        assert authority.execute_action(end_command) == ended
        assert authority.snapshot("casting") == pending
    with AuthorityOwner.open(database, "casting-owner") as authority:
        assert authority.snapshot("casting") == pending
        completed, fall_command = _act(
            authority, "complete_srd_pending_action", pending_action_id=action_id
        )
        assert completed.status is ActionStatus.COMMITTED, completed.result
        landed = authority.snapshot("casting")
        assert _body(landed.state, "victim")["position"] == [150, -50, -100]
        geometry = deepcopy(_scene(landed.state)["geometry"])
        dismissed, dismiss_command = _act(
            authority,
            "dismiss_srd_spell",
            caster_state="hero",
            cast_id=quake._source(landed.state)["cast_id"],
        )
        assert dismissed.status is ActionStatus.COMMITTED, dismissed.result
        final = authority.snapshot("casting")
        assert quake._source(final.state)["active"] is False
        assert _scene(final.state)["geometry"] == geometry
        for result, command in (
            (started, start_command),
            (began, begin_command),
            (cast, cast_command),
            (ended, end_command),
            (completed, fall_command),
            (dismissed, dismiss_command),
        ):
            assert authority.execute_action(command) == result
        assert authority.snapshot("casting") == final
    with AuthorityOwner.open(database, "casting-owner") as authority:
        assert authority.snapshot("casting") == final
