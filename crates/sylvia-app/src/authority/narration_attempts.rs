//! The narration attempt lifecycle, from `application/authority.py`: `start_narration_attempt`
//! (with `_start_narration_attempt`), `emit_narration_token`, `flush_narration`,
//! `bind_narration_action`, `complete_narration_attempt` (with `_complete_narration_attempt`),
//! `disconnect_narration`, `reconnect_narration`, `narration_attempt_status`,
//! `expire_narration`, `interrupt_narration` (with `_expire_narration_locked`),
//! `_flush_attempt_buffer`, `_attempt` and `_live_attempt`; and the interruption reads
//! `unresolved_session_interruption`, `retryable_interruption`, `narration_interruption`, with
//! `_can_redirect_interruption`, and `private_provider_index`.
//!
//! Every attempt lives in the owner's [`NarrationState`], keyed by `(campaign_id, attempt_id)`.
//! Each operation holds [`NarrationState::region`] where Python holds `_narration_lock`
//! (across the store calls it makes), and reads and writes the attempt itself under
//! [`NarrationState::with`]. An attempt holds its campaign's work from its start until it
//! completes, is interrupted or expires.
//!
//! Two details differ from Python, neither observable through the owner's operations:
//! - an attempt's `cancel` runs with the data lock released (still inside the region), since
//!   Python's lock is reentrant and a cancel handle that read the narration state would otherwise
//!   deadlock;
//! - until lane 2's `action_sequences::last_attempt_commit` lands, the interrupted attempt's last
//!   queued action ([`last_attempt_commit`]) is read as none, which is what Python finds without
//!   saved action sequences (no Rust owner writes them yet).

use super::actions::ActionError;
use super::narration::{CancelHandle, NarrationAttempt, NarrationState};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::authority::{
    response_blocks_text, validate_authority_identity, validate_response_blocks, CanonicalEvent,
    CanonicalReplay, NarrationInterruption, ProseChannel, WriterRole,
};
use sylvia_domain::session::{SessionContext, SessionMessageKind};
use sylvia_domain::DomainError;
use sylvia_store::interruptions::{new_interruption_id, NarrationSegment};
use sylvia_store::StoreHandle;

type Key = (String, String);

fn rejected(message: &str) -> ActionError {
    DomainError::command_rejected(message).into()
}

fn conflict(message: &str) -> ActionError {
    DomainError::authority("IdempotencyConflict", message).into()
}

/// `start_narration_attempt(...)`'s keyword arguments.
pub struct NarrationStart {
    pub campaign_id: String,
    pub attempt_id: String,
    pub turn_id: String,
    pub cancel: CancelHandle,
    pub last_action_id: Option<String>,
    /// `interruption_id`: the interruption a Retry resolves.
    pub interruption_id: Option<String>,
}

/// What a closure reads of an attempt.
fn read<R>(narration: &NarrationState, key: &Key, f: impl FnOnce(&mut NarrationAttempt) -> R) -> Option<R> {
    narration
        .with(|inner| inner.attempts.iter_mut().find(|(item, _)| item == key).map(|(_, attempt)| f(attempt)))
}

/// `_attempt(key)`.
fn attempt<R>(
    narration: &NarrationState,
    key: &Key,
    f: impl FnOnce(&mut NarrationAttempt) -> R,
) -> Result<R, ActionError> {
    read(narration, key, f).ok_or_else(|| rejected("Narration attempt is unavailable"))
}

/// `_live_attempt(key)`.
fn live_attempt<R>(
    narration: &NarrationState,
    key: &Key,
    f: impl FnOnce(&mut NarrationAttempt) -> Result<R, ActionError>,
) -> Result<R, ActionError> {
    attempt(narration, key, |attempt| {
        if attempt.terminal {
            return Err(rejected("Narration attempt is terminal"));
        }
        f(attempt)
    })?
}

fn key(campaign_id: &str, attempt_id: &str) -> Key {
    (campaign_id.to_owned(), attempt_id.to_owned())
}

/// `isinstance(now, (int, float)) and math.isfinite(now)`.
fn finite(now: f64, message: &str) -> Result<f64, ActionError> {
    if now.is_finite() {
        Ok(now)
    } else {
        Err(DomainError::value(message).into())
    }
}

/// `start_narration_attempt(...)`: begin the campaign's work and admit one live attempt.
pub async fn start_narration_attempt(store: &StoreHandle, start: NarrationStart) -> Result<(), ActionError> {
    let work = super::guided::campaign_work(store, &start.campaign_id).await?;
    // A failure drops `work`, finishing it, as Python's `except BaseException` does.
    start_attempt(store, start, None, Some(work)).await
}

/// `_start_narration_attempt(..., failure_interruption_id=..., work_registered=...)`.
pub(crate) async fn start_attempt(
    store: &StoreHandle,
    start: NarrationStart,
    failure_interruption_id: Option<String>,
    work: Option<sylvia_store::work::CampaignWorkGuard>,
) -> Result<(), ActionError> {
    let narration = store.narration();
    if narration.disconnect_grace().is_none() {
        return Err(rejected("Narration disconnect grace was not injected"));
    }
    for (value, label) in [
        (&start.campaign_id, "Campaign identity"),
        (&start.attempt_id, "Narration attempt identity"),
        (&start.turn_id, "TURN identity"),
    ] {
        validate_authority_identity(value, label, false)?;
    }
    let snapshot = {
        let campaign_id = start.campaign_id.clone();
        store.call(move |store| store.snapshot(&campaign_id)).await?
    };
    if !snapshot.turns.iter().any(|(turn_id, _)| *turn_id == start.turn_id) {
        return Err(rejected("TURN is unavailable"));
    }
    if let Some(last_action_id) = &start.last_action_id {
        let receipt = {
            let (campaign_id, action_id) = (start.campaign_id.clone(), last_action_id.clone());
            store.call(move |store| store.action_receipt(&campaign_id, &action_id)).await?
        };
        let action = snapshot
            .actions
            .iter()
            .find(|item| item.action_id == receipt.action_id)
            .ok_or_else(|| ActionError::Store(sylvia_store::StoreError::Other("StopIteration".into())))?;
        if action.turn_id != start.turn_id {
            return Err(rejected("Stored action result belongs to another TURN"));
        }
    }
    let key = key(&start.campaign_id, &start.attempt_id);
    let _region = narration.region().await;
    narration.with(|inner| {
        let previous = inner.attempts.iter().position(|(item, _)| *item == key);
        if let Some(index) = previous {
            if !inner.attempts[index].1.terminal || start.interruption_id.is_none() {
                return Err(conflict("Narration attempt identity already exists"));
            }
        }
        if inner.attempts.iter().any(|(_, item)| item.campaign_id == start.campaign_id && !item.terminal) {
            return Err(rejected("Campaign already has live narration"));
        }
        let existing: Vec<Option<i64>> = snapshot
            .events
            .iter()
            .filter(|event| event.attempt_id.as_deref() == Some(start.attempt_id.as_str()))
            .map(|event| event.attempt_ordinal)
            .collect();
        if existing.iter().enumerate().any(|(index, ordinal)| *ordinal != Some(index as i64)) {
            return Err(rejected("Stored narration attempt chronology is invalid"));
        }
        if start.interruption_id.is_none() && !existing.is_empty() {
            return Err(conflict("Narration attempt already has canonical output"));
        }
        let ordinal = if start.interruption_id.is_some() { 0 } else { existing.len() as i64 };
        let mut attempt = NarrationAttempt::new(
            start.campaign_id.clone(),
            start.attempt_id.clone(),
            start.turn_id.clone(),
            start.cancel,
            start.last_action_id.clone(),
        );
        attempt.ordinal = ordinal;
        attempt.interruption_id = failure_interruption_id;
        attempt.retry_basis_id = start.interruption_id.clone();
        attempt.work = work;
        // `self._attempts[key] = ...`: a replaced key keeps its place.
        match previous {
            Some(index) => inner.attempts[index].1 = attempt,
            None => inner.attempts.push((key.clone(), attempt)),
        }
        Ok(())
    })
}

/// `_flush_attempt_buffer(attempt)`: commit the attempt's prose as its next segment.
async fn flush_buffer(store: &StoreHandle, key: &Key) -> Result<(), ActionError> {
    let narration = store.narration();
    let segment = attempt(narration, key, |attempt| match attempt.channel {
        Some(channel) if !attempt.buffer.is_empty() => Some(NarrationSegment {
            campaign_id: attempt.campaign_id.clone(),
            turn_id: attempt.turn_id.clone(),
            attempt_id: attempt.attempt_id.clone(),
            ordinal: attempt.ordinal,
            channel,
            text: attempt.buffer.clone(),
            response_blocks: attempt.response_blocks.clone(),
        }),
        _ => {
            attempt.buffer.clear();
            None
        }
    })?;
    let Some(segment) = segment else { return Ok(()) };
    store.call(move |store| store.append_narration_segment(&segment)).await?;
    attempt(narration, key, |attempt| {
        attempt.ordinal += 1;
        attempt.buffer.clear();
        attempt.response_blocks = None;
    })
}

/// `emit_narration_token(campaign_id, attempt_id, channel, token, response_blocks=...)`: add
/// streamed prose to the live attempt, first committing what it streamed on another channel.
pub async fn emit_narration_token(
    store: &StoreHandle,
    campaign_id: &str,
    attempt_id: &str,
    channel: ProseChannel,
    token: &str,
    response_blocks: Option<&Json>,
) -> Result<(), ActionError> {
    let narration = store.narration();
    let key = key(campaign_id, attempt_id);
    let _region = narration.region().await;
    let switched = live_attempt(narration, &key, |attempt| {
        if attempt.disconnect_deadline.is_some() {
            return Err(rejected("Disconnected narration cannot accept tokens"));
        }
        Ok(attempt.channel.is_some_and(|current| current != channel))
    })?;
    if switched {
        flush_buffer(store, &key).await?;
    }
    attempt(narration, &key, |attempt| {
        if attempt.response_blocks.is_some() && response_blocks.is_none() {
            return Err(rejected("Completed response blocks cannot accept more text"));
        }
        if let Some(blocks) = response_blocks {
            if channel != ProseChannel::Talk {
                return Err(rejected("Response blocks are valid only for TALK"));
            }
            let not_safe = |error: DomainError| -> ActionError {
                if error.python_class() == "ValueError" {
                    rejected("Response blocks are not player-safe")
                } else {
                    error.into()
                }
            };
            let admitted = validate_response_blocks(blocks).map_err(not_safe)?;
            let fallback =
                response_blocks_text(&Json::Array(admitted.iter().cloned().map(Json::Object).collect()))
                    .map_err(not_safe)?;
            if fallback != token {
                return Err(rejected("Response block fallback text does not match"));
            }
            if !attempt.buffer.is_empty() || attempt.response_blocks.is_some() {
                return Err(rejected("Completed response blocks cannot be streamed twice"));
            }
            attempt.response_blocks = Some(admitted);
        }
        attempt.channel = Some(channel);
        attempt.buffer.push_str(token);
        Ok(())
    })?
}

/// `flush_narration(campaign_id, attempt_id)`: commit the current safe prose buffer before a
/// chronological tool call.
pub async fn flush_narration(
    store: &StoreHandle,
    campaign_id: &str,
    attempt_id: &str,
) -> Result<(), ActionError> {
    let narration = store.narration();
    let key = key(campaign_id, attempt_id);
    let _region = narration.region().await;
    let buffered = live_attempt(narration, &key, |attempt| {
        if attempt.disconnect_deadline.is_some() {
            return Err(rejected("Disconnected narration cannot flush"));
        }
        Ok(!attempt.buffer.is_empty())
    })?;
    if buffered {
        flush_buffer(store, &key).await?;
    }
    Ok(())
}

/// `bind_narration_action(campaign_id, attempt_id, action_id)`: associate the latest committed
/// action result of the attempt's TURN with it.
pub async fn bind_narration_action(
    store: &StoreHandle,
    campaign_id: &str,
    attempt_id: &str,
    action_id: &str,
) -> Result<(), ActionError> {
    let narration = store.narration();
    let key = key(campaign_id, attempt_id);
    let _region = narration.region().await;
    let turn_id = live_attempt(narration, &key, |attempt| Ok(attempt.turn_id.clone()))?;
    let (campaign, action) = (campaign_id.to_owned(), action_id.to_owned());
    let action_turn = store
        .call(move |store| {
            let receipt = store.action_receipt(&campaign, &action)?;
            let snapshot = store.snapshot(&campaign)?;
            snapshot
                .actions
                .into_iter()
                .find(|item| item.action_id == receipt.action_id)
                .map(|item| item.turn_id)
                .ok_or_else(|| sylvia_store::StoreError::Other("StopIteration".into()))
        })
        .await?;
    if action_turn != turn_id {
        return Err(rejected("Stored action result belongs to another TURN"));
    }
    attempt(narration, &key, |attempt| attempt.last_action_id = Some(action_id.to_owned()))
}

/// `complete_narration_attempt(campaign_id, attempt_id)`: commit the attempt's last prose (or
/// resolve the interruption a Retry answers), end it and its work, and return its canonical
/// events.
pub async fn complete_narration_attempt(
    store: &StoreHandle,
    campaign_id: &str,
    attempt_id: &str,
) -> Result<Vec<CanonicalEvent>, ActionError> {
    let narration = store.narration();
    let key = key(campaign_id, attempt_id);
    let _region = narration.region().await;
    let (retry_basis, talk, last_action_id) = live_attempt(narration, &key, |attempt| {
        if attempt.disconnect_deadline.is_some() {
            return Err(rejected("Disconnected narration cannot complete"));
        }
        let talk = (attempt.channel == Some(ProseChannel::Talk) && !attempt.buffer.is_empty()).then(|| {
            NarrationSegment {
                campaign_id: attempt.campaign_id.clone(),
                turn_id: attempt.turn_id.clone(),
                attempt_id: attempt.attempt_id.clone(),
                ordinal: attempt.ordinal,
                channel: ProseChannel::Talk,
                text: attempt.buffer.clone(),
                response_blocks: attempt.response_blocks.clone(),
            }
        });
        Ok((attempt.retry_basis_id.clone(), talk, attempt.last_action_id.clone()))
    })?;
    match retry_basis {
        None => flush_buffer(store, &key).await?,
        Some(interruption_id) => {
            if let Some(segment) = talk {
                store.call(move |store| store.append_retry_resolution(&interruption_id, &segment)).await?;
                attempt(narration, &key, |attempt| {
                    attempt.ordinal += 1;
                    attempt.buffer.clear();
                })?;
            } else if let Some(last_action_id) = last_action_id {
                let (campaign, retry) = (campaign_id.to_owned(), attempt_id.to_owned());
                store
                    .call(move |store| {
                        let snapshot = store.snapshot(&campaign)?;
                        let Some(last) = snapshot
                            .events
                            .iter()
                            .rev()
                            .find(|event| event.action_id.as_deref() == Some(last_action_id.as_str()))
                        else {
                            return Err(sylvia_store::StoreError::from(DomainError::command_rejected(
                                "Retry action result is unavailable",
                            )));
                        };
                        store.resolve_interruption(&campaign, &interruption_id, &retry, last.sequence)
                    })
                    .await?;
            } else {
                return Err(rejected("A successful Retry must produce TALK or an action result"));
            }
        }
    }
    // `attempt.terminal = True`, then `_finish_campaign_work` as its guard drops.
    let work = attempt(narration, &key, |attempt| {
        attempt.terminal = true;
        attempt.work.take()
    })?;
    drop(work);
    let (campaign, attempt_id) = (campaign_id.to_owned(), attempt_id.to_owned());
    Ok(store
        .call(move |store| {
            Ok(store
                .snapshot(&campaign)?
                .events
                .into_iter()
                .filter(|event| event.attempt_id.as_deref() == Some(attempt_id.as_str()))
                .collect())
        })
        .await?)
}

/// `disconnect_narration(campaign_id, attempt_id, now=...)`: start the attempt's grace, once;
/// the deadline.
pub async fn disconnect_narration(
    store: &StoreHandle,
    campaign_id: &str,
    attempt_id: &str,
    now: f64,
) -> Result<f64, ActionError> {
    let now = finite(now, "Disconnect time must be finite")?;
    let narration = store.narration();
    let Some(grace) = narration.disconnect_grace() else {
        return Err(rejected("Narration disconnect grace was not injected"));
    };
    let key = key(campaign_id, attempt_id);
    let _region = narration.region().await;
    live_attempt(narration, &key, |attempt| Ok(*attempt.disconnect_deadline.get_or_insert(now + grace)))
}

/// `reconnect_narration(campaign_id, attempt_id, now=..., acknowledged_sequence=...)`: resume a
/// disconnected attempt within its grace (one past it is interrupted) and replay what the client
/// has not acknowledged.
pub async fn reconnect_narration(
    store: &StoreHandle,
    campaign_id: &str,
    attempt_id: &str,
    now: f64,
    acknowledged_sequence: i64,
) -> Result<CanonicalReplay, ActionError> {
    let now = finite(now, "Reconnect time must be finite")?;
    let narration = store.narration();
    let key = key(campaign_id, attempt_id);
    {
        let _region = narration.region().await;
        let elapsed = live_attempt(narration, &key, |attempt| {
            Ok(match attempt.disconnect_deadline {
                // `not now < deadline`; a deadline is always finite.
                Some(deadline) if now >= deadline => true,
                Some(_) => {
                    attempt.disconnect_deadline = None;
                    false
                }
                None => false,
            })
        })?;
        if elapsed {
            expire_locked(store, &key).await?;
            return Err(rejected("Narration reconnect deadline elapsed"));
        }
    }
    Ok(super::player_reads::canonical_replay(store, narration, campaign_id, acknowledged_sequence).await?)
}

/// `narration_attempt_status(campaign_id, attempt_id)`: the attempt's transport lifecycle.
pub async fn narration_attempt_status(
    store: &StoreHandle,
    campaign_id: &str,
    attempt_id: &str,
) -> Result<String, ActionError> {
    let narration = store.narration();
    let _region = narration.region().await;
    attempt(narration, &key(campaign_id, attempt_id), |attempt| {
        if attempt.terminal {
            if attempt.interruption_id.is_none() {
                "completed"
            } else {
                "interrupted"
            }
        } else if attempt.disconnect_deadline.is_some() {
            "disconnected"
        } else {
            "active"
        }
        .to_owned()
    })
}

/// A terminal attempt's interruption, or the refusal for one that completed.
async fn terminal_interruption(
    store: &StoreHandle,
    campaign_id: &str,
    interruption_id: Option<String>,
) -> Result<NarrationInterruption, ActionError> {
    let Some(interruption_id) = interruption_id else {
        return Err(rejected("Narration attempt completed successfully"));
    };
    let campaign = campaign_id.to_owned();
    Ok(store.call(move |store| store.interruption(&campaign, &interruption_id)).await?)
}

/// `expire_narration(campaign_id, attempt_id, now=...)`: interrupt a disconnected attempt whose
/// grace has passed.
pub async fn expire_narration(
    store: &StoreHandle,
    campaign_id: &str,
    attempt_id: &str,
    now: f64,
) -> Result<NarrationInterruption, ActionError> {
    let narration = store.narration();
    let key = key(campaign_id, attempt_id);
    let _region = narration.region().await;
    let now = finite(now, "Expiry time must be finite")?;
    let (terminal, interruption_id, deadline) = attempt(narration, &key, |attempt| {
        (attempt.terminal, attempt.interruption_id.clone(), attempt.disconnect_deadline)
    })?;
    if terminal {
        return terminal_interruption(store, campaign_id, interruption_id).await;
    }
    // `attempt.disconnect_deadline is None or now < attempt.disconnect_deadline`.
    if deadline.is_none_or(|deadline| now < deadline) {
        return Err(rejected("Narration grace has not expired"));
    }
    expire_locked(store, &key).await
}

/// `interrupt_narration(campaign_id, attempt_id)`: interrupt the attempt now; its cancel runs
/// once.
pub async fn interrupt_narration(
    store: &StoreHandle,
    campaign_id: &str,
    attempt_id: &str,
) -> Result<NarrationInterruption, ActionError> {
    let narration = store.narration();
    let key = key(campaign_id, attempt_id);
    let _region = narration.region().await;
    let (terminal, interruption_id) =
        attempt(narration, &key, |attempt| (attempt.terminal, attempt.interruption_id.clone()))?;
    if terminal {
        return terminal_interruption(store, campaign_id, interruption_id).await;
    }
    expire_locked(store, &key).await
}

/// `action_sequences.last_attempt_commit(owner, campaign_id, attempt_id)`, lane 2's, read as none
/// until it lands (see the module notes).
async fn last_attempt_commit(
    _store: &StoreHandle,
    _campaign_id: &str,
    _attempt_id: &str,
) -> Result<Option<String>, ActionError> {
    Ok(None)
}

/// `_expire_narration_locked(attempt)`: end the attempt and its work, save its interruption and
/// cancel it once. The caller holds the region.
async fn expire_locked(store: &StoreHandle, key: &Key) -> Result<NarrationInterruption, ActionError> {
    let narration = store.narration();
    let fresh = new_interruption_id()?;
    let (work, campaign_id, attempt_id) = attempt(narration, key, |attempt| {
        attempt.terminal = true;
        let work = attempt.work.take();
        attempt.buffer.clear();
        attempt.channel = None;
        if attempt.interruption_id.is_none() {
            attempt.interruption_id = Some(fresh);
        }
        (work, attempt.campaign_id.clone(), attempt.attempt_id.clone())
    })?;
    drop(work);
    if let Some(queued) = last_attempt_commit(store, &campaign_id, &attempt_id).await? {
        attempt(narration, key, |attempt| attempt.last_action_id = Some(queued))?;
    }
    let (interruption_id, turn_id, last_action_id) = attempt(narration, key, |attempt| {
        (
            attempt.interruption_id.clone().unwrap_or_default(),
            attempt.turn_id.clone(),
            attempt.last_action_id.clone(),
        )
    })?;
    let record = {
        let campaign = campaign_id.clone();
        store
            .call(move |store| {
                store.record_interruption(
                    &campaign,
                    &interruption_id,
                    &turn_id,
                    last_action_id.as_deref(),
                    &attempt_id,
                )
            })
            .await?
    };
    // The cancel handle leaves the attempt while it runs, outside the data lock.
    let cancel = attempt(narration, key, |attempt| {
        attempt.interruption_id = Some(record.interruption_id.clone());
        if attempt.cancelled {
            None
        } else {
            attempt.cancelled = true;
            Some(std::mem::replace(&mut attempt.cancel, Box::new(|| {})))
        }
    })?;
    if let Some(mut cancel) = cancel {
        cancel();
        attempt(narration, key, |attempt| attempt.cancel = cancel)?;
    }
    Ok(record)
}

/// `_can_redirect_interruption(interruption, context)`: a saved Stop of the failed attempt, or a
/// replacement accepted for its Retry before a restart.
pub fn can_redirect_interruption(interruption: &NarrationInterruption, context: &SessionContext) -> bool {
    let text = |content: &JsonMap, key: &str| content.get(key).and_then(Json::as_str).map(str::to_owned);
    interruption.retryable
        && context.messages.iter().any(|message| {
            let content = message.content();
            let stopped = content.get("kind") == Some(&Json::from("session_attempt_terminal"))
                && content.get("attempt_id") == Some(&Json::from(interruption.failed_attempt_id.as_str()))
                && content.get("failure_category") == Some(&Json::from("user_cancelled"));
            let redirected = message.kind() == SessionMessageKind::User
                && text(content, "redirect_interruption_id").as_deref()
                    == Some(interruption.interruption_id.as_str())
                && equals_optional(content.get("attempt_id"), interruption.resolving_retry_id.as_deref());
            stopped || redirected
        })
}

/// `content.get(key) == value` for an optional text `value` (`None` matches a missing key or a
/// JSON null).
fn equals_optional(found: Option<&Json>, value: Option<&str>) -> bool {
    match (found, value) {
        (None | Some(Json::Null), None) => true,
        (Some(Json::String(found)), Some(value)) => found == value,
        _ => false,
    }
}

/// `unresolved_session_interruption(context)`: a GM session's latest open interruption, and
/// whether new input redirects it (after a settled Stop) rather than needing Retry first.
pub async fn unresolved_session_interruption(
    store: &StoreHandle,
    context: &SessionContext,
) -> Result<Option<(NarrationInterruption, bool)>, ActionError> {
    let Some(campaign_id) = context.session.campaign_id().map(str::to_owned) else { return Ok(None) };
    if context.session.role() != WriterRole::Dm {
        return Ok(None);
    }
    let attempts: Vec<Option<Json>> =
        context.messages.iter().map(|message| message.content().get("attempt_id").cloned()).collect();
    let unresolved: Vec<NarrationInterruption> = store
        .call(move |store| store.interruptions(&campaign_id))
        .await?
        .into_iter()
        .filter(|interruption| {
            interruption.retryable
                && attempts.iter().any(|attempt| {
                    attempt.as_ref() == Some(&Json::from(interruption.failed_attempt_id.as_str()))
                })
        })
        .collect();
    Ok(unresolved.last().map(|latest| (latest.clone(), can_redirect_interruption(latest, context))))
}

/// `retryable_interruption(campaign_id, failed_attempt_id=...)`: the one durable retry basis of
/// a failed attempt, if it has one.
pub async fn retryable_interruption(
    store: &StoreHandle,
    campaign_id: &str,
    failed_attempt_id: &str,
) -> Result<Option<NarrationInterruption>, ActionError> {
    if validate_authority_identity(campaign_id, "Campaign identity", false).is_err()
        || validate_authority_identity(failed_attempt_id, "Failed narration attempt identity", false).is_err()
    {
        return Err(rejected("Narration retry identity is invalid"));
    }
    let (campaign, failed) = (campaign_id.to_owned(), failed_attempt_id.to_owned());
    let mut matches: Vec<NarrationInterruption> = store
        .call(move |store| store.interruptions(&campaign))
        .await?
        .into_iter()
        .filter(|interruption| interruption.failed_attempt_id == failed && interruption.retryable)
        .collect();
    if matches.len() > 1 {
        return Err(rejected("Narration retry chronology is ambiguous"));
    }
    Ok(matches.pop())
}

/// `narration_interruption(campaign_id, interruption_id)`: one durable retry basis by identity.
pub async fn narration_interruption(
    store: &StoreHandle,
    campaign_id: &str,
    interruption_id: &str,
) -> Result<NarrationInterruption, ActionError> {
    let (campaign, interruption) = (campaign_id.to_owned(), interruption_id.to_owned());
    Ok(store.call(move |store| store.interruption(&campaign, &interruption)).await?)
}

/// `saved_action_receipt(campaign_id, action_id)`: a committed result read for a redirected
/// narration, never executed again.
pub async fn saved_action_receipt(
    store: &StoreHandle,
    campaign_id: &str,
    action_id: &str,
) -> Result<sylvia_domain::authority::ActionReceipt, ActionError> {
    let (campaign, action) = (campaign_id.to_owned(), action_id.to_owned());
    Ok(store.call(move |store| store.action_receipt(&campaign, &action)).await?)
}

/// `private_provider_index(session_id)`: availability and correlation of the session's private
/// thinking, never its text.
pub async fn private_provider_index(
    store: &StoreHandle,
    session_id: &str,
) -> Result<Vec<JsonMap>, ActionError> {
    let session = session_id.to_owned();
    Ok(store.call(move |store| store.private_provider_index(&session)).await?)
}
