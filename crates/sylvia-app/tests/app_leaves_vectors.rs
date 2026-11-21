//! Compare sylvia_app's first lane 1 modules with the frozen Python: `setting_character_requirements`,
//! `campaign_starting_world`, `srd_lite_weapon_set_migration` and `network_graph`.
//!
//! Regenerate with `tests/vectors/generate_app_leaves_vectors.py` (needs the snapshot's Python).

use serde_json::json;
use std::collections::BTreeSet;
use sylvia_app::{
    campaign_starting_world, network_graph, setting_character_requirements, srd_lite_weapon_set_migration,
};
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::campaign_plan::{validate_campaign_plan, CampaignPlanExpectations, PackageBinding};
use sylvia_domain::DomainResult;

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/app_leaves_vectors.json")).expect("vector file is valid JSON")
}

fn repository_json(path: &str) -> Json {
    let full = format!("{}/../../{path}", env!("CARGO_MANIFEST_DIR"));
    pyjson::loads(&std::fs::read_to_string(&full).unwrap_or_else(|error| panic!("{full}: {error}"))).unwrap()
}

/// The generator's `apply(document, edits)`.
fn apply(base: &Json, edits: &[Json]) -> Json {
    let mut document = base.clone();
    for edit in edits {
        let (op, path, value) = (edit[0].as_str().unwrap(), edit[1].as_array().unwrap(), &edit[2]);
        let Some((last, parents)) = path.split_last() else {
            document = value.clone();
            continue;
        };
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
    let edits = spec.get("edits").and_then(Json::as_array).map(Vec::as_slice).unwrap_or_default();
    if let Some(value) = spec.get("inline") {
        return apply(value, edits);
    }
    if let Some(name) = spec.get("base").and_then(Json::as_str) {
        return apply(&vectors["bases"][name], edits);
    }
    if let Some(path) = spec.get("plan").and_then(Json::as_str) {
        return apply(&repository_json(path), edits);
    }
    panic!("not a value argument: {spec}")
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
        Err(error) => json!([
            error.python_class(),
            error.message(),
            error.code(),
            error.diagnostics().iter().map(|d| Json::Object(d.as_payload())).collect::<Vec<_>>(),
        ]),
    }
}

fn object(value: Json) -> JsonMap {
    value.as_object().cloned().expect("an object")
}

fn binding(value: &Json) -> PackageBinding {
    PackageBinding {
        package_id: value["id"].as_str().unwrap().into(),
        version: value["version"].as_str().unwrap().into(),
        content_sha256: value["content_sha256"].as_str().unwrap().into(),
    }
}

fn materialize(vectors: &Json, specs: &[Json]) -> DomainResult<Json> {
    let expect = &vectors["plan_expectation"];
    let (ruleset, setting) = (binding(&expect["ruleset"]), binding(&expect["setting"]));
    let paths: BTreeSet<String> =
        expect["paths"].as_array().unwrap().iter().map(|p| p.as_str().unwrap().into()).collect();
    let none = BTreeSet::new();
    let plan = validate_campaign_plan(
        &argument(vectors, &specs[0]),
        "# Plan",
        CampaignPlanExpectations {
            expected_ruleset: Some(&ruleset),
            expected_setting: Some(&setting),
            expected_closure_sha256: expect["closure"].as_str().unwrap(),
            setting_paths: &paths,
            setting_knowledge_ids: &none,
        },
    )
    .expect("the base plan validates");
    let inputs = argument(vectors, &specs[1]);
    let party = inputs.get("party").cloned().unwrap_or_else(|| json!({}));
    let names = inputs.get("names").filter(|names| !names.is_null());
    let reference = inputs.get("reference").filter(|reference| !reference.is_null());
    let states = campaign_starting_world::materialize_starting_world(&plan, reference, &party, names)?;
    Ok(Json::Array(states.iter().map(|s| json!([s.namespace(), s.key(), s.value()])).collect()))
}

fn call(vectors: &Json, case: &Json) -> Json {
    let specs = case["args"].as_array().unwrap();
    let arg = |index: usize| argument(vectors, &specs[index]);
    let result: DomainResult<Json> = match case["fn"].as_str().unwrap() {
        "parse_setting_character_requirements" => {
            let text = match specs[0].get("raw").and_then(Json::as_str) {
                Some(raw) => raw.to_owned(),
                None => pyjson::dumps(&arg(0), Dump::DEFAULT),
            };
            setting_character_requirements::parse_setting_character_requirements(&text).map(Json::Object)
        }
        "validate_setting_character_data" => {
            let schema = match (specs[1].get("schema"), specs[1].get("inline").and_then(Json::as_bool)) {
                (Some(schema), _) => Some(object(schema.clone())),
                (None, Some(true)) => Some(
                    setting_character_requirements::parse_setting_character_requirements(&pyjson::dumps(
                        &vectors["bases"]["envelope"],
                        Dump::DEFAULT,
                    ))
                    .unwrap(),
                ),
                _ => None,
            };
            setting_character_requirements::validate_setting_character_data(&arg(0), schema.as_ref())
                .map(Json::Object)
        }
        "materialize_starting_world" => materialize(vectors, specs),
        "migrate_state" => {
            srd_lite_weapon_set_migration::migrate_state(&object(arg(0)), &arg(1)).map(Json::Object)
        }
        "project_network_graph" => {
            let visible = vectors["visible"]
                .as_array()
                .unwrap()
                .iter()
                .map(|pair| (pair[0].as_str().unwrap().to_owned(), pair[1].as_str().unwrap().to_owned()))
                .collect();
            network_graph::project_network_graph(&arg(0), &visible).map(Json::Object)
        }
        "resolve_network_action_parameters" => {
            let inputs = arg(0);
            let action = inputs.get("action").cloned().unwrap_or_else(|| json!({}));
            let selection = inputs.get("selection").cloned().unwrap_or(Json::Null);
            let projection = object(vectors["graph_projection"].clone());
            let actors: BTreeSet<String> = ["hero".to_owned()].into();
            network_graph::resolve_network_action_parameters(&action, &selection, &projection, &actors)
                .map(Json::Object)
        }
        "visible_physical_entity_ids" => {
            network_graph::visible_physical_entity_ids(arg(0).as_array().unwrap())
                .map(|ids| Json::Array(ids.into_iter().map(|(s, e)| json!([s, e])).collect()))
        }
        "bind_physical_network_surfaces" => {
            network_graph::bind_physical_network_surfaces(arg(0).as_array().unwrap())
                .map(|surfaces| Json::Array(surfaces.into_iter().map(Json::Object).collect()))
        }
        other => panic!("unknown function {other}"),
    };
    outcome(result)
}

#[test]
fn app_leaves_match_python() {
    let vectors = vectors();
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 2500);
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
