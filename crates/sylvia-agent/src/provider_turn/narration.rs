//! `agent/_provider_turn_phases.py`'s narration phases (T4): what a round that ends in prose
//! does with it.
//!
//! - **Corrections:** an empty completion recorded (`record_empty_completion`); a repeated
//!   response, a missing required tool call, private deliberation or a missing Campaign File
//!   update discarded, with the control that corrects it (`retry_*`).
//! - **Held prose:** prose held while a Ruleset decision or the player's declared action is
//!   still open (`preserve_*_narration`), or until the Campaign File disposition
//!   (`retain_campaign_file_narration`).
//! - **The response:** the final narration recorded and spoken (`record_held_narration`,
//!   `record_final_narration`).
//!
//! The controls are read from the Python source into `narration_texts.json` by
//! `generate_narration_texts.py`; each count is filled where Python's f-string has it.

use super::state::ProviderTurnState;
use super::{TurnEvents, TurnResult};
use crate::provider_session::tagged::Tagged;
use crate::session_loop::{ProviderEvent, SessionProviderInput};
use serde_json::{json, Value as Json};
use std::sync::OnceLock;
use sylvia_base::{CodedResult, JsonMap};

fn texts() -> &'static Json {
    static TEXTS: OnceLock<Json> = OnceLock::new();
    TEXTS.get_or_init(|| {
        serde_json::from_str(include_str!("narration_texts.json")).expect("exported texts are JSON")
    })
}

/// One exported control, its `{expression}` fields filled with Python's values.
fn control_text(key: &str, values: &[(&str, i64)]) -> String {
    let mut text = texts()[key].as_str().expect("an exported control").to_owned();
    for (expression, value) in values {
        text = text.replace(&format!("{{{expression}}}"), &value.to_string());
    }
    debug_assert!(!text.contains('{'), "every field of {key} is filled");
    text
}

fn object(pairs: Vec<(&str, Json)>) -> JsonMap {
    pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect()
}

/// `{"role": "system", "content": text}`.
fn system(text: String) -> JsonMap {
    object(vec![("role", json!("system")), ("content", json!(text))])
}

/// `_assistant_draft_message(narration)`: one private assistant draft.
fn assistant_draft(narration: &str) -> JsonMap {
    object(vec![("role", json!("assistant")), ("content", json!(narration))])
}

/// `{"kind": kind, **state.context_fields, ...}`: an evidence payload's opening.
fn with_context(state: &ProviderTurnState, kind: &str) -> CodedResult<JsonMap> {
    let mut payload = object(vec![("kind", json!(kind))]);
    for (key, value) in state.locals.context_fields.get()? {
        payload.insert(key.clone(), value.clone());
    }
    Ok(payload)
}

/// The turn, inference and basis fields most payloads carry.
fn identity(payload: &mut JsonMap, state: &ProviderTurnState, turn_id: &str) -> CodedResult<()> {
    payload.insert("turn_id".into(), json!(turn_id));
    payload.insert("inference_id".into(), json!(state.locals.inference_id.value()?));
    payload.insert("basis_message_id".into(), json!(state.locals.basis_message_id.value()?));
    Ok(())
}

async fn evidence(events: &mut dyn TurnEvents, message_id: String, payload: JsonMap) -> TurnResult<()> {
    events.emit(ProviderEvent::Evidence { message_id, payload }).await
}

/// `record_empty_completion(...)`.
pub async fn record_empty_completion(
    state: &mut ProviderTurnState,
    turn_id: &str,
    events: &mut dyn TurnEvents,
) -> TurnResult<()> {
    // `state.round_finish_reason or "..."`: an empty reason falls back too.
    let finish_reason = state
        .locals
        .round_finish_reason
        .value()?
        .filter(|reason| !reason.is_empty())
        .unwrap_or_else(|| "stream_ended_without_finish_reason".into());
    state.locals.finish_reason.set(finish_reason.clone());
    let next_correction = *state.locals.empty_completion_corrections_used.get()? + 1;
    let inference_id = state.locals.inference_id.value()?;
    let mut payload = object(vec![("kind", json!("provider_empty_completion"))]);
    identity(&mut payload, state, turn_id)?;
    payload.insert("finish_reason".into(), json!(finish_reason));
    payload.insert("tool_call_required".into(), json!(*state.locals.provider_tool_call_required.get()?));
    payload.insert("correction_attempt".into(), json!(next_correction));
    payload.insert("max_corrections".into(), json!(*state.locals.max_empty_completion_corrections.get()?));
    evidence(events, format!("{inference_id}-empty-completion"), payload).await
}

/// `retry_repeated_response(...)`.
pub async fn retry_repeated_response(
    state: &mut ProviderTurnState,
    turn_id: &str,
    events: &mut dyn TurnEvents,
) -> TurnResult<()> {
    let used = *state.locals.repeated_response_corrections_used.get()? + 1;
    state.locals.repeated_response_corrections_used.set(used);
    let maximum = *state.locals.max_repeated_response_corrections.get()?;
    let narration = state.locals.narration.value()?;
    let inference_id = state.locals.inference_id.value()?;
    let mut payload = object(vec![("kind", json!("provider_repeated_player_facing_response"))]);
    identity(&mut payload, state, turn_id)?;
    payload.insert("response_sha256".into(), json!(sylvia_base::hash::sha256_hex(narration.as_bytes())));
    payload.insert("response_characters".into(), json!(narration.chars().count()));
    payload.insert("correction_attempt".into(), json!(used));
    payload.insert("max_corrections".into(), json!(maximum));
    evidence(events, format!("{inference_id}-repeated-response-{used}"), payload).await?;
    state
        .locals
        .exchange_messages_mut()?
        .push(Tagged::plain(object(vec![("role", json!("assistant")), ("content", json!(narration))])));
    let text = control_text(
        "repeated_response",
        &[
            ("state.repeated_response_corrections_used", used),
            ("state.max_repeated_response_corrections", maximum),
        ],
    );
    state.locals.current_application_controls.get_mut()?.push(system(text));
    Ok(())
}

/// The `provider_forced_tool_call_missing` evidence, after counting the correction.
async fn forced_tool_call_missing(
    state: &mut ProviderTurnState,
    turn_id: &str,
    events: &mut dyn TurnEvents,
) -> TurnResult<(i64, i64)> {
    let used = *state.locals.missing_required_tool_corrections_used.get()? + 1;
    state.locals.missing_required_tool_corrections_used.set(used);
    let maximum = *state.locals.max_missing_required_tool_corrections.get()?;
    let inference_id = state.locals.inference_id.value()?;
    let mut payload = object(vec![("kind", json!("provider_forced_tool_call_missing"))]);
    identity(&mut payload, state, turn_id)?;
    payload.insert("correction_attempt".into(), json!(used));
    payload.insert("max_corrections".into(), json!(maximum));
    evidence(events, format!("{inference_id}-forced-tool-call-missing"), payload).await?;
    Ok((used, maximum))
}

/// `retry_missing_tool_call(...)`.
pub async fn retry_missing_tool_call(
    state: &mut ProviderTurnState,
    turn_id: &str,
    events: &mut dyn TurnEvents,
) -> TurnResult<()> {
    let (used, maximum) = forced_tool_call_missing(state, turn_id, events).await?;
    let text = control_text(
        "required_tool_call",
        &[
            ("state.missing_required_tool_corrections_used", used),
            ("state.max_missing_required_tool_corrections", maximum),
        ],
    );
    state.locals.required_tool_call_correction_message.set(Some(system(text)));
    Ok(())
}

/// `retry_private_deliberation(...)`: no events.
pub fn retry_private_deliberation(state: &mut ProviderTurnState) -> CodedResult<()> {
    let used = *state.locals.private_deliberation_corrections_used.get()? + 1;
    state.locals.private_deliberation_corrections_used.set(used);
    let narration = state.locals.narration.value()?;
    state
        .locals
        .exchange_messages_mut()?
        .push(Tagged::plain(object(vec![("role", json!("assistant")), ("content", json!(narration))])));
    let maximum = *state.locals.max_private_deliberation_corrections.get()?;
    let text = control_text(
        "private_deliberation",
        &[
            ("state.private_deliberation_corrections_used", used),
            ("state.max_private_deliberation_corrections", maximum),
        ],
    );
    state.locals.private_deliberation_correction_message.set(Some(system(text)));
    Ok(())
}

/// `record_held_narration(...)` and `record_final_narration(...)`: the final response's
/// reference, then the narration spoken.
pub async fn record_final_narration(
    state: &mut ProviderTurnState,
    input: &SessionProviderInput,
    events: &mut dyn TurnEvents,
) -> TurnResult<()> {
    let inference_id = state.locals.inference_id.value()?;
    let mut payload = with_context(state, "provider_final_response_reference")?;
    payload.insert("response_id".into(), json!(inference_id));
    payload.insert("basis_message_id".into(), json!(state.locals.basis_message_id.value()?));
    payload.insert(
        "requirement".into(),
        json!({
            "provider_id": input.session.fields().provider_id,
            "model_id": input.session.fields().model_id,
            "reasoning_required": !state.locals.assistant_reasoning.get()?.is_empty(),
            "chat_completion_required": state.locals.native_chat_metadata.get()?.is_some(),
        }),
    );
    evidence(events, format!("{inference_id}-final-response-reference"), payload).await?;
    let text = state.locals.narration.value()?;
    events.emit(ProviderEvent::Talk { text, response_blocks: None, response_id: None }).await
}

/// `record_held_narration(...)`: the same as [`record_final_narration`].
pub async fn record_held_narration(
    state: &mut ProviderTurnState,
    input: &SessionProviderInput,
    events: &mut dyn TurnEvents,
) -> TurnResult<()> {
    record_final_narration(state, input, events).await
}

/// `retry_missing_campaign_file_update(...)`.
pub async fn retry_missing_campaign_file_update(
    state: &mut ProviderTurnState,
    turn_id: &str,
    events: &mut dyn TurnEvents,
) -> TurnResult<()> {
    let (used, maximum) = forced_tool_call_missing(state, turn_id, events).await?;
    let held = state.locals.buffered_narration.get()?.is_some()
        || state.locals.buffered_raw_response_blocks.get()?.is_some();
    let opening = texts()[if held { "campaign_file_held" } else { "campaign_file_rejected" }]
        .as_str()
        .expect("an exported control");
    let count = control_text(
        "campaign_file_count",
        &[
            ("state.missing_required_tool_corrections_used", used),
            ("state.max_missing_required_tool_corrections", maximum),
        ],
    );
    state.locals.current_application_controls.get_mut()?.push(system(format!("{opening}{count}")));
    Ok(())
}

/// The held draft added to the exchange, then its evidence of `kind`.
async fn held_draft(
    state: &mut ProviderTurnState,
    turn_id: &str,
    events: &mut dyn TurnEvents,
    kind: &str,
    suffix: &str,
) -> TurnResult<()> {
    let draft = assistant_draft(&state.locals.narration.value()?);
    state.locals.exchange_messages_mut()?.push(Tagged::plain(draft.clone()));
    let inference_id = state.locals.inference_id.value()?;
    let mut payload = with_context(state, kind)?;
    identity(&mut payload, state, turn_id)?;
    payload.insert("assistant".into(), Json::Object(draft));
    evidence(events, format!("{inference_id}-{suffix}"), payload).await
}

/// `preserve_boundary_narration(...)`: prose held while a GM-controlled Ruleset decision is
/// still active.
pub async fn preserve_boundary_narration(
    state: &mut ProviderTurnState,
    turn_id: &str,
    events: &mut dyn TurnEvents,
) -> TurnResult<()> {
    held_draft(state, turn_id, events, "provider_gm_boundary_draft", "gm-boundary-draft").await?;
    let text = control_text("ruleset_decision_active", &[]);
    state.locals.current_application_controls.get_mut()?.push(system(text));
    Ok(())
}

/// `preserve_player_intent_narration(...)`: prose held while the player's declared action is
/// still unresolved.
pub async fn preserve_player_intent_narration(
    state: &mut ProviderTurnState,
    turn_id: &str,
    events: &mut dyn TurnEvents,
) -> TurnResult<()> {
    held_draft(state, turn_id, events, "provider_player_intent_pending_draft", "player-intent-pending-draft")
        .await?;
    let text = control_text("player_intent_pending", &[]);
    state.locals.current_application_controls.get_mut()?.push(system(text));
    Ok(())
}

/// `retain_campaign_file_narration(...)`: prose held until the Campaign File disposition.
pub async fn retain_campaign_file_narration(
    state: &mut ProviderTurnState,
    turn_id: &str,
    events: &mut dyn TurnEvents,
) -> TurnResult<()> {
    let narration = state.locals.narration.value()?;
    state.locals.buffered_narration.set(Some(narration));
    state.locals.buffered_response_blocks.set(None);
    state.locals.buffered_raw_response_blocks.set(None);
    state.obligations.buffered_narration_needs_disposition.set(true);
    state.progress.campaign_file_disposition_in_exchange.set(false);
    state.obligations.force_campaign_file_update.set(true);
    held_draft(state, turn_id, events, "provider_held_narration", "held-narration").await
}
