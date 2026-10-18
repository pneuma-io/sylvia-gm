"""Resolve Ruleset-owned card declarations once, when their mechanic produces a result."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from typing import cast

from sylvia_gm.domain.authority import JsonValue, SafePayload, canonical_payload_is_safe


def project_activity_label(
    declaration: object,
    *,
    parameters: Mapping[str, object],
    public_context: Mapping[str, object],
) -> str | None:
    """Save a bounded routine label; unavailable labels leave the ordinary receipt visible."""
    try:
        return _text(_value(declaration, {"parameters": parameters, "context": public_context}), 80)
    except (TypeError, ValueError):
        return None



def _text(value: object, limit: int = 200) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > limit:
        raise ValueError("Invalid card text")
    return value


def _scalar(value: object) -> JsonValue:
    if value is None or isinstance(value, (str, bool, int)):
        if isinstance(value, str) and len(value) > 4000:
            raise ValueError("Card value is too long")
        return value
    if isinstance(value, float) and math.isfinite(value):
        return value
    raise ValueError("Card values must be scalar")


def _public_value(path_value: object, values: Mapping[str, object]) -> object:
    """Read one bounded public path before validating its scalar or row-list shape."""
    path = _text(path_value, 256).split(".")
    if len(path) > 16 or path[0] not in {"parameters", "context"}:
        raise ValueError("Card path is not a public result or parameter")
    value: object = values
    for field in path:
        if not field or not isinstance(value, Mapping) or field not in value:
            raise ValueError("Card value is unavailable")
        value = value[field]
    return value


def _value(source: object, values: Mapping[str, object]) -> JsonValue:
    """Read explicit result/parameter paths without expressions, state access or interpolation."""
    value = source
    if isinstance(source, Mapping):
        if not {"path"}.issubset(source) or set(source) - {"path", "labels"}:
            raise ValueError("Invalid card value reference")
        value = _public_value(source["path"], values)
        labels = source.get("labels")
        if labels is not None:
            if not isinstance(labels, Mapping) or len(labels) > 64:
                raise ValueError("Invalid card labels")
            label_key = str(_scalar(value))
            if isinstance(value, bool):
                label_key = label_key.lower()
            if label_key not in labels:
                raise ValueError("Card value has no declared label")
            value = _text(labels[label_key], 4000)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        if len(value) > 64:
            raise ValueError("Card list is too long")
        return [_scalar(item) for item in value]
    return _scalar(value)


def _rows(raw: object, values: Mapping[str, object], limit: int) -> list[JsonValue]:
    recorded = isinstance(raw, Mapping)
    if isinstance(raw, Mapping):
        if set(raw) != {"path"}:
            raise ValueError("Invalid card rows reference")
        raw = _public_value(raw["path"], values)
    if not isinstance(raw, Sequence) or isinstance(raw, (str, bytes)) or len(raw) > limit:
        raise ValueError("Invalid card rows")
    rows: list[JsonValue] = []
    for row in raw:
        if not isinstance(row, Mapping) or "value" not in row:
            raise ValueError("Invalid card row")
        if set(row) - {"label", "value", "emphasis", "optional", "omit_zero", "equation"}:
            raise ValueError("Unknown card row field")
        if recorded and isinstance(row["value"], Mapping):
            raise ValueError("Recorded card rows contain literal values, not new references")
        if not isinstance(row.get("optional", False), bool):
            raise ValueError("Invalid optional card row")
        if not isinstance(row.get("emphasis", False), bool):
            raise ValueError("Invalid card emphasis")
        if not isinstance(row.get("omit_zero", False), bool):
            raise ValueError("Invalid zero-value suppression")
        resolved: dict[str, JsonValue] = {}
        if "label" in row:
            resolved["label"] = _text(row["label"])
        try:
            resolved["value"] = _value(row["value"], values)
        except ValueError:
            if row.get("optional") is True:
                continue
            raise
        if row.get("optional") is True and resolved["value"] is None:
            continue
        if (
            row.get("omit_zero") is True
            and type(resolved["value"]) in (int, float)
            and resolved["value"] == 0
        ):
            continue
        resolved["emphasis"] = row.get("emphasis", False)
        if "equation" in row:
            equation = _equation(row["equation"], values, recorded=recorded)
            if equation is not None:
                resolved["equation"] = equation
        rows.append(resolved)
    return rows


def _equation(
    raw: object, values: Mapping[str, object], *, recorded: bool = False,
) -> dict[str, JsonValue] | None:
    """Resolve recorded numbers without performing arithmetic or deciding outcomes."""
    if not isinstance(raw, Mapping) or set(raw) - {
        "die_sides", "roll", "modifier", "total", "optional", "rolls",
    }:
        raise ValueError("Invalid card equation")
    if not isinstance(raw.get("optional", False), bool):
        raise ValueError("Invalid optional equation")
    if recorded and any(isinstance(value, Mapping) for value in raw.values()):
        raise ValueError("Recorded equations contain literal values, not new references")
    try:
        sides = _value(raw.get("die_sides"), values)
        if type(sides) is not int or not 2 <= sides <= 1000:
            raise ValueError("Invalid card die")
        resolved: dict[str, JsonValue] = {"die_sides": sides}
        for key in ("roll", "modifier", "total"):
            value = _value(raw[key], values)
            if value is None and raw.get("optional") is True:
                return None
            if type(value) not in (int, float):
                raise ValueError("Equation requires recorded numbers")
            resolved[key] = value
        if "rolls" in raw:
            rolls = _value(raw["rolls"], values)
            if not isinstance(rolls, list) or not 1 <= len(rolls) <= 64 or any(
                type(roll) is not int or not 1 <= roll <= sides for roll in rolls
            ):
                raise ValueError("Invalid recorded dice pool")
            resolved["rolls"] = rolls
    except (KeyError, ValueError):
        if raw.get("optional") is True:
            return None
        raise ValueError("Equation value unavailable") from None
    return resolved




def project_result_card(
    declaration: object,
    *,
    parameters: Mapping[str, object],
    public_context: Mapping[str, object],
) -> SafePayload | None:
    """Save a detached presentation; invalid display data never changes the mechanic's outcome.

    The caller supplies only its public context, after removing private spatial variables. Cards
    cannot read live state, private provenance or source documents. Reopening uses this saved value.
    Button parameters are a historical request; the action owner must revalidate them when clicked.
    """
    if not isinstance(declaration, Mapping):
        return None
    try:
        if type(declaration.get("version")) is not int or declaration["version"] != 1:
            return None
        if set(declaration) - {
            "version", "title", "summary", "details", "actions", "footprint", "details_label",
            "subtitle", "equation",
            "equations",
        }:
            return None
        values = {"parameters": parameters, "context": public_context}
        card: dict[str, JsonValue] = {
            "version": 1,
            "summary": _rows(declaration.get("summary", []), values, 16),
            "details": _rows(declaration.get("details", []), values, 128),
        }
        if "title" in declaration:
            card["title"] = _text(_value(declaration["title"], values))
        if "subtitle" in declaration:
            card["subtitle"] = _text(_value(declaration["subtitle"], values))
        if "equation" in declaration:
            equation = _equation(declaration["equation"], values)
            if equation is not None:
                card["equation"] = equation
        if "equations" in declaration:
            equations = _rows(declaration["equations"], values, 16)
            if any(not isinstance(row, Mapping) or "equation" not in row for row in equations):
                raise ValueError("A featured equation requires recorded numeric values")
            card["equations"] = equations
        if "footprint" in declaration:
            if declaration["footprint"] not in ("inline", "compact"):
                raise ValueError("Invalid card footprint")
            card["footprint"] = declaration["footprint"]
        if "details_label" in declaration:
            card["details_label"] = _text(declaration["details_label"])
        raw_actions = declaration.get("actions", [])
        if not isinstance(raw_actions, list) or len(raw_actions) > 8:
            raise ValueError("Invalid card actions")
        actions: list[JsonValue] = []
        for action in raw_actions:
            if not isinstance(action, Mapping) or set(action) != {"label", "tool", "parameters"}:
                raise ValueError("Invalid card action")
            tool = _text(action["tool"], 128)
            if not tool.replace("_", "a").isalnum():
                raise ValueError("Invalid card action tool")
            raw_parameters = action["parameters"]
            if not isinstance(raw_parameters, Mapping) or len(raw_parameters) > 64:
                raise ValueError("Invalid card action parameters")
            actions.append({
                "label": _text(action["label"]),
                "tool": tool,
                "parameters": {
                    _text(key, 128): _value(value, values)
                    for key, value in raw_parameters.items()
                },
            })
        card["actions"] = actions
        if not any(card.get(key) for key in ("title", "summary", "details", "actions")):
            return None
        if not canonical_payload_is_safe(card):
            return None
        return cast(SafePayload, card)
    except (TypeError, ValueError):
        return None
