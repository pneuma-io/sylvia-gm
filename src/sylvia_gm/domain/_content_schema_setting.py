"""Author-facing definition for Setting-owned character information."""

from __future__ import annotations

from typing import Any

SETTING_CHARACTER_REQUIREMENTS_SCHEMA: dict[str, Any] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "https://pneuma.io/sylvia-gm/setting-character-requirements-v1.schema.json",
    "title": "Campaign Setting character requirements, version 1",
    "type": "object",
    "required": ["format_id", "format_version", "schema"],
    "properties": {
        "format_id": {"const": "sylvia-gm-setting-character-requirements"},
        "format_version": {"type": "integer", "const": 1},
        "schema": {
            "type": "object",
            "description": (
                "A root object schema using Sylvia GM's admitted JSON-schema subset. "
                "Every object schema must declare additionalProperties explicitly."
            ),
        },
    },
    "additionalProperties": False,
}

EXAMPLE: dict[str, Any] = {
    "format_id": "sylvia-gm-setting-character-requirements",
    "format_version": 1,
    "schema": {
        "type": "object",
        "properties": {
            "home_settlement": {"type": "string", "minLength": 1},
            "affiliation": {
                "type": "object",
                "properties": {"name": {"type": "string", "minLength": 1}},
                "required": ["name"],
                "additionalProperties": {
                    "type": "string",
                },
            },
        },
        "required": ["home_settlement"],
        "additionalProperties": False,
    },
}

FORMAT_NOTES = (
    "This file declares world information stored under the Character File's setting_data.",
    (
        "Ruleset participant data, entity state, choices, formulas, and mechanical effects remain "
        "owned by the Setting's contained Ruleset Snapshot."
    ),
    (
        "Object schemas must explicitly set additionalProperties to false, true, or a schema. "
        "Use true or a schema only where the Setting deliberately permits extensions."
    ),
    "The admitted schema subset is data-only and cannot contain calculations or executable code.",
    "Omitting this file is equivalent to requiring an empty setting_data object.",
)

__all__ = ["SETTING_CHARACTER_REQUIREMENTS_SCHEMA", "EXAMPLE", "FORMAT_NOTES"]
