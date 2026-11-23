//! `provider/_factory.py`: `ProviderFactory.create(config)`, the provider for one model.
//!
//! Chat Completions and Responses build an [`OpenAICompatProvider`], Anthropic Messages an
//! [`AnthropicProvider`] and Gemini a [`GeminiProvider`] (lane 5), each over its own HTTP client,
//! as each Python provider builds its own client (its own connection pool and cookie jar). Claude
//! Code builds a [`ClaudeProvider`] over Sylvia's copy in the runtime home, and the ChatGPT
//! subscription a [`ChatGPTProvider`] over Sylvia's Codex runtime home.

use crate::anthropic::{AnthropicEnvironment, AnthropicProvider};
use crate::chatgpt_provider::ChatGPTProvider;
use crate::claude_provider::ClaudeProvider;
use crate::gemini::GeminiProvider;
use crate::http::HttpTransport;
use crate::openai_compat::{BuildError, OpenAICompatProvider, SdkEnvironment};
use crate::provider::Provider;
use crate::proxy::ProxyError;
use crate::registry::ProviderFactory;
use crate::routes::inference_base_url;
use crate::types::{transport, ModelInfo, ProviderConfig, RetryConfig, TimeoutConfig};
use std::sync::Arc;
use sylvia_base::CodedError;

/// The owner's `rust_port_pending` code, for behaviour the port has not reached yet. Every
/// transport now builds its provider.
pub const PORT_PENDING: &str = "rust_port_pending";

fn value_error(message: impl Into<String>) -> CodedError {
    CodedError::rejected(message).with_error_type("ValueError")
}

fn internal(class: &'static str, message: impl Into<String>) -> CodedError {
    CodedError::new("internal_error", message).with_error_type(class)
}

/// Builds the HTTP client for one provider, from that provider's timeouts.
pub type ClientBuilder = dyn Fn(&TimeoutConfig) -> Result<Arc<dyn HttpTransport>, ProxyError> + Send + Sync;

/// The product's factory.
pub struct DefaultProviderFactory {
    client: Arc<ClientBuilder>,
    /// Gemini's client: Python's plain `httpx.AsyncClient(follow_redirects=False)`.
    plain_client: Arc<ClientBuilder>,
    environment: SdkEnvironment,
    anthropic: AnthropicEnvironment,
}

impl DefaultProviderFactory {
    /// A factory whose providers use clients from `client` and the SDK environment given.
    pub fn new(client: Arc<ClientBuilder>, environment: SdkEnvironment) -> Self {
        Self {
            plain_client: Arc::clone(&client),
            client,
            environment,
            anthropic: AnthropicEnvironment::default(),
        }
    }

    /// The same factory with the client its Gemini providers use (no redirects followed).
    pub fn with_plain_client(mut self, plain_client: Arc<ClientBuilder>) -> Self {
        self.plain_client = plain_client;
        self
    }

    /// The same factory with the environment its Anthropic providers read.
    pub fn with_anthropic_environment(mut self, anthropic: AnthropicEnvironment) -> Self {
        self.anthropic = anthropic;
        self
    }

    /// The product's factory: a network client per provider, configured from the process
    /// environment as the SDK configures httpx, and the SDK's `OPENAI_*` variables.
    pub fn network() -> Self {
        let client: Arc<ClientBuilder> = Arc::new(|timeouts: &TimeoutConfig| {
            let settings = crate::net::ClientSettings::from_system(timeouts.clone());
            Ok(Arc::new(crate::net::NetworkTransport::new(settings)?) as Arc<dyn HttpTransport>)
        });
        let plain_client: Arc<ClientBuilder> = Arc::new(|timeouts: &TimeoutConfig| {
            let mut settings = crate::net::ClientSettings::from_system(timeouts.clone());
            settings.follow_redirects = false;
            Ok(Arc::new(crate::net::NetworkTransport::new(settings)?) as Arc<dyn HttpTransport>)
        });
        Self::new(client, SdkEnvironment::from_process())
            .with_plain_client(plain_client)
            .with_anthropic_environment(AnthropicEnvironment::from_process())
    }
}

impl ProviderFactory for DefaultProviderFactory {
    fn create(&self, config: ProviderConfig) -> Result<Arc<dyn Provider>, CodedError> {
        Ok(match self.build(config)? {
            BuiltProvider::OpenAICompat(provider) => Arc::new(provider),
            BuiltProvider::Anthropic(provider) => Arc::new(provider),
            BuiltProvider::Gemini(provider) => Arc::new(provider),
            BuiltProvider::Claude(provider) => Arc::new(*provider),
            BuiltProvider::ChatGPT(provider) => Arc::new(*provider),
        })
    }
}

/// A provider [`DefaultProviderFactory::build`] made, with its own type.
pub enum BuiltProvider {
    /// Chat Completions or Responses.
    OpenAICompat(OpenAICompatProvider),
    /// Anthropic Messages.
    Anthropic(AnthropicProvider),
    /// Gemini GenerateContent.
    Gemini(GeminiProvider),
    /// Sylvia's copy of Claude Code.
    Claude(Box<ClaudeProvider>),
    /// The ChatGPT subscription through Codex.
    ChatGPT(Box<ChatGPTProvider>),
}

/// The client timeouts for a Gemini provider. Python's `httpx.AsyncClient` applies each call's
/// `timeout` to every phase; the provider enforces that itself, so the client's own are only a
/// backstop (differences row 43, Gemini).
pub const GEMINI_CLIENT_TIMEOUTS: TimeoutConfig =
    TimeoutConfig { connect: 600.0, read: 600.0, write: 600.0, pool: 600.0, stream_health_interval: 600.0 };

impl DefaultProviderFactory {
    /// `ProviderFactory.create(config)` with the built provider's own type.
    pub fn build(&self, config: ProviderConfig) -> Result<BuiltProvider, CodedError> {
        let model_info = config.models.first().cloned().unwrap_or_else(|| {
            let id = if config.default_model.is_empty() { "unknown" } else { &config.default_model };
            let mut model = ModelInfo::new(id, "Unknown", &config.provider_key, &config.provider_key, "byok");
            model.transport = config.transport.clone();
            model.post_init()
        });
        let wire = config.transport.as_str();
        let base_url = inference_base_url(&config.base_url, wire).map_err(|error| value_error(error.0))?;
        if wire == transport::CLAUDE_CODE {
            if config.provider_key != "claude_runtime"
                || config.runtime_home.is_empty()
                || !config.default_headers.is_empty()
                || !config.api_key.is_empty()
            {
                return Err(value_error(
                    "Claude requires its native runtime connection without API credentials",
                ));
            }
            return Ok(BuiltProvider::Claude(Box::new(ClaudeProvider::new(
                std::path::Path::new(&config.runtime_home),
                model_info,
            ))));
        }
        if wire == transport::CODEX_APP_SERVER {
            if config.provider_key != "chatgpt_subscription" || config.runtime_home.is_empty() {
                return Err(value_error("ChatGPT requires an owner-managed subscription profile"));
            }
            return Ok(BuiltProvider::ChatGPT(Box::new(ChatGPTProvider::new(
                std::path::Path::new(&config.runtime_home),
                model_info,
                None,
                &crate::config::env::Environment::current(),
            ))));
        }
        // Explicit configuration, else the provider's default, else the generic default.
        let retry_config =
            config.retry_config.clone().or_else(|| RetryConfig::provider_default(&config.provider_key));
        match wire {
            // `transport in {"chat_completions", "responses"}`: one provider, the transport chosen
            // inside it (Responses ported by lane 5).
            transport::CHAT_COMPLETIONS | transport::RESPONSES => {
                let timeouts = TimeoutConfig::default();
                let client = (self.client)(&timeouts).map_err(|error| match error {
                    ProxyError::Value(message) => value_error(message),
                    ProxyError::Internal(message) => internal("InvalidURL", message),
                })?;
                let provider = OpenAICompatProvider::new(
                    &base_url,
                    Some(&config.api_key),
                    Some(model_info),
                    retry_config,
                    Some(timeouts),
                    config.default_headers.clone(),
                    self.environment.clone(),
                    client,
                )
                .map_err(|error| match error {
                    BuildError::Value(message) => value_error(message),
                    BuildError::InvalidUrl(message) => internal("InvalidURL", message),
                })?;
                Ok(BuiltProvider::OpenAICompat(provider))
            }
            // Python also passes `timeout=config.timeout`, which the provider stores and never
            // reads.
            transport::ANTHROPIC_MESSAGES => {
                let timeouts = TimeoutConfig::default();
                let client = (self.client)(&timeouts).map_err(|error| match error {
                    ProxyError::Value(message) => value_error(message),
                    ProxyError::Internal(message) => internal("InvalidURL", message),
                })?;
                let provider = AnthropicProvider::new(
                    &base_url,
                    &config.api_key,
                    model_info,
                    retry_config,
                    Some(timeouts),
                    config.max_tokens,
                    config.default_headers.clone(),
                    self.anthropic.clone(),
                    client,
                )
                .map_err(|error| match error {
                    BuildError::Value(message) => value_error(message),
                    BuildError::InvalidUrl(message) => internal("InvalidURL", message),
                })?;
                Ok(BuiltProvider::Anthropic(provider))
            }
            transport::GEMINI_GENERATE_CONTENT => {
                let client = (self.plain_client)(&GEMINI_CLIENT_TIMEOUTS).map_err(|error| match error {
                    ProxyError::Value(message) => value_error(message),
                    ProxyError::Internal(message) => internal("InvalidURL", message),
                })?;
                Ok(BuiltProvider::Gemini(GeminiProvider::new(
                    &base_url,
                    &config.api_key,
                    model_info,
                    config.default_headers.clone(),
                    client,
                )))
            }
            other => Err(value_error(format!(
                "Unknown transport '{other}'. Supported: chat_completions, responses, anthropic_messages"
            ))),
        }
    }
}
