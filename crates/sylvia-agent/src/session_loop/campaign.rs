//! `agent/_session_loop.py`'s `CampaignSessionLoop`: the session loop the owner's session
//! commands run turns through (lane 5's `commands/sessions.rs`, lane 1's Builder submit).
//!
//! **A stub, agreed with lanes 1 and 5 (24 Nov 2026).** The API is Python's, so the callers can
//! be written against it now; the loop is built once per owner process and held as
//! `Arc<CampaignSessionLoop>`. The stub never starts a run, so what Python answers for a
//! session with no live provider work is answered exactly: `assert_idle` passes, the hint and
//! provisional output and live progress are empty, `builder_activity` projects the saved
//! attempt records, and `steer`, `stop`, `expire`, `disconnect` and `reconnect` refuse as
//! Python's `_active_run` does. Everything that runs a turn answers `rust_port_pending` naming
//! the method, until T5 (the run, its events, compaction, Stop Session) and T6 (retry) port it.

use super::{
    ProviderControl, SessionFailure, SessionProvider, SessionRunRequest, SessionRunResult, SessionStopResult,
};
use crate::provider_session::reads::TurnOwner;
use crate::provider_turn::TurnFailure;
use std::sync::Arc;
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};
use sylvia_domain::authority::{
    CanonicalReplay, ManualRetryResult, ProseChannel, RetryNarrationInput, WriterRole,
};
use sylvia_domain::pyops::py_str;
use sylvia_domain::session::{SessionMessage, SessionRecord};
use sylvia_provider::factory::PORT_PENDING;
use sylvia_provider::BoxFuture;

/// `NarrationStarted` / `ProviderWorkStarted`: `(campaign_id, attempt_id, cancel)`.
pub type LifecycleStarted = Arc<dyn Fn(&str, &str, Arc<dyn Fn() + Send + Sync>) + Send + Sync>;
/// `NarrationFinished` / `ProviderWorkFinished`: `(campaign_id, attempt_id)`.
pub type LifecycleFinished = Arc<dyn Fn(&str, &str) + Send + Sync>;

/// `CampaignSessionLoop(owner, on_narration_started=..., on_narration_finished=...,
/// on_provider_work_started=..., on_provider_work_finished=...)`: each `None` by default.
#[derive(Clone, Default)]
pub struct LoopHooks {
    pub on_narration_started: Option<LifecycleStarted>,
    pub on_narration_finished: Option<LifecycleFinished>,
    pub on_provider_work_started: Option<LifecycleStarted>,
    pub on_provider_work_finished: Option<LifecycleFinished>,
}

/// `run_builder`'s `persist`: called once, after the run, for the appended messages.
pub type BuilderPersist =
    Box<dyn FnOnce() -> BoxFuture<'static, CodedResult<Option<Vec<SessionMessage>>>> + Send>;

/// `provider_factory`: a session provider for one turn id (`adapter.for_turn`).
pub type ProviderFactory = Arc<dyn Fn(&str) -> Arc<dyn SessionProvider> + Send + Sync>;

/// `NarrationStream`: where a retry's narrator emits its tokens.
pub trait NarrationStream: Send {
    /// `emit(channel, token, response_blocks=...)`.
    fn emit<'a>(
        &'a mut self,
        channel: ProseChannel,
        token: &'a str,
        response_blocks: Option<Vec<JsonMap>>,
    ) -> BoxFuture<'a, CodedResult<()>>;
}

/// `narrator(retry_input, stream)`: one manual retry's narration (`adapter.for_retry`).
pub trait RetryNarrator: Send + Sync {
    fn narrate<'a>(
        &'a self,
        input: RetryNarrationInput,
        stream: &'a mut dyn NarrationStream,
    ) -> BoxFuture<'a, Result<(), SessionFailure>>;
}

/// `retry(session_id, retry_id=..., narrator=..., provider_factory=..., interruption_id=...,
/// cancel=..., control=...)`'s keyword arguments.
pub struct RetryRequest {
    pub retry_id: String,
    pub interruption_id: Option<String>,
    pub narrator: Arc<dyn RetryNarrator>,
    pub provider_factory: Option<ProviderFactory>,
    pub cancel: Arc<dyn Fn() + Send + Sync>,
    pub control: Option<Arc<ProviderControl>>,
}

/// `CampaignSessionLoop`.
pub struct CampaignSessionLoop {
    owner: Arc<dyn TurnOwner>,
    #[allow(dead_code)] // T5
    hooks: LoopHooks,
}

/// The method `CampaignSessionLoop` does not answer yet.
fn pending(method: &str) -> CodedError {
    CodedError::new(PORT_PENDING, format!("The Rust owner does not run agent/_session_loop.py {method} yet."))
}

/// `mapping[key]` on a saved record without it: Python's `KeyError`, an internal fault.
fn key_error(key: &str) -> CodedError {
    CodedError::new("internal_error", format!("'{key}'")).with_error_type("KeyError")
}

/// `_active_run(session_id)` with no live run: Python's refusal.
fn no_active_run() -> CodedError {
    CodedError::rejected("Agent session has no active provider work").with_error_type("CommandRejected")
}

fn pending_run(method: &str) -> SessionFailure {
    SessionFailure::Turn(TurnFailure::Coded(pending(method)))
}

impl CampaignSessionLoop {
    /// `CampaignSessionLoop(owner, ...)`.
    pub fn new(owner: Arc<dyn TurnOwner>, hooks: LoopHooks) -> Self {
        Self { owner, hooks }
    }

    /// `run(request, provider)`: one DM turn (or a campaign's opening) to its end.
    pub async fn run(
        &self,
        _request: SessionRunRequest,
        _provider: Arc<dyn SessionProvider>,
    ) -> Result<SessionRunResult, SessionFailure> {
        Err(pending_run("CampaignSessionLoop.run"))
    }

    /// `run_builder(request, provider, persist)`: one Builder turn; the appended messages.
    pub async fn run_builder(
        &self,
        _request: SessionRunRequest,
        _provider: Arc<dyn SessionProvider>,
        _persist: BuilderPersist,
    ) -> Result<Vec<SessionMessage>, SessionFailure> {
        Err(pending_run("CampaignSessionLoop.run_builder"))
    }

    /// `run_compaction(session_id, attempt_id, provider)`: `/compact`.
    pub async fn run_compaction(
        &self,
        _session_id: &str,
        _attempt_id: &str,
        _provider: Arc<dyn SessionProvider>,
    ) -> Result<(), SessionFailure> {
        Err(pending_run("CampaignSessionLoop.run_compaction"))
    }

    /// `retry(session_id, ...)`: one manual narration retry.
    pub async fn retry(
        &self,
        _session_id: &str,
        _request: RetryRequest,
    ) -> Result<ManualRetryResult, SessionFailure> {
        Err(pending_run("CampaignSessionLoop.retry"))
    }

    /// `steer(session_id, text, message_id=...)`: steering needs a live run.
    pub async fn steer(&self, _session_id: &str, _text: &str, _message_id: &str) -> CodedResult<()> {
        Err(no_active_run())
    }

    /// `assert_idle(session_id)`: no run is pending, preparing or active.
    pub fn assert_idle(&self, _session_id: &str) -> CodedResult<()> {
        Ok(())
    }

    /// `stop_agent_session(session_id)`.
    pub async fn stop_agent_session(&self, _session_id: &str) -> CodedResult<SessionRecord> {
        Err(pending("CampaignSessionLoop.stop_agent_session"))
    }

    /// `stop(session_id)`: there is no live run to stop.
    pub async fn stop(&self, _session_id: &str) -> CodedResult<SessionStopResult> {
        Err(no_active_run())
    }

    /// `expire(session_id, now=...)`.
    pub async fn expire(&self, _session_id: &str, _now: f64) -> CodedResult<SessionStopResult> {
        Err(no_active_run())
    }

    /// `builder_activity(session_id, attempt_id=...)`: the saved outcome of a session's latest
    /// (or the given) attempt. With no live run, Python's `active` is `None` and `pending` is
    /// false, so its projection reduces to the saved records read here.
    pub async fn builder_activity(
        &self,
        session_id: &str,
        attempt_id: Option<&str>,
    ) -> CodedResult<Option<JsonMap>> {
        let basis = self.owner.session_activity_basis(session_id, attempt_id).await?;
        let is_builder = basis.session.role() == WriterRole::Builder;
        if !is_builder && !basis.has_compaction {
            return Ok(None);
        }
        let attempt_id = match (attempt_id, &basis.accepted) {
            (Some(attempt_id), _) => attempt_id.to_owned(),
            // `str(basis.accepted.content["attempt_id"])`.
            (None, Some(accepted)) => {
                py_str(accepted.content().get("attempt_id").ok_or_else(|| key_error("attempt_id"))?)
            }
            (None, None) => return Ok(None),
        };
        let status = match (&basis.terminal, &basis.accepted) {
            (Some(terminal), _) => {
                terminal.content().get("status").cloned().ok_or_else(|| key_error("status"))?
            }
            (None, None) => Json::from("unavailable"),
            (None, Some(_)) => Json::from("interrupted"),
        };
        let mut activity = JsonMap::new();
        activity.insert("attempt_id".into(), Json::String(attempt_id));
        activity.insert("status".into(), status);
        Ok(Some(activity))
    }

    /// `activity_progress(session_id)`: live observations, and there is no live run.
    pub async fn activity_progress(&self, _session_id: &str) -> CodedResult<Option<JsonMap>> {
        Ok(None)
    }

    /// `context_status_hint(session_id)`: no live run has a hint yet.
    pub fn context_status_hint(&self, _session_id: &str) -> Option<String> {
        None
    }

    /// `provisional_session_projection(session_id)`: no live run has provisional output yet.
    pub fn provisional_session_projection(&self, _session_id: &str) -> Vec<JsonMap> {
        Vec::new()
    }

    /// `disconnect(session_id, now=...)`.
    pub async fn disconnect(&self, _session_id: &str, _now: f64) -> CodedResult<f64> {
        Err(no_active_run())
    }

    /// `reconnect(session_id, now=..., acknowledged_sequence=...)`.
    pub async fn reconnect(
        &self,
        _session_id: &str,
        _now: f64,
        _acknowledged_sequence: i64,
    ) -> CodedResult<CanonicalReplay> {
        Err(no_active_run())
    }
}
