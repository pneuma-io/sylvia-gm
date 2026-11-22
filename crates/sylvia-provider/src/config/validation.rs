//! `config/provider_settings.py`, validation (D2): `validate_provider_model`, one explicit,
//! bounded agent-compatibility probe. It may make one tiny inference request, and is never run
//! during an ordinary save or startup.
//!
//! The probe reads `/models`, then streams one tool-calling request in the transport's own shape
//! and reads it line by line as httpx's `Response.iter_lines()` does: text decoded incrementally
//! with `errors="replace"`, lines split as `str.splitlines()` splits them. Every reply field is a
//! safe status, never a provider's response or error text.
//!
//! Python's event checks are not all guarded: an unexpected shape there (a string where a
//! mapping is read, a number where a list is iterated) raises `AttributeError` or `TypeError`,
//! which the probe's `except (httpx.HTTPError, ValueError)` does not catch. Those reach the owner
//! as its internal error, and so do they here.

use super::discovery::{existing_connection, fetch, json_document, ClientFor, Fetch};
use super::presets::Preset;
use super::provider_settings::{dict, text, ConfigSources};
use super::provider_writes::{base_url, known_preset, private_headers};
use super::pyvalue::{from_json, truthy, type_name};
use super::{ConfigFailure, Data};
use crate::http::{HttpRequest, HttpTransport};
use crate::httpx_text::{quote, LineDecoder, TextDecoder};
use crate::httpx_url::HttpxUrl;
use crate::model_facts::{claude_model_facts, claude_model_id};
use crate::request_metadata::opencode_request_headers;
use serde_json::json;
use std::path::Path;
use std::sync::Arc;
use sylvia_base::pystr;
use sylvia_base::Json;

const TOOL_NAME: &str = "sylvia_compatibility_probe";
const PROMPT: &str = "Call sylvia_compatibility_probe with an empty object.";
const DESCRIPTION: &str = "Confirm function-calling compatibility.";
const STREAM_LIMIT: usize = 1024 * 1024;

fn config(message: impl Into<String>) -> ConfigFailure {
    ConfigFailure::Config(message.into())
}

/// A Python exception the probe does not catch.
fn python(class: &'static str, message: String) -> ConfigFailure {
    ConfigFailure::Python { class, message }
}

/// `value.get(...)` where `value` must be a mapping (`AttributeError` otherwise).
fn require_mapping(value: &Data) -> Result<&Data, ConfigFailure> {
    if value.as_dict().is_some() {
        Ok(value)
    } else {
        Err(python("AttributeError", format!("'{}' object has no attribute 'get'", type_name(value))))
    }
}

/// `for item in value`: a list's items, a mapping's keys, a string's characters; anything
/// else is not iterable (`TypeError`).
fn iterate(value: &Data) -> Result<Vec<Data>, ConfigFailure> {
    match value {
        Data::List(items) | Data::Tuple(items) | Data::Set(items) => Ok(items.clone()),
        Data::Dict(pairs) => Ok(pairs.iter().map(|(key, _)| key.clone()).collect()),
        Data::Str(text_value) => Ok(text_value.chars().map(|c| Data::Str(c.to_string())).collect()),
        Data::Bytes(bytes) => Ok(bytes.iter().map(|byte| Data::Int(i64::from(*byte))).collect()),
        other => Err(python("TypeError", format!("'{}' object is not iterable", type_name(other)))),
    }
}

/// `mapping.get(key, default)` on a mapping.
fn get_or(mapping: &Data, key: &str, default: Data) -> Data {
    mapping.get_str(key).cloned().unwrap_or(default)
}

fn empty_dict() -> Data {
    Data::Dict(Vec::new())
}

/// The probe request for a transport: the route and the JSON payload, in Python's key order.
fn probe_request(transport: &str, model_id: &str) -> (String, Json) {
    let schema = json!({"type": "object", "properties": {}, "additionalProperties": false});
    match transport {
        "responses" => (
            "/responses".into(),
            json!({
                "model": model_id,
                "input": PROMPT,
                "stream": true,
                "max_output_tokens": 128,
                "tools": [{"type": "function", "name": TOOL_NAME, "description": DESCRIPTION, "parameters": schema}],
                "tool_choice": "required",
            }),
        ),
        "gemini_generate_content" => {
            let model = model_id.strip_prefix("models/").unwrap_or(model_id);
            (
                format!("/models/{}:streamGenerateContent?alt=sse", quote(model)),
                json!({
                    "contents": [{"role": "user", "parts": [{"text": PROMPT}]}],
                    "generationConfig": {"maxOutputTokens": 128},
                    "tools": [{"functionDeclarations": [{"name": TOOL_NAME, "parametersJsonSchema": schema}]}],
                    "toolConfig": {"functionCallingConfig": {"mode": "ANY", "allowedFunctionNames": [TOOL_NAME]}},
                }),
            )
        }
        "anthropic_messages" => {
            let mut payload = json!({
                "model": model_id,
                "messages": [{"role": "user", "content": PROMPT}],
                "stream": true,
                "max_tokens": 1024,
                "tools": [{"name": TOOL_NAME, "description": DESCRIPTION, "input_schema": schema}],
            });
            // `accepts_forced_tool_choice`: any model but a Claude one that refuses it.
            let forced =
                claude_model_id(model_id).is_none_or(|claude| claude_model_facts(&claude).forced_tool_choice);
            if forced {
                payload["tool_choice"] = json!({"type": "tool", "name": TOOL_NAME});
            }
            ("/messages".into(), payload)
        }
        _ => (
            "/chat/completions".into(),
            json!({
                "model": model_id,
                "messages": [{"role": "user", "content": PROMPT}],
                "stream": true,
                "max_tokens": 128,
                "tools": [{"type": "function", "function": {"name": TOOL_NAME, "description": DESCRIPTION, "parameters": schema}}],
                "tool_choice": "auto",
            }),
        ),
    }
}

/// Whether one stream event shows the probe tool being called, as Python checks it.
fn saw_tool_call(transport: &str, event: &Data) -> Result<bool, ConfigFailure> {
    let named = |value: Option<&Data>| value.and_then(Data::as_str) == Some(TOOL_NAME);
    match transport {
        "gemini_generate_content" => {
            let mut seen = false;
            let candidates = get_or(event, "candidates", Data::List(Vec::new()));
            let candidates = match candidates {
                Data::List(items) => items,
                _ => Vec::new(),
            };
            for candidate in &candidates {
                let content = get_or(require_mapping(candidate)?, "content", empty_dict());
                let parts = get_or(require_mapping(&content)?, "parts", Data::List(Vec::new()));
                for part in iterate(&parts)? {
                    let call = get_or(require_mapping(&part)?, "functionCall", empty_dict());
                    seen |= named(require_mapping(&call)?.get_str("name"));
                }
            }
            Ok(seen)
        }
        "anthropic_messages" => {
            let block = event.get_str("content_block");
            Ok(event.get_str("type").and_then(Data::as_str) == Some("content_block_start")
                && block.is_some_and(|block| {
                    block.as_dict().is_some()
                        && block.get_str("type").and_then(Data::as_str) == Some("tool_use")
                        && named(block.get_str("name"))
                }))
        }
        "responses" => {
            let item = event.get_str("item");
            Ok(item.is_some_and(|item| {
                item.as_dict().is_some()
                    && item.get_str("type").and_then(Data::as_str) == Some("function_call")
                    && named(item.get_str("name"))
            }))
        }
        _ => {
            let mut seen = false;
            for choice in iterate(&get_or(event, "choices", Data::List(Vec::new())))? {
                let delta = if choice.as_dict().is_some() {
                    get_or(&choice, "delta", empty_dict())
                } else {
                    empty_dict()
                };
                if delta.as_dict().is_none() {
                    continue;
                }
                let calls = get_or(&delta, "tool_calls", Data::List(Vec::new()));
                let calls = if truthy(&calls) { iterate(&calls)? } else { Vec::new() };
                for call in calls {
                    let function =
                        if call.as_dict().is_some() { call.get_str("function").cloned() } else { None };
                    seen |= function.is_some_and(|function| {
                        function.as_dict().is_some() && named(function.get_str("name"))
                    });
                }
            }
            Ok(seen)
        }
    }
}

/// `validate_provider_model(config_path, ...)`'s arguments, each as Python receives it.
#[derive(Debug, Clone)]
pub struct Validate {
    pub provider_id: Data,
    pub base_url: Data,
    pub credential: Data,
    pub model_id: Data,
    pub transport: Data,
    pub preset_id: Data,
    pub headers: Data,
}

impl Default for Validate {
    fn default() -> Self {
        let none = || Data::None;
        Self {
            provider_id: none(),
            base_url: none(),
            credential: none(),
            model_id: none(),
            transport: none(),
            preset_id: none(),
            headers: none(),
        }
    }
}

/// The probe's report, in Python's key order.
struct Report {
    reachability: &'static str,
    authentication: &'static str,
    model_discovery: &'static str,
    transport: &'static str,
    streaming: &'static str,
    tool_calling: &'static str,
    agent_ready: bool,
    message: &'static str,
}

impl Report {
    fn data(&self) -> Data {
        dict(vec![
            ("reachability", text(self.reachability)),
            ("authentication", text(self.authentication)),
            ("model_discovery", text(self.model_discovery)),
            ("transport", text(self.transport)),
            ("streaming", text(self.streaming)),
            ("tool_calling", text(self.tool_calling)),
            ("agent_ready", Data::Bool(self.agent_ready)),
            ("message", text(self.message)),
        ])
    }
}

/// `validate_provider_model(config_path, ...)`: one bounded agent-compatibility probe.
pub async fn validate_provider_model(
    sources: ConfigSources<'_>,
    config_path: &Path,
    request: &Validate,
    client_for: &ClientFor,
) -> Result<Data, ConfigFailure> {
    let requested_id = request.provider_id.as_str().unwrap_or("").to_owned();
    let replacing = request.credential.as_str().is_some_and(|value| !pystr::strip(value).is_empty())
        || truthy(&request.headers);
    let existing = if requested_id.is_empty() {
        None
    } else {
        existing_connection(sources, config_path, &requested_id, replacing)?
    };
    let selected = match &request.preset_id {
        Data::Str(preset_id) if !preset_id.is_empty() => Data::Str(preset_id.clone()),
        _ => text(existing.as_ref().map_or(requested_id.as_str(), |found| found.preset_id)),
    };
    let preset: &Preset = known_preset(&selected)?;
    if preset.id == "claude_runtime" {
        return Err(config("Use the Claude runtime connection; an HTTP probe cannot verify this model"));
    }
    if preset.id == "chatgpt_subscription" {
        return Err(config("Use the ChatGPT subscription connection for model verification"));
    }
    let base = if truthy(&request.base_url) {
        request.base_url.clone()
    } else {
        existing.as_ref().map_or(Data::None, |found| text(&found.base_url))
    };
    let endpoint = base_url(preset, &base)?;
    let upstream_model_id = match &request.model_id {
        Data::Str(value) if !pystr::strip(value).is_empty() => pystr::strip(value).to_owned(),
        _ => return Err(config("Provider model ID is required")),
    };
    if pystr::len(&upstream_model_id) > 256 || upstream_model_id.chars().any(|c| (c as u32) < 32) {
        return Err(config("Provider model ID is invalid"));
    }
    let selected_transport = match &request.transport {
        Data::Str(value) if !value.is_empty() => value.clone(),
        _ => preset.default_transport.to_owned(),
    };
    if ![
        "chat_completions",
        "responses",
        "anthropic_messages",
        "codex_app_server",
        "gemini_generate_content",
        "claude_code",
    ]
    .contains(&selected_transport.as_str())
    {
        return Err(config("Provider model transport is unsupported"));
    }
    let mut supplied =
        request.credential.as_str().map(|value| pystr::strip(value).to_owned()).unwrap_or_default();
    if supplied.is_empty() {
        if let Some(found) = &existing {
            supplied = found.credential.clone();
        }
    }
    let mut headers = private_headers(&request.headers)?;
    if headers.is_empty() {
        if let Some(found) = &existing {
            headers = found.headers.clone();
        }
    }
    let setdefault = |headers: &mut Vec<(String, String)>, name: &str, value: String| {
        if !headers.iter().any(|(existing, _)| existing == name) {
            headers.push((name.to_owned(), value));
        }
    };
    let transport = selected_transport.as_str();
    if !supplied.is_empty() && !matches!(transport, "anthropic_messages" | "gemini_generate_content") {
        setdefault(&mut headers, "Authorization", format!("Bearer {supplied}"));
    }
    if transport == "gemini_generate_content" && !supplied.is_empty() {
        setdefault(&mut headers, "x-goog-api-key", supplied.clone());
    }
    if transport == "anthropic_messages" {
        if !supplied.is_empty() {
            setdefault(&mut headers, "x-api-key", supplied.clone());
        }
        setdefault(&mut headers, "anthropic-version", "2023-06-01".into());
    }
    if matches!(preset.id, "opencode_go" | "opencode_zen") {
        // `dict.update`: the protocol values replace configured ones of the same spelling.
        for (name, value) in opencode_request_headers("provider-compatibility-probe") {
            match headers.iter_mut().find(|(existing, _)| *existing == name) {
                Some(slot) => slot.1 = value,
                None => headers.push((name, value)),
            }
        }
    }

    let mut report = Report {
        reachability: "failed",
        authentication: if supplied.is_empty() { "not_required" } else { "unverified" },
        model_discovery: "unverified",
        transport: "unverified",
        streaming: "unverified",
        tool_calling: "unverified",
        agent_ready: false,
        message: "The provider is unreachable. Start it or check the endpoint.",
    };
    match probe(
        client_for,
        &endpoint,
        &headers,
        transport,
        &upstream_model_id,
        !supplied.is_empty(),
        &mut report,
    )
    .await
    {
        Probe::Done => {}
        Probe::Early => return Ok(report.data()),
        Probe::Raised(error) => return Err(error),
    }
    report.agent_ready = report.reachability == "passed"
        && report.authentication != "failed"
        && report.transport == "passed"
        && report.streaming == "passed"
        && report.tool_calling == "passed";
    report.message = if report.agent_ready {
        "Provider and model passed the agent compatibility probe."
    } else {
        "The provider connected, but this model did not pass every agent capability check."
    };
    Ok(report.data())
}

/// How the probe's `try` block ended.
enum Probe {
    /// It ran to the end.
    Done,
    /// It returned the report early, or an httpx error or `ValueError` was caught.
    Early,
    /// An exception the probe does not catch.
    Raised(ConfigFailure),
}

async fn probe(
    client_for: &ClientFor,
    endpoint: &str,
    headers: &[(String, String)],
    transport: &str,
    model_id: &str,
    supplied: bool,
    report: &mut Report,
) -> Probe {
    let client: Arc<dyn HttpTransport> = match client_for(20.0) {
        Ok(client) => client,
        Err(ConfigFailure::Python { class: "ValueError", .. }) => return Probe::Early,
        Err(error) => return Probe::Raised(error),
    };
    let Ok(catalog) = fetch(&client, "GET", format!("{endpoint}/models"), headers, None).await else {
        return Probe::Early;
    };
    if matches!(catalog.status, 401 | 403) {
        report.reachability = "passed";
        report.authentication = "failed";
        report.message = "The provider rejected the API credential.";
        return Probe::Early;
    }
    report.reachability = if catalog.status < 500 { "passed" } else { "failed" };
    if report.reachability == "failed" {
        return Probe::Early;
    }
    if supplied && catalog.status == 200 {
        report.authentication = "passed";
    }
    if catalog.status == 200 {
        match json_document(&catalog.content) {
            Ok(document) => {
                if let Some(Data::List(rows)) = document.as_dict().and_then(|_| document.get_str("data")) {
                    let listed = rows.iter().any(|row| {
                        row.as_dict().is_some() && row.get_str("id").and_then(Data::as_str) == Some(model_id)
                    });
                    report.model_discovery = if listed { "passed" } else { "model_not_listed" };
                }
            }
            Err(Fetch::Value | Fetch::Http) => report.model_discovery = "invalid_response",
        }
    }

    let (route, payload) = probe_request(transport, model_id);
    let body = sylvia_base::pyjson::dump_bytes(&payload, sylvia_base::pyjson::Dump::WIRE.ascii(false));
    let url = format!("{endpoint}{route}");
    let url = HttpxUrl::parse(&url).map_or(url, |parsed| parsed.to_string());
    let mut request_headers: Vec<(String, String)> =
        if headers.iter().any(|(name, _)| name.eq_ignore_ascii_case("user-agent")) {
            Vec::new()
        } else {
            vec![("User-Agent".into(), crate::openai_compat::sylvia_user_agent())]
        };
    request_headers.extend_from_slice(headers);
    crate::net::json_content_headers(&mut request_headers, body.len());
    let request = HttpRequest { method: "POST".into(), url, headers: request_headers, body };
    let Ok(mut response) = client.send(request).await else { return Probe::Early };
    if matches!(response.status, 401 | 403) {
        report.authentication = "failed";
        report.message = "The provider rejected the API credential.";
        return Probe::Early;
    }
    if response.status != 200 {
        report.transport = "failed";
        report.message = "The endpoint is reachable, but the selected transport was rejected.";
        return Probe::Early;
    }
    report.transport = "passed";
    if supplied {
        report.authentication = "passed";
    }
    let mut decoder = TextDecoder::for_content_type(response.header("content-type"));
    let mut lines = LineDecoder::default();
    let mut saw_event = false;
    let mut saw_call = false;
    let mut byte_count = 0usize;
    let mut finished = false;
    'reading: while !finished {
        let batch = match response.body.next_piece().await {
            Some(Ok(piece)) => lines.decode(&decoder.decode(&piece, false)),
            Some(Err(_)) => return Probe::Early,
            None => {
                finished = true;
                let mut rest = lines.decode(&decoder.decode(&[], true));
                rest.extend(lines.flush());
                rest
            }
        };
        for line in batch {
            byte_count += line.len();
            if byte_count > STREAM_LIMIT {
                break 'reading;
            }
            if line.is_empty() || line.starts_with(':') {
                continue;
            }
            let data = match line.strip_prefix("data:") {
                Some(rest) => pystr::strip(rest),
                None => pystr::strip(&line),
            };
            if data.is_empty() || data == "[DONE]" {
                continue;
            }
            let Ok(value) = sylvia_base::pyjson::loads(data) else { continue };
            let Ok(event) = from_json(&value) else { continue };
            if event.as_dict().is_none() {
                continue;
            }
            saw_event = true;
            match event.get_str("type") {
                Some(kind @ (Data::List(_) | Data::Dict(_) | Data::Set(_))) => {
                    return Probe::Raised(python(
                        "TypeError",
                        format!("unhashable type: '{}'", type_name(kind)),
                    ));
                }
                Some(Data::Str(kind))
                    if matches!(kind.as_str(), "error" | "response.failed" | "response.incomplete") =>
                {
                    report.transport = "failed";
                    report.message = "The provider could not complete the test response.";
                    return Probe::Early;
                }
                _ => {}
            }
            match saw_tool_call(transport, &event) {
                Ok(seen) => saw_call |= seen,
                Err(error) => return Probe::Raised(error),
            }
        }
    }
    report.streaming = if saw_event { "passed" } else { "failed" };
    report.tool_calling = if saw_call { "passed" } else { "failed" };
    Probe::Done
}
