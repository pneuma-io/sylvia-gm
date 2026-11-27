//! `ProviderSessionAdapter.for_turn(turn_id)` and `for_retry(model_id, provider_input=...,
//! control=...)`: the session provider a DM turn runs and the narrator a manual retry runs.
//!
//! - **`for_turn`** runs `_turn_events` (`provider_turn::turn`, T5a-1) with the loop's events.
//! - **`for_retry`** is still a stub: running it answers `rust_port_pending` until T6.

use super::context::ProviderSessionAdapter;
use crate::provider_turn::turn::turn_events;
use crate::provider_turn::{TurnEvents, TurnFailure, TurnResult};
use crate::session_loop::campaign::{NarrationStream, RetryNarrator};
use crate::session_loop::{
    ProviderControl, ProviderEvent, SessionEvents, SessionFailure, SessionProvider, SessionProviderError,
    SessionProviderInput,
};
use std::sync::Arc;
use sylvia_base::CodedError;
use sylvia_domain::authority::RetryNarrationInput;
use sylvia_provider::factory::PORT_PENDING;
use sylvia_provider::BoxFuture;

fn pending(method: &str) -> CodedError {
    CodedError::new(
        PORT_PENDING,
        format!("The Rust owner does not run agent/_provider_session.py {method} yet."),
    )
}

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
                let error = CodedError::new("internal_error", "Provider session TURN identity must be nonempty")
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
    #[allow(dead_code)] // T6
    adapter: Arc<ProviderSessionAdapter>,
    #[allow(dead_code)] // T6
    model_id: String,
    #[allow(dead_code)] // T6
    provider_input: SessionProviderInput,
    #[allow(dead_code)] // T6
    control: Arc<ProviderControl>,
}

impl RetryNarrator for RetryProvider {
    fn narrate<'a>(
        &'a self,
        _input: RetryNarrationInput,
        _stream: &'a mut dyn NarrationStream,
    ) -> BoxFuture<'a, Result<(), SessionFailure>> {
        Box::pin(async {
            Err(SessionFailure::Turn(TurnFailure::Coded(pending("ProviderSessionAdapter.for_retry"))))
        })
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
