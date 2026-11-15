"""Actual paid Find the Path sources select world-fact routes and retire stale answers."""

# ruff: noqa: E402

import hashlib
import importlib.util
import sys
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import pytest
import scripts
import yaml

import tests

BASELINE = Path(str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-find-path-routes/input-1"))
ROOT = Path(__file__).resolve().parents[2]
PARENT_TEST = BASELINE / "tests/test_srd_find_the_path.py"
assert hashlib.sha256(PARENT_TEST.read_bytes()).hexdigest() == (
    "e8e91b890b25145d16d4386c3d30338fd8a15c38b1f4337e9f1137579f7b5f2a"
)
scripts.__path__ = [str(BASELINE / "scripts"), *scripts.__path__]
tests.__path__.insert(0, str(BASELINE / "tests"))
spec = importlib.util.spec_from_file_location(
    "scripts.srd_full.srd_find_the_path", ROOT / "scripts/srd_find_the_path.py"
)
owned = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = owned
spec.loader.exec_module(owned)

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_find_the_path as base

DEPENDENCY = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-find-path-routes/srd_spell_travel_telekinesis.py")
)
assert hashlib.sha256(DEPENDENCY.read_bytes()).hexdigest() == (
    "8d3aa4c2671bbce6e24553ca47eb4448a4aab2e2edfb9b6be6cae165922b21fd"
)
dependency_spec = importlib.util.spec_from_file_location("scripts._find_path_tk", DEPENDENCY)
dependency = importlib.util.module_from_spec(dependency_spec)
dependency_spec.loader.exec_module(dependency)
parent_runtime = base._runtime


@lru_cache
def _runtime():
    """Adopt only Root's actual corrected release tool into the existing paid owning fixture."""
    runtime, _ = parent_runtime()
    runtime = deepcopy(runtime)
    name = "prepare_srd_telekinesis_releases"
    corrected = dependency.telekinesis_tools(runtime["tools"])[name]
    assert {k: v for k, v in runtime["tools"][name].items() if k != "effects"} == {
        k: v for k, v in corrected.items() if k != "effects"
    }
    runtime["tools"][name] = corrected
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@pytest.fixture
def path_scope(monkeypatch):
    monkeypatch.setattr(base, "_runtime", _runtime)
    yield from base.path_scope.__wrapped__(monkeypatch)


def _case():
    state, data = base._case()
    state[f"{base.SRD}.state"].pop("spatial_scene")
    return state, data


def _know(state, *, stationary=True):
    return base._run(
        "record_srd_find_path_location",
        state,
        actor_state="hero",
        location_subject="shrine",
        location_name="Old shrine",
        familiar=True,
        stationary=stationary,
        specific=True,
        location_plane="material",
        actor_plane="material",
    )


def _routes(state):
    return {
        "actor_state": "hero",
        "source_id": base._source(state)["instance_id"],
        "destination_subject": "shrine",
        "current_place": "fork",
        "current_plane": "material",
        "distance_feet": 80,
        "direction": "north",
        "routes": [
            {"id": "river", "remaining_distance_feet": 120, "description": "River trail"},
            {"id": "ridge", "remaining_distance_feet": 90, "description": "Ridge trail"},
            {"id": "road", "remaining_distance_feet": 90, "description": "Old road"},
        ],
    }


def test_paid_no_coordinate_source_selects_routes_and_rejects_stale_destination(path_scope):
    state, data = _case()
    _know(state)
    base._complete(state, data, base._paid_parameters())
    source = base._source(state)
    assert source["started_tick"] == 20 and source["expires_tick"] == 28820
    assert source["position"] is None and source["native_anchor"] is False
    assert state[f"{base.SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
    params = _routes(state)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected):
        base._run("record_srd_find_path_routes", state, **(params | {"source_id": "old-source"}))
    assert state == before
    base._run("record_srd_find_path_routes", state, **params)
    assert base._source(state)["route_facts"]["position"] is None
    assert base._source(state)["route_facts"]["body_id"] is None
    read = base._run("query_srd_find_path", state, actor_state="hero").result["context"]
    assert read["_path_distance"] == 80 and read["_path_factual_direction"] == "north"
    assert [r["id"] for r in read["_path_best_routes"]] == ["ridge", "road"]
    moved = base._run(
        "query_srd_find_path", state, actor_state="hero", current_place="bridge"
    ).result["context"]
    assert moved["_path_best_routes"] == [] and moved["_path_distance"] is None
    returned = base._run(
        "query_srd_find_path", state, actor_state="hero", current_place="fork"
    ).result["context"]
    assert returned["_path_best_routes"] == []
    _know(state, stationary=False)
    with pytest.raises(RulesetPreconditionRejected):
        base._run("query_srd_find_path", state, actor_state="hero")


def test_native_route_fact_is_invalidated_by_real_movement_and_emission_repeats(path_scope):
    state, data = base._case()
    base._know(state)
    base._complete(state, data, base._paid_parameters())
    params = _routes(state) | {"destination_subject": "orb"}
    base._run("record_srd_find_path_routes", state, **params)
    context = base._run("query_srd_find_path", state, actor_state="hero").result["context"]
    assert context["_path_distance"] == 40
    assert [r["id"] for r in context["_path_best_routes"]] == ["ridge", "road"]
    base._run(
        "move_srd_scene_creature", state, actor_state="hero", destination=[0, 10, 0], mode="walk"
    )
    assert base._source(state)["route_facts"] is None
    read = base._run("query_srd_find_path", state, actor_state="hero").result["context"]
    assert read["_path_best_routes"] == [] and read["_path_distance"] == pytest.approx(1700**0.5)
    raw = base.RUNTIME.read_text(encoding="utf-8")
    once = owned.extend_runtime(raw)
    assert base._load_runtime(owned.extend_runtime(once)) == base._load_runtime(once)


def test_paid_route_query_reopens_retries_and_exact_day_expiry(path_scope, monkeypatch, tmp_path):
    monkeypatch.setattr(
        base.public, "_spell_case", lambda _spell: (*_case(), base._paid_parameters())
    )
    path = tmp_path / "find-path-routes.db"
    command = ActionCommand(
        campaign_id="casting",
        action_id="knowledge",
        idempotency_key="knowledge",
        owner_id="casting-owner",
        writer=WriterRole.DM,
        expected_revision=0,
        turn_id="casting-turn",
        package_id=base.SRD,
        tool_name="record_srd_find_path_location",
        parameters={},
    )
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = base.public._create_spell_campaign(owner, "find_the_path")
        mechanics = StatelessMcpMechanics(owner)

        def act(identity, name, selected):
            chosen = replace(
                command,
                action_id=identity,
                idempotency_key=identity,
                expected_revision=owner.snapshot("casting").revision,
                tool_name=name,
                parameters=selected,
            )
            result = mechanics.execute_action(chosen)
            assert result.status is ActionStatus.COMMITTED, result.result
            snapshot = owner.snapshot("casting")
            assert (
                mechanics.execute_action(chosen) == result and owner.snapshot("casting") == snapshot
            )
            return result, snapshot

        act(
            "knowledge",
            "record_srd_find_path_location",
            {
                "actor_state": "hero",
                "location_subject": "shrine",
                "location_name": "Old shrine",
                "familiar": True,
                "stationary": True,
                "specific": True,
                "location_plane": "material",
                "actor_plane": "material",
            },
        )
        act("begin", "begin_srd_spell_cast", base._begin(params))
        act(
            "channel",
            "continue_srd_spell_cast",
            {
                "caster_state": "hero",
                "elapsed_rounds": 10,
                "uninterrupted": True,
                "verbal_component_available": True,
                "somatic_component_available": True,
                "material_component_available": True,
            },
        )
        _, paid = act("complete", "cast_spell_outside_combat", params)
        recorded, _ = act("routes", "record_srd_find_path_routes", _routes(paid.state))
        assert recorded.result["result_card"]["summary"][0]["value"] == "fork"
        queried, retained = act("query", "query_srd_find_path", {"actor_state": "hero"})
        assert queried.result["context"]["_path_shortest_distance"] == 90
        assert queried.result["context"]["_path_distance"] == 80
        assert queried.result["result_card"]["summary"][-1]["value"] == ["ridge", "road"]
        assert queried.result["result_card"]["details"] == [
            {"label": "ridge", "value": "Ridge trail", "emphasis": False},
            {"label": "road", "value": "Old road", "emphasis": False},
        ]
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == retained
        chosen = replace(
            command,
            action_id="expiry",
            idempotency_key="expiry",
            expected_revision=retained.revision,
            tool_name="advance_srd_lasting_time",
            parameters={"elapsed_rounds": 14400},
        )
        expired = owner.execute_action(chosen)
        assert expired.status is ActionStatus.COMMITTED, expired.result
        final = owner.snapshot("casting")
        assert owner.execute_action(chosen) == expired and owner.snapshot("casting") == final
        assert not any(
            r.get("spell") == "find_the_path"
            for r in final.state[f"{base.SRD}.state"]["turn_effects"]
        )
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == final
