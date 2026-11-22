//! Gemini's GenerateContent API: `provider/_gemini.py` (`GeminiProvider`), over an
//! [`HttpTransport`]. Lane 5, on lane 4's split.
//!
//! Python talks to the endpoint with a plain `httpx.AsyncClient` (no SDK): `json=` bodies, its
//! own client headers, and `aiter_lines()` for the stream ([`crate::httpx_text`]). Requests are
//! sent once; no hook sees an error, as in Python.
//!
//! Python's exceptions keep their classes here. Inside the request's `try`, a `ValueError`,
//! `TypeError` or `KeyError` becomes `InvalidResponseError("Gemini returned malformed ...")`;
//! anything else (an `AttributeError` from a document of the wrong shape, an error while the
//! request is built) escapes as it is, which is Rust's `Internal`.
//!
//! Where this differs from Python (differences row 43, Gemini): the stream chunk's `content`
//! and `reasoning_content` are text, so Python's `None` there is `""`; stored `gemini_parts`
//! must be a list; httpx's per-operation timeouts are the provider's own per-step timeouts; and
//! `alt=sse` is appended to a base URL's query rather than merged into it.

use crate::chat::ChatError;
use crate::error::{ErrorKind, ProviderError, ProviderResult};
use crate::hooks::{HookContext, HookManager};
use crate::http::{HttpRequest, HttpResponse, HttpTransport, TransportError};
use crate::httpx_text::{quote, LineDecoder, TextDecoder};
use crate::httpx_url::HttpxUrl;
use crate::provider::{BoxFuture, ChunkStream, Provider, ProviderRequest};
use crate::pyobj::{py_eq, py_repr_str, py_str, truthy, type_name, Incidental};
use crate::types::{ModelInfo, ProviderResponse, ProviderStreamChunk, PyValue, TokenUsage, ToolCall};
use futures_util::StreamExt;
use std::collections::VecDeque;
use std::sync::Arc;
use std::time::Duration;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};

/// The refusal for stored `gemini_parts` that are not a list (differences row 43).
pub const NON_LIST_PARTS: &str = "Gemini parts are not a list";

/// The message of `ProviderContinuationRequiredError` for a `CONTINUATION` finish.
pub const CONTINUATION_MESSAGE: &str = "Gemini reached its per-request limit and requires a token continuation with the identical original request. Automatic native continuation is not supported; saved work is preserved. Choose a smaller step or another model.";

const TOKEN_LIMIT_WORDS: [&str; 4] = ["context", "token limit", "input token", "too long"];

/// A source of `uuid4()` text for calls the provider returned without an id.
pub type CallIds = Arc<dyn Fn() -> String + Send + Sync>;

/// A random version 4 UUID, as `str(uuid.uuid4())` writes it.
pub fn uuid4() -> String {
    let mut bytes = [0u8; 16];
    getrandom::getrandom(&mut bytes).expect("the system random source");
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    let hex: String = bytes.iter().map(|byte| format!("{byte:02x}")).collect();
    format!("{}-{}-{}-{}-{}", &hex[..8], &hex[8..12], &hex[12..16], &hex[16..20], &hex[20..])
}

// ── Python's view of the JSON ──────────────────────────────────────────────────────────────

fn object(pairs: Vec<(&str, Json)>) -> Json {
    Json::Object(pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect())
}

/// `value.get(key)` on a dict, or `AttributeError`.
fn dict_get<'a>(value: &'a Json, key: &str) -> Result<Option<&'a Json>, Incidental> {
    match value {
        Json::Object(map) => Ok(map.get(key)),
        other => Err(Incidental::no_attribute(other, "get")),
    }
}

/// `value[key]` with a string key: `KeyError`, or the `TypeError` of a value that is not a
/// dict.
fn subscript<'a>(value: &'a Json, key: &str) -> Result<&'a Json, Incidental> {
    match value {
        Json::Object(map) => map.get(key).ok_or_else(|| Incidental(py_repr_str(key))),
        Json::String(_) => Err(Incidental("string indices must be integers, not 'str'".into())),
        Json::Array(_) => Err(Incidental("list indices must be integers or slices, not str".into())),
        other => Err(Incidental(format!("'{}' object is not subscriptable", type_name(other)))),
    }
}

/// `key in container` for a string key.
fn contains(container: &Json, key: &str) -> Result<bool, Incidental> {
    match container {
        Json::Object(map) => Ok(map.contains_key(key)),
        Json::String(text) => Ok(text.contains(key)),
        Json::Array(items) => Ok(items.iter().any(|item| item.as_str() == Some(key))),
        other => Err(Incidental(format!("argument of type '{}' is not iterable", type_name(other)))),
    }
}

/// A dict key or set member: lists and dicts are unhashable.
fn hashable(value: &Json) -> Result<(), Incidental> {
    match value {
        Json::Array(_) => Err(Incidental("unhashable type: 'list'".into())),
        Json::Object(_) => Err(Incidental("unhashable type: 'dict'".into())),
        _ => Ok(()),
    }
}

/// `value or default`.
fn or<'a>(value: Option<&'a Json>, default: &'a Json) -> &'a Json {
    value.filter(|value| truthy(value)).unwrap_or(default)
}

/// Iterate `value or []` as a `for` loop does.
fn iterate(value: Option<&Json>) -> Result<Vec<Json>, Incidental> {
    crate::pyobj::py_iter(value.unwrap_or(&Json::Null))
}

// ── The request ───────────────────────────────────────────────────────────────────────────

/// `provider_request_representation(messages, tools, options)`: the GenerateContent body.
/// Python's `InvalidRequestError` for a tool result without its call, or an accidental
/// exception.
pub fn request_representation(
    messages: &[Json],
    tools: Option<&[Json]>,
    options: &JsonMap,
) -> Result<JsonMap, ChatError> {
    let empty = Json::String(String::new());
    let mut contents: Vec<(&'static str, Vec<Json>)> = Vec::new();
    let mut system = Vec::new();
    // `calls`: the neutral call id, the native name and the native id.
    let mut calls: Vec<(Json, Json, Json)> = Vec::new();
    for message in messages {
        let role = dict_get(message, "role")?.cloned().unwrap_or(Json::Null);
        hashable(&role)?;
        let role_text = role.as_str();
        if matches!(role_text, Some("system" | "developer")) {
            let content = or(dict_get(message, "content")?, &empty);
            system.push(object(vec![("text", Json::String(py_str(content)))]));
            continue;
        }
        let parts: Vec<Json> = if role_text == Some("tool") {
            let identity = dict_get(message, "tool_call_id")?.cloned().unwrap_or(Json::Null);
            hashable(&identity)?;
            let Some((_, name, native_id)) = calls.iter().find(|(key, _, _)| py_eq(key, &identity)) else {
                return Err(ChatError::Provider(ProviderError::new(
                    ErrorKind::InvalidRequest,
                    "Gemini tool result has no matching function call.",
                )));
            };
            let content = dict_get(message, "content")?;
            let result = match content.filter(|content| truthy(content)) {
                None => Json::Object(JsonMap::new()),
                Some(Json::String(text)) => pyjson::loads(text)
                    .unwrap_or_else(|_| object(vec![("result", Json::String(text.clone()))])),
                Some(other) => object(vec![("result", Json::String(py_str(other)))]),
            };
            let result = if result.is_object() { result } else { object(vec![("result", result)]) };
            let mut response = JsonMap::new();
            response.insert("name".into(), name.clone());
            response.insert("response".into(), result);
            if truthy(native_id) {
                response.insert("id".into(), native_id.clone());
            }
            vec![object(vec![("functionResponse", Json::Object(response))])]
        } else {
            let neutral = iterate(dict_get(message, "tool_calls")?)?;
            let mut parts = match dict_get(message, "gemini_parts")? {
                None | Some(Json::Null) => None,
                Some(Json::Array(items)) => Some(items.clone()),
                Some(_) => return Err(Incidental(NON_LIST_PARTS.into()).into()),
            };
            if parts.is_none() {
                let mut built = Vec::new();
                if dict_get(message, "content")?.is_some_and(truthy) {
                    built.push(object(vec![("text", Json::String(py_str(subscript(message, "content")?)))]));
                }
                for call in &neutral {
                    let function = subscript(call, "function")?;
                    let arguments = dict_get(function, "arguments")?
                        .cloned()
                        .unwrap_or_else(|| Json::String("{}".into()));
                    let arguments = match &arguments {
                        Json::String(text) => {
                            pyjson::loads(text).map_err(|error| Incidental(error.message))?
                        }
                        _ => arguments,
                    };
                    let id = subscript(call, "id")?.clone();
                    let name = subscript(function, "name")?.clone();
                    built.push(object(vec![(
                        "functionCall",
                        object(vec![("id", id), ("name", name), ("args", arguments)]),
                    )]));
                }
                parts = Some(built);
            }
            let parts = parts.unwrap_or_default();
            let mut native_calls = Vec::new();
            for part in &parts {
                if contains(part, "functionCall")? {
                    native_calls.push(subscript(part, "functionCall")?.clone());
                }
            }
            for (call, native) in neutral.iter().zip(&native_calls) {
                let name = subscript(native, "name")?.clone();
                let native_id = dict_get(native, "id")?.cloned().unwrap_or(Json::Null);
                let key = subscript(call, "id")?.clone();
                hashable(&key)?;
                match calls.iter_mut().find(|(existing, _, _)| py_eq(existing, &key)) {
                    Some(slot) => *slot = (slot.0.clone(), name, native_id),
                    None => calls.push((key, name, native_id)),
                }
            }
            // `zip(..., strict=True)`.
            if neutral.len() != native_calls.len() {
                let side = if neutral.len() < native_calls.len() { "longer" } else { "shorter" };
                return Err(Incidental(format!("zip() argument 2 is {side} than argument 1")).into());
            }
            parts
        };
        let wire_role = if role_text == Some("assistant") { "model" } else { "user" };
        if parts.is_empty() {
            continue;
        }
        match contents.last_mut() {
            Some((last_role, last_parts)) if *last_role == wire_role => last_parts.extend(parts),
            _ => contents.push((wire_role, parts)),
        }
    }
    let mut request = JsonMap::new();
    request.insert(
        "contents".into(),
        Json::Array(
            contents
                .into_iter()
                .map(|(role, parts)| {
                    object(vec![("role", Json::String(role.into())), ("parts", Json::Array(parts))])
                })
                .collect(),
        ),
    );
    if !system.is_empty() {
        request.insert("systemInstruction".into(), object(vec![("parts", Json::Array(system))]));
    }
    if let Some(tools) = tools.filter(|tools| !tools.is_empty()) {
        let mut declarations = Vec::new();
        for tool in tools {
            let function = subscript(tool, "function")?;
            let name = subscript(function, "name")?.clone();
            let description = dict_get(function, "description")?.cloned().unwrap_or_else(|| empty.clone());
            let parameters = dict_get(function, "parameters")?
                .cloned()
                .unwrap_or_else(|| object(vec![("type", Json::String("object".into()))]));
            declarations.push(object(vec![
                ("name", name),
                ("description", description),
                ("parametersJsonSchema", parameters),
            ]));
        }
        request.insert(
            "tools".into(),
            Json::Array(vec![object(vec![("functionDeclarations", Json::Array(declarations))])]),
        );
        let mode = |mode: &str| object(vec![("mode", Json::String(mode.into()))]);
        match options.get("tool_choice") {
            Some(Json::String(choice)) => {
                let named = match choice.as_str() {
                    "required" => Some("ANY"),
                    "none" => Some("NONE"),
                    "auto" => Some("AUTO"),
                    _ => None,
                };
                if let Some(named) = named {
                    request.insert("toolConfig".into(), object(vec![("functionCallingConfig", mode(named))]));
                }
            }
            Some(choice @ Json::Object(_)) => {
                let empty_dict = Json::Object(JsonMap::new());
                let function = dict_get(choice, "function")?.unwrap_or(&empty_dict);
                if let Some(name) = dict_get(function, "name")?.filter(|name| truthy(name)) {
                    let config = object(vec![
                        ("mode", Json::String("ANY".into())),
                        ("allowedFunctionNames", Json::Array(vec![name.clone()])),
                    ]);
                    request.insert("toolConfig".into(), object(vec![("functionCallingConfig", config)]));
                }
            }
            _ => {}
        }
    }
    let mut config = JsonMap::new();
    for (source, target) in [
        ("max_tokens", "maxOutputTokens"),
        ("max_output_tokens", "maxOutputTokens"),
        ("temperature", "temperature"),
        ("top_p", "topP"),
        ("top_k", "topK"),
        ("stop", "stopSequences"),
    ] {
        if let Some(value) = options.get(source).filter(|value| !value.is_null()) {
            config.insert(target.into(), value.clone());
        }
    }
    if !config.is_empty() {
        request.insert("generationConfig".into(), Json::Object(config));
    }
    Ok(request)
}

// ── The response ──────────────────────────────────────────────────────────────────────────

/// A failure inside the request's `try`.
enum Fault {
    /// A provider error, raised as it is.
    Provider(ProviderError),
    /// `ValueError`, `TypeError` or `KeyError`: `InvalidResponseError("Gemini returned
    /// malformed ...")`.
    Malformed,
    /// Any other exception, which escapes as it is.
    Raw(String),
}

impl From<ProviderError> for Fault {
    fn from(error: ProviderError) -> Self {
        Self::Provider(error)
    }
}

fn invalid_response(message: &str) -> Fault {
    Fault::Provider(ProviderError::new(ErrorKind::InvalidResponse, message))
}

/// `AttributeError`, which escapes.
fn raw(error: Incidental) -> Fault {
    Fault::Raw(error.0)
}

/// What `_parse` returns.
struct Parsed {
    text: String,
    reasoning: String,
    calls: Vec<ToolCall>,
    parts: Vec<Json>,
    finish: Option<&'static str>,
}

/// `"".join(part.get("text", "") for part in parts if <filter>)`.
fn joined(parts: &[Json], thought: bool) -> Result<String, Fault> {
    let mut out = String::new();
    for part in parts {
        if part.get("thought").is_some_and(truthy) != thought {
            continue;
        }
        match part.get("text") {
            None => {}
            Some(Json::String(text)) => out.push_str(text),
            // `str.join` on a non-string: `TypeError`.
            Some(_) => return Err(Fault::Malformed),
        }
    }
    Ok(out)
}

/// `GeminiProvider._parse(document)`.
fn parse(document: &Json, call_ids: &CallIds) -> Result<Parsed, Fault> {
    let candidates = match document {
        Json::Object(map) => map.get("candidates"),
        _ => None,
    };
    let Some(Json::Array(candidates)) = candidates.filter(|candidates| truthy(candidates)) else {
        return Err(invalid_response("Gemini returned no usable candidate."));
    };
    let candidate = &candidates[0];
    let empty_dict = Json::Object(JsonMap::new());
    let content = dict_get(candidate, "content").map_err(raw)?.unwrap_or(&empty_dict);
    let empty_list = Json::Array(Vec::new());
    let parts = dict_get(content, "parts").map_err(raw)?.unwrap_or(&empty_list);
    let parts = match parts {
        Json::Array(items) if items.iter().all(Json::is_object) => items.clone(),
        _ => return Err(invalid_response("Gemini returned invalid content parts.")),
    };
    let text = joined(&parts, false)?;
    let reasoning = joined(&parts, true)?;
    let mut calls = Vec::new();
    for part in &parts {
        let Some(function) = part.get("functionCall").filter(|function| !function.is_null()) else {
            continue;
        };
        let args = function.get("args");
        let valid = function.is_object()
            && matches!(function.get("name"), Some(Json::String(_)))
            && args.is_none_or(Json::is_object);
        if !valid {
            return Err(invalid_response("Gemini returned invalid function arguments."));
        }
        let id = match function.get("id").filter(|id| truthy(id)) {
            Some(id) => PyValue::from_json(id.clone()),
            None => PyValue::Text(format!("gemini-{}", call_ids())),
        };
        let arguments = match args {
            Some(args) => pyjson::dumps(args, Dump::DEFAULT),
            None => "{}".to_owned(),
        };
        calls.push(ToolCall {
            id,
            name: PyValue::from_json(function["name"].clone()),
            arguments: PyValue::Text(arguments),
        });
    }
    let reason = candidate.get("finishReason").cloned().unwrap_or(Json::Null);
    if reason.as_str() == Some("CONTINUATION") {
        return match candidate.get("continuationToken") {
            Some(Json::String(token)) if !token.is_empty() => Err(Fault::Provider(
                ProviderError::continuation_required(CONTINUATION_MESSAGE, token.clone(), Json::Array(parts)),
            )),
            _ => Err(invalid_response("Gemini continuation is missing its required token.")),
        };
    }
    if !truthy(&reason) {
        return Ok(Parsed { text, reasoning, calls, parts, finish: None });
    }
    if hashable(&reason).is_err() {
        return Err(Fault::Malformed);
    }
    let finish = match reason.as_str() {
        Some("MAX_TOKENS") => "length",
        Some("STOP") if !calls.is_empty() => "tool_calls",
        Some("STOP") => "stop",
        _ => {
            return Err(invalid_response(&format!(
                "Gemini did not complete a usable response ({}).",
                py_str(&reason)
            )))
        }
    };
    Ok(Parsed { text, reasoning, calls, parts, finish: Some(finish) })
}

/// `usage.get("promptTokenCount", 0)` and `usage.get("candidatesTokenCount", 0)`, as
/// `TokenUsage(prompt_tokens=..., completion_tokens=...)` (its `total_tokens` stays 0).
fn usage_of(usage: &Json) -> Result<TokenUsage, Fault> {
    let zero = Json::from(0);
    let prompt = dict_get(usage, "promptTokenCount").map_err(raw)?.unwrap_or(&zero).clone();
    let completion = dict_get(usage, "candidatesTokenCount").map_err(raw)?.unwrap_or(&zero).clone();
    Ok(TokenUsage {
        prompt_tokens: PyValue::from_json(prompt),
        completion_tokens: PyValue::from_json(completion),
        total_tokens: PyValue::int(0),
        reasoning_tokens: None,
    })
}

fn metadata(parts: &[Json], calls: &[ToolCall]) -> JsonMap {
    let mut metadata = JsonMap::new();
    metadata.insert("gemini_parts".into(), Json::Array(parts.to_vec()));
    metadata
        .insert("gemini_call_ids".into(), Json::Array(calls.iter().map(|call| call.id.to_json()).collect()));
    metadata
}

/// `GeminiProvider._check(response)` for a status that is not a success. The body is read
/// only for the statuses that look at it.
async fn status_error(response: &mut HttpResponse, read: Duration) -> Result<ProviderError, TransportError> {
    let status = response.status;
    if status == 401 || status == 403 {
        return Ok(ProviderError::new(ErrorKind::Authentication, "Gemini rejected the supplied credential."));
    }
    if status == 429 {
        let retry = crate::retry::python_float(response.header("retry-after").unwrap_or(""));
        return Ok(ProviderError::rate_limit("Gemini rate or quota limit reached.", retry));
    }
    if status == 404 {
        return Ok(ProviderError::new(ErrorKind::ModelNotFound, "Gemini model or endpoint was not found."));
    }
    if status >= 500 {
        return Ok(ProviderError::new(
            ErrorKind::Unavailable,
            format!("Gemini unavailable (HTTP {status})."),
        ));
    }
    let body = read_body(response, read).await?;
    let text = sylvia_base::pystr::casefold(&String::from_utf8_lossy(&body));
    if (status == 400 || status == 413) && TOKEN_LIMIT_WORDS.iter().any(|word| text.contains(word)) {
        return Ok(ProviderError::token_limit_rejection());
    }
    Ok(ProviderError::new(ErrorKind::InvalidRequest, format!("Gemini rejected the request (HTTP {status}).")))
}

/// One read of the body within httpx's read timeout.
async fn next_piece(response: &mut HttpResponse, read: Duration) -> Option<Result<Vec<u8>, TransportError>> {
    match tokio::time::timeout(read, response.body.next_piece()).await {
        Ok(piece) => piece,
        Err(_) => Some(Err(TransportError::Timeout("read timed out".into()))),
    }
}

async fn read_body(response: &mut HttpResponse, read: Duration) -> Result<Vec<u8>, TransportError> {
    let mut body = Vec::new();
    while let Some(piece) = next_piece(response, read).await {
        body.extend(piece?);
    }
    Ok(body)
}

// ── The provider ──────────────────────────────────────────────────────────────────────────

/// The provider for one Gemini GenerateContent model, direct or through a compatible gateway.
pub struct GeminiProvider {
    base_url: String,
    model_info: ModelInfo,
    api_key: String,
    default_headers: Vec<(String, String)>,
    /// `is_opencode_inference(base_url)`, which Python evaluates on each request.
    opencode: Result<bool, String>,
    transport: Arc<dyn HttpTransport>,
    call_ids: CallIds,
    /// `provider.hooks`.
    pub hooks: HookManager,
}

/// What the provider hands the transport, and what the hooks see.
struct Prepared {
    url: String,
    headers: Vec<(String, Json)>,
    request: JsonMap,
}

fn set<V>(headers: &mut Vec<(String, V)>, name: &str, value: V) {
    match headers.iter_mut().find(|(existing, _)| existing == name) {
        Some(slot) => slot.1 = value,
        None => headers.push((name.to_owned(), value)),
    }
}

impl GeminiProvider {
    /// `GeminiProvider(base_url=..., model_info=..., api_key=..., default_headers=...)` over
    /// `transport`.
    pub fn new(
        base_url: &str,
        api_key: &str,
        model_info: ModelInfo,
        default_headers: Vec<(String, String)>,
        transport: Arc<dyn HttpTransport>,
    ) -> Self {
        let base_url = base_url.trim_end_matches('/').to_owned();
        let mut headers = Vec::new();
        for (name, value) in default_headers {
            set(&mut headers, &name, value);
        }
        Self {
            opencode: crate::routes::is_opencode_inference(&base_url).map_err(|error| error.0),
            base_url,
            model_info,
            api_key: api_key.to_owned(),
            default_headers: headers,
            transport,
            call_ids: Arc::new(uuid4),
            hooks: HookManager::new(),
        }
    }

    /// The same provider naming calls without an id from `call_ids` instead of `uuid4()`.
    #[must_use]
    pub fn with_call_ids(mut self, call_ids: CallIds) -> Self {
        self.call_ids = call_ids;
        self
    }

    /// `_base_url` (without trailing slashes).
    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    /// `model_info`.
    pub fn model(&self) -> &ModelInfo {
        &self.model_info
    }

    /// `_headers`, in their configured order.
    pub fn default_headers(&self) -> &[(String, String)] {
        &self.default_headers
    }

    /// `provider_request_representation(messages, tools, options)`.
    pub fn provider_request_representation(
        &self,
        messages: &[Json],
        tools: Option<&[Json]>,
        options: &JsonMap,
    ) -> ProviderResult<JsonMap> {
        request_representation(messages, tools, options).map_err(escaped)
    }

    /// `_request(...)`: the headers, the endpoint and the body, then `on_request`. Every failure
    /// here escapes as it is, before any hook.
    async fn prepare(&self, request: &ProviderRequest, stream: bool) -> ProviderResult<Prepared> {
        let internal = |Incidental(message)| ProviderError::new(ErrorKind::Internal, message);
        let mut headers: Vec<(String, Json)> = self
            .default_headers
            .iter()
            .map(|(name, value)| (name.clone(), Json::String(value.clone())))
            .collect();
        match request.options.get("extra_headers") {
            None => {}
            Some(value) if !truthy(value) => {}
            Some(Json::Object(extra)) => {
                for (name, value) in extra {
                    set(&mut headers, name, value.clone());
                }
            }
            Some(other) => {
                return Err(internal(Incidental(format!("'{}' object is not a mapping", type_name(other)))))
            }
        }
        if self.opencode.clone().map_err(|message| internal(Incidental(message)))? {
            headers.retain(|(name, _)| name.to_lowercase() != "x-goog-api-key");
            set(&mut headers, "Authorization", Json::String(format!("Bearer {}", self.api_key)));
        } else if !headers.iter().any(|(name, _)| name == "x-goog-api-key") {
            headers.push(("x-goog-api-key".into(), Json::String(self.api_key.clone())));
        }
        if !headers.iter().any(|(name, _)| name == "Content-Type") {
            headers.push(("Content-Type".into(), Json::String("application/json".into())));
        }
        let method = if stream { "streamGenerateContent" } else { "generateContent" };
        let wire = self.model_info.wire_model_id();
        let model = quote(wire.strip_prefix("models/").unwrap_or(wire));
        let mut url = format!("{}/models/{model}:{method}", self.base_url);
        if stream {
            url.push_str(if url.contains('?') { "&alt=sse" } else { "?alt=sse" });
        }
        let body = self.provider_request_representation(
            &request.messages,
            request.tools.as_deref(),
            &request.options,
        )?;
        let context = HookContext {
            provider_key: self.model_info.provider_key.clone(),
            model_id: self.model_info.id.clone(),
            stream,
            request_json: Some(body.clone()),
            ..HookContext::default()
        };
        self.hooks.fire_on_request(&context).await;
        Ok(Prepared { url, headers, request: body })
    }

    /// The request httpx builds: the client's `User-Agent` (Sylvia's, differences row 8) unless
    /// a header names one, the headers, and the `json=` body with its `Content-Length`. A header
    /// value that is not text is httpx's `TypeError`, inside the `try`.
    fn http_request(prepared: &Prepared) -> Result<HttpRequest, Fault> {
        let url = HttpxUrl::parse(&prepared.url).map_err(|error| Fault::Raw(error.0))?.to_string();
        let mut headers: Vec<(String, String)> =
            if prepared.headers.iter().any(|(name, _)| name.eq_ignore_ascii_case("user-agent")) {
                Vec::new()
            } else {
                vec![("User-Agent".into(), crate::openai_compat::sylvia_user_agent())]
            };
        for (name, value) in &prepared.headers {
            match value {
                Json::String(text) => headers.push((name.clone(), text.clone())),
                _ => return Err(Fault::Malformed),
            }
        }
        let body = pyjson::dump_bytes(&Json::Object(prepared.request.clone()), Dump::WIRE.ascii(false));
        crate::net::json_content_headers(&mut headers, body.len());
        Ok(HttpRequest { method: "POST".into(), url, headers, body })
    }

    fn response_context(&self, response: ProviderResponse, status: u16, stream: bool) -> HookContext {
        HookContext {
            provider_key: self.model_info.provider_key.clone(),
            model_id: self.model_info.id.clone(),
            stream,
            response: Some(response),
            http_status: Some(status),
            ..HookContext::default()
        }
    }

    async fn generate_once(&self, request: ProviderRequest) -> ProviderResult<ProviderResponse> {
        if request.stream {
            return self.generate_from_stream(request).await;
        }
        let prepared = self.prepare(&request, false).await?;
        let wait = Duration::from_secs_f64(request.timeout.max(0.0));
        let outcome = self.call(&prepared, wait).await;
        let malformed =
            || ProviderError::new(ErrorKind::InvalidResponse, "Gemini returned malformed response data.");
        match outcome {
            Ok(result) => Ok(result),
            Err(CallError::Transport(TransportError::Timeout(_))) => {
                Err(ProviderError::new(ErrorKind::Timeout, "Gemini request timed out."))
            }
            Err(CallError::Transport(TransportError::Connection(_))) => {
                Err(ProviderError::connection("Gemini connection failed.", None))
            }
            Err(CallError::Fault(Fault::Provider(error))) => Err(error),
            Err(CallError::Fault(Fault::Malformed)) => Err(malformed()),
            Err(CallError::Fault(Fault::Raw(message))) => {
                Err(ProviderError::new(ErrorKind::Internal, message))
            }
        }
    }

    /// The `try` of `generate`.
    async fn call(&self, prepared: &Prepared, wait: Duration) -> Result<ProviderResponse, CallError> {
        let http = Self::http_request(prepared)?;
        let mut response = tokio::time::timeout(wait, self.transport.send(http))
            .await
            .unwrap_or_else(|_| Err(TransportError::Timeout("timed out".into())))?;
        if !(200..300).contains(&response.status) {
            return Err(CallError::Fault(Fault::Provider(status_error(&mut response, wait).await?)));
        }
        let body = read_body(&mut response, wait).await?;
        let document = pyjson::loads_bytes(&body).map_err(|_| Fault::Malformed)?;
        let parsed = parse(&document, &self.call_ids)?;
        let Some(finish) = parsed.finish else {
            return Err(invalid_response("Gemini response has no completion status.").into());
        };
        let empty = Json::Object(JsonMap::new());
        let usage = usage_of(document.get("usageMetadata").unwrap_or(&empty))?;
        let result = ProviderResponse {
            content: Some(parsed.text),
            reasoning_content: Some(parsed.reasoning),
            chat_completion_metadata: Some(metadata(&parsed.parts, &parsed.calls)),
            tool_calls: parsed.calls,
            model: self.model_info.wire_model_id().to_owned(),
            finish_reason: PyValue::from(finish),
            usage,
            ..ProviderResponse::default()
        };
        self.hooks.fire_on_response(&self.response_context(result.clone(), response.status, false)).await;
        Ok(result)
    }

    /// `generate(stream=True)`: the stream's chunks gathered into one response.
    async fn generate_from_stream(&self, request: ProviderRequest) -> ProviderResult<ProviderResponse> {
        let mut chunks = Vec::new();
        let mut stream = self.generate_stream(request);
        while let Some(chunk) = stream.next().await {
            chunks.push(chunk?);
        }
        let last = chunks.last().cloned().unwrap_or_default();
        let tool_calls = chunks
            .iter()
            .filter_map(|chunk| chunk.tool_call_delta.as_ref())
            .map(|delta| ToolCall {
                id: PyValue::from_json(delta["id"].clone()),
                name: PyValue::from_json(delta["function"]["name"].clone()),
                arguments: PyValue::from_json(delta["function"]["arguments"].clone()),
            })
            .collect();
        Ok(ProviderResponse {
            content: Some(chunks.iter().map(|chunk| chunk.content.as_str()).collect()),
            reasoning_content: Some(chunks.iter().map(|chunk| chunk.reasoning_content.as_str()).collect()),
            tool_calls,
            finish_reason: last.finish_reason.unwrap_or_else(PyValue::none),
            model: self.model_info.wire_model_id().to_owned(),
            chat_completion_metadata: last.chat_completion_metadata,
            ..ProviderResponse::default()
        })
    }
}

/// A failure inside `generate`'s `try`.
enum CallError {
    Transport(TransportError),
    Fault(Fault),
}

impl From<TransportError> for CallError {
    fn from(error: TransportError) -> Self {
        Self::Transport(error)
    }
}

impl From<Fault> for CallError {
    fn from(fault: Fault) -> Self {
        Self::Fault(fault)
    }
}

/// An exception Python lets escape as it is: provider errors unchanged, the rest `Internal`.
fn escaped(error: ChatError) -> ProviderError {
    match error {
        ChatError::Provider(error) => error,
        ChatError::Incidental(Incidental(message)) => ProviderError::new(ErrorKind::Internal, message),
    }
}

enum Phase {
    NotStarted,
    Reading,
    Finished,
}

/// One `generate_stream` call, pulled chunk by chunk.
struct StreamRun<'a> {
    provider: &'a GeminiProvider,
    request: ProviderRequest,
    phase: Phase,
    response: Option<HttpResponse>,
    status: u16,
    text: Option<TextDecoder>,
    lines: LineDecoder,
    data: Vec<String>,
    parts: Vec<Json>,
    calls: Vec<ToolCall>,
    finish: Option<&'static str>,
    text_parts: String,
    reasoning_parts: String,
    usage: Json,
    out: VecDeque<ProviderStreamChunk>,
    failure: Option<ProviderError>,
}

impl<'a> StreamRun<'a> {
    fn new(provider: &'a GeminiProvider, request: ProviderRequest) -> Self {
        Self {
            provider,
            request,
            phase: Phase::NotStarted,
            response: None,
            status: 200,
            text: None,
            lines: LineDecoder::default(),
            data: Vec::new(),
            parts: Vec::new(),
            calls: Vec::new(),
            finish: None,
            text_parts: String::new(),
            reasoning_parts: String::new(),
            usage: Json::Object(JsonMap::new()),
            out: VecDeque::new(),
            failure: None,
        }
    }

    fn wait(&self) -> Duration {
        Duration::from_secs_f64(self.request.timeout.max(0.0))
    }

    /// The stream's `except` clauses.
    fn fail(&mut self, error: CallError) {
        let error = match error {
            CallError::Transport(TransportError::Timeout(_)) => {
                ProviderError::new(ErrorKind::Timeout, "Gemini stream timed out.")
            }
            CallError::Transport(TransportError::Connection(_)) => {
                ProviderError::connection("Gemini stream connection failed.", None)
            }
            CallError::Fault(Fault::Provider(error)) => error,
            CallError::Fault(Fault::Malformed) => {
                ProviderError::new(ErrorKind::InvalidResponse, "Gemini returned malformed stream data.")
            }
            CallError::Fault(Fault::Raw(message)) => ProviderError::new(ErrorKind::Internal, message),
        };
        self.response = None;
        self.phase = Phase::Finished;
        self.failure = Some(error);
    }

    async fn next(&mut self) -> Option<ProviderResult<ProviderStreamChunk>> {
        loop {
            if let Some(chunk) = self.out.pop_front() {
                return Some(Ok(chunk));
            }
            if let Some(error) = self.failure.take() {
                return Some(Err(error));
            }
            match self.phase {
                Phase::NotStarted => match self.provider.prepare(&self.request, true).await {
                    Err(error) => {
                        self.phase = Phase::Finished;
                        return Some(Err(error));
                    }
                    Ok(prepared) => {
                        self.phase = Phase::Reading;
                        if let Err(error) = self.open(&prepared).await {
                            self.fail(error);
                        }
                    }
                },
                Phase::Reading => {
                    if let Err(error) = self.read_step().await {
                        self.fail(error);
                    }
                }
                Phase::Finished => return None,
            }
        }
    }

    async fn open(&mut self, prepared: &Prepared) -> Result<(), CallError> {
        let http = GeminiProvider::http_request(prepared)?;
        let wait = self.wait();
        let mut response = tokio::time::timeout(wait, self.provider.transport.send(http))
            .await
            .unwrap_or_else(|_| Err(TransportError::Timeout("timed out".into())))?;
        if !(200..300).contains(&response.status) {
            return Err(Fault::Provider(status_error(&mut response, wait).await?).into());
        }
        self.status = response.status;
        self.text = Some(TextDecoder::for_content_type(response.header("content-type")));
        self.response = Some(response);
        Ok(())
    }

    async fn read_step(&mut self) -> Result<(), CallError> {
        let wait = self.wait();
        let Some(response) = self.response.as_mut() else { return self.complete().await };
        let decoder = self.text.as_mut().expect("a text decoder for an open stream");
        let lines = match next_piece(response, wait).await {
            Some(piece) => {
                let text = decoder.decode(&piece?, false);
                if text.is_empty() {
                    Vec::new()
                } else {
                    self.lines.decode(&text)
                }
            }
            None => {
                let text = decoder.decode(&[], true);
                let mut lines = if text.is_empty() { Vec::new() } else { self.lines.decode(&text) };
                lines.extend(self.lines.flush());
                self.response = None;
                lines
            }
        };
        for line in lines {
            self.line(&line).await?;
        }
        Ok(())
    }

    async fn line(&mut self, line: &str) -> Result<(), CallError> {
        if let Some(rest) = line.strip_prefix("data:") {
            self.data.push(sylvia_base::pystr::strip(rest).to_owned());
            return Ok(());
        }
        if !line.is_empty() || self.data.is_empty() {
            return Ok(());
        }
        let document = pyjson::loads(&self.data.join("\n")).map_err(|_| Fault::Malformed)?;
        self.data.clear();
        let parsed = parse(&document, &self.provider.call_ids)?;
        self.parts.extend(parsed.parts);
        self.text_parts.push_str(&parsed.text);
        self.reasoning_parts.push_str(&parsed.reasoning);
        if let Some(usage) = document.get("usageMetadata") {
            self.usage = usage.clone();
        }
        if !parsed.text.is_empty() || !parsed.reasoning.is_empty() {
            self.out.push_back(ProviderStreamChunk {
                content: parsed.text,
                reasoning_content: parsed.reasoning,
                ..ProviderStreamChunk::default()
            });
        }
        for call in parsed.calls {
            let index = self.calls.len();
            let mut function = JsonMap::new();
            function.insert("name".into(), call.name.to_json());
            function.insert("arguments".into(), call.arguments.to_json());
            let mut delta = JsonMap::new();
            delta.insert("index".into(), Json::from(index));
            delta.insert("id".into(), call.id.to_json());
            delta.insert("type".into(), Json::String("function".into()));
            delta.insert("function".into(), Json::Object(function));
            self.calls.push(call);
            self.out.push_back(ProviderStreamChunk {
                tool_call_delta: Some(Json::Object(delta)),
                ..ProviderStreamChunk::default()
            });
        }
        if let Some(reason) = parsed.finish {
            self.finish = Some(if reason == "length" {
                "length"
            } else if !self.calls.is_empty() {
                "tool_calls"
            } else {
                "stop"
            });
        }
        Ok(())
    }

    /// After the last line: the terminal check, `on_response`, then the final chunk.
    async fn complete(&mut self) -> Result<(), CallError> {
        self.phase = Phase::Finished;
        let finish = match self.finish {
            Some(finish) if self.data.is_empty() => finish,
            _ => {
                return Err(Fault::Provider(ProviderError::new(
                    ErrorKind::IncompleteStream,
                    "Gemini stream ended before a complete terminal event.",
                ))
                .into())
            }
        };
        let usage = usage_of(&self.usage)?;
        let metadata = metadata(&self.parts, &self.calls);
        let result = ProviderResponse {
            content: Some(std::mem::take(&mut self.text_parts)),
            reasoning_content: Some(std::mem::take(&mut self.reasoning_parts)),
            tool_calls: self.calls.clone(),
            finish_reason: PyValue::from(finish),
            model: self.provider.model_info.wire_model_id().to_owned(),
            usage,
            chat_completion_metadata: Some(metadata.clone()),
            ..ProviderResponse::default()
        };
        self.provider
            .hooks
            .fire_on_response(&self.provider.response_context(result, self.status, true))
            .await;
        self.out.push_back(ProviderStreamChunk {
            finish_reason: Some(PyValue::from(finish)),
            chat_completion_metadata: Some(metadata),
            ..ProviderStreamChunk::default()
        });
        Ok(())
    }
}

impl Provider for GeminiProvider {
    fn model_info(&self) -> Option<&ModelInfo> {
        Some(&self.model_info)
    }

    /// `_base_url`, without trailing slashes.
    fn base_url(&self) -> Option<&str> {
        Some(&self.base_url)
    }

    fn generate(&self, request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        Box::pin(self.generate_once(request))
    }

    fn generate_stream(&self, request: ProviderRequest) -> ChunkStream<'_> {
        let run = StreamRun::new(self, request);
        Box::pin(futures_util::stream::unfold(run, |mut run| async move {
            let item = run.next().await?;
            Some((item, run))
        }))
    }

    fn close(&self) -> BoxFuture<'_, ()> {
        Box::pin(self.transport.close())
    }

    fn hooks(&self) -> Option<&HookManager> {
        Some(&self.hooks)
    }

    fn request_representation(
        &self,
        messages: &[Json],
        tools: Option<&[Json]>,
        options: &JsonMap,
    ) -> Option<ProviderResult<JsonMap>> {
        Some(self.provider_request_representation(messages, tools, options))
    }
}
