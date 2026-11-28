//! The context ledger's settings, host seal, contract and tag check against Python's own
//! answers (`tests/vectors/ledger_settings_vectors.json`, written by
//! `generate_ledger_settings_vectors.py` from `agent/_context_ledger.py`).

use serde_json::{json, Value as Json};
use sylvia_agent::context_ledger::{
    check_fixed_blocks_tagged, context_mode, deferred_tool_list, fixed_tool_list, host_seal, journal_marker,
    ledger_contract, native_marker, seal_prefix, stable_tool_list, tool_list_setting, Tagged,
};
use sylvia_base::testing::assert_same_json;
use sylvia_base::JsonMap;
use sylvia_provider::config::env::Environment;

fn vectors() -> Json {
    serde_json::from_str(include_str!("vectors/ledger_settings_vectors.json")).expect("vectors")
}

fn text(value: &Json) -> Option<&str> {
    value.as_str()
}

fn object(value: &Json) -> JsonMap {
    value.as_object().cloned().expect("an object")
}

#[test]
fn settings_match_python() {
    let vectors = vectors();
    let cases = vectors["settings"].as_array().unwrap();
    assert!(cases.len() > 900);
    for (index, case) in cases.iter().enumerate() {
        let pairs: Vec<(String, String)> = case["environ"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(key, value)| (key.clone(), value.as_str().unwrap().to_owned()))
            .collect();
        let borrowed: Vec<(&str, &str)> =
            pairs.iter().map(|(key, value)| (key.as_str(), value.as_str())).collect();
        let environment = Environment::of(&borrowed);
        let (transport, provider_key, model_id) =
            (text(&case["transport"]), text(&case["provider_key"]), text(&case["model_id"]));
        let found = json!({
            "tool_list_setting": tool_list_setting(&environment, transport, provider_key),
            "stable_tool_list": stable_tool_list(&environment, transport, provider_key),
            "fixed_tool_list": fixed_tool_list(&environment),
            "deferred_tool_list": deferred_tool_list(&environment, transport, provider_key, model_id),
            "context_mode": context_mode(&environment, transport, provider_key),
        });
        let expected = json!({
            "tool_list_setting": case["tool_list_setting"],
            "stable_tool_list": case["stable_tool_list"],
            "fixed_tool_list": case["fixed_tool_list"],
            "deferred_tool_list": case["deferred_tool_list"],
            "context_mode": case["context_mode"],
        });
        assert_same_json(&found, &expected, format!("settings #{index} {case}"));
    }
}

#[test]
fn seals_and_contracts_match_python() {
    let vectors = vectors();
    for case in vectors["seals"].as_array().unwrap() {
        let seal = host_seal(case["session_id"].as_str().unwrap());
        let found = json!({"seal": seal, "prefix": seal_prefix(&seal)});
        let expected = json!({"seal": case["seal"], "prefix": case["prefix"]});
        assert_same_json(&found, &expected, format!("seal of {}", case["session_id"]));
    }
    for case in vectors["contracts"].as_array().unwrap() {
        let found = json!(ledger_contract(case["seal"].as_str().unwrap()));
        assert_same_json(&found, &case["text"], format!("contract with seal {}", case["seal"]));
    }
}

#[test]
fn export_markers_match_python() {
    let vectors = vectors();
    let cases = vectors["markers"].as_array().unwrap();
    assert!(cases.len() >= 18);
    let outcome = |answer: sylvia_base::CodedResult<JsonMap>| match answer {
        Ok(marker) => json!({"ok": marker}),
        Err(error) => json!({"error": error.error_type, "message": error.message}),
    };
    for case in cases {
        let value = object(&case["value"]);
        assert_same_json(&outcome(journal_marker(&value)), &case["journal"], format!("journal of {value:?}"));
        assert_same_json(&outcome(native_marker(&value)), &case["native"], format!("native of {value:?}"));
    }
}

#[test]
fn the_tag_check_matches_python() {
    let vectors = vectors();
    let cases = vectors["tag_checks"].as_array().unwrap();
    assert!(cases.len() >= 18);
    for case in cases {
        let canonical: Vec<Tagged> = case["messages"]
            .as_array()
            .unwrap()
            .iter()
            .map(|item| Tagged { message: object(&item["message"]), tags: object(&item["tags"]) })
            .collect();
        let found = match check_fixed_blocks_tagged(&canonical) {
            Ok(()) => json!("ok"),
            Err(error) => json!({"error": error.error_type, "message": error.message}),
        };
        assert_same_json(&found, &case["outcome"], case["name"].as_str().unwrap());
    }
}
