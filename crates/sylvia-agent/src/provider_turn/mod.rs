//! The GM provider turn's shared state, its decision points and its phases.
//!
//! - [`state`]: `agent/_provider_turn_state.py`, the turn locals.
//! - [`decisions`]: `agent/_provider_turn_decisions.py`, the pure questions the phases ask.
//! - [`round`]: one provider round (`prepare_round_catalog`, `prepare_provider_round`,
//!   `stream_provider_round`).
//! - [`tool_checks`]: the tool batch's checks, refusals and settled results (T3a).
//! - [`tool_batch`]: one round's tool batch, admitted and run (T3b-1).
//!
//! Python's phases are generators the session loop consumes: each event is handled before the
//! phase resumes. Here a phase is given a [`TurnEvents`] sink and awaits it where Python yields,
//! so the handling happens at the same point.

pub mod decisions;
pub mod round;
pub mod state;
pub mod tool_batch;
pub mod tool_checks;

use crate::session_loop::ProviderEvent;
use state::Unbound;
use sylvia_base::CodedError;
use sylvia_provider::error::ProviderError;
use sylvia_provider::BoxFuture;

/// How a turn phase fails: an owner refusal or fault (`CommandRejected` and the rest, as the
/// owner reports them), or a provider error that reaches the provider-error reply.
#[derive(Debug, Clone)]
pub enum TurnFailure {
    Coded(CodedError),
    Provider(ProviderError),
}

impl From<CodedError> for TurnFailure {
    fn from(error: CodedError) -> Self {
        Self::Coded(error)
    }
}

impl From<ProviderError> for TurnFailure {
    fn from(error: ProviderError) -> Self {
        Self::Provider(error)
    }
}

impl From<Unbound> for TurnFailure {
    fn from(error: Unbound) -> Self {
        Self::Coded(error.into())
    }
}

/// The result of a turn phase.
pub type TurnResult<T> = Result<T, TurnFailure>;

/// Where a phase's events go: the session loop handles each one before the phase continues
/// (Python's generator and its consumer).
pub trait TurnEvents: Send {
    /// Handle one event; an error stops the phase there.
    fn emit(&mut self, event: ProviderEvent) -> BoxFuture<'_, TurnResult<()>>;
}
