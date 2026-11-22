//! Compare sylvia_engine::expr's evaluator with the frozen Python reference over a generated
//! corpus in one rich context.
//!
//! Regenerate with `tests/vectors/generate_expression_eval_vectors.py` (needs the snapshot's
//! Python).

#[path = "common/tagged.rs"]
mod tagged;

use serde_json::{json, Value as Json};
use sylvia_engine::expr::{evaluate_expression, EvalContext, EvalError, ExecutionBudget, Value};

fn vectors() -> Json {
    serde_json::from_str(include_str!("vectors/expression_eval_vectors.json")).expect("valid JSON")
}

/// The declared refusals (`differences.md`): an integer beyond `i64`, a complex result.
fn refusal(kind: &str) -> Json {
    let message = match kind {
        "int_range" => sylvia_base::pystr::INT_RANGE_MESSAGE,
        "complex" => "complex results are not supported",
        other => panic!("unknown refusal {other}"),
    };
    json!({"error": message, "class": "ValueError"})
}

pub fn outcome(result: &Result<Value, EvalError>) -> Json {
    match result {
        Ok(value) => json!({"result": tagged::encode(value)}),
        Err(error) => json!({"error": error.message(), "class": error.class_name()}),
    }
}

/// The first failure for each expression prefix, so one bug does not hide the others.
fn one_per_kind(failures: &[String]) -> Vec<&String> {
    let mut seen: Vec<&String> = Vec::new();
    for failure in failures {
        let key: String = failure.chars().take(12).collect();
        if !seen.iter().any(|kept| kept.starts_with(&key)) {
            seen.push(failure);
        }
    }
    seen.truncate(20);
    seen
}

#[test]
fn every_generated_expression_evaluates_as_python_does() {
    let vectors = vectors();
    let context = &vectors["context"];
    let params = tagged::decode_dict(&context["params"]);
    let vars = tagged::decode_dict(&context["vars"]);
    let entities = tagged::decode_dict(&context["entities"]);
    let combat_state = tagged::decode(&context["combat_state"]);
    let ruleset_config = tagged::decode(&context["ruleset_config"]);
    let definitions = tagged::decode(&context["definitions"]);
    let max_operations = context["max_operations"].as_u64().unwrap();
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 25_000, "expected the full corpus");
    let (mut failures, mut refused, mut skipped) = (Vec::new(), 0, 0);
    for case in cases {
        if case.get("nondeterministic").is_some() {
            skipped += 1;
            continue;
        }
        let text = case["text"].as_str().unwrap();
        let budget = ExecutionBudget { max_operations, ..ExecutionBudget::default() };
        let ctx = EvalContext {
            params: &params,
            vars: &vars,
            entities: &entities,
            combat_state: &combat_state,
            ruleset_config: &ruleset_config,
            definitions: &definitions,
            budget: Some(&budget),
            required_inputs: false,
        };
        let actual = outcome(&evaluate_expression(text, &ctx));
        let expected = match case.get("refuse").and_then(Json::as_str) {
            Some(kind) => {
                refused += 1;
                refusal(kind)
            }
            None => {
                let mut expected = case.clone();
                expected.as_object_mut().unwrap().remove("text");
                expected
            }
        };
        if actual != expected {
            failures.push(format!("{text}: python {expected}, rust {actual}"));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} differ, one per kind: {:#?}",
        failures.len(),
        cases.len(),
        one_per_kind(&failures)
    );
    assert!(refused > 100 && skipped <= 2, "refused {refused}, skipped {skipped}");
}
