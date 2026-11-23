//! `agent/_provider_session.py`, a mechanic call's arguments before it is submitted: the
//! package-declared derivations from participant authority (`_derive_mechanics_parameters`),
//! exact state references filled in (`_normalize_state_references`), and the required inputs
//! still missing (`_missing_required_arguments`).

use crate::argument_contracts::{py_str, type_name};
use crate::native_continuation::python_equal;
use indexmap::IndexMap;
use serde_json::Value as Json;
use std::collections::{BTreeSet, HashMap};
use sylvia_base::{pystr, CodedError, JsonMap};
use sylvia_domain::authority::Participant;

/// Why a derivation refused the call: Python's `CommandRejected` (or, with `field_paths`, its
/// `MechanicsRejected`), which the batch turns into the call's result; any other exception
/// (`fault`) is not caught there.
#[derive(Debug, Clone, PartialEq)]
pub enum DerivationError {
    /// `CommandRejected(message, code=...)`, or `MechanicsRejected` when `field_paths` is set.
    Rejected { code: String, message: String, field_paths: Option<Vec<String>> },
    /// An exception the batch does not catch (`TypeError` for an unhashable value).
    Fault(CodedError),
}

fn rejected(code: &str, message: impl Into<String>) -> DerivationError {
    DerivationError::Rejected { code: code.to_owned(), message: message.into(), field_paths: None }
}

/// `str(value)`, as an f-string writes it.
fn formatted(value: Option<&Json>) -> String {
    value.map_or_else(|| "None".to_owned(), py_str)
}

/// `value in container` for a decoded JSON container: a list's items by `==`, a dict's keys, or
/// a substring of a string; an unhashable value looked up in a dict is a `TypeError`.
fn contains(container: &Json, value: &Json) -> Result<bool, CodedError> {
    Ok(match container {
        Json::Array(items) => items.iter().any(|item| python_equal(item, value)),
        Json::Object(map) => match value {
            Json::Array(_) | Json::Object(_) => {
                return Err(CodedError::new(
                    "internal_error",
                    format!("unhashable type: '{}'", type_name(value)),
                )
                .with_error_type("TypeError"))
            }
            Json::String(key) => map.contains_key(key),
            _ => false,
        },
        Json::String(text) => match value {
            Json::String(part) => text.contains(part.as_str()),
            other => {
                return Err(CodedError::new(
                    "internal_error",
                    format!("'in <string>' requires string as left operand, not {}", type_name(other)),
                )
                .with_error_type("TypeError"))
            }
        },
        other => {
            return Err(CodedError::new(
                "internal_error",
                format!("argument of type '{}' is not iterable", type_name(other)),
            )
            .with_error_type("TypeError"))
        }
    })
}

/// `_derive_mechanics_parameters(parameters, derivations, participants)`: resolve the
/// package-declared non-choice arguments from current participant authority.
pub fn derive_mechanics_parameters(
    parameters: &JsonMap,
    derivations: &JsonMap,
    participants: &[Participant],
) -> Result<JsonMap, DerivationError> {
    let mut derived = parameters.clone();
    let participant_by_id: HashMap<&str, &Participant> =
        participants.iter().map(|participant| (participant.participant_id(), participant)).collect();
    for (target, specification) in derivations {
        let specification = specification.as_object().cloned().unwrap_or_default();
        let kind = specification.get("kind").and_then(Json::as_str);
        if kind == Some("constant") {
            derived.insert(target.clone(), specification.get("value").cloned().unwrap_or(Json::Null));
            continue;
        }
        let source = match specification.get("source") {
            Some(Json::String(source)) if derived.contains_key(source) => source.clone(),
            other => {
                return Err(DerivationError::Rejected {
                    code: "mechanics_derivation_source_missing".into(),
                    message: format!("Missing input '{}' needed to derive '{target}'.", formatted(other)),
                    field_paths: Some(other.and_then(Json::as_str).map(str::to_owned).into_iter().collect()),
                })
            }
        };
        let value = derived[&source].clone();
        match kind {
            Some("copy") => {
                derived.insert(target.clone(), value);
            }
            Some("enum_map") => {
                let mapping = match specification.get("mapping") {
                    Some(mapping @ Json::Object(_))
                        if contains(mapping, &value).map_err(DerivationError::Fault)? =>
                    {
                        mapping
                    }
                    _ => {
                        return Err(rejected(
                            "mechanics_derivation_mapping_missing",
                            format!("Authority could not map '{source}' to '{target}'"),
                        ))
                    }
                };
                let key = value.as_str().expect("only a text key is in a JSON object");
                derived.insert(target.clone(), mapping[key].clone());
            }
            Some("participant_membership") => {
                let (Some(Json::String(participant_name)), Some(Json::String(field))) =
                    (specification.get("participant"), specification.get("field"))
                else {
                    return Err(rejected(
                        "invalid_mechanics_derivation",
                        "Package participant derivation is invalid",
                    ));
                };
                let participant_id = formatted(derived.get(participant_name));
                let Some(participant) = participant_by_id.get(participant_id.as_str()) else {
                    return Err(rejected(
                        "mechanics_participant_unavailable",
                        "The selected participant is unavailable",
                    ));
                };
                let is_member = match participant.data().get(field) {
                    Some(members @ Json::Array(_)) => {
                        contains(members, &value).map_err(DerivationError::Fault)?
                    }
                    _ => false,
                };
                let choice = if is_member { "present" } else { "absent" };
                derived.insert(target.clone(), specification.get(choice).cloned().unwrap_or(Json::Null));
            }
            _ => {
                return Err(rejected(
                    "invalid_mechanics_derivation",
                    "Package mechanics derivation is unsupported",
                ))
            }
        }
    }
    Ok(derived)
}

/// The key two spell names are matched by: casefolded, apostrophes dropped, each run of
/// whitespace, `_` and `-` one `_`, and `_` stripped from both ends.
fn spell_key(value: &str) -> String {
    let folded: String = pystr::casefold(value).chars().filter(|c| !matches!(c, '\'' | '\u{2019}')).collect();
    let mut key = String::new();
    let mut in_run = false;
    for character in folded.chars() {
        if pystr::is_space(character) || character == '_' || character == '-' {
            if !in_run {
                key.push('_');
            }
            in_run = true;
        } else {
            key.push(character);
            in_run = false;
        }
    }
    key.trim_matches('_').to_owned()
}

/// `_normalize_state_references(parameters, schema, state)`: fill exact entity and
/// participant siblings, and normalize a declared spell identifier.
pub fn normalize_state_references(
    parameters: &JsonMap,
    schema: &JsonMap,
    state: &JsonMap,
) -> Result<JsonMap, CodedError> {
    let mut normalized = parameters.clone();
    let Some(Json::Object(properties)) = schema.get("properties") else {
        return Ok(normalized);
    };
    let entity_ids: BTreeSet<&str> = state
        .iter()
        .filter(|(key, _)| key.ends_with(".entities"))
        .filter_map(|(_, entities)| entities.as_object())
        .flat_map(|entities| entities.keys().map(String::as_str))
        .collect();
    let participant_reference = |schema: Option<&Json>| {
        schema.and_then(|schema| schema.get("x-sylvia-gm-reference")).and_then(Json::as_str)
            == Some("participant")
    };
    if let (false, Some(caster_schema @ Json::Object(_)), Some(Json::String(caster_state))) =
        (normalized.contains_key("caster"), properties.get("caster"), normalized.get("caster_state").cloned())
    {
        // Python's `and` reads the enum only once the earlier conditions hold.
        if participant_reference(Some(caster_schema))
            && entity_ids.contains(caster_state.as_str())
            && match caster_schema.get("enum") {
                Some(choices) => contains(choices, &Json::String(caster_state.clone()))?,
                None => false,
            }
        {
            normalized.insert("caster".into(), Json::String(caster_state));
        }
    }
    if let (Some(Json::String(spell)), Some(Json::Object(spell_schema))) =
        (normalized.get("spell").cloned(), properties.get("spell"))
    {
        let empty = Json::Array(Vec::new());
        let choices = spell_schema.get("enum").unwrap_or(&empty);
        // `{choice: choice for choice in choices if isinstance(choice, str)}`: a list's items, a
        // dict's keys, a string's characters.
        let iterated: Vec<String> = match choices {
            Json::Array(items) => items.iter().filter_map(Json::as_str).map(str::to_owned).collect(),
            Json::Object(map) => map.keys().cloned().collect(),
            Json::String(text) => text.chars().map(String::from).collect(),
            other => {
                return Err(CodedError::new(
                    "internal_error",
                    format!("'{}' object is not iterable", type_name(other)),
                )
                .with_error_type("TypeError"))
            }
        };
        let mut candidates: IndexMap<String, String> = IndexMap::new();
        for choice in iterated {
            candidates.insert(choice.clone(), choice);
        }
        if let Some(Json::Object(aliases)) = spell_schema.get("x-sylvia-gm-value-aliases") {
            for (alias, value) in aliases {
                if let Json::String(value_text) = value {
                    if contains(choices, value)? {
                        candidates.insert(alias.clone(), value_text.clone());
                    }
                }
            }
        }
        let wanted = spell_key(&spell);
        let matches: BTreeSet<&String> = candidates
            .iter()
            .filter(|(choice, _)| spell_key(choice) == wanted)
            .map(|(_, value)| value)
            .collect();
        if matches.len() == 1 {
            let only = (*matches.iter().next().expect("one match")).clone();
            normalized.insert("spell".into(), Json::String(only));
        }
    }
    for (name, property_schema) in properties {
        if normalized.contains_key(name) || !name.ends_with("_state") {
            continue;
        }
        if property_schema.get("type").and_then(Json::as_str) != Some("string") {
            continue;
        }
        let source_name = &name[..name.len() - "_state".len()];
        if !participant_reference(properties.get(source_name)) {
            continue;
        }
        if let Some(Json::String(source)) = normalized.get(source_name) {
            if entity_ids.contains(source.as_str()) {
                let source = source.clone();
                normalized.insert(name.clone(), Json::String(source));
            }
        }
    }
    Ok(normalized)
}

/// `_missing_required_arguments(parameters, schema)`: each required input the call lacks.
pub fn missing_required_arguments(parameters: &JsonMap, schema: &JsonMap) -> Vec<String> {
    match schema.get("required") {
        Some(Json::Array(required)) => {
            required.iter().map(py_str).filter(|name| !parameters.contains_key(name)).collect()
        }
        _ => Vec::new(),
    }
}
