//! Compare sylvia_base::pyjson with vectors written by the frozen Python reference.
//!
//! Regenerate with `tests/vectors/generate_pyjson_vectors.py` (needs the snapshot's Python).

use serde_json::Value;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};

fn vectors() -> Value {
    let text = include_str!("vectors/pyjson_vectors.json");
    serde_json::from_str(text).expect("vector file is valid JSON")
}

fn encoding(name: &str) -> Dump {
    match name {
        "default" => Dump::DEFAULT,
        "sorted" => Dump::DEFAULT.sorted(true),
        "canonical" => Dump::CANONICAL,
        "wire" => Dump::WIRE,
        "compact_unicode" => Dump::WIRE.ascii(false),
        "unicode" => Dump::DEFAULT.ascii(false),
        "indent2" => Dump::INDENT2,
        "indent2_sorted_unicode" => Dump::INDENT2.sorted(true).ascii(false),
        other => panic!("vector file names an encoding this test does not know: {other}"),
    }
}

#[test]
fn floats_print_as_python_repr() {
    let vectors = vectors();
    let floats = vectors["floats"].as_array().unwrap();
    assert!(floats.len() > 5000, "expected the full float corpus, got {}", floats.len());
    let mut failures = Vec::new();
    for pair in floats {
        let input = pair[0].as_str().unwrap();
        let expected = pair[1].as_str().unwrap();
        let value = pyjson::loads(input).unwrap();
        let actual = pyjson::dumps(&value, Dump::DEFAULT);
        if actual != expected {
            failures.push(format!("{input}: python {expected}, rust {actual}"));
        }
    }
    assert!(
        failures.is_empty(),
        "{} float mismatches, first: {:?}",
        failures.len(),
        &failures[..failures.len().min(5)]
    );
}

#[test]
fn every_encoding_matches_python_bytes() {
    let vectors = vectors();
    let mut checked = 0;
    for case in vectors["cases"].as_array().unwrap() {
        let text = case["text"].as_str().unwrap();
        let value = pyjson::loads(text).unwrap();
        for (name, expected) in case["outputs"].as_object().unwrap() {
            let actual = pyjson::dumps(&value, encoding(name));
            assert_eq!(actual, expected.as_str().unwrap(), "input {text}, encoding {name}");
            checked += 1;
        }
        assert_eq!(
            sha256_hex(pyjson::canonical_bytes(&value)),
            case["canonical_sha256"].as_str().unwrap(),
            "canonical hash of {text}"
        );
    }
    assert!(checked >= 300, "expected the full case corpus, checked {checked}");
}

#[test]
fn sha256_matches_python_hexdigest() {
    let vectors = vectors();
    assert_eq!(sha256_hex(b""), vectors["hashes"]["empty"].as_str().unwrap());
    assert_eq!(
        sha256_hex("sylvia-gm\0\u{e9}".as_bytes()),
        vectors["hashes"]["sylvia_nul_e_acute"].as_str().unwrap()
    );
}
