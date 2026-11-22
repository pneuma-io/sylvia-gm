//! Anthropic's Messages API: `provider/_anthropic.py` (`AnthropicMessagesTransport`,
//! `AnthropicProvider`, `with_cache_breakpoints` and the model helpers) and the parts of the
//! locked anthropic SDK (0.122.0) it relies on: the request the SDK sends, how it reads responses
//! and stream events, and its status errors. Lane 5, on lane 4's split.
//!
//! Responses and events are read as the SDK builds them ([`crate::sdk_models`], from
//! `anthropic_models.json`). The request carries Sylvia's `User-Agent` and none of the SDK's
//! identity headers (differences row 8); its protocol headers (`X-Api-Key` or the OpenCode bearer,
//! `anthropic-version`, `anthropic-beta`) are sent as Python sends them.

use crate::chat::{ChatError, SdkRequest};
use crate::error::{is_context_rejection, ErrorKind, ProviderError, ProviderResult};
use crate::hooks::{utc_now_isoformat, HookContext, HookManager};
use crate::http::{HttpRequest, HttpResponse, HttpTransport, TransportError};
use crate::httpx_url::HttpxUrl;
use crate::model_facts::{claude_model_facts, claude_model_id};
use crate::openai_compat::BuildError;
use crate::provider::{BoxFuture, ChunkStream, Provider, ProviderRequest};
use crate::pyobj::{int_or_zero, py_repr, py_str, truthy, Incidental};
use crate::sdk_models::{Node, Table};
use crate::sse::{ServerSentEvent, SseDecoder};
use crate::types::{
    CacheUsage, ModelInfo, ProviderResponse, ProviderStreamChunk, PyValue, RetryConfig, TimeoutConfig,
    TokenUsage, ToolCall,
};
use std::collections::VecDeque;
use std::sync::atomic::{AtomicBool, AtomicU32, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};
use sylvia_base::{Json, JsonMap};

/// `TOOL_CHANGES_BETA`: mid-conversation tool changes, by reference or by value.
pub const TOOL_CHANGES_BETA: &str = "inline-tools-2026-09-15";
/// The `anthropic-version` header the SDK sends.
pub const ANTHROPIC_VERSION: &str = "2023-06-01";
const TOOL_CHANGE_BLOCKS: [&str; 2] = ["tool_addition", "tool_removal"];
const THINKING_BLOCKS: [&str; 2] = ["thinking", "redacted_thinking"];

type AnthropicResult<T> = Result<T, ChatError>;

fn invalid(message: &str) -> ChatError {
    ChatError::Provider(ProviderError::new(ErrorKind::InvalidResponse, message))
}

fn get<'a>(value: &'a Json, key: &str) -> Option<&'a Json> {
    value.as_object().and_then(|map| map.get(key))
}

/// `value.get(key)` on a value Python calls `.get` on: a dict, or an `AttributeError`.
fn dict_get<'a>(value: &'a Json, key: &str) -> AnthropicResult<Option<&'a Json>> {
    match value {
        Json::Object(map) => Ok(map.get(key)),
        other => Err(Incidental::no_attribute(other, "get").into()),
    }
}

/// `accepts_forced_tool_choice(model_id)`: whether the wire model may be sent `tool_choice`
/// `any` or a named tool.
pub fn accepts_forced_tool_choice(model_id: &str) -> bool {
    claude_model_id(model_id).is_none_or(|claude| claude_model_facts(&claude).forced_tool_choice)
}

/// `accepts_temperature(model_id)`: whether the wire model may be sent a non-default temperature.
pub fn accepts_temperature(model_id: &str) -> bool {
    claude_model_id(model_id).is_none_or(|claude| claude_model_facts(&claude).temperature)
}

/// `_is_tool_change(message)`: a system message made only of tool additions and removals.
fn is_tool_change(message: &Json) -> bool {
    match get(message, "content") {
        Some(Json::Array(blocks)) if !blocks.is_empty() => blocks.iter().all(|block| {
            matches!(get(block, "type"), Some(Json::String(kind)) if TOOL_CHANGE_BLOCKS.contains(&kind.as_str()))
        }),
        _ => false,
    }
}

fn object(pairs: &[(&str, Json)]) -> Json {
    Json::Object(pairs.iter().map(|(key, value)| ((*key).to_owned(), value.clone())).collect())
}

/// `json.loads(text)`, or `None` for Python's `JSONDecodeError` and `TypeError`.
fn loads(text: &str) -> Option<Json> {
    sylvia_base::pyjson::loads(text).ok()
}

/// `"\n\n".join(m.get("content", "") for m in system_messages).strip()`.
fn legacy_system(system: &[&Json]) -> AnthropicResult<String> {
    let mut parts = Vec::new();
    for (index, message) in system.iter().enumerate() {
        match get(message, "content") {
            None => parts.push(String::new()),
            Some(Json::String(text)) => parts.push(text.clone()),
            Some(other) => {
                return Err(Incidental(format!(
                    "sequence item {index}: expected str instance, {} found",
                    crate::pyobj::type_name(other)
                ))
                .into())
            }
        }
    }
    Ok(sylvia_base::pystr::strip(&parts.join("\n\n")).to_owned())
}

/// `AnthropicMessagesTransport.format_request(model_id, messages, tools, stream, **kwargs)`.
pub fn format_request(
    model_id: &str,
    messages: &[Json],
    tools: Option<&[Json]>,
    stream: bool,
    kwargs: &JsonMap,
) -> AnthropicResult<JsonMap> {
    let mut system_messages: Vec<&Json> = Vec::new();
    let mut converted: Vec<Json> = Vec::new();
    let mut tool_changes: Vec<Json> = Vec::new();
    let empty = Json::String(String::new());
    for message in messages {
        let role = dict_get(message, "role")?.unwrap_or(&Json::Null);
        let role_text = role.as_str();
        if role_text == Some("system") && is_tool_change(message) {
            tool_changes.push(object(&[
                ("role", Json::String("system".into())),
                ("content", message["content"].clone()),
            ]));
            continue;
        }
        if role_text == Some("system") {
            system_messages.push(message);
            continue;
        }
        if role_text == Some("assistant") && !tool_changes.is_empty() {
            converted.append(&mut tool_changes);
        }
        match role_text {
            Some("assistant") => {
                if let Some(native) = get(message, "anthropic_content").filter(|native| !native.is_null()) {
                    converted.push(object(&[
                        ("role", Json::String("assistant".into())),
                        ("content", native.clone()),
                    ]));
                    continue;
                }
                let mut blocks = Vec::new();
                if let Some(text) = get(message, "content").filter(|text| truthy(text)) {
                    blocks.push(object(&[("type", Json::String("text".into())), ("text", text.clone())]));
                }
                let calls = get(message, "tool_calls").filter(|calls| truthy(calls)).cloned();
                for call in crate::pyobj::py_iter(calls.as_ref().unwrap_or(&Json::Array(Vec::new())))? {
                    let function =
                        dict_get(&call, "function")?.cloned().unwrap_or_else(|| Json::Object(JsonMap::new()));
                    let arguments = dict_get(&function, "arguments")?
                        .cloned()
                        .unwrap_or_else(|| Json::String("{}".into()));
                    let input = match &arguments {
                        Json::String(text) => loads(text).unwrap_or_else(|| Json::Object(JsonMap::new())),
                        other => other.clone(),
                    };
                    blocks.push(object(&[
                        ("type", Json::String("tool_use".into())),
                        ("id", dict_get(&call, "id")?.cloned().unwrap_or_else(|| empty.clone())),
                        ("name", dict_get(&function, "name")?.cloned().unwrap_or_else(|| empty.clone())),
                        ("input", input),
                    ]));
                }
                converted.push(object(&[
                    ("role", Json::String("assistant".into())),
                    ("content", Json::Array(blocks)),
                ]));
            }
            Some("tool") => {
                let content = get(message, "content").map_or_else(String::new, py_str);
                let result = object(&[
                    ("type", Json::String("tool_result".into())),
                    ("tool_use_id", get(message, "tool_call_id").cloned().unwrap_or_else(|| empty.clone())),
                    ("content", Json::String(content)),
                ]);
                converted.push(object(&[
                    ("role", Json::String("user".into())),
                    ("content", Json::Array(vec![result])),
                ]));
            }
            Some("user") => {
                let content = get(message, "content").cloned().unwrap_or_else(|| empty.clone());
                converted.push(object(&[("role", Json::String("user".into())), ("content", content)]));
            }
            _ => {
                return Err(invalid(&format!(
                    "Unsupported internal message role for Anthropic transport: {}",
                    py_repr(role)
                )))
            }
        }
    }
    converted.append(&mut tool_changes);

    let has_cache_blocks =
        system_messages.iter().any(|message| matches!(get(message, "content"), Some(Json::Array(_))));
    let system = if has_cache_blocks {
        let mut blocks = Vec::new();
        for message in &system_messages {
            match get(message, "content") {
                Some(Json::Array(content)) => blocks.extend(content.iter().cloned()),
                Some(Json::String(text)) if !sylvia_base::pystr::strip(text).is_empty() => {
                    blocks.push(object(&[
                        ("type", Json::String("text".into())),
                        ("text", Json::String(text.clone())),
                    ]));
                }
                _ => {}
            }
        }
        (!blocks.is_empty()).then_some(Json::Array(blocks))
    } else {
        let text = legacy_system(&system_messages)?;
        (!text.is_empty()).then_some(Json::String(text))
    };

    let mut request = JsonMap::new();
    request.insert("model".into(), Json::String(model_id.into()));
    request.insert("messages".into(), Json::Array(converted));
    request
        .insert("max_tokens".into(), kwargs.get("max_tokens").cloned().unwrap_or_else(|| Json::from(4096)));
    request.insert("stream".into(), Json::Bool(stream));
    if let Some(temperature) = kwargs.get("temperature") {
        if accepts_temperature(model_id) {
            request.insert("temperature".into(), temperature.clone());
        }
    }
    for key in ["extra_headers", "thinking", "output_config"] {
        if let Some(value) = kwargs.get(key) {
            request.insert(key.into(), value.clone());
        }
    }
    if let Some(speed) = kwargs.get("speed") {
        // Fast mode is a top-level body field the pinned SDK does not type.
        request.insert("extra_body".into(), object(&[("speed", speed.clone())]));
    }
    if let Some(system) = system.filter(truthy) {
        request.insert("system".into(), system);
    }
    if let Some(tools) = tools.filter(|tools| !tools.is_empty()) {
        let mut converted_tools = Vec::new();
        for tool in tools {
            let function =
                dict_get(tool, "function")?.cloned().unwrap_or_else(|| Json::Object(JsonMap::new()));
            let mut item = JsonMap::new();
            item.insert(
                "name".into(),
                dict_get(&function, "name")?.cloned().unwrap_or_else(|| empty.clone()),
            );
            item.insert(
                "description".into(),
                dict_get(&function, "description")?.cloned().unwrap_or_else(|| empty.clone()),
            );
            item.insert(
                "input_schema".into(),
                dict_get(&function, "parameters")?
                    .cloned()
                    .unwrap_or_else(|| object(&[("type", Json::String("object".into()))])),
            );
            if dict_get(tool, "defer_loading")? == Some(&Json::Bool(true)) {
                item.insert("defer_loading".into(), Json::Bool(true));
            }
            converted_tools.push(Json::Object(item));
        }
        let forced = kwargs.get("tool_choice") == Some(&Json::String("required".into()))
            && accepts_forced_tool_choice(model_id);
        let choice = forced.then(|| {
            if converted_tools.len() == 1 {
                object(&[("type", Json::String("tool".into())), ("name", converted_tools[0]["name"].clone())])
            } else {
                object(&[("type", Json::String("any".into()))])
            }
        });
        request.insert("tools".into(), Json::Array(converted_tools));
        if let Some(choice) = choice {
            request.insert("tool_choice".into(), choice);
        }
    }
    let deferred = match request.get("tools") {
        Some(Json::Array(tools)) => tools.iter().any(|tool| get(tool, "defer_loading").is_some_and(truthy)),
        _ => false,
    };
    let system_turn = match request.get("messages") {
        Some(Json::Array(messages)) => {
            messages.iter().any(|message| get(message, "role") == Some(&Json::String("system".into())))
        }
        _ => false,
    };
    if deferred || system_turn {
        // Tool additions in messages need the beta on every request of the conversation.
        let mut headers = match request.get("extra_headers") {
            Some(Json::Object(headers)) => headers.clone(),
            None | Some(Json::Null) => JsonMap::new(),
            Some(other) => {
                return Err(Incidental(format!(
                    "'{}' object is not iterable",
                    crate::pyobj::type_name(other)
                ))
                .into())
            }
        };
        let current = headers.get("anthropic-beta").map_or_else(String::new, py_str);
        let mut betas: Vec<String> = current
            .split(',')
            .map(|beta| sylvia_base::pystr::strip(beta).to_owned())
            .filter(|beta| !beta.is_empty())
            .collect();
        if !betas.iter().any(|beta| beta == TOOL_CHANGES_BETA) {
            betas.push(TOOL_CHANGES_BETA.into());
            headers.insert("anthropic-beta".into(), Json::String(betas.join(",")));
        }
        request.insert("extra_headers".into(), Json::Object(headers));
    }
    Ok(request)
}

fn ephemeral() -> Json {
    object(&[("type", Json::String("ephemeral".into()))])
}

/// `with_cache_breakpoints(request)`: mark the reusable prefix for the first-party prompt cache:
/// the last loaded tool, the last system block and the last non-thinking block of the last
/// message (before any trailing tool-change messages, whose own last block is marked too).
pub fn with_cache_breakpoints(request: &JsonMap) -> JsonMap {
    let mut marked = request.clone();
    if let Some(Json::Array(tools)) = marked.get_mut("tools") {
        if let Some(tool) = tools
            .iter_mut()
            .rev()
            .filter_map(Json::as_object_mut)
            .find(|tool| !tool.get("defer_loading").is_some_and(truthy))
        {
            tool.insert("cache_control".into(), ephemeral());
        }
    }
    match marked.get("system").cloned() {
        Some(Json::String(system)) if !system.is_empty() => {
            marked.insert(
                "system".into(),
                Json::Array(vec![object(&[
                    ("type", Json::String("text".into())),
                    ("text", Json::String(system)),
                    ("cache_control", ephemeral()),
                ])]),
            );
        }
        Some(Json::Array(mut blocks)) if blocks.last().is_some_and(Json::is_object) => {
            if let Some(Json::Object(last)) = blocks.last_mut() {
                last.insert("cache_control".into(), ephemeral());
            }
            marked.insert("system".into(), Json::Array(blocks));
        }
        _ => {}
    }
    if let Some(Json::Array(messages)) = marked.get("messages").cloned().filter(truthy) {
        let mut messages: Vec<Json> = messages
            .into_iter()
            .map(|message| match &message {
                Json::Object(map) => match map.get("content") {
                    Some(Json::String(text)) if !text.is_empty() => {
                        let mut copy = map.clone();
                        copy.insert(
                            "content".into(),
                            Json::Array(vec![object(&[
                                ("type", Json::String("text".into())),
                                ("text", Json::String(text.clone())),
                            ])]),
                        );
                        Json::Object(copy)
                    }
                    _ => message,
                },
                _ => message,
            })
            .collect();
        let is_change = |message: &Json| {
            get(message, "role") == Some(&Json::String("system".into())) && is_tool_change(message)
        };
        let count = messages.len();
        if count > 1 && is_change(&messages[count - 1]) {
            if let Some(Json::Array(blocks)) = messages[count - 1].get_mut("content") {
                if let Some(Json::Object(last)) = blocks.last_mut() {
                    last.insert("cache_control".into(), ephemeral());
                }
            }
        }
        let mut index = count - 1;
        while index > 0 && is_change(&messages[index]) {
            index -= 1;
        }
        if let Some(Json::Array(blocks)) = messages[index].get_mut("content") {
            if let Some(Json::Object(block)) = blocks.iter_mut().rev().find(|block| {
                block.is_object()
                    && !matches!(get(block, "type"), Some(Json::String(kind)) if THINKING_BLOCKS.contains(&kind.as_str()))
            }) {
                block.insert("cache_control".into(), ephemeral());
            }
        }
        marked.insert("messages".into(), Json::Array(messages));
    }
    marked
}

/// `binds_thinking_for_every_account(model_id)`: whether Sylvia treats this wire model as
/// rejecting a thinking block replayed after the system prompt or tool set changed.
pub fn binds_thinking_for_every_account(model_id: &str) -> bool {
    claude_model_facts(model_id).binds_thinking
}

/// `rejects_replayed_thinking(error)`: a 400 that names a thinking block's signature as invalid
/// or bound elsewhere.
pub fn rejects_replayed_thinking(error: &ProviderError) -> bool {
    if !error.is(ErrorKind::InvalidRequest) {
        return false;
    }
    let text = sylvia_base::pystr::casefold(&error.message);
    text.contains("signature")
        && text.contains("thinking")
        && (text.contains("invalid") || text.contains("different") || text.contains("does not match"))
}

// ── Reading responses and events ──────────────────────────────────────────────────────────

static EMPTY: Json = Json::String(String::new());
static NONE: Json = Json::Null;

/// The refusal for a usage count Python would carry as a float or add as text (differences
/// row 42, Anthropic values that are not integers or text).
pub const NON_INTEGER_COUNT: &str = "Anthropic usage count is not an integer";
/// The refusal for a streamed block index that is not an integer (the same row).
pub const NON_INTEGER_INDEX: &str = "Anthropic stream block index is not an integer";
/// The refusal for streamed text that is not a string (the same row).
pub const NON_STRING_TEXT: &str = "Anthropic stream text is not a string";

fn table() -> &'static Table {
    static TABLE: OnceLock<Table> = OnceLock::new();
    TABLE.get_or_init(|| Table::parse(include_str!("anthropic_models.json")))
}

/// The anthropic and pydantic versions the model table was generated from.
pub fn model_table_versions() -> (&'static str, &'static str) {
    (table().version("anthropic"), table().version("pydantic"))
}

fn is_str(value: &Json, text: &str) -> bool {
    value.as_str() == Some(text)
}

/// Python truthiness of a value as the SDK built it: a model is always true.
fn truthy_node(node: &Node) -> bool {
    node.model().is_some() || truthy(node.value)
}

/// `obj.name` without a default: an `AttributeError` when the attribute does not exist.
fn required_attr<'a>(node: &Node<'a>, name: &str) -> Result<Node<'a>, Incidental> {
    node.attr(name).ok_or_else(|| Incidental::no_attribute(node.value, name))
}

/// `_content_block_dict(block)`: a dict as itself, a model as `model_dump(mode="json",
/// exclude_none=True)`, anything else `vars()`'s `TypeError`.
fn content_block_dict(block: Option<&Node>) -> Result<Json, Incidental> {
    match block {
        Some(block) if block.model().is_some() => Ok(block.dump()),
        Some(block) if block.value.is_object() => Ok(block.value.clone()),
        _ => Err(Incidental("vars() argument must have __dict__ attribute".into())),
    }
}

/// An integer count as Python holds it: an `int` or a `bool`.
fn int_like(value: &Json) -> Option<Result<i64, Incidental>> {
    match value {
        Json::Bool(flag) => Some(Ok(i64::from(*flag))),
        Json::Number(number) if !number.is_f64() => {
            Some(number.as_i64().ok_or_else(|| Incidental(crate::pyobj::COUNT_OVERFLOW.into())))
        }
        _ => None,
    }
}

/// Python's `a + b` on two usage values. Integers add; the pairs Python cannot add raise its
/// `TypeError`; floats, and text or lists Python would concatenate, are refused with
/// [`NON_INTEGER_COUNT`].
fn py_add(a: &Json, b: &Json) -> Result<i64, Incidental> {
    let type_name = crate::pyobj::type_name;
    if let (Some(x), Some(y)) = (int_like(a), int_like(b)) {
        return x?.checked_add(y?).ok_or_else(|| Incidental(crate::pyobj::COUNT_OVERFLOW.into()));
    }
    let numeric = |value: &Json| matches!(value, Json::Bool(_) | Json::Number(_));
    match (a, b) {
        _ if numeric(a) && numeric(b) => Err(Incidental(NON_INTEGER_COUNT.into())),
        (Json::String(_), Json::String(_)) | (Json::Array(_), Json::Array(_)) => {
            Err(Incidental(NON_INTEGER_COUNT.into()))
        }
        (Json::String(_), other) => {
            Err(Incidental(format!("can only concatenate str (not \"{}\") to str", type_name(other))))
        }
        (Json::Array(_), other) => {
            Err(Incidental(format!("can only concatenate list (not \"{}\") to list", type_name(other))))
        }
        _ => Err(Incidental(format!(
            "unsupported operand type(s) for +: '{}' and '{}'",
            type_name(a),
            type_name(b)
        ))),
    }
}

fn count_of(value: &Json) -> Result<i64, Incidental> {
    int_like(value).unwrap_or_else(|| Err(Incidental(NON_INTEGER_COUNT.into())))
}

/// `stop_reason not in mapping` for a value that is not a string: a list or dict is
/// unhashable; anything else is simply absent.
fn unhashable(value: &Json) -> Option<Incidental> {
    match value {
        Json::Array(_) => Some(Incidental("unhashable type: 'list'".into())),
        Json::Object(_) => Some(Incidental("unhashable type: 'dict'".into())),
        _ => None,
    }
}

fn refused_stop(stop: &Json) -> Option<ProviderError> {
    if is_str(stop, "refusal") {
        return Some(ProviderError::new(ErrorKind::Refusal, "Anthropic declined the request."));
    }
    if is_str(stop, "pause_turn") {
        return Some(ProviderError::new(
            ErrorKind::Paused,
            "Anthropic requires an unsupported server-tool continuation.",
        ));
    }
    None
}

fn unsupported_stop(stop: &Json) -> ChatError {
    match unhashable(stop) {
        Some(incidental) => incidental.into(),
        None => invalid(&format!("Anthropic returned unsupported stop reason: {}", py_str(stop))),
    }
}

/// `AnthropicMessagesTransport.parse_response(raw_response, model_id)`. Every failure that is
/// not a provider error becomes `InvalidResponseError("Failed to parse Anthropic response: ...")`.
pub fn parse_response(raw: &Json, model_id: &str) -> ProviderResult<ProviderResponse> {
    parse_response_inner(raw, model_id).map_err(|error| match error {
        ChatError::Provider(error) => error,
        ChatError::Incidental(Incidental(message)) => ProviderError::new(
            ErrorKind::InvalidResponse,
            format!("Failed to parse Anthropic response: {message}"),
        ),
    })
}

fn parse_response_inner(raw: &Json, model_id: &str) -> AnthropicResult<ProviderResponse> {
    let zero = Json::from(0);
    let end_turn = Json::String("end_turn".into());
    let message = Node::root(table(), raw, "message");
    // `getattr(raw_response, "content", []) or []`; `None` stands for an item of a string or
    // dict iterated by mistake (a character or key: no attributes, and not a block).
    let blocks: Vec<Option<Node>> = match message.attr("content") {
        Some(content) if truthy_node(&content) => match content.value {
            Json::Array(_) => content.items().unwrap_or_default().into_iter().map(Some).collect(),
            Json::String(text) => text.chars().map(|_| None).collect(),
            Json::Object(map) => map.keys().map(|_| None).collect(),
            other => {
                return Err(Incidental(format!(
                    "'{}' object is not iterable",
                    crate::pyobj::type_name(other)
                ))
                .into())
            }
        },
        _ => Vec::new(),
    };
    let mut text_parts: Vec<&Json> = Vec::new();
    let mut tool_calls = Vec::new();
    for block in blocks.iter().flatten() {
        let kind = block.attr_or("type", &EMPTY);
        if is_str(kind, "text") {
            text_parts.push(block.attr_or("text", &EMPTY));
        } else if is_str(kind, "tool_use") {
            let arguments = match block.attr("input") {
                Some(input) => sylvia_base::pyjson::dumps(input.value, sylvia_base::pyjson::Dump::DEFAULT),
                None => "{}".to_owned(),
            };
            tool_calls.push(ToolCall {
                id: PyValue::from_json(block.attr_or("id", &EMPTY).clone()),
                name: PyValue::from_json(block.attr_or("name", &EMPTY).clone()),
                arguments: PyValue::Text(arguments),
            });
        }
    }

    let usage_data = message.attr("usage");
    let reported = usage_data.as_ref().is_some_and(|usage| !usage.is_none());
    let usage = usage_data.filter(truthy_node);
    let read = |name: &str, default: &'static Json| -> Json {
        usage.as_ref().map_or_else(|| default.clone(), |usage| usage.attr_or(name, default).clone())
    };
    let (prompt, completion) = match &usage {
        Some(usage) => {
            (usage.attr_or("input_tokens", &zero).clone(), usage.attr_or("output_tokens", &zero).clone())
        }
        None => (zero.clone(), zero.clone()),
    };
    // `TokenUsage(total_tokens=input + output)` is evaluated first, as Python does.
    if usage.is_some() {
        py_add(&prompt, &completion)?;
    }
    let or_zero = |value: Json| if truthy(&value) { value } else { Json::from(0) };
    let cache_read = or_zero(read("cache_read_input_tokens", &NONE));
    let cache_write = or_zero(read("cache_creation_input_tokens", &NONE));
    let total_prompt = py_add(&Json::from(py_add(&prompt, &cache_read)?), &cache_write)?;
    let total = py_add(&Json::from(total_prompt), &completion)?;
    let cache_usage = CacheUsage {
        cache_read_tokens: Some(count_of(&cache_read)?),
        cache_write_tokens: Some(count_of(&cache_write)?),
        cache_miss_tokens: Some(count_of(&prompt)?),
        total_prompt_tokens: total_prompt,
    };
    let token_usage = TokenUsage {
        prompt_tokens: PyValue::int(total_prompt),
        completion_tokens: PyValue::from_json(completion),
        total_tokens: PyValue::int(total),
        reasoning_tokens: None,
    };

    let stop = message.attr_or("stop_reason", &end_turn);
    let stop = if truthy(stop) { stop } else { &end_turn };
    if let Some(error) = refused_stop(stop) {
        return Err(error.into());
    }
    let finish = match stop.as_str() {
        Some("end_turn" | "stop_sequence") => "stop",
        Some("max_tokens") => "length",
        Some("tool_use") => "tool_calls",
        Some("model_context_window_exceeded") => "context_length",
        _ => return Err(unsupported_stop(stop)),
    };
    let content = if text_parts.is_empty() {
        None
    } else {
        let mut texts = Vec::with_capacity(text_parts.len());
        for (index, part) in text_parts.iter().enumerate() {
            match part {
                Json::String(text) => texts.push(text.as_str()),
                other => {
                    return Err(Incidental(format!(
                        "sequence item {index}: expected str instance, {} found",
                        crate::pyobj::type_name(other)
                    ))
                    .into())
                }
            }
        }
        Some(texts.join("\n"))
    };
    let mut reasoning = String::new();
    for block in &blocks {
        let dict = content_block_dict(block.as_ref())?;
        if get(&dict, "type").is_some_and(|kind| is_str(kind, "thinking")) {
            reasoning.push_str(&get(&dict, "thinking").map_or_else(String::new, py_str));
        }
    }
    let anthropic_content =
        blocks.iter().map(|block| content_block_dict(block.as_ref())).collect::<Result<Vec<_>, _>>()?;
    Ok(ProviderResponse {
        content,
        reasoning_content: (!reasoning.is_empty()).then_some(reasoning),
        anthropic_content: Some(anthropic_content),
        tool_calls,
        usage: token_usage,
        usage_reported: Some(reported),
        model: model_id.into(),
        finish_reason: PyValue::from(finish),
        cache_usage,
        ..ProviderResponse::default()
    })
}

fn text_of(value: &Json) -> Result<String, Incidental> {
    match value {
        Json::String(text) => Ok(text.clone()),
        _ => Err(Incidental(NON_STRING_TEXT.into())),
    }
}

/// `getattr(obj, name, "") or ""` as text.
fn text_or_empty(node: &Node, name: &str) -> Result<String, Incidental> {
    let value = node.attr_or(name, &EMPTY);
    if truthy(value) {
        text_of(value)
    } else {
        Ok(String::new())
    }
}

/// `AnthropicMessagesTransport.parse_stream_chunk(raw_chunk)` for one event as the SDK built it.
fn parse_stream_chunk(event: &Node) -> AnthropicResult<Option<ProviderStreamChunk>> {
    let zero = Json::from(0);
    let kind = event.attr_or("type", &EMPTY);
    let index = event.attr_or("index", &zero).clone();
    match kind.as_str() {
        Some("content_block_start") => {
            let Some(block) = event.attr("content_block").filter(|block| !block.is_none()) else {
                return Ok(None);
            };
            let block_type = block.attr_or("type", &EMPTY);
            if is_str(block_type, "text") {
                let text = text_or_empty(&block, "text")?;
                return Ok((!text.is_empty()).then(|| ProviderStreamChunk::content(text)));
            }
            if is_str(block_type, "thinking") {
                let thinking = text_or_empty(&block, "thinking")?;
                return Ok((!thinking.is_empty())
                    .then(|| ProviderStreamChunk { reasoning_content: thinking, ..Default::default() }));
            }
            if is_str(block_type, "tool_use") {
                let input = block.attr("input").map_or(&NONE, |input| input.value);
                let arguments = if truthy(input) {
                    sylvia_base::pyjson::dumps(input, sylvia_base::pyjson::Dump::WIRE.sorted(true))
                } else {
                    String::new()
                };
                let or_empty = |name: &str| {
                    let value = block.attr_or(name, &EMPTY);
                    if truthy(value) {
                        value.clone()
                    } else {
                        EMPTY.clone()
                    }
                };
                let mut delta = JsonMap::new();
                delta.insert("index".into(), index);
                delta.insert("id".into(), or_empty("id"));
                delta.insert("name".into(), or_empty("name"));
                delta.insert("arguments".into(), Json::String(arguments));
                return Ok(Some(ProviderStreamChunk {
                    tool_call_delta: Some(Json::Object(delta)),
                    ..Default::default()
                }));
            }
            Ok(None)
        }
        Some("content_block_delta") => {
            let Some(delta) = event.attr("delta").filter(|delta| !delta.is_none()) else { return Ok(None) };
            let delta_type = delta.attr_or("type", &EMPTY);
            if is_str(delta_type, "text_delta") {
                return Ok(Some(ProviderStreamChunk::content(text_or_empty(&delta, "text")?)));
            }
            if is_str(delta_type, "thinking_delta") {
                return Ok(Some(ProviderStreamChunk {
                    reasoning_content: text_or_empty(&delta, "thinking")?,
                    ..Default::default()
                }));
            }
            if is_str(delta_type, "input_json_delta") {
                let mut fragment = JsonMap::new();
                fragment.insert("index".into(), index);
                fragment.insert("partial_json".into(), delta.attr_or("partial_json", &EMPTY).clone());
                return Ok(Some(ProviderStreamChunk {
                    tool_call_delta: Some(Json::Object(fragment)),
                    ..Default::default()
                }));
            }
            Ok(None)
        }
        Some("message_delta") => {
            let Some(delta) = event.attr("delta").filter(truthy_node) else { return Ok(None) };
            let stop = delta.attr_or("stop_reason", &NONE);
            if stop.is_null() {
                return Ok(None);
            }
            if let Some(error) = refused_stop(stop) {
                return Err(error.into());
            }
            let finish = match stop.as_str() {
                Some(name @ ("end_turn" | "stop_sequence" | "tool_use" | "max_tokens")) => name,
                Some("model_context_window_exceeded") => "context_length",
                _ => return Err(unsupported_stop(stop)),
            };
            Ok(Some(ProviderStreamChunk::finish(finish)))
        }
        _ => Ok(None),
    }
}

/// A block index as a dict key: integers only (differences row 42).
fn block_index(value: &Json) -> Result<i64, Incidental> {
    int_like(value).unwrap_or_else(|| Err(Incidental(NON_INTEGER_INDEX.into())))
}

/// `block.get(field, "") + addition`, both text.
fn concat(existing: Option<&Json>, addition: &Json) -> Result<String, Incidental> {
    match (existing.unwrap_or(&EMPTY), addition) {
        (Json::String(left), Json::String(right)) => Ok(format!("{left}{right}")),
        (Json::String(_), other) => Err(Incidental(format!(
            "can only concatenate str (not \"{}\") to str",
            crate::pyobj::type_name(other)
        ))),
        _ => Err(Incidental(NON_STRING_TEXT.into())),
    }
}

/// `_AnthropicStreamContent`: one response's ordered native blocks, for the private tool-result
/// continuation, complete only after `message_stop`.
#[derive(Debug, Default)]
struct StreamContent {
    blocks: Vec<(i64, JsonMap)>,
    arguments: Vec<(i64, String)>,
    completed: bool,
}

impl StreamContent {
    /// `accept(event)`: the complete native content at `message_stop`, else `None`.
    fn accept(&mut self, event: &Node) -> AnthropicResult<Option<Vec<Json>>> {
        let zero = Json::from(0);
        let kind = event.attr_or("type", &EMPTY);
        let index = event.attr_or("index", &zero);
        match kind.as_str() {
            Some("content_block_start") => {
                let block = required_attr(event, "content_block")?;
                let dict = match content_block_dict(Some(&block))? {
                    Json::Object(map) => map,
                    _ => JsonMap::new(),
                };
                let key = block_index(index)?;
                match self.blocks.iter_mut().find(|(existing, _)| *existing == key) {
                    Some(slot) => slot.1 = dict,
                    None => self.blocks.push((key, dict)),
                }
            }
            Some("content_block_delta") => {
                let delta = required_attr(event, "delta")?;
                let delta_type = delta.attr_or("type", &EMPTY);
                let field = match delta_type.as_str() {
                    Some("text_delta") => Some("text"),
                    Some("thinking_delta") => Some("thinking"),
                    Some("signature_delta") => Some("signature"),
                    _ => None,
                };
                if let Some(field) = field {
                    let key = block_index(index)?;
                    let Some((_, block)) = self.blocks.iter_mut().find(|(existing, _)| *existing == key)
                    else {
                        // `KeyError(index)`, whose `str()` is the key.
                        return Err(Incidental(key.to_string()).into());
                    };
                    let joined = concat(block.get(field), delta.attr_or(field, &EMPTY))?;
                    block.insert(field.into(), Json::String(joined));
                } else if is_str(delta_type, "input_json_delta") {
                    let key = block_index(index)?;
                    let partial = required_attr(&delta, "partial_json")?;
                    let slot = self.arguments.iter().position(|(existing, _)| *existing == key);
                    let current = slot.map(|position| Json::String(self.arguments[position].1.clone()));
                    let joined = concat(current.as_ref(), partial.value)?;
                    match slot {
                        Some(position) => self.arguments[position].1 = joined,
                        None => self.arguments.push((key, joined)),
                    }
                }
            }
            Some("message_stop") => {
                for (key, fragments) in &self.arguments {
                    if fragments.is_empty() {
                        continue;
                    }
                    let parsed = sylvia_base::pyjson::loads(fragments).ok();
                    let block = self.blocks.iter_mut().find(|(existing, _)| existing == key);
                    match (parsed, block) {
                        (Some(input), Some((_, block))) => {
                            block.insert("input".into(), input);
                        }
                        _ => {
                            return Err(ChatError::Provider(ProviderError::new(
                                ErrorKind::InvalidToolArguments,
                                format!("Anthropic returned invalid streamed tool input at block {key}"),
                            )))
                        }
                    }
                }
                self.completed = true;
                let mut ordered: Vec<&(i64, JsonMap)> = self.blocks.iter().collect();
                ordered.sort_by_key(|(key, _)| *key);
                return Ok(Some(ordered.into_iter().map(|(_, block)| Json::Object(block.clone())).collect()));
            }
            _ => {}
        }
        Ok(None)
    }

    /// `require_complete()`.
    fn require_complete(&self) -> ProviderResult<()> {
        if self.completed {
            return Ok(());
        }
        Err(ProviderError::new(
            ErrorKind::IncompleteStream,
            "Anthropic stream ended before the terminal message_stop event",
        ))
    }
}

/// The usage `generate_stream` collects: the largest value of each count across
/// `message_start.message.usage` and each `message_delta.usage`.
#[derive(Debug, Default, Clone)]
struct StreamUsage {
    reported: bool,
    input: i64,
    output: i64,
    cache_read: i64,
    cache_write: i64,
}

impl StreamUsage {
    /// Take the usage an event carries, if any.
    fn observe(&mut self, event: &Node) -> Result<(), Incidental> {
        let kind = event.attr_or("type", &EMPTY);
        let usage = if is_str(kind, "message_start") {
            event.attr("message").and_then(|message| message.attr("usage"))
        } else if is_str(kind, "message_delta") {
            event.attr("usage")
        } else {
            None
        };
        let Some(usage) = usage.filter(|usage| !usage.is_none()) else { return Ok(()) };
        self.reported = true;
        let zero = Json::from(0);
        let read = |name: &str| int_or_zero(usage.attr_or(name, &zero));
        self.input = self.input.max(read("input_tokens")?);
        self.output = self.output.max(read("output_tokens")?);
        self.cache_read = self.cache_read.max(read("cache_read_input_tokens")?);
        self.cache_write = self.cache_write.max(read("cache_creation_input_tokens")?);
        Ok(())
    }

    /// The response the hooks see.
    fn response(&self) -> Result<ProviderResponse, Incidental> {
        let overflow = || Incidental(crate::pyobj::COUNT_OVERFLOW.into());
        let total_input = self
            .input
            .checked_add(self.cache_read)
            .and_then(|sum| sum.checked_add(self.cache_write))
            .ok_or_else(overflow)?;
        let total = total_input.checked_add(self.output).ok_or_else(overflow)?;
        Ok(ProviderResponse {
            usage: TokenUsage::from_ints(total_input, self.output, total, None),
            usage_reported: Some(self.reported),
            cache_usage: CacheUsage {
                cache_read_tokens: Some(self.cache_read),
                cache_write_tokens: Some(self.cache_write),
                cache_miss_tokens: Some(self.input),
                total_prompt_tokens: total_input,
            },
            ..ProviderResponse::default()
        })
    }
}

/// The SSE event names `AsyncStream.__stream__` hands to the transport (anthropic 0.122.0),
/// with `type` taken from the event name when the data lacks it. `completion` is passed on as it
/// is; `error` raises; `ping` and every other name are skipped, as is an event without a name.
pub const STREAM_EVENTS: [&str; 46] = [
    "message_start",
    "message_delta",
    "message_stop",
    "content_block_start",
    "content_block_delta",
    "content_block_stop",
    "message",
    "user.message",
    "user.interrupt",
    "user.tool_confirmation",
    "user.custom_tool_result",
    "user.tool_result",
    "agent.message",
    "agent.thinking",
    "agent.tool_use",
    "agent.tool_result",
    "agent.mcp_tool_use",
    "agent.mcp_tool_result",
    "agent.custom_tool_use",
    "agent.thread_context_compacted",
    "session.status_running",
    "session.status_idle",
    "session.status_rescheduled",
    "session.status_terminated",
    "session.error",
    "session.deleted",
    "session.updated",
    "span.model_request_start",
    "span.model_request_end",
    "span.outcome_evaluation_start",
    "span.outcome_evaluation_ongoing",
    "span.outcome_evaluation_end",
    "user.define_outcome",
    "agent.thread_message_received",
    "agent.thread_message_sent",
    "agent.session_thread_message_received",
    "agent.session_thread_message_sent",
    "session.thread_created",
    "session.thread_status_created",
    "session.thread_status_running",
    "session.thread_status_idle",
    "session.thread_status_rescheduled",
    "session.thread_status_terminated",
    "event_start",
    "event_delta",
    "system.message",
];

/// What the SDK makes of one server-sent event.
#[derive(Debug, Clone, PartialEq)]
enum SdkEvent {
    /// An event for the transport.
    Event(Json),
    /// Not passed on.
    Skip,
    /// An `error` event: `APIStatusError` with the stream's status and this message.
    Error(String),
}

/// `AsyncStream.__stream__` for one event. Data that is not JSON raises `JSONDecodeError`.
fn sdk_event(event: &ServerSentEvent, status: u16) -> Result<SdkEvent, Incidental> {
    let loads = |data: &str| sylvia_base::pyjson::loads(data).map_err(|error| Incidental(error.message));
    let Some(name) = event.event.as_deref() else { return Ok(SdkEvent::Skip) };
    if name == "completion" {
        return Ok(SdkEvent::Event(loads(&event.data)?));
    }
    if STREAM_EVENTS.contains(&name) {
        let mut data = loads(&event.data)?;
        if let Json::Object(map) = &mut data {
            if !map.contains_key("type") {
                map.insert("type".into(), Json::String(name.into()));
            }
        }
        return Ok(SdkEvent::Event(data));
    }
    if name == "error" {
        let message = match sylvia_base::pyjson::loads(&event.data) {
            Ok(body) => py_str(&body),
            Err(_) if !event.data.is_empty() => event.data.clone(),
            Err(_) => format!("Error code: {status}"),
        };
        return Ok(SdkEvent::Error(message));
    }
    Ok(SdkEvent::Skip)
}

// ── Errors ────────────────────────────────────────────────────────────────────────────────

/// `_raise_from_anthropic_error(exc, retryable_statuses)`; Sylvia always passes the retry
/// configuration's statuses.
pub fn status_error(
    status: u16,
    message: &str,
    retry_after_header: Option<&str>,
    retryable: &std::collections::BTreeSet<u16>,
) -> ProviderError {
    let delay = || crate::retry::retry_after_seconds(retry_after_header);
    if status == 401 || status == 403 {
        return ProviderError::new(ErrorKind::Authentication, message);
    }
    if retryable.contains(&status) {
        return if status == 429 || status == 529 {
            ProviderError::rate_limit(message, delay())
        } else {
            ProviderError::connection(message, delay())
        };
    }
    match status {
        404 => ProviderError::new(ErrorKind::ModelNotFound, message),
        400 if is_context_rejection(message) => ProviderError::token_limit_rejection(),
        400 => ProviderError::new(ErrorKind::InvalidRequest, message),
        _ => ProviderError::new(ErrorKind::Unavailable, message),
    }
}

// ── The SDK request ───────────────────────────────────────────────────────────────────────

/// The keyword parameters of `AsyncMessages.create` (anthropic 0.122.0) that go into the body,
/// in the order the SDK writes them.
pub const CREATE_PARAMETERS: [&str; 18] = [
    "max_tokens",
    "messages",
    "model",
    "cache_control",
    "container",
    "inference_geo",
    "metadata",
    "output_config",
    "service_tier",
    "stop_sequences",
    "stream",
    "system",
    "temperature",
    "thinking",
    "tool_choice",
    "tools",
    "top_k",
    "top_p",
];

const REQUIRED_PARAMETERS: [&str; 3] = ["max_tokens", "messages", "model"];

fn not_a_mapping(value: &Json) -> Incidental {
    Incidental(format!("'{}' object is not a mapping", crate::pyobj::type_name(value)))
}

/// `client.messages.create(**request)`: the body in the SDK's order with `extra_body` merged
/// over it, and the request's headers (`user_profile_id` as `anthropic-user-profile-id`, then
/// `extra_headers`). An unknown or missing keyword is Python's `TypeError`.
pub fn sdk_request(request: &JsonMap) -> Result<SdkRequest, Incidental> {
    let mut extra_headers = None;
    let mut extra_query = None;
    let mut extra_body = None;
    let mut timeout = None;
    let mut user_profile = None;
    for (key, value) in request {
        match key.as_str() {
            "extra_headers" => extra_headers = Some(value.clone()),
            "extra_query" => extra_query = Some(value.clone()),
            "extra_body" => extra_body = Some(value.clone()),
            "timeout" => timeout = Some(value.clone()),
            "user_profile_id" => user_profile = Some(value.clone()),
            other if CREATE_PARAMETERS.contains(&other) => {}
            other => {
                return Err(Incidental(format!(
                    "AsyncMessages.create() got an unexpected keyword argument '{other}'"
                )))
            }
        }
    }
    let missing: Vec<String> = REQUIRED_PARAMETERS
        .into_iter()
        .filter(|name| !request.contains_key(*name))
        .map(|name| format!("'{name}'"))
        .collect();
    if !missing.is_empty() {
        let listed = match missing.as_slice() {
            [one] => one.clone(),
            [first, second] => format!("{first} and {second}"),
            [rest @ .., last] => format!("{}, and {last}", rest.join(", ")),
            [] => String::new(),
        };
        let plural = if missing.len() == 1 { "argument" } else { "arguments" };
        return Err(Incidental(format!(
            "AsyncMessages.create() missing {} required keyword-only {plural}: {listed}",
            missing.len()
        )));
    }
    let mut body = JsonMap::new();
    for name in CREATE_PARAMETERS {
        if let Some(value) = request.get(name) {
            body.insert(name.into(), value.clone());
        }
    }
    match extra_body {
        None | Some(Json::Null) => {}
        Some(Json::Object(extra)) => body.extend(extra),
        Some(other) => return Err(not_a_mapping(&other)),
    }
    // `{**strip_not_given({"anthropic-user-profile-id": user_profile_id}), **(extra_headers or {})}`
    let extra_headers = match user_profile {
        None => extra_headers,
        Some(profile) => {
            let mut merged = JsonMap::new();
            merged.insert("anthropic-user-profile-id".into(), profile);
            match extra_headers {
                None | Some(Json::Null) => {}
                Some(Json::Object(extra)) => merged.extend(extra),
                Some(other) if !truthy(&other) => {}
                Some(other) => return Err(not_a_mapping(&other)),
            }
            Some(Json::Object(merged))
        }
    };
    Ok(SdkRequest { body, extra_headers, extra_query, timeout })
}

// ── The provider ──────────────────────────────────────────────────────────────────────────

/// The environment the provider and its SDK client read when they are built.
#[derive(Debug, Clone, Default, PartialEq, Eq)]
pub struct AnthropicEnvironment {
    /// `ANTHROPIC_CUSTOM_HEADERS`: `Name: value` lines the SDK sends under the configured
    /// headers.
    pub custom_headers: Option<String>,
    /// `SYLVIA_GM_CONTEXT_MODE`: `legacy` sends no cache breakpoints.
    pub context_mode: Option<String>,
}

impl AnthropicEnvironment {
    /// Read the variables as `AsyncAnthropic()` and `context_mode()` do.
    pub fn from_process() -> Self {
        Self {
            custom_headers: std::env::var("ANTHROPIC_CUSTOM_HEADERS").ok(),
            context_mode: std::env::var("SYLVIA_GM_CONTEXT_MODE").ok(),
        }
    }

    /// The client's custom headers: `{**parsed ANTHROPIC_CUSTOM_HEADERS, **default_headers}`.
    fn client_headers(&self, configured: &[(String, String)]) -> Vec<(String, String)> {
        let mut headers = Vec::new();
        for line in self.custom_headers.as_deref().unwrap_or("").split('\n') {
            if let Some(colon) = line.find(':') {
                let name = sylvia_base::pystr::strip(&line[..colon]);
                let value = sylvia_base::pystr::strip(&line[colon + 1..]);
                set_header(&mut headers, name, value.to_owned());
            }
        }
        for (name, value) in configured {
            set_header(&mut headers, name, value.clone());
        }
        headers
    }

    /// `context_mode(transport="anthropic_messages", provider_key="anthropic") == "ledger"`:
    /// the variable decides when it names `ledger` or `legacy`; the route ships the ledger.
    fn ledger(&self) -> bool {
        let setting = self
            .context_mode
            .as_deref()
            .map(|mode| sylvia_base::pystr::casefold(sylvia_base::pystr::strip(mode)));
        setting.as_deref() != Some("legacy")
    }
}

/// A Python dict assignment: the same key (case-sensitive) keeps its place.
fn set_header<V>(headers: &mut Vec<(String, V)>, name: &str, value: V) {
    match headers.iter_mut().find(|(existing, _)| existing == name) {
        Some(slot) => slot.1 = value,
        None => headers.push((name.to_owned(), value)),
    }
}

/// The SDK's `TypeError` when a request would carry no credentials.
pub const NO_AUTHENTICATION: &str = "\"Could not resolve authentication method. Expected one of api_key, auth_token, or credentials to be set. Or for one of the `X-Api-Key` or `Authorization` headers to be explicitly omitted\"";

/// The provider for one Anthropic Messages model.
pub struct AnthropicProvider {
    base_url: String,
    model_info: ModelInfo,
    retry_config: RetryConfig,
    timeout_config: TimeoutConfig,
    max_tokens: i64,
    default_headers: Vec<(String, String)>,
    client_headers: Vec<(String, String)>,
    api_key: String,
    /// `is_opencode_inference(base_url)`, which Python evaluates on each request.
    opencode: Result<bool, String>,
    endpoint: String,
    cache_breakpoints: bool,
    transport: Arc<dyn HttpTransport>,
    /// `provider.hooks`.
    pub hooks: HookManager,
    consecutive_errors: AtomicU32,
    closed: AtomicBool,
}

/// Python's `MAX_CONSECUTIVE_CONNECTION_ERRORS`.
pub const MAX_CONSECUTIVE_CONNECTION_ERRORS: u32 = 3;

impl AnthropicProvider {
    /// `AnthropicProvider(base_url, api_key, model_info, retry_config, max_tokens=...,
    /// timeout_config=..., default_headers=...)`, plus the environment and the transport.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        base_url: &str,
        api_key: &str,
        model_info: ModelInfo,
        retry_config: Option<RetryConfig>,
        timeout_config: Option<TimeoutConfig>,
        max_tokens: i64,
        default_headers: Vec<(String, String)>,
        environment: AnthropicEnvironment,
        transport: Arc<dyn HttpTransport>,
    ) -> Result<Self, BuildError> {
        // `_build_client`: the SDK adds `/v1/messages` itself.
        let trimmed = base_url.trim_end_matches('/');
        let sdk_base = trimmed.strip_suffix("/v1").unwrap_or(trimmed);
        let endpoint = HttpxUrl::sdk_endpoint(sdk_base, "/v1/messages")
            .map_err(|error| BuildError::InvalidUrl(error.0))?
            .to_string();
        let cache_breakpoints = model_info.provider_key == "anthropic" && environment.ledger();
        Ok(Self {
            base_url: base_url.to_owned(),
            opencode: crate::routes::is_opencode_inference(base_url).map_err(|error| error.0),
            model_info,
            retry_config: retry_config.unwrap_or_default(),
            timeout_config: timeout_config.unwrap_or_default(),
            max_tokens,
            client_headers: environment.client_headers(&default_headers),
            default_headers,
            api_key: api_key.to_owned(),
            endpoint,
            cache_breakpoints,
            transport,
            hooks: HookManager::new(),
            consecutive_errors: AtomicU32::new(0),
            closed: AtomicBool::new(false),
        })
    }

    /// The configured base URL.
    pub fn base_url(&self) -> &str {
        &self.base_url
    }

    /// The URL requests go to.
    pub fn endpoint(&self) -> &str {
        &self.endpoint
    }

    /// `model_info`.
    pub fn model(&self) -> &ModelInfo {
        &self.model_info
    }

    /// `_retry_config`.
    pub fn retry_config(&self) -> &RetryConfig {
        &self.retry_config
    }

    /// The timeouts the HTTP client should use.
    pub fn timeout_config(&self) -> &TimeoutConfig {
        &self.timeout_config
    }

    /// `_default_headers`, in their configured order.
    pub fn default_headers(&self) -> &[(String, String)] {
        &self.default_headers
    }

    /// `_max_tokens`.
    pub fn max_tokens(&self) -> i64 {
        self.max_tokens
    }

    /// `_format(**arguments)`: the transport's request, with cache breakpoints on first-party
    /// Anthropic. A conversion failure escapes before any hook: Python's provider error, or an
    /// accidental exception (here `Internal`).
    fn format(
        &self,
        messages: &[Json],
        tools: Option<&[Json]>,
        stream: bool,
        kwargs: &JsonMap,
    ) -> ProviderResult<JsonMap> {
        let request = format_request(self.model_info.wire_model_id(), messages, tools, stream, kwargs)
            .map_err(escaped)?;
        Ok(if self.cache_breakpoints { with_cache_breakpoints(&request) } else { request })
    }

    /// `provider_request_representation(messages, tools, options)`: the exact client request,
    /// built without sending it.
    pub fn provider_request_representation(
        &self,
        messages: &[Json],
        tools: Option<&[Json]>,
        options: &JsonMap,
    ) -> ProviderResult<JsonMap> {
        let mut options = options.clone();
        let stream = options.shift_remove("stream").is_some_and(|value| truthy(&value));
        options.shift_remove("timeout");
        options.shift_remove("_retry_attempt");
        let max_tokens = options.shift_remove("max_tokens").unwrap_or_else(|| Json::from(self.max_tokens));
        options.shift_remove("max_output_tokens");
        options.insert("max_tokens".into(), max_tokens);
        self.format(messages, tools, stream, &options)
    }

    fn prepare(&self, request: &ProviderRequest, stream: bool) -> ProviderResult<(JsonMap, HookContext)> {
        let mut options = request.options.clone();
        let retry_attempt =
            options.shift_remove("_retry_attempt").and_then(|value| value.as_i64()).unwrap_or(0);
        if !options.contains_key("max_tokens") {
            options.insert("max_tokens".into(), Json::from(self.max_tokens));
        }
        let request_json = self.format(&request.messages, request.tools.as_deref(), stream, &options)?;
        let context = HookContext {
            provider_key: self.model_info.provider_key.clone(),
            model_id: self.model_info.id.clone(),
            message_count: request.messages.len(),
            tool_count: request.tools.as_ref().map_or(0, Vec::len),
            request_json: Some(request_json.clone()),
            retry_attempt,
            timestamp_iso: utc_now_isoformat(),
            stream,
            ..HookContext::default()
        };
        Ok((request_json, context))
    }

    /// `_credential_request_options(headers)`: whether the request omits `X-Api-Key` (OpenCode
    /// authenticates with a bearer token; a connection without a key sends none), which needs
    /// the request's `extra_headers` as a mapping.
    fn credential_options(&self, extra_headers: Option<&Json>) -> Result<bool, Incidental> {
        let opencode = self.opencode.clone().map_err(Incidental)?;
        let omit = opencode
            || (self.api_key.is_empty()
                && !self.default_headers.iter().any(|(name, _)| name.to_lowercase() == "x-api-key"));
        if omit {
            match extra_headers {
                Some(Json::Object(_)) | None => {}
                Some(value) if !truthy(value) => {}
                Some(other) => return Err(not_a_mapping(other)),
            }
        }
        Ok(omit)
    }

    /// The headers the SDK sends that Sylvia or the protocol sets, in the SDK's order: the
    /// client's defaults (credentials, custom headers, `anthropic-version`) merged with the
    /// request's, `Omit` removed, then the SDK's credential check (differences row 8).
    fn headers(
        &self,
        extra_headers: Option<&Json>,
        omit_api_key: bool,
    ) -> Result<Vec<(String, String)>, Incidental> {
        let text = |value: &str| Some(Json::String(value.to_owned()));
        let mut merged: Vec<(String, Option<Json>)> = Vec::new();
        set_header(&mut merged, "Accept", text("application/json"));
        set_header(&mut merged, "Content-Type", text("application/json"));
        set_header(&mut merged, "User-Agent", text(&crate::openai_compat::sylvia_user_agent()));
        if self.opencode == Ok(true) {
            set_header(&mut merged, "X-Api-Key", text(""));
            set_header(&mut merged, "Authorization", text(&format!("Bearer {}", self.api_key)));
        } else {
            set_header(&mut merged, "X-Api-Key", text(&self.api_key));
        }
        for (name, value) in &self.client_headers {
            set_header(&mut merged, name, text(value));
        }
        set_header(&mut merged, "anthropic-version", text(ANTHROPIC_VERSION));
        for (name, value) in &self.client_headers {
            set_header(&mut merged, name, text(value));
        }
        let mut custom: Vec<(String, Option<Json>)> = match extra_headers {
            Some(Json::Object(extra)) => {
                extra.iter().map(|(name, value)| (name.clone(), Some(value.clone()))).collect()
            }
            None => Vec::new(),
            Some(value) if !truthy(value) => Vec::new(),
            Some(other) => return Err(not_a_mapping(other)),
        };
        if omit_api_key {
            set_header(&mut custom, "X-Api-Key", None);
        }
        for (name, value) in &custom {
            set_header(&mut merged, name, value.clone());
        }
        let headers: Vec<(String, Json)> =
            merged.into_iter().filter_map(|(name, value)| value.map(|value| (name, value))).collect();
        let present = |name: &str| headers.iter().any(|(existing, value)| existing == name && truthy(value));
        let omitted = |name: &str| custom.iter().any(|(existing, value)| existing == name && value.is_none());
        if !present("Authorization")
            && !present("X-Api-Key")
            && !omitted("X-Api-Key")
            && !omitted("Authorization")
        {
            return Err(Incidental(NO_AUTHENTICATION.into()));
        }
        headers
            .into_iter()
            .map(|(name, value)| match value {
                Json::String(value) => Ok((name, value)),
                other => Err(Incidental(format!(
                    "Header value must be str or bytes, not {}",
                    crate::pyobj::type_name(&other)
                ))),
            })
            .collect()
    }

    fn http_request(&self, request_json: &JsonMap, omit_api_key: bool) -> Result<HttpRequest, Incidental> {
        let sdk = sdk_request(request_json)?;
        if sdk.extra_query.as_ref().is_some_and(|query| !query.is_null()) {
            // Sylvia never passes one; the SDK would merge it into the URL.
            return Err(Incidental("extra_query is not supported by the Rust provider".into()));
        }
        Ok(HttpRequest {
            method: "POST".into(),
            url: self.endpoint.clone(),
            headers: self.headers(sdk.extra_headers.as_ref(), omit_api_key)?,
            body: sdk.body_bytes(),
        })
    }

    async fn recycle_pool(&self) {
        crate::log::warning(&format!(
            "Recycling connection pool for {} after {} consecutive errors",
            self.model_info.provider_name,
            self.consecutive_errors.load(Ordering::SeqCst)
        ));
        self.transport.recycle().await;
        self.consecutive_errors.store(0, Ordering::SeqCst);
    }

    fn error_context(context: &HookContext, error: &ProviderError, started: Instant) -> HookContext {
        HookContext {
            error: Some(error.clone()),
            duration_ms: started.elapsed().as_secs_f64() * 1000.0,
            ..context.clone()
        }
    }

    /// One attempt of `generate`'s `_call`.
    async fn call_once(&self, request_json: &JsonMap, timeout: f64) -> Result<ProviderResponse, CallFailure> {
        let omit =
            self.credential_options(request_json.get("extra_headers")).map_err(CallFailure::Incidental)?;
        let http = self.http_request(request_json, omit).map_err(CallFailure::Incidental)?;
        let wait = Duration::from_secs_f64(timeout.max(0.0));
        let outcome = tokio::time::timeout(wait, async {
            let mut response = self.transport.send(http).await.map_err(CallFailure::from_transport)?;
            let body = response.read_all().await.map_err(CallFailure::from_transport)?;
            if response.status >= 400 {
                let message = crate::chat::status_error_message(response.status, &body);
                return Err(CallFailure::Provider(status_error(
                    response.status,
                    &message,
                    response.header("retry-after"),
                    &self.retry_config.retryable_statuses,
                )));
            }
            crate::openai_compat::sdk_completion_data(&response, &body).map_err(CallFailure::Incidental)
        })
        .await;
        let data = match outcome {
            Err(_) => {
                return Err(CallFailure::Provider(ProviderError::new(
                    ErrorKind::Timeout,
                    format!(
                        "Anthropic request timed out after {}s",
                        sylvia_base::pyjson::float_repr(timeout)
                    ),
                )))
            }
            Ok(result) => result?,
        };
        parse_response(&data, &self.model_info.id).map_err(CallFailure::Provider)
    }

    async fn generate_inner(&self, request: ProviderRequest) -> ProviderResult<ProviderResponse> {
        let (request_json, context) = self.prepare(&request, false)?;
        self.hooks.fire_on_request(&context).await;
        let started = Instant::now();
        // Failures that are not provider errors stop the retry loop and escape after it.
        let escaped_failure: Mutex<Option<Incidental>> = Mutex::new(None);
        let outcome = crate::retry::retry_with_backoff(&self.retry_config, || async {
            match self.call_once(&request_json, request.timeout).await {
                Ok(response) => Ok(response),
                Err(CallFailure::Provider(error)) => Err(error),
                Err(CallFailure::Incidental(incidental)) => {
                    let message = incidental.0.clone();
                    *escaped_failure.lock().expect("escape lock") = Some(incidental);
                    Err(ProviderError::new(ErrorKind::Internal, message))
                }
            }
        })
        .await;
        match outcome {
            Ok(response) => {
                self.consecutive_errors.store(0, Ordering::SeqCst);
                let finished = HookContext {
                    response: Some(response.clone()),
                    duration_ms: started.elapsed().as_secs_f64() * 1000.0,
                    http_status: Some(200),
                    ..context
                };
                self.hooks.fire_on_response(&finished).await;
                Ok(response)
            }
            Err(error) => {
                let taken = escaped_failure.lock().expect("escape lock").take();
                if taken.is_none() && (error.is(ErrorKind::Timeout) || error.is(ErrorKind::Connection)) {
                    let count = self.consecutive_errors.fetch_add(1, Ordering::SeqCst) + 1;
                    if count >= MAX_CONSECUTIVE_CONNECTION_ERRORS {
                        self.recycle_pool().await;
                    }
                }
                // The outer `except Exception` hands the hooks the exception and re-raises it.
                self.hooks.fire_on_error(&Self::error_context(&context, &error, started)).await;
                Err(error)
            }
        }
    }
}

/// An exception Python lets escape as it is: provider errors unchanged, the rest `Internal`.
fn escaped(error: ChatError) -> ProviderError {
    match error {
        ChatError::Provider(error) => error,
        ChatError::Incidental(Incidental(message)) => ProviderError::new(ErrorKind::Internal, message),
    }
}

/// A failed attempt inside `generate`.
enum CallFailure {
    Provider(ProviderError),
    Incidental(Incidental),
}

impl CallFailure {
    /// The SDK's `APITimeoutError` and `APIConnectionError` as `generate` maps them.
    fn from_transport(error: TransportError) -> Self {
        Self::Provider(match error {
            TransportError::Timeout(_) => {
                ProviderError::new(ErrorKind::Timeout, "Anthropic SDK timeout: Request timed out.")
            }
            TransportError::Connection(_) => {
                ProviderError::connection("Anthropic connection error: Connection error.", None)
            }
        })
    }
}

enum Phase {
    NotStarted,
    Reading,
    Finished,
}

/// One `generate_stream` call: Python's async generator, pulled chunk by chunk.
struct StreamRun<'a> {
    provider: &'a AnthropicProvider,
    request: ProviderRequest,
    phase: Phase,
    context: HookContext,
    started: Instant,
    response: Option<HttpResponse>,
    status: u16,
    retry_after: Option<String>,
    decoder: SseDecoder,
    events: VecDeque<ServerSentEvent>,
    body_ended: bool,
    content: StreamContent,
    usage: StreamUsage,
    out: VecDeque<ProviderStreamChunk>,
    failure: Option<ProviderError>,
}

impl<'a> StreamRun<'a> {
    fn new(provider: &'a AnthropicProvider, request: ProviderRequest) -> Self {
        Self {
            provider,
            request,
            phase: Phase::NotStarted,
            context: HookContext::default(),
            started: Instant::now(),
            response: None,
            status: 200,
            retry_after: None,
            decoder: SseDecoder::new(),
            events: VecDeque::new(),
            body_ended: false,
            content: StreamContent::default(),
            usage: StreamUsage::default(),
            out: VecDeque::new(),
            failure: None,
        }
    }

    fn timed_out(&self) -> ProviderError {
        ProviderError::new(
            ErrorKind::Timeout,
            format!(
                "Anthropic stream timed out after {}s",
                sylvia_base::pyjson::float_repr(self.request.timeout)
            ),
        )
    }

    async fn next(&mut self) -> Option<ProviderResult<ProviderStreamChunk>> {
        loop {
            if let Some(chunk) = self.out.pop_front() {
                return Some(Ok(chunk));
            }
            if let Some(error) = self.failure.take() {
                self.phase = Phase::Finished;
                return Some(Err(error));
            }
            match self.phase {
                Phase::NotStarted => match self.start().await {
                    Ok(()) => self.phase = Phase::Reading,
                    Err(error) => {
                        self.phase = Phase::Finished;
                        return Some(Err(error));
                    }
                },
                Phase::Reading => self.read_step().await,
                Phase::Finished => return None,
            }
        }
    }

    /// A failure handled by one of the `except` branches that hand the hooks no usage.
    async fn fail(&mut self, error: ProviderError) {
        let context = AnthropicProvider::error_context(&self.context, &error, self.started);
        self.provider.hooks.fire_on_error(&context).await;
        self.response = None;
        self.phase = Phase::Finished;
        self.failure = Some(error);
    }

    /// The `except Exception` branch: the hooks also see the usage reported so far.
    async fn fail_generic(&mut self, error: ProviderError) {
        let mut context = AnthropicProvider::error_context(&self.context, &error, self.started);
        if self.usage.reported {
            context.response = self.usage.response().ok();
        }
        self.provider.hooks.fire_on_error(&context).await;
        self.response = None;
        self.phase = Phase::Finished;
        self.failure = Some(error);
    }

    /// Everything up to the response headers.
    async fn start(&mut self) -> ProviderResult<()> {
        let (request_json, context) = self.provider.prepare(&self.request, true)?;
        self.context = context;
        self.provider.hooks.fire_on_request(&self.context).await;
        // Before the `try`: a failure here escapes without `on_error`.
        let omit = self
            .provider
            .credential_options(request_json.get("extra_headers"))
            .map_err(|Incidental(message)| ProviderError::new(ErrorKind::Internal, message))?;
        self.started = Instant::now();
        let http = match self.provider.http_request(&request_json, omit) {
            Ok(http) => http,
            Err(Incidental(message)) => {
                self.fail_generic(ProviderError::new(ErrorKind::Internal, message)).await;
                return Err(self.failure.take().expect("failure"));
            }
        };
        let wait = Duration::from_secs_f64(self.request.timeout.max(0.0));
        let provider = self.provider;
        let opened = tokio::time::timeout(wait, async {
            let mut response = provider.transport.send(http).await.map_err(OpenFailure::Transport)?;
            if response.status >= 400 {
                let body = response.read_all().await.map_err(OpenFailure::Transport)?;
                let message = crate::chat::status_error_message(response.status, &body);
                return Err(OpenFailure::Status(status_error(
                    response.status,
                    &message,
                    response.header("retry-after"),
                    &provider.retry_config.retryable_statuses,
                )));
            }
            Ok(response)
        })
        .await;
        let error = match opened {
            Ok(Ok(response)) => {
                self.status = response.status;
                self.retry_after = response.header("retry-after").map(str::to_owned);
                self.response = Some(response);
                return Ok(());
            }
            Err(_) | Ok(Err(OpenFailure::Transport(TransportError::Timeout(_)))) => self.timed_out(),
            Ok(Err(OpenFailure::Transport(TransportError::Connection(_)))) => {
                ProviderError::connection("Connection error.", None)
            }
            Ok(Err(OpenFailure::Status(error))) => error,
        };
        self.fail(error).await;
        Err(self.failure.take().expect("failure"))
    }

    /// One step of `async for event in stream`: the next event, a piece of the body, or the end.
    async fn read_step(&mut self) {
        if let Some(event) = self.events.pop_front() {
            self.handle_event(&event).await;
            return;
        }
        if self.body_ended {
            self.response = None;
            if let Err(error) = self.content.require_complete() {
                self.fail_generic(error).await;
                return;
            }
            self.phase = Phase::Finished;
            let response = match self.usage.response() {
                Ok(response) => response,
                Err(Incidental(message)) => {
                    self.failure = Some(ProviderError::new(ErrorKind::Internal, message));
                    return;
                }
            };
            let finished = HookContext {
                response: Some(response),
                duration_ms: self.started.elapsed().as_secs_f64() * 1000.0,
                http_status: Some(200),
                ..self.context.clone()
            };
            self.provider.hooks.fire_on_response(&finished).await;
            return;
        }
        let Some(response) = self.response.as_mut() else {
            self.body_ended = true;
            return;
        };
        let decoded = match response.body.next_piece().await {
            None => {
                self.body_ended = true;
                self.decoder.finish()
            }
            Some(Ok(piece)) => self.decoder.feed(&piece),
            Some(Err(TransportError::Timeout(_))) => {
                let error = self.timed_out();
                self.fail(error).await;
                return;
            }
            // An httpx error while reading escapes the SDK's iterator as it is.
            Some(Err(TransportError::Connection(message))) => {
                self.fail_generic(ProviderError::new(ErrorKind::Internal, message)).await;
                return;
            }
        };
        match decoded {
            Ok(events) => self.events.extend(events),
            Err(Incidental(message)) => {
                self.fail_generic(ProviderError::new(ErrorKind::Internal, message)).await
            }
        }
    }

    async fn handle_event(&mut self, event: &ServerSentEvent) {
        let data = match sdk_event(event, self.status) {
            Ok(SdkEvent::Skip) => return,
            Ok(SdkEvent::Event(data)) => data,
            Ok(SdkEvent::Error(message)) => {
                let error = status_error(
                    self.status,
                    &message,
                    self.retry_after.as_deref(),
                    &self.provider.retry_config.retryable_statuses,
                );
                self.fail(error).await;
                return;
            }
            Err(Incidental(message)) => {
                self.fail_generic(ProviderError::new(ErrorKind::Internal, message)).await;
                return;
            }
        };
        let node = Node::root(table(), &data, "stream_event");
        let outcome: AnthropicResult<()> = (|| {
            if let Some(content) = self.content.accept(&node)? {
                self.out.push_back(ProviderStreamChunk {
                    anthropic_content: Some(content),
                    ..Default::default()
                });
            }
            self.usage.observe(&node)?;
            if let Some(chunk) = parse_stream_chunk(&node)? {
                self.out.push_back(chunk);
            }
            Ok(())
        })();
        if let Err(error) = outcome {
            self.fail_generic(escaped(error)).await;
        }
    }
}

/// A failure before the stream exists.
enum OpenFailure {
    Transport(TransportError),
    Status(ProviderError),
}

impl Provider for AnthropicProvider {
    fn model_info(&self) -> Option<&ModelInfo> {
        Some(&self.model_info)
    }

    fn generate(&self, request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        Box::pin(self.generate_inner(request))
    }

    fn generate_stream(&self, request: ProviderRequest) -> ChunkStream<'_> {
        let run = StreamRun::new(self, request);
        Box::pin(futures_util::stream::unfold(run, |mut run| async move {
            let item = run.next().await?;
            Some((item, run))
        }))
    }

    fn close(&self) -> BoxFuture<'_, ()> {
        Box::pin(async move {
            if self.closed.swap(true, Ordering::SeqCst) {
                return;
            }
            self.transport.close().await;
        })
    }
}
