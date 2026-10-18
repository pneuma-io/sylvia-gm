"""Machine-readable definition and neutral example for Character File format version 2."""

from __future__ import annotations

CHARACTER_FILE_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:sylvia-gm:content:character-file:2",
    "title": "Sylvia GM Character File v2",
    "description": (
        "One playable character associated with an exact Campaign Setting and the participant "
        "schema from that Setting's contained Ruleset Snapshot. The root envelope is closed. "
        "The three contextual data objects are checked against those exact declarations."
    ),
    "type": "object",
    "additionalProperties": False,
    "required": [
        "format_id",
        "format_version",
        "id",
        "schema",
        "summary",
        "authored_profile",
        "participant",
        "entity_state",
        "setting_data",
    ],
    "properties": {
        "format_id": {
            "const": "sylvia-gm-character-file",
            "description": "Stable discriminator for the Character File artifact format.",
        },
        "format_version": {
            "type": "integer",
            "const": 2,
            "description": "Current packaged Character File format version.",
        },
        "id": {
            "type": "string",
            "minLength": 1,
            "maxLength": 160,
            "description": (
                "Character identity. It must equal the identity referenced by the Plan's "
                "selected Character File entry."
            ),
        },
        "schema": {
            "type": "object",
            "additionalProperties": False,
            "required": ["id", "content_sha256"],
            "properties": {
                "id": {
                    "$ref": "#/$defs/nonblankString",
                    "description": "Exact participant schema identity from the contained Ruleset.",
                },
                "content_sha256": {
                    "$ref": "#/$defs/sha256",
                    "description": (
                        "Digest binding the exact participant and starting-state declaration."
                    ),
                },
            },
        },
        "summary": {
            "allOf": [{"$ref": "#/$defs/nonblankString"}],
            "maxLength": 500,
            "description": (
                "Short setup-catalog description. It is a selector aid, not a substitute for "
                "the authored character profile."
            ),
        },
        "authored_profile": {
            "$ref": "#/$defs/nonblankString",
            "description": (
                "Complete authored character text. Markdown is allowed. The parser applies the "
                "shared encoded-size limit rather than a prose word limit."
            ),
        },
        "participant": {
            "type": "object",
            "description": (
                "Ruleset-owned starting participant data. Required fields and allowed properties "
                "come from the exact schema binding above."
            ),
        },
        "entity_state": {
            "type": "object",
            "description": (
                "Ruleset-owned initial gameplay state. Its fields and calculated values come from "
                "the exact contained Ruleset declaration."
            ),
        },
        "setting_data": {
            "type": "object",
            "description": (
                "Setting-owned character information. Its fields come from the exact Campaign "
                "Setting's character-requirements declaration; absent requirements allow only "
                "the empty object."
            ),
        },
    },
    "$defs": {
        "nonblankString": {
            "type": "string",
            "minLength": 1,
            "pattern": r"^(?![\s\S]*\u0000)\S(?:[\s\S]*\S)?$(?![\s\S])",
        },
        "sha256": {"type": "string", "pattern": r"^[0-9a-f]{64}$(?![\s\S])"},
    },
}


EXAMPLE: dict[str, object] = {
    "format_id": "sylvia-gm-character-file",
    "format_version": 2,
    "id": "relay-keeper",
    "schema": {
        "id": "example.signal-rules.participant",
        "content_sha256": "1" * 64,
    },
    "summary": "A relay keeper who can join the expedition.",
    "authored_profile": (
        "# Relay Keeper\n\nThe keeper maintains the coast relay and records every unanswered call."
    ),
    "participant": {"name": "Relay Keeper", "rank": 2},
    "entity_state": {"charge": 4},
    "setting_data": {"home": "North Relay"},
}


FORMAT_NOTES: tuple[str, ...] = (
    (
        "The example's participant, entity_state, and setting_data fields are illustrative. A "
        "The example digest is a placeholder. A Character File must use the exact participant "
        "schema, entity-state declaration and "
        "Setting character requirements selected by its contained Ruleset and Setting."
    ),
    (
        "Plan completion or party selection supplies identity and role. id equals that "
        "identity, and the role must be player_character or companion. Role is contextual and is "
        "not a root field in this format."
    ),
    (
        "schema.id and schema.content_sha256 must equal the exact participant declaration in the "
        "Campaign Setting's contained Ruleset Snapshot. The parser also retains the exact Ruleset "
        "and Campaign Setting package bindings on the admitted character."
    ),
    (
        "participant must satisfy that declaration and contain a nonblank, unpadded name of at "
        "most 160 characters. When declared, party_role is set from the parsed role before "
        "participant validation."
    ),
    (
        "entity_state must satisfy the exact entity-state schema and declared starting-state "
        "calculations. setting_data must satisfy the exact Setting character-requirements schema; "
        "without that declaration it must be empty."
    ),
    (
        "The source must be nonempty JSON with unique object members and at most 32 levels of "
        "nesting. The source and canonical admitted Character File are each limited to 256 KiB "
        "when UTF-8 encoded. The authored profile has the same individual byte limit."
    ),
    (
        "Format version 1 remains readable through the older parser path and omits setting_data. "
        "New packaged Character Files use version 2 and include setting_data, including an empty "
        "object when the Setting declares no fields."
    ),
    (
        "The owner-local reusable-participant record is private storage, not an alternative "
        "Character File authoring format. Content definitions are retrieved by agents; this "
        "delivery does not add a schema-browser interface."
    ),
)


__all__ = ["CHARACTER_FILE_SCHEMA", "EXAMPLE", "FORMAT_NOTES"]
