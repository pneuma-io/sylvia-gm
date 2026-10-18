"""Type definitions for the ruleset runtime.

All dataclasses used by ToolResult, ProcedureResult, tool definitions,
procedure definitions, and the execution context.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class RuntimeChoiceOption:
    option_id: str
    label: str


@dataclass(frozen=True)
class RuntimeChoiceRequest:
    choice_id: str
    prompt: str
    options: tuple[RuntimeChoiceOption, ...]


@dataclass
class ChoiceResolutionControl:
    choice_id: str
    option_id: str
    expected_request: RuntimeChoiceRequest
    consumed: bool = False
    prior_answers: tuple[tuple[RuntimeChoiceRequest, str], ...] = ()
    replayed_answers: int = 0


@dataclass
class ToolResult:
    """Result of executing a single tool.

    Attributes:
        tool_name: Name of the executed tool.
        success: Whether the tool completed successfully.
        context: All context variables set during execution (including
                 dice rolls, computed values, etc.). These are ephemeral
                 and discarded after the tool returns.
        state_changes: List of state modifications made (for changelog).
        narrative_hooks: Templates selected for AI DM narration.
        error: Error message if the tool failed.
        failure_kind: Stable internal category for a failed runtime stage.
        logs: Log entries recorded during execution.
        available_tools: Next tools available (from metadata hints).
    """

    tool_name: str
    success: bool = True
    context: dict[str, Any] = field(default_factory=dict)
    state_changes: list[dict[str, Any]] = field(default_factory=list)
    narrative_hooks: dict[str, str] = field(default_factory=dict)
    narrative_directives: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None
    failure_kind: str | None = None
    logs: list[dict[str, Any]] = field(default_factory=list)
    available_tools: list[str] = field(default_factory=list)


@dataclass
class ProcedureResult:
    """Result of triggering a procedure.

    Attributes:
        procedure_name: Name of the triggered procedure.
        trigger: The trigger that fired.
        success: Whether all steps executed without error.
        tool_results: Results from any tools called during the procedure.
        narrative_hooks: Templates for AI DM narration.
        logs: Log entries from procedure steps.
        skipped_steps: List of steps skipped (with reasons).
        error: Error message if the procedure failed catastrophically.
    """

    procedure_name: str
    trigger: str
    success: bool = True
    tool_results: list[ToolResult] = field(default_factory=list)
    narrative_hooks: dict[str, str] = field(default_factory=dict)
    narrative_directives: list[dict[str, Any]] = field(default_factory=list)
    logs: list[dict[str, Any]] = field(default_factory=list)
    skipped_steps: list[dict[str, Any]] = field(default_factory=list)
    error: str | None = None


@dataclass
class ToolParameter:
    """Definition of a single tool parameter.

    ``type`` is the JSON Schema type the LLM sends (string, integer,
    number, boolean, array).  ``resolve`` tells the engine how to
    semantically validate the value after receiving it (``\"entity\"``,
    ``\"participant\"``, ``\"entity_list\"``, or ``None`` for pass-through).
    ``from_state`` makes a non-choice parameter engine-owned: the parameter is
    omitted from the model schema and populated from the Ruleset package's
    authoritative ``state`` or ``combat`` root immediately before execution.
    """

    name: str
    type: str  # string, integer, number, boolean, array
    resolve: str | None = None  # entity, participant, entity_list, or None
    required: bool = True
    default: Any = None
    enum_values: list[str] | None = None
    description: str | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None
    items: dict[str, Any] | None = None
    min_items: int | None = None
    max_items: int | None = None
    unique_items: bool | None = None
    from_state: str | None = None


@dataclass
class ToolDef:
    """Definition of a tool from the ruleset YAML."""

    name: str
    description: str = ""
    parameters: list[ToolParameter] = field(default_factory=list)
    pre_conditions: list[dict[str, Any]] = field(default_factory=list)
    effects: list[dict[str, Any]] = field(default_factory=list)
    narrative: dict[str, Any] = field(default_factory=dict)
    metadata: dict[str, Any] = field(default_factory=dict)
    range: dict[str, Any] | None = None  # {"type": "band", "value": "close"}


@dataclass
class ProcedureDef:
    """Definition of a procedure from the ruleset YAML."""

    name: str
    trigger: str
    description: str = ""
    condition: str | None = None
    steps: list[dict[str, Any]] = field(default_factory=list)
    narrative: dict[str, Any] = field(default_factory=dict)


@dataclass
class ExecutionBudget:
    """Shared deterministic work bound for one top-level package action."""

    max_operations: int = 20_000
    max_call_depth: int = 32
    operations: int = 0
    call_depth: int = 0

    def __post_init__(self) -> None:
        if self.max_operations < 1 or self.max_call_depth < 1:
            raise ValueError("Execution budget limits must be positive")

    def consume_operation(self) -> None:
        self.operations += 1
        if self.operations > self.max_operations:
            raise RuntimeError("Package runtime operation budget exceeded")

    def enter_call(self) -> None:
        self.call_depth += 1
        if self.call_depth > self.max_call_depth:
            self.call_depth -= 1
            raise RuntimeError("Package runtime call depth exceeded")

    def leave_call(self) -> None:
        if self.call_depth < 1:
            raise RuntimeError("Package runtime call depth underflow")
        self.call_depth -= 1


@dataclass
class ExecutionContext:
    """Mutable context carried through tool/procedure execution.

    Context variables (prefixed with _) are ephemeral and live only for
    the duration of a single execute_tool() call. State variables are
    persisted through the StateBridge.

    The context also holds reference to the ruleset definitions and
    feature toggles for expression evaluation.
    """

    # Tool parameters mapped to their values (attacker→entity dict, weapon→"longsword")
    params: dict[str, Any] = field(default_factory=dict)

    # Ephemeral context variables (_attack_roll, _damage_amount, _is_crit, etc.)
    vars: dict[str, Any] = field(default_factory=dict)

    # Entity state snapshots (entity_id → full entity dict including kind)
    entities: dict[str, dict[str, Any]] = field(default_factory=dict)

    # Ruleset config layer (features, abilities — read-only)
    ruleset_config: dict[str, Any] = field(default_factory=dict)

    # Ruleset definitions (weapons, damage_types, scar_table, etc.)
    definitions: dict[str, Any] = field(default_factory=dict)

    # Top-level combat state (injected by procedure executor)
    combat_state: dict[str, Any] = field(default_factory=dict)

    # State bridge for persisting changes
    state_bridge: Any = None  # StateBridge instance (avoids circular import)

    # Current tool name (for error messages)
    current_tool: str = ""

    # Procedure executor for effect handlers to trigger procedures (LOCK-1)
    procedure_executor: Callable[..., Any] | None = None

    # Catch-all dict for runtime manager storage (LOCK-3)
    extras: dict[str, Any] = field(default_factory=dict)

    # One shared operation/call-depth budget for the complete nested action.
    budget: ExecutionBudget | None = None

    def resolve_field(self, path: str) -> Any:
        """Resolve a dot-separated field path against the context.

        Resolution order:
        1. Tool parameters (attacker, target, weapon, etc.)
        2. Context variables (_attack_roll, _damage_roll, etc.)
        3. Top-level combat state (combat.round, combat.turn_index)
        4. Entity state (via entity dicts)
        5. Ruleset config (ruleset.features.*)
        6. Definitions (definitions.weapons.*)
        """
        parts = path.split(".")
        root = parts[0]

        # 1. Tool parameters
        if root in self.params:
            if root in self.vars:
                return self._traverse(self.vars[root], parts[1:])
            return self._traverse(self.params[root], parts[1:])

        # 2. Context variables
        if root in self.vars:
            return self._traverse(self.vars[root], parts[1:])

        # 3. Top-level combat state
        if root == "combat" and len(parts) > 1:
            return self._traverse(self.combat_state, parts[1:])

        if root == "combatants" or root == "party_members":
            return self._traverse(self.params, parts)

        # 4. Entity state (entity_id check)
        if root in self.entities:
            return self._traverse(self.entities[root], parts[1:])

        # 5. Ruleset config
        if root == "ruleset":
            return self._traverse(self.ruleset_config, parts[1:])

        # 6. Definitions
        if root == "definitions":
            return self._traverse(self.definitions, parts[1:])

        return None

    @staticmethod
    def _traverse(obj: Any, parts: list[str]) -> Any:
        """Traverse nested dicts/lists following field path parts."""
        current = obj
        for part in parts:
            if current is None:
                return None
            if isinstance(current, dict):
                current = current.get(part)
            elif isinstance(current, (list, tuple)):
                # Handle array indexing via __index__N protocol
                try:
                    idx = int(part)
                    current = current[idx]
                except (ValueError, IndexError):
                    return None
            elif hasattr(current, part):
                current = getattr(current, part)
            else:
                return None
        return current


# ── Turn System Types ────────────────────────────────────────────────────


@dataclass
class ResourcePoolState:
    """Runtime state of a resource pool."""

    remaining: int | float
    refresh: str  # "turn_start", "round_start", "phase_start"
    unit: str | None = None


@dataclass
class TurnState:
    """Runtime state of a turn in progress."""

    round: int
    resolution_style: str  # "round", "phase", etc.
    participants: list[str] = field(default_factory=list)  # entity IDs
    current_actor: str | None = None  # entity ID or None
    current_resources: dict[str, ResourcePoolState] = field(default_factory=dict)


@dataclass
class ModeState:
    """Runtime state of active modes."""

    active_modes: list[str] = field(default_factory=list)  # mode stack
    turn_states: dict[str, TurnState] = field(default_factory=dict)  # one per active mode
