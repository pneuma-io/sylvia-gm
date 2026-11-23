//! `agent/_provider_turn_phases.py`, the tool batch's checks, refusals and settled results
//! (T3a): what a call's arguments, the held response and the Campaign File disposition are
//! checked against, the rejection each check leaves for the model, and the turn locals they set.
//! The batch that runs them (`prepare_tool_batch`, `execute_tool_batch`,
//! `reconcile_tool_batch`) and the calls that execute (`submit_mechanics_call`,
//! `execute_action_sequence_call`) are T3b.
//!
//! **Legacy routes only.** Sylvia's tool loader (`call_mechanic`, `read_tool_arguments` for a
//! listed mechanic) and the fixed and one-list settings act only in context-ledger mode, which
//! the round's request refuses until L1-L4 port the ledger. So the loader helpers here take
//! their legacy answer, and [`legacy_route`] refuses a ledger-mode request the same way.

use super::state::ProviderTurnState;
use crate::argument_contracts::{
    argument_contract, read_tool_arguments, selected_schema, unwrap_arguments, ContractTool, CONTRACT_KEY,
};
use crate::provider_session::campaign_file::{
    campaign_file_arguments_establish_changes, campaign_file_empty_collection_paths,
    unsupported_response_speaker_ids, validated_raw_response_blocks,
};
use crate::provider_session::context::ProviderSessionAdapter;
use crate::provider_session::tools::{model_tool_schema, CAMPAIGN_FILE_REVISED_NARRATION_ARGUMENT};
use crate::session_loop::SessionProviderInput;
use crate::stateless_mcp::AuthorityRefusal;
use serde_json::{json, Value as Json};
use std::sync::OnceLock;
use sylvia_app::ruleless_play::{has_no_ruleset, mechanics_in_response, ruleless_mechanics_rejection};
use sylvia_base::pyjson::{loads_hooked, HookFailure, HookPolicy, JsonDecodeError};
use sylvia_base::{pystr, CodedError, CodedResult, JsonMap};
use sylvia_domain::authority::WriterRole;

fn texts() -> &'static Json {
    static TEXTS: OnceLock<Json> = OnceLock::new();
    TEXTS.get_or_init(|| {
        serde_json::from_str(include_str!("tool_batch_texts.json")).expect("exported texts are JSON")
    })
}

/// One exported instruction (`generate_tool_batch_texts.py`).
fn instruction(key: &str) -> &'static str {
    texts()["instructions"][key].as_str().unwrap_or_else(|| panic!("no exported instruction {key}"))
}

fn object(pairs: Vec<(&str, Json)>) -> JsonMap {
    pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect()
}

/// The request is on a legacy route: the tool loader and the fixed and one-list settings act
/// only in context-ledger mode, which L1-L4 port.
pub fn legacy_route(adapter: &ProviderSessionAdapter, input: &SessionProviderInput) -> CodedResult<()> {
    if adapter.ledger_active(input) {
        return Err(CodedError::new(
            "rust_port_pending",
            "The Rust owner does not run the context ledger's tool batch yet (agent/_context_ledger.py).",
        ));
    }
    Ok(())
}

/// `_tool_name_of(tool)`: a provider tool's name, from its `function` object when it has one.
pub fn tool_name_of(tool: &JsonMap) -> String {
    let source = match tool.get("function") {
        Some(Json::Object(function)) => function,
        _ => tool,
    };
    match source.get("name") {
        None => String::new(),
        Some(Json::String(name)) => name.clone(),
        Some(other) => pystr::repr_json(other),
    }
}

/// `unwrap_loader_calls(...)`: on a legacy route the loader's names are `None`, so nothing.
pub fn unwrap_loader_calls(
    adapter: &ProviderSessionAdapter,
    input: &SessionProviderInput,
) -> CodedResult<()> {
    legacy_route(adapter, input)
}

/// `refuse_unread_mechanic(...)`: on a legacy route there is no loader, so `False`.
pub fn refuse_unread_mechanic(
    adapter: &ProviderSessionAdapter,
    input: &SessionProviderInput,
) -> CodedResult<bool> {
    legacy_route(adapter, input).map(|()| false)
}

/// `read_loader_mechanic(...)`: on a legacy route there is no loader, so `False`.
pub fn read_loader_mechanic(
    adapter: &ProviderSessionAdapter,
    input: &SessionProviderInput,
) -> CodedResult<bool> {
    legacy_route(adapter, input).map(|()| false)
}

/// Record the current call's result and, for a refusal, its failure.
fn leave_result(state: &mut ProviderTurnState, result: JsonMap, failed: bool) -> CodedResult<()> {
    let call_id = state.locals.assembly.get()?.call_id.clone();
    state.locals.local_tool_results.get_mut()?.insert(call_id.clone(), result);
    if failed {
        state.locals.local_tool_failures.get_mut()?.insert(call_id);
    }
    Ok(())
}

/// `reject_unavailable_tool(...)`: refuse a call to a tool the request does not offer, listing
/// those it does.
pub fn reject_unavailable_tool(
    adapter: &ProviderSessionAdapter,
    state: &mut ProviderTurnState,
    input: &SessionProviderInput,
) -> CodedResult<()> {
    legacy_route(adapter, input)?;
    let requested = state.locals.assembly.get()?.name.clone();
    let available: Vec<Json> = state
        .locals
        .aliases
        .get()?
        .iter()
        .map(|(alias, tool)| json!({"provider_tool_name": alias, "operation": tool.name}))
        .collect();
    let result = object(vec![
        ("status", json!("rejected")),
        ("code", json!("unavailable_catalog_tool")),
        ("state_changed", json!(false)),
        ("requested_provider_tool_name", Json::String(requested)),
        ("available_operations", Json::Array(available)),
        // The one-list wording is for ledger mode's single tool list.
        ("instruction", json!(instruction("unavailable_tool_absent"))),
    ]);
    leave_result(state, result, true)
}

/// `_contract_tools(state)`: this round's tools that keep a selected value's inputs behind an
/// argument contract, in alias order.
pub fn contract_tools(state: &ProviderTurnState) -> CodedResult<Vec<ContractTool>> {
    let mut tools = Vec::new();
    for (alias, tool) in state.locals.aliases.get()? {
        if !tool.input_schema.contains_key(CONTRACT_KEY) {
            continue;
        }
        let schema = model_tool_schema(tool, false)?;
        if let Some(contract) = argument_contract(&schema)? {
            tools.push((alias.clone(), (schema, contract)));
        }
    }
    Ok(tools)
}

/// `json.loads(state.assembly.arguments or "{}")`: the value, or the `JSONDecodeError`.
pub fn load_arguments(arguments: &str) -> CodedResult<Result<Json, JsonDecodeError>> {
    let text = if arguments.is_empty() { "{}" } else { arguments };
    match loads_hooked(text, HookPolicy::default()) {
        Ok(hooked) => Ok(Ok(hooked.value)),
        Err(HookFailure::Decode(error)) => Ok(Err(error)),
        Err(HookFailure::Unsupported(error)) => Err(error),
        Err(other) => unreachable!("json.loads uses no hooks: {other:?}"),
    }
}

/// `read_selected_arguments(...)`: answer a read of one selected value's inputs locally,
/// without touching game state.
pub fn read_selected_arguments(
    adapter: &ProviderSessionAdapter,
    state: &mut ProviderTurnState,
    input: &SessionProviderInput,
) -> CodedResult<()> {
    let tools = contract_tools(state)?;
    if tools.is_empty() {
        return reject_unavailable_tool(adapter, state, input);
    }
    let parameters = match load_arguments(&state.locals.assembly.get()?.arguments)? {
        Ok(parameters) => parameters,
        Err(error) => return reject_invalid_tool_json(state, &error),
    };
    let mut result = read_tool_arguments(&parameters, &tools)?;
    let failed = result.get("status").and_then(Json::as_str) != Some("ok");
    result.insert("state_changed".into(), json!(false));
    leave_result(state, result, failed)
}

/// `unwrap_selected_arguments(self, state)`: flatten a contract tool's call after checking it
/// against the selected value's inputs. `false` when the call is refused; the refusal carries
/// the selected contract, so the model can correct it in one round without a separate read.
pub fn unwrap_selected_arguments(state: &mut ProviderTurnState) -> CodedResult<bool> {
    let tool = state.locals.catalog_tool.get()?;
    if !tool.input_schema.contains_key(CONTRACT_KEY) {
        return Ok(true);
    }
    let schema = model_tool_schema(tool, false)?;
    let Some(contract) = argument_contract(&schema)? else {
        return Ok(true);
    };
    let (parameters, issues) = unwrap_arguments(state.locals.parameters.get()?, &schema, &contract)?;
    let value = parameters.get(&contract.selector).cloned().unwrap_or(Json::Null);
    state.locals.parameters.set(parameters);
    if issues.is_empty() {
        return Ok(true);
    }
    let selected = selected_schema(&schema, &contract, &value)?.map_or(Json::Null, Json::Object);
    let text = instruction("selected_arguments_invalid")
        .replace("{contract.selector}", &contract.selector)
        .replace("{contract.field}", &contract.field);
    let result = object(vec![
        ("status", json!("rejected")),
        ("code", json!("selected_arguments_invalid")),
        ("state_changed", json!(false)),
        ("validation", json!({"issues": issues})),
        ("selected_arguments", selected),
        ("instruction", Json::String(text)),
    ]);
    leave_result(state, result, true)?;
    Ok(false)
}

/// `reject_invalid_tool_json(...)`: refuse streamed arguments that were not one JSON object.
pub fn reject_invalid_tool_json(state: &mut ProviderTurnState, error: &JsonDecodeError) -> CodedResult<()> {
    let finish = state
        .locals
        .round_finish_reason
        .get()?
        .clone()
        .filter(|reason| !reason.is_empty())
        .unwrap_or_else(|| "stream_ended_without_finish_reason".to_owned());
    let characters = pystr::len(&state.locals.assembly.get()?.arguments);
    let result = object(vec![
        ("status", json!("rejected")),
        ("code", json!("provider_tool_arguments_invalid_json")),
        ("state_changed", json!(false)),
        ("provider_response_finish_reason", Json::String(finish)),
        ("received_argument_characters", json!(characters)),
        (
            "json_error",
            json!({"message": error.msg, "line": error.lineno, "column": error.colno, "position": error.pos}),
        ),
        ("instruction", json!(instruction("tool_arguments_invalid_json"))),
    ]);
    leave_result(state, result, true)
}

/// `record_missing_tool_fields(...)`: refuse a call missing required arguments, recording a
/// metadata-only diagnostic for a narrative-state tool.
pub fn record_missing_tool_fields(
    adapter: &ProviderSessionAdapter,
    state: &mut ProviderTurnState,
    input: &SessionProviderInput,
    turn_id: &str,
) -> CodedResult<()> {
    let missing = state.locals.missing.value()?;
    let tool = state.locals.catalog_tool.get()?;
    if tool.category == "narrative_state" {
        let diagnostic = object(vec![
            ("kind", json!("mechanics_rejection")),
            ("privacy", json!("safe_metadata_only")),
            ("session_id", Json::String(input.session.session_id().to_owned())),
            ("turn_id", json!(turn_id)),
            ("action_id", Json::String(state.locals.assembly.get()?.call_id.clone())),
            ("tool_name", Json::String(tool.name.clone())),
            ("rejection_code", json!("missing_required_arguments")),
            ("validation_field_paths", json!(missing)),
            ("state_changed", json!(false)),
        ]);
        adapter.turn_owner()?.record_mechanics_diagnostic(diagnostic)?;
    }
    let result = object(vec![
        ("status", json!("rejected")),
        ("code", json!("missing_required_arguments")),
        ("missing", json!(missing)),
        ("instruction", json!(instruction("missing_required_arguments"))),
    ]);
    leave_result(state, result, true)
}

/// `reject_invalid_revision(...)`: refuse a revised response that is not one complete block list.
pub fn reject_invalid_revision(state: &mut ProviderTurnState) -> CodedResult<()> {
    let result = object(vec![
        ("status", json!("rejected")),
        ("code", json!("campaign_file_revised_narration_invalid")),
        ("field_paths", json!([CAMPAIGN_FILE_REVISED_NARRATION_ARGUMENT])),
        ("state_changed", json!(false)),
        ("instruction", json!(instruction("revised_narration_invalid"))),
    ]);
    leave_result(state, result, true)
}

/// `reject_ruleless_mechanics(...)`: hold back a No Ruleset response that resolves or requests
/// game mechanics. `true` when it refused.
pub async fn reject_ruleless_mechanics(
    adapter: &ProviderSessionAdapter,
    state: &mut ProviderTurnState,
    input: &SessionProviderInput,
) -> CodedResult<bool> {
    if input.session.role() != WriterRole::Dm {
        return Ok(false);
    }
    let campaign_id = state.locals.campaign_id.value()?;
    if !has_no_ruleset(&adapter.turn_owner()?.snapshot(&campaign_id).await?.state) {
        return Ok(false);
    }
    let held_text = if !*state.locals.revised_narration_supplied.get()?
        && state.locals.selected_raw_blocks.get()?.is_none()
    {
        state.locals.buffered_narration.value()?
    } else {
        None
    };
    let findings =
        mechanics_in_response(state.locals.selected_raw_blocks.get()?.as_deref(), held_text.as_deref());
    if findings.is_empty() {
        return Ok(false);
    }
    leave_result(state, ruleless_mechanics_rejection(&findings), true)?;
    Ok(true)
}

/// `record_campaign_file_rejection(..., error)`: the Campaign File authority's refusal, with a
/// mechanics refusal's field paths and issues.
pub fn record_campaign_file_rejection(
    state: &mut ProviderTurnState,
    error: AuthorityRefusal<'_>,
) -> CodedResult<()> {
    let mut rejection = object(vec![("status", json!("rejected")), ("code", json!(error.code))]);
    if matches!(error.error_type, "MechanicsRejected" | "RulesetPreconditionRejected") {
        let empty = Default::default();
        let details = error.mechanics.unwrap_or(&empty);
        rejection.insert("field_paths".into(), json!(details.field_paths));
        rejection.insert(
            "validation".into(),
            json!({
                "field_paths": details.field_paths,
                "issues": details.validation_issues.iter().map(|issue| Json::Object(issue.as_payload())).collect::<Vec<_>>(),
            }),
        );
        rejection.insert("state_changed".into(), json!(details.state_changed));
    }
    if matches!(error.code, "campaign_file_update_invalid" | "campaign_file_disposition_inconsistent") {
        let held = state.locals.buffered_narration.get()?.is_some()
            || state.locals.buffered_raw_response_blocks.get()?.is_some();
        let mut text = instruction("campaign_file_rejection").to_owned();
        if held {
            text.push_str(instruction("campaign_file_rejection_held"));
        }
        rejection.insert("instruction".into(), Json::String(text));
    }
    leave_result(state, rejection, true)
}

/// `restore_unchanged_narration(...)`: every supplied collection was already current; the
/// call's attempt is undone and it is refused.
pub fn restore_unchanged_narration(state: &mut ProviderTurnState) -> CodedResult<()> {
    let attempted = *state.locals.mutation_attempted_before_call.get()?;
    state.locals.campaign_file_mutation_attempted.set(attempted);
    let result = object(vec![
        ("status", json!("rejected")),
        ("code", json!("campaign_file_duplicate_collections")),
        ("state_changed", json!(false)),
        ("instruction", json!(instruction("duplicate_collections"))),
    ]);
    leave_result(state, result, true)
}

/// The settled Campaign File result: `status`, the update, the settled response and, when
/// there are blocks, the blocks.
fn settled_result(state: &ProviderTurnState, status: &str, prefix: &str) -> CodedResult<JsonMap> {
    let update = state.locals.update.value()?;
    let settled = state.locals.settled_narration.value()?;
    let mut result = object(vec![
        ("status", json!(status)),
        ("result", Json::Object(update)),
        ("player_facing_response", settled.clone().map_or(Json::Null, Json::String)),
    ]);
    if let Some(blocks) = state.locals.buffered_response_blocks.get()? {
        result.insert(
            "response_blocks".into(),
            Json::Array(blocks.iter().cloned().map(Json::Object).collect()),
        );
    }
    let key = if settled.is_some() { format!("{prefix}_settled") } else { format!("{prefix}_unsettled") };
    result.insert("instruction".into(), json!(instruction(&key)));
    Ok(result)
}

/// The obligations a settled disposition leaves: with a settled response it is delivered;
/// without one the narration is still owed.
fn settle_obligations(state: &mut ProviderTurnState) -> CodedResult<()> {
    match state.locals.settled_narration.value()? {
        Some(settled) => {
            state.locals.buffered_narration.set(Some(settled));
            state.obligations.buffered_narration_needs_disposition.set(false);
            state.obligations.campaign_file_narration_pending.set(false);
        }
        None => state.obligations.campaign_file_narration_pending.set(true),
    }
    state.obligations.force_campaign_file_update.set(false);
    Ok(())
}

/// `record_unchanged_campaign_file(...)`: the review is settled without a database write.
pub fn record_unchanged_campaign_file(state: &mut ProviderTurnState) -> CodedResult<()> {
    let result = settled_result(state, "campaign_file_unchanged", "unchanged")?;
    leave_result(state, result, false)?;
    state.progress.campaign_file_disposition_in_exchange.set(true);
    settle_obligations(state)
}

/// `record_updated_campaign_file(...)`: the update is settled at its admitted revision.
pub fn record_updated_campaign_file(state: &mut ProviderTurnState) -> CodedResult<()> {
    let result = settled_result(state, "campaign_file_updated", "updated")?;
    leave_result(state, result, false)?;
    state.progress.any_commit_in_exchange.set(true);
    state.progress.campaign_file_commit_in_exchange.set(true);
    state.progress.campaign_file_disposition_in_exchange.set(true);
    settle_obligations(state)
}

/// `prepare_campaign_file_revision(...)`: take the revised response out of the call's
/// arguments, filter private deliberation from it, and read the pending preparation boundary.
pub async fn prepare_campaign_file_revision(
    adapter: &ProviderSessionAdapter,
    state: &mut ProviderTurnState,
) -> CodedResult<()> {
    let parameters = state.locals.parameters.get_mut()?;
    let supplied = parameters.contains_key(CAMPAIGN_FILE_REVISED_NARRATION_ARGUMENT);
    let revised = parameters.shift_remove(CAMPAIGN_FILE_REVISED_NARRATION_ARGUMENT).unwrap_or(Json::Null);
    state.locals.revised_narration_supplied.set(supplied);
    state.locals.revised_narration.set(revised.clone());
    state.locals.revised_raw_blocks.set(None);
    match &revised {
        Json::String(text) => {
            state.locals.revised_narration.set(Json::String(adapter.filter_private_deliberation(text)))
        }
        Json::Array(_) => match retained_blocks(adapter, &revised) {
            Ok(retained) => {
                state.locals.revised_raw_blocks.set(Some(retained));
                state.locals.revised_narration.set(json!(""));
            }
            Err(error) if error.error_type == Some("ValueError") => {
                state.locals.revised_raw_blocks.set(None);
                state.locals.revised_narration.set(Json::Null);
            }
            Err(error) => return Err(error),
        },
        _ => {}
    }
    let selected = if supplied {
        state.locals.revised_raw_blocks.value()?
    } else {
        state.locals.buffered_raw_response_blocks.value()?
    };
    state.locals.selected_raw_blocks.set(selected);
    let campaign_id = state.locals.campaign_id.value()?;
    let boundary = adapter.turn_owner()?.campaign_preparation_boundary(&campaign_id).await?;
    state.locals.pending_boundary.set(boundary);
    Ok(())
}

/// The admitted revised blocks with private deliberation filtered from their text; a block left
/// empty is dropped, and none left is a `ValueError`.
fn retained_blocks(adapter: &ProviderSessionAdapter, revised: &Json) -> CodedResult<Vec<JsonMap>> {
    let mut retained = Vec::new();
    for block in validated_raw_response_blocks(revised, false)? {
        if block.get("kind").and_then(Json::as_str) == Some("npc_dialogue") {
            retained.push(block);
            continue;
        }
        let text = match block.get("text") {
            Some(Json::String(text)) => text.clone(),
            Some(other) => pystr::repr_json(other),
            None => {
                return Err(CodedError::new("internal_error", "'text'").with_error_type("KeyError"));
            }
        };
        let text = adapter.filter_private_deliberation(&text);
        if !pystr::strip(&text).is_empty() {
            let mut kept = block;
            kept.insert("text".into(), Json::String(text));
            retained.push(kept);
        }
    }
    if retained.is_empty() {
        return Err(CodedError::rejected("Provider returned no player-facing response blocks")
            .with_error_type("ValueError"));
    }
    Ok(retained)
}

/// `validate_revision_speakers(...)`: whether a required OOC acknowledgement is missing, and
/// whether the selected blocks name a speaker the disposition cannot establish.
pub async fn validate_revision_speakers(
    adapter: &ProviderSessionAdapter,
    state: &mut ProviderTurnState,
) -> CodedResult<()> {
    let missing = *state.locals.ooc_ack_required.get()?
        && !state.locals.selected_raw_blocks.get()?.as_ref().is_some_and(|blocks| {
            blocks.iter().any(|block| block.get("kind").and_then(Json::as_str) == Some("ooc"))
        });
    state.locals.ooc_ack_missing.set(missing);
    if missing {
        state.locals.revised_narration.set(Json::Null);
    }
    let unsupported = match state.locals.selected_raw_blocks.value()? {
        None => false,
        Some(blocks) => {
            let campaign_id = state.locals.campaign_id.value()?;
            let snapshot = adapter.turn_owner()?.snapshot(&campaign_id).await?;
            !unsupported_response_speaker_ids(
                &blocks,
                &snapshot.state.participants,
                &snapshot.continuity,
                state.locals.parameters.get()?,
            )
            .is_empty()
        }
    };
    state.locals.unsupported_speakers.set(unsupported);
    Ok(())
}

/// `settle_campaign_file_narration(...)`: select the response this disposition settles, and
/// keep it held while a malformed state payload is corrected; then classify the arguments.
pub fn settle_campaign_file_narration(state: &mut ProviderTurnState) -> CodedResult<()> {
    let mut settled = state.locals.buffered_narration.value()?;
    let revised_blocks = state.locals.revised_raw_blocks.value()?;
    if revised_blocks.is_none() {
        if let Json::String(revised) = state.locals.revised_narration.get()? {
            settled = Some(revised.clone());
        }
    }
    state.locals.settled_narration.set(settled.clone());
    if let Some(settled) = settled {
        // Omission in a later tool call means these exact bytes remain selected.
        state.locals.buffered_narration.set(Some(settled));
        if *state.locals.revised_narration_supplied.get()? {
            state.locals.buffered_response_blocks.set(None);
            state.locals.buffered_raw_response_blocks.set(revised_blocks);
        }
        state.obligations.buffered_narration_needs_disposition.set(true);
    } else if revised_blocks.is_some() {
        state.locals.buffered_raw_response_blocks.set(revised_blocks);
        state.locals.buffered_response_blocks.set(None);
        state.obligations.buffered_narration_needs_disposition.set(true);
    }
    let parameters = state.locals.parameters.get()?;
    let establish = campaign_file_arguments_establish_changes(parameters);
    let empty = campaign_file_empty_collection_paths(parameters);
    state.locals.parameters_establish_changes.set(establish);
    state.locals.empty_collection_paths.set(empty);
    Ok(())
}

/// `retry_rejected_tool_call(...)`: count one correction and tell the model what to correct.
pub fn retry_rejected_tool_call(state: &mut ProviderTurnState) -> CodedResult<()> {
    let used = *state.locals.rejected_tool_corrections_used.get()? + 1;
    state.locals.rejected_tool_corrections_used.set(used);
    let held = state.locals.buffered_narration.get()?.is_some()
        || state.locals.buffered_raw_response_blocks.get()?.is_some();
    let maximum = *state.locals.max_rejected_tool_corrections.get()?;
    let mut content = instruction("tool_correction").to_owned();
    if held {
        content.push_str(instruction("tool_correction_held"));
    }
    content.push_str(
        &instruction("tool_correction_count")
            .replace("{state.rejected_tool_corrections_used}", &used.to_string())
            .replace("{state.max_rejected_tool_corrections}", &maximum.to_string()),
    );
    state
        .locals
        .current_application_controls
        .get_mut()?
        .push(object(vec![("role", json!("system")), ("content", Json::String(content))]));
    Ok(())
}
