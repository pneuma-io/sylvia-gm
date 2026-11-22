//! `application/campaign_library_selection.py`, the part lane 5's campaign creation catalog
//! needs first: `participant_catalog_payload`, with `_character_state_reference` and
//! `_participant_choice`. The library selection itself follows separately.
//!
//! The owner reads are [`LibraryOwner`]'s (`AuthorityStore` implements it).

use crate::library_sources::LibraryOwner;
use crate::package_runtime::{
    package_model_decision_boundary, PackageRuntimeFailure, PACKAGE_RUNTIME_BYTE_LIMIT, PACKAGE_RUNTIME_PATH,
};
use crate::reusable_participants::ReusableParticipant;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::error::DomainError;

/// `AuthorityOwner.package_file_paths`' default `maximum_files`.
const FILE_LIMIT: i64 = sylvia_store::package_reads::DEFAULT_MAXIMUM_FILES;

/// A `PackageRuntimeError` (a `ValueError` with diagnostics), or the exception Python lets through.
fn runtime_error(failure: PackageRuntimeFailure) -> DomainError {
    match failure {
        PackageRuntimeFailure::Rejected(error) => DomainError::Diagnosed {
            error_type: "PackageRuntimeError",
            message: error.message,
            diagnostics: error.diagnostics,
        },
        PackageRuntimeFailure::Uncaught { class, message } => DomainError::uncaught(class, message),
    }
}

/// `_character_state_reference(owner, package_id, version, sha256)`: the exact Ruleset location
/// used by both readiness and party materialization.
pub fn character_state_reference<O: LibraryOwner>(
    owner: &O,
    package_id: &str,
    version: &str,
    sha256: &str,
) -> Result<Option<JsonMap>, O::Error> {
    let paths = owner.package_file_paths(package_id, version, sha256, FILE_LIMIT)?;
    if !paths.iter().any(|path| path == PACKAGE_RUNTIME_PATH) {
        return Ok(None);
    }
    let runtime = owner.package_text_file(
        package_id,
        version,
        sha256,
        PACKAGE_RUNTIME_PATH,
        PACKAGE_RUNTIME_BYTE_LIMIT as i64,
    )?;
    let boundary = package_model_decision_boundary(package_id, runtime.as_bytes()).map_err(runtime_error)?;
    Ok(boundary.and_then(|boundary| boundary.get("actor_entities").and_then(Json::as_object).cloned()))
}

/// `_participant_choice(item, entity_reference)`: keep reusable-save receipts and campaign
/// choices honest about playable state.
pub fn participant_choice(item: &ReusableParticipant, entity_reference: Option<&JsonMap>) -> JsonMap {
    let mut row = item.catalog_payload();
    let missing_location = !item.entity_state.is_empty() && entity_reference.is_none();
    row.insert("campaign_ready".into(), Json::Bool(item.authored_profile.is_some() && !missing_location));
    if missing_location {
        row.insert(
            "unavailable_reason".into(),
            Json::from("This Ruleset has no declared character state location."),
        );
    }
    row
}

/// `participant_catalog_payload(owner, item)`: a saved Character's readiness against its
/// immutable Ruleset, without a Plan.
pub fn participant_catalog_payload<O: LibraryOwner>(
    owner: &O,
    item: &ReusableParticipant,
) -> Result<JsonMap, O::Error> {
    let reference =
        character_state_reference(owner, &item.package_id, &item.package_version, &item.package_sha256)?;
    Ok(participant_choice(item, reference.as_ref()))
}
