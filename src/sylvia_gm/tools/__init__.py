"""Unified tool registry package.

Exports:
    ToolRegistry   — central tool registry with toolset gating
    ToolSpec       — registered tool descriptor (schema + handler)
    Toolset        — named group of tools
    ToolsetName    — string constants for toolset identifiers
    ToolHandler    — type alias for tool handler functions
"""

from sylvia_gm.tools._registry import ToolRegistry
from sylvia_gm.tools._spec import ToolHandler, Toolset, ToolSpec
from sylvia_gm.tools._toolsets import MODE_TOOLSETS, ToolsetName

__all__ = [
    "ToolRegistry",
    "ToolSpec",
    "Toolset",
    "ToolsetName",
    "ToolHandler",
    "MODE_TOOLSETS",
]
