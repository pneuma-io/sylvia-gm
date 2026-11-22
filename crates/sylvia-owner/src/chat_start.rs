//! `interfaces/_chat_start.py`: the model, effort and speed a new chat starts from, settled
//! before its first call.
//!
//! `explicit_effort` and `explicit_speed` were ported by lane 4 beside `set_chat_defaults`
//! (`commands::providers`); this module adds the rest of the file.

use crate::commands::campaigns::store_error;
use crate::commands::providers::{
    catalog_model_info, config_path, explicit_effort, explicit_speed, model_choices, with_sources,
};
use crate::dispatch::DispatchContext;
use sylvia_base::pyyaml::Data;
use sylvia_base::{CodedResult, Json, JsonMap};
use sylvia_provider::config::provider_settings::provider_settings;
use sylvia_provider::config::provider_writes::set_session_speed_settings;
use sylvia_provider::speed::{supported_speed_ids, STANDARD_SPEED};

/// `ChatStart`: one new chat's settled start. `None` effort is the mode default; `None` speed is
/// standard.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ChatStart {
    pub provider_id: String,
    pub model_id: String,
    pub reasoning_effort: Option<String>,
    pub speed: Option<String>,
}

/// `params.get(key)`, with JSON `null` as Python's `None`.
fn chosen<'a>(params: &'a JsonMap, key: &str) -> Option<&'a Json> {
    params.get(key).filter(|value| !value.is_null())
}

/// `resolve_chat_start(choices, defaults, role=, provider_id=, model_id=, params=)`: a chosen
/// value must be one the model supports; an absent one inherits the Settings default when the
/// model supports it, and otherwise the mode default effort or standard speed.
pub fn resolve_chat_start(
    choices: &[Json],
    defaults: Option<&Data>,
    role: &str,
    provider_id: &str,
    model_id: &str,
    params: &JsonMap,
) -> CodedResult<ChatStart> {
    let model = catalog_model_info(provider_id, model_id, choices)?;
    let saved = |key: &str| defaults.and_then(|defaults| defaults.get_str(key)).and_then(Data::as_str);
    let effort = match chosen(params, "reasoning_effort") {
        Some(value) => explicit_effort(model.as_ref(), role, value)?,
        None => {
            let supported =
                model.as_ref().and_then(|model| model.supported_reasoning_efforts.as_deref()).unwrap_or(&[]);
            saved("reasoning_effort")
                .filter(|effort| *effort == "provider_default" || supported.iter().any(|item| item == effort))
                .map(str::to_owned)
        }
    };
    let speed = match chosen(params, "speed") {
        Some(value) => explicit_speed(model.as_ref(), value)?,
        None => saved("speed")
            .filter(|speed| supported_speed_ids(model.as_ref()).contains(speed))
            .map(str::to_owned),
    };
    Ok(ChatStart {
        provider_id: provider_id.to_owned(),
        model_id: model_id.to_owned(),
        reasoning_effort: effort,
        speed,
    })
}

/// `chat_start_for(ctx, role=, provider_id=, model_id=, params=)`: resolved against the current
/// Settings defaults (`chat_defaults` when it is a mapping) and the model catalog.
pub async fn chat_start_for(
    ctx: &DispatchContext,
    role: &str,
    provider_id: &str,
    model_id: &str,
    params: &JsonMap,
) -> CodedResult<ChatStart> {
    let path = config_path(ctx);
    let settings = with_sources(|sources| provider_settings(sources, &path))?;
    let defaults = settings.get_str("chat_defaults").filter(|value| value.as_dict().is_some());
    let choices = model_choices(ctx).await?;
    resolve_chat_start(&choices, defaults, role, provider_id, model_id, params)
}

/// `session_exists(ctx, session_id)`: whether a creation command is a replay. Only a
/// `session_unavailable` refusal means the session is new; any other failure is raised.
pub async fn session_exists(ctx: &DispatchContext, session_id: &str) -> CodedResult<bool> {
    match ctx.store.session_context(session_id).await {
        Ok(_) => Ok(true),
        Err(error) => {
            let coded = store_error(error);
            if coded.code == "session_unavailable" {
                Ok(false)
            } else {
                Err(coded)
            }
        }
    }
}

/// `apply_chat_start(ctx, session_id, start)`: save a settled start on the new chat. Nothing
/// here calls a model.
pub async fn apply_chat_start(ctx: &DispatchContext, session_id: &str, start: &ChatStart) -> CodedResult<()> {
    if let Some(effort) = &start.reasoning_effort {
        ctx.store
            .update_agent_session_reasoning(session_id, Some(effort), None)
            .await
            .map_err(store_error)?;
    }
    if let Some(speed) = &start.speed {
        let path = config_path(ctx);
        let (session, speed) = (Data::Str(session_id.to_owned()), Data::Str(speed.clone()));
        with_sources(|sources| set_session_speed_settings(sources, &path, &session, &speed))?;
    }
    Ok(())
}

/// `chat_start_projection(start)`: what a new chat started with, in owner-protocol words.
pub fn chat_start_projection(start: &ChatStart) -> Json {
    let mut value = JsonMap::new();
    value.insert("provider_id".into(), start.provider_id.as_str().into());
    value.insert("model_id".into(), start.model_id.as_str().into());
    value.insert(
        "reasoning_effort".into(),
        start.reasoning_effort.as_deref().unwrap_or("mode_default").into(),
    );
    value.insert("speed".into(), start.speed.as_deref().unwrap_or(STANDARD_SPEED).into());
    Json::Object(value)
}
