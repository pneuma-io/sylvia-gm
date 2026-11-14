"""Final reaction composition preserves the neutral spatial owner's state path."""

import re
from pathlib import Path

from scripts.srd_full.srd_reaction_spells import extend_runtime
from scripts.srd_full.srd_spell_object_forces import _control_tool
from scripts.srd_full.srd_timed_actor_defenses import _load_runtime
from scripts.srd_full.srd_timed_senses import _replace_tools


def test_final_reaction_fence_preserves_mage_hand_spatial_scene():
    runtime = (
        Path(__file__).parents[1] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
    ).read_text()
    runtime = _replace_tools(runtime, {"control_srd_mage_hand": _control_tool()})
    runtime = extend_runtime(runtime)
    tool = _load_runtime(runtime)["tools"]["control_srd_mage_hand"]

    def scenes(value):
        if isinstance(value, list):
            for item in value:
                yield from scenes(item)
        elif isinstance(value, dict):
            for kind in ("spatial_query", "spatial_mutation"):
                if kind in value:
                    yield value[kind]["scene"]
            for item in value.values():
                yield from scenes(item)

    actual = list(scenes(tool["effects"]))
    assert actual
    assert all(
        re.fullmatch(r"state\.[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", scene)
        for scene in actual
    ), actual
    assert tool["effects"][0]["call"] == "require_srd_pending_window_closed"
