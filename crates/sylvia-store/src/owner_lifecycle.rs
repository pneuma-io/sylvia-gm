//! `AuthorityOwner`'s own lifecycle: `_lifecycle` (`open`, `closing`, `closed`) and
//! `_active_operations` under `_lifecycle_condition`, with `_operation()` and `_require_open()`.
//!
//! Every owner operation holds an [`OperationGuard`] for its whole run ([`StoreHandle::operation`],
//! Python's `with self._operation():`), taken while the owner is open; once the owner is closing
//! or closed it is refused with Python's `RuntimeError` message, and never counted. Closing
//! (`sylvia_app::authority::owner_close::close_owner`) waits for the counted operations to finish,
//! then runs its shutdown work, then marks the owner closed; a second close returns at once, and
//! one that starts while another is closing waits for it.
//!
//! Python's refusal is an uncaught `RuntimeError`, so here it is a store fault carrying Python's
//! message: the owner answers `internal_error`, as it does for Python's.

use crate::error::{StoreError, StoreResult};
use crate::handle::StoreHandle;
use std::sync::{Arc, Mutex, PoisonError};

/// Python's refusal of an operation on a closing or closed owner.
pub const OWNER_CLOSED: &str = "Application authority owner is closed";

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
enum Phase {
    Open,
    Closing,
    Closed,
}

#[derive(Debug)]
struct State {
    phase: Phase,
    active: usize,
}

/// The owner's lifecycle, shared by every clone of its store handle.
#[derive(Debug)]
pub struct OwnerLifecycle {
    state: Mutex<State>,
    changed: tokio::sync::Notify,
}

impl Default for OwnerLifecycle {
    fn default() -> Self {
        Self {
            state: Mutex::new(State { phase: Phase::Open, active: 0 }),
            changed: tokio::sync::Notify::new(),
        }
    }
}

/// One counted owner operation; it ends when the guard drops.
#[derive(Debug)]
pub struct OperationGuard {
    lifecycle: Arc<OwnerLifecycle>,
}

impl Drop for OperationGuard {
    fn drop(&mut self) {
        let mut state = self.lifecycle.state.lock().unwrap_or_else(PoisonError::into_inner);
        state.active -= 1;
        if state.active == 0 {
            self.lifecycle.changed.notify_waiters();
        }
    }
}

/// How [`OwnerLifecycle::begin_close`] found the owner.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CloseStart {
    /// This caller closes the owner: no operation is running any more.
    Proceed,
    /// The owner was closed already, or another caller has just finished closing it.
    Done,
}

fn closed() -> StoreError {
    StoreError::Other(OWNER_CLOSED.to_owned())
}

impl OwnerLifecycle {
    fn lock(&self) -> std::sync::MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(PoisonError::into_inner)
    }

    /// `_operation()`: count one operation while the owner is open.
    pub fn operation(self: &Arc<Self>) -> StoreResult<OperationGuard> {
        let mut state = self.lock();
        if state.phase != Phase::Open {
            return Err(closed());
        }
        state.active += 1;
        Ok(OperationGuard { lifecycle: Arc::clone(self) })
    }

    /// `_require_open()`.
    pub fn ensure_open(&self) -> StoreResult<()> {
        if self.lock().phase == Phase::Open {
            Ok(())
        } else {
            Err(closed())
        }
    }

    /// Whether operations are still admitted.
    pub fn is_open(&self) -> bool {
        self.lock().phase == Phase::Open
    }

    /// `close()`'s first half: return at once when closed, wait for a close already under way,
    /// or mark the owner closing and wait until no counted operation is running.
    pub async fn begin_close(&self) -> CloseStart {
        let mut proceeding = false;
        loop {
            let notified = self.changed.notified();
            tokio::pin!(notified);
            notified.as_mut().enable();
            {
                let mut state = self.lock();
                match (state.phase, proceeding) {
                    (Phase::Closed, _) => return CloseStart::Done,
                    (Phase::Open, _) => {
                        state.phase = Phase::Closing;
                        proceeding = true;
                        if state.active == 0 {
                            return CloseStart::Proceed;
                        }
                    }
                    (Phase::Closing, true) if state.active == 0 => return CloseStart::Proceed,
                    // Another caller is closing (wait for `Closed`), or operations still run.
                    (Phase::Closing, _) => {}
                }
            }
            notified.await;
        }
    }

    /// `close()`'s `finally`: the owner is closed, whatever its shutdown work did.
    pub fn finish_close(&self) {
        self.lock().phase = Phase::Closed;
        self.changed.notify_waiters();
    }
}

impl StoreHandle {
    /// `with self._operation():` for one owner operation: refused once the owner is closing.
    /// Hold the guard for the operation's whole run.
    pub fn operation(&self) -> StoreResult<OperationGuard> {
        self.lifecycle().operation()
    }

    /// `_require_open()`.
    pub fn ensure_open(&self) -> StoreResult<()> {
        self.lifecycle().ensure_open()
    }

    /// The store's own `close()` on its thread; later store calls refuse as Python's closed
    /// store does.
    pub async fn close_store(&self) -> StoreResult<()> {
        self.call(|store| {
            store.close();
            Ok(())
        })
        .await
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[tokio::test]
    async fn close_waits_for_operations_and_refuses_new_ones() {
        let lifecycle = Arc::new(OwnerLifecycle::default());
        let held = lifecycle.operation().unwrap();
        let nested = lifecycle.operation().unwrap();
        let closing = {
            let lifecycle = Arc::clone(&lifecycle);
            tokio::spawn(async move { lifecycle.begin_close().await })
        };
        tokio::task::yield_now().await;
        tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        assert!(!closing.is_finished(), "close waits for the running operations");
        // Python's `_operation` during the drain: refused, and not counted.
        assert_eq!(lifecycle.operation().unwrap_err().to_string(), OWNER_CLOSED);
        assert_eq!(lifecycle.ensure_open().unwrap_err().to_string(), OWNER_CLOSED);
        drop(nested);
        tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        assert!(!closing.is_finished(), "one operation still runs");
        drop(held);
        assert_eq!(closing.await.unwrap(), CloseStart::Proceed);
        let second = {
            let lifecycle = Arc::clone(&lifecycle);
            tokio::spawn(async move { lifecycle.begin_close().await })
        };
        tokio::time::sleep(std::time::Duration::from_millis(20)).await;
        assert!(!second.is_finished(), "a second close waits for the first to finish");
        lifecycle.finish_close();
        assert_eq!(second.await.unwrap(), CloseStart::Done);
        assert_eq!(lifecycle.begin_close().await, CloseStart::Done, "a closed owner closes at once");
        assert_eq!(lifecycle.operation().unwrap_err().to_string(), OWNER_CLOSED);
    }
}
