//! The tool list a model provider is shown, from `agent/_provider_session.py`.
//!
//! `_tool_alias`, `_package_tool_alias`, `_resolve_provider_tool_alias`, `_tool_description`,
//! `_model_tool_schema` and `ProviderSessionAdapter._provider_tools`. Everything here is
//! model-facing. The two provider-only argument schemas and `read_tool_arguments` are data,
//! exported to `tool_guidance.json`; the short descriptions are literals copied byte for byte.
//! `tests/tool_projection_vectors.rs` proves the projection against Python.

use crate::argument_contracts::{
    argument_contract, arguments_tool, envelope_schema, py_str, value_error, CONTRACT_KEY,
};
use crate::stateless_mcp::{ModelMechanicsTool, ModelToolBehavior};
use indexmap::IndexMap;
use serde_json::{json, Value as Json};
use std::sync::OnceLock;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::pystr::strip;
use sylvia_base::{CodedError, CodedResult, JsonMap};

/// `_CAMPAIGN_FILE_REVISED_NARRATION_ARGUMENT`.
pub const CAMPAIGN_FILE_REVISED_NARRATION_ARGUMENT: &str = "revised_player_facing_response";
/// `_PREPARATION_INTENT_ARGUMENT`.
pub const PREPARATION_INTENT_ARGUMENT: &str = "player_intent_after_preparation";

const CAMPAIGN_FILE_CAPABILITY: &str = "narrative_state.update_campaign_file";

/// The provider's tool aliases, in the order the tools were listed.
pub type ToolAliases = IndexMap<String, ModelMechanicsTool>;

/// `_tool_alias(tool)`.
pub fn tool_alias(tool: &ModelMechanicsTool) -> String {
    package_tool_alias(
        &tool.package_id,
        &tool.package_version,
        &tool.package_sha256,
        &tool.name,
        &tool.capability,
    )
}

/// `_package_tool_alias(package_id, version, sha256, name, capability)`: a stable provider name,
/// `tool_<12 hex digits>_<slug>`, at most 64 characters.
pub fn package_tool_alias(
    package_id: &str,
    version: &str,
    sha256: &str,
    name: &str,
    capability: &str,
) -> String {
    let slug: String =
        name.chars().map(|c| if c.is_ascii_alphanumeric() || c == '_' { c } else { '_' }).collect();
    let slug = match slug.trim_matches('_') {
        "" => "tool",
        trimmed => trimmed,
    };
    let identity = pyjson::dumps(&json!([package_id, version, sha256, name, capability]), Dump::WIRE);
    let digest = &sha256_hex(identity.as_bytes())[..12];
    let mut alias = format!("tool_{digest}_{slug}");
    // Every character is ASCII, so 64 bytes are 64 characters.
    alias.truncate(64);
    alias
}

/// `_resolve_provider_tool_alias(requested_name, aliases)`: an exposed alias, or one
/// unambiguous exact operation name.
pub fn resolve_provider_tool_alias<'a>(
    requested_name: &str,
    aliases: &'a ToolAliases,
) -> Option<(&'a str, &'a ModelMechanicsTool)> {
    if let Some((alias, tool)) = aliases.get_key_value(requested_name) {
        return Some((alias.as_str(), tool));
    }
    let mut matches = aliases.iter().filter(|(_, tool)| tool.name == requested_name);
    match (matches.next(), matches.next()) {
        (Some((alias, tool)), None) => Some((alias.as_str(), tool)),
        _ => None,
    }
}

/// `_tool_description(tool)`: a model-readable purpose instead of an opaque capability identity.
pub fn tool_description(tool: &ModelMechanicsTool) -> String {
    let own = strip(&tool.description);
    let mut description = if !own.is_empty() && own != tool.capability {
        own.to_owned()
    } else {
        match tool.input_schema.get("description").and_then(Json::as_str).map(strip) {
            Some(schema_description) if !schema_description.is_empty() => schema_description.to_owned(),
            _ => {
                let readable_name = tool.name.replace('_', " ");
                let required_names = match tool.input_schema.get("required") {
                    Some(Json::Array(items)) => items.iter().map(py_str).collect::<Vec<_>>().join(", "),
                    _ => String::new(),
                };
                let requirement = if required_names.is_empty() {
                    String::new()
                } else {
                    format!(" Required arguments: {required_names}.")
                };
                format!(
                    "Execute the package mechanic '{readable_name}'. Use it only when the player's \
                     intent requires this mechanic.{requirement}"
                )
            }
        }
    };
    if tool.category == "narrative_state" && tool.capability == CAMPAIGN_FILE_CAPABILITY {
        description.push_str(" Use only the declared top-level fields; do not invent convenience keys.");
    }
    description
}

/// The exported guidance values (`tool_guidance.json`, written from the Python source by
/// `generate_tool_guidance.py` with every object's keys in Python's order).
pub(crate) fn guidance() -> &'static JsonMap {
    static GUIDANCE: OnceLock<JsonMap> = OnceLock::new();
    GUIDANCE.get_or_init(|| match pyjson::loads(include_str!("tool_guidance.json")) {
        Ok(Json::Object(map)) => map,
        _ => panic!("tool_guidance.json is an exported JSON object"),
    })
}

fn guidance_value(key: &str) -> Json {
    guidance()[key].clone()
}

/// `schema.setdefault("description", text)`.
fn default_description(schema: &mut JsonMap, text: String) {
    schema.entry("description").or_insert(Json::String(text));
}

/// `_model_tool_schema(tool, envelope=...)`: provider-only guidance around the authority-owned
/// tool interface. With `envelope`, a tool declaring argument contracts shows only its envelope;
/// without it, the full projection a selected contract is read from.
pub fn model_tool_schema(tool: &ModelMechanicsTool, envelope: bool) -> CodedResult<JsonMap> {
    let mut projected = tool.input_schema.clone();
    if let Some(Json::Object(properties)) = projected.get_mut("properties") {
        if tool.model_behavior == ModelToolBehavior::LeavesPlayerIntentPending {
            if properties.contains_key(PREPARATION_INTENT_ARGUMENT) {
                return Err(value_error("Ruleset input collides with the provider preparation scope"));
            }
            properties
                .insert(PREPARATION_INTENT_ARGUMENT.into(), guidance_value("preparation_intent_argument"));
        }
        if tool.category == "narrative_state" && tool.capability == CAMPAIGN_FILE_CAPABILITY {
            // An existing key keeps its place, as a dict assignment does.
            properties.insert(
                CAMPAIGN_FILE_REVISED_NARRATION_ARGUMENT.into(),
                guidance_value("campaign_file_revised_narration_argument"),
            );
        }
        for (argument, schema) in properties.iter_mut() {
            let Json::Object(schema) = schema else { continue };
            let reference = schema.get("x-sylvia-gm-reference").and_then(Json::as_str);
            let kind = schema.get("type").and_then(Json::as_str);
            if reference == Some("participant") {
                default_description(
                    schema,
                    format!(
                        "Exact participant_id for {argument} from SYLVIA_GM_CAMPAIGN_PREPARATION or \
                         SYLVIA_GM_FRESH_CAMPAIGN_STATE; never use a display name."
                    ),
                );
            } else if reference == Some("entity") {
                default_description(
                    schema,
                    format!(
                        "Exact state entity ID for {argument} from SYLVIA_GM_CAMPAIGN_PREPARATION or \
                         SYLVIA_GM_FRESH_CAMPAIGN_STATE; never use a display name."
                    ),
                );
            } else if argument.ends_with("_state") && kind == Some("string") {
                default_description(
                    schema,
                    format!(
                        "Exact state entity ID for {argument} from a campaign state key; usually the \
                         participant_id, never a status word such as active or ready."
                    ),
                );
            } else if argument.starts_with("use_") && kind == Some("boolean") {
                default_description(
                    schema,
                    "Set true only when campaign facts explicitly establish this optional feature; \
                     otherwise use false."
                        .into(),
                );
            }
        }
    }
    if envelope {
        if let Some(contract) = argument_contract(&projected)? {
            // The selected value's own inputs are read on demand; execution flattens them back.
            return envelope_schema(&projected, &contract);
        }
    }
    Ok(projected)
}

/// `_provider_tools(catalog)`: the provider's tool definitions and the aliases they are listed
/// under, with `read_tool_arguments` last when any tool declares argument contracts.
pub fn provider_tools(catalog: &[ModelMechanicsTool]) -> CodedResult<(Vec<Json>, ToolAliases)> {
    let mut tools = Vec::with_capacity(catalog.len() + 1);
    let mut aliases = ToolAliases::with_capacity(catalog.len());
    for catalog_tool in catalog {
        let alias = tool_alias(catalog_tool);
        if aliases.contains_key(&alias) {
            return Err(CodedError::new(
                "catalog_tool_alias_conflict",
                "Catalog tool aliases are not unique",
            )
            .with_error_type("CommandRejected"));
        }
        aliases.insert(alias.clone(), catalog_tool.clone());
        let parameters = model_tool_schema(catalog_tool, true)?;
        tools.push(json!({
            "type": "function",
            "function": {
                "name": alias,
                "description": tool_description(catalog_tool),
                "parameters": parameters,
            },
        }));
    }
    if aliases.values().any(|tool| tool.input_schema.contains_key(CONTRACT_KEY)) {
        tools.push(arguments_tool());
    }
    Ok((tools, aliases))
}
