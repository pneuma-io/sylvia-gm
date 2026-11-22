//! `application/action_sequences.py`, in part: the two readers a GM response's context needs,
//! `response_results` and `response_reservations`, with `_bound_queues` and `_committed_ids`.
//!
//! Ported by lane 3 for lane 2's review (the manager's split, 23 Nov 2026); composing, executing
//! and recovering sequences stay with lane 2's port of the rest of the file.
//!
//! A saved queue is the content of an `action_sequence` evidence message, as Python's own
//! sequence code wrote it. A field Python indexes that is missing is its `KeyError`, an internal
//! failure here too.
//!
//! One divergence is left, reachable only through a hand-corrupted queue: Python's
//! `latest[content["sequence_id"]]`, `bases = {...}` and `... in bases` raise `TypeError` for an
//! unhashable value (a list or object as a sequence or basis identity); here those are compared
//! by Python's `==` instead.

use crate::player_actions::player_action_request_matches;
use std::collections::BTreeSet;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::WriterRole;
use sylvia_domain::player_actions::{player_control_origin, PLAYER_ACTION_ORIGIN};
use sylvia_domain::session::{SessionContext, SessionMessageKind};
use sylvia_store::{AuthorityStore, StoreError, StoreResult};

/// `QueueStatus.COMPLETED`.
const QUEUE_COMPLETED: &str = "completed";
/// `StepStatus.COMMITTED`.
const STEP_COMMITTED: &str = "committed";

fn key_error(key: &str) -> StoreError {
    StoreError::Other(format!("KeyError: '{key}'"))
}

fn item<'a>(map: &'a JsonMap, key: &str) -> StoreResult<&'a Json> {
    map.get(key).ok_or_else(|| key_error(key))
}

/// Python's truthiness of a JSON value.
fn truthy(value: Option<&Json>) -> bool {
    match value {
        None | Some(Json::Null) => false,
        Some(Json::Bool(flag)) => *flag,
        Some(Json::Number(number)) => number.as_f64().is_some_and(|value| value != 0.0),
        Some(Json::String(text)) => !text.is_empty(),
        Some(Json::Array(items)) => !items.is_empty(),
        Some(Json::Object(map)) => !map.is_empty(),
    }
}

/// `_bound_queues(context, basis_message_id=..., attempt_id=...)`: the latest saved version of
/// every queue bound to a narration basis, by `sequence_id`, in first-saved order.
fn bound_queues(
    context: &SessionContext,
    basis_message_id: Option<&str>,
    attempt_id: Option<&str>,
) -> StoreResult<Vec<JsonMap>> {
    let mut latest: Vec<(Json, JsonMap)> = Vec::new();
    for message in &context.messages {
        let content = message.content();
        if message.kind() != SessionMessageKind::Evidence
            || content.get("kind") != Some(&Json::from("action_sequence"))
            || !truthy(content.get("narration_basis_message_id"))
        {
            continue;
        }
        if basis_message_id
            .is_some_and(|basis| content.get("narration_basis_message_id") != Some(&Json::from(basis)))
        {
            continue;
        }
        if attempt_id.is_some_and(|attempt| content.get("narration_attempt_id") != Some(&Json::from(attempt)))
        {
            continue;
        }
        let sequence_id = item(content, "sequence_id")?.clone();
        match latest.iter_mut().find(|(known, _)| sylvia_domain::pyops::py_eq(known, &sequence_id)) {
            Some(entry) => entry.1 = content.clone(),
            None => latest.push((sequence_id, content.clone())),
        }
    }
    Ok(latest.into_iter().map(|(_, queue)| queue).collect())
}

/// `_committed_ids(owner, queue)`: the queue's steps whose saved player action committed with
/// exactly the origin the step's execution envelope asked for.
fn committed_ids(store: &AuthorityStore, queue: &JsonMap) -> StoreResult<Vec<String>> {
    let Json::Array(steps) = item(queue, "steps")? else {
        return Err(StoreError::Other("TypeError: steps is not a list".into()));
    };
    let steps: Vec<&JsonMap> = steps
        .iter()
        .map(|step| {
            step.as_object().ok_or_else(|| StoreError::Other("TypeError: step is not an object".into()))
        })
        .collect::<StoreResult<_>>()?;
    let operation_ids: Vec<String> = steps
        .iter()
        .map(|step| Ok(sylvia_domain::pyops::py_str(item(step, "operation_id")?)))
        .collect::<StoreResult<_>>()?;
    let campaign_id = sylvia_domain::pyops::py_str(item(queue, "campaign_id")?);
    let ids: Vec<&str> = operation_ids.iter().map(String::as_str).collect();
    let markers = store.player_action_markers(&campaign_id, &ids, PLAYER_ACTION_ORIGIN)?;
    let origins: Vec<(String, Json)> = markers
        .iter()
        .filter(|marker| marker.get("status") == Some(&Json::from(STEP_COMMITTED)))
        .map(|marker| (sylvia_domain::pyops::py_str(&marker["action_id"]), marker["origin"].clone()))
        .collect();
    let mut identities = Vec::new();
    for (step, operation_id) in steps.iter().zip(&operation_ids) {
        let Some(execution) = step.get("execution").filter(|value| truthy(Some(value))) else {
            continue;
        };
        // `origins` is a dict: of two committed markers for one action, the later one counts.
        let Some((_, stored)) = origins.iter().rev().find(|(action_id, _)| action_id == operation_id) else {
            continue;
        };
        // `envelope["source"]` on a truthy value that is not a mapping is Python's `TypeError`.
        let Json::Object(envelope) = execution else {
            return Err(StoreError::Other("TypeError: the step's execution is not a mapping".into()));
        };
        // Python's arguments, in its order, each as saved: the domain function validates the
        // source and supplies the control fields; the origin's first three items are then the raw
        // saved values (their keys already exist, so they keep their places).
        let source = item(envelope, "source")?;
        let expected_revision = item(envelope, "expected_revision")?.clone();
        let selection = envelope.get("selection").cloned().filter(|value| truthy(Some(value)));
        let session_id = item(queue, "session_id")?.clone();
        let mut request = player_control_origin(source, 0, None, None).map_err(StoreError::from)?;
        request.insert("expected_revision".into(), expected_revision);
        request.insert("selection".into(), selection.unwrap_or_else(|| Json::Object(JsonMap::new())));
        request.insert("session_id".into(), session_id);
        if !player_action_request_matches(stored.as_object(), &request) {
            return Err(sylvia_domain::DomainError::command_rejected(
                "Saved sequence action identity is inconsistent",
            )
            .into());
        }
        identities.push(operation_id.clone());
    }
    Ok(identities)
}

/// `response_results(owner, session_id=..., basis_message_id=...)`: every queue bound to the
/// basis that committed actions, with those identities and whether the provider stopped. A
/// session that does not exist (a Start preflight's unsaved one) has none.
pub fn response_results(
    store: &AuthorityStore,
    session_id: &str,
    basis_message_id: Option<&str>,
) -> StoreResult<Vec<JsonMap>> {
    let context = match store.session_context(session_id) {
        Ok(context) => context,
        Err(error) if error.rejection().is_some_and(|coded| coded.code == "session_unavailable") => {
            return Ok(Vec::new())
        }
        Err(error) => return Err(error),
    };
    if context.session.role() != WriterRole::Dm || context.session.campaign_id().is_none() {
        return Ok(Vec::new());
    }
    let mut results = Vec::new();
    for queue in bound_queues(&context, basis_message_id, None)? {
        let identities = committed_ids(store, &queue)?;
        if !identities.is_empty() {
            let stopped = item(&queue, "status")? != &Json::from(QUEUE_COMPLETED);
            let mut result = queue.clone();
            result.insert(
                "committed_action_ids".into(),
                Json::Array(identities.into_iter().map(Json::from).collect()),
            );
            result.insert("provider_execution_stopped".into(), stopped.into());
            results.push(result);
        }
    }
    Ok(results)
}

/// `response_reservations(owner, contexts)`: the actions bound queues committed, and the
/// narration attempts that answer them (their own, and retries or turn activity naming a bound
/// basis).
pub fn response_reservations(
    store: &AuthorityStore,
    contexts: &[SessionContext],
) -> StoreResult<(BTreeSet<String>, BTreeSet<String>)> {
    let (mut reserved, mut attempts) = (BTreeSet::new(), BTreeSet::new());
    for context in contexts {
        let queues = bound_queues(context, None, None)?;
        let bases: Vec<Json> = queues
            .iter()
            .map(|queue| item(queue, "narration_basis_message_id").cloned())
            .collect::<StoreResult<_>>()?;
        for queue in &queues {
            reserved.extend(committed_ids(store, queue)?);
            // Membership is only ever tested with attempt identities, which are strings.
            if let Json::String(attempt) = item(queue, "narration_attempt_id")? {
                attempts.insert(attempt.clone());
            }
        }
        for message in &context.messages {
            let content = message.content();
            let retry_or_activity = matches!(
                content.get("kind").and_then(Json::as_str),
                Some("gm_turn_activity" | "player_action_response_retry")
            );
            if message.kind() == SessionMessageKind::Evidence
                && retry_or_activity
                && content.get("basis_message_id").is_some_and(|basis| bases.contains(basis))
            {
                if let Some(Json::String(attempt)) = content.get("attempt_id") {
                    attempts.insert(attempt.clone());
                }
            }
        }
    }
    Ok((reserved, attempts))
}
