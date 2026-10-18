"""${VAR} resolution for configuration values.

Raises ConfigError on unresolved variables.
Supports $$ escaping for literal ${VAR}.
"""

from __future__ import annotations

import os
import re
from typing import Any


class ConfigError(Exception):
    """Configuration error raised for invalid or missing configuration."""


_VAR_PATTERN = re.compile(r"(?<!\$)\$\{([A-Za-z_][A-Za-z0-9_]*)\}")

# Placeholder used internally to protect escaped $${ sequences
_ESCAPE_PLACEHOLDER = "\x00ESC_DOLLAR_BRACE\x00"


def resolve_vars(value: Any, source: str = "config") -> Any:
    """Recursively resolve ${VAR} references in strings.

    Args:
        value: Value to resolve (str, dict, list, or scalar).
        source: Description of where this value came from (for error messages).

    Returns:
        Value with all ${VAR} resolved.

    Raises:
        ConfigError: If a referenced variable is not set in the environment.
    """
    if isinstance(value, str):
        return _resolve_string(value, source)
    elif isinstance(value, dict):
        return {k: resolve_vars(v, f"{source}.{k}") for k, v in value.items()}
    elif isinstance(value, list):
        return [resolve_vars(item, f"{source}[{i}]") for i, item in enumerate(value)]
    return value


def _resolve_string(value: str, source: str) -> str:
    """Resolve ${VAR} in a single string value.

    Escaped sequences ($${VAR}) are left as literal ${VAR}.
    """
    # Protect escaped $${ → placeholder so the regex won't match them
    protected = value.replace("$${", _ESCAPE_PLACEHOLDER)

    def _replace(match: re.Match[str]) -> str:
        var_name = match.group(1)
        if var_name not in os.environ:
            raise ConfigError(
                f"Environment variable '{var_name}' referenced in {source} "
                f"but not set. Set it in .env or your shell environment."
            )
        return os.environ[var_name]

    resolved = _VAR_PATTERN.sub(_replace, protected)

    # Restore escaped sequences: placeholder → ${
    resolved = resolved.replace(_ESCAPE_PLACEHOLDER, "${")

    return resolved
