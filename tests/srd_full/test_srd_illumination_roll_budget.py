"""Invisible attack hooks preserve real rolls while leaving ordinary saves inexpensive."""

from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_spell_illumination import _compose_invisibility_roll

from sylvia_gm import dice
from sylvia_gm.application.authority import AuthorityOwner
from sylvia_gm.application.bundled_packages import _archive, _srd_drafts
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import ActionStatus, owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime._types import ExecutionBudget
from tests import test_srd_action_budget, test_srd_spell_result_cards
from tests import test_srd_public_spell_casting as public_casting
from tests.srd_full.test_srd_action_budget import SRD, _run
from tests.srd_full.test_srd_npc_spell_casting import _act
from tests.srd_full.test_srd_spell_illumination import _case, _cast, _records

ROOT = Path(__file__).resolve().parents[2]
ROLL = {
    "actor_state": "guard",
    "kind": "attack_roll",
    "ability": "strength",
    "ability_modifier": 0,
    "proficiency_bonus": 0,
    "proficiency_factor": "none",
    "circumstantial_modifier": 0,
    "target_number": 99,
}


@lru_cache
def _runtime(repaired=True):
    text = (ROOT / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml").read_text()
    runtime = yaml.load(text, Loader=yaml.CSafeLoader)
    tool = _compose_invisibility_roll(runtime["tools"]["d20_test"])
    assert _compose_invisibility_roll(tool) == tool
    if not repaired:
        # Reconstruct the exact legacy Light hook even after canonical adopts the optimized one.
        query = next(
            effect
            for effect in tool["effects"]
            if any(
                child.get("set", {}).get("field") == "_roll_invisible_suppressed"
                for child in effect.get("on_true", [])
            )
        )
        query["condition"] = "actor_state != null and lasting_state.has_illumination_spells == true"
        tool["effects"].insert(
            0, {"set": {"field": "_roll_invisible_suppressed", "value": False}}
        )
        endings = []

        def restore_legacy_ending(effects):
            retained = []
            for effect in effects:
                if any(
                    child.get("call") == "end_srd_invisibility_for_actor"
                    for child in effect.get("on_true", [])
                ):
                    endings.append(deepcopy(effect))
                    continue
                for key in ("on_true", "on_false"):
                    if key in effect:
                        restore_legacy_ending(effect[key])
                retained.append(effect)
            effects[:] = retained

        restore_legacy_ending(tool["effects"])
        assert len(endings) == 1
        endings[0]["condition"] = (
            "actor_state != null and kind == 'attack_roll' and "
            "lasting_state.has_illumination_spells == true"
        )
        tool["effects"].append(endings[0])
    runtime["tools"]["d20_test"] = tool
    text = yaml.safe_dump(runtime, sort_keys=False)
    return runtime, text.encode()


@lru_cache
def _mechanic(name, repaired=True):
    runtime, payload = _runtime(repaired)
    return materialize_package_mechanic(
        package_id=SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=payload,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


@pytest.fixture(autouse=True)
def _actual_runtime(monkeypatch):
    runtime, _ = _runtime()
    monkeypatch.setattr(test_srd_action_budget, "_mechanic", _mechanic)
    monkeypatch.setattr(
        test_srd_spell_result_cards, "_spell_definitions", lambda: runtime["definitions"]["spells"]
    )
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Random(0))


@pytest.mark.parametrize("active,invisible", [(False, False), (True, False), (True, True)])
def test_real_saving_throw_retains_roll_and_state_with_lower_operation_cost(
    monkeypatch, active, invisible
):
    state, _, _ = _case("invisibility")
    state[f"{SRD}.state"]["has_illumination_spells"] = active
    if invisible:
        state[f"{SRD}.entities"]["guard"]["conditions"].append("invisible")
    operation_count = 0
    original = ExecutionBudget.consume_operation

    def count(budget):
        nonlocal operation_count
        operation_count += 1
        original(budget)

    monkeypatch.setattr(ExecutionBudget, "consume_operation", count)
    results = []
    for repaired in (False, True):
        monkeypatch.setattr(
            test_srd_action_budget,
            "_mechanic",
            lambda name, repaired=repaired: _mechanic(name, repaired),
        )
        monkeypatch.setattr(dice, "_rng", Random(0))
        saved = deepcopy(state)
        operation_count = 0
        result = _run("d20_test", saved, **(ROLL | {"kind": "saving_throw"}))
        results.append((result.result["context"]["_d20_test"], saved, operation_count))
    assert results[0][:2] == results[1][:2]
    assert len(results[1][0]["results"]) == 1
    assert results[0][2] - results[1][2] >= 2
    assert ExecutionBudget().max_operations == 10_000


def test_native_invisible_attacker_keeps_advantage_without_any_managed_source():
    state, _, _ = _case("invisibility")
    state[f"{SRD}.entities"]["guard"]["conditions"].append("invisible")
    result = _run("d20_test", state, **ROLL)
    assert len(result.result["context"]["_d20_test"]["results"]) == 2
    assert "invisible" in state[f"{SRD}.entities"]["guard"]["conditions"]


def test_actual_faerie_outline_suppresses_native_invisible_attack_advantage():
    state, _, _, _ = _cast("faerie_fire", illumination_object="", illumination_position=[1, 0, 0])
    assert any(record["source"] == "other" for record in _records(state, "faerie_fire"))
    state[f"{SRD}.entities"]["other"]["conditions"].append("invisible")
    result = _run("d20_test", state, **(ROLL | {"actor_state": "other"}))
    assert len(result.result["context"]["_d20_test"]["results"]) == 1
    assert "invisible" in state[f"{SRD}.entities"]["other"]["conditions"]


@pytest.mark.parametrize("spell,ends", [("invisibility", True), ("greater_invisibility", False)])
def test_actual_missed_roll_ends_only_ordinary_source_after_its_advantage(spell, ends):
    state, _, _, _ = _cast(spell)
    result = _run("d20_test", state, **ROLL)
    roll = result.result["context"]["_d20_test"]
    assert len(roll["results"]) == 2
    assert roll["total"] < 99
    assert ("invisible" in state[f"{SRD}.entities"]["guard"]["conditions"]) is not ends
    assert bool(_records(state, spell)) is not ends


@lru_cache
def _test_archive():
    runtime, payload = _runtime()
    primary, reference = _srd_drafts()
    record = deepcopy(primary.record)
    for tool in record["tools"]:
        if tool["name"] in runtime["tools"]:
            tool["input_schema"] = _tool_schema(
                runtime["tools"][tool["name"]], tool_name=tool["name"]
            )
    return _archive(
        (
            NormalizedPackageDraft(
                record,
                dict(primary.payloads) | {"runtime/ruleset.yaml": payload},
                primary.format_version,
            ),
            reference,
        )
    )


def test_public_invisible_missed_roll_reopens_and_durable_retry_does_not_reroll(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(public_casting, "bundled_srd_archive", _test_archive)
    monkeypatch.setattr(public_casting, "_spell_case", _case)
    database = tmp_path / "invisible-roll.db"
    with AuthorityOwner.open(database, "casting-owner") as owner:
        _, _, params = public_casting._create_spell_campaign(owner, "invisibility")
        cast, _ = _act(owner, "cast_spell_outside_combat", **params)
        assert cast.status is ActionStatus.COMMITTED, cast.result
        assert _records(owner.snapshot("casting").state, "invisibility")
        receipt, command = _act(owner, "d20_test", **ROLL)
        assert receipt.status is ActionStatus.COMMITTED, receipt.result
        assert len(receipt.result["context"]["_d20_test"]["results"]) == 2
        assert receipt.result["context"]["_d20_test"]["total"] < 99
        saved = owner.snapshot("casting")
        assert not _records(saved.state, "invisibility")
        assert "invisible" not in saved.state[f"{SRD}.entities"]["guard"]["conditions"]
    with AuthorityOwner.open(database, "casting-owner") as reopened:
        assert reopened.snapshot("casting") == saved

        class NoReroll(Random):
            def randint(self, _a, _b):
                pytest.fail("Durable Invisible attack retry rolled again")

        monkeypatch.setattr(dice, "_rng", NoReroll(99))
        assert reopened.execute_action(command) == receipt
        assert reopened.snapshot("casting") == saved
