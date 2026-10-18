"""Data-only package declarations for checking authored starting mechanics."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from sylvia_gm.domain.authority import SafePayload, owned_payload, validate_authority_identity

STARTING_STATE_FEATURE = "starting_state_validation.v1"
REQUIRED_STARTING_STATE_FEATURE = "required_starting_state.v1"
_BINDING = re.compile(r"\$[a-zA-Z_][a-zA-Z0-9_]{0,63}\Z")


@dataclass(frozen=True)
class StateReference:
    source: tuple[str, ...]
    target_key: str
    target: tuple[str, ...]


@dataclass(frozen=True)
class StateSchema:
    key: str
    schema: SafePayload
    references: tuple[StateReference, ...]
    required: bool = False


def _path(value: object) -> tuple[str, ...]:
    """Admit bounded selectors, not expressions or executable package code."""
    if (not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray))
            or not 1 <= len(value) <= 16):
        raise ValueError("Starting-state reference path requires 1 to 16 segments")
    for index, part in enumerate(value):
        if not isinstance(part, str) or not part or len(part) > 128:
            raise ValueError("Starting-state reference path segment is invalid")
        if part.startswith("$") and not _BINDING.fullmatch(part):
            raise ValueError("Starting-state reference binding is invalid")
        if part == "$key" and index != len(value) - 1:
            raise ValueError("Starting-state $key selector must be last")
    return tuple(value)


def parse_state_schemas(value: object) -> tuple[StateSchema, ...]:
    """Read declarations whose keys and reference targets belong to their package namespace."""
    if (not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray))
            or not 1 <= len(value) <= 32):
        raise ValueError("Starting-state schemas require 1 to 32 declarations")
    declarations = []
    for item in value:
        if not isinstance(item, Mapping) or set(item) not in (
            {"key", "schema", "references"}, {"key", "schema", "references", "required"},
        ):
            raise ValueError("Starting-state schema fields are invalid")
        if type(item.get("required", False)) is not bool:
            raise ValueError("Starting-state required flag must be a boolean")
        key = validate_authority_identity(item["key"], "Starting-state key")
        references = item["references"]
        if (not isinstance(references, Sequence) or isinstance(references, (str, bytes, bytearray))
                or len(references) > 128):
            raise ValueError("Starting-state references require a bounded array")
        parsed = []
        for reference in references:
            if (not isinstance(reference, Mapping)
                    or set(reference) != {"source", "target_key", "target"}):
                raise ValueError("Starting-state reference fields are invalid")
            source, target = _path(reference["source"]), _path(reference["target"])
            bindings = [part for part in source if part.startswith("$") and part != "$key"]
            if len(set(bindings)) != len(bindings):
                raise ValueError("Starting-state source bindings must be unique")
            if any(part.startswith("$") and part != "$key" and part not in bindings
                   for part in target):
                raise ValueError("Starting-state target uses an unbound selector")
            parsed.append(StateReference(source, validate_authority_identity(
                reference["target_key"], "Starting-state target key",
            ), target))
        declarations.append(StateSchema(
            key, owned_payload(item["schema"]), tuple(parsed), item.get("required", False),
        ))
    if len({item.key for item in declarations}) != len(declarations):
        raise ValueError("Starting-state schema keys must be unique")
    return tuple(declarations)
