"""Paid named-place sources retain real distance/plane/clock, with global routing explicit."""

import hashlib
from copy import deepcopy
from dataclasses import replace
from functools import lru_cache
from pathlib import Path

import pytest
import yaml
from scripts.srd_full.srd_find_the_path import PROFILES, PUBLIC_TOOLS, extend_runtime, validate_profiles
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm.agent._stateless_mcp import StatelessMcpMechanics
from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import ActionCommand, AuthorityOwner
from sylvia_gm.domain.authority import ActionStatus, WriterRole
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from tests import test_srd_public_spell_casting as public
from tests import test_srd_spell_travel_telekinesis_fall as scope
from tests.srd_full.test_srd_action_budget import _run
from tests.srd_full.test_srd_extended_casting import _begin
from tests.srd_full.test_srd_spell_travel import SRD, _point
from tests.srd_full.test_srd_spell_travel import _case as spell_case
from tests.srd_full.test_srd_spell_travel_telekinesis import _tk_case

RUNTIME = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-find-the-path/runtime.yaml")
)


@lru_cache
def _runtime():
    encoded = RUNTIME.read_bytes()
    assert hashlib.sha256(encoded).hexdigest() == (
        "e3c4858d2b42efd1b4aba54ae570ca55a53cb87f83b6eab9b8b894fe0ad1a813"
    )
    runtime = _load_runtime(extend_runtime(encoded.decode().replace("\r\n", "\n")))
    runtime["definitions"]["spells"].update(deepcopy(PROFILES))
    for name in (
        "cast_spell",
        "cast_spell_node",
        "cast_spell_outside_combat",
        "begin_srd_spell_cast",
        "begin_srd_spell_cast_internal",
        "begin_srd_spell_action",
    ):
        if name in runtime["tools"]:
            for p in runtime["tools"][name]["parameters"]:
                if p["name"] == "spell" and "enum_values" in p:
                    p["enum_values"] = sorted(set(p["enum_values"]) | PROFILES.keys())
    return runtime, yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False).encode()


@pytest.fixture
def path_scope(monkeypatch):
    monkeypatch.setattr(scope, "_runtime", _runtime)
    monkeypatch.setattr(scope, "EXTRA_PUBLIC_TOOLS", frozenset(PUBLIC_TOOLS))
    execute = package_runtime.execute_tool

    def evidence(*args, **kwargs):
        result = execute(*args, **kwargs)
        if not result.success:
            print("Selected public named-place failure:", result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", evidence)
    yield from scope.actual_fall_scope.__wrapped__(monkeypatch)


def _case():
    state, data = _tk_case("find_the_path")
    anchor = next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "orb")
    anchor["position"] = [40, 0, 0]
    return state, data


def _know(state, **facts):
    return _run(
        "record_srd_find_path_location",
        state,
        actor_state="hero",
        location_subject="orb",
        location_name="Old shrine",
        familiar=True,
        stationary=True,
        specific=True,
        **facts,
    )


def _source(state):
    return next(
        r for r in state[f"{SRD}.state"]["turn_effects"] if r.get("spell") == "find_the_path"
    )


def _paid_parameters():
    _, _, params = spell_case("find_the_path")
    params.pop("targets", None)
    return params | {"path_location": "Old shrine"}


def _complete(state, data, params):
    _run("begin_srd_spell_cast", state, participant_data=data, **_begin(params))
    _run(
        "continue_srd_spell_cast",
        state,
        caster_state="hero",
        elapsed_rounds=10,
        uninterrupted=True,
        verbal_component_available=True,
        somatic_component_available=True,
        material_component_available=True,
    )
    return _run("cast_spell_outside_combat", state, participant_data=data, **params)


def test_actual_named_source_tracks_distance_after_movement_and_plane(path_scope):
    assert validate_profiles()
    state, data = _case()
    _know(state)
    _complete(state, data, _paid_parameters())
    read = _run("query_srd_find_path", state, actor_state="hero").result["context"]
    assert read["_path_distance"] == 40 and read["_path_direction"] == [1, 0, 0]
    assert read["_path_local_route"][-1] == [40, 0, 0]
    assert "unsupported" in read["_path_route_status"]
    _run("move_srd_scene_creature", state, actor_state="hero", destination=[0, 10, 0], mode="walk")
    after = _run("query_srd_find_path", state, actor_state="hero").result["context"]
    assert after["_path_distance"] == pytest.approx(1700**0.5)
    assert after["_path_direction"][1] < 0
    _run("move_srd_travel_subject", state, subject="hero", destination=[0, 10, 0], plane="ethereal")
    separated = _run("query_srd_find_path", state, actor_state="hero").result["context"]
    assert separated["_path_distance"] is None and separated["_path_direction"] == []
    assert separated["_path_local_route"] == []
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] == "find_the_path"


@pytest.mark.parametrize("invalid", ["moving", "unspecific", "other_plane"])
def test_actual_invalid_destination_keeps_paid_completion_costs(path_scope, invalid):
    state, data = _case()
    anchor = next(e for e in state[f"{SRD}.state"]["spatial_scene"]["entities"] if e["id"] == "orb")
    if invalid == "other_plane":
        anchor["metadata"]["plane"] = "ethereal"
    _run(
        "record_srd_find_path_location",
        state,
        actor_state="hero",
        location_subject="orb",
        location_name="Old shrine",
        familiar=True,
        stationary=invalid != "moving",
        specific=invalid != "unspecific",
    )
    paid = _complete(state, data, _paid_parameters())
    assert state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
    assert state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
    assert not any(
        r.get("spell") == "find_the_path" for r in state[f"{SRD}.state"].get("turn_effects", [])
    )
    assert "Spell fails" in paid.result["result_card"]["details"][-1]["value"]


def _paid_case(_spell):
    state, data = _case()
    return state, data, _paid_parameters()


def test_paid_minute_source_query_exact_day_expiry_reopen_and_retry(
    path_scope, monkeypatch, tmp_path
):
    monkeypatch.setattr(public, "_spell_case", _paid_case)
    path = tmp_path / "find-path.db"
    with AuthorityOwner.open(path, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(owner, "find_the_path")
        command = ActionCommand(
            campaign_id="casting",
            action_id="knowledge",
            idempotency_key="knowledge",
            owner_id="casting-owner",
            writer=WriterRole.DM,
            expected_revision=0,
            turn_id="casting-turn",
            package_id=SRD,
            tool_name="record_srd_find_path_location",
            parameters={
                "actor_state": "hero",
                "location_subject": "orb",
                "location_name": "Old shrine",
                "familiar": True,
                "stationary": True,
                "specific": True,
            },
        )
        mechanics = StatelessMcpMechanics(owner)
        known = mechanics.execute_action(command)
        assert known.status is ActionStatus.COMMITTED, known.result
        saved = owner.snapshot("casting")
        assert mechanics.execute_action(command) == known and owner.snapshot("casting") == saved
        begin_command = replace(
            command,
            action_id="begin",
            idempotency_key="begin",
            expected_revision=saved.revision,
            tool_name="begin_srd_spell_cast",
            parameters=_begin(params),
        )
        begun = mechanics.execute_action(begin_command)
        assert begun.status is ActionStatus.COMMITTED, begun.result
        channel = owner.snapshot("casting")
        assert channel.state[f"{SRD}.entities"]["hero"]["extended_casting"] is not None
        assert mechanics.execute_action(begin_command) == begun
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == channel

        def act(identity, name, selected):
            chosen = replace(
                command,
                action_id=identity,
                idempotency_key=identity,
                expected_revision=owner.snapshot("casting").revision,
                tool_name=name,
                parameters=selected,
            )
            result = owner.execute_action(chosen)
            assert result.status is ActionStatus.COMMITTED, result.result
            snapshot = owner.snapshot("casting")
            assert owner.execute_action(chosen) == result and owner.snapshot("casting") == snapshot
            return result, snapshot

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
        _, created = act("complete", "cast_spell_outside_combat", params)
        assert _source(created.state)["started_tick"] == 20
        assert _source(created.state)["expires_tick"] == 28820
        assert created.state[f"{SRD}.entities"]["hero"]["resources"]["spell_slot_6"] == 2
        queried, _ = act("sense", "query_srd_find_path", {"actor_state": "hero"})
        assert queried.result["context"]["_path_distance"] == 40
        _, moved = act(
            "move",
            "move_srd_scene_creature",
            {"actor_state": "hero", "destination": [0, 10, 0], "mode": "walk"},
        )
        assert _point(moved.state, "hero") == [0, 10, 0]
        act("almost-day", "advance_srd_lasting_time", {"elapsed_rounds": 14399})
        before_end, _ = act("sense-later", "query_srd_find_path", {"actor_state": "hero"})
        assert before_end.result["context"]["_path_distance"] == pytest.approx(1700**0.5)
        _, expired = act("last-round", "advance_srd_lasting_time", {"elapsed_rounds": 1})
        assert expired.state[f"{SRD}.entities"]["hero"]["concentration_spell_id"] is None
        assert not any(
            r.get("spell") == "find_the_path" for r in expired.state[f"{SRD}.state"]["turn_effects"]
        )
    with AuthorityOwner.open(path, "casting-owner") as owner:
        assert owner.snapshot("casting") == expired
    with pytest.raises(RulesetPreconditionRejected):
        _run("query_srd_find_path", deepcopy(expired.state), actor_state="hero")
