//! `agent/_tool_exposure.py`: which Ruleset tools a model is shown in the current campaign state.
//!
//! A Ruleset may annotate a tool's input schema with `x-sylvia-gm-exposure`: conditions, any one
//! of which makes the tool relevant now. A tool whose conditions all fail is left out of the
//! model's tool list; the authority catalog, engine admission and schema validation are
//! unchanged. The conditions are deliberately simple and Ruleset-neutral:
//!
//! - `{"spells": [spell_id, ...]}`: any listed spell can be cast by a current participant or
//!   prepared creature, or appears as a string anywhere in the campaign state (a lasting effect
//!   keeps the id of the spell that created it);
//! - `{"entity_field": "dotted.path", "op": "not_null" | "true" | "gte1"}`: some saved entity in a
//!   `*.entities` state root has that field and it satisfies the operator.
//!
//! A schema without the annotation, or with a condition this host does not understand, is always
//! shown. Nothing here is specific to the GM: any tool list over Ruleset tools can use it.

use serde_json::Value as Json;
use std::collections::BTreeSet;
use sylvia_base::JsonMap;

/// `EXPOSURE_KEY`.
pub const EXPOSURE_KEY: &str = "x-sylvia-gm-exposure";
/// `SPELL_SCOPE_KEY`.
pub const SPELL_SCOPE_KEY: &str = "x-sylvia-gm-spell-scope";
/// `_ENTITY_OPERATORS`.
const ENTITY_OPERATORS: [&str; 3] = ["not_null", "true", "gte1"];

/// `_string_list(value)`: a non-empty list of non-empty strings, else `None`.
fn string_list(value: Option<&Json>) -> Option<Vec<&str>> {
    let Some(Json::Array(items)) = value else { return None };
    if items.is_empty() {
        return None;
    }
    items.iter().map(|item| item.as_str().filter(|text| !text.is_empty())).collect()
}

/// `_understood(condition)`.
fn understood(condition: &JsonMap) -> bool {
    let keys: BTreeSet<&str> = condition.keys().map(String::as_str).collect();
    if keys == BTreeSet::from(["spells"]) {
        return string_list(condition.get("spells")).is_some();
    }
    if keys == BTreeSet::from(["entity_field", "op"]) {
        let path_ok = condition["entity_field"]
            .as_str()
            .is_some_and(|path| !path.is_empty() && path.split('.').all(|part| !part.is_empty()));
        // `condition["op"] in _ENTITY_OPERATORS`: only a string can be one of them.
        return path_ok && condition["op"].as_str().is_some_and(|op| ENTITY_OPERATORS.contains(&op));
    }
    false
}

/// `exposure_conditions(schema)`: the declared conditions, or `None` when the tool is always
/// shown.
pub fn exposure_conditions(schema: &JsonMap) -> Option<Vec<&JsonMap>> {
    let Some(Json::Object(raw)) = schema.get(EXPOSURE_KEY) else { return None };
    let Some(Json::Array(conditions)) = raw.get("any_of") else { return None };
    if conditions.is_empty() {
        return None;
    }
    let conditions: Vec<&JsonMap> = conditions.iter().map(Json::as_object).collect::<Option<_>>()?;
    conditions.iter().all(|condition| understood(condition)).then_some(conditions)
}

/// `exposure_spell_ids(schemas)`: every spell id any exposure condition asks about.
pub fn exposure_spell_ids<'a>(schemas: impl IntoIterator<Item = &'a JsonMap>) -> BTreeSet<String> {
    let mut found = BTreeSet::new();
    for schema in schemas {
        for condition in exposure_conditions(schema).unwrap_or_default() {
            if let Some(spells) = string_list(condition.get("spells")) {
                found.extend(spells.into_iter().map(str::to_owned));
            }
        }
    }
    found
}

/// `spell_ids_in_state(state, wanted)`: which of `wanted` appear as string values anywhere in
/// the campaign state.
pub fn spell_ids_in_state(state: &JsonMap, wanted: &BTreeSet<String>) -> BTreeSet<String> {
    let mut found = BTreeSet::new();
    if wanted.is_empty() {
        return found;
    }
    let mut stack: Vec<&Json> = state.values().collect();
    while let Some(value) = stack.pop() {
        match value {
            Json::String(text) if wanted.contains(text) => {
                found.insert(text.clone());
            }
            Json::Object(map) => stack.extend(map.values()),
            Json::Array(items) => stack.extend(items),
            _ => {}
        }
    }
    found
}

/// `_field(entity, path)`: the value at a dotted path, if every part is present.
fn field<'a>(entity: &'a JsonMap, path: &str) -> Option<&'a Json> {
    let mut parts = path.split('.');
    let mut value = entity.get(parts.next()?)?;
    for part in parts {
        value = value.as_object()?.get(part)?;
    }
    Some(value)
}

/// `_entity_field_holds(state, path, operator)`.
fn entity_field_holds(state: &JsonMap, path: &str, operator: &str) -> bool {
    let entities = state
        .iter()
        .filter(|(key, _)| key.ends_with(".entities"))
        .filter_map(|(_, root)| root.as_object())
        .flat_map(|root| root.values().filter_map(Json::as_object));
    for entity in entities {
        let Some(value) = field(entity, path) else { continue };
        let holds = match operator {
            "not_null" => !value.is_null(),
            "true" => value == &Json::Bool(true),
            "gte1" => value.as_f64().is_some_and(|number| number >= 1.0) && !value.is_boolean(),
            _ => false,
        };
        if holds {
            return true;
        }
    }
    false
}

/// `ExposureContext`: exposure conditions evaluated against one campaign state, which is scanned
/// at most once.
pub struct ExposureContext<'a> {
    state: &'a JsonMap,
    present_spells: BTreeSet<String>,
}

impl<'a> ExposureContext<'a> {
    /// `ExposureContext(state, castable_spell_ids=..., asked_spell_ids=...)`.
    pub fn new(state: &'a JsonMap, castable_spell_ids: &BTreeSet<String>, asked_spell_ids: &BTreeSet<String>) -> Self {
        let unasked: BTreeSet<String> = asked_spell_ids.difference(castable_spell_ids).cloned().collect();
        let mut present_spells = castable_spell_ids.clone();
        present_spells.extend(spell_ids_in_state(state, &unasked));
        Self { state, present_spells }
    }

    /// `exposed(schema)`: whether the tool is shown now.
    pub fn exposed(&self, schema: &JsonMap) -> bool {
        let Some(conditions) = exposure_conditions(schema) else { return true };
        for condition in conditions {
            if let Some(spells) = string_list(condition.get("spells")) {
                if spells.iter().any(|spell| self.present_spells.contains(*spell)) {
                    return true;
                }
                continue;
            }
            let path = condition["entity_field"].as_str().unwrap_or_default();
            let operator = condition["op"].as_str().unwrap_or_default();
            if entity_field_holds(self.state, path, operator) {
                return true;
            }
        }
        false
    }
}

/// `scoped_spell_choices(enum, castable_spell_ids=..., scope=..., aliases=...)`: one `spell`
/// choice list narrowed to castable spells this tool accepts. `scope` is the optional
/// `x-sylvia-gm-spell-scope` list; an alias stays when the spell it names stays.
pub fn scoped_spell_choices(
    choices: &[Json],
    castable_spell_ids: Option<&BTreeSet<String>>,
    scope: Option<&Json>,
    aliases: Option<&Json>,
) -> Vec<Json> {
    let mut allowed: Option<BTreeSet<String>> = castable_spell_ids.cloned();
    if let Some(scope_ids) = string_list(scope) {
        let scope_ids: BTreeSet<String> = scope_ids.into_iter().map(str::to_owned).collect();
        allowed = Some(match allowed {
            None => scope_ids,
            Some(allowed) => allowed.intersection(&scope_ids).cloned().collect(),
        });
    }
    let Some(allowed) = allowed else { return choices.to_vec() };
    let alias_targets: JsonMap = match aliases {
        Some(Json::Object(map)) => map.clone(),
        _ => JsonMap::new(),
    };
    let target = |value: &str| -> Option<String> {
        alias_targets.get(value).map(|target| match target {
            Json::String(text) => text.clone(),
            other => sylvia_base::pystr::repr_json(other),
        })
    };
    choices
        .iter()
        .filter(|value| {
            value.as_str().is_some_and(|value| {
                allowed.contains(value) || target(value).is_some_and(|target| allowed.contains(&target))
            })
        })
        .cloned()
        .collect()
}
