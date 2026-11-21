//! The service handles the dispatch context holds, for the services whose crates are not ready
//! yet. Each is a small object-safe trait owned here: the lane that owns the service
//! implements it for its own type in `sylvia-owner` (which depends on that lane's crate), so
//! the owner's worker model does not wait for the service. Methods return
//! [`BoxFuture`](futures_util::future::BoxFuture) because `async fn` in a trait cannot be
//! called through `dyn`.
//!
//! The store needs no trait: lane 3's `sylvia_store::StoreHandle` is already a cheap clone
//! around the store thread's channel, so the context holds one directly.

use futures_util::future::BoxFuture;
use std::sync::Arc;
use sylvia_base::{CodedResult, Json};
use sylvia_domain::session::SessionRecord;
use sylvia_provider::error::ProviderError;
use sylvia_provider::provider::Provider;

/// The owner's provider resolver (lane 4): Python's `provider_resolver`, `model_choices` and
/// `reload_provider_resolver` closures in `owner_process.run`.
pub trait ProviderResolver: Send + Sync {
    /// `provider_resolver(session)`: the provider that answers this session.
    fn resolve<'a>(
        &'a self,
        session: &'a SessionRecord,
    ) -> BoxFuture<'a, Result<Arc<dyn Provider>, ProviderError>>;

    /// `list_model_choices()`.
    fn model_choices(&self) -> BoxFuture<'_, CodedResult<Vec<Json>>>;

    /// `reload_configured_provider()`: rebuild from `storage_root/config.yaml`.
    fn reload(&self) -> BoxFuture<'_, CodedResult<()>>;
}

/// A held provider-work reservation (Python's `provider_request_work` context manager). It is
/// released when dropped.
pub struct WorkGuard {
    _release: Box<dyn Send>,
}

impl WorkGuard {
    /// Wrap whatever releases the reservation when dropped.
    pub fn new(release: impl Send + 'static) -> Self {
        Self { _release: Box::new(release) }
    }
}

/// The campaign session loop (lane 4): Python's `CampaignSessionLoop`, as far as the run loop
/// itself uses it.
pub trait SessionLoop: Send + Sync {
    /// `session_loop.provider_request_work(session_id)`: reserve provider work for a
    /// session before its command runs as a task. Refusals use Python's codes and messages.
    fn provider_request_work(&self, session_id: &str) -> CodedResult<WorkGuard>;
}
