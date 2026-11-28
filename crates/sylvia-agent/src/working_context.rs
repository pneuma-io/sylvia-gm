//! `agent/_working_context.py`: a replaceable model working copy. The saved conversation and the
//! application state stay authoritative.
//!
//! [`WorkingContextProvider`] wraps the turn's provider: it projects each request over the saved
//! continuation notes, compacts history in bounded pieces when it would not fit, and keeps the
//! unmodified source history for `read_session_history`. The context ledger's hooks
//! ([`LedgerHooks`]) are plumbed here but unset in the legacy assembly.
//!
//! `compact_saved_messages` (the explicit Compact request) and the `/compact` command's
//! `compact_provider` run under the session loop's `run_compaction`.

use serde_json::json;
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Mutex, OnceLock};
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{pyre, pystr, Json, JsonMap};
use sylvia_provider::catalog::output_allowance;
use sylvia_provider::error::{ErrorKind, ProviderError, ProviderResult};
use sylvia_provider::token_estimate::{token_estimator, TokenEstimator};
use sylvia_provider::types::{ModelInfo, ProviderResponse, ProviderStreamChunk};
use sylvia_provider::{BoxFuture, ChunkStream, Provider, ProviderRequest};
use tokio::sync::{mpsc, oneshot};

/// `CHECKPOINT_KIND`.
pub const CHECKPOINT_KIND: &str = "working_context_checkpoint";
/// `REBASE_SHARE`: ledger mode rebases when the wire's excess over the fixed term passes this
/// share of the remaining budget.
pub const REBASE_SHARE: f64 = 0.85;
/// `UNKNOWN_WINDOW`: the window planned against when no source knows a model's window.
pub const UNKNOWN_WINDOW: i64 = 65_536;
/// `_SUMMARY_LIMIT`.
const SUMMARY_LIMIT: usize = 12_000;

/// `UNKNOWN_WINDOW_BY_TRANSPORT.get(transport, UNKNOWN_WINDOW)`: Claude Code reaches only Claude
/// models, every one with at least 200,000 tokens.
pub fn unknown_window(transport: Option<&str>) -> i64 {
    if transport == Some("claude_code") {
        200_000
    } else {
        UNKNOWN_WINDOW
    }
}

fn texts() -> &'static JsonMap {
    static TEXTS: OnceLock<JsonMap> = OnceLock::new();
    TEXTS.get_or_init(|| match pyjson::loads(include_str!("working_context_texts.json")) {
        Ok(Json::Object(map)) => map,
        _ => panic!("working_context_texts.json is an exported JSON object"),
    })
}

/// The exported text `key` of `working_context_texts.json`.
pub(crate) fn text(key: &str) -> &'static str {
    texts()[key].as_str().expect("an exported text")
}

/// `HISTORY_TOOL`: the `read_session_history` tool definition.
pub fn history_tool() -> Json {
    texts()["history_tool"].clone()
}

/// `_json(value)`: `json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))`.
fn compact(value: &Json) -> String {
    pyjson::dumps(value, Dump::CANONICAL)
}

/// Python truthiness of a JSON value (`None` for a missing key).
fn truthy(value: Option<&Json>) -> bool {
    match value {
        None | Some(Json::Null) => false,
        Some(Json::Bool(flag)) => *flag,
        Some(Json::Number(number)) => number.as_f64() != Some(0.0),
        Some(Json::String(text)) => !text.is_empty(),
        Some(Json::Array(items)) => !items.is_empty(),
        Some(Json::Object(map)) => !map.is_empty(),
    }
}

fn role_is(message: &JsonMap, role: &str) -> bool {
    message.get("role").and_then(Json::as_str) == Some(role)
}

/// `str(message.get("content", ""))`.
fn content_text(message: &JsonMap) -> String {
    match message.get("content") {
        None => String::new(),
        Some(Json::String(text)) => text.clone(),
        Some(other) => pystr::repr_json(other),
    }
}

fn internal(message: impl Into<String>) -> ProviderError {
    ProviderError::new(ErrorKind::Internal, message)
}

/// `_safe_message(message)`: protocol data only; native signed thinking never enters
/// checkpoints.
pub fn safe_message(message: &JsonMap) -> JsonMap {
    ["role", "content", "tool_calls", "tool_call_id", "name"]
        .iter()
        .filter_map(|key| message.get(*key).map(|value| ((*key).to_owned(), value.clone())))
        .collect()
}

/// `durable_tool_result(tool_name, result)`: resumable read receipts kept stable between live
/// tool history and saved history.
pub fn durable_tool_result(tool_name: &str, result: &Json) -> Json {
    let Json::Object(original) = result else { return result.clone() };
    let mut result = original.clone();
    if tool_name == "read_file" {
        if let Some(Json::String(content)) = result.shift_remove("content") {
            result.insert("content_sha256".into(), sha256_hex(content.as_bytes()).into());
            result.insert("content_omitted_from_history".into(), Json::Bool(true));
        }
    } else if tool_name == "search_files" {
        if let Some(Json::Array(matches)) = result.get("matches").cloned() {
            let stripped: Vec<Json> = matches
                .iter()
                .map(|item| match item {
                    Json::Object(map) => Json::Object(
                        map.iter()
                            .filter(|(key, _)| *key != "content")
                            .map(|(k, v)| (k.clone(), v.clone()))
                            .collect(),
                    ),
                    other => other.clone(),
                })
                .collect();
            result.insert("matches".into(), Json::Array(stripped));
            if matches.iter().any(|item| item.as_object().is_some_and(|map| map.contains_key("content"))) {
                result.insert("matched_content_omitted_from_history".into(), Json::Bool(true));
            }
        }
    }
    Json::Object(result)
}

/// `_parsed(value)`: a JSON string read, else the value itself.
fn parsed(value: Option<&Json>) -> Json {
    match value {
        Some(Json::String(text)) => pyjson::loads(text).unwrap_or_else(|_| Json::String(text.clone())),
        Some(other) => other.clone(),
        None => Json::Null,
    }
}

/// `record.get("version") == 1`: Python's `1`, `1.0` and `True` all equal 1.
fn equals_one(value: Option<&Json>) -> bool {
    match value {
        Some(Json::Bool(flag)) => *flag,
        Some(Json::Number(number)) => number.as_f64() == Some(1.0),
        _ => false,
    }
}

/// `_valid_checkpoint(record)`: incomplete or unfamiliar saved notes are ignored and the
/// context rebuilt from the original conversation.
pub fn valid_checkpoint(record: &JsonMap) -> bool {
    let text_of = |key: &str| record.get(key).and_then(Json::as_str);
    record.get("kind").and_then(Json::as_str) == Some(CHECKPOINT_KIND)
        && equals_one(record.get("version"))
        && text_of("checkpoint_id").is_some_and(|id| !id.is_empty())
        && text_of("summary").is_some_and(|summary| (1..=SUMMARY_LIMIT).contains(&summary.chars().count()))
        && text_of("source_archive").is_some()
        && record.get("covered_keys").and_then(Json::as_array).is_some_and(|keys| {
            !keys.is_empty()
                && keys.iter().all(|key| key.as_str().is_some_and(|key| key.chars().count() == 64))
        })
}

/// `_Group`: one complete exchange and the fingerprint of each pair in it.
#[derive(Debug, Clone, PartialEq)]
struct Group {
    messages: Vec<JsonMap>,
    keys: Vec<String>,
}

/// A JSON value as a set element (`{r.get("tool_call_id") for r in results}`).
fn set_key(value: Option<&Json>) -> String {
    value.map_or_else(|| "null".to_owned(), compact)
}

fn key_error(key: &str) -> ProviderError {
    internal(format!("'{key}'"))
}

/// `_groups(messages)`: complete native exchanges kept together; each pair fingerprinted
/// independently for SQLite replay. Returns the pinned messages and the groups.
fn groups(messages: &[JsonMap]) -> ProviderResult<(Vec<JsonMap>, Vec<Group>)> {
    let mut pinned = Vec::new();
    let mut groups = Vec::new();
    let mut index = 0;
    while index < messages.len() {
        let message = &messages[index];
        index += 1;
        if role_is(message, "system") || content_text(message).starts_with("SYLVIA_GM_BUILDER_START ") {
            pinned.push(message.clone());
            continue;
        }
        let calls: Vec<Json> = match message.get("tool_calls") {
            Some(Json::Array(calls)) => calls.clone(),
            Some(other) if truthy(Some(other)) => {
                return Err(internal("Working context tool calls are not a list"));
            }
            _ => Vec::new(),
        };
        let mut bundle = vec![message.clone()];
        let mut keys = Vec::new();
        if calls.is_empty() {
            keys.push(compact(&Json::Object(safe_message(message))));
        } else {
            let mut results = Vec::new();
            while index < messages.len() && role_is(&messages[index], "tool") {
                results.push(messages[index].clone());
                index += 1;
            }
            let result_ids: std::collections::HashSet<String> =
                results.iter().map(|result| set_key(result.get("tool_call_id"))).collect();
            let call_ids: std::collections::HashSet<String> =
                calls.iter().map(|call| set_key(call.get("id"))).collect();
            if result_ids != call_ids {
                // An unfinished tool exchange is never summarized or separated from its call.
                pinned.extend(bundle);
                pinned.extend(results);
                continue;
            }
            if truthy(message.get("content")) {
                keys.push(compact(&json!({"role": "assistant", "content": message["content"]})));
            }
            for call in &calls {
                let function = call.get("function").ok_or_else(|| key_error("function"))?;
                let id = call.get("id").ok_or_else(|| key_error("id"))?;
                let name = function.get("name").ok_or_else(|| key_error("name"))?;
                let arguments = function.get("arguments").ok_or_else(|| key_error("arguments"))?;
                let result = results
                    .iter()
                    .find(|result| result.get("tool_call_id") == Some(id))
                    .ok_or_else(|| internal("StopIteration"))?;
                let name_text = name.as_str().map_or_else(|| pystr::repr_json(name), str::to_owned);
                keys.push(compact(&json!({
                    "id": id,
                    "name": name,
                    "arguments": parsed(Some(arguments)),
                    "result": durable_tool_result(&name_text, &parsed(result.get("content"))),
                })));
            }
            bundle.extend(results);
        }
        groups.push(Group {
            messages: bundle,
            keys: keys.iter().map(|key| sha256_hex(key.as_bytes())).collect(),
        });
    }
    Ok((pinned, groups))
}

/// `estimate_tokens(value)`: an offline planning estimate, about three UTF-8 bytes a token.
pub fn estimate_tokens(value: &Json) -> i64 {
    (compact(value).len() as i64 + 2) / 3 + 32
}

/// `CheckpointWrite`: one checkpoint the session owner must append, and its acknowledgement.
#[derive(Debug)]
pub struct CheckpointWrite {
    pub payload: JsonMap,
    pub accepted: oneshot::Sender<()>,
}

/// `CheckpointBridge`: moves a checkpoint write to the session owner and waits for its
/// acknowledgement. The session loop takes the writes, appends each as tool evidence and
/// accepts it; dropping a write unaccepted cancels the save.
#[derive(Debug)]
pub struct CheckpointBridge {
    sender: mpsc::UnboundedSender<CheckpointWrite>,
    pending: tokio::sync::Mutex<mpsc::UnboundedReceiver<CheckpointWrite>>,
}

impl Default for CheckpointBridge {
    fn default() -> Self {
        Self::new()
    }
}

impl CheckpointBridge {
    /// `CheckpointBridge()`.
    pub fn new() -> Self {
        let (sender, pending) = mpsc::unbounded_channel();
        Self { sender, pending: tokio::sync::Mutex::new(pending) }
    }

    /// `save(payload)`: queue the write and wait until the owner accepted it.
    pub async fn save(&self, payload: JsonMap) -> ProviderResult<()> {
        let (accepted, acknowledged) = oneshot::channel();
        self.sender
            .send(CheckpointWrite { payload, accepted })
            .map_err(|_| ProviderError::new(ErrorKind::Cancelled, ""))?;
        acknowledged.await.map_err(|_| ProviderError::new(ErrorKind::Cancelled, ""))
    }

    /// The next queued write, if any (`pending.get_nowait()`).
    pub fn take(&self) -> Option<CheckpointWrite> {
        self.pending.try_lock().ok()?.try_recv().ok()
    }

    /// Wait for the next write.
    pub async fn next(&self) -> Option<CheckpointWrite> {
        self.pending.lock().await.recv().await
    }
}

/// `save(payload)`: the owner's atomic checkpoint append.
pub type SaveCheckpoint = Arc<dyn Fn(JsonMap) -> BoxFuture<'static, ProviderResult<()>> + Send + Sync>;
/// `prepare_continuation(messages, tools_offered)`: the provider message list for a request.
pub type PrepareContinuation =
    Arc<dyn Fn(Vec<JsonMap>, bool) -> BoxFuture<'static, ProviderResult<Vec<JsonMap>>> + Send + Sync>;

/// `cancelled()`: whether the turn's control was cancelled.
pub type Cancelled = Arc<dyn Fn() -> bool + Send + Sync>;
/// `progress(text)`: an application progress hint, or `None` to clear it.
pub type Progress = Arc<dyn Fn(Option<&str>) + Send + Sync>;
/// `finalize_request(projected, request_tools)`: build and durably journal the wire list.
pub type FinalizeRequest = Arc<
    dyn Fn(Vec<JsonMap>, Option<Vec<Json>>) -> BoxFuture<'static, ProviderResult<Vec<JsonMap>>> + Send + Sync,
>;
/// `measure_wire(projected, tools)`: the wire list a request would send.
pub type MeasureWire = Arc<dyn Fn(&[JsonMap], Option<&[Json]>) -> Vec<JsonMap> + Send + Sync>;
/// `rebase_wire()` and `strip_thinking()`.
pub type LedgerAction = Arc<dyn Fn() + Send + Sync>;
/// `wire_tools(request_tools)`: the tool list the provider is sent.
pub type WireTools = Arc<dyn Fn(Option<Vec<Json>>) -> Option<Vec<Json>> + Send + Sync>;
/// `uuid4().hex`, for a new checkpoint's identity.
pub type CheckpointId = Arc<dyn Fn() -> String + Send + Sync>;

/// The context ledger's hooks; every one is unset in the legacy assembly.
#[derive(Clone, Default)]
pub struct LedgerHooks {
    pub finalize_request: Option<FinalizeRequest>,
    pub measure_wire: Option<MeasureWire>,
    /// `rebase_wire()`: restart the journal from canonical history.
    pub rebase_wire: Option<LedgerAction>,
    /// `strip_thinking()`: drop every earlier replayed thinking block.
    pub strip_thinking: Option<LedgerAction>,
    pub wire_tools: Option<WireTools>,
}

/// The constructor's keyword arguments, with Python's defaults from [`WorkingContextOptions::new`].
#[derive(Clone)]
pub struct WorkingContextOptions {
    pub records: Vec<JsonMap>,
    pub save: SaveCheckpoint,
    pub cancelled: Cancelled,
    pub progress: Progress,
    pub prepare_continuation: Option<PrepareContinuation>,
    pub record_owner: Option<JsonMap>,
    pub request_context_id: Option<String>,
    pub pin_first_user: bool,
    pub ledger: LedgerHooks,
    /// `uuid4().hex` for a new checkpoint's identity.
    pub checkpoint_id: CheckpointId,
}

impl WorkingContextOptions {
    /// `save=...` with every other argument at its default.
    pub fn new(save: SaveCheckpoint) -> Self {
        Self {
            records: Vec::new(),
            save,
            cancelled: Arc::new(|| false),
            progress: Arc::new(|_| {}),
            prepare_continuation: None,
            record_owner: None,
            request_context_id: None,
            pin_first_user: true,
            ledger: LedgerHooks::default(),
            checkpoint_id: Arc::new(uuid4_hex),
        }
    }
}

/// `uuid.uuid4().hex`.
pub fn uuid4_hex() -> String {
    let mut bytes = [0u8; 16];
    getrandom::getrandom(&mut bytes).expect("the operating system's random source is available");
    bytes[6] = (bytes[6] & 0x0f) | 0x40;
    bytes[8] = (bytes[8] & 0x3f) | 0x80;
    hex::encode(bytes)
}

/// The sizes behind the compaction and rebase decisions (`budget_terms`).
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct BudgetTerms {
    pub capacity: i64,
    pub input_budget: i64,
    pub fixed_tokens: i64,
    pub history_tokens: i64,
    pub threshold: i64,
    pub target: i64,
    /// Ledger mode only: `wire_tokens`, `wire_excess_tokens` and `rebase_threshold`.
    pub wire: Option<(i64, i64, i64)>,
}

impl BudgetTerms {
    /// The dict Python returns, in its key order.
    pub fn to_json(&self) -> JsonMap {
        let mut terms = JsonMap::new();
        for (key, value) in [
            ("capacity", self.capacity),
            ("input_budget", self.input_budget),
            ("fixed_tokens", self.fixed_tokens),
            ("history_tokens", self.history_tokens),
            ("threshold", self.threshold),
            ("target", self.target),
        ] {
            terms.insert(key.into(), value.into());
        }
        if let Some((wire, excess, rebase)) = self.wire {
            terms.insert("wire_tokens".into(), wire.into());
            terms.insert("wire_excess_tokens".into(), excess.into());
            terms.insert("rebase_threshold".into(), rebase.into());
        }
        terms
    }
}

/// `int(value)` for an option the callers pass as a number.
fn int_option(value: &Json) -> ProviderResult<i64> {
    match value {
        Json::Bool(flag) => Ok(i64::from(*flag)),
        Json::Number(number) => number
            .as_i64()
            .or_else(|| number.as_f64().filter(|value| value.is_finite()).map(|value| value.trunc() as i64))
            .ok_or_else(|| internal("int() of an unsupported number")),
        other => Err(internal(format!("int() argument must be a number, not {}", pystr::repr_json(other)))),
    }
}

/// `f"{value:,}"`.
fn grouped(value: i64) -> String {
    let digits = value.unsigned_abs().to_string();
    let mut out = String::new();
    for (index, digit) in digits.chars().enumerate() {
        if index > 0 && (digits.len() - index).is_multiple_of(3) {
            out.push(',');
        }
        out.push(digit);
    }
    if value < 0 {
        format!("-{out}")
    } else {
        out
    }
}

fn token_limit(message: impl Into<String>) -> ProviderError {
    ProviderError::new(ErrorKind::TokenLimit, message)
}

const CANNOT_SHORTEN: &str =
    "Context cannot be shortened further: instructions, tools or the latest exchange are too \
     large. Saved work is intact; use a larger context model or a bounded source read.";

/// `WorkingContextProvider`: projects each request, compacts in bounded pieces, and keeps the
/// unmodified source history.
pub struct WorkingContextProvider {
    provider: Arc<dyn Provider>,
    /// `records`: the valid checkpoints of this owner, oldest first.
    records: Mutex<Vec<JsonMap>>,
    record_owner: Option<JsonMap>,
    save: SaveCheckpoint,
    cancelled: Cancelled,
    progress: Progress,
    prepare_continuation: Option<PrepareContinuation>,
    /// `_source`: the messages of the latest request.
    source: Mutex<Vec<JsonMap>>,
    /// `history_retrieval_enabled`.
    history_retrieval_enabled: AtomicBool,
    request_context_id: Option<String>,
    pin_first_user: bool,
    ledger: LedgerHooks,
    token_estimator: TokenEstimator,
    checkpoint_id: CheckpointId,
}

/// Clears the progress hint when compaction ends, however it ends (`finally`).
struct ProgressCleared<'a>(&'a (dyn Fn(Option<&str>) + Send + Sync));

impl Drop for ProgressCleared<'_> {
    fn drop(&mut self) {
        (self.0)(None);
    }
}

impl WorkingContextProvider {
    /// `WorkingContextProvider(provider, **options)`.
    pub fn new(provider: Arc<dyn Provider>, options: WorkingContextOptions) -> Self {
        let record_owner = options.record_owner;
        let records = options
            .records
            .into_iter()
            .filter(|record| {
                valid_checkpoint(record)
                    && record_owner
                        .as_ref()
                        .is_none_or(|owner| record.get("context_owner") == Some(&Json::Object(owner.clone())))
            })
            .collect();
        let token_estimator = token_estimator(provider.as_ref());
        Self {
            provider,
            records: Mutex::new(records),
            record_owner,
            save: options.save,
            cancelled: options.cancelled,
            progress: options.progress,
            prepare_continuation: options.prepare_continuation,
            source: Mutex::new(Vec::new()),
            history_retrieval_enabled: AtomicBool::new(false),
            request_context_id: options.request_context_id,
            pin_first_user: options.pin_first_user,
            ledger: options.ledger,
            token_estimator,
            checkpoint_id: options.checkpoint_id,
        }
    }

    /// `records`.
    pub fn records(&self) -> Vec<JsonMap> {
        self.records.lock().unwrap_or_else(std::sync::PoisonError::into_inner).clone()
    }

    /// Messages the caller appended to the latest request's list after sending it. Python's
    /// `_source` is the caller's list object itself, so `read_session_history`'s `current`
    /// source sees what is appended to it afterwards: the Builder runner appends its tool-call
    /// message and each tool result (lane 1's B3a calls this as it does). The GM's request list
    /// is built for each round and never appended to.
    pub fn extend_source(&self, messages: &[JsonMap]) {
        self.source
            .lock()
            .unwrap_or_else(std::sync::PoisonError::into_inner)
            .extend(messages.iter().cloned());
    }

    /// `history_retrieval_enabled = enabled`.
    pub fn set_history_retrieval_enabled(&self, enabled: bool) {
        self.history_retrieval_enabled.store(enabled, Ordering::SeqCst);
    }

    /// `history_retrieval_enabled`.
    pub fn history_retrieval_enabled(&self) -> bool {
        self.history_retrieval_enabled.load(Ordering::SeqCst)
    }

    fn model(&self) -> Option<&ModelInfo> {
        self.provider.model_info()
    }

    fn transport(&self) -> Option<&str> {
        self.model().map(|model| model.transport.as_str())
    }

    /// The model's effective context window (`model_info.context_window`), as it is now: an
    /// account runtime may have observed a new window since construction.
    fn context_window(&self) -> Option<i64> {
        self.model().and_then(ModelInfo::context_window)
    }

    fn runtime_managed(&self) -> bool {
        matches!(self.transport(), Some("codex_app_server" | "claude_code"))
    }

    /// `output_allowance(self.model_info)`: 4096 for no model, as for any other object.
    fn output_allowance(&self) -> Option<i64> {
        match self.model() {
            Some(model) => output_allowance(model, false),
            None => Some(sylvia_provider::catalog::DEFAULT_OUTPUT_ALLOWANCE),
        }
    }

    fn estimate(&self, value: &Json) -> i64 {
        self.token_estimator.estimate(value, self.model())
    }

    /// `_context_options(compaction=False)`.
    fn context_options(&self, compaction: bool) -> JsonMap {
        let mut options = JsonMap::new();
        if let Some(id) = self.request_context_id.as_deref().filter(|id| !id.is_empty()) {
            if self.model().is_some_and(|model| model.provider_key == "openrouter") {
                let suffix = if compaction { "-compact" } else { "" };
                options.insert("_request_context_id".into(), format!("{id}{suffix}").into());
            }
        }
        options
    }

    /// `_check_cancelled()`: `asyncio.CancelledError` once the control was cancelled.
    fn check_cancelled(&self) -> ProviderResult<()> {
        if (self.cancelled)() {
            return Err(ProviderError::new(ErrorKind::Cancelled, ""));
        }
        Ok(())
    }

    /// `_matching(groups)`: the newest checkpoint whose covered keys end on a group boundary,
    /// and how many groups it covers.
    fn matching(&self, groups: &[Group]) -> (Option<JsonMap>, usize) {
        let keys: Vec<&str> = groups.iter().flat_map(|group| group.keys.iter().map(String::as_str)).collect();
        let records = self.records.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        for record in records.iter().rev() {
            let covered: Vec<&Json> = record
                .get("covered_keys")
                .and_then(Json::as_array)
                .map_or_else(Vec::new, |keys| keys.iter().collect());
            if covered.is_empty() || keys.len() < covered.len() {
                continue;
            }
            if !keys[..covered.len()]
                .iter()
                .zip(&covered)
                .all(|(key, covered)| covered.as_str() == Some(*key))
            {
                continue;
            }
            let mut count = 0;
            for (index, group) in groups.iter().enumerate() {
                count += group.keys.len();
                if count == covered.len() {
                    return (Some(record.clone()), index + 1);
                }
            }
        }
        (None, 0)
    }

    /// Whether a group holds the player's own words (not application data).
    fn has_user(group: &Group) -> bool {
        group
            .messages
            .iter()
            .any(|message| role_is(message, "user") && !truthy(message.get("_application_data")))
    }

    fn has_fresh_data(group: &Group) -> bool {
        group.messages.iter().any(|message| truthy(message.get("_fresh_application_data")))
    }

    /// The indices of the first and last groups with the player's own words.
    fn user_bounds(groups: &[Group]) -> Option<(usize, usize)> {
        let users: Vec<usize> = (0..groups.len()).filter(|&index| Self::has_user(&groups[index])).collect();
        Some((*users.first()?, *users.last()?))
    }

    fn kept_user(&self, index: usize, bounds: Option<(usize, usize)>) -> bool {
        bounds.is_some_and(|(first, last)| index == last || (self.pin_first_user && index == first))
    }

    /// `project(messages)`: the messages with covered history replaced by the continuation note.
    pub fn project(&self, messages: &[JsonMap]) -> ProviderResult<Vec<JsonMap>> {
        let (pinned, groups) = groups(messages)?;
        let (record, covered) = self.matching(&groups);
        let Some(record) = record else { return Ok(messages.to_vec()) };
        let summary = record.get("summary").map_or_else(String::new, |summary| match summary {
            Json::String(text) => text.clone(),
            other => pystr::repr_json(other),
        });
        let mut note = JsonMap::new();
        note.insert("role".into(), "user".into());
        note.insert("_application_data".into(), Json::Bool(true));
        note.insert("content".into(), format!("{}{summary}", text("continuation_note_prefix")).into());
        // Builder keeps its original objective; play keeps only the current player action exact.
        let bounds = Self::user_bounds(&groups);
        let mut projected = pinned;
        projected.push(note);
        for (index, group) in groups.iter().enumerate() {
            if index >= covered || self.kept_user(index, bounds) || Self::has_fresh_data(group) {
                projected.extend(group.messages.iter().cloned());
            }
        }
        Ok(projected)
    }

    /// `_fixed_messages(messages)`: material a continuation note cannot shorten.
    fn fixed_messages(&self, messages: &[JsonMap]) -> ProviderResult<Vec<JsonMap>> {
        let (mut fixed, groups) = groups(messages)?;
        let bounds = Self::user_bounds(&groups);
        for (index, group) in groups.iter().enumerate() {
            if self.kept_user(index, bounds) || Self::has_fresh_data(group) {
                fixed.extend(group.messages.iter().cloned());
            }
        }
        Ok(fixed)
    }

    /// `_budgets(options)`: the context capacity and the input budget.
    fn budgets(&self, options: &JsonMap) -> ProviderResult<(i64, i64)> {
        let capacity = self
            .context_window()
            .filter(|&window| window != 0)
            .unwrap_or_else(|| unknown_window(self.transport()));
        let reserve = if self.runtime_managed() {
            0
        } else {
            match ["max_tokens", "max_output_tokens"]
                .iter()
                .find_map(|key| options.get(*key).filter(|v| truthy(Some(v))))
            {
                Some(value) => int_option(value)?,
                None => self.output_allowance().unwrap_or(0),
            }
        };
        let input_budget = 256.max(capacity - reserve - 1024.max(capacity.div_euclid(10)));
        Ok((capacity, input_budget))
    }

    fn tools_json(tools: Option<&[Json]>) -> Json {
        tools.map_or(Json::Null, |tools| Json::Array(tools.to_vec()))
    }

    /// `budget_terms(projected, tools, options)`: the sizes behind the compaction and rebase
    /// decisions, each counted once.
    pub fn budget_terms(
        &self,
        projected: &[JsonMap],
        tools: Option<&[Json]>,
        options: &JsonMap,
    ) -> ProviderResult<BudgetTerms> {
        let (capacity, input_budget) = self.budgets(options)?;
        let fixed = self.fixed_messages(projected)?;
        let request_tools = self.sent_tools(tools);
        let request_tools_json = Self::tools_json(request_tools.as_deref());
        let messages_json =
            |messages: Vec<JsonMap>| Json::Array(messages.into_iter().map(Json::Object).collect());
        let fixed_tokens =
            self.estimate(&Json::Array(vec![messages_json(fixed.clone()), request_tools_json.clone()]));
        let history: Vec<JsonMap> =
            projected.iter().filter(|message| !fixed.contains(message)).cloned().collect();
        let history_tokens = self.estimate(&messages_json(history));
        let remaining = 256.max(input_budget - fixed_tokens);
        let mut terms = BudgetTerms {
            capacity,
            input_budget,
            fixed_tokens,
            history_tokens,
            threshold: (remaining as f64 * 0.70) as i64,
            target: (remaining as f64 * 0.42) as i64,
            wire: None,
        };
        if let (Some(measure), true) = (&self.ledger.measure_wire, self.model().is_some()) {
            let wire = measure(projected, tools);
            let wire_tokens = self.estimate(&Json::Array(vec![messages_json(wire), request_tools_json]));
            terms.wire = Some((
                wire_tokens,
                0.max(wire_tokens - fixed_tokens),
                (remaining as f64 * REBASE_SHARE) as i64,
            ));
        }
        Ok(terms)
    }

    /// `prepare(messages, tools=None, force=False, **options)`: the projected request, after
    /// compacting history when it would not fit (or when `force`).
    pub async fn prepare(
        &self,
        messages: &[JsonMap],
        tools: Option<&[Json]>,
        force: bool,
        options: &JsonMap,
    ) -> ProviderResult<Vec<JsonMap>> {
        self.check_cancelled()?;
        *self.source.lock().unwrap_or_else(std::sync::PoisonError::into_inner) = messages.to_vec();
        let projected = self.project(messages)?;
        let (capacity, input_budget) = self.budgets(options)?;
        let fixed = self.fixed_messages(&projected)?;
        let terms = self.budget_terms(&projected, tools, options)?;
        // Fixed instructions and schemas consume space, but cannot be improved by summarising.
        if self.model().is_some() && terms.fixed_tokens >= input_budget {
            if self.context_window().is_none_or(|window| window == 0) {
                // The model may be large enough: what is missing is its limit (issue 92).
                return Err(token_limit(format!(
                    "Sylvia does not know this model's context limit, so it planned for {} tokens and this request \
                     does not fit. Saved work is intact. To continue, enter the model's context limit as the Optional \
                     Sylvia cap (Settings, Models, Model settings, Advanced model settings), then use Retry.",
                    grouped(capacity)
                )));
            }
            return Err(token_limit(CANNOT_SHORTEN));
        }
        if !force && (self.model().is_none() || terms.history_tokens < terms.threshold) {
            // Legacy's rule says send. In ledger mode the wire decides only whether superseded
            // journal records have grown enough to restart the journal from canonical history.
            if let (Some(rebase), Some((_, excess, rebase_threshold))) =
                (&self.ledger.rebase_wire, terms.wire)
            {
                if excess > rebase_threshold && rebase_threshold > 0 {
                    rebase();
                }
            }
            return Ok(projected);
        }
        let (_, groups) = groups(messages)?;
        let (previous, start) = self.matching(&groups);
        // Always keep the last complete exchange verbatim, including native thinking continuity.
        if groups.len() - start < 2 {
            if force {
                return Err(token_limit(CANNOT_SHORTEN));
            }
            return Ok(projected);
        }
        let unfixed_after = |from: usize| -> Json {
            Json::Array(
                groups[from..]
                    .iter()
                    .flat_map(|group| group.messages.iter())
                    .filter(|message| !fixed.contains(message))
                    .cloned()
                    .map(Json::Object)
                    .collect(),
            )
        };
        let mut stop = start + 1;
        while stop < groups.len() - 1 && self.estimate(&unfixed_after(stop)) > terms.target {
            stop += 1;
        }
        if force {
            stop = if self.pin_first_user {
                stop.max(start + 1.max((groups.len() - start) / 2))
            } else {
                groups.len() - 1
            };
            stop = stop.min(groups.len() - 1);
        }
        let source: Vec<Json> = groups[start..stop]
            .iter()
            .flat_map(|group| group.messages.iter())
            .map(|message| Json::Object(safe_message(message)))
            .collect();
        let previous_summary =
            previous.as_ref().map_or_else(String::new, |record| match record.get("summary") {
                Some(Json::String(text)) => text.clone(),
                Some(other) => pystr::repr_json(other),
                None => String::new(),
            });
        (self.progress)(Some("Compacting context · saved conversation is kept"));
        let _cleared = ProgressCleared(self.progress.as_ref());
        // Byte-sized slices also fit conservative non-Codex admission. Half the token capacity
        // in bytes leaves room for JSON escaping, the prior note and the response.
        let allowance = 240_000.min(256.max(capacity.div_euclid(2))) as usize;
        let source_text = compact(&Json::Array(source.clone()));
        let summary_limit = SUMMARY_LIMIT.min(256.max(capacity.div_euclid(32)) as usize);
        // An old note may itself exceed a smaller model's budget: fold it in as bounded
        // historical source before the newly covered messages, without truncating it.
        let summary_source: Vec<char> =
            compact(&json!({"prior_note": previous_summary, "new_source": source})).chars().collect();
        let byte_len = |piece: &[char]| piece.iter().map(|c| c.len_utf8()).sum::<usize>();
        let mut summary = String::new();
        let mut offset = 0;
        while offset < summary_source.len() {
            let mut end = summary_source.len().min(offset + allowance);
            while byte_len(&summary_source[offset..end]) > allowance {
                end = offset + 1.max((end - offset) / 2);
            }
            let piece: String = summary_source[offset..end].iter().collect();
            let instruction = text("summary_instruction")
                .replace("{summary_limit}", &summary_limit.to_string())
                .replace("{words}", &16.max(summary_limit / 14).to_string());
            let request = vec![
                json!({"role": "system", "content": format!("{}{instruction}", text("summary_prompt"))}),
                json!({"role": "user", "content": compact(&json!({
                    "previous_note": summary,
                    "source_offset": offset,
                    "source_total_characters": summary_source.len(),
                    "source_fragment": piece,
                }))}),
            ];
            let mut summary_options = self.context_options(true);
            if !self.runtime_managed() {
                let allowance_tokens = self.output_allowance().unwrap_or(i64::MAX);
                summary_options.insert(
                    "max_tokens".into(),
                    8192.min(allowance_tokens).min(256.max(capacity.div_euclid(8))).into(),
                );
            }
            let summarize = |messages: Vec<Json>| ProviderRequest {
                messages,
                tools: None,
                stream: false,
                timeout: 60.0,
                options: summary_options.clone(),
            };
            self.check_cancelled()?;
            let mut response = self.provider.generate(summarize(request)).await?;
            self.check_cancelled()?;
            let content_len =
                |response: &ProviderResponse| response.content.as_deref().map_or(0, |c| c.chars().count());
            let finished = |response: &ProviderResponse| {
                matches!(response.finish_reason.as_text(), Some("stop" | "end_turn"))
            };
            // Repair only a complete, bounded note; never replay the source actions or
            // truncate facts locally. A second invalid note leaves the old checkpoint intact.
            if response.tool_calls.is_empty()
                && finished(&response)
                && response.content.as_deref().is_some_and(|content| !content.is_empty())
                && content_len(&response) > summary_limit
                && response.content.as_deref().map_or(0, str::len) <= allowance
            {
                let shorten = text("shorten_instruction")
                    .replace("{summary_limit}", &summary_limit.to_string())
                    .replace("{words}", &12.max(summary_limit / 18).to_string());
                let note = response.content.clone().unwrap_or_default();
                response = self
                    .provider
                    .generate(summarize(vec![
                        json!({"role": "system", "content": format!("{}{shorten}", text("summary_prompt"))}),
                        json!({"role": "user", "content": compact(&json!({"note": note}))}),
                    ]))
                    .await?;
                self.check_cancelled()?;
            }
            let empty = response.content.as_deref().is_none_or(str::is_empty);
            if !response.tool_calls.is_empty()
                || !finished(&response)
                || empty
                || content_len(&response) > summary_limit
            {
                let reason = if !empty && content_len(&response) > summary_limit {
                    format!(
                        "Context compaction note has {} characters; the limit is {summary_limit}. ",
                        content_len(&response)
                    )
                } else {
                    "Context compaction did not produce a complete continuation note. ".to_owned()
                };
                return Err(ProviderError::new(
                    ErrorKind::ContextCompaction,
                    format!("{reason}Saved conversation and the previous checkpoint were kept."),
                ));
            }
            summary = response.content.unwrap_or_default();
            offset = end;
        }
        self.check_cancelled()?;
        // Python rereads its (shared) message list here; this request owns its copy, so the
        // check can only pass.
        if self::groups(messages)?.1 != groups {
            return Err(ProviderError::new(
                ErrorKind::InvalidResponse,
                "Conversation changed during compaction; saved work was kept",
            ));
        }
        let mut payload = JsonMap::new();
        payload.insert("kind".into(), CHECKPOINT_KIND.into());
        if let Some(owner) = &self.record_owner {
            payload.insert("context_owner".into(), Json::Object(owner.clone()));
        }
        payload.insert("version".into(), 1.into());
        payload.insert("checkpoint_id".into(), (self.checkpoint_id)().into());
        payload.insert(
            "previous_checkpoint_id".into(),
            previous.as_ref().and_then(|record| record.get("checkpoint_id").cloned()).unwrap_or(Json::Null),
        );
        payload.insert(
            "covered_keys".into(),
            Json::Array(
                groups[..stop].iter().flat_map(|group| group.keys.iter().cloned().map(Json::from)).collect(),
            ),
        );
        payload.insert("summary".into(), summary.into());
        payload.insert("source_archive".into(), source_text.into());
        payload.insert("model_id".into(), self.model().map_or("unknown", |model| model.id.as_str()).into());
        payload.insert("covered_exchanges".into(), stop.into());
        // The owner's atomic append must finish before this working copy becomes selectable.
        (self.save)(payload.clone()).await?;
        self.check_cancelled()?;
        self.records.lock().unwrap_or_else(std::sync::PoisonError::into_inner).push(payload);
        self.project(messages)
    }

    /// `read_history(params)`: this session only, with explicit pagination and no silent
    /// truncation.
    pub fn read_history(&self, params: &JsonMap) -> JsonMap {
        let mut result = JsonMap::new();
        let source_id = params.get("source_id").cloned().unwrap_or_else(|| Json::from("current"));
        let records = self.records();
        if source_id == "list" {
            let mut sources = vec![Json::from("current")];
            sources.extend(
                records.iter().map(|record| record.get("checkpoint_id").cloned().unwrap_or(Json::Null)),
            );
            result.insert("sources".into(), Json::Array(sources));
            result.insert(
                "instruction".into(),
                "Read a source_id with offset and limit. Archives are historical.".into(),
            );
            return result;
        }
        let text: String = if source_id == "current" {
            let source = self.source.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
            compact(&Json::Array(
                source
                    .iter()
                    .filter(|message| !role_is(message, "system"))
                    .map(|m| Json::Object(safe_message(m)))
                    .collect(),
            ))
        } else {
            let Some(record) = records.iter().find(|record| record.get("checkpoint_id") == Some(&source_id))
            else {
                result.insert("error".into(), "Unknown source in this session; use source_id=list".into());
                return result;
            };
            match record.get("source_archive") {
                Some(Json::String(text)) => text.clone(),
                Some(other) => pystr::repr_json(other),
                None => String::new(),
            }
        };
        let integer = |value: Option<&Json>, default: i64| -> Option<i64> {
            match value {
                None => Some(default),
                Some(Json::Number(number)) if !number.is_f64() => number.as_i64(),
                _ => None,
            }
        };
        let (offset, limit) = (integer(params.get("offset"), 0), integer(params.get("limit"), 8000));
        let (Some(mut offset), Some(limit)) =
            (offset.filter(|&offset| offset >= 0), limit.filter(|limit| (1..=16_000).contains(limit)))
        else {
            result.insert(
                "error".into(),
                "offset must be nonnegative; limit must be 1–16000 characters".into(),
            );
            return result;
        };
        let characters: Vec<char> = text.chars().collect();
        let total = characters.len() as i64;
        if let Some(query) = params.get("query").filter(|query| truthy(Some(query))) {
            let Some(query) = query.as_str() else {
                result.insert("error".into(), "query must be text".into());
                return result;
            };
            match pyre::ignorecase_search(&text, query, offset as usize) {
                None => {
                    result.insert("found".into(), Json::Bool(false));
                    result.insert("source_id".into(), source_id);
                    return result;
                }
                Some(start) => offset = start as i64,
            }
        }
        let end = total.min(offset + limit);
        let content: String = if offset < end {
            characters[offset as usize..end as usize].iter().collect()
        } else {
            String::new()
        };
        result.insert("source_id".into(), source_id);
        result.insert("offset".into(), offset.into());
        result.insert("content".into(), content.into());
        result.insert("total_characters".into(), total.into());
        result.insert("next_offset".into(), if end < total { Json::from(end) } else { Json::Null });
        result
    }

    /// `_request_tools(tools)`: historical retrieval offered after compaction where the host
    /// admits reference work.
    fn request_tools(&self, tools: Option<&[Json]>) -> Option<Vec<Json>> {
        let offered = tools.is_some_and(|tools| {
            tools.iter().any(|tool| {
                tool.get("function").and_then(|function| function.get("name")).and_then(Json::as_str)
                    == Some("read_session_history")
            })
        });
        let has_records = !self.records.lock().unwrap_or_else(std::sync::PoisonError::into_inner).is_empty();
        if has_records && self.history_retrieval_enabled() && !offered {
            let mut list = tools.map(<[Json]>::to_vec).unwrap_or_default();
            list.push(history_tool());
            return Some(list);
        }
        tools.map(<[Json]>::to_vec)
    }

    /// `_sent_tools(tools)`: the tool list the provider is sent for a request offering `tools`.
    fn sent_tools(&self, tools: Option<&[Json]>) -> Option<Vec<Json>> {
        let request_tools = self.request_tools(tools);
        match &self.ledger.wire_tools {
            Some(wire_tools) => wire_tools(request_tools),
            None => request_tools,
        }
    }

    /// `_wire(projected, tools)`: the provider message list; ledger mode journals it first.
    async fn wire(&self, projected: &[JsonMap], tools: Option<&[Json]>) -> ProviderResult<Vec<JsonMap>> {
        let request_tools = self.request_tools(tools);
        if let Some(finalize) = &self.ledger.finalize_request {
            return finalize(projected.to_vec(), request_tools).await;
        }
        let offered = request_tools.is_some_and(|tools| !tools.is_empty());
        match &self.prepare_continuation {
            Some(prepare) => prepare(projected.to_vec(), offered).await,
            None => Ok(projected.to_vec()),
        }
    }

    /// `_retry_without_thinking(error, stripped)`: Claude rejected a replayed thinking block;
    /// strip every one and retry once.
    fn retry_without_thinking(&self, error: &ProviderError, stripped: bool) -> bool {
        let Some(strip) = &self.ledger.strip_thinking else { return false };
        if stripped
            || self.transport() != Some("anthropic_messages")
            || !sylvia_provider::anthropic::rejects_replayed_thinking(error)
        {
            return false;
        }
        strip();
        true
    }

    /// The inner provider's request for the projected messages.
    async fn inner_request(
        &self,
        projected: &[JsonMap],
        tools: Option<&[Json]>,
        timeout: f64,
        options: &JsonMap,
    ) -> ProviderResult<ProviderRequest> {
        let wire = self.wire(projected, tools).await?;
        Ok(ProviderRequest {
            messages: crate::request_data::strip_data_markers(&wire).into_iter().map(Json::Object).collect(),
            tools: self.sent_tools(tools),
            stream: false,
            timeout,
            options: options.clone(),
        })
    }

    fn with_context_options(&self, mut options: JsonMap) -> JsonMap {
        for (key, value) in self.context_options(false) {
            options.insert(key, value);
        }
        options
    }
}

/// Whether a chunk carries output (`content or reasoning_content or tool_call_delta`).
fn has_output(chunk: &ProviderStreamChunk) -> bool {
    !chunk.content.is_empty() || !chunk.reasoning_content.is_empty() || truthy(chunk.tool_call_delta.as_ref())
}

fn length_reason(reason: Option<&str>) -> bool {
    matches!(reason, Some("length" | "max_tokens"))
}

fn output_limit() -> ProviderError {
    ProviderError::new(ErrorKind::OutputLimit, "The model reached its response limit")
}

fn messages_of(request: &ProviderRequest) -> ProviderResult<Vec<JsonMap>> {
    request
        .messages
        .iter()
        .map(|message| {
            message.as_object().cloned().ok_or_else(|| internal("A request message is not an object"))
        })
        .collect()
}

/// What the streaming retry loop does next.
enum Phase<'a> {
    Start(ProviderRequest),
    Open {
        messages: Vec<JsonMap>,
        tools: Option<Vec<Json>>,
        timeout: f64,
        options: JsonMap,
        projected: Vec<JsonMap>,
        attempt: usize,
        stripped: bool,
    },
    Streaming {
        stream: ChunkStream<'a>,
        projected: Vec<JsonMap>,
        messages: Vec<JsonMap>,
        tools: Option<Vec<Json>>,
        timeout: f64,
        options: JsonMap,
        attempt: usize,
        stripped: bool,
        emitted: bool,
    },
    Raise(ProviderError),
    Done,
}

impl Provider for WorkingContextProvider {
    fn model_info(&self) -> Option<&ModelInfo> {
        self.provider.model_info()
    }

    /// `generate(messages, tools=None, stream=False, timeout=60.0, **options)`.
    fn generate(&self, request: ProviderRequest) -> BoxFuture<'_, ProviderResult<ProviderResponse>> {
        Box::pin(async move {
            let messages = messages_of(&request)?;
            let tools = request.tools.clone();
            let options = self.with_context_options(request.options);
            let mut projected = self.prepare(&messages, tools.as_deref(), false, &options).await?;
            let mut stripped = false;
            for attempt in 0..4 {
                let outcome = async {
                    let inner =
                        self.inner_request(&projected, tools.as_deref(), request.timeout, &options).await?;
                    let response = self.provider.generate(inner).await?;
                    match response.finish_reason.as_text() {
                        Some("context_length") => Err(ProviderError::token_limit_rejection()),
                        Some("length" | "max_tokens") => Err(output_limit()),
                        _ => Ok(response),
                    }
                }
                .await;
                match outcome {
                    Ok(response) => return Ok(response),
                    Err(error) if error.is(ErrorKind::TokenLimit) => {
                        if attempt == 3 {
                            return Err(error);
                        }
                        projected = self.prepare(&messages, tools.as_deref(), true, &options).await?;
                    }
                    Err(error) if error.is(ErrorKind::InvalidRequest) => {
                        if !self.retry_without_thinking(&error, stripped) {
                            return Err(error);
                        }
                        stripped = true;
                    }
                    Err(error) => return Err(error),
                }
            }
            // Python falls out of its loop and returns None here.
            Err(internal("The working context returned no response (None)"))
        })
    }

    /// `generate_stream(messages, tools=None, timeout=120.0, **options)`.
    fn generate_stream(&self, request: ProviderRequest) -> ChunkStream<'_> {
        Box::pin(futures_util::stream::unfold(Phase::Start(request), move |phase| async move {
            let mut phase = phase;
            loop {
                phase = match phase {
                    Phase::Done => return None,
                    Phase::Raise(error) => return Some((Err(error), Phase::Done)),
                    Phase::Start(request) => {
                        let messages = match messages_of(&request) {
                            Ok(messages) => messages,
                            Err(error) => return Some((Err(error), Phase::Done)),
                        };
                        let options = self.with_context_options(request.options);
                        match self.prepare(&messages, request.tools.as_deref(), false, &options).await {
                            Ok(projected) => Phase::Open {
                                messages,
                                tools: request.tools,
                                timeout: request.timeout,
                                options,
                                projected,
                                attempt: 0,
                                stripped: false,
                            },
                            Err(error) => return Some((Err(error), Phase::Done)),
                        }
                    }
                    Phase::Open { messages, tools, timeout, options, projected, attempt, stripped } => {
                        if attempt >= 4 {
                            return None;
                        }
                        match self.inner_request(&projected, tools.as_deref(), timeout, &options).await {
                            Ok(inner) => Phase::Streaming {
                                stream: self.provider.generate_stream(inner),
                                projected,
                                messages,
                                tools,
                                timeout,
                                options,
                                attempt,
                                stripped,
                                emitted: false,
                            },
                            Err(error) => {
                                match self
                                    .recover(
                                        error,
                                        &messages,
                                        tools.as_deref(),
                                        &options,
                                        attempt,
                                        stripped,
                                        false,
                                        projected,
                                    )
                                    .await
                                {
                                    Ok(next) => next.into_open(messages, tools, timeout, options),
                                    Err(error) => return Some((Err(error), Phase::Done)),
                                }
                            }
                        }
                    }
                    Phase::Streaming {
                        mut stream,
                        projected,
                        messages,
                        tools,
                        timeout,
                        options,
                        attempt,
                        stripped,
                        emitted,
                    } => {
                        use futures_util::StreamExt as _;
                        let failure = match stream.next().await {
                            None => return None,
                            Some(Ok(chunk)) => {
                                let reason = chunk.finish_reason.as_ref().and_then(|reason| reason.as_text());
                                if reason == Some("context_length") {
                                    ProviderError::token_limit_rejection()
                                } else if length_reason(reason) {
                                    // Some transports send their last output with the cut-off signal.
                                    if has_output(&chunk) {
                                        let mut kept = chunk;
                                        kept.finish_reason = None;
                                        return Some((Ok(kept), Phase::Raise(output_limit())));
                                    }
                                    output_limit()
                                } else {
                                    let emitted = emitted || has_output(&chunk);
                                    let next = Phase::Streaming {
                                        stream,
                                        projected,
                                        messages,
                                        tools,
                                        timeout,
                                        options,
                                        attempt,
                                        stripped,
                                        emitted,
                                    };
                                    return Some((Ok(chunk), next));
                                }
                            }
                            Some(Err(error)) => error,
                        };
                        drop(stream);
                        match self
                            .recover(
                                failure,
                                &messages,
                                tools.as_deref(),
                                &options,
                                attempt,
                                stripped,
                                emitted,
                                projected,
                            )
                            .await
                        {
                            Ok(next) => next.into_open(messages, tools, timeout, options),
                            Err(error) => return Some((Err(error), Phase::Done)),
                        }
                    }
                };
            }
        }))
    }

    /// `close()`: the session resolver owns the wrapped provider's lifetime.
    fn close(&self) -> BoxFuture<'_, ()> {
        Box::pin(async {})
    }

    /// `release_idle()`: releasing what the wrapped provider keeps between requests does not end
    /// its life.
    fn release_idle(&self) -> BoxFuture<'_, ()> {
        self.provider.release_idle()
    }

    fn wrapped(&self) -> Option<&dyn Provider> {
        Some(self.provider.as_ref())
    }
}

/// The next attempt after a failure the streaming loop retries.
struct Retry {
    projected: Vec<JsonMap>,
    attempt: usize,
    stripped: bool,
}

impl Retry {
    fn into_open<'a>(
        self,
        messages: Vec<JsonMap>,
        tools: Option<Vec<Json>>,
        timeout: f64,
        options: JsonMap,
    ) -> Phase<'a> {
        Phase::Open {
            messages,
            tools,
            timeout,
            options,
            projected: self.projected,
            attempt: self.attempt,
            stripped: self.stripped,
        }
    }
}

impl WorkingContextProvider {
    /// The `except` clauses of `generate_stream`: a token limit before any output re-prepares
    /// with `force`; a rejected replayed thinking block strips them once; anything else (or any
    /// failure after output) is raised.
    #[allow(clippy::too_many_arguments)]
    async fn recover(
        &self,
        error: ProviderError,
        messages: &[JsonMap],
        tools: Option<&[Json]>,
        options: &JsonMap,
        attempt: usize,
        stripped: bool,
        emitted: bool,
        projected: Vec<JsonMap>,
    ) -> ProviderResult<Retry> {
        if error.is(ErrorKind::TokenLimit) {
            if emitted || attempt == 3 {
                return Err(error);
            }
            let projected = self.prepare(messages, tools, true, options).await?;
            return Ok(Retry { projected, attempt: attempt + 1, stripped });
        }
        if error.is(ErrorKind::InvalidRequest) {
            if emitted || !self.retry_without_thinking(&error, stripped) {
                return Err(error);
            }
            return Ok(Retry { projected, attempt: attempt + 1, stripped: true });
        }
        Err(error)
    }
}

/// `compact_provider(provider_input)`'s message list: the saved history as the provider is sent it
/// (`_provider_history` for a Builder chat, `adapter._messages(...)` for a DM session).
pub type CompactMessages = Arc<
    dyn Fn(
            crate::session_loop::SessionProviderInput,
        ) -> BoxFuture<'static, sylvia_base::CodedResult<Vec<JsonMap>>>
        + Send
        + Sync,
>;

/// The `/compact` command's session provider (`interfaces/commands/sessions.py`'s
/// `compact_provider`): the saved messages, then [`compact_saved_messages`].
struct CompactProvider {
    provider: Arc<dyn Provider>,
    messages: CompactMessages,
}

impl crate::session_loop::SessionProvider for CompactProvider {
    fn run<'a>(
        &'a self,
        input: crate::session_loop::SessionProviderInput,
        events: &'a mut dyn crate::session_loop::SessionEvents,
    ) -> BoxFuture<'a, Result<(), crate::session_loop::SessionProviderError>> {
        Box::pin(async move {
            let messages = (self.messages)(input.clone()).await?;
            compact_saved_messages(
                Arc::clone(&self.provider),
                &input.history,
                &messages,
                &input.control,
                events,
            )
            .await
        })
    }
}

/// `compact_provider`: the session provider `run_compaction` runs for `/compact`.
pub fn compact_provider(
    provider: Arc<dyn Provider>,
    messages: CompactMessages,
) -> Arc<dyn crate::session_loop::SessionProvider> {
    Arc::new(CompactProvider { provider, messages })
}

/// `compact_saved_messages(provider, context, messages, control)`: the same checkpoint generator
/// as a turn's, for an explicit, action-free Compact request. Each checkpoint is emitted as
/// tool evidence, then accepted.
pub async fn compact_saved_messages(
    provider: Arc<dyn Provider>,
    context: &sylvia_domain::session::SessionContext,
    messages: &[JsonMap],
    control: &Arc<crate::session_loop::ProviderControl>,
    events: &mut dyn crate::session_loop::SessionEvents,
) -> Result<(), crate::session_loop::SessionProviderError> {
    use crate::session_loop::ProviderEvent;
    use sylvia_base::CodedError;

    let bridge = Arc::new(CheckpointBridge::new());
    let saving = Arc::clone(&bridge);
    let mut options = WorkingContextOptions::new(Arc::new(move |payload: JsonMap| {
        let saving = Arc::clone(&saving);
        Box::pin(async move { saving.save(payload).await })
    }));
    options.records = context.messages.iter().map(|message| message.content().clone()).collect();
    options.record_owner =
        messages.first().and_then(|message| message.get("_context_owner")).and_then(Json::as_object).cloned();
    let (cancelled, progress) = (Arc::clone(control), Arc::clone(control));
    options.cancelled = Arc::new(move || cancelled.cancelled());
    options.progress = Arc::new(move |text: Option<&str>| {
        // `control.set_status_hint`: a progress update cannot fail on a live control.
        let _ = progress.set_status_hint(text);
    });
    options.pin_first_user = context.session.role() == sylvia_domain::authority::WriterRole::Builder;
    let working = WorkingContextProvider::new(provider, options);
    control.admit(|| ())?;
    let refused = || {
        crate::session_loop::SessionProviderError::from(
            CodedError::rejected("Context compaction was cancelled; saved work was kept")
                .with_error_type("CommandRejected"),
        )
    };
    let no_options = JsonMap::new();
    let prepare = working.prepare(messages, None, true, &no_options);
    tokio::pin!(prepare);
    // `while not future.done() or not bridge.pending.empty()`: each queued checkpoint becomes
    // evidence before it is accepted; a cancelled control refuses at the next poll.
    loop {
        if control.cancelled() {
            return Err(refused());
        }
        tokio::select! {
            biased;
            write = bridge.next() => {
                let Some(write) = write else { continue };
                if write.accepted.is_closed() {
                    continue;
                }
                let id = write.payload.get("checkpoint_id").ok_or_else(|| {
                    CodedError::new("internal_error", "'checkpoint_id'").with_error_type("KeyError")
                })?;
                let message_id = format!("context-{}", sylvia_domain::pyops::py_str(id));
                events.emit(ProviderEvent::Evidence { message_id, payload: write.payload.clone() }).await?;
                let _ = write.accepted.send(());
            }
            outcome = &mut prepare => {
                // A save waits for its acceptance, so no checkpoint is left queued once it ends.
                while let Some(write) = bridge.take() {
                    if write.accepted.is_closed() {
                        continue;
                    }
                    let id = write.payload.get("checkpoint_id").ok_or_else(|| {
                        CodedError::new("internal_error", "'checkpoint_id'").with_error_type("KeyError")
                    })?;
                    let message_id = format!("context-{}", sylvia_domain::pyops::py_str(id));
                    events.emit(ProviderEvent::Evidence { message_id, payload: write.payload.clone() }).await?;
                    let _ = write.accepted.send(());
                }
                outcome?;
                return Ok(());
            }
            _ = tokio::time::sleep(std::time::Duration::from_millis(50)) => {}
        }
    }
}
