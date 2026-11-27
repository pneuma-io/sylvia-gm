//! Compare one mechanic executed through MCP (T3m-2) with Python, on a real turn.
//!
//! Regenerate with `tests/vectors/generate_execution_vectors.py`: the `srd-mechanics-turn`
//! skill check's `StatelessMcpMechanics.execute_action`, repeated with variants (a choice, the
//! owner's refusals of every class, the castable spell rename, a `ValueError` and a `KeyError`,
//! the tool missing at the revalidation or at the call). Rust is answered every owner call and
//! `uuid4` value Python's call met, in order; the Full Audit records and the mechanics
//! diagnostic are among the calls. Its receipt must be Python's, or its error Python's class,
//! code, message and correction fields.

mod common;

use common::{recorded_execution, Recorded};
use serde_json::Value as J;
use sylvia_base::testing::assert_same_json;
use std::sync::Mutex;
use sylvia_agent::stateless_mcp::execute::execute_action;
use sylvia_agent::stateless_mcp::AuthorityFailure;
use sylvia_domain::authority::{ActionCommand, WriterRole};
use sylvia_domain::error::DomainError;

/// `{"__shared__": key}` written back as the shared value, recursively.
fn expand(value: &J, shared: &J) -> J {
    match value {
        J::Object(map) => match map.get("__shared__") {
            Some(J::String(key)) if map.len() == 1 => expand(&shared[key.as_str()], shared),
            _ => J::Object(map.iter().map(|(key, item)| (key.clone(), expand(item, shared))).collect()),
        },
        J::Array(items) => J::Array(items.iter().map(|item| expand(item, shared)).collect()),
        other => other.clone(),
    }
}

fn command(value: &J) -> ActionCommand {
    let text = |key: &str| value[key].as_str().unwrap().to_owned();
    ActionCommand {
        campaign_id: text("campaign_id"),
        action_id: text("action_id"),
        idempotency_key: text("idempotency_key"),
        owner_id: text("owner_id"),
        writer: WriterRole::parse(value["writer"].as_str().unwrap()).unwrap(),
        expected_revision: value["expected_revision"].as_i64().unwrap(),
        turn_id: text("turn_id"),
        package_id: text("package_id"),
        tool_name: text("tool_name"),
        parameters: value["parameters"].as_object().cloned().unwrap(),
        initiating_session_id: value["initiating_session_id"].as_str().map(str::to_owned),
    }
}

/// The class, code and message an error shows its caller, with a `MechanicsRejected`'s fields.
fn error_json(error: &AuthorityFailure) -> J {
    match error {
        AuthorityFailure::Domain(error @ DomainError::Rejected { code, mechanics, .. }) => {
            let mut value = serde_json::json!({
                "class": error.python_class(), "message": error.message(), "code": code,
            });
            if let Some(details) = mechanics {
                value["field_paths"] = serde_json::json!(details.field_paths);
                value["validation_issues"] = J::Array(
                    details.validation_issues.iter().map(|issue| J::Object(issue.as_payload())).collect(),
                );
                value["state_changed"] = J::Bool(details.state_changed);
                value["refusal_reasons"] = serde_json::json!(details.refusal_reasons);
            }
            value
        }
        AuthorityFailure::Domain(error) => {
            serde_json::json!({"class": error.python_class(), "message": error.message(), "code": null})
        }
        AuthorityFailure::Coded(error) => serde_json::json!({
            "class": error.error_type.unwrap_or("CommandRejected"), "message": error.message, "code": error.code,
        }),
    }
}

#[tokio::test]
async fn mechanics_executions_match_python() {
    let vectors: J = serde_json::from_str(include_str!("vectors/execution_vectors.json"))
        .expect("vector file is valid JSON");
    let cases = expand(&vectors["cases"], &vectors["shared"]);
    let cases = cases.as_array().expect("cases");
    let mut seen = std::collections::BTreeSet::new();
    for (index, case) in cases.iter().enumerate() {
        let variant = case["variant"].as_str().unwrap_or("real");
        seen.insert(variant.to_owned());
        let name = format!("{index} {variant}");
        let owner = Recorded::new(case["calls"].as_array().unwrap());
        let ids = Mutex::new(
            case["uuids"]
                .as_array()
                .unwrap()
                .iter()
                .map(|id| id.as_str().unwrap().to_owned())
                .collect::<Vec<_>>(),
        );
        let draw = || {
            let mut ids = ids.lock().unwrap();
            assert!(!ids.is_empty(), "Rust drew more uuid4 values than Python");
            ids.remove(0)
        };
        let result = execute_action(&owner, &command(&case["command"]), &draw).await;
        assert!(ids.lock().unwrap().is_empty(), "{name}: Rust drew fewer uuid4 values than Python");
        match case.get("raised") {
            Some(raised) => {
                let error = result.expect_err(&format!("{name}: Python raised {raised}"));
                let mut expected = serde_json::json!({
                    "class": raised["class"], "message": raised["message"], "code": raised["code"],
                });
                if raised["class"] == "ExceptionGroup" {
                    // The client's exception group: an internal fault for the owner.
                    expected["code"] = J::from("internal_error");
                }
                for key in ["field_paths", "validation_issues", "state_changed", "refusal_reasons"] {
                    if let Some(value) = raised.get(key) {
                        expected[key] = value.clone();
                    }
                }
                assert_same_json(&error_json(&error), &expected, &name);
            }
            None => {
                let receipt = result.unwrap_or_else(|error| panic!("{name}: {error:?}"));
                assert_eq!(receipt, recorded_execution(&case["receipt"]), "{name}");
            }
        }
        owner.check(&name);
    }
    for expected in [
        "real",
        "awaiting_choice",
        "stale_revision",
        "wrong_owner",
        "wrong_writer",
        "idempotency_conflict",
        "command_rejected",
        "plain_rejected",
        "mechanics_rejected",
        "enum_not_castable",
        "castable_refusal",
        "precondition",
        "value_error",
        "key_error",
        "revalidation_unavailable",
        "no_longer_available",
    ] {
        assert!(seen.contains(expected), "no {expected} case");
    }
}
