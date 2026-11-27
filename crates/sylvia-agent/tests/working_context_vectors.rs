//! Compare the working context and the request data (T2a) with Python.
//!
//! Regenerate with `tests/vectors/generate_working_context_vectors.py`. The request-data and
//! pure cases compare values. Each scenario runs one `WorkingContextProvider` operation around
//! a scripted provider and compares the result (or the error's class and message), every
//! request the wrapped provider was sent (messages, tools, timeout, options), the checkpoints
//! saved, the progress hints and the records left.

use futures_util::StreamExt;
use serde_json::Value as J;
use std::sync::{Arc, Mutex};
use sylvia_agent::request_data::{application_data_after_history, strip_data_markers};
use sylvia_agent::working_context::{
    durable_tool_result, estimate_tokens, safe_message, valid_checkpoint, WorkingContextOptions,
    WorkingContextProvider,
};
use sylvia_base::JsonMap;
use sylvia_provider::error::{ErrorKind, ProviderError, ProviderResult};
use sylvia_provider::types::{ModelInfo, ProviderResponse, ProviderStreamChunk, PyValue, ToolCall};
use sylvia_provider::{BoxFuture, ChunkStream, Provider, ProviderRequest};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/working_context_vectors.json"))
        .expect("vector file is valid JSON")
}

fn text(value: &J) -> String {
    serde_json::to_string(value).unwrap()
}

fn map(value: &J) -> JsonMap {
    value.as_object().cloned().unwrap_or_default()
}

fn maps(value: &J) -> Vec<JsonMap> {
    value.as_array().map(|items| items.iter().map(map).collect()).unwrap_or_default()
}

fn as_json(messages: Vec<JsonMap>) -> J {
    J::Array(messages.into_iter().map(J::Object).collect())
}

/// Compare two JSON texts, showing where they first differ.
fn same(name: &str, what: &str, ours: &J, theirs: &J) {
    let (a, b) = (text(ours), text(theirs));
    if a != b {
        let at = a.bytes().zip(b.bytes()).position(|(x, y)| x != y).unwrap_or(a.len().min(b.len()));
        let from = at.saturating_sub(200);
        panic!(
            "{name}: {what} differs at byte {at}\n  expected …{}\n  got      …{}",
            &b[from..(at + 300).min(b.len())],
            &a[from..(at + 300).min(a.len())]
        );
    }
}

#[test]
fn request_data_matches_python() {
    for (index, case) in vectors()["request_data"].as_array().unwrap().iter().enumerate() {
        let messages = maps(&case["messages"]);
        let after = application_data_after_history(&messages).expect("the vectors parse");
        same(&format!("case {index}"), "after_history", &as_json(after), &case["after_history"]);
        same(
            &format!("case {index}"),
            "stripped",
            &as_json(strip_data_markers(&messages)),
            &case["stripped"],
        );
    }
}

#[test]
fn pure_helpers_match_python() {
    let vectors = vectors();
    let pure = &vectors["pure"];
    for case in pure["safe_message"].as_array().unwrap() {
        same("safe_message", "safe", &J::Object(safe_message(&map(&case["message"]))), &case["safe"]);
    }
    for case in pure["durable"].as_array().unwrap() {
        same(
            "durable",
            case["tool"].as_str().unwrap(),
            &durable_tool_result(case["tool"].as_str().unwrap(), &case["result"]),
            &case["durable"],
        );
    }
    for case in pure["valid_checkpoint"].as_array().unwrap() {
        assert_eq!(J::Bool(valid_checkpoint(&map(&case["record"]))), case["valid"], "{}", case["record"]);
    }
    for case in pure["estimate_tokens"].as_array().unwrap() {
        assert_eq!(J::from(estimate_tokens(&case["value"])), case["tokens"], "{}", case["value"]);
    }
}

/// The groups' keys show through `project`: a checkpoint covering the first `n` groups'
/// keys projects only when the Rust fingerprints are Python's.
#[test]
fn group_keys_match_python() {
    let vectors = vectors();
    let groups = &vectors["pure"]["groups"];
    let messages = maps(&groups["messages"]);
    let all: Vec<J> = groups["groups"]
        .as_array()
        .unwrap()
        .iter()
        .flat_map(|group| group["keys"].as_array().unwrap().clone())
        .collect();
    let mut covered = Vec::new();
    for (index, group) in groups["groups"].as_array().unwrap().iter().enumerate() {
        covered.extend(group["keys"].as_array().unwrap().iter().cloned());
        let record = serde_json::json!({
            "kind": "working_context_checkpoint", "version": 1, "checkpoint_id": format!("c{index}"),
            "summary": "note", "source_archive": "[]", "covered_keys": covered,
        });
        let mut options = WorkingContextOptions::new(Arc::new(|_| Box::pin(async { Ok(()) })));
        options.records = vec![map(&record)];
        let wrapper = WorkingContextProvider::new(Arc::new(Scripted::new(None, &J::Null)), options);
        let projected = wrapper.project(&messages).unwrap();
        assert_ne!(projected, messages, "group {index}: the checkpoint over its keys projects");
    }
    assert!(!all.is_empty());
    let pinned = maps(&groups["pinned"]);
    let mut options = WorkingContextOptions::new(Arc::new(|_| Box::pin(async { Ok(()) })));
    options.records = vec![map(&serde_json::json!({
        "kind": "working_context_checkpoint", "version": 1, "checkpoint_id": "all",
        "summary": "note", "source_archive": "[]", "covered_keys": all,
    }))];
    let wrapper = WorkingContextProvider::new(Arc::new(Scripted::new(None, &J::Null)), options);
    let projected = wrapper.project(&messages).unwrap();
    same("groups", "pinned", &as_json(projected[..pinned.len()].to_vec()), &groups["pinned"]);
}

/// `extend_source`: a source appended to after the request reads back as Python's list object
/// does, the same as a request whose list already held the appended messages.
#[tokio::test]
async fn extended_source_reads_as_the_appended_list() {
    let sent = maps(&serde_json::json!([
        {"role": "system", "content": "Rules"},
        {"role": "user", "content": "Find the ledger"},
    ]));
    let appended = maps(&serde_json::json!([
        {"role": "assistant", "content": null, "tool_calls": [
            {"id": "call-1", "type": "function",
             "function": {"name": "read_session_history", "arguments": "{}"}}]},
        {"role": "tool", "tool_call_id": "call-1", "content": "{\"ok\":true}"},
    ]));
    let current = |wrapper: &WorkingContextProvider| {
        wrapper.read_history(&map(&serde_json::json!({"source_id": "current"})))
    };
    let wrapper = |messages: &[JsonMap]| {
        let messages = messages.to_vec();
        async move {
            let options = WorkingContextOptions::new(Arc::new(|_| Box::pin(async { Ok(()) })));
            let wrapper = WorkingContextProvider::new(Arc::new(Scripted::new(None, &J::Null)), options);
            wrapper.prepare(&messages, None, false, &JsonMap::new()).await.unwrap();
            wrapper
        }
    };
    let extended = wrapper(&sent).await;
    let before = current(&extended);
    for message in &appended {
        extended.extend_source(std::slice::from_ref(message));
    }
    let whole = wrapper(&[sent.clone(), appended.clone()].concat()).await;
    assert_eq!(current(&extended), current(&whole));
    assert_ne!(current(&extended), before, "the appended messages are read");
}

/// A provider answering from the recorded script and recording what it is sent.
struct Scripted {
    model: Option<ModelInfo>,
    generate: Mutex<Vec<J>>,
    streams: Mutex<Vec<J>>,
    calls: Mutex<Vec<J>>,
}

fn model(value: &J) -> Option<ModelInfo> {
    let value = value.as_object()?;
    let text = |key: &str| value[key].as_str().unwrap();
    let mut model = ModelInfo::new(text("id"), text("id"), "p", text("provider_key"), "api");
    model.transport = text("transport").into();
    model.context_window = value["context_window"].as_i64();
    model.max_output_tokens = value["max_output_tokens"].as_i64();
    model.builder_output_tokens = value["builder_output_tokens"].as_i64().unwrap_or(0);
    Some(model)
}

fn kind(class: &str) -> ErrorKind {
    *ErrorKind::ALL.iter().find(|kind| kind.class_name() == class).unwrap_or_else(|| panic!("class {class}"))
}

fn error(spec: &J) -> ProviderError {
    ProviderError::new(kind(spec["class"].as_str().unwrap()), spec["message"].as_str().unwrap())
}

impl Scripted {
    fn new(model: Option<ModelInfo>, script: &J) -> Self {
        let list = |key: &str| script.get(key).and_then(J::as_array).cloned().unwrap_or_default();
        Self {
            model,
            generate: Mutex::new(list("generate")),
            streams: Mutex::new(list("streams")),
            calls: Mutex::new(Vec::new()),
        }
    }

    fn record(&self, kind: &str, request: &ProviderRequest) {
        self.calls.lock().unwrap().push(serde_json::json!({
            "kind": kind,
            "messages": request.messages,
            "tools": request.tools,
            "timeout": request.timeout,
            "options": request.options,
        }));
    }
}

fn response(step: &J) -> ProviderResponse {
    let tool_calls = step
        .get("tool_calls")
        .and_then(J::as_array)
        .map(|calls| {
            calls
                .iter()
                .map(|call| {
                    ToolCall::new(
                        call["id"].as_str().unwrap(),
                        call["name"].as_str().unwrap(),
                        call["arguments"].as_str().unwrap(),
                    )
                })
                .collect()
        })
        .unwrap_or_default();
    ProviderResponse {
        content: step.get("content").and_then(J::as_str).map(str::to_owned),
        tool_calls,
        finish_reason: PyValue::Text(step.get("finish_reason").and_then(J::as_str).unwrap_or("stop").into()),
        ..ProviderResponse::default()
    }
}

fn chunk(step: &J) -> ProviderStreamChunk {
    let text = |key: &str| step.get(key).and_then(J::as_str).unwrap_or("").to_owned();
    ProviderStreamChunk {
        content: text("content"),
        reasoning_content: text("reasoning_content"),
        tool_call_delta: step.get("tool_call_delta").cloned().filter(|delta| !delta.is_null()),
        finish_reason: step
            .get("finish_reason")
            .and_then(J::as_str)
            .map(|reason| PyValue::Text(reason.into())),
        ..ProviderStreamChunk::default()
    }
}

impl Provider for Scripted {
    fn model_info(&self) -> Option<&ModelInfo> {
        self.model.as_ref()
    }

    fn generate(&self, request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        self.record("generate", &request);
        let step = {
            let mut steps = self.generate.lock().unwrap();
            (!steps.is_empty()).then(|| steps.remove(0))
        };
        Box::pin(async move {
            let Some(step) = step else {
                return Err(ProviderError::new(ErrorKind::Provider, "script exhausted"));
            };
            match step.get("raise") {
                Some(spec) => Err(error(spec)),
                None => Ok(response(&step)),
            }
        })
    }

    fn generate_stream(&self, request: ProviderRequest) -> ChunkStream<'_> {
        self.record("stream", &request);
        let steps = {
            let mut streams = self.streams.lock().unwrap();
            if streams.is_empty() {
                vec![serde_json::json!({"raise": {"class": "ProviderError", "message": "script exhausted"}})]
            } else {
                streams.remove(0).as_array().unwrap().clone()
            }
        };
        Box::pin(futures_util::stream::iter(steps.into_iter().map(|step| match step.get("raise") {
            Some(spec) => Err(error(spec)),
            None => Ok(chunk(&step)),
        })))
    }

    fn close(&self) -> BoxFuture<'_, ()> {
        Box::pin(async {})
    }

    fn release_idle(&self) -> BoxFuture<'_, ()> {
        self.calls.lock().unwrap().push(serde_json::json!({"kind": "release_idle"}));
        Box::pin(async {})
    }
}

fn error_json(error: &ProviderError) -> J {
    serde_json::json!({"class": error.kind.class_name(), "message": error.message})
}

fn response_json(response: &ProviderResponse) -> J {
    let calls: Vec<J> = response
        .tool_calls
        .iter()
        .map(|call| serde_json::json!({"id": call.id.to_json(), "name": call.name.to_json(), "arguments": call.arguments.to_json()}))
        .collect();
    serde_json::json!({"content": response.content, "finish_reason": response.finish_reason.to_json(), "tool_calls": calls})
}

fn chunk_json(chunk: &ProviderStreamChunk) -> J {
    serde_json::json!({
        "content": chunk.content,
        "reasoning_content": chunk.reasoning_content,
        "tool_call_delta": chunk.tool_call_delta,
        "finish_reason": chunk.finish_reason.as_ref().map_or(J::Null, PyValue::to_json),
    })
}

fn tools(value: &J) -> Option<Vec<J>> {
    value.as_array().cloned()
}

#[tokio::test]
async fn scenarios_match_python() {
    for case in vectors()["scenarios"].as_array().unwrap() {
        let name = case["name"].as_str().unwrap();
        let scripted = Arc::new(Scripted::new(model(&case["model"]), case.get("script").unwrap_or(&J::Null)));
        let saved: Arc<Mutex<Vec<J>>> = Arc::default();
        let progress: Arc<Mutex<Vec<J>>> = Arc::default();
        let ids: Arc<Mutex<i64>> = Arc::default();
        let saving = Arc::clone(&saved);
        let mut options = WorkingContextOptions::new(Arc::new(move |payload: JsonMap| {
            saving.lock().unwrap().push(J::Object(payload));
            Box::pin(async { Ok(()) })
        }));
        options.records = maps(case.get("records").unwrap_or(&J::Null));
        let cancelled = case.get("cancelled").and_then(J::as_bool).unwrap_or(false);
        options.cancelled = Arc::new(move || cancelled);
        let hints = Arc::clone(&progress);
        options.progress =
            Arc::new(move |hint: Option<&str>| hints.lock().unwrap().push(hint.map_or(J::Null, J::from)));
        options.record_owner = case.get("record_owner").and_then(J::as_object).cloned();
        options.request_context_id = case.get("request_context_id").and_then(J::as_str).map(str::to_owned);
        options.pin_first_user = case.get("pin_first_user").and_then(J::as_bool).unwrap_or(true);
        let counter = Arc::clone(&ids);
        options.checkpoint_id = Arc::new(move || {
            let mut next = counter.lock().unwrap();
            *next += 1;
            format!("checkpoint-{next}")
        });
        let wrapper = WorkingContextProvider::new(Arc::clone(&scripted) as Arc<dyn Provider>, options);
        if case.get("history").and_then(J::as_bool).unwrap_or(false) {
            wrapper.set_history_retrieval_enabled(true);
        }
        let messages = maps(&case["messages"]);
        let offered = tools(case.get("tools").unwrap_or(&J::Null));
        let call_options = map(case.get("options").unwrap_or(&J::Null));
        let force = case.get("force").and_then(J::as_bool).unwrap_or(false);
        let request = |timeout: f64| ProviderRequest {
            messages: messages.iter().cloned().map(J::Object).collect(),
            tools: offered.clone(),
            stream: false,
            timeout,
            options: call_options.clone(),
        };
        let outcome = match case["op"].as_str().unwrap() {
            "project" => match wrapper.project(&messages) {
                Ok(result) => serde_json::json!({"result": as_json(result)}),
                Err(error) => serde_json::json!({"error": error_json(&error)}),
            },
            "budget_terms" => {
                let projected = wrapper.project(&messages).unwrap();
                match wrapper.budget_terms(&projected, offered.as_deref(), &call_options) {
                    Ok(terms) => serde_json::json!({"result": terms.to_json()}),
                    Err(error) => serde_json::json!({"error": error_json(&error)}),
                }
            }
            "prepare" => match wrapper.prepare(&messages, offered.as_deref(), force, &call_options).await {
                Ok(result) => serde_json::json!({"result": as_json(result)}),
                Err(error) => serde_json::json!({"error": error_json(&error)}),
            },
            "read_history" => {
                wrapper.prepare(&messages, offered.as_deref(), false, &JsonMap::new()).await.unwrap();
                let pages: Vec<J> = case["params"]
                    .as_array()
                    .unwrap()
                    .iter()
                    .map(|params| J::Object(wrapper.read_history(&map(params))))
                    .collect();
                serde_json::json!({"result": pages})
            }
            "generate" => {
                let timeout = case.get("timeout").and_then(J::as_f64).unwrap_or(60.0);
                match wrapper.generate(request(timeout)).await {
                    Ok(response) => serde_json::json!({"result": response_json(&response)}),
                    Err(error) => serde_json::json!({"error": error_json(&error)}),
                }
            }
            _ => {
                let mut stream = wrapper.generate_stream(request(120.0));
                let mut chunks = Vec::new();
                let mut failure = None;
                while let Some(item) = stream.next().await {
                    match item {
                        Ok(chunk) => chunks.push(chunk_json(&chunk)),
                        Err(error) => {
                            failure = Some(error_json(&error));
                            break;
                        }
                    }
                }
                match failure {
                    Some(error) => serde_json::json!({"chunks": chunks, "error": error}),
                    None => serde_json::json!({"chunks": chunks}),
                }
            }
        };
        same(name, "outcome", &outcome, &case["outcome"]);
        same(name, "calls", &J::Array(scripted.calls.lock().unwrap().clone()), &case["calls"]);
        same(name, "saved", &J::Array(saved.lock().unwrap().clone()), &case["saved"]);
        same(name, "progress", &J::Array(progress.lock().unwrap().clone()), &case["progress"]);
        same(name, "records_after", &as_json(wrapper.records()), &case["records_after"]);
    }
}
