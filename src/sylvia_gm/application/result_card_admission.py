"""Authoring-time result-card checks with field-level, value-free diagnostics."""

from __future__ import annotations

import math
from collections.abc import Collection, Mapping, Sequence

from sylvia_gm.domain.content_validation import ContentShapeError, validate_content_shape
from sylvia_gm.domain.errors import RulesetDiagnostic


def validate_activity_label(value: object, *, path: str) -> None:
    """Accept a short literal label or one public path to a conditional label."""
    if isinstance(value, str) and value.strip() and len(value) <= 80:
        return
    if (isinstance(value, Mapping) and set(value) == {"path"}
            and isinstance(value["path"], str) and 1 <= len(value["path"]) <= 256):
        issues = _value_issues(value, path)
        if not issues:
            return
        raise ContentShapeError(tuple(issues))
    raise ContentShapeError((_diagnostic(
        path, "An activity label is 1–80 characters or a public result/parameter path.",
        "Supply a neutral routine-operation label; use a result card for meaningful outcomes.",
    ),))



def _full_path(prefix: str, local: str) -> str:
    """Attach a definition-relative JSON path to its runtime tool metadata location."""

    return prefix + local[1:] if local.startswith("$") else f"{prefix}.{local}"


def _diagnostic(path: str, message: str, guidance: str) -> RulesetDiagnostic:
    """Build one stable correction without copying the rejected authored value."""

    return RulesetDiagnostic(
        code="result_card_declaration_invalid",
        phase="runtime",
        file="runtime/ruleset.yaml",
        path=path,
        message=message,
        guidance=guidance,
    )


def _value_issues(value: object, path: str) -> list[RulesetDiagnostic]:
    """Check procedural scalar and public-path rules not expressed by the shared schema."""

    if isinstance(value, float) and not math.isfinite(value):
        return [_diagnostic(path, "Use a finite card value.", "Replace the non-finite number.")]
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        issues: list[RulesetDiagnostic] = []
        for index, item in enumerate(value):
            issues.extend(_value_issues(item, f"{path}[{index}]"))
        return issues
    if not isinstance(value, Mapping):
        return []
    source = value.get("path")
    if not isinstance(source, str):  # Shared shape validation owns this correction.
        return []
    parts = source.split(".")
    if len(parts) > 16 or any(not part for part in parts):
        return [
            _diagnostic(
                f"{path}.path",
                "Card paths use one to 16 nonempty dot-separated segments.",
                "Shorten the path and remove empty segments.",
            )
        ]
    if parts[0] not in {"parameters", "context"}:
        return [
            _diagnostic(
                f"{path}.path",
                "Card paths begin with parameters or context.",
                "Reference an admitted tool parameter or public result-context field.",
            )
        ]
    return []




def _equation_issues(equation: object, path: str) -> list[RulesetDiagnostic]:
    """Check every public value path in a card or recipient equation."""
    if not isinstance(equation, Mapping):
        return []
    return [issue for field in ("die_sides", "roll", "modifier", "total", "rolls")
            for issue in _value_issues(equation.get(field), f"{path}.{field}")]


def validate_result_card_declaration(
    value: object,
    *,
    path: str = "$.result_card",
    tool_names: Collection[str] | None = None,
) -> None:
    """Reject an invalid Ruleset-authored declaration before package publication."""

    try:
        validate_content_shape("result_card", value, file="runtime/ruleset.yaml")
    except ContentShapeError as error:
        raise ContentShapeError(
            tuple(
                RulesetDiagnostic(
                    code=item.code,
                    phase="runtime",
                    file="runtime/ruleset.yaml",
                    path=_full_path(path, item.path or "$"),
                    message=item.message,
                    guidance=item.guidance,
                )
                for item in error.diagnostics
            )
        ) from error

    declaration = value
    assert isinstance(declaration, Mapping)  # Proved by shared shape validation.
    issues: list[RulesetDiagnostic] = []
    for field in ("title", "subtitle"):
        if field in declaration:
            issues.extend(_value_issues(declaration[field], f"{path}.{field}"))
    issues.extend(_equation_issues(declaration.get("equation"), f"{path}.equation"))
    if not any(declaration.get(field) for field in ("title", "summary", "details", "actions")):
        issues.append(
            _diagnostic(
                path,
                "A result card must declare visible content or an action.",
                "Supply a title, row, or action, or remove the result_card declaration.",
            )
        )
    for section in ("summary", "details", "equations"):
        rows = declaration.get(section, ())
        if isinstance(rows, Mapping):
            issues.extend(_value_issues(rows, f"{path}.{section}"))
        if isinstance(rows, Sequence) and not isinstance(rows, (str, bytes, bytearray)):
            for index, row in enumerate(rows):
                if isinstance(row, Mapping) and "value" in row:
                    issues.extend(_value_issues(row["value"], f"{path}.{section}[{index}].value"))
                    issues.extend(_equation_issues(
                        row.get("equation"), f"{path}.{section}[{index}].equation",
                    ))
    actions = declaration.get("actions", ())
    if isinstance(actions, Sequence) and not isinstance(actions, (str, bytes, bytearray)):
        for index, action in enumerate(actions):
            if not isinstance(action, Mapping):
                continue
            tool = action.get("tool")
            if isinstance(tool, str) and not tool.replace("_", "a").isalnum():
                issues.append(
                    _diagnostic(
                        f"{path}.actions[{index}].tool",
                        "Use only alphanumeric characters and underscores in card tool names.",
                        "Use the exact declared Ruleset tool name.",
                    )
                )
            elif isinstance(tool, str) and tool_names is not None and tool not in tool_names:
                issues.append(
                    _diagnostic(
                        f"{path}.actions[{index}].tool",
                        "Card action names an undeclared Ruleset tool.",
                        "Use the exact name of a tool declared in this runtime and manifest.",
                    )
                )
            parameters = action.get("parameters")
            if isinstance(parameters, Mapping):
                for name, parameter_value in parameters.items():
                    issues.extend(
                        _value_issues(
                            parameter_value,
                            f"{path}.actions[{index}].parameters.{name}",
                        )
                    )
    if issues:
        raise ContentShapeError(tuple(issues[:16]))


__all__ = ["validate_result_card_declaration"]
