"""Tool conversion utilities for the session runner.

Converts ruleset tool definitions (ToolDef / ToolParameter) into the
OpenAI function-calling JSON schema format so the LLM can invoke them.

DEPRECATED: New code should use sylvia_gm.tools._converter.tool_spec_to_openai()
and the ToolRegistry instead. This module is kept for backward compatibility.
"""

from __future__ import annotations

import logging
from typing import Any

from sylvia_gm.runtime._types import ToolDef, ToolParameter

logger = logging.getLogger(__name__)

# Map ruleset parameter types to JSON Schema types.
_PARAM_TYPE_MAP: dict[str, str] = {
    "entity_id": "string",
    "entity_id_list": "array",
    "string": "string",
    "int": "integer",
    "integer": "integer",
    "float": "number",
    "number": "number",
    "bool": "boolean",
    "boolean": "boolean",
    "list": "array",
    "array": "array",
    "enum": "string",
}


def _param_to_json_schema(param: ToolParameter) -> dict[str, Any]:
    """Convert a single ToolParameter to a JSON Schema property."""
    json_type = _PARAM_TYPE_MAP.get(param.type, "string")
    schema: dict[str, Any] = {
        "type": json_type,
        "description": param.description or "",
    }
    if param.enum_values:
        schema["enum"] = param.enum_values
    return schema


def convert_tools(tool_defs: dict[str, ToolDef]) -> list[dict[str, Any]]:
    """Convert ruleset ToolDef objects to OpenAI function-calling format.

    DEPRECATED: This function is kept for backward compatibility.
    New code should use ToolRegistry.get_definitions() instead.

    Args:
        tool_defs: Dict of tool_name → ToolDef from the runtime.

    Returns:
        List of tool descriptors in OpenAI format.
    """
    tools: list[dict[str, Any]] = []
    for name, tool_def in tool_defs.items():
        properties: dict[str, Any] = {}
        required: list[str] = []

        for param in tool_def.parameters:
            properties[param.name] = _param_to_json_schema(param)
            if param.required and param.default is None:
                required.append(param.name)

        tools.append({
            "type": "function",
            "function": {
                "name": name,
                "description": tool_def.description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        })

    return tools
