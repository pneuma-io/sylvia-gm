"""Authoring shapes for package-owned Interview Contributions and panel layouts."""

from __future__ import annotations

from typing import Any


def closed_object(
    properties: dict[str, Any], *, required: list[str] | None = None
) -> dict[str, Any]:
    """Declare an exact record, requiring every field unless an explicit subset is supplied."""

    return {
        "type": "object",
        "properties": properties,
        "required": list(properties) if required is None else required,
        "additionalProperties": False,
    }


def trimmed_text(maximum: int) -> dict[str, Any]:
    """Describe nonempty, trimmed text without a NUL character."""

    return {
        "type": "string",
        "minLength": 1,
        "maxLength": maximum,
        "pattern": r"^\S(?:[^\u0000]*\S)?$(?![\s\S])",
        "not": {"pattern": r"\u0000"},
    }


_QUESTION_ID = {
    "type": "string",
    "minLength": 1,
    "maxLength": 96,
    "pattern": r"^[A-Za-z0-9][A-Za-z0-9._:-]*$(?![\s\S])",
}
_LAYOUT_ID = {"type": "string", "pattern": r"^[A-Za-z0-9_-]{1,96}$(?![\s\S])"}
_PANEL_ID = {"type": "string", "minLength": 1, "description": "An exact declared panel ID."}

INTERVIEW_CONTRIBUTION_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:sylvia-gm:content:interview-contribution:1",
    "title": "Interview Contribution, version 1",
    "description": "Optional data-only questions owned by a Ruleset, Setting or Plan.",
    **closed_object(
        {
            "format_id": {"const": "sylvia-gm-interview-contribution"},
            "format_version": {"type": "integer", "const": 1},
            "owner_kind": {"enum": ["ruleset", "campaign_setting", "campaign_plan"]},
            "id": _QUESTION_ID,
            "title": trimmed_text(160),
            "questions": {
                "type": "array",
                "minItems": 1,
                "maxItems": 32,
                "items": {"$ref": "#/$defs/question"},
                "description": (
                    "Ordered questions with unique IDs. Conditions refer to earlier IDs."
                ),
            },
        }
    ),
    "$defs": {
        "option": closed_object({"id": _QUESTION_ID, "label": trimmed_text(160)}),
        "condition": closed_object(
            {
                "question_id": _QUESTION_ID,
                "equals": {"type": ["string", "boolean", "integer"]},
            }
        ),
        "question": {
            **closed_object(
                {
                    "id": _QUESTION_ID,
                    "prompt": trimmed_text(500),
                    "type": {"enum": ["choice", "free_text"]},
                    "required": {"type": "boolean"},
                    "adaptive": {
                        "type": "boolean",
                        "description": "Whether interpreting the answer needs Builder assistance.",
                    },
                    "options": {"type": "array", "items": {"$ref": "#/$defs/option"}},
                    "condition": {"anyOf": [{"type": "null"}, {"$ref": "#/$defs/condition"}]},
                }
            ),
            "allOf": [
                {
                    "if": {"properties": {"type": {"const": "choice"}}},
                    "then": {"properties": {"options": {"minItems": 2, "maxItems": 16}}},
                    "else": {"properties": {"options": {"maxItems": 0}}},
                }
            ],
        },
    },
}

INTERVIEW_CONTRIBUTION_EXAMPLE = {
    "format_id": "sylvia-gm-interview-contribution",
    "format_version": 1,
    "owner_kind": "campaign_setting",
    "id": "journey-tone",
    "title": "Your journey",
    "questions": [
        {
            "id": "mood",
            "prompt": "What mood would you like?",
            "type": "choice",
            "required": True,
            "adaptive": False,
            "options": [{"id": "hopeful", "label": "Hopeful"}, {"id": "tense", "label": "Tense"}],
            "condition": None,
        }
    ],
}

INTERVIEW_CONTRIBUTION_NOTES = (
    (
        "Save as interview-contribution.json in its owning package or Plan. Maximum UTF-8 "
        "size: 32 KiB."
    ),
    "The declared owner_kind must match the actual owner. IDs are unique within their group.",
    (
        "A condition references only an earlier question. Free-text questions have an "
        "empty options list."
    ),
    (
        "Content supplies questions, not executable code, tools, system policy or "
        "cross-artifact writes."
    ),
)

PANEL_LAYOUT_SCHEMA = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:sylvia-gm:content:panel-layout:1",
    "title": "Ruleset starting panel layout, version 1",
    "description": "The package's starting arrangement; user-saved view preferences are separate.",
    **closed_object(
        {
            "version": {"type": "integer", "const": 1},
            "sidebar": {"anyOf": [{"type": "null"}, {"$ref": "#/$defs/tree"}]},
            "windows": {"type": "array", "maxItems": 64, "items": {"$ref": "#/$defs/window"}},
            "hidden": {"type": "array", "maxItems": 64, "uniqueItems": True, "items": _PANEL_ID},
        }
    ),
    "$defs": {
        "tree": {"oneOf": [{"$ref": "#/$defs/tabs"}, {"$ref": "#/$defs/split"}]},
        "tabs": closed_object(
            {
                "kind": {"const": "tabs"},
                "id": _LAYOUT_ID,
                "panels": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 64,
                    "uniqueItems": True,
                    "items": _PANEL_ID,
                },
                "active": {**_PANEL_ID, "description": "One member of this group's panels list."},
            }
        ),
        "split": closed_object(
            {
                "kind": {"const": "split"},
                "id": _LAYOUT_ID,
                "axis": {"enum": ["row", "column"]},
                "ratio": {"type": "number", "minimum": 0.1, "maximum": 0.9},
                "first": {"$ref": "#/$defs/tree"},
                "second": {"$ref": "#/$defs/tree"},
            }
        ),
        "window": closed_object(
            {
                "id": {**_LAYOUT_ID, "not": {"const": "sidebar"}},
                "title": {
                    **trimmed_text(256),
                    "description": "Trimmed text, at most 256 UTF-8 bytes.",
                },
                "tree": {"$ref": "#/$defs/tree"},
            }
        ),
    },
}

PANEL_LAYOUT_EXAMPLE = {
    "version": 1,
    "sidebar": {"kind": "tabs", "id": "main", "panels": ["party"], "active": "party"},
    "windows": [],
    "hidden": [],
}

PANEL_LAYOUT_NOTES = (
    (
        "Save as ui/panel-layout.json. Maximum UTF-8 size: 64 KiB; at most 256 tree nodes "
        "and depth 16."
    ),
    (
        "Every referenced panel must be declared by this Ruleset and appear at most once "
        "in the layout."
    ),
    "Group IDs are unique across all trees; window IDs are unique and cannot be sidebar.",
    (
        "A sidebar panel must permit docked placement; an extra-window panel must permit "
        "window placement."
    ),
    "The active panel belongs to its group. Split children and window trees cannot be null.",
    "The example needs a panel named party. Omitted declared panels appear in the sidebar.",
)
