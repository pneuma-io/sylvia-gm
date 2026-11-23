//! `agent/_stateless_mcp.py`'s mechanics call: `StatelessMcpMechanics.execute_action` and
//! `_execute_action`, with the server's `call_tool` handler for a Ruleset mechanic.
//!
//! As for the listing ([`super::listing`]), there is no protocol layer: the client's steps are
//! run in Python's order against the server's handlers.
//! 1. **The revalidation:** a `call_tool_revalidation` listing (its own Full Audit record) must
//!    still hold the command's tool.
//! 2. **The client's own listing:** the fresh client lists the tools once more before calling,
//!    with no session and no record. Its owner reads are made; its tools are not used.
//! 3. **The server's `call_tool`:** it checks the request identity, finds the exact tool in the
//!    catalog and has the owner execute the command.
//!    - A Ruleset refusal of a castable spell is renamed `spell_unavailable` with the castable
//!      choices.
//!    - An `AuthorityError` becomes `_safe_error`'s correction contract, and a `TypeError` or
//!      `ValueError` the invalid-metadata contract (an error result, not an exception).
//! 4. **The client:** it records the `mcp_call_tool` Full Audit entry and reads the result back.
//!    - A refusal becomes a `MechanicsRejected` (or the typed ownership, writer, revision and
//!      idempotency errors) and is recorded as a mechanics diagnostic.
//!    - A success becomes the receipt.
//!
//! An exception the server does not catch reaches Python's caller inside the client's
//! exception groups, an internal fault ([`super::listing::listing_refusal`]'s shape).

use super::listing::{self, castable_spell_ids, listing_refusal, wire_input_schema};
use super::{safe_error, AuthorityFailure, AuthorityRefusal, ModelMechanicsTool};
use crate::provider_session::reads::TurnOwner;
use serde_json::{json, Value as Json};
use std::collections::BTreeSet;
use sylvia_app::authority::actions::ActionExecution;
use sylvia_base::{CodedError, JsonMap};
use sylvia_domain::authority::{
    ActionCommand, ActionReceipt, ActionStatus, AwaitingChoiceReceipt, MechanicsChoiceOption,
    MechanicsChoiceRequest,
};
use sylvia_domain::error::{DomainError, MechanicsDetails, MechanicsValidationIssue};
use sylvia_domain::pyops::py_str;

fn rejected(message: &str, code: &str) -> AuthorityFailure {
    CodedError::new(code, message).with_error_type("CommandRejected").into()
}

/// The first `limit` characters (code points), as Python's `text[:limit]`.
fn prefix(text: &str, limit: usize) -> String {
    text.chars().take(limit).collect()
}

/// `_validation_issues_from_payload(value)`: the bounded correction contract read back, without
/// accepting submitted values.
pub fn validation_issues_from_payload(value: Option<&Json>) -> Vec<MechanicsValidationIssue> {
    let Some(Json::Array(raw_issues)) = value else { return Vec::new() };
    let text = |raw: &JsonMap, key: &str, limit: usize| {
        raw.get(key).and_then(Json::as_str).map(|value| prefix(value, limit))
    };
    // `isinstance(value, int)`: a JSON integer, `true`/`false` included.
    let integer = |raw: &JsonMap, key: &str| -> Option<serde_json::Number> {
        match raw.get(key) {
            Some(Json::Number(number)) if number.is_i64() || number.is_u64() => Some(number.clone()),
            Some(Json::Bool(flag)) => Some(serde_json::Number::from(i64::from(*flag))),
            _ => None,
        }
    };
    let mut issues = Vec::new();
    for raw in raw_issues.iter().take(64) {
        let Json::Object(raw) = raw else { continue };
        let (Some(Json::String(path)), Some(Json::String(kind))) = (raw.get("path"), raw.get("kind")) else {
            continue;
        };
        let allowed_values = match raw.get("allowed_values") {
            Some(Json::Array(allowed)) => {
                allowed.iter().take(64).filter_map(Json::as_str).map(str::to_owned).collect()
            }
            _ => Vec::new(),
        };
        issues.push(MechanicsValidationIssue {
            path: prefix(path, 240),
            kind: prefix(kind, 80),
            expected_type: text(raw, "expected_type", 80),
            actual_type: text(raw, "actual_type", 80),
            parent_path: text(raw, "parent_path", 240),
            allowed_values,
            minimum: integer(raw, "minimum"),
            maximum: integer(raw, "maximum"),
            // `type(...) is int and >= 0`: never a boolean.
            actual_length: raw.get("actual_length").and_then(Json::as_i64).filter(|length| *length >= 0),
        });
    }
    issues
}

/// `_castable_spell_refusal(error, schema, castable_spell_ids)`: a refused spell outside the
/// castable choices, named with the castable spells the tool offers; `None` keeps the error.
pub fn castable_spell_refusal(
    message: &str,
    details: &MechanicsDetails,
    schema: &JsonMap,
    castable: &BTreeSet<String>,
) -> Option<DomainError> {
    let Some(Json::Object(properties)) = schema.get("properties") else { return None };
    let mut matched = false;
    let mut issues = Vec::new();
    for issue in &details.validation_issues {
        let mut issue = issue.clone();
        if let Some(Json::Object(child)) = properties.get(&issue.path) {
            if let (true, Some(&Json::Bool(true)), Some(Json::Array(choices))) = (
                issue.kind == "invalid_enum_value",
                child.get("x-sylvia-gm-castable-spells"),
                child.get("enum"),
            ) {
                issue.allowed_values = choices
                    .iter()
                    .filter_map(Json::as_str)
                    .filter(|value| castable.contains(*value))
                    .map(str::to_owned)
                    .collect();
                matched = true;
            }
        }
        issues.push(issue);
    }
    if !matched {
        return None;
    }
    let details = MechanicsDetails {
        field_paths: details.field_paths.clone(),
        validation_issues: issues,
        state_changed: details.state_changed,
        refusal_reasons: Vec::new(),
    };
    Some(DomainError::mechanics_rejected(message, "spell_unavailable", details).expect("no refusal reasons"))
}

/// `_meta_text(meta, name)`: a request identity, present and not padded.
fn meta_text(value: &str) -> Result<(), AuthorityFailure> {
    if value.is_empty() || value != sylvia_base::pystr::strip(value) {
        return Err(rejected("MCP request identity is invalid", "invalid_mcp_request_identity"));
    }
    Ok(())
}

/// The server's `call_tool` for a Ruleset mechanic, past the protocol check: the structured
/// result and whether it is an error result. `Err` is an exception the handler does not catch.
async fn server_call_tool(
    owner: &dyn TurnOwner,
    command: &ActionCommand,
    expected: &ModelMechanicsTool,
) -> Result<(JsonMap, bool), CodedError> {
    match call_tool_body(owner, command, expected).await {
        Ok(payload) => Ok((payload, false)),
        Err(failure) => {
            if let Some(refusal) = failure.refusal() {
                return Ok((safe_error(refusal), true));
            }
            let value_or_type = match &failure {
                AuthorityFailure::Domain(error) => {
                    error.is_value_error() || matches!(error, DomainError::Type(_))
                }
                AuthorityFailure::Coded(error) => {
                    matches!(error.error_type, Some("ValueError" | "TypeError"))
                }
            };
            if value_or_type {
                let metadata =
                    CodedError::new("invalid_mcp_request_metadata", "MCP request metadata is invalid")
                        .with_error_type("CommandRejected");
                return Ok((safe_error(AuthorityRefusal::of_coded(&metadata)), true));
            }
            Err(failure.into_coded())
        }
    }
}

/// The handler's `try` body for a mechanic.
async fn call_tool_body(
    owner: &dyn TurnOwner,
    command: &ActionCommand,
    expected: &ModelMechanicsTool,
) -> Result<JsonMap, AuthorityFailure> {
    meta_text(&command.campaign_id)?;
    for identity in
        [&expected.package_id, &expected.package_version, &expected.package_sha256, &expected.name]
    {
        meta_text(identity)?;
    }
    // `params.name != tool_name`: the client sends the tool's own name in both.
    let catalog = owner.catalog(&command.campaign_id, false).await?;
    let current: Vec<_> = catalog
        .iter()
        .filter(|tool| {
            (&tool.package_id, &tool.package_version, &tool.package_sha256, &tool.name)
                == (&expected.package_id, &expected.package_version, &expected.package_sha256, &expected.name)
        })
        .collect();
    if current.len() != 1 {
        return Err(rejected(
            "MCP tool is no longer available from the canonical catalog",
            "mcp_tool_unavailable",
        ));
    }
    for identity in [&command.action_id, &command.idempotency_key, &command.owner_id] {
        meta_text(identity)?;
    }
    // `WriterRole(...)` accepts the command's writer; `_meta_revision` refuses a negative one.
    if command.expected_revision < 0 {
        return Err(rejected("MCP expected revision is invalid", "invalid_mcp_expected_revision"));
    }
    meta_text(&command.turn_id)?;
    if let Some(session_id) = &command.initiating_session_id {
        meta_text(session_id)?;
    }
    let server_command = ActionCommand {
        package_id: expected.package_id.clone(),
        tool_name: expected.name.clone(),
        ..command.clone()
    };
    let execution = match owner.execute_action(&server_command).await {
        Ok(execution) => execution,
        Err(AuthorityFailure::Domain(error)) => {
            let Some(details) = error.mechanics().filter(|_| is_mechanics_rejected(&error)) else {
                return Err(AuthorityFailure::Domain(error));
            };
            if !details.validation_issues.iter().any(|issue| issue.kind == "invalid_enum_value") {
                return Err(AuthorityFailure::Domain(error));
            }
            let (visible, _boundary) = owner.model_context_snapshot(&command.campaign_id).await?;
            let castable = castable_spell_ids(&visible);
            return Err(AuthorityFailure::Domain(
                castable_spell_refusal(error.message(), details, &current[0].input_schema, &castable)
                    .unwrap_or(error),
            ));
        }
        Err(failure) => return Err(failure),
    };
    Ok(execution_payload(&execution))
}

/// `isinstance(error, MechanicsRejected)`, its subclass included.
fn is_mechanics_rejected(error: &DomainError) -> bool {
    matches!(error.python_class(), "MechanicsRejected" | "RulesetPreconditionRejected")
}

/// The handler's `action_payload`, in Python's key order.
fn execution_payload(execution: &ActionExecution) -> JsonMap {
    let value = match execution {
        ActionExecution::AwaitingChoice(receipt) => json!({
            "campaign_id": receipt.campaign_id,
            "action_id": receipt.action_id,
            "status": receipt.status(),
            "basis_revision": receipt.basis_revision,
            "choice_sequence": receipt.choice_sequence,
            "turn_id": receipt.turn_id,
            "choice": {
                "choice_id": receipt.choice.choice_id,
                "prompt": receipt.choice.prompt,
                "options": receipt.choice.options.iter()
                    .map(|option| json!({"option_id": option.option_id, "label": option.label}))
                    .collect::<Vec<_>>(),
            },
        }),
        ActionExecution::Receipt(receipt) => json!({
            "campaign_id": receipt.campaign_id,
            "action_id": receipt.action_id,
            "status": receipt.status.as_str(),
            "result": receipt.result,
            "resulting_revision": receipt.resulting_revision,
        }),
    };
    let Json::Object(payload) = value else { unreachable!("a JSON object literal") };
    payload
}

/// `StatelessMcpMechanics.execute_action(command)`: one mechanic executed through a fresh MCP
/// authority request, revalidated against the canonical catalog first. `ids` is `uuid4().hex`,
/// for the revalidation listing's discovery identity.
pub async fn execute_action(
    owner: &dyn TurnOwner,
    command: &ActionCommand,
    ids: &(dyn Fn() -> String + Send + Sync),
) -> Result<ActionExecution, AuthorityFailure> {
    let tools =
        listing::list_tools(owner, &command.campaign_id, "call_tool_revalidation", None, None, false, ids)
            .await?;
    let current: Vec<ModelMechanicsTool> = tools
        .into_iter()
        .filter(|tool| tool.package_id == command.package_id && tool.name == command.tool_name)
        .collect();
    if current.len() != 1 {
        return Err(rejected("MCP tool is unavailable from the canonical catalog", "mcp_tool_unavailable"));
    }
    let expected = &current[0];
    // The fresh client's own listing, before it calls the tool.
    for tool in
        listing::server_list_tools(owner, &command.campaign_id, None).await.map_err(listing_refusal)?
    {
        wire_input_schema(tool.input_schema)?;
    }
    let (payload, is_error) = server_call_tool(owner, command, expected).await.map_err(listing_refusal)?;
    let audit = json!({
        "kind": "mcp_call_tool",
        "campaign_id": command.campaign_id,
        "action_id": command.action_id,
        "turn_id": command.turn_id,
        "expected_revision": command.expected_revision,
        "package_id": expected.package_id,
        "package_version": expected.package_version,
        "package_sha256": expected.package_sha256,
        "tool_name": expected.name,
        "arguments": command.parameters,
        "result": payload,
        "is_error": is_error,
    });
    let Json::Object(audit) = audit else { unreachable!("a JSON object literal") };
    owner.record_admin_full_audit(audit)?;
    if is_error {
        return Err(refusal_from_payload(owner, command, expected, &payload)?);
    }
    receipt_from_payload(&payload)
}

/// The client's reading of an error result: the mechanics diagnostic it records, then the
/// error it raises (as `Ok`; `Err` is a failure while reading it).
fn refusal_from_payload(
    owner: &dyn TurnOwner,
    command: &ActionCommand,
    expected: &ModelMechanicsTool,
    payload: &JsonMap,
) -> Result<AuthorityFailure, AuthorityFailure> {
    let code = payload.get("code").map_or_else(|| "mcp_action_rejected".to_owned(), py_str);
    let validation = payload.get("validation").and_then(Json::as_object);
    let field_paths: Vec<String> = match validation.and_then(|validation| validation.get("field_paths")) {
        Some(Json::Array(paths)) => paths.iter().filter_map(Json::as_str).map(str::to_owned).collect(),
        _ => Vec::new(),
    };
    let state_changed = payload.get("state_changed") == Some(&Json::Bool(true));
    let diagnostic = json!({
        "kind": "mechanics_rejection",
        "privacy": "safe_metadata_only",
        "session_id": command.initiating_session_id,
        "turn_id": command.turn_id,
        "action_id": command.action_id,
        "tool_name": expected.name,
        "rejection_code": code,
        "validation_field_paths": field_paths,
        "state_changed": state_changed,
    });
    let Json::Object(diagnostic) = diagnostic else { unreachable!("a JSON object literal") };
    owner.record_mechanics_diagnostic(diagnostic)?;
    let typed = match code.as_str() {
        "idempotency_conflict" => Some(("IdempotencyConflict", "idempotency conflict")),
        "stale_revision" => Some(("StaleRevision", "stale revision")),
        "wrong_owner" => Some(("WrongOwner", "wrong owner")),
        "wrong_writer" => Some(("WrongWriter", "Command writer is not authoritative")),
        _ => None,
    };
    if let Some((class, message)) = typed {
        return Ok(
            DomainError::authority(class, format!("{message} rejected by MCP mechanics authority")).into()
        );
    }
    let message = payload
        .get("reason")
        .and_then(Json::as_str)
        .unwrap_or("Mechanics authority rejected the operation.")
        .to_owned();
    let issues = validation_issues_from_payload(validation.and_then(|validation| validation.get("issues")));
    // `_string_sequence(payload.get("reasons", ()))`.
    let refusal_reasons: Vec<String> = match payload.get("reasons") {
        None => Vec::new(),
        Some(Json::Array(reasons)) if reasons.iter().all(Json::is_string) => {
            reasons.iter().filter_map(Json::as_str).map(str::to_owned).collect()
        }
        Some(_) => return Err(rejected("Campaign narrative options are invalid", "command_rejected")),
    };
    let details = MechanicsDetails { field_paths, validation_issues: issues, state_changed, refusal_reasons };
    // Constructing it raises `ValueError` when a reason is not model-safe.
    let (Ok(error) | Err(error)) = DomainError::mechanics_rejected(message, code, details);
    Ok(error.into())
}

/// The client's reading of a success result.
fn receipt_from_payload(payload: &JsonMap) -> Result<ActionExecution, AuthorityFailure> {
    let text = |key: &str| payload.get(key).map(py_str);
    // `int(payload[key])` for the integers the handler wrote.
    let integer = |key: &str| payload.get(key).and_then(Json::as_i64);
    if payload.get("status").and_then(Json::as_str) == Some("awaiting_choice") {
        let invalid = || rejected("MCP mechanics choice receipt is invalid", "command_rejected");
        let choice = payload.get("choice").and_then(Json::as_object).ok_or_else(invalid)?;
        let options = choice.get("options").and_then(Json::as_array).ok_or_else(invalid)?;
        let option = |value: &JsonMap| -> Option<MechanicsChoiceOption> {
            Some(MechanicsChoiceOption {
                option_id: py_str(value.get("option_id")?),
                label: py_str(value.get("label")?),
            })
        };
        let options: Vec<MechanicsChoiceOption> = options
            .iter()
            .filter_map(Json::as_object)
            .map(option)
            .collect::<Option<_>>()
            .ok_or_else(invalid)?;
        return Ok(ActionExecution::AwaitingChoice(AwaitingChoiceReceipt {
            campaign_id: text("campaign_id").ok_or_else(invalid)?,
            action_id: text("action_id").ok_or_else(invalid)?,
            basis_revision: integer("basis_revision").ok_or_else(invalid)?,
            choice_sequence: payload
                .get("choice_sequence")
                .map_or(Some(0), Json::as_i64)
                .ok_or_else(invalid)?,
            turn_id: text("turn_id").ok_or_else(invalid)?,
            choice: MechanicsChoiceRequest {
                choice_id: choice.get("choice_id").map(py_str).ok_or_else(invalid)?,
                prompt: choice.get("prompt").map(py_str).ok_or_else(invalid)?,
                options,
            },
        }));
    }
    let Some(Json::Object(result)) = payload.get("result") else {
        return Err(rejected("MCP action result is invalid", "command_rejected"));
    };
    let invalid = || rejected("MCP action receipt is invalid", "command_rejected");
    Ok(ActionExecution::Receipt(ActionReceipt {
        campaign_id: text("campaign_id").ok_or_else(invalid)?,
        action_id: text("action_id").ok_or_else(invalid)?,
        status: payload
            .get("status")
            .map(py_str)
            .and_then(|status| ActionStatus::parse(&status))
            .ok_or_else(invalid)?,
        result: result.clone(),
        resulting_revision: integer("resulting_revision").ok_or_else(invalid)?,
    }))
}
