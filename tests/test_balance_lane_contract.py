"""Consumer regressions against the current source-built TD package."""

from tests.td_source_fixture import candidate as candidate

from balance_suite.model import ActorState, Position, Side, Stance
from copy import deepcopy
from dataclasses import replace
import json
import hashlib
import os
from pathlib import Path

import pytest

from balance_suite import checkpoint, overwatch
from balance_suite.lane_parity import (
    paired_fixture,
    setup_pair,
    lab_action,
    decision,
    projection,
    NS,
)
from balance_suite.policies import OverwatchSelection, PolicyDecision

FIXTURES = Path(__file__).parent / "fixtures/overwatch-lab-parity-v1.json"


@pytest.fixture(autouse=True)
def bind_current_candidate(candidate, monkeypatch):
    monkeypatch.setenv("SYLVIA_TD_CANDIDATE", str(candidate))


def fixture(identity):
    return next(f for f in json.loads(FIXTURES.read_text())["fixtures"] if f["id"] == identity)


def test_move_and_stance_return_leave_a_mutation_record():
    actor = ActorState(
        "watcher",
        Side.A,
        "RIFLEMAN",
        10,
        "BOUND-POSE-V1",
        6,
        2,
        11,
        11,
        10,
        10,
        6,
        6,
        Position(2, 2),
    )
    initial = getattr(actor, "pose_revision", 0)
    actor.position = Position(3, 2)
    actor.position = Position(2, 2)
    actor.stance = Stance.PRONE
    actor.stance = Stance.STANDING
    assert getattr(actor, "pose_revision", 0) == initial + 4


def armed(identity="ROW-EAST-V1"):
    f = fixture(identity)
    oracle, engine, grid, metrics = paired_fixture(os.environ["SYLVIA_TD_CANDIDATE"], f)
    for index, (actor, values) in enumerate(
        f.get("watches", {"vesper": f.get("selection")}).items()
    ):
        setup_pair(
            oracle,
            engine,
            grid,
            metrics,
            actor,
            OverwatchSelection(**values, fixture_id=f["id"]),
            "setup-" + str(index),
        )
    engine.state.active_side = Side.B
    oracle.state[NS + ".state"]["active_side"] = "hostiles"
    return oracle, engine, grid, metrics


def test_pending_order_checkpoint_resolution_and_exact_retries(tmp_path):
    oracle, engine, grid, metrics = armed("SIMULTANEOUS-EXPLICIT-ORDER-V1")
    command = decision("MOVE_OBJECTIVE", destination=[5, 2], budget=4)
    before, count, original_metrics = (
        engine.state.semantic_hash(),
        engine.event_count,
        deepcopy(metrics),
    )
    pending = oracle.execute(
        "move_actor", {"actor": "hostile_alpha", "destination": [5, 2]}, roll=1
    )
    assert pending["pending"]["choice_id"] == "overwatch-window-order"
    entry = lab_action(engine, grid, metrics, "hostile_alpha", command, "pending-move", 1)
    assert entry.rejected_reason == "PENDING_REACTION_ORDER"
    assert engine.state.semantic_hash() == before and engine.event_count == count
    assert metrics == original_metrics and engine.pending["owners"] == ["hex", "vesper"]
    path = tmp_path / "pending.json"
    checkpoint.save(path, engine, metrics)
    engine, metrics = checkpoint.reopen(path)
    grid.covers = engine.state.covers
    assert engine.state.semantic_hash() == before and engine.pending["owners"] == ["hex", "vesper"]
    oracle.execute(
        "move_actor",
        {"actor": "hostile_alpha", "destination": [5, 2]},
        roll=1,
        order=("hex", "vesper"),
    )
    entry = lab_action(
        engine,
        grid,
        metrics,
        "hostile_alpha",
        replace(command, reaction_order=("hex", "vesper")),
        "pending-move",
        1,
    )
    assert entry.rejected_reason is None and engine.pending is None
    expected, actual = projection(oracle, engine)
    assert expected == actual
    after, count, final_metrics = (
        engine.state.semantic_hash(),
        engine.event_count,
        deepcopy(metrics),
    )
    assert lab_action(engine, grid, metrics, "hostile_alpha", command, "pending-move", 20) == entry
    assert (
        engine.state.semantic_hash() == after
        and engine.event_count == count
        and metrics == final_metrics
    )
    with pytest.raises(ValueError, match="changed inputs"):
        lab_action(
            engine,
            grid,
            metrics,
            "hostile_alpha",
            replace(command, destination=Position(4, 2)),
            "pending-move",
        )
    with pytest.raises(ValueError, match="changed inputs"):
        lab_action(engine, grid, metrics, "vesper", command, "pending-move")
    checkpoint.save(tmp_path / "finished.json", engine, metrics)
    engine, metrics = checkpoint.reopen(tmp_path / "finished.json")
    assert engine.state.semantic_hash() == after
    assert lab_action(engine, grid, metrics, "hostile_alpha", command, "pending-move", 20) == entry


@pytest.mark.parametrize("y,found", [(3, True), (3.0001, False)])
def test_closed_boundary_contact_against_actual_td_probe(y, found):
    oracle, engine, grid, metrics = armed()
    path = [[9.5, y, 0.9], [4.5, y, 0.9]]
    result = oracle.execute(
        "td_watch_probe",
        {
            "actor": "hostile_alpha",
            "watcher": "vesper",
            "kind": "LANE",
            "event": "MOVEMENT",
            "path": path,
        },
    )
    assert result["context"]["_found"] is found
    watch = next(iter(engine.state.prepared.values()))
    host = overwatch.SpatialEngine(overwatch.scene(engine.state, grid))
    observed = host.query(
        {
            "kind": "exposure_path",
            "map_id": grid.map_id,
            "observer": {"point": overwatch.muzzle(engine.state.actors["vesper"])},
            "target": {"entity_id": "hostile_alpha"},
            "samples": {"horizontal": 3, "vertical": 3},
            "include": ["geometry", "entities"],
            "path": path,
            "zone_id": watch.lane_zone["id"],
            "max_tests": 4096,
        }
    ).as_payload()
    assert observed["found"] is found
    # The host intentionally omits raw spatial variables from public context.
    # Verify its retained ordered-result digest against the exact synthetic scene.
    from sylvia_gm.domain.package_library import canonical_json_bytes

    evidence = next(e for e in result["spatial_evidence"] if e["operation"] == "exposure_path")
    authority = (
        overwatch.SpatialEngine(oracle.state[NS + ".state"]["spatial_scene"])
        .query(evidence["query"])
        .as_payload()
    )
    assert (
        hashlib.sha256(canonical_json_bytes(authority)).hexdigest()
        == evidence["ordered_result_sha256"]
    )
    assert {k: observed[k] for k in ("found", "point", "distance", "exposure")} == {
        k: authority[k] for k in ("found", "point", "distance", "exposure")
    }


@pytest.mark.parametrize(
    "changes",
    [{"width": 2}, {"width": 0}, {"extent": 0}, {"direction": "northeast"}, {"pattern": "circle"}],
)
def test_policy_rejects_unsupported_geometry(changes):
    values = dict(
        pattern="lane", direction="east", extent=6, width=1, fixture_id="INVALID-SELECTION-V1"
    )
    values.update(changes)
    with pytest.raises(ValueError):
        OverwatchSelection(**values)


def test_setup_without_selection_cannot_reuse_arc_parameter():
    oracle, engine, grid, metrics = paired_fixture(
        os.environ["SYLVIA_TD_CANDIDATE"], fixture("ROW-EAST-V1")
    )
    before = engine.state.semantic_hash()
    entry = lab_action(engine, grid, metrics, "vesper", decision("OVERWATCH"), "missing-selection")
    assert entry.rejected_reason and "explicit fixture" in entry.rejected_reason
    assert engine.state.semantic_hash() == before and not engine.state.prepared


def test_seeded_checkpoint_replay_matches_all_draws_and_hashes(tmp_path):
    from balance_suite.tactical import _execute_decision

    oracle, engine, grid, metrics = armed()
    path = tmp_path / "before-move.json"
    checkpoint.save(path, engine, metrics)
    command = decision("MOVE_OBJECTIVE", destination=[5, 2], budget=4)
    results = []
    for _ in range(2):
        replay, measured = checkpoint.reopen(path)
        grid.covers = replay.state.covers
        entry = _execute_decision(
            replay,
            grid,
            {"hostile_alpha": "POLICY_DIRECT"},
            "hostile_alpha",
            command,
            measured,
            operation_id="seeded-move",
        )
        assert entry.rejected_reason is None
        results.append(
            (
                entry.to_dict(),
                replay.log_hash(),
                replay.state.semantic_hash(),
                replay.rng.provenance(),
                measured,
            )
        )
    assert results[0] == results[1]
    assert any(row["draw_count"] == 1 for row in results[0][3]["derived_streams"])


@pytest.mark.parametrize(
    "field,value",
    [("location", "STOWED"), ("controlled_by", "hostile_alpha"), ("chamber", "EMPTY")],
)
def test_reaction_rechecks_current_equipment(field, value):
    oracle, engine, grid, metrics = armed()
    oracle.state[NS + ".entities"]["vesper"]["weapon"][field] = value
    setattr(engine.state.actors["vesper"].weapon, field, value)
    oracle.execute("move_actor", {"actor": "hostile_alpha", "destination": [5, 2]})
    entry = lab_action(
        engine,
        grid,
        metrics,
        "hostile_alpha",
        decision("MOVE_OBJECTIVE", destination=[5, 2], budget=4),
        "equipment-move",
    )
    assert entry.rejected_reason is None
    assert projection(oracle, engine)[0] == projection(oracle, engine)[1]
    assert metrics["by_side"]["A"]["overwatch_triggers"] == 0


def test_authored_legacy_zone_stays_fixed_and_paid_lane_replaces_own_allowance():
    oracle, engine, grid, metrics = paired_fixture(
        os.environ["SYLVIA_TD_CANDIDATE"], fixture("ROW-EAST-V1")
    )
    zone = {
        "id": "legacy-fixture",
        "map_id": grid.map_id,
        "shape": {"kind": "box", "min": [5, 0, 0], "max": [8, 4, 2.5]},
        "metadata": {},
    }
    oracle.state[NS + ".state"]["spatial_scene"]["zones"].append(deepcopy(zone))
    grid.host_scene["zones"].append(deepcopy(zone))
    oracle.execute("prepare_overwatch", {"actor": "vesper", "zone_id": zone["id"]})
    command = PolicyDecision(
        "OVERWATCH_FIXED", None, None, 0, None, "LEGACY-FIXED-V1", fixed_zone_id=zone["id"]
    )
    entry = lab_action(engine, grid, metrics, "vesper", command, "fixed-setup")
    assert entry.rejected_reason is None
    old = next(iter(engine.state.prepared.values()))
    assert old.fixed_zone == zone and old.lane_zone is None
    setup_pair(
        oracle,
        engine,
        grid,
        metrics,
        "vesper",
        OverwatchSelection("lane", "east", 6, 1, "LEGACY-REPLACE-V1"),
        "new-lane",
    )
    assert not old.active and old.shots_remaining == 0
    assert not oracle.state[NS + ".state"]["prepared"]["overwatch"]["active"]
    assert len([p for p in engine.state.prepared.values() if p.active]) == 1
    assert engine.state.actors["vesper"].ap == oracle.state[NS + ".entities"]["vesper"]["ap"] == 2
    assert (
        next(z for z in overwatch.scene(engine.state, grid)["zones"] if z["id"] == zone["id"])
        == zone
    )


def test_ordinary_lane_does_not_infer_the_distinct_sentinel_combined_aim_action():
    f = deepcopy(fixture("ROW-EAST-V1"))
    f["actor_values"] = {"vesper": {"specialties": ["specialty.sentinel.entry.prepared_sightline"]}}
    oracle, engine, grid, metrics = paired_fixture(os.environ["SYLVIA_TD_CANDIDATE"], f)
    setup_pair(
        oracle,
        engine,
        grid,
        metrics,
        "vesper",
        OverwatchSelection("lane", "east", 6, 1, "ORDINARY-SENTINEL-V1"),
        "ordinary-sentinel",
    )
    assert engine.state.objective["prepared_aim_ap"] == {}
    assert engine.state.actors["vesper"].ap == oracle.state[NS + ".entities"]["vesper"]["ap"] == 4


def test_unsupported_curved_exposure_refuses_without_partial_spending():
    from sylvia_gm.domain.errors import RulesetPreconditionRejected

    oracle, engine, grid, metrics = armed()
    for scene in (oracle.state[NS + ".state"]["spatial_scene"], grid.host_scene):
        next(e for e in scene["entities"] if e["id"] == "hostile_alpha")["footprint"] = {
            "kind": "circle",
            "radius": 0.4,
        }
    before = deepcopy(oracle.state)
    with pytest.raises(RulesetPreconditionRejected, match="unsupported"):
        oracle.execute("move_actor", {"actor": "hostile_alpha", "destination": [5, 2]})
    assert oracle.state == before
    before, before_metrics = engine.state.semantic_hash(), deepcopy(metrics)
    with pytest.raises(ValueError, match="cannot resolve this exposure path"):
        lab_action(
            engine,
            grid,
            metrics,
            "hostile_alpha",
            decision("MOVE_OBJECTIVE", destination=[5, 2], budget=4),
            "curved-move",
        )
    assert engine.state.semantic_hash() == before and metrics == before_metrics


def test_changed_current_mask_cannot_retarget_a_prepared_snapshot():
    oracle, engine, grid, metrics = armed()
    watch = next(iter(engine.state.prepared.values()))
    current = engine.state.spatial_zones[watch.lane_zone["id"]]
    current["shape"]["cells"].pop()
    assert current["shape"] != watch.lane_zone["shape"]
    assert not overwatch.valid(engine.state, watch)
    assert not watch.active and watch.shots_remaining == 0


def test_current_zone_membership_is_rechecked_before_a_response():
    oracle, engine, grid, metrics = armed()
    watch = next(iter(engine.state.prepared.values()))
    target = engine.state.actors["hostile_alpha"]
    target.position = Position(6, 2)
    assert overwatch.contains(engine.state, grid, watch, target.actor_id)
    target.position = Position(9, 4)
    assert not overwatch.contains(engine.state, grid, watch, target.actor_id)


def test_prepared_fire_scenario_cannot_silently_reuse_an_angle_sector():
    from balance_suite.tactical import TeamScenarioSpec

    with pytest.raises(ValueError, match="explicit fixture-bound"):
        TeamScenarioSpec(
            policies_a=("POLICY_PREPARED_FIRE", "POLICY_DIRECT", "POLICY_DIRECT", "POLICY_DIRECT")
        )


def test_unaffordable_declared_move_refuses_without_partial_motion_or_spend():
    oracle, engine, grid, metrics = armed()
    oracle.state[NS + ".entities"]["hostile_alpha"]["ap"] = 1
    engine.state.actors["hostile_alpha"].ap = 1
    before = deepcopy(oracle.state)
    oracle.execute("move_actor", {"actor": "hostile_alpha", "destination": [5, 2]})
    assert oracle.state[NS + ".state"]["last_move"]["status"] == "ILLEGAL_OR_UNREACHABLE"
    assert oracle.state[NS + ".entities"] == before[NS + ".entities"]
    assert oracle.state[NS + ".state"]["spatial_scene"] == before[NS + ".state"]["spatial_scene"]
    assert oracle.state[NS + ".state"]["prepared"] == before[NS + ".state"]["prepared"]
    before = engine.state.semantic_hash()
    entry = lab_action(
        engine,
        grid,
        metrics,
        "hostile_alpha",
        decision("MOVE_OBJECTIVE", destination=[5, 2], budget=4),
        "unaffordable-move",
    )
    assert entry.rejected_reason == "insufficient AP for declared movement"
    assert engine.state.semantic_hash() == before
    assert projection(oracle, engine)[0] == projection(oracle, engine)[1]


@pytest.mark.parametrize("cycle_ap", [None, 0])
def test_cycle_without_a_positive_authored_price_preserves_resources(cycle_ap):
    _, engine, grid, metrics = paired_fixture(
        os.environ["SYLVIA_TD_CANDIDATE"], fixture("ROW-EAST-V1")
    )
    weapon = engine.state.actors["vesper"].weapon
    weapon.chamber, weapon.needs_cycle = "SPENT", True
    weapon.cycle_ap = cycle_ap
    before = engine.state.semantic_hash()
    entry = lab_action(engine, grid, metrics, "vesper", decision("CYCLE"), "unpriced-cycle")
    assert entry.rejected_reason
    assert engine.state.semantic_hash() == before and entry.random_draws == []


def test_cycle_uses_the_same_authored_price_as_current_runtime():
    oracle, engine, grid, metrics = paired_fixture(
        os.environ["SYLVIA_TD_CANDIDATE"], fixture("ROW-EAST-V1")
    )
    actual = oracle.state[NS + ".entities"]["vesper"]["weapon"]
    actual.update(chamber="SPENT", needs_cycle=True, cycle_ap=2)
    weapon = engine.state.actors["vesper"].weapon
    weapon.chamber, weapon.needs_cycle = "SPENT", True
    weapon.cycle_ap = 2
    oracle.execute("cycle_weapon", {"actor": "vesper"})
    entry = lab_action(engine, grid, metrics, "vesper", decision("CYCLE"), "priced-cycle")
    assert not entry.rejected_reason
    assert projection(oracle, engine)[0] == projection(oracle, engine)[1]
