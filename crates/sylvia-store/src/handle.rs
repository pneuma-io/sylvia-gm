//! The store thread: one dedicated thread owns the [`AuthorityStore`] and every SQLite
//! connection it opens, and runs requests one at a time from a channel. That keeps the
//! single-writer behaviour the Python store relies on, while the rest of the owner stays on
//! tokio and awaits replies.

use crate::error::{StoreError, StoreResult};
use crate::lanes::OwnerLanes;
use crate::store::AuthorityStore;
use std::sync::mpsc;
use std::thread::JoinHandle;

type Job = Box<dyn FnOnce(&mut AuthorityStore) + Send>;

/// The store thread is gone (it panicked or was shut down).
pub const STORE_STOPPED: &str = "SQLite authority store thread has stopped";

/// A cloneable handle to the store thread. The owner's dispatch context holds one. Its clones
/// share the owner's lanes ([`OwnerLanes`]), as Python's one `AuthorityOwner` does.
#[derive(Debug, Clone)]
pub struct StoreHandle {
    jobs: mpsc::Sender<Job>,
    lanes: OwnerLanes,
    narration: std::sync::Arc<crate::narration::NarrationState>,
    tool_cache: std::sync::Arc<crate::tool_cache::ToolCache>,
    lifecycle: std::sync::Arc<crate::owner_lifecycle::OwnerLifecycle>,
}

/// Joins the store thread; returned once by [`StoreHandle::spawn`].
#[derive(Debug)]
pub struct StoreThread {
    thread: Option<JoinHandle<()>>,
}

impl StoreHandle {
    /// Start the store thread and open the store on it with `open`. Returns once opening has
    /// finished, with its error if it failed (the thread then exits).
    pub fn spawn<F>(open: F) -> StoreResult<(Self, StoreThread)>
    where
        F: FnOnce() -> StoreResult<AuthorityStore> + Send + 'static,
    {
        let (jobs, receiver) = mpsc::channel::<Job>();
        let tool_cache = std::sync::Arc::<crate::tool_cache::ToolCache>::default();
        let shared = std::sync::Arc::clone(&tool_cache);
        let (opened, opened_receiver) = mpsc::sync_channel::<StoreResult<()>>(1);
        let thread = std::thread::Builder::new()
            .name("sylvia-store".into())
            .spawn(move || {
                let mut store = match open() {
                    Ok(mut store) => {
                        store.share_tool_cache(shared);
                        let _ = opened.send(Ok(()));
                        store
                    }
                    Err(error) => {
                        let _ = opened.send(Err(error));
                        return;
                    }
                };
                while let Ok(job) = receiver.recv() {
                    job(&mut store);
                }
                store.close();
            })
            .map_err(StoreError::from)?;
        match opened_receiver.recv() {
            Ok(Ok(())) => {
                let handle = Self {
                    jobs,
                    lanes: OwnerLanes::default(),
                    narration: std::sync::Arc::default(),
                    tool_cache,
                    lifecycle: std::sync::Arc::default(),
                };
                Ok((handle, StoreThread { thread: Some(thread) }))
            }
            Ok(Err(error)) => {
                let _ = thread.join();
                Err(error)
            }
            Err(_) => {
                let _ = thread.join();
                Err(StoreError::Other(STORE_STOPPED.into()))
            }
        }
    }

    /// The owner's live narration and admin audit state (`AuthorityOwner._narration_lock` and
    /// what it guards), shared by every clone.
    pub fn narration(&self) -> &crate::narration::NarrationState {
        &self.narration
    }

    /// The owner's package tool caches (`AuthorityOwner._tools` and `_runtime_payloads`), the same
    /// ones the store thread's [`AuthorityStore::tool_cache`] holds.
    pub fn tool_cache(&self) -> &crate::tool_cache::ToolCache {
        &self.tool_cache
    }

    /// The owner's own lifecycle (`_lifecycle` and `_active_operations`), shared by every clone.
    pub fn lifecycle(&self) -> &std::sync::Arc<crate::owner_lifecycle::OwnerLifecycle> {
        &self.lifecycle
    }

    /// The owner's lane registries: `AuthorityOwner._lanes` and `SessionApplication._lanes`.
    pub fn lanes(&self) -> &OwnerLanes {
        &self.lanes
    }

    /// Run `operation` on the store thread and await its result.
    pub async fn call<R, F>(&self, operation: F) -> StoreResult<R>
    where
        R: Send + 'static,
        F: FnOnce(&mut AuthorityStore) -> StoreResult<R> + Send + 'static,
    {
        let (reply, receiver) = tokio::sync::oneshot::channel();
        self.jobs
            .send(Box::new(move |store| {
                let _ = reply.send(operation(store));
            }))
            .map_err(|_| StoreError::Other(STORE_STOPPED.into()))?;
        receiver.await.map_err(|_| StoreError::Other(STORE_STOPPED.into()))?
    }

    /// Run `operation` on the store thread and block until it finishes. For callers outside
    /// the tokio runtime (startup, tests, tools).
    pub fn call_blocking<R, F>(&self, operation: F) -> StoreResult<R>
    where
        R: Send + 'static,
        F: FnOnce(&mut AuthorityStore) -> StoreResult<R> + Send + 'static,
    {
        let (reply, receiver) = mpsc::sync_channel(1);
        self.jobs
            .send(Box::new(move |store| {
                let _ = reply.send(operation(store));
            }))
            .map_err(|_| StoreError::Other(STORE_STOPPED.into()))?;
        receiver.recv().map_err(|_| StoreError::Other(STORE_STOPPED.into()))?
    }
}

impl StoreThread {
    /// Wait for the store thread to finish. It finishes once every [`StoreHandle`] clone has
    /// been dropped; the store is then closed and its owner lock released.
    pub fn join(mut self) {
        if let Some(thread) = self.thread.take() {
            let _ = thread.join();
        }
    }
}
