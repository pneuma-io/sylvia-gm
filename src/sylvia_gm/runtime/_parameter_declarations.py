"""Resolve package-local literal parameter declarations without changing their meaning."""

from __future__ import annotations

import re
from collections.abc import Callable
from copy import deepcopy
from typing import Any

from jsonschema import Draft202012Validator

PARAMETER_DECLARATION_ID_PATTERN = r"^[a-z][a-z0-9_]*$"
PARAMETER_DESCRIPTOR_SCHEMA = {
    "type": "object",
    "required": ["name"],
    "properties": {
        "name": {"type": "string", "minLength": 1},
        "type": {
            "enum": [
                "array",
                "boolean",
                "entity_id",
                "entity_id_list",
                "enum",
                "integer",
                "number",
                "object",
                "string",
            ]
        },
        "description": {"type": "string"},
        "required": {"type": "boolean"},
        "enum_values": {"type": "array"},
        "minimum": {"type": "number"},
        "maximum": {"type": "number"},
        "items": {"type": "object", "additionalProperties": True},
        "from_state": {"type": "string"},
        "default": {},
        "resolve": {"enum": ["entity", "participant", "entity_list"]},
        "model_derive": {
            "oneOf": [
                {"type": "boolean"},
                {
                    "type": "object",
                    "properties": {
                        "kind": {"type": "string"},
                        "source": {"type": "string"},
                        "field": {"type": "string"},
                        "participant": {"type": "string"},
                        "mapping": {"type": "object", "additionalProperties": True},
                        "present": {},
                        "absent": {},
                        "value": {},
                    },
                    "required": ["kind"],
                    "additionalProperties": False,
                },
            ]
        },
        "model_hidden": {"type": "boolean"},
        "model_required": {"type": "boolean"},
        "min_items": {"type": "integer", "minimum": 0},
        "max_items": {"type": "integer", "minimum": 0},
        "unique_items": {"type": "boolean"},
    },
    "additionalProperties": False,
}
PARAMETER_DESCRIPTOR_FIELDS = frozenset(PARAMETER_DESCRIPTOR_SCHEMA["properties"])
_PARAMETER_DESCRIPTOR_VALIDATOR = Draft202012Validator(PARAMETER_DESCRIPTOR_SCHEMA)


class ParameterDeclarationError(ValueError):
    """A parameter reference or its literal declaration cannot be resolved."""


def resolve_parameter_declarations(
    data: dict[str, Any], *, validate_shape: Callable[[object], None] | None = None
) -> dict[str, Any]:
    """Expand only tool parameter-list IDs, copying each use and leaving input data intact."""
    pool = data.get("parameter_declarations", {})
    if not isinstance(pool, dict):
        raise ParameterDeclarationError("parameter_declarations must be a mapping")
    for name, descriptor in pool.items():
        if not isinstance(name, str) or not re.fullmatch(PARAMETER_DECLARATION_ID_PATTERN, name):
            raise ParameterDeclarationError(
                "Parameter declaration IDs must be literal snake-case text"
            )
        if not isinstance(descriptor, dict):
            raise ParameterDeclarationError(
                f"Parameter declaration '{name}' must be a literal descriptor"
            )
        if not _PARAMETER_DESCRIPTOR_VALIDATOR.is_valid(descriptor):
            raise ParameterDeclarationError(
                f"Parameter declaration '{name}' has invalid descriptor fields"
            )
        if set(descriptor) - PARAMETER_DESCRIPTOR_FIELDS:
            raise ParameterDeclarationError(
                f"Parameter declaration '{name}' has unknown descriptor fields"
            )
        if not isinstance(descriptor.get("name"), str) or not descriptor["name"]:
            raise ParameterDeclarationError(
                f"Parameter declaration '{name}' requires a parameter name"
            )

    tools = data.get("tools", {})
    if not isinstance(tools, dict):
        return data  # The existing executable validator owns invalid tool-section diagnostics.
    has_references = any(
        isinstance(tool, dict)
        and isinstance(tool.get("parameters"), list)
        and any(isinstance(parameter, str) for parameter in tool["parameters"])
        for tool in tools.values()
    )
    if "parameter_declarations" not in data and not has_references:
        return data
    normalized = {
        key: value for key, value in data.items() if key not in {"parameter_declarations", "tools"}
    }
    normalized["tools"] = {}
    for name, tool in tools.items():
        copied = dict(tool) if isinstance(tool, dict) else tool
        if isinstance(tool, dict) and tool.get("parameters") is not None:
            parameters = tool["parameters"]
            if not isinstance(parameters, list):
                raise ParameterDeclarationError(f"Tool '{name}' parameters must be an ordered list")
            resolved = []
            for parameter in parameters:
                if isinstance(parameter, str):
                    if parameter not in pool:
                        raise ParameterDeclarationError(
                            f"Tool '{name}' has unknown parameter declaration ID '{parameter}'"
                        )
                    parameter = pool[parameter]
                elif not isinstance(parameter, dict):
                    raise ParameterDeclarationError(
                        f"Tool '{name}' parameter must be a descriptor or ID"
                    )
                resolved.append(parameter)
            copied["parameters"] = resolved
        normalized["tools"][name] = copied
    if validate_shape is not None:
        validate_shape(normalized)
    return {
        **{key: deepcopy(value) for key, value in normalized.items() if key != "tools"},
        "tools": {
            name: {
                **{key: deepcopy(value) for key, value in tool.items() if key != "parameters"},
                "parameters": [deepcopy(parameter) for parameter in tool["parameters"]],
            }
            if isinstance(tool, dict) and isinstance(tool.get("parameters"), list)
            else deepcopy(tool)
            for name, tool in normalized["tools"].items()
        },
    }
