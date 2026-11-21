//! Compare sylvia_domain's JSON Schema engine with `jsonschema` 4.26's own errors.
//!
//! Each case lists Python's `iter_errors` in order: `validator`, `message`, `absolute_path`,
//! `relative_schema_path` and, recursively, the union `context`. Regenerate with
//! `tests/vectors/generate_content_vectors.py` (needs the snapshot's Python).

use sylvia_base::pyjson;
use sylvia_base::Json;
use sylvia_domain::content::schema::{PathPart, SchemaError, SchemaPart, TypeChecker, Validator};
use sylvia_domain::content::shape_diagnostics;

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/schema_vectors.json")).expect("vector file is valid JSON")
}

fn read(path: &str) -> Json {
    let full = format!("{}/../../{path}", env!("CARGO_MANIFEST_DIR"));
    pyjson::loads(&std::fs::read_to_string(&full).unwrap_or_else(|_| panic!("{full}"))).unwrap()
}

fn path_json(parts: &[PathPart]) -> Json {
    Json::Array(
        parts
            .iter()
            .map(|part| match part {
                PathPart::Key(key) => Json::from(key.as_str()),
                PathPart::Index(index) => Json::from(*index),
            })
            .collect(),
    )
}

/// Python's `error_tree(error)` for a Rust error whose parent's absolute path is `parent`.
fn tree(error: &SchemaError, parent: &[PathPart]) -> Json {
    let absolute: Vec<PathPart> = parent.iter().cloned().chain(error.path.iter().cloned()).collect();
    let schema_path: Vec<Json> = error
        .schema_path
        .iter()
        .map(|part| match part {
            SchemaPart::Name(name) => Json::from(name.as_str()),
            SchemaPart::Index(index) => Json::from(*index),
        })
        .collect();
    serde_json::json!({
        "keyword": error.keyword,
        "message": error.message(),
        "path": path_json(&absolute),
        "schema_path": schema_path,
        "context": error.context.iter().map(|child| tree(child, &absolute)).collect::<Vec<_>>(),
    })
}

fn trees(validator: &Validator, document: &Json) -> Json {
    Json::Array(validator.iter_errors(document).iter().map(|error| tree(error, &[])).collect())
}

fn report(name: &str, failures: Vec<String>, total: usize) {
    assert!(
        failures.is_empty(),
        "{name}: {} of {total} cases differ, first: {:#?}",
        failures.len(),
        &failures[..failures.len().min(3)]
    );
}

#[test]
fn synthetic_schemas_give_python_errors_messages_and_corrections() {
    let vectors = vectors();
    let schemas = vectors["synthetic_schemas"].as_array().unwrap();
    let content: Vec<Validator> =
        schemas.iter().map(|schema| Validator::new(schema, TypeChecker::Content).unwrap()).collect();
    let draft: Vec<Validator> =
        schemas.iter().map(|schema| Validator::new(schema, TypeChecker::Draft202012).unwrap()).collect();
    let cases = vectors["synthetic"].as_array().unwrap();
    let mut failures = Vec::new();
    for case in cases {
        let index = case["schema"].as_u64().unwrap() as usize;
        let document = &case["document"];
        for (field, validator) in [("errors", &content[index]), ("draft_errors", &draft[index])] {
            let actual = trees(validator, document);
            if actual != case[field] {
                failures.push(format!(
                    "schema {index} {field} on {document}: python {}, rust {actual}",
                    case[field]
                ));
            }
        }
        let diagnostics: Vec<Json> = shape_diagnostics(&content[index], "synthetic", document, None)
            .iter()
            .map(|d| Json::Object(d.as_payload()))
            .collect();
        if Json::Array(diagnostics.clone()) != case["diagnostics"] {
            failures.push(format!(
                "schema {index} corrections on {document}: python {}, rust {}",
                case["diagnostics"],
                Json::Array(diagnostics)
            ));
        }
    }
    report("synthetic", failures, cases.len());
}

#[test]
fn shipped_tool_parameter_schemas_give_python_errors() {
    let recording = read("crates/sylvia-compare/recordings/windows/startup.recording.json");
    let rows = recording["database"]["tables"]["package_tools"]["rows"].as_array().unwrap();
    let vectors = vectors();
    let cases = vectors["tools"].as_array().unwrap();
    assert!(cases.len() > 2000);
    let mut failures = Vec::new();
    for case in cases {
        let row = rows
            .iter()
            .find(|row| row["package_id"] == case["package_id"] && row["tool_name"] == case["tool_name"])
            .expect("recorded tool");
        let schema = &row["input_schema_json"]["$json"];
        let validator = Validator::new(schema, TypeChecker::Draft202012).unwrap();
        let actual = trees(&validator, &case["document"]);
        if actual != case["errors"] {
            failures.push(format!(
                "{} on {}: python {}, rust {actual}",
                case["tool_name"], case["document"], case["errors"]
            ));
        }
    }
    report("tools", failures, cases.len());
}

#[test]
fn state_and_parameter_descriptor_schemas_give_python_errors() {
    let vectors = vectors();
    let state = read("src/sylvia_gm/state/state_schema.json");
    let descriptor = vectors["parameter_descriptor_schema"].clone();
    let state_validator = Validator::new(&state, TypeChecker::Draft202012).unwrap();
    let descriptor_validator = Validator::new(&descriptor, TypeChecker::Draft202012).unwrap();
    let cases = vectors["state"].as_array().unwrap();
    let mut failures = Vec::new();
    for case in cases {
        let validator = if case["schema"] == "state" { &state_validator } else { &descriptor_validator };
        let document = &case["document"];
        let actual = trees(validator, document);
        if actual != case["errors"] || Json::Bool(validator.is_valid(document)) != case["valid"] {
            failures
                .push(format!("{} on {document}: python {}, rust {actual}", case["schema"], case["errors"]));
        }
    }
    report("state", failures, cases.len());
}
