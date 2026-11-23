//! Compare sylvia-app::package_mechanic (`materialize_package_mechanic` and its mechanic) and
//! sylvia-app::result_cards with the frozen Python.
//!
//! Regenerate with `tests/vectors/generate_package_mechanic_vectors.py` (needs the snapshot's
//! Python). A `pending` record is where Python reached a spatial effect: the port must stop there
//! with `MechanicFailure::PortPending` naming that effect, through every nested call, procedure
//! and catch-all.

use serde_json::{json, Value as Json};
use std::path::Path;
use sylvia_app::package_runtime::{materialize_package_mechanic, MechanicFailure, PackageRuntimeFailure};
use sylvia_domain::authority::{
    MechanicContext, MechanicOutcome, MechanicsChoiceAnswer, MechanicsChoiceOption, MechanicsChoiceRequest,
    MechanicsChoiceResolution,
};
use sylvia_domain::package_library::ToolDeclaration;
use sylvia_engine::exec::handlers::with_identity_shuffle_for_vectors;

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

fn request(value: &Json) -> MechanicsChoiceRequest {
    MechanicsChoiceRequest {
        choice_id: value["choice_id"].as_str().unwrap().into(),
        prompt: value["prompt"].as_str().unwrap().into(),
        options: value["options"]
            .as_array()
            .unwrap()
            .iter()
            .map(|pair| MechanicsChoiceOption {
                option_id: pair[0].as_str().unwrap().into(),
                label: pair[1].as_str().unwrap().into(),
            })
            .collect(),
    }
}

fn request_json(request: &MechanicsChoiceRequest) -> Json {
    json!({"choice_id": request.choice_id, "prompt": request.prompt,
           "options": request.options.iter().map(|o| json!([o.option_id, o.label])).collect::<Vec<_>>()})
}

fn context(value: &Json) -> MechanicContext {
    let resolution = &value["choice_resolution"];
    MechanicContext {
        campaign_id: value["campaign_id"].as_str().unwrap().into(),
        turn_id: value["turn_id"].as_str().unwrap().into(),
        action_id: value["action_id"].as_str().unwrap().into(),
        state: value["state"].as_object().unwrap().clone(),
        parameters: value["parameters"].as_object().unwrap().clone(),
        participants: value["participants"].as_object().unwrap().clone(),
        choice_resolution: (!resolution.is_null()).then(|| MechanicsChoiceResolution {
            choice_id: resolution["choice_id"].as_str().unwrap().into(),
            option_id: resolution["option_id"].as_str().unwrap().into(),
            expected_request: request(&resolution["expected_request"]),
            prior_answers: resolution["prior_answers"]
                .as_array()
                .unwrap()
                .iter()
                .map(|answer| MechanicsChoiceAnswer {
                    resolution_id: answer["resolution_id"].as_str().unwrap().into(),
                    request: request(&answer["request"]),
                    option_id: answer["option_id"].as_str().unwrap().into(),
                    session_id: answer["session_id"].as_str().map(str::to_owned),
                })
                .collect(),
        }),
    }
}

fn outcome_json(outcome: &MechanicOutcome) -> Json {
    json!({
        "result": outcome.result,
        "mutations": outcome.mutations.iter().map(|m| json!([m.namespace, m.key, m.value])).collect::<Vec<_>>(),
        "participant_mutations": outcome.participant_mutations.iter().map(|m| json!([m.participant_id, m.data])).collect::<Vec<_>>(),
        "consequences": outcome.consequences,
        "choice": outcome.choice.as_ref().map(request_json),
    })
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
    let mechanic = match materialize_package_mechanic(
        case["package_id"].as_str().unwrap(),
        case["package_version"].as_str().unwrap(),
        case["package_sha256"].as_str().unwrap(),
        &declaration(&case["declaration"]),
        &data,
    ) {
        Ok(mechanic) => mechanic,
        Err(PackageRuntimeFailure::Rejected(error)) => {
            return json!({"materialize": ["PackageRuntimeError", error.message]})
        }
        Err(PackageRuntimeFailure::Uncaught { class, message }) => {
            return json!({"materialize": [class, message]})
        }
    };
    match with_identity_shuffle_for_vectors(|| mechanic.execute(&context(&case["context"]))) {
        Ok(outcome) => {
            let record = outcome_json(&outcome);
            let encoded = sylvia_base::pyjson::canonical_bytes(&record);
            if encoded.len() > 4096 {
                let digest: String =
                    sylvia_base::hash::sha256(&encoded).iter().map(|b| format!("{b:02x}")).collect();
                json!({"outcome_sha256": digest, "status": outcome.result.get("status")})
            } else {
                json!({"outcome": record})
            }
        }
        Err(MechanicFailure::Rejected(error)) => {
            let details = error.mechanics().expect("a precondition refusal carries its reasons");
            json!({"rejected": [error.message(), details.refusal_reasons, details.field_paths]})
        }
        Err(MechanicFailure::Failed { class, message, cause }) => json!({"failed": [class, message, cause]}),
        Err(MechanicFailure::PortPending(message)) => json!({"pending": message}),
    }
}

fn compare(name: &str, actual: &Json, wanted: &Json, failures: &mut Vec<String>) {
    let mut wanted = wanted.clone();
    for key in
        ["name", "package_id", "package_version", "package_sha256", "declaration", "runtime", "context"]
    {
        wanted.as_object_mut().unwrap().shift_remove(key);
    }
    if let (Some(effect), Some(message)) =
        (wanted.get("pending").and_then(Json::as_str), actual.get("pending").and_then(Json::as_str))
    {
        if message != format!("not yet ported: the '{effect}' effect (lane 2's spatial effect handlers)") {
            failures.push(format!("{name}: python reached '{effect}', rust {message}"));
        }
        return;
    }
    let (a, w) = (serde_json::to_string(actual).unwrap(), serde_json::to_string(&wanted).unwrap());
    if a != w {
        failures.push(format!("{name}\n  python {w}\n  rust   {a}"));
    }
}

#[test]
fn package_mechanics_run_as_python() {
    let vectors: Json =
        serde_json::from_str(include_str!("vectors/package_mechanic_vectors.json")).expect("valid JSON");
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
            "recorded {index} {} {}",
            input["package_id"].as_str().unwrap(),
            input["declaration"]["name"]
        );
        compare(&name, &record(input, &runtimes), wanted, &mut failures);
    }
    assert!(cases.len() > 100);
    assert!(
        failures.is_empty(),
        "{} of {} differ, first: {:#?}",
        failures.len(),
        cases.len() + inputs.len(),
        &failures[..failures.len().min(6)]
    );
}

/// `PortPending` passes through a procedure and a nested `call` as `Suspended` does: the targeted
/// `pending` cases reach an effect not yet ported (`aoe`) only inside one, and Python's catch-alls
/// there would otherwise turn the refusal into a failed action.
#[test]
fn port_pending_passes_through_procedures_and_nested_calls() {
    let vectors: Json =
        serde_json::from_str(include_str!("vectors/package_mechanic_vectors.json")).expect("valid JSON");
    let catalog: Json =
        serde_json::from_str(include_str!("vectors/catalog_tool_vectors.json")).expect("valid JSON");
    let runtimes = Runtimes {
        documents: vectors["documents"].as_object().unwrap(),
        riichi: hex(catalog["riichi"]["runtime_hex"].as_str().unwrap()),
        root: Path::new(env!("CARGO_MANIFEST_DIR")).join("../.."),
    };
    for name in ["pending nested", "pending procedure", "pending for_each", "pending conditional"] {
        let case = vectors["cases"].as_array().unwrap().iter().find(|c| c["name"] == name).expect(name);
        assert_eq!(case["pending"], "aoe", "{name}: Python reaches the effect");
        let actual = record(case, &runtimes);
        assert_eq!(
            actual["pending"], "not yet ported: the 'aoe' effect (lane 2's spatial effect handlers)",
            "{name}: {actual}"
        );
    }
}
