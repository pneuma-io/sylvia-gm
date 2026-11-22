//! Compare sylvia_engine::pymath with CPython's `math.hypot` and `math.dist`, bit for bit.
//!
//! Regenerate with `tests/vectors/generate_pymath_vectors.py` (needs the snapshot's Python).

use serde_json::Value as Json;
use sylvia_engine::pymath::{dist, hypot};

fn floats(value: &Json) -> Vec<f64> {
    value.as_array().unwrap().iter().map(|bits| f64::from_bits(bits.as_u64().unwrap())).collect()
}

/// NaN results compare as NaN (CPython returns its own NaN constant).
fn same(actual: f64, wanted: u64) -> bool {
    let wanted = f64::from_bits(wanted);
    (actual.is_nan() && wanted.is_nan()) || actual.to_bits() == wanted.to_bits()
}

#[test]
fn hypot_and_dist_match_cpython() {
    let vectors: Json =
        serde_json::from_str(include_str!("vectors/pymath_vectors.json")).expect("valid JSON");
    let mut failures = Vec::new();
    for case in vectors["hypot"].as_array().unwrap() {
        let input = floats(&case["input"]);
        let actual = hypot(&input);
        if !same(actual, case["result"].as_u64().unwrap()) {
            failures.push(format!(
                "hypot{input:?} = {actual:e}, python {:e}",
                f64::from_bits(case["result"].as_u64().unwrap())
            ));
        }
    }
    for case in vectors["dist"].as_array().unwrap() {
        let (p, q) = (floats(&case["p"]), floats(&case["q"]));
        let actual = dist(&p, &q);
        if !same(actual, case["result"].as_u64().unwrap()) {
            failures.push(format!(
                "dist({p:?}, {q:?}) = {actual:e}, python {:e}",
                f64::from_bits(case["result"].as_u64().unwrap())
            ));
        }
    }
    assert!(vectors["hypot"].as_array().unwrap().len() > 20000);
    assert!(
        failures.is_empty(),
        "{} differ, first: {:#?}",
        failures.len(),
        &failures[..failures.len().min(5)]
    );
}
