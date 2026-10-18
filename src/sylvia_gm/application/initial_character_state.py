"""Materialize and verify Ruleset-declared initial character state."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from copy import deepcopy
from typing import Any, cast

from sylvia_gm.application.character_rules import validate_character_rules, verify_character_rules
from sylvia_gm.application.schema_admission import validate_json_schema, validate_parameters
from sylvia_gm.domain.authority import SafePayload
from sylvia_gm.domain.errors import CommandRejected, MechanicsRejected, RulesetDiagnostic
from sylvia_gm.domain.package_library import ParticipantArtifactDeclaration, canonical_json_bytes
from sylvia_gm.runtime._condition_evaluator import evaluate
from sylvia_gm.runtime._exceptions import ExpressionError, FieldNotFoundError
from sylvia_gm.runtime._expression_parser import (
    ASTNode,
    BinaryOpNode,
    FieldAccessNode,
    FunctionCallNode,
    UnaryOpNode,
    parse_expression,
)
from sylvia_gm.runtime._types import ExecutionContext

_MAX_CALCULATIONS = 128
_FIELD_PATH = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*")


def _rejection(
    code: str,
    message: str,
    *,
    path: str,
    guidance: str | None = None,
) -> CommandRejected:
    return CommandRejected(
        "Initial character state is invalid",
        code=code,
        diagnostics=(
            RulesetDiagnostic(
                code=code,
                phase="character_initial_state",
                message=message,
                path=path,
                guidance=guidance,
            ),
        ),
    )


def _schema_rejection(error: Exception) -> CommandRejected:
    if isinstance(error, MechanicsRejected) and error.validation_issues:
        diagnostics = tuple(
            RulesetDiagnostic(
                code="character_initial_state_schema_invalid",
                phase="character_initial_state",
                message=f"The value at {issue.path} does not match the declared state schema.",
                path=issue.path,
                expected=issue.expected_type,
                actual=issue.actual_type,
                guidance="Supply the required authored input with the declared JSON type.",
            )
            for issue in error.validation_issues[:16]
        )
    else:
        diagnostics = (
            RulesetDiagnostic(
                code="character_initial_state_schema_invalid",
                phase="character_initial_state",
                message="The Ruleset's entity state schema is invalid.",
                path="entity_state_schema",
                guidance="Correct the Ruleset's declared entity state schema.",
            ),
        )
    return CommandRejected(
        "Initial character state does not match its Ruleset schema",
        code="character_initial_state_schema_invalid",
        diagnostics=diagnostics,
    )


def _calculation_contract(
    schema: Mapping[str, object],
    calculations: Sequence[object],
) -> tuple[tuple[str, str, str | None], ...]:
    try:
        validate_json_schema(schema, label="Initial entity state schema")
    except CommandRejected as error:
        raise _schema_rejection(error) from error
    if schema.get("type") != "object":
        raise _rejection(
            "character_initial_state_schema_invalid",
            "The initial entity state schema must describe an object.",
            path="entity_state_schema.type",
        )
    if (
        not isinstance(calculations, Sequence)
        or isinstance(calculations, (str, bytes, bytearray))
        or len(calculations) > _MAX_CALCULATIONS
    ):
        raise _rejection(
            "character_initial_state_calculations_invalid",
            "Initial state calculations must be a bounded ordered array.",
            path="initial_state_calculations",
        )
    admitted: list[tuple[str, str, str | None]] = []
    targets: set[str] = set()
    for index, raw in enumerate(calculations):
        base = f"initial_state_calculations[{index}]"
        if not isinstance(raw, Mapping) or set(raw) not in ({"set"}, {"set", "condition"}):
            raise _rejection(
                "character_initial_state_calculation_invalid",
                "Each calculation must contain exactly set and an optional condition.",
                path=base,
            )
        setting = raw.get("set")
        condition = raw.get("condition")
        if (
            not isinstance(setting, Mapping)
            or set(setting) != {"field", "value"}
            or not isinstance(setting.get("field"), str)
            or _FIELD_PATH.fullmatch(cast(str, setting["field"])) is None
            or not isinstance(setting.get("value"), str)
            or not setting["value"]
            or ("condition" in raw and (not isinstance(condition, str) or not condition))
        ):
            raise _rejection(
                "character_initial_state_calculation_invalid",
                "The calculation field and expressions are invalid.",
                path=base,
            )
        field = cast(str, setting["field"])
        if not _schema_declares_path(schema, field):
            raise _rejection(
                "character_initial_state_calculation_invalid",
                "A calculated field must be explicitly declared by the entity state schema.",
                path=f"{base}.set.field",
            )
        if any(
            field.startswith(f"{target}.") or target.startswith(f"{field}.")
            for target in targets
        ):
            raise _rejection(
                "character_initial_state_calculation_invalid",
                "Calculated fields cannot overlap a calculated parent or child field.",
                path=f"{base}.set.field",
            )
        targets.add(field)
        _guard_expression(cast(str, setting["value"]), path=f"{base}.set.value")
        if condition is not None:
            _guard_expression(cast(str, condition), path=f"{base}.condition")
        admitted.append((field, cast(str, setting["value"]), cast(str | None, condition)))
    return tuple(admitted)


def _schema_declares_path(schema: Mapping[str, object], path: str) -> bool:
    current = schema
    for part in path.split("."):
        if current.get("type") != "object":
            return False
        properties = current.get("properties")
        if not isinstance(properties, Mapping) or not isinstance(properties.get(part), Mapping):
            return False
        current = cast(Mapping[str, object], properties[part])
    kind = current.get("type")
    if isinstance(kind, Sequence) and not isinstance(kind, (str, bytes, bytearray)):
        # The schema is already admitted. A calculation may replace a nullable field,
        # but the traversal above does not invent children inside an empty object.
        return len(kind) == 2 and "null" in kind
    return isinstance(kind, str) and kind in {
        "integer", "number", "string", "boolean", "null", "array", "object"
    }


def _authored_schema(schema: Mapping[str, object], calculated: Sequence[str]) -> dict[str, Any]:
    """Remove calculated paths from required lists while retaining their type declarations."""

    result = json.loads(canonical_json_bytes(schema))
    for path in calculated:
        current: object = result
        parts = path.split(".")
        for index, part in enumerate(parts):
            if not isinstance(current, dict):
                break
            required = current.get("required")
            if isinstance(required, list) and part in required:
                required.remove(part)
            properties = current.get("properties")
            current = properties.get(part) if isinstance(properties, dict) else None
            if index == len(parts) - 1:
                break
    return result


def _path_present(root: object, path: str) -> bool:
    current = root
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return False
        current = current[part]
    return True


def _remove_path(root: dict[str, Any], path: str) -> None:
    current: object = root
    parts = path.split(".")
    for part in parts[:-1]:
        if not isinstance(current, dict) or not isinstance(current.get(part), dict):
            return
        current = current[part]
    if isinstance(current, dict):
        current.pop(parts[-1], None)


def _set_path(root: dict[str, Any], path: str, value: object) -> None:
    current = root
    parts = path.split(".")
    for part in parts[:-1]:
        existing = current.get(part)
        if existing is None:
            existing = {}
            current[part] = existing
        if not isinstance(existing, dict):
            raise _rejection(
                "character_initial_state_calculation_failed",
                "A calculated field's parent is not an object.",
                path=f"entity_state.{path}",
            )
        current = existing
    current[parts[-1]] = value


def _expression_nodes(node: ASTNode) -> Sequence[ASTNode]:
    if isinstance(node, UnaryOpNode):
        return (node.operand,)
    if isinstance(node, BinaryOpNode):
        return (node.left, node.right)
    if isinstance(node, FunctionCallNode):
        return tuple(node.args)
    if isinstance(node, FieldAccessNode):
        return tuple(index for _, index in node.segments if index is not None)
    return ()


def _reads_root(node: ASTNode, name: str) -> bool:
    """Identify a named input dependency without mistaking quoted text for a field read."""
    pending = [node]
    while pending:
        current = pending.pop()
        if isinstance(current, FieldAccessNode) and current.segments[0][0] == name:
            return True
        pending.extend(_expression_nodes(current))
    return False


def _guard_expression(expression: str, *, path: str) -> ASTNode:
    """Admit the existing expression language with only creation input roots available."""
    try:
        root = parse_expression(expression)
    except ExpressionError as error:
        raise _rejection(
            "character_initial_state_expression_invalid",
            "The Ruleset's initial state expression is invalid.",
            path=path,
            guidance="Correct the expression using the existing Ruleset expression syntax.",
        ) from error
    pending = [root]
    while pending:
        node = pending.pop()
        if isinstance(node, FieldAccessNode):
            root_name = node.segments[0][0]
            if root_name not in {"participant", "state", "setting_data", "rules", "campaign_state"}:
                raise _rejection(
                    "character_initial_state_expression_invalid",
                    "Initial state expressions may read only participant, state, setting_data, "
                    "read-only campaign_state and rules results.",
                    path=path,
                )
        pending.extend(_expression_nodes(node))
    return root


class _RequiredInputs(dict[str, Any]):
    """Keep missing creation inputs distinct from null in the existing evaluator."""

    def get(self, key: str, default: Any = None) -> Any:
        if key not in self:
            raise FieldNotFoundError("A required creation input is missing")
        return self[key]


def _required_inputs(value: Any) -> Any:
    if isinstance(value, dict):
        return _RequiredInputs({key: _required_inputs(child) for key, child in value.items()})
    if isinstance(value, list):
        return [_required_inputs(child) for child in value]
    return value


def validate_initial_character_contract(
    entity_state_schema: Mapping[str, object], initial_state_calculations: Sequence[object],
    initial_state_validation: Mapping[str, object] | None = None,
) -> None:
    """Check the complete declaration before admitting a usable Ruleset package."""

    _calculation_contract(entity_state_schema, initial_state_calculations)
    validate_character_rules(initial_state_validation)


def initial_character_input_schema(
    entity_state_schema: Mapping[str, object], initial_state_calculations: Sequence[object]
) -> dict[str, Any]:
    """Expose the authored input shape, omitting fields owned by Ruleset calculations."""

    calculations = _calculation_contract(entity_state_schema, initial_state_calculations)
    result = _authored_schema(entity_state_schema, [field for field, _, _ in calculations])
    for field, _, _ in calculations:
        current = result
        parts = field.split(".")
        for part in parts[:-1]:
            current = current["properties"][part]
        current["properties"].pop(parts[-1], None)
    return result


def _calculate(
    participant: Mapping[str, object],
    authored_state: Mapping[str, object],
    calculations: tuple[tuple[str, str, str | None], ...],
    setting_data: Mapping[str, object] | None,
    initial_state_validation: Mapping[str, object] | None = None,
    *, campaign_state: Mapping[str, object] | None = None,
) -> dict[str, Any]:
    state = json.loads(canonical_json_bytes(authored_state))
    participant_inputs = _required_inputs(json.loads(canonical_json_bytes(participant)))
    setting_inputs = _required_inputs(json.loads(canonical_json_bytes(
        {} if setting_data is None else setting_data
    )))
    for index, (field, expression, condition) in enumerate(calculations):
        base = f"initial_state_calculations[{index}]"
        expression_path = f"{base}.condition" if condition is not None else f"{base}.set.value"
        context = ExecutionContext(
            params={
                "participant": participant_inputs, "state": _required_inputs(state),
                "setting_data": setting_inputs,
                "rules": _RequiredInputs(),
                "campaign_state": _required_inputs(json.loads(canonical_json_bytes(campaign_state or {}))),
            }
        )
        try:
            if condition is not None:
                condition_node = _guard_expression(condition, path=expression_path)
                if _reads_root(condition_node, "rules"):
                    context.params["rules"] = _required_inputs(verify_character_rules(
                        participant, state, initial_state_validation, setting_data,
                        campaign_state=campaign_state,
                    ))
                if not bool(evaluate(condition_node, context)):
                    continue
            expression_path = f"{base}.set.value"
            expression_node = _guard_expression(expression, path=expression_path)
            if _reads_root(expression_node, "rules") and not context.params["rules"]:
                context.params["rules"] = _required_inputs(verify_character_rules(
                    participant, state, initial_state_validation, setting_data,
                    campaign_state=campaign_state,
                ))
            value = evaluate(expression_node, context)
        except CommandRejected:
            raise
        except FieldNotFoundError as error:
            raise _rejection(
                "character_initial_state_input_missing",
                "A required expression input is unavailable.",
                path=expression_path,
                guidance="Supply the missing authored input or correct the calculation.",
            ) from error
        except (ExpressionError, TypeError, ValueError, ArithmeticError) as error:
            raise _rejection(
                "character_initial_state_calculation_failed",
                "The Ruleset could not calculate the declared starting value.",
                path=expression_path,
                guidance="Supply every required input and correct the calculation.",
            ) from error
        try:
            encoded = canonical_json_bytes(value)
            if len(encoded) > 256 * 1024:
                raise ValueError("Calculated value exceeds the Character File limit")
            owned_value = json.loads(encoded)
        except (ValueError, TypeError, RecursionError) as error:
            raise _rejection(
                "character_initial_state_calculation_failed",
                "A calculation must produce finite JSON data within the Character File limit.",
                path=f"{base}.set.value",
            ) from error
        _set_path(state, field, owned_value)
    return state


def materialize_initial_character_state(
    participant: Mapping[str, object],
    authored_state: Mapping[str, object],
    *,
    entity_state_schema: Mapping[str, object],
    initial_state_calculations: Sequence[object],
    initial_state_validation: Mapping[str, object] | None = None,
    setting_data: Mapping[str, object] | None = None,
) -> SafePayload:
    """Calculate Ruleset-owned outputs and validate one complete initial state."""

    calculations = _calculation_contract(entity_state_schema, initial_state_calculations)
    for field, _, _ in calculations:
        if _path_present(authored_state, field):
            raise _rejection(
                "character_initial_state_calculated_field_authored",
                "A Ruleset-calculated field was supplied as authored input.",
                path=f"entity_state.{field}",
                guidance="Remove the calculated field and let the Ruleset produce it.",
            )
    try:
        validate_parameters(
            cast(SafePayload, authored_state),
            _authored_schema(entity_state_schema, [field for field, _, _ in calculations]),
        )
    except CommandRejected as error:
        raise _schema_rejection(error) from error
    completed = _calculate(
        participant, authored_state, calculations, setting_data, initial_state_validation,
    )
    try:
        validate_parameters(completed, entity_state_schema)
    except CommandRejected as error:
        raise _schema_rejection(error) from error
    verify_character_rules(participant, completed, initial_state_validation, setting_data)
    return cast(SafePayload, completed)


def verify_initial_character_state(
    participant: Mapping[str, object],
    complete_state: Mapping[str, object],
    *,
    entity_state_schema: Mapping[str, object],
    initial_state_calculations: Sequence[object],
    initial_state_validation: Mapping[str, object] | None = None,
    setting_data: Mapping[str, object] | None = None,
) -> SafePayload:
    """Recompute calculated fields and reject a stale or inconsistent saved state."""

    calculations = _calculation_contract(entity_state_schema, initial_state_calculations)
    original = json.loads(canonical_json_bytes(complete_state))
    authored = deepcopy(original)
    for field, _, _ in calculations:
        _remove_path(authored, field)
    expected = _calculate(
        participant, authored, calculations, setting_data, initial_state_validation,
    )
    for field, _, _ in calculations:
        original_has = _path_present(original, field)
        expected_has = _path_present(expected, field)
        if original_has != expected_has or (
            original_has and _read_path(original, field) != _read_path(expected, field)
        ):
            raise _rejection(
                "character_initial_state_calculated_value_mismatch",
                "A saved Ruleset-calculated field does not match its declared calculation.",
                path=f"entity_state.{field}",
                guidance="Repair the Character File through Builder before using it.",
            )
    try:
        validate_parameters(original, entity_state_schema)
    except CommandRejected as error:
        raise _schema_rejection(error) from error
    verify_character_rules(participant, original, initial_state_validation, setting_data)
    return cast(SafePayload, original)


def initialize_campaign_character_state(
    participant: Mapping[str, object], state: Mapping[str, object],
    declaration: ParticipantArtifactDeclaration, campaign_state: Mapping[str, object],
    setting_data: Mapping[str, object] | None = None,
) -> SafePayload:
    """Apply only explicitly campaign-context calculations, preserving injuries and source cards."""
    if declaration.entity_state_schema is None:
        return cast(SafePayload, json.loads(canonical_json_bytes(state)))
    calculations = _calculation_contract(declaration.entity_state_schema,
                                         declaration.initial_state_calculations)
    selected = tuple(row for row in calculations if any(
        expression is not None and _reads_root(parse_expression(expression), "campaign_state")
        for expression in row[1:]
    ))
    return cast(SafePayload, _calculate(participant, state, selected, setting_data,
                                       declaration.initial_state_validation,
                                       campaign_state=campaign_state))


def verify_placed_character(
    participant: Mapping[str, object], state: Mapping[str, object],
    declaration: ParticipantArtifactDeclaration, setting_data: Mapping[str, object] | None = None,
) -> None:
    """Recheck fields and choices after placement, preserving authored injuries and positions."""
    if declaration.entity_state_schema is not None:
        # Placement may add runtime fields such as token IDs and positions. Existing character
        # fields still retain their declared types, ranges and nested extension policies.
        schema = {**declaration.entity_state_schema, "additionalProperties": True}
        try:
            validate_parameters(state, schema)
        except CommandRejected as error:
            raise _schema_rejection(error) from error
    verify_character_rules(participant, state, declaration.initial_state_validation, setting_data)


def _read_path(root: Mapping[str, object], path: str) -> object:
    current: object = root
    for part in path.split("."):
        if not isinstance(current, Mapping) or part not in current:
            return object()
        current = current[part]
    return current


__all__ = [
    "initial_character_input_schema",
    "materialize_initial_character_state",
    "validate_initial_character_contract",
    "verify_initial_character_state",
    "verify_placed_character",
]
