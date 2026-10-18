"""Tool specification types for the unified tool registry.

Defines ToolSpec (the central tool descriptor), Toolset (named group),
and ToolHandler (type alias for handler functions).
"""

from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any

from sylvia_gm.runtime._types import ToolParameter

# ToolHandler signature: (params: dict, context: Any) -> dict[str, Any]
# context is mode-specific (RulesetRuntime for DM, AgentRunner for builder)
# Always returns dict with at least {"success": bool}
ToolHandler = Callable[[dict[str, Any], Any], dict[str, Any]]


@dataclass
class ToolSpec:
    """A registered tool: schema + handler + metadata.

    Replaces the scattered ToolDef (runtime) + JSON dicts (builder)
    with a single canonical representation.
    """

    name: str
    description: str
    parameters: list[ToolParameter]  # Reuses existing runtime._types.ToolParameter
    handler: ToolHandler  # Callable[[dict, Any], dict]
    toolsets: frozenset[str]  # e.g. {"dm_tools"}, {"builder_tools"}
    precondition: Callable[[], bool] | None = None  # Dynamic availability check
    metadata: dict[str, Any] = field(default_factory=dict)
    result_validator: Callable[[dict[str, Any]], bool] | None = None
    parameters_schema: dict[str, Any] | None = None

    # ── Factory: create from an OpenAI function-calling tool definition dict ──

    @classmethod
    def from_openai_schema(
        cls,
        openai_tool_def: dict[str, Any],
        *,
        handler: ToolHandler,
        toolsets: frozenset[str],
        precondition: Callable[[], bool] | None = None,
        metadata: dict[str, Any] | None = None,
        result_validator: Callable[[dict[str, Any]], bool] | None = None,
    ) -> ToolSpec:
        """Retain the full model schema alongside the legacy parameter descriptors.

        Expected input shape (from BUILDER_TOOL_DEFINITIONS):
        {
            "type": "function",
            "function": {
                "name": "read_file",
                "description": "Read a text file...",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Path to file"},
                        "offset": {"type": "integer", "description": "..."},
                        ...
                    },
                    "required": ["path"]
                }
            }
        }
        """
        func = openai_tool_def["function"]
        name = func["name"]
        description = func.get("description", "")
        params_schema = func.get("parameters", {"type": "object", "properties": {}})

        # Reverse-convert JSON Schema properties → list[ToolParameter]
        properties = params_schema.get("properties", {})
        required = set(params_schema.get("required", []))

        parameters: list[ToolParameter] = []
        for param_name, param_schema in properties.items():
            param_type = cls._json_type_to_tool_param_type(param_schema.get("type", "string"))
            parameters.append(ToolParameter(
                name=param_name,
                type=param_type,
                required=(param_name in required),
                default=param_schema.get("default"),
                enum_values=param_schema.get("enum"),
                description=param_schema.get("description", ""),
            ))

        return cls(
            name=name,
            description=description,
            parameters=parameters,
            handler=handler,
            toolsets=toolsets,
            precondition=precondition,
            metadata=metadata or {},
            result_validator=result_validator,
            parameters_schema=deepcopy(params_schema),
        )

    @staticmethod
    def _json_type_to_tool_param_type(json_type: str) -> str:
        """Retain JSON Schema types in the shared ToolParameter representation."""
        type_map = {
            "string":  "string",
            "integer": "integer",
            "number":  "number",
            "boolean": "boolean",
            "array":   "array",
            "object":  "object",
        }
        return type_map.get(json_type, "string")


@dataclass
class Toolset:
    """A named group of tools. Tools can belong to multiple toolsets."""

    name: str
    description: str
    enabled: bool = True  # Can be toggled at runtime
