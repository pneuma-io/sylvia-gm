//! Compare sylvia_domain::yaml_bounds with the frozen Python's `validate_yaml_graph`, on graphs
//! composed by `sylvia_base::pyyaml` with both loaders, and through a `construct_document` hook.
//!
//! Regenerate with `tests/vectors/generate_yaml_bounds_vectors.py` (needs the snapshot's Python).

use serde_json::json;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::pyyaml::{self, Constructor, Hooks, Loader, NodeId, Value, YamlError};
use sylvia_base::Json;
use sylvia_domain::yaml_bounds::{validate_yaml_graph, YamlGraphLimits};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/yaml_bounds_vectors.json")).expect("vector file is valid JSON")
}

fn limits(spec: &Json) -> YamlGraphLimits {
    YamlGraphLimits {
        maximum_depth: spec["maximum_depth"].as_u64().unwrap() as usize,
        maximum_nodes: spec["maximum_nodes"].as_u64().unwrap() as usize,
        maximum_object_members: spec["maximum_object_members"].as_u64().map(|value| value as usize),
        count_mapping_keys: spec["count_mapping_keys"].as_bool().unwrap(),
    }
}

fn check(text: &str, loader: Loader, limits: YamlGraphLimits) -> Json {
    let composed = match pyyaml::compose(text, loader) {
        Ok(composed) => composed,
        Err(error) => return json!(["compose", error.class_name()]),
    };
    let Some(root) = composed.root else { return json!(["empty"]) };
    match validate_yaml_graph(&composed.graph, root, limits) {
        Ok(()) => json!(["ok"]),
        Err(error) => json!(["YamlShapeError", error.0]),
    }
}

/// The generator's `BoundedLoader`: the "small-values" limits, then the base construction.
const SMALL_VALUES: YamlGraphLimits = YamlGraphLimits {
    maximum_depth: 4,
    maximum_nodes: 20,
    maximum_object_members: Some(3),
    count_mapping_keys: false,
};

fn bounded(constructor: &mut Constructor, root: NodeId) -> Result<Option<Value>, YamlError> {
    validate_yaml_graph(constructor.graph, root, SMALL_VALUES)?;
    constructor.base_construct_document(root)
}

fn load(text: &str) -> Json {
    let hooks = Hooks { construct_document: Some(bounded), ..Hooks::default() };
    match pyyaml::load(text, Loader::C, hooks) {
        Ok(loaded) => match loaded.to_json() {
            Ok(value) => json!(["ok", pyjson::dumps(&value, Dump::DEFAULT)]),
            Err(_) => json!(["not JSON"]),
        },
        Err(error @ YamlError::Python { .. }) => json!([error.class_name(), error.to_string()]),
        Err(error) => json!([error.class_name(), null]),
    }
}

#[test]
fn yaml_graphs_are_bounded_like_python() {
    let vectors = vectors();
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 150);
    let mut failures = Vec::new();
    let mut checked = 0;
    for case in cases {
        let text = case["text"].as_str().unwrap();
        for (key, expected) in case["outcomes"].as_object().unwrap() {
            let (loader, name) = key.split_once('/').unwrap();
            let loader = if loader == "c" { Loader::C } else { Loader::Pure };
            let actual = check(text, loader, limits(&vectors["limits"][name]));
            checked += 1;
            if &actual != expected {
                failures.push(format!("{key} {text:?}: python {expected}, rust {actual}"));
            }
        }
        if let Some(expected) = case.get("load") {
            let actual = load(text);
            checked += 1;
            if &actual != expected {
                failures.push(format!("load {text:?}: python {expected}, rust {actual}"));
            }
        }
    }
    let shown: Vec<String> = failures.iter().take(6).map(|f| f.chars().take(600).collect()).collect();
    assert!(failures.is_empty(), "{} of {checked} differ: {:#?}", failures.len(), shown);
}
