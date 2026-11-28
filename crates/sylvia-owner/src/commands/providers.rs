//! Provider commands: `interfaces/commands/providers.py`.
//!
//! Also here, beside `set_chat_defaults`, three helpers from lane 5's files (ported by lane 4 at
//! the manager's request): `interfaces/_chat_start.explicit_effort` and `explicit_speed`, and
//! `interfaces/_session_reasoning.catalog_model_info`.

use crate::dispatch::DispatchContext;
use std::path::PathBuf;
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};
use sylvia_provider::config::discovery::{
    detect_local_providers, discover_provider_models, network_clients, Discover,
};
use sylvia_provider::config::env::Environment;
use sylvia_provider::config::protection::PlatformSeal;
use sylvia_provider::config::provider_settings::{
    now_isoformat, provider_settings, require_configured_model, ConfigSources,
};
use sylvia_provider::config::provider_writes::{
    remove_provider_settings, save_provider_settings, set_chat_defaults_settings, set_default_model_settings,
    SaveProvider,
};
use sylvia_provider::config::validation::{validate_provider_model, Validate};
use sylvia_provider::config::{data_from_json, default_config_path, reply_json, ConfigFailure, Data};
use sylvia_provider::pyobj::py_str;
use sylvia_provider::reasoning::{reasoning_values, select_reasoning, suggested_from_data, Advertised};
use sylvia_provider::speed::{select_speed, speed_options, AdvertisedTiers};
use sylvia_provider::types::{transport, ModelInfo};

/// A configuration failure as the owner answers it: `ConfigError` and `ValueError` are
/// rejections; anything else is the internal error with its class.
pub(crate) fn config_error(failure: ConfigFailure) -> CodedError {
    failure.rejection().unwrap_or_else(|| {
        let class = match &failure {
            ConfigFailure::Python { class, .. } => *class,
            ConfigFailure::Config(_) => "ConfigError",
        };
        CodedError::new("internal_error", failure.to_string()).with_error_type(class)
    })
}

/// Run `operation` with the product's configuration sources: the shipped defaults, this
/// platform's credential protection and the process environment.
pub(crate) fn with_sources<T>(
    operation: impl FnOnce(ConfigSources<'_>) -> Result<T, ConfigFailure>,
) -> CodedResult<T> {
    let default_config = default_config_path();
    let env = Environment::current();
    operation(ConfigSources { default_config: &default_config, seal: &PlatformSeal, env: &env })
        .map_err(config_error)
}

pub(crate) fn config_path(ctx: &DispatchContext) -> PathBuf {
    ctx.storage_root.join("config.yaml")
}

/// `params.get(key)` as the Python value it holds (`None` when absent).
fn param(params: &JsonMap, key: &str) -> CodedResult<Data> {
    params.get(key).map_or(Ok(Data::None), |value| data_from_json(value).map_err(config_error))
}

fn reply(value: &Data) -> CodedResult<Json> {
    reply_json(value).map_err(config_error)
}

/// `OwnerProtocolError("Provider settings reload is unavailable")` when there is no resolver.
pub(crate) fn providers(
    ctx: &DispatchContext,
) -> CodedResult<&std::sync::Arc<dyn crate::services::ProviderResolver>> {
    ctx.providers.as_ref().ok_or_else(|| {
        CodedError::rejected("Provider settings reload is unavailable").with_error_type("OwnerProtocolError")
    })
}

/// `ctx.model_choices()`.
pub(crate) async fn model_choices(ctx: &DispatchContext) -> CodedResult<Vec<Json>> {
    let Some(providers) = ctx.providers.as_ref() else {
        return Err(CodedError::new("internal_error", "The provider resolver is not available.")
            .with_error_type("RuntimeError"));
    };
    providers.model_choices().await
}

/// `handle_model_choices`: `tuple(ctx.model_choices())`.
pub async fn handle_model_choices(ctx: &DispatchContext, _params: &JsonMap) -> CodedResult<Json> {
    Ok(Json::Array(model_choices(ctx).await?))
}

/// `handle_provider_settings`: `provider_settings(ctx.storage_root / "config.yaml")`.
pub async fn handle_provider_settings(ctx: &DispatchContext, _params: &JsonMap) -> CodedResult<Json> {
    let path = config_path(ctx);
    reply(&with_sources(|sources| provider_settings(sources, &path))?)
}

/// `handle_detect_local_providers`: probe the fixed loopback presets.
pub async fn handle_detect_local_providers(_ctx: &DispatchContext, _params: &JsonMap) -> CodedResult<Json> {
    reply(&detect_local_providers(network_clients().as_ref()).await.map_err(config_error)?)
}

/// `handle_discover_provider_models`: one provider's model catalogue, searched and paged.
pub async fn handle_discover_provider_models(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let default_config = default_config_path();
    let env = Environment::current();
    let sources = ConfigSources { default_config: &default_config, seal: &PlatformSeal, env: &env };
    let mut request = Discover {
        provider_id: param(params, "provider_id")?,
        preset_id: param(params, "preset_id")?,
        base_url: param(params, "base_url")?,
        credential: param(params, "credential")?,
        headers: param(params, "headers")?,
        query: param(params, "query")?,
        ..Discover::default()
    };
    // `params.get("offset", 0)` and `params.get("limit", 100)`: only a missing key takes the default.
    if params.contains_key("offset") {
        request.offset = param(params, "offset")?;
    }
    if params.contains_key("limit") {
        request.limit = param(params, "limit")?;
    }
    let path = config_path(ctx);
    let found =
        discover_provider_models(sources, &path, &request, network_clients().as_ref(), &now_isoformat)
            .await
            .map_err(config_error)?;
    reply(&found)
}

/// `handle_validate_provider_model`: one bounded agent-compatibility probe.
pub async fn handle_validate_provider_model(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let default_config = default_config_path();
    let env = Environment::current();
    let sources = ConfigSources { default_config: &default_config, seal: &PlatformSeal, env: &env };
    let request = Validate {
        provider_id: param(params, "provider_id")?,
        preset_id: param(params, "preset_id")?,
        base_url: param(params, "base_url")?,
        credential: param(params, "credential")?,
        model_id: param(params, "model_id")?,
        transport: param(params, "transport")?,
        headers: param(params, "headers")?,
    };
    let path = config_path(ctx);
    let report = validate_provider_model(sources, &path, &request, network_clients().as_ref())
        .await
        .map_err(config_error)?;
    reply(&report)
}

/// `handle_save_provider_settings`: save one model, then reload the provider resolver.
pub async fn handle_save_provider_settings(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let providers = providers(ctx)?;
    let request = SaveProvider {
        provider_id: param(params, "provider_id")?,
        preset_id: param(params, "preset_id")?,
        display_name: param(params, "display_name")?,
        base_url: param(params, "base_url")?,
        credential: param(params, "credential")?,
        model_id: param(params, "model_id")?,
        model_name: param(params, "model_name")?,
        context_window: param(params, "context_window")?,
        max_output_tokens: param(params, "max_output_tokens")?,
        declared_max_output_tokens: param(params, "declared_max_output_tokens")?,
        builder_output_tokens: param(params, "builder_output_tokens")?,
        declared_context_window: param(params, "declared_context_window")?,
        declared_context_source: param(params, "declared_context_source")?,
        runtime_context_window: param(params, "runtime_context_window")?,
        runtime_context_source: param(params, "runtime_context_source")?,
        configured_context_window: param(params, "configured_context_window")?,
        context_observed_at: param(params, "context_observed_at")?,
        declared_context_observed_at: param(params, "declared_context_observed_at")?,
        runtime_context_observed_at: param(params, "runtime_context_observed_at")?,
        configured_context_observed_at: param(params, "configured_context_observed_at")?,
        transport: param(params, "transport")?,
        headers: param(params, "headers")?,
        supports_streaming: param(params, "supports_streaming")?,
        supports_tools: param(params, "supports_tools")?,
        ..SaveProvider::default()
    };
    let path = config_path(ctx);
    let saved = with_sources(|sources| save_provider_settings(sources, &path, &request, &now_isoformat))?;
    providers.reload().await?;
    reply(&saved)
}

/// `handle_remove_provider_settings`: remove one connection, then reload the provider resolver.
pub async fn handle_remove_provider_settings(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let providers = providers(ctx)?;
    let provider_id = param(params, "provider_id")?;
    let path = config_path(ctx);
    with_sources(|sources| remove_provider_settings(sources, &path, &provider_id))?;
    providers.reload().await?;
    let mut result = JsonMap::new();
    result.insert("status".into(), Json::String("removed".into()));
    Ok(Json::Object(result))
}

/// `handle_set_default_model`.
pub async fn handle_set_default_model(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let (provider_id, model_id) = (param(params, "provider_id")?, param(params, "model_id")?);
    let path = config_path(ctx);
    reply(&with_sources(|sources| set_default_model_settings(sources, &path, &provider_id, &model_id))?)
}

/// `handle_set_chat_defaults`: save the model, effort and speed new chats start from; existing
/// chats are untouched.
pub async fn handle_set_chat_defaults(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let path = config_path(ctx);
    let (provider_id, model_id) = (param(params, "provider_id")?, param(params, "model_id")?);
    let selection =
        with_sources(|sources| require_configured_model(sources, &path, &provider_id, &model_id))?;
    let text = |key: &str| selection.get_str(key).and_then(Data::as_str).unwrap_or_default().to_owned();
    let (provider_id, model_id) = (text("provider_id"), text("model_id"));
    let choices = model_choices(ctx).await?;
    let model = catalog_model_info(&provider_id, &model_id, &choices)?;
    // Role only picks the mode default, which is never saved as a named level.
    let effort = match params.get("reasoning_effort") {
        None | Some(Json::Null) => Data::None,
        Some(value) => explicit_effort(model.as_ref(), "builder", value)?.map_or(Data::None, Data::Str),
    };
    let speed = match params.get("speed") {
        None | Some(Json::Null) => Data::None,
        Some(value) => explicit_speed(model.as_ref(), value)?.map_or(Data::None, Data::Str),
    };
    let (provider, model) = (Data::Str(provider_id), Data::Str(model_id));
    reply(&with_sources(|sources| {
        set_chat_defaults_settings(sources, &path, &provider, &model, &effort, &speed)
    })?)
}

/// `_chat_start.explicit_effort(model, role, value)`: a chosen effort checked against this model;
/// the value a chat stores (`None` for the mode default).
pub fn explicit_effort(model: Option<&ModelInfo>, role: &str, value: &Json) -> CodedResult<Option<String>> {
    let Json::String(value) = value else {
        return Err(CodedError::rejected("Reasoning effort must be a named setting"));
    };
    select_reasoning(model, role, Some(Some(value)), "mode_default")?;
    Ok((value != "mode_default").then(|| value.clone()))
}

/// `_chat_start.explicit_speed(model, value)`: a chosen speed checked against this model; the
/// value a chat stores (`None` for standard speed).
pub fn explicit_speed(model: Option<&ModelInfo>, value: &Json) -> CodedResult<Option<String>> {
    let Json::String(value) = value else {
        return Err(CodedError::rejected("Speed must be a named setting"));
    };
    Ok(select_speed(model, Some(Some(value)), true, "session")?.effective)
}

const TRANSPORTS: [&str; 6] = [
    transport::CHAT_COMPLETIONS,
    transport::RESPONSES,
    transport::ANTHROPIC_MESSAGES,
    transport::CODEX_APP_SERVER,
    transport::GEMINI_GENERATE_CONTENT,
    transport::CLAUDE_CODE,
];

fn value_error(message: impl Into<String>) -> CodedError {
    CodedError::rejected(message).with_error_type("ValueError")
}

/// `_session_reasoning.catalog_model_info(provider_id, model_id, choices)`: one configured model's
/// effort and speed capabilities from its `model_choices` row, or `None` if it is not listed.
pub fn catalog_model_info(
    provider_id: &str,
    model_id: &str,
    choices: &[Json],
) -> CodedResult<Option<ModelInfo>> {
    let Some(choice) = choices.iter().find(|item| {
        item.get("provider_id").and_then(Json::as_str) == Some(provider_id)
            && item.get("model_id").and_then(Json::as_str) == Some(model_id)
    }) else {
        return Ok(None);
    };
    let field =
        |key: &str| choice.get(key).map(|value| data_from_json(value).map_err(config_error)).transpose();
    let efforts = field("supported_reasoning_efforts")?;
    let suggested = field("default_reasoning_effort")?;
    let (supported, suggested) =
        reasoning_values(&Advertised::from_data(efforts.as_ref()), suggested_from_data(suggested.as_ref()))
            .map_err(value_error)?;
    let wire = match choice.get("transport") {
        None => transport::CHAT_COMPLETIONS.to_owned(),
        Some(Json::String(name)) if TRANSPORTS.contains(&name.as_str()) => name.clone(),
        Some(_) => return Err(value_error("Configured model transport is unsupported")),
    };
    let text_or = |key: &str, fallback: &str| choice.get(key).map_or_else(|| fallback.to_owned(), py_str);
    let mut model = ModelInfo::new(
        model_id,
        text_or("model_name", model_id),
        text_or("provider_name", ""),
        provider_id,
        "unknown",
    );
    model.supported_reasoning_efforts = supported;
    model.default_reasoning_effort = suggested;
    model.reasoning_control = choice.get("reasoning_control").and_then(Json::as_str).map(str::to_owned);
    model.reasoning_source = text_or("reasoning_source", "unknown");
    let speeds = field("speed_options")?;
    model.speed_options = speed_options(&AdvertisedTiers::from_data(speeds.as_ref()))
        .map_err(value_error)?
        .filter(|tiers| !tiers.is_empty());
    model.speed_control = choice.get("speed_control").and_then(Json::as_str).map(str::to_owned);
    model.speed_source = text_or("speed_source", "unknown");
    model.transport = wire;
    Ok(Some(model.post_init()))
}

/// A download client that could not be built (a bad proxy setting): every request fails as a
/// connection error, as Python's installer fails when its `httpx.Client` cannot be made.
struct UnavailableTransport(String);

impl sylvia_provider::http::HttpTransport for UnavailableTransport {
    fn send(
        &self,
        _request: sylvia_provider::http::HttpRequest,
    ) -> sylvia_provider::provider::BoxFuture<
        '_,
        Result<sylvia_provider::http::HttpResponse, sylvia_provider::http::TransportError>,
    > {
        let message = self.0.clone();
        Box::pin(async move { Err(sylvia_provider::http::TransportError::Connection(message)) })
    }
}

/// The installer's system: the download client (`httpx.Client(timeout=httpx.Timeout(30.0,
/// read=60.0), follow_redirects=False)`) and the free-space reading.
pub(crate) fn claude_install_system() -> sylvia_provider::claude_install::InstallSystem {
    let mut settings =
        sylvia_provider::net::ClientSettings::from_system(sylvia_provider::claude_install::INSTALL_TIMEOUTS);
    settings.follow_redirects = false;
    let transport: std::sync::Arc<dyn sylvia_provider::http::HttpTransport> =
        match sylvia_provider::net::NetworkTransport::new(settings) {
            Ok(transport) => std::sync::Arc::new(transport),
            Err(
                sylvia_provider::proxy::ProxyError::Value(message)
                | sylvia_provider::proxy::ProxyError::Internal(message),
            ) => std::sync::Arc::new(UnavailableTransport(message)),
        };
    sylvia_provider::claude_install::InstallSystem::new(
        transport,
        std::sync::Arc::new(|path: &std::path::Path| crate::desktop_update::storage::disk_free(path)),
    )
}

/// `handle_claude_connection`: Sylvia's own Claude Code copy and its sign-in
/// (`claude_settings_action`). `add_model` needs the provider resolver, which is reloaded after
/// it; a `ProviderConnectionError` is the owner's protocol refusal with its text. The action
/// installs, signs in and runs the binary, so it runs on a blocking thread.
pub async fn handle_claude_connection(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    use sylvia_provider::config::claude_settings::{claude_settings_action, ClaudeSettingsError};
    let action = params.get("action").cloned().unwrap_or(Json::Null);
    let adding = action.as_str() == Some("add_model");
    let resolver = if adding { Some(std::sync::Arc::clone(providers(ctx)?)) } else { ctx.providers.clone() };
    let options = params.get("options").cloned().unwrap_or(Json::Null);
    let storage_root = ctx.storage_root.clone();
    let outcome = tokio::task::spawn_blocking(move || {
        let default_config = default_config_path();
        let env = Environment::current();
        let sources = ConfigSources { default_config: &default_config, seal: &PlatformSeal, env: &env };
        claude_settings_action(
            sources,
            &storage_root,
            &action,
            &options,
            &claude_install_system,
            &now_isoformat,
        )
    })
    .await
    .map_err(|error| CodedError::new("internal_error", error.to_string()))?;
    let result = match outcome {
        Ok(result) => result,
        Err(ClaudeSettingsError::Connection(message)) => {
            return Err(CodedError::rejected(message).with_error_type("OwnerProtocolError"))
        }
        Err(ClaudeSettingsError::Config(failure)) => return Err(config_error(failure)),
        Err(ClaudeSettingsError::Internal(message)) => {
            return Err(CodedError::new("internal_error", message).with_error_type("Exception"))
        }
    };
    if adding {
        if let Some(resolver) = resolver {
            resolver.reload().await?;
        }
    }
    Ok(Json::Object(result))
}

/// `handle_chatgpt_connection`: `chatgpt_settings_action(storage_root, action, model)`, with a
/// `CodexConnectionError` replied as `OwnerProtocolError`. The resolver reloads after `add_model`,
/// and after any action that refreshed the saved models' effort or speed options.
pub async fn handle_chatgpt_connection(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    use sylvia_provider::config::chatgpt_settings::{chatgpt_settings_action, ChatGPTSettingsError};
    use sylvia_provider::config::host_browser::open_in_browser;
    let action = params.get("action").cloned().unwrap_or(Json::Null);
    let adding = action.as_str() == Some("add_model");
    let resolver = if adding { Some(std::sync::Arc::clone(providers(ctx)?)) } else { ctx.providers.clone() };
    let model = params.get("model").cloned().unwrap_or(Json::Null);
    let storage_root = ctx.storage_root.clone();
    let outcome = tokio::task::spawn_blocking(move || {
        let default_config = default_config_path();
        let env = Environment::current();
        let sources = ConfigSources { default_config: &default_config, seal: &PlatformSeal, env: &env };
        chatgpt_settings_action(
            sources,
            &storage_root,
            &action,
            &model,
            None,
            &open_in_browser,
            &now_isoformat,
        )
    })
    .await
    .map_err(|error| CodedError::new("internal_error", error.to_string()))?;
    let result = match outcome {
        Ok(result) => result,
        Err(ChatGPTSettingsError::Codex(error)) if error.is_connection_error() => {
            return Err(CodedError::rejected(error.message()).with_error_type("OwnerProtocolError"))
        }
        Err(ChatGPTSettingsError::Codex(error)) => {
            return Err(CodedError::new("internal_error", error.message()).with_error_type(error.class_name()))
        }
        Err(ChatGPTSettingsError::Config(failure)) => return Err(config_error(failure)),
    };
    let refreshed = result.get("saved_models_refreshed").is_some_and(sylvia_provider::pyobj::truthy);
    if adding || refreshed {
        if let Some(resolver) = resolver {
            resolver.reload().await?;
        }
    }
    Ok(Json::Object(result))
}

/// `handle_open_provider_setup`: `open_provider_setup(params.get("preset_id"))`, which opens
/// only a shipped service's setup page.
pub async fn handle_open_provider_setup(_ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let preset_id = param(params, "preset_id")?;
    let opened = tokio::task::spawn_blocking(move || {
        sylvia_provider::config::provider_settings::open_provider_setup(&preset_id)
    })
    .await
    .map_err(|error| CodedError::new("internal_error", error.to_string()))?;
    reply(&opened.map_err(config_error)?)
}

/// `_MAX_ADMIN_AUDIT_VIEW_BYTES` (`owner_process.py`): the largest record the live view carries.
const MAX_ADMIN_AUDIT_VIEW_BYTES: usize = 256 * 1024;

/// `CommandRejected(message)`.
fn command_rejected(message: &str) -> CodedError {
    CodedError::rejected(message)
}

/// `OwnerProtocolError(message)`.
fn protocol(message: &str) -> CodedError {
    CodedError::rejected(message).with_error_type("OwnerProtocolError")
}

/// `handle_admin_full_audit_status`: `AuthorityOwner.admin_full_audit_status()`.
pub async fn handle_admin_full_audit_status(ctx: &DispatchContext, _params: &JsonMap) -> CodedResult<Json> {
    let _operation = super::operation(ctx)?;
    Ok(Json::Object(ctx.store.narration().admin_full_audit_status()))
}

/// `handle_set_admin_full_audit`: `set_admin_full_audit(_optional_bool(params, "enabled"))`.
pub async fn handle_set_admin_full_audit(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let enabled = super::campaigns::optional_bool(params, "enabled")?;
    let _operation = super::operation(ctx)?;
    Ok(Json::Object(ctx.store.narration().set_admin_full_audit(enabled)))
}

/// `handle_admin_full_audit_record`: one exact trace, `{"index", "trace"}`, unless its compact
/// ASCII encoding is over the live-view limit, which answers with its size instead.
pub async fn handle_admin_full_audit_record(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let index = super::campaigns::required_nonnegative_int(params, "index")?;
    // `admin_full_audit_record(index)`: `with self._operation(), self._narration_lock:`.
    let _operation = super::operation(ctx)?;
    let trace = ctx.store.narration().with(|inner| {
        usize::try_from(index).ok().and_then(|index| inner.inspector_trace.get(index).cloned())
    });
    let Some(trace) = trace else {
        return Err(command_rejected("Admin Full Audit record is unavailable"));
    };
    let mut record = JsonMap::new();
    record.insert("index".into(), index.into());
    record.insert("trace".into(), trace);
    let record = Json::Object(record);
    let encoded = record_view_bytes(&record);
    if encoded > MAX_ADMIN_AUDIT_VIEW_BYTES {
        let mut reply = JsonMap::new();
        reply.insert("index".into(), index.into());
        reply.insert("trace".into(), Json::Null);
        reply.insert("unavailable_reason".into(), "record_too_large_for_live_view".into());
        reply.insert("bytes".into(), encoded.into());
        return Ok(Json::Object(reply));
    }
    Ok(record)
}

/// The `errno` Python gives an `OSError`: on Windows CPython's `winerror_to_errno` for the
/// codes whose errno `audit_export_save_error` tests (any other code is `EINVAL`), elsewhere the
/// raw error number.
fn python_errno(error: &std::io::Error) -> Option<i32> {
    let raw = error.raw_os_error()?;
    if !cfg!(windows) {
        return Some(raw);
    }
    Some(match raw {
        2 | 3 | 15 | 18 | 53 | 67 | 161 | 206 => 2,
        5 | 16 | 19..=36 | 65 | 82 | 83 | 108 | 132 | 158 | 167 | 10013 => 13,
        112 => 28,
        267 => 20,
        _ => 22,
    })
}

/// `audit_export_save_error(error)`: the save failure explained without paths or payloads.
fn audit_export_save_error(error: &std::io::Error) -> String {
    let errno = python_errno(error);
    let winerror = if cfg!(windows) { error.raw_os_error() } else { None };
    // ENOSPC; EACCES, EPERM, EROFS; ENOENT, ENOTDIR, ENODEV (Linux and Windows numbering).
    let (enospc, permission, unavailable): (&[i32], &[i32], &[i32]) = (&[28], &[13, 1, 30], &[2, 20, 19]);
    let has = |set: &[i32]| errno.is_some_and(|errno| set.contains(&errno));
    let windows = |set: &[i32]| winerror.is_some_and(|code| set.contains(&code));
    if has(enospc) || windows(&[39, 112]) {
        return "Audit export could not be saved: not enough space. Free space or choose another drive."
            .into();
    }
    if has(permission) || windows(&[5, 32, 33]) {
        return "Audit export could not be saved: the destination is not writable. \
                Choose a writable folder or close applications using the destination file."
            .into();
    }
    if has(unavailable) || windows(&[3, 15, 21]) {
        return "Audit export could not be saved: the destination is unavailable. \
                Choose an available folder or drive."
            .into();
    }
    "Audit export file could not be saved. Try another destination.".into()
}

/// One value as `json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2)`, each line
/// after the first indented four more spaces, as the export writes its list items.
fn audit_export_item(value: &Json) -> Vec<u8> {
    let options = sylvia_base::pyjson::Dump {
        ensure_ascii: false,
        item_separator: ",",
        key_separator: ": ",
        indent: Some(2),
        ..sylvia_base::pyjson::Dump::DEFAULT
    };
    let encoded = sylvia_base::pyjson::dumps(value, options);
    format!("    {}", encoded.replace('\n', "\n    ")).into_bytes()
}

/// `export_admin_full_audit`'s document: the enabled flag, the traces and the safe mechanics
/// diagnostics, written exactly as Python's streaming writer writes them.
fn audit_export_document(enabled: bool, traces: &[Json], diagnostics: &[Json]) -> Vec<u8> {
    let mut out = Vec::new();
    out.extend_from_slice(b"{\n  \"enabled\": ");
    out.extend_from_slice(if enabled { b"true" } else { b"false" });
    out.extend_from_slice(format!(",\n  \"trace_count\": {},\n  \"traces\": [", traces.len()).as_bytes());
    for (index, trace) in traces.iter().enumerate() {
        out.extend_from_slice(if index == 0 { b"\n" } else { b",\n" });
        out.extend_from_slice(&audit_export_item(trace));
    }
    if !traces.is_empty() {
        out.extend_from_slice(b"\n  ");
    }
    out.extend_from_slice(b"],\n  \"safe_mechanics_diagnostics\": [");
    for (index, diagnostic) in diagnostics.iter().enumerate() {
        out.extend_from_slice(if index == 0 { b"\n" } else { b",\n" });
        out.extend_from_slice(&audit_export_item(diagnostic));
    }
    if !diagnostics.is_empty() {
        out.extend_from_slice(b"\n  ");
    }
    out.extend_from_slice(b"]\n}\n");
    out
}

/// `NamedTemporaryFile(prefix=f".{name}.", suffix=".tmp", dir=folder, delete=False)`: a new
/// file that did not exist, opened for writing.
fn audit_temporary_file(folder: &std::path::Path, name: &str) -> std::io::Result<(PathBuf, std::fs::File)> {
    const LETTERS: &[u8] = b"abcdefghijklmnopqrstuvwxyz0123456789_";
    for _ in 0..100 {
        let mut random = [0u8; 8];
        getrandom::getrandom(&mut random).expect("the operating system's random source is available");
        let tail: String =
            random.iter().map(|byte| LETTERS[*byte as usize % LETTERS.len()] as char).collect();
        let path = folder.join(format!(".{name}.{tail}.tmp"));
        match std::fs::OpenOptions::new().write(true).create_new(true).open(&path) {
            Ok(file) => return Ok((path, file)),
            Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
            Err(error) => return Err(error),
        }
    }
    Err(std::io::Error::new(std::io::ErrorKind::AlreadyExists, "No usable temporary file name found"))
}

/// `handle_export_admin_full_audit`: one immutable audit snapshot, written atomically to the
/// selected file (a temporary file beside it, fsynced, then renamed over it).
pub async fn handle_export_admin_full_audit(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let text = super::campaigns::required(params, "destination_path")?;
    let selected = std::path::PathBuf::from(&text);
    let Some(name) = selected.file_name().map(|name| name.to_string_lossy().into_owned()) else {
        return Err(protocol("Audit export destination must be an absolute file path"));
    };
    if !selected.is_absolute() {
        return Err(protocol("Audit export destination must be an absolute file path"));
    }
    let parent = selected.parent().unwrap_or(&selected);
    let parent = super::campaigns::resolve_strict(parent)
        .map_err(|error| command_rejected(&audit_export_save_error(&error)))?;
    let destination = parent.join(&name);
    // `not destination.exists() or destination.is_file()`.
    let regular_file = match std::fs::metadata(&destination) {
        Ok(metadata) => metadata.is_file(),
        Err(error) if error.kind() == std::io::ErrorKind::NotFound => true,
        Err(error) => return Err(command_rejected(&audit_export_save_error(&error))),
    };
    if !regular_file {
        return Err(protocol(
            "Audit export destination is not a regular file. Choose a file in a writable folder.",
        ));
    }
    // The snapshot, under the owner's operation and one narration lock; the file is written
    // after both, as Python's `with` block ends before the write.
    let operation = super::operation(ctx)?;
    let (enabled, traces, diagnostics) = ctx.store.narration().with(|inner| {
        (inner.admin_full_audit_enabled, inner.inspector_trace.clone(), inner.mechanics_diagnostics.clone())
    });
    drop(operation);
    let document = audit_export_document(enabled, &traces, &diagnostics);
    let written = tokio::task::spawn_blocking(move || -> std::io::Result<u64> {
        use std::io::Write;
        // `destination.parent.mkdir(parents=False, exist_ok=True)`.
        if let Err(error) = std::fs::create_dir(&parent) {
            if !(error.kind() == std::io::ErrorKind::AlreadyExists && parent.is_dir()) {
                return Err(error);
            }
        }
        let (temporary, mut file) = audit_temporary_file(&parent, &name)?;
        let outcome = (|| {
            file.write_all(&document)?;
            file.flush()?;
            file.sync_all()?;
            let size = file.metadata()?.len();
            drop(file);
            std::fs::rename(&temporary, &destination)?;
            Ok(size)
        })();
        if outcome.is_err() {
            // Best-effort cleanup must not replace the original save failure.
            let _ = std::fs::remove_file(&temporary);
        }
        outcome
    })
    .await
    .map_err(|error| CodedError::new("internal_error", error.to_string()))?;
    let size = written.map_err(|error| command_rejected(&audit_export_save_error(&error)))?;
    let mut reply = JsonMap::new();
    reply.insert("status".into(), "exported".into());
    reply.insert("trace_count".into(), traces.len().into());
    reply.insert("bytes".into(), size.into());
    Ok(Json::Object(reply))
}

/// The live view's size check: `len(json.dumps(_json_value(record), ensure_ascii=True,
/// separators=(",", ":"), allow_nan=False).encode("utf-8"))`.
fn record_view_bytes(record: &Json) -> usize {
    let options = sylvia_base::pyjson::Dump {
        item_separator: ",",
        key_separator: ":",
        ..sylvia_base::pyjson::Dump::DEFAULT
    };
    sylvia_base::pyjson::dump_bytes(record, options).len()
}

#[cfg(test)]
mod tests {
    use super::*;

    /// `tests/vectors/admin_full_audit_vectors.json`, from `generate_admin_full_audit_vectors.py`:
    /// the export document Python writes for two traces (nested values, Unicode, escapes, empty
    /// containers, a float) and one diagnostic, the document with nothing captured, and the live
    /// view's byte count for each record. The `admin-commands` session covers the commands
    /// themselves, with nothing captured.
    #[test]
    fn audit_export_and_record_size_match_python() {
        let path = crate::util::source_root()
            .join("crates/sylvia-owner/tests/vectors/admin_full_audit_vectors.json");
        let text = std::fs::read_to_string(&path)
            .unwrap_or_else(|error| panic!("cannot read {}: {error}", path.display()));
        let vectors: Json = serde_json::from_str(&text).unwrap();
        let traces = vectors["traces"].as_array().unwrap();
        let diagnostics = vectors["diagnostics"].as_array().unwrap();
        assert_eq!(
            String::from_utf8(audit_export_document(true, traces, diagnostics)).unwrap(),
            vectors["document"].as_str().unwrap()
        );
        assert_eq!(
            String::from_utf8(audit_export_document(false, &[], &[])).unwrap(),
            vectors["empty_document"].as_str().unwrap()
        );
        for (index, trace) in traces.iter().enumerate() {
            let record = serde_json::json!({"index": index, "trace": trace});
            assert_eq!(
                record_view_bytes(&record) as u64,
                vectors["record_bytes"][index].as_u64().unwrap(),
                "record {index}"
            );
        }
    }

    /// `audit_export_save_error`: each errno and Windows code Python names, and the fallback.
    #[test]
    fn audit_export_save_errors_follow_python() {
        let message = |raw: i32| audit_export_save_error(&std::io::Error::from_raw_os_error(raw));
        let space = "Audit export could not be saved: not enough space. Free space or choose another drive.";
        let writable = "Audit export could not be saved: the destination is not writable. \
                        Choose a writable folder or close applications using the destination file.";
        let unavailable = "Audit export could not be saved: the destination is unavailable. \
                           Choose an available folder or drive.";
        let other = "Audit export file could not be saved. Try another destination.";
        let cases: &[(i32, &str)] = if cfg!(windows) {
            &[
                (112, space),
                (39, space),
                (5, writable),
                (32, writable),
                (19, writable),
                (2, unavailable),
                (3, unavailable),
                (267, unavailable),
                (21, writable),
                (87, other),
            ]
        } else {
            &[
                (28, space),
                (13, writable),
                (1, writable),
                (30, writable),
                (2, unavailable),
                (20, unavailable),
                (19, unavailable),
                (22, other),
            ]
        };
        for (raw, want) in cases {
            assert_eq!(message(*raw), *want, "code {raw}");
        }
    }
}
