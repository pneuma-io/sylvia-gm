"""Sandboxed AST evaluator for expression evaluation.

Walks the AST produced by _expression_parser.py and evaluates
against an ExecutionContext. No eval(), no exec().

Supports:
- Arithmetic (+,-,*,//,%,**)
- Comparison (==,!=,>=,<=,>,<)
- Logic (and, or, not)
- Membership (in, not in, contains)
- Field path resolution (dot notation + array indexing)
- Whitelisted functions (max, min, abs, floor, ceil, len, merge)
"""

from __future__ import annotations

from typing import Any

from sylvia_gm.runtime._exceptions import ExpressionError, FieldNotFoundError
from sylvia_gm.runtime._expression_parser import (
    _SANDBOXED_FUNCTIONS,
    ASTNode,
    BinaryOpNode,
    FieldAccessNode,
    FunctionCallNode,
    LiteralNode,
    UnaryOpNode,
)
from sylvia_gm.runtime._types import ExecutionContext


def evaluate(node: ASTNode, context: ExecutionContext) -> Any:
    """Recursively evaluate an AST node against the execution context.

    Args:
        node: AST node from _expression_parser.parse_expression().
        context: ExecutionContext with params, vars, entities, etc.

    Returns:
        The evaluated value (int, float, bool, str, list, dict, None).

    Raises:
        ExpressionError: On type errors, missing fields, or other issues.
    """
    if isinstance(node, LiteralNode):
        return node.value

    if isinstance(node, FieldAccessNode):
        return _eval_field_access(node, context)

    if isinstance(node, UnaryOpNode):
        return _eval_unary(node, context)

    if isinstance(node, BinaryOpNode):
        return _eval_binary(node, context)

    if isinstance(node, FunctionCallNode):
        return _eval_function(node, context)

    raise ExpressionError(f"Unknown AST node type: {type(node).__name__}")


# ── Field access ──────────────────────────────────────────────────────────


def _eval_field_access(node: FieldAccessNode, ctx: ExecutionContext) -> Any:
    """Resolve a field path like attacker.combat.hp or _attack_roll.results[0]."""
    value: Any = None

    for i, (name, index_expr) in enumerate(node.segments):
        if i == 0:  # noqa: SIM108
            # First segment: resolve root from context
            value = _resolve_root(name, ctx)
        else:
            # Subsequent segments: traverse into current value
            value = _traverse_field(value, name)

        # Apply array or mapping index if present
        if index_expr is not None:
            idx = evaluate(index_expr, ctx)
            try:
                if isinstance(value, (list, tuple)):
                    idx_int = int(idx) if isinstance(idx, float) else idx
                    if not isinstance(idx_int, int):
                        raise ExpressionError(
                            f"Index must be integer, got {type(idx).__name__} ({idx!r})"
                        )
                    value = value[idx_int]
                elif isinstance(value, str):
                    idx_int = int(idx) if isinstance(idx, float) else idx
                    if not isinstance(idx_int, int):
                        raise ExpressionError(
                            f"String index must be integer, got {type(idx).__name__}"
                        )
                    value = value[idx_int]
                elif isinstance(value, dict):
                    if not isinstance(idx, (str, int)) or isinstance(idx, bool):
                        raise ExpressionError(
                            f"Mapping index must be a string or integer, got {type(idx).__name__}"
                        )
                    if idx not in value:
                        raise FieldNotFoundError(
                            f"Mapping key {idx!r} is unavailable for field "
                            f"'{'.'.join(segment for segment, _ in node.segments)}'"
                        )
                    value = value[idx]
                else:
                    raise ExpressionError(f"Cannot index into {type(value).__name__} ({value!r})")
            except IndexError:
                raise FieldNotFoundError(  # noqa: B904
                    f"Index {idx!r} out of range for field "
                    f"'{'.'.join(s for s, _ in node.segments)}'"
                )

    return value


def _resolve_root(name: str, ctx: ExecutionContext) -> Any:
    """Resolve the root identifier against the execution context.

    Resolution order (plan §6.7):
    1. Tool parameters (attacker, target, weapon, etc.)
    2. Context variables (_attack_roll, _damage_roll, etc.)
    3. Top-level combat state (if name == 'combat', 'combatants')
    4. Entity state (entity ID → entity dict)
    5. Ruleset config (ruleset.*)
    6. Definitions (definitions.*)

    Also auto-resolves: if a param value is a string matching an entity ID,
    returns the entity dict.
    """
    # 1. Tool parameters
    if name in ctx.params:
        if name in ctx.vars:
            return ctx.vars[name]
        value = ctx.params[name]
        # Auto-resolve entity IDs to entity dicts
        if isinstance(value, str) and value in ctx.entities:
            return ctx.entities[value]
        return value

    # 2. Context variables
    if name in ctx.vars:
        return ctx.vars[name]

    # 3. Top-level combat state
    if name == "combat":
        return ctx.combat_state

    if name in ("combatants", "party_members"):
        return ctx.params.get(name)

    # 4. Entity state
    if name in ctx.entities:
        return ctx.entities[name]

    # 5. Ruleset config
    if name == "ruleset":
        return ctx.ruleset_config

    # 6. Definitions
    if name == "definitions":
        return ctx.definitions

    return None


def _traverse_field(value: Any, field: str) -> Any:
    """Traverse into a nested field on a value (dict key, object attr, list idx)."""
    if value is None:
        return None

    if isinstance(value, dict):
        return value.get(field)

    if isinstance(value, (list, tuple)):
        try:
            idx = int(field)
            return value[idx]
        except (ValueError, IndexError):
            return None

    # Handle objects with attributes (e.g., RollResult)
    if hasattr(value, field):
        return getattr(value, field)

    return None


# ── Unary operators ───────────────────────────────────────────────────────


def _eval_unary(node: UnaryOpNode, ctx: ExecutionContext) -> Any:
    operand = evaluate(node.operand, ctx)
    op = node.operator

    if op == "-":
        if isinstance(operand, bool):
            raise ExpressionError(f"Cannot negate boolean value ({operand!r})")
        if isinstance(operand, str):
            raise ExpressionError(f"Cannot negate string value ({operand!r})")
        if operand is None:
            raise ExpressionError("Cannot negate null")
        return -operand

    if op == "not":
        return not _to_bool(operand)

    raise ExpressionError(f"Unknown unary operator: {op!r}")


# ── Binary operators ──────────────────────────────────────────────────────


def _eval_binary(node: BinaryOpNode, ctx: ExecutionContext) -> Any:
    op = node.operator

    # Short-circuit logic operators
    if op == "and":
        return _to_bool(evaluate(node.left, ctx)) and _to_bool(evaluate(node.right, ctx))

    if op == "or":
        return _to_bool(evaluate(node.left, ctx)) or _to_bool(evaluate(node.right, ctx))

    # Eager evaluation for everything else
    left = evaluate(node.left, ctx)
    right = evaluate(node.right, ctx)

    # Arithmetic
    if op == "+":
        _check_numeric_op(op, left, right)
        return left + right

    if op == "-":
        _check_numeric_op(op, left, right)
        return left - right

    if op == "*":
        _check_numeric_op(op, left, right)
        return left * right

    if op == "//":
        _check_numeric_op(op, left, right)
        if right == 0:
            raise ExpressionError("Division by zero")
        return int(left // right)

    if op == "%":
        _check_numeric_op(op, left, right)
        if right == 0:
            raise ExpressionError("Modulo by zero")
        return left % right

    if op == "**":
        _check_numeric_op(op, left, right)
        try:
            return left**right
        except OverflowError:
            raise ExpressionError(f"Exponentiation overflow: {left} ** {right}")  # noqa: B904

    # Comparison
    if op in ("==", "!=", ">=", "<=", ">", "<"):
        return _eval_comparison(op, left, right)

    # Membership
    if op == "in":
        return _eval_membership(left, right)

    if op == "not_in":
        return not _eval_membership(left, right)

    if op == "contains":
        return _eval_contains(left, right)

    raise ExpressionError(f"Unknown binary operator: {op!r}")


def _eval_comparison(op: str, left: Any, right: Any) -> bool:
    """Compare two values."""
    # null comparisons
    if left is None and right is None:
        return op == "=="
    if left is None or right is None:
        return op == "!="

    # Bool vs everything else — treat as false
    if isinstance(left, bool) or isinstance(right, bool):
        if isinstance(left, bool) and isinstance(right, bool):
            if op == "==":
                return left == right
            if op == "!=":
                return left != right
        raise ExpressionError(
            f"Cannot compare bool with {type(right).__name__ if isinstance(left, bool) else type(left).__name__}"  # noqa: E501
        )

    # String comparisons
    if isinstance(left, str) or isinstance(right, str):
        if not isinstance(left, str) or not isinstance(right, str):
            raise ExpressionError(
                f"Cannot compare {type(left).__name__} with {type(right).__name__}"
            )
        if op == "==":
            return left == right
        if op == "!=":
            return left != right
        raise ExpressionError(f"Cannot use '{op}' with strings")

    # Numeric comparisons
    try:
        lf = float(left)
        rf = float(right)
    except (TypeError, ValueError):
        raise ExpressionError(  # noqa: B904
            f"Cannot compare {type(left).__name__} ({left!r}) "
            f"with {type(right).__name__} ({right!r})"
        )

    if op == "==":
        return lf == rf
    if op == "!=":
        return lf != rf
    if op == ">=":
        return lf >= rf
    if op == "<=":
        return lf <= rf
    if op == ">":
        return lf > rf
    if op == "<":
        return lf < rf

    return False


def _eval_membership(left: Any, right: Any) -> bool:
    """Check if left is in right."""
    if right is None:
        return False
    if isinstance(right, str) and isinstance(left, str):
        return left in right
    if isinstance(right, (list, tuple, set, frozenset, dict)):
        return left in right
    raise ExpressionError(f"Right operand of 'in' must be a collection, got {type(right).__name__}")


def _eval_contains(left: Any, right: Any) -> bool:
    """Check if left contains right."""
    if left is None:
        return False
    if isinstance(left, str) and isinstance(right, str):
        return right in left
    if isinstance(left, (list, tuple, set, frozenset, dict)):
        return right in left
    raise ExpressionError(
        f"Left operand of 'contains' must be a string or collection, got {type(left).__name__}"
    )


# ── Functions ─────────────────────────────────────────────────────────────


def _eval_function(node: FunctionCallNode, ctx: ExecutionContext) -> Any:
    """Evaluate a whitelisted function call."""
    func = _SANDBOXED_FUNCTIONS.get(node.name)
    if func is None:
        raise ExpressionError(
            f"Function '{node.name}' is not available. "
            f"Available: {', '.join(sorted(_SANDBOXED_FUNCTIONS))}."
        )

    args = [evaluate(arg, ctx) for arg in node.args]

    # Validate arg types
    for i, arg in enumerate(args if node.name not in {"len", "merge"} else ()):
        if arg is None:
            raise ExpressionError(f"Argument {i + 1} to {node.name}() is null")
        if isinstance(arg, str):
            raise ExpressionError(f"Argument {i + 1} to {node.name}() is a string, expected number")
        if isinstance(arg, bool):
            raise ExpressionError(
                f"Argument {i + 1} to {node.name}() is a boolean, expected number"
            )

    try:
        return func(*args)
    except Exception as e:
        raise ExpressionError(  # noqa: B904
            f"Error calling {node.name}(): {e}"
        )


# ── Helpers ───────────────────────────────────────────────────────────────


def _to_bool(value: Any) -> bool:
    """Convert a value to boolean for logical operations.

    Numbers: 0 → False, non-zero → True
    Strings: "" → False, non-empty → True
    None: False
    Lists/dicts: empty → False, non-empty → True
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return len(value) > 0
    if isinstance(value, (list, tuple, set, frozenset, dict)):
        return len(value) > 0
    return True  # Unknown types → truthy


def _check_numeric_op(op: str, left: Any, right: Any) -> None:
    """Raise if either operand is not numeric (or bool)."""
    if isinstance(left, bool) or isinstance(right, bool):
        raise ExpressionError(f"Cannot use '{op}' with boolean values")
    if isinstance(left, str) or isinstance(right, str):
        raise ExpressionError(f"Cannot use '{op}' with strings")
    if left is None or right is None:
        raise ExpressionError(f"Cannot use '{op}' with null")


def evaluate_expression(expr_str: str, context: ExecutionContext) -> Any:
    """Parse and evaluate an expression string against a context.

    Convenience wrapper that combines parsing and evaluation.

    Args:
        expr_str: Expression string (e.g., "attacker.combat.hp > 0").
        context: ExecutionContext with params, vars, entities.

    Returns:
        Evaluated result (bool, int, float, str, etc.).

    Raises:
        ExpressionError: On parse or evaluation failures.
    """
    from sylvia_gm.runtime._expression_parser import parse_expression

    node = parse_expression(expr_str)
    return evaluate(node, context)


def evaluate_condition(expr_str: str, context: ExecutionContext) -> bool:
    """Evaluate a condition expression, always returning a boolean.

    This is the primary entry point for evaluating pre-conditions
    and condition clauses in effects.

    Args:
        expr_str: Condition string.
        context: ExecutionContext.

    Returns:
        True if condition passes, False otherwise.

    Raises:
        ExpressionError: On parse or evaluation failures.
    """
    result = evaluate_expression(expr_str, context)
    return _to_bool(result)
