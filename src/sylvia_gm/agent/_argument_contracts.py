"""Per-choice argument contracts: a small model-facing envelope over one large tool schema.

A package may declare, at the root of a tool's input schema, which inputs each value of one
selector input can use (version 1)::

    "x-sylvia-gm-argument-contracts": {
        "version": 1, "selector": "spell", "field": "spell_arguments",
        "common": ["caster", ...], "by_value": {"fire_bolt": ["target", ...], ...}
    }

The model is shown only the selector, the common inputs and one object field. The selected
value's own inputs are read with ``read_tool_arguments`` (or come back in a refusal), and are
passed inside that field or at the top level. Before the existing mechanics run, the turn loop
checks the field against the exact selected contract, then flattens the call. The executable
schema, package validation and every engine check are unchanged, so a flat call that worked
with the full schema still works.
"""
from __future__ import annotations

import copy
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

CONTRACT_KEY = "x-sylvia-gm-argument-contracts"
ARGUMENTS_TOOL_NAME = "read_tool_arguments"


@dataclass(frozen=True)
class ArgumentContract:
    selector: str
    field: str
    common: frozenset[str]
    by_value: Mapping[str, frozenset[str]]
    aliases: Mapping[str, str]

    def canonical(self, value: object) -> str | None:
        if not isinstance(value, str):
            return None
        return self.aliases.get(value, value)

    def selected(self, value: object) -> frozenset[str] | None:
        """The inputs the selected value may use beyond the envelope, or None if unknown."""
        if isinstance(value, str) and value in self.by_value:
            return self.by_value[value]
        canonical = self.canonical(value)
        return self.by_value.get(canonical) if canonical is not None else None


def _names(value: object, where: str) -> frozenset[str]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes)) or not all(
            isinstance(item, str) for item in value):
        raise ValueError(f"Argument contract {where} must be a list of input names")
    return frozenset(value)


def argument_contract(schema: Mapping[str, object]) -> ArgumentContract | None:
    """Read a tool schema's version-1 contract; a malformed declaration is a package defect."""
    raw = schema.get(CONTRACT_KEY)
    if raw is None:
        return None
    if not isinstance(raw, Mapping) or raw.get("version") != 1:
        raise ValueError("Unsupported argument contract declaration")
    properties = schema.get("properties")
    selector, field = raw.get("selector"), raw.get("field")
    if not isinstance(properties, Mapping) or not isinstance(selector, str) \
            or not isinstance(field, str) or selector not in properties or field in properties:
        raise ValueError("Argument contract selector or field is invalid")
    common = _names(raw.get("common"), "common")
    by_value_raw = raw.get("by_value")
    if not isinstance(by_value_raw, Mapping):
        raise ValueError("Argument contract by_value must be an object")
    # The model projection hides derived and engine-only inputs; a contract names the package
    # schema's inputs, so keep only those the model is shown.
    declared = frozenset(properties)
    common &= declared
    by_value = {str(value): (_names(names, f"by_value.{value}") & declared) - common
                for value, names in by_value_raw.items()}
    selector_schema = properties[selector]
    raw_aliases = (selector_schema.get("x-sylvia-gm-value-aliases")
                   if isinstance(selector_schema, Mapping) else None)
    aliases = ({str(k): str(v) for k, v in raw_aliases.items()}
               if isinstance(raw_aliases, Mapping) else {})
    return ArgumentContract(selector, field, common - {selector}, by_value, aliases)


def _field_description(contract: ArgumentContract) -> str:
    return (
        f"The inputs of the selected {contract.selector} beyond those listed here. Read them "
        f"once per {contract.selector} with {ARGUMENTS_TOOL_NAME} (this tool's name and the "
        f"{contract.selector}), then pass them in this object. Omit it when the selected "
        f"{contract.selector} needs nothing more. An input this tool does not have, or one "
        "already given at the top level, is refused before anything resolves."
    )


def _used_definitions(value: object, definitions: Mapping[str, object]) -> set[str]:
    used: set[str] = set()
    pending = [value]
    while pending:
        item = pending.pop()
        if isinstance(item, Mapping):
            reference = item.get("$ref")
            if isinstance(reference, str) and reference.startswith("#/$defs/"):
                key = reference.removeprefix("#/$defs/")
                if key not in used:
                    used.add(key)
                    pending.append(definitions.get(key))
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return used


def envelope_schema(schema: Mapping[str, Any], contract: ArgumentContract) -> dict[str, Any]:
    """The model-facing schema: the selector, common inputs and one object for the rest."""
    projected = {key: copy.deepcopy(value) for key, value in schema.items()
                 if key not in {CONTRACT_KEY, "properties", "required", "$defs"}}
    properties = schema["properties"]
    kept = {name: copy.deepcopy(child) for name, child in properties.items()
            if name == contract.selector or name in contract.common}
    kept[contract.field] = {"type": "object", "description": _field_description(contract)}
    projected["properties"] = kept
    projected["required"] = [name for name in schema.get("required", ()) if name in kept]
    definitions = schema.get("$defs")
    if isinstance(definitions, Mapping):
        used = _used_definitions(kept, definitions)
        if used:
            projected["$defs"] = {key: copy.deepcopy(definitions[key])
                                  for key in sorted(used)}
    return projected


def _inline(value: Any, definitions: Mapping[str, Any]) -> Any:
    if isinstance(value, Mapping):
        reference = value.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/") and len(value) == 1:
            return _inline(definitions[reference.removeprefix("#/$defs/")], definitions)
        return {key: _inline(item, definitions) for key, item in value.items()}
    if isinstance(value, list):
        return [_inline(item, definitions) for item in value]
    return copy.deepcopy(value)


def selected_schema(schema: Mapping[str, Any], contract: ArgumentContract,
                    value: object) -> dict[str, Any] | None:
    """The exact object schema of the selected value's inputs, or None for an unknown value."""
    names = contract.selected(value)
    if names is None:
        return None
    properties = schema["properties"]
    raw_definitions = schema.get("$defs")
    definitions: Mapping[str, Any] = (
        raw_definitions if isinstance(raw_definitions, Mapping) else {})
    return {
        "type": "object",
        "properties": {name: _inline(properties[name], definitions)
                       for name in sorted(names) if name in properties},
        "required": [name for name in schema.get("required", ()) if name in names],
        "additionalProperties": False,
    }


def unwrap_arguments(
    arguments: dict[str, Any], schema: Mapping[str, Any], contract: ArgumentContract,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Flatten one call, checking the contract field against the selected contract.

    Common inputs stay at the top level. The selected value's inputs may be at the top level or
    inside the contract field. Inside the field, an input the tool does not have, one that
    belongs at the top level, a duplicate, or a selected input whose value its schema refuses
    is an issue. Any other input reaches the existing checks exactly as before this envelope,
    which ignore an input the selected value never reads and refuse one it forbids, so a call
    that succeeded with the full schema still succeeds.
    """
    flat = dict(arguments)
    nested = flat.pop(contract.field, None)
    if nested is None:
        return flat, []
    if not isinstance(nested, Mapping):
        return flat, [{"path": contract.field, "kind": "not_an_object"}]
    issues: list[dict[str, Any]] = []
    envelope = contract.common | {contract.selector}
    properties = schema["properties"]
    for name in sorted(nested):
        path = f"{contract.field}.{name}"
        if name in flat:
            issues.append({"path": path, "kind": "duplicate_input"})
        elif name in envelope:
            issues.append({"path": path, "kind": "belongs_at_top_level"})
        elif name not in properties:
            issues.append({"path": path, "kind": "unknown_input"})
    allowed = contract.selected(flat.get(contract.selector)) or frozenset()
    if not issues and allowed:
        from jsonschema import Draft202012Validator

        selected = selected_schema(schema, contract, flat.get(contract.selector)) or {}
        values = {name: nested[name] for name in nested if name in allowed}
        for error in Draft202012Validator(selected).iter_errors(values):
            if error.validator == "required":
                continue  # required inputs are reported by the existing missing-input check
            issues.append({"path": ".".join([contract.field,
                                             *(str(p) for p in error.absolute_path)]),
                           "kind": "invalid_value", "detail": error.message[:300]})
    return {**flat, **nested}, issues


ARGUMENTS_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": ARGUMENTS_TOOL_NAME,
        "description": (
            "READ-ONLY: return the exact inputs one choice of a tool accepts, for a tool whose "
            "schema keeps them in one object field (for example a spell's own casting inputs). "
            "Call it before using that choice for the first time in this conversation."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "tool": {"type": "string", "description": "The tool's name as listed."},
                "value": {"type": "string",
                          "description": "The selected choice, for example the spell."},
            },
            "required": ["tool", "value"],
            "additionalProperties": False,
        },
    },
}


def read_tool_arguments(
    parameters: object, tools: Mapping[str, tuple[Mapping[str, Any], ArgumentContract]],
) -> dict[str, Any]:
    """Answer one read from the round's contract tools, keyed by the listed tool name."""
    if not isinstance(parameters, Mapping):
        return {"status": "rejected", "code": "provider_tool_arguments_not_object"}
    name, value = parameters.get("tool"), parameters.get("value")
    entry = None
    if isinstance(name, str):
        # A model may name the tool as listed, by its operation, or with the prefix its
        # transport adds (Claude Code shows Sylvia's tools as mcp__sylvia__<name>).
        bare = name.rsplit("__", 1)[-1] if name.startswith("mcp__") else name
        entry = tools.get(bare) or next((item for listed, item in tools.items()
                                         if listed.endswith("_" + bare)), None)
    if entry is None:
        return {"status": "rejected", "code": "argument_contract_tool_unavailable",
                "available_tools": sorted(tools)}
    schema, contract = entry
    selected = selected_schema(schema, contract, value)
    if selected is None:
        return {"status": "rejected", "code": "argument_contract_value_unknown",
                "instruction": f"Choose a {contract.selector} the tool lists."}
    return {
        "status": "ok", "tool": name, contract.selector: contract.canonical(value),
        "arguments_field": contract.field, "arguments": selected,
        "instruction": (f"Pass these inside {contract.field} (or at the top level); keep the "
                        f"tool's own listed inputs at the top level."),
    }
