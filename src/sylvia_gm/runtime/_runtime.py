"""RulesetRuntime — the top-level API for the ruleset execution engine.

This is a shell that interprets ruleset YAML files. It contains ZERO
hardcoded game mechanics — all game rules live in the ruleset YAML.
"""

from __future__ import annotations

import logging
from typing import Any

from sylvia_gm.runtime._exceptions import (
    ProcedureDepthError,
)
from sylvia_gm.runtime._parameter_declarations import resolve_parameter_declarations
from sylvia_gm.runtime._procedure_executor import trigger_procedure
from sylvia_gm.runtime._schema_validator import validate_parameter_declarations, validate_ruleset
from sylvia_gm.runtime._state_bridge import StateBridge
from sylvia_gm.runtime._tool_executor import execute_tool
from sylvia_gm.runtime._types import (
    ExecutionContext,
    ModeState,
    ProcedureDef,
    ProcedureResult,
    ToolDef,
    ToolParameter,
    ToolResult,
)

logger = logging.getLogger(__name__)


class RulesetRuntime:
    """Generic engine that interprets ruleset YAML files.

    Shell architecture: the runtime knows nothing about AC, HP, death saves,
    initiative, or any game concept. It executes tools and procedures
    defined entirely in the ruleset YAML.

    Runtime dictionaries come from admitted package bytes; the interpreter does
    not discover or select a bundled ruleset by name.
    """

    def __init__(
        self,
        state_api: Any = None,
        ruleset_dict: dict[str, Any] | None = None,
    ) -> None:
        """Initialize the runtime.

        Args:
            state_api: T1.1 StateAPI instance (or None for testing).
            ruleset_dict: Parsed ruleset YAML (or None to load later).
        """
        self._state_bridge = StateBridge(state_api) if state_api else None
        self._ruleset: dict[str, Any] = ruleset_dict or {}
        self._tools: dict[str, ToolDef] = {}
        self._procedures: dict[str, ProcedureDef] = {}
        self._combat_state: dict[str, Any] = {}
        self._mode_state = ModeState()

        if ruleset_dict:
            self._parse_ruleset(ruleset_dict)

    # ── Properties ───────────────────────────────────────────────────────

    @property
    def state_bridge(self) -> StateBridge | None:
        """The StateBridge adapter for T1.1 integration."""
        return self._state_bridge

    @property
    def tools(self) -> dict[str, ToolDef]:
        """All tool definitions loaded from the ruleset."""
        return dict(self._tools)

    @property
    def model_tool_names(self) -> frozenset[str]:
        """Return the Ruleset-declared model surface, or the legacy visible runtime surface."""

        declared = self._ruleset.get("model_tool_availability")
        if isinstance(declared, dict):
            return frozenset(str(name) for name in declared)
        return frozenset(
            name
            for name, tool in self._tools.items()
            if tool.metadata.get("visible_to_player", True)
        )

    @property
    def model_decision_boundary(self) -> dict[str, Any] | None:
        """Return the optional Ruleset-owned actor-control projection."""

        declared = self._ruleset.get("model_decision_boundary")
        return dict(declared) if isinstance(declared, dict) else None

    @property
    def model_decision_scope(self) -> dict[str, Any] | None:
        """Return the optional Ruleset-owned multi-actor decision projection."""

        declared = self._ruleset.get("model_decision_scope")
        return dict(declared) if isinstance(declared, dict) else None

    @property
    def procedures(self) -> dict[str, ProcedureDef]:
        """All procedure definitions loaded from the ruleset."""
        return dict(self._procedures)

    @property
    def ruleset_config(self) -> dict[str, Any]:
        """Ruleset config layer (features, abilities)."""
        value: object = self._ruleset.get("features", {})
        return dict(value) if isinstance(value, dict) else {}

    @property
    def definitions(self) -> dict[str, Any]:
        """Ruleset definitions (weapons, tables, etc.)."""
        value: object = self._ruleset.get("definitions", {})
        return dict(value) if isinstance(value, dict) else {}

    @property
    def mode_state(self) -> ModeState:
        """Current mode stack and turn states."""
        return self._mode_state

    # ── Lifecycle ────────────────────────────────────────────────────────

    def load_ruleset(self, ruleset_dict: dict[str, Any]) -> None:
        """Load and validate a ruleset from a parsed dict.

        Args:
            ruleset_dict: Parsed ruleset YAML as a dict.
                Must have 'meta', 'features', 'abilities' sections.
                Optional: 'tools', 'procedures', 'definitions', 'narrative'.
        """
        self._parse_ruleset(ruleset_dict)

    def set_state_api(self, state_api: Any) -> None:
        """Set or replace the StateAPI instance."""
        self._state_bridge = StateBridge(state_api)

    def set_combat_state(self, combat_state: dict[str, Any]) -> None:
        """Set top-level combat state (round, turn_index, order, etc.)."""
        self._combat_state = dict(combat_state)

    # ── Mode Management ─────────────────────────────────────────────────

    def start_mode(self, mode_name: str, participants: list[str] | None = None) -> dict[str, Any]:
        """Activate a mode and push it onto the mode stack.

        Args:
            mode_name: Name of the mode from ruleset.modes
            participants: Initial list of entity IDs participating in this mode

        Returns:
            {"success": True, "mode": mode_name} or {"success": False, "error": str}
        """
        if mode_name in self._mode_state.active_modes:
            return {"success": False, "error": f"Mode '{mode_name}' is already active"}

        modes = self._ruleset.get("modes", {})
        if mode_name not in modes:
            return {"success": False, "error": f"Mode '{mode_name}' not found in ruleset"}

        mode_config = modes[mode_name]
        turn_system_config = mode_config.get("turn_system")
        if turn_system_config is None:
            return {"success": False, "error": f"Mode '{mode_name}' has no turn_system config"}

        # Create TurnSystem and initialize TurnState
        from sylvia_gm.ruleset.schema import TurnSystemConfig
        from sylvia_gm.runtime._turn_system import TurnSystem

        ts_config = TurnSystemConfig(**turn_system_config)
        turn_system = TurnSystem(ts_config, participants=participants)

        # Push mode onto stack and store turn state
        self._mode_state.active_modes.append(mode_name)
        self._mode_state.turn_states[mode_name] = turn_system.state

        logger.info(f"Started mode '{mode_name}' with {len(participants or [])} participants")
        return {"success": True, "mode": mode_name}

    def end_mode(self) -> dict[str, Any]:
        """Deactivate the current mode and pop it from the stack.

        Returns:
            Success with ended/active modes, or failure with an error string.
        """
        if not self._mode_state.active_modes:
            return {"success": False, "error": "No active mode to end"}

        ended_mode = self._mode_state.active_modes.pop()
        self._mode_state.turn_states.pop(ended_mode, None)

        active_mode = self._mode_state.active_modes[-1] if self._mode_state.active_modes else None

        logger.info(f"Ended mode '{ended_mode}', active mode: {active_mode}")
        return {"success": True, "ended_mode": ended_mode, "active_mode": active_mode}

    def add_to_mode(self, entities: list[str]) -> dict[str, Any]:
        """Add entities to the active mode's participants.

        Args:
            entities: List of entity IDs to add

        Returns:
            {"success": True, "added": list[str]} or {"success": False, "error": str}
        """
        if not self._mode_state.active_modes:
            return {"success": False, "error": "No active mode"}

        active_mode = self._mode_state.active_modes[-1]
        turn_state = self._mode_state.turn_states.get(active_mode)
        if turn_state is None:
            return {"success": False, "error": f"No turn state for mode '{active_mode}'"}

        added = []
        for entity_id in entities:
            if entity_id not in turn_state.participants:
                turn_state.participants.append(entity_id)
                added.append(entity_id)

        logger.info(f"Added {len(added)} entities to mode '{active_mode}'")
        return {"success": True, "added": added}

    def remove_from_mode(self, entities: list[str]) -> dict[str, Any]:
        """Remove entities from the active mode's participants.

        Args:
            entities: List of entity IDs to remove

        Returns:
            {"success": True, "removed": list[str]} or {"success": False, "error": str}
        """
        if not self._mode_state.active_modes:
            return {"success": False, "error": "No active mode"}

        active_mode = self._mode_state.active_modes[-1]
        turn_state = self._mode_state.turn_states.get(active_mode)
        if turn_state is None:
            return {"success": False, "error": f"No turn state for mode '{active_mode}'"}

        removed = []
        for entity_id in entities:
            if entity_id in turn_state.participants:
                turn_state.participants.remove(entity_id)
                removed.append(entity_id)

                # If removed entity was current actor, reset current_actor
                if turn_state.current_actor == entity_id:
                    turn_state.current_actor = None

        logger.info(f"Removed {len(removed)} entities from mode '{active_mode}'")
        return {"success": True, "removed": removed}

    # ── Tool Execution ───────────────────────────────────────────────────

    def execute_tool(
        self,
        tool_name: str,
        params: dict[str, Any],
        seed: int | None = None,
    ) -> ToolResult:
        """Execute a single tool.

        Flow:
        1. Look up tool definition in ruleset.tools
        2. Validate parameter types
        3. Fetch entity state snapshots via StateBridge
        4. Evaluate pre-conditions
        5. Execute effects sequentially
        6. Return ToolResult

        Args:
            tool_name: Name of the tool (must exist in ruleset).
            params: Parameter values keyed by name.
            seed: If provided, seed the dice roller for determinism.

        Returns:
            ToolResult with success, context vars, logs, narrative hooks.
        """
        if seed is not None:
            from sylvia_gm.dice import set_seed

            set_seed(seed)

        # T5.5: Log tool execution start
        entity_id = (
            params.get("entity_id") or params.get("attacker") or params.get("target") or "n/a"
        )
        logger.debug("tool_exec_start tool=%s entity=%s", tool_name, entity_id)

        if tool_name not in self._tools:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=f"Tool '{tool_name}' not found in ruleset.",
                failure_kind="tool_unavailable",
            )

        # Create the nested executor closure for 'call' effects
        def nested_executor(
            nested_tool_name: str,
            nested_params: dict[str, Any],
            parent_ctx: ExecutionContext,
        ) -> ToolResult:
            return execute_tool(
                nested_tool_name,
                nested_params,
                self._tools,
                self._state_bridge,
                self.ruleset_config,
                self.definitions,
                self._combat_state,
                tool_executor=nested_executor,
                budget=parent_ctx.budget,
            )

        return execute_tool(
            tool_name,
            params,
            self._tools,
            self._state_bridge,
            self.ruleset_config,
            self.definitions,
            self._combat_state,
            tool_executor=nested_executor,
        )

    def list_available_tools(self) -> list[ToolDef]:
        """Return tools whose pre-conditions pass for current state.

        This is called by the AI DM to determine what actions are
        available to suggest to the player.

        Currently returns all tools — full pre-condition filtering
        is deferred to a future pass.
        """
        # Basic: return all tools that don't have failing pre-conditions
        available: list[ToolDef] = []
        for tool_def in self._tools.values():
            # Skip hidden tools
            if not tool_def.metadata.get("visible_to_player", True):
                continue
            available.append(tool_def)
        return available

    # ── Procedure Execution ──────────────────────────────────────────────

    def trigger_procedure(
        self,
        trigger_name: str,
        context: dict[str, Any] | None = None,
        _depth: int = 0,
    ) -> ProcedureResult:
        """Find and execute all procedures matching a trigger.

        Args:
            trigger_name: The trigger that fired (e.g., 'on_combat_start').
            context: Payload context (actor, damage_source, etc.).
            _depth: Internal recursion guard (max 10).

        Returns:
            ProcedureResult with outcomes.

        Raises:
            ProcedureDepthError: If procedure chain exceeds 10 levels.
        """
        if _depth > 10:
            raise ProcedureDepthError(trigger_name, _depth, 10)

        # T5.5: Log procedure trigger
        logger.debug("procedure_triggered proc=%s depth=%d", trigger_name, _depth)

        return trigger_procedure(
            trigger_name,
            self._procedures,
            self._tools,
            self._state_bridge,
            self.ruleset_config,
            self.definitions,
            context=context,
            combat_state=self._combat_state,
            _depth=_depth,
        )

    def get_active_procedures(self) -> list[ProcedureDef]:
        """Return all procedures defined in the active ruleset."""
        return list(self._procedures.values())

    # ── Validation ───────────────────────────────────────────────────────

    def validate_ruleset(self, yaml_content: str) -> list[str]:
        """Validate ruleset YAML for syntax, schema, and cross-references.

        Args:
            yaml_content: Raw ruleset YAML string.

        Returns:
            List of error messages (empty = valid).
        """
        return validate_ruleset(yaml_content)

    # ── Internal ─────────────────────────────────────────────────────────

    def _parse_ruleset(self, data: dict[str, Any]) -> None:
        """Extract tools and procedures from the ruleset dict."""
        declaration_errors = validate_parameter_declarations(data)
        if declaration_errors:
            raise ValueError("; ".join(declaration_errors))
        data = resolve_parameter_declarations(data)
        self._ruleset = data
        self._tools.clear()
        self._procedures.clear()

        # Parse tools
        tools_raw = data.get("tools", {})
        if isinstance(tools_raw, dict):
            for name, defn in tools_raw.items():
                if not isinstance(defn, dict):
                    continue
                params = []
                for pd in defn.get("parameters", []) or []:
                    if isinstance(pd, dict):
                        params.append(
                            ToolParameter(
                                name=pd.get("name", ""),
                                type=pd.get("type", "string"),
                                resolve=pd.get("resolve"),
                                required=pd.get("required", True),
                                default=pd.get("default"),
                                enum_values=pd.get("enum_values"),
                                description=pd.get("description"),
                                minimum=pd.get("minimum"),
                                maximum=pd.get("maximum"),
                                items=pd.get("items"),
                                min_items=pd.get("min_items"),
                                max_items=pd.get("max_items"),
                                unique_items=pd.get("unique_items"),
                                from_state=pd.get("from_state"),
                            )
                        )
                self._tools[name] = ToolDef(
                    name=name,
                    description=defn.get("description", ""),
                    parameters=params,
                    pre_conditions=defn.get("pre_conditions", []) or [],
                    effects=defn.get("effects", []) or [],
                    narrative=defn.get("narrative", {}) or {},
                    metadata=defn.get("metadata", {}) or {},
                    range=defn.get("range"),  # T5.10: distance range annotation
                )

        # Parse procedures
        procedures_raw = data.get("procedures", {})
        if isinstance(procedures_raw, dict):
            for name, defn in procedures_raw.items():
                if not isinstance(defn, dict):
                    continue
                self._procedures[name] = ProcedureDef(
                    name=name,
                    trigger=defn.get("trigger", ""),
                    description=defn.get("description", ""),
                    condition=defn.get("condition"),
                    steps=defn.get("steps", []) or [],
                    narrative=defn.get("narrative", {}) or {},
                )

    def __repr__(self) -> str:
        meta = self._ruleset.get("meta", {})
        ruleset_name = meta.get("name", "unknown")
        return (
            f"RulesetRuntime(ruleset='{ruleset_name}', "
            f"tools={len(self._tools)}, procedures={len(self._procedures)})"
        )
