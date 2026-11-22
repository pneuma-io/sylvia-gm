//! Compare sylvia_app::ruleset_source with the frozen Python's `compile_ruleset_source`.
//!
//! Regenerate with `tests/vectors/generate_ruleset_source_vectors.py` (needs the snapshot's
//! Python). The shipped Riichi Mahjong source is read from the repository.

use serde_json::json;
use std::path::PathBuf;
use sylvia_app::ruleset_source::compile_ruleset_source;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{pyyaml, Json};
use sylvia_domain::error::DomainError;
use sylvia_domain::package_library::canonical_json_bytes;

const VECTORS: &str = include_str!("vectors/ruleset_source_vectors.json");

fn step<'a>(value: &'a mut Json, part: &Json) -> &'a mut Json {
    match part {
        Json::String(key) => value.get_mut(key.as_str()).unwrap(),
        index => value.get_mut(index.as_u64().unwrap() as usize).unwrap(),
    }
}

/// The generator's `apply(document, edits)`.
fn apply(document: &Json, edits: &Json) -> Json {
    let mut document = document.clone();
    for edit in edits.as_array().into_iter().flatten() {
        let (op, path, value) = (edit[0].as_str().unwrap(), edit[1].as_array().unwrap(), &edit[2]);
        let mut parent = &mut document;
        for part in &path[..path.len() - 1] {
            parent = step(parent, part);
        }
        let last = &path[path.len() - 1];
        match (op, last) {
            ("delete", Json::String(key)) => {
                parent.as_object_mut().unwrap().shift_remove(key.as_str());
            }
            ("delete", index) => {
                parent.as_array_mut().unwrap().remove(index.as_u64().unwrap() as usize);
            }
            ("append", _) => step(parent, last).as_array_mut().unwrap().push(value.clone()),
            (_, Json::String(key)) => {
                parent.as_object_mut().unwrap().insert(key.clone(), value.clone());
            }
            (_, index) => *step(parent, index) = value.clone(),
        }
    }
    document
}

fn key_order(value: &Json) -> Json {
    let mut order = Vec::new();
    let mut pending = vec![value];
    while let Some(item) = pending.pop() {
        match item {
            Json::Object(map) => {
                order.push(Json::Array(map.keys().map(|key| Json::from(key.as_str())).collect()));
                pending.extend(map.values().rev());
            }
            Json::Array(items) => pending.extend(items.iter().rev()),
            _ => {}
        }
    }
    Json::Array(order)
}

fn error_outcome(error: &DomainError) -> Json {
    let diagnostics: Vec<Json> =
        error.diagnostics().iter().map(|item| Json::Object(item.as_payload())).collect();
    json!([error.python_class(), error.message(), diagnostics, error.code()])
}

#[test]
fn ruleset_source_matches_python() {
    let vectors = pyjson::loads(VECTORS).unwrap();
    let root = PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..");
    let riichi_bytes = std::fs::read(root.join(vectors["riichi"].as_str().unwrap())).unwrap();
    let riichi = pyyaml::safe_load(std::str::from_utf8(&riichi_bytes).unwrap()).unwrap().to_json().unwrap();
    let small = &vectors["small"];
    let mut failures = Vec::new();
    let cases = vectors["cases"].as_array().unwrap();
    for case in cases {
        let spec = &case["source"];
        let source: Vec<u8> = if spec.get("riichi").is_some() {
            riichi_bytes.clone()
        } else if let Some(edits) = spec.get("riichi_edits") {
            pyjson::dumps(&apply(&riichi, edits), Dump::DEFAULT).into_bytes()
        } else if let Some(edits) = spec.get("small_edits") {
            pyjson::dumps(&apply(small, edits), Dump::DEFAULT).into_bytes()
        } else if let Some(text) = spec.get("latin1") {
            text.as_str().unwrap().chars().map(|c| u8::try_from(u32::from(c)).unwrap()).collect()
        } else {
            spec["text"].as_str().unwrap().as_bytes().to_vec()
        };
        let result = compile_ruleset_source(&source);
        if case.get("row").is_some() {
            // Differences row 5: an integer beyond i64 is refused while the YAML is read.
            let refused = matches!(&result, Err(error)
                if error.diagnostics().first().is_some_and(|item| item.code == "ruleset_source_yaml_invalid"));
            if !refused {
                failures.push(format!("row 5 {spec}: rust {result:?}"));
            }
            continue;
        }
        let actual = match result {
            Ok(compiled) => {
                let draft = compiled.draft;
                let record = Json::Object(draft.record);
                let payloads: Vec<Json> = draft
                    .payloads
                    .iter()
                    .map(|(path, data)| json!([path, sha256_hex(data), data.len()]))
                    .collect();
                let mut result = json!({
                    "record": sha256_hex(canonical_json_bytes(&record)),
                    "record_order": sha256_hex(canonical_json_bytes(&key_order(&record))),
                    "payloads": payloads,
                    "features": compiled.required_engine_features,
                    "format_version": draft.format_version,
                });
                if spec.get("show").is_some_and(|show| show == &Json::Bool(true)) {
                    let runtime =
                        &draft.payloads.iter().find(|(path, _)| path == "runtime/ruleset.yaml").unwrap().1;
                    result["runtime_yaml"] = Json::from(String::from_utf8(runtime.clone()).unwrap());
                }
                json!(["ok", result])
            }
            Err(error) => error_outcome(&error),
        };
        if actual != case["outcome"] {
            let shown = spec.to_string();
            failures.push(format!(
                "{}: python {}, rust {actual}",
                &shown[..shown.len().min(160)],
                case["outcome"]
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} differ: {:#?}",
        failures.len(),
        cases.len(),
        &failures[..failures.len().min(6)]
    );
}
