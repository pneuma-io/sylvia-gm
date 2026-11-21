//! `domain/content_validation.py`: authoring-format checks with value-free corrections.

use super::catalog;
use super::schema::{PathPart, SchemaError, SchemaPart, TypeChecker, Validator};
use crate::error::{DomainError, RulesetDiagnostic};
use std::collections::HashMap;
use std::sync::OnceLock;
use sylvia_base::{pystr, Json};

/// `ContentShapeError`: public field-level corrections without submitted values.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContentShapeError {
    pub diagnostics: Vec<RulesetDiagnostic>,
}

impl ContentShapeError {
    /// `str(error)`.
    pub const MESSAGE: &'static str = "Content fields do not match the declared format";

    pub fn new(diagnostics: Vec<RulesetDiagnostic>) -> Self {
        Self { diagnostics }
    }
}

impl std::fmt::Display for ContentShapeError {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.write_str(Self::MESSAGE)
    }
}

impl std::error::Error for ContentShapeError {}

impl From<ContentShapeError> for DomainError {
    fn from(error: ContentShapeError) -> Self {
        DomainError::Diagnosed {
            error_type: "ContentShapeError",
            message: ContentShapeError::MESSAGE.to_owned(),
            diagnostics: error.diagnostics,
        }
    }
}

/// The compiled validator of each definition (Python's `lru_cache` of `_validator`).
fn validator(name: &str) -> &'static Validator<'static> {
    static VALIDATORS: OnceLock<HashMap<&'static str, Validator<'static>>> = OnceLock::new();
    let validators = VALIDATORS.get_or_init(|| {
        catalog::definitions()
            .iter()
            .map(|definition| {
                let compiled =
                    Validator::new(&definition.schema, TypeChecker::Content).unwrap_or_else(|error| {
                        panic!("definition {} does not compile: {error}", definition.name)
                    });
                (definition.name, compiled)
            })
            .collect()
    });
    // Python's `get_content_definition` raises for an unknown name; every caller passes a
    // literal installed name.
    validators.get(name).unwrap_or_else(|| panic!("Unknown content definition {name:?}"))
}

/// `_field_path(parts)`: keep correction paths useful without echoing arbitrary field text.
fn field_path(parts: &[PathPart]) -> String {
    let mut result = String::from("$");
    for part in parts {
        match part {
            PathPart::Index(index) => result.push_str(&format!("[{index}]")),
            PathPart::Key(key) if is_plain_field(key) => {
                result.push('.');
                result.push_str(key);
            }
            PathPart::Key(_) => result.push_str("[invalid field name]"),
        }
    }
    result
}

/// `re.fullmatch(r"[A-Za-z_][A-Za-z0-9_-]{0,95}", part)`.
fn is_plain_field(key: &str) -> bool {
    let mut chars = key.chars();
    chars.next().is_some_and(|c| c.is_ascii_alphabetic() || c == '_')
        && key.chars().count() <= 96
        && chars.all(|c| c.is_ascii_alphanumeric() || c == '_' || c == '-')
}

fn joined(parent: &[PathPart], error: &SchemaError) -> Vec<PathPart> {
    parent.iter().cloned().chain(error.path.iter().cloned()).collect()
}

fn is_union(error: &SchemaError) -> bool {
    matches!(error.keyword, Some("oneOf" | "anyOf"))
}

/// `_root_type_mismatch(error, path)`: a wrong union representation, not a field inside it.
fn root_type_mismatch(error: &SchemaError, absolute: &[PathPart], path: &[PathPart]) -> bool {
    if absolute != path {
        return false;
    }
    if error.keyword == Some("type") {
        return true;
    }
    !error.context.is_empty()
        && is_union(error)
        && error.context.iter().all(|child| root_type_mismatch(child, &joined(absolute, child), path))
}

/// `_field_errors(error)`: unwrap a uniquely type-compatible union branch, keeping nested paths.
/// Each error comes with its absolute path.
fn field_errors<'e, 'a>(
    error: &'e SchemaError<'a>,
    parent: &[PathPart],
    out: &mut Vec<(&'e SchemaError<'a>, Vec<PathPart>)>,
) {
    let absolute = joined(parent, error);
    if is_union(error) && !error.context.is_empty() {
        let mut branches: Vec<(&SchemaPart, Vec<&SchemaError>)> = Vec::new();
        for child in &error.context {
            let key = child.schema_path.front().expect("a union branch error has its branch index");
            match branches.iter_mut().find(|(existing, _)| *existing == key) {
                Some((_, children)) => children.push(child),
                None => branches.push((key, vec![child])),
            }
        }
        let compatible: Vec<&Vec<&SchemaError>> = branches
            .iter()
            .map(|(_, children)| children)
            .filter(|children| {
                !children.iter().any(|child| root_type_mismatch(child, &joined(&absolute, child), &absolute))
            })
            .collect();
        if compatible.len() == 1 {
            for child in compatible[0] {
                field_errors(child, &absolute, out);
            }
            return;
        }
    }
    out.push((error, absolute));
}

/// `str()` of a `type` keyword's value: a name, or Python's repr of a list of names.
fn type_text(value: &Json) -> String {
    match value {
        Json::String(name) => name.clone(),
        Json::Array(names) => {
            let quoted: Vec<String> = names
                .iter()
                .map(|name| {
                    let name = name.as_str().expect("type names are text");
                    assert!(
                        name.chars().all(|c| c.is_ascii_lowercase()),
                        "type name {name:?} needs a full repr"
                    );
                    format!("'{name}'")
                })
                .collect();
            format!("[{}]", quoted.join(", "))
        }
        other => panic!("unexpected type keyword value {other}"),
    }
}

/// `validate_content_shape(name, value, file=...)`: reject structural mistakes with bounded,
/// value-free field corrections (at most 16).
pub fn validate_content_shape(name: &str, value: &Json, file: Option<&str>) -> Result<(), ContentShapeError> {
    let diagnostics = shape_diagnostics(validator(name), name, value, file);
    if diagnostics.is_empty() {
        Ok(())
    } else {
        Err(ContentShapeError::new(diagnostics))
    }
}

/// The corrections `validate_content_shape` gives for `value` under `validator`, with `name`
/// in the guidance. Public so other formats (and tests) can use an uninstalled schema.
pub fn shape_diagnostics<'a>(
    validator: &Validator<'a>,
    name: &str,
    value: &'a Json,
    file: Option<&str>,
) -> Vec<RulesetDiagnostic> {
    let errors = validator.iter_errors(value);
    let mut details = Vec::new();
    for error in &errors {
        field_errors(error, &[], &mut details);
    }
    let mut corrections: Vec<((String, String), RulesetDiagnostic)> = Vec::new();
    'errors: for (error, path) in details {
        let keyword = error.keyword.unwrap_or("None");
        let instance = error.instance.as_ref();
        let (paths, message) = match (keyword, instance) {
            ("required", Json::Object(members)) => (
                error
                    .validator_value
                    .as_array()
                    .into_iter()
                    .flatten()
                    .filter_map(Json::as_str)
                    .filter(|field| !members.contains_key(*field))
                    .map(|field| [path.as_slice(), &[PathPart::Key(field.to_owned())]].concat())
                    .collect(),
                "Supply this required field.".to_owned(),
            ),
            ("additionalProperties", Json::Object(members)) => {
                let declared = error.schema.get("properties").and_then(Json::as_object);
                let extra: Vec<Vec<PathPart>> = members
                    .keys()
                    .filter(|field| declared.is_none_or(|declared| !declared.contains_key(*field)))
                    .map(|field| [path.as_slice(), &[PathPart::Key(field.clone())]].concat())
                    .collect();
                let paths = if extra.is_empty() { vec![path.clone()] } else { extra };
                (paths, "Remove this undeclared field.".to_owned())
            }
            ("type", _) => {
                (vec![path.clone()], format!("Use the declared type: {}.", type_text(&error.validator_value)))
            }
            ("minLength" | "maxLength", Json::String(text)) => {
                let bound = if keyword == "minLength" { "at least" } else { "at most" };
                (
                    vec![path.clone()],
                    format!(
                        "Use {bound} {} characters; this field has {} characters.",
                        error.validator_value,
                        pystr::len(text)
                    ),
                )
            }
            _ => (
                vec![path.clone()],
                format!("Correct this field to satisfy the definition's {keyword} constraint."),
            ),
        };
        for item_path in paths {
            let location = field_path(&item_path);
            let diagnostic = RulesetDiagnostic {
                code: format!("content_{keyword}_invalid"),
                phase: "content".into(),
                message: message.clone(),
                file: file.map(str::to_owned),
                path: Some(location.clone()),
                guidance: Some(format!("Read the {name} content definition and correct this field.")),
                ..Default::default()
            };
            let key = (keyword.to_owned(), location);
            match corrections.iter_mut().find(|(existing, _)| *existing == key) {
                Some((_, slot)) => *slot = diagnostic,
                None => corrections.push((key, diagnostic)),
            }
            if corrections.len() >= 16 {
                break 'errors;
            }
        }
    }
    corrections.into_iter().map(|(_, diagnostic)| diagnostic).collect()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn every_installed_definition_compiles_and_accepts_its_example() {
        for definition in catalog::definitions() {
            assert!(
                validate_content_shape(definition.name, &definition.example, None).is_ok(),
                "{} rejects its own example",
                definition.name
            );
        }
    }

    #[test]
    fn plain_field_names_follow_python() {
        assert!(is_plain_field("a_b-9"));
        assert!(is_plain_field(&"a".repeat(96)));
        assert!(!is_plain_field(&"a".repeat(97)));
        assert!(!is_plain_field("9a"));
        assert!(!is_plain_field("é"));
        assert!(!is_plain_field(""));
    }
}
