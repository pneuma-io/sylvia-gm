"""Ending one Lite source must retain other independently tracked casters."""
import pytest

from scripts.srd_lite_profiles import TRUE_STRIKE, bind_profiles
from sylvia_gm.runtime import _effect_executor
from sylvia_gm.runtime._types import ExecutionContext


def composed():
    runtime = {
        "definitions": {"spells": {}, "spell_casting": {"true_strike": {}}},
        "definition_row_schemas": {"definitions.spells": {"variants": {"true_strike": {"properties": {key: {} for key in TRUE_STRIKE if key != "family"}}}}},
        "tools": {name: {"effects": []} for name in ("cast_spell", "begin_srd_spell_action", "resolve_srd_attack_conditions", "end_srd_concentration_node", "process_srd_turn_effects")},
    }
    runtime["tools"]["cast_spell"]["effects"] = [{"condition": "_spell_definition.family == 'true_strike'", "on_true": []}]
    bind_profiles(runtime)
    return runtime


@pytest.mark.parametrize("spell,ending,tracked", [
    ("true_strike", "hero", ["hero", "ally-a", "ally-b"]),
    ("bless", "hero", ["ally-a", "hero", "ally-b"]),
    ("cloud_of_daggers", "hero", ["ally-a", "ally-b", "hero"]),
    ("true_strike", "unregistered", ["ally-a", "ally-b"]),
])
def test_actual_cleanup_branch_keeps_other_casters(spell, ending, tracked):
    runtime = composed()
    source = {"concentration_spell_id": spell, "concentration_effect_family": "lite_true_strike" if spell == "true_strike" else "other", "lite_true_strike": {"target": "guard"}}
    state_key = "lite_true_strike_casters" if spell == "true_strike" else "lite_concentration_casters"
    result_key = "_lite_casters_kept" if spell == "true_strike" else "_lite_concentration_kept"
    lasting = {state_key: tracked.copy()}
    context = ExecutionContext(params={"source_state": source, "lasting_state": lasting}, vars={"_source_state_id": ending})
    # Exercise the two authored prefix branches at their real runtime seam.
    # This source test proves list computation, not admitted-state persistence.
    _effect_executor.execute_effects(runtime["tools"]["end_srd_concentration_node"]["effects"][:2], context)
    assert context.vars[result_key] == [caster for caster in tracked if caster != ending]
    assert lasting[state_key] == tracked  # The input snapshot is not consumed in place.
