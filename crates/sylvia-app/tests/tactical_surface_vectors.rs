//! Compare sylvia-app::tactical_grid's projections and selection binding,
//! sylvia-app::piece_table and sylvia-app::map_segment_preview with the frozen Python.
//!
//! Regenerate with `tests/vectors/generate_tactical_surface_vectors.py` (needs the snapshot's
//! Python; `--record` also reruns Python's surface tests to record their calls).

use serde_json::{json, Value as Json};
use std::collections::BTreeSet;
use sylvia_app::map_segment_preview::segment_preview;
use sylvia_app::piece_table::{project_piece_table, resolve_piece_action_parameters};
use sylvia_app::tactical_grid::{
    coordinate_actor_reference, project_coordinate_map, project_tactical_grid,
    resolve_tactical_action_parameters, StatePaths,
};

const VECTORS: &str = include_str!("vectors/tactical_surface_vectors.json");

/// A stored value with each `{"$doc": digest}` reference, at any depth, replaced by its document.
fn loaded(value: &Json, documents: &Json) -> Json {
    match value {
        Json::Object(map) if map.len() == 1 && map.contains_key("$doc") => {
            loaded(&documents[map["$doc"].as_str().unwrap()], documents)
        }
        Json::Object(map) => {
            Json::Object(map.iter().map(|(key, item)| (key.clone(), loaded(item, documents))).collect())
        }
        Json::Array(items) => Json::Array(items.iter().map(|item| loaded(item, documents)).collect()),
        other => other.clone(),
    }
}

fn ok<T: Into<Json>>(value: T) -> Json {
    json!({"ok": value.into()})
}

/// A case's `state_paths`: where its value holds the campaign's frozen state.
fn state_paths(value: &Json) -> StatePaths {
    let path =
        |path: &Json| path.as_array().unwrap().iter().map(|key| key.as_str().unwrap().to_owned()).collect();
    StatePaths::from_paths(value.as_array().unwrap().iter().map(path).collect())
}

fn ids(value: &Json) -> BTreeSet<String> {
    value.as_array().unwrap().iter().map(|item| item.as_str().unwrap().to_owned()).collect()
}

fn run(case: &Json, documents: &Json) -> Json {
    let arguments: serde_json::Map<String, Json> = case["arguments"]
        .as_object()
        .unwrap()
        .iter()
        .map(|(key, value)| (key.clone(), loaded(value, documents)))
        .collect();
    let null = Json::Null;
    let arg = |name: &str| arguments.get(name).unwrap_or(&null);
    let object = |name: &str| arg(name).as_object().expect("an object argument");
    let error = |class: &str, message: String| json!({"error": [class, message]});
    match case["function"].as_str().unwrap() {
        "grid" => match project_tactical_grid(arg("value")) {
            Ok(value) => ok(value),
            Err(e) => error(e.python_class(), e.to_string()),
        },
        "coordinate" => match project_coordinate_map(
            arg("value"),
            arg("actor_reference").as_str(),
            &state_paths(arg("state_paths")),
        ) {
            Ok(value) => ok(value),
            Err(e) => error(e.python_class(), e.to_string()),
        },
        "actor_reference" => match coordinate_actor_reference(arg("actions")) {
            Ok(value) => ok(value),
            Err(e) => error(e.python_class(), e.to_string()),
        },
        "tactical" => {
            let scene = arg("authoritative_scene");
            match resolve_tactical_action_parameters(
                object("action"),
                arg("selection"),
                object("projection"),
                Some(scene),
            ) {
                Ok(value) => ok(value),
                Err(e) => error(e.python_class(), e.to_string()),
            }
        }
        "piece_table" => {
            match project_piece_table(arg("value"), &ids(arg("viewer_ids")), &ids(arg("action_ids"))) {
                Ok(value) => ok(value),
                Err(e) => error(e.python_class(), e.to_string()),
            }
        }
        "piece_action" => {
            match resolve_piece_action_parameters(
                object("action"),
                arg("selection"),
                object("projected_table"),
            ) {
                Ok(value) => ok(value),
                Err(e) => error(e.python_class(), e.to_string()),
            }
        }
        "segment" => match segment_preview(object("surface"), arg("data"), arg("selection")) {
            Ok(value) => ok(value),
            Err(e) => error(e.python_class(), e.to_string()),
        },
        other => panic!("unknown function {other}"),
    }
}

/// The first path where two JSON values differ, with both sides there.
fn first_difference(expected: &Json, actual: &Json, path: &str) -> Option<String> {
    match (expected, actual) {
        (Json::Object(left), Json::Object(right)) => {
            let left_keys: Vec<&String> = left.keys().collect();
            let right_keys: Vec<&String> = right.keys().collect();
            if left_keys != right_keys {
                return Some(format!("{path}: keys {left_keys:?} != {right_keys:?}"));
            }
            left.iter()
                .find_map(|(key, value)| first_difference(value, &right[key], &format!("{path}.{key}")))
        }
        (Json::Array(left), Json::Array(right)) if left.len() == right.len() => left
            .iter()
            .zip(right)
            .enumerate()
            .find_map(|(index, (l, r))| first_difference(l, r, &format!("{path}[{index}]"))),
        _ if expected == actual => None,
        _ => Some(format!("{path}: expected {expected}, got {actual}")),
    }
}

#[test]
fn tactical_surfaces_match_python() {
    let vectors: Json = serde_json::from_str(VECTORS).unwrap();
    let documents = &vectors["documents"];
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 1500, "the vector file is complete");
    assert!(
        cases.iter().any(|case| case["label"].as_str().unwrap().starts_with("recorded ")),
        "recorded calls are kept"
    );
    let mut failures = Vec::new();
    for case in cases {
        let actual = run(case, documents);
        if actual != case["expected"] {
            let at = first_difference(&case["expected"], &actual, "$").unwrap_or_default();
            failures.push(format!("{}: {at}", case["label"]));
        }
    }
    let shown: Vec<String> =
        failures.iter().take(25).map(|failure| failure.chars().take(1500).collect()).collect();
    assert!(failures.is_empty(), "{} of {} cases differ:\n{}", failures.len(), cases.len(), shown.join("\n"));
}
