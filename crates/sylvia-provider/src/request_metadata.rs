//! `provider/_request_metadata.py`: provider-protocol request metadata bound per session, without
//! changing shared clients.
//!
//! The wrapper's `provider_request_representation` (the fit and Full Audit preview) comes with
//! the agent layer, which is its only caller.

use crate::error::ProviderResult;
use crate::hooks::HookManager;
use crate::provider::{BoxFuture, ChunkStream, Provider, ProviderRequest};
use crate::pyobj::py_str;
use crate::types::{ModelInfo, ProviderResponse};
use sha2::{Digest, Sha256};
use std::sync::Arc;
use sylvia_base::pystr::casefold;
use sylvia_base::{Json, JsonMap};

const SESSION_HEADER: &str = "x-opencode-session";
const CLIENT_HEADER: &str = "x-opencode-client";
const USER_AGENT_HEADER: &str = "User-Agent";

/// `_OPENCODE_PROTOCOL_HEADER_NAMES`: the casefolded names Sylvia owns per request.
fn is_protocol_header(name: &str) -> bool {
    let name = casefold(name);
    [SESSION_HEADER, CLIENT_HEADER, USER_AGENT_HEADER].iter().any(|owned| casefold(owned) == name)
}

/// `opencode_request_headers(session_id)`: opaque, stable OpenCode metadata for one durable
/// Sylvia session, in Python's order.
pub fn opencode_request_headers(session_id: &str) -> Vec<(String, String)> {
    let digest = hex::encode(Sha256::digest(session_id.as_bytes()));
    vec![
        (SESSION_HEADER.into(), format!("sylvia_{digest}")),
        (CLIENT_HEADER.into(), "sylvia-gm".into()),
        (USER_AGENT_HEADER.into(), format!("Sylvia-GM/{}", env!("CARGO_PKG_VERSION"))),
    ]
}

/// A Python `dict[str, str]` assignment: an existing key keeps its place.
fn assign(pairs: &mut Vec<(String, String)>, name: String, value: String) {
    match pairs.iter_mut().find(|(existing, _)| *existing == name) {
        Some(slot) => slot.1 = value,
        None => pairs.push((name, value)),
    }
}

/// `without_opencode_protocol_headers(headers)`: drop static values whose OpenCode meaning Sylvia
/// owns per request.
pub fn without_opencode_protocol_headers(headers: &[(String, String)]) -> Vec<(String, String)> {
    let mut kept = Vec::new();
    for (name, value) in headers {
        if !is_protocol_header(name) {
            assign(&mut kept, name.clone(), value.clone());
        }
    }
    kept
}

/// `_with_protocol_headers(options, protocol_headers)`: one call's extra headers merged, the
/// protocol-owned values taking precedence.
pub fn with_protocol_headers(options: &JsonMap, protocol_headers: &[(String, String)]) -> JsonMap {
    let mut merged: Vec<(String, String)> = Vec::new();
    if let Some(Json::Object(configured)) = options.get("extra_headers") {
        for (name, value) in configured {
            if !is_protocol_header(name) {
                assign(&mut merged, name.clone(), py_str(value));
            }
        }
    }
    for (name, value) in protocol_headers {
        assign(&mut merged, name.clone(), value.clone());
    }
    let mut result = options.clone();
    result.insert(
        "extra_headers".into(),
        Json::Object(merged.into_iter().map(|(name, value)| (name, Json::String(value))).collect()),
    );
    result
}

/// `SessionBoundRequestMetadataProvider`: immutable per-session request metadata on a shared
/// provider client.
pub struct SessionBoundRequestMetadataProvider {
    provider: Arc<dyn Provider>,
    headers: Vec<(String, String)>,
}

impl SessionBoundRequestMetadataProvider {
    /// `SessionBoundRequestMetadataProvider(provider, headers=...)`.
    pub fn new(provider: Arc<dyn Provider>, headers: Vec<(String, String)>) -> Self {
        Self { provider, headers }
    }

    /// The headers every call carries.
    pub fn headers(&self) -> &[(String, String)] {
        &self.headers
    }

    /// The wrapped provider.
    pub fn inner(&self) -> &Arc<dyn Provider> {
        &self.provider
    }

    fn bound(&self, mut request: ProviderRequest) -> ProviderRequest {
        request.options = with_protocol_headers(&request.options, &self.headers);
        request
    }
}

impl Provider for SessionBoundRequestMetadataProvider {
    fn model_info(&self) -> Option<&ModelInfo> {
        self.provider.model_info()
    }

    fn generate(&self, request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        self.provider.generate(self.bound(request))
    }

    fn generate_stream(&self, request: ProviderRequest) -> ChunkStream<'_> {
        self.provider.generate_stream(self.bound(request))
    }

    fn close(&self) -> BoxFuture<'_, ()> {
        self.provider.close()
    }

    fn release_idle(&self) -> BoxFuture<'_, ()> {
        self.provider.release_idle()
    }

    fn hooks(&self) -> Option<&HookManager> {
        self.provider.hooks()
    }

    fn wrapped(&self) -> Option<&dyn Provider> {
        Some(self.provider.as_ref())
    }

    fn base_url(&self) -> Option<&str> {
        self.provider.base_url()
    }

    /// `provider_request_representation`: the wrapped provider's, with the same per-request
    /// headers, or the neutral `{"messages", "tools", "options"}` when it has none (one level,
    /// as Python's `getattr`).
    fn request_representation(
        &self,
        messages: &[Json],
        tools: Option<&[Json]>,
        options: &JsonMap,
    ) -> Option<ProviderResult<JsonMap>> {
        let options = with_protocol_headers(options, &self.headers);
        if let Some(value) = self.provider.request_representation(messages, tools, &options) {
            return Some(value);
        }
        let mut neutral = JsonMap::new();
        neutral.insert("messages".into(), Json::Array(messages.to_vec()));
        neutral.insert("tools".into(), Json::Array(tools.map(<[Json]>::to_vec).unwrap_or_default()));
        neutral.insert("options".into(), Json::Object(options));
        Some(Ok(neutral))
    }
}
