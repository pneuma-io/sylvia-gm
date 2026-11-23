//! `agent/_activity.py`: a small, private-content-free snapshot of one admitted response
//! attempt.

use std::sync::{Arc, Mutex};
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};

/// The tool-name prefix `operation_category` strips: `^tool_[a-f0-9]{12}_`.
fn strip_tool_prefix(name: &str) -> &str {
    let Some(rest) = name.strip_prefix("tool_") else { return name };
    let bytes = rest.as_bytes();
    if bytes.len() > 12
        && bytes[..12].iter().all(|byte| matches!(byte, b'a'..=b'f' | b'0'..=b'9'))
        && bytes[12] == b'_'
    {
        &rest[13..]
    } else {
        name
    }
}

/// `operation_category(name)`: a tool name reduced to the fixed vocabulary live activity shows.
pub fn operation_category(name: Option<&str>) -> &'static str {
    let name = strip_tool_prefix(name.unwrap_or(""));
    match name {
        "read_file"
        | "read_reference"
        | "read_campaign_file"
        | "read_campaign_reference"
        | "get_content_definition"
        | "inspect_builder_references"
        | "read_session_history"
        | "list_files"
        | "list_directory"
        | "search_files"
        | "search_knowledge"
        | "search_campaign_knowledge"
        | "search_library"
        | "discover_library"
        | "get_library_item" => "read",
        "write_file" | "edit_file" | "create_file" | "copy_builder_reference" => "write",
        "check_participant_draft"
        | "check_participant_draft_file"
        | "check_character_profile_draft"
        | "test_ruleset_draft"
        | "check_campaign_plan"
        | "check_campaign_setting" => "validate",
        "publish_participant_artifact"
        | "publish_participant_artifact_file"
        | "publish_character_profile"
        | "publish_campaign_plan"
        | "publish_campaign_setting"
        | "publish_ruleset_candidate" => "save",
        "ask_question" => "question",
        _ => "action",
    }
}

/// A monotonic clock in seconds (`time.monotonic`).
pub type Clock = Arc<dyn Fn() -> f64 + Send + Sync>;

/// `time.monotonic`.
pub fn monotonic() -> Clock {
    let start = std::time::Instant::now();
    Arc::new(move || start.elapsed().as_secs_f64())
}

#[derive(Debug)]
struct Progress {
    started: f64,
    last: f64,
    ended: Option<f64>,
    phase: String,
    operation: Option<String>,
    last_operation: Option<String>,
    correction: bool,
    revision: i64,
}

/// `AttemptProgress`: observed transitions; polling never changes the last-activity clock.
pub struct AttemptProgress {
    clock: Clock,
    state: Mutex<Progress>,
}

impl std::fmt::Debug for AttemptProgress {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter.debug_struct("AttemptProgress").field("state", &self.state).finish_non_exhaustive()
    }
}

fn value_error(message: &str) -> CodedError {
    CodedError::rejected(message).with_error_type("ValueError")
}

impl AttemptProgress {
    /// `AttemptProgress(clock)`.
    pub fn new(clock: Clock) -> Self {
        let now = clock();
        Self {
            clock,
            state: Mutex::new(Progress {
                started: now,
                last: now,
                ended: None,
                phase: "queued".into(),
                operation: None,
                last_operation: None,
                correction: false,
                revision: 0,
            }),
        }
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, Progress> {
        self.state.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    /// `update(phase, tool=None)`.
    pub fn update(&self, phase: &str, tool: Option<&str>) -> CodedResult<()> {
        if !matches!(
            phase,
            "queued" | "working" | "provider" | "tool" | "retrying" | "compacting" | "stopping"
        ) {
            return Err(value_error("Unsupported activity phase"));
        }
        let mut state = self.lock();
        if state.ended.is_some() || state.phase == "stopping" {
            return Ok(());
        }
        state.last = (self.clock)();
        state.revision += 1;
        if phase == "retrying" {
            state.correction = true;
        }
        let mut phase = phase;
        if phase == "provider" && state.correction {
            phase = "retrying";
        } else if phase == "working" {
            state.correction = false;
        }
        state.phase = phase.to_owned();
        state.operation = (phase == "tool").then(|| operation_category(tool).to_owned());
        if let Some(operation) = state.operation.clone() {
            state.last_operation = Some(operation);
        }
        Ok(())
    }

    /// `observe()`: record receipt without exposing provider text or restarting the phase.
    pub fn observe(&self) {
        let mut state = self.lock();
        if state.ended.is_some() || state.phase == "stopping" {
            return;
        }
        state.last = (self.clock)();
        if matches!(state.phase.as_str(), "provider" | "retrying" | "queued") {
            state.phase = "working".into();
            state.correction = false;
            state.revision += 1;
        }
    }

    /// `expect_correction()`: Retrying starts only when another request is admitted.
    pub fn expect_correction(&self) {
        let mut state = self.lock();
        if state.ended.is_none() {
            state.correction = true;
        }
    }

    /// `finish(status)`.
    pub fn finish(&self, status: &str) -> CodedResult<JsonMap> {
        if !matches!(status, "completed" | "stopped" | "failed" | "interrupted") {
            return Err(value_error("Unsupported terminal activity status"));
        }
        let mut state = self.lock();
        if state.ended.is_none() {
            state.ended = Some((self.clock)());
            state.phase = status.to_owned();
            state.operation = None;
            state.revision += 1;
        }
        let ended = state.ended.unwrap_or_default();
        Ok(snapshot(&state, ended))
    }

    /// `snapshot()`.
    pub fn snapshot(&self) -> JsonMap {
        let state = self.lock();
        let now = state.ended.unwrap_or_else(|| (self.clock)());
        snapshot(&state, now)
    }
}

/// `max(0, int(seconds * 1000))`: `int` truncates toward zero.
fn milliseconds(seconds: f64) -> i64 {
    ((seconds * 1000.0).trunc() as i64).max(0)
}

fn snapshot(state: &Progress, now: f64) -> JsonMap {
    let text = |value: &Option<String>| value.as_deref().map_or(Json::Null, Json::from);
    let mut map = JsonMap::new();
    map.insert("phase".into(), Json::from(state.phase.as_str()));
    map.insert("operation".into(), text(&state.operation));
    map.insert("last_operation".into(), text(&state.last_operation));
    map.insert("elapsed_ms".into(), Json::from(milliseconds(now - state.started)));
    map.insert("quiet_ms".into(), Json::from(milliseconds(now - state.last)));
    map.insert("revision".into(), Json::from(state.revision));
    map
}
