//! `application/setting_character_requirements.py`: parse and enforce Setting-owned Character
//! File information.

use sylvia_base::{Json, JsonMap};
use sylvia_domain::content::validate_content_shape;
use sylvia_domain::error::{DomainError, DomainResult, RulesetDiagnostic};
use sylvia_domain::package_library::{canonical_json_bytes, parse_content_json_bytes, JsonShapeLimits};
use sylvia_domain::pyops::{py_eq, py_truthy};
use sylvia_domain::schema_admission::{validate_json_schema, validate_parameters};

/// `CHARACTER_REQUIREMENTS_PATH`.
pub const CHARACTER_REQUIREMENTS_PATH: &str = "character-requirements.json";
const BYTE_LIMIT: usize = 256 * 1024;

/// `_reject(code, message, path=..., guidance=...)`.
fn reject(code: &str, message: &str, path: &str, guidance: &str) -> DomainError {
    DomainError::command_rejected_with(
        "Setting character requirements are invalid",
        code,
        vec![RulesetDiagnostic {
            code: code.into(),
            phase: "setting_character_requirements".into(),
            file: Some(CHARACTER_REQUIREMENTS_PATH.into()),
            path: Some(path.into()),
            message: message.into(),
            guidance: Some(guidance.into()),
            ..Default::default()
        }],
    )
}

fn is_command_rejected(error: &DomainError) -> bool {
    matches!(
        error,
        DomainError::Rejected {
            error_type: "CommandRejected" | "MechanicsRejected" | "RulesetPreconditionRejected",
            ..
        }
    )
}

/// `_require_explicit_object_extensions(schema, path=...)`: every structured object states
/// whether and how undeclared fields are admitted.
fn require_explicit_object_extensions(schema: &JsonMap, path: &str) -> DomainResult<()> {
    if schema.get("type").is_some_and(|kind| py_eq(kind, &Json::from("object")))
        && !schema.contains_key("additionalProperties")
    {
        return Err(reject(
            "setting_character_requirements_schema_invalid",
            "An object schema does not declare its extension policy.",
            path,
            "Set additionalProperties to false, true, or an admitted schema.",
        ));
    }
    if let Some(Json::Object(properties)) = schema.get("properties") {
        for (name, child) in properties {
            if let Json::Object(child) = child {
                require_explicit_object_extensions(child, &format!("{path}.properties.{name}"))?;
            }
        }
    }
    if let Some(Json::Object(items)) = schema.get("items") {
        require_explicit_object_extensions(items, &format!("{path}.items"))?;
    }
    if let Some(Json::Object(additional)) = schema.get("additionalProperties") {
        require_explicit_object_extensions(additional, &format!("{path}.additionalProperties"))?;
    }
    Ok(())
}

/// `parse_setting_character_requirements(source)`: one bounded, closed requirements envelope;
/// returns its admitted root schema. (A Rust `&str` is always encodable UTF-8, so Python's
/// "Use valid UTF-8 text." branch for a lone surrogate cannot arise.)
pub fn parse_setting_character_requirements(source: &str) -> DomainResult<JsonMap> {
    let encoded = source.as_bytes();
    if encoded.is_empty() || encoded.len() > BYTE_LIMIT {
        return Err(reject(
            "setting_character_requirements_size_invalid",
            "The requirements file is empty or oversized.",
            "$",
            "Keep the complete file within 256 KiB.",
        ));
    }
    let limits = JsonShapeLimits::new(64, 250_000).with_object_members(10_000);
    let raw = parse_content_json_bytes(encoded, "Setting character requirements", limits).map_err(|_| {
        reject(
            "setting_character_requirements_json_invalid",
            "The requirements file is not strict JSON.",
            "$",
            "Use finite JSON values, unique object fields, and bounded nesting.",
        )
    })?;
    if let Err(error) =
        validate_content_shape("setting_character_requirements", &raw, Some(CHARACTER_REQUIREMENTS_PATH))
    {
        return Err(DomainError::command_rejected_with(
            "Setting character requirements fields are invalid",
            "setting_character_requirements_envelope_invalid",
            error.diagnostics,
        ));
    }
    let schema = match &raw["schema"] {
        Json::Object(schema) if schema.get("type").is_some_and(|kind| py_eq(kind, &Json::from("object"))) => {
            schema
        }
        _ => {
            return Err(reject(
                "setting_character_requirements_schema_invalid",
                "The requirements schema must have an object root.",
                "schema",
                "Declare an admitted schema with type object.",
            ))
        }
    };
    let checked = validate_json_schema(schema, "Setting character requirements schema")
        .and_then(|()| require_explicit_object_extensions(schema, "schema"));
    match checked {
        Ok(()) => Ok(schema.clone()),
        Err(error) if is_command_rejected(&error) && error.diagnostics().is_empty() => Err(reject(
            "setting_character_requirements_schema_invalid",
            "The requirements schema is unsupported.",
            "schema",
            "Use only Sylvia GM's admitted JSON-schema subset.",
        )),
        Err(error) => Err(error),
    }
}

/// `validate_setting_character_data(data, schema=...)`: an owned `setting_data` object after
/// exact, value-free schema validation.
pub fn validate_setting_character_data(data: &Json, schema: Option<&JsonMap>) -> DomainResult<JsonMap> {
    let Json::Object(captured) = data else {
        return Err(reject(
            "setting_character_data_invalid",
            "setting_data must be an object.",
            "setting_data",
            "Supply the Setting-owned character fields as a JSON object.",
        ));
    };
    if canonical_json_bytes(data).len() > BYTE_LIMIT {
        return Err(reject(
            "setting_character_data_size_invalid",
            "setting_data is not bounded finite JSON.",
            "setting_data",
            "Keep setting_data within 256 KiB.",
        ));
    }
    let Some(schema) = schema else {
        if py_truthy(data) {
            return Err(reject(
                "setting_character_data_undeclared",
                "This Setting declares no character information.",
                "setting_data",
                "Use an empty setting_data object.",
            ));
        }
        return Ok(captured.clone());
    };
    match validate_parameters(captured, schema) {
        Ok(()) => Ok(captured.clone()),
        Err(error) if error.mechanics().is_some() => {
            let issues = &error.mechanics().expect("checked").validation_issues;
            let diagnostics = issues
                .iter()
                .take(16)
                .map(|issue| RulesetDiagnostic {
                    code: "setting_character_data_schema_invalid".into(),
                    phase: "setting_character_requirements".into(),
                    path: Some(if issue.path == "$" {
                        "setting_data".into()
                    } else {
                        format!("setting_data.{}", issue.path.strip_prefix("$.").unwrap_or(&issue.path))
                    }),
                    message: "This field does not match the Setting's character requirements.".into(),
                    expected: issue.expected_type.clone(),
                    actual: issue.actual_type.clone(),
                    guidance: Some("Supply the required field with its declared JSON type.".into()),
                    ..Default::default()
                })
                .collect();
            Err(DomainError::command_rejected_with(
                "Setting character data does not match its requirements",
                "setting_character_data_schema_invalid",
                diagnostics,
            ))
        }
        Err(error) if is_command_rejected(&error) => Err(reject(
            "setting_character_requirements_schema_invalid",
            "The Setting requirements schema is invalid.",
            "setting_data",
            "Correct the Setting's character requirements definition.",
        )),
        Err(error) => Err(error),
    }
}
