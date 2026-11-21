//! `domain/ruleset_tests.py`: validate saved scenario data before any disposable campaign is
//! created.
//!
//! Two inputs Python accepts are refused when the JSON is read, under
//! `docs/rust-port/differences.md` rows 2 and 4: the `NaN`/`Infinity` literals (Python reads
//! them and then reports "Scenario values must be finite JSON data.") and nesting of 128
//! levels or more (Python reports "Scenario data exceeds the nesting or size limit." up to its
//! own recursion limit). Both are reported here as "Scenario file must contain unambiguous
//! UTF-8 JSON.", with the same code, file and path.

use crate::content::{validate_content_shape, ContentShapeError};
use crate::error::{DomainResult, RulesetDiagnostic};
use crate::package_library::{parse_content_json_bytes, JsonShapeLimits};
use crate::pyops::py_number_cmp;
use std::cmp::Ordering;
use sylvia_base::{Json, JsonMap};

/// `RULESET_TESTS_PATH`.
pub const RULESET_TESTS_PATH: &str = "tests/scenarios.json";
/// The scenario file byte limit.
pub const RULESET_TESTS_BYTE_LIMIT: usize = 2 * 1024 * 1024;

/// `_invalid(path, message)`: an authored field, without echoing submitted content.
fn invalid(path: impl Into<String>, message: &str) -> ContentShapeError {
    ContentShapeError::new(vec![RulesetDiagnostic {
        code: "ruleset_tests_invalid".into(),
        phase: "source".into(),
        message: message.into(),
        file: Some(RULESET_TESTS_PATH.into()),
        path: Some(path.into()),
        ..Default::default()
    }])
}

/// `validate_ruleset_tests(value)`: shape and cross-field identities; execution owns real game
/// validation.
pub fn validate_ruleset_tests(value: &Json) -> DomainResult<JsonMap> {
    // A JSON tree always has text keys and finite numbers, so the walk only bounds its size.
    let mut pending: Vec<(&Json, usize)> = vec![(value, 0)];
    let mut nodes = 0usize;
    while let Some((item, depth)) = pending.pop() {
        nodes += 1;
        if depth > 64 || nodes > 250_000 {
            return Err(invalid("$", "Scenario data exceeds the nesting or size limit.").into());
        }
        match item {
            Json::Object(map) => pending.extend(map.values().map(|child| (child, depth + 1))),
            Json::Array(items) => pending.extend(items.iter().map(|child| (child, depth + 1))),
            _ => {}
        }
    }
    validate_content_shape("ruleset_tests", value, Some(RULESET_TESTS_PATH))?;
    // The schema has fixed every type read below.
    let suite = value.as_object().expect("the schema requires an object");
    let mut seen: Vec<&str> = Vec::new();
    for (index, scenario) in suite["scenarios"].as_array().into_iter().flatten().enumerate() {
        let path = format!("$.scenarios[{index}]");
        let id = scenario["id"].as_str().unwrap_or_default();
        if seen.contains(&id) {
            return Err(invalid(format!("{path}.id"), "Scenario identities must be unique.").into());
        }
        seen.push(id);
        let mut states: Vec<(Option<&str>, &str)> = Vec::new();
        for (offset, state) in scenario["initial_state"].as_array().into_iter().flatten().enumerate() {
            let identity =
                (state.get("namespace").and_then(Json::as_str), state["key"].as_str().unwrap_or_default());
            if states.contains(&identity) {
                return Err(invalid(
                    format!("{path}.initial_state[{offset}]"),
                    "State identities must be unique.",
                )
                .into());
            }
            states.push(identity);
        }
        let mut participants: Vec<&str> = Vec::new();
        for (offset, participant) in scenario["participants"].as_array().into_iter().flatten().enumerate() {
            let id = participant["id"].as_str().unwrap_or_default();
            if participants.contains(&id) {
                return Err(invalid(
                    format!("{path}.participants[{offset}].id"),
                    "Participant identities must be unique.",
                )
                .into());
            }
            participants.push(id);
        }
        for (offset, roll) in scenario["rolls"].as_array().into_iter().flatten().enumerate() {
            if py_number_cmp(&roll["value"], &roll["sides"]) == Some(Ordering::Greater) {
                return Err(
                    invalid(format!("{path}.rolls[{offset}].value"), "Roll exceeds its die size.").into()
                );
            }
        }
    }
    Ok(suite.clone())
}

/// `parse_ruleset_tests(data)`: bounded UTF-8 scenario JSON with unique member names.
pub fn parse_ruleset_tests(data: &[u8]) -> DomainResult<JsonMap> {
    if data.len() > RULESET_TESTS_BYTE_LIMIT {
        return Err(invalid("$", "Scenario file exceeds the size limit.").into());
    }
    let value = parse_content_json_bytes(data, "Scenario file", JsonShapeLimits::NONE)
        .map_err(|_| invalid("$", "Scenario file must contain unambiguous UTF-8 JSON."))?;
    validate_ruleset_tests(&value)
}
