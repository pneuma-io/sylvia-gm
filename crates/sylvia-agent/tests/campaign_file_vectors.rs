//! Compare the Campaign File commit and the reference reads through MCP (T3m-3) with Python.
//!
//! Regenerate with `tests/vectors/generate_campaign_file_vectors.py`: every
//! `StatelessMcpMechanics.commit_campaign_file` call of the `ruleless-turn` and
//! `srd-mechanics-turn` turns, followed by variants (each validation branch of the batch, the
//! owner's refusals and failures, the open-turn lookup, knowledge searches and reference reads).
//! Rust is answered every owner call Python's call made, in order, the Full Audit records among
//! them; its result must be Python's byte for byte, or its error Python's class, code, message
//! and correction fields.

mod common;

use common::Recorded;
use serde_json::Value as J;
use sylvia_base::testing::assert_same_json;
use sylvia_agent::stateless_mcp::campaign_file::{commit_campaign_file, read_reference, search_knowledge};
use sylvia_agent::stateless_mcp::AuthorityFailure;
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
async fn campaign_file_calls_match_python() {
    let vectors: J = serde_json::from_str(include_str!("vectors/campaign_file_vectors.json"))
        .expect("vector file is valid JSON");
    let cases = expand(&vectors["cases"], &vectors["shared"]);
    let cases = cases.as_array().expect("cases");
    let mut seen = std::collections::BTreeSet::new();
    for (index, case) in cases.iter().enumerate() {
        let variant = case["variant"].as_str().unwrap_or("real");
        let method = case["method"].as_str().unwrap();
        seen.insert(format!("{method} {variant}"));
        let name = format!("{index} {} {method} {variant}", case["session"].as_str().unwrap());
        let owner = Recorded::new(case["calls"].as_array().unwrap());
        let campaign_id = case["campaign_id"].as_str().unwrap();
        let session_id = case["session_id"].as_str();
        let result = match method {
            "commit_campaign_file" => {
                commit_campaign_file(
                    &owner,
                    campaign_id,
                    session_id.unwrap(),
                    case["basis_message_id"].as_str().unwrap(),
                    case["parameters"].as_object().unwrap(),
                    case["turn_id"].as_str(),
                )
                .await
            }
            "search_knowledge" => {
                let sources: Option<Vec<String>> = case["sources"]
                    .as_array()
                    .map(|items| items.iter().map(|item| item.as_str().unwrap().to_owned()).collect());
                search_knowledge(
                    &owner,
                    campaign_id,
                    case["query"].as_str().unwrap(),
                    sources.as_deref(),
                    case["limit"].as_i64().unwrap(),
                    case["offset"].as_i64().unwrap(),
                    session_id,
                )
                .await
            }
            "read_reference" => {
                read_reference(
                    &owner,
                    campaign_id,
                    case["source"].as_str().unwrap(),
                    case["path"].as_str().unwrap(),
                    session_id,
                )
                .await
            }
            other => panic!("unknown method {other}"),
        };
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
                let found = result.unwrap_or_else(|error| panic!("{name}: {error:?}"));
                assert_eq!(
                    serde_json::to_string(&J::Object(found)).unwrap(),
                    serde_json::to_string(&case["result"]).unwrap(),
                    "{name}"
                );
            }
        }
        owner.check(&name);
    }
    for expected in [
        "commit_campaign_file real",
        "commit_campaign_file nothing changed",
        "commit_campaign_file no assessment",
        "commit_campaign_file a long non-ASCII summary",
        "commit_campaign_file a non-ASCII summary over the bound",
        "commit_campaign_file foreign and mistyped collections",
        "commit_campaign_file too many items",
        "commit_campaign_file item shapes",
        "commit_campaign_file changed without changes",
        "commit_campaign_file unchanged with a change",
        "commit_campaign_file a committed change",
        "commit_campaign_file the owner refuses",
        "commit_campaign_file the owner fails",
        "commit_campaign_file every note type",
        "commit_campaign_file invalid options",
        "commit_campaign_file the open turn",
        "commit_campaign_file no open turn",
        "search_knowledge a search",
        "search_knowledge a search of every source",
        "search_knowledge a refused search",
        "search_knowledge a failed search",
        "read_reference a read of current state",
        "read_reference a read of a missing path",
        "read_reference a refused read",
    ] {
        assert!(seen.contains(expected), "no {expected} case");
    }
}
