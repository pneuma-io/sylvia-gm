"""Deep merge utility for configuration dictionaries.

Rules:
- Dicts: deep-merge (user keys override defaults, missing keys retain defaults)
- Lists: replace entirely (not extend)
- None/Null: remove the key
- Scalars: replace
"""

from __future__ import annotations

from typing import Any


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Deep-merge override into base.

    Lists and null values replace rather than extend.
    Returns a new dict; does not modify inputs.
    """
    result = dict(base)
    for key, value in override.items():
        if value is None:
            result.pop(key, None)
        elif isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result
