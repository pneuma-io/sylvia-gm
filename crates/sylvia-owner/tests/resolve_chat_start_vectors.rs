//! `chat_start::resolve_chat_start` against Python's `_chat_start.resolve_chat_start`
//! (`tests/vectors/generate_resolve_chat_start_vectors.py`): every catalog row, role, saved
//! `chat_defaults` mapping and set of chosen parameters must give Python's `ChatStart`, or its
//! failure with the same class, code and message.

use serde_json::{json, Value as J};
use sylvia_base::CodedError;
use sylvia_owner::chat_start::resolve_chat_start;
use sylvia_provider::config::data_from_json;

fn failure(error: CodedError) -> J {
    let class = error.error_type.unwrap_or("CommandRejected");
    if class == "CommandRejected" {
        json!({"error": {"class": class, "code": error.code, "message": error.message}})
    } else {
        json!({"error": {"class": class, "message": error.message}})
    }
}

#[test]
fn resolved_starts_match_python() {
    let document: J =
        serde_json::from_str(include_str!("vectors/resolve_chat_start_vectors.json")).expect("valid JSON");
    let rows = document["rows"].as_array().unwrap();
    let outcomes = document["outcomes"].as_array().unwrap();
    let cases = document["cases"].as_array().unwrap();
    assert!(cases.len() > 7000, "expected the full corpus, got {}", cases.len());
    let mut differences = Vec::new();
    for case in cases {
        let defaults = &document["defaults"][case["defaults"].as_u64().unwrap() as usize];
        let params = document["params"][case["params"].as_u64().unwrap() as usize].as_object().unwrap();
        let defaults = data_from_json(defaults).unwrap();
        let role = case["role"].as_str().unwrap();
        let got = match case["row"].as_u64() {
            Some(index) => {
                let row = &rows[index as usize];
                let (provider, model) =
                    (row["provider_id"].as_str().unwrap(), row["model_id"].as_str().unwrap());
                resolve_chat_start(std::slice::from_ref(row), Some(&defaults), role, provider, model, params)
            }
            None => resolve_chat_start(rows, Some(&defaults), role, "p", "p/none", params),
        };
        let got = match got {
            Ok(start) => json!({"value": {
                "provider_id": start.provider_id, "model_id": start.model_id,
                "reasoning_effort": start.reasoning_effort, "speed": start.speed,
            }}),
            Err(error) => failure(error),
        };
        let want = &outcomes[case["outcome"].as_u64().unwrap() as usize];
        if &got != want {
            differences.push(format!("{case}: python {want}, rust {got}"));
        }
    }
    assert!(
        differences.is_empty(),
        "{} of {} differ:\n{}",
        differences.len(),
        cases.len(),
        differences.join("\n")
    );
}
