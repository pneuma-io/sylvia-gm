//! The provider hook system from `provider/_hooks.py`: `HookContext`, the `ProviderHook`
//! callbacks and the `HookManager` every provider carries.
//!
//! Hooks fire in registration order; a failing hook is logged and never stops the others or
//! the request. Python's built-in hooks (logging, metrics, dump, usage tracking, diagnostics)
//! and task-scoped hooks (`HookManager.scoped`, used by the admin audit) are ported with their
//! consumers.

use crate::error::ProviderError;
use crate::provider::BoxFuture;
use crate::types::ProviderResponse;
use std::sync::{Arc, Mutex};
use sylvia_base::JsonMap;

/// `HookContext`: one immutable snapshot of a provider call.
#[derive(Debug, Clone, Default, PartialEq)]
pub struct HookContext {
    pub provider_key: String,
    pub model_id: String,
    pub message_count: usize,
    pub tool_count: usize,
    /// The provider-specific request (`format_request`'s dict).
    pub request_json: Option<JsonMap>,
    pub response: Option<ProviderResponse>,
    /// The error, as the provider raises it. Python passes the raw exception, which for an
    /// accidental failure in a stream is, say, a `JSONDecodeError`; here it is already the
    /// `ProviderConnectionError` Python turns it into. No hook in the snapshot reads more than
    /// `retry_attempt` from an error context except the never-enabled `DumpHook`.
    pub error: Option<ProviderError>,
    pub duration_ms: f64,
    pub http_status: Option<u16>,
    pub response_headers: Vec<(String, String)>,
    pub retry_attempt: i64,
    pub timestamp_iso: String,
    pub stream: bool,
}

/// `ProviderHook`: implement any of the three callbacks. An `Err` is a hook failure, logged as
/// Python logs an exception from a hook.
pub trait ProviderHook: Send + Sync {
    /// The hook's class name, for the failure warning.
    fn name(&self) -> &str;

    /// Before the provider sends a request.
    fn on_request<'a>(&'a self, _context: &'a HookContext) -> BoxFuture<'a, Result<(), String>> {
        Box::pin(async { Ok(()) })
    }

    /// After a successful response.
    fn on_response<'a>(&'a self, _context: &'a HookContext) -> BoxFuture<'a, Result<(), String>> {
        Box::pin(async { Ok(()) })
    }

    /// When the call fails.
    fn on_error<'a>(&'a self, _context: &'a HookContext) -> BoxFuture<'a, Result<(), String>> {
        Box::pin(async { Ok(()) })
    }
}

/// `HookManager` (with its `HookRegistry`): the ordered hooks of one provider.
#[derive(Default)]
pub struct HookManager {
    hooks: Mutex<Vec<Arc<dyn ProviderHook>>>,
}

impl std::fmt::Debug for HookManager {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let names: Vec<String> = self.hooks().iter().map(|hook| hook.name().to_owned()).collect();
        f.debug_struct("HookManager").field("hooks", &names).finish()
    }
}

#[derive(Clone, Copy)]
enum Callback {
    Request,
    Response,
    Error,
}

impl Callback {
    fn name(self) -> &'static str {
        match self {
            Self::Request => "on_request",
            Self::Response => "on_response",
            Self::Error => "on_error",
        }
    }
}

impl HookManager {
    /// No hooks.
    pub fn new() -> Self {
        Self::default()
    }

    /// Append a hook unless the same hook is already registered.
    pub fn register(&self, hook: Arc<dyn ProviderHook>) {
        let mut hooks = self.hooks.lock().expect("hook lock");
        if !hooks.iter().any(|existing| Arc::ptr_eq(existing, &hook)) {
            hooks.push(hook);
        }
    }

    /// Remove a hook; a hook that is not registered is ignored.
    pub fn deregister(&self, hook: &Arc<dyn ProviderHook>) {
        self.hooks.lock().expect("hook lock").retain(|existing| !Arc::ptr_eq(existing, hook));
    }

    /// The registered hooks, in order.
    pub fn hooks(&self) -> Vec<Arc<dyn ProviderHook>> {
        self.hooks.lock().expect("hook lock").clone()
    }

    async fn fire(&self, callback: Callback, context: &HookContext) {
        for hook in self.hooks() {
            let outcome = match callback {
                Callback::Request => hook.on_request(context).await,
                Callback::Response => hook.on_response(context).await,
                Callback::Error => hook.on_error(context).await,
            };
            if let Err(message) = outcome {
                crate::log::warning(&format!("Hook {}.{} failed: {message}", hook.name(), callback.name()));
            }
        }
    }

    /// `fire_on_request(ctx)`.
    pub async fn fire_on_request(&self, context: &HookContext) {
        self.fire(Callback::Request, context).await;
    }

    /// `fire_on_response(ctx)`.
    pub async fn fire_on_response(&self, context: &HookContext) {
        self.fire(Callback::Response, context).await;
    }

    /// `fire_on_error(ctx)`.
    pub async fn fire_on_error(&self, context: &HookContext) {
        self.fire(Callback::Error, context).await;
    }
}

/// `datetime.now(UTC).isoformat()`: `YYYY-MM-DDTHH:MM:SS[.ffffff]+00:00`, the fraction left out
/// when the microseconds are zero.
pub fn utc_now_isoformat() -> String {
    let since = std::time::SystemTime::now().duration_since(std::time::UNIX_EPOCH).unwrap_or_default();
    isoformat_utc(i64::try_from(since.as_secs()).unwrap_or(0), since.subsec_micros())
}

/// [`utc_now_isoformat`] for a given instant.
pub fn isoformat_utc(seconds: i64, micros: u32) -> String {
    let days = seconds.div_euclid(86_400);
    let rest = seconds.rem_euclid(86_400);
    // Civil date from days since the epoch (Howard Hinnant's algorithm).
    let z = days + 719_468;
    let era = z.div_euclid(146_097);
    let doe = z - era * 146_097;
    let yoe = (doe - doe / 1460 + doe / 36_524 - doe / 146_096) / 365;
    let doy = doe - (365 * yoe + yoe / 4 - yoe / 100);
    let mp = (5 * doy + 2) / 153;
    let day = doy - (153 * mp + 2) / 5 + 1;
    let month = if mp < 10 { mp + 3 } else { mp - 9 };
    let year = yoe + era * 400 + i64::from(month <= 2);
    let (hour, minute, second) = (rest / 3600, rest % 3600 / 60, rest % 60);
    let fraction = if micros == 0 { String::new() } else { format!(".{micros:06}") };
    format!("{year:04}-{month:02}-{day:02}T{hour:02}:{minute:02}:{second:02}{fraction}+00:00")
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn isoformat_matches_python() {
        // datetime.fromtimestamp(1795264215.25, UTC).isoformat()
        assert_eq!(isoformat_utc(1_795_264_215, 250_000), "2026-11-21T12:30:15.250000+00:00");
        assert_eq!(isoformat_utc(0, 0), "1970-01-01T00:00:00+00:00");
        assert_eq!(isoformat_utc(951_782_400, 1), "2000-02-29T00:00:00.000001+00:00");
    }

    struct Failing;
    impl ProviderHook for Failing {
        fn name(&self) -> &str {
            "Failing"
        }
        fn on_request<'a>(&'a self, _: &'a HookContext) -> BoxFuture<'a, Result<(), String>> {
            Box::pin(async { Err("ValueError: no".into()) })
        }
    }

    #[tokio::test]
    async fn a_failing_hook_does_not_stop_the_call() {
        let manager = HookManager::new();
        let hook: Arc<dyn ProviderHook> = Arc::new(Failing);
        manager.register(Arc::clone(&hook));
        manager.register(Arc::clone(&hook));
        assert_eq!(manager.hooks().len(), 1);
        manager.fire_on_request(&HookContext::default()).await;
        manager.deregister(&hook);
        assert!(manager.hooks().is_empty());
    }
}
