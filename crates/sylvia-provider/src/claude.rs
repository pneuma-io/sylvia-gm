//! `provider/_claude.py`: Sylvia's own Claude Code copy as a streaming provider; Sylvia owns
//! every game action. Lane 5.
//!
//! This part holds the request side and the rules the provider applies: the native transcript
//! a step resumes from ([`prepare_request`], with the context ledger's projection), whether a
//! step searches for deferred tools, how a runtime failure is classified, whether a kept
//! process is still waiting, the failure codes of the reuse audit, and the usage, per-model
//! usage and context-window readings. The provider's event loop follows in its own PR.
//!
//! Python names each record with `uuid.uuid4()` and stamps it with `datetime.now(UTC)`; both come
//! from a [`RecordIds`] here, so tests can supply Python's.

use crate::chat::ChatError;
use crate::claude_kept::{ContinuationError, ContinuationMismatch};
use crate::claude_native::{NativeRoute, FILLER_TEXT};
use crate::claude_runtime::RuntimeEvent;
use crate::error::{ErrorKind, ProviderError};
use crate::pyobj::{py_eq, py_str, Incidental};
use crate::types::{CacheUsage, TokenUsage};
use std::path::Path;
use sylvia_base::{Json, JsonMap};

/// `_PREFIX`: how Claude Code names Sylvia's MCP tools.
pub const PREFIX: &str = "mcp__sylvia__";
/// `TOOL_SEARCH`: Claude Code's own tool that loads a deferred tool's definition.
pub const TOOL_SEARCH: &str = "ToolSearch";
/// `ALWAYS_LOAD`: the MCP `_meta` key that keeps a tool loaded under the tool search.
pub const ALWAYS_LOAD: &str = "anthropic/alwaysLoad";
/// `_CONTINUE`: the stdin prompt when the transcript ends in tool results.
pub const CONTINUE: &str = "SYLVIA_GM_RUNTIME_CONTINUE: Continue the response to the saved Sylvia conversation, using the committed tool results above. This is an application invocation, not player speech.";

/// The record names and the clock `prepare_request` uses: `uuid.uuid4()` (as 16 bytes) and
/// `datetime.now(UTC).isoformat()`.
pub struct RecordIds<'a> {
    pub uuid4: &'a mut dyn FnMut() -> [u8; 16],
    pub now: &'a dyn Fn() -> String,
}

impl RecordIds<'_> {
    /// `str(uuid.uuid4())`.
    fn text(&mut self) -> String {
        let hex = self.hex();
        format!("{}-{}-{}-{}-{}", &hex[..8], &hex[8..12], &hex[12..16], &hex[16..20], &hex[20..])
    }

    /// `uuid.uuid4().hex`.
    fn hex(&mut self) -> String {
        (self.uuid4)().iter().map(|byte| format!("{byte:02x}")).collect()
    }
}

/// A random version 4 UUID's bytes.
pub fn random_uuid4() -> [u8; 16] {
    let mut bytes = [0u8; 16];
    getrandom::getrandom(&mut bytes).expect("the system random source");
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    bytes
}

fn invalid(message: &str) -> ChatError {
    ChatError::Provider(ProviderError::new(ErrorKind::InvalidResponse, message))
}

/// `value.get(key)` on a dict, else `AttributeError`.
fn get<'a>(value: &'a Json, key: &str) -> Result<Option<&'a Json>, ChatError> {
    match value {
        Json::Object(map) => Ok(map.get(key)),
        other => Err(Incidental::no_attribute(other, "get").into()),
    }
}

/// `value[key]`: `KeyError`, or the `TypeError` of a value that is not a dict.
fn index<'a>(value: &'a Json, key: &str) -> Result<&'a Json, ChatError> {
    match value {
        Json::Object(map) => map.get(key).ok_or_else(|| Incidental(crate::pyobj::py_repr_str(key)).into()),
        other => {
            Err(Incidental(format!("'{}' object is not subscriptable", crate::pyobj::type_name(other)))
                .into())
        }
    }
}

fn is_type(block: &Json, kind: &str) -> Result<bool, ChatError> {
    Ok(get(block, "type")?.and_then(Json::as_str) == Some(kind))
}

/// `any(block.get("type") == "tool_use" for block in content)`.
fn has_tool_use(content: &Json) -> Result<bool, ChatError> {
    for block in crate::pyobj::py_iter(content)? {
        if is_type(&block, "tool_use")? {
            return Ok(true);
        }
    }
    Ok(false)
}

fn object(pairs: Vec<(&str, Json)>) -> JsonMap {
    pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect()
}

/// What `prepare_request` returns: the system text, the transcript records, the stdin prompt
/// and the tools Sylvia's MCP server advertises.
#[derive(Debug, Clone, PartialEq)]
pub struct PreparedRequest {
    pub system: String,
    pub records: Vec<Json>,
    pub prompt: JsonMap,
    pub native_tools: Vec<Json>,
}

/// `re.fullmatch(r"[A-Za-z0-9_-]{1,64}", name)`.
fn tool_name(name: &str) -> bool {
    (1..=64).contains(&name.len())
        && name.bytes().all(|byte| byte.is_ascii_alphanumeric() || byte == b'_' || byte == b'-')
}

/// `prepare_request(messages, tools, model_id, workspace, native)`: canonical roles and
/// correlated tools projected into a disposable native transcript. With `native` (the
/// context ledger's route) the transcript is the ledger's native projection.
pub fn prepare_request(
    messages: &[Json],
    tools: Option<&[Json]>,
    model_id: &str,
    workspace: &Path,
    native: Option<&NativeRoute>,
    ids: &mut RecordIds<'_>,
) -> Result<PreparedRequest, ChatError> {
    let request = crate::anthropic::format_request(model_id, messages, tools, true, &JsonMap::new())?;
    let system = match request.get("system") {
        None => String::new(),
        Some(Json::Array(blocks)) => {
            for block in blocks {
                if !is_type(block, "text")? {
                    return Err(invalid("Claude cannot represent this system input."));
                }
            }
            let mut texts = Vec::new();
            for (position, block) in blocks.iter().enumerate() {
                match index(block, "text")? {
                    Json::String(text) => texts.push(text.clone()),
                    other => {
                        return Err(Incidental(format!(
                            "sequence item {position}: expected str instance, {} found",
                            crate::pyobj::type_name(other)
                        ))
                        .into())
                    }
                }
            }
            texts.join("\n\n")
        }
        Some(other) => py_str(other),
    };
    let empty = Vec::new();
    let request_tools = match request.get("tools") {
        Some(Json::Array(tools)) => tools,
        _ => &empty,
    };
    // Deferred tool list (#204): a deferred tool stays behind Claude Code's tool search.
    let deferring = request_tools.iter().any(|tool| tool.get("defer_loading") == Some(&Json::Bool(true)));
    let mut native_tools = Vec::new();
    for tool in request_tools {
        let name = index(tool, "name")?;
        if !name.as_str().is_some_and(tool_name) {
            return Err(invalid("Claude cannot represent this Sylvia tool name."));
        }
        let mut entry = object(vec![
            ("name", name.clone()),
            ("description", index(tool, "description")?.clone()),
            ("inputSchema", index(tool, "input_schema")?.clone()),
        ]);
        if deferring {
            let load = tool.get("defer_loading") != Some(&Json::Bool(true));
            entry.insert("_meta".into(), Json::Object(object(vec![(ALWAYS_LOAD, Json::Bool(load))])));
        }
        native_tools.push(Json::Object(entry));
    }
    let names: std::collections::BTreeSet<&str> =
        native_tools.iter().filter_map(|tool| tool["name"].as_str()).collect();
    if names.len() != native_tools.len() {
        return Err(invalid("Claude received duplicate Sylvia tool names."));
    }
    let mut history = match request.get("messages") {
        Some(Json::Array(messages)) => messages.clone(),
        _ => Vec::new(),
    };
    for message in &mut history {
        let content = get(message, "content")?.cloned();
        if let Some(Json::Array(blocks)) = content {
            let mut renamed = Vec::with_capacity(blocks.len());
            for mut block in blocks {
                if is_type(&block, "tool_use")? {
                    let original = py_str(index(&block, "name")?);
                    block["name"] = Json::String(format!("{PREFIX}{original}"));
                }
                renamed.push(block);
            }
            message["content"] = Json::Array(renamed);
        }
    }
    if let Some(route) = native {
        let (records, prompt) = native_projection(messages, &history, route, model_id, workspace, ids)?;
        return Ok(PreparedRequest { system, records, prompt, native_tools });
    }
    // A genuine final player input goes directly to stdin; a tool-result tail stays in the
    // transcript, since Claude's stdin prompt normalizer drops tool_result blocks.
    let is_player = match messages.last() {
        Some(last) => get(last, "role")?.and_then(Json::as_str) == Some("user"),
        None => false,
    };
    let prompt = if !history.is_empty() && is_player {
        let mut prompt = object(vec![
            ("type", Json::String("user".into())),
            ("message", history.pop().expect("non-empty")),
        ]);
        if get(messages.last().expect("non-empty"), "_application_data")?.is_some_and(crate::pyobj::truthy) {
            prompt.insert("isSynthetic".into(), Json::Bool(true));
        }
        prompt
    } else {
        object(vec![
            ("type", Json::String("user".into())),
            ("isSynthetic", Json::Bool(true)),
            (
                "message",
                Json::Object(object(vec![
                    ("role", Json::String("user".into())),
                    ("content", Json::String(CONTINUE.into())),
                ])),
            ),
        ])
    };
    let session = ids.text();
    let mut parent = Json::Null;
    let mut records = Vec::new();
    for message in history {
        let identity = ids.text();
        let mut entry = message.as_object().cloned().unwrap_or_default();
        if entry.get("role").and_then(Json::as_str) == Some("assistant") {
            let tool_use = has_tool_use(&entry["content"])?;
            entry.insert("id".into(), Json::String(format!("msg_{}", ids.hex())));
            entry.insert("type".into(), Json::String("message".into()));
            entry.insert("model".into(), Json::String(model_id.into()));
            entry.insert(
                "stop_reason".into(),
                Json::String(if tool_use { "tool_use" } else { "end_turn" }.into()),
            );
            entry.insert("stop_sequence".into(), Json::Null);
        }
        let mut record = object(vec![
            ("type", entry.get("role").cloned().unwrap_or(Json::Null)),
            ("uuid", Json::String(identity.clone())),
            ("parentUuid", parent),
            ("sessionId", Json::String(session.clone())),
            ("isSidechain", Json::Bool(false)),
            ("cwd", Json::String(workspace.to_string_lossy().into_owned())),
            ("timestamp", Json::String((ids.now)())),
        ]);
        record.insert("message".into(), Json::Object(entry));
        records.push(Json::Object(record));
        parent = Json::String(identity);
    }
    Ok(PreparedRequest { system, records, prompt, native_tools })
}

/// `tool_search(native_tools)`: does this step defer tools behind Claude Code's tool search?
pub fn tool_search(native_tools: &[Json]) -> bool {
    native_tools
        .iter()
        .any(|tool| tool.get("_meta").and_then(|meta| meta.get(ALWAYS_LOAD)) == Some(&Json::Bool(false)))
}

/// `_text_blocks(content)`: every user record and prompt uses list content.
fn text_blocks(content: Option<&Json>) -> Result<Vec<Json>, ChatError> {
    match content {
        Some(Json::String(text)) => Ok(vec![Json::Object(object(vec![
            ("type", Json::String("text".into())),
            ("text", Json::String(text.clone())),
        ]))]),
        Some(Json::Array(blocks)) if blocks.iter().all(Json::is_object) => Ok(blocks.clone()),
        _ => Err(invalid("Claude cannot represent this Sylvia input.")),
    }
}

/// `_answers(message, answer)`: is `answer` (a kept process's stored tool-result message) the
/// answer to `message`?
fn answers(message: Option<&Json>, answer: &Json) -> Result<bool, ChatError> {
    let Some(message) = message else { return Ok(false) };
    if get(message, "role")?.and_then(Json::as_str) != Some("user") {
        return Ok(false);
    }
    let first_result = |content: Option<&Json>| -> Option<Json> {
        let Some(Json::Array(blocks)) = content else { return None };
        let first = blocks.first().filter(|first| first.is_object())?;
        (first.get("type").and_then(Json::as_str) == Some("tool_result")).then(|| first.clone())
    };
    let (Some(ours), Some(stored)) =
        (first_result(get(message, "content")?), first_result(get(answer, "content")?))
    else {
        return Ok(false);
    };
    Ok(py_eq(
        stored.get("tool_use_id").unwrap_or(&Json::Null),
        ours.get("tool_use_id").unwrap_or(&Json::Null),
    ))
}

/// `_is_prompt_entry(message)`: a user message that is not a tool result.
fn is_prompt_entry(message: &Json) -> Result<bool, ChatError> {
    if get(message, "role")?.and_then(Json::as_str) != Some("user") {
        return Ok(false);
    }
    let result = match get(message, "content")? {
        Some(Json::Array(blocks)) => blocks.iter().any(|block| {
            block.is_object() && block.get("type").and_then(Json::as_str) == Some("tool_result")
        }),
        _ => false,
    };
    Ok(!result)
}

struct Transcript<'a, 'b> {
    records: Vec<Json>,
    parent: Json,
    session: String,
    timestamp: String,
    workspace: String,
    model_id: &'a str,
    ids: &'a mut RecordIds<'b>,
}

impl Transcript<'_, '_> {
    fn add(&mut self, kind: &str, body: JsonMap) {
        let identity = self.ids.text();
        let mut record = object(vec![
            ("type", Json::String(kind.into())),
            ("uuid", Json::String(identity.clone())),
            ("parentUuid", self.parent.clone()),
            ("sessionId", Json::String(self.session.clone())),
            ("isSidechain", Json::Bool(false)),
            ("cwd", Json::String(self.workspace.clone())),
            ("timestamp", Json::String(self.timestamp.clone())),
        ]);
        record.extend(body);
        self.records.push(Json::Object(record));
        self.parent = Json::String(identity);
    }

    fn add_message(&mut self, message: &Json) -> Result<(), ChatError> {
        let mut native = message.as_object().cloned().unwrap_or_default();
        let role = py_str(native.get("role").ok_or_else(|| Incidental("'role'".into()))?);
        if role == "assistant" {
            let tool_use = has_tool_use(native.get("content").unwrap_or(&Json::Null))?;
            native.insert("id".into(), Json::String(format!("msg_{}", self.ids.hex())));
            native.insert("type".into(), Json::String("message".into()));
            native.insert("model".into(), Json::String(self.model_id.into()));
            native.insert(
                "stop_reason".into(),
                Json::String(if tool_use { "tool_use" } else { "end_turn" }.into()),
            );
            native.insert("stop_sequence".into(), Json::Null);
        } else {
            let content = text_blocks(native.get("content"))?;
            native.insert("content".into(), Json::Array(content));
        }
        self.add(&role, object(vec![("message", Json::Object(native))]));
        Ok(())
    }

    /// Claude Code inserts its own unrecorded filler after a trailing user record.
    fn needs_filler(&self) -> bool {
        self.records
            .iter()
            .rev()
            .map(|record| record["type"].as_str().unwrap_or(""))
            .find(|kind| *kind != "attachment")
            == Some("user")
    }

    /// `_replay_record(add, saved)`: a rendered context record, or a tool-search round message.
    fn replay(&mut self, saved: &Json) {
        let kind = saved.get("type").and_then(Json::as_str);
        if matches!(kind, Some("assistant" | "user")) && saved.get("message").is_some_and(Json::is_object) {
            self.add(kind.expect("a kind"), object(vec![("message", saved["message"].clone())]));
            return;
        }
        let mut body = JsonMap::new();
        for key in ["attachment", "rendered", "renderedRole"] {
            if let Some(value) = saved.get(key) {
                body.insert(key.into(), value.clone());
            }
        }
        self.add("attachment", body);
    }
}

/// `_native_projection(messages, history, route, model_id, workspace)`: the ledger journal as
/// Claude Code's transcript, plus this step's stdin prompt.
fn native_projection(
    messages: &[Json],
    history: &[Json],
    route: &NativeRoute,
    model_id: &str,
    workspace: &Path,
    ids: &mut RecordIds<'_>,
) -> Result<(Vec<Json>, JsonMap), ChatError> {
    let mut leading = 0;
    while leading < messages.len()
        && get(&messages[leading], "role")?.and_then(Json::as_str) == Some("system")
    {
        leading += 1;
    }
    let mut later_system = false;
    for message in &messages[leading..] {
        later_system |= get(message, "role")?.and_then(Json::as_str) == Some("system");
    }
    let increasing = route.boundaries.windows(2).all(|pair| pair[0] < pair[1]);
    if leading != route.stable_count
        || history.len() != route.entry_count
        || messages.len() - leading != route.entry_count
        || later_system
        || route.boundaries.iter().any(|boundary| !(0 < *boundary && *boundary < route.entry_count))
        || !increasing
    {
        return Err(invalid("Claude's native transcript does not match this request."));
    }
    let session = ids.text();
    let timestamp = (ids.now)();
    let mut transcript = Transcript {
        records: Vec::new(),
        parent: Json::Null,
        session,
        timestamp,
        workspace: workspace.to_string_lossy().into_owned(),
        model_id,
        ids,
    };
    let mut prompt = None;
    let ends: Vec<usize> = route.boundaries.iter().copied().chain([history.len()]).collect();
    let mut start = 0;
    for end in ends {
        let mut group = end;
        while group > start && is_prompt_entry(&history[group - 1])? {
            group -= 1;
        }
        let answer = if end != history.len() { route.native_answers.get(&end) } else { None };
        if let Some(answer) = answer {
            if answers(if group > start { Some(&history[group - 1]) } else { None }, answer)? {
                // A kept process answered this request's call in place: replay its stored message.
                for message in &history[start..group - 1] {
                    transcript.add_message(message)?;
                }
                transcript.add("user", object(vec![("message", answer.clone())]));
                for saved in route.native_records.get(&end).into_iter().flatten() {
                    transcript.replay(saved);
                }
                start = end;
                continue;
            }
        }
        for message in &history[start..group] {
            transcript.add_message(message)?;
        }
        let mut content = Vec::new();
        for message in &history[group..end] {
            content.extend(text_blocks(Some(index(message, "content")?))?);
        }
        start = end;
        if content.is_empty() {
            if end == history.len() {
                return Err(invalid("Claude has no prompt for this request."));
            }
            continue;
        }
        if transcript.needs_filler() {
            let message = object(vec![
                ("role", Json::String("assistant".into())),
                ("content", serde_json::json!([{"type": "text", "text": FILLER_TEXT}])),
                ("id", Json::String(format!("msg_{}", transcript.ids.hex()))),
                ("type", Json::String("message".into())),
                ("model", Json::String(model_id.into())),
                ("stop_reason", Json::String("end_turn".into())),
                ("stop_sequence", Json::Null),
            ]);
            transcript.add("assistant", object(vec![("message", Json::Object(message))]));
        }
        let message = Json::Object(object(vec![
            ("role", Json::String("user".into())),
            ("content", Json::Array(content)),
        ]));
        if end == history.len() {
            prompt = Some(object(vec![("type", Json::String("user".into())), ("message", message)]));
        } else {
            transcript.add("user", object(vec![("message", message)]));
            for saved in route.native_records.get(&end).into_iter().flatten() {
                transcript.replay(saved);
            }
        }
    }
    let prompt = prompt.expect("the last group is the prompt");
    Ok((transcript.records, prompt))
}

// ── Rules ─────────────────────────────────────────────────────────────────────────────────

/// `runtime_failure(event)`: a native failure classified without reflecting errors that may
/// echo private inputs.
pub fn runtime_failure(event: &JsonMap) -> ProviderError {
    let status = event.get("api_error_status");
    let empty = Json::String(String::new());
    let text = ["result", "errors", "error", "subtype"]
        .iter()
        .map(|key| py_str(event.get(*key).unwrap_or(&empty)))
        .collect::<Vec<_>>()
        .join(" ")
        .to_lowercase();
    let status_is = |codes: &[i64]| {
        status.is_some_and(|status| codes.iter().any(|code| py_eq(status, &Json::from(*code))))
    };
    let any = |words: &[&str]| words.iter().any(|word| text.contains(word));
    if status_is(&[401, 403]) || any(&["not logged in", "authentication", "login required", "oauth"]) {
        return ProviderError::new(
            ErrorKind::Authentication,
            "Sign in again through the Claude runtime. Saved work is kept.",
        );
    }
    if status_is(&[429]) || any(&["rate_limit", "rate limit", "usage limit", "quota"]) {
        return ProviderError::rate_limit(
            "Claude reports that your account allowance is unavailable. Check Claude for its reset time.",
            None,
        );
    }
    if crate::error::is_context_rejection(&text) {
        return ProviderError::token_limit_rejection();
    }
    if status_is(&[404])
        || any(&["model_not_found", "model not found", "invalid model", "not have access to"])
    {
        return ProviderError::new(
            ErrorKind::ModelNotFound,
            "This model is unavailable to the selected Claude account. Choose another model.",
        );
    }
    ProviderError::new(
        ErrorKind::Provider,
        "Claude did not complete its response. Saved work is kept; no API fallback was started.",
    )
}

/// `_INVALID_REASONS`: the adapter's own refusals of a response, as audit codes.
pub fn invalid_reason(message: &str) -> Option<&'static str> {
    match message {
        "Claude requested an unavailable or uncorrelated Sylvia operation." => Some("uncorrelated_call"),
        "Claude did not expose exactly this request's Sylvia tools." => Some("tools_not_exposed"),
        "Claude could not admit the requested operation under its native permissions." => {
            Some("operation_not_admitted")
        }
        "Claude returned invalid text." => Some("invalid_text"),
        _ => None,
    }
}

/// `_check_still_waiting(kept)` on what a kept process wrote while idle: frames that only finish
/// the reply it was left on are dropped; a result, a further call, a new model message, an
/// invalid stream or its exit mean it cannot serve this request.
pub fn check_still_waiting(drained: &[RuntimeEvent]) -> Result<(), ContinuationError> {
    let moved = || ContinuationError::Mismatch(ContinuationMismatch { reason: "turn_moved_on" });
    for event in drained {
        let event = match event {
            RuntimeEvent::Exited => {
                return Err(ContinuationError::Mismatch(ContinuationMismatch { reason: "runtime_exited" }))
            }
            RuntimeEvent::Failed(_) => return Err(moved()),
            RuntimeEvent::Event(event) => event,
        };
        let kind = event.get("type").and_then(Json::as_str);
        if matches!(kind, Some("result" | "control_request")) {
            return Err(moved());
        }
        if kind == Some("stream_event") {
            // `(event.get("event") or {}).get("type")`.
            let native = event.get("event").filter(|native| crate::pyobj::truthy(native));
            let native_type = match native {
                None => None,
                Some(Json::Object(map)) => map.get("type"),
                Some(other) => return Err(ContinuationError::Raw(Incidental::no_attribute(other, "get").0)),
            };
            if native_type.and_then(Json::as_str) == Some("message_start") {
                return Err(moved());
            }
        }
    }
    Ok(())
}

/// `_failure_code(error)`: a content-free code for why a step failed. `None` with `cancelled`
/// is `asyncio.CancelledError`.
pub fn failure_code(error: Option<&ProviderError>, cancelled: bool) -> &'static str {
    if cancelled {
        return "cancelled";
    }
    let Some(error) = error else { return "local_error" };
    if error.is(ErrorKind::Timeout) {
        return "timeout";
    }
    if error.is(ErrorKind::IncompleteStream) || error.is(ErrorKind::Connection) {
        return "connection_lost";
    }
    if error.is(ErrorKind::InvalidResponse) {
        return invalid_reason(&error.message).unwrap_or("invalid_response");
    }
    if error.kind == ErrorKind::Internal {
        return "local_error";
    }
    "provider_error"
}

/// A whole number Python holds as `int` and not `bool`, at least 0.
fn count(value: Option<&Json>) -> Option<i64> {
    match value? {
        Json::Number(number) if !number.is_f64() => number.as_i64().filter(|value| *value >= 0),
        _ => None,
    }
}

/// `_usage(response, usage)`: only measured counts, never the CLI's list-price estimates.
pub fn measured_usage(usage: &Json) -> Option<(TokenUsage, CacheUsage)> {
    let usage = usage.as_object()?;
    let prompt = count(usage.get("input_tokens"))?;
    let output = count(usage.get("output_tokens"))?;
    let read = count(usage.get("cache_read_input_tokens"));
    let write = count(usage.get("cache_creation_input_tokens"));
    let total_prompt = prompt + read.unwrap_or(0) + write.unwrap_or(0);
    Some((
        TokenUsage::from_ints(total_prompt, output, total_prompt + output, None),
        CacheUsage {
            cache_read_tokens: read,
            cache_write_tokens: write,
            cache_miss_tokens: Some(prompt),
            total_prompt_tokens: total_prompt,
        },
    ))
}

/// `_MODEL_USAGE_COUNTS`: the whole-number fields of a result's per-model usage kept for the
/// audit; the first four add up over a process's life.
pub const MODEL_USAGE_COUNTS: [&str; 6] = [
    "inputTokens",
    "outputTokens",
    "cacheReadInputTokens",
    "cacheCreationInputTokens",
    "contextWindow",
    "maxOutputTokens",
];

/// `_audit_model_usage`'s figures: the counts per model, and what the audit records (this
/// step's share of the totals when `since` holds what the process reported before).
pub fn model_usage(usage_by_model: &Json, since: Option<&JsonMap>) -> Option<(JsonMap, JsonMap)> {
    let usage = usage_by_model.as_object()?;
    let mut counts = JsonMap::new();
    for (name, row) in usage {
        let Json::Object(row) = row else { continue };
        let mut kept = JsonMap::new();
        for key in MODEL_USAGE_COUNTS {
            // `type(row.get(key)) is int`, bools excluded.
            if let Some(value @ Json::Number(number)) = row.get(key) {
                if !number.is_f64() {
                    kept.insert(key.into(), value.clone());
                }
            }
        }
        counts.insert(name.clone(), Json::Object(kept));
    }
    let recorded = match since {
        None => counts.clone(),
        Some(since) => counts
            .iter()
            .map(|(name, row)| {
                let before = since.get(name).and_then(Json::as_object);
                let row = row.as_object().expect("a row");
                let shares = row
                    .iter()
                    .map(|(key, value)| {
                        if MODEL_USAGE_COUNTS[..4].contains(&key.as_str()) {
                            let earlier =
                                before.and_then(|before| before.get(key)).and_then(Json::as_i64).unwrap_or(0);
                            (key.clone(), Json::from(value.as_i64().unwrap_or(0) - earlier))
                        } else {
                            (key.clone(), value.clone())
                        }
                    })
                    .collect();
                (name.clone(), Json::Object(shares))
            })
            .collect(),
    };
    Some((counts, recorded))
}

/// `_observe_context_window`'s reading: the window Claude Code reports for this model in a
/// result's per-model usage, when it is a plausible figure.
pub fn reported_window(
    usage_by_model: &Json,
    resolved_model: Option<&str>,
    wire_model_id: &str,
) -> Option<i64> {
    let usage = usage_by_model.as_object()?;
    let rows: Vec<(&String, &JsonMap)> =
        usage.iter().filter_map(|(name, row)| row.as_object().map(|row| (name, row))).collect();
    let find = |name: &str| rows.iter().find(|(key, _)| *key == name).map(|(_, row)| *row);
    // `rows.get(resolved or "") or rows.get(wire)`: an empty row counts as missing.
    let first = find(resolved_model.unwrap_or("")).filter(|row| !row.is_empty());
    let row = match first.or_else(|| find(wire_model_id)) {
        Some(row) => row,
        None if rows.len() == 1 => rows[0].1,
        None => return None,
    };
    match row.get("contextWindow") {
        Some(Json::Number(number)) if !number.is_f64() => {
            number.as_i64().filter(|value| (512..=10_000_000).contains(value))
        }
        _ => None,
    }
}
