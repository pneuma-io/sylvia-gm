"""Owner-invoked F3 package workflows; this module creates no authority or connection."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import BinaryIO, Protocol

from sylvia_gm.application.initial_character_state import validate_initial_character_contract
from sylvia_gm.application.package_runtime import (
    PACKAGE_RUNTIME_BYTE_LIMIT,
    PACKAGE_RUNTIME_PATH,
    PackageRuntimeError,
    validate_package_runtime,
)
from sylvia_gm.application.setting_character_requirements import (
    CHARACTER_REQUIREMENTS_PATH,
    parse_setting_character_requirements,
)
from sylvia_gm.domain.authority import JsonValue, SafePayload, owned_payload, thaw_json_value
from sylvia_gm.domain.errors import CommandRejected, RulesetDiagnostic
from sylvia_gm.domain.package_library import (
    PackageCandidate,
    PackageSurfaceProjection,
    StatePredicate,
    SurfaceDeclaration,
    ToolDeclaration,
    campaign_setting_entries,
    canonical_json_bytes,
)
from sylvia_gm.infrastructure.package_archive import (
    AdmittedPackageArchive,
    admit_package_archive,
)


class _PackageStorePort(Protocol):
    def stage_package_archive(self, admitted: AdmittedPackageArchive, install_id: str) -> None: ...

    def publish_package_archive(
        self, admitted: AdmittedPackageArchive, install_id: str
    ) -> PackageCandidate: ...


MigrationFunction = Callable[[Mapping[str, JsonValue]], Mapping[str, JsonValue]]


def validate_admitted_package_runtimes(admitted: AdmittedPackageArchive) -> None:
    """Check executable declarations without acquiring publication or campaign authority."""

    setting_identities = {
        (entry.package_id, entry.version, entry.sha256)
        for entry in campaign_setting_entries(admitted.package_lock)
    }
    for archive_file in admitted.files:
        if archive_file.file.path != CHARACTER_REQUIREMENTS_PATH:
            continue
        if archive_file.manifest.identity not in setting_identities:
            raise CommandRejected("Character world requirements belong in a Campaign Setting")
        if archive_file.file.byte_count > 256 * 1024:
            raise CommandRejected("Setting character requirements are oversized")
        parse_setting_character_requirements(
            b"".join(admitted.iter_file_chunks(archive_file)).decode("utf-8")
        )
    for manifest in admitted.manifests:
        for declaration in manifest.participant_artifacts:
            if declaration.entity_state_schema is not None:
                validate_initial_character_contract(
                    declaration.entity_state_schema, declaration.initial_state_calculations,
                    declaration.initial_state_validation
                )
        runtime_file = next(
            (
                archive_file
                for archive_file in admitted.files
                if archive_file.manifest.identity == manifest.identity
                and archive_file.file.path == PACKAGE_RUNTIME_PATH
            ),
            None,
        )
        if runtime_file is None:
            continue
        if runtime_file.file.byte_count > PACKAGE_RUNTIME_BYTE_LIMIT:
            raise CommandRejected(
                "Package runtime admission failed",
                code="package_runtime_invalid",
            )
        runtime_bytes = b"".join(admitted.iter_file_chunks(runtime_file))
        try:
            validate_package_runtime(
                package_id=manifest.package_id,
                declarations=manifest.tools,
                runtime_bytes=runtime_bytes,
            )
        except PackageRuntimeError as error:
            raise CommandRejected(
                "Package runtime admission failed",
                code="package_runtime_invalid",
                diagnostics=error.diagnostics or (
                    RulesetDiagnostic(
                        code="package_runtime_invalid",
                        phase="runtime",
                        message=str(error),
                        file=PACKAGE_RUNTIME_PATH,
                        package_id=manifest.package_id,
                        guidance="Correct the generated runtime or its public action declaration.",
                    ),
                ),
            ) from error


def install_package_archive(
    store: _PackageStorePort,
    source: BinaryIO,
    *,
    install_id: str,
) -> PackageCandidate:
    """Bounded admission and bytes-first staging before one authority publication."""

    with admit_package_archive(source) as admitted:
        validate_admitted_package_runtimes(admitted)
        store.stage_package_archive(admitted, install_id)
        return store.publish_package_archive(admitted, install_id)


def run_migration_outside_sqlite(
    state: Mapping[str, JsonValue],
    migrate: MigrationFunction,
) -> SafePayload:
    """Provide an owned snapshot to deterministic code and recapture its result."""

    basis = owned_payload(state)
    result = migrate(basis)
    if not isinstance(result, Mapping):
        raise CommandRejected(
            "Package migration must return canonical state", code="migration_invalid"
        )
    try:
        captured = owned_payload(result)
    except (TypeError, ValueError) as error:
        raise CommandRejected(
            "Package migration returned unsafe state", code="migration_invalid"
        ) from error
    if any(not isinstance(key, str) or "." not in key for key in captured):
        raise CommandRejected(
            "Package migration returned invalid state identities", code="migration_invalid"
        )
    return captured


def declaration_admitted(
    declaration: ToolDeclaration,
    *,
    state: Mapping[str, JsonValue],
    turn_status: str,
    writer: str,
    declared_capabilities: frozenset[str],
) -> bool:
    if declaration.capability not in declared_capabilities:
        return False
    if turn_status not in declaration.turn_statuses or writer not in declaration.writers:
        return False
    return state_predicates_match(declaration.state_predicates, state)


def state_predicates_match(
    predicates: Sequence[StatePredicate], state: Mapping[str, JsonValue]
) -> bool:
    """Evaluate one Ruleset-authored predicate set against authoritative state."""

    return all(_predicate_matches(predicate, state) for predicate in predicates)


def _predicate_matches(predicate: StatePredicate, state: Mapping[str, JsonValue]) -> bool:
    """Resolve a predicate key through one namespaced state root and optional nested fields."""

    missing = object()
    exact_key = f"{predicate.namespace}.{predicate.key}"
    if exact_key in state:
        value: object = state[exact_key]
    else:
        root_key, separator, nested_path = predicate.key.partition(".")
        value = state.get(f"{predicate.namespace}.{root_key}", missing)
        if separator:
            for field in nested_path.split("."):
                if not isinstance(value, Mapping) or field not in value:
                    value = missing
                    break
                value = value[field]
    matches = value == predicate.equals
    return not matches if predicate.negated else matches


def project_surfaces(
    prompts: tuple[SurfaceDeclaration, ...],
    ui: tuple[SurfaceDeclaration, ...],
    *,
    active_capabilities: frozenset[str],
) -> PackageSurfaceProjection:
    def player_safe_source(source: object) -> bool:
        """Exclude private Campaign Plan reads from every player-facing projection."""

        if not isinstance(source, Mapping):
            return False
        kind = source.get("kind")
        if kind == "object":
            fields = source.get("fields")
            return isinstance(fields, Mapping) and all(
                player_safe_source(child) for child in fields.values()
            )
        return kind in {"state", "participants", "literal"}

    active_ui = tuple(
        item
        for item in ui
        if item.capability in active_capabilities and player_safe_source(item.source)
    )
    return PackageSurfaceProjection(
        prompt_fragment_ids=tuple(
            item.identity for item in prompts if item.capability in active_capabilities
        ),
        ui_surface_ids=tuple(item.identity for item in active_ui),
        ui_surfaces=tuple(
            owned_payload(
                {
                    "id": item.identity,
                    "title": item.title,
                    "icon": item.icon,
                    "renderer": item.renderer,
                    "source": thaw_json_value(item.source),
                    "group": item.group,
                    "order": item.order,
                    "default_size": item.default_size,
                    "minimum_size": item.minimum_size,
                    "default_placement": item.default_placement,
                    "allowed_placements": list(item.allowed_placements),
                    "actions": [thaw_json_value(action) for action in item.actions],
                }
            )
            for item in active_ui
        ),
    )


def projection_bytes(projection: PackageSurfaceProjection) -> bytes:
    return canonical_json_bytes(
        {
            "prompt_fragment_ids": projection.prompt_fragment_ids,
            "ui_surface_ids": projection.ui_surface_ids,
            "ui_surfaces": projection.ui_surfaces,
        }
    )


def dependency_only_manifest_rebind(original_json: str, current_json: str) -> bool:
    """Preserve all admitted package content/metadata while rebinding dependencies."""
    import json

    original = json.loads(original_json)
    current = json.loads(current_json)
    for value in (original, current):
        value.pop("content_sha256", None)
        value.pop("dependencies", None)
    return original == current
