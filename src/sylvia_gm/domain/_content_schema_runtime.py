"""Retrievable definitions for runtime YAML and its authored schema subset."""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sylvia_gm.domain._runtime_executable_reference import RUNTIME_EXECUTABLE_DEFINITION
from sylvia_gm.ruleset.schema import RulesetFile
from sylvia_gm.runtime._parameter_declarations import PARAMETER_DESCRIPTOR_SCHEMA

_SCHEMA_FIELDS = {
    "$id": {"type": "string", "minLength": 1},
    "description": {"type": "string"},
    "type": {"oneOf": [
        {"enum": ["string", "integer", "number", "boolean", "object", "array", "null"]},
        {"type": "array", "minItems": 2, "maxItems": 2, "uniqueItems": True,
         "items": {"enum": ["string", "integer", "number", "boolean", "object", "array", "null"]},
         "contains": {"const": "null"}},
    ]},
    "properties": {"type": "object", "additionalProperties": {"$ref": "#/$defs/schema"}},
    "required": {"type": "array", "items": {"type": "string"}, "uniqueItems": True},
    "additionalProperties": {"oneOf": [{"type": "boolean"}, {"$ref": "#/$defs/schema"}]},
    "items": {"$ref": "#/$defs/schema"},
    "enum": {"type": "array"},
    "minimum": {"type": "number"},
    "maximum": {"type": "number"},
    "minLength": {"type": "integer", "minimum": 0},
    "maxLength": {"type": "integer", "minimum": 0},
    "minItems": {"type": "integer", "minimum": 0},
    "maxItems": {"type": "integer", "minimum": 0},
    "uniqueItems": {"type": "boolean"},
    "allOf": {"type": "array", "minItems": 1, "maxItems": 256,
              "items": {"$ref": "#/$defs/schema"}},
    "if": {"$ref": "#/$defs/schema"},
    "then": {"$ref": "#/$defs/schema"},
    "contains": {"$ref": "#/$defs/schema"},
    "minContains": {"type": "integer", "minimum": 0},
    "x-sylvia-gm-reference": {"type": "string"},
    "x-sylvia-gm-derived": {"type": "boolean"},
    "x-sylvia-gm-model-hidden": {"type": "boolean"},
    "x-sylvia-gm-model-required": {"type": "boolean"},
}

SCHEMA_ADMISSION_SUBSET_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://pneuma.io/sylvia-gm/admitted-json-schema-subset.schema.json",
    "title": "Sylvia GM admitted JSON-schema subset",
    "$ref": "#/$defs/schema",
    "$defs": {
        "schema": {
            "type": "object",
            "required": ["type"],
            "properties": _SCHEMA_FIELDS,
            "additionalProperties": False,
            "dependentRequired": {"if": ["then"], "then": ["if"],
                                  "minContains": ["contains"]},
            "allOf": [
                {"if": {"required": ["contains"]},
                 "then": {"properties": {"type": {
                     "enum": ["array", ["array", "null"], ["null", "array"]],
                 }}}},
                {
                    "if": {"properties": {"type": {"const": "object"}}, "required": ["type"]},
                    "then": {
                        "properties": {
                            "properties": {"type": "object"},
                            "required": {"type": "array"},
                        }
                    },
                },
                {
                    "if": {"properties": {"type": {
                        "enum": ["array", ["array", "null"], ["null", "array"]],
                    }}, "required": ["type"]},
                    "then": {"required": ["items"]},
                },
            ],
        }
    },
}

_MODEL_SCHEMA = RulesetFile.model_json_schema()
_CONFIG_PROPERTIES = deepcopy(_MODEL_SCHEMA["properties"])
for _name in ("meta", "tools", "procedures", "definitions", "definition_row_schemas", "narrative"):
    _CONFIG_PROPERTIES.pop(_name, None)

_PARAMETER = PARAMETER_DESCRIPTOR_SCHEMA

_RANGE = {
    "type": "object",
    "required": ["type", "value"],
    "properties": {
        "type": {"enum": ["band", "grid"]},
        "value": {
            "oneOf": [{"type": "string", "minLength": 1}, {"type": "number"}],
        },
    },
    "additionalProperties": False,
}
_TOOL = {
    "type": "object",
    "properties": {
        "description": {"type": "string"},
        "parameters": {"type": "array", "items": {"oneOf": [
            _PARAMETER, {"type": "string", "pattern": "^[a-z][a-z0-9_]*$"},
        ]}},
        "pre_conditions": {
            "type": "array",
            "items": {
                "type": "object",
                "required": ["condition"],
                "properties": {"condition": {"type": "string"}, "error": {"type": "string"}},
                "additionalProperties": False,
            },
        },
        "effects": {"type": "array", "items": {"type": "object", "minProperties": 1}},
        "metadata": {
            "type": "object",
            "additionalProperties": True,
            "description": "Metadata extension area; known consumers validate their own keys.",
            "properties": {"activity_label": {"oneOf": [
                {"type": "string", "minLength": 1, "maxLength": 80},
                {"type": "object", "required": ["path"], "additionalProperties": False,
                 "properties": {"path": {"type": "string", "minLength": 1, "maxLength": 256}}},
            ], "description": (
                "Neutral routine label, saved with the result. A public path may resolve "
                "to null when an outcome needs its own result card. Maximum 80 characters."
            )}},
        },
        "narrative": {"type": "object", "additionalProperties": True},
        "range": _RANGE,
    },
    "additionalProperties": False,
}
_PROCEDURE = {
    "type": "object",
    "required": ["trigger"],
    "properties": {
        "trigger": {"type": "string", "pattern": "^on_[a-z][a-z0-9_]*$"},
        "description": {"type": "string"},
        "condition": {"type": "string"},
        "steps": {"type": "array", "items": {"type": "object", "minProperties": 1}},
        "narrative": {"type": "object", "additionalProperties": True},
    },
    "additionalProperties": False,
}

PACKAGE_RUNTIME_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://pneuma.io/sylvia-gm/package-runtime-v1.schema.json",
    "title": "Sylvia GM package runtime YAML, format 1",
    "$defs": deepcopy(_MODEL_SCHEMA.get("$defs", {})),
    "type": "object",
    "required": ["sylvia_gm_runtime"],
    "properties": {
        **_CONFIG_PROPERTIES,
        "sylvia_gm_runtime": {
            "type": "object",
            "required": ["format", "state_namespace"],
            "properties": {
                "format": {"type": "integer", "const": 1},
                "state_namespace": {"type": "string", "minLength": 1},
            },
            "additionalProperties": False,
        },
        "tools": {"type": "object", "additionalProperties": _TOOL},
        "parameter_declarations": {
            "type": "object",
            "propertyNames": {"pattern": "^[a-z][a-z0-9_]*$"},
            "additionalProperties": _PARAMETER,
            "description": (
                "Package-local complete literal descriptors referenced by ID "
                "only in tool parameter lists."
            ),
        },
        "procedures": {"type": "object", "additionalProperties": _PROCEDURE},
        "definitions": {
            "oneOf": [{"type": "null"}, {"type": "object", "additionalProperties": True}]
        },
        "definition_row_schemas": {"type": "object", "additionalProperties": True},
        "narrative": {
            "oneOf": [{"type": "null"}, {"type": "object", "additionalProperties": True}]
        },
        "model_decision_boundary": {"type": "object", "additionalProperties": True,
            "description": "Actor control may declare preparation {queue_field,intent,actor_parameter,prompt}; queued character events precede play and use the ordinary mechanics/choice path."},
        "model_decision_scope": {"type": "object", "additionalProperties": True},
    },
    "additionalProperties": False,
}

RUNTIME_PROCEDURAL_DEFINITION = {
    "executable": RUNTIME_EXECUTABLE_DEFINITION,
    "parser": "application.package_runtime.validate_package_runtime",
    "configuration_owner": "ruleset.schema.RulesetFile",
    "executable_owner": "runtime._schema_validator.validate_ruleset",
    "execution_loader": "runtime._runtime.RulesetRuntime",
    "expression_owner": "runtime._expression_parser.parse_expression",
    "effect_owner": "runtime._effect_executor._EFFECT_HANDLERS",
    "limits": {"bytes": 16 * 1024 * 1024, "depth": 64, "nodes": 500_000, "object_members": 25_000},
    "required_descriptor_fields": ["format", "state_namespace"],
    "parameter_types": [
        "array",
        "boolean",
        "entity_id",
        "entity_id_list",
        "enum",
        "integer",
        "number",
        "string",
    ],
    "definition_schema_fields": [
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
    ],
    "definition_variant_fields": ["discriminator", "variants"],
    "validation": [
        "UTF-8 safe YAML and bounded finite JSON-compatible values",
        "descriptor format and exact manifest package state namespace",
        "Pydantic configuration fields and cross-references",
        "tool parameters, metadata, effects, expressions and nested calls",
        "procedures, triggers, definition rows/lookups and narrative shape",
        "runtime loader, trigger links, static call graph and manifest tool-schema agreement",
    ],
}

RUNTIME_EXAMPLE = {
    "sylvia_gm_runtime": {"format": 1, "state_namespace": "example.coin-rules"},
    "tools": {},
    "procedures": {},
    "definition_row_schemas": {},
    "definitions": {},
    "narrative": {},
}

RUNTIME_NOTES = (
    (
        "Expressions allow at most 500 characters, with string literals of at most 200 "
        "characters. Player-choice prompts allow at most 4096 UTF-8 bytes and option labels "
        "at most 1024 UTF-8 bytes, including after interpolation."
    ),
    (
        "runtime/ruleset.yaml is compiler output. Authors normally edit "
        "ruleset-source.yaml and let the existing materializer expand mechanic "
        "families and derive the runtime."
    ),
    (
        "The structural schema identifies the accepted sections; the procedural "
        "definition names every live validator that owns the executable grammar. "
        "Final admission must call validate_package_runtime."
    ),
    (
        "Tools and procedures are mappings keyed by authored identities. Their "
        "bodies are not generic extensions: _schema_validator checks supported "
        "fields, effects, expressions, references, metadata, procedures and calls."
    ),
    (
        "Configuration maps explicitly preserve compatibility-specific extensions "
        "only where RulesetFile declares them. Runtime admission rejects unknown "
        "root and known-record fields."
    ),
    (
        "This definition grants no mechanics sandbox or arbitrary code execution. "
        "Only the listed effects and expression functions are available; it supplies "
        "no ruleset-specific defaults."
    ),
)

SCHEMA_SUBSET_EXAMPLE = {
    "type": "object",
    "properties": {
        "name": {"type": "string", "minLength": 1, "description": "The character's chosen name."}
    },
    "required": ["name"],
    "additionalProperties": False,
}

SCHEMA_SUBSET_NOTES = (
    "Descriptions explain authored fields; they are guidance, not executable validation rules.",
    "allOf combines 1–256 typed schemas. if/then must be paired. Array contains requires at "
    "least minContains matching items (one by default). These can express build prerequisites "
    "without executable package code; every nested schema still requires an explicit type.",
    (
        "This definition mirrors application/schema_admission.py. Contextual owners "
        "may impose tighter rules, including an object root, explicit extension "
        "policy, or matching $id."
    ),
    (
        "The runtime definition-row subset is smaller: additionalProperties is "
        "boolean-only and annotations plus array-size constraints are unavailable "
        "there."
    ),
    (
        "Schema validation is deterministic and data-only; it provides no arbitrary "
        "code or formula execution."
    ),
)

__all__ = [
    "SCHEMA_ADMISSION_SUBSET_SCHEMA",
    "PACKAGE_RUNTIME_SCHEMA",
    "RUNTIME_PROCEDURAL_DEFINITION",
    "RUNTIME_EXAMPLE",
    "RUNTIME_NOTES",
    "SCHEMA_SUBSET_EXAMPLE",
    "SCHEMA_SUBSET_NOTES",
]
