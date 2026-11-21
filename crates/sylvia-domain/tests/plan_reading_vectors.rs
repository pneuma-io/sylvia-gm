//! Compare sylvia_domain::campaign_ending and ::no_ruleset_plan with the frozen Python.
//!
//! Regenerate with `tests/vectors/generate_plan_reading_vectors.py` (needs the snapshot's
//! Python). Campaign Plans are read from their repository paths.

use serde_json::json;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::{campaign_ending, no_ruleset_plan, DomainError, DomainResult};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/plan_reading_vectors.json")).expect("vector file is valid JSON")
}

fn repository_json(path: &str) -> Json {
    let full = format!("{}/../../{path}", env!("CARGO_MANIFEST_DIR"));
    let text = std::fs::read_to_string(&full).unwrap_or_else(|error| panic!("{full}: {error}"));
    pyjson::loads(&text).expect("a repository JSON file")
}

/// The generator's `apply(document, edits)`.
fn apply(base: &Json, edits: &[Json]) -> Json {
    let mut document = base.clone();
    for edit in edits {
        let (op, path, value) = (edit[0].as_str().unwrap(), edit[1].as_array().unwrap(), &edit[2]);
        let (last, parents) = path.split_last().expect("a non-empty path");
        let mut parent = &mut document;
        for part in parents {
            parent = match part {
                Json::String(key) => &mut parent[key.as_str()],
                index => &mut parent[index.as_u64().unwrap() as usize],
            };
        }
        match (op, last) {
            ("delete", Json::String(key)) => {
                parent.as_object_mut().unwrap().shift_remove(key.as_str());
            }
            ("delete", index) => {
                parent.as_array_mut().unwrap().remove(index.as_u64().unwrap() as usize);
            }
            (_, Json::String(key)) => {
                parent.as_object_mut().unwrap().insert(key.clone(), value.clone());
            }
            (_, index) => parent[index.as_u64().unwrap() as usize] = value.clone(),
        }
    }
    document
}

fn argument(vectors: &Json, spec: &Json) -> Json {
    if let Some(value) = spec.get("inline") {
        return value.clone();
    }
    let edits = spec["edits"].as_array().unwrap();
    if let Some(path) = spec.get("plan").and_then(Json::as_str) {
        return apply(&repository_json(path), edits);
    }
    let path = spec["no_ruleset"].as_str().expect("a plan argument");
    let mut plan = repository_json(path);
    for (key, value) in vectors["no_ruleset_fields"].as_object().unwrap() {
        plan.as_object_mut().unwrap().insert(key.clone(), value.clone());
    }
    apply(&plan, edits)
}

fn outcome(result: DomainResult<Json>) -> Json {
    match result {
        Ok(value) => {
            let text = pyjson::dumps(&value, Dump::DEFAULT);
            if text.len() <= 1000 {
                json!(["ok", text])
            } else {
                json!(["ok-sha256", sha256_hex(text.as_bytes())])
            }
        }
        Err(error @ DomainError::Diagnosed { .. }) => panic!("no diagnosed errors here: {error}"),
        Err(error) => json!([error.python_class(), error.message()]),
    }
}

fn strings<'a>(values: impl IntoIterator<Item = &'a String>) -> Json {
    Json::Array(values.into_iter().map(|value| Json::from(value.as_str())).collect())
}

/// The generator's `ending_items(plan)`.
fn ending_items(plan: &JsonMap) -> DomainResult<Json> {
    let mut items = Vec::new();
    for ending in campaign_ending::intended_endings(plan) {
        let identity = campaign_ending::ending_progress_id(ending)?;
        items.push(json!([identity, strings(&campaign_ending::required_ending_items(plan, ending)?)]));
    }
    Ok(Json::Array(items))
}

fn call(vectors: &Json, case: &Json) -> Json {
    let specs = case["args"].as_array().unwrap();
    let arg = |index: usize| argument(vectors, &specs[index]);
    let plan = || arg(0).as_object().cloned().expect("a plan object");
    let result: DomainResult<Json> = match case["fn"].as_str().unwrap() {
        "intended_endings" => {
            let plan = plan();
            Ok(Json::Array(
                campaign_ending::intended_endings(&plan)
                    .into_iter()
                    .map(|m| Json::Object(m.clone()))
                    .collect(),
            ))
        }
        "ending_items" => ending_items(&plan()),
        "required_story_items" => campaign_ending::required_story_items(&plan()).map(|items| strings(&items)),
        "validate_ending" => campaign_ending::validate_ending(&plan()).map(|()| Json::Null),
        "check_ending_progress" => {
            let (progress, updates) = (arg(1), arg(2));
            campaign_ending::check_ending_progress(
                &plan(),
                progress.as_array().unwrap(),
                updates.as_array().unwrap(),
            )
            .map(|()| Json::Null)
        }
        "resolved_progress" => {
            campaign_ending::resolved_progress(arg(0).as_array().unwrap()).map(|items| strings(&items))
        }
        "is_no_ruleset_plan" => Ok(Json::Bool(no_ruleset_plan::is_no_ruleset_plan(&plan()))),
        "validate_no_ruleset_world" => {
            no_ruleset_plan::validate_no_ruleset_world(&plan()).map(|paths| strings(&paths))
        }
        "no_ruleset_world_records" => no_ruleset_plan::no_ruleset_world_records(&plan()).map(|records| {
            Json::Array(records.into_iter().map(|(identity, payload)| json!([identity, payload])).collect())
        }),
        other => panic!("unknown function {other}"),
    };
    outcome(result)
}

#[test]
fn plan_reading_matches_python() {
    let vectors = vectors();
    assert_eq!(vectors["plans"].as_array().unwrap().len(), 18);
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 8000);
    let mut failures = Vec::new();
    for case in cases {
        let actual = call(&vectors, case);
        if actual != case["outcome"] {
            failures
                .push(format!("{} {}: python {}, rust {actual}", case["fn"], case["args"], case["outcome"]));
        }
    }
    let shown: Vec<String> = failures.iter().take(6).map(|f| f.chars().take(1200).collect()).collect();
    assert!(failures.is_empty(), "{} of {} differ: {:#?}", failures.len(), cases.len(), shown);
}
