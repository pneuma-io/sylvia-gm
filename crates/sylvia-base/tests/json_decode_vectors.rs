//! `sylvia_base::pyjson::loads` failures against CPython's `json.loads`, from vectors written
//! by `tests/vectors/generate_json_decode_vectors.py`.
//!
//! Where Python raises `JSONDecodeError`, Rust must refuse with the same class and the same
//! text (message, line, column and code-point position). Where Python reads the text, Rust must
//! read the same value, or refuse it with its own "Invalid JSON" message for one of the declared
//! limits (`docs/rust-port/differences.md`). Where Python raises something else (a
//! `RecursionError` for very deep nesting), Rust must refuse.

use serde_json::Value;
use sylvia_base::pyjson::{self, Dump};

#[test]
fn decode_errors_match_python() {
    let record: Value = serde_json::from_str(include_str!("vectors/json_decode_vectors.json")).unwrap();
    let cases = record["cases"].as_array().unwrap();
    assert!(cases.len() > 2500, "expected the full corpus, got {}", cases.len());
    let mut failures = Vec::new();
    let mut declared = 0;
    for case in cases {
        let text = case["text"].as_str().unwrap();
        let got = pyjson::loads(text);
        match (case.get("error").and_then(Value::as_str), got) {
            (Some("JSONDecodeError"), Err(error)) => {
                let want = case["message"].as_str().unwrap();
                if error.message != want || error.error_type != Some("JSONDecodeError") {
                    failures.push(format!(
                        "{text:?}: python {want:?}, rust {:?} ({:?})",
                        error.message, error.error_type
                    ));
                }
            }
            (Some(_), Err(_)) => {}
            (Some(kind), Ok(value)) => failures.push(format!("{text:?}: python {kind}, rust read {value}")),
            (None, Ok(value)) => {
                let want = &case["ok"];
                let dumped = pyjson::dumps(&value, Dump::DEFAULT.sorted(true));
                if want.as_str() != Some(dumped.as_str()) {
                    failures.push(format!("{text:?}: python {want}, rust {dumped}"));
                }
            }
            (None, Err(error)) => {
                // A text Python reads may only be refused for a declared limit, never with a
                // JSONDecodeError Python would not raise.
                if error.error_type == Some("JSONDecodeError") || !declared_limit(text, &error.message) {
                    failures.push(format!("{text:?}: python read it, rust {:?}", error.message));
                }
                declared += 1;
            }
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} cases differ:\n{}",
        failures.len(),
        cases.len(),
        failures.join("\n")
    );
    assert!(declared > 0, "the corpus exercises the declared limits");
}

/// Whether a refusal of text Python reads is one of the declared limits of
/// `docs/rust-port/differences.md`: an integer beyond 64 bits (row 1), `NaN`, `Infinity` or a
/// float beyond the `f64` range (row 2), a lone surrogate (row 3), or nesting 128 deep (row 4).
fn declared_limit(text: &str, message: &str) -> bool {
    let Some(reason) = message.strip_prefix("Invalid JSON: ") else { return false };
    reason.contains("outside the supported 64-bit range")
        || reason.starts_with("number out of range")
        || (reason.starts_with("expected value") && (text.contains("NaN") || text.contains("Infinity")))
        || reason.starts_with("lone leading surrogate in hex escape")
        || reason.starts_with("unexpected end of hex escape")
        || reason.starts_with("recursion limit exceeded")
}
