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
    Ok(crate::owner_json::interruption_value(&interruption))
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

/// `_MAX_CAMPAIGN_PLAN_BYTES`: the admitted Plan's usability limit.
const MAX_CAMPAIGN_PLAN_BYTES: usize = 1024 * 1024;

/// A config value given to a settings function, from a request parameter (`params.get(key)`).
fn data_of(value: Option<&Json>) -> CodedResult<Data> {
    match value {
        None => Ok(Data::None),
        Some(value) => sylvia_provider::config::data_from_json(value).map_err(super::providers::config_error),
    }
}

/// `_selection_from_reference(choices, reference)`: an exact or uniquely named configured model,
/// never a guess.
fn selection_from_reference(choices: &[Json], reference: &str) -> CodedResult<(String, String)> {
    let folded = sylvia_base::pystr::casefold(reference);
    let mut unique: Vec<(String, String)> = Vec::new();
    for choice in choices {
        let (Some(Json::String(provider_id)), Some(Json::String(model_id))) =
            (choice.get("provider_id"), choice.get("model_id"))
        else {
            continue;
        };
        let prefix = format!("{}/", provider_id.replace('_', "-"));
        let upstream = model_id.strip_prefix(&prefix).unwrap_or(model_id);
        let mut candidates =
            vec![sylvia_base::pystr::casefold(model_id), sylvia_base::pystr::casefold(upstream)];
        if let Some(Json::String(name)) = choice.get("model_name") {
            candidates.push(sylvia_base::pystr::casefold(name));
        }
        let pair = (provider_id.clone(), model_id.clone());
        if candidates.contains(&folded) && !unique.contains(&pair) {
            unique.push(pair);
        }
    }
    if unique.len() != 1 {
        return Err(CodedError::rejected(
            "Model reference is unavailable or ambiguous; use the configured picker",
        ));
    }
    Ok(unique.remove(0))
}

/// `_release_previous_model_threads(prior, updated, choices)`: a chat that leaves a subscription
/// model leaves nothing for its kept thread (ChatGPT) or process (Claude Code).
fn release_previous_model_threads(
    prior: &SessionRecordFields,
    updated: &SessionRecordFields,
    choices: &[Json],
) -> CodedResult<()> {
    if (prior.provider_id.as_str(), prior.model_id.as_str())
        == (updated.provider_id.as_str(), updated.model_id.as_str())
    {
        return Ok(());
    }
    let Some(previous) = crate::session_reasoning::selected_model_info(prior, choices)? else {
        return Ok(());
    };
    let wire = vec![previous.wire_model_id().to_owned()];
    match previous.transport.as_str() {
        "codex_app_server" => {
            sylvia_provider::chatgpt::release_kept_threads(&wire);
        }
        "claude_code" => {
            sylvia_provider::claude_kept::release_kept_processes(None, Some(&wire));
        }
        _ => {}
    }
    Ok(())
}

/// `AuthorityOwner.update_agent_session_model`: the session's campaign work and lane, then the
/// store's update.
async fn update_session_model(
    ctx: &DispatchContext,
    session_id: &str,
    provider_id: &str,
    model_id: &str,
) -> CodedResult<sylvia_domain::session::SessionRecord> {
    let _work = ctx.store.session_campaign_work(session_id).await.map_err(super::campaigns::store_error)?;
    let _lane = ctx.store.lanes().sessions.acquire(session_id).await;
    let (session, provider, model) = (session_id.to_owned(), provider_id.to_owned(), model_id.to_owned());
    ctx.store
        .call(move |store| store.update_agent_session_model(&session, &provider, &model))
        .await
        .map_err(super::campaigns::store_error)
}

/// `_switch_session_model(...)`: one idle-session model switch for the chat (`current`), Settings
/// too (`global`), or the next reply only (`once`).
async fn switch_session_model(
    ctx: &DispatchContext,
    session_id: &str,
    provider_id: &Data,
    model_id: &Data,
    mode: &str,
    choices: &[Json],
) -> CodedResult<Json> {
    campaign_loop(ctx)?.assert_idle(session_id)?;
    let path = super::providers::config_path(ctx);
    let selection = config_json(&super::providers::with_sources(|sources| {
        sylvia_provider::config::provider_settings::require_configured_model(
            sources,
            &path,
            provider_id,
            model_id,
        )
    })?)?;
    let text = |key: &str| selection.get(key).and_then(Json::as_str).unwrap_or_default().to_owned();
    let (chosen_provider, chosen_model) = (text("provider_id"), text("model_id"));
    let prior = session_fields(ctx, session_id).await?;
    let mut destination = prior.clone();
    destination.provider_id = chosen_provider.clone();
    destination.model_id = chosen_model.clone();
    crate::session_reasoning::validate_model_preferences(&destination, choices, mode == "once")?;
    let session_key = Data::Str(session_id.to_owned());
    let (provider_data, model_data) = (Data::Str(chosen_provider.clone()), Data::Str(chosen_model.clone()));
    if mode == "once" {
        super::providers::with_sources(|sources| {
            sylvia_provider::config::provider_writes::set_session_model_once_settings(
                sources,
                &path,
                &session_key,
                &provider_data,
                &model_data,
            )
        })?;
        let context = ctx.store.session_context(session_id).await.map_err(super::campaigns::store_error)?;
        let mut reply = JsonMap::new();
        reply.insert("status".into(), "staged_once".into());
        reply.insert("session".into(), super::builder::session_record_value(&context.session));
        reply.insert("once".into(), selection.clone());
        return Ok(Json::Object(reply));
    }
    if mode != "current" && mode != "global" {
        return Err(protocol("Model switch mode is unsupported"));
    }
    let updated = update_session_model(ctx, session_id, &chosen_provider, &chosen_model).await?;
    let updated_fields = updated.fields().clone();
    release_previous_model_threads(&prior, &updated_fields, choices)?;
    if mode == "global" {
        let saved = super::providers::with_sources(|sources| {
            sylvia_provider::config::provider_writes::set_default_model_settings(
                sources,
                &path,
                &provider_data,
                &model_data,
            )
        });
        if let Err(error) = saved {
            update_session_model(ctx, session_id, &prior.provider_id, &prior.model_id).await?;
            return Err(error);
        }
    }
    let pending = config_json(&super::providers::with_sources(|sources| {
        sylvia_provider::config::provider_settings::session_model_once_settings(sources, &path, &session_key)
    })?)?;
    if let Json::Object(pending) = &pending {
        let expected = |key: &str| data_of(pending.get(key));
        let (expected_provider, expected_model) = (expected("provider_id")?, expected("model_id")?);
        super::providers::with_sources(|sources| {
            sylvia_provider::config::provider_writes::clear_session_model_once_settings(
                sources,
                &path,
                &session_key,
                &expected_provider,
                &expected_model,
            )
        })?;
    }
    // Speed tiers belong to a model: one the new model does not offer is cleared, visibly.
    let saved_speed = config_json(&super::providers::with_sources(|sources| {
        sylvia_provider::config::provider_settings::session_speed_settings(sources, &path, &session_key)
    })?)?;
    let mut speed =
        crate::session_reasoning::speed_state(&updated_fields, choices, saved_speed.as_str(), None)?;
    let speed_cleared = !saved_speed.is_null() && speed.get("valid") != Some(&Json::Bool(true));
    if speed_cleared {
        super::providers::with_sources(|sources| {
            sylvia_provider::config::provider_writes::set_session_speed_settings(
                sources,
                &path,
                &session_key,
                &Data::None,
            )
        })?;
        speed = crate::session_reasoning::speed_state(&updated_fields, choices, None, None)?;
    }
    let settings = config_json(&super::providers::with_sources(|sources| {
        sylvia_provider::config::provider_settings::provider_settings(sources, &path)
    })?)?;
    let mut reply = JsonMap::new();
    reply.insert("status".into(), if mode == "global" { "switched_global" } else { "switched" }.into());
    reply.insert("session".into(), super::builder::session_record_value(&updated));
    reply.insert("once".into(), Json::Null);
    reply.insert("default_model".into(), settings.get("default_model").cloned().unwrap_or(Json::Null));
    reply.insert("speed_state".into(), Json::Object(speed));
    reply.insert("speed_cleared".into(), speed_cleared.into());
    Ok(Json::Object(reply))
}

/// `handle_switch_session_model` (`providers.py`): `_switch_session_model(...)` with the
/// request's model.
pub async fn handle_switch_session_model(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let session_id = super::campaigns::required(params, "session_id")?;
    let provider_id = data_of(params.get("provider_id"))?;
    let model_id = data_of(params.get("model_id"))?;
    let mode = super::campaigns::optional_text(params, "mode")?.unwrap_or_else(|| "current".to_owned());
    let choices = super::providers::model_choices(ctx).await?;
    switch_session_model(ctx, &session_id, &provider_id, &model_id, &mode, &choices).await
}

/// `_admitted_dm_campaign_plan(owner, campaign_id=..., session_id=...)`: the DM session's
/// admitted Plan text and structured Plan, from the latest adventure descended from its handoff.
async fn admitted_dm_campaign_plan(
    ctx: &DispatchContext,
    campaign_id: &str,
    session_id: &str,
) -> CodedResult<(String, Option<JsonMap>)> {
    let suffix = &sylvia_base::hash::sha256_hex(campaign_id.as_bytes())[..24];
    let (plan_id, handoff_id) = (format!("campaign-plan-{suffix}"), format!("builder-dm-handoff-{suffix}"));
    let admitted = || CodedError::rejected("DM session requires an admitted Builder campaign plan");
    let plan = ctx
        .store
        .call(move |store| store.campaign_plan_hit(&plan_id))
        .await
        .map_err(super::campaigns::store_error)?;
    let Some(plan) = plan else { return Err(admitted()) };
    let handoff = ctx
        .store
        .call(move |store| store.handoff_hit(&handoff_id))
        .await
        .map_err(super::campaigns::store_error)?;
    let Some(handoff) = handoff else { return Err(admitted()) };
    let (plan_fields, handoff_fields) = (plan.fields(), handoff.fields());
    if plan_fields.campaign_id != campaign_id
        || handoff_fields.campaign_id != campaign_id
        || handoff_fields.plan_id != plan_fields.plan_id
        || handoff_fields.dm_session_id != session_id
    {
        return Err(CodedError::rejected("DM session does not match the admitted campaign plan"));
    }
    let id = campaign_id.to_owned();
    let adventures = ctx
        .store
        .call(move |store| store.campaign_adventures(&id))
        .await
        .map_err(super::campaigns::store_error)?;
    if adventures.first().is_none_or(|first| first.fields().plan_id != plan_fields.plan_id) {
        return Err(CodedError::rejected("Current adventure is not descended from the admitted handoff"));
    }
    let current = adventures.last().expect("nonempty");
    let content = current.content();
    let text = match content.get("campaign_plan_markdown") {
        Some(Json::String(text)) if !sylvia_base::pystr::strip(text).is_empty() => text.clone(),
        _ => return Err(CodedError::rejected("Admitted campaign plan content is unavailable")),
    };
    if text.len() > MAX_CAMPAIGN_PLAN_BYTES {
        return Err(CodedError::rejected("Admitted campaign plan exceeds the usability limit"));
    }
    let domain = |error: sylvia_domain::error::DomainError| {
        sylvia_app::authority::actions::ActionError::Domain(error).into_coded()
    };
    let mut structured = match content.get("campaign_plan") {
        Some(Json::Object(plan)) => Some(plan.clone()),
        _ => None,
    };
    let continuation = sylvia_domain::campaign_sequel::sequel_preparation(content).map_err(domain)?;
    if let (Some(continuation), Some(plan)) = (continuation, structured.as_mut()) {
        // This application projection is not a change to the authored or accepted Plan bytes.
        plan.insert("_app_adventure_continuation".into(), Json::Object(continuation));
    }
    let markdown = sylvia_domain::campaign_sequel::sequel_markdown(content).map_err(domain)?;
    Ok((markdown, structured))
}

/// The submit shell's ending choice: absent, or exactly `{"ending_id": text, "choice":
/// "continue"|"finish"}`.
fn ending_choice(params: &JsonMap) -> CodedResult<Option<JsonMap>> {
    let Some(value) = params.get("campaign_ending_choice").filter(|value| !value.is_null()) else {
        return Ok(None);
    };
    let valid = match value {
        Json::Object(choice) => {
            let keys: std::collections::BTreeSet<&str> = choice.keys().map(String::as_str).collect();
            keys == ["choice", "ending_id"].into_iter().collect()
                && matches!(choice.get("ending_id"), Some(Json::String(_)))
                && matches!(choice.get("choice"), Some(Json::String(text)) if text == "continue" || text == "finish")
        }
        _ => false,
    };
    if !valid {
        return Err(protocol("Campaign ending choice is invalid"));
    }
    Ok(value.as_object().cloned())
}

/// `handle_submit_session_input`: one player input to a DM session or a Builder chat. Every check
/// before the loop runs is Python's, in Python's order; the run itself (lane 4's
/// `CampaignSessionLoop.run`, `run_compaction`, and lane 1's Builder branch) answers
/// `rust_port_pending` until those land. A provider failure keeps Python's provider-error reply.
pub async fn handle_submit_session_input(
    ctx: &DispatchContext,
    params: &JsonMap,
) -> crate::dispatch::DispatchResult {
    use crate::dispatch::Failure;
    use sylvia_domain::authority::WriterRole;
    let ending = ending_choice(params)?;
    let builder_start = super::campaigns::optional_bool(params, "builder_start")?;
    let player_action_id = super::campaigns::optional_text(params, "player_action_id")?;
    let automatic_response = super::campaigns::optional_bool(params, "automatic_response")?;
    if automatic_response && player_action_id.is_none() {
        return Err(protocol("Automatic response requires its saved player action").into());
    }
    if builder_start && player_action_id.is_some() {
        return Err(protocol("Builder start cannot respond to a gameplay action").into());
    }
    let user_text = match params.get("user_text") {
        None => String::new(),
        Some(Json::String(text)) => text.clone(),
        Some(_) => return Err(protocol("user_text must be text").into()),
    };
    let ooc_text = match params.get("ooc_text") {
        None | Some(Json::Null) => None,
        Some(Json::String(text)) => {
            Some(text.clone()).filter(|text| !sylvia_base::pystr::strip(text).is_empty())
        }
        Some(_) => return Err(protocol("ooc_text must be text").into()),
    };
    let blank = sylvia_base::pystr::strip(&user_text).is_empty();
    if blank && ooc_text.is_none() && !builder_start && player_action_id.is_none() && ending.is_none() {
        return Err(protocol("user_text or ooc_text must be nonempty").into());
    }
    if player_action_id.is_some() && (!user_text.is_empty() || ooc_text.is_some()) {
        return Err(protocol("Action responses do not contain player speech").into());
    }
    if builder_start && (!user_text.is_empty() || ooc_text.is_some()) {
        return Err(protocol("Builder start does not accept invented player input").into());
    }
    if sylvia_base::pystr::len(&user_text) > MAX_USER_TEXT_CHARS {
        return Err(protocol("user_text exceeds the session input limit").into());
    }
    if ooc_text.as_deref().is_some_and(|text| sylvia_base::pystr::len(text) > MAX_USER_TEXT_CHARS) {
        return Err(protocol("ooc_text exceeds the session input limit").into());
    }
    let session_id = super::campaigns::required(params, "session_id")?;
    let question_answers = params.get("builder_question_answers").filter(|value| !value.is_null());
    let context = ctx.store.session_context(&session_id).await.map_err(super::campaigns::store_error)?;
    let redirect_given = params.get("redirect_interruption_id").is_some_and(sylvia_provider::pyobj::truthy);
    if let Some(answers) = question_answers {
        if builder_start
            || player_action_id.is_some()
            || ending.is_some()
            || ooc_text.is_some()
            || redirect_given
        {
            return Err(protocol("Question answers cannot be combined with other input").into());
        }
        sylvia_agent::builder::question_answers::builder_question_answer_content(
            &context, answers, &user_text,
        )?;
    }
    let model_command = if ooc_text.is_none() { crate::run::model_slash_command(&user_text)? } else { None };
    if let Some((reference, mode)) = model_command {
        let Some(reference) = reference else {
            campaign_loop(ctx)?.assert_idle(&session_id)?;
            let mut reply = JsonMap::new();
            reply.insert("status".into(), "model_picker_requested".into());
            reply.insert("mode".into(), mode.into());
            return Ok(Json::Object(reply));
        };
        let (provider_id, model_id) =
            selection_from_reference(&super::providers::model_choices(ctx).await?, &reference)?;
        let choices = super::providers::model_choices(ctx).await?;
        return Ok(switch_session_model(
            ctx,
            &session_id,
            &Data::Str(provider_id),
            &Data::Str(model_id),
            mode,
            &choices,
        )
        .await?);
    }
    let role = context.session.role();
    if ending.is_some()
        && (role != WriterRole::Dm
            || builder_start
            || player_action_id.is_some()
            || !user_text.is_empty()
            || ooc_text.is_some()
            || redirect_given)
    {
        return Err(protocol("Ending choices require an idle GM ending without other input").into());
    }
    if builder_start && role != WriterRole::Builder {
        return Err(protocol("builder_start is available only to Builder sessions").into());
    }
    if role == WriterRole::Builder && ooc_text.is_some() {
        return Err(protocol("ooc_text is available only to DM sessions").into());
    }
    let turn_id = match context.session.campaign_id() {
        Some(_) => super::campaigns::required(params, "turn_id")?,
        None => super::campaigns::optional_text(params, "turn_id")?
            .unwrap_or_else(|| format!("builder-chat-{session_id}")),
    };
    if player_action_id.is_some() {
        return Err(CodedError::new(
            crate::dispatch::PORT_PENDING,
            "not yet ported: application/authority.py AuthorityOwner.player_action_response_basis",
        )
        .into());
    }
    let path = super::providers::config_path(ctx);
    let session_key = Data::Str(session_id.clone());
    let pending_once = pending_once_model(ctx, &session_id)?;
    let consume_pending_once = || -> CodedResult<()> {
        let Some((provider_id, model_id)) = &pending_once else { return Ok(()) };
        let (provider_id, model_id) = (Data::Str(provider_id.clone()), Data::Str(model_id.clone()));
        super::providers::with_sources(|sources| {
            sylvia_provider::config::provider_writes::clear_session_model_once_settings(
                sources,
                &path,
                &session_key,
                &provider_id,
                &model_id,
            )
        })?;
        Ok(())
    };
    let mut effective_fields = context.session.fields().clone();
    if let Some((provider_id, model_id)) = &pending_once {
        effective_fields.provider_id = provider_id.clone();
        effective_fields.model_id = model_id.clone();
    }
    let effective_session = sylvia_domain::session::SessionRecord::new(effective_fields.clone())
        .map_err(|error| sylvia_app::authority::actions::ActionError::Domain(error).into_coded())?;
    let resolver = super::providers::providers(ctx)?;
    let mut provider = match resolver.resolve(&effective_session).await {
        Ok(provider) => provider,
        Err(error) => {
            consume_pending_once()?;
            return Err(error.into());
        }
    };
    let reads = crate::owner_reads::OwnerReads::for_owner(ctx.store.clone(), ctx.owner_id.clone());
    if ctx.store.narration().admin_full_audit_enabled() {
        let sink_reads = reads.clone();
        provider = Arc::new(sylvia_provider::admin_audit::AdminAuditedProvider::new(
            provider,
            Arc::new(move |payload| {
                let _ = sink_reads.record_admin_full_audit(payload);
            }),
            session_id.clone(),
            effective_fields.role.as_str(),
            effective_fields.provider_id.clone(),
            effective_fields.model_id.clone(),
            if pending_once.is_some() { "once" } else { "session" },
        ));
    }
    let saved_speed = config_json(&super::providers::with_sources(|sources| {
        sylvia_provider::config::provider_settings::session_speed_settings(sources, &path, &session_key)
    })?)?;
    let bound = sylvia_provider::reasoning::bind_session_reasoning(
        provider,
        effective_fields.role.as_str(),
        effective_fields.reasoning_effort.as_deref(),
        effective_fields.reasoning_effort_once.as_deref(),
        saved_speed.as_str(),
    )?;
    let provider: Arc<dyn sylvia_provider::provider::Provider> =
        Arc::new(sylvia_provider::context_fit::context_fitted_provider(
            Arc::new(bound),
            effective_fields.role == WriterRole::Dm,
        ));
    if sylvia_base::pystr::strip(&user_text) == "/compact" && ooc_text.is_none() {
        if role == WriterRole::Builder {
            if let Some(campaign_id) = context.session.campaign_id() {
                let basis =
                    sylvia_app::authority::guided::campaign_construction_basis(&ctx.store, campaign_id)
                        .await
                        .map_err(super::campaigns::store_error)?;
                if basis.builder_session_id == session_id && basis.completion_revision.is_none() {
                    return Err(protocol(
                        "The campaign Interview keeps its exact transcript. Compaction is unavailable.",
                    )
                    .into());
                }
            }
        }
        // `run_compaction(session_id, attempt_id, compact_provider)`, then `consume_pending_once`.
        let outcome = match super::campaigns::required(params, "attempt_id") {
            Err(error) => Err(Failure::from(error)),
            Ok(_attempt_id) => Err(CodedError::new(
                crate::dispatch::PORT_PENDING,
                "not yet ported: agent/_working_context.py compact_saved_messages (the /compact provider)",
            )
            .into()),
        };
        consume_pending_once()?;
        return outcome;
    }
    if role == WriterRole::Builder {
        let _ = provider;
        return Err(CodedError::new(
            crate::dispatch::PORT_PENDING,
            "not yet ported: the Builder branch (sylvia_agent::builder::submit::submit_builder_input, lane 1's B3b)",
        )
        .into());
    }
    if super::campaigns::optional_bool(params, "allow_public_web")? {
        return Err(protocol("allow_public_web is available only to Builder sessions").into());
    }
    let Some(campaign_id) = context.session.campaign_id() else {
        return Err(protocol("DM sessions require campaign authority").into());
    };
    let (plan_text, plan) = admitted_dm_campaign_plan(ctx, campaign_id, &session_id).await?;
    let adapter = Arc::new(
        sylvia_agent::provider_session::context::ProviderSessionAdapter::new(
            Arc::new(reads) as Arc<dyn sylvia_agent::provider_session::reads::OpeningReads>,
            provider,
            &ctx.owner_id,
            ctx.gm_system_prompt.as_deref().map(str::to_owned),
            // `ProviderSessionAdapter(..., timeout=120.0)`: Python's default.
            120.0,
            Some(plan_text),
            plan,
            None,
            &sylvia_provider::config::env::Environment::current(),
        )?
        .with_record_warning(Arc::clone(&ctx.record_provider_warning)),
    );
    let outcome = async {
        let attempt_id = super::campaigns::required(params, "attempt_id")?;
        let message_id = super::campaigns::required(params, "message_id")?;
        let mut request = sylvia_agent::session_loop::SessionRunRequest::new(
            session_id.clone(),
            turn_id.clone(),
            attempt_id,
            message_id,
            user_text.clone(),
        );
        request.ooc_text = ooc_text.clone();
        request.campaign_ending_choice = ending.clone();
        request.input_kind = sylvia_agent::session_loop::SessionInputKind::User;
        request.automatic_response = automatic_response;
        request.redirect_interruption_id =
            super::campaigns::optional_text(params, "redirect_interruption_id")?;
        let result = campaign_loop(ctx)?.run(request, adapter.for_turn(&turn_id)).await;
        session_run_reply(result)
    }
    .await;
    consume_pending_once()?;
    outcome
}

/// A loop run's result as the owner replies: the run's payload, or its failure in Python's
/// class (`AgentLoopError` is an uncaught `RuntimeError`, the fixed internal-error reply).
fn session_run_reply(
    result: Result<sylvia_agent::session_loop::SessionRunResult, sylvia_agent::session_loop::SessionFailure>,
) -> crate::dispatch::DispatchResult {
    use crate::dispatch::Failure;
    use sylvia_agent::provider_turn::TurnFailure;
    use sylvia_agent::session_loop::SessionFailure;
    match result {
        Ok(result) => Ok(crate::owner_json::session_run_value(&result)?),
        Err(SessionFailure::Turn(TurnFailure::Coded(error))) => Err(Failure::from(error)),
        Err(SessionFailure::Turn(TurnFailure::Provider(error))) => Err(Failure::Provider(error)),
        Err(SessionFailure::AgentLoop { .. }) => Err(Failure::Internal { error_type: "AgentLoopError" }),
    }
}
