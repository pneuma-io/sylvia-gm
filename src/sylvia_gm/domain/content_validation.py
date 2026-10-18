"""Shared authoring-format checks; semantic owners retain contextual reference checks."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from functools import lru_cache

from jsonschema import Draft202012Validator, validators
from jsonschema.exceptions import ValidationError
from jsonschema.protocols import Validator

from sylvia_gm.domain.content_definitions import get_content_definition
from sylvia_gm.domain.errors import RulesetDiagnostic

_ContentValidator = validators.extend(
    Draft202012Validator,
    type_checker=Draft202012Validator.TYPE_CHECKER.redefine_many(
        {
            "object": lambda _checker, value: isinstance(value, Mapping),
            "array": lambda _checker, value: (
                isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
            ),
            "integer": lambda _checker, value: type(value) is int,
        }
    ),
)


class ContentShapeError(ValueError):
    """Public field-level corrections without submitted values or private parser state."""

    def __init__(self, diagnostics: tuple[RulesetDiagnostic, ...]) -> None:
        super().__init__("Content fields do not match the declared format")
        self.diagnostics = diagnostics


@lru_cache(maxsize=32)
def _validator(name: str) -> Validator:
    """Compile the same installed schema exposed to authors."""

    schema = get_content_definition(name)["schema"]
    Draft202012Validator.check_schema(schema)
    return _ContentValidator(schema)


def _field_path(parts: list[object]) -> str:
    """Keep correction paths useful without echoing arbitrary submitted property text."""

    result = "$"
    for part in parts:
        if isinstance(part, int):
            result += f"[{part}]"
        elif isinstance(part, str) and re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,95}", part):
            result += "." + part
        else:
            result += "[invalid field name]"
    return result


def _root_type_mismatch(error: ValidationError, path: Sequence[object]) -> bool:
    """Distinguish a wrong union representation from an invalid field inside it."""
    if list(error.absolute_path) != list(path):
        return False
    if error.validator == "type":
        return True
    return (
        bool(error.context)
        and error.validator in {"oneOf", "anyOf"}
        and all(_root_type_mismatch(child, path) for child in error.context)
    )


def _field_errors(error: ValidationError) -> Iterator[ValidationError]:
    """Unwrap a uniquely type-compatible union branch to retain nested field paths."""
    if error.validator in {"oneOf", "anyOf"} and error.context:
        branches: dict[object, list[ValidationError]] = {}
        for child in error.context:
            branches.setdefault(child.schema_path[0], []).append(child)
        compatible = [
            children
            for children in branches.values()
            if not any(_root_type_mismatch(child, error.absolute_path) for child in children)
        ]
        if len(compatible) == 1:
            for child in compatible[0]:
                yield from _field_errors(child)
            return
    yield error


def validate_content_shape(name: str, value: object, *, file: str | None = None) -> None:
    """Reject structural mistakes with bounded, value-free field corrections."""

    corrections: dict[tuple[str, str], RulesetDiagnostic] = {}
    errors = (
        detail for error in _validator(name).iter_errors(value) for detail in _field_errors(error)
    )
    for error in errors:
        path = list(error.absolute_path)
        paths = [path]
        keyword = str(error.validator)
        if keyword == "required" and isinstance(error.instance, Mapping):
            paths = [
                path + [field] for field in error.validator_value if field not in error.instance
            ]
            message = "Supply this required field."
        elif keyword == "additionalProperties" and isinstance(error.instance, Mapping):
            declared = error.schema.get("properties", {})
            patterns = error.schema.get("patternProperties", {})
            paths = [
                path + [field]
                for field in error.instance
                if field not in declared
                and not any(re.search(pattern, field) for pattern in patterns)
            ] or [path]
            message = "Remove this undeclared field."
        elif keyword == "type":
            message = f"Use the declared type: {error.validator_value}."
        elif keyword in {"minLength", "maxLength"} and isinstance(error.instance, str):
            bound = "at least" if keyword == "minLength" else "at most"
            message = (
                f"Use {bound} {error.validator_value} characters; "
                f"this field has {len(error.instance)} characters."
            )
        else:
            message = f"Correct this field to satisfy the definition's {keyword} constraint."
        for item_path in paths:
            location = _field_path(item_path)
            corrections[(keyword, location)] = RulesetDiagnostic(
                code=f"content_{keyword}_invalid",
                phase="content",
                file=file,
                path=location,
                message=message,
                guidance=f"Read the {name} content definition and correct this field.",
            )
            if len(corrections) >= 16:
                break
        if len(corrections) >= 16:
            break
    if corrections:
        raise ContentShapeError(tuple(corrections.values()))
