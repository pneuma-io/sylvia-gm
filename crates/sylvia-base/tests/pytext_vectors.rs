//! Compare `pyjson::loads_bytes`, `pyre` templates, `pystr::splitlines` and
//! `pystr::isidentifier` with the frozen Python.
//!
//! Regenerate with `tests/vectors/generate_pytext_vectors.py` (needs the snapshot's Python).

use serde_json::json;
use sylvia_base::{pyjson, pyre, pystr, Json};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/pytext_vectors.json")).expect("vector file is valid JSON")
}

fn unhex(text: &str) -> Vec<u8> {
    (0..text.len()).step_by(2).map(|index| u8::from_str_radix(&text[index..index + 2], 16).unwrap()).collect()
}

fn report(failures: &[String], total: usize) {
    let shown: Vec<&String> = failures.iter().take(8).collect();
    assert!(failures.is_empty(), "{} of {total} differ: {shown:#?}", failures.len());
}

#[test]
fn loads_bytes_matches_python() {
    let vectors = vectors();
    let cases = vectors["loads_bytes"].as_array().unwrap();
    let mut failures = Vec::new();
    for case in cases {
        let data = unhex(case["data"].as_str().unwrap());
        let result = pyjson::loads_bytes(&data);
        let agrees = match (&result, case["outcome"][0].as_str().unwrap()) {
            // Row 3: Python holds a lone surrogate, which a Rust string cannot.
            (Err(error), "row 3") => error.error_type.is_none(),
            (Ok(value), "ok") => *value == case["outcome"][1],
            (Err(error), class) => {
                error.error_type == Some(class) && error.message == case["outcome"][1].as_str().unwrap()
            }
            _ => false,
        };
        if !agrees {
            failures.push(format!("{}: python {}, rust {result:?}", case["data"], case["outcome"]));
        }
    }
    report(&failures, cases.len());
}

#[test]
fn templates_match_python() {
    let vectors = vectors();
    let cases = vectors["templates"].as_array().unwrap();
    let mut failures = Vec::new();
    for case in cases {
        let template = case["template"].as_str().unwrap();
        let actual = match pyre::parse_template(template, 0) {
            Ok(pieces) => json!(["ok", pyre::expand(&pieces, &["x"])]),
            Err(error) => json!([error.class, error.message]),
        };
        if actual != case["outcome"] {
            failures.push(format!("{template:?}: python {}, rust {actual}", case["outcome"]));
        }
    }
    report(&failures, cases.len());
}

#[test]
fn splitlines_and_isidentifier_match_python() {
    let vectors = vectors();
    let cases = vectors["texts"].as_array().unwrap();
    let mut failures = Vec::new();
    for case in cases {
        let text = case["text"].as_str().unwrap();
        let actual =
            json!({"splitlines": pystr::splitlines(text), "isidentifier": pystr::isidentifier(text)});
        let expected = json!({"splitlines": case["splitlines"], "isidentifier": case["isidentifier"]});
        if actual != expected {
            failures.push(format!("{text:?}: python {expected}, rust {actual}"));
        }
    }
    report(&failures, cases.len());
}
