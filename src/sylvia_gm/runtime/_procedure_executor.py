"""Procedure executor — triggers procedures based on events.

Flow:
1. Check depth guard (MAX_PROCEDURE_DEPTH = 10)
2. Find matching procedures with the given trigger
3. Evaluate procedure-level condition (if any)
4. Execute steps sequentially
5. Collect tool results and narrative hooks
6. Return ProcedureResult
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import Any

from sylvia_gm.runtime._condition_evaluator import evaluate_condition
from sylvia_gm.runtime._effect_executor import execute_effects
from sylvia_gm.runtime._exceptions import (
    MechanicsChoiceSuspended,
    ProcedureDepthError,
)
from sylvia_gm.runtime._types import (
    ExecutionBudget,
    ExecutionContext,
    ProcedureDef,
    ProcedureResult,
    ToolResult,
)

MAX_PROCEDURE_DEPTH: int = 10


def trigger_procedure(
    trigger_name: str,
    procedure_defs: dict[str, ProcedureDef],
    tool_defs: dict[str, Any],
    state_bridge: Any,
    ruleset_config: dict[str, Any],
    definitions: dict[str, Any],
    context: dict[str, Any] | None = None,
    combat_state: dict[str, Any] | None = None,
    budget: ExecutionBudget | None = None,
    _depth: int = 0,
) -> ProcedureResult:
    """Find and execute all procedures matching a trigger.

    Args:
        trigger_name: The trigger that fired (on_combat_start, etc.).
        procedure_defs: All procedure definitions keyed by name.
        tool_defs: All tool definitions (for nested calls).
        state_bridge: StateBridge instance.
        ruleset_config: Ruleset config layer.
        definitions: Ruleset definitions.
        context: Payload context (actor, damage_source, etc.).
        combat_state: Top-level combat state.
        _depth: Internal recursion guard.

    Returns:
        ProcedureResult with outcomes.

    Raises:
        ProcedureDepthError: If procedure chain exceeds 10 levels.
    """
    # Depth guard
    if _depth > MAX_PROCEDURE_DEPTH:
        raise ProcedureDepthError(trigger_name, _depth, MAX_PROCEDURE_DEPTH)

    active_budget = budget or ExecutionBudget()

    # Find matching procedures
    matching: list[ProcedureDef] = [
        proc for proc in procedure_defs.values() if proc.trigger == trigger_name
    ]

    if not matching:
        # Not an error — just no procedures for this trigger
        return ProcedureResult(
            procedure_name=trigger_name,
            trigger=trigger_name,
            success=True,
        )

    # Execute each matching procedure
    all_tool_results: list[ToolResult] = []
    all_logs: list[dict[str, Any]] = []
    all_narrative: dict[str, str] = {}
    skipped: list[dict[str, Any]] = []

    for proc_def in matching:
        # Check procedure-level condition
        if proc_def.condition:
            ctx = ExecutionContext(
                params=context or {},
                state_bridge=state_bridge,
                ruleset_config=ruleset_config,
                definitions=definitions,
                combat_state=combat_state or {},
                budget=active_budget,
            )
            # LOCK-2: Inject active flags for condition check
            _inject_flags_into_context(ctx, state_bridge)
            try:
                if not evaluate_condition(proc_def.condition, ctx):
                    continue
            except Exception as error:
                return ProcedureResult(
                    procedure_name=proc_def.name,
                    trigger=trigger_name,
                    success=False,
                    error=f"Procedure condition evaluation failed: {error}",
                    tool_results=all_tool_results,
                    logs=all_logs,
                    narrative_hooks=all_narrative,
                    skipped_steps=skipped,
                )

        # Build execution context for this procedure
        ctx = _build_procedure_context(
            proc_def,
            context or {},
            state_bridge,
            ruleset_config,
            definitions,
            combat_state,
            active_budget,
        )

        procedure_combat_state = ctx.combat_state

        def procedure_executor(
            nested_trigger: str,
            nested_context: dict[str, Any],
            _parent: ExecutionContext,
            _combat_state: dict[str, Any] = procedure_combat_state,
        ) -> ProcedureResult:
            return trigger_procedure(
                nested_trigger,
                procedure_defs,
                tool_defs,
                state_bridge,
                ruleset_config,
                definitions,
                context=nested_context,
                combat_state=_combat_state,
                budget=active_budget,
                _depth=_depth + 1,
            )

        ctx.procedure_executor = procedure_executor

        # Execute steps
        try:
            for step in proc_def.steps:
                # Check if step references a missing entity
                if _step_references_missing_entity(step, ctx, state_bridge):
                    skipped.append(
                        {
                            "step": step,
                            "reason": "Referenced entity no longer exists",
                        }
                    )
                    continue

                # Admission defines call as a tool reference; trigger invokes procedures.
                # Shared effect handlers propagate both kinds of nested failure.
                _exec_tool = _make_nested_executor(
                    tool_defs,
                    procedure_defs,
                    state_bridge,
                    ruleset_config,
                    definitions,
                    _depth,
                )
                execute_effects([step], ctx, tool_executor=_exec_tool)

        except (ProcedureDepthError, MechanicsChoiceSuspended):
            raise
        except Exception as e:
            return ProcedureResult(
                procedure_name=proc_def.name,
                trigger=trigger_name,
                success=False,
                error=str(e),
                tool_results=all_tool_results,
                logs=all_logs,
                narrative_hooks=all_narrative,
                skipped_steps=skipped,
            )

        # Collect logs
        logs = ctx.vars.get("_logs", [])
        all_logs.extend(logs)

        # Collect narrative
        for key, value in ctx.vars.items():
            if key.startswith("_narrative_"):
                hook_name = key[len("_narrative_") :]
                all_narrative[hook_name] = str(value) if value else ""

    return ProcedureResult(
        procedure_name=",".join(p.name for p in matching),
        trigger=trigger_name,
        success=True,
        tool_results=all_tool_results,
        logs=all_logs,
        narrative_hooks=all_narrative,
        skipped_steps=skipped,
    )


# ── Helpers ────────────────────────────────────────────────────────────────


def _build_procedure_context(
    proc_def: ProcedureDef,
    context: dict[str, Any],
    state_bridge: Any,
    ruleset_config: dict[str, Any],
    definitions: dict[str, Any],
    combat_state: dict[str, Any] | None,
    budget: ExecutionBudget,
) -> ExecutionContext:
    """Build ExecutionContext for procedure execution.

    Fetches entity snapshots for entities referenced in the context.
    Injects combat-related variables.
    """
    ctx = ExecutionContext(
        params=dict(context),
        state_bridge=state_bridge,
        ruleset_config=ruleset_config,
        definitions=definitions,
        combat_state=combat_state or {},
        current_tool=proc_def.name,
        budget=budget,
    )

    # LOCK-1: Allow procedure steps to trigger other procedures via effect handlers
    # The caller (trigger_procedure) sets the actual procedure_executor

    # Fetch entity snapshots for entity references in context
    for key in (
        "actor",
        "attacker",
        "target",
        "entity",
        "caster",
        "current_actor",
        "member",
        "combatant",
    ):
        entity_id = context.get(key)
        if entity_id and isinstance(entity_id, str) and state_bridge:
            with contextlib.suppress(Exception):
                ctx.entities[entity_id] = state_bridge.get_entity(entity_id)

    # Inject combat state variables for procedure steps
    if combat_state:
        order = combat_state.get("order", [])
        ctx.combat_state["order_length"] = len(order)

    return ctx


def _step_references_missing_entity(
    step: dict[str, Any],
    ctx: ExecutionContext,
    state_bridge: Any,
) -> bool:
    """Check if a step references an entity that no longer exists."""
    # Check set effects for entity_id.field pattern
    if "set" in step:
        field = step.get("set", {}).get("field", "")
        if field:
            parts = field.split(".", 1)
            entity_id = parts[0]
            if entity_id in ctx.entities and state_bridge:  # noqa: SIM102
                if not state_bridge.entity_exists(entity_id):
                    return True

    # Check call effects for entity params
    for effect_key in (
        "call",
        "apply_damage",
        "apply_condition",
        "remove_condition",
        "heal",
        "save_throw",
        "decrement",
        "increment",
    ):
        if effect_key in step:
            params = step.get(effect_key, {})
            if isinstance(params, dict):
                for key in ("target", "entity", "attacker", "caster", "actor"):
                    eid = params.get(key)
                    if eid and isinstance(eid, str):  # noqa: SIM102
                        if state_bridge and not state_bridge.entity_exists(eid):
                            return True

    return False


def _make_nested_executor(
    tool_defs: dict[str, Any],
    procedure_defs: dict[str, ProcedureDef],
    state_bridge: Any,
    ruleset_config: dict[str, Any],
    definitions: dict[str, Any],
    _depth: int,
) -> Callable[[str, dict[str, Any], ExecutionContext], ToolResult]:
    """Create a closure for nested tool execution within procedures."""
    from sylvia_gm.runtime._tool_executor import execute_tool

    def nested_executor(
        tool_name: str,
        params: dict[str, Any],
        parent_ctx: ExecutionContext,
    ) -> ToolResult:
        # Merge parent context state into the nested call
        sb = parent_ctx.state_bridge or state_bridge

        combat = dict(parent_ctx.combat_state)

        return execute_tool(
            tool_name,
            params,
            tool_defs,
            sb,
            ruleset_config,
            definitions,
            combat,
            tool_executor=nested_executor,
            procedure_executor=parent_ctx.procedure_executor,
            budget=parent_ctx.budget,
        )

    return nested_executor


def _inject_flags_into_context(ctx: ExecutionContext, state_bridge: Any) -> None:
    """Inject active campaign flags as context variables (LOCK-2).

    Reads flags from the _campaign entity via StateBridge and sets
    ctx.vars["_flag_X"] = True for each active flag.
    """
    if state_bridge is None:
        return
    try:
        flags = state_bridge.read_field("_campaign", "flags") or {}
        for flag_name, value in flags.items():
            if value:
                ctx.vars[f"_flag_{flag_name}"] = True
    except Exception:
        pass  # _campaign entity may not exist yet
