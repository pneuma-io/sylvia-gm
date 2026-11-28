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
//! - **Still `rust_port_pending`:** `run_builder` and `run_compaction` (T5b), and `retry` (T6).

use super::{
    ProviderControl, ProviderEvent, SessionEvents, SessionFailure, SessionInputKind, SessionProvider,
    SessionProviderError, SessionProviderInput, SessionRunRequest, SessionRunResult, SessionStopResult,
};
use crate::provider_session::reads::{NarrationAttemptStart, TurnOwner};
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

/// The method `CampaignSessionLoop` does not answer yet.
fn pending(method: &str) -> CodedError {
    CodedError::new(PORT_PENDING, format!("The Rust owner does not run agent/_session_loop.py {method} yet."))
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

fn pending_run(method: &str) -> SessionFailure {
    SessionFailure::Turn(TurnFailure::Coded(pending(method)))
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
                        self.run.record_gm_turn_activity(request).await?;
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
                        self.run.record_mechanics_activity(request, source_order, &action_id).await
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
        self.install_callbacks(&mut provider_input, &campaign_id, &request);
        self.run_dm_provider(&request, provider, provider_input, initial_high_water, redirection.as_ref())
            .await
    }

    /// The provider input's catalog supplier, knowledge searcher, reference reader and
    /// narrative writer, over the stateless MCP client.
    fn install_callbacks(
        &self,
        input: &mut SessionProviderInput,
        campaign_id: &str,
        request: &SessionRunRequest,
    ) {
        let (owner, campaign, session) =
            (Arc::clone(&self.owner), campaign_id.to_owned(), request.session_id.clone());
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
            (Arc::clone(&self.owner), campaign_id.to_owned(), request.session_id.clone());
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
            (Arc::clone(&self.owner), campaign_id.to_owned(), request.session_id.clone());
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
            (Arc::clone(&self.owner), campaign_id.to_owned(), request.session_id.clone());
        let (basis, turn_id) = (request.message_id.clone(), request.turn_id.clone());
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
        let campaign_id = provider_input.session.campaign_id().expect("a DM run has a campaign").to_owned();
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
        self.record_gm_turn_activity(request).await?;
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
    async fn record_gm_turn_activity(&self, request: &SessionRunRequest) -> Result<(), SessionFailure> {
        let owner = self.owner.as_ref();
        owner
            .finish_private_provider_attempt(&request.session_id, &request.attempt_id, true)
            .await
            .map_err(owner_failure)?;
        let session = owner.session_context(&request.session_id).await.map_err(failure)?.session;
        if session.role() != WriterRole::Dm {
            return Ok(());
        }
        let opening = request.input_kind == SessionInputKind::CampaignStart;
        let timing = self.finish_activity(&request.session_id, &request.attempt_id, "completed", None)?;
        let content = object(vec![
            ("kind", json!("gm_turn_activity")),
            ("activity", json!(if opening { "campaign_opened" } else { "turn_completed" })),
            ("activity_timing", Json::Object(timing)),
            ("attempt_id", json!(request.attempt_id)),
            ("basis_message_id", json!(request.message_id)),
            ("turn_id", json!(request.turn_id)),
        ]);
        let message_id = format!("gm-activity-{}", request.attempt_id);
        owner
            .append_agent_message(&request.session_id, &message_id, SessionMessageKind::Evidence, &content)
            .await
            .map_err(failure)?;
        Ok(())
    }

    /// `_record_mechanics_activity(...)`: execution-time correlation beside canonical mechanics.
    async fn record_mechanics_activity(
        &self,
        request: &SessionRunRequest,
        source_order: i64,
        action_id: &str,
    ) -> Result<(), SessionFailure> {
        let activity = object(vec![
            ("attempt_id", json!(request.attempt_id)),
            ("basis_message_id", json!(request.message_id)),
            ("source_order", json!(source_order)),
        ]);
        let content = object(vec![
            ("kind", json!("mechanics_activity")),
            ("action_id", json!(action_id)),
            ("activity", Json::Object(activity)),
        ]);
        let message_id = format!("mechanics-activity-{}-{action_id}", request.attempt_id);
        self.owner
            .append_agent_message(&request.session_id, &message_id, SessionMessageKind::Evidence, &content)
            .await
            .map_err(failure)?;
        Ok(())
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
