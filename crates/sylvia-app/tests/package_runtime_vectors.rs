//! Compare sylvia_app::package_runtime::validate_package_runtime with the frozen Python.
//!
//! Regenerate with `tests/vectors/generate_package_runtime_vectors.py` (needs the snapshot's
//! Python). The shipped SRD Lite runtime and the signal-and-static fixture are read from the
//! repository itself.

use serde_json::{json, Value as Json};
use sylvia_app::package_runtime::{
    validate_package_runtime, PackageRuntimeFailure, PACKAGE_RUNTIME_BYTE_LIMIT,
};
use sylvia_app::player_actions::parse_player_controls;
use sylvia_domain::package_library::ToolDeclaration;
use sylvia_engine::ruleset::model::{
    check_ruleset_file, coerce_bool, coerce_int, coerce_int_or_float_or_none, coerce_str, ModelRefusal,
};
use sylvia_engine::ruleset::runtime_tables::load_runtime_tables;

const SRD_LITE: &[u8] = include_bytes!("../../../rulesets/srd-lite/runtime/ruleset.yaml");
const FIXTURE: &[u8] =
    include_bytes!("../../../tests/fixtures/rulesets/signal-and-static/runtime/ruleset.yaml");

fn vectors() -> Json {
    serde_json::from_str(include_str!("vectors/package_runtime_vectors.json")).expect("valid JSON")
}

fn declarations(list: &Json) -> Vec<ToolDeclaration> {
    let strings =
        |value: &Json| value.as_array().unwrap().iter().map(|s| s.as_str().unwrap().to_owned()).collect();
    list.as_array()
        .unwrap()
        .iter()
        .map(|item| ToolDeclaration {
            name: item["name"].as_str().unwrap().to_owned(),
            capability: item["capability"].as_str().unwrap().to_owned(),
            input_schema: item["input_schema"].as_object().unwrap().clone(),
            state_predicates: Vec::new(),
            turn_statuses: strings(&item["turn_statuses"]),
            writers: strings(&item["writers"]),
        })
        .collect()
}

fn outcome(package_id: &str, declarations: &[ToolDeclaration], runtime: &[u8]) -> Json {
    match validate_package_runtime(package_id, declarations, runtime) {
        Ok(()) => json!({"ok": true}),
        Err(PackageRuntimeFailure::Rejected(error)) => json!({
            "raise": "PackageRuntimeError",
            "message": error.message,
            "diagnostics": error.diagnostics.iter().map(|item| Json::Object(item.as_payload())).collect::<Vec<_>>(),
        }),
        Err(PackageRuntimeFailure::Uncaught { class, message }) => {
            json!({"raise": class, "message": message, "diagnostics": []})
        }
    }
}

fn runtime_bytes(case: &Json) -> Vec<u8> {
    match case.get("runtime_hex").and_then(Json::as_str) {
        Some(hex) => {
            (0..hex.len()).step_by(2).map(|i| u8::from_str_radix(&hex[i..i + 2], 16).unwrap()).collect()
        }
        None => case["runtime"].as_str().unwrap().as_bytes().to_vec(),
    }
}

#[test]
fn shipped_runtimes_validate_as_python() {
    let vectors = vectors();
    for case in vectors["shipped"].as_array().unwrap() {
        let bytes = match case.get("source").and_then(Json::as_str) {
            Some("rulesets/srd-lite/runtime/ruleset.yaml") => SRD_LITE.to_vec(),
            Some("tests/fixtures/rulesets/signal-and-static/runtime/ruleset.yaml") => FIXTURE.to_vec(),
            Some(other) => panic!("unknown source {other}"),
            None => runtime_bytes(case),
        };
        let package_id = case["package_id"].as_str().unwrap();
        assert_eq!(
            outcome(package_id, &declarations(&case["declarations"]), &bytes),
            case["result"],
            "{package_id}"
        );
    }
}

#[test]
fn every_case_validates_as_python() {
    let vectors = vectors();
    let package_id = vectors["package_id"].as_str().unwrap();
    let sets: Vec<Vec<ToolDeclaration>> =
        vectors["declarations"].as_array().unwrap().iter().map(declarations).collect();
    let cases = vectors["cases"].as_array().unwrap();
    assert!(cases.len() > 1900, "expected the full corpus");
    let mut failures = Vec::new();
    for (index, case) in cases.iter().enumerate() {
        let set = &sets[case["declarations"].as_u64().unwrap() as usize];
        let actual = outcome(package_id, set, &runtime_bytes(case));
        // `differences.md` row 5: the YAML loader refuses an integer outside i64.
        let wanted = if case.get("wide_int") == Some(&json!(true)) {
            json!({"raise": "PackageRuntimeError", "message": "Package runtime YAML is invalid", "diagnostics": []})
        } else {
            case["result"].clone()
        };
        if actual != wanted {
            failures.push(format!("case {index}: python {wanted}\n  rust {actual}"));
        }
    }
    assert!(
        failures.is_empty(),
        "{} of {} differ, first: {:#?}",
        failures.len(),
        cases.len(),
        &failures[..failures.len().min(6)]
    );
}

#[test]
fn pydantic_conversions_match_python() {
    let vectors = vectors();
    let values = vectors["coercion"]["values"].as_array().unwrap();
    let fields = &vectors["coercion"]["fields"];
    let mut failures = Vec::new();
    for (index, value) in values.iter().enumerate() {
        let wanted = |field: &str| &fields[field][index];
        let checks = [
            (
                "str",
                coerce_str(value).map_or(json!({"ok": false}), |text| json!({"ok": true, "value": text})),
            ),
            (
                "bool",
                coerce_bool(value).map_or(json!({"ok": false}), |flag| json!({"ok": true, "value": flag})),
            ),
            (
                "int",
                coerce_int(value)
                    .map_or(json!({"ok": false}), |int| json!({"ok": true, "value": int.to_string()})),
            ),
            ("number", json!({"ok": coerce_int_or_float_or_none(value)})),
            (
                "str_list",
                match value {
                    Json::Array(items) if items.iter().all(Json::is_string) => {
                        json!({"ok": true, "value": value})
                    }
                    _ => json!({"ok": false}),
                },
            ),
        ];
        for (field, actual) in checks {
            if &actual != wanted(field) {
                failures.push(format!("{field} {value}: python {} rust {actual}", wanted(field)));
            }
        }
    }
    assert!(failures.is_empty(), "{failures:#?}");
}

#[test]
fn ruleset_file_model_matches_python() {
    let vectors = vectors();
    let cases = vectors["models"].as_array().unwrap();
    assert!(cases.len() >= 1500);
    let mut failures = Vec::new();
    for case in cases {
        let actual = match check_ruleset_file(&case["document"]) {
            Ok(()) => "ok",
            Err(ModelRefusal::Invalid) => "invalid",
            Err(ModelRefusal::CrossReference) => "cross_reference",
        };
        if actual != case["result"] {
            failures.push(format!("python {} rust {actual}: {}", case["result"], case["document"]));
        }
    }
    let kinds = |kind: &str| cases.iter().filter(|case| case["result"] == kind).count();
    assert!(kinds("ok") > 100 && kinds("invalid") > 100 && kinds("cross_reference") > 5);
    assert!(
        failures.is_empty(),
        "{} differ, first: {:#?}",
        failures.len(),
        &failures[..failures.len().min(6)]
    );
}

#[test]
fn player_controls_parse_as_python() {
    let vectors = vectors();
    let cases = vectors["controls"].as_array().unwrap();
    assert!(cases.len() >= 800);
    let object =
        |value: &Option<serde_json::Map<String, Json>>| value.clone().map_or(Json::Null, Json::Object);
    let list = |items: &[serde_json::Map<String, Json>]| {
        Json::Array(items.iter().cloned().map(Json::Object).collect())
    };
    let mut failures = Vec::new();
    for case in cases {
        let value = &case["value"];
        let policy = parse_player_controls(if value.is_null() { None } else { Some(value) });
        let actual = json!({
            "confirmation": policy.confirmation, "response": policy.response, "preview": object(&policy.preview),
            "actor_parameter": policy.actor_parameter, "checks": list(&policy.checks), "queries": list(&policy.queries),
            "calculations": list(&policy.calculations), "map_preview": object(&policy.map_preview),
            "available": policy.available, "preflight": policy.preflight, "preflight_path": object(&policy.preflight_path),
        });
        if actual != case["result"] {
            failures.push(format!("{value}\n  python {}\n  rust {actual}", case["result"]));
        }
    }
    assert!(
        failures.is_empty(),
        "{} differ, first: {:#?}",
        failures.len(),
        &failures[..failures.len().min(4)]
    );
}

#[test]
fn permanently_refused_tools_match_python() {
    let vectors = vectors();
    for case in vectors["tables"].as_array().unwrap() {
        let text = serde_json::to_string(&case["document"]).unwrap();
        let data = sylvia_base::pyyaml::load(
            &text,
            sylvia_base::pyyaml::Loader::C,
            sylvia_base::pyyaml::Hooks::default(),
        )
        .unwrap()
        .to_data()
        .unwrap()
        .unwrap();
        let tables = load_runtime_tables(data.as_dict().unwrap()).unwrap();
        let mut refused: Vec<String> =
            tables.permanently_refused.iter().map(|name| name.as_str().unwrap().to_owned()).collect();
        refused.sort();
        assert_eq!(json!(refused), case["refused"], "{}", case["document"]["tools"]);
    }
}

/// The byte limit is checked first, so a runtime one byte over refuses without being read.
#[test]
fn oversized_runtime_is_refused_before_parsing() {
    let bytes = vec![b'#'; PACKAGE_RUNTIME_BYTE_LIMIT + 1];
    assert_eq!(
        outcome("x", &[], &bytes),
        json!({"raise": "PackageRuntimeError", "message": "Package runtime exceeds the byte limit", "diagnostics": []})
    );
}
