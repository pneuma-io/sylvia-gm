//! `provider/_protocol_routes.py`: documented protocol routes for OpenCode.
//!
//! `model_protocol` reads the documented families in [`crate::model_facts`].

use crate::pyurl::{urlsplit, urlunsplit, SplitUrl, UrlError};

/// `SUPPORTED_PROTOCOLS`.
pub const SUPPORTED_PROTOCOLS: [&str; 6] = [
    "chat_completions",
    "responses",
    "anthropic_messages",
    "gemini_generate_content",
    "codex_app_server",
    "claude_code",
];

/// `is_opencode_inference(base_url)`: the documented OpenCode gateway.
pub fn is_opencode_inference(base_url: &str) -> Result<bool, UrlError> {
    let url = urlsplit(base_url)?;
    let trimmed = url.path.trim_end_matches('/');
    Ok(url.scheme == "https"
        && url.netloc == "opencode.ai"
        && (url.path.starts_with("/inference/") || trimmed == "/zen/v1" || trimmed == "/zen/go/v1"))
}

/// `inference_base_url(base_url, protocol)`: a saved OpenCode catalogue base resolved to the
/// selected protocol's endpoint; any other base unchanged.
pub fn inference_base_url(base_url: &str, protocol: &str) -> Result<String, UrlError> {
    if !is_opencode_inference(base_url)? {
        return Ok(base_url.to_owned());
    }
    let url = urlsplit(base_url)?;
    let trimmed = url.path.trim_end_matches('/');
    let path = match trimmed {
        "/zen/v1" => "/inference/v1",
        "/zen/go/v1" => "/inference/go/v1",
        other => other,
    };
    let family = if path.starts_with("/inference/go/") { "/inference/go" } else { "/inference" };
    let suffix = match protocol {
        "chat_completions" | "responses" => "/openai/v1",
        "anthropic_messages" => "/anthropic/v1",
        "gemini_generate_content" => "/google/v1beta",
        _ => return Ok(base_url.to_owned()),
    };
    let known = [
        format!("{family}/v1"),
        format!("{family}/openai/v1"),
        format!("{family}/anthropic/v1"),
        format!("{family}/google/v1beta"),
    ];
    if !known.iter().any(|candidate| candidate == path) {
        return Ok(base_url.to_owned());
    }
    Ok(urlunsplit(&SplitUrl { path: format!("{family}{suffix}"), ..url }))
}

/// `model_protocol(preset, model_id, declared, fallback=...)`: a declared supported protocol,
/// else the documented family's, else the fallback.
pub fn model_protocol<'a>(
    preset: &str,
    model_id: &str,
    declared: Option<&'a str>,
    fallback: &'a str,
) -> &'a str {
    if let Some(declared) = declared.filter(|protocol| SUPPORTED_PROTOCOLS.contains(protocol)) {
        return declared;
    }
    crate::model_facts::inferred_model_protocol(preset, model_id).unwrap_or(fallback)
}
