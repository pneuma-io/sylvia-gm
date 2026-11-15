"""A printed creature target can earn Enhance Ability through the genuine paid cast."""

import ast
import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import pytest
import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_enhance_ability_source import OWNER, bind_runtime_consumers, correct_target_guards
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import PackageRuntimeError, materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.errors import RulesetPreconditionRejected
from sylvia_gm.domain.package_library import ToolDeclaration
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards

ROOT = Path(__file__).parents[1]
NATIVE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-enhance-ability-source/ruleset.yaml")
)


@lru_cache
def runtimes():
    """Emit the corrected declaration while retaining every actual native receiving owner."""
    raw = NATIVE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "8499f04a288bd35c92302c21cd01345c43111c9223b60f91a032af071ac62d07"
    )
    parent = _load_runtime(raw.decode())
    definitions = deepcopy(parent["definitions"]["spells"])
    candidate = yaml.safe_load(
        (ROOT / "rulesets/dnd-srd-5.2.1/content/spell-mechanics.yaml").read_text(encoding="utf-8")
    )["spells"]["enhance_ability"]["definition"]
    assert candidate["requires_willing"] is False
    assert candidate | {"requires_willing": True} == definitions["enhance_ability"]
    definitions["enhance_ability"] = candidate
    tree = ast.parse((ROOT / "scripts/build_srd_5_2_1_spells.py").read_text(encoding="utf-8"))
    emitter = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "_replace_generated_block"
    )
    assert hashlib.sha256(ast.dump(emitter, include_attributes=False).encode()).hexdigest() == (
        "9776f46d84556f77a09f557cd4e2e3e7b09c48c371131512f4f7ee2d661aa3a9"
    )
    namespace = {}
    exec(
        compile(ast.Module(body=[emitter], type_ignores=[]), "actual-block-emitter", "exec"),
        namespace,
    )
    body = [
        "    "
        + identity
        + ": "
        + json.dumps(
            definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        for identity, definition in definitions.items()
    ]
    encoded = namespace["_replace_generated_block"](
        raw.decode(),
        "    # BEGIN GENERATED SPELL DEFINITIONS",
        "    # END GENERATED SPELL DEFINITIONS",
        body,
    )
    encoded = bind_runtime_consumers(encoded)
    assert bind_runtime_consumers(encoded) == encoded
    changed = _load_runtime(encoded)
    restored = deepcopy(changed)
    restored["definitions"]["spells"]["enhance_ability"] = parent["definitions"]["spells"][
        "enhance_ability"
    ]
    restored["tools"][OWNER]["pre_conditions"] = parent["tools"][OWNER]["pre_conditions"]
    assert restored == parent
    return (parent, raw), (changed, encoded.encode())


@lru_cache
def mechanic(name, corrected=True):
    runtime, encoded = runtimes()[int(corrected)]
    return materialize_package_mechanic(
        package_id=budget.SRD,
        package_version="0.3.1",
        package_sha256="a" * 64,
        runtime_bytes=encoded,
        declaration=ToolDeclaration(
            name=name,
            capability="dnd.srd.5.2.1.spells",
            input_schema=owned_payload(_tool_schema(runtime["tools"][name], tool_name=name)),
            writers=("dm",),
            turn_statuses=("open",),
            state_predicates=(),
        ),
    )


def cast_case(monkeypatch):
    monkeypatch.setattr(
        cards, "_spell_definitions", lambda: runtimes()[1][0]["definitions"]["spells"]
    )
    state, data, parameters = cards._spell_case("enhance_ability")
    parameters.update(
        targets=["guard"],
        strength_targets=["guard"],
        target_willing=False,
        all_targets_willing=False,
    )
    return state, data, parameters


def test_real_unwilling_touch_cast_pays_once_and_earns_strength_check_advantage(monkeypatch):
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    monkeypatch.setattr(dice, "_rng", Random(0))
    state, data, parameters = cast_case(monkeypatch)
    before = deepcopy(state)
    monkeypatch.setattr(budget, "_mechanic", lambda name: mechanic(name, corrected=False))
    with pytest.raises((RulesetPreconditionRejected, PackageRuntimeError), match="willing"):
        budget._run("cast_spell", state, participant_data=data, **parameters)
    assert state == before

    monkeypatch.setattr(budget, "_mechanic", mechanic)
    cast = budget._run("cast_spell", state, participant_data=data, **parameters).result
    assert cast["status"] == "committed"
    hero = state[f"{budget.SRD}.entities"]["hero"]
    guard = state[f"{budget.SRD}.entities"]["guard"]
    assert hero["resources"]["spell_slot_2"] == 2
    assert hero["concentration_spell_id"] == "enhance_ability"
    assert hero["concentration_effect_family"] == "multi_target_concentration_ability_advantage"
    assert hero["concentration_expires_tick"] - hero["concentration_started_tick"] == 1200
    assert list(hero["concentration_strength_target_state_ids"]) == ["guard"]
    assert "hero" in guard["concentration_d20_modifier_source_ids"]
    assert state[f"{budget.SRD}.combat"]["action_available"] is False
    assert cast["result_card"]["title"] == "Enhance Ability"
    assert [row["label"] for row in cast["result_card"]["details"]] == ["Guard"]
    summary = {row["label"]: row["value"] for row in cast["result_card"]["summary"]}
    assert summary["Spell slots spent"] == 1

    original_advantage = dice.advantage
    rolls = []

    def record_advantage(modifier=0):
        result = original_advantage(modifier)
        rolls.append(result)
        return result

    monkeypatch.setattr(dice, "advantage", record_advantage)
    check = budget._run(
        "d20_test",
        state,
        actor_state="guard",
        kind="ability_check",
        ability="strength",
        ability_modifier=0,
        proficiency_bonus=0,
        proficiency_factor="none",
        circumstantial_modifier=0,
        target_number=10,
    ).result
    assert check["status"] == "committed"
    assert [(roll.sides, roll.count) for roll in rolls] == [(20, 2)]
    assert rolls[0].total == max(rolls[0].results)
    assert state[f"{budget.SRD}.entities"]["hero"]["resources"]["spell_slot_2"] == 2


@pytest.mark.parametrize("guard", ["caster_can_touch_all_targets", "all_targets_have_clear_path"])
def test_unwilling_cast_keeps_real_touch_and_clear_path_rejections(monkeypatch, guard):
    state, data, parameters = cast_case(monkeypatch)
    parameters[guard] = False
    before = deepcopy(state)
    monkeypatch.setattr(budget, "_mechanic", mechanic)
    with pytest.raises((RulesetPreconditionRejected, PackageRuntimeError), match="Touch reach"):
        budget._run("cast_spell", state, participant_data=data, **parameters)
    assert state == before


def test_source_binder_rejects_an_unfamiliar_target_gate():
    tool = deepcopy(runtimes()[0][0]["tools"][OWNER])
    tool["pre_conditions"][-1]["condition"] = "all_targets_willing == true"
    with pytest.raises(ValueError, match="unfamiliar retained touch/path"):
        correct_target_guards(tool)
