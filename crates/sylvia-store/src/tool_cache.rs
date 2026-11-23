//! The owner's package tool caches, `AuthorityOwner._tools` and `_runtime_payloads` under
//! `_tools_lock`: one instance per owner, shared by [`crate::StoreHandle::tool_cache`] and the
//! store thread's [`crate::AuthorityStore::tool_cache`].
//!
//! `_package_tool` keeps each tool it materializes, keyed by its exact package identity and tool
//! name, for the owner's lifetime (`_tools` has no bound, as in Python), and the verified runtime
//! bytes of the last 16 package identities it read (`_runtime_payloads`, the oldest dropped
//! first). Package identities are immutable, so a cached tool is the one a fresh
//! materialization would give. The tools are `sylvia-app`'s `CatalogTool`s, held here as
//! [`Arc<dyn Any + Send + Sync>`] because this crate cannot name them.

use std::any::Any;
use std::collections::{HashMap, VecDeque};
use std::sync::{Arc, Mutex, PoisonError};

/// `(package_id, version, sha256, tool name)`: a tool's exact identity.
pub type ToolIdentity = (String, String, String, String);

/// `(package_id, version, sha256)`: a package's exact identity.
pub type PackageIdentity = (String, String, String);

/// How many packages' runtime bytes are kept (`len(self._runtime_payloads) >= 16`).
pub const RUNTIME_PAYLOADS_KEPT: usize = 16;

/// What `_tools_lock` guards.
#[derive(Default)]
pub struct ToolCacheInner {
    /// `_tools`.
    pub tools: HashMap<ToolIdentity, Arc<dyn Any + Send + Sync>>,
    /// `_runtime_payloads`, oldest first.
    pub runtime_payloads: VecDeque<(PackageIdentity, Arc<Vec<u8>>)>,
}

impl std::fmt::Debug for ToolCacheInner {
    fn fmt(&self, formatter: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        formatter
            .debug_struct("ToolCacheInner")
            .field("tools", &self.tools.len())
            .field("runtime_payloads", &self.runtime_payloads.len())
            .finish()
    }
}

impl ToolCacheInner {
    /// `self._runtime_payloads.get(identity)`.
    pub fn runtime_payload(&self, identity: &PackageIdentity) -> Option<Arc<Vec<u8>>> {
        self.runtime_payloads.iter().find(|(known, _)| known == identity).map(|(_, bytes)| Arc::clone(bytes))
    }

    /// Keep one package's verified runtime bytes, dropping the oldest when 16 are kept.
    pub fn keep_runtime_payload(&mut self, identity: PackageIdentity, bytes: Arc<Vec<u8>>) {
        if self.runtime_payloads.len() >= RUNTIME_PAYLOADS_KEPT {
            self.runtime_payloads.pop_front();
        }
        self.runtime_payloads.push_back((identity, bytes));
    }
}

/// The owner's tool caches, under one lock.
#[derive(Debug, Default)]
pub struct ToolCache {
    inner: Mutex<ToolCacheInner>,
}

impl ToolCache {
    /// Run `f` under `_tools_lock`. Python holds it through a whole materialization, so a tool is
    /// built at most once.
    pub fn with<R>(&self, f: impl FnOnce(&mut ToolCacheInner) -> R) -> R {
        let mut inner = self.inner.lock().unwrap_or_else(PoisonError::into_inner);
        f(&mut inner)
    }
}
