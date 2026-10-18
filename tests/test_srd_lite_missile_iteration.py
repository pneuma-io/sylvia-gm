"""The authored dart family must execute one nested damage call per actual die."""
import pytest

from scripts.srd_lite_missiles import bind_missiles
from sylvia_gm.dice import RollResult
from sylvia_gm.runtime import _effect_executor
from sylvia_gm.runtime._types import ExecutionContext, ToolResult


@pytest.mark.parametrize("slot,faces", [(1, (1, 3, 1)), (3, (1, 3, 1, 4, 2))])
def test_composed_darts_reach_each_allocated_target_once(monkeypatch, slot, faces):
    runtime = {
        "definitions": {"spells": {"magic_missile": {}}, "spell_casting": {"magic_missile": {}}},
        "tools": {name: {"effects": []} for name in ("cast_spell", "begin_srd_spell_action", "cast_spell_automatic_force_projectiles")},
    }
    bind_missiles(runtime)
    allocation = ["guard", "other", "guard"] + ["extra"] * (len(faces) - 3)
    rolls = []
    calls = []

    def rolled(sides, count=1, modifier=0):
        rolls.append((sides, count, modifier))
        return RollResult(sides=sides, count=count, results=faces, total=sum(faces), modifier=modifier)

    def nested(name, parameters, context):
        assert name == "apply_srd_lite_force_dart"
        calls.append(dict(parameters))
        return ToolResult(name, context={"_spell_target_summary": f"target {parameters['target']}", "_spell_target_detail": f"dart {parameters['index']}: {parameters['face']}"})

    monkeypatch.setattr(_effect_executor, "dice_roll", rolled)
    context = ExecutionContext(
        params={"base_projectiles": 3, "base_level": 1, "slot_level": slot, "projectiles_per_slot_level": 1, "flat_damage_per_projectile": 1, "damage_die": "d4", "projectile_targets": allocation},
        vars={"_target_id": "guard", "_caster_state_id": "hero", "_lasting_state_id": "state"},
    )
    _effect_executor.execute_effects(runtime["tools"]["cast_spell_automatic_force_projectiles"]["effects"], context, nested)
    assert [(row["target"], row["face"], row["index"]) for row in calls] == [(target, face, index) for index, (target, face) in enumerate(zip(allocation, faces, strict=True), 1)]
    assert rolls == [(4, len(faces), 0)]
    assert len(context.vars["_spell_summary_rows"]) == len(faces)
    assert len(context.vars["_spell_detail_rows"]) == len(faces)
