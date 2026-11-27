//! `AuthorityOwner.close()`: the owner's shutdown.
//!
//! The owner stops admitting operations, waits for those running to finish, interrupts every
//! live narration attempt (its interruption saved and its cancel run once, as
//! `_expire_narration_locked` does), clears the in-memory narration state (attempts, pending
//! tools, the Full Audit trace and its pending usage), closes the store on its thread, and is
//! then closed, even when a shutdown step failed. Closing a closed owner returns at once; a
//! close that starts while another is closing waits for it.
//!
//! **Never call [`close_owner`] from inside an owner operation**, that is while holding an
//! [`sylvia_store::owner_lifecycle::OperationGuard`] (directly, or through any function that took
//! one): the close waits for every running operation, its caller's included, so it would wait on
//! itself, as Python's `close()` called inside `_operation()` would. The owner's shutdown path
//! (lane 5's) calls it from outside any held guard.

use super::actions::ActionError;
use super::narration_attempts::expire_locked;
use sylvia_store::owner_lifecycle::CloseStart;
use sylvia_store::StoreHandle;

/// `AuthorityOwner.close()`.
pub async fn close_owner(store: &StoreHandle) -> Result<(), ActionError> {
    if store.lifecycle().begin_close().await == CloseStart::Done {
        return Ok(());
    }
    let result = shut_down(store).await;
    store.lifecycle().finish_close();
    result
}

/// The `try` of `close()`: live attempts interrupted, narration state cleared, the store closed.
/// A failure leaves the store open, as Python's would skip `store.close()`.
async fn shut_down(store: &StoreHandle) -> Result<(), ActionError> {
    let narration = store.narration();
    {
        let _region = narration.region().await;
        let live: Vec<(String, String)> = narration.with(|inner| {
            inner
                .attempts
                .iter()
                .filter(|(_, attempt)| !attempt.terminal)
                .map(|(key, _)| key.clone())
                .collect()
        });
        for key in &live {
            expire_locked(store, key).await?;
        }
        narration.with(|inner| {
            inner.inspector_trace.clear();
            inner.pending_audit_usage.clear();
            inner.attempts.clear();
            inner.pending_tools.clear();
        });
    }
    store.close_store().await?;
    Ok(())
}
