//! Session commands: `interfaces/commands/sessions.py`, on lane 4's `CampaignSessionLoop`.
//! Here: `resume_agent_session`, the reasoning and speed switches and `steer_session`. The
//! turn commands (`submit_session_input`, `start_dm_session`, `manual_retry_session`,
//! `stop_agent_session`) follow; `session_context`/`session_history_page` and the narration
//! stream commands wait for lane 3's narration attempts.

use crate::dispatch::DispatchContext;
use std::sync::Arc;
use sylvia_agent::session_loop::campaign::CampaignSessionLoop;
use sylvia_base::{CodedError, CodedResult, Json, JsonMap};
use sylvia_domain::session::SessionRecordFields;
use sylvia_provider::config::Data;

/// `handle_resume_agent_session`: `ctx.bus.resume_agent_session(session_id)`, through the
/// supervisor's verified owner (`LocalOwnerSupervisor.resume_agent_session`) to
/// `AuthorityOwner.resume_agent_session`: one explicitly selected stopped writer session is
/// active again.
pub async fn handle_resume_agent_session(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let session_id = super::campaigns::required(params, "session_id")?;
    let record = ctx
        .store
        .call(move |store| store.resume_agent_session(&session_id))
        .await
        .map_err(super::campaigns::store_error)?;
    Ok(super::builder::session_record_value(&record))
}

/// `_MAX_USER_TEXT_CHARS`: the steering and input text limit.
const MAX_USER_TEXT_CHARS: usize = 131_072;

/// `OwnerProtocolError(message)`.
fn protocol(message: &str) -> CodedError {
    CodedError::rejected(message).with_error_type("OwnerProtocolError")
}

/// The owner's `CampaignSessionLoop` (lane 4). The owner builds one at launch; a context built
/// without it (a unit test) has none.
pub(crate) fn campaign_loop(ctx: &DispatchContext) -> CodedResult<&Arc<CampaignSessionLoop>> {
    ctx.campaign_loop
        .as_ref()
        .ok_or_else(|| CodedError::new("internal_error", "The session loop is not running"))
}

/// A configuration value as the JSON the handlers return.
fn config_json(value: &Data) -> CodedResult<Json> {
    sylvia_provider::config::reply_json(value).map_err(super::providers::config_error)
}

/// `session_model_once_settings(config_path, session_id=...)`: a pending next-reply model as
/// `(provider_id, model_id)`, or `None`.
fn pending_once_model(ctx: &DispatchContext, session_id: &str) -> CodedResult<Option<(String, String)>> {
    let path = super::providers::config_path(ctx);
    let session = Data::Str(session_id.to_owned());
    let pending = super::providers::with_sources(|sources| {
        sylvia_provider::config::provider_settings::session_model_once_settings(sources, &path, &session)
    })?;
    Ok(match config_json(&pending)? {
        Json::Object(model) => match (model.get("provider_id"), model.get("model_id")) {
            (Some(Json::String(provider)), Some(Json::String(model))) => {
                Some((provider.clone(), model.clone()))
            }
            _ => None,
        },
        _ => None,
    })
}

fn once_pair(model: &Option<(String, String)>) -> Option<(&str, &str)> {
    model.as_ref().map(|(provider, model)| (provider.as_str(), model.as_str()))
}

/// `AuthorityOwner.session_context(session_id).session`.
async fn session_fields(ctx: &DispatchContext, session_id: &str) -> CodedResult<SessionRecordFields> {
    let context = ctx.store.session_context(session_id).await.map_err(super::campaigns::store_error)?;
    Ok(context.session.fields().clone())
}

/// `handle_switch_session_reasoning`: `_switch_session_reasoning(...)`. A saved (`current`) or
/// next-reply (`once`) effort is validated at the same idle-session boundary as a model change.
pub async fn handle_switch_session_reasoning(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let Some(effort) = params.get("effort") else {
        return Err(protocol("Reasoning selection requires effort"));
    };
    // The call's arguments, in Python's order.
    let session_id = super::campaigns::required(params, "session_id")?;
    let mode = super::campaigns::optional_text(params, "mode")?.unwrap_or_else(|| "current".to_owned());
    let choices = super::providers::model_choices(ctx).await?;
    campaign_loop(ctx)?.assert_idle(&session_id)?;
    if mode != "current" && mode != "once" {
        return Err(protocol("Reasoning scope must be current chat or next reply"));
    }
    let effort = match effort {
        Json::Null => None,
        Json::String(text) => Some(text.clone()),
        _ => return Err(protocol("Reasoning effort must be a native setting or null")),
    };
    let prior = session_fields(ctx, &session_id).await?;
    let pending_model = pending_once_model(ctx, &session_id)?;
    let saved = if mode == "current" {
        effort.clone().filter(|value| value != "mode_default")
    } else {
        prior.reasoning_effort.clone()
    };
    let once = if mode == "once" { effort.clone() } else { prior.reasoning_effort_once.clone() };
    let mut updated = prior.clone();
    updated.reasoning_effort = saved.clone();
    updated.reasoning_effort_once = once.clone();
    if mode == "current" {
        let model = crate::session_reasoning::selected_model_info(&updated, &choices)?;
        sylvia_provider::reasoning::select_reasoning(
            model.as_ref(),
            updated.role.as_str(),
            saved.as_deref().map(Some),
            "mode_default",
        )?;
    }
    let state = crate::session_reasoning::reasoning_state(&updated, &choices, once_pair(&pending_model))?;
    // Clearing one stale preference must stay possible when both saved values became
    // unsupported; a valid chat change may leave an older once choice for a separate reset.
    let valid = state.get("valid") == Some(&Json::Bool(true));
    let clearing =
        (mode == "once" && effort.is_none()) || (mode == "current" && prior.reasoning_effort_once.is_some());
    if !valid && !clearing {
        let message = state.get("error").map(sylvia_provider::pyobj::py_str).unwrap_or_default();
        return Err(CodedError::rejected(message));
    }
    let record = ctx
        .store
        .update_agent_session_reasoning(&session_id, saved.as_deref(), once.as_deref())
        .await
        .map_err(super::campaigns::store_error)?;
    let mut reply = JsonMap::new();
    reply.insert("status".into(), if mode == "once" { "staged_once" } else { "switched" }.into());
    reply.insert("session".into(), super::builder::session_record_value(&record));
    reply.insert("reasoning_state".into(), Json::Object(state));
    Ok(Json::Object(reply))
}

/// `handle_switch_session_speed`: `_switch_session_speed(...)`. One chat's speed, saved at the
/// same idle-session boundary as its model and effort.
pub async fn handle_switch_session_speed(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let Some(speed) = params.get("speed") else {
        return Err(protocol("Speed selection requires speed"));
    };
    let session_id = super::campaigns::required(params, "session_id")?;
    let choices = super::providers::model_choices(ctx).await?;
    campaign_loop(ctx)?.assert_idle(&session_id)?;
    let speed = match speed {
        Json::Null => None,
        Json::String(text) => Some(text.clone()),
        _ => return Err(protocol("Speed must be a native setting, standard, or null")),
    };
    let session = session_fields(ctx, &session_id).await?;
    let model = crate::session_reasoning::selected_model_info(&session, &choices)?;
    let chosen =
        sylvia_provider::speed::select_speed(model.as_ref(), speed.as_deref().map(Some), true, "session")?
            .effective;
    let path = super::providers::config_path(ctx);
    let (session_key, chosen_value) =
        (Data::Str(session_id.clone()), chosen.clone().map_or(Data::None, Data::Str));
    super::providers::with_sources(|sources| {
        sylvia_provider::config::provider_writes::set_session_speed_settings(
            sources,
            &path,
            &session_key,
            &chosen_value,
        )
    })?;
    let pending_model = pending_once_model(ctx, &session_id)?;
    let state = crate::session_reasoning::speed_state(
        &session,
        &choices,
        chosen.as_deref(),
        once_pair(&pending_model),
    )?;
    let mut reply = JsonMap::new();
    reply.insert("status".into(), "switched".into());
    reply.insert("speed_state".into(), Json::Object(state));
    Ok(Json::Object(reply))
}

/// `handle_steer_session`: one steering message for the session's live run. With no live run
/// the loop refuses it (`Agent session has no active provider work`).
pub async fn handle_steer_session(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let steering_text = super::campaigns::required(params, "text")?;
    if sylvia_base::pystr::len(&steering_text) > MAX_USER_TEXT_CHARS {
        return Err(protocol("text exceeds the session steering limit"));
    }
    let session_id = super::campaigns::required(params, "session_id")?;
    let message_id = super::campaigns::required(params, "message_id")?;
    campaign_loop(ctx)?.steer(&session_id, &steering_text, &message_id).await?;
    let mut reply = JsonMap::new();
    reply.insert("status".into(), "accepted".into());
    Ok(Json::Object(reply))
}
