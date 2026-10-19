"""Pure immutable F3 package, closure, catalog, activation, and reference contracts."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from pathlib import PureWindowsPath
from typing import Never, cast

from sylvia_gm.domain.authority import (
    FrozenJsonValue,
    PackageDependency,
    PackageLock,
    PackageLockEntry,
    PackageRole,
    SafePayload,
    owned_json_value,
    owned_payload,
    thaw_json_value,
    validate_authority_identity,
)
from sylvia_gm.domain.content_validation import validate_content_shape
from sylvia_gm.domain.inventory import inventory_holding_schema
from sylvia_gm.domain.model_capability import validate_capability_tier_id
from sylvia_gm.domain.starting_state import (
    REQUIRED_STARTING_STATE_FEATURE,
    STARTING_STATE_FEATURE,
    StateSchema,
    parse_state_schemas,
)


class CandidateStatus(StrEnum):
    STAGING = "staging"
    VALIDATED = "validated"
    ACTIVATED = "activated"
    FAILED = "failed"


class PackageLockAvailability(StrEnum):
    AVAILABLE = "available"
    LEGACY_UNAVAILABLE = "legacy_unavailable"


class ActivationStatus(StrEnum):
    COMMITTED = "committed"
    FAILED = "failed"


@dataclass(frozen=True)
class PackageFile:
    path: str
    byte_count: int
    sha256: str

    def __post_init__(self) -> None:
        validate_portable_path(self.path)
        if self.byte_count < 0:
            raise ValueError("Package file byte count cannot be negative")
        validate_authority_identity(self.sha256, "Package file SHA-256", sha256=True)


@dataclass(frozen=True)
class ExactPackageDependency:
    namespace: str
    kind: str
    package_id: str
    version: str
    content_sha256: str

    def __post_init__(self) -> None:
        validate_authority_identity(self.namespace, "Dependency namespace")
        validate_authority_identity(self.kind, "Dependency kind")
        validate_authority_identity(self.package_id, "Dependency package identity")
        validate_authority_identity(self.version, "Dependency version")
        validate_authority_identity(self.content_sha256, "Dependency content SHA-256", sha256=True)


@dataclass(frozen=True)
class StatePredicate:
    namespace: str
    key: str
    equals: FrozenJsonValue
    negated: bool = False


@dataclass(frozen=True)
class ToolDeclaration:
    name: str
    capability: str
    input_schema: SafePayload
    state_predicates: tuple[StatePredicate, ...]
    turn_statuses: tuple[str, ...]
    writers: tuple[str, ...]


def _validate_mechanical_inventory_declaration(
    inventory: Mapping[str, object], package_id: str,
) -> None:
    """Select Ruleset state and ordinary mechanics without granting bookkeeping write access."""
    if set(inventory) != {"format", "state", "surface_id", "tools"}:
        raise ValueError("Mechanical inventory must declare only its state, surface and tools")
    target = _object(inventory["state"], "Inventory state")
    if set(target) != {"namespace", "key"} or target.get("namespace") != package_id:
        raise ValueError("Mechanical inventory must address the declaring package's state")
    validate_authority_identity(target["key"], "Inventory state key")
    validate_authority_identity(inventory["surface_id"], "Inventory surface")
    names = _array(inventory["tools"], "Inventory tools")
    if not 1 <= len(names) <= 32:
        raise ValueError("Mechanical inventory requires a bounded tool list")
    identities = [validate_authority_identity(name, "Inventory tool") for name in names]
    if len(set(identities)) != len(identities):
        raise ValueError("Mechanical inventory tool names must be unique")


def _validate_character_update_declaration(schema: SafePayload, package_id: str) -> None:
    """Check the shared-bookkeeping shape while leaving editable fields to the Ruleset."""
    marker = "x-sylvia-gm-character-update"
    inventory_annotation = schema.get("x-sylvia-gm-inventory")
    if (
        isinstance(inventory_annotation, Mapping)
        and inventory_annotation.get("format") == "mechanics"
    ):
        if marker in schema:
            raise ValueError("Mechanical inventory cannot also declare character bookkeeping")
        _validate_mechanical_inventory_declaration(inventory_annotation, package_id)
        return
    if marker not in schema:
        if "x-sylvia-gm-inventory" in schema:
            raise ValueError("Inventory must use the shared character bookkeeping declaration")
        return
    try:
        target = schema[marker]
        if set(target) != {"namespace", "key"} or target["namespace"] != package_id:
            raise ValueError("Bookkeeping must address the declaring package's state")
        validate_authority_identity(target["key"], "Character state key")
        properties = schema["properties"]
        updates = properties["updates"]
        item = updates["items"]
        fields = item["properties"]["fields"]
        if (
            schema.get("type") != "object"
            or schema.get("additionalProperties") is not False
            or set(properties) != {"updates"}
            or schema.get("required") != ["updates"]
            or updates.get("type") != "array"
            or item.get("type") != "object"
            or set(item["properties"]) != {"participant_id", "fields"}
            or set(item.get("required", ())) != {"participant_id", "fields"}
            or item.get("additionalProperties") is not False
            or item["properties"]["participant_id"].get("type") != "string"
            or fields.get("type") != "object"
            or not fields.get("properties")
            or fields.get("additionalProperties") is not False
        ):
            raise ValueError("Character bookkeeping schema must declare bounded editable fields")
        inventory = schema.get("x-sylvia-gm-inventory")
        if inventory is not None:
            if (
                not isinstance(inventory, Mapping)
                or set(inventory) not in ({"field", "format"}, {"field", "format", "surface_id"})
                or inventory.get("format") not in {"string_list", "holdings"}
                or inventory.get("field") not in fields["properties"]
            ):
                raise ValueError(
                    "Ruleset inventory must identify a declared editable inventory field"
                )
            if "surface_id" in inventory:
                validate_authority_identity(inventory["surface_id"], "Inventory surface")
            inventory_field = fields["properties"][inventory["field"]]
            if inventory_field.get("type") != "array" or inventory_field.get("items", {}).get(
                "type"
            ) != ("string" if inventory["format"] == "string_list" else "object"):
                raise ValueError("Inventory projection format must match the editable field schema")
            if inventory["format"] == "holdings" and inventory_field["items"] != (
                inventory_holding_schema()
            ):
                raise ValueError(
                    "Structured inventory must use the shared inventory holding schema"
                )
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("Character bookkeeping declaration is malformed") from error


def _validate_inventory_targets(
    tools: Sequence[ToolDeclaration], surfaces: Sequence[SurfaceDeclaration],
) -> None:
    """A mechanical inventory selects existing package tools and one real presentation surface."""
    declarations = [tool for tool in tools if "x-sylvia-gm-inventory" in tool.input_schema]
    if len(declarations) > 1:
        raise ValueError("Ruleset inventory declaration is ambiguous")
    if not declarations:
        return
    declaration = declarations[0]
    inventory = _object(declaration.input_schema["x-sylvia-gm-inventory"], "Ruleset inventory")
    if inventory.get("format") != "mechanics":
        return
    by_name = {tool.name: tool for tool in tools}
    names = [
        validate_authority_identity(name, "Inventory tool")
        for name in _array(inventory["tools"], "Inventory tools")
    ]
    for name in names:
        if name not in by_name or "x-sylvia-gm-character-update" in by_name[name].input_schema:
            raise ValueError("Mechanical inventory must reference declared mechanical tools")
        if "dm" not in by_name[name].writers:
            raise ValueError("Mechanical inventory tools must allow the GM writer")
    if declaration.name not in names:
        raise ValueError("Mechanical inventory must include its declaring tool")
    if not any(surface.identity == inventory["surface_id"] for surface in surfaces):
        raise ValueError("Mechanical inventory must reference a declared presentation surface")


@dataclass(frozen=True)
class SurfaceDeclaration:
    identity: str
    capability: str
    title: str | None = None
    icon: str | None = None
    renderer: str | None = None
    source: SafePayload | None = None
    group: str | None = None
    order: int = 0
    default_size: int = 180
    minimum_size: int = 96
    default_placement: str = "docked"
    allowed_placements: tuple[str, ...] = ("docked",)
    actions: tuple[SafePayload, ...] = ()


@dataclass(frozen=True)
class ParticipantArtifactDeclaration:
    kind: str
    schema_id: str
    schema_sha256: str
    schema: SafePayload
    entity_state_schema: SafePayload | None = None
    initial_state_calculations: tuple[SafePayload, ...] = ()
    initial_state_validation: SafePayload | None = None

    def __post_init__(self) -> None:
        validate_authority_identity(self.kind, "Participant artifact kind")
        validate_authority_identity(self.schema_id, "Participant artifact schema identity")
        validate_authority_identity(
            self.schema_sha256, "Participant artifact schema SHA-256", sha256=True
        )
        if self.entity_state_schema is not None:
            object.__setattr__(self, "entity_state_schema", owned_payload(self.entity_state_schema))
        elif self.initial_state_calculations or self.initial_state_validation is not None:
            raise ValueError("Character calculations require a complete entity-state schema")
        object.__setattr__(self, "initial_state_calculations", tuple(
            owned_payload(item) for item in self.initial_state_calculations
        ))
        if self.initial_state_validation is not None:
            object.__setattr__(self, "initial_state_validation",
                               owned_payload(self.initial_state_validation))


CONTENT_DECLARATION_BYTE_LIMIT = 32 * 1024 * 1024
CONTENT_DECLARATION_DEPTH_LIMIT = 16
CONTENT_DECLARATION_NODE_LIMIT = 250_000
CONTENT_DECLARATION_OBJECT_MEMBER_LIMIT = 32
CONTENT_DECLARATION_SOURCE_AUTHORITY_LIMIT = 64
CONTENT_DECLARATION_BOUNDARY_REFERENCE_LIMIT = 256
CONTENT_DECLARATION_RECORD_LIMIT = 10_000
CONTENT_DECLARATION_SOURCE_LOCATOR_LIMIT = 64
CONTENT_DECLARATION_SECTION_PATH_LIMIT = 32
CONTENT_DECLARATION_LINK_FAMILY_LIMIT = 256
CONTENT_DECLARATION_EVIDENCE_LIMIT = 256
CONTENT_DECLARATION_CLASSIFICATION_LIMIT = 8
CAMPAIGN_SETTING_KNOWLEDGE_PATH = "setting-knowledge.json"
CAMPAIGN_SETTING_KNOWLEDGE_BYTE_LIMIT = 256 * 1024
CAMPAIGN_SETTING_KNOWLEDGE_FACT_LIMIT = 128
GAMEPLAY_SUPPORT_PATH = "support/gameplay-resolution.json"
GAMEPLAY_SUPPORT_BYTE_LIMIT = 2 * 1024 * 1024
GAMEPLAY_SUPPORT_RECORD_LIMIT = 10_000
PACKAGE_HOST_CONTRACT = 1
PACKAGE_RUNTIME_FORMAT = 1
ENGINE_FEATURE_SPATIAL_MUTATION = "spatial_mutation.v1"
ENGINE_FEATURE_SPATIAL_PATH_MOVING_ENTITY = "spatial_path_moving_entity.v1"
ENGINE_FEATURE_SPATIAL_CLEARANCE = "spatial_clearance.v1"
ENGINE_FEATURE_SHOT_EFFECTS = "shot_effects.v1"
ENGINE_FEATURE_PANEL_WORKSPACES = "panel_workspaces.v1"
ENGINE_FEATURE_TACTICAL_GRID = "tactical_grid.v1"
SUPPORTED_ENGINE_FEATURES = frozenset(
    {
        ENGINE_FEATURE_SPATIAL_MUTATION,
        ENGINE_FEATURE_SPATIAL_PATH_MOVING_ENTITY,
        ENGINE_FEATURE_SPATIAL_CLEARANCE,
        ENGINE_FEATURE_SHOT_EFFECTS,
        ENGINE_FEATURE_PANEL_WORKSPACES,
        ENGINE_FEATURE_TACTICAL_GRID,
        STARTING_STATE_FEATURE,
        REQUIRED_STARTING_STATE_FEATURE,
        "participant_mutation.v1",
        "runtime_require.v1",
        "runtime_choice_sequences.v1",
        "character_rule_results.v1",
        "campaign_starting_events.v1",
        "runtime_immutable_collections.v1",
        "entity_creation.v1",
    }
)


@dataclass(frozen=True)
class ContentSourceAuthority:
    identity: str
    document_id: str
    version: str
    media_type: str
    content_sha256: str
    license_id: str


@dataclass(frozen=True)
class ContentSourceLocator:
    authority_id: str
    source_unit_id: str
    kind: str
    page: int | None
    section_path: tuple[str, ...]
    derived_text_sha256: str | None
    page_range: tuple[int, int] | None = None
    content_sha256: str | None = None


@dataclass(frozen=True)
class ContentLinks:
    artifacts: tuple[str, ...]
    schemas: tuple[str, ...]
    state_namespaces: tuple[str, ...]
    mechanics: tuple[str, ...]
    procedures: tuple[str, ...]
    capabilities: tuple[str, ...]
    tools: tuple[str, ...]
    prompt_fragments: tuple[str, ...]
    ui_surfaces: tuple[str, ...]


@dataclass(frozen=True)
class ContentInclusion:
    identity: str
    kind: str
    family: str
    disposition: str
    classifications: tuple[str, ...]
    sources: tuple[ContentSourceLocator, ...]
    links: ContentLinks
    test_evidence: tuple[str, ...]


@dataclass(frozen=True)
class ContentExclusion:
    identity: str
    kind: str
    family: str
    reason: str
    classifications: tuple[str, ...]
    sources: tuple[ContentSourceLocator, ...]
    within_declared_scope: bool


@dataclass(frozen=True)
class PackageContentDeclarations:
    scope_id: str
    scope_kind: str
    claim_status: str
    source_boundary_refs: tuple[tuple[str, str], ...]
    source_authorities: tuple[ContentSourceAuthority, ...]
    inclusions: tuple[ContentInclusion, ...]
    exclusions: tuple[ContentExclusion, ...]
    canonical_json: str


@dataclass(frozen=True)
class GameplaySupportRecord:
    """One installed content claim, independent of authoring progress or evidence."""

    identity: str
    reference_availability: str
    gameplay_resolution: str
    mechanic_family: str | None
    deterministic_part: str | None
    gm_part: str | None


@dataclass(frozen=True)
class GameplaySupport:
    """Validated installed support claims for one exact gameplay package."""

    records: tuple[GameplaySupportRecord, ...]
    canonical_json: str


@dataclass(frozen=True)
class CampaignSettingKnowledgeFact:
    identity: str
    fact: str
    known_by: tuple[str, ...]
    source_path: str


@dataclass(frozen=True)
class CampaignSettingKnowledge:
    facts: tuple[CampaignSettingKnowledgeFact, ...]
    private_references: tuple[str, ...]
    canonical_json: str


@dataclass(frozen=True)
class PackageManifest:
    package_id: str
    version: str
    role: PackageRole
    content_sha256: str
    canonical_json: str
    metadata: SafePayload
    provenance: SafePayload
    compatibility: SafePayload
    minimum_model_capability_tier: str
    dependencies: tuple[ExactPackageDependency, ...]
    capabilities: tuple[str, ...]
    tools: tuple[ToolDeclaration, ...]
    prompt_fragments: tuple[SurfaceDeclaration, ...]
    ui_surfaces: tuple[SurfaceDeclaration, ...]
    files: tuple[PackageFile, ...]
    format_version: int = 1
    participant_artifacts: tuple[ParticipantArtifactDeclaration, ...] = ()
    content_declarations_path: str | None = None
    state_schemas: tuple[StateSchema, ...] = ()

    @property
    def identity(self) -> tuple[str, str, str]:
        return self.package_id, self.version, self.content_sha256


@dataclass(frozen=True, slots=True)
class PackageGraphNode:
    """Keep exact dependency semantics without retaining package schemas or content."""

    identity: tuple[str, str, str]
    role: PackageRole
    format_version: int
    content_kind: str | None
    dependencies: tuple[ExactPackageDependency, ...]

    @classmethod
    def from_manifest(cls, manifest: PackageManifest) -> PackageGraphNode:
        return cls(
            identity=manifest.identity,
            role=manifest.role,
            format_version=manifest.format_version,
            content_kind=(
                json.loads(manifest.canonical_json)["content_kind"]
                if manifest.format_version == 4 else None
            ),
            dependencies=manifest.dependencies,
        )


@dataclass(frozen=True)
class PackageCandidate:
    candidate_id: str
    install_id: str
    status: CandidateStatus
    closure_sha256: str
    package_lock: PackageLock
    validation_report: SafePayload

    def __post_init__(self) -> None:
        if not self.package_lock.entries:
            raise ValueError("Package candidate lock must be nonempty")


@dataclass(frozen=True)
class PackageLockRecord:
    lock_id: str
    campaign_id: str
    closure_sha256: str
    package_lock: PackageLock
    availability: PackageLockAvailability
    predecessor_lock_id: str | None
    created_revision: int


@dataclass(frozen=True)
class PackageSurfaceProjection:
    prompt_fragment_ids: tuple[str, ...]
    ui_surface_ids: tuple[str, ...]
    ui_surfaces: tuple[SafePayload, ...] = ()


@dataclass(frozen=True)
class PackageBlobReadReceipt:
    sha256: str
    byte_count: int
    maximum_chunk_size: int
    lease_id: str


@dataclass(frozen=True)
class PackageStagingRecord:
    install_id: str
    status: str
    chunk_count: int


@dataclass(frozen=True)
class ReclamationResult:
    deleted_blobs: int
    campaign_lock_roots: int
    candidate_roots: int
    activation_roots: int
    staging_roots: int
    read_lease_roots: int
    recovery_roots: int


@dataclass(frozen=True)
class ActivationCommand:
    campaign_id: str
    candidate_id: str
    activation_id: str
    expected_revision: int
    expected_lock_id: str
    migration_id: str

    def __post_init__(self) -> None:
        for value, label in (
            (self.campaign_id, "Activation campaign identity"),
            (self.candidate_id, "Activation candidate identity"),
            (self.activation_id, "Activation identity"),
            (self.expected_lock_id, "Activation expected lock identity"),
            (self.migration_id, "Activation migration identity"),
        ):
            validate_authority_identity(value, label)
        if self.expected_revision < 0:
            raise ValueError("Activation expected revision cannot be negative")


@dataclass(frozen=True)
class ActivationResult:
    campaign_id: str
    activation_id: str
    status: ActivationStatus
    old_lock_id: str
    new_lock_id: str
    resulting_revision: int
    payload_sha256: str
    replayed: bool = False
    resolution: str = "confirmed_commit"


def canonical_json_bytes(value: object) -> bytes:
    """Encode finite owned JSON with the exact F3 canonical byte representation."""

    captured = owned_json_value(thaw_json_value(value))
    return json.dumps(
        thaw_json_value(captured),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def participant_schema_sha256(
    schema: Mapping[str, object], *, entity_state_schema: Mapping[str, object] | None = None,
    initial_state_calculations: Sequence[Mapping[str, object]] = (),
    initial_state_validation: Mapping[str, object] | None = None,
) -> str:
    """Bind the complete creation contract while preserving older participant-only identities."""
    if entity_state_schema is None:
        if initial_state_calculations or initial_state_validation is not None:
            raise ValueError("Character calculations require a complete entity-state schema")
        payload: object = schema
    else:
        payload = {
            "schema": schema,
            "entity_state_schema": entity_state_schema,
            "initial_state_calculations": list(initial_state_calculations),
        }
        if initial_state_validation is not None:
            payload["initial_state_validation"] = initial_state_validation
    return hashlib.sha256(canonical_json_bytes(payload)).hexdigest()


def _duplicate_members(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Canonical JSON members must be unique")
        result[key] = value
    return result


def _nonstandard_constant(_value: str) -> Never:
    raise ValueError("Canonical JSON values must be finite and standard")


def _preflight_json_shape(
    value: object,
    *,
    maximum_depth: int,
    maximum_nodes: int,
    maximum_object_members: int | None = None,
) -> None:
    """Bound a materialized JSON tree before recursive ownership/canonical copies."""

    stack: list[tuple[object, int]] = [(value, 1)]
    nodes = 0
    while stack:
        item, depth = stack.pop()
        if depth > maximum_depth:
            raise ValueError("Canonical JSON nesting is too deep")
        nodes += 1
        if nodes > maximum_nodes:
            raise ValueError("Canonical JSON node count is too large")
        if isinstance(item, dict):
            if maximum_object_members is not None and len(item) > maximum_object_members:
                raise ValueError("Canonical JSON object member count is too large")
            child_depth = depth + 1
            for child in item.values():
                if child_depth > maximum_depth:
                    raise ValueError("Canonical JSON nesting is too deep")
                nodes += 1  # Object-member names are materialized nodes too.
                if nodes > maximum_nodes:
                    raise ValueError("Canonical JSON node count is too large")
                stack.append((child, child_depth))
        elif isinstance(item, list):
            child_depth = depth + 1
            if item and child_depth > maximum_depth:
                raise ValueError("Canonical JSON nesting is too deep")
            stack.extend((child, child_depth) for child in item)


def parse_content_json_bytes(
    value: bytes,
    *,
    label: str = "Package manifest",
    maximum_depth: int | None = None,
    maximum_nodes: int | None = None,
    maximum_object_members: int | None = None,
) -> FrozenJsonValue:
    """Read finite JSON with unique fields and optional bounds, allowing authored whitespace."""
    try:
        text = value.decode("utf-8", errors="strict")
        parsed = json.loads(
            text,
            object_pairs_hook=_duplicate_members,
            parse_constant=_nonstandard_constant,
        )
        if (
            maximum_depth is not None
            or maximum_nodes is not None
            or maximum_object_members is not None
        ):
            if maximum_depth is None or maximum_nodes is None:
                raise ValueError("Canonical JSON shape budgets must be paired")
            _preflight_json_shape(
                parsed,
                maximum_depth=maximum_depth,
                maximum_nodes=maximum_nodes,
                maximum_object_members=maximum_object_members,
            )
        captured = owned_json_value(parsed)
    except (
        UnicodeDecodeError,
        json.JSONDecodeError,
        RecursionError,
        TypeError,
        ValueError,
    ) as error:
        raise ValueError(f"{label} is not strict JSON") from error
    return captured


def parse_canonical_json_bytes(
    value: bytes,
    *,
    label: str = "Package manifest",
    maximum_depth: int | None = None,
    maximum_nodes: int | None = None,
    maximum_object_members: int | None = None,
) -> FrozenJsonValue:
    """Require the strict content parser's value to use its exact canonical encoding."""
    try:
        captured = parse_content_json_bytes(
            value, label=label, maximum_depth=maximum_depth, maximum_nodes=maximum_nodes,
            maximum_object_members=maximum_object_members,
        )
    except ValueError as error:
        raise ValueError(f"{label} is not strict canonical JSON") from error
    if value != canonical_json_bytes(captured):
        raise ValueError(f"{label} bytes are not canonical")
    return captured


def content_root_for_record(record: Mapping[str, object]) -> str:
    """Hash the exact canonical package record with its root claim omitted."""

    without_claim = {key: value for key, value in record.items() if key != "content_sha256"}
    return hashlib.sha256(canonical_json_bytes(without_claim)).hexdigest()


def closure_sha256(package_lock: PackageLock) -> str:
    membership = [
        {
            "id": entry.package_id,
            "version": entry.version,
            "content_sha256": entry.sha256,
            "role": entry.role.value,
            "load_order": entry.load_order,
        }
        for entry in package_lock.entries
    ]
    return hashlib.sha256(canonical_json_bytes(membership)).hexdigest()


def activation_payload_sha256(command: ActivationCommand) -> str:
    """Hash only caller-owned request fields so durable lookup never dereferences a candidate."""

    return hashlib.sha256(
        canonical_json_bytes(
            {
                "campaign_id": command.campaign_id,
                "candidate_id": command.candidate_id,
                "expected_revision": command.expected_revision,
                "expected_lock_id": command.expected_lock_id,
                "migration_id": command.migration_id,
            }
        )
    ).hexdigest()


def validate_portable_path(path: object) -> str:
    """Require ordinary relative filenames, including on the Windows repair/export path."""
    if not isinstance(path, str) or not path:
        raise ValueError("Portable package path must be nonempty text")
    if "\x00" in path or "\\" in path or path.startswith("/"):
        raise ValueError("Portable package path is unsafe")
    if re.match(r"^[A-Za-z]:", path):
        raise ValueError("Portable package path cannot contain a drive")
    pieces = path.split("/")
    if any(piece in ("", ".", "..") for piece in pieces):
        raise ValueError("Portable package path contains an alias")
    if any(piece != piece.strip() for piece in pieces):
        raise ValueError("Portable package path contains ambiguous whitespace")
    if any(ord(character) < 32 or character in '<>:"|?*' for character in path):
        raise ValueError("Portable package path contains non-portable characters")
    if any(piece.endswith(".") or PureWindowsPath(piece).is_reserved() for piece in pieces):
        raise ValueError("Portable package path contains a reserved name or alias")
    return path


def _exact_keys(value: Mapping[str, object], expected: frozenset[str], label: str) -> None:
    if frozenset(value) != expected:
        raise ValueError(f"{label} fields do not match the portable format")


def _object(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping) or not all(isinstance(key, str) for key in value):
        raise ValueError(f"{label} must be an object")
    return cast(Mapping[str, object], value)


def _array(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise ValueError(f"{label} must be an array")
    return value


def _bounded_array(
    value: object,
    label: str,
    maximum_items: int,
) -> Sequence[object]:
    result = _array(value, label)
    if len(result) > maximum_items:
        raise ValueError(f"{label} has too many entries")
    return result


def _safe_small_label(value: object, label: str) -> str:
    validate_authority_identity(value, label)
    if not isinstance(value, str):
        raise ValueError(f"{label} must be a string")
    if len(value.encode("utf-8")) > 256 or "/" in value or "\\" in value or ":" in value:
        raise ValueError(f"{label} is not a portable safe label")
    return value


def _sorted_unique_identities(
    value: object,
    label: str,
    maximum_items: int,
) -> tuple[str, ...]:
    result = tuple(cast(str, item) for item in _bounded_array(value, label, maximum_items))
    if any(not isinstance(item, str) for item in result):
        raise ValueError(f"{label} must contain strings")
    for item in result:
        validate_authority_identity(item, label)
        if len(item.encode("utf-8")) > 512:
            raise ValueError(f"{label} contains an oversized identity")
    if tuple(sorted(set(result))) != result:
        raise ValueError(f"{label} must be sorted and unique")
    return result


def _content_sources(
    value: object,
    *,
    authorities: frozenset[str],
    label: str,
) -> tuple[ContentSourceLocator, ...]:
    result: list[ContentSourceLocator] = []
    for raw in _bounded_array(
        value,
        label,
        CONTENT_DECLARATION_SOURCE_LOCATOR_LIMIT,
    ):
        source = _object(raw, label)
        _exact_keys(
            source,
            frozenset({"authority_id", "source_unit_id", "locator"}),
            label,
        )
        authority_id = validate_authority_identity(source["authority_id"], f"{label} authority")
        if authority_id not in authorities:
            raise ValueError(f"{label} references an unknown source authority")
        source_unit_id = validate_authority_identity(source["source_unit_id"], f"{label} unit")
        locator = _object(source["locator"], f"{label} locator")
        kind = locator.get("kind")
        if kind == "pdf_page_section":
            _exact_keys(
                locator,
                frozenset(
                    {
                        "kind",
                        "page",
                        "section_path",
                        "derived_text_sha256",
                    }
                ),
                f"{label} locator",
            )
            page = locator["page"]
            if not isinstance(page, int) or isinstance(page, bool) or not 1 <= page <= 100_000:
                raise ValueError(f"{label} locator page is invalid")
            section_path = tuple(
                cast(str, item)
                for item in _bounded_array(
                    locator["section_path"],
                    f"{label} section path",
                    CONTENT_DECLARATION_SECTION_PATH_LIMIT,
                )
            )
            if not section_path or any(
                not isinstance(item, str)
                or not item
                or item != item.strip()
                or len(item.encode("utf-8")) > 256
                for item in section_path
            ):
                raise ValueError(f"{label} section path is invalid")
            derived_text_sha256 = validate_authority_identity(
                locator["derived_text_sha256"],
                f"{label} derived text SHA-256",
                sha256=True,
            )
            result.append(
                ContentSourceLocator(
                    authority_id,
                    source_unit_id,
                    kind,
                    page,
                    section_path,
                    derived_text_sha256,
                )
            )
        elif kind == "pdf_page_range":
            _exact_keys(
                locator,
                frozenset({"kind", "pages", "content_sha256"}),
                f"{label} locator",
            )
            pages = _bounded_array(locator["pages"], f"{label} page range", 2)
            if len(pages) != 2 or any(
                not isinstance(page, int) or isinstance(page, bool) for page in pages
            ):
                raise ValueError(f"{label} locator page range is invalid")
            first_page = cast(int, pages[0])
            last_page = cast(int, pages[1])
            if not 1 <= first_page <= last_page <= 100_000:
                raise ValueError(f"{label} locator page range is invalid")
            content_sha256 = validate_authority_identity(
                locator["content_sha256"],
                f"{label} content SHA-256",
                sha256=True,
            )
            result.append(
                ContentSourceLocator(
                    authority_id,
                    source_unit_id,
                    kind,
                    None,
                    (),
                    None,
                    (first_page, last_page),
                    content_sha256,
                )
            )
        else:
            raise ValueError(f"{label} locator kind is unsupported")
    identities = tuple((item.authority_id, item.source_unit_id) for item in result)
    if tuple(sorted(set(identities))) != identities:
        raise ValueError(f"{label} must be sorted and unique")
    return tuple(result)


def _content_links(value: object) -> ContentLinks:
    links = _object(value, "Content inclusion links")
    fields = (
        "artifacts",
        "schemas",
        "state_namespaces",
        "mechanics",
        "procedures",
        "capabilities",
        "tools",
        "prompt_fragments",
        "ui_surfaces",
    )
    _exact_keys(links, frozenset(fields), "Content inclusion links")
    values = {
        field: _sorted_unique_identities(
            links[field],
            f"Content {field}",
            CONTENT_DECLARATION_LINK_FAMILY_LIMIT,
        )
        for field in fields
    }
    return ContentLinks(**values)


def parse_campaign_setting_knowledge_bytes(value: bytes) -> CampaignSettingKnowledge:
    """Parse the compact fact-level and file-level GM knowledge contract."""

    if len(value) > CAMPAIGN_SETTING_KNOWLEDGE_BYTE_LIMIT:
        raise ValueError("Campaign Setting knowledge is too large")
    parsed = _object(
        parse_canonical_json_bytes(
            value,
            label="Campaign Setting knowledge",
            maximum_depth=8,
            maximum_nodes=10_000,
            maximum_object_members=8,
        ),
        "Campaign Setting knowledge",
    )
    validate_content_shape(
        "campaign_setting_knowledge", parsed, file=CAMPAIGN_SETTING_KNOWLEDGE_PATH
    )
    _exact_keys(
        parsed,
        frozenset({"format_id", "format_version", "facts", "private_references"}),
        "Campaign Setting knowledge",
    )
    if (
        parsed["format_id"] != "sylvia-gm-campaign-setting-knowledge"
        or parsed["format_version"] != 1
    ):
        raise ValueError("Campaign Setting knowledge format is unsupported")
    private_references = tuple(
        validate_portable_path(item)
        for item in _bounded_array(
            parsed["private_references"],
            "Campaign Setting private references",
            256,
        )
    )
    if tuple(sorted(set(private_references))) != private_references:
        raise ValueError("Campaign Setting private references must be sorted and unique")
    facts: list[CampaignSettingKnowledgeFact] = []
    for raw in _bounded_array(
        parsed["facts"],
        "Campaign Setting knowledge facts",
        CAMPAIGN_SETTING_KNOWLEDGE_FACT_LIMIT,
    ):
        fact = _object(raw, "Campaign Setting knowledge fact")
        _exact_keys(
            fact,
            frozenset({"id", "fact", "known_by", "source_path"}),
            "Campaign Setting knowledge fact",
        )
        identity = validate_authority_identity(
            fact["id"], "Campaign Setting knowledge fact identity"
        )
        text = fact["fact"]
        if (
            not isinstance(text, str)
            or not text
            or text != text.strip()
            or len(text.encode("utf-8")) > 4_000
        ):
            raise ValueError("Campaign Setting knowledge fact text is invalid")
        known_by = _sorted_unique_identities(
            fact["known_by"],
            "Campaign Setting knowledge fact identities",
            128,
        )
        facts.append(
            CampaignSettingKnowledgeFact(
                identity=identity,
                fact=text,
                known_by=known_by,
                source_path=validate_portable_path(fact["source_path"]),
            )
        )
    identities = tuple(item.identity for item in facts)
    if tuple(sorted(set(identities))) != identities:
        raise ValueError("Campaign Setting knowledge facts must be sorted and unique")
    return CampaignSettingKnowledge(
        facts=tuple(facts),
        private_references=private_references,
        canonical_json=value.decode("utf-8"),
    )


def validate_campaign_setting_knowledge_links(
    manifest: PackageManifest,
    knowledge: CampaignSettingKnowledge,
) -> None:
    """Require every knowledge source and private reference to be package-owned."""

    files = {item.path for item in manifest.files}
    referenced = {
        *knowledge.private_references,
        *(item.source_path for item in knowledge.facts),
    }
    if not referenced <= files:
        raise ValueError("Campaign Setting knowledge references missing package content")


def parse_gameplay_support_bytes(value: bytes) -> GameplaySupport:
    """Parse bounded installed support claims using the stable Ruleset vocabulary."""

    if len(value) > GAMEPLAY_SUPPORT_BYTE_LIMIT:
        raise ValueError("Gameplay support declarations are too large")
    parsed = _object(parse_canonical_json_bytes(value), "Gameplay support declarations")
    validate_content_shape("gameplay_support", parsed, file=GAMEPLAY_SUPPORT_PATH)
    _exact_keys(
        parsed,
        frozenset({"format_id", "format_version", "records"}),
        "Gameplay support declarations",
    )
    if parsed["format_id"] != "sylvia-gm-gameplay-support" or parsed["format_version"] != 1:
        raise ValueError("Gameplay support declaration format is unsupported")
    raw_records = _bounded_array(
        parsed["records"],
        "Gameplay support records",
        GAMEPLAY_SUPPORT_RECORD_LIMIT,
    )
    records: list[GameplaySupportRecord] = []
    resolution_values = {
        "not_applicable",
        "engine_family",
        "engine_specialized",
        "hybrid",
        "gm_adjudicated",
        "unsupported",
    }
    for raw in raw_records:
        item = _object(raw, "Gameplay support record")
        _exact_keys(
            item,
            frozenset(
                {
                    "id",
                    "reference_availability",
                    "gameplay_resolution",
                    "mechanic_family",
                    "deterministic_part",
                    "gm_part",
                }
            ),
            "Gameplay support record",
        )
        identity = validate_authority_identity(item["id"], "Gameplay support identity")
        reference = item["reference_availability"]
        resolution = item["gameplay_resolution"]
        if reference not in {"included", "excluded"}:
            raise ValueError("Reference Availability is unsupported")
        if resolution not in resolution_values:
            raise ValueError("Gameplay Resolution is unsupported")
        family = item["mechanic_family"]
        deterministic_part = item["deterministic_part"]
        gm_part = item["gm_part"]
        for label, optional in (
            ("mechanic family", family),
            ("deterministic part", deterministic_part),
            ("GM part", gm_part),
        ):
            if optional is not None and (not isinstance(optional, str) or not optional.strip()):
                raise ValueError(f"Gameplay support {label} is invalid")
        if family is not None:
            validate_authority_identity(family, "Gameplay mechanic family")
        if resolution in {"engine_family", "hybrid", "gm_adjudicated"} and family is None:
            raise ValueError("Gameplay Resolution requires a mechanic family")
        if resolution == "hybrid" and (deterministic_part is None or gm_part is None):
            raise ValueError("Hybrid Gameplay Resolution must identify both owned portions")
        if resolution != "hybrid" and (deterministic_part is not None or gm_part is not None):
            raise ValueError("Only hybrid Gameplay Resolution declares split ownership")
        if reference == "excluded" and resolution in {
            "engine_family",
            "engine_specialized",
            "hybrid",
            "gm_adjudicated",
        }:
            raise ValueError("Excluded reference content cannot claim supported resolution")
        records.append(
            GameplaySupportRecord(
                identity=identity,
                reference_availability=cast(str, reference),
                gameplay_resolution=cast(str, resolution),
                mechanic_family=cast(str | None, family),
                deterministic_part=cast(str | None, deterministic_part),
                gm_part=cast(str | None, gm_part),
            )
        )
    identities = tuple(item.identity for item in records)
    if identities != tuple(sorted(set(identities))):
        raise ValueError("Gameplay support records must be sorted and unique")
    return GameplaySupport(tuple(records), value.decode("utf-8"))


def parse_content_declarations_bytes(value: bytes) -> PackageContentDeclarations:
    """Parse one bounded canonical immutable package content-declaration artifact."""

    if len(value) > CONTENT_DECLARATION_BYTE_LIMIT:
        raise ValueError("Package content declarations are too large")
    parsed = _object(
        parse_canonical_json_bytes(
            value,
            label="Package content declarations",
            maximum_depth=CONTENT_DECLARATION_DEPTH_LIMIT,
            maximum_nodes=CONTENT_DECLARATION_NODE_LIMIT,
            maximum_object_members=CONTENT_DECLARATION_OBJECT_MEMBER_LIMIT,
        ),
        "Package content declarations",
    )
    _exact_keys(
        parsed,
        frozenset(
            {
                "format_id",
                "format_version",
                "scope",
                "source_authorities",
                "inclusions",
                "exclusions",
            }
        ),
        "Package content declarations",
    )
    if (
        parsed["format_id"] != "sylvia-gm-package-content-declarations"
        or parsed["format_version"] != 1
    ):
        raise ValueError("Package content declaration format is unsupported")

    inclusion_values = _bounded_array(
        parsed["inclusions"],
        "Content inclusions",
        CONTENT_DECLARATION_RECORD_LIMIT,
    )
    exclusion_values = _bounded_array(
        parsed["exclusions"],
        "Content exclusions",
        CONTENT_DECLARATION_RECORD_LIMIT,
    )
    combined_count = len(inclusion_values) + len(exclusion_values)
    if combined_count > CONTENT_DECLARATION_RECORD_LIMIT:
        raise ValueError("Package content declaration record count is too large")

    scope = _object(parsed["scope"], "Content declaration scope")
    _exact_keys(
        scope,
        frozenset({"scope_id", "scope_kind", "claim_status", "source_boundary_refs"}),
        "Content declaration scope",
    )
    scope_id = validate_authority_identity(scope["scope_id"], "Content scope identity")
    scope_kind = validate_authority_identity(scope["scope_kind"], "Content scope kind")
    if scope_kind not in {"development_tracer", "bounded_package", "complete_ruleset"}:
        raise ValueError("Content scope kind is unsupported")
    claim_status = validate_authority_identity(scope["claim_status"], "Content claim status")
    if claim_status not in {"NOT_COMPLETE", "COMPLETE"}:
        raise ValueError("Content claim status is unsupported")
    boundary_refs: list[tuple[str, str]] = []
    for raw in _bounded_array(
        scope["source_boundary_refs"],
        "Content source boundary references",
        CONTENT_DECLARATION_BOUNDARY_REFERENCE_LIMIT,
    ):
        reference = _object(raw, "Content source boundary reference")
        _exact_keys(
            reference,
            frozenset({"artifact_id", "sha256"}),
            "Content source boundary reference",
        )
        boundary_refs.append(
            (
                validate_authority_identity(
                    reference["artifact_id"], "Content source boundary identity"
                ),
                validate_authority_identity(
                    reference["sha256"],
                    "Content source boundary SHA-256",
                    sha256=True,
                ),
            )
        )
    if tuple(sorted(set(boundary_refs))) != tuple(boundary_refs):
        raise ValueError("Content source boundary references must be sorted and unique")

    authorities: list[ContentSourceAuthority] = []
    for raw in _bounded_array(
        parsed["source_authorities"],
        "Content source authorities",
        CONTENT_DECLARATION_SOURCE_AUTHORITY_LIMIT,
    ):
        authority = _object(raw, "Content source authority")
        _exact_keys(
            authority,
            frozenset(
                {
                    "id",
                    "document_id",
                    "version",
                    "media_type",
                    "content_sha256",
                    "license_id",
                }
            ),
            "Content source authority",
        )
        authorities.append(
            ContentSourceAuthority(
                validate_authority_identity(authority["id"], "Content source identity"),
                validate_authority_identity(authority["document_id"], "Content source document"),
                validate_authority_identity(authority["version"], "Content source version"),
                validate_authority_identity(authority["media_type"], "Content source media type"),
                validate_authority_identity(
                    authority["content_sha256"],
                    "Content source SHA-256",
                    sha256=True,
                ),
                validate_authority_identity(authority["license_id"], "Content source license"),
            )
        )
    authority_ids = tuple(item.identity for item in authorities)
    if tuple(sorted(set(authority_ids))) != authority_ids or not authority_ids:
        raise ValueError("Content source authorities must be sorted, unique, and nonempty")
    authority_set = frozenset(authority_ids)

    inclusions: list[ContentInclusion] = []
    for raw in inclusion_values:
        inclusion = _object(raw, "Content inclusion")
        _exact_keys(
            inclusion,
            frozenset(
                {
                    "id",
                    "kind",
                    "family",
                    "disposition",
                    "classifications",
                    "sources",
                    "links",
                    "evidence",
                }
            ),
            "Content inclusion",
        )
        disposition = validate_authority_identity(
            inclusion["disposition"], "Content inclusion disposition"
        )
        if disposition not in {
            "included_mechanical",
            "included_nonmechanical",
            "included_compatibility",
        }:
            raise ValueError("Content inclusion disposition is unsupported")
        classifications = _sorted_unique_identities(
            inclusion["classifications"],
            "Content classifications",
            CONTENT_DECLARATION_CLASSIFICATION_LIMIT,
        )
        if not classifications or not set(classifications) <= {
            "authored_content",
            "official_licensed_content",
            "mechanically_derived_official",
            "sylvia_gm_implementation",
            "compatibility",
            "interpretation",
        }:
            raise ValueError("Content inclusion classifications are unsupported")
        sources = _content_sources(
            inclusion["sources"],
            authorities=authority_set,
            label="Content inclusion source",
        )
        if (
            set(classifications)
            & {
                "compatibility",
                "interpretation",
                "mechanically_derived_official",
                "official_licensed_content",
            }
            and not sources
        ):
            raise ValueError("Source-derived content inclusion lacks a source locator")
        evidence = _object(inclusion["evidence"], "Content inclusion evidence")
        _exact_keys(evidence, frozenset({"tests"}), "Content inclusion evidence")
        inclusions.append(
            ContentInclusion(
                validate_authority_identity(inclusion["id"], "Content inclusion identity"),
                validate_authority_identity(inclusion["kind"], "Content inclusion kind"),
                validate_authority_identity(inclusion["family"], "Content inclusion family"),
                disposition,
                classifications,
                sources,
                _content_links(inclusion["links"]),
                _sorted_unique_identities(
                    evidence["tests"],
                    "Content test evidence",
                    CONTENT_DECLARATION_EVIDENCE_LIMIT,
                ),
            )
        )

    exclusions: list[ContentExclusion] = []
    for raw in exclusion_values:
        exclusion = _object(raw, "Content exclusion")
        _exact_keys(
            exclusion,
            frozenset(
                {
                    "id",
                    "kind",
                    "family",
                    "reason",
                    "classifications",
                    "sources",
                    "within_declared_scope",
                }
            ),
            "Content exclusion",
        )
        reason = validate_authority_identity(exclusion["reason"], "Content exclusion reason")
        if reason not in {
            "unavailable",
            "unsupported",
            "intentionally_excluded",
            "superseded",
            "other_classified",
        }:
            raise ValueError("Content exclusion reason is unsupported")
        within_scope = exclusion["within_declared_scope"]
        if within_scope is not True:
            raise ValueError("Content exclusions must be inside the declared package scope")
        classifications = _sorted_unique_identities(
            exclusion["classifications"],
            "Content exclusion classifications",
            CONTENT_DECLARATION_CLASSIFICATION_LIMIT,
        )
        if not classifications or not set(classifications) <= {
            "compatibility",
            "interpretation",
            "legal_nonmechanical",
            "official_licensed_content",
            "unavailable_closed_content",
            "unsupported",
        }:
            raise ValueError("Content exclusion classifications are unsupported")
        sources = _content_sources(
            exclusion["sources"],
            authorities=authority_set,
            label="Content exclusion source",
        )
        if (
            set(classifications) & {"compatibility", "interpretation", "official_licensed_content"}
            and not sources
        ):
            raise ValueError("Source-derived content exclusion lacks a source locator")
        exclusions.append(
            ContentExclusion(
                validate_authority_identity(exclusion["id"], "Content exclusion identity"),
                validate_authority_identity(exclusion["kind"], "Content exclusion kind"),
                validate_authority_identity(exclusion["family"], "Content exclusion family"),
                reason,
                classifications,
                sources,
                within_scope,
            )
        )

    inclusion_ids = tuple(item.identity for item in inclusions)
    exclusion_ids = tuple(item.identity for item in exclusions)
    if tuple(sorted(set(inclusion_ids))) != inclusion_ids:
        raise ValueError("Content inclusions must be sorted and unique")
    if tuple(sorted(set(exclusion_ids))) != exclusion_ids:
        raise ValueError("Content exclusions must be sorted and unique")
    if set(inclusion_ids) & set(exclusion_ids):
        raise ValueError("Content inclusion and exclusion identities collide")
    if claim_status == "COMPLETE" and scope_kind != "complete_ruleset":
        raise ValueError("Only a complete-ruleset scope can claim completeness")
    if claim_status == "COMPLETE" and (not boundary_refs or combined_count == 0):
        raise ValueError("Complete-ruleset scope requires source boundaries and accounted content")
    return PackageContentDeclarations(
        scope_id,
        scope_kind,
        claim_status,
        tuple(boundary_refs),
        tuple(authorities),
        tuple(inclusions),
        tuple(exclusions),
        value.decode("utf-8"),
    )


def validate_content_declaration_links(
    manifest: PackageManifest,
    declarations: PackageContentDeclarations,
) -> None:
    """Require every format-3 local runtime surface to be inclusion-linked exactly."""

    links = tuple(item.links for item in declarations.inclusions)
    linked_artifacts = {value for item in links for value in item.artifacts}
    linked_state_namespaces = {value for item in links for value in item.state_namespaces}
    linked_schemas = {value for item in links for value in item.schemas}
    linked_capabilities = {value for item in links for value in item.capabilities}
    linked_tools = {value for item in links for value in item.tools}
    linked_prompts = {value for item in links for value in item.prompt_fragments}
    linked_ui = {value for item in links for value in item.ui_surfaces}
    local_artifacts = {item.path for item in manifest.files}
    local_schemas = {item.schema_id for item in manifest.participant_artifacts}
    local_capabilities = set(manifest.capabilities)
    local_tools = {item.name for item in manifest.tools}
    local_prompts = {item.identity for item in manifest.prompt_fragments}
    local_ui = {item.identity for item in manifest.ui_surfaces}
    namespace_prefix = f"{manifest.package_id}."
    if any(not value.startswith(namespace_prefix) for value in linked_state_namespaces):
        raise ValueError("Content declaration links a foreign package state namespace")
    for linked, local, label in (
        (linked_artifacts, local_artifacts, "artifact"),
        (linked_schemas, local_schemas, "schema"),
        (linked_capabilities, local_capabilities, "capability"),
        (linked_tools, local_tools, "tool"),
        (linked_prompts, local_prompts, "prompt fragment"),
        (linked_ui, local_ui, "UI surface"),
    ):
        if not linked <= local:
            detail = json.dumps(sorted(linked - local)[:8])
            raise ValueError(f"Content declaration links an unknown local {label}: {detail}")
    for local, linked, label in (
        (
            local_artifacts - {manifest.content_declarations_path},
            linked_artifacts,
            "artifact",
        ),
        (local_schemas, linked_schemas, "schema"),
        (local_capabilities, linked_capabilities, "capability"),
        (local_tools, linked_tools, "tool"),
        (local_prompts, linked_prompts, "prompt fragment"),
        (local_ui, linked_ui, "UI surface"),
    ):
        if not local <= linked:
            detail = json.dumps(sorted(local - linked)[:8])
            raise ValueError(f"Package {label} is not linked to a content inclusion: {detail}")


_UI_RENDERERS = frozenset(
    {
        "party",
        "encounter",
        "inventory",
        "objectives",
        "unit_list",
        "key_value",
        "text",
        "tactical_grid",
        "coordinate_map",
        "network_graph",
        "piece_table",
    }
)
_UI_PLACEMENTS = frozenset({"docked", "floating", "window"})
TACTICAL_UI_DYNAMIC_SELECTIONS = frozenset(
    {
        "actor",
        "zone",
        "device",
        "destination",
        "path",
        "target",
        "aim_point",
        "area",
        "orientation",
        "declared_action",
    }
)
NETWORK_UI_DYNAMIC_SELECTIONS = frozenset(
    {"actor", "node", "link", "route_step", "remote_function", "declared_action"}
)
UI_DYNAMIC_SELECTIONS = TACTICAL_UI_DYNAMIC_SELECTIONS | NETWORK_UI_DYNAMIC_SELECTIONS
_UI_RENDERER_SELECTIONS = {
    "piece_table": frozenset({"piece"}),
    "party": frozenset({"actor"}),
    "inventory": frozenset({"actor", "declared_action"}),
    "unit_list": frozenset({"actor"}),
    "coordinate_map": frozenset({"actor", "target", "aim_point", "declared_action"}),
    "tactical_grid": TACTICAL_UI_DYNAMIC_SELECTIONS,
    "network_graph": NETWORK_UI_DYNAMIC_SELECTIONS,
}


def _surface_source(value: object, *, depth: int = 0) -> SafePayload:
    """Validate one bounded owner-projection query declared by a UI surface."""

    if depth > 3:
        raise ValueError("UI surface source nesting is too deep")
    source = _object(value, "UI surface source")
    kind = source.get("kind")
    if kind == "literal":
        _exact_keys(source, frozenset({"kind", "value"}), "UI literal source")
        owned_payload(source["value"])
    elif kind == "state":
        _exact_keys(source, frozenset({"kind", "namespace", "key"}), "UI state source")
        validate_authority_identity(source["namespace"], "UI state namespace")
        validate_authority_identity(source["key"], "UI state key")
    elif kind == "participants":
        _exact_keys(source, frozenset({"kind", "kinds"}), "UI participant source")
        kinds = tuple(cast(str, item) for item in _array(source["kinds"], "UI participant kinds"))
        if not kinds or len(kinds) > 16 or any(not isinstance(item, str) for item in kinds):
            raise ValueError("UI participant source kinds are invalid")
        for item in kinds:
            validate_authority_identity(item, "UI participant kind")
        if len(set(kinds)) != len(kinds):
            raise ValueError("UI participant source kinds must be unique")
    elif kind == "campaign_plan":
        _exact_keys(source, frozenset({"kind", "path"}), "UI Campaign Plan source")
        path = tuple(cast(str, item) for item in _array(source["path"], "UI Campaign Plan path"))
        if not path or len(path) > 16 or any(not isinstance(item, str) for item in path):
            raise ValueError("UI Campaign Plan path is invalid")
        for item in path:
            validate_authority_identity(item, "UI Campaign Plan path component")
    elif kind == "object":
        _exact_keys(source, frozenset({"kind", "fields"}), "UI object source")
        fields = _object(source["fields"], "UI object source fields")
        if not fields or len(fields) > 16:
            raise ValueError("UI object source field count is invalid")
        for name, field_source in fields.items():
            validate_authority_identity(name, "UI object source field")
            _surface_source(field_source, depth=depth + 1)
    else:
        raise ValueError("UI surface source kind is unsupported")
    return owned_payload(source)


def _ui_surface(
    value: object,
    capabilities: tuple[str, ...],
    tools: tuple[str, ...],
) -> SurfaceDeclaration:
    item = _object(value, "UI surface")
    validate_content_shape("panel_declaration", item, file="package.json:ui_surfaces")
    _exact_keys(
        item,
        frozenset(
            {
                "id",
                "capability",
                "title",
                "icon",
                "renderer",
                "source",
                "group",
                "order",
                "default_size",
                "minimum_size",
                "default_placement",
                "allowed_placements",
                "actions",
            }
        ),
        "UI surface",
    )
    identity = validate_authority_identity(item["id"], "UI surface identity")
    capability = validate_authority_identity(item["capability"], "UI surface capability")
    if capability not in capabilities:
        raise ValueError("Package surface requires one declared capability")
    title = _safe_small_label(item["title"], "UI surface title")
    icon = _safe_small_label(item["icon"], "UI surface icon")
    renderer = cast(str, item["renderer"])
    if renderer not in _UI_RENDERERS:
        raise ValueError("UI surface renderer is unsupported")
    group_value = item["group"]
    group = None if group_value is None else validate_authority_identity(group_value, "UI group")
    order = item["order"]
    default_size = item["default_size"]
    minimum_size = item["minimum_size"]
    if any(
        not isinstance(number, int) or isinstance(number, bool)
        for number in (order, default_size, minimum_size)
    ):
        raise ValueError("UI surface sizes and order must be integers")
    order = cast(int, order)
    default_size = cast(int, default_size)
    minimum_size = cast(int, minimum_size)
    if not 0 <= order <= 10_000 or not 96 <= minimum_size <= default_size <= 2_000:
        raise ValueError("UI surface sizes or order are outside supported bounds")
    default_placement = cast(str, item["default_placement"])
    placements = tuple(
        cast(str, placement)
        for placement in _array(item["allowed_placements"], "UI allowed placements")
    )
    if (
        not placements
        or len(set(placements)) != len(placements)
        or any(placement not in _UI_PLACEMENTS for placement in placements)
        or default_placement not in placements
    ):
        raise ValueError("UI surface placement declaration is invalid")
    actions: list[SafePayload] = []
    for action_value in _array(item["actions"], "UI surface actions"):
        action = _object(action_value, "UI surface action")
        action_fields = frozenset({"id", "label", "intent", "params"})
        if "bindings" in action:
            action_fields |= {"bindings"}
        if "actor_reference" in action:
            action_fields |= {"actor_reference"}
            if (renderer != 'coordinate_map' or not isinstance(action.get('bindings'), Mapping)
                    or 'actor' not in action['bindings'].values()):
                raise ValueError('Actor references require a coordinate map actor binding')
        if "target_reference" in action:
            action_fields |= {"target_reference"}
            if (renderer != 'coordinate_map' or not isinstance(action.get('bindings'), Mapping)
                    or 'target' not in action['bindings'].values()
                    or action.get('target_reference') != action.get('actor_reference')):
                raise ValueError('Target references require the coordinate map actor reference')
        if "spatial_preview" in action:
            action_fields |= {"spatial_preview"}
            sources = set(action.get('bindings', {}).values())
            if (renderer not in {'coordinate_map', 'tactical_grid'} or 'actor' not in sources
                    or not sources.intersection({'target', 'aim_point'})):
                raise ValueError('Segment previews require declared spatial endpoints')
        _exact_keys(action, action_fields, "UI surface action")
        validate_authority_identity(action["id"], "UI surface action identity")
        _safe_small_label(action["label"], "UI surface action label")
        intent = validate_authority_identity(action["intent"], "UI surface action intent")
        if intent not in tools:
            raise ValueError("UI surface action references an undeclared package tool")
        params = _object(action["params"], "UI surface action parameters")
        if "bindings" in action:
            if renderer not in _UI_RENDERER_SELECTIONS:
                raise ValueError("UI selection bindings require an interactive renderer")
            bindings = _object(action["bindings"], "UI surface selection bindings")
            renderer_selections = _UI_RENDERER_SELECTIONS[renderer]
            if (
                not bindings
                or len(bindings) > 16
                or set(bindings) & set(params)
                or any(
                    not isinstance(source, str) or source not in renderer_selections
                    for source in bindings.values()
                )
                or (len(set(bindings.values())) != len(bindings)
                    and not (renderer == 'coordinate_map' and action.get('actor_reference')
                             and all(source == 'actor' or list(bindings.values()).count(source) == 1
                                     for source in bindings.values())))
            ):
                raise ValueError("UI surface selection binding is invalid")
            for parameter in bindings:
                validate_authority_identity(parameter, "UI surface bound parameter")
        actions.append(owned_payload(action))
    if len(actions) > 16:
        raise ValueError("UI surface declares too many actions")
    actor_references = {action.get('actor_reference') for action in actions
                        if isinstance(action.get('bindings'), Mapping)
                        and 'actor' in action['bindings'].values()}
    if len(actor_references) > 1:
        raise ValueError('Coordinate map actions must share one actor reference')
    return SurfaceDeclaration(
        identity=identity,
        capability=capability,
        title=title,
        icon=icon,
        renderer=renderer,
        source=_surface_source(item["source"]),
        group=group,
        order=order,
        default_size=default_size,
        minimum_size=minimum_size,
        default_placement=default_placement,
        allowed_placements=placements,
        actions=tuple(actions),
    )


def manifest_from_record(
    record_value: object, *, format_version: int | None = None
) -> PackageManifest:
    record = _object(record_value, "Package record")
    if format_version is None:
        if "content_kind" in record:
            format_version = 4
        elif "content_declarations" in record:
            format_version = 3
        elif "participant_artifacts" in record:
            format_version = 2
        else:
            format_version = 1
    if format_version not in (1, 2, 3, 4):
        raise ValueError("Package record format is unsupported")
    base_keys = frozenset(
        {
            "id",
            "version",
            "role",
            "metadata",
            "provenance",
            "compatibility",
            "minimum_model_capability_tier",
            "dependencies",
            "capabilities",
            "tools",
            "prompt_fragments",
            "ui_surfaces",
            "files",
            "content_sha256",
        }
    )
    if format_version >= 3:
        expected_keys = base_keys | {"participant_artifacts", "content_declarations"}
        if format_version == 4:
            expected_keys |= {"content_kind"}
            if "state_schemas" in record:
                expected_keys |= {"state_schemas"}
    elif format_version == 2 and "participant_artifacts" in record:
        expected_keys = base_keys | {"participant_artifacts"}
    else:
        expected_keys = base_keys
    _exact_keys(
        record,
        expected_keys,
        "Package record",
    )
    package_id = validate_authority_identity(record["id"], "Package identity")
    version = validate_authority_identity(record["version"], "Package version")
    content_sha = validate_authority_identity(
        record["content_sha256"], "Package content SHA-256", sha256=True
    )
    if content_root_for_record(record) != content_sha:
        raise ValueError("Package content-root claim does not match its canonical record")
    role_text = validate_authority_identity(record["role"], "Package role")
    role = PackageRole(role_text)
    if format_version == 4:
        content_kind = record["content_kind"]
        if content_kind not in ("ruleset", "campaign_setting", "support") or (
            (role is PackageRole.PRIMARY) != (content_kind == "ruleset")
        ):
            raise ValueError("Package content kind does not match its role")

    metadata = _object(record["metadata"], "Package metadata")
    _exact_keys(metadata, frozenset({"label"}), "Package metadata")
    _safe_small_label(metadata["label"], "Package label")
    provenance = _object(record["provenance"], "Package provenance")
    _exact_keys(provenance, frozenset({"publisher", "source_sha256"}), "Package provenance")
    _safe_small_label(provenance["publisher"], "Package publisher")
    validate_authority_identity(provenance["source_sha256"], "Package source SHA-256", sha256=True)
    compatibility = _object(record["compatibility"], "Package compatibility")
    _exact_keys(
        compatibility,
        frozenset({"host_contract", "runtime_format", "requires"}),
        "Package compatibility",
    )
    if compatibility["host_contract"] != PACKAGE_HOST_CONTRACT:
        raise ValueError("Package host contract is unsupported")
    if compatibility["runtime_format"] != PACKAGE_RUNTIME_FORMAT:
        raise ValueError("Package runtime format is unsupported")
    required_features = _array(compatibility["requires"], "Package engine requirements")
    if any(not isinstance(item, str) or not item for item in required_features) or len(
        set(cast(Sequence[str], required_features))
    ) != len(required_features):
        raise ValueError("Package engine requirements are invalid")
    unsupported_features = set(cast(Sequence[str], required_features)) - SUPPORTED_ENGINE_FEATURES
    if unsupported_features:
        raise ValueError(
            f"Package engine feature is unsupported: {sorted(unsupported_features)[0]}"
        )
    state_schemas: tuple[StateSchema, ...] = ()
    if "state_schemas" in record:
        if role is not PackageRole.PRIMARY or STARTING_STATE_FEATURE not in required_features:
            raise ValueError("Starting-state schemas require a Ruleset and declared host support")
        state_schemas = parse_state_schemas(record["state_schemas"])
        if any(item.required for item in state_schemas) and (
            REQUIRED_STARTING_STATE_FEATURE not in required_features
        ):
            raise ValueError("Required starting-state keys need declared host support")
    minimum_model_capability_tier = validate_capability_tier_id(
        record["minimum_model_capability_tier"]
    )

    dependencies: list[ExactPackageDependency] = []
    for value in _array(record["dependencies"], "Package dependencies"):
        item = _object(value, "Package dependency")
        _exact_keys(
            item,
            frozenset({"namespace", "kind", "id", "version", "content_sha256"}),
            "Package dependency",
        )
        dependencies.append(
            ExactPackageDependency(
                namespace=cast(str, item["namespace"]),
                kind=cast(str, item["kind"]),
                package_id=cast(str, item["id"]),
                version=cast(str, item["version"]),
                content_sha256=cast(str, item["content_sha256"]),
            )
        )
    if len({item.namespace for item in dependencies}) != len(dependencies):
        raise ValueError("Dependency namespaces must be unique within a package")

    capabilities = tuple(
        cast(str, value) for value in _array(record["capabilities"], "Capabilities")
    )
    if any(not isinstance(value, str) for value in capabilities):
        raise ValueError("Capabilities must be strings")
    for capability in capabilities:
        validate_authority_identity(capability, "Package capability")
    if tuple(sorted(set(capabilities))) != capabilities:
        raise ValueError("Capabilities must be sorted and unique")

    tools: list[ToolDeclaration] = []
    for value in _array(record["tools"], "Package tools"):
        item = _object(value, "Package tool")
        _exact_keys(
            item,
            frozenset(
                {
                    "name",
                    "capability",
                    "input_schema",
                    "state_predicates",
                    "turn_statuses",
                    "writers",
                }
            ),
            "Package tool",
        )
        name = validate_authority_identity(item["name"], "Package tool name")
        capability = validate_authority_identity(item["capability"], "Tool capability")
        if capability not in capabilities:
            raise ValueError("Every tool requires one declared package capability")
        predicates: list[StatePredicate] = []
        for predicate_value in _array(item["state_predicates"], "State predicates"):
            predicate = _object(predicate_value, "State predicate")
            if frozenset(predicate) not in {
                frozenset({"namespace", "key", "equals"}),
                frozenset({"namespace", "key", "equals", "negated"}),
            }:
                raise ValueError("State predicate fields do not match the portable format")
            negated = predicate.get("negated", False)
            if not isinstance(negated, bool):
                raise ValueError("State predicate negated flag must be boolean")
            predicates.append(
                StatePredicate(
                    namespace=validate_authority_identity(
                        predicate["namespace"], "State namespace"
                    ),
                    key=validate_authority_identity(predicate["key"], "State key"),
                    equals=owned_json_value(predicate["equals"]),
                    negated=negated,
                )
            )
        turns = tuple(
            cast(str, item_value) for item_value in _array(item["turn_statuses"], "TURN statuses")
        )
        writers = tuple(cast(str, item_value) for item_value in _array(item["writers"], "Writers"))
        if any(value not in ("open", "completed") for value in turns):
            raise ValueError("Tool TURN predicate is unsupported")
        if any(value not in ("builder", "dm") for value in writers):
            raise ValueError("Tool writer predicate is unsupported")
        _validate_character_update_declaration(owned_payload(item["input_schema"]), package_id)
        tools.append(
            ToolDeclaration(
                name=name,
                capability=capability,
                input_schema=owned_payload(item["input_schema"]),
                state_predicates=tuple(predicates),
                turn_statuses=turns,
                writers=writers,
            )
        )
    if len({tool.name for tool in tools}) != len(tools):
        raise ValueError("Package tool names must be unique")

    participant_artifacts: list[ParticipantArtifactDeclaration] = []
    for value in _array(record.get("participant_artifacts", ()), "Participant artifacts"):
        item = _object(value, "Participant artifact")
        state_fields = frozenset({"entity_state_schema", "initial_state_calculations"})
        has_state_contract = bool(set(item) & state_fields)
        validation = (
            _object(item["initial_state_validation"], "Character starting-state validation")
            if "initial_state_validation" in item else None
        )
        _exact_keys(
            item,
            frozenset({"kind", "schema_id", "schema_sha256", "schema"})
            | (state_fields if has_state_contract else frozenset())
            | ({"initial_state_validation"} if validation is not None else set()),
            "Participant artifact",
        )
        schema = _object(item["schema"], "Participant artifact schema")
        entity_state_schema = (
            _object(item["entity_state_schema"], "Character entity-state schema")
            if has_state_contract else None
        )
        calculations = tuple(
            owned_payload(_object(step, "Character starting-state calculation"))
            for step in _array(
                item.get("initial_state_calculations", ()), "Character starting-state calculations"
            )
        )
        schema_id = validate_authority_identity(
            item["schema_id"], "Participant artifact schema identity"
        )
        if schema.get("$id") != schema_id:
            raise ValueError("Participant artifact schema identity does not match its schema")
        schema_sha256 = validate_authority_identity(
            item["schema_sha256"], "Participant artifact schema SHA-256", sha256=True
        )
        if participant_schema_sha256(
            schema, entity_state_schema=entity_state_schema,
            initial_state_calculations=calculations,
            initial_state_validation=validation,
        ) != schema_sha256:
            raise ValueError("Participant artifact schema hash does not match canonical schema")
        participant_artifacts.append(
            ParticipantArtifactDeclaration(
                kind=cast(str, item["kind"]),
                schema_id=schema_id,
                schema_sha256=schema_sha256,
                schema=owned_payload(schema),
                entity_state_schema=(
                    None if entity_state_schema is None else owned_payload(entity_state_schema)
                ),
                initial_state_calculations=calculations,
                initial_state_validation=validation,
            )
        )
    if len({item.kind for item in participant_artifacts}) != len(participant_artifacts):
        raise ValueError("Participant artifact kinds must be unique")

    content_declarations_path: str | None = None
    if format_version >= 3:
        content_declarations_path = validate_portable_path(record["content_declarations"])

    def surfaces(field: str) -> tuple[SurfaceDeclaration, ...]:
        values: list[SurfaceDeclaration] = []
        for raw in _array(record[field], field):
            if field == "ui_surfaces":
                values.append(
                    _ui_surface(
                        raw,
                        capabilities,
                        tuple(tool.name for tool in tools),
                    )
                )
                continue
            item = _object(raw, field)
            _exact_keys(item, frozenset({"id", "capability"}), field)
            identity = validate_authority_identity(item["id"], f"{field} identity")
            capability = validate_authority_identity(item["capability"], f"{field} capability")
            if capability not in capabilities:
                raise ValueError("Package surface requires one declared capability")
            values.append(SurfaceDeclaration(identity, capability))
        if len({value.identity for value in values}) != len(values):
            raise ValueError("Package surface identities must be unique")
        return tuple(values)

    files: list[PackageFile] = []
    for value in _array(record["files"], "Package files"):
        item = _object(value, "Package file")
        _exact_keys(item, frozenset({"path", "bytes", "sha256"}), "Package file")
        if not isinstance(item["bytes"], int) or isinstance(item["bytes"], bool):
            raise ValueError("Package file byte count must be an integer")
        files.append(PackageFile(cast(str, item["path"]), item["bytes"], cast(str, item["sha256"])))
    if tuple(sorted(file.path for file in files)) != tuple(file.path for file in files):
        raise ValueError("Package file index must be sorted")
    if len({file.path.casefold() for file in files}) != len(files):
        raise ValueError("Package file paths collide")
    if content_declarations_path is not None and content_declarations_path not in {
        file.path for file in files
    }:
        raise ValueError("Package content declarations are not indexed as a package file")

    ui_surfaces = surfaces("ui_surfaces")
    _validate_inventory_targets(tools, ui_surfaces)
    return PackageManifest(
        package_id=package_id,
        version=version,
        role=role,
        content_sha256=content_sha,
        canonical_json=canonical_json_bytes(record).decode("utf-8"),
        metadata=owned_payload(metadata),
        provenance=owned_payload(provenance),
        compatibility=owned_payload(compatibility),
        minimum_model_capability_tier=minimum_model_capability_tier,
        dependencies=tuple(dependencies),
        capabilities=capabilities,
        tools=tuple(tools),
        prompt_fragments=surfaces("prompt_fragments"),
        ui_surfaces=ui_surfaces,
        files=tuple(files),
        format_version=format_version,
        participant_artifacts=tuple(participant_artifacts),
        content_declarations_path=content_declarations_path,
        state_schemas=state_schemas,
    )


def resolve_package_graph(nodes: Iterable[PackageGraphNode]) -> tuple[tuple[str, str, str], ...]:
    """Resolve current Setting roots and legacy Ruleset roots using exact dependency links."""
    values = tuple(nodes)
    by_identity = {item.identity: item for item in values}
    if len(by_identity) != len(values):
        raise ValueError("Package identities in one archive must be unique")
    primaries = tuple(item for item in values if item.role is PackageRole.PRIMARY)
    if len(primaries) != 1:
        raise ValueError("An exact closure requires exactly one primary")
    primary = primaries[0]
    modern = any(item.format_version == 4 for item in values)
    root = primary
    if modern:
        if any(item.format_version != 4 for item in values):
            raise ValueError("Snapshot packages require one archive format")
        kinds = {item.identity: item.content_kind for item in values}
        settings = tuple(item for item in values if kinds[item.identity] == "campaign_setting")
        if len(settings) > 1:
            raise ValueError("A snapshot contains at most one Campaign Setting")
        for item in values:
            for dependency in item.dependencies:
                target = by_identity.get((
                    dependency.package_id, dependency.version, dependency.content_sha256,
                ))
                if item is primary and (
                    dependency.kind == "campaign_setting"
                    or dependency.namespace == "campaign_setting"
                    or (target is not None and kinds[target.identity] == "campaign_setting")
                ):
                    raise ValueError("A Ruleset must not declare a Campaign Setting")
                if dependency.kind == "ruleset_snapshot" and (
                    kinds[item.identity] != "campaign_setting"
                    or dependency.namespace != "ruleset_snapshot"
                    or target is not primary
                ):
                    raise ValueError("A Setting must contain its exact Ruleset snapshot")
        if settings:
            root = settings[0]
            if sum(dep.kind == "ruleset_snapshot" for dep in root.dependencies) != 1:
                raise ValueError("A Setting requires exactly one Ruleset snapshot")

    ordered: list[PackageGraphNode] = []
    visiting: set[tuple[str, str, str]] = set()
    visited: set[tuple[str, str, str]] = set()
    logical: dict[str, tuple[str, str, str]] = {}

    def visit(item: PackageGraphNode) -> None:
        identity = item.identity
        if identity in visiting:
            raise ValueError("Package closure contains a cycle")
        previous = logical.get(identity[0])
        if previous is not None and previous != identity:
            raise ValueError("A logical package has conflicting exact identities")
        logical[identity[0]] = identity
        if identity in visited:
            return
        visiting.add(identity)
        ordered.append(item)
        for dependency in item.dependencies:
            target_identity = (
                dependency.package_id,
                dependency.version,
                dependency.content_sha256,
            )
            target = by_identity.get(target_identity)
            if target is None or (target.role is PackageRole.PRIMARY and not (
                modern and item is root and dependency.kind == "ruleset_snapshot"
            )):
                raise ValueError("Exact dependency is absent or attempts peer-primary merge")
            visit(target)
        visiting.remove(identity)
        visited.add(identity)

    visit(root)
    if {item.identity for item in ordered} != {item.identity for item in values}:
        raise ValueError("Package archive contains undeclared closure members")
    if modern:
        # Execution still loads the one effective Ruleset first. Ownership follows the
        # snapshot links, independently of this stable runtime/storage ordering.
        ordered = [primary, *(item for item in ordered if item is not primary)]
    return tuple(item.identity for item in ordered)


def resolve_exact_closure(manifests: Iterable[PackageManifest]) -> PackageLock:
    """Build the execution lock from the same graph used for portable campaign validation."""
    values = tuple(manifests)
    identities = resolve_package_graph(PackageGraphNode.from_manifest(item) for item in values)
    by_identity = {item.identity: item for item in values}
    ordered = tuple(by_identity[identity] for identity in identities)
    return PackageLock(
        entries=tuple(
            PackageLockEntry(
                package_id=item.package_id,
                version=item.version,
                sha256=item.content_sha256,
                role=item.role,
                load_order=order,
                dependencies=tuple(
                    PackageDependency(
                        package_id=dependency.package_id,
                        version=dependency.version,
                        sha256=dependency.content_sha256,
                        kind=dependency.kind,
                        namespace=dependency.namespace,
                    )
                    for dependency in item.dependencies
                ),
                capabilities=item.capabilities,
            )
            for order, item in enumerate(ordered)
        )
    )


def campaign_setting_entries(lock: PackageLock) -> tuple[PackageLockEntry, ...]:
    """Find contained Settings in current snapshots and exact legacy campaign locks."""
    primary = next((item for item in lock.entries if item.role is PackageRole.PRIMARY), None)
    if primary is None:
        return ()
    primary_identity = (primary.package_id, primary.version, primary.sha256)
    snapshots = tuple(
        item for item in lock.entries
        if any(
            dep.kind == "ruleset_snapshot"
            and (dep.package_id, dep.version, dep.sha256) == primary_identity
            for dep in item.dependencies
        )
    )
    if snapshots:
        return snapshots
    legacy = {
        (dep.package_id, dep.version, dep.sha256) for dep in primary.dependencies
        if dep.kind == "campaign_setting" or dep.namespace == "campaign_setting"
    }
    return tuple(
        item for item in lock.entries
        if (item.package_id, item.version, item.sha256) in legacy
    )


def manifest_index(
    manifests: Iterable[PackageManifest],
) -> Mapping[tuple[str, str, str], PackageManifest]:
    return cast(
        Mapping[tuple[str, str, str], PackageManifest],
        {manifest.identity: manifest for manifest in manifests},
    )
