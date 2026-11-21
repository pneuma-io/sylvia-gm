//! Bounded, content-free owner diagnostics and deliberate local report snapshots:
//! `interfaces/_local_diagnostics.py`.
//!
//! Only the constants below admit text into automatic records. Error messages, request ids,
//! arguments, names, paths and model or tool payloads never cross this boundary. The writer
//! owns two files: a 512 KiB snapshot (`diagnostics/runtime.jsonl`) and its 512 KiB atomic
//! replacement (`runtime.pending.jsonl`).
//!
//! One deliberate reading of Python's `runtime` field: Python writes `packaged` for a frozen
//! (PyInstaller) build and `source` otherwise. The Rust owner is always compiled, so a release
//! build writes `packaged` and a debug build, the counterpart of running from source, writes
//! `source`.

use crate::commands::REGISTRY;
use crate::util::{python_round1, uuid4_hex};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::sync::{mpsc, Arc, Condvar, Mutex};
use std::time::{Duration, SystemTime, UNIX_EPOCH};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};

/// `MAX_SNAPSHOT_BYTES`.
pub const MAX_SNAPSHOT_BYTES: usize = 512 * 1024;
/// `MAX_STORAGE_BYTES`.
pub const MAX_STORAGE_BYTES: usize = 2 * MAX_SNAPSHOT_BYTES;
/// `MAX_RECORDS`.
pub const MAX_RECORDS: usize = 512;
/// `MAX_AGE_SECONDS`.
pub const MAX_AGE_SECONDS: f64 = 7.0 * 24.0 * 60.0 * 60.0;
const WRITE_INTERVAL: Duration = Duration::from_secs(1);
const RETRY_INTERVAL: Duration = Duration::from_secs(30);
/// `MAX_REPORT_TEXT`, in characters.
pub const MAX_REPORT_TEXT: usize = 4000;
/// `MAX_REPORT_BYTES`.
pub const MAX_REPORT_BYTES: usize = 2 * 1024 * 1024;
/// `MAX_CLIENT_FAULT_DELTA`.
pub const MAX_CLIENT_FAULT_DELTA: i64 = 65535;
/// `2**31 - 1`, the largest count a record keeps.
const MAX_COUNT: i64 = 2_147_483_647;

/// `sylvia_gm.__version__`.
pub const VERSION: &str = env!("CARGO_PKG_VERSION");

const EVENTS: &[&str] = &[
    "owner_started",
    "owner_stopped",
    "owner_interrupted",
    "owner_failed",
    "command_completed",
    "request_rejected",
    "provider_warning",
    "renderer_error",
    "renderer_rejection",
];

const ERROR_CODES: &[&str] = &[
    "command_rejected",
    "internal_error",
    "provider_error",
    "provider_private_deliberation",
    "context_compaction_failed",
    "stale_character",
    "display_load_cancelled",
    "other_rejection",
    "none",
];

const ERROR_TYPES: &[&str] = &[
    "OwnerProtocolError",
    "CommandRejected",
    "AuthorityError",
    "ConfigError",
    "ValueError",
    "TypeError",
    "RuntimeError",
    "KeyError",
    "OSError",
    "PermissionError",
    "FileNotFoundError",
    "TimeoutError",
    "MemoryError",
    "ContextCompactionError",
    "TokenLimitError",
    "ProviderContinuationRequiredError",
    "ProviderRefusalError",
    "ProviderPausedError",
    "ProviderOutputLimitError",
    "ProviderTimeoutError",
    "ModelCapacityError",
    "AuthenticationError",
    "RateLimitError",
    "ModelNotFoundError",
    "ProviderConnectionError",
    "InvalidRequestError",
    "IncompleteProviderStreamError",
    "InvalidToolArgumentsError",
    "InvalidResponseError",
    "ProviderUnavailableError",
    "ProviderError",
    "OtherError",
    "none",
];

/// `OPERATIONS`: the registry commands recorded by diagnostics (`DIAGNOSTIC_OPERATIONS`).
fn is_operation(name: &str) -> bool {
    REGISTRY.iter().any(|command| command.diagnostics && command.name == name)
}

/// `_choice`: the value when it is one of `choices`, otherwise the fallback.
fn choice(value: Option<&Json>, choices: impl Fn(&str) -> bool, fallback: &str) -> String {
    match value {
        Some(Json::String(text)) if choices(text) => text.clone(),
        _ => fallback.to_owned(),
    }
}

/// `_number`: a finite, non-negative number clamped to `maximum` and rounded to 0.1, else 0.0.
/// Booleans are not numbers here.
fn number(value: Option<&Json>, maximum: f64) -> f64 {
    let Some(Json::Number(value)) = value else { return 0.0 };
    // Python converts an int beyond float range with OverflowError (0.0); serde has no such int.
    let Some(numeric) = value.as_f64() else { return 0.0 };
    if !numeric.is_finite() {
        return 0.0;
    }
    python_round1(numeric.min(maximum).max(0.0))
}

fn float(value: f64) -> Json {
    serde_json::Number::from_f64(value).map_or(Json::Null, Json::Number)
}

/// `_platform()`: a fixed OS name and, on Windows, the numeric OS version.
pub fn platform() -> Json {
    let mut result = JsonMap::new();
    let name = if cfg!(windows) {
        "windows"
    } else if cfg!(target_os = "linux") {
        "linux"
    } else if cfg!(target_os = "macos") {
        "macos"
    } else {
        "other"
    };
    result.insert("os".into(), name.into());
    #[cfg(windows)]
    {
        let version = windows_version::OsVersion::current();
        result.insert("major".into(), version.major.into());
        result.insert("minor".into(), version.minor.into());
        result.insert("build".into(), version.build.into());
    }
    Json::Object(result)
}

fn runtime() -> &'static str {
    if cfg!(debug_assertions) {
        "source"
    } else {
        "packaged"
    }
}

/// `_encode(records)`: one compact ASCII JSON line per record.
pub fn encode(records: &[JsonMap]) -> Vec<u8> {
    let mut out = Vec::new();
    for row in records {
        out.extend_from_slice(pyjson::dumps(&Json::Object(row.clone()), Dump::WIRE).as_bytes());
        out.push(b'\n');
    }
    out
}

/// `LocalDiagnostics._project(fields)`: every stored field constructed explicitly.
pub fn project(fields: &JsonMap) -> JsonMap {
    let event = choice(fields.get("event"), |text| EVENTS.contains(&text), "request_rejected");
    let outcome = choice(fields.get("outcome"), |text| text == "ok" || text == "error", "ok");
    let error_type = choice(fields.get("error_type"), |text| ERROR_TYPES.contains(&text), "OtherError");
    let severity = if outcome == "error" {
        "error"
    } else if event == "owner_interrupted" || event == "provider_warning" {
        "warning"
    } else {
        "info"
    };
    let mut row = JsonMap::new();
    row.insert("format".into(), 1.into());
    row.insert("event".into(), event.into());
    row.insert("app_version".into(), VERSION.into());
    row.insert("build_version".into(), VERSION.into());
    row.insert("runtime".into(), runtime().into());
    row.insert("platform".into(), platform());
    row.insert("operation".into(), choice(fields.get("operation"), is_operation, "other_operation").into());
    row.insert("outcome".into(), outcome.into());
    row.insert("severity".into(), severity.into());
    row.insert(
        "error_code".into(),
        choice(fields.get("error_code"), |text| ERROR_CODES.contains(&text), "other_rejection").into(),
    );
    row.insert("error_type".into(), error_type.into());
    row.insert("duration_ms".into(), float(number(fields.get("duration_ms"), 24.0 * 60.0 * 60.0 * 1000.0)));
    row
}

/// Python compares `row.get("format") != 1`, which `1`, `1.0` and `True` all pass.
fn is_format_one(value: Option<&Json>) -> bool {
    match value {
        Some(Json::Bool(flag)) => *flag,
        Some(Json::Number(number)) => number.as_f64() == Some(1.0),
        _ => false,
    }
}

fn plain_number(value: Option<&Json>) -> Option<f64> {
    match value {
        Some(Json::Number(number)) => number.as_f64(),
        _ => None,
    }
}

fn is_identity(value: Option<&Json>) -> bool {
    value.and_then(Json::as_str).is_some_and(|text| {
        text.chars().count() == 32 && text.chars().all(|c| "0123456789abcdef".contains(c))
    })
}

/// The key of one retained record: loaded records by their identity, new ones by what they
/// aggregate over (one minute of matching outcomes in one owner session).
#[derive(Debug, Clone, PartialEq)]
enum Key {
    Loaded(String),
    Aggregate { session: String, minute: i64, fields: [String; 5] },
}

struct Inner {
    records: Vec<(Key, JsonMap)>,
    session: String,
    generation: u64,
    persisted: Option<u64>,
    storage: &'static str,
    pending_report: Option<(String, String)>,
    closed: bool,
    stop: bool,
    wake: bool,
}

/// What every [`LocalDiagnostics`] shares with its writer thread.
struct Shared {
    path: PathBuf,
    pending_path: PathBuf,
    clock: Box<dyn Fn() -> f64 + Send + Sync>,
    inner: Mutex<Inner>,
    woken: Condvar,
}

/// `LocalDiagnostics`: capture independent of disk I/O, with one bounded writer thread.
pub struct LocalDiagnostics {
    shared: Arc<Shared>,
    finished: Mutex<Option<mpsc::Receiver<()>>>,
}

/// A plain `ValueError(message)`: code `command_rejected`.
fn value_error(message: &str) -> CodedError {
    CodedError::rejected(message).with_error_type("ValueError")
}

/// `time.time()`.
pub fn wall_clock() -> f64 {
    SystemTime::now().duration_since(UNIX_EPOCH).map_or(0.0, |elapsed| elapsed.as_secs_f64())
}

impl LocalDiagnostics {
    /// `LocalDiagnostics(storage_root)`.
    pub fn new(storage_root: &Path) -> Self {
        Self::with_clock(storage_root, Box::new(wall_clock))
    }

    /// `LocalDiagnostics(storage_root, clock=clock)`.
    pub fn with_clock(storage_root: &Path, clock: Box<dyn Fn() -> f64 + Send + Sync>) -> Self {
        let path = storage_root.join("diagnostics").join("runtime.jsonl");
        let pending_path = path.with_file_name("runtime.pending.jsonl");
        let shared = Arc::new(Shared {
            path,
            pending_path,
            clock,
            inner: Mutex::new(Inner {
                records: Vec::new(),
                session: uuid4_hex(),
                generation: 0,
                persisted: None,
                storage: "available",
                pending_report: None,
                closed: false,
                stop: false,
                wake: false,
            }),
            woken: Condvar::new(),
        });
        let diagnostics = Self { shared, finished: Mutex::new(None) };
        diagnostics.load();
        let previous_event = {
            let inner = diagnostics.lock();
            inner
                .records
                .last()
                .and_then(|(_, row)| row.get("event").and_then(Json::as_str).map(str::to_owned))
        };
        if previous_event.is_some_and(|event| event != "owner_stopped") {
            diagnostics.record("owner_interrupted", Fields::default());
        }
        diagnostics.record("owner_started", Fields::default());
        let (done, finished) = mpsc::channel();
        let shared = Arc::clone(&diagnostics.shared);
        let started = std::thread::Builder::new().name("local-diagnostics".into()).spawn(move || {
            writer(&shared);
            let _ = done.send(());
        });
        match started {
            Ok(_) => *diagnostics.finished.lock().expect("diagnostics lock") = Some(finished),
            Err(_) => diagnostics.lock().storage = "unavailable",
        }
        diagnostics
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Inner> {
        self.shared.inner.lock().expect("diagnostics lock")
    }

    /// `_load`: read a bounded current-format snapshot. Legacy or raw objects are never kept.
    fn load(&self) {
        let path = &self.shared.path;
        let Ok(metadata) = std::fs::metadata(path) else { return };
        if metadata.len() > MAX_SNAPSHOT_BYTES as u64 {
            return;
        }
        let Ok(data) = std::fs::read(path) else { return };
        if data.len() > MAX_SNAPSHOT_BYTES {
            return;
        }
        let now = (self.shared.clock)();
        let lines = bytes_splitlines(&data);
        let start = lines.len().saturating_sub(MAX_RECORDS);
        let mut inner = self.lock();
        for line in &lines[start..] {
            // `json.loads` raising ValueError skips the line; so does text that is not UTF-8.
            let Ok(text) = std::str::from_utf8(line) else { continue };
            let Ok(Json::Object(row)) = pyjson::loads(text) else { continue };
            if !is_format_one(row.get("format"))
                || row.get("app_version").and_then(Json::as_str) != Some(VERSION)
            {
                continue;
            }
            let (Some(first), Some(last)) =
                (plain_number(row.get("first_seen")), plain_number(row.get("last_seen")))
            else {
                continue;
            };
            if !(now - MAX_AGE_SECONDS <= first && first <= last && last <= now) {
                continue;
            }
            if !is_identity(row.get("id")) || !is_identity(row.get("session")) {
                continue;
            }
            let identity = row["id"].as_str().expect("checked").to_owned();
            let mut projected = project(&row);
            projected.insert("id".into(), identity.clone().into());
            projected.insert("session".into(), row["session"].clone());
            projected.insert("first_seen".into(), row["first_seen"].clone());
            projected.insert("last_seen".into(), row["last_seen"].clone());
            // `max(1, int(_number(count, 2**31 - 1)))`: int() truncates the rounded value.
            let count = (number(row.get("count"), MAX_COUNT as f64).trunc() as i64).max(1);
            projected.insert("count".into(), count.into());
            let key = Key::Loaded(identity);
            match inner.records.iter_mut().find(|(existing, _)| *existing == key) {
                // A repeated identity replaces the row in its first position, as a dict does.
                Some((_, slot)) => *slot = projected,
                None => inner.records.push((key, projected)),
            }
        }
    }

    /// `record(event, ...)`: aggregate one minute of matching outcomes. Returns the record's
    /// identity, which error replies carry as `diagnostic_id`.
    pub fn record(&self, event: &str, fields: Fields<'_>) -> String {
        let now = (self.shared.clock)();
        let mut input = JsonMap::new();
        input.insert("event".into(), event.into());
        input.insert("operation".into(), fields.operation.map_or(Json::Null, Json::from));
        input.insert("outcome".into(), fields.outcome.unwrap_or("ok").into());
        input.insert("error_code".into(), fields.error_code.unwrap_or("none").into());
        input.insert("error_type".into(), fields.error_type.unwrap_or("none").into());
        input.insert("duration_ms".into(), fields.duration_ms.map_or(Json::Null, float));
        let row = project(&input);
        let text = |key: &str| row[key].as_str().unwrap_or_default().to_owned();
        let count_delta = fields.count_delta.unwrap_or(1);
        let mut inner = self.lock();
        if inner.closed {
            return inner.session.clone();
        }
        let key = Key::Aggregate {
            session: inner.session.clone(),
            // `int(now // 60)`.
            minute: (now / 60.0).floor() as i64,
            fields: [
                text("event"),
                text("operation"),
                text("outcome"),
                text("error_code"),
                text("error_type"),
            ],
        };
        let identity =
            if let Some((_, retained)) = inner.records.iter_mut().find(|(existing, _)| *existing == key) {
                let count = retained.get("count").and_then(Json::as_i64).unwrap_or(0);
                // Both terms are bounded (a kept count by MAX_COUNT, a delta by 65535), so the
                // sum cannot overflow; saturating keeps that true if a bound ever moves.
                retained.insert("count".into(), MAX_COUNT.min(count.saturating_add(count_delta)).into());
                retained.insert("last_seen".into(), float(now));
                let duration = row["duration_ms"].as_f64().unwrap_or(0.0);
                let kept = retained.get("duration_ms").and_then(Json::as_f64).unwrap_or(0.0);
                retained.insert("duration_ms".into(), float(duration.max(kept)));
                retained["id"].as_str().unwrap_or_default().to_owned()
            } else {
                let identity = uuid4_hex();
                let mut retained = row;
                retained.insert("id".into(), identity.clone().into());
                retained.insert("session".into(), inner.session.clone().into());
                retained.insert("first_seen".into(), float(now));
                retained.insert("last_seen".into(), float(now));
                retained.insert("count".into(), count_delta.into());
                inner.records.push((key, retained));
                while inner.records.len() > MAX_RECORDS {
                    inner.records.remove(0);
                }
                identity
            };
        inner.generation += 1;
        drop(inner);
        identity
    }

    /// `preview(kind=, reproduction=, feedback=)`: freeze the exact export bytes.
    pub fn preview(
        &self,
        kind: Option<&Json>,
        reproduction: Option<&Json>,
        feedback: Option<&Json>,
    ) -> CodedResult<Json> {
        let kind = match kind {
            Some(Json::String(kind)) if kind == "app_problem" || kind == "model_feedback" => kind.clone(),
            _ => return Err(value_error("Choose an app problem or model feedback.")),
        };
        let mut texts = Vec::new();
        for value in [reproduction, feedback] {
            match value {
                Some(Json::String(text)) if text.chars().count() <= MAX_REPORT_TEXT => {
                    texts.push(text.clone())
                }
                _ => return Err(value_error("Report fields must each contain at most 4,000 characters.")),
            }
        }
        let (_, rows) = snapshot(&self.shared);
        let mut application = JsonMap::new();
        application.insert("version".into(), VERSION.into());
        application.insert("build_version".into(), VERSION.into());
        application.insert("runtime".into(), runtime().into());
        application.insert("platform".into(), platform());
        let mut retention = JsonMap::new();
        retention.insert("max_storage_bytes".into(), MAX_STORAGE_BYTES.into());
        retention.insert("max_age_days".into(), 7.into());
        let mut entered = JsonMap::new();
        entered.insert("reproduction_steps".into(), texts[0].clone().into());
        entered.insert("feedback".into(), texts[1].clone().into());
        let storage = self.lock().storage;
        let mut payload = JsonMap::new();
        payload.insert("format".into(), "sylvia-local-report-v1".into());
        payload.insert("application".into(), Json::Object(application));
        payload.insert("retention".into(), Json::Object(retention));
        payload.insert("storage_status".into(), storage.into());
        payload.insert("report_kind".into(), kind.into());
        payload.insert("player_entered".into(), Json::Object(entered));
        payload.insert("diagnostics".into(), Json::Array(rows.into_iter().map(Json::Object).collect()));
        let mut content = pyjson::dumps(&Json::Object(payload), Dump::INDENT2);
        content.push('\n');
        if content.len() > MAX_REPORT_BYTES {
            return Err(value_error("The local report exceeds its size limit."));
        }
        let identity = uuid4_hex();
        let bytes = content.len();
        self.lock().pending_report = Some((identity.clone(), content.clone()));
        let mut result = JsonMap::new();
        result.insert("preview_id".into(), identity.into());
        result.insert("content".into(), content.into());
        result.insert("bytes".into(), bytes.into());
        Ok(Json::Object(result))
    }

    /// `client_fault(category, delta)`: a renderer failure category, never its value.
    pub fn client_fault(&self, category: Option<&Json>, delta: Option<&Json>) -> CodedResult<Json> {
        let category = match category {
            Some(Json::String(category))
                if category == "renderer_error" || category == "renderer_rejection" =>
            {
                category.clone()
            }
            _ => return Err(value_error("Unknown app failure category.")),
        };
        // `type(delta) is not int`: a bool or a float is refused.
        let delta = match delta {
            Some(Json::Number(number)) if number.is_i64() || number.is_u64() => {
                number.as_i64().filter(|delta| (1..=MAX_CLIENT_FAULT_DELTA).contains(delta))
            }
            _ => None,
        };
        let Some(delta) = delta else {
            return Err(value_error("App failure count must be an integer from 1 to 65535."));
        };
        let identity = self.record(
            &category,
            Fields {
                operation: Some("renderer"),
                outcome: Some("error"),
                error_code: Some("internal_error"),
                count_delta: Some(delta),
                ..Fields::default()
            },
        );
        let mut result = JsonMap::new();
        result.insert("diagnostic_id".into(), identity.into());
        Ok(Json::Object(result))
    }

    /// `cancel(preview_id)`.
    pub fn cancel(&self, preview_id: &str) {
        let mut inner = self.lock();
        if inner.pending_report.as_ref().is_some_and(|(identity, _)| identity == preview_id) {
            inner.pending_report = None;
        }
    }

    /// `save(preview_id, destination)`: write the previewed bytes through a temporary file.
    pub fn save(&self, preview_id: &str, destination: &Path) -> CodedResult<Json> {
        let pending = self.lock().pending_report.clone();
        let Some((_, content)) = pending.filter(|(identity, _)| identity == preview_id) else {
            return Err(value_error("Preview this report again before saving."));
        };
        let data = content.into_bytes();
        let parent = destination.parent().unwrap_or(Path::new(""));
        let mut temporary: Option<PathBuf> = None;
        let outcome = (|| -> std::io::Result<()> {
            let (path, mut stream) = crate::util::named_temporary(parent, ".sylvia-report-", ".tmp")?;
            temporary = Some(path.clone());
            stream.write_all(&data)?;
            stream.flush()?;
            stream.sync_all()?;
            drop(stream);
            std::fs::rename(&path, destination)
        })();
        if let Some(path) = temporary {
            let _ = std::fs::remove_file(path);
        }
        if outcome.is_err() {
            return Err(value_error("Could not save the report. Choose a writable local file."));
        }
        self.cancel(preview_id);
        let mut result = JsonMap::new();
        result.insert("status".into(), "saved".into());
        result.insert("bytes".into(), data.len().into());
        Ok(Json::Object(result))
    }

    /// `close()`: one final write; a stuck filesystem never holds shutdown past a second.
    pub fn close(&self) {
        self.record("owner_stopped", Fields::default());
        {
            let mut inner = self.lock();
            inner.closed = true;
            inner.stop = true;
            inner.wake = true;
        }
        self.shared.woken.notify_all();
        if let Some(finished) = self.finished.lock().expect("diagnostics lock").take() {
            let _ = finished.recv_timeout(Duration::from_secs(1));
        }
    }
}

/// The optional fields of [`LocalDiagnostics::record`], Python's keyword arguments.
#[derive(Debug, Clone, Copy, Default)]
pub struct Fields<'a> {
    /// `operation=`.
    pub operation: Option<&'a str>,
    /// `outcome=` (default `ok`).
    pub outcome: Option<&'a str>,
    /// `error_code=` (default `none`).
    pub error_code: Option<&'a str>,
    /// `error_type=` (default `none`).
    pub error_type: Option<&'a str>,
    /// `duration_ms=`.
    pub duration_ms: Option<f64>,
    /// `count_delta=` (default 1).
    pub count_delta: Option<i64>,
}

/// `bytes.splitlines()`: splits on `\n`, `\r\n` and `\r`.
fn bytes_splitlines(data: &[u8]) -> Vec<&[u8]> {
    let mut lines = Vec::new();
    let mut start = 0;
    let mut index = 0;
    while index < data.len() {
        match data[index] {
            b'\n' => {
                lines.push(&data[start..index]);
                start = index + 1;
            }
            b'\r' => {
                lines.push(&data[start..index]);
                if data.get(index + 1) == Some(&b'\n') {
                    index += 1;
                }
                start = index + 1;
            }
            _ => {}
        }
        index += 1;
    }
    if start < data.len() {
        lines.push(&data[start..]);
    }
    lines
}

/// `_snapshot()`: drop expired records, then copy the rest.
fn snapshot(shared: &Shared) -> (u64, Vec<JsonMap>) {
    let mut inner = shared.inner.lock().expect("diagnostics lock");
    let cutoff = (shared.clock)() - MAX_AGE_SECONDS;
    let before = inner.records.len();
    inner
        .records
        .retain(|(_, row)| row.get("first_seen").and_then(Json::as_f64).is_none_or(|first| first >= cutoff));
    if inner.records.len() != before {
        inner.generation += 1;
    }
    (inner.generation, inner.records.iter().map(|(_, row)| row.clone()).collect())
}

/// `_write_snapshot(rows)`: flush a replacement, then publish it atomically.
fn write_snapshot(shared: &Shared, mut rows: Vec<JsonMap>) -> std::io::Result<()> {
    let mut data = encode(&rows);
    while data.len() > MAX_SNAPSHOT_BYTES {
        rows.remove(0);
        data = encode(&rows);
    }
    if let Some(parent) = shared.path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    {
        let mut stream = std::fs::File::create(&shared.pending_path)?;
        stream.write_all(&data)?;
        stream.flush()?;
        stream.sync_all()?;
    }
    std::fs::rename(&shared.pending_path, &shared.path)?;
    // The prior journal owner's sole backup, never a campaign or audit export.
    match std::fs::remove_file(shared.path.with_file_name("runtime.previous.jsonl")) {
        Err(error) if error.kind() != std::io::ErrorKind::NotFound => Err(error),
        _ => Ok(()),
    }
}

/// `_writer()`: write whenever the records changed, at most once a second.
fn writer(shared: &Shared) {
    loop {
        let (generation, rows) = snapshot(shared);
        let mut failed = false;
        let persisted = shared.inner.lock().expect("diagnostics lock").persisted;
        if persisted != Some(generation) {
            let written = write_snapshot(shared, rows);
            let mut inner = shared.inner.lock().expect("diagnostics lock");
            match written {
                Ok(()) => {
                    inner.persisted = Some(generation);
                    inner.storage = "available";
                }
                Err(_) => {
                    // No stderr or raw error fallback, recursive logging or retry queue.
                    inner.storage = "unavailable";
                    failed = true;
                }
            }
        }
        let mut inner = shared.inner.lock().expect("diagnostics lock");
        if inner.stop {
            if failed || generation == inner.generation {
                return;
            }
            continue;
        }
        let wait = if failed { RETRY_INTERVAL } else { WRITE_INTERVAL };
        if !inner.wake {
            inner = shared.woken.wait_timeout(inner, wait).expect("diagnostics lock").0;
        }
        inner.wake = false;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    fn fixed_clock(seconds: f64) -> Box<dyn Fn() -> f64 + Send + Sync> {
        Box::new(move || seconds)
    }

    fn folder(name: &str) -> PathBuf {
        let path = std::env::temp_dir().join(format!("sylvia-owner-diag-{name}-{}", std::process::id()));
        let _ = std::fs::remove_dir_all(&path);
        std::fs::create_dir_all(&path).unwrap();
        path
    }

    #[test]
    fn projection_keeps_only_fixed_categories() {
        let row = project(
            json!({"event": "owner_started", "operation": "campaign_index", "outcome": "ok",
            "error_code": "none", "error_type": "none", "duration_ms": 12.345})
            .as_object()
            .unwrap(),
        );
        let keys: Vec<&String> = row.keys().collect();
        assert_eq!(
            keys,
            [
                "format",
                "event",
                "app_version",
                "build_version",
                "runtime",
                "platform",
                "operation",
                "outcome",
                "severity",
                "error_code",
                "error_type",
                "duration_ms"
            ]
        );
        assert_eq!(row["duration_ms"], json!(12.3));
        let odd = project(
            json!({"event": "secret", "operation": "my campaign", "outcome": "maybe",
            "error_code": "boom", "error_type": "Exception", "duration_ms": true})
            .as_object()
            .unwrap(),
        );
        assert_eq!(
            [
                &odd["event"],
                &odd["operation"],
                &odd["outcome"],
                &odd["error_code"],
                &odd["error_type"],
                &odd["duration_ms"]
            ],
            [
                &json!("request_rejected"),
                &json!("other_operation"),
                &json!("ok"),
                &json!("other_rejection"),
                &json!("OtherError"),
                &json!(0.0)
            ]
        );
    }

    #[test]
    fn records_aggregate_by_minute_and_survive_a_restart() {
        let root = folder("restart");
        let diagnostics = LocalDiagnostics::with_clock(&root, fixed_clock(1_800_000_000.0));
        let fields = Fields {
            operation: Some("campaign_index"),
            outcome: Some("error"),
            error_code: Some("command_rejected"),
            error_type: Some("CommandRejected"),
            ..Fields::default()
        };
        let first = diagnostics.record("command_completed", fields);
        let second = diagnostics.record("command_completed", fields);
        assert_eq!(first, second, "one minute of matching outcomes is one record");
        assert_eq!(first.len(), 32);
        diagnostics.close();
        let text = std::fs::read_to_string(root.join("diagnostics/runtime.jsonl")).unwrap();
        let events: Vec<String> = text
            .lines()
            .map(|line| serde_json::from_str::<Json>(line).unwrap()["event"].as_str().unwrap().to_owned())
            .collect();
        assert_eq!(events, ["owner_started", "command_completed", "owner_stopped"]);
        assert!(text.contains("\"count\":2"));

        // A run that did not stop cleanly is reported as interrupted by the next one.
        let again = LocalDiagnostics::with_clock(&root, fixed_clock(1_800_000_100.0));
        again.record("command_completed", fields);
        // Not closed: wait for the writer's own snapshot, as a killed owner leaves it.
        let deadline = std::time::Instant::now() + Duration::from_secs(10);
        while !std::fs::read_to_string(root.join("diagnostics/runtime.jsonl"))
            .unwrap_or_default()
            .contains("1800000100")
        {
            assert!(std::time::Instant::now() < deadline, "the writer did not write within 10 s");
            std::thread::sleep(Duration::from_millis(20));
        }
        drop(again);
        let resumed = LocalDiagnostics::with_clock(&root, fixed_clock(1_800_000_200.0));
        resumed.close();
        let text = std::fs::read_to_string(root.join("diagnostics/runtime.jsonl")).unwrap();
        assert!(text.contains("owner_interrupted"), "{text}");
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn reports_and_faults_check_their_inputs_with_pythons_messages() {
        let root = folder("report");
        let diagnostics = LocalDiagnostics::with_clock(&root, fixed_clock(1_800_000_000.0));
        let error = |result: CodedResult<Json>| result.unwrap_err().message;
        assert_eq!(
            error(diagnostics.preview(Some(&json!("other")), Some(&json!("")), Some(&json!("")))),
            "Choose an app problem or model feedback."
        );
        assert_eq!(
            error(diagnostics.preview(
                Some(&json!("app_problem")),
                Some(&json!("x".repeat(4001))),
                Some(&json!(""))
            )),
            "Report fields must each contain at most 4,000 characters."
        );
        assert_eq!(
            error(diagnostics.client_fault(Some(&json!("renderer_error")), Some(&json!(true)))),
            "App failure count must be an integer from 1 to 65535."
        );
        assert_eq!(
            error(diagnostics.client_fault(Some(&json!("renderer_error")), Some(&json!(1.0)))),
            "App failure count must be an integer from 1 to 65535."
        );
        assert_eq!(
            error(diagnostics.client_fault(Some(&json!("crash")), Some(&json!(1)))),
            "Unknown app failure category."
        );
        assert!(diagnostics.client_fault(Some(&json!("renderer_error")), Some(&json!(3))).is_ok());

        let preview =
            diagnostics.preview(Some(&json!("app_problem")), Some(&json!("Ré")), Some(&json!(""))).unwrap();
        let content = preview["content"].as_str().unwrap();
        assert!(content.starts_with("{\n  \"format\": \"sylvia-local-report-v1\","));
        assert!(content.contains("\"reproduction_steps\": \"R\\u00e9\""));
        assert!(content.ends_with("}\n"));
        let destination = root.join("report.json");
        assert_eq!(
            error(diagnostics.save("not-it", &destination)),
            "Preview this report again before saving."
        );
        let saved = diagnostics.save(preview["preview_id"].as_str().unwrap(), &destination).unwrap();
        assert_eq!(saved, json!({"status": "saved", "bytes": content.len()}));
        assert_eq!(std::fs::read_to_string(&destination).unwrap(), content);
        assert_eq!(
            error(diagnostics.save(preview["preview_id"].as_str().unwrap(), &destination)),
            "Preview this report again before saving."
        );
        diagnostics.close();
        let _ = std::fs::remove_dir_all(root);
    }

    #[test]
    fn splits_lines_like_python_bytes() {
        assert_eq!(bytes_splitlines(b"a\nb\r\nc\rd"), [&b"a"[..], b"b", b"c", b"d"]);
        assert_eq!(bytes_splitlines(b"a\n"), [&b"a"[..]]);
    }
}
