//! The Claude Code provider's request side and rules against the frozen `provider/_claude.py`
//! (`tests/vectors/generate_claude_vectors.py`). Lane 5.

use serde_json::Value;
use std::collections::BTreeMap;
use std::path::Path;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_provider::chat::ChatError;
use sylvia_provider::claude::{self, RecordIds};
use sylvia_provider::claude_kept::ContinuationError;
use sylvia_provider::claude_native::NativeRoute;
use sylvia_provider::claude_runtime::RuntimeEvent;
use sylvia_provider::error::ErrorKind;
use sylvia_provider::types::PyValue;
use sylvia_provider::ProviderError;

fn vectors() -> Value {
    serde_json::from_str(include_str!("vectors/claude_vectors.json")).expect("claude vectors")
}

fn dumps(value: &Json) -> String {
    pyjson::dumps(value, Dump::DEFAULT)
}

fn route(spec: &Json) -> NativeRoute {
    let keyed = |key: &str| -> BTreeMap<usize, Json> {
        spec.get(key)
            .and_then(Json::as_object)
            .map(|map| map.iter().map(|(at, value)| (at.parse().unwrap(), value.clone())).collect())
            .unwrap_or_default()
    };
    NativeRoute {
        stable_count: spec["stable_count"].as_u64().unwrap() as usize,
        entry_count: spec["entry_count"].as_u64().unwrap() as usize,
        boundaries: spec
            .get("boundaries")
            .and_then(Json::as_array)
            .map(|list| list.iter().map(|b| b.as_u64().unwrap() as usize).collect())
            .unwrap_or_default(),
        native_records: keyed("native_records")
            .into_iter()
            .map(|(at, value)| (at, value.as_array().unwrap().clone()))
            .collect(),
        native_answers: keyed("native_answers"),
        on_collected: None,
    }
}

fn check_prepare(case: &Json, now: &str, workspace: &Path, native: bool) {
    let name = case["name"].as_str().unwrap();
    let mut counter = 0u128;
    let mut uuid4 = || {
        counter += 1;
        counter.to_be_bytes()
    };
    let clock = || now.to_owned();
    let mut ids = RecordIds { uuid4: &mut uuid4, now: &clock };
    let tools = case.get("tools").and_then(Json::as_array).cloned();
    let route = native.then(|| route(&case["route"]));
    let ours = claude::prepare_request(
        case["messages"].as_array().unwrap(),
        tools.as_deref(),
        "claude-opus-5-5",
        workspace,
        route.as_ref(),
        &mut ids,
    );
    match (ours, case.get("error")) {
        (Ok(prepared), None) => {
            assert_eq!(prepared.system, case["system"].as_str().unwrap(), "{name}: system");
            assert_eq!(dumps(&Json::Array(prepared.records)), dumps(&case["records"]), "{name}: records");
            assert_eq!(dumps(&Json::Object(prepared.prompt)), dumps(&case["prompt"]), "{name}: prompt");
            assert_eq!(
                dumps(&Json::Array(prepared.native_tools)),
                dumps(&case["native_tools"]),
                "{name}: tools"
            );
        }
        (Err(ChatError::Provider(error)), Some(expected)) => {
            assert_eq!(error.kind.class_name(), expected[0].as_str().unwrap(), "{name}");
            assert_eq!(error.message, expected[1].as_str().unwrap(), "{name}");
        }
        (Err(ChatError::Incidental(error)), Some(expected)) => {
            assert_ne!(expected[0], "InvalidResponseError", "{name}");
            assert_eq!(error.0, expected[1].as_str().unwrap(), "{name}");
        }
        (ours, theirs) => panic!("{name}: Rust {ours:?}, Python {theirs:?}"),
    }
}

#[test]
fn prepare_request_matches_python() {
    let vectors = vectors();
    let now = vectors["now"].as_str().unwrap();
    let workspace = Path::new(vectors["workspace"].as_str().unwrap());
    let plain = vectors["plain"].as_array().unwrap();
    let native = vectors["native"].as_array().unwrap();
    assert!(plain.len() >= 13 && native.len() >= 13);
    for case in plain {
        check_prepare(case, now, workspace, false);
    }
    for case in native {
        check_prepare(case, now, workspace, true);
    }
}

#[test]
fn tool_search_matches_python() {
    for case in vectors()["tool_search"].as_array().unwrap() {
        assert_eq!(
            claude::tool_search(case["tools"].as_array().unwrap()),
            case["search"].as_bool().unwrap(),
            "{}",
            case["tools"]
        );
    }
}

#[test]
fn runtime_failures_match_python() {
    for case in vectors()["failures"].as_array().unwrap() {
        let error = claude::runtime_failure(case["event"].as_object().unwrap());
        assert_eq!(error.kind.class_name(), case["class"].as_str().unwrap(), "{}", case["event"]);
        assert_eq!(error.message, case["message"].as_str().unwrap(), "{}", case["event"]);
    }
}

#[test]
fn still_waiting_matches_python() {
    for case in vectors()["waiting"].as_array().unwrap() {
        let drained: Vec<RuntimeEvent> = case["events"]
            .as_array()
            .unwrap()
            .iter()
            .map(|event| match event {
                Json::Null => RuntimeEvent::Exited,
                Json::String(_) => RuntimeEvent::Failed(ProviderError::new(ErrorKind::InvalidResponse, "x")),
                Json::Object(map) => RuntimeEvent::Event(map.clone()),
                other => panic!("{other}"),
            })
            .collect();
        match (claude::check_still_waiting(&drained), case) {
            (Ok(()), case) if case.get("ok").is_some() => {}
            (Err(ContinuationError::Mismatch(mismatch)), case) if case.get("mismatch").is_some() => {
                assert_eq!(mismatch.reason, case["mismatch"].as_str().unwrap(), "{}", case["events"])
            }
            (Err(ContinuationError::Raw(text)), case) if case.get("raw").is_some() => {
                assert_eq!(text, case["raw"][1].as_str().unwrap(), "{}", case["events"])
            }
            (ours, case) => panic!("{}: Rust {ours:?}, Python {case}", case["events"]),
        }
    }
}

#[test]
fn failure_codes_match_python() {
    for case in vectors()["codes"].as_array().unwrap() {
        let message = case["message"].as_str().unwrap();
        let kind = match case["class"].as_str().unwrap() {
            "cancelled" | "Exception" => None,
            "ProviderTimeoutError" => Some(ErrorKind::Timeout),
            "StaleConnectionError" => Some(ErrorKind::StaleConnection),
            "IncompleteProviderStreamError" => Some(ErrorKind::IncompleteStream),
            "ProviderConnectionError" => Some(ErrorKind::Connection),
            "InvalidResponseError" => Some(ErrorKind::InvalidResponse),
            "RateLimitError" => Some(ErrorKind::RateLimit),
            "AuthenticationError" => Some(ErrorKind::Authentication),
            "ModelNotFoundError" => Some(ErrorKind::ModelNotFound),
            "TokenLimitError" => Some(ErrorKind::TokenLimit),
            "ProviderError" => Some(ErrorKind::Provider),
            other => panic!("{other}"),
        };
        let error = kind.map(|kind| ProviderError::new(kind, message));
        let cancelled = case["class"] == "cancelled";
        assert_eq!(claude::failure_code(error.as_ref(), cancelled), case["code"].as_str().unwrap(), "{case}");
    }
}

fn int(value: &Json) -> PyValue {
    PyValue::int(value.as_i64().unwrap())
}

#[test]
fn usage_matches_python() {
    for case in vectors()["usages"].as_array().unwrap() {
        let measured = claude::measured_usage(&case["usage"]);
        assert_eq!(measured.is_some(), case["reported"].as_bool().unwrap(), "{}", case["usage"]);
        let Some((tokens, cache)) = measured else { continue };
        let expected = &case["tokens"];
        assert_eq!(tokens.prompt_tokens, int(&expected[0]), "{}", case["usage"]);
        assert_eq!(tokens.completion_tokens, int(&expected[1]), "{}", case["usage"]);
        assert_eq!(tokens.total_tokens, int(&expected[2]), "{}", case["usage"]);
        let expected = &case["cache"];
        assert_eq!(cache.cache_read_tokens, expected[0].as_i64(), "{}", case["usage"]);
        assert_eq!(cache.cache_write_tokens, expected[1].as_i64(), "{}", case["usage"]);
        assert_eq!(cache.cache_miss_tokens, expected[2].as_i64(), "{}", case["usage"]);
        assert_eq!(cache.total_prompt_tokens, expected[3].as_i64().unwrap(), "{}", case["usage"]);
    }
}

#[test]
fn model_usage_matches_python() {
    for case in vectors()["model_usages"].as_array().unwrap() {
        let since: Option<JsonMap> = case["since"].as_object().cloned();
        let ours = claude::model_usage(&case["usage"], since.as_ref());
        match ours {
            None => assert!(case["counts"].is_null(), "{}", case["usage"]),
            Some((counts, recorded)) => {
                assert_eq!(dumps(&Json::Object(counts)), dumps(&case["counts"]), "{}", case["usage"]);
                assert_eq!(dumps(&Json::Object(recorded)), dumps(&case["recorded"]), "{}", case["usage"]);
            }
        }
    }
}

#[test]
fn context_window_matches_python() {
    for case in vectors()["windows"].as_array().unwrap() {
        let window = claude::reported_window(&case["usage"], case["resolved"].as_str(), "test-model");
        assert_eq!(window, case["window"].as_i64(), "{case}");
    }
}
