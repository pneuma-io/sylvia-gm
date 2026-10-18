"""JSON Schema loading, validation, and management.

The master schema ships at ``src/sylvia_gm/state/state_schema.json``.
On ``StateAPI.create()``, the schema is copied to the campaign directory.
Validation is performed using ``jsonschema`` (draft-2020-12).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema

from ._validators import validate_all

# Current schema version for metadata tracking.
CURRENT_SCHEMA_VERSION: str = "0.2.0"

# Path to the master schema shipped with the module.
_MASTER_SCHEMA_PATH: Path = Path(__file__).parent / "state_schema.json"


def load_schema(path: Path | None = None) -> dict[str, Any]:
    """Load the JSON Schema from *path*, or the master schema if None."""
    if path is None:
        path = _MASTER_SCHEMA_PATH
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def validate_state(state: dict[str, Any], schema: dict[str, Any] | None = None) -> list[str]:
    """Validate *state* against the JSON Schema and custom validators.

    Args:
        state: The full state dict to validate.
        schema: The JSON Schema dict. If None, loads the master schema.

    Returns:
        List of error strings. Empty list means valid.
    """
    if schema is None:
        schema = load_schema()

    errors: list[str] = []

    # JSON Schema validation
    validator_class = jsonschema.validators.validator_for(schema)
    validator = validator_class(schema)
    for err in validator.iter_errors(state):
        errors.append(f"Schema error: {err.message} (path: {'/'.join(str(p) for p in err.absolute_path)})")

    # Custom validators
    errors.extend(validate_all(state))

    return errors


def validate_single_entity(
    entity: dict[str, Any],
    entity_type: str,
    schema: dict[str, Any] | None = None,
) -> list[str]:
    """Validate a single entity against the schema's entity definition.

    Args:
        entity: The entity dict to validate.
        entity_type: One of 'character', 'monster', 'npc', 'quest'.
        schema: The JSON Schema dict.

    Returns:
        List of error strings.
    """
    if schema is None:
        schema = load_schema()

    # Map entity_type to schema definition path
    _type_map = {
        "character": "#/$defs/character",
        "monster": "#/$defs/monster",
        "npc": "#/$defs/npc",
        "quest": "#/$defs/quest",
    }

    ref = _type_map.get(entity_type)
    if ref is None:
        return [f"Unknown entity type: {entity_type!r}"]

    errors: list[str] = []

    # Resolve the $defs reference
    resolver = jsonschema.validators.RefResolver.from_schema(schema)
    try:
        _, entity_schema = resolver.resolve(ref)
    except jsonschema.exceptions.RefResolutionError as e:
        return [f"Cannot resolve schema ref {ref!r}: {e}"]

    validator_class = jsonschema.validators.validator_for(entity_schema)
    validator = validator_class(entity_schema)
    for err in validator.iter_errors(entity):
        errors.append(
            f"Entity schema error: {err.message} "
            f"(path: {'/'.join(str(p) for p in err.absolute_path)})"
        )

    return errors
