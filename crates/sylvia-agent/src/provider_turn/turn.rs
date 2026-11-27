//! `ProviderSessionAdapter._turn_events` and `_events` (T5a-1): one GM turn's provider work, and
//! the turn's setup phases from `agent/_provider_turn_phases.py`.
//!
//! - **Setup**, every turn: `prepare_turn_recovery` (the turn's messages, context fields and
//!   basis; a redirected or post-mechanics continuation's control; the saved queue results and
//!   narration facts), `restore_saved_turn_progress` (committed actions' intent effects, the
//!   correction budgets, the resumed suffix of the exchange) and `resume_exchange_results` (a
//!   held draft or settled Campaign File result in that suffix).
//! - **The loop:** each round's catalog and request, the stream, then either the prose phases
//!   (T4) or the tool batch (T3b), with the bounded corrections, until the narration is spoken.
//! - **Ending:** any ending but a spoken narration (an error, Stop, the events dropped) asks the
//!   provider once to release what it keeps between requests.
//!
//! A required engine transition and a required actor decision are T6: a turn that reaches one
//! answers `rust_port_pending`. The controls are read from the Python source into
//! `turn_texts.json` by `generate_turn_texts.py`.

use super::decisions::{
    actor_decision_can_be_requested, buffered_narration_is_settled, campaign_file_must_be_settled,
    campaign_file_needs_narration, held_campaign_file_narration_is_needed, narration_needs_private_correction,
    narration_repeats_previous_player_response, required_tool_call_is_missing, required_tool_correction_was_abandoned,
    required_transition_can_run, round_needs_gm_decision, round_needs_player_intent_resolution,
    settled_disposition_still_owes_ooc_acknowledgement, tool_round_can_release_narration, tool_round_needs_correction,
};
use super::narration;
use super::round::{prepare_provider_round, prepare_round_catalog, stream_provider_round};
use super::state::ProviderTurnState;
use super::tool_batch::{execute_tool_batch, prepare_tool_batch, reconcile_tool_batch};
use super::tool_checks::retry_rejected_tool_call;
use super::{TurnEvents, TurnFailure, TurnResult};
use crate::provider_session::campaign_file::{campaign_file_arguments_establish_changes, validated_raw_response_blocks};
use crate::provider_session::context::{
    canonical_player_narration, saved_action_sequence_results, ProviderSessionAdapter,
};
use crate::provider_session::control_texts::preparation_keeps_intent_pending;
use crate::provider_session::narration_facts::{action_sequence_narration_facts, public_action_result};
use crate::provider_session::request::{contains_serialized_tool_call, release_provider_idle_soon};
use crate::provider_session::py_values::{py_iter, require_hashable, require_hashable_frozen};
use crate::provider_session::tagged::Tagged;
use crate::provider_session::texts::{gm_system_contract_version, player_turn_text};
use crate::provider_session::tools::{CAMPAIGN_FILE_REVISED_NARRATION_ARGUMENT, PREPARATION_INTENT_ARGUMENT};
use crate::session_loop::{ProviderEvent, SessionProviderInput};
use serde_json::{json, Value as Json};
use std::sync::{Arc, OnceLock};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{CodedError, CodedResult, JsonMap};
use sylvia_domain::authority::{
    validate_response_blocks, CanonicalEventType, ModelDecisionOwner, ModelToolBehavior, WriterRole,
};
use sylvia_domain::pyops::{py_eq, py_str, py_type_name};
use sylvia_domain::session::{SessionMessage, SessionMessageKind};
use sylvia_provider::factory::PORT_PENDING;
use sylvia_provider::Provider;

fn texts() -> &'static Json {
    static TEXTS: OnceLock<Json> = OnceLock::new();
    TEXTS.get_or_init(|| serde_json::from_str(include_str!("turn_texts.json")).expect("exported texts are JSON"))
}

fn text(key: &str) -> &'static str {
    texts()[key].as_str().expect("an exported control")
}

fn rejected(message: &str, code: &str) -> CodedError {
    CodedError::new(code, message).with_error_type("CommandRejected")
}

fn object(pairs: Vec<(&str, Json)>) -> JsonMap {
    pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect()
}

fn system(content: String) -> Tagged {
    Tagged::plain(object(vec![("role", json!("system")), ("content", json!(content))]))
}

/// `message.content.get("kind") == kind` for an evidence message.
fn evidence_of(message: &SessionMessage, kind: &str) -> bool {
    message.kind() == SessionMessageKind::Evidence && message.content().get("kind") == Some(&json!(kind))
}

/// A turn that reaches T6's work, not ported yet.
fn pending(method: &str) -> TurnFailure {
    TurnFailure::Coded(CodedError::new(
        PORT_PENDING,
        format!("The Rust owner does not run agent/_provider_turn_phases.py {method} yet."),
    ))
}

/// `retry_empty_completion(...)`: no events.
pub fn retry_empty_completion(state: &mut ProviderTurnState) -> CodedResult<()> {
    let used = *state.locals.empty_completion_corrections_used.get()? + 1;
    state.locals.empty_completion_corrections_used.set(used);
    let required_output =
        text(if *state.locals.provider_tool_call_required.get()? { "empty_required_tool" } else { "empty_response" });
    let content = text("empty_completion")
        .replace("{state.finish_reason}", &state.locals.finish_reason.value()?)
        .replace("{required_output}", required_output)
        .replace("{state.empty_completion_corrections_used}", &used.to_string())
        .replace(
            "{state.max_empty_completion_corrections}",
            &state.locals.max_empty_completion_corrections.get()?.to_string(),
        );
    state.locals.current_application_controls.get_mut()?.push(system(content).message);
    Ok(())
}

/// `_saved_round_narration_facts(provider_input, basis_message_id)`: the exact facts this saved
/// GM input's live rounds committed, kept beside their tool results.
fn saved_round_narration_facts(input: &SessionProviderInput, basis_message_id: Option<&str>) -> Vec<JsonMap> {
    let Some(basis) = basis_message_id.filter(|_| input.session.role() == WriterRole::Dm) else { return Vec::new() };
    let mut facts = Vec::new();
    for message in &input.history.messages {
        let content = message.content();
        if !evidence_of(message, "provider_tool_results") || content.get("basis_message_id") != Some(&json!(basis)) {
            continue;
        }
        if let Some(Json::Array(saved)) = content.get("narration_facts") {
            facts.extend(saved.iter().filter_map(Json::as_object).cloned());
        }
    }
    facts
}

/// `{action.get("action_id") for fact in facts for action in fact.get("committed_actions", ())}`.
fn narrated_action_ids(facts: &[JsonMap]) -> CodedResult<Vec<Json>> {
    let mut narrated = Vec::new();
    for fact in facts {
        let Some(actions) = fact.get("committed_actions") else { continue };
        for action in py_iter(actions)? {
            let Json::Object(action) = action else {
                return Err(CodedError::new(
                    "internal_error",
                    format!("'{}' object has no attribute 'get'", py_type_name(&action)),
                )
                .with_error_type("AttributeError"));
            };
            let id = action.get("action_id").cloned().unwrap_or(Json::Null);
            require_hashable(&id)?;
            narrated.push(id);
        }
    }
    Ok(narrated)
}

/// `set(result.get("committed_action_ids", ())) <= narrated`, with Python's equality.
fn already_narrated(ids: Option<&Json>, narrated: &[Json]) -> CodedResult<bool> {
    let ids = match ids {
        None => Vec::new(),
        Some(ids) => py_iter(ids)?,
    };
    for id in &ids {
        require_hashable(id)?;
    }
    Ok(ids.iter().all(|id| narrated.iter().any(|seen| py_eq(id, seen))))
}

/// `prepare_turn_recovery(...)`: the turn's messages, context and basis.
pub async fn prepare_turn_recovery(
    adapter: &ProviderSessionAdapter,
    state: &mut ProviderTurnState,
    input: &SessionProviderInput,
    events: &mut dyn TurnEvents,
) -> TurnResult<()> {
    let Some(campaign_id) = input.session.campaign_id().map(str::to_owned) else {
        return Err(rejected("A DM provider turn requires a campaign", "command_rejected").into());
    };
    state.locals.campaign_id.set(campaign_id.clone());
    let owner = adapter.turn_owner()?;
    let narration = canonical_player_narration(adapter.reads.as_ref(), &campaign_id).await?;
    state.locals.canonical_player_narration.set(narration.clone());
    let messages = adapter.messages_with(input, adapter.ranker()?, Some(narration)).await?;
    let contract = messages[0].message.get("content").cloned().unwrap_or(Json::Null);
    state.locals.messages.set(messages);
    let context_owner = adapter.gm_context_owner(input).await?;
    let mut context_fields = JsonMap::new();
    if let Some(context_owner) = &context_owner {
        context_fields.insert("context_owner".into(), Json::Object(context_owner.clone()));
    }
    state.locals.context_owner.set(context_owner);
    state.locals.context_fields.set(context_fields);
    let history = &input.history.messages;
    let mut basis = input.basis_message_id.clone();
    let persisted_system_contract = history.iter().any(|message| evidence_of(message, "gm_system_contract"));
    if basis.is_some() && !persisted_system_contract {
        let payload = object(vec![
            ("kind", json!("gm_system_contract")),
            ("version", json!(gm_system_contract_version())),
            ("contract", contract),
        ]);
        let message_id = format!("gm-system-contract-{}", input.session.session_id());
        events.emit(ProviderEvent::Evidence { message_id, payload }).await?;
    }
    if basis.is_none() {
        basis = history
            .iter()
            .rev()
            .find(|message| {
                message.kind() == SessionMessageKind::User
                    || evidence_of(message, "campaign_start")
                    || evidence_of(message, "player_action_response")
            })
            .map(|message| message.message_id().to_owned());
    }
    state.locals.basis_message_id.set(basis.clone());
    let direct_response = history.iter().any(|message| {
        basis.as_deref() == Some(message.message_id()) && evidence_of(message, "player_action_response")
    });
    state.locals.direct_response.set(direct_response);
    let post_mechanics = input.recovery_kind.as_deref() == Some("post_mechanics_narration");
    state.locals.post_mechanics_recovery.set(post_mechanics);
    let redirected = history.iter().find(|message| basis.as_deref() == Some(message.message_id()));
    if let Some(redirected) = redirected
        .filter(|message| message.content().get("redirect_interruption_id").is_some_and(sylvia_domain::pyops::py_truthy))
    {
        let start = redirected.content().get("canonical_start_sequence").cloned().unwrap_or(json!(0));
        // `isinstance(start_sequence, int)`: a boolean is one too.
        let start = match start {
            Json::Bool(flag) => Some(i64::from(flag)),
            Json::Number(number) => number.as_i64(),
            _ => None,
        };
        let saved_events = owner.snapshot(&campaign_id).await?.events;
        let saved_action_ids: Vec<Option<String>> = saved_events
            .iter()
            .filter(|event| {
                start.is_some_and(|start| event.sequence > start)
                    && event.event_type == CanonicalEventType::ActionCommitted
            })
            .map(|event| event.action_id.clone())
            .collect();
        let mut saved_results = Vec::new();
        for event in &saved_events {
            if event.event_type != CanonicalEventType::ToolResult || !saved_action_ids.contains(&event.action_id) {
                continue;
            }
            let mut tool_result = event.safe_payload.clone();
            let result = event.safe_payload.get("result").cloned().unwrap_or(Json::Null);
            tool_result.insert("result".into(), public_action_result(event.action_id.as_deref(), &result)?);
            saved_results.push(json!({"action_id": event.action_id, "tool_result": tool_result}));
        }
        let facts = pyjson::dumps(&Json::Array(saved_results), Dump::WIRE.ascii(false));
        state.locals.messages.get_mut()?.push(system(format!("{}{facts}", text("redirected_continuation"))));
    }
    if post_mechanics {
        let Some(receipt) = &input.retry_action_receipt else {
            return Err(rejected(
                "Post-mechanics recovery requires its committed action receipt",
                "retry_action_receipt_unavailable",
            )
            .into());
        };
        let facts = json!({
            "campaign_id": receipt.campaign_id,
            "action_id": receipt.action_id,
            "status": receipt.status.as_str(),
            "result": public_action_result(Some(&receipt.action_id), &Json::Object(receipt.result.clone()))?,
            "resulting_revision": receipt.resulting_revision,
        });
        let facts = pyjson::dumps(&facts, Dump::CANONICAL);
        state.locals.messages.get_mut()?.push(system(format!("{}{facts}", text("post_mechanics_recovery"))));
    }
    let opening_request = !direct_response
        && history.iter().any(|message| evidence_of(message, "campaign_start"))
        && !history.iter().any(|message| matches!(message.kind(), SessionMessageKind::User | SessionMessageKind::Talk));
    state.locals.opening_request.set(opening_request);
    state.locals.participants.set(owner.snapshot(&campaign_id).await?.state.participants);
    let footing = if opening_request { adapter.opening_footing_payload(input).await?.as_object().cloned() } else { None };
    state.locals.opening_footing.set(footing);
    let current = if direct_response {
        None
    } else {
        history.iter().rev().find(|message| message.kind() == SessionMessageKind::User).cloned()
    };
    state
        .locals
        .current_player_action
        .set(current.as_ref().map(|message| message.content().get("text").map_or_else(String::new, py_str)));
    state.locals.current_user_wire_text.set(current.as_ref().map(|message| player_turn_text(message.content())));
    state.locals.current_player_message.set(current);
    let sequence_results = saved_action_sequence_results(input, adapter.reads.as_ref()).await?;
    state.locals.saved_sequence_results.set(sequence_results.clone());
    // The live rounds' facts are assigned first: a saved queue's facts extend them.
    state.locals.pending_narration_facts.set(saved_round_narration_facts(input, basis.as_deref()));
    let narrated = narrated_action_ids(state.locals.pending_narration_facts.get()?)?;
    for result in &sequence_results {
        if already_narrated(result.get("committed_action_ids"), &narrated)? {
            continue;
        }
        if let Some(facts) = action_sequence_narration_facts(owner, &campaign_id, result).await? {
            state.locals.pending_narration_facts.get_mut()?.push(facts);
        }
    }
    state.obligations.provisional_tool_prose_pending.set(false);
    state.obligations.player_intent_pending.set(false);
    let committed = owner
        .snapshot(&campaign_id)
        .await?
        .events
        .into_iter()
        .filter(|event| event.event_type == CanonicalEventType::ActionCommitted)
        .filter_map(|event| event.action_id)
        .collect();
    state.locals.committed_action_ids.set(committed);
    Ok(())
}

/// `_persisted_campaign_file_mutation_attempted(messages)`: whether same-turn evidence holds a
/// Campaign File call that records a change.
fn persisted_campaign_file_mutation_attempted(messages: &[Tagged]) -> bool {
    messages.iter().any(|message| {
        let Some(Json::Array(calls)) = message.message.get("tool_calls") else { return false };
        calls.iter().filter_map(Json::as_object).any(|call| {
            let Some(Json::Object(function)) = call.get("function") else { return false };
            if !function.get("name").and_then(Json::as_str).is_some_and(is_campaign_file_tool) {
                return false;
            }
            let raw = function.get("arguments").map_or_else(|| "{}".to_owned(), py_str);
            matches!(sylvia_base::pyjson::loads(&raw), Ok(Json::Object(arguments)) if campaign_file_arguments_establish_changes(&arguments))
        })
    })
}

/// `name == "update_campaign_file" or name.endswith("_update_campaign_file")`.
fn is_campaign_file_tool(name: &str) -> bool {
    name == "update_campaign_file" || name.ends_with("_update_campaign_file")
}

/// `restore_saved_turn_progress(...)`: committed actions' intent effects, the correction
/// budgets and the resumed suffix of the exchange.
pub fn restore_saved_turn_progress(state: &mut ProviderTurnState, input: &SessionProviderInput) -> CodedResult<()> {
    let invalid = |message: &str| rejected(message, "provider_tool_evidence_invalid");
    let basis = state.locals.basis_message_id.value()?;
    for message in &input.history.messages {
        if message.kind() != SessionMessageKind::Evidence {
            continue;
        }
        let contract = message.content();
        if contract.get("kind") != Some(&json!("provider_mechanics_action_contract"))
            || contract.get("basis_message_id").cloned().unwrap_or(Json::Null) != json!(basis)
        {
            continue;
        }
        let action_id = contract.get("action_id").and_then(Json::as_str).filter(|id| !id.is_empty());
        let raw_behavior = contract.get("model_behavior").and_then(Json::as_str);
        let decision_owner = contract.get("decision_owner").cloned().unwrap_or(Json::Null);
        let (Some(action_id), Some(raw_behavior)) = (action_id, raw_behavior) else {
            return Err(invalid("Persisted mechanics action contract is invalid"));
        };
        // `decision_owner not in {None, *owners}`: hashed only once the id and behavior passed.
        require_hashable_frozen(&decision_owner)?;
        let owner_known = decision_owner.is_null()
            || decision_owner.as_str().is_some_and(|owner| ModelDecisionOwner::parse(owner).is_some());
        if !owner_known {
            return Err(invalid("Persisted mechanics action contract is invalid"));
        }
        if !state.locals.committed_action_ids.get()?.contains(action_id) {
            continue;
        }
        let behavior = ModelToolBehavior::parse(raw_behavior)
            .ok_or_else(|| invalid("Persisted mechanics action behavior is invalid"))?;
        state.locals.behavior.set(behavior);
        if behavior == ModelToolBehavior::LeavesPlayerIntentPending {
            let pending = *state.obligations.player_intent_pending.get()?;
            let kept = preparation_keeps_intent_pending(contract.get(PREPARATION_INTENT_ARGUMENT), pending)
                .map_err(|_| invalid("Persisted preparation scope is invalid"))?;
            state.obligations.player_intent_pending.set(kept);
        } else if behavior == ModelToolBehavior::ResolvesPlayerIntent
            && decision_owner.as_str() == Some(ModelDecisionOwner::Player.as_str())
        {
            state.obligations.player_intent_pending.set(false);
        }
    }
    state.locals.buffered_narration.set(None);
    state.locals.buffered_response_blocks.set(None);
    state.locals.buffered_raw_response_blocks.set(None);
    let ooc_ack_required = state.locals.current_player_message.get()?.as_ref().is_some_and(|message| {
        message.content().get("ooc_text").and_then(Json::as_str).is_some_and(|ooc| !sylvia_base::pystr::strip(ooc).is_empty())
    });
    state.locals.ooc_ack_required.set(ooc_ack_required);
    let action_blank = state
        .locals
        .current_player_action
        .get()?
        .as_deref()
        .is_none_or(|action| sylvia_base::pystr::strip(action).is_empty());
    let participant_ooc_only = state.locals.context_owner.get()?.is_some() && ooc_ack_required && action_blank;
    state.locals.participant_ooc_only.set(participant_ooc_only);
    state.obligations.buffered_narration_needs_disposition.set(false);
    let any_commit = *state.locals.post_mechanics_recovery.get()? || *state.locals.direct_response.get()?;
    state.progress.any_commit_in_exchange.set(any_commit);
    state.progress.campaign_file_commit_in_exchange.set(false);
    state.progress.campaign_file_disposition_in_exchange.set(false);
    state.obligations.campaign_file_narration_pending.set(false);
    state.locals.missing_required_tool_corrections_used.set(0);
    state.locals.max_missing_required_tool_corrections.set(3);
    state.locals.rejected_tool_corrections_used.set(0);
    state.locals.max_rejected_tool_corrections.set(5);
    state.locals.empty_completion_corrections_used.set(0);
    state.locals.max_empty_completion_corrections.set(2);
    state.locals.private_deliberation_corrections_used.set(0);
    state.locals.max_private_deliberation_corrections.set(2);
    state.locals.private_deliberation_correction_message.set(None);
    state.locals.required_tool_call_correction_message.set(None);
    state.locals.current_application_controls.set(Vec::new());
    state.locals.repeated_response_corrections_used.set(0);
    state.locals.max_repeated_response_corrections.set(2);
    state.obligations.pending_failed_tool_call.set(false);
    state.obligations.force_campaign_file_update.set(false);
    // Native history before the current task is completed dialogue; only the persisted
    // same-turn suffix after it may resume an unsettled draft or tool result.
    let messages = state.locals.messages.get()?;
    let wire_text = state.locals.current_user_wire_text.value()?;
    let mut task_index = messages
        .iter()
        .enumerate()
        .filter(|(_, message)| {
            message.message.get("role") == Some(&json!("user"))
                && wire_text.as_ref().is_some_and(|text| message.message.get("content") == Some(&json!(text)))
        })
        .map(|(index, _)| index)
        .max()
        // `messages` always starts with the system contract (`prepare_turn_recovery` read it).
        .unwrap_or(messages.len() - 1);
    let starts = |message: &Tagged, prefix: &str| {
        message.message.get("role") == Some(&json!("system"))
            && message.message.get("content").map_or_else(String::new, py_str).starts_with(prefix)
    };
    // `next(...)` with no match raises `StopIteration` inside the `_events` generator, which
    // Python turns into this `RuntimeError` (PEP 479).
    let stop = || CodedError::new("internal_error", "generator raised StopIteration").with_error_type("RuntimeError");
    if *state.locals.opening_request.get()? {
        task_index = messages.iter().position(|message| starts(message, "SYLVIA_GM_OPENING_TASK ")).ok_or_else(stop)?;
        if task_index + 1 < messages.len() && messages[task_index + 1].message.get("role") == Some(&json!("user")) {
            task_index += 1;
        }
    } else if *state.locals.direct_response.get()? {
        task_index = messages
            .iter()
            .position(|message| starts(message, "SYLVIA_PLAYER_ACTION_RESPONSE_TASK "))
            .ok_or_else(stop)?;
    }
    let suffix: Vec<Tagged> = messages[task_index + 1..].to_vec();
    let prefix: Vec<Tagged> = messages[..task_index].to_vec();
    state.locals.campaign_file_mutation_attempted.set(persisted_campaign_file_mutation_attempted(&suffix));
    let player_actions: Vec<String> = input
        .history
        .messages
        .iter()
        .filter(|message| message.kind() == SessionMessageKind::User)
        .map(|message| message.content().get("text").map_or_else(String::new, py_str))
        .collect();
    state
        .locals
        .previous_player_action
        .set((player_actions.len() >= 2).then(|| player_actions[player_actions.len() - 2].clone()));
    let mut previous = state
        .locals
        .canonical_player_narration
        .get()?
        .iter()
        .rev()
        .find_map(|record| record.get("text").and_then(Json::as_str).filter(|text| !text.is_empty()).map(str::to_owned));
    if previous.is_none() {
        previous = prefix.iter().rev().find_map(|message| {
            let content = message.message.get("content").and_then(Json::as_str).filter(|text| !text.is_empty())?;
            let calls = message.message.get("tool_calls").is_some_and(sylvia_domain::pyops::py_truthy);
            (message.message.get("role") == Some(&json!("assistant")) && !calls).then(|| content.to_owned())
        });
    }
    state.locals.previous_player_facing_response.set(previous);
    state.locals.resumed_messages.set(suffix);
    Ok(())
}

/// `resume_exchange_results(...)`: a held draft or a settled Campaign File result in the
/// resumed suffix of the exchange.
pub fn resume_exchange_results(state: &mut ProviderTurnState) -> CodedResult<()> {
    let invalid = |message: &str| rejected(message, "provider_tool_evidence_invalid");
    let resumed = state.locals.resumed_messages.get()?.clone();
    for message in resumed.iter().map(|tagged| &tagged.message) {
        if message.get("role") == Some(&json!("assistant")) {
            if let Some(Json::String(content)) = message.get("content").filter(|content| content != &&json!("")) {
                state.locals.buffered_narration.set(Some(content.clone()));
                state.locals.buffered_response_blocks.set(None);
                state.locals.buffered_raw_response_blocks.set(None);
                state.obligations.buffered_narration_needs_disposition.set(true);
            }
            let raw_calls = message.get("tool_calls");
            if let Some(Json::Array(calls)) = raw_calls {
                for call in calls {
                    let Some(Json::Object(function)) = call.as_object().and_then(|call| call.get("function")) else { continue };
                    if !function.get("name").and_then(Json::as_str).is_some_and(is_campaign_file_tool) {
                        continue;
                    }
                    // `except (json.JSONDecodeError, ValueError, AttributeError): pass`.
                    let raw = function.get("arguments").map_or_else(|| "{}".to_owned(), py_str);
                    let Ok(arguments) = sylvia_base::pyjson::loads(&raw) else { continue };
                    let Some(arguments) = arguments.as_object() else { continue };
                    match arguments.get(CAMPAIGN_FILE_REVISED_NARRATION_ARGUMENT) {
                        Some(blocks @ Json::Array(_)) => {
                            let Ok(blocks) = validated_raw_response_blocks(blocks, true) else { continue };
                            state.locals.buffered_raw_response_blocks.set(Some(blocks));
                            state.locals.buffered_narration.set(None);
                            state.locals.buffered_response_blocks.set(None);
                            state.obligations.buffered_narration_needs_disposition.set(true);
                        }
                        Some(Json::String(text))
                            if !sylvia_base::pystr::strip(text).is_empty() && !contains_serialized_tool_call(text) =>
                        {
                            state.locals.buffered_narration.set(Some(text.clone()));
                            state.locals.buffered_response_blocks.set(None);
                            state.locals.buffered_raw_response_blocks.set(None);
                            state.obligations.buffered_narration_needs_disposition.set(true);
                        }
                        _ => {}
                    }
                }
            }
            if raw_calls.is_some_and(sylvia_domain::pyops::py_truthy) {
                state.obligations.pending_failed_tool_call.set(false);
            }
        }
        if message.get("role") != Some(&json!("tool")) {
            continue;
        }
        let raw = message.get("content").map_or_else(|| "{}".to_owned(), py_str);
        let Ok(Json::Object(result)) = sylvia_base::pyjson::loads(&raw) else { continue };
        let status = result.get("status").cloned().unwrap_or(Json::Null);
        // `status in {...}`: an object or a list cannot be looked up.
        require_hashable(&status)?;
        let status = status.as_str();
        let optional = result.get("correction_optional").is_some_and(sylvia_domain::pyops::py_truthy);
        if matches!(status, Some("rejected" | "action_failed")) && !optional {
            state.obligations.pending_failed_tool_call.set(true);
        }
        if matches!(status, Some("campaign_file_updated" | "action_committed")) {
            state.progress.any_commit_in_exchange.set(true);
        }
        if let Some(settled @ ("campaign_file_updated" | "campaign_file_unchanged")) = status {
            let blocks = match result.get("response_blocks") {
                None | Some(Json::Null) => None,
                Some(blocks) => Some(
                    validate_response_blocks(blocks)
                        .map_err(|_| invalid("Persisted Campaign File response blocks are invalid"))?,
                ),
            };
            let had_blocks = blocks.is_some();
            state.locals.buffered_response_blocks.set(blocks);
            match result.get("player_facing_response") {
                None | Some(Json::Null) => {}
                Some(Json::String(text)) if !sylvia_base::pystr::strip(text).is_empty() => {
                    state.locals.buffered_narration.set(Some(text.clone()));
                    if !had_blocks {
                        state.locals.buffered_raw_response_blocks.set(None);
                    }
                }
                Some(_) => return Err(invalid("Persisted Campaign File disposition narration is invalid")),
            }
            if settled == "campaign_file_updated" {
                state.progress.campaign_file_commit_in_exchange.set(true);
            }
            state.progress.campaign_file_disposition_in_exchange.set(true);
            if state.locals.buffered_narration.get()?.is_some() {
                state.obligations.buffered_narration_needs_disposition.set(false);
                state.obligations.campaign_file_narration_pending.set(false);
            } else {
                state.obligations.campaign_file_narration_pending.set(true);
            }
        }
        if status == Some("action_committed") {
            state.progress.campaign_file_disposition_in_exchange.set(false);
            state.obligations.campaign_file_narration_pending.set(false);
        }
        if status == Some("action_committed")
            || !matches!(status, Some("rejected" | "action_failed" | "campaign_file_updated" | "campaign_file_unchanged"))
        {
            state.locals.buffered_narration.set(None);
            state.locals.buffered_response_blocks.set(None);
            state.locals.buffered_raw_response_blocks.set(None);
            state.obligations.buffered_narration_needs_disposition.set(false);
        }
    }
    if settled_disposition_still_owes_ooc_acknowledgement(state)? {
        return Err(invalid("Persisted OOC turn response omits its acknowledgement"));
    }
    let held = state.locals.buffered_narration.get()?.is_some() || state.locals.buffered_raw_response_blocks.get()?.is_some();
    let force = held && *state.obligations.buffered_narration_needs_disposition.get()?;
    state.obligations.force_campaign_file_update.set(force);
    Ok(())
}

/// Asks the provider to release its between-request state unless the turn ended narrated.
struct ReleaseUnlessNarrated(Option<Arc<dyn Provider>>);

impl Drop for ReleaseUnlessNarrated {
    fn drop(&mut self) {
        if let Some(provider) = self.0.take() {
            release_provider_idle_soon(provider);
        }
    }
}

/// `_turn_events(provider_input, turn_id=...)`: one turn's provider work; any ending but a
/// returned turn (an error, Stop, the events dropped) releases the provider once.
pub async fn turn_events(
    adapter: &Arc<ProviderSessionAdapter>,
    input: &SessionProviderInput,
    turn_id: &str,
    events: &mut dyn TurnEvents,
) -> TurnResult<()> {
    let mut release = ReleaseUnlessNarrated(Some(Arc::clone(&adapter.provider)));
    let outcome = provider_events(adapter, input, turn_id, events).await;
    if outcome.is_ok() {
        release.0 = None;
    }
    outcome
}

/// `{"paused", "failed", "stale"}` membership; an unhashable status raises `TypeError`.
fn stopped_status(status: Option<&Json>) -> CodedResult<bool> {
    let Some(status) = status else { return Ok(false) };
    require_hashable(status)?;
    Ok(matches!(status.as_str(), Some("paused" | "failed" | "stale")))
}

/// `_events(provider_input, turn_id=...)`.
async fn provider_events(
    adapter: &Arc<ProviderSessionAdapter>,
    input: &SessionProviderInput,
    turn_id: &str,
    events: &mut dyn TurnEvents,
) -> TurnResult<()> {
    let mut state = ProviderTurnState::default();
    let state = &mut state;
    prepare_turn_recovery(adapter, state, input, events).await?;
    restore_saved_turn_progress(state, input)?;
    resume_exchange_results(state)?;
    if buffered_narration_is_settled(state)? {
        let text = state.locals.buffered_narration.value()?.unwrap_or_default();
        let response_blocks = state.locals.buffered_response_blocks.value()?;
        return events.emit(ProviderEvent::Talk { text, response_blocks, response_id: None }).await;
    }
    let names = state
        .locals
        .participants
        .get()?
        .iter()
        .filter_map(|participant| {
            let name = participant.data().get("name").filter(|name| !name.is_null())?;
            Some((participant.participant_id().to_owned(), py_str(name)))
        })
        .collect();
    state.locals.entity_display_names.set(names);
    // Useful tool work continues until completion or cancellation; engine bookkeeping does not
    // consume a model request, and there is no arbitrary total-round cutoff.
    state.locals.provider_round.set(0);
    state.obligations.engine_transition_review_pending.set(false);
    let mut stopped = false;
    for result in state.locals.saved_sequence_results.get()? {
        if stopped_status(result.get("status"))? || result.get("provider_execution_stopped") == Some(&Json::Bool(true)) {
            stopped = true;
            break;
        }
    }
    state.progress.sequence_execution_stopped.set(stopped);
    loop {
        prepare_round_catalog(adapter, state, input, turn_id).await?;
        if required_transition_can_run(state)? {
            return Err(pending("execute_required_transition"));
        }
        if actor_decision_can_be_requested(state)? {
            // Read only after required bookkeeping has drained and refreshed.
            let campaign_id = state.locals.campaign_id.value()?;
            let request = adapter.turn_owner()?.actor_decision_request(&campaign_id, turn_id).await?;
            let requested = request.is_some();
            state.locals.actor_request.set(request);
            if requested {
                return Err(pending("run_required_actor_decision"));
            }
        }
        prepare_provider_round(adapter, state, input, turn_id).await?;
        stream_provider_round(adapter, state, input, turn_id, events).await?;
        if state.locals.assemblies.get()?.is_empty() {
            state.locals.narration.set(state.locals.assistant_text.get()?.concat());
            if held_campaign_file_narration_is_needed(state)? {
                // The decision has just seen a held narration.
                state.locals.narration.set(state.locals.buffered_narration.value()?.unwrap_or_default());
            }
            let narration = state.locals.narration.value()?;
            if contains_serialized_tool_call(&narration) {
                return Err(rejected("Provider returned serialized tool syntax as narration", "provider_serialized_tool_call").into());
            }
            if narration.is_empty() {
                narration::record_empty_completion(state, turn_id, events).await?;
                if *state.locals.empty_completion_corrections_used.get()? < *state.locals.max_empty_completion_corrections.get()? {
                    retry_empty_completion(state)?;
                    continue;
                }
                return Err(rejected(
                    "Provider completed without player-visible narration or a tool call",
                    "provider_empty_completion",
                )
                .into());
            }
            state.locals.empty_completion_corrections_used.set(0);
            if narration_repeats_previous_player_response(state)? {
                if *state.locals.repeated_response_corrections_used.get()? < *state.locals.max_repeated_response_corrections.get()? {
                    narration::retry_repeated_response(state, turn_id, events).await?;
                    continue;
                }
                return Err(rejected(
                    "Provider repeatedly returned the previous player-facing response",
                    "provider_repeated_player_facing_response",
                )
                .into());
            }
            if required_tool_call_is_missing(state)? {
                if *state.locals.missing_required_tool_corrections_used.get()?
                    < *state.locals.max_missing_required_tool_corrections.get()?
                {
                    narration::retry_missing_tool_call(state, turn_id, events).await?;
                    continue;
                }
                return Err(rejected(
                    "Provider repeatedly returned prose where a tool call was required",
                    "provider_required_tool_call_missing",
                )
                .into());
            }
            if narration_needs_private_correction(state)? {
                if *state.locals.private_deliberation_corrections_used.get()?
                    < *state.locals.max_private_deliberation_corrections.get()?
                {
                    narration::retry_private_deliberation(state)?;
                    continue;
                }
                let filtered = adapter.filter_private_deliberation(&state.locals.narration.value()?);
                state.locals.narration.set(filtered.clone());
                if filtered.is_empty() {
                    return Err(rejected("Provider completed without player-visible narration", "provider_empty_completion").into());
                }
            }
            if campaign_file_needs_narration(state)? {
                return narration::record_held_narration(state, input, events).await;
            }
            if campaign_file_must_be_settled(state)? {
                if *state.locals.missing_required_tool_corrections_used.get()?
                    < *state.locals.max_missing_required_tool_corrections.get()?
                {
                    narration::retry_missing_campaign_file_update(state, turn_id, events).await?;
                    continue;
                }
                return Err(rejected("Provider completed without settling the Campaign File", "campaign_file_disposition_required").into());
            }
            if required_tool_correction_was_abandoned(state)? {
                return Err(rejected("Provider abandoned a required tool-call correction", "provider_tool_correction_abandoned").into());
            }
            if round_needs_gm_decision(state)? {
                narration::preserve_boundary_narration(state, turn_id, events).await?;
                continue;
            }
            if round_needs_player_intent_resolution(state)? {
                narration::preserve_player_intent_narration(state, turn_id, events).await?;
                continue;
            }
            if *state.locals.campaign_file_disposition_supported.get()? {
                narration::retain_campaign_file_narration(state, turn_id, events).await?;
                continue;
            }
            return narration::record_final_narration(state, input, events).await;
        }
        prepare_tool_batch(adapter, state, input, turn_id, events).await?;
        execute_tool_batch(adapter, state, input, turn_id, events).await?;
        reconcile_tool_batch(adapter, state, input, turn_id, events).await?;
        if tool_round_needs_correction(state)? {
            input.control.progress.expect_correction();
            if *state.locals.round_has_non_narrative_call.get()? {
                state.locals.buffered_narration.set(None);
                state.locals.buffered_response_blocks.set(None);
                state.locals.buffered_raw_response_blocks.set(None);
                state.obligations.buffered_narration_needs_disposition.set(false);
            } else if state.locals.buffered_narration.get()?.is_some()
                || state.locals.buffered_raw_response_blocks.get()?.is_some()
            {
                state.obligations.force_campaign_file_update.set(true);
            }
            if state.locals.required_tool_failures.get()?.is_empty() {
                state.obligations.pending_failed_tool_call.set(false);
                let control = system(text("optional_reference_recovery").to_owned()).message;
                state.locals.current_application_controls.get_mut()?.push(control);
                continue;
            }
            state.obligations.pending_failed_tool_call.set(true);
            if *state.locals.rejected_tool_corrections_used.get()? < *state.locals.max_rejected_tool_corrections.get()? {
                retry_rejected_tool_call(state)?;
                continue;
            }
            let failures = state.locals.local_tool_failures.get()?;
            let results = state.locals.local_tool_results.get()?;
            let failed = state
                .locals
                .ordered
                .get()?
                .iter()
                .find(|assembly| failures.contains(&assembly.call_id))
                .and_then(|assembly| results.get(&assembly.call_id));
            let code = failed.and_then(|result| result.get("code")).map_or_else(|| "mechanics_rejected".to_owned(), py_str);
            return Err(rejected("The provider tool call remained invalid after bounded correction", &code).into());
        }
        if tool_round_can_release_narration(state)? {
            let text = state.locals.buffered_narration.value()?.unwrap_or_default();
            let response_blocks = state.locals.buffered_response_blocks.value()?;
            return events.emit(ProviderEvent::Talk { text, response_blocks, response_id: None }).await;
        }
        state.obligations.pending_failed_tool_call.set(false);
    }
}
