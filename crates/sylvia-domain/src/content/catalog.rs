//! `domain/content_definitions.py`: the installed content definition catalogue.
//!
//! The definitions are data exported from the Python snapshot by `generate_definitions.py`
//! (one JSON file each, in `definitions/`). They are parsed once, keeping key order and the
//! integer/float distinction, so what authors and the model read is byte-identical.

use crate::error::{DomainError, DomainResult};
use std::sync::OnceLock;
use sylvia_base::{pyjson, Json, JsonMap};

// Generated; kept exactly as the generator writes it.
#[rustfmt::skip]
#[path = "definitions.rs"]
mod data;

/// One installed definition, as `_DEFINITIONS[name]` holds it.
#[derive(Debug)]
pub struct ContentDefinition {
    pub name: &'static str,
    pub schema: Json,
    pub example: Json,
    pub notes: Vec<Json>,
    pub file: String,
}

fn parse(name: &str, text: &str) -> Json {
    pyjson::loads(text).unwrap_or_else(|error| panic!("content definition {name} is not JSON: {error}"))
}

/// Every installed definition in catalogue order.
pub fn definitions() -> &'static [ContentDefinition] {
    static DEFINITIONS: OnceLock<Vec<ContentDefinition>> = OnceLock::new();
    DEFINITIONS.get_or_init(|| {
        data::DEFINITIONS
            .iter()
            .map(|(name, text)| {
                let Json::Object(mut record) = parse(name, text) else {
                    panic!("definition {name} is an object")
                };
                let notes = match record.remove("notes") {
                    Some(Json::Array(notes)) => notes,
                    _ => panic!("definition {name} has notes"),
                };
                ContentDefinition {
                    name,
                    schema: record.remove("schema").expect("schema"),
                    example: record.remove("example").expect("example"),
                    notes,
                    file: record.remove("file").and_then(|f| f.as_str().map(str::to_owned)).expect("file"),
                }
            })
            .collect()
    })
}

/// The installed definition called `name`.
pub fn definition(name: &str) -> Option<&'static ContentDefinition> {
    definitions().iter().find(|definition| definition.name == name)
}

fn procedural() -> &'static Json {
    static PROCEDURAL: OnceLock<Json> = OnceLock::new();
    PROCEDURAL.get_or_init(|| parse("package_runtime.procedural", data::PACKAGE_RUNTIME_PROCEDURAL))
}

/// `list_content_definitions()`: the catalogue rows.
pub fn list_content_definitions() -> Vec<JsonMap> {
    definitions()
        .iter()
        .map(|definition| {
            let mut row = JsonMap::new();
            row.insert("name".into(), Json::from(definition.name));
            row.insert("id".into(), definition.schema["$id"].clone());
            row.insert("title".into(), definition.schema["title"].clone());
            row.insert("file".into(), Json::from(definition.file.as_str()));
            row
        })
        .collect()
}

/// `get_content_definition(name)`: schema, example, a derived invalid example and the
/// procedural requirements.
pub fn get_content_definition(name: &str) -> DomainResult<JsonMap> {
    let definition = definition(name).ok_or_else(|| DomainError::value("Unknown content definition"))?;
    let mut invalid_example = definition.example.as_object().cloned().unwrap_or_default();
    invalid_example.insert("unsupported_field".into(), Json::Bool(true));
    let mut result = JsonMap::new();
    result.insert("name".into(), Json::from(definition.name));
    result.insert("file".into(), Json::from(definition.file.as_str()));
    result.insert("schema".into(), definition.schema.clone());
    result.insert("example".into(), definition.example.clone());
    result.insert("invalid_example".into(), Json::Object(invalid_example));
    result.insert(
        "invalid_example_problem".into(),
        Json::from("Remove $.unsupported_field: this format accepts only its declared root fields."),
    );
    result.insert("requirements".into(), Json::Array(definition.notes.clone()));
    result.insert(
        "validation_scope".into(),
        Json::from("Structure plus the listed procedural requirements; not publication or gameplay testing."),
    );
    if name == "package_runtime" {
        result.insert("procedural_definition".into(), procedural().clone());
    }
    Ok(result)
}
