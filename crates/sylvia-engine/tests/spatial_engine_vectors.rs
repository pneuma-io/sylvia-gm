//! Compare sylvia_engine::spatial's engine resolvers with the frozen Python
//! (`spatial._engine._resolve_*`), on this platform.
//!
//! Regenerate with `tests/vectors/generate_spatial_engine_vectors.py` on each platform (needs the
//! snapshot's Python): Windows writes `spatial_engine_vectors.windows.json`, Linux
//! `spatial_engine_vectors.linux.json`. `math.cos`, `math.sin` and `math.atan2` are the platform
//! C library's on both sides, so the two recordings may differ.
//!
//! Larger records are kept as `{key}_sha256`: the SHA-256 of Python's compact JSON in its own
//! key order, which `pyjson::dumps(.., Dump::WIRE)` reproduces. Every tenth engine case keeps its
//! whole result.

use serde_json::{json, Value as Json};
use sylvia_engine::spatial::{admit_spatial_query, admit_spatial_scene, resolve_spatial_query};

#[cfg(windows)]
const VECTORS: &str = include_str!("vectors/spatial_engine_vectors.windows.json");
#[cfg(not(windows))]
const VECTORS: &str = include_str!("vectors/spatial_engine_vectors.linux.json");

/// `record` with `key` replaced by `{key}_sha256` wherever the wanted record holds a hash.
fn hashed_like(mut record: Json, wanted: &Json, key: &str) -> Json {
    let hash_key = format!("{key}_sha256");
    if wanted.get(&hash_key).is_some() {
        if let Some(fields) = record.as_object_mut() {
            if let Some(value) = fields.shift_remove(key) {
                let text = sylvia_base::pyjson::dumps(&value, sylvia_base::pyjson::Dump::WIRE);
                let digest: String =
                    sylvia_base::hash::sha256(text.as_bytes()).iter().map(|b| format!("{b:02x}")).collect();
                fields.insert(hash_key, Json::String(digest));
            }
        }
    }
    record
}

#[test]
fn engine_resolves_as_python() {
    let vectors: Json = serde_json::from_str(VECTORS).expect("valid JSON");
    let scenes: Vec<_> = vectors["scenes"]
        .as_array()
        .unwrap()
        .iter()
        .map(|scene| admit_spatial_scene(scene).expect("an admissible scene"))
        .collect();
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() >= 7000);
    let mut failures = Vec::new();
    for (index, case) in cases.iter().enumerate() {
        let scene = &scenes[case["scene"].as_u64().unwrap() as usize];
        let query = admit_spatial_query(scene, &case["query"]).expect("an admissible query");
        let actual = match resolve_spatial_query(scene, &query) {
            Ok(result) => json!({"result": result}),
            Err(error) => {
                json!({"error": ["SpatialValidationError", error.to_string(), error.code, error.path]})
            }
        };
        let mut wanted = case.clone();
        let fields = wanted.as_object_mut().unwrap();
        fields.shift_remove("scene");
        fields.shift_remove("query");
        let actual = hashed_like(actual, &wanted, "result");
        let (actual, wanted) =
            (serde_json::to_string(&actual).unwrap(), serde_json::to_string(&wanted).unwrap());
        if actual != wanted {
            failures.push(format!(
                "case {index} ({}): {}\n  python {}\n  rust   {}",
                case["query"]["kind"],
                case["query"],
                &wanted[..wanted.len().min(900)],
                &actual[..actual.len().min(900)]
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} differ, first: {:#?}",
        failures.len(),
        cases.len(),
        &failures[..failures.len().min(4)]
    );
}

fn error_record(error: &sylvia_engine::spatial::SpatialError) -> Json {
    match error {
        sylvia_engine::spatial::SpatialError::Validation(error) => {
            json!({"error": ["SpatialValidationError", error.to_string(), error.code, error.path]})
        }
        sylvia_engine::spatial::SpatialError::Uncaught { error_type, message } => {
            json!({"error": [error_type, message]})
        }
    }
}

/// Text comparison: key order counts.
fn same(actual: &Json, wanted: &Json) -> bool {
    serde_json::to_string(actual).unwrap() == serde_json::to_string(wanted).unwrap()
}

#[test]
fn results_admit_as_python() {
    use sylvia_engine::spatial::admit_spatial_result;
    let vectors: Json = serde_json::from_str(VECTORS).expect("valid JSON");
    let scenes: Vec<_> = vectors["scenes"]
        .as_array()
        .unwrap()
        .iter()
        .map(|scene| admit_spatial_scene(scene).unwrap())
        .collect();
    let engine_cases = vectors["cases"].as_array().unwrap();
    let cases = vectors["results"].as_array().unwrap();
    assert!(cases.len() > 2500);
    let mut failures = Vec::new();
    for (index, case) in cases.iter().enumerate() {
        let scene = &scenes[case["scene"].as_u64().unwrap() as usize];
        // An engine result admitted as it is names its case, whose result is kept whole.
        let input = match case.get("case").and_then(Json::as_u64) {
            Some(number) => &engine_cases[number as usize]["result"],
            None => &case["input"],
        };
        let actual = match admit_spatial_result(scene, input) {
            Ok(result) => {
                json!({"payload": result.as_payload(), "kind": result.kind, "snapshot_id": result.snapshot_id})
            }
            Err(error) => error_record(&error),
        };
        let mut wanted = case.clone();
        let fields = wanted.as_object_mut().unwrap();
        for key in ["scene", "input", "case"] {
            fields.shift_remove(key);
        }
        let actual = hashed_like(actual, &wanted, "payload");
        if !same(&actual, &wanted) {
            failures.push(format!("result {index}\n  python {wanted}\n  rust   {actual}"));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} differ, first: {:#?}",
        failures.len(),
        cases.len(),
        &failures[..failures.len().min(4)]
    );
}

#[test]
fn engine_sessions_run_as_python() {
    use sylvia_engine::spatial::SpatialEngine;
    let vectors: Json = serde_json::from_str(VECTORS).expect("valid JSON");
    let mut failures = Vec::new();
    for (number, session) in vectors["sessions"].as_array().unwrap().iter().enumerate() {
        let base = &vectors["scenes"][session["scene"].as_u64().unwrap() as usize];
        let mut engine = SpatialEngine::new(base).expect("an admissible scene");
        let mut snapshots = vec![engine.snapshot().clone()];
        for (index, step) in session["steps"].as_array().unwrap().iter().enumerate() {
            let (actual, mut wanted) = if let Some(replacement) = step.get("replace") {
                let actual = match engine.replace_scene(replacement) {
                    Ok(admitted) => {
                        snapshots.push(admitted.clone());
                        json!({"snapshot_id": admitted.snapshot_id})
                    }
                    Err(error) => error_record(&error),
                };
                (actual, step.clone())
            } else {
                let snapshot = step["snapshot"].as_u64().map(|i| snapshots[i as usize].clone());
                let actual = match engine.query(&step["query"], snapshot.as_ref()) {
                    Ok(result) => json!({"payload": result.as_payload()}),
                    Err(error) => error_record(&error),
                };
                (actual, step.clone())
            };
            let fields = wanted.as_object_mut().unwrap();
            for key in ["replace", "query", "snapshot"] {
                fields.shift_remove(key);
            }
            let actual = hashed_like(actual, &wanted, "payload");
            if !same(&actual, &wanted) {
                failures.push(format!("session {number} step {index}\n  python {wanted}\n  rust   {actual}"));
            }
        }
    }
    assert!(
        failures.is_empty(),
        "{} differ, first: {:#?}",
        failures.len(),
        &failures[..failures.len().min(4)]
    );
}
