//! What the model has been given (L2a): narration facts and transition results as the journal
//! keeps them, and the campaign state as a keyframe followed by small updates.
//!
//! Every comparison is Python's `==` (`py_eq`: `1 == 1.0 == True`, mappings without regard to
//! order), and removing a key keeps the order of the others, as a `dict` does. A malformed value
//! that Python would raise on raises the same class here (`DomainError::uncaught`), with its text.

use super::wire::{canonical_json, content_text, sha};
use super::{
    facts_added_header, facts_header, seal_prefix, update_header, ADDITIVE_MAP_PATHS, DEFAULT_REPLACE_DEPTH,
    FACT_LIST_PATHS, NARRATION_FACTS_LABEL, STATE_LABEL, SUBTREE_REPLACE_DEPTH, UPDATE_LABEL,
};
use serde_json::Value as Json;
use std::collections::HashSet;
use sylvia_base::pyjson::{self, HookFailure};
use sylvia_base::JsonMap;
use sylvia_domain::error::{DomainError, DomainResult};
use sylvia_domain::pyops::{py_eq, py_get_attr, py_item, py_iter, py_str, py_truthy};

/// The facts' JSON is read as Python's decoder reads it; a value Rust cannot hold (a non-finite
/// number) is refused by the declared JSON limits rather than read differently.
fn non_finite() -> DomainError {
    DomainError::uncaught(
        "ValueError",
        "Invalid JSON: a non-finite number cannot be carried by the Rust owner",
    )
}

/// The byte offset of character `index` of `text`.
fn byte_offset(text: &str, index: usize) -> usize {
    text.char_indices().nth(index).map_or(text.len(), |(offset, _)| offset)
}

// ── facts and transition results ──────────────────────────────────────────────────────────

/// `request_values(state)`: (entity id → entity, namespace name → value) the request's
/// campaign state carries.
pub fn request_values(state: Option<&JsonMap>) -> DomainResult<(JsonMap, JsonMap)> {
    let empty = Json::Object(JsonMap::new());
    let campaign = state.and_then(|state| state.get("campaign_state")).filter(|value| py_truthy(value));
    let owned = py_get_attr(campaign.unwrap_or(&empty), "ruleset_owned_values")?;
    let mut entities = JsonMap::new();
    let mut namespaces = JsonMap::new();
    if let Some(Json::Object(owned)) = owned {
        for (key, value) in owned {
            namespaces.insert(key.clone(), value.clone());
            if let (true, Json::Object(members)) = (key.ends_with(".entities"), value) {
                for (id, entity) in members {
                    entities.insert(id.clone(), entity.clone());
                }
            }
        }
    }
    Ok((entities, namespaces))
}

/// `_without_covered_entities(snapshot, entities)`: the entity snapshot minus every entity the
/// campaign state carries with exactly that value.
fn without_covered_entities(snapshot: &Json, entities: &JsonMap) -> Json {
    let Json::Object(snapshot) = snapshot else {
        return snapshot.clone();
    };
    Json::Object(
        snapshot
            .iter()
            .filter(|(key, value)| !entities.get(key.as_str()).is_some_and(|held| py_eq(held, value)))
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect(),
    )
}

/// `fact["current_entities"]` slimmed against `entities`: kept where anything remains, else
/// removed.
fn slim_entities(fact: &mut JsonMap, entities: &JsonMap) {
    let Some(snapshot @ Json::Object(_)) = fact.get("current_entities") else {
        return;
    };
    let remaining = without_covered_entities(snapshot, entities);
    if py_truthy(&remaining) {
        fact.insert("current_entities".into(), remaining);
    } else {
        fact.shift_remove("current_entities");
    }
}

/// `slim_narration_facts(facts, entities)`: narration facts as the journal keeps them. Each
/// entity of a fact's `current_entities` that the same request's campaign state carries with
/// exactly that value is removed; every other key is the commit's own outcome and is kept.
pub fn slim_narration_facts(facts: &[Json], entities: &JsonMap) -> Vec<Json> {
    facts
        .iter()
        .map(|fact| match fact {
            Json::Object(map) if matches!(map.get("current_entities"), Some(Json::Object(_))) => {
                let mut map = map.clone();
                slim_entities(&mut map, entities);
                Json::Object(map)
            }
            other => other.clone(),
        })
        .collect()
}

/// `comparable_facts(facts)`: facts without their entity snapshots, to compare a journaled list
/// with canonical.
pub fn comparable_facts(facts: &[Json]) -> Vec<Json> {
    facts
        .iter()
        .map(|fact| match fact {
            Json::Object(map) => Json::Object(
                map.iter()
                    .filter(|(key, _)| key.as_str() != "current_entities")
                    .map(|(key, value)| (key.clone(), value.clone()))
                    .collect(),
            ),
            other => other.clone(),
        })
        .collect()
}

/// `slim_transition_result(payload, entities, namespaces)`: an automatic engine transition
/// result as the journal keeps it. Removed only where the same request's campaign state carries
/// exactly the same value: `current_entities` per entity; in `result.context`, `actor` and
/// `completed_actor` (copies of the entities `_actor_id` and `_completed_actor_id` name), and
/// `lasting_state` and `time_stop_state` (copies of the Ruleset namespace `_lasting_state_id`
/// and `_time_stop_state_id` name, found by its suffix).
pub fn slim_transition_result(payload: &JsonMap, entities: &JsonMap, namespaces: &JsonMap) -> JsonMap {
    let mut slimmed = payload.clone();
    slim_entities(&mut slimmed, entities);
    let Some(Json::Object(result)) = slimmed.get("result") else {
        return slimmed;
    };
    let Some(Json::Object(context)) = result.get("context") else {
        return slimmed;
    };
    let mut context = context.clone();
    for (key, ident) in [("actor", "_actor_id"), ("completed_actor", "_completed_actor_id")] {
        let covered = match (context.get(ident), context.get(key)) {
            (Some(Json::String(entity_id)), Some(copy)) => {
                entities.get(entity_id.as_str()).is_some_and(|held| py_eq(held, copy))
            }
            _ => false,
        };
        if covered {
            context.shift_remove(key);
        }
    }
    for (key, ident) in [("lasting_state", "_lasting_state_id"), ("time_stop_state", "_time_stop_state_id")] {
        // The suffix only finds the candidate namespace; the copy is removed because the state
        // record holds exactly the same value, never because the names match.
        let covered = match (context.get(ident), context.get(key)) {
            (Some(Json::String(name)), Some(copy)) => namespaces
                .iter()
                .any(|(namespace, value)| namespace.ends_with(&format!(".{name}")) && py_eq(value, copy)),
            _ => false,
        };
        if covered {
            context.shift_remove(key);
        }
    }
    let mut result = result.clone();
    result.insert("context".into(), Json::Object(context));
    slimmed.insert("result".into(), Json::Object(result));
    slimmed
}

/// A narration-facts record's parts (`split_narration_facts`).
#[derive(Debug, Clone, PartialEq)]
pub struct NarrationFactsRecord {
    /// The record adds to the facts already given since the last narration.
    pub added: bool,
    pub facts: Vec<Json>,
    /// The instruction text after the facts.
    pub rest: String,
}

/// `split_narration_facts(content)`: (adds to earlier facts?, facts, instruction text) of a
/// narration-facts record, or `None` when it is not one.
pub fn split_narration_facts(content: &str) -> DomainResult<Option<NarrationFactsRecord>> {
    for (header, added) in [(facts_added_header(), true), (facts_header(), false)] {
        let Some(rest) = content.strip_prefix(header) else {
            continue;
        };
        let (value, end) = match pyjson::raw_decode(rest) {
            Ok((decoded, _)) if decoded.non_finite => return Err(non_finite()),
            Ok((decoded, end)) => (decoded.value, end),
            Err(HookFailure::Unsupported(error)) => {
                return Err(DomainError::uncaught("ValueError", error.message))
            }
            Err(_) => return Ok(None),
        };
        let Json::Array(facts) = value else {
            return Ok(None);
        };
        return Ok(Some(NarrationFactsRecord {
            added,
            facts,
            rest: rest[byte_offset(rest, end)..].to_owned(),
        }));
    }
    Ok(None)
}

/// `narration_facts_given(messages, seal)`: the narration facts the model has been given since
/// the last player message, scanning back only to that message or to the last full list.
pub fn narration_facts_given(messages: &[JsonMap], seal: &str) -> DomainResult<Vec<Json>> {
    let prefix = seal_prefix(seal);
    let mut records: Vec<(bool, Vec<Json>)> = Vec::new();
    for message in messages.iter().rev() {
        let content = content_text(message);
        if message.get("role").and_then(Json::as_str) == Some("user") && !content.starts_with(&prefix) {
            break; // a player message starts a new turn
        }
        let head: String = content.chars().take(96).collect();
        if !head.contains(NARRATION_FACTS_LABEL) {
            continue;
        }
        let body = content.strip_prefix(&prefix).unwrap_or(&content);
        if let Some(record) = split_narration_facts(body)? {
            let full = !record.added;
            records.push((record.added, record.facts));
            if full {
                break; // a full list replaces everything before it
            }
        }
    }
    let mut given: Vec<Json> = Vec::new();
    for (added, facts) in records.into_iter().rev() {
        if added {
            given.extend(facts);
        } else {
            given = facts;
        }
    }
    Ok(given)
}

// ── campaign state: keyframe plus updates ─────────────────────────────────────────────────

/// `_parse_state(content)`: the JSON object after a state record's first line, else `None`.
pub fn parse_state(content: &str) -> Option<JsonMap> {
    let body = content.split_once('\n').map_or("", |(_, body)| body);
    match pyjson::loads(body) {
        Ok(Json::Object(value)) => Some(value),
        _ => None,
    }
}

/// `_get(value, path)`: the value at `path`, `None` when a step is missing or not a mapping.
fn get_path<'a>(value: &'a JsonMap, path: &[&str]) -> Option<&'a Json> {
    let (first, rest) = path.split_first()?;
    let mut current = value.get(*first)?;
    for key in rest {
        current = current.as_object()?.get(*key)?;
    }
    Some(current)
}

/// `_set(value, path, item)`: missing or non-mapping steps become empty mappings.
fn set_path(value: &mut JsonMap, path: &[&str], item: Json) {
    let (last, steps) = path.split_last().expect("a non-empty path");
    let mut current = value;
    for key in steps {
        if !matches!(current.get(*key), Some(Json::Object(_))) {
            current.insert((*key).to_owned(), Json::Object(JsonMap::new()));
        }
        current = current.get_mut(*key).and_then(Json::as_object_mut).expect("a mapping just ensured");
    }
    current.insert((*last).to_owned(), item);
}

/// `_remove(value, path)`: nothing when a step is missing or not a mapping.
fn remove_path(value: &mut JsonMap, path: &[&str]) {
    let Some((last, steps)) = path.split_last() else {
        return;
    };
    let mut current = value;
    for key in steps {
        match current.get_mut(*key) {
            Some(Json::Object(child)) => current = child,
            _ => return,
        }
    }
    current.shift_remove(*last);
}

/// `fact_key(fact)`: its `record_id`, else the SHA-256 of its canonical JSON.
pub fn fact_key(fact: &Json) -> String {
    match fact.get("record_id") {
        Some(Json::String(record_id)) if !record_id.is_empty() => record_id.clone(),
        _ => format!("sha:{}", sha(&canonical_json(fact))),
    }
}

/// `_replace_depth(path)`: the depth at which a changed subtree is replaced whole; the longest
/// configured prefix wins, the first of equal ones.
fn replace_depth(path: &[String]) -> usize {
    let mut best: Option<usize> = None;
    let mut depth = DEFAULT_REPLACE_DEPTH;
    for (prefix, value) in SUBTREE_REPLACE_DEPTH {
        let matches = path.len() >= prefix.len() && path.iter().zip(prefix).all(|(a, b)| a == b);
        if matches && best.is_none_or(|best| prefix.len() > best) {
            best = Some(prefix.len());
            depth = value;
        }
    }
    depth
}

/// `_active_for(active, path)`: the still-active record ids of a fact list, when given.
fn active_for(active: Option<&JsonMap>, path: &[&str]) -> Option<HashSet<String>> {
    match active?.get(&path.join(".")) {
        Some(Json::Array(ids)) => Some(ids.iter().filter_map(Json::as_str).map(str::to_owned).collect()),
        _ => None,
    }
}

/// `_retired(item, active)`: a held fact whose record is no longer active.
fn retired(item: &Json, active: Option<&HashSet<String>>) -> bool {
    let key = fact_key(item);
    active.is_some_and(|active| !key.starts_with("sha:") && !active.contains(&key))
}

/// `{fact_key(item): position for position, item in enumerate(items)}`: a repeated key keeps
/// its last position.
fn positions(items: &[Json]) -> std::collections::HashMap<String, usize> {
    items.iter().enumerate().map(|(position, item)| (fact_key(item), position)).collect()
}

/// Upsert `items` into `merged` by fact key: a known key is replaced in place, a new one
/// appended.
fn upsert(merged: &mut Vec<Json>, items: &[Json]) {
    let mut index = positions(merged);
    for item in items {
        let key = fact_key(item);
        match index.get(&key) {
            Some(&position) => merged[position] = item.clone(),
            None => {
                index.insert(key, merged.len());
                merged.push(item.clone());
            }
        }
    }
}

/// `state_target(view, canonical, active)`: what the model should hold, canonical state with
/// its selection-derived parts kept additive. A held fact whose record left the active set is
/// dropped instead of kept.
pub fn state_target(view: Option<&JsonMap>, canonical: &JsonMap, active: Option<&JsonMap>) -> JsonMap {
    let mut target = canonical.clone();
    let Some(view) = view else {
        return target;
    };
    for path in FACT_LIST_PATHS {
        let (Some(Json::Array(held)), Some(Json::Array(current))) =
            (get_path(view, path), get_path(canonical, path))
        else {
            continue;
        };
        let ids = active_for(active, path);
        let current_keys: HashSet<String> = current.iter().map(fact_key).collect();
        let mut merged: Vec<Json> = held
            .iter()
            .filter(|item| !(retired(item, ids.as_ref()) && !current_keys.contains(&fact_key(item))))
            .cloned()
            .collect();
        upsert(&mut merged, current);
        set_path(&mut target, path, Json::Array(merged));
    }
    for path in ADDITIVE_MAP_PATHS {
        let (Some(Json::Object(held)), Some(Json::Object(current))) =
            (get_path(view, path), get_path(canonical, path))
        else {
            continue;
        };
        let mut merged = held.clone();
        for (key, items) in current {
            match (items, merged.get(key)) {
                (Json::Array(items), Some(Json::Array(existing))) => {
                    let mut combined = existing.clone();
                    combined.extend(
                        items.iter().filter(|item| !existing.iter().any(|old| py_eq(old, item))).cloned(),
                    );
                    merged.insert(key.clone(), Json::Array(combined));
                }
                _ => {
                    merged.insert(key.clone(), items.clone());
                }
            }
        }
        set_path(&mut target, path, Json::Object(merged));
    }
    target
}

/// `_diff(old, new, path, sets, removes)`: a mapping is descended while its path is shallower
/// than its replacement depth; any other changed value is set whole.
fn diff(old: &Json, new: &Json, path: &mut Vec<String>, sets: &mut Vec<Json>, removes: &mut Vec<Json>) {
    if py_eq(old, new) {
        return;
    }
    let descend = matches!((old, new), (Json::Object(_), Json::Object(_)))
        && (path.is_empty() || path.len() < replace_depth(path));
    if let (true, Json::Object(old), Json::Object(new)) = (descend, old, new) {
        for (key, value) in new {
            path.push(key.clone());
            match old.get(key) {
                None => sets.push(set_entry(path, value)),
                Some(previous) => diff(previous, value, path, sets, removes),
            }
            path.pop();
        }
        for key in old.keys().filter(|key| !new.contains_key(*key)) {
            removes
                .push(Json::Array(path.iter().chain([key]).map(|step| Json::String(step.clone())).collect()));
        }
        return;
    }
    sets.push(set_entry(path, new));
}

fn set_entry(path: &[String], value: &Json) -> Json {
    let mut entry = JsonMap::new();
    entry.insert("path".into(), Json::Array(path.iter().map(|step| Json::String(step.clone())).collect()));
    entry.insert("value".into(), value.clone());
    Json::Object(entry)
}

fn path_json(path: &[&str]) -> Json {
    Json::Array(path.iter().map(|step| Json::String((*step).to_owned())).collect())
}

/// `state_update(view, canonical, active)`: the smallest update, by the configured
/// granularity, that brings `view` to the target; `None` when it already holds it.
pub fn state_update(view: &JsonMap, canonical: &JsonMap, active: Option<&JsonMap>) -> Option<JsonMap> {
    let target = state_target(Some(view), canonical, active);
    let mut facts: Vec<Json> = Vec::new();
    let mut stripped_view = view.clone();
    let mut stripped_target = target.clone();
    for path in FACT_LIST_PATHS {
        let (Some(Json::Array(held)), Some(Json::Array(wanted))) =
            (get_path(view, path), get_path(&target, path))
        else {
            continue;
        };
        let mut known: std::collections::HashMap<String, &Json> = std::collections::HashMap::new();
        for item in held {
            known.insert(fact_key(item), item);
        }
        let upserts: Vec<Json> = wanted
            .iter()
            .filter(|item| !py_eq(known.get(&fact_key(item)).copied().unwrap_or(&Json::Null), item))
            .cloned()
            .collect();
        let kept: HashSet<String> = wanted.iter().map(fact_key).collect();
        let mut removed: Vec<String> = Vec::new();
        for key in held.iter().map(fact_key).filter(|key| !kept.contains(key)) {
            if !removed.contains(&key) {
                removed.push(key);
            }
        }
        let mut entry = JsonMap::new();
        entry.insert("path".into(), path_json(path));
        if !removed.is_empty() {
            entry.insert("remove".into(), Json::Array(removed.iter().cloned().map(Json::String).collect()));
        }
        if !upserts.is_empty() {
            entry.insert("upsert".into(), Json::Array(upserts.clone()));
        }
        if !removed.is_empty() || !upserts.is_empty() {
            facts.push(Json::Object(entry));
        }
        remove_path(&mut stripped_view, path);
        remove_path(&mut stripped_target, path);
    }
    let mut sets = Vec::new();
    let mut removes = Vec::new();
    diff(
        &Json::Object(stripped_view),
        &Json::Object(stripped_target),
        &mut Vec::new(),
        &mut sets,
        &mut removes,
    );
    if sets.is_empty() && removes.is_empty() && facts.is_empty() {
        return None;
    }
    let mut update = JsonMap::new();
    update.insert(
        "campaign_revision".into(),
        get_path(canonical, &["campaign", "revision"]).cloned().unwrap_or(Json::Null),
    );
    if !sets.is_empty() {
        update.insert("set".into(), Json::Array(sets));
    }
    if !removes.is_empty() {
        update.insert("remove".into(), Json::Array(removes));
    }
    if !facts.is_empty() {
        update.insert("facts".into(), Json::Array(facts));
    }
    Some(update)
}

/// `[str(key) for key in value]`.
fn path_steps(value: &Json) -> DomainResult<Vec<String>> {
    Ok(py_iter(value)?.iter().map(py_str).collect())
}

/// `mapping.get(key, ())` iterated.
fn entries_of(update: &JsonMap, key: &str) -> DomainResult<Vec<Json>> {
    update.get(key).map_or(Ok(Vec::new()), py_iter)
}

/// `apply_state_update(view, update)`: `view` with an update's sets, removes and fact upserts
/// applied in that order.
pub fn apply_state_update(view: &JsonMap, update: &JsonMap) -> DomainResult<JsonMap> {
    let mut result = view.clone();
    for entry in entries_of(update, "set")? {
        let path = path_steps(py_item(&entry, "path")?)?;
        let value = py_item(&entry, "value")?;
        if !path.is_empty() {
            let steps: Vec<&str> = path.iter().map(String::as_str).collect();
            set_path(&mut result, &steps, value.clone());
        } else if let Json::Object(value) = value {
            result = value.clone();
        }
    }
    for path in entries_of(update, "remove")? {
        if py_truthy(&path) {
            let steps = path_steps(&path)?;
            let steps: Vec<&str> = steps.iter().map(String::as_str).collect();
            remove_path(&mut result, &steps);
        }
    }
    for entry in entries_of(update, "facts")? {
        let path = path_steps(py_item(&entry, "path")?)?;
        let steps: Vec<&str> = path.iter().map(String::as_str).collect();
        let mut merged: Vec<Json> = match get_path(&result, &steps) {
            Some(Json::Array(held)) => held.clone(),
            _ => Vec::new(),
        };
        let removed: Vec<Json> = match py_get_attr(&entry, "remove")? {
            Some(value) => py_iter(value)?,
            None => Vec::new(),
        };
        for key in &removed {
            sylvia_domain::pyops::py_require_hashable(key)?;
        }
        merged.retain(|item| {
            let key = Json::String(fact_key(item));
            !removed.iter().any(|removed| py_eq(removed, &key))
        });
        let upserts = match py_get_attr(&entry, "upsert")? {
            Some(value) => py_iter(value)?,
            None => Vec::new(),
        };
        upsert(&mut merged, &upserts);
        if steps.is_empty() {
            // `_set(result, [], merged)` indexes `path[-1]` of an empty path.
            return Err(DomainError::uncaught("IndexError", "list index out of range"));
        }
        set_path(&mut result, &steps, Json::Array(merged));
    }
    Ok(result)
}

/// `state_covers(view, canonical, active)`: the model's view equals canonical state modulo the
/// additive-facts rule.
pub fn state_covers(view: Option<&JsonMap>, canonical: &JsonMap, active: Option<&JsonMap>) -> bool {
    view.is_some_and(|view| {
        py_eq(&Json::Object(state_target(Some(view), canonical, active)), &Json::Object(view.clone()))
    })
}

/// `update_record_content(update)`: the update record's text.
pub fn update_record_content(update: &JsonMap) -> String {
    format!("{}{}", update_header(), canonical_json(&Json::Object(update.clone())))
}

/// `reconstruct_state(entries, seal)`: what the model holds, rebuilt from journaled keyframes
/// and updates. A keyframe that does not parse leaves nothing held until the next one.
pub fn reconstruct_state(entries: &[JsonMap], seal: &str) -> DomainResult<Option<JsonMap>> {
    let mut view: Option<JsonMap> = None;
    let prefix = seal_prefix(seal);
    for entry in entries {
        if entry.get("c").and_then(Json::as_str) != Some("state") {
            continue;
        }
        let message = entry.get("m").ok_or_else(|| DomainError::uncaught("KeyError", "'m'"))?;
        let content = match py_get_attr(message, "content")? {
            None => String::new(),
            Some(value) => py_str(value),
        };
        let content = content.strip_prefix(&prefix).unwrap_or(&content);
        if content.starts_with(&format!("{STATE_LABEL} ")) {
            view = parse_state(content);
        } else if content.starts_with(&format!("{UPDATE_LABEL} ")) {
            if let (Some(held), Some(update)) = (&view, parse_state(content)) {
                view = Some(apply_state_update(held, &update)?);
            }
        }
    }
    Ok(view)
}
