//! Compare sylvia-app::package_runtime::package_model_decision_boundary (and the execution load
//! under it) with the frozen Python.
//!
//! Regenerate with `tests/vectors/generate_decision_boundary_vectors.py` (needs the snapshot's Python).

use serde_json::{json, Value as Json};
use std::path::Path;
use sylvia_app::package_runtime::{package_model_decision_boundary, PackageRuntimeFailure};

fn record(package_id: &str, runtime: &[u8]) -> Json {
    match package_model_decision_boundary(package_id, runtime) {
        Ok(value) => json!({"result": value}),
        Err(PackageRuntimeFailure::Rejected(error)) => {
            json!({"error": ["PackageRuntimeError", error.message]})
        }
        Err(PackageRuntimeFailure::Uncaught { class, message }) => json!({"error": [class, message]}),
    }
}

fn runtime_of(case: &Json) -> Vec<u8> {
    if let Some(source) = case.get("source").and_then(Json::as_str) {
        let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
        return std::fs::read(root.join(source)).expect("the shipped runtime");
    }
    let hex = case["runtime_hex"].as_str().unwrap();
    (0..hex.len()).step_by(2).map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap()).collect()
}

#[test]
fn decision_boundaries_load_as_python() {
    let vectors: Json =
        serde_json::from_str(include_str!("vectors/decision_boundary_vectors.json")).expect("valid JSON");
    let mut failures = Vec::new();
    for case in vectors["cases"].as_array().unwrap().iter().chain(vectors["shipped"].as_array().unwrap()) {
        let actual = record(case["package_id"].as_str().unwrap(), &runtime_of(case));
        let mut wanted = serde_json::Map::new();
        for key in ["result", "error"] {
            if let Some(value) = case.get(key) {
                wanted.insert(key.into(), value.clone());
            }
        }
        let (actual, wanted) =
            (serde_json::to_string(&actual).unwrap(), serde_json::to_string(&wanted).unwrap());
        if actual != wanted {
            failures.push(format!("{}\n  python {wanted}\n  rust   {actual}", case["name"]));
        }
    }
    assert!(vectors["cases"].as_array().unwrap().len() > 400);
    assert!(
        failures.is_empty(),
        "{} differ, first: {:#?}",
        failures.len(),
        &failures[..failures.len().min(5)]
    );
}
