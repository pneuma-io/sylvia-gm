"""Ruleset YAML validation.

Validates the executable layer (tools, procedures) of a ruleset YAML.
Cross-references checks, syntax validation, and structural checks.
"""

from __future__ import annotations

import math
import re
from collections.abc import Iterator, Mapping, Sequence, Set
from typing import Any, cast

import yaml

from sylvia_gm.runtime._effect_executor import _EFFECT_HANDLERS
from sylvia_gm.runtime._exceptions import ExpressionError
from sylvia_gm.runtime._expression_parser import parse_expression
from sylvia_gm.runtime._parameter_declarations import (
    ParameterDeclarationError,
    resolve_parameter_declarations,
)

# ── Valid parameter types ─────────────────────────────────────────────────

VALID_PARAM_TYPES: frozenset[str] = frozenset(
    {
        "entity_id",
        "string",
        "integer",
        "number",
        "boolean",
        "array",
        "object",
        "enum",
        "entity_id_list",
    }
)

# ── Valid effect keys ─────────────────────────────────────────────────────

VALID_EFFECT_KEYS: frozenset[str] = frozenset(_EFFECT_HANDLERS)

_SPATIAL_QUERY_FIELDS: dict[str, frozenset[str]] = {
    "body_proximity": frozenset(
        {
            "map_id",
            "moving_entity_id",
            "fixed_entity_id",
            "destination",
            "radius",
        }
    ),
    "sweep": frozenset(
        {
            "map_id",
            "moving_entity_id",
            "destination",
            "include_entities",
            "exclude_entity_ids",
            "metadata_filter",
        }
    ),
    "placement": frozenset(
        {
            "map_id",
            "moving_entity_id",
            "origin",
            "exclude_entity_ids",
            "metadata_filter",
            "max_candidates",
            "containing_zone_id",
            "reference_face",
        }
    ),
    "ray": frozenset({"map_id", "origin", "destination", "include", "max_intersections"}),
    "exposure": frozenset(
        {"map_id", "observer", "target", "samples", "include", "metadata_filter"}
    ),
    "path": frozenset(
        {
            "map_id",
            "start",
            "goal",
            "allow_diagonal",
            "corner_policy",
            "include_entities",
            "moving_entity_id",
            "metadata_filter",
            "max_steps",
        }
    ),
    "crossed_zones": frozenset({"map_id", "path", "max_crossings"}),
    "area": frozenset({"map_id", "shape", "include", "max_results"}),
}
_SPATIAL_TEMPLATE = re.compile(r"^\{[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*\}$")
_SPATIAL_MUTATION_FIELDS = {
    "geometry": frozenset(
        {
            "volume",
            "active",
            "movement",
            "occupies_cells",
            "visibility",
            "metadata",
        }
    ),
    "entity": frozenset(
        {
            "position",
            "footprint",
            "height",
            "facing",
            "active",
            "movement",
            "visibility",
            "metadata",
        }
    ),
}

# ── Valid trigger names ───────────────────────────────────────────────────

VALID_TRIGGERS: frozenset[str] = frozenset(
    {
        "on_combat_start",
        "on_combat_end",
        "on_turn_start",
        "on_turn_end",
        "on_hp_zero",
        "on_hp_restored",
        "on_damage_taken",
        "on_condition_applied",
        "on_condition_removed",
        "on_ally_down",
        "on_critical",
        "on_rest",
        "on_level_up",
        "on_spell_cast",
        "on_death",
    }
)
VALID_TRIGGER_PATTERN: re.Pattern[str] = re.compile(r"^on_[a-z][a-z0-9_]*$")


# ── Top-level validation ──────────────────────────────────────────────────


def validate_ruleset(yaml_content: str) -> list[str]:
    """Validate a ruleset YAML string for structural and syntax errors.

    Args:
        yaml_content: Raw YAML string content.

    Returns:
        List of error messages (empty = valid).
    """
    try:
        data = yaml.safe_load(yaml_content)
    except yaml.YAMLError as e:
        return [f"YAML syntax error: {e}"]
    return validate_ruleset_data(data)


def validate_ruleset_data(data: object) -> list[str]:
    """Validate already parsed ruleset data without parsing its source again."""

    if not isinstance(data, dict):
        return ["Ruleset YAML must be a mapping (dict) at top level"]

    errors = validate_parameter_declarations(data)
    try:
        data = resolve_parameter_declarations(data)
    except ParameterDeclarationError as error:
        return [*errors, str(error)]
    is_package_runtime = "sylvia_gm_runtime" in data
    if is_package_runtime:
        if "meta" in data:
            errors.append("'meta' is manifest-owned and is not allowed in package runtime YAML")
    else:
        meta = data.get("meta")
        if not isinstance(meta, dict):
            errors.append("Missing required section: 'meta'")
        elif "id" not in meta:
            errors.append("meta.id is required (canonical ruleset identifier)")
        elif not re.match(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$", str(meta["id"])):
            errors.append(f"meta.id '{meta['id']}' must be lowercase kebab-case")

    # Validate tools
    tools = data.get("tools", {})
    all_tool_names: set[str] = set()
    if not isinstance(tools, dict):
        errors.append("'tools' section must be a mapping")
    else:
        all_tool_names = set(tools.keys())
        errors.extend(_validate_tools(tools))

    if is_package_runtime and "model_tool_availability" in data:
        errors.append(
            "'model_tool_availability' is manifest-owned and is not allowed in runtime YAML"
        )
    elif not is_package_runtime:
        errors.extend(
            _validate_model_tool_availability(
                data.get("model_tool_availability", {}),
                all_tool_names,
            )
        )
    boundary = data.get("model_decision_boundary")
    scope = data.get("model_decision_scope")
    if boundary is not None and scope is not None:
        errors.append("Ruleset may declare only one model decision boundary form")
    errors.extend(_validate_model_decision_boundary(boundary))
    errors.extend(_validate_model_decision_scope(scope))

    # Validate procedures
    procedures = data.get("procedures", {})
    if not isinstance(procedures, dict):
        errors.append("'procedures' section must be a mapping")
    else:
        errors.extend(_validate_procedures(procedures, all_tool_names))

    # Validate definitions
    definitions_value = data.get("definitions", {})
    definition_row_schemas = data.get("definition_row_schemas", {})
    if definitions_value is None:
        definitions: dict[str, Any] = {}
    elif isinstance(definitions_value, dict):
        definitions = definitions_value
    else:
        errors.append("'definitions' section must be a mapping or null")
        definitions = {}
    if not isinstance(definition_row_schemas, dict):
        errors.append("'definition_row_schemas' section must be a mapping")
    else:
        errors.extend(_validate_definition_row_schemas(definitions, definition_row_schemas))
        lookup_owners: list[tuple[str, Mapping[str, Any], object]] = []
        if isinstance(tools, dict):
            lookup_owners.extend(
                (f"Tool '{name}'", definition, definition.get("effects", []))
                for name, definition in tools.items()
                if isinstance(definition, Mapping)
            )
        if isinstance(procedures, dict):
            lookup_owners.extend(
                (f"Procedure '{name}'", {}, definition.get("steps", []))
                for name, definition in procedures.items()
                if isinstance(definition, Mapping)
            )
        errors.extend(
            _validate_definition_lookups(
                lookup_owners,
                definitions,
                definition_row_schemas,
            )
        )

    # Validate narrative
    narrative = data.get("narrative", {})
    if narrative is not None and not isinstance(narrative, dict):
        errors.append("'narrative' section must be a mapping or null")

    return errors


def _iter_effects(effects: object) -> Iterator[Mapping[str, Any]]:
    if not isinstance(effects, list):
        return
    for effect in effects:
        if not isinstance(effect, dict):
            continue
        yield effect
        for branch in ("on_true", "on_false", "do", "consequence"):
            yield from _iter_effects(effect.get(branch))
        for handler_name in VALID_EFFECT_KEYS:
            body = effect.get(handler_name)
            if not isinstance(body, dict):
                continue
            for branch in ("on_true", "on_false", "do", "consequence"):
                yield from _iter_effects(body.get(branch))


def _definition_table(definitions: Mapping[str, Any], path: str) -> Mapping[object, Any] | None:
    parts = path.split(".")
    if len(parts) < 2 or parts[0] != "definitions":
        return None
    current: object = definitions
    for part in parts[1:]:
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, Mapping) else None


_DEFINITION_SCHEMA_FIELDS = frozenset(
    {
        "type",
        "properties",
        "required",
        "additionalProperties",
        "items",
        "enum",
        "minimum",
        "maximum",
        "minLength",
        "uniqueItems",
    }
)
_DEFINITION_SCHEMA_TYPES = frozenset(
    {"string", "integer", "number", "boolean", "object", "array", "null"}
)
_DEFINITION_VARIANT_FIELDS = frozenset({"discriminator", "variants"})


def _schema_value_matches(value: object, expected: object) -> bool:
    if not isinstance(expected, str):
        return False
    return {
        "string": isinstance(value, str),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "object": isinstance(value, Mapping),
        "array": isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)),
        "null": value is None,
    }.get(expected, False)


def _validate_definition_schema(schema: Mapping[str, Any], *, label: str) -> list[str]:
    errors: list[str] = []
    unknown = set(schema) - _DEFINITION_SCHEMA_FIELDS
    if unknown:
        errors.append(f"{label} contains unsupported field '{sorted(unknown)[0]}'")
    expected = schema.get("type")
    if expected not in _DEFINITION_SCHEMA_TYPES:
        errors.append(f"{label} has an invalid type")
        return errors
    object_only = set(schema) & {"properties", "required", "additionalProperties"}
    if expected != "object" and object_only:
        errors.append(
            f"{label} field '{sorted(object_only)[0]}' is not valid for type '{expected}'"
        )
    if expected != "array" and "items" in schema:
        errors.append(f"{label} field 'items' is not valid for type '{expected}'")
    if "uniqueItems" in schema and (
        expected != "array" or not isinstance(schema["uniqueItems"], bool)
    ):
        errors.append(f"{label} uniqueItems requires an array and a boolean")
    enum = schema.get("enum")
    if enum is not None and (
        not isinstance(enum, list)
        or not enum
        or any(not _schema_value_matches(value, expected) for value in enum)
        or len({repr(value) for value in enum}) != len(enum)
    ):
        errors.append(f"{label} has an invalid enum")
    minimum = schema.get("minimum")
    maximum = schema.get("maximum")
    for bound_name, bound in (("minimum", minimum), ("maximum", maximum)):
        if bound_name in schema and (
            expected not in {"integer", "number"}
            or not isinstance(bound, (int, float))
            or isinstance(bound, bool)
        ):
            errors.append(f"{label} has an invalid {bound_name}")
    if (
        isinstance(minimum, (int, float))
        and not isinstance(minimum, bool)
        and isinstance(maximum, (int, float))
        and not isinstance(maximum, bool)
        and minimum > maximum
    ):
        errors.append(f"{label} minimum exceeds maximum")
    min_length = schema.get("minLength")
    if "minLength" in schema and (
        expected != "string"
        or not isinstance(min_length, int)
        or isinstance(min_length, bool)
        or min_length < 0
    ):
        errors.append(f"{label} has an invalid minimum length")

    if expected == "object":
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            errors.append(f"{label} properties are invalid")
            return errors
        required = schema.get("required", [])
        if (
            not isinstance(required, list)
            or any(not isinstance(item, str) for item in required)
            or len(set(required)) != len(required)
            or any(item not in properties for item in required)
        ):
            errors.append(f"{label} required fields are invalid")
        if not isinstance(schema.get("additionalProperties", True), bool):
            errors.append(f"{label} additionalProperties must be a boolean")
        for name, child in properties.items():
            if not isinstance(name, str) or not isinstance(child, Mapping):
                errors.append(f"{label} property schemas are invalid")
                continue
            errors.extend(_validate_definition_schema(child, label=f"{label}.{name}"))
    elif expected == "array":
        items = schema.get("items")
        if not isinstance(items, Mapping):
            errors.append(f"{label} has invalid array items schema")
        else:
            errors.extend(_validate_definition_schema(items, label=f"{label}.items"))
    return errors


def _validate_definition_value(
    value: object, schema: Mapping[str, Any], *, label: str
) -> list[str]:
    expected = schema.get("type")
    if not _schema_value_matches(value, expected):
        return [f"{label} has invalid type"]
    errors: list[str] = []
    enum = schema.get("enum")
    if isinstance(enum, list) and value not in enum:
        errors.append(f"{label} is outside its enum")
    minimum = schema.get("minimum")
    if (
        isinstance(minimum, (int, float))
        and not isinstance(minimum, bool)
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value < minimum
    ):
        errors.append(f"{label} is below its minimum")
    maximum = schema.get("maximum")
    if (
        isinstance(maximum, (int, float))
        and not isinstance(maximum, bool)
        and isinstance(value, (int, float))
        and not isinstance(value, bool)
        and value > maximum
    ):
        errors.append(f"{label} is above its maximum")
    min_length = schema.get("minLength")
    if isinstance(min_length, int) and isinstance(value, str) and len(value) < min_length:
        errors.append(f"{label} is shorter than its minimum length")
    if expected == "object" and isinstance(value, Mapping):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        if isinstance(properties, Mapping) and isinstance(required, list):
            missing = [name for name in required if name not in value]
            if missing:
                errors.append(f"{label} is missing required field '{missing[0]}'")
            if schema.get("additionalProperties") is False:
                extra = sorted(set(value) - set(properties))
                if extra:
                    errors.append(f"{label} contains undeclared field '{extra[0]}'")
            for name, child_value in value.items():
                child = properties.get(name)
                if isinstance(child, Mapping):
                    errors.extend(
                        _validate_definition_value(
                            child_value,
                            child,
                            label=f"{label}.{name}",
                        )
                    )
    elif (
        expected == "array"
        and isinstance(value, Sequence)
        and not isinstance(value, (str, bytes, bytearray))
    ):
        if schema.get("uniqueItems") is True and any(
            item == previous for index, item in enumerate(value) for previous in value[:index]
        ):
            errors.append(f"{label} items must be unique")
        items = schema.get("items")
        if isinstance(items, Mapping):
            for index, item in enumerate(value):
                errors.extend(_validate_definition_value(item, items, label=f"{label}[{index}]"))
    return errors


def _is_discriminated_definition_schema(schema: Mapping[str, Any]) -> bool:
    return "discriminator" in schema or "variants" in schema


def _validate_definition_row_schema(schema: Mapping[str, Any], *, label: str) -> list[str]:
    if not _is_discriminated_definition_schema(schema):
        schema_errors = _validate_definition_schema(schema, label=label)
        if schema.get("type") != "object":
            schema_errors.append(f"{label} must describe object rows")
        if schema.get("additionalProperties") is not False:
            schema_errors.append(f"{label} must set additionalProperties to false")
        return schema_errors

    errors: list[str] = []
    unknown = set(schema) - _DEFINITION_VARIANT_FIELDS
    if unknown:
        errors.append(f"{label} contains unsupported field '{sorted(unknown)[0]}'")
    discriminator = schema.get("discriminator")
    if not isinstance(discriminator, str) or not re.fullmatch(
        r"[A-Za-z_][A-Za-z0-9_]*", discriminator
    ):
        errors.append(f"{label} has an invalid discriminator")
    variants = schema.get("variants")
    if not isinstance(variants, Mapping) or not variants:
        errors.append(f"{label} has invalid variants")
        return errors
    for name, variant_schema in variants.items():
        variant_label = f"{label} variant {name!r}"
        if not isinstance(name, str) or not name or not isinstance(variant_schema, Mapping):
            errors.append(f"{label} has invalid variants")
            continue
        errors.extend(_validate_definition_schema(variant_schema, label=variant_label))
        if variant_schema.get("type") != "object":
            errors.append(f"{variant_label} must describe an object payload")
        if variant_schema.get("additionalProperties") is not False:
            errors.append(f"{variant_label} must set additionalProperties to false")
    return errors


def _validate_definition_row(row: object, schema: Mapping[str, Any], *, label: str) -> list[str]:
    if not _is_discriminated_definition_schema(schema):
        return _validate_definition_value(row, schema, label=label)
    if not isinstance(row, Mapping):
        return [f"{label} has invalid type"]
    discriminator = schema.get("discriminator")
    variants = schema.get("variants")
    if not isinstance(discriminator, str) or not isinstance(variants, Mapping):
        return []
    if discriminator not in row:
        return [f"{label} is missing discriminator '{discriminator}'"]
    selected = row[discriminator]
    if not isinstance(selected, str) or selected not in variants:
        return [f"{label} selects undeclared variant {selected!r}"]
    variant_schema = variants[selected]
    if not isinstance(variant_schema, Mapping):
        return []
    payload = {name: value for name, value in row.items() if name != discriminator}
    return _validate_definition_value(payload, variant_schema, label=label)


def _validate_definition_row_schemas(
    definitions: Mapping[str, Any], schemas: Mapping[str, Any]
) -> list[str]:
    errors: list[str] = []
    for table_path, schema in schemas.items():
        label = f"Definition table '{table_path}' schema"
        if not isinstance(table_path, str) or not table_path.startswith("definitions."):
            errors.append("Definition row schema paths must start with 'definitions.'")
            continue
        if not isinstance(schema, Mapping):
            errors.append(f"{label} must be a mapping")
            continue
        schema_errors = _validate_definition_row_schema(schema, label=label)
        errors.extend(schema_errors)
        table = _definition_table(definitions, table_path)
        if table is None:
            errors.append(f"{label} targets a missing table")
            continue
        if schema_errors:
            continue
        for key, row in table.items():
            errors.extend(
                _validate_definition_row(
                    row,
                    schema,
                    label=f"Definition table '{table_path}' row {key!r}",
                )
            )
    return errors


def _validate_definition_lookups(
    owners: Sequence[tuple[str, Mapping[str, Any], object]],
    definitions: Mapping[str, Any],
    definition_row_schemas: Mapping[str, Any],
) -> list[str]:
    """Fail closed for exact enum-to-definition dispatch through table_lookup."""

    errors: list[str] = []
    allowed_body_keys = {"table", "key", "default", "required", "exact_selector"}
    for owner_label, tool_def, effects in owners:
        parameters = {
            parameter.get("name"): parameter
            for parameter in tool_def.get("parameters", [])
            if isinstance(parameter, Mapping) and isinstance(parameter.get("name"), str)
        }
        for effect in _iter_effects(effects):
            if "table_lookup" not in effect:
                continue
            body = effect["table_lookup"]
            if not isinstance(body, Mapping):
                continue
            location = f"{owner_label}: table_lookup"
            unknown = set(body) - allowed_body_keys
            if unknown:
                errors.append(f"{location} has unsupported fields: {sorted(unknown)}")
            store_as = effect.get("store_as", "_table_result")
            if not isinstance(store_as, str) or not re.fullmatch(r"_[A-Za-z0-9_]+", store_as):
                errors.append(f"{location} store_as must be a private context identifier")
            required = body.get("required", False)
            exact_selector = body.get("exact_selector", False)
            if not isinstance(required, bool):
                errors.append(f"{location} required must be a boolean")
                continue
            if not isinstance(exact_selector, bool):
                errors.append(f"{location} exact_selector must be a boolean")
                continue
            if required and "default" in body:
                errors.append(f"{location} cannot combine required with default")
            if exact_selector and not required:
                errors.append(f"{location} exact_selector requires required: true")

            table_path = body.get("table")
            table = (
                _definition_table(definitions, table_path) if isinstance(table_path, str) else None
            )
            if required and table is None:
                errors.append(f"{location} required table '{table_path}' does not exist")
            if not exact_selector or table is None:
                continue
            if not isinstance(table_path, str):
                continue

            row_schema = definition_row_schemas.get(table_path)
            if not isinstance(row_schema, Mapping):
                errors.append(f"{location} exact selector requires a definition row schema")

            selector_name = body.get("key")
            selector = parameters.get(selector_name)
            if (
                not isinstance(selector_name, str)
                or not isinstance(selector, Mapping)
                or selector.get("type") != "enum"
                or selector.get("required", True) is not True
            ):
                errors.append(f"{location} exact selector must name a required enum parameter")
                continue
            enum_values = selector.get("enum_values")
            if (
                not isinstance(enum_values, list)
                or not enum_values
                or any(not isinstance(value, str) for value in enum_values)
                or len(enum_values) != len(set(enum_values))
            ):
                errors.append(f"{location} selector enum values must be unique strings")
                continue
            if set(enum_values) != set(table):
                errors.append(f"{location} selector enum must exactly match table keys")
            for key, row in table.items():
                if not isinstance(row, Mapping):
                    errors.append(f"{location} row {key!r} must be a mapping")

    return errors


# ── Tool validation ───────────────────────────────────────────────────────


def _validate_tools(tools: dict[str, Any]) -> list[str]:
    """Validate all tool definitions."""
    errors: list[str] = []

    for tool_name, tool_def in tools.items():
        if not isinstance(tool_def, dict):
            errors.append(f"Tool '{tool_name}': must be a mapping")
            continue

        # Name validation
        if not re.match(r"^[a-z_][a-z0-9_]*$", tool_name):
            errors.append(f"Tool '{tool_name}': invalid name (must be snake_case)")

        # Parameters
        params = tool_def.get("parameters", [])
        if isinstance(params, list):
            errors.extend(_validate_tool_params(tool_name, params))
        elif params is not None:
            errors.append(f"Tool '{tool_name}': 'parameters' must be a list")

        # Pre-conditions
        pre_conds = tool_def.get("pre_conditions", [])
        if isinstance(pre_conds, list):
            errors.extend(_validate_pre_conditions(tool_name, pre_conds))

        # Effects
        effects = tool_def.get("effects", [])
        if isinstance(effects, list):
            errors.extend(_validate_effects(tool_name, effects))
        elif effects is not None:
            errors.append(f"Tool '{tool_name}': 'effects' must be a list")

        # Metadata
        metadata = tool_def.get("metadata", {})
        if isinstance(metadata, dict):
            category = metadata.get("category")
            if category and not isinstance(category, str):
                errors.append(f"Tool '{tool_name}': metadata.category must be a string")
            model_behavior = metadata.get("model_behavior")
            behavior_kind = (
                model_behavior.get("kind") if isinstance(model_behavior, dict) else model_behavior
            )
            if behavior_kind not in {
                None,
                "optional",
                "required_engine_transition",
                "required_ruleset_decision",
                "leaves_player_intent_pending",
                "resolves_player_intent",
                "supports_player_intent",
            }:
                errors.append(f"Tool '{tool_name}': metadata.model_behavior is unsupported")
            if isinstance(model_behavior, dict):
                if set(model_behavior) != {"kind", "state_predicates"}:
                    errors.append(f"Tool '{tool_name}': metadata.model_behavior fields are invalid")
                else:
                    behavior_errors = _validate_model_tool_availability(
                        {tool_name: {"state_predicates": model_behavior["state_predicates"]}},
                        {tool_name},
                    )
                    errors.extend(
                        error.replace(
                            f"Model tool '{tool_name}'",
                            f"Tool '{tool_name}' metadata.model_behavior",
                        )
                        for error in behavior_errors
                    )

    return errors


def _validate_model_tool_availability(value: Any, tool_names: set[str]) -> list[str]:
    """Validate the Ruleset-owned projection from runtime tools to model-facing tools."""

    if not isinstance(value, dict):
        return ["'model_tool_availability' section must be a mapping"]
    errors: list[str] = []
    for tool_name, declaration in value.items():
        if tool_name not in tool_names:
            errors.append(f"Model tool availability references unknown runtime tool '{tool_name}'")
            continue
        if not isinstance(declaration, dict) or set(declaration) != {"state_predicates"}:
            errors.append(
                f"Model tool '{tool_name}' availability must contain only state_predicates"
            )
            continue
        predicates = declaration["state_predicates"]
        if not isinstance(predicates, list):
            errors.append(f"Model tool '{tool_name}' state_predicates must be a list")
            continue
        for index, predicate in enumerate(predicates):
            if not isinstance(predicate, dict) or set(predicate) not in (
                {"namespace", "key", "equals"},
                {"namespace", "key", "equals", "negated"},
            ):
                errors.append(
                    f"Model tool '{tool_name}' state predicate {index} has invalid fields"
                )
                continue
            if not isinstance(predicate["namespace"], str) or not predicate["namespace"].strip():
                errors.append(
                    f"Model tool '{tool_name}' state predicate {index} requires a namespace"
                )
            if not isinstance(predicate["key"], str) or not predicate["key"].strip():
                errors.append(f"Model tool '{tool_name}' state predicate {index} requires a key")
            if "negated" in predicate and not isinstance(predicate["negated"], bool):
                errors.append(
                    f"Model tool '{tool_name}' state predicate {index} negated must be boolean"
                )
    return errors


def _validate_campaign_preparation(value: Any) -> list[str]:
    """Admit only a bounded data declaration of the normal character preparation intent."""
    if value is None:
        return []
    fields = {"queue_field", "intent", "actor_parameter", "prompt"}
    if (not isinstance(value, dict) or set(value) != fields
            or any(not isinstance(value[name], str) or not 1 <= len(value[name]) <= 2000
                   for name in fields)):
        return ["Model decision preparation fields are invalid"]
    if any(not value[name].replace("_", "").isalnum()
           for name in ("queue_field", "intent", "actor_parameter")):
        return ["Model decision preparation identifiers are invalid"]
    return []


def _validate_model_decision_boundary(value: Any) -> list[str]:
    """Validate the package-owned mapping from active actor to decision owner."""

    if value is None:
        return []
    required_fields = {"active_actor", "actor_entities", "participant_control", "default_owner"}
    if (not isinstance(value, dict) or not required_fields <= set(value)
            or set(value) - required_fields - {"preparation", "isolated_participant"}):
        return ["'model_decision_boundary' fields are invalid"]
    errors: list[str] = []
    for field in ("active_actor", "actor_entities"):
        reference = value[field]
        if not isinstance(reference, dict) or set(reference) != {"namespace", "key"}:
            errors.append(f"Model decision boundary {field} must be a state reference")
            continue
        if not all(
            isinstance(reference.get(name), str) and reference[name].strip()
            for name in ("namespace", "key")
        ):
            errors.append(f"Model decision boundary {field} reference is invalid")
    control = value["participant_control"]
    if (not isinstance(control, dict) or not {"field", "player_values"} <= set(control)
            or set(control) - {"field", "player_values", "actor_participant_field"}):
        errors.append("Model decision boundary participant_control fields are invalid")
    else:
        if "actor_participant_field" in control and (
            not isinstance(control["actor_participant_field"], str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", control["actor_participant_field"]) is None
        ):
            errors.append("Model decision boundary actor participant field is invalid")
        player_values = control["player_values"]
        if not isinstance(control["field"], str) or not control["field"].strip():
            errors.append("Model decision boundary participant control field is invalid")
        if (
            not isinstance(player_values, list)
            or not player_values
            or any(not isinstance(item, str) or not item.strip() for item in player_values)
            or len(player_values) != len(set(player_values))
        ):
            errors.append("Model decision boundary player values are invalid")
    if value["default_owner"] not in {"player", "gm"}:
        errors.append("Model decision boundary default owner is invalid")
    errors.extend(_validate_campaign_preparation(value.get("preparation")))
    isolated = value.get("isolated_participant")
    if "isolated_participant" in value:
        fields = {"instructions", "observation_path", "actions_path", "public_observation"}
        if (not isinstance(isolated, dict) or not fields <= set(isolated)
                or set(isolated) - fields - {"persona_path"}):
            errors.append("Isolated participant fields are invalid")
        else:
            if (not isinstance(isolated["instructions"], str)
                    or not isolated["instructions"].strip()
                    or len(isolated["instructions"].encode("utf-8")) > 8192):
                errors.append("Isolated participant instructions are invalid")
            for key in ("observation_path", "actions_path", "persona_path"):
                if key not in isolated:
                    continue
                path = isolated[key]
                if (not isinstance(path, list) or len(path) > 8
                        or any(not isinstance(part, str)
                               or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", part) is None
                               for part in path)):
                    errors.append("Isolated participant path is invalid")
            reference = isolated["public_observation"]
            if (not isinstance(reference, dict) or set(reference) != {"namespace", "key"}
                    or any(not isinstance(reference.get(key), str) or not reference[key].strip()
                           for key in ("namespace", "key"))):
                errors.append("Isolated participant public observation is invalid")
    return errors


def _validate_model_decision_scope(value: Any) -> list[str]:
    """Validate the multi-actor side/group decision-scope declaration."""

    if value is None:
        return []
    fields = {
        "decision_owner",
        "active_side",
        "eligible_actors",
        "focused_actor",
        "actor_entities",
    }
    if (not isinstance(value, dict) or not fields <= set(value)
            or set(value) - fields - {"preparation"}):
        return ["'model_decision_scope' fields are invalid"]
    errors: list[str] = []
    for field in sorted(fields):
        reference = value[field]
        if not isinstance(reference, dict) or set(reference) != {"namespace", "key"}:
            errors.append(f"Model decision scope {field} must be a state reference")
            continue
        if not all(
            isinstance(reference.get(name), str) and reference[name].strip()
            for name in ("namespace", "key")
        ):
            errors.append(f"Model decision scope {field} reference is invalid")
    errors.extend(_validate_campaign_preparation(value.get("preparation")))
    return errors


def validate_parameter_declarations(data: Mapping[str, Any]) -> list[str]:
    """Apply the existing parameter semantics to every pooled descriptor, including unused ones."""
    pool = data.get("parameter_declarations", {})
    if not isinstance(pool, dict):
        return ["parameter_declarations must be a mapping"]
    errors = []
    for name, descriptor in pool.items():
        try:
            errors.extend(_validate_tool_params(f"parameter_declarations.{name}", [descriptor]))
        except (TypeError, ValueError):
            errors.append(f"Parameter declaration '{name}' is invalid")
    return errors


def _validate_tool_params(tool_name: str, params: list[Any]) -> list[str]:
    """Validate tool parameter definitions."""
    errors: list[str] = []

    for i, param in enumerate(params):
        if not isinstance(param, dict):
            errors.append(f"Tool '{tool_name}': parameter {i} must be a mapping")
            continue

        name = param.get("name")
        if not name:
            errors.append(f"Tool '{tool_name}': parameter {i} missing 'name'")
        elif not isinstance(name, str):
            errors.append(f"Tool '{tool_name}': parameter {i} name must be a string")

        ptype = param.get("type", "string")
        if not ptype:
            errors.append(f"Tool '{tool_name}': parameter '{name}' missing 'type'")
        elif ptype not in VALID_PARAM_TYPES:
            errors.append(
                f"Tool '{tool_name}': parameter '{name}' has invalid type "
                f"'{ptype}'. Valid: {', '.join(sorted(VALID_PARAM_TYPES))}"
            )

        if ptype == "enum" and "enum_values" not in param:
            errors.append(
                f"Tool '{tool_name}': parameter '{name}' is enum type but missing 'enum_values'"
            )
        if ptype == "array":
            items = param.get("items")
            if not isinstance(items, Mapping):
                errors.append(
                    f"Tool '{tool_name}': array parameter '{name}' requires an items schema"
                )
            else:
                errors.extend(
                    _validate_definition_schema(
                        items,
                        label=f"Tool '{tool_name}' parameter '{name}' items",
                    )
                )
        elif "items" in param:
            errors.append(f"Tool '{tool_name}': parameter '{name}' items are only valid for array")

        from_state = param.get("from_state")
        if ptype == "object" and from_state is None:
            errors.append(f"Tool '{tool_name}': object parameter '{name}' requires from_state")
        if from_state is not None and (
            not isinstance(from_state, str)
            or not from_state.strip()
            or not from_state.startswith(("state.", "combat."))
        ):
            errors.append(
                f"Tool '{tool_name}': parameter '{name}' from_state must name a "
                "package state.* or combat.* field"
            )
        if from_state is not None and "default" in param:
            errors.append(
                f"Tool '{tool_name}': parameter '{name}' cannot define both from_state and default"
            )

        list_constraint_names = ("min_items", "max_items", "unique_items")
        if ptype not in {"array", "entity_id_list"} and any(
            field in param for field in list_constraint_names
        ):
            errors.append(
                f"Tool '{tool_name}': list constraints on parameter '{name}' "
                "are only valid for entity_id_list or array parameters"
            )
            continue
        if ptype in {"array", "entity_id_list"}:
            if ptype == "entity_id_list" and param.get("resolve") != "entity_list":
                errors.append(
                    f"Tool '{tool_name}': parameter '{name}' entity_id_list requires "
                    "resolve: entity_list"
                )
            for field in ("min_items", "max_items"):
                if field not in param:
                    continue
                bound = param[field]
                if not isinstance(bound, int) or isinstance(bound, bool) or bound < 0:
                    errors.append(
                        f"Tool '{tool_name}': parameter '{name}' {field} must be an integer "
                        "at least zero"
                    )
            unique_items = param.get("unique_items")
            if "unique_items" in param and not isinstance(unique_items, bool):
                errors.append(
                    f"Tool '{tool_name}': parameter '{name}' unique_items must be a boolean"
                )
            min_items = param.get("min_items")
            max_items = param.get("max_items")
            if (
                isinstance(min_items, int)
                and not isinstance(min_items, bool)
                and isinstance(max_items, int)
                and not isinstance(max_items, bool)
                and min_items > max_items
            ):
                errors.append(
                    f"Tool '{tool_name}': parameter '{name}' min_items cannot exceed max_items"
                )

    return errors


def _validate_pre_conditions(tool_name: str, pre_conds: list[Any]) -> list[str]:
    """Validate pre-condition entries."""
    errors: list[str] = []

    for i, pc in enumerate(pre_conds):
        if not isinstance(pc, dict):
            errors.append(f"Tool '{tool_name}': pre_condition {i} must be a mapping")
            continue

        condition = pc.get("condition")
        if not condition:
            errors.append(f"Tool '{tool_name}': pre_condition {i} missing 'condition'")
        elif isinstance(condition, str):
            try:
                parse_expression(condition)
            except ExpressionError as e:
                errors.append(
                    f"Tool '{tool_name}': pre_condition {i} invalid expression '{condition}': {e}"
                )

    return errors


def _validate_effects(tool_name: str, effects: list[Any]) -> list[str]:
    """Validate effect declarations (recursive)."""
    errors: list[str] = []

    for i, effect in enumerate(effects):
        if not isinstance(effect, dict):
            errors.append(f"Tool '{tool_name}': effect {i} must be a mapping")
            continue

        errors.extend(_validate_single_effect(tool_name, effect, f"effect {i}"))

    return errors


def _validate_single_effect(tool_name: str, effect: dict[str, Any], path: str) -> list[str]:
    """Validate a single effect declaration."""
    errors: list[str] = []

    condition = effect.get("condition")
    handler_keys = [key for key in effect if key in VALID_EFFECT_KEYS]
    flat_conditional = (
        isinstance(condition, str)
        and ("on_true" in effect or "on_false" in effect)
        and not [key for key in handler_keys if key != "condition"]
    )
    if flat_conditional:
        handler_keys = ["condition"]
        body: Any = {
            "condition": condition,
            "on_true": effect.get("on_true", []),
            "on_false": effect.get("on_false", []),
        }
    else:
        if isinstance(condition, str):
            handler_keys = [key for key in handler_keys if key != "condition"]
        body = effect[handler_keys[0]] if len(handler_keys) == 1 else None

    if len(handler_keys) != 1:
        errors.append(
            f"Tool '{tool_name}': {path} must contain exactly one effect "
            f"handler; found {sorted(handler_keys)}"
        )
        return errors

    if isinstance(condition, str):
        try:
            parse_expression(condition)
        except ExpressionError as error:
            errors.append(f"Tool '{tool_name}': {path} condition invalid expression: {error}")

    if handler_keys == ["call"]:
        exports = effect.get("export_context")
        if exports is not None and exports != "*":
            if not isinstance(exports, dict):
                errors.append(
                    f"Tool '{tool_name}': {path} context exports must be an object or '*'"
                )
            elif any(
                not isinstance(destination, str)
                or not destination.startswith("_")
                or not isinstance(source, str)
                or not source.startswith("_")
                for destination, source in exports.items()
            ):
                errors.append(
                    f"Tool '{tool_name}': {path} context exports require private variable names"
                )

    for branch in ("on_true", "on_false", "do", "consequence"):
        nested = effect.get(branch, [])
        if isinstance(nested, list):
            errors.extend(_validate_effects(tool_name, nested))

    if handler_keys == ["shot_effect"] and not isinstance(body, dict):
        errors.append(f"Tool '{tool_name}': {path} shot_effect must be an object")
    if isinstance(body, dict):
        if handler_keys == ["request_choice"]:
            errors.extend(_validate_request_choice_effect(tool_name, effect, body, path))
        elif handler_keys == ["spatial_query"]:
            errors.extend(_validate_spatial_query_effect(tool_name, effect, body, path))
        elif handler_keys == ["spatial_mutation"]:
            errors.extend(_validate_spatial_mutation_effect(tool_name, effect, body, path))
        elif handler_keys == ["shot_effect"]:
            required = {
                "scene",
                "snapshot",
                "source_id",
                "target_id",
                "origin",
                "destination",
                "delay_ms",
                "miss",
                "contacts",
            }
            if (
                set(effect) - {"shot_effect", "condition"}
                or not required.issubset(body)
                or set(body) - required - {"style", "target_kind"}
                or not isinstance(body.get("scene"), str)
                or not body["scene"].startswith("state.")
            ):
                errors.append(f"Tool '{tool_name}': {path} invalid shot_effect declaration")
        for branch in ("on_true", "on_false", "do", "consequence"):
            nested = body.get(branch, [])
            if isinstance(nested, list) and nested is not effect.get(branch):
                errors.extend(_validate_effects(tool_name, nested))

    return errors


def _spatial_literal_or_template(
    value: object,
    expected: type[object] | tuple[type[object], ...],
) -> bool:
    return isinstance(value, expected) or (
        isinstance(value, str) and _SPATIAL_TEMPLATE.fullmatch(value) is not None
    )


def _spatial_positive_number_or_template(value: object) -> bool:
    if isinstance(value, str):
        return _SPATIAL_TEMPLATE.fullmatch(value) is not None
    return (
        not isinstance(value, bool)
        and isinstance(value, (int, float))
        and math.isfinite(value)
        and value > 0
    )


def _spatial_coordinate(value: object, dimensions: int, *, integer: bool = False) -> bool:
    if isinstance(value, str):
        return _SPATIAL_TEMPLATE.fullmatch(value) is not None
    if not isinstance(value, list) or len(value) != dimensions:
        return False
    for item in value:
        if isinstance(item, str) and _SPATIAL_TEMPLATE.fullmatch(item) is not None:
            continue
        if isinstance(item, bool) or not isinstance(item, int if integer else (int, float)):
            return False
        if isinstance(item, float) and not math.isfinite(item):
            return False
    return True


def _validate_spatial_mutation_effect(
    tool_name: str,
    effect: Mapping[str, Any],
    body: Mapping[str, Any],
    path: str,
) -> list[str]:
    """Validate the closed static shape of one neutral scene-object patch."""

    label = f"Tool '{tool_name}': {path} spatial_mutation"
    errors: list[str] = []
    if set(body) != {"scene", "map_id", "expected_revision", "target", "patch"}:
        errors.append(f"{label} uses an invalid closed body")
    if set(effect) - {"spatial_mutation", "store_as", "condition"}:
        errors.append(f"{label} effect contains unsupported fields")
    scene = body.get("scene")
    if (
        not isinstance(scene, str)
        or re.fullmatch(r"state\.[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", scene)
        is None
    ):
        errors.append(f"{label} scene must be a package-owned state path")
    if not isinstance(body.get("map_id"), str) or not body.get("map_id"):
        errors.append(f"{label} map_id must be text or a context template")
    if not _spatial_literal_or_template(body.get("expected_revision"), int) or isinstance(
        body.get("expected_revision"), bool
    ):
        errors.append(f"{label} expected_revision must be an integer or context template")
    store_as = effect.get("store_as")
    if not isinstance(store_as, str) or re.fullmatch(r"_[A-Za-z0-9_]+", store_as) is None:
        errors.append(f"{label} requires a private store_as")
    target = body.get("target")
    if not isinstance(target, Mapping) or set(target) != {"kind", "id"}:
        errors.append(f"{label} target is invalid")
        return errors
    target_kind = target.get("kind")
    if target_kind not in _SPATIAL_MUTATION_FIELDS:
        errors.append(f"{label} target kind is invalid")
        return errors
    if not isinstance(target.get("id"), str) or not target.get("id"):
        errors.append(f"{label} target id must be text or a context template")
    patch = body.get("patch")
    if (
        not isinstance(patch, Mapping)
        or not patch
        or set(patch) - _SPATIAL_MUTATION_FIELDS[cast(str, target_kind)]
    ):
        errors.append(f"{label} patch uses unsupported fields")
    return errors


def _validate_spatial_query_effect(
    tool_name: str,
    effect: Mapping[str, Any],
    body: Mapping[str, Any],
    path: str,
) -> list[str]:
    """Validate the complete static portion of one neutral spatial query."""

    label = f"Tool '{tool_name}': {path} spatial_query"
    errors: list[str] = []
    operation = body.get("operation")
    if operation == "snapshot":
        expected_body = {"scene", "operation"}
    elif isinstance(operation, str) and operation in _SPATIAL_QUERY_FIELDS:
        expected_body = {"scene", "operation", "query"}
        if "snapshot" in body:
            expected_body.add("snapshot")
    else:
        errors.append(f"{label} operation is invalid")
        return errors
    if set(body) != expected_body:
        errors.append(f"{label} uses an invalid closed body")
    if set(effect) - {"spatial_query", "store_as", "condition"}:
        errors.append(f"{label} effect contains unsupported fields")
    scene = body.get("scene")
    if (
        not isinstance(scene, str)
        or re.fullmatch(r"state\.[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*", scene)
        is None
    ):
        errors.append(f"{label} scene must be a package-owned state path")
    store_as = effect.get("store_as")
    if not isinstance(store_as, str) or re.fullmatch(r"_[A-Za-z0-9_]+", store_as) is None:
        errors.append(f"{label} requires a private store_as")
    if operation == "snapshot":
        return errors
    snapshot = body.get("snapshot")
    if snapshot is not None and (
        not isinstance(snapshot, str) or _SPATIAL_TEMPLATE.fullmatch(snapshot) is None
    ):
        errors.append(f"{label} snapshot must reference private execution context")
    query = body.get("query")
    if not isinstance(query, Mapping):
        errors.append(f"{label} query must be an object")
        return errors
    expected_query = _SPATIAL_QUERY_FIELDS[cast(str, operation)]
    if operation == "path" and "moving_entity_id" not in query:
        expected_query = expected_query - {"moving_entity_id"}
    if operation in {"path", "exposure"} and "metadata_filter" not in query:
        expected_query = expected_query - {"metadata_filter"}
    if operation == "sweep":
        expected_query = expected_query - ({"metadata_filter", "exclude_entity_ids"} - set(query))
    if operation == "placement":
        expected_query = expected_query - ({"containing_zone_id", "reference_face"} - set(query))
    if set(query) != expected_query:
        errors.append(f"{label} {operation} query uses invalid fields")
        return errors
    map_id = query.get("map_id")
    if not isinstance(map_id, str) or not map_id:
        errors.append(f"{label} map_id must be text or a context template")
    if operation == "ray":
        for name in ("origin", "destination"):
            value = query.get(name)
            if not _spatial_coordinate(value, 3):
                errors.append(f"{label} {name} must be a three-coordinate value")
        _validate_spatial_include(errors, label, query.get("include"), {"geometry", "entities"})
        _validate_spatial_limit(
            errors, label, query.get("max_intersections"), "max_intersections", 1, 1024
        )
    elif operation == "exposure":
        observer = query.get("observer")
        if not isinstance(observer, Mapping) or set(observer) != {"point"}:
            errors.append(f"{label} observer is invalid")
        elif not _spatial_coordinate(observer.get("point"), 3):
            errors.append(f"{label} observer point must contain three coordinates")
        target = query.get("target")
        if (
            not isinstance(target, Mapping)
            or set(target) != {"entity_id"}
            or not isinstance(target.get("entity_id"), str)
        ):
            errors.append(f"{label} target is invalid")
        samples = query.get("samples")
        if not isinstance(samples, Mapping) or set(samples) != {"horizontal", "vertical"}:
            errors.append(f"{label} samples are invalid")
        else:
            _validate_spatial_limit(
                errors, label, samples.get("horizontal"), "horizontal samples", 1, 9
            )
            _validate_spatial_limit(
                errors, label, samples.get("vertical"), "vertical samples", 1, 9
            )
        _validate_spatial_include(errors, label, query.get("include"), {"geometry", "entities"})
        selector = query.get("metadata_filter")
        if selector is not None and (
            not isinstance(selector, Mapping)
            or set(selector) != {"key", "value", "missing_matches"}
            or not isinstance(selector.get("key"), str)
            or not isinstance(selector.get("value"), str)
            or not _spatial_literal_or_template(selector.get("missing_matches"), bool)
        ):
            errors.append(f"{label} metadata filter is invalid")
    elif operation == "body_proximity":
        if not _spatial_coordinate(query.get("destination"), 3):
            errors.append(f"{label} destination must contain three coordinates")
        for name in ("moving_entity_id", "fixed_entity_id"):
            if not isinstance(query.get(name), str) or not query.get(name):
                errors.append(f"{label} {name} must be text or a template")
        radius = query.get("radius")
        if not (isinstance(radius, str) and _SPATIAL_TEMPLATE.fullmatch(radius)) and not (
            isinstance(radius, (int, float))
            and not isinstance(radius, bool)
            and math.isfinite(radius)
            and radius >= 0
        ):
            errors.append(f"{label} radius must be a finite nonnegative number or a template")
    elif operation == "sweep":
        if not _spatial_coordinate(query.get("destination"), 3):
            errors.append(f"{label} destination must contain three coordinates")
        if not isinstance(query.get("moving_entity_id"), str) or not query.get("moving_entity_id"):
            errors.append(f"{label} moving_entity_id must be text or a template")
        if not _spatial_literal_or_template(query.get("include_entities"), bool):
            errors.append(f"{label} include_entities must be boolean")
        excluded = query.get("exclude_entity_ids", [])
        if not (isinstance(excluded, str) and _SPATIAL_TEMPLATE.fullmatch(excluded)) and not (
            isinstance(excluded, list)
            and len(excluded) <= 64
            and all(isinstance(value, str) for value in excluded)
        ):
            errors.append(f"{label} excluded entities are invalid")
        selector = query.get("metadata_filter")
        if selector is not None and (
            not isinstance(selector, Mapping)
            or set(selector) != {"key", "value", "missing_matches"}
            or not isinstance(selector.get("key"), str)
            or not isinstance(selector.get("value"), str)
            or not _spatial_literal_or_template(selector.get("missing_matches"), bool)
        ):
            errors.append(f"{label} metadata filter is invalid")
    elif operation == "placement":
        if "reference_face" in query:
            reference = query["reference_face"]
            if not isinstance(reference, Mapping) or set(reference) != {"target", "face"}:
                errors.append(f"{label} reference_face is invalid")
            else:
                target = reference["target"]
                if (
                    not isinstance(target, Mapping)
                    or set(target) != {"kind", "id"}
                    or not isinstance(target.get("kind"), str)
                    or target.get("kind") not in {"geometry", "entity"}
                    or not isinstance(target.get("id"), str)
                    or not target.get("id")
                ):
                    errors.append(f"{label} reference_face target is invalid")
                face = reference["face"]
                if not isinstance(face, str) or (
                    face not in {"east", "west", "north", "south", "up", "down"}
                    and _SPATIAL_TEMPLATE.fullmatch(face) is None
                ):
                    errors.append(f"{label} reference_face face is invalid")
        zone_id = query.get("containing_zone_id")
        if "containing_zone_id" in query and (not isinstance(zone_id, str) or not zone_id):
            errors.append(f"{label} containing_zone_id must be text or a template")
        if not _spatial_coordinate(query.get("origin"), 3):
            errors.append(f"{label} origin must contain three coordinates")
        if not isinstance(query.get("moving_entity_id"), str):
            errors.append(f"{label} moving_entity_id must be text or a template")
        excluded = query.get("exclude_entity_ids")
        if not (isinstance(excluded, str) and _SPATIAL_TEMPLATE.fullmatch(excluded)) and not (
            isinstance(excluded, list)
            and len(excluded) <= 64
            and all(isinstance(value, str) for value in excluded)
        ):
            errors.append(f"{label} excluded entities are invalid")
        selector = query.get("metadata_filter")
        if (
            not isinstance(selector, Mapping)
            or set(selector) != {"key", "value", "missing_matches"}
            or not isinstance(selector.get("key"), str)
            or not isinstance(selector.get("value"), str)
            or not _spatial_literal_or_template(selector.get("missing_matches"), bool)
        ):
            errors.append(f"{label} metadata filter is invalid")
        _validate_spatial_limit(
            errors, label, query.get("max_candidates"), "max_candidates", 1, 100000
        )
    elif operation == "path":
        selector = query.get("metadata_filter")
        if selector is not None and (
            not isinstance(selector, Mapping)
            or set(selector) != {"key", "value", "missing_matches"}
            or not isinstance(selector.get("key"), str)
            or not isinstance(selector.get("value"), str)
            or not _spatial_literal_or_template(selector.get("missing_matches"), bool)
        ):
            errors.append(f"{label} metadata filter is invalid")
        for name in ("start", "goal"):
            value = query.get(name)
            if not _spatial_coordinate(value, 2, integer=True):
                errors.append(f"{label} {name} must be a two-cell value")
        if not _spatial_literal_or_template(query.get("allow_diagonal"), bool):
            errors.append(f"{label} allow_diagonal must be boolean")
        corner_policy = query.get("corner_policy")
        if corner_policy not in {"allow_cutting", "forbid_cutting"} and not (
            isinstance(corner_policy, str)
            and _SPATIAL_TEMPLATE.fullmatch(corner_policy) is not None
        ):
            errors.append(f"{label} corner_policy is invalid")
        if not _spatial_literal_or_template(query.get("include_entities"), bool):
            errors.append(f"{label} include_entities must be boolean")
        moving_entity_id = query.get("moving_entity_id")
        if moving_entity_id is not None and (
            not isinstance(moving_entity_id, str) or not moving_entity_id
        ):
            errors.append(f"{label} moving_entity_id must be text or a context template")
        _validate_spatial_limit(errors, label, query.get("max_steps"), "max_steps", 1, 4_000_000)
    elif operation == "crossed_zones":
        value = query.get("path")
        if isinstance(value, str):
            path_valid = _SPATIAL_TEMPLATE.fullmatch(value) is not None
        else:
            path_valid = (
                isinstance(value, list)
                and 2 <= len(value) <= 256
                and all(_spatial_coordinate(point, 3) for point in value)
            )
        if not path_valid:
            errors.append(f"{label} path must contain 2 to 256 points")
        _validate_spatial_limit(errors, label, query.get("max_crossings"), "max_crossings", 1, 4096)
    else:
        shape = query.get("shape")
        fields = {
            "box": {"kind", "min", "max"},
            "cylinder": {"kind", "center", "radius", "height"},
            "sphere": {"kind", "center", "radius"},
            "cone": {"kind", "apex", "direction", "length", "radius"},
            "prism": {"kind", "origin", "direction", "up", "length", "width", "height"},
        }
        if (
            not isinstance(shape, Mapping)
            or not isinstance(shape.get("kind"), str)
            or shape.get("kind") not in fields
        ):
            errors.append(f"{label} area shape is invalid")
        elif set(shape) != fields[shape["kind"]]:
            errors.append(f"{label} area shape uses invalid fields")
        elif shape["kind"] == "box" and (
            not _spatial_coordinate(shape.get("min"), 3)
            or not _spatial_coordinate(shape.get("max"), 3)
        ):
            errors.append(f"{label} area box coordinates are invalid")
        elif shape["kind"] == "cylinder" and (
            not _spatial_coordinate(shape.get("center"), 3)
            or not _spatial_positive_number_or_template(shape.get("radius"))
            or not _spatial_positive_number_or_template(shape.get("height"))
        ):
            errors.append(f"{label} area cylinder values are invalid")
        elif shape["kind"] == "sphere" and (
            not _spatial_coordinate(shape.get("center"), 3)
            or not _spatial_positive_number_or_template(shape.get("radius"))
        ):
            errors.append(f"{label} area sphere values are invalid")
        elif shape["kind"] == "cone" and (
            not _spatial_coordinate(shape.get("apex"), 3)
            or not _spatial_coordinate(shape.get("direction"), 3)
            or not _spatial_positive_number_or_template(shape.get("length"))
            or not _spatial_positive_number_or_template(shape.get("radius"))
        ):
            errors.append(f"{label} area cone values are invalid")
        elif shape["kind"] == "prism" and (
            not all(
                _spatial_coordinate(shape.get(field), 3) for field in ("origin", "direction", "up")
            )
            or not all(
                _spatial_positive_number_or_template(shape.get(field))
                for field in ("length", "width", "height")
            )
        ):
            errors.append(f"{label} area prism values are invalid")
        _validate_spatial_include(
            errors, label, query.get("include"), {"geometry", "entities", "zones"}
        )
        _validate_spatial_limit(errors, label, query.get("max_results"), "max_results", 1, 4096)
    return errors


def _validate_spatial_include(
    errors: list[str],
    label: str,
    value: object,
    allowed: set[str],
) -> None:
    if isinstance(value, str) and _SPATIAL_TEMPLATE.fullmatch(value) is not None:
        return
    if (
        not isinstance(value, list)
        or not value
        or any(not isinstance(item, str) or item not in allowed for item in value)
        or len(set(cast(list[str], value))) != len(value)
    ):
        errors.append(f"{label} include list is invalid")


def _validate_spatial_limit(
    errors: list[str],
    label: str,
    value: object,
    field: str,
    minimum: int,
    maximum: int,
) -> None:
    if isinstance(value, str) and _SPATIAL_TEMPLATE.fullmatch(value) is not None:
        return
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        errors.append(f"{label} {field} is outside bounds")


def _validate_request_choice_effect(
    tool_name: str,
    effect: Mapping[str, Any],
    body: Mapping[str, Any],
    path: str,
) -> list[str]:
    """Fail closed on the complete static portion of a choice checkpoint."""

    errors: list[str] = []
    unknown = set(body) - {"choice_id", "prompt", "options"}
    if unknown:
        errors.append(
            f"Tool '{tool_name}': {path} request_choice contains unknown field "
            f"'{sorted(unknown)[0]}'"
        )
    choice_id = body.get("choice_id")
    prompt = body.get("prompt")
    options = body.get("options")
    store_as = effect.get("store_as")
    if (
        not isinstance(choice_id, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", choice_id) is None
    ):
        errors.append(f"Tool '{tool_name}': {path} choice_id is invalid")
    if not isinstance(prompt, str) or not prompt or len(prompt.encode("utf-8")) > 4096:
        errors.append(f"Tool '{tool_name}': {path} choice prompt is invalid")
    if not isinstance(store_as, str) or not store_as.startswith("_"):
        errors.append(f"Tool '{tool_name}': {path} request_choice requires private store_as")
    # Computed private collections receive the same bounded validation at execution.
    if isinstance(options, str) and re.fullmatch(r"_[A-Za-z0-9_]+", options):
        return errors
    if not isinstance(options, list) or not 2 <= len(options) <= 16:
        errors.append(f"Tool '{tool_name}': {path} choice options must contain 2 to 16 items")
        return errors
    option_ids: set[str] = set()
    for index, option in enumerate(options):
        if not isinstance(option, Mapping) or set(option) != {"id", "label"}:
            errors.append(f"Tool '{tool_name}': {path} option {index} is invalid")
            continue
        option_id = option.get("id")
        label = option.get("label")
        if (
            not isinstance(option_id, str)
            or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", option_id) is None
            or option_id in option_ids
        ):
            errors.append(f"Tool '{tool_name}': {path} option {index} identity is invalid")
        else:
            option_ids.add(option_id)
        if not isinstance(label, str) or not label or len(label.encode("utf-8")) > 1024:
            errors.append(f"Tool '{tool_name}': {path} option {index} label is invalid")
    return errors


# ── Procedure validation ──────────────────────────────────────────────────


def _validate_procedures(procedures: dict[str, Any], all_tool_names: set[str]) -> list[str]:
    """Validate procedure definitions."""
    errors: list[str] = []

    for proc_name, proc_def in procedures.items():
        if not isinstance(proc_def, dict):
            errors.append(f"Procedure '{proc_name}': must be a mapping")
            continue

        # Trigger validation
        trigger = proc_def.get("trigger")
        if not trigger:
            errors.append(f"Procedure '{proc_name}': missing 'trigger'")
        elif not isinstance(trigger, str) or VALID_TRIGGER_PATTERN.fullmatch(trigger) is None:
            near = _suggest(str(trigger), VALID_TRIGGERS)
            hint = f" Did you mean '{near}'?" if near else ""
            errors.append(
                f"Procedure '{proc_name}': unknown trigger "
                f"'{trigger}'.{hint} Triggers must use neutral on_* snake_case identity."
            )

        # Procedure-level condition
        condition = proc_def.get("condition")
        if isinstance(condition, str):
            try:
                parse_expression(condition)
            except ExpressionError as e:
                errors.append(
                    f"Procedure '{proc_name}': invalid condition expression '{condition}': {e}"
                )

        # Cross-reference: called tools must exist
        steps = proc_def.get("steps", [])
        if isinstance(steps, list):
            for index, step in enumerate(steps):
                if isinstance(step, dict):
                    contains_spatial_effect = any(
                        "spatial_query" in effect
                        or "spatial_mutation" in effect
                        or "shot_effect" in effect
                        for effect in _iter_effects([step])
                    )
                    if contains_spatial_effect:
                        errors.extend(
                            _validate_single_effect(
                                f"procedure {proc_name}",
                                step,
                                f"step {index}",
                            )
                        )
                    call_name = step.get("call")
                    if call_name and isinstance(call_name, str):  # noqa: SIM102
                        if call_name not in all_tool_names:
                            near = _suggest(call_name, all_tool_names)
                            hint = f" Did you mean '{near}'?" if near else ""
                            errors.append(
                                f"Procedure '{proc_name}': calls undefined "
                                f"tool '{call_name}'.{hint}"
                            )

    return errors


# ── Utility ───────────────────────────────────────────────────────────────


def _suggest(target: str, options: Set[str]) -> str | None:
    """Simple suggestion — find the closest match by edit distance."""
    if not options:
        return None
    best = min(options, key=lambda o: _levenshtein(target, o))
    if _levenshtein(target, best) <= 3:
        return best
    return None


def _levenshtein(a: str, b: str) -> int:
    """Compute Levenshtein edit distance."""
    if len(a) < len(b):
        return _levenshtein(b, a)
    if len(b) == 0:
        return len(a)
    prev_row = list(range(len(b) + 1))
    for i, ca in enumerate(a):
        curr_row = [i + 1]
        for j, cb in enumerate(b):
            insert = prev_row[j + 1] + 1
            delete = curr_row[j] + 1
            sub = prev_row[j] + (0 if ca == cb else 1)
            curr_row.append(min(insert, delete, sub))
        prev_row = curr_row
    return prev_row[-1]
