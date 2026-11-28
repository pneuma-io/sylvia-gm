//! Compare sylvia-app::player_actions' player-control checks and parameter bindings with the
//! frozen Python.
//!
//! Regenerate with `tests/vectors/generate_player_action_binding_vectors.py` (needs the
//! snapshot's Python).

use serde_json::{json, Value as Json};
use sylvia_app::player_actions::{
    resolve_inventory_action_parameters, resolve_party_action_parameters, retain_player_action_origin,
    validate_player_control,
};
use sylvia_base::testing::{assert_same_json, json_difference};
use sylvia_domain::authority::MechanicOutcome;
use sylvia_domain::error::{DomainError, DomainResult};

const VECTORS: &str = include_str!("vectors/player_action_binding_vectors.json");

fn record<T: Into<Json>>(result: DomainResult<T>) -> Json {
    match result {
        Ok(value) => json!({"ok": value.into()}),
        Err(error) => {
            let mut record = json!({"error": [error.python_class(), error.message()]});
            if let DomainError::Rejected { .. } = error {
                record["code"] = json!(error.code().expect("a rejection has a code"));
            }
            record
        }
    }
}

fn object(value: &Json) -> &serde_json::Map<String, Json> {
    value.as_object().expect("an object")
}

fn run(case: &Json) -> Json {
    match case["function"].as_str().unwrap() {
        "validate" => record(
            validate_player_control(
                case["scope"].as_object(),
                object(&case["parameters"]),
                case["actor_parameter"].as_str(),
            )
            .map(|()| Json::Null),
        ),
        "party" => record(resolve_party_action_parameters(
            object(&case["action"]),
            &case["selection"],
            &case["data"],
            case["allow_entity_ids"].as_bool().unwrap(),
        )),
        "inventory" => record(resolve_inventory_action_parameters(
            object(&case["action"]),
            &case["selection"],
            &case["data"],
        )),
        "retain" => {
            let outcome = MechanicOutcome {
                result: object(&case["result"]).clone(),
                consequences: case["consequences"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|item| object(item).clone())
                    .collect(),
                ..MechanicOutcome::default()
            };
            let kept = retain_player_action_origin(outcome, case["origin"].as_object());
            json!({"ok": {"result": kept.result, "consequences": kept.consequences}})
        }
        other => panic!("unknown function {other}"),
    }
}

#[test]
fn player_action_bindings_match_python() {
    let vectors: Json = serde_json::from_str(VECTORS).unwrap();
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 1500, "the vector file is complete");
    let mut failures = Vec::new();
    for case in cases {
        let actual = run(case);
        if json_difference(&actual, &case["expected"]).is_some() {
            failures.push(format!("{}: expected {}, got {}", case["label"], case["expected"], actual));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} cases differ:\n{}",
        failures.len(),
        cases.len(),
        failures.join("\n")
    );
}

#[test]
fn retained_origin_is_the_owner_marker() {
    let origin = json!({"surface": "party"});
    let kept = retain_player_action_origin(MechanicOutcome::default(), origin.as_object());
    assert_eq!(
        sylvia_domain::player_actions::PLAYER_ACTION_ORIGIN,
        kept.consequences[0]["kind"].as_str().unwrap(),
    );
    assert_same_json(&kept.consequences[0]["origin"], &origin, "the retained origin");
}
