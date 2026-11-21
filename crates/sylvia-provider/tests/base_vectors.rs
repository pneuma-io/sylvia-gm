//! Compare sylvia-provider's base types, errors and retry timing with the frozen Python source.
//!
//! Regenerate with `tests/vectors/generate_base_vectors.py` (needs the snapshot's Python).

use serde_json::Value;
use sylvia_base::pystr::casefold;
use sylvia_provider::error::{is_context_rejection, ErrorKind, ProviderError};
use sylvia_provider::pyfmt::format_g;
use sylvia_provider::reply::provider_error_reply;
use sylvia_provider::retry::{build_delay_with, retry_after_seconds_at};
use sylvia_provider::tool_delta::parse_tool_call_delta;
use sylvia_provider::types::{CacheUsage, ModelInfo, RetryConfig};

fn vectors() -> Value {
    serde_json::from_str(include_str!("vectors/base_vectors.json")).expect("vector file is valid JSON")
}

fn kind(name: &str) -> ErrorKind {
    ErrorKind::ALL
        .into_iter()
        .find(|kind| kind.class_name() == name)
        .unwrap_or_else(|| panic!("Python has an error class Rust does not: {name}"))
}

fn optional_float(value: &Value) -> Option<f64> {
    match value {
        Value::Null => None,
        Value::String(text) if text == "nan" => Some(f64::NAN),
        Value::String(text) if text == "inf" => Some(f64::INFINITY),
        Value::String(text) if text == "-inf" => Some(f64::NEG_INFINITY),
        other => Some(other.as_f64().expect("float")),
    }
}

#[test]
fn error_classes_match_python() {
    let vectors = vectors();
    let hierarchy = vectors["errors"]["hierarchy"].as_array().unwrap();
    assert_eq!(hierarchy.len(), ErrorKind::ALL.len(), "same set of classes");
    for row in hierarchy {
        let kind = kind(row["class"].as_str().unwrap());
        assert_eq!(kind.category().as_str(), row["category"], "{kind:?} category");
        let bases: Vec<&str> = row["bases"].as_array().unwrap().iter().map(|b| b.as_str().unwrap()).collect();
        for other in ErrorKind::ALL {
            let expected = other == kind || bases.contains(&other.class_name());
            assert_eq!(kind.is_a(other), expected, "issubclass({kind:?}, {other:?})");
        }
    }
    assert_eq!(
        ProviderError::token_limit_rejection().message,
        vectors["errors"]["token_limit_rejection"].as_str().unwrap()
    );
}

#[test]
fn owner_replies_match_python() {
    let vectors = vectors();
    let replies = vectors["errors"]["replies"].as_array().unwrap();
    assert!(replies.len() > 150);
    for case in replies {
        let kind = kind(case["class"].as_str().unwrap());
        let message = case["message"].as_str().unwrap();
        let mut error = if kind == ErrorKind::ContinuationRequired {
            ProviderError::continuation_required(message, "secret-token", serde_json::json!([{"type": "x"}]))
        } else {
            ProviderError::new(kind, message)
        };
        if let Some(delay) = case.get("retry_after") {
            error.retry_after = optional_float(delay);
        }
        if let Some(accepted) = case["accepted_message_id"].as_str() {
            error = error.with_accepted_message_id(accepted);
        }
        let submitted = if case["method"] == "submit_session_input" {
            case["submitted_message_id"].as_str()
        } else {
            None
        };
        let reply = provider_error_reply(&error, submitted);
        let expected = &case["reply"]["error"];
        assert_eq!(reply.code, expected["code"], "{case}");
        assert_eq!(reply.message, expected["message"].as_str().unwrap(), "{case}");
        assert_eq!(expected["diagnostics"], serde_json::json!([]));
    }
}

#[test]
fn format_g_matches_python() {
    let vectors = vectors();
    let cases = vectors["format_g"].as_array().unwrap();
    assert!(cases.len() > 5000);
    let failures: Vec<String> = cases
        .iter()
        .filter_map(|pair| {
            let value = pair[0].as_f64().unwrap();
            let expected = pair[1].as_str().unwrap();
            let actual = format_g(value);
            (actual != expected).then(|| format!("{value:e}: python {expected}, rust {actual}"))
        })
        .collect();
    assert!(
        failures.is_empty(),
        "{} mismatches, first {:?}",
        failures.len(),
        &failures[..failures.len().min(5)]
    );
}

#[test]
fn context_rejection_matches_python() {
    for pair in vectors()["context_rejection"].as_array().unwrap() {
        let text = pair[0].as_str().unwrap();
        assert_eq!(
            is_context_rejection(text),
            pair[1].as_bool().unwrap(),
            "{text:?} folds to {:?}",
            casefold(text)
        );
    }
}

#[test]
fn tool_deltas_match_python() {
    for case in vectors()["tool_deltas"].as_array().unwrap() {
        let parsed = parse_tool_call_delta(&case["delta"]);
        let expected = &case["parsed"];
        // Python returns `True` itself as the index; it indexes as 1.
        let index = sylvia_provider::tool_delta::python_int(&expected[0]).unwrap();
        assert_eq!(parsed.index as i128, index, "{case}");
        assert_eq!(parsed.call_id, expected[1].as_str().unwrap(), "{case}");
        assert_eq!(parsed.name, expected[2].as_str().unwrap(), "{case}");
        assert_eq!(parsed.arguments, expected[3].as_str().unwrap(), "{case}");
    }
}

#[test]
fn retry_after_matches_python() {
    let vectors = vectors();
    let now = vectors["now_micros"].as_i64().unwrap();
    for pair in vectors["retry_after"].as_array().unwrap() {
        let value = pair[0].as_str();
        let actual = retry_after_seconds_at(value, now);
        let expected = optional_float(&pair[1]);
        assert_eq!(actual, expected, "retry-after {value:?}");
    }
}

fn config(name: &str) -> RetryConfig {
    if name == "default" {
        return RetryConfig::default();
    }
    RetryConfig::provider_default(name).unwrap_or_else(|| panic!("no Rust default for {name}"))
}

#[test]
fn retry_defaults_and_delays_match_python() {
    let vectors = vectors();
    let project = |config: &RetryConfig| {
        serde_json::json!({
            "max_retries": config.max_retries, "base_delay": config.base_delay,
            "max_delay": config.max_delay, "backoff_multiplier": config.backoff_multiplier,
            "retryable_statuses": config.retryable_statuses.iter().collect::<Vec<_>>(),
        })
    };
    assert_eq!(project(&RetryConfig::default()), vectors["retry_configs"]["default"]);
    let providers = vectors["retry_configs"]["providers"].as_array().unwrap();
    let keys: Vec<&str> = providers.iter().map(|pair| pair[0].as_str().unwrap()).collect();
    assert_eq!(keys, RetryConfig::PROVIDER_DEFAULT_KEYS);
    for pair in providers {
        assert_eq!(project(&config(pair[0].as_str().unwrap())), pair[1]);
    }
    for case in vectors["delays"].as_array().unwrap() {
        let config = config(case["config"].as_str().unwrap());
        let delay = build_delay_with(
            case["attempt"].as_u64().unwrap() as u32,
            &config,
            optional_float(&case["retry_after"]),
            case["unit"].as_f64().unwrap(),
        );
        assert_eq!(delay, case["delay"].as_f64().unwrap(), "{case}");
    }
}

#[test]
fn model_info_matches_python() {
    for case in vectors()["model_info"].as_array().unwrap() {
        let fields = &case["fields"];
        let mut info = ModelInfo::new(fields["id"].as_str().unwrap(), "N", "P", "k", "byok");
        if let Some(wire) = fields["provider_model_id"].as_str() {
            info.provider_model_id = wire.into();
        }
        info.context_window = fields["context_window"].as_i64();
        info.declared_context_window = fields["declared_context_window"].as_i64();
        if let Some(source) = fields["context_window_source"].as_str() {
            info.context_window_source = source.into();
        }
        let info = info.post_init();
        assert_eq!(info.wire_model_id(), case["wire_model_id"].as_str().unwrap(), "{case}");
        assert_eq!(info.context_window_source, case["context_window_source"].as_str().unwrap(), "{case}");
        assert_eq!(info.max_output_tokens, case["max_output_tokens"].as_i64());
        assert_eq!(info.builder_output_tokens, case["builder_output_tokens"].as_i64().unwrap());
    }
}

#[test]
fn cache_usage_matches_python() {
    for case in vectors()["cache_usage"].as_array().unwrap() {
        let usage = CacheUsage {
            cache_read_tokens: case["read"].as_i64(),
            total_prompt_tokens: case["total"].as_i64().unwrap(),
            ..CacheUsage::default()
        };
        assert_eq!(usage.hit_ratio(), case["hit_ratio"].as_f64(), "{case}");
        assert_eq!(usage.savings_pct(), case["savings_pct"].as_f64(), "{case}");
    }
}

#[tokio::test(start_paused = true)]
async fn stale_stream_message_matches_python() {
    use sylvia_provider::stream_health::StreamHealthMonitor;
    for pair in vectors()["stale_messages"].as_array().unwrap() {
        let interval = pair[0].as_f64().unwrap();
        let stream: sylvia_provider::ChunkStream<'static> = Box::pin(futures_util::stream::pending());
        let mut monitor = StreamHealthMonitor::new(stream, interval, "OpenCode Go");
        let error = monitor.next().await.unwrap().unwrap_err();
        assert_eq!(error.kind, ErrorKind::StaleConnection);
        assert_eq!(error.message, pair[1].as_str().unwrap());
    }
}

#[tokio::test]
async fn retry_with_backoff_matches_python() {
    use std::cell::RefCell;
    use sylvia_provider::retry::retry_with_backoff_sleeping;
    let vectors = vectors();
    let cases = vectors["backoff"].as_array().unwrap();
    assert!(cases.len() >= 60);
    for case in cases {
        let config = config(case["config"].as_str().unwrap());
        let scenario: Vec<Option<&str>> =
            case["scenario"].as_array().unwrap().iter().map(Value::as_str).collect();
        let calls = RefCell::new(0usize);
        let sleeps = RefCell::new(Vec::new());
        let outcome = retry_with_backoff_sleeping(
            &config,
            || {
                let position = *calls.borrow();
                *calls.borrow_mut() += 1;
                let step = scenario[position.min(scenario.len() - 1)];
                async move {
                    match step {
                        None => Ok("ok"),
                        Some(name) => {
                            let kind = kind(name);
                            let mut error = ProviderError::new(kind, name);
                            if kind.has_retry_after() {
                                error.retry_after = Some(0.25 * (position + 1) as f64);
                            }
                            Err(error)
                        }
                    }
                }
            },
            |delay| {
                sleeps.borrow_mut().push(delay);
                async {}
            },
        )
        .await;
        let outcome = match outcome {
            Ok(text) => text.to_owned(),
            Err(error) => format!("{}: {}", error.kind.class_name(), error.message),
        };
        assert_eq!(outcome, case["outcome"].as_str().unwrap(), "{case}");
        assert_eq!(*calls.borrow() as u64, case["calls"].as_u64().unwrap(), "{case}");
        let sleeps = sleeps.borrow();
        assert_eq!(sleeps.len() as u64, case["sleeps"].as_u64().unwrap(), "{case}");
        let server: Vec<f64> =
            sleeps.iter().copied().filter(|delay| (1..10).any(|n| *delay == 0.25 * f64::from(n))).collect();
        let expected: Vec<f64> =
            case["server_sleeps"].as_array().unwrap().iter().map(|v| v.as_f64().unwrap()).collect();
        assert_eq!(server, expected, "{case}");
    }
}

#[tokio::test]
async fn fake_provider_follows_python() {
    use futures_util::StreamExt;
    use sylvia_provider::{FakeProvider, Provider, ProviderRequest, ProviderResponse, ProviderStreamChunk};
    let fake = FakeProvider::new(
        vec![ProviderResponse { content: Some("You strike the goblin!".into()), ..Default::default() }],
        Vec::new(),
    );
    let chunks: Vec<_> = fake.generate_stream(ProviderRequest::new(Vec::new())).collect().await;
    let chunks: Vec<ProviderStreamChunk> = chunks.into_iter().map(Result::unwrap).collect();
    assert_eq!(
        chunks,
        vec![ProviderStreamChunk::content("You strike the goblin!"), ProviderStreamChunk::finish("stop")]
    );
    let response = fake.generate(ProviderRequest::new(Vec::new())).await.unwrap();
    assert_eq!(response.content.as_deref(), Some("I don't know what to do next."));
    assert_eq!(fake.calls().len(), 2);
    // A stream that is never polled records nothing, like an un-iterated Python generator.
    drop(fake.generate_stream(ProviderRequest::new(Vec::new())));
    assert_eq!(fake.calls().len(), 2);
}

#[test]
fn internal_error_reply_matches_python() {
    let vectors = vectors();
    let expected = &vectors["errors"]["internal_reply"]["error"];
    let reply =
        provider_error_reply(&ProviderError::new(ErrorKind::Internal, "unexpected keyword"), Some("m-1"));
    assert_eq!(reply.code, expected["code"].as_str().unwrap());
    assert_eq!(reply.message, expected["message"].as_str().unwrap());
    assert!(expected.get("diagnostics").is_none(), "Python's internal reply has no diagnostics member");
    assert!(!ErrorKind::Internal.is_a(ErrorKind::Provider));
}
