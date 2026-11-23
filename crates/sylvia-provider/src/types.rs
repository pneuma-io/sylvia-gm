//! Request, response and stream types from `provider/_base.py`.
//!
//! Field names and defaults are Python's. Opaque provider payloads (native continuation
//! blocks, tool-call fragments) stay ordered JSON, so they reach storage and the next request
//! exactly as the provider sent them.

use std::collections::BTreeSet;
use sylvia_base::{Json, JsonMap};

/// `TokenUsage`: token counts for one request.
///
/// The three counts hold Python's value. A stream's usage is always an integer (Python coerces
/// it with `int(...)`), but a complete response keeps what the SDK held: `None` when missing,
/// or `3.9`, `"12"` from a malformed reply. Code that does arithmetic or comparisons on a count
/// must go through the helper Python's code uses there ([`PyValue::int_or_zero`] for
/// `int(x or 0)`, [`PyValue::as_int`] for an exact `int`), so it fails where Python fails.
#[derive(Debug, Clone, PartialEq)]
pub struct TokenUsage {
    pub prompt_tokens: PyValue,
    pub completion_tokens: PyValue,
    pub total_tokens: PyValue,
    pub reasoning_tokens: Option<i64>,
}

impl Default for TokenUsage {
    /// `TokenUsage()`: zero counts, no reasoning count.
    fn default() -> Self {
        Self::from_ints(0, 0, 0, None)
    }
}

impl TokenUsage {
    /// Integer counts, as the streaming path and most providers produce them.
    pub fn from_ints(prompt: i64, completion: i64, total: i64, reasoning: Option<i64>) -> Self {
        Self {
            prompt_tokens: PyValue::int(prompt),
            completion_tokens: PyValue::int(completion),
            total_tokens: PyValue::int(total),
            reasoning_tokens: reasoning,
        }
    }
}

/// A field Python keeps as whatever the provider sent. Normally text; a malformed reply (common
/// from small local models) can leave `None` or another JSON value, which Python then saves in
/// history and sends back to the model unchanged.
#[derive(Debug, Clone, PartialEq)]
pub enum PyValue {
    /// A Python `str`.
    Text(String),
    /// Anything else, `None` included (`Json::Null`). Never a JSON string.
    Other(Json),
}

impl Default for PyValue {
    fn default() -> Self {
        Self::Text(String::new())
    }
}

impl PyValue {
    /// The value a JSON value stands for.
    pub fn from_json(value: Json) -> Self {
        match value {
            Json::String(text) => Self::Text(text),
            other => Self::Other(other),
        }
    }

    /// Python's `None`.
    pub fn none() -> Self {
        Self::Other(Json::Null)
    }

    /// A Python `int`.
    pub fn int(value: i64) -> Self {
        Self::Other(Json::from(value))
    }

    /// The value when it is a Python `int` (not a `bool`, `float` or text), else `None`.
    pub fn as_int(&self) -> Option<i64> {
        match self {
            Self::Other(Json::Number(number)) if !number.is_f64() => number.as_i64(),
            _ => None,
        }
    }

    /// `int(value or 0)`, with Python's failures as `Incidental` (and the integer-overflow
    /// refusal for values beyond `i64`).
    pub fn int_or_zero(&self) -> Result<i64, crate::pyobj::Incidental> {
        crate::pyobj::int_or_zero(&self.to_json())
    }

    /// The text, when it is a `str`.
    pub fn as_text(&self) -> Option<&str> {
        match self {
            Self::Text(text) => Some(text),
            Self::Other(_) => None,
        }
    }

    /// Whether it is Python's `None`.
    pub fn is_none(&self) -> bool {
        matches!(self, Self::Other(Json::Null))
    }

    /// As JSON, the way Python's `json.dumps` would write the value.
    pub fn to_json(&self) -> Json {
        match self {
            Self::Text(text) => Json::String(text.clone()),
            Self::Other(value) => value.clone(),
        }
    }
}

impl From<&str> for PyValue {
    fn from(text: &str) -> Self {
        Self::Text(text.to_owned())
    }
}

impl From<String> for PyValue {
    fn from(text: String) -> Self {
        Self::Text(text)
    }
}

/// `ToolCall`: a completed tool call. `arguments` is the JSON text exactly as received. Each
/// field holds what Python held: text normally, but `None` or another value from a malformed
/// complete response.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ToolCall {
    pub id: PyValue,
    pub name: PyValue,
    pub arguments: PyValue,
}

impl ToolCall {
    /// A tool call with text fields.
    pub fn new(id: impl Into<String>, name: impl Into<String>, arguments: impl Into<String>) -> Self {
        Self {
            id: PyValue::Text(id.into()),
            name: PyValue::Text(name.into()),
            arguments: PyValue::Text(arguments.into()),
        }
    }
}

/// `CacheUsage`: prompt-cache counts. A missing field is `None`; a reported zero is measured.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct CacheUsage {
    pub cache_read_tokens: Option<i64>,
    pub cache_write_tokens: Option<i64>,
    pub cache_miss_tokens: Option<i64>,
    pub total_prompt_tokens: i64,
}

impl CacheUsage {
    /// Observed read fraction; `None` when cache reads were not reported.
    pub fn hit_ratio(&self) -> Option<f64> {
        let read = self.cache_read_tokens?;
        if self.total_prompt_tokens == 0 {
            return if read == 0 { Some(0.0) } else { None };
        }
        Some(read as f64 / self.total_prompt_tokens as f64)
    }

    /// Cached-token percentage (not a billing estimate).
    pub fn savings_pct(&self) -> Option<f64> {
        self.hit_ratio().map(|ratio| ratio * 100.0)
    }
}

/// `ProviderResponse`: a complete (non-streaming) response.
#[derive(Debug, Clone, PartialEq)]
pub struct ProviderResponse {
    pub content: Option<String>,
    pub reasoning_content: Option<String>,
    pub tool_calls: Vec<ToolCall>,
    pub usage: TokenUsage,
    pub usage_reported: Option<bool>,
    pub model: String,
    /// `"stop"` by default; whatever truthy value the provider sent otherwise.
    pub finish_reason: PyValue,
    pub cache_usage: CacheUsage,
    /// Opaque native continuation blocks; private, never canonical history.
    pub anthropic_content: Option<Vec<Json>>,
    /// Private signed/tool continuation fields, never canonical history.
    pub chat_completion_metadata: Option<JsonMap>,
    /// What the provider reported, never copied from the request.
    pub provider_reported_reasoning_effort: Option<String>,
    /// The speed tier the provider says it used.
    pub provider_reported_service_tier: Option<String>,
}

impl Default for ProviderResponse {
    fn default() -> Self {
        Self {
            content: None,
            reasoning_content: None,
            tool_calls: Vec::new(),
            usage: TokenUsage::default(),
            usage_reported: None,
            model: String::new(),
            finish_reason: PyValue::from("stop"),
            cache_usage: CacheUsage::default(),
            anthropic_content: None,
            chat_completion_metadata: None,
            provider_reported_reasoning_effort: None,
            provider_reported_service_tier: None,
        }
    }
}

/// `ProviderStreamChunk`: one piece of a streamed response.
///
/// `tool_call_delta` is one wire fragment as an ordered JSON object (Chat Completions
/// `{"index", "id", "type", "function": {"name", "arguments"}}`, or the shapes the other
/// transports emit); read it with [`crate::tool_delta::parse_tool_call_delta`]. Argument
/// fragments are the exact strings received and are only ever joined, never re-encoded.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct ProviderStreamChunk {
    pub content: String,
    pub reasoning_content: String,
    pub tool_call_delta: Option<Json>,
    /// `None`, or the provider's value as Python held it (normally text).
    pub finish_reason: Option<PyValue>,
    /// Complete native assistant content at message end, including signed thinking blocks.
    pub anthropic_content: Option<Vec<Json>>,
    /// Private signed/tool continuation fields, never canonical history.
    pub chat_completion_metadata: Option<JsonMap>,
    pub provider_reported_reasoning_effort: Option<String>,
    pub provider_reported_service_tier: Option<String>,
}

impl ProviderStreamChunk {
    /// A chunk carrying only visible text.
    pub fn content(text: impl Into<String>) -> Self {
        Self { content: text.into(), ..Self::default() }
    }

    /// A chunk carrying only a finish reason.
    pub fn finish(reason: impl Into<String>) -> Self {
        Self { finish_reason: Some(PyValue::Text(reason.into())), ..Self::default() }
    }

    /// A chunk carrying only a tool-call fragment.
    pub fn tool_call(delta: Json) -> Self {
        Self { tool_call_delta: Some(delta), ..Self::default() }
    }
}

/// `SpeedOption`: one provider-native speed tier with the provider's own wording.
#[derive(Debug, Clone, PartialEq, Eq, Hash)]
pub struct SpeedOption {
    pub id: String,
    pub name: String,
    pub description: String,
}

impl SpeedOption {
    /// `projection()`: `{"id", "name", "description"}`.
    pub fn projection(&self) -> Json {
        let mut map = JsonMap::new();
        map.insert("id".into(), Json::String(self.id.clone()));
        map.insert("name".into(), Json::String(self.name.clone()));
        map.insert("description".into(), Json::String(self.description.clone()));
        Json::Object(map)
    }
}

/// The wire protocols a model can need (`ModelInfo.transport`).
pub mod transport {
    pub const CHAT_COMPLETIONS: &str = "chat_completions";
    pub const RESPONSES: &str = "responses";
    pub const ANTHROPIC_MESSAGES: &str = "anthropic_messages";
    pub const CODEX_APP_SERVER: &str = "codex_app_server";
    pub const GEMINI_GENERATE_CONTENT: &str = "gemini_generate_content";
    pub const CLAUDE_CODE: &str = "claude_code";
}

/// The context figures an account runtime rewrites on its `ModelInfo` after construction
/// (`_claude.py`'s `_apply_runtime_window`, `_chatgpt.py`'s `_observe_context_window`).
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContextFigures {
    pub context_window: Option<i64>,
    pub context_window_source: String,
    pub runtime_context_window: Option<i64>,
    pub runtime_context_source: String,
    pub runtime_context_observed_at: Option<String>,
    pub context_observed_at: Option<String>,
    pub configured_runtime_mismatch: bool,
}

/// A runtime's live context figures, shared by every clone of its `ModelInfo`.
///
/// Python's wrappers, runner and prompt assembler hold the runtime's one `ModelInfo` object, so
/// a window the runtime observes is what the next request plans within. Here every clone of
/// that `ModelInfo` shares this cell. Only a runtime creates one
/// ([`ModelInfo::share_live_context`]) and writes it ([`LiveContext::update`]).
#[derive(Clone)]
pub struct LiveContext(std::sync::Arc<std::sync::Mutex<ContextFigures>>);

impl LiveContext {
    /// The current figures.
    pub fn get(&self) -> ContextFigures {
        self.0.lock().expect("live context lock").clone()
    }

    /// Rewrite the figures as one change: readers only ever see whole figures.
    pub fn update(&self, change: impl FnOnce(&mut ContextFigures)) {
        change(&mut self.0.lock().expect("live context lock"));
    }
}

impl std::fmt::Debug for LiveContext {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        self.get().fmt(formatter)
    }
}

/// `ModelInfo`: what the registry knows about one model.
///
/// Build with [`ModelInfo::new`] and set fields, then call [`ModelInfo::post_init`] where Python
/// constructs the dataclass (it runs `__post_init__`). `transport` stays a string as in Python,
/// which does not check it; the known values are in [`transport`].
///
/// `Debug` and `PartialEq` go by the current figures, as the dataclass's `__repr__` and `__eq__`
/// read its fields.
#[derive(Clone)]
pub struct ModelInfo {
    pub id: String,
    pub name: String,
    pub provider_name: String,
    pub provider_key: String,
    pub provider_mode: String,
    pub recommended: bool,
    pub premium: bool,
    /// Effective context capacity used for request admission (derived, may be `None`).
    /// Set at construction; after construction, read it through the accessor of the same name, which sees a runtime's
    /// observed figure.
    pub context_window: Option<i64>,
    pub declared_context_window: Option<i64>,
    pub declared_context_source: String,
    /// Set at construction; after construction, read it through the accessor of the same name, which sees a runtime's
    /// observed figure.
    pub runtime_context_window: Option<i64>,
    /// Set at construction; after construction, read it through the accessor of the same name, which sees a runtime's
    /// observed figure.
    pub runtime_context_source: String,
    pub configured_context_window: Option<i64>,
    /// Set at construction; after construction, read it through the accessor of the same name, which sees a runtime's
    /// observed figure.
    pub context_window_source: String,
    pub declared_context_observed_at: Option<String>,
    /// Set at construction; after construction, read it through the accessor of the same name, which sees a runtime's
    /// observed figure.
    pub runtime_context_observed_at: Option<String>,
    pub configured_context_observed_at: Option<String>,
    /// Set at construction; after construction, read it through the accessor of the same name, which sees a runtime's
    /// observed figure.
    pub context_observed_at: Option<String>,
    /// Set at construction; after construction, read it through the accessor of the same name, which sees a runtime's
    /// observed figure.
    pub configured_runtime_mismatch: bool,
    pub configured_declared_mismatch: bool,
    /// Sylvia's completion allowance; `None` for runtime-managed output.
    pub max_output_tokens: Option<i64>,
    pub builder_output_tokens: i64,
    pub cost_per_1k_tokens: f64,
    pub transport: String,
    /// Exact model identifier sent to the provider.
    pub provider_model_id: String,
    pub supports_streaming: Option<bool>,
    pub supports_tools: Option<bool>,
    /// Native values advertised; `None` means unknown.
    pub supported_reasoning_efforts: Option<Vec<String>>,
    pub default_reasoning_effort: Option<String>,
    pub reasoning_control: Option<String>,
    pub reasoning_source: String,
    /// Native speed tiers; `None` means no known choice.
    pub speed_options: Option<Vec<SpeedOption>>,
    pub speed_control: Option<String>,
    pub speed_source: String,
    /// A runtime's live figures; `None` for configured and catalog models.
    pub live: Option<LiveContext>,
}

impl ModelInfo {
    /// The five required fields, every other field at its Python default.
    pub fn new(
        id: impl Into<String>,
        name: impl Into<String>,
        provider_name: impl Into<String>,
        provider_key: impl Into<String>,
        provider_mode: impl Into<String>,
    ) -> Self {
        let unknown = || String::from("unknown");
        Self {
            id: id.into(),
            name: name.into(),
            provider_name: provider_name.into(),
            provider_key: provider_key.into(),
            provider_mode: provider_mode.into(),
            recommended: false,
            premium: false,
            context_window: None,
            declared_context_window: None,
            declared_context_source: unknown(),
            runtime_context_window: None,
            runtime_context_source: unknown(),
            configured_context_window: None,
            context_window_source: unknown(),
            declared_context_observed_at: None,
            runtime_context_observed_at: None,
            configured_context_observed_at: None,
            context_observed_at: None,
            configured_runtime_mismatch: false,
            configured_declared_mismatch: false,
            max_output_tokens: Some(4096),
            builder_output_tokens: 32_000,
            cost_per_1k_tokens: 0.0,
            transport: transport::CHAT_COMPLETIONS.into(),
            provider_model_id: String::new(),
            supports_streaming: None,
            supports_tools: None,
            supported_reasoning_efforts: None,
            default_reasoning_effort: None,
            reasoning_control: None,
            reasoning_source: unknown(),
            speed_options: None,
            speed_control: None,
            speed_source: unknown(),
            live: None,
        }
    }

    /// `__post_init__`: a bare legacy `context_window` is marked `legacy_unverified`.
    #[must_use]
    pub fn post_init(mut self) -> Self {
        if self.context_window.is_some()
            && self.declared_context_window.is_none()
            && self.runtime_context_window.is_none()
            && self.configured_context_window.is_none()
            && self.context_window_source == "unknown"
        {
            self.context_window_source = "legacy_unverified".into();
        }
        self
    }

    /// Share this model's context figures with every clone of it, from their values now: a
    /// runtime calls it once at construction and writes through the returned cell.
    pub fn share_live_context(&mut self) -> LiveContext {
        if let Some(live) = &self.live {
            return live.clone();
        }
        let live = LiveContext(std::sync::Arc::new(std::sync::Mutex::new(self.stored_figures())));
        self.live = Some(live.clone());
        live
    }

    fn stored_figures(&self) -> ContextFigures {
        ContextFigures {
            context_window: self.context_window,
            context_window_source: self.context_window_source.clone(),
            runtime_context_window: self.runtime_context_window,
            runtime_context_source: self.runtime_context_source.clone(),
            runtime_context_observed_at: self.runtime_context_observed_at.clone(),
            context_observed_at: self.context_observed_at.clone(),
            configured_runtime_mismatch: self.configured_runtime_mismatch,
        }
    }

    /// One consistent snapshot of the context figures: the runtime's live ones, else the fields.
    pub fn context_figures(&self) -> ContextFigures {
        match &self.live {
            Some(live) => live.get(),
            None => self.stored_figures(),
        }
    }

    /// `context_window`, as it is now.
    pub fn context_window(&self) -> Option<i64> {
        self.context_figures().context_window
    }

    /// `context_window_source`, as it is now.
    pub fn context_window_source(&self) -> String {
        self.context_figures().context_window_source
    }

    /// `runtime_context_window`, as it is now.
    pub fn runtime_context_window(&self) -> Option<i64> {
        self.context_figures().runtime_context_window
    }

    /// `runtime_context_source`, as it is now.
    pub fn runtime_context_source(&self) -> String {
        self.context_figures().runtime_context_source
    }

    /// `runtime_context_observed_at`, as it is now.
    pub fn runtime_context_observed_at(&self) -> Option<String> {
        self.context_figures().runtime_context_observed_at
    }

    /// `context_observed_at`, as it is now.
    pub fn context_observed_at(&self) -> Option<String> {
        self.context_figures().context_observed_at
    }

    /// `configured_runtime_mismatch`, as it is now.
    pub fn configured_runtime_mismatch(&self) -> bool {
        self.context_figures().configured_runtime_mismatch
    }

    /// `wire_model_id`: the upstream identifier, without changing the canonical id.
    pub fn wire_model_id(&self) -> &str {
        if !self.provider_model_id.is_empty() {
            return &self.provider_model_id;
        }
        match self.id.split_once('/') {
            Some((_, rest)) => rest,
            None => &self.id,
        }
    }
}

impl PartialEq for ModelInfo {
    fn eq(&self, other: &Self) -> bool {
        self.context_figures() == other.context_figures()
            && self.id == other.id
            && self.name == other.name
            && self.provider_name == other.provider_name
            && self.provider_key == other.provider_key
            && self.provider_mode == other.provider_mode
            && self.recommended == other.recommended
            && self.premium == other.premium
            && self.declared_context_window == other.declared_context_window
            && self.declared_context_source == other.declared_context_source
            && self.configured_context_window == other.configured_context_window
            && self.declared_context_observed_at == other.declared_context_observed_at
            && self.configured_context_observed_at == other.configured_context_observed_at
            && self.configured_declared_mismatch == other.configured_declared_mismatch
            && self.max_output_tokens == other.max_output_tokens
            && self.builder_output_tokens == other.builder_output_tokens
            && self.cost_per_1k_tokens == other.cost_per_1k_tokens
            && self.transport == other.transport
            && self.provider_model_id == other.provider_model_id
            && self.supports_streaming == other.supports_streaming
            && self.supports_tools == other.supports_tools
            && self.supported_reasoning_efforts == other.supported_reasoning_efforts
            && self.default_reasoning_effort == other.default_reasoning_effort
            && self.reasoning_control == other.reasoning_control
            && self.reasoning_source == other.reasoning_source
            && self.speed_options == other.speed_options
            && self.speed_control == other.speed_control
            && self.speed_source == other.speed_source
    }
}

impl std::fmt::Debug for ModelInfo {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let figures = self.context_figures();
        formatter
            .debug_struct("ModelInfo")
            .field("id", &self.id)
            .field("name", &self.name)
            .field("provider_name", &self.provider_name)
            .field("provider_key", &self.provider_key)
            .field("provider_mode", &self.provider_mode)
            .field("recommended", &self.recommended)
            .field("premium", &self.premium)
            .field("context_window", &figures.context_window)
            .field("declared_context_window", &self.declared_context_window)
            .field("declared_context_source", &self.declared_context_source)
            .field("runtime_context_window", &figures.runtime_context_window)
            .field("runtime_context_source", &figures.runtime_context_source)
            .field("configured_context_window", &self.configured_context_window)
            .field("context_window_source", &figures.context_window_source)
            .field("declared_context_observed_at", &self.declared_context_observed_at)
            .field("runtime_context_observed_at", &figures.runtime_context_observed_at)
            .field("configured_context_observed_at", &self.configured_context_observed_at)
            .field("context_observed_at", &figures.context_observed_at)
            .field("configured_runtime_mismatch", &figures.configured_runtime_mismatch)
            .field("configured_declared_mismatch", &self.configured_declared_mismatch)
            .field("max_output_tokens", &self.max_output_tokens)
            .field("builder_output_tokens", &self.builder_output_tokens)
            .field("cost_per_1k_tokens", &self.cost_per_1k_tokens)
            .field("transport", &self.transport)
            .field("provider_model_id", &self.provider_model_id)
            .field("supports_streaming", &self.supports_streaming)
            .field("supports_tools", &self.supports_tools)
            .field("supported_reasoning_efforts", &self.supported_reasoning_efforts)
            .field("default_reasoning_effort", &self.default_reasoning_effort)
            .field("reasoning_control", &self.reasoning_control)
            .field("reasoning_source", &self.reasoning_source)
            .field("speed_options", &self.speed_options)
            .field("speed_control", &self.speed_control)
            .field("speed_source", &self.speed_source)
            .finish()
    }
}

/// `RetryConfig`. Authentication errors are never retried regardless.
#[derive(Debug, Clone, PartialEq)]
pub struct RetryConfig {
    pub max_retries: u32,
    pub base_delay: f64,
    pub max_delay: f64,
    pub backoff_multiplier: f64,
    pub retryable_statuses: BTreeSet<u16>,
}

impl Default for RetryConfig {
    fn default() -> Self {
        Self::with(3, 1.0, 30.0, 2.0, &[429, 500, 502, 503, 504])
    }
}

impl RetryConfig {
    fn with(max_retries: u32, base_delay: f64, max_delay: f64, multiplier: f64, statuses: &[u16]) -> Self {
        Self {
            max_retries,
            base_delay,
            max_delay,
            backoff_multiplier: multiplier,
            retryable_statuses: statuses.iter().copied().collect(),
        }
    }

    /// `PROVIDER_RETRY_DEFAULTS[provider_key]`.
    pub fn provider_default(provider_key: &str) -> Option<Self> {
        Some(match provider_key {
            "opencode_go" => Self::with(2, 2.0, 15.0, 2.0, &[429, 500, 502, 503]),
            "openrouter" => Self::with(3, 1.0, 30.0, 2.0, &[429, 500, 502, 503, 504]),
            "anthropic" => Self::with(4, 5.0, 60.0, 2.0, &[429, 529, 500, 502, 503]),
            "ollama" => Self::with(1, 0.5, 5.0, 1.5, &[500, 502, 503]),
            "sylvia_cloud" => Self::with(2, 1.0, 15.0, 2.0, &[429, 500, 502, 503, 504]),
            _ => return None,
        })
    }

    /// The keys of `PROVIDER_RETRY_DEFAULTS`, in Python's order.
    pub const PROVIDER_DEFAULT_KEYS: [&'static str; 5] =
        ["opencode_go", "openrouter", "anthropic", "ollama", "sylvia_cloud"];
}

/// `TimeoutConfig`: per-operation timeouts in seconds, with httpx's meanings.
#[derive(Debug, Clone, PartialEq)]
pub struct TimeoutConfig {
    /// TCP handshake and TLS.
    pub connect: f64,
    /// Longest silence between received bytes.
    pub read: f64,
    /// Longest wait to send each part of the request body.
    pub write: f64,
    /// Longest wait for a pooled connection.
    pub pool: f64,
    /// Longest wait between stream chunks before the stream is declared dead.
    pub stream_health_interval: f64,
}

impl Default for TimeoutConfig {
    fn default() -> Self {
        Self { connect: 15.0, read: 120.0, write: 30.0, pool: 5.0, stream_health_interval: 60.0 }
    }
}

/// `ProviderConfig`: what the factory needs to build a provider.
#[derive(Clone, PartialEq)]
pub struct ProviderConfig {
    pub provider_key: String,
    pub transport: String,
    pub base_url: String,
    /// Owner-selected private profile for a managed subscription runtime.
    pub runtime_home: String,
    /// Header names keep their configured order.
    pub default_headers: Vec<(String, String)>,
    /// Never printed: `Debug` leaves it out, as Python's `repr=False` does.
    pub api_key: String,
    pub models: Vec<ModelInfo>,
    pub default_model: String,
    pub timeout: f64,
    pub max_tokens: i64,
    /// `None` uses the provider default.
    pub retry_config: Option<RetryConfig>,
}

impl ProviderConfig {
    /// Required fields, the rest at Python's defaults.
    pub fn new(
        provider_key: impl Into<String>,
        transport: impl Into<String>,
        base_url: impl Into<String>,
    ) -> Self {
        Self {
            provider_key: provider_key.into(),
            transport: transport.into(),
            base_url: base_url.into(),
            runtime_home: String::new(),
            default_headers: Vec::new(),
            api_key: String::new(),
            models: Vec::new(),
            default_model: String::new(),
            timeout: 60.0,
            max_tokens: 4096,
            retry_config: None,
        }
    }
}

impl std::fmt::Debug for ProviderConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("ProviderConfig")
            .field("provider_key", &self.provider_key)
            .field("transport", &self.transport)
            .field("base_url", &self.base_url)
            .field("runtime_home", &self.runtime_home)
            .field("default_headers", &self.default_headers.iter().map(|(name, _)| name).collect::<Vec<_>>())
            .field("models", &self.models)
            .field("default_model", &self.default_model)
            .field("timeout", &self.timeout)
            .field("max_tokens", &self.max_tokens)
            .field("retry_config", &self.retry_config)
            .finish_non_exhaustive()
    }
}

#[cfg(test)]
mod live_context_tests {
    use super::*;

    fn model() -> ModelInfo {
        let mut model = ModelInfo::new(
            "claude_runtime/claude-opus-5-5",
            "Opus",
            "Claude",
            "claude_runtime",
            "subscription",
        );
        model.context_window = Some(200_000);
        model.context_window_source = "model_catalog".into();
        model
    }

    #[test]
    fn a_clone_sees_what_the_runtime_writes() {
        let mut runtime = model();
        let live = runtime.share_live_context();
        let held = runtime.clone();
        assert_eq!(held.context_window(), Some(200_000));
        live.update(|figures| {
            figures.context_window = Some(1_000_000);
            figures.context_window_source = "runtime_reported".into();
            figures.runtime_context_window = Some(1_000_000);
            figures.runtime_context_source = "runtime_reported".into();
            figures.runtime_context_observed_at = Some("2026-11-23T09:30:00+00:00".into());
            figures.context_observed_at = Some("2026-11-23T09:30:00+00:00".into());
            figures.configured_runtime_mismatch = true;
        });
        for holder in [&runtime, &held] {
            assert_eq!(holder.context_window(), Some(1_000_000));
            assert_eq!(holder.runtime_context_window(), Some(1_000_000));
            assert_eq!(holder.context_window_source(), "runtime_reported");
            assert!(holder.configured_runtime_mismatch());
            assert_eq!(holder.context_figures(), live.get());
        }
        // A second share is the same cell.
        assert_eq!(runtime.share_live_context().get(), live.get());
    }

    #[test]
    fn equality_and_debug_read_the_current_figures() {
        let mut runtime = model();
        let live = runtime.share_live_context();
        let mut plain = model();
        assert_eq!(runtime, plain);
        live.update(|figures| figures.context_window = Some(5));
        assert_ne!(runtime, plain);
        plain.context_window = Some(5);
        assert_eq!(runtime, plain);
        let shown = format!("{runtime:?}");
        assert!(shown.contains("context_window: Some(5)"), "{shown}");
        assert!(!shown.contains("live"), "{shown}");
        assert_eq!(shown, format!("{plain:?}"));
    }

    #[test]
    fn an_info_without_a_live_cell_is_a_plain_value() {
        let mut plain = model();
        let copy = plain.clone();
        plain.context_window = Some(7);
        assert_eq!(plain.context_window(), Some(7));
        assert_eq!(copy.context_window(), Some(200_000));
        assert!(plain.live.is_none());
    }
}
