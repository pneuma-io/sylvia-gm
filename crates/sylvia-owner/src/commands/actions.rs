//! `interfaces/commands/actions.py`: the owner's direct action and TURN commands. A command's
//! arguments are read in Python's order, then the application owner runs it
//! (`LocalOwnerSupervisor` only forwards to it).

use super::campaigns::{required, required_nonnegative_int, store_error};
use crate::dispatch::DispatchContext;
use sylvia_app::authority::actions::{ActionError, ActionExecution};
use sylvia_base::{pystr, CodedError, CodedResult, Json, JsonMap};
use sylvia_domain::authority::{
    ActionCommand, MechanicsExecutionReceipt, ResolveMechanicsChoiceCommand, WriterRole,
};

/// `OwnerProtocolError(message)`.
fn protocol(message: &str) -> CodedError {
    CodedError::rejected(message).with_error_type("OwnerProtocolError")
}

/// `None if params.get(key) is None else _required_text(params, key)`.
fn optional_required(params: &JsonMap, key: &str) -> CodedResult<Option<String>> {
    match params.get(key) {
        None | Some(Json::Null) => Ok(None),
        Some(_) => required(params, key).map(Some),
    }
}

/// `WriterRole(text)`: Python's `ValueError` for an unknown role.
fn writer_role(text: &str) -> CodedResult<WriterRole> {
    WriterRole::parse(text).ok_or_else(|| {
        CodedError::rejected(format!("{} is not a valid WriterRole", pystr::repr(text)))
            .with_error_type("ValueError")
    })
}

/// `_json_value(MechanicsExecutionReceipt)`.
fn execution_value(execution: ActionExecution) -> CodedResult<Json> {
    let receipt = match execution {
        ActionExecution::Receipt(receipt) => MechanicsExecutionReceipt::Action(receipt),
        ActionExecution::AwaitingChoice(receipt) => MechanicsExecutionReceipt::AwaitingChoice(receipt),
    };
    crate::owner_json::receipt_value(&receipt)
}

fn action_error(error: ActionError) -> CodedError {
    error.into_coded()
}

/// `handle_execute_action`: one Action-Execute command (`ActionCommand`, with the owner's own
/// identity) through the application owner.
pub async fn handle_execute_action(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let parameters = match params.get("parameters") {
        None => JsonMap::new(),
        Some(Json::Object(parameters)) => parameters.clone(),
        Some(_) => return Err(protocol("parameters must be an object")),
    };
    let campaign_id = required(params, "campaign_id")?;
    let action_id = required(params, "action_id")?;
    let idempotency_key = required(params, "idempotency_key")?;
    let writer = writer_role(&required(params, "writer")?)?;
    let expected_revision = required_nonnegative_int(params, "expected_revision")?;
    let turn_id = required(params, "turn_id")?;
    let package_id = required(params, "package_id")?;
    let tool_name = required(params, "tool_name")?;
    let initiating_session_id = optional_required(params, "session_id")?;
    let command = ActionCommand {
        campaign_id,
        action_id,
        idempotency_key,
        owner_id: ctx.owner_id.clone(),
        writer,
        expected_revision,
        turn_id,
        package_id,
        tool_name,
        parameters,
        initiating_session_id,
    };
    let execution = sylvia_app::authority::actions::execute_action(&ctx.store, &ctx.owner_id, &command)
        .await
        .map_err(action_error)?;
    execution_value(execution)
}

/// `handle_resolve_mechanics_choice`: one durable rules choice. `choice_sequence` defaults to 0.
pub async fn handle_resolve_mechanics_choice(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let campaign_id = required(params, "campaign_id")?;
    let action_id = required(params, "action_id")?;
    let resolution_id = required(params, "resolution_id")?;
    let expected_revision = required_nonnegative_int(params, "expected_revision")?;
    let choice_id = required(params, "choice_id")?;
    let option_id = required(params, "option_id")?;
    // `_required_nonnegative_int({"choice_sequence": params.get("choice_sequence", 0)}, ...)`.
    let mut sequence = JsonMap::new();
    sequence
        .insert("choice_sequence".into(), params.get("choice_sequence").cloned().unwrap_or_else(|| 0.into()));
    let choice_sequence = required_nonnegative_int(&sequence, "choice_sequence")?;
    let session_id = optional_required(params, "session_id")?;
    let command = ResolveMechanicsChoiceCommand {
        campaign_id,
        action_id,
        resolution_id,
        owner_id: ctx.owner_id.clone(),
        expected_revision,
        choice_id,
        option_id,
        session_id,
        choice_sequence,
    };
    let execution =
        sylvia_app::authority::actions::resolve_mechanics_choice(&ctx.store, &ctx.owner_id, &command)
            .await
            .map_err(action_error)?;
    execution_value(execution)
}

fn status(value: &str) -> Json {
    let mut reply = JsonMap::new();
    reply.insert("status".into(), value.into());
    Json::Object(reply)
}

/// `handle_open_turn`: `{"status": "open"}` once the TURN is open.
pub async fn handle_open_turn(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let campaign_id = required(params, "campaign_id")?;
    let turn_id = required(params, "turn_id")?;
    sylvia_app::authority::turns::open_turn(&ctx.store, &campaign_id, &turn_id).await.map_err(store_error)?;
    Ok(status("open"))
}

/// `handle_complete_turn`: `{"status": "completed"}` once the TURN is complete.
pub async fn handle_complete_turn(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let campaign_id = required(params, "campaign_id")?;
    let turn_id = required(params, "turn_id")?;
    sylvia_app::authority::turns::complete_turn(&ctx.store, &campaign_id, &turn_id)
        .await
        .map_err(store_error)?;
    Ok(status("completed"))
}

/// `handle_retryable_interruption` (`commands/campaigns.py`): one durable retry basis, or `null`.
pub async fn handle_retryable_interruption(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    let campaign_id = required(params, "campaign_id")?;
    let failed_attempt_id = required(params, "failed_attempt_id")?;
    let interruption = sylvia_app::authority::narration_attempts::retryable_interruption(
        &ctx.store,
        &campaign_id,
        &failed_attempt_id,
    )
    .await
    .map_err(action_error)?;
    Ok(interruption.as_ref().map_or(Json::Null, crate::owner_json::interruption_value))
}

/// `handle_execute_player_action`, for `execute_player_action` and `preview_player_action`: a
/// player's direct action from a saved result card's control, through lane 3's
/// `direct_actions::player_action` (`preview_only` for the preview).
pub async fn handle_execute_player_action(ctx: &DispatchContext, params: &JsonMap) -> CodedResult<Json> {
    use sylvia_app::authority::direct_actions::{player_action, PlayerActionAnswer, PlayerActionRequest};
    let source = params.get("source").cloned().unwrap_or(Json::Null);
    let selection = params.get("selection").cloned().unwrap_or(Json::Null);
    if !source.is_object() || !(selection.is_null() || selection.is_object()) {
        return Err(protocol("Player control source and selection must be objects"));
    }
    let campaign_id = required(params, "campaign_id")?;
    let session_id = required(params, "session_id")?;
    let operation_id = required(params, "operation_id")?;
    let expected_revision = required_nonnegative_int(params, "expected_revision")?;
    let confirmation = super::campaigns::optional_text(params, "confirmation")?;
    let request = PlayerActionRequest {
        campaign_id,
        source,
        operation_id,
        expected_revision,
        selection,
        session_id: Some(session_id),
        confirmation,
        preview_only: ctx.method == "preview_player_action",
    };
    match player_action(&ctx.store, &ctx.owner_id, &request).await.map_err(action_error)? {
        PlayerActionAnswer::Receipt(receipt) => {
            crate::owner_json::receipt_value(&MechanicsExecutionReceipt::Action(receipt))
        }
        PlayerActionAnswer::AwaitingChoice(receipt) => {
            crate::owner_json::receipt_value(&MechanicsExecutionReceipt::AwaitingChoice(receipt))
        }
        PlayerActionAnswer::Preview(preview) => crate::owner_json::json_value(&Json::Object(preview)),
    }
}
