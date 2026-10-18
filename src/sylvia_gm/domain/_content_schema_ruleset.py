"""Authoring definition for compact Ruleset source before family expansion."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sylvia_gm.domain._content_schema_package import PACKAGE_RECORD_SCHEMA
from sylvia_gm.domain._content_schema_panel import PANEL_DECLARATION_SCHEMA
from sylvia_gm.domain._content_schema_ruleset_tests import RULESET_TESTS_SCHEMA
from sylvia_gm.domain._content_schema_ui import PANEL_LAYOUT_SCHEMA

TEMPLATE = {"type": "string", "pattern": r"^\{[^{}]+\}$(?![\s\S])"}
IDENTIFIER = {"type": "string", "minLength": 1}
PARAM_TYPES = [
    "entity_id",
    "string",
    "integer",
    "number",
    "boolean",
    "array",
    "enum",
    "entity_id_list",
]
EFFECT_FIELDS = [
    "condition",
    "on_true",
    "on_false",
    "store_as",
    "dice",
    "set",
    "request_choice",
    "apply_damage",
    "apply_condition",
    "remove_condition",
    "heal",
    "decrement",
    "increment",
    "modify_field",
    "conditional",
    "nested_call",
    "trigger_procedure",
    "save_throw",
    "log",
    "table_lookup",
    "spatial_query",
    "shot_effect",
    "spatial_mutation",
    "for_each",
    "sort",
    "apply_ability_damage",
    "count_successes",
    "reroll",
    "hunger_dice",
    "set_flag",
    "advance_time",
    "list_push",
    "list_pop",
    "initiative",
    "advance_initiative",
    "random_table",
    "transition_scene",
    "aoe",
    "defer",
    "narrate",
    "register_initiative_trigger",
    "clock_create",
    "clock_tick",
]


def tokenized(schema: dict[str, Any]) -> dict[str, Any]:
    """Allow a concrete value or one whole family-configuration token."""
    return {"anyOf": [schema, {"$ref": "#/$defs/template"}]}


RULESET_SOURCE_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:sylvia-gm:content:ruleset-source:1",
    "title": "Sylvia GM compact Ruleset source, format 1",
    "type": "object",
    "additionalProperties": False,
    "required": [
        "format",
        "id",
        "version",
        "label",
        "publisher",
        "mechanic_families",
        "public_actions",
        "private_mechanics",
        "definitions",
        "participant_artifacts",
        "operating_guide",
    ],
    "properties": {
        "format": {"type": "integer", "const": 1},
        "id": {"$ref": "#/$defs/nonempty"},
        "version": {"$ref": "#/$defs/nonempty"},
        "label": {"$ref": "#/$defs/nonempty"},
        "publisher": {"$ref": "#/$defs/nonempty"},
        "mechanic_families": {
            "type": "object",
            "propertyNames": {"$ref": "#/$defs/identifier"},
            "additionalProperties": {"$ref": "#/$defs/family"},
        },
        "public_actions": {"type": "array", "items": {"$ref": "#/$defs/public_action"}},
        "private_mechanics": {"type": "array", "items": {"$ref": "#/$defs/private_action"}},
        "definitions": {
            "type": "object",
            "description": "Open Ruleset-owned runtime data.",
            "additionalProperties": True,
        },
        "participant_artifacts": {
            "type": "array",
            "description": "Declared participant formats.",
            "items": {"$ref": "#/$defs/participant_artifact"},
        },
        "state_schemas": deepcopy(PACKAGE_RECORD_SCHEMA["properties"]["state_schemas"]),
        "operating_guide": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/nonempty"}]},
        "ui_surfaces": {
            "type": "array",
            "description": "Panels declared by this Ruleset.",
            "items": {"$ref": "urn:sylvia-gm:content:panel-declaration:1"},
        },
        "panel_layout": {"$ref": "urn:sylvia-gm:content:panel-layout:1"},
        "tests": {"$ref": "urn:sylvia-gm:content:ruleset-tests:1"},
    },
    "$defs": {
        "template": deepcopy(TEMPLATE),
        "identifier": deepcopy(IDENTIFIER),
        "nonempty": {"type": "string", "minLength": 1, "pattern": r".*\S.*"},
        "panel_layout_resource": deepcopy(PANEL_LAYOUT_SCHEMA),
        "ruleset_tests_resource": deepcopy(RULESET_TESTS_SCHEMA),
        "panel_declaration_resource": deepcopy(PANEL_DECLARATION_SCHEMA),
        "family": {
            "type": "object",
            "additionalProperties": False,
            "required": ["parameters", "pre_conditions", "effects", "narrative"],
            "properties": {
                "parameters": tokenized(
                    {"type": "array", "items": tokenized({"$ref": "#/$defs/parameter"})}
                ),
                "pre_conditions": tokenized(
                    {
                        "type": "array",
                        "items": tokenized({"type": "object", "additionalProperties": True}),
                    }
                ),
                "effects": tokenized(
                    {"type": "array", "items": tokenized({"$ref": "#/$defs/effect"})}
                ),
                "narrative": tokenized({"type": "object", "additionalProperties": True}),
            },
        },
        "parameter": {
            "type": "object",
            "additionalProperties": False,
            "required": ["name", "type", "source"],
            "properties": {
                "name": deepcopy(IDENTIFIER),
                "type": tokenized({"enum": PARAM_TYPES}),
                "source": tokenized({"enum": ["choice", "gm_judgment", "state"]}),
                "required": tokenized({"type": "boolean"}),
                "description": {"type": "string"},
                "enum_values": tokenized(
                    {
                        "type": "array",
                        "minItems": 1,
                        "uniqueItems": True,
                        "items": {"type": "string"},
                    }
                ),
                "minimum": tokenized({"type": "number"}),
                "maximum": tokenized({"type": "number"}),
                "min_items": tokenized({"type": "integer", "minimum": 0}),
                "max_items": tokenized({"type": "integer", "minimum": 0}),
                "unique_items": tokenized({"type": "boolean"}),
                "items": tokenized(
                    {
                        "type": "object",
                        "description": "Open runtime item schema.",
                        "additionalProperties": True,
                    }
                ),
                "resolve": tokenized({"enum": ["entity", "participant", "entity_list"]}),
                "from_state": {"type": "string", "minLength": 1},
                "default": {"description": "Open JSON-compatible Ruleset value."},
            },
            "allOf": [
                {
                    "if": {"properties": {"source": {"const": "state"}}, "required": ["source"]},
                    "then": {"required": ["from_state"], "not": {"required": ["default"]}},
                },
                {
                    "if": {
                        "properties": {"source": {"enum": ["choice", "gm_judgment"]}},
                        "required": ["source"],
                    },
                    "then": {"not": {"required": ["from_state"]}},
                },
                {
                    "if": {"properties": {"type": {"const": "enum"}}, "required": ["type"]},
                    "then": {"required": ["enum_values"]},
                },
                {
                    "if": {"properties": {"type": {"const": "array"}}, "required": ["type"]},
                    "then": {"required": ["items"]},
                },
                {
                    "if": {
                        "properties": {"type": {"const": "entity_id_list"}},
                        "required": ["type"],
                    },
                    "then": {
                        "required": ["resolve"],
                        "properties": {"resolve": tokenized({"const": "entity_list"})},
                    },
                },
            ],
        },
        "effect": {
            "type": "object",
            "minProperties": 1,
            "description": (
                "Open pre-expansion handler bodies; runtime validation owns their grammar."
            ),
            "propertyNames": {"enum": EFFECT_FIELDS},
            "additionalProperties": True,
        },
        "action_base": {
            "type": "object",
            "required": ["name", "family", "description", "configure"],
            "properties": {
                "name": {"$ref": "#/$defs/identifier"},
                "family": {"$ref": "#/$defs/identifier"},
                "description": {"type": "string"},
                "configure": {"type": "object", "additionalProperties": True},
            },
        },
        "private_action": {
            "allOf": [
                {"$ref": "#/$defs/action_base"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "properties": {
                        "name": True,
                        "family": True,
                        "description": True,
                        "configure": True,
                    },
                },
            ]
        },
        "public_action": {
            "allOf": [
                {"$ref": "#/$defs/action_base"},
                {
                    "type": "object",
                    "additionalProperties": False,
                    "required": [
                        "capability",
                        "state_predicates",
                        "turn_statuses",
                        "writers",
                        "reference_availability",
                        "gameplay_resolution",
                        "deterministic_part",
                        "gm_part",
                    ],
                    "properties": {
                        "name": True,
                        "family": True,
                        "description": True,
                        "configure": True,
                        "capability": {"$ref": "#/$defs/nonempty"},
                        "state_predicates": {
                            "type": "array",
                            "items": {"type": "object", "additionalProperties": True},
                        },
                        "turn_statuses": {"type": "array", "items": {"type": "string"}},
                        "writers": {"type": "array", "items": {"type": "string"}},
                        "reference_availability": {"enum": ["included", "excluded"]},
                        "gameplay_resolution": {
                            "enum": [
                                "engine_family",
                                "engine_specialized",
                                "hybrid",
                                "gm_adjudicated",
                                "unsupported",
                            ]
                        },
                        "deterministic_part": {
                            "oneOf": [{"type": "null"}, {"$ref": "#/$defs/nonempty"}]
                        },
                        "gm_part": {"oneOf": [{"type": "null"}, {"$ref": "#/$defs/nonempty"}]},
                    },
                    "allOf": [
                        {
                            "if": {"properties": {"gameplay_resolution": {"const": "hybrid"}}},
                            "then": {
                                "properties": {
                                    "deterministic_part": {"$ref": "#/$defs/nonempty"},
                                    "gm_part": {"$ref": "#/$defs/nonempty"},
                                }
                            },
                            "else": {
                                "properties": {
                                    "deterministic_part": {"type": "null"},
                                    "gm_part": {"type": "null"},
                                }
                            },
                        },
                        {
                            "if": {"properties": {"reference_availability": {"const": "excluded"}}},
                            "then": {
                                "properties": {"gameplay_resolution": {"const": "unsupported"}}
                            },
                        },
                    ],
                },
            ]
        },
        "participant_artifact": {
            "type": "object",
            "required": ["kind", "schema_id", "schema"],
            "properties": {
                "kind": {"$ref": "#/$defs/nonempty"},
                "schema_id": {"$ref": "#/$defs/nonempty"},
                "schema": {
                    "type": "object",
                    "additionalProperties": True,
                    "description": (
                        "The supported participant schema subset; its $id must equal schema_id."
                    ),
                },
                "entity_state_schema": {
                    "type": "object",
                    "description": (
                        "Complete starting entity state using the supported schema subset."
                    ),
                },
                "initial_state_calculations": {
                    "type": "array",
                    "maxItems": 128,
                    "items": {"$ref": "#/$defs/initial_state_calculation"},
                    "description": (
                        "Ordered Ruleset calculations; authored inputs cannot set their outputs."
                    ),
                },
                "initial_state_validation": {
                    "type": "object", "required": ["description", "definitions", "effects"],
                    "properties": {
                        "description": {"type": "string", "minLength": 1, "maxLength": 2000},
                        "definitions": {"type": "object"},
                        "effects": {"type": "array", "minItems": 1, "maxItems": 1024,
                                    "items": {"type": "object"}},
                    },
                    "additionalProperties": False,
                    "description": (
                        "Read-only character requirements using bounded runtime effects."
                    ),
                },
            },
            "additionalProperties": False,
            "dependentRequired": {
                "entity_state_schema": ["initial_state_calculations"],
                "initial_state_calculations": ["entity_state_schema"],
                "initial_state_validation": ["entity_state_schema", "initial_state_calculations"],
            },
        },
        "initial_state_calculation": {
            "type": "object",
            "required": ["set"],
            "additionalProperties": False,
            "properties": {
                "condition": {"type": "string", "minLength": 1, "maxLength": 500},
                "set": {
                    "type": "object",
                    "required": ["field", "value"],
                    "additionalProperties": False,
                    "properties": {
                        "field": {
                            "type": "string",
                            "pattern": (
                                r"^[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*$(?![\s\S])"
                            ),
                            "description": (
                                "An explicitly declared field within entity state."
                            ),
                        },
                        "value": {"type": "string", "minLength": 1, "maxLength": 500},
                    },
                },
            },
        },
    },
}

EXAMPLE: dict[str, Any] = {
    "format": 1,
    "id": "example.coin-rules",
    "version": "1.0.0",
    "label": "Coin Rules",
    "publisher": "Example author",
    "mechanic_families": {
        "flip": {
            "parameters": [],
            "pre_conditions": [],
            "effects": [{"dice": {"expression": "1d2"}, "store_as": "_result"}],
            "narrative": {},
        }
    },
    "public_actions": [
        {
            "name": "flip_coin",
            "family": "flip",
            "description": "Flip a coin.",
            "configure": {},
            "capability": "example.coin-rules.flip",
            "state_predicates": [],
            "turn_statuses": ["open"],
            "writers": ["dm"],
            "reference_availability": "included",
            "gameplay_resolution": "engine_family",
            "deterministic_part": None,
            "gm_part": None,
        }
    ],
    "private_mechanics": [],
    "definitions": {},
    "participant_artifacts": [],
    "operating_guide": None,
}

FORMAT_NOTES = (
    (
        "Expressions allow at most 500 characters; string literals at most 200 characters. "
        "Player-choice prompts allow at most 4096 UTF-8 bytes and option labels at most "
        "1024 UTF-8 bytes, including after interpolation."
    ),
    (
        "Source mappings require unique text field names and finite JSON-compatible "
        "values. Maximum nesting is 64 levels and 250,000 value nodes; cyclic aliases "
        "reject."
    ),
    (
        "After family expansion, the shared runtime validator owns parameters, "
        "preconditions, effects, expressions, references, and execution bounds."
    ),
    "Family objects have exactly four fields, even when no operation references the family.",
    "Family references and operation-name uniqueness require procedural checks.",
    (
        "Unused configure keys and every unresolved token are not separately rejected by "
        "the current compiler."
    ),
    (
        "Participant schemas use the package admission subset and their $id must match "
        "schema_id. The materializer derives schema_sha256; do not author it."
    ),
    (
        "Panel depth, node count, references, placement support, and cross-tree uniqueness"
        " remain procedural checks."
    ),
)

__all__ = ["RULESET_SOURCE_SCHEMA", "EXAMPLE", "FORMAT_NOTES"]
