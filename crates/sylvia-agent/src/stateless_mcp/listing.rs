//! `agent/_stateless_mcp.py`'s tool listing: the server's `list_tools` handler and
//! `StatelessMcpMechanics.list_tools`, which reads it back as [`ModelMechanicsTool`]s and records
//! the `mcp_list_tools` Full Audit entry.
//!
//! Python asks a fresh in-process MCP server and converts its `types.Tool`s; the port has no
//! protocol layer, so [`list_tools`] builds the converted tools directly, with the values the
//! conversion gives (every tool's `parameter_derivations` is a mapping, `category` defaults to
//! `mechanic`, `model_behavior` to optional), and each input schema as the protocol's wire model
//! carries it ([`wire_input_schema`]). The server turns an `AuthorityError` into an
//! `MCPError` (its message, the code in `data`), and the SDK's in-process client hands that to
//! the caller inside two `ExceptionGroup`s (anyio's task groups): the caller sees the class
//! `ExceptionGroup` and the message `unhandled errors in a TaskGroup (1 sub-exception)`. That is
//! no `AuthorityError`, so nothing turns it into a tool result, a Full Audit record or a coded
//! reply: the owner answers its fixed `internal_error` text and local diagnostics records
//! `error_code` `internal_error` with `error_type` `ExceptionGroup`. The Rust error is that
//! internal fault: code `internal_error`, class `ExceptionGroup`, Python's message.
//!
//! Only the legacy listing is ported: the fixed list (`fixed_tool_list`, the context ledger's
//! catalog) answers `rust_port_pending` until `_fixed_tool_projection.py` is (L4).
//!
//! The model-read texts and schemas (the reference tools, the Campaign File tool and its note
//! types) are read from the Python source into `listing_texts.json` by
//! `generate_listing_texts.py`; only the request-dependent parts are filled here.

use super::ModelMechanicsTool;
use crate::provider_session::reads::TurnOwner;
use crate::tool_exposure::{
    exposure_spell_ids, scoped_spell_choices, ExposureContext, EXPOSURE_KEY, SPELL_SCOPE_KEY,
};
use serde_json::{json, Value as Json};
use std::collections::BTreeSet;
use std::sync::OnceLock;
use sylvia_base::{CodedError, CodedResult, JsonMap};
use sylvia_domain::authority::{CampaignDisplaySnapshot, ModelToolBehavior, TurnStatus};
use sylvia_domain::pyops::{py_str, py_truthy};
use sylvia_provider::factory::PORT_PENDING;

/// `_KNOWLEDGE_SEARCH_TOOL_NAME`.
pub const KNOWLEDGE_SEARCH_TOOL_NAME: &str = "search_campaign_knowledge";
/// `_REFERENCE_TOOL_NAME`.
pub const REFERENCE_TOOL_NAME: &str = "read_campaign_reference";
/// `_CAMPAIGN_FILE_UPDATE_TOOL_NAME`.
pub const CAMPAIGN_FILE_UPDATE_TOOL_NAME: &str = "update_campaign_file";
/// `_REFERENCE_PACKAGE_ID`: the application's own tools' package.
pub const REFERENCE_PACKAGE_ID: &str = "sylvia.application";

/// One tool of `AuthorityOwner.catalog(campaign_id)`: the `CatalogTool` fields the listing reads.
#[derive(Debug, Clone, PartialEq)]
pub struct CatalogEntry {
    pub package_id: String,
    pub package_version: String,
    pub package_sha256: String,
    pub name: String,
    pub capability: String,
    pub input_schema: JsonMap,
    pub description: String,
    pub model_behavior: ModelToolBehavior,
}

/// `listing_texts.json`.
fn texts() -> &'static Json {
    static TEXTS: OnceLock<Json> = OnceLock::new();
    TEXTS.get_or_init(|| {
        sylvia_base::pyjson::loads(include_str!("listing_texts.json")).expect("the exported listing texts")
    })
}

fn exported(path: &[&str]) -> &'static Json {
    path.iter().fold(texts(), |value, key| &value[*key])
}

fn exported_map(path: &[&str]) -> JsonMap {
    exported(path).as_object().cloned().expect("an exported object")
}

/// `value` with every exported placeholder string `marker` replaced by `with`.
fn filled(value: &mut Json, marker: &str, with: &Json) {
    match value {
        Json::String(text) if text == marker => *value = with.clone(),
        Json::Object(map) => map.values_mut().for_each(|child| filled(child, marker, with)),
        Json::Array(items) => items.iter_mut().for_each(|child| filled(child, marker, with)),
        _ => {}
    }
}

fn rejected(message: &str) -> CodedError {
    CodedError::rejected(message).with_error_type("CommandRejected")
}

/// `_model_input_schema(schema, participant_ids=..., castable_spell_ids=...)` for the legacy
/// list: the model's contract and the authority-owned argument derivations.
pub fn model_input_schema(
    schema: &JsonMap,
    participant_ids: &[String],
    castable_spell_ids: Option<&BTreeSet<String>>,
) -> CodedResult<(JsonMap, JsonMap)> {
    let mut projected = schema.clone();
    let Some(Json::Object(raw_properties)) = projected.get("properties") else {
        return Err(rejected("Catalog input schema properties are invalid"));
    };
    let mut properties = JsonMap::new();
    let mut derivations = JsonMap::new();
    let mut model_required: BTreeSet<String> = BTreeSet::new();
    for (name, raw_schema) in raw_properties {
        let Json::Object(child) = raw_schema else {
            return Err(rejected("Catalog input schema property is invalid"));
        };
        let mut child = child.clone();
        let derivation = child.shift_remove("x-sylvia-gm-derived").unwrap_or(Json::Null);
        let hidden = child.shift_remove("x-sylvia-gm-model-hidden");
        let required_for_model = child.shift_remove("x-sylvia-gm-model-required");
        if !derivation.is_null() {
            let Json::Object(derivation) = derivation else {
                return Err(rejected("Catalog model derivation is invalid"));
            };
            derivations.insert(name.clone(), Json::Object(derivation));
            continue;
        }
        // `hidden is True`: only the JSON `true`.
        if hidden == Some(Json::Bool(true)) {
            continue;
        }
        if required_for_model == Some(Json::Bool(true)) {
            model_required.insert(name.clone());
        }
        if child.get("x-sylvia-gm-reference").and_then(Json::as_str) == Some("participant") {
            child.insert("enum".into(), participant_ids.iter().cloned().map(Json::from).collect());
        }
        let spell_scope = child.shift_remove(SPELL_SCOPE_KEY);
        if child.get("x-sylvia-gm-castable-spells") == Some(&Json::Bool(true)) {
            if let Some(Json::Array(choices)) = child.get("enum") {
                let scoped = scoped_spell_choices(
                    choices,
                    castable_spell_ids,
                    spell_scope.as_ref(),
                    child.get("x-sylvia-gm-value-aliases"),
                );
                child.insert("enum".into(), Json::Array(scoped));
            }
        }
        properties.insert(name.clone(), Json::Object(child));
    }
    // `{str(name) for name in raw_required if str(name) in properties}` when it is a sequence.
    let mut required: BTreeSet<String> = match projected.get("required") {
        Some(Json::Array(names)) => {
            names.iter().map(py_str).filter(|name| properties.contains_key(name)).collect()
        }
        _ => BTreeSet::new(),
    };
    required.extend(model_required);
    projected.insert("properties".into(), Json::Object(properties));
    // `sorted(...)`: code-point order, which is UTF-8 byte order.
    projected.insert("required".into(), required.into_iter().map(Json::from).collect());
    projected.shift_remove(EXPOSURE_KEY);
    Ok((projected, derivations))
}

/// A string array's items, if every item is text.
fn string_items(value: &Json) -> Option<Vec<&str>> {
    value.as_array()?.iter().map(Json::as_str).collect()
}

/// `_castable_spell_ids(snapshot)`: the admitted characters' spellbooks and the prepared
/// creatures' spells.
pub fn castable_spell_ids(snapshot: &CampaignDisplaySnapshot) -> BTreeSet<String> {
    let texts = |value: Option<&Json>| -> Vec<String> {
        match value {
            Some(Json::Array(items)) => items.iter().filter_map(Json::as_str).map(str::to_owned).collect(),
            _ => Vec::new(),
        }
    };
    let mut choices = BTreeSet::new();
    for participant in &snapshot.state.participants {
        choices.extend(texts(participant.data().get("wizard_spellbook")));
    }
    for (key, entities) in &snapshot.state.state {
        let Json::Object(entities) = entities else { continue };
        if !key.ends_with(".entities") {
            continue;
        }
        for entity in entities.values().filter_map(Json::as_object) {
            choices.extend(texts(entity.get("prepared_spells")));
        }
    }
    choices
}

/// `_string_sequence(options.get(key, ()))`.
fn option_texts(options: &JsonMap, key: &str) -> CodedResult<Vec<String>> {
    match options.get(key) {
        None => Ok(Vec::new()),
        Some(value) => string_items(value)
            .map(|items| items.into_iter().map(str::to_owned).collect())
            .ok_or_else(|| rejected("Campaign narrative options are invalid")),
    }
}

fn texts_json(values: &[String]) -> Json {
    values.iter().cloned().map(Json::from).collect()
}

/// `_narrative_input_schema(operation, options)`: one Campaign File note type's item schema.
pub fn narrative_input_schema(operation: &str, options: &JsonMap) -> CodedResult<Json> {
    match operation {
        "update_character" => {
            return options.get("character_update_schema").cloned().ok_or_else(|| {
                // `options["character_update_schema"]`: a `KeyError`, never an authority error.
                CodedError::new("internal_error", "'character_update_schema'").with_error_type("KeyError")
            });
        }
        "update_inventory" => return Ok(exported(&["campaign_file", "inventory"]).clone()),
        _ => {}
    }
    let Some(Json::Object(item)) = exported(&["campaign_file", "items"]).get(operation) else {
        return Err(CodedError::new(
            "unsupported_campaign_narrative_operation",
            "Campaign narrative operation is unsupported",
        )
        .with_error_type("CommandRejected"));
    };
    let mut item = item.clone();
    let properties = item
        .get_mut("properties")
        .and_then(Json::as_object_mut)
        .expect("an exported note schema's properties");
    match operation {
        "update_scene" => {
            let acts = option_texts(options, "scene_act_ids")?;
            if !acts.is_empty() {
                let mut act = exported(&["campaign_file", "scene_act"]).clone();
                filled(&mut act, "__ACTS__", &texts_json(&acts));
                properties.insert("act_id".into(), act);
            }
        }
        "update_plan_progress" => {
            let items = option_texts(options, "plan_item_ids")?;
            if !items.is_empty() {
                properties["plan_item_id"]
                    .as_object_mut()
                    .expect("the exported plan item")
                    .insert("enum".into(), texts_json(&items));
            }
        }
        "reveal_secret" => {
            let secrets = option_texts(options, "eligible_secret_ids")?;
            properties["secret_id"]
                .as_object_mut()
                .expect("the exported secret")
                .insert("enum".into(), texts_json(&secrets));
        }
        _ => {}
    }
    Ok(Json::Object(item))
}

/// `_campaign_file_update_description()`.
pub fn campaign_file_update_description() -> &'static str {
    exported(&["campaign_file", "description"]).as_str().expect("the exported description")
}

/// `_campaign_file_update_schema(options)`: one batch over the note types offered.
pub fn campaign_file_update_schema(options: &JsonMap) -> CodedResult<JsonMap> {
    let available: BTreeSet<String> = match options.get("operations") {
        None => BTreeSet::new(),
        Some(value) => string_items(value)
            .ok_or_else(|| rejected("Campaign narrative options are invalid"))?
            .into_iter()
            .map(str::to_owned)
            .collect(),
    };
    let mut properties = exported_map(&["campaign_file", "assessment_properties"]);
    let collections =
        exported(&["campaign_file", "collections"]).as_array().expect("the exported collections");
    for pair in collections {
        let (operation, collection) = (pair[0].as_str().unwrap(), pair[1].as_str().unwrap());
        if !available.contains(operation) {
            continue;
        }
        let mut wrapper = exported(&["campaign_file", "collection"]).clone();
        filled(&mut wrapper, "__ITEMS__", &narrative_input_schema(operation, options)?);
        properties.insert(collection.into(), wrapper);
    }
    let mut schema = JsonMap::new();
    schema.insert("type".into(), "object".into());
    schema.insert("properties".into(), Json::Object(properties));
    schema.insert("required".into(), exported(&["campaign_file", "required"]).clone());
    schema.insert("additionalProperties".into(), false.into());
    Ok(schema)
}

/// One of the application's own tools, with `_meta`'s fixed package identity.
fn application_tool(
    name: &str,
    description: &str,
    input_schema: JsonMap,
    capability: &str,
    category: &str,
) -> ModelMechanicsTool {
    let mut tool =
        ModelMechanicsTool::new(REFERENCE_PACKAGE_ID, "1", "0".repeat(64), name, capability, input_schema);
    tool.parameter_derivations = Some(JsonMap::new());
    tool.description = description.to_owned();
    tool.category = category.to_owned();
    tool
}

/// A reference tool's exported description and schema, with the request's sources filled in.
fn reference_tool(key: &str, sources: &[&str], capability: &str) -> ModelMechanicsTool {
    let texts = exported(&[key]);
    let mut schema = texts["input_schema"].clone();
    let listed: Vec<Json> = sources.iter().map(|source| Json::from(*source)).collect();
    let mut with_engine = listed.clone();
    with_engine.push("engine_format".into());
    filled(&mut schema, "__SOURCES__", &Json::Array(listed));
    filled(&mut schema, "__SOURCE_COUNT__", &json!(sources.len()));
    filled(&mut schema, "__SOURCES_AND_ENGINE__", &Json::Array(with_engine));
    application_tool(
        texts["name"].as_str().unwrap(),
        texts["description"].as_str().unwrap(),
        schema.as_object().cloned().unwrap(),
        capability,
        "reference",
    )
}

/// Whether an owner refusal is Python's `CommandRejected` (`except CommandRejected`).
fn is_command_rejected(error: &CodedError) -> bool {
    matches!(error.error_type, None | Some("CommandRejected")) && error.code != PORT_PENDING
}

/// `str()` of the exception group the in-process client raises around one `MCPError`.
pub const LISTING_REFUSAL_MESSAGE: &str = "unhandled errors in a TaskGroup (1 sub-exception)";

/// The server handler's `except AuthorityError: raise MCPError(...)`, as the client raises it:
/// an internal fault (`sylvia_owner::dispatch::Failure::Internal`), not a refusal.
pub fn listing_refusal(error: CodedError) -> CodedError {
    if error.code == PORT_PENDING || !super::is_authority_error(&error) {
        return error;
    }
    CodedError::new("internal_error", LISTING_REFUSAL_MESSAGE).with_error_type("ExceptionGroup")
}

/// `build_stateless_mcp_server(owner)`'s `list_tools` for one request, as the client converts it.
async fn server_list_tools(
    owner: &dyn TurnOwner,
    campaign_id: &str,
    session_id: Option<&str>,
) -> CodedResult<Vec<ModelMechanicsTool>> {
    let (snapshot, _boundary) = owner.model_context_snapshot(campaign_id).await?;
    let participant_ids: Vec<String> = snapshot
        .state
        .participants
        .iter()
        .map(|participant| participant.participant_id().to_owned())
        .collect();
    let castable = castable_spell_ids(&snapshot);
    let catalog = owner.catalog(campaign_id, false).await?;
    let asked = exposure_spell_ids(catalog.iter().map(|tool| &tool.input_schema));
    // Exposure reads the whole saved state, engine-private records included; it only decides
    // which tools to list.
    let state = owner.snapshot(campaign_id).await?.state.state;
    let exposure = ExposureContext::new(&state, &castable, &asked);
    let mut projected = Vec::new();
    for entry in catalog {
        if !exposure.exposed(&entry.input_schema) {
            continue;
        }
        let (schema, derivations) =
            model_input_schema(&entry.input_schema, &participant_ids, Some(&castable))?;
        let spell = schema["properties"].get("spell");
        if spell.and_then(Json::as_object).and_then(|spell| spell.get("enum"))
            == Some(&Json::Array(Vec::new()))
        {
            continue;
        }
        let description =
            if entry.description.is_empty() { entry.capability.clone() } else { entry.description };
        let mut tool = ModelMechanicsTool::new(
            entry.package_id,
            entry.package_version,
            entry.package_sha256,
            entry.name,
            entry.capability,
            schema,
        );
        tool.parameter_derivations = Some(derivations);
        tool.description = description;
        tool.model_behavior = entry.model_behavior;
        projected.push(tool);
    }
    let accepted_plan = match owner.accepted_campaign_plan(campaign_id).await {
        Ok(content) => Some(content),
        Err(error) if is_command_rejected(&error) => None,
        Err(error) => return Err(error),
    };
    let structured =
        accepted_plan.as_ref().is_some_and(|plan| plan.get("campaign_plan").is_some_and(Json::is_object));
    let readable = accepted_plan
        .as_ref()
        .is_some_and(|plan| plan.get("campaign_plan_markdown").is_some_and(Json::is_string));
    if session_id.is_none() && !structured && !readable {
        return Ok(projected);
    }
    let mut sources = vec!["campaign_state", "participant"];
    if structured || readable {
        sources.push("campaign_plan");
    }
    if structured {
        sources.extend(["campaign_setting", "ruleset"]);
    }
    projected.push(reference_tool("knowledge_search", &sources, "context.search"));
    projected.push(reference_tool("reference_read", &sources, "context.read"));
    if let Some(session_id) = session_id {
        let options = owner.campaign_narrative_options(session_id).await?;
        if options.get("operations").is_some_and(py_truthy) {
            projected.push(application_tool(
                CAMPAIGN_FILE_UPDATE_TOOL_NAME,
                campaign_file_update_description(),
                campaign_file_update_schema(&options)?,
                "narrative_state.update_campaign_file",
                "narrative_state",
            ));
        }
    }
    Ok(projected)
}

/// One input schema as the client receives it: the 2026-07-28 wire model (`InputSchema`)
/// declares `$schema` and `type` and keeps every other key as an extra, and the result is
/// dumped with `exclude_none`. So `$schema`, then `type`, lead; a top-level key whose value is
/// `null` is dropped; nested values are left as they are.
pub fn wire_input_schema(schema: JsonMap) -> CodedResult<JsonMap> {
    if schema.get("type").and_then(Json::as_str) != Some("object") {
        // Pydantic refuses the result (`type: Literal["object"]`); no admitted tool has another
        // root type, so its exact message is not reproduced.
        return Err(CodedError::new("internal_error", "Tool input schema type must be 'object'")
            .with_error_type("ValidationError"));
    }
    let mut wired = JsonMap::new();
    for key in ["$schema", "type"] {
        if let Some(value) = schema.get(key).filter(|value| !value.is_null()) {
            wired.insert(key.into(), value.clone());
        }
    }
    for (key, value) in schema {
        if !value.is_null() && !wired.contains_key(&key) {
            wired.insert(key, value);
        }
    }
    Ok(wired)
}

/// The `mcp_list_tools` Full Audit record's description of one listed tool.
fn audited_tool(tool: &ModelMechanicsTool) -> Json {
    json!({
        "package_id": tool.package_id,
        "package_version": tool.package_version,
        "package_sha256": tool.package_sha256,
        "name": tool.name,
        "capability": tool.capability,
        "description": tool.description,
        "input_schema": tool.input_schema,
        "category": tool.category,
        "model_behavior": tool.model_behavior.as_str(),
    })
}

/// `StatelessMcpMechanics._list_tools(campaign_id, purpose=..., discovery_id=..., session_id=...,
/// fixed=...)`: the current tools a model may be shown, each listing recorded in the Full Audit.
pub async fn list_tools_with_discovery(
    owner: &dyn TurnOwner,
    campaign_id: &str,
    purpose: &str,
    discovery_id: &str,
    session_id: Option<&str>,
    fixed: bool,
) -> CodedResult<Vec<ModelMechanicsTool>> {
    if fixed {
        return Err(CodedError::new(
            PORT_PENDING,
            "The fixed tool list (agent/_fixed_tool_projection.py) is not ported yet.",
        ));
    }
    let basis = owner.snapshot(campaign_id).await?;
    let mut tools = server_list_tools(owner, campaign_id, session_id).await.map_err(listing_refusal)?;
    for tool in &mut tools {
        tool.input_schema = wire_input_schema(std::mem::take(&mut tool.input_schema))?;
    }
    let current = owner.snapshot(campaign_id).await?;
    let mut open_turn_ids: Vec<&str> = current
        .turns
        .iter()
        .filter(|(_, status)| *status == TurnStatus::Open)
        .map(|(turn_id, _)| turn_id.as_str())
        .collect();
    open_turn_ids.sort_unstable();
    let ruleset_state: JsonMap = current
        .state
        .state
        .iter()
        .filter(|(key, _)| key.ends_with(".combat") || key.ends_with(".phase"))
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect();
    let audit = json!({
        "kind": "mcp_list_tools",
        "discovery_id": discovery_id,
        "purpose": purpose,
        "campaign_id": campaign_id,
        "session_id": session_id,
        "basis_revision": basis.state.revision,
        "result_revision": current.state.revision,
        "writer": current.state.writer.as_str(),
        "open_turn_ids": open_turn_ids,
        "ruleset_state": ruleset_state,
        "tools": tools.iter().map(audited_tool).collect::<Vec<_>>(),
    });
    let Json::Object(audit) = audit else { unreachable!("a JSON object literal") };
    owner.record_admin_full_audit(audit)?;
    Ok(tools)
}

/// `StatelessMcpMechanics.list_tools(campaign_id, purpose=..., discovery_id=..., session_id=...,
/// fixed=...)`: without a discovery identity, `mcp-discovery-<uuid4 hex>` is drawn from `ids`.
pub async fn list_tools(
    owner: &dyn TurnOwner,
    campaign_id: &str,
    purpose: &str,
    discovery_id: Option<&str>,
    session_id: Option<&str>,
    fixed: bool,
    ids: &(dyn Fn() -> String + Send + Sync),
) -> CodedResult<Vec<ModelMechanicsTool>> {
    let drawn;
    let discovery_id = match discovery_id {
        Some(identity) if !identity.is_empty() => identity,
        _ => {
            drawn = format!("mcp-discovery-{}", ids());
            drawn.as_str()
        }
    };
    list_tools_with_discovery(owner, campaign_id, purpose, discovery_id, session_id, fixed).await
}
