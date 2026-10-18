"""Source identity refresh retains the real paid Until-dispelled mask dispatch."""

from pathlib import Path

import yaml
from scripts.srd_full.srd_detection_mask_casting import extend_runtime as cast_masks
from scripts.srd_full.srd_spell_detection_masks import PROFILES, _install_tool, _schema
from scripts.srd_full.srd_spell_source_instances import extend_runtime as cast_sources
from scripts.srd_full.srd_spell_trigger_composition import _effect_lists
from scripts.srd_full.srd_timed_actor_defenses import _call, _load_runtime, _parameter, _tool, _when


def test_source_refresh_preserves_paid_mask_branch_and_its_sequester_allocator():
    package = Path(__file__).resolve().parents[2] / "rulesets/dnd-srd-5.2.1/runtime/ruleset.yaml"
    native = _load_runtime(package.read_text())
    requirements = {"sequester": {"duration": "Until dispelled"}}
    definitions = {"sequester": PROFILES["sequester"]}
    cast = _tool(
        "Resolve the existing paid casting dispatch.",
        [
            _parameter("spell", enum_values=["sequester"]),
            _parameter("caster_state", resolve="entity"),
        ],
        [
            _when(
                "_spell_definition.family == 'information_spell'",
                [
                    _call("cast_spell_information", {}),
                ],
            )
        ],
    )
    text = yaml.safe_dump(
        {
            "definitions": {"spells": definitions, "spell_casting": requirements},
            "tools": {"cast_spell": cast, "apply_srd_detection_mask": _install_tool(native)},
            "procedures": {},
            "definition_row_schemas": {
                "definitions.spells": {
                    "variants": {
                        "detection_mask_spell": _schema(),
                    }
                }
            },
        },
        sort_keys=False,
    )
    text = cast_masks(text)
    for _ in range(2):
        text = cast_sources(text, requirements=requirements, definitions=definitions)
        cast = _load_runtime(text)["tools"]["cast_spell"]
        branches = [
            effect
            for effects in _effect_lists(cast["effects"])
            for effect in effects
            if "_spell_definition.family == 'detection_mask_spell'" in effect.get("condition", "")
        ]
        assert len(branches) == 1
        calls = [
            effect["call"]
            for effects in _effect_lists(branches[0]["on_true"])
            for effect in effects
            if "call" in effect
        ]
        assert calls.count("begin_srd_spell_source_instance") == 1
        assert calls.count("apply_srd_detection_mask") == 1
