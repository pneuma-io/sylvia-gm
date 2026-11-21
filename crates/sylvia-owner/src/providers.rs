//! The owner's provider resolver (lane 4): `owner_process.run`'s `resolve_provider`,
//! `list_model_choices` and `reload_configured_provider` closures around one
//! [`ConfiguredProviderResolver`].
//!
//! As in Python, the resolver is built from `storage_root/config.yaml` the first time it is
//! needed, and a failed build is tried again on the next call. A reload builds the replacement
//! first and swaps it in only when that succeeds.
//!
//! `_release_unoffered_model_threads` (after a reload) belongs to the account runtimes, which
//! are not ported yet.

use crate::services::ProviderResolver;
use futures_util::future::BoxFuture;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use sylvia_agent::provider_resolver::{
    ConfiguredProviderResolver, LocalDiscovery, ResolverSources, RuntimeHooks,
};
use sylvia_base::{CodedError, CodedResult, Json};
use sylvia_domain::session::SessionRecord;
use sylvia_provider::config::env::Environment;
use sylvia_provider::config::protection::PlatformSeal;
use sylvia_provider::config::{default_config_path, ConfigFailure, Data};
use sylvia_provider::provider::Provider;
use sylvia_provider::registry::ProviderFactory;
use sylvia_provider::types::ProviderConfig;

/// The code an unported piece answers with (see [`crate::dispatch::PORT_PENDING`]).
fn pending(message: String) -> CodedError {
    CodedError::new(crate::dispatch::PORT_PENDING, message)
}

/// Provider construction until the HTTP client and the protocol adapters are ported: every
/// build answers `rust_port_pending`.
pub struct PendingFactory;

impl ProviderFactory for PendingFactory {
    fn create(&self, config: ProviderConfig) -> Result<Arc<dyn Provider>, CodedError> {
        Err(pending(format!("The Rust owner cannot build {} providers yet.", config.transport)))
    }
}

/// The account runtime and local discovery until they are ported.
pub struct PendingRuntimes;

impl RuntimeHooks for PendingRuntimes {
    /// No ChatGPT runtime: Python's answer when the account is unavailable.
    fn refresh_subscription_reasoning(&self, _storage_root: &Path) -> bool {
        false
    }

    fn discover_local_models(&self, request: &LocalDiscovery<'_>) -> Result<Data, ConfigFailure> {
        Err(ConfigFailure::Python {
            class: "RuntimeError",
            message: format!("The Rust owner cannot discover {} models yet.", request.provider_id),
        })
    }
}

/// The owner's [`ProviderResolver`].
pub struct OwnerProviders {
    config_path: PathBuf,
    sources: ResolverSources,
    factory: Arc<dyn ProviderFactory>,
    hooks: Arc<dyn RuntimeHooks>,
    current: Mutex<Option<Arc<ConfiguredProviderResolver>>>,
}

impl OwnerProviders {
    /// The product's resolver for an owner whose storage root is `storage_root`.
    pub fn new(storage_root: &Path) -> Self {
        let sources = ResolverSources {
            default_config: default_config_path(),
            seal: Arc::new(PlatformSeal),
            env: Environment::current(),
        };
        Self::with(
            storage_root.join("config.yaml"),
            sources,
            Arc::new(PendingFactory),
            Arc::new(PendingRuntimes),
        )
    }

    /// A resolver over given sources, factory and runtime hooks.
    pub fn with(
        config_path: PathBuf,
        sources: ResolverSources,
        factory: Arc<dyn ProviderFactory>,
        hooks: Arc<dyn RuntimeHooks>,
    ) -> Self {
        Self { config_path, sources, factory, hooks, current: Mutex::new(None) }
    }

    fn build(&self) -> CodedResult<Arc<ConfiguredProviderResolver>> {
        ConfiguredProviderResolver::new(
            &self.sources,
            Some(&self.config_path),
            None,
            self.factory.clone(),
            self.hooks.clone(),
        )
        .map(Arc::new)
        .map_err(|error| error.into_coded())
    }

    /// The current resolver, built on first use (`resolver_lock` held while building, as in
    /// Python).
    fn resolver(&self) -> CodedResult<Arc<ConfiguredProviderResolver>> {
        let mut current = self.current.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
        if let Some(resolver) = current.as_ref() {
            return Ok(resolver.clone());
        }
        let resolver = self.build()?;
        *current = Some(resolver.clone());
        Ok(resolver)
    }
}

impl ProviderResolver for OwnerProviders {
    fn resolve<'a>(&'a self, session: &'a SessionRecord) -> BoxFuture<'a, CodedResult<Arc<dyn Provider>>> {
        Box::pin(async move { self.resolver()?.resolve(session).map_err(|error| error.into_coded()) })
    }

    fn model_choices(&self) -> BoxFuture<'_, CodedResult<Vec<Json>>> {
        Box::pin(async move { self.resolver()?.model_choices().map_err(|error| error.into_coded()) })
    }

    fn reload(&self) -> BoxFuture<'_, CodedResult<()>> {
        Box::pin(async move {
            let replacement = self.build()?;
            *self.current.lock().unwrap_or_else(std::sync::PoisonError::into_inner) = Some(replacement);
            Ok(())
        })
    }
}
