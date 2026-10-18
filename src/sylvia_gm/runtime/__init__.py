"""Ruleset Runtime — T1.4 execution engine.

The runtime is a SHELL that interprets ruleset YAML files.
It contains ZERO hardcoded game mechanics — all game rules
live in the ruleset YAML.

Core classes:
    RulesetRuntime  — top-level engine
    StateBridge     — T1.1 StateAPI adapter
    DistanceTracker — T48 distance tracking (band + grid modes)
    ToolResult      — single tool execution result
    ProcedureResult — procedure execution result

Expression evaluation:
    evaluate_condition() — sandboxed boolean expression evaluation
    evaluate_expression() — evaluate any expression against context
    parse_expression() — parse expression string to AST
"""

from sylvia_gm.runtime._clock_manager import Clock, ClockManager
from sylvia_gm.runtime._condition_evaluator import (
    evaluate,
    evaluate_condition,
    evaluate_expression,
)
from sylvia_gm.runtime._deferred_queue import DeferredCall, DeferredQueue
from sylvia_gm.runtime._distance_tracker import (
    BAND_ORDER,
    BAND_RANGES,
    BAND_TO_FEET,
    DistanceTracker,
    FEET_PER_SQUARE,
    GridPosition,
)
from sylvia_gm.runtime._duration_tracker import DurationEntry, DurationTracker
from sylvia_gm.runtime._effect_executor import execute_effects
from sylvia_gm.runtime._exceptions import (
    EffectExecutionError,
    EntityNotFoundRuntimeError,
    ExpressionError,
    FieldNotFoundError,
    PreConditionError,
    ProcedureDepthError,
    ProcedureNotFoundError,
    RuntimeException,
    ToolNotFoundError,
    ToolValidationError,
)
from sylvia_gm.runtime._expression_parser import parse_expression
from sylvia_gm.runtime._flag_store import FlagStore
from sylvia_gm.runtime._initiative import (
    advance_turn,
    get_current_actor,
    insert_combatant,
    roll_initiative,
)
from sylvia_gm.runtime._initiative_count_triggers import (
    InitiativeTrigger,
    InitiativeTriggerManager,
)
from sylvia_gm.runtime._rng_queue import RNGQueue, RNGQueueEntry
from sylvia_gm.runtime._runtime import RulesetRuntime
from sylvia_gm.runtime._scene_awareness import SceneAwarenessBuilder
from sylvia_gm.runtime._simulation_mode import (
    SimulationMode,
    ToolGatingManager,
    parse_simulation_modes,
)
from sylvia_gm.runtime._state_memo import StateMemoBuilder
from sylvia_gm.runtime._strictness import (
    StrictnessCheckResult,
    StrictnessLevel,
    StrictnessManager,
    parse_strictness,
    strictness_level_from_string,
    subsystem_for_tool,
)
from sylvia_gm.runtime._companion_registry import CompanionEntry, CompanionRegistry
from sylvia_gm.runtime._content_registry import ContentRegistry
from sylvia_gm.runtime._cs_loader import CSLoader
from sylvia_gm.runtime._frontmatter import (
    parse_file,
    parse_frontmatter,
    parse_item_file,
    parse_monster_file,
    parse_npc_file,
    parse_spell_file,
)
from sylvia_gm.runtime._schema_validator import validate_ruleset
from sylvia_gm.runtime._state_bridge import StateBridge
from sylvia_gm.runtime._template_resolver import resolve_template
from sylvia_gm.runtime._time_manager import (
    advance_time,
    compute_time_of_day,
    get_current_datestamp,
    get_current_time,
    get_time_for_context,
    parse_duration,
)
from sylvia_gm.runtime._types import (
    ExecutionContext,
    ProcedureDef,
    ProcedureResult,
    ToolDef,
    ToolParameter,
    ToolResult,
)

__all__ = [
    # Runtime engine
    "RulesetRuntime",
    "StateBridge",
    # Results & types
    "ToolResult",
    "ToolDef",
    "ToolParameter",
    "ProcedureResult",
    "ProcedureDef",
    "ExecutionContext",
    # Expression evaluation
    "evaluate",
    "evaluate_condition",
    "evaluate_expression",
    "parse_expression",
    # Batch 1 primitives
    "FlagStore",
    "advance_time",
    "advance_turn",
    "compute_time_of_day",
    "execute_effects",
    "get_current_actor",
    "get_current_datestamp",
    "get_current_time",
    "get_time_for_context",
    "insert_combatant",
    "parse_duration",
    "roll_initiative",
    # T148 — Distance Tracking
    "DistanceTracker",
    "GridPosition",
    "BAND_ORDER",
    "BAND_RANGES",
    "BAND_TO_FEET",
    "FEET_PER_SQUARE",
    # Batch 4 primitives
    "DeferredCall",
    "DeferredQueue",
    "DurationEntry",
    "DurationTracker",
    "InitiativeTrigger",
    "InitiativeTriggerManager",
    # Batch 5 primitives
    "Clock",
    "ClockManager",
    # Batch 6 — RNG Queue (T148)
    "RNGQueue",
    "RNGQueueEntry",
    # T149 — State Memo Injection
    "StateMemoBuilder",
    # T150 — Scene Awareness
    "SceneAwarenessBuilder",
    # T147 — Simulation Mode Architecture
    "SimulationMode",
    "ToolGatingManager",
    "parse_simulation_modes",
    # T150 — Simulation Strictness
    "StrictnessCheckResult",
    "StrictnessLevel",
    "StrictnessManager",
    "parse_strictness",
    "strictness_level_from_string",
    "subsystem_for_tool",
    # Utilities
    "resolve_template",
    "validate_ruleset",
    # T142 — CS Runtime Integration
    "CSLoader",
    "ContentRegistry",
    "parse_frontmatter",
    "parse_file",
    "parse_monster_file",
    "parse_npc_file",
    "parse_spell_file",
    "parse_item_file",
    # T47 — Companion Registry (thin — LLM controls companions)
    "CompanionRegistry",
    "CompanionEntry",
    # Exceptions
    "RuntimeException",
    "ProcedureDepthError",
    "ProcedureNotFoundError",
    "ToolNotFoundError",
    "ToolValidationError",
    "PreConditionError",
    "ExpressionError",
    "FieldNotFoundError",
    "EntityNotFoundRuntimeError",
    "EffectExecutionError",
]
