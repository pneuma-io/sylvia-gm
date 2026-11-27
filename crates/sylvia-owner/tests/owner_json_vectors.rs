//! `sylvia_owner::owner_json::json_value` against the real `owner_process._json_value`
//! (`tests/vectors/generate_owner_json_vectors.py`): 30 values covering the mapping kinds that
//! never cross the wire as saved, nested and at the top, with the shapes Python tolerates and the
//! `TypeError`s it raises. Compared as text, so key order counts.

use sylvia_base::Json;

#[test]
fn owner_json_matches_python() {
    let path = std::path::Path::new(env!("CARGO_MANIFEST_DIR")).join("tests/vectors/owner_json_vectors.json");
    let text = std::fs::read_to_string(&path)
        .unwrap_or_else(|error| panic!("cannot read {}: {error}", path.display()));
    let vectors: Json = serde_json::from_str(&text).unwrap();
    let cases = vectors["cases"].as_array().unwrap();
    assert_eq!(cases.len(), 30);
    let mut failures = Vec::new();
    for (index, case) in cases.iter().enumerate() {
        let got = sylvia_owner::owner_json::json_value(&case["value"]);
        let ok = match (&got, case.get("ok"), case.get("error")) {
            // As text: `serde_json`'s equality ignores key order, which Python's projection fixes.
            (Ok(value), Some(want), None) => {
                let (value, want) = (value.to_string(), want.to_string());
                value == want
            }
            (Err(error), None, Some(want)) => {
                error.code == "internal_error"
                    && error.error_type == want["class"].as_str()
                    && error.message == want["message"].as_str().unwrap()
            }
            _ => false,
        };
        if !ok {
            failures.push(format!("#{index}\n  Rust   {got:?}\n  Python {case}"));
        }
    }
    assert!(failures.is_empty(), "{} differ:\n{}", failures.len(), failures.join("\n"));
}
