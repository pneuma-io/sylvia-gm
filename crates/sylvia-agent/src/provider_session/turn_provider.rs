//! `ProviderSessionAdapter.for_turn(turn_id)` and `for_retry(model_id, provider_input=...,
//! control=...)`: the session provider a DM turn runs and the narrator a manual retry runs.
//!
//! - **`for_turn`** runs `_turn_events` (`provider_turn::turn`, T5a-1) with the loop's events.
//! - **`for_retry`** (T6a) is the narration-only Retry: one tool-free request from the GM's
//!   messages and the canonical Retry data, its player-visible prose emitted as TALK. Its controls
//!   are `retry_texts.json`, exported by `generate_retry_texts.py`.

use super::context::ProviderSessionAdapter;
use crate::provider_turn::turn::turn_events;
use crate::provider_turn::{TurnEvents, TurnFailure, TurnResult};
use crate::session_loop::campaign::{NarrationStream, RetryNarrator};
use crate::session_loop::{
    ProviderControl, ProviderEvent, SessionEvents, SessionFailure, SessionProvider, SessionProviderError,
    SessionProviderInput,
};
use super::request::{contains_serialized_tool_call, release_provider_idle_soon};
use super::tagged::Tagged;
use super::texts::player_turn_text;
use crate::session_loop::campaign::resolve_retry_basis_message;
use serde_json::{json, Value as Json};
use std::sync::{Arc, OnceLock};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{CodedError, JsonMap};
use sylvia_domain::authority::{ProseChannel, RetryNarrationInput};
use sylvia_domain::session::SessionMessageKind;
use sylvia_provider::BoxFuture;

/// The provider `for_turn` gives.
struct TurnProvider {
    adapter: Arc<ProviderSessionAdapter>,
    turn_id: String,
}

/// The loop's events, as the turn phases emit them. Python's loop raises its own exception and
/// closes the generator; here the phase stops at the failed emit, and the loop is given back
/// exactly the error its handler raised (an `AgentLoopError` and its reason included).
struct LoopEvents<'a> {
    events: &'a mut dyn SessionEvents,
    raised: Option<SessionProviderError>,
}

impl TurnEvents for LoopEvents<'_> {
    fn emit(&mut self, event: ProviderEvent) -> BoxFuture<'_, TurnResult<()>> {
        Box::pin(async move {
            let Err(error) = self.events.emit(event).await else { return Ok(()) };
            let failure = match &error.error {
                SessionFailure::Turn(failure) => failure.clone(),
                SessionFailure::AgentLoop { reason, message } => {
                    TurnFailure::Coded(CodedError::new(reason.as_str(), message.as_str()))
                }
            };
            self.raised = Some(error);
            Err(failure)
        })
    }
}

impl SessionProvider for TurnProvider {
    fn run<'a>(
        &'a self,
        input: SessionProviderInput,
        events: &'a mut dyn SessionEvents,
    ) -> BoxFuture<'a, Result<(), SessionProviderError>> {
        Box::pin(async move {
            // `for_turn` raises this when it is called; both callers run the provider at once.
            if self.turn_id.is_empty() {
                let error =
                    CodedError::new("internal_error", "Provider session TURN identity must be nonempty")
                        .with_error_type("ValueError");
                return Err(error.into());
            }
            let mut events = LoopEvents { events, raised: None };
            match turn_events(&self.adapter, &input, &self.turn_id, &mut events).await {
                Ok(()) => Ok(()),
                Err(failure) => Err(events.raised.take().unwrap_or_else(|| failure.into())),
            }
        })
    }
}

/// The narrator `for_retry` gives.
struct RetryProvider {
    adapter: Arc<ProviderSessionAdapter>,
    /// `model_id`: checked nonempty, as `for_retry` refuses an empty one.
    model_id: String,
    provider_input: SessionProviderInput,
    control: Arc<ProviderControl>,
}

impl RetryNarrator for RetryProvider {
    fn narrate<'a>(
        &'a self,
        input: RetryNarrationInput,
        stream: &'a mut dyn NarrationStream,
    ) -> BoxFuture<'a, Result<(), SessionFailure>> {
        Box::pin(async move {
            // `narrator`: any failure asks the provider to release what it keeps between requests.
            let outcome = self.narrate_retry(input, stream).await;
            if outcome.is_err() {
                release_provider_idle_soon(Arc::clone(&self.adapter.provider));
            }
            outcome
        })
    }
}

fn retry_texts() -> &'static Json {
    static TEXTS: OnceLock<Json> = OnceLock::new();
    TEXTS.get_or_init(|| serde_json::from_str(include_str!("retry_texts.json")).expect("exported texts are JSON"))
}

fn retry_text(key: &str) -> &'static str {
    retry_texts()[key].as_str().expect("an exported Retry control")
}

fn rejected(message: &str, code: &str) -> CodedError {
    CodedError::new(code, message).with_error_type("CommandRejected")
}

fn system(content: String) -> Tagged {
    let mut message = JsonMap::new();
    message.insert("role".into(), "system".into());
    message.insert("content".into(), content.into());
    Tagged::plain(message)
}

impl RetryProvider {
    /// `narrate(retry_input, stream)`.
    async fn narrate_retry(
        &self,
        input: RetryNarrationInput,
        stream: &mut dyn NarrationStream,
    ) -> Result<(), SessionFailure> {
        let failure = |error: CodedError| SessionFailure::Turn(TurnFailure::Coded(error));
        // `for_retry` raises this when it is called; its callers run the narrator at once.
        if self.model_id.is_empty() {
            return Err(failure(
                CodedError::new("internal_error", "Provider retry model identity must be nonempty")
                    .with_error_type("ValueError"),
            ));
        }
        let history = &self.provider_input.history;
        let receipt = input.action_receipt.as_ref();
        let basis = resolve_retry_basis_message(history, &input.failed_attempt_id).map_err(failure)?;
        let opening = basis.kind() == SessionMessageKind::Evidence
            && basis.content().get("kind").and_then(Json::as_str) == Some("campaign_start");
        if basis.kind() == SessionMessageKind::User && receipt.is_none() {
            return Err(failure(rejected(
                "Uncommitted player action requires a full mechanics-capable retry",
                "command_rejected",
            )));
        }
        let terminal = history.messages.iter().find(|message| {
            message.kind() == SessionMessageKind::Evidence
                && message.content().get("kind").and_then(Json::as_str) == Some("session_attempt_terminal")
                && message.content().get("attempt_id").and_then(Json::as_str) == Some(input.failed_attempt_id.as_str())
        });
        let exact_player_action =
            if basis.kind() == SessionMessageKind::User { player_turn_text(basis.content()) } else { String::new() };
        let recovery_kind = if receipt.is_some() {
            "post_mechanics_narration"
        } else if opening {
            "opening"
        } else {
            "uncommitted_player_action"
        };
        let action_receipt = receipt.map_or(Json::Null, |receipt| {
            json!({
                "campaign_id": receipt.campaign_id,
                "action_id": receipt.action_id,
                "status": receipt.status.as_str(),
                "result": receipt.result,
                "resulting_revision": receipt.resulting_revision,
            })
        });
        let safe_basis = json!({
            "campaign_id": input.campaign_id,
            "interruption_id": input.interruption_id,
            "retry_id": input.retry_id,
            "failed_attempt_id": input.failed_attempt_id,
            "canonical_high_water": input.canonical_high_water,
            "turn_id": input.turn_id,
            "input_id": basis.content().get("input_id").cloned().unwrap_or(Json::Null),
            "input_kind": basis.content().get("input_kind").cloned().unwrap_or(Json::Null),
            "original_player_action": exact_player_action,
            "recovery_kind": recovery_kind,
            "failure_category": terminal
                .and_then(|terminal| terminal.content().get("failure_category").cloned())
                .unwrap_or(Json::Null),
            "action_receipt": action_receipt,
        });
        let adapter = &self.adapter;
        let mut messages =
            adapter.messages_with(&self.provider_input, adapter.ranker().map_err(failure)?, None).await.map_err(failure)?;
        let sentence = retry_text(if opening { "retry_control_opening" } else { "retry_control_mechanic" });
        messages.push(system(format!(
            "{}{sentence}{}",
            retry_text("retry_control_prefix"),
            retry_text("retry_control_suffix")
        )));
        messages.push(system(format!(
            "{}{}",
            retry_text("retry_data_prefix"),
            pyjson::dumps(&safe_basis, Dump::CANONICAL)
        )));
        // `_stream_round(messages, [], control=retry_control, provider_input=provider_input)`.
        let mut round_input = self.provider_input.clone();
        round_input.control = Arc::clone(&self.control);
        let mut round =
            adapter.stream_round(messages, Vec::new(), &round_input, None, false).await.map_err(SessionFailure::Turn)?;
        let mut parts = Vec::new();
        while let Some(chunk) = round.next().await.map_err(SessionFailure::Turn)? {
            if chunk.tool_call_delta.is_some() {
                return Err(failure(rejected("Narration Retry cannot request package tools", "command_rejected")));
            }
            if !chunk.content.is_empty() {
                parts.push(chunk.content);
            }
        }
        let narration = parts.concat();
        if contains_serialized_tool_call(&narration) {
            return Err(failure(rejected(
                "Provider returned serialized tool syntax as narration",
                "provider_serialized_tool_call",
            )));
        }
        let narration = adapter.filter_private_deliberation(&narration);
        if narration.is_empty() {
            return Err(failure(rejected(
                "Provider completed Retry without player-visible narration",
                "provider_empty_completion",
            )));
        }
        stream.emit(ProseChannel::Talk, &narration, None).await.map_err(failure)
    }
}

impl ProviderSessionAdapter {
    /// `for_turn(turn_id)`: the session provider for one DM turn.
    pub fn for_turn(self: &Arc<Self>, turn_id: &str) -> Arc<dyn SessionProvider> {
        Arc::new(TurnProvider { adapter: Arc::clone(self), turn_id: turn_id.to_owned() })
    }

    /// `for_retry(model_id, provider_input=..., control=...)`: the narrator for one manual retry.
    pub fn for_retry(
        self: &Arc<Self>,
        model_id: &str,
        provider_input: SessionProviderInput,
        control: Arc<ProviderControl>,
    ) -> Arc<dyn RetryNarrator> {
        Arc::new(RetryProvider {
            adapter: Arc::clone(self),
            model_id: model_id.to_owned(),
            provider_input,
            control,
        })
    }
}
