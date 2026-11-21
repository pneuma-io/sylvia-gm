//! The Chat Completions transport: `ChatCompletionsTransport`, `_InlineReasoningFilter`, the
//! usage and reasoning readers of `provider/_openai_compat.py`, `ChatCompletionMetadata`
//! (`provider/_chat_metadata.py`), `split_chat_content` (`provider/_native_output.py`), and the
//! part of the OpenAI SDK that turns Sylvia's request into body bytes.
//!
//! Everything here is pure: bytes and JSON in, chunks, responses and errors out. The HTTP
//! client, timeouts, hooks and retries sit on top in the provider.
//!
//! Raw provider JSON is read with Python's semantics ([`crate::pyobj`]): the SDK keeps every
//! value as received, so a malformed field behaves as it does in Python, including the cases
//! where Python fails by accident of the data's shape ([`Incidental`]).

use crate::error::{is_context_rejection, ErrorKind, ProviderError};
use crate::pyobj::{
    attr, attr_or_none, int_or_zero, models, observed_count, py_eq, py_iter, py_str, truthy, Incidental,
};
use crate::types::{CacheUsage, ProviderResponse, ProviderStreamChunk, PyValue, TokenUsage, ToolCall};
use std::collections::BTreeSet;
use sylvia_base::{Json, JsonMap};

/// A failure while reading provider data: a provider error Python raises on purpose, or an
/// accidental Python exception that the surrounding `except Exception` turns into
/// `ProviderConnectionError(str(exc))` (and that `retry_with_backoff` does not retry).
#[derive(Debug, Clone, PartialEq)]
pub enum ChatError {
    Provider(ProviderError),
    Incidental(Incidental),
}

impl From<ProviderError> for ChatError {
    fn from(error: ProviderError) -> Self {
        Self::Provider(error)
    }
}

impl From<Incidental> for ChatError {
    fn from(error: Incidental) -> Self {
        Self::Incidental(error)
    }
}

impl ChatError {
    /// What the provider's `except Exception` makes of it: provider errors as they are, the rest
    /// as `ProviderConnectionError(str(exc))`.
    pub fn into_provider_error(self) -> ProviderError {
        match self {
            Self::Provider(error) => error,
            Self::Incidental(Incidental(message)) => ProviderError::connection(message, None),
        }
    }
}

type ChatResult<T> = Result<T, ChatError>;

fn invalid(message: &str) -> ChatError {
    ChatError::Provider(ProviderError::new(ErrorKind::InvalidResponse, message))
}

// ── Request ───────────────────────────────────────────────────────────────────────────────

/// `ChatCompletionsTransport.format_request`: `{"model", "messages", "stream", **kwargs}`, then
/// `tools` when there are any and `stream_options` for a stream unless the caller set it.
///
/// This dict is what hooks, the audit and the token estimate see; the wire body is
/// [`sdk_request`] of it.
pub fn format_request(
    model_id: &str,
    messages: &[Json],
    tools: Option<&[Json]>,
    stream: bool,
    kwargs: &JsonMap,
) -> JsonMap {
    let mut request = JsonMap::new();
    request.insert("model".into(), Json::String(model_id.into()));
    request.insert("messages".into(), Json::Array(messages.to_vec()));
    request.insert("stream".into(), Json::Bool(stream));
    for (key, value) in kwargs {
        request.insert(key.clone(), value.clone());
    }
    if let Some(tools) = tools.filter(|tools| !tools.is_empty()) {
        request.insert("tools".into(), Json::Array(tools.to_vec()));
    }
    if stream && !request.contains_key("stream_options") {
        let mut options = JsonMap::new();
        options.insert("include_usage".into(), Json::Bool(true));
        request.insert("stream_options".into(), Json::Object(options));
    }
    request
}

/// The keyword parameters of `AsyncCompletions.create` (openai 2.53.0), in the order the SDK
/// writes them into the body.
pub const CREATE_PARAMETERS: [&str; 37] = [
    "messages",
    "model",
    "audio",
    "frequency_penalty",
    "function_call",
    "functions",
    "logit_bias",
    "logprobs",
    "max_completion_tokens",
    "max_tokens",
    "metadata",
    "modalities",
    "moderation",
    "n",
    "parallel_tool_calls",
    "prediction",
    "presence_penalty",
    "prompt_cache_key",
    "prompt_cache_options",
    "prompt_cache_retention",
    "reasoning_effort",
    "response_format",
    "safety_identifier",
    "seed",
    "service_tier",
    "stop",
    "store",
    "stream",
    "stream_options",
    "temperature",
    "tool_choice",
    "tools",
    "top_logprobs",
    "top_p",
    "user",
    "verbosity",
    "web_search_options",
];

/// What `client.chat.completions.create(**request)` sends.
#[derive(Debug, Clone, PartialEq)]
pub struct SdkRequest {
    /// The JSON body, in the SDK's key order with `extra_body` merged over it.
    pub body: JsonMap,
    /// `extra_headers`, merged over the client's headers.
    pub extra_headers: Option<Json>,
    /// `extra_query`.
    pub extra_query: Option<Json>,
    /// `timeout`.
    pub timeout: Option<Json>,
}

impl SdkRequest {
    /// The body bytes: `json.dumps(body, ensure_ascii=False, separators=(",", ":"))`, as the
    /// SDK's `openapi_dumps` writes them.
    pub fn body_bytes(&self) -> Vec<u8> {
        sylvia_base::pyjson::dump_bytes(
            &Json::Object(self.body.clone()),
            sylvia_base::pyjson::Dump::WIRE.ascii(false),
        )
    }
}

/// `create(**request)`: sort the keywords into the body, the SDK's options and an error.
///
/// An unknown keyword is Python's `TypeError`, an [`Incidental`] failure: Python lets it escape
/// the provider unconverted, so the owner reports an internal error.
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
                    "AsyncCompletions.create() got an unexpected keyword argument '{other}'"
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

// ── Content and reasoning ─────────────────────────────────────────────────────────────────

/// `split_chat_content(content)`: visible text and Mistral `thinking` text.
pub fn split_chat_content(content: &Json) -> ChatResult<(String, String)> {
    match content {
        Json::Null => Ok((String::new(), String::new())),
        Json::String(text) => Ok((text.clone(), String::new())),
        Json::Array(blocks) => {
            let (mut visible, mut private) = (String::new(), String::new());
            for block in blocks {
                let Some(block) = block.as_object() else {
                    return Err(invalid("Provider content block must be an object"));
                };
                let kind = block.get("type").and_then(Json::as_str);
                if kind == Some("text") {
                    if let Some(text) = block.get("text").and_then(Json::as_str) {
                        visible.push_str(text);
                        continue;
                    }
                }
                if kind == Some("thinking") {
                    if let Some(parts) = block.get("thinking").and_then(Json::as_array) {
                        for part in parts {
                            if let Some(text) =
                                part.as_object().and_then(|p| p.get("text")).and_then(Json::as_str)
                            {
                                private.push_str(text);
                            }
                        }
                    }
                }
            }
            Ok((visible, private))
        }
        _ => Err(invalid("Provider content must be text or content blocks")),
    }
}

/// `_chat_reasoning(message)`: `reasoning_content`, then `reasoning`, then the text of
/// `reasoning_details`. `message` is the SDK delta or message model (`declared` fields).
pub fn chat_reasoning(message: &Json, declared: &[&str]) -> ChatResult<String> {
    for field in ["reasoning_content", "reasoning"] {
        let value = attr_or_none(message, declared, field);
        if value.is_null() || value.as_str() == Some("") {
            continue;
        }
        return match value.as_str() {
            Some(text) => Ok(text.to_owned()),
            None => Err(invalid("Provider reasoning channel must be text")),
        };
    }
    let details = attr_or_none(message, declared, "reasoning_details");
    let mut out = String::new();
    if let Some(items) = details.as_array() {
        for item in items {
            let Some(item) = item.as_object() else { continue };
            let kind = item.get("type").and_then(Json::as_str);
            if !matches!(kind, Some("reasoning.text") | Some("reasoning.summary")) {
                continue;
            }
            // item.get("text", item.get("summary", ""))
            let text = match item.get("text") {
                Some(text) => Some(text),
                None => item.get("summary"),
            };
            match text {
                None => {}
                Some(Json::String(text)) => out.push_str(text),
                Some(_) => {}
            }
        }
    }
    Ok(out)
}

/// `str.strip()` on a Python string.
fn strip(text: &str) -> &str {
    sylvia_base::pystr::strip(text)
}

// ── Inline reasoning markup ───────────────────────────────────────────────────────────────

const MARKERS: [(&str, Option<bool>); 20] = [
    ("<|channel|>analysis", Some(true)),
    ("<|channel|>thought", Some(true)),
    ("<|channel|>reasoning", Some(true)),
    ("<thinking>", Some(true)),
    ("<reasoning>", Some(true)),
    ("<think>", Some(true)),
    ("<mm:think>", Some(true)),
    ("<thought>", Some(true)),
    ("<reasoning_scratchpad>", Some(true)),
    ("<|channel|>final", Some(false)),
    ("<|channel|>commentary", Some(false)),
    ("</thinking>", Some(false)),
    ("</reasoning>", Some(false)),
    ("</think>", Some(false)),
    ("</mm:think>", Some(false)),
    ("</thought>", Some(false)),
    ("</reasoning_scratchpad>", Some(false)),
    ("<|start|>assistant", None),
    ("<|message|>", None),
    ("<|end|>", None),
];

/// Python `text[start:end]` by code points, with Python's clamping.
fn char_slice(text: &[char], start: isize, end: Option<isize>) -> String {
    let len = text.len() as isize;
    let clamp = |index: isize| if index < 0 { (len + index).max(0) } else { index.min(len) };
    let (from, to) = (clamp(start), end.map_or(len, clamp));
    if from >= to {
        return String::new();
    }
    text[from as usize..to as usize].iter().collect()
}

fn find_chars(haystack: &[char], needle: &[char]) -> Option<usize> {
    if needle.is_empty() {
        return Some(0);
    }
    haystack.windows(needle.len()).position(|window| window == needle)
}

/// `_InlineReasoningFilter`: moves reasoning channel markup that some local servers put in
/// `content` to the reasoning channel, holding back a marker split across chunks.
///
/// Python finds markers in the *casefolded* buffer and slices the *original* buffer with those
/// indexes. Where casefolding changes the length (`ß` becomes `ss`), the slices are off by the
/// difference. That is ported as it is.
#[derive(Debug, Clone, Default)]
pub struct InlineReasoningFilter {
    buffer: Vec<char>,
    private: bool,
}

impl InlineReasoningFilter {
    /// A filter at the start of a stream.
    pub fn new() -> Self {
        Self::default()
    }

    /// `feed(text, final=...)`: the visible and private text ready so far.
    pub fn feed(&mut self, text: &str, final_: bool) -> (String, String) {
        self.buffer.extend(text.chars());
        let (mut visible, mut private) = (String::new(), String::new());
        while !self.buffer.is_empty() {
            let buffer: String = self.buffer.iter().collect();
            let lowered: Vec<char> = sylvia_base::pystr::casefold(&buffer).chars().collect();
            let mut found: Option<(usize, usize, Option<bool>)> = None;
            for (marker, mode) in MARKERS {
                let folded: Vec<char> = sylvia_base::pystr::casefold(marker).chars().collect();
                if let Some(index) = find_chars(&lowered, &folded) {
                    if found.is_none_or(|(best, _, _)| index < best) {
                        found = Some((index, marker.chars().count(), mode));
                    }
                }
            }
            if let Some((index, marker_len, mode)) = found {
                let before = char_slice(&self.buffer, 0, Some(index as isize));
                self.append(&before, &mut visible, &mut private);
                self.buffer = char_slice(&self.buffer, (index + marker_len) as isize, None).chars().collect();
                if let Some(mode) = mode {
                    self.private = mode;
                }
                continue;
            }
            let held = if final_ { 0 } else { partial_marker_suffix(&lowered) };
            let ready =
                if held == 0 { buffer.clone() } else { char_slice(&self.buffer, 0, Some(-(held as isize))) };
            self.append(&ready, &mut visible, &mut private);
            self.buffer = if held == 0 {
                Vec::new()
            } else {
                char_slice(&self.buffer, -(held as isize), None).chars().collect()
            };
            break;
        }
        (visible, private)
    }

    /// `filter_chunk(chunk)`: final when the chunk finishes or carries a tool-call fragment.
    pub fn filter_chunk(&mut self, mut chunk: ProviderStreamChunk) -> ProviderStreamChunk {
        let final_ = chunk.finish_reason.is_some() || chunk.tool_call_delta.is_some();
        let (content, inline) = self.feed(&chunk.content, final_);
        chunk.content = content;
        chunk.reasoning_content.push_str(&inline);
        chunk
    }

    fn append(&self, text: &str, visible: &mut String, private: &mut String) {
        if text.is_empty() {
            return;
        }
        if self.private { private } else { visible }.push_str(text);
    }
}

fn partial_marker_suffix(lowered: &[char]) -> usize {
    let longest = MARKERS.iter().map(|(marker, _)| marker.chars().count()).max().unwrap_or(1);
    let max_length = lowered.len().min(longest - 1);
    for length in (1..=max_length).rev() {
        let suffix: String = lowered[lowered.len() - length..].iter().collect();
        if MARKERS.iter().any(|(marker, _)| sylvia_base::pystr::casefold(marker).starts_with(&suffix)) {
            return length;
        }
    }
    0
}

// ── Continuation metadata ─────────────────────────────────────────────────────────────────

#[derive(Debug, Clone, PartialEq)]
enum DetailKey {
    Int(i128),
    Str(String),
}

/// `ChatCompletionMetadata`: reassembles reasoning blocks and tool signatures without
/// interpreting them, for private continuation storage.
#[derive(Debug, Clone, Default)]
pub struct ChatCompletionMetadata {
    preserve_reasoning: bool,
    preserve_content: bool,
    reasoning: JsonMap,
    content: Vec<Json>,
    details: Vec<(DetailKey, JsonMap)>,
    calls: Vec<(i128, String, Option<JsonMap>)>,
}

impl ChatCompletionMetadata {
    /// `ChatCompletionMetadata(preserve_reasoning=..., preserve_content=...)`.
    pub fn new(preserve_reasoning: bool, preserve_content: bool) -> Self {
        Self { preserve_reasoning, preserve_content, ..Self::default() }
    }

    /// `add(message)` for an SDK delta or message (`declared` fields).
    pub fn add(&mut self, message: &Json, declared: &[&str]) -> ChatResult<()> {
        let get = |name: &str| attr_or_none(message, declared, name);
        if self.preserve_reasoning {
            for field in ["reasoning", "reasoning_content"] {
                let value = get(field);
                if value.is_null() {
                    continue;
                }
                let Some(text) = value.as_str() else {
                    return Err(invalid("Provider reasoning channel must be text"));
                };
                let joined =
                    format!("{}{text}", self.reasoning.get(field).and_then(Json::as_str).unwrap_or(""));
                self.reasoning.insert(field.into(), Json::String(joined));
            }
        }
        if self.preserve_content {
            match get("content") {
                Json::Array(blocks) => self.content.extend(blocks.iter().cloned()),
                Json::String(text) if !text.is_empty() => {
                    let mut block = JsonMap::new();
                    block.insert("type".into(), Json::String("text".into()));
                    block.insert("text".into(), Json::String(text.clone()));
                    self.content.push(Json::Object(block));
                }
                _ => {}
            }
        }
        for (position, detail) in py_iter(get("reasoning_details"))?.into_iter().enumerate() {
            let Some(detail) = detail.as_object() else {
                return Err(invalid("Provider reasoning detail must be an object"));
            };
            let raw_key = match detail.get("index") {
                Some(index) if !index.is_null() => index.clone(),
                _ => match detail.get("id") {
                    Some(id) if truthy(id) => id.clone(),
                    _ => Json::from(position),
                },
            };
            let key = match &raw_key {
                Json::String(text) => DetailKey::Str(text.clone()),
                other => match crate::tool_delta::python_int(other) {
                    Some(number) => DetailKey::Int(number),
                    None => return Err(invalid("Provider reasoning detail identity is invalid")),
                },
            };
            let slot = match self.details.iter().position(|(existing, _)| *existing == key) {
                Some(slot) => slot,
                None => {
                    self.details.push((key, JsonMap::new()));
                    self.details.len() - 1
                }
            };
            let existing = &mut self.details[slot].1;
            for (field, value) in detail {
                if value.is_null() {
                    existing.entry(field.clone()).or_insert(Json::Null);
                } else if matches!(field.as_str(), "text" | "summary" | "data" | "signature") {
                    let Some(text) = value.as_str() else {
                        return Err(invalid("Provider reasoning fragment must be text"));
                    };
                    let before = existing.get(field).and_then(Json::as_str).unwrap_or("");
                    let joined = format!("{before}{text}");
                    existing.insert(field.clone(), Json::String(joined));
                } else if existing.get(field).is_none_or(Json::is_null) {
                    existing.insert(field.clone(), value.clone());
                } else if !py_eq(&existing[field], value) {
                    return Err(invalid("Provider reasoning detail identity changed"));
                }
            }
        }
        for (position, call) in py_iter(get("tool_calls"))?.into_iter().enumerate() {
            // Elements that are dicts are SDK tool-call models; others are kept as they are.
            let call_fields = tool_call_fields(&call, declared);
            let index_value = if call.is_object() {
                attr(&call, call_fields, "index").cloned().unwrap_or_else(|| Json::from(position))
            } else if call.is_string() || call.is_array() {
                // str.index and list.index are methods, not integers.
                return Err(invalid("Provider tool metadata index is invalid"));
            } else {
                Json::from(position)
            };
            let index = match &index_value {
                Json::Bool(_) | Json::Number(_) => crate::tool_delta::python_int(&index_value),
                _ => None,
            }
            .filter(|index| *index >= 0)
            .ok_or_else(|| invalid("Provider tool metadata index is invalid"))?;
            let slot = match self.calls.iter().position(|(existing, _, _)| *existing == index) {
                Some(slot) => slot,
                None => {
                    self.calls.push((index, String::new(), None));
                    self.calls.len() - 1
                }
            };
            let id = if call.is_object() { attr_or_none(&call, call_fields, "id") } else { &Json::Null };
            if let Some(id) = id.as_str().filter(|id| !id.is_empty()) {
                let current = &mut self.calls[slot].1;
                if !current.is_empty() && current != id {
                    return Err(invalid("Provider tool metadata identity changed"));
                }
                *current = id.to_owned();
            }
            let extra = if call.is_object() {
                attr_or_none(&call, call_fields, "extra_content")
            } else {
                &Json::Null
            };
            if !extra.is_null() {
                let Some(extra) = extra.as_object() else {
                    return Err(invalid("Provider tool signature metadata is invalid"));
                };
                if let Some(previous) = &self.calls[slot].2 {
                    if !py_eq(&Json::Object(previous.clone()), &Json::Object(extra.clone())) {
                        return Err(invalid("Provider tool signature metadata changed"));
                    }
                }
                self.calls[slot].2 = Some(extra.clone());
            }
        }
        Ok(())
    }

    /// `payload()`: only private extensions, or `None` when there are none.
    pub fn payload(&self) -> ChatResult<Option<JsonMap>> {
        let mut result = JsonMap::new();
        if !self.reasoning.is_empty() {
            result.insert("reasoning_fields".into(), Json::Object(self.reasoning.clone()));
        }
        if !self.content.is_empty() {
            let mut thinking = false;
            for block in &self.content {
                let Some(block) = block.as_object() else {
                    return Err(ChatError::Incidental(Incidental::no_attribute(block, "get")));
                };
                thinking |= block.get("type").and_then(Json::as_str) == Some("thinking");
            }
            if thinking {
                result.insert("chat_content".into(), Json::Array(self.content.clone()));
            }
        }
        if !self.details.is_empty() {
            let values = self.details.iter().map(|(_, value)| Json::Object(value.clone())).collect();
            result.insert("reasoning_details".into(), Json::Array(values));
        }
        let mut extras = JsonMap::new();
        for (_, id, extra) in &self.calls {
            if let Some(extra) = extra {
                if id.is_empty() {
                    return Err(invalid("Provider tool signature has no call identity"));
                }
                extras.insert(id.clone(), Json::Object(extra.clone()));
            }
        }
        if !extras.is_empty() {
            result.insert("tool_call_extras".into(), Json::Object(extras));
        }
        Ok((!result.is_empty()).then_some(result))
    }
}

/// The SDK model of one tool call inside a delta (`ChoiceDeltaToolCall`) or a complete message
/// (`ChatCompletionMessageFunctionToolCall`, or the custom one when `type` is `custom`).
fn tool_call_fields(call: &Json, parent: &[&str]) -> &'static [&'static str] {
    if parent == models::DELTA {
        models::DELTA_TOOL_CALL
    } else if call.get("type").and_then(Json::as_str) == Some("custom") {
        models::CUSTOM_TOOL_CALL
    } else {
        models::FUNCTION_TOOL_CALL
    }
}

// ── Usage ─────────────────────────────────────────────────────────────────────────────────

/// A value Python holds as an SDK model (`Some(declared)`) or as a plain value (`None`).
type Held<'a> = (&'a Json, Option<&'static [&'static str]>);

fn model_or(value: &Json, declared: &'static [&'static str]) -> Option<&'static [&'static str]> {
    value.is_object().then_some(declared)
}

/// `a or b` where `a` may be a model (always truthy).
fn held_or<'a>(a: Held<'a>, b: Held<'a>) -> Held<'a> {
    if a.1.is_some() || truthy(a.0) {
        a
    } else {
        b
    }
}

/// `field(value, key)` from `_reported_cache_usage`: `.get` on a plain dict, `getattr` otherwise.
fn cache_field<'a>(held: Held<'a>, key: &str) -> Option<&'a Json> {
    match held.1 {
        Some(declared) => attr(held.0, declared, key),
        None => held.0.as_object().and_then(|map| map.get(key)),
    }
}

/// `_reported_cache_usage(raw_usage, prompt_tokens)`.
pub fn reported_cache_usage(raw_usage: &Json, prompt_tokens: i64) -> Result<CacheUsage, Incidental> {
    let usage: Held = (raw_usage, model_or(raw_usage, models::USAGE));
    let first = cache_field(usage, "prompt_tokens_details").unwrap_or(&Json::Null);
    let second = cache_field(usage, "input_tokens_details").unwrap_or(&Json::Null);
    let details = held_or((first, model_or(first, models::PROMPT_DETAILS)), (second, None));
    let read = observed_count(cache_field(details, "cached_tokens"))
        .or_else(|| observed_count(cache_field(usage, "cached_tokens")))
        .or_else(|| observed_count(cache_field(usage, "prompt_cache_hit_tokens")));
    let write = observed_count(cache_field(details, "cache_write_tokens"))
        .or_else(|| observed_count(cache_field(usage, "cache_write_tokens")));
    let miss = match read {
        None => None,
        Some(read) => Some(
            prompt_tokens
                .checked_sub(read)
                .ok_or_else(|| Incidental(crate::pyobj::COUNT_OVERFLOW.into()))?
                .max(0),
        ),
    };
    Ok(CacheUsage {
        cache_read_tokens: read,
        cache_write_tokens: write,
        cache_miss_tokens: miss,
        total_prompt_tokens: prompt_tokens,
    })
}

/// `_stream_usage(raw_usage)`: usage from a delta-bearing or usage-only chunk.
pub fn stream_usage(raw_usage: &Json) -> Result<(TokenUsage, CacheUsage), Incidental> {
    let get = |name: &str| attr(raw_usage, models::USAGE, name);
    let zero = Json::from(0);
    let prompt = int_or_zero(get("prompt_tokens").or_else(|| get("input_tokens")).unwrap_or(&zero))?;
    let completion = int_or_zero(get("completion_tokens").or_else(|| get("output_tokens")).unwrap_or(&zero))?;
    // getattr(raw_usage, "total_tokens", prompt + completion): the sum is used only without the
    // attribute, so only then can it overflow.
    let total = match get("total_tokens") {
        Some(value) => int_or_zero(value)?,
        None => {
            prompt.checked_add(completion).ok_or_else(|| Incidental(crate::pyobj::COUNT_OVERFLOW.into()))?
        }
    };
    let first = get("completion_tokens_details").unwrap_or(&Json::Null);
    let second = get("output_tokens_details").unwrap_or(&Json::Null);
    let details = held_or((first, model_or(first, models::COMPLETION_DETAILS)), (second, None));
    let reasoning = if details.0.is_null() {
        None
    } else {
        let value = match details.1 {
            Some(declared) => attr(details.0, declared, "reasoning_tokens").unwrap_or(&zero),
            None => &zero,
        };
        Some(int_or_zero(value)?)
    };
    let usage = TokenUsage::from_ints(prompt, completion, total, reasoning);
    Ok((usage, reported_cache_usage(raw_usage, prompt)?))
}

// ── Stream chunks ─────────────────────────────────────────────────────────────────────────

/// `choices[0]` of a value Python holds as it arrived.
fn first_choice(choices: &Json) -> Result<Json, Incidental> {
    match choices {
        Json::Array(items) => {
            items.first().cloned().ok_or_else(|| Incidental("list index out of range".into()))
        }
        Json::Object(_) => Err(Incidental("0".into())),
        Json::String(text) => text
            .chars()
            .next()
            .map(|c| Json::String(c.to_string()))
            .ok_or_else(|| Incidental("string index out of range".into())),
        other => Err(Incidental(format!("'{}' object is not subscriptable", crate::pyobj::type_name(other)))),
    }
}

/// `choice.<name>` (a direct attribute read) on a choice model.
fn choice_attr(choice: &Json, declared: &[&str], name: &str) -> Result<Json, Incidental> {
    attr(choice, declared, name).cloned().ok_or_else(|| Incidental::no_attribute(choice, name))
}

/// The delta of a chunk: `chunk.choices[0].delta if chunk.choices else None`.
fn chunk_delta(raw_chunk: &Json) -> Result<(Json, Json), Incidental> {
    let choices = attr(raw_chunk, models::CHUNK, "choices")
        .ok_or_else(|| Incidental::no_attribute(raw_chunk, "choices"))?;
    if !truthy(choices) {
        return Ok((Json::Null, Json::Null));
    }
    let choice = first_choice(choices)?;
    let delta = choice_attr(&choice, models::CHUNK_CHOICE, "delta")?;
    let finish = choice_attr(&choice, models::CHUNK_CHOICE, "finish_reason")?;
    Ok((delta, finish))
}

/// A stream chunk's finish reason: `None`, or whatever the provider sent, as Python keeps it.
fn finish_value(value: &Json) -> Option<PyValue> {
    (!value.is_null()).then(|| PyValue::from_json(value.clone()))
}

/// `ChatCompletionsTransport.parse_stream_chunk(raw_chunk)`.
pub fn parse_stream_chunk(raw_chunk: &Json) -> ChatResult<Option<ProviderStreamChunk>> {
    let (delta, finish) = chunk_delta(raw_chunk)?;
    if delta.is_null() {
        return Ok(None);
    }
    let content_value =
        attr(&delta, models::DELTA, "content").cloned().unwrap_or(Json::String(String::new()));
    let (mut content, block_reasoning) = split_chat_content(&content_value)?;
    let reasoning = chat_reasoning(&delta, models::DELTA)? + &block_reasoning;
    if !reasoning.is_empty() && !content.is_empty() {
        let r_stripped = strip(&reasoning);
        let c_stripped: Vec<char> = strip(&content).chars().collect();
        if !c_stripped.is_empty() {
            let prefix: String = c_stripped[..c_stripped.len().min(40)].iter().collect();
            if r_stripped.starts_with(&prefix) {
                content = String::new();
            }
        }
    }
    let raw_tool_calls = attr_or_none(&delta, models::DELTA, "tool_calls");
    let tool_call_delta = if truthy(raw_tool_calls) {
        match raw_tool_calls {
            Json::Array(items) => items.first().cloned().unwrap_or(Json::Null),
            other => other.clone(),
        }
    } else {
        Json::Null
    };
    // A dict element is an SDK model, which is always truthy.
    let has_tool = tool_call_delta.is_object() || truthy(&tool_call_delta);
    if content.is_empty() && reasoning.is_empty() && !has_tool && !truthy(&finish) {
        return Ok(None);
    }
    Ok(Some(ProviderStreamChunk {
        content,
        reasoning_content: reasoning,
        tool_call_delta: (!tool_call_delta.is_null()).then_some(tool_call_delta),
        finish_reason: finish_value(&finish),
        ..ProviderStreamChunk::default()
    }))
}

/// `parse_stream_chunks(raw_chunk)`: every tool-call fragment of a delta as its own chunk, the
/// finish reason only on the last.
pub fn parse_stream_chunks(raw_chunk: &Json) -> ChatResult<Vec<ProviderStreamChunk>> {
    let Some(first) = parse_stream_chunk(raw_chunk)? else {
        return Ok(Vec::new());
    };
    let (delta, _) = chunk_delta(raw_chunk)?;
    let calls = attr_or_none(&delta, models::DELTA, "tool_calls");
    let Some(calls) = calls.as_array().filter(|calls| calls.len() >= 2) else {
        return Ok(vec![first]);
    };
    let finish = first.finish_reason.clone();
    let mut chunks = vec![ProviderStreamChunk { finish_reason: None, ..first }];
    for call in &calls[1..] {
        chunks.push(ProviderStreamChunk {
            tool_call_delta: (!call.is_null()).then(|| call.clone()),
            ..ProviderStreamChunk::default()
        });
    }
    if let Some(last) = chunks.last_mut() {
        last.finish_reason = finish;
    }
    Ok(chunks)
}

/// The per-stream work `OpenAICompatProvider.generate_stream` does on each parsed SDK chunk:
/// usage capture, continuation metadata, the inline-reasoning filter and terminal tracking.
#[derive(Debug, Clone)]
pub struct ChatStreamState {
    filter: InlineReasoningFilter,
    metadata: ChatCompletionMetadata,
    /// The last usage reported, with its cache counts.
    pub usage: Option<(TokenUsage, CacheUsage)>,
    terminal_seen: bool,
}

impl ChatStreamState {
    /// The state at the start of one stream.
    pub fn new(preserve_reasoning: bool, preserve_content: bool) -> Self {
        Self {
            filter: InlineReasoningFilter::new(),
            metadata: ChatCompletionMetadata::new(preserve_reasoning, preserve_content),
            usage: None,
            terminal_seen: false,
        }
    }

    /// One SDK chunk in; the chunks to yield out.
    pub fn on_chunk(&mut self, raw_chunk: &Json) -> ChatResult<Vec<ProviderStreamChunk>> {
        if let Some(raw_usage) = attr(raw_chunk, models::CHUNK, "usage").filter(|usage| !usage.is_null()) {
            self.usage = Some(stream_usage(raw_usage)?);
        }
        let choices = attr_or_none(raw_chunk, models::CHUNK, "choices");
        if truthy(choices) {
            let choice = first_choice(choices)?;
            let delta = choice_attr(&choice, models::CHUNK_CHOICE, "delta")?;
            self.metadata.add(&delta, models::DELTA)?;
        }
        let mut out = Vec::new();
        for parsed in parse_stream_chunks(raw_chunk)? {
            if parsed.finish_reason.is_some() {
                self.terminal_seen = true;
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

    /// The end of the stream: `done_seen` is whether the raw bytes carried `[DONE]`. Fails with
    /// `IncompleteProviderStreamError` without a finish reason or the sentinel; otherwise
    /// returns the private continuation chunk, if any.
    pub fn finish(&self, done_seen: bool) -> ChatResult<Option<ProviderStreamChunk>> {
        self.check_terminal(done_seen)?;
        self.final_metadata()
    }

    /// The terminal check Python runs inside the stream's `try` (so its error fires `on_error`).
    pub fn check_terminal(&self, done_seen: bool) -> ChatResult<()> {
        if !self.terminal_seen || !done_seen {
            return Err(ChatError::Provider(ProviderError::new(
                ErrorKind::IncompleteStream,
                "Provider stream ended before its terminal event",
            )));
        }
        Ok(())
    }

    /// The private continuation chunk Python yields after the stream's `try`, where a failure
    /// escapes without `on_error`.
    pub fn final_metadata(&self) -> ChatResult<Option<ProviderStreamChunk>> {
        Ok(self.metadata.payload()?.map(|metadata| ProviderStreamChunk {
            chat_completion_metadata: Some(metadata),
            ..ProviderStreamChunk::default()
        }))
    }
}

// ── Complete responses ────────────────────────────────────────────────────────────────────

/// `_parse_completion(completion, model_id, ...)`: a non-streaming chat completion.
pub fn parse_completion(
    completion: &Json,
    model_id: &str,
    preserve_reasoning: bool,
    preserve_content: bool,
) -> ChatResult<ProviderResponse> {
    let failed = |message: String| invalid(&format!("Failed to parse completion: {message}"));
    let choices = attr(completion, models::COMPLETION, "choices")
        .ok_or_else(|| failed(Incidental::no_attribute(completion, "choices").0))?;
    let choice = match choices {
        Json::Null => return Err(failed("'NoneType' object is not subscriptable".into())),
        Json::Object(_) => return Err(ChatError::Incidental(Incidental("0".into()))),
        other => first_choice(other).map_err(|error| failed(error.0))?,
    };
    let message = attr(&choice, models::COMPLETION_CHOICE, "message")
        .cloned()
        .ok_or_else(|| failed(Incidental::no_attribute(&choice, "message").0))?;

    let content_value = attr_or_none(&message, models::MESSAGE, "content").clone();
    let (content, block_reasoning) = split_chat_content(&content_value)?;
    let reasoning = chat_reasoning(&message, models::MESSAGE)? + &block_reasoning;
    let (visible, inline) = InlineReasoningFilter::new().feed(&content, true);
    let combined = reasoning + &inline;

    let mut tool_calls = Vec::new();
    for call in py_iter(attr_or_none(&message, models::MESSAGE, "tool_calls"))? {
        if !call.is_object() {
            // getattr on a plain value finds nothing: the defaults.
            tool_calls.push(ToolCall::new("", "", "{}"));
            continue;
        }
        let custom = call.get("type").and_then(Json::as_str) == Some("custom");
        let declared = if custom { models::CUSTOM_TOOL_CALL } else { models::FUNCTION_TOOL_CALL };
        // getattr(tc, "id", ""): a declared field, so a missing id is None.
        let id = PyValue::from_json(attr_or_none(&call, declared, "id").clone());
        // `getattr(tc.function, ...) if hasattr(tc, "function") else ...`
        let (name, arguments) = match attr(&call, declared, "function") {
            None => (PyValue::from(""), PyValue::from("{}")),
            Some(function) => {
                let get = |field: &str, default: &str| match model_or(function, models::FUNCTION) {
                    Some(fields) => PyValue::from_json(attr_or_none(function, fields, field).clone()),
                    None => PyValue::from(default),
                };
                (get("name", ""), get("arguments", "{}"))
            }
        };
        tool_calls.push(ToolCall { id, name, arguments });
    }

    let usage_raw = attr_or_none(completion, models::COMPLETION, "usage");
    let (usage, cache_usage) = if usage_raw.is_null() {
        (TokenUsage::default(), CacheUsage::default())
    } else {
        let get = |name: &str| attr(usage_raw, models::USAGE, name).unwrap_or(&Json::Null);
        let details = get("completion_tokens_details");
        let reasoning_tokens = if details.is_null() {
            None
        } else {
            let value = match model_or(details, models::COMPLETION_DETAILS) {
                Some(fields) => attr(details, fields, "reasoning_tokens").unwrap_or(&Json::Null),
                None => &Json::Null,
            };
            Some(int_or_zero(value)?)
        };
        // TokenUsage(prompt_tokens=getattr(usage_raw, "prompt_tokens", 0), ...): kept as the SDK
        // held it, None included; consumers coerce the way Python's code does.
        let count = |name: &str| {
            PyValue::from_json(attr(usage_raw, models::USAGE, name).cloned().unwrap_or_else(|| Json::from(0)))
        };
        let usage = TokenUsage {
            prompt_tokens: count("prompt_tokens"),
            completion_tokens: count("completion_tokens"),
            total_tokens: count("total_tokens"),
            reasoning_tokens,
        };
        let (_, cache) = stream_usage(usage_raw)?;
        (usage, cache)
    };

    let finish = attr_or_none(&choice, models::COMPLETION_CHOICE, "finish_reason");
    // getattr(choice, "finish_reason", "stop") or "stop"
    let finish_reason =
        if truthy(finish) { PyValue::from_json(finish.clone()) } else { PyValue::from("stop") };
    if finish_reason.as_text() == Some("length") {
        crate::log::warning(&format!(
            "Model {model_id} returned finish_reason='length' \u{2014} possible context window exceeded."
        ));
    }

    let mut metadata = ChatCompletionMetadata::new(preserve_reasoning, preserve_content);
    metadata.add(&message, models::MESSAGE)?;
    Ok(ProviderResponse {
        chat_completion_metadata: metadata.payload()?,
        content: (!visible.is_empty()).then_some(visible),
        reasoning_content: (!combined.is_empty()).then_some(combined),
        tool_calls,
        usage,
        usage_reported: Some(!usage_raw.is_null()),
        model: model_id.into(),
        finish_reason,
        cache_usage,
        ..ProviderResponse::default()
    })
}

// ── HTTP status errors ────────────────────────────────────────────────────────────────────

/// `str(APIStatusError)` as the SDK builds it from the response body: the stripped text parsed
/// as JSON and shown with Python's `str()`, or the text itself, or `Error code: <status>`.
pub fn status_error_message(status: u16, body: &[u8]) -> String {
    let text = String::from_utf8_lossy(body);
    let text = strip(&text);
    match sylvia_base::pyjson::loads(text) {
        Ok(parsed) => format!("Error code: {status} - {}", py_str(&parsed)),
        Err(_) if !text.is_empty() => text.to_owned(),
        Err(_) => format!("Error code: {status}"),
    }
}

/// `_raise_from_openai_error(exc, retryable_statuses)`.
///
/// With `retryable` (the non-streaming path), statuses in the set become retryable rate-limit
/// or connection errors and the rest are classified; without it (the streaming path), Python's
/// fixed ranges apply. `retry_after_header` is the response's `retry-after` value.
pub fn status_error(
    status: u16,
    message: &str,
    retry_after_header: Option<&str>,
    retryable: Option<&BTreeSet<u16>>,
) -> ProviderError {
    let delay = || crate::retry::retry_after_seconds(retry_after_header);
    if status == 401 || status == 403 {
        return ProviderError::new(ErrorKind::Authentication, message);
    }
    match retryable {
        Some(statuses) => {
            if statuses.contains(&status) {
                return if status == 429 {
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
        None => match status {
            429 => ProviderError::rate_limit(message, delay()),
            400 if is_context_rejection(message) => ProviderError::token_limit_rejection(),
            502 | 503 => ProviderError::new(ErrorKind::Unavailable, message),
            400 => ProviderError::new(ErrorKind::InvalidRequest, message),
            _ => ProviderError::connection(message, None),
        },
    }
}

/// Which wire fields a route documents for replay (`OpenAICompatProvider.__init__`).
pub fn preserve_flags(hostname: Option<&str>, provider_key: &str) -> (bool, bool) {
    const REPLAY_HOSTS: [&str; 11] = [
        "openrouter.ai",
        "api.deepseek.com",
        "api.moonshot.ai",
        "api.moonshot.cn",
        "api.together.ai",
        "api.z.ai",
        "api.fireworks.ai",
        "api.cerebras.ai",
        "dashscope-intl.aliyuncs.com",
        "dashscope.aliyuncs.com",
        "dashscope-us.aliyuncs.com",
    ];
    const REPLAY_KEYS: [&str; 8] =
        ["openrouter", "deepseek", "moonshot", "together", "zai", "fireworks", "cerebras", "alibaba"];
    let preserve_reasoning =
        hostname.is_some_and(|host| REPLAY_HOSTS.contains(&host)) || REPLAY_KEYS.contains(&provider_key);
    let preserve_content = hostname == Some("api.mistral.ai") || provider_key == "mistral";
    (preserve_reasoning, preserve_content)
}
