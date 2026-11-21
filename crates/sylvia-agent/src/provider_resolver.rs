//! `agent/_provider_resolver.py`: durable session identities resolved to configured providers
//! inside the owner.
//!
//! Credentials never cross the owner protocol. The ordinary configuration is loaded with dotenv
//! discovery off and credentials sealed, and only the provider the session record names is built.
//! There is no fallback chain and no guessed substitute model.
//!
//! Two pieces live in services not ported yet, so the caller supplies them as [`RuntimeHooks`]:
//! the ChatGPT account's saved-model refresh (`refresh_saved_subscription_reasoning`) and local
//! model discovery (`discover_provider_models`). Providers are built through the registry's
//! [`ProviderFactory`].

use std::collections::HashSet;
use std::path::{Path, PathBuf};
use std::sync::{Arc, Mutex};
use sylvia_base::pystr;
use sylvia_base::{CodedError, Json, JsonMap};
use sylvia_domain::error::DomainError;
use sylvia_domain::model_capability::{ModelCapabilityRegistry, MODEL_CAPABILITY_REGISTRY_VERSION};
use sylvia_domain::session::SessionRecord;
use sylvia_provider::catalog::{build_catalog, output_allowance, DEFAULT_OUTPUT_ALLOWANCE};
use sylvia_provider::config::env::Environment;
use sylvia_provider::config::protection::Seal;
use sylvia_provider::config::{
    load_config, provider_credentials, ConfigFailure, Data, ProviderDef, SylviaGMConfig,
};
use sylvia_provider::context_capacity::{resolve_context_capacity, CapacityInputs, CONTEXT_CAPACITY_SOURCES};
use sylvia_provider::provider::Provider;
use sylvia_provider::pyurl::UrlError;
use sylvia_provider::reasoning::{configure_reasoning, mode_reasoning_default, reasoning_values, Advertised};
use sylvia_provider::registry::{ProviderFactory, ProviderRegistry, RegistryError};
use sylvia_provider::request_metadata::{
    opencode_request_headers, without_opencode_protocol_headers, SessionBoundRequestMetadataProvider,
};
use sylvia_provider::speed::{configure_speed, speed_options, AdvertisedTier, AdvertisedTiers};
use sylvia_provider::types::{transport, ModelInfo};

const LOCAL_PROVIDER_MODES: [&str; 2] = ["local", "ollama"];
const OPENCODE_PRESETS: [&str; 2] = ["opencode_go", "opencode_zen"];

/// `_MODEL_FIELDS`.
const MODEL_FIELDS: [&str; 30] = [
    "id",
    "name",
    "context_window",
    "declared_context_window",
    "declared_context_source",
    "runtime_context_window",
    "runtime_context_source",
    "configured_context_window",
    "effective_context_window",
    "effective_context_source",
    "context_window_source",
    "declared_context_observed_at",
    "runtime_context_observed_at",
    "configured_context_observed_at",
    "context_observed_at",
    "configured_runtime_mismatch",
    "configured_declared_mismatch",
    "max_output_tokens",
    "declared_max_output_tokens",
    "configured_output_tokens",
    "output_allowance_source",
    "documented_source",
    "documented_at",
    "builder_output_tokens",
    "transport",
    "supports_streaming",
    "supports_tools",
    "supported_reasoning_efforts",
    "default_reasoning_effort",
    "service_tiers",
];
const MODEL_ID_LIMIT: usize = 256;
const MODEL_NAME_LIMIT: usize = 128;
const TRANSPORTS: [&str; 6] = [
    transport::CHAT_COMPLETIONS,
    transport::RESPONSES,
    transport::ANTHROPIC_MESSAGES,
    transport::CODEX_APP_SERVER,
    transport::CLAUDE_CODE,
    transport::GEMINI_GENERATE_CONTENT,
];

/// Why the resolver failed, before it becomes the owner's reply.
#[derive(Debug, Clone, PartialEq)]
pub enum ResolverError {
    /// `ConfigError` or another exception from the configuration code.
    Config(ConfigFailure),
    /// `ValueError` from `urlsplit`.
    Url(UrlError),
    /// An error that already has its owner reply (`CommandRejected`, a factory refusal, ...).
    Coded(CodedError),
}

impl From<ConfigFailure> for ResolverError {
    fn from(error: ConfigFailure) -> Self {
        Self::Config(error)
    }
}

impl From<UrlError> for ResolverError {
    fn from(error: UrlError) -> Self {
        Self::Url(error)
    }
}

impl From<CodedError> for ResolverError {
    fn from(error: CodedError) -> Self {
        Self::Coded(error)
    }
}

impl From<DomainError> for ResolverError {
    fn from(error: DomainError) -> Self {
        Self::Coded(error.rejection().unwrap_or_else(|| {
            CodedError::new("internal_error", error.message()).with_error_type(error.python_class())
        }))
    }
}

impl ResolverError {
    /// The owner's reply: `ConfigError` and `ValueError` are `command_rejected`; any other
    /// exception is `internal_error` with its class.
    pub fn into_coded(self) -> CodedError {
        match self {
            Self::Config(failure) => failure.rejection().unwrap_or_else(|| {
                let class = match &failure {
                    ConfigFailure::Python { class, .. } => *class,
                    ConfigFailure::Config(_) => "ConfigError",
                };
                CodedError::new("internal_error", failure.to_string()).with_error_type(class)
            }),
            Self::Url(error) => CodedError::rejected(error.0).with_error_type("ValueError"),
            Self::Coded(error) => error,
        }
    }
}

fn config_error(message: impl Into<String>) -> ResolverError {
    ResolverError::Config(ConfigFailure::Config(message.into()))
}

/// `discover_provider_models(config_path, provider_id=..., preset_id=..., base_url=...,
/// credential=None, query=..., limit=100)`.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct LocalDiscovery<'a> {
    pub config_path: &'a Path,
    pub provider_id: &'a str,
    pub preset_id: &'a str,
    pub base_url: &'a str,
    pub query: &'a str,
    pub limit: i64,
}

/// The services the resolver calls that live elsewhere.
pub trait RuntimeHooks: Send + Sync {
    /// `refresh_saved_subscription_reasoning(storage_root)`: whether the ChatGPT account's
    /// saved models were refreshed (the configuration is then read again).
    fn refresh_subscription_reasoning(&self, storage_root: &Path) -> bool;

    /// `discover_provider_models(...)`: the reply (a `dict` with `models`), or its failure.
    fn discover_local_models(&self, request: &LocalDiscovery<'_>) -> Result<Data, ConfigFailure>;
}

/// Where the configuration comes from: the shipped defaults, this platform's credential
/// protection, and `os.environ`.
#[derive(Clone)]
pub struct ResolverSources {
    pub default_config: PathBuf,
    pub seal: Arc<dyn Seal + Send + Sync>,
    pub env: Environment,
}

/// `Path(path).resolve()` for a settings file: links followed, without Windows' `\\?\`.
fn resolve_path(path: &Path) -> PathBuf {
    let canonical = std::fs::canonicalize(path).ok().or_else(|| {
        let parent = path.parent().filter(|parent| !parent.as_os_str().is_empty()).unwrap_or(Path::new("."));
        Some(std::fs::canonicalize(parent).ok()?.join(path.file_name()?))
    });
    let resolved =
        canonical.unwrap_or_else(|| std::path::absolute(path).unwrap_or_else(|_| path.to_path_buf()));
    let text = resolved.to_string_lossy();
    match text.strip_prefix(r"\\?\UNC\") {
        Some(rest) => PathBuf::from(format!(r"\\{rest}")),
        None => text.strip_prefix(r"\\?\").map_or(resolved.clone(), PathBuf::from),
    }
}

/// `row.get(key)`: `None` when the key is absent.
fn field<'a>(row: &'a Data, key: &str) -> Option<&'a Data> {
    row.get_str(key)
}

/// `value` is a Python `None` (or absent).
fn is_none(value: Option<&Data>) -> bool {
    matches!(value, None | Some(Data::None))
}

/// `_required_model_text(value, field=..., limit=...)`.
fn required_model_text(value: Option<&Data>, name: &str, limit: usize) -> Result<String, ResolverError> {
    let text = match value {
        Some(Data::Str(text)) if !text.is_empty() && pystr::strip(text) == text.as_str() => text,
        _ => return Err(config_error(format!("Configured model {name} must be non-empty trimmed text"))),
    };
    if pystr::len(text) > limit || text.chars().any(|c| (c as u32) < 32) {
        return Err(config_error(format!("Configured model {name} is invalid")));
    }
    Ok(text.clone())
}

/// `_bounded_model_integer(value, field=..., minimum=..., maximum=...)`.
fn bounded_model_integer(
    value: Option<&Data>,
    name: &str,
    minimum: i64,
    maximum: i64,
) -> Result<i64, ResolverError> {
    let Some(Data::Int(value)) = value else {
        return Err(config_error(format!("Configured model {name} must be an integer")));
    };
    if *value < minimum || *value > maximum {
        return Err(config_error(format!("Configured model {name} is outside the supported range")));
    }
    Ok(*value)
}

/// `_optional_model_integer(value, field=..., minimum=..., maximum=...)`.
fn optional_model_integer(
    value: Option<&Data>,
    name: &str,
    minimum: i64,
    maximum: i64,
) -> Result<Option<i64>, ResolverError> {
    match value {
        None | Some(Data::None) => Ok(None),
        Some(Data::Str(text)) if text.is_empty() => Ok(None),
        value => bounded_model_integer(value, name, minimum, maximum).map(Some),
    }
}

fn optional_window(value: Option<&Data>, name: &str) -> Result<Option<i64>, ResolverError> {
    optional_model_integer(value, name, 512, 10_000_000)
}

/// `_capacity_source(value, fallback=...)`.
fn capacity_source<'a>(value: Option<&'a Data>, fallback: &'a str) -> &'a str {
    match value.and_then(Data::as_str) {
        Some(text) if CONTEXT_CAPACITY_SOURCES.contains(&text) => text,
        _ => fallback,
    }
}

/// `row.get("transport") not in ("codex_app_server", "claude_code")`.
fn not_runtime_managed(row: &Data) -> bool {
    !matches!(
        field(row, "transport").and_then(Data::as_str),
        Some(transport::CODEX_APP_SERVER | transport::CLAUDE_CODE)
    )
}

/// `value` for `reasoning_values` / `speed_options`.
fn advertised(value: Option<&Data>) -> Advertised<'_> {
    match value {
        None | Some(Data::None) => Advertised::Missing,
        Some(Data::List(items) | Data::Tuple(items)) => {
            Advertised::List(items.iter().map(Data::as_str).collect())
        }
        Some(_) => Advertised::Other,
    }
}

fn advertised_tiers(value: Option<&Data>) -> AdvertisedTiers<'_> {
    match value {
        None | Some(Data::None) => AdvertisedTiers::Missing,
        Some(Data::List(items) | Data::Tuple(items)) => AdvertisedTiers::List(
            items
                .iter()
                .map(|item| {
                    item.as_dict().map(|_| AdvertisedTier {
                        id: item.get_str("id").and_then(Data::as_str),
                        name: item.get_str("name").and_then(Data::as_str),
                        description: match item.get_str("description") {
                            None => Some(""),
                            Some(value) => value.as_str(),
                        },
                    })
                })
                .collect(),
        ),
        Some(_) => AdvertisedTiers::Other,
    }
}

/// An optional `bool`: `Err` for any other non-`None` value.
fn optional_bool(value: Option<&Data>, default: Option<bool>) -> Result<Option<bool>, ()> {
    match value {
        None => Ok(default),
        Some(Data::None) => Ok(None),
        Some(Data::Bool(flag)) => Ok(Some(*flag)),
        Some(_) => Err(()),
    }
}

/// An observation time: the text when the value is a `str`.
fn observed(value: Option<&Data>) -> Option<&str> {
    value.and_then(Data::as_str)
}

/// `_configured_provider_models(provider_key, definition, known_catalog)`.
pub fn configured_provider_models(
    provider_key: &str,
    definition: &ProviderDef,
    known_catalog: &[ModelInfo],
) -> Result<Vec<ModelInfo>, ResolverError> {
    let known: Vec<&ModelInfo> =
        known_catalog.iter().filter(|model| model.provider_key == provider_key).collect();
    if definition.models.is_empty() {
        if known.is_empty() {
            return Err(config_error(format!(
                "Enabled provider '{provider_key}' must declare at least one model"
            )));
        }
        return Ok(known.into_iter().cloned().collect());
    }
    let provider_prefix = provider_key.replace('_', "-");
    let mut configured = Vec::new();
    let mut identities: HashSet<String> = HashSet::new();
    for row in &definition.models {
        let pairs = row.as_dict().unwrap_or_default();
        let mut unknown: Vec<&str> = pairs
            .iter()
            .filter_map(|(key, _)| key.as_str())
            .filter(|key| !MODEL_FIELDS.contains(key))
            .collect();
        if !unknown.is_empty() {
            unknown.sort_unstable();
            unknown.dedup();
            return Err(config_error(format!(
                "Configured model contains unsupported fields: {}",
                unknown.join(", ")
            )));
        }
        let declared_id = required_model_text(field(row, "id"), "id", MODEL_ID_LIMIT)?;
        let known_model = known
            .iter()
            .copied()
            .find(|model| model.id == declared_id || model.wire_model_id() == declared_id);
        let exact = known_model.filter(|model| model.id == declared_id);
        let canonical_id =
            exact.map_or_else(|| format!("{provider_prefix}/{declared_id}"), |model| model.id.clone());
        let upstream_id = exact.map_or_else(|| declared_id.clone(), |model| model.wire_model_id().to_owned());
        if !identities.insert(canonical_id.clone()) {
            return Err(config_error(format!("Configured model identity is duplicated: {canonical_id}")));
        }
        let default_name =
            Data::Str(known_model.map_or_else(|| declared_id.clone(), |model| model.name.clone()));
        let name = required_model_text(field(row, "name").or(Some(&default_name)), "name", MODEL_NAME_LIMIT)?;
        let known_declared =
            known_model.and_then(|model| model.declared_context_window.or(model.context_window));
        let has_capacity_facts = [
            "declared_context_window",
            "runtime_context_window",
            "configured_context_window",
            "effective_context_window",
        ]
        .iter()
        .any(|key| field(row, key).is_some());
        let known_declared_data = known_declared.map_or(Data::None, Data::Int);
        let declared_context_window = optional_window(
            field(row, "declared_context_window").or(Some(&known_declared_data)),
            "declared_context_window",
        )?;
        let runtime_context_window =
            optional_window(field(row, "runtime_context_window"), "runtime_context_window")?;
        let configured_context_window =
            optional_window(field(row, "configured_context_window"), "configured_context_window")?;
        let legacy_context_window = if has_capacity_facts {
            None
        } else {
            optional_window(field(row, "context_window"), "legacy_context_window")?
        };
        let observed_at = field(row, "context_observed_at");
        let declared_observed_at = field(row, "declared_context_observed_at").or(observed_at);
        let runtime_observed_at = field(row, "runtime_context_observed_at").or(observed_at);
        let configured_observed_at = field(row, "configured_context_observed_at");
        let capacity = resolve_context_capacity(&CapacityInputs {
            declared_context_window,
            declared_context_source: Some(capacity_source(
                field(row, "declared_context_source"),
                if known_declared.is_some() { "model_catalog" } else { "unknown" },
            )),
            runtime_context_window,
            runtime_context_source: Some(capacity_source(field(row, "runtime_context_source"), "unknown")),
            configured_context_window,
            legacy_context_window,
            observed_at: observed(observed_at),
            declared_observed_at: observed(declared_observed_at),
            runtime_observed_at: observed(runtime_observed_at),
            configured_observed_at: observed(configured_observed_at),
        });
        let context_window = capacity.effective_context_window;
        let mut selected_output = field(row, "max_output_tokens").cloned();
        if is_none(selected_output.as_ref()) && not_runtime_managed(row) {
            selected_output = Some(match known_model {
                Some(model) => model.max_output_tokens.map_or(Data::None, Data::Int),
                None => Data::Int(
                    DEFAULT_OUTPUT_ALLOWANCE.min(context_window.unwrap_or(DEFAULT_OUTPUT_ALLOWANCE)),
                ),
            });
        }
        let mut max_output_tokens =
            optional_model_integer(selected_output.as_ref(), "max_output_tokens", 1, 10_000_000)?;
        if let (Some(output), Some(window)) = (max_output_tokens, context_window) {
            if not_runtime_managed(row) && output > window {
                return Err(config_error(
                    "Configured model max_output_tokens is outside the supported range",
                ));
            }
        }
        let declared_output = optional_model_integer(
            field(row, "declared_max_output_tokens"),
            "declared_max_output_tokens",
            1,
            10_000_000,
        )?;
        optional_model_integer(
            field(row, "configured_output_tokens"),
            "configured_output_tokens",
            1,
            10_000_000,
        )?;
        if let (Some(declared), Some(output)) = (declared_output, max_output_tokens) {
            max_output_tokens = Some(output.min(declared));
        }
        let default_builder = Data::Int(32_000.min(max_output_tokens.unwrap_or(32_000)));
        let builder_output_tokens = bounded_model_integer(
            field(row, "builder_output_tokens").or(Some(&default_builder)),
            "builder_output_tokens",
            1,
            max_output_tokens.unwrap_or(10_000_000),
        )?;
        let default_transport = Data::Str(
            known_model.map_or(transport::CHAT_COMPLETIONS, |model| model.transport.as_str()).to_owned(),
        );
        let wire = match field(row, "transport").unwrap_or(&default_transport) {
            Data::Str(text) if TRANSPORTS.contains(&text.as_str()) => text.clone(),
            _ => return Err(config_error("Configured model transport is unsupported")),
        };
        let supports_streaming =
            optional_bool(field(row, "supports_streaming"), known_model.and_then(|m| m.supports_streaming))
                .map_err(|()| config_error("Configured model streaming support must be boolean or null"))?;
        let supports_tools =
            optional_bool(field(row, "supports_tools"), known_model.and_then(|m| m.supports_tools))
                .map_err(|()| config_error("Configured model tool support must be boolean or null"))?;

        let mut model = match known_model {
            Some(known) => known.clone(),
            None => {
                let provider_name =
                    if definition.name.is_empty() { provider_key } else { definition.name.as_str() };
                let mode = if definition.mode.is_empty() { "byok" } else { definition.mode.as_str() };
                ModelInfo::new(&canonical_id, &name, provider_name, provider_key, mode)
            }
        };
        model.id = canonical_id;
        model.name = name;
        model.context_window = context_window;
        model.declared_context_window = capacity.declared_context_window;
        model.declared_context_source = capacity.declared_context_source;
        model.runtime_context_window = capacity.runtime_context_window;
        model.runtime_context_source = capacity.runtime_context_source;
        model.configured_context_window = capacity.configured_context_window;
        model.context_window_source = capacity.effective_context_source;
        model.context_observed_at =
            capacity.runtime_observed_at.clone().or_else(|| capacity.declared_observed_at.clone());
        model.declared_context_observed_at = capacity.declared_observed_at;
        model.runtime_context_observed_at = capacity.runtime_observed_at;
        model.configured_context_observed_at = capacity.configured_observed_at;
        model.configured_runtime_mismatch = capacity.configured_runtime_mismatch;
        model.configured_declared_mismatch = capacity.configured_declared_mismatch;
        model.max_output_tokens = max_output_tokens;
        model.builder_output_tokens = builder_output_tokens;
        model.transport = wire;
        model.provider_model_id = upstream_id;
        model.supports_streaming = supports_streaming;
        model.supports_tools = supports_tools;
        let mut model = model.post_init();
        let suggested = match field(row, "default_reasoning_effort") {
            None | Some(Data::None) => None,
            Some(value) => Some(value.as_str()),
        };
        let (supported, suggested) =
            reasoning_values(&advertised(field(row, "supported_reasoning_efforts")), suggested)
                .map_err(config_error)?;
        let speeds = speed_options(&advertised_tiers(field(row, "service_tiers"))).map_err(config_error)?;
        model.supported_reasoning_efforts = supported;
        model.default_reasoning_effort = suggested;
        model.speed_options = speeds;
        configured.push(model.post_init());
    }
    Ok(configured)
}

/// What one configuration offers.
struct State {
    registry: ProviderRegistry,
    /// `_configured_providers`.
    configured: HashSet<String>,
}

/// `ConfiguredProviderResolver`: one session resolved through enabled, owner-local provider
/// configuration.
pub struct ConfiguredProviderResolver {
    state: Mutex<State>,
    enabled: Vec<String>,
    model_providers: Vec<(String, String)>,
    /// `subscription_models`: the ChatGPT subscription models offered, by runtime model id.
    pub subscription_models: HashSet<String>,
    /// `claude_code_models`: the same for the Claude subscription.
    pub claude_code_models: HashSet<String>,
    definitions: Vec<(String, ProviderDef)>,
    user_config_path: Option<PathBuf>,
    capability_registry: ModelCapabilityRegistry,
    seal: Arc<dyn Seal + Send + Sync>,
    /// `os.environ` for `${VAR}` references inside sealed credentials.
    env: Environment,
    hooks: Arc<dyn RuntimeHooks>,
}

fn load(sources: &ResolverSources, path: Option<&Path>) -> Result<SylviaGMConfig, ConfigFailure> {
    load_config(&sources.default_config, path, sources.seal.as_ref(), &sources.env)
}

impl ConfiguredProviderResolver {
    /// `ConfiguredProviderResolver(user_config_path, model_capability_registry=...)`. `None`
    /// for the registry is `EMPTY_MODEL_CAPABILITY_REGISTRY`.
    pub fn new(
        sources: &ResolverSources,
        user_config_path: Option<&Path>,
        capability_registry: Option<ModelCapabilityRegistry>,
        factory: Arc<dyn ProviderFactory>,
        hooks: Arc<dyn RuntimeHooks>,
    ) -> Result<Self, ResolverError> {
        let mut config = load(sources, user_config_path)?;
        if let Some(path) = user_config_path {
            let stale_subscription =
                config.providers.iter().find(|(key, _)| key == "chatgpt_subscription").is_some_and(
                    |(_, subscription)| {
                        subscription.enabled
                            && subscription.models.iter().any(|row| {
                                field(row, "transport").and_then(Data::as_str)
                                    == Some(transport::CODEX_APP_SERVER)
                                    && is_none(field(row, "supported_reasoning_efforts"))
                            })
                    },
                );
            let storage_root = || resolve_path(path).parent().map(Path::to_path_buf).unwrap_or_default();
            if stale_subscription && hooks.refresh_subscription_reasoning(&storage_root()) {
                config = load(sources, user_config_path)?;
            }
        }
        let known_catalog = build_catalog();
        let mut catalog: Vec<ModelInfo> = Vec::new();
        let mut enabled: Vec<String> = Vec::new();
        for (provider_key, definition) in &config.providers {
            if !definition.enabled {
                continue;
            }
            if definition.base_url.is_empty() {
                return Err(config_error(format!("Enabled provider '{provider_key}' requires base_url")));
            }
            for model in configured_provider_models(provider_key, definition, &known_catalog)? {
                let model = configure_reasoning(model, &definition.base_url)?;
                catalog.push(configure_speed(model, &definition.base_url)?);
            }
            if !enabled.contains(provider_key) {
                enabled.push(provider_key.clone());
            }
        }
        // `{model.id: model.provider_key ...}`: the last entry for an id wins.
        let mut model_providers: Vec<(String, String)> = Vec::new();
        for model in &catalog {
            match model_providers.iter_mut().find(|(id, _)| *id == model.id) {
                Some(slot) => slot.1 = model.provider_key.clone(),
                None => model_providers.push((model.id.clone(), model.provider_key.clone())),
            }
        }
        let runtime_ids = |wire: &str| -> HashSet<String> {
            catalog
                .iter()
                .filter(|model| model.transport == wire)
                .map(|model| model.wire_model_id().to_owned())
                .collect()
        };
        let subscription_models = runtime_ids(transport::CODEX_APP_SERVER);
        let claude_code_models = runtime_ids(transport::CLAUDE_CODE);
        // `ProviderRegistry(catalog=[])` would fall back to the built-in catalogue.
        let registry = ProviderRegistry::new(catalog, factory);
        Ok(Self {
            state: Mutex::new(State { registry, configured: HashSet::new() }),
            enabled,
            model_providers,
            subscription_models,
            claude_code_models,
            definitions: config.providers,
            user_config_path: user_config_path.map(Path::to_path_buf),
            capability_registry: capability_registry.unwrap_or_else(ModelCapabilityRegistry::empty),
            seal: sources.seal.clone(),
            env: sources.env.clone(),
            hooks,
        })
    }

    fn definition(&self, provider_key: &str) -> &ProviderDef {
        &self.definitions.iter().find(|(key, _)| key == provider_key).expect("an enabled provider").1
    }

    fn lock(&self) -> std::sync::MutexGuard<'_, State> {
        self.state.lock().unwrap_or_else(std::sync::PoisonError::into_inner)
    }

    /// `_configure_selected_provider(provider_key)`: unlock a selected connection once; a
    /// failure does not disable other connections.
    fn configure_selected_provider(
        &self,
        state: &mut State,
        provider_key: &str,
    ) -> Result<(), ConfigFailure> {
        if state.configured.contains(provider_key) {
            return Ok(());
        }
        let definition = self.definition(provider_key);
        let (credential, mut headers) = provider_credentials(definition, self.seal.as_ref(), &self.env)?;
        let preset =
            if definition.preset_id.is_empty() { provider_key } else { definition.preset_id.as_str() };
        if OPENCODE_PRESETS.contains(&preset) {
            headers = without_opencode_protocol_headers(&headers);
        }
        let runtime_home = match (&self.user_config_path, provider_key) {
            (Some(path), "chatgpt_subscription" | "claude_runtime") => {
                let folder =
                    if provider_key == "claude_runtime" { "claude-runtime" } else { "chatgpt-runtime" };
                let root = resolve_path(path).parent().map(Path::to_path_buf).unwrap_or_default();
                root.join(folder).to_string_lossy().into_owned()
            }
            _ => String::new(),
        };
        state.registry.configure_provider(
            provider_key,
            &definition.base_url,
            &credential,
            headers,
            &runtime_home,
        );
        state.configured.insert(provider_key.to_owned());
        Ok(())
    }

    /// `_refresh_local_runtime_capacity(model, definition)`: a fresh local runtime observation
    /// for this GM admission, written into the catalogue entry.
    fn refresh_local_runtime_capacity(
        &self,
        model: &ModelInfo,
        definition: &ProviderDef,
    ) -> Result<(), ResolverError> {
        let mut discovered: Option<Data> = None;
        if let Some(path) = &self.user_config_path {
            let preset = if definition.preset_id.is_empty() {
                model.provider_key.as_str()
            } else {
                &definition.preset_id
            };
            let request = LocalDiscovery {
                config_path: path,
                provider_id: &model.provider_key,
                preset_id: preset,
                base_url: &definition.base_url,
                query: model.wire_model_id(),
                limit: 100,
            };
            let result = match self.hooks.discover_local_models(&request) {
                Ok(result) => result,
                Err(ConfigFailure::Config(_)) => {
                    Data::Dict(vec![(Data::Str("models".into()), Data::List(Vec::new()))])
                }
                Err(failure) => return Err(failure.into()),
            };
            if let Some(Data::List(rows)) = result.get_str("models") {
                discovered = rows
                    .iter()
                    .find(|row| {
                        row.as_dict().is_some()
                            && row.get_str("id").and_then(Data::as_str) == Some(model.wire_model_id())
                    })
                    .cloned();
            }
        }
        let observed_integer = |name: &str| match discovered.as_ref().and_then(|row| row.get_str(name)) {
            Some(Data::Int(value)) => Some(*value),
            _ => None,
        };
        let observed_text =
            |name: &str| discovered.as_ref().and_then(|row| row.get_str(name)).and_then(Data::as_str);
        let declared = observed_integer("declared_context_window").or(model.declared_context_window);
        let capacity = resolve_context_capacity(&CapacityInputs {
            declared_context_window: declared,
            declared_context_source: Some(
                observed_text("declared_context_source").unwrap_or(&model.declared_context_source),
            ),
            runtime_context_window: observed_integer("runtime_context_window"),
            runtime_context_source: Some(observed_text("runtime_context_source").unwrap_or("unknown")),
            configured_context_window: model.configured_context_window,
            legacy_context_window: None,
            observed_at: None,
            declared_observed_at: observed_text("declared_context_observed_at")
                .or(model.declared_context_observed_at.as_deref()),
            runtime_observed_at: observed_text("runtime_context_observed_at"),
            configured_observed_at: model.configured_context_observed_at.as_deref(),
        });
        let mut state = self.lock();
        let Some(entry) = state.registry.model_mut(&model.id) else { return Ok(()) };
        entry.context_window = capacity.effective_context_window;
        entry.declared_context_window = capacity.declared_context_window;
        entry.declared_context_source = capacity.declared_context_source;
        entry.runtime_context_window = capacity.runtime_context_window;
        entry.runtime_context_source = capacity.runtime_context_source;
        entry.configured_context_window = capacity.configured_context_window;
        entry.context_window_source = capacity.effective_context_source;
        entry.context_observed_at =
            capacity.runtime_observed_at.clone().or_else(|| capacity.declared_observed_at.clone());
        entry.declared_context_observed_at = capacity.declared_observed_at;
        entry.runtime_context_observed_at = capacity.runtime_observed_at;
        entry.configured_context_observed_at = capacity.configured_observed_at;
        entry.configured_runtime_mismatch = capacity.configured_runtime_mismatch;
        entry.configured_declared_mismatch = capacity.configured_declared_mismatch;
        Ok(())
    }

    /// `model_choices()`: configured catalogue choices without connection details or credentials.
    pub fn model_choices(&self) -> Result<Vec<Json>, ResolverError> {
        let state = self.lock();
        let mut enabled: Vec<&str> = self.enabled.iter().map(String::as_str).collect();
        enabled.sort_unstable();
        let mut models: Vec<&ModelInfo> =
            enabled.iter().flat_map(|provider_id| state.registry.list_models(Some(provider_id))).collect();
        let keys: Vec<(String, String)> = models
            .iter()
            .map(|model| (pystr::casefold(&model.provider_name), pystr::casefold(&model.name)))
            .collect();
        let mut order: Vec<usize> = (0..models.len()).collect();
        order.sort_by(|&a, &b| keys[a].cmp(&keys[b]).then_with(|| models[a].id.cmp(&models[b].id)));
        models = order.into_iter().map(|index| models[index]).collect();
        models.into_iter().map(|model| self.choice(model)).collect()
    }

    fn choice(&self, model: &ModelInfo) -> Result<Json, ResolverError> {
        let text = |value: &str| Json::String(value.to_owned());
        let opt_text = |value: Option<&str>| value.map_or(Json::Null, text);
        let opt_int = |value: Option<i64>| value.map_or(Json::Null, Json::from);
        let opt_bool = |value: Option<bool>| value.map_or(Json::Null, Json::Bool);
        let placement = self.capability_registry.placement(&model.id)?;
        let mut defaults = JsonMap::new();
        defaults.insert("dm".into(), opt_text(mode_reasoning_default(Some(model), "dm").as_deref()));
        defaults
            .insert("builder".into(), opt_text(mode_reasoning_default(Some(model), "builder").as_deref()));
        let runtime_managed =
            matches!(model.transport.as_str(), transport::CODEX_APP_SERVER | transport::CLAUDE_CODE);
        let pairs: Vec<(&str, Json)> = vec![
            ("provider_id", text(&model.provider_key)),
            ("provider_name", text(&model.provider_name)),
            ("model_id", text(&model.id)),
            ("model_name", text(&model.name)),
            ("context_window", opt_int(model.context_window)),
            ("declared_context_window", opt_int(model.declared_context_window)),
            ("declared_context_source", text(&model.declared_context_source)),
            ("runtime_context_window", opt_int(model.runtime_context_window)),
            ("runtime_context_source", text(&model.runtime_context_source)),
            ("configured_context_window", opt_int(model.configured_context_window)),
            ("effective_context_window", opt_int(model.context_window)),
            ("effective_context_source", text(&model.context_window_source)),
            ("declared_context_observed_at", opt_text(model.declared_context_observed_at.as_deref())),
            ("runtime_context_observed_at", opt_text(model.runtime_context_observed_at.as_deref())),
            ("configured_context_observed_at", opt_text(model.configured_context_observed_at.as_deref())),
            ("context_observed_at", opt_text(model.context_observed_at.as_deref())),
            ("configured_runtime_mismatch", Json::Bool(model.configured_runtime_mismatch)),
            ("configured_declared_mismatch", Json::Bool(model.configured_declared_mismatch)),
            ("max_output_tokens", opt_int(model.max_output_tokens)),
            ("builder_output_tokens", opt_int(output_allowance(model, true))),
            ("output_budget_mode", text(if runtime_managed { "runtime_managed" } else { "enforced" })),
            ("transport", text(&model.transport)),
            ("supports_streaming", opt_bool(model.supports_streaming)),
            ("supports_tools", opt_bool(model.supports_tools)),
            (
                "supported_reasoning_efforts",
                model.supported_reasoning_efforts.as_ref().map_or(Json::Null, |values| {
                    Json::Array(values.iter().map(|value| text(value)).collect())
                }),
            ),
            ("default_reasoning_effort", opt_text(model.default_reasoning_effort.as_deref())),
            ("reasoning_source", text(&model.reasoning_source)),
            ("reasoning_control", opt_text(model.reasoning_control.as_deref())),
            ("reasoning_mode_defaults", Json::Object(defaults)),
            (
                "speed_options",
                match model.speed_options.as_deref() {
                    None | Some([]) => Json::Null,
                    Some(options) => Json::Array(options.iter().map(|option| option.projection()).collect()),
                },
            ),
            ("speed_control", opt_text(model.speed_control.as_deref())),
            ("speed_source", text(&model.speed_source)),
            ("capability_registry_version", text(MODEL_CAPABILITY_REGISTRY_VERSION)),
            ("capability_tier_id", opt_text(placement.map(|placement| placement.tier_id()))),
            (
                "capability_evidence_sha256",
                placement.map_or(Json::Null, |placement| Json::String(placement.evidence_sha256())),
            ),
        ];
        Ok(Json::Object(pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect()))
    }

    /// `__call__(session)`: exactly the configured provider and model the session names.
    pub fn resolve(&self, session: &SessionRecord) -> Result<Arc<dyn Provider>, ResolverError> {
        let fields = session.fields();
        let rejected = |message: &str| {
            ResolverError::Coded(CodedError::rejected(message).with_error_type("CommandRejected"))
        };
        if !self.enabled.contains(&fields.provider_id) {
            return Err(rejected("Agent session provider is not configured"));
        }
        let Some((_, owner)) = self.model_providers.iter().find(|(id, _)| *id == fields.model_id) else {
            return Err(rejected("Agent session model identity is not canonical"));
        };
        if *owner != fields.provider_id {
            return Err(rejected("Agent session provider/model identity mismatch"));
        }
        let (provider, model) = {
            let mut state = self.lock();
            if let Err(error) = self.configure_selected_provider(&mut state, &fields.provider_id) {
                return Err(match error {
                    ConfigFailure::Config(message) => ResolverError::Coded(
                        CodedError::new("provider_credentials_unavailable", message)
                            .with_error_type("CommandRejected"),
                    ),
                    other => other.into(),
                });
            }
            match state.registry.resolve(&fields.model_id) {
                Ok(resolved) => resolved,
                Err(RegistryError::NotFound(_)) => {
                    return Err(rejected("Agent session model is not configured"))
                }
                Err(RegistryError::Build(error)) => return Err(error.into()),
            }
        };
        if model.provider_key != fields.provider_id {
            return Err(rejected("Agent session provider/model identity mismatch"));
        }
        let definition = self.definition(&model.provider_key);
        if session.role().as_str() == "dm" && LOCAL_PROVIDER_MODES.contains(&model.provider_mode.as_str()) {
            self.refresh_local_runtime_capacity(&model, definition)?;
        }
        let preset =
            if definition.preset_id.is_empty() { model.provider_key.as_str() } else { &definition.preset_id };
        if OPENCODE_PRESETS.contains(&preset) {
            return Ok(Arc::new(SessionBoundRequestMetadataProvider::new(
                provider,
                opencode_request_headers(session.session_id()),
            )));
        }
        Ok(provider)
    }
}
