//! `ClaudeProvider` against the frozen `provider/_claude.py`, both run against the same scripted
//! Claude Code (`vectors/generate_claude_provider_vectors.py`).
//!
//! This test runs without libtest's harness because it is also the stand-in Claude Code: copied
//! into each case's runtime home as `claude`, it plays `vectors/claude_standin.py`'s scripts the
//! same way. The cases run in two child runs of this binary, one per `SYLVIA_CLAUDE_PROCESS`
//! setting, since the provider reads that switch from its environment.

use serde_json::{json, Value};
use std::io::{BufRead, Write};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_provider::claude_native::{with_native_route, NativeCollection, NativeRoute};
use sylvia_provider::claude_provider::ClaudeProvider;
use sylvia_provider::claude_runtime;
use sylvia_provider::codex_rpc::python_resolve;
use sylvia_provider::hooks::{HookContext, ProviderHook};
use sylvia_provider::provider::BoxFuture;
use sylvia_provider::types::{transport, ModelInfo, ProviderResponse, ProviderStreamChunk};
use sylvia_provider::{Provider, ProviderRequest};

fn vectors() -> Value {
    serde_json::from_str(include_str!("vectors/claude_provider_vectors.json"))
        .expect("claude provider vectors")
}

/// A fresh work folder; an old one that cannot be removed fails here, with its path.
fn folder(name: &str) -> PathBuf {
    let path = std::env::temp_dir().join(format!("sylvia-claude-provider-{}-{name}", std::process::id()));
    if path.exists() {
        std::fs::remove_dir_all(&path)
            .unwrap_or_else(|error| panic!("cannot clear {}: {error}", path.display()));
    }
    std::fs::create_dir_all(&path).unwrap();
    path
}

// ── The stand-in Claude Code ─────────────────────────────────────────────────────────────

fn stand_in(arguments: &[String]) -> i32 {
    let home = std::env::current_dir().unwrap().parent().unwrap().to_owned();
    let script: Value = serde_json::from_slice(&std::fs::read(home.join("standin.json")).unwrap()).unwrap();
    let counter = home.join("standin-count");
    let run: usize = std::fs::read_to_string(&counter).ok().and_then(|text| text.parse().ok()).unwrap_or(0);
    std::fs::write(&counter, (run + 1).to_string()).unwrap();
    let log = |entry: Value| {
        let mut entry = entry.as_object().unwrap().clone();
        entry.insert("run".into(), json!(run));
        let mut file = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(home.join("standin-log.jsonl"))
            .unwrap();
        let line = pyjson::dumps(&Json::Object(entry), Dump { sort_keys: true, ..Dump::DEFAULT });
        file.write_all(format!("{line}\n").as_bytes()).unwrap();
    };
    log(json!({"argv": arguments}));
    for (flag, key) in [("--system-prompt-file", "system"), ("--resume", "history")] {
        if let Some(position) = arguments.iter().position(|argument| argument == flag) {
            let text = String::from_utf8(std::fs::read(&arguments[position + 1]).unwrap()).unwrap();
            log(json!({ key: text }));
        }
    }
    log(json!({"env": {"ENABLE_TOOL_SEARCH": std::env::var("ENABLE_TOOL_SEARCH").ok()}}));
    let session = arguments
        .iter()
        .position(|argument| argument == "--session-id")
        .map(|position| arguments[position + 1].clone());
    let mut received = 0;
    let mut prompt = Json::Null;
    let stdin = std::io::stdin();
    let mut lines = stdin.lock();
    let mut read_line = |received: &mut usize, prompt: &mut Json| -> bool {
        let mut line = String::new();
        if lines.read_line(&mut line).unwrap_or(0) == 0 {
            return false;
        }
        let message = pyjson::loads(&line).unwrap();
        *received += 1;
        log(json!({"stdin": message}));
        if message.get("type").and_then(Json::as_str) == Some("user") {
            *prompt = message.get("message").cloned().unwrap_or(Json::Null);
        }
        true
    };
    let empty = Vec::new();
    let items = script["runs"].as_array().unwrap().get(run).and_then(Value::as_array).unwrap_or(&empty);
    let stdout = std::io::stdout();
    for item in items {
        if let Some(count) = item.get("await").and_then(Value::as_u64) {
            while (received as u64) < count {
                if !read_line(&mut received, &mut prompt) {
                    break;
                }
            }
        } else if let Some(event) = item.get("emit") {
            let mut out = stdout.lock();
            out.write_all(format!("{}\n", pyjson::dumps(event, Dump::DEFAULT)).as_bytes()).unwrap();
            out.flush().unwrap();
        } else if let Some(rows) = item.get("session").and_then(Value::as_array) {
            let folder = PathBuf::from(std::env::var_os("CLAUDE_CONFIG_DIR").unwrap())
                .join("projects")
                .join("standin");
            std::fs::create_dir_all(&folder).unwrap();
            let path = folder.join(format!("{}.jsonl", session.as_deref().unwrap()));
            let mut file = std::fs::OpenOptions::new().create(true).append(true).open(path).unwrap();
            for row in rows {
                let row =
                    if row == "$prompt" { json!({"type": "user", "message": prompt}) } else { row.clone() };
                file.write_all(format!("{}\n", pyjson::dumps(&row, Dump::DEFAULT)).as_bytes()).unwrap();
            }
        } else if let Some(code) = item.get("exit").and_then(Value::as_i64) {
            stdout.lock().flush().unwrap();
            return code as i32;
        }
    }
    while read_line(&mut received, &mut prompt) {}
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
        "anthropic_content": response.anthropic_content,
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
        "anthropic_content": chunk.anthropic_content,
    })
}

fn route(spec: &Value, collected: Arc<Mutex<Vec<Value>>>) -> NativeRoute {
    let keyed = |key: &str| -> Vec<(usize, Value)> {
        spec[key]
            .as_object()
            .unwrap()
            .iter()
            .map(|(at, value)| (at.parse().unwrap(), value.clone()))
            .collect()
    };
    NativeRoute {
        stable_count: spec["stable_count"].as_u64().unwrap() as usize,
        entry_count: spec["entry_count"].as_u64().unwrap() as usize,
        boundaries: spec["boundaries"]
            .as_array()
            .unwrap()
            .iter()
            .map(|b| b.as_u64().unwrap() as usize)
            .collect(),
        native_records: keyed("native_records")
            .into_iter()
            .map(|(at, value)| (at, value.as_array().unwrap().clone()))
            .collect(),
        native_answers: keyed("native_answers").into_iter().collect(),
        on_collected: Some(Arc::new(move |found: NativeCollection| {
            collected.lock().unwrap().push(json!({
                "status": found.status, "records": found.records, "reason": found.reason, "answer": found.answer,
            }));
            Box::pin(async {})
        })),
    }
}

fn files(home: &Path) -> Vec<String> {
    fn walk(root: &Path, folder: &Path, out: &mut Vec<String>) {
        for entry in std::fs::read_dir(folder).into_iter().flatten().flatten() {
            let path = entry.path();
            if path.is_dir() {
                walk(root, &path, out);
            } else {
                let relative = path.strip_prefix(root).unwrap().to_string_lossy().replace('\\', "/");
                let name = path.file_name().unwrap().to_string_lossy().into_owned();
                if !["standin.json", "standin-count", "standin-log.jsonl"].contains(&name.as_str())
                    && !relative.starts_with("install/")
                {
                    out.push(relative);
                }
            }
        }
    }
    let mut out = Vec::new();
    walk(home, home, &mut out);
    out.sort();
    out
}

/// Replace the home's path with `<home>`, and on Linux and macOS write separators as `/` on
/// both sides (the vectors are made on Windows).
fn normalise(value: &Value, home: &str) -> Value {
    let escaped = serde_json::to_string(home).unwrap();
    let escaped = &escaped[1..escaped.len() - 1];
    match value {
        Value::String(text) => {
            let text = text.replace(escaped, "<home>").replace(home, "<home>");
            Value::String(if cfg!(windows) { text } else { text.replace("\\\\", "/").replace('\\', "/") })
        }
        Value::Array(items) => Value::Array(items.iter().map(|item| normalise(item, home)).collect()),
        Value::Object(map) => {
            Value::Object(map.iter().map(|(key, item)| (key.clone(), normalise(item, home))).collect())
        }
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
    let home = python_resolve(&folder(name)).join("claude-runtime");
    let install = home.join("install").join("2.1.322");
    std::fs::create_dir_all(&install).unwrap();
    std::fs::copy(std::env::current_exe().unwrap(), install.join(claude_runtime::executable_name())).unwrap();
    std::fs::write(home.join("standin.json"), json!({"runs": case["runs"]}).to_string()).unwrap();
    let mut model =
        ModelInfo::new("claude_runtime/claude-opus-5-5", "Opus", "Claude", "claude_runtime", "subscription");
    model.transport = transport::CLAUDE_CODE.into();
    model.max_output_tokens = None;
    let mut counter = 0u128;
    let clock = now.to_owned();
    let provider = ClaudeProvider::new(&home, model).with_ids(
        Box::new(move || {
            counter += 1;
            counter.to_be_bytes()
        }),
        Box::new(move || clock.clone()),
    );
    let recorder = Arc::new(Recorder::default());
    provider.hooks().unwrap().register(recorder.clone());
    let mut steps = Vec::new();
    for spec in case["steps_in"].as_array().unwrap() {
        tokio::time::sleep(std::time::Duration::from_secs_f64(spec["pause"].as_f64().unwrap())).await;
        let collected = Arc::new(Mutex::new(Vec::new()));
        let native = (!spec["route"].is_null()).then(|| route(&spec["route"], Arc::clone(&collected)));
        let request = ProviderRequest {
            messages: spec["messages"].as_array().unwrap().clone(),
            tools: spec["tools"].as_array().cloned(),
            stream: false,
            timeout: 20.0,
            options: JsonMap::new(),
        };
        let (chunks, error) = with_native_route(native, async {
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
        let figures = serde_json::to_value(json!({
            "context_window": figures.context_window,
            "context_window_source": figures.context_window_source,
            "runtime_context_window": figures.runtime_context_window,
            "runtime_context_source": figures.runtime_context_source,
            "runtime_context_observed_at": figures.runtime_context_observed_at,
            "context_observed_at": figures.context_observed_at,
            "configured_runtime_mismatch": figures.configured_runtime_mismatch,
        }))
        .unwrap();
        let collected = collected.lock().unwrap().clone();
        steps.push(json!({
            "chunks": chunks, "error": error, "collected": collected,
            "kept": sylvia_provider::claude_kept::kept_processes().len(), "figures": figures,
        }));
    }
    provider.close().await;
    let log_text = std::fs::read_to_string(home.join("standin-log.jsonl")).unwrap_or_default();
    let log: Vec<Value> = log_text.lines().map(|line| serde_json::from_str(line).unwrap()).collect();
    let home_text = home.to_string_lossy().into_owned();
    let ours = normalise(
        &json!({"steps": steps, "audits": *recorder.events.lock().unwrap(), "log": log}),
        &home_text,
    );
    let mut theirs =
        normalise(&json!({"steps": case["steps"], "audits": case["audits"], "log": case["log"]}), "\u{0}");
    if !cfg!(windows) {
        // `write_text` wrote the per-step transcript with Windows line endings.
        for entry in theirs["log"].as_array_mut().unwrap() {
            if let Some(Value::String(history)) = entry.get_mut("history") {
                *history = history.replace("\r\n", "\n");
            }
        }
    }
    for (index, (a, b)) in
        ours["steps"].as_array().unwrap().iter().zip(theirs["steps"].as_array().unwrap()).enumerate()
    {
        for key in ["error", "chunks", "collected", "kept", "figures"] {
            assert_eq!(dumps(&a[key]), dumps(&b[key]), "{name}: step {index} {key}");
        }
    }
    let (our_audits, their_audits) =
        (ours["audits"].as_array().unwrap(), theirs["audits"].as_array().unwrap());
    for (index, (a, b)) in our_audits.iter().zip(their_audits).enumerate() {
        assert_eq!(dumps(a), dumps(b), "{name}: audit {index}");
    }
    assert_eq!(our_audits.len(), their_audits.len(), "{name}: audits");
    let (our_log, their_log) = (ours["log"].as_array().unwrap(), theirs["log"].as_array().unwrap());
    for (index, (a, b)) in our_log.iter().zip(their_log).enumerate() {
        assert_eq!(sorted(a), sorted(b), "{name}: log line {index}");
    }
    assert_eq!(our_log.len(), their_log.len(), "{name}: log");
    assert_eq!(json!(files(&home)), case["files"], "{name}: files left in the home");
    let _ = std::fs::remove_dir_all(home.parent().unwrap());
}

/// Rust's side of differences row NEXT: a step dropped after its first chunk (Stop) closes its
/// process and removes its files from the drop guard.
async fn dropped_step(case: &Value, now: &str) {
    let home = python_resolve(&folder("dropped")).join("claude-runtime");
    let install = home.join("install").join("2.1.322");
    std::fs::create_dir_all(&install).unwrap();
    std::fs::copy(std::env::current_exe().unwrap(), install.join(claude_runtime::executable_name())).unwrap();
    std::fs::write(home.join("standin.json"), json!({"runs": case["runs"]}).to_string()).unwrap();
    let mut model =
        ModelInfo::new("claude_runtime/claude-opus-5-5", "Opus", "Claude", "claude_runtime", "subscription");
    model.transport = transport::CLAUDE_CODE.into();
    let clock = now.to_owned();
    let provider =
        ClaudeProvider::new(&home, model).with_ids(Box::new(|| [7; 16]), Box::new(move || clock.clone()));
    let spec = &case["steps_in"][0];
    let mut request = ProviderRequest::new(spec["messages"].as_array().unwrap().clone());
    request.tools = spec["tools"].as_array().cloned();
    let mut stream = provider.generate_stream(request);
    let first = futures_util::StreamExt::next(&mut stream).await.unwrap().unwrap();
    assert_eq!(first.content, "It ");
    assert!(!files(&home).is_empty(), "the step's files exist while it runs");
    drop(stream);
    let deadline = std::time::Instant::now() + std::time::Duration::from_secs(10);
    while !files(&home).is_empty() && std::time::Instant::now() < deadline {
        tokio::time::sleep(std::time::Duration::from_millis(50)).await;
    }
    assert_eq!(files(&home), Vec::<String>::new(), "a dropped step leaves no files");
    // The stand-in had started and received the prompt.
    let log = std::fs::read_to_string(home.join("standin-log.jsonl")).unwrap();
    assert!(log.lines().count() >= 5, "{log}");
    let _ = std::fs::remove_dir_all(home.parent().unwrap());
    println!("case dropped-step ... ok");
}

fn run_cases(persistent: bool) {
    let vectors = vectors();
    let now = vectors["now"].as_str().unwrap().to_owned();
    let runtime = tokio::runtime::Builder::new_multi_thread().enable_all().build().unwrap();
    let mut ran = 0;
    for case in vectors["cases"].as_array().unwrap() {
        if case["persistent"].as_bool().unwrap() != persistent {
            continue;
        }
        runtime.block_on(run_case(case, &now));
        println!("case {} ... ok", case["name"].as_str().unwrap());
        ran += 1;
    }
    assert!(ran > 0);
    if !persistent {
        let narrate =
            vectors["cases"].as_array().unwrap().iter().find(|case| case["name"] == "narrate").unwrap();
        runtime.block_on(dropped_step(narrate, &now));
    }
}

fn main() {
    let arguments: Vec<String> = std::env::args().collect();
    if arguments.len() == 2 && arguments[1] == "--version" {
        println!("2.1.322 (Claude Code)");
        return;
    }
    if arguments.iter().any(|argument| argument == "--print") {
        std::process::exit(stand_in(&arguments[1..]));
    }
    if arguments.get(1).map(String::as_str) == Some("--cases") {
        run_cases(arguments[2] == "persistent");
        return;
    }
    if arguments.iter().any(|argument| argument == "--list") {
        println!("claude_provider_vectors: test");
        return;
    }
    let me = std::env::current_exe().unwrap();
    for mode in ["per-step", "persistent"] {
        let status = std::process::Command::new(&me)
            .args(["--cases", mode])
            .env("SYLVIA_CLAUDE_PROCESS", mode)
            .status()
            .unwrap();
        assert!(status.success(), "the {mode} cases failed");
    }
    println!("\ntest result: ok. ClaudeProvider matches Python in both process modes");
}
