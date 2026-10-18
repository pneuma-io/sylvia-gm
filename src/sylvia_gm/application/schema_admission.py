"""Small deterministic JSON-schema subset for F1 MCP admission."""

from __future__ import annotations

from collections.abc import Collection, Iterator, Mapping, Sequence

from sylvia_gm.domain.authority import JsonValue, SafePayload
from sylvia_gm.domain.errors import (
    CommandRejected,
    MechanicsRejected,
    MechanicsValidationIssue,
)

_SUPPORTED_SCHEMA_FIELDS = frozenset(
    {
        "$id",
        "description",
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "minimum",
        "maximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "uniqueItems",
        "allOf",
        "if",
        "then",
        "contains",
        "minContains",
        "x-sylvia-gm-reference",
        "x-sylvia-gm-derived",
        "x-sylvia-gm-model-hidden",
        "x-sylvia-gm-model-required",
    }
)


def _nullable_type(schema: Mapping[str, object]) -> str | None:
    """Recognize the standard two-type nullable form without admitting arbitrary unions."""
    value = schema.get("type")
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        return None
    if (len(value) != 2 or "null" not in value
            or any(not isinstance(item, str) for item in value)):
        raise CommandRejected("Nullable schema type must pair one type with null")
    base = next((item for item in value if item != "null"), None)
    if base not in {"string", "integer", "number", "boolean", "object", "array"}:
        raise CommandRejected("Nullable schema type must pair one type with null")
    return str(base)


def _value_schema(schema: Mapping[str, object], value: object) -> Mapping[str, object]:
    """Apply type-specific constraints only to the non-null value of a nullable field."""
    base = _nullable_type(schema)
    if base is None:
        return schema
    if value is not None:
        return {**schema, "type": base}
    return {"type": "null", **{
        key: schema[key] for key in ("enum", "allOf", "if", "then") if key in schema
    }}


def validate_json_schema(schema: Mapping[str, object], *, label: str = "JSON schema") -> None:
    """Validate the deterministic subset shared by tool and participant admission."""

    extra = set(schema) - _SUPPORTED_SCHEMA_FIELDS
    if extra:
        raise CommandRejected(f"{label} contains unsupported field '{sorted(extra)[0]}'")
    if "description" in schema and not isinstance(schema["description"], str):
        raise CommandRejected(f"{label} has an invalid description")
    expected = _nullable_type(schema) or schema.get("type")
    if not isinstance(expected, str) or expected not in {
        "string", "integer", "number", "boolean", "object", "array", "null",
    }:
        raise CommandRejected(f"{label} has an invalid type")
    if "allOf" in schema:
        branches = schema["allOf"]
        if (not isinstance(branches, Sequence) or isinstance(branches, (str, bytes, bytearray))
                or not 1 <= len(branches) <= 256
                or any(not isinstance(branch, Mapping) for branch in branches)):
            raise CommandRejected(f"{label} allOf must contain 1 to 256 schemas")
        for index, branch in enumerate(branches):
            validate_json_schema(branch, label=f"{label}.allOf[{index}]")
    if ("if" in schema) != ("then" in schema):
        raise CommandRejected(f"{label} requires both if and then")
    for field in ("if", "then", "contains"):
        if field in schema:
            child = schema[field]
            if not isinstance(child, Mapping):
                raise CommandRejected(f"{label}.{field} must be a schema")
            validate_json_schema(child, label=f"{label}.{field}")
    if "contains" in schema or "minContains" in schema:
        if expected != "array" or "contains" not in schema:
            raise CommandRejected(f"{label} contains constraints require an array and contains")
        count = schema.get("minContains", 1)
        if not isinstance(count, int) or isinstance(count, bool) or count < 0:
            raise CommandRejected(f"{label} has invalid minContains")
    schema_id = schema.get("$id")
    if schema_id is not None and (not isinstance(schema_id, str) or not schema_id):
        raise CommandRejected(f"{label} has an invalid identity")
    enum = schema.get("enum")
    if enum is not None and (
        not isinstance(enum, Sequence) or isinstance(enum, (str, bytes, bytearray))
    ):
        raise CommandRejected(f"{label} has an invalid enum")
    minimum = schema.get("minimum")
    if minimum is not None and (not isinstance(minimum, (int, float)) or isinstance(minimum, bool)):
        raise CommandRejected(f"{label} has an invalid minimum")
    maximum = schema.get("maximum")
    if maximum is not None and (not isinstance(maximum, (int, float)) or isinstance(maximum, bool)):
        raise CommandRejected(f"{label} has an invalid maximum")
    min_length = schema.get("minLength")
    if min_length is not None and (
        not isinstance(min_length, int) or isinstance(min_length, bool) or min_length < 0
    ):
        raise CommandRejected(f"{label} has an invalid minimum length")
    max_length = schema.get("maxLength")
    if max_length is not None and (
        not isinstance(max_length, int) or isinstance(max_length, bool) or max_length < 0
    ):
        raise CommandRejected(f"{label} has an invalid maximum length")
    if expected == "object":
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            raise CommandRejected(f"{label} properties are invalid")
        required = schema.get("required", [])
        if (
            not isinstance(required, Sequence)
            or isinstance(required, (str, bytes, bytearray))
            or not all(isinstance(item, str) for item in required)
            or len(set(required)) != len(required)
            or any(item not in properties for item in required)
        ):
            raise CommandRejected(f"{label} required fields are invalid")
        additional = schema.get("additionalProperties", True)
        if not isinstance(additional, (bool, Mapping)):
            raise CommandRejected(f"{label} additional properties are invalid")
        for name, child in properties.items():
            if not isinstance(name, str) or not isinstance(child, Mapping):
                raise CommandRejected(f"{label} property schemas are invalid")
            validate_json_schema(child, label=f"{label}.{name}")
        if isinstance(additional, Mapping):
            validate_json_schema(additional, label=f"{label}.additionalProperties")
    elif expected == "array":
        min_items = schema.get("minItems")
        max_items = schema.get("maxItems")
        for field, bound in (("minimum items", min_items), ("maximum items", max_items)):
            if bound is not None and (
                not isinstance(bound, int) or isinstance(bound, bool) or bound < 0
            ):
                raise CommandRejected(f"{label} has invalid {field}")
        if (
            isinstance(min_items, int)
            and not isinstance(min_items, bool)
            and isinstance(max_items, int)
            and not isinstance(max_items, bool)
            and min_items > max_items
        ):
            raise CommandRejected(f"{label} minimum items exceed maximum items")
        unique_items = schema.get("uniqueItems")
        if unique_items is not None and not isinstance(unique_items, bool):
            raise CommandRejected(f"{label} has invalid unique items")
        items = schema.get("items")
        if not isinstance(items, Mapping):
            raise CommandRejected(f"{label} has invalid array items schema")
        validate_json_schema(items, label=f"{label}.items")
    elif any(field in schema for field in ("minItems", "maxItems", "uniqueItems")):
        raise CommandRejected(f"{label} uses array constraints on a non-array schema")


def validate_json_value(
    value: JsonValue, schema: Mapping[str, object], *, label: str = "JSON value"
) -> None:
    """Validate any JSON value with the same recursive implementation as tool input."""

    validate_json_schema(schema, label=f"{label} schema")
    _validate_value(label, value, schema)


def validate_parameters(parameters: SafePayload, schema: Mapping[str, object]) -> None:
    """Validate the explicit object schemas admitted by the F1 catalog."""

    if schema.get("type") != "object":
        raise CommandRejected("Tool schema must declare an object")
    properties_value = schema.get("properties", {})
    if not isinstance(properties_value, Mapping):
        raise CommandRejected("Tool schema properties are invalid")
    required_value = schema.get("required", [])
    if (
        not isinstance(required_value, Sequence)
        or isinstance(required_value, (str, bytes, bytearray))
        or not all(isinstance(item, str) for item in required_value)
    ):
        raise CommandRejected("Tool schema required fields are invalid")
    issues = _parameter_value_issues(parameters, schema, path="$", parent_path="$")
    if issues:
        ordered = tuple(sorted(issues, key=lambda issue: (issue.path, issue.kind)))
        legacy_constraint_kinds = {
            "above_maximum",
            "below_minimum",
            "duplicate_items",
            "too_few_items",
            "too_many_items",
        }
        raise MechanicsRejected(
            "Tool parameters do not match the exposed operation schema",
            code=(
                "invalid_schema"
                if any(issue.kind in legacy_constraint_kinds for issue in ordered)
                else "mechanics_validation_failed"
            ),
            field_paths=tuple(dict.fromkeys(issue.path for issue in ordered)),
            validation_issues=ordered,
            state_changed=False,
        )


def validate_references(
    parameters: SafePayload,
    schema: Mapping[str, object],
    *,
    participant_ids: Collection[str],
) -> None:
    """Admit schema-declared references only within the command campaign."""

    _validate_reference_value(parameters, schema, participant_ids=participant_ids)


def _applied_branches(
    value: JsonValue, schema: Mapping[str, object],
) -> Iterator[Mapping[str, object]]:
    """Select conjunction and conditional constraints without interpreting package rules."""
    branches = schema.get("allOf", ())
    if isinstance(branches, Sequence) and not isinstance(branches, (str, bytes, bytearray)):
        for branch in branches:
            if isinstance(branch, Mapping):
                yield branch
    condition, consequence = schema.get("if"), schema.get("then")
    if (isinstance(condition, Mapping) and isinstance(consequence, Mapping)
            and not _parameter_value_issues(value, condition, path="$", parent_path="$")):
        yield consequence


def _contains_count(value: Sequence[JsonValue], schema: Mapping[str, object]) -> int:
    """Count schema matches without leaking the values that failed to match."""
    return sum(not _parameter_value_issues(item, schema, path="$", parent_path="$")
               for item in value)


def _applicable_child_schema(
    schema: Mapping[str, object], child_name: str
) -> Mapping[str, object] | None:
    """Return the exact property/additionalProperties branch used for validation."""

    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        return None
    if child_name in properties:
        declared = properties[child_name]
        return declared if isinstance(declared, Mapping) else None
    additional = schema.get("additionalProperties", True)
    return additional if isinstance(additional, Mapping) else None


def _json_type_name(value: object) -> str:
    """Return a value's JSON type without exposing the value itself."""

    if value is None:
        return "null"
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, str):
        return "string"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, Mapping):
        return "object"
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return "array"
    return "unknown"


def _parameter_issue(
    *,
    path: str,
    kind: str,
    schema: Mapping[str, object] | None = None,
    actual: object = ...,
    parent_path: str | None = None,
) -> MechanicsValidationIssue:
    """Build one bounded correction issue from schema facts and value type only."""

    expected = schema.get("type") if isinstance(schema, Mapping) else None
    enum = schema.get("enum") if isinstance(schema, Mapping) else None
    allowed_values = (
        tuple(str(item) for item in enum[:64])
        if isinstance(enum, Sequence) and not isinstance(enum, (str, bytes, bytearray))
        else ()
    )
    minimum = None
    maximum = None
    if isinstance(schema, Mapping):
        for name in ("minimum", "minLength", "minItems"):
            candidate = schema.get(name)
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                minimum = candidate
                break
        for name in ("maximum", "maxLength", "maxItems"):
            candidate = schema.get(name)
            if isinstance(candidate, int) and not isinstance(candidate, bool):
                maximum = candidate
                break
    return MechanicsValidationIssue(
        path=path[:240],
        kind=kind,
        expected_type=str(expected) if isinstance(expected, str) else None,
        actual_type=None if actual is ... else _json_type_name(actual),
        parent_path=(parent_path or "$")[:240],
        allowed_values=allowed_values,
        minimum=minimum,
        maximum=maximum,
        actual_length=len(actual) if isinstance(actual, str) else None,
    )


def _value_matches_type(value: object, expected: str) -> bool:
    """Apply the admitted JSON type rules shared by mechanics parameters."""

    return {
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "object": isinstance(value, Mapping),
        "array": isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)),
        "null": value is None,
    }.get(expected, False)


def _parameter_value_issues(
    value: JsonValue,
    schema: Mapping[str, object],
    *,
    path: str,
    parent_path: str,
) -> list[MechanicsValidationIssue]:
    """Collect actionable schema mismatches without copying parameter values."""

    schema = _value_schema(schema, value)
    expected = schema.get("type")
    if not isinstance(expected, str):
        raise CommandRejected(f"Tool parameter schema for '{path}' has an invalid type")
    if not _value_matches_type(value, expected):
        return [
            _parameter_issue(
                path=path,
                kind="wrong_type",
                schema=schema,
                actual=value,
                parent_path=parent_path,
            )
        ]

    enum = schema.get("enum")
    if enum is not None:
        if not isinstance(enum, Sequence) or isinstance(enum, (str, bytes, bytearray)):
            raise CommandRejected(f"Tool parameter schema for '{path}' has an invalid enum")
        if value not in enum:
            return [
                _parameter_issue(
                    path=path,
                    kind="invalid_enum_value",
                    schema=schema,
                    actual=value,
                    parent_path=parent_path,
                )
            ]

    issues: list[MechanicsValidationIssue] = []
    minimum = schema.get("minimum")
    if minimum is not None:
        if not isinstance(minimum, (int, float)) or isinstance(minimum, bool):
            raise CommandRejected(f"Tool parameter schema for '{path}' has an invalid minimum")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value < minimum:
            issues.append(
                _parameter_issue(
                    path=path,
                    kind="below_minimum",
                    schema=schema,
                    actual=value,
                    parent_path=parent_path,
                )
            )
    maximum = schema.get("maximum")
    if maximum is not None:
        if not isinstance(maximum, (int, float)) or isinstance(maximum, bool):
            raise CommandRejected(f"Tool parameter schema for '{path}' has an invalid maximum")
        if isinstance(value, (int, float)) and not isinstance(value, bool) and value > maximum:
            issues.append(
                _parameter_issue(
                    path=path,
                    kind="above_maximum",
                    schema=schema,
                    actual=value,
                    parent_path=parent_path,
                )
            )
    min_length = schema.get("minLength")
    if min_length is not None:
        if not isinstance(min_length, int) or isinstance(min_length, bool) or min_length < 0:
            raise CommandRejected(
                f"Tool parameter schema for '{path}' has an invalid minimum length"
            )
        if isinstance(value, str) and len(value) < min_length:
            issues.append(
                _parameter_issue(
                    path=path,
                    kind="string_too_short",
                    schema=schema,
                    actual=value,
                    parent_path=parent_path,
                )
            )
    max_length = schema.get("maxLength")
    if max_length is not None:
        if not isinstance(max_length, int) or isinstance(max_length, bool) or max_length < 0:
            raise CommandRejected(
                f"Tool parameter schema for '{path}' has an invalid maximum length"
            )
        if isinstance(value, str) and len(value) > max_length:
            issues.append(_parameter_issue(
                path=path, kind="string_too_long", schema=schema,
                actual=value, parent_path=parent_path,
            ))
    if expected == "object":
        assert isinstance(value, Mapping)
        issues.extend(_parameter_object_issues(value, schema, path=path))
    elif expected == "array":
        assert isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
        min_items = schema.get("minItems")
        if min_items is not None and (
            not isinstance(min_items, int) or isinstance(min_items, bool) or min_items < 0
        ):
            raise CommandRejected(f"Tool parameter schema for '{path}' has invalid minItems")
        if isinstance(min_items, int) and len(value) < min_items:
            issues.append(
                _parameter_issue(
                    path=path,
                    kind="too_few_items",
                    schema=schema,
                    actual=value,
                    parent_path=parent_path,
                )
            )
        max_items = schema.get("maxItems")
        if max_items is not None and (
            not isinstance(max_items, int) or isinstance(max_items, bool) or max_items < 0
        ):
            raise CommandRejected(f"Tool parameter schema for '{path}' has invalid maxItems")
        if isinstance(max_items, int) and len(value) > max_items:
            issues.append(
                _parameter_issue(
                    path=path,
                    kind="too_many_items",
                    schema=schema,
                    actual=value,
                    parent_path=parent_path,
                )
            )
        if schema.get("uniqueItems") is True and any(
            item == prior for index, item in enumerate(value) for prior in value[:index]
        ):
            issues.append(
                _parameter_issue(
                    path=path,
                    kind="duplicate_items",
                    schema=schema,
                    actual=value,
                    parent_path=parent_path,
                )
            )
        item_schema = schema.get("items")
        if not isinstance(item_schema, Mapping):
            raise CommandRejected(f"Tool parameter schema for '{path}' has invalid items")
        for index, item in enumerate(value):
            issues.extend(
                _parameter_value_issues(
                    item,
                    item_schema,
                    path=f"{path}[{index}]",
                    parent_path=path,
                )
            )
        contains = schema.get("contains")
        count = schema.get("minContains", 1)
        if (isinstance(contains, Mapping) and isinstance(count, int)
                and _contains_count(value, contains) < count):
            issues.append(_parameter_issue(
                path=path, kind="too_few_matching_items", parent_path=parent_path,
                schema={"type": "array", "minItems": count, "enum": contains.get("enum", ())},
            ))
    for branch in _applied_branches(value, schema):
        issues.extend(_parameter_value_issues(value, branch, path=path, parent_path=parent_path))
    return issues


def _parameter_object_issues(
    value: Mapping[str, JsonValue],
    schema: Mapping[str, object],
    *,
    path: str,
) -> list[MechanicsValidationIssue]:
    """Collect object-field issues from one admitted mechanics schema."""

    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise CommandRejected(f"Tool parameter schema for '{path}' has invalid properties")
    required = schema.get("required", [])
    if (
        not isinstance(required, Sequence)
        or isinstance(required, (str, bytes, bytearray))
        or not all(isinstance(item, str) for item in required)
    ):
        raise CommandRejected(f"Tool parameter schema for '{path}' has invalid required fields")
    parent = "$" if path == "$" else path
    prefix = "" if path == "$" else f"{path}."
    issues = [
        _parameter_issue(
            path=f"{prefix}{name}",
            kind="missing_required_field",
            schema=(
                properties[name]
                if name in properties and isinstance(properties[name], Mapping)
                else None
            ),
            parent_path=parent,
        )
        for name in required
        if name not in value
    ]
    if schema.get("additionalProperties") is False:
        issues.extend(
            _parameter_issue(
                path=f"{prefix}{name}",
                kind="unexpected_field",
                actual=value[name],
                parent_path=parent,
            )
            for name in sorted(set(value) - set(properties))
        )
    for name, child in value.items():
        child_schema = _applicable_child_schema(schema, name)
        if child_schema is None:
            if name in properties:
                raise CommandRejected(f"Tool parameter schema for '{prefix}{name}' is invalid")
            continue
        issues.extend(
            _parameter_value_issues(
                child,
                child_schema,
                path=f"{prefix}{name}",
                parent_path=parent,
            )
        )
    return issues


def _validate_value(name: str, value: JsonValue, schema: Mapping[str, object]) -> None:
    schema = _value_schema(schema, value)
    expected = schema.get("type")
    if isinstance(expected, str):
        valid = {
            "string": isinstance(value, str),
            "integer": isinstance(value, int) and not isinstance(value, bool),
            "number": isinstance(value, (int, float)) and not isinstance(value, bool),
            "boolean": isinstance(value, bool),
            "object": isinstance(value, Mapping),
            "array": isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)),
            "null": value is None,
        }.get(expected, False)
    else:
        valid = False
    if not valid:
        raise CommandRejected(f"Tool parameter '{name}' has invalid type")
    enum = schema.get("enum")
    if enum is not None and (
        not isinstance(enum, Sequence)
        or isinstance(enum, (str, bytes, bytearray))
        or value not in enum
    ):
        raise CommandRejected(f"Tool parameter '{name}' is outside its enum")
    minimum = schema.get("minimum")
    if minimum is not None:
        if not isinstance(minimum, (int, float)) or isinstance(minimum, bool):
            raise CommandRejected(f"Tool parameter '{name}' has an invalid minimum schema")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value < minimum:
            raise CommandRejected(
                f"Tool parameter '{name}' is below its minimum", code="invalid_schema"
            )
    maximum = schema.get("maximum")
    if maximum is not None:
        if not isinstance(maximum, (int, float)) or isinstance(maximum, bool):
            raise CommandRejected(f"Tool parameter '{name}' has an invalid maximum schema")
        if not isinstance(value, (int, float)) or isinstance(value, bool) or value > maximum:
            raise CommandRejected(
                f"Tool parameter '{name}' is above its maximum", code="invalid_schema"
            )
    min_length = schema.get("minLength")
    if min_length is not None:
        if not isinstance(min_length, int) or isinstance(min_length, bool) or min_length < 0:
            raise CommandRejected(f"Tool parameter '{name}' has an invalid minimum length schema")
        if not isinstance(value, str) or len(value) < min_length:
            raise CommandRejected(f"Tool parameter '{name}' is shorter than its minimum length")
    max_length = schema.get("maxLength")
    if max_length is not None:
        if not isinstance(max_length, int) or isinstance(max_length, bool) or max_length < 0:
            raise CommandRejected(f"Tool parameter '{name}' has an invalid maximum length schema")
        if not isinstance(value, str) or len(value) > max_length:
            raise CommandRejected(f"Tool parameter '{name}' is longer than its maximum length")
    if expected == "object":
        assert isinstance(value, Mapping)
        _validate_object_value(name, value, schema)
    elif expected == "array":
        assert isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray))
        min_items = schema.get("minItems")
        if (
            isinstance(min_items, int)
            and not isinstance(min_items, bool)
            and len(value) < min_items
        ):
            raise CommandRejected(
                f"Tool parameter '{name}' contains fewer than {min_items} items",
                code="invalid_schema",
            )
        max_items = schema.get("maxItems")
        if (
            isinstance(max_items, int)
            and not isinstance(max_items, bool)
            and len(value) > max_items
        ):
            raise CommandRejected(
                f"Tool parameter '{name}' contains more than {max_items} items",
                code="invalid_schema",
            )
        if schema.get("uniqueItems") is True and any(
            item == prior for index, item in enumerate(value) for prior in value[:index]
        ):
            raise CommandRejected(
                f"Tool parameter '{name}' contains duplicate items",
                code="invalid_schema",
            )
        items = schema.get("items")
        if not isinstance(items, Mapping):
            raise CommandRejected(f"Tool parameter '{name}' has invalid array items schema")
        for index, item in enumerate(value):
            _validate_value(f"{name}[{index}]", item, items)
        contains = schema.get("contains")
        count = schema.get("minContains", 1)
        if (isinstance(contains, Mapping) and isinstance(count, int)
                and _contains_count(value, contains) < count):
            raise CommandRejected(
                f"Tool parameter '{name}' has fewer than {count} matching items",
                code="invalid_schema",
            )
    for branch in _applied_branches(value, schema):
        _validate_value(name, value, branch)


def _validate_object_value(
    name: str, value: Mapping[str, JsonValue], schema: Mapping[str, object]
) -> None:
    properties = schema.get("properties", {})
    if not isinstance(properties, Mapping):
        raise CommandRejected(f"Tool parameter '{name}' has invalid object properties schema")
    required = schema.get("required", [])
    if (
        not isinstance(required, Sequence)
        or isinstance(required, (str, bytes, bytearray))
        or not all(isinstance(item, str) for item in required)
    ):
        raise CommandRejected(f"Tool parameter '{name}' has invalid required schema")
    missing = [item for item in required if item not in value]
    if missing:
        raise CommandRejected(f"Tool parameter '{name}' is missing required field '{missing[0]}'")
    if schema.get("additionalProperties") is False:
        extra = sorted(set(value) - set(properties))
        if extra:
            raise CommandRejected(f"Tool parameter '{name}' contains undeclared field '{extra[0]}'")
    for child_name, child_value in value.items():
        child_schema = _applicable_child_schema(schema, child_name)
        if child_schema is not None:
            _validate_value(f"{name}.{child_name}", child_value, child_schema)


def _validate_reference_value(
    value: JsonValue,
    schema: Mapping[str, object],
    *,
    participant_ids: Collection[str],
) -> None:
    schema = _value_schema(schema, value)
    if schema.get("x-sylvia-gm-reference") == "participant" and (
        not isinstance(value, str) or value not in participant_ids
    ):
        raise CommandRejected(
            "Participant reference is outside the command campaign",
            code="cross_campaign_reference",
        )
    expected = schema.get("type")
    if expected == "object" and isinstance(value, Mapping):
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            return
        for child_name, child_value in value.items():
            child_schema = _applicable_child_schema(schema, child_name)
            if child_schema is not None:
                _validate_reference_value(
                    child_value, child_schema, participant_ids=participant_ids
                )
    elif (expected == "array" and isinstance(value, Sequence)
          and not isinstance(value, (str, bytes, bytearray))):
        items = schema.get("items")
        if isinstance(items, Mapping):
            for item in value:
                _validate_reference_value(item, items, participant_ids=participant_ids)
        contains = schema.get("contains")
        if isinstance(contains, Mapping):
            for item in value:
                if not _parameter_value_issues(item, contains, path="$", parent_path="$"):
                    _validate_reference_value(item, contains, participant_ids=participant_ids)
    for branch in _applied_branches(value, schema):
        _validate_reference_value(value, branch, participant_ids=participant_ids)
