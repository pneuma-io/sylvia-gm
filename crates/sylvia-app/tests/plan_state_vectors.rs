//! Compare sylvia_app::plan_state_admission with the frozen Python.
//!
//! Regenerate with `tests/vectors/generate_plan_state_vectors.py` (needs the snapshot's Python).

use serde_json::json;
use sylvia_app::plan_state_admission::validate_starting_declarations;
use sylvia_base::{pyjson, Json};
use sylvia_domain::package_library::manifest_from_record;
use sylvia_domain::starting_state::{StateReference, StateSchema};
use sylvia_domain::DomainError;

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/plan_state_vectors.json")).expect("vector file is valid JSON")
}

fn repository_json(path: &str) -> Json {
    let full = format!("{}/../../{path}", env!("CARGO_MANIFEST_DIR"));
    pyjson::loads(&std::fs::read_to_string(&full).unwrap_or_else(|error| panic!("{full}: {error}"))).unwrap()
}

/// The generator's `apply(document, edits)`, with `{"$repeat": [value, count]}` expanded.
fn apply(base: &Json, edits: &[Json]) -> Json {
    let mut document = base.clone();
    for edit in edits {
        let (op, path) = (edit[0].as_str().unwrap(), edit[1].as_array().unwrap());
        let value = match edit[2].get("$repeat") {
            Some(spec) => Json::Array(vec![spec[0].clone(); spec[1].as_u64().unwrap() as usize]),
            None => edit[2].clone(),
        };
        let (last, parents) = path.split_last().unwrap();
        let parent = parents.iter().fold(&mut document, |node, part| match part {
            Json::String(key) => &mut node[key.as_str()],
            index => &mut node[index.as_u64().unwrap() as usize],
        });
        match (op, last) {
            ("delete", Json::String(key)) => {
                parent.as_object_mut().unwrap().shift_remove(key.as_str());
            }
            ("delete", index) => {
                parent.as_array_mut().unwrap().remove(index.as_u64().unwrap() as usize);
            }
            (_, Json::String(key)) => {
                parent.as_object_mut().unwrap().insert(key.clone(), value);
            }
            (_, index) => parent[index.as_u64().unwrap() as usize] = value,
        }
    }
    document
}

fn strings(value: &Json) -> Vec<String> {
    value.as_array().unwrap().iter().map(|part| part.as_str().unwrap().to_owned()).collect()
}

fn declarations(specs: &Json) -> Vec<StateSchema> {
    specs
        .as_array()
        .unwrap()
        .iter()
        .map(|item| StateSchema {
            key: item["key"].as_str().unwrap().into(),
            schema: item["schema"].as_object().unwrap().clone(),
            references: item["references"]
                .as_array()
                .unwrap()
                .iter()
                .map(|reference| StateReference {
                    source: strings(&reference[0]),
                    target_key: reference[1].as_str().unwrap().into(),
                    target: strings(&reference[2]),
                })
                .collect(),
            required: item["required"].as_bool().unwrap(),
            visibility: "public".into(),
        })
        .collect()
}

#[test]
fn plan_starting_state_matches_python() {
    let vectors = vectors();
    let srd = manifest_from_record(&repository_json(vectors["srd"].as_str().unwrap()), Some(4)).unwrap();
    let namespace = vectors["namespace"].as_str().unwrap();
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 800);
    let mut failures = Vec::new();
    for case in cases {
        let base = match case["plan"].as_str() {
            Some(path) => repository_json(path),
            None => vectors["synthetic_plan"].clone(),
        };
        let plan = apply(&base, case["edits"].as_array().unwrap());
        let plan = plan.as_object().unwrap();
        let result = match case["declarations"].as_str().unwrap() {
            "srd" => validate_starting_declarations(plan, &srd.package_id, &srd.version, &srd.state_schemas),
            name => validate_starting_declarations(
                plan,
                namespace,
                "1.0.0",
                &declarations(&vectors["declarations"][name]),
            ),
        };
        let actual = match result {
            Ok(()) => json!(["ok"]),
            Err(error @ DomainError::Diagnosed { .. }) | Err(error @ DomainError::Rejected { .. }) => {
                json!([
                    error.python_class(),
                    error.message(),
                    error.diagnostics().iter().map(|d| Json::Object(d.as_payload())).collect::<Vec<_>>()
                ])
            }
            Err(error) => json!([error.python_class(), error.message(), []]),
        };
        if actual != case["outcome"] {
            failures.push(format!(
                "{} {}: python {}, rust {actual}",
                case["plan"], case["edits"], case["outcome"]
            ));
        }
    }
    let shown: Vec<String> = failures.iter().take(6).map(|f| f.chars().take(900).collect()).collect();
    assert!(failures.is_empty(), "{} of {} differ: {:#?}", failures.len(), cases.len(), shown);
}
