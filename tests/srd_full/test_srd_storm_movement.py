"""Actual Storm intervals alter native movement spending once, never a caller cost verdict."""

from copy import deepcopy
from functools import lru_cache

import pytest
import yaml
from scripts import srd_storm_movement as movement
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.errors import MechanicsRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_storm_conditions as base
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_sunbeam import Rolls

_BASE_RUNTIME = base._runtime


@lru_cache
def _runtime():
    encoded = movement.bind_runtime_consumers(_BASE_RUNTIME()[1].decode())
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
    monkeypatch.setattr(base.base.base.base, "_runtime", _runtime)
    monkeypatch.setattr(base.base.base.base.budget, "_mechanic", _mechanic)
    monkeypatch.setattr(
        base.base.base.base.cards,
        "_spell_definitions",
        lambda: _runtime()[0]["definitions"]["spells"],
    )
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Rolls())


def _cost(state, start, end, plane="material"):
    return _run(
        movement.OWNER,
        state,
        start=start,
        end=end,
        plane=plane,
        base_difficult=False,
    ).result["context"]["_route_cost"]


def test_real_source_partition_and_existing_difficult_zone_do_not_stack(monkeypatch):
    state, _, _ = base._case()
    rolls = Rolls()
    monkeypatch.setattr(dice, "_rng", rolls)
    before = deepcopy(state)
    assert _cost(state, [0, 5, 0], [40, 5, 0]) == pytest.approx(60)
    assert _cost(state, [0, 5, 50], [40, 5, 50]) == pytest.approx(40)
    assert _cost(state, [0, 305, 0], [40, 305, 0]) == pytest.approx(40)
    assert _cost(state, [0, 5, 0], [40, 5, 0], "ethereal") == pytest.approx(40)
    assert state == before and rolls.sides == []
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"].append(
        {
            "id": "broken-ground",
            "map_id": scene["map"]["id"],
            "shape": {"kind": "box", "min": [20, 0, -5], "max": [40, 10, 5]},
            "metadata": {"plane": "material", "difficult": True},
        }
    )
    assert _cost(state, [0, 5, 0], [40, 5, 0]) == pytest.approx(60)
    _run("end_concentration", state, caster_state="hero")
    assert _cost(state, [0, 5, 0], [40, 5, 0]) == pytest.approx(60)
    scene = state[f"{SRD}.state"]["spatial_scene"]
    scene["zones"] = []
    assert _cost(state, [0, 5, 0], [40, 5, 0]) == pytest.approx(40)


def test_repeat_changes_only_native_route_owner_and_no_roll_or_payment_api():
    runtime, encoded = _runtime()
    assert movement.bind_runtime_consumers(encoded.decode()) == encoded.decode()
    parent = _BASE_RUNTIME()[0]
    assert {
        name for name in runtime["tools"] if runtime["tools"][name] != parent["tools"][name]
    } == {movement.OWNER}
    assert (
        runtime["tools"][movement.OWNER]["parameters"]
        == parent["tools"][movement.OWNER]["parameters"]
    )


def test_paid_cloud_native_turn_public_movement_budget_reopen_and_exact_retry(
    tmp_path, monkeypatch
):
    public = base.base.base.base.public_casting
    monkeypatch.setattr(public, "bundled_srd_archive", base.base.base.base._archive)
    monkeypatch.setattr(public, "_spell_case", lambda _spell: base.base.base._case())
    rolls, budgets = Rolls(), []
    monkeypatch.setattr(dice, "_rng", rolls)
    consume = ExecutionBudget.consume_operation

    def count(budget):
        if not budgets or budgets[-1] is not budget:
            budgets.append(budget)
        consume(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    database, receipts = tmp_path / "storm-route.db", []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner,
            base.base.base.storm.SPELL,
            participant_overrides={"speed": 30},
            entity_overrides={"speed": 30},
            target_overrides={"hp": 1000, "max_hp": 1000},
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
        before = owner.snapshot("casting")
        assert before.state[f"{SRD}.combat"]["movement_remaining"] == 30
        with pytest.raises(MechanicsRejected, match="movement remaining"):
            _act(owner, "move_srd_scene_creature", actor_state="hero", destination=[40, 50, 0])
        assert owner.snapshot("casting") == before
        receipts.append(
            _act(owner, "move_srd_scene_creature", actor_state="hero", destination=[24, 0, 0])
        )
        moved = receipts[-1][0].result["context"]
        assert moved["_scene_movement_distance"] == 24
        assert 27 < moved["_scene_movement_cost"] < 28
        saved = owner.snapshot("casting")
        assert saved.state[f"{SRD}.combat"]["movement_remaining"] == pytest.approx(
            30 - moved["_scene_movement_cost"]
        )
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        receipts.append(_act(owner, "end_concentration", caster_state="hero"))
        receipts.append(
            _act(owner, "move_srd_scene_creature", actor_state="hero", destination=[25, 0, 0])
        )
        assert receipts[-1][0].result["context"]["_scene_movement_cost"] == 1
        saved, faces = owner.snapshot("casting"), deepcopy(rolls.sides)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        for result, command in receipts:
            assert result.status is ActionStatus.COMMITTED
            assert owner.execute_action(command) == result
        assert owner.snapshot("casting") == saved and rolls.sides == faces
    assert all(budget.max_operations == 10000 for budget in budgets)
    print("Storm route/public max operations:", max(budget.operations for budget in budgets))
