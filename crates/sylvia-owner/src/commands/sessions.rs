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

/// A history page's `before_sequence` or `limit`, as the store reads it: an integer (not a
/// bool) passes as itself; anything else is given a value the store refuses with Python's
/// `ValueError` text. An integer beyond 64 bits keeps its sign: a positive cursor past
/// every sequence reads from the end, a limit past 200 is refused.
fn page_integer(value: &Json, refused: i64) -> i64 {
    match value {
        Json::Number(number) if number.is_i64() => number.as_i64().unwrap_or(refused),
        Json::Number(number) if number.is_u64() => i64::MAX,
        _ => refused,
    }
}

/// `handle_session_context`, for `session_context` and `session_history_page`: the chat's
/// display history (a page of it for `session_history_page`, read after the activity basis so
/// a completed result includes its final prose), its private-thinking index, the loop's live
/// projections, and its reasoning, speed and model state.
pub async fn handle_session_context(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    use crate::owner_json::{json_value, session_messages_value};
    let session_id = super::campaigns::required(params, "session_id")?;
    let session_loop = campaign_loop(ctx)?;
    let activity = session_loop.builder_activity(&session_id, None).await?;
    let mut page_fields = JsonMap::new();
    let (display_session, display_messages) = if ctx.method == "session_history_page" {
        let before = match params.get("before_sequence") {
            None | Some(Json::Null) => None,
            Some(value) => Some(page_integer(value, 0)),
        };
        let limit = params.get("limit").map_or(50, |value| page_integer(value, 0));
        let id = session_id.clone();
        let page = ctx
            .store
            .call(move |store| store.session_history_page(&id, before, limit))
            .await
            .map_err(super::campaigns::store_error)?;
        let number = |value: Option<i64>| value.map_or(Json::Null, Json::from);
        let records = |records: &[JsonMap]| -> CodedResult<Json> {
            Ok(Json::Array(
                records
                    .iter()
                    .map(|record| json_value(&Json::Object(record.clone())))
                    .collect::<CodedResult<_>>()?,
            ))
        };
        page_fields.insert("next_before_sequence".into(), number(page.next_before_sequence));
        page_fields.insert("latest_sequence".into(), number(page.latest_sequence));
        page_fields.insert("latest_user_sequence".into(), number(page.latest_user_sequence));
        page_fields.insert("launch_messages".into(), session_messages_value(&page.launch_messages)?);
        page_fields.insert("latest_canonical_sequence".into(), number(page.latest_canonical_sequence));
        page_fields.insert("act_markers".into(), records(&page.act_markers)?);
        page_fields.insert(
            "campaign_ending".into(),
            match &page.campaign_ending {
                Some(ending) => json_value(&Json::Object(ending.clone()))?,
                None => Json::Null,
            },
        );
        page_fields.insert("campaign_endings".into(), records(&page.campaign_endings)?);
        let questions: Vec<_> = page
            .builder_question_messages
            .iter()
            .filter(|message| {
                sylvia_agent::builder::question_answers::builder_question_details(message.content()).is_some()
            })
            .cloned()
            .collect();
        page_fields.insert("builder_question_messages".into(), session_messages_value(&questions)?);
        (page.session, page.messages)
    } else {
        let context = ctx.store.session_context(&session_id).await.map_err(super::campaigns::store_error)?;
        (context.session, context.messages)
    };
    let path = super::providers::config_path(ctx);
    let settings = config_json(&super::providers::with_sources(|sources| {
        sylvia_provider::config::provider_settings::provider_settings(sources, &path)
    })?)?;
    let pending_value = {
        let session = Data::Str(session_id.clone());
        config_json(&super::providers::with_sources(|sources| {
            sylvia_provider::config::provider_settings::session_model_once_settings(sources, &path, &session)
        })?)?
    };
    let pending_model = pending_once_model(ctx, &session_id)?;
    let thinking = sylvia_app::authority::narration_attempts::private_provider_index(&ctx.store, &session_id)
        .await
        .map_err(sylvia_app::authority::actions::ActionError::into_coded)?;
    let fields = display_session.fields().clone();
    let mut reply = JsonMap::new();
    reply.insert("session".into(), super::builder::session_record_value(&display_session));
    reply.insert("messages".into(), session_messages_value(&display_messages)?);
    reply.insert(
        "thinking".into(),
        Json::Array(
            thinking
                .iter()
                .map(|entry| json_value(&Json::Object(entry.clone())))
                .collect::<CodedResult<_>>()?,
        ),
    );
    for (key, value) in page_fields {
        reply.insert(key, value);
    }
    reply.insert(
        "provisional".into(),
        Json::Array(
            session_loop.provisional_session_projection(&session_id).into_iter().map(Json::Object).collect(),
        ),
    );
    reply.insert("activity".into(), activity.map_or(Json::Null, Json::Object));
    reply.insert(
        "activity_progress".into(),
        session_loop.activity_progress(&session_id).await?.map_or(Json::Null, Json::Object),
    );
    reply.insert(
        "context_status_hint".into(),
        session_loop.context_status_hint(&session_id).map_or(Json::Null, Json::String),
    );
    let choices = super::providers::model_choices(ctx).await?;
    reply.insert(
        "reasoning_state".into(),
        Json::Object(crate::session_reasoning::reasoning_state(
            &fields,
            &choices,
            once_pair(&pending_model),
        )?),
    );
    let saved_speed = {
        let session = Data::Str(session_id.clone());
        config_json(&super::providers::with_sources(|sources| {
            sylvia_provider::config::provider_settings::session_speed_settings(sources, &path, &session)
        })?)?
    };
    let choices = super::providers::model_choices(ctx).await?;
    reply.insert(
        "speed_state".into(),
        Json::Object(crate::session_reasoning::speed_state(
            &fields,
            &choices,
            saved_speed.as_str(),
            once_pair(&pending_model),
        )?),
    );
    let mut current = JsonMap::new();
    current.insert("provider_id".into(), fields.provider_id.clone().into());
    current.insert("model_id".into(), fields.model_id.clone().into());
    let mut model_state = JsonMap::new();
    model_state.insert("current".into(), Json::Object(current));
    model_state.insert("once".into(), pending_value);
    model_state.insert("default_model".into(), settings.get("default_model").cloned().unwrap_or(Json::Null));
    model_state.insert("chat_defaults".into(), settings.get("chat_defaults").cloned().unwrap_or(Json::Null));
    reply.insert("model_state".into(), Json::Object(model_state));
    Ok(Json::Object(reply))
}

/// `_json_value(NarrationInterruption)`: the dataclass fields in declaration order.
fn interruption_value(interruption: &sylvia_domain::authority::NarrationInterruption) -> Json {
    let text = |value: &Option<String>| value.clone().map_or(Json::Null, Json::String);
    let mut value = JsonMap::new();
    value.insert("campaign_id".into(), interruption.campaign_id.clone().into());
    value.insert("interruption_id".into(), interruption.interruption_id.clone().into());
    value.insert("turn_id".into(), interruption.turn_id.clone().into());
    value.insert("last_action_id".into(), text(&interruption.last_action_id));
    value.insert("failed_attempt_id".into(), interruption.failed_attempt_id.clone().into());
    value.insert("retryable".into(), interruption.retryable.into());
    value.insert("resolving_retry_id".into(), text(&interruption.resolving_retry_id));
    value.insert("resolved_sequence".into(), interruption.resolved_sequence.map_or(Json::Null, Json::from));
    Json::Object(value)
}

/// `_optional_nonnegative_int(params, key)`: absent or `null` is `None`.
fn optional_nonnegative_int(params: &JsonMap, key: &str) -> CodedResult<Option<i64>> {
    match params.get(key) {
        None | Some(Json::Null) => Ok(None),
        Some(_) => super::campaigns::required_nonnegative_int(params, key).map(Some),
    }
}

/// A supervisor branch that needs a tracked narration lease. Only a running turn tracks one
/// (the loop's `on_narration_started` hook), so none exists before lane 4's T5 runs turns; the
/// lease lifecycle (tracking, the attachment and grace timers) comes with that wiring.
fn lease_pending(method: &str) -> CodedError {
    CodedError::new(
        crate::dispatch::PORT_PENDING,
        format!(
            "not yet ported: interfaces/local_owner.py LocalOwnerSupervisor.{method} with a tracked lease"
        ),
    )
}

/// `handle_interrupt_stream`: `ctx.bus.interrupt_stream(campaign_id, attempt_id)`, which is the
/// supervisor's `interrupt_narration` (the owner interrupts the attempt, then the lease is
/// released) and drops the attempt's stream sinks.
pub async fn handle_interrupt_stream(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let campaign_id = super::campaigns::required(params, "campaign_id")?;
    let attempt_id = super::campaigns::required(params, "attempt_id")?;
    let interruption =
        sylvia_app::authority::narration_attempts::interrupt_narration(&ctx.store, &campaign_id, &attempt_id)
            .await
            .map_err(sylvia_app::authority::actions::ActionError::into_coded)?;
    // No lease or sink is ever tracked before the loop runs turns, so there is nothing to release.
    Ok(interruption_value(&interruption))
}

/// `handle_disconnect_stream`: the supervisor's `signal_disconnect(..., expected_generation=...)`.
/// The generation is required, so without a tracked lease it is stale.
pub async fn handle_disconnect_stream(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let _campaign_id = super::campaigns::required(params, "campaign_id")?;
    let _attempt_id = super::campaigns::required(params, "attempt_id")?;
    let _generation = super::campaigns::required_nonnegative_int(params, "attachment_generation")?;
    let _ = ctx;
    if !narration_leases_tracked() {
        return Err(CodedError::rejected("Narration attachment generation is stale"));
    }
    Err(lease_pending("signal_disconnect"))
}

/// `handle_stream_heartbeat`: the supervisor's `heartbeat_narration`, which renews a tracked
/// renderer attachment lease.
pub async fn handle_stream_heartbeat(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let _campaign_id = super::campaigns::required(params, "campaign_id")?;
    let _attempt_id = super::campaigns::required(params, "attempt_id")?;
    let _generation = optional_nonnegative_int(params, "attachment_generation")?;
    let _ = ctx;
    if !narration_leases_tracked() {
        return Err(CodedError::rejected("Narration attachment is unavailable"));
    }
    Err(lease_pending("heartbeat_narration"))
}

/// `handle_narration_attempt_status`: `{"status": owner.narration_attempt_status(...)}`.
pub async fn handle_narration_attempt_status(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let campaign_id = super::campaigns::required(params, "campaign_id")?;
    let attempt_id = super::campaigns::required(params, "attempt_id")?;
    let status = sylvia_app::authority::narration_attempts::narration_attempt_status(
        &ctx.store,
        &campaign_id,
        &attempt_id,
    )
    .await
    .map_err(sylvia_app::authority::actions::ActionError::into_coded)?;
    let mut reply = JsonMap::new();
    reply.insert("status".into(), status.into());
    Ok(Json::Object(reply))
}

/// `handle_reconnect_stream`: the supervisor's `reconnect_narration`, which needs the
/// attempt's tracked lease before it asks the owner to replay.
pub async fn handle_reconnect_stream(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let _campaign_id = super::campaigns::required(params, "campaign_id")?;
    let _attempt_id = super::campaigns::required(params, "attempt_id")?;
    let _acknowledged = super::campaigns::required_nonnegative_int(params, "acknowledged_sequence")?;
    let _generation = super::campaigns::required_nonnegative_int(params, "attachment_generation")?;
    let _ = ctx;
    if !narration_leases_tracked() {
        return Err(CodedError::rejected("Narration attachment lease is unavailable"));
    }
    Err(lease_pending("reconnect_narration"))
}

/// Whether the supervisor tracks any narration lease. It never does before lane 4's T5 runs
/// turns (the loop's lifecycle hooks are empty), so every lookup finds none.
fn narration_leases_tracked() -> bool {
    false
}
