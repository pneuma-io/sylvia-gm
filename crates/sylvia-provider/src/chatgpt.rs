//! `provider/_chatgpt.py`: subscription inference through Codex, with Sylvia retaining the tool
//! loop. Lane 5.
//!
//! This part holds the request side, the rules and the kept-thread pool: which thread mode
//! applies ([`thread_setting`]), the App Server projection of a request
//! ([`app_server_request`]), the usage the runtime reports, the late crediting of a pending
//! call's usage, whether a request answers a kept thread's call ([`call_tail`],
//! [`in_place_answer`]), and the process-wide pool of idle runtime threads ([`KeptThreads`]).
//! `ChatGPTProvider` follows in its own PR.

use crate::claude_kept::Timer;
use crate::codex_rpc::{python_resolve, CodexConnection};
use crate::config::env::Environment;
use crate::context_mode::{context_mode, LEDGER_MODE};
use crate::error::{ErrorKind, ProviderError};
use crate::hooks::LateUsageListener;
use crate::pyobj::{py_eq, py_str};
use crate::types::{CacheUsage, ProviderResponse, TokenUsage};
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};

/// `_USAGE_SETTLE_SECONDS`: how long the runtime may record a finished response before a turn
/// that ends in a tool call is interrupted (0.15 s lost 2 of 32 reports in a live fight).
pub const USAGE_SETTLE_SECONDS: f64 = 0.3;
/// `_USAGE_REPORT_SECONDS`: how long the usage report is awaited after the interrupt.
pub const USAGE_REPORT_SECONDS: f64 = 1.0;
/// `_THREAD_SWITCH`.
pub const THREAD_SWITCH: &str = "SYLVIA_CHATGPT_THREAD";
/// `_FRESH_THREADS`.
pub const FRESH_THREADS: &str = "fresh";
/// `_DEFAULT_THREAD_MODE`.
pub const DEFAULT_THREAD_MODE: &str = "answer";
/// `_THREAD_MODES`: the switch's values and the mode each runs.
pub const THREAD_MODES: [(&str, &str); 3] =
    [("stable", "interrupt"), ("answer", "answer"), ("answer-draft", "answer")];
/// `_DRAFT_KEEPING_VALUE`.
pub const DRAFT_KEEPING_VALUE: &str = "answer-draft";
/// `_KEPT_THREAD_LIMIT`.
pub const KEPT_THREAD_LIMIT: usize = 4;
/// `_KEPT_THREAD_IDLE_SECONDS`.
pub const KEPT_THREAD_IDLE_SECONDS: f64 = 600.0;
/// `_PENDING_TURN_IDLE_SECONDS`: a thread whose turn waits for a call's result (the player's
/// reading and typing time; the runtime and the provider's cache keep at least 30 minutes).
pub const PENDING_TURN_IDLE_SECONDS: f64 = 1800.0;
/// `_ABANDONED_REPORT_LIMIT`.
pub const ABANDONED_REPORT_LIMIT: usize = 64;
/// `_TURN_MOVED_ON`: events of a waiting turn showing the runtime went on without the answer.
pub const TURN_MOVED_ON: [&str; 6] = [
    "turn/completed",
    "item/tool/call",
    "item/agentMessage/delta",
    "item/reasoning/textDelta",
    "item/reasoning/summaryTextDelta",
    "error",
];

/// `thread_setting(environ)`: the `SYLVIA_CHATGPT_THREAD` value that applies, one of
/// [`THREAD_MODES`]' names or [`FRESH_THREADS`]. Unset or unknown: `answer` with the context
/// ledger, `fresh` without it.
pub fn thread_setting(environment: &Environment) -> String {
    let value = environment.get(THREAD_SWITCH).and_then(Result::ok).unwrap_or("");
    let setting = sylvia_base::pystr::casefold(sylvia_base::pystr::strip(value));
    if setting == FRESH_THREADS || THREAD_MODES.iter().any(|(name, _)| *name == setting) {
        return setting;
    }
    // A kept thread only pays with requests that extend the previous one (the ledger).
    let ledger = context_mode(environment, Some("codex_app_server"), None) == LEDGER_MODE;
    (if ledger { DEFAULT_THREAD_MODE } else { FRESH_THREADS }).to_owned()
}

/// `type(value) is int and value >= 0`.
fn count(map: &JsonMap, key: &str) -> Option<i64> {
    match map.get(key)? {
        Json::Number(number) if !number.is_f64() => number.as_i64().filter(|value| *value >= 0),
        _ => None,
    }
}

/// `_reported_usage(params)`: the runtime's last response, never its cumulative thread total.
pub fn reported_usage(params: &Json) -> Option<(TokenUsage, CacheUsage)> {
    let last = params.get("tokenUsage")?.as_object()?.get("last")?.as_object()?;
    let (prompt, output, total) =
        (count(last, "inputTokens")?, count(last, "outputTokens")?, count(last, "totalTokens")?);
    let read = count(last, "cachedInputTokens");
    Some((
        TokenUsage::from_ints(prompt, output, total, count(last, "reasoningOutputTokens")),
        CacheUsage {
            cache_read_tokens: read,
            cache_write_tokens: count(last, "cacheWriteInputTokens"),
            cache_miss_tokens: read.map(|read| (prompt - read).max(0)),
            total_prompt_tokens: prompt,
        },
    ))
}

/// `_usage_figures(usage, cache)`.
pub fn usage_figures(usage: &TokenUsage, cache: &CacheUsage) -> JsonMap {
    let mut figures = JsonMap::new();
    figures.insert("input".into(), usage.prompt_tokens.to_json());
    figures.insert("cached".into(), cache.cache_read_tokens.map_or(Json::Null, Json::from));
    figures.insert("output".into(), usage.completion_tokens.to_json());
    figures
}

/// The App Server form of a request: its dynamic tools, injected items and base instructions.
#[derive(Debug, Clone, PartialEq)]
pub struct AppServerRequest {
    pub dynamic_tools: Vec<Json>,
    pub items: Vec<Json>,
    pub system_instructions: Vec<Json>,
}

/// `app_server_request(model_id, messages, tools)`.
pub fn app_server_request(
    model_id: &str,
    messages: &[Json],
    tools: Option<&[Json]>,
) -> Result<AppServerRequest, crate::chat::ChatError> {
    let converted = crate::responses::format_request(model_id, messages, tools, true, &JsonMap::new())?;
    let missing =
        |key: &str| crate::chat::ChatError::from(crate::pyobj::Incidental(crate::pyobj::py_repr_str(key)));
    let mut dynamic_tools = Vec::new();
    for tool in converted.get("tools").and_then(Json::as_array).into_iter().flatten() {
        let field = |key: &str| tool.get(key).cloned().ok_or_else(|| missing(key));
        let mut entry = JsonMap::new();
        entry.insert("type".into(), Json::String("function".into()));
        entry.insert("name".into(), field("name")?);
        entry.insert("description".into(), field("description")?);
        entry.insert("inputSchema".into(), field("parameters")?);
        dynamic_tools.push(Json::Object(entry));
    }
    let mut items = Vec::new();
    let mut system_instructions = Vec::new();
    for original in converted.get("input").and_then(Json::as_array).ok_or_else(|| missing("input"))? {
        let mut item = original.as_object().cloned().unwrap_or_default();
        // App Server excludes injected system messages; its dedicated field owns them.
        if item.get("role").and_then(Json::as_str) == Some("system") {
            system_instructions.push(item.get("content").cloned().ok_or_else(|| missing("content"))?);
            continue;
        }
        if item.get("type").and_then(Json::as_str) == Some("function_call") {
            item.insert("namespace".into(), Json::String("sylvia".into()));
        }
        if let Some(role) = item.get("role").cloned() {
            let content = item.get("content").cloned().ok_or_else(|| missing("content"))?;
            item.insert("type".into(), Json::String("message".into()));
            let kind = if role.as_str() == Some("assistant") { "output_text" } else { "input_text" };
            item.insert("content".into(), serde_json::json!([{"type": kind, "text": content}]));
        }
        items.push(Json::Object(item));
    }
    Ok(AppServerRequest { dynamic_tools, items, system_instructions })
}

/// `_KeptThreadMismatchError` (and its `_AnswerInPlaceError`): a kept thread cannot serve this
/// request, a fresh thread must. A `ProviderConnectionError` with a reason code.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct KeptThreadMismatch {
    pub reason: &'static str,
    pub message: &'static str,
    /// `_AnswerInPlaceError`: the request cannot be delivered as the pending call's answer.
    pub in_place: bool,
}

impl KeptThreadMismatch {
    /// The `ProviderConnectionError` Python raises.
    pub fn error(&self) -> ProviderError {
        ProviderError::new(ErrorKind::Connection, self.message)
    }
}

fn get<'a>(item: &'a Json, key: &str) -> Option<&'a Json> {
    item.as_object().and_then(|item| item.get(key))
}

fn is(item: &Json, key: &str, value: &str) -> bool {
    get(item, key).and_then(Json::as_str) == Some(value)
}

/// `_item_text(item)`.
pub fn item_text(item: &Json) -> String {
    match get(item, "content") {
        Some(Json::String(text)) => text.clone(),
        Some(Json::Array(parts)) => parts
            .iter()
            .filter(|part| part.is_object())
            .map(|part| py_str(get(part, "text").unwrap_or(&Json::String(String::new()))))
            .collect(),
        _ => String::new(),
    }
}

/// `_strings(value)`: every text in a JSON value, depth first.
pub fn strings(value: &Json) -> Vec<String> {
    match value {
        Json::String(text) => vec![text.clone()],
        Json::Object(map) => map.values().flat_map(strings).collect(),
        Json::Array(items) => items.iter().flat_map(strings).collect(),
        _ => Vec::new(),
    }
}

/// `_call_tail(tail, call_id, spoken, error, dropped_draft_ok)`: Sylvia's copy of the kept
/// thread's call, its result and what follows. A request continues the thread only when it
/// appends exactly what the model streamed (`spoken`), its call `call_id` and the result.
pub fn call_tail<'a>(
    tail: &'a [Json],
    call_id: &str,
    spoken: &str,
    in_place: bool,
    dropped_draft_ok: bool,
) -> Result<(&'a Json, &'a Json, &'a [Json]), KeptThreadMismatch> {
    let fail = |reason, message| KeptThreadMismatch { reason, message, in_place };
    let mut index = 0;
    let mut text = String::new();
    while index < tail.len()
        && (is(&tail[index], "type", "reasoning")
            || (is(&tail[index], "type", "message") && is(&tail[index], "role", "assistant")))
    {
        if is(&tail[index], "type", "message") {
            text.push_str(&item_text(&tail[index]));
        }
        index += 1;
    }
    let no_result = || fail("no_call_result", "The request does not answer the kept tool call.");
    if tail.len() < index + 2 {
        return Err(no_result());
    }
    let (call, output) = (&tail[index], &tail[index + 1]);
    if !is(call, "type", "function_call")
        || !is(call, "call_id", call_id)
        || !is(output, "type", "function_call_output")
        || !is(output, "call_id", call_id)
    {
        return Err(no_result());
    }
    if !spoken.is_empty() && text.is_empty() {
        // The owner drops the text of a round whose call was not narrative.
        if !dropped_draft_ok {
            return Err(fail("model_text_dropped", "The request dropped the model's own text."));
        }
    } else if text != spoken {
        return Err(fail("model_text_differs", "The request does not hold the model's own text."));
    }
    Ok((call, output, &tail[index + 2..]))
}

/// `_in_place_answer(tail, call_id, spoken, dropped_draft_ok)`: the pending call's result and
/// the user-role texts that follow it. An assistant message after the result is accepted (and
/// not sent) only when it is exactly one text value of the result or the call's arguments.
pub fn in_place_answer(
    tail: &[Json],
    call_id: &str,
    spoken: &str,
    dropped_draft_ok: bool,
) -> Result<(String, Vec<String>), KeptThreadMismatch> {
    let (call, output, rest) = call_tail(tail, call_id, spoken, true, dropped_draft_ok)?;
    let result = match get(output, "output") {
        Some(Json::String(text)) => text.clone(),
        raw => pyjson::dumps(raw.unwrap_or(&Json::Null), Dump { ensure_ascii: false, ..Dump::DEFAULT }),
    };
    let arguments = get(call, "arguments").cloned();
    let mut known = vec![result.clone(), py_str(arguments.as_ref().unwrap_or(&Json::String(String::new())))];
    for source in [Some(Json::String(result.clone())), arguments] {
        if let Some(Json::String(text)) = source {
            if let Ok(parsed) = pyjson::loads(&text) {
                known.extend(strings(&parsed));
            }
        }
    }
    let undeliverable =
        |message| KeptThreadMismatch { reason: "tail_not_deliverable", message, in_place: true };
    let mut texts = Vec::new();
    for item in rest {
        if !is(item, "type", "message") {
            return Err(undeliverable("The request adds more than messages after the result."));
        }
        let text = item_text(item);
        if is(item, "role", "user") {
            texts.push(text);
        } else if is(item, "role", "assistant") && !text.is_empty() && known.contains(&text) {
            continue;
        } else {
            return Err(undeliverable("The request adds a message the open turn cannot take."));
        }
    }
    Ok((result, texts))
}

/// `_PendingCall`: a Sylvia tool call the runtime is still waiting on, inside an open turn.
#[derive(Clone)]
pub struct PendingCall {
    pub request_id: Json,
    pub call_id: String,
    pub turn_id: String,
    /// The runtime has not yet reported the usage of the response that made this call.
    pub usage_owed: bool,
    pub exchange_id: Option<String>,
    pub model: String,
    pub listeners: Vec<LateUsageListener>,
}

impl std::fmt::Debug for PendingCall {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("PendingCall")
            .field("call_id", &self.call_id)
            .field("turn_id", &self.turn_id)
            .field("usage_owed", &self.usage_owed)
            .finish_non_exhaustive()
    }
}

impl PendingCall {
    pub fn new(request_id: Json, call_id: &str, turn_id: &str) -> Self {
        Self {
            request_id,
            call_id: call_id.to_owned(),
            turn_id: turn_id.to_owned(),
            usage_owed: true,
            exchange_id: None,
            model: String::new(),
            listeners: Vec::new(),
        }
    }

    /// `credit(usage, cache)`: hand the late report to each listener (a failing one is
    /// logged) and return its figures for the audit.
    pub fn credit(&mut self, usage: TokenUsage, cache: CacheUsage) -> JsonMap {
        self.usage_owed = false;
        let figures = usage_figures(&usage, &cache);
        let report = ProviderResponse {
            model: self.model.clone(),
            usage,
            cache_usage: cache,
            usage_reported: Some(true),
            ..ProviderResponse::default()
        };
        for listener in &self.listeners {
            if std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| listener(&report))).is_err() {
                crate::log::warning("Late usage listener failed");
            }
        }
        let mut figures = figures;
        if let Some(exchange_id) = &self.exchange_id {
            figures.insert("exchange_id".into(), Json::String(exchange_id.clone()));
        }
        figures
    }
}

/// What a kept thread holds that a request updates as it uses the thread.
#[derive(Debug, Clone, Default)]
pub struct ThreadState {
    pub items: Vec<Json>,
    pub generation: u64,
    /// The call the thread's last response made, and the text it streamed before it.
    pub call_id: String,
    pub spoken: String,
    /// The reasoning effort and speed tier the thread's current turn was started with.
    pub effort: Json,
    pub tier: Json,
    pub pending: Option<PendingCall>,
    /// The number of the request whose response made the pending call.
    pub ordinal: u64,
}

/// `_KeptThread`: an idle runtime thread a later request may continue. Its last turn either
/// ended in an interrupted Sylvia tool call, or (`pending`) is still open, waiting for that
/// call's result.
pub struct KeptThread {
    pub connection: CodexConnection,
    pub home: PathBuf,
    pub model: String,
    pub thread_id: String,
    pub instructions: String,
    pub dynamic_tools: Vec<Json>,
    pub state: Mutex<ThreadState>,
    idle_since: Mutex<Instant>,
    timer: Mutex<Option<Timer>>,
}

impl KeptThread {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        connection: CodexConnection,
        home: &Path,
        model: &str,
        thread_id: &str,
        instructions: &str,
        dynamic_tools: Vec<Json>,
        state: ThreadState,
    ) -> Arc<Self> {
        Arc::new(Self {
            connection,
            home: home.to_owned(),
            model: model.to_owned(),
            thread_id: thread_id.to_owned(),
            instructions: instructions.to_owned(),
            dynamic_tools,
            state: Mutex::new(state),
            idle_since: Mutex::new(Instant::now()),
            timer: Mutex::new(None),
        })
    }

    /// `extended_by(...)`: the request holds everything this thread holds, in order, and more.
    pub fn extended_by(
        &self,
        home: &Path,
        model: &str,
        instructions: &str,
        dynamic_tools: &[Json],
        items: &[Json],
    ) -> bool {
        let held = &self.state.lock().expect("thread state").items;
        self.home == home
            && self.model == model
            && self.instructions == instructions
            && py_eq(&Json::Array(self.dynamic_tools.clone()), &Json::Array(dynamic_tools.to_vec()))
            && items.len() > held.len()
            && py_eq(&Json::Array(items[..held.len()].to_vec()), &Json::Array(held.clone()))
    }

    fn stop_timer(&self) {
        if let Some(timer) = self.timer.lock().expect("timer lock").take() {
            timer.cancel();
        }
    }

    /// `collect_owed_usage()`: end a waiting turn whose response's usage is unreported, and
    /// return that report. Bounded; never fails.
    pub fn collect_owed_usage(&self) -> Option<JsonMap> {
        let turn_id = {
            let mut state = self.state.lock().expect("thread state");
            let pending = state.pending.as_mut().filter(|pending| pending.usage_owed)?;
            pending.usage_owed = false;
            pending.turn_id.clone()
        };
        let identity = serde_json::json!({"threadId": self.thread_id, "turnId": turn_id});
        self.connection
            .call("turn/interrupt", identity, Duration::from_secs_f64(USAGE_REPORT_SECONDS * 5.0))
            .ok()?;
        let deadline = Instant::now() + Duration::from_secs_f64(USAGE_REPORT_SECONDS);
        while Instant::now() < deadline {
            let event = self.connection.next_event(Duration::from_millis(100)).ok()?;
            let Some(event) = event else { continue };
            let params = event.get("params").cloned().unwrap_or(Json::Null);
            if event.get("method").and_then(Json::as_str) == Some("thread/tokenUsage/updated")
                && params.is_object()
                && is(&params, "threadId", &self.thread_id)
                && is(&params, "turnId", &turn_id)
            {
                if let Some((usage, cache)) = reported_usage(&params) {
                    let mut state = self.state.lock().expect("thread state");
                    return state.pending.as_mut().map(|pending| pending.credit(usage, cache));
                }
            }
        }
        None
    }

    /// `close_quietly(pool, collect=...)`.
    pub fn close_quietly(&self, pool: Option<&KeptThreads>, collect: bool) {
        self.stop_timer();
        let figures = if collect { self.collect_owed_usage() } else { None };
        if let (Some(figures), Some(pool)) = (figures, pool) {
            let ordinal = self.state.lock().expect("thread state").ordinal;
            pool.abandon(&self.home, ordinal, figures);
        }
        self.connection.close();
    }
}

#[derive(Default)]
struct Pool {
    /// Least recently used first.
    idle: Vec<Arc<KeptThread>>,
    generations: BTreeMap<PathBuf, u64>,
    ordinals: BTreeMap<PathBuf, u64>,
    abandoned: Vec<(PathBuf, u64, JsonMap)>,
}

/// `_KeptThreads`: process-wide idle runtime threads, keyed by runtime home and matched by
/// content. A thread is removed while a request uses it; callers close what these methods
/// return outside the lock.
pub struct KeptThreads {
    pub limit: usize,
    pub idle_seconds: f64,
    pub pending_idle_seconds: f64,
    pool: Mutex<Pool>,
}

fn contains(list: &[Arc<KeptThread>], kept: &Arc<KeptThread>) -> bool {
    list.iter().any(|each| Arc::ptr_eq(each, kept))
}

impl KeptThreads {
    pub fn new(limit: usize, idle_seconds: f64, pending_idle_seconds: f64) -> Arc<Self> {
        Arc::new(Self { limit, idle_seconds, pending_idle_seconds, pool: Mutex::new(Pool::default()) })
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Pool> {
        self.pool.lock().expect("kept thread pool")
    }

    pub fn len(&self) -> usize {
        self.lock().idle.len()
    }

    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    pub fn generation(&self, home: &Path) -> u64 {
        self.lock().generations.get(home).copied().unwrap_or(0)
    }

    /// `next_ordinal(home, take=...)`: number this home's requests in order; `take=false` only
    /// says which number the next request will get.
    pub fn next_ordinal(&self, home: &Path, take: bool) -> u64 {
        let mut pool = self.lock();
        let ordinal = pool.ordinals.get(home).copied().unwrap_or(0) + 1;
        if take {
            pool.ordinals.insert(home.to_owned(), ordinal);
        }
        ordinal
    }

    /// `abandon(home, ordinal, figures)`: hold the report of a thread closed unanswered.
    pub fn abandon(&self, home: &Path, ordinal: u64, figures: JsonMap) {
        let mut pool = self.lock();
        pool.abandoned.push((home.to_owned(), ordinal, figures));
        let excess = pool.abandoned.len().saturating_sub(ABANDONED_REPORT_LIMIT);
        pool.abandoned.drain(..excess);
    }

    /// `take_abandoned(home, ordinal)`: this home's held reports, each saying how many requests
    /// before `ordinal` it is.
    pub fn take_abandoned(&self, home: &Path, ordinal: u64) -> Vec<JsonMap> {
        let mine: Vec<(PathBuf, u64, JsonMap)> = {
            let mut pool = self.lock();
            let (mine, rest) =
                std::mem::take(&mut pool.abandoned).into_iter().partition(|entry| entry.0 == home);
            pool.abandoned = rest;
            mine
        };
        mine.into_iter()
            .map(|(_, made, figures)| {
                let ago = ordinal as i64 - made as i64;
                let mut report = JsonMap::new();
                report.insert(
                    "response".into(),
                    Json::String(if ago == 1 { "previous" } else { "earlier" }.into()),
                );
                report.insert("requests_ago".into(), Json::from(ago));
                report.insert("closed_unanswered".into(), Json::Bool(true));
                report.extend(figures);
                report
            })
            .collect()
    }

    /// `idle_limit(kept)`.
    pub fn idle_limit(&self, kept: &KeptThread) -> f64 {
        if kept.state.lock().expect("thread state").pending.is_none() {
            self.idle_seconds
        } else {
            self.pending_idle_seconds
        }
    }

    /// `take(...)`: remove the thread this request extends furthest, and the threads now to
    /// close (another home's, ones idle too long, and without a match this model's threads
    /// whose history starts like this request's: its superseded earlier forms).
    pub fn take(
        &self,
        home: &Path,
        model: &str,
        instructions: &str,
        dynamic_tools: &[Json],
        items: &[Json],
    ) -> (Option<Arc<KeptThread>>, Vec<Arc<KeptThread>>) {
        let now = Instant::now();
        let mut pool = self.lock();
        let mut close: Vec<Arc<KeptThread>> = pool
            .idle
            .iter()
            .filter(|kept| {
                let idle = now.duration_since(*kept.idle_since.lock().expect("idle lock")).as_secs_f64();
                kept.home != home || idle >= self.idle_limit(kept)
            })
            .cloned()
            .collect();
        let candidates: Vec<Arc<KeptThread>> =
            pool.idle.iter().filter(|kept| !contains(&close, kept)).cloned().collect();
        let mut chosen: Option<Arc<KeptThread>> = None;
        let mut longest = 0;
        for kept in &candidates {
            if kept.extended_by(home, model, instructions, dynamic_tools, items) {
                let held = kept.state.lock().expect("thread state").items.len();
                // `max(..., key=len)`: the first of the longest.
                if chosen.is_none() || held > longest {
                    chosen = Some(Arc::clone(kept));
                    longest = held;
                }
            }
        }
        if chosen.is_none() {
            let first = Json::Array(items.iter().take(1).cloned().collect());
            for kept in &candidates {
                let held = Json::Array(
                    kept.state.lock().expect("thread state").items.iter().take(1).cloned().collect(),
                );
                if kept.model == model && py_eq(&held, &first) {
                    close.push(Arc::clone(kept));
                }
            }
        }
        pool.idle = candidates
            .into_iter()
            .filter(|kept| {
                !chosen.as_ref().is_some_and(|chosen| Arc::ptr_eq(chosen, kept)) && !contains(&close, kept)
            })
            .collect();
        for kept in close.iter().chain(chosen.iter()) {
            kept.stop_timer();
        }
        (chosen, close)
    }

    /// `keep(kept)`: hold an idle thread; return the threads to close, including it when
    /// refused (its home's generation moved on).
    pub fn keep(self: &Arc<Self>, kept: Arc<KeptThread>) -> Vec<Arc<KeptThread>> {
        let mut pool = self.lock();
        let generation = kept.state.lock().expect("thread state").generation;
        if generation != pool.generations.get(&kept.home).copied().unwrap_or(0) {
            return vec![kept];
        }
        *kept.idle_since.lock().expect("idle lock") = Instant::now();
        pool.idle.push(Arc::clone(&kept));
        let count = if self.limit > 0 { pool.idle.len().saturating_sub(self.limit) } else { pool.idle.len() };
        let evicted: Vec<Arc<KeptThread>> = pool.idle.drain(..count).collect();
        for each in &evicted {
            each.stop_timer();
        }
        if !contains(&evicted, &kept) {
            let (pool_ref, held) = (Arc::downgrade(self), Arc::downgrade(&kept));
            let timer = Timer::start(self.idle_limit(&kept), move || {
                if let (Some(pool), Some(kept)) = (pool_ref.upgrade(), held.upgrade()) {
                    pool.expire(&kept);
                }
            });
            *kept.timer.lock().expect("timer lock") = Some(timer);
        }
        evicted
    }

    /// `_expire(kept)`.
    fn expire(&self, kept: &Arc<KeptThread>) {
        {
            let mut pool = self.lock();
            if !contains(&pool.idle, kept) {
                return;
            }
            pool.idle.retain(|each| !Arc::ptr_eq(each, kept));
        }
        kept.close_quietly(Some(self), true);
    }

    /// `release(home, model)`: remove idle threads of a home (and model), and move each
    /// affected home's generation on so a busy thread of it is not kept.
    pub fn release(&self, home: Option<&Path>, model: Option<&str>) -> Vec<Arc<KeptThread>> {
        let mut pool = self.lock();
        let homes: BTreeSet<PathBuf> = match home {
            Some(home) => [home.to_owned()].into(),
            None => pool
                .idle
                .iter()
                .map(|kept| kept.home.clone())
                .chain(pool.generations.keys().cloned())
                .collect(),
        };
        for each in homes {
            *pool.generations.entry(each).or_insert(0) += 1;
        }
        let (released, kept): (Vec<_>, Vec<_>) =
            std::mem::take(&mut pool.idle).into_iter().partition(|kept| {
                home.is_none_or(|home| kept.home == home) && model.is_none_or(|model| kept.model == model)
            });
        pool.idle = kept;
        for each in &released {
            each.stop_timer();
        }
        released
    }

    /// `close_all()`: every kept thread at process exit, without collecting usage.
    pub fn close_all(&self) {
        for kept in self.release(None, None) {
            kept.close_quietly(Some(self), false);
        }
    }

    /// The models kept threads are held for.
    pub fn models(&self) -> BTreeSet<String> {
        self.lock().idle.iter().map(|kept| kept.model.clone()).collect()
    }
}

/// `_KEPT_THREADS`, with `_close_kept_threads_for` registered as an account-change listener when
/// it is first used (Python registers it at import, before any request).
pub fn kept_threads() -> &'static Arc<KeptThreads> {
    static POOL: OnceLock<Arc<KeptThreads>> = OnceLock::new();
    POOL.get_or_init(|| {
        crate::codex_rpc::add_account_change_listener(Arc::new(|home: &Path| close_kept_threads_for(home)));
        KeptThreads::new(KEPT_THREAD_LIMIT, KEPT_THREAD_IDLE_SECONDS, PENDING_TURN_IDLE_SECONDS)
    })
}

/// `_close_kept_threads_for(home)`: a sign-in or sign-out changes the account a kept child
/// would answer for.
pub fn close_kept_threads_for(home: &Path) {
    let pool = kept_threads();
    for kept in pool.release(Some(&python_resolve(home)), None) {
        kept.close_quietly(Some(pool), true);
    }
}

/// `_close_all_kept_threads()`, Python's `atexit` hook: the owner calls it at exit.
pub fn close_all_kept_threads() {
    kept_threads().close_all();
}

/// `kept_thread_models()`.
pub fn kept_thread_models() -> BTreeSet<String> {
    kept_threads().models()
}

/// `release_kept_threads(models)`: close these models' kept threads in every home without
/// waiting on them; each waiting turn's usage is still collected on a background thread
/// (returned) and held for its home's next request.
pub fn release_kept_threads(models: &[String]) -> Option<std::thread::JoinHandle<()>> {
    let pool = kept_threads();
    let unique: BTreeSet<&String> = models.iter().collect();
    let released: Vec<Arc<KeptThread>> =
        unique.into_iter().flat_map(|model| pool.release(None, Some(model))).collect();
    if released.is_empty() {
        return None;
    }
    let pool = Arc::clone(pool);
    Some(
        std::thread::Builder::new()
            .name("sylvia-chatgpt-release".into())
            .spawn(move || {
                for kept in released {
                    kept.close_quietly(Some(&pool), true);
                }
            })
            .expect("the release thread starts"),
    )
}

/// `_continuation_failed(error)`: an error a continued thread may cause that a fresh thread can
/// be expected to avoid (`ProviderConnectionError`, `ModelNotFoundError`,
/// `AuthenticationError`, or exactly `ProviderError`).
pub fn continuation_failed(error: &ProviderError) -> bool {
    error.is(ErrorKind::Connection)
        || error.is(ErrorKind::ModelNotFound)
        || error.is(ErrorKind::Authentication)
        || error.kind == ErrorKind::Provider
}
