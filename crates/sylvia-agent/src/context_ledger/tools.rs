//! The ledger's tool lists (L2b): one tool list for the session, brought up to date by name;
//! the deferred catalog and its appended tool changes; the callable-tools record; and the
//! fingerprints a request's tools are observed by.

use super::wire::{canonical_json, sha};
use super::{
    CALLABLE_LABEL, JOURNAL_KIND, LOADER_DEFERRAL, NATIVE_DEFERRAL, PROVIDER_DEFERRAL, TOOL_CHANGE_BLOCKS,
    TOOL_CHANGE_CLASS,
};
use crate::tool_loader::{function_of, mechanic_tool, reader_tool, tool_name};
use serde_json::Value as Json;
use std::collections::{BTreeSet, HashMap};
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::JsonMap;
use sylvia_domain::error::DomainResult;
use sylvia_domain::pyops::{py_eq, py_iter, py_str};

/// `_prefix_fingerprint(stable)`: the stable prefix's roles and contents.
pub fn prefix_fingerprint(stable: &[JsonMap]) -> String {
    let projected: Vec<Json> = stable
        .iter()
        .map(|message| {
            let mut entry = JsonMap::new();
            entry.insert("role".into(), message.get("role").cloned().unwrap_or(Json::Null));
            entry.insert("content".into(), message.get("content").cloned().unwrap_or(Json::Null));
            Json::Object(entry)
        })
        .collect();
    sha(&canonical_json(&Json::Array(projected)))
}

/// `_tools_fingerprint(tools)`: the tool list as sent, order-sensitive.
pub fn tools_fingerprint(tools: Option<&[JsonMap]>) -> String {
    let tools: Vec<Json> = tools.unwrap_or_default().iter().cloned().map(Json::Object).collect();
    sha(&pyjson::dumps(&Json::Array(tools), Dump::WIRE.ascii(false)))
}

/// `toolset_fingerprint(tools)`: the tool set as full definitions, order-insensitive (sorted by
/// name, then by its canonical JSON). Claude binds a thinking block to the set of tools, so a
/// reordering is not a change but an added, removed or reworded tool is.
pub fn toolset_fingerprint(tools: Option<&[JsonMap]>) -> String {
    let mut ordered: Vec<(String, String, Json)> = tools
        .unwrap_or_default()
        .iter()
        .map(|tool| {
            (tool_name(tool), canonical_json(&Json::Object(tool.clone())), Json::Object(tool.clone()))
        })
        .collect();
    ordered.sort_by(|a, b| (&a.0, &a.1).cmp(&(&b.0, &b.1)));
    sha(&canonical_json(&Json::Array(ordered.into_iter().map(|(_, _, tool)| tool).collect())))
}

/// What [`merged_tool_list`] answers: the list, the tools added and the tools replaced.
#[derive(Debug, Clone, PartialEq, Default)]
pub struct MergedTools {
    pub merged: Vec<JsonMap>,
    pub added: Vec<JsonMap>,
    pub replaced: Vec<JsonMap>,
}

/// `merged_tool_list(sent, offered)`: the tools already sent, brought up to date by `offered`.
/// A tool is identified by its name: one not yet sent is added at the end; one offered with a
/// different definition is replaced at its position.
pub fn merged_tool_list(sent: &[JsonMap], offered: Option<&[JsonMap]>) -> MergedTools {
    let mut merged: Vec<JsonMap> = sent.to_vec();
    // `{name: index for index, tool in enumerate(merged)}`: a repeated name keeps its last.
    let mut position: HashMap<String, usize> =
        merged.iter().enumerate().map(|(index, tool)| (tool_name(tool), index)).collect();
    let mut seen: BTreeSet<String> = BTreeSet::new();
    let mut added = Vec::new();
    let mut replaced = Vec::new();
    for tool in offered.unwrap_or_default() {
        let name = tool_name(tool);
        if name.is_empty() || !seen.insert(name.clone()) {
            continue;
        }
        match position.get(&name) {
            None => {
                position.insert(name, merged.len());
                merged.push(tool.clone());
                added.push(tool.clone());
            }
            Some(&index) if !py_eq(&Json::Object(merged[index].clone()), &Json::Object(tool.clone())) => {
                merged[index] = tool.clone();
                replaced.push(tool.clone());
            }
            Some(_) => {}
        }
    }
    MergedTools { merged, added, replaced }
}

/// `_canonical_tool(tool)`: a tool definition with every mapping's keys sorted, the bytes a
/// saved copy reloads to.
pub fn canonical_tool(tool: &JsonMap) -> JsonMap {
    match pyjson::loads(&canonical_json(&Json::Object(tool.clone()))) {
        Ok(Json::Object(map)) => map,
        _ => unreachable!("canonical JSON of a mapping reloads as one"),
    }
}

/// `_tool_definitions(value)`: the mappings of a saved list, else none.
fn tool_definitions(value: Option<&Json>) -> Vec<JsonMap> {
    match value {
        Some(Json::Array(items)) => items.iter().filter_map(Json::as_object).cloned().collect(),
        _ => Vec::new(),
    }
}

/// `_saved(record, kind)`: saved evidence of `kind`, not an export stub that stands for none.
pub fn saved(record: &Json, kind: &str) -> bool {
    match record {
        Json::Object(record) => {
            record.get("kind").and_then(Json::as_str) == Some(kind)
                && record.get("omitted_from_export") != Some(&Json::Bool(true))
        }
        _ => false,
    }
}

/// `saved_tool_list(records, context_owner)`: the tool list this session's journal records say
/// was last sent, in first-sent order. Each record's additions and replacements are replayed in
/// order, so a reopened session sends exactly the list it last sent.
pub fn saved_tool_list(records: &[Json], context_owner: Option<&Json>) -> Vec<JsonMap> {
    let mut tools: Vec<JsonMap> = Vec::new();
    for record in records {
        if !saved(record, JOURNAL_KIND) {
            continue;
        }
        if let Some(owner) = context_owner {
            if !py_eq(record.get("context_owner").unwrap_or(&Json::Null), owner) {
                continue;
            }
        }
        let mut changes = tool_definitions(record.get("tools_added"));
        changes.extend(tool_definitions(record.get("tools_replaced")));
        if !changes.is_empty() {
            tools = merged_tool_list(&tools, Some(&changes)).merged;
        }
    }
    tools
}

/// `callable_record(sent, offered)`: which of the listed tools can be called at this step, in
/// the shorter of two forms.
pub fn callable_record(sent: &[JsonMap], offered: Option<&[JsonMap]>) -> String {
    let listed: Vec<String> = sent.iter().map(tool_name).collect();
    let allowed: BTreeSet<String> = offered.unwrap_or_default().iter().map(tool_name).collect();
    let mut can: Vec<&str> =
        listed.iter().filter(|name| allowed.contains(*name)).map(String::as_str).collect();
    let mut cannot: Vec<&str> =
        listed.iter().filter(|name| !allowed.contains(*name)).map(String::as_str).collect();
    can.sort();
    cannot.sort();
    let head =
        format!("{CALLABLE_LABEL} (application control, not player speech; the latest record with this label applies): ");
    if cannot.is_empty() {
        return format!("{head}every tool listed in this request can be called at this step.");
    }
    if can.is_empty() {
        return format!(
            "{head}no tool can be called at this step. Reply without a tool call; a tool call now is refused."
        );
    }
    if can.len() <= cannot.len() {
        return format!(
            "{head}at this step only these listed tools can be called: {}. A call to any other tool is refused.",
            can.join(", ")
        );
    }
    format!(
        "{head}at this step every listed tool can be called except: {}. A call to one of those is refused.",
        cannot.join(", ")
    )
}

/// `deferred_definitions(fixed, core, deferral)`: the declared catalog, `core` in full and
/// every other tool deferred (`native`, `provider`) or, with the loader, left out and reached
/// through the one generic mechanic tool, declared last after the argument reader (unless the
/// catalog already has a tool of the reader's name).
pub fn deferred_definitions(fixed: &[JsonMap], core: &BTreeSet<String>, deferral: &str) -> Vec<JsonMap> {
    let mut declared: Vec<JsonMap> = Vec::new();
    for tool in fixed {
        if core.contains(&tool_name(tool)) {
            declared.push(tool.clone());
        } else if deferral == NATIVE_DEFERRAL || deferral == PROVIDER_DEFERRAL {
            let mut deferred = tool.clone();
            deferred.insert("defer_loading".into(), Json::Bool(true));
            declared.push(deferred);
        }
    }
    if deferral == LOADER_DEFERRAL {
        let reader = reader_tool();
        if !declared.iter().any(|tool| tool_name(tool) == tool_name(&reader)) {
            declared.push(reader);
        }
        declared.push(mechanic_tool());
    }
    declared
}

/// `is_tool_change(message)`: an in-conversation system message whose content changes the tool
/// set.
pub fn is_tool_change(message: &JsonMap) -> bool {
    message.get("role").and_then(Json::as_str) == Some("system")
        && matches!(message.get("content"), Some(Json::Array(blocks)) if !blocks.is_empty()
            && blocks.iter().all(|block| block.get("type").and_then(Json::as_str)
                .is_some_and(|kind| TOOL_CHANGE_BLOCKS.contains(&kind))))
}

/// `_surfaced(entries)`: the tool names the journal's tool-change entries made usable.
pub fn surfaced(entries: &[JsonMap]) -> DomainResult<BTreeSet<String>> {
    let mut names = BTreeSet::new();
    for entry in entries {
        if entry.get("c").and_then(Json::as_str) != Some(TOOL_CHANGE_CLASS) {
            continue;
        }
        if let Some(listed) = entry.get("n").filter(|value| sylvia_domain::pyops::py_truthy(value)) {
            names.extend(py_iter(listed)?.iter().map(py_str));
        }
    }
    Ok(names)
}

/// `_addition_block(tool, declared)`: surface a declared tool by reference, or define one the
/// catalog lacks by value.
pub fn addition_block(tool: &JsonMap, declared: bool) -> JsonMap {
    let mut inner = JsonMap::new();
    if declared {
        inner.insert("type".into(), "tool_reference".into());
        inner.insert("name".into(), tool_name(tool).into());
    } else {
        let function = function_of(tool);
        let mut definition = JsonMap::new();
        definition.insert("name".into(), function.get("name").cloned().unwrap_or_else(|| "".into()));
        definition
            .insert("description".into(), function.get("description").cloned().unwrap_or_else(|| "".into()));
        let default = {
            let mut object = JsonMap::new();
            object.insert("type".into(), "object".into());
            Json::Object(object)
        };
        definition.insert("input_schema".into(), function.get("parameters").cloned().unwrap_or(default));
        inner.insert("type".into(), "tool_definition".into());
        inner.insert("definition".into(), Json::Object(definition));
    }
    let mut block = JsonMap::new();
    block.insert("type".into(), "tool_addition".into());
    block.insert("tool".into(), Json::Object(inner));
    block
}
