//! Compare sylvia_engine::spatial's query admission with the frozen Python
//! (`spatial._interface.admit_spatial_query`).
//!
//! Regenerate with `tests/vectors/generate_spatial_scene_vectors.py` (needs the snapshot's Python).

use serde_json::{json, Value as Json};
use sylvia_engine::spatial::{admit_spatial_query, admit_spatial_scene, SpatialError};

#[test]
fn queries_admit_as_python() {
    let vectors: Json =
        serde_json::from_str(include_str!("vectors/spatial_query_vectors.json")).expect("valid JSON");
    let scene = admit_spatial_scene(&vectors["scene"]).expect("the query scene");
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 6000);
    let mut failures = Vec::new();
    for (index, case) in cases.iter().enumerate() {
        let actual = match admit_spatial_query(&scene, &case["query"]) {
            Ok(query) => json!({"payload": query.as_payload(), "kind": query.kind, "map_id": query.map_id}),
            Err(SpatialError::Validation(error)) => {
                json!({"error": ["SpatialValidationError", error.to_string(), error.code, error.path]})
            }
            Err(SpatialError::Uncaught { error_type, message }) => json!({"error": [error_type, message]}),
        };
        let mut wanted = case.clone();
        wanted.as_object_mut().unwrap().shift_remove("query");
        let (actual, wanted) =
            (serde_json::to_string(&actual).unwrap(), serde_json::to_string(&wanted).unwrap());
        if actual != wanted {
            failures.push(format!("case {index}: {}\n  python {wanted}\n  rust   {actual}", case["query"]));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} differ, first: {:#?}",
        failures.len(),
        cases.len(),
        &failures[..failures.len().min(5)]
    );
}
