"""Actual paid explosions ignite admitted mundane sources without another damage event."""

from copy import deepcopy
from functools import lru_cache

import pytest
import yaml
from scripts import srd_delayed_blast_ignition as ignition
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm import dice
from sylvia_gm.application import package_runtime
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_delayed_blast_fireball as base
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_wave3_information import _wall
from tests.srd_full.test_srd_sunbeam import Rolls

_BASE_RUNTIME = base._runtime


@lru_cache
def _runtime():
    parent, encoded = _BASE_RUNTIME()
    encoded = ignition.bind_runtime_consumers(encoded.decode())
    runtime = yaml.load(encoded, Loader=yaml.CSafeLoader)
    for name in (
        "d20_test",
        "resolve_spell_area_save_damage_target",
        "qualify_srd_delayed_blast_target_cover",
    ):
        assert runtime["tools"][name] == parent["tools"][name]
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
    monkeypatch.setattr(base, "_runtime", _runtime)
    monkeypatch.setattr(base.budget, "_mechanic", _mechanic)
    monkeypatch.setattr(base.cards, "_spell_definitions", lambda: runtime["definitions"]["spells"])
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Rolls())
    actual = package_runtime.execute_tool

    def diagnose(*args, **kwargs):
        result = actual(*args, **kwargs)
        if not result.success:
            print(args[0], result.error)
        return result

    monkeypatch.setattr(package_runtime, "execute_tool", diagnose)


def _object(state, object_id):
    return next(
        row for row in state[f"{SRD}.state"]["spatial_scene"]["entities"] if row["id"] == object_id
    )


def _flame(state, object_id, position, *, kind="torch", **metadata):
    scene = state[f"{SRD}.state"]["spatial_scene"]
    row = deepcopy(scene["entities"][0])
    row.update(
        id=object_id,
        position=position,
        height=4,
        footprint={"kind": "box", "half_extents": [0.25, 0.25]},
        movement="passable",
        metadata={"plane": "material", "lighting": "bright", "fixture_note": "preserved"},
    )
    scene["entities"].append(row)
    _run(
        "record_srd_minor_world_subject",
        state,
        object_id=object_id,
        kind=kind,
        nonliving=True,
        lit=False,
        exposed=True,
    )
    _object(state, object_id)["metadata"].update(metadata)


def _case():
    state, data, params = base._case()
    _flame(state, "torch", [38, 5, 0])
    return state, data, params


def test_actual_explosion_ignites_and_preserves_the_established_ordinary_flame_fact(monkeypatch):
    state, data, params = _case()
    original = deepcopy(_object(state, "torch"))
    base._cast(state, data, params)
    assert _object(state, "torch")["metadata"]["srd_ordinary_flame"]["burning"] is False
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    result = _run("detonate_srd_delayed_blast_fireball", state, caster_state="hero").result
    row = _object(state, "torch")
    assert row["metadata"]["srd_ordinary_flame"] == {
        "kind": "ordinary_flame",
        "burning": True,
        "exposed": True,
    }
    assert row["position"] == original["position"] and row["footprint"] == original["footprint"]
    assert row["metadata"]["fixture_note"] == "preserved"
    assert rolls.sides == [6] * 12 + [20, 20]
    assert state[f"{SRD}.entities"]["guard"]["hp"] == 964
    assert state[f"{SRD}.entities"]["other"]["hp"] == 982
    assert result["result_card"]


def test_closed_sphere_body_boundary_and_worn_carried_crossplane_exclusions():
    state, data, params = _case()
    _flame(state, "edge", [50.25, 5, 0], kind="lamp")
    _flame(state, "outside", [50.2501, 5, 0], kind="candle")
    _flame(state, "carried", [35, 5, 0], carried_by="hero")
    _flame(state, "worn", [35, 5, 0], worn_by="guard")
    _flame(state, "other-plane", [35, 5, 0], plane="ethereal")
    base._cast(state, data, params)
    _run("detonate_srd_delayed_blast_fireball", state, caster_state="hero")
    for name in ("torch", "edge"):
        assert _object(state, name)["metadata"]["srd_ordinary_flame"]["burning"] is True
    for name in ("outside", "carried", "worn", "other-plane"):
        assert _object(state, name)["metadata"]["srd_ordinary_flame"]["burning"] is False


def test_physical_glass_obstruction_preserves_required_admitted_cover_and_atomicity():
    state, data, params = _case()
    scene = state[f"{SRD}.state"]["spatial_scene"]
    next(row for row in scene["entities"] if row["id"] == "other")["position"] = [80, -20, 0]
    wall = _wall(0.5, material="glass")
    wall.update(map_id=scene["map"]["id"], visibility="clear")
    wall["volume"] = {"kind": "box", "min": [35, 2, -10], "max": [35.5, 8, 10]}
    scene["geometry"].append(wall)
    base._cast(state, data, params)
    before = deepcopy(state)
    with pytest.raises(RulesetPreconditionRejected, match="categorical Cover"):
        _run("detonate_srd_delayed_blast_fireball", state, caster_state="hero")
    assert state == before
    # This is the unchanged owning categorical Cover fact, backed by the real solid glass wall.
    _object(state, "torch")["metadata"]["cover_from"] = {"hero": "total"}
    _run("detonate_srd_delayed_blast_fireball", state, caster_state="hero")
    assert _object(state, "torch")["metadata"]["srd_ordinary_flame"]["burning"] is False


def test_ignition_emission_repeats_and_preserves_original_roll_packet_and_cover():
    runtime, encoded = _runtime()
    assert (
        yaml.load(ignition.bind_runtime_consumers(encoded.decode()), Loader=yaml.CSafeLoader)
        == runtime
    )
    assert ignition.IGNITE not in str(runtime["tools"][base.QUEUE]["effects"])
    assert not {
        "damage_total",
        "visible",
        "cover",
        "flammable",
    } & {p["name"] for p in runtime["tools"]["detonate_srd_delayed_blast_fireball"]["parameters"]}


def test_paid_archived_ignition_reopens_and_exact_retry_has_no_second_fire_or_dice(
    tmp_path,
    monkeypatch,
):
    monkeypatch.setattr(base.public_casting, "bundled_srd_archive", base._archive)
    monkeypatch.setattr(base.public_casting, "_spell_case", lambda _spell: _case())
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    measured = []
    actual = ExecutionBudget.consume_operation

    def count(budget):
        if not measured or measured[-1] is not budget:
            measured.append(budget)
        actual(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    database, receipts = tmp_path / "dbf-ignition.db", []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = base.public_casting._create_spell_campaign(
            owner,
            base.SPELL,
            target_overrides={"hp": 1000, "max_hp": 1000},
        )
        receipts.append(_act(owner, "cast_spell_outside_combat", **params))
        saved = owner.snapshot("casting")
        assert _object(saved.state, "torch")["metadata"]["srd_ordinary_flame"]["burning"] is False
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        receipts.append(_act(owner, "detonate_srd_delayed_blast_fireball", caster_state="hero"))
        saved, faces = owner.snapshot("casting"), deepcopy(rolls.sides)
        assert _object(saved.state, "torch")["metadata"]["srd_ordinary_flame"]["burning"] is True
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        for result, command in receipts:
            assert result.status is ActionStatus.COMMITTED
            assert owner.execute_action(command) == result
        assert owner.snapshot("casting") == saved and rolls.sides == faces
    assert all(b.max_operations == 10000 for b in measured)
    print("DBF ordinary-flame/public max operations:", max(b.operations for b in measured))
