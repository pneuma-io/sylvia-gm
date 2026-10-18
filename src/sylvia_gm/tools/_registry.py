"""Central tool registry with toolset gating and dynamic availability.

Provides a single source of truth for all tools across DM and Builder modes.
Toolset gating is enforced at the LLM level (get_definitions filters by mode
+ toolset). The execute() method does NOT check toolset membership — this is
intentional: direct programmatic calls bypass the gate, matching current
behavior where any handler can be called directly.
"""

from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

if TYPE_CHECKING:
    from sylvia_gm.agent._mode import AgentMode

from sylvia_gm.tools._converter import tool_spec_to_openai
from sylvia_gm.tools._spec import ToolSpec, Toolset
from sylvia_gm.tools._toolsets import ToolsetName, resolve_mode_toolsets

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Central tool registry with toolset gating and dynamic availability.

    Usage:
        registry = ToolRegistry(context=runner_instance)
        registry.register(ToolSpec(name="read_file", ...))
        definitions = registry.get_definitions(AgentMode.BUILDER_FREEFORM)
        result = registry.execute("read_file", {"path": "foo.txt"}, AgentMode.BUILDER_FREEFORM)
    """

    def __init__(self, context: Any = None) -> None:
        """Initialize the registry.

        Args:
            context: Injected for handler calls (e.g., AgentRunner instance).
        """
        self._tools: dict[str, ToolSpec] = {}
        self._toolsets: dict[str, Toolset] = {
            ToolsetName.DM_TOOLS: Toolset(ToolsetName.DM_TOOLS, "DM ruleset tools"),
            ToolsetName.CAMPAIGN_TOOLS: Toolset(ToolsetName.CAMPAIGN_TOOLS, "DM campaign-file tools (R2)"),
            ToolsetName.BUILDER_TOOLS: Toolset(ToolsetName.BUILDER_TOOLS, "Builder file I/O tools"),
            ToolsetName.INTERVIEW_TOOLS: Toolset(ToolsetName.INTERVIEW_TOOLS, "Builder interview widget tools"),
            ToolsetName.COMMON_TOOLS: Toolset(ToolsetName.COMMON_TOOLS, "All-mode shared tools"),
        }
        self._context: Any = context

    # ── Context setter (allows late injection) ──

    def set_context(self, context: Any) -> None:
        """Set or update the context for handler calls."""
        self._context = context

    # ── Registration ──

    def register(self, tool: ToolSpec) -> None:
        """Register a tool.

        If a tool with the same name already exists, it is replaced
        (last-write-wins). This allows re-registration from hot-reloaded
        rulesets without clearing state.
        """
        self._tools[tool.name] = tool

    def register_many(self, tools: list[ToolSpec]) -> None:
        """Register multiple tools at once."""
        for tool in tools:
            self.register(tool)

    def unregister(self, name: str) -> None:
        """Remove a tool from the registry by name."""
        self._tools.pop(name, None)

    # ── Querying ──

    def get_definitions(
        self, mode: "AgentMode", context: Any = None
    ) -> list[dict[str, Any]]:
        """Return OpenAI function-calling JSON for all available tools in mode.

        Filters by:
        1. Toolsets enabled for the given mode (via resolve_mode_toolsets)
        2. Toolset.enabled flag
        3. tool.precondition() returns True (or None = always available)

        Args:
            mode: The AgentMode to filter tools for.
            context: Optional context override (used for precondition evaluation).

        Returns:
            List of OpenAI function-calling tool definitions.
        """
        enabled_toolset_names = resolve_mode_toolsets(mode)
        enabled_set: set[str] = set()
        for ts_name in enabled_toolset_names:
            if ts_name in self._toolsets and self._toolsets[ts_name].enabled:
                enabled_set.add(ts_name)
            else:
                available = list(self._toolsets.keys())
                logger.warning(
                    "get_definitions mode=%s: toolset '%s' not found or disabled — available: %s",
                    mode.value if hasattr(mode, 'value') else str(mode),
                    ts_name, available,
                )

        available: list[dict[str, Any]] = []
        for tool in self._tools.values():
            # Toolset gating: tool must belong to at least one enabled toolset
            if not (tool.toolsets & enabled_set):
                continue

            # Dynamic availability: precondition must pass (or be None)
            if tool.precondition is not None and not tool.precondition():
                continue

            # Convert to OpenAI JSON
            available.append(tool_spec_to_openai(tool))

        return available

    def get_tool(self, name: str) -> ToolSpec | None:
        """Return a tool by name, or None if not registered."""
        return self._tools.get(name)

    def list_tools(self, mode: "AgentMode | None" = None) -> list[ToolSpec]:
        """List all registered tools, optionally filtered by mode.

        Args:
            mode: If None, returns ALL registered tools.
                  If set, filters by enabled toolsets for that mode.

        Returns:
            List of ToolSpec objects.
        """
        if mode is None:
            return list(self._tools.values())

        enabled_toolset_names = resolve_mode_toolsets(mode)
        enabled_set = {
            ts_name for ts_name in enabled_toolset_names
            if ts_name in self._toolsets and self._toolsets[ts_name].enabled
        }

        return [
            tool for tool in self._tools.values()
            if tool.toolsets & enabled_set
        ]

    # ── Execution ──

    def execute(
        self, name: str, params: dict[str, Any], mode: "AgentMode"
    ) -> dict[str, Any]:
        """Dispatch tool by name. Returns dict with at least {"success": bool}.

        Design note: Toolset gating is NOT enforced here. It is enforced at
        the LLM level via get_definitions() filtering. Direct programmatic
        calls (tests, proxy methods) bypass the gate intentionally, matching
        current behavior where _tool_read_file() can be called directly.

        Args:
            name: The tool name to dispatch.
            params: Tool parameters (dict).
            mode: The current AgentMode (informational, not used for gating).

        Returns:
            Dict with at least {"success": bool}.
        """
        tool = self._tools.get(name)
        if tool is None:
            return {
                "success": False,
                "error": f"Tool '{name}' not found in registry.",
            }

        try:
            result = tool.handler(params, self._context)
        except Exception as e:
            return {
                "success": False,
                "error": f"Tool execution error: {e}",
            }

        # Basic shape validation
        if not isinstance(result, dict):
            result = {"success": False, "error": "Handler returned non-dict result"}
        elif "success" not in result:
            result = {"success": True, **result}  # Assume success if missing

        # Optional: custom validator (soft warning, never blocks)
        if tool.result_validator is not None:
            try:
                if not tool.result_validator(result):
                    logger.warning(
                        "Tool '%s' result failed custom validation", name
                    )
            except Exception:
                logger.warning(
                    "Tool '%s' result_validator raised, ignoring", name
                )

        return result

    # ── Toolset management ──

    def enable_toolset(self, name: str) -> None:
        """Enable a toolset so its tools appear in get_definitions()."""
        if name in self._toolsets:
            self._toolsets[name].enabled = True

    def disable_toolset(self, name: str) -> None:
        """Disable a toolset so its tools disappear from get_definitions()."""
        if name in self._toolsets:
            self._toolsets[name].enabled = False

    def is_toolset_enabled(self, name: str) -> bool:
        """Return whether a toolset is currently enabled."""
        toolset = self._toolsets.get(name)
        return toolset is not None and toolset.enabled

    # ── Dynamic availability ──

    def refresh_preconditions(self) -> None:
        """Re-evaluate all tool preconditions (no-op; preconditions are evaluated
        lazily at get_definitions() time)."""
        pass  # Preconditions are evaluated inline in get_definitions()

    # ── Validation ──

    def validate_result(self, name: str, result: dict[str, Any]) -> bool:
        """Check result shape against the tool's result_validator (if defined)."""
        tool = self._tools.get(name)
        if tool is None or tool.result_validator is None:
            return True  # No validator = always valid
        try:
            return tool.result_validator(result)
        except Exception:
            return False

    def get_combat_tool_names(self, mode: "AgentMode | None" = None) -> set[str]:
        """Return the set of tool names whose metadata marks them as combat-only.

        Used by ``_build_tools`` to strip combat tools from the non-combat
        tool list (and surface ``start_combat`` instead).
        """
        return {
            name for name, spec in self._tools.items()
            if spec.metadata.get("category") == "combat"
        }
