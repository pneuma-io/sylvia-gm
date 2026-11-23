//! `provider/_claude_native.py`: Claude Code's native transcript under the context ledger:
//! projection inputs, collection and cleanup. Lane 5.
//!
//! Under the ledger each step runs with a fresh `--session-id` and session persistence on;
//! Sylvia copies the context records the CLI wrote into its session file and replays them in
//! every later transcript, so consecutive requests extend each other. The session file holds a
//! full copy of the request, GM-private state included: it is deleted after every step, and
//! leftovers are swept before each step.
//!
//! `CURRENT_NATIVE_ROUTE`, Python's `ContextVar` the GM adapter sets for the request it is about
//! to send, is a tokio task-local here ([`with_native_route`], [`current_native_route`]).

use crate::provider::BoxFuture;
use std::collections::{BTreeMap, BTreeSet};
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex, OnceLock};
use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};
use sylvia_base::{Json, JsonMap};

/// `FILLER_TEXT`: what Claude Code inserts after a trailing user record; Sylvia writes it
/// instead.
pub const FILLER_TEXT: &str = "No response requested.";
/// `CLI_INSERTED_MODEL`: the model name of the assistant turn the CLI inserts on resume.
pub const CLI_INSERTED_MODEL: &str = "<synthetic>";
/// `TRANSIENT_SESSION`: the audit's description of the session file under the ledger.
pub const TRANSIENT_SESSION: &str = "transient_deleted_after_step";
/// `STALE_AFTER_SECONDS`: another process's session file or step folder is swept only once
/// unchanged this long.
pub const STALE_AFTER_SECONDS: f64 = 15.0 * 60.0;

/// `NativeCollection`: what one step's session file said.
#[derive(Debug, Clone, PartialEq)]
pub struct NativeCollection {
    /// `collected` or `unknown`.
    pub status: &'static str,
    pub records: Vec<Json>,
    pub reason: Option<&'static str>,
    /// For a call a kept process answered in place: the CLI's own stored tool-result message.
    pub answer: Option<Json>,
}

impl NativeCollection {
    /// `NativeCollection("unknown", reason=reason)`.
    pub fn unknown(reason: &'static str) -> Self {
        Self { status: "unknown", records: Vec::new(), reason: Some(reason), answer: None }
    }

    fn collected(records: Vec<Json>, answer: Option<Json>) -> Self {
        Self { status: "collected", records, reason: None, answer }
    }
}

/// Called once after a completed step with what collection found.
pub type OnCollected = Arc<dyn Fn(NativeCollection) -> BoxFuture<'static, ()> + Send + Sync>;

/// `NativeRoute`: the ledger's per-request boundaries and saved native records for one
/// `claude_code` wire: `stable_count` leading system messages, then `entry_count` journal
/// entries.
#[derive(Clone, Default)]
pub struct NativeRoute {
    pub stable_count: usize,
    pub entry_count: usize,
    /// Journal lengths at which earlier requests of this journal generation ended.
    pub boundaries: Vec<usize>,
    /// A boundary's context records, as the CLI wrote them after that request's prompt.
    pub native_records: BTreeMap<usize, Vec<Json>>,
    /// A boundary whose request a kept process served by answering its waiting call: the
    /// tool-result message that process stored.
    pub native_answers: BTreeMap<usize, Json>,
    pub on_collected: Option<OnCollected>,
}

impl NativeRoute {
    /// `first_step`.
    pub fn first_step(&self) -> bool {
        self.boundaries.is_empty()
    }
}

tokio::task_local! {
    static CURRENT_NATIVE_ROUTE: std::sync::Mutex<Option<NativeRoute>>;
}

/// Run `future` with `route` as `CURRENT_NATIVE_ROUTE`.
pub async fn with_native_route<F: std::future::Future>(route: Option<NativeRoute>, future: F) -> F::Output {
    CURRENT_NATIVE_ROUTE.scope(std::sync::Mutex::new(route), future).await
}

/// `CURRENT_NATIVE_ROUTE.get()`: `None` outside a scope, as the `ContextVar`'s default.
pub fn current_native_route() -> Option<NativeRoute> {
    CURRENT_NATIVE_ROUTE.try_with(|route| route.lock().expect("native route").clone()).ok().flatten()
}

/// `CURRENT_NATIVE_ROUTE.get()` then `.set(None)`: the provider takes the route, which belongs
/// to exactly the request it was planned for.
pub fn take_native_route() -> Option<NativeRoute> {
    CURRENT_NATIVE_ROUTE.try_with(|route| route.lock().expect("native route").take()).ok().flatten()
}

/// Every entry of `folder` whose name is `name` (`Path.glob(f"*/{name}")` one level down).
fn in_subfolders(folder: &Path, name: &str) -> Vec<PathBuf> {
    let Ok(entries) = std::fs::read_dir(folder) else { return Vec::new() };
    entries
        .flatten()
        .filter(|entry| entry.path().is_dir())
        .map(|entry| entry.path().join(name))
        .filter(|path| std::fs::symlink_metadata(path).is_ok())
        .collect()
}

/// `session_files(config, session_id)`: the session file (and any per-session folder) the CLI
/// wrote.
pub fn session_files(config: &Path, session_id: &str) -> Vec<PathBuf> {
    let projects = config.join("projects");
    if !projects.is_dir() {
        return Vec::new();
    }
    let mut files = in_subfolders(&projects, &format!("{session_id}.jsonl"));
    files.extend(in_subfolders(&projects, session_id));
    files
}

/// `_remove(path)`: Windows can hold a file briefly after the child exits, so retry.
fn remove(path: &Path) -> bool {
    for attempt in 0..5u32 {
        let metadata = std::fs::symlink_metadata(path);
        let outcome = match &metadata {
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return true,
            Ok(metadata) if metadata.is_dir() => std::fs::remove_dir_all(path),
            _ => std::fs::remove_file(path),
        };
        match outcome {
            Ok(()) => return true,
            Err(error) if error.kind() == std::io::ErrorKind::NotFound => return true,
            Err(_) if attempt == 4 => return false,
            Err(_) => std::thread::sleep(Duration::from_secs_f64(0.05 * f64::from(attempt + 1))),
        }
    }
    false
}

/// `inside(path, home)`: is `path` (resolved) inside `home` (resolved)?
pub fn inside(path: &Path, home: &Path) -> bool {
    let (path, home) = (crate::codex_rpc::python_resolve(path), crate::codex_rpc::python_resolve(home));
    if cfg!(windows) {
        // `PureWindowsPath.relative_to` compares case-insensitively.
        let lower = |path: &Path| PathBuf::from(path.to_string_lossy().to_lowercase());
        return lower(&path).starts_with(lower(&home));
    }
    path.starts_with(home)
}

fn seconds(time: SystemTime) -> f64 {
    time.duration_since(UNIX_EPOCH).map_or(0.0, |duration| duration.as_secs_f64())
}

/// `_newest_change(path)`: the latest modification time of `path` and anything inside it;
/// infinite when it cannot be read.
fn newest_change(path: &Path) -> f64 {
    let Ok(metadata) = std::fs::metadata(path) else { return f64::INFINITY };
    let Ok(modified) = metadata.modified() else { return f64::INFINITY };
    let mut newest = seconds(modified);
    let is_link = std::fs::symlink_metadata(path).is_ok_and(|metadata| metadata.file_type().is_symlink());
    if metadata.is_dir() && !is_link {
        let mut pending = vec![path.to_owned()];
        while let Some(folder) = pending.pop() {
            let Ok(entries) = std::fs::read_dir(&folder) else { continue };
            for entry in entries.flatten() {
                let child = entry.path();
                if let Ok(child_metadata) = std::fs::metadata(&child) {
                    if let Ok(modified) = child_metadata.modified() {
                        newest = newest.max(seconds(modified));
                    }
                    if child_metadata.is_dir() {
                        pending.push(child);
                    }
                }
            }
        }
    }
    newest
}

/// `_FAILED_DELETIONS`: per runtime home, what this process failed to delete.
fn failed() -> &'static Mutex<BTreeMap<PathBuf, BTreeSet<PathBuf>>> {
    static FAILED: OnceLock<Mutex<BTreeMap<PathBuf, BTreeSet<PathBuf>>>> = OnceLock::new();
    FAILED.get_or_init(|| Mutex::new(BTreeMap::new()))
}

fn remember_failed(home: &Path, path: &Path) {
    failed()
        .lock()
        .expect("failed lock")
        .entry(crate::codex_rpc::python_resolve(home))
        .or_default()
        .insert(path.to_owned());
}

/// `retry_failed_deletions(home)`: delete again what this process could not delete before.
pub fn retry_failed_deletions(home: &Path) -> usize {
    let key = crate::codex_rpc::python_resolve(home);
    let pending: Vec<PathBuf> = failed()
        .lock()
        .expect("failed lock")
        .get(&key)
        .map(|paths| paths.iter().cloned().collect())
        .unwrap_or_default();
    let mut removed = 0;
    for path in pending {
        if remove(&path) {
            removed += 1;
            if let Some(paths) = failed().lock().expect("failed lock").get_mut(&key) {
                paths.remove(&path);
            }
        }
    }
    removed
}

/// `remove_step_folder(step, home)`: a failure is remembered and retried next step.
pub fn remove_step_folder(step: &Path, home: &Path) -> bool {
    if remove(step) {
        return true;
    }
    remember_failed(home, step);
    crate::log::warning(
        "A Claude runtime step folder could not be deleted yet; this process retries it at the start of its next step.",
    );
    false
}

/// `delete_session(config, session_id, home)`: everything the CLI persisted for one step;
/// never a config folder outside Sylvia's runtime home.
pub fn delete_session(config: &Path, session_id: &str, home: &Path) -> bool {
    if !inside(config, home) {
        crate::log::warning("A Claude config directory outside Sylvia's runtime home was not touched.");
        return false;
    }
    let mut removed = true;
    for path in session_files(config, session_id) {
        if !remove(&path) {
            removed = false;
            remember_failed(home, &path);
        }
    }
    if !removed {
        crate::log::warning(
            "A Claude runtime session file could not be deleted yet; this process retries it at the start of its next step.",
        );
    }
    removed
}

/// `remove_debug_logs(cache, home)`: Claude Code's MCP debug logs under Sylvia's runtime home
/// (`<cache>/claude-cli-nodejs/*/mcp-logs-*/`).
pub fn remove_debug_logs(cache: Option<&Path>, home: &Path) -> usize {
    let Some(cache) = cache.filter(|cache| inside(cache, home)) else { return 0 };
    let root = cache.join("claude-cli-nodejs");
    let mut count = 0;
    let Ok(folders) = std::fs::read_dir(&root) else { return 0 };
    for folder in folders.flatten().filter(|entry| entry.path().is_dir()) {
        let Ok(entries) = std::fs::read_dir(folder.path()) else { continue };
        for entry in entries.flatten() {
            let path = entry.path();
            let name = entry.file_name().to_string_lossy().into_owned();
            let is_link =
                std::fs::symlink_metadata(&path).is_ok_and(|metadata| metadata.file_type().is_symlink());
            if name.starts_with("mcp-logs-") && path.is_dir() && !is_link && inside(&path, home) {
                count += usize::from(remove(&path));
            }
        }
    }
    count
}

/// `_UUID.fullmatch(name)`, case-insensitively.
fn is_uuid(name: &str) -> bool {
    let groups: Vec<&str> = name.split('-').collect();
    groups.len() == 5
        && groups.iter().zip([8, 4, 4, 4, 12]).all(|(group, length)| {
            group.len() == length && group.bytes().all(|byte| byte.is_ascii_hexdigit())
        })
}

/// `sweep(config, steps, active, home, now=...)`: leftover session files and step folders,
/// only inside the runtime home, only ones this process is not running, and only those
/// unchanged for [`STALE_AFTER_SECONDS`]. `now` is seconds since the epoch.
pub fn sweep(config: &Path, steps: &Path, active: &BTreeSet<String>, home: &Path, now: Option<f64>) -> usize {
    if !(inside(config, home) && inside(steps, home)) {
        crate::log::warning("A Claude config or step directory outside Sylvia's runtime home was not swept.");
        return 0;
    }
    let mut count = retry_failed_deletions(home);
    let cutoff = now.unwrap_or_else(|| seconds(SystemTime::now())) - STALE_AFTER_SECONDS;
    let projects = config.join("projects");
    if projects.is_dir() {
        for folder in
            std::fs::read_dir(&projects).into_iter().flatten().flatten().filter(|entry| entry.path().is_dir())
        {
            for entry in std::fs::read_dir(folder.path()).into_iter().flatten().flatten() {
                let file = entry.file_name().to_string_lossy().into_owned();
                let name = file.strip_suffix(".jsonl").unwrap_or(&file);
                let path = entry.path();
                if is_uuid(name) && !active.contains(name) && newest_change(&path) < cutoff && remove(&path) {
                    count += 1;
                }
            }
        }
    }
    if steps.is_dir() {
        for entry in std::fs::read_dir(steps).into_iter().flatten().flatten() {
            let name = entry.file_name().to_string_lossy().into_owned();
            let path = entry.path();
            if !active.contains(&name) && newest_change(&path) < cutoff && remove(&path) {
                count += 1;
            }
        }
    }
    count
}

fn get<'a>(value: &'a Json, key: &str) -> Option<&'a Json> {
    value.as_object().and_then(|map| map.get(key))
}

fn is_row(row: &Json, kind: &str) -> bool {
    get(row, "type").and_then(Json::as_str) == Some(kind)
}

/// `_rows(path)`: the file's JSON objects, line by line; a line still being written is
/// skipped.
fn rows(path: &Path) -> Vec<Json> {
    let Ok(bytes) = std::fs::read(path) else { return Vec::new() };
    let Ok(text) = String::from_utf8(bytes) else { return Vec::new() };
    sylvia_base::pystr::splitlines(&text)
        .into_iter()
        .filter_map(|line| sylvia_base::pyjson::loads(line).ok())
        .filter(Json::is_object)
        .collect()
}

/// `_requests_after(rows, start)`: model requests the CLI recorded after row `start`.
fn requests_after(rows: &[Json], start: usize) -> usize {
    rows.iter().skip(start + 1).filter(|row| is_row(row, "api-request")).count()
}

/// `_ROUND_MESSAGE_KEYS`.
const ROUND_MESSAGE_KEYS: [&str; 7] =
    ["id", "type", "role", "model", "content", "stop_reason", "stop_sequence"];
/// `_DROPPED_BLOCKS`: private reasoning, and text the journal holds already.
const DROPPED_BLOCKS: [&str; 3] = ["thinking", "redacted_thinking", "text"];

/// `_round_row(row)`: a ToolSearch round message, for replay; `None` when nothing is kept.
fn round_row(row: &Json) -> Option<Json> {
    let message = get(row, "message").filter(|message| message.is_object())?;
    let kind = get(row, "type").and_then(Json::as_str)?;
    let mut out = JsonMap::new();
    if kind == "assistant" {
        let Some(Json::Array(content)) = get(message, "content") else { return None };
        let kept: Vec<Json> = content
            .iter()
            .filter(|block| {
                !get(block, "type").and_then(Json::as_str).is_some_and(|kind| DROPPED_BLOCKS.contains(&kind))
            })
            .cloned()
            .collect();
        if kept.is_empty() {
            return None;
        }
        let mut kept_message = JsonMap::new();
        for key in ROUND_MESSAGE_KEYS {
            if let Some(value) = get(message, key) {
                kept_message.insert(key.into(), value.clone());
            }
        }
        kept_message.insert("content".into(), Json::Array(kept));
        out.insert("type".into(), Json::String("assistant".into()));
        out.insert("message".into(), Json::Object(kept_message));
        return Some(Json::Object(out));
    }
    if kind != "user" {
        return None;
    }
    let mut kept_message = JsonMap::new();
    for key in ["role", "content"] {
        if let Some(value) = get(message, key) {
            kept_message.insert(key.into(), value.clone());
        }
    }
    out.insert("type".into(), Json::String("user".into()));
    out.insert("message".into(), Json::Object(kept_message));
    Some(Json::Object(out))
}

/// `_is_final(row, final_message_id)`.
fn is_final(row: &Json, final_message_id: Option<&str>) -> bool {
    is_row(row, "assistant")
        && final_message_id.is_some_and(|id| {
            get(row, "message").and_then(|message| get(message, "id")).and_then(Json::as_str) == Some(id)
        })
}

/// A user row whose message content is `prompt_content`.
fn is_prompt(row: &Json, prompt_content: &Json) -> bool {
    is_row(row, "user")
        && get(row, "message").filter(|message| message.is_object()).is_some_and(|message| {
            get(message, "content").is_some_and(|content| crate::pyobj::py_eq(content, prompt_content))
        })
}

fn jsonl_file(config: &Path, session_id: &str) -> Option<PathBuf> {
    session_files(config, session_id)
        .into_iter()
        .find(|path| path.extension().is_some_and(|ext| ext == "jsonl"))
}

/// `wait_for_flush(config, session_id, prompt_content, limit, requests=...)`: before the step's
/// process closes, let the CLI finish writing this step's records. `true` when they are
/// complete.
pub fn wait_for_flush(
    config: &Path,
    session_id: &str,
    prompt_content: &Json,
    limit: f64,
    requests: Option<usize>,
) -> bool {
    let deadline = Instant::now() + Duration::from_secs_f64(limit.max(0.0));
    let (mut last_size, mut quiet_since) = (-1i64, Instant::now());
    loop {
        if let Some(file) = jsonl_file(config, session_id) {
            let rows = rows(&file);
            if let Some(prompt) = rows.iter().rposition(|row| is_prompt(row, prompt_content)) {
                match requests {
                    Some(requests) => {
                        if requests_after(&rows, prompt) >= requests {
                            return true;
                        }
                    }
                    None => {
                        let bookkeeping = rows[prompt + 1..].iter().any(|row| {
                            get(row, "type")
                                .map_or(String::new(), crate::pyobj::py_str)
                                .starts_with("api-request")
                        });
                        if bookkeeping {
                            return true;
                        }
                    }
                }
                let size = std::fs::metadata(&file).map_or(-1, |metadata| metadata.len() as i64);
                if size != last_size {
                    (last_size, quiet_since) = (size, Instant::now());
                } else if quiet_since.elapsed() >= Duration::from_millis(250) {
                    return true;
                }
            }
        }
        if Instant::now() >= deadline {
            return false;
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}

/// An attachment row's copy: `attachment`, `rendered` and, if present, `renderedRole`.
fn attachment_copy(row: &Json) -> Json {
    let mut copied = JsonMap::new();
    copied.insert("attachment".into(), get(row, "attachment").cloned().unwrap_or(Json::Null));
    copied.insert("rendered".into(), get(row, "rendered").cloned().unwrap_or(Json::Null));
    if let Some(role) = get(row, "renderedRole") {
        copied.insert("renderedRole".into(), role.clone());
    }
    Json::Object(copied)
}

/// `collect(session_file, prompt_content, final_message_id=...)`: the CLI's rendered context
/// records after this step's prompt record (and, with `final_message_id`, the ToolSearch
/// rounds' messages up to the final one). Never fails: `unknown` with a reason instead.
pub fn collect(
    session_file: Option<&Path>,
    prompt_content: &Json,
    final_message_id: Option<&str>,
) -> NativeCollection {
    let Some(file) = session_file.filter(|file| file.is_file()) else {
        return NativeCollection::unknown("session_file_missing");
    };
    let Some(text) = std::fs::read(file).ok().and_then(|bytes| String::from_utf8(bytes).ok()) else {
        return NativeCollection::unknown("session_file_unreadable");
    };
    let mut rows = Vec::new();
    for line in sylvia_base::pystr::splitlines(&text) {
        if sylvia_base::pystr::strip(line).is_empty() {
            continue;
        }
        match sylvia_base::pyjson::loads(line) {
            Ok(row) => rows.push(row),
            Err(_) => return NativeCollection::unknown("session_file_unreadable"),
        }
    }
    if !rows.iter().all(Json::is_object) {
        return NativeCollection::unknown("session_file_unreadable");
    }
    let Some(prompt_index) = rows.iter().rposition(|row| is_prompt(row, prompt_content)) else {
        return NativeCollection::unknown("prompt_record_missing");
    };
    let inserted = rows[..prompt_index].iter().any(|row| {
        is_row(row, "assistant")
            && get(row, "message")
                .filter(|message| message.is_object())
                .and_then(|message| get(message, "model"))
                .is_some_and(|model| model.as_str() == Some(CLI_INSERTED_MODEL))
    });
    if inserted {
        return NativeCollection::unknown("cli_inserted_turn");
    }
    let mut records = Vec::new();
    for row in &rows[prompt_index + 1..] {
        if final_message_id.is_some() && (is_row(row, "assistant") || is_row(row, "user")) {
            if is_final(row, final_message_id) {
                break;
            }
            records.extend(round_row(row));
            continue;
        }
        if !is_row(row, "attachment") || !get(row, "rendered").is_some_and(crate::pyobj::truthy) {
            continue;
        }
        if !get(row, "attachment").is_some_and(Json::is_object) {
            return NativeCollection::unknown("session_file_unreadable");
        }
        records.push(attachment_copy(row));
    }
    NativeCollection::collected(records, None)
}

/// `_answer_row(rows, call_id)`: the last user row answering `call_id`.
fn answer_row(rows: &[Json], call_id: &str) -> Option<usize> {
    rows.iter().rposition(|row| {
        let content = get(row, "message").and_then(|message| get(message, "content"));
        let Some(Json::Array(content)) = content else { return false };
        is_row(row, "user")
            && content.first().is_some_and(|first| {
                first.is_object()
                    && get(first, "type").and_then(Json::as_str) == Some("tool_result")
                    && get(first, "tool_use_id").and_then(Json::as_str) == Some(call_id)
            })
    })
}

/// `collect_answer(config, session_id, call_id, limit, final_message_id=..., requests=...)`:
/// the tool-result message a kept process stored when its waiting call was answered, and the
/// records the CLI wrote after it. Never fails: `unknown` when it is missing.
pub fn collect_answer(
    config: &Path,
    session_id: &str,
    call_id: &str,
    limit: f64,
    final_message_id: Option<&str>,
    requests: Option<usize>,
) -> NativeCollection {
    let deadline = Instant::now() + Duration::from_secs_f64(limit.max(0.0));
    loop {
        let rows = jsonl_file(config, session_id).map(|file| rows(&file)).unwrap_or_default();
        let index = answer_row(&rows, call_id);
        if let (Some(index), Some(requests)) = (index, requests) {
            if requests_after(&rows, index) < requests {
                if Instant::now() < deadline {
                    std::thread::sleep(Duration::from_millis(20));
                    continue;
                }
                return NativeCollection::unknown("answer_record_missing");
            }
        }
        if let Some(index) = index {
            let mut records = Vec::new();
            for row in &rows[index + 1..] {
                let message_row = is_row(row, "assistant") || is_row(row, "user");
                if final_message_id.is_some() && message_row {
                    if is_final(row, final_message_id) {
                        break;
                    }
                    records.extend(round_row(row));
                    continue;
                }
                if message_row {
                    break;
                }
                if is_row(row, "attachment") && get(row, "rendered").is_some_and(crate::pyobj::truthy) {
                    records.push(attachment_copy(row));
                }
            }
            let answer = get(&rows[index], "message").cloned();
            return NativeCollection::collected(records, answer);
        }
        if Instant::now() >= deadline {
            return NativeCollection::unknown("answer_record_missing");
        }
        std::thread::sleep(Duration::from_millis(20));
    }
}
