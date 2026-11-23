//! `ChatGPTProvider` against the frozen `provider/_chatgpt.py`, both on their real
//! `CodexConnection` to the same scripted app-server (`vectors/generate_chatgpt_provider_vectors.py`).
//!
//! This test runs without libtest's harness because it is also the stand-in app-server: started
//! as `<this binary> app-server ...` in a runtime home's workspace, it plays
//! `vectors/chatgpt_turn_standin.py`'s script the same way.

use serde_json::{json, Value};
use std::io::{BufRead, Write};
use std::path::PathBuf;
use std::sync::Mutex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::Json;
use sylvia_provider::chatgpt::{close_all_kept_threads, kept_threads};
use sylvia_provider::chatgpt_provider::ChatGPTProvider;
use sylvia_provider::codex_rpc::python_resolve;
use sylvia_provider::config::env::Environment;
use sylvia_provider::context_mode::{set_ledger_request, with_ledger_request_scope};
use sylvia_provider::hooks::{HookContext, ProviderHook};
use sylvia_provider::provider::BoxFuture;
use sylvia_provider::types::{transport, ModelInfo, ProviderResponse, ProviderStreamChunk};
use sylvia_provider::{Provider, ProviderRequest};

fn vectors() -> Value {
    serde_json::from_str(include_str!("vectors/chatgpt_provider_vectors.json"))
        .expect("chatgpt provider vectors")
}

/// A fresh work folder; an old one that cannot be removed fails here, with its path.
fn folder(name: &str) -> PathBuf {
    let path = std::env::temp_dir().join(format!("sylvia-chatgpt-provider-{}-{name}", std::process::id()));
    if path.exists() {
        std::fs::remove_dir_all(&path)
            .unwrap_or_else(|error| panic!("cannot clear {}: {error}", path.display()));
    }
    std::fs::create_dir_all(&path).unwrap();
    python_resolve(&path)
}

// ── The stand-in app-server ──────────────────────────────────────────────────────────────

fn stand_in() -> i32 {
    let home = std::env::current_dir().unwrap().parent().unwrap().to_owned();
    let script: Value = serde_json::from_slice(&std::fs::read(home.join("standin.json")).unwrap()).unwrap();
    let steps = script["steps"].as_array().unwrap();
    let position_path = home.join("standin-position");
    let mut position: usize =
        std::fs::read_to_string(&position_path).ok().and_then(|text| text.parse().ok()).unwrap_or(0);
    let stdout = std::io::stdout();
    let write = |message: &Json| {
        let mut out = stdout.lock();
        out.write_all(format!("{}\n", pyjson::dumps(message, Dump::DEFAULT)).as_bytes()).unwrap();
        out.flush().unwrap();
    };
    let log = |entry: &Json| {
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(home.join("standin-log.jsonl"))
            .unwrap();
        file.write_all(
            format!("{}\n", pyjson::dumps(entry, Dump { sort_keys: true, ..Dump::DEFAULT })).as_bytes(),
        )
        .unwrap();
    };
    for line in std::io::stdin().lock().lines() {
        let Ok(line) = line else { break };
        let message = pyjson::loads(&line).unwrap();
        log(&message);
        let Some(id) = message.get("id").cloned() else { continue };
        if message.get("method").and_then(Json::as_str) == Some("initialize") {
            write(&json!({"id": id, "result": {"userAgent": "standin"}}));
            continue;
        }
        let fallback = json!({"method": null, "error": {"message": "no step"}});
        let step = steps.get(position).unwrap_or(&fallback);
        position += 1;
        std::fs::write(&position_path, position.to_string()).unwrap();
        for early in step.get("before").and_then(Value::as_array).into_iter().flatten() {
            write(early);
        }
        if let Some(method) = message.get("method") {
            if step.get("method") != Some(method) {
                log(&json!({"unexpected": method, "step": position - 1}));
                write(&json!({"id": id, "error": {"message": "unexpected"}}));
            } else if let Some(error) = step.get("error") {
                write(&json!({"id": id, "error": error}));
            } else {
                write(&json!({"id": id, "result": step["result"]}));
            }
        } else if step.get("response") != Some(&json!(true)) {
            log(&json!({"unexpected": "response", "step": position - 1}));
        }
        for late in step.get("after").and_then(Value::as_array).into_iter().flatten() {
            write(late);
        }
    }
    0
}

// ── The provider runs ────────────────────────────────────────────────────────────────────

#[derive(Default)]
struct Recorder {
    events: Mutex<Vec<Value>>,
}

fn response_view(response: &ProviderResponse) -> Value {
    json!({
        "content": response.content,
        "tool_calls": response.tool_calls.iter().map(|call| json!([call.id.to_json(), call.name.to_json(), call.arguments.to_json()])).collect::<Vec<_>>(),
        "usage": [response.usage.prompt_tokens.to_json(), response.usage.completion_tokens.to_json(), response.usage.total_tokens.to_json()],
        "usage_reported": response.usage_reported,
        "model": response.model,
        "finish_reason": response.finish_reason.to_json(),
        "cache": [response.cache_usage.cache_read_tokens, response.cache_usage.cache_write_tokens,
                  response.cache_usage.cache_miss_tokens, response.cache_usage.total_prompt_tokens],
    })
}

impl ProviderHook for Recorder {
    fn name(&self) -> &str {
        "Recorder"
    }

    fn on_request<'a>(&'a self, context: &'a HookContext) -> BoxFuture<'a, Result<(), String>> {
        Box::pin(async move {
            self.events.lock().unwrap().push(json!({"request": context.request_json}));
            Ok(())
        })
    }

    fn on_response<'a>(&'a self, context: &'a HookContext) -> BoxFuture<'a, Result<(), String>> {
        Box::pin(async move {
            self.events
                .lock()
                .unwrap()
                .push(json!({"response": response_view(context.response.as_ref().unwrap())}));
            Ok(())
        })
    }
}

fn chunk_view(chunk: &ProviderStreamChunk) -> Value {
    json!({
        "content": chunk.content,
        "reasoning_content": chunk.reasoning_content,
        "tool_call_delta": chunk.tool_call_delta,
        "finish_reason": chunk.finish_reason.as_ref().map(|reason| reason.to_json()),
    })
}

/// The generator's `normalise`: clock readings and the start time.
fn normalise(value: &Value) -> Value {
    match value {
        Value::Object(map) => Value::Object(
            map.iter()
                .map(|(key, item)| {
                    let item = if key.ends_with("_ns") && item.is_i64() {
                        json!(0)
                    } else if key == "started_utc" {
                        json!("<utc>")
                    } else {
                        normalise(item)
                    };
                    (key.clone(), item)
                })
                .collect(),
        ),
        Value::Array(items) => Value::Array(items.iter().map(normalise).collect()),
        other => other.clone(),
    }
}

fn dumps(value: &Value) -> String {
    pyjson::dumps(value, Dump::DEFAULT)
}

fn sorted(value: &Value) -> String {
    pyjson::dumps(value, Dump { sort_keys: true, ..Dump::DEFAULT })
}

async fn run_case(case: &Value, now: &str) {
    let name = case["name"].as_str().unwrap();
    let home = folder(name).join("chatgpt-runtime");
    std::fs::create_dir_all(home.join("workspace")).unwrap();
    std::fs::write(home.join("standin.json"), json!({"steps": case["steps"]}).to_string()).unwrap();
    let runtime = home.parent().unwrap().join("runtime");
    std::fs::create_dir_all(&runtime).unwrap();
    let executable = runtime.join(if cfg!(windows) { "codex.exe" } else { "codex" });
    std::fs::copy(std::env::current_exe().unwrap(), &executable).unwrap();
    let mut model = ModelInfo::new(
        "chatgpt_subscription/gpt-6-sol",
        "GPT-6 Sol",
        "ChatGPT",
        "chatgpt_subscription",
        "subscription",
    );
    model.transport = transport::CODEX_APP_SERVER.into();
    model.max_output_tokens = None;
    let environment = Environment::of(&[("SYLVIA_CHATGPT_THREAD", case["thread"].as_str().unwrap())]);
    let clock = now.to_owned();
    let provider = ChatGPTProvider::new(&home, model, Some(&executable), &environment)
        .with_clock(Box::new(move || clock.clone()));
    let recorder = std::sync::Arc::new(Recorder::default());
    provider.hooks().unwrap().register(recorder.clone());
    let tools: Vec<Json> = serde_json::from_value(json!([
        {"type": "function", "function": {"name": "roll", "description": "Roll dice.",
                                          "parameters": {"type": "object", "properties": {"n": {"type": "integer"}}}}},
        {"type": "function", "function": {"name": "look", "description": "Look.", "parameters": {"type": "object"}}},
    ]))
    .unwrap();
    let mut results = Vec::new();
    for spec in case["requests"].as_array().unwrap() {
        let request = ProviderRequest {
            messages: spec["messages"].as_array().unwrap().clone(),
            tools: Some(tools.clone()),
            stream: false,
            timeout: spec["timeout"].as_f64().unwrap(),
            options: spec["options"].as_object().unwrap().clone(),
        };
        let mark = spec["marked"].as_str().map(str::to_owned);
        let (chunks, error) = with_ledger_request_scope(async {
            set_ledger_request(mark);
            let mut chunks = Vec::new();
            let mut error = Value::Null;
            let mut stream = provider.generate_stream(request);
            while let Some(item) = futures_util::StreamExt::next(&mut stream).await {
                match item {
                    Ok(chunk) => chunks.push(chunk_view(&chunk)),
                    Err(failure) => error = json!([failure.kind.class_name(), failure.message]),
                }
            }
            (chunks, error)
        })
        .await;
        let figures = provider.model_info().unwrap().context_figures();
        results.push(json!({
            "chunks": chunks, "error": error, "kept": kept_threads().len(),
            "figures": {
                "context_window": figures.context_window,
                "context_window_source": figures.context_window_source,
                "runtime_context_window": figures.runtime_context_window,
                "runtime_context_source": figures.runtime_context_source,
                "runtime_context_observed_at": figures.runtime_context_observed_at,
                "context_observed_at": figures.context_observed_at,
                "configured_runtime_mismatch": figures.configured_runtime_mismatch,
            },
        }));
    }
    if case.get("release").and_then(Value::as_bool) == Some(true) {
        provider.release_idle().await;
    }
    provider.close().await;
    close_all_kept_threads();
    let text = std::fs::read_to_string(home.join("standin-log.jsonl")).unwrap_or_default();
    let log: Vec<Value> = text.lines().map(|line| serde_json::from_str(line).unwrap()).collect();
    for (index, (ours, theirs)) in results.iter().zip(case["results"].as_array().unwrap()).enumerate() {
        for key in ["error", "chunks", "kept", "figures"] {
            assert_eq!(dumps(&ours[key]), dumps(&theirs[key]), "{name}: request {index} {key}");
        }
    }
    let audits = normalise(&Value::Array(recorder.events.lock().unwrap().clone()));
    let (ours, theirs) = (audits.as_array().unwrap(), case["audits"].as_array().unwrap());
    for (index, (a, b)) in ours.iter().zip(theirs).enumerate() {
        assert_eq!(dumps(a), dumps(b), "{name}: audit {index}");
    }
    assert_eq!(ours.len(), theirs.len(), "{name}: audits");
    let theirs_log = case["log"].as_array().unwrap();
    for (index, (a, b)) in log.iter().zip(theirs_log).enumerate() {
        assert_eq!(sorted(a), sorted(b), "{name}: log line {index}");
    }
    assert_eq!(log.len(), theirs_log.len(), "{name}: log");
    let _ = std::fs::remove_dir_all(home.parent().unwrap());
    println!("case {name} ... ok");
}

fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    if arguments.get(1).map(String::as_str) == Some("app-server") {
        std::process::exit(stand_in());
    }
    if arguments.iter().any(|argument| argument == "--list") {
        println!("chatgpt_provider_vectors: test");
        return;
    }
    let vectors = vectors();
    let now = vectors["now"].as_str().unwrap().to_owned();
    let runtime = tokio::runtime::Builder::new_multi_thread().enable_all().build().unwrap();
    for case in vectors["cases"].as_array().unwrap() {
        runtime.block_on(run_case(case, &now));
    }
    println!("\ntest result: ok. ChatGPTProvider matches Python");
}
