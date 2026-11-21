//! Compare sylvia_base::pystr's int(), float() and repr() with the frozen Python reference.
//!
//! Regenerate with `tests/vectors/generate_number_vectors.py` (needs the snapshot's Python).

use serde_json::Value;
use sylvia_base::pyjson;
use sylvia_base::pystr::{parse_float, parse_int, repr, repr_json, IntParseError, INT_RANGE_MESSAGE};

fn vectors() -> Value {
    serde_json::from_str(include_str!("vectors/number_vectors.json")).expect("valid JSON")
}

fn report(name: &str, failures: &[String], checked: usize) {
    assert!(checked > 1000, "{name}: expected the full corpus, got {checked}");
    assert!(
        failures.is_empty(),
        "{name}: {} of {checked} differ, first: {:?}",
        failures.len(),
        &failures[..failures.len().min(6)]
    );
}

#[test]
fn int_matches_python() {
    let vectors = vectors();
    let cases = vectors["int"].as_array().unwrap();
    let mut failures = Vec::new();
    let mut beyond = 0;
    for case in cases {
        let text = case["text"].as_str().unwrap();
        let actual = parse_int(text);
        let same = match (&actual, case.get("value").and_then(Value::as_str)) {
            (_, Some(expected)) => match expected.parse::<i64>() {
                Ok(exact) => actual == Ok(exact),
                // Python's exact integer beyond i64: the declared refusal (differences.md row 5).
                Err(_) => {
                    beyond += 1;
                    let negative = expected.starts_with('-');
                    actual == Err(IntParseError::OutOfRange { negative })
                        && actual.as_ref().unwrap_err().refusal().message == INT_RANGE_MESSAGE
                }
            },
            (Err(IntParseError::Invalid(error)), None) => error.message == case["error"].as_str().unwrap(),
            _ => false,
        };
        if !same {
            failures.push(format!("{text:?}: python {case}, rust {actual:?}"));
        }
    }
    report("int", &failures, cases.len());
    assert!(beyond >= 10, "expected integers beyond i64 in the corpus, got {beyond}");
}

#[test]
fn float_matches_python() {
    let vectors = vectors();
    let cases = vectors["float"].as_array().unwrap();
    let mut failures = Vec::new();
    for case in cases {
        let text = case["text"].as_str().unwrap();
        let actual = parse_float(text);
        let same = match (&actual, case.get("repr").and_then(Value::as_str)) {
            (Ok(value), Some(expected)) => {
                let text = match *value {
                    v if v.is_nan() => "nan".to_owned(),
                    v if v.is_infinite() => if v > 0.0 { "inf" } else { "-inf" }.to_owned(),
                    v => pyjson::float_repr(v),
                };
                text == expected && value.is_sign_negative() == case["negative"].as_bool().unwrap()
            }
            (Err(error), None) => error.message == case["error"].as_str().unwrap(),
            _ => false,
        };
        if !same {
            failures.push(format!("{text:?}: python {case}, rust {actual:?}"));
        }
    }
    report("float", &failures, cases.len());
}

#[test]
fn repr_matches_python() {
    let vectors = vectors();
    let cases = vectors["repr"].as_array().unwrap();
    let mut failures = Vec::new();
    for pair in cases {
        let actual = repr(pair[0].as_str().unwrap());
        if actual != pair[1].as_str().unwrap() {
            failures.push(format!("{:?}: python {:?}, rust {actual:?}", pair[0], pair[1]));
        }
    }
    report("repr", &failures, cases.len());
}

#[test]
fn repr_of_json_values_matches_python() {
    let vectors = vectors();
    let cases = vectors["repr_json"].as_array().unwrap();
    let mut failures = Vec::new();
    for pair in cases {
        let text = pair[0].as_str().unwrap();
        let value = pyjson::loads(text).expect("vector JSON loads");
        let actual = repr_json(&value);
        if actual != pair[1].as_str().unwrap() {
            failures.push(format!("{text}: python {:?}, rust {actual:?}", pair[1]));
        }
    }
    report("repr_json", &failures, cases.len());
}
