"""Current choices kept outside the fixed GM tool definitions."""
from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from typing import Any


def schema_choices(current: Mapping[str, Any], fixed: Mapping[str, Any],
                   path: str = "$") -> dict[str, Any]:
    """Describe only constraints omitted from the fixed projection, using schema paths.

    A participant reference is left out: its IDs are in the campaign state, and listing a
    participant-only field's choices read as the only allowed target to a model (issue 235).
    The turn loop still refuses a value outside it, naming the allowed values.
    """
    choices: dict[str, Any] = {}
    if ("enum" in current and current["enum"] != fixed.get("enum")
            and current.get("x-sylvia-gm-reference") != "participant"):
        choices[path] = {"allowed_values": current["enum"]}
    properties = current.get("properties", {})
    fixed_properties = fixed.get("properties", {})
    if isinstance(properties, Mapping) and isinstance(fixed_properties, Mapping):
        if set(properties) != set(fixed_properties):
            choices.setdefault(path, {})["available_fields"] = sorted(properties)
        for name, child in properties.items():
            if isinstance(child, Mapping):
                choices.update(schema_choices(child, fixed_properties.get(name, {}),
                                              f"{path}.{name}"))
    items, fixed_items = current.get("items"), fixed.get("items", {})
    if isinstance(items, Mapping) and isinstance(fixed_items, Mapping):
        choices.update(schema_choices(items, fixed_items, path + "[]"))
    return choices


def argument_choices_record(fixed: Sequence[Mapping[str, Any]],
                            offered: Sequence[Mapping[str, Any]] | None) -> str:
    """Put exact current enum choices beside the callable tools, never in the prefix."""
    by_name = {tool["function"]["name"]: tool["function"]["parameters"] for tool in fixed}
    choices = {}
    for tool in offered or ():
        function = tool["function"]
        fields = schema_choices(function["parameters"], by_name.get(function["name"], {}))
        if fields:
            choices[function["name"]] = fields
    return (
        "\nCurrent argument choices (the latest record replaces earlier choices; $ is the "
        "argument object and [] means each array item). Each entry limits only the field it "
        "names: use its allowed_values or available_fields there. A field not listed is not "
        "limited by this record (a target field not listed may name any current creature or "
        "object). All other schema and engine checks still apply:\n"
        + json.dumps(choices, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    )


def unavailable_values(arguments: Mapping[str, Any], current: Mapping[str, Any],
                       fixed: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Check the current enums removed by fixed projection before submitting a mutation."""
    issues: list[dict[str, Any]] = []

    def visit(value: Any, schema: Mapping[str, Any], stable: Mapping[str, Any], path: str) -> None:
        if ("enum" in schema and schema["enum"] != stable.get("enum")
                and value not in schema["enum"]):
            issues.append({"path": path, "kind": "invalid_enum_value",
                           "allowed_values": schema["enum"]})
        if isinstance(value, Mapping):
            properties = schema.get("properties", {})
            stable_properties = stable.get("properties", {})
            for name, child in properties.items():
                if name in value and isinstance(child, Mapping):
                    visit(value[name], child, stable_properties.get(name, {}),
                          f"{path}.{name}" if path else name)
        elif isinstance(value, list) and isinstance(schema.get("items"), Mapping):
            for index, item in enumerate(value):
                visit(item, schema["items"], stable.get("items", {}), f"{path}[{index}]")

    visit(arguments, current, fixed, "")
    return issues


def compact_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Factor repeated simple property schemas into local refs only when bytes decrease.

    These are exact copies, including descriptions and bounds. Object/enum/reference scopes
    stay inline so current argument choices can still be compared directly with the catalog.
    Execution always validates the original package schema, independently of this projection.
    """
    from collections import Counter

    properties = schema.get("properties")
    if not isinstance(properties, dict) or "$defs" in schema:
        return schema

    def eligible(value: Any) -> bool:
        if isinstance(value, dict):
            if any(key in value for key in ("$ref", "$id", "$defs", "properties", "enum")):
                return False
            if value.get("x-sylvia-gm-reference") == "participant":
                return False
            if value.get("x-sylvia-gm-castable-spells"):
                return False
            return all(eligible(item) for item in value.values())
        return not isinstance(value, list) or all(eligible(item) for item in value)

    def dump(value: Any) -> str:
        return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    counts = Counter(dump(child) for child in properties.values() if eligible(child))
    definitions: dict[str, Any] = {}
    references = {}
    for serialized, count in sorted(counts.items()):
        key = f"p{len(definitions)}"
        reference = {"$ref": f"#/$defs/{key}"}
        size = len(serialized.encode("utf-8"))
        # Include the definition's key and the $defs object overhead in each candidate's cost.
        if count * size <= size + count * len(dump(reference).encode("utf-8")) + len(key) + 16:
            continue
        definitions[key] = json.loads(serialized)
        references[serialized] = reference
    if definitions:
        schema["properties"] = {name: references.get(dump(child), child)
                                for name, child in properties.items()}
        schema["$defs"] = definitions
    return schema
