"""The proposed source row changes only the actual composite family's Bludgeoning dice."""

import ast
import hashlib
import json
from copy import deepcopy
from functools import lru_cache
from pathlib import Path
from random import Random

import yaml
from scripts.srd_full.build_srd_5_2_1_mechanics import _tool_schema
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime

from sylvia_gm import dice
from sylvia_gm.application.package_runtime import materialize_package_mechanic
from sylvia_gm.domain.authority import owned_payload
from sylvia_gm.domain.package_library import ToolDeclaration
from sylvia_gm.runtime import _effect_executor
from tests import test_srd_action_budget as budget
from tests import test_srd_spell_result_cards as cards

ROOT = Path(__file__).resolve().parents[2]
NATIVE = Path(
    str(Path(__file__).resolve().parents[2] / "tests/fixtures/srd-full-external/test-srd-ice-storm-growth/ruleset.yaml")
)


@lru_cache
def proposed_runtime():
    """Use the real definition-block emitter with the proposed row and authentic native owners."""
    builder_text = (ROOT / "scripts/build_srd_5_2_1_spells.py").read_text()
    function = next(
        node
        for node in ast.parse(builder_text).body
        if isinstance(node, ast.FunctionDef) and node.name == "_replace_generated_block"
    )
    assert (
        hashlib.sha256(ast.dump(function, include_attributes=False).encode()).hexdigest()
        == '9776f46d84556f77a09f557cd4e2e3e7b09c48c371131512f4f7ee2d661aa3a9'
    )
    namespace = {}
    exec(
        compile(ast.Module(body=[function], type_ignores=[]), "actual-block-emitter", "exec"),
        namespace,
    )
    raw = NATIVE.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "8499f04a288bd35c92302c21cd01345c43111c9223b60f91a032af071ac62d07"
    )
    parent = _load_runtime(raw.decode())
    definitions = deepcopy(parent["definitions"]["spells"])
    candidate = yaml.safe_load(
        (ROOT / "rulesets/dnd-srd-5.2.1/content/spell-mechanics.yaml").read_text()
    )["spells"]["ice_storm"]
    definitions["ice_storm"] = candidate["definition"]
    body = [
        "    "
        + spell_id
        + ": "
        + json.dumps(
            definition, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
        )
        for spell_id, definition in definitions.items()
    ]
    encoded = namespace["_replace_generated_block"](
        raw.decode(),
        "    # BEGIN GENERATED SPELL DEFINITIONS",
        "    # END GENERATED SPELL DEFINITIONS",
        body,
    ).encode()
    runtime = _load_runtime(encoded.decode())
    assert runtime["tools"] == parent["tools"]
    assert runtime["definitions"]["spells"] == definitions
    restored = deepcopy(runtime)
    restored["definitions"]["spells"]["ice_storm"] = parent["definitions"]["spells"]["ice_storm"]
    assert restored == parent
    return runtime, encoded


@lru_cache
def mechanic(name):
    runtime, encoded = proposed_runtime()
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


def test_actual_generated_ice_storm_descriptor_keeps_cold_and_scales_bludgeoning(monkeypatch):
    monkeypatch.setattr(budget, "_mechanic", mechanic)
    monkeypatch.setattr(
        cards, "_spell_definitions", lambda: proposed_runtime()[0]["definitions"]["spells"]
    )
    monkeypatch.setattr(dice, "set_seed", lambda _seed: None)
    original_roll = _effect_executor.dice_roll
    rolls = []

    def record_roll(sides, count=1, modifier=0):
        result = original_roll(sides, count, modifier)
        rolls.append(result)
        return result

    monkeypatch.setattr(_effect_executor, "dice_roll", record_roll)
    for slot, bludgeoning_count in ((4, 2), (5, 3)):
        rolls.clear()
        monkeypatch.setattr(dice, "_rng", Random(0))
        state, data, parameters = cards._spell_case("ice_storm")
        target = state[f"{budget.SRD}.entities"]["guard"]
        target.update(hp=1000, max_hp=1000, temp_hp=0, damage_resistances=[], damage_immunities=[])
        target["saving_throw_modifiers"]["dexterity"] = -30
        resource = f"spell_slot_{slot}"
        parameters.update(slot_resource=resource, targets=["guard"])
        before_slots = state[f"{budget.SRD}.entities"]["hero"]["resources"][resource]
        result = budget._run("cast_spell", state, participant_data=data, **parameters).result
        assert result["status"] == "committed"
        damage_rolls = [roll for roll in rolls if roll.sides in (6, 10)]
        assert [(roll.sides, roll.count) for roll in damage_rolls] == [
            (10, bludgeoning_count),
            (6, 4),
        ]
        assert sum(roll.sides == 20 for roll in rolls) == 1
        assert 1000 - state[f"{budget.SRD}.entities"]["guard"]["hp"] == sum(
            roll.total for roll in damage_rolls
        )
        assert state[f"{budget.SRD}.entities"]["hero"]["resources"][resource] == before_slots - 1
