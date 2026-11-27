//! `ProviderSessionAdapter.for_turn(turn_id)` and `for_retry(model_id, provider_input=...,
//! control=...)`: the session provider a DM turn runs and the narrator a manual retry runs.
//!
//! **A stub, with the session loop's** (`session_loop::campaign`, agreed with lanes 1 and 5):
//! the shapes are final, and running either answers `rust_port_pending` until T5 (the turn's
//! phases driven as a session provider) and T6 (the retry narrator) port them.

use super::context::ProviderSessionAdapter;
use crate::provider_turn::TurnFailure;
use crate::session_loop::campaign::{NarrationStream, RetryNarrator};
use crate::session_loop::{
    ProviderControl, SessionEvents, SessionFailure, SessionProvider, SessionProviderError,
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
    #[allow(dead_code)] // T5
    adapter: Arc<ProviderSessionAdapter>,
    #[allow(dead_code)] // T5
    turn_id: String,
}

impl SessionProvider for TurnProvider {
    fn run<'a>(
        &'a self,
        _input: SessionProviderInput,
        _events: &'a mut dyn SessionEvents,
    ) -> BoxFuture<'a, Result<(), SessionProviderError>> {
        Box::pin(async { Err(pending("ProviderSessionAdapter.for_turn").into()) })
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
