//! The player's projection of a campaign, from `application/authority.py`:
//! `AuthorityOwner.player_projection` and `player_projection_page`, with
//! `_project_player_snapshot`, `_card_control_readiness` and `_interruption_saved_changes`.
//!
//! The projection is the campaign's player-safe chronology (safe thinking, TALK and tool cards
//! with their buttons' current availability), then a pending mechanics choice, the live
//! provisional prose and pending tools, and a Retry for each interruption no live Retry answers.
//! A page reads only the events after a sequence, from the display snapshot.

use super::actions::ActionError;
use super::content_reads::CatalogTool;
use super::direct_actions::{check_player_action, player_control_scope, Failure};
use super::narration_attempts::can_redirect_interruption;
use std::collections::{BTreeMap, BTreeSet};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    owned_payload, ActionRecord, ActionStatus, CampaignSnapshot, CampaignState, CanonicalEvent,
    CanonicalEventType, NarrationInterruption, PlayerProjection, PlayerProjectionKind, WriterRole,
};
use sylvia_domain::pyops::py_str;
use sylvia_domain::session::SessionMessageKind;
use sylvia_domain::DomainError;
use sylvia_store::{AuthorityStore, StoreError, StoreHandle};

/// The owner's in-memory state a projection reads (`_campaign_work_counts`, `_attempts` and
/// `_provisional_projection`), taken before the store read.
struct Live {
    controls_idle: bool,
    provisional: Vec<PlayerProjection>,
    active_retry_ids: BTreeSet<String>,
}

fn live(store: &StoreHandle, campaign_id: &str) -> Live {
    let narration = store.narration();
    Live {
        controls_idle: store.lanes().work.count(campaign_id) == 0,
        provisional: narration.provisional_projection(campaign_id),
        active_retry_ids: narration.with(|inner| {
            inner
                .attempts
                .iter()
                .filter(|(_, attempt)| attempt.campaign_id == campaign_id && !attempt.terminal)
                .map(|(_, attempt)| attempt.attempt_id.clone())
                .collect()
        }),
    }
}

/// `AuthorityOwner.player_projection(campaign_id)`: the whole campaign's projection.
pub async fn player_projection(
    store: &StoreHandle,
    campaign_id: &str,
) -> Result<Vec<PlayerProjection>, ActionError> {
    let _operation = store.operation()?;
    let live = live(store, campaign_id);
    let (id, work) = (campaign_id.to_owned(), store.lanes().work.clone());
    store
        .call(move |store| {
            let snapshot = super::player_reads::project_private_tool_inputs(store, store.snapshot(&id)?)?;
            let (state, actions, events) =
                (snapshot.state.clone(), snapshot.actions.clone(), snapshot.events.clone());
            Ok(project(store, &work, &state, &actions, &events, Some(snapshot), live))
        })
        .await?
}

/// `AuthorityOwner.player_projection_page(campaign_id, after_sequence=...)`: the projection of
/// the events after `after_sequence` (all of them when absent), with the live controls.
pub async fn player_projection_page(
    store: &StoreHandle,
    campaign_id: &str,
    after_sequence: Option<&Json>,
) -> Result<Vec<PlayerProjection>, ActionError> {
    // `type(after_sequence) is not int or after_sequence < 0`: a bool is not an int here.
    let after = match after_sequence {
        None | Some(Json::Null) => None,
        Some(Json::Number(number)) if number.as_i64().is_some_and(|value| value >= 0) => number.as_i64(),
        Some(Json::Number(number)) if number.is_u64() => Some(i64::MAX),
        Some(_) => return Err(DomainError::command_rejected("Player history sequence is invalid").into()),
    };
    let _operation = store.operation()?;
    let live = live(store, campaign_id);
    let (id, work) = (campaign_id.to_owned(), store.lanes().work.clone());
    store
        .call(move |store| {
            let query = sylvia_store::snapshots::DisplayQuery { event_after: after, ..Default::default() };
            let mut display = store.display_snapshot(&id, &query)?;
            super::player_reads::project_private_records(
                store,
                &display.state.package_lock.clone(),
                &mut display.actions,
                &mut display.events,
            )?;
            Ok(project(store, &work, &display.state, &display.actions, &display.events, None, live))
        })
        .await?
}

/// A failure the readiness check reports as the button's reason: Python's `except
/// CommandRejected` (and its subclasses).
fn rejected_reason(failure: &Failure) -> Option<String> {
    match failure {
        Failure::Domain(DomainError::Rejected { error_type, message, .. })
            if matches!(*error_type, "CommandRejected" | "MechanicsRejected") =>
        {
            Some(message.clone())
        }
        Failure::Store(StoreError::Coded(coded))
            if coded.code != super::actions::PORT_PENDING
                && matches!(
                    coded.error_type.unwrap_or("CommandRejected"),
                    "CommandRejected" | "MechanicsRejected"
                ) =>
        {
            Some(coded.message.clone())
        }
        _ => None,
    }
}

fn unavailable(reason: &str) -> (bool, Json) {
    (false, Json::from(reason))
}

/// `_card_control_readiness(snapshot, original, tools, control_scope)`: each saved card button's
/// current availability and reason, checked as a click would be.
fn card_control_readiness(
    store: &AuthorityStore,
    state: &CampaignState,
    original: Option<&ActionRecord>,
    tools: &BTreeMap<(String, String), CatalogTool>,
    control_scope: Option<&JsonMap>,
) -> Result<Vec<(bool, Json)>, ActionError> {
    let _ = store;
    let Some(original) = original else { return Ok(Vec::new()) };
    let Some(Json::Object(card)) = original.result.get("result_card") else { return Ok(Vec::new()) };
    let version_one = matches!(card.get("version"), Some(Json::Number(number)) if number.as_i64() == Some(1));
    let Some(Json::Array(actions)) = card.get("actions").filter(|_| version_one) else {
        return Ok(Vec::new());
    };
    if actions.len() > 8 {
        return Ok(Vec::new());
    }
    let participant_ids: Vec<&str> = state.participants.iter().map(|item| item.participant_id()).collect();
    let mut available = Vec::with_capacity(actions.len());
    for action in actions {
        let Some(action) = action.as_object() else {
            available.push(unavailable("This action is unavailable"));
            continue;
        };
        let Some(Json::String(tool_name)) = action.get("tool") else {
            available.push(unavailable("This action is unavailable"));
            continue;
        };
        let tool = tools.get(&(original.package_id.clone(), tool_name.clone()));
        let (Some(tool), Some(Json::Object(parameters))) = (tool, action.get("parameters")) else {
            available.push(unavailable("This action is unavailable"));
            continue;
        };
        if !tool.player_controls.available {
            available.push(unavailable("This action is unavailable"));
            continue;
        }
        let checked = (|| -> Result<(), Failure> {
            sylvia_domain::schema_admission::validate_parameters(parameters, &tool.input_schema)?;
            sylvia_domain::schema_admission::validate_references(
                parameters,
                &tool.input_schema,
                &participant_ids,
            )?;
            crate::player_actions::validate_player_control(
                control_scope,
                parameters,
                tool.player_controls.actor_parameter.as_deref(),
            )?;
            check_player_action(tool, parameters, state, true)
        })();
        match checked {
            Ok(()) => available.push((true, Json::Null)),
            Err(failure) => match rejected_reason(&failure) {
                Some(reason) => available.push((false, reason.into())),
                None => return Err(failure.into()),
            },
        }
    }
    Ok(available)
}

/// `_interruption_saved_changes(snapshot, interruption)`: whether the interrupted input already
/// committed an action of its own (not a failed one, not an earlier input's).
fn interruption_saved_changes(
    store: &AuthorityStore,
    snapshot: &CampaignSnapshot,
    interruption: &NarrationInterruption,
) -> Result<bool, ActionError> {
    let mut action_ids: BTreeSet<String> = interruption.last_action_id.iter().cloned().collect();
    let failed = Json::from(interruption.failed_attempt_id.as_str());
    let kind_of = |content: &JsonMap| content.get("kind").and_then(Json::as_str).map(str::to_owned);
    let int_of = |value: Option<&Json>| match value {
        Some(Json::Number(number)) => number.as_i64(),
        _ => None,
    };
    for context in store.campaign_session_contexts(&snapshot.state.campaign_id, &[])? {
        let messages = &context.messages;
        let terminal = messages.iter().find(|message| {
            kind_of(message.content()).as_deref() == Some("session_attempt_terminal")
                && message.content().get("attempt_id") == Some(&failed)
        });
        let basis = messages.iter().find(|message| {
            let input = message.kind() == SessionMessageKind::User
                || matches!(
                    kind_of(message.content()).as_deref(),
                    Some("campaign_start" | "player_action_response")
                );
            input
                && (message.content().get("attempt_id") == Some(&failed)
                    || terminal.is_some_and(|terminal| {
                        terminal.content().get("message_id") == Some(&Json::from(message.message_id()))
                    }))
        });
        let Some(basis) = basis else { continue };
        if snapshot
            .continuity
            .records
            .iter()
            .any(|record| record.basis_message_ids.iter().any(|id| id == basis.message_id()))
        {
            return Ok(true);
        }
        let next_input = |message: &&sylvia_domain::session::SessionMessage| {
            message.sequence() > basis.sequence()
                && (message.kind() == SessionMessageKind::User
                    || kind_of(message.content()).as_deref() == Some("player_action_response"))
        };
        let start = int_of(basis.content().get("canonical_start_sequence"));
        let end = messages
            .iter()
            .find(next_input)
            .and_then(|message| int_of(message.content().get("canonical_start_sequence")));
        if let Some(start) = start {
            action_ids.extend(
                snapshot
                    .events
                    .iter()
                    .filter(|event| {
                        event.sequence > start
                            && end.is_none_or(|end| event.sequence <= end)
                            && event.turn_id == interruption.turn_id
                    })
                    .filter_map(|event| event.action_id.clone()),
            );
        }
        let stop = messages.iter().find(next_input).map_or(i64::MAX, |message| message.sequence());
        let sequences: BTreeSet<i64> = messages
            .iter()
            .filter(|message| basis.sequence() < message.sequence() && message.sequence() < stop)
            .filter_map(|message| message.canonical_sequence())
            .collect();
        action_ids.extend(
            snapshot
                .events
                .iter()
                .filter(|event| sequences.contains(&event.sequence))
                .filter_map(|event| event.action_id.clone()),
        );
        action_ids.extend(messages.iter().filter_map(|message| {
            let content = message.content();
            (kind_of(content).as_deref() == Some("provider_mechanics_action_contract")
                && content.get("basis_message_id") == Some(&Json::from(basis.message_id())))
            .then(|| content.get("action_id").and_then(Json::as_str).map(str::to_owned))
            .flatten()
        }));
    }
    Ok(snapshot
        .actions
        .iter()
        .any(|action| action_ids.contains(&action.action_id) && action.status == ActionStatus::Committed))
}

/// `_project_player_snapshot(snapshot)`, after `_project_private_tool_inputs`. `full` is the
/// snapshot itself when it is a complete one (a display snapshot reads one only for a Retry's
/// saved changes).
fn project(
    store: &AuthorityStore,
    work: &sylvia_store::work::CampaignWork,
    state: &CampaignState,
    actions: &[ActionRecord],
    events: &[CanonicalEvent],
    full: Option<CampaignSnapshot>,
    live: Live,
) -> Result<Vec<PlayerProjection>, ActionError> {
    let campaign_id = state.campaign_id.as_str();
    let controls_available = live.controls_idle
        && state.writer == WriterRole::Dm
        && store.current_open_turn(campaign_id)?.is_some()
        && store.pending_mechanics_action(campaign_id)?.is_none();
    let mut tools: BTreeMap<(String, String), CatalogTool> = BTreeMap::new();
    if controls_available {
        // `_catalog(campaign_id)`, with its transition check.
        work.ensure_not_transitioning(campaign_id)?;
        for tool in super::content_reads::catalog_on(store, campaign_id, false, false)? {
            tools.insert((tool.package_id.clone(), tool.name.clone()), tool);
        }
    }
    let by_id: BTreeMap<&str, &ActionRecord> =
        actions.iter().map(|action| (action.action_id.as_str(), action)).collect();
    let control_scope = player_control_scope(store, state)?;
    let mut projected: Vec<PlayerProjection> = Vec::new();
    let mut calls: BTreeMap<String, JsonMap> = BTreeMap::new();
    let mut bookkeeping: BTreeMap<(String, String, String, String), bool> = BTreeMap::new();
    let text = |event: &CanonicalEvent| -> Result<String, ActionError> {
        event
            .safe_payload
            .get("text")
            .map(py_str)
            .ok_or_else(|| DomainError::uncaught("KeyError", "'text'").into())
    };
    for event in events {
        match event.event_type {
            CanonicalEventType::ToolCall if event.action_id.is_some() => {
                calls.insert(event.action_id.clone().unwrap(), event.safe_payload.clone());
            }
            CanonicalEventType::Think => projected.push(PlayerProjection {
                kind: PlayerProjectionKind::Think,
                sequence: Some(event.sequence),
                text: Some(text(event)?),
                safe_payload: event.safe_payload.clone(),
                collapsed_by_default: true,
                provisional: false,
                action_id: None,
                attempt_id: event.attempt_id.clone(),
            }),
            CanonicalEventType::Talk => projected.push(PlayerProjection {
                kind: PlayerProjectionKind::Talk,
                sequence: Some(event.sequence),
                text: Some(text(event)?),
                safe_payload: event.safe_payload.clone(),
                collapsed_by_default: false,
                provisional: false,
                action_id: None,
                attempt_id: event.attempt_id.clone(),
            }),
            CanonicalEventType::ToolResult => {
                let action_key = event.action_id.clone().unwrap_or_default();
                let mut payload = event.safe_payload.clone();
                if let Some(call) = calls.get(&action_key) {
                    if let Some(Json::String(tool_name)) =
                        call.get("tool_name").filter(|name| name.as_str() != Some(""))
                    {
                        payload.insert("tool_name".into(), tool_name.as_str().into());
                        if let Some(action) = by_id.get(action_key.as_str()) {
                            let identity = (
                                action.package_id.clone(),
                                action.package_version.clone(),
                                action.package_sha256.clone(),
                                action.tool_name.clone(),
                            );
                            if !bookkeeping.contains_key(&identity) {
                                let declaration = store.declared_package_tool(
                                    &identity.0,
                                    &identity.1,
                                    &identity.2,
                                    &identity.3,
                                )?;
                                let routine = declaration.is_some_and(|declaration| {
                                    declaration.input_schema.contains_key("x-sylvia-gm-character-update")
                                });
                                bookkeeping.insert(identity.clone(), routine);
                            }
                            if bookkeeping[&identity] {
                                payload.insert("routine_activity".into(), "character_update".into());
                            }
                        }
                    }
                    if let Some(Json::Object(parameters)) = call.get("parameters") {
                        payload.insert("parameters".into(), Json::Object(parameters.clone()));
                    }
                }
                let controls = card_control_readiness(
                    store,
                    state,
                    event.action_id.as_deref().and_then(|id| by_id.get(id).copied()),
                    &tools,
                    control_scope.as_ref(),
                )?;
                payload.insert("action_id".into(), event.action_id.clone().map_or(Json::Null, Json::from));
                payload.insert(
                    "result_card_action_availability".into(),
                    Json::Array(controls.iter().map(|(available, _)| Json::Bool(*available)).collect()),
                );
                payload.insert(
                    "result_card_action_reasons".into(),
                    Json::Array(controls.iter().map(|(_, reason)| reason.clone()).collect()),
                );
                projected.push(PlayerProjection {
                    kind: PlayerProjectionKind::ToolCard,
                    sequence: Some(event.sequence),
                    text: None,
                    safe_payload: owned_payload(&Json::Object(payload))?,
                    collapsed_by_default: true,
                    provisional: false,
                    action_id: event.action_id.clone(),
                    attempt_id: None,
                });
            }
            _ => {}
        }
    }
    if let Some(pending) = store.pending_mechanics_action(campaign_id)? {
        let mut payload = JsonMap::new();
        payload.insert("action_id".into(), pending.action_id.as_str().into());
        payload.insert("basis_revision".into(), pending.basis_revision.into());
        payload.insert("turn_id".into(), pending.turn_id.as_str().into());
        payload.insert("choice_id".into(), pending.choice.choice_id.as_str().into());
        if !pending.prior_answers.is_empty() {
            payload.insert("choice_sequence".into(), (pending.prior_answers.len() as i64).into());
        }
        payload.insert("prompt".into(), pending.choice.prompt.as_str().into());
        payload.insert(
            "options".into(),
            Json::Array(
                pending
                    .choice
                    .options
                    .iter()
                    .map(|option| {
                        let mut item = JsonMap::new();
                        item.insert("option_id".into(), option.option_id.as_str().into());
                        item.insert("label".into(), option.label.as_str().into());
                        Json::Object(item)
                    })
                    .collect(),
            ),
        );
        projected.push(PlayerProjection {
            kind: PlayerProjectionKind::MechanicsChoice,
            sequence: None,
            text: None,
            safe_payload: payload,
            collapsed_by_default: false,
            provisional: false,
            action_id: Some(pending.action_id.clone()),
            attempt_id: None,
        });
    }
    projected.extend(live.provisional);
    let mut recovery = full;
    for interruption in store.interruptions(campaign_id)? {
        let answered =
            interruption.resolving_retry_id.as_ref().is_some_and(|id| live.active_retry_ids.contains(id));
        if !interruption.retryable || answered {
            continue;
        }
        if recovery.is_none() {
            recovery = Some(store.snapshot(campaign_id)?);
        }
        let contexts = store.campaign_session_contexts(campaign_id, &[])?;
        let can_redirect = contexts.iter().any(|context| can_redirect_interruption(&interruption, context));
        let saved = interruption_saved_changes(store, recovery.as_ref().unwrap(), &interruption)?;
        let mut payload = JsonMap::new();
        payload.insert("interruption_id".into(), interruption.interruption_id.as_str().into());
        payload.insert("failed_attempt_id".into(), interruption.failed_attempt_id.as_str().into());
        payload.insert(
            "resolving_retry_id".into(),
            interruption.resolving_retry_id.clone().map_or(Json::Null, Json::from),
        );
        payload.insert("can_redirect".into(), can_redirect.into());
        payload.insert("saved_changes".into(), saved.into());
        projected.push(PlayerProjection {
            kind: PlayerProjectionKind::Retry,
            sequence: None,
            text: Some("Narration interrupted—Retry".into()),
            safe_payload: payload,
            collapsed_by_default: false,
            provisional: false,
            action_id: None,
            attempt_id: Some(interruption.failed_attempt_id.clone()),
        });
    }
    Ok(projected)
}
