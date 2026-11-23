//! The ChatGPT provider's rules and kept-thread pool (`chatgpt`) and `lifecycle` against the
//! frozen `provider/_chatgpt.py` and `provider/_lifecycle.py`
//! (`vectors/generate_chatgpt_rules_vectors.py`).

use serde_json::{json, Value};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::Json;
use sylvia_provider::chatgpt::{self, KeptThread, KeptThreads, PendingCall, ThreadState};
use sylvia_provider::codex_rpc::CodexConnection;
use sylvia_provider::config::env::Environment;
use sylvia_provider::error::ErrorKind;
use sylvia_provider::hooks::LateUsageListener;
use sylvia_provider::lifecycle::LifecycleTiming;
use sylvia_provider::types::{CacheUsage, ProviderResponse, TokenUsage};
use sylvia_provider::ProviderError;

fn vectors() -> Value {
    serde_json::from_str(include_str!("vectors/chatgpt_rules_vectors.json")).expect("chatgpt rules vectors")
}

fn dumps(value: &Value) -> String {
    pyjson::dumps(value, Dump::DEFAULT)
}

#[test]
fn thread_setting_matches_python() {
    for case in vectors()["settings"].as_array().unwrap() {
        let pairs: Vec<(&str, &str)> = case["environ"]
            .as_object()
            .unwrap()
            .iter()
            .map(|(key, value)| (key.as_str(), value.as_str().unwrap()))
            .collect();
        let environment = Environment::of(&pairs);
        assert_eq!(
            chatgpt::thread_setting(&environment),
            case["setting"].as_str().unwrap(),
            "{}",
            case["environ"]
        );
    }
}

fn usage_view(usage: &TokenUsage, cache: &CacheUsage) -> Value {
    json!({
        "tokens": [usage.prompt_tokens.to_json(), usage.completion_tokens.to_json(), usage.total_tokens.to_json(), usage.reasoning_tokens],
        "cache": [cache.cache_read_tokens, cache.cache_write_tokens, cache.cache_miss_tokens, cache.total_prompt_tokens],
    })
}

#[test]
fn reported_usage_matches_python() {
    for case in vectors()["usages"].as_array().unwrap() {
        let parsed = chatgpt::reported_usage(&case["params"]);
        let view = parsed.as_ref().map_or(Value::Null, |(usage, cache)| usage_view(usage, cache));
        assert_eq!(dumps(&view), dumps(&case["parsed"]), "{}", case["params"]);
        if let Some((usage, cache)) = parsed {
            assert_eq!(
                dumps(&Json::Object(chatgpt::usage_figures(&usage, &cache))),
                dumps(&case["figures"]),
                "{}",
                case["params"]
            );
        }
    }
}

#[test]
fn app_server_request_matches_python() {
    for case in vectors()["requests"].as_array().unwrap() {
        let tools = case["tools"].as_array().cloned();
        let request =
            chatgpt::app_server_request("gpt-6-sol", case["messages"].as_array().unwrap(), tools.as_deref())
                .unwrap_or_else(|error| panic!("{error:?}"));
        assert_eq!(
            dumps(&Json::Array(request.dynamic_tools)),
            dumps(&case["dynamic"]),
            "{}",
            case["messages"]
        );
        assert_eq!(dumps(&Json::Array(request.items)), dumps(&case["items"]), "{}", case["messages"]);
        assert_eq!(
            dumps(&Json::Array(request.system_instructions)),
            dumps(&case["system"]),
            "{}",
            case["messages"]
        );
    }
}

fn mismatch(error: &chatgpt::KeptThreadMismatch) -> Value {
    let class = if error.in_place { "_AnswerInPlaceError" } else { "_KeptThreadMismatchError" };
    json!({"error": [class, error.reason, error.message]})
}

#[test]
fn call_tails_match_python() {
    for case in vectors()["tails"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let tail = case["tail"].as_array().unwrap();
        let spoken = case["spoken"].as_str().unwrap();
        let dropped = case["dropped_draft_ok"].as_bool().unwrap();
        let ours = match chatgpt::call_tail(tail, "call_1", spoken, false, dropped) {
            Ok((call, output, rest)) => json!({"call": call, "output": output, "rest": rest}),
            Err(error) => mismatch(&error),
        };
        assert_eq!(dumps(&ours), dumps(&case["call_tail"]), "call tail {name}");
        let ours = match chatgpt::in_place_answer(tail, "call_1", spoken, dropped) {
            Ok((result, texts)) => json!({"result": result, "texts": texts}),
            Err(error) => mismatch(&error),
        };
        assert_eq!(dumps(&ours), dumps(&case["in_place"]), "in place {name}");
    }
}

#[test]
fn credit_matches_python() {
    for case in vectors()["credits"].as_array().unwrap() {
        let received = Arc::new(Mutex::new(Vec::new()));
        let log = Arc::clone(&received);
        let good: LateUsageListener = Arc::new(move |report: &ProviderResponse| {
            let mut view = usage_view(&report.usage, &report.cache_usage);
            view["model"] = json!(report.model);
            view["reported"] = json!(report.usage_reported);
            log.lock().unwrap().push(view);
        });
        let bad: LateUsageListener = Arc::new(|_: &ProviderResponse| panic!("listener broke"));
        let mut pending = PendingCall::new(json!(3), "call_1", "turn_1");
        pending.exchange_id = case["exchange_id"].as_str().map(str::to_owned);
        pending.model = "gpt-6-sol".into();
        pending.listeners = vec![Arc::clone(&good), bad, good];
        let usage = TokenUsage::from_ints(100, 10, 110, Some(4));
        let cache = CacheUsage {
            cache_read_tokens: Some(80),
            cache_write_tokens: None,
            cache_miss_tokens: Some(20),
            total_prompt_tokens: 100,
        };
        let figures = pending.credit(usage, cache);
        assert_eq!(dumps(&Json::Object(figures)), dumps(&case["figures"]));
        let received = received.lock().unwrap().clone();
        let mut expected = case["received"].clone();
        for entry in expected.as_array_mut().unwrap() {
            // The view's keys in Rust's order; the values are compared.
            let view = entry.as_object().unwrap().clone();
            *entry = json!({"tokens": view["tokens"], "cache": view["cache"], "model": view["model"], "reported": view["reported"]});
        }
        assert_eq!(dumps(&Value::Array(received)), dumps(&expected));
        assert_eq!(pending.usage_owed, case["owed"].as_bool().unwrap());
    }
}

#[test]
fn continuation_failures_match_python() {
    for case in vectors()["failures"].as_array().unwrap() {
        let kind = match case["class"].as_str().unwrap() {
            "ProviderConnectionError" | "_KeptThreadMismatchError" => ErrorKind::Connection,
            "StaleConnectionError" => ErrorKind::StaleConnection,
            "ModelNotFoundError" => ErrorKind::ModelNotFound,
            "AuthenticationError" => ErrorKind::Authentication,
            "ProviderError" => ErrorKind::Provider,
            "RateLimitError" => ErrorKind::RateLimit,
            "TokenLimitError" => ErrorKind::TokenLimit,
            "ContextCompactionRequiredError" => ErrorKind::ContextCompactionRequired,
            "ModelCapacityError" => ErrorKind::ModelCapacity,
            "InvalidResponseError" => ErrorKind::InvalidResponse,
            "IncompleteProviderStreamError" => ErrorKind::IncompleteStream,
            "ProviderTimeoutError" => ErrorKind::Timeout,
            // Not provider errors in Rust either.
            _ => continue,
        };
        let error = ProviderError::new(kind, "x");
        assert_eq!(
            chatgpt::continuation_failed(&error),
            case["failed"].as_bool().unwrap(),
            "{}",
            case["class"]
        );
    }
}

fn thread(op: &Value) -> Arc<KeptThread> {
    let state = ThreadState {
        items: op["items"].as_array().unwrap().clone(),
        generation: op["generation"].as_u64().unwrap(),
        pending: op["pending"].as_bool().unwrap().then(|| PendingCall::new(json!(1), "call_1", "turn_1")),
        ..ThreadState::default()
    };
    let home = Path::new(op["home"].as_str().unwrap());
    KeptThread::new(
        CodexConnection::new(home, None, Default::default()),
        home,
        op["model"].as_str().unwrap(),
        &format!("thread-{}", op["name"].as_str().unwrap()),
        op["instructions"].as_str().unwrap(),
        op["tools"].as_array().unwrap().clone(),
        state,
    )
}

#[test]
fn kept_thread_pools_match_python() {
    for script in vectors()["pools"].as_array().unwrap() {
        let name = script["name"].as_str().unwrap();
        let pool = KeptThreads::new(
            script.get("limit").and_then(Value::as_u64).unwrap_or(4) as usize,
            script.get("idle").and_then(Value::as_f64).unwrap_or(600.0),
            script.get("pending_idle").and_then(Value::as_f64).unwrap_or(1800.0),
        );
        let mut names: HashMap<usize, String> = HashMap::new();
        let label = |names: &HashMap<usize, String>, kept: &Arc<KeptThread>| {
            names[&(Arc::as_ptr(kept) as usize)].clone()
        };
        let home = |op: &Value| op.get("home").and_then(Value::as_str).map(PathBuf::from);
        let mut results = Vec::new();
        for op in script["ops"].as_array().unwrap() {
            let result = match op["op"].as_str().unwrap() {
                "keep" => {
                    let kept = thread(op);
                    names.insert(Arc::as_ptr(&kept) as usize, op["name"].as_str().unwrap().to_owned());
                    json!(pool.keep(kept).iter().map(|each| label(&names, each)).collect::<Vec<_>>())
                }
                "take" => {
                    let (chosen, close) = pool.take(
                        &home(op).unwrap(),
                        op["model"].as_str().unwrap(),
                        op["instructions"].as_str().unwrap(),
                        op["tools"].as_array().unwrap(),
                        op["items"].as_array().unwrap(),
                    );
                    json!({"chosen": chosen.map(|kept| label(&names, &kept)), "close": close.iter().map(|each| label(&names, each)).collect::<Vec<_>>()})
                }
                "release" => {
                    let mut released: Vec<String> = pool
                        .release(home(op).as_deref(), op.get("model").and_then(Value::as_str))
                        .iter()
                        .map(|each| label(&names, each))
                        .collect();
                    released.sort();
                    json!(released)
                }
                "generation" => json!(pool.generation(&home(op).unwrap())),
                "next_ordinal" => json!(pool.next_ordinal(&home(op).unwrap(), op["take"].as_bool().unwrap())),
                "abandon" => {
                    pool.abandon(
                        &home(op).unwrap(),
                        op["ordinal"].as_u64().unwrap(),
                        op["figures"].as_object().unwrap().clone(),
                    );
                    Value::Null
                }
                "take_abandoned" => json!(pool
                    .take_abandoned(&home(op).unwrap(), op["ordinal"].as_u64().unwrap())
                    .into_iter()
                    .map(Json::Object)
                    .collect::<Vec<_>>()),
                "len" => json!(pool.len()),
                "models" => json!(pool.models()),
                "sleep" => {
                    std::thread::sleep(std::time::Duration::from_secs_f64(op["seconds"].as_f64().unwrap()));
                    Value::Null
                }
                "close_all" => {
                    pool.close_all();
                    Value::Null
                }
                other => panic!("{other}"),
            };
            results.push(result);
        }
        assert_eq!(dumps(&Value::Array(results)), dumps(&script["results"]), "pool {name}");
    }
}

#[test]
fn lifecycle_timing_matches_python() {
    for case in vectors()["lifecycles"].as_array().unwrap() {
        let timing = LifecycleTiming::at("2026-11-23T09:30:00+00:00".into(), 1000);
        for record in case["records"].as_array().unwrap() {
            timing.record(
                record[0].as_str().unwrap(),
                record[1].as_u64().unwrap().into(),
                record[2].as_u64().unwrap().into(),
                record[3].as_str().unwrap(),
                record[4].as_str(),
            );
        }
        let snapshot =
            timing.snapshot_at(case["status"].as_str().unwrap(), case["now"].as_u64().unwrap().into());
        assert_eq!(dumps(&Json::Object(snapshot)), dumps(&case["snapshot"]), "{}", case["records"]);
    }
}
