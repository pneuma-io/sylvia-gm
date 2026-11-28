//! Replay expression evaluations recorded while the frozen Python reference ran real SRD Lite
//! and Riichi Mahjong mechanics.
//!
//! Regenerate with `tests/vectors/generate_expression_recorded_vectors.py` (needs the snapshot's
//! Python), on each platform: it writes `expression_recorded_vectors.<platform>.json`. Each case holds the root values the Python evaluation read; Rust resolves the same
//! names to the same values and must give the same result or exception.

#[path = "common/tagged.rs"]
mod tagged;

use sylvia_base::testing::json_difference;

use serde_json::{json, Value as Json};
use std::cell::Cell;
use sylvia_engine::expr::{evaluate_expression, Dict, EvalContext, ExecutionBudget, Value};

/// The recording for this platform: float `**` uses the C library's `pow` in Python as in Rust,
/// and the Microsoft runtime and glibc can differ in the last bit.
fn vectors() -> Json {
    #[cfg(windows)]
    let text = include_str!("vectors/expression_recorded_vectors.windows.json");
    #[cfg(not(windows))]
    let text = include_str!("vectors/expression_recorded_vectors.linux.json");
    serde_json::from_str(text).expect("valid JSON")
}

#[test]
fn every_recorded_evaluation_replays_as_python() {
    let vectors = vectors();
    let values: Vec<Value> = vectors["values"].as_array().unwrap().iter().map(tagged::decode).collect();
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 20_000, "expected the full recording, got {}", cases.len());
    let mut failures = Vec::new();
    for case in cases {
        let text = case["text"].as_str().unwrap();
        let (mut vars, empty) = (Dict::new(), Dict::new());
        let (mut combat, mut ruleset, mut definitions) = (Value::None, Value::None, Value::None);
        for (name, index) in case["roots"].as_object().unwrap() {
            let value = values[usize::try_from(index.as_u64().unwrap()).unwrap()].clone();
            match name.as_str() {
                "combat" => combat = value,
                "ruleset" => ruleset = value,
                "definitions" => definitions = value,
                _ => {
                    vars.insert(name.clone(), value);
                }
            }
        }
        let budget = case.get("budget").map(|limits| ExecutionBudget {
            max_operations: limits[0].as_u64().unwrap(),
            operations: Cell::new(limits[1].as_u64().unwrap()),
            ..ExecutionBudget::default()
        });
        let ctx = EvalContext {
            params: &empty,
            vars: &vars,
            entities: &empty,
            combat_state: &combat,
            ruleset_config: &ruleset,
            definitions: &definitions,
            budget: budget.as_ref(),
            required_inputs: false,
        };
        let actual = match evaluate_expression(text, &ctx) {
            Ok(value) => json!({"result": tagged::encode(&value)}),
            Err(error) => json!({"error": error.class_name(), "message": error.message()}),
        };
        let expected = match case.get("result") {
            Some(result) => json!({"result": result}),
            None => json!({"error": case["error"], "message": case["message"]}),
        };
        if let Some(difference) = json_difference(&actual, &expected) {
            failures.push(format!("{text}: {difference}
  python {expected}, rust {actual}"));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} differ, first: {:#?}",
        failures.len(),
        cases.len(),
        &failures[..failures.len().min(8)]
    );
}
