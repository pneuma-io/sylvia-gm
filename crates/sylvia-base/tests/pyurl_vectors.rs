//! Compare sylvia_base::pyurl with vectors written by the frozen Python reference.
//!
//! Regenerate with `tests/vectors/generate_pyurl_vectors.py` (needs the snapshot's Python).

use serde_json::{json, Value};
use sylvia_base::pyurl::{unquote_strict, urlsplit, urlunsplit};

fn vectors() -> Value {
    serde_json::from_str(include_str!("vectors/pyurl_vectors.json")).expect("vector file is valid JSON")
}

#[test]
fn urlsplit_and_its_properties_match_python() {
    let vectors = vectors();
    let cases = vectors["urlsplit"].as_array().unwrap();
    assert!(cases.len() > 900);
    let mut failures = Vec::new();
    for case in cases {
        let url = case["url"].as_str().unwrap();
        let actual = match urlsplit(url) {
            Err(error) => json!({"url": url, "error": error.0}),
            Ok(parts) => {
                let mut record = json!({
                    "url": url,
                    "parts": [parts.scheme, parts.netloc, parts.path, parts.query, parts.fragment],
                    "hostname": parts.hostname(),
                    "username": parts.username(),
                    "password": parts.password(),
                    "unsplit": urlunsplit(&parts),
                });
                match parts.port() {
                    Ok(port) => record["port"] = json!(port),
                    Err(error) => record["port_error"] = json!(error.0),
                }
                record
            }
        };
        // Compared as text: key order is fixed on both sides, and Value equality would hide it.
        let expected = serde_json::to_string(case).unwrap();
        let mut sorted_actual: serde_json::Map<String, Value> = serde_json::Map::new();
        for key in case.as_object().unwrap().keys() {
            sorted_actual.insert(key.clone(), actual.get(key).cloned().unwrap_or(Value::Null));
        }
        if serde_json::to_string(&sorted_actual).unwrap() != expected
            || actual.as_object().unwrap().len() != case.as_object().unwrap().len()
        {
            failures.push(format!("{url:?}\n  expected {expected}\n  got      {actual}"));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} differ:\n{}",
        failures.len(),
        cases.len(),
        failures.iter().take(20).cloned().collect::<Vec<_>>().join("\n")
    );
}

#[test]
fn unquote_matches_python() {
    let vectors = vectors();
    for case in vectors["unquote"].as_array().unwrap() {
        let text = case["text"].as_str().unwrap();
        match (unquote_strict(text), case.get("result")) {
            (Ok(result), Some(expected)) => assert_eq!(result, expected.as_str().unwrap(), "{text:?}"),
            (Err(error), None) => assert_eq!(error.0, case["error"].as_str().unwrap(), "{text:?}"),
            (got, _) => panic!("{text:?}: got {got:?}, expected {case}"),
        }
    }
}
