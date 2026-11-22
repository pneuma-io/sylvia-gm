//! `provider/_admin_audit.py`: Admin Full Audit, the opt-in, current-process capture of exact
//! provider requests and responses.
//!
//! [`AdminAuditedProvider`] mirrors each call to the owner's in-memory sink (Python's
//! `record_admin_full_audit`): the neutral request, the provider-specific wire request seen by
//! a task-scoped hook on the wrapped provider, and the completed response or the failure. It
//! also records context-fit preflights, which may never reach a transport.
//!
//! Not reproduced exactly: the measured `duration_ms` (a clock reading in both) and the
//! random `audit-<uuid4 hex>` exchange ids. A call the caller stops is recorded when Rust drops
//! it, as `CancelledError` (an unfinished `generate`) or an `interrupted` stream; Python records
//! a cancelled task the same way.

use crate::context_mode::ledger_request;
use crate::error::ProviderResult;
use crate::hooks::{utc_now_isoformat, HookContext, LateUsageListener, ProviderHook};
use crate::provider::{BoxFuture, ChunkStream, Provider, ProviderRequest};
use crate::types::{CacheUsage, ModelInfo, ProviderResponse, ProviderStreamChunk, PyValue, TokenUsage};
use futures_util::StreamExt;
use serde_json::{json, Value as Json};
use std::sync::{Arc, Mutex};
use std::time::Instant;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::JsonMap;

/// The owner's audit sink (`record_admin_full_audit`).
pub type AuditSink = Arc<dyn Fn(Json) + Send + Sync>;

/// `_plain(TokenUsage)`.
fn plain_usage(usage: &TokenUsage) -> Json {
    json!({
        "prompt_tokens": usage.prompt_tokens.to_json(),
        "completion_tokens": usage.completion_tokens.to_json(),
        "total_tokens": usage.total_tokens.to_json(),
        "reasoning_tokens": usage.reasoning_tokens,
    })
}

/// `_plain(CacheUsage)`.
fn plain_cache(cache: &CacheUsage) -> Json {
    json!({
        "cache_read_tokens": cache.cache_read_tokens,
        "cache_write_tokens": cache.cache_write_tokens,
        "cache_miss_tokens": cache.cache_miss_tokens,
        "total_prompt_tokens": cache.total_prompt_tokens,
    })
}

/// `_plain(response)`: `dataclasses.asdict` of a `ProviderResponse`, in field order.
pub fn plain_response(response: &ProviderResponse) -> Json {
    let tool_calls: Vec<Json> = response
        .tool_calls
        .iter()
        .map(|call| json!({"id": call.id.to_json(), "name": call.name.to_json(), "arguments": call.arguments.to_json()}))
        .collect();
    json!({
        "content": response.content,
        "reasoning_content": response.reasoning_content,
        "tool_calls": tool_calls,
        "usage": plain_usage(&response.usage),
        "usage_reported": response.usage_reported,
        "model": response.model,
        "finish_reason": response.finish_reason.to_json(),
        "cache_usage": plain_cache(&response.cache_usage),
        "anthropic_content": response.anthropic_content,
        "chat_completion_metadata": response.chat_completion_metadata,
        "provider_reported_reasoning_effort": response.provider_reported_reasoning_effort,
        "provider_reported_service_tier": response.provider_reported_service_tier,
    })
}

/// `_plain(chunk)`: `dataclasses.asdict` of a `ProviderStreamChunk`, in field order.
pub fn plain_chunk(chunk: &ProviderStreamChunk) -> Json {
    json!({
        "content": chunk.content,
        "reasoning_content": chunk.reasoning_content,
        "tool_call_delta": chunk.tool_call_delta,
        "finish_reason": chunk.finish_reason.as_ref().map_or(Json::Null, PyValue::to_json),
        "anthropic_content": chunk.anthropic_content,
        "chat_completion_metadata": chunk.chat_completion_metadata,
        "provider_reported_reasoning_effort": chunk.provider_reported_reasoning_effort,
        "provider_reported_service_tier": chunk.provider_reported_service_tier,
    })
}

/// `value not in {None, 0}`: `False`, `0` and `0.0` count as zero.
fn counted(value: &Json) -> bool {
    match value {
        Json::Null | Json::Bool(false) => false,
        Json::Number(number) => number.as_f64() != Some(0.0),
        _ => true,
    }
}

/// `_usage_projection(response)`: provider-reported counts apart from absent usage.
pub fn usage_projection(response: Option<&ProviderResponse>) -> Json {
    let reported = match response {
        None => false,
        Some(response) => response.usage_reported.unwrap_or_else(|| {
            let usage = &response.usage;
            [
                usage.prompt_tokens.to_json(),
                usage.completion_tokens.to_json(),
                usage.total_tokens.to_json(),
                usage.reasoning_tokens.map_or(Json::Null, Json::from),
            ]
            .iter()
            .any(counted)
        }),
    };
    let when = |value: Json| if reported { value } else { Json::Null };
    let usage = response.map(|response| &response.usage);
    let cache = response.map(|response| &response.cache_usage);
    json!({
        "availability": if reported { "provider_returned" } else { "not_returned" },
        "precision": if reported { "authoritative_count" } else { "unavailable" },
        "source": if reported { "provider_reported_completed_request" } else { "unavailable" },
        "scope": if reported { "completed_request_only" } else { "unavailable" },
        "input_tokens": when(usage.map_or(Json::Null, |usage| usage.prompt_tokens.to_json())),
        "output_tokens": when(usage.map_or(Json::Null, |usage| usage.completion_tokens.to_json())),
        "total_tokens": when(usage.map_or(Json::Null, |usage| usage.total_tokens.to_json())),
        "cached_tokens": cache.and_then(|cache| cache.cache_read_tokens),
        "cache_write_tokens": cache.and_then(|cache| cache.cache_write_tokens),
        "reasoning_tokens": when(usage.and_then(|usage| usage.reasoning_tokens).map_or(Json::Null, Json::from)),
    })
}

/// `_serialization_projection(value, layer=...)`: one canonical serialization measured.
pub fn serialization_projection(value: &Json, layer: &str) -> Json {
    let encoded = pyjson::dump_bytes(value, Dump::CANONICAL);
    json!({
        "layer": layer,
        "encoding": "canonical_json_utf8",
        "bytes": encoded.len(),
        "sha256": sha256_hex(&encoded),
        "claim_boundary": "sylvia_owned_representation",
    })
}

/// `round(seconds * 1000, 3)`.
fn duration_ms(started: Instant) -> Json {
    let ms = started.elapsed().as_secs_f64() * 1000.0;
    serde_json::Number::from_f64((ms * 1000.0).round() / 1000.0).map_or(Json::Null, Json::Number)
}

/// `uuid.uuid4().hex`.
fn uuid4_hex() -> String {
    let mut bytes = [0u8; 16];
    getrandom::getrandom(&mut bytes).expect("the operating system supplies random bytes");
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    hex::encode(bytes)
}

/// `_CompletedResponseObserver`: the transport request and completed response of one call.
struct Observer {
    sink: AuditSink,
    session_id: String,
    role: String,
    exchange_id: String,
    correlation: JsonMap,
    response: Mutex<Option<ProviderResponse>>,
    retry_attempt: Mutex<i64>,
}

impl ProviderHook for Observer {
    fn name(&self) -> &str {
        "_CompletedResponseObserver"
    }

    fn on_request<'a>(&'a self, context: &'a HookContext) -> BoxFuture<'a, Result<(), String>> {
        Box::pin(async move {
            *self.retry_attempt.lock().expect("observer lock") = context.retry_attempt;
            if let Some(request_json) = &context.request_json {
                let request = Json::Object(request_json.clone());
                let runtime = matches!(context.provider_key.as_str(), "chatgpt_subscription" | "claude_runtime");
                let runtime_layer = if context.provider_key == "claude_runtime" {
                    "claude_code_boundary"
                } else {
                    "codex_app_server_boundary"
                };
                (self.sink)(json!({
                    "kind": if runtime { "provider_runtime_exchange" } else { "provider_wire_request" },
                    "input_stage": if runtime { runtime_layer } else { "provider_specific_wire_request" },
                    "exchange_id": self.exchange_id,
                    "session_id": self.session_id,
                    "role": self.role,
                    "retry_attempt": context.retry_attempt,
                    "request": request,
                    "serialization": serialization_projection(
                        &request,
                        if runtime { runtime_layer } else { "provider_client_request_representation" },
                    ),
                    "correlation": self.correlation,
                }));
            }
            Ok(())
        })
    }

    fn on_response<'a>(&'a self, context: &'a HookContext) -> BoxFuture<'a, Result<(), String>> {
        Box::pin(async move {
            *self.response.lock().expect("observer lock") = context.response.clone();
            *self.retry_attempt.lock().expect("observer lock") = context.retry_attempt;
            Ok(())
        })
    }

    fn on_error<'a>(&'a self, context: &'a HookContext) -> BoxFuture<'a, Result<(), String>> {
        Box::pin(async move {
            *self.retry_attempt.lock().expect("observer lock") = context.retry_attempt;
            Ok(())
        })
    }

    fn late_usage_listener(&self, _provider_key: &str, exchange_id: &str) -> Option<LateUsageListener> {
        let sink = Arc::clone(&self.sink);
        let exchange_id = exchange_id.to_owned();
        Some(Arc::new(move |response: &ProviderResponse| {
            sink(json!({"kind": "provider_late_usage", "exchange_id": exchange_id, "usage": usage_projection(Some(response))}));
        }))
    }
}

/// `AdminAuditedProvider`.
pub struct AdminAuditedProvider {
    provider: Arc<dyn Provider>,
    sink: AuditSink,
    session_id: String,
    role: String,
    provider_id: String,
    model_id: String,
    selection_mode: String,
    audit_context: Mutex<JsonMap>,
}

impl AdminAuditedProvider {
    /// `AdminAuditedProvider(provider, sink=..., session_id=..., role=..., provider_id=...,
    /// model_id=..., selection_mode=...)`. Python's defaults are `"unavailable"`,
    /// `"unavailable"` and `"session"`.
    pub fn new(
        provider: Arc<dyn Provider>,
        sink: AuditSink,
        session_id: impl Into<String>,
        role: impl Into<String>,
        provider_id: impl Into<String>,
        model_id: impl Into<String>,
        selection_mode: impl Into<String>,
    ) -> Self {
        Self {
            provider,
            sink,
            session_id: session_id.into(),
            role: role.into(),
            provider_id: provider_id.into(),
            model_id: model_id.into(),
            selection_mode: selection_mode.into(),
            audit_context: Mutex::new(JsonMap::new()),
        }
    }

    /// `_exchange_id()`: the ledger's mark on the Codex runtime, else a fresh id.
    fn exchange_id(&self) -> String {
        let codex = self.provider.model_info().is_some_and(|model| model.transport == "codex_app_server");
        if codex {
            if let Some(marked) = ledger_request() {
                return marked;
            }
        }
        format!("audit-{}", uuid4_hex())
    }

    /// `_provider_metadata()`: provider identity without credentials, headers or URL queries.
    fn provider_metadata(&self) -> Json {
        let model: Option<&ModelInfo> = self.provider.model_info();
        let mut endpoint = "unavailable".to_owned();
        if let Some(raw) = self.provider.base_url().filter(|raw| !raw.is_empty()) {
            if let Ok(parts) = crate::pyurl::urlsplit(raw) {
                if !parts.scheme.is_empty() && !parts.netloc.is_empty() {
                    let mut host = parts.hostname().unwrap_or_default();
                    if host.contains(':') && !host.starts_with('[') {
                        host = format!("[{host}]");
                    }
                    let authority = match parts.port() {
                        Ok(Some(port)) => format!("{host}:{port}"),
                        _ => host,
                    };
                    // `urlunsplit((scheme, authority, path, "", ""))`.
                    let path = if !parts.path.is_empty() && !parts.path.starts_with('/') {
                        format!("/{}", parts.path)
                    } else {
                        parts.path.clone()
                    };
                    endpoint = format!("{}://{authority}{path}", parts.scheme);
                }
            }
        }
        let text = |value: Option<&str>| value.unwrap_or("unavailable").to_owned();
        json!({
            "provider_id": self.provider_id,
            "provider_name": text(model.map(|model| model.provider_name.as_str())),
            "model_id": self.model_id,
            "wire_model_id": text(model.map(ModelInfo::wire_model_id)),
            "transport": text(model.map(|model| model.transport.as_str())),
            "endpoint": endpoint,
            "selection_mode": self.selection_mode,
        })
    }

    /// `_request(...)`: the neutral application request, and the clock started.
    fn record_request(&self, exchange_id: &str, request: &ProviderRequest, options: &JsonMap) -> Instant {
        let started_at = utc_now_isoformat();
        let messages = Json::Array(request.messages.clone());
        let tools = Json::Array(request.tools.clone().unwrap_or_default());
        let neutral = json!({"messages": messages, "tools": tools, "options": options});
        let correlation = std::mem::take(&mut *self.audit_context.lock().expect("audit context lock"));
        (self.sink)(json!({
            "kind": "provider_request",
            "input_stage": "application_provider_request",
            "exchange_id": exchange_id,
            "session_id": self.session_id,
            "role": self.role,
            "started_at_utc": started_at,
            "provider": self.provider_metadata(),
            "messages": neutral["messages"],
            "tools": neutral["tools"],
            "options": neutral["options"],
            "serialization": serialization_projection(&neutral, "neutral_application_request"),
            "correlation": correlation,
        }));
        Instant::now()
    }

    fn observer(&self, exchange_id: &str) -> Arc<Observer> {
        Arc::new(Observer {
            sink: Arc::clone(&self.sink),
            session_id: self.session_id.clone(),
            role: self.role.clone(),
            exchange_id: exchange_id.to_owned(),
            correlation: self.audit_context.lock().expect("audit context lock").clone(),
            response: Mutex::new(None),
            retry_attempt: Mutex::new(0),
        })
    }
}

/// `{"stream": stream, "timeout": timeout, **kwargs}`.
fn call_options(stream: bool, timeout: f64, kwargs: &JsonMap) -> JsonMap {
    let mut options = JsonMap::new();
    options.insert("stream".into(), stream.into());
    options.insert("timeout".into(), serde_json::Number::from_f64(timeout).map_or(Json::Null, Json::Number));
    for (key, value) in kwargs {
        options.insert(key.clone(), value.clone());
    }
    options
}

/// Records `provider_error` with `CancelledError` if the audited `generate` is dropped unfinished.
struct GenerateGuard {
    sink: AuditSink,
    exchange_id: String,
    started: Instant,
    finished: bool,
}

impl Drop for GenerateGuard {
    fn drop(&mut self) {
        if !self.finished {
            (self.sink)(json!({
                "kind": "provider_error",
                "exchange_id": self.exchange_id,
                "error_type": "CancelledError",
                "duration_ms": duration_ms(self.started),
            }));
        }
    }
}

/// The audited stream's running record; the final `provider_stream_response` is written once,
/// when the stream ends, fails or is dropped.
struct StreamRecord {
    sink: AuditSink,
    exchange_id: String,
    started: Instant,
    observer: Arc<Observer>,
    chunks: Vec<Json>,
    content: String,
    reasoning: String,
    tool_call_deltas: Vec<Json>,
    finish_reason: Json,
    reported_effort: Json,
    reported_tier: Json,
    written: bool,
}

impl StreamRecord {
    fn observe(&mut self, chunk: &ProviderStreamChunk) {
        self.chunks.push(plain_chunk(chunk));
        self.content.push_str(&chunk.content);
        self.reasoning.push_str(&chunk.reasoning_content);
        if let Some(delta) = &chunk.tool_call_delta {
            self.tool_call_deltas.push(delta.clone());
        }
        if let Some(reason) = &chunk.finish_reason {
            self.finish_reason = reason.to_json();
        }
        if let Some(effort) = &chunk.provider_reported_reasoning_effort {
            self.reported_effort = effort.clone().into();
        }
        if let Some(tier) = &chunk.provider_reported_service_tier {
            self.reported_tier = tier.clone().into();
        }
    }

    fn write(&mut self, status: &str) {
        if self.written {
            return;
        }
        self.written = true;
        let completed = self.observer.response.lock().expect("observer lock").clone();
        let retry = *self.observer.retry_attempt.lock().expect("observer lock");
        (self.sink)(json!({
            "kind": "provider_stream_response",
            "exchange_id": self.exchange_id,
            "status": status,
            "duration_ms": duration_ms(self.started),
            "chunks": self.chunks,
            "assembled_response": {
                "content": self.content,
                "reasoning_content": self.reasoning,
                "tool_call_deltas": self.tool_call_deltas,
                "finish_reason": self.finish_reason,
                "provider_reported_reasoning_effort": self.reported_effort,
                "provider_reported_service_tier": self.reported_tier,
            },
            "usage": usage_projection(completed.as_ref()),
            "retry_count": retry,
        }));
    }
}

impl Drop for StreamRecord {
    fn drop(&mut self) {
        if !self.written {
            self.chunks.push(json!({"error_type": "CancelledError"}));
            self.write("interrupted");
        }
    }
}

impl Provider for AdminAuditedProvider {
    fn model_info(&self) -> Option<&ModelInfo> {
        self.provider.model_info()
    }

    fn generate(&self, request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        Box::pin(async move {
            let exchange_id = self.exchange_id();
            let observer = self.observer(&exchange_id);
            let options = call_options(request.stream, request.timeout, &request.options);
            let started = self.record_request(&exchange_id, &request, &options);
            let mut guard =
                GenerateGuard { sink: Arc::clone(&self.sink), exchange_id: exchange_id.clone(), started, finished: false };
            let call = self.provider.generate(request);
            let outcome = match self.provider.hooks() {
                Some(hooks) => hooks.scoped(observer, call).await,
                None => call.await,
            };
            guard.finished = true;
            match &outcome {
                Ok(response) => (self.sink)(json!({
                    "kind": "provider_response",
                    "exchange_id": exchange_id,
                    "duration_ms": duration_ms(started),
                    "response": plain_response(response),
                    "usage": usage_projection(Some(response)),
                    "retry_count": "unavailable",
                })),
                Err(error) => (self.sink)(json!({
                    "kind": "provider_error",
                    "exchange_id": exchange_id,
                    "error_type": error.kind.class_name(),
                    "duration_ms": duration_ms(started),
                })),
            }
            outcome
        })
    }

    fn generate_stream(&self, request: ProviderRequest) -> ChunkStream<'_> {
        let exchange_id = self.exchange_id();
        let observer = self.observer(&exchange_id);
        let options = call_options(true, request.timeout, &request.options);
        let started = self.record_request(&exchange_id, &request, &options);
        let inner = self.provider.generate_stream(request);
        let inner: ChunkStream<'_> = match self.provider.hooks() {
            Some(hooks) => Box::pin(hooks.scoped_stream(Arc::clone(&observer) as Arc<dyn ProviderHook>, inner)),
            None => inner,
        };
        let record = StreamRecord {
            sink: Arc::clone(&self.sink),
            exchange_id,
            started,
            observer,
            chunks: Vec::new(),
            content: String::new(),
            reasoning: String::new(),
            tool_call_deltas: Vec::new(),
            finish_reason: Json::Null,
            reported_effort: Json::Null,
            reported_tier: Json::Null,
            written: false,
        };
        Box::pin(futures_util::stream::unfold(Some((inner, record)), |state| async move {
            let (mut inner, mut record) = state?;
            match inner.next().await {
                Some(Ok(chunk)) => {
                    record.observe(&chunk);
                    Some((Ok(chunk), Some((inner, record))))
                }
                Some(Err(error)) => {
                    record.chunks.push(json!({"error_type": error.kind.class_name()}));
                    record.write("failed");
                    Some((Err(error), None))
                }
                None => {
                    record.write("completed");
                    None
                }
            }
        }))
    }

    fn close(&self) -> BoxFuture<'_, ()> {
        self.provider.close()
    }

    fn release_idle(&self) -> BoxFuture<'_, ()> {
        self.provider.release_idle()
    }

    fn wrapped(&self) -> Option<&dyn Provider> {
        Some(self.provider.as_ref())
    }

    /// Attach application and MCP correlation to the next provider request.
    fn set_audit_context(&self, context: &JsonMap) -> bool {
        *self.audit_context.lock().expect("audit context lock") = context.clone();
        true
    }

    /// Record a preflight even when a rejected request never reaches the transport.
    fn record_context_fit(&self, context: &JsonMap) -> bool {
        (self.sink)(json!({
            "kind": "provider_context_fit",
            "session_id": self.session_id,
            "role": self.role,
            "provider": self.provider_metadata(),
            "correlation": context,
        }));
        true
    }
}
