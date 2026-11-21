//! Request, response and stream types from `provider/_base.py`.
//!
//! Field names and defaults are Python's. Opaque provider payloads (native continuation
//! blocks, tool-call fragments) stay ordered JSON, so they reach storage and the next request
//! exactly as the provider sent them.

use std::collections::BTreeSet;
use sylvia_base::{Json, JsonMap};

/// `TokenUsage`: token counts for one request.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct TokenUsage {
    pub prompt_tokens: i64,
    pub completion_tokens: i64,
    pub total_tokens: i64,
    pub reasoning_tokens: Option<i64>,
}

/// `ToolCall`: a completed tool call; `arguments` is the JSON text exactly as received.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct ToolCall {
    pub id: String,
    pub name: String,
    pub arguments: String,
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
    pub finish_reason: String,
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
            finish_reason: "stop".into(),
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
    pub finish_reason: Option<String>,
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
        Self { finish_reason: Some(reason.into()), ..Self::default() }
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

/// `ModelInfo`: what the registry knows about one model.
///
/// Build with [`ModelInfo::new`] and set fields, then call [`ModelInfo::post_init`] where Python
/// constructs the dataclass (it runs `__post_init__`). `transport` stays a string as in Python,
/// which does not check it; the known values are in [`transport`].
#[derive(Debug, Clone, PartialEq)]
pub struct ModelInfo {
    pub id: String,
    pub name: String,
    pub provider_name: String,
    pub provider_key: String,
    pub provider_mode: String,
    pub recommended: bool,
    pub premium: bool,
    /// Effective context capacity used for request admission (derived, may be `None`).
    pub context_window: Option<i64>,
    pub declared_context_window: Option<i64>,
    pub declared_context_source: String,
    pub runtime_context_window: Option<i64>,
    pub runtime_context_source: String,
    pub configured_context_window: Option<i64>,
    pub context_window_source: String,
    pub declared_context_observed_at: Option<String>,
    pub runtime_context_observed_at: Option<String>,
    pub configured_context_observed_at: Option<String>,
    pub context_observed_at: Option<String>,
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
