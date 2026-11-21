//! Provider commands: `interfaces/commands/providers.py`.

use crate::dispatch::DispatchContext;
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};
use sylvia_provider::config::env::Environment;
use sylvia_provider::config::protection::PlatformSeal;
use sylvia_provider::config::provider_settings::{provider_settings, ConfigSources};
use sylvia_provider::config::{default_config_path, reply_json, ConfigFailure};

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

/// `handle_model_choices`: `tuple(ctx.model_choices())`.
pub async fn handle_model_choices(ctx: &DispatchContext, _params: &JsonMap) -> CodedResult<Json> {
    let Some(providers) = ctx.providers.as_ref() else {
        return Err(CodedError::new("internal_error", "The provider resolver is not available.")
            .with_error_type("RuntimeError"));
    };
    Ok(Json::Array(providers.model_choices().await?))
}

/// `handle_provider_settings`: `provider_settings(ctx.storage_root / "config.yaml")`.
pub async fn handle_provider_settings(ctx: &DispatchContext, _params: &JsonMap) -> CodedResult<Json> {
    let default_config = default_config_path();
    let env = Environment::current();
    let sources = ConfigSources { default_config: &default_config, seal: &PlatformSeal, env: &env };
    let settings = provider_settings(sources, &ctx.storage_root.join("config.yaml")).map_err(config_error)?;
    reply_json(&settings).map_err(config_error)
}
