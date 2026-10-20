"""Effect executor — resolves and executes effect declarations.

Handlers are classified at the dispatch boundary as either neutral compositional
primitives or optional game-family compatibility conveniences. Both remain
available to packages that explicitly declare them; the classification keeps
Builder/runtime documentation from presenting compatibility helpers as the
universal Sylvia GM game model.

Each handler takes:
- effect_def: the effect dict from the ruleset YAML
- context: mutable ExecutionContext
- tool_executor: callback for nested tool calls (call effect)

Returns: None (mutations applied to context/state)
"""

from __future__ import annotations

import contextlib
import re
import secrets
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from typing import Any

from sylvia_gm.dice import (
    RollResult,
)
from sylvia_gm.dice import (
    count_successes as dice_count_successes,
)
from sylvia_gm.dice import (
    explode as dice_explode,
)
from sylvia_gm.dice import (
    hunger_roll as dice_hunger_roll,
)
from sylvia_gm.dice import (
    reroll as dice_reroll,
)
from sylvia_gm.dice import (
    roll as dice_roll,
)
from sylvia_gm.runtime._condition_evaluator import (
    evaluate_condition,
    evaluate_expression,
)
from sylvia_gm.runtime._exceptions import (
    EffectExecutionError,
    ExpressionError,
    MechanicsChoiceSuspended,
    PreConditionError,
)
from sylvia_gm.runtime._template_resolver import resolve_params, resolve_template
from sylvia_gm.runtime._types import ExecutionContext, RuntimeChoiceOption, RuntimeChoiceRequest

# Callback type: (tool_name, params, context) -> ToolResult
NestedToolExecutor = Callable[[str, dict[str, Any], ExecutionContext], Any]


def _handle_publish(body: Any, effect: dict[str, Any], ctx: ExecutionContext,
                    tool_executor: NestedToolExecutor | None) -> None:
    """Select the sole public result fields while retaining private calculation context."""
    if (
        not isinstance(body, dict) or set(body) != {"fields"}
        or not isinstance(body["fields"], dict)
    ):
        raise EffectExecutionError("publish", "Published fields must be an object")
    if len(body["fields"]) > 32:
        raise EffectExecutionError("publish", "Too many published fields")
    ctx.extras["published_result"] = _resolve_value(body["fields"], ctx)


def _handle_shuffle(body: Any, effect: dict[str, Any], ctx: ExecutionContext,
                    tool_executor: NestedToolExecutor | None) -> None:
    """Sample an unguessable permutation and retain its committed order as ordinary state."""
    from sylvia_gm.runtime._collections import collection

    if not isinstance(body, dict) or set(body) != {"collection"}:
        raise EffectExecutionError("shuffle", "A collection is required")
    destination = effect.get("store_as")
    if not isinstance(destination, str) or re.fullmatch(r"_[A-Za-z0-9_]+", destination) is None:
        raise EffectExecutionError("shuffle", "A context destination is required")
    values = deepcopy(collection(_resolve_value(body["collection"], ctx)))
    secrets.SystemRandom().shuffle(values)
    ctx.vars[destination] = values
    if ctx.budget is not None:
        ctx.budget.private_random_sampled = True


def execute_effects(
    effects: list[dict[str, Any]],
    context: ExecutionContext,
    tool_executor: NestedToolExecutor | None = None,
) -> None:
    """Execute a list of effect declarations sequentially.

    Each effect sees all mutations from previous effects immediately.
    """
    for effect in effects:
        _execute_effect(effect, context, tool_executor)


def _execute_effect(
    effect: dict[str, Any],
    context: ExecutionContext,
    tool_executor: NestedToolExecutor | None = None,
) -> None:
    """Dispatch a single effect by its key."""
    if context.budget is not None:
        try:
            context.budget.consume_operation()
        except RuntimeError as error:
            raise EffectExecutionError("budget", str(error)) from error
    # Select one primary handler independent of YAML key order. A string
    # ``condition`` is metadata for another handler unless branch keys make
    # it the flat conditional-effect form used by existing rulesets.
    condition_value = effect.get("condition")
    handler_keys = [key for key in effect if key in _EFFECT_HANDLERS]
    flat_conditional = (
        isinstance(condition_value, str)
        and ("on_true" in effect or "on_false" in effect)
        and not [key for key in handler_keys if key != "condition"]
    )
    if flat_conditional:
        effect_type = "condition"
        effect_body: Any = {
            "condition": condition_value,
            "on_true": effect.get("on_true", []),
            "on_false": effect.get("on_false", []),
        }
    else:
        if isinstance(condition_value, str):
            handler_keys = [key for key in handler_keys if key != "condition"]
        if len(handler_keys) != 1:
            raise EffectExecutionError(
                "unknown",
                "Effect declaration must contain exactly one recognized handler",
            )
        effect_type = handler_keys[0]
        effect_body = effect[effect_type]

    # Check an optional gate attached to a non-conditional handler.
    if effect_type != "condition" and condition_value:
        try:
            if not evaluate_condition(str(condition_value), context):
                return
        except ExpressionError as error:
            raise EffectExecutionError(effect_type, str(error)) from error

    handler = _EFFECT_HANDLERS[effect_type]

    try:
        handler(effect_body, effect, context, tool_executor)
    except (MechanicsChoiceSuspended, PreConditionError):
        raise
    except EffectExecutionError:
        raise
    except Exception as e:
        raise EffectExecutionError(effect_type, str(e))  # noqa: B904


# ── Handler implementations ───────────────────────────────────────────────


def _handle_dice(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Roll dice and store the RollResult in context vars.

    effect:
      dice:
        expression: "d20"
        modifier: "_atk_mod"          # optional
        advantage: "expr"             # optional boolean expression
        disadvantage: "expr"          # optional boolean expression
        critical: "expr"              # optional — double dice count
        explode: true                 # optional — exploding/acing dice
        explode_on: 6                 # optional — value that triggers explosion
        max_explosions: 10            # optional — cap on extra dice
      store_as: "_attack_roll"
    """
    if not isinstance(body, dict):
        raise EffectExecutionError("dice", "dice effect body must be a dict")

    expr = body.get("expression", "d20")
    modifier_expr = body.get("modifier")
    advantage_expr = body.get("advantage")
    disadvantage_expr = body.get("disadvantage")
    critical_expr = body.get("critical")
    explode_flag = body.get("explode")
    explode_on_raw = body.get("explode_on")
    max_explosions_raw = body.get("max_explosions", 100)
    store_as = effect.get("store_as", "_roll_result")

    # Resolve the dice expression
    expr_resolved = resolve_template(str(expr), ctx)
    modifier = 0
    if modifier_expr:
        mod_val = evaluate_expression(str(modifier_expr), ctx)
        modifier = int(mod_val) if mod_val is not None else 0

    # Determine advantage/disadvantage
    use_advantage = False
    use_disadvantage = False
    if advantage_expr:
        use_advantage = evaluate_condition(str(advantage_expr), ctx)
    if disadvantage_expr:
        use_disadvantage = evaluate_condition(str(disadvantage_expr), ctx)

    # Cancellation rule
    if use_advantage and use_disadvantage:
        use_advantage = False
        use_disadvantage = False

    # Check for critical (double dice)
    is_critical = False
    if critical_expr:
        is_critical = evaluate_condition(str(critical_expr), ctx)

    # Parse dice expression
    from sylvia_gm.dice import advantage, disadvantage

    dice_parts = _parse_dice_expr(expr_resolved)
    count = dice_parts["count"]
    sides = dice_parts["sides"]

    if is_critical:
        count *= 2  # Double dice on critical

    # Resolve explode options
    use_explode = False
    if explode_flag is not None:
        if isinstance(explode_flag, bool):
            use_explode = explode_flag
        else:
            use_explode = evaluate_condition(str(explode_flag), ctx)

    explode_on: int | None = None
    if explode_on_raw is not None:
        explode_on = _resolve_int(explode_on_raw, ctx)

    max_explosions = _resolve_int(max_explosions_raw, ctx)

    # Roll
    if use_explode:
        result = dice_explode(sides, count, modifier, explode_on, max_explosions)
    elif use_advantage:
        result = advantage(modifier)
    elif use_disadvantage:
        result = disadvantage(modifier)
    else:
        result = dice_roll(sides, count, modifier)

    # Store in context
    ctx.vars[store_as] = result


def _protect_spatial_aggregate(
    name: str, source: object, value: object, ctx: ExecutionContext,
) -> None:
    """Keep copied query objects/lists private while allowing declared scalar result values."""

    bridge = ctx.state_bridge
    if isinstance(value, (Mapping, list, tuple)) and hasattr(bridge, "register_spatial_aggregate"):
        bridge.register_spatial_aggregate(name, source)


def _handle_set(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Set a context variable or state field.

    effect:
      set:
        field: "_hit"
        value: "true"            # expression
    """
    if not isinstance(body, dict):
        raise EffectExecutionError("set", "set effect body must be a dict")

    field = resolve_template(str(body.get("field", "")), ctx)
    value_expr = body.get("value")

    if not field:
        return

    # Evaluate string expressions without erasing typed YAML literals.
    value = _resolve_value(value_expr, ctx) if value_expr is not None else None

    # Context variables (prefixed with _) are ephemeral
    if field.startswith("_"):
        ctx.vars[field] = value
        _protect_spatial_aggregate(field, value_expr, value, ctx)
    else:
        # State field write — resolve first segment as entity ID
        parts = field.split(".", 1)
        entity_id = _resolve_entity_ref(parts[0], ctx)
        field_path = parts[1] if len(parts) > 1 else ""

        if entity_id is not None and ctx.state_bridge and ctx.state_bridge.entity_exists(entity_id):
            ctx.state_bridge.write_field(entity_id, field_path, value)
            _refresh_entity_snapshots(ctx)
        else:
            # Top-level combat state
            ctx.combat_state[field_path] = value


def _handle_create_entity(
    body: Any, effect: dict[str, Any], ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Create a package-defined entity in the detached authority transaction."""
    if not isinstance(body, dict) or not hasattr(ctx.state_bridge, "create_entity"):
        raise EffectExecutionError("create_entity", "Entity creation is unavailable")
    entity_id = _resolve_value(body.get("id"), ctx)
    ctx.state_bridge.create_entity(entity_id, _resolve_value(body.get("value"), ctx))
    ctx.entities[entity_id] = ctx.state_bridge.get_entity(entity_id)


def _handle_require(
    body: Any, effect: dict[str, Any], ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Reject a mechanic after intermediate calculations without committing its writes."""
    if not isinstance(body, dict) or not isinstance(body.get("condition"), str):
        raise EffectExecutionError("require", "A condition is required")
    if not evaluate_condition(body["condition"], ctx):
        raise PreConditionError(body["condition"], str(body.get("error", "Requirement not met")))


def _handle_set_participant(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Change a live participant data field through the authority's detached bridge."""
    if not isinstance(body, dict) or not hasattr(ctx.state_bridge, "write_participant_field"):
        raise EffectExecutionError("set_participant", "Participant writes are unavailable")
    participant_id = _resolve_value(body.get("participant"), ctx)
    field = resolve_template(str(body.get("field", "")), ctx)
    if not isinstance(participant_id, str) or not field:
        raise EffectExecutionError("set_participant", "Participant identity and field are required")
    ctx.state_bridge.write_participant_field(
        participant_id, field, _resolve_value(body.get("value"), ctx),
    )
    _refresh_participant_snapshots(ctx)


def _handle_request_choice(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Suspend one deterministic mechanic until the trusted owner selects an option."""

    if ctx.budget is not None and ctx.budget.private_random_sampled:
        raise EffectExecutionError(
            "request_choice", "Commit a hidden shuffle before requesting a later choice",
        )

    if not isinstance(body, dict):
        raise EffectExecutionError("request_choice", "request body must be an object")
    choice_id = body.get("choice_id")
    prompt = body.get("prompt")
    raw_options = body.get("options")
    if isinstance(raw_options, str) and re.fullmatch(r"_[A-Za-z0-9_]+", raw_options):
        raw_options = _resolve_collection(raw_options, ctx)
    store_as = effect.get("store_as")
    if (
        not isinstance(choice_id, str)
        or not choice_id
        or not isinstance(prompt, str)
        or not prompt
        or not isinstance(raw_options, list)
        or not isinstance(store_as, str)
        or not store_as.startswith("_")
    ):
        raise EffectExecutionError("request_choice", "request declaration is invalid")
    rendered_prompt = resolve_template(prompt, ctx)
    options: list[RuntimeChoiceOption] = []
    option_ids: set[str] = set()
    for raw in raw_options:
        if not isinstance(raw, dict) or set(raw) != {"id", "label"}:
            raise EffectExecutionError("request_choice", "choice option is invalid")
        option_id = raw.get("id")
        label = raw.get("label")
        if (
            not isinstance(option_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", option_id) is None
            or not isinstance(label, str)
        ):
            raise EffectExecutionError("request_choice", "choice option is invalid")
        rendered_label = resolve_template(label, ctx)
        if option_id in option_ids or not rendered_label:
            raise EffectExecutionError("request_choice", "choice option is invalid")
        option_ids.add(option_id)
        options.append(RuntimeChoiceOption(option_id=option_id, label=rendered_label))
    if not 2 <= len(options) <= 16:
        raise EffectExecutionError("request_choice", "choice option count is out of bounds")
    if len(rendered_prompt.encode("utf-8")) > 4096 or any(
        len(option.label.encode("utf-8")) > 1024 for option in options
    ):
        raise EffectExecutionError("request_choice", "choice text is out of bounds")
    request = RuntimeChoiceRequest(
        choice_id=choice_id,
        prompt=rendered_prompt,
        options=tuple(options),
    )
    control = ctx.extras.get("mechanics_choice_resolution")
    if control is None:
        raise MechanicsChoiceSuspended(request)
    if len(control.prior_answers) >= 64:
        raise EffectExecutionError("request_choice", "choice sequence exceeds its bound")
    if control.replayed_answers < len(control.prior_answers):
        previous_request, previous_option = control.prior_answers[control.replayed_answers]
        if previous_request != request or previous_option not in option_ids:
            raise EffectExecutionError("request_choice", "prior choice resolution does not match")
        control.replayed_answers += 1
        ctx.vars[store_as] = previous_option
        return
    if control.consumed:
        if len(control.prior_answers) >= 63:
            raise EffectExecutionError("request_choice", "choice sequence exceeds its bound")
        raise MechanicsChoiceSuspended(request)
    if (
        getattr(control, "choice_id", None) != choice_id
        or getattr(control, "option_id", None) not in option_ids
        or getattr(control, "expected_request", None) != request
    ):
        raise EffectExecutionError("request_choice", "choice resolution does not match")
    control.consumed = True
    ctx.vars[store_as] = control.option_id


def _handle_apply_damage(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Deal damage to a target entity.

    effect:
      apply_damage:
        target: "target"           # entity_id parameter name
        amount: "_damage_roll.total"
    """
    if not isinstance(body, dict):
        raise EffectExecutionError("apply_damage", "body must be a dict")

    target_ref = body.get("target", "")
    amount_expr = body.get("amount", "0")

    # Resolve target entity ID
    target_id = _resolve_entity_ref(target_ref, ctx)
    if target_id is None:
        raise EffectExecutionError("apply_damage", f"Unknown target: {target_ref}")

    # Resolve damage amount
    amount = _resolve_int(amount_expr, ctx)

    # Apply through StateBridge
    if ctx.state_bridge:
        ctx.state_bridge.take_damage(target_id, amount)
        ctx.vars["_damage_amount"] = amount
        _refresh_entity_snapshots(ctx)


def _handle_apply_condition(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Add a condition to a target entity.

    effect:
      apply_condition:
        target: "target"
        condition: "prone"
        duration: "until_save"     # optional
    """
    if not isinstance(body, dict):
        raise EffectExecutionError("apply_condition", "body must be a dict")

    target_ref = body.get("target", "")
    condition = resolve_template(str(body.get("condition", "")), ctx)

    target_id = _resolve_entity_ref(target_ref, ctx)
    if target_id is None:
        raise EffectExecutionError("apply_condition", f"Unknown target: {target_ref}")

    if ctx.state_bridge:
        ctx.state_bridge.add_condition(target_id, str(condition))
        _refresh_entity_snapshots(ctx)


def _handle_remove_condition(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Remove a condition from a target entity."""
    if not isinstance(body, dict):
        raise EffectExecutionError("remove_condition", "body must be a dict")

    target_ref = body.get("target", "")
    condition = resolve_template(str(body.get("condition", "")), ctx)

    target_id = _resolve_entity_ref(target_ref, ctx)
    if target_id is None:
        raise EffectExecutionError("remove_condition", f"Unknown target: {target_ref}")

    if ctx.state_bridge:
        ctx.state_bridge.remove_condition(target_id, str(condition))
        _refresh_entity_snapshots(ctx)


def _handle_heal(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Heal a target entity."""
    if not isinstance(body, dict):
        raise EffectExecutionError("heal", "body must be a dict")

    target_ref = body.get("target", "")
    amount_expr = body.get("amount", "0")

    target_id = _resolve_entity_ref(target_ref, ctx)
    if target_id is None:
        raise EffectExecutionError("heal", f"Unknown target: {target_ref}")

    amount = _resolve_int(amount_expr, ctx)

    if ctx.state_bridge:
        ctx.state_bridge.heal(target_id, amount)
        _refresh_entity_snapshots(ctx)


def _handle_decrement(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Decrement a numeric field by an amount."""
    _handle_modify_field(body, effect, ctx, tool_executor, delta_multiplier=-1)


def _handle_increment(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Increment a numeric field by an amount."""
    _handle_modify_field(body, effect, ctx, tool_executor, delta_multiplier=1)


def _handle_modify_field(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
    delta_multiplier: int = 1,
) -> None:
    """Shared implementation for decrement/increment."""
    if not isinstance(body, dict):
        raise EffectExecutionError("modify_field", "body must be a dict")

    field = resolve_template(str(body.get("field", "")), ctx)
    amount_expr = body.get("amount", 1)
    amount = _resolve_int(amount_expr, ctx) * delta_multiplier

    if not field:
        return

    parts = field.split(".", 1)
    entity_id = _resolve_entity_ref(parts[0], ctx)
    field_path = parts[1] if len(parts) > 1 else ""

    if entity_id is not None and ctx.state_bridge and ctx.state_bridge.entity_exists(entity_id):
        ctx.state_bridge.modify_field(entity_id, field_path, amount)
        _refresh_entity_snapshots(ctx)


def _handle_conditional(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Branching logic: evaluate condition, execute on_true or on_false.

    effect:
      condition: "_attack_roll.total >= target.combat.ac"
      on_true:
        - set: ...
        - apply_damage: ...
      on_false:
        - set: ...
    """
    if not isinstance(body, dict):
        raise EffectExecutionError("condition", "body must be a dict")

    cond_expr = body.get("condition", "true")
    on_true = body.get("on_true", [])
    on_false = body.get("on_false", [])

    try:
        passed = evaluate_condition(str(cond_expr), ctx)
    except ExpressionError as e:
        raise EffectExecutionError("condition", str(e))  # noqa: B904

    if passed:
        for sub_effect in on_true:
            _execute_effect(sub_effect, ctx, tool_executor)
    else:
        for sub_effect in on_false:
            _execute_effect(sub_effect, ctx, tool_executor)


def _handle_nested_call(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Call another tool (nested execution).

    effect:
      call: death_save
      params:
        entity: "target"
      condition: "expr"          # optional
      store_as: "_death_result"  # optional
      export_context: "*"       # optional: copy the child's private result variables
    """
    tool_name: str
    params: dict[str, Any]

    if isinstance(body, str):
        tool_name = body
        params = effect.get("params", {})
    elif isinstance(body, dict):
        raw_tool_name = body.get("call", body.get("name", ""))
        if not isinstance(raw_tool_name, str):
            raise EffectExecutionError("call", "Nested tool name must be text")
        tool_name = raw_tool_name
        params = body.get("params", {})
    else:
        raise EffectExecutionError("call", "Invalid call effect format")

    if not isinstance(params, dict):
        raise EffectExecutionError("call", "Nested tool parameters must be an object")
    if not tool_name:
        raise EffectExecutionError("call", "Nested tool name is required")
    if not tool_executor:
        raise EffectExecutionError("call", "Nested tool executor not available")

    # Resolve params
    resolved_params = resolve_params(params, ctx)
    store_as = effect.get("store_as")

    # Execute nested tool under the same bounded call-depth budget.
    if ctx.budget is not None:
        try:
            ctx.budget.enter_call()
        except RuntimeError as error:
            raise EffectExecutionError("call", str(error)) from error
    try:
        result = tool_executor(tool_name, resolved_params, ctx)
    finally:
        if ctx.budget is not None:
            ctx.budget.leave_call()

    if not getattr(result, "success", False):
        if getattr(result, "failure_kind", None) == "precondition" and result.error:
            raise PreConditionError(tool_name, result.error)
        raise EffectExecutionError(
            "call",
            str(getattr(result, "error", None) or f"Nested tool '{tool_name}' failed"),
        )

    _refresh_entity_snapshots(ctx)

    export_context = effect.get("export_context")
    if export_context is not None:
        if export_context != "*" and not isinstance(export_context, dict):
            raise EffectExecutionError("call", "Nested context exports must be an object or '*'")
        nested_context = getattr(result, "context", None)
        if not isinstance(nested_context, Mapping):
            raise EffectExecutionError("call", "Nested tool result has no context to export")
        if export_context == "*":
            for name, value in nested_context.items():
                if isinstance(name, str) and name.startswith("_"):
                    ctx.vars[name] = deepcopy(value)
        else:
            for destination, source in export_context.items():
                if (
                    not isinstance(destination, str)
                    or not destination.startswith("_")
                    or not isinstance(source, str)
                    or not source.startswith("_")
                ):
                    raise EffectExecutionError(
                        "call",
                        "Nested context exports require private context-variable names",
                    )
                if source not in nested_context:
                    raise EffectExecutionError(
                        "call",
                        f"Nested tool context does not contain '{source}'",
                    )
                ctx.vars[destination] = deepcopy(nested_context[source])

    # Store result if requested
    if store_as:
        ctx.vars[store_as] = result


def _refresh_entity_snapshots(ctx: ExecutionContext) -> None:
    """Refresh every entity alias after a mutation, before later effects or result projection."""

    if ctx.state_bridge is None:
        return
    entity_parameter_names = ctx.extras.get("entity_parameter_names", set())
    for entity_id in tuple(ctx.entities):
        if not ctx.state_bridge.entity_exists(entity_id):
            continue
        snapshot = ctx.state_bridge.get_entity(entity_id)
        ctx.entities[entity_id] = snapshot
        for parameter_name, parameter_value in ctx.params.items():
            if (
                parameter_name in entity_parameter_names
                and parameter_value == entity_id
                and parameter_name in ctx.vars
            ):
                ctx.vars[parameter_name] = snapshot
    _refresh_participant_snapshots(ctx)


def _refresh_participant_snapshots(ctx: ExecutionContext) -> None:
    """Expose same-action profile writes without replacing an entity reference of the same ID."""
    if ctx.state_bridge is None:
        return
    for name in ctx.extras.get("participant_parameter_names", ()):
        participant_id = ctx.params.get(name)
        if participant_id is None:
            continue
        snapshot = ctx.state_bridge.get_participant(participant_id)
        ctx.vars[name] = snapshot
        if not ctx.state_bridge.entity_exists(participant_id):
            ctx.entities[participant_id] = snapshot


def _handle_trigger_procedure(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Trigger package-declared procedures through the shared execution budget."""

    trigger_name: object
    if isinstance(body, str):
        trigger_name = body
        context_value = effect.get("context", {})
    elif isinstance(body, dict):
        trigger_name = body.get("name", body.get("trigger", ""))
        context_value = body.get("context", {})
    else:
        raise EffectExecutionError("trigger", "Trigger effect format is invalid")
    if not isinstance(trigger_name, str) or not trigger_name:
        raise EffectExecutionError("trigger", "Trigger identity is required")
    if not isinstance(context_value, dict):
        raise EffectExecutionError("trigger", "Trigger context must be an object")
    if ctx.procedure_executor is None:
        raise EffectExecutionError("trigger", "Procedure executor is unavailable")
    resolved_context = resolve_params(context_value, ctx)
    if ctx.budget is not None:
        try:
            ctx.budget.enter_call()
        except RuntimeError as error:
            raise EffectExecutionError("trigger", str(error)) from error
    try:
        result = ctx.procedure_executor(trigger_name, resolved_context, ctx)
    finally:
        if ctx.budget is not None:
            ctx.budget.leave_call()
    if not getattr(result, "success", False):
        raise EffectExecutionError(
            "trigger",
            str(getattr(result, "error", None) or f"Procedure trigger '{trigger_name}' failed"),
        )
    store_as = effect.get("store_as")
    if isinstance(store_as, str) and store_as:
        ctx.vars[store_as] = result


def _handle_save_throw(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Force a saving throw.

    effect:
      save_throw:
        target: "target"
        ability: "dex"
        dc: "_spell_save_dc"
        on_save: "half"          # half | none | full
      store_as: "_save_result"
    """
    if not isinstance(body, dict):
        raise EffectExecutionError("save_throw", "body must be a dict")

    target_ref = body.get("target", "")
    ability = body.get("ability", "dex")
    dc_expr = body.get("dc", "10")
    body.get("on_save", "none")
    store_as = effect.get("store_as", "_save_result")

    target_id = _resolve_entity_ref(target_ref, ctx)
    if target_id is None:
        raise EffectExecutionError("save_throw", f"Unknown target: {target_ref}")

    dc = _resolve_int(dc_expr, ctx)

    # Get ability modifier
    ability_mod = 0
    if ctx.state_bridge and target_id in ctx.entities:
        entity = ctx.entities[target_id]
        abilities = entity.get("abilities", {})
        ab = abilities.get(ability, {})
        if isinstance(ab, dict):
            ability_mod = ab.get("modifier", 0)

    # Roll d20 + modifier
    from sylvia_gm.dice import check as dice_check

    check_result = dice_check(dc, ability_mod)

    ctx.vars[store_as] = check_result
    ctx.vars[f"{store_as}._save_success"] = check_result.success


def _handle_log(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Record a log entry.

    effect:
      log:
        message: "Template {actor.name}"
        level: "combat"         # combat | narrative | debug
    """
    if not isinstance(body, dict):
        return

    message = body.get("message", "")
    level = body.get("level", "combat")

    resolved = resolve_template(str(message), ctx)

    # Store in context for the caller to collect
    logs = ctx.vars.setdefault("_logs", [])
    logs.append({"message": resolved, "level": level})


def _handle_table_lookup(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Look up a value from a definitions table.

    effect:
      table_lookup:
        table: "definitions.scar_table"
        key: "_scar_roll.total"
        store_as: "_scar_description"
        default: "No scar"
    """
    if not isinstance(body, dict):
        raise EffectExecutionError("table_lookup", "body must be a dict")

    table_path = body.get("table", "")
    key_expr = body.get("key", "")
    store_as = effect.get("store_as", "_table_result")
    default = body.get("default")
    required = body.get("required", False)

    if not isinstance(store_as, str) or not store_as.startswith("_"):
        raise EffectExecutionError("table_lookup", "store_as must be a private context identifier")
    if not isinstance(required, bool):
        raise EffectExecutionError("table_lookup", "required must be a boolean")
    if required and default is not None:
        raise EffectExecutionError("table_lookup", "a required lookup cannot declare a default")

    # Resolve table from definitions
    table = _resolve_table(table_path, ctx)
    if table is None:
        if required:
            raise EffectExecutionError(
                "table_lookup", f"required table '{table_path}' does not exist"
            )
        if default is not None:
            ctx.vars[store_as] = default
        return

    # Resolve key
    key = evaluate_expression(str(key_expr), ctx)

    # Look up (support int keys for numeric tables like scar_table)
    if isinstance(key, float) and key == int(key):
        key = int(key)

    value = table.get(key)
    if value is None and required:
        raise EffectExecutionError(
            "table_lookup",
            f"required key {key!r} does not exist in table '{table_path}'",
        )
    if value is None and default is not None:
        value = default

    ctx.vars[store_as] = value


def _resolve_spatial_input(value: object, ctx: ExecutionContext) -> object:
    """Resolve nested declarative query inputs without accepting executable values."""

    if isinstance(value, Mapping):
        return {str(key): _resolve_spatial_input(item, ctx) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_resolve_spatial_input(item, ctx) for item in value]
    if isinstance(value, str):
        return resolve_params({"value": value}, ctx)["value"]
    return deepcopy(value)


def _handle_spatial_query(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Resolve one neutral physical query through the package-local owner bridge."""

    del tool_executor
    if not isinstance(body, dict):
        raise EffectExecutionError("spatial_query", "body must be an object")
    store_as = effect.get("store_as")
    if not isinstance(store_as, str) or not store_as.startswith("_"):
        raise EffectExecutionError("spatial_query", "store_as must be private")
    scene_path = body.get("scene")
    operation = body.get("operation")
    if not isinstance(scene_path, str) or not isinstance(operation, str):
        raise EffectExecutionError("spatial_query", "scene and operation are required")
    bridge = ctx.state_bridge
    if bridge is None or not hasattr(bridge, "spatial_snapshot"):
        raise EffectExecutionError("spatial_query", "authoritative spatial bridge is unavailable")
    try:
        if operation == "snapshot":
            result = bridge.spatial_snapshot(scene_path)
        else:
            query = _resolve_spatial_input(body.get("query"), ctx)
            snapshot = (
                None if "snapshot" not in body else _resolve_spatial_input(body["snapshot"], ctx)
            )
            if "snapshot" in body and snapshot is None:
                raise EffectExecutionError(
                    "spatial_query", "spatial snapshot context is unavailable"
                )
            result = bridge.spatial_query(
                scene_path,
                operation=operation,
                query=query,
                snapshot=snapshot,
            )
    except EffectExecutionError:
        raise
    except Exception as error:
        raise EffectExecutionError("spatial_query", str(error)) from error
    ctx.vars[store_as] = result
    bridge.register_spatial_private_variable(store_as)


def _handle_shot_effect(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Capture a cosmetic shot without allowing presentation failure to undo mechanics."""

    del effect, tool_executor
    bridge = ctx.state_bridge
    if bridge is None or not hasattr(bridge, "record_shot_effect"):
        return
    try:
        resolved = _resolve_spatial_input(body, ctx)
    except Exception:
        resolved = None
    bridge.record_shot_effect(resolved)


def _handle_spatial_mutation(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Apply one neutral exact-object patch through the package-local spatial bridge."""

    del tool_executor
    if not isinstance(body, dict):
        raise EffectExecutionError("spatial_mutation", "body must be an object")
    store_as = effect.get("store_as")
    if not isinstance(store_as, str) or not store_as.startswith("_"):
        raise EffectExecutionError("spatial_mutation", "store_as must be private")
    scene_path = body.get("scene")
    if not isinstance(scene_path, str):
        raise EffectExecutionError("spatial_mutation", "scene is required")
    bridge = ctx.state_bridge
    if bridge is None or not hasattr(bridge, "spatial_mutation"):
        raise EffectExecutionError(
            "spatial_mutation", "authoritative spatial bridge is unavailable"
        )
    try:
        request = _resolve_spatial_input(
            {key: value for key, value in body.items() if key != "scene"}, ctx
        )
        result = bridge.spatial_mutation(scene_path, request)
    except EffectExecutionError:
        raise
    except Exception as error:
        raise EffectExecutionError("spatial_mutation", str(error)) from error
    ctx.vars[store_as] = result
    bridge.register_spatial_private_variable(store_as)


def _handle_for_each(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Iterate over a collection and execute effects for each item.

    effect:
      for_each:
        collection: "combatants"
        as: combatant
        do:
          - call: initiative
            params:
              entity: "combatant"
    """
    if not isinstance(body, dict):
        raise EffectExecutionError("for_each", "body must be a dict")

    collection_ref = body.get("collection", "")
    var_name = body.get("as", "_item")
    do_effects = body.get("do", [])

    # Resolve collection
    collection = _resolve_collection(collection_ref, ctx)
    if not collection:
        return

    for item in collection:
        ctx.vars[var_name] = item
        _protect_spatial_aggregate(var_name, collection_ref, item, ctx)
        for sub_effect in do_effects:
            _execute_effect(sub_effect, ctx, tool_executor)


def _handle_sort(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Sort a collection by a field.

    effect:
      sort:
        collection: "combatants"
        by: "initiative_result"
        direction: desc
    """
    if not isinstance(body, dict):
        return

    collection_ref = body.get("collection", "")
    by_field = body.get("by", "")
    direction = body.get("direction", "desc")

    collection = _resolve_collection(collection_ref, ctx)
    if not collection or not by_field:
        return

    reverse = direction.lower() == "desc"

    def sort_key(item: Any) -> Any:
        if isinstance(item, dict):
            return item.get(by_field, 0)
        if hasattr(item, by_field):
            return getattr(item, by_field)
        return 0

    try:  # noqa: SIM105
        collection.sort(key=sort_key, reverse=reverse)
    except TypeError:
        pass  # Can't sort, leave as-is


def _handle_apply_ability_damage(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Damage an ability score (Cairn overflow).

    effect:
      apply_ability_damage:
        target: "target"
        ability: "str"
        amount: "_overflow"
    """
    if not isinstance(body, dict):
        raise EffectExecutionError("apply_ability_damage", "body must be a dict")

    target_ref = body.get("target", "")
    ability = body.get("ability", "str")
    amount_expr = body.get("amount", "0")

    target_id = _resolve_entity_ref(target_ref, ctx)
    if target_id is None:
        raise EffectExecutionError("apply_ability_damage", f"Unknown target: {target_ref}")

    amount = _resolve_int(amount_expr, ctx)

    if ctx.state_bridge:
        ctx.state_bridge.damage_ability(target_id, str(ability), amount)


# ── Batch 2: Dice extension handlers ──────────────────────────────────────


def _handle_count_successes(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Roll a dice pool and count successes.

    effect:
      count_successes:
        pool: "10d6"            # dice expression
        threshold: 5            # minimum for a success
        modifier: "_pool_bonus" # optional bonus successes
      store_as: "_success_count"
    """
    if not isinstance(body, dict):
        raise EffectExecutionError("count_successes", "body must be a dict")

    pool_expr = body.get("pool", "1d10")
    threshold_raw = body.get("threshold", 6)
    modifier_expr = body.get("modifier")
    store_as = effect.get("store_as", "_pool_result")

    # Parse pool expression
    pool_resolved = resolve_template(str(pool_expr), ctx)
    dice_parts = _parse_dice_expr(pool_resolved)
    count = dice_parts["count"]
    sides = dice_parts["sides"]

    threshold = _resolve_int(threshold_raw, ctx)
    modifier = 0
    if modifier_expr:
        mod_val = evaluate_expression(str(modifier_expr), ctx)
        modifier = int(mod_val) if mod_val is not None else 0

    result = dice_count_successes(sides, count, threshold, modifier)
    ctx.vars[store_as] = result


def _handle_reroll(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Reroll a previous dice result with consequences.

    effect:
      reroll:
        source: "_attack_roll"       # context var holding RollResult dict
        predicate: "ones"            # "all" | "ones" | "failures"
        threshold: 6                 # required for "failures"
        modifier: "_reroll_bonus"    # optional
        consequence:                 # optional — effects to execute
          - apply_damage:
              target: "self"
              amount: 1
      store_as: "_reroll_result"
    """
    if not isinstance(body, dict):
        raise EffectExecutionError("reroll", "body must be a dict")

    source_var = body.get("source", "_roll_result")
    predicate = body.get("predicate", "all")
    threshold_raw = body.get("threshold")
    modifier_expr = body.get("modifier")
    consequence = body.get("consequence", [])
    store_as = effect.get("store_as", "_reroll_result")

    # Retrieve previous result from context vars
    previous = ctx.vars.get(source_var)
    if previous is None:
        raise EffectExecutionError("reroll", f"Source var '{source_var}' not found")

    # Convert from dict (serialized) to RollResult if needed
    if isinstance(previous, dict):
        previous = RollResult(
            sides=previous.get("sides", 20),
            count=previous.get("count", 1),
            results=tuple(previous.get("results", [])),
            total=previous.get("total", 0),
            modifier=previous.get("modifier", 0),
        )
    if not isinstance(previous, RollResult):
        raise EffectExecutionError("reroll", f"Source '{source_var}' is not a RollResult")

    modifier = 0
    if modifier_expr:
        mod_val = evaluate_expression(str(modifier_expr), ctx)
        modifier = int(mod_val) if mod_val is not None else 0

    threshold: int | None = None
    if threshold_raw is not None:
        threshold = _resolve_int(threshold_raw, ctx)

    result = dice_reroll(previous, str(predicate), modifier, threshold)
    ctx.vars[store_as] = result

    # Execute consequences (the "push" cost)
    for cons in consequence:
        _execute_effect(cons, ctx, tool_executor)


def _handle_hunger_dice(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Roll a dice pool with hunger dice (VtM / V5 style).

    effect:
      hunger_dice:
        pool: 8              # total pool size (expression or literal)
        hunger: 2            # hunger dice count
        sides: 10            # die type (default d10)
        threshold: 6         # success threshold (default 6)
        modifier: "_hunger_mod"  # optional bonus successes
      store_as: "_hunger_result"
    """
    if not isinstance(body, dict):
        raise EffectExecutionError("hunger_dice", "body must be a dict")

    pool_raw = body.get("pool", "1")
    hunger_raw = body.get("hunger", 0)
    sides_raw = body.get("sides", 10)
    threshold_raw = body.get("threshold", 6)
    modifier_expr = body.get("modifier")
    store_as = effect.get("store_as", "_hunger_result")

    pool_size = _resolve_int(pool_raw, ctx)
    hunger = _resolve_int(hunger_raw, ctx)
    sides = _resolve_int(sides_raw, ctx)
    threshold = _resolve_int(threshold_raw, ctx)
    modifier = 0
    if modifier_expr:
        mod_val = evaluate_expression(str(modifier_expr), ctx)
        modifier = int(mod_val) if mod_val is not None else 0

    result = dice_hunger_roll(pool_size, hunger, sides, threshold, modifier)
    ctx.vars[store_as] = result


# ── Batch 1 handlers ──────────────────────────────────────────────────────


def _handle_set_flag(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Set a campaign-level flag (LOCK-2).

    effect:
      set_flag: "dragon_defeated"
      value: true
    """
    if isinstance(body, str):
        flag_name = body
        value = effect.get("value", True)
    elif isinstance(body, dict):
        flag_name = str(body.get("set_flag", body.get("flag", "")))
        value = body.get("value", True)
    else:
        raise EffectExecutionError("set_flag", "Invalid set_flag format")

    if not flag_name:
        return

    flag_store = ctx.extras.get("flag_store")
    if flag_store is None:
        # No flag store — set as context var only (ephemeral)
        ctx.vars[f"_flag_{flag_name}"] = value
        return

    flag_store.set(str(flag_name), bool(value))

    # Also inject into current ctx.vars for immediate use in same chain
    ctx.vars[f"_flag_{flag_name}"] = value


def _handle_advance_time(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Advance campaign time (LOCK-1).

    effect:
      advance_time: "10 minutes"
    """
    from sylvia_gm.runtime._time_manager import advance_time as do_advance_time

    if isinstance(body, str):
        duration = body
    elif isinstance(body, dict):
        duration = str(body.get("advance_time", body.get("duration", "1 round")))
    else:
        raise EffectExecutionError("advance_time", "Invalid advance_time format")

    duration = str(duration)

    do_advance_time(
        duration=duration,
        combat_state=ctx.combat_state,
        state_bridge=ctx.state_bridge,
        ctx=ctx,
    )


def _handle_list_push(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Push a value onto a context list variable.

    effect:
      list_push:
        list: "_pending_events"
        value: "ambush"
      store_as: "_pending_events"   # optional (defaults to list field)
    """
    if not isinstance(body, dict):
        raise EffectExecutionError("list_push", "body must be a dict")

    list_ref = str(body.get("list", ""))
    if not list_ref:
        return

    value_expr = body.get("value")
    store_as = str(effect.get("store_as", list_ref))

    # Resolve value
    value = _resolve_value(value_expr, ctx) if value_expr is not None else None

    # Get or create the list
    if list_ref.startswith("_"):
        existing = ctx.vars.get(list_ref, [])
    else:
        existing = ctx.vars.get(list_ref, [])

    # Saved authority collections are immutable; retain their members in a local copy.
    if isinstance(existing, Sequence) and not isinstance(existing, (str, bytes, bytearray, list)):
        existing = list(existing)
    if not isinstance(existing, list):
        existing = []

    existing.append(value)
    ctx.vars[store_as] = existing
    _protect_spatial_aggregate(store_as, [list_ref, value_expr], existing, ctx)


def _handle_list_pop(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Pop a value from a context list variable.

    effect:
      list_pop:
        list: "_pending_events"
      store_as: "_popped_event"
    """
    if not isinstance(body, dict):
        raise EffectExecutionError("list_pop", "body must be a dict")

    list_ref = str(body.get("list", ""))
    store_as = str(effect.get("store_as", "_popped"))

    if list_ref.startswith("_"):
        existing = ctx.vars.get(list_ref, [])
    else:
        existing = ctx.vars.get(list_ref, [])

    if isinstance(existing, Sequence) and not isinstance(existing, (str, bytes, bytearray, list)):
        existing = list(existing)
    if not isinstance(existing, list) or not existing:
        ctx.vars[store_as] = None
        return

    value = existing.pop()
    ctx.vars[list_ref] = existing
    ctx.vars[store_as] = value
    _protect_spatial_aggregate(store_as, list_ref, value, ctx)


def _handle_initiative(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Roll and set initiative order.

    effect:
      initiative:
        combatants: "combatants"         # collection reference
        expression: "d20 + combat.initiative"
      store_as: "_initiative_order"
    """
    from sylvia_gm.runtime._initiative import roll_initiative as do_initiative

    if not isinstance(body, dict):
        raise EffectExecutionError("initiative", "body must be a dict")

    combatants_ref = body.get("combatants", "combatants")
    expr = body.get("expression", body.get("roll", "d20"))
    modifier_field = body.get("modifier_field")
    modifier_minimum = body.get("modifier_minimum")
    modifier_maximum = body.get("modifier_maximum")
    required_truthy_field = body.get("required_truthy_field")
    label_field = body.get("label_field")
    store_as = effect.get("store_as", "_initiative_order")

    for option_name, option_value in (
        ("modifier_field", modifier_field),
        ("required_truthy_field", required_truthy_field),
        ("label_field", label_field),
    ):
        if option_value is not None and (
            not isinstance(option_value, str) or not option_value.strip()
        ):
            raise EffectExecutionError("initiative", f"{option_name} must be a field name")
    for option_name, option_value in (
        ("modifier_minimum", modifier_minimum),
        ("modifier_maximum", modifier_maximum),
    ):
        if option_value is not None and (
            not isinstance(option_value, int) or isinstance(option_value, bool)
        ):
            raise EffectExecutionError("initiative", f"{option_name} must be an integer")
    if (
        modifier_minimum is not None
        and modifier_maximum is not None
        and modifier_minimum > modifier_maximum
    ):
        raise EffectExecutionError("initiative", "modifier_minimum cannot exceed modifier_maximum")

    # Resolve combatants
    combatants = _resolve_collection(combatants_ref, ctx)
    if not combatants:
        ctx.vars[store_as] = []
        return

    # Extract entity IDs
    entity_ids: list[str] = []
    for c in combatants:
        if isinstance(c, str):
            entity_ids.append(c)
        elif isinstance(c, dict):
            entity_id = c.get("entity_id", c.get("id", ""))
            if isinstance(entity_id, str) and entity_id:
                entity_ids.append(entity_id)

    # Roll initiative
    results = do_initiative(
        combatants=entity_ids,
        initiative_expr=str(expr),
        state_bridge=ctx.state_bridge,
        ctx=ctx,
        modifier_field=modifier_field,
        modifier_minimum=modifier_minimum,
        modifier_maximum=modifier_maximum,
        required_truthy_field=required_truthy_field,
        label_field=label_field,
    )

    ctx.vars[store_as] = results

    # Also set combat state order if applicable
    ctx.combat_state["order"] = results
    ctx.combat_state["turn_index"] = -1  # Ready for first advance_turn


def _handle_advance_initiative(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Advance arbitrary package initiative order, optionally skipping matching entities."""

    from sylvia_gm.runtime._initiative import advance_turn

    if not isinstance(body, dict):
        raise EffectExecutionError("advance_initiative", "body must be a dict")

    skip_field = body.get("skip_field")
    if skip_field is not None and (not isinstance(skip_field, str) or not skip_field.strip()):
        raise EffectExecutionError("advance_initiative", "skip_field must be a field name")
    skip_equals = _resolve_value(body.get("skip_equals"), ctx)
    order = ctx.combat_state.get("order", [])
    if not isinstance(order, list):
        raise EffectExecutionError("advance_initiative", "combat order must be a list")

    selected: str | None = None
    for _ in range(len(order)):
        candidate = advance_turn(ctx.combat_state)
        if candidate is None:
            break
        if skip_field is None:
            selected = candidate
            break
        entity = ctx.entities.get(candidate)
        if entity is None and ctx.state_bridge:
            try:
                entity = ctx.state_bridge.get_entity(candidate)
                ctx.entities[candidate] = entity
            except Exception:
                entity = None
        value: Any = entity
        for segment in skip_field.split("."):
            if not isinstance(value, dict):
                value = None
                break
            value = value.get(segment)
        if value != skip_equals:
            selected = candidate
            break

    if selected is None:
        ctx.combat_state["current_actor"] = None
    ctx.vars[str(effect.get("store_as", "_current_actor"))] = selected


# ── Batch 3: Random Table, Scene Transition, AoE ──────────────────────────


def _handle_random_table(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Weighted random table lookup.

    effect:
      random_table:
        table: "definitions.encounter_table"
        roll: "d100"                  # dice expression or omit for uniform
        weight_field: "weight"        # field name in table rows for weighting
        store_as: "_encounter_result"

    If no roll is specified, selection is purely random weighted by
    ``weight_field``.  If a roll is specified, the table acts like a
    weighted-lookup table (d100 encounter table style).
    """
    from sylvia_gm.dice import roll as dice_roll
    from sylvia_gm.dice import weighted_pick

    if not isinstance(body, dict):
        raise EffectExecutionError("random_table", "body must be a dict")

    table_path = body.get("table", "")
    weight_field = body.get("weight_field", "weight")
    roll_expr = body.get("roll")  # optional
    store_as = effect.get("store_as", "_random_table_result")
    default = body.get("default")

    # Resolve table from definitions
    table = _resolve_table(table_path, ctx)
    if table is None:
        if default is not None:
            ctx.vars[store_as] = default
        return

    # Convert table dict to list of items (dicts with a weight_field)
    # Tables can be {key: value, ...} or {key: {weight_field: N, ...}, ...}
    items: list[dict[str, Any]] = []
    for key, value in table.items():
        if isinstance(value, dict):
            row = dict(value)
            row.setdefault(weight_field, 1)
            row.setdefault("_key", key)
            items.append(row)
        else:
            items.append({"_key": key, weight_field: 1, "value": value})

    if not items:
        if default is not None:
            ctx.vars[store_as] = default
        return

    # If a roll expression is specified, roll first
    roll_result = None
    if roll_expr is not None:
        roll_str = resolve_template(str(roll_expr), ctx)
        dice_parts = _parse_dice_expr(roll_str)
        roll_result = dice_roll(
            dice_parts["sides"],
            dice_parts["count"],
            dice_parts["modifier_delta"],
        )

    # Pick weighted item
    picked = weighted_pick(items, weight_field=weight_field, roll_result=roll_result)

    # Store result
    if picked is not None:
        ctx.vars[store_as] = picked
    elif default is not None:
        ctx.vars[store_as] = default


def _handle_transition_scene(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Transition the party to a new scene.

    effect:
      transition_scene: "forest_clearing"
      trigger_procedures: true     # default true

    Uses the SceneManager from ctx.extras["scene_manager"].
    Triggers on_enter / on_leave procedures via ctx.procedure_executor.
    """

    if isinstance(body, str):
        new_scene_id = body
    elif isinstance(body, dict):
        new_scene_id = str(body.get("transition_scene", body.get("scene", "")))
    else:
        raise EffectExecutionError("transition_scene", "Invalid transition_scene format")

    if not new_scene_id:
        return

    scene_manager = ctx.extras.get("scene_manager")
    if scene_manager is None:
        # No SceneManager — set context vars directly
        ctx.vars["_current_scene"] = new_scene_id
        ctx.vars["_current_scene_name"] = new_scene_id
        return

    scene_manager.transition_to(str(new_scene_id), ctx)


def _handle_aoe(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Area-of-effect entity resolution with do: sub-effects (LOCK-5).

    effect:
      aoe:
        shape: "cone"
        size: 30
        origin: "self"
        filter: "kind == 'hostile'"
        as: _target                  # optional loop variable (default: _aoe_target)
      do:
        - apply_damage:
            target: "{_target}"
            amount: "8d6"

    The handler resolves which entities are affected, then iterates over
    each target, setting the loop variable and executing sub-effects.
    """
    from sylvia_gm.runtime._aoe_resolver import AoESpec, resolve_aoe

    if not isinstance(body, dict):
        raise EffectExecutionError("aoe", "body must be a dict")

    aoe_spec_raw = body.get("aoe", body)
    sub_effects = body.get("do", effect.get("do", []))
    as_var = body.get("as", effect.get("as", "_aoe_target"))

    # Build AoESpec
    spec = AoESpec(
        shape=str(aoe_spec_raw.get("shape", "sphere")),
        size=aoe_spec_raw.get("size", 30),
        origin=str(aoe_spec_raw.get("origin", "self")),
        filter=aoe_spec_raw.get("filter"),
    )

    # Resolve targets
    targets = resolve_aoe(spec, ctx)

    if tool_executor is None:
        # Store targets for inspection but can't execute sub-effects
        ctx.vars["_aoe_targets"] = targets
        return

    # Iterate and execute sub-effects
    for target_id in targets:
        ctx.vars[as_var] = target_id
        for sub in sub_effects:
            _execute_effect(sub, ctx, tool_executor)


# ── Batch 4: Defer, Narrate, Initiative Count Triggers ────────────────────


def _handle_defer(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Enqueue a deferred tool call for later execution.

    effect:
      defer:
        call: "lair_action"                  # tool name
        params:                               # tool params
          target: "nearest_player"
        reason: "end_of_round"               # end_of_turn | end_of_round
        priority: 50                          # optional (lower = first)
        context_vars:                         # vars to inject at drain time
          _trigger_count: 20
      store_as: "_deferred_id"               # optional

    The DeferredQueue is resolved from ctx.extras["deferred_queue"].
    """

    if not isinstance(body, dict):
        raise EffectExecutionError("defer", "defer effect body must be a dict")

    call_name = body.get("call", body.get("tool", ""))
    if not call_name:
        raise EffectExecutionError("defer", "defer requires a 'call' field")

    params = body.get("params", {})
    reason = body.get("reason", "end_of_turn")
    priority = body.get("priority", 100)
    context_vars = body.get("context_vars", {})

    deferred_queue = ctx.extras.get("deferred_queue")
    if deferred_queue is None:
        # No queue — store the call info as context var for inspection
        import uuid

        call_id = str(uuid.uuid4())[:8]
        ctx.vars["_deferred_calls"] = ctx.vars.get("_deferred_calls", [])
        ctx.vars["_deferred_calls"].append(
            {
                "call_id": call_id,
                "tool_name": call_name,
                "params": dict(params),
                "reason": reason,
                "priority": priority,
            }
        )
        store_as = effect.get("store_as", "_deferred_id")
        ctx.vars[store_as] = call_id
        return

    # Resolve template params
    from sylvia_gm.runtime._template_resolver import resolve_params

    resolved_params = resolve_params(params, ctx)

    # Generate call ID
    import uuid

    call_id = str(uuid.uuid4())[:8]

    deferred_queue.enqueue(
        call_id=call_id,
        tool_name=str(call_name),
        params=resolved_params,
        reason=str(reason),
        priority=int(priority),
        context_vars=dict(context_vars),
    )

    store_as = effect.get("store_as", "_deferred_id")
    ctx.vars[store_as] = call_id


def _handle_narrate(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Emit a narrative directive (LOCK-6).

    effect:
      narrate:
        text: "The {attacker.name} strikes with fury!"
        style: "combat"        # combat | exploration | system | ambient
        priority: "high"       # high | normal | low

    Directives are stored in ctx.vars["_narrative_directives"] and
    collected into ToolResult.narrative_directives by _collect_narrative.
    """
    from sylvia_gm.runtime._template_resolver import resolve_template

    text: object
    if isinstance(body, str):
        text = body
        style = effect.get("style", "combat")
        priority = effect.get("priority", "normal")
    elif isinstance(body, dict):
        text = body.get("text", body.get("narrate", ""))
        style = body.get("style", "combat")
        priority = body.get("priority", "normal")
    else:
        raise EffectExecutionError("narrate", "Invalid narrate format")

    text_resolved = resolve_template(str(text), ctx)
    current_tool = ctx.current_tool or "unknown"

    directive = {
        "text": text_resolved,
        "style": str(style),
        "priority": str(priority),
        "source": f"tool:{current_tool}",
    }

    directives = ctx.vars.setdefault("_narrative_directives", [])
    directives.append(directive)


def _handle_register_initiative_trigger(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Register an initiative-count-based trigger.

    effect:
      register_initiative_trigger:
        trigger_id: "lair_20"
        count: 20
        procedure: "dragon_lair_action"
        source: "ancient_dragon"       # optional
        repeats: true                  # default true
        context_data:                  # injected into ctx.vars on fire
          _lair_target: "{nearest_player}"

    The InitiativeTriggerManager is resolved from
    ctx.extras["initiative_triggers"].
    """

    if not isinstance(body, dict):
        raise EffectExecutionError("register_initiative_trigger", "body must be a dict")

    trigger_id = body.get("trigger_id", body.get("id", ""))
    if not trigger_id:
        raise EffectExecutionError(
            "register_initiative_trigger",
            "trigger_id is required",
        )

    count_raw = body.get("count", 0)
    procedure_name = body.get("procedure", body.get("procedure_name", ""))
    if not procedure_name:
        raise EffectExecutionError(
            "register_initiative_trigger",
            "procedure is required",
        )

    source = body.get("source", "")
    repeats = body.get("repeats", True)
    context_data = body.get("context_data", {})

    try:
        count = int(count_raw)
    except (ValueError, TypeError):
        count = 0

    mgr = ctx.extras.get("initiative_triggers")
    if mgr is None:
        # No manager — store as context var for inspection
        triggers = ctx.vars.setdefault("_initiative_triggers", [])
        triggers.append(
            {
                "trigger_id": str(trigger_id),
                "count": count,
                "procedure_name": str(procedure_name),
                "source": str(source),
                "repeats": bool(repeats),
            }
        )
        return

    mgr.register(
        trigger_id=str(trigger_id),
        count=count,
        procedure_name=str(procedure_name),
        source=str(source),
        repeats=bool(repeats),
        context_data=dict(context_data),
    )


# ── Batch 5 handlers (clocks) ─────────────────────────────────────────────


def _handle_clock_create(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Create a progress/countdown clock.

    effect:
      clock_create:
        clock_id: "doom_clock"
        name: "Doom Clock"
        max_segments: 6
        thresholds:
          4: "on_doom_impending"
          6: "on_doom_arrives"
        metadata:
          source: "ancient_dragon"
    """
    if not isinstance(body, dict):
        raise EffectExecutionError("clock_create", "body must be a dict")

    clock_id = body.get("clock_id", body.get("id", ""))
    if not clock_id:
        raise EffectExecutionError("clock_create", "clock_id is required")

    name = body.get("name", clock_id)
    max_segments_raw = body.get("max_segments", 4)
    thresholds_raw = body.get("thresholds", {}) or {}

    try:
        max_segments = int(max_segments_raw)
    except (ValueError, TypeError):
        max_segments = 4

    # Resolve thresholds (values may be template expressions)
    thresholds: dict[int, str] = {}
    for k, v in thresholds_raw.items():
        try:
            key = int(k)
            thresholds[key] = resolve_template(str(v), ctx)
        except (ValueError, TypeError):
            continue

    metadata_raw = body.get("metadata", {}) or {}
    metadata = {
        k: resolve_template(str(v), ctx) if isinstance(v, str) else v
        for k, v in metadata_raw.items()
    }

    mgr = ctx.extras.get("clock_manager")
    if mgr is None:
        # No manager — store as context var for inspection
        clock_data = {
            "clock_id": str(clock_id),
            "name": str(name),
            "max_segments": max_segments,
            "filled_segments": 0,
            "thresholds": thresholds,
            "metadata": metadata,
        }
        ctx.vars[f"_clock_{clock_id}"] = clock_data
        return

    clock = mgr.create_clock(
        clock_id=str(clock_id),
        name=str(name),
        max_segments=max_segments,
        thresholds=thresholds,
        metadata=metadata,
    )

    store_as = effect.get("store_as")
    if store_as:
        ctx.vars[store_as] = clock


def _handle_clock_tick(
    body: Any,
    effect: dict[str, Any],
    ctx: ExecutionContext,
    tool_executor: NestedToolExecutor | None,
) -> None:
    """Advance (tick) a progress/countdown clock.

    effect:
      clock_tick:
        clock_id: "doom_clock"
        segments: 2           # default 1
    """
    if isinstance(body, str):
        clock_id = body
        segments = 1
    elif isinstance(body, dict):
        clock_id = str(body.get("clock_id", body.get("id", "")))
        segments_raw = body.get("segments", 1)
        try:
            segments = int(segments_raw)
        except (ValueError, TypeError):
            segments = 1
    else:
        raise EffectExecutionError("clock_tick", "Invalid clock_tick format")

    if not clock_id:
        return

    mgr = ctx.extras.get("clock_manager")
    if mgr is None:
        # No manager — operate on cached context var
        clock_key = f"_clock_{clock_id}"
        clock_data = ctx.vars.get(clock_key, {})
        if not isinstance(clock_data, dict):
            return
        old_filled = clock_data.get("filled_segments", 0)
        max_seg = clock_data.get("max_segments", 4)
        new_filled = min(max_seg, old_filled + segments)
        clock_data["filled_segments"] = new_filled
        ctx.vars[clock_key] = clock_data
        return

    triggered = mgr.tick(str(clock_id), segments=segments, ctx=ctx)

    store_as = effect.get("store_as")
    if store_as:
        ctx.vars[store_as] = triggered


# ── Helper functions ──────────────────────────────────────────────────────


def _resolve_entity_ref(ref: str, ctx: ExecutionContext) -> str | None:
    """Resolve an entity reference to an entity ID.

    If ref is a parameter name (e.g., "target"), look it up in ctx.params.
    If ref is a template (e.g., "{target}"), strip braces and resolve.
    If ref is an entity ID string, use it directly.
    """
    # Strip template braces: "{target}" → "target"
    clean_ref = ref.strip("{}")

    if clean_ref in ctx.params:
        param = ctx.params[clean_ref]
        if isinstance(param, str):
            return param
        if isinstance(param, dict) and "id" in param:
            entity_id = param["id"]
            return entity_id if isinstance(entity_id, str) else None
    # Try as direct entity ID
    if clean_ref in ctx.entities:
        return clean_ref
    # Try original ref
    if ref in ctx.params:
        param = ctx.params[ref]
        if isinstance(param, str):
            return param
    return ref  # Return as-is, caller will verify


def _resolve_value(expr: Any, ctx: ExecutionContext) -> Any:
    """Resolve expressions recursively while preserving typed YAML literals."""
    if isinstance(expr, dict):
        return {str(key): _resolve_value(value, ctx) for key, value in expr.items()}
    if isinstance(expr, list):
        return [_resolve_value(value, ctx) for value in expr]
    if not isinstance(expr, str):
        return deepcopy(expr)
    if expr == "true":
        return True
    if expr == "false":
        return False
    if expr == "null":
        return None
    try:
        result = evaluate_expression(expr, ctx)
    except ExpressionError:
        # Expression failed to evaluate — treat as literal string
        return expr
    if result is None:
        # A declared value or path may intentionally be null. Preserve it;
        # literal fallback is only for names outside the execution context.
        match = re.match(r"^([A-Za-z_][A-Za-z_0-9]*)", expr)
        root = match.group(1) if match else None
        declared = root in ctx.params or root in ctx.vars or root in ctx.entities
        scoped_path = root in {"state", "combat", "ruleset", "definitions"} and ("." in expr or "[" in expr)
        if declared or scoped_path:
            return None
        return expr
    return result


def _resolve_int(expr: Any, ctx: ExecutionContext) -> int:
    """Resolve an expression to an integer."""
    if isinstance(expr, (int, float)):
        return int(expr)
    value = evaluate_expression(str(expr), ctx)
    if value is None:
        return 0
    return int(value)


def _resolve_table(path: str, ctx: ExecutionContext) -> dict[str, Any] | None:
    """Resolve a table path like 'definitions.scar_table'."""
    parts = path.split(".")
    if parts[0] == "definitions":
        current: Any = ctx.definitions
        for part in parts[1:]:
            if isinstance(current, dict):
                current = current.get(part)
            else:
                return None
        if isinstance(current, dict):
            return current
    return None


def _resolve_collection(ref: str, ctx: ExecutionContext) -> list[Any] | None:
    """Resolve declared arrays, copying immutable dice results before iteration or sorting."""
    value = ctx.params[ref] if ref in ctx.params else ctx.vars.get(ref)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray, list)):
        return list(value)
    return value if isinstance(value, list) else None


def _parse_dice_expr(expr: str) -> dict[str, Any]:
    """Parse a dice expression like 'd20', '2d6', 'd20 + 5'.

    Returns dict with count, sides, modifier_delta.
    """
    expr = expr.strip().lower()
    modifier_delta = 0

    # Check for modifier in expression (e.g., "d20 + 5")
    if "+" in expr:
        parts = expr.split("+", 1)
        expr = parts[0].strip()
        with contextlib.suppress(ValueError, IndexError):
            modifier_delta = int(parts[1].strip())
    elif " - " in expr:
        parts = expr.split(" - ", 1)
        expr = parts[0].strip()
        with contextlib.suppress(ValueError, IndexError):
            modifier_delta = -int(parts[1].strip())

    if expr.startswith("d"):
        count = 1
        sides_str = expr[1:]
    elif "d" in expr:
        count_str, sides_str = expr.split("d", 1)
        try:
            count = int(count_str)
        except ValueError:
            count = 1
    else:
        count = 1
        sides_str = "20"

    try:
        sides = int(sides_str)
    except ValueError:
        sides = 20

    return {"count": max(1, count), "sides": max(2, sides), "modifier_delta": modifier_delta}


# ── Effect handler dispatch table ─────────────────────────────────────────

_EFFECT_HANDLERS: dict[
    str,
    Callable[[Any, dict[str, Any], ExecutionContext, NestedToolExecutor | None], None],
] = {
    "publish": _handle_publish,
    "shuffle": _handle_shuffle,
    "dice": _handle_dice,
    "set": _handle_set,
    "set_participant": _handle_set_participant,
    "create_entity": _handle_create_entity,
    "require": _handle_require,
    "request_choice": _handle_request_choice,
    "apply_damage": _handle_apply_damage,
    "apply_condition": _handle_apply_condition,
    "remove_condition": _handle_remove_condition,
    "call": _handle_nested_call,
    "trigger": _handle_trigger_procedure,
    "save_throw": _handle_save_throw,
    "heal": _handle_heal,
    "decrement": _handle_decrement,
    "increment": _handle_increment,
    "condition": _handle_conditional,
    "log": _handle_log,
    "table_lookup": _handle_table_lookup,
    "spatial_query": _handle_spatial_query,
    "spatial_mutation": _handle_spatial_mutation,
    "shot_effect": _handle_shot_effect,
    "for_each": _handle_for_each,
    "sort": _handle_sort,
    "apply_ability_damage": _handle_apply_ability_damage,
    # Batch 2: Dice extensions
    "count_successes": _handle_count_successes,
    "reroll": _handle_reroll,
    "hunger_dice": _handle_hunger_dice,
    # Batch 1: Flags, Time, Lists, Initiative
    "set_flag": _handle_set_flag,
    "advance_time": _handle_advance_time,
    "list_push": _handle_list_push,
    "list_pop": _handle_list_pop,
    "initiative": _handle_initiative,
    "advance_initiative": _handle_advance_initiative,
    # Batch 3: Random Table, Scene Transition, AoE
    "random_table": _handle_random_table,
    "transition_scene": _handle_transition_scene,
    "aoe": _handle_aoe,
    # Batch 4: Defer, Narrate, Initiative Count Triggers
    "defer": _handle_defer,
    "narrate": _handle_narrate,
    "register_initiative_trigger": _handle_register_initiative_trigger,
    # Batch 5: Clocks
    "clock_create": _handle_clock_create,
    "clock_tick": _handle_clock_tick,
}

# RULE-005: this classification is deliberately explicit. Adding a handler
# requires deciding whether it is a reusable engine primitive or optional
# game-family compatibility machinery rather than silently expanding either
# contract.
_NEUTRAL_EFFECT_KEYS: frozenset[str] = frozenset(
    {
        "publish",
        "shuffle",
        "dice",
        "set",
        "set_participant",
        "create_entity",
        "require",
        "request_choice",
        "apply_condition",
        "remove_condition",
        "call",
        "trigger",
        "decrement",
        "increment",
        "condition",
        "log",
        "table_lookup",
        "spatial_query",
        "spatial_mutation",
        "shot_effect",
        "for_each",
        "sort",
        "count_successes",
        "reroll",
        "set_flag",
        "advance_time",
        "list_push",
        "list_pop",
        "initiative",
        "advance_initiative",
        "random_table",
        "transition_scene",
        "aoe",
        "defer",
        "narrate",
        "register_initiative_trigger",
        "clock_create",
        "clock_tick",
    }
)
_COMPATIBILITY_EFFECT_KEYS: frozenset[str] = frozenset(
    {
        "apply_damage",
        "save_throw",
        "heal",
        "apply_ability_damage",
        "hunger_dice",
    }
)

if (
    _NEUTRAL_EFFECT_KEYS & _COMPATIBILITY_EFFECT_KEYS
    or frozenset(_EFFECT_HANDLERS) != _NEUTRAL_EFFECT_KEYS | _COMPATIBILITY_EFFECT_KEYS
):
    raise RuntimeError("Every effect handler must have exactly one RULE-005 classification")
