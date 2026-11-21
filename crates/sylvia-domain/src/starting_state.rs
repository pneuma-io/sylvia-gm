//! `domain/starting_state.py`: data-only declarations for checking authored starting mechanics.

use crate::authority::{owned_payload, validate_authority_identity_value};
use crate::error::{DomainError, DomainResult};
use crate::pyops::py_eq;
use sylvia_base::{pystr, Json, JsonMap};

/// `STARTING_STATE_FEATURE`.
pub const STARTING_STATE_FEATURE: &str = "starting_state_validation.v1";
/// `REQUIRED_STARTING_STATE_FEATURE`.
pub const REQUIRED_STARTING_STATE_FEATURE: &str = "required_starting_state.v1";
/// `PRIVATE_STATE_FEATURE`.
pub const PRIVATE_STATE_FEATURE: &str = "private_state.v1";

/// One reference from a starting-state source path to a target key and path.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct StateReference {
    pub source: Vec<String>,
    pub target_key: String,
    pub target: Vec<String>,
}

/// One package-declared starting-state key (`StateSchema`).
#[derive(Debug, Clone, PartialEq)]
pub struct StateSchema {
    pub key: String,
    pub schema: JsonMap,
    pub references: Vec<StateReference>,
    pub required: bool,
    /// `"public"` (Python's default) or `"engine"`.
    pub visibility: String,
}

/// `_BINDING.fullmatch(part)`: `\$[a-zA-Z_][a-zA-Z0-9_]{0,63}\Z`.
fn is_binding(part: &str) -> bool {
    let Some(rest) = part.strip_prefix('$') else { return false };
    let mut chars = rest.chars();
    chars.next().is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
        && rest.len() <= 64
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_')
}

/// `_path(value)`: bounded selectors, not expressions or executable package code.
fn path(value: &Json) -> DomainResult<Vec<String>> {
    let parts = match value {
        Json::Array(parts) if (1..=16).contains(&parts.len()) => parts,
        _ => return Err(DomainError::value("Starting-state reference path requires 1 to 16 segments")),
    };
    let mut result = Vec::with_capacity(parts.len());
    for (index, part) in parts.iter().enumerate() {
        let part = match part {
            Json::String(text) if !text.is_empty() && pystr::len(text) <= 128 => text,
            _ => return Err(DomainError::value("Starting-state reference path segment is invalid")),
        };
        if part.starts_with('$') && !is_binding(part) {
            return Err(DomainError::value("Starting-state reference binding is invalid"));
        }
        if part == "$key" && index != parts.len() - 1 {
            return Err(DomainError::value("Starting-state $key selector must be last"));
        }
        result.push(part.clone());
    }
    Ok(result)
}

const SCHEMA_FIELDS: &[&str] = &["key", "schema", "references", "required", "visibility"];

/// `parse_state_schemas(value)`: declarations whose keys and reference targets belong to their
/// package namespace.
pub fn parse_state_schemas(value: &Json) -> DomainResult<Vec<StateSchema>> {
    let items = match value {
        Json::Array(items) if (1..=32).contains(&items.len()) => items,
        _ => return Err(DomainError::value("Starting-state schemas require 1 to 32 declarations")),
    };
    let mut declarations: Vec<StateSchema> = Vec::with_capacity(items.len());
    for item in items {
        let fields = match item {
            Json::Object(fields)
                if ["key", "schema", "references"].iter().all(|key| fields.contains_key(*key))
                    && fields.keys().all(|key| SCHEMA_FIELDS.contains(&key.as_str())) =>
            {
                fields
            }
            _ => return Err(DomainError::value("Starting-state schema fields are invalid")),
        };
        let visibility = fields.get("visibility").cloned().unwrap_or(Json::from("public"));
        if !py_eq(&visibility, &Json::from("public")) && !py_eq(&visibility, &Json::from("engine")) {
            return Err(DomainError::value("Starting-state visibility is invalid"));
        }
        let required = match fields.get("required") {
            None => false,
            Some(Json::Bool(flag)) => *flag,
            Some(_) => return Err(DomainError::value("Starting-state required flag must be a boolean")),
        };
        let key = validate_authority_identity_value(&fields["key"], "Starting-state key", false)?.to_owned();
        let references = match &fields["references"] {
            Json::Array(references) if references.len() <= 128 => references,
            _ => return Err(DomainError::value("Starting-state references require a bounded array")),
        };
        let mut parsed = Vec::with_capacity(references.len());
        for reference in references {
            let reference = match reference {
                Json::Object(reference)
                    if reference.len() == 3
                        && ["source", "target_key", "target"]
                            .iter()
                            .all(|key| reference.contains_key(*key)) =>
                {
                    reference
                }
                _ => return Err(DomainError::value("Starting-state reference fields are invalid")),
            };
            let source = path(&reference["source"])?;
            let target = path(&reference["target"])?;
            let bindings: Vec<&String> =
                source.iter().filter(|part| part.starts_with('$') && part.as_str() != "$key").collect();
            let mut unique = bindings.clone();
            unique.sort();
            unique.dedup();
            if unique.len() != bindings.len() {
                return Err(DomainError::value("Starting-state source bindings must be unique"));
            }
            if target.iter().any(|part| part.starts_with('$') && part != "$key" && !bindings.contains(&part))
            {
                return Err(DomainError::value("Starting-state target uses an unbound selector"));
            }
            let target_key = validate_authority_identity_value(
                &reference["target_key"],
                "Starting-state target key",
                false,
            )?;
            parsed.push(StateReference { source, target_key: target_key.to_owned(), target });
        }
        // `visibility` is "public" or "engine" here; Python keeps the value it was given.
        let visibility = visibility.as_str().map(str::to_owned).unwrap_or_default();
        declarations.push(StateSchema {
            key,
            schema: owned_payload(&fields["schema"])?,
            references: parsed,
            required,
            visibility,
        });
    }
    let mut keys: Vec<&str> = declarations.iter().map(|item| item.key.as_str()).collect();
    keys.sort_unstable();
    keys.dedup();
    if keys.len() != declarations.len() {
        return Err(DomainError::value("Starting-state schema keys must be unique"));
    }
    Ok(declarations)
}
