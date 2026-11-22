//! `application/package_library.py`: owner-invoked package workflows. This module creates no
//! authority or connection; the store it installs into is a [`PackageStorePort`].

use crate::initial_character_state::validate_initial_character_contract;
use crate::package_runtime::{
    validate_package_runtime, PackageRuntimeFailure, PACKAGE_RUNTIME_BYTE_LIMIT, PACKAGE_RUNTIME_PATH,
};
use crate::setting_character_requirements::{
    parse_setting_character_requirements, CHARACTER_REQUIREMENTS_PATH,
};
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::error::{DomainError, DomainResult, RulesetDiagnostic};
use sylvia_domain::package_archive::{admit_package_archive, AdmittedPackageArchive};
use sylvia_domain::package_library::{
    campaign_setting_entries, canonical_json_bytes, PackageCandidate, PackageSurfaceProjection,
    StatePredicate, SurfaceDeclaration, ToolDeclaration,
};
use sylvia_domain::pyops::{py_eq, py_utf8_decode};
use sylvia_store::{AuthorityStore, StoreError};

const REQUIREMENTS_BYTE_LIMIT: i64 = 256 * 1024;

/// `_PackageStorePort`: the two store operations an install needs, bytes first. Errors from
/// admission and validation convert into the store's error type, as the owner reports them.
pub trait PackageStorePort {
    type Error: From<DomainError>;

    /// `stage_package_archive(admitted, install_id)`.
    fn stage_package_archive(
        &self,
        admitted: &AdmittedPackageArchive,
        install_id: &str,
    ) -> Result<(), Self::Error>;

    /// `publish_package_archive(admitted, install_id)`.
    fn publish_package_archive(
        &self,
        admitted: &AdmittedPackageArchive,
        install_id: &str,
    ) -> Result<PackageCandidate, Self::Error>;
}

impl PackageStorePort for AuthorityStore {
    type Error = StoreError;

    fn stage_package_archive(
        &self,
        admitted: &AdmittedPackageArchive,
        install_id: &str,
    ) -> Result<(), StoreError> {
        AuthorityStore::stage_package_archive(self, admitted, install_id)
    }

    fn publish_package_archive(
        &self,
        admitted: &AdmittedPackageArchive,
        install_id: &str,
    ) -> Result<PackageCandidate, StoreError> {
        AuthorityStore::publish_package_archive(self, admitted, install_id)
    }
}

fn runtime_rejected(diagnostics: Vec<RulesetDiagnostic>) -> DomainError {
    DomainError::command_rejected_with(
        "Package runtime admission failed",
        "package_runtime_invalid",
        diagnostics,
    )
}

/// `validate_admitted_package_runtimes(admitted)`: check executable declarations without
/// acquiring publication or campaign authority.
pub fn validate_admitted_package_runtimes(admitted: &AdmittedPackageArchive) -> DomainResult<()> {
    let settings: Vec<(&str, &str, &str)> =
        campaign_setting_entries(&admitted.package_lock).iter().map(|entry| entry.identity()).collect();
    for archive_file in &admitted.files {
        if archive_file.file.path() != CHARACTER_REQUIREMENTS_PATH {
            continue;
        }
        if !settings.contains(&admitted.manifests[archive_file.manifest].identity()) {
            return Err(DomainError::command_rejected(
                "Character world requirements belong in a Campaign Setting",
            ));
        }
        if archive_file.file.byte_count() > REQUIREMENTS_BYTE_LIMIT {
            return Err(DomainError::command_rejected("Setting character requirements are oversized"));
        }
        let text = py_utf8_decode(&admitted.read_file(archive_file)?).map_err(|message| {
            DomainError::Diagnosed { error_type: "UnicodeDecodeError", message, diagnostics: Vec::new() }
        })?;
        parse_setting_character_requirements(&text)?;
    }
    for (index, manifest) in admitted.manifests.iter().enumerate() {
        for declaration in &manifest.participant_artifacts {
            if let Some(schema) = &declaration.entity_state_schema {
                let calculations: Vec<Json> =
                    declaration.initial_state_calculations.iter().cloned().map(Json::Object).collect();
                let validation = declaration.initial_state_validation.clone().map(Json::Object);
                validate_initial_character_contract(schema, &calculations, validation.as_ref())?;
            }
        }
        let Some(runtime_file) = admitted
            .files
            .iter()
            .find(|file| file.manifest == index && file.file.path() == PACKAGE_RUNTIME_PATH)
        else {
            continue;
        };
        if runtime_file.file.byte_count() > PACKAGE_RUNTIME_BYTE_LIMIT as i64 {
            return Err(runtime_rejected(Vec::new()));
        }
        let runtime_bytes = admitted.read_file(runtime_file)?;
        match validate_package_runtime(&manifest.package_id, &manifest.tools, &runtime_bytes) {
            Ok(()) => {}
            Err(PackageRuntimeFailure::Rejected(error)) => {
                let diagnostics = if error.diagnostics.is_empty() {
                    vec![RulesetDiagnostic {
                        code: "package_runtime_invalid".into(),
                        phase: "runtime".into(),
                        message: error.message.clone(),
                        file: Some(PACKAGE_RUNTIME_PATH.into()),
                        package_id: Some(manifest.package_id.clone()),
                        guidance: Some(
                            "Correct the generated runtime or its public action declaration.".into(),
                        ),
                        ..Default::default()
                    }]
                } else {
                    error.diagnostics
                };
                return Err(runtime_rejected(diagnostics));
            }
            Err(PackageRuntimeFailure::Uncaught { class, message }) => {
                return Err(match class {
                    "ValueError" => DomainError::value(message),
                    "TypeError" => DomainError::type_error(message),
                    other => DomainError::uncaught(other, message),
                })
            }
        }
    }
    Ok(())
}

/// `install_package_archive(store, source, install_id=...)`: bounded admission, then bytes-first
/// staging before one authority publication.
pub fn install_package_archive<S: PackageStorePort>(
    store: &S,
    source: &[u8],
    install_id: &str,
) -> Result<PackageCandidate, S::Error> {
    let admitted = admit_package_archive(source)?;
    validate_admitted_package_runtimes(&admitted)?;
    store.stage_package_archive(&admitted, install_id)?;
    store.publish_package_archive(&admitted, install_id)
}

/// `run_migration_outside_sqlite(state, migrate)`: give deterministic code an owned snapshot
/// and recapture its result.
pub fn run_migration_outside_sqlite(
    state: &JsonMap,
    migrate: impl FnOnce(JsonMap) -> Json,
) -> DomainResult<JsonMap> {
    let Json::Object(captured) = migrate(state.clone()) else {
        return Err(DomainError::command_rejected_with(
            "Package migration must return canonical state",
            "migration_invalid",
            Vec::new(),
        ));
    };
    if captured.keys().any(|key| !key.contains('.')) {
        return Err(DomainError::command_rejected_with(
            "Package migration returned invalid state identities",
            "migration_invalid",
            Vec::new(),
        ));
    }
    Ok(captured)
}

/// `declaration_admitted(declaration, state=..., turn_status=..., writer=...,
/// declared_capabilities=...)`.
pub fn declaration_admitted(
    declaration: &ToolDeclaration,
    state: &JsonMap,
    turn_status: &str,
    writer: &str,
    declared_capabilities: &[String],
) -> bool {
    if !declared_capabilities.contains(&declaration.capability) {
        return false;
    }
    if !declaration.turn_statuses.iter().any(|status| status == turn_status)
        || !declaration.writers.iter().any(|known| known == writer)
    {
        return false;
    }
    state_predicates_match(&declaration.state_predicates, state)
}

/// `state_predicates_match(predicates, state)`: one Ruleset-authored predicate set against
/// authoritative state.
pub fn state_predicates_match(predicates: &[StatePredicate], state: &JsonMap) -> bool {
    predicates.iter().all(|predicate| predicate_matches(predicate, state))
}

/// `_predicate_matches(predicate, state)`: the key through one namespaced state root and
/// optional nested fields; a missing value equals nothing.
fn predicate_matches(predicate: &StatePredicate, state: &JsonMap) -> bool {
    let exact = format!("{}.{}", predicate.namespace, predicate.key);
    let value = match state.get(&exact) {
        Some(value) => Some(value),
        None => {
            let (root, nested) = match predicate.key.split_once('.') {
                Some((root, nested)) => (root, Some(nested)),
                None => (predicate.key.as_str(), None),
            };
            let mut value = state.get(&format!("{}.{root}", predicate.namespace));
            if let Some(nested) = nested {
                for field in nested.split('.') {
                    value = value.and_then(Json::as_object).and_then(|map| map.get(field));
                    if value.is_none() {
                        break;
                    }
                }
            }
            value
        }
    };
    let matches = value.is_some_and(|value| py_eq(value, &predicate.equals));
    matches != predicate.negated
}

/// `player_safe_source(source)`: no private Campaign Plan reads in a player-facing projection.
/// Python's `kind in {...}` hashes the kind, so a list or object kind raises `TypeError`.
fn player_safe_source(source: &Json) -> DomainResult<bool> {
    let Some(source) = source.as_object() else { return Ok(false) };
    let kind = source.get("kind").unwrap_or(&Json::Null);
    match kind {
        Json::String(kind) if kind == "keyed_values" => match source.get("entries") {
            // `isinstance(entries, Sequence)`: a list, or a string (whose characters are not
            // mappings, so only an empty string passes).
            Some(Json::Array(entries)) => {
                for entry in entries {
                    let Some(entry) = entry.as_object() else { return Ok(false) };
                    if !player_safe_source(entry.get("key").unwrap_or(&Json::Null))?
                        || !player_safe_source(entry.get("value").unwrap_or(&Json::Null))?
                    {
                        return Ok(false);
                    }
                }
                Ok(true)
            }
            Some(Json::String(text)) => Ok(text.is_empty()),
            _ => Ok(false),
        },
        Json::String(kind) if kind == "object" => {
            let Some(fields) = source.get("fields").and_then(Json::as_object) else { return Ok(false) };
            for child in fields.values() {
                if !player_safe_source(child)? {
                    return Ok(false);
                }
            }
            Ok(true)
        }
        Json::String(kind) => Ok(matches!(kind.as_str(), "state" | "participants" | "literal")),
        Json::Array(_) | Json::Object(_) => Err(sylvia_domain::pyops::py_unhashable(kind)),
        _ => Ok(false),
    }
}

/// `project_surfaces(prompts, ui, active_capabilities=...)`.
pub fn project_surfaces(
    prompts: &[SurfaceDeclaration],
    ui: &[SurfaceDeclaration],
    active_capabilities: &[String],
) -> DomainResult<PackageSurfaceProjection> {
    let active = |item: &SurfaceDeclaration| active_capabilities.contains(&item.capability);
    let mut active_ui: Vec<&SurfaceDeclaration> = Vec::new();
    for item in ui {
        if active(item) && player_safe_source(&item.source.clone().map(Json::Object).unwrap_or(Json::Null))? {
            active_ui.push(item);
        }
    }
    Ok(PackageSurfaceProjection {
        prompt_fragment_ids: prompts
            .iter()
            .filter(|item| active(item))
            .map(|item| item.identity.clone())
            .collect(),
        ui_surface_ids: active_ui.iter().map(|item| item.identity.clone()).collect(),
        ui_surfaces: active_ui
            .iter()
            .map(|item| {
                let Json::Object(surface) = serde_json::json!({
                    "id": item.identity,
                    "title": item.title,
                    "icon": item.icon,
                    "renderer": item.renderer,
                    "source": item.source,
                    "group": item.group,
                    "order": item.order,
                    "default_size": item.default_size,
                    "minimum_size": item.minimum_size,
                    "default_placement": item.default_placement,
                    "allowed_placements": item.allowed_placements,
                    "actions": item.actions,
                }) else {
                    unreachable!("an object")
                };
                surface
            })
            .collect(),
    })
}

/// `projection_bytes(projection)`.
pub fn projection_bytes(projection: &PackageSurfaceProjection) -> Vec<u8> {
    canonical_json_bytes(&serde_json::json!({
        "prompt_fragment_ids": projection.prompt_fragment_ids,
        "ui_surface_ids": projection.ui_surface_ids,
        "ui_surfaces": projection.ui_surfaces,
    }))
}

/// `dependency_only_manifest_rebind(original_json, current_json)`: whether two manifests agree
/// on everything but their content hash and dependencies.
pub fn dependency_only_manifest_rebind(original_json: &str, current_json: &str) -> DomainResult<bool> {
    let read = |text: &str| -> DomainResult<Json> {
        let mut value = pyjson::loads(text).map_err(|error| DomainError::Diagnosed {
            error_type: "JSONDecodeError",
            message: error.message,
            diagnostics: Vec::new(),
        })?;
        match &mut value {
            Json::Object(map) => {
                map.shift_remove("content_sha256");
                map.shift_remove("dependencies");
            }
            // `list.pop(index)` takes one argument.
            Json::Array(_) => return Err(DomainError::type_error("pop expected at most 1 argument, got 2")),
            other => {
                return Err(DomainError::uncaught(
                    "AttributeError",
                    format!("'{}' object has no attribute 'pop'", sylvia_domain::pyops::py_type_name(other)),
                ))
            }
        }
        Ok(value)
    };
    let (original, current) = (read(original_json)?, read(current_json)?);
    Ok(py_eq(&original, &current))
}
