//! The OpenAI Responses transport: `ResponsesTransport`, `_responses_reasoning_effort` and
//! `_responses_service_tier` in `provider/_openai_compat.py`, and `native_output` and
//! `responses_finish` in `provider/_native_output.py`. Lane 5, on lane 4's split.
//!
//! The internal conversation stays Chat-Completions-shaped; this converts history and tools at
//! the provider boundary and reads Responses output and stream events back into Sylvia's types.
//! `OpenAICompatProvider` picks it for `transport == "responses"` (`openai_compat.rs`).
//!
//! The response JSON is read as the OpenAI SDK reads it, from the SDK's own model table
//! (`responses_models.json`, from `generate_sdk_models.py`). The SDK builds its models
//! leniently: a declared field the JSON lacks reads as `None`, an undeclared name falls back to
//! `getattr`'s default, and a plain dict has no attributes at all. Retained output items are
//! dumped as `model_dump(mode="json", exclude_none=True)` dumps the constructed models.

use crate::chat::{ChatError, SdkRequest};
use crate::error::{ErrorKind, ProviderError};
use crate::pyobj::{int_or_zero, py_repr, py_str, truthy, Incidental};
use crate::sdk_models::{Node, Table};
use crate::types::{CacheUsage, ProviderResponse, ProviderStreamChunk, PyValue, TokenUsage, ToolCall};
use std::sync::OnceLock;
use sylvia_base::{Json, JsonMap};

type ResponsesResult<T> = Result<T, ChatError>;

fn invalid(message: &str) -> ChatError {
    ChatError::Provider(ProviderError::new(ErrorKind::InvalidResponse, message))
}

fn get<'a>(value: &'a Json, key: &str) -> Option<&'a Json> {
    value.as_object().and_then(|map| map.get(key))
}

// ── Request ───────────────────────────────────────────────────────────────────────────────

/// `ResponsesTransport._input(messages)`: Chat-shaped history as Responses input items.
pub fn input(messages: &[Json]) -> ResponsesResult<Vec<Json>> {
    let mut result = Vec::new();
    for message in messages {
        let Json::Object(message) = message else {
            return Err(Incidental::no_attribute(message, "get").into());
        };
        let role = message.get("role").unwrap_or(&Json::Null);
        if role == &Json::String("tool".into()) {
            let call_id = match message.get("tool_call_id") {
                Some(Json::String(id)) if !id.is_empty() => id.clone(),
                _ => return Err(invalid("Tool result is missing its call identity")),
            };
            let output = message.get("content").map_or_else(String::new, py_str);
            let mut item = JsonMap::new();
            item.insert("type".into(), Json::String("function_call_output".into()));
            item.insert("call_id".into(), Json::String(call_id));
            item.insert("output".into(), Json::String(output));
            result.push(Json::Object(item));
            continue;
        }
        if role == &Json::String("assistant".into()) {
            if let Some(Json::Array(retained)) = message.get("responses_output") {
                result.extend(retained.iter().cloned());
                continue;
            }
            if let Some(Json::String(content)) = message.get("content") {
                if !content.is_empty() {
                    let mut item = JsonMap::new();
                    item.insert("role".into(), Json::String("assistant".into()));
                    item.insert("content".into(), Json::String(content.clone()));
                    result.push(Json::Object(item));
                }
            }
            if let Some(Json::Array(calls)) = message.get("tool_calls") {
                for call in calls {
                    let Json::Object(call) = call else {
                        return Err(invalid("Assistant tool call is malformed"));
                    };
                    let Some(Json::Object(function)) = call.get("function") else {
                        return Err(invalid("Assistant tool call has no function"));
                    };
                    let (Some(Json::String(call_id)), Some(Json::String(name))) =
                        (call.get("id"), function.get("name"))
                    else {
                        return Err(invalid("Assistant tool call identity is malformed"));
                    };
                    if call_id.is_empty() {
                        return Err(invalid("Assistant tool call identity is malformed"));
                    }
                    let arguments = match function.get("arguments") {
                        None => "{}".to_string(),
                        Some(Json::String(arguments)) => arguments.clone(),
                        Some(_) => "{}".to_string(),
                    };
                    let mut item = JsonMap::new();
                    item.insert("type".into(), Json::String("function_call".into()));
                    item.insert("call_id".into(), Json::String(call_id.clone()));
                    item.insert("name".into(), Json::String(name.clone()));
                    item.insert("arguments".into(), Json::String(arguments));
                    result.push(Json::Object(item));
                }
            }
            continue;
        }
        if let Json::String(text) = role {
            if matches!(text.as_str(), "system" | "developer" | "user") {
                let mut item = JsonMap::new();
                item.insert("role".into(), Json::String(text.clone()));
                item.insert(
                    "content".into(),
                    Json::String(message.get("content").map_or_else(String::new, py_str)),
                );
                result.push(Json::Object(item));
                continue;
            }
        }
        return Err(invalid(&format!("Unsupported internal message role: {}", py_repr(role))));
    }
    Ok(result)
}

/// `ResponsesTransport._tools(tools)`: function tools in the Responses shape; anything else is
/// left out.
pub fn tools(tools: Option<&[Json]>) -> Vec<Json> {
    let mut converted = Vec::new();
    for tool in tools.unwrap_or_default() {
        let Some(Json::Object(function)) = get(tool, "function") else { continue };
        let (Some(Json::String(name)), Some(Json::Object(parameters))) =
            (function.get("name"), function.get("parameters"))
        else {
            continue;
        };
        let mut item = JsonMap::new();
        item.insert("type".into(), Json::String("function".into()));
        item.insert("name".into(), Json::String(name.clone()));
        item.insert(
            "description".into(),
            Json::String(function.get("description").map_or_else(String::new, py_str)),
        );
        item.insert("parameters".into(), Json::Object(parameters.clone()));
        if let Some(strict) = function.get("strict") {
            item.insert("strict".into(), strict.clone());
        }
        converted.push(Json::Object(item));
    }
    converted
}

/// `request.setdefault("text", {})`: the request's `text` object, which an earlier keyword may
/// have set to something that is not one (Python then fails on the item assignment).
fn text_object(request: &mut JsonMap) -> ResponsesResult<&mut JsonMap> {
    let text = request.entry("text").or_insert_with(|| Json::Object(JsonMap::new()));
    let kind = crate::pyobj::type_name(text);
    text.as_object_mut()
        .ok_or_else(|| Incidental(format!("'{kind}' object does not support item assignment")).into())
}

/// `ResponsesTransport.format_request(model_id, messages, tools, stream, **kwargs)`.
pub fn format_request(
    model_id: &str,
    messages: &[Json],
    tool_list: Option<&[Json]>,
    stream: bool,
    kwargs: &JsonMap,
) -> ResponsesResult<JsonMap> {
    let mut request = JsonMap::new();
    request.insert("model".into(), Json::String(model_id.into()));
    request.insert("input".into(), Json::Array(input(messages)?));
    request.insert("stream".into(), Json::Bool(stream));
    let converted = tools(tool_list);
    if !converted.is_empty() {
        request.insert("tools".into(), Json::Array(converted));
    }
    for (key, value) in kwargs {
        match key.as_str() {
            "max_tokens" => {
                request.insert("max_output_tokens".into(), value.clone());
            }
            "tool_choice" if matches!(get(value, "function"), Some(Json::Object(_))) => {
                let name = get(get(value, "function").expect("checked"), "name")
                    .ok_or_else(|| ChatError::Incidental(Incidental("'name'".into())))?;
                let mut choice = JsonMap::new();
                choice.insert("type".into(), Json::String("function".into()));
                choice.insert("name".into(), name.clone());
                request.insert(key.clone(), Json::Object(choice));
            }
            "response_format" if value.is_object() => {
                let format_type = get(value, "type");
                if format_type == Some(&Json::String("json_schema".into()))
                    && matches!(get(value, "json_schema"), Some(Json::Object(_)))
                {
                    let mut format = JsonMap::new();
                    format.insert("type".into(), Json::String("json_schema".into()));
                    for (name, item) in get(value, "json_schema").and_then(Json::as_object).expect("checked")
                    {
                        format.insert(name.clone(), item.clone());
                    }
                    text_object(&mut request)?.insert("format".into(), Json::Object(format));
                } else if matches!(format_type, Some(Json::String(kind)) if kind == "json_object" || kind == "text")
                {
                    text_object(&mut request)?.insert("format".into(), value.clone());
                } else {
                    return Err(ChatError::Provider(ProviderError::new(
                        ErrorKind::InvalidRequest,
                        "Unsupported Responses output format",
                    )));
                }
            }
            "text" if value.is_object() => {
                let text = text_object(&mut request)?;
                for (name, item) in value.as_object().expect("checked") {
                    text.insert(name.clone(), item.clone());
                }
            }
            "messages" | "input" | "stream_options" => {}
            _ => {
                request.insert(key.clone(), value.clone());
            }
        }
    }
    Ok(request)
}

/// `AsyncResponses.create`'s keywords, in its body's order.
pub const CREATE_PARAMETERS: [&str; 31] = [
    "background",
    "context_management",
    "conversation",
    "include",
    "input",
    "instructions",
    "max_output_tokens",
    "max_tool_calls",
    "metadata",
    "model",
    "moderation",
    "parallel_tool_calls",
    "previous_response_id",
    "prompt",
    "prompt_cache_key",
    "prompt_cache_options",
    "prompt_cache_retention",
    "reasoning",
    "safety_identifier",
    "service_tier",
    "store",
    "stream",
    "stream_options",
    "temperature",
    "text",
    "tool_choice",
    "tools",
    "top_logprobs",
    "top_p",
    "truncation",
    "user",
];

/// `client.responses.create(**request)`: the body in the SDK's order, `extra_body` merged over
/// it, and the SDK's own options. An unknown keyword is Python's `TypeError`.
pub fn sdk_request(request: &JsonMap) -> Result<SdkRequest, Incidental> {
    let mut extra_headers = None;
    let mut extra_query = None;
    let mut extra_body = None;
    let mut timeout = None;
    for (key, value) in request {
        match key.as_str() {
            "extra_headers" => extra_headers = Some(value.clone()),
            "extra_query" => extra_query = Some(value.clone()),
            "extra_body" => extra_body = Some(value.clone()),
            "timeout" => timeout = Some(value.clone()),
            other if CREATE_PARAMETERS.contains(&other) => {}
            other => {
                return Err(Incidental(format!(
                    "AsyncResponses.create() got an unexpected keyword argument '{other}'"
                )))
            }
        }
    }
    let mut body = JsonMap::new();
    for name in CREATE_PARAMETERS {
        if let Some(value) = request.get(name) {
            body.insert(name.into(), value.clone());
        }
    }
    match extra_body {
        None | Some(Json::Null) => {}
        Some(Json::Object(extra)) => {
            for (key, value) in extra {
                body.insert(key, value);
            }
        }
        Some(other) => {
            return Err(Incidental(format!("'{}' object is not a mapping", crate::pyobj::type_name(&other))));
        }
    }
    Ok(SdkRequest { body, extra_headers, extra_query, timeout })
}

// ── The SDK's models ──────────────────────────────────────────────────────────────────────

/// The locked OpenAI SDK's Responses models (`sdk_models`).
fn table() -> &'static Table {
    static TABLE: OnceLock<Table> = OnceLock::new();
    TABLE.get_or_init(|| Table::parse(include_str!("responses_models.json")))
}

/// The SDK's `openai` and `pydantic` versions the table was made from.
pub fn model_table_versions() -> (String, String) {
    (table().version("openai").into(), table().version("pydantic").into())
}

static NULL: Json = Json::Null;

/// `native_output(items)` on `items` constructed as a list of output items.
fn native_output_of(items: Option<Node<'_>>) -> ResponsesResult<Vec<Json>> {
    match items.and_then(|node| node.items()) {
        Some(items) => retained(items),
        None => Ok(Vec::new()),
    }
}

fn retained(items: Vec<Node<'_>>) -> ResponsesResult<Vec<Json>> {
    let mut result = Vec::new();
    for item in items {
        // A non-object item is neither a mapping nor an SDK model: `vars(item)` raises.
        if !item.value.is_object() {
            return Err(Incidental("vars() argument must have __dict__ attribute".into()).into());
        }
        let value = item.dump();
        if !matches!(get(&value, "type"), Some(Json::String(_))) {
            return Err(invalid("Responses output item is malformed"));
        }
        result.push(value);
    }
    Ok(result)
}

/// `native_output(items)`: complete Responses output items, opaque reasoning included, for
/// private storage, from a JSON list of output items.
pub fn native_output(items: Option<&Json>) -> ResponsesResult<Vec<Json>> {
    match items.and_then(|items| Node::list_of(table(), items, "output_item")) {
        Some(items) => retained(items),
        None => Ok(Vec::new()),
    }
}

/// `responses_finish(response)`: only output-budget exhaustion is a length finish; a failure
/// never becomes success.
fn finish_of(response: Option<Node<'_>>) -> ResponsesResult<String> {
    let status = response.and_then(|response| response.attr("status")).map(|node| node.value);
    match status {
        Some(Json::String(status)) if status == "completed" => Ok("stop".into()),
        Some(Json::String(status)) if status == "incomplete" => {
            let reason = response
                .and_then(|response| response.attr("incomplete_details"))
                .and_then(|details| details.attr("reason"))
                .map(|node| node.value);
            if reason == Some(&Json::String("max_output_tokens".into())) {
                Ok("length".into())
            } else {
                Err(invalid("Responses output is incomplete for a reason other than its output limit"))
            }
        }
        Some(Json::String(status)) if status == "failed" => Err(ChatError::Provider(ProviderError::new(
            ErrorKind::Unavailable,
            "Provider returned a failed Responses result",
        ))),
        _ => Err(invalid("Responses result has no valid terminal status")),
    }
}

/// `responses_finish(response)` on a JSON `Response`.
pub fn responses_finish(response: Option<&Json>) -> ResponsesResult<String> {
    finish_of(response.map(|value| Node::root(table(), value, "response")))
}

/// `_responses_reasoning_effort(response)`: the effort the Responses result says it used, if it
/// is a native setting name; absence is not confirmation of the request.
fn reasoning_effort(response: Option<Node<'_>>) -> Option<String> {
    let value = response?.attr("reasoning")?.attr("effort")?.value;
    if value.is_null() {
        return None;
    }
    let advertised = crate::reasoning::Advertised::List(vec![value.as_str()]);
    match crate::reasoning::reasoning_values(&advertised, None) {
        Ok((Some(supported), _)) => supported.into_iter().next(),
        _ => None,
    }
}

/// `_responses_service_tier(response)`: the tier the result says it used.
fn service_tier(response: Option<Node<'_>>) -> Option<String> {
    match response?.attr("service_tier")?.value {
        Json::String(tier) if !tier.is_empty() && tier.chars().count() <= 32 && tier.is_ascii() => {
            Some(tier.clone())
        }
        _ => None,
    }
}

/// `for x in value` over a value that is not a list: a string or dict iterates (by character or
/// key, whose `.type` then raises `AttributeError`, so `getattr` gives `None`), an empty one gives
/// nothing, and anything else raises `TypeError`.
enum Iteration<'a> {
    Items(Vec<Node<'a>>),
    AttributeError,
}

fn iterate<'a>(node: Node<'a>) -> ResponsesResult<Iteration<'a>> {
    if let Some(items) = node.items() {
        return Ok(Iteration::Items(items));
    }
    match node.value {
        Json::String(text) if text.is_empty() => Ok(Iteration::Items(Vec::new())),
        Json::Object(map) if map.is_empty() => Ok(Iteration::Items(Vec::new())),
        Json::String(_) | Json::Object(_) => Ok(Iteration::AttributeError),
        other => {
            Err(Incidental(format!("'{}' object is not iterable", crate::pyobj::type_name(other))).into())
        }
    }
}

/// `Response.output_text`: the `output_text` parts of `message` items, joined. `None` where the
/// SDK property raises `AttributeError` (which `getattr(..., None)` turns into `None`); its
/// `TypeError`s escape.
fn output_text(response: Node<'_>) -> ResponsesResult<Option<String>> {
    let Some(output) = response.attr("output") else { return Ok(None) };
    let items = match iterate(output)? {
        Iteration::Items(items) => items,
        Iteration::AttributeError => return Ok(None),
    };
    let mut texts: Vec<&Json> = Vec::new();
    for item in items {
        let Some(kind) = item.attr("type") else { return Ok(None) };
        if kind.value != &Json::String("message".into()) {
            continue;
        }
        let Some(content) = item.attr("content") else { return Ok(None) };
        let parts = match iterate(content)? {
            Iteration::Items(parts) => parts,
            Iteration::AttributeError => return Ok(None),
        };
        for part in parts {
            let Some(part_kind) = part.attr("type") else { return Ok(None) };
            if part_kind.value != &Json::String("output_text".into()) {
                continue;
            }
            let Some(text) = part.attr("text") else { return Ok(None) };
            texts.push(text.value);
        }
    }
    // `"".join(texts)` raises for anything that is not a `str`.
    let mut joined = String::new();
    for (index, text) in texts.iter().enumerate() {
        match text {
            Json::String(text) => joined.push_str(text),
            other => {
                return Err(Incidental(format!(
                    "sequence item {index}: expected str instance, {} found",
                    crate::pyobj::type_name(other)
                ))
                .into())
            }
        }
    }
    Ok(Some(joined))
}

/// `int(getattr(value, name, 0) or 0)`.
fn count(value: Option<Node<'_>>, name: &str) -> ResponsesResult<i64> {
    let zero = Json::from(0);
    let held = value.map_or(&zero, |node| node.attr_or(name, &zero));
    Ok(int_or_zero(held)?)
}

/// `str(getattr(item, "call_id", None) or getattr(item, "id", ""))`.
fn call_identity(item: Node<'_>) -> String {
    match item.attr("call_id").filter(|node| truthy(node.value)) {
        Some(node) => py_str(node.value),
        None => item.attr("id").map_or_else(String::new, |node| py_str(node.value)),
    }
}

/// `ResponsesTransport.parse_response(raw_response, model_id)`.
pub fn parse_response(raw: &Json, model_id: &str) -> ResponsesResult<ProviderResponse> {
    // A body that is not an object is not constructed: every attribute is its default.
    let response = raw.is_object().then(|| Node::root(table(), raw, "response"));
    let text = match response {
        Some(response) => output_text(response)?.unwrap_or_default(),
        None => String::new(),
    };
    let output = response.and_then(|response| response.attr("output"));
    let mut tool_calls = Vec::new();
    for item in output.and_then(|output| output.items()).unwrap_or_default() {
        if item.attr("type").map(|node| node.value) != Some(&Json::String("function_call".into())) {
            continue;
        }
        let name = item.attr("name").map_or_else(String::new, |node| py_str(node.value));
        let arguments = match item.attr("arguments") {
            Some(node) if truthy(node.value) => py_str(node.value),
            _ => "{}".to_string(),
        };
        tool_calls.push(ToolCall::new(call_identity(item), name, arguments));
    }
    let usage = response.and_then(|response| response.attr("usage")).filter(|usage| !usage.is_none());
    let prompt_tokens = count(usage, "input_tokens")?;
    let completion_tokens = count(usage, "output_tokens")?;
    let details =
        usage.and_then(|usage| usage.attr("output_tokens_details")).filter(|details| !details.is_none());
    let reasoning_tokens = match details {
        Some(details) => Some(count(Some(details), "reasoning_tokens")?),
        None => None,
    };
    let finish = finish_of(response)?;
    let retained = native_output_of(output)?;
    let cache_usage =
        crate::chat::reported_cache_usage(usage.map_or(&NULL, |usage| usage.value), prompt_tokens)?;
    Ok(ProviderResponse {
        chat_completion_metadata: metadata(retained),
        content: if text.is_empty() { None } else { Some(text) },
        provider_reported_reasoning_effort: reasoning_effort(response),
        provider_reported_service_tier: service_tier(response),
        tool_calls,
        usage: TokenUsage::from_ints(
            prompt_tokens,
            completion_tokens,
            prompt_tokens + completion_tokens,
            reasoning_tokens,
        ),
        model: model_id.to_owned(),
        finish_reason: PyValue::from(finish.as_str()),
        usage_reported: Some(usage.is_some()),
        cache_usage,
        ..ProviderResponse::default()
    })
}

/// `{"responses_output": retained} if retained else None`.
fn metadata(retained: Vec<Json>) -> Option<JsonMap> {
    if retained.is_empty() {
        return None;
    }
    let mut map = JsonMap::new();
    map.insert("responses_output".into(), Json::Array(retained));
    Some(map)
}

/// `_stream_usage(raw_usage)` as Python runs it on a Responses usage object: `getattr(usage,
/// "prompt_tokens", getattr(usage, "input_tokens", 0))` and the like, where an undeclared name
/// falls back and a declared one the JSON lacks reads as `None`.
fn stream_usage_of(usage: Node<'_>) -> ResponsesResult<(TokenUsage, CacheUsage)> {
    let zero = Json::from(0);
    let either = |first: &str, second: &str| {
        usage.attr(first).map(|node| node.value).unwrap_or_else(|| usage.attr_or(second, &zero))
    };
    let prompt_tokens = int_or_zero(either("prompt_tokens", "input_tokens"))?;
    let completion_tokens = int_or_zero(either("completion_tokens", "output_tokens"))?;
    let details = usage
        .attr("completion_tokens_details")
        .filter(|node| truthy(node.value))
        .or_else(|| usage.attr("output_tokens_details"))
        .filter(|node| !node.is_none());
    let total = match usage.attr("total_tokens") {
        Some(node) => int_or_zero(node.value)?,
        None => prompt_tokens
            .checked_add(completion_tokens)
            .ok_or_else(|| Incidental(crate::pyobj::COUNT_OVERFLOW.into()))?,
    };
    let reasoning = match details {
        Some(details) => Some(int_or_zero(details.attr_or("reasoning_tokens", &zero))?),
        None => None,
    };
    let cache = crate::chat::reported_cache_usage(usage.value, prompt_tokens)?;
    Ok((TokenUsage::from_ints(prompt_tokens, completion_tokens, total, reasoning), cache))
}

// ── Streams ───────────────────────────────────────────────────────────────────────────────

fn event_type<'a>(event: Node<'a>) -> &'a str {
    event.attr("type").and_then(|node| node.value.as_str()).unwrap_or("")
}

/// `ResponsesTransport.parse_stream_chunk(raw_chunk)` on an event constructed as the SDK's
/// `ResponseStreamEvent`.
fn parse_event(event: Node<'_>) -> ResponsesResult<Option<ProviderStreamChunk>> {
    let delta_text = || event.attr("delta").map_or_else(String::new, |node| py_str(node.value));
    let zero = Json::from(0);
    match event_type(event) {
        "response.output_text.delta" => Ok(Some(ProviderStreamChunk::content(delta_text()))),
        "response.reasoning_text.delta" | "response.reasoning_summary_text.delta" => {
            Ok(Some(ProviderStreamChunk {
                reasoning_content: delta_text(),
                ..ProviderStreamChunk::default()
            }))
        }
        "response.output_item.added" => {
            let Some(item) = event.attr("item") else { return Ok(None) };
            if item.attr("type").map(|node| node.value) != Some(&Json::String("function_call".into())) {
                return Ok(None);
            }
            let index = int_or_zero(event.attr_or("output_index", &zero))?;
            let arguments = match item.attr("arguments") {
                Some(node) if truthy(node.value) => py_str(node.value),
                _ => String::new(),
            };
            let mut function = JsonMap::new();
            function.insert(
                "name".into(),
                Json::String(item.attr("name").map_or_else(String::new, |n| py_str(n.value))),
            );
            function.insert("arguments".into(), Json::String(arguments));
            let mut delta = JsonMap::new();
            delta.insert("index".into(), Json::from(index));
            delta.insert("id".into(), Json::String(call_identity(item)));
            delta.insert("function".into(), Json::Object(function));
            Ok(Some(ProviderStreamChunk::tool_call(Json::Object(delta))))
        }
        "response.function_call_arguments.delta" => {
            let index = int_or_zero(event.attr_or("output_index", &zero))?;
            let mut function = JsonMap::new();
            function.insert("name".into(), Json::String(String::new()));
            function.insert("arguments".into(), Json::String(delta_text()));
            let mut delta = JsonMap::new();
            delta.insert("index".into(), Json::from(index));
            delta.insert("id".into(), Json::String(String::new()));
            delta.insert("function".into(), Json::Object(function));
            Ok(Some(ProviderStreamChunk::tool_call(Json::Object(delta))))
        }
        "error" => Err(ChatError::Provider(ProviderError::new(
            ErrorKind::Unavailable,
            "Provider returned a Responses stream error",
        ))),
        "response.completed" | "response.incomplete" | "response.failed" => {
            let response = event.attr("response").filter(|node| !node.is_none());
            let finish = finish_of(response)?;
            let retained = native_output_of(response.and_then(|response| response.attr("output")))?;
            Ok(Some(ProviderStreamChunk {
                finish_reason: Some(PyValue::from(finish.as_str())),
                chat_completion_metadata: metadata(retained),
                provider_reported_reasoning_effort: reasoning_effort(response),
                provider_reported_service_tier: service_tier(response),
                ..ProviderStreamChunk::default()
            }))
        }
        _ => Ok(None),
    }
}

/// `ResponsesTransport.parse_stream_chunk(raw_chunk)`: one SDK stream event, from its JSON.
pub fn parse_stream_chunk(event: &Json) -> ResponsesResult<Option<ProviderStreamChunk>> {
    parse_event(Node::root(table(), event, "stream_event"))
}

/// The per-stream work `OpenAICompatProvider.generate_stream` does for the Responses transport:
/// usage capture (also from the terminal events' response), the private output-item chunks, the
/// reported effort and tier, the inline-reasoning filter and terminal tracking.
#[derive(Debug, Clone)]
pub struct ResponsesStreamState {
    filter: crate::chat::InlineReasoningFilter,
    /// The last usage reported, with its cache counts.
    pub usage: Option<(TokenUsage, CacheUsage)>,
    /// The last reasoning effort a chunk reported.
    pub reported_effort: Option<String>,
    /// The last service tier a chunk reported.
    pub reported_tier: Option<String>,
    terminal_seen: bool,
}

impl Default for ResponsesStreamState {
    fn default() -> Self {
        Self::new()
    }
}

impl ResponsesStreamState {
    /// The state at the start of one stream.
    pub fn new() -> Self {
        Self {
            filter: crate::chat::InlineReasoningFilter::new(),
            usage: None,
            reported_effort: None,
            reported_tier: None,
            terminal_seen: false,
        }
    }

    /// One SDK event in; the chunks to yield out.
    pub fn on_chunk(&mut self, raw: &Json) -> ResponsesResult<Vec<ProviderStreamChunk>> {
        let event = Node::root(table(), raw, "stream_event");
        let mut usage = event.attr("usage").filter(|node| !node.is_none());
        if usage.is_none()
            && matches!(event_type(event), "response.completed" | "response.failed" | "response.incomplete")
        {
            usage = event
                .attr("response")
                .and_then(|response| response.attr("usage"))
                .filter(|node| !node.is_none());
        }
        if let Some(usage) = usage {
            self.usage = Some(stream_usage_of(usage)?);
        }
        // `choices = getattr(chunk, "choices", None)`: only an extra field could carry it, and its
        // first item is then a plain value, whose `.delta` raises.
        if let Some(choices) = event.attr("choices").filter(|node| truthy(node.value)) {
            return Err(match choices.value {
                Json::Array(items) => Incidental::no_attribute(&items[0], "delta"),
                Json::String(_) => Incidental("'str' object has no attribute 'delta'".into()),
                Json::Object(_) => Incidental("0".into()),
                other => {
                    Incidental(format!("'{}' object is not subscriptable", crate::pyobj::type_name(other)))
                }
            }
            .into());
        }
        let mut out = Vec::new();
        if let Some(mut parsed) = parse_event(event)? {
            if parsed.finish_reason.is_some() {
                self.terminal_seen = true;
            }
            if let Some(metadata) = parsed.chat_completion_metadata.take() {
                out.push(ProviderStreamChunk {
                    chat_completion_metadata: Some(metadata),
                    ..ProviderStreamChunk::default()
                });
            }
            if parsed.provider_reported_reasoning_effort.is_some() {
                self.reported_effort.clone_from(&parsed.provider_reported_reasoning_effort);
            }
            if parsed.provider_reported_service_tier.is_some() {
                self.reported_tier.clone_from(&parsed.provider_reported_service_tier);
            }
            let filtered = self.filter.filter_chunk(parsed);
            if !filtered.content.is_empty()
                || !filtered.reasoning_content.is_empty()
                || filtered.tool_call_delta.is_some()
                || filtered.finish_reason.is_some()
            {
                out.push(filtered);
            }
        }
        Ok(out)
    }

    /// The terminal check: a Responses stream needs only its terminal event, not `[DONE]`.
    pub fn check_terminal(&self) -> ResponsesResult<()> {
        if !self.terminal_seen {
            return Err(ChatError::Provider(ProviderError::new(
                ErrorKind::IncompleteStream,
                "Provider stream ended before its terminal event",
            )));
        }
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn retained_items_are_dumped_in_the_sdks_declared_order() {
        let item = json!({"type": "message", "status": "completed", "role": "assistant", "id": "m1",
            "content": [{"type": "output_text", "text": "Hi", "annotations": [], "logprobs": null, "extra": 1}],
            "unknown": null, "later": "x"});
        let dumped = native_output(Some(&json!([item]))).unwrap();
        let keys: Vec<&str> = dumped[0].as_object().unwrap().keys().map(String::as_str).collect();
        assert_eq!(keys, ["id", "content", "role", "status", "type", "later"]);
        let part: Vec<&str> =
            dumped[0]["content"][0].as_object().unwrap().keys().map(String::as_str).collect();
        assert_eq!(part, ["annotations", "text", "type", "extra"]);
    }

    #[test]
    fn a_declared_field_the_json_lacks_reads_as_none() {
        let response = json!({"status": "completed", "output": [{"type": "function_call"}]});
        let parsed = parse_response(&response, "m").unwrap();
        assert_eq!(parsed.tool_calls, vec![ToolCall::new("None", "None", "{}")]);
        assert_eq!(responses_finish(Some(&json!({"status": "completed"}))).unwrap(), "stop");
        assert!(responses_finish(None).is_err());
    }
}
