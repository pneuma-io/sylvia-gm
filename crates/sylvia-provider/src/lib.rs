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
//! - [`pyfmt`] holds Python's `:g` number formatting.

pub mod chat;
pub mod error;
pub mod http;
mod log;
pub mod provider;
pub mod pyfmt;
pub mod pyobj;
pub mod reply;
pub mod retry;
pub mod sse;
pub mod stream_health;
pub mod tool_delta;
pub mod types;

pub use error::{ErrorCategory, ErrorKind, ProviderError, ProviderResult};
pub use provider::{BoxFuture, ChunkStream, FakeProvider, Provider, ProviderRequest};
pub use reply::{provider_error_reply, ProviderErrorReply};
pub use types::{
    CacheUsage, ModelInfo, ProviderConfig, ProviderResponse, ProviderStreamChunk, RetryConfig, SpeedOption,
    TimeoutConfig, TokenUsage, ToolCall,
};
