//! Compare two recordings after removing what is allowed to differ between runs.
//!
//! Three kinds of allowance, all declared in data (the built-in defaults plus a session's own
//! `rules`), never decided in code:
//! - `ignore_keys`: object keys whose values are dropped wherever they appear (durations), or
//!   with `parent/key` only where `key` sits directly in an object stored under `parent`;
//! - `scrub`: regular expressions replaced by a fixed word (timestamps);
//! - `alias`: regular expressions for run-specific identifiers. Each distinct match becomes
//!   `<name:1>`, `<name:2>` in order of first appearance, so two runs still have to agree on
//!   which identifier appears where, just not on its spelling. An alias can be limited to the
//!   values of named keys, so it cannot hide a wrong hash somewhere else;
//! - `volatile_files`: data-folder files whose size and content vary (logs);
//! - `ignore_headers_row_8` and `header_name_case_row_40`: the two that cover agreed Rust
//!   differences (`differences.md` rows 8 and 40) in model requests' headers.
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
    /// Keys dropped wherever they appear. `parent/key` drops `key` only directly inside an
    /// object that is itself the value of `parent` (or an item of an array under `parent`).
    pub ignore_keys: Vec<String>,
    /// JSON pointers dropped (exact pointers into the recording).
    pub ignore_pointers: Vec<String>,
    /// Text replaced by a fixed word.
    pub scrub: Vec<Replace>,
    /// Run-specific identifiers numbered by first appearance.
    pub alias: Vec<Alias>,
    /// Regular expressions for data-folder paths whose size and content are not compared.
    pub volatile_files: Vec<String>,
    /// Model-request header names that differences row 8 lets differ (the HTTP client's own
    /// headers): dropped from each `model_requests` entry's `headers` on both sides, name
    /// compared case-insensitively, a trailing `*` a prefix. Only the names row 8 declares
    /// belong here; never a blanket header ignore.
    pub ignore_headers_row_8: Vec<String>,
    /// Differences row 40 (header name case): the Rust client writes request header names in
    /// lowercase, Python writes them as given, and HTTP names are case-insensitive (RFC 9110
    /// §5.1). When set, each `model_requests` entry's header *names* are lowercased on both
    /// sides; values and order are still compared exactly.
    #[serde(rename = "header_name_case_row_40")]
    pub header_name_case_row_next: bool,
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
            scrub: vec![
                Replace {
                    // ISO 8601 date-times, with or without fraction and zone.
                    pattern: r"\d{4}-\d{2}-\d{2}[T ]\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:?\d{2})?".into(),
                    with: "<time>".into(),
                },
                Replace {
                    // The `Host` header of a model request: the stand-in's port is per run.
                    // Anchored, so only a whole value that is exactly this is replaced.
                    pattern: r"^127\.0\.0\.1:[0-9]{1,5}$".into(),
                    with: "<standin_host>".into(),
                },
            ],
            // `uuid4().hex` identities of diagnostics records and of one owner run.
            alias: vec![Alias {
                name: "diagnostic".into(),
                pattern: "^[0-9a-f]{32}$".into(),
                keys: vec!["diagnostic_id".into(), "id".into(), "session".into()],
            }],
            volatile_files: Vec::new(),
            ignore_headers_row_8: Vec::new(),
            header_name_case_row_next: false,
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
        self.ignore_headers_row_8.extend(other.ignore_headers_row_8);
        self.header_name_case_row_next |= other.header_name_case_row_next;
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

/// What `ignore_headers_row_8` dropped from one recording.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct DroppedHeaders {
    /// Headers dropped.
    pub headers: usize,
    /// Model requests that lost at least one.
    pub requests: usize,
}

fn header_matches(names: &[String], header: &str) -> bool {
    names.iter().any(|name| match name.strip_suffix('*') {
        Some(prefix) => header
            .as_bytes()
            .get(..prefix.len())
            .is_some_and(|start| start.eq_ignore_ascii_case(prefix.as_bytes())),
        None => header.eq_ignore_ascii_case(name),
    })
}

/// Check `ignore_headers_row_8`'s names: non-empty, with `*` only as the last character.
fn check_header_names(names: &[String]) -> Result<(), String> {
    for name in names {
        let body = name.strip_suffix('*').unwrap_or(name);
        if body.is_empty() || body.contains('*') {
            return Err(format!(
                "bad ignore_headers_row_8 name {name:?}: a header name, optionally ending in *"
            ));
        }
    }
    Ok(())
}

/// Drop `ignore_headers_row_8`'s headers from each model request, and count them.
pub fn drop_row_8_headers(recording: &mut Json, rules: &Rules) -> Result<DroppedHeaders, String> {
    check_header_names(&rules.ignore_headers_row_8)?;
    let mut dropped = DroppedHeaders::default();
    if rules.ignore_headers_row_8.is_empty() {
        return Ok(dropped);
    }
    let Some(requests) = recording.get_mut("model_requests").and_then(Json::as_array_mut) else {
        return Ok(dropped);
    };
    for request in requests {
        let Some(headers) = request.get_mut("headers").and_then(Json::as_array_mut) else { continue };
        let before = headers.len();
        headers.retain(|header| {
            let name = header.get(0).and_then(Json::as_str).unwrap_or("");
            !header_matches(&rules.ignore_headers_row_8, name)
        });
        if headers.len() < before {
            dropped.headers += before - headers.len();
            dropped.requests += 1;
        }
    }
    Ok(dropped)
}

/// What `header_name_case_row_40` changed in one recording.
#[derive(Debug, Clone, Copy, Default, PartialEq, Eq)]
pub struct FoldedNames {
    /// Header names that were not already lowercase.
    pub headers: usize,
    /// Model requests holding at least one.
    pub requests: usize,
}

/// Lowercase each model request's header names when `header_name_case_row_40` is set, and
/// count the names that changed.
pub fn fold_header_names(recording: &mut Json, rules: &Rules) -> FoldedNames {
    let mut folded = FoldedNames::default();
    if !rules.header_name_case_row_next {
        return folded;
    }
    let Some(requests) = recording.get_mut("model_requests").and_then(Json::as_array_mut) else {
        return folded;
    };
    for request in requests {
        let Some(headers) = request.get_mut("headers").and_then(Json::as_array_mut) else { continue };
        let mut changed = 0;
        for header in headers {
            if let Some(name) = header.get_mut(0) {
                if let Some(text) = name.as_str() {
                    let lower = text.to_ascii_lowercase();
                    if lower != text {
                        changed += 1;
                        *name = Json::String(lower);
                    }
                }
            }
        }
        if changed > 0 {
            folded.headers += changed;
            folded.requests += 1;
        }
    }
    folded
}

/// Apply `rules` to a recording. Aliases are numbered in document order.
pub fn normalise(recording: &Json, rules: &Rules) -> Result<Json, String> {
    let compiled = compile(rules)?;
    let mut seen: HashMap<(usize, String), usize> = HashMap::new();
    let mut counts = vec![0usize; compiled.alias.len()];
    let mut recording = recording.clone();
    drop_row_8_headers(&mut recording, rules)?;
    fold_header_names(&mut recording, rules);
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
                .map(|(index, item)| {
                    let child = format!("{pointer}/{index}");
                    // An exact pointer may name an array item too, such as one header's value.
                    if rules.ignore_pointers.contains(&child) {
                        return Json::String("<ignored>".into());
                    }
                    walk(item, &child, key, rules, seen, counts)
                })
                .collect(),
        ),
        Json::Object(map) => {
            let parent = key;
            let mut out = sylvia_base::JsonMap::new();
            for (key, item) in map {
                let child = format!("{pointer}/{}", key.replace('~', "~0").replace('/', "~1"));
                let scoped = parent.is_some_and(|parent| {
                    rules.ignore_keys.iter().any(|rule| rule.split_once('/') == Some((parent, key.as_str())))
                });
                if rules.ignore_keys.contains(key) || scoped || rules.ignore_pointers.contains(&child) {
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
    fn row_8_headers_are_dropped_by_exact_name_or_prefix_and_counted() {
        let request = |headers: Json| json!({"method": "POST", "headers": headers});
        let mut recording = json!({"model_requests": [
            request(json!([["Host", "h"], ["user-agent", "python-httpx/0.28.1"], ["Accept-Encoding", "gzip"],
                           ["X-Stainless-Lang", "python"], ["x-stainless-retry-count", "0"], ["Content-Type", "application/json"]])),
            request(json!([["Host", "h"], ["User-Agent", "SylviaGM/0.4.1"]])),
            request(json!([["Host", "h"], ["X-Stainless", "no dash: not the prefix"], ["Connectionless", "kept"]])),
        ]});
        let rules = Rules {
            ignore_headers_row_8: vec![
                "User-Agent".into(),
                "Accept-Encoding".into(),
                "Connection".into(),
                "X-Stainless-*".into(),
            ],
            ..Rules::default()
        };
        let dropped = drop_row_8_headers(&mut recording, &rules).unwrap();
        assert_eq!(dropped, DroppedHeaders { headers: 5, requests: 2 });
        assert_eq!(
            recording["model_requests"][0]["headers"],
            json!([["Host", "h"], ["Content-Type", "application/json"]])
        );
        assert_eq!(recording["model_requests"][1]["headers"], json!([["Host", "h"]]));
        // Exact names are whole names, and a prefix ends where its * is.
        assert_eq!(recording["model_requests"][2]["headers"].as_array().unwrap().len(), 3);
        // Every other header is still compared: a changed one still differs.
        let mut other = recording.clone();
        other["model_requests"][0]["headers"][1][1] = json!("text/plain");
        assert_eq!(
            compare(&normalise(&recording, &rules).unwrap(), &normalise(&other, &rules).unwrap(), 10).len(),
            1
        );
    }

    #[test]
    fn header_name_case_folds_names_only() {
        let recording = json!({"model_requests": [
            {"method": "GET", "headers": [["Host", "h"], ["Accept", "*/*"], ["Authorization", null]]},
            {"method": "GET", "headers": [["host", "h"]]},
        ]});
        let lower = json!({"model_requests": [
            {"method": "GET", "headers": [["host", "h"], ["accept", "*/*"], ["authorization", null]]},
            {"method": "GET", "headers": [["host", "h"]]},
        ]});
        let off = Rules::default();
        assert_eq!(
            compare(&normalise(&recording, &off).unwrap(), &normalise(&lower, &off).unwrap(), 10).len(),
            3
        );
        let on = Rules { header_name_case_row_next: true, ..Rules::default() };
        assert_eq!(fold_header_names(&mut recording.clone(), &on), FoldedNames { headers: 3, requests: 1 });
        assert!(
            compare(&normalise(&recording, &on).unwrap(), &normalise(&lower, &on).unwrap(), 10).is_empty()
        );
        // Values and order still count.
        let mut value = lower.clone();
        value["model_requests"][0]["headers"][1][1] = json!("application/json");
        assert_eq!(
            compare(&normalise(&recording, &on).unwrap(), &normalise(&value, &on).unwrap(), 10).len(),
            1
        );
        let mut order = lower.clone();
        order["model_requests"][0]["headers"].as_array_mut().unwrap().swap(0, 1);
        assert!(
            !compare(&normalise(&recording, &on).unwrap(), &normalise(&order, &on).unwrap(), 10).is_empty()
        );
        // The session key is the row's name.
        let parsed: Rules = serde_json::from_value(json!({"header_name_case_row_40": true})).unwrap();
        assert!(parsed.header_name_case_row_next);
    }

    #[test]
    fn row_8_header_names_must_be_names() {
        for bad in ["", "*", "X-*-Lang", "**"] {
            let rules = Rules { ignore_headers_row_8: vec![bad.into()], ..Rules::default() };
            assert!(drop_row_8_headers(&mut json!({}), &rules).is_err(), "{bad:?}");
        }
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
    fn only_the_stand_in_host_port_may_differ_in_headers() {
        let rules = Rules::defaults();
        let normal = |value: Json| normalise(&value, &rules).unwrap();
        let first = json!({"headers": [["Host", "127.0.0.1:51296"], ["Content-Length", "37814"]]});
        let second = json!({"headers": [["Host", "127.0.0.1:63279"], ["Content-Length", "37814"]]});
        let longer = json!({"headers": [["Host", "127.0.0.1:63279"], ["Content-Length", "37815"]]});
        assert!(compare(&normal(first.clone()), &normal(second), 10).is_empty());
        assert_eq!(compare(&normal(first), &normal(longer), 10).len(), 1);
    }

    #[test]
    fn a_scoped_key_is_ignored_only_under_its_parent() {
        let rules = Rules { ignore_keys: vec!["activity_timing/revision".into()], ..Rules::default() };
        let normal = |value: Json| normalise(&value, &rules).unwrap();
        let first = json!({"revision": 4, "content": {"activity_timing": {"phase": "working", "revision": 4}},
                           "rows": [{"$json": {"activity_timing": {"revision": 4}}}]});
        let second = json!({"revision": 4, "content": {"activity_timing": {"phase": "working", "revision": 3}},
                            "rows": [{"$json": {"activity_timing": {"revision": 3}}}]});
        assert!(compare(&normal(first.clone()), &normal(second), 10).is_empty());
        // A campaign revision elsewhere is still compared, and so is a missing key.
        let other = json!({"revision": 5, "content": {"activity_timing": {"phase": "working", "revision": 4}},
                           "rows": [{"$json": {"activity_timing": {"revision": 4}}}]});
        let differences = compare(&normal(first.clone()), &normal(other), 10);
        assert_eq!(differences.len(), 1);
        assert_eq!(differences[0].pointer, "/revision");
        let missing = json!({"revision": 4, "content": {"activity_timing": {"phase": "working"}},
                             "rows": [{"$json": {"activity_timing": {"revision": 4}}}]});
        assert_eq!(compare(&normal(first), &normal(missing), 10).len(), 1);
        // Nested deeper than directly inside `activity_timing`, it is compared.
        let deep = |value: u64| json!({"activity_timing": {"inner": {"revision": value}}});
        assert_eq!(compare(&normal(deep(1)), &normal(deep(2)), 10).len(), 1);
    }

    #[test]
    fn an_ignored_pointer_may_name_an_array_item() {
        let rules = Rules { ignore_pointers: vec!["/requests/0/headers/1/1".into()], ..Rules::default() };
        let normal = |length: &str, other: &str| {
            normalise(
                &json!({"requests": [{"headers": [["Host", other], ["Content-Length", length]]}]}),
                &rules,
            )
            .unwrap()
        };
        assert!(compare(&normal("193457", "a"), &normal("193463", "a"), 10).is_empty());
        // The other header in the same array is still compared.
        let differences = compare(&normal("1", "a"), &normal("1", "b"), 10);
        assert_eq!(differences.len(), 1);
        assert_eq!(differences[0].pointer, "/requests/0/headers/0/1");
    }

    #[test]
    fn key_order_is_a_difference() {
        let differences = compare(&json!({"a": 1, "b": 2}), &json!({"b": 2, "a": 1}), 10);
        assert_eq!(differences.len(), 1);
        assert!(differences[0].reference.starts_with("key order"));
    }
}
