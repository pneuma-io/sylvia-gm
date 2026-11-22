//! Compare sylvia-app::package_runtime's catalog-facing tool helpers with the frozen Python.
//!
//! Regenerate with `tests/vectors/generate_catalog_tool_vectors.py` (needs the snapshot's Python).

use serde_json::{json, Value as Json};
use std::path::Path;
use sylvia_app::package_runtime::{
    package_tool_decision_title, package_tool_description, package_tool_model_behavior,
    package_tool_permanently_refused, package_tool_player_controls, PackageRuntimeFailure,
};
use sylvia_app::player_actions::PlayerControls;
use sylvia_domain::package_library::ToolDeclaration;

fn hex(text: &str) -> Vec<u8> {
    (0..text.len()).step_by(2).map(|i| u8::from_str_radix(&text[i..i + 2], 16).unwrap()).collect()
}

fn declaration(value: &Json) -> ToolDeclaration {
    ToolDeclaration {
        name: value["name"].as_str().unwrap().into(),
        capability: value["capability"].as_str().unwrap().into(),
        input_schema: value["input_schema"].as_object().unwrap().clone(),
        state_predicates: Vec::new(),
        turn_statuses: value["turn_statuses"]
            .as_array()
            .unwrap()
            .iter()
            .map(|s| s.as_str().unwrap().into())
            .collect(),
        writers: value["writers"].as_array().unwrap().iter().map(|s| s.as_str().unwrap().into()).collect(),
    }
}

fn failure(error: PackageRuntimeFailure) -> Json {
    match error {
        PackageRuntimeFailure::Rejected(error) => json!({"error": ["PackageRuntimeError", error.message]}),
        PackageRuntimeFailure::Uncaught { class, message } => json!({"error": [class, message]}),
    }
}

fn controls_json(controls: &PlayerControls) -> Json {
    json!({
        "confirmation": controls.confirmation, "response": controls.response, "preview": controls.preview,
        "actor_parameter": controls.actor_parameter, "checks": controls.checks, "queries": controls.queries,
        "calculations": controls.calculations, "map_preview": controls.map_preview, "available": controls.available,
        "preflight": controls.preflight, "preflight_path": controls.preflight_path,
    })
}

fn record(package_id: &str, declaration: &ToolDeclaration, runtime: &[u8]) -> Json {
    let wrap = |result: Result<Json, PackageRuntimeFailure>| {
        result.map_or_else(failure, |value| json!({"result": value}))
    };
    json!({
        "description": wrap(package_tool_description(package_id, declaration, runtime).map(Json::from)),
        "model_behavior": wrap(package_tool_model_behavior(package_id, declaration, runtime).map(|(behavior, predicates)| {
            json!([behavior.as_str(), predicates.iter().map(|p| json!({
                "namespace": p.namespace, "key": p.key, "equals": p.equals, "negated": p.negated,
            })).collect::<Vec<_>>()])
        })),
        "decision_title": wrap(package_tool_decision_title(package_id, declaration, runtime).map(Json::from)),
        "player_controls": wrap(package_tool_player_controls(package_id, declaration, runtime).map(|c| controls_json(&c))),
        "permanently_refused": wrap(package_tool_permanently_refused(package_id, declaration, runtime).map(Json::from)),
    })
}

fn compare(name: &str, actual: &Json, case: &Json, failures: &mut Vec<String>) {
    for key in ["description", "model_behavior", "decision_title", "player_controls", "permanently_refused"] {
        let (a, w) =
            (serde_json::to_string(&actual[key]).unwrap(), serde_json::to_string(&case[key]).unwrap());
        if a != w {
            failures.push(format!("{name} {key}\n  python {w}\n  rust   {a}"));
        }
    }
}

#[test]
fn catalog_helpers_read_as_python() {
    let vectors: Json =
        serde_json::from_str(include_str!("vectors/catalog_tool_vectors.json")).expect("valid JSON");
    let mut failures = Vec::new();
    for case in vectors["cases"].as_array().unwrap() {
        let runtime = hex(case["runtime_hex"].as_str().unwrap());
        let actual =
            record(case["package_id"].as_str().unwrap(), &declaration(&case["declaration"]), &runtime);
        compare(case["name"].as_str().unwrap(), &actual, case, &mut failures);
    }
    let root = Path::new(env!("CARGO_MANIFEST_DIR")).join("../..");
    for case in vectors["shipped"].as_array().unwrap() {
        let runtime =
            std::fs::read(root.join(case["source"].as_str().unwrap())).expect("the shipped runtime");
        let actual =
            record(case["package_id"].as_str().unwrap(), &declaration(&case["declaration"]), &runtime);
        compare(case["name"].as_str().unwrap(), &actual, case, &mut failures);
    }
    let riichi = &vectors["riichi"];
    let runtime = hex(riichi["runtime_hex"].as_str().unwrap());
    for case in riichi["cases"].as_array().unwrap() {
        let actual =
            record(riichi["package_id"].as_str().unwrap(), &declaration(&case["declaration"]), &runtime);
        compare(case["name"].as_str().unwrap(), &actual, case, &mut failures);
    }
    assert!(vectors["shipped"].as_array().unwrap().len() > 150);
    assert!(
        failures.is_empty(),
        "{} differ, first: {:#?}",
        failures.len(),
        &failures[..failures.len().min(5)]
    );
}
