"""Tool executor — executes a single tool from the ruleset.

Flow:
1. Look up tool definition
2. Validate parameter types and entity existence
3. Build ExecutionContext with entity snapshots
4. Evaluate pre-conditions
5. Execute effects sequentially (each sees prior mutations)
6. Return ToolResult
"""

from __future__ import annotations

import contextlib
import logging
from copy import deepcopy
from typing import Any

from sylvia_gm.runtime._condition_evaluator import evaluate_condition
from sylvia_gm.runtime._effect_executor import execute_effects
from sylvia_gm.runtime._exceptions import MechanicsChoiceSuspended, PreConditionError
from sylvia_gm.runtime._template_resolver import resolve_template
from sylvia_gm.runtime._types import (
    ExecutionBudget,
    ExecutionContext,
    ToolDef,
    ToolResult,
)

logger = logging.getLogger(__name__)


def execute_tool(
    tool_name: str,
    params: dict[str, Any],
    tool_defs: dict[str, ToolDef],
    state_bridge: Any,
    ruleset_config: dict[str, Any],
    definitions: dict[str, Any],
    combat_state: dict[str, Any] | None = None,
    tool_executor: Any = None,  # Recursive reference: callable
    extras: dict[str, Any] | None = None,
    procedure_executor: Any = None,
    budget: ExecutionBudget | None = None,
) -> ToolResult:
    """Execute a single tool against the current state.

    Args:
        tool_name: Name of the tool to execute.
        params: Parameter values keyed by name.
        tool_defs: All tool definitions from the ruleset.
        state_bridge: StateBridge instance for state access.
        ruleset_config: Ruleset config layer (features, abilities).
        definitions: Ruleset definitions (weapons, tables, etc.).
        combat_state: Top-level combat state (round, turn_index, etc.).
        tool_executor: Callable for nested tool calls.
            Signature: (tool_name, params, ExecutionContext) -> ToolResult

    Returns:
        ToolResult with success, context vars, logs, etc.
    """
    prepared = prepare_tool_context(
        tool_name, params, tool_defs, state_bridge, ruleset_config, definitions,
        combat_state, extras=extras, procedure_executor=procedure_executor, budget=budget,
    )
    if isinstance(prepared, ToolResult):
        return prepared
    ctx = prepared
    tool_def = tool_defs[tool_name]

    # 5. Execute effects
    try:
        execute_effects(
            tool_def.effects,
            ctx,
            tool_executor=tool_executor,
        )
        # T5.5: Log effects applied
        logger.debug("effects_applied tool=%s count=%d", tool_name, len(tool_def.effects))
    except MechanicsChoiceSuspended:
        raise
    except PreConditionError as error:
        return ToolResult(
            tool_name=tool_name, success=False, error=str(error), failure_kind="precondition",
        )
    except Exception as e:
        return ToolResult(
            tool_name=tool_name,
            success=False,
            error=f"Effect execution error: {e}",
            failure_kind="effect_execution",
            context=ctx.vars,
            narrative_hooks=_collect_narrative(ctx),
            narrative_directives=_collect_narrative_directives(ctx),
            logs=ctx.vars.get("_logs", []),
        )

    # 6. Package result
    return ToolResult(
        tool_name=tool_name,
        success=True,
        context=ctx.vars,
        public_context=ctx.extras.get("published_result"),
        private_random_sampled=bool(ctx.budget and ctx.budget.private_random_sampled),
        narrative_hooks=_collect_narrative(ctx),
        narrative_directives=_collect_narrative_directives(ctx),
        logs=ctx.vars.get("_logs", []),
    )


def prepare_tool_context(
    tool_name: str,
    params: dict[str, Any],
    tool_defs: dict[str, ToolDef],
    state_bridge: Any,
    ruleset_config: dict[str, Any],
    definitions: dict[str, Any],
    combat_state: dict[str, Any] | None = None,
    *,
    extras: dict[str, Any] | None = None,
    procedure_executor: Any = None,
    budget: ExecutionBudget | None = None,
) -> ExecutionContext | ToolResult:
    """Bind a tool's context and check prerequisites without invoking any effects."""

    # 1. Look up tool definition
    tool_def = tool_defs.get(tool_name)
    if tool_def is None:
        return ToolResult(
            tool_name=tool_name,
            success=False,
            error=f"Tool '{tool_name}' not found.",
            failure_kind="tool_unavailable",
        )

    # Defaults belong to every invocation, including nested and procedure calls.
    # Copy mutable defaults so one invocation cannot alter the tool declaration.
    params = {
        **{
            parameter.name: deepcopy(parameter.default)
            for parameter in tool_def.parameters
            if parameter.name not in params and (parameter.default is not None or not parameter.required)
        },
        **params,
    }

    # 2. Validate parameters
    validation_errors = _validate_params(tool_def, params, state_bridge)
    if validation_errors:
        return ToolResult(
            tool_name=tool_name,
            success=False,
            error=f"Parameter validation failed: {'; '.join(validation_errors)}",
            failure_kind="parameter_validation",
        )

    # 3. Build execution context
    ctx = _build_context(
        tool_name,
        params,
        tool_def,
        state_bridge,
        ruleset_config,
        definitions,
        combat_state,
        budget,
    )

    # LOCK-1: Set procedure executor on context
    if procedure_executor:
        ctx.procedure_executor = procedure_executor

    # LOCK-3: Inject runtime managers
    if extras:
        ctx.extras.update(extras)

    # LOCK-2: Inject active flags as context variables
    if flag_store := ctx.extras.get("flag_store"):
        for flag_name, value in flag_store.all_flags().items():
            if value:
                ctx.vars[f"_flag_{flag_name}"] = True

    # 4. Evaluate pre-conditions
    # T5.10: Inject distance tracking vars for range pre-conditions
    _inject_distance_vars(tool_def, params, ctx, state_bridge)

    for pc in tool_def.pre_conditions:
        if isinstance(pc, str):
            cond_expr = pc
            error_template = "Pre-condition failed"
        else:
            cond_expr = pc.get("condition", "")
            error_template = pc.get("error", "Pre-condition failed")

        try:
            eval_ok = evaluate_condition(str(cond_expr), ctx)
            # T5.5: Log pre-condition evaluation
            logger.debug(
                "pre_condition tool=%s cond=%s result=%s",
                tool_name,
                str(cond_expr)[:80],
                eval_ok,
            )
            if not eval_ok:
                error_msg = resolve_template(str(error_template), ctx)
                return ToolResult(
                    tool_name=tool_name,
                    success=False,
                    error=error_msg,
                    failure_kind="precondition",
                    context=ctx.vars,
                    narrative_hooks=_collect_narrative(ctx),
                    narrative_directives=_collect_narrative_directives(ctx),
                    logs=ctx.vars.get("_logs", []),
                )
        except Exception as e:
            return ToolResult(
                tool_name=tool_name,
                success=False,
                error=f"Pre-condition evaluation error: {e}",
                failure_kind="precondition_evaluation",
            )

    return ctx


# ── Context building ──────────────────────────────────────────────────────


def _build_context(
    tool_name: str,
    params: dict[str, Any],
    tool_def: ToolDef,
    state_bridge: Any,
    ruleset_config: dict[str, Any],
    definitions: dict[str, Any],
    combat_state: dict[str, Any] | None,
    budget: ExecutionBudget | None,
) -> ExecutionContext:
    """Build the ExecutionContext for a tool invocation.

    Fetches entity snapshots for all entity_id parameters.
    Loads weapon info for weapon parameters.
    """
    ctx = ExecutionContext(
        params=dict(params),
        state_bridge=state_bridge,
        ruleset_config=ruleset_config,
        definitions=definitions,
        combat_state=combat_state if combat_state is not None else {},
        current_tool=tool_name,
        budget=budget or ExecutionBudget(),
    )
    if state_bridge is not None and state_bridge.entity_exists("state"):
        ctx.entities["state"] = state_bridge.get_entity("state")

    # LOCK-3: Inject runtime managers from extras if provided via execute_tool
    # (extras dict is set by the caller, e.g. RulesetRuntime)

    # Keep named entity and participant references distinct even when they share an ID.
    entity_parameter_names: set[str] = set()
    participant_parameter_names: set[str] = set()
    for param_def in tool_def.parameters:
        if param_def.from_state and hasattr(state_bridge, "register_state_parameter"):
            state_bridge.register_state_parameter(param_def.name, param_def.from_state)
        if param_def.resolve == "entity":
            entity_parameter_names.add(param_def.name)
            entity_id = params.get(param_def.name)
            if entity_id and isinstance(entity_id, str) and state_bridge:
                with contextlib.suppress(Exception):
                    snapshot = state_bridge.get_entity(entity_id)
                    ctx.entities[entity_id] = snapshot
                    ctx.vars[param_def.name] = snapshot
                    ctx.vars[f"_{param_def.name}_id"] = entity_id
        elif param_def.resolve == "participant":
            participant_parameter_names.add(param_def.name)
            participant_id = params.get(param_def.name)
            if participant_id and isinstance(participant_id, str) and state_bridge:
                with contextlib.suppress(Exception):
                    snapshot = state_bridge.get_participant(participant_id)
                    ctx.entities[participant_id] = snapshot
                    ctx.vars[param_def.name] = snapshot
                    ctx.vars[f"_{param_def.name}_id"] = participant_id
    ctx.extras["entity_parameter_names"] = entity_parameter_names
    ctx.extras["participant_parameter_names"] = participant_parameter_names

    # Also fetch entities referenced by name (common params: attacker, target, entity, caster, etc.)
    entity_param_names = {
        "attacker",
        "target",
        "entity",
        "actor",
        "caster",
        "helper",
        "ally",
        "against_target",
    }  # noqa: E501
    for name in entity_param_names:
        entity_id = params.get(name)
        if entity_id and isinstance(entity_id, str) and state_bridge:  # noqa: SIM102
            if entity_id not in ctx.entities:
                with contextlib.suppress(Exception):
                    ctx.entities[entity_id] = state_bridge.get_entity(entity_id)

    # Load weapon info from definitions
    weapon_name = params.get("weapon")
    if weapon_name and isinstance(weapon_name, str):
        weapons = (definitions or {}).get("weapons", {})
        weapon_info = weapons.get(weapon_name)
        if weapon_info:
            params["weapon_info"] = weapon_info
        ctx.vars["weapon_info"] = weapon_info or {}

    offhand = params.get("offhand_weapon")
    if offhand and isinstance(offhand, str):
        weapons = (definitions or {}).get("weapons", {})
        weapon_info = weapons.get(offhand)
        if weapon_info:
            params["offhand_weapon_info"] = weapon_info
            ctx.vars["offhand_weapon_info"] = weapon_info or {}

    return ctx


# ── Parameter validation ──────────────────────────────────────────────────


def _validate_params(
    tool_def: ToolDef,
    params: dict[str, Any],
    state_bridge: Any,
) -> list[str]:
    """Validate tool parameters against the tool definition.

    Returns list of error strings (empty = valid).
    """
    errors: list[str] = []

    for param_def in tool_def.parameters:
        name = param_def.name
        value = params.get(name)

        # Required check
        if param_def.required and (value is None or value == ""):
            errors.append(f"Missing required parameter '{name}'")
            continue

        if value is None:
            continue

        # Type validation
        ptype = param_def.type
        resolve = param_def.resolve

        if resolve == "entity":
            if not isinstance(value, str):
                errors.append(f"Parameter '{name}' must be an entity ID string")
            elif state_bridge and not state_bridge.entity_exists(value):
                errors.append(f"Entity '{value}' not found for parameter '{name}'")

        elif resolve == "participant":
            if not isinstance(value, str):
                errors.append(f"Parameter '{name}' must be a participant ID string")
            elif state_bridge and not state_bridge.participant_exists(value):
                errors.append(f"Participant '{value}' not found for parameter '{name}'")

        elif ptype == "string":
            if not isinstance(value, str):
                errors.append(f"Parameter '{name}' must be a string")

        elif ptype == "integer":
            if not isinstance(value, (int, float)):
                errors.append(f"Parameter '{name}' must be an integer")
            elif isinstance(value, float) and value != int(value):
                errors.append(f"Parameter '{name}' must be an integer, got float")

        elif ptype == "number":
            if not isinstance(value, (int, float)):
                errors.append(f"Parameter '{name}' must be a number")

        elif ptype == "boolean":
            if not isinstance(value, bool):
                errors.append(f"Parameter '{name}' must be a boolean")

        elif ptype == "object":
            if not isinstance(value, dict):
                errors.append(f"State parameter '{name}' must be an object")

        elif ptype == "enum":
            enum_values = param_def.enum_values or []
            if value not in enum_values:
                options = ", ".join(enum_values)
                errors.append(f"Parameter '{name}' must be one of: {options}")

        elif ptype == "entity_id_list":
            if not isinstance(value, list):
                errors.append(f"Parameter '{name}' must be a list of entity IDs")
            else:
                all_strings = all(isinstance(eid, str) for eid in value)
                if not all_strings:
                    errors.append(f"Parameter '{name}' must contain only entity ID strings")
                if param_def.min_items is not None and len(value) < param_def.min_items:
                    errors.append(
                        f"Parameter '{name}' must contain at least {param_def.min_items} items"
                    )
                if param_def.max_items is not None and len(value) > param_def.max_items:
                    errors.append(
                        f"Parameter '{name}' must contain at most {param_def.max_items} items"
                    )
                if param_def.unique_items and all_strings and len(set(value)) != len(value):
                    errors.append(f"Parameter '{name}' must contain unique items")
                if resolve == "entity_list" and state_bridge:
                    for eid in value:
                        if isinstance(eid, str) and not state_bridge.entity_exists(eid):
                            errors.append(f"Entity '{eid}' not found in parameter '{name}'")

        elif ptype == "object":
            if not isinstance(value, dict):
                errors.append(f"Parameter '{name}' must be an object")

        elif ptype == "array":
            if not isinstance(value, list):
                errors.append(f"Parameter '{name}' must be a list")
            else:
                if param_def.min_items is not None and len(value) < param_def.min_items:
                    errors.append(
                        f"Parameter '{name}' must contain at least {param_def.min_items} items"
                    )
                if param_def.max_items is not None and len(value) > param_def.max_items:
                    errors.append(
                        f"Parameter '{name}' must contain at most {param_def.max_items} items"
                    )
                if param_def.unique_items:
                    try:
                        unique_count = len({repr(item) for item in value})
                    except Exception:  # pragma: no cover - JSON values always have repr
                        unique_count = len(value)
                    if unique_count != len(value):
                        errors.append(f"Parameter '{name}' must contain unique items")

        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if param_def.minimum is not None and value < param_def.minimum:
                errors.append(f"Parameter '{name}' must be at least {param_def.minimum}")
            if param_def.maximum is not None and value > param_def.maximum:
                errors.append(f"Parameter '{name}' must be at most {param_def.maximum}")

    return errors


# ── Distance tracking injection ───────────────────────────────────────────


def _inject_distance_vars(
    tool_def: ToolDef,
    params: dict[str, Any],
    ctx: ExecutionContext,
    state_bridge: Any,
) -> None:
    """Inject distance-tracking context variables for range pre-conditions.

    Reads tool.range from ToolDef and sets ctx.vars entries:
        tool_range_type: "band" | "grid" | None
        tool_range_value: "close" | 30 | None
        target_band: The target entity's current band ("close" etc.)

    If tool.range is None, no injection happens.
    If target entity has no position, target_band is set to None.
    """
    tool_range = tool_def.range
    if not tool_range:
        return

    range_type = tool_range.get("type")
    range_value = tool_range.get("value")
    ctx.vars["tool_range_type"] = range_type
    ctx.vars["tool_range_value"] = range_value

    # Resolve target entity's current band
    target_id = params.get("target")
    if target_id and isinstance(target_id, str) and state_bridge:
        with contextlib.suppress(Exception):
            pos = state_bridge.get_position(target_id)
            if pos and pos.get("band"):
                ctx.vars["target_band"] = pos["band"]
                return
    ctx.vars["target_band"] = None


# ── Narrative collection ──────────────────────────────────────────────────


def _collect_narrative(ctx: ExecutionContext) -> dict[str, str]:
    """Collect narrative hooks and directives from context.

    Hooks are stored as narrative_hooks dict (unchanged).
    Directives are stored as narrative_directives list (LOCK-6).

    Returns:
        Dict with 'hooks' (dict) and 'directives' (list).
    """
    hooks: dict[str, str] = {}
    for key, value in ctx.vars.items():
        if key.startswith("_narrative_"):
            hook_name = key[len("_narrative_") :]
            hooks[hook_name] = str(value) if value else ""
    return hooks


def _collect_narrative_directives(ctx: ExecutionContext) -> list[dict[str, Any]]:
    """Collect narrative directives from context (LOCK-6).

    Directives are stored in ctx.vars["_narrative_directives"] by
    the _handle_narrate effect handler.
    """
    return list(ctx.vars.get("_narrative_directives", []))
