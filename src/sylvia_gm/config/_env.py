"""Environment variable mapping for configuration overrides.

SYLVIA_GM_PROVIDERS__OPENCODE_GO__API_KEY → providers.opencode_go.api_key
"""

from __future__ import annotations

import json
import os
from typing import Any


def apply_env_overrides(
    config: dict[str, Any], prefix: str = "SYLVIA_GM_"
) -> dict[str, Any]:
    """Apply environment variable overrides to a config dictionary.

    Env vars with the given prefix are mapped to nested dict keys
    using ``__`` as the separator.  Segments are lowercased.

    Returns a new dict (does not modify the input).
    """
    result = _deep_copy_dict(config)

    for env_name, env_value in os.environ.items():
        if not env_name.startswith(prefix):
            continue

        # Strip prefix and split on __, lowercasing each segment
        key_path_str = env_name[len(prefix) :]
        key_parts = [part.lower() for part in key_path_str.split("__")]

        if not key_parts or key_parts == [""]:
            continue

        parsed_value = _coerce_value(env_value, result, key_parts)
        _set_nested(result, key_parts, parsed_value)

    return result


def _deep_copy_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Deep-copy a dict of plain values (no circular references)."""
    result: dict[str, Any] = {}
    for k, v in d.items():
        if isinstance(v, dict):
            result[k] = _deep_copy_dict(v)
        elif isinstance(v, list):
            result[k] = list(v)
        else:
            result[k] = v
    return result


def _coerce_value(
    env_value: str, config: dict[str, Any], key_parts: list[str]
) -> Any:
    """Coerce an env-var string to the expected type.

    Heuristics:
    * Values that look like JSON arrays/objects are parsed as JSON.
    * Otherwise the type of any existing value at the target path is used
      to decide between bool / int / float / str.
    """
    stripped = env_value.strip()

    # JSON-like values
    if stripped.startswith(("[", "{")):
        try:
            return json.loads(env_value)
        except json.JSONDecodeError:
            pass

    # Infer from existing config value at the same path
    existing: Any = config
    for part in key_parts[:-1]:
        if isinstance(existing, dict) and part in existing:
            existing = existing[part]
        else:
            return env_value

    last_key = key_parts[-1]
    if isinstance(existing, dict) and last_key in existing:
        existing_val = existing[last_key]
        if isinstance(existing_val, bool):
            return env_value.lower() in ("true", "1", "yes")
        elif isinstance(existing_val, int):
            return int(env_value)
        elif isinstance(existing_val, float):
            return float(env_value)

    return env_value


def _set_nested(d: dict[str, Any], key_parts: list[str], value: Any) -> None:
    """Set a value in a nested dict, creating intermediate dicts as needed."""
    for part in key_parts[:-1]:
        if part not in d or not isinstance(d[part], dict):
            d[part] = {}
        d = d[part]
    d[key_parts[-1]] = value
