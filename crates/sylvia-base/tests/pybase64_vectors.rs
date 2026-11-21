//! Compare sylvia_base::pybase64 with vectors written by the frozen Python reference.
//!
//! Regenerate with `tests/vectors/generate_pybase64_vectors.py` (needs the snapshot's Python).

use serde_json::{json, Value};
use sylvia_base::pybase64;

fn vectors() -> Value {
    serde_json::from_str(include_str!("vectors/pybase64_vectors.json")).expect("vector file is valid JSON")
}

fn record(result: Result<Vec<u8>, String>, class: &str) -> Value {
    match result {
        Ok(bytes) => json!({"ok": bytes.iter().map(|b| format!("{b:02x}")).collect::<String>()}),
        Err(message) => json!({"error": class, "message": message}),
    }
}

#[test]
fn decoding_matches_python_in_both_modes() {
    let vectors = vectors();
    let decodes = vectors["decodes"].as_array().unwrap();
    assert!(decodes.len() > 3000);
    for case in decodes {
        let text = case["text"].as_str().unwrap();
        // b64decode raises ValueError for non-ASCII text, binascii.Error otherwise.
        let class = if text.is_ascii() { "Error" } else { "ValueError" };
        assert_eq!(record(pybase64::b64decode_validate(text), class), case["validate"], "validate {text:?}");
        if let Some(expected) = case.get("decodebytes") {
            assert_eq!(
                record(pybase64::a2b_base64(text.as_bytes(), false), "Error"),
                *expected,
                "decodebytes {text:?}"
            );
        }
    }
}

#[test]
fn encoding_matches_python() {
    let vectors = vectors();
    for case in vectors["encodes"].as_array().unwrap() {
        let hex = case["data"].as_str().unwrap();
        let data: Vec<u8> =
            (0..hex.len()).step_by(2).map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap()).collect();
        assert_eq!(pybase64::b64encode(&data), case["b64encode"].as_str().unwrap());
        assert_eq!(pybase64::encodebytes(&data), case["encodebytes"].as_str().unwrap());
    }
}
