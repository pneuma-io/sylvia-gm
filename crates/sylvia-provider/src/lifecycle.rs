//! `provider/_lifecycle.py`: bounded, metadata-only observations of an adapter's local
//! lifecycle boundaries. Lane 5.
//!
//! Python reads `time.perf_counter_ns()`; here the clock is
//! [`crate::codex_rpc::perf_counter_ns`], and the `_at` methods take its readings so tests can
//! supply Python's.

use crate::codex_rpc::perf_counter_ns;
use std::sync::Mutex;
use sylvia_base::{Json, JsonMap};

/// `_PHASES`.
pub const PHASES: [&str; 9] = [
    "runtime_start",
    "process_boot",
    "initialize",
    "account_read",
    "thread_start",
    "history_injection",
    "turn_start",
    "turn_stream",
    "shutdown",
];

/// The most spans one timing keeps.
const SPAN_LIMIT: usize = 24;

#[derive(Debug, Clone, PartialEq, Eq)]
struct Span {
    name: &'static str,
    parent: Option<String>,
    start: u128,
    end: u128,
    status: &'static str,
}

/// `LifecycleTiming`: one monotonic clock, with nested spans whose durations are not added
/// twice.
#[derive(Debug)]
pub struct LifecycleTiming {
    pub started_utc: String,
    pub origin: u128,
    spans: Mutex<Vec<Span>>,
}

impl LifecycleTiming {
    /// `LifecycleTiming()`.
    pub fn new() -> Self {
        Self::at(crate::hooks::utc_now_isoformat(), perf_counter_ns())
    }

    /// A timing started at `origin` (nanoseconds on the clock), stamped `started_utc`.
    pub fn at(started_utc: String, origin: u128) -> Self {
        Self { started_utc, origin, spans: Mutex::new(Vec::new()) }
    }

    /// `record(name, start, end, status, parent=...)`: kept when the phase and status are known,
    /// the span falls after the origin, and fewer than 24 are held.
    pub fn record(&self, name: &str, start: u128, end: u128, status: &str, parent: Option<&str>) {
        let Some(name) = PHASES.iter().copied().find(|phase| *phase == name) else { return };
        let Some(status) = ["completed", "error", "cancelled"].into_iter().find(|known| *known == status)
        else {
            return;
        };
        if start < self.origin || end < start {
            return;
        }
        let mut spans = self.spans.lock().expect("span lock");
        if spans.len() < SPAN_LIMIT {
            spans.push(Span { name, parent: parent.map(str::to_owned), start, end, status });
        }
    }

    /// `measure(name)`: time `work`; a failure is `error`, or `cancelled` when `cancelled`
    /// says so (Python's `CancelledError`, `GeneratorExit` and `CodexCancelledError`).
    pub fn measure<T, E>(
        &self,
        name: &str,
        cancelled: impl Fn(&E) -> bool,
        work: impl FnOnce() -> Result<T, E>,
    ) -> Result<T, E> {
        let start = perf_counter_ns();
        let outcome = work();
        let status = match &outcome {
            Ok(_) => "completed",
            Err(error) if cancelled(error) => "cancelled",
            Err(_) => "error",
        };
        self.record(name, start, perf_counter_ns(), status, None);
        outcome
    }

    /// `snapshot(status)`.
    pub fn snapshot(&self, status: &str) -> JsonMap {
        self.snapshot_at(status, perf_counter_ns())
    }

    /// `snapshot(status)` with the clock reading `now`. Receipt emission itself is excluded;
    /// audit, consumer and scheduling time stays visible as the unclassified residual.
    pub fn snapshot_at(&self, status: &str, now: u128) -> JsonMap {
        let elapsed = now as i128 - self.origin as i128;
        let mut spans = self.spans.lock().expect("span lock").clone();
        // `sorted(..., key=start_ns)`: stable, as Python's sort is.
        spans.sort_by_key(|span| span.start);
        let (mut covered, mut last) = (0i128, 0i128);
        let mut listed = Vec::new();
        for span in &spans {
            let (start, end) = ((span.start - self.origin) as i128, (span.end - self.origin) as i128);
            covered += (end - last.max(start)).max(0);
            last = last.max(end);
            let mut entry = JsonMap::new();
            entry.insert("name".into(), Json::String(span.name.into()));
            entry.insert("parent".into(), span.parent.clone().map_or(Json::Null, Json::String));
            entry.insert("start_ns".into(), number(start));
            entry.insert("end_ns".into(), number(end));
            entry.insert("duration_ns".into(), number(end - start));
            entry.insert("status".into(), Json::String(span.status.into()));
            listed.push(Json::Object(entry));
        }
        let boot = spans.iter().any(|span| span.name == "process_boot");
        let mut out = JsonMap::new();
        out.insert("schema_version".into(), Json::from(1));
        out.insert("clock".into(), Json::String("perf_counter_ns".into()));
        out.insert("started_utc".into(), Json::String(self.started_utc.clone()));
        out.insert("scope".into(), Json::String("adapter_lifecycle_not_model_service_time".into()));
        out.insert("status".into(), Json::String(status.into()));
        out.insert("elapsed_ns".into(), number(elapsed));
        out.insert("covered_ns".into(), number(covered));
        out.insert("unclassified_ns".into(), number(elapsed - covered));
        out.insert("spans".into(), Json::Array(listed));
        out.insert(
            "runtime_start_detail".into(),
            Json::String(if boot { "observed" } else { "unavailable" }.into()),
        );
        out
    }
}

impl Default for LifecycleTiming {
    fn default() -> Self {
        Self::new()
    }
}

fn number(value: i128) -> Json {
    i64::try_from(value).map_or_else(|_| Json::from(value as f64), Json::from)
}
