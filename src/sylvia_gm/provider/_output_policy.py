"""Application reply allowances are separate from provider model maxima."""

from typing import Any

DEFAULT_OUTPUT_ALLOWANCE = 4096


def output_allowance(model: Any, *, builder: bool = False) -> int | None:
    """Return an enforceable app allowance, or None for runtime-managed output."""
    if getattr(model, "transport", None) == "codex_app_server":
        return None
    value = getattr(model, "max_output_tokens", None)
    allowance = (
        value
        if isinstance(value, int) and not isinstance(value, bool) and value > 0
        else DEFAULT_OUTPUT_ALLOWANCE
    )
    if value is None:
        context = getattr(model, "context_window", None)
        if isinstance(context, int) and not isinstance(context, bool) and context > 0:
            allowance = min(allowance, max(1, context // 4))
    if builder:
        value = getattr(model, "builder_output_tokens", None)
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            allowance = min(allowance, value)
    return allowance
