//! The `Provider` contract from `provider/_base.py`.
//!
//! Python's provider is an object with async `generate`, an async-generator `generate_stream`,
//! `close` and `release_idle`. Here `generate_stream` returns a pull-based [`ChunkStream`]: like
//! a Python async generator, it does no work until it is polled, and dropping it stops it (the
//! counterpart of `aclose()`). The GM loop consumes it chunk by chunk.

use crate::error::ProviderResult;
use crate::hooks::HookManager;
use crate::types::{ModelInfo, ProviderResponse, ProviderStreamChunk};
use futures_util::{Stream, StreamExt};
use std::future::Future;
use std::pin::Pin;
use std::sync::Mutex;
use sylvia_base::{Json, JsonMap};

/// A boxed, sendable future.
pub type BoxFuture<'a, T> = Pin<Box<dyn Future<Output = T> + Send + 'a>>;

/// A provider's chunk stream. An `Err` item is terminal: the stream yields nothing after it.
pub type ChunkStream<'a> = Pin<Box<dyn Stream<Item = ProviderResult<ProviderStreamChunk>> + Send + 'a>>;

/// The arguments of one `generate` / `generate_stream` call.
#[derive(Debug, Clone, PartialEq)]
pub struct ProviderRequest {
    /// Chat-Completions-shaped messages (`role`, `content`, ...), in order.
    pub messages: Vec<Json>,
    /// Tool definitions, or `None` for no tools (`tools or None` in the callers).
    pub tools: Option<Vec<Json>>,
    /// `generate(stream=...)`; ignored by `generate_stream`.
    pub stream: bool,
    /// Seconds: the whole call for `generate`, the inactivity bound for `generate_stream`.
    pub timeout: f64,
    /// The remaining keyword arguments (`max_tokens`, `tool_choice`, `extra_body`,
    /// `_retry_attempt`, `_request_context_id`, ...), in the order the caller passed them.
    pub options: JsonMap,
}

impl ProviderRequest {
    /// A request with Python's default timeout for `generate_stream` (120 s).
    pub fn new(messages: Vec<Json>) -> Self {
        Self { messages, tools: None, stream: false, timeout: 120.0, options: JsonMap::new() }
    }
}

/// An LLM provider.
pub trait Provider: Send + Sync {
    /// `provider.model_info`.
    fn model_info(&self) -> Option<&ModelInfo>;

    /// Send one request and return the complete response.
    fn generate(&self, request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>>;

    /// Stream the response chunk by chunk.
    fn generate_stream(&self, request: ProviderRequest) -> ChunkStream<'_>;

    /// Release every resource. Idempotent.
    fn close(&self) -> BoxFuture<'_, ()>;

    /// Close anything kept open between requests; the provider stays usable. Default: nothing.
    fn release_idle(&self) -> BoxFuture<'_, ()> {
        Box::pin(async {})
    }

    // Optional capabilities. Python finds these by attribute (`getattr(provider, name, None)`),
    // some one level down and some by walking Sylvia's wrappers through `_provider`
    // ([`capability`]). Each default says the provider has no such attribute.

    /// `provider.hooks`. A transport returns its own manager; a Sylvia wrapper returns its
    /// wrapped provider's, since Python's wrappers share `provider.hooks`. `None` stands for a
    /// fresh manager nobody fires (Python's `Provider()` default).
    fn hooks(&self) -> Option<&HookManager> {
        None
    }

    /// `_provider`: the provider a Sylvia wrapper wraps. `None` for every transport.
    fn wrapped(&self) -> Option<&dyn Provider> {
        None
    }

    /// `provider_request_representation(messages, tools, options)`: the exact client request
    /// object, built without sending it. `None` when the provider has no such method.
    fn request_representation(
        &self,
        _messages: &[Json],
        _tools: Option<&[Json]>,
        _options: &JsonMap,
    ) -> Option<ProviderResult<JsonMap>> {
        None
    }

    /// `record_context_fit(context)`. Returns whether the provider has the method (only
    /// Sylvia's wrappers do); one that has it has acted on `context`.
    fn record_context_fit(&self, _context: &JsonMap) -> bool {
        false
    }

    /// `set_audit_context(context)`. Returns whether the provider has the method.
    fn set_audit_context(&self, _context: &JsonMap) -> bool {
        false
    }
}

/// `_provider_capability(provider, name)`: the first provider in the wrapper chain, from
/// `provider` down through [`Provider::wrapped`], for which `has` gives a result.
pub fn capability<'a, T>(
    provider: &'a dyn Provider,
    mut has: impl FnMut(&'a dyn Provider) -> Option<T>,
) -> Option<T> {
    let mut current = Some(provider);
    while let Some(candidate) = current {
        if let Some(found) = has(candidate) {
            return Some(found);
        }
        current = candidate.wrapped();
    }
    None
}

/// One call `FakeProvider` saw.
#[derive(Debug, Clone, PartialEq)]
pub struct FakeCall {
    pub messages: Vec<Json>,
    pub tools: Option<Vec<Json>>,
}

#[derive(Default)]
struct FakeState {
    index: usize,
    calls: Vec<FakeCall>,
}

const FAKE_FALLBACK: &str = "I don't know what to do next.";

/// `FakeProvider`: predetermined responses for tests.
///
/// Each `generate` returns the next response, then "I don't know what to do next.". Each
/// `generate_stream` yields the configured chunks (or, with none configured, the next response's
/// content as one chunk) and then a `stop` finish chunk.
pub struct FakeProvider {
    responses: Vec<ProviderResponse>,
    stream_chunks: Vec<ProviderStreamChunk>,
    state: Mutex<FakeState>,
}

impl FakeProvider {
    /// `FakeProvider(responses=..., stream_chunks=...)`.
    pub fn new(responses: Vec<ProviderResponse>, stream_chunks: Vec<ProviderStreamChunk>) -> Self {
        Self { responses, stream_chunks, state: Mutex::new(FakeState::default()) }
    }

    /// Every call so far (`fake.calls`).
    pub fn calls(&self) -> Vec<FakeCall> {
        self.state.lock().expect("fake provider lock").calls.clone()
    }

    /// Record a call and take the next scripted response, if any.
    fn take(&self, request: &ProviderRequest) -> Option<ProviderResponse> {
        let mut state = self.state.lock().expect("fake provider lock");
        state.calls.push(FakeCall { messages: request.messages.clone(), tools: request.tools.clone() });
        let next = self.responses.get(state.index).cloned();
        if next.is_some() {
            state.index += 1;
        }
        next
    }
}

impl Provider for FakeProvider {
    fn model_info(&self) -> Option<&ModelInfo> {
        None
    }

    fn generate(&self, request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        Box::pin(async move {
            Ok(self.take(&request).unwrap_or_else(|| ProviderResponse {
                content: Some(FAKE_FALLBACK.into()),
                ..Default::default()
            }))
        })
    }

    fn generate_stream(&self, request: ProviderRequest) -> ChunkStream<'_> {
        // The Python generator records the call (and takes a response) when iteration starts.
        let chunks = futures_util::stream::once(async move {
            let mut chunks = Vec::new();
            if self.stream_chunks.is_empty() {
                let text = match self.take(&request) {
                    Some(response) => response.content.unwrap_or_default(),
                    None => FAKE_FALLBACK.into(),
                };
                chunks.push(ProviderStreamChunk::content(text));
            } else {
                let mut state = self.state.lock().expect("fake provider lock");
                state
                    .calls
                    .push(FakeCall { messages: request.messages.clone(), tools: request.tools.clone() });
                chunks.extend(self.stream_chunks.iter().cloned());
            }
            chunks.push(ProviderStreamChunk::finish("stop"));
            futures_util::stream::iter(chunks.into_iter().map(Ok))
        });
        Box::pin(chunks.flatten())
    }

    fn close(&self) -> BoxFuture<'_, ()> {
        Box::pin(async {})
    }
}
