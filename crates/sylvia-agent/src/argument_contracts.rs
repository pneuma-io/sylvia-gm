//! `agent/_argument_contracts.py`: per-choice argument contracts, a small model-facing envelope
//! over one large tool schema.
//!
//! A package may declare, at the root of a tool's input schema, which inputs each value of one
//! selector input can use (version 1). The model is shown only the selector, the common inputs
//! and one object field; the selected value's own inputs are read with `read_tool_arguments`.
//!
//! Ported so far: the declaration ([`argument_contract`]), the envelope the model is shown
//! ([`envelope_schema`]) and the read tool's definition ([`arguments_tool`]). The read itself
//! (`selected_schema`, `read_tool_arguments`) and the call flattening (`unwrap_arguments`) belong
//! to the turn loop and come with it.

use serde_json::{json, Value as Json};
use std::collections::{BTreeMap, BTreeSet};
use sylvia_base::pystr::{repr, repr_json};
use sylvia_base::{CodedError, CodedResult, JsonMap};

/// `CONTRACT_KEY`.
pub const CONTRACT_KEY: &str = "x-sylvia-gm-argument-contracts";
/// `ARGUMENTS_TOOL_NAME`.
pub const ARGUMENTS_TOOL_NAME: &str = "read_tool_arguments";

/// `ArgumentContract`. The sets are Python frozensets: only membership and sorted order matter.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct ArgumentContract {
    pub selector: String,
    pub field: String,
    pub common: BTreeSet<String>,
    pub by_value: BTreeMap<String, BTreeSet<String>>,
    pub aliases: BTreeMap<String, String>,
}

impl ArgumentContract {
    /// `canonical(value)`: a text value through its alias, `None` for anything else.
    pub fn canonical<'a>(&'a self, value: &'a Json) -> Option<&'a str> {
        let text = value.as_str()?;
        Some(self.aliases.get(text).map_or(text, String::as_str))
    }

    /// `selected(value)`: the inputs the selected value may use beyond the envelope, or `None`
    /// if unknown.
    pub fn selected(&self, value: &Json) -> Option<&BTreeSet<String>> {
        if let Some(found) = value.as_str().and_then(|text| self.by_value.get(text)) {
            return Some(found);
        }
        self.by_value.get(self.canonical(value)?)
    }
}

pub(crate) fn value_error(message: impl Into<String>) -> CodedError {
    CodedError::rejected(message).with_error_type("ValueError")
}

pub(crate) fn type_error(message: impl Into<String>) -> CodedError {
    CodedError::rejected(message).with_error_type("TypeError")
}

/// `str(value)` for a JSON value.
pub(crate) fn py_str(value: &Json) -> String {
    match value {
        Json::String(text) => text.clone(),
        other => repr_json(other),
    }
}

/// `type(value).__name__` for a JSON value.
pub(crate) fn type_name(value: &Json) -> &'static str {
    match value {
        Json::Null => "NoneType",
        Json::Bool(_) => "bool",
        Json::Number(number) if number.is_f64() => "float",
        Json::Number(_) => "int",
        Json::String(_) => "str",
        Json::Array(_) => "list",
        Json::Object(_) => "dict",
    }
}

/// `value == 1`, as Python compares it: `True` and `1.0` are equal to 1.
fn equals_one(value: Option<&Json>) -> bool {
    match value {
        Some(Json::Bool(flag)) => *flag,
        Some(Json::Number(number)) => number.as_f64() == Some(1.0),
        _ => false,
    }
}

fn names(value: Option<&Json>, place: &str) -> CodedResult<BTreeSet<String>> {
    let failure = || value_error(format!("Argument contract {place} must be a list of input names"));
    let Some(Json::Array(items)) = value else {
        return Err(failure());
    };
    items.iter().map(|item| item.as_str().map(str::to_owned).ok_or_else(failure)).collect()
}

/// `argument_contract(schema)`: read a tool schema's version-1 contract; a malformed
/// declaration is a package defect (`ValueError`).
pub fn argument_contract(schema: &JsonMap) -> CodedResult<Option<ArgumentContract>> {
    let raw = match schema.get(CONTRACT_KEY) {
        None | Some(Json::Null) => return Ok(None),
        Some(raw) => raw,
    };
    let raw = match raw {
        Json::Object(raw) if equals_one(raw.get("version")) => raw,
        _ => return Err(value_error("Unsupported argument contract declaration")),
    };
    let (Some(Json::Object(properties)), Some(Json::String(selector)), Some(Json::String(field))) =
        (schema.get("properties"), raw.get("selector"), raw.get("field"))
    else {
        return Err(value_error("Argument contract selector or field is invalid"));
    };
    if !properties.contains_key(selector) || properties.contains_key(field) {
        return Err(value_error("Argument contract selector or field is invalid"));
    }
    let mut common = names(raw.get("common"), "common")?;
    let Some(Json::Object(by_value_raw)) = raw.get("by_value") else {
        return Err(value_error("Argument contract by_value must be an object"));
    };
    // The model projection hides derived and engine-only inputs; a contract names the package
    // schema's inputs, so keep only those the model is shown.
    common.retain(|name| properties.contains_key(name));
    let mut by_value = BTreeMap::new();
    for (value, value_names) in by_value_raw {
        let mut kept = names(Some(value_names), &format!("by_value.{value}"))?;
        kept.retain(|name| properties.contains_key(name) && !common.contains(name));
        by_value.insert(value.clone(), kept);
    }
    let aliases = match &properties[selector] {
        Json::Object(selector_schema) => match selector_schema.get("x-sylvia-gm-value-aliases") {
            Some(Json::Object(raw_aliases)) => {
                raw_aliases.iter().map(|(key, value)| (key.clone(), py_str(value))).collect()
            }
            _ => BTreeMap::new(),
        },
        _ => BTreeMap::new(),
    };
    common.remove(selector);
    Ok(Some(ArgumentContract { selector: selector.clone(), field: field.clone(), common, by_value, aliases }))
}

fn field_description(contract: &ArgumentContract) -> String {
    let selector = &contract.selector;
    format!(
        "Inputs the selected {selector} needs beyond those listed here; most need none, so cast \
         directly. If one is missing, its refusal names it and lists that {selector}'s inputs; \
         {ARGUMENTS_TOOL_NAME} (this tool's name and the {selector}) returns them beforehand. An \
         input this tool does not have, or one already given at the top level, is refused before \
         anything resolves."
    )
}

fn used_definitions(value: &Json, definitions: &JsonMap) -> BTreeSet<String> {
    let mut used = BTreeSet::new();
    let mut pending = vec![value];
    while let Some(item) = pending.pop() {
        match item {
            Json::Object(map) => {
                if let Some(key) =
                    map.get("$ref").and_then(Json::as_str).and_then(|r| r.strip_prefix("#/$defs/"))
                {
                    if used.insert(key.to_owned()) {
                        pending.extend(definitions.get(key));
                    }
                }
                pending.extend(map.values());
            }
            Json::Array(items) => pending.extend(items),
            _ => {}
        }
    }
    used
}

/// The items `for name in value` yields, for `schema.get("required", ())`: a list's items, a
/// string's characters, a dict's keys; anything else raises Python's `TypeError`.
fn iterated(value: Option<&Json>) -> CodedResult<Vec<Json>> {
    match value {
        None => Ok(Vec::new()),
        Some(Json::Array(items)) => Ok(items.clone()),
        Some(Json::String(text)) => Ok(text.chars().map(|c| Json::String(c.to_string())).collect()),
        Some(Json::Object(map)) => Ok(map.keys().map(|key| Json::String(key.clone())).collect()),
        Some(other) => Err(type_error(format!("'{}' object is not iterable", type_name(other)))),
    }
}

/// `[name for name in schema.get("required", ()) if name in kept]`.
fn required_in(schema: &JsonMap, kept: &JsonMap) -> CodedResult<Vec<Json>> {
    let mut required = Vec::new();
    for name in iterated(schema.get("required"))? {
        match &name {
            Json::String(text) if kept.contains_key(text) => required.push(name),
            Json::Array(_) | Json::Object(_) => {
                return Err(type_error(format!("unhashable type: '{}'", type_name(&name))))
            }
            _ => {}
        }
    }
    Ok(required)
}

/// `envelope_schema(schema, contract)`: the model-facing schema, the selector, common inputs and
/// one object for the rest. `contract` is the one [`argument_contract`] read from `schema`.
pub fn envelope_schema(schema: &JsonMap, contract: &ArgumentContract) -> CodedResult<JsonMap> {
    let mut projected: JsonMap = schema
        .iter()
        .filter(|(key, _)| !matches!(key.as_str(), CONTRACT_KEY | "properties" | "required" | "$defs"))
        .map(|(key, value)| (key.clone(), value.clone()))
        .collect();
    let Some(Json::Object(properties)) = schema.get("properties") else {
        return Err(CodedError::rejected("'properties'").with_error_type("KeyError"));
    };
    let mut kept: JsonMap = properties
        .iter()
        .filter(|(name, _)| **name == contract.selector || contract.common.contains(*name))
        .map(|(name, child)| (name.clone(), child.clone()))
        .collect();
    kept.insert(
        contract.field.clone(),
        json!({"type": "object", "description": field_description(contract)}),
    );
    let required = required_in(schema, &kept)?;
    let kept = Json::Object(kept);
    let used = match schema.get("$defs") {
        Some(Json::Object(definitions)) => Some((used_definitions(&kept, definitions), definitions)),
        _ => None,
    };
    projected.insert("properties".into(), kept);
    projected.insert("required".into(), Json::Array(required));
    if let Some((used, definitions)) = used {
        if !used.is_empty() {
            let mut kept_definitions = JsonMap::new();
            for key in used {
                let Some(definition) = definitions.get(&key) else {
                    return Err(CodedError::rejected(repr(&key)).with_error_type("KeyError"));
                };
                kept_definitions.insert(key, definition.clone());
            }
            projected.insert("$defs".into(), Json::Object(kept_definitions));
        }
    }
    Ok(projected)
}

/// `copy.deepcopy(ARGUMENTS_TOOL)`, from the exported guidance data.
pub fn arguments_tool() -> Json {
    crate::provider_session::tools::guidance()["arguments_tool"].clone()
}
