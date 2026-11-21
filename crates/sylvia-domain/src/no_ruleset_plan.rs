//! `domain/no_ruleset_plan.py`: structured Campaign Plans for No Ruleset campaigns.
//!
//! A No Ruleset Plan is an ordinary format-2 Campaign Plan whose Ruleset and Campaign Setting
//! bindings are both null. Its own `initial_state.entities` are its world: each person, place
//! or item is addressed as `entities/<id>` wherever a Ruleset Plan would use a Setting path.

use crate::authority::validate_authority_identity_value;
use crate::error::{DomainError, DomainResult};
use crate::pyops::{py_eq, py_item, py_iter, py_str, py_truthy};
use serde_json::json;
use std::collections::BTreeSet;
use sylvia_base::pystr;
use sylvia_base::{Json, JsonMap};

/// `NO_RULESET_ENTITY_PREFIX`.
pub const NO_RULESET_ENTITY_PREFIX: &str = "entities/";
const ENTITY_CONTROL_FIELDS: [&str; 3] = ["name", "visibility", "known_by"];

fn text_is(value: Option<&Json>, expected: &str) -> bool {
    value.is_some_and(|value| py_eq(value, &Json::from(expected)))
}

/// `is_no_ruleset_plan(plan)`: both bindings present and null.
pub fn is_no_ruleset_plan(plan: &JsonMap) -> bool {
    let Some(Json::Object(bindings)) = plan.get("bindings") else { return false };
    bindings.get("ruleset") == Some(&Json::Null) && bindings.get("campaign_setting") == Some(&Json::Null)
}

/// `_entities(plan)`: the mapping items of `initial_state.entities` when it is a list.
fn entities(plan: &JsonMap) -> Vec<&JsonMap> {
    let Some(Json::Object(initial_state)) = plan.get("initial_state") else { return Vec::new() };
    match initial_state.get("entities") {
        Some(Json::Array(items)) => items.iter().filter_map(Json::as_object).collect(),
        _ => Vec::new(),
    }
}

/// `validate_no_ruleset_world(plan)`: check the Plan's own world and return the paths it may
/// reference.
pub fn validate_no_ruleset_world(plan: &JsonMap) -> DomainResult<BTreeSet<String>> {
    if let Some(Json::Object(initial_state)) = plan.get("initial_state") {
        if initial_state.get("mechanics").is_some_and(py_truthy) {
            return Err(DomainError::value("A No Ruleset Plan cannot declare Ruleset mechanical state"));
        }
        if initial_state.contains_key("party_placements") {
            return Err(DomainError::value("A No Ruleset Plan cannot declare Ruleset party placements"));
        }
    }
    if let Some(Json::Object(party)) = plan.get("starting_party") {
        if let Some(Json::Object(player)) = party.get("player_character") {
            if text_is(player.get("mode"), "fixed") {
                return Err(DomainError::value("A No Ruleset Plan cannot fix a Setting player character"));
            }
        }
        if party.get("required_companions").is_some_and(py_truthy) {
            return Err(DomainError::value("A No Ruleset Plan cannot require Setting companions"));
        }
    }
    let mut paths = BTreeSet::new();
    for entity in entities(plan) {
        let id = entity.get("id").unwrap_or(&Json::Null);
        let identity = validate_authority_identity_value(id, "No Ruleset Plan entity", false)?;
        let path = format!("{NO_RULESET_ENTITY_PREFIX}{identity}");
        let expected = json!({"id": identity, "path": path});
        if !entity.get("source_ref").is_some_and(|source| source.is_object() && py_eq(source, &expected)) {
            return Err(DomainError::value(format!(
                "No Ruleset Plan entity {identity} must use source_ref {{\"id\": \"{identity}\", \"path\": \"{path}\"}}"
            )));
        }
        let Some(Json::Object(state)) = entity.get("state") else {
            return Err(DomainError::value(format!("No Ruleset Plan entity {identity} state is invalid")));
        };
        let named = match state.get("name") {
            Some(Json::String(name)) => {
                let stripped = pystr::strip(name);
                !stripped.is_empty() && name == stripped && pystr::len(name) <= 300
            }
            _ => false,
        };
        if !named {
            return Err(DomainError::value(format!("No Ruleset Plan entity {identity} needs a name")));
        }
        let visibility = state.get("visibility").cloned().unwrap_or_else(|| Json::from("gm_private"));
        if !matches!(visibility.as_str(), Some("player_known" | "gm_private")) {
            return Err(DomainError::value(format!(
                "No Ruleset Plan entity {identity} visibility is invalid"
            )));
        }
        let known_by = state.get("known_by").cloned().unwrap_or_else(|| json!([]));
        let Json::Array(known_by) = known_by else {
            return Err(DomainError::value(format!(
                "No Ruleset Plan entity {identity} knowledge is invalid"
            )));
        };
        for item in &known_by {
            validate_authority_identity_value(item, "No Ruleset Plan entity knowledge", false)?;
        }
        paths.insert(path);
    }
    Ok(paths)
}

/// `no_ruleset_world_records(plan)`: `(record_id, entity payload)` pairs that seed Campaign
/// State at acceptance. Prepared entities stay `gm_private` unless marked `player_known`.
pub fn no_ruleset_world_records(plan: &JsonMap) -> DomainResult<Vec<(String, JsonMap)>> {
    let mut records = Vec::new();
    for entity in entities(plan) {
        let entity_json = Json::Object(entity.clone());
        let identity = py_str(py_item(&entity_json, "id")?);
        let Json::Object(state) = py_item(&entity_json, "state")? else {
            return Err(DomainError::uncaught("AssertionError", ""));
        };
        let known_by = state.get("known_by").cloned().unwrap_or_else(|| json!([]));
        let entity_kind = py_str(py_item(&entity_json, "kind")?);
        let name = py_str(state.get("name").ok_or_else(|| DomainError::uncaught("KeyError", "'name'"))?);
        let attributes: JsonMap = state
            .iter()
            .filter(|(key, _)| !ENTITY_CONTROL_FIELDS.contains(&key.as_str()))
            .map(|(key, value)| (key.clone(), value.clone()))
            .collect();
        let visibility = state.get("visibility").map(py_str).unwrap_or_else(|| "gm_private".to_owned());
        // `[str(item) for item in known_by] if isinstance(known_by, Sequence) else []`: text
        // is a sequence of its characters.
        let known_by: Vec<Json> = match &known_by {
            Json::Array(_) | Json::String(_) => {
                py_iter(&known_by)?.iter().map(|item| Json::String(py_str(item))).collect()
            }
            _ => Vec::new(),
        };
        let payload = json!({
            "entity_kind": entity_kind,
            "name": name,
            "attributes": attributes,
            "visibility": visibility,
            "known_by": known_by,
            "source_ref": identity,
            "origin": "accepted_initial_state",
        });
        records.push((identity, payload.as_object().cloned().expect("an object")));
    }
    Ok(records)
}
