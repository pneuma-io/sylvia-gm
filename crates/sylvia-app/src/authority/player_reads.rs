//! The GM response context's player-action reads, the application half: `AuthorityOwner.
//! canonical_replay` (with `_project_private_tool_inputs` and the owner's provisional state),
//! `player_action_context` (with `_player_response_reservations`),
//! `player_action_response_context` (with `_response_action_ids`) and
//! `action_sequence_response_results`.
//!
//! The store reads are `sylvia_store`'s `snapshot`, `campaign_session_contexts`,
//! `response_window_actions` and `player_action_markers`; the saved-outcome readers are
//! [`crate::player_actions`] and [`crate::action_sequences`].

use super::narration::NarrationState;
use super::snapshots::engine_private_keys;
use crate::action_sequences;
use crate::player_actions::player_action_records;
use std::collections::BTreeSet;
use std::sync::Arc;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{CampaignSnapshot, CanonicalEventType, CanonicalReplay};
use sylvia_domain::session::SessionMessageKind;
use sylvia_store::{AuthorityStore, StoreHandle, StoreResult};

/// `_project_private_tool_inputs(snapshot)`: the parameters of every action and tool call of a
/// package with engine-private state are hidden from readers; the stored bytes stay as they are.
pub fn project_private_tool_inputs(
    store: &AuthorityStore,
    mut snapshot: CampaignSnapshot,
) -> StoreResult<CampaignSnapshot> {
    let private_keys = engine_private_keys(store, &snapshot.state.package_lock)?;
    let private_packages: BTreeSet<String> = snapshot
        .state
        .package_lock
        .entries()
        .iter()
        .filter(|entry| private_keys.iter().any(|key| key.starts_with(&format!("{}.", entry.package_id()))))
        .map(|entry| entry.package_id().to_owned())
        .collect();
    for action in &mut snapshot.actions {
        if private_packages.contains(&action.package_id) {
            action.parameters = JsonMap::new();
        }
    }
    for event in &mut snapshot.events {
        let private = matches!(event.safe_payload.get("package_id"), Some(Json::String(id)) if private_packages.contains(id));
        if event.event_type == CanonicalEventType::ToolCall && private {
            // `{**payload, "parameters": {}}`: an existing key keeps its place.
            event.safe_payload.insert("parameters".into(), Json::Object(JsonMap::new()));
        }
    }
    Ok(snapshot)
}

/// `AuthorityOwner.canonical_replay(campaign_id, acknowledged_sequence=...)`: the canonical events
/// after the acknowledged sequence, the owner's provisional state and the high water mark.
pub async fn canonical_replay(
    store: &StoreHandle,
    narration: &NarrationState,
    campaign_id: &str,
    acknowledged_sequence: i64,
) -> StoreResult<CanonicalReplay> {
    if acknowledged_sequence < 0 {
        return Err(sylvia_store::error::value_error("Acknowledged canonical sequence cannot be negative"));
    }
    let id = campaign_id.to_owned();
    let snapshot = store.call(move |store| project_private_tool_inputs(store, store.snapshot(&id)?)).await?;
    let high_water = snapshot.events.last().map_or(0, |event| event.sequence);
    Ok(CanonicalReplay {
        canonical: snapshot
            .events
            .into_iter()
            .filter(|event| event.sequence > acknowledged_sequence)
            .collect(),
        provisional: narration.provisional_projection(campaign_id),
        high_water,
    })
}

/// `_response_action_ids(campaign_id, basis)`: the saved response batch's actions: its explicit
/// `player_action_ids`, else the response window up to its `canonical_start_sequence`, else its
/// one `player_action_id`.
fn response_action_ids(
    store: &AuthorityStore,
    campaign_id: &str,
    basis: &JsonMap,
) -> StoreResult<Vec<String>> {
    if let Some(Json::Array(identities)) = basis.get("player_action_ids") {
        return Ok(identities.iter().filter_map(|key| key.as_str().map(str::to_owned)).collect());
    }
    // `isinstance(boundary, int)`: a boolean is an integer too.
    let boundary = match basis.get("canonical_start_sequence") {
        Some(Json::Bool(flag)) => Some(i64::from(*flag)),
        Some(Json::Number(number)) => number.as_i64(),
        _ => None,
    };
    if let Some(boundary) = boundary {
        return store.response_window_actions(campaign_id, &[], Some(boundary));
    }
    Ok(match basis.get("player_action_id") {
        Some(Json::String(key)) => vec![key.clone()],
        _ => Vec::new(),
    })
}

/// `_player_response_reservations(campaign_id)`: the actions saved responses already answer, and
/// the narration attempts that are those responses.
fn player_response_reservations(
    store: &AuthorityStore,
    campaign_id: &str,
) -> StoreResult<(BTreeSet<String>, BTreeSet<String>)> {
    let (mut reserved, mut attempts) = (BTreeSet::new(), BTreeSet::new());
    let contexts = store.campaign_session_contexts(
        campaign_id,
        &["player_action_response", "player_action_response_retry", "gm_turn_activity", "action_sequence"],
    )?;
    for context in &contexts {
        // `{message.message_id: message ...}`: a repeated identity keeps its first place, last value.
        let mut requests: Vec<(&str, &JsonMap)> = Vec::new();
        for message in &context.messages {
            if message.kind() == SessionMessageKind::Evidence
                && message.content().get("kind") == Some(&Json::from("player_action_response"))
            {
                match requests.iter_mut().find(|(id, _)| *id == message.message_id()) {
                    Some(entry) => entry.1 = message.content(),
                    None => requests.push((message.message_id(), message.content())),
                }
            }
        }
        for (_, content) in &requests {
            reserved.extend(response_action_ids(store, campaign_id, content)?);
            if let Some(Json::String(attempt)) = content.get("attempt_id") {
                attempts.insert(attempt.clone());
            }
        }
        for message in &context.messages {
            let content = message.content();
            let follows = matches!(
                content.get("kind").and_then(Json::as_str),
                Some("player_action_response_retry" | "gm_turn_activity")
            );
            let basis = content.get("basis_message_id").and_then(Json::as_str);
            if message.kind() == SessionMessageKind::Evidence
                && follows
                && basis.is_some_and(|basis| requests.iter().any(|(id, _)| *id == basis))
            {
                if let Some(Json::String(attempt)) = content.get("attempt_id") {
                    attempts.insert(attempt.clone());
                }
            }
        }
    }
    let (queue_reserved, queue_attempts) = action_sequences::response_reservations(store, &contexts)?;
    reserved.extend(queue_reserved);
    attempts.extend(queue_attempts);
    Ok((reserved, attempts))
}

/// `AuthorityOwner.player_action_context(campaign_id)`: the saved outcomes since the last
/// ordinary GM narration that no saved response has answered yet.
pub async fn player_action_context(store: &StoreHandle, campaign_id: &str) -> StoreResult<Vec<JsonMap>> {
    let id = campaign_id.to_owned();
    store
        .call(move |store| {
            let snapshot = store.snapshot(&id)?;
            let (reserved, response_attempts) = player_response_reservations(store, &id)?;
            let last_response = snapshot
                .events
                .iter()
                .filter(|event| {
                    event.event_type == CanonicalEventType::Talk
                        && !event
                            .attempt_id
                            .as_ref()
                            .is_some_and(|attempt| response_attempts.contains(attempt))
                })
                .map(|event| event.sequence)
                .max()
                .unwrap_or(0);
            let recent: BTreeSet<&str> = snapshot
                .events
                .iter()
                .filter(|event| event.sequence > last_response)
                .filter_map(|event| event.action_id.as_deref())
                .collect();
            Ok(player_action_records(&snapshot)
                .into_iter()
                .filter(|record| {
                    let action_id = record["action_id"].as_str().unwrap_or_default();
                    recent.contains(action_id) && !reserved.contains(action_id)
                })
                .collect())
        })
        .await
}

/// `AuthorityOwner.player_action_response_context(campaign_id, basis)`: exactly the saved outcomes
/// one response is assigned, retries included.
pub async fn player_action_response_context(
    store: &StoreHandle,
    campaign_id: &str,
    basis: &JsonMap,
) -> StoreResult<Vec<JsonMap>> {
    let (id, basis) = (campaign_id.to_owned(), Arc::new(basis.clone()));
    store
        .call(move |store| {
            let snapshot = store.snapshot(&id)?;
            let identities = response_action_ids(store, &id, &basis)?;
            Ok(player_action_records(&snapshot)
                .into_iter()
                .filter(|record| {
                    identities.iter().any(|identity| Some(identity.as_str()) == record["action_id"].as_str())
                })
                .collect())
        })
        .await
}

/// `AuthorityOwner.action_sequence_response_results(session_id=..., basis_message_id=...)`.
pub async fn action_sequence_response_results(
    store: &StoreHandle,
    session_id: &str,
    basis_message_id: Option<&str>,
) -> StoreResult<Vec<JsonMap>> {
    let (session_id, basis) = (session_id.to_owned(), basis_message_id.map(str::to_owned));
    store.call(move |store| action_sequences::response_results(store, &session_id, basis.as_deref())).await
}
