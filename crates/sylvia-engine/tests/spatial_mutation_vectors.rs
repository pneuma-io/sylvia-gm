//! Compare sylvia_engine::spatial's scene mutation with the frozen Python
//! (`spatial._interface.mutate_spatial_scene`).
//!
//! Regenerate with `tests/vectors/generate_spatial_scene_vectors.py` (needs the snapshot's Python).

use serde_json::{json, Value as Json};
use sha2::{Digest, Sha256};
use sylvia_engine::spatial::{admit_spatial_scene, mutate_spatial_scene, SpatialError};

fn record(scene: &Json, request: &Json) -> Json {
    let scene = admit_spatial_scene(scene).expect("an admissible starting scene");
    match mutate_spatial_scene(&scene, request) {
        Ok(mutation) => {
            let advanced = &mutation.scene;
            let canonical =
                sylvia_domain::package_library::canonical_json_bytes(&Json::Object(advanced.as_payload()));
            json!({"payload": mutation.as_payload(), "scene": {
                "map_id": advanced.map_id, "revision": advanced.geometry_revision, "snapshot_id": advanced.snapshot_id,
                "canonical_sha256": hex::encode(Sha256::digest(canonical))}})
        }
        Err(SpatialError::Validation(error)) => {
            json!({"error": ["SpatialValidationError", error.to_string(), error.code, error.path]})
        }
        Err(SpatialError::Uncaught { error_type, message }) => json!({"error": [error_type, message]}),
    }
}

#[test]
fn mutations_apply_as_python() {
    let vectors: Json =
        serde_json::from_str(include_str!("vectors/spatial_mutation_vectors.json")).expect("valid JSON");
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 1500);
    let mut failures = Vec::new();
    for (index, case) in cases.iter().enumerate() {
        let actual = record(&case["scene_input"], &case["request"]);
        let mut wanted = case.clone();
        let fields = wanted.as_object_mut().unwrap();
        fields.shift_remove("scene_input");
        fields.shift_remove("request");
        let (actual, wanted) =
            (serde_json::to_string(&actual).unwrap(), serde_json::to_string(&wanted).unwrap());
        if actual != wanted {
            failures.push(format!(
                "case {index}\n  python {}\n  rust   {}",
                &wanted[..wanted.len().min(700)],
                &actual[..actual.len().min(700)]
            ));
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
