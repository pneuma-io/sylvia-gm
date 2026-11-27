//! Test helpers every crate's tests share.
//!
//! [`assert_same_json`] compares JSON values the way Python's output is compared: the same
//! values, and the same key order in every object. `serde_json`'s `==` cannot do that: with the
//! `preserve_order` feature a map is an `IndexMap`, and `IndexMap`'s `==` ignores order. A vector
//! test comparing `Value`s with `assert_eq!` therefore passes when the Rust side writes a
//! result's keys in another order, although a model or a client reads that order (`json.dumps`
//! writes a dict in insertion order).
//!
//! The comparison is on the serialized text, so it also tells an integer from a float of the
//! same value (`1` and `1.0`), as Python's text does.

use crate::Json;
use std::fmt::Display;

/// How much of a value a failure message shows.
const SHOWN: usize = 300;

/// Panic with the first difference unless `found` and `expected` serialize to the same text:
/// the same values, with every object's keys in the same order.
#[track_caller]
pub fn assert_same_json(found: &Json, expected: &Json, label: impl Display) {
    if let Some(difference) = json_difference(found, expected) {
        panic!("{label}: {difference}");
    }
}

/// The first difference between two JSON values as their serialized texts see it, with its
/// path (`$`, `.key`, `[index]`), or `None` when the texts are the same.
pub fn json_difference(found: &Json, expected: &Json) -> Option<String> {
    let difference = first_difference(found, expected, "$");
    if difference.is_none() && text(found) != text(expected) {
        // Every value matched, so the texts cannot differ; kept so the helper never hides one.
        return Some(format!(
            "the serialized texts differ: found {}, expected {}",
            shown(found),
            shown(expected)
        ));
    }
    difference
}

fn text(value: &Json) -> String {
    serde_json::to_string(value).unwrap_or_default()
}

fn shown(value: &Json) -> String {
    let text = text(value);
    match text.char_indices().nth(SHOWN) {
        Some((end, _)) => format!("{}...", &text[..end]),
        None => text,
    }
}

fn keys(map: &serde_json::Map<String, Json>) -> Vec<&str> {
    map.keys().map(String::as_str).collect()
}

fn first_difference(found: &Json, expected: &Json, path: &str) -> Option<String> {
    match (found, expected) {
        (Json::Object(found), Json::Object(expected)) => {
            let (found_keys, expected_keys) = (keys(found), keys(expected));
            if found_keys != expected_keys {
                let mut sorted_found = found_keys.clone();
                let mut sorted_expected = expected_keys.clone();
                sorted_found.sort_unstable();
                sorted_expected.sort_unstable();
                let what = if sorted_found == sorted_expected { "key order differs" } else { "keys differ" };
                return Some(format!("at {path}: {what}: found {found_keys:?}, expected {expected_keys:?}"));
            }
            expected
                .iter()
                .find_map(|(key, value)| first_difference(&found[key], value, &format!("{path}.{key}")))
        }
        (Json::Array(found), Json::Array(expected)) => {
            if found.len() != expected.len() {
                return Some(format!(
                    "at {path}: lengths differ: found {}, expected {}",
                    found.len(),
                    expected.len()
                ));
            }
            found.iter().zip(expected).enumerate().find_map(|(index, (found, expected))| {
                first_difference(found, expected, &format!("{path}[{index}]"))
            })
        }
        _ if text(found) == text(expected) => None,
        _ => Some(format!("at {path}: found {}, expected {}", shown(found), shown(expected))),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn parsed(text: &str) -> Json {
        serde_json::from_str(text).unwrap()
    }

    #[test]
    fn the_same_value_passes() {
        let value = parsed(r#"{"a": [1, {"b": null, "c": "x"}], "d": 1.5}"#);
        assert_same_json(&value, &value.clone(), "same");
    }

    #[test]
    fn a_swapped_key_fails_where_assert_eq_passes() {
        let found = parsed(r#"{"outer": {"status": "passed", "message": "m"}}"#);
        let expected = parsed(r#"{"outer": {"message": "m", "status": "passed"}}"#);
        // `serde_json`'s `==` is blind to the order.
        assert_eq!(found, expected);
        assert_eq!(
            json_difference(&found, &expected).as_deref(),
            Some(
                r#"at $.outer: key order differs: found ["status", "message"], expected ["message", "status"]"#
            )
        );
        let raised = std::panic::catch_unwind(|| assert_same_json(&found, &expected, "swapped"));
        assert!(raised.is_err());
    }

    #[test]
    fn the_first_difference_has_its_path() {
        let found = parsed(r#"{"a": [1, {"b": 2}], "c": 3}"#);
        let expected = parsed(r#"{"a": [1, {"b": 4}], "c": 3}"#);
        assert_eq!(json_difference(&found, &expected).as_deref(), Some("at $.a[1].b: found 2, expected 4"));
    }

    #[test]
    fn missing_keys_lengths_and_number_kinds_differ() {
        let difference = json_difference(&parsed(r#"{"a": 1}"#), &parsed(r#"{"a": 1, "b": 2}"#));
        assert_eq!(difference.as_deref(), Some(r#"at $: keys differ: found ["a"], expected ["a", "b"]"#));
        let difference = json_difference(&parsed("[1, 2]"), &parsed("[1]"));
        assert_eq!(difference.as_deref(), Some("at $: lengths differ: found 2, expected 1"));
        // Python writes `1` and `1.0` differently.
        assert_eq!(
            json_difference(&parsed("1"), &parsed("1.0")).as_deref(),
            Some("at $: found 1, expected 1.0")
        );
    }
}
