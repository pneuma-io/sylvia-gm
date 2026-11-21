//! Compare sylvia_domain's smaller domain modules with the frozen Python: `mechanics_choices`,
//! `ruleset_tests` and `campaign_sequel`.
//!
//! Regenerate with `tests/vectors/generate_domain_records_vectors.py` (needs the snapshot's
//! Python). Campaign Plans are read from their repository paths.

use serde_json::json;
use std::collections::BTreeSet;
use sylvia_base::hash::sha256_hex;
use sylvia_base::pyjson::{self, Dump};
use sylvia_base::{Json, JsonMap};
use sylvia_domain::inventory::{self, HoldingTransfer};
use sylvia_domain::{
    campaign_sequel, interview_contribution, mechanics_choices, ruleset_tests, DomainError, DomainResult,
};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/domain_records_vectors.json")).expect("vector file is valid JSON")
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

/// One argument, as the generator describes it.
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
    if let Some(count) = spec.get("suite_zeros").and_then(Json::as_u64) {
        let mut suite = vectors["bases"]["suite"].clone();
        suite["scenarios"][0]["initial_state"][0]["value"] = Json::Array(vec![json!(0); count as usize]);
        return suite;
    }
    panic!("not a value argument: {spec}")
}

/// The bytes of a `parse_ruleset_tests` argument.
fn bytes_argument(vectors: &Json, spec: &Json) -> Vec<u8> {
    if let Some(raw) = spec.get("raw").and_then(Json::as_str) {
        return raw.as_bytes().to_vec();
    }
    if let Some(raw) = spec.get("raw_hex").and_then(Json::as_str) {
        return hex::decode(raw).unwrap();
    }
    if let Some(count) = spec.get("raw_spaces").and_then(Json::as_u64) {
        return vec![b' '; count as usize];
    }
    sylvia_domain::package_library::canonical_json_bytes(&argument(vectors, spec))
}

fn object(value: Json) -> JsonMap {
    value.as_object().cloned().expect("an object argument")
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

fn entity_ids(vectors: &Json) -> BTreeSet<String> {
    vectors["entity_ids"].as_array().unwrap().iter().map(|id| id.as_str().unwrap().to_owned()).collect()
}

fn holdings(items: Vec<JsonMap>) -> Json {
    Json::Array(items.into_iter().map(Json::Object).collect())
}

fn call(vectors: &Json, case: &Json) -> Json {
    let specs = case["args"].as_array().unwrap();
    let arg = |index: usize| argument(vectors, &specs[index]);
    let result: DomainResult<Json> = match case["fn"].as_str().unwrap() {
        "choice_from_payload" => mechanics_choices::choice_from_payload(&arg(0))
            .map(|choice| Json::Object(mechanics_choices::choice_payload(&choice))),
        "answers_from_payload" => mechanics_choices::answers_from_payload(&arg(0)).map(|answers| {
            Json::Array(answers.iter().map(|a| Json::Object(mechanics_choices::answer_payload(a))).collect())
        }),
        "parse_ruleset_tests" => {
            ruleset_tests::parse_ruleset_tests(&bytes_argument(vectors, &specs[0])).map(Json::Object)
        }
        "validate_ruleset_tests" => ruleset_tests::validate_ruleset_tests(&arg(0)).map(Json::Object),
        "prepare_story" => campaign_sequel::prepare_story(&object(arg(0)), arg(1).as_i64().unwrap())
            .map(|(plan, mapping)| json!([plan, mapping])),
        "review_payload" => campaign_sequel::review_payload(&arg(0)).map(Json::Object),
        "sequel_markdown" => campaign_sequel::sequel_markdown(&object(arg(0))).map(Json::String),
        "sequel_preparation" => campaign_sequel::sequel_preparation(&object(arg(0)))
            .map(|value| value.map(Json::Object).unwrap_or(Json::Null)),
        "json_object" => campaign_sequel::json_object(&arg(0)).map(|map| Json::Object(map.clone())),
        "parse_interview_contribution_bytes" => interview_contribution::parse_interview_contribution_bytes(
            &bytes_argument(vectors, &specs[0]),
            arg(1).as_str().unwrap(),
        )
        .map(|c| json!([c.as_payload(), c.question_text()])),
        "validate_interview_contribution_answers" => {
            let base =
                sylvia_domain::package_library::canonical_json_bytes(&vectors["bases"]["contribution"]);
            let contribution =
                interview_contribution::parse_interview_contribution_bytes(&base, "ruleset").unwrap();
            interview_contribution::validate_interview_contribution_answers(&contribution, &arg(0))
                .map(Json::Object)
        }
        "validate_holdings" => {
            inventory::validate_holdings(arg(0).as_array().unwrap(), &entity_ids(vectors)).map(holdings)
        }
        "player_inventory" => inventory::player_inventory(
            arg(0).as_array().unwrap(),
            vectors["visible_entities"].as_object().unwrap(),
            arg(1).as_str().unwrap(),
        )
        .map(Json::Object),
        "transfer_holding" => {
            let spec = arg(1);
            let transfer = HoldingTransfer {
                source_id: &spec[0],
                destination_id: &spec[1],
                quantity: &spec[2],
                holder_id: &spec[3],
                owner_id: Some(&spec[4]).filter(|owner| !owner.is_null()),
            };
            inventory::transfer_holding(arg(0).as_array().unwrap(), transfer, &entity_ids(vectors))
                .map(holdings)
        }
        other => panic!("unknown function {other}"),
    };
    outcome(result)
}

/// differences.md rows 2 and 4: `NaN`/`Infinity` literals and nesting of 128 levels or more are
/// refused when the scenario JSON is read, with the reader's message, where Python reads them
/// and then reports "Scenario values must be finite JSON data." or "Scenario data exceeds the
/// nesting or size limit.".
fn unambiguous() -> Json {
    json!(["ContentShapeError", "Content fields do not match the declared format", [{
        "code": "ruleset_tests_invalid", "phase": "source",
        "message": "Scenario file must contain unambiguous UTF-8 JSON.",
        "file": "tests/scenarios.json", "path": "$"}]])
}

#[test]
fn domain_records_match_python() {
    let vectors = vectors();
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 1900);
    let mut failures = Vec::new();
    for case in cases {
        let actual = call(&vectors, case);
        let expected = match case.get("difference").and_then(Json::as_str) {
            Some("row 2" | "row 4") => unambiguous(),
            _ => case["outcome"].clone(),
        };
        if actual != expected {
            failures.push(format!("{} {}: expected {expected}, rust {actual}", case["fn"], case["args"]));
        }
    }
    let shown: Vec<String> = failures.iter().take(6).map(|f| f.chars().take(1200).collect()).collect();
    assert!(failures.is_empty(), "{} of {} differ: {:#?}", failures.len(), cases.len(), shown);
}
