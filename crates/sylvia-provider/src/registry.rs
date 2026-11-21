//! `provider/_registry.py`'s `ProviderRegistry`: model resolution and provider caching.
//!
//! Canonical model ids are `provider_key/model_name`. A short name resolves only when exactly one
//! catalogue entry has it. Providers are built through a [`ProviderFactory`] (Python's
//! `ProviderFactory.create`) and cached by `(provider_key, model id)`.
//!
//! `ModelDiscovery`, `discover_models`, `configure_providers`, `list_providers`,
//! `get_recommended` and `model_count` are not ported: no live caller in the 0.4.1 snapshot
//! (nothing calls `discover_models`, so the discovered-model lookup in `_resolve_model_info` is
//! always empty).

use crate::error::{ErrorKind, ProviderError};
use crate::provider::Provider;
use crate::types::{transport, ModelInfo, ProviderConfig};
use std::collections::HashMap;
use std::sync::Arc;
use sylvia_base::CodedError;

/// Python's `ProviderFactory.create(config)`.
pub trait ProviderFactory: Send + Sync {
    /// Build the provider for one model. The error is the coded failure Python's owner would
    /// answer with (`ValueError` is a rejection; any other exception `internal_error`).
    fn create(&self, config: ProviderConfig) -> Result<Arc<dyn Provider>, CodedError>;
}

/// Why `resolve` failed.
#[derive(Debug, Clone, PartialEq)]
pub enum RegistryError {
    /// `ModelNotFoundError`.
    NotFound(ProviderError),
    /// The factory refused.
    Build(CodedError),
}

/// Connection details registered by `configure_provider`.
#[derive(Clone, PartialEq, Eq)]
struct Connection {
    base_url: String,
    api_key: String,
    headers: Vec<(String, String)>,
    runtime_home: String,
}

/// `ProviderRegistry`.
pub struct ProviderRegistry {
    /// Every catalogue entry in order, including one a later entry with the same id replaced.
    entries: Vec<ModelInfo>,
    /// `_catalog`: id → entry, in first-insertion order (a dict keeps a replaced key's place).
    catalog: Vec<(String, usize)>,
    /// `_by_short_name`.
    by_short_name: HashMap<String, Vec<usize>>,
    /// `_provider_configs`.
    connections: HashMap<String, Connection>,
    /// `_providers`.
    providers: HashMap<(String, String), Arc<dyn Provider>>,
    factory: Arc<dyn ProviderFactory>,
}

impl ProviderRegistry {
    /// `ProviderRegistry(catalog=...)`: an empty catalogue uses `build_catalog()`.
    pub fn new(catalog: Vec<ModelInfo>, factory: Arc<dyn ProviderFactory>) -> Self {
        let catalog = if catalog.is_empty() { crate::catalog::build_catalog() } else { catalog };
        let mut registry = Self {
            entries: Vec::new(),
            catalog: Vec::new(),
            by_short_name: HashMap::new(),
            connections: HashMap::new(),
            providers: HashMap::new(),
            factory,
        };
        for entry in catalog {
            let index = registry.entries.len();
            let short = entry.id.split_once('/').map_or(entry.id.as_str(), |(_, rest)| rest).to_owned();
            match registry.catalog.iter_mut().find(|(id, _)| *id == entry.id) {
                Some(slot) => slot.1 = index,
                None => registry.catalog.push((entry.id.clone(), index)),
            }
            registry.by_short_name.entry(short).or_default().push(index);
            registry.entries.push(entry);
        }
        registry
    }

    /// `configure_provider(...)`: clients built with older details are dropped.
    pub fn configure_provider(
        &mut self,
        provider_key: &str,
        base_url: &str,
        api_key: &str,
        headers: Vec<(String, String)>,
        runtime_home: &str,
    ) {
        self.providers.retain(|(key, _), _| key != provider_key);
        self.connections.insert(
            provider_key.to_owned(),
            Connection {
                base_url: base_url.to_owned(),
                api_key: api_key.to_owned(),
                headers,
                runtime_home: runtime_home.to_owned(),
            },
        );
    }

    /// `_resolve_model_info(model_id)`: the catalogue entry's index.
    fn resolve_index(&self, model_id: &str) -> Result<usize, ProviderError> {
        if let Some((_, index)) = self.catalog.iter().find(|(id, _)| id == model_id) {
            return Ok(*index);
        }
        match self.by_short_name.get(model_id).map(Vec::as_slice) {
            Some([only]) => Ok(*only),
            Some(candidates) if candidates.len() > 1 => {
                let ids: Vec<&str> =
                    candidates.iter().map(|&index| self.entries[index].id.as_str()).collect();
                Err(ProviderError::new(
                    ErrorKind::ModelNotFound,
                    format!(
                        "Ambiguous model name '{model_id}'. Use a fully qualified ID: {}",
                        ids.join(", ")
                    ),
                ))
            }
            _ => {
                let mut ids: Vec<&str> = self.catalog.iter().map(|(id, _)| id.as_str()).collect();
                ids.sort_unstable();
                Err(ProviderError::new(
                    ErrorKind::ModelNotFound,
                    format!("Model '{model_id}' not found in registry. Available models: {}", ids.join(", ")),
                ))
            }
        }
    }

    /// `resolve(model_id)`: the provider and the catalogue entry it answers for.
    pub fn resolve(&mut self, model_id: &str) -> Result<(Arc<dyn Provider>, ModelInfo), RegistryError> {
        let index = self.resolve_index(model_id).map_err(RegistryError::NotFound)?;
        let info = self.entries[index].clone();
        let provider = self.get_or_create_provider(&info)?;
        Ok((provider, info))
    }

    /// `_get_or_create_provider(provider_key, model_info)`.
    fn get_or_create_provider(&mut self, info: &ModelInfo) -> Result<Arc<dyn Provider>, RegistryError> {
        let cache_key = (info.provider_key.clone(), info.id.clone());
        let fresh_each_time = info.transport == transport::CLAUDE_CODE;
        if !fresh_each_time {
            if let Some(provider) = self.providers.get(&cache_key) {
                return Ok(provider.clone());
            }
        }
        let Some(connection) = self.connections.get(&info.provider_key) else {
            return Err(RegistryError::NotFound(ProviderError::new(
                ErrorKind::ModelNotFound,
                format!(
                    "No configuration registered for provider '{}'. Call configure_provider() first.",
                    info.provider_key
                ),
            )));
        };
        let mut config = ProviderConfig::new(&info.provider_key, &info.transport, &connection.base_url);
        config.runtime_home = connection.runtime_home.clone();
        config.default_headers = connection.headers.clone();
        config.api_key = connection.api_key.clone();
        config.models = vec![info.clone()];
        config.default_model = info.id.clone();
        let provider = self.factory.create(config).map_err(RegistryError::Build)?;
        // Native Claude steps own a process: each resolved session gets its own adapter.
        if !fresh_each_time {
            self.providers.insert(cache_key, provider.clone());
        }
        Ok(provider)
    }

    /// `list_models(provider_key)`: registered models in catalogue order.
    pub fn list_models(&self, provider_key: Option<&str>) -> Vec<&ModelInfo> {
        self.catalog
            .iter()
            .map(|(_, index)| &self.entries[*index])
            .filter(|model| provider_key.is_none_or(|key| model.provider_key == key))
            .collect()
    }

    /// The catalogue entry `resolve(model_id)` answers with, for changing it in place (Python
    /// mutates the shared `ModelInfo`).
    pub fn model_mut(&mut self, model_id: &str) -> Option<&mut ModelInfo> {
        let index = self.resolve_index(model_id).ok()?;
        self.entries.get_mut(index)
    }
}
