//! Compare sylvia_engine::spatial's scene admission with the frozen Python
//! (`spatial._interface.admit_spatial_scene`).
//!
//! Regenerate with `tests/vectors/generate_spatial_scene_vectors.py` (needs the snapshot's Python).

use serde_json::{json, Value as Json};
use sha2::{Digest, Sha256};
use sylvia_engine::spatial::{admit_spatial_scene, SpatialError};

/// The generator's `build(value)`: a `copies` recipe repeats the first item of that list in the
/// base scene, the copies' ids `{key[0]}00000`, `{key[0]}00001` and so on, their metadata `{}`.
fn build(input: &Json, base: &Json) -> Json {
    let (Some(key), Some(count)) =
        (input.pointer("/recipe/copies").and_then(Json::as_str), input.pointer("/recipe/count").and_then(Json::as_u64))
    else {
        return input.clone();
    };
    let mut scene = base.clone();
    let first = base[key][0].clone();
    let prefix = &key[..1];
    scene[key] = Json::Array(
        (0..count)
            .map(|index| {
                let mut copy = first.clone();
                if copy.get("id").is_some() {
                    copy["id"] = json!(format!("{prefix}{index:05}"));
                }
                if copy.get("metadata").is_some() {
                    copy["metadata"] = json!({});
                }
                copy
            })
            .collect(),
    );
    scene
}

fn record(input: &Json) -> Json {
    match admit_spatial_scene(input) {
        Ok(scene) => {
            let mut payload = Json::Object(scene.as_payload());
            if serde_json::to_string(&payload).unwrap().len() > 200_000 {
                let digest = Sha256::digest(sylvia_domain::package_library::canonical_json_bytes(&payload));
                payload = json!({"canonical_sha256": hex::encode(digest)});
            }
            json!({"payload": payload, "map_id": scene.map_id, "revision": scene.geometry_revision,
                   "snapshot_id": scene.snapshot_id, "algorithm_version": scene.algorithm_version})
        }
        Err(SpatialError::Validation(error)) => {
            json!({"error": ["SpatialValidationError", error.to_string(), error.code, error.path]})
        }
        Err(SpatialError::Uncaught { error_type, message }) => json!({"error": [error_type, message]}),
    }
}

#[test]
fn scenes_admit_as_python() {
    let vectors: Json =
        serde_json::from_str(include_str!("vectors/spatial_scene_vectors.json")).expect("valid JSON");
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 3000);
    let base = cases[0]["input"].clone();
    let mut failures = Vec::new();
    for (index, case) in cases.iter().enumerate() {
        let actual = record(&build(&case["input"], &base));
        let mut wanted = case.clone();
        wanted.as_object_mut().unwrap().shift_remove("input");
        // Key order matters (the payload keeps Python's), so compare the serialized text.
        let (actual, wanted) = (serde_json::to_string(&actual).unwrap(), serde_json::to_string(&wanted).unwrap());
        if actual != wanted {
            failures.push(format!("case {index}\n  python {}\n  rust   {}", &wanted[..wanted.len().min(600)],
                                  &actual[..actual.len().min(600)]));
        }
    }
    assert!(failures.is_empty(), "{} of {} differ, first: {:#?}", failures.len(), cases.len(), &failures[..failures.len().min(5)]);
}
