//! Compare sylvia_app::character_rules with the frozen Python's `validate_character_rules` and
//! `campaign_character_state`.
//!
//! Regenerate with `tests/vectors/generate_character_rules_vectors.py` (needs the snapshot's
//! Python). The shipped program and Plans are read from the repository.

use serde_json::json;
use std::path::PathBuf;
use sylvia_app::character_rules::{campaign_character_state, validate_character_rules};
use sylvia_base::hash::sha256_hex;
use sylvia_base::pystr::INT_RANGE_MESSAGE;
use sylvia_base::{pyjson, Json};
use sylvia_domain::error::DomainError;
use sylvia_domain::package_library::canonical_json_bytes;

const VECTORS: &str = include_str!("vectors/character_rules_vectors.json");

fn repository() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("..").join("..")
}

fn read_json(relative: &str) -> Json {
    let text = std::fs::read_to_string(repository().join(relative)).unwrap();
    pyjson::loads(&text).unwrap()
}

/// `{"$repeat": [text, n]}` as the text repeated.
fn expand(value: &Json) -> Json {
    match value {
        Json::Object(map) if map.len() == 1 && map.contains_key("$repeat") => {
            let parts = map["$repeat"].as_array().unwrap();
            Json::String(parts[0].as_str().unwrap().repeat(parts[1].as_u64().unwrap() as usize))
        }
        Json::Object(map) => {
            Json::Object(map.iter().map(|(key, item)| (key.clone(), expand(item))).collect())
        }
        Json::Array(items) => Json::Array(items.iter().map(expand).collect()),
        other => other.clone(),
    }
}

fn step<'a>(value: &'a mut Json, part: &Json) -> &'a mut Json {
    match part {
        Json::String(key) => value.get_mut(key.as_str()).unwrap(),
        index => value.get_mut(index.as_u64().unwrap() as usize).unwrap(),
    }
}

fn apply(document: &Json, edits: &Json) -> Json {
    let mut document = document.clone();
    for edit in edits.as_array().into_iter().flatten() {
        let (op, path, value) = (edit[0].as_str().unwrap(), edit[1].as_array().unwrap(), &edit[2]);
        let mut parent = &mut document;
        for part in &path[..path.len() - 1] {
            parent = step(parent, part);
        }
        let last = &path[path.len() - 1];
        if op == "delete" {
            match last {
                Json::String(key) => {
                    parent.as_object_mut().unwrap().shift_remove(key.as_str());
                }
                index => {
                    parent.as_array_mut().unwrap().remove(index.as_u64().unwrap() as usize);
                }
            }
        } else {
            match last {
                Json::String(key) => {
                    parent.as_object_mut().unwrap().insert(key.clone(), expand(value));
                }
                index => *step(parent, index) = expand(value),
            }
        }
    }
    document
}

/// The generator's `key_order(value)`: every object's keys, depth first in iteration order.
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

fn error_outcome(error: &DomainError, with_details: bool) -> Json {
    if !with_details {
        return json!([error.python_class(), error.message(), [], null]);
    }
    let diagnostics: Vec<Json> =
        error.diagnostics().iter().map(|item| Json::Object(item.as_payload())).collect();
    json!([error.python_class(), error.message(), diagnostics, error.code()])
}

#[test]
fn character_rules_match_python() {
    let vectors = pyjson::loads(VECTORS).unwrap();
    let record = read_json(vectors["srd"].as_str().unwrap());
    let base = &record["participant_artifacts"][0]["initial_state_validation"];
    let mut failures = Vec::new();

    let validate = vectors["validate"].as_array().unwrap();
    for case in validate {
        let spec = &case["program"];
        let program = if spec.is_null() {
            None
        } else if spec.get("base").is_some() {
            Some(apply(base, spec.get("edits").unwrap_or(&Json::Null)))
        } else {
            Some(expand(&spec["program"]))
        };
        let result = validate_character_rules(program.as_ref());
        if case.get("row").is_some() {
            // Differences row 5: an integer beyond i64 is refused before the runtime validator.
            let refused = matches!(&result, Err(error) if error.python_class() == "ValueError"
                && error.message() == INT_RANGE_MESSAGE);
            if !refused {
                failures.push(format!("validate row 5 {spec}: rust {result:?}"));
            }
            continue;
        }
        let actual = match result {
            Ok(None) => json!(["ok", null]),
            Ok(Some(value)) => {
                let value = Json::Object(value);
                json!([
                    "ok",
                    sha256_hex(canonical_json_bytes(&value)),
                    sha256_hex(canonical_json_bytes(&key_order(&value)))
                ])
            }
            Err(error) => error_outcome(&error, true),
        };
        if actual != case["outcome"] {
            failures.push(format!("validate {spec}: python {}, rust {actual}", case["outcome"]));
        }
    }

    let campaign = vectors["campaign"].as_array().unwrap();
    let mut plans = std::collections::HashMap::new();
    for case in campaign {
        let path = case["plan"].as_str().unwrap();
        let plan = plans.entry(path.to_owned()).or_insert_with(|| read_json(path));
        let document = apply(plan, &case["edits"]);
        let actual = match campaign_character_state(&document, case["namespace"].as_str().unwrap()) {
            Ok(result) => {
                let keys: Vec<&str> = result.keys().map(String::as_str).collect();
                json!(["ok", keys, result])
            }
            Err(error) => error_outcome(&error, false),
        };
        if actual != case["outcome"] {
            failures.push(format!(
                "campaign {path} {}: python {}, rust {actual}",
                case["edits"], case["outcome"]
            ));
        }
    }

    assert!(
        failures.is_empty(),
        "{} of {} cases differ: {:#?}",
        failures.len(),
        validate.len() + campaign.len(),
        &failures[..failures.len().min(8)]
    );
}
