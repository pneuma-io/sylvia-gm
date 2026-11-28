//! `agent/_fixed_tool_projection.py`: current choices kept outside the fixed GM tool
//! definitions.
//!
//! The fixed and deferred tool lists declare each tool once, with state-independent schemas;
//! the current enum choices and fields go in an appended record beside the callable tools
//! (`argument_choices_record`), and a mutation's arguments are checked against them before it
//! is submitted (`unavailable_values`). `compact_schema` factors repeated simple property
//! schemas into local references when that saves bytes.
//!
//! Python's `==` and `in` throughout (`py_eq`, `py_contains`); a malformed schema raises the
//! class and text Python would.

use serde_json::Value as Json;
use std::collections::BTreeMap;
use std::sync::OnceLock;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::JsonMap;
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::pyops::{py_contains, py_eq, py_get_attr, py_item, py_str, py_truthy};

fn texts() -> &'static JsonMap {
    static TEXTS: OnceLock<JsonMap> = OnceLock::new();
    TEXTS.get_or_init(|| match pyjson::loads(include_str!("tool_loader_texts.json")) {
        Ok(Json::Object(map)) => map,
        _ => panic!("tool_loader_texts.json is an exported JSON object"),
    })
}

/// An exported text or value of `tool_loader_texts.json`.
pub(crate) fn loader_text(key: &str) -> &'static Json {
    &texts()[key]
}

fn empty() -> Json {
    Json::Object(JsonMap::new())
}

/// `mapping.get(key, {})` on a value Python calls `.get` on.
fn get_or_empty(value: &Json, key: &str) -> DomainResult<Json> {
    Ok(py_get_attr(value, key)?.cloned().unwrap_or_else(empty))
}

/// `set(a) != set(b)` of two mappings' keys.
fn same_keys(a: &JsonMap, b: &JsonMap) -> bool {
    a.len() == b.len() && a.keys().all(|key| b.contains_key(key))
}

/// `schema_choices(current, fixed, path)`: only the constraints the fixed projection left out,
/// by schema path. A participant reference is left out (its ids are in the campaign state, and
/// listing a participant-only field's choices read as the only allowed target to a model).
pub fn schema_choices(current: &JsonMap, fixed: &Json, path: &str) -> DomainResult<JsonMap> {
    let mut choices = JsonMap::new();
    if let Some(choices_now) = current.get("enum") {
        let unchanged = py_get_attr(fixed, "enum")?.is_some_and(|before| py_eq(before, choices_now));
        let participant = current.get("x-sylvia-gm-reference").and_then(Json::as_str) == Some("participant");
        if !unchanged && !participant {
            let mut entry = JsonMap::new();
            entry.insert("allowed_values".into(), choices_now.clone());
            choices.insert(path.into(), Json::Object(entry));
        }
    }
    let properties = current.get("properties").cloned().unwrap_or_else(empty);
    let fixed_properties = get_or_empty(fixed, "properties")?;
    if let (Json::Object(properties), Json::Object(fixed_properties)) = (&properties, &fixed_properties) {
        if !same_keys(properties, fixed_properties) {
            let mut fields: Vec<&String> = properties.keys().collect();
            fields.sort();
            let entry = choices.entry(path.to_owned()).or_insert_with(empty);
            if let Json::Object(entry) = entry {
                entry.insert(
                    "available_fields".into(),
                    Json::Array(fields.into_iter().map(|field| Json::String(field.clone())).collect()),
                );
            }
        }
        for (name, child) in properties {
            if let Json::Object(child) = child {
                let fixed_child = fixed_properties.get(name).cloned().unwrap_or_else(empty);
                for (key, value) in schema_choices(child, &fixed_child, &format!("{path}.{name}"))? {
                    choices.insert(key, value);
                }
            }
        }
    }
    let fixed_items = get_or_empty(fixed, "items")?;
    if let (Some(Json::Object(items)), Json::Object(_)) = (current.get("items"), &fixed_items) {
        for (key, value) in schema_choices(items, &fixed_items, &format!("{path}[]"))? {
            choices.insert(key, value);
        }
    }
    Ok(choices)
}

/// `argument_choices_record(fixed, offered)`: the exact current enum choices beside the
/// callable tools, never in the prefix.
pub fn argument_choices_record(fixed: &[Json], offered: Option<&[Json]>) -> DomainResult<String> {
    // `{tool["function"]["name"]: tool["function"]["parameters"] for tool in fixed}`: a
    // repeated name keeps its last parameters.
    let mut by_name: Vec<(Json, Json)> = Vec::new();
    for tool in fixed {
        let function = py_item(tool, "function")?;
        let (name, parameters) =
            (py_item(function, "name")?.clone(), py_item(function, "parameters")?.clone());
        match by_name.iter_mut().find(|(existing, _)| py_eq(existing, &name)) {
            Some(slot) => slot.1 = parameters,
            None => by_name.push((name, parameters)),
        }
    }
    let mut choices = JsonMap::new();
    for tool in offered.unwrap_or_default() {
        let function = py_item(tool, "function")?;
        let name = py_item(function, "name")?;
        let parameters = match py_item(function, "parameters")? {
            Json::Object(parameters) => parameters,
            other => {
                return Err(DomainError::uncaught(
                    "AttributeError",
                    format!("'{}' object has no attribute 'get'", sylvia_domain::pyops::py_type_name(other)),
                ))
            }
        };
        let stable =
            by_name.iter().find(|(existing, _)| py_eq(existing, name)).map_or_else(empty, |(_, p)| p.clone());
        let fields = schema_choices(parameters, &stable, "$")?;
        if !fields.is_empty() {
            choices.insert(py_str(name), Json::Object(fields));
        }
    }
    let preamble = loader_text("choices_preamble").as_str().expect("an exported text");
    Ok(format!("{preamble}{}", pyjson::dumps(&Json::Object(choices), Dump::CANONICAL)))
}

/// `unavailable_values(arguments, current, fixed)`: the current enums the fixed projection
/// removed, checked before a mutation is submitted.
pub fn unavailable_values(arguments: &Json, current: &Json, fixed: &Json) -> DomainResult<Vec<Json>> {
    let mut issues = Vec::new();
    visit(arguments, current, fixed, "", &mut issues)?;
    Ok(issues)
}

fn visit(value: &Json, schema: &Json, stable: &Json, path: &str, issues: &mut Vec<Json>) -> DomainResult<()> {
    if let Some(choices) = py_get_attr(schema, "enum")? {
        let unchanged = py_get_attr(stable, "enum")?.is_some_and(|before| py_eq(before, choices));
        if !unchanged && !py_contains(value, choices)? {
            let mut issue = JsonMap::new();
            issue.insert("path".into(), Json::String(path.to_owned()));
            issue.insert("kind".into(), Json::String("invalid_enum_value".into()));
            issue.insert("allowed_values".into(), choices.clone());
            issues.push(Json::Object(issue));
        }
    }
    match value {
        Json::Object(value) => {
            let properties = get_or_empty(schema, "properties")?;
            let stable_properties = get_or_empty(stable, "properties")?;
            let Json::Object(properties) = properties else {
                return Err(DomainError::uncaught(
                    "AttributeError",
                    format!(
                        "'{}' object has no attribute 'items'",
                        sylvia_domain::pyops::py_type_name(&properties)
                    ),
                ));
            };
            for (name, child) in &properties {
                if let (Some(item), Json::Object(_)) = (value.get(name), child) {
                    let stable_child = get_or_empty(&stable_properties, name)?;
                    let child_path = if path.is_empty() { name.clone() } else { format!("{path}.{name}") };
                    visit(item, child, &stable_child, &child_path, issues)?;
                }
            }
        }
        Json::Array(items) => {
            if let Some(item_schema @ Json::Object(_)) = py_get_attr(schema, "items")? {
                for (index, item) in items.iter().enumerate() {
                    // `stable.get("items", {})` is read for each item, so never for none.
                    let stable_items = get_or_empty(stable, "items")?;
                    visit(item, item_schema, &stable_items, &format!("{path}[{index}]"), issues)?;
                }
            }
        }
        _ => {}
    }
    Ok(())
}

/// `compact_schema.dump(value)`.
fn dump(value: &Json) -> String {
    pyjson::dumps(value, Dump::CANONICAL)
}

/// `compact_schema.eligible(value)`: an exact copy can be factored out unless it scopes a
/// reference, an object, an enum or a participant or castable-spell choice.
fn eligible(value: &Json) -> bool {
    match value {
        Json::Object(map) => {
            if ["$ref", "$id", "$defs", "properties", "enum"].iter().any(|key| map.contains_key(*key)) {
                return false;
            }
            if map.get("x-sylvia-gm-reference").and_then(Json::as_str) == Some("participant") {
                return false;
            }
            if map.get("x-sylvia-gm-castable-spells").is_some_and(py_truthy) {
                return false;
            }
            map.values().all(eligible)
        }
        Json::Array(items) => items.iter().all(eligible),
        _ => true,
    }
}

/// `compact_schema(schema)`: repeated simple property schemas factored into local `$defs`
/// references, only when bytes decrease. The copies are exact; execution always validates the
/// original package schema.
pub fn compact_schema(mut schema: JsonMap) -> JsonMap {
    let Some(Json::Object(properties)) = schema.get("properties") else {
        return schema;
    };
    if schema.contains_key("$defs") {
        return schema;
    }
    let mut counts: BTreeMap<String, usize> = BTreeMap::new();
    for child in properties.values().filter(|child| eligible(child)) {
        *counts.entry(dump(child)).or_insert(0) += 1;
    }
    let mut definitions = JsonMap::new();
    let mut references: std::collections::HashMap<String, Json> = std::collections::HashMap::new();
    for (serialized, count) in &counts {
        let key = format!("p{}", definitions.len());
        let mut reference = JsonMap::new();
        reference.insert("$ref".into(), Json::String(format!("#/$defs/{key}")));
        let reference = Json::Object(reference);
        let size = serialized.len();
        // The definition's key and the `$defs` object overhead count in each candidate's cost.
        if count * size <= size + count * dump(&reference).len() + key.len() + 16 {
            continue;
        }
        definitions.insert(key, pyjson::loads(serialized).expect("canonical JSON reloads"));
        references.insert(serialized.clone(), reference);
    }
    if !definitions.is_empty() {
        let replaced: JsonMap = properties
            .iter()
            .map(|(name, child)| {
                (name.clone(), references.get(&dump(child)).cloned().unwrap_or_else(|| child.clone()))
            })
            .collect();
        schema.insert("properties".into(), Json::Object(replaced));
        schema.insert("$defs".into(), Json::Object(definitions));
    }
    schema
}
