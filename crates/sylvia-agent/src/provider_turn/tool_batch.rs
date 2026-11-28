//! `agent/_provider_turn_phases.py`, one round's tool batch (T3b-1): its calls admitted and
//! recorded (`prepare_tool_batch`), then each one checked and run (`execute_tool_batch`): a
//! history or argument read, a reference read or knowledge search, an action-sequence operation
//! (`execute_action_sequence_call`), the Campaign File disposition, or a mechanic, which is
//! handed to the session loop as an action (`submit_mechanics_call`). Reconciling the batch's
//! results is T3b-2.
//!
//! Legacy routes only, as [`super::tool_checks`]: the fixed tool list and the tool loader act
//! only in context-ledger mode.

use super::state::{ProviderTurnState, ToolCallAssembly};
use super::tool_checks::{
    legacy_route, load_arguments, prepare_campaign_file_revision, read_selected_arguments,
    record_campaign_file_rejection, record_missing_tool_fields, record_unchanged_campaign_file,
    record_updated_campaign_file, refuse_unread_mechanic, reject_invalid_revision, reject_invalid_tool_json,
    reject_ruleless_mechanics, reject_unavailable_tool, restore_unchanged_narration,
    settle_campaign_file_narration, unwrap_loader_calls, unwrap_selected_arguments,
    validate_revision_speakers,
};
use super::{TurnEvents, TurnResult};
use crate::argument_contracts::ARGUMENTS_TOOL_NAME;
use crate::native_continuation::attach_chat_metadata;
use crate::provider_session::arguments::{
    derive_mechanics_parameters, missing_required_arguments, normalize_state_references, DerivationError,
};
use crate::provider_session::campaign_file::{
    campaign_file_arguments_establish_changes, current_referenced_entities, project_response_blocks,
};
use crate::provider_session::context::{
    is_command_rejected, persisted_selected_party_roles, ProviderSessionAdapter,
};
use crate::provider_session::control_texts::preparation_keeps_intent_pending;
use crate::provider_session::narration_facts::{action_sequence_narration_facts, mechanics_narration_facts};
use crate::provider_session::request::contains_serialized_tool_call;
use crate::provider_session::tagged::{Tagged, LEDGER_ENTRY_KEY};
use crate::provider_session::tools::{resolve_provider_tool_alias, PREPARATION_INTENT_ARGUMENT};
use crate::provider_turn::decisions::{
    buffered_narration_can_be_replaced, committed_tool_resolves_player_boundary,
    tool_call_needs_fresh_catalog,
};
use crate::provider_turn::round::current_combat_state;
use crate::session_loop::{ProviderActionFeedback, ProviderEvent, SessionProviderInput};
use crate::stateless_mcp::{call_action_sequence_tool, safe_error, AuthorityFailure};
use serde_json::{json, Value as Json};
use std::sync::OnceLock;
use sylvia_base::{pystr, CodedError, CodedResult, JsonMap};
use sylvia_domain::authority::{ActionCommand, CanonicalEventType, ModelToolBehavior};

fn texts() -> &'static Json {
    static TEXTS: OnceLock<Json> = OnceLock::new();
    TEXTS.get_or_init(|| {
        serde_json::from_str(include_str!("tool_batch_texts.json")).expect("exported texts are JSON")
    })
}

/// One exported `execute_tool_batch` instruction, by its result's code.
fn batch_instruction(key: &str) -> &'static str {
    texts()["batch_instructions"][key].as_str().unwrap_or_else(|| panic!("no exported instruction {key}"))
}

fn object(pairs: Vec<(&str, Json)>) -> JsonMap {
    pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect()
}

/// Record the current call's result and, for a refusal, its failure.
fn leave(state: &mut ProviderTurnState, result: JsonMap, failed: bool) -> CodedResult<()> {
    let call_id = state.locals.assembly.get()?.call_id.clone();
    state.locals.local_tool_results.get_mut()?.insert(call_id.clone(), result);
    if failed {
        state.locals.local_tool_failures.get_mut()?.insert(call_id);
    }
    Ok(())
}

/// `{"status": "rejected", "code": code}`, a failure.
fn reject_code(state: &mut ProviderTurnState, code: &str) -> CodedResult<()> {
    leave(state, object(vec![("status", json!("rejected")), ("code", json!(code))]), true)
}

/// `state.assembly = assembly`, with the same assembly kept in `assemblies` (under `key`) and in
/// `ordered` (at `position`), as Python's one object is.
fn set_assembly(
    state: &mut ProviderTurnState,
    key: i64,
    position: usize,
    assembly: ToolCallAssembly,
) -> CodedResult<()> {
    state.locals.ordered.get_mut()?[position] = assembly.clone();
    state.locals.assemblies.get_mut()?.insert(key, assembly.clone());
    state.locals.assembly.set(assembly);
    Ok(())
}

/// `prepare_tool_batch(...)`: order the round's calls, give each an identity and its resolved
/// name, record the assistant's tool-call message in the exchange and emit it as evidence.
pub async fn prepare_tool_batch(
    adapter: &ProviderSessionAdapter,
    state: &mut ProviderTurnState,
    input: &SessionProviderInput,
    turn_id: &str,
    events: &mut dyn TurnEvents,
) -> TurnResult<()> {
    let mut keys: Vec<i64> = state.locals.assemblies.get()?.keys().copied().collect();
    keys.sort_unstable();
    let ordered: Vec<ToolCallAssembly> = keys
        .iter()
        .map(|key| state.locals.assemblies.get().map(|map| map[key].clone()))
        .collect::<Result<_, _>>()?;
    let has_text = !state.locals.assistant_text.get()?.is_empty();
    state.locals.ordered.set(ordered.clone());
    if !ordered.is_empty() && has_text {
        state.obligations.provisional_tool_prose_pending.set(true);
    }
    for (position, assembly) in ordered.iter().enumerate() {
        let mut assembly = assembly.clone();
        if assembly.call_id.is_empty() {
            assembly.call_id = format!("call-{}", adapter.uuid_hex());
        }
        set_assembly(state, keys[position], position, assembly)?;
    }
    unwrap_loader_calls(adapter, input)?;
    for (position, key) in keys.iter().enumerate() {
        let mut assembly = state.locals.ordered.get()?[position].clone();
        if let Some((name, _)) = resolve_provider_tool_alias(&assembly.name, state.locals.aliases.get()?) {
            assembly.name = name.to_owned();
        }
        set_assembly(state, *key, position, assembly)?;
    }
    let aliases = state.locals.aliases.get()?;
    let ordered = state.locals.ordered.value()?;
    let non_narrative = ordered.iter().any(|assembly| {
        resolve_provider_tool_alias(&assembly.name, aliases)
            .is_none_or(|(_, tool)| tool.category != "narrative_state")
    });
    state.locals.round_has_non_narrative_call.set(non_narrative);
    let raw_calls: Vec<Json> = ordered
        .iter()
        .map(|assembly| {
            json!({
                "id": assembly.call_id, "type": "function",
                "function": {
                    "name": if assembly.name.is_empty() { "unavailable_tool" } else { assembly.name.as_str() },
                    "arguments": if assembly.arguments.is_empty() { "{}" } else { assembly.arguments.as_str() },
                },
            })
        })
        .collect();
    let text: String = state.locals.assistant_text.get()?.concat();
    let content = if non_narrative || text.is_empty() { Json::Null } else { Json::String(text) };
    let assistant_message = object(vec![
        ("role", json!("assistant")),
        ("content", content),
        ("tool_calls", Json::Array(raw_calls)),
    ]);
    let native_content = state.locals.native_assistant_content.value()?;
    let chat_metadata = state.locals.native_chat_metadata.value()?;
    let reasoning = state.locals.assistant_reasoning.value()?;
    let native_requirement = object(vec![
        ("provider_id", json!(input.session.fields().provider_id)),
        ("model_id", json!(input.session.fields().model_id)),
        ("anthropic_required", json!(native_content.is_some())),
        ("chat_completion_required", json!(chat_metadata.is_some())),
        ("reasoning_required", json!(!reasoning.is_empty())),
    ]);
    // Signed native blocks belong only to this in-memory provider exchange. The separate
    // evidence message below stays player-safe and must never persist private thinking.
    let mut in_turn = assistant_message.clone();
    if let Some(blocks) = &native_content {
        in_turn.insert(
            "anthropic_content".into(),
            Json::Array(blocks.iter().cloned().map(Json::Object).collect()),
        );
    }
    if !reasoning.is_empty() {
        in_turn.insert("reasoning_content".into(), Json::String(reasoning.concat()));
    }
    let metadata = chat_metadata.map(Json::Object);
    let in_turn = attach_chat_metadata(&in_turn, metadata.as_ref(), false)?;
    // The context ledger journals this exchange in its saved form and reattaches the native
    // blocks by reference, exactly as for the canonical evidence after the turn. Host-only tag.
    let inference_id = state.locals.inference_id.value()?;
    let mut entry = assistant_message.clone();
    entry.insert(
        "_native_reference".into(),
        json!({"response_id": inference_id, "requirement": native_requirement.clone()}),
    );
    state.locals.exchange_messages_mut()?.push(Tagged::with(in_turn, LEDGER_ENTRY_KEY, Json::Object(entry)));
    let mut payload = object(vec![("kind", json!("provider_tool_calls"))]);
    for (key, value) in state.locals.context_fields.get()? {
        payload.insert(key.clone(), value.clone());
    }
    payload.insert("turn_id".into(), json!(turn_id));
    payload.insert("inference_id".into(), json!(inference_id));
    payload.insert(
        "basis_message_id".into(),
        state.locals.basis_message_id.value()?.map_or(Json::Null, Json::String),
    );
    payload.insert("assistant".into(), Json::Object(assistant_message));
    payload.insert("native_continuation".into(), Json::Object(native_requirement));
    events
        .emit(ProviderEvent::Evidence { message_id: format!("{inference_id}-assistant-tools"), payload })
        .await?;
    state.progress.state_changing_call_submitted.set(false);
    Ok(())
}

/// The session's working context, read for `read_session_history`.
fn read_session_history(
    adapter: &ProviderSessionAdapter,
    state: &mut ProviderTurnState,
    input: &SessionProviderInput,
) -> CodedResult<()> {
    let arguments = state.locals.assembly.get()?.arguments.clone();
    let answered = match load_arguments(&arguments)? {
        Ok(Json::Object(parameters)) => {
            let working = adapter.working.lock().unwrap_or_else(std::sync::PoisonError::into_inner);
            match working.get(input.session.session_id()) {
                Some(working) if working.history_retrieval_enabled() && !working.records().is_empty() => {
                    Some(working.read_history(&parameters))
                }
                // The fixed tool list's refusal is for ledger mode; a legacy route raises.
                _ => None,
            }
        }
        _ => None,
    };
    match answered {
        Some(result) => leave(state, result, false),
        None => leave(state, object(vec![("error", json!("Invalid history request"))]), true),
    }
}

/// Python integer arguments: a JSON integer, never a bool or a float.
fn integer(value: &Json) -> Option<i64> {
    match value {
        Json::Number(number) if !number.is_f64() => number.as_i64(),
        _ => None,
    }
}

/// A reference tool: the knowledge search or an exact reference read.
async fn run_reference_tool(state: &mut ProviderTurnState, input: &SessionProviderInput) -> CodedResult<()> {
    let tool = state.locals.catalog_tool.value()?;
    let parameters = state.locals.parameters.value()?;
    if tool.capability == "context.search" {
        let query =
            parameters.get("query").and_then(Json::as_str).filter(|query| !pystr::strip(query).is_empty());
        let raw_sources = parameters.get("sources").filter(|sources| !sources.is_null());
        let sources: Option<Vec<String>> = match raw_sources {
            Some(Json::Array(items)) => items.iter().map(|item| item.as_str().map(str::to_owned)).collect(),
            _ => None,
        };
        let limit = parameters.get("limit").map_or(Some(5), integer);
        let offset = parameters.get("offset").map_or(Some(0), integer).filter(|offset| *offset >= 0);
        let (Some(searcher), Some(query), Some(limit), Some(offset)) =
            (&input.knowledge_searcher, query, limit, offset)
        else {
            return reject_code(state, "campaign_knowledge_search_unavailable");
        };
        if raw_sources.is_some() && sources.is_none() {
            return reject_code(state, "campaign_knowledge_search_unavailable");
        }
        return match searcher(query.to_owned(), sources, limit, offset).await {
            Ok(matches) => leave(
                state,
                object(vec![("status", json!("knowledge_search")), ("result", Json::Object(matches))]),
                false,
            ),
            Err(error) if is_command_rejected(&error) => reject_code(state, &error.code),
            Err(error) => Err(error),
        };
    }
    let (Some(reader), Some(source), Some(path)) = (
        &input.reference_reader,
        parameters.get("source").and_then(Json::as_str),
        parameters.get("path").and_then(Json::as_str),
    ) else {
        return reject_code(state, "campaign_reference_unavailable");
    };
    match reader(source.to_owned(), path.to_owned()).await {
        Ok(reference) => leave(
            state,
            object(vec![("status", json!("reference_read")), ("result", Json::Object(reference))]),
            false,
        ),
        Err(error) if is_command_rejected(&error) => reject_code(state, &error.code),
        Err(error) => Err(error),
    }
}

/// The Campaign File disposition: the held response, the disposition's checks, then the
/// narrative writer, and the settled result.
async fn run_campaign_file_tool(
    adapter: &ProviderSessionAdapter,
    state: &mut ProviderTurnState,
    input: &SessionProviderInput,
) -> CodedResult<()> {
    let refuse = |state: &mut ProviderTurnState, code: &str, extra: Vec<(&str, Json)>| {
        let mut result = object(vec![("status", json!("rejected")), ("code", json!(code))]);
        for (key, value) in extra {
            result.insert(key.to_owned(), value);
        }
        result.insert("state_changed".into(), json!(false));
        result.insert("instruction".into(), json!(batch_instruction(code)));
        leave(state, result, true)
    };
    if state.locals.ordered.get()?.len() != 1 {
        return refuse(state, "campaign_file_update_must_be_only_tool", Vec::new());
    }
    state.progress.state_changing_call_submitted.set(true);
    let attached = if buffered_narration_can_be_replaced(state)? {
        state.locals.assistant_text.get()?.concat()
    } else {
        String::new()
    };
    let attached = adapter.filter_private_deliberation(&attached);
    if !pystr::strip(&attached).is_empty() {
        if contains_serialized_tool_call(&attached) {
            let result = object(vec![
                ("status", json!("rejected")),
                ("code", json!("provider_serialized_tool_call")),
                ("state_changed", json!(false)),
            ]);
            return leave(state, result, true);
        }
        state.locals.buffered_narration.set(Some(attached));
        state.locals.buffered_response_blocks.set(None);
        state.locals.buffered_raw_response_blocks.set(None);
        state.obligations.buffered_narration_needs_disposition.set(true);
        state.progress.campaign_file_disposition_in_exchange.set(false);
    }
    prepare_campaign_file_revision(adapter, state).await?;
    let preparing =
        state.locals.pending_boundary.get()?.as_ref().is_some_and(|boundary| boundary.preparation.is_some());
    if preparing {
        let not_only_ooc = match state.locals.selected_raw_blocks.get()? {
            None => true,
            Some(blocks) => {
                blocks.iter().any(|block| block.get("kind").and_then(Json::as_str) != Some("ooc"))
            }
        };
        if not_only_ooc || campaign_file_arguments_establish_changes(state.locals.parameters.get()?) {
            return refuse(state, "campaign_preparation_pending", Vec::new());
        }
    }
    validate_revision_speakers(adapter, state).await?;
    if *state.locals.ooc_ack_missing.get()?
        || *state.locals.unsupported_speakers.get()?
        || (*state.locals.revised_narration_supplied.get()?
            && state.locals.revised_raw_blocks.get()?.is_none())
    {
        return reject_invalid_revision(state);
    }
    if reject_ruleless_mechanics(adapter, state, input).await? {
        return Ok(());
    }
    settle_campaign_file_narration(state)?;
    let empty = state.locals.empty_collection_paths.value()?;
    if !empty.is_empty() {
        return refuse(state, "campaign_file_empty_collections", vec![("field_paths", json!(empty))]);
    }
    let establishes = *state.locals.parameters_establish_changes.get()?;
    let attempted = *state.locals.campaign_file_mutation_attempted.get()?;
    if !establishes && attempted {
        return refuse(state, "campaign_file_unchanged_after_rejected_update", Vec::new());
    }
    state.locals.mutation_attempted_before_call.set(attempted);
    if establishes {
        state.locals.campaign_file_mutation_attempted.set(true);
    }
    let Some(writer) = &input.narrative_writer else {
        return reject_code(state, "campaign_narrative_state_unavailable");
    };
    let update = match writer(state.locals.parameters.value()?).await {
        Ok(update) => update,
        Err(error) => match error.refusal() {
            Some(refusal) if error.is_command_rejected() => {
                return record_campaign_file_rejection(state, refusal)
            }
            _ => return Err(error.into_coded()),
        },
    };
    state.locals.update.set(update);
    if let Some(raw) = state.locals.buffered_raw_response_blocks.value()? {
        let owner = adapter.turn_owner()?;
        let campaign_id = state.locals.campaign_id.value()?;
        let snapshot = owner.snapshot(&campaign_id).await?;
        let roles = persisted_selected_party_roles(owner, &campaign_id, &snapshot.state.participants).await?;
        let (settled, blocks) = project_response_blocks(
            &raw,
            &snapshot.state.participants,
            &snapshot.continuity,
            roles.as_ref(),
        )?;
        state.locals.settled_narration.set(Some(settled.clone()));
        state.locals.buffered_response_blocks.set(Some(blocks));
        state.locals.buffered_narration.set(Some(settled));
    }
    let status = match state.locals.update.get()?.get("status") {
        None => "campaign_file_updated".to_owned(),
        Some(Json::String(status)) => status.clone(),
        Some(other) => pystr::repr_json(other),
    };
    if status == "campaign_file_unchanged" {
        if establishes {
            return restore_unchanged_narration(state);
        }
        return record_unchanged_campaign_file(state);
    }
    record_updated_campaign_file(state)
}

/// `execute_tool_batch(...)`: check and run each of the round's calls in order.
pub async fn execute_tool_batch(
    adapter: &ProviderSessionAdapter,
    state: &mut ProviderTurnState,
    input: &SessionProviderInput,
    turn_id: &str,
    events: &mut dyn TurnEvents,
) -> TurnResult<()> {
    let count = state.locals.ordered.get()?.len();
    for position in 0..count {
        let assembly = state.locals.ordered.get()?[position].clone();
        state.locals.assembly.set(assembly.clone());
        input.control.progress.update("tool", Some(&assembly.name))?;
        if assembly.name == "read_session_history" {
            read_session_history(adapter, state, input)?;
            continue;
        }
        // The loader's own reader serves only ledger mode.
        legacy_route(adapter, input)?;
        // A spell-value read the loader leaves to a contract tool's own reader (#65).
        if assembly.name == ARGUMENTS_TOOL_NAME {
            read_selected_arguments(adapter, state, input)?;
            continue;
        }
        let Some((_, tool)) = resolve_provider_tool_alias(&assembly.name, state.locals.aliases.get()?) else {
            reject_unavailable_tool(adapter, state, input)?;
            continue;
        };
        let tool = tool.clone();
        state.locals.catalog_tool.set(tool.clone());
        if refuse_unread_mechanic(adapter, input)? {
            continue;
        }
        if tool_call_needs_fresh_catalog(state, position as i64)? {
            let result = object(vec![
                ("status", json!("superseded_by_catalog_refresh")),
                ("state_changed", json!(false)),
                ("instruction", json!(batch_instruction("superseded_by_catalog_refresh"))),
            ]);
            leave(state, result, false)?;
            continue;
        }
        let parameters = match load_arguments(&assembly.arguments)? {
            Ok(Json::Object(parameters)) => parameters,
            Ok(_) => {
                // Python assigns the non-object value to `state.parameters` first; the Rust
                // local is an object, so it is left as it was. Nothing reads it for this call.
                let result = object(vec![
                    ("status", json!("rejected")),
                    ("code", json!("provider_tool_arguments_not_object")),
                    ("state_changed", json!(false)),
                    ("instruction", json!(batch_instruction("provider_tool_arguments_not_object"))),
                ]);
                leave(state, result, true)?;
                continue;
            }
            Err(error) => {
                reject_invalid_tool_json(state, &error)?;
                continue;
            }
        };
        state.locals.parameters.set(parameters);
        if !unwrap_selected_arguments(state)? {
            continue;
        }
        let scope = state.locals.parameters.get_mut()?.shift_remove(PREPARATION_INTENT_ARGUMENT);
        if let Some(scope) = scope {
            let checked = if tool.model_behavior != ModelToolBehavior::LeavesPlayerIntentPending {
                Err(CodedError::new(
                    "provider_player_intent_scope_invalid",
                    "This operation does not declare a preparation scope",
                )
                .with_error_type("CommandRejected"))
            } else {
                preparation_keeps_intent_pending(
                    Some(&scope),
                    *state.obligations.player_intent_pending.get()?,
                )
                .map(|_| ())
            };
            match checked {
                Ok(()) => {
                    let call_id = state.locals.assembly.get()?.call_id.clone();
                    state.locals.mechanics_preparation_scopes.get_mut()?.insert(call_id, scope);
                }
                Err(error) if is_command_rejected(&error) => {
                    let result = object(vec![
                        ("status", json!("rejected")),
                        ("code", json!(error.code)),
                        ("field_paths", json!([PREPARATION_INTENT_ARGUMENT])),
                        ("state_changed", json!(false)),
                        ("instruction", json!(error.message)),
                    ]);
                    leave(state, result, true)?;
                    continue;
                }
                Err(error) => return Err(error.into()),
            }
        }
        let campaign_id = state.locals.campaign_id.value()?;
        let snapshot = adapter.turn_owner()?.snapshot(&campaign_id).await?;
        let derivations = tool.parameter_derivations.clone().unwrap_or_default();
        let derived = derive_mechanics_parameters(
            state.locals.parameters.get()?,
            &derivations,
            &snapshot.state.participants,
        );
        state.locals.snapshot.set(snapshot);
        match derived {
            Ok(derived) => state.locals.parameters.set(derived),
            Err(DerivationError::Rejected { code, message, field_paths }) => {
                let result = object(vec![
                    ("status", json!("rejected")),
                    ("code", json!(code)),
                    ("reason", json!(message)),
                    ("field_paths", json!(field_paths.unwrap_or_default())),
                    ("state_changed", json!(false)),
                    ("instruction", json!(batch_instruction("derivation_rejected"))),
                ]);
                leave(state, result, true)?;
                continue;
            }
            Err(DerivationError::Fault(error)) => return Err(error.into()),
        }
        let normalized = normalize_state_references(
            state.locals.parameters.get()?,
            &tool.input_schema,
            &state.locals.snapshot.get()?.state.state,
        )?;
        state.locals.parameters.set(normalized);
        let call_id = state.locals.assembly.get()?.call_id.clone();
        state.locals.mechanics_tool_names.get_mut()?.insert(call_id.clone(), tool.name.clone());
        state.locals.mechanics_tool_behaviors.get_mut()?.insert(call_id, tool.model_behavior);
        let validation_schema = state
            .locals
            .provider_tool_schemas
            .get()?
            .get(&assembly.name)
            .cloned()
            .unwrap_or_else(|| tool.input_schema.clone());
        let missing = missing_required_arguments(state.locals.parameters.get()?, &validation_schema);
        let has_missing = !missing.is_empty();
        state.locals.missing.set(missing);
        if has_missing {
            record_missing_tool_fields(adapter, state, input, turn_id)?;
            continue;
        }
        if tool.package_id == "sylvia.application" && tool.capability.starts_with("action_sequence.") {
            execute_action_sequence_call(adapter, state, input).await?;
            continue;
        }
        if tool.category == "reference" {
            run_reference_tool(state, input).await?;
            continue;
        }
        if tool.category == "narrative_state" {
            run_campaign_file_tool(adapter, state, input).await?;
            continue;
        }
        submit_mechanics_call(adapter, state, input, turn_id, events).await?;
    }
    Ok(())
}

/// `submit_mechanics_call(...)`: hand one mechanic to the session loop as an action, after its
/// contract evidence; a rejection the loop writes back becomes the call's result.
pub async fn submit_mechanics_call(
    adapter: &ProviderSessionAdapter,
    state: &mut ProviderTurnState,
    input: &SessionProviderInput,
    turn_id: &str,
    events: &mut dyn TurnEvents,
) -> TurnResult<()> {
    let action_id = format!("action-{}", adapter.uuid_hex());
    let idempotency_key = format!("provider-{}", adapter.uuid_hex());
    let tool = state.locals.catalog_tool.value()?;
    let parameters = state.locals.parameters.value()?;
    let snapshot = state.locals.snapshot.get()?;
    let command = ActionCommand {
        campaign_id: state.locals.campaign_id.value()?,
        action_id: action_id.clone(),
        idempotency_key,
        owner_id: adapter.owner_id().to_owned(),
        writer: input.session.role(),
        expected_revision: snapshot.state.revision,
        turn_id: turn_id.to_owned(),
        package_id: tool.package_id.clone(),
        tool_name: tool.name.clone(),
        parameters: parameters.clone(),
        initiating_session_id: Some(input.session.session_id().to_owned()),
    };
    let pre_entities = current_referenced_entities(&parameters, &snapshot.state.state);
    let call_id = state.locals.assembly.get()?.call_id.clone();
    state.locals.mechanics_action_ids.get_mut()?.insert(call_id.clone(), action_id.clone());
    state.locals.mechanics_parameters.get_mut()?.insert(call_id.clone(), parameters);
    state.locals.mechanics_pre_entities.get_mut()?.insert(call_id.clone(), pre_entities);
    state.progress.state_changing_call_submitted.set(true);
    let feedback = ProviderActionFeedback::new();
    let inference_id = state.locals.inference_id.value()?;
    let mut payload = object(vec![
        ("kind", json!("provider_mechanics_action_contract")),
        ("turn_id", json!(turn_id)),
        ("inference_id", json!(inference_id)),
        ("basis_message_id", state.locals.basis_message_id.value()?.map_or(Json::Null, Json::String)),
        ("action_id", json!(action_id)),
        ("operation", json!(tool.name)),
        ("model_behavior", json!(tool.model_behavior.as_str())),
    ]);
    if let Some(scope) = state.locals.mechanics_preparation_scopes.get()?.get(&call_id) {
        payload.insert(PREPARATION_INTENT_ARGUMENT.into(), scope.clone());
    }
    let owner =
        state.locals.decision_boundary.get()?.as_ref().map(|boundary| boundary.owner.as_str().to_owned());
    payload.insert("decision_owner".into(), owner.map_or(Json::Null, Json::String));
    events
        .emit(ProviderEvent::Evidence {
            message_id: format!("{inference_id}-{call_id}-mechanics-contract"),
            payload,
        })
        .await?;
    events.emit(ProviderEvent::Action { command, feedback: Some(feedback.clone()) }).await?;
    if let Some(rejection) = feedback.rejection() {
        leave(state, rejection, true)?;
    }
    Ok(())
}

/// `execute_action_sequence_call(...)`: an action-sequence operation through the shared queue
/// service, with its saved outcomes as narration facts; when execution's reply fails, the saved
/// queue is recovered by reading, never by executing again.
pub async fn execute_action_sequence_call(
    adapter: &ProviderSessionAdapter,
    state: &mut ProviderTurnState,
    input: &SessionProviderInput,
) -> CodedResult<()> {
    let tool = state.locals.catalog_tool.value()?;
    let submitted = *state.progress.state_changing_call_submitted.get()?;
    state.progress.state_changing_call_submitted.set(submitted || tool.category != "reference");
    let failure = match sequence_call(adapter, state, input, &tool.name).await {
        Ok(()) => return Ok(()),
        Err(failure) => failure,
    };
    let Some(refusal) = failure.refusal() else {
        return Err(failure.into_coded());
    };
    let feedback = safe_error(refusal);
    let code = refusal.code.to_owned();
    let owner = adapter.turn_owner()?;
    if tool.name == "execute_action_sequence" {
        // The service may have persisted a prefix before its reply failed. Recover receipts by
        // reading; never retry execution implicitly.
        state.progress.sequence_execution_stopped.set(true);
        let sequence_id = state.locals.parameters.get()?.get("sequence_id").cloned();
        let saved = owner
            .action_sequence(input.session.session_id(), sequence_id.as_ref())
            .await
            .map_err(AuthorityFailure::into_coded)?
            .map_or(Json::Null, Json::Object);
        let result = object(vec![
            ("kind", json!("action_sequence_result")),
            ("operation", json!(tool.name)),
            ("result", saved.clone()),
            ("execution_stopped", json!(true)),
            ("delivery_error", json!({"code": code, "reason": feedback["reason"]})),
        ]);
        leave(state, result, false)?;
        if let Json::Object(saved) = &saved {
            let campaign_id = state.locals.campaign_id.value()?;
            let facts = action_sequence_narration_facts(owner, &campaign_id, saved).await?;
            state.locals.facts.set(facts.clone());
            if let Some(facts) = facts {
                state.locals.sequence_narration_facts.get_mut()?.push(facts);
            }
        }
        push_stop(state, "undelivered")?;
    } else {
        let result = object(vec![
            ("status", json!("rejected")),
            ("code", json!(code)),
            ("reason", feedback["reason"].clone()),
            ("validation", feedback["validation"].clone()),
            ("state_changed", json!(false)),
        ]);
        leave(state, result, true)?;
    }
    Ok(())
}

/// The `try` body of `execute_action_sequence_call`: an `AuthorityError` it raises at any point
/// (the state it changed before stays changed) goes to the caller's `except`.
async fn sequence_call(
    adapter: &ProviderSessionAdapter,
    state: &mut ProviderTurnState,
    input: &SessionProviderInput,
    name: &str,
) -> Result<(), AuthorityFailure> {
    let owner = adapter.turn_owner()?;
    let campaign_id = state.locals.campaign_id.value().map_err(CodedError::from)?;
    let parameters = state.locals.parameters.value().map_err(CodedError::from)?;
    let result =
        call_action_sequence_tool(owner, &campaign_id, input.session.session_id(), name, &parameters).await?;
    leave(state, result.clone(), false)?;
    if name == "execute_action_sequence" {
        let Some(Json::Object(saved)) = result.get("result") else {
            return Err(CodedError::rejected("Action sequence result is unavailable")
                .with_error_type("CommandRejected")
                .into());
        };
        let facts = action_sequence_narration_facts(owner, &campaign_id, saved).await?;
        state.locals.facts.set(facts.clone());
        if let Some(facts) = facts {
            state.locals.sequence_narration_facts.get_mut().map_err(CodedError::from)?.push(facts);
        }
        if matches!(saved.get("status").and_then(Json::as_str), Some("paused" | "failed" | "stale")) {
            state.progress.sequence_execution_stopped.set(true);
            push_stop(state, "stopped")?;
        }
    }
    Ok(())
}

/// Append one `SYLVIA_ACTION_SEQUENCE_STOP` control.
fn push_stop(state: &mut ProviderTurnState, key: &str) -> CodedResult<()> {
    let content = texts()["sequence_stops"][key].as_str().expect("an exported stop");
    state
        .locals
        .current_application_controls
        .get_mut()?
        .push(object(vec![("role", json!("system")), ("content", json!(content))]));
    Ok(())
}

/// `mapping[key]` on a turn local without it: Python's `KeyError`, an internal fault.
fn entry<'a, T>(map: &'a indexmap::IndexMap<String, T>, key: &str) -> CodedResult<&'a T> {
    map.get(key).ok_or_else(|| {
        CodedError::new("internal_error", pystr::repr_json(&Json::from(key))).with_error_type("KeyError")
    })
}

/// `json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)`.
fn canonical_text(value: &Json) -> String {
    sylvia_base::pyjson::dumps(value, sylvia_base::pyjson::Dump::CANONICAL)
}

/// One `{"role": "tool", ...}` result message of the round.
fn tool_message(assembly: &ToolCallAssembly, content: &Json) -> Tagged {
    Tagged::plain(object(vec![
        ("role", json!("tool")),
        ("tool_call_id", json!(assembly.call_id)),
        ("name", json!(assembly.name)),
        ("content", json!(canonical_text(content))),
    ]))
}

/// `reconcile_tool_batch(...)`: the round's results back into the exchange, in call order. A
/// failed optional reference read is marked correctable; each mechanic's canonical result is
/// read back from the owner (its terminal event and tool result), with its narration facts and
/// what it settles about the player's intent; the results are emitted as one evidence message.
pub async fn reconcile_tool_batch(
    adapter: &ProviderSessionAdapter,
    state: &mut ProviderTurnState,
    input: &SessionProviderInput,
    turn_id: &str,
    events: &mut dyn TurnEvents,
) -> TurnResult<()> {
    let reference_failure_instruction =
        texts()["reference_failure_instruction"].as_str().expect("the exported reference instruction");
    let ordered = state.locals.ordered.get()?.clone();
    let mut optional_reference_failures: indexmap::IndexSet<String> = indexmap::IndexSet::new();
    for assembly in &ordered {
        state.locals.assembly.set(assembly.clone());
        if !state.locals.local_tool_failures.get()?.contains(&assembly.call_id) {
            continue;
        }
        let reference = resolve_provider_tool_alias(&assembly.name, state.locals.aliases.get()?)
            .is_some_and(|(_, tool)| tool.category == "reference");
        if !reference {
            continue;
        }
        optional_reference_failures.insert(assembly.call_id.clone());
        let results = state.locals.local_tool_results.get_mut()?;
        entry(results, &assembly.call_id)?;
        let result = results.get_mut(&assembly.call_id).expect("checked above");
        result.insert("correction_optional".into(), Json::Bool(true));
        result.insert("instruction".into(), json!(reference_failure_instruction));
    }
    let failures = state.locals.local_tool_failures.get()?.clone();
    state.locals.required_tool_failures.set(
        failures.iter().filter(|call_id| !optional_reference_failures.contains(*call_id)).cloned().collect(),
    );
    state.progress.round_failed.set(!failures.is_empty());
    let sequence_facts = state.locals.sequence_narration_facts.get()?.clone();
    let mut round_has_any_commit = !sequence_facts.is_empty();
    let mut round_leaves_player_intent_pending = false;
    let mut round_resolves_player_intent = false;
    let mut round_narration_facts: Vec<JsonMap> = sequence_facts;
    let tool_messages_start = state.locals.exchange_messages_mut()?.len();
    for assembly in &ordered {
        state.locals.assembly.set(assembly.clone());
        let local_result = state.locals.local_tool_results.get()?.get(&assembly.call_id).cloned();
        if let Some(local_result) = local_result {
            if state.locals.local_tool_failures.get()?.contains(&assembly.call_id) {
                state.progress.round_failed.set(true);
            }
            state.locals.exchange_messages_mut()?.push(tool_message(assembly, &Json::Object(local_result)));
            continue;
        }
        let campaign_id = state.locals.campaign_id.value()?;
        let snapshot = adapter.turn_owner()?.snapshot(&campaign_id).await?;
        state.locals.snapshot.set(snapshot);
        let action_id = entry(state.locals.mechanics_action_ids.get()?, &assembly.call_id)?.clone();
        let snapshot = state.locals.snapshot.get()?;
        let latest = |kinds: &[CanonicalEventType]| {
            snapshot
                .events
                .iter()
                .rev()
                .find(|event| {
                    event.action_id.as_deref() == Some(action_id.as_str())
                        && kinds.contains(&event.event_type)
                })
                .cloned()
        };
        let terminal_event = latest(&[CanonicalEventType::ActionCommitted, CanonicalEventType::ActionFailed]);
        let tool_result_event = latest(&[CanonicalEventType::ToolResult]);
        let (Some(terminal_event), Some(tool_result_event)) = (terminal_event, tool_result_event) else {
            return Err(CodedError::new(
                "canonical_mechanic_result_unavailable",
                "Canonical mechanic result is unavailable",
            )
            .with_error_type("CommandRejected")
            .into());
        };
        let revision = snapshot.state.revision;
        let snapshot_state = snapshot.state.state.clone();
        let committed = terminal_event.event_type == CanonicalEventType::ActionCommitted;
        if committed {
            round_has_any_commit = true;
            let behavior = *entry(state.locals.mechanics_tool_behaviors.get()?, &assembly.call_id)?;
            state.locals.behavior.set(behavior);
            if behavior == ModelToolBehavior::LeavesPlayerIntentPending {
                let scope = state.locals.mechanics_preparation_scopes.get()?.get(&assembly.call_id).cloned();
                round_leaves_player_intent_pending = preparation_keeps_intent_pending(
                    scope.as_ref(),
                    *state.obligations.player_intent_pending.get()?,
                )?;
            } else if committed_tool_resolves_player_boundary(state)? {
                round_resolves_player_intent = true;
            }
        } else {
            state.progress.round_failed.set(true);
            state.locals.required_tool_failures.get_mut()?.insert(assembly.call_id.clone());
        }
        let result_payload =
            tool_result_event.safe_payload.get("result").cloned().unwrap_or_else(|| json!({}));
        // Narration facts use the full receipt; model history needs the outcome and card.
        let model_result = match (&result_payload, committed) {
            (Json::Object(result), true) => Json::Object(
                result
                    .iter()
                    .filter(|(key, _)| key.as_str() != "context")
                    .map(|(k, v)| (k.clone(), v.clone()))
                    .collect(),
            ),
            _ => result_payload.clone(),
        };
        adapter.turn_owner()?.record_admin_full_audit(object(vec![
            ("kind", json!("gm_mechanic_receipt")),
            ("action_id", json!(tool_result_event.action_id)),
            ("campaign_id", json!(campaign_id)),
            ("session_id", json!(input.session.session_id())),
            ("turn_id", json!(turn_id)),
            ("result", result_payload.clone()),
        ]))?;
        let parameters = entry(state.locals.mechanics_parameters.get()?, &assembly.call_id)?.clone();
        let current_entities = current_referenced_entities(&parameters, &snapshot_state);
        let round_result = object(vec![
            ("status", json!(terminal_event.event_type.as_str())),
            ("action_id", json!(tool_result_event.action_id)),
            ("campaign_revision", json!(revision)),
            ("safe_payload", Json::Object(terminal_event.safe_payload.clone())),
            ("result", model_result),
            ("current_entities", Json::Object(current_entities.clone())),
        ]);
        if committed {
            let tool_name = entry(state.locals.mechanics_tool_names.get()?, &assembly.call_id)?.clone();
            let transition = tool_name == "end_combat_encounter"
                || state.locals.required_transitions.get()?.iter().any(|tool| tool.name == tool_name);
            if transition {
                state.obligations.engine_transition_review_pending.set(false);
            }
            let previous = entry(state.locals.mechanics_pre_entities.get()?, &assembly.call_id)?.clone();
            let combat = current_combat_state(&snapshot_state);
            round_narration_facts.push(mechanics_narration_facts(
                &tool_name,
                &result_payload,
                &current_entities,
                &previous,
                Some(&parameters),
                Some(&combat),
                Some(state.locals.entity_display_names.get()?),
            )?);
        }
        state.locals.exchange_messages_mut()?.push(tool_message(assembly, &Json::Object(round_result)));
    }
    let tool_messages: Vec<Json> = state.locals.exchange_messages_mut()?[tool_messages_start..]
        .iter()
        .map(|message| Json::Object(message.message.clone()))
        .collect();
    if !tool_messages.is_empty() {
        let inference_id = state.locals.inference_id.value()?;
        let mut payload = object(vec![("kind", json!("provider_tool_results"))]);
        for (key, value) in state.locals.context_fields.get()? {
            payload.insert(key.clone(), value.clone());
        }
        payload.insert("turn_id".into(), json!(turn_id));
        payload.insert("inference_id".into(), json!(inference_id));
        payload.insert(
            "basis_message_id".into(),
            state.locals.basis_message_id.value()?.map_or(Json::Null, Json::String),
        );
        payload.insert("results".into(), Json::Array(tool_messages));
        // Model results omit engine context; a resumed turn restores these exact facts. This
        // field is evidence only and never sent as history.
        if !round_narration_facts.is_empty() {
            payload.insert(
                "narration_facts".into(),
                Json::Array(round_narration_facts.iter().cloned().map(Json::Object).collect()),
            );
        }
        events
            .emit(ProviderEvent::Evidence { message_id: format!("{inference_id}-tool-results"), payload })
            .await?;
    }
    input.control.progress.update("working", None)?;
    if round_has_any_commit {
        state.progress.any_commit_in_exchange.set(true);
        if round_leaves_player_intent_pending {
            state.obligations.player_intent_pending.set(true);
        }
        if round_resolves_player_intent {
            state.obligations.player_intent_pending.set(false);
        }
        if input.session.role() == sylvia_domain::authority::WriterRole::Dm {
            state.progress.campaign_file_disposition_in_exchange.set(false);
        }
        state.locals.buffered_narration.set(None);
        state.locals.buffered_response_blocks.set(None);
        state.locals.buffered_raw_response_blocks.set(None);
        state.obligations.buffered_narration_needs_disposition.set(false);
        state.locals.pending_narration_facts.get_mut()?.extend(round_narration_facts);
    }
    Ok(())
}
