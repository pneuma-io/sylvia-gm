//! `agent/_stateless_mcp.py`'s Campaign File update and its reference reads:
//! `StatelessMcpMechanics.commit_campaign_file`, `search_knowledge` and `read_reference`, with
//! the server's `call_tool` branches for `update_campaign_file`, `search_campaign_knowledge` and
//! `read_campaign_reference`.
//!
//! As for the listing and the mechanics call, there is no protocol layer: each client lists the
//! tools once (its owner reads made, its tools unused), then calls the server's handler.
//! - **The Campaign File update** validates the model's whole batch before any write
//!   (`_campaign_file_updates`, over `_narrative_shape_issues` and `_schema_issue`), then has the
//!   owner commit it (lane 3's `update_campaign_file`). A batch with no changes reads only the
//!   current revision. Its update identity is the SHA-256 of the canonical request.
//! - **The reference read and knowledge search** check their arguments, then ask the owner.
//! - An `AuthorityError` is `_safe_error`'s contract, a `TypeError` or `ValueError` the
//!   invalid-metadata one, and any other exception the client's exception group
//!   ([`super::listing::listing_refusal`]). The client records its Full Audit entry, then raises
//!   a refusal: the Campaign File one as a `MechanicsRejected` rebuilt from the contract, the
//!   reads as a `CommandRejected` naming the contract's text.
//!
//! The per-operation narrative tools (`_NARRATIVE_OPERATIONS_BY_TOOL`,
//! `commit_narrative_state`) are not ported: nothing calls `commit_narrative_state`, and the
//! listing never offers those tools.

use super::execute::validation_issues_from_payload;
use super::listing::{
    campaign_file_update_schema, listing_refusal, narrative_input_schema, server_list_tools,
    wire_input_schema,
};
use super::{safe_error, tool_result_text, AuthorityFailure, AuthorityRefusal};
use crate::provider_session::reads::TurnOwner;
use serde_json::{json, Value as Json};
use std::collections::BTreeSet;
use sylvia_base::{pystr, CodedError, CodedResult, JsonMap};
use sylvia_domain::error::{DomainError, MechanicsDetails, MechanicsValidationIssue};
use sylvia_domain::pyops::{py_eq, py_str};

/// `_CAMPAIGN_FILE_STATE_CHANGE`.
const STATE_CHANGE: &str = "state_change";
/// `_CAMPAIGN_FILE_STATE_CHANGE_SUMMARY`.
const STATE_CHANGE_SUMMARY: &str = "state_change_summary";

fn rejected(message: impl Into<String>, code: &str) -> AuthorityFailure {
    CodedError::new(code, message).with_error_type("CommandRejected").into()
}

fn object(pairs: Vec<(&str, Json)>) -> JsonMap {
    pairs.into_iter().map(|(key, value)| (key.to_owned(), value)).collect()
}

/// `_json_type_name(value)`.
fn json_type_name(value: &Json) -> &'static str {
    match value {
        Json::Null => "null",
        Json::Bool(_) => "boolean",
        Json::String(_) => "string",
        Json::Number(number) if number.is_f64() => "number",
        Json::Number(_) => "integer",
        Json::Object(_) => "object",
        Json::Array(_) => "array",
    }
}

/// `_parent_path(path)`.
fn parent_path(path: &str) -> String {
    if let Some((parent, _)) = path.rsplit_once('.') {
        return parent.to_owned();
    }
    if path.contains('[') && path.ends_with(']') {
        if let Some((parent, _)) = path.rsplit_once('[') {
            return parent.to_owned();
        }
    }
    "$".to_owned()
}

/// `isinstance(value, int)` for a schema bound: a JSON integer.
fn bound(schema: &JsonMap, names: [&str; 2]) -> Option<serde_json::Number> {
    names.into_iter().find_map(|name| match schema.get(name) {
        Some(Json::Number(number)) if !number.is_f64() => Some(number.clone()),
        _ => None,
    })
}

/// `_schema_issue(path=..., kind=..., schema=..., actual=..., parent_path=...)`: one bounded
/// correction issue from schema facts and the value's type only.
pub fn schema_issue(
    path: &str,
    kind: &str,
    schema: Option<&JsonMap>,
    actual: Option<&Json>,
    parent: Option<&str>,
) -> MechanicsValidationIssue {
    let expected_type =
        schema.and_then(|schema| schema.get("type")).and_then(Json::as_str).map(str::to_owned);
    let allowed_values = match schema.and_then(|schema| schema.get("enum")) {
        Some(Json::Array(choices)) => choices.iter().map(py_str).collect(),
        _ => Vec::new(),
    };
    let (minimum, maximum) = match schema {
        Some(schema) => (bound(schema, ["minLength", "minItems"]), bound(schema, ["maxLength", "maxItems"])),
        None => (None, None),
    };
    let actual_length = match actual {
        Some(Json::String(text)) if matches!(kind, "string_too_short" | "string_too_long") => {
            Some(text.chars().count() as i64)
        }
        _ => None,
    };
    MechanicsValidationIssue {
        path: path.to_owned(),
        kind: kind.to_owned(),
        expected_type,
        actual_type: actual.map(|value| json_type_name(value).to_owned()),
        parent_path: Some(
            parent.filter(|parent| !parent.is_empty()).map_or_else(|| parent_path(path), str::to_owned),
        ),
        allowed_values,
        minimum,
        maximum,
        actual_length,
    }
}

/// `sorted(issues, key=lambda issue: (issue.path, issue.kind))`.
fn sort_issues(issues: &mut [MechanicsValidationIssue]) {
    issues.sort_by(|left, right| (&left.path, &left.kind).cmp(&(&right.path, &right.kind)));
}

/// `f"{prefix}.{name}" if prefix else name`.
fn child_path(prefix: &str, name: &str) -> String {
    if prefix.is_empty() {
        name.to_owned()
    } else {
        format!("{prefix}.{name}")
    }
}

/// `len(value)` against an integer bound: a code-point count.
fn below(value: &str, minimum: &Json) -> bool {
    minimum.as_i64().is_some_and(|minimum| (value.chars().count() as i64) < minimum)
}

fn above(value: &str, maximum: &Json) -> bool {
    maximum.as_i64().is_some_and(|maximum| (value.chars().count() as i64) > maximum)
}

/// `_narrative_shape_issues(arguments, schema, path_prefix=...)`: every schema violation, with
/// enough safe detail for one correction, sorted by path then kind.
pub fn narrative_shape_issues(
    arguments: &JsonMap,
    schema: &JsonMap,
    path_prefix: &str,
) -> CodedResult<Vec<MechanicsValidationIssue>> {
    let invalid =
        || CodedError::rejected("Campaign narrative schema is invalid").with_error_type("CommandRejected");
    let Some(Json::Object(properties)) = schema.get("properties") else { return Err(invalid()) };
    // `isinstance(required, Sequence)`: a list, or text (its characters).
    let required: Vec<String> = match schema.get("required") {
        Some(Json::Array(names)) => names.iter().map(py_str).collect(),
        Some(Json::String(text)) => text.chars().map(String::from).collect(),
        _ => return Err(invalid()),
    };
    let parent = if path_prefix.is_empty() { "$" } else { path_prefix };
    let mut missing: Vec<&String> =
        required.iter().filter(|name| !arguments.contains_key(name.as_str())).collect();
    missing.sort();
    let foreign: BTreeSet<&String> =
        arguments.keys().filter(|name| !properties.contains_key(name.as_str())).collect();
    let mut issues = Vec::new();
    for name in missing {
        let property = properties.get(name).and_then(Json::as_object);
        issues.push(schema_issue(
            &child_path(path_prefix, name),
            "missing_required_field",
            property,
            None,
            Some(parent),
        ));
    }
    for name in foreign {
        issues.push(schema_issue(
            &child_path(path_prefix, name),
            "unexpected_field",
            None,
            arguments.get(name),
            Some(parent),
        ));
    }
    for (name, value) in arguments {
        let Some(Json::Object(property)) = properties.get(name) else { continue };
        let path = child_path(path_prefix, name);
        let invalid_type = match property.get("type").and_then(Json::as_str) {
            Some("string") => match value {
                Json::String(text) => {
                    if property.get("minLength").is_some_and(|minimum| below(text, minimum)) {
                        issues.push(schema_issue(
                            &path,
                            "string_too_short",
                            Some(property),
                            Some(value),
                            None,
                        ));
                        continue;
                    }
                    if property.get("maxLength").is_some_and(|maximum| above(text, maximum)) {
                        issues.push(schema_issue(
                            &path,
                            "string_too_long",
                            Some(property),
                            Some(value),
                            None,
                        ));
                        continue;
                    }
                    false
                }
                _ => true,
            },
            Some("array") => match value {
                Json::Array(items) => {
                    let item_schema = property.get("items").and_then(Json::as_object);
                    if item_schema
                        .is_some_and(|schema| schema.get("type").and_then(Json::as_str) == Some("string"))
                    {
                        let mut item_type_invalid = false;
                        for (index, item) in items.iter().enumerate() {
                            if !item.is_string() {
                                item_type_invalid = true;
                                issues.push(schema_issue(
                                    &format!("{path}[{index}]"),
                                    "wrong_type",
                                    item_schema,
                                    Some(item),
                                    Some(&path),
                                ));
                            }
                        }
                        if item_type_invalid {
                            continue;
                        }
                    }
                    false
                }
                _ => true,
            },
            Some("object") => !value.is_object(),
            _ => false,
        };
        if invalid_type {
            issues.push(schema_issue(&path, "wrong_type", Some(property), Some(value), None));
            continue;
        }
        // `value not in enum`: Python's equality.
        if let Some(Json::Array(choices)) = property.get("enum") {
            if !choices.iter().any(|choice| py_eq(value, choice)) {
                issues.push(schema_issue(&path, "invalid_enum_value", Some(property), Some(value), None));
            }
        }
    }
    sort_issues(&mut issues);
    Ok(issues)
}

/// A `MechanicsRejected` of Campaign File validation (never a state change).
fn refusal(message: &str, code: &str, issues: Vec<MechanicsValidationIssue>) -> AuthorityFailure {
    let mut field_paths: Vec<String> = Vec::new();
    for issue in &issues {
        if !field_paths.contains(&issue.path) {
            field_paths.push(issue.path.clone());
        }
    }
    let details = MechanicsDetails {
        field_paths,
        validation_issues: issues,
        state_changed: false,
        refusal_reasons: Vec::new(),
    };
    DomainError::mechanics_rejected(message, code, details).expect("no refusal reasons").into()
}

/// `_campaign_file_updates(arguments, options)`: the model's batch validated completely before
/// any Campaign File write begins; each update is its operation and the item's fields.
pub fn campaign_file_updates(
    arguments: &JsonMap,
    options: &JsonMap,
) -> Result<Vec<JsonMap>, AuthorityFailure> {
    let schema = campaign_file_update_schema(options)?;
    let properties = schema["properties"].as_object().expect("the update schema's properties");
    let disposition_schema = object(vec![
        ("type", json!("object")),
        (
            "properties",
            json!({STATE_CHANGE: properties[STATE_CHANGE], STATE_CHANGE_SUMMARY: properties[STATE_CHANGE_SUMMARY]}),
        ),
        ("required", json!([STATE_CHANGE, STATE_CHANGE_SUMMARY])),
        ("additionalProperties", json!(false)),
    ]);
    let disposition_arguments: JsonMap = [STATE_CHANGE, STATE_CHANGE_SUMMARY]
        .into_iter()
        .filter_map(|name| arguments.get(name).map(|value| (name.to_owned(), value.clone())))
        .collect();
    let mut issues = narrative_shape_issues(&disposition_arguments, &disposition_schema, "")?;
    let foreign: BTreeSet<&String> =
        arguments.keys().filter(|name| !properties.contains_key(name.as_str())).collect();
    for name in foreign {
        issues.push(schema_issue(name, "unexpected_field", None, arguments.get(name), Some("$")));
    }
    let collections = super::listing::campaign_file_collections();
    let mut updates = Vec::new();
    for (collection, raw_items) in arguments {
        if collection == STATE_CHANGE || collection == STATE_CHANGE_SUMMARY {
            continue;
        }
        let Some(Json::Object(collection_schema)) = properties.get(collection) else { continue };
        let Json::Array(items) = raw_items else {
            issues.push(schema_issue(
                collection,
                "wrong_type",
                Some(collection_schema),
                Some(raw_items),
                Some("$"),
            ));
            continue;
        };
        if items.is_empty() {
            issues.push(schema_issue(
                collection,
                "empty_collection",
                Some(collection_schema),
                Some(raw_items),
                Some("$"),
            ));
            continue;
        }
        if items.len() > 64 {
            issues.push(schema_issue(
                collection,
                "collection_too_long",
                Some(collection_schema),
                Some(raw_items),
                Some("$"),
            ));
            continue;
        }
        let operation = collections
            .iter()
            .find(|(_, name)| name == collection)
            .map(|(operation, _)| operation.clone())
            .expect("an offered collection's operation");
        let item_schema = narrative_input_schema(&operation, options)?;
        let item_schema = item_schema.as_object().cloned().unwrap_or_default();
        for (index, raw_item) in items.iter().enumerate() {
            let path = format!("{collection}[{index}]");
            let Json::Object(item) = raw_item else {
                let object_schema = object(vec![("type", json!("object"))]);
                issues.push(schema_issue(
                    &path,
                    "wrong_type",
                    Some(&object_schema),
                    Some(raw_item),
                    Some(collection),
                ));
                continue;
            };
            let item_issues = narrative_shape_issues(item, &item_schema, &path)?;
            if !item_issues.is_empty() {
                issues.extend(item_issues);
                continue;
            }
            let mut update = object(vec![("operation", json!(operation))]);
            update.extend(item.iter().map(|(key, value)| (key.clone(), value.clone())));
            updates.push(update);
        }
    }
    if !issues.is_empty() {
        sort_issues(&mut issues);
        return Err(refusal(
            "Campaign File parameters do not match the exposed update schema",
            "campaign_file_update_invalid",
            issues,
        ));
    }
    let state_change = &arguments[STATE_CHANGE];
    let inconsistent = (py_eq(state_change, &json!("changed")) && updates.is_empty())
        || (py_eq(state_change, &json!("unchanged")) && !updates.is_empty());
    if inconsistent {
        let issue = MechanicsValidationIssue {
            path: STATE_CHANGE.into(),
            kind: "inconsistent_disposition".into(),
            expected_type: Some("string".into()),
            actual_type: Some("string".into()),
            parent_path: Some("$".into()),
            allowed_values: vec!["changed".into(), "unchanged".into()],
            ..Default::default()
        };
        return Err(refusal(
            "Campaign File state assessment contradicts the supplied collections",
            "campaign_file_disposition_inconsistent",
            vec![issue],
        ));
    }
    if updates.len() > 64 {
        let issue = MechanicsValidationIssue {
            path: "$".into(),
            kind: "update_too_large".into(),
            expected_type: Some("object".into()),
            actual_type: Some("object".into()),
            parent_path: Some("$".into()),
            maximum: Some(64.into()),
            ..Default::default()
        };
        return Err(refusal(
            "Campaign File update contains too many changes",
            "campaign_file_update_too_large",
            vec![issue],
        ));
    }
    Ok(updates)
}

/// A server handler's `try` body outcome, as `call_tool` returns it: the structured result and
/// whether it is an error result. `Err` is an exception the handler does not catch.
fn handled(outcome: Result<JsonMap, AuthorityFailure>) -> Result<(JsonMap, bool), CodedError> {
    match outcome {
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

/// `_meta_text(meta, name)`: a request identity, present and not padded.
fn meta_text(value: &str) -> Result<(), AuthorityFailure> {
    if value.is_empty() || value != pystr::strip(value) {
        return Err(rejected("MCP request identity is invalid", "invalid_mcp_request_identity"));
    }
    Ok(())
}

/// The fresh client's own listing before it calls a tool: its owner reads, its tools unused.
async fn client_listing(
    owner: &dyn TurnOwner,
    campaign_id: &str,
    session_id: Option<&str>,
) -> Result<(), AuthorityFailure> {
    for tool in server_list_tools(owner, campaign_id, session_id).await.map_err(listing_refusal)? {
        wire_input_schema(tool.input_schema)?;
    }
    Ok(())
}

/// The identity of one Campaign File request.
pub struct CampaignFileRequest<'a> {
    pub campaign_id: &'a str,
    pub session_id: &'a str,
    pub basis_message_id: &'a str,
    pub turn_id: &'a str,
    pub update_id: &'a str,
}

/// The server's `call_tool` for `update_campaign_file`, past the protocol check.
async fn server_update_campaign_file(
    owner: &dyn TurnOwner,
    request: &CampaignFileRequest<'_>,
    parameters: &JsonMap,
) -> Result<JsonMap, AuthorityFailure> {
    meta_text(request.campaign_id)?;
    // `_meta_text(meta, "tool_name")`: the client names the tool it calls.
    for identity in [request.session_id, request.basis_message_id, request.update_id, request.turn_id] {
        meta_text(identity)?;
    }
    let options = owner.campaign_narrative_options(request.session_id).await?;
    let updates = campaign_file_updates(parameters, &options)?;
    if updates.is_empty() {
        let current = owner.snapshot(request.campaign_id).await?;
        return Ok(object(vec![
            ("status", json!("campaign_file_unchanged")),
            ("update_id", json!(request.update_id)),
            ("campaign_revision", json!(current.state.revision)),
            ("state_changed", json!(false)),
        ]));
    }
    owner
        .update_campaign_file(
            request.session_id,
            request.update_id,
            updates,
            vec![request.basis_message_id.to_owned()],
            Some(request.turn_id),
        )
        .await
}

/// `StatelessMcpMechanics.commit_campaign_file(campaign_id, session_id=..., basis_message_id=...,
/// parameters=..., turn_id=...)`: one replay-safe Campaign File batch the active GM selected,
/// committed through a fresh MCP authority request.
pub async fn commit_campaign_file(
    owner: &dyn TurnOwner,
    campaign_id: &str,
    session_id: &str,
    basis_message_id: &str,
    parameters: &JsonMap,
    turn_id: Option<&str>,
) -> Result<JsonMap, AuthorityFailure> {
    let resolved = match turn_id.filter(|turn_id| !turn_id.is_empty()) {
        Some(turn_id) => Some(turn_id.to_owned()),
        None => owner.current_open_turn(campaign_id).await?,
    };
    let Some(turn_id) = resolved else {
        return Err(rejected("Campaign File update requires an open TURN", "campaign_turn_unavailable"));
    };
    let identity = json!({
        "campaign_id": campaign_id,
        "session_id": session_id,
        "basis_message_id": basis_message_id,
        "turn_id": turn_id,
        "parameters": parameters,
    });
    let encoded = sylvia_base::pyjson::dumps(&identity, sylvia_base::pyjson::Dump::CANONICAL);
    let digest = sylvia_base::hash::sha256_hex(encoded.as_bytes());
    let update_id = format!("campaign-file-{}", &digest[..32]);
    client_listing(owner, campaign_id, Some(session_id)).await?;
    let request = CampaignFileRequest {
        campaign_id,
        session_id,
        basis_message_id,
        turn_id: &turn_id,
        update_id: &update_id,
    };
    let (payload, is_error) =
        handled(server_update_campaign_file(owner, &request, parameters).await).map_err(listing_refusal)?;
    owner.record_admin_full_audit(object(vec![
        ("kind", json!("mcp_update_campaign_file")),
        ("campaign_id", json!(campaign_id)),
        ("session_id", json!(session_id)),
        ("basis_message_id", json!(basis_message_id)),
        ("turn_id", json!(turn_id)),
        ("update_id", json!(update_id)),
        ("parameters", Json::Object(parameters.clone())),
        ("result", Json::Object(payload.clone())),
        ("is_error", json!(is_error)),
    ]))?;
    if is_error {
        let code = payload.get("code").map_or_else(|| "mcp_campaign_file_update_rejected".to_owned(), py_str);
        let validation = payload.get("validation").and_then(Json::as_object);
        let field_paths: Vec<String> = match validation.and_then(|validation| validation.get("field_paths")) {
            Some(Json::Array(paths)) => paths.iter().filter_map(Json::as_str).map(str::to_owned).collect(),
            _ => Vec::new(),
        };
        let message = payload
            .get("reason")
            .and_then(Json::as_str)
            .unwrap_or("Campaign File authority rejected the update.")
            .to_owned();
        let issues =
            validation_issues_from_payload(validation.and_then(|validation| validation.get("issues")));
        // `_string_sequence(payload.get("reasons", ()))`.
        let refusal_reasons: Vec<String> = match payload.get("reasons") {
            None => Vec::new(),
            Some(Json::Array(reasons)) if reasons.iter().all(Json::is_string) => {
                reasons.iter().filter_map(Json::as_str).map(str::to_owned).collect()
            }
            Some(_) => return Err(rejected("Campaign narrative options are invalid", "command_rejected")),
        };
        let details = MechanicsDetails {
            field_paths,
            validation_issues: issues,
            state_changed: false,
            refusal_reasons,
        };
        // Constructing it raises `ValueError` when a reason is not model-safe.
        let (Ok(error) | Err(error)) = DomainError::mechanics_rejected(message, code, details);
        return Err(error.into());
    }
    Ok(payload)
}

/// The server's `call_tool` for `search_campaign_knowledge`, past the protocol check.
async fn server_search_knowledge(
    owner: &dyn TurnOwner,
    campaign_id: &str,
    arguments: &JsonMap,
) -> Result<JsonMap, AuthorityFailure> {
    meta_text(campaign_id)?;
    let invalid = || rejected("MCP knowledge search parameters are invalid", "command_rejected");
    if !arguments.keys().all(|key| matches!(key.as_str(), "query" | "sources" | "limit" | "offset")) {
        return Err(invalid());
    }
    let query = arguments.get("query").cloned().unwrap_or(Json::Null);
    let sources = arguments.get("sources").cloned().unwrap_or(Json::Null);
    let limit = arguments.get("limit").cloned().unwrap_or_else(|| json!(5));
    let offset = arguments.get("offset").cloned().unwrap_or_else(|| json!(0));
    let integer = |value: &Json| value.as_i64().is_some() || value.as_u64().is_some();
    let sources_ok = match &sources {
        Json::Null => true,
        Json::Array(items) => items.iter().all(Json::is_string),
        _ => false,
    };
    if !query.is_string() || !sources_ok || !integer(&limit) || !integer(&offset) {
        return Err(invalid());
    }
    let sources = (!sources.is_null()).then_some(sources);
    owner.search_campaign_knowledge(campaign_id, &query, sources.as_ref(), &limit, &offset).await
}

/// The server's `call_tool` for `read_campaign_reference`, past the protocol check.
async fn server_read_reference(
    owner: &dyn TurnOwner,
    campaign_id: &str,
    arguments: &JsonMap,
) -> Result<JsonMap, AuthorityFailure> {
    meta_text(campaign_id)?;
    let keys: BTreeSet<&str> = arguments.keys().map(String::as_str).collect();
    if keys != BTreeSet::from(["source", "path"]) {
        return Err(rejected("MCP reference parameters are invalid", "command_rejected"));
    }
    let (source, path) = (&arguments["source"], &arguments["path"]);
    if !source.is_string() || !path.is_string() {
        return Err(rejected("MCP reference parameters are invalid", "command_rejected"));
    }
    owner.read_campaign_reference(campaign_id, source, path).await
}

/// A read's refusal: `CommandRejected(f"{prefix}: {detail[:500]}")`, where the detail is the
/// error result's text.
fn read_refusal(prefix: &str, payload: &JsonMap) -> AuthorityFailure {
    let text = tool_result_text(payload);
    let detail: String =
        if text.is_empty() { "no structured result".into() } else { text.chars().take(500).collect() };
    rejected(format!("{prefix}: {detail}"), "command_rejected")
}

/// `StatelessMcpMechanics.search_knowledge(campaign_id, query=..., sources=..., limit=...,
/// offset=..., session_id=...)`: bounded campaign references, discovered through a fresh MCP
/// request.
pub async fn search_knowledge(
    owner: &dyn TurnOwner,
    campaign_id: &str,
    query: &str,
    sources: Option<&[String]>,
    limit: i64,
    offset: i64,
    session_id: Option<&str>,
) -> Result<JsonMap, AuthorityFailure> {
    let mut arguments =
        object(vec![("query", json!(query)), ("limit", json!(limit)), ("offset", json!(offset))]);
    if let Some(sources) = sources {
        arguments.insert("sources".into(), json!(sources));
    }
    client_listing(owner, campaign_id, session_id).await?;
    let (payload, is_error) =
        handled(server_search_knowledge(owner, campaign_id, &arguments).await).map_err(listing_refusal)?;
    owner.record_admin_full_audit(object(vec![
        ("kind", json!("mcp_search_campaign_knowledge")),
        ("campaign_id", json!(campaign_id)),
        ("query", json!(query)),
        ("sources", sources.map_or(Json::Null, |sources| json!(sources))),
        ("limit", json!(limit)),
        ("offset", json!(offset)),
        ("result", Json::Object(payload.clone())),
        ("is_error", json!(is_error)),
    ]))?;
    if is_error {
        return Err(read_refusal("MCP campaign knowledge search was rejected", &payload));
    }
    Ok(payload)
}

/// `StatelessMcpMechanics.read_reference(campaign_id, source=..., path=..., session_id=...)`: one
/// exact campaign reference, read through a fresh MCP request.
pub async fn read_reference(
    owner: &dyn TurnOwner,
    campaign_id: &str,
    source: &str,
    path: &str,
    session_id: Option<&str>,
) -> Result<JsonMap, AuthorityFailure> {
    client_listing(owner, campaign_id, session_id).await?;
    let arguments = object(vec![("source", json!(source)), ("path", json!(path))]);
    let (payload, is_error) =
        handled(server_read_reference(owner, campaign_id, &arguments).await).map_err(listing_refusal)?;
    owner.record_admin_full_audit(object(vec![
        ("kind", json!("mcp_read_campaign_reference")),
        ("campaign_id", json!(campaign_id)),
        ("source", json!(source)),
        ("path", json!(path)),
        ("result", Json::Object(payload.clone())),
        ("is_error", json!(is_error)),
    ]))?;
    if is_error {
        return Err(read_refusal("MCP campaign reference read was rejected", &payload));
    }
    Ok(payload)
}
