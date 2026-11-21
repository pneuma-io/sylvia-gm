//! Compare sylvia_domain::campaign_plan with the frozen Python's Campaign Plan validator.
//!
//! Regenerate with `tests/vectors/generate_campaign_plan_vectors.py` (needs the snapshot's
//! Python). Campaign Plans and their readable renderings are read from their repository paths.

use serde_json::json;
use std::collections::BTreeSet;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::campaign_plan::{
    parse_starting_party_policy, validate_campaign_plan, CampaignPlanExpectations, PackageBinding,
    ValidatedCampaignPlan,
};
use sylvia_domain::{DomainError, DomainResult};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/campaign_plan_vectors.json")).expect("vector file is valid JSON")
}

fn repository_text(path: &str) -> String {
    let full = format!("{}/../../{path}", env!("CARGO_MANIFEST_DIR"));
    std::fs::read_to_string(&full).unwrap_or_else(|error| panic!("{full}: {error}"))
}

/// The generator's `expand(value)`: `{"$repeat": [text, count]}` stands for `text * count`.
fn expand(value: &Json) -> Json {
    match value.as_object().and_then(|map| map.get("$repeat").filter(|_| map.len() == 1)) {
        Some(spec) => Json::String(spec[0].as_str().unwrap().repeat(spec[1].as_u64().unwrap() as usize)),
        None => value.clone(),
    }
}

/// The generator's `apply(document, edits)`.
fn apply(base: &Json, edits: &[Json]) -> Json {
    let mut document = base.clone();
    for edit in edits {
        let (op, path) = (edit[0].as_str().unwrap(), edit[1].as_array().unwrap());
        let expanded = expand(&edit[2]);
        let value = &expanded;
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

/// The generator's `no_ruleset_plan(plan)`.
fn no_ruleset_plan(plan: &Json, fields: &JsonMap) -> Json {
    let mut document = plan.as_object().unwrap().clone();
    for (key, value) in fields {
        document.insert(key.clone(), value.clone());
    }
    for act in document["structure"].as_array_mut().unwrap() {
        act["setting_refs"] = json!([]);
    }
    let secrets = document["secrets"].as_array_mut().unwrap();
    secrets.retain(|item| !item.as_object().is_some_and(|secret| secret.contains_key("setting_fact_id")));
    Json::Object(document)
}

fn binding(value: &Json) -> Option<PackageBinding> {
    value.as_object().map(|item| PackageBinding {
        package_id: item["id"].as_str().unwrap().into(),
        version: item["version"].as_str().unwrap().into(),
        content_sha256: item["content_sha256"].as_str().unwrap().into(),
    })
}

fn strings(value: &Json) -> BTreeSet<String> {
    value.as_array().unwrap().iter().map(|item| item.as_str().unwrap().to_owned()).collect()
}

fn project(validated: &ValidatedCampaignPlan) -> Json {
    json!({
        "content_sha256": validated.content_sha256,
        "artifact_id": validated.artifact_id,
        "title": validated.title,
        "markdown_sha256": sha256_hex(validated.readable_markdown.as_bytes()),
        "paths": validated.referenced_setting_paths,
        "payload_sha256": sha256_hex(pyjson::dumps(&Json::Object(validated.payload.clone()), Dump::DEFAULT).as_bytes()),
    })
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
        Err(DomainError::Diagnosed { error_type, message, diagnostics }) => {
            json!([
                error_type,
                message,
                diagnostics.iter().map(|d| Json::Object(d.as_payload())).collect::<Vec<_>>()
            ])
        }
        Err(error) => json!([error.python_class(), error.message()]),
    }
}

fn call(vectors: &Json, case: &Json) -> Json {
    let path = case["plan"].as_str().unwrap();
    let mut plan = pyjson::loads(&repository_text(path)).unwrap();
    if case.get("no_ruleset") == Some(&Json::Bool(true)) {
        plan = no_ruleset_plan(&plan, vectors["no_ruleset_fields"].as_object().unwrap());
    }
    let plan = apply(&plan, case["edits"].as_array().unwrap());
    if case["fn"] == "parse_starting_party_policy" {
        return outcome(
            parse_starting_party_policy(&plan["starting_party"])
                .map(|policy| Json::Object(policy.as_payload())),
        );
    }
    let markdown = match &case["markdown"] {
        Json::Null => repository_text(&path.replace("plan.json", "plan.md")),
        spec if spec.get("text").is_some() => spec["text"].as_str().unwrap().to_owned(),
        spec => spec["repeat"][0].as_str().unwrap().repeat(spec["repeat"][1].as_u64().unwrap() as usize),
    };
    let expect = match &case["expect"] {
        Json::Null => &vectors["expectations"][path],
        other => other,
    };
    let (ruleset, setting) = (binding(&expect["ruleset"]), binding(&expect["setting"]));
    let (paths, knowledge) = (strings(&expect["paths"]), strings(&expect["knowledge"]));
    let expectations = CampaignPlanExpectations {
        expected_ruleset: ruleset.as_ref(),
        expected_setting: setting.as_ref(),
        expected_closure_sha256: expect["closure"].as_str().unwrap(),
        setting_paths: &paths,
        setting_knowledge_ids: &knowledge,
    };
    outcome(validate_campaign_plan(&plan, &markdown, expectations).map(|validated| project(&validated)))
}

#[test]
fn campaign_plans_validate_like_python() {
    let vectors = vectors();
    assert_eq!(vectors["plans"].as_array().unwrap().len(), 18);
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 2000);
    let mut failures = Vec::new();
    for case in cases {
        let actual = call(&vectors, case);
        if actual != case["outcome"] {
            failures.push(format!(
                "{} {} {}: python {}, rust {actual}",
                case["fn"], case["plan"], case["edits"], case["outcome"]
            ));
        }
    }
    let shown: Vec<String> = failures.iter().take(6).map(|f| f.chars().take(1500).collect()).collect();
    assert!(failures.is_empty(), "{} of {} differ: {:#?}", failures.len(), cases.len(), shown);
}
