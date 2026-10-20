"""Compile compact Builder-authored Ruleset source into one normalized package draft."""

from __future__ import annotations

import hashlib
import math
from collections.abc import Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, replace
from typing import Any, cast

import yaml

from sylvia_gm.application._yaml_bounds import validate_yaml_graph
from sylvia_gm.application.package_materialization import NormalizedPackageDraft
from sylvia_gm.domain._content_schema_ruleset_tests import RULESET_TESTS_PATH
from sylvia_gm.domain.content_validation import ContentShapeError, validate_content_shape
from sylvia_gm.domain.errors import CommandRejected, RulesetDiagnostic
from sylvia_gm.domain.package_library import (
    ENGINE_FEATURE_PANEL_WORKSPACES,
    ENGINE_FEATURE_SHOT_EFFECTS,
    ENGINE_FEATURE_SPATIAL_CLEARANCE,
    ENGINE_FEATURE_SPATIAL_MUTATION,
    ENGINE_FEATURE_SPATIAL_PATH_MOVING_ENTITY,
    ENGINE_FEATURE_TACTICAL_GRID,
    canonical_json_bytes,
)
from sylvia_gm.domain.panel_layout import PANEL_LAYOUT_PATH
from sylvia_gm.domain.ruleset_tests import validate_ruleset_tests
from sylvia_gm.domain.starting_state import PRIVATE_STATE_FEATURE, STARTING_STATE_FEATURE

COMPACT_RULESET_SOURCE = "ruleset-source.yaml"
_SOURCE_DEPTH_LIMIT = 64
_SOURCE_NODE_LIMIT = 250_000
_SOURCE_KEYS = frozenset(
    {
        "format",
        "id",
        "version",
        "label",
        "publisher",
        "mechanic_families",
        "public_actions",
        "private_mechanics",
        "definitions",
        "participant_artifacts",
        "operating_guide",
    }
)
_RESOLUTIONS = frozenset(
    {"engine_family", "engine_specialized", "hybrid", "gm_adjudicated", "unsupported"}
)


@dataclass(frozen=True)
class CompiledRulesetSource:
    """Generated package draft plus the author-facing compatibility facts it selected."""

    draft: NormalizedPackageDraft
    required_engine_features: tuple[str, ...]


class _SourceLoader(yaml.SafeLoader):
    """Keep YAML mappings unambiguous before family expansion can copy their values."""

    def construct_document(self, node: yaml.Node) -> object:
        validate_yaml_graph(
            node, maximum_depth=_SOURCE_DEPTH_LIMIT, maximum_nodes=_SOURCE_NODE_LIMIT,
        )
        return super().construct_document(node)

    def construct_mapping(self, node: yaml.MappingNode, deep: bool = False) -> dict[str, Any]:
        self.flatten_mapping(node)
        result: dict[str, Any] = {}
        for key, value in self.construct_pairs(node, deep=deep):
            if not isinstance(key, str) or key in result:
                raise ValueError("Ruleset source requires unique text field names")
            result[key] = value
        return result


def _read_source(source_bytes: bytes) -> object:
    """Bound the source tree and reject YAML-only values and cycles before recursive work."""
    loaded = yaml.load(source_bytes.decode("utf-8"), Loader=_SourceLoader)
    pending = [(loaded, 0)]
    nodes = 0
    while pending:
        value, depth = pending.pop()
        nodes += 1
        if depth > _SOURCE_DEPTH_LIMIT or nodes > _SOURCE_NODE_LIMIT:
            raise ValueError("Ruleset source nesting or size exceeds the supported limit")
        if isinstance(value, dict):
            pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)
        elif value is None or isinstance(value, (str, bool, int)):
            continue
        elif not isinstance(value, float) or not math.isfinite(value):
            raise ValueError("Ruleset source values must be finite JSON-compatible data")
    return loaded


def _diagnostic(
    message: str,
    *,
    code: str,
    path: str,
    package_id: str | None = None,
    guidance: str | None = None,
) -> CommandRejected:
    return CommandRejected(
        "Builder Ruleset source is invalid",
        code="builder_ruleset_source_invalid",
        diagnostics=(
            RulesetDiagnostic(
                code=code,
                phase="source",
                message=message,
                file=COMPACT_RULESET_SOURCE,
                path=path,
                package_id=package_id,
                guidance=guidance,
            ),
        ),
    )


def _mapping(value: object, *, path: str, package_id: str | None = None) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise _diagnostic(
            "Expected a YAML object.",
            code="ruleset_source_expected_object",
            path=path,
            package_id=package_id,
        )
    if any(not isinstance(key, str) for key in value):
        raise _diagnostic(
            "Object field names must be text.",
            code="ruleset_source_field_name_invalid", path=path, package_id=package_id,
        )
    return {key: deepcopy(item) for key, item in value.items()}


def _require_fields(
    value: Mapping[str, object], required: set[str] | frozenset[str], *,
    optional: set[str] | frozenset[str] = frozenset(), path: str,
    package_id: str | None = None,
) -> None:
    """Identify each missing or undeclared structured field for a repairable draft."""

    missing = required - set(value)
    extra = set(value) - required - optional
    diagnostics = tuple(
        RulesetDiagnostic(
            code=f"ruleset_source_{kind}_field", phase="source",
            message=f"Field '{field}' is {kind}.", file=COMPACT_RULESET_SOURCE,
            path=f"{path}.{field}", package_id=package_id,
            guidance=("Supply the required field." if kind == "missing"
                      else "Remove the undeclared field."),
        )
        for kind, fields in (("missing", missing), ("undeclared", extra))
        for field in sorted(fields)
    )
    if diagnostics:
        raise CommandRejected(
            "Builder Ruleset source is invalid", code="builder_ruleset_source_invalid",
            diagnostics=diagnostics,
        )


def _sequence(value: object, *, path: str, package_id: str) -> list[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise _diagnostic(
            "Expected a YAML list.",
            code="ruleset_source_expected_list",
            path=path,
            package_id=package_id,
        )
    return list(value)


def _substitute(value: object, configuration: Mapping[str, object]) -> object:
    """Expand only explicit whole or embedded ``{name}`` family configuration tokens."""

    if isinstance(value, str):
        for name, replacement in configuration.items():
            token = "{" + name + "}"
            if value == token:
                return deepcopy(replacement)
            if token in value:
                if not isinstance(replacement, (str, int, float, bool)):
                    raise ValueError("Embedded family configuration values must be scalar")
                value = value.replace(token, str(replacement))
        return value
    if isinstance(value, Mapping):
        return {str(key): _substitute(item, configuration) for key, item in value.items()}
    if isinstance(value, list):
        return [_substitute(item, configuration) for item in value]
    return deepcopy(value)


def _runtime_parameter(
    raw: object,
    *,
    path: str,
    package_id: str,
) -> tuple[dict[str, object], dict[str, object] | None]:
    parameter = _mapping(raw, path=path, package_id=package_id)
    allowed = {
        "name",
        "type",
        "source",
        "required",
        "description",
        "enum_values",
        "minimum",
        "maximum",
        "min_items",
        "max_items",
        "unique_items",
        "items",
        "resolve",
        "from_state",
        "default",
    }
    if set(parameter) - allowed:
        raise _diagnostic(
            "Parameter contains unsupported fields.",
            code="ruleset_source_parameter_fields",
            path=path,
            package_id=package_id,
            guidance="Use choice, gm_judgment, or state as the input source.",
        )
    name = parameter.get("name")
    parameter_type = parameter.get("type")
    source = parameter.pop("source", None)
    if not isinstance(name, str) or not name or not isinstance(parameter_type, str):
        raise _diagnostic(
            "Parameter name and type are required.",
            code="ruleset_source_parameter_identity",
            path=path,
            package_id=package_id,
        )
    if source not in {"choice", "gm_judgment", "state"}:
        raise _diagnostic(
            "Parameter input source is unsupported.",
            code="ruleset_source_parameter_authority",
            path=f"{path}.source",
            package_id=package_id,
            guidance="Choose choice, gm_judgment, or state.",
        )
    if source == "state":
        if not isinstance(parameter.get("from_state"), str):
            raise _diagnostic(
                "State-owned parameters require from_state.",
                code="ruleset_source_state_path_missing",
                path=f"{path}.from_state",
                package_id=package_id,
            )
        return cast(dict[str, object], parameter), None
    if "from_state" in parameter:
        raise _diagnostic(
            "Only a state-owned parameter may declare from_state.",
            code="ruleset_source_state_path_conflict",
            path=f"{path}.from_state",
            package_id=package_id,
        )
    schema: dict[str, object]
    if parameter_type == "enum":
        schema = {"type": "string", "enum": list(parameter.get("enum_values", []))}
    elif parameter_type == "entity_id_list":
        schema = {"type": "array", "items": {"type": "string"}}
    elif parameter_type in {"entity_id", "string"}:
        schema = {"type": "string"}
    else:
        schema = {"type": parameter_type}
    if parameter.get("resolve") in {"participant", "entity"}:
        schema["x-sylvia-gm-reference"] = parameter["resolve"]
    elif parameter.get("resolve") == "entity_list" and isinstance(schema.get("items"), dict):
        schema["items"]["x-sylvia-gm-reference"] = "entity"
    if isinstance(parameter.get("description"), str):
        schema["description"] = parameter["description"]
    for source_name, schema_name in (
        ("minimum", "minimum"),
        ("maximum", "maximum"),
        ("min_items", "minItems"),
        ("max_items", "maxItems"),
        ("unique_items", "uniqueItems"),
        ("items", "items"),
    ):
        if source_name in parameter:
            schema[schema_name] = deepcopy(parameter[source_name])
    schema["x-sylvia-gm-input-source"] = source
    return cast(dict[str, object], parameter), schema


def _required_engine_features(value: object) -> tuple[str, ...]:
    """Infer the host capabilities used by compiled runtime data."""

    features: set[str] = set()

    def collect(item: object) -> None:
        if isinstance(item, Mapping):
            if "isolated_participant" in item:
                features.add("isolated_participants.v1")
            if "shuffle" in item or any(
                isinstance(value, str) and any(
                    name + "(" in value for name in (
                        "indices", "portion", "joined", "replaced", "without",
                        "partitions", "completions",
                        "occurrences", "one", "ordered", "text", "total",
                    )
                )
                for value in item.values()
            ):
                features.add("collection_mechanics.v1")
            if "publish" in item:
                features.add("public_results.v1")
            if "shot_effect" in item:
                features.update({ENGINE_FEATURE_SHOT_EFFECTS, ENGINE_FEATURE_TACTICAL_GRID})
            if "occupies_cells" in item:
                features.add(ENGINE_FEATURE_SPATIAL_CLEARANCE)
            if "spatial_mutation" in item:
                features.update(
                    {ENGINE_FEATURE_SPATIAL_MUTATION, ENGINE_FEATURE_TACTICAL_GRID}
                )
            query = item.get("spatial_query")
            if isinstance(query, Mapping):
                features.add(ENGINE_FEATURE_TACTICAL_GRID)
                if query.get("operation") == "path":
                    features.add(ENGINE_FEATURE_SPATIAL_CLEARANCE)
                path_query = query.get("query")
                if (
                    query.get("operation") == "path"
                    and isinstance(path_query, Mapping)
                    and "moving_entity_id" in path_query
                ):
                    features.add(ENGINE_FEATURE_SPATIAL_PATH_MOVING_ENTITY)
            for nested in item.values():
                collect(nested)
        elif isinstance(item, list):
            for nested in item:
                collect(nested)

    collect(value)
    return tuple(sorted(features))


def compile_ruleset_source(source_bytes: bytes) -> CompiledRulesetSource:
    """Compile readable family-based YAML into the existing portable runtime representation."""

    try:
        loaded = _read_source(source_bytes)
    except (UnicodeDecodeError, yaml.YAMLError, ValueError, RecursionError) as error:
        raise _diagnostic(
            "Ruleset source must use bounded UTF-8 YAML with unique text fields "
            "and finite JSON values.",
            code="ruleset_source_yaml_invalid",
            path="$",
        ) from error
    source = _mapping(loaded, path="$")
    package_id = source.get("id") if isinstance(source.get("id"), str) else None
    optional_keys = {
        "ui_surfaces", "panel_layout", "tests", "state_schemas", "model_decision_boundary",
        "procedures",
    }
    _require_fields(source, _SOURCE_KEYS, optional=optional_keys, path="$", package_id=package_id)
    if type(source.get("format")) is not int or source["format"] != 1:
        raise _diagnostic(
            "Ruleset source format must be the integer 1.",
            code="ruleset_source_format_invalid",
            path="$.format",
            package_id=package_id,
        )
    if package_id is None:
        raise _diagnostic(
            "Ruleset identity is required.",
            code="ruleset_source_identity_missing",
            path="$.id",
        )
    for field in ("version", "label", "publisher"):
        if not isinstance(source[field], str) or not str(source[field]).strip():
            raise _diagnostic(
                f"Ruleset {field} is required.",
                code="ruleset_source_identity_missing",
                path=f"$.{field}",
                package_id=package_id,
            )
    families = _mapping(
        source["mechanic_families"], path="$.mechanic_families", package_id=package_id
    )
    for family_id, raw_family in families.items():
        family_path = f"$.mechanic_families.{family_id}"
        family = _mapping(raw_family, path=family_path, package_id=package_id)
        _require_fields(
            family, {"parameters", "pre_conditions", "effects", "narrative"},
            optional={"metadata"}, path=family_path, package_id=package_id,
        )
    try:
        validate_content_shape("ruleset_source", source, file=COMPACT_RULESET_SOURCE)
        if "tests" in source:
            validate_ruleset_tests(source["tests"])
    except ContentShapeError as error:
        raise CommandRejected(
            "Builder Ruleset source is invalid", code="builder_ruleset_source_invalid",
            diagnostics=tuple(
                replace(item, file=COMPACT_RULESET_SOURCE, path="$.tests" + item.path[1:])
                if item.file == RULESET_TESTS_PATH else item
                for item in error.diagnostics
            ),
        ) from error
    runtime_tools: dict[str, object] = {}
    manifest_tools: list[dict[str, object]] = []
    capabilities: set[str] = set()
    support_records: list[dict[str, object]] = []

    def compile_operation(raw: object, *, path: str, public: bool) -> None:
        operation = _mapping(raw, path=path, package_id=package_id)
        required_fields = {"name", "family", "description", "configure"}
        public_fields = {
            "capability",
            "state_predicates",
            "turn_statuses",
            "writers",
            "reference_availability",
            "gameplay_resolution",
            "deterministic_part",
            "gm_part",
        }
        expected = required_fields | (public_fields if public else set())
        _require_fields(operation, expected, path=path, package_id=package_id)
        name = operation["name"]
        family_id = operation["family"]
        if not isinstance(name, str) or not isinstance(family_id, str) or family_id not in families:
            raise _diagnostic(
                "Operation names an unknown mechanic family.",
                code="ruleset_source_family_unknown",
                path=f"{path}.family",
                package_id=package_id,
            )
        family = _mapping(families[family_id], path=f"$.mechanic_families.{family_id}")
        if name in runtime_tools:
            raise _diagnostic(
                "Operation names must be unique across public actions and private mechanics.",
                code="ruleset_source_operation_duplicate", path=f"{path}.name",
                package_id=package_id, guidance="Give each operation a distinct name.",
            )
        configuration = _mapping(
            operation["configure"], path=f"{path}.configure", package_id=package_id
        )
        expanded = cast(dict[str, object], _substitute(family, configuration))
        runtime_parameters: list[dict[str, object]] = []
        model_properties: dict[str, object] = {}
        model_required: list[str] = []
        for index, raw_parameter in enumerate(
            _sequence(expanded["parameters"], path=f"{path}.parameters", package_id=package_id)
        ):
            runtime_parameter, model_schema = _runtime_parameter(
                raw_parameter,
                path=f"{path}.parameters[{index}]",
                package_id=package_id,
            )
            runtime_parameters.append(runtime_parameter)
            if model_schema is not None:
                parameter_name = cast(str, runtime_parameter["name"])
                model_properties[parameter_name] = model_schema
                if runtime_parameter.get("required", True) is True:
                    model_required.append(parameter_name)
        runtime_tool = {
            "description": operation["description"],
            "parameters": runtime_parameters,
            "pre_conditions": expanded["pre_conditions"],
            "effects": expanded["effects"],
            "narrative": expanded["narrative"],
        }
        if "metadata" in expanded:
            runtime_tool["metadata"] = expanded["metadata"]
        runtime_tools[name] = runtime_tool
        if not public:
            return
        capability = operation["capability"]
        resolution = operation["gameplay_resolution"]
        reference = operation["reference_availability"]
        if not isinstance(capability, str) or resolution not in _RESOLUTIONS:
            raise _diagnostic(
                "Public action capability or Gameplay Resolution is invalid.",
                code="ruleset_source_public_claim_invalid",
                path=path,
                package_id=package_id,
            )
        capabilities.add(capability)
        manifest_tools.append(
            {
                "name": name,
                "capability": capability,
                "input_schema": {
                    "type": "object",
                    "properties": model_properties,
                    "required": model_required,
                    "additionalProperties": False,
                },
                "state_predicates": operation["state_predicates"],
                "turn_statuses": operation["turn_statuses"],
                "writers": operation["writers"],
            }
        )
        support_records.append(
            {
                "id": f"action.{name}",
                "reference_availability": reference,
                "gameplay_resolution": resolution,
                "mechanic_family": (
                    family_id
                    if resolution in {"engine_family", "hybrid", "gm_adjudicated"}
                    else None
                ),
                "deterministic_part": operation["deterministic_part"],
                "gm_part": operation["gm_part"],
            }
        )

    for index, operation in enumerate(
        _sequence(source["private_mechanics"], path="$.private_mechanics", package_id=package_id)
    ):
        compile_operation(operation, path=f"$.private_mechanics[{index}]", public=False)
    for index, operation in enumerate(
        _sequence(source["public_actions"], path="$.public_actions", package_id=package_id)
    ):
        compile_operation(operation, path=f"$.public_actions[{index}]", public=True)
    surfaces = []
    for index, raw in enumerate(_sequence(
        source.get("ui_surfaces", []), path="$.ui_surfaces", package_id=package_id,
    )):
        surface = _mapping(raw, path=f"$.ui_surfaces[{index}]", package_id=package_id)
        if not isinstance(surface.get("id"), str) or not isinstance(surface.get("capability"), str):
            raise _diagnostic(
                "Each game panel needs an identity and capability.",
                code="ruleset_source_panel_invalid", path=f"$.ui_surfaces[{index}]",
                package_id=package_id,
            )
        capabilities.add(surface["capability"])
        surfaces.append({
            "title": surface["id"],
            "icon": "◇",
            "group": None,
            "order": index,
            "default_size": 180,
            "minimum_size": 96,
            "default_placement": "docked",
            "allowed_placements": ["docked", "window"],
            "actions": [],
            **surface,
        })
    procedures = _mapping(source.get("procedures", {}), path="$.procedures", package_id=package_id)
    required_features = set(_required_engine_features({
        "tools": runtime_tools, "procedures": procedures,
    }))
    if any(surface.get("renderer") == "tactical_grid" for surface in surfaces):
        required_features.add(ENGINE_FEATURE_TACTICAL_GRID)
    if "panel_layout" in source:
        required_features.add(ENGINE_FEATURE_PANEL_WORKSPACES)
    if "state_schemas" in source:
        required_features.add(STARTING_STATE_FEATURE)
        if any(item.get("visibility") == "engine" for item in source["state_schemas"]):
            required_features.add(PRIVATE_STATE_FEATURE)
    required_features = tuple(sorted(required_features))
    runtime = {
        "sylvia_gm_runtime": {"format": 1, "state_namespace": package_id},
        "features": {},
        "abilities": {},
        "tools": runtime_tools,
        "procedures": procedures,
        "definitions": source["definitions"],
        "narrative": {},
    }
    if "model_decision_boundary" in source:
        runtime["model_decision_boundary"] = source["model_decision_boundary"]
    support = {
        "format_id": "sylvia-gm-gameplay-support",
        "format_version": 1,
        "records": sorted(support_records, key=lambda item: cast(str, item["id"])),
    }
    payloads: dict[str, bytes] = {
        "runtime/ruleset.yaml": yaml.safe_dump(runtime, sort_keys=False, allow_unicode=True).encode(
            "utf-8"
        ),
        "support/gameplay-resolution.json": canonical_json_bytes(support),
    }
    if "panel_layout" in source:
        payloads[PANEL_LAYOUT_PATH] = canonical_json_bytes(source["panel_layout"])
    if "tests" in source:
        payloads[RULESET_TESTS_PATH] = canonical_json_bytes(source["tests"])
    prompt_fragments: list[dict[str, str]] = []
    guide = source["operating_guide"]
    if guide is not None:
        if not isinstance(guide, str) or not guide.strip():
            raise _diagnostic(
                "Operating guide must be non-empty text or null.",
                code="ruleset_source_operating_guide_invalid",
                path="$.operating_guide",
                package_id=package_id,
            )
        capability = f"{package_id}.guidance"
        capabilities.add(capability)
        prompt_fragments.append({"id": "gm-operating-guide", "capability": capability})
        payloads["prompts/gm-operating-guide.md"] = guide.encode("utf-8")
    source_sha = hashlib.sha256(source_bytes).hexdigest()
    artifacts = sorted(payloads)
    content_declarations = {
        "format_id": "sylvia-gm-package-content-declarations",
        "format_version": 1,
        "scope": {
            "scope_id": f"{package_id}.gameplay",
            "scope_kind": "bounded_package",
            "claim_status": "NOT_COMPLETE",
            "source_boundary_refs": [],
        },
        "source_authorities": [
            {
                "id": f"{package_id}.source",
                "document_id": f"{package_id}.ruleset-source",
                "version": str(source["version"]),
                "media_type": "application-yaml",
                "content_sha256": source_sha,
                "license_id": "NOASSERTION",
            }
        ],
        "inclusions": [
            {
                "id": f"{package_id}.gameplay-runtime",
                "kind": "ruleset_runtime",
                "family": "ruleset-runtime",
                "disposition": "included_mechanical",
                "classifications": ["sylvia_gm_implementation"],
                "sources": [],
                "links": {
                    "artifacts": artifacts,
                    "capabilities": sorted(capabilities),
                    "mechanics": [],
                    "procedures": sorted(procedures),
                    "prompt_fragments": [item["id"] for item in prompt_fragments],
                    "schemas": [
                        item["schema_id"]
                        for item in source["participant_artifacts"]
                        if isinstance(item, Mapping) and isinstance(item.get("schema_id"), str)
                    ],
                    "state_namespaces": [],
                    "tools": sorted(item["name"] for item in manifest_tools),
                    "ui_surfaces": sorted(surface["id"] for surface in surfaces),
                },
                "evidence": {"tests": []},
            }
        ],
        "exclusions": [],
    }
    payloads["content/content-declarations.json"] = canonical_json_bytes(content_declarations)
    record = {
        "id": package_id,
        "version": source["version"],
        "role": "primary",
        "content_kind": "ruleset",
        "metadata": {"label": source["label"]},
        "provenance": {"publisher": source["publisher"], "source_sha256": source_sha},
        "compatibility": {
            "host_contract": 1,
            "runtime_format": 1,
            "requires": list(required_features),
        },
        "minimum_model_capability_tier": "sylvia.capability.t00",
        "dependencies": [],
        "capabilities": sorted(capabilities),
        "tools": sorted(manifest_tools, key=lambda item: cast(str, item["name"])),
        "prompt_fragments": prompt_fragments,
        "ui_surfaces": surfaces,
        "participant_artifacts": source["participant_artifacts"],
        "content_declarations": "content/content-declarations.json",
    }
    if "state_schemas" in source:
        record["state_schemas"] = source["state_schemas"]
    return CompiledRulesetSource(NormalizedPackageDraft(record, payloads, 4), required_features)


__all__ = ["COMPACT_RULESET_SOURCE", "CompiledRulesetSource", "compile_ruleset_source"]
