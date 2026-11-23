//! `provider/_claude_kept.py`: Claude Code processes kept between the requests of a GM session
//! (issue 171). Lane 5.
//!
//! The process that served one ledger request is kept, and a later request that extends exactly
//! what it holds continues it through stdin: after a reply that ended in text, a new prompt;
//! after a reply that ended in a Sylvia tool call, the call's answer (its result and the sealed
//! host records after it), so the reply goes on in the same native turn. Anything else starts a
//! fresh process from the saved transcript. `SYLVIA_CLAUDE_PROCESS=per-step` (or `step`) starts
//! a new process for every step instead.
//!
//! Python's `threading.Timer`s are [`Timer`] threads here, and the `atexit` hook that closes
//! every kept process is the owner's call to [`close_all`] at shutdown.

use crate::claude_runtime::ClaudeConnection;
use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicBool, Ordering};
use std::sync::{Arc, Condvar, Mutex, OnceLock};
use std::time::{Duration, Instant, SystemTime};
use sylvia_base::{Json, JsonMap};

/// `PROCESS_SWITCH`: read for every request.
pub const PROCESS_SWITCH: &str = "SYLVIA_CLAUDE_PROCESS";
pub const PERSISTENT: &str = "persistent";
pub const PER_STEP: &str = "per-step";
/// `_PER_STEP_VALUES`.
const PER_STEP_VALUES: [&str; 3] = [PER_STEP, "per_step", "step"];
/// `KEPT_SESSION`: the audit's `persist_session` value for a kept process's session file.
pub const KEPT_SESSION: &str = "kept_until_process_closes";
/// `KEPT_PROCESS_LIMIT`: idle processes kept at once; the least recently used closes first.
pub const KEPT_PROCESS_LIMIT: usize = 4;
/// `KEPT_PROCESS_IDLE_SECONDS`.
pub const KEPT_PROCESS_IDLE_SECONDS: f64 = 1800.0;
/// `_SEAL_START`: how every sealed host record starts.
const SEAL_START: &str = "[host-seal ";
/// `_KEEPALIVE_SECONDS`: kept files are touched this often, so another owner's sweep (of files
/// unchanged for 15 minutes) leaves them.
const KEEPALIVE_SECONDS: f64 = 600.0;

/// `process_setting(environ)`: `persistent` unless `SYLVIA_CLAUDE_PROCESS` asks for per-step.
pub fn process_setting(value: Option<&str>) -> &'static str {
    let value = sylvia_base::pystr::casefold(sylvia_base::pystr::strip(value.unwrap_or("")));
    if PER_STEP_VALUES.contains(&value.as_str()) {
        PER_STEP
    } else {
        PERSISTENT
    }
}

/// `ContinuationMismatchError(reason)`: a kept process cannot serve this request.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ContinuationMismatch {
    /// A code for the audit.
    pub reason: &'static str,
}

fn mismatch(reason: &'static str) -> ContinuationMismatch {
    ContinuationMismatch { reason }
}

/// A `threading.Timer`: runs once after `seconds` unless cancelled first.
pub struct Timer {
    cancelled: Arc<(Mutex<bool>, Condvar)>,
}

impl Timer {
    pub fn start(seconds: f64, action: impl FnOnce() + Send + 'static) -> Self {
        let cancelled = Arc::new((Mutex::new(false), Condvar::new()));
        let flag = Arc::clone(&cancelled);
        std::thread::Builder::new()
            .name("sylvia-claude-kept-timer".into())
            .spawn(move || {
                let (lock, wake) = &*flag;
                let deadline = Instant::now() + Duration::from_secs_f64(seconds.max(0.0));
                let mut stopped = lock.lock().expect("timer lock");
                while !*stopped {
                    let now = Instant::now();
                    if now >= deadline {
                        break;
                    }
                    stopped = wake.wait_timeout(stopped, deadline - now).expect("timer lock").0;
                }
                if !*stopped {
                    drop(stopped);
                    action();
                }
            })
            .expect("the timer thread starts");
        Self { cancelled }
    }

    /// `cancel()`.
    pub fn cancel(&self) {
        let (lock, wake) = &*self.cancelled;
        *lock.lock().expect("timer lock") = true;
        wake.notify_all();
    }
}

/// What a kept process holds and is waiting on, which the provider updates as it serves.
#[derive(Debug, Default, Clone)]
pub struct KeptState {
    /// The Anthropic-form messages of the last request it served, as Sylvia sent them.
    pub held: Vec<Json>,
    /// The text the model streamed in its last reply (before its call, if it made one).
    pub spoken: String,
    /// The `tools/call` control request the process is waiting on, if any.
    pub pending: Option<JsonMap>,
    pub call_id: String,
    pub call_arguments: Json,
    /// The per-model usage the process has reported over its life.
    pub model_usage: JsonMap,
}

/// A callback run when a kept process closes.
pub type OnClose = Box<dyn Fn(&KeptProcess) + Send + Sync>;

/// `KeptProcess`: one idle Claude Code process and what it holds.
pub struct KeptProcess {
    pub connection: ClaudeConnection,
    pub home: PathBuf,
    pub model: String,
    pub system: String,
    pub tools_key: String,
    pub session_id: String,
    pub step: PathBuf,
    pub config: PathBuf,
    pub cache: Option<PathBuf>,
    pub state: Mutex<KeptState>,
    idle_since: Mutex<Instant>,
    timer: Mutex<Option<Timer>>,
    keepalive: Mutex<Option<Timer>>,
    closed: AtomicBool,
    on_close: Mutex<Vec<OnClose>>,
}

impl KeptProcess {
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        connection: ClaudeConnection,
        home: &Path,
        model: &str,
        system: &str,
        tools_key: &str,
        session_id: &str,
        step: &Path,
        config: &Path,
        cache: Option<&Path>,
        state: KeptState,
    ) -> Arc<Self> {
        Arc::new(Self {
            connection,
            home: home.to_owned(),
            model: model.to_owned(),
            system: system.to_owned(),
            tools_key: tools_key.to_owned(),
            session_id: session_id.to_owned(),
            step: step.to_owned(),
            config: config.to_owned(),
            cache: cache.map(Path::to_owned),
            state: Mutex::new(state),
            idle_since: Mutex::new(Instant::now()),
            timer: Mutex::new(None),
            keepalive: Mutex::new(None),
            closed: AtomicBool::new(false),
            on_close: Mutex::new(Vec::new()),
        })
    }

    /// `kept.on_close.append(callback)`.
    pub fn on_close(&self, callback: OnClose) {
        self.on_close.lock().expect("callback lock").push(callback);
    }

    /// `kept.closed`.
    pub fn is_closed(&self) -> bool {
        self.closed.load(Ordering::SeqCst)
    }

    /// `close()`: once; the connection, then every callback (a failing one is ignored).
    pub fn close(&self) {
        if self.closed.swap(true, Ordering::SeqCst) {
            return;
        }
        self.stop_timers();
        self.connection.close();
        for callback in self.on_close.lock().expect("callback lock").iter() {
            let _ = std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| callback(self)));
        }
    }

    /// `touch()`: keep this process's files recent for other owners' sweeps.
    pub fn touch(&self) {
        let now = filetime_now();
        let mut paths = vec![self.step.clone()];
        let projects = self.config.join("projects");
        if let Ok(entries) = std::fs::read_dir(&projects) {
            for entry in entries.flatten() {
                let candidate = entry.path().join(format!("{}.jsonl", self.session_id));
                if candidate.exists() {
                    paths.push(candidate);
                }
            }
        }
        for path in paths {
            let _ = set_times(&path, now);
        }
    }

    /// `_stop_timers([self])`.
    fn stop_timers(&self) {
        for slot in [&self.timer, &self.keepalive] {
            if let Some(timer) = slot.lock().expect("timer lock").take() {
                timer.cancel();
            }
        }
    }

    fn held_first(&self) -> Option<Json> {
        self.state.lock().expect("state lock").held.first().cloned()
    }
}

fn filetime_now() -> SystemTime {
    SystemTime::now()
}

/// `os.utime(path, (now, now))`, for a file or a folder.
pub fn set_times(path: &Path, now: SystemTime) -> std::io::Result<()> {
    let mut options = std::fs::OpenOptions::new();
    #[cfg(windows)]
    {
        use std::os::windows::fs::OpenOptionsExt;
        /// `FILE_FLAG_BACKUP_SEMANTICS`: needed to open a folder.
        const BACKUP_SEMANTICS: u32 = 0x0200_0000;
        options.write(true).custom_flags(BACKUP_SEMANTICS);
    }
    #[cfg(not(windows))]
    options.read(true);
    options.open(path)?.set_times(std::fs::FileTimes::new().set_accessed(now).set_modified(now))
}

/// Python's `==` of two values for `held[:1] == history[:1]`.
fn same_first(a: Option<&Json>, b: Option<&Json>) -> bool {
    match (a, b) {
        (None, None) => true,
        (Some(a), Some(b)) => crate::pyobj::py_eq(a, b),
        _ => false,
    }
}

struct Pool {
    /// Least recently used first.
    idle: Vec<Arc<KeptProcess>>,
    generations: Vec<(PathBuf, u64)>,
}

impl Pool {
    fn generation(&self, home: &Path) -> u64 {
        self.generations.iter().find(|(existing, _)| existing == home).map_or(0, |(_, value)| *value)
    }
}

/// `KeptProcesses`: the process-wide idle Claude Code processes, matched by home, model, system
/// and tools.
pub struct KeptProcesses {
    limit: usize,
    idle_seconds: f64,
    pool: Mutex<Pool>,
}

fn stop_timers(kept: &[Arc<KeptProcess>]) {
    for each in kept {
        each.stop_timers();
    }
}

impl KeptProcesses {
    pub fn new(limit: usize, idle_seconds: f64) -> Arc<Self> {
        Arc::new(Self {
            limit,
            idle_seconds,
            pool: Mutex::new(Pool { idle: Vec::new(), generations: Vec::new() }),
        })
    }

    /// `len(pool)`.
    pub fn len(&self) -> usize {
        self.pool.lock().expect("pool lock").idle.len()
    }

    /// Whether no process is kept.
    pub fn is_empty(&self) -> bool {
        self.len() == 0
    }

    /// `generation(home)`.
    pub fn generation(&self, home: &Path) -> u64 {
        self.pool.lock().expect("pool lock").generation(home)
    }

    /// `take(home, model, system, tools_key, history)`: remove the process this request
    /// extends, and the processes now to close (another home, idle too long, or this model and
    /// system starting like this request without being extended by it).
    pub fn take(
        &self,
        home: &Path,
        model: &str,
        system: &str,
        tools_key: &str,
        history: &[Json],
    ) -> (Option<Arc<KeptProcess>>, Vec<Arc<KeptProcess>>) {
        let now = Instant::now();
        let mut pool = self.pool.lock().expect("pool lock");
        let mut close: Vec<Arc<KeptProcess>> = pool
            .idle
            .iter()
            .filter(|kept| {
                kept.home != home
                    || now.duration_since(*kept.idle_since.lock().expect("idle lock")).as_secs_f64()
                        >= self.idle_seconds
            })
            .cloned()
            .collect();
        let candidates: Vec<Arc<KeptProcess>> = pool
            .idle
            .iter()
            .filter(|kept| !close.iter().any(|closing| Arc::ptr_eq(closing, kept)))
            .cloned()
            .collect();
        let chosen = candidates
            .iter()
            .rev()
            .find(|kept| {
                kept.model == model
                    && kept.system == system
                    && kept.tools_key == tools_key
                    && extends(history, &kept.state.lock().expect("state lock").held)
            })
            .cloned();
        if chosen.is_none() {
            close.extend(
                candidates
                    .iter()
                    .filter(|kept| {
                        kept.model == model
                            && kept.system == system
                            && same_first(kept.held_first().as_ref(), history.first())
                    })
                    .cloned(),
            );
        }
        pool.idle = candidates
            .into_iter()
            .filter(|kept| {
                !chosen.as_ref().is_some_and(|chosen| Arc::ptr_eq(chosen, kept))
                    && !close.iter().any(|closing| Arc::ptr_eq(closing, kept))
            })
            .collect();
        stop_timers(&close);
        if let Some(chosen) = &chosen {
            chosen.stop_timers();
        }
        (chosen, close)
    }

    /// `keep(kept, generation)`: hold an idle process; the processes to close, including it
    /// when refused (its home was released since `generation`).
    pub fn keep(self: &Arc<Self>, kept: Arc<KeptProcess>, generation: u64) -> Vec<Arc<KeptProcess>> {
        let mut pool = self.pool.lock().expect("pool lock");
        if generation != pool.generation(&kept.home) {
            return vec![kept];
        }
        *kept.idle_since.lock().expect("idle lock") = Instant::now();
        pool.idle.push(Arc::clone(&kept));
        let excess = pool.idle.len().saturating_sub(self.limit);
        let evicted: Vec<Arc<KeptProcess>> =
            if self.limit > 0 { pool.idle[..excess].to_vec() } else { pool.idle.clone() };
        pool.idle.drain(..evicted.len());
        stop_timers(&evicted);
        if !evicted.iter().any(|each| Arc::ptr_eq(each, &kept)) {
            let pool_handle = Arc::clone(self);
            let expiring = Arc::clone(&kept);
            *kept.timer.lock().expect("timer lock") =
                Some(Timer::start(self.idle_seconds, move || pool_handle.expire(&expiring)));
            self.schedule_keepalive(&kept);
        }
        evicted
    }

    /// `_schedule_keepalive(kept)`.
    fn schedule_keepalive(self: &Arc<Self>, kept: &Arc<KeptProcess>) {
        let pool_handle = Arc::clone(self);
        let ticking = Arc::clone(kept);
        let timer = Timer::start(KEEPALIVE_SECONDS, move || {
            {
                let pool = pool_handle.pool.lock().expect("pool lock");
                if !pool.idle.iter().any(|each| Arc::ptr_eq(each, &ticking)) {
                    return;
                }
                pool_handle.schedule_keepalive(&ticking);
            }
            ticking.touch();
        });
        *kept.keepalive.lock().expect("keepalive lock") = Some(timer);
    }

    /// `_expire(kept)`.
    fn expire(&self, kept: &Arc<KeptProcess>) {
        {
            let mut pool = self.pool.lock().expect("pool lock");
            let Some(position) = pool.idle.iter().position(|each| Arc::ptr_eq(each, kept)) else { return };
            pool.idle.remove(position);
        }
        kept.close();
    }

    /// `release(home, model)`: remove idle processes of a home (and model); a busy one there is
    /// not kept afterwards (its home's generation moves on). With no home, every home.
    pub fn release(&self, home: Option<&Path>, model: Option<&str>) -> Vec<Arc<KeptProcess>> {
        let mut pool = self.pool.lock().expect("pool lock");
        let homes: Vec<PathBuf> = match home {
            Some(home) => vec![home.to_owned()],
            None => {
                let mut homes: Vec<PathBuf> = pool.idle.iter().map(|kept| kept.home.clone()).collect();
                homes.extend(pool.generations.iter().map(|(home, _)| home.clone()));
                homes.sort();
                homes.dedup();
                homes
            }
        };
        for each in homes {
            match pool.generations.iter_mut().find(|(existing, _)| *existing == each) {
                Some(slot) => slot.1 += 1,
                None => pool.generations.push((each, 1)),
            }
        }
        let released: Vec<Arc<KeptProcess>> = pool
            .idle
            .iter()
            .filter(|kept| {
                home.is_none_or(|home| kept.home == home) && model.is_none_or(|model| kept.model == model)
            })
            .cloned()
            .collect();
        pool.idle.retain(|kept| !released.iter().any(|each| Arc::ptr_eq(each, kept)));
        stop_timers(&released);
        released
    }

    /// `models()`.
    pub fn models(&self) -> std::collections::BTreeSet<String> {
        self.pool.lock().expect("pool lock").idle.iter().map(|kept| kept.model.clone()).collect()
    }

    /// `close_all()`.
    pub fn close_all(&self) {
        for kept in self.release(None, None) {
            kept.close();
        }
    }
}

/// `KEPT_PROCESSES`: the owner's one pool.
pub fn kept_processes() -> &'static Arc<KeptProcesses> {
    static POOL: OnceLock<Arc<KeptProcesses>> = OnceLock::new();
    POOL.get_or_init(|| KeptProcesses::new(KEPT_PROCESS_LIMIT, KEPT_PROCESS_IDLE_SECONDS))
}

/// `release_kept_processes(home, models)`: close kept processes of a home, or of these models;
/// how many closed.
pub fn release_kept_processes(home: Option<&Path>, models: Option<&[String]>) -> usize {
    let pool = kept_processes();
    let released: Vec<Arc<KeptProcess>> = match models {
        None => pool.release(home, None),
        Some(models) => {
            let unique: std::collections::BTreeSet<&String> = models.iter().collect();
            unique.into_iter().flat_map(|model| pool.release(home, Some(model))).collect()
        }
    };
    for kept in &released {
        kept.close();
    }
    released.len()
}

/// The `atexit` hook: close every kept process. The owner calls it at shutdown.
pub fn close_all() {
    kept_processes().close_all();
}

// ── Continuation ──────────────────────────────────────────────────────────────────────────

/// Why `continuation` gave no delivery: Python's `ContinuationMismatchError`, or an accidental
/// exception (`AttributeError`, `TypeError`, `ValueError`) Python lets escape.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum ContinuationError {
    Mismatch(ContinuationMismatch),
    Raw(String),
}

impl From<ContinuationMismatch> for ContinuationError {
    fn from(mismatch: ContinuationMismatch) -> Self {
        Self::Mismatch(mismatch)
    }
}

impl From<crate::pyobj::Incidental> for ContinuationError {
    fn from(error: crate::pyobj::Incidental) -> Self {
        Self::Raw(error.0)
    }
}

type Continued<T> = Result<T, ContinuationError>;

/// `_plain(value)`: without cache markers, which move between requests and are not content.
fn plain(value: &Json) -> Json {
    match value {
        Json::Object(map) => Json::Object(
            map.iter()
                .filter(|(key, _)| *key != "cache_control")
                .map(|(key, value)| (key.clone(), plain(value)))
                .collect(),
        ),
        Json::Array(items) => Json::Array(items.iter().map(plain).collect()),
        other => other.clone(),
    }
}

/// `_same(a, b)`: equal as `json.dumps(_plain(...), sort_keys=True, ensure_ascii=False)`.
fn same(a: &Json, b: &Json) -> bool {
    let dump = |value: &Json| {
        sylvia_base::pyjson::dumps(
            &plain(value),
            sylvia_base::pyjson::Dump::DEFAULT.sorted(true).ascii(false),
        )
    };
    dump(a) == dump(b)
}

/// `_extends(history, held)`: longer, and starting with exactly what is held.
pub fn extends(history: &[Json], held: &[Json]) -> bool {
    history.len() > held.len() && history.iter().zip(held).all(|(a, b)| same(a, b))
}

/// `value.get(key)` on a dict, else `AttributeError`.
fn get<'a>(value: &'a Json, key: &str) -> Continued<Option<&'a Json>> {
    match value {
        Json::Object(map) => Ok(map.get(key)),
        other => Err(crate::pyobj::Incidental::no_attribute(other, "get").into()),
    }
}

/// `_blocks(content)`: text as one text block, a list of dicts as it is.
fn blocks(content: Option<&Json>) -> Continued<Vec<Json>> {
    match content {
        Some(Json::String(text)) => Ok(vec![text_block(Json::String(text.clone()))]),
        Some(Json::Array(items)) if items.iter().all(Json::is_object) => Ok(items.clone()),
        _ => Err(mismatch("content_not_deliverable").into()),
    }
}

fn is_type(block: &Json, kind: &str) -> bool {
    block.get("type").and_then(Json::as_str) == Some(kind)
}

/// `"".join(b.get("text", "") for b in blocks if b.get("type") == "text")`.
fn text_of(blocks: &[Json]) -> Continued<String> {
    let mut out = String::new();
    for (index, block) in blocks.iter().filter(|block| is_type(block, "text")).enumerate() {
        match block.get("text") {
            None => {}
            Some(Json::String(text)) => out.push_str(text),
            Some(other) => {
                return Err(ContinuationError::Raw(format!(
                    "sequence item {index}: expected str instance, {} found",
                    crate::pyobj::type_name(other)
                )))
            }
        }
    }
    Ok(out)
}

/// `_strings(value)`: every string inside it.
fn strings(value: &Json) -> Vec<String> {
    match value {
        Json::String(text) => vec![text.clone()],
        Json::Object(map) => map.values().flat_map(strings).collect(),
        Json::Array(items) => items.iter().flat_map(strings).collect(),
        _ => Vec::new(),
    }
}

fn text_block(text: Json) -> Json {
    let mut block = JsonMap::new();
    block.insert("type".into(), Json::String("text".into()));
    block.insert("text".into(), text);
    Json::Object(block)
}

/// `text.startswith(prefix)` on a value Python calls it on: a string, else `AttributeError`.
fn starts_with(text: &Json, prefix: &str) -> Continued<bool> {
    match text {
        Json::String(text) => Ok(text.starts_with(prefix)),
        other => Err(crate::pyobj::Incidental::no_attribute(other, "startswith").into()),
    }
}

/// `_text_blocks_after(message, skip)`: a user message's text blocks, without the ledger's
/// sealed continue record.
fn text_blocks_after(content: Option<&Json>, skip: &str) -> Continued<Vec<Json>> {
    let mut out = Vec::new();
    for block in blocks(content)? {
        if !is_type(&block, "text") {
            return Err(mismatch("content_not_deliverable").into());
        }
        let text = block.get("text").cloned().unwrap_or_else(|| Json::String(String::new()));
        if !skip.is_empty() && starts_with(&text, SEAL_START)? {
            if let Json::String(value) = &text {
                if value.ends_with(skip) {
                    continue;
                }
            }
        }
        out.push(text_block(text));
    }
    Ok(out)
}

/// What to write to a kept process for a request.
#[derive(Debug, Clone, PartialEq)]
pub enum Delivery {
    /// After a reply that ended in text: the stdin prompt's content blocks.
    Prompt(Vec<Json>),
    /// For a waiting call: the MCP tool result.
    Answer(JsonMap),
}

/// `continuation(kept, history, continue_text)`: a prompt, or the waiting call's answer, or why
/// the request does not continue exactly what the process holds.
pub fn continuation(state: &KeptState, history: &[Json], continue_text: &str) -> Continued<Delivery> {
    let tail = history.get(state.held.len()..).unwrap_or_default();
    let role = |message: &Json| -> Continued<Option<String>> {
        Ok(get(message, "role")?.and_then(Json::as_str).map(str::to_owned))
    };
    let Some(first) = tail.first() else { return Err(mismatch("no_reply_copy").into()) };
    if role(first)?.as_deref() != Some("assistant") {
        return Err(mismatch("no_reply_copy").into());
    }
    let reply = blocks(get(first, "content")?)?;
    if text_of(&reply)? != state.spoken {
        return Err(mismatch("model_text_differs").into());
    }
    let calls: Vec<&Json> = reply.iter().filter(|block| is_type(block, "tool_use")).collect();
    if state.pending.is_none() {
        // The user messages after the reply are this request's prompt, together.
        let mut users = true;
        for message in tail.iter().skip(1) {
            users &= role(message)?.as_deref() == Some("user");
        }
        if !calls.is_empty() || tail.len() < 2 || !users {
            return Err(mismatch("tail_not_deliverable").into());
        }
        let mut content = Vec::new();
        for message in &tail[1..] {
            content.extend(text_blocks_after(get(message, "content")?, continue_text)?);
        }
        if content.is_empty() {
            return Err(mismatch("no_prompt").into());
        }
        return Ok(Delivery::Prompt(content));
    }
    let call_id = Json::String(state.call_id.clone());
    if calls.len() != 1 || !calls[0].get("id").is_some_and(|id| crate::pyobj::py_eq(id, &call_id)) {
        return Err(mismatch("no_call_result").into());
    }
    if tail.len() < 2 || role(&tail[1])?.as_deref() != Some("user") {
        return Err(mismatch("no_call_result").into());
    }
    let answer_blocks = blocks(get(&tail[1], "content")?)?;
    let Some((first_block, rest)) = answer_blocks.split_first() else {
        return Err(ContinuationError::Raw(
            "not enough values to unpack (expected at least 1, got 0)".into(),
        ));
    };
    if !is_type(first_block, "tool_result")
        || !first_block.get("tool_use_id").is_some_and(|id| crate::pyobj::py_eq(id, &call_id))
    {
        return Err(mismatch("no_call_result").into());
    }
    let empty = Json::String(String::new());
    let result = blocks(Some(first_block.get("content").unwrap_or(&empty)))?;
    if result.iter().any(|block| !is_type(block, "text")) {
        return Err(mismatch("content_not_deliverable").into());
    }
    let mut known = vec![text_of(&result)?];
    known.extend(strings(&state.call_arguments));
    let mut extras = if rest.is_empty() {
        Vec::new()
    } else {
        text_blocks_after(Some(&Json::Array(rest.to_vec())), continue_text)?
    };
    for message in &tail[2..] {
        if role(message)?.as_deref() == Some("assistant") {
            // Sylvia's copy of narration the model wrote into the call is not sent again.
            let content = get(message, "content")?;
            let text = text_of(&blocks(content)?)?;
            if !text.is_empty()
                && known.contains(&text)
                && blocks(content)?.iter().all(|block| is_type(block, "text"))
            {
                continue;
            }
            return Err(mismatch("tail_not_deliverable").into());
        }
        extras.extend(text_blocks_after(get(message, "content")?, continue_text)?);
    }
    for block in &extras {
        let text = block.get("text").cloned().unwrap_or_else(|| empty.clone());
        if !starts_with(&text, SEAL_START)? {
            // The player's words after the call: they stay a real user message, so a fresh
            // process serves this request.
            return Err(mismatch("player_speech_after_call").into());
        }
    }
    let mut content: Vec<Json> = result
        .iter()
        .map(|block| text_block(block.get("text").cloned().unwrap_or_else(|| empty.clone())))
        .collect();
    content.extend(extras);
    let mut answer = JsonMap::new();
    answer.insert("content".into(), Json::Array(content));
    if first_block.get("is_error").is_some_and(crate::pyobj::truthy) {
        answer.insert("isError".into(), Json::Bool(true));
    }
    Ok(Delivery::Answer(answer))
}
