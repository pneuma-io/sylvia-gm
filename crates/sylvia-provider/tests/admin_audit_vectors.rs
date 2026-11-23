//! Compare Admin Full Audit with vectors written by the frozen Python reference.
//!
//! Regenerate with `tests/vectors/generate_admin_audit_vectors.py`. A fake provider fires its
//! hooks as a transport does; the audited wrapper's sink records must be Python's, with the
//! random exchange ids numbered by first appearance and the clock readings fixed on both sides.

use futures_util::StreamExt;
use serde_json::{json, Value as J};
use std::sync::{Arc, Mutex};
use sylvia_base::JsonMap;
use sylvia_provider::admin_audit::{usage_projection, AdminAuditedProvider};
use sylvia_provider::hooks::{HookContext, HookManager};
use sylvia_provider::types::{
    CacheUsage, ModelInfo, ProviderResponse, ProviderStreamChunk, PyValue, TokenUsage, ToolCall,
};
use sylvia_provider::{
    BoxFuture, ChunkStream, ErrorKind, Provider, ProviderError, ProviderRequest, ProviderResult,
};

fn vectors() -> J {
    serde_json::from_str(include_str!("vectors/admin_audit_vectors.json")).expect("vector file is valid JSON")
}

fn text(value: &J) -> String {
    serde_json::to_string(value).unwrap()
}

fn pyvalue(value: &J) -> PyValue {
    PyValue::from_json(value.clone())
}

fn response(value: &J) -> ProviderResponse {
    let mut response = ProviderResponse::default();
    let Some(fields) = value.as_object() else { return response };
    if let Some(content) = fields.get("content") {
        response.content = content.as_str().map(str::to_owned);
    }
    if let Some(usage) = fields.get("usage").and_then(J::as_object) {
        let mut tokens = TokenUsage::default();
        if let Some(value) = usage.get("prompt_tokens") {
            tokens.prompt_tokens = pyvalue(value);
        }
        if let Some(value) = usage.get("completion_tokens") {
            tokens.completion_tokens = pyvalue(value);
        }
        if let Some(value) = usage.get("total_tokens") {
            tokens.total_tokens = pyvalue(value);
        }
        tokens.reasoning_tokens = usage.get("reasoning_tokens").and_then(J::as_i64);
        response.usage = tokens;
    }
    response.usage_reported = fields.get("usage_reported").and_then(J::as_bool);
    if let Some(cache) = fields.get("cache_usage").and_then(J::as_object) {
        response.cache_usage = CacheUsage {
            cache_read_tokens: cache.get("cache_read_tokens").and_then(J::as_i64),
            cache_write_tokens: cache.get("cache_write_tokens").and_then(J::as_i64),
            cache_miss_tokens: cache.get("cache_miss_tokens").and_then(J::as_i64),
            total_prompt_tokens: cache.get("total_prompt_tokens").and_then(J::as_i64).unwrap_or(0),
        };
    }
    if let Some(calls) = fields.get("tool_calls").and_then(J::as_array) {
        response.tool_calls = calls
            .iter()
            .map(|call| {
                ToolCall::new(
                    call["id"].as_str().unwrap(),
                    call["name"].as_str().unwrap(),
                    call["arguments"].as_str().unwrap(),
                )
            })
            .collect();
    }
    if let Some(model) = fields.get("model").and_then(J::as_str) {
        response.model = model.into();
    }
    if let Some(reason) = fields.get("finish_reason") {
        response.finish_reason = pyvalue(reason);
    }
    response.chat_completion_metadata =
        fields.get("chat_completion_metadata").and_then(J::as_object).cloned();
    response
}

fn chunk(value: &J) -> ProviderStreamChunk {
    let text = |key: &str| value.get(key).and_then(J::as_str).unwrap_or("").to_owned();
    let opt = |key: &str| value.get(key).and_then(J::as_str).map(str::to_owned);
    ProviderStreamChunk {
        content: text("content"),
        reasoning_content: text("reasoning_content"),
        tool_call_delta: value.get("tool_call_delta").cloned(),
        finish_reason: value.get("finish_reason").map(pyvalue),
        provider_reported_reasoning_effort: opt("provider_reported_reasoning_effort"),
        provider_reported_service_tier: opt("provider_reported_service_tier"),
        ..ProviderStreamChunk::default()
    }
}

/// The generator's fake transport: it fires its hooks around each call.
struct Fake {
    case: J,
    model: Option<ModelInfo>,
    base_url: Option<String>,
    hooks: HookManager,
    late: Mutex<Vec<sylvia_provider::hooks::LateUsageListener>>,
}

impl Fake {
    fn context(&self, response: Option<ProviderResponse>) -> HookContext {
        HookContext {
            provider_key: self.case.get("provider_key").and_then(J::as_str).unwrap_or("test").into(),
            model_id: "test/model".into(),
            message_count: 1,
            tool_count: 0,
            request_json: Some(
                self.case
                    .get("wire")
                    .and_then(J::as_object)
                    .cloned()
                    .unwrap_or_else(|| json!({"model": "m", "messages": []}).as_object().unwrap().clone()),
            ),
            retry_attempt: self.case.get("retry_attempt").and_then(J::as_i64).unwrap_or(0),
            response,
            ..HookContext::default()
        }
    }

    fn fails(&self) -> bool {
        self.case.get("fail").and_then(J::as_bool).unwrap_or(false)
    }
}

impl Provider for Fake {
    fn model_info(&self) -> Option<&ModelInfo> {
        self.model.as_ref()
    }

    fn generate(&self, _request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        Box::pin(async move {
            self.hooks.fire_on_request(&self.context(None)).await;
            if self.case.get("late").and_then(J::as_bool).unwrap_or(false) {
                // A runtime binds its late-usage listeners during the call, then credits later.
                *self.late.lock().unwrap() = self.hooks.late_usage_listeners("test", "audit-late");
            }
            if self.fails() {
                self.hooks.fire_on_error(&self.context(None)).await;
                return Err(ProviderError::connection("Fake connection lost.", None));
            }
            let reply = match self.case.get("response") {
                Some(value) if !value.is_null() => response(value),
                _ => ProviderResponse { content: Some("ok".into()), ..ProviderResponse::default() },
            };
            self.hooks.fire_on_response(&self.context(Some(reply.clone()))).await;
            Ok(reply)
        })
    }

    fn generate_stream(&self, _request: ProviderRequest) -> ChunkStream<'_> {
        let chunks: Vec<ProviderStreamChunk> = self
            .case
            .get("chunks")
            .and_then(J::as_array)
            .map(|items| items.iter().map(chunk).collect())
            .unwrap_or_default();
        Box::pin(
            futures_util::stream::once(async move {
                self.hooks.fire_on_request(&self.context(None)).await;
                futures_util::stream::iter(chunks.into_iter().map(Ok))
            })
            .flatten()
            .chain(
                futures_util::stream::once(async move {
                    if self.fails() {
                        self.hooks.fire_on_error(&self.context(None)).await;
                        return Some(Err(ProviderError::new(ErrorKind::TokenLimit, "Fake token limit.")));
                    }
                    if let Some(value) = self.case.get("response").filter(|value| !value.is_null()) {
                        self.hooks.fire_on_response(&self.context(Some(response(value)))).await;
                    }
                    None
                })
                .filter_map(|item| async move { item }),
            ),
        )
    }

    fn close(&self) -> BoxFuture<'_, ()> {
        Box::pin(async {})
    }

    fn hooks(&self) -> Option<&HookManager> {
        Some(&self.hooks)
    }

    fn base_url(&self) -> Option<&str> {
        self.base_url.as_deref()
    }
}

/// Number exchange ids by first appearance, zero the durations and fix the start times.
fn normalise(records: Vec<J>) -> J {
    let mut ids: Vec<String> = Vec::new();
    fn walk(value: &J, ids: &mut Vec<String>) -> J {
        match value {
            J::Object(map) => J::Object(
                map.iter()
                    .map(|(key, item)| {
                        let item = match (key.as_str(), item) {
                            ("exchange_id", J::String(id)) => {
                                let index = ids.iter().position(|seen| seen == id).unwrap_or_else(|| {
                                    ids.push(id.clone());
                                    ids.len() - 1
                                });
                                J::String(format!("<exchange:{}>", index + 1))
                            }
                            ("duration_ms", _) => json!(0),
                            ("started_at_utc", _) => json!("<time>"),
                            _ => walk(item, ids),
                        };
                        (key.clone(), item)
                    })
                    .collect(),
            ),
            J::Array(items) => J::Array(items.iter().map(|item| walk(item, ids)).collect()),
            other => other.clone(),
        }
    }
    walk(&J::Array(records), &mut ids)
}

fn model(value: &J) -> Option<ModelInfo> {
    value.as_object().map(|_| {
        let get = |key: &str| value[key].as_str().unwrap();
        let mut model = ModelInfo::new(
            get("id"),
            get("name"),
            get("provider_name"),
            get("provider_key"),
            get("provider_mode"),
        );
        model.provider_model_id = get("provider_model_id").into();
        model
    })
}

#[tokio::test]
async fn audit_records_match_python() {
    let vectors = vectors();
    let mut failures = Vec::new();
    for case in vectors["cases"].as_array().unwrap() {
        let records: Arc<Mutex<Vec<J>>> = Arc::default();
        let sink = {
            let records = Arc::clone(&records);
            Arc::new(move |record: J| records.lock().unwrap().push(record))
        };
        let fake = Arc::new(Fake {
            case: case.clone(),
            model: model(&case["model"]),
            base_url: case.get("base_url").and_then(J::as_str).map(str::to_owned),
            hooks: HookManager::new(),
            late: Mutex::default(),
        });
        let audited = AdminAuditedProvider::new(
            Arc::clone(&fake) as Arc<dyn Provider>,
            sink,
            "session-1",
            "dm",
            "test",
            "test/model-a",
            "session",
        );
        if let Some(context) = case.get("audit_context").and_then(J::as_object) {
            audited.set_audit_context(context);
        }
        let tools = match case.get("tools") {
            Some(J::Null) => None,
            _ => Some(vec![json!({"type": "function", "function": {"name": "roll"}})]),
        };
        let messages = vec![json!({"role": "user", "content": "Hola"})];
        match case["operation"].as_str().unwrap() {
            "context_fit" => {
                audited.record_context_fit(case["context"].as_object().unwrap());
            }
            "generate" => {
                let options: JsonMap =
                    case.get("options").and_then(J::as_object).cloned().unwrap_or_default();
                let request = ProviderRequest { messages, tools, stream: false, timeout: 60.0, options };
                let _ = audited.generate(request).await;
                for listener in fake.late.lock().unwrap().iter() {
                    listener(&response(&case["response"]));
                }
            }
            "stream" => {
                let request = ProviderRequest {
                    messages,
                    tools,
                    stream: false,
                    timeout: 30.0,
                    options: JsonMap::new(),
                };
                let _: Vec<_> = audited.generate_stream(request).collect().await;
            }
            other => panic!("unknown operation {other}"),
        }
        let actual = normalise(records.lock().unwrap().clone());
        if text(&actual) != text(&case["records"]) {
            failures.push(format!(
                "{}\n  expected {}\n  got      {}",
                case["name"],
                text(&case["records"]),
                text(&actual)
            ));
        }
    }
    assert!(
        failures.is_empty(),
        "{} differ:\n{}",
        failures.len(),
        failures[..failures.len().min(3)].join("\n")
    );
}

#[test]
fn usage_projections_match_python() {
    let vectors = vectors();
    for case in vectors["usage"].as_array().unwrap() {
        let reply = case["response"].as_object().map(|_| response(&case["response"]));
        assert_eq!(text(&usage_projection(reply.as_ref())), text(&case["usage"]), "{case}");
    }
}

/// A provider whose calls never finish: one chunk, then nothing.
struct Hanging;

impl Provider for Hanging {
    fn model_info(&self) -> Option<&ModelInfo> {
        None
    }
    fn generate(&self, _request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        Box::pin(futures_util::future::pending())
    }
    fn generate_stream(&self, _request: ProviderRequest) -> ChunkStream<'_> {
        Box::pin(
            futures_util::stream::iter([Ok(ProviderStreamChunk {
                content: "Hel".into(),
                ..ProviderStreamChunk::default()
            })])
            .chain(futures_util::stream::pending()),
        )
    }
    fn close(&self) -> BoxFuture<'_, ()> {
        Box::pin(async {})
    }
}

/// Differences row 44 (Admin Full Audit early close): a dropped call is recorded as cancelled.
#[tokio::test]
async fn a_dropped_call_is_recorded_as_cancelled() {
    use futures_util::FutureExt;
    let records: Arc<Mutex<Vec<J>>> = Arc::default();
    let sink = {
        let records = Arc::clone(&records);
        Arc::new(move |record: J| records.lock().unwrap().push(record))
    };
    let audited = AdminAuditedProvider::new(
        Arc::new(Hanging),
        sink,
        "session-1",
        "dm",
        "test",
        "test/model-a",
        "session",
    );
    let request = || ProviderRequest::new(vec![json!({"role": "user", "content": "Hola"})]);
    let mut stream = audited.generate_stream(request());
    assert!(stream.next().await.is_some_and(|chunk| chunk.is_ok()));
    drop(stream);
    assert!(audited.generate(request()).now_or_never().is_none(), "the call is still running when dropped");
    let records = records.lock().unwrap().clone();
    let kinds: Vec<&str> = records.iter().map(|record| record["kind"].as_str().unwrap()).collect();
    assert_eq!(kinds, ["provider_request", "provider_stream_response", "provider_request", "provider_error"]);
    assert_eq!(records[1]["status"], "interrupted");
    assert_eq!(records[1]["chunks"][1], json!({"error_type": "CancelledError"}));
    assert_eq!(records[1]["assembled_response"]["content"], "Hel");
    assert_eq!(records[3]["error_type"], "CancelledError");
}
