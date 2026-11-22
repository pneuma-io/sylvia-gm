//! Provider commands: `interfaces/commands/providers.py`.
//!
//! Also here, beside `set_chat_defaults`, three helpers from lane 5's files (ported by lane 4 at
//! the manager's request): `interfaces/_chat_start.explicit_effort` and `explicit_speed`, and
//! `interfaces/_session_reasoning.catalog_model_info`.

use crate::dispatch::DispatchContext;
use std::path::PathBuf;
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};
use sylvia_provider::config::env::Environment;
use sylvia_provider::config::protection::PlatformSeal;
use sylvia_provider::config::provider_settings::{
    now_isoformat, provider_settings, require_configured_model, ConfigSources,
};
use sylvia_provider::config::provider_writes::{
    remove_provider_settings, save_provider_settings, set_chat_defaults_settings, set_default_model_settings,
    SaveProvider,
};
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
fn providers(ctx: &DispatchContext) -> CodedResult<&std::sync::Arc<dyn crate::services::ProviderResolver>> {
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
