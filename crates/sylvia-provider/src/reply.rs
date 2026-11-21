//! The owner's player-facing sentence for a provider error.
//!
//! Python builds it in `interfaces/owner_process.py` (the `except (..., ProviderError, ...)`
//! branch of the command loop). Lane 5's owner calls [`provider_error_reply`] instead of keeping
//! a second copy. The checks run in the same order as Python's `isinstance` chain, because a
//! subclass must meet its own branch before its parent's.

use crate::error::{ErrorKind, ProviderError};
use crate::pyfmt::format_g;

/// The owner's sentence for an internal fault (any exception that is not a provider error).
pub const INTERNAL_ERROR_MESSAGE: &str = "Sylvia encountered an internal error, not a confirmed provider \
     failure. Previously saved work is kept. Try the action again; \
     if it repeats, report this error with the action you were taking.";

/// The `code` and `message` of the owner's error reply for a provider error. For
/// `internal_error` the owner's reply has no `diagnostics` member.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ProviderErrorReply {
    /// `provider_error`, or `context_compaction_failed` for a `ContextCompactionError`.
    pub code: &'static str,
    /// The sentence the player reads.
    pub message: String,
}

/// Build the reply for `error`.
///
/// `submitted_message_id` is `params.get("message_id")` when the command is
/// `submit_session_input`, and `None` for every other command. When it equals the error's
/// `accepted_message_id`, the message starts with "Your message was saved. ".
pub fn provider_error_reply(error: &ProviderError, submitted_message_id: Option<&str>) -> ProviderErrorReply {
    if error.kind == ErrorKind::Internal {
        // The owner's `except Exception` branch: no prefix, and its reply has no `diagnostics`.
        return ProviderErrorReply { code: "internal_error", message: INTERNAL_ERROR_MESSAGE.into() };
    }
    let is = |kind| error.is(kind);
    let mut code = "provider_error";
    let mut message: String = if is(ErrorKind::ContextCompaction) {
        code = "context_compaction_failed";
        error.message.clone()
    } else if is(ErrorKind::TokenLimit) {
        error.message.clone()
    } else if is(ErrorKind::ContinuationRequired) {
        "The model requires a native continuation that Sylvia cannot perform \
         automatically. Previously saved work is kept. Choose a smaller step \
         or another model."
            .into()
    } else if is(ErrorKind::Refusal) {
        "The model declined this request. Previously saved work is kept. \
         Revise the request or choose another model."
            .into()
    } else if is(ErrorKind::Paused) {
        "The provider paused for a server-tool continuation that Sylvia does \
         not support. Previously saved work is kept. Choose a model or API \
         configuration that does not require server tools."
            .into()
    } else if is(ErrorKind::OutputLimit) {
        "The model reached its response limit. Saved work is kept. \
         Continue with a smaller step or increase the supported response budget."
            .into()
    } else if is(ErrorKind::Timeout) {
        "The selected provider timed out while responding".into()
    } else if is(ErrorKind::ModelCapacity) {
        "The selected model is at capacity. Saved work is kept. \
         Try again later or choose another model for this chat."
            .into()
    } else if is(ErrorKind::Authentication) {
        "The provider rejected authentication. Sign in again or check the API key \
         for this connection in Settings. Saved work is kept."
            .into()
    } else if is(ErrorKind::RateLimit) {
        let mut text = String::from(
            "The provider reported a rate or usage limit. Check your provider's \
             allowance or try again later. Saved work is kept.",
        );
        if let Some(delay) = error.retry_after {
            if 0.0 < delay && delay < 604_800.0 {
                text.push_str(&format!(" Retry delay: {} seconds.", format_g(delay)));
            }
        }
        text
    } else if is(ErrorKind::ModelNotFound) {
        "The selected model was not found or is unavailable to this connection. \
         Check the model selection in Settings. Saved work is kept."
            .into()
    } else if is(ErrorKind::Connection) {
        "Sylvia could not connect to the provider. Check the connection address, \
         network, and whether your local model server is running, then retry. \
         Saved work is kept."
            .into()
    } else if is(ErrorKind::InvalidRequest) {
        "The provider rejected the request. Check this connection's model and \
         API settings. Previously saved work is kept. If it repeats, report \
         the action you were taking."
            .into()
    } else if is(ErrorKind::IncompleteStream) {
        "The provider's response ended before completion. Previously saved work \
         is kept. Retry or continue the interrupted action."
            .into()
    } else if is(ErrorKind::InvalidToolArguments) {
        "The model returned invalid tool arguments, so Sylvia could not apply \
         that operation. Previously saved work is kept. Retry or ask the model \
         to continue with a smaller step."
            .into()
    } else if is(ErrorKind::InvalidResponse) {
        "The provider returned an invalid response that Sylvia could not use. \
         Previously saved work is kept. Retry; if it repeats, check the \
         connection's API format and model support in Settings."
            .into()
    } else if is(ErrorKind::Unavailable) {
        "The provider is unavailable or could not complete the response. \
         Previously saved work is kept. Try again later."
            .into()
    } else {
        "The provider could not complete the request; no specific cause was \
         reported. Previously saved work is kept. Retry, and report the action \
         you were taking if it repeats."
            .into()
    };
    if let (Some(accepted), Some(submitted)) = (error.accepted_message_id.as_deref(), submitted_message_id) {
        if accepted == submitted {
            message = format!("Your message was saved. {message}");
        }
    }
    ProviderErrorReply { code, message }
}
