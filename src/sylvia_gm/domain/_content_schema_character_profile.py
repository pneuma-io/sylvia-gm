"""Unbound character source format, distinct from a playable Character File."""

CHARACTER_PROFILE_SCHEMA: dict[str, object] = {
    "$schema": "https://json-schema.org/draft/2020-12/schema",
    "$id": "urn:sylvia-gm:content:character-profile:1",
    "title": "General character profile v1",
    "type": "object",
    "additionalProperties": False,
    "required": ["format_id", "format_version", "name", "summary", "authored_profile"],
    "properties": {
        "format_id": {"const": "sylvia-gm-character-profile"},
        "format_version": {"type": "integer", "const": 1},
        "name": {"$ref": "#/$defs/text", "maxLength": 160},
        "summary": {"$ref": "#/$defs/text", "maxLength": 500},
        "authored_profile": {"$ref": "#/$defs/text"},
    },
    "$defs": {
        "text": {
            "type": "string",
            "minLength": 1,
            "pattern": r"^(?![\s\S]*\u0000)\S(?:[\s\S]*\S)?$(?![\s\S])",
        }
    },
}

EXAMPLE: dict[str, object] = {
    "format_id": "sylvia-gm-character-profile",
    "format_version": 1,
    "name": "Vela",
    "summary": "An inquisitive travelling archivist.",
    "authored_profile": (
        "# Vela\n\nA loyal archivist with a dry wit and a curiosity for lost stories."
    ),
}

FORMAT_NOTES = (
    "An unbound authored source, not a playable Character File. No Setting, Ruleset, schema, "
    "participant, state or party-role fields are permitted. Original-world lore is free prose.",
    "All text is nonblank and unpadded. Markdown is allowed; no biography headings are required. "
    "JSON members must be unique, nesting at most 32, and source/canonical UTF-8 at most 256 KiB.",
    "Builder checks and publishes workspace files. Saved sources are immutable; editing creates "
    "a separate saved entry. Adaptations require the target's normal character checks.",
)
