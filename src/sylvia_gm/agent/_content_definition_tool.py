"""Read installed authoring definitions without inspecting user files or changing a session."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sylvia_gm.application.initial_character_state import initial_character_input_schema
from sylvia_gm.domain.authority import SafePayload
from sylvia_gm.domain.content_definitions import get_content_definition, list_content_definitions
from sylvia_gm.domain.package_library import ParticipantArtifactDeclaration, canonical_json_bytes
from sylvia_gm.tools._spec import ToolSpec
from sylvia_gm.tools._toolsets import ToolsetName


def content_definition_tool(
    participant_declarations: tuple[ParticipantArtifactDeclaration, ...] = (),
    setting_character_schema: SafePayload | None = None,
) -> ToolSpec:
    """Offer one on-demand definition or the small catalog of available formats."""

    def read_full(params: dict[str, Any], _context: Any) -> dict[str, Any]:
        if set(params) - {"name"} or ("name" in params and not isinstance(params["name"], str)):
            return {"success": False, "code": "content_definition_parameters_invalid"}
        if "name" not in params:
            catalog = list_content_definitions()
            if participant_declarations:
                catalog.append(
                    {
                        "name": "character_creation",
                        "title": "This Setting's exact character creation requirements",
                    }
                )
            return {"success": True, "definitions": catalog}
        if params["name"] == "character_creation" and participant_declarations:
            declarations = []
            for declaration in participant_declarations:
                complete = declaration.entity_state_schema is not None
                declarations.append(
                    {
                        "kind": declaration.kind,
                        "schema_id": declaration.schema_id,
                        "schema_sha256": declaration.schema_sha256,
                        "participant": declaration.schema,
                        "entity_state_schema": declaration.entity_state_schema,
                        "initial_state_calculations": declaration.initial_state_calculations,
                        **({"validation_requirements": declaration.initial_state_validation[
                            "description"
                        ]} if declaration.initial_state_validation is not None else {}),
                        "creation_available": complete,
                        "authored_entity_state": (
                            initial_character_input_schema(
                                declaration.entity_state_schema,
                                declaration.initial_state_calculations,
                            )
                            if complete
                            else None
                        ),
                    }
                )
            return {
                "success": True,
                "definition": {
                    "name": "character_creation",
                    "declarations": json.loads(canonical_json_bytes(declarations)),
                    "setting_data": json.loads(canonical_json_bytes(
                        setting_character_schema if setting_character_schema is not None else {
                            "type": "object", "properties": {}, "additionalProperties": False,
                        }
                    )),
                    "requirements": [
                        "Requirements come from this chat's saved Setting and contained Ruleset.",
                        "Supply authored choices and facts; omit calculated output fields.",
                        "Supply Setting-specific fields separately in setting_data; both apply.",
                        "Sylvia evaluates the ordered formulas and validates the complete result.",
                        "Report missing declarations or choices; do not assume defaults.",
                    ],
                },
            }
        try:
            return {"success": True, "definition": get_content_definition(params["name"])}
        except ValueError:
            return {
                "success": False,
                "code": "content_definition_unknown",
                "message": "Use a name from the installed content definition catalog.",
            }

    def read(params: dict[str, Any], context: Any) -> dict[str, Any]:
        """Allow a precise reread of a large definition without repeating every schema/example."""
        if set(params) - {"name", "section"}:
            return {"success": False, "code": "content_definition_parameters_invalid"}
        result = read_full({key: value for key, value in params.items() if key == "name"}, context)
        if "section" not in params or not result.get("success"):
            return result
        section = params["section"]
        definition = result.get("definition")
        if not isinstance(section, str) or not section.startswith("/") or definition is None:
            return {"success": False, "code": "content_definition_section_invalid"}
        value = definition
        try:
            for token in section[1:].split("/"):
                token = token.replace("~1", "/").replace("~0", "~")
                if isinstance(value, list):
                    if not token.isdecimal():
                        raise KeyError(token)
                    value = value[int(token)]
                elif isinstance(value, dict):
                    value = value[token]
                else:
                    raise KeyError(token)
        except (KeyError, IndexError, ValueError):
            return {"success": False, "code": "content_definition_section_unknown"}
        return {"success": True, "name": params["name"], "section": section, "value": value,
                "definition_sha256": hashlib.sha256(canonical_json_bytes(definition)).hexdigest()}

    return ToolSpec.from_openai_schema(
        {
            "type": "function",
            "function": {
                "name": "get_content_definition",
                "description": (
                    "Read the installed, versioned content format definition, field descriptions, "
                    "starter example and additional admission requirements before authoring. "
                    "Omit name to list available formats. Definitions describe structure; check "
                    "the draft with its applicable validation tool before publishing. "
                    "When revisiting a large definition, section is an optional JSON Pointer "
                    "(such as /requirements or /schema/properties) to read only the needed part."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {"name": {"type": "string"}, "section": {"type": "string"}},
                    "additionalProperties": False,
                },
            },
        },
        handler=read,
        toolsets=frozenset({ToolsetName.BUILDER_TOOLS}),
    )
