//! The GM request assembly's mode, from `agent/_context_ledger.py`: the parts providers read.
//!
//! GM play assembles its request in the legacy way or through the context ledger. The mode is
//! `SYLVIA_GM_CONTEXT_MODE` when it names one, else the route's shipped default. Providers read
//! it here (they cannot depend on the agent crate); the ledger itself is `sylvia-agent`'s, which
//! re-exports this module.
//!
//! [`LEDGER_REQUEST`] is Python's task-scoped `ContextVar`: the ledger marks the one request it
//! has just built, and a provider reads (and the ChatGPT runtime clears) the mark. Here it is a
//! tokio task-local, opened per provider round by [`with_ledger_request_scope`], as each Python
//! round runs in its own task with a copy of the context. Outside a scope it reads as unset and
//! a set does nothing, which is the `ContextVar`'s default `False`.

use crate::config::env::Environment;
use std::cell::RefCell;
use std::future::Future;
use sylvia_base::pystr;

/// `MODE_ENV`.
pub const MODE_ENV: &str = "SYLVIA_GM_CONTEXT_MODE";
/// `LEGACY_MODE`.
pub const LEGACY_MODE: &str = "legacy";
/// `LEDGER_MODE`.
pub const LEDGER_MODE: &str = "ledger";
/// `DEFAULT_CONTEXT_MODE`.
pub const DEFAULT_CONTEXT_MODE: &str = LEGACY_MODE;
/// `ANTHROPIC_API_ROUTE`: the first-party Claude API connection.
pub const ANTHROPIC_API_ROUTE: &str = "anthropic_api";
/// `DEFAULT_CONTEXT_MODE_BY_TRANSPORT`: the routes that ship with the context ledger.
pub const DEFAULT_CONTEXT_MODE_BY_TRANSPORT: [(&str, &str); 3] =
    [("claude_code", LEDGER_MODE), ("codex_app_server", LEDGER_MODE), (ANTHROPIC_API_ROUTE, LEDGER_MODE)];
/// `CONTINUE_LABEL`.
pub const CONTINUE_LABEL: &str = "SYLVIA_GM_CONTINUE";
/// `CONTINUE_RECORD_TEXT`: the continue record's text after its seal.
pub const CONTINUE_RECORD_TEXT: &str = "SYLVIA_GM_CONTINUE (application invocation, not player speech): Continue the \
                                        response to the saved Sylvia conversation, using the committed tool results \
                                        above.";

/// `gm_route(transport, provider_key)`: the route the shipped defaults are keyed by, the
/// transport or a named connection.
pub fn gm_route(transport: Option<&str>, provider_key: Option<&str>) -> String {
    match (provider_key.unwrap_or(""), transport.unwrap_or("")) {
        ("anthropic", "anthropic_messages") => ANTHROPIC_API_ROUTE.into(),
        (_, transport) => transport.into(),
    }
}

/// `context_mode_setting(environ)`: the mode `SYLVIA_GM_CONTEXT_MODE` names, else `None`.
pub fn context_mode_setting(environment: &Environment) -> Option<String> {
    environment
        .get(MODE_ENV)
        .and_then(Result::ok)
        .map(|value| pystr::casefold(pystr::strip(value)))
        .filter(|value| value == LEGACY_MODE || value == LEDGER_MODE)
}

/// `context_mode(environ, transport=..., provider_key=...)`: the setting for every route, else
/// the route's shipped default.
pub fn context_mode(
    environment: &Environment,
    transport: Option<&str>,
    provider_key: Option<&str>,
) -> String {
    if let Some(setting) = context_mode_setting(environment) {
        return setting;
    }
    let route = gm_route(transport, provider_key);
    DEFAULT_CONTEXT_MODE_BY_TRANSPORT
        .iter()
        .find(|(name, _)| *name == route)
        .map_or(DEFAULT_CONTEXT_MODE, |(_, mode)| mode)
        .to_owned()
}

tokio::task_local! {
    /// `LEDGER_REQUEST`: `None` is Python's `False`; a mark is the exchange id.
    static LEDGER_REQUEST: RefCell<Option<String>>;
}

/// Run `future` with its own, unset ledger-request mark.
pub async fn with_ledger_request_scope<F: Future>(future: F) -> F::Output {
    LEDGER_REQUEST.scope(RefCell::new(None), future).await
}

/// `LEDGER_REQUEST.set(value)`; outside a scope, nothing.
pub fn set_ledger_request(value: Option<String>) {
    let _ = LEDGER_REQUEST.try_with(|mark| *mark.borrow_mut() = value);
}

/// `LEDGER_REQUEST.get()`.
pub fn ledger_request() -> Option<String> {
    LEDGER_REQUEST.try_with(|mark| mark.borrow().clone()).ok().flatten()
}

/// `get()` then `set(False)`: the ChatGPT runtime's one-request read.
pub fn take_ledger_request() -> Option<String> {
    LEDGER_REQUEST.try_with(|mark| mark.borrow_mut().take()).ok().flatten()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn routes_and_settings_choose_the_mode() {
        let none = Environment::default();
        assert_eq!(context_mode(&none, Some("chat_completions"), Some("openai")), LEGACY_MODE);
        assert_eq!(context_mode(&none, Some("claude_code"), None), LEDGER_MODE);
        assert_eq!(context_mode(&none, Some("codex_app_server"), None), LEDGER_MODE);
        assert_eq!(context_mode(&none, Some("anthropic_messages"), Some("anthropic")), LEDGER_MODE);
        assert_eq!(context_mode(&none, Some("anthropic_messages"), Some("openrouter")), LEGACY_MODE);
        assert_eq!(context_mode(&none, None, None), LEGACY_MODE);
        let ledger = Environment::of(&[(MODE_ENV, "  LEDGER ")]);
        assert_eq!(context_mode(&ledger, Some("chat_completions"), None), LEDGER_MODE);
        let legacy = Environment::of(&[(MODE_ENV, "legacy")]);
        assert_eq!(context_mode(&legacy, Some("claude_code"), None), LEGACY_MODE);
        let other = Environment::of(&[(MODE_ENV, "fast")]);
        assert_eq!(context_mode(&other, Some("claude_code"), None), LEDGER_MODE);
        assert_eq!(context_mode_setting(&other), None);
    }

    #[tokio::test]
    async fn the_ledger_mark_lives_in_its_scope() {
        set_ledger_request(Some("ignored".into()));
        assert_eq!(ledger_request(), None);
        with_ledger_request_scope(async {
            assert_eq!(ledger_request(), None);
            set_ledger_request(Some("audit-1".into()));
            assert_eq!(ledger_request().as_deref(), Some("audit-1"));
            assert_eq!(take_ledger_request().as_deref(), Some("audit-1"));
            assert_eq!(ledger_request(), None);
        })
        .await;
    }
}
