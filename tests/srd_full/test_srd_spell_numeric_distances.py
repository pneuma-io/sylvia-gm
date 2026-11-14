"""An actual non-axial Hellish reaction retains distance through paid saved casting."""

import importlib.util
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from math import sqrt
from pathlib import Path

import pytest
import yaml
from scripts import srd_spell_numeric_distances as distances
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_storm_hellish_outer as outer
from tests.srd_full.test_srd_action_budget import SRD
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_sunbeam import Rolls

_BASE_RUNTIME = outer._runtime
ANCESTRY = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-spell-numeric-distances/srd_reaction_spells.py")
)
REACTION_SHA = "c1437650d07163925268e69b9961c69b3408814692e584d284d42c7ab70965ef"
FEATHER_ANCESTRY = (
    ANCESTRY.parents[1].parent / "spell-distance-feather-ancestry-v1/scripts/srd_feather_fall.py"
)
FEATHER_SHA = "328d2d18a58ade087dbe3c54bb08852bbf4895b1b6d8a66a399a15dc8c294edf"


def _feather_closure(runtime):
    """Supply only genuine missing Feather leaves referenced by the current Reaction factory."""
    assert sha256(FEATHER_ANCESTRY.read_bytes()).hexdigest() == FEATHER_SHA
    spec = importlib.util.spec_from_file_location(
        "scripts._numeric_distance_feather", FEATHER_ANCESTRY
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    candidates = {
        **module._qualify_tools(),
        **module._validate_tools(),
        **module._source_tools(),
        **module._settle_tools(),
        **module._continuation_tools(),
        **module._shared_descent_tools(),
    }
    pending = ["install_srd_feather_fall"]
    while pending:
        name = pending.pop()
        if name in runtime["tools"]:
            continue
        assert name in candidates, name
        leaf = deepcopy(candidates[name])
        runtime["tools"][name] = leaf
        pending.extend(
            effect["call"]
            for sequence in outer._source()._lists(leaf["effects"])
            for effect in sequence
            if "call" in effect
        )


@lru_cache
def _reaction_owner():
    assert sha256(ANCESTRY.read_bytes()).hexdigest() == REACTION_SHA
    spec = importlib.util.spec_from_file_location("scripts._numeric_distance_rx_parent", ANCESTRY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module._reaction_tools()["cast_spell_reaction"]


@lru_cache
def _parent():
    runtime = deepcopy(_BASE_RUNTIME()[0])
    runtime["tools"]["cast_spell_reaction"] = deepcopy(_reaction_owner())
    _feather_closure(runtime)
    encoded = outer._source().bind_runtime_consumers(
        yaml.dump(runtime, Dumper=yaml.CSafeDumper, sort_keys=False)
    )
    runtime = yaml.load(encoded, Loader=yaml.CSafeLoader)
    actual = runtime["tools"]["cast_spell_reaction"]
    expected = deepcopy(_reaction_owner())
    for sequence in outer._source()._lists(expected["effects"]):
        for effect in sequence:
            if effect.get("condition", "").startswith("spell != 'feather_fall'"):
                effect["condition"] += " and spell != 'hellish_rebuke'"
    assert actual["effects"][:-1] == expected["effects"]
    assert outer._source().RESOLVE in str(actual["effects"])
    assert {p["name"] for p in actual["parameters"]} >= {"actual_spell_level", "spell_cast_id"}
    return runtime, encoded.encode()


@lru_cache
def _runtime():
    parent, encoded = _parent()
    text = distances.bind_runtime_consumers(encoded.decode())
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    for name, tool in parent["tools"].items():
        expected = deepcopy(tool)
        if name in distances.OWNERS:
            next(p for p in expected["parameters"] if p["name"] == "target_distance")["type"] = (
                "number"
            )
        assert runtime["tools"][name] == expected
    return runtime, text.encode()


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
    outer._owners.__wrapped__(monkeypatch)
    monkeypatch.setattr(outer, "_runtime", _runtime)
    dbf = outer.storm.base.base.base.base
    monkeypatch.setattr(dbf.budget, "_mechanic", _mechanic)
    outer._archive.cache_clear()


def _public_case(_spell):
    state, data, parameters = outer.storm._unpaid_case()
    for row in state[f"{SRD}.state"]["spatial_scene"]["entities"]:
        if row["id"] == "other":
            row["id"] = "rain_other"
            row["metadata"]["participant_ref"] = "rain_other"
    return state, data, parameters


def test_common_distance_changes_only_the_type_and_preserves_public_bounds_and_repeat():
    parent, _ = _parent()
    runtime, encoded = _runtime()
    assert distances.bind_runtime_consumers(encoded.decode()) == encoded.decode()
    for name in distances.OWNERS:
        before = _tool_schema(parent["tools"][name], tool_name=name)
        after = _tool_schema(runtime["tools"][name], tool_name=name)
        assert before["properties"]["target_distance"]["type"] == "integer"
        before["properties"]["target_distance"]["type"] = "number"
        assert after == before
        assert after["properties"]["target_distance"]["minimum"] == 0
    for name in (
        "help_srd_attack_action",
        "shake_srd_sleeping_creature",
        "cast_spell_timed_senses",
    ):
        assert runtime["tools"][name] == parent["tools"][name]


def test_actual_non_axial_received_reaction_retains_distance_through_paid_reopen_and_retry(
    tmp_path, monkeypatch
):
    dbf = outer.storm.base.base.base.base
    public = dbf.public_casting
    monkeypatch.setattr(public, "bundled_srd_archive", outer._archive)
    monkeypatch.setattr(public, "_spell_case", _public_case)
    rolls, budgets = Rolls(), []
    monkeypatch.setattr(dice, "_rng", rolls)
    consume = ExecutionBudget.consume_operation

    def count(budget):
        if not budgets or budgets[-1] is not budget:
            budgets.append(budget)
        consume(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    database, receipts = tmp_path / "numeric-distance.db", []
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public._create_spell_campaign(
            owner,
            "storm_of_vengeance",
            target_overrides={
                "hp": 1000,
                "max_hp": 1000,
                "saving_throw_modifiers": {"constitution": 20},
            },
            entity_overrides={
                "hp": 1000,
                "max_hp": 1000,
                "temp_hp": 0,
                "saving_throw_modifiers": {"dexterity": -10, "constitution": 20},
            },
        )
        receipts.append(_act(owner, "prepare_srd_creature", **outer._npc("rain_other")))
        for name in outer.storm.TARGETS[2:]:
            receipts.append(
                _act(
                    owner,
                    "prepare_srd_spell_object",
                    object_id=name,
                    material="wood",
                    size="large",
                    durability="resilient",
                )
            )
        receipts.append(_act(owner, "cast_spell_outside_combat", **params))
        source_id = outer._source_row(owner.snapshot("casting").state)["source_id"]
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
        receipts.append(_act(owner, "begin_combat_turn"))
        state = owner.snapshot("casting").state
        ((event_id, event),) = outer._receipts(state)
        assert event["status"] == "open" and event["turn_actor"] == "hero"
        assert event["received_damage"]["effective_damage"] == 12
        assert event["target_state_id"] == "rain_other"
        faces = deepcopy(rolls.sides)
        begun = _act(
            owner,
            "begin_srd_hellish_rebuke",
            caster_state="rain_other",
            reaction_trigger_id=event_id,
            verbal_component_available=True,
            somatic_component_available=True,
        )
        receipts.append(begun)
        assert begun[0].status is ActionStatus.COMMITTED
        spell_id = begun[0].result["context"]["_pending_action_id"]
        saved = owner.snapshot("casting")
        cast = outer._pending(saved.state)["pending_srd_actions"][spell_id]
        assert cast["parameters"]["target_distance"] == sqrt(1604)
        assert cast["parameters"]["target_distance"] != int(sqrt(1604))
        assert saved.state[f"{SRD}.entities"]["rain_other"]["reaction_available"] is False
        assert (
            saved.state[f"{SRD}.entities"]["rain_other"]["spell_uses_remaining"]["hellish_rebuke"]
            == 2
        )
        assert saved.state[f"{SRD}.entities"]["hero"]["hp"] == 1000
        assert rolls.sides == faces
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        completed = _act(owner, "complete_srd_pending_action", pending_action_id=spell_id)
        receipts.append(completed)
        assert completed[0].status is ActionStatus.COMMITTED
        assert "Hellish Rebuke" in str(completed[0].result["result_card"])
        state = owner.snapshot("casting").state
        assert state[f"{SRD}.entities"]["hero"]["hp"] == 994
        assert state[f"{SRD}.entities"]["rain_other"]["hp"] == 988
        assert state[f"{SRD}.entities"]["rain_other"]["spell_uses_remaining"]["hellish_rebuke"] == 1
        assert outer._source_row(state)["source_id"] == source_id
        assert rolls.sides[len(faces) :] == [10, 10, 20, 20]
        receipts.append(_act(owner, "complete_srd_pending_action", pending_action_id=event_id))
        saved = owner.snapshot("casting")
        assert not outer._pending(saved.state)["pending_srd_action_stack"]
        faces = deepcopy(rolls.sides)
    with AuthorityOwner.open(database, "casting-owner") as owner:
        assert owner.snapshot("casting") == saved
        for result, command in receipts:
            assert result.status is ActionStatus.COMMITTED
            assert owner.execute_action(command) == result
        assert owner.snapshot("casting") == saved and rolls.sides == faces
    assert all(budget.max_operations == 10000 for budget in budgets)
    print("Numeric distance public max operations:", max(budget.operations for budget in budgets))
