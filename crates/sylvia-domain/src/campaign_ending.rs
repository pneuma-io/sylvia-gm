//! `domain/campaign_ending.py`: authored campaign conclusions and their saved, player-selected
//! continuation.
//!
//! Plans reach these checks as untyped JSON, so the port follows Python's duck typing: a
//! `str` is a sequence of characters, `str(value)` is the value's `repr` for anything but
//! text, and the exceptions Python would raise on a malformed value are raised with its text.

use crate::error::{DomainError, DomainResult};
use crate::pyops::{
    py_eq, py_get_attr, py_is_sequence, py_item, py_iter, py_last, py_require_hashable, py_set, py_str,
    py_truthy, py_unhashable,
};
use std::collections::BTreeSet;
use sylvia_base::{Json, JsonMap};

fn text_is(value: Option<&Json>, expected: &str) -> bool {
    value.is_some_and(|value| py_eq(value, &Json::from(expected)))
}

/// `intended_endings(plan)`: finite ending preparation; older and explicitly ongoing Plans
/// have none.
pub fn intended_endings(plan: &JsonMap) -> Vec<&JsonMap> {
    let Some(Json::Object(ending)) = plan.get("ending") else { return Vec::new() };
    if !text_is(ending.get("mode"), "finite") {
        return Vec::new();
    }
    match ending.get("outcomes") {
        Some(Json::Array(items)) => items.iter().filter_map(Json::as_object).collect(),
        // A missing value is `()`; text is a sequence of characters, none of them a mapping.
        _ => Vec::new(),
    }
}

/// `ending_progress_id(ending)`: ending progress cannot resolve an act or objective.
pub fn ending_progress_id(ending: &JsonMap) -> DomainResult<String> {
    let id = ending.get("id").ok_or_else(|| DomainError::uncaught("KeyError", "'id'"))?;
    Ok(format!("ending:{}", py_str(id)))
}

/// `required_story_items(plan)`: finite acts and their linked objectives.
pub fn required_story_items(plan: &JsonMap) -> DomainResult<BTreeSet<String>> {
    let mut items = BTreeSet::new();
    let Some(structure) = plan.get("structure") else { return Ok(items) };
    if !py_is_sequence(structure) {
        return Ok(items);
    }
    for act in py_iter(structure)? {
        if let Json::Object(map) = &act {
            items.insert(py_str(py_item(&act, "id")?));
            if let Some(objectives) = map.get("objectives") {
                items.extend(py_iter(objectives)?.iter().map(py_str));
            }
        }
    }
    Ok(items)
}

/// `resolved_progress(progress)`: the latest recorded status, including a later correction.
pub fn resolved_progress<'a>(progress: impl IntoIterator<Item = &'a Json>) -> DomainResult<BTreeSet<String>> {
    let mut latest: Vec<(String, Option<Json>)> = Vec::new();
    for item in progress {
        let key = py_str(py_item(item, "plan_item_id")?);
        let status = py_get_attr(item, "status")?.cloned();
        match latest.iter_mut().find(|(existing, _)| *existing == key) {
            Some(entry) => entry.1 = status,
            None => latest.push((key, status)),
        }
    }
    Ok(latest
        .into_iter()
        .filter(|(_, status)| text_is(status.as_ref(), "resolved"))
        .map(|(key, _)| key)
        .collect())
}

/// `required_ending_items(plan, ending)`: an explicit early outcome does not finish unplayed acts.
pub fn required_ending_items(plan: &JsonMap, ending: &JsonMap) -> DomainResult<BTreeSet<String>> {
    if ending.get("requires_all_acts") == Some(&Json::Bool(false)) {
        return Ok(BTreeSet::new());
    }
    required_story_items(plan)
}

/// `{ending_progress_id(item): item for item in intended_endings(plan)}`.
fn ending_map(plan: &JsonMap) -> DomainResult<Vec<(String, &JsonMap)>> {
    let mut endings: Vec<(String, &JsonMap)> = Vec::new();
    for item in intended_endings(plan) {
        let key = ending_progress_id(item)?;
        match endings.iter_mut().find(|(existing, _)| *existing == key) {
            Some(entry) => entry.1 = item,
            None => endings.push((key, item)),
        }
    }
    Ok(endings)
}

/// `check_ending_progress(plan, progress, updates)`: reject a premature or ambiguous ending;
/// narrative facts remain the GM's judgement.
pub fn check_ending_progress(plan: &JsonMap, progress: &[Json], updates: &[Json]) -> DomainResult<()> {
    let endings = ending_map(plan)?;
    let declared = |identity: &str| endings.iter().any(|(key, _)| key == identity);
    for update in updates {
        let identity = match py_get_attr(update, "plan_item_id")? {
            Some(value) => py_str(value),
            None => String::new(),
        };
        if identity.starts_with("ending:") && !declared(&identity) {
            return Err(DomainError::value("Ending is not declared by this Campaign Plan"));
        }
    }
    let ending = plan.get("ending");
    let structure = plan.get("structure");
    if let (Some(Json::Object(ending)), Some(structure)) = (ending, structure) {
        if text_is(ending.get("mode"), "ongoing") && py_is_sequence(structure) && py_truthy(structure) {
            for item in updates {
                let identity = py_get_attr(item, "plan_item_id")?.cloned().unwrap_or(Json::Null);
                let final_id = py_item(&py_last(structure)?, "id")?.clone();
                if py_eq(&identity, &final_id) && text_is(py_get_attr(item, "status")?, "resolved") {
                    return Err(DomainError::value(
                        "The final act is explicitly ongoing and has no completion",
                    ));
                }
            }
        }
    }
    let mut resolving = false;
    for item in updates {
        let identity = py_get_attr(item, "plan_item_id")?.cloned().unwrap_or(Json::Null);
        py_require_hashable(&identity)?;
        if identity.as_str().is_some_and(declared) && text_is(py_get_attr(item, "status")?, "resolved") {
            resolving = true;
            break;
        }
    }
    if !resolving {
        return Ok(());
    }
    let resolved = resolved_progress(progress.iter().chain(updates))?;
    let reached: Vec<&(String, &JsonMap)> =
        endings.iter().filter(|(key, _)| resolved.contains(key)).collect();
    for (_, ending) in &reached {
        if !required_ending_items(plan, ending)?.is_subset(&resolved) {
            return Err(DomainError::value(
                "Resolve the required acts and objectives before recording the ending",
            ));
        }
    }
    if reached.len() > 1 {
        return Err(DomainError::value(
            "Only one intended ending can be resolved; retire a superseded outcome",
        ));
    }
    Ok(())
}

/// `validate_ending(plan)`: outcome identity and ongoing-act shape beyond the closed JSON
/// definition.
pub fn validate_ending(plan: &JsonMap) -> DomainResult<()> {
    let Some(ending) = plan.get("ending") else {
        return Ok(()); // Retained pre-ending-format snapshots remain readable without migration.
    };
    let Json::Object(ending) = ending else {
        return Err(DomainError::value("Campaign ending must be an object"));
    };
    let structure = plan.get("structure").ok_or_else(|| DomainError::uncaught("KeyError", "'structure'"))?;
    if !py_is_sequence(structure) || !py_truthy(structure) {
        return Err(DomainError::value("Campaign ending requires a final act"));
    }
    let Json::Object(final_act) = py_last(structure)? else {
        return Err(DomainError::value("Campaign final act must be an object"));
    };
    if text_is(ending.get("mode"), "ongoing") {
        if final_act.get("objectives").is_some_and(py_truthy) {
            return Err(DomainError::value("An ongoing final act has no required completion objectives"));
        }
        return Ok(());
    }
    let outcomes = intended_endings(plan);
    let identities: Vec<Json> =
        outcomes.iter().map(|item| item.get("id").cloned().unwrap_or(Json::Null)).collect();
    if let Some(unhashable) = identities.iter().find(|id| matches!(id, Json::Array(_) | Json::Object(_))) {
        return Err(py_unhashable(unhashable));
    }
    let unique = py_set(&Json::Array(identities.clone())).expect("every identity is hashable");
    if unique.len() != identities.len() {
        return Err(DomainError::value("Campaign ending identities must be unique"));
    }
    let story = required_story_items(plan)?;
    let mut progress_ids = BTreeSet::new();
    for item in &outcomes {
        progress_ids.insert(ending_progress_id(item)?);
    }
    if !story.is_disjoint(&progress_ids) {
        return Err(DomainError::value("Campaign ending progress identity overlaps an act or objective"));
    }
    Ok(())
}
