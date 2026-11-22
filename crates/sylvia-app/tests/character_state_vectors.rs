//! Compare sylvia_app's character rules and initial character state with the frozen Python, on
//! SRD Lite's own declaration and starters (through lane 2's executor and evaluator) and a
//! synthetic declaration.
//!
//! Regenerate with `tests/vectors/generate_character_state_vectors.py` (needs the snapshot's Python).

use serde_json::json;
use sylvia_app::character_rules::verify_character_rules;
use sylvia_app::initial_character_state::{
    initialize_campaign_character_state, materialize_initial_character_state, verify_initial_character_state,
    verify_placed_character,
};
use sylvia_base::{pyjson, Json, JsonMap};
use sylvia_domain::package_library::{manifest_from_record, ParticipantArtifactDeclaration};
use sylvia_domain::{DomainError, DomainResult};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/character_state_vectors.json")).expect("vector file is valid JSON")
}

fn repository_json(path: &str) -> Json {
    let full = format!("{}/../../{path}", env!("CARGO_MANIFEST_DIR"));
    pyjson::loads(&std::fs::read_to_string(&full).unwrap_or_else(|error| panic!("{full}: {error}"))).unwrap()
}

/// The generator's `apply(document, edits)`.
fn apply(mut document: Json, edits: &[Json]) -> Json {
    for edit in edits {
        let (op, path, value) = (edit[0].as_str().unwrap(), edit[1].as_array().unwrap(), edit[2].clone());
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

fn outcome<T: Into<Json>>(result: DomainResult<T>) -> Json {
    match result {
        Ok(value) => match value.into() {
            Json::Null => json!(["ok", null]),
            value => json!(["ok", value]),
        },
        Err(error) => error_outcome(&error),
    }
}

fn error_outcome(error: &DomainError) -> Json {
    let diagnostics: Vec<Json> = error.diagnostics().iter().map(|d| Json::Object(d.as_payload())).collect();
    json!([error.python_class(), error.message(), error.code(), diagnostics])
}

fn object(map: JsonMap) -> Json {
    Json::Object(map)
}

fn synthetic_declaration(spec: &Json) -> ParticipantArtifactDeclaration {
    ParticipantArtifactDeclaration::new(
        &spec["kind"],
        &spec["schema_id"],
        &spec["schema_sha256"],
        spec["schema"].as_object().unwrap().clone(),
        spec["entity_state_schema"].as_object().cloned(),
        spec["initial_state_calculations"]
            .as_array()
            .unwrap()
            .iter()
            .map(|c| c.as_object().unwrap().clone())
            .collect(),
        spec["initial_state_validation"].as_object().cloned(),
    )
    .unwrap()
}

/// The generator's `run_set`, compared key by key.
fn check_set(
    label: &str,
    declaration: &ParticipantArtifactDeclaration,
    participant: &Json,
    authored: &Json,
    campaign_states: &[Json],
    case: &Json,
    failures: &mut Vec<String>,
) {
    let schema = declaration.entity_state_schema.clone().unwrap();
    let calculations: Vec<Json> =
        declaration.initial_state_calculations.iter().cloned().map(Json::Object).collect();
    let validation = declaration.initial_state_validation.clone().map(Json::Object);
    let mut compare = |key: String, actual: Json, expected: &Json| {
        if actual != *expected {
            failures.push(format!("{label} {key}: python {expected}, rust {actual}"));
        }
    };
    let authored_map = authored.as_object().unwrap();
    let materialized = materialize_initial_character_state(
        participant,
        authored_map,
        &schema,
        &calculations,
        validation.as_ref(),
        None,
    );
    let materialize = outcome(materialized.map(object));
    compare("materialize".into(), materialize.clone(), &case["materialize"]);
    if case.get("verify").is_none() || materialize[0] != "ok" {
        return;
    }
    let complete = materialize[1].as_object().unwrap().clone();
    let verified = verify_initial_character_state(
        participant,
        &complete,
        &schema,
        &calculations,
        validation.as_ref(),
        None,
    );
    compare("verify".into(), outcome(verified.map(object)), &case["verify"]);
    let complete_json = Json::Object(complete.clone());
    let rules = verify_character_rules(participant, &complete_json, validation.as_ref(), None, None);
    compare("rules".into(), outcome(rules.map(object)), &case["rules"]);
    for (index, campaign_state) in campaign_states.iter().enumerate() {
        let initialized =
            initialize_campaign_character_state(participant, &complete, declaration, campaign_state, None);
        compare(format!("initialize[{index}]"), outcome(initialized.map(object)), &case["initialize"][index]);
    }
    let mut placed = complete.clone();
    placed.insert("token_id".into(), json!("token-1"));
    placed.insert("position".into(), json!([1, 2]));
    compare(
        "placed".into(),
        outcome(verify_placed_character(participant, &placed, declaration, None).map(|()| Json::Null)),
        &case["placed"],
    );
    for (index, tampered) in case["tampered"].as_array().unwrap().iter().enumerate() {
        let top = tampered[1].as_str().unwrap();
        let mut changed = complete.clone();
        if tampered[0] == "set" {
            let replacement = if changed[top] == json!(999) { json!(998) } else { json!(999) };
            changed.insert(top.into(), replacement);
        } else {
            changed.shift_remove(top);
        }
        let result = verify_initial_character_state(
            participant,
            &changed,
            &schema,
            &calculations,
            validation.as_ref(),
            None,
        );
        compare(format!("tampered[{index}]"), outcome(result.map(object)), &tampered[2]);
    }
}

#[test]
fn character_state_matches_python() {
    let vectors = vectors();
    let srd = manifest_from_record(&repository_json(vectors["srd"].as_str().unwrap()), Some(4)).unwrap();
    let declaration = srd.participant_artifacts.iter().find(|item| item.kind == "player_character").unwrap();
    let starters = repository_json(vectors["starters"].as_str().unwrap());
    let campaign_states = vectors["campaign_states"].as_array().unwrap();
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 300);
    let mut failures = Vec::new();
    let mut row_cases = 0;
    for case in cases {
        if case["set"] == "srd" {
            let row = starters.as_array().unwrap().iter().find(|row| row["id"] == case["starter"]).unwrap();
            let document = json!({"participant": row["participant"], "entity_state": row["entity_state"]});
            let varied = apply(document, case["edits"].as_array().unwrap());
            let label = format!("{} {}", case["starter"], case["edits"]);
            let states: &[Json] = if case.get("verify").is_some() { campaign_states } else { &[] };
            check_set(
                &label,
                declaration,
                &varied["participant"],
                &varied["entity_state"],
                states,
                case,
                &mut failures,
            );
            continue;
        }
        let variant = case["variant"].as_str().unwrap();
        let spec =
            apply(vectors["synthetic"].clone(), vectors["synthetic_edits"][variant].as_array().unwrap());
        let synthetic = synthetic_declaration(&spec);
        let inputs = &vectors["synthetic_inputs"][case["input"].as_u64().unwrap() as usize];
        let label = format!("synthetic {variant} {}", case["input"]);
        let states = [json!({"advancement": {"x": 1}}), json!({})];
        if case.get("row").is_some() {
            // Differences row NEXT (merge results in creation calculations): a `merge(...)`
            // result's own missing key raises here, where Python's plain dict gives None.
            row_cases += 1;
            let schema = synthetic.entity_state_schema.clone().unwrap();
            let calculations: Vec<Json> =
                synthetic.initial_state_calculations.iter().cloned().map(Json::Object).collect();
            let validation = synthetic.initial_state_validation.clone().map(Json::Object);
            let result = materialize_initial_character_state(
                &inputs[0],
                inputs[1].as_object().unwrap(),
                &schema,
                &calculations,
                validation.as_ref(),
                None,
            );
            let python_ok = case["materialize"][0] == "ok";
            let rust_missing = result.as_ref().err().and_then(DomainError::code)
                == Some("character_initial_state_input_missing");
            if python_ok && !rust_missing {
                failures
                    .push(format!("{label}: row NEXT expected input_missing, rust {:?}", result.map(object)));
            }
            if !python_ok {
                check_set(&label, &synthetic, &inputs[0], &inputs[1], &states, case, &mut failures);
            }
            continue;
        }
        check_set(&label, &synthetic, &inputs[0], &inputs[1], &states, case, &mut failures);
    }
    assert!(row_cases > 0);
    let shown: Vec<String> = failures.iter().take(6).map(|f| f.chars().take(1500).collect()).collect();
    assert!(failures.is_empty(), "{} differ: {:#?}", failures.len(), shown);
}
