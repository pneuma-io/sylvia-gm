//! `domain/ruleset_tests.py`: validate saved scenario data before any disposable campaign is
//! created.
//!
//! Python's `json.loads` reads `NaN`, `Infinity`, `-Infinity` and float literals beyond the
//! float range (such as `1e999`) as non-finite floats, which the size walk then refuses with
//! "Scenario values must be finite JSON data.". A Rust JSON value cannot hold them (differences
//! row 2), so the reader first stands each one in for a text sentinel that no string in the
//! file equals, and the walk treats that sentinel as Python treats a non-finite float. The walk
//! runs in Python's order, so the first refusal is Python's too.
//!
//! Nesting of 128 levels or more is refused when the JSON is read (differences row 4), as
//! "Scenario file must contain unambiguous UTF-8 JSON.", where Python reports "Scenario data
//! exceeds the nesting or size limit." up to its own recursion limit.

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
    walk(value, None)?;
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

/// The size walk at the top of `validate_ruleset_tests`, in Python's last-in, first-out order.
/// JSON keys are always text; `non_finite` is the sentinel standing for a non-finite float.
fn walk(value: &Json, non_finite: Option<&str>) -> Result<(), ContentShapeError> {
    let mut pending: Vec<(&Json, usize)> = vec![(value, 0)];
    let mut nodes = 0usize;
    while let Some((item, depth)) = pending.pop() {
        nodes += 1;
        if depth > 64 || nodes > 250_000 {
            return Err(invalid("$", "Scenario data exceeds the nesting or size limit."));
        }
        match item {
            Json::Object(map) => pending.extend(map.values().map(|child| (child, depth + 1))),
            Json::Array(items) => pending.extend(items.iter().map(|child| (child, depth + 1))),
            Json::String(text) if Some(text.as_str()) == non_finite => {
                return Err(invalid("$", "Scenario values must be finite JSON data."));
            }
            _ => {}
        }
    }
    Ok(())
}

/// `-?(0|[1-9][0-9]*)(\.[0-9]+)?([eE][-+]?[0-9]+)?`, Python's JSON number, with a fraction or an
/// exponent (an integer token is an exact `int` in Python).
fn json_float_token(token: &str) -> bool {
    let bytes = token.strip_prefix('-').unwrap_or(token).as_bytes();
    let digits = |at: usize| bytes[at..].iter().take_while(|b| b.is_ascii_digit()).count();
    let whole = digits(0);
    if whole == 0 || (bytes[0] == b'0' && whole > 1) {
        return false;
    }
    let mut at = whole;
    let mut float = false;
    if bytes.get(at) == Some(&b'.') {
        let fraction = digits(at + 1);
        if fraction == 0 {
            return false;
        }
        at += 1 + fraction;
        float = true;
    }
    if matches!(bytes.get(at), Some(b'e' | b'E')) {
        at += 1;
        if matches!(bytes.get(at), Some(b'+' | b'-')) {
            at += 1;
        }
        let exponent = digits(at);
        if exponent == 0 {
            return false;
        }
        at += exponent;
        float = true;
    }
    float && at == bytes.len()
}

/// `text` with every non-finite literal outside strings replaced by `replacement`, and how many
/// were replaced: `NaN`, `Infinity`, `-Infinity`, and float literals Python reads as infinite.
fn replace_non_finite(text: &str, replacement: &str) -> (String, usize) {
    let bytes = text.as_bytes();
    let mut out = String::with_capacity(text.len());
    let (mut at, mut start, mut count) = (0usize, 0usize, 0usize);
    let mut in_string = false;
    while at < bytes.len() {
        let byte = bytes[at];
        if in_string {
            match byte {
                b'\\' => at += 2,
                b'"' => {
                    in_string = false;
                    at += 1;
                }
                _ => at += 1,
            }
            continue;
        }
        if byte == b'"' {
            in_string = true;
            at += 1;
            continue;
        }
        // Bytes, not `str`: outside a string a stray non-ASCII character is only ever skipped.
        let rest = &bytes[at..];
        let literal = ["-Infinity", "Infinity", "NaN"]
            .into_iter()
            .find(|word| rest.starts_with(word.as_bytes()))
            .map(str::len);
        let number = if literal.is_none() && (byte == b'-' || byte.is_ascii_digit()) {
            let length = rest
                .iter()
                .take_while(|b| b.is_ascii_digit() || matches!(b, b'-' | b'+' | b'.' | b'e' | b'E'))
                .count();
            let token = std::str::from_utf8(&rest[..length]).expect("ASCII");
            if json_float_token(token) && token.parse::<f64>().is_ok_and(f64::is_infinite) {
                Ok(length)
            } else {
                Err(length)
            }
        } else {
            Err(1)
        };
        match (literal, number) {
            (Some(length), _) | (None, Ok(length)) => {
                out.push_str(&text[start..at]);
                out.push_str(replacement);
                at += length;
                start = at;
                count += 1;
            }
            (None, Err(length)) => at += length.max(1),
        }
    }
    out.push_str(&text[start..]);
    (out, count)
}

fn collect_strings<'a>(value: &'a Json, strings: &mut std::collections::BTreeSet<&'a str>) {
    match value {
        Json::String(text) => {
            strings.insert(text);
        }
        Json::Array(items) => items.iter().for_each(|item| collect_strings(item, strings)),
        Json::Object(map) => {
            for (key, item) in map {
                strings.insert(key);
                collect_strings(item, strings);
            }
        }
        _ => {}
    }
}

/// `parse_ruleset_tests(data)`: bounded UTF-8 scenario JSON with unique member names.
pub fn parse_ruleset_tests(data: &[u8]) -> DomainResult<JsonMap> {
    if data.len() > RULESET_TESTS_BYTE_LIMIT {
        return Err(invalid("$", "Scenario file exceeds the size limit.").into());
    }
    let ambiguous = || invalid("$", "Scenario file must contain unambiguous UTF-8 JSON.");
    let read = |bytes: &[u8]| parse_content_json_bytes(bytes, "Scenario file", JsonShapeLimits::NONE);
    let text = std::str::from_utf8(data).map_err(|_| ambiguous())?;
    let (with_nulls, count) = replace_non_finite(text, "null");
    if count == 0 {
        let value = read(data).map_err(|_| ambiguous())?;
        return validate_ruleset_tests(&value);
    }
    // Python reads the file whole; any other fault makes it ambiguous before the walk runs.
    let plain = read(with_nulls.as_bytes()).map_err(|_| ambiguous())?;
    let mut strings = std::collections::BTreeSet::new();
    collect_strings(&plain, &mut strings);
    let sentinel = (0..)
        .map(|k| format!("\u{0}non-finite-{k}"))
        .find(|s| !strings.contains(s.as_str()))
        .expect("a free name");
    let literal =
        sylvia_base::pyjson::dumps(&Json::String(sentinel.clone()), sylvia_base::pyjson::Dump::DEFAULT);
    let (marked, _) = replace_non_finite(text, &literal);
    let value = read(marked.as_bytes()).map_err(|_| ambiguous())?;
    walk(&value, Some(&sentinel))?;
    // Every node is visited, so the walk has already refused the first non-finite value.
    unreachable!("a non-finite value is always reached by the walk")
}
