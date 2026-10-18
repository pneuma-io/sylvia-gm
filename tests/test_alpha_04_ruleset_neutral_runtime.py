"""Alpha-04 owning-boundary proof for RULE-005 executable neutrality."""

from __future__ import annotations

import pytest
import yaml

from sylvia_gm.ruleset.schema import RulesetFile
from sylvia_gm.runtime._effect_executor import (
    _COMPATIBILITY_EFFECT_KEYS,
    _EFFECT_HANDLERS,
    _NEUTRAL_EFFECT_KEYS,
)
from sylvia_gm.runtime._runtime import RulesetRuntime
from sylvia_gm.runtime._schema_validator import validate_ruleset


def _meta() -> dict[str, str]:
    return {
        "id": "signal-and-static",
        "name": "Signal and Static",
        "version": "0.1.0",
        "author": "Sylvia GM conformance",
        "license": "CC-BY-4.0",
    }


def test_ability_free_ruleset_accepts_independent_skills_and_no_features() -> None:
    ruleset = RulesetFile.model_validate(
        {
            "meta": _meta(),
            "skills": [
                {"name": "Intrusion"},
                {"name": "Field Medicine"},
            ],
        }
    )

    errors = [issue for issue in ruleset.validate() if issue.severity == "error"]

    assert ruleset.abilities == {}
    assert ruleset.features is None
    assert [skill.ability for skill in ruleset.skills] == [None, None]
    assert errors == []
    print("RULE-005 ABILITY-FREE", ruleset.meta.id, [s.name for s in ruleset.skills])


def test_declared_stat_does_not_gain_implicit_cairn_mechanics() -> None:
    ruleset = RulesetFile.model_validate(
        {
            "meta": _meta(),
            "abilities": {
                "focus": {
                    "name": "Focus",
                    "used_for": ["resist_signal_bleed"],
                }
            },
        }
    )
    focus = ruleset.abilities["focus"]

    assert focus.default_min is None
    assert focus.default_max is None
    assert focus.generation is None
    assert focus.modifier_formula is None
    assert focus.save_label is None
    assert not [issue for issue in ruleset.validate() if issue.severity == "warning"]
    with pytest.raises(ValueError, match="does not declare a modifier formula"):
        ruleset.modifier_for("focus", 4)
    print("RULE-005 EXPLICIT STAT", focus.name, focus.used_for)


def test_executable_yaml_requires_only_neutral_metadata() -> None:
    yaml_content = """
meta:
  id: signal-and-static
  name: Signal and Static
  version: 0.1.0
  author: Sylvia GM conformance
  license: CC-BY-4.0
skills:
  - name: Intrusion
tools:
  mark_stress:
    description: Mark package-owned Stress.
    parameters:
      - name: amount
        type: integer
        required: true
    effects:
      - increment:
          path: package_state.stress
          value: amount
"""

    errors = validate_ruleset(yaml_content)

    assert errors == []
    print("RULE-005 EXECUTABLE YAML", "no abilities/features required")


def test_model_tool_availability_is_ruleset_owned_and_validated_neutrally() -> None:
    raw = {
        "meta": _meta(),
        "model_tool_availability": {
            "mark_stress": {
                "state_predicates": [
                    {
                        "namespace": "signal.static",
                        "key": "scene.phase",
                        "equals": "broadcast",
                        "negated": True,
                    }
                ]
            }
        },
        "tools": {
            "mark_stress": {
                "description": "Mark package-owned Stress.",
                "parameters": [],
                "effects": [],
            }
        },
    }

    assert validate_ruleset(yaml.safe_dump(raw, sort_keys=False)) == []
    assert RulesetRuntime(None, raw).model_tool_names == frozenset({"mark_stress"})

    raw["tools"]["mark_stress"]["metadata"] = {
        "model_behavior": "required_engine_transition"
    }
    assert validate_ruleset(yaml.safe_dump(raw, sort_keys=False)) == []
    raw["tools"]["mark_stress"]["metadata"][
        "model_behavior"
    ] = "leaves_player_intent_pending"
    assert validate_ruleset(yaml.safe_dump(raw, sort_keys=False)) == []
    raw["tools"]["mark_stress"]["metadata"][
        "model_behavior"
    ] = "resolves_player_intent"
    assert validate_ruleset(yaml.safe_dump(raw, sort_keys=False)) == []
    raw["tools"]["mark_stress"]["metadata"]["model_behavior"] = "guess_for_the_model"
    errors = validate_ruleset(yaml.safe_dump(raw, sort_keys=False))
    assert any("metadata.model_behavior is unsupported" in error for error in errors)
    raw["tools"]["mark_stress"]["metadata"][
        "model_behavior"
    ] = "required_engine_transition"

    raw["model_tool_availability"]["mark_stress"]["state_predicates"][0]["negated"] = "yes"
    errors = validate_ruleset(yaml.safe_dump(raw, sort_keys=False))
    assert any("negated must be boolean" in error for error in errors)

    del raw["model_tool_availability"]["mark_stress"]
    raw["model_tool_availability"]["missing_operation"] = {"state_predicates": []}
    errors = validate_ruleset(yaml.safe_dump(raw, sort_keys=False))
    assert any("unknown runtime tool 'missing_operation'" in error for error in errors)


@pytest.mark.parametrize(
    ("parameter_update", "expected"),
    (
        ({"from_state": "entities.current"}, "must name a package state.* or combat.* field"),
        (
            {"from_state": "combat.current_actor", "default": "fallback"},
            "cannot define both from_state and default",
        ),
    ),
)
def test_state_bound_model_parameters_fail_closed(
    parameter_update: dict[str, object], expected: str
) -> None:
    parameter = {
        "name": "actor",
        "type": "string",
        "resolve": "entity",
        **parameter_update,
    }
    raw = {
        "meta": _meta(),
        "tools": {
            "close_window": {
                "description": "Close the current package-owned window.",
                "parameters": [parameter],
                "effects": [],
            }
        },
    }

    errors = validate_ruleset(yaml.safe_dump(raw, sort_keys=False))

    assert any(expected in error for error in errors), errors


def test_every_effect_handler_is_classified_without_restricting_compatibility() -> None:
    assert not (_NEUTRAL_EFFECT_KEYS & _COMPATIBILITY_EFFECT_KEYS)
    assert frozenset(_EFFECT_HANDLERS) == (_NEUTRAL_EFFECT_KEYS | _COMPATIBILITY_EFFECT_KEYS)
    assert {"set", "increment", "decrement", "dice"} <= _NEUTRAL_EFFECT_KEYS
    assert {
        "apply_damage",
        "heal",
        "save_throw",
        "apply_ability_damage",
        "hunger_dice",
    } <= _COMPATIBILITY_EFFECT_KEYS
    print(
        "RULE-005 HANDLERS",
        len(_NEUTRAL_EFFECT_KEYS),
        "neutral",
        len(_COMPATIBILITY_EFFECT_KEYS),
        "compatibility",
    )


def _definition_lookup_ruleset() -> dict[str, object]:
    return {
        "meta": _meta(),
        "tools": {
            "run_program": {
                "description": "Run one declared signal program.",
                "parameters": [
                    {
                        "name": "program",
                        "type": "enum",
                        "enum_values": ["amplify", "mask"],
                        "required": True,
                    },
                    {"name": "intensity", "type": "integer", "required": True},
                ],
                "pre_conditions": [
                    {
                        "condition": "intensity >= 0",
                        "error": "Intensity cannot be negative.",
                    }
                ],
                "effects": [
                    {
                        "table_lookup": {
                            "table": "definitions.programs",
                            "key": "program",
                            "required": True,
                            "exact_selector": True,
                        },
                        "store_as": "_definition",
                    },
                    {"set": {"field": "_program_family", "value": "_definition.family"}},
                    {
                        "condition": "_definition.family == 'signal'",
                        "on_true": [
                            {
                                "set": {
                                    "field": "_program_output",
                                    "value": "intensity * _definition.multiplier",
                                }
                            }
                        ],
                    },
                    {
                        "condition": "_definition.family == 'stealth'",
                        "on_true": [
                            {
                                "set": {
                                    "field": "_program_output",
                                    "value": "intensity + _definition.offset",
                                }
                            }
                        ],
                    },
                ],
            }
        },
        "definitions": {
            "programs": {
                "amplify": {
                    "family": "signal",
                    "multiplier": 2,
                },
                "mask": {
                    "family": "stealth",
                    "offset": 4,
                },
            }
        },
        "definition_row_schemas": {
            "definitions.programs": {
                "discriminator": "family",
                "variants": {
                    "signal": {
                        "type": "object",
                        "properties": {
                            "multiplier": {
                                "type": "integer",
                                "minimum": 1,
                                "maximum": 8,
                            }
                        },
                        "required": ["multiplier"],
                        "additionalProperties": False,
                    },
                    "stealth": {
                        "type": "object",
                        "properties": {"offset": {"type": "integer", "minimum": 0, "maximum": 8}},
                        "required": ["offset"],
                        "additionalProperties": False,
                    },
                },
            }
        },
    }


def test_required_definition_lookup_executes_package_owned_non_dnd_variants() -> None:
    raw = _definition_lookup_ruleset()
    assert validate_ruleset(yaml.safe_dump(raw, sort_keys=False)) == []
    parsed = RulesetFile.model_validate(raw)
    assert set(parsed.definition_row_schemas) == {"definitions.programs"}

    runtime = RulesetRuntime(None, raw)
    amplified = runtime.execute_tool("run_program", {"program": "amplify", "intensity": 3})
    masked = runtime.execute_tool("run_program", {"program": "mask", "intensity": 3})

    assert amplified.success is True, amplified.error
    assert amplified.context["_definition"] == {"family": "signal", "multiplier": 2}
    assert amplified.context["_program_family"] == "signal"
    assert amplified.context["_program_output"] == 6
    assert masked.success is True, masked.error
    assert masked.context["_program_family"] == "stealth"
    assert masked.context["_program_output"] == 7


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("missing_table", "required table 'definitions.missing' does not exist"),
        ("enum_mismatch", "selector enum must exactly match table keys"),
        ("invalid_row", "row 'mask' must be a mapping"),
        ("public_store", "store_as must be a private context identifier"),
        ("missing_row_schema", "exact selector requires a definition row schema"),
        ("missing_field", "row 'mask' is missing discriminator 'family'"),
        ("extra_field", "row 'mask' contains undeclared field 'unexpected'"),
        ("wrong_type", "row 'mask'.offset has invalid type"),
        ("below_minimum", "row 'mask'.offset is below its minimum"),
        ("above_maximum", "row 'mask'.offset is above its maximum"),
        ("unknown_variant", "row 'mask' selects undeclared variant 'unknown'"),
        ("missing_variant", "row 'mask' selects undeclared variant 'stealth'"),
        ("invalid_schema", "variant 'stealth' contains unsupported field 'pattern'"),
        ("misapplied_schema_keyword", "field 'properties' is not valid for type 'integer'"),
        ("null_numeric_bound", "has an invalid minimum"),
    ),
)
def test_required_definition_lookup_admission_fails_closed(mutation: str, expected: str) -> None:
    raw = _definition_lookup_ruleset()
    tool = raw["tools"]["run_program"]
    programs = raw["definitions"]["programs"]
    lookup = tool["effects"][0]
    if mutation == "missing_table":
        lookup["table_lookup"]["table"] = "definitions.missing"
    elif mutation == "enum_mismatch":
        tool["parameters"][0]["enum_values"] = ["amplify"]
    elif mutation == "invalid_row":
        programs["mask"] = "not-a-definition"
    elif mutation == "public_store":
        lookup["store_as"] = "definition"
    elif mutation == "missing_row_schema":
        del raw["definition_row_schemas"]["definitions.programs"]
    elif mutation == "missing_field":
        del programs["mask"]["family"]
    elif mutation == "extra_field":
        programs["mask"]["unexpected"] = True
    elif mutation == "wrong_type":
        programs["mask"]["offset"] = "four"
    elif mutation == "below_minimum":
        programs["mask"]["offset"] = -1
    elif mutation == "above_maximum":
        programs["mask"]["offset"] = 9
    elif mutation == "unknown_variant":
        programs["mask"]["family"] = "unknown"
    elif mutation == "missing_variant":
        del raw["definition_row_schemas"]["definitions.programs"]["variants"]["stealth"]
    elif mutation == "invalid_schema":
        raw["definition_row_schemas"]["definitions.programs"]["variants"]["stealth"]["pattern"] = (
            ".*"
        )
    else:
        offset_schema = raw["definition_row_schemas"]["definitions.programs"]["variants"][
            "stealth"
        ]["properties"]["offset"]
        if mutation == "misapplied_schema_keyword":
            offset_schema["properties"] = {}
        else:
            offset_schema["minimum"] = None

    errors = validate_ruleset(yaml.safe_dump(raw, sort_keys=False))

    assert any(expected in error for error in errors), errors


def test_required_definition_lookup_runtime_rejects_a_missing_table() -> None:
    raw = _definition_lookup_ruleset()
    del raw["definitions"]["programs"]

    result = RulesetRuntime(None, raw).execute_tool(
        "run_program", {"program": "amplify", "intensity": 3}
    )

    assert result.success is False
    assert result.error == (
        "Effect execution error: Effect 'table_lookup' failed: "
        "required table 'definitions.programs' does not exist"
    )


@pytest.mark.parametrize(
    ("mutation", "expected"),
    (
        ("null_definitions", "required table 'definitions.programs' does not exist"),
        ("consequence", "exact selector requires a definition row schema"),
        ("procedure", "exact selector must name a required enum parameter"),
    ),
)
def test_definition_lookup_admission_has_no_nested_or_null_bypass(
    mutation: str, expected: str
) -> None:
    raw = _definition_lookup_ruleset()
    tool = raw["tools"]["run_program"]
    lookup = tool["effects"].pop(0)
    if mutation == "null_definitions":
        raw["definitions"] = None
        tool["effects"].insert(0, lookup)
    elif mutation == "consequence":
        del raw["definition_row_schemas"]["definitions.programs"]
        tool["effects"] = [
            {"dice": {"expression": "1d6"}, "store_as": "_program_roll"},
            {
                "reroll": {
                    "source": "_program_roll",
                    "predicate": "all",
                    "consequence": [lookup],
                },
                "store_as": "_program_reroll",
            },
        ]
    else:
        raw["procedures"] = {
            "select_program": {
                "trigger": "on_program_selected",
                "steps": [lookup],
            }
        }

    errors = validate_ruleset(yaml.safe_dump(raw, sort_keys=False))

    assert any(expected in error for error in errors), errors
