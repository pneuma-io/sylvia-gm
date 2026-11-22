//! The owner's provider resolver (lane 4): `owner_process.run`'s `resolve_provider`,
//! `list_model_choices` and `reload_configured_provider` closures around one
//! [`ConfiguredProviderResolver`].
//!
//! As in Python, the resolver is built from `storage_root/config.yaml` the first time it is
//! needed, and a failed build is tried again on the next call. A reload builds the replacement
//! first and swaps it in only when that succeeds.
//!
//! Providers come from the real [`DefaultProviderFactory`], and local discovery is the real
//! `discover_provider_models` ([`OwnerRuntimes`]); the account runtimes are not ported yet.
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
use sylvia_base::{CodedResult, Json};
use sylvia_domain::session::SessionRecord;
use sylvia_provider::config::discovery::{discover_provider_models, network_clients, ClientFor, Discover};
use sylvia_provider::config::env::Environment;
use sylvia_provider::config::protection::PlatformSeal;
use sylvia_provider::config::provider_settings::{now_isoformat, ConfigSources};
use sylvia_provider::config::{default_config_path, ConfigFailure, Data};
use sylvia_provider::factory::DefaultProviderFactory;
use sylvia_provider::provider::Provider;
use sylvia_provider::registry::ProviderFactory;

/// The services the resolver calls: local model discovery over the network (Python's
/// `discover_provider_models`), and the ChatGPT account, which is not ported yet.
pub struct OwnerRuntimes {
    sources: ResolverSources,
    clients: Arc<ClientFor>,
}

impl OwnerRuntimes {
    /// Discovery with these configuration sources and HTTP clients.
    pub fn new(sources: ResolverSources, clients: Arc<ClientFor>) -> Self {
        Self { sources, clients }
    }
}

impl RuntimeHooks for OwnerRuntimes {
    /// No ChatGPT runtime yet: Python's answer when the account is unavailable.
    fn refresh_subscription_reasoning(&self, _storage_root: &Path) -> bool {
        false
    }

    /// `discover_provider_models(config_path, provider_id=..., preset_id=..., base_url=...,
    /// credential=None, query=..., limit=100)`.
    fn discover_local_models<'a>(
        &'a self,
        request: LocalDiscovery<'a>,
    ) -> BoxFuture<'a, Result<Data, ConfigFailure>> {
        Box::pin(async move {
            let sources = ConfigSources {
                default_config: &self.sources.default_config,
                seal: self.sources.seal.as_ref(),
                env: &self.sources.env,
            };
            let discover = Discover {
                provider_id: Data::Str(request.provider_id.into()),
                preset_id: Data::Str(request.preset_id.into()),
                base_url: Data::Str(request.base_url.into()),
                query: Data::Str(request.query.into()),
                limit: Data::Int(request.limit),
                ..Discover::default()
            };
            discover_provider_models(
                sources,
                request.config_path,
                &discover,
                self.clients.as_ref(),
                &now_isoformat,
            )
            .await
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
        let runtimes = OwnerRuntimes::new(sources.clone(), network_clients());
        Self::with(
            storage_root.join("config.yaml"),
            sources,
            Arc::new(DefaultProviderFactory::network()),
            Arc::new(runtimes),
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
        Box::pin(async move { self.resolver()?.resolve(session).await.map_err(|error| error.into_coded()) })
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
