//! Compare sylvia-app's player-action checker and previewer (`package_runtime`) and the
//! player-known scene (`tactical_grid`) with the frozen Python.
//!
//! Regenerate with `tests/vectors/generate_player_action_check_vectors.py` (needs the snapshot's
//! Python). A `pending` record is where Python reached code the port refuses by name until its
//! spatial effect handlers land; the port must stop there with `PortPending` naming it.

use serde_json::{json, Value as Json};
use std::path::Path;
use sylvia_app::package_runtime::{
    materialize_player_action_checker, materialize_player_action_previewer, PackageRuntimeFailure,
};
use sylvia_app::tactical_grid::{player_known_spatial_scene, player_known_spatial_value};
use sylvia_domain::package_library::ToolDeclaration;
use sylvia_engine::exec::handlers::with_identity_shuffle_for_vectors;
use sylvia_engine::spatial::SpatialError;

fn hex(text: &str) -> Vec<u8> {
    (0..text.len()).step_by(2).map(|i| u8::from_str_radix(&text[i..i + 2], 16).unwrap()).collect()
}

fn strings(value: &Json) -> Vec<String> {
    value.as_array().unwrap().iter().map(|s| s.as_str().unwrap().to_owned()).collect()
}

fn declaration(value: &Json) -> ToolDeclaration {
    ToolDeclaration {
        name: value["name"].as_str().unwrap().into(),
        capability: value["capability"].as_str().unwrap().into(),
        input_schema: value["input_schema"].as_object().unwrap().clone(),
        state_predicates: Vec::new(),
        turn_statuses: strings(&value["turn_statuses"]),
        writers: strings(&value["writers"]),
    }
}

struct Runtimes<'v> {
    documents: &'v serde_json::Map<String, Json>,
    riichi: Vec<u8>,
    root: std::path::PathBuf,
}

impl Runtimes<'_> {
    fn bytes(&self, reference: &Json) -> Vec<u8> {
        if let Some(digest) = reference.get("document").and_then(Json::as_str) {
            return hex(self.documents[digest].as_str().unwrap());
        }
        if reference.get("riichi").is_some() {
            return self.riichi.clone();
        }
        std::fs::read(self.root.join(reference["source"].as_str().unwrap())).expect("a shipped runtime")
    }
}

/// One case's record, in the generator's shape.
fn record(case: &Json, runtimes: &Runtimes<'_>) -> Json {
    let data = runtimes.bytes(&case["runtime"]);
    let (package_id, tool) = (case["package_id"].as_str().unwrap(), declaration(&case["declaration"]));
    let (parameters, state, participants) = (
        case["parameters"].as_object().unwrap(),
        case["state"].as_object().unwrap(),
        case["participants"].as_object().unwrap(),
    );
    let failure = |error: PackageRuntimeFailure| match error {
        PackageRuntimeFailure::Rejected(error) => {
            json!({"materialize": ["PackageRuntimeError", error.message]})
        }
        PackageRuntimeFailure::Uncaught { class, message } => json!({"materialize": [class, message]}),
    };
    with_identity_shuffle_for_vectors(|| {
        if case["kind"] == "check" {
            match materialize_player_action_checker(package_id, &tool, &data) {
                Err(error) => failure(error),
                Ok(checker) => match checker.check(parameters, state, participants) {
                    Ok(reason) => json!({"reason": reason}),
                    Err(pending) => json!({"pending": pending.0}),
                },
            }
        } else {
            match materialize_player_action_previewer(package_id, &tool, &data) {
                Err(error) => failure(error),
                Ok(previewer) => match previewer.preview(parameters, state, participants) {
                    Ok(preview) => json!({"preview": preview}),
                    Err(pending) => json!({"pending": pending.0}),
                },
            }
        }
    })
}

/// The port's message for what Python reached.
fn pending_message(reached: &str) -> String {
    let what = match reached {
        "queries" => "the player controls' spatial queries".to_owned(),
        "preflight_path" => "the player controls' preflight path preview".to_owned(),
        "map_preview" => "the player controls' map preview".to_owned(),
        effect => format!("the '{effect}' effect"),
    };
    format!("not yet ported: {what} (lane 2's spatial effect handlers)")
}

fn compare(name: &str, actual: &Json, wanted: &Json, failures: &mut Vec<String>) {
    let mut wanted = wanted.clone();
    if let Some(map) = wanted.as_object_mut() {
        for key in
            ["name", "kind", "package_id", "declaration", "runtime", "parameters", "state", "participants"]
        {
            map.remove(key);
        }
        if let Some(Json::String(reached)) = map.get("pending").cloned() {
            map.insert("pending".into(), Json::String(pending_message(&reached)));
        }
    }
    let (a, w) = (serde_json::to_string(actual).unwrap(), serde_json::to_string(&wanted).unwrap());
    if a != w {
        failures.push(format!("{name}\n  python {w}\n  rust   {a}"));
    }
}

fn spatial(result: Result<Json, SpatialError>) -> Json {
    match result {
        Ok(value) => json!({"result": value}),
        Err(error) => {
            let class = match &error {
                SpatialError::Validation(_) => "SpatialValidationError",
                SpatialError::Uncaught { error_type, .. } => error_type,
            };
            json!({"error": [class, error.to_string()]})
        }
    }
}

#[test]
fn player_known_scenes_as_python() {
    let vectors: Json =
        serde_json::from_str(include_str!("vectors/player_action_check_vectors.json")).expect("valid JSON");
    let mut failures = Vec::new();
    let scenes = vectors["scenes"].as_array().unwrap();
    for case in scenes {
        let value = &case["value"];
        let actual = json!({
            "scene_false": spatial(player_known_spatial_scene(value, false).map(Json::Object)),
            "scene_true": spatial(player_known_spatial_scene(value, true).map(Json::Object)),
            "value_result": spatial(player_known_spatial_value(&json!({
                "outer": [value, {"format": 1.0, "map": 1}], "n": 1, "scene": value,
            }))),
        });
        let wanted = json!({"scene_false": case["scene_False"], "scene_true": case["scene_True"],
                            "value_result": case["value_result"]});
        compare(case["name"].as_str().unwrap(), &actual, &wanted, &mut failures);
    }
    assert!(scenes.len() > 25);
    assert!(
        failures.is_empty(),
        "{} differ, first: {:#?}",
        failures.len(),
        &failures[..failures.len().min(4)]
    );
}

#[test]
fn player_action_checks_and_previews_as_python() {
    let vectors: Json =
        serde_json::from_str(include_str!("vectors/player_action_check_vectors.json")).expect("valid JSON");
    let catalog: Json =
        serde_json::from_str(include_str!("vectors/catalog_tool_vectors.json")).expect("valid JSON");
    let runtimes = Runtimes {
        documents: vectors["documents"].as_object().unwrap(),
        riichi: hex(catalog["riichi"]["runtime_hex"].as_str().unwrap()),
        root: Path::new(env!("CARGO_MANIFEST_DIR")).join("../.."),
    };
    let mut failures = Vec::new();
    let cases = vectors["cases"].as_array().unwrap();
    for case in cases {
        compare(case["name"].as_str().unwrap(), &record(case, &runtimes), case, &mut failures);
    }
    let inputs = vectors["recorded_inputs"].as_array().unwrap();
    let recorded = vectors["recorded"].as_array().unwrap();
    assert_eq!(inputs.len(), recorded.len());
    for (index, (input, wanted)) in inputs.iter().zip(recorded).enumerate() {
        let name = format!(
            "recorded {index} {} {} {}",
            input["kind"],
            input["package_id"].as_str().unwrap(),
            input["declaration"]["name"]
        );
        compare(&name, &record(input, &runtimes), wanted, &mut failures);
    }
    assert!(cases.len() > 50);
    assert!(
        failures.is_empty(),
        "{} of {} differ, first: {:#?}",
        failures.len(),
        cases.len() + inputs.len(),
        &failures[..failures.len().min(6)]
    );
}
