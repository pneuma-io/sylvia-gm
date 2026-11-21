//! Compare two recordings after removing what is allowed to differ between runs.
//!
//! Three kinds of allowance, all declared in data (the built-in defaults plus a session's own
//! `rules`), never decided in code:
//! - `ignore_keys`: object keys whose values are dropped wherever they appear (durations);
//! - `scrub`: regular expressions replaced by a fixed word (timestamps);
//! - `alias`: regular expressions for run-specific identifiers. Each distinct match becomes
//!   `<name:1>`, `<name:2>` in order of first appearance, so two runs still have to agree on
//!   which identifier appears where, just not on its spelling. An alias can be limited to the
//!   values of named keys, so it cannot hide a wrong hash somewhere else;
//! - `volatile_files`: data-folder files whose size and content vary (logs).
//!
//! Anything not covered by a rule must match exactly.

use regex::Regex;
use serde::Deserialize;
use std::collections::HashMap;
use sylvia_base::Json;

/// What may differ between two runs of the same session.
#[derive(Debug, Clone, Default, Deserialize)]
#[serde(default, deny_unknown_fields)]
pub struct Rules {
    /// Keys dropped wherever they appear.
    pub ignore_keys: Vec<String>,
    /// JSON pointers dropped (exact pointers into the recording).
    pub ignore_pointers: Vec<String>,
    /// Text replaced by a fixed word.
    pub scrub: Vec<Replace>,
    /// Run-specific identifiers numbered by first appearance.
    pub alias: Vec<Alias>,
    /// Regular expressions for data-folder paths whose size and content are not compared.
    pub volatile_files: Vec<String>,
}

/// One `scrub` rule.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Replace {
    /// Regular expression.
    pub pattern: String,
    /// Replacement text.
    pub with: String,
}

/// One `alias` rule.
#[derive(Debug, Clone, Deserialize)]
#[serde(deny_unknown_fields)]
pub struct Alias {
    /// Name used in the placeholder.
    pub name: String,
    /// Regular expression matching one identifier.
    pub pattern: String,
    /// Apply only to values stored under these keys (and to items of arrays under them).
    /// Empty means everywhere, including object keys.
    #[serde(default)]
    pub keys: Vec<String>,
}

impl Rules {
    /// Allowances that hold for every session: wall-clock times and measured durations.
    pub fn defaults() -> Self {
        Self {
            // Measured durations and the epoch stamps of the diagnostics log.
            ignore_keys: vec![
                "duration_ms".into(),
                "elapsed_seconds".into(),
                "first_seen".into(),
                "last_seen".into(),
            ],
            ignore_pointers: Vec::new(),
            scrub: vec![Replace {
                // ISO 8601 date-times, with or without fraction and zone.
                pattern: r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?".into(),
                with: "<time>".into(),
            }],
            // `uuid4().hex` identities of diagnostics records and of one owner run.
            alias: vec![Alias {
                name: "diagnostic".into(),
                pattern: "^[0-9a-f]{32}$".into(),
                keys: vec!["diagnostic_id".into(), "id".into(), "session".into()],
            }],
            volatile_files: Vec::new(),
        }
    }

    /// These rules followed by `other`'s.
    #[must_use]
    pub fn merged(mut self, other: Rules) -> Self {
        self.ignore_keys.extend(other.ignore_keys);
        self.ignore_pointers.extend(other.ignore_pointers);
        self.scrub.extend(other.scrub);
        self.alias.extend(other.alias);
        self.volatile_files.extend(other.volatile_files);
        self
    }
}

struct Compiled {
    ignore_keys: Vec<String>,
    ignore_pointers: Vec<String>,
    scrub: Vec<(Regex, String)>,
    alias: Vec<(String, Regex, Vec<String>)>,
    volatile_files: Vec<Regex>,
}

fn compile(rules: &Rules) -> Result<Compiled, String> {
    let regex =
        |pattern: &str| Regex::new(pattern).map_err(|error| format!("bad rule pattern {pattern}: {error}"));
    Ok(Compiled {
        ignore_keys: rules.ignore_keys.clone(),
        ignore_pointers: rules.ignore_pointers.clone(),
        scrub: rules
            .scrub
            .iter()
            .map(|rule| Ok((regex(&rule.pattern)?, rule.with.clone())))
            .collect::<Result<_, String>>()?,
        alias: rules
            .alias
            .iter()
            .map(|rule| Ok((rule.name.clone(), regex(&rule.pattern)?, rule.keys.clone())))
            .collect::<Result<_, String>>()?,
        volatile_files: rules
            .volatile_files
            .iter()
            .map(|pattern| regex(pattern))
            .collect::<Result<_, String>>()?,
    })
}

/// Apply only the `scrub` rules of `rules`. Done before a recording is written, so text a
/// session declares as run-specific (a protected credential) never reaches the repository.
pub fn scrub(recording: &Json, rules: &Rules) -> Result<Json, String> {
    normalise(recording, &Rules { scrub: rules.scrub.clone(), ..Rules::default() })
}

/// Apply `rules` to a recording. Aliases are numbered in document order.
pub fn normalise(recording: &Json, rules: &Rules) -> Result<Json, String> {
    let compiled = compile(rules)?;
    let mut seen: HashMap<(usize, String), usize> = HashMap::new();
    let mut counts = vec![0usize; compiled.alias.len()];
    let mut recording = recording.clone();
    // A volatile file keeps its path (it must still exist) and loses everything else.
    if let Some(files) = recording.get_mut("files").and_then(Json::as_array_mut) {
        for file in files {
            let volatile = file
                .get("path")
                .and_then(Json::as_str)
                .is_some_and(|path| compiled.volatile_files.iter().any(|pattern| pattern.is_match(path)));
            if let (true, Some(entry)) = (volatile, file.as_object_mut()) {
                entry.retain(|key, _| key == "path");
                entry.insert("volatile".into(), Json::Bool(true));
            }
        }
    }
    Ok(walk(&recording, "", None, &compiled, &mut seen, &mut counts))
}

fn walk(
    value: &Json,
    pointer: &str,
    key: Option<&str>,
    rules: &Compiled,
    seen: &mut HashMap<(usize, String), usize>,
    counts: &mut [usize],
) -> Json {
    match value {
        Json::String(text) => Json::String(rewrite(text, key, rules, seen, counts)),
        Json::Array(items) => Json::Array(
            items
                .iter()
                .enumerate()
                .map(|(index, item)| walk(item, &format!("{pointer}/{index}"), key, rules, seen, counts))
                .collect(),
        ),
        Json::Object(map) => {
            let mut out = sylvia_base::JsonMap::new();
            for (key, item) in map {
                let child = format!("{pointer}/{}", key.replace('~', "~0").replace('/', "~1"));
                if rules.ignore_keys.contains(key) || rules.ignore_pointers.contains(&child) {
                    out.insert(key.clone(), Json::String("<ignored>".into()));
                    continue;
                }
                // Identifiers are also used as object keys.
                let value = walk(item, &child, Some(key), rules, seen, counts);
                out.insert(rewrite(key, None, rules, seen, counts), value);
            }
            Json::Object(out)
        }
        other => other.clone(),
    }
}

fn rewrite(
    text: &str,
    key: Option<&str>,
    rules: &Compiled,
    seen: &mut HashMap<(usize, String), usize>,
    counts: &mut [usize],
) -> String {
    let mut text = text.to_owned();
    for (index, (name, pattern, keys)) in rules.alias.iter().enumerate() {
        let applies = keys.is_empty() || key.is_some_and(|key| keys.iter().any(|allowed| allowed == key));
        if !applies || !pattern.is_match(&text) {
            continue;
        }
        text = pattern
            .replace_all(&text, |found: &regex::Captures<'_>| {
                let number = *seen.entry((index, found[0].to_owned())).or_insert_with(|| {
                    counts[index] += 1;
                    counts[index]
                });
                format!("<{name}:{number}>")
            })
            .into_owned();
    }
    for (pattern, with) in &rules.scrub {
        if pattern.is_match(&text) {
            text = pattern.replace_all(&text, with.as_str()).into_owned();
        }
    }
    text
}

/// One place where two normalised recordings differ.
#[derive(Debug, Clone, PartialEq)]
pub struct Difference {
    /// JSON pointer into the recording.
    pub pointer: String,
    /// What the reference has there.
    pub reference: String,
    /// What the candidate has there.
    pub candidate: String,
}

/// List the differences between two normalised recordings, at most `limit`.
pub fn compare(reference: &Json, candidate: &Json, limit: usize) -> Vec<Difference> {
    let mut found = Vec::new();
    visit(reference, candidate, "", &mut found, limit);
    found
}

fn brief(value: &Json) -> String {
    let text = value.to_string();
    if text.chars().count() > 200 {
        let head: String = text.chars().take(200).collect();
        format!("{head}... ({} bytes)", text.len())
    } else {
        text
    }
}

fn visit(reference: &Json, candidate: &Json, pointer: &str, found: &mut Vec<Difference>, limit: usize) {
    if found.len() >= limit {
        return;
    }
    let mut note = |reference: String, candidate: String| {
        found.push(Difference {
            pointer: if pointer.is_empty() { "/".into() } else { pointer.to_owned() },
            reference,
            candidate,
        });
    };
    match (reference, candidate) {
        (Json::Object(left), Json::Object(right)) => {
            // Key order is behaviour (Python dicts keep it and it reaches hashed bytes).
            let left_keys: Vec<&String> = left.keys().collect();
            let right_keys: Vec<&String> = right.keys().collect();
            if left_keys != right_keys {
                let mut sorted_left = left_keys.clone();
                let mut sorted_right = right_keys.clone();
                sorted_left.sort();
                sorted_right.sort();
                if sorted_left == sorted_right {
                    note(format!("key order {left_keys:?}"), format!("key order {right_keys:?}"));
                } else {
                    let missing: Vec<_> = left_keys.iter().filter(|key| !right.contains_key(**key)).collect();
                    let extra: Vec<_> = right_keys.iter().filter(|key| !left.contains_key(**key)).collect();
                    note(format!("has keys {missing:?}"), format!("has keys {extra:?}"));
                }
            }
            for (key, left_value) in left {
                if let Some(right_value) = right.get(key) {
                    let child = format!("{pointer}/{}", key.replace('~', "~0").replace('/', "~1"));
                    visit(left_value, right_value, &child, found, limit);
                }
            }
        }
        (Json::Array(left), Json::Array(right)) => {
            if left.len() != right.len() {
                note(format!("{} items", left.len()), format!("{} items", right.len()));
            }
            for (index, (left_value, right_value)) in left.iter().zip(right).enumerate() {
                visit(left_value, right_value, &format!("{pointer}/{index}"), found, limit);
            }
        }
        // Integers and floats are distinct in Python and in the encodings Sylvia hashes.
        (Json::Number(left), Json::Number(right)) => {
            if left.to_string() != right.to_string() {
                note(left.to_string(), right.to_string());
            }
        }
        (left, right) => {
            if left != right {
                note(brief(left), brief(right));
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use serde_json::json;

    #[test]
    fn aliases_number_identifiers_by_first_appearance() {
        let rules = Rules {
            alias: vec![Alias {
                name: "campaign".into(),
                pattern: "campaign-[0-9a-f]{8}".into(),
                keys: Vec::new(),
            }],
            ..Rules::default()
        };
        let first = json!({"a": "campaign-aaaaaaaa", "b": ["campaign-bbbbbbbb", "campaign-aaaaaaaa"]});
        let second = json!({"a": "campaign-11111111", "b": ["campaign-22222222", "campaign-11111111"]});
        let swapped = json!({"a": "campaign-11111111", "b": ["campaign-22222222", "campaign-22222222"]});
        let normal = |value| normalise(value, &rules).unwrap();
        assert!(compare(&normal(&first), &normal(&second), 10).is_empty());
        assert_eq!(compare(&normal(&first), &normal(&swapped), 10).len(), 1);
    }

    #[test]
    fn times_and_durations_are_allowed_but_values_are_not() {
        let rules = Rules::defaults();
        let first = json!({"at": "2026-11-21T03:04:05.123456+00:00", "duration_ms": 12.5, "hp": 7});
        let second = json!({"at": "2026-11-22T09:00:00Z", "duration_ms": 80.1, "hp": 7});
        let third = json!({"at": "2026-11-22T09:00:00Z", "duration_ms": 80.1, "hp": 7.0});
        let normal = |value| normalise(value, &rules).unwrap();
        assert!(compare(&normal(&first), &normal(&second), 10).is_empty());
        let differences = compare(&normal(&first), &normal(&third), 10);
        assert_eq!(differences.len(), 1);
        assert_eq!(differences[0].pointer, "/hp");
    }

    #[test]
    fn keyed_aliases_leave_other_values_alone() {
        let rules = Rules::defaults();
        let hex = "0123456789abcdef0123456789abcdef";
        let other = "fedcba9876543210fedcba9876543210";
        let normal = |value: Json| normalise(&value, &rules).unwrap();
        // Under `diagnostic_id` the spelling is free; under `content_hash` it is not.
        assert!(compare(
            &normal(json!({"diagnostic_id": hex})),
            &normal(json!({"diagnostic_id": other})),
            10
        )
        .is_empty());
        assert_eq!(
            compare(&normal(json!({"content_hash": hex})), &normal(json!({"content_hash": other})), 10).len(),
            1
        );
    }

    #[test]
    fn volatile_files_must_exist_but_may_differ() {
        let rules = Rules { volatile_files: vec!["^diagnostics/".into()], ..Rules::default() };
        let first = json!({"files": [{"path": "diagnostics/runtime.jsonl", "bytes": 10}, {"path": "a.md", "bytes": 3}]});
        let second = json!({"files": [{"path": "diagnostics/runtime.jsonl", "bytes": 99}, {"path": "a.md", "bytes": 3}]});
        let third = json!({"files": [{"path": "a.md", "bytes": 3}]});
        let normal = |value| normalise(value, &rules).unwrap();
        assert!(compare(&normal(&first), &normal(&second), 10).is_empty());
        assert!(!compare(&normal(&first), &normal(&third), 10).is_empty());
    }

    #[test]
    fn key_order_is_a_difference() {
        let differences = compare(&json!({"a": 1, "b": 2}), &json!({"b": 2, "a": 1}), 10);
        assert_eq!(differences.len(), 1);
        assert!(differences[0].reference.starts_with("key order"));
    }
}
