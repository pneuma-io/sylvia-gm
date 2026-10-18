"""ToolSpec → OpenAI function-calling JSON conversion.

Replaces the conversion logic from session/_tools.py with a generalized
converter that accepts ToolSpec objects instead of raw ToolDef objects.

``ToolParameter.type`` is already a JSON Schema type string (string, array,
integer, number, boolean) — no translation needed.  The converter passes it
straight through.
"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from sylvia_gm.runtime._types import ToolParameter
from sylvia_gm.tools._spec import ToolSpec


def _param_to_json_schema(param: ToolParameter) -> dict[str, Any]:
    """Convert a single ToolParameter to a JSON Schema property."""
    json_type = param.type
    schema: dict[str, Any] = {
        "type": json_type,
        "description": param.description or "",
    }
    if json_type == "array":
        schema["items"] = {"type": "string"}
    if param.enum_values:
        schema["enum"] = param.enum_values
    return schema


def tool_spec_to_openai(spec: ToolSpec) -> dict[str, Any]:
    """Convert a single ToolSpec to an OpenAI function-calling JSON dict.

    Args:
        spec: The ToolSpec to convert.

    Returns:
        A dict in OpenAI function-calling format:
        {"type": "function", "function": {"name": ..., "description": ..., "parameters": ...}}
    """
    properties: dict[str, Any] = {}
    required: list[str] = []

    for param in spec.parameters:
        properties[param.name] = _param_to_json_schema(param)
        if param.required and param.default is None:
            required.append(param.name)

    return {
        "type": "function",
        "function": {
            "name": spec.name,
            "description": spec.description,
            "parameters": (
                deepcopy(spec.parameters_schema) if spec.parameters_schema is not None else {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                }
            ),
        },
    }
