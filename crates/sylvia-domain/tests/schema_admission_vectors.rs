//! Compare sylvia_domain::schema_admission and the rejection errors with the frozen Python.
//!
//! Regenerate with `tests/vectors/generate_schema_admission_vectors.py` (needs the snapshot's
//! Python). The shipped schemas are read from the Python owner's startup recording.

use serde_json::json;
use sylvia_base::pyjson;
use sylvia_base::{Json, JsonMap};
use sylvia_domain::schema_admission::{
    validate_json_schema, validate_json_value, validate_parameters, validate_references,
};
use sylvia_domain::{DomainError, DomainResult};

fn vectors() -> Json {
    pyjson::loads(include_str!("vectors/schema_admission_vectors.json")).expect("vector file is valid JSON")
}

/// The generator's `shipped_schemas()` followed by its synthetic list.
fn sources(vectors: &Json) -> Vec<Json> {
    let path =
        concat!(env!("CARGO_MANIFEST_DIR"), "/../sylvia-compare/recordings/windows/startup.recording.json");
    let recording = pyjson::loads(&std::fs::read_to_string(path).unwrap()).unwrap();
    let tables = &recording["database"]["tables"];
    let mut schemas: Vec<Json> = tables["package_tools"]["rows"]
        .as_array()
        .unwrap()
        .iter()
        .map(|row| row["input_schema_json"]["$json"].clone())
        .collect();
    for row in tables["package_manifests"]["rows"].as_array().unwrap() {
        let record = &row["canonical_json"]["$json"];
        for artifact in record.get("participant_artifacts").and_then(Json::as_array).into_iter().flatten() {
            schemas.push(artifact["schema"].clone());
        }
        for state in record.get("state_schemas").and_then(Json::as_array).into_iter().flatten() {
            schemas.push(state["schema"].clone());
        }
    }
    schemas.extend(vectors["synthetic"].as_array().unwrap().iter().cloned());
    schemas
}

fn outcome(result: DomainResult<()>) -> Json {
    match result {
        Ok(()) => json!(["ok"]),
        Err(error) => {
            let mut row = vec![
                Json::from(error.python_class()),
                error.code().map(Json::from).unwrap_or(Json::Null),
                Json::from(error.message()),
                Json::Array(error.diagnostics().iter().map(|d| Json::Object(d.as_payload())).collect()),
            ];
            if let DomainError::Rejected { mechanics: Some(details), .. } = &error {
                row.push(json!({
                    "field_paths": details.field_paths,
                    "validation_issues": details.validation_issues.iter().map(|i| Json::Object(i.as_payload())).collect::<Vec<_>>(),
                    "state_changed": details.state_changed,
                    "refusal_reasons": details.refusal_reasons,
                }));
            }
            Json::Array(row)
        }
    }
}

fn step<'v>(node: &'v mut Json, part: &Json) -> &'v mut Json {
    match part {
        Json::String(key) => &mut node[key.as_str()],
        Json::Number(index) => &mut node[index.as_u64().unwrap() as usize],
        other => panic!("bad path part {other}"),
    }
}

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
            parent = step(parent, part);
        }
        match (op, parent) {
            ("delete", Json::Object(map)) => {
                map.shift_remove(last.as_str().unwrap());
            }
            ("delete", Json::Array(items)) => {
                items.remove(last.as_u64().unwrap() as usize);
            }
            ("set", Json::Object(map)) => {
                map.insert(last.as_str().unwrap().to_owned(), value.clone());
            }
            ("set", Json::Array(items)) => items[last.as_u64().unwrap() as usize] = value.clone(),
            (other, _) => panic!("bad edit {other}"),
        }
    }
    document
}

#[test]
fn schema_admission_matches_python() {
    let vectors = vectors();
    let sources = sources(&vectors);
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 500);
    let mut failures = Vec::new();
    let mut checks = 0;
    let participants = ["p-1", "p-2"];
    for case in cases {
        let schema =
            apply(&sources[case["source"].as_u64().unwrap() as usize], case["edits"].as_array().unwrap());
        let schema: &JsonMap = schema.as_object().expect("the generator keeps object schemas");
        let admitted = outcome(validate_json_schema(schema, "Schema"));
        if admitted != case["admission"] {
            failures.push(format!(
                "admission {} {}: python {}, rust {admitted}",
                case["source"], case["edits"], case["admission"]
            ));
            continue;
        }
        for check in case.get("checks").and_then(Json::as_array).into_iter().flatten() {
            checks += 1;
            let value = check["value"].as_object().unwrap();
            for (field, actual) in [
                ("parameters", outcome(validate_parameters(value, schema))),
                ("json_value", outcome(validate_json_value(&check["value"], schema, "Value"))),
                ("references", outcome(validate_references(value, schema, &participants))),
            ] {
                if actual != check[field] {
                    failures.push(format!(
                        "{field} {} on {}: python {}, rust {actual}",
                        case["source"], check["value"], check[field]
                    ));
                }
            }
        }
    }
    assert!(checks > 1000, "expected the full value corpus, got {checks}");
    assert!(
        failures.is_empty(),
        "{} differ, first: {:#?}",
        failures.len(),
        &failures[..failures.len().min(3)]
    );
}

#[test]
fn rejection_constructors_match_python() {
    let vectors = vectors();
    let strings =
        |v: &Json| v.as_array().unwrap().iter().map(|s| s.as_str().unwrap().to_owned()).collect::<Vec<_>>();
    for case in vectors["rejections"].as_array().unwrap() {
        let built = if let Some(safe) = case.get("safe_reason") {
            DomainError::ruleset_precondition_rejected(safe.as_str().unwrap(), &strings(&case["reasons"]))
        } else {
            DomainError::mechanics_rejected(
                "m",
                "c",
                sylvia_domain::error::MechanicsDetails {
                    refusal_reasons: strings(&case["refusal_reasons"]),
                    ..Default::default()
                },
            )
        };
        // Python raises the rejection (or the ValueError from constructing it).
        let actual = outcome(Err(built.unwrap_or_else(|error| error)));
        assert_eq!(actual, case["outcome"], "{case}");
    }
}

#[test]
fn named_inputs_match_python_findall() {
    let vectors = vectors();
    for case in vectors["named_inputs"].as_array().unwrap() {
        let actual = sylvia_domain::error::named_inputs(case[0].as_str().unwrap());
        assert_eq!(Json::from(actual), case[1], "{case}");
    }
    // Every code point before "input 'x'": Python's `\b` blocks the match exactly after a word
    // character (`str.isalnum()` or `_`). Surrogates are not characters in Rust.
    let blockers: Vec<(u32, u32)> = vectors["named_input_blockers"]
        .as_array()
        .unwrap()
        .iter()
        .map(|range| (range[0].as_u64().unwrap() as u32, range[1].as_u64().unwrap() as u32))
        .collect();
    let mut differ = Vec::new();
    for code in 0..=0x10FFFFu32 {
        let Some(c) = char::from_u32(code) else { continue };
        let expected = blockers.iter().any(|(start, end)| (*start..=*end).contains(&code));
        let blocked = sylvia_domain::error::named_inputs(&format!("{c}input 'x'")).is_empty();
        if blocked != expected {
            differ.push(code);
        }
    }
    assert!(
        differ.is_empty(),
        "{} code points differ, first {:x?}",
        differ.len(),
        &differ[..differ.len().min(8)]
    );
}
