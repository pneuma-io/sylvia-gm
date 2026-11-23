//! `application/compatible_character_copy.py`: explicitly copy one unchanged character to a
//! compatible exact Setting binding.

use crate::initial_character_state::verify_initial_character_state;
use crate::reusable_participants::{save_reusable_participant, ReusableParticipant};
use crate::setting_character_requirements::validate_setting_character_data;
use std::path::Path;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::PackageLockEntry;
use sylvia_domain::campaign_plan::PackageBinding;
use sylvia_domain::error::{DomainError, DomainResult, RulesetDiagnostic};
use sylvia_domain::package_library::{canonical_json_bytes, ParticipantArtifactDeclaration};
use sylvia_domain::pyops::py_eq;
use sylvia_domain::schema_admission::{validate_json_schema, validate_parameters};

const GUIDANCE: &str = "Adapt an explicit copy in Builder for the selected Setting.";

fn diagnostic(
    path: Option<String>,
    message: &str,
    expected: Option<String>,
    actual: Option<String>,
) -> RulesetDiagnostic {
    RulesetDiagnostic {
        code: "character_adaptation_required".into(),
        phase: "compatible_character_copy".into(),
        message: message.to_owned(),
        file: None,
        path,
        package_id: None,
        item_id: None,
        expected,
        actual,
        guidance: Some(GUIDANCE.into()),
    }
}

/// `_adaptation_required(error, path=..., message=...)`: destination incompatibility as bounded,
/// value-free Builder guidance.
fn adaptation_required(error: Option<&DomainError>, path: &str, message: &str) -> DomainError {
    let existing = error.map(DomainError::diagnostics).unwrap_or_default();
    let issues = error
        .filter(|error| error.python_class() == "MechanicsRejected")
        .and_then(DomainError::mechanics)
        .map(|details| details.validation_issues.as_slice())
        .unwrap_or_default();
    let diagnostics: Vec<RulesetDiagnostic> = if !existing.is_empty() {
        existing
            .iter()
            .take(16)
            .map(|item| diagnostic(item.path.clone(), message, item.expected.clone(), item.actual.clone()))
            .collect()
    } else if !issues.is_empty() {
        issues
            .iter()
            .take(16)
            .map(|issue| {
                diagnostic(
                    Some(issue.path.clone()),
                    message,
                    issue.expected_type.clone(),
                    issue.actual_type.clone(),
                )
            })
            .collect()
    } else {
        vec![diagnostic(Some(path.to_owned()), message, None, None)]
    };
    DomainError::command_rejected_with(
        "The character needs explicit adaptation for the selected Setting",
        "character_adaptation_required",
        diagnostics,
    )
}

/// `json.loads(canonical_json_bytes(value))`.
fn canonical(value: &Json) -> Json {
    let text = String::from_utf8(canonical_json_bytes(value)).expect("canonical JSON is UTF-8");
    sylvia_base::pyjson::loads(&text).expect("canonical JSON reads back")
}

/// `authored_character_state(complete, declaration)`: calculated leaves omitted before the
/// established save path recomputes them.
pub fn authored_character_state(
    complete: &JsonMap,
    declaration: &ParticipantArtifactDeclaration,
) -> DomainResult<JsonMap> {
    let Json::Object(mut authored) = canonical(&Json::Object(complete.clone())) else {
        unreachable!("a mapping stays a mapping")
    };
    for calculation in &declaration.initial_state_calculations {
        let field = match calculation.get("set") {
            Some(Json::Object(setting)) => setting.get("field"),
            _ => None,
        };
        let Some(Json::String(field)) = field else {
            return Err(DomainError::value("Destination calculation target is invalid"));
        };
        let parts: Vec<&str> = field.split('.').collect();
        let (last, parents) = parts.split_last().expect("split yields one part at least");
        let mut current = Some(&mut authored);
        for part in parents {
            current = match current {
                Some(map) => match map.get_mut(*part) {
                    Some(Json::Object(child)) => Some(child),
                    _ => None,
                },
                None => None,
            };
            if current.is_none() {
                break;
            }
        }
        if let Some(map) = current {
            map.shift_remove(*last);
        }
    }
    Ok(authored)
}

/// `isinstance(error, (CommandRejected, TypeError, ValueError))`.
fn adaptable(error: &DomainError) -> bool {
    error.is_value_error()
        || matches!(error, DomainError::Type(_))
        || matches!(error.python_class(), "CommandRejected" | "MechanicsRejected")
}

/// The destination of `copy_compatible_character`.
pub struct CopyDestination<'a> {
    pub operation_id: &'a str,
    pub package: &'a PackageLockEntry,
    pub declaration: &'a ParticipantArtifactDeclaration,
    pub campaign_setting: &'a PackageBinding,
    pub setting_character_schema: Option<&'a JsonMap>,
}

/// `copy_compatible_character(storage_root, source=..., expected_content_sha256=..., ...)`: a new
/// exact binding saved only when every unchanged source value satisfies the destination.
pub fn copy_compatible_character(
    storage_root: &Path,
    source: &ReusableParticipant,
    expected_content_sha256: &str,
    destination: &CopyDestination<'_>,
) -> DomainResult<ReusableParticipant> {
    let sha256 = expected_content_sha256.len() == 64
        && expected_content_sha256.bytes().all(|byte| matches!(byte, b'0'..=b'9' | b'a'..=b'f'));
    if !sha256 || source.content_sha256 != expected_content_sha256 {
        return Err(DomainError::command_rejected_with(
            "The source character changed; refresh it before copying",
            "stale_character",
            Vec::new(),
        ));
    }
    let Some(authored_profile) = &source.authored_profile else {
        return Err(adaptation_required(
            None,
            "authored_profile",
            "The source character does not contain an authored profile.",
        ));
    };
    let declaration = destination.declaration;
    let checked = (|| -> DomainResult<JsonMap> {
        validate_json_schema(&declaration.schema, "Destination participant schema")?;
        validate_parameters(&source.participant, &declaration.schema)?;
        if let Some(Json::Object(properties)) = declaration.schema.get("properties") {
            if properties.contains_key("party_role")
                && !source
                    .participant
                    .get("party_role")
                    .is_some_and(|role| py_eq(role, &Json::from(source.role.as_str())))
            {
                return Err(DomainError::value("Destination role field would change the participant"));
            }
        }
        let world_data = validate_setting_character_data(
            &Json::Object(source.setting_data.clone()),
            destination.setting_character_schema,
        )?;
        let Some(entity_state_schema) = &declaration.entity_state_schema else {
            return Err(DomainError::value("Destination creation contract is incomplete"));
        };
        let calculations: Vec<Json> =
            declaration.initial_state_calculations.iter().cloned().map(Json::Object).collect();
        let validation = declaration.initial_state_validation.clone().map(Json::Object);
        let complete = verify_initial_character_state(
            &Json::Object(source.participant.clone()),
            &source.entity_state,
            entity_state_schema,
            &calculations,
            validation.as_ref(),
            Some(&Json::Object(world_data)),
        )?;
        authored_character_state(&complete, declaration)
    })();
    let authored_state = match checked {
        Ok(state) => state,
        Err(error) if adaptable(&error) => {
            return Err(adaptation_required(
                Some(&error),
                "character",
                "The unchanged character data does not satisfy the destination requirements.",
            ))
        }
        Err(error) => return Err(error),
    };
    save_reusable_participant(
        storage_root,
        destination.operation_id,
        &source.role,
        &source.summary,
        &Json::from(authored_profile.as_str()),
        &Json::Object(source.participant.clone()),
        &Json::Object(authored_state),
        destination.package,
        declaration,
        destination.campaign_setting,
        Some(&Json::Object(source.setting_data.clone())),
        destination.setting_character_schema,
        source.source_profile.clone().map(Json::Object).as_ref(),
    )
}
