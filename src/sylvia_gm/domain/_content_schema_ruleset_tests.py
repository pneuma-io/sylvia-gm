"""Declarative saved scenarios for testing the ordinary campaign game loop."""

from __future__ import annotations

from typing import Any

RULESET_TESTS_PATH = "tests/scenarios.json"
TEXT = {"type": "string", "minLength": 1}
DATA = {"type": "object", "additionalProperties": True}


def _object(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    """Keep scenario control fields closed while allowing Ruleset-owned data."""
    return {
        "type": "object",
        "additionalProperties": False,
        "properties": properties,
        "required": required,
    }


ASSERTION = _object(
    {
        "subject": {"enum": ["state", "receipt", "control", "available_tools", "tool_results"]},
        "path": {
            "type": "array",
            "maxItems": 32,
            "items": {"oneOf": [TEXT, {"type": "integer", "minimum": 0}]},
        },
        "equals": {"description": "Exact expected JSON value, including false or null."},
    },
    ["subject", "path", "equals"],
)
ASSERTIONS = {"type": "array", "minItems": 1, "maxItems": 256, "items": ASSERTION}
REQUEST = _object(
    {
        "decision_owner": {"enum": ["player", "gm", "none"]},
        "available_tools": {"type": "array", "items": TEXT, "uniqueItems": True},
        "unavailable_tools": {"type": "array", "items": TEXT, "uniqueItems": True},
    },
    [],
)
REPLY = {
    "oneOf": [
        _object(
            {
                "kind": {"const": "tool"},
                "tool": TEXT,
                "arguments": DATA,
                "expect_request": REQUEST,
            },
            ["kind", "tool", "arguments"],
        ),
        _object(
            {
                "kind": {"const": "narration"},
                "text": TEXT,
                "expect_request": REQUEST,
            },
            ["kind", "text"],
        ),
    ],
}
STEP = {
    "oneOf": [
        _object(
            {
                "kind": {"const": "player_action_response"},
                "automatic": {"type": "boolean"},
                "replies": {"type": "array", "minItems": 1, "maxItems": 256, "items": REPLY},
                "expect": ASSERTIONS,
            },
            ["kind", "replies", "expect"],
        ),
        _object(
            {
                "kind": {"const": "gm_turn"},
                "message": TEXT,
                "replies": {"type": "array", "minItems": 1, "maxItems": 256, "items": REPLY},
                "expect": ASSERTIONS,
            },
            ["kind", "message", "replies", "expect"],
        ),
        _object(
            {
                "kind": {"const": "player_action"},
                "source": DATA,
                "selection": DATA,
                "confirmation": TEXT,
                "preview_only": {"type": "boolean"},
                "expect": ASSERTIONS,
            },
            ["kind", "source", "expect"],
        ),
        _object(
            {
                "kind": {"const": "assert"},
                "expect": ASSERTIONS,
            },
            ["kind", "expect"],
        ),
    ],
}
STATE = _object({"namespace": TEXT, "key": TEXT, "value": {}}, ["key", "value"])
PARTICIPANT = _object(
    {
        "id": TEXT,
        "kind": TEXT,
        "schema_id": TEXT,
        "data": DATA,
    },
    ["id", "kind", "schema_id", "data"],
)
SCENARIO = _object(
    {
        "id": TEXT,
        "description": TEXT,
        "covers": {"type": "array", "minItems": 1, "items": TEXT, "uniqueItems": True},
        "initial_state": {"type": "array", "maxItems": 256, "items": STATE},
        "participants": {"type": "array", "maxItems": 256, "items": PARTICIPANT},
        "rolls": {
            "type": "array",
            "maxItems": 4096,
            "items": _object(
                {
                    "sides": {"type": "integer", "minimum": 2},
                    "value": {"type": "integer", "minimum": 1},
                },
                ["sides", "value"],
            ),
        },
        "steps": {"type": "array", "minItems": 1, "maxItems": 256, "items": STEP},
    },
    ["id", "description", "covers", "initial_state", "participants", "rolls", "steps"],
)
RULESET_TESTS_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:sylvia-gm:content:ruleset-tests:1",
    "title": "Sylvia GM saved Ruleset scenarios, format 1",
    **_object(
        {
            "format": {"type": "integer", "const": 1},
            "scenarios": {"type": "array", "minItems": 1, "maxItems": 256, "items": SCENARIO},
        },
        ["format", "scenarios"],
    ),
}
EXAMPLE: dict[str, Any] = {
    "format": 1,
    "scenarios": [
        {
            "id": "coin-heads",
            "description": "A controlled coin flip commits its result.",
            "covers": ["flip_coin"],
            "initial_state": [],
            "participants": [],
            "rolls": [{"sides": 2, "value": 2}],
            "steps": [
                {
                    "kind": "gm_turn",
                    "message": "Flip the coin.",
                    "replies": [
                        {"kind": "tool", "tool": "flip_coin", "arguments": {}},
                        {"kind": "narration", "text": "The coin lands heads."},
                    ],
                    "expect": [
                        {"subject": "receipt", "path": ["status"], "equals": "committed"},
                        {
                            "subject": "receipt",
                            "path": ["result", "context", "_result", "total"],
                            "equals": 2,
                        },
                    ],
                }
            ],
        }
    ],
}
FORMAT_NOTES = (
    "Scenario and initial-state identities must be unique. Rolls cannot exceed their die size.",
    "Setup data and action sources are validated by the ordinary campaign owners at execution.",
    "Omitted state namespaces refer to the tested Ruleset; participant hashes are derived.",
    "Tool names are canonical package tool names, not generated provider aliases.",
    "player_action_response replies to the preceding saved action without inventing player speech.",
    "Covers declares intent; only exercised actions and checked results establish coverage.",
    "Missing paths fail assertions. Unexpected requests or unconsumed scripted replies/rolls fail.",
    "Passing format validation alone does not run scenarios or establish readiness.",
)
