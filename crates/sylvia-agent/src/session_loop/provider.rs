//! `SessionProvider = Callable[[SessionProviderInput], Iterable[ProviderEvent]]`
//! (`agent/_session_loop.py`): what the session loop runs for one provider turn. Both the GM's
//! provider session adapter and the Builder's session (lane 1's `builder`) are session providers.
//!
//! Python's provider is a generator the loop iterates. Here the provider is given the loop's
//! [`SessionEvents`] and awaits [`SessionEvents::emit`] where Python yields, so each event is
//! handled before the provider continues, as with the turn phases' `TurnEvents`.

use super::{ProviderEvent, SessionProviderInput};
use crate::provider_turn::TurnFailure;
use sylvia_base::CodedError;
use sylvia_provider::{BoxFuture, ProviderError};

/// A provider the session loop runs: Python's `provider(provider_input)`, consumed to the end.
pub trait SessionProvider: Send + Sync {
    /// Run one provider turn, emitting its events through `events`.
    fn run<'a>(
        &'a self,
        input: SessionProviderInput,
        events: &'a mut dyn SessionEvents,
    ) -> BoxFuture<'a, Result<(), SessionProviderError>>;
}

/// The session loop's side of the generator.
pub trait SessionEvents: Send {
    /// Handle one event. An error stops the provider there and is what the loop raises.
    fn emit(&mut self, event: ProviderEvent) -> BoxFuture<'_, Result<(), SessionProviderError>>;
}

/// How a session provider's turn failed.
#[derive(Debug)]
pub struct SessionProviderError {
    /// What the loop raises to the command, after recording the interrupted terminal.
    pub error: SessionFailure,
    /// `AgentLoopError.reason`, when the error is an `AgentLoopError` or its `__cause__` is one.
    /// The loop records it as the interrupted terminal's `reason_code`.
    pub loop_reason: Option<String>,
}

/// The exception a session provider raised.
#[derive(Debug)]
pub enum SessionFailure {
    /// A refusal or a provider failure, as the turn phases report them.
    Turn(TurnFailure),
    /// `AgentLoopError(reason)` itself (lane 1's `builder::runner`): `str(error)` is `message`,
    /// the reason's user message.
    AgentLoop { reason: String, message: String },
}

impl SessionProviderError {
    /// `AgentLoopError(reason)` with its user message, raised as itself.
    pub fn agent_loop(reason: impl Into<String>, message: impl Into<String>) -> Self {
        let reason = reason.into();
        Self {
            loop_reason: Some(reason.clone()),
            error: SessionFailure::AgentLoop { reason, message: message.into() },
        }
    }

    /// `raise error from loop_error`: `error`, with an `AgentLoopError` of `reason` as its cause.
    pub fn caused_by_loop(error: impl Into<TurnFailure>, reason: impl Into<String>) -> Self {
        Self { error: SessionFailure::Turn(error.into()), loop_reason: Some(reason.into()) }
    }
}

impl From<TurnFailure> for SessionProviderError {
    fn from(error: TurnFailure) -> Self {
        Self { error: SessionFailure::Turn(error), loop_reason: None }
    }
}

impl From<CodedError> for SessionProviderError {
    fn from(error: CodedError) -> Self {
        TurnFailure::Coded(error).into()
    }
}

impl From<ProviderError> for SessionProviderError {
    fn from(error: ProviderError) -> Self {
        TurnFailure::Provider(error).into()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    /// A sink that keeps what it is given and refuses the event after `limit` of them.
    struct Kept {
        events: Vec<ProviderEvent>,
        limit: usize,
    }

    impl SessionEvents for Kept {
        fn emit(&mut self, event: ProviderEvent) -> BoxFuture<'_, Result<(), SessionProviderError>> {
            Box::pin(async move {
                if self.events.len() == self.limit {
                    return Err(CodedError::rejected("Agent session provider work was cancelled").into());
                }
                self.events.push(event);
                Ok(())
            })
        }
    }

    /// Emits two events, then fails the way its `reason` says.
    struct Scripted {
        reason: Option<&'static str>,
    }

    impl Scripted {
        async fn turn(&self, events: &mut dyn SessionEvents) -> Result<(), SessionProviderError> {
            events.emit(ProviderEvent::SafeThink { text: "Checking the notice.".into() }).await?;
            events.emit(ProviderEvent::ProseComplete).await?;
            match self.reason {
                Some(reason) => Err(SessionProviderError::agent_loop(reason, "Builder stopped.")),
                None => Ok(()),
            }
        }
    }

    impl SessionProvider for Scripted {
        fn run<'a>(
            &'a self,
            _input: SessionProviderInput,
            events: &'a mut dyn SessionEvents,
        ) -> BoxFuture<'a, Result<(), SessionProviderError>> {
            Box::pin(self.turn(events))
        }
    }

    fn block_on<T>(future: impl std::future::Future<Output = T>) -> T {
        tokio::runtime::Builder::new_current_thread().build().expect("runtime").block_on(future)
    }

    #[test]
    fn events_arrive_in_order_and_a_refused_event_stops_the_provider() {
        let provider = Scripted { reason: None };
        let mut sink = Kept { events: Vec::new(), limit: 8 };
        block_on(provider.turn(&mut sink)).expect("the turn completes");
        assert_eq!(
            sink.events,
            vec![
                ProviderEvent::SafeThink { text: "Checking the notice.".into() },
                ProviderEvent::ProseComplete
            ]
        );
        let mut refusing = Kept { events: Vec::new(), limit: 1 };
        let error = block_on(provider.turn(&mut refusing)).expect_err("the second event is refused");
        assert_eq!(refusing.events.len(), 1);
        assert!(error.loop_reason.is_none());
        assert!(matches!(error.error, SessionFailure::Turn(TurnFailure::Coded(_))));
    }

    #[test]
    fn an_agent_loop_failure_carries_its_reason_raised_or_as_the_cause() {
        let provider = Scripted { reason: Some("repeated_reference_failure") };
        let mut sink = Kept { events: Vec::new(), limit: 8 };
        let error = block_on(provider.turn(&mut sink)).expect_err("the loop error");
        assert_eq!(error.loop_reason.as_deref(), Some("repeated_reference_failure"));
        assert!(matches!(
            &error.error,
            SessionFailure::AgentLoop { reason, message }
                if reason == "repeated_reference_failure" && message == "Builder stopped."
        ));
        let caused = SessionProviderError::caused_by_loop(
            CodedError::rejected("Builder stopped."),
            "required_reference_unavailable",
        );
        assert_eq!(caused.loop_reason.as_deref(), Some("required_reference_unavailable"));
        assert!(matches!(caused.error, SessionFailure::Turn(TurnFailure::Coded(_))));
    }
}
