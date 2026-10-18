"""{field.path} template interpolation.

Resolves template strings like:
    "{attacker.name} swings their {weapon} at {target.name}!"
    "{entity.name} takes {amount} damage."
    "{_attack_roll.total} vs AC {target.combat.ac}"

Uses the ExecutionContext for field resolution.
"""

from __future__ import annotations

import re
from typing import Any

from sylvia_gm.runtime._types import ExecutionContext

# Pattern: {identifier.field.path} — allows dots inside braces
_TEMPLATE_RE: re.Pattern[str] = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)\}")  # noqa: E501


def resolve_template(template: str, context: ExecutionContext) -> str:
    """Interpolate {field.path} references in a template string.

    Args:
        template: Template string with {field.path} placeholders.
        context: ExecutionContext with params, vars, entities.

    Returns:
        Resolved string with all placeholders replaced.

    Scalar sequences use readable comma-separated text. Exact call-parameter references
    are handled by resolve_params and retain their original type and values.

    Example:
        >>> ctx = ExecutionContext(params={"attacker": {"name": "Sample Player"}})
        >>> resolve_template("{attacker.name} attacks!", ctx)
        'Sample Player attacks!'
    """
    def replacer(match: re.Match[str]) -> str:
        path = match.group(1)
        value = context.resolve_field(path)
        if value is None:
            return f"{{{path}}}"  # Leave unresolved if value is None
        if isinstance(value, (list, tuple)) and all(
            item is None or isinstance(item, (str, bool, int, float)) for item in value
        ):
            return ", ".join(str(item) for item in value) or "none"
        return str(value)

    return _TEMPLATE_RE.sub(replacer, template)


def resolve_params(params: dict[str, Any], context: ExecutionContext) -> dict[str, Any]:
    """Resolve all parameter values that contain template references.

    Tool calls like `params: {attacker: "attacker", target: "target", weapon: "weapon"}`
    need their values resolved against the context.

    If a value is a string matching {name}, resolve it from context.
    Otherwise pass through as-is (for literal values like 5, true, etc.).
    """
    resolved: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, str):
            # Check if it's a simple {name} reference
            match = re.fullmatch(r"\{([a-zA-Z_][a-zA-Z0-9_]*(?:\.[a-zA-Z_][a-zA-Z0-9_]*)*)\}", value)  # noqa: E501
            if match:
                path = match.group(1)
                resolved[key] = context.resolve_field(path)
            else:
                resolved[key] = resolve_template(value, context)
        else:
            resolved[key] = value
    return resolved
