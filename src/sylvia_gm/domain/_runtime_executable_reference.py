"""Agent-readable grammar for the existing declarative runtime executable layer."""

from __future__ import annotations

from sylvia_gm.runtime._authoring_declarations import (
    COMPATIBILITY_EFFECT_HANDLERS,
    EFFECT_BODY_FIELDS,
    NEUTRAL_EFFECT_HANDLERS,
)
from sylvia_gm.runtime._expression_parser import (
    _FUNCTION_WHITELIST,
    MAX_EXPRESSION_LENGTH,
    MAX_NESTING_DEPTH,
    MAX_STRING_LENGTH,
    TokenType,
)
from sylvia_gm.runtime._schema_validator import VALID_PARAM_TYPES, VALID_TRIGGERS

EXPRESSION_GRAMMAR = {
    "literals": ["number", "single-quoted string", "double-quoted string", "true", "false", "null"],
    "field_access": "identifier followed by .field or [expression] segments",
    "unary_operators": ["-", "not"],
    "binary_operators": [
        member.value
        for member in (
            TokenType.OR,
            TokenType.AND,
            TokenType.EQ_EQ,
            TokenType.NOT_EQ,
            TokenType.GT,
            TokenType.GE,
            TokenType.LT,
            TokenType.LE,
            TokenType.IN,
            TokenType.CONTAINS,
            TokenType.PLUS,
            TokenType.MINUS,
            TokenType.STAR,
            TokenType.SLASH_SLASH,
            TokenType.PERCENT,
            TokenType.STAR_STAR,
        )
    ],
    "functions": sorted(_FUNCTION_WHITELIST),
    "maximum_characters": MAX_EXPRESSION_LENGTH,
    "maximum_nesting_depth": MAX_NESTING_DEPTH,
    "maximum_string_characters": MAX_STRING_LENGTH,
    "notes": [
        "There is no attribute or function-call escape beyond this parser.",
        "Available root identifiers depend on the owning tool, calculation, or procedure context.",
        "len(value) counts array elements, object fields or string characters. "
        "It takes one argument.",
    ],
}

PARAMETER_FIELDS = {
    "required": ["name"],
    "optional": [
        "type",
        "description",
        "required",
        "enum_values",
        "minimum",
        "maximum",
        "items",
        "from_state",
        "default",
        "resolve",
        "model_derive",
        "model_hidden",
        "model_required",
        "min_items",
        "max_items",
        "unique_items",
    ],
    "types": sorted(VALID_PARAM_TYPES),
    "declaration_sharing": {
        "pool": "optional top-level parameter_declarations map of complete literal descriptors",
        "entry": "an inline descriptor or a literal package-local snake-case declaration ID",
        "resolution": (
            "one exact lookup per parameter-list entry, preserving order and copying every use"
        ),
        "rejections": (
            "unknown IDs, malformed or unused-invalid descriptors, overrides and chained references"
        ),
        "bounds": (
            "encoded YAML and the fully resolved graph independently "
            "retain their existing shape limits"
        ),
    },
    "conditional": [
        "omitted type defaults to string; explicit types must be valid",
        "enum requires enum_values",
        "array requires an admitted definition items schema",
        "entity_id_list requires resolve: entity_list",
        "from_state starts with state. or combat. and excludes default",
        "object parameters require from_state and are never caller-provided model inputs",
        "list bounds apply only to array and entity_id_list",
    ],
}

EFFECT_ENVELOPE = {
    "handler": "exactly one registered effect key",
    "optional_fields": ["condition", "store_as"],
    "handler_specific_siblings": {
        "call": ["params", "export_context"],
        "trigger": ["context"],
        "flat condition": ["on_true", "on_false"],
    },
    "private_result": "store_as values used for internal results begin with _ where required",
    "call_context_exports": {
        "omitted_or_null": "Do not export the child's context variables.",
        "mapping": "Map private destination names to private child source names; "
        "every named source must exist. Values are deep copies.",
        "wildcard": {
            "value": "*",
            "behavior": "Deep-copy only underscore-prefixed string keys that exist in a "
            "successful child's result context, keeping the same names. Conditional absent "
            "keys are omitted. Public fields, parameters and entity bindings are not exported.",
        },
        "scope": "Exports go into the caller's ephemeral context only. The child receives "
        "only explicit Call params, never the caller's private context. Existing entity and "
        "participant aliases refresh after successful nested execution.",
    },
    "recursive_branches": ["on_true", "on_false", "do", "consequence"],
    "neutral_handlers": list(NEUTRAL_EFFECT_HANDLERS),
    "compatibility_handlers": list(COMPATIBILITY_EFFECT_HANDLERS),
}

PROCEDURE_FIELDS = {
    "required": ["trigger"],
    "optional": ["description", "condition", "steps", "narrative"],
    "triggers": sorted(VALID_TRIGGERS),
    "trigger_extension": "other neutral on_<snake_case> identities are admitted",
}

VALID_EXECUTABLE_EXAMPLE = {
    "tools": {
        "resolve_signal": {
            "description": "Resolve a signal check.",
            "parameters": [
                {"name": "difficulty", "type": "integer", "minimum": 1, "required": True}
            ],
            "pre_conditions": [
                {"condition": "state.ready == true", "error": "The signal is unavailable."}
            ],
            "effects": [
                {"dice": {"expression": "d20"}, "store_as": "_roll"},
                {
                    "condition": "_roll.total >= difficulty",
                    "on_true": [{"set": {"field": "state.success", "value": True}}],
                },
            ],
            "metadata": {"category": "signal", "model_behavior": "optional"},
        }
    },
    "procedures": {},
    "definitions": {},
    "definition_row_schemas": {},
    "narrative": {},
}

INVALID_EXECUTABLE_EXAMPLES = [
    {"problem": "unknown effect handler", "effect": {"execute_python": {"code": "pass"}}},
    {
        "problem": "unsupported expression function",
        "effect": {"condition": "eval('x')", "on_true": []},
    },
    {
        "problem": "two effect handlers",
        "effect": {
            "increment": {"field": "state.x", "amount": 1},
            "decrement": {"field": "state.y", "amount": 1},
        },
    },
]

RUNTIME_EXECUTABLE_DEFINITION = {
    "parameters": PARAMETER_FIELDS,
    "effects": {
        "envelope": EFFECT_ENVELOPE,
        "body_fields": EFFECT_BODY_FIELDS,
        "dice_result": {
            "fields": ["sides", "count", "results", "total", "modifier"],
            "total": "Sum of die faces only. The modifier is stored separately.",
            "effective_result": "Compute _roll.total + _roll.modifier when the rule adds a bonus.",
            "expression": "Use NdS (for example 1d2); supply a flat bonus through modifier.",
            "reporting_example": [
                {"dice": {"expression": "1d2", "modifier": "1"}, "store_as": "_roll"},
                {"set": {"field": "_result", "value": "_roll.total + _roll.modifier"}},
            ],
            "test_example": (
                "For the example above, a controlled face of 2 produces scalar _result = 3. "
                "Assert receipt.result.context._result, not the raw _roll.total. "
                "There is no modified_total field. A missing-path failure identifies an assertion "
                "error, not proof of an arithmetic defect."
            ),
        },
    },
    "expressions": EXPRESSION_GRAMMAR,
    "procedures": PROCEDURE_FIELDS,
    "valid_example": VALID_EXECUTABLE_EXAMPLE,
    "invalid_examples": INVALID_EXECUTABLE_EXAMPLES,
    "extension_areas": [
        "Tool metadata is retained for model behavior, result cards and player controls.",
        "configuration Features and ModeConfig preserve compatibility or mode-specific keys",
        "definition_row_schemas can close and type authored definition table rows.",
    ],
    "validation_scope": [
        "New package admission closes effect fields using the shared authoring shape checker.",
        "Existing installed packages retain their runtime execution behavior.",
        (
            "Conditions and selected declarations are parsed before execution; some effect-value "
            "expressions parse only when the handler executes. Runtime values still need a context."
        ),
        "Use pre_conditions; the runtime does not consume a field named preconditions.",
    ],
}

__all__ = [
    "RUNTIME_EXECUTABLE_DEFINITION",
    "EFFECT_BODY_FIELDS",
    "VALID_EXECUTABLE_EXAMPLE",
    "INVALID_EXECUTABLE_EXAMPLES",
]
