//! `config/provider_settings.py`, discovery (D1): `discover_provider_models`, with the bounded
//! `/models` catalogue (Anthropic's pagination included) and the native capacity probes for
//! Featherless, LM Studio and Ollama, and `detect_local_providers`.
//!
//! Python opens a short-lived `httpx.Client(timeout=..., follow_redirects=False)` for each step.
//! Here each step asks a [`ClientFor`] for its client, so tests replay recorded exchanges through
//! the scripted transport. The headers compared with Python are the ones Sylvia sets; the HTTP
//! client's own (`Host`, `Accept`, `Accept-Encoding`, `Connection`, `User-Agent`) follow
//! differences row 8.

use super::loader::{load_config, provider_credentials};
use super::presets::{presets, Preset};
use super::provider_settings::{dict, discovery_model, provider_preset_id, text, ConfigSources};
use super::provider_writes::{base_url, known_preset, private_headers};
use super::pyvalue::from_json;
use super::user_settings::read_user_settings;
use super::{ConfigFailure, Data};
use crate::context_capacity::{resolve_context_capacity, CapacityInputs, ResolvedContextCapacity};
use crate::http::{HttpRequest, HttpTransport, TransportError};
use crate::httpx_url::HttpxUrl;
use crate::pyurl::urlsplit;
use std::path::Path;
use std::sync::Arc;
use sylvia_base::pyyaml::data_key_eq;
use sylvia_base::{pystr, Json};

/// `_DISCOVERY_RESPONSE_LIMIT`.
const RESPONSE_LIMIT: usize = 32 * 1024 * 1024;
/// `_DISCOVERY_MODEL_LIMIT`.
const MODEL_LIMIT: usize = 100_000;
/// `_DISCOVERY_PAGE_LIMIT`.
const PAGE_LIMIT: i64 = 200;

/// `httpx.Client(timeout=seconds, follow_redirects=False)`: the client for one discovery step.
/// `Err` is the failure building it (a bad proxy variable, a certificate file that can't be read).
pub type ClientFor = dyn Fn(f64) -> Result<Arc<dyn HttpTransport>, ConfigFailure> + Send + Sync;

/// The product's discovery clients: a new network client per step, configured from the process
/// environment as `httpx.Client(timeout=..., follow_redirects=False)` is. Building one fails as
/// httpx does: `ValueError` for a bad proxy variable, otherwise an internal error.
pub fn network_clients() -> Arc<ClientFor> {
    Arc::new(|seconds: f64| {
        crate::net::NetworkTransport::new(crate::net::ClientSettings::plain(seconds))
            .map(|transport| Arc::new(transport) as Arc<dyn HttpTransport>)
            .map_err(|error| match error {
                crate::proxy::ProxyError::Value(message) => {
                    ConfigFailure::Python { class: "ValueError", message }
                }
                crate::proxy::ProxyError::Internal(message) => {
                    ConfigFailure::Python { class: "InvalidURL", message }
                }
            })
    })
}

/// What failed below a status code: httpx's `HTTPError` (a transport failure) or `ValueError`
/// (a body that is not JSON).
pub(super) enum Fetch {
    Http,
    Value,
}

/// One response: the status and the whole body (httpx reads it before `.content`).
pub(super) struct Reply {
    pub(super) status: u16,
    pub(super) content: Vec<u8>,
}

fn config(message: impl Into<String>) -> ConfigFailure {
    ConfigFailure::Config(message.into())
}

/// `application/x-www-form-urlencoded` with `quote_plus`, as `urllib.parse.urlencode` writes it.
fn quote_plus(value: &str) -> String {
    let mut out = String::new();
    for byte in value.bytes() {
        match byte {
            b' ' => out.push('+'),
            b'A'..=b'Z' | b'a'..=b'z' | b'0'..=b'9' | b'_' | b'.' | b'-' | b'~' => out.push(char::from(byte)),
            _ => out.push_str(&format!("%{byte:02X}")),
        }
    }
    out
}

/// `client.get/post(url, params=...)`: httpx's URL with the parameters merged into its query.
pub(super) fn request_url(url: &str, params: &[(&str, String)]) -> Result<String, ConfigFailure> {
    let query = if params.is_empty() {
        String::new()
    } else {
        let pairs: Vec<String> =
            params.iter().map(|(key, value)| format!("{}={}", quote_plus(key), quote_plus(value))).collect();
        format!("?{}", pairs.join("&"))
    };
    HttpxUrl::parse(&format!("{url}{query}"))
        .map(|parsed| parsed.to_string())
        .map_err(|error| ConfigFailure::Python { class: "InvalidURL", message: error.0 })
}

/// Send one request and read the whole body.
pub(super) async fn fetch(
    client: &Arc<dyn HttpTransport>,
    method: &str,
    url: String,
    request_headers: &[(String, String)],
    body: Option<Vec<u8>>,
) -> Result<Reply, Fetch> {
    // httpx normalises every URL it is given; the catalogue's was already, which is a no-op.
    let url = HttpxUrl::parse(&url).map_or(url, |parsed| parsed.to_string());
    // httpx's client defaults come first. Of those, Sylvia sends only its own `User-Agent`
    // (differences row 8), unless a configured private header already names one.
    let mut headers: Vec<(String, String)> =
        if request_headers.iter().any(|(name, _)| name.eq_ignore_ascii_case("user-agent")) {
            Vec::new()
        } else {
            vec![("User-Agent".into(), crate::openai_compat::sylvia_user_agent())]
        };
    headers.extend_from_slice(request_headers);
    if body.is_some() {
        headers.push(("Content-Type".into(), "application/json".into()));
    }
    let request = HttpRequest { method: method.into(), url, headers, body: body.unwrap_or_default() };
    let mut response = client.send(request).await.map_err(|_: TransportError| Fetch::Http)?;
    let content = response.read_all().await.map_err(|_| Fetch::Http)?;
    Ok(Reply { status: response.status, content })
}

/// `json.loads(content)` for bytes: `detect_encoding`, then a decode with `surrogatepass`. A
/// lone surrogate cannot be held (differences row 3), so it fails as a decoding error would.
pub(super) fn json_document(content: &[u8]) -> Result<Data, Fetch> {
    let decoded = decode_json_bytes(content).ok_or(Fetch::Value)?;
    let value: Json = sylvia_base::pyjson::loads(&decoded).map_err(|_| Fetch::Value)?;
    from_json(&value).map_err(|_| Fetch::Value)
}

/// `json.detect_encoding(b)` and the decode.
fn decode_json_bytes(b: &[u8]) -> Option<String> {
    let utf16 = |bytes: &[u8], big: bool| -> Option<String> {
        if !bytes.len().is_multiple_of(2) {
            return None;
        }
        let units: Vec<u16> = bytes
            .as_chunks::<2>()
            .0
            .iter()
            .map(|pair| if big { u16::from_be_bytes(*pair) } else { u16::from_le_bytes(*pair) })
            .collect();
        String::from_utf16(&units).ok()
    };
    let utf32 = |bytes: &[u8], big: bool| -> Option<String> {
        if !bytes.len().is_multiple_of(4) {
            return None;
        }
        bytes
            .as_chunks::<4>()
            .0
            .iter()
            .map(|quad| {
                char::from_u32(if big { u32::from_be_bytes(*quad) } else { u32::from_le_bytes(*quad) })
            })
            .collect()
    };
    if let Some(rest) = b.strip_prefix(&[0x00, 0x00, 0xFE, 0xFF]) {
        return utf32(rest, true);
    }
    if let Some(rest) = b.strip_prefix(&[0xFF, 0xFE, 0x00, 0x00]) {
        return utf32(rest, false);
    }
    if let Some(rest) = b.strip_prefix(&[0xFE, 0xFF]) {
        return utf16(rest, true);
    }
    if let Some(rest) = b.strip_prefix(&[0xFF, 0xFE]) {
        return utf16(rest, false);
    }
    if let Some(rest) = b.strip_prefix(&[0xEF, 0xBB, 0xBF]) {
        return String::from_utf8(rest.to_vec()).ok();
    }
    if b.len() >= 4 {
        if b[0] == 0 {
            return if b[1] != 0 { utf16(b, true) } else { utf32(b, true) };
        }
        if b[1] == 0 {
            return if b[2] != 0 || b[3] != 0 { utf16(b, false) } else { utf32(b, false) };
        }
    } else if b.len() == 2 {
        if b[0] == 0 {
            return utf16(b, true);
        }
        if b[1] == 0 {
            return utf16(b, false);
        }
    }
    String::from_utf8(b.to_vec()).ok()
}

/// `_response_document(response)`: a 200 mapping within the size bound, else `None`.
fn response_document(reply: &Reply) -> Option<Data> {
    if reply.status != 200 || reply.content.len() > RESPONSE_LIMIT {
        return None;
    }
    json_document(&reply.content).ok().filter(|document| document.as_dict().is_some())
}

/// `_native_endpoint(endpoint, suffix)`: a native route beside an OpenAI-compatible `/v1` base.
fn native_endpoint(endpoint: &str, suffix: &str) -> String {
    let base = endpoint.strip_suffix("/v1").unwrap_or(endpoint);
    format!("{}{suffix}", base.trim_end_matches('/'))
}

/// A Python `int` that is not a `bool`.
pub(super) fn int(value: Option<&Data>) -> Option<i64> {
    match value {
        Some(Data::Int(number)) => Some(*number),
        _ => None,
    }
}

/// `mapping.get(key)` for a mapping value.
fn get<'a>(mapping: &'a Data, key: &str) -> Option<&'a Data> {
    mapping.as_dict().and_then(|_| mapping.get_str(key))
}

/// `{**row, **pairs}` and `row.update(pairs)`: each key keeps its place or is appended.
fn merge(row: &mut Data, pairs: Vec<(&str, Data)>) {
    let entries = row.as_dict_mut().expect("a mapping");
    for (key, value) in pairs {
        let key = text(key);
        match entries.iter_mut().find(|(existing, _)| data_key_eq(existing, &key)) {
            Some(slot) => slot.1 = value,
            None => entries.push((key, value)),
        }
    }
}

fn capacity_pairs(capacity: &ResolvedContextCapacity) -> Vec<(&'static str, Data)> {
    capacity
        .projection()
        .into_iter()
        .map(|(key, value)| (key, from_json(&value).expect("capacity values are small")))
        .collect()
}

/// An enabled saved connection: Python's `(base_url, credential, headers, preset_id)`.
pub(super) struct Existing {
    pub(super) base_url: String,
    pub(super) credential: String,
    pub(super) headers: Vec<(String, String)>,
    pub(super) preset_id: &'static str,
}

/// `_existing_connection(config_path, provider_id, replacing_secrets=...)`: an enabled saved
/// connection's endpoint, unlocked credential, headers and preset.
pub(super) fn existing_connection(
    sources: ConfigSources<'_>,
    config_path: &Path,
    provider_id: &str,
    replacing_secrets: bool,
) -> Result<Option<Existing>, ConfigFailure> {
    let configuration = load_config(sources.default_config, Some(config_path), sources.seal, sources.env)?;
    let Some((_, definition)) = configuration.providers.iter().find(|(key, _)| key == provider_id) else {
        return Ok(None);
    };
    if !definition.enabled {
        return Ok(None);
    }
    let document = read_user_settings(sources.seal, config_path)?;
    let raw = document
        .get_str("providers")
        .filter(|value| value.as_dict().is_some())
        .and_then(|providers| providers.get_str(provider_id).filter(|value| value.as_dict().is_some()));
    let preset_id = provider_preset_id(provider_id, raw);
    let (credential, headers) = match provider_credentials(definition, sources.seal, sources.env) {
        Ok(unlocked) => unlocked,
        Err(ConfigFailure::Config(_)) if replacing_secrets => (String::new(), Vec::new()),
        Err(error) => return Err(error),
    };
    Ok(Some(Existing { base_url: definition.base_url.clone(), credential, headers, preset_id }))
}

/// `_model_catalog(client, ...)`: the bounded `/models` rows, following Anthropic's pagination.
async fn model_catalog(
    client: &Arc<dyn HttpTransport>,
    endpoint: &str,
    preset_id: &str,
    name: &str,
    headers: &[(String, String)],
) -> Result<Vec<Data>, Result<ConfigFailure, Fetch>> {
    let mut rows: Vec<Data> = Vec::new();
    let mut cursors: Vec<String> = Vec::new();
    let mut params: Vec<(&str, String)> =
        if preset_id == "anthropic" { vec![("limit", "1000".into())] } else { Vec::new() };
    let mut total_bytes = 0usize;
    for _ in 0..100 {
        let url = request_url(&format!("{endpoint}/models"), &params).map_err(Ok)?;
        let reply = fetch(client, "GET", url, headers, None).await.map_err(Err)?;
        match reply.status {
            401 | 403 => return Err(Ok(config(format!("{name} rejected the API credential")))),
            404 | 405 => {
                return Err(Ok(config(format!(
                    "{name} does not expose model discovery here. Enter its documented model identifier and limits under Advanced."
                ))))
            }
            200 => {}
            status => return Err(Ok(config(format!("{name} model discovery failed with HTTP {status}")))),
        }
        total_bytes += reply.content.len();
        if total_bytes > RESPONSE_LIMIT {
            return Err(Ok(config("Provider model discovery response is too large")));
        }
        let document = json_document(&reply.content).map_err(Err)?;
        let page = match &document {
            Data::List(items) if matches!(preset_id, "together" | "mistral") => Some(items.clone()),
            Data::Dict(_) => match document.get_str("data") {
                Some(Data::List(items)) => Some(items.clone()),
                _ => None,
            },
            _ => None,
        };
        let Some(page) = page else { return Err(Ok(config("Provider returned an invalid model catalog"))) };
        let empty_page = page.is_empty();
        rows.extend(page);
        if rows.len() > MODEL_LIMIT {
            return Err(Ok(config("Provider model catalog exceeds the supported bound")));
        }
        let has_more =
            document.as_dict().is_some() && document.get_str("has_more").is_some_and(super::pyvalue::truthy);
        if preset_id != "anthropic" || !has_more {
            return Ok(rows);
        }
        let cursor = match document.get_str("last_id") {
            Some(Data::Str(cursor)) if !cursor.is_empty() && !empty_page && !cursors.contains(cursor) => {
                cursor.clone()
            }
            _ => return Err(Ok(config("Provider returned invalid model catalog pagination"))),
        };
        cursors.push(cursor.clone());
        match params.iter_mut().find(|(key, _)| *key == "after_id") {
            Some(slot) => slot.1 = cursor,
            None => params.push(("after_id", cursor)),
        }
    }
    Err(Ok(config("Provider model catalog exceeds the supported page bound")))
}

/// `_merge_featherless_capacity(...)`: the account plan ceiling; the notice when it is unknown.
async fn merge_featherless_capacity(
    client: &Arc<dyn HttpTransport>,
    endpoint: &str,
    headers: &[(String, String)],
    models: &mut [Data],
    now: &(dyn Fn() -> String + Sync),
) -> Option<String> {
    let plan = match fetch(client, "GET", format!("{endpoint}/plan"), headers, None).await {
        Ok(reply) => response_document(&reply),
        Err(_) => None,
    };
    let limit_value = plan.as_ref().and_then(|plan| plan.get_str("max_context_length"));
    let bad_limit = match limit_value {
        None => true,
        Some(Data::None) => false,
        Some(Data::Int(limit)) => *limit < 512,
        Some(_) => true,
    };
    if plan.is_none() || bad_limit {
        return Some(
            "Featherless plan limits were unavailable. Refresh, or enter your plan's context allowance under Advanced."
                .into(),
        );
    }
    let limit = int(limit_value);
    let observed = now();
    for model in models.iter_mut() {
        let maximum = int(get(model, "declared_context_window"));
        let runtime = match (maximum.filter(|value| *value != 0), limit) {
            (Some(maximum), Some(limit)) => Some(maximum.min(limit)),
            _ => maximum.filter(|value| *value != 0).or(limit),
        };
        let declared_source = get(model, "declared_context_source").and_then(Data::as_str).map(str::to_owned);
        let declared_observed =
            get(model, "declared_context_observed_at").and_then(Data::as_str).map(str::to_owned);
        let capacity = resolve_context_capacity(&CapacityInputs {
            declared_context_window: maximum,
            declared_context_source: declared_source.as_deref(),
            runtime_context_window: runtime,
            runtime_context_source: Some("provider_metadata"),
            declared_observed_at: declared_observed.as_deref(),
            runtime_observed_at: Some(&observed),
            ..CapacityInputs::default()
        });
        merge(model, capacity_pairs(&capacity));
    }
    None
}

/// `_lm_studio_capacity_models(...)`: LM Studio's loaded instance contexts and model maxima.
async fn lm_studio_capacity_models(
    client: &Arc<dyn HttpTransport>,
    endpoint: &str,
    headers: &[(String, String)],
) -> Vec<Data> {
    let Ok(reply) = fetch(client, "GET", native_endpoint(endpoint, "/api/v1/models"), headers, None).await
    else {
        return Vec::new();
    };
    let Some(document) = response_document(&reply) else { return Vec::new() };
    let rows = match document.get_str("models") {
        Some(Data::List(rows)) if rows.len() <= MODEL_LIMIT => rows.clone(),
        _ => return Vec::new(),
    };
    let mut models = Vec::new();
    for raw in &rows {
        if raw.as_dict().is_none() || raw.get_str("type").and_then(Data::as_str) != Some("llm") {
            continue;
        }
        let Some(key) =
            raw.get_str("key").and_then(Data::as_str).filter(|key| !key.is_empty() && pystr::len(key) <= 256)
        else {
            continue;
        };
        let declared = int(raw.get_str("max_context_length")).filter(|value| *value >= 512);
        let mut loaded = Vec::new();
        if let Some(Data::List(instances)) = raw.get_str("loaded_instances") {
            for instance in instances {
                if instance.as_dict().is_none() {
                    continue;
                }
                let id = instance.get_str("id").and_then(Data::as_str).filter(|id| !id.is_empty());
                let context = instance
                    .get_str("config")
                    .filter(|value| value.as_dict().is_some())
                    .and_then(|config| int(config.get_str("context_length")));
                if let (Some(id), Some(context)) = (id, context.filter(|value| *value >= 512)) {
                    loaded.push(dict(vec![("id", text(id)), ("context_window", Data::Int(context))]));
                }
            }
        }
        let tools =
            raw.get_str("capabilities").filter(|value| value.as_dict().is_some()).and_then(|capabilities| {
                match capabilities.get_str("trained_for_tool_use") {
                    Some(Data::Bool(flag)) => Some(*flag),
                    _ => None,
                }
            });
        let name = match raw.get_str("display_name") {
            Some(value @ Data::Str(_)) => value.clone(),
            _ => text(key),
        };
        models.push(dict(vec![
            ("id", text(key)),
            ("name", name),
            ("declared_context_window", declared.map_or(Data::None, Data::Int)),
            ("loaded_instances", Data::List(loaded)),
            ("supports_tools", tools.map_or(Data::None, Data::Bool)),
        ]));
    }
    models
}

/// `str(value)` of a row's id.
fn id_of(row: &Data) -> String {
    super::pyvalue::py_str(row.get_str("id").unwrap_or(&Data::None))
}

/// `value or fallback` for a row field.
fn or_data(value: Option<&Data>, fallback: Data) -> Data {
    match value {
        Some(value) if super::pyvalue::truthy(value) => value.clone(),
        _ => fallback,
    }
}

/// `_merge_lm_studio_capacity(discovered, native_models)`.
fn merge_lm_studio_capacity(
    discovered: Vec<Data>,
    native_models: &[Data],
    now: &(dyn Fn() -> String + Sync),
) -> Vec<Data> {
    let instances_of = |row: &Data| -> Vec<Data> {
        match row.get_str("loaded_instances") {
            Some(Data::List(items)) => {
                items.iter().filter(|item| item.as_dict().is_some()).cloned().collect()
            }
            _ => Vec::new(),
        }
    };
    // `{str(row["id"]): row}` and the instance map: the last row for an id wins.
    let by_key = |model_id: &str| native_models.iter().rev().find(|row| id_of(row) == model_id);
    let by_instance = |model_id: &str| {
        native_models.iter().rev().find(|row| {
            instances_of(row)
                .iter()
                .any(|instance| instance.get_str("id").and_then(Data::as_str) == Some(model_id))
        })
    };
    let mut merged = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    for generic in discovered {
        let model_id = id_of(&generic);
        let Some(native) = by_key(&model_id).or_else(|| by_instance(&model_id)) else {
            merged.push(generic);
            seen.push(model_id);
            continue;
        };
        let rows = instances_of(native);
        let exact =
            rows.iter().find(|item| item.get_str("id").and_then(Data::as_str) == Some(model_id.as_str()));
        let selected = exact.or(if rows.len() == 1 { rows.first() } else { None });
        let runtime = selected.and_then(|item| int(item.get_str("context_window")));
        let native_declared = int(native.get_str("declared_context_window"));
        let declared = native_declared.or_else(|| int(get(&generic, "declared_context_window")));
        let declared_source = if native_declared.is_some() {
            "provider_metadata".to_owned()
        } else {
            match get(&generic, "declared_context_source") {
                None => "unknown".to_owned(),
                Some(value) => super::pyvalue::py_str(value),
            }
        };
        let stamp = (declared.is_some() || runtime.is_some()).then(now);
        let capacity = resolve_context_capacity(&CapacityInputs {
            declared_context_window: declared,
            declared_context_source: Some(&declared_source),
            runtime_context_window: runtime,
            runtime_context_source: Some("runtime_reported"),
            observed_at: stamp.as_deref(),
            ..CapacityInputs::default()
        });
        let mut row = generic.clone();
        merge(&mut row, capacity_pairs(&capacity));
        let name = or_data(native.get_str("name"), or_data(get(&generic, "name"), text(&model_id)));
        let tools = match native.get_str("supports_tools") {
            Some(value) if !matches!(value, Data::None) => value.clone(),
            _ => get(&generic, "supports_tools").cloned().unwrap_or(Data::None),
        };
        merge(&mut row, vec![("name", name), ("supports_tools", tools)]);
        merged.push(row);
        seen.push(model_id);
    }
    for native in native_models {
        let model_id = id_of(native);
        if seen.contains(&model_id) {
            continue;
        }
        let rows = instances_of(native);
        let selected = if rows.len() == 1 { rows.first() } else { None };
        let runtime = selected.and_then(|item| int(item.get_str("context_window")));
        let declared = int(native.get_str("declared_context_window"));
        let stamp = now();
        let capacity = resolve_context_capacity(&CapacityInputs {
            declared_context_window: declared,
            declared_context_source: Some("provider_metadata"),
            runtime_context_window: runtime,
            runtime_context_source: Some("runtime_reported"),
            observed_at: Some(&stamp),
            ..CapacityInputs::default()
        });
        let mut pairs =
            vec![("id", text(&model_id)), ("name", or_data(native.get_str("name"), text(&model_id)))];
        pairs.extend(capacity_pairs(&capacity));
        pairs.extend([
            ("max_output_tokens", Data::None),
            ("transport", text("chat_completions")),
            ("supports_tools", native.get_str("supports_tools").cloned().unwrap_or(Data::None)),
            ("supports_streaming", Data::None),
        ]);
        let mut row = Data::Dict(Vec::new());
        merge(&mut row, pairs);
        merged.push(row);
    }
    merged
}

/// `_merge_ollama_capacity(...)`: Ollama's running context (`/api/ps`) and model maximum
/// (`/api/show`) when available.
async fn merge_ollama_capacity(
    client: &Arc<dyn HttpTransport>,
    endpoint: &str,
    headers: &[(String, String)],
    discovered: Vec<Data>,
    now: &(dyn Fn() -> String + Sync),
) -> Vec<Data> {
    let document = match fetch(client, "GET", native_endpoint(endpoint, "/api/ps"), headers, None).await {
        Ok(reply) => response_document(&reply),
        Err(_) => None,
    };
    let running_rows = match document.as_ref().and_then(|document| document.get_str("models")) {
        Some(Data::List(rows)) => rows.clone(),
        _ => Vec::new(),
    };
    // `row.get("model", row.get("name"))`, the last row for a name winning.
    let running_name = |row: &Data| -> Option<String> {
        row.as_dict()?;
        let value = row.get_str("model").or_else(|| row.get_str("name"));
        value.and_then(Data::as_str).map(str::to_owned)
    };
    let mut result = Vec::new();
    for model in discovered {
        let model_id = id_of(&model);
        let active =
            running_rows.iter().rev().find(|row| running_name(row).as_deref() == Some(model_id.as_str()));
        let runtime = active.and_then(|row| int(row.get_str("context_length"))).filter(|value| *value >= 512);
        let mut declared = int(get(&model, "declared_context_window"));
        let mut declared_source = match get(&model, "declared_context_source") {
            None => "unknown".to_owned(),
            Some(value) => super::pyvalue::py_str(value),
        };
        let body = serde_json::to_vec(&serde_json::json!({"model": model_id})).expect("a small body");
        if let Ok(reply) =
            fetch(client, "POST", native_endpoint(endpoint, "/api/show"), headers, Some(body)).await
        {
            if let Some(info) = response_document(&reply).and_then(|show| show.get_str("model_info").cloned())
            {
                if let Some(pairs) = info.as_dict() {
                    let candidates: Vec<i64> = pairs
                        .iter()
                        .filter(|(key, _)| key.as_str().is_some_and(|key| key.ends_with(".context_length")))
                        .filter_map(|(_, value)| int(Some(value)))
                        .filter(|value| *value >= 512)
                        .collect();
                    if let Some(largest) = candidates.iter().max() {
                        declared = Some(*largest);
                        declared_source = "provider_metadata".into();
                    }
                }
            }
        }
        let stamp = (runtime.is_some() || declared.is_some()).then(now);
        let capacity = resolve_context_capacity(&CapacityInputs {
            declared_context_window: declared,
            declared_context_source: Some(&declared_source),
            runtime_context_window: runtime,
            runtime_context_source: Some("runtime_reported"),
            observed_at: stamp.as_deref(),
            ..CapacityInputs::default()
        });
        let mut row = model;
        merge(&mut row, capacity_pairs(&capacity));
        result.push(row);
    }
    result
}

/// `discover_provider_models(config_path, ...)`'s arguments, each as Python receives it.
#[derive(Debug, Clone)]
pub struct Discover {
    pub provider_id: Data,
    pub base_url: Data,
    pub credential: Data,
    pub preset_id: Data,
    pub query: Data,
    pub offset: Data,
    pub limit: Data,
    pub headers: Data,
}

impl Default for Discover {
    /// Python's defaults: `query=""`, `offset=0`, `limit=100`, everything else `None`.
    fn default() -> Self {
        Self {
            provider_id: Data::None,
            base_url: Data::None,
            credential: Data::None,
            preset_id: Data::None,
            query: Data::Str(String::new()),
            offset: Data::Int(0),
            limit: Data::Int(100),
            headers: Data::None,
        }
    }
}

/// `discover_provider_models(config_path, ...)`: query and page one selected provider's catalogue.
pub async fn discover_provider_models(
    sources: ConfigSources<'_>,
    config_path: &Path,
    request: &Discover,
    client_for: &ClientFor,
    now: &(dyn Fn() -> String + Sync),
) -> Result<Data, ConfigFailure> {
    let requested_id = request.provider_id.as_str().unwrap_or("").to_owned();
    let supplied_text = request.credential.as_str().map(|value| pystr::strip(value).to_owned());
    let replacing = supplied_text.as_deref().is_some_and(|value| !value.is_empty())
        || super::pyvalue::truthy(&request.headers);
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
        return Err(config("Use the Claude runtime controls in Accounts; HTTP discovery is unavailable"));
    }
    if preset.id == "chatgpt_subscription" {
        return Err(config("Use the ChatGPT subscription controls to discover models"));
    }
    let base = if super::pyvalue::truthy(&request.base_url) {
        request.base_url.clone()
    } else {
        existing.as_ref().map_or(Data::None, |found| text(&found.base_url))
    };
    let endpoint = base_url(preset, &base)?;
    let mut supplied = supplied_text.unwrap_or_default();
    if supplied.is_empty() {
        if let Some(found) = &existing {
            supplied = found.credential.clone();
        }
    }
    if preset.credential_required && supplied.is_empty() {
        return Err(config(format!("{} requires an API key", preset.name)));
    }
    let mut request_headers = private_headers(&request.headers)?;
    if request_headers.is_empty() {
        if let Some(found) = &existing {
            request_headers = found.headers.clone();
        }
    }
    let offset = match request.offset {
        Data::Int(offset) if offset >= 0 => offset,
        _ => return Err(config("Model discovery offset is invalid")),
    };
    let limit = match request.limit {
        Data::Int(limit) if (1..=PAGE_LIMIT).contains(&limit) => limit,
        _ => return Err(config("Model discovery page size is invalid")),
    };
    let search = request.query.as_str().map(|query| pystr::casefold(pystr::strip(query))).unwrap_or_default();
    if pystr::len(&search) > 256 {
        return Err(config("Model discovery search is too long"));
    }
    // `dict.setdefault`: a header the user set (same spelling) wins.
    let setdefault = |headers: &mut Vec<(String, String)>, name: &str, value: String| {
        if !headers.iter().any(|(existing, _)| existing == name) {
            headers.push((name.to_owned(), value));
        }
    };
    if preset.default_transport == "anthropic_messages" {
        setdefault(&mut request_headers, "x-api-key", supplied.clone());
        setdefault(&mut request_headers, "anthropic-version", "2023-06-01".into());
    } else if !supplied.is_empty() {
        setdefault(&mut request_headers, "Authorization", format!("Bearer {supplied}"));
    }
    let connect_failure = || config(format!("Could not connect to {}", preset.name));
    let client = match client_for(20.0) {
        Ok(client) => client,
        Err(ConfigFailure::Python { class: "ValueError", .. }) => return Err(connect_failure()),
        Err(error) => return Err(error),
    };
    let rows = match model_catalog(&client, &endpoint, preset.id, preset.name, &request_headers).await {
        Ok(rows) => rows,
        Err(Ok(failure)) => return Err(failure),
        Err(Err(Fetch::Http | Fetch::Value)) => return Err(connect_failure()),
    };
    drop(client);

    let mut discovered: Vec<Data> = Vec::new();
    let mut seen: Vec<String> = Vec::new();
    for raw in &rows {
        if raw.as_dict().is_none() {
            continue;
        }
        let Some(model) = discovery_model(raw, preset.id, now) else { continue };
        let model_id = model.get_str("id").and_then(Data::as_str).unwrap_or_default().to_owned();
        if seen.contains(&model_id) {
            continue;
        }
        seen.push(model_id);
        discovered.push(model);
    }

    let mut notice: Option<String> = None;
    if preset.id == "featherless" {
        let native = client_for(20.0)?;
        notice = merge_featherless_capacity(&native, &endpoint, &request_headers, &mut discovered, now).await;
    }
    if matches!(preset.id, "lm_studio" | "ollama") {
        let native = client_for(20.0)?;
        discovered = if preset.id == "lm_studio" {
            let native_models = lm_studio_capacity_models(&native, &endpoint, &request_headers).await;
            merge_lm_studio_capacity(discovered, &native_models, now)
        } else {
            merge_ollama_capacity(&native, &endpoint, &request_headers, discovered, now).await
        };
    }

    // `unique.setdefault(model_id, model)` after the search filter, then the stable sort.
    let mut unique: Vec<(String, Data)> = Vec::new();
    for model in discovered {
        let model_id = id_of(&model);
        let name = super::pyvalue::py_str(model.get_str("name").unwrap_or(&Data::None));
        if !search.is_empty()
            && !pystr::casefold(&model_id).contains(&search)
            && !pystr::casefold(&name).contains(&search)
        {
            continue;
        }
        if !unique.iter().any(|(id, _)| *id == model_id) {
            unique.push((model_id, model));
        }
    }
    let mut models: Vec<(String, String, Data)> = unique
        .into_iter()
        .map(|(model_id, model)| {
            let name = super::pyvalue::py_str(model.get_str("name").unwrap_or(&Data::None));
            (pystr::casefold(&name), model_id, model)
        })
        .collect();
    models.sort_by(|a, b| (&a.0, &a.1).cmp(&(&b.0, &b.1)));
    let total = models.len() as i64;
    let start = usize::try_from(offset).unwrap_or(usize::MAX).min(models.len());
    let end = start.saturating_add(usize::try_from(limit).expect("bounded")).min(models.len());
    let page: Vec<Data> = models.drain(start..end).map(|(_, _, model)| model).collect();
    let shown = page.len() as i64;
    Ok(dict(vec![
        ("models", Data::List(page)),
        ("total", Data::Int(total)),
        ("offset", Data::Int(offset)),
        ("limit", Data::Int(limit)),
        ("has_more", Data::Bool(offset.saturating_add(shown) < total)),
        ("notice", notice.as_deref().map_or(Data::None, text)),
    ]))
}

/// `detect_local_providers(config_path)`: probe only the fixed loopback presets, never the LAN.
pub async fn detect_local_providers(client_for: &ClientFor) -> Result<Data, ConfigFailure> {
    let client = client_for(1.5)?;
    let mut results = Vec::new();
    for preset in presets() {
        if preset.mode != "local" {
            continue;
        }
        let endpoint = preset.base_url;
        let host = urlsplit(endpoint)
            .map_err(|error| ConfigFailure::Python { class: "ValueError", message: error.0 })?
            .hostname();
        if !matches!(host.as_deref(), Some("127.0.0.1" | "localhost" | "::1")) {
            continue;
        }
        let mut reachable = false;
        let mut authentication_required = false;
        let mut model_count = 0i64;
        let mut message = preset.launch_hint.unwrap_or("Start the local provider first.").to_owned();
        if let Ok(reply) = fetch(&client, "GET", format!("{endpoint}/models"), &[], None).await {
            reachable = reply.status < 500;
            authentication_required = matches!(reply.status, 401 | 403);
            if reply.status == 200 {
                if let Ok(document) = json_document(&reply.content) {
                    if let Some(Data::List(data)) = get(&document, "data") {
                        model_count = data.len() as i64;
                        message = format!(
                            "Detected {model_count} model{}.",
                            if model_count != 1 { "s" } else { "" }
                        );
                    }
                }
            }
        }
        results.push(dict(vec![
            ("provider_id", text(preset.id)),
            ("name", text(preset.name)),
            ("base_url", text(endpoint)),
            ("reachable", Data::Bool(reachable)),
            ("authentication_required", Data::Bool(authentication_required)),
            ("model_count", Data::Int(model_count)),
            ("message", text(&message)),
        ]));
    }
    Ok(Data::List(results))
}
