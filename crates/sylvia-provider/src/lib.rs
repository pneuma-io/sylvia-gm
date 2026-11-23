//! Lane 4 (providers): the Rust home of `src/sylvia_gm/provider` and `config`.
//!
//! Stream event types, the scripted transport used by tests, four API protocols, two supervised
//! account runtimes, model settings and credential storage. See `docs/rust-port/README.md`.
//!
//! - [`types`] holds `provider/_base.py` data types.
//! - [`provider`] holds the `Provider` contract and `FakeProvider`.
//! - [`error`] holds `provider/_exceptions.py`.
//! - [`reply`] holds the owner's player-facing sentence for each provider error.
//! - [`retry`] holds `provider/_retry.py` and `provider/_retry_after.py`.
//! - [`stream_health`] holds `provider/_stream_health.py`.
//! - [`tool_delta`] holds `provider/_tool_delta.py`.
//! - [`chat`] holds the Chat Completions transport (`provider/_openai_compat.py` without HTTP).
//! - [`http`] holds the HTTP seam and the scripted transport for tests.
//! - [`sse`] holds the OpenAI SDK's server-sent-event reading.
//! - [`pyobj`] holds Python's view of provider JSON.
//! - [`openai_compat`] holds `OpenAICompatProvider` over the HTTP seam.
//! - [`hooks`] holds `provider/_hooks.py`'s context and manager.
//! - [`httpx_url`] holds `httpx.URL`; `urllib.parse.urlsplit` is [`sylvia_base::pyurl`].
//! - [`pyfmt`] holds Python's `:g` number formatting.
//! - [`catalog`] holds `provider/_models.py` and `provider/_output_policy.py`.
//! - [`reasoning`] and [`speed`] hold `provider/_reasoning.py` and `provider/_speed.py`.
//! - [`admin_audit`] holds `provider/_admin_audit.py` (Admin Full Audit).
//! - [`context_mode`] holds the request-assembly mode from `agent/_context_ledger.py` that providers read.
//! - [`context_fit`] holds `provider/_context_fit.py`, and [`token_estimate`] `provider/_token_estimate.py`.
//! - [`request_metadata`] holds `provider/_request_metadata.py`.
//! - [`registry`] holds `provider/_registry.py`'s `ProviderRegistry`.

pub mod admin_audit;
pub mod anthropic;
pub mod catalog;
pub mod chat;
pub mod chatgpt;
pub mod chatgpt_account;
pub mod chatgpt_provider;
pub mod claude;
pub mod claude_install;
pub mod claude_kept;
pub mod claude_native;
pub mod claude_provider;
pub mod claude_runtime;
pub mod codex_rpc;
pub mod config;
pub mod context_capacity;
pub mod context_fit;
pub mod context_mode;
pub mod error;
pub mod factory;
pub mod gemini;
pub(crate) mod generator;
pub mod hooks;
pub mod http;
mod httpx_text;
pub mod httpx_url;
pub mod lifecycle;
mod log;
pub mod model_facts;
pub mod net;
pub mod openai_compat;
pub mod provider;
pub mod proxy;
pub mod pydatetime;
pub mod pyfmt;
pub mod pyobj;
pub mod reasoning;
pub mod registry;
pub mod request_metadata;
/// `urllib.parse`, now shared in `sylvia_base`.
pub use sylvia_base::pyurl;
pub mod reply;
pub mod responses;
pub mod retry;
pub mod routes;
pub mod runtime_process;
mod sdk_models;
pub mod speed;
pub mod sse;
pub mod stream_health;
pub mod token_estimate;
pub mod tool_delta;
pub mod types;

pub use error::{ErrorCategory, ErrorKind, ProviderError, ProviderResult};
pub use provider::{BoxFuture, ChunkStream, FakeProvider, Provider, ProviderRequest};
pub use reply::{provider_error_reply, ProviderErrorReply};
pub use types::{
    CacheUsage, ModelInfo, ProviderConfig, ProviderResponse, ProviderStreamChunk, RetryConfig, SpeedOption,
    TimeoutConfig, TokenUsage, ToolCall,
};
