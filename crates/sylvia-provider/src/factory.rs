//! `provider/_factory.py`: `ProviderFactory.create(config)`, the provider for one model.
//!
//! Chat Completions builds an [`OpenAICompatProvider`] over its own HTTP client, as each Python
//! provider builds its own `AsyncOpenAI` client (its own connection pool and cookie jar). The
//! other transports (Responses, Anthropic Messages, Gemini, and the Claude and ChatGPT runtimes)
//! pass Python's checks and then answer `rust_port_pending` until their adapters are ported.

use crate::http::HttpTransport;
use crate::openai_compat::{BuildError, OpenAICompatProvider, SdkEnvironment};
use crate::provider::Provider;
use crate::proxy::ProxyError;
use crate::registry::ProviderFactory;
use crate::routes::inference_base_url;
use crate::types::{transport, ModelInfo, ProviderConfig, RetryConfig, TimeoutConfig};
use std::sync::Arc;
use sylvia_base::CodedError;

/// The code a transport whose adapter is not ported yet answers with (the owner's
/// `rust_port_pending`).
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
    environment: SdkEnvironment,
}

impl DefaultProviderFactory {
    /// A factory whose providers use clients from `client` and the SDK environment given.
    pub fn new(client: Arc<ClientBuilder>, environment: SdkEnvironment) -> Self {
        Self { client, environment }
    }

    /// The product's factory: a network client per provider, configured from the process
    /// environment as the SDK configures httpx, and the SDK's `OPENAI_*` variables.
    pub fn network() -> Self {
        let client: Arc<ClientBuilder> = Arc::new(|timeouts: &TimeoutConfig| {
            let settings = crate::net::ClientSettings::from_system(timeouts.clone());
            Ok(Arc::new(crate::net::NetworkTransport::new(settings)?) as Arc<dyn HttpTransport>)
        });
        Self::new(client, SdkEnvironment::from_process())
    }
}

fn pending(transport: &str) -> CodedError {
    CodedError::new(PORT_PENDING, format!("The Rust owner cannot build {transport} providers yet."))
}

impl ProviderFactory for DefaultProviderFactory {
    fn create(&self, config: ProviderConfig) -> Result<Arc<dyn Provider>, CodedError> {
        Ok(Arc::new(self.build(config)?))
    }
}

impl DefaultProviderFactory {
    /// `ProviderFactory.create(config)` with the built provider's own type. Every transport
    /// whose adapter is ported builds an [`OpenAICompatProvider`] (Chat Completions and Responses, so far).
    pub fn build(&self, config: ProviderConfig) -> Result<OpenAICompatProvider, CodedError> {
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
            return Err(pending(wire));
        }
        if wire == transport::CODEX_APP_SERVER {
            if config.provider_key != "chatgpt_subscription" || config.runtime_home.is_empty() {
                return Err(value_error("ChatGPT requires an owner-managed subscription profile"));
            }
            return Err(pending(wire));
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
                Ok(provider)
            }
            transport::GEMINI_GENERATE_CONTENT | transport::ANTHROPIC_MESSAGES => Err(pending(wire)),
            other => Err(value_error(format!(
                "Unknown transport '{other}'. Supported: chat_completions, responses, anthropic_messages"
            ))),
        }
    }
}
