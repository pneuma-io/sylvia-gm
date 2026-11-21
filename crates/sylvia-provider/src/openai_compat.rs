//! `OpenAICompatProvider` (`provider/_openai_compat.py`): any OpenAI-compatible Chat Completions
//! endpoint, over an [`HttpTransport`].
//!
//! The OpenAI SDK's part of each request is reproduced here: the URL (`httpx.URL` with the
//! SDK's trailing-slash join), the body (`chat::sdk_request`), and the headers Sylvia or the
//! protocol sets, in the SDK's order. Headers the SDK adds about itself (`X-Stainless-*`, its
//! `User-Agent`) and httpx's own (`Accept-Encoding`, `Connection`) are not imitated; Sylvia sends
//! `User-Agent: SylviaGM/<version>` instead (`differences.md`).
//!
//! The control flow follows Python's, including which failures fire `on_error`, which are
//! retried, and which escape unconverted.

use crate::chat::{self, ChatError, ChatStreamState};
use crate::error::{ErrorKind, ProviderError, ProviderResult};
use crate::hooks::{utc_now_isoformat, HookContext, HookManager};
use crate::http::{HttpRequest, HttpResponse, HttpTransport, TransportError};
use crate::httpx_url::HttpxUrl;
use crate::provider::{BoxFuture, ChunkStream, Provider, ProviderRequest};
use crate::pyobj::Incidental;
use crate::sse::{stream_item, DoneTracker, ServerSentEvent, SseDecoder, StreamItem};
use crate::types::{
    ModelInfo, ProviderResponse, ProviderStreamChunk, RetryConfig, TimeoutConfig, TokenUsage,
};
use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use sylvia_base::{Json, JsonMap};

/// The API key the SDK is given when the connection has none (local servers such as LM Studio).
pub const CREDENTIALLESS_API_KEY: &str = "sylvia-gm-local";

/// `User-Agent` in place of the SDK's own (`differences.md`).
pub fn sylvia_user_agent() -> String {
    format!("SylviaGM/{}", env!("CARGO_PKG_VERSION"))
}

/// Why a provider could not be built. Python raises these from `__init__`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum BuildError {
    /// `ValueError` from `urlsplit` (the owner answers `command_rejected` with this message).
    Value(String),
    /// `httpx.InvalidURL` from the SDK client (not a `ValueError`: the owner's `internal_error`).
    InvalidUrl(String),
}

/// The environment the SDK reads when the client is built.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct SdkEnvironment {
    /// `OPENAI_ORG_ID`, sent as `OpenAI-Organization`.
    pub organization: Option<String>,
    /// `OPENAI_PROJECT_ID`, sent as `OpenAI-Project`.
    pub project: Option<String>,
}

impl SdkEnvironment {
    /// Read `OPENAI_ORG_ID` and `OPENAI_PROJECT_ID` as `AsyncOpenAI()` does.
    pub fn from_process() -> Self {
        Self {
            organization: std::env::var("OPENAI_ORG_ID").ok(),
            project: std::env::var("OPENAI_PROJECT_ID").ok(),
        }
    }
}

/// The provider for one OpenAI-compatible model.
pub struct OpenAICompatProvider {
    base_url: String,
    hostname: Option<String>,
    model_info: ModelInfo,
    retry_config: RetryConfig,
    timeout_config: TimeoutConfig,
    default_headers: Vec<(String, String)>,
    api_key: String,
    environment: SdkEnvironment,
    endpoint: String,
    preserve_reasoning: bool,
    preserve_content: bool,
    transport: Arc<dyn HttpTransport>,
    /// `provider.hooks`.
    pub hooks: HookManager,
    consecutive_errors: AtomicU32,
    closed: AtomicBool,
}

/// Python's `MAX_CONSECUTIVE_CONNECTION_ERRORS`.
pub const MAX_CONSECUTIVE_CONNECTION_ERRORS: u32 = 3;

fn dict_insert(headers: &mut Vec<(String, String)>, name: &str, value: &str) {
    // A Python dict: the same key (case-sensitive) keeps its place and takes the new value.
    match headers.iter_mut().find(|(existing, _)| existing == name) {
        Some(slot) => slot.1 = value.to_owned(),
        None => headers.push((name.to_owned(), value.to_owned())),
    }
}

impl OpenAICompatProvider {
    /// `OpenAICompatProvider(base_url, api_key, model_info, retry_config, ..., default_headers)`.
    ///
    /// The arguments mirror Python's constructor, plus the SDK's environment and the transport.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        base_url: &str,
        api_key: Option<&str>,
        model_info: Option<ModelInfo>,
        retry_config: Option<RetryConfig>,
        timeout_config: Option<TimeoutConfig>,
        default_headers: Vec<(String, String)>,
        environment: SdkEnvironment,
        transport: Arc<dyn HttpTransport>,
    ) -> Result<Self, BuildError> {
        let model_info =
            model_info.unwrap_or_else(|| ModelInfo::new("unknown", "Unknown", "BYOK", "byok", "byok"));
        let hostname =
            crate::pyurl::urlsplit(base_url).map_err(|error| BuildError::Value(error.0.into()))?.hostname();
        let (preserve_reasoning, preserve_content) =
            chat::preserve_flags(hostname.as_deref(), &model_info.provider_key);
        let path = if model_info.transport == crate::types::transport::RESPONSES {
            "/responses"
        } else {
            "/chat/completions"
        };
        let endpoint = HttpxUrl::sdk_endpoint(base_url, path)
            .map_err(|error| BuildError::InvalidUrl(error.0))?
            .to_string();
        Ok(Self {
            base_url: base_url.to_owned(),
            hostname,
            model_info,
            retry_config: retry_config.unwrap_or_default(),
            timeout_config: timeout_config.unwrap_or_default(),
            default_headers,
            api_key: api_key.unwrap_or("").to_owned(),
            environment,
            endpoint,
            preserve_reasoning,
            preserve_content,
            transport,
            hooks: HookManager::new(),
            consecutive_errors: AtomicU32::new(0),
            closed: AtomicBool::new(false),
        })
    }

    /// The configured base URL.
    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    /// The URL requests go to.
    pub fn endpoint(&self) -> &str {
        &self.endpoint
    }

    /// The timeouts the HTTP client should use.
    pub fn timeout_config(&self) -> &TimeoutConfig {
        &self.timeout_config
    }

    /// `_wire_messages`: Groq does not accept `name` on messages.
    fn wire_messages(&self, messages: &[Json]) -> Vec<Json> {
        if self.hostname.as_deref() == Some("api.groq.com") || self.model_info.provider_key == "groq" {
            return messages
                .iter()
                .map(|message| match message {
                    Json::Object(map) => Json::Object(
                        map.iter()
                            .filter(|(key, _)| *key != "name")
                            .map(|(k, v)| (k.clone(), v.clone()))
                            .collect(),
                    ),
                    other => other.clone(),
                })
                .collect();
        }
        messages.to_vec()
    }

    /// `_response_continuation_options`: OpenRouter's session id, and stateless reasoning on the
    /// direct OpenAI and xAI Responses APIs.
    fn response_continuation_options(&self, options: &mut JsonMap) {
        let context_id = options.shift_remove("_request_context_id");
        if self.hostname.as_deref() == Some("openrouter.ai") {
            if let Some(Json::String(context_id)) = &context_id {
                if context_id.starts_with("sylvia-") && context_id.chars().count() <= 256 {
                    let mut extra = match options.get("extra_body") {
                        Some(Json::Object(extra)) => extra.clone(),
                        _ => JsonMap::new(),
                    };
                    extra.insert("session_id".into(), Json::String(context_id.clone()));
                    options.insert("extra_body".into(), Json::Object(extra));
                }
            }
        }
        if self.model_info.transport != crate::types::transport::RESPONSES
            || !matches!(self.hostname.as_deref(), Some("api.openai.com" | "api.x.ai"))
        {
            return;
        }
        options.entry("store").or_insert(Json::Bool(false));
        // include = options.get("include", []); a list without the entry gets it appended.
        let include = options.get("include").cloned().unwrap_or_else(|| Json::Array(Vec::new()));
        if let Json::Array(mut include) = include {
            let wanted = Json::String("reasoning.encrypted_content".into());
            if !include.iter().any(|item| crate::pyobj::py_eq(item, &wanted)) {
                include.push(wanted);
                options.insert("include".into(), Json::Array(include));
            }
        }
    }

    /// `provider_request_representation(messages, tools, options)`: the exact provider-client
    /// request object, without sending it.
    pub fn provider_request_representation(
        &self,
        messages: &[Json],
        tools: Option<&[Json]>,
        options: &JsonMap,
    ) -> JsonMap {
        let mut options = options.clone();
        let stream = options.shift_remove("stream").is_some_and(|value| crate::pyobj::truthy(&value));
        options.shift_remove("timeout");
        options.shift_remove("_retry_attempt");
        self.response_continuation_options(&mut options);
        chat::format_request(
            self.model_info.wire_model_id(),
            &self.wire_messages(messages),
            tools,
            stream,
            &options,
        )
    }

    fn prepare(&self, request: &ProviderRequest, stream: bool) -> (JsonMap, HookContext) {
        let mut options = request.options.clone();
        let retry_attempt =
            options.shift_remove("_retry_attempt").and_then(|value| value.as_i64()).unwrap_or(0);
        self.response_continuation_options(&mut options);
        let tools = request.tools.as_deref();
        let request_json = chat::format_request(
            self.model_info.wire_model_id(),
            &self.wire_messages(&request.messages),
            tools,
            stream,
            &options,
        );
        let context = HookContext {
            provider_key: self.model_info.provider_key.clone(),
            model_id: self.model_info.id.clone(),
            message_count: request.messages.len(),
            tool_count: request.tools.as_ref().map_or(0, Vec::len),
            request_json: Some(request_json.clone()),
            retry_attempt,
            timestamp_iso: utc_now_isoformat(),
            stream,
            ..HookContext::default()
        };
        (request_json, context)
    }

    /// The headers the SDK sends that Sylvia or the protocol sets, in the SDK's order:
    /// `{**auth, **default_headers}` merged with the call's `extra_headers`.
    fn headers(&self, extra_headers: Option<&Json>) -> Result<Vec<(String, String)>, Incidental> {
        let mut headers = Vec::new();
        let key = if self.api_key.is_empty() { CREDENTIALLESS_API_KEY } else { &self.api_key };
        dict_insert(&mut headers, "Authorization", &format!("Bearer {key}"));
        dict_insert(&mut headers, "Accept", "application/json");
        dict_insert(&mut headers, "Content-Type", "application/json");
        dict_insert(&mut headers, "User-Agent", &sylvia_user_agent());
        for (name, value) in &self.default_headers {
            dict_insert(&mut headers, name, value);
        }
        if let Some(organization) = &self.environment.organization {
            dict_insert(&mut headers, "OpenAI-Organization", organization);
        }
        if let Some(project) = &self.environment.project {
            dict_insert(&mut headers, "OpenAI-Project", project);
        }
        for (name, value) in &self.default_headers {
            dict_insert(&mut headers, name, value);
        }
        match extra_headers {
            None | Some(Json::Null) => {}
            Some(Json::Object(extra)) => {
                for (name, value) in extra {
                    let Some(value) = value.as_str() else {
                        return Err(Incidental(format!(
                            "Header value must be str or bytes, not {}",
                            crate::pyobj::type_name(value)
                        )));
                    };
                    dict_insert(&mut headers, name, value);
                }
            }
            Some(other) => {
                return Err(Incidental(format!(
                    "'{}' object is not a mapping",
                    crate::pyobj::type_name(other)
                )));
            }
        }
        Ok(headers)
    }

    fn http_request(&self, request_json: &JsonMap) -> Result<HttpRequest, Incidental> {
        let sdk = chat::sdk_request(request_json)?;
        if sdk.extra_query.as_ref().is_some_and(|query| !query.is_null()) {
            // Sylvia never passes one; the SDK would merge it into the URL.
            return Err(Incidental("extra_query is not supported by the Rust provider".into()));
        }
        Ok(HttpRequest {
            method: "POST".into(),
            url: self.endpoint.clone(),
            headers: self.headers(sdk.extra_headers.as_ref())?,
            body: sdk.body_bytes(),
        })
    }

    fn timed_out_message(&self, prefix: &str, timeout: f64) -> String {
        format!(
            "{prefix} {} timed out after {}s",
            self.model_info.provider_name,
            sylvia_base::pyjson::float_repr(timeout)
        )
    }

    async fn recycle_pool(&self) {
        crate::log::warning(&format!(
            "Recycling connection pool for {} after {} consecutive errors",
            self.model_info.provider_name,
            self.consecutive_errors.load(Ordering::SeqCst)
        ));
        self.transport.recycle().await;
        self.consecutive_errors.store(0, Ordering::SeqCst);
    }

    fn error_context(context: &HookContext, error: &ProviderError, started: Instant) -> HookContext {
        HookContext {
            error: Some(error.clone()),
            duration_ms: started.elapsed().as_secs_f64() * 1000.0,
            ..context.clone()
        }
    }

    /// One attempt of `generate`'s `_call`.
    async fn call_once(&self, request_json: &JsonMap, timeout: f64) -> Result<ProviderResponse, CallFailure> {
        let http = self.http_request(request_json).map_err(CallFailure::Incidental)?;
        let wait = Duration::from_secs_f64(timeout.max(0.0));
        let outcome = tokio::time::timeout(wait, async {
            let mut response = self.transport.send(http).await.map_err(CallFailure::from_transport)?;
            let body = response.read_all().await.map_err(CallFailure::from_transport)?;
            if response.status >= 400 {
                let message = chat::status_error_message(response.status, &body);
                let error = chat::status_error(
                    response.status,
                    &message,
                    response.header("retry-after"),
                    Some(&self.retry_config.retryable_statuses),
                );
                return Err(CallFailure::Provider(error));
            }
            sdk_completion_data(&response, &body).map_err(CallFailure::Incidental)
        })
        .await;
        let data = match outcome {
            Err(_) => {
                return Err(CallFailure::Provider(ProviderError::new(
                    ErrorKind::Timeout,
                    self.timed_out_message("Request to", timeout),
                )))
            }
            Ok(result) => result?,
        };
        chat::parse_completion(&data, &self.model_info.id, self.preserve_reasoning, self.preserve_content)
            .map_err(|error| match error {
                ChatError::Provider(error) => CallFailure::Provider(error),
                ChatError::Incidental(incidental) => CallFailure::Incidental(incidental),
            })
    }

    async fn generate_inner(&self, request: ProviderRequest) -> ProviderResult<ProviderResponse> {
        let (request_json, context) = self.prepare(&request, false);
        self.hooks.fire_on_request(&context).await;
        let started = Instant::now();
        // Failures that are not provider errors stop the retry loop and are handled after it.
        let escaped: Mutex<Option<Incidental>> = Mutex::new(None);
        let outcome = crate::retry::retry_with_backoff(&self.retry_config, || async {
            match self.call_once(&request_json, request.timeout).await {
                Ok(response) => Ok(response),
                Err(CallFailure::Provider(error)) => Err(error),
                Err(CallFailure::Incidental(incidental)) => {
                    let message = incidental.0.clone();
                    *escaped.lock().expect("escape lock") = Some(incidental);
                    Err(ProviderError::new(ErrorKind::Internal, message))
                }
            }
        })
        .await;
        match outcome {
            Ok(response) => {
                let finished = HookContext {
                    response: Some(response.clone()),
                    duration_ms: started.elapsed().as_secs_f64() * 1000.0,
                    http_status: Some(200),
                    ..context
                };
                self.hooks.fire_on_response(&finished).await;
                Ok(response)
            }
            Err(error) => {
                let taken = escaped.lock().expect("escape lock").take();
                if let Some(Incidental(message)) = taken {
                    // The outer `except Exception`: not retried, reported as a connection error.
                    let converted = ProviderError::connection(message, None);
                    self.hooks.fire_on_error(&Self::error_context(&context, &converted, started)).await;
                    return Err(converted);
                }
                if error.is(ErrorKind::RateLimit)
                    || error.is(ErrorKind::Timeout)
                    || error.is(ErrorKind::Connection)
                {
                    let count = self.consecutive_errors.fetch_add(1, Ordering::SeqCst) + 1;
                    if count >= MAX_CONSECUTIVE_CONNECTION_ERRORS {
                        self.recycle_pool().await;
                    }
                }
                self.hooks.fire_on_error(&Self::error_context(&context, &error, started)).await;
                Err(error)
            }
        }
    }
}

/// A failed attempt inside `generate`.
enum CallFailure {
    Provider(ProviderError),
    Incidental(Incidental),
}

impl CallFailure {
    /// The SDK's mapping of httpx failures: `APITimeoutError`, `APIConnectionError`.
    fn from_transport(error: TransportError) -> Self {
        Self::Provider(match error {
            TransportError::Timeout(_) => {
                ProviderError::new(ErrorKind::Timeout, "Provider request timed out")
            }
            TransportError::Connection(_) => ProviderError::connection("Provider connection failed", None),
        })
    }
}

/// The SDK's reading of a successful non-streaming body: JSON when the content type says so
/// (a parse failure raises), JSON if it happens to parse otherwise, else the text itself.
fn sdk_completion_data(response: &HttpResponse, body: &[u8]) -> Result<Json, Incidental> {
    let content_type = response.header("content-type").unwrap_or("*");
    let content_type = content_type.split(';').next().unwrap_or("");
    let text = String::from_utf8_lossy(body);
    let parsed = std::str::from_utf8(body)
        .map_err(|error| Incidental(error.to_string()))
        .and_then(|text| sylvia_base::pyjson::loads(text).map_err(|error| Incidental(error.message)));
    if content_type.ends_with("json") {
        return parsed;
    }
    Ok(parsed.unwrap_or_else(|_| Json::String(text.into_owned())))
}

/// How a stream ended badly while reading, for the catch-all handling.
enum StreamFailure {
    Chat(ChatError),
    /// An httpx error while reading the body (raised raw through the SDK iterator).
    Transport(TransportError),
}

enum Phase {
    NotStarted,
    Reading,
    Completing,
    Finished,
}

struct StreamRun<'a> {
    provider: &'a OpenAICompatProvider,
    request: ProviderRequest,
    phase: Phase,
    context: HookContext,
    started: Instant,
    response: Option<HttpResponse>,
    decoder: SseDecoder,
    tracker: DoneTracker,
    events: VecDeque<ServerSentEvent>,
    body_ended: bool,
    sdk_done: bool,
    state: ChatStreamState,
    out: VecDeque<ProviderStreamChunk>,
}

impl<'a> StreamRun<'a> {
    fn new(provider: &'a OpenAICompatProvider, request: ProviderRequest) -> Self {
        Self {
            provider,
            request,
            phase: Phase::NotStarted,
            context: HookContext::default(),
            started: Instant::now(),
            response: None,
            decoder: SseDecoder::new(),
            tracker: DoneTracker::default(),
            events: VecDeque::new(),
            body_ended: false,
            sdk_done: false,
            state: ChatStreamState::new(provider.preserve_reasoning, provider.preserve_content),
            out: VecDeque::new(),
        }
    }

    fn timeout(&self) -> Duration {
        Duration::from_secs_f64(self.request.timeout.max(0.0))
    }

    async fn next(&mut self) -> Option<ProviderResult<ProviderStreamChunk>> {
        loop {
            if let Some(chunk) = self.out.pop_front() {
                return Some(Ok(chunk));
            }
            match self.phase {
                Phase::NotStarted => match self.start().await {
                    Ok(()) => self.phase = Phase::Reading,
                    Err(error) => {
                        self.phase = Phase::Finished;
                        return Some(Err(error));
                    }
                },
                Phase::Reading => {
                    if let Err(error) = self.read_step().await {
                        self.phase = Phase::Finished;
                        self.response = None;
                        return Some(Err(error));
                    }
                }
                Phase::Completing => {
                    self.phase = Phase::Finished;
                    let (usage, cache) = match &self.state.usage {
                        Some((usage, cache)) => (usage.clone(), cache.clone()),
                        None => (TokenUsage::default(), Default::default()),
                    };
                    let response = ProviderResponse {
                        usage,
                        usage_reported: Some(self.state.usage.is_some()),
                        cache_usage: cache,
                        ..ProviderResponse::default()
                    };
                    let finished = HookContext {
                        response: Some(response),
                        duration_ms: self.started.elapsed().as_secs_f64() * 1000.0,
                        http_status: Some(200),
                        ..self.context.clone()
                    };
                    self.provider.hooks.fire_on_response(&finished).await;
                    return None;
                }
                Phase::Finished => return None,
            }
        }
    }

    /// Everything up to the response headers: hooks, the request, and the initial failures.
    async fn start(&mut self) -> ProviderResult<()> {
        let (request_json, context) = self.provider.prepare(&self.request, true);
        self.context = context;
        self.provider.hooks.fire_on_request(&self.context).await;
        self.started = Instant::now();
        // An unknown keyword is Python's TypeError, which escapes the provider unconverted.
        let http = self
            .provider
            .http_request(&request_json)
            .map_err(|Incidental(message)| ProviderError::new(ErrorKind::Internal, message))?;
        let opened = tokio::time::timeout(self.timeout(), async {
            let mut response = self.provider.transport.send(http).await.map_err(stream_open_error)?;
            if response.status >= 400 {
                let body = response.read_all().await.map_err(stream_open_error)?;
                let message = chat::status_error_message(response.status, &body);
                return Err(chat::status_error(
                    response.status,
                    &message,
                    response.header("retry-after"),
                    None,
                ));
            }
            Ok(response)
        })
        .await;
        match opened {
            Err(_) => {
                let error = ProviderError::new(
                    ErrorKind::Timeout,
                    self.provider.timed_out_message("Stream request to", self.request.timeout),
                );
                self.provider
                    .hooks
                    .fire_on_error(&OpenAICompatProvider::error_context(&self.context, &error, self.started))
                    .await;
                Err(error)
            }
            Ok(Err(error)) => Err(error),
            Ok(Ok(response)) => {
                self.response = Some(response);
                Ok(())
            }
        }
    }

    /// One `wait_for(stream_iterator.__anext__(), timeout)` and the work on its chunk.
    async fn read_step(&mut self) -> ProviderResult<()> {
        let wait = self.timeout();
        let next = tokio::time::timeout(wait, self.next_sdk_chunk()).await;
        let result = match next {
            Err(_) => {
                let error = ProviderError::new(
                    ErrorKind::Timeout,
                    format!(
                        "Stream from {} was inactive for {}s",
                        self.provider.model_info.provider_name,
                        sylvia_base::pyjson::float_repr(self.request.timeout)
                    ),
                );
                self.provider
                    .hooks
                    .fire_on_error(&OpenAICompatProvider::error_context(&self.context, &error, self.started))
                    .await;
                return Err(error);
            }
            Ok(Ok(Some(raw))) => self.state.on_chunk(&raw).map_err(StreamFailure::Chat),
            Ok(Ok(None)) => {
                self.response = None;
                match self.state.check_terminal(self.tracker.done) {
                    Err(error) => Err(StreamFailure::Chat(error)),
                    Ok(()) => {
                        // After the try: a failure here escapes without on_error.
                        match self.state.final_metadata() {
                            Ok(chunk) => {
                                self.out.extend(chunk);
                                self.phase = Phase::Completing;
                                return Ok(());
                            }
                            Err(ChatError::Provider(error)) => return Err(error),
                            Err(ChatError::Incidental(Incidental(message))) => {
                                return Err(ProviderError::new(ErrorKind::Internal, message))
                            }
                        }
                    }
                }
            }
            Ok(Err(failure)) => Err(failure),
        };
        match result {
            Ok(chunks) => {
                self.out.extend(chunks);
                Ok(())
            }
            Err(failure) => {
                let error = match failure {
                    StreamFailure::Chat(error) => error.into_provider_error(),
                    StreamFailure::Transport(
                        TransportError::Timeout(message) | TransportError::Connection(message),
                    ) => ProviderError::connection(message, None),
                };
                let mut context = OpenAICompatProvider::error_context(&self.context, &error, self.started);
                if let Some((usage, cache)) = &self.state.usage {
                    context.response = Some(ProviderResponse {
                        usage: usage.clone(),
                        usage_reported: Some(true),
                        cache_usage: cache.clone(),
                        ..ProviderResponse::default()
                    });
                }
                self.provider.hooks.fire_on_error(&context).await;
                Err(error)
            }
        }
    }

    /// The SDK iterator's next chunk: reads pieces until an event completes; `[DONE]` or the end of
    /// the body ends it.
    async fn next_sdk_chunk(&mut self) -> Result<Option<Json>, StreamFailure> {
        loop {
            if self.sdk_done {
                return Ok(None);
            }
            if let Some(event) = self.events.pop_front() {
                return match stream_item(&event)
                    .map_err(|error| StreamFailure::Chat(ChatError::Incidental(error)))?
                {
                    StreamItem::Done => {
                        self.sdk_done = true;
                        Ok(None)
                    }
                    StreamItem::Chunk(raw) => Ok(Some(raw)),
                };
            }
            if self.body_ended {
                self.sdk_done = true;
                return Ok(None);
            }
            let Some(response) = self.response.as_mut() else {
                self.sdk_done = true;
                return Ok(None);
            };
            match response.body.next_piece().await {
                None => {
                    self.body_ended = true;
                    let events = self
                        .decoder
                        .finish()
                        .map_err(|error| StreamFailure::Chat(ChatError::Incidental(error)))?;
                    self.events.extend(events);
                }
                Some(Err(error)) => return Err(StreamFailure::Transport(error)),
                Some(Ok(piece)) => {
                    self.tracker.observe(&piece);
                    let events = self
                        .decoder
                        .feed(&piece)
                        .map_err(|error| StreamFailure::Chat(ChatError::Incidental(error)))?;
                    self.events.extend(events);
                }
            }
        }
    }
}

/// The SDK's mapping for failures before a stream exists.
fn stream_open_error(error: TransportError) -> ProviderError {
    match error {
        TransportError::Timeout(_) => {
            ProviderError::new(ErrorKind::Timeout, "Provider stream request timed out")
        }
        TransportError::Connection(_) => ProviderError::connection("Provider stream connection failed", None),
    }
}

impl Provider for OpenAICompatProvider {
    fn model_info(&self) -> Option<&ModelInfo> {
        Some(&self.model_info)
    }

    fn generate(&self, request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        Box::pin(self.generate_inner(request))
    }

    fn generate_stream(&self, request: ProviderRequest) -> ChunkStream<'_> {
        let run = StreamRun::new(self, request);
        Box::pin(futures_util::stream::unfold(run, |mut run| async move {
            let item = run.next().await?;
            Some((item, run))
        }))
    }

    fn close(&self) -> BoxFuture<'_, ()> {
        Box::pin(async move {
            if self.closed.swap(true, Ordering::SeqCst) {
                return;
            }
            self.transport.close().await;
        })
    }
}
