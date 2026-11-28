//! `agent/_session_loop.py`'s `CampaignSessionLoop`: the session loop the owner's session
//! commands run turns through (lane 5's `commands/sessions.rs`, lane 1's Builder submit).
//!
//! The loop is built once per owner process and held as `Arc<CampaignSessionLoop>`.
//!
//! - **The DM run** (T5a-2): `run` admits one DM input (or a campaign's opening) and drives its
//!   provider. Each event goes to the owner as Python's loop takes it (private records,
//!   narration tokens, mechanics through the stateless MCP client, evidence). The narration
//!   attempt then completes, or is interrupted with its terminal saved.
//! - **The live calls on a run:** `steer`, `stop`, `stop_agent_session`, `expire`, `disconnect`,
//!   `reconnect`, and the idle, hint, progress and provisional reads, over the same active-run
//!   registry Python keeps.
//! - **The Builder run and compaction** (T5b): `run_builder` drives a Builder chat's provider
//!   with provisional prose, private records and its terminal; `run_compaction` runs `/compact`,
//!   admitting only working-context checkpoints.
//! - **Retry** (T6a): `retry` runs the owner's `manual_retry` with the loop's narrator: a
//!   narration-only Retry goes to `for_retry`, one that still needs tools runs the turn's provider
//!   again from its basis. A failed Retry records its own terminal.

use super::{
    ProviderControl, ProviderEvent, SessionEvents, SessionFailure, SessionInputKind, SessionProvider,
    SessionProviderError, SessionProviderInput, SessionRunRequest, SessionRunResult, SessionStopResult,
};
use crate::provider_session::reads::{ManualRetryCall, NarrationAttemptStart, RetryRefusal, TurnOwner};
use crate::provider_turn::TurnFailure;
use crate::stateless_mcp::{self, AuthorityFailure};
use serde_json::{json, Value as Json};
use std::collections::{HashMap, HashSet};
use std::sync::{Arc, Mutex, MutexGuard, PoisonError};
use sylvia_app::authority::actions::ActionExecution;
use sylvia_base::{CodedError, CodedResult, JsonMap};
use sylvia_domain::authority::{
    CanonicalEvent, CanonicalReplay, ManualRetryResult, MechanicsExecutionReceipt, NarrationInterruption,
    ProseChannel, RetryNarrationInput, WriterRole,
};
use sylvia_domain::provider_records::ProviderPrivateRecord;
use sylvia_domain::pyops::py_str;
use sylvia_domain::session::{SessionContext, SessionMessage, SessionMessageKind, SessionRecord};
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

/// `_ActiveRun`: one admitted run's identity, control and provisional prose.
struct ActiveRun {
    campaign_id: String,
    attempt_id: String,
    control: Arc<ProviderControl>,
    basis_message_id: Option<String>,
    provisional: Mutex<Provisional>,
}

/// `_provisional` and `_segment_ordinal`, under `_projection_lock`.
#[derive(Default)]
struct Provisional {
    items: Vec<JsonMap>,
    segment_ordinal: i64,
}

impl ActiveRun {
    fn provisional(&self) -> MutexGuard<'_, Provisional> {
        self.provisional.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// `take_completed_provisional()`: each non-empty provisional segment with its identity.
    fn take_completed_provisional(&self) -> CodedResult<Vec<(String, SessionMessageKind, JsonMap)>> {
        let mut provisional = self.provisional();
        let items = std::mem::take(&mut provisional.items);
        let mut completed = Vec::new();
        for projection in items {
            let text = projection.get("text").map_or_else(String::new, py_str);
            if text.is_empty() {
                continue;
            }
            provisional.segment_ordinal += 1;
            let raw_kind = projection.get("kind").map_or_else(|| "None".to_owned(), py_str);
            let kind = SessionMessageKind::parse(&raw_kind).ok_or_else(|| {
                CodedError::new("internal_error", format!("'{raw_kind}' is not a valid SessionMessageKind"))
                    .with_error_type("ValueError")
            })?;
            let mut content = JsonMap::new();
            content.insert("text".into(), text.into());
            completed.push((
                format!("{}-segment-{}", self.attempt_id, provisional.segment_ordinal),
                kind,
                content,
            ));
        }
        Ok(completed)
    }
}

/// The loop's registry: `_active`, `_pending`, `_preparing`, `_stop_requested` and
/// `_last_interruption`, under `_active_lock`.
#[derive(Default)]
struct LoopState {
    active: HashMap<String, Arc<ActiveRun>>,
    pending: HashSet<String>,
    preparing: HashSet<String>,
    stop_requested: HashSet<String>,
    last_interruption: HashMap<String, NarrationInterruption>,
}

/// `CampaignSessionLoop`.
pub struct CampaignSessionLoop {
    owner: Arc<dyn TurnOwner>,
    hooks: LoopHooks,
    state: Mutex<LoopState>,
    /// `uuid4().hex` for the stateless MCP client's identities.
    ids: Arc<dyn Fn() -> String + Send + Sync>,
}

/// `mapping[key]` on a saved record without it: Python's `KeyError`, an internal fault.
fn key_error(key: &str) -> CodedError {
    CodedError::new("internal_error", format!("'{key}'")).with_error_type("KeyError")
}

fn rejected(message: &str) -> CodedError {
    CodedError::rejected(message).with_error_type("CommandRejected")
}

fn type_error(message: &str) -> CodedError {
    CodedError::new("internal_error", message).with_error_type("TypeError")
}

/// `_active_run(session_id)` with no live run: Python's refusal.
fn no_active_run() -> CodedError {
    rejected("Agent session has no active provider work")
}

fn failure(error: CodedError) -> SessionFailure {
    SessionFailure::Turn(TurnFailure::Coded(error))
}

/// An owner refusal raised to the caller.
fn owner_failure(error: AuthorityFailure) -> SessionFailure {
    failure(error.into_coded())
}

/// A JSON object from its pairs, in order.
fn object(pairs: Vec<(&str, Json)>) -> JsonMap {
    pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect()
}

/// `message.content.get(key) == value`.
fn content_is(message: &SessionMessage, key: &str, value: &str) -> bool {
    message.content().get(key).and_then(Json::as_str) == Some(value)
}

/// Whether Python's `isinstance(error, CommandRejected)` holds (subclasses included).
fn is_command_rejected(error: &CodedError) -> bool {
    AuthorityFailure::Coded(error.clone()).is_command_rejected()
}

/// `resolve_retry_basis_message(context, failed_attempt_id)`: the one durable player input or
/// opening message a retry chain resolves to.
pub fn resolve_retry_basis_message(
    context: &SessionContext,
    failed_attempt_id: &str,
) -> CodedResult<SessionMessage> {
    let ambiguous = || rejected("Retry input identity is unavailable or ambiguous");
    let is_input = |message: &SessionMessage| {
        matches!(message.kind(), SessionMessageKind::User | SessionMessageKind::Evidence)
            && !matches!(
                message.content().get("kind").and_then(Json::as_str),
                Some("session_attempt_terminal" | "player_action_response_retry")
            )
    };
    let direct: Vec<&SessionMessage> = context
        .messages
        .iter()
        .filter(|message| content_is(message, "attempt_id", failed_attempt_id) && is_input(message))
        .collect();
    if direct.len() == 1 {
        return Ok(direct[0].clone());
    }
    if !direct.is_empty() {
        return Err(ambiguous());
    }
    let terminals: Vec<&SessionMessage> = context
        .messages
        .iter()
        .filter(|message| {
            message.kind() == SessionMessageKind::Evidence
                && content_is(message, "kind", "session_attempt_terminal")
                && content_is(message, "attempt_id", failed_attempt_id)
        })
        .collect();
    if terminals.len() != 1 {
        return Err(ambiguous());
    }
    let Some(original_id) =
        terminals[0].content().get("message_id").and_then(Json::as_str).filter(|id| !id.is_empty())
    else {
        return Err(ambiguous());
    };
    let original: Vec<&SessionMessage> = context
        .messages
        .iter()
        .filter(|message| message.message_id() == original_id && is_input(message))
        .collect();
    if original.len() != 1 {
        return Err(ambiguous());
    }
    Ok(original[0].clone())
}

/// `_pending_campaign_file_checkpoint(context, basis_message_id=...)`: held prose of this input
/// that still needs its GM-screen write.
fn pending_campaign_file_checkpoint(context: &SessionContext, basis_message_id: &str) -> bool {
    let latest_held = context
        .messages
        .iter()
        .filter(|message| {
            message.kind() == SessionMessageKind::Evidence
                && content_is(message, "kind", "provider_held_narration")
                && content_is(message, "basis_message_id", basis_message_id)
        })
        .map(SessionMessage::sequence)
        .max();
    let Some(latest_held) = latest_held else { return false };
    !context
        .messages
        .iter()
        .any(|message| message.kind() == SessionMessageKind::Talk && message.sequence() > latest_held)
}

/// `resolve_interruption_basis(context, interruption)`: the accepted replacement input, or the
/// failed attempt's own.
pub fn resolve_interruption_basis(
    context: &SessionContext,
    interruption: &NarrationInterruption,
) -> CodedResult<SessionMessage> {
    let replacement = context.messages.iter().find(|message| {
        message.kind() == SessionMessageKind::User
            // `content.get("attempt_id") == interruption.resolving_retry_id`: a missing id equals `None`.
            && message.content().get("attempt_id").cloned().unwrap_or(Json::Null) == json!(interruption.resolving_retry_id)
            && content_is(message, "redirect_interruption_id", &interruption.interruption_id)
    });
    match replacement {
        Some(message) => Ok(message.clone()),
        None => resolve_retry_basis_message(context, &interruption.failed_attempt_id),
    }
}

/// `_recovery_kind(context, basis, interruption)`: the phase an interrupted GM turn resumes from.
fn recovery_kind(
    context: &SessionContext,
    basis: &SessionMessage,
    interruption: &NarrationInterruption,
) -> &'static str {
    if pending_campaign_file_checkpoint(context, basis.message_id()) {
        "campaign_file_checkpoint"
    } else if interruption.last_action_id.is_some() {
        "post_mechanics_narration"
    } else if content_is(basis, "kind", "campaign_start") {
        "opening"
    } else {
        "uncommitted_player_action"
    }
}

/// `_failure_category(error, cancelled=...)`: one safe stable category for a recoverable
/// terminal attempt.
fn failure_category(error: &SessionFailure, cancelled: bool) -> String {
    if cancelled {
        return "user_cancelled".into();
    }
    match error {
        SessionFailure::Turn(TurnFailure::Provider(error)) => error.category().as_str().into(),
        SessionFailure::Turn(TurnFailure::Coded(error)) if is_command_rejected(error) => error.code.clone(),
        _ => "local_adapter_error".into(),
    }
}

/// `"Application authority owner is closed"`: the owner's `RuntimeError` once it has shut down.
fn owner_closed(error: &CodedError) -> bool {
    error.message == "Application authority owner is closed"
}

/// The refusal a cancelled run's unwind answers when the owner shut down under it.
fn cancelled_during_shutdown() -> SessionFailure {
    failure(rejected("Agent session provider work was cancelled during owner shutdown"))
}

/// The identities an attempt's records carry: from a run's request, or a Retry's.
#[derive(Clone, Copy)]
struct AttemptIds<'a> {
    session_id: &'a str,
    attempt_id: &'a str,
    basis_message_id: &'a str,
    turn_id: &'a str,
}

impl SessionRunRequest {
    fn ids(&self) -> AttemptIds<'_> {
        AttemptIds {
            session_id: &self.session_id,
            attempt_id: &self.attempt_id,
            basis_message_id: &self.message_id,
            turn_id: &self.turn_id,
        }
    }
}

/// Clears the run's reservation (`_pending`, `_stop_requested`) when `run` ends.
struct Reservation<'a> {
    state: &'a Mutex<LoopState>,
    session_id: String,
}

impl Drop for Reservation<'_> {
    fn drop(&mut self) {
        let mut state = self.state.lock().unwrap_or_else(PoisonError::into_inner);
        state.pending.remove(&self.session_id);
        state.stop_requested.remove(&self.session_id);
    }
}

/// What the provider loop's events ended with, beside the provider's own outcome.
#[derive(Default)]
struct Outcome {
    receipts: Vec<MechanicsExecutionReceipt>,
    /// A mechanic awaiting a choice ends the run there, with this result.
    early: Option<SessionRunResult>,
}

/// `_run_dm_provider`'s `for source_order, event in enumerate(stream)` body, as the provider's
/// event sink.
struct DmEvents<'a> {
    run: &'a CampaignSessionLoop,
    request: &'a SessionRunRequest,
    active: &'a ActiveRun,
    provider_id: String,
    model_id: String,
    initial_high_water: i64,
    source_order: i64,
    outcome: Outcome,
}

impl DmEvents<'_> {
    async fn handle(&mut self, event: ProviderEvent, source_order: i64) -> Result<(), SessionFailure> {
        let owner = self.run.owner.as_ref();
        let (request, active) = (self.request, self.active);
        active.control.progress.observe();
        if active.control.cancelled() {
            return Err(failure(rejected("Agent session provider work was cancelled")));
        }
        match event {
            ProviderEvent::PrivateReasoning { text, response_id } => {
                self.private_record(text, response_id, "thinking", source_order).await
            }
            ProviderEvent::PrivateContinuation { text, response_id } => {
                self.private_record(text, Some(response_id), "continuation", source_order).await
            }
            ProviderEvent::SafeThink { text } => owner
                .emit_narration_token(
                    &active.campaign_id,
                    &active.attempt_id,
                    ProseChannel::SafeThink,
                    &text,
                    None,
                )
                .await
                .map_err(owner_failure),
            ProviderEvent::Talk { text, response_blocks, .. } => {
                let blocks =
                    response_blocks.map(|blocks| Json::Array(blocks.into_iter().map(Json::Object).collect()));
                owner
                    .emit_narration_token(
                        &active.campaign_id,
                        &active.attempt_id,
                        ProseChannel::Talk,
                        &text,
                        blocks.as_ref(),
                    )
                    .await
                    .map_err(owner_failure)
            }
            ProviderEvent::Action { command, feedback } => {
                owner
                    .flush_narration(&active.campaign_id, &active.attempt_id)
                    .await
                    .map_err(owner_failure)?;
                let receipt = match stateless_mcp::execute::execute_action(
                    owner,
                    &command,
                    self.run.ids.as_ref(),
                )
                .await
                {
                    Ok(receipt) => receipt,
                    Err(AuthorityFailure::Domain(error)) if error.mechanics().is_some() => {
                        let Some(feedback) = feedback else {
                            return Err(owner_failure(AuthorityFailure::Domain(error)));
                        };
                        let details = error.mechanics().expect("checked above");
                        let rejection = crate::session_loop::mechanics_rejection_feedback(
                            error.code().unwrap_or_default(),
                            error.message(),
                            details,
                        );
                        feedback.set_rejection(Some(rejection));
                        return Ok(());
                    }
                    Err(error) => return Err(owner_failure(error)),
                };
                match receipt {
                    ActionExecution::AwaitingChoice(receipt) => {
                        self.outcome.receipts.push(MechanicsExecutionReceipt::AwaitingChoice(receipt));
                        owner
                            .complete_narration_attempt(&active.campaign_id, &active.attempt_id)
                            .await
                            .map_err(owner_failure)?;
                        self.run
                            .record_gm_turn_activity(
                                request.ids(),
                                request.input_kind == SessionInputKind::CampaignStart,
                            )
                            .await?;
                        let canonical =
                            self.run.canonical_since(&active.campaign_id, self.initial_high_water).await?;
                        owner
                            .append_session_canonical_references(&request.session_id, &canonical)
                            .await
                            .map_err(owner_failure)?;
                        self.outcome.early = Some(SessionRunResult {
                            canonical,
                            receipts: std::mem::take(&mut self.outcome.receipts),
                            interruption: None,
                        });
                        Ok(())
                    }
                    ActionExecution::Receipt(receipt) => {
                        let action_id = receipt.action_id.clone();
                        self.outcome.receipts.push(MechanicsExecutionReceipt::Action(receipt));
                        owner
                            .bind_narration_action(&active.campaign_id, &active.attempt_id, &action_id)
                            .await
                            .map_err(owner_failure)?;
                        self.run.record_mechanics_activity(request.ids(), source_order, &action_id).await
                    }
                }
            }
            ProviderEvent::Evidence { message_id, payload } => {
                let mut content = payload;
                let activity = object(vec![
                    ("attempt_id", json!(request.attempt_id)),
                    ("basis_message_id", json!(request.message_id)),
                    ("source_order", json!(source_order)),
                ]);
                content.insert("activity".into(), Json::Object(activity));
                owner
                    .append_agent_message(
                        &request.session_id,
                        &message_id,
                        SessionMessageKind::Evidence,
                        &content,
                    )
                    .await
                    .map_err(failure)?;
                Ok(())
            }
            ProviderEvent::ProseComplete => Err(failure(type_error("Unknown provider event"))),
        }
    }

    /// A private reasoning or continuation record, kept apart from the session's messages.
    async fn private_record(
        &self,
        text: String,
        response_id: Option<String>,
        kind: &str,
        source_order: i64,
    ) -> Result<(), SessionFailure> {
        if text.is_empty() {
            return Ok(());
        }
        let request = self.request;
        let record = ProviderPrivateRecord {
            session_id: request.session_id.clone(),
            attempt_id: request.attempt_id.clone(),
            response_id: response_id
                .filter(|id| !id.is_empty())
                .unwrap_or_else(|| request.attempt_id.clone()),
            turn_id: request.turn_id.clone(),
            basis_message_id: request.message_id.clone(),
            source_order,
            provider_id: self.provider_id.clone(),
            model_id: self.model_id.clone(),
            kind: kind.to_owned(),
            payload: text,
        };
        self.run.owner.append_private_provider_record(&record).await.map_err(owner_failure)
    }
}

impl SessionEvents for DmEvents<'_> {
    fn emit(&mut self, event: ProviderEvent) -> BoxFuture<'_, Result<(), SessionProviderError>> {
        Box::pin(async move {
            let source_order = self.source_order;
            self.source_order += 1;
            match self.handle(event, source_order).await {
                // A run that ended at an awaited choice stops its provider there.
                Ok(()) if self.outcome.early.is_some() => Err(SessionProviderError {
                    error: failure(rejected("The run ended at a choice")),
                    loop_reason: None,
                }),
                Ok(()) => Ok(()),
                Err(error) => Err(SessionProviderError { error, loop_reason: None }),
            }
        })
    }
}

/// `_MAX_PROVISIONAL_SESSION_CHARS`.
const MAX_PROVISIONAL_SESSION_CHARS: usize = 256_000;

impl ActiveRun {
    /// `emit_provisional(kind, text)`: safe prose held until it completes.
    fn emit_provisional(&self, kind: SessionMessageKind, text: &str) -> CodedResult<()> {
        if !matches!(kind, SessionMessageKind::SafeThink | SessionMessageKind::Talk) {
            return Err(type_error("Only safe prose can enter a provisional session projection"));
        }
        let mut provisional = self.provisional();
        let used: usize = provisional
            .items
            .iter()
            .map(|item| item.get("text").map_or_else(String::new, py_str).chars().count())
            .sum();
        if used + text.chars().count() > MAX_PROVISIONAL_SESSION_CHARS {
            return Err(rejected("Agent session provisional output is too large"));
        }
        let kind_value = kind.as_str();
        if let Some(current) = provisional.items.last_mut() {
            if current.get("kind").and_then(Json::as_str) == Some(kind_value) {
                let joined = current.get("text").map_or_else(String::new, py_str) + text;
                current.insert("text".into(), joined.into());
                return Ok(());
            }
        }
        provisional.items.push(object(vec![
            ("kind", json!(kind_value)),
            ("text", json!(text)),
            ("collapsed_by_default", json!(kind == SessionMessageKind::SafeThink)),
            ("provisional", json!(true)),
            ("attempt_id", json!(self.attempt_id)),
        ]));
        Ok(())
    }

    /// `provisional_kind()`: the kind of the latest provisional segment.
    fn provisional_kind(&self) -> Option<String> {
        self.provisional().items.last().map(|item| item.get("kind").map_or_else(|| "None".to_owned(), py_str))
    }
}

/// A coded refusal as the provider loop's error.
fn provider_failure(error: CodedError) -> SessionProviderError {
    error.into()
}

/// `_run_builder_with_session_work`'s event loop, as the Builder provider's event sink.
struct BuilderEvents<'a> {
    run: &'a CampaignSessionLoop,
    request: &'a SessionRunRequest,
    active: &'a ActiveRun,
    provider_id: String,
    model_id: String,
    source_order: i64,
    appended: Vec<SessionMessage>,
    provisional_response_id: Option<String>,
    native_requirements: HashMap<String, JsonMap>,
}

impl BuilderEvents<'_> {
    /// `flush_provisional()`: each completed provisional segment saved, a TALK with its
    /// response and native requirements.
    async fn flush_provisional(&mut self) -> Result<(), SessionFailure> {
        for (message_id, kind, mut content) in self.active.take_completed_provisional().map_err(failure)? {
            if kind == SessionMessageKind::Talk {
                if let Some(response_id) = self.provisional_response_id.clone().filter(|id| !id.is_empty()) {
                    content.insert("provider_response_id".into(), json!(response_id));
                    if let Some(requirement) = self.native_requirements.get(&response_id) {
                        content.insert("native_continuation".into(), Json::Object(requirement.clone()));
                    }
                }
            }
            let message = self
                .run
                .owner
                .append_agent_message(&self.request.session_id, &message_id, kind, &content)
                .await
                .map_err(failure)?;
            self.appended.push(message);
        }
        Ok(())
    }

    /// `record_terminal(status, reason_code=...)`.
    async fn record_terminal(&self, status: &str, reason_code: Option<&str>) -> Result<(), SessionFailure> {
        let (owner, request) = (self.run.owner.as_ref(), self.request);
        owner
            .finish_private_provider_attempt(&request.session_id, &request.attempt_id, status == "completed")
            .await
            .map_err(owner_failure)?;
        let timing_status = if status == "interrupted" { "failed" } else { status };
        let timing =
            self.run.finish_activity(&request.session_id, &request.attempt_id, timing_status, None)?;
        let mut content = object(vec![
            ("kind", json!("builder_attempt_terminal")),
            ("attempt_id", json!(request.attempt_id)),
            ("status", json!(status)),
            ("activity_timing", Json::Object(timing)),
        ]);
        if let Some(reason) = reason_code.filter(|reason| !reason.is_empty()) {
            content.insert("reason_code".into(), json!(reason));
        }
        let message_id = format!("builder-terminal-{}", request.attempt_id);
        owner
            .append_agent_message(&request.session_id, &message_id, SessionMessageKind::Evidence, &content)
            .await
            .map_err(failure)?;
        Ok(())
    }

    async fn handle(&mut self, event: ProviderEvent, source_order: i64) -> Result<(), SessionFailure> {
        let (request, active) = (self.request, self.active);
        active.control.progress.observe();
        let private = matches!(
            event,
            ProviderEvent::PrivateReasoning { .. } | ProviderEvent::PrivateContinuation { .. }
        );
        if active.control.cancelled() && !private && !matches!(event, ProviderEvent::Evidence { .. }) {
            // Stop fences prose; completed tool receipts and their native continuation still
            // describe effects that already happened.
            return Ok(());
        }
        match event {
            ProviderEvent::PrivateReasoning { text, response_id } => {
                self.private_record(text, response_id, "thinking", source_order).await
            }
            ProviderEvent::PrivateContinuation { text, response_id } => {
                self.private_record(text.clone(), Some(response_id.clone()), "continuation", source_order)
                    .await?;
                if !text.is_empty() && !response_id.is_empty() {
                    let native = sylvia_base::pyjson::loads(&text).map_err(|error| {
                        failure(
                            CodedError::new("internal_error", error.to_string())
                                .with_error_type("JSONDecodeError"),
                        )
                    })?;
                    let has = |key: &str| native.as_object().is_some_and(|native| native.contains_key(key));
                    let requirement = object(vec![
                        ("provider_id", json!(self.provider_id)),
                        ("model_id", json!(self.model_id)),
                        ("anthropic_required", json!(has("anthropic_content"))),
                        ("chat_completion_required", json!(has("chat_completion_metadata"))),
                        ("reasoning_required", json!(has("reasoning_content"))),
                    ]);
                    self.native_requirements.insert(response_id, requirement);
                }
                Ok(())
            }
            ProviderEvent::SafeThink { text } => {
                if !matches!(self.active.provisional_kind().as_deref(), None | Some("safe_think")) {
                    self.flush_provisional().await?;
                }
                active.emit_provisional(SessionMessageKind::SafeThink, &text).map_err(failure)
            }
            ProviderEvent::Talk { text, response_id, .. } => {
                if self.provisional_response_id != response_id {
                    self.flush_provisional().await?;
                }
                self.provisional_response_id = response_id;
                if !matches!(self.active.provisional_kind().as_deref(), None | Some("talk")) {
                    self.flush_provisional().await?;
                }
                active.emit_provisional(SessionMessageKind::Talk, &text).map_err(failure)
            }
            ProviderEvent::ProseComplete => self.flush_provisional().await,
            ProviderEvent::Evidence { message_id, payload } => {
                if !active.control.cancelled() {
                    self.flush_provisional().await?;
                }
                // `native_requirements.get(str(response_id))`.
                let response_id =
                    payload.get("provider_response_id").map_or_else(|| "None".to_owned(), py_str);
                let requirement = self.native_requirements.get(&response_id).cloned();
                let mut content = payload;
                if let Some(requirement) = requirement {
                    content.insert("native_continuation".into(), Json::Object(requirement));
                }
                let activity = object(vec![
                    ("attempt_id", json!(request.attempt_id)),
                    ("basis_message_id", json!(request.message_id)),
                    ("source_order", json!(source_order)),
                ]);
                content.insert("activity".into(), Json::Object(activity));
                let message = self
                    .run
                    .owner
                    .append_agent_message(
                        &request.session_id,
                        &message_id,
                        SessionMessageKind::Evidence,
                        &content,
                    )
                    .await
                    .map_err(failure)?;
                self.appended.push(message);
                Ok(())
            }
            ProviderEvent::Action { .. } => {
                Err(failure(rejected("Builder provider cannot emit gameplay session events")))
            }
        }
    }

    /// A private reasoning or continuation record.
    async fn private_record(
        &self,
        text: String,
        response_id: Option<String>,
        kind: &str,
        source_order: i64,
    ) -> Result<(), SessionFailure> {
        if text.is_empty() {
            return Ok(());
        }
        let request = self.request;
        let record = ProviderPrivateRecord {
            session_id: request.session_id.clone(),
            attempt_id: request.attempt_id.clone(),
            response_id: response_id
                .filter(|id| !id.is_empty())
                .unwrap_or_else(|| request.attempt_id.clone()),
            turn_id: request.turn_id.clone(),
            basis_message_id: request.message_id.clone(),
            source_order,
            provider_id: self.provider_id.clone(),
            model_id: self.model_id.clone(),
            kind: kind.to_owned(),
            payload: text,
        };
        self.run.owner.append_private_provider_record(&record).await.map_err(owner_failure)
    }
}

impl SessionEvents for BuilderEvents<'_> {
    fn emit(&mut self, event: ProviderEvent) -> BoxFuture<'_, Result<(), SessionProviderError>> {
        Box::pin(async move {
            let source_order = self.source_order;
            self.source_order += 1;
            self.handle(event, source_order)
                .await
                .map_err(|error| SessionProviderError { error, loop_reason: None })
        })
    }
}

/// `run_compaction`'s event loop: only working-context checkpoints are admitted.
struct CompactionEvents<'a> {
    owner: &'a dyn TurnOwner,
    session_id: &'a str,
    control: &'a ProviderControl,
}

impl SessionEvents for CompactionEvents<'_> {
    fn emit(&mut self, event: ProviderEvent) -> BoxFuture<'_, Result<(), SessionProviderError>> {
        Box::pin(async move {
            if self.control.cancelled() {
                return Err(provider_failure(rejected("Context compaction was cancelled")));
            }
            let ProviderEvent::Evidence { message_id, payload } = event else {
                return Err(provider_failure(rejected("Compaction cannot perform gameplay or narrate")));
            };
            if payload.get("kind").and_then(Json::as_str) != Some("working_context_checkpoint") {
                return Err(provider_failure(rejected("Compaction cannot perform gameplay or narrate")));
            }
            self.owner
                .append_agent_message(self.session_id, &message_id, SessionMessageKind::Evidence, &payload)
                .await
                .map_err(provider_failure)?;
            Ok(())
        })
    }
}

/// What `retry`'s failure path reads of the attempt it got to.
#[derive(Default)]
struct RetryProgress {
    campaign_id: Option<String>,
    basis: Option<SessionMessage>,
    initial_high_water: i64,
    narration_started: bool,
}

/// `_terminal_recovery_kind(context, failed_attempt_id)`: the one persisted terminal phase, if
/// exactly one terminal names the attempt.
fn terminal_recovery_kind(context: &SessionContext, failed_attempt_id: &str) -> Option<String> {
    let terminals: Vec<&SessionMessage> = context
        .messages
        .iter()
        .filter(|message| {
            message.kind() == SessionMessageKind::Evidence
                && content_is(message, "kind", "session_attempt_terminal")
                && content_is(message, "attempt_id", failed_attempt_id)
        })
        .collect();
    if terminals.len() != 1 {
        return None;
    }
    terminals[0]
        .content()
        .get("recovery_kind")
        .and_then(Json::as_str)
        .filter(|kind| !kind.is_empty())
        .map(str::to_owned)
}

/// `retry`'s `run_retry` and `observe_retry_failure`: the narrator the owner's `manual_retry`
/// runs. A narration-only Retry goes to `for_retry`'s narrator; one that still needs tools runs
/// the tool-capable provider from the basis.
struct LoopRetry<'a> {
    run: &'a CampaignSessionLoop,
    session_id: &'a str,
    campaign_id: &'a str,
    retry_id: &'a str,
    context: &'a SessionContext,
    basis: &'a SessionMessage,
    provider_factory: Option<ProviderFactory>,
    narrator: Arc<dyn RetryNarrator>,
    control: Arc<ProviderControl>,
    /// `retry_failure_cancelled`: whether cancellation caused the narrator's failure.
    failure_cancelled: &'a Mutex<Option<bool>>,
}

impl crate::provider_session::reads::LoopRetryNarration for LoopRetry<'_> {
    fn narrate<'a>(
        &'a mut self,
        input: RetryNarrationInput,
        stream: &'a mut dyn NarrationStream,
    ) -> BoxFuture<'a, Result<(), SessionFailure>> {
        Box::pin(async move {
            let outcome = self.run_retry(input, stream).await;
            if outcome.is_err() {
                *self.failure_cancelled.lock().unwrap_or_else(PoisonError::into_inner) =
                    Some(self.control.cancelled());
            }
            outcome
        })
    }
}

impl LoopRetry<'_> {
    /// `run_retry(retry_input, stream)`.
    async fn run_retry(
        &self,
        input: RetryNarrationInput,
        stream: &mut dyn NarrationStream,
    ) -> Result<(), SessionFailure> {
        let owner = self.run.owner.as_ref();
        let basis = self.basis;
        if basis.kind() == SessionMessageKind::Evidence && content_is(basis, "kind", "player_action_response")
        {
            let content = object(vec![
                ("kind", json!("player_action_response_retry")),
                ("basis_message_id", json!(basis.message_id())),
                ("attempt_id", json!(self.retry_id)),
            ]);
            owner
                .append_agent_message(
                    self.session_id,
                    &format!("player-response-retry-{}", self.retry_id),
                    SessionMessageKind::Evidence,
                    &content,
                )
                .await
                .map_err(failure)?;
        }
        let recovery_kind = terminal_recovery_kind(self.context, &input.failed_attempt_id);
        let checkpoint = recovery_kind.as_deref() == Some("campaign_file_checkpoint");
        let uncommitted = input.action_receipt.is_none() && basis.kind() == SessionMessageKind::User;
        let post_mechanics =
            recovery_kind.as_deref() == Some("post_mechanics_narration") && input.action_receipt.is_some();
        if !uncommitted && !checkpoint && !post_mechanics {
            return self.narrator.narrate(input, stream).await;
        }
        let Some(factory) = self.provider_factory.clone() else {
            if checkpoint {
                return Err(failure(rejected(
                    "Campaign File checkpoint Retry requires the tool-capable provider",
                )));
            }
            return self.narrator.narrate(input, stream).await;
        };
        let messages = &self.context.messages;
        let retry_messages: Vec<SessionMessage> = if checkpoint {
            messages.clone()
        } else {
            // `context.messages.index(basis_message)`: the first equal message.
            let index = messages.iter().position(|message| message == basis).ok_or_else(|| {
                failure(
                    CodedError::new("internal_error", "SessionMessage is not in list")
                        .with_error_type("ValueError"),
                )
            })?;
            let mut kept: Vec<SessionMessage> = messages[..=index].to_vec();
            kept.extend(
                messages[index + 1..]
                    .iter()
                    .filter(|message| {
                        message.kind() == SessionMessageKind::Evidence
                            && (content_is(message, "kind", "gm_system_contract")
                                || (matches!(
                                    message.content().get("kind").and_then(Json::as_str),
                                    Some("provider_tool_calls" | "provider_tool_results")
                                ) && content_is(message, "basis_message_id", basis.message_id())))
                    })
                    .cloned(),
            );
            kept
        };
        let retry_context =
            SessionContext { session: self.context.session.clone(), messages: retry_messages };
        let mut provider_input = SessionProviderInput::new(
            self.context.session.clone(),
            retry_context,
            Vec::new(),
            Arc::clone(&self.control),
        );
        provider_input.basis_message_id = Some(basis.message_id().to_owned());
        provider_input.recovery_kind = recovery_kind;
        provider_input.retry_action_receipt = input.action_receipt.clone();
        let ids = AttemptIds {
            session_id: self.session_id,
            attempt_id: self.retry_id,
            basis_message_id: basis.message_id(),
            turn_id: &input.turn_id,
        };
        self.run.install_callbacks(&mut provider_input, self.campaign_id, ids);
        let provider = factory(&input.turn_id);
        self.control.admit(|| ()).map_err(failure)?;
        let fields = provider_input.session.fields();
        let mut events = RetryEvents {
            run: self.run,
            ids,
            campaign_id: self.campaign_id,
            control: &self.control,
            stream,
            provider_id: fields.provider_id.clone(),
            model_id: fields.model_id.clone(),
            source_order: 0,
            ended: false,
        };
        let ran = provider.run(provider_input, &mut events).await;
        if events.ended {
            // An awaited choice ends the Retry: the provider was stopped there.
            return Ok(());
        }
        ran.map_err(|error| error.error)
    }
}

/// `run_retry`'s provider event loop: prose goes to the Retry's stream, mechanics and evidence
/// to the owner, as the DM run takes them.
struct RetryEvents<'a> {
    run: &'a CampaignSessionLoop,
    ids: AttemptIds<'a>,
    campaign_id: &'a str,
    control: &'a ProviderControl,
    stream: &'a mut dyn NarrationStream,
    provider_id: String,
    model_id: String,
    source_order: i64,
    /// A mechanic awaiting a choice ended the Retry with its prompt.
    ended: bool,
}

impl RetryEvents<'_> {
    async fn handle(&mut self, event: ProviderEvent, source_order: i64) -> Result<(), SessionFailure> {
        let owner = self.run.owner.as_ref();
        let ids = self.ids;
        self.control.progress.observe();
        if self.control.cancelled() {
            return Err(failure(rejected("Agent session provider work was cancelled")));
        }
        match event {
            ProviderEvent::PrivateReasoning { text, response_id } => {
                self.private_record(text, response_id, "thinking", source_order).await
            }
            ProviderEvent::PrivateContinuation { text, response_id } => {
                self.private_record(text, Some(response_id), "continuation", source_order).await
            }
            ProviderEvent::SafeThink { text } => {
                self.stream.emit(ProseChannel::SafeThink, &text, None).await.map_err(failure)
            }
            ProviderEvent::Talk { text, response_blocks, .. } => {
                self.stream.emit(ProseChannel::Talk, &text, response_blocks).await.map_err(failure)
            }
            ProviderEvent::Action { command, feedback } => {
                owner.flush_narration(self.campaign_id, ids.attempt_id).await.map_err(owner_failure)?;
                let receipt = match stateless_mcp::execute::execute_action(
                    owner,
                    &command,
                    self.run.ids.as_ref(),
                )
                .await
                {
                    Ok(receipt) => receipt,
                    Err(AuthorityFailure::Domain(error)) if error.mechanics().is_some() => {
                        let Some(feedback) = feedback else {
                            return Err(owner_failure(AuthorityFailure::Domain(error)));
                        };
                        let details = error.mechanics().expect("checked above");
                        let rejection = crate::session_loop::mechanics_rejection_feedback(
                            error.code().unwrap_or_default(),
                            error.message(),
                            details,
                        );
                        feedback.set_rejection(Some(rejection));
                        return Ok(());
                    }
                    Err(error) => return Err(owner_failure(error)),
                };
                match receipt {
                    // A Retry can succeed by presenting an uncommitted player choice.
                    ActionExecution::AwaitingChoice(receipt) => {
                        self.stream
                            .emit(ProseChannel::Talk, &receipt.choice.prompt, None)
                            .await
                            .map_err(failure)?;
                        self.ended = true;
                        Ok(())
                    }
                    ActionExecution::Receipt(receipt) => {
                        owner
                            .bind_narration_action(self.campaign_id, ids.attempt_id, &receipt.action_id)
                            .await
                            .map_err(owner_failure)?;
                        self.run.record_mechanics_activity(ids, source_order, &receipt.action_id).await
                    }
                }
            }
            ProviderEvent::Evidence { message_id, payload } => {
                let mut content = payload;
                let activity = object(vec![
                    ("attempt_id", json!(ids.attempt_id)),
                    ("basis_message_id", json!(ids.basis_message_id)),
                    ("source_order", json!(source_order)),
                ]);
                content.insert("activity".into(), Json::Object(activity));
                owner
                    .append_agent_message(ids.session_id, &message_id, SessionMessageKind::Evidence, &content)
                    .await
                    .map_err(failure)?;
                Ok(())
            }
            ProviderEvent::ProseComplete => Err(failure(type_error("Unknown provider event"))),
        }
    }

    async fn private_record(
        &mut self,
        text: String,
        response_id: Option<String>,
        kind: &str,
        source_order: i64,
    ) -> Result<(), SessionFailure> {
        if text.is_empty() {
            return Ok(());
        }
        let ids = self.ids;
        let record = ProviderPrivateRecord {
            session_id: ids.session_id.to_owned(),
            attempt_id: ids.attempt_id.to_owned(),
            response_id: response_id.filter(|id| !id.is_empty()).unwrap_or_else(|| ids.attempt_id.to_owned()),
            turn_id: ids.turn_id.to_owned(),
            basis_message_id: ids.basis_message_id.to_owned(),
            source_order,
            provider_id: self.provider_id.clone(),
            model_id: self.model_id.clone(),
            kind: kind.to_owned(),
            payload: text,
        };
        self.run.owner.append_private_provider_record(&record).await.map_err(owner_failure)
    }
}

impl SessionEvents for RetryEvents<'_> {
    fn emit(&mut self, event: ProviderEvent) -> BoxFuture<'_, Result<(), SessionProviderError>> {
        Box::pin(async move {
            let source_order = self.source_order;
            self.source_order += 1;
            match self.handle(event, source_order).await {
                Ok(()) if self.ended => Err(SessionProviderError {
                    error: failure(rejected("The Retry ended at a choice")),
                    loop_reason: None,
                }),
                Ok(()) => Ok(()),
                Err(error) => Err(SessionProviderError { error, loop_reason: None }),
            }
        })
    }
}

impl CampaignSessionLoop {
    /// `CampaignSessionLoop(owner, ...)`.
    pub fn new(owner: Arc<dyn TurnOwner>, hooks: LoopHooks) -> Self {
        Self {
            owner,
            hooks,
            state: Mutex::new(LoopState::default()),
            ids: Arc::new(crate::working_context::uuid4_hex),
        }
    }

    /// The stateless MCP client's identity source (tests pin Python's values).
    pub fn with_ids(mut self, ids: Arc<dyn Fn() -> String + Send + Sync>) -> Self {
        self.ids = ids;
        self
    }

    fn state(&self) -> MutexGuard<'_, LoopState> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }

    fn active(&self, session_id: &str) -> Option<Arc<ActiveRun>> {
        self.state().active.get(session_id).cloned()
    }

    /// `_active_run(session_id)`.
    fn active_run(&self, session_id: &str) -> CodedResult<Arc<ActiveRun>> {
        self.active(session_id).ok_or_else(no_active_run)
    }

    /// `run(request, provider)`: one DM turn (or a campaign's opening) to its end.
    pub async fn run(
        &self,
        request: SessionRunRequest,
        provider: Arc<dyn SessionProvider>,
    ) -> Result<SessionRunResult, SessionFailure> {
        {
            let mut state = self.state();
            if state.active.contains_key(&request.session_id) || state.pending.contains(&request.session_id) {
                return Err(failure(rejected("Agent session already has active provider work")));
            }
            state.pending.insert(request.session_id.clone());
        }
        let _reservation = Reservation { state: &self.state, session_id: request.session_id.clone() };
        let held = self.owner.agent_session_work(&request.session_id).await.map_err(owner_failure)?;
        let context = held.context.clone();
        let outcome = self.run_with_session_work(request, provider, context).await;
        drop(held);
        outcome
    }

    /// `_run_with_session_work(request, provider, context)`.
    async fn run_with_session_work(
        &self,
        mut request: SessionRunRequest,
        provider: Arc<dyn SessionProvider>,
        context: SessionContext,
    ) -> Result<SessionRunResult, SessionFailure> {
        let owner = self.owner.as_ref();
        let Some(campaign_id) = context.session.campaign_id().map(str::to_owned) else {
            return Err(failure(rejected("DM sessions require campaign authority")));
        };
        if let Some(choice) = request.campaign_ending_choice.clone() {
            let redirecting = request.redirect_interruption_id.as_deref().is_some_and(|id| !id.is_empty());
            if request.input_kind != SessionInputKind::User || redirecting {
                return Err(failure(rejected("Ending choices require ordinary idle player input")));
            }
            let text = owner
                .campaign_ending_choice_text(&request.session_id, &request.message_id, &choice)
                .await
                .map_err(owner_failure)?;
            request.ooc_text = Some(text);
        }
        let terminal = context.messages.iter().any(|message| {
            content_is(message, "attempt_id", &request.attempt_id)
                && ((content_is(message, "kind", "gm_turn_activity")
                    && content_is(message, "activity", "turn_completed"))
                    || (content_is(message, "kind", "session_attempt_terminal")
                        && content_is(message, "status", "interrupted")))
        });
        if terminal {
            let error =
                CodedError::new("idempotency_conflict", "Agent session attempt identity is already terminal")
                    .with_error_type("IdempotencyConflict");
            return Err(failure(error));
        }
        let snapshot = owner.snapshot(&campaign_id).await.map_err(failure)?;
        let initial_high_water = snapshot.events.last().map_or(0, |event| event.sequence);
        let application_event = matches!(
            request.input_kind,
            SessionInputKind::CampaignStart | SessionInputKind::PlayerActionResponse
        );
        let ooc_given = request.ooc_text.as_deref().is_some_and(|text| !text.is_empty());
        if application_event && (!request.user_text.is_empty() || ooc_given) {
            return Err(failure(rejected("Application response requests do not contain player speech")));
        }
        if request.input_kind == SessionInputKind::PlayerActionResponse {
            let Some(action_id) = request.player_action_id.clone().filter(|id| !id.is_empty()) else {
                return Err(failure(rejected("A response request needs its saved player action")));
            };
            let basis = owner
                .player_action_response_basis(&request.session_id, &action_id, request.automatic_response)
                .await
                .map_err(owner_failure)?;
            let turn_id = basis.get("turn_id").ok_or_else(|| failure(key_error("turn_id")))?;
            if turn_id != &json!(request.turn_id) {
                return Err(failure(rejected("The saved player action belongs to another turn")));
            }
        } else if request.player_action_id.is_some() || request.automatic_response {
            return Err(failure(rejected("Player action response context requires its application event")));
        }
        if request.redirect_interruption_id.is_none()
            && !application_event
            && request.campaign_ending_choice.is_none()
        {
            // Plain input never leaves an interrupted turn behind it (architecture contract 8.3).
            if let Some((interruption, redirectable)) =
                owner.unresolved_session_interruption(&context).await.map_err(owner_failure)?
            {
                if !redirectable {
                    return Err(failure(rejected("Retry or resolve the interrupted turn first")));
                }
                request.redirect_interruption_id = Some(interruption.interruption_id);
            }
        }
        let mut redirection = None;
        if let Some(interruption_id) = request.redirect_interruption_id.clone() {
            if application_event {
                return Err(failure(rejected("Only player input can redirect stopped narration")));
            }
            let interrupted =
                owner.narration_interruption(&campaign_id, &interruption_id).await.map_err(owner_failure)?;
            let previous_input = resolve_interruption_basis(&context, &interrupted).map_err(failure)?;
            let mut content = object(vec![("text", json!(request.user_text))]);
            if let Some(ooc) = &request.ooc_text {
                content.insert("ooc_text".into(), json!(ooc));
            }
            let start = previous_input.content().get("canonical_start_sequence").cloned();
            content.insert(
                "canonical_start_sequence".into(),
                start.unwrap_or_else(|| json!(initial_high_water)),
            );
            for (key, value) in [
                ("input_id", json!(request.attempt_id)),
                ("attempt_id", json!(request.attempt_id)),
                ("turn_id", json!(request.turn_id)),
                ("input_kind", json!(request.input_kind.as_str())),
                ("redirect_interruption_id", json!(interruption_id)),
                ("redirected_from_message_id", json!(previous_input.message_id())),
            ] {
                content.insert(key.into(), value);
            }
            let redirected = owner
                .redirect_narration(&request.session_id, &request.message_id, &content, &interruption_id)
                .await
                .map_err(owner_failure)?;
            redirection = Some(redirected);
        } else {
            self.append_dm_input(&request, application_event, initial_high_water).await?;
        }
        let history = owner.session_context(&request.session_id).await.map_err(failure)?;
        let mut provider_input = SessionProviderInput::new(
            context.session.clone(),
            history,
            Vec::new(),
            Arc::new(ProviderControl::new()),
        );
        provider_input.basis_message_id = Some(request.message_id.clone());
        let last_action_id = redirection.as_ref().and_then(|redirected| redirected.last_action_id.clone());
        if let Some(action_id) = &last_action_id {
            provider_input.recovery_kind = Some("post_mechanics_narration".into());
            let receipt = owner.saved_action_receipt(&campaign_id, action_id).await.map_err(owner_failure)?;
            provider_input.retry_action_receipt = Some(receipt);
        }
        self.install_callbacks(&mut provider_input, &campaign_id, request.ids());
        self.run_dm_provider(&request, provider, provider_input, initial_high_water, redirection.as_ref())
            .await
    }

    /// The provider input's catalog supplier, knowledge searcher, reference reader and
    /// narrative writer, over the stateless MCP client.
    fn install_callbacks(&self, input: &mut SessionProviderInput, campaign_id: &str, ids: AttemptIds<'_>) {
        let (owner, campaign, session) =
            (Arc::clone(&self.owner), campaign_id.to_owned(), ids.session_id.to_owned());
        input.catalog_supplier = Some(Arc::new(move |catalog_request_id: String| {
            let (owner, campaign, session) = (Arc::clone(&owner), campaign.clone(), session.clone());
            Box::pin(async move {
                stateless_mcp::listing::list_tools_with_discovery(
                    owner.as_ref(),
                    &campaign,
                    "provider_inference",
                    &catalog_request_id,
                    Some(&session),
                    false,
                )
                .await
            })
        }));
        let (owner, campaign, session) =
            (Arc::clone(&self.owner), campaign_id.to_owned(), ids.session_id.to_owned());
        input.knowledge_searcher =
            Some(Arc::new(move |query: String, sources: Option<Vec<String>>, limit, offset| {
                let (owner, campaign, session) = (Arc::clone(&owner), campaign.clone(), session.clone());
                Box::pin(async move {
                    stateless_mcp::campaign_file::search_knowledge(
                        owner.as_ref(),
                        &campaign,
                        &query,
                        sources.as_deref(),
                        limit,
                        offset,
                        Some(&session),
                    )
                    .await
                    .map_err(AuthorityFailure::into_coded)
                })
            }));
        let (owner, campaign, session) =
            (Arc::clone(&self.owner), campaign_id.to_owned(), ids.session_id.to_owned());
        input.reference_reader = Some(Arc::new(move |source: String, path: String| {
            let (owner, campaign, session) = (Arc::clone(&owner), campaign.clone(), session.clone());
            Box::pin(async move {
                stateless_mcp::campaign_file::read_reference(
                    owner.as_ref(),
                    &campaign,
                    &source,
                    &path,
                    Some(&session),
                )
                .await
                .map_err(AuthorityFailure::into_coded)
            })
        }));
        let (owner, campaign, session) =
            (Arc::clone(&self.owner), campaign_id.to_owned(), ids.session_id.to_owned());
        let (basis, turn_id) = (ids.basis_message_id.to_owned(), ids.turn_id.to_owned());
        input.narrative_writer = Some(Arc::new(move |parameters: JsonMap| {
            let (owner, campaign, session) = (Arc::clone(&owner), campaign.clone(), session.clone());
            let (basis, turn_id) = (basis.clone(), turn_id.clone());
            Box::pin(async move {
                stateless_mcp::campaign_file::commit_campaign_file(
                    owner.as_ref(),
                    &campaign,
                    &session,
                    &basis,
                    &parameters,
                    Some(&turn_id),
                )
                .await
            })
        }));
    }

    /// `_append_dm_input(request, application_event, initial_high_water)`: the exact ordinary
    /// player input or application response request.
    async fn append_dm_input(
        &self,
        request: &SessionRunRequest,
        application_event: bool,
        initial_high_water: i64,
    ) -> Result<(), SessionFailure> {
        let owner = self.owner.as_ref();
        let kind = request.input_kind.as_str();
        let mut content = JsonMap::new();
        if application_event {
            content.insert("kind".into(), json!(kind));
            content.insert("event".into(), json!(kind));
            if request.input_kind == SessionInputKind::PlayerActionResponse {
                let session = owner.session_context(&request.session_id).await.map_err(failure)?.session;
                let campaign_id = session.campaign_id().unwrap_or_default();
                let records = owner.player_action_context(campaign_id).await.map_err(failure)?;
                let mut ids = Vec::new();
                for record in records {
                    ids.push(
                        record.get("action_id").cloned().ok_or_else(|| failure(key_error("action_id")))?,
                    );
                }
                content.insert("player_action_id".into(), json!(request.player_action_id));
                content.insert("player_action_ids".into(), Json::Array(ids));
                content.insert("automatic_response".into(), json!(request.automatic_response));
            }
        } else {
            content.insert("text".into(), json!(request.user_text));
            if let Some(ooc) = &request.ooc_text {
                content.insert("ooc_text".into(), json!(ooc));
            }
            if let Some(choice) = &request.campaign_ending_choice {
                content.insert("campaign_ending_choice".into(), Json::Object(choice.clone()));
            }
        }
        for (key, value) in [
            ("canonical_start_sequence", json!(initial_high_water)),
            ("input_id", json!(request.attempt_id)),
            ("attempt_id", json!(request.attempt_id)),
            ("turn_id", json!(request.turn_id)),
            ("input_kind", json!(kind)),
        ] {
            content.insert(key.into(), value);
        }
        let message_kind =
            if application_event { SessionMessageKind::Evidence } else { SessionMessageKind::User };
        owner
            .append_agent_message(&request.session_id, &request.message_id, message_kind, &content)
            .await
            .map_err(failure)?;
        Ok(())
    }

    /// `canonical_replay(campaign_id, acknowledged_sequence=...).canonical`.
    async fn canonical_since(
        &self,
        campaign_id: &str,
        initial_high_water: i64,
    ) -> Result<Vec<CanonicalEvent>, SessionFailure> {
        Ok(self.owner.canonical_replay(campaign_id, initial_high_water).await.map_err(failure)?.canonical)
    }

    /// `_run_dm_provider(request, provider, provider_input, initial_high_water, redirection)`.
    async fn run_dm_provider(
        &self,
        request: &SessionRunRequest,
        provider: Arc<dyn SessionProvider>,
        provider_input: SessionProviderInput,
        initial_high_water: i64,
        redirection: Option<&NarrationInterruption>,
    ) -> Result<SessionRunResult, SessionFailure> {
        // `assert campaign_id is not None`: a bare `AssertionError`, an internal fault.
        let Some(campaign_id) = provider_input.session.campaign_id().map(str::to_owned) else {
            return Err(failure(CodedError::new("internal_error", "").with_error_type("AssertionError")));
        };
        let active = Arc::new(ActiveRun {
            campaign_id,
            attempt_id: request.attempt_id.clone(),
            control: Arc::clone(&provider_input.control),
            basis_message_id: Some(request.message_id.clone()),
            provisional: Mutex::default(),
        });
        {
            let mut state = self.state();
            if state.stop_requested.contains(&request.session_id) {
                return Err(failure(rejected("Agent session was stopped before provider admission")));
            }
            state.active.insert(request.session_id.clone(), Arc::clone(&active));
            state.pending.remove(&request.session_id);
        }
        let mut narration_started = false;
        let mut receipts = Vec::new();
        let outcome = self
            .provider_attempt(
                request,
                provider,
                provider_input,
                initial_high_water,
                redirection,
                &active,
                &mut narration_started,
                &mut receipts,
            )
            .await;
        let outcome = match outcome {
            Ok(result) => Ok(result),
            Err(error) => {
                self.unwind(request, error, &active, narration_started, initial_high_water, receipts).await
            }
        };
        // `finally`: the provider's events ended with its run.
        if narration_started {
            if let Some(finished) = &self.hooks.on_narration_finished {
                finished(&active.campaign_id, &active.attempt_id);
            }
        }
        self.state().active.remove(&request.session_id);
        outcome
    }

    /// `_run_dm_provider`'s `try` body: the attempt, the provider's events, and the completion.
    #[allow(clippy::too_many_arguments)]
    async fn provider_attempt(
        &self,
        request: &SessionRunRequest,
        provider: Arc<dyn SessionProvider>,
        provider_input: SessionProviderInput,
        initial_high_water: i64,
        redirection: Option<&NarrationInterruption>,
        active: &ActiveRun,
        narration_started: &mut bool,
        receipts: &mut Vec<MechanicsExecutionReceipt>,
    ) -> Result<SessionRunResult, SessionFailure> {
        let owner = self.owner.as_ref();
        let control = Arc::clone(&active.control);
        let cancel: Arc<dyn Fn() + Send + Sync> = Arc::new(move || {
            // `ProviderControl.cancel`: its progress update cannot fail on a live run.
            let _ = control.cancel();
        });
        let last_action_id = match redirection {
            Some(redirected) => redirected.last_action_id.clone(),
            None => request.player_action_id.clone(),
        };
        owner
            .start_narration_attempt(NarrationAttemptStart {
                campaign_id: active.campaign_id.clone(),
                attempt_id: active.attempt_id.clone(),
                turn_id: request.turn_id.clone(),
                cancel: Arc::clone(&cancel),
                last_action_id,
                interruption_id: request.redirect_interruption_id.clone(),
            })
            .await
            .map_err(owner_failure)?;
        *narration_started = true;
        if let Some(started) = &self.hooks.on_narration_started {
            started(&active.campaign_id, &active.attempt_id, Arc::clone(&cancel));
        }
        let stopped_before_provider = self.state().stop_requested.contains(&request.session_id);
        if stopped_before_provider || active.control.cancelled() {
            return Err(failure(rejected("Agent session was stopped before provider admission")));
        }
        active.control.admit(|| ()).map_err(failure)?;
        let fields = provider_input.session.fields();
        let mut events = DmEvents {
            run: self,
            request,
            active,
            provider_id: fields.provider_id.clone(),
            model_id: fields.model_id.clone(),
            initial_high_water,
            source_order: 0,
            outcome: Outcome::default(),
        };
        let ran = provider.run(provider_input, &mut events).await;
        let outcome = std::mem::take(&mut events.outcome);
        receipts.extend(outcome.receipts);
        if let Some(early) = outcome.early {
            // The provider was stopped at the choice: its own error is that stop.
            receipts.clear();
            return Ok(early);
        }
        ran.map_err(|error| error.error)?;
        if active.control.cancelled() {
            return Err(failure(rejected("Agent session provider work was cancelled")));
        }
        owner
            .complete_narration_attempt(&active.campaign_id, &active.attempt_id)
            .await
            .map_err(owner_failure)?;
        self.record_gm_turn_activity(request.ids(), request.input_kind == SessionInputKind::CampaignStart)
            .await?;
        let canonical = self.canonical_since(&active.campaign_id, initial_high_water).await?;
        owner
            .append_session_canonical_references(&request.session_id, &canonical)
            .await
            .map_err(owner_failure)?;
        Ok(SessionRunResult { canonical, receipts: std::mem::take(receipts), interruption: None })
    }

    /// `_run_dm_provider`'s `except BaseException` branch: interrupt the attempt, record its
    /// terminal and committed results, then answer a cancelled run or raise.
    async fn unwind(
        &self,
        request: &SessionRunRequest,
        error: SessionFailure,
        active: &ActiveRun,
        narration_started: bool,
        initial_high_water: i64,
        receipts: Vec<MechanicsExecutionReceipt>,
    ) -> Result<SessionRunResult, SessionFailure> {
        let error = match error {
            SessionFailure::Turn(TurnFailure::Provider(provider_error))
                if request.input_kind == SessionInputKind::User =>
            {
                SessionFailure::Turn(TurnFailure::Provider(
                    provider_error.with_accepted_message_id(&request.message_id),
                ))
            }
            other => other,
        };
        if !narration_started {
            return Err(error);
        }
        let was_cancelled = active.control.cancelled();
        let interruption = match self.owner.interrupt_narration(&active.campaign_id, &active.attempt_id).await
        {
            Ok(interruption) => interruption,
            Err(owner_error) => {
                let owner_error = owner_error.into_coded();
                if was_cancelled && owner_closed(&owner_error) {
                    return Err(cancelled_during_shutdown());
                }
                return Err(failure(owner_error));
            }
        };
        self.state().last_interruption.insert(request.session_id.clone(), interruption.clone());
        self.record_interrupted_terminal(
            &request.session_id,
            &interruption,
            &failure_category(&error, was_cancelled),
            Some(active),
        )
        .await?;
        // Committed results belong in chat history even when only narration failed.
        let canonical = match self.canonical_since(&active.campaign_id, initial_high_water).await {
            Ok(canonical) => canonical,
            Err(SessionFailure::Turn(TurnFailure::Coded(owner_error)))
                if was_cancelled && owner_closed(&owner_error) =>
            {
                return Err(cancelled_during_shutdown());
            }
            Err(other) => return Err(other),
        };
        if let Err(owner_error) =
            self.owner.append_session_canonical_references(&request.session_id, &canonical).await
        {
            let owner_error = owner_error.into_coded();
            if was_cancelled && owner_closed(&owner_error) {
                return Err(cancelled_during_shutdown());
            }
            return Err(failure(owner_error));
        }
        if was_cancelled {
            return Ok(SessionRunResult { canonical, receipts, interruption: Some(interruption) });
        }
        Err(error)
    }

    /// `_record_interrupted_terminal(session_id, interruption, failure_category=..., active=...)`:
    /// the recoverable terminal, saved once whichever of Stop and the run's unwind comes first.
    async fn record_interrupted_terminal(
        &self,
        session_id: &str,
        interruption: &NarrationInterruption,
        category: &str,
        active: Option<&ActiveRun>,
    ) -> Result<(), SessionFailure> {
        let owner = self.owner.as_ref();
        owner
            .finish_private_provider_attempt(session_id, &interruption.failed_attempt_id, false)
            .await
            .map_err(owner_failure)?;
        let first = owner.session_context(session_id).await.map_err(failure)?;
        let basis = first
            .messages
            .iter()
            .find(|message| {
                content_is(message, "attempt_id", &interruption.failed_attempt_id)
                    && matches!(message.kind(), SessionMessageKind::User | SessionMessageKind::Evidence)
                    && !content_is(message, "kind", "session_attempt_terminal")
            })
            .cloned()
            .ok_or_else(|| failure(rejected("Interrupted session input identity is unavailable")))?;
        let context = owner.session_context(session_id).await.map_err(failure)?;
        let terminal_id = format!("terminal-{}", interruption.failed_attempt_id);
        if context.messages.iter().any(|message| message.message_id() == terminal_id) {
            return Ok(());
        }
        let recovery = recovery_kind(&context, &basis, interruption);
        let status = if category == "user_cancelled" { "stopped" } else { "failed" };
        let timing = self.finish_activity(session_id, &interruption.failed_attempt_id, status, active)?;
        let input_id =
            basis.content().get("input_id").cloned().unwrap_or_else(|| json!(interruption.failed_attempt_id));
        let content = object(vec![
            ("kind", json!("session_attempt_terminal")),
            ("input_id", input_id),
            ("attempt_id", json!(interruption.failed_attempt_id)),
            ("message_id", json!(basis.message_id())),
            ("turn_id", json!(interruption.turn_id)),
            ("status", json!("interrupted")),
            ("failure_category", json!(category)),
            ("activity_timing", Json::Object(timing)),
            ("recovery_kind", json!(recovery)),
            ("committed_action_id", json!(interruption.last_action_id)),
            ("retryable", json!(interruption.retryable)),
        ]);
        owner
            .append_agent_message(session_id, &terminal_id, SessionMessageKind::Evidence, &content)
            .await
            .map_err(failure)?;
        Ok(())
    }

    /// `_finish_activity(session_id, attempt_id, status, active=None)`: the timing frozen once.
    fn finish_activity(
        &self,
        session_id: &str,
        attempt_id: &str,
        status: &str,
        active: Option<&ActiveRun>,
    ) -> Result<JsonMap, SessionFailure> {
        let registered;
        let active = match active {
            Some(active) => Some(active),
            None => {
                registered = self.active(session_id);
                registered.as_deref()
            }
        };
        match active {
            Some(active) if active.attempt_id == attempt_id => {
                active.control.progress.finish(status).map_err(failure)
            }
            _ => Ok(JsonMap::new()),
        }
    }

    /// `_record_gm_turn_activity(...)`: one spoiler-safe card for a completed GM turn.
    async fn record_gm_turn_activity(
        &self,
        ids: AttemptIds<'_>,
        opening: bool,
    ) -> Result<(), SessionFailure> {
        let owner = self.owner.as_ref();
        owner
            .finish_private_provider_attempt(ids.session_id, ids.attempt_id, true)
            .await
            .map_err(owner_failure)?;
        let session = owner.session_context(ids.session_id).await.map_err(failure)?.session;
        if session.role() != WriterRole::Dm {
            return Ok(());
        }
        let timing = self.finish_activity(ids.session_id, ids.attempt_id, "completed", None)?;
        let content = object(vec![
            ("kind", json!("gm_turn_activity")),
            ("activity", json!(if opening { "campaign_opened" } else { "turn_completed" })),
            ("activity_timing", Json::Object(timing)),
            ("attempt_id", json!(ids.attempt_id)),
            ("basis_message_id", json!(ids.basis_message_id)),
            ("turn_id", json!(ids.turn_id)),
        ]);
        let message_id = format!("gm-activity-{}", ids.attempt_id);
        owner
            .append_agent_message(ids.session_id, &message_id, SessionMessageKind::Evidence, &content)
            .await
            .map_err(failure)?;
        Ok(())
    }

    /// `_record_mechanics_activity(...)`: execution-time correlation beside canonical mechanics.
    async fn record_mechanics_activity(
        &self,
        ids: AttemptIds<'_>,
        source_order: i64,
        action_id: &str,
    ) -> Result<(), SessionFailure> {
        let activity = object(vec![
            ("attempt_id", json!(ids.attempt_id)),
            ("basis_message_id", json!(ids.basis_message_id)),
            ("source_order", json!(source_order)),
        ]);
        let content = object(vec![
            ("kind", json!("mechanics_activity")),
            ("action_id", json!(action_id)),
            ("activity", Json::Object(activity)),
        ]);
        let message_id = format!("mechanics-activity-{}-{action_id}", ids.attempt_id);
        self.owner
            .append_agent_message(ids.session_id, &message_id, SessionMessageKind::Evidence, &content)
            .await
            .map_err(failure)?;
        Ok(())
    }

    /// `run_builder(request, provider, persist)`: one Builder turn under Stop and shutdown,
    /// without gameplay authority; the messages it appended.
    pub async fn run_builder(
        &self,
        request: SessionRunRequest,
        provider: Arc<dyn SessionProvider>,
        persist: BuilderPersist,
    ) -> Result<Vec<SessionMessage>, SessionFailure> {
        {
            let mut state = self.state();
            if state.active.contains_key(&request.session_id) || state.pending.contains(&request.session_id) {
                return Err(failure(rejected("Agent session already has active provider work")));
            }
            state.pending.insert(request.session_id.clone());
        }
        let _reservation = Reservation { state: &self.state, session_id: request.session_id.clone() };
        let held = self.owner.builder_session_work(&request.session_id).await.map_err(owner_failure)?;
        let context = held.context.clone();
        let outcome = self.run_builder_with_session_work(request, provider, persist, context).await;
        drop(held);
        outcome
    }

    /// `_run_builder_with_session_work(request, provider, persist, context)`.
    async fn run_builder_with_session_work(
        &self,
        request: SessionRunRequest,
        provider: Arc<dyn SessionProvider>,
        persist: BuilderPersist,
        context: SessionContext,
    ) -> Result<Vec<SessionMessage>, SessionFailure> {
        let owner = self.owner.as_ref();
        let (input_kind, input_content) = match request.input_kind {
            SessionInputKind::BuilderStart => {
                let launched = context.messages.iter().any(|message| {
                    message.kind() == SessionMessageKind::Evidence
                        && matches!(
                            message.content().get("kind").and_then(Json::as_str),
                            Some("builder_launch_context" | "import_repair_context")
                        )
                });
                if !request.user_text.is_empty() || !launched {
                    return Err(failure(rejected("Builder start requires its saved launch context")));
                }
                if context.messages.iter().any(|message| {
                    message.kind() == SessionMessageKind::User || content_is(message, "kind", "builder_start")
                }) {
                    return Err(failure(rejected(
                        "Builder chat has already started; send a message to continue",
                    )));
                }
                let content =
                    object(vec![("kind", json!("builder_start")), ("attempt_id", json!(request.attempt_id))]);
                (SessionMessageKind::Evidence, content)
            }
            SessionInputKind::User => {
                let mut content = object(vec![
                    ("text", json!(request.user_text)),
                    ("attempt_id", json!(request.attempt_id)),
                ]);
                if let Some(answers) = &request.builder_question_answers {
                    let answers = Json::Array(answers.iter().cloned().map(Json::Object).collect());
                    content = crate::builder::question_answers::builder_question_answer_content(
                        &context,
                        &answers,
                        &request.user_text,
                    )
                    .map_err(failure)?;
                    content.insert("attempt_id".into(), json!(request.attempt_id));
                }
                (SessionMessageKind::User, content)
            }
            _ => return Err(failure(rejected("Builder input kind is unsupported"))),
        };
        owner
            .append_agent_message(&request.session_id, &request.message_id, input_kind, &input_content)
            .await
            .map_err(failure)?;
        let history = owner.session_context(&request.session_id).await.map_err(failure)?;
        let provider_input = SessionProviderInput::new(
            context.session.clone(),
            history,
            Vec::new(),
            Arc::new(ProviderControl::new()),
        );
        // Builder provider work is attached to the durable session, even when that session
        // belongs to a campaign: the frontend streams, stops and reconnects by session id.
        let active = Arc::new(ActiveRun {
            campaign_id: context.session.session_id().to_owned(),
            attempt_id: request.attempt_id.clone(),
            control: Arc::clone(&provider_input.control),
            basis_message_id: Some(request.message_id.clone()),
            provisional: Mutex::default(),
        });
        {
            let mut state = self.state();
            if state.stop_requested.contains(&request.session_id) {
                return Err(failure(rejected("Agent session was stopped before provider admission")));
            }
            state.active.insert(request.session_id.clone(), Arc::clone(&active));
            state.pending.remove(&request.session_id);
        }
        let mut work_started = false;
        let mut events = BuilderEvents {
            run: self,
            request: &request,
            active: &active,
            provider_id: provider_input.session.fields().provider_id.clone(),
            model_id: provider_input.session.fields().model_id.clone(),
            source_order: 0,
            appended: Vec::new(),
            provisional_response_id: None,
            native_requirements: HashMap::new(),
        };
        let outcome = async {
            let control = Arc::clone(&active.control);
            let cancel: Arc<dyn Fn() + Send + Sync> = Arc::new(move || {
                let _ = control.cancel();
            });
            if let Some(started) = &self.hooks.on_provider_work_started {
                started(&active.campaign_id, &active.attempt_id, cancel);
            }
            work_started = true;
            active.control.admit(|| ()).map_err(provider_failure)?;
            provider.run(provider_input, &mut events).await?;
            if active.control.cancelled() {
                return Err(provider_failure(rejected("Agent session provider work was cancelled")));
            }
            let persisted = async {
                events.flush_provisional().await?;
                persist().await.map_err(failure)
            }
            .await;
            match persisted {
                Ok(Some(messages)) => events.appended.extend(messages),
                Ok(None) => {}
                Err(SessionFailure::Turn(TurnFailure::Coded(error)))
                    if active.control.cancelled() && owner_closed(&error) =>
                {
                    return Err(SessionProviderError {
                        error: cancelled_during_shutdown(),
                        loop_reason: None,
                    });
                }
                Err(error) => return Err(SessionProviderError { error, loop_reason: None }),
            }
            events
                .record_terminal("completed", None)
                .await
                .map_err(|error| SessionProviderError { error, loop_reason: None })?;
            Ok(std::mem::take(&mut events.appended))
        }
        .await;
        let outcome = match outcome {
            Ok(appended) => Ok(appended),
            Err(SessionProviderError { error, loop_reason }) => {
                let error = match error {
                    SessionFailure::Turn(TurnFailure::Provider(provider_error))
                        if request.input_kind == SessionInputKind::User =>
                    {
                        SessionFailure::Turn(TurnFailure::Provider(
                            provider_error.with_accepted_message_id(&request.message_id),
                        ))
                    }
                    other => other,
                };
                // Shutdown may already have closed storage: the saved input without a terminal
                // still identifies the interrupted work after a restart.
                let cancelled = active.control.cancelled();
                let reason_code = if cancelled {
                    None
                } else if let Some(reason) = loop_reason {
                    Some(reason)
                } else if matches!(
                    &error,
                    SessionFailure::Turn(TurnFailure::Provider(provider_error))
                        if provider_error.kind.is_a(sylvia_provider::error::ErrorKind::OutputLimit)
                ) {
                    Some("output_limit".to_owned())
                } else {
                    None
                };
                let status = if cancelled { "stopped" } else { "interrupted" };
                let _ = events.record_terminal(status, reason_code.as_deref()).await;
                Err(error)
            }
        };
        if work_started {
            if let Some(finished) = &self.hooks.on_provider_work_finished {
                finished(&active.campaign_id, &active.attempt_id);
            }
        }
        self.state().active.remove(&request.session_id);
        outcome
    }

    /// `run_compaction(session_id, attempt_id, provider)`: manual context maintenance under the
    /// ordinary session lock, without a game action.
    pub async fn run_compaction(
        &self,
        session_id: &str,
        attempt_id: &str,
        provider: Arc<dyn SessionProvider>,
    ) -> Result<(), SessionFailure> {
        {
            let mut state = self.state();
            if state.active.contains_key(session_id) || state.pending.contains(session_id) {
                return Err(failure(rejected("Agent session already has active provider work")));
            }
            state.pending.insert(session_id.to_owned());
        }
        let owner = self.owner.as_ref();
        // Python reads the role before its `try`: a failed read leaves the reservation in place.
        let role = owner.session_context(session_id).await.map_err(failure)?.session.role();
        let mut started = false;
        let outcome = async {
            let held = if role == WriterRole::Builder {
                owner.builder_session_work(session_id).await
            } else {
                owner.agent_session_work(session_id).await
            }
            .map_err(owner_failure)?;
            let control = Arc::new(ProviderControl::new());
            let active = Arc::new(ActiveRun {
                campaign_id: session_id.to_owned(),
                attempt_id: attempt_id.to_owned(),
                control: Arc::clone(&control),
                basis_message_id: None,
                provisional: Mutex::default(),
            });
            {
                let mut state = self.state();
                if state.stop_requested.contains(session_id) {
                    return Err(failure(rejected("Session was stopped before compaction")));
                }
                state.active.insert(session_id.to_owned(), Arc::clone(&active));
                state.pending.remove(session_id);
            }
            let request = object(vec![
                ("kind", json!("context_compaction_requested")),
                ("attempt_id", json!(attempt_id)),
            ]);
            owner
                .append_agent_message(
                    session_id,
                    &format!("compact-request-{attempt_id}"),
                    SessionMessageKind::Evidence,
                    &request,
                )
                .await
                .map_err(failure)?;
            let mut status = "interrupted";
            let ran = async {
                let cancel_control = Arc::clone(&control);
                let cancel: Arc<dyn Fn() + Send + Sync> = Arc::new(move || {
                    let _ = cancel_control.cancel();
                });
                if let Some(started_hook) = &self.hooks.on_provider_work_started {
                    started_hook(session_id, attempt_id, cancel);
                }
                started = true;
                let input = SessionProviderInput::new(
                    held.context.session.clone(),
                    held.context.clone(),
                    Vec::new(),
                    Arc::clone(&control),
                );
                let mut events = CompactionEvents { owner, session_id, control: &control };
                provider.run(input, &mut events).await.map_err(|error| error.error)
            }
            .await;
            if ran.is_ok() {
                status = "completed";
            }
            // `finally`: the terminal is saved however the provider ended; its own failure wins.
            let terminal = object(vec![
                ("kind", json!("context_compaction_terminal")),
                ("attempt_id", json!(attempt_id)),
                ("status", json!(status)),
            ]);
            owner
                .append_agent_message(
                    session_id,
                    &format!("compact-terminal-{attempt_id}"),
                    SessionMessageKind::Evidence,
                    &terminal,
                )
                .await
                .map_err(failure)?;
            drop(held);
            ran
        }
        .await;
        if started {
            if let Some(finished) = &self.hooks.on_provider_work_finished {
                finished(session_id, attempt_id);
            }
        }
        {
            let mut state = self.state();
            state.active.remove(session_id);
            state.pending.remove(session_id);
            state.stop_requested.remove(session_id);
        }
        outcome
    }

    /// `retry(session_id, retry_id=..., narrator=..., provider_factory=..., interruption_id=...,
    /// cancel=..., control=...)`: one manual Retry of an interrupted turn, through the owner's
    /// `manual_retry`.
    pub async fn retry(
        &self,
        session_id: &str,
        request: RetryRequest,
    ) -> Result<ManualRetryResult, SessionFailure> {
        {
            let mut state = self.state();
            if state.active.contains_key(session_id) || state.pending.contains(session_id) {
                return Err(failure(rejected("Agent session already has active provider work")));
            }
            state.pending.insert(session_id.to_owned());
        }
        let retry_id = request.retry_id.clone();
        let active_control = request.control.clone().unwrap_or_else(|| Arc::new(ProviderControl::new()));
        let failure_cancelled: Mutex<Option<bool>> = Mutex::new(None);
        let mut progress = RetryProgress::default();
        let outcome = self
            .retry_with_session_work(session_id, &request, &active_control, &failure_cancelled, &mut progress)
            .await;
        let outcome = match outcome {
            Ok(result) => Ok(result),
            Err(error) => {
                let cancelled = *failure_cancelled.lock().unwrap_or_else(PoisonError::into_inner);
                Err(self
                    .retry_failed(session_id, &retry_id, error, cancelled, &active_control, &progress)
                    .await)
            }
        };
        // `finally`.
        if progress.narration_started {
            if let (Some(finished), Some(campaign_id)) =
                (&self.hooks.on_narration_finished, &progress.campaign_id)
            {
                finished(campaign_id, &retry_id);
            }
        }
        {
            let mut state = self.state();
            state.active.remove(session_id);
            state.pending.remove(session_id);
            state.stop_requested.remove(session_id);
        }
        outcome
    }

    /// `retry`'s `with self._owner.agent_session_work(session_id) as context:` body.
    async fn retry_with_session_work(
        &self,
        session_id: &str,
        request: &RetryRequest,
        active_control: &Arc<ProviderControl>,
        failure_cancelled: &Mutex<Option<bool>>,
        progress: &mut RetryProgress,
    ) -> Result<ManualRetryResult, SessionFailure> {
        let owner = self.owner.as_ref();
        let retry_id = request.retry_id.as_str();
        let held = owner.agent_session_work(session_id).await.map_err(owner_failure)?;
        let context = held.context.clone();
        let interruption_id = match &request.interruption_id {
            Some(id) => id.clone(),
            None => match self.state().last_interruption.get(session_id) {
                Some(interruption) => interruption.interruption_id.clone(),
                None => return Err(failure(rejected("Agent session has no retryable interruption"))),
            },
        };
        let Some(campaign_id) = context.session.campaign_id().map(str::to_owned) else {
            return Err(failure(rejected("DM sessions require campaign authority")));
        };
        progress.campaign_id = Some(campaign_id.clone());
        let snapshot = owner.snapshot(&campaign_id).await.map_err(failure)?;
        progress.initial_high_water = snapshot.events.last().map_or(0, |event| event.sequence);
        let interruption =
            owner.narration_interruption(&campaign_id, &interruption_id).await.map_err(owner_failure)?;
        let basis = resolve_interruption_basis(&context, &interruption).map_err(failure)?;
        progress.basis = Some(basis.clone());
        let active = Arc::new(ActiveRun {
            campaign_id: campaign_id.clone(),
            attempt_id: retry_id.to_owned(),
            control: Arc::clone(active_control),
            basis_message_id: Some(basis.message_id().to_owned()),
            provisional: Mutex::default(),
        });
        let narration_started = Mutex::new(false);
        let admit = {
            let (active, cancel, campaign) =
                (Arc::clone(&active), Arc::clone(&request.cancel), campaign_id.clone());
            let narration_started = &narration_started;
            let session = session_id.to_owned();
            let retry = retry_id.to_owned();
            Box::new(move || -> BoxFuture<'_, Result<(), SessionFailure>> {
                Box::pin(async move {
                    // `admit_attempt()`.
                    let stopped = {
                        let mut state = self.state();
                        if state.stop_requested.contains(&session) {
                            true
                        } else {
                            state.active.insert(session.clone(), Arc::clone(&active));
                            state.pending.remove(&session);
                            if let Some(started) = &self.hooks.on_narration_started {
                                started(&campaign, &retry, Arc::clone(&cancel));
                            }
                            *narration_started.lock().unwrap_or_else(PoisonError::into_inner) = true;
                            false
                        }
                    };
                    if stopped {
                        let interruption =
                            self.owner.interrupt_narration(&campaign, &retry).await.map_err(owner_failure)?;
                        self.state().last_interruption.insert(session.clone(), interruption);
                        return Err(failure(rejected(
                            "Agent session was stopped before Retry provider admission",
                        )));
                    }
                    Ok(())
                })
            }) as crate::provider_session::reads::AttemptStarted<'_>
        };
        let narrator = Box::new(LoopRetry {
            run: self,
            session_id,
            campaign_id: &campaign_id,
            retry_id,
            context: &context,
            basis: &basis,
            provider_factory: request.provider_factory.clone(),
            narrator: Arc::clone(&request.narrator),
            control: Arc::clone(active_control),
            failure_cancelled,
        });
        let call = ManualRetryCall {
            campaign_id: campaign_id.clone(),
            interruption_id: interruption_id.clone(),
            retry_id: retry_id.to_owned(),
            cancel: Arc::clone(&request.cancel),
        };
        let outcome = owner.manual_retry(call, admit, narrator).await;
        progress.narration_started = *narration_started.lock().unwrap_or_else(PoisonError::into_inner);
        let result = match outcome {
            Ok(result) => result,
            Err(RetryRefusal::Owner(error)) => return Err(owner_failure(error)),
            Err(RetryRefusal::Narrator(error)) => return Err(error),
        };
        if !result.replayed {
            let ids = AttemptIds {
                session_id,
                attempt_id: retry_id,
                basis_message_id: basis.message_id(),
                turn_id: &interruption.turn_id,
            };
            self.record_gm_turn_activity(ids, content_is(&basis, "kind", "campaign_start")).await?;
        }
        // A Retry can commit a first mechanic as well as narration: keep the whole new interval.
        let canonical = if result.replayed {
            result.canonical.clone()
        } else {
            self.canonical_since(&campaign_id, progress.initial_high_water).await?
        };
        owner.append_session_canonical_references(session_id, &canonical).await.map_err(owner_failure)?;
        drop(held);
        Ok(result)
    }

    /// `retry`'s `except BaseException` branch: the failed Retry's terminal and its committed
    /// results, then the error raised again (or the error its own cleanup raised).
    async fn retry_failed(
        &self,
        session_id: &str,
        retry_id: &str,
        error: SessionFailure,
        failure_cancelled: Option<bool>,
        active_control: &ProviderControl,
        progress: &RetryProgress,
    ) -> SessionFailure {
        let owner = self.owner.as_ref();
        let stop_was_requested = self.state().stop_requested.contains(session_id);
        let was_cancelled = failure_cancelled.unwrap_or(stop_was_requested);
        let category = failure_category(&error, was_cancelled);
        let terminal_status = if category == "user_cancelled" { "stopped" } else { "failed" };
        let mut timing = match self.finish_activity(session_id, retry_id, terminal_status, None) {
            Ok(timing) => timing,
            Err(error) => return error,
        };
        if timing.is_empty() {
            // Stop can win before this Retry is registered as the active run.
            timing = match active_control.progress.finish(terminal_status) {
                Ok(timing) => timing,
                Err(error) => return failure(error),
            };
        }
        let Some(campaign_id) = &progress.campaign_id else { return error };
        let _ = owner.finish_private_provider_attempt(session_id, retry_id, false).await;
        let interruption = match owner.retryable_interruption(campaign_id, retry_id).await {
            Ok(interruption) => interruption,
            Err(owner_error) => return owner_failure(owner_error),
        };
        let Some(interruption) = interruption else { return error };
        self.state().last_interruption.insert(session_id.to_owned(), interruption.clone());
        if let Some(basis) = &progress.basis {
            let context = match owner.session_context(session_id).await {
                Ok(context) => context,
                Err(owner_error) => return failure(owner_error),
            };
            let content = object(vec![
                ("kind", json!("session_attempt_terminal")),
                ("input_id", basis.content().get("input_id").cloned().unwrap_or(Json::Null)),
                ("attempt_id", json!(retry_id)),
                ("message_id", json!(basis.message_id())),
                ("turn_id", json!(interruption.turn_id)),
                ("status", json!("interrupted")),
                ("failure_category", json!(category)),
                ("activity_timing", Json::Object(timing)),
                ("recovery_kind", json!(recovery_kind(&context, basis, &interruption))),
                ("committed_action_id", json!(interruption.last_action_id)),
                ("retryable", json!(interruption.retryable)),
            ]);
            let terminal_id = format!("terminal-{retry_id}");
            if let Err(owner_error) = owner
                .append_agent_message(session_id, &terminal_id, SessionMessageKind::Evidence, &content)
                .await
            {
                return failure(owner_error);
            }
        }
        let canonical = match self.canonical_since(campaign_id, progress.initial_high_water).await {
            Ok(canonical) => canonical,
            Err(owner_error) => return owner_error,
        };
        if let Err(owner_error) = owner.append_session_canonical_references(session_id, &canonical).await {
            return owner_failure(owner_error);
        }
        error
    }

    /// `steer(session_id, text, message_id=...)`: persist the run's completed provisional
    /// segments and the steering, then queue it while the provider still accepts it.
    pub async fn steer(&self, session_id: &str, text: &str, message_id: &str) -> CodedResult<()> {
        let active = self.active_run(session_id)?;
        let owner = Arc::clone(&self.owner);
        let run = Arc::clone(&active);
        active
            .control
            .admit_steering(text, || async move {
                for (segment_id, kind, content) in run.take_completed_provisional()? {
                    owner
                        .append_live_steering_message(session_id, &segment_id, kind, &content)
                        .await
                        .map_err(AuthorityFailure::into_coded)?;
                }
                let content = object(vec![("text", json!(text))]);
                owner
                    .append_live_steering_message(
                        session_id,
                        message_id,
                        SessionMessageKind::Steering,
                        &content,
                    )
                    .await
                    .map_err(AuthorityFailure::into_coded)?;
                Ok(())
            })
            .await
    }

    /// `assert_idle(session_id)`: no run is pending, preparing or active.
    pub fn assert_idle(&self, session_id: &str) -> CodedResult<()> {
        let state = self.state();
        if state.active.contains_key(session_id)
            || state.pending.contains(session_id)
            || state.preparing.contains(session_id)
        {
            return Err(rejected(
                "Agent session settings cannot change during pending or active provider work",
            ));
        }
        Ok(())
    }

    /// `stop_agent_session(session_id)`: Stop Session, linearised against pending and active
    /// provider work.
    pub async fn stop_agent_session(&self, session_id: &str) -> CodedResult<SessionRecord> {
        let active = {
            let mut state = self.state();
            let active = state.active.get(session_id).cloned();
            match &active {
                None if state.pending.contains(session_id) => {
                    state.stop_requested.insert(session_id.to_owned());
                }
                Some(active) => {
                    state.stop_requested.insert(session_id.to_owned());
                    active.control.cancel()?;
                }
                None => {}
            }
            active
        };
        if let Some(active) = active {
            let interruption =
                match self.owner.interrupt_narration(&active.campaign_id, &active.attempt_id).await {
                    Ok(interruption) => Some(interruption),
                    // Provider completion may win this race; stopping the now-idle session remains valid.
                    Err(error) if error.is_command_rejected() => None,
                    Err(error) => return Err(error.into_coded()),
                };
            if let Some(interruption) = interruption {
                self.state().last_interruption.insert(session_id.to_owned(), interruption.clone());
                self.record_interrupted_terminal(session_id, &interruption, "user_cancelled", Some(&active))
                    .await
                    .map_err(coded)?;
            }
        }
        self.owner.stop_agent_session(session_id).await.map_err(AuthorityFailure::into_coded)
    }

    /// `stop(session_id)`: stop one active provider call through the interruption path.
    pub async fn stop(&self, session_id: &str) -> CodedResult<SessionStopResult> {
        let active = self.active_run(session_id)?;
        let interruption = self
            .owner
            .interrupt_narration(&active.campaign_id, &active.attempt_id)
            .await
            .map_err(AuthorityFailure::into_coded)?;
        self.state().last_interruption.insert(session_id.to_owned(), interruption.clone());
        self.record_interrupted_terminal(session_id, &interruption, "user_cancelled", Some(&active))
            .await
            .map_err(coded)?;
        self.owner.stop_agent_session(session_id).await.map_err(AuthorityFailure::into_coded)?;
        Ok(SessionStopResult { interruption, cancellation_count: active.control.cancellation_count() })
    }

    /// `expire(session_id, now=...)`.
    pub async fn expire(&self, session_id: &str, now: f64) -> CodedResult<SessionStopResult> {
        let active = self.active_run(session_id)?;
        let interruption = self
            .owner
            .expire_narration(&active.campaign_id, &active.attempt_id, now)
            .await
            .map_err(AuthorityFailure::into_coded)?;
        self.state().last_interruption.insert(session_id.to_owned(), interruption.clone());
        Ok(SessionStopResult { interruption, cancellation_count: active.control.cancellation_count() })
    }

    /// `builder_activity(session_id, attempt_id=...)`: live work, or its saved outcome.
    pub async fn builder_activity(
        &self,
        session_id: &str,
        attempt_id: Option<&str>,
    ) -> CodedResult<Option<JsonMap>> {
        let (active, pending) = {
            let state = self.state();
            (state.active.get(session_id).cloned(), state.pending.contains(session_id))
        };
        // Read after the live snapshot: terminal evidence is saved before live work is removed.
        let attempt_id =
            attempt_id.map(str::to_owned).or_else(|| active.as_ref().map(|run| run.attempt_id.clone()));
        let basis = self.owner.session_activity_basis(session_id, attempt_id.as_deref()).await?;
        let is_builder = basis.session.role() == WriterRole::Builder;
        if !is_builder && !basis.has_compaction {
            return Ok(None);
        }
        let attempt_id = match (attempt_id, &basis.accepted) {
            (Some(attempt_id), _) => attempt_id,
            // `str(basis.accepted.content["attempt_id"])`.
            (None, Some(accepted)) => {
                py_str(accepted.content().get("attempt_id").ok_or_else(|| key_error("attempt_id"))?)
            }
            (None, None) => return Ok(None),
        };
        let mut activity = JsonMap::new();
        activity.insert("attempt_id".into(), Json::String(attempt_id.clone()));
        if let Some(terminal) = &basis.terminal {
            let status = terminal.content().get("status").cloned().ok_or_else(|| key_error("status"))?;
            activity.insert("status".into(), status);
            return Ok(Some(activity));
        }
        if let Some(active) = active.filter(|run| run.attempt_id == attempt_id) {
            activity.insert("status".into(), json!("active"));
            activity.insert("status_hint".into(), json!(active.control.status_hint()));
            return Ok(Some(activity));
        }
        let status = match (&basis.accepted, pending) {
            (None, _) => "unavailable",
            (Some(_), true) => "active",
            (Some(_), false) => "interrupted",
        };
        activity.insert("status".into(), json!(status));
        Ok(Some(activity))
    }

    /// `activity_progress(session_id)`: live owner observations of the active run.
    pub async fn activity_progress(&self, session_id: &str) -> CodedResult<Option<JsonMap>> {
        let Some(active) = self.active(session_id) else { return Ok(None) };
        let mut progress = object(vec![
            ("attempt_id", json!(active.attempt_id)),
            ("basis_message_id", json!(active.basis_message_id)),
        ]);
        progress.extend(active.control.progress.snapshot());
        Ok(Some(progress))
    }

    /// `context_status_hint(session_id)`: private-data-free progress of the active run.
    pub fn context_status_hint(&self, session_id: &str) -> Option<String> {
        self.active(session_id).and_then(|active| active.control.status_hint())
    }

    /// `provisional_session_projection(session_id)`: the active run's provisional prose.
    pub fn provisional_session_projection(&self, session_id: &str) -> Vec<JsonMap> {
        self.active(session_id).map(|active| active.provisional().items.clone()).unwrap_or_default()
    }

    /// `disconnect(session_id, now=...)`.
    pub async fn disconnect(&self, session_id: &str, now: f64) -> CodedResult<f64> {
        let active = self.active_run(session_id)?;
        self.owner
            .disconnect_narration(&active.campaign_id, &active.attempt_id, now)
            .await
            .map_err(AuthorityFailure::into_coded)
    }

    /// `reconnect(session_id, now=..., acknowledged_sequence=...)`.
    pub async fn reconnect(
        &self,
        session_id: &str,
        now: f64,
        acknowledged_sequence: i64,
    ) -> CodedResult<CanonicalReplay> {
        let active = self.active_run(session_id)?;
        self.owner
            .reconnect_narration(&active.campaign_id, &active.attempt_id, now, acknowledged_sequence)
            .await
            .map_err(AuthorityFailure::into_coded)
    }
}

/// A loop failure as a command's refusal.
fn coded(error: SessionFailure) -> CodedError {
    match error {
        SessionFailure::Turn(TurnFailure::Coded(error)) => error,
        SessionFailure::Turn(TurnFailure::Provider(error)) => {
            CodedError::new("internal_error", error.to_string())
        }
        SessionFailure::AgentLoop { reason, message } => CodedError::new(reason, message),
    }
}
